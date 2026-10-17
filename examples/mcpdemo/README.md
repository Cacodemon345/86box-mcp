# MCPDEMO

A tiny DOS 6.22 game for exercising every 86box MCP tool, installed to and run
from C: on a VM whose disk matches a 512 MB CompactFlash card.

- **Text mode:** asks for your name (`type_text`, `wait_for_text`, `screen_text`).
- **Arena (VGA mode 13h):** arrows move the green square (Shift = fast),
  collect yellow coins, avoid the red chaser, click the blue button with the
  mouse, Esc quits (`act`, `press_keys`, `mouse_to`, `mouse`, `screenshot`,
  `record_start`).
- **COM1 log:** start, coins, clicks, hits and exit are logged to COM1 at
  115200 baud (`serial_read`, `wait_for_serial`).
- **State block:** every tick the game copies its state to conventional
  memory. `0000:04F0` holds `"MCPD"` plus the block's segment; the block
  starts with `"MCPDEMO1"`. Layout is the `demo_state_t` struct in
  `mcpdemo.c`. Read it with `read_memory`; wait on it with `wait_for_memory`.
- **Mailbox:** write `arg1`/`arg2` (offsets 0x3E/0x40), then `cmd` (0x3C):
  1 = move coin to (arg1, arg2), 2 = set lives to arg1, 3 = freeze chaser if
  arg1 is nonzero. The game zeroes `cmd` when it's done (`write_memory`, then
  `wait_for_memory(addr + 0x3C, "0000")`).

```sh
CWSDPMI=/path/CWSDPMI.EXE CTMOUSE=/path/CTMOUSE.EXE ./build.sh   # DJGPP in ~/djgpp, or set DJGPP_DIR
../../server/.venv/bin/python drive.py      # builds out/vm, runs 34 checks, saves out/*.png and a recording
```

The same techniques fit a real game. Publish a state block (or log to COM1),
then test against exact numbers rather than pixels. `mouse_to` places the
pointer exactly when given the address of the game's pointer coordinates
(`pointer_addr`); without it, it finds the pointer on screen.
