#!/bin/sh
# The host-timing experiments in README.md. Each run: NAME IMAGE WRAPPER [RUNS]. The wrappers run
# 86Box with VOODOO_WAIT_STATS=1 (one also with --cpuset-cpus=0); the variant images change only
# render_threads or recompiler under [3Dfx Voodoo Graphics]. Paths are this machine's: adapt them.
cd $HOME/86box_mcp
E=.cache/e2e
run() {
  BOX86_ROMS=$HOME/tools/86box/roms PYTHONPATH=server server/.venv/bin/python -u bench/bench3dmark.py \
    --image $E/$2 --binary $1=$PWD/$E/$3 --runs ${4:-1} --out $E/exp/$1 --v9x-repo .cache/v9x/repo 2>&1 \
    | grep -E "^== |^   (ok|FAILED)"
}
run stock golden 86box-stats.sh 3
run rt1 variants/rt1 86box-stats.sh
run rt4 variants/rt4 86box-stats.sh
run norec variants/norec 86box-stats.sh
run onecpu golden 86box-stats-1cpu.sh
echo ALL DONE
