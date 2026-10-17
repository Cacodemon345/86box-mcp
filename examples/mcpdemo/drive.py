"""Drive MCPDEMO through the 86box MCP server, exercising every tool.

Talks to the server over MCP stdio exactly as Claude would, checks each
result against the game's published state block, and saves screenshots to
out/. Run from anywhere:

    server/.venv/bin/python examples/mcpdemo/drive.py

Exit status is 0 only if every check passed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import struct
import sys
import time
from pathlib import Path

from box86mcp.ctl import READ_ONLY, render_timeline
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
VM_DIR = OUT / "vm"
SERVER = REPO / "server" / ".venv" / "bin" / "box86mcp"

# State block layout (see mcpdemo.c).
FIELDS = ["ticks", "phase", "player_x", "player_y", "coin_x", "coin_y", "enemy_x", "enemy_y", "mouse_x",
          "mouse_y", "mouse_btn", "score", "lives", "clicks", "keys", "hits", "has_mouse"]
FMT = "<I H 8h 7H"
OFF_CMD, OFF_ARG1 = 0x3C, 0x3E
CMD_MOVE_COIN, CMD_SET_LIVES, CMD_FREEZE_ENEMY = 1, 2, 3
BUTTON_CENTER = (256 + 28, 16 + 8)

checks: list[tuple[str, bool, str]] = []
used: set[str] = set()


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def parse_hexdump(text: str) -> bytes:
    return bytes.fromhex("".join(line[10:57] for line in text.splitlines()))


class Driver:
    def __init__(self, session: ClientSession):
        self.s = session
        self.state_addr = 0
        self.shot_no = 0
        self.calls_log = OUT / "calls.jsonl"

    async def emulated_ms(self) -> int | None:
        res = await self.s.call_tool("vm_status", {})
        return None if res.is_error else json.loads(res.content[0].text)["ms"]

    def log_call(self, start_ms, end_ms, tool: str, caption: str) -> None:
        """Same format mcpctl writes, so render_timeline can caption the 86Box recording."""
        with open(self.calls_log, "a") as f:
            f.write(json.dumps({"start_ms": start_ms, "end_ms": end_ms, "tool": tool, "caption": caption}) + "\n")

    async def section(self, title: str) -> None:
        print(f"\n== {title} ==")
        ms = await self.emulated_ms()
        if ms is not None:
            self.log_call(ms, ms, "note", "NOTE: " + title)

    async def call(self, tool: str, quiet: bool = False, **args) -> tuple[str, list[bytes]]:
        used.add(tool)
        logged = tool not in READ_ONLY | {"screenshot"}
        start_ms = await self.emulated_ms() if logged else None
        res = await self.s.call_tool(tool, args)
        if logged:
            self.log_call(start_ms, await self.emulated_ms(), tool,
                          f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
        texts = [c.text for c in res.content if c.type == "text"]
        images = [base64.b64decode(c.data) for c in res.content if c.type == "image"]
        text = "\n".join(texts)
        if res.is_error:
            raise RuntimeError(f"{tool} failed: {text}")
        if not quiet:
            shown = {k: v for k, v in args.items()}
            first = text.splitlines()[0] if text else ""
            print(f"> {tool}({', '.join(f'{k}={v!r}' for k, v in shown.items())}) -> {first[:100]}")
        return text, images

    async def shot(self, label: str, images: list[bytes] | None = None, **args) -> Path:
        if images is None:
            _, images = await self.call("screenshot", **args)
        self.shot_no += 1
        path = OUT / f"{self.shot_no:02d}-{label}.png"
        path.write_bytes(images[0])
        print(f"  saved {path.relative_to(REPO)}")
        return path

    async def state(self) -> dict:
        text, _ = await self.call("read_memory", quiet=True, addr=self.state_addr, length=0x44)
        raw = parse_hexdump(text)
        assert raw[:8] == b"MCPDEMO1", raw[:8]
        values = dict(zip(FIELDS, struct.unpack_from(FMT, raw, 8)))
        values["cmd"], values["cmds_done"] = struct.unpack_from("<H", raw, OFF_CMD)[0], raw[0x42] | raw[0x43] << 8
        return values

    async def command(self, cmd: int, arg1: int = 0, arg2: int = 0) -> None:
        """Post a command to the game's mailbox and wait for it to be acknowledged."""
        await self.call("write_memory", addr=self.state_addr + OFF_ARG1,
                        hex_bytes=struct.pack("<hh", arg1, arg2).hex())
        await self.call("write_memory", addr=self.state_addr + OFF_CMD, hex_bytes=struct.pack("<H", cmd).hex())
        text, _ = await self.call("wait_for_memory", addr=self.state_addr + OFF_CMD, hex_bytes="0000",
                                  timeout_ms=1000)
        check(f"game acknowledged command {cmd}", text.startswith("matched"))


