"""MCP server exposing a headless 86Box machine as tools."""

from __future__ import annotations

import io
import os
import re
import shutil
import time
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage

from . import disk
from . import keys as K
from .gdbrsp import BREAK_KINDS, Gdb, describe_stop
from .protocol import AutomationError
from .vm import VM, machine_of, prime_nvr, write_config

mcp = MCPServer(
    "86box",
    instructions=(
        "Drives a headless 86Box PC emulator. Start with vm_start. The machine starts HELD: emulated time "
        "only advances inside tool calls (step, act, press_keys, type_text, wait_for_text, dos_run), so the "
        "game or program waits while you think. Use release to let it run in real time. Prefer screen_text "
        "over screenshot in text modes; it is cheaper and exact."
    ),
)

_vm: VM | None = None
_held = True


def vm() -> VM:
    if _vm is None or not _vm.running():
        raise AutomationError("no VM running; call vm_start first")
    return _vm


def _advance(ms: int) -> None:
    """Let ms of emulated time pass: exactly, if held; approximately, if free-running."""
    if ms <= 0:
        return
    if _held:
        vm().auto.step(ms)
    else:
        time.sleep(ms / 1000)


def _snapshot(max_width: int) -> tuple[bytes, dict]:
    v = vm()
    info = v.auto.screenshot()
    img = PILImage.open(io.BytesIO(info.pop("png")))
    img.load()
    if max_width and img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), PILImage.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), info


def _time_note(info: dict) -> str:
    note = f"emulated t={info.get('ms')}ms frame={info.get('frames')}"
    if info.get("stopped") == "debugger":
        note += "; STOPPED: 86Box's GDB stub has the CPU stopped"
    return note


# Lifecycle -----------------------------------------------------------------




@mcp.tool()
def vm_create(vm_dir: str, cylinders: int = 993, heads: int = 16, sectors: int = 63,
              config: str | None = None, nvr: str | None = None) -> str:
    """Create a VM in vm_dir with a blank hard disk, ready for installing an OS from floppy or CD.

    The machine is a 486DX2-66 with 16 MB, unless config names an 86box.cfg to copy: then that
    machine, with its first hard disk replaced by the new blank one. nvr is a saved CMOS (.nvr file,
    or a folder of them) so the BIOS boots without stopping at setup; by default the bundled one is
    used when the machine is the default 486DX2-66. The default geometry, 993/16/63, matches a 512 MB
    CompactFlash card. Boot media: floppy_insert or cdrom_insert, then vm_start.
    """
    d = Path(vm_dir).expanduser()
    if (d / "86box.cfg").exists():
        raise FileExistsError(f"{d} already has an 86box.cfg")
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "hdd.img", "wb") as f:
        f.truncate(cylinders * heads * sectors * 512)
    write_config(d, cylinders, heads, sectors, template=config)
    cmos = prime_nvr(d, nvr)
    mb = cylinders * heads * sectors * 512 // (1024 * 1024)
    return f"created {d} ({machine_of(d)}) with a blank {mb} MB disk ({cylinders}/{heads}/{sectors}); {cmos}"


@mcp.tool()
def dos_create_vm(vm_dir: str, base_image: str | None = None, cylinders: int = 993,
                  heads: int = 16, sectors: int = 63, autoexec: str = "@ECHO OFF\nPROMPT $P$G\n",
                  config: str | None = None, nvr: str | None = None) -> str:
    """Create a new VM in vm_dir with a fresh bootable DOS hard disk.

    The machine is a 486DX2-66 with 16 MB, unless config names an 86box.cfg to copy (its first hard
    disk becomes the new DOS disk). nvr: a saved CMOS file or folder, as for vm_create.

    The default geometry, 993/16/63, matches a 512 MB CompactFlash card. The disk uses 86Box's RAM-disk
    speed (no seek or rotation delays). DOS system files are taken from base_image, a bootable DOS hard
    disk image (default: $BOX86_DOS_BASE_IMG).
    """
    base_image = base_image or os.environ.get("BOX86_DOS_BASE_IMG")
    if not base_image:
        raise ValueError("pass base_image (a bootable DOS hard disk image) or set BOX86_DOS_BASE_IMG")
    d = Path(vm_dir).expanduser()
    if (d / "86box.cfg").exists():
        raise FileExistsError(f"{d} already has an 86box.cfg")
    d.mkdir(parents=True, exist_ok=True)
    img = d / "hdd.img"
    disk.make_dos_disk(img, Path(base_image).expanduser(), (cylinders, heads, sectors))
    disk.write_text(img, "AUTOEXEC.BAT", autoexec)
    write_config(d, cylinders, heads, sectors, template=config)
    cmos = prime_nvr(d, nvr)
    mb = cylinders * heads * sectors * 512 // (1024 * 1024)
    return f"created {d} ({machine_of(d)}) with a {mb} MB DOS disk ({cylinders}/{heads}/{sectors}); {cmos}"


@mcp.tool()
def dos_install_files(paths: list[str], dest_dir: str = "\\", vm_dir: str | None = None) -> str:
    """Copy host files/directories onto the VM's C: drive, e.g. dest_dir="\\GAME".

    The VM must be stopped (vm_stop): 86Box caches the disk, so writing to it while running corrupts it.
    No size limit, unlike dos_put_floppy. vm_dir defaults to the last VM started.
    """
    if _vm is not None and _vm.running():
        raise AutomationError("stop the VM first (vm_stop); then dos_install_files, then vm_start")
    target = VM(vm_dir) if vm_dir else _vm
    if target is None:
        raise ValueError("no VM known yet; pass vm_dir")
    copied = disk.put_files(target.hdd_image(), paths, dest_dir)
    return f"copied {', '.join(copied)} to C:{dest_dir}"


