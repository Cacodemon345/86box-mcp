# MTMOUSE.COM: a 3M MicroTouch serial touch screen on COM1 as the mouse's absolute position.
#
# Load it after the mouse driver (e.g. CuteMouse). The mouse keeps working as before; a touch
# moves the pointer to where the screen was touched (through the driver's INT 33h function 4,
# scaled to the range the program set with functions 7 and 8) and holds the left button while
# the finger is down: in function 3, in functions 5 and 6 (press and release counts), and in
# calls to the program's event handler (function 0Ch). The press comes two timer ticks (about
# 110 ms) after the touch, once the program has seen the pointer arrive: programs such as
# Warcraft II only click a button the pointer was already over. The handler is told about the move first,
# with the position and with the move in mickeys (SI, DI): games such as Warcraft II follow the
# pointer by its mickeys, and would otherwise see the press where the pointer used to be. The
# mickeys are the driver's running counts plus the touches' (the program's handler is called
# through a stub, which adds them), so a program that follows the counts moves its pointer to
# the touch and keeps following the mouse from there.
#
# Build:  i586-pc-msdosdjgpp-as -o mtmouse.o mtmouse.s
#         i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o MTMOUSE.COM mtmouse.o
#
# The resident part starts with the signature "MTMOUSE1" at offset 0x103 of its segment, followed
# by counts of the INT 33h functions 0-3Fh programs called, to see how a program reads the mouse.

        .intel_syntax noprefix
        .code16
        .text
        .globl start
start:  jmp     init

sig:    .ascii  "MTMOUSE1"
hist:   .fill   64, 2, 0                # INT 33h calls by function number (AX < 40h)
old33:  .long   0
oldirq: .long   0
pkt:    .byte   0, 0, 0, 0, 0
have:   .byte   0
tx:     .word   0                       # last touch, 0..16383, y from the top
ty:     .word   0
down:   .byte   0                       # the left button as programs see it
touch:  .byte   0                       # the finger as the controller reports it
pdown:  .byte   0                       # a press waiting for the pointer to settle
qrel:   .byte   0                       # a release to deliver right after that press
ptick:  .word   0                       # BIOS tick of the touch
old1c:  .long   0
pend:   .byte   0                       # 1: a new position to apply; 2: down changed
busy:   .byte   0                       # inside INT 33h, or applying a touch
xmin:   .word   0
xmax:   .word   639
ymin:   .word   0
ymax:   .word   199
hand:   .long   0                       # the program's event handler, and its mask
hmask:  .word   0
px:     .word   0                       # the touch in the program's coordinates
py:     .word   0
npress: .word   0                       # left presses / releases since functions 5 / 6
nrel:   .word   0
prx:    .word   0                       # where the last press and release were
pry:    .word   0
rlx:    .word   0
rly:    .word   0
mdx:    .word   0                       # the touch's move in mickeys, for the event handler
mdy:    .word   0
offx:   .word   0                       # all touches' mickeys so far: added to the driver's
offy:   .word   0                       # running counts (SI, DI) on their way to the program
lastsi: .word   0                       # the counts the program last got
lastdi: .word   0

        .equ    COM, 0x3F8

# Scale tx, ty to the program's range: px = xmin + tx * (xmax - xmin + 1) / 16384.
scale:
        push    eax
        push    ebx
        push    edx
        movzx   eax, word ptr cs:[tx]
        movzx   ebx, word ptr cs:[xmax]
        sub     bx, word ptr cs:[xmin]
        inc     ebx
        mul     ebx
        shr     eax, 14
        add     ax, word ptr cs:[xmin]
        mov     word ptr cs:[px], ax
        movzx   eax, word ptr cs:[ty]
        movzx   ebx, word ptr cs:[ymax]
        sub     bx, word ptr cs:[ymin]
        inc     ebx
        mul     ebx
        shr     eax, 14
        add     ax, word ptr cs:[ymin]
        mov     word ptr cs:[py], ax
        pop     edx
        pop     ebx
        pop     eax
        ret

# Apply a pending touch: move the driver's pointer there, count presses and releases, and call
# the program's event handler. Called with busy set (no reentry into the driver).
apply:
        test    byte ptr cs:[pend], 3
        jz      apply9
        pushad
        push    ds
        push    es
        call    scale
        xor     si, si                  # event mask for the handler
        mov     word ptr cs:[mdx], 0
        mov     word ptr cs:[mdy], 0
        test    byte ptr cs:[pend], 1
        jz      apply2
        # A program that follows the handler's mickeys (SI, DI) keeps its own pointer, which can
        # drift from the driver's (Warcraft II sets the driver's position itself). So the touch
        # goes as two moves: far up and left, where both stop at the corner, then the touch's
        # distance from the corner, in mickeys at the default ratios (8 per 8 pixels across,
        # 16 per 8 down).
        sub     word ptr cs:[offx], 4000
        sub     word ptr cs:[lastsi], 4000
        sub     word ptr cs:[offy], 4000
        sub     word ptr cs:[lastdi], 4000
        cmp     dword ptr cs:[hand], 0
        je      apply1
        test    word ptr cs:[hmask], 1
        jz      apply1
        mov     ax, 1
        movzx   bx, byte ptr cs:[down]
        test    byte ptr cs:[pend], 2
        jz      apply0
        xor     bx, 1                   # the button hasn't changed yet
