"""An MCP server attached over TCP, as from another machine: media goes over the connection, and so do
86Box's log, its GDB stub port and its fatal errors."""

from __future__ import annotations

import json
import os
import socket
import threading

import pytest

from box86mcp.protocol import Automation, AutomationError
from conftest import make_dos_vm
from test_sound import run


def test_media_and_log_reach_an_86box_attached_over_tcp(tmp_path, monkeypatch):
    from box86mcp.vm import VM

    monkeypatch.setenv("BOX86_TRANSPORT", "tcp")
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
        token = open(vm.token_file).read().strip()
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
    assert out["info"]["floppy"][0]["file"].startswith(str(media)), out
    assert "matched" in out["typed"] or "found" in out["typed"], out["typed"]
    # The cue sheet went with its track, renamed to a name 86Box takes and rewritten to match.
    assert out["info"]["cdrom"][0]["file"] == str(media / "disc.cue"), out
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
