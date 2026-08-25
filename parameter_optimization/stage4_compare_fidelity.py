#!/usr/bin/env python3
"""Compare Stage 4 candidate rankings across event budgets.

    python stage4_compare_fidelity.py results/stage4_confirmation.json \\
        results/stage4_confirmation_M.json results/stage4_confirmation_L.json

This is the Stage 4 counterpart of the v2 beamOn scaling study, and it exists
because that study's conclusion was uncomfortable: **125,000 events per sub-run
got the broad ordering right (Spearman 0.994) and still flipped the top-1**.
A screening tier is only allowed to select if its ranking survives the move to a
converged one, and that has to be measured, not assumed.

Reported per pair of tiers:

* Spearman rho and Kendall tau over the candidates present in both;
* every candidate's shift, so a rank correlation near 1 cannot hide a leader
  swapping places;
* which adjacent pairs are separated by more than the *measured* replica error,
  because two candidates 2% apart at a 12% error are not ranked at all.
"""

import argparse
import itertools
import json
import os
import sys

import numpy as np


def load(path):
    with open(path) as handle:
        data = json.load(handle)
    vals = {k: v for k, v in data["results"].items() if v.get("value") is not None}
    # Events per sub-run is READ, never assumed. It used to be `events // 32`,
    # which is wrong by exactly 4x under the 16 x 8 = 128 protocol: the
    # objective values were unaffected (they use total events) but every tier
    # LABEL and every fidelity statement derived from them was.
    per_sub = data.get("events_per_sub_run")
    n_sub = data.get("n_sub_runs")
    if per_sub is None and n_sub:
        per_sub = data["events"] // n_sub
    if per_sub is None:
        raise SystemExit(
            f"{os.path.basename(path)} records no `events_per_sub_run` or "
            f"`n_sub_runs`. It predates the metadata fix, and guessing the split "
            f"is how the tier labels came to be wrong by 4x. Re-run it, or add "
            f"the field from the ledger:\n"
            f"  SELECT events_per_sub_run, n_positions, n_replicas FROM trials "
            f"WHERE trial_id = '<any trial_id in this file>';")
    return {"path": os.path.basename(path), "events": data["events"],
            "per_sub_run": per_sub, "seed_bank": data["seed_bank"],
            "n_positions": data.get("n_positions"), "n_replicas": data.get("n_replicas"),
            "sim_hash": data.get("simulation_identity_hash"),
            "values": {k: v["value"] for k, v in vals.items()},
            "se": {k: (v.get("relative_se") or float("nan")) for k, v in vals.items()}}


