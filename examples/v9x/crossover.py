"""Drive one Windows 98 VM through two MCP servers at once: 86box-mcp (outside the machine) and
V9x Remote Agent (inside it), and record which combinations work.

    BOX86_ROMS=... python examples/v9x/crossover.py [--vm DIR] [--v9x-repo DIR]

Needs a VM with Windows 98 and the V9x agent installed (see NOTES.md), with the agent's port
forwarded to host port 9869. Everything is an MCP tool call on one of the two servers; the
86Box recording is captioned with the scenario in progress and rendered at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sys
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from PIL import Image, ImageChops

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
BOX_SERVER = REPO / "server" / ".venv" / "bin" / "box86mcp"
PYTHON = REPO / "server" / ".venv" / "bin" / "python"

results: list[tuple[str, str, str]] = []   # (scenario, verdict, detail)
T0 = 0.0


def record(scenario: str, ok: bool | None, detail: str = "") -> None:
    verdict = "works" if ok else ("by design" if ok is None else "FAILED")
    results.append((scenario, verdict, detail))
    print(f"  [{verdict:>9}] {scenario}" + (f": {detail}" if detail else ""), flush=True)


class Both:
    """The two sessions, plus the caption log for the 86Box recording."""

    def __init__(self, box: ClientSession, v9x: ClientSession):
        self.box, self.v9x = box, v9x
        self.calls_log = OUT / "calls.jsonl"
        self.shots = 0

    async def b(self, tool: str, **args):
        res = await self.box.call_tool(tool, args)
        text = "".join(c.text for c in res.content if c.type == "text")
        if res.is_error:
            raise RuntimeError(f"{tool}: {text}")
        for c in res.content:
            if c.type == "image":
                return base64.b64decode(c.data)
        return text

    async def v(self, tool: str, **args):
        for attempt in range(2):
            res = await self.v9x.call_tool(tool, args)
            text = "".join(c.text for c in res.content if c.type == "text")
            if not res.is_error:
                return text
            # The agent serves one client at a time; a connection right behind the previous one
            # is sometimes dropped. One retry after a pause covers it.
            if "peer closed" in text and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            raise RuntimeError(f"{tool}: {text}")

    async def ms(self) -> int:
        try:
            return json.loads(await self.b("vm_status"))["ms"]
        except RuntimeError:
            return 0  # no VM yet

    async def caption(self, text: str, span_ms: int = 4000) -> None:
        ms = await self.ms()
        with open(self.calls_log, "a") as f:
            f.write(json.dumps({"start_ms": ms, "end_ms": ms + span_ms, "tool": "act", "caption": text}) + "\n")
        print(f"\n== {text}", flush=True)

    async def await_desktop(self, seconds: int) -> str:
        """v9x_wait_desktop, retried: until the agent is up, connecting fails outright."""
        deadline = time.time() + seconds
        last = ""
        while time.time() < deadline:
            try:
                return await self.v("v9x_wait_desktop", wait_seconds=30)
            except RuntimeError as e:
                last = str(e)
                await asyncio.sleep(5)
        raise RuntimeError(f"desktop not ready after {seconds} s: {last}")

    async def box_shot(self, name: str) -> Image.Image:
        png = await self.b("screenshot", max_width=640)
        p = OUT / f"{name}.png"
        p.write_bytes(png)
        return Image.open(p).convert("RGB")


def changed_px(a: Image.Image, b: Image.Image) -> int:
    return sum(ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0).histogram()[255:])


async def scenarios(d: Both, vm_dir: Path) -> None:
    global T0
    T0 = time.time()
    # Boot ------------------------------------------------------------------------------
    await d.caption("Boot Windows 98 held, start recording, then release so the agent's TCP works")
    print(await d.b("vm_start", vm_dir=str(vm_dir), held=True))
    print(await d.b("record_start", directory=str(OUT / "frames"), interval_ms=100))
    await d.b("release")
    print(await d.await_desktop(300))
    ping = json.loads(await d.v("v9x_ping"))
    boot0 = ping.get("boot_counter", 0)   # persists across the agent's runs, so compare relatively
    record("boot to desktop, then v9x_wait_desktop and v9x_ping", "uptime_ms" in ping, str(ping))

    # Time -------------------------------------------------------------------------------
    await d.caption("Held: the guest's TCP stack is frozen, so V9x calls time out")
    await d.b("hold")
    t = time.time()
    try:
        await d.v("v9x_ping")
        record("v9x_ping while held", False, "answered, but the machine was held")
    except RuntimeError as e:
        record("v9x_ping while held", None, f"times out after {time.time() - t:.0f} s: V9x needs emulated time to run")

    await d.caption("Held, but a step of 4 s lets a V9x request complete inside the step")
    t = time.time()
    step, ping = await asyncio.gather(d.b("step", ms=4000), d.v("v9x_ping"))
    record("v9x_ping during step(4000) while held", "uptime_ms" in ping, f"ping answered in {time.time() - t:.1f} s wall")
    status = json.loads(await d.b("vm_status"))
    record("machine still held after the step", status["held"] is True)

    await d.caption("Released: V9x works normally, and 86box-mcp still reads the machine")
    await d.b("release")
    info = json.loads(await d.b("vm_info"))
    inv = await d.v("v9x_inventory")
    guest_mb = None
    for line in inv.splitlines():
        if "total_phys" in line:
            digits = "".join(ch for ch in line.split("total_phys")[1] if ch.isdigit())
            guest_mb = int(digits[:12]) // (1024 * 1024) if digits else None
            break
    record("RAM agrees: vm_info mem_kb vs v9x_inventory total_phys",
           guest_mb is not None and abs(info["mem_kb"] // 1024 - guest_mb) <= 2,
           f"86Box says {info['mem_kb'] // 1024} MB, the guest sees {guest_mb} MB")

    # Screens ----------------------------------------------------------------------------
    await d.caption("Two screenshots of the same desktop: V9x's GDI capture and 86Box's frame")
    await d.v("v9x_screenshot", save_path=str(OUT / "v9x_desktop.png"))
    box_img = await d.box_shot("box_desktop")
    v9x_img = Image.open(OUT / "v9x_desktop.png").convert("RGB")
    px = changed_px(box_img, v9x_img) if box_img.size == v9x_img.size else -1
    record("v9x_screenshot matches 86box screenshot", 0 <= px < 2000, f"{v9x_img.size} vs {box_img.size}, {px} px differ")
    try:
        await d.b("screen_text")
        record("screen_text on the Windows desktop", False, "returned text in a graphics mode")
    except RuntimeError as e:
        record("screen_text on the Windows desktop", None, "not a text mode: use v9x for anything textual")

    # Files ------------------------------------------------------------------------------
    await d.caption("Files: V9x transfers live; 86box-mcp's disk tools need the VM stopped")
    marker = f"XOVER-{int(time.time()) % 100000}-crossover-marker"
    local = OUT / "HELLO.TXT"
    local.write_text(f"Hello from the host.\r\n{marker}\r\n")
    await d.v("v9x_mkdir", guest_path="C:\\XOVER")
    await d.v("v9x_put_file", local_path=str(local), guest_path="C:\\XOVER\\HELLO.TXT")
    out = await d.v("v9x_shell", command="TYPE C:\\XOVER\\HELLO.TXT")
    record("v9x_put_file then v9x_shell TYPE", marker in out)
    try:
        await d.b("dos_install_files", paths=[str(local)], dest_dir="\\XOVER")
        record("dos_install_files while Windows runs", False, "it wrote to a disk 86Box has open")
    except RuntimeError as e:
        record("dos_install_files while Windows runs", None, "refused: the VM must be stopped; use v9x_put_file instead")

    # Memory -----------------------------------------------------------------------------
    await d.caption("V9x opens the file in Notepad; 86box-mcp finds its text in physical RAM")
    await d.v("v9x_exec", application="C:\\WINDOWS\\NOTEPAD.EXE", arguments="C:\\XOVER\\HELLO.TXT", detach=True)
    await asyncio.sleep(3)
    await d.b("hold")
    t = time.time()
    found = await d.b("find_memory", text=marker)
    record("find_memory finds Notepad's text (V9x launched it)", not found.startswith("0 found"),
           f"{found[:60]}... in {time.time() - t:.0f} s")
    await d.caption("Held: step 20 ms at a time while Notepad is open, frame by frame")
    frames0 = json.loads(await d.b("vm_status"))["frames"]
    for _ in range(5):
        await d.b("step", ms=20)
    frames1 = json.loads(await d.b("vm_status"))["frames"]
    record("exact stepping with a GUI app up", frames1 > frames0, f"{frames1 - frames0} frames in 100 ms")
    await d.b("release")

    # Input ------------------------------------------------------------------------------
    await d.caption("86box-mcp types through the keyboard controller; V9x saves and fetches the file")
    typed = "typed by 86box-mcp through the emulated keyboard"
    await d.b("press_keys", keys=["ctrl+end"])
    await d.b("type_text", text=typed)
    await asyncio.sleep(2)
    await d.v("v9x_key", keys="ALT+F4")       # Notepad asks whether to save the changes
    await asyncio.sleep(2)
    await d.v("v9x_key", keys="ENTER")        # Yes (the default)
    await asyncio.sleep(3)
    got = OUT / "HELLO_back.txt"
    await d.v("v9x_get_file", guest_path="C:\\XOVER\\HELLO.TXT", local_path=str(got))
    back = got.read_text(errors="replace") if got.exists() else ""
    record("keys from 86box-mcp land in Notepad, saved and fetched by V9x", typed in back,
           "" if typed in back else f"file holds: {back[:80]!r}")

    await d.caption("Mouse: V9x places the pointer absolutely; 86box-mcp moves it relatively")
    await d.v("v9x_move", x=100, y=100)
    await asyncio.sleep(0.5)
    await d.b("mouse", dx=40, dy=0)
    await asyncio.sleep(0.5)
    pos = json.loads(await d.v("v9x_input", actions=[{"op": "delay", "ms": 1}]))
    cx, cy = pos.get("cursor_x"), pos.get("cursor_y")
    record("v9x_move then 86box mouse(dx=40): V9x reports the pointer", cx is not None and cx > 100,
           f"pointer at ({cx}, {cy}); 40 counts moved it {cx - 100 if cx else '?'} px (Windows acceleration applies)")

    # Media ------------------------------------------------------------------------------
    await d.caption("86box-mcp inserts a CD built from host files; V9x lists D:")
    cd_dir = OUT / "cd"
    cd_dir.mkdir(exist_ok=True)
    (cd_dir / "FROMHOST.TXT").write_text("built into an ISO by cdrom_insert\r\n")
    print(await d.b("cdrom_insert", files=[str(cd_dir / "FROMHOST.TXT")]))
    await asyncio.sleep(4)
    listing = await d.v("v9x_list_dir", guest_path="D:\\")
    record("cdrom_insert(files) then v9x_list_dir D:", "FROMHOST" in listing.upper(), listing.strip()[:80])
    await d.b("cdrom_eject")

    # Reset ------------------------------------------------------------------------------
    await d.caption("86box-mcp hard-resets the machine; V9x waits for the desktop and proves the reboot")
    await d.b("reset", kind="hard")
    await asyncio.sleep(2)
    print(await d.await_desktop(360))
    ping = json.loads(await d.v("v9x_ping"))
    record("reset(hard) then v9x_wait_desktop; boot counter went up", ping.get("boot_counter") == boot0 + 1,
           f"{boot0} -> {ping.get('boot_counter')}")
    info = json.loads(await d.b("vm_info"))
    record("vm_info counts the reset too", info.get("hard_resets", 0) >= 1, f"hard_resets={info.get('hard_resets')}")

    await d.caption("Done")
    await d.box_shot("final")
    ms = await d.ms()
    record("emulated time vs wall time for the whole run", True,
           f"{ms / 1000:.0f} s emulated in {time.time() - T0:.0f} s wall")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm", default=str(REPO / ".cache" / "v9x" / "vm"))
    ap.add_argument("--v9x-repo", default=str(REPO / ".cache" / "v9x" / "repo"))
    a = ap.parse_args()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    env = dict(os.environ)
    box = StdioServerParameters(command=str(BOX_SERVER), env=env)
    v9x = StdioServerParameters(command=str(PYTHON), env=env,
                                args=[str(Path(a.v9x_repo) / "mcp" / "v9x_mcp.py"), "--host", "127.0.0.1",
                                      "--port", "9869", "--timeout", "8"])
    async with stdio_client(box) as (br, bw), stdio_client(v9x) as (vr, vw):
        async with ClientSession(br, bw) as bs, ClientSession(vr, vw) as vs:
            await bs.initialize()
            await vs.initialize()
            d = Both(bs, vs)
            try:
                await scenarios(d, Path(a.vm).expanduser())
            finally:
                try:
                    print(await d.b("record_stop", video=str(OUT / "crossover.mp4"), calls_log=str(d.calls_log)))
                except Exception as e:
                    print("render failed:", e)
                print(await d.b("vm_stop"))
    (OUT / "results.md").write_text(
        "| Scenario | Verdict | Detail |\n|---|---|---|\n"
        + "".join(f"| {s} | {v} | {x} |\n" for s, v, x in results))
    failed = [r for r in results if r[1] == "FAILED"]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios as expected; results in {OUT / 'results.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
