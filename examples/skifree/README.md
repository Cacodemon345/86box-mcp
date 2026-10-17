# SkiFree 1.0 on Windows 3.11

Bots for the original 16-bit SkiFree, driven over the automation socket. They
read the game's state from guest memory each tick and play with the keyboard,
with time held between ticks.

Results:

| Run | Result |
|---|---|
| **Freestyle score** | **32767**, the largest value of the game's 16-bit signed style score, entered in the High Scores box |
| **Distance** | survived the Abominable Snowman past 2000 m, through the distance wrapping round (y is a 16-bit short) and back to the course; eaten on the next lap at 2047 m |
| **Scoring events** | all four trick poses, scaring a dog, making a novice crash. A snowboarder crash, a magic mushroom and a burning-tree jump weren't caught on the recorded runs |

## How the score works

The rules come from
[the decompiled 32-bit SkiFree 1.04](https://github.com/yuv422/skifree_decomp),
checked against 1.0 in guest memory. In 1.0, `int` is 16 bits.

- **Trick poses score every game tick in the air:**
  - backscratcher (`0x14`/`0x15`): 8 points;
  - head-over-heels (`0x12`/`0x13`): 4 points;
  - helicopter (`0x10`): 2 points.

  Plain air scores nothing in 1.0, though Microsoft's KB article says 1.
- **A hop (Insert) works from a standstill.** It lasts four ticks. Landing in
  pose `0xe` leaves you standing still (pose 3) with no fall.
- **Drift:** speed builds up in the air, so repeated hops drift down the slope.
  An uphill step (numpad 8) in the same tick as the hop sends it uphill
  instead, about 9 units per hop.
- **The best run:**
  1. Hop down the whole Freestyle course: about 26,000 points.
  2. Hop in place near the bottom until close to the target.
  3. Finish exactly: choose each air tick's pose (8, 4, 2 or 0).
  4. Ski straight across the finish line.

## Memory map (16-bit SkiFree 1.0, as loaded on this VM)

| What | Where |
|---|---|
| Data segment | physical `0x493000`. Actor pointers are offsets into it. |
| Style points | `0x493B7E` (int16) |
| Live actor list head | `0x493B7C` |
| Game tick (`GetTickCount()` at the last update) | `0x493B12` |
| Player actor | `0x4980D4`: 46 bytes, x at +32, y at +34, height at +36, then vx, vy and the jump counter |
| Sprite table | data segment + `0x4CBC`: 12-byte records, width at +6, height at +8 |

The actor types are one less than in 1.04:

| Type | Object |
|---|---|
| 4 | lift chair |
| 9 | burning tree |
| 10 | moguls |
| 11 | lift pole |
| 12 | tree (sprite 50 can be set on fire) |
| 13 | rock or stump |
| 14 | bump |
| 15 | ramp |
| 16 | sign |

## Files

| File | What it does |
|---|---|
| `skictl.py` | Boots the VM with Windows, then runs Python snippets against it over a local socket, so the machine stays up between experiments |
| `skilib.py`, `skibot.py` | Launching SkiFree; reading the state; stepping exactly one game tick (and pressing OK on dialogs) |
| `skiplan.py` | Stopping, walking, and skiing round obstacles |
| `skirun.py` | The score run |
| `technical.py` | The scoring-event hunt |
| `maxlen.py` | The distance run |
| `render_segments.py` | Joins pieces of recordings into one video |

```sh
python skictl.py serve --record /path/to/frames &
python skictl.py run 'exec(open("skilib.py").read()); launch()'
```

The VM is the Windows for Workgroups 3.11 one from the Solitaire example,
with `SKI.EXE` copied to `C:\SKI`.
