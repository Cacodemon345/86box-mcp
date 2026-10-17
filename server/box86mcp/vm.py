"""Launching 86Box headless and preparing media for it."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .protocol import SUPPORTED_PROTOCOLS, Automation, AutomationError


def _env_path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(name, default)))


_REPO = Path(__file__).resolve().parents[2]


def _default_binary() -> str:
    """A native build in 86box/build-mcp made with -D AUTOMATION=ON, else the Docker wrapper."""
    build = _REPO / "86box" / "build-mcp"
    native = build / "src" / "86Box"
    try:
        automation = "AUTOMATION:BOOL=ON" in (build / "CMakeCache.txt").read_text()
    except OSError:
        automation = False
    return str(native if native.exists() and automation else _REPO / "docker" / "86box.sh")


BINARY = _env_path("BOX86_BINARY", _default_binary())

# Disk images we build must not depend on when they were built, or two runs of the same script
# differ (in DIR listings, for a start): a fixed volume serial for mformat, and a fixed timestamp
# for files we generate. Host files keep their own timestamps (mcopy -m).
VOLUME_SERIAL = "86B0C0DE"
FIXED_MTIME = 1_700_000_000  # 2023-11-14
# FAT dates are local time; mtools converts in UTC so images don't depend on the host's zone.
MTOOLS_ENV = {**os.environ, "TZ": "UTC"}
ROMS = _env_path("BOX86_ROMS", "~/tools/86box/roms")
MTOOLS_DIR = os.environ.get("BOX86_MTOOLS_DIR", os.path.expanduser("~/tools/hostpkgs/root/usr/bin"))


# A 486DX2-66 VLB board close to typical period hardware; time_sync disabled so
# runs are repeatable. Format with hdd_fn/cyls/heads/spt/speed.
DX2_66_CFG = """[General]
confirm_exit = 0
confirm_reset = 0
confirm_save = 0
vid_renderer = qt_software
time_sync = disabled

[Machine]
cpu_family = i486dx2
cpu_multi = 2
cpu_speed = 66666666
cpu_use_dynarec = 0
fpu_type = internal
machine = 486sp3c
mem_size = 16384

[Video]
gfxcard = cl_gd5428_migrated_vlb

[Input devices]
keyboard_type = keyboard_ps2
mouse_type = ps2

[Sound]
sndcard = sb16

[Hard disks]
hdd_01_fn = {hdd_fn}
hdd_01_ide_channel = 0:0
hdd_01_parameters = {spt}, {heads}, {cyls}, 0, ide
hdd_01_speed = {speed}

[Floppy and CD-ROM drives]
fdd_01_type = 35_2hd
cdrom_01_parameters = 1, atapi
cdrom_01_ide_channel = 0:1

