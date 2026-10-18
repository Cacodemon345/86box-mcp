"""Several VMs in one server, through a real MCP client: named VMs, and lockstep."""

from __future__ import annotations

import asyncio
import os
import sys

from conftest import make_dos_vm


def test_two_vms_in_lockstep(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    a, b = make_dos_vm(tmp_path / "a"), make_dos_vm(tmp_path / "b")
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "box86mcp.server"], env=env)

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()

                async def call(tool, **args):
                    res = await s.call_tool(tool, args)
                    text = "".join(c.text for c in res.content if c.type == "text")
                    assert not res.is_error, f"{tool}: {text}"
                    return res.structured_content["result"] if tool == "vm_list" else text

                out = {}
                await call("vm_start", vm="p1", vm_dir=str(a))
                await call("step", vm="p1", ms=700)               # p1 starts ahead
                await call("vm_start", vm="p2", vm_dir=str(b))
                out["step_all"] = await call("step_all", ms=8000, quantum_ms=5)
                out["list"] = await call("vm_list")
                out["p1"] = await call("screen_text", vm="p1")
                out["p2"] = await call("screen_text", vm="p2")
                out["release"] = await call("release_all")
                await asyncio.sleep(1)
                out["hold"] = await call("hold_all")
                out["list2"] = await call("vm_list")
                out["dir_p2"] = await call("dos_run", vm="p2", command="DIR C:\\AUTOEXEC.BAT")
                await call("vm_stop", vm="p1")
                await call("vm_stop", vm="p2")
                return out

    out = asyncio.run(go())
    lst = out["list"]
    by = {v["name"]: v for v in lst}
    assert by["p1"]["running"] and by["p2"]["running"] and by["p1"]["ms"] == by["p2"]["ms"], lst
    assert by["p2"]["current"]                                   # the last one named
    assert "C:\\>" in out["p1"] and "C:\\>" in out["p2"]
    lst2 = out["list2"]
    assert len({v["ms"] for v in lst2}) == 1 and all(v["held"] for v in lst2), lst2
    assert "AUTOEXEC BAT" in out["dir_p2"]
