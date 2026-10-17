"""Movement and planning on top of skibot.py (exec both into the skictl namespace)."""

SOLID = (9, 10, 11, 12, 13)   # burnt tree, moguls, lift pole, tree, rock: things that stop you
MOVERS = (1, 2, 3, 4)         # other skiers, dogs, snowboarders, lift chairs


def stopped(p=None):
    p = p or player()
    return p["vx"] == 0 and p["vy"] == 0 and p["alt"] == 0


def recover(limit=200):
    """Wait out a crash (frames 11 and 17 ignore keys), then stand still, side-on."""
    for _ in range(limit):
        p = player()
        if p["frame"] not in (11, 17) and stopped(p):
            break
        if p["frame"] not in (11, 17) and p["alt"] == 0 and p["frame"] not in (3, 6):
            key("kp7")
        tick()
    if player()["frame"] not in (3, 6):
        key("kp7")
        tick()


def stop(limit=60):
    for _ in range(limit):
        p = player()
        if stopped(p) and p["frame"] in (3, 6, 12):
            return True
        if p["frame"] not in (3, 6, 11, 17) and p["alt"] == 0:
            key("kp7" if p["vx"] <= 0 else "kp9")
        tick()
    return False


def walk_to(x, y, tol_x=3, tol_y=6, limit=600):
    """From a standstill: step uphill (8) or sideways (4/6) until near (x, y). Uphill only."""
    for _ in range(limit):
        p = player()
        dx, dy = x - p["x"], y - p["y"]
        if abs(dx) <= tol_x and dy >= -tol_y:
            return True
        if p["frame"] in (11, 17):
            recover()
            continue
        if p["vx"] == 0 and p["vy"] == 0:
            if abs(dx) > tol_x:
                # Sideways steps need the stopped pose facing that way: 3 (left) or 6 (right).
                face = 3 if dx < 0 else 6
                if p["frame"] != face:
                    key("kp7" if dx < 0 else "kp9")
                else:
                    key("kp4" if dx < 0 else "kp6")
            elif dy < -tol_y:
                if p["frame"] not in (3, 6, 7, 8, 12):
                    key("kp7")
                else:
                    key("kp8")
        tick()
    return False


def danger_ahead(p, lane_x, look=90):
    for o in objects():
        if o["type"] in SOLID + MOVERS:
            if abs(o["x"] - lane_x) < 26 and 0 < o["y"] - p["y"] < look:
                return o
    return None


def ski_step(p, lane_x):
    """One tick of downhill skiing along lane_x, sidestepping anything in the way."""
    o = danger_ahead(p, p["x"])
    if o is not None:
        want = 1 if o["x"] >= p["x"] else 4
    else:
        dx = lane_x - p["x"]
        want = 0 if abs(dx) <= 6 else (1 if dx < 0 else 4)
    if p["frame"] != want and p["alt"] == 0 and p["frame"] not in (11, 17):
        key({0: "kp2", 1: "kp1", 4: "kp3"}[want])
