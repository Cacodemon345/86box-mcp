"""Helpers loaded into the skictl namespace: exec(open("skilib.py").read())."""

import tempfile

import numpy as np

TMP = tempfile.gettempdir()


def press(name, hold=40):
    codes = K.parse_combo(name)
    m.a.keys(True, codes)
    m.step(hold)
    m.a.keys(False, codes)
    m.step(10)


def shot(name="s"):
    p = f"{TMP}/{name}.png"
    m.screen().save(p)
    return p


def snap():
    """All 16 MB of guest RAM."""
    return np.frombuffer(b"".join(m.a.memr(a, 65536) for a in range(0, 16 << 20, 65536)), dtype=np.uint8)


def word_at(buf, off):
    return int(np.frombuffer(buf[off:off + 2].tobytes(), "<i2")[0])


def words_equal(buf, value):
    """Byte offsets where a signed 16-bit little-endian word equals value (any alignment)."""
    lo, hi = np.uint8(value & 0xFF), np.uint8((value >> 8) & 0xFF)
    return np.nonzero((buf[:-1] == lo) & (buf[1:] == hi))[0]


def launch():
    """Program Manager > File > Run > C:\\SKI\\SKI.EXE, then maximize."""
    m.keys("alt+f")
    m.step(300)
    m.keys("r")
    m.step(800)
    type_text(m, "C:\\SKI\\SKI.EXE\n")
    m.step(4000)
    m.dclick(330, 13)
    m.step(1500)
