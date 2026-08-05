"""Preliminary constrained/Pareto analysis for the Cu backside stack.

WHY THIS NEEDS NO EM NUMBERS
---------------------------
The microwave-loss surface is an external input this pipeline cannot produce
(plan sec. 3.3a) -- but the *front* does not depend on its values, only on its
shape. If loss is monotone non-decreasing in coverage at fixed pitch, then for
two designs at equal pitch

    B dominates A  <=>  coverage_B <= coverage_A  and  QP_B <= QP_A

because any monotone loss function preserves the coverage ordering. So the
Pareto-optimal SET is computable from the QP campaign alone. The EM curve is
needed to choose a POINT ON the front, not to find the front.

Stated assumptions, in decreasing order of confidence:
  A1. Loss is monotone non-decreasing in coverage at fixed pitch. This is the
      premise of the whole exercise; if it fails, coverage is not a cost at all
      and the QP minimum is simply the answer.
  A2. Pitch is fixed at 250 um. Pitch was measured QP-neutral to within 12% at
      2 sigma -- but only at t=1 um, f=0.64 (plan sec. 6d), NOT at the
      high-coverage corner where the front lives.
  A3. Thickness carries no EM cost. Physical deposition cost is real, so
      thickness is reported per front point as a secondary cost axis rather
      than being optimized away silently.

ROBUST DOMINATION (rule 4 applied to the front)
-----------------------------------------------
A design is removed from the front only if some other design dominates it with a
RESOLVED paired contrast. An unresolved QP difference cannot eliminate a
candidate -- which is exactly the error the M2 promotion caught, where the grid's
apparent minimum at f=0.85 was a noise excursion (plan sec. 6h).

Usage:
    python stage4_pareto.py --results-dir results/<m2_run_id> \
        --labels m2_grid_design.labels.json \
        [--promoted results/<m2promote_run_id>:m2_promote_design.labels.json] \
        [--em-loss em_loss.csv]
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

BOOT = 20000
BOOT_SEED = 20260729

EM_SCHEMA = """\
Expected --em-loss CSV schema (one row per geometry actually simulated/measured):

    coverage,pitch_um,loss_metric,loss_value,loss_sigma,source
    0.30,250,Qi_internal,1.85e6,0.09e6,HFSS-2026-08-03
    0.45,250,Qi_internal,1.42e6,0.08e6,HFSS-2026-08-03
    ...

  coverage     as defined here: (l_island/(l_island+l_spacing))^2
  pitch_um     l_island + l_spacing
  loss_metric  free text, but ONE metric per file; state whether higher is
               better (Qi) or worse (tan_delta, loss_rate)
  loss_value   the metric
  loss_sigma   1-sigma uncertainty; required, since the robust front needs it
  source       solver + date, or measurement id -- provenance is not optional

Also needed alongside, and NOT inferable here:
  * whether the metric improves or degrades with increasing value
  * the acceptable region (threshold or budget) that defines "constrained"
  * whether pitch is genuinely free or bounded by fabrication
"""

CONSTRAINT_MENU = """\
MINIMUM EXTERNAL INFORMATION NEEDED. A full loss surface is ideal, but ANY ONE
of these collapses the candidate pool to a decision:

  1. maximum acceptable coverage          -> selects the highest-coverage front
                                             design at or below it
  2. allowed pitch range                  -> fixes A2, and if it excludes 250 um
                                             the pitch-neutrality check must be
                                             repeated at the front geometry
  3. thickness / stress / Cu-volume limit -> prunes the thickness objective
  4. max added 1/Q, or minimum Q_i        -> the constrained form proper
  5. a device-team list of fabrication-   -> intersect it with the front and
     admissible geometries                   report the best admissible member

