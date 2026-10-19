#!/bin/bash
# Two-player Warcraft II over IPX, from power-on to both players in the lobby, ready.
#
#   lobby.sh WORKDIR CD_IMAGE
#
# WORKDIR holds the VMs p1 and p2 (see README.md); recordings go to WORKDIR/rec. CD_IMAGE is the game
# CD (it checks for it). Drives a running `python -m box86mcp.ctl serve` through $CTL (default:
# python -m box86mcp.ctl). p1 (Alpha) creates the game on battle_2.pud, p2 (Bravo) joins and readies.
# The coordinates are for Warcraft II 2X v1.50 at 640x480; check them with screenshots for another
# version. Then set Units on p1 and press Start Game (see README.md).
set -e
cd "$1"
CD=$2
C=${CTL:-python -m box86mcp.ctl}
S() { $C call step_all "{\"ms\": $1}" | tail -1; }
VMS=${VMS:-p1 p2}
# each TOOL ARGS: call TOOL on every VM in turn, with VM in ARGS replaced by its name
each() { for p in $VMS; do $C call "$1" "$(echo "$2" | sed "s/VM/$p/")" >/dev/null; done; }
rm -rf rec; mkdir -p rec/p1 rec/p2
for p in p1 p2; do
  $C call vm_start "{\"vm\": \"$p\", \"vm_dir\": \"$PWD/$p\"}" | tail -1
  $C call cdrom_insert "{\"vm\": \"$p\", \"image\": \"$CD\"}" >/dev/null
  $C call record_start "{\"vm\": \"$p\", \"directory\": \"$PWD/rec/$p\", \"interval_ms\": 100}" >/dev/null
done
S 30000
each type_text '{"vm": "VM", "text": "CD \\WAR2X\nWAR2\n"}'
S 15000
for i in 1 2 3; do each press_keys '{"vm": "VM", "keys": ["esc"]}'; S 3000; done
each press_keys '{"vm": "VM", "keys": ["m"]}'; S 2000
each press_keys '{"vm": "VM", "keys": ["backspace","backspace","backspace","backspace","backspace","backspace","backspace","backspace","backspace"]}'
$C call type_text '{"vm": "p1", "text": "Alpha\n"}' >/dev/null
$C call type_text '{"vm": "p2", "text": "Bravo\n"}' >/dev/null
S 2000
each press_keys '{"vm": "VM", "keys": ["down","down","c"]}'; S 4000
# p1 creates, and opens Select Scenario (the map is chosen afterwards)
$C call press_keys '{"vm": "p1", "keys": ["enter"]}' >/dev/null; S 3000
$C call mouse_to '{"vm": "p1", "x": 512, "y": 381}' | tail -1; $C call mouse '{"vm": "p1", "left": true}' >/dev/null; S 2000
# Custom scenario: scroll to PUDS, open PUDS\MULTI, pick battle_2.pud, OK
$C call mouse_to '{"vm": "p1", "x": 475, "y": 288}' | tail -1
for i in 1 2 3 4 5 6; do $C call mouse '{"vm": "p1", "left": true, "hold_ms": 120}' >/dev/null; S 200; done
dbl() { $C call mouse_to "{\"vm\": \"p1\", \"x\": $1, \"y\": $2}" | tail -1; for i in 1 2; do $C call mouse '{"vm": "p1", "left": true, "hold_ms": 60}' >/dev/null; done; S 600; }
dbl 205 249; dbl 205 196
$C call mouse_to '{"vm": "p1", "x": 215, "y": 286}' | tail -1; $C call mouse '{"vm": "p1", "left": true, "hold_ms": 80}' >/dev/null; S 400
$C call mouse_to '{"vm": "p1", "x": 241, "y": 381}' | tail -1; $C call mouse '{"vm": "p1", "left": true, "hold_ms": 150}' >/dev/null; S 1000
# p2 joins: the first game in the list, Join Game
$C call mouse_to '{"vm": "p2", "x": 200, "y": 219}' | tail -1; $C call mouse '{"vm": "p2", "left": true}' >/dev/null; S 1000
$C call mouse_to '{"vm": "p2", "x": 489, "y": 276}' | tail -1; $C call mouse '{"vm": "p2", "left": true}' >/dev/null; S 6000
# p2 ready
$C call mouse_to '{"vm": "p2", "x": 18, "y": 79}' | tail -1; $C call mouse '{"vm": "p2", "left": true}' >/dev/null; S 3000
