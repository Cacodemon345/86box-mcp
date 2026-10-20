"""Sound recorded by 86Box's automation socket: load it, put it on the emulated clock, check it.

`audio start <dir>` makes 86Box write one WAV per sound stream (main mix, music/OPL and PC speaker,
CD, floppy, ...), each at its own rate. record_stop saves 86Box's description of them as sound.json:
a segment's frame n is at start_ms + n * 1000 / rate of emulated time. The polled streams are
sample-exact ("exact"); the threaded ones (CD, floppy, hard disk, MIDI synths) are as good as the
moment each block arrived.
"""

from __future__ import annotations

import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 48000
SILENT = 0          # a stream whose every sample is 0 is left out


@dataclass
class Segment:
    stream: str
    path: Path
    rate: int
    frames: int
    start_ms: float
    exact: bool
    drift_frames: float | None = None


def save_description(directory: Path, reply: dict) -> list[Segment]:
    """Write 86Box's `audio stop` reply as sound.json, dropping all-silent files. Returns the rest."""
    kept = []
    for s in reply.get("streams", []):
        p = directory / s["file"]
        if not p.exists():
            continue
        if _peak(p) <= SILENT:
            p.unlink()
            continue
        kept.append(s)
    desc = {"start_ms": reply.get("start_ms"), "stop_ms": reply.get("ms"), "streams": kept}
    (directory / "sound.json").write_text(json.dumps(desc, indent=1))
    return load(directory)


def load(directory: Path) -> list[Segment]:
    f = Path(directory) / "sound.json"
    if not f.exists():
        return []
    d = json.loads(f.read_text())
    return [Segment(s["name"], Path(directory) / s["file"], s["rate"], s["frames"], s["start_ms"], s["exact"],
                    s.get("drift_frames")) for s in d["streams"]]


def _peak(path: Path) -> int:
    with wave.open(str(path)) as w:
        peak = 0
        while True:
            b = w.readframes(1 << 16)
            if not b:
                return peak
            peak = max(peak, int(np.abs(np.frombuffer(b, dtype=np.int16).astype(np.int32)).max()))


def read(seg: Segment, rate: int = RATE) -> np.ndarray:
    """A segment as float32 stereo at `rate` (ffmpeg resamples), shape (frames, 2), -1..1."""
    if seg.rate == rate:
        with wave.open(str(seg.path)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return (x.reshape(-1, 2) / 32768.0).astype(np.float32)
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(seg.path), "-af", f"aresample={rate}",
                          "-f", "f32le", "-ac", "2", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, 2)


def mix(segments: list[Segment], start_ms: float, end_ms: float, rate: int = RATE,
        streams: set[str] | None = None) -> np.ndarray:
    """Every segment (or those of `streams`) mixed onto one clock from start_ms to end_ms."""
    n = max(0, round((end_ms - start_ms) * rate / 1000))
    out = np.zeros((n, 2), np.float32)
    for seg in segments:
        if streams and seg.stream not in streams:
            continue
        x = read(seg, rate)
        at = round((seg.start_ms - start_ms) * rate / 1000)
        lo, hi = max(0, at), min(n, at + len(x))
        if hi > lo:
            out[lo:hi] += x[lo - at:hi - at]
    return np.clip(out, -1.0, 1.0)


