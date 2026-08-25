#!/bin/bash
# Post-XL runbook: check -> snapshot -> validate -> migrate -> unfreeze.
#
# FAIL-CLOSED STATE MACHINE. Each step writes a receipt only on success, and the
# next step refuses without it. Finding N0: the previous version keyed migrate
# and unfreeze on SHA256SUMS, which `snapshot` itself creates -- so
# snapshot -> migrate -> unfreeze silently skipped validation entirely.
#
#   ./stage4_post_xl.sh check      # read-only; safe at any time
#   ./stage4_post_xl.sh snapshot   # needs: XL genuinely complete
#   ./stage4_post_xl.sh validate   # needs: .snapshot_complete
#   ./stage4_post_xl.sh migrate    # needs: .validated  (+ manifest digest match)
#   ./stage4_post_xl.sh unfreeze   # needs: .migrated
#   ./stage4_post_xl.sh all        # the five above, stopping at the first failure
#   ./stage4_post_xl.sh snapshot-recovery   # snapshot an INCOMPLETE attempt
#
# Receipts live in the snapshot directory and record what was verified, so the
# state machine cannot be satisfied by a file that merely exists.
#
# Overridable for testing: LEDGER, SNAPROOT, XL_TRIAL, XL_RESULT_JSON, AUDIT_CMD,
# XL_OWNER_PID.
set -uo pipefail
cd "$(dirname "$0")"

PY=${PY:-$HOME/.conda/envs/G4CMP/bin/python}
LEDGER=${LEDGER:-stage4_trials.sqlite}
FREEZE=$LEDGER.frozen
SNAPROOT=${SNAPROOT:-snapshots}
STAMP=${STAMP:-$(date '+%Y%m%d')}
SNAPDIR=$SNAPROOT/pre_migration_$STAMP
XL_TRIAL=${XL_TRIAL:-stage4_property_v1_confirmXL_603969004eaa}
XL_RESULT_JSON=${XL_RESULT_JSON:-results/stage4_confirmation_XL_best_random+elasticity_of_SiC.json}
# What gets copied alongside the database. Overridable so the gate can snapshot
# a disposable tree instead of the repository's real results directory.
SNAP_ARTIFACTS=${SNAP_ARTIFACTS:-"results runs/stage4_property_v1_confirmXL"}
AUDIT_CMD=${AUDIT_CMD:-$PY stage4_audit.py}
REQUIRED_COLUMNS=${REQUIRED_COLUMNS:-"proposal_source optimizer_generation used_in_optimizer_update control_replica_id lease_uuid heartbeat_at hostname owner_pid owner_ppid scheduler_job_id simulation_identity_hash"}

