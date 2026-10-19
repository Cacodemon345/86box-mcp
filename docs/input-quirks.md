# Mouse and keyboard input: how programs differ

Input that works in one program can fail in another, because DOS and Windows programs read the
mouse and keyboard in different ways. This file collects what we have found, program by program,
with how to work around it. Add to it when you find more.

Everything here was observed through 86box-mcp, unless it says otherwise.

## How DOS programs read the mouse

A DOS mouse driver (CuteMouse, Microsoft's MOUSE.COM) offers several ways to read the mouse
through INT 33h, and programs pick different ones:

| Way | INT 33h | What the program gets |
|---|---|---|
| **Poll** | function 3 | The driver's pointer position and buttons, when it asks |
| **Handler position** | function 0Ch, CX/DX | The same position, pushed to the program's event handler on each change |
| **Follow mickeys** | function 0Ch, SI/DI | Running counts of raw motion (mickeys). The program keeps its own pointer from them and draws it itself |
| **Counters** | function 0Bh | Motion since the last call. Typical of games that turn a view with the mouse |
| **Raw hardware** | none | The program talks to the serial port or the touch controller itself, with no driver |

`examples/touch/INPTEST.EXE` shows all of these at once, so you can see what a program would see
before trying the program itself. Use `INPTEST /X:0,1278 /Y:0,479` to set a range, as Warcraft II
does, `/F:n` for the pixels per mickey a mickey-following program uses, and `/T` to read the
touchscreen directly.

## What each kind of input reaches

| Input | Poll | Handler position | Follow mickeys | Counters | Raw hardware |
|---|---|---|---|---|---|
| `mouse(dx, dy)`, relative | yes | yes | yes | yes | serial or PS/2 mouse protocol |
| `tablet(x, y)` with 3M's DOSTOUCH | yes | not tested | no: the program's pointer stays behind | not tested | |
| `tablet(x, y)` with MTMOUSE | yes | yes | yes | **no** | |
| `tablet(x, y)`, raw | | | | | MicroTouch Tablet-format reports on COM1 |

So relative motion reaches every program. Touch reaches the ones that use the driver's position.
With MTMOUSE, it also reaches programs that follow mickeys. Programs that read counters
(function 0Bh) ignore touches: use relative motion for those.

## Touchscreen (absolute) input

`tablet(x, y)` touches the screen at a pixel of the frame, the coordinates of `screenshot`.
Three things must be in place, or nothing happens:

1. **The device must be configured in 86Box.** Set `tablet_type = microtouch_touchpen` under
   `[Input devices]` in `86box.cfg` (86Box's 3M MicroTouch serial touchscreen, on COM1 by
   default). Without it, `tablet` fails with "no tablet configured". 86Box keeps running and
   nothing else changes; we tested that.
2. **A driver must be loaded in the guest:** 3M's DOSTOUCH (TouchWare 3.4 for DOS), or MTMOUSE
   from `examples/touch`, loaded after a mouse driver. One of them, not both: each reads the
   touchscreen on COM1. MTMOUSE needs the mouse driver. DOSTOUCH works without one (it provides
   INT 33h itself), but then the relative `mouse` no longer moves the pointer. Windows needs its own touch driver.
3. **The guest must be past the point where the driver loads.** The BIOS setup, boot menus and
   anything before AUTOEXEC.BAT only see the relative mouse, so use `mouse` there.

The relative mouse keeps working alongside a touchscreen: `mouse` goes to the mouse and `tablet`
to the touchscreen.

### Calibration

Real touchscreens need calibrating to the picture. 86Box's MicroTouch starts out 1:1: uncalibrated,
a touch at the centre of the frame reads 8202, 8212 of 0..16383, the centre (tested with
`INPTEST /T`). So no calibration is needed for `tablet` to land where it says.

The emulated controller does implement MicroTouch's calibration commands, and keeps what they set
in its NVRAM, as a real one does. Running a calibration utility (3M's MicroCal) stores a new
mapping that later touches go through, so a wrong calibration shifts every touch. DOSTOUCH can also
map each video mode differently (`CalibrateVideoMode` in DOSTOUCH.INI). If touches land off by a
constant scale or offset, suspect one of these.

A calibration done with touches short of the real corners also leaves **dead edges**: touches
near the edge map past the end of the range and pin there. To tell that apart from a program
limiting the mouse's range (functions 7 and 8): run `INPTEST` with no options, so the range is
the full screen. Dead edges there are calibration; edges that are only dead inside one program
are its range. `INPTEST /T` shows the controller's raw reading, before any driver.

### 3M's DOSTOUCH

DOSTOUCH comes from TouchWare 3.4 (1997) for DOS, which was on 3M's driver CD ("3M Touchscreen
Monitor" on the Internet Archive). It installs under `C:\MTS\TOUCH`. The 2021 "DOS" download on
microtouch.com is a different driver, EETI's TouchKit for eGalax controllers, and doesn't talk to
a MicroTouch.

- **CursorOffset:** DOSTOUCH.INI's default `CursorOffset=25` puts the pointer above the finger
  (one text row up, in text mode). Set `CursorOffset=0` for the pointer to land exactly on the
  touch.
- **TouchMode:** with `TouchMode=0` and `TouchMode=2`, the left button is down while the finger
  is. With `TouchMode=1`, our touches never pressed the button, at least not with touches of
  0.3 s.
- **With or without a mouse driver:** 3M's readme asks for a standard Microsoft driver (it warns
  about some Logitech and Compaq ones). With Microsoft's MOUSE.COM (DOS 6.22's, 8.20) under it,
  with CuteMouse under it, and alone, DOSTOUCH behaved the same in Warcraft II. Alone, the
  relative mouse stopped moving the pointer.
