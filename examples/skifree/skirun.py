"""Freestyle score run: hop in place in the +8 pose, drifting down the course and back up.

exec after skibot.py and skiplan.py. Style points are a signed 16-bit int in SkiFree 1.0, so the
top is 32767; one more point wraps to -32768.

Why it works (from the decompiled 1.04 source, checked against 1.0 in memory):
- every game tick the skier spends in the air in pose 0x14 adds 8 style points, 0x12 adds 4;
- a hop (Insert) works from a standstill and lasts four ticks; landing in pose 0xe puts you
  back on your feet standing still (pose 3), with no fall;
- in the air you accelerate downhill, so plain hops drift down the slope. An uphill step
  (8, with the skier standing still) in the same tick as the hop flies the hop uphill instead.
"""

MAX = 32767
FS_TOP, FS_BOTTOM = 640, 0x4100   # Freestyle ends if you go above the start or past the end


def blocked(p, uphill, margin=6):
    """Something solid in the next few hops' path (they drift about 10 units each)."""
    for o in objects():
        if o["type"] not in SOLID + MOVERS:
            continue
        w = (sprite(o["sprite"])[0] + 16) // 2 + margin
        ahead = (p["y"] - 80 < o["y"] < p["y"] + 10) if uphill else (p["y"] - 10 < o["y"] < p["y"] + 120)
        if abs(o["x"] - p["x"]) < w and ahead:
            return o
    return None


def sidestep(o, p):
    """Walk sideways (each step is +1 style point) until clear of o."""
    go_left = o["x"] >= p["x"]
    for _ in range(60):
        p = player()
        if not blocked(p, True) and not blocked(p, False):
            return
        if p["frame"] in (11, 17):
            recover()
            continue
        if p["vx"] == 0 and p["vy"] == 0:
            face = 3 if go_left else 6
            key(("kp7" if go_left else "kp9") if p["frame"] != face else ("kp4" if go_left else "kp6"))
        tick()


def hop(uphill, poses=None):
    """One hop from a standstill. poses: list of per-air-tick poses (0x14, 0x12 or 0xd)."""
    p = player()
    if p["frame"] in (11, 17):
        recover()
        return
    while not (p["vx"] == 0 and p["vy"] == 0):
        if p["frame"] not in (3, 6):
            key("kp7")
        tick()
        p = player()
        if p["frame"] in (11, 17):
            recover()
            return
    if p["frame"] not in (3, 6, 7, 8, 12):
        key("kp7")
    if uphill:
        key("kp8")
    key("insert")
    key("kp4")            # 0xd -> 0xe
    key("kp8")            # 0xe -> 0x14 (+8 a tick)
    prev = 0
    for i in range(8):
        tick()
        p = player()
        if p["alt"] == 0:
            break
        if p["alt"] < prev and p["air"] <= -1:
            key("kp2")    # 0x14 -> 0xe: land standing
        prev = p["alt"]


def grind(target, uphill, log_every=200):
    n = 0
    while score() < target:
        p = player()
        if uphill and p["y"] < FS_TOP + 120:
            return "top"
        if not uphill and p["y"] > FS_BOTTOM - 400:
            return "bottom"
        o = blocked(p, uphill)
        if o is not None:
            m.caption(f"sidestep a {TYPES.get(o['type'], o['type'])}")
            sidestep(o, p)
        hop(uphill)
        n += 1
        if n % log_every == 0:
            print(f"  {score():6d}  y={player()['y']}", flush=True)
            progress()
    return "target"


def step_point():
    """One sideways step: exactly +1 style point."""
    stop()
    p = player()
    face = 3 if p["frame"] != 6 else 6
    if p["frame"] != face:
        key("kp7")
        tick()
    s0 = score()
    key("kp4" if face == 3 else "kp6")
    for _ in range(10):
        tick()
        if player()["vx"] == 0:
            break
    return score() - s0


def finish_exact(target=MAX):
    """From within a few dozen points: helicopters (+8) then single steps (+1)."""
    m.caption(f"fine-tuning to exactly {target}")
    for _ in range(200):
        r = target - score()
        if r <= 0:
            break
        if r >= 8:
            hop_pose(["kp4", "kp4"], "helicopter")
        else:
            step_point()
    return score()


# Per-tick poses in a hop. Back to 0xd from each pose, then on to the next.
TO_D = {13: [], 14: ["kp6"], 15: ["kp4"], 16: ["kp6", "kp6"], 18: ["kp2"], 19: ["kp8"], 20: ["kp6"], 21: ["kp4"]}
FROM_D = {0: [], 2: ["kp4", "kp4"], 4: ["kp8"], 8: ["kp4", "kp8"]}


def hop_custom(points):
    """Hop from a standstill scoring points[i] on air tick i (each 0, 2, 4 or 8)."""
    stop()
    s0 = score()
    key("insert")
    for k in FROM_D[points[0]]:
        key(k)
    i = 0
    for _ in range(10):
        tick()
        p = player()
        if p["alt"] == 0:
            break
        i += 1
        if i < len(points):
            for k in TO_D.get(p["frame"], []):
                key(k)
            for k in FROM_D[points[i]]:
                key(k)
    for _ in range(4):
        tick()
    return score() - s0


def plan_hops(r):
    """Split an even remainder into hops of four ticks of 8/4/2/0."""
    hops = []
    while r > 0:
        h = []
        for _ in range(4):
            v = 8 if r >= 8 else (4 if r >= 4 else (2 if r >= 2 else 0))
            h.append(v)
            r -= v
        hops.append(h)
    return hops


PTS_PER_UNIT_DOWN = 0.8     # downhill hopping: about 5 points a tick for 6 units a tick
FINISH_Y = FS_BOTTOM - 250  # where the exact finish happens, short of the line


def phase_b(target=MAX - 60):
    """Hop uphill until the rest can be earned hopping back down to FINISH_Y."""
    n = 0
    while True:
        p = player()
        need = target - score()
        if need <= (FINISH_Y - p["y"]) * PTS_PER_UNIT_DOWN or p["y"] < FS_TOP + 150:
            return "switch"
        o = blocked(p, True)
        if o is not None:
            sidestep(o, p)
        hop(True)
        n += 1
        if n % 150 == 0:
            print(f"  B {score():6d}  y={player()['y']}", flush=True)
            progress()


def phase_c(target=MAX - 60):
    """Hop downhill to within a few dozen points of the target."""
    while score() < target:
        p = player()
        if p["y"] > FINISH_Y + 150:
            return "too far"
        o = blocked(p, False)
        if o is not None:
            sidestep(o, p)
        hop(False)
    return "close"


def hold_grind(target=MAX - 60, hold_y=16450):
    """Hop in place near hold_y: uphill hops when below it, downhill when above."""
    n = 0
    while score() < target:
        p = player()
        up = p["y"] > hold_y
        o = blocked(p, up)
        if o is not None:
            sidestep(o, p)
        hop(up)
        n += 1
        if n % 100 == 0:
            print(f"  H {score():6d} y={player()['y']}", flush=True)
