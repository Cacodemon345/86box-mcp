"""A minimal GDB Remote Serial Protocol client for 86Box's GDB stub (built with -D GDBSTUB=ON).

86Box's stub speaks the i386 register layout, takes software and hardware breakpoints and memory
watchpoints, single-steps instructions, and has monitor commands for I/O ports (`qRcmd`).
"""

from __future__ import annotations

import socket
import struct

# Order of the registers in a 'g' reply (i386 target description).
REGS = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi", "eip", "eflags",
        "cs", "ss", "ds", "es", "fs", "gs"]

BREAK_KINDS = {"exec_sw": 0, "exec": 1, "write": 2, "read": 3, "access": 4}


class GdbError(RuntimeError):
    pass


class Gdb:
    def __init__(self, host: str = "127.0.0.1", port: int = 12345, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        # GDB acks on connecting. 86Box's stub waits for an ack before it writes any response, on
        # the emulator thread, so without this the first query blocks the emulator.
        self.sock.sendall(b"+")

    def close(self) -> None:
        self.sock.close()

    # ---- framing ----

    def _recv_byte(self) -> int:
        while not self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise GdbError("stub closed the connection")
            self.buf += chunk
        b, self.buf = self.buf[0], self.buf[1:]
        return b

    def send(self, payload: str) -> None:
        data = payload.encode("latin-1")
        frame = b"$" + data + b"#" + b"%02x" % (sum(data) & 0xFF)
        for _ in range(3):
            self.sock.sendall(frame)
            if self._wait_ack():
                return
        raise GdbError(f"no ack for {payload!r}")

    def _wait_ack(self) -> bool:
        while True:
            b = self._recv_byte()
            if b == ord("+"):
                return True
            if b == ord("-"):
                return False
            # Anything else before the ack belongs to an asynchronous packet; keep it.
            self.buf = bytes([b]) + self.buf
            return True

    def recv(self, timeout: float | None = None) -> str:
        """Read one packet, ack it, and return its payload."""
        old = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while self._recv_byte() != ord("$"):
                pass
            body = bytearray()
            while (b := self._recv_byte()) != ord("#"):
                body.append(b)
            self._recv_byte(); self._recv_byte()   # checksum; TCP already guards the bytes
            self.sock.sendall(b"+")
            return body.decode("latin-1")
        finally:
            self.sock.settimeout(old)

    def cmd(self, payload: str, timeout: float | None = None) -> str:
        self.send(payload)
        return self.recv(timeout)

    # ---- commands ----

    def halt_reason(self) -> str:
        return self.cmd("?")

    def interrupt(self) -> str:
        """Stop a running CPU (Ctrl+C), and return the stop reply."""
        self.sock.sendall(b"\x03")
        return self.recv()

    def regs(self) -> dict[str, int]:
        raw = bytes.fromhex(self.cmd("g"))
        n = min(len(REGS), len(raw) // 4)
        return dict(zip(REGS, struct.unpack(f"<{n}I", raw[: n * 4])))

    def mem(self, addr: int, length: int) -> bytes:
        reply = self.cmd(f"m{addr:x},{length:x}")
        if reply.startswith("E"):
            raise GdbError(f"read {addr:#x}: {reply}")
        return bytes.fromhex(reply)

    def set_break(self, addr: int, kind: str = "exec", length: int = 1) -> None:
        reply = self.cmd(f"Z{BREAK_KINDS[kind]},{addr:x},{length:x}")
        if reply != "OK":
            raise GdbError(f"set {kind} break at {addr:#x}: {reply!r}")

    def clear_break(self, addr: int, kind: str = "exec", length: int = 1) -> None:
        reply = self.cmd(f"z{BREAK_KINDS[kind]},{addr:x},{length:x}")
        if reply != "OK":
            raise GdbError(f"clear {kind} break at {addr:#x}: {reply!r}")

    def cont(self) -> None:
        """Resume; the stop reply comes later, when a breakpoint hits (read it with recv())."""
        self.send("c")

    def step(self, timeout: float | None = None) -> str:
        """Execute one instruction and return the stop reply."""
        return self.cmd("s", timeout)

    def monitor(self, command: str) -> str:
        """Run a stub monitor command (`ib 0x60`, `ob 0x80 0x12`, `r`) and return its output."""
        self.send("qRcmd," + command.encode().hex())
        out = []
        while True:
            reply = self.recv()
            if reply.startswith("O") and reply != "OK":
                out.append(bytes.fromhex(reply[1:]).decode("latin-1"))
                continue
            if reply and reply != "OK" and not reply.startswith("E"):
                out.append(bytes.fromhex(reply).decode("latin-1"))
            return "".join(out)


def describe_stop(stop: str) -> str:
    """'breakpoint', 'write to 0x46c' and so on, from a stop reply like T05hwbreak:;..."""
    fields = dict(f.split(":", 1) for f in stop[3:].split(";") if ":" in f)
    for key, what in (("watch", "write to"), ("rwatch", "read of"), ("awatch", "access to")):
        if key in fields:
            return f"{what} {int(fields[key], 16):#x}"
    if "hwbreak" in fields or "swbreak" in fields:
        return "breakpoint"
    return "debugger stop"
