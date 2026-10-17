/*
 * MCPDEMO - a tiny DOS game for exercising the 86Box MCP controls.
 *
 * Text mode: asks for a name (type_text, wait_for_text, screen_text).
 * Graphics:  mode 13h arena. Arrow keys move the green player (Shift = fast),
 *            collect yellow coins, avoid the red chaser. Click the blue
 *            button with the mouse. Esc quits. (act, mouse, screenshot)
 *
 * State block: the game publishes its state to conventional memory every tick
 * so a test harness can read it with read_memory instead of reading pixels.
 * The BIOS inter-application area at 0000:04F0 holds "MCPD" followed by the
 * real-mode segment of the block; the block itself starts with "MCPDEMO1".
 * The harness can also post a command into the block (write_memory); the game
 * executes it and clears cmd (wait_for_memory).
 *
 * Build: i586-pc-msdosdjgpp-gcc -O2 -Wall -o MCPDEMO.EXE mcpdemo.c
 * Needs CWSDPMI.EXE alongside, and a mouse driver (CTMOUSE) for the button.
 */
#include <bios.h>
#include <conio.h>
#include <crt0.h>
#include <dpmi.h>
#include <go32.h>
#include <pc.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/farptr.h>
#include <sys/movedata.h>
#include <time.h>

/* The keyboard ISR touches these, so keep everything locked in memory. */
int _crt0_startup_flags = _CRT0_FLAG_LOCK_MEMORY;

#define W         320
#define H         200
#define HUD_H     12
#define TICK_HZ   35
#define PLAYER_SZ 8
#define COIN_SZ   6
#define ENEMY_SZ  8
#define BTN_X     256
#define BTN_Y     HUD_H + 4
#define BTN_W     56
#define BTN_H     16

enum { PHASE_TEXT = 0, PHASE_ARENA = 1, PHASE_DONE = 2 };
enum { CMD_NONE = 0, CMD_MOVE_COIN = 1, CMD_SET_LIVES = 2, CMD_FREEZE_ENEMY = 3 };

/* Key bits in state.keys. */
#define K_UP    0x01
#define K_DOWN  0x02
#define K_LEFT  0x04
#define K_RIGHT 0x08
#define K_SHIFT 0x10
#define K_ESC   0x20

/* Layout is part of the harness contract: see README.md. All little-endian. */
typedef struct __attribute__((packed)) {
    char     sig[8];      /* 0x00 "MCPDEMO1" */
    uint32_t ticks;       /* 0x08 game ticks at 35 Hz */
    uint16_t phase;       /* 0x0C 0 text, 1 arena, 2 done */
    int16_t  player_x;    /* 0x0E top-left, pixels */
    int16_t  player_y;    /* 0x10 */
    int16_t  coin_x;      /* 0x12 */
    int16_t  coin_y;      /* 0x14 */
    int16_t  enemy_x;     /* 0x16 */
    int16_t  enemy_y;     /* 0x18 */
    int16_t  mouse_x;     /* 0x1A 0..319 */
    int16_t  mouse_y;     /* 0x1C 0..199 */
    uint16_t mouse_btn;   /* 0x1E bit0 left, bit1 right */
    uint16_t score;       /* 0x20 coins collected */
    uint16_t lives;       /* 0x22 */
    uint16_t clicks;      /* 0x24 button clicks */
    uint16_t keys;        /* 0x26 K_* bits currently held */
    uint16_t hits;        /* 0x28 times caught by the chaser */
    uint16_t has_mouse;   /* 0x2A mouse driver present */
    char     name[16];    /* 0x2C NUL-terminated */
    /* Harness -> game mailbox. Write args first, then cmd; game zeroes cmd. */
    uint16_t cmd;         /* 0x3C */
    int16_t  arg1;        /* 0x3E */
    int16_t  arg2;        /* 0x40 */
    uint16_t cmds_done;   /* 0x42 count of executed commands */
} demo_state_t;

static demo_state_t st;
static int          dos_seg;     /* real-mode segment of the published block */
static int          dos_sel;
static uint8_t      frame[W * H];
static uint8_t      font[128 * 8];
static uint32_t     rng = 12345;
static int          enemy_frozen;

static volatile uint8_t keydown[128];
static volatile int     kb_e0;
static _go32_dpmi_seginfo old_kb, new_kb;

/* ---- serial log (COM1 via BIOS INT 14h) ---- */

/* The BIOS leaves COM1 slow (each log line would stall the game for
   ~100 ms); program the UART for 115200 8N1 directly. */
