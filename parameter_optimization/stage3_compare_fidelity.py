#!/usr/bin/env python3
"""Compare material rankings across beamOn levels.

Answers: does the ranking obtained at 125,000 events per sub-run survive at 1e7
and 1e8? If it does, the cheap tier is a valid screen. If it does not, every
ranking claim made at the cheap tier has to be re-made.

Reports, per tier:

  * Spearman rho and Kendall tau against the baseline tier's ranking;
  * the largest rank displacement and which candidate moved;
  * whether the top-1 and top-3 sets are preserved (what actually matters for
    selection);
  * per-candidate QPs-per-primary-event, which is the tier-independent quantity;
  * pairs that were statistically unresolved at the baseline tier and whether
    the higher tier resolves them.

A caution the numbers cannot express on their own: counting statistics is only
one error term. With 16 injection sites and a measured 60.6% site-to-site
spread, the spatial quadrature error on the mean is ~15%, which does not shrink
with more events per site. Past ~1e7 the ranking is limited by the site set and
the interface model, not by Poisson noise -- so agreement between tiers means
"the counting noise was not the problem", never "the ranking is correct".

Usage:
    python stage3_compare_fidelity.py \
        --baseline-ledger stage3_trials.sqlite \
        --ledger e7=stage3_trials_e7.sqlite \
        --ledger e8=stage3_trials_e8.sqlite
"""

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from stage3_ledger import Ledger   # noqa: E402


def load(path, events_total=None):
    """{candidate name: row} for every scored trial, keyed by material triplet.

    `events_total` pins the tier. Without it a ledger holding more than one event
    count -- e.g. a timing probe alongside the campaign -- would silently
    contribute its highest-statistics row and the comparison would be against the
    wrong baseline. A name may also legitimately repeat within one tier when the
    code fingerprint changed between runs; those are checked for agreement rather
    than silently collapsed.
    """
    if not os.path.isfile(path):
        return {}
    out, seen = {}, {}
    with Ledger(path) as ledger:
        for r in ledger.observations():
            if events_total is not None and r["events_total"] != events_total:
                continue
            c = json.loads(r["candidate"])
            name = f"{c['substrate']}/{c['top_ground_film']}/{c['bottom_film']}"
            seen.setdefault(name, []).append(r["total_qps"])
            if name not in out or r["events_total"] > out[name]["events_total"]:
                out[name] = {"total_qps": r["total_qps"],
                             "qps_per_primary": r["qps_per_primary"],
                             "events_total": r["events_total"],
                             "n_positions": r["n_positions"],
                             "n_replicas": r["n_replicas"]}
    for name, qs in seen.items():
        if len(qs) > 1 and len(set(qs)) > 1:
            print(f"  NOTE {name}: {len(qs)} rows at this event count with differing "
                  f"totals {sorted(set(qs))}; using {out[name]['total_qps']:.0f}")
    return out


def ranks(values):
    """name -> 1-based rank, ascending (rank 1 = fewest QPs = best)."""
    order = sorted(values, key=lambda n: values[n]["qps_per_primary"])
    return {n: i + 1 for i, n in enumerate(order)}


def spearman(r1, r2, names):
    n = len(names)
    if n < 2:
        return float("nan")
    d2 = sum((r1[x] - r2[x]) ** 2 for x in names)
    return 1 - 6 * d2 / (n * (n * n - 1))


def kendall_tau(r1, r2, names):
    conc = disc = 0
    ns = list(names)
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            a, b = ns[i], ns[j]
            s = (r1[a] - r1[b]) * (r2[a] - r2[b])
            if s > 0:
                conc += 1
            elif s < 0:
                disc += 1
    total = conc + disc
    return (conc - disc) / total if total else float("nan")


