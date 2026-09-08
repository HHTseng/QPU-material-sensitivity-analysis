#!/bin/bash
# P7: equal-cost optimizer benchmark on the CONVERGED stratified objective.
#
# Differences from run_stage4_optimizer_benchmark.sh, all deliberate:
#   * objective  device_weighted_junction_qps_per_energy on the frozen
#     stratified design, not the equal-site mean over a uniform draw
#   * a direct modeled-gap floor is active, so every reported point is feasible
#   * BO is warm-started from re-evaluated physical vectors -- never from old
#     objective values, which measured a different quantity
#
# Usage:  ./run_stage4_optimizer_benchmark_stratified.sh <NSITES> <WARM.json> [seeds]
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LOG=logs_stage4; mkdir -p "$LOG"
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/benchmark_strat.log"; }

NSITES=${1:?usage: $0 <NSITES> <WARM.json> [seeds]}
WARM=${2:?usage: $0 <NSITES> <WARM.json> [seeds]}
SEEDS=${3:-"1 2 3"}
TRIALS=${TRIALS:-96}
PARALLEL=${PARALLEL:-6}
WORKERS=${WORKERS:-96}
BASELINE_EVERY=${BASELINE_EVERY:-24}

BO_PARAMS='{"n_init":32,"refit_every":5,"n_candidates":4096,"n_polish":4,"xi":0.0}'
CMA_PARAMS='{"popsize":12,"sigma0":0.3,"reeval_every":6,"sigma_min":0.03,"stagnation":8,"max_popsize":64,"synchronous":true}'
OBJ=device_weighted_junction_qps_per_energy

say "stratified benchmark: $NSITES sites, seeds [$SEEDS], $TRIALS trials/campaign"
for SEED in $SEEDS; do
  for METHOD in bo_gp cmaes random sobol; do
    case $METHOD in
      bo_gp) PARAMS=$BO_PARAMS ;;
      cmaes) PARAMS=$CMA_PARAMS ;;
      *)     PARAMS='{}' ;;
    esac
    TAG="sbench_${METHOD}_s${SEED}"
    MAN="runs/stage4_property_v1_${TAG}/campaign_${METHOD}_${OBJ}.json"
    if [ -f "$MAN" ] && $PY - "$MAN" "$TRIALS" <<'PY'
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("n_observations", 0) >= int(sys.argv[2]) else 1)
PY
    then say "$TAG already complete -- skipping"; continue; fi
    say "=== $METHOD seed $SEED ==="
    $PY -u stage4_optimize.py \
      --optimizer "$METHOD" --optimizer-params "$PARAMS" \
      --objective "$OBJ" --stratified "$NSITES" --warm-start "$WARM" \
      --trials "$TRIALS" --parallel "$PARALLEL" --workers "$WORKERS" \
      --fidelity S --seed "$SEED" --seed-bank 0 \
      --baseline-every "$BASELINE_EVERY" --tag "$TAG" \
      >> "$LOG/${TAG}.log" 2>&1 \
      && say "$TAG done" || say "WARNING: $TAG returned $?"
  done
done
say "assembling report"
$PY stage4_report.py --pattern 'stage4_*sbench*/campaign_*.json' \
    --out results/stage4_p7_report.json >> "$LOG/benchmark_strat.log" 2>&1 \
  && say "report -> results/stage4_p7_report.json" || say "WARNING: report failed"
say "stratified benchmark finished"