[Cirrus Logic GD5428 (VLB)]
bios = cl_gd5428_vlb
"""


def write_config(vm_dir: str | os.PathLike, cyls: int, heads: int, spt: int, hdd_fn: str = "hdd.img",
                 speed: str = "ramdisk") -> Path:
    """Write an 86box.cfg for the DX2-66 machine with one IDE disk."""
    cfg = Path(vm_dir) / "86box.cfg"
    cfg.write_text(DX2_66_CFG.format(hdd_fn=hdd_fn, cyls=cyls, heads=heads, spt=spt, speed=speed))
    return cfg


def _ini_set(lines: list[str], section: str, key: str, value: str) -> list[str]:
    """Set key in [section] of an 86Box-style INI held as lines, adding either if missing."""
    header = f"[{section}]"
    try:
        start = lines.index(header)
    except ValueError:
        if lines and lines[-1].strip():
            lines.append("")
        return lines + [header, f"{key} = {value}"]
    end = start + 1
    while end < len(lines) and not lines[end].startswith("["):
        if lines[end].split("=", 1)[0].strip() == key:
            lines[end] = f"{key} = {value}"
            return lines
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return lines[:end] + [f"{key} = {value}"] + lines[end:]


def _ini_get(lines: list[str], section: str, key: str) -> str | None:
    inside = False
    for line in lines:
        if line.startswith("["):
            inside = line.strip() == f"[{section}]"
        elif inside and line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return None


def configure_serial_capture(vm_dir: str | os.PathLike, port: int = 1) -> Path | None:
    """Send COM<port> output to <vm_dir>/com<port>.log via 86Box's file backend.

    Leaves the port alone if the VM already attaches something else to it. Returns the log path,
    or None if the port is in use.
    """
    cfg = Path(vm_dir) / "86box.cfg"
    lines = cfg.read_text().splitlines()
    log = (Path(vm_dir) / f"com{port}.log").resolve()
    device = _ini_get(lines, "Ports (COM & LPT)", f"serial{port}_device")
    if device not in (None, "none", "file"):
        return None
    section = f"File (COM) #{port}"
    if device == "file" and _ini_get(lines, section, "path") not in (None, str(log)):
        return None  # the user's own file capture
    lines = _ini_set(lines, "Ports (COM & LPT)", f"serial{port}_device", "file")
    lines = _ini_set(lines, section, "path", str(log))
    # An input file makes the backend raise DSR/DCD; without them the BIOS (INT 14h) refuses to send
    # and DOS reports "Write fault error writing device COM1". /dev/null supplies no input.
    lines = _ini_set(lines, section, "input_path", "/dev/null")
    # Write each byte through to the file, so serial_read sees it while the VM runs.
    lines = _ini_set(lines, section, "flush", "1")
    cfg.write_text("\n".join(lines) + "\n")
    return log


def _free_port() -> int:
    """An unused loopback TCP port (86Box binds it a moment later)."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tool(name: str) -> str:
    found = shutil.which(name) or shutil.which(name, path=MTOOLS_DIR)
    if not found:
        raise FileNotFoundError(f"{name} not found on PATH or in {MTOOLS_DIR}")
    return found


