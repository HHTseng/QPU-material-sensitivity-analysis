#!/usr/bin/env bash
# =============================================================================
# Stage 3 beamOn scaling study -- does the material ranking survive more events?
#
# Compares the material ranking at three per-sub-run event counts:
#
#     125,000   (done: stage3_trials.sqlite, campaign stage3_factorial_v1)
#     1e7       80x   -- this script, tier "e7"
#     1e8       800x  -- this script, tier "e8"
#
# "Per sub-run" means per injection site per replica. With 16 sites x 2 replicas
# a candidate is 32 sub-runs, so events PER CANDIDATE are 32x the tier value.
#
# Designed to be run inside tmux and left alone:
#
#     tmux new -s beamon
#     cd /home/htseng/Sensitivity_Analysis_HT/parameter_optimization
#     ./run_beamon_scaling.sh e7
#     # detach with Ctrl-b d ; reattach with: tmux attach -t beamon
#
# RESUMABLE. Every completed sub-run is recorded in the tier's ledger and reused
# on a re-run, so an interrupted campaign is restarted with the same command.
# Partial trials are never scored -- they are re-run.
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-$HOME/.conda/envs/G4CMP/bin/python}"
LOGDIR="${LOGDIR:-$HERE/logs_beamon}"

# ---- machine budget ---------------------------------------------------------
# Full machine. 6 shards x 32 workers = 192, one worker per core. Each shard
# runs its own memory guard and only sees its own sub-runs, so the 200 GB
# machine budget is divided between them rather than given to each.
TOTAL_CORES="${TOTAL_CORES:-192}"
N_SHARDS="${N_SHARDS:-6}"
WORKERS_PER_SHARD=$(( TOTAL_CORES / N_SHARDS ))
TOTAL_MEM_GB="${TOTAL_MEM_GB:-200}"
MEM_PER_SHARD=$(awk "BEGIN{printf \"%.1f\", $TOTAL_MEM_GB / $N_SHARDS}")
PER_SAMPLE_MEM_GB="${PER_SAMPLE_MEM_GB:-4}"

POSITIONS="${POSITIONS:-16}"
REPLICAS="${REPLICAS:-2}"
SUBRUNS=$(( POSITIONS * REPLICAS ))
BASELINE="${BASELINE:-Si/Nb/Cu}"

usage() {
  cat <<EOF
Usage: $0 <tier> [--subset] [--dry-run]

  tier        e7        1e7 events per sub-run  (3.2e8 per candidate)
              e8        1e8 events per sub-run  (3.2e9 per candidate)
              compare   compare the finished tiers against the 125k baseline

  --subset    run only the candidates whose ranking is unresolved at 125k,
              instead of all 18. Cuts an e8 run from ~30 h to ~10 h.
  --dry-run   print the plan and the budget, run nothing.

Environment overrides: PYTHON N_SHARDS TOTAL_CORES TOTAL_MEM_GB POSITIONS
                       REPLICAS BASELINE LOGDIR
EOF
}

[[ $# -lt 1 ]] && { usage; exit 2; }
TIER="$1"; shift
SUBSET=0; DRYRUN=0
for arg in "$@"; do
  case "$arg" in
    --subset)  SUBSET=1 ;;
    --dry-run) DRYRUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done

# ---- tier parameters --------------------------------------------------------
# TIMEOUT is the per-sub-run watchdog. It MUST exceed the expected sub-run time
# or every sub-run is killed and no trial is ever scored. Measured at 10 meV:
# ~1.4e-4 s per event, so 1e7 -> ~23 min and 1e8 -> ~3.9 h for the fastest
# candidate; the slowest is ~3.5x that. The values below carry ~3x headroom.
case "$TIER" in
  e7) PER_SUB=10000000;  TIMEOUT=21600  ; EST_H=3   ;;
  e8) PER_SUB=100000000; TIMEOUT=172800 ; EST_H=30  ;;
  compare) PER_SUB=0 ;;
  *) echo "unknown tier: $TIER" >&2; usage; exit 2 ;;
esac

# ---- candidate list ---------------------------------------------------------
ALL=(Si/Nb/Cu Si/Nb/Au Si/Ta/Cu Si/Ta/Au Si/Ti/Cu Si/Ti/Au
     Ge/Nb/Cu Ge/Nb/Au Ge/Ta/Cu Ge/Ta/Au Ge/Ti/Cu Ge/Ti/Au
     GaAs/Nb/Cu GaAs/Nb/Au GaAs/Ta/Cu GaAs/Ta/Au GaAs/Ti/Cu GaAs/Ti/Au)
# Pairs separated by <2% at 125k, i.e. the ones counting statistics cannot yet
# order. These are the only candidates for which more events changes a decision.
SUBSET_LIST=(GaAs/Nb/Cu Ge/Nb/Cu Ge/Ti/Cu Ge/Ta/Cu Si/Nb/Cu Si/Ti/Cu)

if [[ "$TIER" == "compare" ]]; then
  exec "$PYTHON" stage3_compare_fidelity.py \
      --baseline-ledger "$HERE/stage3_trials.sqlite" \
      --ledger e7="$HERE/stage3_trials_e7.sqlite" \
      --ledger e8="$HERE/stage3_trials_e8.sqlite" \
      --baseline-candidate "$BASELINE"
fi

