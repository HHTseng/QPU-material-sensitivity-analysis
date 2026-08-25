#!/usr/bin/env python3
"""Assemble the combined 1e8 table from confirmation JSONs. NO simulation.

    python stage4_assemble_xl.py            # writes results/stage4_confirmation_XL.json
    python stage4_assemble_xl.py --ladder   # then runs the four-tier comparison

Replaces steps 4 and 5 of `run_stage4_xl.sh`. Those steps re-invoked
`stage4_confirm.py`, which was free only while the cache was warm: every
candidate was expected to be a cache hit and the whole step took seconds.

That assumption no longer holds. The audit's fixes changed four
`CODE_IDENTITY_FILES`, so a new process has a different code fingerprint and
would miss every cache entry -- re-simulating five candidates at 3.2e9 events
each, days of compute, for numbers that already exist and whose scientific
interpretation has not changed. It would also open a new `Ledger`, running the
schema migration underneath a campaign that is still finishing under its launch
identity.

So this reads the per-pair JSONs the chain already wrote and merges them. It
opens no ledger, starts no Geant4 process, and needs no contract. Rows that a
known finding invalidated are carried through with their verdict attached rather
than silently dropped -- the table is evidence, and a missing row is harder to
audit than a labelled bad one.
"""

import argparse
import glob
import json
import os
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")


def invalid_trials(registry_path):
    """{trial_id: (finding, reason)} for every row a finding invalidated."""
    if not os.path.isfile(registry_path):
        return {}
    with open(registry_path) as handle:
        reg = yaml.safe_load(handle) or {}
    out = {}
    for key, finding in (reg.get("findings") or {}).items():
        for row in finding.get("trials") or []:
            if row.get("verdict") == "invalid":
                out[row["trial_id"]] = (
                    key,
                    f"simulated as {row.get('simulated_carrier')} instead of "
                    f"{row.get('intended_carrier')}")
    return out


def merge(paths, registry_path):
    merged, provenance, conflicts = {}, {}, []
    events = seed_bank = objective = None
    for path in paths:
        with open(path) as handle:
            doc = json.load(handle)
        events = events or doc.get("events")
        seed_bank = seed_bank if seed_bank is not None else doc.get("seed_bank")
        objective = objective or doc.get("objective")
        if doc.get("events") != events:
            conflicts.append(f"{os.path.basename(path)}: events {doc.get('events')} "
                             f"!= {events}")
        if doc.get("seed_bank") != seed_bank:
            conflicts.append(f"{os.path.basename(path)}: seed bank "
                             f"{doc.get('seed_bank')} != {seed_bank}")
        for label, rec in (doc.get("results") or {}).items():
            if rec.get("value") is None:
                continue
            prev = merged.get(label)
            if prev and prev.get("trial_id") != rec.get("trial_id"):
                conflicts.append(
                    f"{label}: two different trials ({prev.get('trial_id')} and "
                    f"{rec.get('trial_id')}) -- keeping the first")
                continue
            merged.setdefault(label, rec)
            provenance.setdefault(label, os.path.basename(path))

    bad = invalid_trials(registry_path)
    for label, rec in merged.items():
        if rec.get("trial_id") in bad:
            finding, why = bad[rec["trial_id"]]
            rec["validity"] = "invalid"
            rec["invalidated_by"] = finding
            rec["invalidation_reason"] = why
        else:
            rec["validity"] = "valid"
    return merged, provenance, conflicts, events, seed_bank, objective


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", nargs="*", default=None,
                    help="confirmation JSONs to merge (default: every "
                         "results/stage4_confirmation_XL_*.json)")
    ap.add_argument("--registry",
                    default=os.path.join(HERE, "stage4_invalidations.yaml"))
    ap.add_argument("--out", default=os.path.join(RESULTS, "stage4_confirmation_XL.json"))
    ap.add_argument("--ladder", action="store_true",
                    help="also run stage4_compare_fidelity.py over all four tiers")
    args = ap.parse_args()

    paths = args.inputs or sorted(
        p for p in glob.glob(os.path.join(RESULTS, "stage4_confirmation_XL_*.json"))
        if os.path.abspath(p) != os.path.abspath(args.out))
    if not paths:
        sys.exit("no XL confirmation JSONs found; nothing to assemble")

    merged, provenance, conflicts, events, seed_bank, objective = merge(
        paths, args.registry)
    print(f"Assembling {len(merged)} candidate(s) from {len(paths)} file(s); "
          f"{events:,} events each, held-out seed bank {seed_bank}")
    for c in conflicts:
        print(f"  CONFLICT: {c}")

    base = next((r["value"] for k, r in merged.items()
                 if k == "baseline" and r.get("validity") == "valid"), None)
    print(f"\n  {'candidate':28s} {'objective':>12s} {'vs baseline':>12s}  validity")
    for label, rec in sorted(merged.items(), key=lambda kv: kv[1]["value"]):
        vs = (f"{100 * (rec['value'] - base) / base:+11.1f}%" if base else f"{'--':>12s}")
        flag = "" if rec["validity"] == "valid" else f"  <-- {rec['invalidated_by']}"
        print(f"  {label:28s} {rec['value']:12.4e} {vs}  {rec['validity']}{flag}")

    out = {"fidelity": "L", "events": events, "seed_bank": seed_bank,
           "objective": objective, "results": merged,
           "assembled_from": {k: provenance[k] for k in sorted(provenance)},
           "conflicts": conflicts,
           "note": "Assembled from existing confirmation JSONs by "
                   "stage4_assemble_xl.py. No simulation was run and no ledger "
                   "was opened. Rows a known finding invalidated carry "
                   "validity='invalid' and must not be quoted."}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(f"\nWritten: {args.out}")

    if args.ladder:
        tiers = [os.path.join(RESULTS, f"stage4_confirmation_{t}.json")
                 for t in ("S", "M", "L")] + [args.out]
        missing = [t for t in tiers if not os.path.isfile(t)]
        if missing:
            print(f"  skipping the ladder: missing {missing}")
            return 0
        cmd = [sys.executable, os.path.join(HERE, "stage4_compare_fidelity.py")] \
            + tiers + ["--out", os.path.join(RESULTS, "stage4_fidelity_comparison.json")]
        print("\n" + " ".join(cmd))
        return subprocess.call(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