def dc_block(x: np.ndarray, rate: int = RATE, window_ms: float = 50.0) -> np.ndarray:
    """Remove DC, as the coupling capacitor on a real card's output does: a sound card holding its
    last sample after playback otherwise sits at a constant level, and every start and stop of the
    held level is a click. Subtracts a centred moving average (linear phase), so tones above about
    20 Hz pass unchanged."""
    n = max(1, int(rate * window_ms / 1000)) | 1
    if len(x) < n:
        return x - x.mean(axis=0)
    pad = np.pad(x, ((n // 2, n // 2), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0, dtype=np.float64)
    c = np.concatenate([np.zeros((1, x.shape[1])), c])
    avg = (c[n:] - c[:-n]) / n
    return (x - avg[:len(x)]).astype(np.float32)


def fade_edges(x: np.ndarray, rate: int = RATE, ms: float = 5.0) -> np.ndarray:
    """A short fade in and out, so a recording cut where the sound isn't at zero doesn't click."""
    n = min(len(x) // 2, int(rate * ms / 1000))
    if n:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        x = x.copy()
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def write_wav(path: Path, x: np.ndarray, rate: int = RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def to_db(v: float) -> float:
    return 20 * np.log10(v) if v > 0 else -120.0


@dataclass
class Gap:
    at_ms: float
    length_ms: float

    def __str__(self) -> str:
        return f"dropout at {self.at_ms / 1000:.3f} s, {self.length_ms:.1f} ms"


def find_gaps(x: np.ndarray, start_ms: float, rate: int = RATE, quiet_db: float = -70.0,
              loud_db: float = -45.0, min_gap_ms: float = 4.0) -> list[Gap]:
    """Dropouts: near-silence of at least min_gap_ms inside sustained sound (45 of the 50 ms on
    each side), measured as AC level, so a DC level a sound card holds isn't sound. A click or a
    short sound before silence is a program starting its sound, not a gap."""
    mono = x.mean(axis=1) if x.ndim == 2 else x
    win = max(1, round(rate / 1000))                    # 1 ms windows
    nwin = len(mono) // win
    if nwin == 0:
        return []
    db = 20 * np.log10(np.maximum(mono[:nwin * win].reshape(nwin, win).std(axis=1), 1e-9))
    quiet, loud = db < quiet_db, db > loud_db
    gaps: list[Gap] = []
    i = 0
    while i < nwin:
        if quiet[i]:
            j = i
            while j < nwin and quiet[j]:
                j += 1
            if (j - i >= min_gap_ms and i >= 50 and loud[i - 50:i].sum() >= 45
                    and loud[j:j + 50].sum() >= 45):
                gaps.append(Gap(start_ms + i, j - i))
            i = j
        else:
            i += 1
    return gaps


def drift_ok(seg: Segment) -> bool:
    """A polled stream's sample count against the emulated clock: within 2 frames plus 50 ppm (the
    1 ms quantum counter and the sound timers round differently)."""
    if seg.drift_frames is None:
        return True
    return abs(seg.drift_frames) <= 2 + 50e-6 * seg.frames


def lag_ms(a: np.ndarray, b: np.ndarray, rate: int = RATE, max_ms: float = 500.0) -> float:
    """How far b lags a (ms; negative: b leads), by cross-correlation, to a fraction of a sample."""
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    size = 1 << (2 * n - 1).bit_length()
    c = np.fft.irfft(np.conj(np.fft.rfft(a, size)) * np.fft.rfft(b, size), size)
    m = int(max_ms * rate / 1000)
    c = np.concatenate([c[-m:], c[:m + 1]])
    k = int(np.argmax(c))
    frac = 0.0
    if 0 < k < len(c) - 1:                               # parabolic peak interpolation
        y0, y1, y2 = c[k - 1], c[k], c[k + 1]
        d = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / d if d else 0.0
    return (k - m + frac) * 1000 / rate


def spectrogram(x: np.ndarray, rate: int = RATE, width: int = 800, height: int = 300, max_hz: float = 8000,
                title: str = ""):
    """A PIL image: time across, frequency up (log magnitude)."""
    from PIL import Image, ImageDraw

    mono = x.mean(axis=1) if x.ndim == 2 else x
    nfft = 1024
    hop = max(1, (len(mono) - nfft) // width) if len(mono) > nfft else 1
    cols = []
    win = np.hanning(nfft)
    for k in range(0, max(1, len(mono) - nfft), hop):
        seg = mono[k:k + nfft]
        if len(seg) < nfft:
            seg = np.pad(seg, (0, nfft - len(seg)))
        cols.append(np.abs(np.fft.rfft(seg * win)))
    if not cols:
        cols = [np.zeros(nfft // 2 + 1)]
    s = np.array(cols).T
    s = s[: int(max_hz / (rate / nfft))]
    s = 20 * np.log10(np.maximum(s, 1e-6))
    s = np.clip((s + 100) / 100, 0, 1)
    img = Image.fromarray((s[::-1] * 255).astype(np.uint8), "L").resize((width, height)).convert("RGB")
    d = ImageDraw.Draw(img)
    for hz in (1000, 2000, 4000, 6000):
        if hz < max_hz:
            y = height - int(hz / max_hz * height)
            d.line([(0, y), (6, y)], fill=(255, 255, 0))
            d.text((8, y - 6), f"{hz // 1000} kHz", fill=(255, 255, 0))
    if title:
        d.text((width - 6, 4), title, fill=(255, 255, 255), anchor="ra")
    return img


def dominant_hz(x: np.ndarray, rate: int = RATE, count: int = 3) -> list[tuple[float, float]]:
    """The strongest frequencies, [(hz, dB relative to the strongest)], DC removed."""
    mono = x.mean(axis=1) if x.ndim == 2 else x
    mono = mono - mono.mean()
    if len(mono) < 64:
        return []
    f = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    hz = np.fft.rfftfreq(len(mono), 1 / rate)
    out = []
    f2 = f.copy()
    for _ in range(count):
        k = int(np.argmax(f2))
        if f2[k] <= 0:
            break
        out.append((float(hz[k]), to_db(f2[k] / f.max())))
        lo, hi = max(0, k - 20), k + 21
        f2[lo:hi] = 0                                   # skip the peak's neighbours
    return out
