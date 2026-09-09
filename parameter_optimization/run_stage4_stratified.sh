#!/bin/bash
# P2-4 of the Sep-8 plan: electrode-aware stratified quadrature, then a nested
# convergence study on the five anchor/control points.
#
# WHY. The uniform 16 -> 32 -> 64 sweep did not converge: three of four
# candidates moved far outside their error bars from 32 to 64 sites and the
# ranking flipped, because ONE site (index 36, 0.056 mm from electrode 12)
# carried 30-40% of every candidate's objective. Measured from that data, a
# uniform design needs ~2931 sites for a 5% standard error; Neyman-allocated
# strata need ~115. The defect is the design, not the sample size.
#
# Each level DOUBLES the site count at a fixed 31250 events per sub-run, so
# spatial error falls as 1/sqrt(n_sites) while per-site statistics stay
# constant. Designs are nested within every stratum, so level N+1 contains
# level N and the comparison is meaningful.
#
# Restartable: a level whose output JSON already exists is skipped.
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LOG=logs_stage4; mkdir -p "$LOG" results
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/stratified.log"; }

# Deliberately NOT named stage4_strat_*: the level outputs use that prefix and
# a cleanup glob over them once deleted this file.
POINTS=results/stage4_p4_anchor_points.json
BANK=9

[ -f "$POINTS" ] || { say "REFUSED: missing $POINTS"; exit 3; }
say "=========================================================================="
say "P2-4  electrode-aware stratified quadrature + nested convergence"
say "  points : $POINTS  (+ baseline)"
say "  design : Neyman allocation over H0..H3, every electrode covered"
say "  levels : 128 -> 256 -> 512 sites at 31250 events/sub-run"
say "=========================================================================="

# sites:events pairs keep events_per_sub_run == 31250 exactly (sites*8*31250).
for LEVEL in 128:32000000 256:64000000 512:128000000; do
  N=${LEVEL%%:*}; EV=${LEVEL##*:}
  OUT="results/stage4_strat_${N}.json"
  if [ -f "$OUT" ]; then say "$N sites already done -- skipping"; continue; fi
  say "=== $N stratified sites, $((N*8)) sub-runs, $EV events/candidate ==="
  $PY -u stage4_confirm.py --top 0 --points-file "$POINTS" \
      --fidelity L --stratified "$N" --events "$EV" --seed-bank "$BANK" \
      --workers 96 --parallel 5 --tag "strat${N}" --out "$OUT" \
      >> "$LOG/stratified_${N}.log" 2>&1 \
    && say "$N sites done" || {
         say "FAILED at $N sites -- see $LOG/stratified_${N}.log"
         say "Stopping: the levels are nested, so a later level cannot be"
         say "interpreted without the one below it."
         exit 1
       }
done

say "--- convergence verdict ---"
# PIPESTATUS, not $?: `| tee` would otherwise report tee's success and this
# script would exit 0 after a failed convergence check. P6-8 refuses on its own
# too, but a scheduler reading only this exit code would have seen "finished".
$PY -u analyze_stratified_convergence.py 2>&1 | tee -a "$LOG/stratified.log"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  say "P2-4 convergence FAILED (rc=$rc) -- not advancing to P6-8"
  exit "$rc"
fi
say "P2-4 finished: CONVERGED"