class VM:
    """One running 86Box process with an automation connection."""

    def __init__(self, vm_dir: str | os.PathLike, binary: Path = BINARY, roms: Path = ROMS):
        self.vm_dir = Path(vm_dir).expanduser().resolve()
        self.binary = Path(binary).expanduser()
        self.roms = Path(roms).expanduser()
        self.run_dir = Path(tempfile.mkdtemp(prefix="box86mcp-"))
        self.log_path = self.run_dir / "86box.log"
        self.proc: subprocess.Popen | None = None
        self.serial_logs: dict[int, Path] = {}
        self.serial_offsets: dict[int, int] = {}
        # UNIX socket on Linux/macOS (owner-only); TCP on loopback where there are no UNIX sockets.
        self.transport = os.environ.get("BOX86_TRANSPORT", "tcp" if os.name == "nt" else "unix")
        if self.transport == "tcp":
            self.token_file = str(self.run_dir / "automation.token")
            self.endpoint = f"tcp:127.0.0.1:{_free_port()}"
        elif self.transport == "unix":
            self.token_file = None
            self.endpoint = "unix:" + str(self.run_dir / "automation.sock")
        else:
            raise ValueError(f"BOX86_TRANSPORT must be unix or tcp, not {self.transport!r}")
        self.auto = Automation(self.endpoint, self.token_file)
        self._media_seq = 0

    def start(self, held: bool = True, window: bool = False, extra_args: list[str] | None = None,
              com1_log: bool = False) -> dict:
        """Launch 86Box. window=True shows the normal 86Box window on $DISPLAY. com1_log=True captures
        COM1 output (off by default: with it, runs are not always byte-for-byte reproducible)."""
        if not (self.vm_dir / "86box.cfg").exists():
            raise FileNotFoundError(f"{self.vm_dir}/86box.cfg not found")
        if not self.binary.exists():
            raise FileNotFoundError(f"86Box binary {self.binary} not found (set BOX86_BINARY)")
        log = configure_serial_capture(self.vm_dir, 1) if com1_log else None
        if log:
            self.serial_logs[1] = log
            self.serial_offsets[1] = 0
        args = [str(self.binary), "-P", str(self.vm_dir), "-R", str(self.roms), "-N",
                "--automation", self.endpoint]
        if self.token_file:
            args += ["--automation-token-file", self.token_file]
        if held:
            args.append("--automation-hold")
        args += extra_args or []
        env = dict(os.environ)
        if window:
            if not env.get("DISPLAY"):
                raise RuntimeError("window=True needs DISPLAY set to an X server (Wayland users: XWayland)")
            env["QT_QPA_PLATFORM"] = "xcb"
        else:
            env["QT_QPA_PLATFORM"] = "offscreen"
        log = open(self.log_path, "wb")
        # Through guard.py, which stops 86Box if this process dies without calling stop(): it holds
        # the read end of the stdin pipe, and only we hold the write end.
        guard = [sys.executable, str(Path(__file__).with_name("guard.py"))]
        self.proc = subprocess.Popen(guard + args, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                     env=env, start_new_session=True)
        log.close()
        try:
            self.auto.connect(wait=60)
        except AutomationError:
            self.stop()
            raise AutomationError(f"86Box did not open its automation socket; log tail:\n{self.log_tail()}")
        status = self.auto.status()
        if status.get("protocol") not in SUPPORTED_PROTOCOLS:
            self.stop()
            raise AutomationError(f"86Box speaks automation protocol {status.get('protocol')}; "
                                  f"this server supports {sorted(SUPPORTED_PROTOCOLS)}")
        return status

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 10.0) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.auto.call("quit")
            except (AutomationError, OSError):
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                # SIGTERM first: docker/86box.sh traps it to kill its container.
                os.killpg(self.proc.pid, signal.SIGTERM)
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait()
        if self.proc and self.proc.stdin:
            self.proc.stdin.close()
        self.auto.close()
        # Media copies, the socket and the token aren't needed once 86Box has gone; the log stays.
        for f in self.run_dir.iterdir():
            if f.name == self.log_path.name:
                continue
            if f.is_dir() and not f.is_symlink():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)

    def hdd_image(self) -> Path:
        """Path of the primary hard disk image named in 86box.cfg."""
        for line in (self.vm_dir / "86box.cfg").read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "hdd_01_fn":
                p = Path(value.strip())
                return p if p.is_absolute() else self.vm_dir / p
        raise FileNotFoundError("no hdd_01_fn in 86box.cfg")

    def serial_read(self, port: int = 1, from_start: bool = False) -> str:
        """Text the guest wrote to COM<port> since the last read (or since boot)."""
        log = self.serial_logs.get(port)
        if log is None:
            raise RuntimeError(f"COM{port} is not captured (the VM attaches another device to it)")
        offset = 0 if from_start else self.serial_offsets.get(port, 0)
        try:
            with open(log, "rb") as f:
                f.seek(offset)
                data = f.read()
        except FileNotFoundError:
            return ""
        self.serial_offsets[port] = offset + len(data)
        return data.decode("latin-1")

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except FileNotFoundError:
            return ""

    # Media ---------------------------------------------------------------

    def make_floppy(self, files: list[str | os.PathLike]) -> Path:
        """Build a 1.44 MB FAT12 floppy image holding the given files and directories."""
        self._media_seq += 1
        img = self.run_dir / f"transfer{self._media_seq}.img"
        with open(img, "wb") as f:
            f.truncate(1474560)
        subprocess.run([_tool("mformat"), "-i", str(img), "-f", "1440", "-N", VOLUME_SERIAL, "::"],
                       check=True, capture_output=True, env=MTOOLS_ENV)
        for src in files:
            src = Path(src).expanduser()
            cmd = [_tool("mcopy"), "-i", str(img), "-s", "-o", "-m", str(src), "::"]
            r = subprocess.run(cmd, capture_output=True, text=True, env=MTOOLS_ENV)
            if r.returncode:
                raise RuntimeError(f"mcopy {src} failed: {r.stderr.strip()} (floppy holds 1.44 MB)")
        return img

    def make_iso(self, files: list[str | os.PathLike], label: str = "TRANSFER") -> Path:
        """Build an ISO 9660 image (with Joliet long names) holding the given files and directories."""
        import pycdlib

        self._media_seq += 1
        iso_path = self.run_dir / f"transfer{self._media_seq}.iso"
        iso = pycdlib.PyCdlib()
        iso.new(interchange_level=1, joliet=3, vol_ident=label[:32].upper())
        used: set[str] = set()

        def dos_name(name: str, is_dir: bool) -> str:
            stem, _, ext = name.upper().rpartition(".") if "." in name and not is_dir else (name.upper(), "", "")
            clean = lambda s, n: "".join(c if c.isalnum() or c == "_" else "_" for c in s)[:n] or "_"
            base, ext = clean(stem, 8), clean(ext, 3) if ext else ""
            candidate, i = base, 1
            while (candidate + ("." + ext if ext else "")) in used:
                suffix = f"~{i}"
                candidate, i = base[: 8 - len(suffix)] + suffix, i + 1
            full = candidate + ("." + ext if ext else "")
            used.add(full)
            return full

        def add(src: Path, iso_dir: str, joliet_dir: str) -> None:
            short = dos_name(src.name, src.is_dir())
            iso_name, joliet_name = f"{iso_dir}/{short}", f"{joliet_dir}/{src.name}"
            if src.is_dir():
                iso.add_directory(iso_name, joliet_path=joliet_name)
                for child in sorted(src.iterdir()):
                    add(child, iso_name, joliet_name)
            else:
                iso.add_file(str(src), iso_name + ";1", joliet_path=joliet_name)

        for f in files:
            src = Path(f).expanduser()
            if not src.exists():
                raise FileNotFoundError(src)
            add(src, "", "")
        iso.write(str(iso_path))
        iso.close()
        return iso_path

    def load_cdrom(self, image: str | os.PathLike, drive: int = 0) -> dict:
        """Insert a CD image. Images outside paths 86Box can see are copied into the run directory."""
        image = Path(image).expanduser().resolve()
        visible = (Path.home(), Path("/tmp"))
        if not any(image.is_relative_to(p) for p in visible):
            copy = self.run_dir / image.name
            shutil.copy(image, copy)
            image = copy
        return self.auto.call(f"cd load {drive} {image}")

    def load_floppy(self, img: str | os.PathLike, drive: int = 0) -> dict:
        return self.auto.call(f"fdd load {drive} {img}")



