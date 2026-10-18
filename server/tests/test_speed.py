"""measure_speed, both ways, and vm_start(binary=...)."""

from __future__ import annotations

import re

from conftest import make_dos_vm


def _pct(text: str) -> float:
    return float(re.match(r"(\d+) % of real time", text).group(1))


def test_measure_speed_and_binary(tmp_path):
    from box86mcp import server as S
    from box86mcp.vm import BINARY

    out = S.vm_start(str(make_dos_vm(tmp_path / "vm")), binary=str(BINARY))
    try:
        assert str(BINARY) in out
        assert "found" in S.wait_for_text("C:\\>", 30000)

        unpaced = S.measure_speed(3, paced=False)
        assert "unpaced: 3.0 s emulated" in unpaced and _pct(unpaced) > 0 and "left held" in unpaced
        assert S.vm_status()["held"] is True

        paced = S.measure_speed(3, paced=True)
        # Real-time pacing: at most real time, give or take the sampling around the sleep.
        assert 0 < _pct(paced) <= 110 and "left held" in paced, paced
        assert S.vm_status()["held"] is True

        S.release()
        paced = S.measure_speed(2)
        assert "running in real time" in paced
        assert S.vm_status()["held"] is False
    finally:
        S.vm_stop()


def test_binary_from_a_build_folder_sticks(tmp_path, monkeypatch):
    """vm_start(binary=<build folder>) finds src/86Box in it, and later starts keep it."""
    import os

    from box86mcp import server as S

    exe = "86Box.exe" if os.name == "nt" else "86Box"
    build = tmp_path / "worktree" / "build"
    (build / "src").mkdir(parents=True)
    (build / "src" / exe).write_text("")
    assert S._find_binary(str(build)) == build / "src" / exe
    assert S._find_binary(str(tmp_path / "worktree")) == build / "src" / exe     # the checkout
    monkeypatch.chdir(tmp_path)
    assert S._find_binary("worktree/build") == build / "src" / exe              # relative
    import pytest
    with pytest.raises(FileNotFoundError, match="no 86Box"):
        S._find_binary(str(tmp_path))


def test_chosen_binary_sticks(tmp_path, monkeypatch):
    from box86mcp import server as S
    from box86mcp.vm import BINARY

    monkeypatch.setattr(S, "_binary", None)
    vm = str(make_dos_vm(tmp_path / "vm"))
    try:
        S.vm_start(vm, binary=str(BINARY))
        S.vm_stop()
        assert str(BINARY) in S.vm_start(vm)          # no binary: the one chosen before
    finally:
        S.vm_stop()
