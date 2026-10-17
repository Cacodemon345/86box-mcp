"""A Windows 3.11 Solitaire bot driving 86Box through the automation socket.

It plays as fast as the emulated machine allows: Windows' pointer acceleration is off in this VM
(Control Panel > Mouse > Slow), so one mouse count is exactly one pixel and the bot can track the
pointer itself and jump straight to targets. Cards are recognised by their top-left corner (rank and
suit) against a labelled library in lib/.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from box86mcp.vm import VM  # noqa: E402

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"

# Screen geometry (640x480, Solitaire's window at its default place).
# All positions are a card's top-left black border pixel.
CARD_W, CARD_H = 71, 96
STOCK = (15, 46)
WASTE = (97, 46)
FOUND_X = [261, 343, 425, 507]
FOUND_Y = 46
COL_X = [15 + 82 * i for i in range(7)]
COL_Y = 149
DOWN_STEP, UP_STEP = 3, 15
RANK_BOX = (2, 3, 14, 14)   # rank glyph, relative to the border
SUIT_BOX = (2, 14, 14, 25)  # small suit symbol under it

RANKS = "A23456789TJQK"
SUITS = "SHDC"  # spades, hearts, diamonds, clubs
RED = set("HD")


def rank_of(card: str) -> int:
    return RANKS.index(card[0]) + 1


def red(card: str) -> bool:
    return card[1] in RED


class Machine:
    """The emulated PC: pointer tracking, fast input, screenshots, captions for the recording."""

    def __init__(self, vm: VM, calls_log: Path | None = None):
        self.vm = vm
        self.a = vm.auto
        self.px, self.py = 0, 0
        self.calls_log = calls_log

    def ms(self) -> int:
        return self.a.status()["ms"]

    def caption(self, text: str) -> None:
        if self.calls_log:
            ms = self.ms()
            with open(self.calls_log, "a") as f:
                f.write(json.dumps({"start_ms": ms, "end_ms": ms + 300, "tool": "mouse_drag",
                                    "caption": text}) + "\n")

    def note(self, text: str) -> None:
        if self.calls_log:
            ms = self.ms()
            with open(self.calls_log, "a") as f:
                f.write(json.dumps({"start_ms": ms, "end_ms": ms, "tool": "note", "caption": "NOTE: " + text}) + "\n")

    def step(self, ms: int) -> None:
        if ms > 0:
            self.a.step(ms)

    # 86Box hands motion to the guest in reports of at most ~255 counts, and Windows polls the mouse
    # every ~25 ms; anything sent faster queues up and cancels later moves. So: <=250 counts per
    # piece and 30 ms per piece.
    PIECE, PIECE_MS = 250, 30

    def home(self) -> None:
        for _ in range(3):  # 750 counts: more than the screen in either axis
            self.a.mouse(-250, -250, 0)
            self.step(self.PIECE_MS)
        self.step(60)
        self.px, self.py = 0, 0

    def move(self, x: int, y: int, buttons: int = 0) -> None:
        """Jump to (x, y): one relative move, delivered in PS/2-packet-sized pieces."""
        dx, dy = x - self.px, y - self.py
        while dx or dy:
            sx = max(-self.PIECE, min(self.PIECE, dx))
            sy = max(-self.PIECE, min(self.PIECE, dy))
            self.a.mouse(sx, sy, buttons)
            dx, dy = dx - sx, dy - sy
            self.step(self.PIECE_MS)
        self.px, self.py = x, y

    # Each button state must last longer than one mouse report (~25 ms) or Windows never sees it.
    BUTTON_MS = 35

    def click(self, x: int, y: int) -> None:
        self.move(x, y)
        self.a.mouse(0, 0, 1)
        self.step(self.BUTTON_MS)
        self.a.mouse(0, 0, 0)
        self.step(self.BUTTON_MS)

    def dclick(self, x: int, y: int) -> None:
        self.move(x, y)
        for _ in range(2):
            self.a.mouse(0, 0, 1)
            self.step(self.BUTTON_MS)
            self.a.mouse(0, 0, 0)
            self.step(self.BUTTON_MS)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.move(x1, y1)
        self.a.mouse(0, 0, 1)
        self.step(self.BUTTON_MS)
        self.move(x2, y2, buttons=1)
        self.step(self.BUTTON_MS)
        self.a.mouse(0, 0, 0)
        self.step(self.BUTTON_MS)

    def keys(self, *names: str) -> None:
        from box86mcp import keys as K

        for combo in names:
            codes = K.parse_combo(combo)
            self.a.keys(True, codes)
            self.step(40)
            self.a.keys(False, list(reversed(codes)))
            self.step(40)

    def screen(self) -> Image.Image:
        img = Image.open(io.BytesIO(self.a.screenshot()["png"]))
        img.load()
        return img.convert("RGB")


# Card recognition ---------------------------------------------------------------


def snap(img: Image.Image, x: int, y: int) -> tuple[int, int]:
    """Exact border of the card near (x, y): the black left edge and top edge."""
    def black_col(cx):
        return all(img.getpixel((cx, yy)) == (0, 0, 0) for yy in range(y + 20, y + 40))

    def black_row(ry):
        return all(img.getpixel((xx, ry)) == (0, 0, 0) for xx in range(x + 20, x + 40))

    # Piles get thicker as they grow (Solitaire offsets every few cards), and the top card is the
    # right-most and lowest of the borders.
    bx = max((cx for cx in range(x - 4, x + 6) if black_col(cx)), default=x)
    by = max((ry for ry in range(y - 4, y + 6) if black_row(ry)), default=y)
    return bx, by


def ink(img: Image.Image, x: int, y: int, box) -> bytes:
    """Binarised glyph: 1 where the pixel isn't white, plus whether any ink is red."""
    l, t, r_, b = box
    crop = img.crop((x + l, y + t, x + r_, y + b))
    bits = bytes(0 if p == (255, 255, 255) else 1 for p in crop.getdata())
    return bits