@mcp.tool()
def vm_start(vm_dir: str | None = None, held: bool = True, window: bool = False, com1_log: bool = False,
             debugger: bool = False) -> str:
    """Boot the 86Box machine whose 86box.cfg lives in vm_dir. vm_dir defaults to the last VM.

    held=True (default) freezes emulated time between tool calls, for deterministic, turn-based control.
    window=True shows the 86Box window on the host's display so a person can watch (and also type);
    the default runs without a window. com1_log=True captures COM1 output for serial_read and
    wait_for_serial; it's off by default because with it, runs aren't always exactly reproducible.
    debugger=True attaches to 86Box's GDB stub for breakpoint_set, run_until_break,
    step_instructions, debug_continue, io_read/io_write and the PCI tools; it needs 86Box built with
    -D GDBSTUB=ON.
    """
    global _vm, _held
    if _vm is not None and _vm.running():
        _vm.stop()
    if vm_dir is None:
        if _vm is None:
            raise ValueError("pass vm_dir")
        vm_dir = str(_vm.vm_dir)
    _vm = VM(vm_dir)
    status = _vm.start(held=held, window=window, com1_log=com1_log, debugger=debugger)
    _held = held
    extras = (", windowed" if window else "") + (f", debugger on port {_vm.gdb_port}" if debugger else "")
    return f"86Box {status.get('version')} running from {vm_dir} ({'held' if held else 'real-time'}{extras}). Log: {_vm.log_path}"


@mcp.tool()
def vm_attach(endpoint: str, token: str | None = None) -> str:
    """Control an 86Box that is already running, possibly on another machine, instead of starting one.

    endpoint: "tcp:<host>:<port>" or "unix:<path>". token: the contents of the token file that 86Box
    wrote at startup (required for TCP). Paths in later tool calls (media images, record_start
    directories) are paths on that machine; screenshots come back over the connection.
    """
    global _vm, _held
    if _vm is not None and _vm.running():
        _vm.stop()
    from .vm import AttachedVM

    _vm = AttachedVM(endpoint, token)
    status = _vm.auto.status()
    _held = bool(status.get("held"))
    info = _vm.auto.info()
    return (f"attached to 86Box {status.get('version')} at {endpoint}: {info.get('machine_name')}, "
            f"{'held' if _held else 'running in real time'}, emulated t={status.get('ms')}ms")


@mcp.tool()
def vm_stop(power_off: bool = True) -> str:
    """Power off the emulated machine and exit 86Box.

    For a VM joined with vm_attach, power_off=False just disconnects and leaves it running.
    """
    from .vm import AttachedVM

    if _vm is None or not _vm.running():
        return "no VM running"
    if isinstance(_vm, AttachedVM):
        if not power_off:
            _vm.stop()
            return "disconnected; the VM is still running"
        _vm.power_off()
        return "powered off the attached VM (86Box exited)"
    _vm.stop()
    return "stopped"


@mcp.tool()
def vm_status() -> dict:
    """Emulated time, frame count, held state, video mode, and the tail of the 86Box log."""
    v = vm()
    s = v.auto.status()
    s.pop("ok", None)
    s["log_tail"] = v.log_tail(10)
    return s


@mcp.tool()
def vm_info() -> dict:
    """What the running emulator actually is: 86Box version, machine, CPU and clock, RAM, video and
    sound cards, mounted disk/floppy/CD images, emulation speed, and resets so far.

    hard_resets/soft_resets count every CPU reset, including ones the guest causes itself (a triple
    fault, or a reboot through the keyboard controller). A soft_resets count that goes up unexpectedly
    usually means a crash. speed_percent is measured while running; 100 = real time.
    """
    return vm().auto.info()


@mcp.tool()
def hold() -> str:
    """Freeze emulated time between tool calls (turn-based mode)."""
    global _held
    _held = True
    return _time_note(vm().auto.hold())


@mcp.tool()
def release() -> str:
    """Let the machine run in real time between tool calls."""
    global _held
    _held = False
    v = vm()
    v.let_cpu_run()
    return _time_note(v.auto.release())


@mcp.tool()
def step(ms: int) -> str:
    """Run exactly ms milliseconds of emulated time (as fast as the host allows), then hold again."""
    return _time_note(vm().auto.step(ms))


@mcp.tool()
def reset(kind: str = "hard") -> str:
    """Reset the machine: kind='hard' (power cycle) or 'cad' (Ctrl+Alt+Del)."""
    if kind not in ("hard", "cad"):
        raise ValueError("kind must be 'hard' or 'cad'")
    return _time_note(vm().auto.call(f"reset {kind}"))


# Screen --------------------------------------------------------------------


@mcp.tool()
def screenshot(max_width: int = 640) -> list:
    """The current frame as an image, scaled down to max_width pixels wide (0 = native size)."""
    png, info = _snapshot(max_width)
    return [Image(data=png, format="png"), f"{info['w']}x{info['h']} native, {_time_note(info)}"]


@mcp.tool()
def screen_text() -> str:
    """The text-mode screen as plain text (CP437 decoded), with video mode and cursor position."""
    return vm().auto.text().render()


# Input ---------------------------------------------------------------------


@mcp.tool()
def press_keys(keys: list[str], hold_ms: int = 60, gap_ms: int = 60) -> str:
    """Press keys or combos in sequence, e.g. ["enter"], ["ctrl+c"], ["up","up","space"], ["alt+f","x"].

    Names: letters, digits, f1-f12, esc, enter, space, tab, backspace, up/down/left/right, home/end,
    pageup/pagedown, insert/delete, ctrl/alt/shift (and l/r variants), kp0-kp9, or hex scancodes like 0x1c.
    """
    v = vm()
    for combo in keys:
        codes = K.parse_combo(combo)
        v.auto.keys(True, codes)
        _advance(hold_ms)
        v.auto.keys(False, list(reversed(codes)))
        _advance(gap_ms)
    return f"pressed {len(keys)} key(s); {_time_note(v.auto.status())}"


