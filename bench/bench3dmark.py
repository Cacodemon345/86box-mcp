"""Benchmark 86Box builds with 3DMark 99 MAX on Windows 98 with a 3dfx Voodoo 2.

    python bench/bench3dmark.py --image IMAGE_DIR --binary new=/path/to/86Box \\
        [--binary old=/path/to/older/86Box] [--runs 3] [--out bench-out]

Each run starts from a pristine copy of the prepared image (see README.md), so every build sees
exactly the same disk. A run:

1. boots Windows held, stepping as fast as the host allows, until the taskbar appears;
2. releases the machine and waits for V9x Remote Agent (installed in the image), which launches
   3DMark 99 and clicks Benchmark;
3. holds again and steps as fast as the host allows until the score appears: the timed part;
4. releases, saves the project, exports the results as text through 3DMark's Result Browser,
   and fetches them.

It reports, per build:

- reliability: whether every phase finished, and why not (86Box stopped answering, a reset,
  a timeout);
- speed: emulated seconds per wall second while held, for the boot and for the benchmark. This
  is the number that falls when 86Box gets slower. Real-time pacing would cap it at 100%;
- the score and every sub-test from 3DMark's own export. The benchmark measures in emulated
  time, so its results depend on how 86Box emulates the hardware's timing, not on the host:
  a changed score means the emulation changed.

Needs 86Box built with -D AUTOMATION=ON, the box86mcp package (server/), and V9x Remote Agent's
mcp/v9x_mcp.py (--v9x-repo) on the host side.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from box86mcp import keys as K
from box86mcp.vm import VM

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

# Where things are on the 640x480 desktop of the prepared image (see README.md).
TEMPLATE_BOXES = {"desktop": (0, 454, 58, 479),      # the Start button: Explorer is up
                  "welcome": (112, 22, 530, 104),    # 3DMark's welcome dialog
                  "project": (290, 64, 605, 175),    # the project window, with the Voodoo 2 named
                  "score": (70, 68, 572, 98)}        # the Overall Score dialog
BENCHMARK_BUTTON = (448, 427)
SAVE_PROJECT_BUTTON = (507, 161)
RB_FILE_MENU, RB_EXPORT = (56, 70), (84, 217)
THREEDMARK = "C:\\Program Files\\3DMark 99 Max"
GUEST_PROJECT, GUEST_EXPORT = "C:\\SETUP\\BENCH.3DP", "C:\\SETUP\\BENCH.TXT"

BOOT_LIMIT_MS = 300_000           # emulated
BENCH_LIMIT_MS = 1_200_000        # emulated; the run takes about 315 s
STEP_MS = 2000
WALL_LIMIT_S = 300                # for each released, real-time phase


@dataclass
class Run:
    label: str
    binary: str
    run: int
    ok: bool = False
    failure: str = ""
    boot_emulated_s: float = 0.0
    boot_wall_s: float = 0.0
    bench_emulated_s: float = 0.0
    bench_wall_s: float = 0.0
    results: dict[str, str] = field(default_factory=dict)
    hard_resets: int = 0
    phase: str = ""         # the last phase started, for a failure

    @property
    def boot_speed(self) -> float:
        return 100 * self.boot_emulated_s / self.boot_wall_s if self.boot_wall_s else 0.0

    @property
    def bench_speed(self) -> float:
        return 100 * self.bench_emulated_s / self.bench_wall_s if self.bench_wall_s else 0.0

    def score(self, name: str = "3DMark Result") -> float | None:
        m = re.match(r"([\d,.]+)", self.results.get(name, ""))
        return float(m.group(1).replace(",", "")) if m else None


class Failed(Exception):
    pass


def load_templates() -> dict[str, Image.Image]:
    return {n: Image.open(TEMPLATES / f"{n}.png").convert("RGB") for n in TEMPLATE_BOXES}


def shows(vm: VM, tpl: dict[str, Image.Image], name: str, tolerance: float = 6.0) -> bool:
    try:
        png = vm.auto.screenshot()["png"]
    except (OSError, TimeoutError) as e:
        raise Failed(f"86Box stopped answering ({type(e).__name__}); log: {last_log_line(vm)}")
    frame = Image.open(BytesIO(png)).convert("RGB")
    if frame.size != (640, 480):
        return False
    crop = frame.crop(TEMPLATE_BOXES[name])
    return sum(ImageStat.Stat(ImageChops.difference(crop, tpl[name])).mean) / 3 < tolerance


def step_until(vm: VM, tpl, name: str, limit_ms: int) -> tuple[float, float]:
    """Held, unpaced: step until the template shows. Returns (emulated s, wall s)."""
    ms0, w0 = vm.auto.status()["ms"], time.monotonic()
    while True:
        try:
            ms = vm.auto.step(STEP_MS)["ms"]
        except (OSError, TimeoutError) as e:
            raise Failed(f"86Box stopped answering while waiting for {name} ({type(e).__name__}); "
                         f"log: {last_log_line(vm)}")
        if shows(vm, tpl, name):
            return (ms - ms0) / 1000, time.monotonic() - w0
        if ms - ms0 > limit_ms:
            raise Failed(f"no {name} after {limit_ms // 1000} s emulated")


def wait_real_time(what: str, check, limit_s: float = WALL_LIMIT_S, every: float = 1.0):
    deadline, last = time.monotonic() + limit_s, ""
    while time.monotonic() < deadline:
        try:
            r = check()
            if r:
                return r
        except Failed:
            raise
        except Exception as e:      # not up yet, usually; reported if it never is
            last = f" (last error: {type(e).__name__}: {e})"
        time.sleep(every)
    raise Failed(f"timed out waiting for {what}{last}")


def alive(vm: VM) -> str:
    """'' if 86Box still answers; otherwise how it went quiet. A fatal() leaves it behind an error
    dialog: with 86Box#8392 it closes the automation socket first; before, it just stops
    replying."""
    if not vm.running():
        return "86Box exited"
    try:
        vm.auto.sock.settimeout(10)
        vm.auto.status()
        return ""
    except TimeoutError:
        return "86Box stopped answering (no reply in 10 s)"
    except Exception as e:
        return f"86Box closed the automation socket ({type(e).__name__})"
    finally:
        if vm.auto.sock:
            vm.auto.sock.settimeout(vm.auto.timeout)


def last_log_line(vm: VM) -> str:
    lines = [l for l in vm.log_tail(30).splitlines() if l.strip() and "propagateSizeHints" not in l
             and not l.startswith("ALSA")]
    return lines[-1] if lines else ""


def parse_export(text: str) -> dict[str, str]:
    """'Test Results:' section of 3DMark's text export -> {test: value}."""
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith("Test Results:"):
            inside = True
        elif inside and line.strip() and not line.startswith("\t"):
            break
        elif inside and ":" in line:
            k, v = line.strip().rsplit(":", 1) if " : " not in line else line.strip().split(" : ", 1)
            out[k.strip()] = v.strip()
    return out


