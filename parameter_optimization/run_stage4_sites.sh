#!/bin/bash
# Spatial-convergence test (item 2 of the 2026-09-08 plan): does the objective
# survive a finer injection-site quadrature?
#
# Motivation, measured 2026-09-07 from existing block data: ONE of the 16 Sobol
# sites (index 11, 0.119 mm from an electrode, inside the 0.200 mm island)
# carries 49.3% of the baseline's QPs. Dropping it moves the headline reductions
# by 16-27 points. The integrand is steeply peaked at electrodes, so a 16-point
# draw is dominated by whichever point lands nearest one -- a quadrature
# artefact, not physics.
#
# A different site count is a different SCENARIO, so these are different
# simulation identities and cannot be paired across counts. What is comparable
# is the absolute yield and the ranking.
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LOG=logs_stage4; mkdir -p "$LOG"
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/sites.log"; }
say "spatial convergence: 16 -> 32 -> 64 sites, M tier, held-out bank 9"
for N in 16 32 64; do
  OUT="results/stage4_sites_${N}.json"
  if [ -f "$OUT" ]; then say "$N sites already done"; continue; fi
  say "=== $N sites ($((N*8)) sub-runs per candidate) ==="
  $PY -u stage4_confirm.py --top 0 --points-file results/stage4_sites_points.json \
    --fidelity M --positions "$N" --seed-bank 9 --workers 96 --parallel 4 \
    --tag "sites${N}" --out "$OUT" >> "$LOG/sites_${N}.log" 2>&1 \
    && say "$N sites done" || say "WARNING: $N sites returned $?"
done
say "spatial convergence finished"
