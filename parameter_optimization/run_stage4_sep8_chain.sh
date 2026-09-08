#!/bin/bash
# The whole Sep-8 sequence, in order, with the gate between the halves.
# P6-8 refuses to start unless P2-4 passed its predeclared convergence criteria.
set -uo pipefail
cd "$(dirname "$0")"
LOG=logs_stage4; mkdir -p "$LOG"
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG/sep8_chain.log"; }
say "########## Sep-8 chain start ##########"
./run_stage4_stratified.sh
say "########## P2-4 finished; attempting P6-8 ##########"
./run_stage4_p678.sh
say "########## Sep-8 chain finished ##########"
