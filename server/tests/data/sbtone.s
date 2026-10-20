# SBTONE.COM: play `samples` (a 1 kHz sine, appended after the code twice) once through the
# Sound Blaster at 220h, DMA 1, 8-bit single cycle at about 22 kHz, wait for it, and exit.
        .code16
        .globl start
start:
        movw    $0x226, %dx             # reset the DSP
        movb    $1, %al
        outb    %al, %dx
        movw    $100, %cx
1:      loop    1b
        xorb    %al, %al
        outb    %al, %dx
        movw    $0xffff, %cx
2:      movw    $0x22e, %dx
        inb     %dx, %al
        testb   $0x80, %al
        loopz   2b
        movw    $0x22a, %dx
        inb     %dx, %al
        movb    $0xd1, %al              # speaker on
        call    dsp
        movb    $0x40, %al              # time constant 211: 22222 Hz
        call    dsp
        movb    $211, %al
        call    dsp

        xorl    %eax, %eax              # physical address of the first copy that doesn't
        movw    %cs, %ax                # cross a 64 KB page
        shll    $4, %eax
        addl    $samples, %eax
        movl    %eax, %ebx
        addl    $(NSAMP - 1), %ebx
        xorl    %eax, %ebx
        testl   $0xffff0000, %ebx
        jz      3f
        addl    $NSAMP, %eax
3:      movl    %eax, %ebx
        movb    $5, %al                 # mask DMA 1
        outb    %al, $0x0a
        outb    %al, $0x0c              # clear the flip-flop
        movb    $0x49, %al              # single, read, channel 1
        outb    %al, $0x0b
        movb    %bl, %al
        outb    %al, $0x02
        movb    %bh, %al
        outb    %al, $0x02
        shrl    $16, %ebx
        movb    %bl, %al
        outb    %al, $0x83
        movb    $((NSAMP - 1) & 0xff), %al
        outb    %al, $0x03
        movb    $((NSAMP - 1) >> 8), %al
        outb    %al, $0x03
        movb    $1, %al                 # unmask
        outb    %al, $0x0a
        movb    $0x14, %al              # 8-bit single-cycle output
        call    dsp
        movb    $((NSAMP - 1) & 0xff), %al
        call    dsp
        movb    $((NSAMP - 1) >> 8), %al
        call    dsp

4:      inb     $0x08, %al              # until DMA 1 reaches terminal count
        testb   $0x02, %al
        jz      4b
        movw    $0x22e, %dx             # acknowledge the DSP's interrupt
        inb     %dx, %al
        ret

dsp:    movw    $0x22c, %dx
        movb    %al, %ah
5:      inb     %dx, %al
        testb   $0x80, %al
        jnz     5b
        movb    %ah, %al
        outb    %al, %dx
        ret

        .set    NSAMP, 11111
samples:
# The samples follow, twice (see build line): 128 + 100 sin(2 pi 1000 n / 22222), n = 0..11110.
#
#   i586-pc-msdosdjgpp-as -o sbtone.o sbtone.s
#   i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o code.bin sbtone.o
#   python3 -c "import math,sys; c=open('code.bin','rb').read()[:int(sys.argv[1],16)]; \
#     s=bytes(round(128+100*math.sin(2*math.pi*1000*n/22222)) for n in range(11111)); \
#     open('SBTONE.COM','wb').write(c+s+s)" $(i586-pc-msdosdjgpp-nm sbtone.o | awk '/samples/{print $1}')