def is_red(img: Image.Image, x: int, y: int) -> bool:
    l, t, r_, b = RANK_BOX
    return any(p[0] > 150 and p[1] < 100 for p in img.crop((x + l, y + t, x + r_, y + b)).getdata())


def corner(img: Image.Image, x: int, y: int) -> Image.Image:
    return img.crop((x + RANK_BOX[0], y + RANK_BOX[1], x + SUIT_BOX[2], y + SUIT_BOX[3]))


def is_face_up(img: Image.Image, x: int, y: int) -> bool:
    """Face-up cards are white near the top-left; backs are patterned."""
    px = img.getpixel((x + 30, y + 6))
    return px == (255, 255, 255)


def _bits(im: Image.Image) -> list[int]:
    return [0 if p == (255, 255, 255) else 1 for p in im.getdata()]


def _red(im: Image.Image) -> bool:
    return any(p[0] > 150 and p[1] < 100 for p in im.getdata())


class Library:
    """Labelled corners. Exact matches first; unseen cards by nearest rank glyph and suit glyph."""

    RANK_ROWS, SUIT_ROWS = (0, 11), (11, 22)

    def __init__(self, path: Path = LIB):
        self.path = path
        self.exact: dict[bytes, str] = {}
        self.refs: list[tuple[str, Image.Image]] = []
        labels = path / "labels.json"
        if labels.exists():
            for h, name in json.loads(labels.read_text()).items():
                f = path / "unlabeled" / f"{h}.png"
                if name and f.exists():
                    im = Image.open(f).convert("RGB")
                    self.exact[im.tobytes()] = name
                    self.refs.append((name, im))

    def _part(self, im, rows):
        # Left 10 columns only: face cards' pictures start further right.
        return im.crop((0, rows[0], 10, rows[1]))

    def _dist(self, a: Image.Image, b: Image.Image) -> int:
        """Hamming distance of the ink, allowing a pixel of shift either way."""
        best = None
        A = a.load()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                B = b.load()
                d = 0
                for y in range(a.height):
                    for x in range(a.width):
                        xx, yy = x + dx, y + dy
                        pa = A[x, y] != (255, 255, 255)
                        pb = 0 <= xx < b.width and 0 <= yy < b.height and B[xx, yy] != (255, 255, 255)
                        d += pa != pb
                best = d if best is None else min(best, d)
        return best

    def identify(self, crop: Image.Image) -> str:
        name = self.exact.get(crop.tobytes())
        if name:
            return name
        isred = _red(crop.crop((0, 0, 10, crop.height)))
        r_im, s_im = self._part(crop, self.RANK_ROWS), self._part(crop, self.SUIT_ROWS)
        rank = min(self.refs, key=lambda nr: self._dist(r_im, self._part(nr[1], self.RANK_ROWS)))[0][0]
        same = [nr for nr in self.refs if (nr[0][1] in RED) == isred]
        suit = min(same, key=lambda nr: self._dist(s_im, self._part(nr[1], self.SUIT_ROWS)))[0][1]
        self.collect(crop)
        return rank + suit

    def collect(self, crop: Image.Image) -> str:
        """Save an unseen corner for labelling; returns its hash."""
        h = hashlib.sha256(crop.tobytes()).hexdigest()[:12]
        d = self.path / "unlabeled"
        d.mkdir(parents=True, exist_ok=True)
        if not (d / f"{h}.png").exists():
            crop.save(d / f"{h}.png")
        return h


