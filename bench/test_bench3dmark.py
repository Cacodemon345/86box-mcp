"""3DMark 99 on Windows 98 with a Voodoo 2, as a test of an 86Box build.

    BENCH_IMAGE=<prepared image> BENCH_BINARY=<86Box> V9X_REPO=<V9x checkout> pytest bench/

- Reliability: the build boots the image, runs the benchmark to the end and exports a score, with
  no hard reset on the way.
- Fidelity: the results that depend only on the emulated machine (baseline.json lists them:
  CPU 3DMarks) are within BENCH_TOLERANCE (default 2 %) of baseline.json. A change means 86Box
  now emulates the CPU's timing differently. The Voodoo-bound results aren't checked: the
  emulated Voodoo runs as fast as the host renders, so they measure the host (see README.md).
- Performance: if BENCH_MIN_SPEED is set, the benchmark ran at least that fast, in emulated
  seconds per wall second, as a percentage. It depends on the host, so there is no default; take
  it from a run of a known-good build on the same machine.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NEEDED = ("BENCH_IMAGE", "BENCH_BINARY", "V9X_REPO")
pytestmark = pytest.mark.skipif(not all(os.environ.get(v) for v in NEEDED),
                                reason="set " + ", ".join(NEEDED))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    import bench3dmark as B
    sys.path.insert(0, str(Path(os.environ["V9X_REPO"]) / "mcp"))
    from v9x_mcp import ToolHandler

    class V9x:
        def __init__(self):
            self.h = ToolHandler("127.0.0.1", 9869, 60.0)

        def call(self, tool, args):
            out = self.h.call(tool, args)     # MCP content: [{"type": "text", "text": ...}]
            return "".join(c.get("text", "") for c in out) if isinstance(out, list) else str(out)

    work = tmp_path_factory.mktemp("bench") / "run"
    return B.run_once("test", os.environ["BENCH_BINARY"], 1, Path(os.environ["BENCH_IMAGE"]), work, V9x(),
                      B.load_templates())


def test_reliability(run):
    assert run.ok, run.failure
    assert run.hard_resets == 0


@pytest.mark.parametrize("test", json.loads((HERE / "baseline.json").read_text())["results"])
def test_fidelity(run, test):
    assert run.ok, run.failure
    baseline = json.loads((HERE / "baseline.json").read_text())["results"][test]
    got = run.score(test)
    tolerance = float(os.environ.get("BENCH_TOLERANCE", "2")) / 100
    assert got is not None, f"no {test!r} in 3DMark's export"
    assert abs(got - baseline) <= tolerance * baseline, f"{test}: {got} against a baseline of {baseline}"


def test_performance(run):
    assert run.ok, run.failure
    minimum = os.environ.get("BENCH_MIN_SPEED")
    if not minimum:
        pytest.skip("set BENCH_MIN_SPEED (percent) from a known-good build on this host")
    assert run.bench_speed >= float(minimum), \
        f"benchmark ran at {run.bench_speed:.0f} % of real time; the minimum is {minimum} %"
