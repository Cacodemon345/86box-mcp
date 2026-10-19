/* INPTEST: the mouse and keyboard as DOS programs see them, for testing input to 86Box VMs.

   Programs read the mouse in different ways, and input that works for one can fail another (see
   docs/input-quirks.md). INPTEST shows the common ways side by side, on screen rows 1-6:

     POLL     x y b          function 3: the driver's position and buttons, as polled
     HANDLER  x y b          the position the event handler (function 0Ch) was given
     FOLLOW   x y            a pointer kept from the handler's mickey counts (SI, DI), the way
                             programs that draw their own pointer do; /F sets its scale
     COUNTERS x y  cx cy     with /C: function 0Bh, mickeys moved since the last call,
                             accumulated, and a pointer kept from them (scaled and clamped like
                             FOLLOW). Off by default: CuteMouse resets the handler's mickey counts
                             (SI, DI) when 0Bh is read, which would upset FOLLOW.
     PRESS    n tick pit     left presses seen by the handler, and when the last one came
                             (BIOS tick, then PIT channel 0 count: compare VMs given input together)
     KEY      n last tick pit  keys, likewise (polled, so within a loop pass)
     TOUCH    x y down n     with /T: the touch screen itself, read from COM1 (a 3M MicroTouch
                             in Tablet format, 0..16383 from the top left), bypassing any driver

   Options:
     /X:min,max  /Y:min,max  the range set with functions 7 and 8 (default: the driver's own)
     /F:n        FOLLOW moves one pixel per n mickeys (default 2), clamped to /W:width,height
                 (default 640,480)
     /C          read the motion counters (function 0Bh) too
     /T          raw touch screen on COM1. It takes COM1's interrupt away from a touch driver
                 (MOUSEBOX /T, DOSTOUCH) while INPTEST runs, so those see no touches meanwhile.
   Esc quits. Build with DJGPP. */
#include <conio.h>
#include <dpmi.h>
#include <go32.h>
#include <pc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/farptr.h>

static volatile int hx, hy, hb, fx, fy, events, presses, lastsi, lastdi, primed;
static volatile unsigned long press_tick;
static volatile unsigned press_pit;
static int fdiv = 2, width = 640, height = 480;
static _go32_dpmi_seginfo cb;
static _go32_dpmi_registers cbregs;

