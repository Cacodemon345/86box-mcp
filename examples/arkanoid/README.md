# Arkanoid II: Revenge of DoH (DOS, 1989)

A bot for the DOS version of Arkanoid II on MS-DOS 6.22, run with
`ARK E K 1 G` (EGA, keyboard, one player). It reads the game's state from
guest memory every frame (about 14 ms) and steers the paddle with the
keyboard while time is held between frames.

Best games so far: **386,920** (round 10) and **207,790** (round 5).

## How it plays

- **Prediction:** it predicts where each falling ball will reach the paddle,
  bouncing off the side walls, and moves there early. The paddle is only about
  as fast as the ball.
- **Aiming:** where the ball meets the paddle sets its new direction (left edge
  shallow left, centre steep, right edge shallow right). The bot cycles through
  eight hit positions, so successive bounces sweep the field and reach the last
  bricks.
- **Capsules:** these are worth 1,000 points each. While the ball is heading up
  and high, the bot moves under any falling capsule.

## Memory map (as loaded on this VM)

| What | Where |
|---|---|
| Paddle x | byte at `0x23358`: 8 at the left wall, 186 at the right |
| Score | space-padded ASCII digits at `0x233DD` |
| Objects | 33-byte records from `0x237DF`: x, y as 8.8 fixed point, then dx and dy in 1/256ths. The first three are the ball slots; later records hold other objects, and falling capsules among them have dx = 0. |

The ball's x runs from about 10.6 to 208.8 between the walls. It meets the
paddle at y ≈ 180, and a centred hit is ball x − paddle x ≈ 14.

## Things learned the hard way

- **Injected keys don't auto-repeat, and the game keeps only the last key
  event.** So tapping fire (to shoot the laser or release a caught ball) while
  a direction key is held stops the paddle. The bot presses the held key again
  after every tap; before that fix, a game lost all its lives in seconds.
- **Aim with the middle of the paddle.** Aiming uses the paddle's edges, which
  leaves no margin with fast balls, or when a capsule has shrunk the paddle.
  The bot keeps hits within a few units of the centre.
- **A ball that stops moving at the paddle has been caught** (the Catch
  capsule). Firing releases it.
- **`0x233DD` is a display buffer.** It usually holds the player's score, but
  sometimes the high score; `score()` skips that value.

- **The extended arrow keys don't move the paddle.** The game only reads the
  keypad codes, so the bot uses numpad 4 and 6.
- **After a game ends, the attract-mode demo takes over.** It plays itself and
  runs through rounds, which looks a lot like a real game. Start a new game
  from the title (`start_game()`), then check the paddle responds.

## Running it

Load `arkbot.py` into a `skictl` session (`examples/skifree/skictl.py`) booted
with `--vm <dir> --dos`, then use `start_game()` and `play_full(ms)`.

## To do

What would raise the score most, roughly in order:

- **Disassemble the game.** It's the quickest way to get the items below for
  certain, rather than guessing from memory diffs.
- **Find the brick map,** and aim at the remaining bricks instead of sweeping
  angles. Last bricks are where rounds stall: one silver brick took 95 seconds.
  The 13-wide grid near `0x23261` turned out not to be it.
- **Read capsule letters** and skip the bad ones. Some misses happened with the
  paddle in position, which looks like a shrunken paddle from the R capsule;
  the bot catches every capsule. Catch (C) could also be used to hold the ball
  and aim precisely.
- **Find the lives counter.** `0x2CA6A` isn't reliable, so losses are spotted
  indirectly, from no ball moving for a while.
- **Find the real score variable.** `0x233DD` is a display buffer.
- **Confirm the ball slots** during a multi-ball. The bot reads only the first
  three object records; more balls may use later ones.
- **Handle fast deflections near the paddle,** for example by staying under
  balls that could come down quickly, not only the predicted one.
