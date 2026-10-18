"""Two inventories of one Windows 98 PC, side by side: what 86Box emulates, read from outside by
86box-mcp, and what Windows found, read from inside by V9x.

    BOX86_BINARY=<86Box built with -D GDBSTUB=ON> BOX86_ROMS=... python examples/v9x/hwview.py

86box-mcp scans the PCI bus through the GDB stub (vm_start(debugger=True)), as a driver would see
it. V9x exports Windows' Plug and Play tree (HKLM\\Enum) with regedit and fetches it. Then each
PCI function is matched to the Windows device on the same bus, device and function.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from crossover import BOX_SERVER, OUT, PYTHON, REPO, Both

GUEST_EXPORT = "C:\\V9XREMOTE\\TEMP\\ENUM.REG"


def parse_regedit(text: str) -> dict[str, dict[str, str]]:
    """REGEDIT4 export -> {key path: {value name: string value}}; non-string values are skipped."""
    keys: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            cur = keys.setdefault(line[1:-1], {})
        elif cur is not None and (m := re.match(r'^"([^"]*)"="(.*)"$', line)):
            cur[m.group(1)] = m.group(2).replace('\\\\', '\\').replace('\\"', '"')
    return keys


def windows_devices(keys: dict[str, dict[str, str]]) -> list[dict]:
    """The device instances: HKLM\\Enum\\<enumerator>\\<device id>\\<instance>, with a DeviceDesc."""
    out = []
    for path, vals in keys.items():
        parts = path.split("\\")
        if len(parts) == 5 and parts[1].lower() == "enum" and "DeviceDesc" in vals:
            out.append({"bus": parts[2], "id": parts[3], "instance": parts[4], "desc": vals["DeviceDesc"],
                        "class": vals.get("Class", ""), "driver": vals.get("Driver", "")})
    return out


def match_pci(scan: str, devices: list[dict]) -> list[tuple[str, str, str]]:
    """(86Box line, Windows device, verdict) for each PCI function, then PCI devices only Windows has."""
    rows, seen = [], set()
    for line in scan.splitlines():
        m = re.match(r"(\w\w):(\w\w)\.(\d) (\w{4}):(\w{4})", line)
        if not m:
            continue
        bus, dev, fn, ven, did = m.groups()
        want_id = f"VEN_{ven}&DEV_{did}".upper()
        want_inst = f"BUS_{bus}&DEV_{dev}&FUNC_{int(fn):02d}".upper()
        hit = [d for d in devices if d["bus"].upper() == "PCI" and d["id"].upper().startswith(want_id)
               and d["instance"].upper() == want_inst]
        if hit:
            seen.add(id(hit[0]))
            rows.append((line, f'{hit[0]["desc"]} ({hit[0]["class"]})', "matched"))
        else:
            rows.append((line, "", "emulated, but Windows has no device here"))
    for d in devices:
        if d["bus"].upper() == "PCI" and id(d) not in seen:
            what = ("Windows' own PCI interrupt steering entry, not a device" if d["id"].upper() == "IRQHOLDER"
                    else "only in Windows")
            rows.append(("", f'{d["desc"]} (`{d["id"]}\\{d["instance"]}`)', what))
    return rows


def cfg_value(vm_dir: Path, key: str) -> str:
    for line in (vm_dir / "86box.cfg").read_text().splitlines():
        if line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return "none"


# (what 86Box emulates, its 86box.cfg key, the Windows device class, the enumerators it could be under)
NON_PCI = [("network card", "net_01_card", "Net", ("ISAPNP", "PCI")),
           ("sound card", "sndcard", "MEDIA", ("ISAPNP", "Root", "PCI")),
           ("keyboard", "keyboard_type", "Keyboard", ("Root", "BIOS")),
           ("mouse", "mouse_type", "Mouse", ("Root", "BIOS")),
           ("floppy drive", "fdd_01_type", "DiskDrive", ("FLOP",)),
           ("hard disk", "hdd_01_parameters", "DiskDrive", ("ESDI",)),
           ("CD-ROM drive", "cdrom_01_parameters", "CDROM", ("SCSI",))]


def found_as(devices: list[dict], cls: str, buses: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(f'{x["desc"].strip()} (`{x["bus"]}\\{x["id"]}`)' for x in devices
                              if x["class"] == cls and x["bus"].upper() in (b.upper() for b in buses)))


async def scenarios(d: Both, vm_dir: Path) -> list[str]:
    md = []
    await d.b("vm_start", vm_dir=str(vm_dir), held=True, debugger=True)
    await d.b("record_start", directory=str(OUT / "hwview_frames"), interval_ms=100)
    await d.caption("Boot Windows 98, with 86box-mcp's debugger attached (GDB stub)")
    await d.b("release")
    await d.await_desktop(400)

    await d.caption("V9x, inside: regedit exports Windows' device tree (HKLM\\Enum)")
    await d.v("v9x_exec", application="C:\\WINDOWS\\REGEDIT.EXE",
              arguments=f"/e {GUEST_EXPORT} HKEY_LOCAL_MACHINE\\Enum", timeout_ms=60000)
    local = OUT / "hwview_enum.reg"
    await d.v("v9x_get_file", guest_path=GUEST_EXPORT, local_path=str(local))
    devices = windows_devices(parse_regedit(local.read_bytes().decode("latin-1")))
    print(f"  Windows lists {len(devices)} device instances", flush=True)

    await d.caption("V9x opens Device Manager")
    await d.v("v9x_exec", application="C:\\WINDOWS\\CONTROL.EXE", arguments="sysdm.cpl,,1", detach=True)
    await asyncio.sleep(6)
    await d.box_shot("hwview_devmgr")

    await d.caption("86box-mcp, outside: hold and scan the PCI bus (0xCF8/0xCFC)")
    await d.b("hold")
    scan = await d.b("pci_scan")
    print(scan, flush=True)

    await d.caption("Match: the same bus, device and function on both sides")
    rows = match_pci(scan, devices)
    md += ["### PCI: 86Box's bus against Windows' devices", "",
           "| 86Box (`pci_scan`) | Windows (`HKLM\\Enum\\PCI`) | |", "|---|---|---|"]
    md += [f"| `{a}` | {b} | {c} |" for a, b, c in rows]
    for a, b, c in rows:
        print(f"  [{c}] {a or '-'}  <->  {b or '-'}", flush=True)

    md += ["", "### Other devices: configured in 86Box, and what Windows made of them", "",
           "| 86Box (`86box.cfg`) | Windows |", "|---|---|"]
    for label, key, cls, buses in NON_PCI:
        hits = found_as(devices, cls, buses)
        md.append(f"| {label}: `{cfg_value(vm_dir, key)}` | {'; '.join(hits) or '**not found**'} |")
        print(f"  {label}: {cfg_value(vm_dir, key)} <-> {hits or 'NOT FOUND'}", flush=True)

    await d.caption("Registers through the debugger: S3 BAR0, VGA port 0x3CC")
    s3 = next((l for l in scan.splitlines() if " 5333:" in l), None)
    md += ["", "### Registers, read through the debugger", ""]
    if s3:
        bus, dev, fn = re.match(r"(\w\w):(\w\w)\.(\d)", s3).groups()
        cfg = await d.b("pci_config_read", bus=int(bus, 16), device=int(dev, 16), function=int(fn), offset=0x10, length=4)
        md.append(f"- S3 card, BAR0 (offset 0x10): `{cfg.splitlines()[-1]}`")
    md.append(f"- VGA miscellaneous output register: `{await d.b('io_read', port=0x3CC)}`")

    await d.b("release")
    await d.caption("Done")
    await asyncio.sleep(2)
    return md


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm", default=str(REPO / ".cache" / "v9x" / "vm"))
    ap.add_argument("--v9x-repo", default=str(REPO / ".cache" / "v9x" / "repo"))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hwview_calls.jsonl").unlink(missing_ok=True)
    env = dict(os.environ)
    box = StdioServerParameters(command=str(BOX_SERVER), env=env)
    v9x = StdioServerParameters(command=str(PYTHON), env=env,
                                args=[str(Path(a.v9x_repo) / "mcp" / "v9x_mcp.py"), "--host", "127.0.0.1",
                                      "--port", "9869", "--timeout", "30"])
    async with stdio_client(box) as (br, bw), stdio_client(v9x) as (vr, vw):
        async with ClientSession(br, bw) as bs, ClientSession(vr, vw) as vs:
            await bs.initialize()
            await vs.initialize()
            d = Both(bs, vs)
            d.calls_log = OUT / "hwview_calls.jsonl"
            try:
                md = await scenarios(d, Path(a.vm).expanduser())
            finally:
                try:
                    print(await d.b("record_stop", video=str(OUT / "hwview.mp4"), calls_log=str(d.calls_log)))
                except Exception as e:
                    print("render failed:", e)
                print(await d.b("vm_stop"))
    (OUT / "hwview.md").write_text("\n".join(md) + "\n")
    print("\n" + "\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
