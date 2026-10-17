"""Arkanoid II (DOS, EGA) bot, loaded into the skictl namespace: exec(open(".../arkbot.py").read()).

Addresses are physical, found by diffing guest RAM while the game runs on this VM:
- paddle x: byte at 0x23358 (8 at the left wall, 186 at the right);
- object records: 33 bytes each from 0x237DF. Words: x, y (8.8 fixed point), dx, dy (1/256 units
  per step). Balls are the records that move with sensible velocities; stale ones stay still.
The field is about x 10.6..208.8 for the ball; the ball meets the paddle at y ~180, and a centred
hit has ball x - paddle x ~ 14. The game moves things every ~14 ms (vsync), several steps a frame.
"""

import struct

PADDLE_X = 0x23358
OBJ = 0x237DF
OBJ_SIZE = 33
NOBJ = 8
X_MIN, X_MAX = 10.6, 208.8
PADDLE_Y = 179.5
CENTRE = 14          # ball x - paddle x for a centred hit
PADDLE_MIN, PADDLE_MAX = 8, 186

_prev = {}
held = [None]


def paddle():
    return m.a.memr(PADDLE_X, 1)[0]


def objects():
    raw = m.a.memr(OBJ, OBJ_SIZE * NOBJ)
    out = []
    for k in range(NOBJ):
        x, y, dx, dy = struct.unpack_from("<HHhh", raw, k * OBJ_SIZE)
        out.append({"k": k, "x": x / 256, "y": y / 256, "dx": dx, "dy": dy})
    return out


_moved_at = {}


def balls():
    """Records that look like moving balls. Stale records don't move; live ones move every frame
    (~14 ms), so a record counts if it moved within the last 50 ms of emulated time."""
    out = []
    now = m.ms()
    for o in objects():
        if _prev.get(o["k"]) not in (None, (o["x"], o["y"])):
            _moved_at[o["k"]] = now
        _prev[o["k"]] = (o["x"], o["y"])
        moved = now - _moved_at.get(o["k"], -10**9) <= 50
        # Only the first three records are ball slots; the rest hold other kinds of object.
        if o["k"] < 3 and moved and 0 < abs(o["dx"]) < 1200 and 0 < abs(o["dy"]) < 1200 and 4 < o["y"] < 190 \
                and X_MIN - 4 <= o["x"] <= X_MAX + 4:
            out.append(o)
    return out


def landing_x(b):
    """Where ball b reaches the paddle line, bouncing off the side walls (bricks ignored)."""
    x, y, dx, dy = b["x"], b["y"], b["dx"] / 256, b["dy"] / 256
    if dy <= 0:
        return None
    steps = (PADDLE_Y - y) / dy
    x += dx * steps
    width = X_MAX - X_MIN
    # Unfold reflections.
    u = (x - X_MIN) % (2 * width)
    return X_MIN + (u if u <= width else 2 * width - u)


def go(direction):
    if held[0] == direction:
        return
    if held[0]:
        m.a.keys(False, K.parse_combo(held[0]))
    if direction:
        m.a.keys(True, K.parse_combo(direction))
    held[0] = direction


def fire():
    """Tap fire: shoots the laser when the paddle has one, and releases a caught ball. The game
    keeps only the last key event, so releasing fire stops the paddle even with a direction key
    still held (injected keys don't auto-repeat); press the held key again afterwards."""
    press("space", hold=10)
    if held[0]:
        m.a.keys(True, K.parse_combo(held[0]))


def steer(target_paddle, tol=1.5):
    p = paddle()
    target = max(PADDLE_MIN, min(PADDLE_MAX, target_paddle))
    # The game reads the keypad codes; the extended arrow keys (E0-prefixed) don't move the paddle.
    go("kp6" if p < target - tol else ("kp4" if p > target + tol else None))


def play(ms, aim=0, log=False):
    """Keep the paddle under the most urgent falling ball for ms of emulated time."""
    t0 = m.ms()
    idle = 0
    events = []
    while m.ms() - t0 < ms:
        m.step(7)
        bs = balls()
        falling = [b for b in bs if b["dy"] > 0]
        if falling:
            b = min(falling, key=lambda b: (PADDLE_Y - b["y"]) / max(b["dy"], 1))
            steer(landing_x(b) - CENTRE + aim)
            idle = 0
        elif bs:
            b = max(bs, key=lambda b: b["y"])
            steer(b["x"] - CENTRE)
            idle = 0
        else:
            go(None)
            idle += 1
            if idle in (60, 300):          # nothing moving for a while: launch
                press("space")
                events.append(("launch", m.ms() - t0))
    go(None)
    return events


