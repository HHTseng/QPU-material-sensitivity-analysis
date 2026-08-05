"""Spatial-quadrature diagnostics for a Stage-4 run, with noise debiasing.

The raw participation ratio n_eff = (sum x)^2 / sum x^2 over per-position QP
counts is **biased downward at low counts**, because counting noise alone makes
positions look unequal. For P positions carrying N total counts drawn with Fano
factor F (Var = F*mean),

    E[sum x^2] ~ P*(lam^2 + F*lam)   with lam = N/P
    =>  n_eff_raw ~ P*lam/(lam + F) = P*N / (N + P*F)

so even P perfectly equivalent sites do not give n_eff = P. At P=16, N=19:
F=1 (Poisson) gives 8.7, and F=2.3 (the measured compound-Poisson dispersion of
this pipeline) gives 5.5. A raw value of 4.3 is therefore NOT "4.3 effective
sites" -- most of that apparent concentration is counting noise.

This module reports the raw metric alongside its noise floor, and computes a
replica-debiased estimate that separates true between-position spread from the
Monte Carlo term using the replica structure directly:

    V_total = Var_q( mean_r x_qr )            spread of position means
    V_mc    = mean_q( Var_r(x_qr) ) / R       MC contribution to that spread
    V_true  = max(V_total - V_mc, 0)          true between-position variance
    n_eff_debiased = P / (1 + V_true / xbar^2)

The last line is exact: n_eff = P/(1 + CV^2) for any count vector, so replacing
the observed CV^2 with its noise-corrected value debiases the ratio.

This needs R >= 2 replicas and the per-position arrays that stage 2 writes into
qps/*_QPs.npz.

Usage:
    python stage4_position_diagnostics.py --results-dir results/<run_id>
    python stage4_position_diagnostics.py --results-dir results/<run_id> --first-n 16
"""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_position_matrix(results_dir, first_n=None):
    """{design_point: array (R, P) of per-position QP counts}."""
    manifest = []
    with open(os.path.join(results_dir, "qp_manifest.jsonl")) as f:
        for line in f:
            if line.strip():
                manifest.append(json.loads(line))

    by_point = defaultdict(list)
    for entry in manifest:
        npz_path = os.path.join(results_dir, "qps", entry["sample_name"] + "_QPs.npz")
        if not os.path.exists(npz_path):
            continue
        with np.load(npz_path, allow_pickle=True) as data:
            if "position_QPs" not in data:
                raise SystemExit(
                    f"{npz_path} has no per-position data. Re-run stage 2 "
                    f"(the per-position block was added 2026-07-29)."
                )
            row = np.asarray(data["position_QPs"], dtype=float)
        if first_n is not None:
            row = row[:first_n]
        by_point[entry.get("design_point", entry["sample_name"])].append(row)

    return {k: np.vstack(v) for k, v in sorted(by_point.items()) if v}


def diagnose(counts, fano=2.3):
    """Raw and debiased effective-position counts for one (R, P) matrix."""
    n_replicas, n_positions = counts.shape
    pooled = counts.sum(axis=0)                      # total per position
    total = float(pooled.sum())

    raw = float(pooled.sum() ** 2 / np.sum(pooled ** 2)) if np.any(pooled) else float("nan")
    # Noise floor: what P equivalent sites would give at this count and dispersion.
    floor_poisson = n_positions * total / (total + n_positions * 1.0) if total else float("nan")
    floor_fano = n_positions * total / (total + n_positions * fano) if total else float("nan")

    debiased = float("nan")
    if n_replicas >= 2:
        position_means = counts.mean(axis=0)
        # ddof=0 for the SPATIAL term: n_eff = P/(1 + CV^2) is an identity over
        # the finite position vector, (sum x)^2/sum x^2 = P/(1 + sigma_pop^2/xbar^2),
        # so it is the POPULATION variance of those P values. Using ddof=1 here
        # would inflate CV^2 by P/(P-1) and silently break the identity the
        # docstring claims. ddof=1 IS correct for v_mc below, which is a sample
        # estimate of the Monte-Carlo variance from R replicas.
        v_total = float(position_means.var(ddof=0))
        v_mc = float(counts.var(axis=0, ddof=1).mean() / n_replicas)
        v_true = max(v_total - v_mc, 0.0)
        xbar = float(position_means.mean())
        if xbar > 0:
            debiased = n_positions / (1.0 + v_true / xbar ** 2)

    return {
        "R": n_replicas, "P": n_positions, "total_QPs": total,
        "raw": raw, "floor_poisson": floor_poisson, "floor_fano": floor_fano,
        "debiased": debiased,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--first-n", type=int, default=None,
                        help="Use only the first N positions (nested-Sobol subset).")
    parser.add_argument("--fano", type=float, default=2.3,
                        help="Dispersion for the noise floor (default 2.3, measured).")
    args = parser.parse_args()

    matrices = load_position_matrix(args.results_dir, args.first_n)
    if not matrices:
        raise SystemExit("No per-position data found.")

    label = f"first {args.first_n}" if args.first_n else "all"
    print(f"Positions used: {label}   (noise floor at Fano={args.fano})\n")
    print(f"{'design point':28s} {'R':>2} {'P':>3} {'totalQP':>9} "
          f"{'n_eff raw':>10} {'floor F=1':>10} {'floor F=%.1f' % args.fano:>10} {'n_eff deb':>10}")
    for name, counts in matrices.items():
        d = diagnose(counts, args.fano)
        print(f"{name:28s} {d['R']:>2} {d['P']:>3} {d['total_QPs']:9.0f} "
              f"{d['raw']:10.1f} {d['floor_poisson']:10.1f} {d['floor_fano']:10.1f} "
              f"{d['debiased']:10.1f}")
    print("\nn_eff raw below the floor => real concentration; at or above => consistent "
          "with equivalent sites.\nn_eff debiased removes the counting-noise term using the "
          "replica structure; use it, not raw,\nas the convergence diagnostic.")


if __name__ == "__main__":
    main()