Item 1 alone is enough for a conditional recommendation TODAY, because the front
is monotone in coverage: given a cap, the answer is the front member at that cap.
"""


def load_matrix(results_dir, labels_path, prefix_strip=True):
    labels = json.load(open(labels_path))
    by_row = {r["row"]: r for r in labels["rows"]}
    summary = pd.read_csv(os.path.join(results_dir, "qp_summary.csv"))
    summary["row"] = summary["design_point"].str.rsplit("_", n=1).str[-1].astype(int)

    rows, matrix = [], []
    for row in sorted(summary["row"].unique()):
        block = summary[summary["row"] == row].sort_values("replica")
        meta = by_row[row]
        rows.append({
            "label": meta["label"],
            "t_um": meta["setBotThickness_um"],
            "coverage": round(meta["coverage"], 4),
            "pitch_um": meta["pitch_um"],
        })
        matrix.append(block["QP_yield_per_event"].to_numpy(dtype=float))
    return pd.DataFrame(rows), np.array(matrix)


def build_bootstrap(cells, n_boot=BOOT, seed=BOOT_SEED):
    """Per-cell bootstrap draws of the mean, correctly paired within a seed bank.

    Two defects this replaces, both of which silently corrupted the front:

      * index sets were sized to the GRID's R and reused for promoted cells with
        a different R, so a promoted-vs-promoted contrast resampled only the
        first 4 of 8 replicas -- exactly the t=10 f=0.85 vs f=0.90 comparison at
        the top of the front;
      * the unpaired branch drew from a single shared RNG inside the comparison
        loop, so results depended on the ORDER comparisons were made in and were
        not reproducible.

    Fix: one index matrix per (bank, R) group, drawn once from a seeded RNG.
    Cells in the same group share indices, which preserves the common-random-
    number pairing the seed banks were built for; cells in different groups get
    independent indices, which is honest because they share no streams.

    Returns an array of shape (n_cells, n_boot) of bootstrap mean draws.
    """
    rng = np.random.default_rng(seed)
    groups = {}
    for cell in cells:
        groups.setdefault((cell["bank"], len(cell["values"])), None)
    for key in sorted(groups, key=lambda k: (str(k[0]), k[1])):
        _, n_rep = key
        groups[key] = rng.integers(0, n_rep, size=(n_boot, n_rep))

    draws = np.empty((len(cells), n_boot))
    for k, cell in enumerate(cells):
        idx = groups[(cell["bank"], len(cell["values"]))]
        draws[k] = cell["values"][idx].mean(axis=1)
    return draws


def dominates(cells, draws, j, i, objectives):
    """Does j resolvedly dominate i over `objectives`?

    Non-QP objectives (coverage, thickness) are deterministic design choices, so
    they are compared exactly. QP is compared through the bootstrap and must be
    RESOLVED -- an unresolved QP difference cannot eliminate a candidate, which
    is the error that produced a false minimum in plan sec. 6h.
    """
    for key in objectives:
        if key == "qp":
            continue
        if cells[j][key] > cells[i][key]:
            return False
    ratio = draws[j] / draws[i] - 1.0
    return bool(np.percentile(ratio, 97.5) < 0)


def front_indices(cells, draws, objectives):
    """Indices not resolvedly dominated."""
    n = len(cells)
    keep = []
    for i in range(n):
        if not any(dominates(cells, draws, j, i, objectives)
                   for j in range(n) if j != i):
            keep.append(i)
    return np.array(keep, dtype=int)


def front_membership_probability(cells, draws, objectives):
    """P(design is Pareto-optimal), from the bootstrap.

    Binary inclusion at a 95% cutoff answers "can this design be excluded?",
    which is not the decision question. This answers "how often is it actually
    on the front?" -- so a design that is optimal in 8% of draws is visibly
    marginal rather than indistinguishable from one that is optimal in 92%.

    Domination here uses the bootstrap draw directly rather than a re-derived
    interval, since within a draw the values are known.
    """
    n_cells, n_boot = draws.shape
    counts = np.zeros(n_cells)
    fixed = [k for k in objectives if k != "qp"]
    better_or_equal = np.ones((n_cells, n_cells), dtype=bool)
    for j in range(n_cells):
        for i in range(n_cells):
            better_or_equal[j, i] = all(cells[j][k] <= cells[i][k] for k in fixed)

    for b in range(n_boot):
        y = draws[:, b]
        for i in range(n_cells):
            beaten = False
            for j in range(n_cells):
                if j != i and better_or_equal[j, i] and y[j] < y[i]:
                    beaten = True
                    break
            if not beaten:
                counts[i] += 1
    return counts / n_boot


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--promoted", default=None,
                        help="results_dir:labels_path for promoted candidates, "
                             "whose values override the grid's where labels match "
                             "on (t_um, coverage).")
    parser.add_argument("--em-loss", type=Path, default=None,
                        help="EM loss surface. Without it the front is reported "
                             "but no point on it can be selected.")
    args = parser.parse_args()

    grid, matrix = load_matrix(str(args.results_dir), str(args.labels))

    overrides = {}
    if args.promoted:
        pdir, plabels = args.promoted.split(":", 1)
        pgrid, pmatrix = load_matrix(pdir, plabels)
        for k, r in pgrid.iterrows():
            overrides[(r["t_um"], r["coverage"])] = pmatrix[k]

    print("PRELIMINARY PARETO ANALYSIS -- Cu backside stack")
    print(f"grid: {len(grid)} designs x {matrix.shape[1]} replicas   "
          f"bootstrap: {BOOT} replica-block draws")
    print("\nAssumptions: A1 loss monotone in coverage; A2 pitch fixed 250 um "
          "(neutrality tested\nonly at t=1/f=0.64); A3 thickness IS treated as a "
          "formal cost axis -- see below.")

    # Build the cell list, SUBSTITUTING promoted measurements where they exist.
    # Omitting this would leave the known-bad f=0.85 grid value on the front
    # (a +34% noise excursion, plan sec. 6h).
    cells = []
    for k, r in grid.iterrows():
        key = (r["t_um"], r["coverage"])
        promoted = overrides.get(key)
        cells.append({
            "label": r["label"], "t_um": r["t_um"], "coverage": r["coverage"],
            "values": promoted if promoted is not None else matrix[k],
            "bank": "promoted" if promoted is not None else "grid",
        })

    if overrides:
        print(f"\nPromoted measurements substituted for {len(overrides)} design(s):")
        for (t, c), v in sorted(overrides.items()):
            hit = grid.index[(grid["t_um"] == t) & (np.isclose(grid["coverage"], c))]
            if len(hit):
                old = matrix[hit[0]].mean()
                print(f"  t={t:g}, f={c:.2f}: grid {old:.4e} -> promoted {v.mean():.4e} "
                      f"({100*(v.mean()/old-1):+.1f}%, R={len(v)})")

    draws = build_bootstrap(cells)

    for label, objectives in (("2-objective  (QP, coverage)", ("qp", "coverage")),
                              ("3-objective  (QP, coverage, thickness)",
                               ("qp", "coverage", "t_um"))):
        idx = front_indices(cells, draws, objectives)
        prob = front_membership_probability(cells, draws, objectives)
        print(f"\n=== {label}: {len(idx)} of {len(cells)} on the front ===")
        print(f"{'label':22s} {'t_um':>5} {'cov':>5} {'yield/evt':>11} {'relSEM':>7} "
              f"{'R':>3} {'source':>9} {'P(front)':>9}")
        for i in sorted(idx, key=lambda k: (cells[k]["coverage"], cells[k]["t_um"])):
            c = cells[i]; v = c["values"]
            sem = v.std(ddof=1) / np.sqrt(len(v))
            print(f"{c['label']:22s} {c['t_um']:>5g} {c['coverage']:>5.2f} "
                  f"{v.mean():>11.4e} {100*sem/v.mean():>6.1f}% {len(v):>3} "
                  f"{c['bank']:>9} {prob[i]:>8.1%}")
        marginal = [(cells[i]["label"], prob[i]) for i in idx if prob[i] < 0.25]
        if marginal:
            print("  marginal members (on the front only because they cannot be "
                  "resolvedly excluded):")
            for name, pr in sorted(marginal, key=lambda x: x[1]):
                print(f"    {name:24s} P(front) = {pr:.1%}")

    print("\n--- thickness as a formal objective: the decision, and why ---")
    print("  DO NOT make it a formal Pareto objective. The 3-objective front above")
    print("  contains ALL 28 designs, and that degeneracy is structural, not a")
    print("  fidelity artifact: QP decreases monotonically with thickness, so a")
    print("  thinner design can never dominate on QP and a thicker one can never")
    print("  dominate on cost. Every combination is non-dominated by construction,")
    print("  P(front) saturates, and the front loses all discriminating power.")
    print("  Thickness IS a real cost (Cu volume, deposition, stress) -- but it")
    print("  enters correctly as a CONSTRAINT ('thickness <= X'), supplied")
    print("  externally, exactly like a coverage cap. The operative front is")
    print("  therefore the 2-objective one, read subject to a thickness cap.")

    print("\n--- what this front does and does not say ---")
    print("  * Every listed design is a legitimate candidate: nothing else is")
    print("    cheaper on every fixed axis AND resolvedly lower in QP.")
    print("  * The front spans the full coverage range, so the choice among these")
    print("    designs is determined entirely by external cost information.")
    print("  * P(front) separates strong members from ones that survive only")
    print("    because they cannot be excluded at this fidelity.")

    if args.em_loss is None:
        print("\n--- NO EXTERNAL CONSTRAINT SUPPLIED ---")
        print(EM_SCHEMA)
        print(CONSTRAINT_MENU)
    else:
        raise SystemExit(
            f"--em-loss given ({args.em_loss}) but the combination step is not "
            f"implemented against a guessed schema. Supply a file matching the "
            f"schema above.\n\n{EM_SCHEMA}")


if __name__ == "__main__":
    main()