def movable():
    """True once the paddle responds to the keys (it's frozen during intros and banners)."""
    p0 = paddle()
    k = "kp4" if p0 > 100 else "kp6"
    c = K.parse_combo(k)
    m.a.keys(True, c)
    m.step(120)
    m.a.keys(False, c)
    m.step(20)
    return paddle() != p0


def player_controls():
    """True when we, not the demo, are moving the paddle: still with no keys held, and moving when
    a key is pressed. (The attract-mode demo moves it by itself; intros and banners freeze it.)"""
    go(None)
    p0 = paddle()
    m.step(500)
    if paddle() != p0:
        return False
    return movable()


def start_game(limit_s=120):
    """From anywhere outside a game: Space through the title and story until we control the paddle."""
    for i in range(limit_s // 2):
        if player_controls():
            return True
        if i % 2 == 0:
            press("space")
        m.step(1500)
    return False


def play_logged(ms, keep=60):
    """play(), but return the frames before each lost ball."""
    from collections import deque
    t0 = m.ms()
    hist = deque(maxlen=keep)
    losses = []
    idle = 0
    while m.ms() - t0 < ms:
        m.step(7)
        bs = balls()
        p = paddle()
        falling = [b for b in bs if b["dy"] > 0]
        target = None
        if falling:
            b = min(falling, key=lambda b: (PADDLE_Y - b["y"]) / max(b["dy"], 1))
            target = landing_x(b) - CENTRE
            steer(target)
            idle = 0
        elif bs:
            b = max(bs, key=lambda b: b["y"])
            steer(b["x"] - CENTRE)
            idle = 0
        else:
            go(None)
            idle += 1
            if idle in (60, 300):
                press("space")
        hist.append((m.ms() - t0, p, target and round(target, 1),
                     [(b["k"], round(b["x"], 1), round(b["y"], 1), b["dx"], b["dy"]) for b in bs]))
        if any(b["y"] > 183 and b["dy"] > 0 for b in bs):
            losses.append(list(hist))
            hist.clear()
    go(None)
    return losses


# Offsets from a centred hit (ball x - paddle x = CENTRE) that send the ball off at different
# angles: about -12 is shallow left, -4 steep left, +1 steep right, +10 shallow right.
AIMS = [-12, -9, -6, -3, 0, 3, 6, 10]


def play_sweep(ms, start=0):
    """play(), aiming each bounce at the next angle in AIMS so the ball sweeps the field."""
    t0 = m.ms()
    idle = 0
    turn = start
    prev = {}
    while m.ms() - t0 < ms:
        m.step(7)
        bs = balls()
        for b in bs:
            q = prev.get(b["k"])
            if q and q["dy"] > 0 and b["dy"] < 0 and b["y"] > 165:
                turn += 1
            prev[b["k"]] = b
        falling = [b for b in bs if b["dy"] > 0]
        if falling:
            b = min(falling, key=lambda b: (PADDLE_Y - b["y"]) / max(b["dy"], 1))
            steer(landing_x(b) - CENTRE - AIMS[turn % len(AIMS)])
            idle = 0
        elif bs:
            steer(max(bs, key=lambda b: b["y"])["x"] - CENTRE)
            idle = 0
        else:
            go(None)
            idle += 1
            if idle in (60, 300):
                press("space")
    go(None)
    return turn


SCORE = 0x233DD   # a buffer the game formats numbers into for display: usually the player's score
_last_score = [None]


def score():
    """The player's score as last drawn. The buffer sometimes holds the high score instead, so
    that value is skipped (keeping the last other one)."""
    s = m.a.memr(SCORE, 8).split(b"\0")[0].decode("ascii", "replace").strip()
    if s.isdigit() and int(s) != 797320:
        _last_score[0] = int(s)
    return _last_score[0]


def capsules():
    """Falling capsules: records past the ball slots that drop straight down."""
    now = m.ms()
    out = []
    for o in objects():
        if o["k"] < 3:
            continue
        if _prev.get(o["k"]) not in (None, (o["x"], o["y"])):
            _moved_at[o["k"]] = now
        _prev[o["k"]] = (o["x"], o["y"])
        if now - _moved_at.get(o["k"], -10**9) <= 50 and o["dx"] == 0 and 0 < o["dy"] < 1200 \
                and 20 < o["y"] < 186 and X_MIN - 4 <= o["x"] <= X_MAX + 4:
            out.append(o)
    return out


def play_full(ms, start=0, log_caps=False):
    """Sweep-aiming play that also catches capsules while the ball is heading up and high."""
    t0 = m.ms()
    idle = 0
    turn = start
    prev = {}
    caught = 0
    while m.ms() - t0 < ms:
        m.step(7)
        bs = balls()
        cs = capsules()
        for b in bs:
            q = prev.get(b["k"])
            if q and q["dy"] > 0 and b["dy"] < 0 and b["y"] > 165:
                turn += 1
            prev[b["k"]] = b
        falling = [b for b in bs if b["dy"] > 0]
        urgent = falling and min((PADDLE_Y - b["y"]) / max(b["dy"] / 256, 0.1) for b in falling) < 70
        if cs and not urgent and (not bs or all(b["dy"] < 0 or b["y"] < 110 for b in bs)):
            c = max(cs, key=lambda c: c["y"])
            steer(c["x"] - CENTRE + 2)
        elif falling:
            b = min(falling, key=lambda b: (PADDLE_Y - b["y"]) / max(b["dy"], 1))
            steer(landing_x(b) - CENTRE - AIMS[turn % len(AIMS)])
            idle = 0
        elif bs:
            steer(max(bs, key=lambda b: b["y"])["x"] - CENTRE)
            idle = 0
        else:
            go(None)
            idle += 1
            if idle in (60, 300):
                press("space")
    go(None)
    return turn


LIVES = 0x2CA6A   # probably the lives left (went 3 -> 2 -> 0 at game over)


def safe_aim(aim, b):
    """Shrink the aiming offset for fast balls: aiming uses the paddle's edges, and a fast ball
    leaves no room for error there."""
    speed = max(abs(b["dx"]), abs(b["dy"]))
    limit = 3 if speed > 300 else 5
    return max(-limit, min(limit, aim))


def play_logged_full(ms, logfile, keep=120):
    """play_full(), writing the frames before each lost ball to logfile (JSON lines)."""
    import json
    from collections import deque
    t0 = m.ms()
    hist = deque(maxlen=keep)
    idle = 0
    turn = 0
    prev = {}
    losses = 0
    n = 0
    with open(logfile, "a") as log:
        while m.ms() - t0 < ms:
            m.step(7)
            n += 1
            if n % 25 == 0:
                fire()
            bs = balls()
            cs = capsules()
            p = paddle()
            for b in bs:
                q = prev.get(b["k"])
                if q and q["dy"] > 0 and b["dy"] < 0 and b["y"] > 165:
                    turn += 1
                prev[b["k"]] = b
            falling = [b for b in bs if b["dy"] > 0]
            urgent = falling and min((PADDLE_Y - b["y"]) / max(b["dy"] / 256, 0.1) for b in falling) < 70
            target = None
            mode = ""
            if cs and not urgent and (not bs or all(b["dy"] < 0 or b["y"] < 110 for b in bs)):
                c = max(cs, key=lambda c: c["y"])
                target = c["x"] - CENTRE + 2
                mode = "capsule"
            elif falling:
                b = min(falling, key=lambda b: (PADDLE_Y - b["y"]) / max(b["dy"], 1))
                target = landing_x(b) - CENTRE - safe_aim(AIMS[turn % len(AIMS)], b)
                mode = "ball"
            elif bs:
                target = max(bs, key=lambda b: b["y"])["x"] - CENTRE
                mode = "shadow"
            if target is not None:
                steer(target)
                if idle >= 60:
                    hist.clear()
                idle = 0
            else:
                go(None)
                idle += 1
                if idle == 60:
                    losses += 1
                    log.write(json.dumps({"t": m.ms(), "score": score(), "lives": m.a.memr(LIVES, 1)[0],
                                          "frames": list(hist)}) + "\n")
                    log.flush()
                if idle in (60, 300):
                    press("space")
            hist.append((m.ms(), p, mode, target and round(target, 1),
                         [(b["k"], round(b["x"], 1), round(b["y"], 1), b["dx"], b["dy"]) for b in bs],
                         [(c["k"], round(c["x"], 1), round(c["y"], 1)) for c in cs]))
    go(None)
    return losses
