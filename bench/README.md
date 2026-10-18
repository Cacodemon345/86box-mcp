# 86Box release benchmark: 3DMark 99 on a Voodoo 2

`bench3dmark.py` runs one 86Box build, or several to compare, through the same
workload, and reports reliability, speed and the benchmark's own results.
`test_bench3dmark.py` turns one run into pass/fail checks for CI. The
workload is Windows 98 SE booting, then 3DMark 99 MAX's full default benchmark
on a 3dfx Voodoo 2.

```sh
python bench/bench3dmark.py --image IMAGE --v9x-repo V9X \
    --binary new=/path/to/new/86Box --binary old=/path/to/old/86Box --runs 3
```

## What it measures

Each run starts from a pristine copy of the prepared image, so every build
sees exactly the same disk.

| | How | What a change means |
|---|---|---|
| **Reliability** | Every phase must finish: boot to the taskbar, the benchmark to its score, the export. No hard resets. A run that stops answering (an 86Box `fatal()`, a deadlock) fails with the last log line. | The build crashes or hangs on this workload. |
| **Speed** | The boot and the benchmark run **held and unpaced**, stepping as fast as the host allows. Speed is emulated seconds per wall second, as a percentage. | The build got slower or faster. Real-time pacing would cap it at 100 % and hide this. |
| **3DMark's results** | The overall score, CPU 3DMarks, rasterizer marks and every sub-test, from 3DMark's own text export. | The benchmark measures in emulated time, so its results reflect how 86Box emulates the hardware's timing. A changed result means the emulation changed. |

The speed numbers depend on the host. Compare builds on the same machine, in
the same session, with nothing else heavy running.

## Results so far

From two 2014 Xeon E5-2640 v3 (2 × 8 cores, 2.6 GHz), 86Box built in Docker (Debian
trixie, Qt 6, Release):

| | master `fff297459` (new recompiler) | `2804cabd6` (old recompiler) |
|---|---|---|
| Runs completed | 4 of 4 | 0 of 7 (2 of them with #8392): `fatal("Over limit!")` when the Result Browser starts |
| Boot to the taskbar | 30 s emulated, at 138–143 % | 30 s emulated, at 121–125 % |
| Benchmark | 314 s emulated in 313–325 s, at 97–100 % | 324–326 s emulated, at 76–82 % |
| 3DMark score | 1,645, 1,842, 1,648, 1,659 | — |
| CPU 3DMarks | 2,066–2,072 | — |
| Game 1 – Race | 17.7–18.1 FPS | — |
| Game 2 – First Person | 15.3, 18.7, 15.4, 15.6 FPS | — |
| Rasterizer score | 18,776–20,998 (4 runs) | — |
| Fill rate, multi-texturing | 3,883–4,046 MTexels/s | — |

Boot, benchmark and fill-rate figures for master are from three side-by-side
runs, and for the old build from all seven; master's other results add a
fourth run, from `pytest`. The old recompiler's build is about 20 % slower on
this workload. Runs of the same build differ by a few percent in speed, and in
3DMark's results as described below.

## Preparing the image

The image isn't distributed: it contains Windows 98. It takes about an hour to
build, once, mostly with [`examples/v9x`](../examples/v9x/) tooling:

1. **Windows 98 SE with V9x Remote Agent**, on a Pentium MMX 233 (`p55t2p4`)
   with 128 MB, S3 Trio64, SB16, an NE2000 PnP on SLiRP with port 9869
   forwarded, and a 1 GB disk. See the
   [V9x example](../examples/v9x/README.md#setting-it-up). The agent runs the
   GUI steps of a benchmark run; the benchmark itself doesn't depend on it.
2. **Add the Voodoo 2** to `86box.cfg`, and set `time_sync = disabled` under
   `[General]` so the guest clock follows emulated time:

   ```ini
   [Video]
   voodoo = 1

   [3Dfx Voodoo Graphics]
   type = voodoo_2
   framebuffer_memory = 4
   texture_memory = 4
   render_threads = 2
   recompiler = 1
   ```

3. **Driver:** Windows finds the card at the next boot. We used
   FastVoodoo2 4.6 (3dfxzone.it), which Windows 9x QuickInstall's driver pack
   installs on its own. Restart when asked.
4. **DirectX 7.0a** (`dx7aeng`), then restart. FastVoodoo2 needs DirectX 7.
   Without it, 3DMark says it needs a DirectX 6 compatible 3D accelerator.
5. **3DMark 99 MAX** (freeware, `3DMark99Max.exe`) with every default, into
   `C:\Program Files\3DMark 99 Max`. Choose "Purchase Later", and untick the
   readme. Create `C:\SETUP`, where runs save their project and export.
6. Run the benchmark once by hand to check it, then close 3DMark and **shut
   Windows down** (Start > Shut Down, or `v9x_shutdown`), so runs don't start
   with ScanDisk.
7. The image folder holds `86box.cfg` (without its `uuid` line), `nvr/` and
   `hdd.img`.

The tool finds its way around the screen with four small templates in
`templates/` and fixed click positions, so the image has to match: the 640×480
desktop, and 3DMark's windows where they open by default. A different image
needs new templates; `TEMPLATE_BOXES` in `bench3dmark.py` says where each one is
cut from.

## In CI

```sh
BENCH_IMAGE=... BENCH_BINARY=... V9X_REPO=... BENCH_MIN_SPEED=80 pytest bench/
```

- `test_reliability`: the run finished, with no hard reset.
- `test_fidelity`: the results that are stable from run to run are within
  `BENCH_TOLERANCE` (default 5 %) of `baseline.json`.
- `test_performance`: the benchmark ran at no less than `BENCH_MIN_SPEED` %
  (no default: it depends on the host).

## What it has found

- **A crash in the old dynamic recompiler.** 86Box at `2804cabd6`, an x86-64
  build with the old recompiler (then the default), stops with
  `fatal("Over limit!")` every time 3DMark's Result Browser starts, after the
  benchmark itself has finished; it also did 43 s into the first boot with the
  Voodoo 2, while Windows installed its driver. The interpreter, and builds
  after the old recompiler's removal (`f50f8cdf7`), are fine.
- **A crash looked like a hang.** `fatal()` showed its dialog with the machine
  paused, and the automation socket stopped answering: a headless client saw
  only timeouts.
  [86Box#8392](https://github.com/86Box/86Box/pull/8392) closes the socket
  first. Tested on `2804cabd6`: the socket closes at once instead.
- **Game 2 runs at one of two speeds.** Identical runs of the same build give
  15.3 or 18.7 FPS in Game 2, and nothing in between, so the overall score is
  about 1,645 or 1,842. 3DMark measures in emulated time, so something
  outside it decides; our guess is the Voodoo's render threads, which run on
  the host. CPU 3DMarks, Game 1 and bump mapping stay within 3 %.
- **Multi-texturing is far too fast.** The multi-texturing fill rate comes out
  at 3,900–4,000 MTexels/s, about 20 times a real Voodoo 2 (two texture units
  at 90 MHz: 180). Single-texture fill rate, 32–34 MTexels/s, is below the
  real card's 90. The emulated card's speed isn't modelled closely, in emulated
  time.

Because of the variation, `baseline.json` checks only the results that stay
within 3 % from run to run (CPU 3DMarks, Game 1, 4 MB textures, bump
mapping), and the report shows the median and range of several runs: use
`--runs 3` or more to compare builds.
