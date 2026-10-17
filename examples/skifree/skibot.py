"""SkiFree 1.0 (16-bit) state reader and controls, loaded into the skictl namespace.

Addresses are physical, found by diffing RAM (they hold while SkiFree's data segment stays put;
find() re-checks them). The actor layout is the decompilation's Actor struct with 16-bit ints
and near pointers: 46 bytes, x at +32, y at +34, height above the snow at +36.

1.0's actor types are one less than the 32-bit 1.04's: 10 moguls, 11 lift pole, 12 tree,
13 rock, 14 bump, 15 ramp, 16 sign/flags.
"""

import struct

SCORE = 0x493B7E      # int16 style points
PLAYER_Y = 0x493B7A   # int16, mirrors the player actor
TICKS = 0x493B12      # uint32 GetTickCount() at the last game update
ACT = 0x4980D4        # actor table; [0] is the player
NACT = 33
NAMES = ["next", "linked", "actorPtr", "perm", "sprite", "spritePtr", "type", "frame",
         "sr0", "sr1", "sr2", "sr3", "r0", "r1", "r2", "r3",
         "x", "y", "alt", "vx", "vy", "air", "flags"]
TYPES = {10: "moguls", 11: "pole", 12: "tree", 13: "rock", 14: "bump", 15: "ramp", 16: "sign"}


def score():
    return struct.unpack("<h", m.a.memr(SCORE, 2))[0]


def ticks():
    return struct.unpack("<I", m.a.memr(TICKS, 4))[0]


def actors():
    raw = m.a.memr(ACT, 46 * NACT)
    out = []
    for k in range(NACT):
        d = dict(zip(NAMES, struct.unpack_from("<23h", raw, k * 46)))
        d["k"] = k
        out.append(d)
    return out


def player():
    return dict(zip(NAMES, struct.unpack("<23h", m.a.memr(ACT, 46))))


DS = 0x493000          # SkiFree's data segment; actor pointers are offsets into it
HEAD = 0x493B7C        # actorListPtr


def live():
    """The game's live actor list (each drawn object appears with a linked redraw copy)."""
    off = struct.unpack("<H", m.a.memr(HEAD, 2))[0]
    out, seen = [], set()
    while off and off not in seen and len(out) < 80:
        seen.add(off)
        d = dict(zip(NAMES, struct.unpack("<23h", m.a.memr(DS + off, 46))))
        d["off"] = off
        out.append(d)
        off = d["next"] & 0xFFFF
    return out


def objects():
    """Distinct non-player objects on the slope."""
    seen, out = set(), []
    for d in live():
        if d["type"] == 0 or not d["sprite"]:
            continue
        key = (d["type"], d["sprite"], d["x"], d["y"])
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def tick(max_ms=400):
    """Advance emulated time to just after the next game update.

    If the game stops updating, a modal box is up (High Scores at the Freestyle finish):
    press OK and carry on.
    """
    t = ticks()
    for _ in range(max_ms // 5):
        m.step(5)
        if ticks() != t:
            return True
    m.caption("press OK on the dialog")
    key("enter")
    m.step(300)
    return False


def key(name):
    """One key press, kept short so it lands within the current tick."""
    codes = K.parse_combo(name)
    m.a.keys(True, codes)
    m.step(2)
    m.a.keys(False, codes)
    m.step(2)


# Numpad poses on the ground: 2 straight down (frame 0), 1/3 diagonal (frames 1/4),
# 7/9 across the slope (frames 3/6, which stop you).
def steer(target_x, p=None, tol=12):
    p = p or player()
    dx = target_x - p["x"]
    want = 0 if abs(dx) <= tol else (1 if dx < 0 else 4)
    if p["frame"] != want and p["alt"] == 0 and p["frame"] not in (11, 17):
        key({0: "kp2", 1: "kp1", 4: "kp3"}[want])


SPR = DS + 0x4CBC     # sprite table: 12-byte records, width at +6, height at +8
_sprite_cache = {}


def sprite(i):
    """(width, height) of sprite i."""
    if i not in _sprite_cache:
        v = struct.unpack("<6h", m.a.memr(SPR + 12 * i, 12))
        _sprite_cache[i] = (v[3], v[4])
    return _sprite_cache[i]
