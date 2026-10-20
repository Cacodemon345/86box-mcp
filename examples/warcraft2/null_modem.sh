#!/bin/bash
# Two-player Warcraft II over a null-modem cable, from power-on to Victory and Defeat.
#
#   null_modem.sh WORKDIR CD_IMAGE [VIDEO]
#
# WORKDIR holds the VMs p1 and p2, made as for lobby.sh (see README.md); their network cards stay
# but aren't used. CD_IMAGE is the game CD. With VIDEO, both are recorded from power-on and
# rendered side by side to that file. Drives a running `python -m box86mcp.ctl serve` through
# $CTL (default: python -m box86mcp.ctl). The coordinates are for Warcraft II 2X v1.50 at 640x480.
#
# serial_link joins COM1 to COM1 with full handshaking. p1 (Alpha) calls and p2 (Bravo) answers
# in Direct Connection Setup; they chat in the lobby, Bravo clicks Ready, Alpha starts the game,
# and Bravo surrenders. Prints the link's byte counts, which two runs should agree on.
set -e
cd "$1"
CD=$2
VIDEO=$3
C=${CTL:-python -m box86mcp.ctl}
S() { $C call step_all "{\"ms\": $1}" | tail -1; }
each() { for p in p1 p2; do $C call "$1" "$(echo "$2" | sed "s/VM/$p/")" >/dev/null; done; }
click() { $C call mouse_to "{\"vm\": \"$1\", \"x\": $2, \"y\": $3}" >/dev/null; $C call mouse "{\"vm\": \"$1\", \"left\": true}" >/dev/null; }
# pick VM x y item_x item_y: open a dropdown, move onto an item, let go
pick() {
  $C call mouse_to "{\"vm\": \"$1\", \"x\": $2, \"y\": $3}" >/dev/null
  $C call mouse "{\"vm\": \"$1\", \"left\": true, \"release\": false}" >/dev/null; S 300 >/dev/null
  $C call mouse_to "{\"vm\": \"$1\", \"x\": $4, \"y\": $5}" >/dev/null
  $C call mouse "{\"vm\": \"$1\", \"left\": false}" >/dev/null; S 300 >/dev/null
}
[ -n "$VIDEO" ] && { rm -rf rec; mkdir -p rec/p1 rec/p2; }
for p in p1 p2; do
  $C call vm_start "{\"vm\": \"$p\", \"vm_dir\": \"$PWD/$p\", \"serial\": [1]}" | tail -1
  $C call cdrom_insert "{\"vm\": \"$p\", \"image\": \"$CD\"}" >/dev/null
  [ -n "$VIDEO" ] && $C call record_start "{\"vm\": \"$p\", \"directory\": \"$PWD/rec/$p\", \"interval_ms\": 100}" >/dev/null
done
$C call serial_link '{"vm_a": "p1", "vm_b": "p2", "wiring": "full"}' | tail -1
S 30000
each type_text '{"vm": "VM", "text": "CD \\WAR2X\nWAR2\n"}'
S 15000
for i in 1 2; do each press_keys '{"vm": "VM", "keys": ["esc"]}'; S 3000; done
each press_keys '{"vm": "VM", "keys": ["m"]}'; S 2000
each press_keys '{"vm": "VM", "keys": ["backspace","backspace","backspace","backspace","backspace","backspace","backspace","backspace","backspace"]}'
$C call type_text '{"vm": "p1", "text": "Alpha\n"}' >/dev/null
$C call type_text '{"vm": "p2", "text": "Bravo\n"}' >/dev/null
S 2000
each press_keys '{"vm": "VM", "keys": ["c"]}'; S 3000       # Direct Connect
pick p1 372 341 320 323                                    # COM 1 (the IRQ follows: 4)
pick p2 372 341 320 323
pick p2 175 375 120 392                                    # p2 answers; p1 stays on Call
click p2 517 350; S 1000                                   # Connect: answer first, then call
click p1 517 350
S 6000
$C call type_text '{"vm": "p1", "text": "hello from alpha over serial\n"}' >/dev/null; S 2000
$C call type_text '{"vm": "p2", "text": "bravo here, reading you\n"}' >/dev/null; S 2000
click p2 18 79; S 2000                                     # Bravo: Ready
click p1 511 418                                           # Alpha: Start Game
S 20000
# Bravo surrenders: Menu, End Scenario, Surrender, Surrender
click p2 45 11; S 1000
for k in e s s; do $C call press_keys "{\"vm\": \"p2\", \"keys\": [\"$k\"]}" >/dev/null; S 1000; done
S 7000
$C call serial_links
if [ -n "$VIDEO" ]; then
  $C call record_stop_all "{\"video\": \"$VIDEO\", \"speed\": [\"0:6\", \"50000:2\", \"60000:1\", \"82000:4\", \"104000:1\"], \"labels\": {\"p1\": \"Alpha: COM1, Call\", \"p2\": \"Bravo: COM1, Answer\"}}" | tail -1
fi