say()  { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die()  { printf '[%s] REFUSED: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }
need() { [ -f "$SNAPDIR/$1" ] || die "missing receipt '$1' -- run '$2' first (fail-closed: a step never runs on assumption)"; }

# --------------------------------------------------------------------------
# check -- is the XL confirmation genuinely COMPLETE?
#
# Finding N1: accepting `incomplete_scenario_set` let `all` migrate and unfreeze
# after a failed attempt. A planned 32-sub-run confirmation is complete only if
# every one of those checks passes. An incomplete attempt is a recovery case and
# takes `snapshot-recovery`, which never leads to unfreeze.
# --------------------------------------------------------------------------
check() {
  say "checking whether the XL confirmation is COMPLETE (not merely terminal)"
  LEDGER="$LEDGER" XL_TRIAL="$XL_TRIAL" XL_RESULT_JSON="$XL_RESULT_JSON" \
  XL_OWNER_PID="${XL_OWNER_PID:-}" $PY - <<'PY'
import json, os, sqlite3, sys, time
ledger, trial = os.environ["LEDGER"], os.environ["XL_TRIAL"]
result_json = os.environ["XL_RESULT_JSON"]
GOOD = ("success", "success_zero_qp")
SUBRUN_GOOD = ("success", "success_zero_qp", "teardown_sigsegv_complete")
fail = []
c = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True); c.row_factory = sqlite3.Row
r = c.execute("SELECT * FROM trials WHERE trial_id=?", (trial,)).fetchone()
if r is None:
    print(f"  {trial}: not in the ledger"); sys.exit(2)

print(f"  trial status            : {r['status']}")
if r["status"] not in GOOD:
    fail.append(f"status is {r['status']!r}, not one of {GOOD}")

planned = r["n_positions"] * r["n_replicas"]
subs = c.execute("SELECT status, count(*) n FROM sub_runs WHERE trial_id=? "
                 "GROUP BY status", (trial,)).fetchall()
n_rows = sum(x["n"] for x in subs)
n_good = sum(x["n"] for x in subs if x["status"] in SUBRUN_GOOD)
print(f"  planned sub-runs        : {planned} (rows: {n_rows})")
print(f"  terminal-good sub-runs  : {n_good}/{planned}  {dict((x['status'], x['n']) for x in subs)}")
if n_rows != planned:
    fail.append(f"{n_rows} sub-run rows, expected {planned}")
if n_good != planned:
    fail.append(f"{n_good}/{planned} sub-runs terminal-good")

run_dir = r["run_dir"] or ""
hits = os.path.join(run_dir, "hits")
if not os.path.isdir(hits):
    fail.append(f"no hits directory at {hits}")
else:
    files = sorted(os.listdir(hits))
    done = [f for f in files if f.endswith(".done")]
    data = [f for f in files if f.endswith("_hitsfile.txt")]
    empty = [f for f in data if os.path.getsize(os.path.join(hits, f)) == 0]
    total = sum(os.path.getsize(os.path.join(hits, f)) for f in files)
    newest = max((os.path.getmtime(os.path.join(hits, f)) for f in files), default=0)
    print(f"  done markers            : {len(done)}/{planned}")
    print(f"  hit artifacts           : {len(data)} files, {total/1e6:.1f} MB, "
          f"{len(empty)} empty, newest {(time.time()-newest)/60:.0f} min ago")
    if len(done) != planned:
        fail.append(f"{len(done)} done markers, expected {planned}")
    if empty:
        fail.append(f"{len(empty)} empty hit artifact(s): {empty[:3]}")

print(f"  result JSON             : {result_json}")
if not os.path.isfile(result_json):
    fail.append(f"expected result JSON {result_json} not written")
else:
    try:
        doc = json.load(open(result_json))
        scored = [k for k, v in (doc.get("results") or {}).items()
                  if v.get("value") is not None]
        print(f"    scored candidates     : {scored}")
        if not scored:
            fail.append("result JSON contains no scored candidate")
    except (ValueError, OSError) as exc:
        fail.append(f"result JSON unreadable: {exc}")

pid = os.environ.get("XL_OWNER_PID", "").strip()
if pid:
    alive = os.path.exists(f"/proc/{pid}")
    print(f"  parent confirm process  : pid {pid} {'STILL ALIVE' if alive else 'exited'}")
    if alive:
        fail.append(f"parent confirmation process {pid} is still running")
else:
    procs = []
    try:
        import subprocess
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True,
                             timeout=20).stdout
        want = os.path.basename(result_json)
        procs = [ln for ln in out.splitlines()
                 if trial in ln or ("stage4_confirm.py" in ln and want in ln)]
    except Exception:
        procs = None
    if procs is None:
        print("  parent confirm process  : UNVERIFIABLE (no process list)")
        fail.append("cannot confirm the writer exited (set XL_OWNER_PID to assert it)")
    elif procs:
        print(f"  parent confirm process  : {len(procs)} process(es) still reference it")
        fail.append("a stage4_confirm process is still running")
    else:
        print("  parent confirm process  : none running")

if fail:
    print("\n  NOT COMPLETE:")
    for f in fail:
        print(f"    - {f}")
    sys.exit(3)
print("\n  COMPLETE: every completion condition satisfied")
PY
  rc=$?
  case $rc in
    0) say "XL is COMPLETE -- safe to snapshot" ;;
    2) say "XL trial not found in the ledger" ;;
    *) say "XL is NOT complete -- do NOT snapshot, migrate or unfreeze.
           An incomplete attempt can be preserved with 'snapshot-recovery',
           which deliberately does not lead to unfreeze." ;;
  esac
  return $rc
}

# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------
_do_snapshot() {
  local mode=$1
  # Guard on the SNAPSHOT, not on the directory: `close-incomplete` writes its
  # closure record into $SNAPDIR before any snapshot exists, and treating the
  # bare directory as "already snapshotted" made the closed-incomplete path
  # unreachable.
  [ -f "$SNAPDIR/.snapshot_complete" ] || [ -f "$SNAPDIR/$(basename "$LEDGER")" ] \
    && die "$SNAPDIR already holds a snapshot; refusing to overwrite it"
  mkdir -p "$SNAPDIR" || die "cannot create $SNAPDIR"
  say "snapshotting with sqlite3 .backup (consistent across the WAL)"
  LEDGER="$LEDGER" DEST="$SNAPDIR/$(basename "$LEDGER")" $PY - <<'PY' || die "backup failed"
import os, sqlite3
src = sqlite3.connect(f"file:{os.environ['LEDGER']}?mode=ro", uri=True)
dst = sqlite3.connect(os.environ["DEST"])
with dst:
    src.backup(dst)
dst.close(); src.close()
print("  backup complete")
PY
  for f in $SNAP_ARTIFACTS; do
    [ -e "$f" ] || continue
    cp -a "$f" "$SNAPDIR/" || die "failed to copy $f into the snapshot"
  done
  # Manifest EXCLUDING itself, and built through a temp file OUTSIDE the
  # snapshot. Finding N0: `find | xargs sha256sum > SHA256SUMS` inside the tree
  # races with its own output. Writing to `SHA256SUMS.tmp` in the tree only
  # moved the race -- the gate caught the manifest listing a temp file that the
  # subsequent `mv` had already removed, so `--check` failed on every snapshot.
  local tmp_manifest
  tmp_manifest=$(mktemp) || die "cannot create a temporary manifest"
  ( cd "$SNAPDIR" \
    && find . -type f ! -name 'SHA256SUMS*' ! -name '.*' -print0 | sort -z \
       | xargs -0 sha256sum ) > "$tmp_manifest" \
    || { rm -f "$tmp_manifest"; die "failed to compute checksums"; }
  mv "$tmp_manifest" "$SNAPDIR/SHA256SUMS" || die "cannot install the checksum manifest"
  printf 'mode=%s\ncreated_at=%s\nledger=%s\nhost=%s\nmanifest_sha256=%s\n' \
    "$mode" "$(date '+%F %T')" "$LEDGER" "$(hostname)" \
    "$(sha256sum "$SNAPDIR/SHA256SUMS" | cut -d' ' -f1)" \
    > "$SNAPDIR/.snapshot_complete" || die "cannot write the snapshot receipt"
  say "snapshot written: $SNAPDIR ($(du -sh "$SNAPDIR" | cut -f1)), mode=$mode"
}

# Migration SAFETY and scientific COMPLETENESS are different questions, and the
# first version of this runbook conflated them: `snapshot` demanded that the XL
# trial had SUCCEEDED, when what actually makes migration unsafe is a LIVE
# WRITER. best_random duly timed out at 41.7 h, leaving a campaign that was over
# -- nothing running, nothing to corrupt -- but a runbook that would never let
# the ledger be migrated. A gate that can never be satisfied is not fail-closed,
# it is stuck.
no_live_writer() {
  say "checking the MIGRATION SAFETY condition (no live writer) -- which is a"
  say "  different question from whether the confirmation succeeded"
  LEDGER="$LEDGER" $PY - <<'PY'
import os, sqlite3, subprocess, sys, time
ledger = os.environ["LEDGER"]
c = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True); c.row_factory = sqlite3.Row
running = c.execute("SELECT trial_id, run_dir FROM trials WHERE status='running'").fetchall()
print(f"  trials in `running` state : {len(running)}")
for r in running:
    hits = os.path.join(r["run_dir"] or "", "hits")
    newest = 0
    if os.path.isdir(hits):
        for f in os.listdir(hits):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(hits, f)))
            except OSError:
                pass
    age = f"{(time.time() - newest) / 3600:.1f} h old" if newest else "absent"
    print(f"    {r['trial_id'][-12:]} (artifacts {age})")
