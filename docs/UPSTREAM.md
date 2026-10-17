# Changes to 86Box

Everything this project needs from 86Box lives on the `automation-socket`
branch of the fork at https://github.com/dafugg/86Box (cloned into `86box/`,
with `upstream` pointing at 86Box/86Box). Keep this list current: it is the
checklist for an upstream pull request.

The branch is three commits on top of upstream `master`, and they go upstream
as two pull requests:

| Branch | Commits | Pull request |
|---|---|---|
| `automation-pr` | `mouse: let input through when an automation client sends it`, then `Add an opt-in automation control socket` | The automation socket |
| `char-file-flush` | `char_file: add an option to write output to the file immediately` | A separate small PR; 86box-mcp's COM1 capture turns the option on |
| `automation-socket` | both of the above | What 86box-mcp builds against (`86BOX_COMMIT`) |
| `ci-automation-pr` | `automation-pr` plus a CI commit | Test only: upstream's Linux, macOS and Windows workflows with `-D AUTOMATION=ON` |

The branch before the rebase onto 2026-10-16 `master` is kept as the tag
`automation-socket-pre-rebase`. [automation-code-guide.md](automation-code-guide.md)
walks through the code for review.

The automation socket is machine-level, not DOS-specific: keyboard input
enters the emulated keyboard controller as scancodes, mouse input goes to the
configured mouse device, and frames come from the emulated video card. Only
`text` / `wait text` assume a BIOS text mode.

The feature is opt-in twice: compile time (`-D AUTOMATION=ON`, default OFF)
and run time (`--automation <socket>`). With the option off, the hooks are
empty macros, so default builds are unchanged.

| File | Change | Why |
|---|---|---|
| `src/automation.c` (new) | Socket listener thread and command handlers | The feature itself; frontend-agnostic C |
| `src/include/86box/automation.h` (new) | API, plus no-op macros when disabled | Same pattern as `gdbstub.h` |
| `doc/specifications/automation.md` (new) | Protocol reference | Next to upstream's `86box-unit-tester.md` |
| `CMakeLists.txt` | `option(AUTOMATION ...)` | Opt-in build flag |
| `src/CMakeLists.txt` | Adds `automation.c`, defines `USE_AUTOMATION` | Same pattern as GDBSTUB |
| `src/86box.c` | `--automation`, `--automation-hold` args; `automation_init()` at the end of `pc_init_modules()`; `automation_close()` in `pc_close()` | Lifecycle |
| `src/qt/qt_main.cpp` | `main_thread_fn`: poll the socket each pass; honor hold/run state; call `automation_quantum()` after `pc_run()`; zero `debt_ns` after unpaced steps | Exact stepping in the Qt frontend |
| `src/unix/sdl_main.c` | Same hooks in the SDL main loop | Exact stepping in the SDL frontend |
| `src/video/video.c` | `automation_frame()` in `video_blit_memtoscreen_monitor()` | Capture the frame at the instant it is finished, independent of the renderer; also feeds `record` |
| `src/cpu/x86.c` | `automation_reset(hard)` in `reset_common()` | Count every CPU reset, including guest-triggered ones (triple faults), for `info` |
| `src/device/mouse.c`, `include/86box/mouse.h` | New `mouse_automation` flag | Let injected motion reach the emulated mouse without faking the UI's capture state |
| `src/device/mouse_ps2.c`, `mouse_serial.c`, `mouse_bus.c` | Accept input when `mouse_capture \|\| mouse_automation` | Same; five one-line conditions |
| `src/char/char_file.c` | A "Write to the file immediately" option (`flush`, off by default) on the COM and LPT File devices | The File backend's output can be followed live. Separate PR (`char-file-flush`) |

## Behaviors worth discussing upstream

- **Mouse capture.** Mouse devices drop input unless the UI has captured the
  pointer. Setting `mouse_capture` from automation broke a visible window (the
  UI hid the host cursor and forwarded host motion), so injected input sets a
  separate `mouse_automation` flag that the devices also accept.
- **Key pacing** (below) and **recording**: `record start <interval> <dir>`
  writes PNGs from the frame hook, on the CPU thread, so a slow disk slows
  emulation slightly while recording.
- **`char_file` flush**: independent of automation, so it's a separate PR. It's an option, off by default, because both File devices write a byte at a time and a flush per byte only suits people who want the file live. Its label needs adding to the translations.
- **Key pacing.** Injecting several key events in the same instant overflows
  the keyboard's 16-byte FIFO (extended keys plus Num Lock fake shifts are up
  to 6 bytes each) and silently loses keys. The `key` command queues events
  and delivers them 2 ms of emulated time apart.

## Transports

`--automation unix:<path>` or `tcp:[host:]port`. A bare value means a path on
Linux/macOS and a TCP port on Windows. TCP binds loopback only unless
`--automation-allow-remote` is given. It always requires a per-run random
token (OS RNG, owner-only token file, constant-time compare, only 8
characters logged). It links `ws2_32` on Windows.

The Windows build has been run. We cross-compiled it with Fedora's MinGW
toolchain (`docker/windows/build.sh`) and drove it over TCP from Linux: boot,
input, screen text, inline screenshots, memory and registers, held and
real-time running, and `quit`, which exits and deletes the token file.

That cross build needed workarounds that are not part of the patch:
- a `Windows.h` and `SetupAPI` case shim (upstream relies on Windows'
  case-insensitive filesystem);
- a placeholder `Qt6::windeployqt` for the install rules;
- a static libsndfile, because upstream links it `-static` as MSYS2 provides;
- an explicit `winmm` link (MSYS2's static SDL pulls it in).

Upstream builds Windows with MSYS2. Its CI workflows pass on the fork with the
patch applied, but with the default `AUTOMATION=OFF`. The `ci-automation`
branch runs the same MSYS2 workflow with `-D AUTOMATION=ON`.

## Before proposing upstream

Done:
- Talked to the 86Box developers: they said it would be useful.
- Rebased onto current `master` (one trivial conflict in `x86.c`).
- `clang-format` with the repo's `.clang-format`: no changes in the new
  files, and no new drift in the touched ones. Unrelated reformatting the
  first version introduced (`mouse_ps2.c`, `sdl_main.c`) is gone.
- Author and copyright headers name a person, as upstream's do.
- No compiler warnings from the change in the Linux GCC build.
- The UNIX socket is created owner-only (umask around `bind()`), rather than
  `chmod`ed afterwards.

- Upstream's Linux, macOS and Windows workflows, with `-D AUTOMATION=ON`
  (`ci-automation-pr`): all 36 jobs build, with no warnings in our files. The
  first run found two (an unused `listen_thread`, now joined on shutdown, and
  a false-positive uninitialized array); both are fixed.
- The SDL frontend: the test suite passes on a `QT=OFF` build.

Still to do:
- Run the MSYS2-built binary on Windows. Only our MinGW cross-build has run
  there.
- Decide whether `step` should also work while the UI pause is active.
- Write the PR description from upstream's template, disclosing the AI
  assistance as upstream's `CONTRIBUTING.md` requires.
## The change

The whole change is the fork's `automation-socket` branch, compared with
upstream: <https://github.com/86Box/86Box/compare/master...dafugg:86Box:automation-socket>.
`86BOX_COMMIT` at the top of this repo names the fork commit that this
version of the server expects. The build scripts warn if `86box/` doesn't
contain it.
