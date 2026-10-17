"""Render pieces of SkiFree recordings (by emulated-ms range) and join them into one video.

    python render_segments.py out.mp4 rec2:305000-878000 rec3:428000-436000 ...

A third field keeps runs of identical frames longer (they're cut to 5 by default), for an
ending that should stay on screen: rec2:866000-877400:90
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from box86mcp.ctl import render_timeline  # noqa: E402

import os
ROOT = Path(os.environ.get("RENDER_ROOT", Path.home() / "86box_mcp/.cache/skifree"))


def segment(spec: str, out: Path) -> None:
    rec, rng, *rest = spec.split(":")
    a, b = (int(x) for x in rng.split("-"))
    max_still = int(rest[0]) if rest else 5
    frames = ROOT / rec / "frames"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for f in frames.glob("*.png"):
            if a <= int(f.stem) <= b:
                (t / f.name).symlink_to(f)
        render_timeline(t, out, ROOT / rec / "calls.jsonl", max_still=max_still)


def main() -> None:
    out = Path(sys.argv[1])
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, spec in enumerate(sys.argv[2:]):
            p = Path(tmp) / f"part{i:02d}.mp4"
            segment(spec, p)
            parts.append(p)
            print("rendered", spec, flush=True)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], check=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
