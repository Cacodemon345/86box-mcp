"""Fixtures: a throwaway DOS 6.22 VM built from a base disk image."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from box86mcp.vm import FIXED_MTIME, MTOOLS_ENV, VM, _tool, write_config

BASE_IMG = Path(os.path.expanduser(os.environ.get("BOX86_TEST_BASE_IMG", "~/tools/86box/vms/dos622-base.img")))
# CMOS contents with the hard disk already set up, so the BIOS boots without prompting.
PRIMED_NVR = Path(os.path.expanduser(os.environ.get("BOX86_TEST_NVR", "~/tools/86box/primed-nvr/dx2-66-vlb")))

AUTOEXEC = "@ECHO OFF\r\nPROMPT $P$G\r\n"


def make_dos_vm(dest: Path) -> Path:
    if not BASE_IMG.exists():
        pytest.skip(f"base DOS image {BASE_IMG} not available")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE_IMG, dest / "hdd.img")
    if PRIMED_NVR.is_dir():
        shutil.copytree(PRIMED_NVR, dest / "nvr", dirs_exist_ok=True)
    write_config(dest, 130, 16, 63, speed="1997_5400rpm")
    autoexec = dest / "AUTOEXEC.BAT"
    autoexec.write_bytes(AUTOEXEC.encode())
    # A fixed timestamp (kept by mcopy -m): with the host's clock, VMs made either side of a minute
    # boundary list AUTOEXEC.BAT differently in DIR.
    os.utime(autoexec, (FIXED_MTIME, FIXED_MTIME))
    subprocess.run([_tool("mcopy"), "-m", "-o", "-i", f"{dest / 'hdd.img'}@@32256", str(autoexec), "::AUTOEXEC.BAT"],
                   check=True, env=MTOOLS_ENV)
    autoexec.unlink()
    return dest


@pytest.fixture
def dos_vm(tmp_path):
    vm = VM(make_dos_vm(tmp_path / "dos622"))
    vm.start(held=True, com1_log=True)
    yield vm
    vm.stop()
    print(vm.log_tail())