def run_once(label: str, binary: str, n: int, image: Path, work: Path, v9x, tpl) -> Run:
    r = Run(label, binary, n)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shutil.copy(image / "86box.cfg", work / "86box.cfg")
    shutil.copytree(image / "nvr", work / "nvr")
    shutil.copy(image / "hdd.img", work / "hdd.img")
    vm = VM(work, binary=Path(binary))
    # A 2 s step takes well under this even on a slow host. 86Box's fatal() waits on an error
    # dialog that nobody headless can dismiss, so not answering is how a crash shows.
    vm.auto.timeout = 60
    try:
        r.phase = "boot"
        vm.start(held=True)
        # 1. Boot, held and unpaced, until Explorer's taskbar shows.
        r.boot_emulated_s, r.boot_wall_s = step_until(vm, tpl, "desktop", BOOT_LIMIT_MS)
        resets0 = vm.auto.info().get("hard_resets", 0)
        # 2. Real time: the agent starts 3DMark and clicks Benchmark.
        r.phase = "launch 3DMark"
        vm.let_cpu_run()
        vm.auto.release()
        wait_real_time("the V9x agent", lambda: json.loads(v9x.call("v9x_info", {}))["desktop_ready"])
        v9x.call("v9x_exec", {"application": f"{THREEDMARK}\\3dmark.exe", "working_dir": THREEDMARK,
                              "detach": True})
        wait_real_time("3DMark's welcome dialog", lambda: shows(vm, tpl, "welcome"))
        vm.auto.keys(True, [K.scancode("enter")]); vm.auto.keys(False, [K.scancode("enter")])
        wait_real_time("3DMark's project window", lambda: shows(vm, tpl, "project"))
        x, y = BENCHMARK_BUTTON
        v9x.call("v9x_click", {"x": x, "y": y})
        # 3. The benchmark, held and unpaced, until the score shows.
        r.phase = "benchmark"
        vm.auto.hold()
        r.bench_emulated_s, r.bench_wall_s = step_until(vm, tpl, "score", BENCH_LIMIT_MS)
        r.hard_resets = vm.auto.info().get("hard_resets", 0) - resets0
        if r.hard_resets:
            raise Failed(f"{r.hard_resets} hard reset(s) during the benchmark")
        # 4. Real time again: save the project, export it as text, fetch it.
        r.phase = "save the project"
        vm.auto.release()
        x, y = SAVE_PROJECT_BUTTON
        v9x.call("v9x_click", {"x": x, "y": y})
        time.sleep(3)
        v9x.call("v9x_type", {"text": GUEST_PROJECT})
        v9x.call("v9x_key", {"keys": "ENTER"})
        wait_real_time("the saved project", lambda: v9x.call("v9x_stat", {"guest_path": GUEST_PROJECT}))
        r.phase = "export from the Result Browser"
        v9x.call("v9x_exec", {"application": f"{THREEDMARK}\\Result Browser.exe", "arguments": GUEST_PROJECT,
                              "working_dir": THREEDMARK, "detach": True})
        time.sleep(15)
        for (x, y) in (RB_FILE_MENU, RB_EXPORT):
            v9x.call("v9x_click", {"x": x, "y": y})
            time.sleep(2)
        v9x.call("v9x_type", {"text": GUEST_EXPORT})
        v9x.call("v9x_key", {"keys": "ENTER"})
        wait_real_time("the exported results", lambda: v9x.call("v9x_stat", {"guest_path": GUEST_EXPORT}))
        local = work / "results.txt"
        v9x.call("v9x_get_file", {"guest_path": GUEST_EXPORT, "local_path": str(local)})
        r.results = parse_export(local.read_bytes().decode("latin-1"))
        if r.score() is None:
            raise Failed("no score in the export")
        r.ok = True
    except Failed as e:
        r.failure = str(e)
    except Exception as e:
        # A V9x call that fails is often 86Box gone: tell that apart from a harness problem.
        gone = alive(vm)
        r.failure = (f"{gone}, seen as {type(e).__name__}; log: {last_log_line(vm)}" if gone
                     else f"harness: {type(e).__name__}: {e}")
    finally:
        try:
            vm.auto.screenshot(str(work / "last.png"))
        except Exception:
            pass
        vm.stop(timeout=10)
        (work / "hdd.img").unlink(missing_ok=True)
    return r