def spearman(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def kendall(a, b):
    n = len(a)
    con = dis = 0
    for i, j in itertools.combinations(range(n), 2):
        s = np.sign(a[i] - a[j]) * np.sign(b[i] - b[j])
        con += s > 0
        dis += s < 0
    return float((con - dis) / (con + dis)) if (con + dis) else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tiers", nargs="+", help="confirmation JSONs, cheapest first")
    ap.add_argument("--baseline-label", default="baseline")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    tiers = [load(p) for p in args.tiers]
    tiers.sort(key=lambda t: t["per_sub_run"])
    print("Stage 4 fidelity comparison")
    for t in tiers:
        print(f"  {t['per_sub_run']:>12,} events/sub-run "
              f"({t['events']:,}/candidate, bank {t['seed_bank']}): "
              f"{len(t['values'])} candidates  [{t['path']}]")

    # Per-candidate table across tiers, normalised to each tier's own baseline so
    # the comparison is of RANKINGS, not of absolute yields at different budgets.
    labels = sorted(set().union(*[set(t["values"]) for t in tiers]),
                    key=lambda k: tiers[-1]["values"].get(k, 9e9))
    def tier_tag(t):
        n = t["per_sub_run"]
        return f"{n / 10 ** (len(str(n)) - 1):g}e{len(str(n)) - 1}/sub-run"

    header = f"{'candidate':28s}" + "".join(f"{tier_tag(t):>20s}" for t in tiers)
    print("\nQPs per primary event (relative to each tier's own baseline in brackets):")
    print(header)
    for label in labels:
        line = f"{label[:28]:28s}"
        for t in tiers:
            v = t["values"].get(label)
            base = t["values"].get(args.baseline_label)
            if v is None:
                line += f"{'--':>20s}"
            elif base:
                line += f"{v:12.3e} {100 * (v - base) / base:+6.1f}%"
            else:
                line += f"{v:20.3e}"
        print(line)

    print("\nRank agreement between tiers (baseline excluded):")
    out = {"tiers": [{k: t[k] for k in ("path", "events", "per_sub_run", "seed_bank")}
                     for t in tiers], "pairs": []}
    for lo, hi in itertools.combinations(range(len(tiers)), 2):
        a, b = tiers[lo], tiers[hi]
        common = [k for k in a["values"] if k in b["values"] and k != args.baseline_label]
        if len(common) < 3:
            continue
        va = np.array([a["values"][k] for k in common])
        vb = np.array([b["values"][k] for k in common])
        rho, tau = spearman(va, vb), kendall(va, vb)
        order_a = [common[i] for i in np.argsort(va)]
        order_b = [common[i] for i in np.argsort(vb)]
        moved = [k for k in common if order_a.index(k) != order_b.index(k)]
        top1 = "kept" if order_a[0] == order_b[0] else f"FLIPPED ({order_a[0]} -> {order_b[0]})"
        dev = np.abs(vb / va - 1.0)
        print(f"  {a['per_sub_run']:,}/sub-run vs {b['per_sub_run']:,}/sub-run "
              f"({len(common)} candidates): rho={rho:.3f} tau={tau:.3f}, "
              f"top-1 {top1}, {len(moved)} candidate(s) changed rank, "
              f"RMS value shift {100 * float(np.sqrt((dev ** 2).mean())):.1f}%")
        out["pairs"].append({"low": a["per_sub_run"], "high": b["per_sub_run"],
                             "n": len(common), "spearman": rho, "kendall": tau,
                             "top1_kept": order_a[0] == order_b[0],
                             "order_low": order_a, "order_high": order_b,
                             "rms_value_shift": float(np.sqrt((dev ** 2).mean()))})

    # Resolution at the finest tier: which adjacent pairs are actually separated.
    fine = tiers[-1]
    ranked = sorted([(v, k) for k, v in fine["values"].items() if k != args.baseline_label])
    print(f"\nAt {fine['per_sub_run']:,} events/sub-run, adjacent pairs separated by more "
          f"than the measured replica error:")
    for (v1, k1), (v2, k2) in zip(ranked, ranked[1:]):
        gap = (v2 - v1) / v1
        # The error on a DIFFERENCE is not the larger of the two errors -- that
        # understates it and calls pairs "resolved" that are not. For two
        # independent estimates it is the quadrature sum; the trials do share
        # sites and seeds, so a genuinely PAIRED error (stage4_objectives
        # .paired_difference) would be tighter still, but it needs the blocks,
        # which this file does not carry. Quadrature is the conservative choice
        # available here, and it is labelled as such.
        e1 = fine["se"].get(k1, float("nan"))
        e2 = fine["se"].get(k2, float("nan"))
        err = float(np.sqrt(np.nansum([e1 ** 2, e2 ** 2])))
        if not np.isfinite(err) or err == 0:
            err = 0.1
        verdict = "resolved" if gap > 2 * err else "NOT resolved"
        print(f"  {k1[:24]:24s} < {k2[:24]:24s}  gap {100 * gap:5.1f}% vs 2x combined "
              f"error {100 * 2 * err:4.1f}%  -> {verdict}")
        out.setdefault("adjacent_pairs", []).append(
            {"a": k1, "b": k2, "gap": gap, "combined_relative_error": err,
             "threshold": 2 * err, "resolved": gap > 2 * err,
             "error_model": "quadrature sum of the two replica-based relative "
                            "errors, compared at 2 sigma; a paired error would "
                            "be tighter but needs per-block data"})

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as handle:
            json.dump(out, handle, indent=1)
        print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
