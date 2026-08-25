#!/bin/bash
# Stage 4 fidelity ladder, 64 workers.
#
# Completes the ladder at 1e7 and 1e8 events per sub-run on the SAME candidate
# set at every tier, so the optimizer comparison is fair: the baseline plus the
# best point found by each of bo_gp, cmaes and random, plus the SiC elasticity
# variant (the fabrication-relevant answer).
#
# Worker shape, and why: a candidate has exactly 32 sub-runs, so more than 32
# workers on ONE candidate is wasted. 64 workers therefore means TWO candidates
# at 32 workers each -- each candidate completes in a single wave, with no
# straggler wave, and two run at once.
#
# The ledger caches completed trials, so this script is restartable and re-running
# costs nothing for what already finished. Every step logs to logs_stage4/.
set -u
cd /home/htseng/Sensitivity_Analysis_HT/parameter_optimization
LOG=logs_stage4
mkdir -p "$LOG"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG/xl_chain.log"; }

say "chain started (64 workers)"

# --- 1. the 1e7 tier: optimum, runner-up, SiC variant, baseline ------------
if [ ! -f results/stage4_confirmation_L.json ]; then
  say "1e7/sub-run tier: 4 candidates, 2 x 32 workers"
  python3 -u stage4_confirm.py --top 0 --points-file results/stage4_confirm_points_L.json \
    --fidelity L --seed-bank 9 --workers 64 --parallel 2 --timeout 43200 \
    --tag confirmL --out results/stage4_confirmation_L.json \
    >> "$LOG/confirm_L.log" 2>&1 && say "1e7 tier done" || say "WARNING: 1e7 tier returned $?"
else
  say "1e7 tier already complete"
fi

# --- 2. fairness backfill: random's winner at 1e6 and 1e7 ------------------
say "backfill: best_random at 1e6/sub-run"
python3 -u stage4_confirm.py --top 0 --points-file results/stage4_points_random.json \
  --fidelity M --seed-bank 9 --workers 64 --parallel 2 --timeout 7200 \
  --tag confirmM --out results/stage4_confirmation_M_random.json \
  >> "$LOG/confirm_M_random.log" 2>&1 || say "WARNING: 1e6 backfill returned $?"

say "backfill: best_random at 1e7/sub-run"
python3 -u stage4_confirm.py --top 0 --points-file results/stage4_points_random.json \
  --fidelity L --seed-bank 9 --workers 64 --parallel 2 --timeout 43200 \
  --tag confirmL --out results/stage4_confirmation_L_random.json \
  >> "$LOG/confirm_L_random.log" 2>&1 || say "WARNING: 1e7 backfill returned $?"

# --- 3. the 1e8 tier, two candidates at a time -----------------------------
# 3.2e9 events per candidate. The baseline rides along in the first pair and is
# a free cache hit thereafter.
for PAIR in "best_bo_gp best_cmaes" "best_random elasticity_of_SiC"; do
  TAGNAME=$(echo "$PAIR" | tr ' ' '+')
  say "1e8 tier: $PAIR"
  python3 - $PAIR << 'PY'
import json, sys
allpts = json.load(open('results/stage4_points_fair.json'))
json.dump({n: allpts[n] for n in sys.argv[1:]}, open('results/_xl_pair.json', 'w'), indent=1)
PY
  python3 -u stage4_confirm.py --top 0 --points-file results/_xl_pair.json \
    --fidelity L --events 3200000000 --seed-bank 9 --workers 64 --parallel 2 \
    --timeout 150000 --tag confirmXL \
    --out "results/stage4_confirmation_XL_${TAGNAME}.json" \
    >> "$LOG/confirm_XL.log" 2>&1 \
    && say "1e8 tier: $PAIR done" || say "WARNING: 1e8 $PAIR returned $?"
done

# --- 4. combined 1e8 table (all cache hits, seconds) -----------------------
say "assembling the combined 1e8 table"
python3 -u stage4_confirm.py --top 0 --points-file results/stage4_points_fair.json \
  --fidelity L --events 3200000000 --seed-bank 9 --workers 64 --parallel 2 \
  --timeout 150000 --tag confirmXL --out results/stage4_confirmation_XL.json \
  >> "$LOG/confirm_XL_combined.log" 2>&1 || say "WARNING: combined XL returned $?"

# --- 5. the four-tier ladder ----------------------------------------------
say "fidelity comparison across all four tiers"
python3 -u stage4_compare_fidelity.py \
  results/stage4_confirmation_S.json results/stage4_confirmation_M.json \
  results/stage4_confirmation_L.json results/stage4_confirmation_XL.json \
  --out results/stage4_fidelity_comparison.json \
  > "$LOG/fidelity_ladder.log" 2>&1 || say "WARNING: comparison returned $?"

say "chain finished; tmux session will close"