def report(runs: list[Run]) -> str:
    labels = list(dict.fromkeys(r.label for r in runs))
    def med(xs):
        xs = [x for x in xs if x is not None]
        return statistics.median(xs) if xs else None
    def fmt(x, unit=""):
        return "-" if x is None else (f"{x:,.0f}{unit}" if abs(x) >= 100 else f"{x:.1f}{unit}")

    def spread(xs, unit=""):
        """Median, and the range when there is more than one run."""
        xs = [x for x in xs if x is not None]
        if not xs:
            return "-"
        m = fmt(statistics.median(xs), unit)
        return m if len(xs) == 1 else f"{m} ({fmt(min(xs))}–{fmt(max(xs))})"
    rows = [("Runs completed", lambda rs: f"{sum(r.ok for r in rs)} of {len(rs)}"),
            ("Boot: emulated s", lambda rs: fmt(med([r.boot_emulated_s for r in rs]))),
            ("Boot: speed (emulated/wall)", lambda rs: fmt(med([r.boot_speed for r in rs]), " %")),
            ("Benchmark: emulated s", lambda rs: fmt(med([r.bench_emulated_s for r in rs if r.bench_wall_s]))),
            ("Benchmark: wall s", lambda rs: spread([r.bench_wall_s for r in rs if r.bench_wall_s])),
            ("Benchmark: speed (emulated/wall)",
             lambda rs: spread([r.bench_speed for r in rs if r.bench_wall_s], " %"))]
    tests = [t for t in dict.fromkeys(k for r in runs for k in r.results)
             if any(r.score(t) is not None for r in runs)]      # leave out tests that didn't run
    for t in tests:
        rows.append((t, lambda rs, t=t: spread([r.score(t) for r in rs if r.ok])))
    out = ["| | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for name, f in rows:
        out.append(f"| {name} | " + " | ".join(f([r for r in runs if r.label == l]) for l in labels) + " |")
    failures = [f"- {r.label} run {r.run}, in {r.phase}: {r.failure}" for r in runs if not r.ok]
    if failures:
        out += ["", "Failures:", *failures]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--image", required=True, help="prepared VM folder: 86box.cfg, nvr/, hdd.img")
    ap.add_argument("--binary", action="append", required=True, help="LABEL=PATH, or PATH; repeat to compare")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", default="bench-out")
    ap.add_argument("--v9x-repo", default=os.environ.get("V9X_REPO", ""), help="V9x Remote Agent checkout")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.v9x_repo) / "mcp"))
    from v9x_mcp import ToolHandler

    class V9x:
        """V9x Remote Agent, through its own MCP tool handler: one call, one connection."""
        def __init__(self):
            self.h = ToolHandler("127.0.0.1", 9869, 60.0)
        def call(self, tool, args):
            out = self.h.call(tool, args)     # MCP content: [{"type": "text", "text": ...}]
            return "".join(c.get("text", "") for c in out) if isinstance(out, list) else str(out)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tpl = load_templates()
    runs = []
    for spec in a.binary:
        label, _, path = spec.rpartition("=")
        label = label or Path(path).parent.name
        for n in range(1, a.runs + 1):
            print(f"== {label} run {n}", flush=True)
            r = run_once(label, path, n, Path(a.image), out / "runs" / f"{label}-{n}", V9x(), tpl)
            runs.append(r)
            print(f"   {'ok' if r.ok else f'FAILED in {r.phase}: ' + r.failure}; boot {r.boot_emulated_s:.0f} s at "
                  f"{r.boot_speed:.0f} %; benchmark {r.bench_emulated_s:.0f} s at {r.bench_speed:.0f} %; "
                  f"score {r.score()}", flush=True)
            (out / "results.json").write_text(json.dumps(
                [asdict(x) | {"boot_speed": x.boot_speed, "bench_speed": x.bench_speed} for x in runs], indent=1))
    md = report(runs)
    (out / "report.md").write_text(md)
    print("\n" + md)
    return 0 if all(r.ok for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
