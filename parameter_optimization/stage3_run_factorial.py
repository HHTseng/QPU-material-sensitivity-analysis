#!/usr/bin/env python3
"""Run the Stage 3 exhaustive factorial over the enabled catalog materials.

This is the driver that produced the 2026-08-13 18-combination result. It is
deliberately a *loop over `evaluate()`*, not an optimizer: at ~25 s per candidate
the whole factorial is minutes of compute, so exhaustive enumeration is both
simpler and more reliable than any surrogate. An optimizer only earns its
complexity when the space is larger than the budget.

Every candidate is scored on the SAME injection sites and the SAME seed bank, so
comparisons are paired. Results land in the SQLite ledger; nothing is printed
that is not also persisted.

Usage:
    python stage3_run_factorial.py                       # all enabled materials
    python stage3_run_factorial.py --events 4000000      # per candidate
    python stage3_run_factorial.py --positions 16 --replicas 2 --workers 32
    python stage3_run_factorial.py --lifetime-scale low  # uncertainty bracket
"""

import argparse
import itertools
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from stage3_contract import load_contract, ContractError   # noqa: E402
from stage3_trial_runner import evaluate, SUBSTRATES, TOP_FILMS, BOTTOM_FILMS  # noqa: E402
from stage3_ledger import Ledger                            # noqa: E402


def enabled(section):
    return [k for k, v in section.items() if v.get("enabled")]


def apply_lifetime_bracket(which):
    """Set every film's phonon lifetime to an end of its declared uncertainty.

    This is the section 5.4 ranking-stability test: if the ranking flips between
    `low` and `high`, the lifetime uncertainty dominates and the ranking is not
    yet a physics result. Materials without a declared range are left alone and
    reported, so a silent partial bracket cannot masquerade as a full one.
    """
    if which == "nominal":
        return []
    index = 0 if which == "low" else 1
    touched, skipped = [], []
    for name, section in (("top", TOP_FILMS), ("bottom", BOTTOM_FILMS)):
        for key, rec in section.items():
            rng = rec.get("ph_lifetime_uncertainty_ns")
            if rng and len(rng) == 2:
                rec["ph_lifetime_ns"] = float(rng[index])
                touched.append(f"{key}={rec['ph_lifetime_ns']}")
            elif rec.get("enabled"):
                skipped.append(key)
    return touched, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contract", default=os.path.join(HERE, "stage3_config.yaml"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage3_trials.sqlite"))
    ap.add_argument("--events", type=int, default=4000000, help="TOTAL events per candidate")
    ap.add_argument("--positions", type=int, default=16)
    ap.add_argument("--replicas", type=int, default=2)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--seed-bank", type=int, default=None)
    ap.add_argument("--lifetime-scale", choices=("nominal", "low", "high"), default="nominal",
                    help="use an end of each film's declared lifetime uncertainty")
    ap.add_argument("--baseline", default="Si/Nb/Cu")
    args = ap.parse_args()

    contract = load_contract(args.contract)
    contract.fixed["n_positions"] = args.positions
    contract.fixed["n_replicas"] = args.replicas
    contract.fixed["max_workers"] = args.workers
    fidelity = contract.decision["fidelity"]["value"]
    contract.decision["fidelity"]["events_total_per_candidate"][fidelity] = args.events

    if args.lifetime_scale != "nominal":
        touched, skipped = apply_lifetime_bracket(args.lifetime_scale)
        print(f"Lifetime bracket '{args.lifetime_scale}': {touched}")
        if skipped:
            print(f"  WARNING: no declared uncertainty, left at nominal: {skipped}")

    subs, tops, bots = enabled(SUBSTRATES), enabled(TOP_FILMS), enabled(BOTTOM_FILMS)
    combos = list(itertools.product(subs, tops, bots))
    print(f"Campaign {contract.campaign_id}: {len(subs)}x{len(tops)}x{len(bots)} = "
          f"{len(combos)} combinations at {args.events:,} events "
          f"({args.positions} positions x {args.replicas} replicas), "
          f"lifetime={args.lifetime_scale}")

    rows = {}
    with Ledger(args.ledger) as ledger:
        for sub, top, bot in combos:
            name = f"{sub}/{top}/{bot}"
            try:
                result = evaluate(contract, {"substrate": sub, "top_ground_film": top,
                                             "bottom_film": bot},
                                  seed_bank_id=args.seed_bank, ledger=ledger, verbose=False)
            except ContractError as exc:
                print(f"  {name:16s} REJECTED: {exc}")
                continue
            rows[name] = result
            flag = "" if result.is_observation else "  <-- NOT an observation"
            print(f"  {name:16s} {result.status:8s} QPs={result.total_qps or 0:7.0f}  "
                  f"{(result.qps_per_primary or 0):.3e}/event  {result.runtime_s:5.1f}s{flag}")

    observations = {n: r for n, r in rows.items() if r.is_observation}
    if args.baseline not in observations:
        print(f"\nBaseline {args.baseline} not among the observations; ranking by raw count.")
        base = None
    else:
        base = observations[args.baseline]

    print(f"\nRANKED (lower = less QP damage)"
          + (f". Baseline {args.baseline} = {base.total_qps:.0f}" if base else ""))
    for name, r in sorted(observations.items(), key=lambda kv: kv[1].total_qps):
        if base is None or name == args.baseline:
            print(f"  {name:16s} {r.total_qps:7.0f}" + ("  <-- baseline" if base else ""))
            continue
        delta = r.total_qps - base.total_qps
        # Poisson counting comparison; replace with the replicate SD once more
        # than two replicas are available.
        sigma = math.sqrt(r.total_qps + base.total_qps) or 1.0
        print(f"  {name:16s} {r.total_qps:7.0f}  {100 * delta / base.total_qps:+6.1f}%  "
              f"z={delta / sigma:+5.1f}")

    failed = len(rows) - len(observations)
    print(f"\n{len(observations)} scored, {failed} not scored, "
          f"{len(combos) - len(rows)} rejected before running.")
    print(f"Ledger: {args.ledger}")
    return 0 if observations and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
