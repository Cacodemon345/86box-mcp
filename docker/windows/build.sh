#!/bin/sh
# Cross-compile 86Box for 64-bit Windows and package it with its DLLs.
# Output: .cache/win64/86Box-automation-win64/ (and a .zip of it)
set -e
root=$(cd "$(dirname "$0")/../.." && pwd)
. "$root/docker/check-86box.sh"
docker build -t 86box-mcp-win64 "$root/docker/windows"
mkdir -p "$root/.cache/win64"
docker run --rm -e OWNER="$(id -u):$(id -g)" -v "$root:$root" -w "$root" 86box-mcp-win64 sh -ec '
  trap "chown -R \$OWNER .cache/win64" EXIT
  # Configure twice: with the full option set, a fresh configure fails CMake'"'"'s compiler ABI
  # probe (not yet understood); the probe result is cached by a minimal first configure.
  [ -f .cache/win64/build/CMakeCache.txt ] || \
    mingw64-cmake -G Ninja -S 86box -B .cache/win64/build -D QT=ON -D USE_QT6=ON -D STATIC_BUILD=OFF > /dev/null 2>&1 || true
  mingw64-cmake -G Ninja -S 86box -B .cache/win64/build -D CMAKE_BUILD_TYPE=Release \
    -D QT=ON -D USE_QT6=ON -D AUTOMATION=ON -D DISCORD=OFF -D MUNT=OFF -D SOUNDCANVAS=OFF \
    -D RTMIDI=OFF -D FLUIDSYNTH=OFF -D VNC=OFF -D DYNAREC=ON -D STATIC_BUILD=OFF -D QT_HOST_PATH=/usr -D CMAKE_PROJECT_86Box_INCLUDE=$PWD/docker/windows/cross-fixups.cmake
  cmake --build .cache/win64/build
  python3 docker/windows/package.py .cache/win64/build/src/86Box.exe .cache/win64/86Box-automation-win64'
