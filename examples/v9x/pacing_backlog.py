"""Hold, fire N V9x pings at once, release, then ping every 0.3 s for 25 s: which succeed, and when.

    python pacing_backlog.py V9X_REPO/mcp ../skifree/skictl.py CTL_SOCK 0,1,5,10,16,40

Needs a VM run by `skictl.py serve --raw` with the agent at the desktop."""
import subprocess, sys, threading, time
sys.path.insert(0, sys.argv[1])
from v9x_mcp import V9xClient
CTL = [sys.executable, "-W", "ignore", sys.argv[2], "run", "--sock", sys.argv[3]]
ctl = lambda code: subprocess.run(CTL + [code], capture_output=True, text=True).stdout.strip()
def ping(timeout):
    try:
        with V9xClient("127.0.0.1", 9869, timeout=timeout) as c: c.ping(); return True
    except Exception as e: return type(e).__name__[:4]
for n in [int(x) for x in sys.argv[4].split(",")]:
    for rep in range(2):
        ctl("m.a.call('hold')")
        th = [threading.Thread(target=ping, args=(3,)) for _ in range(n)]
        [x.start() for x in th]; [x.join() for x in th]
        ctl("m.a.release()")
        t0 = time.time(); out = []
        while time.time() - t0 < 25:
            r = ping(2); out.append(f"{time.time()-t0:4.1f}:{'ok' if r is True else r}")
            time.sleep(0.3)
        fails = [o for o in out if not o.endswith("ok")]
        print(f"N={n:2d} rep{rep}: {len(out)} pings, {len(fails)} failed: {' '.join(fails)[:300]}")
        time.sleep(5)
