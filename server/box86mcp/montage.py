"""Several VMs' recordings in one video, on their shared emulated clock.

Each VM's frames come from record_start (PNGs named by emulated ms). Linked VMs step in lockstep, so
their clocks agree to within a quantum; each video frame shows, for every VM, its frame nearest that
emulated time, in a grid (two side by side, up to four 2x2, up to eight 4x2), with the clock on top.

    python -m box86mcp.montage OUT.mp4 --vm Alpha rec/p1 rec2/p1 --vm Bravo rec/p2 \\
        --speed 0:4 75000:2 143000:1 --audio split

Sound recorded with the frames (record_start(audio=True)) goes on the same clock: each video frame
carries exactly its stretch of emulated audio, at real speed; sped-up stretches are silent.
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


def frame_period(dirs) -> float:
    """The usual spacing of recorded frames (ms): the screen's refresh period when the recording
    interval is shorter, else the interval rounded up to whole refreshes."""
    import statistics

    times, _ = _frames(dirs)
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not gaps:
        return 1000 / FPS
    # Frames land on whole ms: average over a run of typical gaps for the fractional period.
    typical = statistics.median(gaps)
    near = [g for g in gaps if abs(g - typical) <= 1]
    return sum(near) / len(near)


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


def _audio_tracks(sources, audio: str | None, start: float, end: float):
    """One stereo track per VM that is heard, with its pan (-1 left .. 1 right), on the shared clock."""
    from . import audio as A

    if not audio or audio == "none":
        return []
    tracks = []
    for i, (label, dirs) in enumerate(sources):
        segs = [s for d in dirs for s in A.load(Path(d))]
        if not segs:
            continue
        if audio == "mix" or audio == "split" or audio == label or (audio == "first" and not tracks):
            pan = 0.0
            if audio == "split" and len(sources) > 1:
                cols = len(sources) if len(sources) <= 2 else 2 if len(sources) <= 4 else math.ceil(len(sources) / 2)
                pan = -1.0 + 2.0 * (i % cols) / (cols - 1) if cols > 1 else 0.0
            tracks.append((A.dc_block(A.mix(segs, start, end)), pan))
    return tracks


def render_grid(sources: list[tuple[str, list]], output: str | Path,
                speed: list[tuple[float, float]] | None = None, frame_ms: float = 1000 / FPS,
                audio: str | None = "first") -> str:
    """Render [(label, [frame dirs]), ...] as one video (.mp4). Returns a summary.

    frame_ms: emulated ms per video frame at real speed: the spacing of the recorded frames (see
    frame_period) gives one video frame per recorded frame, as smooth as the guest's screen.
    audio: "first" (the first VM that recorded sound), "mix" (all), "split" (each VM panned to its
    column: two VMs left and right), a VM's label, or "none"."""
    import tempfile

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from . import audio as A

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
    tracks = _audio_tracks(sources, audio, start, end)
    pieces = []
    tmp = tempfile.TemporaryDirectory()
    video = Path(tmp.name) / "video.mp4" if tracks else output
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{size[0]}x{size[1]}", "-r", f"{1000 / frame_ms:.6f}", "-i", "-", "-pix_fmt", "yuv420p",
                           "-c:v", "libx264", "-crf", "20", str(video)], stdin=subprocess.PIPE)
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
        if tracks:
            # This frame's stretch of emulated sound, at real speed only. Sample positions come from
            # the frame count, so the sound never drifts from the picture.
            per_frame = round((n + 1) * frame_ms * A.RATE / 1000) - round(n * frame_ms * A.RATE / 1000)
            chunk = np.zeros((per_frame, 2), np.float32)
            if factor == 1:
                at = round((t - start) * A.RATE / 1000)
                for x, pan in tracks:
                    part = x[at:at + per_frame]
                    if pan:
                        m = part.mean(axis=1)
                        part = np.stack([m * min(1.0, 1 - pan), m * min(1.0, 1 + pan)], axis=1)
                    chunk[:len(part)] += part
            pieces.append(chunk)
        n += 1
        t += frame_ms * factor
    ff.stdin.close()
    if ff.wait():
        raise RuntimeError("ffmpeg failed")
    heard = ""
    if tracks:
        wav = Path(tmp.name) / "audio.wav"
        A.write_wav(wav, A.fade_edges(np.concatenate(pieces)))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav), "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", str(output)], check=True)
        heard = f", sound ({audio})"
    tmp.cleanup()
    return (f"rendered {output}: {len(clips)} VMs, emulated {start / 1000:.1f}-{end / 1000:.1f} s, "
            f"{n * frame_ms / 1000:.1f} s of video{heard}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--vm", nargs="+", action="append", required=True, metavar=("LABEL", "DIR"),
                    help="a label, then that VM's frame directories (several: one recording after another)")
    ap.add_argument("--speed", nargs="+", help="from_ms:factor ...")
    ap.add_argument("--frame-ms", type=float, help="emulated ms per video frame (default: the recorded spacing)")
    ap.add_argument("--audio", default="first", help="first, mix, split, a VM label, or none")
    a = ap.parse_args()
    for v in a.vm:
        if len(v) < 2:
            ap.error("--vm takes a label and at least one directory")
    frame_ms = a.frame_ms or min(frame_period(v[1:]) for v in a.vm)
    print(render_grid([(v[0], v[1:]) for v in a.vm], a.out, parse_speed(a.speed), frame_ms, a.audio))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
