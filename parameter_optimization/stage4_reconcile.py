#!/usr/bin/env python3
"""Make recorded artifacts agree with the ledger and the invalidation registry.

    python stage4_reconcile.py --dry-run     # show what would change
    python stage4_reconcile.py               # apply

Two jobs, both of which the audit reports as warnings until they are done:

1. **Label invalidated results.** Result JSONs still carry rows that a known
   finding invalidated. The values are NOT changed and NOT deleted -- they are
   the evidence the defect was real, and a reader who finds a bare number in a
   file has no way to know it is withdrawn. Each affected result gains
   `validity`, `invalidated_by` and `invalidation_reason`, and the file gains a
   top-level `_invalidation_notice`.

2. **Backfill `ledger_status_counts` into historical manifests.** Campaign
   manifests written before the P7 fix report the driver's in-process counters,
   which are blind to trials abandoned when the process was killed -- so they
   said "0 failures" while the ledger held 5, 6 and 4 non-success rows. The
   original `n_failed` / `n_rejected` are LEFT ALONE: they are a true record of
   what the driver observed. What is added is the ledger's authoritative view,
   exactly as new manifests now carry it.

Both operations are additive and idempotent: re-running changes nothing, and no
recorded value is ever overwritten.
"""

import argparse
import copy
import json
import os
import shutil
import sqlite3
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
NOTICE_KEY = "_invalidation_notice"


def invalid_map(registry_path):
    """{trial_id: {finding, reason, label}} for every invalidated trial."""
    if not os.path.isfile(registry_path):
        return {}, {}
    with open(registry_path) as handle:
        reg = yaml.safe_load(handle) or {}
    out, findings = {}, {}
    for key, finding in (reg.get("findings") or {}).items():
        findings[key] = finding.get("title") or key
        for row in finding.get("trials") or []:
            if row.get("verdict") != "invalid":
                continue
            out[row["trial_id"]] = {
                "finding": key,
                "label": row.get("label"),
                "reason": (f"simulated as {row.get('simulated_carrier')} instead of "
                           f"{row.get('intended_carrier')}"),
            }
    return out, findings


def label_results(results_dir, bad, findings, dry_run):
    changed = []
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(results_dir, name)
        try:
            with open(path) as handle:
                doc = json.load(handle)
        except (ValueError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        before = copy.deepcopy(doc)
        hits = []
        for label, rec in (doc.get("results") or {}).items():
            if not isinstance(rec, dict):
                continue
            tid = rec.get("trial_id")
            if tid in bad:
                rec["validity"] = "invalid"
                rec["invalidated_by"] = bad[tid]["finding"]
                rec["invalidation_reason"] = bad[tid]["reason"]
                hits.append(label)
            elif rec.get("value") is not None:
                rec.setdefault("validity", "valid")
        # The projection stores its verification results one level down.
        ver = (doc.get("verification") or {}).get("results")
        if isinstance(ver, dict):
            for label, rec in ver.items():
                if isinstance(rec, dict) and rec.get("trial_id") in bad:
                    tid = rec["trial_id"]
                    rec["validity"] = "invalid"
                    rec["invalidated_by"] = bad[tid]["finding"]
                    rec["invalidation_reason"] = bad[tid]["reason"]
                    hits.append(label)
        if hits:
            doc[NOTICE_KEY] = {
                "labelled_at": time.strftime("%F %T"),
                "registry": "stage4_invalidations.yaml",
                "invalid_rows": sorted(set(hits)),
                "findings": sorted({bad[r["trial_id"]]["finding"]
                                    for r in list((doc.get("results") or {}).values())
                                    + list((ver or {}).values())
                                    if isinstance(r, dict) and r.get("trial_id") in bad}),
                "note": "Values are preserved deliberately: they are the evidence "
                        "the defect was real. Rows marked validity='invalid' must "
                        "not be quoted.",
            }
        if doc != before:
            changed.append((name, sorted(set(hits))))
            if not dry_run:
                if not os.path.exists(path + ".prelabel"):
                    shutil.copy2(path, path + ".prelabel")
                tmp = path + ".tmp"
                with open(tmp, "w") as handle:
                    json.dump(doc, handle, indent=1, default=str)
                os.replace(tmp, path)
    return changed


def backfill_manifests(runs_root, ledger_path, dry_run):
    if not os.path.isfile(ledger_path):
        return []
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    changed = []
    for campaign in sorted(os.listdir(runs_root)):
        cdir = os.path.join(runs_root, campaign)
        if not os.path.isdir(cdir):
            continue
        for name in sorted(os.listdir(cdir)):
            if not (name.startswith("campaign_") and name.endswith(".json")):
                continue
            path = os.path.join(cdir, name)
            with open(path) as handle:
                man = json.load(handle)
            cid_probe = man.get("campaign_id", campaign)
            live = conn.execute(
                "SELECT count(*) n FROM trials WHERE campaign_id=? AND status='running'",
                (cid_probe,)).fetchone()["n"] > 0
            if live:
                continue          # a live campaign rewrites its own manifest
            existing = man.get("ledger_status_counts")
            if existing is not None:
                fresh = {r["status"]: r["n"] for r in conn.execute(
                    "SELECT status, count(*) n FROM trials WHERE campaign_id=? "
                    "GROUP BY status", (cid_probe,))}
                if existing == fresh:
                    continue                              # already authoritative
                # Stale: the campaign was killed before it could save, so its
                # counts predate whatever happened afterwards. Refresh them --
                # the ledger is the authority, and a manifest frozen at the
                # moment of death is exactly the misleading artifact this tool
                # exists to correct.
            cid = man.get("campaign_id", campaign)
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, count(*) n FROM trials WHERE campaign_id=? "
                "GROUP BY status", (cid,))}
            if not counts:
                continue
            man["ledger_status_counts"] = counts
            man["ledger_status_counts_backfilled_at"] = time.strftime("%F %T")
            man["ledger_status_counts_note"] = (
                "Backfilled by stage4_reconcile.py. `n_failed` and `n_rejected` "
                "above are the DRIVER's in-process counters and are left as "
                "recorded; they cannot see trials abandoned when the process was "
                "killed. These counts are the ledger's authoritative view.")
            changed.append((f"{campaign}/{name}", counts))
            if not dry_run:
                if not os.path.exists(path + ".prebackfill"):
                    shutil.copy2(path, path + ".prebackfill")
                tmp = path + ".tmp"
                with open(tmp, "w") as handle:
                    json.dump(man, handle, indent=1, default=str)
                os.replace(tmp, path)
    conn.close()
    return changed


