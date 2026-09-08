#!/bin/bash
# Site sweep for the Nb-gap-compatible ANCHOR (5c1e62ff8466).
#
# Run this ONLY after run_stage4_sites.sh has finished all three stages. The
# anchor was not in that sweep's point file, and editing an active chain's
# point file mid-run would make its 32- and 64-site stages evaluate a different
# candidate set from its 16-site stage -- destroying the comparison the sweep
# exists to make.
#
# Same protocol as the main sweep: M tier, held-out bank 9, 16/32/64 sites.
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LOG=logs_stage4; mkdir -p "$LOG"
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/sites_anchor.log"; }
if [ ! -f results/stage4_sites_64.json ]; then
  say "REFUSING: the main sweep has not finished all three stages."
  say "  run_stage4_sites.sh must complete first, or the two sweeps are not comparable."
  exit 2
fi
say "anchor sweep: 16 -> 32 -> 64 sites, M tier, held-out bank 9"
for N in 16 32 64; do
  OUT="results/stage4_sites_anchor_${N}.json"
  [ -f "$OUT" ] && { say "$N sites already done"; continue; }
  say "=== anchor at $N sites ==="
  $PY -u stage4_confirm.py --top 0 --points-file results/stage4_sites_anchor_points.json \
    --fidelity M --positions "$N" --seed-bank 9 --workers 96 --parallel 4 \
    --tag "sitesA${N}" --out "$OUT" >> "$LOG/sites_anchor_${N}.log" 2>&1 \
    && say "anchor $N sites done" || say "WARNING: anchor $N returned $?"
done
say "anchor sweep finished"
