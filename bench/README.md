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
| **CPU-bound results** | CPU 3DMarks, from 3DMark's own text export. 3DMark measures in emulated time, and this result depends only on the emulated CPU: within 0.2 % over 11 runs, whatever the host did. | 86Box now emulates the CPU's timing differently. |
| **Voodoo-bound results** | The overall score, the two games, fill rate, bump mapping and texture tests. | **The host's Voodoo rendering got slower or faster.** The emulated Voodoo 2 has no speed of its own: it runs as fast as 86Box's render threads draw on the host (see [below](#the-emulated-voodoo-2-runs-at-the-hosts-speed)). |

The speed numbers and the Voodoo-bound results depend on the host. Compare
builds on the same machine, in the same session, with the same Voodoo
settings and nothing else heavy running, and use `--runs 3` or more: the
overall score varied by 12 % between runs of the same build.

## Results so far

From two 2014 Xeon E5-2640 v3 (2 × 8 cores, 2.6 GHz), 86Box built in Docker (Debian
trixie, Qt 6, Release):

| | master `fff297459` (new recompiler) | `2804cabd6` (old recompiler) |
|---|---|---|
| Runs completed | 4 of 4 | 0 of 7 (2 of them with #8392): `fatal("Over limit!")` when the Result Browser starts |
| Boot to the taskbar | 30 s emulated, at 138–143 % | 30 s emulated, at 121–125 % |
| Benchmark | 314 s emulated in 313–325 s, at 97–100 % | 324–326 s emulated, at 76–82 % |
| 3DMark score | 1,639–1,842 (7 runs) | — |
| CPU 3DMarks | 2,066–2,072 | — |
| Game 2 – First Person | 15.2–18.8 FPS | — |
| Fill rate | 32.4–35.4 MTexels/s | — |

Boot and benchmark figures for master are from three side-by-side runs, and
for the old build from all seven. The old recompiler's build is about 20 %
slower on this workload.

## The emulated Voodoo 2 runs at the host's speed

3DMark's Voodoo-bound results varied from run to run of the same build, so we
changed only the host side of the emulated card, one run each, and kept the
emulated machine identical:

| | 2 render threads (stock, 2 runs) | 1 thread | 4 threads | Voodoo recompiler off | 1 host CPU for 86Box |
|---|---|---|---|---|---|
| CPU 3DMarks | 2,066 / 2,069 | 2,070 | 2,067 | 2,068 | 2,067 |
| Fill rate (MTexels/s) | 35.4 / 33.8 | 15.8 | 63.5 | 9.3 | 36.2 |
| Bump mapping, 3-pass (FPS) | 39.3 / 39.1 | 19.7 | 73.0 | 12.0 | 45.4 |
| Game 1 (FPS) | 17.8 / 18.1 | 16.0 | 18.4 | 6.6 | 18.4 |
| Game 2 (FPS) | 15.2 / 17.2 | 11.0 | 20.1 | 7.8 | 13.6 |
| 3DMark score | 1,639 / 1,763 | 1,303 | 1,924 | 713 | 1,565 |

- **Fill rate follows the host's render threads.** It roughly doubles with
  each doubling of the threads, and falls to a quarter without the renderer's
  recompiler. CPU 3DMarks stays within 0.2 % throughout. A real Voodoo 2
  fills 90 Mpixels/s whatever the host; this one fills as fast as the host
  draws, measured in emulated time.
- **It leaks in through polling.** The card's status register (`SST_status`)
  reports busy while the host's FIFO or render threads still have work.
  Drivers poll it until the card is idle, and every poll costs emulated time.
  86Box's wait statistics (`VOODOO_WAIT_STATS=1`) show that the other route,
  the CPU blocking in wall time with emulated time stopped, was never taken
  (zero FIFO-full, FIFO-empty and render waits) except six times with one host
  CPU.
- **The guest did exactly the same thing every time:** 3,490,344
  framebuffer reads, 4,136 of them synchronised, in every run.
- **With one host CPU the results stop making sense.** The CPU and render
  threads take turns, rendering happens while emulated time stands still, and
  the 2 MB texture test jumps to 1,488 FPS while the multi-texturing fill rate
  drops to 49.
- **The multi-texturing fill rate measures nothing useful:** 1,737–4,216
  MTexels/s, unrelated to the thread count. A real card manages 180.

So the Voodoo-bound results are a benchmark of 86Box's Voodoo **renderer** on
the host, which is useful for A/B runs on the same machine, but not of the
emulated card's fidelity. Making them faithful would mean giving the emulated
card a speed in emulated time: busy until the pixels it was given would have
taken a real Voodoo 2 to draw, with the CPU waiting in wall time only if the
host falls behind that.

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
- `test_fidelity`: the CPU-bound results (CPU 3DMarks) are within
  `BENCH_TOLERANCE` (default 2 %) of `baseline.json`.
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
- **The emulated Voodoo 2 has no speed of its own.** Its results follow the
  host's rendering: twice the render threads, about twice the fill rate. See
  [above](#the-emulated-voodoo-2-runs-at-the-hosts-speed). So `baseline.json`
  checks only CPU 3DMarks, and the Voodoo-bound results are for comparing
  renderer speed between builds on one host.
