# Touchscreen input and input testing

These tools are for absolute (touch) input to DOS guests, sent with the server's `tablet` tool,
and for checking how a program sees the mouse. [docs/input-quirks.md](../../docs/input-quirks.md)
explains which input reaches which program.

| File | What it is |
|---|---|
| `INPTEST.EXE`, `inptest.c` | The mouse and keyboard as DOS programs see them: poll, handler position, a pointer kept from mickeys, counters, the raw touchscreen (`/T`), and when the last press and key came (BIOS tick and timer count) |
| `MTMOUSE.COM`, `mtmouse.s` | A small TSR (2.3 KB) that drives the loaded mouse driver from the 3M MicroTouch touchscreen on COM1, including for programs that follow mickeys, such as Warcraft II |

## Setting up a touchscreen

1. **In `86box.cfg`,** under `[Input devices]`, set `tablet_type = microtouch_touchpen`. That's
   86Box's 3M MicroTouch serial touchscreen, on COM1.
2. **In the guest,** load a mouse driver, then a touch driver:
   - **3M's DOSTOUCH** (TouchWare 3.4 for DOS) for most programs. Set `CursorOffset=0` in
     `DOSTOUCH.INI`.
   - **MTMOUSE** for programs that follow mickeys and draw their own pointer, such as Warcraft
     II.
3. **Touch** with `tablet(x, y)`, in screenshot coordinates. Use `release=False` to keep the
   finger down, for drags and dropdowns.

Before the driver loads (the BIOS, boot menus), only the relative `mouse` works.

## Building

```
i586-pc-msdosdjgpp-gcc -O2 -o INPTEST.EXE inptest.c            # needs CWSDPMI.EXE in the guest
i586-pc-msdosdjgpp-as -o mtmouse.o mtmouse.s
i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o MTMOUSE.COM mtmouse.o
```