apply0: mov     cx, word ptr cs:[xmin]
        mov     dx, word ptr cs:[ymin]
        mov     si, word ptr cs:[lastsi]
        mov     di, word ptr cs:[lastdi]
        call    dword ptr cs:[hand]
apply1: xor     si, si
        mov     ax, word ptr cs:[px]
        sub     ax, word ptr cs:[xmin]
        mov     word ptr cs:[mdx], ax
        mov     ax, word ptr cs:[py]
        sub     ax, word ptr cs:[ymin]
        shl     ax, 1
        mov     word ptr cs:[mdy], ax
        add     word ptr cs:[offy], ax  # the program's mickey counts run on by the move
        add     word ptr cs:[lastdi], ax
        mov     ax, word ptr cs:[mdx]
        add     word ptr cs:[offx], ax
        add     word ptr cs:[lastsi], ax
        mov     si, 1                   # moved
        mov     ax, 4
        mov     cx, word ptr cs:[px]
        mov     dx, word ptr cs:[py]
        pushf
        call    dword ptr cs:[old33]
apply2: test    byte ptr cs:[pend], 2
        jz      apply4
        mov     ax, word ptr cs:[px]
        mov     dx, word ptr cs:[py]
        cmp     byte ptr cs:[down], 0
        je      apply3
        or      si, 2                   # left pressed
        inc     word ptr cs:[npress]
        mov     word ptr cs:[prx], ax
        mov     word ptr cs:[pry], dx
        jmp     apply4
apply3: or      si, 4                   # left released
        inc     word ptr cs:[nrel]
        mov     word ptr cs:[rlx], ax
        mov     word ptr cs:[rly], dx
apply4: mov     byte ptr cs:[pend], 0
        cmp     dword ptr cs:[hand], 0
        je      apply8
        mov     bp, si                  # the events, for the handler: the move first, with the
        test    bp, 1                   # buttons as they were, then the button change, so
        jz      apply6                  # the press happens where the pointer now is
        test    word ptr cs:[hmask], 1
        jz      apply6
        movzx   bx, byte ptr cs:[down]
        test    bp, 6
        jz      apply5
        xor     bx, 1                   # the button hasn't changed yet
apply5: mov     ax, 1
        mov     cx, word ptr cs:[px]
        mov     dx, word ptr cs:[py]
        mov     si, word ptr cs:[lastsi]
        mov     di, word ptr cs:[lastdi]
        push    bp
        call    dword ptr cs:[hand]
        pop     bp
apply6: and     bp, 6
        and     bp, word ptr cs:[hmask]
        jz      apply8
        mov     ax, bp
        movzx   bx, byte ptr cs:[down]
        mov     cx, word ptr cs:[px]
        mov     dx, word ptr cs:[py]
        mov     si, word ptr cs:[lastsi]
        mov     di, word ptr cs:[lastdi]
        call    dword ptr cs:[hand]
apply8: pop     es
        pop     ds
        popad
        cmp     byte ptr cs:[qrel], 0   # a quick tap: its release follows its press
        je      apply9
        mov     byte ptr cs:[qrel], 0
        mov     byte ptr cs:[down], 0
        or      byte ptr cs:[pend], 2
        jmp     apply
apply9: ret

# Timer tick: deliver a waiting press two ticks (about 110 ms) after the touch.
int1c:
        cmp     byte ptr cs:[pdown], 0
        je      i1cgo
        push    ax
        push    es
        mov     ax, 0x40
        mov     es, ax
        mov     ax, word ptr es:[0x6C]
        pop     es
        sub     ax, word ptr cs:[ptick]
        cmp     ax, 2
        jb      i1cno
        mov     byte ptr cs:[pdown], 0
        mov     byte ptr cs:[down], 1
        or      byte ptr cs:[pend], 2
        inc     byte ptr cs:[busy]
        cmp     byte ptr cs:[busy], 1
        jne     i1cb
        sti
        call    apply
        cli
i1cb:   dec     byte ptr cs:[busy]
i1cno:  pop     ax
i1cgo:  jmp     dword ptr cs:[old1c]

