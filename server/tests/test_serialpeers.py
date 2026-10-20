"""The host-side serial peers, driven directly as the pump would drive them (no 86Box needed)."""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

from box86mcp.serialpeers import Modem, PtyPeer, TcpPeer


class Clock:
    """Emulated milliseconds, advanced by the test."""

    def __init__(self):
        self.ms = 100_000


def drive(peer, data: bytes, clock: Clock, dtr: bool = True, ms: int = 10, rounds: int = 1) -> bytes:
    """Feed the guest's bytes, then collect what the peer sends back over a few quanta."""
    out = b""
    for i in range(rounds):
        peer.from_guest(data if i == 0 else b"", clock.ms, dtr, True)
        out += peer.to_guest(clock.ms)
        clock.ms += ms
    return out


def settle(peer, clock: Clock, until: bytes = b"", timeout: float = 3.0, dtr: bool = True) -> bytes:
    """Pump until the guest has received `until` (data from the TCP side arrives on the host's clock)."""
    out = b""
    end = time.time() + timeout
    while time.time() < end:
        out += drive(peer, b"", clock, dtr)
        if until and until in out:
            return out
        time.sleep(0.01)
    return out


def until(peer, clock: Clock, needle: bytes, already: bytes = b"") -> bytes:
    """What the guest has received, waiting for needle if it isn't there yet."""
    return already if needle in already else already + settle(peer, clock, needle)


@pytest.fixture
def server():
    """A TCP server that greets, then echoes uppercased; it records what it received."""
    srv = socket.create_server(("127.0.0.1", 0))
    got = []
    conns = []

    def run():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            conns.append(c)
            try:
                c.sendall(b"WELCOME\r\n")
                while True:
                    d = c.recv(4096)
                    if not d:
                        break
                    got.append(d)
                    if b"BYE" in b"".join(got):
                        c.close()
                        break
                    c.sendall(d.upper())
            except OSError:
                pass                     # the modem hung up first

    t = threading.Thread(target=run, daemon=True)
    t.start()
    yield srv.getsockname()[1], got, conns
    for c in conns:
        c.close()                        # ends a recv the thread may be blocked in
    srv.close()                          # ends its accept
    t.join(2)
    for c in conns:
        c.close()


def test_at_commands_echo_and_results():
    m, c = Modem(), Clock()
    assert drive(m, b"AT\r", c) == b"AT\r\r\nOK\r\n"
    assert drive(m, b"ATE0\r", c) == b"ATE0\r\r\nOK\r\n"          # echo off takes effect after
    assert drive(m, b"AT\r", c) == b"\r\nOK\r\n"
    assert drive(m, b"ATV0\r", c) == b"0\r"
    assert drive(m, b"ATQ1\r", c) == b""
    assert drive(m, b"ATZ\r", c) == b"\r\nOK\r\n"     # typed with echo off; the reply is verbose again
    assert drive(m, b"AT\r", c) == b"AT\r\r\nOK\r\n"   # and echo is back on
    assert drive(m, b"ATS0=2S7?\r", c).endswith(b"\r\n050\r\n\r\nOK\r\n")
    assert drive(m, b"AT S0?\r", c).endswith(b"\r\n002\r\n\r\nOK\r\n")
    assert drive(m, b"AT&F&C1&D2 X4 M1 L0\r", c).endswith(b"\r\nOK\r\n")
    assert drive(m, b"ATI\r", c).endswith(b"\r\n86box-mcp modem\r\n\r\nOK\r\n")
    assert drive(m, b"AT#\r", c).endswith(b"\r\nERROR\r\n")
    assert drive(m, b"hello\r", c) == b"hello\r"                  # not a command: echoed, ignored
    assert drive(m, b"ATX\x08Q0\r", c).endswith(b"\r\nOK\r\n")    # backspace edits the line
    assert not m.lines().dcd


