# Protected-mode debugging

`PMDEMO.COM` takes a DOS machine into 32-bit protected mode with every system structure an agent
might need to inspect. `demo.py` then drives the 86box MCP server over stdio, as an agent would,
and checks each tool's answer against what the program set up.

| Tool | What it shows here |
|---|---|
| `registers` | CR0 (PE and PG on), CR2 (the address of the page fault the program took), CR3, DR0, GDTR, IDTR, LDTR and TR, each segment's cached base, limit and access rights, CPL |
| `fpu_registers` | 1.0 and π on the x87 stack, with the control, status and tag words |
| `descriptor_table` | The GDT, LDT and IDT, decoded: code and data segments, a not-present segment, the LDT and TSS descriptors, a call gate, interrupt and trap gates, and a task gate. In real mode the IDT is the interrupt vector table |
| `translate_address` | Selector:offset to linear to physical, with each page directory and page table entry: through an LDT selector based at C0000000, through a segment register's cached base, and an unmapped page |
| `read_memory(linear=True)` | Memory as the CPU sees it with paging on: the program's marker through the C0000000 alias, and the TSS at TR's base |

These need 86Box with [86Box/86Box#8416](https://github.com/86Box/86Box/pull/8416), which adds the
control, debug and descriptor-table registers and the `fpu` command to the automation socket.

## What PMDEMO sets up

| Structure | Where | Contents |
|---|---|---|
| GDT | In the program | 08 flat code32, 10 flat data32, 18 a not-present data segment, 20 the LDT, 28 the TSS, 30 a DPL 3 call gate, 38 a DPL 3 data16 segment at B8000 |
| LDT | 82100 | 04 data32 at C0000000, 0C data16 at B8000 |
| IDT | 83000 | 32 interrupt gates; vector 3 a DPL 3 trap gate; 8 a task gate to the TSS; 0E the page-fault handler |
| TSS | 82000 | esp0, ss0 and cr3 |
| Page tables | 80000 (directory), 81000 (table) | The first 4 MB identity-mapped, and again at C0000000, except the page at 3FF000 |

Then the program:

1. writes "PM" at the top left of the screen through the LDT's video segment;
2. reads the unmapped page once, so CR2 = 3FF000, and its fault handler skips the read;
3. sets DR0 to its marker;
4. spins with EBX = C0DEF00D.

It never returns to DOS, so reset the machine afterwards. [pmdemo.s](pmdemo.s) has the build lines.

## Running it

```
server/.venv/bin/python examples/protected-mode/demo.py
```

It needs the usual `BOX86_*` settings, including `BOX86_DOS_BASE_IMG`, a bootable DOS image. It
prints each tool call and its result, ends with "19/19 checks passed", and saves a screenshot to
`out/`. A shortened run:

```
> descriptor_table(table='gdt', count=8)
  GDT base 0x0000f970 limit 0x003f (8 entries), CPU protected, paging
     0  sel 0000  null
     1  sel 0008  code, base 00000000, limit ffffffff, DPL 0, 4K 32-bit readable accessed
     2  sel 0010  data, base 00000000, limit ffffffff, DPL 0, 4K big writable accessed
     3  sel 0018  data, base 00000000, limit 0000ffff, not present, DPL 0, 16-bit writable
     4  sel 0020  LDT, base 00082100, limit 0000000f, DPL 0
     5  sel 0028  TSS32 busy, base 00082000, limit 00000067, DPL 0
     6  sel 0030  call gate32 -> 0008:0000f96a, DPL 3, 2 params
     7  sel 0038  data, base 000b8000, limit 0000ffff, DPL 3, 16-bit writable

> translate_address(address=63948, selector=4)
  CPU protected, paging
  selector 0004 (LDT entry 0x0, RPL 0): data, base c0000000, limit ffffffff, DPL 0, 4K big writable accessed
  linear 0xc000f9cc -> physical 0xf9cc
    PDE at 0x80c00 = 00081003 (P W)
    PTE at 0x8103c = 0000f023 (P W A)

> translate_address(address=4190208)
  CPU protected, paging
  linear 0x003ff000 -> not mapped
    PDE at 0x80000 = 00081023 (P W A)
    PTE at 0x81ffc = 00000000 (-)
```

The page walk also handles 4 MB pages (PSE, and PSE-36's high address bits) and PAE. The unit tests
in `server/tests/test_x86.py` cover those on synthetic tables.
