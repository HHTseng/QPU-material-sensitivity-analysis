"""M2 analysis: thickness x coverage QP response surface for the Cu backside stack.

Implements the seven reporting rules pre-registered before the campaign ran
(Stage-4 plan sec. 6h). They exist because at P=16, R=4 and 126,000 events per
position-replica the trends are resolvable but many ADJACENT cells are not, and
a fitted surface can easily manufacture local orderings the data do not support.

  1. every raw grid mean and uncertainty is printed, even though a fit is shown
  2. the fit and all contrasts use replica-block resampling -- the four replica
     indices are resampled JOINTLY across all 28 candidates, preserving the
     common-seed pairing that makes candidate-vs-candidate contrasts paired
  3. fit residuals and leave-one-out CV error are reported
  4. two neighbouring cells are called ordered only if their DIRECT paired
     contrast supports it
  5. unexpected non-monotonicity is reported as unresolved, not as a finding
  6. position_effective_n is a diagnostic, not a physical observable, at R=4
  7. any minimum is "minimum modeled QP yield within the sampled box", never a
     device optimum -- the model contains no microwave-loss or fabrication term
     (plan sec. 3.3a)

Objective: QP_yield_per_event (a rate, so comparable across event counts).

Usage:
    python stage4_m2_analysis.py --results-dir results/<run_id> \
        --labels m2_grid_design.labels.json
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

BOOT = 20000
BOOT_SEED = 20260729


def load(results_dir, labels_path):
    """Per-replica objective matrix plus the geometry of each design point."""
    labels = json.load(open(labels_path))
    by_row = {r["row"]: r for r in labels["rows"]}

    summary = pd.read_csv(os.path.join(results_dir, "qp_summary.csv"))
    summary["row"] = summary["design_point"].str.rsplit("_", n=1).str[-1].astype(int)

    rows, matrix = [], []
    for row in sorted(summary["row"].unique()):
        block = summary[summary["row"] == row].sort_values("replica")
        meta = by_row[row]
        rows.append({
            "row": row,
            "label": meta["label"],
            "t_um": meta["setBotThickness_um"],
            "coverage": round(meta["coverage"], 4),
            "n_eff": float(block["position_effective_n"].mean()),
            "total_QPs": float(block["total_QPs"].mean()),
        })
        matrix.append(block["QP_yield_per_event"].to_numpy(dtype=float))

    return pd.DataFrame(rows), np.array(matrix), labels


def block_bootstrap(matrix, rng, n_boot=BOOT):
    """Replica-block bootstrap indices, shared across every candidate.

    Resampling the replica AXIS jointly (not per candidate) is what preserves
    the common-random-number structure: candidate A's replica 2 and candidate
    B's replica 2 were run on the same seed bank at matched (position, replica),
    so they must travel together or the pairing is destroyed and every contrast
    inherits variance the design deliberately cancelled.
    """
    n_replicas = matrix.shape[1]
    return rng.integers(0, n_replicas, size=(n_boot, n_replicas))


def paired_contrast(matrix, i, j, boot_idx):
    """Relative change from cell i to cell j, with a paired bootstrap interval.

    Returns (relative_difference, lo, hi, resolved) where `resolved` means the
    95% interval excludes zero -- rule 4's criterion for calling two cells
    ordered.
    """
    a, b = matrix[i], matrix[j]
    point = b.mean() / a.mean() - 1.0
    draws = b[boot_idx].mean(axis=1) / a[boot_idx].mean(axis=1) - 1.0
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, lo, hi, bool(lo > 0 or hi < 0)


def fit_surface(grid, matrix):
    """log(yield) ~ 1 + log(t) + f + f^2, with leave-one-out CV.

    Deliberately low-order and interpretable rather than flexible: the point is
    to summarise the trend and expose how much of the surface it fails to
    capture (rule 3), not to interpolate 28 noisy cells exactly.
    """
    y = np.log(matrix.mean(axis=1))
    t, f = grid["t_um"].to_numpy(float), grid["coverage"].to_numpy(float)
    design = np.column_stack([np.ones_like(t), np.log(t), f, f ** 2])

    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coefficients
    residuals = y - fitted

    loo = np.empty_like(y)
    for k in range(len(y)):
        keep = np.arange(len(y)) != k
        beta, *_ = np.linalg.lstsq(design[keep], y[keep], rcond=None)
        loo[k] = design[k] @ beta

    return {
        "coefficients": coefficients,
        "residuals": residuals,
        "rms_residual_pct": 100 * float(np.sqrt(np.mean(residuals ** 2))),
        "max_residual_pct": 100 * float(np.max(np.abs(residuals))),
        "loo_rms_pct": 100 * float(np.sqrt(np.mean((y - loo) ** 2))),
    }


def axis_monotonicity(grid, matrix, boot_idx, axis, other):
    """Adjacent-cell paired contrasts along one axis, at each level of the other.

    Reports every adjacent step and whether it is resolved, so a non-monotone
    step is visible as an unresolved wobble rather than being smoothed away
    (rules 4 and 5).
    """
    print(f"\n--- monotonicity along {axis} (expect yield to DECREASE) ---")
    print(f"{other:>10}  {'step':>26} {'change':>9} {'95% interval':>18}  {'resolved':>8}")
    findings = []
    for level in sorted(grid[other].unique()):
        block = grid[grid[other] == level].sort_values(axis)
        idx = block.index.to_numpy()
        vals = block[axis].to_numpy()
        for k in range(len(idx) - 1):
            d, lo, hi, res = paired_contrast(matrix, idx[k], idx[k + 1], boot_idx)
            step = f"{vals[k]:g} -> {vals[k+1]:g}"
            flag = "" if not res else ("  DECREASE" if d < 0 else "  INCREASE")
            print(f"{level:>10g}  {step:>26} {100*d:+8.1f}% "
                  f"[{100*lo:+6.1f}%,{100*hi:+6.1f}%]  {str(res):>8}{flag}")
            findings.append((level, vals[k], vals[k + 1], d, res))
    resolved_up = [x for x in findings if x[4] and x[3] > 0]
    print(f"  adjacent steps: {len(findings)} total, "
          f"{sum(1 for x in findings if x[4])} resolved, "
          f"{len(resolved_up)} resolved INCREASES (non-monotone)")
    return findings


def plateau(grid, matrix, boot_idx):
    """First thickness whose step to the next is unresolved, per coverage.

    "Plateau" is defined operationally as the point beyond which the data cannot
    distinguish further improvement -- which is the actionable quantity, and is
    weaker than a claim that the response has genuinely flattened.
    """
    print("\n--- thickness plateau per coverage (first unresolved step) ---")
    print(f"{'coverage':>9}  {'plateau onset':>14}  note")
    for cov in sorted(grid["coverage"].unique()):
        block = grid[grid["coverage"] == cov].sort_values("t_um")
        idx, vals = block.index.to_numpy(), block["t_um"].to_numpy()
        onset, note = None, "all steps resolved -- no plateau within the box"
        for k in range(len(idx) - 1):
            _, _, _, res = paired_contrast(matrix, idx[k], idx[k + 1], boot_idx)
            if not res:
                onset = vals[k]
                note = f"{vals[k]:g} -> {vals[k+1]:g} um unresolved"
                break
        print(f"{cov:>9.2f}  {(f'{onset:g} um' if onset else '-'):>14}  {note}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()

    grid, matrix, labels = load(str(args.results_dir), str(args.labels))
    rng = np.random.default_rng(BOOT_SEED)
    boot_idx = block_bootstrap(matrix, rng)
    n_replicas = matrix.shape[1]

    print(f"M2 response surface: {len(grid)} design points x {n_replicas} replicas")
    print(f"design: {labels['design_file']}  sha256 {labels['design_file_sha256'][:16]}")
    print(f"objective: QP_yield_per_event   bootstrap: {BOOT} replica-block draws\n")

    # RULE 1: every raw cell, always.
    print("--- raw grid (rule 1: shown regardless of any fit) ---")
    print(f"{'row':>3} {'t_um':>5} {'cov':>5} {'yield/evt':>11} {'SEM':>10} {'rel':>6} "
          f"{'totQP':>7} {'n_eff':>6}")
    for k, r in grid.iterrows():
        v = matrix[k]
        mean, sem = v.mean(), v.std(ddof=1) / np.sqrt(len(v))
        print(f"{r['row']:>3} {r['t_um']:>5g} {r['coverage']:>5.2f} {mean:>11.4e} "
              f"{sem:>10.2e} {100*sem/mean:>5.1f}% {r['total_QPs']:>7.0f} {r['n_eff']:>6.1f}")

    axis_monotonicity(grid, matrix, boot_idx, "t_um", "coverage")
    axis_monotonicity(grid, matrix, boot_idx, "coverage", "t_um")
    plateau(grid, matrix, boot_idx)

    # RULE 3: fit quality, so the reader can see what the trend misses.
    fit = fit_surface(grid, matrix)
    print("\n--- fitted trend log(yield) ~ 1 + log(t) + f + f^2 (rule 3) ---")
    names = ["intercept", "log(t)", "f", "f^2"]
    print("  " + "  ".join(f"{n}={c:+.4f}" for n, c in zip(names, fit["coefficients"])))
    print(f"  in-sample RMS residual {fit['rms_residual_pct']:.1f}%   "
          f"max |residual| {fit['max_residual_pct']:.1f}%   "
          f"leave-one-out RMS {fit['loo_rms_pct']:.1f}%")
    print("  The trend summarises the surface; it does NOT resolve differences "
          "the paired\n  contrasts above leave unresolved.")

    # RULE 7: minimum, phrased correctly.
    best = int(np.argmin(matrix.mean(axis=1)))
    ref = grid.index[(grid["t_um"] == 1.0) & (np.isclose(grid["coverage"], 0.64))]
    print("\n--- minimum within the sampled box (rule 7) ---")
    b = grid.loc[best]
    print(f"  lowest modeled QP yield: t={b['t_um']:g} um, coverage={b['coverage']:.2f} "
          f"({b['label']})")
    print(f"    yield {matrix[best].mean():.4e}   n_eff {b['n_eff']:.1f}")
    if len(ref):
        i = ref[0]
        d, lo, hi, res = paired_contrast(matrix, i, best, boot_idx)
        print(f"  vs reference (t=1, cov=0.64): {100*d:+.1f}% "
              f"[{100*lo:+.1f}%, {100*hi:+.1f}%]  resolved={res}")
    print("  This is the MINIMUM MODELED QP YIELD WITHIN THE SAMPLED BOX, not a")
    print("  device optimum: the model has no microwave-loss, stress or")
    print("  fabrication term, and coverage is monotone-beneficial in it by")
    print("  construction (plan sec. 3.3a).")

    # RULE 6.
    print(f"\n--- diagnostics (rule 6) ---")
    print(f"  position_effective_n spans {grid['n_eff'].min():.1f}-{grid['n_eff'].max():.1f} "
          f"(raw, R={n_replicas}). At R=4 and low corner counts this is a")
    print("  DIAGNOSTIC of where the spatial quadrature is thin, not a physical "
          "observable;\n  see stage4_position_diagnostics.py for the debiased estimate.")


if __name__ == "__main__":
    main()
