"""Several VMs' recordings in one video, on their shared emulated clock.

Each VM's frames come from record_start (PNGs named by emulated ms). Linked VMs step in lockstep, so
their clocks agree to within a quantum; each video frame shows, for every VM, its frame nearest that
emulated time, in a grid (two side by side, up to four 2x2, up to eight 4x2), with the clock on top.

    python -m box86mcp.montage OUT.mp4 --vm Alpha rec/p1 rec2/p1 --vm Bravo rec/p2 \\
        --speed 0:4 75000:2 143000:1
"""

from __future__ import annotations

import argparse
import bisect
import math
import subprocess
from pathlib import Path

W, H, BAR, GAP, FPS = 640, 480, 28, 8, 20


def _frames(dirs) -> tuple[list[int], list[Path]]:
    found = sorted((int(p.stem), p) for d in dirs for p in Path(d).glob("*.png") if p.stem.isdigit())
    if not found:
        raise ValueError(f"no recorded frames in {', '.join(map(str, dirs))}")
    return [t for t, _ in found], [p for _, p in found]


def _nearest(times: list[int], paths: list[Path], t: float) -> Path:
    i = bisect.bisect_left(times, t)
    if i == len(times) or (i and t - times[i - 1] < times[i] - t):
        i -= 1
    return paths[i]


def _panel(path: Path):
    from PIL import Image

    img = Image.open(path).convert("RGB")
    scale = min(W / img.width, H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)))
    out = Image.new("RGB", (W, H))
    out.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return out


def parse_speed(spec: list[str] | None) -> list[tuple[float, float]]:
    """["0:4", "75000:1"] -> from 0 ms 4x, from 75 s real speed."""
    out = sorted(tuple(map(float, s.split(":"))) for s in (spec or ["0:1"]))
    if out[0][0] > 0:
        out.insert(0, (0.0, 1.0))
    if any(f <= 0 for _, f in out):
        raise ValueError("speed factors must be positive")
    return out


def render_grid(sources: list[tuple[str, list]], output: str | Path,
                speed: list[tuple[float, float]] | None = None) -> str:
    """Render [(label, [frame dirs]), ...] as one video (.mp4). Returns a summary."""
    from PIL import Image, ImageDraw, ImageFont

    if not sources:
        raise ValueError("no recordings to render")
    speed = speed or [(0.0, 1.0)]
    clips = [(label, *_frames(dirs)) for label, dirs in sources]
    start = max(c[1][0] for c in clips)
    end = min(c[1][-1] for c in clips)
    if end <= start:
        raise ValueError("the recordings don't overlap in emulated time")
    cols = len(clips) if len(clips) <= 2 else 2 if len(clips) <= 4 else math.ceil(len(clips) / 2)
    rows = math.ceil(len(clips) / cols)
    size = (cols * W + (cols - 1) * GAP, BAR + rows * (BAR + H) + (rows - 1) * GAP)
    size = (size[0] + size[0] % 2, size[1] + size[1] % 2)       # x264 wants even sizes
    font = ImageFont.load_default(size=18)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-", "-pix_fmt", "yuv420p",
                           "-c:v", "libx264", "-crf", "20", str(output)], stdin=subprocess.PIPE)
    t, n = float(start), 0
    while t <= end:
        factor = [f for at, f in speed if at <= t][-1]
        canvas = Image.new("RGB", size, (24, 24, 24))
        d = ImageDraw.Draw(canvas)
        clock = f"emulated t = {t / 1000:7.1f} s" + (f"  ({factor:g}x)" if factor != 1 else "")
        d.text((size[0] - 8, 4), clock, fill=(200, 200, 120), font=font, anchor="ra")
        for i, (label, times, paths) in enumerate(clips):
            x = (i % cols) * (W + GAP)
            y = BAR + (i // cols) * (BAR + H + GAP)
            d.text((x + 8, y + 4), label, fill="white", font=font)
            canvas.paste(_panel(_nearest(times, paths, t)), (x, y + BAR))
        ff.stdin.write(canvas.tobytes())
        n += 1
        t += 1000 / FPS * factor
    ff.stdin.close()
    if ff.wait():
        raise RuntimeError("ffmpeg failed")
    return (f"rendered {output}: {len(clips)} VMs, emulated {start / 1000:.1f}-{end / 1000:.1f} s, "
            f"{n / FPS:.1f} s of video")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--vm", nargs="+", action="append", required=True, metavar=("LABEL", "DIR"),
                    help="a label, then that VM's frame directories (several: one recording after another)")
    ap.add_argument("--speed", nargs="+", help="from_ms:factor ...")
    a = ap.parse_args()
    for v in a.vm:
        if len(v) < 2:
            ap.error("--vm takes a label and at least one directory")
    print(render_grid([(v[0], v[1:]) for v in a.vm], a.out, parse_speed(a.speed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
