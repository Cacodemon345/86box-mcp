"""Host-side devices for a guest's COM port: what sits at the other end of the cable.

Each peer is fed the guest's output and asked for its input once per lockstep quantum (see
server._pump_serial), with the emulated time, so timing rules like the modem's escape guard time
are measured in emulated milliseconds. The far ends they talk to (a TCP server, a program on a
pty) run on the host's clock, so traffic through them isn't reproducible run to run the way a
held VM on its own is.

  Modem    a Hayes-compatible modem that "dials" TCP: ATDT 5551234 connects to the address the
           number maps to (or the one given as the number, e.g. ATDT 127.0.0.1:2323), like
           DOSBox's soft modem and tcpser. CONNECT, NO CARRIER, +++ and ATH, DTR hang-up and DCD.
  PtyPeer  a pseudo-terminal: any host program (minicom, screen, a script) opens its path and
           talks to the guest directly, as over a null-modem cable. POSIX only.
  TcpPeer  listens on a TCP port; one client at a time gets the guest's port, raw, with DCD and
           DSR up while it is connected.
"""

from __future__ import annotations

import errno
import os
import socket
from dataclasses import dataclass, field


@dataclass
class Lines:
    """The modem status lines the guest sees."""
    cts: bool = True
    dsr: bool = True
    dcd: bool = True
    ri: bool = False

    def as_tuple(self) -> tuple[bool, bool, bool, bool]:
        return self.cts, self.dsr, self.dcd, self.ri


class Peer:
    """What the pump needs from a host-side device."""

    kind = "peer"

    def from_guest(self, data: bytes, now_ms: int, dtr: bool, rts: bool) -> None:
        raise NotImplementedError

    def to_guest(self, now_ms: int) -> bytes:
        raise NotImplementedError

    def lines(self) -> Lines:
        return Lines()

    def describe(self) -> dict:
        return {"peer": self.kind}

    def close(self) -> None:
        pass


def _parse_address(text: str) -> tuple[str, int] | None:
    """host:port, or None if text isn't one."""
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit():
        return None
    return (host or "127.0.0.1"), int(port)


# Modem ---------------------------------------------------------------------------------------

# Hayes result codes: (verbose text, number) for ATV1 and ATV0.
_RESULTS = {"OK": 0, "CONNECT": 1, "RING": 2, "NO CARRIER": 3, "ERROR": 4, "NO DIALTONE": 6,
            "BUSY": 7, "NO ANSWER": 8}
_SREG_DEFAULTS = {0: 0, 1: 0, 2: 43, 3: 13, 4: 10, 5: 8, 6: 2, 7: 50, 8: 2, 9: 6, 10: 14, 11: 95, 12: 50}


@dataclass
class _Settings:
    echo: bool = True
    verbose: bool = True
    quiet: bool = False
    dtr_mode: int = 2       # &D2: dropping DTR hangs up
    dcd_mode: int = 1       # &C1: DCD follows the carrier (&C0: always on)
    sregs: dict = field(default_factory=lambda: dict(_SREG_DEFAULTS))