static void
serial_init(void)
{
    outportb(0x3fb, 0x80); /* DLAB on */
    outportb(0x3f8, 0x01); /* divisor 1 = 115200 */
    outportb(0x3f9, 0x00);
    outportb(0x3fb, 0x03); /* 8N1, DLAB off */
    outportb(0x3fc, 0x03); /* DTR, RTS */
}

static void
serial_log(const char *fmt, ...)
{
    char        line[96];
    va_list     ap;
    __dpmi_regs r;

    va_start(ap, fmt);
    vsnprintf(line, sizeof(line) - 2, fmt, ap);
    va_end(ap);
    strcat(line, "\r\n");
    for (const char *p = line; *p; p++) {
        memset(&r, 0, sizeof(r));
        r.h.ah = 0x01; /* send character */
        r.h.al = *p;
        r.x.dx = 0;    /* COM1 */
        __dpmi_int(0x14, &r);
        if (r.h.ah & 0x80)
            return; /* timeout: nothing attached */
    }
}

/* ---- keyboard ---- */

static void
kb_isr(void)
{
    uint8_t sc = inportb(0x60);

    if (sc == 0xe0) {
        kb_e0 = 1;
        return;
    }
    /* Skip the fake shifts that extended keys generate. */
    if (!(kb_e0 && ((sc & 0x7f) == 0x2a || (sc & 0x7f) == 0x36)))
        keydown[sc & 0x7f] = !(sc & 0x80);
    kb_e0 = 0;
}

static void
kb_install(void)
{
    _go32_dpmi_get_protected_mode_interrupt_vector(9, &old_kb);
    new_kb.pm_offset   = (unsigned long) kb_isr;
    new_kb.pm_selector = _go32_my_cs();
    /* Chain so the BIOS still acknowledges the interrupt and fills its buffer. */
    _go32_dpmi_chain_protected_mode_interrupt_vector(9, &new_kb);
}

static void
kb_remove(void)
{
    _go32_dpmi_set_protected_mode_interrupt_vector(9, &old_kb);
    /* Drain the BIOS buffer directly. getch() reads through DOS, which keeps the
       second byte of an extended key (e.g. Down = 0x50 'P') for the next reader:
       COMMAND.COM. */
    while (bioskey(1))
        bioskey(0);
}

static uint16_t
kb_bits(void)
{
    uint16_t k = 0;

    if (keydown[0x48]) k |= K_UP;
    if (keydown[0x50]) k |= K_DOWN;
    if (keydown[0x4b]) k |= K_LEFT;
    if (keydown[0x4d]) k |= K_RIGHT;
    if (keydown[0x2a] || keydown[0x36]) k |= K_SHIFT;
    if (keydown[0x01]) k |= K_ESC;
    return k;
}

/* ---- mouse (INT 33h) ---- */

static int
mouse_init(void)
{
    __dpmi_regs r;

    memset(&r, 0, sizeof(r));
    r.x.ax = 0x0000;
    __dpmi_int(0x33, &r);
    if (r.x.ax != 0xffff)
        return 0;
    /* 1:1 mickeys per pixel and no acceleration, so moves are predictable. */
    r.x.ax = 0x000f;
    r.x.cx = 8;
    r.x.dx = 8;
    __dpmi_int(0x33, &r);
    r.x.ax = 0x0013;
    r.x.dx = 0x7fff;
    __dpmi_int(0x33, &r);
    /* Mode 13h reports x in 0..639. */
    r.x.ax = 0x0007;
    r.x.cx = 0;
    r.x.dx = 639;
    __dpmi_int(0x33, &r);
    r.x.ax = 0x0008;
    r.x.cx = 0;
    r.x.dx = 199;
    __dpmi_int(0x33, &r);
    r.x.ax = 0x0004;
    r.x.cx = 320;
    r.x.dx = 100;
    __dpmi_int(0x33, &r);
    return 1;
}

static void
mouse_read(void)
{
    __dpmi_regs r;

    memset(&r, 0, sizeof(r));
    r.x.ax = 0x0003;
    __dpmi_int(0x33, &r);
    st.mouse_x   = r.x.cx / 2;
    st.mouse_y   = r.x.dx;
    st.mouse_btn = r.x.bx & 3;
}

/* ---- video ---- */

static void
set_mode(int mode)
{
    __dpmi_regs r;

    memset(&r, 0, sizeof(r));
    r.x.ax = mode;
    __dpmi_int(0x10, &r);
}

static void
load_font(void)
{
    __dpmi_regs r;

    /* BIOS 8x8 font, characters 0-127. */
    memset(&r, 0, sizeof(r));
    r.x.ax = 0x1130;
    r.h.bh = 0x03;
    __dpmi_int(0x10, &r);
    dosmemget(r.x.es * 16 + r.x.bp, sizeof(font), font);
}

