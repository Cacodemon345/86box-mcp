#!/usr/bin/env python3
"""A tiny BBS for testing serial terminal programs in a guest, reached through serial_attach's modem.

    python examples/telix/fakebbs.py [--port 2323] [--log bbs.log] [--files DIR]

The guest's terminal program (Telix, say) dials the emulated modem, which connects here. Callers
get a banner, a name prompt and a menu:

  E  echo: each line typed comes back, until an empty line
  L  lines: 200 numbered lines, then the CRC-32 of them all, to check bulk transfer
  D  download TEST.BIN (8 KiB of a fixed pattern) by XMODEM-CRC; K for XMODEM-1K
  U  upload by XMODEM; the BBS reports the size and CRC-32 of what it got, and saves it
  G  goodbye (hangs up)

Everything sent and received goes to the log, one line per chunk with its direction, so a test
can check what crossed the line. The guest's clock only moves while the VM steps, so timeouts here
are long (a minute).
"""

from __future__ import annotations

import argparse
import socket
import time
import zlib
from pathlib import Path

SOH, STX, EOT, ACK, NAK, CAN, SUB = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18, 0x1A
TIMEOUT = 60.0


def test_file() -> bytes:
    """TEST.BIN: 8 KiB that catches dropped, doubled and swapped bytes, and has every byte value."""
    return bytes((i * 7 + (i >> 8)) & 0xFF for i in range(8192))


def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


