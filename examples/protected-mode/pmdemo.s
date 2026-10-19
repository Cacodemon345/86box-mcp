# PMDEMO.COM: enter 32-bit protected mode from DOS with a full set of system structures, then
# spin, so that registers, descriptor_table, translate_address and read_memory(linear=True) have
# something known to show. It never returns to DOS: reset the machine afterwards.
#
#   GDT (in this program)   08 code32 flat   10 data32 flat   18 data, not present
#                           20 LDT           28 TSS32         30 call gate32 (DPL 3, 2 params)
#                           38 data16 at B8000 (DPL 3)
#   LDT  at 0x82100         04 data32 at C0000000 (the alias below)   0C data16 at B8000
#   IDT  at 0x83000         32 interrupt gates; 3 a DPL 3 trap gate; 8 a task gate to the TSS;
#                           0E the page-fault handler
#   TSS  at 0x82000         esp0, ss0 10, cr3
#   Page directory 0x80000, page table 0x81000: the first 4 MB identity-mapped, and again at
#   linear C0000000, except the page at 3FF000 (and C03FF000), left unmapped.
#
# Then it writes "PM" at the top left of the screen through the LDT's video segment, reads the
# unmapped page once (the fault handler skips the read, so CR2 = 3FF000), sets DR0 to `marker`,
# leaves 1.0 and pi on the FPU stack, and spins with EBX = C0DEF00D.
#
#   i586-pc-msdosdjgpp-as -o pmdemo.o pmdemo.s
#   i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o PMDEMO.COM pmdemo.o

        .set PD, 0x80000
        .set PT0, 0x81000
        .set TSS, 0x82000
        .set LDT, 0x82100
        .set IDT, 0x83000

        .code16
        .globl start
start:
        cli
        fninit
        fldpi
        fld1
        xorl    %eax, %eax
        movw    %cs, %ax
        shll    $4, %eax
        movl    %eax, %ebp              # EBP = linear base of this program, kept throughout
        addl    $gdt, %eax
        movl    %eax, gdtr + 2
        lgdtl   gdtr
        movl    %cr0, %eax
        orb     $1, %al
        movl    %eax, %cr0
        pushl   $0x08                   # far return to pm32 in the flat code segment
        movl    %ebp, %eax
        addl    $pm32, %eax
        pushl   %eax
        lretl

        .code32
pm32:
        movw    $0x10, %ax
        movw    %ax, %ds
        movw    %ax, %es
        movw    %ax, %ss
        movw    %ax, %fs
        movw    %ax, %gs
        leal    0xfff0(%ebp), %esp

        leal    gate_handler(%ebp), %eax        # the call gate's offset
        movw    %ax, gdt + 0x30(%ebp)
        shrl    $16, %eax
        movw    %ax, gdt + 0x36(%ebp)

        movl    $PD, %edi                       # clear the page tables, TSS, LDT and IDT
        movl    $0x4000 / 4, %ecx
        xorl    %eax, %eax
        cld
        rep stosl

        movl    $PT0, %edi                      # identity-map 0..4 MB
        movl    $0x003, %eax
        movl    $1024, %ecx
1:      stosl
        addl    $0x1000, %eax
        loop    1b
        movl    $0, PT0 + 0x3ff * 4             # but not 3FF000
        movl    $PT0 | 3, PD                    # linear 0 and C0000000 share the table
        movl    $PT0 | 3, PD + 0x300 * 4

        leal    ldt_entries(%ebp), %esi
        movl    $LDT, %edi
        movl    $16, %ecx
        rep movsb

        movl    %esp, TSS + 4                   # esp0
        movl    $0x10, TSS + 8                  # ss0
        movl    $PD, TSS + 0x1c                 # cr3
        movw    $0x68, TSS + 0x66               # no I/O bitmap

        movl    $IDT, %edi                      # 32 interrupt gates to default_handler
        leal    default_handler(%ebp), %edx
        movl    $32, %ecx
1:      movl    %edx, %eax
        andl    $0xffff, %eax
        orl     $0x00080000, %eax
        stosl
        movl    %edx, %eax
        andl    $0xffff0000, %eax
        orl     $0x8e00, %eax
        stosl
        loop    1b
        movb    $0xef, IDT + 3 * 8 + 5          # vector 3: trap gate, DPL 3
        movl    $0x00280000, IDT + 8 * 8        # vector 8: task gate to the TSS
        movl    $0x00008500, IDT + 8 * 8 + 4
        leal    pf_handler(%ebp), %eax          # vector 0E: the page-fault handler
        movw    %ax, IDT + 0x0e * 8
        shrl    $16, %eax
        movw    %ax, IDT + 0x0e * 8 + 6
        lidtl   idtr(%ebp)

        movw    $0x20, %ax
        lldt    %ax
        movw    $0x28, %ax
        ltr     %ax

        movl    $PD, %eax                       # paging on
        movl    %eax, %cr3
        movl    %cr0, %eax
        orl     $0x80000000, %eax
        movl    %eax, %cr0
        jmp     1f
1:
        movw    $0x04, %ax                      # FS: the LDT's alias at C0000000
        movw    %ax, %fs
        movw    $0x0c, %ax                      # GS: the LDT's video segment
        movw    %ax, %gs
        movl    $0x1f4d1f50, %gs:0              # "PM", white on blue

        .byte   0xa1                            # movl 0x3FF000, %eax: faults, skipped
        .long   0x3ff000

        leal    marker(%ebp), %eax
        movl    %eax, %dr0
        movl    $0xc0def00d, %ebx
2:      jmp     2b

pf_handler:                                     # skip the 5-byte read that faulted
        addl    $4, %esp
        addl    $5, (%esp)
        iretl
default_handler:
        iretl
gate_handler:
        lretl   $8

        .balign 8
gdt:
        .quad   0
        .byte   0xff, 0xff, 0, 0, 0, 0x9a, 0xcf, 0      # 08 code32 flat
        .byte   0xff, 0xff, 0, 0, 0, 0x92, 0xcf, 0      # 10 data32 flat
        .byte   0xff, 0xff, 0, 0, 0, 0x12, 0x00, 0      # 18 data, not present
        .byte   0x0f, 0, 0x00, 0x21, 0x08, 0x82, 0, 0   # 20 LDT at 82100
        .byte   0x67, 0, 0x00, 0x20, 0x08, 0x89, 0, 0   # 28 TSS32 at 82000
        .byte   0, 0, 0x08, 0, 2, 0xec, 0, 0            # 30 call gate32 to 08:gate_handler
        .byte   0xff, 0xff, 0x00, 0x80, 0x0b, 0xf2, 0, 0        # 38 data16 at B8000, DPL 3
gdt_end:
ldt_entries:
        .byte   0xff, 0xff, 0, 0, 0, 0x92, 0xcf, 0xc0   # 04 data32 at C0000000
        .byte   0xff, 0x0f, 0x00, 0x80, 0x0b, 0xf2, 0, 0        # 0C data16 at B8000, DPL 3
gdtr:
        .word   gdt_end - gdt - 1
        .long   0
idtr:
        .word   32 * 8 - 1
        .long   IDT
marker:
        .ascii  "PMDEMO"
