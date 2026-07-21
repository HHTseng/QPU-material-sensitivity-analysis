#!/bin/bash
set -euo pipefail
cd /home/htseng/Sensitivity_Analysis_HT

export SENSITIVITY_MACRO_TEMPLATE=/home/htseng/Sensitivity_Analysis_HT/sensitivity_template_beamOn1e5.mac
export SENSITIVITY_MAX_WORKERS=4
PY=/home/htseng/.conda/envs/G4CMP/bin/python

STAGE1_LOG=$(mktemp)
"$PY" -u stage1_run_simulations.py 2>&1 | tee "$STAGE1_LOG"

RESULTS_DIR=$(grep -m1 "Writing sensitivity results to:" "$STAGE1_LOG" | sed 's/.*Writing sensitivity results to: *//')
rm -f "$STAGE1_LOG"

if [ -z "$RESULTS_DIR" ]; then
  echo "ERROR: could not determine results dir from stage1 output" >&2
  exit 1
fi

echo "=== Stage 1 complete. Results dir: $RESULTS_DIR ==="
echo "=== Starting stage 2 ==="
"$PY" -u stage2_compute_QPs.py "$RESULTS_DIR"
echo "=== Stage 2 complete ==="
