#!/bin/bash
# The whole Sep-8 sequence, in order, with the gate between the halves.
# P6-8 refuses to start unless P2-4 passed its predeclared convergence criteria.
set -uo pipefail
cd "$(dirname "$0")"
LOG=logs_stage4; mkdir -p "$LOG"
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/sep8_chain.log"; }
say "########## Sep-8 chain start ##########"
# Explicit propagation. Without it the final `say` makes the chain exit 0 even
# when convergence failed, so the job is marked successful and prints
# "finished" over a failure.
./run_stage4_stratified.sh || { rc=$?; say "P2-4 FAILED (rc=$rc); stopping"; exit "$rc"; }
say "########## P2-4 converged; starting P6-8 ##########"
./run_stage4_p678.sh || { rc=$?; say "P6-8 FAILED (rc=$rc)"; exit "$rc"; }
say "########## Sep-8 chain finished ##########"