class Modem(Peer):
    """A Hayes-compatible modem whose phone line is TCP.

    dial: phone number -> "host:port"; digits only are compared, so "555-1234" matches "5551234".
    default: where any other number goes (None: unknown numbers get NO ANSWER). A number that is
    itself host:port (ATDT 127.0.0.1:2323) is dialled as it stands.
    connect: what CONNECT reports; None reports the guest UART's speed (the pump supplies it).
    """

    kind = "modem"

    def __init__(self, dial: dict[str, str] | None = None, default: str | None = None,
                 connect_timeout: float = 5.0):
        self.dial_map = {"".join(c for c in k if c.isdigit()): v for k, v in (dial or {}).items()}
        self.default = default
        self.connect_timeout = connect_timeout
        self.s = _Settings()
        self.cmd = bytearray()          # the command line being typed
        self.last_cmd = b""
        self.out = bytearray()          # to the guest
        self.sock: socket.socket | None = None
        self.online = False             # data mode (else command mode, maybe with a call up)
        self.pending_dial: str | None = None
        self.baud: int | None = None    # the guest UART's speed, for CONNECT
        self.dtr = False
        self.last_data_ms = -10**9      # when the guest last sent anything (for +++ guard time)
        self.plus = 0                   # +'s typed in a row after a guard time
        self.plus_ms = 0
        self.log: list[str] = []        # what happened, for serial_attach's report

    # guest side ------------------------------------------------------------------------------

    def _guard_ms(self) -> int:
        return self.s.sregs[12] * 20     # S12 is in fiftieths of a second

    def from_guest(self, data: bytes, now_ms: int, dtr: bool, rts: bool) -> None:
        if self.dtr and not dtr and self.sock and self.s.dtr_mode == 2:
            self._hangup("DTR dropped")
            self._result("NO CARRIER")
        self.dtr = dtr
        if not data:
            return
        if self.online:
            esc = self.s.sregs[2]
            for b in data:
                if b == esc and (self.plus or now_ms - self.last_data_ms >= self._guard_ms()) and self.plus < 3:
                    self.plus += 1
                    self.plus_ms = now_ms
                else:
                    self.plus = 0
                self.last_data_ms = now_ms
            self._send(data)
            return
        for b in data:
            self._command_byte(b)

    def _command_byte(self, b: int) -> None:
        cr, bs = self.s.sregs[3], self.s.sregs[5]
        if self.s.echo:
            self.out.append(b)
        if b == bs:
            if self.cmd:
                self.cmd.pop()
            return
        if b == cr:
            line, self.cmd = bytes(self.cmd), bytearray()
            self._command_line(line)
            return
        if b == 10 and not self.cmd:
            return
        self.cmd.append(b)
        if self.cmd[-2:].upper() == b"A/":
            self.cmd = bytearray()
            if self.s.echo:
                self.out += bytes([cr])
            self._command_line(self.last_cmd)

    def _command_line(self, line: bytes) -> None:
        text = line.decode("latin-1").strip()
        if not text:
            return
        if text[:2].upper() != "AT":
            return                       # not for the modem
        self.last_cmd = line
        result = self._execute(text[2:])
        if result:
            self._result(result)

    def _execute(self, rest: str) -> str | None:
        """Run the commands after AT; the result code to report, or None (a dial reports later)."""
        i = 0
        info = []

        def number() -> int:
            nonlocal i
            j = i
            while i < len(rest) and rest[i].isdigit():
                i += 1
            return int(rest[j:i]) if i > j else 0

        while i < len(rest):
            c = rest[i].upper()
            i += 1
            if c == " ":
                continue
            if c == "D":
                self.pending_dial = rest[i:].strip()
                self.log.append(f"dial {self.pending_dial!r}")
                return None
            if c == "Z":
                number()
                self._hangup("ATZ")
                self.s = _Settings()
            elif c == "E":
                self.s.echo = bool(number())
            elif c == "V":
                self.s.verbose = bool(number())
            elif c == "Q":
                self.s.quiet = bool(number())
            elif c == "H":
                if number() == 0:
                    self._hangup("ATH")
            elif c == "O":
                number()
                if not self.sock:
                    return "NO CARRIER"
                self.online, self.plus = True, 0
                return "CONNECT"
            elif c == "A":
                return "NO CARRIER"      # nobody ever calls in
            elif c == "I":
                info.append(("86box-mcp modem", "0")[min(number(), 1)])
            elif c == "S":
                reg = number()
                if i < len(rest) and rest[i] == "=":
                    i += 1
                    self.s.sregs[reg] = number() & 0xFF
                elif i < len(rest) and rest[i] == "?":
                    i += 1
                    info.append(f"{self.s.sregs.get(reg, 0):03d}")
                else:
                    return "ERROR"
            elif c == "&":
                if i >= len(rest):
                    return "ERROR"
                sub = rest[i].upper()
                i += 1
                n = number()
                if sub == "F":
                    self.s = _Settings()
                elif sub == "D":
                    self.s.dtr_mode = n
                elif sub == "C":
                    self.s.dcd_mode = n
            elif c.isalpha() or c in "\\%":
                number()                 # M, L, X, B, N, W and the like: accepted, no effect
            else:
                return "ERROR"
        for text in info:
            self.out += f"\r\n{text}\r\n".encode() if self.s.verbose else f"{text}\r\n".encode()
        return "OK"

    def _result(self, name: str, suffix: str = "") -> None:
        self.log.append(name + suffix)
        if self.s.quiet:
            return
        if self.s.verbose:
            self.out += f"\r\n{name}{suffix}\r\n".encode()
        else:
            self.out += f"{_RESULTS[name]}\r".encode()

    # line side -------------------------------------------------------------------------------

    def _target(self, number: str) -> tuple[str, int] | None:
        stripped = number.lstrip("TtPp").strip()
        addr = _parse_address(stripped.replace(" ", ""))
        if addr:
            return addr
        digits = "".join(c for c in stripped if c.isdigit())
        target = self.dial_map.get(digits, self.default)
        return _parse_address(target) if target else None

    def _dial(self, number: str) -> None:
        target = self._target(number)
        if target is None:
            self._result("NO ANSWER")
            return
        try:
            sock = socket.create_connection(target, timeout=self.connect_timeout)
        except ConnectionRefusedError:
            self.log.append(f"{target[0]}:{target[1]} refused")
            self._result("BUSY")
            return
        except OSError as e:
            self.log.append(f"{target[0]}:{target[1]}: {e}")
            self._result("NO ANSWER")
            return
        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock, self.online, self.plus = sock, True, 0
        self._result("CONNECT", f" {self.baud}" if self.baud and self.s.verbose else "")

    def _hangup(self, why: str) -> None:
        if self.sock:
            self.log.append(f"hang up ({why})")
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock, self.online, self.plus = None, False, 0

    def _send(self, data: bytes) -> None:
        if not self.sock:
            return
        try:
            self.sock.sendall(data)
        except BlockingIOError:
            pass                         # a loopback server that can't keep up at modem speeds
        except OSError:
            self._hangup("line dropped")
            self._result("NO CARRIER")

    def to_guest(self, now_ms: int) -> bytes:
        if self.pending_dial is not None:
            number, self.pending_dial = self.pending_dial, None
            self._dial(number)
        if self.plus == 3 and now_ms - self.plus_ms >= self._guard_ms():
            self.plus, self.online = 0, False
            self.log.append("escape to command mode")
            self._result("OK")
        if self.sock and self.online:
            while True:
                try:
                    chunk = self.sock.recv(65536)
                except BlockingIOError:
                    break
                except OSError:
                    chunk = b""
                if not chunk:
                    self._hangup("remote closed")
                    self._result("NO CARRIER")
                    break
                self.out += chunk
        elif self.sock:
            # In command mode with a call up: notice if the far end goes away.
            try:
                if self.sock.recv(65536, socket.MSG_PEEK) == b"":
                    self._hangup("remote closed")
            except BlockingIOError:
                pass
            except OSError:
                self._hangup("line dropped")
        data, self.out = bytes(self.out), bytearray()
        return data

    def lines(self) -> Lines:
        return Lines(cts=True, dsr=True, dcd=bool(self.sock) or self.s.dcd_mode == 0)

    def describe(self) -> dict:
        return {"peer": self.kind, "connected": bool(self.sock), "online": self.online,
                "default": self.default, "dial": dict(self.dial_map), "log": self.log[-20:]}

    def close(self) -> None:
        self._hangup("detached")


