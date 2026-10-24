"""The GDB stub client, against a fake stub; and the debugging tools, against 86Box built with
-D GDBSTUB=ON (set BOX86_GDB_BINARY to it; those tests are skipped without)."""

from __future__ import annotations

import functools
import os
import sys
import socket
import struct
import threading
from pathlib import Path

import pytest

from box86mcp.gdbrsp import Gdb, describe_stop
from box86mcp.protocol import AutomationError

from conftest import make_dos_vm


def _frame(payload: str) -> bytes:
    data = payload.encode()
    return b"$" + data + b"#" + b"%02x" % (sum(data) & 0xFF)


class FakeStub:
    """Answers like 86Box's stub: acks each packet, and writes nothing before the client's first ack."""

    def __init__(self, answers: dict[str, list[str]]):
        self.answers = answers
        self.packets: list[str] = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self.listener.accept()
        buf, acked = b"", False
        while chunk := conn.recv(4096):
            buf += chunk
            while buf:
                if buf[:1] == b"+":
                    acked, buf = True, buf[1:]
                elif buf[:1] == b"$" and b"#" in buf and len(buf) >= buf.index(b"#") + 3:
                    end = buf.index(b"#")
                    payload, buf = buf[1:end].decode(), buf[end + 3:]
                    self.packets.append(payload)
                    conn.sendall(b"+")
                    assert acked, "the client must ack before the stub responds"
                    key = next(k for k in self.answers if payload.startswith(k))
                    for reply in self.answers[key]:
                        conn.sendall(_frame(reply))
                else:
                    break


def test_client_framing_registers_and_monitor():
    regs = struct.pack("<16I", 0x12345678, *[0] * 7, 0xDBEC, *[0] * 7).hex()   # eax..edi, eip, ...
    stub = FakeStub({
        "?": ["T02"],
        "g": [regs],
        "Z1,dbec,1": ["OK"],
        "qRcmd,": ["O" + b"0020: 11\n".hex(), b"0021: B8\n".hex()],
        "m46c,4": ["0a0b0c0d"],
    })
    g = Gdb("127.0.0.1", stub.port)
    try:
        assert g.halt_reason() == "T02"
        r = g.regs()
        assert r["eax"] == 0x12345678 and r["eip"] == 0xDBEC
        g.set_break(0xDBEC, "exec")
        assert g.monitor("ib 20 2") == "0020: 11\n0021: B8\n"
        assert g.mem(0x46C, 4) == bytes([0x0A, 0x0B, 0x0C, 0x0D])
    finally:
        g.close()
    assert stub.packets[2] == "Z1,dbec,1"
    assert stub.packets[3] == "qRcmd," + b"ib 20 2".hex()


def test_describe_stop():
    assert describe_stop("T05hwbreak:;0:00840000;") == "breakpoint"
    assert describe_stop("T05watch:0000046c;0:1;") == "write to 0x46c"
    assert describe_stop("T05awatch:10;") == "access to 0x10"
    assert describe_stop("T02") == "debugger stop"


GDB_BINARY = os.environ.get("BOX86_GDB_BINARY")
needs_gdb_build = pytest.mark.skipif(not GDB_BINARY, reason="set BOX86_GDB_BINARY to an 86Box built "
                                     "with -D GDBSTUB=ON")


@pytest.fixture
def server(monkeypatch):
    from box86mcp import server as S
    monkeypatch.setattr(S, "VM", functools.partial(S.VM, binary=Path(GDB_BINARY)))
    yield S
    try:
        S.vm_stop()
    except Exception:
        pass


@needs_gdb_build
def test_stub_build_runs_without_a_debugger(tmp_path, server):
    """The stub starts the CPU stopped; without debugger=True the server lets it go by itself."""
    server.vm_start(str(make_dos_vm(tmp_path / "vm")))
    assert "found" in server.wait_for_text("C:\\>", 30000)


@needs_gdb_build
def test_stub_build_runs_in_real_time(tmp_path, server):
    """release, with no timed command at all, must let the stub's stopped CPU go too."""
    import time
    server.vm_start(str(make_dos_vm(tmp_path / "vm")))
    server.release()
    time.sleep(3)
    assert server.vm_status()["ms"] > 1000