static void
rect(int x, int y, int w, int h, uint8_t c)
{
    for (int yy = y; yy < y + h; yy++) {
        if (yy < 0 || yy >= H)
            continue;
        for (int xx = x; xx < x + w; xx++) {
            if (xx >= 0 && xx < W)
                frame[yy * W + xx] = c;
        }
    }
}

static void
text(int x, int y, uint8_t c, const char *s)
{
    for (; *s; s++, x += 8) {
        const uint8_t *g = &font[(*s & 0x7f) * 8];
        for (int row = 0; row < 8; row++) {
            for (int col = 0; col < 8; col++) {
                if ((g[row] & (0x80 >> col)) && x + col < W && y + row < H)
                    frame[(y + row) * W + x + col] = c;
            }
        }
    }
}

static void
wait_vsync(void)
{
    while (inportb(0x3da) & 8)
        ;
    while (!(inportb(0x3da) & 8))
        ;
}

/* ---- game ---- */

static int
rnd(int n)
{
    rng = rng * 1103515245u + 12345u;
    return (rng >> 16) % n;
}

static int
overlap(int ax, int ay, int as, int bx, int by, int bs)
{
    return ax < bx + bs && bx < ax + as && ay < by + bs && by < ay + as;
}

static void
place_coin(void)
{
    st.coin_x = 8 + rnd(W - 16 - COIN_SZ);
    st.coin_y = HUD_H + 24 + rnd(H - HUD_H - 32 - COIN_SZ);
}

static void
respawn(void)
{
    st.player_x = W / 2 - PLAYER_SZ / 2;
    st.player_y = H / 2;
    st.enemy_x  = 4;
    st.enemy_y  = H - ENEMY_SZ - 4;
}

/* Publish everything before the mailbox; the mailbox belongs to the harness
   until take_command() acknowledges it. */
static void
publish(void)
{
    dosmemput(&st, offsetof(demo_state_t, cmd), dos_seg * 16);
}

/* Pick up a command posted by the harness into the published block. */
static void
take_command(void)
{
    demo_state_t in;

    dosmemget(dos_seg * 16, sizeof(in), &in);
    if (in.cmd == CMD_NONE)
        return;

    switch (in.cmd) {
        case CMD_MOVE_COIN:
            st.coin_x = in.arg1;
            st.coin_y = in.arg2;
            break;
        case CMD_SET_LIVES:
            st.lives = in.arg1;
            break;
        case CMD_FREEZE_ENEMY:
            enemy_frozen = in.arg1;
            break;
        default:
            break;
    }
    st.cmds_done++;
    st.cmd = CMD_NONE;
    dosmemput(&st.cmds_done, sizeof(st.cmds_done), dos_seg * 16 + offsetof(demo_state_t, cmds_done));
    dosmemput(&st.cmd, sizeof(st.cmd), dos_seg * 16 + offsetof(demo_state_t, cmd));
}

static void
tick(void)
{
    static int was_down;
    int        speed;
    int        click;

    st.ticks++;
    st.keys = kb_bits();
    speed   = (st.keys & K_SHIFT) ? 4 : 2;
    if (st.has_mouse)
        mouse_read();

    if (st.keys & K_UP)    st.player_y -= speed;
    if (st.keys & K_DOWN)  st.player_y += speed;
    if (st.keys & K_LEFT)  st.player_x -= speed;
    if (st.keys & K_RIGHT) st.player_x += speed;
    if (st.player_x < 0) st.player_x = 0;
    if (st.player_y < HUD_H) st.player_y = HUD_H;
    if (st.player_x > W - PLAYER_SZ) st.player_x = W - PLAYER_SZ;
    if (st.player_y > H - PLAYER_SZ) st.player_y = H - PLAYER_SZ;

    /* The chaser moves 1 px per tick on each axis, every other tick. */
    if (!enemy_frozen && (st.ticks & 1)) {
        st.enemy_x += (st.player_x > st.enemy_x) - (st.player_x < st.enemy_x);
        st.enemy_y += (st.player_y > st.enemy_y) - (st.player_y < st.enemy_y);
    }

    if (overlap(st.player_x, st.player_y, PLAYER_SZ, st.coin_x, st.coin_y, COIN_SZ)) {
        st.score++;
        serial_log("COIN score=%u tick=%lu", st.score, (unsigned long) st.ticks);
        place_coin();
    }
    if (overlap(st.player_x, st.player_y, PLAYER_SZ, st.enemy_x, st.enemy_y, ENEMY_SZ)) {
        st.hits++;
        serial_log("HIT lives=%u tick=%lu", st.lives ? st.lives - 1 : 0, (unsigned long) st.ticks);
        if (st.lives)
            st.lives--;
        respawn();
    }

    click = st.mouse_btn & 1;
    if (click && !was_down && st.mouse_x >= BTN_X && st.mouse_x < BTN_X + BTN_W
        && st.mouse_y >= BTN_Y && st.mouse_y < BTN_Y + BTN_H) {
        st.clicks++;
        serial_log("CLICK clicks=%u tick=%lu", st.clicks, (unsigned long) st.ticks);
    }
    was_down = click;
}

