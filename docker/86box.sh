#!/bin/sh
# Run the container-built 86Box. $HOME, /tmp and the repo are mounted at their
# host paths, so VM directories, ROMs and automation sockets keep the same paths.
# Headless by default; with QT_QPA_PLATFORM=xcb and DISPLAY set, it opens a
# window on the host's X server (the X socket lives in /tmp/.X11-unix and the
# cookie in ~/.Xauthority, both already mounted). HOME is passed through so 86Box finds ROMs in
# ~/.local/share/86Box/roms as a native install would.
# Stopping this script (SIGTERM/SIGINT) stops the container.
root=$(cd "$(dirname "$0")/.." && pwd)
name="86box-$$"
trap 'docker kill "$name" >/dev/null 2>&1' TERM INT HUP
docker run --rm --init --name "$name" --network host -v "$HOME:$HOME" -v "$root:$root" -v /tmp:/tmp -w "$PWD" \
    -e QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
    -e HOME="$HOME" -e DISPLAY="${DISPLAY:-}" -e XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" -e QT_X11_NO_MITSHM=1 \
    86box-mcp "$root/.cache/build/src/86Box" "$@" &
wait $!
