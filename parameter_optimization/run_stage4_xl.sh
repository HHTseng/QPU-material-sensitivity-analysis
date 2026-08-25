#!/bin/bash
# RETIRED 2026-08-25. This script must not be run. It refuses, on purpose.
#
# The original is preserved as run_stage4_xl.sh.retired and in git history; it
# is a record of how the first fidelity ladder was produced, not a runnable
# recipe. Four things in it are now actively wrong, and each would fail quietly
# rather than loudly:
#
#   1. It states "a candidate has exactly 32 sub-runs" and sizes its worker
#      shape from that. The contract now specifies 16 x 8 = 128.
#   2. It passes explicit --timeout 7200 / 43200 / 150000, reinstating the
#      absolute wall-clock censoring that finding N6 removed -- the mechanism
#      that killed best_random after 41.7 h and ~1300 core-hours. The contract
#      now sets sample_timeout_s: 0 with a progress watchdog instead, and these
#      flags would override it.
#   3. It reads results/stage4_confirm_points_L.json, results/_xl_pair.json and
#      results/stage4_points_fair.json -- all three are listed under
#      `contaminated_inputs` in stage4_invalidations.yaml because they store
#      substrate_carrier: null. Re-running from them reproduces the P0 defect.
#   4. It labels its tiers "1e7/sub-run" and "1e8/sub-run". Under the 8-replica
#      split the same totals are 2.5e6 and 2.5e7 per sub-run, so every log line
#      it writes would be off by 4x.
#
# To run a fidelity ladder now:
#
#   1. regenerate the points from the CORRECTED projection --
#        python stage4_project_material.py --point "$(target vector)" ...
#      never reuse a points file written before 2026-08-25;
#   2. call stage4_confirm.py per tier WITHOUT --timeout, so the contract's
#      unlimited wall clock and progress watchdog apply;
#   3. let the sub-run count come from the contract -- never hard-code it;
#   4. assemble with stage4_assemble_xl.py (no simulation, no ledger writer)
#      and compare tiers with stage4_compare_fidelity.py.
#
# `parameter_optimization/README.md` carries the same instructions.
set -uo pipefail
cat >&2 <<'MSG'
REFUSING TO RUN: run_stage4_xl.sh was retired on 2026-08-25.

It hard-codes 32 sub-runs (now 128), passes absolute --timeout values that
reinstate candidate-dependent censoring, reads carrier-contaminated point files,
and mislabels its tiers by 4x. Running it would produce plausible-looking,
wrong results.

Read the header of this file for the corrected procedure. The original is kept
as run_stage4_xl.sh.retired and in git history.
MSG
exit 2
