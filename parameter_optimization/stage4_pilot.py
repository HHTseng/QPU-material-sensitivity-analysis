#!/usr/bin/env python3
"""Stage 4 pilot: determinism, noise, throughput, and the two inertness A/Bs.

Runs before any campaign, because four numbers decide how the campaign is sized
and none of them can be assumed:

1. **Determinism.** The same property vector with the same seed bank must give
   the SAME objective. If it does not, the cache is meaningless and so is every
   paired comparison.
2. **Noise.** The spread across seed banks at the screening event_count, measured
   two ways -- between whole banks, and from the within-site replica spread.
   The v2 record predicts ~5.0% (Poisson x 2.3, Fano ~ 5); this checks it on a
   pseudo-material rather than assuming it transfers.
3. **Throughput.** Wall time per candidate on a machine that is currently
   shared, which sets how many trials the campaign can afford.
4. **Inertness.** `sub_lattice_a` and `/g4cmp/temperature` are claimed inert for
   this gun type. Both are A/B'd at identical seeds: bit-identical results
   confirm the claim and justify holding them out of the search; a difference
   means the claim was wrong and the variable must be searched.

    python stage4_pilot.py --events 4000000 --workers 16
"""

import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from experiment_definition import load_definition                       # noqa: E402
from experiment_database import Database                                # noqa: E402
from stage3_trial_runner import evaluate                        # noqa: E402
import stage4_space as S                                        # noqa: E402
import stage4_objectives as O                                   # noqa: E402
from stage4_optimize import candidate_payload, resolver_for     # noqa: E402


def run(definition, point, space, database, seed_bank, force=False, label=""):
    cand = candidate_payload(point, space)
    t0 = time.time()
    r = evaluate(definition, cand, event_count=definition.decision["event_count"]["value"],
                 seed_bank_id=seed_bank, database=database, verbose=False, force=force,
                 resolver=resolver_for(space))
    wall = time.time() - t0
    if not r.is_observation:
        print(f"  {label:28s} FAILED: {r.status} {r.failure_reason}")
        return None
    v = O.get("total_qps_per_primary")(r)
    print(f"  {label:28s} total_QPs={r.total_qps:8.0f}  {v.value:.4e}/event  "
          f"replica-SE {100 * (v.detail['relative_se'] or 0):4.1f}%  "
          f"{wall:6.1f}s{'  (cached)' if r.cached else ''}")
    return {"total_qps": r.total_qps, "value": v.value, "se": v.se,
            "relative_se": v.detail["relative_se"], "wall_s": wall,
            "cached": r.cached, "trial_id": r.trial_id,
            "per_electrode": r.per_electrode_qps,
            "site_totals": v.detail["site_totals"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--definition", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--database", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--events", type=int, default=4000000)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--total-mem-gb", type=float, default=60.0)
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--banks", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--out", default=os.path.join(HERE, "results", "stage4_pilot.json"))
    args = ap.parse_args()

    definition = load_definition(args.definition)
    definition.campaign_id = "stage4_pilot"
    fid = definition.decision["event_count"]["value"]
    definition.decision["event_count"]["events_total_per_candidate"][fid] = args.events
    definition.fixed.update(max_workers=args.workers, total_mem_gb=args.total_mem_gb,
                          sample_timeout_s=args.timeout)
    space = S.DEFAULT_SPACE
    base = space.baseline_point()
    out = {"events_total_per_candidate": args.events, "workers": args.workers,
           "event_count": fid}

    print(f"Stage 4 pilot: {args.events:,} events per candidate "
          f"({definition.fixed['n_positions']} sites x {definition.fixed['n_replicas']} "
          f"replicas x {definition.events_per_sub_run(fid):,}), {args.workers} workers\n")

    with Database(args.database) as led:
        print("1. Determinism -- same vector, same seed bank, forced re-run")
        a = run(definition, base, space, led, args.banks[0], force=True, label="baseline run 1")
        b = run(definition, base, space, led, args.banks[0], force=True, label="baseline run 2")
        det = (a and b and a["total_qps"] == b["total_qps"]
               and a["per_electrode"] == b["per_electrode"])
        print(f"   -> {'PASS' if det else 'FAIL'}: identical objective under identical seeds")
        out["determinism"] = {"pass": bool(det), "run1": a, "run2": b}

        print("\n2. Noise -- independent seed banks at the same vector")
        banks = {}
        for bank in args.banks:
            r = run(definition, base, space, led, bank, label=f"seed bank {bank}")
            if r:
                banks[bank] = r
        out["seed_banks"] = banks
        if len(banks) >= 2:
            vals = np.array([v["value"] for v in banks.values()])
            across = float(vals.std(ddof=1) / vals.mean())
            within = float(np.mean([v["relative_se"] for v in banks.values()]))
            poisson = float(np.mean([1 / math.sqrt(v["total_qps"]) for v in banks.values()]))
            print(f"   across-bank CV      {across:6.2%}   (n={len(vals)} banks)")
            print(f"   within-run SE       {within:6.2%}   (replica spread at matched sites)")
            print(f"   Poisson would be    {poisson:6.2%}   -> Fano ~ "
                  f"{(within / poisson) ** 2:.1f}")
            out["noise"] = {"across_bank_cv": across, "within_run_se": within,
                            "poisson": poisson, "implied_fano": (within / poisson) ** 2}

        print("\n3. Inertness A/B -- identical seeds, one variable changed")
        for name, alt in (("sub_lattice_a", 6.5), ("temperature", 0.1)):
            p = dict(base)
            p[name] = alt
            r = run(definition, p, space, led, args.banks[0],
                    label=f"{name} = {alt}")
            if r and a:
                same = (r["total_qps"] == a["total_qps"]
                        and r["per_electrode"] == a["per_electrode"])
                delta = 100 * (r["value"] - a["value"]) / a["value"]
                se = 100 * (a["relative_se"] or 0.06)
                # Bit-identity is the only clean INERT verdict. Anything else is
                # reported against the stochastic error, because this executable
                # does not replay bit-for-bit (see the plan's reproducibility
                # note) -- so a few percent is not evidence of an effect.
                verdict = ("INERT (bit-identical)" if same else
                           f"not bit-identical: {delta:+.1f}% against a {se:.1f}% "
                           f"replica SE -> "
                           + ("INDISTINGUISHABLE from noise"
                              if abs(delta) < 2 * se else "SIGNIFICANT, keep it in the search"))
                print(f"   -> {name}: {verdict}")
                out.setdefault("inertness", {})[name] = {
                    "alt_value": alt, "bit_identical": bool(same),
                    "relative_change": delta / 100.0,
                    "replica_se": (a["relative_se"] or None),
                    "verdict": verdict,
                    "baseline_total_qps": a["total_qps"], "alt_total_qps": r["total_qps"]}

        print("\n4. Throughput")
        walls = [v["wall_s"] for v in [a, b] + list(banks.values()) if v and not v["cached"]]
        if walls:
            per = float(np.median(walls))
            print(f"   median {per:.0f}s per candidate at {args.workers} workers "
                  f"({args.events / per / 1000:.0f}k events/s)")
            print(f"   -> a 100-trial campaign at 4 concurrent candidates: "
                  f"~{100 * per / 4 / 60:.0f} min")
            out["throughput"] = {"median_wall_s": per, "workers": args.workers}

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
