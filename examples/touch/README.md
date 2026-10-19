# Pointer and touchscreen input, and input testing

These tools put the pointer where you want it in DOS guests, whichever way a program reads the
mouse (with the server's `mouse_to`), take touches from 86Box's touchscreen (`tablet`), and show how
a program sees the mouse. [docs/input-quirks.md](../../docs/input-quirks.md)
explains which input reaches which program.

| File | What it is |
|---|---|
| `INPTEST.EXE`, `inptest.c` | The mouse and keyboard as DOS programs see them: poll, handler position, a pointer kept from mickeys, counters, the raw touchscreen (`/T`), and when the last press and key came (BIOS tick and timer count) |
| `MOUSEBOX.COM`, `mousebox.s` | A small TSR (about 2 KB resident), loaded after the mouse driver. `mouse_to` puts the pointer where it says through a mailbox in its memory, for every way programs read the mouse, including programs that draw their own pointer from mickeys such as Warcraft II. `/T` also takes the 3M MicroTouch touchscreen on COM1 |

## Setting up a touchscreen

1. **In `86box.cfg`,** under `[Input devices]`, set `tablet_type = microtouch_touchpen`. That's
   86Box's 3M MicroTouch serial touchscreen, on COM1.
2. **In the guest,** load a mouse driver, then one touch driver, not both (each reads COM1):
   - **3M's DOSTOUCH** (TouchWare 3.4 for DOS) for most programs. Set `CursorOffset=0` in
     `DOSTOUCH.INI`.
   - **`MOUSEBOX /T`** for programs that follow mickeys and draw their own pointer, such as
     Warcraft II.
3. **Touch** with `tablet(x, y)`, in screenshot coordinates. Use `release=False` to keep the
   finger down, for drags and dropdowns.

Before the driver loads (the BIOS, boot menus), only the relative `mouse` works.

## Pointing without a touchscreen

Load `C:\MOUSEBOX.COM` after the mouse driver in AUTOEXEC.BAT. `mouse_to(x, y)` then finds it and
puts the pointer there; click with `mouse(left=True)`. No device to configure in 86Box.

## Building

```
i586-pc-msdosdjgpp-gcc -O2 -o INPTEST.EXE inptest.c            # needs CWSDPMI.EXE in the guest
i586-pc-msdosdjgpp-as -o mousebox.o mousebox.s
i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o MOUSEBOX.COM mousebox.o
```
