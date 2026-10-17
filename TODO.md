# To do

## 86Box

- **`step` while the UI is paused.** While the UI's pause is on, `step` waits
  until it's released. Should an automation step override the pause instead?
  #8365 asked, and the merge didn't settle it. Any change would be a new
  86Box pull request (see [docs/UPSTREAM.md](docs/UPSTREAM.md)).
- **Run an MSYS2-built 86Box on Windows.** Upstream's MSYS2 workflow builds
  with `-D AUTOMATION=ON`, but only our MinGW cross-build has actually run
  there.

## MCP server

- **Run the MCP server itself on Windows,** including its 86Box launcher and
  disk tooling. So far 86Box has run on Windows and been driven from Linux.
- **Try macOS** at all.
- **Reproducible CD images.** `cdrom_insert` builds ISOs with pycdlib, which
  stamps the volume dates with the current time. Hard-disk and floppy images
  are already reproducible.
- **COM1 capture by default.** It's opt-in because runs once looked
  non-reproducible with it on. With the disk-image timestamps fixed, 40 of 40
  paired runs matched. Decide whether to turn it on by default.

## Examples

- **Arkanoid II bot:** see the to-do list in
  [examples/arkanoid/README.md](examples/arkanoid/README.md#to-do) (brick
  map, capsule letters, lives, ball slots).
