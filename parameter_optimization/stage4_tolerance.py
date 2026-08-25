#!/usr/bin/env python3
"""One-factor-at-a-time tolerance scan around a Stage 4 optimum.

    python stage4_tolerance.py --manifest runs/.../campaign_bo_gp_*.json --delta 0.1

Answers the two questions a fabricator and the projection step both need:

1. **Which specifications actually matter.** For each active variable, move it
   by +-delta of its box width (in the transformed coordinate, so a log-scaled
   variable moves by a factor) and measure how far the objective moves. A
   variable whose +-delta swing stays inside the tier's ~5% stochastic noise is
   one the fabrication does not have to control tightly -- and, equally
   important, one whose optimized value should not be quoted as if it were
   resolved.

2. **How to weight the projection metric.** `stage4_project_material.py`
   compares real materials on physical features; weighting them equally would
   let an irrelevant property outvote a decisive one. The weights written here
   are proportional to the measured local sensitivity, so the metric cares about
   what the objective cares about.

The scan is deliberately local and one-factor: it is a tolerance statement about
a specific optimum, not a global sensitivity analysis. Interactions are not
resolved by it and it does not claim to be.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from stage3_contract import load_contract, ContractError         # noqa: E402
from stage3_ledger import Ledger                                 # noqa: E402
from stage3_trial_runner import evaluate                         # noqa: E402
import stage4_space as S                                         # noqa: E402
import stage4_objectives as O                                    # noqa: E402
from stage4_optimize import candidate_payload, resolver_for, build_space  # noqa: E402


def _refuse_if_frozen(ledger_path):
    """Exit before touching anything if the ledger is closed to new writers."""
    from stage3_ledger import freeze_reason, freeze_path
    reason = freeze_reason(ledger_path)
    if reason:
        sys.exit(f"REFUSING TO RUN: {os.path.abspath(ledger_path)} is frozen.\n\n"
                 f"{reason}\n\nRemove {freeze_path(ledger_path)} deliberately, "
                 f"after snapshotting, to lift this.")


# Which comparison feature(s) each decision variable acts through. Used only to
# turn variable sensitivities into projection weights; stated explicitly because
# the mapping is a modelling choice, not a derivation. `null` means the variable
# is a setting rather than a material property, so it has no projection weight.
VARIABLE_TO_FEATURES = {
    "topfilm_vsound": ["topfilm_vsound_m_s", "topfilm_impedance"],
    "topfilm_gap": ["topfilm_gap_eV"],
    "topfilm_ph_lifetime": ["topfilm_ph_lifetime_ns"],
    "topfilm_density": ["topfilm_impedance"],
    "bot_vsound": ["bot_vsound_m_s", "bot_impedance"],
    "bot_ph_lifetime": ["bot_ph_lifetime_ns"],
    "bot_density": ["bot_impedance"],
    "bot_gap_thres": [],
    "sub_c11": ["sub_vsound_m_s", "sub_vtrans_m_s", "sub_anisotropy", "sub_impedance"],
    "sub_c12": ["sub_vsound_m_s", "sub_vtrans_m_s", "sub_anisotropy", "sub_impedance"],
    "sub_c44": ["sub_vsound_m_s", "sub_vtrans_m_s", "sub_anisotropy", "sub_impedance"],
    "sub_scat": ["sub_scat"],
    "sub_decay": ["sub_decay"],
    "sub_decayTT": ["sub_decayTT"],
    "temperature": [],
    "lattice_deg": [],
    "sub_lattice_a": [],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--point", default=None, help="explicit property vector as JSON")
    ap.add_argument("--contract", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--delta", type=float, default=0.1,
                    help="fraction of the box width, in transformed coordinates")
    ap.add_argument("--events", type=int, default=4000000)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--seed-bank", type=int, default=0)
    ap.add_argument("--tag", default="tolerance")
    ap.add_argument("--objective", default="total_qps_per_primary")
    ap.add_argument("--improvement-threshold", type=float, default=0.02,
                    help="a paired improvement smaller than this is 'no better' "
                         "for the convergence rule (default 2%%)")
    ap.add_argument("--bound-margin", type=float, default=0.02,
                    help="a variable within this fraction of the box width from a "
                         "wall counts as an active bound (default 2%%)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "stage4_tolerance.json"))
    args = ap.parse_args()
    _refuse_if_frozen(args.ledger)

    contract = load_contract(args.contract)
    space = build_space(contract)
    if args.manifest:
        with open(args.manifest) as handle:
            man = json.load(handle)
        centre = space.complete(man["best"]["point"])
    elif args.point:
        centre = space.complete(json.loads(args.point))
    else:
        centre = space.baseline_point()

    contract.campaign_id = f"{contract.campaign_id}_{args.tag}"
    fid = contract.decision["fidelity"]["value"]
    contract.decision["fidelity"]["events_total_per_candidate"][fid] = args.events
    contract.fixed["max_workers"] = max(1, args.workers // max(1, args.parallel))
    contract.fixed["total_mem_gb"] = float(contract.fixed["total_mem_gb"]) / max(1, args.parallel)
    objective = O.get(args.objective)
    resolver = resolver_for(space)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    local = threading.local()

    def led():
        if getattr(local, "l", None) is None:
            local.l = Ledger(args.ledger)
        return local.l

    def run(label, point):
        try:
            r = evaluate(contract, candidate_payload(point, space), fidelity=fid,
                         seed_bank_id=args.seed_bank, ledger=led(), verbose=False,
                         resolver=resolver)
        except (ContractError, S.GateError) as exc:
            return label, {"status": "rejected", "reason": str(exc)}, None
        if not r.is_observation:
            return label, {"status": r.status, "reason": r.failure_reason}, None
        v = objective(r)
        return label, {"status": r.status, "value": v.value, "se": v.se,
                       "raw": v.raw, "trial_id": r.trial_id}, r

    jobs = {"centre": centre}
    u0 = space.to_unit(centre)
    for i, var in enumerate(space.variables):
        for sign, tag in ((+1, "plus"), (-1, "minus")):
            u = u0.copy()
            u[i] = float(np.clip(u0[i] + sign * args.delta, 0.0, 1.0))
            if abs(u[i] - u0[i]) < 1e-9:
                continue                       # already at that wall
            p = space.from_unit(u, space.miller_index(centre), base=centre)
            ok, why = S.precheck_cheap(p, space)
            if not ok:
                print(f"  skip {var.name} {tag}: {why}")
                continue
            jobs[f"{var.name}:{tag}"] = p

    print(f"Tolerance scan around {'the manifest optimum' if args.manifest else 'the given point'}: "
          f"{len(jobs)} evaluations at {args.events:,} events, delta = {args.delta} of the box")
    t0 = time.time()
    results, trials = {}, {}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(run, k, v) for k, v in jobs.items()]
        for fut in as_completed(futures):
            label, rec, trial = fut.result()
            results[label] = rec
            if trial is not None and getattr(trial, "blocks", None):
                trials[label] = trial
            if rec.get("value") is not None:
                print(f"  {label:26s} {rec['value']:.4e}")
            else:
                print(f"  {label:26s} {rec['status']}: {str(rec.get('reason'))[:80]}")

    centre_v = results.get("centre", {}).get("value")
    if centre_v is None:
        sys.exit("the centre point failed; nothing to compare against")
    centre_se = results["centre"].get("se") or 0.0

    print(f"\nCentre: {centre_v:.4e} +- {centre_se:.1e} "
          f"({100 * centre_se / centre_v:.1f}%)")
    # Every move is now compared to the centre with a PAIRED, SITE-MATCHED
    # difference (audit P2). Comparing two noisy single values against the
    # centre's own error, as this scan used to, ignores that the 16 sites are
    # common to both trials and cancel: it inflates the error of a real
    # improvement and understates the error of the difference itself.
    centre_trial = trials.get("centre")
    paired = {}
    if centre_trial is not None:
        for label, trial in trials.items():
            if label == "centre":
                continue
            try:
                paired[label] = O.paired_difference(trial, centre_trial)
            except ValueError as exc:                        # noqa: PERF203
                paired[label] = {"error": str(exc)}

    def _paired(label):
        d = paired.get(label) or {}
        if "relative" not in d or d.get("relative") is None:
            return None, None, None
        rel = float(d["relative"])
        se_rel = (abs(d["se_delta_total_qps"] / d["delta_total_qps"] * rel)
                  if d.get("se_delta_total_qps") and d.get("delta_total_qps")
                  else None)
        return rel, se_rel, d.get("z_paired")

    print(f"\n{'variable':22s} {'-delta':>11s} {'+delta':>11s} {'max |dJ|/J':>11s} "
          f"{'best paired':>12s} {'z':>7s}  verdict")
    sens = {}
    improving = []
    for var in space.variables:
        lo = results.get(f"{var.name}:minus", {}).get("value")
        hi = results.get(f"{var.name}:plus", {}).get("value")
        swings = [abs(v - centre_v) / centre_v for v in (lo, hi) if v is not None]
        s = max(swings) if swings else None
        sens[var.name] = s
        noise = 2 * (centre_se / centre_v if centre_v else 0.05)
        verdict = ("--" if s is None else
                   ("below noise: not resolved at this fidelity" if s < noise
                    else "RESOLVED" if s > 2 * noise else "marginal"))
        best_rel, best_z, best_side = None, None, None
        for tag in ("minus", "plus"):
            rel, _, z = _paired(f"{var.name}:{tag}")
            if rel is None:
                continue
            if best_rel is None or rel < best_rel:
                best_rel, best_z, best_side = rel, z, tag
        if best_rel is not None and best_rel < 0:
            improving.append({"variable": var.name, "side": best_side,
                              "paired_relative": best_rel, "z_paired": best_z})
        print(f"{var.name:22s} {(lo if lo else float('nan')):11.4e} "
              f"{(hi if hi else float('nan')):11.4e} "
              f"{(s if s is not None else float('nan')):10.1%} "
              f"{(best_rel if best_rel is not None else float('nan')):11.1%} "
              f"{(best_z if best_z is not None else float('nan')):7.1f}  {verdict}")

    # ---------------------------------------------------------------------
    # The declared local-convergence rule (audit P2). A point is a "locally
    # converged optimum within the declared box" only if ALL of these hold.
    # This scan can test the first three; the last two are campaign-level and
    # are reported as untested rather than quietly assumed.
    # ---------------------------------------------------------------------
    material = [m for m in improving
                if m["paired_relative"] <= -args.improvement_threshold
                and (m["z_paired"] is None or m["z_paired"] <= -2.0)]
    at_bound = []
    for i, var in enumerate(space.variables):
        u = float(u0[i])
        if u <= args.bound_margin or u >= 1.0 - args.bound_margin:
            at_bound.append({"variable": var.name,
                             "value": float(centre[var.name]),
                             "at": "low" if u <= args.bound_margin else "high",
                             "bound": var.low if u <= args.bound_margin else var.high})
    convergence = {
        "rule": ("no tested single-variable move improves the PAIRED objective by "
                 "more than the threshold AND by more than two paired standard "
                 "errors; no active bound is approached without a documented "
                 "physical reason; stability across model updates and optimizer "
                 "seeds is a separate, campaign-level check this scan cannot make"),
        "improvement_threshold": args.improvement_threshold,
        "paired_z_threshold": -2.0,
        "bound_margin_of_box_width": args.bound_margin,
        "improving_moves": sorted(improving, key=lambda m: m["paired_relative"]),
        "materially_improving_moves": sorted(material,
                                             key=lambda m: m["paired_relative"]),
        "variables_at_or_near_a_bound": at_bound,
        "locally_converged": (not material and not at_bound),
        "untested_criteria": [
            "two successive model updates fail to improve the held-out incumbent",
            "the result is stable across at least two optimizer seeds",
        ],
    }
    print("\nLocal convergence (declared rule, audit P2):")
    if material:
        print(f"  NOT converged: {len(material)} move(s) improve the paired "
              f"objective by more than {args.improvement_threshold:.0%} at |z| > 2:")
        for m in material[:6]:
            print(f"    {m['variable']:22s} {m['side']:5s} "
                  f"{m['paired_relative']:+.1%} (z = {m['z_paired']:.1f})")
    else:
        print(f"  no single-variable move improves the paired objective by more "
              f"than {args.improvement_threshold:.0%} at |z| > 2")
    if at_bound:
        print(f"  NOT converged: {len(at_bound)} variable(s) sit within "
              f"{args.bound_margin:.0%} of a box wall -- widen the box or record "
              f"the physical reason for the wall:")
        for b in at_bound:
            print(f"    {b['variable']:22s} {b['value']:.4g} at its {b['at']} "
                  f"bound {b['bound']:.4g}")
    print(f"  VERDICT: {'locally converged within the declared box' if convergence['locally_converged'] else 'BEST POINT FOUND, not a converged optimum'}")
    print("  (untested here: " + "; ".join(convergence["untested_criteria"]) + ")")

    # Projection weights: sensitivity per feature, averaged over the variables
    # that act through it, normalised to a mean of 1.
    feature_w = {}
    for var, s in sens.items():
        if s is None:
            continue
        for f in VARIABLE_TO_FEATURES.get(var, []):
            feature_w.setdefault(f, []).append(s)
    weights = {f: float(np.mean(v)) for f, v in feature_w.items()}
    if weights:
        mean = float(np.mean(list(weights.values()))) or 1.0
        weights = {f: round(w / mean, 4) for f, w in weights.items()}
    print("\nSuggested projection weights (pass to stage4_project_material.py --weights):")
    print("  " + json.dumps(weights))

    out = {"centre_point": {k: (list(v) if isinstance(v, list) else v)
                            for k, v in centre.items()},
           "centre_value": centre_v, "centre_se": centre_se,
           "delta": args.delta, "events": args.events,
           "results": results, "sensitivity": sens, "projection_weights": weights,
           "paired_differences": paired, "convergence": convergence,
           "elapsed_s": round(time.time() - t0, 1)}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
