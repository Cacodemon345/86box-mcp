"""Sound capture through a real MCP client: the PC speaker and a Sound Blaster DMA tone, recorded on
the emulated clock, measured, and identical on two VMs in lockstep."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from box86mcp import audio as A
from box86mcp.vm import MTOOLS_ENV, _tool
from conftest import make_dos_vm

DATA = Path(__file__).parent / "data"


def dos_vm_with(dest: Path, *files: str) -> Path:
    d = make_dos_vm(dest)
    for f in files:
        subprocess.run([_tool("mcopy"), "-o", "-i", f"{d / 'hdd.img'}@@32256", str(DATA / f), f"::{f}"],
                       check=True, env=MTOOLS_ENV)
    return d


def run(params_env, script):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "box86mcp.server"], env=params_env)

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()

                async def call(tool, **args):
                    res = await s.call_tool(tool, args)
                    text = "".join(c.text for c in res.content if c.type == "text")
                    assert not res.is_error, f"{tool}: {text}"
                    images = [c for c in res.content if c.type == "image"]
                    return images[0] if images and not text else text

                return await script(call)

    return asyncio.run(go())


def test_speaker_and_sound_blaster(tmp_path):
    vm_dir = dos_vm_with(tmp_path / "dos", "BEEP.COM", "SBTONE.COM")
    rec = tmp_path / "rec"
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))

    async def script(call):
        out = {}
        await call("vm_start", vm_dir=str(vm_dir))
        await call("wait_for_text", text="C:\\>", timeout_ms=60000)
        out["start"] = await call("record_start", directory=str(rec), interval_ms=50)
        if "no sound" in out["start"]:
            return out
        await call("type_text", text="BEEP\n")
        out["sound"] = await call("wait_for_sound", threshold_db=-30, timeout_ms=10000)
        out["level"] = await call("sound_level", ms=100)
        out["spectrogram"] = await call("sound_spectrogram", ms=500)
        out["silence"] = await call("wait_for_silence", threshold_db=-60, for_ms=300, timeout_ms=5000)
        await call("type_text", text="SBTONE\n")
        out["sb"] = await call("wait_for_sound", threshold_db=-30, timeout_ms=10000)
        out["sb_level"] = await call("sound_level", ms=100)
        await call("wait_for_silence", threshold_db=-60, for_ms=300, timeout_ms=5000)
        out["save"] = await call("sound_save", path=str(tmp_path / "last.wav"), ms=3000)
        out["stop"] = await call("record_stop", video=str(tmp_path / "rec.mp4"))
        out["check"] = await call("sound_check")
        await call("vm_stop")
        return out

    out = run(env, script)
    if "no sound" in out["start"]:
        pytest.skip("86Box without the automation audio capture")
    assert "sound on music" in out["sound"], out["sound"]          # the PC speaker mixes into "music"
    assert out["spectrogram"].type == "image"
    assert "quiet since" in out["silence"]
    assert "sound on main" in out["sb"], out["sb"]                  # the Sound Blaster's DSP
    assert "sound:" in out["stop"] and "rendered" in out["stop"] and "sound (first)" in out["stop"]
    assert "continuous" in out["check"] and "FRAMES MISSING" not in out["check"], out["check"]

    segs = {s.stream: s for s in A.load(rec)}
    assert {"main", "music"} <= set(segs), segs
    for name, low, high in (("music", 0.15, 0.24), ("main", 0.48, 0.52)):     # 3-4 BIOS ticks; 11111 samples
        x = A.read(segs[name])
        mono = x.mean(axis=1)
        win = 48                                        # 1 ms; a DC level left behind isn't sound
        std = mono[:len(mono) // win * win].reshape(-1, win).std(axis=1)
        loud = np.nonzero(std > 0.02)[0]
        tone = x[loud[0] * win:(loud[-1] + 1) * win]
        length = len(tone) / A.RATE
        hz = A.dominant_hz(tone)[0][0]
        assert low < length < high, (name, length)
        assert abs(hz - 1000) < 5, (name, hz)
        assert not A.find_gaps(x, 0), name

    # Each frame's stamps: CPU cycles always increase, and its sample position agrees with its
    # cycle time (both count emulated time from the start of the recording).
    stamps = [json.loads(line) for line in (rec / "frames.jsonl").read_text().splitlines()]
    assert len(stamps) > 10
    assert all(b["cycles"] > a["cycles"] for a, b in zip(stamps, stamps[1:]))
    for st in stamps:
        assert abs(st["audio"]["main"] - st["us"] * 48000 / 1e6) <= 2, st

    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json",
                            str(tmp_path / "rec.mp4")], capture_output=True, text=True, check=True)
    assert {s["codec_type"] for s in json.loads(probe.stdout)["streams"]} == {"video", "audio"}


def test_two_vms_in_lockstep_sound_the_same(tmp_path):
    a = dos_vm_with(tmp_path / "a", "SBTONE.COM")
    b = dos_vm_with(tmp_path / "b", "SBTONE.COM")
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))

    async def script(call):
        out = {}
        await call("vm_start", vm="p1", vm_dir=str(a))
        await call("vm_start", vm="p2", vm_dir=str(b))
        await call("wait_for_text", vm="p1", text="C:\\>", timeout_ms=60000)
        await call("step", vm="p1", ms=500)
        out["start1"] = await call("record_start", vm="p1", directory=str(tmp_path / "r1"), interval_ms=50)
        out["start2"] = await call("record_start", vm="p2", directory=str(tmp_path / "r2"), interval_ms=50)
        if "no sound" in out["start1"]:
            return out
        await call("together", actions=[{"vm": "p1", "text": "SBTONE\n"}, {"vm": "p2", "text": "SBTONE\n"}])
        await call("wait_for_sound", vm="p1", threshold_db=-30, timeout_ms=10000)
        await call("wait_for_silence", vm="p1", threshold_db=-60, for_ms=300, timeout_ms=5000)
        out["stop"] = await call("record_stop_all", video=str(tmp_path / "both.mp4"), audio="split")
        await call("vm_stop", vm="p1")
        await call("vm_stop", vm="p2")
        return out

    out = run(env, script)
    if "no sound" in out["start1"]:
        pytest.skip("86Box without the automation audio capture")
    s1 = {s.stream: s for s in A.load(tmp_path / "r1")}
    s2 = {s.stream: s for s in A.load(tmp_path / "r2")}
    assert s1["main"].start_ms == s2["main"].start_ms and s1["main"].frames == s2["main"].frames
    # Same input at the same emulated time: the same tone at the same moment, to well within a
    # sample. (Not always bit-identical: two 86Box runs can differ by a few CPU cycles.)
    x1, x2 = A.read(s1["main"]).mean(axis=1), A.read(s2["main"]).mean(axis=1)
    assert A.lag_ms(x1, x2) < 1000 / A.RATE
    assert "sound (split)" in out["stop"]


def test_sound_reaches_a_client_attached_over_tcp(tmp_path, monkeypatch):
    """The sound comes over the connection: 86Box records it in memory (no files), and an MCP server
    attached over TCP, as from another machine, writes the WAVs itself."""
    from box86mcp.vm import VM

    monkeypatch.setenv("BOX86_TRANSPORT", "tcp")
    vm = VM(dos_vm_with(tmp_path / "vm", "SBTONE.COM"))
    vm.start(held=True)
    rec = tmp_path / "here"
    try:
        assert vm.auto.wait_text("C:\\>", 60000)["matched"]
        token = open(vm.token_file).read().strip()
        vm.auto.close()                                    # one client at a time
        env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))

        async def script(call):
            out = {"attach": await call("vm_attach", endpoint=vm.endpoint, token=token)}
            out["start"] = await call("record_start", directory=str(rec), interval_ms=50)
            if "no sound" in out["start"]:
                return out
            await call("type_text", text="SBTONE\n")
            await call("wait_for_sound", threshold_db=-30, timeout_ms=10000)
            await call("wait_for_silence", threshold_db=-60, for_ms=300, timeout_ms=5000)
            out["stop"] = await call("record_stop")
            return out

        out = run(env, script)
        vm.auto.connect()
    finally:
        vm.stop()
    if "no sound" in out["start"]:
        pytest.skip("86Box without the automation audio capture")
    segs = {s.stream: s for s in A.load(rec)}
    assert "main" in segs and segs["main"].lost == 0, out["stop"]
    x = A.read(segs["main"]).mean(axis=1)
    std = x[:len(x) // 48 * 48].reshape(-1, 48).std(axis=1)
    assert (std > 0.02).sum() > 400                       # the half-second tone, in 1 ms windows