def z_between(qa, qb):
    """Poisson z for two raw counts."""
    if qa is None or qb is None:
        return float("nan")
    s = math.sqrt(qa + qb)
    return (qb - qa) / s if s else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-ledger", default=os.path.join(HERE, "stage3_trials.sqlite"))
    ap.add_argument("--baseline-label", default="125k")
    ap.add_argument("--ledger", action="append", default=[],
                    help="label=path, repeatable")
    ap.add_argument("--baseline-candidate", default="Si/Nb/Cu")
    ap.add_argument("--baseline-events", type=int, default=4000000,
                    help="events_total that identifies the baseline tier")
    args = ap.parse_args()

    base = load(args.baseline_ledger, events_total=args.baseline_events)
    if not base:
        sys.exit(f"no scored trials in {args.baseline_ledger}")

    tiers = []
    for spec in args.ledger:
        if "=" not in spec:
            sys.exit(f"--ledger needs label=path, got {spec!r}")
        label, path = spec.split("=", 1)
        rows = load(path)
        if rows:
            tiers.append((label, path, rows))
        else:
            print(f"(tier {label}: no scored trials yet at {path} -- skipped)")
    if not tiers:
        sys.exit("no higher-statistics tier has scored trials yet; nothing to compare.")

    per_sub_base = None
    any_row = next(iter(base.values()))
    per_sub_base = any_row["events_total"] // (any_row["n_positions"] * any_row["n_replicas"])
    print(f"\nBaseline tier {args.baseline_label}: {len(base)} candidates, "
          f"{per_sub_base:,} events per sub-run\n")

    base_rank = ranks(base)

    for label, path, rows in tiers:
        shared = sorted(set(base) & set(rows))
        if len(shared) < 2:
            print(f"tier {label}: only {len(shared)} shared candidate(s); skipping.")
            continue
        r0 = any_row = next(iter(rows.values()))
        per_sub = r0["events_total"] // (r0["n_positions"] * r0["n_replicas"])
        sub_base = {n: base[n] for n in shared}
        sub_new = {n: rows[n] for n in shared}
        rk_base, rk_new = ranks(sub_base), ranks(sub_new)

        rho = spearman(rk_base, rk_new, shared)
        tau = kendall_tau(rk_base, rk_new, shared)
        moves = {n: rk_new[n] - rk_base[n] for n in shared}
        worst = max(moves, key=lambda n: abs(moves[n]))

        print("=" * 78)
        print(f"TIER {label}: {per_sub:,} events per sub-run "
              f"({per_sub / per_sub_base:.0f}x baseline), {len(shared)} candidates compared")
        print("=" * 78)
        print(f"  Spearman rho = {rho:+.4f}     Kendall tau = {tau:+.4f}")
        print(f"  largest rank move: {worst} {rk_base[worst]} -> {rk_new[worst]} "
              f"({moves[worst]:+d})")
        top1_same = (min(shared, key=lambda n: rk_base[n]) ==
                     min(shared, key=lambda n: rk_new[n]))
        top3_base = {n for n in shared if rk_base[n] <= 3}
        top3_new = {n for n in shared if rk_new[n] <= 3}
        print(f"  top-1 preserved: {top1_same}      "
              f"top-3 set preserved: {top3_base == top3_new}"
              + ("" if top3_base == top3_new else f"  ({sorted(top3_base)} -> {sorted(top3_new)})"))

        print(f"\n  {'candidate':16s} {'rank':>9s}  {'QPs/event ' + args.baseline_label:>16s} "
              f"{'QPs/event ' + label:>16s} {'shift':>8s}")
        for n in sorted(shared, key=lambda x: rk_new[x]):
            b, v = sub_base[n]["qps_per_primary"], sub_new[n]["qps_per_primary"]
            shift = 100 * (v - b) / b if b else float("nan")
            mv = f"{rk_base[n]}->{rk_new[n]}"
            print(f"  {n:16s} {mv:>9s}  {b:16.4e} {v:16.4e} {shift:+7.1f}%")

        # Pairs the baseline tier could not order.
        print(f"\n  pairs unresolved at {args.baseline_label} (|z| < 2), and their status at {label}:")
        order = sorted(shared, key=lambda n: rk_base[n])
        found = False
        for a, b in zip(order, order[1:]):
            zb = z_between(sub_base[a]["total_qps"], sub_base[b]["total_qps"])
            if abs(zb) >= 2:
                continue
            found = True
            zn = z_between(sub_new[a]["total_qps"], sub_new[b]["total_qps"])
            flipped = (rk_new[a] > rk_new[b])
            verdict = ("RESOLVED" if abs(zn) >= 3 else "still unresolved")
            print(f"    {a} vs {b}:  z {zb:+.1f} -> {zn:+.1f}  {verdict}"
                  + ("  ORDER FLIPPED" if flipped else ""))
        if not found:
            print("    (none)")
        print()

    print("-" * 78)
    print("Reminder: agreement between tiers shows counting noise was not the")
    print("limiting error. It does NOT validate the ranking. With 16 sites and a")
    print("60.6% site-to-site spread the spatial quadrature error on the mean is")
    print("~15%, independent of events per site, and the interface model still")
    print("reports physics_validation_passed = False.")
    return 0


if __name__ == "__main__":
    sys.exit(main())