static void
draw(void)
{
    char hud[48];

    memset(frame, 0, sizeof(frame));
    rect(0, 0, W, HUD_H, 8);
    snprintf(hud, sizeof(hud), "SCORE %u  LIVES %u  CLICKS %u", st.score, st.lives, st.clicks);
    text(2, 2, 15, hud);

    rect(BTN_X, BTN_Y, BTN_W, BTN_H, (st.mouse_btn & 1) ? 9 : 1);
    text(BTN_X + 8, BTN_Y + 4, 15, "CLICK");

    rect(st.coin_x, st.coin_y, COIN_SZ, COIN_SZ, 14);
    rect(st.enemy_x, st.enemy_y, ENEMY_SZ, ENEMY_SZ, 4);
    rect(st.player_x, st.player_y, PLAYER_SZ, PLAYER_SZ, 10);

    if (st.has_mouse) {
        rect(st.mouse_x - 3, st.mouse_y, 7, 1, 15);
        rect(st.mouse_x, st.mouse_y - 3, 1, 7, 15);
    }

    wait_vsync();
    dosmemput(frame, sizeof(frame), 0xa0000);
}

static void
read_name(void)
{
    int len = 0;

    cputs("Your name? ");
    for (;;) {
        int c = getch();
        if (c == '\r' && len)
            break;
        if (c == '\b' && len) {
            len--;
            cputs("\b \b");
        } else if (c >= ' ' && c < 127 && len < (int) sizeof(st.name) - 1) {
            st.name[len++] = c;
            putch(c);
        }
    }
    st.name[len] = '\0';
    cputs("\r\n");
}

int
main(void)
{
    uclock_t next;
    uclock_t step = UCLOCKS_PER_SEC / TICK_HZ;

    memcpy(st.sig, "MCPDEMO1", 8);
    st.lives = 3;
    st.phase = PHASE_TEXT;

    dos_seg = __dpmi_allocate_dos_memory((sizeof(st) + 15) / 16, &dos_sel);
    if (dos_seg < 0) {
        cputs("Out of conventional memory\r\n");
        return 1;
    }
    /* Advertise the block in the BIOS inter-application area (0000:04F0). */
    dosmemput("MCPD", 4, 0x4f0);
    _farpokew(_dos_ds, 0x4f4, dos_seg);
    dosmemput(&st, sizeof(st), dos_seg * 16);

    textcolor(LIGHTGREEN);
    cputs("MCPDEMO - 86Box MCP control demo\r\n");
    textcolor(LIGHTGRAY);
    cprintf("State block at %04X:0000 (physical 0x%05X), pointer at 0000:04F0\r\n\r\n",
            dos_seg, dos_seg * 16);
    read_name();
    cprintf("Hello, %s! Arrows move, Shift is fast, click the button, Esc quits.\r\n", st.name);
    cputs("Press any key to start.");
    getch();

    serial_init();
    serial_log("MCPDEMO start name=%s state=%05X", st.name, dos_seg * 16);
    st.has_mouse = mouse_init();
    load_font();
    respawn();
    place_coin();
    set_mode(0x13);
    kb_install();
    st.phase = PHASE_ARENA;
    publish();

    next = uclock();
    while (!(st.keys & K_ESC) && st.lives) {
        while (uclock() < next)
            ;
        next += step;
        take_command();
        tick();
        publish();
        draw();
    }

    kb_remove();
    set_mode(0x03);
    st.phase = PHASE_DONE;
    publish();

    serial_log("EXIT score=%u clicks=%u hits=%u ticks=%lu", st.score, st.clicks, st.hits, (unsigned long) st.ticks);
    cprintf("Thanks for playing, %s.\r\n", st.name);
    cprintf("Score %u, clicks %u, hits %u, commands %u, ticks %lu.\r\n",
            st.score, st.clicks, st.hits, st.cmds_done, (unsigned long) st.ticks);
    cputs(st.lives ? "You quit.\r\n" : "GAME OVER\r\n");

    _farpokel(_dos_ds, 0x4f0, 0);
    __dpmi_free_dos_memory(dos_sel);
    return st.lives ? 0 : 2;
}
