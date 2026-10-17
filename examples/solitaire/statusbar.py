"""Read Solitaire's status bar ("Score: N Time: N") from a screenshot."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

BAR = (300, 410, 586, 424)
DIGITS = Path(__file__).resolve().parent / "lib" / "digits.json"


def glyphs(img: Image.Image) -> list[str]:
    """Glyphs left to right, each its ink columns joined into a string key."""
    bar = img.crop(BAR).convert("RGB")
    W, H = bar.size
    px = bar.load()
    cols = ["".join("1" if sum(px[x, y]) < 200 else "0" for y in range(H)) for x in range(W)]
    out, cur = [], []
    for c in cols + ["0" * H]:
        if "1" in c:
            cur.append(c)
        elif cur:
            out.append("|".join(cur))
            cur = []
    return out


def is_colon(g: str) -> bool:
    cols = g.split("|")
    return len(cols) <= 2 and all(c.count("1") <= 6 and "0110" in c or "010" in c for c in cols) and \
        sum(c.count("1") for c in cols) <= 8


def fields(img: Image.Image) -> tuple[list[str], list[str]] | None:
    """Glyphs of the score digits and of the time digits."""
    g = glyphs(img)
    colons = [i for i, x in enumerate(g) if is_colon(x)]
    if len(colons) < 2:
        return None
    c1, c2 = colons[-2], colons[-1]
    return g[c1 + 1:c2 - 4], g[c2 + 1:]


def read(img: Image.Image) -> tuple[int | None, int | None]:
    """(score, time); None for a field that doesn't parse."""
    table = json.loads(DIGITS.read_text()) if DIGITS.exists() else {}
    f = fields(img)
    if not f:
        return None, None

    def num(gs):
        try:
            return int("".join(table.get(x, "?") for x in gs))
        except ValueError:
            return None

    return num(f[0]), num(f[1])
