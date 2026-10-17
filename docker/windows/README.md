# Windows build of 86Box

`build.sh` cross-compiles 86Box with the automation socket (`-D AUTOMATION=ON`)
for 64-bit Windows. It uses Fedora's MinGW toolchain in a container, and
collects the DLLs into `.cache/win64/86Box-automation-win64/`.

The build has been run on Windows 11 and driven over TCP from Linux: boot,
input, screen text, inline screenshots, memory and registers, held and
real-time running, and `quit`, which exits and deletes the token file.

Upstream 86Box builds Windows with MSYS2 instead. Cross-compiling needed
workarounds that live here rather than in 86Box:
- a `Windows.h` and `SetupAPI` case shim (`Windows.h`, `cross-fixups.cmake`),
  because upstream relies on Windows' case-insensitive filesystem;
- a placeholder `Qt6::windeployqt` for the install rules;
- a static libsndfile, because upstream links it `-static`, as MSYS2 provides;
- an explicit `winmm` link, which MSYS2's static SDL pulls in.

Upstream's MSYS2 CI workflow builds cleanly with `-D AUTOMATION=ON`, but an
MSYS2-built binary hasn't been run on Windows yet (see [TODO.md](../../TODO.md)).