class AttachedVM(VM):
    """An 86Box started elsewhere (another machine, or by hand), reached at an endpoint.

    vm_stop disconnects rather than powering it off.
    """

    def __init__(self, endpoint: str, token: str | None = None):
        self.vm_dir = None
        self.run_dir = Path(tempfile.mkdtemp(prefix="box86mcp-attach-"))
        self.log_path = self.run_dir / "86box.log"
        self.proc = None
        self.serial_logs = {}
        self.serial_offsets = {}
        self._media_seq = 0
        self.endpoint = endpoint
        self.token_file = None
        if endpoint.startswith("tcp:"):
            if not token:
                raise ValueError("a TCP endpoint needs the token from 86Box's token file")
            self.token_file = str(self.run_dir / "automation.token")
            fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(token.strip() + "\n")
        self.auto = Automation(endpoint, self.token_file)
        self.auto.connect(wait=10)

    def running(self) -> bool:
        return self.auto.sock is not None

    def stop(self, timeout: float = 10.0) -> None:
        self.auto.close()

    def power_off(self) -> None:
        """Ask 86Box to power off and exit (the "quit" command), then disconnect."""
        try:
            self.auto.call("quit")
        except (AutomationError, OSError):
            pass  # it may close the connection as it exits
        self.auto.close()

    def hdd_image(self) -> Path:
        raise RuntimeError("the disk image of an attached VM is on the other machine")
