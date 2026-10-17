"""Technical run: collect every positive Freestyle scoring event (Microsoft KB Q100019).

exec after skibot.py, skiplan.py and skirun.py.
"""

got = {}   # event -> points seen


def scored(event, before):
    d = score() - before
    got.setdefault(event, []).append(d)
    m.caption(f"{event}: {d:+d}")
    print(f"  {event}: {d:+d} (style {score()})", flush=True)


def hop_pose(keys_in_air, label):
    """Hop from a standstill, press keys_in_air once airborne, land standing."""
    stop()
    s0 = score()
    key("insert")
    for k in keys_in_air:
        key(k)
    prev = 0
    for _ in range(8):
        tick()
        p = player()
        if p["alt"] == 0:
            break
        prev = p["alt"]
    for _ in range(6):
        tick()
    scored(label, s0)


def tricks():
    m.caption("tricks from a standstill")
    hop_pose([], "plain air")
    hop_pose(["kp4", "kp4"], "helicopter (0x10)")
    hop_pose(["kp8"], "head-over-heels flip (0x12)")
    hop_pose(["kp4", "kp8"], "backscratcher (0x14)")


def click_jump():
    m.a.mouse(0, 0, 1)
    m.step(2)
    m.a.mouse(0, 0, 0)
    m.step(2)


def nearest(types, sprites=None, below=(-200, 400), p=None):
    p = p or player()
    c = [o for o in objects() if o["type"] in types and (sprites is None or o["sprite"] in sprites)
         and below[0] < o["y"] - p["y"] < below[1]]
    return min(c, key=lambda o: abs(o["y"] - p["y"]) + abs(o["x"] - p["x"])) if c else None


def mushroom(stump):
    """Walk uphill into a stump (it becomes a magic mushroom), then jump over it in turbo."""
    m.caption("a stump: walk uphill into it to grow a magic mushroom")
    stop()
    p = player()
    if p["y"] < stump["y"] + 30:            # get below it first
        return False
    walk_to(stump["x"], stump["y"] + 20)
    for _ in range(30):                      # step uphill into it
        p = player()
        if p["vy"] == 0 and p["vx"] == 0:
            key("kp8")
        tick()
        if any(o["sprite"] == 86 and abs(o["x"] - stump["x"]) < 4 for o in objects()):
            break
    mush = [o for o in objects() if o["sprite"] == 86]
    if not mush:
        print("  no mushroom", flush=True)
        return False
    mu = mush[0]
    m.caption("magic mushroom! now jump over it")
    # Go round it and stand 30 above, lined up.
    stop()
    p = player()
    side = mu["x"] + (30 if p["x"] >= mu["x"] else -30)
    walk_to(side, p["y"])
    walk_to(side, mu["y"] - 40)
    walk_to(mu["x"], mu["y"] - 40)
    key("f")                                 # turbo: jumps go twice as high
    s0 = score()
    m.move(318, 420)                         # pointer below the skier, so clicks reach the game
    key("kp2")
    for _ in range(40):
        tick()
        p = player()
        if p["alt"] == 0 and 0 < mu["y"] - p["y"] <= 2 * p["vy"] + 6:
            click_jump()
        if p["y"] > mu["y"] + 30 and p["alt"] == 0:
            break
    key("f")
    m.move(330, 13)                          # park the pointer on the title bar again
    scored("jump over a magic mushroom", s0)
    return True


def intercept(o, limit=200):
    """Steer into a moving skier, snowboarder or dog."""
    s0 = score()
    for _ in range(limit):
        tick()
        p = player()
        if p["frame"] in (11, 17):
            break
        cur = [a for a in live() if a["off"] == o["off"]]
        if not cur:
            break
        t = cur[0]
        dx, dy = t["x"] - p["x"], t["y"] - p["y"]
        if dy < -30:
            break
        want = 0 if abs(dx) <= 6 else (1 if dx < 0 else 4)
        if p["frame"] != want and p["alt"] == 0:
            key({0: "kp2", 1: "kp1", 4: "kp3"}[want])
        if score() != s0:
            break
    return score() - s0