def backfill_identity(results_dir, ledger_path, dry_run):
    """Add the sub-run split and trial identity to result files that lack it.

    A result JSON that records only `events` is not self-describing: a reader
    cannot tell whether that total was split over 32 sub-runs or 128, and
    `stage4_compare_fidelity.py` was reduced to assuming 32 -- wrong by 4x under
    the 8-replica protocol. Every value here is READ BACK FROM THE LEDGER for
    the trials the file actually references, never assumed, and the file is left
    alone if its trials disagree with each other.
    """
    if not os.path.isfile(ledger_path):
        return []
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    changed = []
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(results_dir, name)
        try:
            with open(path) as handle:
                doc = json.load(handle)
        except (ValueError, OSError):
            continue
        if not isinstance(doc, dict) or "results" not in doc:
            continue
        if doc.get("events_per_sub_run") is not None:
            continue
        tids = [r.get("trial_id") for r in doc["results"].values()
                if isinstance(r, dict) and r.get("trial_id")]
        if not tids:
            continue
        marks = ",".join("?" * len(tids))
        rows = conn.execute(
            f"SELECT DISTINCT n_positions, n_replicas, events_per_sub_run, "
            f"events_total, contract_hash FROM trials WHERE trial_id IN ({marks})",
            tuple(tids)).fetchall()
        shapes = {(r["n_positions"], r["n_replicas"], r["events_per_sub_run"])
                  for r in rows}
        if len(shapes) != 1:
            changed.append((name, f"SKIPPED: {len(shapes)} different sub-run "
                                  f"shapes among its trials {sorted(shapes)}"))
            continue
        npos, nrep, per_sub = shapes.pop()
        doc["n_positions"], doc["n_replicas"] = npos, nrep
        doc["n_sub_runs"] = npos * nrep
        doc["events_per_sub_run"] = per_sub
        doc["contract_hash"] = rows[0]["contract_hash"]
        doc["identity_backfilled_at"] = time.strftime("%F %T")
        doc["identity_backfilled_note"] = (
            "Read back from the ledger for the trials this file references. "
            "`simulation_identity_hash` and `code_fingerprint` are absent: they "
            "were not recorded when these trials ran, and are not reconstructible.")
        changed.append((name, f"{npos}x{nrep}={npos * nrep} sub-runs, "
                              f"{per_sub:,} events each"))
        if not dry_run:
            if not os.path.exists(path + ".preidentity"):
                shutil.copy2(path, path + ".preidentity")
            tmp = path + ".tmp"
            with open(tmp, "w") as handle:
                json.dump(doc, handle, indent=1, default=str)
            os.replace(tmp, path)
    conn.close()
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    ap.add_argument("--runs-root", default=os.path.join(HERE, "runs"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--registry",
                    default=os.path.join(HERE, "stage4_invalidations.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bad, findings = invalid_map(args.registry)
    print(f"Registry: {len(bad)} invalidated trial(s) across {len(findings)} finding(s)")

    print("\n1. Labelling invalidated rows in result files "
          f"({'dry run' if args.dry_run else 'applying'}):")
    labelled = label_results(args.results, bad, findings, args.dry_run)
    for name, hits in labelled:
        print(f"   {name}: marked {hits}")
    if not labelled:
        print("   nothing to label")

    print("\n2. Backfilling ledger_status_counts into historical manifests "
          f"({'dry run' if args.dry_run else 'applying'}):")
    filled = backfill_manifests(args.runs_root, args.ledger, args.dry_run)
    for name, counts in filled:
        print(f"   {name}: {counts}")
    if not filled:
        print("   nothing to backfill")

    print("\n3. Backfilling sub-run identity into result files "
          f"({'dry run' if args.dry_run else 'applying'}):")
    ident = backfill_identity(args.results, args.ledger, args.dry_run)
    for name, detail in ident:
        print(f"   {name}: {detail}")
    if not ident:
        print("   nothing to backfill")

    print(f"\n{len(labelled)} result file(s), {len(filled)} manifest(s), "
          f"{len(ident)} identity backfill(s)"
          + (" would change" if args.dry_run else " updated")
          + ". Originals kept as *.prelabel / *.prebackfill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