# Raw peers -----------------------------------------------------------------------------------


class PtyPeer(Peer):
    """A pseudo-terminal: host programs open `path` and see the guest's port, raw. POSIX only."""

    kind = "pty"

    def __init__(self, link: str | None = None):
        import pty
        import tty

        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.path = os.ttyname(self.slave)
        # We keep the slave open too, so the master never sees EIO between programs that open it.
        self.link = link
        if link:
            if os.path.islink(link):
                os.unlink(link)
            os.symlink(self.path, link)
        self.pending = b""

    def from_guest(self, data: bytes, now_ms: int, dtr: bool, rts: bool) -> None:
        data = self.pending + data
        self.pending = b""
        while data:
            try:
                n = os.write(self.master, data)
            except BlockingIOError:
                break                    # nobody is reading: keep it for later
            data = data[n:]
        self.pending = data[-65536:]

    def to_guest(self, now_ms: int) -> bytes:
        try:
            return os.read(self.master, 65536)
        except BlockingIOError:
            return b""
        except OSError as e:
            if e.errno == errno.EIO:
                return b""
            raise

    def describe(self) -> dict:
        return {"peer": self.kind, "path": self.path, "link": self.link}

    def close(self) -> None:
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        if self.link and os.path.islink(self.link):
            os.unlink(self.link)


class TcpPeer(Peer):
    """Listens on host:port; the client connected gets the guest's port, raw. DCD and DSR are up
    only while a client is connected, as if it were the far end of a cable."""

    kind = "tcp"

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.server = socket.create_server((host, port))
        self.server.setblocking(False)
        self.address = self.server.getsockname()[:2]
        self.client: socket.socket | None = None
        self.clients = 0

    def _accept(self) -> None:
        if self.client:
            return
        try:
            self.client, _ = self.server.accept()
        except BlockingIOError:
            return
        self.client.setblocking(False)
        self.client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.clients += 1

    def _drop(self) -> None:
        if self.client:
            self.client.close()
        self.client = None

    def from_guest(self, data: bytes, now_ms: int, dtr: bool, rts: bool) -> None:
        self._accept()
        if self.client and data:
            try:
                self.client.sendall(data)
            except BlockingIOError:
                pass
            except OSError:
                self._drop()

    def to_guest(self, now_ms: int) -> bytes:
        self._accept()
        if not self.client:
            return b""
        out = bytearray()
        while True:
            try:
                chunk = self.client.recv(65536)
            except BlockingIOError:
                break
            except OSError:
                chunk = b""
            if not chunk:
                self._drop()
                break
            out += chunk
        return bytes(out)

    def lines(self) -> Lines:
        up = self.client is not None
        return Lines(cts=True, dsr=up, dcd=up)

    def describe(self) -> dict:
        return {"peer": self.kind, "address": f"{self.address[0]}:{self.address[1]}",
                "connected": self.client is not None, "clients": self.clients}

    def close(self) -> None:
        self._drop()
        self.server.close()