def test_dial_talk_escape_and_hang_up(server):
    port, got, conns = server
    m, c = Modem(dial={"555-1234": f"127.0.0.1:{port}"}), Clock()
    m.baud = 9600
    out = drive(m, b"ATDT 5551234\r", c)
    assert b"\r\nCONNECT 9600\r\n" in out, out                   # WELCOME may follow at once
    assert m.lines().dcd
    assert b"WELCOME" in until(m, c, b"WELCOME", out)
    assert b"HELLO THERE" in until(m, c, b"HELLO THERE", drive(m, b"hello there", c))
    # +++ needs a guard time of silence on both sides
    c.ms += 1500
    drive(m, b"+++", c)
    assert b"OK" not in drive(m, b"", c, ms=500)                  # not yet: 1 s guard
    c.ms += 600
    assert drive(m, b"", c).endswith(b"\r\nOK\r\n")
    assert m.lines().dcd                                          # still connected, command mode
    assert b"\r\nCONNECT\r\n" in drive(m, b"ATO\r", c)     # the server may echo the +++ back too
    assert drive(m, b"ab+++cd", c) is not None                    # +++ inside data isn't an escape
    c.ms += 2000
    assert b"OK" not in drive(m, b"", c)
    c.ms += 1500
    drive(m, b"+++", c)
    c.ms += 1100
    drive(m, b"", c)
    assert drive(m, b"ATH\r", c).endswith(b"\r\nOK\r\n")
    assert not m.lines().dcd
    assert b"hello there" in b"".join(got)


def test_dtr_drop_hangs_up(server):
    port, _, _ = server
    m, c = Modem(default=f"127.0.0.1:{port}"), Clock()
    assert b"CONNECT" in drive(m, b"ATDT1\r", c)
    assert m.lines().dcd
    assert drive(m, b"", c, dtr=False).endswith(b"\r\nNO CARRIER\r\n")
    assert not m.lines().dcd


def test_remote_close_is_no_carrier(server):
    port, got, _ = server
    m, c = Modem(), Clock()
    first = drive(m, f"ATDT127.0.0.1:{port}\r".encode(), c)
    assert b"CONNECT" in first
    second = drive(m, b"BYE", c)
    rest = second + (b"" if b"NO CARRIER" in second else settle(m, c, b"NO CARRIER"))
    assert b"NO CARRIER" in rest, (first, second, rest, got, m.log)
    assert not m.lines().dcd


def test_unknown_number_and_refused():
    s = socket.create_server(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    m, c = Modem(dial={"1": f"127.0.0.1:{free}"}), Clock()
    assert drive(m, b"ATDT2\r", c).endswith(b"\r\nNO ANSWER\r\n")
    assert drive(m, b"ATDT1\r", c).endswith(b"\r\nBUSY\r\n")


def test_repeat_last_command():
    m, c = Modem(), Clock()
    drive(m, b"ATS0=3\r", c)
    drive(m, b"ATS0=0\r", c)
    drive(m, b"ATS0?\r", c)
    assert drive(m, b"A/", c).endswith(b"\r\n000\r\n\r\nOK\r\n")


def test_tcp_peer():
    p, c = TcpPeer(), Clock()
    assert not p.lines().dcd
    cl = socket.create_connection(p.address)
    drive(p, b"", c)
    assert p.lines().dcd and p.lines().dsr
    cl.sendall(b"to guest")
    assert b"to guest" in until(p, c, b"to guest")
    drive(p, b"to client", c)
    cl.settimeout(2)
    assert cl.recv(100) == b"to client"
    cl.close()
    settle(p, c, timeout=0.3)
    assert not p.lines().dcd
    p.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX pty")
def test_pty_peer(tmp_path):
    link = str(tmp_path / "com1")
    p, c = PtyPeer(link), Clock()
    fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
    os.write(fd, b"from host\x00\xff")
    assert settle(p, c, b"from host\x00\xff") == b"from host\x00\xff"   # raw: no line discipline
    drive(p, b"from guest\r\n", c)
    time.sleep(0.05)
    assert os.read(fd, 100) == b"from guest\r\n"
    os.close(fd)
    p.close()
    assert not os.path.lexists(link)
