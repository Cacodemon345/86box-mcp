#!/bin/sh
# Build 86Box from ./86box with the automation socket enabled.
# Usage: docker/build.sh [extra cmake flags...]
# Output: .cache/build/src/86Box (run it with docker/86box.sh)
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
. "$root/docker/check-86box.sh"
docker build -q -t 86box-mcp --build-arg UID=$(id -u) --build-arg GID=$(id -g) "$root/docker" >/dev/null
mkdir -p "$root/.cache/build"
exec docker run --rm -v "$root:$root" -w "$root" 86box-mcp sh -c '
  cmake -G Ninja -S 86box -B .cache/build -D CMAKE_BUILD_TYPE=Release -D QT=ON -D USE_QT6=ON \
    -D AUTOMATION=ON -D DISCORD=OFF -D MUNT=OFF -D SOUNDCANVAS=OFF "$@" &&
  cmake --build .cache/build' sh "$@"
