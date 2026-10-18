"""Build the base VM for the two-player Warcraft II demo: DOS 6.22, a CD-ROM driver, and an IPX
network stack on an emulated NE2000 attached to 86Box's local network switch.

    BOX86_DOS_BASE_IMG=... python examples/warcraft2/prepare.py --out DIR \\
        --dos-files DIR_WITH_HIMEM.SYS_MSCDEX.EXE_VIDECDD.SYS --net-kit DIR_WITH_IPX_AND_NE2000 \\
        --mouse CTMOUSE.EXE [--war2x DIR_OF_WARCRAFT_2X_V1.50]

The DOS files and the network kit aren't distributed here: HIMEM.SYS and MSCDEX.EXE come with MS-DOS
6.22, VIDECDD.SYS is a generic ATAPI CD-ROM driver, and the kit is Novell's ODI drivers (LSL.COM,
NE2000.COM, IPXODI.COM), as collected in "dos_net_ne2000_3com". --war2x installs Warcraft II as
C:\\WAR2X from an installed copy of v1.50 ("Warcraft 2X RETAIL v1.50", as eXoDOS ships
it), set up for a Sound Blaster 16. Use 1.2 or later: 1.0's IPX games can't be joined.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from box86mcp import disk
from box86mcp import server as S
from box86mcp.vm import _ini_set

CONFIG_SYS = """DEVICE=C:\\DOS\\HIMEM.SYS
DOS=HIGH
FILES=40
BUFFERS=20
LASTDRIVE=Z
DEVICE=C:\\DOS\\VIDECDD.SYS /D:MSCD001
"""

AUTOEXEC_BAT = """@ECHO OFF
PROMPT $P$G
PATH C:\\DOS;C:\\NET
SET BLASTER=A220 I5 D1 H5 T6
C:\\DOS\\MSCDEX.EXE /D:MSCD001 /L:D
C:\\DOS\\CTMOUSE.EXE
C:
CD \\NET
LSL.COM
NE2000.COM
IPXODI.COM
CD \\
"""

# The ODI drivers read NET.CFG from the current directory. IRQ 3, the NE2000's default, with COM2 off.
NET_CFG = """Link Driver NE2000
    INT 3
    PORT 300
    FRAME Ethernet_802.3
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dos-files", required=True)
    ap.add_argument("--net-kit", required=True)
    ap.add_argument("--mouse", required=True, help="a DOS mouse driver, e.g. CuteMouse's CTMOUSE.EXE")
    ap.add_argument("--war2x", help="an installed Warcraft II v1.50 folder, to copy to C:\\WAR2X")
    a = ap.parse_args()
    out, dos, kit, mouse = Path(a.out), Path(a.dos_files), Path(a.net_kit), Path(a.mouse)
    print(S.dos_create_vm(str(out), autoexec=AUTOEXEC_BAT))
    img = out / "hdd.img"
    disk.write_text(img, "CONFIG.SYS", CONFIG_SYS)

    stage = out / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "DOS").mkdir(parents=True)
    (stage / "NET").mkdir()
    for f in ("HIMEM.SYS", "MSCDEX.EXE", "VIDECDD.SYS"):
        shutil.copy(dos / f, stage / "DOS" / f)
    shutil.copy(mouse, stage / "DOS" / "CTMOUSE.EXE")
    shutil.copy(kit / "IPX" / "LSL.COM", stage / "NET")
    shutil.copy(kit / "IPX" / "IPXODI.COM", stage / "NET")
    shutil.copy(kit / "NE2000" / "ODIDRV" / "NE2000.COM", stage / "NET")
    (stage / "NET" / "NET.CFG").write_bytes(NET_CFG.replace("\n", "\r\n").encode())
    dirs = [stage / "DOS", stage / "NET"]
    if a.war2x:
        game = stage / "WAR2X"
        shutil.copytree(a.war2x, game)
        for sb16 in (game / "SB16", game / "DRIVERS" / "SB16"):      # eXoDOS's Sound Blaster 16 setup
            if sb16.is_dir():
                shutil.copytree(sb16, sb16.parent, dirs_exist_ok=True)
        dirs.append(game)
    print(S.dos_install_files([str(d) for d in dirs], "\\", vm_dir=str(out)))
    shutil.rmtree(stage)

    cfg = out / "86box.cfg"
    lines = cfg.read_text().splitlines()
    for section, key, value in [
        ("Network", "net_01_card", "novell_ne2k"),
        ("Network", "net_01_net_type", "nlswitch"),
        ("Network", "net_01_secret", "warcraft2"),         # the switch group the players share
        ("Novell NE2000 #1", "base", "0300"),
        ("Novell NE2000 #1", "irq", "3"),
        ("Ports (COM & LPT)", "serial2_enabled", "0"),     # COM2 would take IRQ 3
    ]:
        lines = _ini_set(lines, section, key, value)
    cfg.write_text("\n".join(lines) + "\n")
    print(f"prepared {out}: DOS, CD-ROM driver, IPX on an NE2000 (port 300, IRQ 3) on the local switch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
