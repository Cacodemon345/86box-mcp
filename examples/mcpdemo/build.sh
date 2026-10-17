#!/bin/sh
# Build MCPDEMO.EXE with DJGPP and stage it with its runtime files in ./dist.
set -e
here=$(cd "$(dirname "$0")" && pwd)
DJGPP_DIR=${DJGPP_DIR:-$HOME/djgpp}
CWSDPMI=${CWSDPMI:?set CWSDPMI to CWSDPMI.EXE (the DPMI host, from the CWSDPMI release zip)}
CTMOUSE=${CTMOUSE:?set CTMOUSE to CTMOUSE.EXE (the CuteMouse driver)}
mkdir -p "$here/dist"
"$DJGPP_DIR/bin/i586-pc-msdosdjgpp-gcc" -O2 -s -Wall -Wextra -o "$here/dist/MCPDEMO.EXE" "$here/mcpdemo.c"
rm -f "$here/dist/MCPDEMO.exe"  # DJGPP also leaves a lowercase copy; FAT would see a clash
cp "$CWSDPMI" "$here/dist/CWSDPMI.EXE"
cp "$CTMOUSE" "$here/dist/CTMOUSE.EXE"
ls -l "$here/dist"