static unsigned long ticks(void) { return _farpeekl(_dos_ds, 0x46C); }
static unsigned pit(void)
{
    outportb(0x43, 0x00);                 /* latch channel 0 */
    unsigned lo = inportb(0x40);
    return lo | (inportb(0x40) << 8);
}
static int clamp(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

static void handler(_go32_dpmi_registers *r)
{
    int si = (short) r->x.si, di = (short) r->x.di;
    if (primed) {
        fx = clamp(fx + (short) (si - lastsi) / fdiv, 0, width - 1);
        fy = clamp(fy + (short) (di - lastdi) / fdiv, 0, height - 1);
    }
    lastsi = si; lastdi = di; primed = 1;
    hx = r->x.cx; hy = r->x.dx; hb = r->x.bx;
    events++;
    if (r->x.ax & 2) {                    /* left pressed */
        presses++;
        press_tick = ticks();
        press_pit = pit();
    }
}

static void int33(__dpmi_regs *r) { __dpmi_int(0x33, r); }

#define COM 0x3F8
static unsigned char pkt[5];
static int have, tx, ty, tdown, tn, old_ier;

static void com_put(int c)
{
    while (!(inportb(COM + 5) & 0x20))
        ;
    outportb(COM, c);
}

static void touch_open(void)
{
    old_ier = inportb(COM + 1);
    outportb(COM + 1, 0);                 /* polled: no interrupts */
    outportb(COM + 3, 0x80);              /* 9600 baud, 8N1 */
    outportb(COM + 0, 12);
    outportb(COM + 1, 0);
    outportb(COM + 3, 0x03);
    outportb(COM + 4, 0x0B);
    for (const char *p = "\001FT\r"; *p; p++)  /* Format Tablet */
        com_put(*p);
}

static void touch_poll(void)
{
    while (inportb(COM + 5) & 1) {
        int c = inportb(COM);
        if (c & 0x80)
            have = 0;                     /* a report starts */
        else if (!have)
            continue;                     /* the rest of an acknowledgement */
        pkt[have++] = c;
        if (have == 5) {
            have = 0;
            tx = pkt[1] | (pkt[2] << 7);
            ty = 16383 - (pkt[3] | (pkt[4] << 7));
            tdown = !!(pkt[0] & 0x40);
            tn++;
        }
    }
}

int main(int argc, char **argv)
{
    __dpmi_regs r;
    int keys = 0, lastkey = 0, cx = 0, cy = 0, raw = 0, counters = 0, cfx, cfy;
    unsigned long key_tick = 0;
    unsigned key_pit = 0;

    memset(&r, 0, sizeof r); r.x.ax = 0; int33(&r);
    if (r.x.ax != 0xFFFF) { cprintf("INPTEST: no mouse driver\r\n"); return 1; }
    for (int i = 1; i < argc; i++) {
        int a, b;
        char *o = argv[i];
        if ((o[0] == '/' || o[0] == '-') && o[1] && o[2] == ':') {
            char k = o[1] | 0x20;
            if ((k == 'x' || k == 'y') && sscanf(o + 3, "%d,%d", &a, &b) == 2) {
                memset(&r, 0, sizeof r); r.x.ax = k == 'x' ? 7 : 8; r.x.cx = a; r.x.dx = b; int33(&r);
            } else if (k == 'f' && sscanf(o + 3, "%d", &a) == 1 && a > 0)
                fdiv = a;
            else if (k == 'w' && sscanf(o + 3, "%d,%d", &a, &b) == 2)
                width = a, height = b;
        } else if ((o[0] == '/' || o[0] == '-') && (o[1] | 0x20) == 't')
            raw = 1;
        else if ((o[0] == '/' || o[0] == '-') && (o[1] | 0x20) == 'c')
            counters = 1;
    }
    fx = cfx = width / 2; fy = cfy = height / 2;

    cb.pm_offset = (unsigned long) handler;
    if (_go32_dpmi_allocate_real_mode_callback_retf(&cb, &cbregs)) { cprintf("no callback\r\n"); return 1; }
    memset(&r, 0, sizeof r);
    r.x.ax = 0x0C; r.x.cx = 0x1F; r.x.es = cb.rm_segment; r.x.dx = cb.rm_offset;
    int33(&r);

    if (raw)
        touch_open();
    clrscr();
    for (;;) {
        if (raw)
            touch_poll();
        if (kbhit()) {
            int c = getch();
            keys++; lastkey = c; key_tick = ticks(); key_pit = pit();
            if (c == 27) break;
        }
        if (counters) {
            memset(&r, 0, sizeof r); r.x.ax = 0x0B; int33(&r);
            cx += (short) r.x.cx; cy += (short) r.x.dx;
            cfx = clamp(cfx + (short) r.x.cx / fdiv, 0, width - 1);
            cfy = clamp(cfy + (short) r.x.dx / fdiv, 0, height - 1);
        }
        memset(&r, 0, sizeof r); r.x.ax = 3; int33(&r);
        gotoxy(1, 1); cprintf("POLL     x=%4d y=%4d b=%d          ", r.x.cx, r.x.dx, r.x.bx);
        gotoxy(1, 2); cprintf("HANDLER  x=%4d y=%4d b=%d events=%-6d", hx, hy, hb, events);
        gotoxy(1, 3); cprintf("FOLLOW   x=%4d y=%4d (1 px per %d mickeys)   ", fx, fy, fdiv);
        gotoxy(1, 4);
        if (counters)
            cprintf("COUNTERS x=%6d y=%6d  cx=%4d cy=%4d  ", cx, cy, cfx, cfy);
        else
            cprintf("COUNTERS (not read; /C)                    ");
        gotoxy(1, 5); cprintf("PRESS    n=%-3d tick=%-8lu pit=%-5u       ", presses, press_tick, press_pit);
        gotoxy(1, 6); cprintf("KEY      n=%-3d last=%02x tick=%-8lu pit=%-5u", keys, lastkey & 0xFF, key_tick, key_pit);
        if (raw) {
            gotoxy(1, 7);
            cprintf("TOUCH    x=%5d y=%5d down=%d n=%-5d (of 16383)", tx, ty, tdown, tn);
        }
    }
    if (raw)
        outportb(COM + 1, old_ier);       /* the touch driver's interrupts again */
    memset(&r, 0, sizeof r); r.x.ax = 0; int33(&r);   /* drops our handler */
    _go32_dpmi_free_real_mode_callback(&cb);
    return 0;
}
