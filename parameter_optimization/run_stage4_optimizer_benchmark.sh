#!/bin/bash
# Formal optimizer comparison, per BO_GP_and_CMA_ES_Rerun_Recommendations.md.
#
#   ./run_stage4_optimizer_benchmark.sh            # all 4 methods x 3 seeds
#   ./run_stage4_optimizer_benchmark.sh 1          # seed 1 only (first stage)
#
# 4 methods x 3 seeds x 96 S-tier evaluations = 1152 candidates = 4.6e9 primary
# events. Restartable: every campaign resumes from the ledger, so re-running
# this after an interruption costs nothing for what already finished.
#
# WHAT IS DELIBERATELY FROZEN (Priority 0 of the recommendation): the search
# bounds, objective, geometry, injection sites, fidelity and physics seed bank
# are identical across every method and seed. The optimizer is the ONLY
# experimental variable. Do not add an engineering constraint here -- that is a
# separate campaign with its own identity (Priority 2).
#
# Unlike the retired run_stage4_xl.sh this script passes NO --timeout: the
# contract's unlimited wall clock and progress watchdog apply, so a slow
# candidate is never censored for being slow (finding N6).
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LOG=logs_stage4
mkdir -p "$LOG"
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/benchmark.log"; }

TRIALS=${TRIALS:-96}
PARALLEL=${PARALLEL:-4}
# 96 worker slots. Measured 2026-09-04: a Geant4 worker draws ~99% of one core
# and 0.06 GB RSS, so 96 workers is ~96 cores against 96 physical (192 SMT
# threads), with ~17 cores in use by other users. Memory is nowhere near a
# constraint -- `per_sample_mem_gb: 4` is a kill threshold, not an admission
# gate, and actual usage is ~65x below it.
#
# With PARALLEL=4 that is 24 workers per candidate. A trial has 128 sub-runs, so
# it runs 6 waves of which the last uses 8 of 24 slots: 89% worker utilisation.
# PARALLEL=6 (16 each) would divide 128 exactly and reach 100%, worth ~13%
# throughput, but 4 is the value pinned in
# BO_GP_and_CMA_ES_Rerun_Recommendations.md and the degree of asynchrony changes
# how much the GP must fantasise, so it is left alone.
WORKERS=${WORKERS:-96}
# A drift control every 24 completions -> 4 per campaign, 48 over the benchmark.
# They are excluded from observations and from the event-efficiency curve, so
# they cannot affect the comparison; they exist because P5 has never recorded a
# single control and this is the first campaign that can produce them.
BASELINE_EVERY=${BASELINE_EVERY:-24}
SEEDS=${1:-"1 2 3"}

BO_PARAMS='{"n_init":32,"refit_every":5,"n_candidates":4096,"n_polish":4,"xi":0.0}'
CMA_PARAMS='{"popsize":12,"sigma0":0.3,"reeval_every":6,"sigma_min":0.03,"stagnation":8,"max_popsize":64,"synchronous":true}'

say "benchmark start: seeds [$SEEDS], $TRIALS trials/campaign, ${PARALLEL}x$((WORKERS/PARALLEL)) workers"
for SEED in $SEEDS; do
  for METHOD in bo_gp cmaes random sobol; do
    case $METHOD in
      bo_gp) PARAMS=$BO_PARAMS ;;
      cmaes) PARAMS=$CMA_PARAMS ;;
      *)     PARAMS='{}' ;;
    esac
    TAG="bench_${METHOD}_s${SEED}"
    if [ -f "runs/stage4_property_v1_${TAG}/campaign_${METHOD}_total_qps_per_primary.json" ] \
       && $PY - "$TAG" "$METHOD" "$TRIALS" <<'PY'
import json, sys
tag, method, trials = sys.argv[1], sys.argv[2], int(sys.argv[3])
p = f"runs/stage4_property_v1_{tag}/campaign_{method}_total_qps_per_primary.json"
sys.exit(0 if json.load(open(p)).get("n_observations", 0) >= trials else 1)
PY
    then
      say "$TAG already complete -- skipping"
      continue
    fi
    say "=== $METHOD seed $SEED ==="
    $PY -u stage4_optimize.py \
      --optimizer "$METHOD" --optimizer-params "$PARAMS" \
      --trials "$TRIALS" --parallel "$PARALLEL" --workers "$WORKERS" \
      --fidelity S --seed "$SEED" --seed-bank 0 \
      --baseline-every "$BASELINE_EVERY" --tag "$TAG" \
      >> "$LOG/${TAG}.log" 2>&1 \
      && say "$TAG done" || say "WARNING: $TAG returned $?"
  done
done
say "benchmark finished; assemble with: $PY stage4_report.py --pattern 'stage4_*bench*/campaign_*.json'"
