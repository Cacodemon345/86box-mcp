"""Play Draw One Solitaire for score: win as close to 30 s of game time as possible.

Windows 3.1 Solitaire's standard scoring gives a 700000 / seconds bonus for a win that takes 30 s or
more, so the score is dominated by finishing fast. The bot keeps a model of the table, recognises
only the cards it hasn't seen before (flipped tableau cards and waste draws), and checks the screen
after every move.

    python play.py [games] [--record DIR]
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from PIL import Image

import statusbar

from solbot import (COL_X, COL_Y, DOWN_STEP, FOUND_X, FOUND_Y, RANKS, RED, STOCK, UP_STEP, WASTE,
                    Library, Machine, boot, corner, snap)

OPP = {"S": "HD", "C": "HD", "H": "SC", "D": "SC"}


def rank(c: str) -> int:
    return RANKS.index(c[0]) + 1


def red(c: str) -> bool:
    return c[1] in RED


def stacks(card: str, onto: str) -> bool:
    return rank(onto) == rank(card) + 1 and red(onto) != red(card)


class Lost(Exception):
    pass


class Desync(Exception):
    pass


class Game:
    def __init__(self, m: Machine, lib: Library, log=print):
        self.m, self.lib, self.log = m, lib, log
        self.down = [i for i in range(7)]           # face-down counts
        self.up: list[list[str]] = [[] for _ in range(7)]
        self.stock: list[str | None] = [None] * 24  # top of the stock is the end
        self.waste: list[str] = []
        self.found = {s: 0 for s in "SHDC"}
        self.passes = 0
        self.progress = True  # anything played since the last recycle
        self.moves = 0
        self.t0 = None  # emulated ms of the first move
        self.pending = None  # a card a partial move just freed for its foundation

    debug: Path | None = None
    seq = 0

    def shot(self) -> Image.Image:
        img = self.m.screen()
        if self.debug:
            self.seq += 1
            img.save(self.debug / f"{self.seq:04d}.png")
        return img

    def say(self, text: str) -> None:
        self.m.caption(text)
        if self.debug:
            with open(self.debug / "log.txt", "a") as f:
                f.write(f"{self.seq:04d} {text}  | up={self.up} down={self.down} w={self.waste[-1:]} f={self.found}\n")

    # Screen -------------------------------------------------------------------

    def card_y(self, c: int, k: int) -> int:
        return COL_Y + DOWN_STEP * self.down[c] + UP_STEP * k

    def read(self, img: Image.Image, x: int, y: int) -> str:
        return self.lib.identify(corner(img, *snap(img, x, y)))

    def face_up(self, img: Image.Image, x: int, y: int) -> bool:
        px = [img.getpixel((x + 62 + i % 6, y + 2 + i // 6)) for i in range(12)]
        return all(p == (255, 255, 255) for p in px)

    def look_tops(self) -> None:
        img = self.shot()
        for c in range(7):
            self.up[c] = [self.read(img, COL_X[c], self.card_y(c, 0))]
        self.log("deal: " + " ".join(self.up[c][0] for c in range(7)))

    def verify(self, cols=(), waste=False) -> Image.Image:
        """Check the model against the screen, giving Windows a few frames to finish redrawing."""
        for attempt in range(15):
            img = self.shot()
            bad = None
            for c in cols:
                if self.up[c]:
                    got = self.read(img, COL_X[c], self.card_y(c, len(self.up[c]) - 1))
                    if got != self.up[c][-1]:
                        bad = f"column {c + 1}: expected {self.up[c][-1]}, screen shows {got}"
            if waste and self.waste:
                got = self.read(img, *WASTE)
                if got != self.waste[-1]:
                    bad = f"waste: expected {self.waste[-1]}, screen shows {got}"
            if not bad:
                return img
            self.m.step(30)
        raise Desync(bad)

    # Actions --------------------------------------------------------------------

    def started(self):
        if self.t0 is None:
            self.t0 = self.m.ms()
        self.moves += 1

    def flip_if_needed(self, c: int) -> None:
        if self.up[c] or not self.down[c]:
            return
        self.down[c] -= 1
        y = self.card_y(c, 0)
        img = self.shot()
        if not self.face_up(img, COL_X[c], y):
            self.m.click(COL_X[c] + 35 + self.jitter(), y + 75)
            for _ in range(15):
                img = self.shot()
                if self.face_up(img, COL_X[c], y):
                    break
                self.m.step(30)
            else:
                raise Desync(f"column {c + 1} didn't turn over")
        card = self.read(img, COL_X[c], y)
        self.up[c] = [card]
        self.say(f"turn over {card}")

    # Two presses within a few pixels and half a second make a double-click, which sends a card to
    # its foundation. So successive presses alternate between two points well apart.
    jit = 0

    def jitter(self) -> int:
        self.jit ^= 1
        return 14 if self.jit else -14

    def grab_point(self, src) -> tuple[int, int]:
        if src == "w":
            return WASTE[0] + 35 + self.jitter(), WASTE[1] + 40
        c, k = src
        last = k == len(self.up[c]) - 1
        return COL_X[c] + 35 + self.jitter(), self.card_y(c, k) + (40 if last else 7)

    def to_found(self, src) -> None:
        self.started()
        x, y = self.grab_point(src)
        if src == "w":
            card = self.waste.pop()
        else:
            card = self.up[src[0]].pop()
        self.say(f"{card} to foundation")
        self.m.dclick(x, y)
        self.found[card[1]] += 1
        self.progress = True
        if src == "w":
            self.verify(waste=True)
        else:
            self.verify(cols=[src[0]])
            self.flip_if_needed(src[0])

    def to_col(self, src, dst: int) -> None:
        self.started()
        x, y = self.grab_point(src)
        dx = x - (WASTE[0] if src == "w" else COL_X[src[0]])
        if self.up[dst]:
            tx, ty = COL_X[dst] + dx, self.card_y(dst, len(self.up[dst])) + (y - self.grab_top(src))
        else:
            tx, ty = COL_X[dst] + dx, COL_Y + (y - self.grab_top(src))
        if src == "w":
            cards = [self.waste.pop()]
        else:
            c, k = src
            cards = self.up[c][k:]
            del self.up[c][k:]
        self.say(f"{cards[0]} to column {dst + 1}")
        self.m.drag(x, y, tx, ty)
        # A drop onto an empty column lands at the column's base even if it had face-down cards.
        self.up[dst] += cards
        self.progress = True
        if src == "w":
            self.verify(cols=[dst], waste=True)
        else:
            self.verify(cols=[dst, src[0]])
            self.flip_if_needed(src[0])

    def grab_top(self, src) -> int:
        if src == "w":
            return WASTE[1]
        return self.card_y(src[0], src[1])

    def draw(self) -> None:
        self.started()
        if not self.stock:
            self.say("turn the waste over")
            self.stock = list(reversed(self.waste))
            self.waste = []
            self.passes += 1
            self.progress = False
            self.m.click(STOCK[0] + 35 + self.jitter(), STOCK[1] + 45)
            return
        self.m.click(STOCK[0] + 35 + self.jitter(), STOCK[1] + 45)
        known = self.stock.pop()
        prev = self.waste[-1] if self.waste else None
        for _ in range(15):
            img = self.shot()
            if img.getpixel((WASTE[0] + 35, WASTE[1] + 40)) != (0, 170, 85):
                card = self.read(img, *WASTE)
                if card != prev and (known is None or card == known):
                    break
            self.m.step(30)
        else:
            raise Desync(f"draw: waste still shows {prev}")
        self.waste.append(card)
        self.say(f"draw {card}")

    # Strategy -------------------------------------------------------------------

    def can_found(self, card: str) -> bool:
        return self.found[card[1]] == rank(card) - 1

    def safe_found(self, card: str) -> bool:
        r = rank(card)
        return self.can_found(card) and (r <= 2 or all(self.found[s] >= r - 1 for s in OPP[card[1]]))

    def endgame(self) -> bool:
        return not any(self.down) and not self.stock and not self.waste

    def won(self) -> bool:
        return all(v == 13 for v in self.found.values())

    def choose(self):
        tops = [(c, len(self.up[c]) - 1) for c in range(7) if self.up[c]]
        empties = [c for c in range(7) if not self.up[c] and not self.down[c]]
        wtop = self.waste[-1] if self.waste else None
        if self.pending:
            card, self.pending = self.pending, None
            for c, k in tops:
                if self.up[c][k] == card and self.can_found(card):
                    return ("found", (c, k))
        if self.endgame() or (not any(self.down)):
            # Everything is face up: play anything that goes up.
            for c, k in tops:
                if self.can_found(self.up[c][k]):
                    return ("found", (c, k))
            if wtop and self.can_found(wtop):
                return ("found", "w")
        # Safe foundation plays.
        for c, k in tops:
            if self.safe_found(self.up[c][k]):
                return ("found", (c, k))
        if wtop and self.safe_found(wtop):
            if rank(wtop) > 2:
                for d in range(7):
                    if self.up[d] and stacks(wtop, self.up[d][-1]):
                        return ("col", "w", d)  # 5 points now and 10 later, not 10
            return ("found", "w")
        # Moves that turn over a face-down card, from the tallest hidden pile first.
        for c in sorted(range(7), key=lambda i: -self.down[i]):
            if not self.down[c] or not self.up[c]:
                continue
            base = self.up[c][0]
            for d in range(7):
                if d != c and self.up[d] and stacks(base, self.up[d][-1]):
                    return ("col", (c, 0), d)
            if rank(base) == 13 and empties:
                return ("col", (c, 0), empties[0])
            if len(self.up[c]) == 1 and self.can_found(base):
                return ("found", (c, 0))
        # The waste card onto the tableau.
        if wtop:
            for d in range(7):
                if self.up[d] and stacks(wtop, self.up[d][-1]):
                    return ("col", "w", d)
            if rank(wtop) == 13 and empties:
                return ("col", "w", empties[0])
            if self.can_found(wtop):
                return ("found", "w")
        # Clear a column for a king that's waiting on top of face-down cards (or on the waste).
        kings_waiting = (wtop and rank(wtop) == 13) or any(
            self.down[c] and self.up[c] and rank(self.up[c][0]) == 13 for c in range(7))
        if kings_waiting and not empties:
            for c in range(7):
                if self.down[c] or not self.up[c] or rank(self.up[c][0]) == 13:
                    continue
                for d in range(7):
                    if d != c and self.up[d] and stacks(self.up[c][0], self.up[d][-1]):
                        return ("col", (c, 0), d)
        # Uncover a place for the waste card: move the cards sitting on one that would take it.
        if wtop:
            for c in range(7):
                for k in range(len(self.up[c]) - 1):
                    if not stacks(wtop, self.up[c][k]):
                        continue
                    for d in range(7):
                        if d != c and self.up[d] and stacks(self.up[c][k + 1], self.up[d][-1]):
                            return ("col", (c, k + 1), d)
        # Partial moves that let a card go up to its foundation.
        for c in range(7):
            for k in range(1, len(self.up[c])):
                if not self.can_found(self.up[c][k - 1]):
                    continue
                for d in range(7):
                    if d != c and self.up[d] and stacks(self.up[c][k], self.up[d][-1]):
                        self.pending = self.up[c][k - 1]  # play it up next, or this move undoes itself
                        return ("col", (c, k), d)
        # The stock.
        if self.stock:
            return ("draw",)
        if self.waste and (self.progress or self.passes == 0) and self.passes < 6:
            return ("draw",)
        # Stuck: unsafe foundation plays, if they help anything at all.
        for c, k in tops:
            if self.can_found(self.up[c][k]):
                return ("found", (c, k))
        return None

    def play(self, target_s: float = 30.4) -> bool:
        self.look_tops()
        while not self.won():
            # The last card: wait until the clock has passed 30 s so the time bonus applies.
            # (Solitaire's own clock, read off the status bar: it doesn't keep pace with real time.)
            if sum(self.found.values()) == 51:
                waited = False
                misreads = 0
                while True:
                    _, t = statusbar.read(self.m.screen())
                    if t is not None and t >= 30:
                        break
                    if t is None:  # caught mid-redraw; a few in a row means something's wrong
                        misreads += 1
                        if misreads > 50:
                            break
                    waited = True
                    self.m.step(100)
                if waited:
                    self.say("wait for 30 s on the clock: the time bonus starts there")
            a = self.choose()
            if self.moves > 400:
                raise Lost(f"gave up after {self.moves} moves (going round in circles?)")
            if a is None:
                raise Lost(f"stuck after {self.moves} moves, {sum(self.found.values())} cards up")
            if a[0] == "found":
                self.to_found(a[1])
            elif a[0] == "col":
                self.to_col(a[1], a[2])
            else:
                self.draw()
        return True


def score_digits(img: Image.Image) -> Image.Image:
    return img.crop((420, 409, 590, 425))


def main():
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else \
        Path(__file__).resolve().parents[2] / ".cache" / "solitaire" / "games"
    out.mkdir(parents=True, exist_ok=True)
    import os

    vm_dir = Path(os.path.expanduser("~/86box_mcp/.cache/solitaire/vm"))
    m = boot(vm_dir, None)
    lib = Library()
    results = []
    after_win = False
    t_end = time.time() + 60 * float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else None
    for g in range(games):
        if t_end and time.time() > t_end:
            break
        if sum(1 for r in results if (r.get("score") or 0) >= 24000) >= 2:
            break
        gdir = out / f"g{int(time.time())}"
        frames = gdir / "frames"
        frames.mkdir(parents=True)
        m.calls_log = gdir / "calls.jsonl"
        m.a.call(f"record start 50 {frames}")
        m.step(200)
        if g and after_win:
            m.keys("enter")  # Deal Again? Yes
            m.step(800)
        elif g:
            m.keys("alt+g", "d")
            m.step(800)
        game = Game(m, lib)
        if "--debug" in sys.argv:
            game.debug = gdir / "debug"
            game.debug.mkdir()
        status = "?"
        try:
            game.play()
            status = "won"
            m.step(1500)
        except Lost as e:
            status = f"lost: {e}"
        except Desync as e:
            status = f"desync: {e}"
        after_win = status == "won"
        if after_win:
            final_score = statusbar.read(m.screen())
            m.keys("esc")  # stop the cascade
            m.step(1000)
        img = m.screen()
        img.save(gdir / "final.png")
        score, clock = statusbar.read(img)
        if after_win and final_score[0] is not None:
            score = max(score or 0, final_score[0])
        score_digits(img).resize((680, 64), Image.NEAREST).save(gdir / "status.png")
        m.step(500)
        m.a.call("record stop")
        took = (m.ms() - game.t0) / 1000 if game.t0 else 0
        r = {"dir": str(gdir), "status": status, "moves": game.moves, "passes": game.passes,
             "up": sum(game.found.values()), "seconds": round(took, 2), "score": score, "clock": clock}
        print(json.dumps(r), flush=True)
        results.append(r)
        (gdir / "result.json").write_text(json.dumps(r))
        if status != "won":
            shutil.rmtree(frames)
    m.vm.stop()


if __name__ == "__main__":
    main()
