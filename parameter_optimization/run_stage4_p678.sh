#!/bin/bash
# P6-8 of the Sep-8 plan. REFUSES to start until P2-4 has produced a CONVERGED
# stratified design, because every step here consumes that design as a frozen
# contract and a non-converged objective would make all of it meaningless.
#
#   P5  freeze the production contract (recorded, not simulated)
#   P6  re-evaluate warm starts under the NEW objective -- old J16 values are
#       observations of a DIFFERENT quantity and are never imported
#   P7  equal-cost optimizer benchmark, 4 methods x 3 seeds x 96 evaluations
#   P8  held-out confirmation of the feasible finalists
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LOG=logs_stage4; mkdir -p "$LOG" results
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/p678.log"; }

# ---- gate ---------------------------------------------------------------
if ! $PY analyze_stratified_convergence.py > "$LOG/p678_gate.txt" 2>&1; then
  say "REFUSED: the stratified quadrature has not passed its convergence gate."
  sed 's/^/    /' "$LOG/p678_gate.txt" | tee -a "$LOG/p678.log"
  say "Fix the quadrature first; optimizing a non-converged objective wastes"
  say "every CPU-hour spent after this point."
  exit 2
fi
say "gate passed: stratified quadrature is converged"

# Only digit-suffixed level files; stage4_strat_points.json is a POINT LIST,
# not a level, and globbing it in once made the gate report "points sites".
FROZEN=$(ls -1 results/stage4_strat_[0-9]*.json 2>/dev/null \
         | sed 's/.*_\([0-9]*\)\.json/\1 &/' | sort -n | tail -1 | cut -d' ' -f2)
NSITES=$(basename "$FROZEN" .json | sed 's/.*_//')
if ! [[ "$NSITES" =~ ^[0-9]+$ ]]; then
  say "REFUSED: could not identify a frozen level file (got '$NSITES')"; exit 4
fi
say "frozen production design: $NSITES sites (from $FROZEN)"
EVENTS=$((NSITES * 8 * 31250))

# ---- P6  warm-start re-evaluation ---------------------------------------
OUT6=results/stage4_p6_warmstarts.json
if [ -f "$OUT6" ]; then
  say "P6 already done"
else
  say "=== P6: re-evaluating warm starts under the NEW objective ==="
  $PY -u stage4_build_warmstarts.py --n 24 --out results/stage4_p6_points.json \
      >> "$LOG/p6.log" 2>&1 || { say "P6 point selection FAILED"; exit 3; }
  $PY -u stage4_confirm.py --top 0 --points-file results/stage4_p6_points.json \
      --fidelity L --stratified "$NSITES" --events "$EVENTS" --seed-bank 9 \
      --workers 96 --parallel 6 --tag "p6warm" --out "$OUT6" \
      >> "$LOG/p6.log" 2>&1 && say "P6 done" || say "WARNING: P6 returned $?"
fi

# ---- P7  equal-cost optimizer benchmark ---------------------------------
say "=== P7: optimizer benchmark, 4 methods x 3 seeds, stratified objective ==="
say "    this is the long pole: ~4 days at the measured rate"
./run_stage4_optimizer_benchmark_stratified.sh "$NSITES" results/stage4_p6_points.json "1 2 3" \
    >> "$LOG/p7.log" 2>&1 && say "P7 done" || say "WARNING: P7 returned $?"

# ---- P8  held-out confirmation ------------------------------------------
OUT8=results/stage4_p8_confirmation.json
if [ -f "$OUT8" ]; then
  say "P8 already done"
else
  say "=== P8: held-out confirmation of feasible finalists ==="
  $PY -u stage4_confirm.py --report results/stage4_p7_report.json --top 4 \
      --fidelity L --stratified "$NSITES" --events "$((EVENTS * 4))" \
      --seed-bank 11 --workers 96 --parallel 4 --tag "p8" --out "$OUT8" \
      >> "$LOG/p8.log" 2>&1 && say "P8 done" || say "WARNING: P8 returned $?"
fi
say "P6-8 finished"
exit 0