# The driver calls this instead of the program's handler: add the touches' mickeys to its counts.
stub:
        add     si, word ptr cs:[offx]
        add     di, word ptr cs:[offy]
        mov     word ptr cs:[lastsi], si
        mov     word ptr cs:[lastdi], di
        call    dword ptr cs:[hand]
        retf

# INT 33h: note ranges and the handler, apply a pending touch, then the driver; add the touch's
# button to functions 3, 5 and 6.
int33:
        cmp     ax, 64
        jae     i33a
        push    bx
        mov     bx, ax
        shl     bx, 1
        inc     word ptr cs:[hist + bx]
        pop     bx
i33a:   inc     byte ptr cs:[busy]
        cmp     byte ptr cs:[busy], 1
        jne     i33b
        call    apply
i33b:   cmp     ax, 0
        je      i33rst
        cmp     ax, 0x21
        je      i33rst
        cmp     ax, 7
        je      i33x
        cmp     ax, 8
        je      i33y
        cmp     ax, 0x0C
        je      i33h
        cmp     ax, 3
        je      i33f3
        cmp     ax, 5
        je      i33f5
        cmp     ax, 6
        je      i33f6
i33go:  dec     byte ptr cs:[busy]
        jmp     dword ptr cs:[old33]

i33rst: mov     word ptr cs:[xmin], 0
        mov     word ptr cs:[xmax], 639
        mov     word ptr cs:[ymin], 0
        mov     word ptr cs:[ymax], 199
        mov     dword ptr cs:[hand], 0
        mov     word ptr cs:[hmask], 0
        mov     word ptr cs:[offx], 0
        mov     word ptr cs:[offy], 0
        mov     word ptr cs:[lastsi], 0
        mov     word ptr cs:[lastdi], 0
        jmp     i33go
i33x:   push    cx
        push    dx
        cmp     cx, dx
        jle     i33x1
        xchg    cx, dx
i33x1:  mov     word ptr cs:[xmin], cx
        mov     word ptr cs:[xmax], dx
        pop     dx
        pop     cx
        jmp     i33go
i33y:   push    cx
        push    dx
        cmp     cx, dx
        jle     i33y1
        xchg    cx, dx
i33y1:  mov     word ptr cs:[ymin], cx
        mov     word ptr cs:[ymax], dx
        pop     dx
        pop     cx
        jmp     i33go
i33h:   mov     word ptr cs:[hand], dx      # the program's handler: the driver gets the stub
        mov     word ptr cs:[hand + 2], es
        mov     word ptr cs:[hmask], cx
        push    ax
        mov     ax, es
        or      ax, dx
        pop     ax
        jz      i33go                       # no handler: pass it on as it is
        push    es
        push    dx
        push    cs
        pop     es
        mov     dx, offset stub
        pushf
        call    dword ptr cs:[old33]
        pop     dx
        pop     es
        dec     byte ptr cs:[busy]
        iret

i33f3:  pushf
        call    dword ptr cs:[old33]
        cmp     byte ptr cs:[down], 0
        je      i33ret
        or      bx, 1
        jmp     i33ret
i33f5:  cmp     bx, 0
        jne     i33go
        pushf
        call    dword ptr cs:[old33]
        cmp     byte ptr cs:[down], 0
        je      i33f5a
        or      ax, 1
i33f5a: cmp     word ptr cs:[npress], 0
        je      i33ret
        add     bx, word ptr cs:[npress]
        mov     cx, word ptr cs:[prx]
        mov     dx, word ptr cs:[pry]
        mov     word ptr cs:[npress], 0
        jmp     i33ret
i33f6:  cmp     bx, 0
        jne     i33go
        pushf
        call    dword ptr cs:[old33]
        cmp     byte ptr cs:[down], 0
        je      i33f6a
        or      ax, 1
i33f6a: cmp     word ptr cs:[nrel], 0
        je      i33ret
        add     bx, word ptr cs:[nrel]
        mov     cx, word ptr cs:[rlx]
        mov     dx, word ptr cs:[rly]
        mov     word ptr cs:[nrel], 0
i33ret: dec     byte ptr cs:[busy]
        iret

# COM1 receive (IRQ 4): collect 5-byte Tablet reports, then apply them unless the driver is busy.
irq4:
        push    ax
        push    bx
        push    dx
irq4a:  mov     dx, COM + 5
        in      al, dx
        test    al, 1
        jz      irq4e
        mov     dx, COM
        in      al, dx
        test    al, 0x80
        jz      irq4b
        mov     byte ptr cs:[have], 0   # a report starts
        jmp     irq4c
irq4b:  cmp     byte ptr cs:[have], 0
        je      irq4a                   # not in a report (an acknowledgement)
