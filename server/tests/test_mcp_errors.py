"""Through a real MCP client: failures carry their message, and vm_start finds a VM by itself."""

from __future__ import annotations

import asyncio
import os
import sys

from conftest import make_dos_vm


def _session(cache_dir, calls):
    """Run calls [(tool, args)] in one stdio session; return [(is_error, text)]."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ, XDG_CACHE_HOME=str(cache_dir), LOCALAPPDATA=str(cache_dir))
    env.pop("BOX86_VM_DIR", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "box86mcp.server"], env=env)

    async def go():
        out = []
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name: t for t in (await s.list_tools()).tools}
                assert "vm_dir" in tools["vm_start"].input_schema["properties"]   # schema survives the wrapper
                for tool, args in calls:
                    res = await s.call_tool(tool, args)
                    out.append((res.is_error, "".join(c.text for c in res.content if c.type == "text")))
        return out

    return asyncio.run(go())


def test_failures_say_why(tmp_path):
    (nothing, why), (nothing2, why2) = _session(tmp_path, [("vm_start", {}), ("vm_status", {})])
    assert nothing and "no VM to start: pass vm_dir" in why
    assert nothing2 and "no VM running; call vm_start first" in why2


def test_vm_start_remembers_the_last_vm(tmp_path):
    vm = make_dos_vm(tmp_path / "vm")
    first = _session(tmp_path / "cache", [("vm_start", {"vm_dir": str(vm)}), ("vm_stop", {})])
    assert not first[0][0], first
    again = _session(tmp_path / "cache", [("vm_start", {}), ("vm_stop", {})])
    assert not again[0][0] and str(vm) in again[0][1], again
