# MOUSEBOX.COM: put the pointer where an outside program says, for every way DOS programs read it.
#
# Load it after the mouse driver (e.g. CuteMouse); the mouse keeps working as before. Then a
# program outside the PC (86box-mcp's mouse_to) can put the pointer anywhere, by writing to a
# mailbox in MOUSEBOX's memory: it moves the driver's pointer there (INT 33h function 4), and
# tells the program about the move in mickeys too, both in its event handler's counts (function
# 0Ch, SI and DI) and in the motion counters (function 0Bh), so programs that draw their own
# pointer from mickeys, such as Warcraft II, follow it. Each move
# goes as two: far up and left, so the driver's pointer and the program's own both stop at the
# corner, then the target's distance from the corner, so it works however far the two had
# drifted apart. Mickeys use the default ratios (8 per 8 units across, 16 down).
#
# MOUSEBOX /T also takes a 3M MicroTouch serial touch screen on COM1 (86Box's
# microtouch_touchpen) as the pointer's position, holding the left button while touched. The
# press comes two timer ticks (about 110 ms) after the touch, once the program has seen the
# pointer arrive: Warcraft II only clicks a button the pointer was already over.
#
# Build:  i586-pc-msdosdjgpp-as -o mousebox.o mousebox.s
#         i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o MOUSEBOX.COM mousebox.o
#
# The mailbox, at the signature "MOUSEBOX" (offset 0x103 of MOUSEBOX's segment); offsets from it:
#   +8  seq    word  the outside program writes x, y, then increments seq
#   +10 ack    word  MOUSEBOX sets it to seq once the move is done (within a timer tick)
#   +12 x, y   words the target, in the driver's units (the coordinates functions 3, 4, 7, 8 use)
#   +16 xmin, xmax, ymin, ymax   words  the range the program last set (functions 7, 8)
#   +24 xfull, yfull             words  the widest range's maxima since the last reset: the
#                                       whole screen, in units, for programs that later narrow it
#   +28 dx, dy, buttons          words  the driver's pointer, as of the last timer tick
#   +34 hmask  word, +36 handler dword  the program's event handler and its mask (0: none)
#   +40 calls  64 words                 INT 33h calls by function number, to see how a program
#                                       reads the mouse

        .intel_syntax noprefix
        .code16
        .text
        .globl start
start:  jmp     init

sig:    .ascii  "MOUSEBOX"
mbseq:  .word   0
mback:  .word   0
mbx:    .word   0
mby:    .word   0
xmin:   .word   0
xmax:   .word   639
ymin:   .word   0
ymax:   .word   199
xfull:  .word   639
yfull:  .word   199
pubx:   .word   0
puby:   .word   0
pubb:   .word   0
hmask:  .word   0
hand:   .long   0                       # the program's event handler
hist:   .fill   64, 2, 0                # INT 33h calls by function number (AX < 40h)
direct: .byte   0                       # px, py given in units (the mailbox), not a touch
touchon: .byte  0                       # /T: the touch screen is in use
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
busy:   .byte   0                       # inside INT 33h, or applying a move
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
cntx:   .word   0                       # moves' mickeys not yet read through function 0Bh
cnty:   .word   0
cntx2:  .word   0                       # and for the read after that: the target, after the
cnty2:  .word   0                       # corner, so a program clamping its pointer sees both
lastsi: .word   0                       # the counts the program last got
lastdi: .word   0

        .equ    COM, 0x3F8

# Scale a touch (tx, ty, 0..16383 of the screen) to units: px = tx * (xfull + 1) / 16384, over the
# widest range the program set, which is the whole screen even when it later narrows the range.
scale:
        push    eax
        push    ebx
        push    edx
        movzx   eax, word ptr cs:[tx]
        movzx   ebx, word ptr cs:[xfull]
        inc     ebx
        mul     ebx
        shr     eax, 14
        mov     word ptr cs:[px], ax
        movzx   eax, word ptr cs:[ty]
        movzx   ebx, word ptr cs:[yfull]
        inc     ebx
        mul     ebx
        shr     eax, 14
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
        cmp     byte ptr cs:[direct], 0
        jne     applyd
        call    scale
applyd: mov     byte ptr cs:[direct], 0
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
        sub     word ptr cs:[cntx], 4000
        sub     word ptr cs:[cnty], 4000
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
        mov     ax, word ptr cs:[px]    # from the corner, the screen's 0,0: its distance
        mov     word ptr cs:[mdx], ax   # in mickeys
        mov     ax, word ptr cs:[py]
        shl     ax, 1
        mov     word ptr cs:[mdy], ax
        add     word ptr cs:[offy], ax  # the program's mickey counts run on by the move
        add     word ptr cs:[lastdi], ax
        add     word ptr cs:[cnty2], ax
        mov     ax, word ptr cs:[mdx]
        add     word ptr cs:[offx], ax
        add     word ptr cs:[lastsi], ax
        add     word ptr cs:[cntx2], ax
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

# Timer tick: take a move from the mailbox, note the driver's pointer, and deliver a waiting press
# two ticks (about 110 ms) after a touch.
int1c:
        cmp     byte ptr cs:[busy], 0
        jne     i1cp
        inc     byte ptr cs:[busy]
        sti
        pusha
        mov     ax, word ptr cs:[mbseq]
        cmp     ax, word ptr cs:[mback]
        je      i1cm
        mov     bx, word ptr cs:[mbx]
        mov     word ptr cs:[px], bx
        mov     bx, word ptr cs:[mby]
        mov     word ptr cs:[py], bx
        mov     byte ptr cs:[direct], 1
        or      byte ptr cs:[pend], 1
        push    ax
        call    apply
        pop     ax
        mov     word ptr cs:[mback], ax
