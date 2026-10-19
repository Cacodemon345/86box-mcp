"""Descriptor decoding and page walks on synthetic tables (no VM)."""

from __future__ import annotations

import struct

import pytest

from box86mcp import x86


def desc(base: int, limit: int, access: int, flags: int) -> bytes:
    return struct.pack("<HHBBBB", limit & 0xFFFF, base & 0xFFFF, (base >> 16) & 0xFF, access,
                       (flags << 4) | ((limit >> 16) & 0xF), base >> 24)


def gate(sel: int, off: int, access: int, params: int = 0) -> bytes:
    return struct.pack("<HHBBH", off & 0xFFFF, sel, params, access, off >> 16)


def test_flat_code_and_data():
    d = x86.decode_descriptor(desc(0, 0xFFFFF, 0x9A, 0xC))
    assert (d.kind, d.base, d.limit, d.dpl, d.present) == ("code", 0, 0xFFFFFFFF, 0, True)
    assert "32-bit" in d.flags and "readable" in d.flags and "4K" in d.flags
    d = x86.decode_descriptor(desc(0x12345678, 0xFFFF, 0xF2, 0x0))
    assert (d.kind, d.base, d.limit, d.dpl) == ("data", 0x12345678, 0xFFFF, 3)
    assert "writable" in d.flags and "16-bit" in d.flags
    assert str(x86.decode_descriptor(bytes(8))) == "null"


def test_system_descriptors_and_gates():
    d = x86.decode_descriptor(desc(0x80001000, 0x67, 0x89, 0))
    assert (d.kind, d.base, d.limit) == ("TSS32 available", 0x80001000, 0x67)
    d = x86.decode_descriptor(gate(0x0008, 0xC0123456, 0x8E))
    assert (d.kind, d.selector, d.offset, d.dpl) == ("interrupt gate32", 8, 0xC0123456, 0)
    assert str(d) == "interrupt gate32 -> 0008:c0123456, DPL 0"
    d = x86.decode_descriptor(gate(0x0010, 0x1234, 0xEC, params=3))
    assert (d.kind, d.offset, d.dpl, d.flags) == ("call gate32", 0x1234, 3, ["3 params"])
    d = x86.decode_descriptor(gate(0x0028, 0, 0x85))
    assert d.kind == "task gate" and str(d) == "task gate -> 0028, DPL 0"


class Mem:
    def __init__(self):
        self.m = bytearray(0x400000)

    def put(self, addr: int, value: int, size: int) -> None:
        self.m[addr:addr + size] = value.to_bytes(size, "little")

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self.m[addr:addr + n])


def test_walk_32bit_4k_and_4m_pages():
    m = Mem()
    m.put(0x1000 + (0xC0000000 >> 22) * 4, 0x2000 | 0x7, 4)       # PDE -> page table at 0x2000
    m.put(0x2000 + 0x12 * 4, 0x345000 | 0x3, 4)                    # PTE: page 0x12 -> 0x345000
    w = x86.walk(m.read, x86.CR0_PG | x86.CR0_PE, 0x1000, 0, 0xC0012ABC)
    assert w.physical == 0x345ABC and len(w.steps) == 2
    assert x86.walk(m.read, x86.CR0_PG, 0x1000, 0, 0xC0013000).physical is None   # PTE not present
    m.put(0x1000 + 1 * 4, 0x00800000 | 0x83 | (0x3 << 13), 4)      # 4 MB page, PSE-36 bits 33:32 = 3
    w = x86.walk(m.read, x86.CR0_PG, 0x1000, x86.CR4_PSE, 0x00412345)
    assert w.physical == 0x3_00812345 and "4 MB page" in w.steps
    assert x86.walk(m.read, 0, 0x1000, 0, 0xB8000).physical == 0xB8000             # paging off


def test_walk_pae():
    m = Mem()
    m.put(0x3000 + 3 * 8, 0x4000 | 1, 8)                           # PDPTE 3 -> page directory 0x4000
    m.put(0x4000 + 0x5 * 8, 0x5000 | 3, 8)                         # PDE 5 -> page table 0x5000
    m.put(0x5000 + 0x7 * 8, 0x1_2345_6000 | 3, 8)                  # PTE 7 -> 36-bit frame
    lin = (3 << 30) | (5 << 21) | (7 << 12) | 0x89
    assert x86.walk(m.read, x86.CR0_PG, 0x3000, x86.CR4_PAE, lin).physical == 0x1_2345_6089
    m.put(0x4000 + 0x6 * 8, 0x0060_0000 | 0x83, 8)                 # PDE 6: a 2 MB page
    lin = (3 << 30) | (6 << 21) | 0x1234
    assert x86.walk(m.read, x86.CR0_PG, 0x3000, x86.CR4_PAE, lin).physical == 0x601234


def test_read_linear_crosses_pages_and_stops_at_holes():
    m = Mem()
    m.put(0x1000, 0x2000 | 1, 4)
    m.put(0x2000 + 0 * 4, 0x9000 | 1, 4)                           # linear page 0 -> 0x9000
    m.put(0x2000 + 1 * 4, 0x7000 | 1, 4)                           # linear page 1 -> 0x7000
    m.m[0x9FFE:0xA000] = b"ab"
    m.m[0x7000:0x7002] = b"cd"
    assert x86.read_linear(m.read, x86.CR0_PG, 0x1000, 0, 0xFFE, 4) == b"abcd"
    with pytest.raises(ValueError, match="not mapped"):
        x86.read_linear(m.read, x86.CR0_PG, 0x1000, 0, 0x1FFE, 4)


def test_mode():
    assert x86.mode({"cr0": 0x10, "eflags": 0x2}) == "real"
    assert x86.mode({"cr0": 0x80000011, "eflags": 0x20202}) == "virtual-8086"
    assert x86.mode({"cr0": 0x80000011, "eflags": 0x202, "cr4": 0x20}) == "protected, paging, PAE"