def _type(text: str, ms_per_key: int) -> None:
    v = vm()
    shift = K.scancode("shift")
    for ch in text:
        code, shifted = K.char_keys(ch)
        if shifted:
            v.auto.keys(True, [shift])
        v.auto.keys(True, [code])
        _advance(ms_per_key // 2)
        v.auto.keys(False, [code])
        if shifted:
            v.auto.keys(False, [shift])
        _advance(ms_per_key - ms_per_key // 2)


@mcp.tool()
def type_text(text: str, ms_per_key: int = 50) -> str:
    """Type text on the emulated keyboard (US layout). Use \\n for Enter."""
    _type(text, ms_per_key)
    return f"typed {len(text)} chars; {_time_note(vm().auto.status())}"


@mcp.tool()
def mouse(dx: int = 0, dy: int = 0, left: bool = False, right: bool = False, middle: bool = False,
          hold_ms: int = 30) -> str:
    """Move the mouse by (dx, dy) and set buttons, run hold_ms, then release the buttons."""
    v = vm()
    buttons = (1 if left else 0) | (2 if right else 0) | (4 if middle else 0)
    v.auto.mouse(dx, dy, buttons)
    _advance(hold_ms)
    if buttons:
        v.auto.mouse(0, 0, 0)
        _advance(hold_ms)
    return _time_note(v.auto.status())


def _frame() -> PILImage.Image:
    img = PILImage.open(io.BytesIO(vm().auto.screenshot()["png"]))
    img.load()
    return img.convert("RGB")


def _changes(a: PILImage.Image, b: PILImage.Image) -> PILImage.Image:
    from PIL import ImageChops

    return ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0)


def _pointer_from_screen(frame: PILImage.Image, home: PILImage.Image, corner: int, hotspot: str,
                         noise: PILImage.Image | None = None):
    """Where the pointer is: the changed region versus the pointer-at-home frame, ignoring the home
    corner and anything that changes by itself (noise: e.g. a blinking caret)."""
    from PIL import ImageChops, ImageDraw

    if frame.size != home.size:
        return None
    diff = _changes(frame, home)
    if noise is not None and noise.size == diff.size:
        diff = ImageChops.subtract(diff, noise)
    ImageDraw.Draw(diff).rectangle([0, 0, corner, corner], fill=0)
    if not diff.getbbox():
        return None
    # Other things change too (a blinking caret, a clock): take the densest pointer-sized area,
    # not the bounding box of every change.
    cell = 16
    small = diff.resize((max(1, diff.width // cell), max(1, diff.height // cell)), PILImage.Resampling.BOX)
    w = small.width
    data = list(small.tobytes())
    best = max(range(len(data)), key=lambda i: sum(
        data[j] for j in (i - 1, i, i + 1, i - w, i + w) if 0 <= j < len(data) and abs(j % w - i % w) <= 1))
    cx, cy = (best % w) * cell, (best // w) * cell
    box = diff.crop((max(0, cx - 32), max(0, cy - 32), cx + cell + 32, cy + cell + 32)).getbbox()
    if not box:
        return None
    box = (box[0] + max(0, cx - 32), box[1] + max(0, cy - 32), box[2] + max(0, cx - 32), box[3] + max(0, cy - 32))
    if hotspot == "center":
        return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return box[0], box[1]


_mouse_state: dict = {}


# Slow moves: at most this many counts per ~10 ms (one PS/2 packet at the default 100 Hz). Guest
# drivers accelerate fast motion (Windows 3.x doubles it above 2 counts per report), which makes the
# pointer gain depend on speed; below the threshold the gain is constant and measurable.
SLOW_COUNTS = 2


def _move_counts(dx: int, dy: int, buttons: int, slow: bool = False) -> None:
    """Relative move, holding the given buttons: packet-sized chunks, or unaccelerated slow chunks."""
    v = vm()
    per, wait = (SLOW_COUNTS, 10) if slow else (250, 25)
    n = max(1, (max(abs(dx), abs(dy)) + per - 1) // per)
    sent_x = sent_y = 0
    for i in range(1, n + 1):
        tx, ty = round(dx * i / n), round(dy * i / n)
        v.auto.mouse(tx - sent_x, ty - sent_y, buttons)
        sent_x, sent_y = tx, ty
        _advance(wait)


@mcp.tool()
def mouse_drag(x1: int, y1: int, x2: int, y2: int, pointer_addr: int | None = None,
               hotspot: str = "top-left") -> str:
    """Drag with the left button from (x1, y1) to (x2, y2), e.g. a card, a window or an icon.

    Places the pointer on (x1, y1) with mouse_to, presses, moves to (x2, y2) in a few steps using the
    pointer gain mouse_to just measured (the pointer can't be tracked while it carries something),
    then releases. Check the result with a screenshot.

    If drags overshoot or the reported gain varies between calls, the guest is accelerating the
    pointer: in Windows 3.x set Control Panel > Mouse > Mouse Tracking Speed to Slow first.
    """
    v = vm()
    placed = mouse_to(x1, y1, pointer_addr=pointer_addr, hotspot=hotspot, tolerance=2)
    if not placed.startswith("at target"):
        return f"could not grab: {placed}"
    gx, gy = _mouse_state["gain"]
    v.auto.mouse(0, 0, 1)
    _advance(60)
    # Same slow, unaccelerated motion mouse_to measured its gain with.
    _move_counts(round((x2 - x1) / gx), round((y2 - y1) / gy), 1, slow=True)
    _advance(80)
    v.auto.mouse(0, 0, 0)
    _advance(100)
    return f"dragged from ({x1},{y1}) to about ({x2},{y2}); gain {gx:.2f}x{gy:.2f} px/count"


@mcp.tool()
def mouse_to(x: int, y: int, pointer_addr: int | None = None, hotspot: str = "top-left",
             tolerance: int = 1, max_moves: int = 10) -> str:
    """Put the pointer at (x, y), closing the loop on where it actually lands.

    Guest mouse drivers scale and accelerate relative motion, so this homes the pointer to the top-left
    corner, moves, measures, and corrects. Two ways to measure:
    - pointer_addr: physical address of the program's pointer position, two little-endian int16 (x, y).
      Exact; x/y are then in the program's own units.
    - otherwise the screen: the pointer is found by comparing with a frame taken while it sits in the
      corner, so the rest of the screen must be still (menus, installers, desktops). x/y are native
      frame pixels (screenshot reports the native size). hotspot: "top-left" for arrow pointers,
      "center" for crosshairs. Targets within ~32 px of the top-left corner can't be measured this way.
    """
    v = vm()
    corner = 32

    noise = None

    def measure(home):
        if pointer_addr is not None:
            raw = v.auto.memr(pointer_addr, 4)
            return int.from_bytes(raw[:2], "little", signed=True), int.from_bytes(raw[2:], "little", signed=True)
        return _pointer_from_screen(_frame(), home, corner, hotspot, noise)

    # Home in packet-sized chunks: 86Box hands motion to the guest at most 255 counts per mouse
    # packet and queues the rest, so one huge move would still be draining (and would swallow the
    # next moves) long after it was sent.
    for _ in range(12):
        v.auto.mouse(-250, -250, 0)
        _advance(25)
    _advance(50)
    home = _frame() if pointer_addr is None else None
    if home is not None:
        # Learn what changes on its own (blinking carets, clocks) while the pointer sits still.
        from PIL import ImageFilter

        _advance(270)
        noise = _changes(_frame(), home)
        _advance(270)
        noise = PILImage.composite(noise, _changes(_frame(), home), noise).filter(ImageFilter.MaxFilter(9))
    pos = None
    if pointer_addr is None:
        # Nudge out of the corner so there is something to measure; how far one count moves the
        # pointer depends on the guest's driver, so keep pushing until it shows up.
        nudge = corner * 2
        for _ in range(5):
            v.auto.mouse(nudge, nudge, 0)
            _advance(60)
            pos = measure(home)
            if pos is not None:
                break
    else:
        pos = measure(home)
    gain_x = gain_y = 1.0
    moves = 0
    while moves < max_moves:
        if pos is None:
            return ("could not find the pointer on screen. Is it visible? Some programs hide it, e.g. dialogs "
                    "meant for the keyboard; use press_keys there. Animation elsewhere on screen can also hide it.")
        ex, ey = x - pos[0], y - pos[1]
        if abs(ex) <= tolerance and abs(ey) <= tolerance:
            break
        dx, dy = round(ex / gain_x), round(ey / gain_y)
        dx = dx or (1 if ex > 0 else -1 if ex < 0 else 0)
        dy = dy or (1 if ey > 0 else -1 if ey < 0 else 0)
        _move_counts(dx, dy, 0, slow=True)
        _advance(40)
        moves += 1
        new = measure(home)
        if new is not None:
            # Learn how far one count moves the pointer; keep the estimate sane.
            if dx and new[0] != pos[0]:
                gain_x = min(max((new[0] - pos[0]) / dx, 0.1), 10.0)
            if dy and new[1] != pos[1]:
                gain_y = min(max((new[1] - pos[1]) / dy, 0.1), 10.0)
        pos = new
    if pos is None:
        return "lost the pointer while moving"
    _mouse_state.update(pos=pos, gain=(gain_x, gain_y))
    ok = abs(x - pos[0]) <= tolerance and abs(y - pos[1]) <= tolerance
    return (f"{'at' if ok else 'NOT at'} target: pointer ({pos[0]},{pos[1]}), target ({x},{y}), "
            f"{moves} corrective moves")


@mcp.tool()
def act(hold_keys: list[str] | None = None, ms: int = 150, mouse_dx: int = 0, mouse_dy: int = 0,
        mouse_buttons: int = 0, max_width: int = 480) -> list:
    """Game-loop primitive: hold keys (and mouse) for ms of emulated time, release, return a screenshot.

    With the machine held, the game is frozen between calls, so each call is one turn. Example for DOOM:
    act(["up","ctrl"], 200) = run forward firing for 0.2 s.
    """
    v = vm()
    codes = [c for combo in (hold_keys or []) for c in K.parse_combo(combo)]
    v.auto.keys(True, codes)
    if mouse_dx or mouse_dy or mouse_buttons:
        v.auto.mouse(mouse_dx, mouse_dy, mouse_buttons)
    _advance(ms)
    v.auto.keys(False, list(reversed(codes)))
    if mouse_buttons:
        v.auto.mouse(0, 0, 0)
    png, info = _snapshot(max_width)
    return [Image(data=png, format="png"), _time_note(info)]


# Waiting and DOS -----------------------------------------------------------


@mcp.tool()
def wait_for_text(text: str, timeout_ms: int = 10000) -> str:
    """Run until text appears on one row of the text-mode screen, or timeout_ms of emulated time passes."""
    r = vm().auto.wait_text(text, timeout_ms)
    return ("found" if r.get("matched") else "TIMED OUT") + f"; {_time_note(r)}"


_PROMPT = re.compile(r"^[A-Z]:\\[^>]*>$")


@mcp.tool()
def dos_run(command: str, timeout_ms: int = 30000, poll_ms: int = 100) -> str:
    """Type a DOS command plus Enter, run until the command prompt returns, and return the screen text.

    Gives up after timeout_ms of emulated time (the program may still be running or waiting for input).
    """
    v = vm()
    _type(command + "\n", 40)
    waited = 0
    while waited < timeout_ms:
        _advance(poll_ms)
        waited += poll_ms
        scr = v.auto.text() if v.auto.status().get("bios_mode", 0) in (2, 3, 7) else None
        if scr:
            row, col = scr.cursor
            line = scr.lines[row].rstrip() if row < len(scr.lines) else ""
            if _PROMPT.match(line) and col == len(line):
                return scr.render()
    try:
        screen = v.auto.text().render()
    except AutomationError as e:
        screen = f"(no text screen: {e})"
    return f"TIMED OUT after {timeout_ms} ms emulated; still running or waiting for input.\n{screen}"


# Recording -----------------------------------------------------------------


@mcp.tool()
def record_start(directory: str, interval_ms: int = 100) -> str:
    """Record the screen: 86Box saves the displayed frame every interval_ms of emulated time into
    directory (one PNG per frame, named by emulated ms), whatever advances the clock.

    Use record_stop to finish; it can render a video.
    """
    d = Path(directory).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    r = vm().auto.call(f"record start {int(interval_ms)} {d}")
    _record_dirs.append(d)
    return f"recording every {interval_ms} ms of emulated time to {d}; {_time_note(r)}"


@mcp.tool()
def record_stop(video: str | None = None, calls_log: str | None = None, fast_factor: int = 8) -> str:
    """Stop recording. With video (.mp4 or .gif), render it: real speed around interactions, idle
    stretches fast-forwarded fast_factor times. calls_log is an optional JSONL of
    {"start_ms", "end_ms", "caption"} for captions (mcpctl writes one).
    """
    v = vm()
    r = v.auto.call("record stop")
    msg = f"stopped recording; {r.get('frames')} frames"
    if video:
        from .ctl import render_timeline

        frames = _last_record_dir()
        render_timeline(frames, Path(video).expanduser(), Path(calls_log).expanduser() if calls_log else None,
                        fast_factor=fast_factor)
        msg += f"; rendered {video}"
    return msg


_record_dirs: list[Path] = []


def _last_record_dir() -> Path:
    if not _record_dirs:
        raise ValueError("no recording made in this session")
    return _record_dirs[-1]


# Serial --------------------------------------------------------------------


@mcp.tool()
def serial_read(port: int = 1, from_start: bool = False) -> str:
    """Output the guest has written to a COM port since the last serial_read (from_start: since boot).

    COM1 is captured when the VM was started with vm_start(com1_log=True). In DOS,
    `ECHO hello > COM1` or a program writing to the port shows up here; it is the easiest way to get
    debug logs out of your own programs.
    """
    text = vm().serial_read(port, from_start)
    return text if text else "(no new output)"


@mcp.tool()
def wait_for_serial(text: str, port: int = 1, timeout_ms: int = 10000, poll_ms: int = 20) -> str:
    """Run until `text` appears in new COM port output, or timeout_ms of emulated time passes.

    Returns the output seen while waiting, so a log line can be waited for and read in one call.
    """
    v = vm()
    seen = ""
    waited = 0
    while True:
        seen += v.serial_read(port)
        if text in seen:
            return f"found after {waited} ms\n{seen}"
        if waited >= timeout_ms:
            return f"TIMED OUT after {timeout_ms} ms\n{seen or '(no output)'}"
        _advance(poll_ms)
        waited += poll_ms


# Files ---------------------------------------------------------------------


@mcp.tool()
def dos_put_floppy(paths: list[str], drive: str = "A") -> str:
    """Copy host files/directories onto a fresh 1.44 MB floppy image and insert it in drive A: or B:."""
    v = vm()
    img = v.make_floppy(paths)
    v.load_floppy(img, 0 if drive.upper().startswith("A") else 1)
    names = ", ".join(Path(p).name for p in paths)
    return f"inserted floppy with {names} in {drive.upper()[0]}:"


@mcp.tool()
def floppy_insert(image: str, drive: str = "A", write_protect: bool = False) -> str:
    """Insert an existing floppy image (.img/.ima/.86f, ...) in drive A: or B:. Works for any guest.

    Images outside $HOME and /tmp are copied first. With write_protect=False the guest can write to the
    image file itself.
    """
    v = vm()
    path = Path(image).expanduser().resolve()
    if not any(path.is_relative_to(p) for p in (Path.home(), Path("/tmp"))):
        copy = v.run_dir / path.name
        shutil.copy(path, copy)
        path = copy
    index = 0 if drive.upper().startswith("A") else 1
    v.load_floppy(("wp://" if write_protect else "") + str(path), index)  # str: Path would collapse "//"
    mounted = [f["file"] for f in v.auto.info()["floppy"] if f["drive"] == index]
    if not mounted:
        raise AutomationError(f"86Box did not mount {path} in {drive.upper()[0]}:")
    return f"inserted {path.name} in {drive.upper()[0]}:"


@mcp.tool()
def floppy_eject(drive: str = "A") -> str:
    """Eject the floppy from drive A: or B:."""
    vm().auto.call(f"fdd eject {0 if drive.upper().startswith('A') else 1}")
    return f"ejected {drive.upper()[0]}:"


@mcp.tool()
def cdrom_insert(image: str | None = None, files: list[str] | None = None, drive: int = 0) -> str:
    """Insert a CD in the emulated CD-ROM drive: an existing image (.iso/.cue) or a new ISO built
    from host files and directories (ISO 9660 8.3 names plus Joliet long names).

    Works for any guest; DOS needs a CD-ROM driver and MSCDEX (or SHSUCDX) to read it. VMs made by
    vm_create/dos_create_vm have an ATAPI CD-ROM drive as primary slave (this board's BIOS leaves the
    secondary IDE channel off). The first read after a disc change may report "drive not ready"; retry.
    """
    v = vm()
    if (image is None) == (files is None):
        raise ValueError("pass exactly one of image or files")
    path = Path(image) if image else v.make_iso(files)
    v.load_cdrom(path, drive)
    mounted = [c["file"] for c in v.auto.info()["cdrom"] if c["drive"] == drive]
    if not mounted:
        raise AutomationError(f"86Box did not mount {path}; is there a CD-ROM drive {drive}? See vm_info.")
    return f"inserted {mounted[0]} in CD-ROM drive {drive}"


@mcp.tool()
def cdrom_eject(drive: int = 0) -> str:
    """Eject the CD from an emulated CD-ROM drive."""
    vm().auto.call(f"cd eject {drive}")
    return f"ejected CD-ROM drive {drive}"


# Memory and CPU ------------------------------------------------------------


CHUNK = 65536  # the automation socket's largest memr


def _check_range(addr: int, length: int) -> int:
    """Refuse addresses outside the emulated RAM; returns the RAM size in bytes."""
    size = vm().mem_bytes()
    if addr < 0 or length < 0 or addr + length > size:
        raise ValueError(f"{addr:#x}+{length} is outside the emulated RAM (0..{size:#x}, "
                         f"{size // 1024} KB; see vm_info)")
    return size


def _read_range(start: int, end: int) -> bytes:
    v = vm()
    return b"".join(v.auto.memr(a, min(CHUNK, end - a)) for a in range(start, end, CHUNK))


def _span(start: int, end: int | None) -> tuple[int, int]:
    size = vm().mem_bytes()
    end = size if end is None else end
    _check_range(start, end - start)
    return start, end


@mcp.tool()
def read_memory(addr: int, length: int = 256) -> str:
    """Hex dump of physical memory (real-mode seg:off -> seg*16+off). Up to 65536 bytes, within the
    emulated RAM (vm_info reports mem_kb)."""
    _check_range(addr, length)
    data = vm().auto.memr(addr, length)
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{addr + off:08x}  {chunk.hex(' '):<47}  {asc}")
    return "\n".join(lines)


@mcp.tool()
def write_memory(addr: int, hex_bytes: str) -> str:
    """Write bytes (hex string, e.g. "90 90 cc") to physical memory, within the emulated RAM."""
    data = bytes.fromhex(hex_bytes)
    _check_range(addr, len(data))
    vm().auto.memw(addr, data)
    return f"wrote {len(data)} bytes at {addr:#x}"


@mcp.tool()
def wait_for_memory(addr: int, hex_bytes: str, timeout_ms: int = 10000) -> str:
    """Run until physical memory at addr equals hex_bytes, or timeout_ms of emulated time passes."""
    data = bytes.fromhex(hex_bytes)
    _check_range(addr, len(data))
    r = vm().auto.wait_mem(addr, data, timeout_ms)
    return ("matched" if r.get("matched") else "TIMED OUT") + f"; {_time_note(r)}"


def _pattern(hex_bytes: str | None, text: str | None, value: int | None, size: int, signed: bool) -> bytes:
    given = [x is not None for x in (hex_bytes, text, value)]
    if sum(given) != 1:
        raise ValueError("give exactly one of hex_bytes, text or value")
    if hex_bytes is not None:
        return bytes.fromhex(hex_bytes)
    if text is not None:
        return text.encode("latin-1")
    if size not in (1, 2, 4):
        raise ValueError("size must be 1, 2 or 4")
    return value.to_bytes(size, "little", signed=signed)


@mcp.tool()
def find_memory(hex_bytes: str | None = None, text: str | None = None, value: int | None = None,
                size: int = 2, signed: bool = False, start: int = 0, end: int | None = None,
                max_results: int = 50) -> str:
    """Search physical memory for bytes (hex), text, or a little-endian number of `size` bytes.

    Searches start..end, by default the whole emulated RAM. For a real-mode DOS program, the first
    640 KB (end=0xA0000) is enough; DOS-extended programs and Windows use memory above 1 MB too.
    The BIOS's own count of conventional memory, in KB, is the word at 0x413.
    Lists the addresses where it occurs, at any alignment.
    """
    pat = _pattern(hex_bytes, text, value, size, signed)
    start, end = _span(start, end)
    data = _read_range(start, end)
    hits, i = [], data.find(pat)
    while i >= 0 and len(hits) < max_results:
        hits.append(start + i)
        i = data.find(pat, i + 1)
    more = " (more not shown)" if i >= 0 else ""
    return f"{len(hits)} found{more} in {start:#x}..{end:#x}: " + " ".join(f"{a:#x}" for a in hits)


_snapshots: dict[str, dict] = {}


@mcp.tool()
def snapshot_memory(name: str = "default", start: int = 0, end: int | None = None) -> str:
    """Remember the contents of start..end (default: all emulated RAM), for diff_memory.

    The way to find a variable: snapshot, make the game change it (move, score, lose a life), then
    diff_memory with the kind of change you expect. Each diff keeps only the addresses that matched,
    so repeat until a few are left.
    """
    start, end = _span(start, end)
    _snapshots[name] = {"start": start, "end": end, "data": _read_range(start, end), "candidates": None}
    return f"snapshot {name!r}: {end - start} bytes from {start:#x}"


def _num(buf: bytes, off: int, size: int, signed: bool) -> int:
    return int.from_bytes(buf[off:off + size], "little", signed=signed)


@mcp.tool()
def diff_memory(change: str = "changed", name: str = "default", size: int = 1, signed: bool = False,
                value: int | None = None, max_results: int = 50) -> str:
    """Compare memory with snapshot `name` and keep only the addresses that changed as described.

    change: "changed", "unchanged", "increased", "decreased", or "equals" (now equal to value).
    size: 1, 2 or 4 bytes, read little-endian, signed or not. The first diff after a snapshot looks
    at every address; later ones only at the survivors, and the snapshot then moves to the present.
    """
    if change not in ("changed", "unchanged", "increased", "decreased", "equals"):
        raise ValueError("change must be changed, unchanged, increased, decreased or equals")
    if change == "equals" and value is None:
        raise ValueError("change='equals' needs value")
    if size not in (1, 2, 4):
        raise ValueError("size must be 1, 2 or 4")
    snap = _snapshots.get(name)
    if snap is None:
        raise ValueError(f"no snapshot {name!r}; call snapshot_memory first")
    start, end, old = snap["start"], snap["end"], snap["data"]
    new = _read_range(start, end)

    def keep(off: int) -> bool:
        if off + size > len(new):
            return False
        a, b = _num(old, off, size, signed), _num(new, off, size, signed)
        return {"changed": a != b, "unchanged": a == b, "increased": b > a, "decreased": b < a,
                "equals": b == value}[change]

    if snap["candidates"] is not None:
        survivors = [off for off in snap["candidates"] if keep(off)]
    elif change in ("unchanged", "equals"):
        survivors = [off for off in range(len(new) - size + 1) if keep(off)]
    else:
        # Only bytes that differ can start a changed value: look near them, chunk by chunk.
        survivors = []
        for c in range(0, len(new), 4096):
            if old[c:c + 4096] == new[c:c + 4096]:
                continue
            for off in range(max(0, c - size + 1), min(len(new), c + 4096)):
                if (not survivors or survivors[-1] < off) and keep(off):
                    survivors.append(off)
    snap["candidates"], snap["data"] = survivors, new
    shown = survivors[:max_results]
    def fmt(off):
        return f"{start + off:#x}={_num(new, off, size, signed)}"
    more = f" (showing {len(shown)})" if len(survivors) > len(shown) else ""
    return f"{len(survivors)} addresses {change}{more}: " + " ".join(fmt(o) for o in shown)


@mcp.tool()
def registers() -> dict:
    """CPU registers at the current instruction boundary (sampled between 1 ms quanta)."""
    return vm().auto.regs()


# Debugging, through 86Box's GDB stub ------------------------------------------------------------


def _debugger() -> tuple[VM, Gdb]:
    v = vm()
    if v.gdb is None:
        raise AutomationError("no debugger: start with vm_start(debugger=True), which needs 86Box built "
                              "with -D GDBSTUB=ON")
    return v, v.gdb


def _linear_eip(g: Gdb) -> int:
    return g.regs()["eip"]      # the stub reports eip as a linear address (CS base + EIP)


def _stop_cpu(v: VM, g: Gdb) -> None:
    """Make sure the stub has the CPU stopped at an instruction boundary."""
    if v.gdb_stopped:
        return
    g.sock.sendall(b"\x03")     # interrupt; the stub stops the CPU when it next runs
    with v.expecting_stop():
        v.auto.step(1)
    g.recv(timeout=10)
    v.gdb_stopped = True


def _single_step(v: VM, g: Gdb) -> str:
    """One instruction, stepping over an execution breakpoint at the current address."""
    here = _linear_eip(g)
    over = [(a, k, n) for (a, k, n) in v.breaks if k == "exec" and a == here]
    for a, k, n in over:
        g.clear_break(a, k, n)
    g.send("s")
    with v.expecting_stop():
        v.auto.step(1)
    stop = g.recv(timeout=10)
    for a, k, n in over:
        g.set_break(a, k, n)
    v.gdb_stopped = True
    return stop


def _where(v: VM, g: Gdb) -> str:
    r = v.auto.regs()
    lin = _linear_eip(g)
    code = v.auto.memr(lin, 8).hex(" ") if lin < v.mem_bytes() else "?"
    return f"{r['cs']:04x}:{r['eip']:08x} (linear {lin:#x}), bytes {code}"


@mcp.tool()
def breakpoint_set(addr: int, kind: str = "exec", length: int = 1, clear: bool = False) -> str:
    """Stop the CPU when it executes the instruction at addr, or when it writes, reads or accesses
    memory at addr..addr+length (kind "exec", "write", "read" or "access"). clear=True removes it.

    Addresses are linear: in real mode, segment*16 + offset; without paging, physical. Then
    run_until_break runs to it. Watchpoints stop after the access, with the CPU at the next
    instruction. Needs vm_start(debugger=True).
    """
    v, g = _debugger()
    if kind not in BREAK_KINDS or kind == "exec_sw":
        raise ValueError("kind must be exec, write, read or access")
    key = (addr, kind, length)
    if clear:
        g.clear_break(addr, kind, length)
        v.breaks.discard(key)
        return f"cleared {kind} breakpoint at {addr:#x}"
    g.set_break(addr, kind, length)
    v.breaks.add(key)
    return f"{kind} breakpoint at {addr:#x}" + (f" ({length} bytes)" if kind != "exec" else "") + \
        f"; {len(v.breaks)} set"


def _resume(v: VM, g: Gdb) -> None:
    if v.gdb_stopped:
        _single_step(v, g)      # over a breakpoint at the current instruction
        g.cont()
        v.gdb_stopped = False


@mcp.tool()
def debug_continue() -> str:
    """Let the CPU go on after a breakpoint or step_instructions, so step, press_keys, dos_run and
    the rest advance the machine again. Breakpoints stay set; a hit during another tool's call stops
    the CPU and that call reports it."""
    v, g = _debugger()
    was = v.gdb_stopped
    _resume(v, g)
    return "the CPU runs again (held: time passes in tool calls)" if was else "the CPU wasn't stopped"


@mcp.tool()
def run_until_break(timeout_ms: int = 10000) -> str:
    """Run until a breakpoint or watchpoint stops the CPU, or timeout_ms of emulated time passes.

    Reports where the CPU stopped and why. Running on from a breakpoint steps over it first. The
    CPU then stays stopped, and tools that need time to pass (step, press_keys, dos_run...) fail
    until debug_continue.
    """
    v, g = _debugger()
    _resume(v, g)
    with v.expecting_stop():
        r = v.auto.step(timeout_ms)
    if r.get("stopped") != "debugger":
        return f"no break in {timeout_ms} ms; {_time_note(r)}"
    why = describe_stop(g.recv(timeout=10))
    return f"{why} at {_where(v, g)}; emulated t={r['ms']}ms"


@mcp.tool()
def step_instructions(count: int = 1) -> str:
    """Execute count instructions (up to 200) one at a time, listing each address and its first bytes.

    Stops the CPU first if it's running. Afterwards the CPU stays stopped: debug_continue or
    run_until_break go on.
    Each instruction counts as one 1 ms quantum on the automation clock (the guest itself sees only
    the instruction's own cycles).
    """
    if not 1 <= count <= 200:
        raise ValueError("count must be 1..200")
    v, g = _debugger()
    _stop_cpu(v, g)
    lines = [f"start  {_where(v, g)}"]
    for i in range(count):
        _single_step(v, g)
        lines.append(f"{i + 1:5d}  {_where(v, g)}")
    return "\n".join(lines)


_WIDTHS = {1: "b", 2: "w", 4: "l"}


def _io_values(out: str) -> list[int]:
    return [int(x, 16) for line in out.splitlines() if ":" in line for x in line.split(":", 1)[1].split()]


@mcp.tool()
def io_read(port: int, width: int = 1, count: int = 1) -> str:
    """Read count values of width bytes (1, 2 or 4) from I/O port `port` upward, as the CPU's IN would.

    Reading a port can change the device's state (a status register that clears on read, a FIFO).
    Works while held. Needs vm_start(debugger=True).
    """
    if width not in _WIDTHS or not 1 <= count <= 256:
        raise ValueError("width must be 1, 2 or 4, and count 1..256")
    _, g = _debugger()
    vals = _io_values(g.monitor(f"i{_WIDTHS[width]} {port:x} {count * width:x}"))
    return f"port {port:#x}: " + " ".join(f"{x:#0{2 + 2 * width}x}" for x in vals)


@mcp.tool()
def io_write(port: int, value: int, width: int = 1) -> str:
    """Write value (width 1, 2 or 4 bytes) to I/O port `port`, as the CPU's OUT would.

    The device acts on it at once, as if the guest had written it. Works while held. Needs
    vm_start(debugger=True).
    """
    if width not in _WIDTHS:
        raise ValueError("width must be 1, 2 or 4")
    _, g = _debugger()
    g.monitor(f"o{_WIDTHS[width]} {port:x} {value:x}")
    return f"wrote {value:#x} to port {port:#x}"


_PCI_CLASSES = {0x00: "unclassified", 0x01: "storage", 0x02: "network", 0x03: "display", 0x04: "multimedia",
                0x05: "memory", 0x06: "bridge", 0x07: "communication", 0x08: "system", 0x09: "input",
                0x0C: "serial bus"}


def _pci_dwords(g: Gdb, bus: int, dev: int, fn: int, offsets: list[int]) -> list[int]:
    """Read config dwords through configuration mechanism #1 (ports 0xCF8/0xCFC), restoring 0xCF8."""
    saved = _io_values(g.monitor("il cf8 4"))[0]
    out = []
    for off in offsets:
        g.monitor(f"ol cf8 {0x80000000 | bus << 16 | dev << 11 | fn << 8 | (off & 0xFC):x}")
        out.append(_io_values(g.monitor("il cfc 4"))[0])
    g.monitor(f"ol cf8 {saved:x}")
    return out


@mcp.tool()
def pci_scan(bus: int = 0) -> str:
    """List the PCI functions on `bus`: address, vendor:device ID, class, and interrupt line/pin.

    Through configuration mechanism #1, as a driver would. PCI machines only. Needs
    vm_start(debugger=True).
    """
    _, g = _debugger()
    lines = []
    for dev in range(32):
        for fn in range(8):
            ident, cls, hdr, irq = _pci_dwords(g, bus, dev, fn, [0x00, 0x08, 0x0C, 0x3C])
            if ident in (0xFFFFFFFF, 0):
                if fn == 0:
                    break
                continue
            klass = _PCI_CLASSES.get(cls >> 24, f"class {cls >> 24:#04x}")
            pin = "ABCD"[((irq >> 8) & 0xFF) - 1] if 1 <= (irq >> 8) & 0xFF <= 4 else "-"
            lines.append(f"{bus:02x}:{dev:02x}.{fn} {ident & 0xFFFF:04x}:{ident >> 16:04x} {klass} "
                         f"({cls >> 8:06x}) irq line {irq & 0xFF} pin {pin}")
            if fn == 0 and not (hdr >> 16) & 0x80:
                break       # not a multi-function device
    return "\n".join(lines) or f"no PCI functions on bus {bus}"


@mcp.tool()
def pci_config_read(bus: int, device: int, function: int = 0, offset: int = 0, length: int = 64) -> str:
    """Hex dump of a PCI function's configuration space, offset..offset+length (up to 256), as
    dwords. Needs vm_start(debugger=True)."""
    if not (0 <= offset < 256 and 1 <= length <= 256 - offset):
        raise ValueError("offset and length must stay within the 256-byte configuration space")
    _, g = _debugger()
    start = offset & ~3
    offs = list(range(start, offset + length, 4))
    vals = _pci_dwords(g, bus, device, function, offs)
    rows = [f"{o:02x}: {v:08x}" for o, v in zip(offs, vals)]
    return f"{bus:02x}:{device:02x}.{function} config:\n" + "\n".join(rows)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
