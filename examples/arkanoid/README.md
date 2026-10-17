# Arkanoid II: Revenge of DoH (DOS, 1989)

A bot for the DOS version of Arkanoid II on MS-DOS 6.22, run with
`ARK E K 1 G` (EGA, keyboard, one player). It reads the game's state from
guest memory every frame (about 14 ms) and steers the paddle with the
keyboard while time is held between frames.

Best game so far: **386,920**, reaching round 10.

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

- **The extended arrow keys don't move the paddle.** The game only reads the
  keypad codes, so the bot uses numpad 4 and 6.
- **After a game ends, the attract-mode demo takes over.** It plays itself and
  runs through rounds, which looks a lot like a real game. Start a new game
  from the title (`start_game()`), then check the paddle responds.

## Running it

Load `arkbot.py` into a `skictl` session (`examples/skifree/skictl.py`) booted
with `--vm <dir> --dos`, then use `start_game()` and `play_full(ms)`.
