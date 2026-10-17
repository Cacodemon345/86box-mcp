# Changes to 86Box

Everything this project needs from 86Box is now in upstream 86Box `master`.
Both pull requests were merged on 2026-10-17:

| Pull request | What it adds |
|---|---|
| [#8365](https://github.com/86Box/86Box/pull/8365) | The automation socket (`mouse: let input through when an automation client sends it` and `Add an opt-in automation control socket`) |
| [#8361](https://github.com/86Box/86Box/pull/8361) | `char_file: add an option to write output to the file immediately`. 86box-mcp's COM1 capture turns the option on. |

`86BOX_COMMIT` names the upstream commit with both, which is the merge of #8361.
Build 86Box with `-D AUTOMATION=ON`; it's off by default, so official releases
don't have the socket. [automation-code-guide.md](automation-code-guide.md)
walks through the code.

The rest of this page records how the change was designed, tested and
proposed. The branches on the fork at https://github.com/dafugg/86Box
(`automation-socket`, `automation-pr`, `char-file-flush`, and the tag
`automation-socket-pre-rebase`) are kept for history.

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

## Design decisions (as merged)

- **Mouse capture.** Mouse devices drop input unless the UI has captured the
  pointer. Setting `mouse_capture` from automation broke a visible window (the
  UI hid the host cursor and forwarded host motion). So injected input sets a
  separate `mouse_automation` flag, which the PS/2, serial and bus mice also
  accept.
- **Key pacing.** Injecting several key events in the same instant overflows
  the keyboard's 16-byte FIFO, because extended keys plus Num Lock fake shifts
  are up to 6 bytes each, and keys get lost silently. The `key` command queues
  events and delivers them 2 ms of emulated time apart.
- **Recording.** `record start <interval> <dir>` writes PNGs from the frame
  hook, on the CPU thread, so a slow disk slows emulation slightly while
  recording.
- **`char_file` flush.** This is independent of automation, so it went in as a
  separate PR (#8361). It's an option, off by default. Both File devices write
  one byte at a time, so a flush per byte only suits people who want the file
  live. Its label is waiting to be added to the translations.

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

## Before proposing upstream (done)

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

- The PR descriptions followed upstream's template and disclosed the AI
  assistance, as upstream's `CONTRIBUTING.md` requires.

Still open after the merge:
- Run an MSYS2-built binary on Windows. Only our MinGW cross-build has run
  there.
- Decide whether `step` should also work while the UI pause is active. The PR
  asked; the merge didn't settle it.

## The change

The whole change as merged:
<https://github.com/86Box/86Box/pull/8365/files> and
<https://github.com/86Box/86Box/pull/8361/files>.