async def run(d: Driver) -> None:
    await d.section("Set up a VM with a 512 MB CF-style C: drive and install the demo")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    await d.call("dos_create_vm", vm_dir=str(VM_DIR))
    files = [str(HERE / "dist" / n) for n in ("MCPDEMO.EXE", "CWSDPMI.EXE", "CTMOUSE.EXE")]
    text, _ = await d.call("dos_install_files", paths=files, dest_dir="\\MCPDEMO", vm_dir=str(VM_DIR))
    check("installed to C:\\MCPDEMO while stopped", "MCPDEMO.EXE" in text)

    await d.section("Boot DOS (held: emulated time only advances inside tool calls)")
    await d.call("vm_start", vm_dir=str(VM_DIR), held=True, com1_log=True)
    text, _ = await d.call("vm_status")
    check("vm_status reports held", json.loads(text)["held"] is True)
    text, _ = await d.call("wait_for_text", text="C:\\>", timeout_ms=60000)
    check("DOS reached the prompt", text.startswith("found"), text)
    info = json.loads((await d.call("vm_info"))[0])
    check("vm_info reports the machine and the CF-geometry disk",
          info["machine"] == "486sp3c" and info["hdd"][0]["cylinders"] == 993,
          f"{info['cpu_family']} {info['cpu_hz'] // 1000000} MHz, {info['mem_kb'] // 1024} MB, {info['video']}")
    text, _ = await d.call("registers")
    regs = json.loads(text)
    check("real mode at the DOS prompt (CR0.PE=0)", regs["cr0"] & 1 == 0, f"CS:IP {regs['cs']:04x}:{regs['eip']:04x}")

    await d.section("DOS commands")
    await d.call("dos_run", command="CD \\MCPDEMO")
    text, _ = await d.call("dos_run", command="CTMOUSE")
    check("CTMOUSE loaded", "installed" in text.lower() or "driver" in text.lower())
    text, _ = await d.call("dos_run", command="DIR")
    check("DIR shows MCPDEMO.EXE on C:", "MCPDEMO  EXE" in text)

    await d.section("Text mode: the name prompt")
    await d.call("type_text", text="MCPDEMO\n")
    text, _ = await d.call("wait_for_text", text="Your name?", timeout_ms=10000)
    check("game asked for a name", text.startswith("found"))
    text, _ = await d.call("screen_text")
    print("  " + "\n  ".join(text.splitlines()[-6:]))

    raw = parse_hexdump((await d.call("read_memory", addr=0x4F0, length=8))[0])
    check("pointer at 0000:04F0 has the MCPD signature", raw[:4] == b"MCPD")
    d.state_addr = struct.unpack_from("<H", raw, 4)[0] * 16
    print(f"  state block at physical {d.state_addr:#x}")

    await d.call("type_text", text="Claude\n")
    await d.call("wait_for_text", text="Press any key", timeout_ms=5000)
    await d.call("press_keys", keys=["space"])
    text, _ = await d.call("wait_for_memory", addr=d.state_addr + 0x0C, hex_bytes="0100", timeout_ms=5000)
    check("game entered the arena (phase=1)", text.startswith("matched"))
    frames_dir = OUT / "frames"
    await d.call("record_start", directory=str(frames_dir), interval_ms=50)
    await d.call("step", ms=200)
    await d.shot("arena")
    text, _ = await d.call("registers")
    regs = json.loads(text)
    check("DJGPP runs in protected mode (CR0.PE=1)", regs["cr0"] & 1 == 1,
          f"CS selector {regs['cs']:#x}, EIP {regs['eip']:#x}")

    await d.section("Keyboard: exact movement in held mode")
    await d.command(CMD_FREEZE_ENEMY, 1)
    s0 = await d.state()
    _, img = await d.call("act", hold_keys=["right"], ms=1000)
    s1 = await d.state()
    moved = s1["player_x"] - s0["player_x"]
    check("1 s of Right moved ~70 px (2 px/tick at 35 Hz)", 64 <= moved <= 72, f"moved {moved}")
    _, img = await d.call("act", hold_keys=["shift", "left"], ms=500)
    s2 = await d.state()
    moved = s1["player_x"] - s2["player_x"]
    check("0.5 s of Shift+Left moved ~70 px back (4 px/tick)", 64 <= moved <= 76, f"moved {moved}")
    await d.shot("after-keys", img)

    await d.section("Mouse: absolute positioning with mouse_to")
    st = await d.state()
    check("game sees a mouse driver", st["has_mouse"] == 1)
    # Exact: the game publishes its pointer position (x, y int16) at offset 0x1A.
    text, _ = await d.call("mouse_to", x=BUTTON_CENTER[0], y=BUTTON_CENTER[1], pointer_addr=d.state_addr + 0x1A)
    st = await d.state()
    check("mouse_to via memory reached the button", text.startswith("at target"),
          f"({st['mouse_x']},{st['mouse_y']})")
    await d.call("mouse", left=True)
    st = await d.state()
    check("click registered", st["clicks"] == 1, f"clicks={st['clicks']}")
    text, _ = await d.call("wait_for_serial", text="CLICK", timeout_ms=1000)
    check("game logged the click on COM1", "CLICK clicks=1" in text)
    await d.shot("clicked")

    # Generic: find the pointer on screen. Mode 13h frames are 640x400, so game pixels double;
    # the demo draws a crosshair, so aim with its center.
    target = (100, 150)
    text, _ = await d.call("mouse_to", x=target[0] * 2, y=target[1] * 2, hotspot="center", tolerance=2)
    st = await d.state()
    err = max(abs(st["mouse_x"] - target[0]), abs(st["mouse_y"] - target[1]))
    check("mouse_to via the screen landed within 2 game px", err <= 2,
          f"game says ({st['mouse_x']},{st['mouse_y']}), wanted {target}; tool: {text}")

    await d.section("write_memory: drop a coin in front of the player")
    st = await d.state()
    await d.command(CMD_MOVE_COIN, st["player_x"] + 30, st["player_y"] + 1)
    await d.call("act", hold_keys=["right"], ms=600)
    st2 = await d.state()
    check("walking right collected the planted coin", st2["score"] == st["score"] + 1, f"score={st2['score']}")

    await d.section("Turn-based play: a greedy bot, 150 ms per turn")
    await d.command(CMD_SET_LIVES, 9)
    await d.command(CMD_FREEZE_ENEMY, 0)
    start = await d.state()
    t0 = time.monotonic()
    turns = 60
    frames = []
    for turn in range(turns):
        st = await d.state()
        keys = bot_keys(st)
        _, img = await d.call("act", quiet=True, hold_keys=keys, ms=150, max_width=320)
        frames.append(img[0])
        if turn % 20 == 19:
            await d.shot(f"play-turn{turn + 1}", img)
    save_gif(frames, OUT / "play.gif")
    wall = time.monotonic() - t0
    end = await d.state()
    print(f"  {turns} turns in {wall:.1f} s wall ({1000 * wall / turns:.0f} ms/turn incl. screenshot), "
          f"{(end['ticks'] - start['ticks']) / 35:.1f} s of game time")
    check("bot collected 3+ coins playing turn by turn", end["score"] - start["score"] >= 3,
          f"coins {end['score'] - start['score']}, caught {end['hits'] - start['hits']} times")

    await d.section("Real time vs held")
    st = await d.state()
    await d.call("release")
    time.sleep(2)
    await d.call("hold")
    st2 = await d.state()
    ticks = st2["ticks"] - st["ticks"]
    check("released for 2 s wall: game ran in real time", 40 <= ticks <= 100, f"{ticks} ticks ~ {ticks / 35:.1f} s")
    time.sleep(1)
    st3 = await d.state()
    check("held: nothing moves while we wait", st3["ticks"] == st2["ticks"])
    await d.call("step", ms=1000)
    st4 = await d.state()
    check("step 1000 ms = 35 ticks", 34 <= st4["ticks"] - st3["ticks"] <= 36, f"{st4['ticks'] - st3['ticks']} ticks")
    text, _ = await d.call("record_stop")
    recorded = len(list(frames_dir.glob("*.png")))
    check("86Box recorded the arena session frame by frame", recorded > 100, text)

    await d.section("Quit back to DOS")
    await d.call("press_keys", keys=["esc"])
    text, _ = await d.call("wait_for_text", text="Thanks for playing", timeout_ms=5000)
    check("game exited to text mode", text.startswith("found"))
    text, _ = await d.call("screen_text")
    print("  " + "\n  ".join(text.splitlines()[-5:]))
    text, _ = await d.call("serial_read", from_start=True)
    print("  COM1 log:\n    " + "\n    ".join(text.splitlines()))
    check("COM1 log has the session from start to exit", "MCPDEMO start name=Claude" in text and "EXIT" in text)

    await d.section("Hot media: floppy while running, copied to C:")
    notes = OUT / "NOTES.TXT"
    notes.write_text("Delivered by dos_put_floppy while the VM was running.\r\n")
    await d.call("dos_put_floppy", paths=[str(notes)])
    await d.call("dos_run", command="COPY A:\\NOTES.TXT C:\\MCPDEMO")
    text, _ = await d.call("dos_run", command="TYPE NOTES.TXT")
    check("file arrived via floppy and was copied to C:", "Delivered by dos_put_floppy" in text)
    await d.call("floppy_eject")

    await d.section("CD-ROM: build an ISO from host files, insert, eject")
    text, _ = await d.call("cdrom_insert", files=[str(notes), str(HERE / "mcpdemo.c")])
    info = json.loads((await d.call("vm_info"))[0])
    check("CD image mounted in drive 0", len(info["cdrom"]) == 1, text)
    await d.call("cdrom_eject")
    info = json.loads((await d.call("vm_info"))[0])
    check("CD ejected", info["cdrom"] == [])

    await d.section("Reset")
    await d.call("reset", kind="hard")
    text, _ = await d.call("wait_for_text", text="C:\\>", timeout_ms=60000)
    check("machine rebooted to DOS after a hard reset", text.startswith("found"))
    await d.shot("after-reset")
    await d.call("vm_stop")


