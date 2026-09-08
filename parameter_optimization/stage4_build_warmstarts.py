#!/usr/bin/env python
"""Select warm-start vectors for the stratified campaign (Sep-8 plan, Priority 6).

THE RULE THIS FILE ENFORCES
---------------------------
Old objective values are NEVER imported. J_16, J_32, J_64 and J_stratified are
four different quantities; the optimizer is single-fidelity, so inserting an old
value as an observation of the new objective would poison the GP with a number
that was never measured. What carries over is the RAW PHYSICAL VECTOR, which is
re-transformed under the current bounds -- never the old unit coordinates, which
mean something different if any bound moved.

Selection favours diversity over past score, because past score was measured on
the quadrature that failed. The mandatory members are the baseline, the old
Sobol winner, the Nb-gap-compatible anchor and the corner diagnostic; the rest
maximise minimum distance in the current unit box (a farthest-point / maximin
sweep) so the initial design spans the space rather than clustering where the
old objective happened to look good.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from stage3_contract import load_contract          # noqa: E402
from stage4_optimize import build_space            # noqa: E402
import stage4_space as S                           # noqa: E402

MANDATORY = ("baseline", "ideal_target", "nb_gap_compatible_anchor", "bo_gp_corner")


def gap_ok(space, point, floor_eV):
    try:
        return float(point.get("topfilm_gap", 0.0)) >= floor_eV
    except (TypeError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--contract", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--gap-floor", type=float, default=1.5384e-3,
                    help="direct modeled-gap floor in eV (Sep-8 plan 3.1)")
    ap.add_argument("--out", default=os.path.join(HERE, "results",
                                                  "stage4_p6_points.json"))
    args = ap.parse_args()

    contract = load_contract(args.contract)
    space = build_space(contract)

    pool = {}
    for name in ("stage4_strat_points.json", "stage4_sites_points.json",
                 "stage4_sites_anchor_points.json", "stage4_gapfill_points.json"):
        path = os.path.join(HERE, "results", name)
        if os.path.isfile(path):
            with open(path) as fh:
                for k, v in json.load(fh).items():
                    pool.setdefault(k, v)

    # Every point the benchmark actually simulated, read from the LEDGER's
    # `candidate` column -- the report only carries the promotion shortlist, and
    # selecting warm starts from the shortlist would inherit the ranking that
    # the failed quadrature produced.
    import sqlite3
    led = os.path.join(HERE, "stage4_trials.sqlite")
    if os.path.isfile(led):
        con = sqlite3.connect(f"file:{led}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT trial_id, candidate FROM trials "
            "WHERE status IN ('success','success_zero') AND candidate IS NOT NULL"
        ).fetchall()
        for tid, blob in rows:
            try:
                vec = json.loads(blob)
            except (TypeError, ValueError):
                continue
            if isinstance(vec, dict) and "topfilm_gap" in vec:
                pool.setdefault(f"led_{str(tid)[-10:]}", vec)
        con.close()
        print(f"ledger contributed {len(rows)} completed trials")

    feasible = {k: v for k, v in pool.items()
                if k in MANDATORY or gap_ok(space, v, args.gap_floor)}
    print(f"pool {len(pool)} -> {len(feasible)} satisfy "
          f"topfilm_gap >= {args.gap_floor:.4e} eV (mandatory members kept regardless)")

    # Re-transform to CURRENT unit coordinates. If a bound moved, the old unit
    # value is meaningless; the physical value is not.
    def unit(vec):
        try:
            return np.asarray(space.to_unit(vec), dtype=float)
        except Exception:
            return None

    chosen = [k for k in MANDATORY if k in feasible]
    rest = {k: unit(v) for k, v in feasible.items() if k not in chosen}
    rest = {k: u for k, u in rest.items() if u is not None and np.all(np.isfinite(u))}
    picked_u = [u for u in (unit(feasible[k]) for k in chosen)
                if u is not None and np.all(np.isfinite(u))]

    while len(chosen) < args.n and rest:
        if picked_u:
            best = max(rest, key=lambda k: min(
                float(np.linalg.norm(rest[k] - p)) for p in picked_u))
        else:
            best = next(iter(rest))
        picked_u.append(rest.pop(best))
        chosen.append(best)

    out = {k: feasible[k] for k in chosen}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"wrote {len(out)} warm starts -> {args.out}")
    print("  mandatory: " + ", ".join(k for k in chosen if k in MANDATORY))
    print("  NOTE: physical vectors only. No old objective value is carried;")
    print("        they are re-evaluated under the stratified objective.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