# Who actually HOLDS THE DATABASE OPEN. Matching process command lines is
# hopeless here: a shell whose argv merely mentions "stage4_confirm.py" -- an
# editor, a grep, this very script's own heredoc -- looks exactly like the
# writer. An open file descriptor on the ledger does not lie.
# /proc must be showing OUR pid namespace. In a container whose /proc is the
# host's, os.getpid() returns a namespace pid while /proc/self/stat reports the
# host one -- so the "skip myself" test below fails and this check reports its
# OWN read-only connection as an external writer, refusing to snapshot forever.
try:
    with open("/proc/self/stat", "rb") as fh:
        ns_ok = int(fh.read().split(b" ", 1)[0]) == os.getpid()
except (OSError, ValueError, IndexError):
    ns_ok = False
if not ns_ok:
    print("  /proc does not show this pid namespace -- cannot identify writers")
    print("  ACTIVITY UNVERIFIABLE: confirm by hand that nothing is writing to")
    print(f"  {ledger} before snapshotting or migrating.")
    sys.exit(4)
# Resolve our own /proc entry rather than trusting os.getpid() alone.
try:
    self_pid = int(os.readlink("/proc/self"))
except (OSError, ValueError):
    self_pid = os.getpid()
targets = {os.path.realpath(ledger), os.path.realpath(ledger + "-wal"),
           os.path.realpath(ledger + "-shm")}
holders, scanned, unreadable = [], 0, 0
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid) in (self_pid, os.getpid()):
        continue
    fd_dir = f"/proc/{pid}/fd"
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        unreadable += 1
        continue
    scanned += 1
    for fd in fds:
        try:
            target = os.path.realpath(os.path.join(fd_dir, fd))
        except OSError:
            continue
        if target in targets:
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")
                cmd = cmd.decode(errors="replace").strip()
            except OSError:
                cmd = "?"
            holders.append(f"pid {pid}: {cmd[:90]}")
            break
print(f"  processes scanned         : {scanned}"
      + (f" ({unreadable} not readable)" if unreadable else ""))
print(f"  holding the ledger open   : {len(holders)}")
for h in holders[:5]:
    print(f"    {h}")
if not scanned:
    print("  /proc not readable -- UNVERIFIABLE, confirm by hand")
    sys.exit(4)
sys.exit(0 if not holders and not running else 5)
PY
  rc=$?
  case $rc in
    0) say "no live writer -- safe to snapshot and migrate" ;;
    4) say "cannot verify (no process list); confirm by hand" ;;
    *) say "a writer may still be live -- NOT safe" ;;
  esac
  return $rc
}

snapshot() {
  no_live_writer || die "a writer may still be live; refusing to snapshot a moving database"
  if check; then
    _do_snapshot complete
  elif [ -f "$SNAPDIR/.campaign_closed" ]; then
    say "XL did not succeed, but the campaign was explicitly closed:"
    sed -n 's/^reason=/  reason: /p' "$SNAPDIR/.campaign_closed"
    _do_snapshot closed_incomplete
  else
    die "The XL confirmation did NOT complete successfully.

           Migration is still SAFE (no writer is live), but this snapshot must
           not be labelled a completed confirmation. Record the decision:

             ./stage4_post_xl.sh close-incomplete 'why it will not be retried'

           then re-run 'snapshot'. Or use 'snapshot-recovery' to preserve the
           attempt without certifying anything."
  fi
}

