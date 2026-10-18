"""Keep a Windows 3.11 VM with SkiFree running, and take Python snippets to run against it.

    python skictl.py serve [--record DIR] [--vm DIR] [--dos|--raw] [--sock PATH]
                                              # boots the VM, then listens on skictl.sock
                                              # (--dos: stop at the DOS prompt, don't start Windows;
                                              #  --raw: just start the VM, held, and wait for nothing)
    python skictl.py run 'code...'            # runs code with m (a solbot Machine) in scope

Snippets share one namespace across calls, so state (and helper functions) persist. Whatever a
snippet prints comes back to the caller. The machine stays held between snippets.
"""

from __future__ import annotations

import contextlib
import io
import os
import socket
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "solitaire"))
sys.path.insert(0, str(HERE.parents[1] / "server"))
from solbot import Machine  # noqa: E402

from box86mcp import keys as K  # noqa: E402
from box86mcp.vm import VM  # noqa: E402

SOCK = HERE / "skictl.sock"
VM_DIR = Path(os.path.expanduser("~/86box_mcp/.cache/skifree/vm"))


def type_text(m: Machine, text: str) -> None:
    for ch in text:
        code, shift = K.char_keys(ch)
        down = [K.KEYS["shift"], code] if shift else [code]
        m.a.keys(True, down)
        m.step(30)
        m.a.keys(False, list(reversed(down)))
        m.step(30)


def boot(record: str | None, vm_dir: Path = VM_DIR, dos: bool = False, raw: bool = False) -> Machine:
    vm = VM(vm_dir)
    vm.start(held=True)
    m = Machine(vm)
    if record:
        Path(record).mkdir(parents=True, exist_ok=True)
        m.a.call(f"record start 50 {record}")
    if raw:
        return m
    m.a.wait_text("C:\\>", 60000)
    if dos:
        return m
    for ch in "WIN\n":
        code, _ = K.char_keys(ch)
        m.a.keys(True, [code])
        m.step(30)
        m.a.keys(False, [code])
        m.step(30)
    m.step(30000)
    m.home()
    return m


# Set SKICTL_PROGRESS_DIR to have a screenshot saved there every few minutes, for watching remotely.
PROGRESS_DIR = Path(os.environ["SKICTL_PROGRESS_DIR"]) if os.environ.get("SKICTL_PROGRESS_DIR") else None
PROGRESS_EVERY_S = 180


def serve(record: str | None, vm_dir: Path = VM_DIR, dos: bool = False, raw: bool = False) -> None:
    m = boot(record, vm_dir, dos, raw)
    last = [0.0]

    def progress(force: bool = False) -> None:
        """Save a screenshot for whoever is watching, at most every few minutes."""
        if PROGRESS_DIR and (force or time.time() - last[0] >= PROGRESS_EVERY_S):
            PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
            m.screen().save(PROGRESS_DIR / time.strftime("%Y%m%d-%H%M%S.png"))
            last[0] = time.time()

    ns = {"m": m, "K": K, "type_text": type_text, "progress": progress, "__name__": "skictl"}
    if SOCK.exists():
        SOCK.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    srv.listen(1)
    print("ready", flush=True)
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                data = b""
                while not data.endswith(b"\0"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                code = data.rstrip(b"\0").decode()
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    try:
                        exec(code, ns)
                    except SystemExit:
                        conn.sendall(b"bye\n")
                        return
                    except Exception:
                        traceback.print_exc(file=out)
                try:
                    progress()
                except Exception:
                    pass
                conn.sendall(out.getvalue().encode())
    finally:
        m.vm.stop()
        SOCK.unlink(missing_ok=True)


def run(code: str) -> None:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(SOCK))
    c.sendall(code.encode() + b"\0")
    c.shutdown(socket.SHUT_WR)
    out = b""
    while True:
        chunk = c.recv(65536)
        if not chunk:
            break
        out += chunk
    sys.stdout.write(out.decode())


if __name__ == "__main__":
    def opt(name):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None

    if opt("--sock"):
        SOCK = Path(opt("--sock"))
    if sys.argv[1] == "serve":
        serve(opt("--record"), Path(opt("--vm")).expanduser() if opt("--vm") else VM_DIR, "--dos" in sys.argv,
              "--raw" in sys.argv)
    else:
        code = [a for a in sys.argv[2:] if a not in ("--sock", opt("--sock"))]
        run(code[0] if code else sys.stdin.read())
