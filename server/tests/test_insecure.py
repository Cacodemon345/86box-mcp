"""Insecure mode (BOX86_INSECURE=1): MCP over HTTP on a non-loopback address, and 86Box's TCP
automation socket without its token (86Box run with --automation-insecure)."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import make_dos_vm


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lan_address() -> str | None:
    """An address of this machine other than loopback, if it has one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))     # TEST-NET-1: nothing is sent; this picks the route
            addr = s.getsockname()[0]
        except OSError:
            return None
    return None if addr.startswith("127.") else addr


def _server(env_extra: dict) -> subprocess.Popen:
    env = dict(os.environ, **env_extra)
    return subprocess.Popen([sys.executable, "-m", "box86mcp.server"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def test_non_loopback_host_needs_insecure_mode():
    p = _server({"BOX86_MCP_HTTP_PORT": str(_free_port()), "BOX86_MCP_HTTP_HOST": "0.0.0.0",
                 "BOX86_INSECURE": "0"})
    out, _ = p.communicate(timeout=30)
    assert p.returncode != 0 and b"BOX86_INSECURE=1" in out


def test_insecure_mode_serves_the_network():
    addr = _lan_address()
    if addr is None:
        pytest.skip("no non-loopback address")
    port = _free_port()
    p = _server({"BOX86_MCP_HTTP_PORT": str(port), "BOX86_MCP_HTTP_HOST": "0.0.0.0", "BOX86_INSECURE": "1"})
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                socket.create_connection((addr, port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)

        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async def go():
            async with streamable_http_client(f"http://{addr}:{port}/mcp") as (r, w, *_):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return [t.name for t in (await session.list_tools()).tools]

        assert "vm_start" in asyncio.run(go())
    finally:
        p.terminate()
        out = p.communicate(timeout=10)[0]
    assert b"WARNING: insecure mode" in out


INSECURE_BINARY = os.environ.get("BOX86_INSECURE_BINARY")


@pytest.mark.skipif(not INSECURE_BINARY, reason="set BOX86_INSECURE_BINARY to an 86Box with --automation-insecure")
def test_tcp_without_a_token(tmp_path, monkeypatch):
    from box86mcp import vm as V

    monkeypatch.setenv("BOX86_TRANSPORT", "tcp")
    monkeypatch.setattr(V, "INSECURE", True)
    vm = V.VM(make_dos_vm(tmp_path / "vm"), binary=Path(INSECURE_BINARY))
    vm.start(held=True)
    try:
        assert vm.token_file is None
        assert not list(vm.run_dir.glob("*.token")) and not (tmp_path / "vm" / "automation.token").exists()
        assert vm.auto.wait_text("C:\\>", 60000)["matched"]
        vm.auto.close()                                   # one client at a time
        other = V.AttachedVM(vm.endpoint)                 # no token
        assert other.auto.status()["ms"] > 0
        other.stop()
        vm.auto.connect()
    finally:
        vm.stop()