class Caller:
    def __init__(self, conn: socket.socket, log, files: Path):
        self.conn, self.log, self.files = conn, log, files
        self.buf = bytearray()
        conn.settimeout(TIMEOUT)

    def note(self, text: str) -> None:
        self.log.write(f"{time.strftime('%H:%M:%S')} -- {text}\n")
        self.log.flush()

    def send(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.replace("\n", "\r\n").encode("latin-1")
        self.log.write(f"{time.strftime('%H:%M:%S')} >> {data!r}\n")
        self.log.flush()
        self.conn.sendall(data)

    def byte(self, timeout: float = TIMEOUT) -> int | None:
        if not self.buf:
            self.conn.settimeout(timeout)
            try:
                chunk = self.conn.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError("caller hung up")
            self.log.write(f"{time.strftime('%H:%M:%S')} << {chunk!r}\n")
            self.log.flush()
            self.buf += chunk
        return self.buf.pop(0)

    def line(self, prompt: str = "", echo: bool = True) -> str:
        """A line typed by the caller, echoed back as they type it (the BBS does the echoing)."""
        if prompt:
            self.send(prompt)
        out = bytearray()
        while True:
            b = self.byte()
            if b is None:
                raise ConnectionError("caller went quiet")
            if b in (13, 10):
                if b == 10 and not out:
                    continue
                self.send(b"\r\n")
                return out.decode("latin-1")
            if b in (8, 127):
                if out:
                    out.pop()
                    if echo:
                        self.send(b"\b \b")
                continue
            if 32 <= b < 256:
                out.append(b)
                if echo:
                    self.send(bytes([b]))

    # session -----------------------------------------------------------------------------

    def run(self) -> None:
        self.send("\x1b[2J\x1b[1;36m*** 86box-mcp test BBS ***\x1b[0m\n"
                  "You're connected through an emulated modem.\n\n")
        name = self.line("Your name: ").strip() or "anonymous"
        self.note(f"name {name!r}")
        self.send(f"Hello, {name}!\n")
        while True:
            choice = self.line("\n[E]cho [L]ines [D]ownload [K] 1K download [U]pload [G]oodbye: ").strip().upper()
            self.note(f"menu {choice!r}")
            if choice == "E":
                self.send("Echo test: type lines, an empty line to stop.\n")
                while True:
                    text = self.line("> ")
                    if not text:
                        break
                    self.send(f"ECHO: {text}\n")
            elif choice == "L":
                lines = "".join(f"LINE {i:03d} the quick brown fox jumps over the lazy dog\r\n" for i in range(1, 201))
                self.send(lines.encode())
                self.send(f"END OF LINES crc32={zlib.crc32(lines.encode()):08x}\n")
            elif choice in ("D", "K"):
                data = test_file()
                self.send(f"Sending TEST.BIN, {len(data)} bytes, crc32={zlib.crc32(data):08x}. "
                          f"Start your XMODEM{'-1K' if choice == 'K' else ''} download now.\n")
                ok = self.xmodem_send(data, 1024 if choice == "K" else 128)
                self.note(f"xmodem send {'ok' if ok else 'FAILED'}")
                self.send(f"\nDownload {'complete' if ok else 'FAILED'}.\n")
            elif choice == "U":
                self.send("Start your XMODEM upload now.\n")
                data = self.xmodem_receive()
                if data is None:
                    self.note("xmodem receive FAILED")
                    self.send("\nUpload FAILED.\n")
                else:
                    trimmed = data.rstrip(bytes([SUB]))
                    (self.files / "UPLOAD.BIN").write_bytes(data)
                    self.note(f"xmodem receive ok: {len(data)} bytes, {len(trimmed)} without padding, "
                              f"crc32={zlib.crc32(trimmed):08x}")
                    self.send(f"\nGot {len(trimmed)} bytes (without padding), crc32={zlib.crc32(trimmed):08x}.\n")
            elif choice == "G":
                self.send("Goodbye!\n")
                self.note("goodbye")
                return

    # XMODEM ------------------------------------------------------------------------------

    def xmodem_send(self, data: bytes, size: int) -> bool:
        b = self.byte()
        while b not in (NAK, ord("C")):
            if b is None or b == CAN:
                return False
            b = self.byte()
        use_crc = b == ord("C")
        self.note(f"xmodem send: receiver wants {'CRC' if use_crc else 'checksum'}, blocks of {size}")
        blocks = [data[i:i + size] for i in range(0, len(data), size)]
        for n, block in enumerate(blocks, 1):
            block = block.ljust(size, bytes([SUB]))
            check = crc16(block).to_bytes(2, "big") if use_crc else bytes([sum(block) & 0xFF])
            packet = bytes([STX if size == 1024 else SOH, n & 0xFF, 0xFF - (n & 0xFF)]) + block + check
            for attempt in range(10):
                self.conn.sendall(packet)
                reply = self.byte()
                if reply == ACK:
                    break
                if reply == CAN:
                    return False
                self.note(f"block {n}: {reply!r}, sending again")
            else:
                return False
        for attempt in range(10):
            self.conn.sendall(bytes([EOT]))
            if self.byte() == ACK:
                return True
        return False

    def xmodem_receive(self) -> bytes | None:
        out = bytearray()
        expect = 1
        # The receiver drives XMODEM: send C every 3 s until the sender starts. Anything else that
        # arrives meanwhile (the end of the menu line, say) is dropped, not taken for a reply.
        self.buf.clear()
        head = None
        for attempt in range(20):
            self.conn.sendall(b"C")
            end = time.time() + 3.0
            while time.time() < end:
                b = self.byte(max(0.05, end - time.time()))
                if b in (SOH, STX, EOT, CAN):
                    head = b
                    break
            if head is not None:
                break
        else:
            return None
        while True:
            if head == EOT:
                self.conn.sendall(bytes([ACK]))
                return bytes(out)
            if head == CAN:
                return None
            if head not in (SOH, STX):
                head = self.byte()
                continue
            size = 1024 if head == STX else 128
            rest = bytearray()
            while len(rest) < size + 4:
                b = self.byte(5.0)
                if b is None:
                    break
                rest.append(b)
            if len(rest) == size + 4 and rest[0] == 0xFF - rest[1] and \
                    crc16(bytes(rest[2:2 + size])) == int.from_bytes(rest[2 + size:], "big"):
                if rest[0] == expect & 0xFF:
                    out += rest[2:2 + size]
                    expect += 1
                self.conn.sendall(bytes([ACK]))
            else:
                self.note(f"bad block {expect}")
                self.conn.sendall(bytes([NAK]))
            head = self.byte()
            if head is None:
                return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=2323)
    ap.add_argument("--log", default="fakebbs.log")
    ap.add_argument("--files", default=".", help="where uploads are saved")
    ap.add_argument("--once", action="store_true", help="exit after the first caller")
    args = ap.parse_args()
    srv = socket.create_server(("127.0.0.1", args.port))
    print(f"fakebbs listening on 127.0.0.1:{args.port}", flush=True)
    with open(args.log, "a", encoding="latin-1") as log:
        while True:
            conn, addr = srv.accept()
            caller = Caller(conn, log, Path(args.files))
            caller.note(f"call from {addr[0]}:{addr[1]}")
            try:
                caller.run()
            except (ConnectionError, OSError) as e:
                caller.note(f"call ended: {e}")
            finally:
                conn.close()
                caller.note("hung up")
            if args.once:
                return


if __name__ == "__main__":
    main()
