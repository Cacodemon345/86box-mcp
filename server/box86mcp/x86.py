"""Protected-mode structures: segment descriptors, gates and page tables, decoded from raw bytes.

Pure functions over a `read(phys_addr, length) -> bytes` callable, so they can be tested without a VM.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable

Read = Callable[[int, int], bytes]

CR0_PE = 1 << 0
CR0_PG = 1 << 31
CR4_PSE = 1 << 4
CR4_PAE = 1 << 5
EFLAGS_VM = 1 << 17

_SYSTEM_TYPES = {
    0x1: "TSS16 available", 0x2: "LDT", 0x3: "TSS16 busy", 0x4: "call gate16", 0x5: "task gate",
    0x6: "interrupt gate16", 0x7: "trap gate16", 0x9: "TSS32 available", 0xB: "TSS32 busy",
    0xC: "call gate32", 0xE: "interrupt gate32", 0xF: "trap gate32",
}
_GATES = {0x4, 0x5, 0x6, 0x7, 0xC, 0xE, 0xF}


@dataclass
class Descriptor:
    raw: bytes
    present: bool
    dpl: int
    system: bool            # S bit clear: a system segment or gate
    type: int
    kind: str
    base: int = 0
    limit: int = 0          # in bytes, after granularity
    flags: list[str] = field(default_factory=list)
    selector: int | None = None     # gates
    offset: int | None = None       # gates

    def __str__(self) -> str:
        if not any(self.raw):
            return "null"
        p = "" if self.present else "not present, "
        if self.selector is not None:
            target = f"{self.selector:04x}" if self.kind == "task gate" else f"{self.selector:04x}:{self.offset:08x}"
            extra = f", {', '.join(self.flags)}" if self.flags else ""
            return f"{self.kind} -> {target}, {p}DPL {self.dpl}{extra}"
        fl = f", {' '.join(self.flags)}" if self.flags else ""
        return f"{self.kind}, base {self.base:08x}, limit {self.limit:08x}, {p}DPL {self.dpl}{fl}"


def decode_descriptor(raw: bytes) -> Descriptor:
    """An 8-byte GDT, LDT or IDT entry."""
    w0, w1, b4, access, b6, b7 = struct.unpack("<HHBBBB", raw[:8])
    present, dpl, system, typ = bool(access & 0x80), (access >> 5) & 3, not access & 0x10, access & 0xF
    if system and typ in _GATES:
        d = Descriptor(raw, present, dpl, True, typ, _SYSTEM_TYPES[typ], selector=w1)
        d.offset = w0 | ((b7 << 24 | b6 << 16) if typ & 0x8 else 0)
        if typ in (0x4, 0xC):
            d.flags.append(f"{b4 & 0x1F} params")
        if typ == 0x5:
            d.offset = None
        return d
    base = w1 | b4 << 16 | b7 << 24
    limit = w0 | (b6 & 0xF) << 16
    flags = []
    if b6 & 0x80:
        limit = limit << 12 | 0xFFF
        flags.append("4K")
    if system:
        kind = _SYSTEM_TYPES.get(typ, f"reserved system type {typ:x}")
    elif typ & 0x8:
        kind = "code"
        flags += ["32-bit" if b6 & 0x40 else "16-bit"]
        flags += (["conforming"] if typ & 0x4 else []) + (["readable"] if typ & 0x2 else ["execute-only"])
    else:
        kind = "data"
        flags += ["big" if b6 & 0x40 else "16-bit"]
        flags += (["expand-down"] if typ & 0x4 else []) + (["writable"] if typ & 0x2 else ["read-only"])
    if not system and typ & 0x1:
        flags.append("accessed")
    if b6 & 0x10:
        flags.append("AVL")
    return Descriptor(raw, present, dpl, system, typ, kind, base, limit, flags)


@dataclass
class Walk:
    linear: int
    physical: int | None            # None: not present
    steps: list[str]

    def __str__(self) -> str:
        where = f"physical {self.physical:#x}" if self.physical is not None else "not mapped"
        return f"linear {self.linear:#010x} -> {where}" + "".join(f"\n  {s}" for s in self.steps)


def _pte_flags(e: int) -> str:
    names = [(0x1, "P"), (0x2, "W"), (0x4, "U"), (0x8, "PWT"), (0x10, "PCD"), (0x20, "A"), (0x40, "D"),
             (0x100, "G")]
    return " ".join(n for bit, n in names if e & bit) or "-"


def walk(read: Read, cr0: int, cr3: int, cr4: int, linear: int) -> Walk:
    """Translate a linear address to a physical one through the page tables, as the CPU would."""
    linear &= 0xFFFFFFFF
    if not cr0 & CR0_PG:
        return Walk(linear, linear, ["paging off: physical = linear"])
    steps: list[str] = []

    def entry(kind: str, addr: int, size: int) -> int:
        e = int.from_bytes(read(addr, size), "little")
        steps.append(f"{kind} at {addr:#x} = {e:0{size * 2}x} ({_pte_flags(e)})")
        return e

    if cr4 & CR4_PAE:
        mask = 0xFFFFFF000         # 36-bit physical addresses
        pdpte = entry("PDPTE", (cr3 & 0xFFFFFFE0) + ((linear >> 30) & 3) * 8, 8)
        if not pdpte & 1:
            return Walk(linear, None, steps)
        pde = entry("PDE", (pdpte & mask) + ((linear >> 21) & 0x1FF) * 8, 8)
        if not pde & 1:
            return Walk(linear, None, steps)
        if pde & 0x80:
            steps.append("2 MB page")
            return Walk(linear, (pde & 0xFFFE00000) | (linear & 0x1FFFFF), steps)
        pte = entry("PTE", (pde & mask) + ((linear >> 12) & 0x1FF) * 8, 8)
        if not pte & 1:
            return Walk(linear, None, steps)
        return Walk(linear, (pte & mask) | (linear & 0xFFF), steps)

    pde = entry("PDE", (cr3 & 0xFFFFF000) + (linear >> 22) * 4, 4)
    if not pde & 1:
        return Walk(linear, None, steps)
    if pde & 0x80 and cr4 & CR4_PSE:
        steps.append("4 MB page")
        return Walk(linear, (pde & 0xFFC00000) | ((pde >> 13) & 0xF) << 32 | (linear & 0x3FFFFF), steps)
    pte = entry("PTE", (pde & 0xFFFFF000) + ((linear >> 12) & 0x3FF) * 4, 4)
    if not pte & 1:
        return Walk(linear, None, steps)
    return Walk(linear, (pte & 0xFFFFF000) | (linear & 0xFFF), steps)


def read_linear(read: Read, cr0: int, cr3: int, cr4: int, linear: int, length: int) -> bytes:
    """Bytes at a linear address, a page at a time; raises ValueError at an unmapped page."""
    out = bytearray()
    while length > 0:
        n = min(length, 0x1000 - (linear & 0xFFF))
        w = walk(read, cr0, cr3, cr4, linear)
        if w.physical is None:
            raise ValueError(f"linear {linear:#x} is not mapped")
        out += read(w.physical, n)
        linear, length = linear + n, length - n
    return bytes(out)


def mode(r: dict) -> str:
    """The CPU mode from a `regs` reply."""
    if not r["cr0"] & CR0_PE:
        return "real"
    if r["eflags"] & EFLAGS_VM:
        return "virtual-8086"
    return "protected" + (", paging" if r["cr0"] & CR0_PG else "") + (", PAE" if r.get("cr4", 0) & CR4_PAE else "")