def burning_tree(ramp, tree):
    """Hop into a normal tree (it catches fire), then fly over it off the ramp above."""
    m.caption("a ramp with a tree below it: set the tree on fire")
    stop()
    walk_to(tree["x"], tree["y"] - 30)
    s0 = score()
    key("kp2")
    for _ in range(40):
        tick()
        p = player()
        if p["frame"] in (11, 17):
            break
        if p["alt"] == 0 and 0 < tree["y"] - p["y"] <= p["vy"] + 4:
            key("insert")
    scored("hop into a tree: it catches fire", s0)
    if not any(o["type"] == 9 for o in objects()):
        return False
    recover()
    m.caption("walk back up above the ramp")
    walk_to(ramp["x"], ramp["y"] - 120)
    s0 = score()
    key("kp2")
    flew = False
    for _ in range(120):
        tick()
        p = player()
        if p["alt"] > 0 and p["air"] >= 8 and not flew:
            flew = True
            key("kp4")
            key("kp8")
        if flew and p["alt"] == 0:
            break
        if not flew and p["frame"] in (11, 17):
            break
    for _ in range(10):
        tick()
    scored("fly over the burning tree", s0)
    return True


def pair(p):
    """A ramp below us with a normal tree lined up under it, both inside the live window."""
    for r in objects():
        if r["type"] != 15 or not (150 < r["y"] - p["y"] < 400):
            continue
        for t in objects():
            if t["type"] == 12 and t["sprite"] == 50 and abs(t["x"] - r["x"]) <= 10 and 60 < t["y"] - r["y"] < 260:
                return r, t
    return None


def wait_for_snowboarder(sb, limit=150):
    """Stand in a snowboarder's path and let it run into us."""
    m.caption("snowboarder coming: stand in its way")
    stop()
    s0 = score()
    for _ in range(limit):
        cur = [a for a in live() if a["off"] == sb["off"]]
        p = player()
        if not cur or cur[0]["y"] > p["y"] + 40:
            break
        t = cur[0]
        if p["vx"] == 0 and p["vy"] == 0 and abs(t["x"] - p["x"]) > 6:
            dx = t["x"] - p["x"]
            face = 3 if dx < 0 else 6
            key(("kp7" if dx < 0 else "kp9") if p["frame"] != face else ("kp4" if dx < 0 else "kp6"))
        tick()
        if score() != s0:
            break
    return score() - s0


def hunt(n, done):
    """Ski down taking chances at the events not yet in done. Returns why it stopped."""
    key("kp2")
    for _ in range(n):
        tick()
        p = player()
        if p["y"] > FS_BOTTOM - 500:
            return "end"
        if p["frame"] in (11, 17):
            recover()
            key("kp2")
            continue
        if "make a snowboarder crash" not in done:
            sb = [o for o in live() if o["type"] == 3 and -300 < o["y"] - p["y"] < -40 and abs(o["x"] - p["x"]) < 200]
            if sb:
                d = wait_for_snowboarder(sb[0])
                got.setdefault("make a snowboarder crash", []).append(d)
                m.caption(f"make a snowboarder crash: {d:+d}")
                print("  snowboarder", d, flush=True)
                if d == 20:
                    done.add("make a snowboarder crash")
                recover()
                key("kp2")
                continue
        if "fly over the burning tree" not in done:
            pr = pair(p)
            if pr:
                burning_tree(*pr)
                if got.get("fly over the burning tree", [0])[-1] >= 1000:
                    done.add("fly over the burning tree")
                recover()
                key("kp2")
                continue
        if "jump over a magic mushroom" not in done:
            st_ = nearest((13,), (46,), (150, 380), p)
            if st_:
                mushroom(st_)
                if got.get("jump over a magic mushroom", [0])[-1] >= 100:
                    done.add("jump over a magic mushroom")
                recover()
                key("kp2")
                continue
        ski_step(p, p["x"])
    return "ticks"
