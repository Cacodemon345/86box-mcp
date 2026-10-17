"""PC/AT keyboard: key names and characters to 86Box scancodes.

86Box takes set-1 make codes; extended (E0-prefixed) keys are 0x100 | code.
"""

from __future__ import annotations

KEYS: dict[str, int] = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07,
    "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B, "minus": 0x0C, "equals": 0x0D,
    "backspace": 0x0E, "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13,
    "t": 0x14, "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "lbracket": 0x1A, "rbracket": 0x1B, "enter": 0x1C, "ctrl": 0x1D, "lctrl": 0x1D,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23, "j": 0x24,
    "k": 0x25, "l": 0x26, "semicolon": 0x27, "quote": 0x28, "backquote": 0x29,
    "shift": 0x2A, "lshift": 0x2A, "backslash": 0x2B, "z": 0x2C, "x": 0x2D,
    "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32, "comma": 0x33,
    "period": 0x34, "slash": 0x35, "rshift": 0x36, "kp_multiply": 0x37,
    "alt": 0x38, "lalt": 0x38, "space": 0x39, "capslock": 0x3A,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "numlock": 0x45,
    "scrolllock": 0x46, "kp7": 0x47, "kp8": 0x48, "kp9": 0x49, "kp_minus": 0x4A,
    "kp4": 0x4B, "kp5": 0x4C, "kp6": 0x4D, "kp_plus": 0x4E, "kp1": 0x4F,
    "kp2": 0x50, "kp3": 0x51, "kp0": 0x52, "kp_period": 0x53, "f11": 0x57,
    "f12": 0x58,
    # Extended keys.
    "kp_enter": 0x11C, "rctrl": 0x11D, "kp_divide": 0x135, "ralt": 0x138,
    "home": 0x147, "up": 0x148, "pageup": 0x149, "left": 0x14B, "right": 0x14D,
    "end": 0x14F, "down": 0x150, "pagedown": 0x151, "insert": 0x152,
    "delete": 0x153,
}

ALIASES = {
    "escape": "esc", "return": "enter", "control": "ctrl", "bksp": "backspace",
    "pgup": "pageup", "pgdn": "pagedown", "ins": "insert", "del": "delete",
    "-": "minus", "=": "equals", "[": "lbracket", "]": "rbracket", ";": "semicolon",
    "'": "quote", "`": "backquote", "\\": "backslash", ",": "comma", ".": "period",
    "/": "slash",
}

_UNSHIFTED = "1234567890-=qwertyuiop[]asdfghjkl;'`\\zxcvbnm,./"
_SHIFTED = '!@#$%^&*()_+QWERTYUIOP{}ASDFGHJKL:"~|ZXCVBNM<>?'


def scancode(name: str) -> int:
    """Scancode for a key name ("a", "enter", "f1", "up", "0x1c")."""
    key = name.strip().lower()
    if key.startswith("0x"):
        return int(key, 16)
    key = ALIASES.get(key, key)
    if key not in KEYS:
        raise ValueError(f"unknown key {name!r}; known keys: {', '.join(sorted(KEYS))}")
    return KEYS[key]


def char_keys(ch: str) -> tuple[int, bool]:
    """(scancode, needs_shift) for a printable character, tab or newline."""
    if ch == "\n" or ch == "\r":
        return KEYS["enter"], False
    if ch == "\t":
        return KEYS["tab"], False
    if ch == " ":
        return KEYS["space"], False
    if ch in _UNSHIFTED:
        return scancode(ALIASES.get(ch, ch)), False
    if ch in _SHIFTED:
        base = _UNSHIFTED[_SHIFTED.index(ch)]
        return scancode(ALIASES.get(base, base)), True
    raise ValueError(f"cannot type character {ch!r}")


def parse_combo(combo: str) -> list[int]:
    """'ctrl+alt+delete' -> scancodes in press order."""
    return [scancode(part) for part in combo.split("+") if part]