irq4c:  movzx   bx, byte ptr cs:[have]
        mov     byte ptr cs:[pkt + bx], al
        inc     bx
        mov     byte ptr cs:[have], bl
        cmp     bx, 5
        jb      irq4a
        mov     byte ptr cs:[have], 0
        movzx   ax, byte ptr cs:[pkt + 2]
        shl     ax, 7
        or      al, byte ptr cs:[pkt + 1]
        mov     word ptr cs:[tx], ax
        movzx   ax, byte ptr cs:[pkt + 4]
        shl     ax, 7
        or      al, byte ptr cs:[pkt + 3]
        mov     bx, 16383
        sub     bx, ax                  # the controller counts y from the bottom
        mov     word ptr cs:[ty], bx
        or      byte ptr cs:[pend], 1
        mov     al, byte ptr cs:[pkt]
        shr     al, 6
        and     al, 1
        cmp     al, byte ptr cs:[touch]
        je      irq4a
        mov     byte ptr cs:[touch], al
        test    al, al
        jz      irq4r
        mov     byte ptr cs:[pdown], 1  # touched: the press comes once the program has seen
        push    es                      # the pointer arrive (int1c)
        mov     ax, 0x40
        mov     es, ax
        mov     ax, word ptr es:[0x6C]
        pop     es
        mov     word ptr cs:[ptick], ax
        jmp     irq4a
irq4r:  cmp     byte ptr cs:[pdown], 0  # lifted
        je      irq4s
        mov     byte ptr cs:[pdown], 0  # before its press was delivered: press, then release
        mov     byte ptr cs:[down], 1
        mov     byte ptr cs:[qrel], 1
        or      byte ptr cs:[pend], 2
        jmp     irq4a
irq4s:  mov     byte ptr cs:[down], 0
        or      byte ptr cs:[pend], 2
        jmp     irq4a
irq4e:  mov     al, 0x20                # end of interrupt
        out     0x20, al
        inc     byte ptr cs:[busy]
        cmp     byte ptr cs:[busy], 1
        jne     irq4f
        sti
        call    apply
        cli
irq4f:  dec     byte ptr cs:[busy]
        pop     dx
        pop     bx
        pop     ax
        iret

resident_end:

# Not resident -----------------------------------------------------------------------------------

msg_nomouse: .ascii "MTMOUSE: load a mouse driver first$"
msg_ok:      .ascii "MTMOUSE: MicroTouch touch screen on COM1 as the mouse position$"
crlf:        .ascii "\r\n$"

say:    mov     ah, 9
        int     0x21
        mov     dx, offset crlf
        mov     ah, 9
        int     0x21
        ret

send:   mov     dx, COM + 5             # send AL to the controller
        push    ax
send1:  in      al, dx
        test    al, 0x20
        jz      send1
        pop     ax
        mov     dx, COM
        out     dx, al
        ret

init:
        mov     ax, 0x3533              # a mouse driver must be there
        int     0x21
        mov     ax, es
        or      ax, bx
        jz      nomouse
        mov     word ptr [old33], bx
        mov     word ptr [old33 + 2], es
        xor     ax, ax
        int     0x33
        cmp     ax, 0xFFFF
        jne     nomouse

        mov     dx, COM + 1             # UART: 9600 baud, 8N1, receive interrupts
        xor     al, al
        out     dx, al
        mov     dx, COM + 3
        mov     al, 0x80
        out     dx, al
        mov     dx, COM
        mov     al, 12
        out     dx, al
        mov     dx, COM + 1
        xor     al, al
        out     dx, al
        mov     dx, COM + 3
        mov     al, 3
        out     dx, al
        mov     dx, COM + 4
        mov     al, 0x0B                # DTR, RTS, OUT2 (the interrupt line)
        out     dx, al
        mov     dx, COM
        in      al, dx
        mov     al, 1                   # Format Tablet
        call    send
        mov     al, 'F'
        call    send
        mov     al, 'T'
        call    send
        mov     al, 13
        call    send

        mov     ax, 0x350C              # hook IRQ 4 and INT 33h
        int     0x21
        mov     word ptr [oldirq], bx
        mov     word ptr [oldirq + 2], es
        mov     ax, 0x250C
        mov     dx, offset irq4
        int     0x21
        mov     ax, 0x2533
        mov     dx, offset int33
        int     0x21
        mov     ax, 0x351C
        int     0x21
        mov     word ptr [old1c], bx
        mov     word ptr [old1c + 2], es
        mov     ax, 0x251C
        mov     dx, offset int1c
        int     0x21
        mov     dx, COM + 1
        mov     al, 1
        out     dx, al
        in      al, 0x21                # unmask IRQ 4
        and     al, 0xEF
        out     0x21, al

        mov     dx, offset msg_ok
        call    say
        mov     dx, offset resident_end + 15
        shr     dx, 4
        mov     ax, 0x3100
        int     0x21

nomouse:
        mov     dx, offset msg_nomouse
        call    say
        mov     ax, 0x4C01
        int     0x21