def save_gif(pngs: list[bytes], path: Path) -> None:
    """One frame per turn; 150 ms per frame replays the game at real speed."""
    import io
    from PIL import Image
    rgb = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    first = rgb[0].quantize(colors=32, method=Image.Quantize.MEDIANCUT)
    images = [first] + [im.quantize(palette=first, dither=Image.Dither.NONE) for im in rgb[1:]]
    images[0].save(path, save_all=True, append_images=images[1:], duration=150, loop=0, optimize=True)
    print(f"  saved {path.relative_to(REPO)} ({len(images)} frames)")


def bot_keys(st: dict) -> list[str]:
    """Head for the coin; if the chaser gets close, sidestep across its line of approach."""
    px, py = st["player_x"] + 4, st["player_y"] + 4
    ex, ey = st["enemy_x"] + 4, st["enemy_y"] + 4
    tx, ty = st["coin_x"] + 3, st["coin_y"] + 3
    if abs(px - ex) + abs(py - ey) < 32:
        # Perpendicular to the chaser's approach, toward the roomier side of the arena.
        dx, dy = px - ex, py - ey
        side = 1 if (px < 160) == (dy >= 0) else -1
        tx, ty = px + dx * 2 - dy * side * 2, py + dy * 2 + dx * side * 2
        tx, ty = min(max(tx, 20), 300), min(max(ty, 32), 180)
    keys = []
    if tx > px + 2:
        keys.append("right")
    elif tx < px - 2:
        keys.append("left")
    if ty > py + 2:
        keys.append("down")
    elif ty < py - 2:
        keys.append("up")
    return keys


async def main() -> int:
    if not (HERE / "dist" / "MCPDEMO.EXE").exists():
        print("build first: examples/mcpdemo/build.sh")
        return 1
    # Pass our environment on, so BOX86_* settings reach the server.
    params = StdioServerParameters(command=str(SERVER), env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            d = Driver(session)
            try:
                await run(d)
            except Exception as e:  # report what we have so far
                check("demo ran to completion", False, repr(e))
                try:
                    await d.call("vm_stop")
                except Exception:
                    pass
    if (OUT / "frames").is_dir():
        render_timeline(OUT / "frames", OUT / "demo.mp4", OUT / "calls.jsonl", fast_factor=4)
        print(f"  rendered {(OUT / 'demo.mp4').relative_to(REPO)}")
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed; "
          f"{len(used & tools)}/{len(tools)} tools used" + (f"; unused: {sorted(tools - used)}" if tools - used else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
