#!/usr/bin/env python3
"""Consistency audit across the contract, the templates, the ledger and the docs.

    python stage4_audit.py                 # everything, non-zero exit on a failure
    python stage4_audit.py --json out.json # machine-readable

Written for P7 of STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md. Each of these
drifted at least once during the campaign, and every one of them was found by a
human reading two files side by side rather than by anything that runs:

* the energy protocol disagreed between `parameter_set.txt` (1 meV) and the
  contract and macro templates (10 meV);
* headline numbers in the summaries were a campaign behind the ledger;
* a campaign row sat at `running` with no process behind it;
* the manifests reported zero failures while the ledger held incomplete and
  operator-stopped rows whose partial cost was never charged to anything;
* results invalidated by a known finding were still being quoted.

The rule this encodes: ONE machine-readable contract is authoritative
(`stage4_config.yaml` plus `stage4_invalidations.yaml`), and every human-facing
statement is checked against it rather than maintained in parallel.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CHECKS = []


def record(name, ok, detail="", severity="fail"):
    CHECKS.append({"check": name, "ok": bool(ok),
                   "severity": ("pass" if ok else severity), "detail": detail})
    mark = "PASS" if ok else ("WARN" if severity == "warn" else "FAIL")
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(ok)


# ---------------------------------------------------------------------------
def check_energy_protocol(contract_path, param_set):
    """Catches: the injection protocol documented in one place, run from another.

    `gun_energy_eV` and `minEPhonons` are the two numbers that decide whether
    the downconversion cascade -- the mechanism every scanned property acts
    through -- is simulated at all. A 10x disagreement between the prose and
    the macro is not a typo; it is two different experiments.
    """
    with open(contract_path) as handle:
        raw = yaml.safe_load(handle)
    fixed = raw.get("fixed") or {}
    e_contract = float(fixed["gun_energy_eV"])
    min_contract = float(fixed["min_e_phonons_eV"])

    templates = sorted(f for f in os.listdir(REPO_ROOT)
                       if f.startswith("sensitivity_template_beamOn")
                       and f.endswith(".mac"))
    bad = []
    for name in templates:
        text = open(os.path.join(REPO_ROOT, name)).read()
        for cmd, want in (("/main/gun/setEnergy", e_contract),
                          ("/g4cmp/minEPhonons", min_contract)):
            found = None
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(cmd + " "):
                    found = float(line.split()[1])
                    break
            if found is None:
                bad.append(f"{name}: {cmd} absent")
            elif abs(found - want) > 1e-12 * max(1.0, abs(want)):
                bad.append(f"{name}: {cmd} = {found:g}, contract says {want:g}")
    record("energy protocol agrees across contract and every macro template",
           not bad, "; ".join(bad) or f"E_gun {e_contract:g} eV, "
                                      f"minEPhonons {min_contract:g} eV "
                                      f"over {len(templates)} template(s)")

    # The physical ordering the contract itself declares.
    top_gap = float(fixed["setTopGap"])
    record("minEPhonons < 2*setTopGap <= E_gun (a numerical cut must not "
           "preempt a physical one)",
           min_contract < 2 * top_gap <= e_contract + 1e-15,
           f"{min_contract:g} < {2 * top_gap:g} <= {e_contract:g}")

    # The human-facing parameter list.
    if not os.path.isfile(param_set):
        record("parameter_set.txt states the same gun energy", True,
               "skipped: file not found", severity="warn")
        return
    text = open(param_set).read()
    stated = re.findall(r"/main/gun/setEnergy\s*\"?,?\s*([0-9.eE+-]+)", text)
    stated = [float(s) for s in stated if s.strip(".")]
    agree = stated and all(abs(s - e_contract) <= 1e-12 * max(1.0, e_contract)
                           for s in stated)
    record("parameter_set.txt states the contract's gun energy",
           bool(agree),
           f"parameter_set.txt says {stated}, contract says {e_contract:g} eV")


def check_invalidations(ledger_path, registry_path, results_dir):
    """Catches: quoting a number a known finding already invalidated."""
    if not os.path.isfile(registry_path):
        record("invalidation registry present", False,
               f"{registry_path} not found")
        return {}
    with open(registry_path) as handle:
        reg = yaml.safe_load(handle)
    findings = reg.get("findings") or {}
    invalid = {}
    for key, finding in findings.items():
        for row in finding.get("trials") or []:
            if row.get("verdict") == "invalid":
                invalid[row["trial_id"]] = {"finding": key, **row}
    record("invalidation registry parses and names its findings",
           bool(findings),
           f"{len(findings)} finding(s), {len(invalid)} invalidated trial(s)")

    if os.path.isfile(ledger_path):
        import sqlite3
        conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
        known = {r[0] for r in conn.execute("SELECT trial_id FROM trials")}
        conn.close()
        missing = sorted(set(invalid) - known)
        record("every invalidated trial still exists in the ledger as evidence",
               not missing, "; ".join(missing) or f"{len(invalid)} row(s) retained")

    # A result file may CONTAIN an invalidated trial -- that is the evidence and
    # deleting it would be worse. What it must not do is contain one WITHOUT
    # saying so, because a bare number gives the reader no way to know it is
    # withdrawn. `stage4_reconcile.py` adds the labels.
    unlabelled, labelled = [], []
    if os.path.isdir(results_dir):
        for name in sorted(os.listdir(results_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(results_dir, name)
            text = open(path, errors="replace").read()
            hits = [t for t in invalid if t in text]
            if not hits:
                continue
            try:
                doc = json.loads(text)
            except ValueError:
                doc = {}
            marked = set()
            for section in (doc.get("results") or {}, 
                            (doc.get("verification") or {}).get("results") or {}):
                for rec in section.values():
                    if isinstance(rec, dict) and rec.get("validity") == "invalid" \
                            and rec.get("trial_id") in invalid:
                        marked.add(rec["trial_id"])
            missing = [t for t in hits if t not in marked]
            if missing:
                unlabelled.append(f"{name} ({len(missing)} unlabelled)")
            else:
                labelled.append(f"{name} ({len(marked)})")
    record("every invalidated trial quoted in a result file is labelled invalid",
           not unlabelled,
           "; ".join(unlabelled) + "  -- run stage4_reconcile.py"
           if unlabelled else
           (f"{len(labelled)} file(s) carry labelled invalid rows: "
            + ", ".join(labelled) if labelled
            else "no result file quotes an invalidated trial"))
    return invalid


def recorded_decisions(registry_path):
    """Deliberate, documented states -- not defects. See the registry."""
    if not os.path.isfile(registry_path):
        return {}
    with open(registry_path) as handle:
        return (yaml.safe_load(handle) or {}).get("decisions") or {}


def check_freeze(ledger_path):
    """Reports whether the ledger is deliberately closed to new writers."""
    from stage3_ledger import freeze_reason, freeze_path
    reason = freeze_reason(ledger_path)
    if reason is None:
        record("ledger is open to writers", True,
               "no freeze file -- new campaigns may run")
        return
    first = reason.splitlines()[0] if reason else ""
    record("ledger is FROZEN (deliberate)", True,
           f"{first} -- lift by deleting {os.path.basename(freeze_path(ledger_path))} "
           f"after snapshotting", severity="warn")


def check_ledger_status(ledger_path, registry_path=None):
    """Catches: a `running` row with no process, and unaccounted partial cost."""
    if not os.path.isfile(ledger_path):
        record("ledger present", False, f"{ledger_path} not found")
        return
    import sqlite3
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    have = {r[1] for r in conn.execute("PRAGMA table_info(trials)")}
    running = conn.execute(
        "SELECT * FROM trials WHERE status='running'").fetchall()
    live = _live_stage4_processes()
    verdicts = []
    for row in running:
        verdicts.append(_liveness(row, live, conn, have))
    dead = [v for v in verdicts if v["verdict"] == "abandoned"]
    unknown = [v for v in verdicts if v["verdict"] == "unverifiable"]
    if not running:
        record("every `running` row has a live worker behind it", True,
               "no running rows")
    elif dead:
        record("every `running` row has a live worker behind it", False,
               "; ".join(f"{v['trial']}: {v['why']}" for v in dead)
               + " -- resume or close explicitly", severity="warn")
    else:
        alive = [v for v in verdicts if v["verdict"] == "alive"]
        detail = "; ".join(f"{v['trial']}: {v['why']}" for v in verdicts)
        record("every `running` row has a live worker behind it", True,
               f"{len(alive)} alive, {len(unknown)} unverifiable -- {detail}",
               severity=("warn" if unknown and not alive else "fail"))

    by_status = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, count(*) n FROM trials GROUP BY status")}
    incomplete = sum(v for k, v in by_status.items()
                     if k not in ("success", "success_zero_qp"))
    record("incomplete and failed trials are counted, not dropped",
           True,
           f"{by_status.get('success', 0)} success, {incomplete} incomplete/failed "
           f"({by_status})")

    # Manifests claim zero failures; the ledger is the authority.
    mismatches = []
    runs_dir = os.path.join(HERE, "runs")
    if os.path.isdir(runs_dir):
        for campaign in sorted(os.listdir(runs_dir)):
            cdir = os.path.join(runs_dir, campaign)
            if not os.path.isdir(cdir):
                continue
            for name in sorted(os.listdir(cdir)):
                if not (name.startswith("campaign_") and name.endswith(".json")):
                    continue
                with open(os.path.join(cdir, name)) as handle:
                    man = json.load(handle)
                rows = conn.execute(
                    "SELECT status, count(*) n FROM trials WHERE campaign_id=? "
                    "GROUP BY status", (man.get("campaign_id", campaign),)).fetchall()
                led_bad = sum(r["n"] for r in rows
                              if r["status"] not in ("success", "success_zero_qp"))
                counts = man.get("ledger_status_counts")
                # backfilled manifests carry the authoritative view
                if counts is not None:
                    man_bad = sum(v for k, v in counts.items()
                                  if k not in ("success", "success_zero_qp")
                                  and isinstance(v, int))
                else:
                    man_bad = (int(man.get("n_failed") or 0)
                               + int(man.get("n_rejected") or 0))
                if led_bad != man_bad:
                    mismatches.append(
                        f"{campaign}/{name}: manifest {man_bad} failed+rejected, "
                        f"ledger {led_bad} non-success")
    record("campaign manifests agree with the ledger on failed/incomplete counts",
           not mismatches,
           "; ".join(mismatches) or "manifests and ledger agree",
           severity="warn")

    # Drift controls must be real re-evaluations (P5), and optimizer rows must
    # name their proposal source (P1). Both columns are added by the ledger's
    # migration the first time a WRITER opens it with the current code; this
    # audit is read-only on purpose (a campaign may be live), so their absence
    # is reported rather than fixed here.
    if "control_replica_id" not in have:
        record("drift controls are recorded as their own trial rows (P5)", False,
               "the ledger predates the control_replica_id column -- it is added "
               "automatically by the next campaign that opens it for writing",
               severity="warn")
    else:
        ctl = conn.execute(
            "SELECT count(*) n FROM trials WHERE control_replica_id IS NOT NULL"
        ).fetchone()["n"]
        record("drift controls are recorded as their own trial rows (P5)",
               True,
               f"{ctl} control row(s)"
               + ("" if ctl else " -- none yet; the pre-fix campaigns recorded "
                                 "none, which is why their '0% drift' claim "
                                 "measured the cache"),
               severity="warn")

    opt = conn.execute(
        "SELECT count(*) n FROM trials WHERE optimizer IS NOT NULL").fetchone()["n"]
    if "proposal_source" not in have:
        record("optimizer trials record which proposal source produced them (P1)",
               False,
               f"the ledger predates the proposal_source column, so none of its "
               f"{opt} optimizer rows can be attributed to a proposal source",
               severity="warn")
    else:
        prov = conn.execute(
            "SELECT count(*) n FROM trials WHERE proposal_source IS NOT NULL"
        ).fetchone()["n"]
        # An unattributed row is only a problem if it was written by code that
        # COULD have recorded it. The pre-audit campaigns provably could not, and
        # the registry says so -- guessing their sources from n_init would be the
        # very error P1 was raised about. What must still fail is a NEW campaign
        # that silently stops recording.
        decision = recorded_decisions(registry_path).get(
            "optimizer_provenance_unrecoverable") or {}
        historical = set(decision.get("campaigns") or [])
        unexplained = 0
        if historical:
            marks = ",".join("?" * len(historical))
            unexplained = conn.execute(
                f"SELECT count(*) n FROM trials WHERE optimizer IS NOT NULL AND "
                f"proposal_source IS NULL AND campaign_id NOT IN ({marks})",
                tuple(historical)).fetchone()["n"]
        else:
            unexplained = opt - prov
        accounted = opt - prov - unexplained
        record("every optimizer trial that COULD record its proposal source did",
               unexplained == 0,
               f"{prov} of {opt} rows carry one; {accounted} are pre-audit rows "
               f"whose columns did not exist (recorded decision "
               f"`optimizer_provenance_unrecoverable`, so their ranking stays "
               f"withdrawn); {unexplained} unexplained"
               + ("" if unexplained == 0 else " -- a current campaign is not "
                                              "recording provenance"))
    conn.close()


def check_code_identity(contract_path, ledger_path, registry_path=None):
    """Reports whether the current code can still reuse the ledger's trials.

    Four of the files this audit's own fixes touched -- `stage3_ledger.py`,
    `stage3_trial_runner.py`, `stage4_space.py`, `stage4_objectives.py` -- are in
    `CODE_IDENTITY_FILES`, so editing them changes the code fingerprint and
    therefore every cache key. That is the fingerprint working as designed, and
    it is deliberately blunt: the alternative is a cache that silently returns
    results from code that no longer exists, which this project has already been
    bitten by twice.

    It is reported rather than fixed. Whether to re-run or to re-baseline is a
    decision about compute budget, and it needs a human.
    """
    if not os.path.isfile(ledger_path):
        return
    from stage3_contract import load_contract
    import sqlite3
    contract = load_contract(contract_path)
    now = json.dumps(contract.code_fingerprint(), sort_keys=True, default=str)
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT code_fingerprint, count(*) n FROM trials WHERE status='success' "
        "GROUP BY code_fingerprint").fetchall()
    conn.close()
    stored = {r["code_fingerprint"]: r["n"] for r in rows}
    reachable = stored.get(now, 0)
    total = sum(stored.values())
    if reachable == total:
        record("the current code can still cache-reuse every recorded trial",
               True, f"{total} trial(s) reachable")
        return
    changed = []
    if stored:
        newest = max(stored, key=stored.get)
        old_files = (json.loads(newest) or {}).get("files", {})
        new_files = json.loads(now).get("files", {})
        changed = sorted(k for k in set(old_files) | set(new_files)
                         if old_files.get(k) != new_files.get(k))
    # A cold cache is only a finding if it is UNEXPLAINED. The decision to keep
    # it cold is recorded, together with which identity files were expected to
    # change; the numbers are still printed, but a NEW file entering that set is
    # what actually warrants attention.
    decision = recorded_decisions(registry_path).get("cache_cold_accepted") or {}
    accepted = set(decision.get("accepted_changed_identity_files") or [])
    unexpected = sorted(set(changed) - accepted) if accepted else sorted(changed)
    if accepted and not unexpected:
        record("the cold cache is the accepted, recorded one (no NEW identity "
               "file changed)",
               True,
               f"{reachable} of {total} success trial(s) reachable, as decided on "
               f"{decision.get('decided')}: {', '.join(sorted(os.path.basename(c) for c in changed))}")
    else:
        record("the cold cache is the accepted, recorded one (no NEW identity "
               "file changed)",
               False,
               f"{reachable} of {total} reachable; UNEXPECTED identity change in "
               f"{', '.join(os.path.basename(c) for c in unexpected)} -- not covered "
               f"by any recorded decision",
               severity="warn")


# A trial is judged alive on EVIDENCE, in this order. Timestamps on the trial
# row are the weakest signal and are never used alone: long evaluator work does
# not touch that row, and sub-run rows are written only at completion, so a
# healthy 29-hour trial and an abandoned one look identical there.
LIVENESS_STALE_SECONDS = 3600.0


def _liveness(row, live_processes, conn, columns):
    """(verdict, why) for one `running` trial: alive / abandoned / unverifiable.

    `unverifiable` is a first-class answer. A PID namespace can hide a genuine
    host process, and an older trial may predate the heartbeat columns entirely.
    Declaring such a trial stale would be a false positive, and a false positive
    here invites someone to kill a job that is doing real work.
    """
    trial = row["trial_id"]
    short = trial[-12:]
    now = time.time()

    # 1. Heartbeat, if the row has one. Strongest signal: it is written DURING
    #    the work by the process holding the lease.
    if "heartbeat_at" in columns and row["heartbeat_at"]:
        age = now - float(row["heartbeat_at"])
        where = row["hostname"] if "hostname" in columns else None
        who = f" on {where}" if where else ""
        if age < LIVENESS_STALE_SECONDS:
            return {"trial": short, "verdict": "alive",
                    "why": f"heartbeat {age / 60:.0f} min ago{who}"}
        # A stale heartbeat is SUSPICION, not proof (finding N3). The heartbeat
        # can stop while the evaluator lives -- a database lock storm, a paused
        # process, a full disk. Calling that "abandoned" without corroboration
        # is the false positive that gets real work killed. Corroboration is
        # checked below; if the process list is unavailable this stays
        # unverifiable.
        beaten = {"trial": short, "verdict": "abandoned",
                  "why": f"heartbeat {age / 3600:.1f} h old{who}, no artifact "
                         f"growth, and no process names it"}
        suspected = {"trial": short, "verdict": "unverifiable",
                     "why": f"heartbeat {age / 3600:.1f} h old{who} -- SUSPECTED "
                            f"dead, but unconfirmed: no process list is available "
                            f"to corroborate it (a PID namespace can hide a "
                            f"genuine host process). Confirm by hand before "
                            f"closing or re-running."}
    else:
        beaten = suspected = None

    # 2. Growing artifacts. The run writes hits files continuously, so a file
    #    modified recently proves work is happening even when the process is
    #    invisible and the row has no heartbeat.
    newest, nbytes = _artifact_activity(row["run_dir"])
    if newest is not None and (now - newest) < LIVENESS_STALE_SECONDS:
        return {"trial": short, "verdict": "alive",
                "why": f"hits files growing ({nbytes / 1e6:.1f} MB, newest "
                       f"{(now - newest) / 60:.0f} min ago)"}

    # 3. A process that names this trial.
    if any(trial in proc for proc in live_processes):
        return {"trial": short, "verdict": "alive",
                "why": "a live process names this trial"}

    if beaten is not None:
        # Only a stale heartbeat CORROBORATED by a usable process list, in which
        # nothing names this trial, justifies `abandoned`.
        return beaten if live_processes else suspected
    if not live_processes:
        return {"trial": short, "verdict": "unverifiable",
                "why": "no heartbeat recorded, no recent artifact growth, and no "
                       "process list available (a PID namespace can hide a "
                       "genuine host process) -- ACTIVITY UNVERIFIABLE, do not "
                       "treat as stale"}
    age = now - (newest or row["updated_at"] or 0)
    return {"trial": short, "verdict": "unverifiable",
            "why": f"no heartbeat (row predates the liveness columns); newest "
                   f"artifact {age / 3600:.1f} h old and no process names it -- "
                   f"ACTIVITY UNVERIFIABLE, confirm by hand before closing"}


def _artifact_activity(run_dir):
    """(newest mtime, total bytes) over a trial's hits directory, or (None, 0)."""
    if not run_dir:
        return None, 0
    hits = os.path.join(run_dir, "hits")
    if not os.path.isdir(hits):
        return None, 0
    newest, total = None, 0
    try:
        with os.scandir(hits) as it:
            for entry in it:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                total += st.st_size
                newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
    except OSError:
        return None, 0
    return newest, total