# Setting up the session ---------------------------------------------------------


def boot(vm_dir: Path, frames: Path | None) -> Machine:
    vm = VM(vm_dir)
    vm.start(held=True)
    m = Machine(vm)
    if frames:
        frames.mkdir(parents=True, exist_ok=True)
        m.a.call(f"record start 50 {frames}")
    m.a.wait_text("C:\\>", 60000)
    for ch in "WIN\n":
        from box86mcp import keys as K

        code, _ = K.char_keys(ch)
        m.a.keys(True, [code])
        m.step(30)
        m.a.keys(False, [code])
        m.step(30)
    m.step(30000)
    m.home()
    # Games group, then Solitaire (positions on this VM's Program Manager).
    m.dclick(262, 326)
    m.step(1500)
    m.dclick(182, 200)
    m.step(3000)
    return m


def collect_mode(vm_dir: Path, deals: int) -> None:
    """Deal a few games, show every card (7 tops + the whole stock), save corners for labelling."""
    m = boot(vm_dir, None)
    lib = Library()
    seen = []
    for deal in range(deals):
        if deal:
            m.keys("alt+g", "d")
            m.step(1500)
        img = m.screen()
        img.save(LIB / f"deal{deal}.png")
        for i, x in enumerate(COL_X):
            y = COL_Y + DOWN_STEP * i
            seen.append(lib.collect(corner(img, *snap(img, x, y))))
        for _ in range(24):
            m.click(STOCK[0] + 35, STOCK[1] + 45)
            m.step(150)
            img = m.screen()
            seen.append(lib.collect(corner(img, *snap(img, *WASTE))))
    m.vm.stop()
    uniq = sorted(set(seen))
    print(f"{len(seen)} corners seen, {len(uniq)} distinct")
    make_sheet(uniq)


def make_sheet(hashes: list[str]) -> None:
    """Contact sheet of corners, scaled up 3x, numbered, for labelling by eye."""
    from PIL import ImageDraw

    cols = 13
    cw, ch = 13 * 3 + 10, 23 * 3 + 16
    sheet = Image.new("RGB", (cols * cw, ((len(hashes) + cols - 1) // cols) * ch), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, h in enumerate(hashes):
        im = Image.open(LIB / "unlabeled" / f"{h}.png").resize((39, 69), Image.NEAREST)
        x, y = (i % cols) * cw, (i // cols) * ch
        sheet.paste(im, (x + 5, y + 2))
        d.text((x + 5, y + 72), str(i), fill=(255, 255, 0))
    sheet.save(LIB / "sheet.png")
    (LIB / "sheet.json").write_text(json.dumps(hashes))
    print(f"sheet: {LIB / 'sheet.png'}")


if __name__ == "__main__":
    vm_dir = Path(os.path.expanduser("~/86box_mcp/.cache/solitaire/vm"))
    if sys.argv[1] == "collect":
        collect_mode(vm_dir, int(sys.argv[2]) if len(sys.argv) > 2 else 3)