- **Programs that follow mickeys:** see Warcraft II below; touches reach them through the
  driver's position, which the program may clamp.

### Memory

Measured with `MEM /C`, all in conventional memory (none tried in upper memory):

| Setup | Resident |
|---|---|
| CuteMouse 2.1 + MTMOUSE | 3.1 KB + 1.8 KB = 4.9 KB |
| Microsoft MOUSE.COM 8.20 + DOSTOUCH 3.5 | 17.3 KB + 14.9 KB = 32.2 KB |
| DOSTOUCH 3.5 alone | 14.9 KB |

For programs short of conventional memory, CuteMouse + MTMOUSE is much the smallest.

### MTMOUSE

MTMOUSE is ours (`examples/touch/mtmouse.s`, 2.3 KB). It reads the touchscreen on COM1 and drives
the mouse driver already loaded:

- It sets the driver's position with function 4.
- It holds the left button while the finger is down, in functions 3, 5 and 6 and in event-handler
  calls.
- It sends the program's event handler the move in mickeys. Each touch goes as two moves: far up
  and left, so both the driver's pointer and the program's own stop at the corner, then the
  touch's distance from the corner. That keeps working when the two pointers have drifted apart.
- The press arrives two timer ticks (about 110 ms) after the touch, once the program has seen the
  pointer arrive.

It doesn't feed function 0Bh's counters.

## Programs

### Warcraft II (DOS, v1.50 "2X")

- **Touches with DOSTOUCH** (under Microsoft's MOUSE.COM or CuteMouse, the same): the pointer lands
  on the touch, within 3 pixels, but a touch on a new spot only moves it; a second touch there
  clicks. The game seems to click only a button the pointer was already over. In a game, touches
  can't reach the left panel (minimap, unit and command buttons): the pointer stops at x 176, the
  edge of the map view. The game sets the driver's range to the map view while playing, and moves
  its own pointer into the panel from mickeys alone. The relative `mouse` reaches the panel.
  Reproduced in INPTEST under DOSTOUCH: with `/X:352,1278`, touches left of 176 px all read
  x = 352, the range's edge, while touches inside it read the same as with the full range. So it
  is the program's range, not calibration, which would shift or scale every touch.
- **The pointer:** the game follows the handler's mickeys and draws its own pointer, at half a
  pixel per mickey on both axes. The screen is 640x480, but the game sets the driver's range to
  x 0..1278 and y 0..479 (functions 7 and 8): twice the width, so at the driver's default ratios
  (8 mickeys per 8 units across, 16 down) both axes move half a pixel per mickey. It also sets the
  driver's position itself (function 4), so the driver's pointer and the game's drift apart. With
  DOSTOUCH, touches land at the old spot. MTMOUSE's corner-then-target moves fix that.
- **`mouse_to` scrolls the map:** in a game, parking the pointer in a corner scrolls the map.
  Relative moves from a known spot are fine. The minimap re-centres the view, so slam to the
  corner, move onto the minimap and click: the pointer and view are then known.
- **Dropdowns** pick the item under the pointer when the button is released: press with
  `mouse(left=True, release=False)` or `tablet(..., release=False)`, move onto the item, then
  let go.
- **Scroll arrows** repeat while held: a 300 ms press moves several lines, a 120 ms one about
  two.
- **Orders:**
  - A right-click on the minimap does nothing.
  - A right-click into unexplored, black ground seemed to be ignored.
  - A click next to trees means harvest lumber.
  - Move with the Move button, then a left click on the minimap or the map.
- **The lobby:** typed letters go into the chat line. Enter or a click starts things, and a
  click with the pointer not exactly on the button does nothing.
- **Version 1.0's IPX games can't be joined:** use 1.2 or later.

### Arkanoid II (DOS)

The game reads only the keypad keys, not the extended arrow keys.

### Windows 3.x

- **Mouse acceleration:** set Control Panel > Mouse > Mouse Tracking Speed to Slow. Otherwise the
  pointer's gain depends on speed, and drags overshoot.
- **Setup hides the pointer** in its keyboard-driven dialogs, so `mouse_to` can't find it: use
  the keyboard there.
- **Moving clocks** (Solitaire's status bar) can fool `mouse_to`'s frame comparison.

## Others with the same problems

The way programs keep their own pointer from mickeys trips up other absolute-mouse projects too:

- [pcrecomp #76](https://github.com/sp00nznet/pcrecomp/pull/76) found Theme Park's cursor drifting
  with an absolute pointer, because the game moves its cursor by how far the driver's position
  changed, and its edge scrolling needs moves past the edge. They added relative motion alongside.
- [VBMOUSE](https://git.javispedro.com/cgit/vbados.git/about/), the DOS mouse driver for VirtualBox
  and VMware (about 10 KB), notes that programs which draw their own cursor or compute the position
  from relative motion go "jumpy" or stuck with absolute input, and turns it off for them
  (`vbmouse integ off`).

## Keyboard

- **Key events take time:** 86Box delivers key events 2 ms of emulated time apart, so a burst
  doesn't overflow the keyboard controller, and every key costs 2 ms.
- **Polled programs see keys late:** a program that polls the keyboard sees a key within one pass
  of its loop, not when the key arrived. In INPTEST that spread keys across eight VMs by about a
  millisecond, though they arrived together.

## Several VMs at once

`together(actions)` gives several VMs input at the same emulated instant, different input for
each if you like. Tested with INPTEST on eight VMs:

- **Touch:** the same touch on all eight reached each handler within about 30 µs of emulated
  time, and a different touch on each landed on each VM's own spot.
- **The BIOS clock** starts from the host's clock at boot. VMs booted a second apart differ by a
  constant number of BIOS ticks, so compare each VM against its own clock.