@needs_gdb_build
def test_breakpoints_steps_and_ports(tmp_path, server):
    server.vm_start(str(make_dos_vm(tmp_path / "vm")), debugger=True)
    assert "found" in server.wait_for_text("C:\\>", 30000)
    v = server.vm()
    ivt = v.auto.memr(0x20, 4)
    isr = int.from_bytes(ivt[2:], "little") * 16 + int.from_bytes(ivt[:2], "little")

    server.breakpoint_set(isr)
    out = server.run_until_break(2000)
    assert out.startswith("breakpoint at") and f"linear {isr:#x}" in out
    with pytest.raises(AutomationError, match="debugger has the CPU stopped"):
        server.step(100)
    assert len(server.step_instructions(3).splitlines()) == 4
    assert server.run_until_break(2000).startswith("breakpoint at")     # the next timer tick
    server.breakpoint_set(isr, clear=True)
    assert server.run_until_break(300).startswith("no break in 300 ms")

    server.breakpoint_set(0x46C, "write", 4)            # the BIOS tick count
    assert server.run_until_break(2000).startswith("write to 0x46c")
    server.breakpoint_set(0x46C, "write", 4, clear=True)
    assert server.debug_continue().startswith("the CPU runs again")

    server.breakpoint_set(isr)                          # a hit during another tool's call
    with pytest.raises(AutomationError, match="breakpoint at linear"):
        server.step(1000)
    server.breakpoint_set(isr, clear=True)
    server.debug_continue()

    server.io_write(0x70, 0x0A)                         # CMOS status register A
    assert server.io_read(0x71) == "port 0x71: 0x26"
    assert "1039:0496 bridge" in server.pci_scan()      # the 486SP3C's SiS 496
    assert "MS-DOS Version 6.22" in server.dos_run("VER")


@needs_gdb_build
def test_debugger_through_an_mcp_client(tmp_path):
    """The same tools as an agent uses them: through MCP, over stdio, with errors as messages."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    vm_dir = make_dos_vm(tmp_path / "vm")
    env = dict(os.environ, BOX86_BINARY=GDB_BINARY, XDG_CACHE_HOME=str(tmp_path / "cache"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "box86mcp.server"], env=env)

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()

                async def call(tool, **args):
                    res = await s.call_tool(tool, args)
                    return res.is_error, "".join(c.text for c in res.content if c.type == "text")

                out = {"start": await call("vm_start", vm_dir=str(vm_dir), debugger=True),
                       "prompt": await call("wait_for_text", text="C:\\>", timeout_ms=30000)}
                ivt = bytes.fromhex("".join((await call("read_memory", addr=0x20, length=4))[1].split()[1:5]))
                isr = int.from_bytes(ivt[2:], "little") * 16 + int.from_bytes(ivt[:2], "little")
                out["isr"] = isr
                out["break"] = await call("breakpoint_set", addr=isr)
                out["run"] = await call("run_until_break", timeout_ms=2000)
                out["steps"] = await call("step_instructions", count=3)
                out["step_while_stopped"] = await call("step", ms=100)
                out["continue"] = await call("debug_continue")
                out["clear"] = await call("breakpoint_set", addr=isr, clear=True)
                out["cmos"] = (await call("io_write", port=0x70, value=0x0A), await call("io_read", port=0x71))
                out["pci"] = await call("pci_scan")
                out["dos"] = await call("dos_run", command="VER")
                await call("vm_stop")
                return out

    out = asyncio.run(go())
    assert not out["start"][0] and "debugger on port" in out["start"][1], out["start"]
    assert not out["prompt"][0], out["prompt"]
    assert not out["run"][0] and f"linear {out['isr']:#x}" in out["run"][1], out["run"]
    assert not out["steps"][0] and len(out["steps"][1].splitlines()) == 4, out["steps"]
    assert out["step_while_stopped"][0] and "debugger has the CPU stopped" in out["step_while_stopped"][1]
    assert not out["continue"][0] and "runs again" in out["continue"][1]
    assert out["cmos"][1] == (False, "port 0x71: 0x26"), out["cmos"]
    assert not out["pci"][0] and "1039:0496" in out["pci"][1], out["pci"]
    assert "MS-DOS Version 6.22" in out["dos"][1], out["dos"]


@needs_gdb_build
def test_an_attached_vm_finds_the_stub_port(tmp_path, monkeypatch):
    """vm_attach has no log to read the stub's port from; 86Box says it in info."""
    from box86mcp.vm import VM, AttachedVM

    monkeypatch.setenv("BOX86_TRANSPORT", "tcp")
    vm = VM(make_dos_vm(tmp_path / "vm"), binary=Path(GDB_BINARY))
    vm.start(held=True, debugger=True)                      # on a free port, not the default
    other = None
    try:
        vm.gdb.close()
        vm.gdb = None
        token = open(vm.token_file).read().strip()
        vm.auto.close()                                     # one client at a time
        other = AttachedVM(vm.endpoint, token)
        if "gdbstub_port" not in other.auto.info():
            pytest.skip("86Box without gdbstub_port in info")
        assert other._stub_port() == vm.gdb_port != 12345
        gdb = other.attach_debugger()
        assert other.auto.wait_text("C:\\>", 60000)["matched"]
        assert gdb.mem(0x449, 1) == b"\x03"                 # held, the stub still answers: text mode 3
    finally:
        if other:
            other.stop()
        vm.auto.connect()
        vm.stop()