snapshot_recovery() {
  say "RECOVERY snapshot of a possibly incomplete attempt"
  no_live_writer || die "a writer may still be live; refusing to snapshot a moving database"
  _do_snapshot recovery
  say "NOTE: mode=recovery. 'validate' will refuse to certify it for migration."
}

close_incomplete() {
  local reason="${1:-}"
  [ -n "$reason" ] || die "close-incomplete needs a reason, e.g.
           ./stage4_post_xl.sh close-incomplete 'best_random timed out at 41.7 h'"
  no_live_writer || die "a writer may still be live; refusing to close the campaign"
  mkdir -p "$SNAPDIR" || die "cannot create $SNAPDIR"
  {
    printf 'closed_at=%s\n' "$(date '+%F %T')"
    printf 'host=%s\n' "$(hostname)"
    printf 'trial=%s\n' "$XL_TRIAL"
    printf 'reason=%s\n' "$reason"
  } > "$SNAPDIR/.campaign_closed" || die "cannot write the closure record"
  say "campaign closed as INCOMPLETE; reason recorded in $SNAPDIR/.campaign_closed:"
  say "  $reason"
  say "'snapshot' will now proceed, labelling the snapshot mode=closed_incomplete."
}

# --------------------------------------------------------------------------
# validate -- the only step that may write .validated
# --------------------------------------------------------------------------
validate() {
  need .snapshot_complete snapshot
  grep -qE '^mode=(complete|closed_incomplete)$' "$SNAPDIR/.snapshot_complete" \
    || die "snapshot is mode=recovery; a recovery snapshot is never certified for migration.
           If the campaign is genuinely over, record it with 'close-incomplete <reason>'."

  say "verifying the checksum manifest"
  ( cd "$SNAPDIR" && sha256sum --quiet --check SHA256SUMS ) \
    || die "checksum verification FAILED -- the snapshot is corrupt"
  say "  all files match SHA256SUMS"

  say "verifying database integrity and invariants"
  LEDGER="$LEDGER" SNAP="$SNAPDIR/$(basename "$LEDGER")" $PY - <<'PY' || die "snapshot invariants FAILED"
import json, os, sqlite3, sys
a = sqlite3.connect(f"file:{os.environ['LEDGER']}?mode=ro", uri=True); a.row_factory = sqlite3.Row
b = sqlite3.connect(f"file:{os.environ['SNAP']}?mode=ro", uri=True); b.row_factory = sqlite3.Row
ok = True
res = b.execute("PRAGMA integrity_check").fetchone()[0]
print(f"  integrity_check: {res}"); ok &= (res == "ok")
for table in ("trials", "sub_runs"):
    na = a.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    nb = b.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    print(f"  {table}: live {na}, snapshot {nb}"); ok &= (na == nb)
hist = {r["status"]: r["n"] for r in
        b.execute("SELECT status, count(*) n FROM trials GROUP BY status")}
for k, v in sorted(hist.items()):
    print(f"    {k:24s} {v}")
json.dump(hist, open(os.environ["SNAP"] + ".statushist.json", "w"), indent=1)
sys.exit(0 if ok else 1)
PY

  say "running the consistency audit against the snapshot (failures are fatal here)"
  $AUDIT_CMD --ledger "$SNAPDIR/$(basename "$LEDGER")" \
    || die "the audit reported FAILURES against the snapshot -- not certifying it"

  printf 'validated_at=%s\nhost=%s\nmanifest_sha256=%s\naudit=%s\n' \
    "$(date '+%F %T')" "$(hostname)" \
    "$(sha256sum "$SNAPDIR/SHA256SUMS" | cut -d' ' -f1)" "$AUDIT_CMD" \
    > "$SNAPDIR/.validated" || die "cannot write the validation receipt"
  say "snapshot VALIDATED -- receipt written"
}