i1cm:   mov     ax, 3                   # the driver's pointer, for the outside to read
        pushf
        call    dword ptr cs:[old33]
        mov     word ptr cs:[pubx], cx
        mov     word ptr cs:[puby], dx
        mov     word ptr cs:[pubb], bx
        popa
        cli
        dec     byte ptr cs:[busy]
i1cp:   cmp     byte ptr cs:[pdown], 0
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
        cmp     ax, 0x0B
        je      i33fb
        cmp     ax, 5
        je      i33f5
        cmp     ax, 6
        je      i33f6
i33go:  dec     byte ptr cs:[busy]
        jmp     dword ptr cs:[old33]

i33rst: pushf                           # reset the driver, then ask it its range (function 26h:
        call    dword ptr cs:[old33]    # the maximum coordinates, in MS driver 6.26 and CuteMouse)
        push    ax
        push    bx
        push    cx
        push    dx
        mov     cx, 639
        mov     dx, 199
        mov     ax, 0x26
        pushf
        call    dword ptr cs:[old33]
        mov     word ptr cs:[xmin], 0
        mov     word ptr cs:[xmax], cx
        mov     word ptr cs:[xfull], cx
        mov     word ptr cs:[ymin], 0
        mov     word ptr cs:[ymax], dx
        mov     word ptr cs:[yfull], dx
        pop     dx
        pop     cx
        pop     bx
        pop     ax
        mov     dword ptr cs:[hand], 0
        mov     word ptr cs:[hmask], 0
        mov     word ptr cs:[offx], 0
        mov     word ptr cs:[offy], 0
        mov     word ptr cs:[lastsi], 0
        mov     word ptr cs:[lastdi], 0
        mov     word ptr cs:[cntx], 0
        mov     word ptr cs:[cnty], 0
        mov     word ptr cs:[cntx2], 0
        mov     word ptr cs:[cnty2], 0
        dec     byte ptr cs:[busy]
        iret
i33x:   push    cx
        push    dx
        cmp     cx, dx
        jle     i33x1
        xchg    cx, dx
i33x1:  mov     word ptr cs:[xmin], cx
        mov     word ptr cs:[xmax], dx
        cmp     dx, word ptr cs:[xfull]
        jle     i33x2
        mov     word ptr cs:[xfull], dx
i33x2:
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
        cmp     dx, word ptr cs:[yfull]
        jle     i33y2
        mov     word ptr cs:[yfull], dx
i33y2:
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

i33fb:  pushf                           # motion counters: the moves' mickeys too
        call    dword ptr cs:[old33]
        add     cx, word ptr cs:[cntx]
        add     dx, word ptr cs:[cnty]
        push    ax
        mov     ax, word ptr cs:[cntx2]
        mov     word ptr cs:[cntx], ax
        mov     ax, word ptr cs:[cnty2]
        mov     word ptr cs:[cnty], ax
        pop     ax
        mov     word ptr cs:[cntx2], 0
        mov     word ptr cs:[cnty2], 0
        jmp     i33ret
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

msg_nomouse: .ascii "MOUSEBOX: load a mouse driver first$"
msg_ok:      .ascii "MOUSEBOX: installed$"
msg_touch:   .ascii "MOUSEBOX: and the MicroTouch touch screen on COM1$"
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
        mov     ax, 0x26                # the driver's range now, for xfull/yfull
        mov     cx, 639
        mov     dx, 199
        int     0x33
        mov     word ptr [xmax], cx
        mov     word ptr [xfull], cx
        mov     word ptr [ymax], dx
        mov     word ptr [yfull], dx

        movzx   cx, byte ptr ds:[0x80]  # /T on the command line: the touch screen too
        mov     si, 0x81
argl:   jcxz    argd
        lodsb
        dec     cx
        cmp     al, '/'
        jne     argl
        jcxz    argd
        lodsb
        dec     cx
        or      al, 0x20
        cmp     al, 't'
        jne     argl
        mov     byte ptr [touchon], 1
        jmp     argl
argd:   cmp     byte ptr [touchon], 0
        je      hooks

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
        mov     ax, 0x350C              # hook IRQ 4
        int     0x21
        mov     word ptr [oldirq], bx
        mov     word ptr [oldirq + 2], es
        mov     ax, 0x250C
        mov     dx, offset irq4
        int     0x21

hooks:  mov     ax, 0x2533              # hook INT 33h and the timer tick

        mov     dx, offset int33
        int     0x21
        mov     ax, 0x351C
        int     0x21
        mov     word ptr [old1c], bx
        mov     word ptr [old1c + 2], es
        mov     ax, 0x251C
        mov     dx, offset int1c
        int     0x21
        mov     dx, offset msg_ok
        call    say
        cmp     byte ptr [touchon], 0
        je      resid
        mov     dx, COM + 1
        mov     al, 1
        out     dx, al
        in      al, 0x21                # unmask IRQ 4
        and     al, 0xEF
        out     0x21, al
        mov     dx, offset msg_touch
        call    say
resid:  mov     dx, offset resident_end + 15
        shr     dx, 4
        mov     ax, 0x3100
        int     0x21

nomouse:
        mov     dx, offset msg_nomouse
        call    say
        mov     ax, 0x4C01
        int     0x21
