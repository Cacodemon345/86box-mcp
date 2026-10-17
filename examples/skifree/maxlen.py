"""Maximum-length run: turbo all the way, planning lanes a screen ahead.

y is a 16-bit short: past 32767 it wraps to -32768. The yeti zones are y > 32000 (bottom yeti)
and y < -2000 (top yeti), so surviving the wrap means outrunning both until y > -2000 again.
"""

AVOID = (1, 2, 3, 9, 10, 11, 12, 13)    # skiers, dogs, snowboarders, burnt trees, moguls, poles, trees, rocks
YETI = (5, 6, 7, 8)


def lane_blocked(x, p, objs, look=360, pad=8):
    for o in objs:
        if o["type"] in YETI and -60 < o["y"] - p["y"] < look:
            if abs(o["x"] - x) < 120:
                return True
        if o["type"] in AVOID and 0 < o["y"] - p["y"] < look:
            w = (sprite(o["sprite"])[0] + 16) // 2 + pad
            if abs(o["x"] - x) < w:
                return True
    return False


def plan_lane(p, objs):
    """Nearest clear lane, nudged towards x=0 so the side yetis never wake."""
    best = None
    # The bottom yeti waits at x=0, y=32060: come past it well to one side.
    home = 500 if 26000 < p["y"] < 32767 else 0
    for dx in sorted(range(-200, 201, 6), key=lambda d: abs(d) + (abs(p["x"] + d - home) // 100)):
        if not lane_blocked(p["x"] + dx, p, objs):
            best = p["x"] + dx
            break
    return best if best is not None else p["x"]


def ski_fast(p, objs):
    lane = plan_lane(p, objs)
    dx = lane - p["x"]
    want = 0 if abs(dx) <= 4 else (1 if dx < 0 else 4)
    if p["frame"] != want and p["alt"] == 0 and p["frame"] not in (11, 17):
        key({0: "kp2", 1: "kp1", 4: "kp3"}[want])
    return lane


def eaten(p, objs):
    return any(o["type"] in YETI and abs(o["x"] - p["x"]) < 20 and abs(o["y"] - p["y"]) < 20 for o in objs) \
        and p["vy"] == 0 and p["vx"] == 0


state = {"wraps": 0, "prev_y": 0, "ticks": 0, "crashes": 0, "yeti": False}


def run_max(n):
    for _ in range(n):
        tick()
        state["ticks"] += 1
        p = player()
        objs = objects()
        if state["prev_y"] > 30000 and p["y"] < -30000:
            state["wraps"] += 1
            m.caption(f"distance wrapped round! y {state['prev_y']} -> {p['y']}")
            print("  WRAP at tick", state["ticks"], flush=True)
        state["prev_y"] = p["y"]
        if any(o["type"] in YETI for o in objs) and not state["yeti"]:
            state["yeti"] = True
            m.caption("the Abominable Snowman: keep going, turbo is faster")
            print("  yeti at y", p["y"], flush=True)
        if eaten(p, objs):
            return "eaten"
        if p["frame"] in (11, 17):
            state["crashes"] += 1
            recover()
            key("kp2")
            continue
        ski_fast(p, objs)
    return "running"