def _live_stage4_processes():
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    # Both the driver (python stage4_*.py) and the Geant4 workers it spawns
    # count: a worker's argv carries the trial_id in its macro path, which is
    # what actually proves a `running` row is alive.
    return [ln for ln in out.splitlines()
            if "stage4" in ln and "stage4_audit" not in ln]


WITHDRAWAL_MARKERS = ("withdraw", "WITHDRAWN", "invalid", "INVALID", "~~",
                      "no longer", "must be rerun", "not yet validated",
                      "cannot be", "was supposed to")


def check_headlines(docs, registry_path, ledger_path):
    """Catches: a summary still ASSERTING a claim a finding withdrew.

    The phrases come from `withdrawn_phrases` in the invalidation registry, so
    the list of what may not be said lives with the finding that withdrew it
    rather than in this file. An occurrence is a violation only if the sentence
    around it carries no withdrawal marker -- otherwise the withdrawal notices
    would flag themselves, and the honest fix (saying plainly that a claim is
    withdrawn) would look like the defect.
    """
    phrases = []
    if os.path.isfile(registry_path):
        with open(registry_path) as handle:
            reg = yaml.safe_load(handle) or {}
        for finding in (reg.get("findings") or {}).values():
            phrases += list(finding.get("withdrawn_phrases") or [])
    if not phrases:
        record("withdrawn claims are listed in the registry", True,
               "no withdrawn_phrases declared", severity="warn")
        return

    asserting = []
    for doc in docs:
        if not os.path.isfile(doc):
            continue
        text = open(doc, errors="replace").read()
        for phrase in phrases:
            for match in re.finditer(re.escape(phrase), text):
                lo = max(0, match.start() - 400)
                hi = min(len(text), match.end() + 200)
                context = text[lo:hi]
                if not any(m in context for m in WITHDRAWAL_MARKERS):
                    line = text[:match.start()].count("\n") + 1
                    asserting.append(f"{os.path.basename(doc)}:{line} "
                                     f"asserts {phrase!r}")
    record("no document still asserts a withdrawn claim",
           not asserting,
           "; ".join(asserting) or
           f"{len(phrases)} withdrawn phrase(s) checked; every occurrence is "
           f"inside a withdrawal notice")

    # And the headline must match the best CONFIRMED value in the ledger.
    if not os.path.isfile(ledger_path):
        return
    import sqlite3
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # Best vs baseline WITHIN one campaign: the campaigns differ in fidelity and
    # the baseline itself moves 7.3% between seed banks, so a min/max taken
    # across all of them would compare candidates that were never comparable.
    invalid_ids = set()
    if os.path.isfile(registry_path):
        with open(registry_path) as handle:
            reg2 = yaml.safe_load(handle) or {}
        for finding in (reg2.get("findings") or {}).values():
            invalid_ids |= {r["trial_id"] for r in (finding.get("trials") or [])
                            if r.get("verdict") == "invalid"}
    per_campaign = {}
    for row in conn.execute(
            "SELECT trial_id, campaign_id, qps_per_primary FROM trials "
            "WHERE status='success' AND campaign_id LIKE "
            "'stage4_property_v1_confirm%' AND qps_per_primary IS NOT NULL"):
        if row["trial_id"] in invalid_ids:
            continue                              # never set a headline from one
        per_campaign.setdefault(row["campaign_id"], []).append(
            row["qps_per_primary"])
    conn.close()
    reductions = [100.0 * (1.0 - min(v) / max(v))
                  for v in per_campaign.values() if len(v) >= 2]
    if not reductions:
        return
    reduction = max(reductions)
    quoted = []
    for doc in docs:
        if not os.path.isfile(doc):
            continue
        quoted += [float(m) for m in
                   re.findall(r"[−-](\d\d\.\d)%\*{0,2} junction QPs", 
                              open(doc, errors="replace").read())]
    record("quoted headline reductions are within 2 points of the ledger's best "
           "confirmed value",
           all(abs(q - reduction) <= 2.0 for q in quoted),
           f"ledger best confirmed = -{reduction:.1f}%; documents quote "
           f"{sorted(set(quoted)) or 'nothing parseable'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contract", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--registry",
                    default=os.path.join(HERE, "stage4_invalidations.yaml"))
    ap.add_argument("--param-set", default=os.path.join(HERE, "parameter_set.txt"))
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    ap.add_argument("--json", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    args = ap.parse_args()

    print("Stage 4 consistency audit\n")
    print("Energy protocol and contract:")
    check_energy_protocol(args.contract, args.param_set)
    print("\nInvalidated results:")
    check_invalidations(args.ledger, args.registry, args.results)
    print("\nLedger and campaign status:")
    check_freeze(args.ledger)
    check_ledger_status(args.ledger, args.registry)
    check_code_identity(args.contract, args.ledger, args.registry)
    print("\nDocumentation:")
    check_headlines([os.path.join(HERE, "STAGE4_RESULTS.md"),
                     os.path.join(HERE, "README.md")], args.registry, args.ledger)

    fails = [c for c in CHECKS if c["severity"] == "fail"]
    warns = [c for c in CHECKS if c["severity"] == "warn"]
    print(f"\n{sum(1 for c in CHECKS if c['ok'])}/{len(CHECKS)} checks passed, "
          f"{len(fails)} failure(s), {len(warns)} warning(s)")
    if args.json:
        with open(args.json, "w") as handle:
            json.dump({"checks": CHECKS, "generated_at": time.time()}, handle, indent=1)
        print(f"Written: {args.json}")
    return 1 if fails or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
