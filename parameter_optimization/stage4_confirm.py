#!/usr/bin/env python3
"""Confirm Stage 4 finalists at a higher fidelity with HELD-OUT seeds.

    python stage4_confirm.py --report results/stage4_report.json --top 3 \\
        --fidelity M --seed-bank 9 --workers 16 --parallel 2

Selection and confirmation must not share seeds. The screening campaign runs on
seed bank 0; this runs the finalists on a bank that no optimizer has seen, at a
higher event budget, on the SAME 16 injection sites. Changing the site set here
would change the objective between selection and confirmation and invalidate the
comparison -- confirmation adds seeds and statistics, never a new scenario.

What it reports, and why each one:

* the confirmed objective with its replica-based error -- the headline;
* the paired, site-matched difference against the baseline, with the number of
  sites that favour the candidate. A candidate that wins on 9 of 16 sites by a
  lot is a different animal from one that wins on 16 of 16 by a little, and the
  second is the one you can fabricate against;
* the screening-vs-confirmation shift, which is the honest measure of how much
  the screening tier was over-fitting its own noise.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from stage3_contract import load_contract, ContractError        # noqa: E402
from stage3_ledger import Ledger                                # noqa: E402
from stage3_trial_runner import evaluate                        # noqa: E402
import stage4_space as S                                        # noqa: E402
import stage4_objectives as O                                   # noqa: E402
from stage4_optimize import candidate_payload, resolver_for, build_space  # noqa: E402


def _refuse_if_frozen(ledger_path):
    """Exit before touching anything if the ledger is closed to new writers."""
    from stage3_ledger import freeze_reason, freeze_path
    reason = freeze_reason(ledger_path)
    if reason:
        sys.exit(f"REFUSING TO RUN: {os.path.abspath(ledger_path)} is frozen.\n\n"
                 f"{reason}\n\nRemove {freeze_path(ledger_path)} deliberately, "
                 f"after snapshotting, to lift this.")



def _complete(space, point, label):
    """`space.complete` plus an explicit realization check.

    `complete()` preserves the realization block; this validates it here, at
    load time, so a points file naming an unknown density carrier or an
    incomplete lattice record fails before any trial is planned rather than
    silently falling back to Si -- the P0 defect.
    """
    out = space.complete(point)
    try:
        real = S.normalize_realization(out)
    except S.GateError as exc:
        sys.exit(f"{label}: unusable realization -- {exc}")
    if real != S.default_realization():
        out[S.REALIZATION_KEY] = real
        print(f"  {label}: realization {real['mode']} "
              f"carrier={real['substrate_carrier']} "
              f"lattice={real['lattice_map']}")
    return out


def collect_points(args, space):
    """Finalists, the baseline control, and anything named explicitly."""
    points = {}
    if args.report and os.path.isfile(args.report):
        with open(args.report) as handle:
            report = json.load(handle)
        for i, t in enumerate((report.get("promote") or [])[:args.top], 1):
            label = f"{i:02d}_{t.get('optimizer', 'opt')}_{str(t.get('trial_id'))[-8:]}"
            points[label] = _complete(space, t["point"], label)
    for path in args.manifest or []:
        with open(path) as handle:
            man = json.load(handle)
        if man.get("best"):
            points[f"best_{man.get('optimizer')}"] = _complete(
                space, man["best"]["point"], f"best_{man.get('optimizer')}")
    if args.projection and os.path.isfile(args.projection):
        with open(args.projection) as handle:
            proj = json.load(handle)
        for label, rec in ((proj.get("verification") or {}).get("results") or {}).items():
            if label in ("ideal_target", "baseline"):
                continue
            # The projection stores only its verification results, not the
            # points; re-derive them from the shortlist on demand instead of
            # guessing here.
            _ = rec
    if args.point:
        points["explicit"] = _complete(space, json.loads(args.point), "explicit")
    if args.points_file:
        # {label: point} written by whatever produced the candidates -- e.g. the
        # projection's elasticity variants, which exist only as ledger rows.
        with open(args.points_file) as handle:
            for label, point in json.load(handle).items():
                points[label] = _complete(space, point, label)
    points["baseline"] = space.baseline_point()
    return points


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", default=os.path.join(HERE, "results", "stage4_report.json"))
    ap.add_argument("--manifest", nargs="*", default=None)
    ap.add_argument("--projection", default=None)
    ap.add_argument("--point", default=None)
    ap.add_argument("--points-file", default=None,
                    help="JSON {label: property vector} to confirm alongside the "
                         "report's finalists")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--contract", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--fidelity", default="M", choices=("S", "M", "L"))
    ap.add_argument("--events", type=int, default=None, help="override the tier")
    ap.add_argument("--seed-bank", type=int, default=9,
                    help="MUST be a bank the campaign did not use")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="absolute wall-clock limit per sub-run; 0 = UNLIMITED "
                         "(default). An absolute limit censors on candidate "
                         "quality -- the low-absorption designs the optimizer "
                         "prefers are the slow ones -- so prefer --stall-timeout")
    ap.add_argument("--stall-timeout", type=float, default=7200.0,
                    help="kill a sub-run only if it writes NOTHING for this "
                         "long (0 = off). Cannot correlate with candidate "
                         "quality; catches a hung process and nothing else")
    ap.add_argument("--objective", default="total_qps_per_primary")
    ap.add_argument("--tag", default="confirm")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "stage4_confirmation.json"))
    args = ap.parse_args()
    _refuse_if_frozen(args.ledger)

    contract = load_contract(args.contract)
    space = build_space(contract)
    points = collect_points(args, space)
    if len(points) < 2:
        sys.exit("nothing to confirm: no finalists found")

    contract.campaign_id = f"{contract.campaign_id}_{args.tag}"
    contract.decision["fidelity"]["value"] = args.fidelity
    if args.events:
        contract.decision["fidelity"]["events_total_per_candidate"][args.fidelity] = args.events
    events = int(contract.decision["fidelity"]["events_total_per_candidate"][args.fidelity])
    contract.fixed["max_workers"] = max(1, args.workers // max(1, args.parallel))
    contract.fixed["total_mem_gb"] = float(contract.fixed["total_mem_gb"]) / max(1, args.parallel)
    contract.fixed["sample_timeout_s"] = args.timeout
    contract.fixed["sample_stall_timeout_s"] = args.stall_timeout
    objective = O.get(args.objective)
    resolver = resolver_for(space)
    local = threading.local()

    def led():
        if getattr(local, "l", None) is None:
            local.l = Ledger(args.ledger)
        return local.l

    def run(label, point):
        t0 = time.time()
        try:
            r = evaluate(contract, candidate_payload(point, space), fidelity=args.fidelity,
                         seed_bank_id=args.seed_bank, ledger=led(), verbose=False,
                         resolver=resolver)
        except (ContractError, S.GateError) as exc:
            return label, {"status": "rejected", "reason": str(exc)}, None
        if not r.is_observation:
            return label, {"status": r.status, "reason": r.failure_reason}, None
        v = objective(r)
        return label, {"status": r.status, "value": v.value, "se": v.se,
                       "total_qps": r.total_qps, "trial_id": r.trial_id,
                       "relative_se": v.detail.get("relative_se"),
                       "wall_s": round(time.time() - t0, 1),
                       "cached": r.cached}, r

    print(f"Confirmation: {len(points)} candidate(s) at fidelity {args.fidelity} "
          f"({events:,} events each), HELD-OUT seed bank {args.seed_bank}, "
          f"{args.parallel} x {contract.fixed['max_workers']} workers")
    results, trials = {}, {}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(run, k, v) for k, v in points.items()]
        for fut in as_completed(futures):
            label, rec, trial = fut.result()
            results[label] = rec
            if trial is not None:
                trials[label] = trial
            if rec.get("value") is not None:
                print(f"  {label:26s} {rec['value']:.4e} +-{100 * (rec['relative_se'] or 0):.1f}%"
                      f"  ({rec['total_qps']:.0f} QPs, {rec['wall_s']:.0f}s"
                      f"{', cached' if rec.get('cached') else ''})")
            else:
                print(f"  {label:26s} {rec['status']}: {str(rec.get('reason'))[:80]}")

    base = results.get("baseline", {}).get("value")
    print(f"\n{'candidate':26s} {'objective':>11s} {'+-':>7s} {'vs baseline':>12s} "
          f"{'paired z':>9s} {'sites won':>10s}")
    if base:
        print(f"{'baseline':26s} {base:11.4e} "
              f"{100 * (results['baseline']['relative_se'] or 0):6.1f}% {'--':>12s} "
              f"{'--':>9s} {'--':>10s}")
    for label, rec in sorted(results.items(), key=lambda kv: kv[1].get("value") or 9e9):
        if label == "baseline" or rec.get("value") is None:
            continue
        line = f"{label:26s} {rec['value']:11.4e} {100 * (rec['relative_se'] or 0):6.1f}%"
        if base and label in trials and "baseline" in trials:
            paired = O.paired_difference(trials[label], trials["baseline"])
            rec["paired"] = paired
            line += (f" {100 * paired['relative']:11.1f}% "
                     f"{(paired['z_paired'] or float('nan')):9.1f} "
                     f"{paired['n_sites_favouring_a']:6d}/{paired['n_sites']:<3d}")
        print(line)

    out = {"fidelity": args.fidelity, "events": events, "seed_bank": args.seed_bank,
           "objective": args.objective, "results": results,
           "points": {k: {kk: (list(vv) if isinstance(vv, list) else vv)
                          for kk, vv in v.items()} for k, v in points.items()}}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(f"\nWritten: {args.out}")
    print("Reminder: this is simulated junction-QP yield under one scenario set and "
          "one interface model. It is not a logical-error-rate claim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