# --------------------------------------------------------------------------
# migrate -- requires .validated AND that the manifest has not changed since
# --------------------------------------------------------------------------
migrate() {
  need .validated validate
  local recorded current
  recorded=$(sed -n 's/^manifest_sha256=//p' "$SNAPDIR/.validated")
  current=$(sha256sum "$SNAPDIR/SHA256SUMS" | cut -d' ' -f1)
  [ -n "$recorded" ] && [ "$recorded" = "$current" ] \
    || die "the checksum manifest changed after validation ($recorded != $current) -- re-validate"

  say "migrating the LIVE ledger (additive columns only; no row is rewritten)"
  LEDGER="$LEDGER" HIST="$SNAPDIR/$(basename "$LEDGER").statushist.json" \
  REQUIRED_COLUMNS="$REQUIRED_COLUMNS" $PY - <<'PY' || die "migration FAILED"
import json, os, sys
sys.path.insert(0, os.getcwd())
from stage3_ledger import Ledger
ledger = os.environ["LEDGER"]
required = os.environ["REQUIRED_COLUMNS"].split()
before_hist = json.load(open(os.environ["HIST"]))
with Ledger(ledger, allow_frozen=True) as led:          # migration runs on open
    cols = [r[1] for r in led.conn.execute("PRAGMA table_info(trials)")]
    missing = [c for c in required if c not in cols]
    for c in required:
        print(f"    {'OK     ' if c in cols else 'MISSING'} {c}")
    after_hist = {r[0]: r[1] for r in led.conn.execute(
        "SELECT status, count(*) FROM trials GROUP BY status")}
    n_tr = led.conn.execute("SELECT count(*) FROM trials").fetchone()[0]
    n_sr = led.conn.execute("SELECT count(*) FROM sub_runs").fetchone()[0]
print(f"  trials columns now: {len(cols)}")
print(f"  rows preserved    : {n_tr} trials, {n_sr} sub_runs")
bad = []
if missing:
    bad.append(f"required column(s) still missing after migration: {missing}")
if after_hist != before_hist:
    bad.append(f"status histogram changed: {before_hist} -> {after_hist}")
if bad:
    for b in bad:
        print(f"  FAIL: {b}")
    sys.exit(1)
print("  status histogram unchanged; every required column present")
PY

  printf 'migrated_at=%s\nhost=%s\nledger=%s\nvalidated_manifest=%s\n' \
    "$(date '+%F %T')" "$(hostname)" "$LEDGER" "$current" \
    > "$SNAPDIR/.migrated" || die "cannot write the migration receipt"
  say "migration complete and verified -- receipt written"
}

# --------------------------------------------------------------------------
unfreeze() {
  need .migrated migrate
  [ -f "$FREEZE" ] || { say "already unfrozen"; return 0; }
  mv "$FREEZE" "$SNAPDIR/frozen.reason.txt" || die "cannot move the freeze file"
  printf 'unfrozen_at=%s\nhost=%s\n' "$(date '+%F %T')" "$(hostname)" \
    > "$SNAPDIR/.unfrozen"
  say "ledger unfrozen; the reason is preserved at $SNAPDIR/frozen.reason.txt"
}

status() {
  say "state machine for $SNAPDIR"
  for r in .snapshot_complete .validated .migrated .unfrozen; do
    if [ -f "$SNAPDIR/$r" ]; then printf '  [x] %-20s %s\n' "$r" "$(head -1 "$SNAPDIR/$r")"
    else printf '  [ ] %-20s not reached\n' "$r"; fi
  done
  [ -f "$FREEZE" ] && say "ledger is FROZEN" || say "ledger is writable"
}

case "${1:-}" in
  check)             check ;;
  no-live-writer)    no_live_writer ;;
  close-incomplete)  close_incomplete "${2:-}" ;;
  snapshot)          snapshot ;;
  snapshot-recovery) snapshot_recovery ;;
  validate)          validate ;;
  migrate)           migrate ;;
  unfreeze)          unfreeze ;;
  status)            status ;;
  all)               snapshot && validate && migrate && unfreeze ;;
  *)                 sed -n '2,26p' "$0"; exit 1 ;;
esac
