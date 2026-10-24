"""An MCP server attached over TCP, as from another machine: media goes over the connection, and so do
86Box's log, its GDB stub port and its fatal errors."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from box86mcp.protocol import Automation, AutomationError
from conftest import make_dos_vm
from test_sound import run

TRANSPORTS = ["tcp", pytest.param("unix", marks=pytest.mark.skipif(not hasattr(socket, "AF_UNIX"),
                                                               reason="no UNIX sockets"))]


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_media_and_log_reach_an_attached_86box(tmp_path, monkeypatch, transport):
    from box86mcp.vm import VM

    monkeypatch.setenv("BOX86_TRANSPORT", transport)
    here = tmp_path / "here"                                # this machine's files
    here.mkdir()
    (here / "HELLO.TXT").write_text("SENT OVER THE SOCKET\r\n")
    (here / "my track.bin").write_bytes(bytes(2352 * 300))  # a 4 s data track
    (here / "disc.cue").write_text('FILE "my track.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    vm = VM(make_dos_vm(tmp_path / "vm"))
    vm.start(held=True)
    try:
        assert vm.auto.wait_text("C:\\>", 60000)["matched"]
        if not vm.auto.can_put_files():
            pytest.skip("86Box without media put")
        token = open(vm.token_file).read().strip() if vm.token_file else None
        vm.auto.close()                                     # one client at a time
        env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))

        async def script(call):
            out = {"attach": await call("vm_attach", endpoint=vm.endpoint, token=token)}
            out["floppy"] = await call("dos_put_floppy", paths=[str(here / "HELLO.TXT")])
            await call("type_text", text="TYPE A:\\HELLO.TXT\n")
            out["typed"] = await call("wait_for_text", text="SENT OVER THE SOCKET", timeout_ms=20000)
            out["cd"] = await call("cdrom_insert", image=str(here / "disc.cue"))
            out["info"] = json.loads(await call("vm_info"))
            out["status"] = json.loads(await call("vm_status"))
            await call("vm_stop", power_off=False)
            return out

        out = run(env, script)
        vm.auto.connect()
    finally:
        vm.stop()
    media = tmp_path / "vm" / "automation-media"
    # As Paths: 86Box on Windows gives them with forward slashes.
    assert Path(out["info"]["floppy"][0]["file"]).parent == media, out
    assert "matched" in out["typed"] or "found" in out["typed"], out["typed"]
    # The cue sheet went with its track, renamed to a name 86Box takes and rewritten to match.
    assert Path(out["info"]["cdrom"][0]["file"]) == media / "disc.cue", out
    assert (media / "my_track.bin").stat().st_size == 2352 * 300
    assert '"my_track.bin"' in (media / "disc.cue").read_text()
    assert "86Box" in out["status"]["log_tail"] or "Automation" in out["status"]["log_tail"], out["status"]


class FakeBox:
    """A one-client automation endpoint that answers each request line from `answer`."""

    def __init__(self, answer):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.endpoint = f"tcp:127.0.0.1:{self.srv.getsockname()[1]}"
        self.answer = answer
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        with conn, conn.makefile("rb") as f:
            for line in f:
                reply = self.answer(line.decode().strip())
                if reply is None:
                    return
                conn.sendall(reply.encode() + b"\n")


def test_a_fatal_error_reaches_the_client():
    box = FakeBox(lambda line: '{"ok":false,"fatal":true,"error":"Life expired"}')
    a = Automation(box.endpoint)
    with pytest.raises(AutomationError, match="fatal error: Life expired"):
        a.step(10)
    assert a.sock is None


def test_an_older_86box_gets_paths_not_files(tmp_path):
    from box86mcp.vm import AttachedVM

    seen = []

    def answer(line):
        seen.append(line)
        if line == "info":
            return '{"ok":true,"serial":[]}'
        if line.startswith(("fdd ", "cd ")):
            return '{"ok":true,"ms":0}'
        return '{"ok":false,"error":"unknown command or wrong arguments"}'

    img = tmp_path / "a.img"
    img.write_bytes(bytes(1474560))
    v = AttachedVM(FakeBox(answer).endpoint)
    assert v.media(img) == str(img)                         # a path on its machine, as before
    assert v._stub_port() == 12345
    assert AttachedVM(FakeBox(answer).endpoint, gdb_port=1234)._stub_port() == 1234
    assert v.log_tail() == ""
    assert not any(line.startswith("media put") for line in seen), seen


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_media_put_failures(tmp_path, monkeypatch, transport):
    """Names that would leave the folder, names the OS refuses, a full disk, a folder it can't write and
    a connection that drops part way: each gets an error with the reason, leaves no partial file, and
    the connection (or the next one) works on."""
    from box86mcp.vm import VM

    monkeypatch.setenv("BOX86_TRANSPORT", transport)
    vm = VM(make_dos_vm(tmp_path / "vm"))
    vm.start(held=True)
    media = tmp_path / "vm" / "automation-media"
    a = vm.auto
    try:
        if not a.can_put_files():
            pytest.skip("86Box without media put")

        def put(name, data):
            a.sock.sendall(f"media put {name} {len(data)}\n".encode() + data)
            try:
                return a._reply()
            except AutomationError as e:
                return {"ok": False, "error": str(e)}

        assert put("fine.img", b"x" * 1000)["ok"]
        for name in ("../up.img", "a/b.img", "..", "a\\b", "c:x"):
            assert "bad name" in put(name, b"y" * 3000)["error"], name
            assert a.status()["ok"]                         # the bytes were taken, not read as requests
        assert not (tmp_path / "vm" / "up.img").exists()
        # Anything else is up to the OS, and its reason comes back (Windows calls a name too long
        # a file that isn't there).
        reason = "No such file" if os.name == "nt" else "File name too long"
        assert reason in put("x" * 300, b"y" * 3000)["error"]
        assert a.status()["ok"]
        if os.name == "nt":                                 # characters Windows doesn't allow in names
            assert "Invalid argument" in put("f*g.img", b"y" * 3000)["error"]
            assert a.status()["ok"]

        if os.name != "nt":                                 # no /dev/full, and chmod can't stop writes
            # A full disk: the file being written is /dev/full.
            (media / "full.img.part").symlink_to("/dev/full")
            r = put("full.img", os.urandom(200000))
            assert "No space left" in r["error"], r
            assert not (media / "full.img").exists() and not (media / "full.img.part").is_symlink()

            # A folder it can't write to.
            media.chmod(0o555)
            try:
                assert "Permission denied" in put("ro.img", b"x" * 100)["error"]
            finally:
                media.chmod(0o755)

        # Something in the way of the finished file: the error, and the partial file goes.
        (media / "dir.img").mkdir()
        assert "cannot replace" in put("dir.img", b"x" * 100)["error"]
        assert (media / "dir.img").is_dir() and not (media / "dir.img.part").exists()

        assert "No such file" in str(pytest.raises(AutomationError, a.call, "media remove gone.img").value)

        # The connection drops part way: no partial file, and the next client is served.
        a.sock.sendall(b"media put drop.img 1000000\n" + b"x" * 5000)
        a.close()
        a.connect()
        assert a.status()["ok"]
        assert not (media / "drop.img").exists() and not (media / "drop.img.part").exists()
        assert sorted(p.name for p in media.iterdir()) == ["dir.img", "fine.img"]
    finally:
        vm.stop()


def test_a_file_that_shrinks_while_it_is_sent(tmp_path, monkeypatch):
    """The size goes first; if the file then has fewer bytes, 86Box would wait for the rest forever."""
    got = []
    box = FakeBox(lambda line: got.append(line) or None)
    f = tmp_path / "f.img"
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(os.path, "getsize", lambda p: 1000)
    a = Automation(box.endpoint)
    with pytest.raises(OSError, match="changed while it was being sent"):
        a.put_file("f.img", f)
    assert a.sock is None
