# 86box-mcp and V9x Remote Agent on one Windows 98 VM

[V9x Remote Agent](https://github.com/michaeldale/V9x-Remote-Agent) is an
agent program that runs *inside* Windows 9x and gives an MCP client commands,
files, screenshots and input through the guest's own Windows APIs. 86box-mcp
works from *outside*, at the hardware. This example installs Windows 98 and the
agent into an 86Box VM, then drives that VM through **both MCP servers at
once** and records which combinations work.

`crossover.py` is the demonstration: an MCP client that connects to both
servers over stdio, runs the scenarios below, captions the 86Box recording as
it goes, and writes `out/results.md`.

## Setting it up

### 1. Windows 98 install media

We used [Windows 9x QuickInstall](https://github.com/oerg866/win98-quickinstall)
(`win98qi`), a bootable ISO that copies a pre-patched Windows 98 SE onto the
disk in a few minutes. Its DOS boot floppy chains straight into it, so with the
floppy and the ISO both inserted, no BIOS boot-order changes are needed. Any
Windows 98 SE media works; you need your own licence.

### 2. The VM

Create it from an `86box.cfg` for a Pentium-class machine, with the new
`config` parameter. Ours was a Socket 7 board (`p55t2p4`), Pentium MMX 233,
**128 MB**, S3 Trio64 PCI, SB16, PS/2 mouse, a 1 GB IDE disk and an ATAPI
CD-ROM, with `cpu_use_dynarec = 1`. On our host the interpreter ran Windows 98
at about a third of real time (4 s of emulated time took 12.6 s); the
recompiler took 6.6 s. Add the network card and the port forward
V9x needs, exactly as V9x's own 86Box quickstart says:

```ini
[Network]
net_01_card = ne2kpnp
net_01_net_type = slirp

[SLiRP Port Forwarding #1]
0_protocol = tcp
0_external = 9869
0_internal = 9869
```

Then:

```
vm_create(vm_dir, cylinders=2080, config="win98-template.cfg", nvr="<a saved CMOS for that machine>")
```

SLiRP is 86Box's user-mode NAT: the guest gets `10.0.2.15` by DHCP, Windows 98
detects the NE2000 PnP card by itself, and the forward makes the agent's port
appear on the host. **It listens on every host address, not just localhost**
(`0.0.0.0:9869`), and V9x's protocol has no authentication, so firewall it.
pcap, TAP and VDE would bridge the guest onto your LAN; V9x's docs say not to.

### 3. Install Windows 98 and the agent

Boot with the QuickInstall floppy and ISO inserted (`floppy_insert`,
`cdrom_insert`). In QuickInstall: `[DISK]` to partition the blank disk with
cfdisk (capital `W` writes the table), then `[INSTALL]` with the defaults. Five
minutes of emulated time later, eject both and reboot to the desktop.

Insert `V9XREMOTE.ISO` from a V9x release, run `D:\SETUP.EXE` (Start > Run;
AutoPlay didn't fire for us), accept the defaults, Install, Finish with
"Restart Windows now" ticked. After the restart the agent starts with Windows
and sits in the tray.

`record_start` before booting captures the whole install; ours rendered to a
38-second video.

### 4. Both servers at once

```sh
claude mcp add 86box -- /path/to/86box-mcp/server/.venv/bin/box86mcp
claude mcp add v9x -- python /path/to/V9x-Remote-Agent/mcp/v9x_mcp.py --host 127.0.0.1 --port 9869
```

`v9x_mcp.py` needs nothing beyond the Python standard library. Start the VM
with 86box-mcp's `vm_start`, `release`, and `v9x_wait_desktop`.

## The one rule: V9x needs emulated time to pass

The agent is a TCP server inside the guest. While 86box-mcp holds the machine,
the guest's TCP stack is frozen, so every V9x call times out. Three ways to
work:

- **`release`**, then use V9x freely in real time. `hold` again for exact
  steps, memory and frames.
- **Stay held and `step`**: a V9x request sent while a `step` is running
  completes inside the step. The machine is still held afterwards.
- **After a V9x action, `hold` and step frame by frame** to examine what it
  did.

## Pacing V9x calls around held time

The V9x agent serves one TCP client at a time, through SLiRP, and its
requests only make progress while emulated time passes. Calls sent while the
machine is held don't fail cleanly. They queue up:

- The host side connects at once, because SLiRP's listener runs on the host.
  The guest side of the connection doesn't exist yet: it needs the guest's TCP
  stack to run.
- The V9x client gives up after its timeout and closes. When the machine runs
  again, the guest sees those dead connections and the agent works through
  them one at a time, before it reaches the live one.
- In our runs, a connection opened right behind the previous one was
  occasionally dropped ("peer closed the connection").

So:

- **One V9x call at a time**, never several in parallel.
- **Don't send V9x calls while held.** `release` first, or send the call while
  a `step` runs, and give the step a margin: a ping fits easily in 4 s of
  emulated time, and a screenshot transfer needs more.
- **After a timeout, let the guest drain.** Run a few seconds of emulated time,
  then `v9x_ping` until it answers, before the next real call.
- **Retry once on "peer closed the connection"**, after a short pause.
  `crossover.py` does this.

## What works together (from `crossover.py`)

| Scenario | Result |
|---|---|
| Boot held, `record_start`, `release`, `v9x_wait_desktop`, `v9x_ping` | works |
| `v9x_ping` while held | times out, by design |
| `v9x_ping` sent during `step(4000)` while held | works; the machine is still held afterwards |
| `vm_info` vs `v9x_inventory` | agree: 86Box reports 128 MB, the guest sees 127 MB |
| `v9x_screenshot` vs `screenshot` | the same desktop; only the pointer differs |
| `screen_text` on the Windows desktop | not a text mode, by design; V9x is the way to read text |
| `v9x_put_file`, then `v9x_shell TYPE` | works |
| `dos_install_files` while Windows runs | refused, by design: 86box-mcp's disk tools need the VM stopped |
| V9x opens a file in Notepad, 86box-mcp `find_memory` for its text | works: found in RAM in about 7 s |
| `hold`, then `step(20)` five times with Notepad open | works: 6 frames in 100 ms |
| 86box-mcp `type_text` into Notepad, V9x closes it and answers the save prompt, `v9x_get_file` | works |
| `v9x_move` to (100,100), 86box-mcp `mouse(dx=40)`, V9x reports the pointer | works: 40 counts moved it 40 px, and V9x can read the pointer position back |
| `cdrom_insert(files=...)`, then `v9x_list_dir D:\` | works |
| `reset(hard)`, then `v9x_wait_desktop`; boot counter up by one | works |

What each side is for, in short: use **86box-mcp** for time (hold, exact
steps), frames from the video card, physical memory, media, resets and
recordings; use **V9x** for running programs with exit codes and output, file
transfer while Windows runs, reading text, and pointer placement with a
read-back.

## Things we hit

- **A copied `86box.cfg` carries its `uuid`.** With a network card configured,
  86Box treats a known uuid in a new folder as a moved or copied machine and
  stops at a dialog *before* starting the emulator thread. Headless, that's an
  invisible hang: the automation socket listens and connects but never answers.
  `vm_create(config=...)` now drops `uuid` and `mac` lines from the template.
- **`cfdisk` writes with capital `W`.** Lowercase `w` does nothing, and the
  Enter after it selects Quit.
- **V9x's `Ctrl+S`** didn't save in Notepad; closing with `Alt+F4` and answering
  the prompt did.
