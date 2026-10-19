"""Show the protected-mode tools on PMDEMO.COM, a DOS program that builds a GDT, an LDT, an IDT, a
TSS and page tables, takes a page fault, sets DR0, and spins in 32-bit protected mode.

Talks to the 86box MCP server over stdio as an agent would, prints what each tool returns, and checks
it against what pmdemo.s sets up. Needs 86Box with 86Box/86Box#8416 and the usual BOX86_* settings
(a DOS base image in BOX86_DOS_BASE_IMG):

    server/.venv/bin/python examples/protected-mode/demo.py

Exit status is 0 only if every check passed. Output (a screenshot) goes to out/.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
VM_DIR = OUT / "vm"
SERVER = REPO / "server" / ".venv" / "bin" / "box86mcp"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


class Box:
    def __init__(self, session: ClientSession):
        self.s = session

    async def __call__(self, tool: str, show: bool = True, **args) -> str:
        res = await self.s.call_tool(tool, args)
        text = "\n".join(c.text for c in res.content if c.type == "text")
        if res.is_error:
            raise RuntimeError(f"{tool}: {text}")
        if show:
            args_s = ", ".join(f"{k}={v!r}" for k, v in args.items())
            print(f"\n> {tool}({args_s})\n" + "\n".join("  " + line for line in text.splitlines()))
        return text

    async def image(self, tool: str, path: Path, **args) -> None:
        res = await self.s.call_tool(tool, args)
        for c in res.content:
            if c.type == "image":
                path.write_bytes(base64.b64decode(c.data))


async def run(box: Box) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    await box("dos_create_vm", show=False, vm_dir=str(VM_DIR))
    await box("dos_install_files", show=False, paths=[str(HERE / "PMDEMO.COM")], dest_dir="\\", vm_dir=str(VM_DIR))
    await box("vm_start", show=False, vm_dir=str(VM_DIR), held=True)
    await box("wait_for_text", show=False, text="C:\\>", timeout_ms=60000)

    print("== At the DOS prompt: real mode")
    r = json.loads(await box("registers", show=False))
    check("real mode, no paging", r["cr0"] & 0x80000001 == 0, f"cr0 {r['cr0']:#x}")
    await box("descriptor_table", table="idt", first=0x08, count=4)

    print("\n== Run PMDEMO")
    await box("type_text", show=False, text="PMDEMO\n")
    for _ in range(40):
        await box("step", show=False, ms=100)
        r = json.loads(await box("registers", show=False))
        if r["ebx"] == 0xC0DEF00D:
            break
    await box("registers")
    marker = r["dr0"]
    check("protected mode with paging", r["cr0"] & 0x80000001 == 0x80000001, f"cr0 {r['cr0']:#x}")
    check("CR3 is the page directory", r["cr3"] == 0x80000)
    check("CR2 holds the address of the page fault", r["cr2"] == 0x3FF000, f"{r['cr2']:#x}")
    check("GDTR, IDTR, LDTR, TR", (r["gdtr_limit"], r["idtr_base"], r["idtr_limit"], r["ldtr"], r["tr"],
                                   r["tr_base"], r["ldtr_base"]) == (0x3F, 0x83000, 0xFF, 0x20, 0x28, 0x82000, 0x82100))
    check("FS and GS hold LDT selectors", (r["fs"], r["fs_base"], r["gs"], r["gs_base"]) ==
          (0x04, 0xC0000000, 0x0C, 0xB8000))
    check("CPL 0, DR0 set", r["cpl"] == 0 and marker != 0, f"dr0 {marker:#x}")

    f = json.loads(await box("fpu_registers"))
    check("FPU: 1.0 and pi on the stack", f["st0"] == "3fff8000000000000000" and f["st1"].startswith("4000c90fdaa22168c"))

    gdt = await box("descriptor_table", table="gdt", count=8)
    check("GDT decoded", all(s in gdt for s in ("code, base 00000000, limit ffffffff", "LDT, base 00082100",
                                                  "TSS32 busy, base 00082000", "call gate32 -> 0008:",
                                                  "not present", "DPL 3")))
    ldt = await box("descriptor_table", table="ldt")
    check("LDT decoded", "data, base c0000000" in ldt and "data, base 000b8000, limit 00000fff" in ldt)
    idt = await box("descriptor_table", table="idt", first=0, count=16)
    check("IDT decoded", "task gate -> 0028" in idt and "trap gate32" in idt and "interrupt gate32" in idt)

    print("\n== Addresses")
    t = await box("translate_address", address=0x10, selector="gs")
    check("GS:10 -> B8010 through the identity mapping", "physical 0xb8010" in t)
    t = await box("translate_address", address=marker, selector=0x04)
    check("LDT selector 04 (base C0000000) reaches the same bytes as DR0's address",
          f"physical {marker:#x}" in t and "PDE at 0x80c00" in t)
    t = await box("translate_address", address=0x3FF000)
    check("the page that faulted isn't mapped", "not mapped" in t)
    t = await box("translate_address", address=0, selector=0x18)
    check("translate a not-present segment", "not present" in t)

    print("\n== Reading through the page tables")
    dump = await box("read_memory", addr=0xC0000000 + marker, length=16, linear=True)
    check("read_memory(linear=True) at C0000000 + marker finds PMDEMO", "PMDEMO" in dump)
    tss = await box("read_memory", addr=r["tr_base"], length=0x20, linear=True)
    words = bytes.fromhex("".join(line[10:57] for line in tss.splitlines()))
    check("the TSS's ss0 and cr3", int.from_bytes(words[8:12], "little") == 0x10
          and int.from_bytes(words[0x1C:0x20], "little") == 0x80000)
    try:
        await box("read_memory", show=False, addr=0x3FF000, length=4, linear=True)
        check("an unmapped linear read fails", False)
    except RuntimeError as e:
        check("an unmapped linear read fails", "not mapped" in str(e), str(e).splitlines()[-1])

    await box.image("screenshot", OUT / "pmdemo.png")
    screen = await box("read_memory", show=False, addr=0xB8000, length=4)
    check("PM written to the screen through the LDT", screen.split()[1:5] == ["50", "1f", "4d", "1f"])
    await box("vm_stop", show=False)


async def main() -> int:
    if not (HERE / "PMDEMO.COM").exists():
        print("PMDEMO.COM missing: see the build line in pmdemo.s")
        return 1
    params = StdioServerParameters(command=str(SERVER), env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            box = Box(session)
            try:
                await run(box)
            except Exception as e:
                check("demo ran to completion", False, repr(e))
                try:
                    await box("vm_stop", show=False)
                except Exception:
                    pass
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
