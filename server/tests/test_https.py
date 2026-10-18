"""The MCP server over streamable HTTP with TLS (BOX86_MCP_TLS_CERT / BOX86_MCP_TLS_KEY)."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl for a test certificate")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def https_server(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    port = _free_port()
    env = dict(os.environ, BOX86_MCP_HTTP_PORT=str(port), BOX86_MCP_TLS_CERT=str(cert), BOX86_MCP_TLS_KEY=str(key))
    proc = subprocess.Popen([sys.executable, "-m", "box86mcp.server"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read().decode(errors="replace"))
            time.sleep(0.2)
    yield port, cert
    proc.terminate()
    proc.wait(10)


def test_mcp_over_https(https_server):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port, cert = https_server

    async def go():
        async with httpx2.AsyncClient(verify=ssl.create_default_context(cafile=str(cert))) as http:
            async with streamable_http_client(f"https://localhost:{port}/mcp", http_client=http) as (r, w, *_):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return [t.name for t in (await session.list_tools()).tools]

    names = asyncio.run(go())
    assert "vm_start" in names and "screenshot" in names


def test_plain_http_is_refused(https_server):
    import httpx2

    port, _ = https_server
    with pytest.raises(httpx2.HTTPError):
        httpx2.post(f"http://127.0.0.1:{port}/mcp", json={}, timeout=5)


def test_https_origin_is_allowed(https_server):
    """The SDK's DNS-rebinding protection allows only http:// origins on loopback by default."""
    import httpx2

    port, cert = https_server
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}}
    ctx = ssl.create_default_context(cafile=str(cert))
    ok = httpx2.post(f"https://localhost:{port}/mcp", json=body, verify=ctx, timeout=10,
                     headers=headers | {"Origin": f"https://localhost:{port}"})
    assert ok.status_code == 200, ok.text
    bad = httpx2.post(f"https://localhost:{port}/mcp", json=body, verify=ctx, timeout=10,
                      headers=headers | {"Origin": "https://evil.example"})
    assert bad.status_code == 403
