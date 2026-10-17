"""Measure automation-socket latencies against a booted DOS VM.

Usage: python -m bench.latency   (from server/, uses the test VM recipe)
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import make_dos_vm  # noqa: E402

from box86mcp.server import _snapshot  # noqa: E402
import box86mcp.server as srv  # noqa: E402
from box86mcp.vm import VM  # noqa: E402


def timeit(fn, n):
    samples = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples), max(samples)


def main():
    with tempfile.TemporaryDirectory() as d:
        vm = VM(make_dos_vm(Path(d) / "vm"))
        vm.start(held=True)
        a = vm.auto
        try:
            a.wait_text("C:\\>", 60000)
            rows = []
            rows.append(("status round trip", *timeit(a.status, 200)))
            rows.append(("step 1 ms", *timeit(lambda: a.step(1), 200)))
            rows.append(("step 29 ms (1 DOOM tic)", *timeit(lambda: a.step(29), 50)))
            t = time.perf_counter()
            a.step(10000)
            wall = time.perf_counter() - t
            rows.append(("step 10 s emulated", wall * 1000, wall * 1000))
            shot = Path(d) / "s.png"
            rows.append(("screenshot to PNG file", *timeit(lambda: a.screenshot(str(shot)), 50)))
            rows.append(("text screen", *timeit(a.text, 50)))
            rows.append(("memr 64 KiB", *timeit(lambda: a.memr(0, 65536), 20)))
            srv._vm, srv._held = vm, True
            rows.append(("MCP screenshot (PNG + scale 640)", *timeit(lambda: _snapshot(640), 30)))
            print(f"{'operation':36} {'median ms':>10} {'max ms':>8}")
            for name, med, mx in rows:
                print(f"{name:36} {med:10.2f} {mx:8.2f}")
            print(f"\nheld-mode speed: {10000 / (wall * 1000):.1f}x real time (486DX2-66, interpreter)")
        finally:
            vm.stop()


if __name__ == "__main__":
    main()
