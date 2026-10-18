# Setting up V9x Remote Agent alongside 86box-mcp (working notes)

Raw notes taken while doing it; the README section is distilled from these.

## Pieces

- **Windows 98 SE install media:** `win98qi` (Windows 9x QuickInstall by
  oerg866), the `stock` ISO plus its DOS boot floppy. QuickInstall is a Linux
  kernel + installer that copies a pre-patched Win98 SE onto the disk in a few
  minutes; the floppy boots FreeDOS and chains into it (`FINDCD.BAT` then
  `INSTALL.BAT` → `loadlin`), so floppy + CD attached needs no BIOS boot-order
  changes.
- **V9x Remote Agent 0.9.1:** release assets `V9XREMOTE.ISO` (guest installer)
  and the repo (for `mcp/v9x_mcp.py`, pure standard-library Python).

## The VM

Created with the new `vm_create(config=..., nvr=...)` from the user's
`pmmx233-pci` config as the template:

- `p55t2p4` (Socket 7, i430HX), Pentium MMX 233, **128 MB**, S3 Trio64 PCI,
  SB16, PS/2 mouse, 1 GB IDE disk (2080/16/63), ATAPI CD-ROM on IDE 0:1.
- Network: `net_01_card = ne2kpnp`, `net_01_net_type = slirp`, and
  `[SLiRP Port Forwarding #1]` with `0_protocol = tcp`, `0_external = 9869`,
  `0_internal = 9869`. V9x's own quickstart recommends exactly this NIC.
- The CMOS came from the template VM's `nvr/` folder (same machine), so the
  BIOS doesn't stop at setup.

## Bug found on the way (fixed in 86box-mcp `74dfa3c`)

Copying a template `86box.cfg` carried its `uuid` into the new folder. With a
network card configured, 86Box's Qt frontend compares that uuid with the one it
derives for the folder and, on mismatch, shows "This machine might have been
moved or copied" *before* starting the emulator thread. Headless, that's an
invisible hang: the automation socket listens (it's set up at the end of
`pc_init_modules()`), connects, and never answers. Diagnosis: the 86Box log
stopped right after "Automation: listening", and `/proc/<pid>/task` showed no
emulator thread. Fix: the template copy now drops `uuid` and `mac` lines; 86Box
then just generates new ones with no prompt.

## Networking facts

- SLiRP's forward listens on **0.0.0.0:9869** on the host (confirmed with
  `ss -ltn`), not just localhost. V9x's protocol has no authentication, so
  anything that can reach the host on 9869 controls the guest. Keep it
  firewalled; don't bridge.
- Our Docker wrapper runs with `--network host`, so the forward is a plain host
  port; nothing extra to map.

## Time: the thing that makes the crossover interesting

86box-mcp holds emulated time between calls. V9x's agent lives inside the guest
and talks real-time TCP through SLiRP: with the machine held, the guest's TCP
stack doesn't run, so V9x calls can't complete. So: `release` (or long `step`s)
while using V9x; `hold` for exact stepping, memory and frame capture.