if (( SUBSET )); then CANDS=("${SUBSET_LIST[@]}"); else CANDS=("${ALL[@]}"); fi
N=${#CANDS[@]}
LEDGER="$HERE/stage3_trials_${TIER}.sqlite"
EVENTS=$(( PER_SUB * SUBRUNS ))
[[ $SUBSET -eq 1 ]] && EST_H=$(awk "BEGIN{printf \"%.0f\", $EST_H * $N / 18}")

# ---- plan -------------------------------------------------------------------
cat <<EOF
=============================================================================
Stage 3 beamOn scaling -- tier ${TIER}
=============================================================================
  events per sub-run     : $(printf "%'d" $PER_SUB)   (per site, per replica)
  sites x replicas       : ${POSITIONS} x ${REPLICAS} = ${SUBRUNS} sub-runs
  events per candidate   : $(printf "%'d" $EVENTS)
  candidates             : ${N}$( ((SUBSET)) && echo "  (subset: unresolved pairs only)" )
  total events           : $(awk "BEGIN{printf \"%.2e\", $EVENTS * $N}")

  shards x workers       : ${N_SHARDS} x ${WORKERS_PER_SHARD} = $(( N_SHARDS * WORKERS_PER_SHARD )) cores
  memory budget          : ${TOTAL_MEM_GB} GB total -> ${MEM_PER_SHARD} GB per shard,
                           ${PER_SAMPLE_MEM_GB} GB per sub-run
  per-sub-run timeout    : $(awk "BEGIN{printf \"%.1f\", $TIMEOUT/3600}") h
  ESTIMATED WALL TIME    : ~${EST_H} h

  ledger                 : ${LEDGER}
  logs                   : ${LOGDIR}/${TIER}/
=============================================================================
EOF

if (( DRYRUN )); then echo "dry run -- nothing started."; exit 0; fi

# Refuse to start if the machine is already busy: a competing job silently
# doubles the wall time and can trip the memory guard.
LOAD=$(awk '{print int($1)}' /proc/loadavg)
if (( LOAD > TOTAL_CORES / 4 )); then
  echo "WARNING: load average is ${LOAD} on ${TOTAL_CORES} cores -- something else is running."
  read -r -p "Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
fi

mkdir -p "$LOGDIR/$TIER"
START=$(date +%s)
echo "started $(date -Is)"

# ---- launch shards ----------------------------------------------------------
# Candidates are dealt round-robin so each shard gets a mix of fast (Cu) and
# slow (Au) candidates; contiguous slicing would leave one shard with all the
# slow ones and the rest idle.
declare -a SHARD_LIST
for ((i = 0; i < N_SHARDS; i++)); do SHARD_LIST[$i]=""; done
for ((j = 0; j < N; j++)); do
  s=$(( j % N_SHARDS ))
  SHARD_LIST[$s]="${SHARD_LIST[$s]:+${SHARD_LIST[$s]},}${CANDS[$j]}"
done

PIDS=()
for ((i = 0; i < N_SHARDS; i++)); do
  [[ -z "${SHARD_LIST[$i]}" ]] && continue
  LOG="$LOGDIR/$TIER/shard${i}.log"
  echo "  shard ${i}: ${SHARD_LIST[$i]}"
  nohup "$PYTHON" -u stage3_run_factorial.py \
      --events "$EVENTS" \
      --positions "$POSITIONS" --replicas "$REPLICAS" \
      --workers "$WORKERS_PER_SHARD" \
      --timeout "$TIMEOUT" \
      --total-mem-gb "$MEM_PER_SHARD" \
      --per-sample-mem-gb "$PER_SAMPLE_MEM_GB" \
      --tag "$TIER" \
      --only "${SHARD_LIST[$i]}" \
      --ledger "$LEDGER" \
      --baseline "$BASELINE" \
      > "$LOG" 2>&1 &
  PIDS+=($!)
done

echo
echo "${#PIDS[@]} shards running. Detach with Ctrl-b d; follow with:"
echo "    tail -f $LOGDIR/$TIER/shard*.log"
echo "    watch -n 60 '$PYTHON $HERE/stage3_report.py --ledger $LEDGER | tail -25'"
echo

# Kill the whole process group on Ctrl-C, including orphaned Geant4 children --
# killing only the drivers leaves `Main` running at 100% CPU.
cleanup() {
  echo; echo "interrupted -- stopping shards and their Geant4 children"
  for p in "${PIDS[@]}"; do kill -TERM "$p" 2>/dev/null || true; done
  sleep 3
  pgrep -x Main | xargs -r kill -9 2>/dev/null || true
  echo "stopped. Re-run the same command to resume from the ledger."
  exit 130
}
trap cleanup INT TERM

FAIL=0
for p in "${PIDS[@]}"; do wait "$p" || FAIL=1; done

ELAPSED=$(( $(date +%s) - START ))
echo
echo "finished $(date -Is)  wall $(printf '%dh%02dm' $((ELAPSED/3600)) $(((ELAPSED%3600)/60)))"
(( FAIL )) && echo "WARNING: at least one shard exited non-zero -- check the logs."

echo
echo "=== tier ${TIER} ranking ==="
"$PYTHON" stage3_report.py --ledger "$LEDGER" \
    --csv "$HERE/results/beamon_${TIER}.csv" --baseline "$BASELINE" | tail -30

echo
echo "=== compare against the 125k baseline ==="
"$PYTHON" stage3_compare_fidelity.py \
    --baseline-ledger "$HERE/stage3_trials.sqlite" \
    --ledger "${TIER}=${LEDGER}" \
    --baseline-candidate "$BASELINE" || true

exit $FAIL
