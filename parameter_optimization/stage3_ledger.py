"""Stage 3 trial ledger: SQLite, append-only in spirit, restartable.

Two tables:

  trials    one row per (candidate, fidelity, scenario set, seed bank)
  sub_runs  one row per (trial, replica, position)

The design rule that matters most: **planned rows are written before anything
launches.** Completeness is then judged against what was planned, never against
what happens to be on disk. Inferring the expected set from the observed set is
the failure this project has already hit three times -- it degrades silently
while every file still looks well formed.

Second rule: **a missing output is never a zero.** There is no status that means
"no data, scored as 0". An unfinished trial is `planned` or a failure; it is not
an observation.

The cache key covers the resolved candidate, every derived value, the ordered
scenario set and its weights, the seeds, and the code fingerprint. Two trials
share a key only if re-running one would reproduce the other.
"""

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time

# Terminal statuses. Only `success` may be read as an optimizer observation;
# `success_zero_qp` is a legitimate physical zero and is also observable.
STATUS_PLANNED = "planned"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_SUCCESS_ZERO = "success_zero_qp"
STATUS_TIMEOUT = "timeout"
STATUS_MEMORY_KILLED = "memory_killed"
STATUS_MACRO_ABORTED = "macro_aborted"
STATUS_SIM_FAILED = "simulation_failed"
STATUS_TEARDOWN_SEGV = "teardown_sigsegv_complete"
STATUS_CORRUPT = "corrupt_or_incomplete"
STATUS_CONSTRAINT = "constraint_rejected"
STATUS_INCOMPLETE_SET = "incomplete_scenario_set"

OBSERVABLE_STATUSES = (STATUS_SUCCESS, STATUS_SUCCESS_ZERO)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    trial_id          TEXT PRIMARY KEY,
    campaign_id       TEXT NOT NULL,
    cache_key         TEXT NOT NULL UNIQUE,
    contract_hash     TEXT NOT NULL,
    code_fingerprint  TEXT NOT NULL,
    candidate         TEXT NOT NULL,
    derived           TEXT NOT NULL,
    fidelity          TEXT NOT NULL,
    events_total      INTEGER NOT NULL,
    events_per_sub_run INTEGER NOT NULL,
    n_positions       INTEGER NOT NULL,
    n_replicas        INTEGER NOT NULL,
    scenario          TEXT NOT NULL,
    seed_bank_id      INTEGER NOT NULL,
    status            TEXT NOT NULL,
    total_qps         REAL,
    qps_per_primary   REAL,
    per_electrode_qps TEXT,
    runtime_s         REAL,
    peak_rss_gb       REAL,
    failure_reason    TEXT,
    run_dir           TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    objective_name    TEXT,
    objective_value   REAL,
    objective_se      REAL,
    optimizer         TEXT,
    iteration         INTEGER,
    acquisition       REAL
);

CREATE TABLE IF NOT EXISTS sub_runs (
    trial_id     TEXT NOT NULL,
    replica      INTEGER NOT NULL,
    position     INTEGER NOT NULL,
    seed         INTEGER,
    macro        TEXT,
    hits_file    TEXT,
    done_marker  TEXT,
    status       TEXT NOT NULL,
    return_code  INTEGER,
    runtime_s    REAL,
    failure_reason TEXT,
    updated_at   REAL NOT NULL,
    total_qps    REAL,
    per_electrode_qps TEXT,
    n_hits       INTEGER,
    PRIMARY KEY (trial_id, replica, position),
    FOREIGN KEY (trial_id) REFERENCES trials(trial_id)
);

CREATE INDEX IF NOT EXISTS idx_trials_cache ON trials(cache_key);
CREATE INDEX IF NOT EXISTS idx_subruns_status ON sub_runs(trial_id, status);
"""


def compute_cache_key(contract_hash, code_fingerprint, candidate, derived,
                      fidelity, events_total, scenario, seed_bank_id,
                      control_replica_id=None):
    """Canonical hash of everything that would have to be identical for a
    re-run to reproduce this trial.

    Order matters and is preserved: the injection sites are an ordered
    quadrature, so a permuted site list is a different scenario even though the
    set of points is the same.

    `control_replica_id` is the ONE field here that is not physics. A drift
    control has to execute rather than return the earlier row -- otherwise its
    "0.0% spread over six re-evaluations" measures the cache, which is what the
    first campaign actually reported. Giving each control replica its own
    identity mints its own trial row, so six controls leave six independently
    inspectable records instead of overwriting one. It is omitted from the
    payload entirely when absent, so every existing cache key is unchanged.
    """
    payload = {
        "contract": contract_hash,
        "code": code_fingerprint,
        "candidate": candidate,
        "derived": derived,
        "fidelity": fidelity,
        "events_total": events_total,
        "scenario": scenario,
        "seed_bank_id": seed_bank_id,
    }
    if control_replica_id is not None:
        payload["control_replica_id"] = str(control_replica_id)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# Serializes schema migration within one process. Several threads opening a
# fresh ledger at once each issued the same ALTER TABLE; the duplicate-column
# and locked-database retries below cover the cross-PROCESS case, and this
# removes the far commoner in-process one before it reaches SQLite.
_MIGRATE_LOCK = threading.Lock()

# Opening a database is not a read-only act: it can create tables, switch the
# journal mode and add columns, all of which take schema locks. Several
# processes (or several threads that got past the in-process lock) doing this at
# once is normal for this project -- every multi-threaded entry point opens one
# connection per thread. A busy timeout does not reliably cover a schema change,
# so these are retried explicitly.
def _retry_locked(fn, what="operation", attempts=12):
    delay = 0.05
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if attempt == attempts - 1:
                raise sqlite3.OperationalError(
                    f"{what}: database stayed locked after {attempts} attempts "
                    f"({exc})") from exc
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


class LedgerFrozen(RuntimeError):
    """The ledger is deliberately closed to new writers."""


class LeaseHeld(RuntimeError):
    """Another evaluator holds a live lease on this trial."""


FREEZE_SUFFIX = ".frozen"


def freeze_path(ledger_path):
    return os.path.abspath(ledger_path) + FREEZE_SUFFIX


def freeze_reason(ledger_path):
    """Why this ledger is closed to new writers, or None if it is open.

    Opening a `Ledger` runs `_migrate()`, which ALTERs the schema, and any
    evaluator that opens one can mint trials. Both are unwanted while a campaign
    is finishing under an older code identity: the running process holds its own
    connection and its own in-memory module, so it is unaffected, but a NEW
    process would migrate the schema underneath it and would miss every cache
    entry (its fingerprint differs), silently re-simulating days of work.

    A freeze file is the enforcement. It is checked before `sqlite3.connect`, so
    a frozen ledger is never even opened, and it cannot be undone by a signal --
    which is what made freezing the chain's shell unreliable.
    """
    path = freeze_path(ledger_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as handle:
            return handle.read().strip() or "no reason recorded"
    except OSError:
        return "no reason recorded (freeze file unreadable)"


class Ledger:
    def __init__(self, path, allow_frozen=False, read_only=False):
        """Open the ledger.

        `read_only=True` opens with SQLite's `mode=ro` and skips schema creation
        AND migration entirely. Reading must never mutate: two exit-gate tests
        read the v2 Stage 3 ledger for reference values, and because opening a
        ledger migrates it, simply running the test suite added ten columns to a
        historical campaign's database. A reader is also allowed past the freeze
        -- the freeze exists to keep WRITERS out.
        """
        self.path = os.path.abspath(path)
        self.read_only = bool(read_only)
        self._column_cache = {}
        if self.read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True,
                                        timeout=60.0)
            self.conn.row_factory = sqlite3.Row
            return
        reason = freeze_reason(self.path)
        if reason and not allow_frozen:
            raise LedgerFrozen(
                f"{self.path} is FROZEN and will not be opened.\n\n{reason}\n\n"
                f"Opening it would run the schema migration and would mint new "
                f"trials under the current code fingerprint. Remove "
                f"{freeze_path(self.path)} deliberately, after snapshotting, to "
                f"lift this.")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=60.0)
        self.conn.row_factory = sqlite3.Row
        # WAL so a reader (a monitoring script) cannot block the writer.
        # `journal_mode=WAL` is itself a schema-level operation and can collide
        # when several connections open a fresh database at once, so the whole
        # of setup is serialized in-process and retried on a busy database.
        with _MIGRATE_LOCK:
            _retry_locked(lambda: self.conn.execute("PRAGMA journal_mode=WAL"),
                          what="journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA busy_timeout=60000")
            _retry_locked(lambda: self.conn.executescript(SCHEMA),
                          what="schema creation")
            self._migrate(locked=True)
            self.conn.commit()

    # Columns added after the first campaigns. An existing ledger is migrated in
    # place rather than rebuilt: the v2 rows are the comparison baseline for v3
    # and must stay byte-for-byte what they were.
    _ADDED_COLUMNS = {
        "sub_runs": (("total_qps", "REAL"), ("per_electrode_qps", "TEXT"),
                     ("n_hits", "INTEGER")),
        "trials": (("objective_name", "TEXT"), ("objective_value", "REAL"),
                   ("objective_se", "REAL"), ("optimizer", "TEXT"),
                   ("iteration", "INTEGER"), ("acquisition", "REAL"),
                   # Proposal provenance (audit P1). A campaign that labels a
                   # Sobol initialisation point "bo_gp" attributes a random
                   # result to Bayesian optimization; these three columns make
                   # the attribution checkable instead of assumed.
                   ("proposal_source", "TEXT"),
                   ("optimizer_generation", "INTEGER"),
                   ("used_in_optimizer_update", "INTEGER"),
                   # Drift controls (audit P5): a non-physics replica tag that
                   # is excluded from candidate identity but recorded here, so
                   # six controls leave six inspectable rows.
                   ("control_replica_id", "TEXT"),
                   # Liveness (audit follow-up). A `running` row said nothing
                   # about whether anything was still working on it: the trial
                   # row is not touched while Geant4 workers run, and sub-run
                   # rows are only written at completion, so a healthy 29-hour
                   # trial looks identical to an abandoned one. Process
                   # visibility is not a substitute -- a PID namespace can hide
                   # a genuine host process. These make liveness a recorded
                   # fact rather than an inference.
                   ("lease_uuid", "TEXT"),          # unique per evaluator attempt
                   ("heartbeat_at", "REAL"),        # updated DURING long work
                   ("hostname", "TEXT"),
                   ("owner_pid", "INTEGER"),
                   ("owner_ppid", "INTEGER"),
                   ("scheduler_job_id", "TEXT"),
                   # Finding N2: `contract_hash` is the CAMPAIGN identity (it
                   # includes the campaign name, the search box, the optimizer
                   # and the resource limits). The cache key is built from the
                   # SIMULATION identity instead. Both are stored so a trial can
                   # be traced to its campaign and reused across campaigns.
                   ("simulation_identity_hash", "TEXT")),
    }

    def _migrate(self, locked=False):
        """Add any missing columns. Idempotent AND race-tolerant.

        Found by the projection-parallelism gate: several threads opening a
        fresh ledger at once each saw the column missing and each issued the
        ALTER, so all but the first died with "duplicate column name". Every
        multi-threaded entry point (`stage4_confirm.py`, the projection's
        verify, the campaign driver) opens one connection per thread, so this
        was reachable on the first open of any new ledger.

        SQLite has no ADD COLUMN IF NOT EXISTS, so the duplicate is caught and
        treated as success -- another connection already did the work.
        """
        ctx = contextlib.nullcontext() if locked else _MIGRATE_LOCK
        with ctx:                        # removes intra-process contention, which
            for table, columns in self._ADDED_COLUMNS.items():  # is the common case
                have = {row["name"] for row in
                        self.conn.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns:
                    if name in have:
                        continue
                    self._alter_with_retry(table, name, kind)

    def _alter_with_retry(self, table, name, kind, attempts=12):
        """One ADD COLUMN, tolerating both racing outcomes.

        `duplicate column name` -> another connection already did it: success.
        `database is locked`    -> another connection is mid-ALTER: back off and
                                   retry. ALTER takes an exclusive lock, and a
                                   busy timeout alone does not always cover a
                                   schema change, so this is retried explicitly.
        """
        delay = 0.05
        for attempt in range(attempts):
            try:
                with self.conn:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column name" in message:
                    return
                if "locked" not in message and "busy" not in message:
                    raise
                have = {row["name"] for row in
                        self.conn.execute(f"PRAGMA table_info({table})")}
                if name in have:
                    return
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 2.0)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- lookup -------------------------------------------------------------
    def find_by_cache_key(self, cache_key):
        cur = self.conn.execute("SELECT * FROM trials WHERE cache_key = ?", (cache_key,))
        return cur.fetchone()

    def sub_runs(self, trial_id):
        cur = self.conn.execute(
            "SELECT * FROM sub_runs WHERE trial_id = ? ORDER BY replica, position",
            (trial_id,))
        return cur.fetchall()

    def incomplete_sub_runs(self, trial_id):
        """Sub-runs that still need to run. Resume reruns exactly these."""
        cur = self.conn.execute(
            "SELECT * FROM sub_runs WHERE trial_id = ? AND status NOT IN (?, ?, ?) "
            "ORDER BY replica, position",
            (trial_id, STATUS_SUCCESS, STATUS_SUCCESS_ZERO, STATUS_TEARDOWN_SEGV))
        return cur.fetchall()

    # -- planning -----------------------------------------------------------
    def plan_trial(self, trial_id, campaign_id, cache_key, contract_hash,
                   code_fingerprint, candidate, derived, fidelity, events_total,
                   events_per_sub_run, n_positions, n_replicas, scenario,
                   seed_bank_id, run_dir, planned_sub_runs,
                   simulation_identity_hash=None):
        """Write the trial and every intended sub-run identity BEFORE launching.

        This is what makes completeness checkable: the expected set is recorded
        independently of the results, so a missing sub-run is a visible gap
        rather than an unnoticed absence.
        """
        now = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO trials (trial_id, campaign_id, cache_key, "
                "contract_hash, code_fingerprint, candidate, derived, fidelity, "
                "events_total, events_per_sub_run, n_positions, n_replicas, scenario, "
                "seed_bank_id, status, run_dir, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trial_id, campaign_id, cache_key, contract_hash,
                 json.dumps(code_fingerprint, sort_keys=True),
                 json.dumps(candidate, sort_keys=True),
                 json.dumps(derived, sort_keys=True), fidelity, events_total,
                 events_per_sub_run, n_positions, n_replicas,
                 json.dumps(scenario, sort_keys=True), seed_bank_id,
                 STATUS_PLANNED, run_dir, now, now),
            )
            if simulation_identity_hash is not None:
                self.conn.execute(
                    "UPDATE trials SET simulation_identity_hash=? WHERE trial_id=?",
                    (simulation_identity_hash, trial_id))
            for sr in planned_sub_runs:
                self.conn.execute(
                    "INSERT OR IGNORE INTO sub_runs (trial_id, replica, position, seed, "
                    "macro, hits_file, done_marker, status, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (trial_id, sr["replica"], sr["position_index"], sr.get("seed"),
                     sr.get("macro"), sr.get("hits_file"), sr.get("done_marker"),
                     STATUS_PLANNED, now),
                )

    # -- transitions --------------------------------------------------------
    def set_sub_run_status(self, trial_id, replica, position, status,
                           return_code=None, runtime_s=None, failure_reason=None):
        with self.conn:
            self.conn.execute(
                "UPDATE sub_runs SET status=?, return_code=?, runtime_s=?, "
                "failure_reason=?, updated_at=? WHERE trial_id=? AND replica=? AND position=?",
                (status, return_code, runtime_s, failure_reason, time.time(),
                 trial_id, replica, position),
            )

    def set_sub_run_score(self, trial_id, replica, position, total_qps,
                          per_electrode_qps=None, n_hits=None):
        """Per-(position, replica) QP count.

        These are the blocks every uncertainty statement in Stage 4 rests on:
        the counts are NOT Poisson (measured Fano ~ 5), so an interval has to
        come from the observed block spread, and a block-resolved objective
        (p90 over sites, worst electrode) cannot be recovered from a pooled sum.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE sub_runs SET total_qps=?, per_electrode_qps=?, n_hits=?, "
                "updated_at=? WHERE trial_id=? AND replica=? AND position=?",
                (total_qps,
                 json.dumps(per_electrode_qps) if per_electrode_qps is not None else None,
                 n_hits, time.time(), trial_id, replica, position),
            )

    def set_trial_result(self, trial_id, status, total_qps=None, qps_per_primary=None,
                         per_electrode_qps=None, runtime_s=None, peak_rss_gb=None,
                         failure_reason=None, lease_uuid=None):
        """Write a trial's outcome. Returns True if the write landed.

        Pass `lease_uuid` to make the write CONDITIONAL on still owning the
        trial (finding N3). An evaluator whose lease was taken over must not
        overwrite the new owner's result with its own stale one.
        """
        # A TERMINAL status releases the lease. Acquiring a lease without ever
        # releasing it was a real gap in the N3 work: a finished or abandoned
        # trial kept its lease for the full 1800 s expiry, so a legitimate
        # resume -- the whole point of the restartable design -- was refused
        # with "leased by a live evaluator" by an evaluator that no longer
        # existed. Measured 2026-09-04: relaunching the benchmark produced a
        # burst of constraint_rejected on exactly the trials just closed.
        # `running` and `planned` must NOT clear it; the owner is still working.
        terminal = status not in (STATUS_RUNNING, STATUS_PLANNED)
        args = [status, total_qps, qps_per_primary,
                json.dumps(per_electrode_qps) if per_electrode_qps is not None else None,
                runtime_s, peak_rss_gb, failure_reason, time.time(), trial_id]
        sql = ("UPDATE trials SET status=?, total_qps=?, qps_per_primary=?, "
               "per_electrode_qps=?, runtime_s=?, peak_rss_gb=?, failure_reason=?, "
               "updated_at=?" + (", lease_uuid=NULL" if terminal else "") +
               " WHERE trial_id=?")
        if lease_uuid is not None:
            sql += " AND (lease_uuid IS NULL OR lease_uuid=?)"
            args.append(lease_uuid)
        with self.conn:
            cur = self.conn.execute(sql, args)
        return cur.rowcount > 0

    # How long a lease survives without a heartbeat before another evaluator may
    # take it over. Generous next to the 120 s heartbeat: the cost of waiting is
    # a delayed restart, the cost of a premature takeover is two processes
    # writing the same run directory.
    LEASE_EXPIRY_SECONDS = 1800.0

    def claim_trial(self, trial_id, lease_uuid, hostname=None, owner_pid=None,
                    owner_ppid=None, scheduler_job_id=None, takeover=False):
        """Atomically take the lease on a trial. Returns True if acquired.

        Finding N3: this used to be an unconditional UPDATE, so two evaluators
        could hold the "same" lease and both execute the same cache key into the
        same run directory. The claim is now a compare-and-swap inside one
        IMMEDIATE transaction: it succeeds only if the trial is unleased, if the
        existing lease has expired, or if it is this same lease being refreshed.

        `takeover=True` is the explicit stale-run recovery path. It still
        refuses a lease that is being actively beaten -- recovery is for owners
        that died, not for owners that are merely slow.
        """
        now = time.time()
        cutoff = now - self.LEASE_EXPIRY_SECONDS
        # Explicit transaction rather than `with self.conn:` -- the context
        # manager opens its own transaction, so issuing BEGIN IMMEDIATE inside
        # it is a nested BEGIN, and the ROLLBACK below would fight its commit.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT lease_uuid, heartbeat_at, hostname, owner_pid FROM trials "
                "WHERE trial_id=?", (trial_id,)).fetchone()
            if row is None:
                raise KeyError(f"no such trial: {trial_id}")
            held = row["lease_uuid"]
            beat = row["heartbeat_at"] or 0.0
            fresh = held is not None and beat > cutoff
            if fresh and held != lease_uuid and not takeover:
                self.conn.rollback()
                return False
            if fresh and takeover:
                self.conn.rollback()
                raise LeaseHeld(
                    f"{trial_id} is still being beaten by lease {held} on "
                    f"{row['hostname']} (pid {row['owner_pid']}), "
                    f"{now - beat:.0f}s ago. Recovery is for a dead owner, not a "
                    f"slow one; wait for the lease to expire "
                    f"({self.LEASE_EXPIRY_SECONDS:.0f}s without a beat).")
            self.conn.execute(
                "UPDATE trials SET lease_uuid=?, heartbeat_at=?, hostname=?, "
                "owner_pid=?, owner_ppid=?, scheduler_job_id=?, updated_at=? "
                "WHERE trial_id=?",
                (lease_uuid, now, hostname, owner_pid, owner_ppid,
                 scheduler_job_id, now, trial_id))
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except sqlite3.Error:                             # already rolled back
                pass
            raise
        return True

    def heartbeat(self, trial_id, lease_uuid=None):
        """Mark the trial as still being worked on. Returns True if it landed.

        Only the holder of the lease may beat, so a stale second process cannot
        make an abandoned trial look alive. A False return means the lease was
        taken away -- the caller should stop, not keep computing.
        """
        with self.conn:
            if lease_uuid is None:
                cur = self.conn.execute(
                    "UPDATE trials SET heartbeat_at=? WHERE trial_id=?",
                    (time.time(), trial_id))
            else:
                cur = self.conn.execute(
                    "UPDATE trials SET heartbeat_at=? WHERE trial_id=? AND "
                    "lease_uuid=?", (time.time(), trial_id, lease_uuid))
        return cur.rowcount > 0

    def holds_lease(self, trial_id, lease_uuid):
        """True if `lease_uuid` still owns this trial."""
        row = self.conn.execute(
            "SELECT lease_uuid FROM trials WHERE trial_id=?", (trial_id,)).fetchone()
        return row is not None and row["lease_uuid"] == lease_uuid

    def set_optimizer_record(self, trial_id, objective_name=None, objective_value=None,
                             objective_se=None, optimizer=None, iteration=None,
                             acquisition=None, proposal_source=None,
                             optimizer_generation=None,
                             used_in_optimizer_update=None,
                             control_replica_id=None):
        """Optimizer bookkeeping (pipeline sec 7's ledger fields).

        Kept separate from `set_trial_result` on purpose: the physics result and
        the search bookkeeping are written by different layers, and a trial
        recovered from cache gets a new optimizer record without its physics
        being touched.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE trials SET objective_name=COALESCE(?, objective_name), "
                "objective_value=COALESCE(?, objective_value), "
                "objective_se=COALESCE(?, objective_se), "
                "optimizer=COALESCE(?, optimizer), iteration=COALESCE(?, iteration), "
                "acquisition=COALESCE(?, acquisition), "
                "proposal_source=COALESCE(?, proposal_source), "
                "optimizer_generation=COALESCE(?, optimizer_generation), "
                "used_in_optimizer_update=COALESCE(?, used_in_optimizer_update), "
                "control_replica_id=COALESCE(?, control_replica_id), "
                "updated_at=? WHERE trial_id=?",
                (objective_name, objective_value, objective_se, optimizer, iteration,
                 acquisition, proposal_source, optimizer_generation,
                 (None if used_in_optimizer_update is None
                  else int(bool(used_in_optimizer_update))),
                 control_replica_id, time.time(), trial_id),
            )

    # -- reporting ----------------------------------------------------------
    def observations(self, campaign_id=None, include_controls=False):
        """Only rows an optimizer may consume. A failure is never an observation.

        Drift controls are excluded by default: they are deliberate repeats of
        the frozen baseline, so replaying them into a surrogate on resume would
        train it on the same point six times and charge their cost to the
        search's event-efficiency curve.
        """
        sql = "SELECT * FROM trials WHERE status IN (?, ?)"
        args = [STATUS_SUCCESS, STATUS_SUCCESS_ZERO]
        # A read-only historical ledger is never migrated, so the column may not
        # exist -- and a ledger with no controls column has no controls to
        # exclude. Filtering blind would make the v2 database unreadable.
        if not include_controls and self.has_column("trials", "control_replica_id"):
            sql += " AND (control_replica_id IS NULL)"
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
        return self.conn.execute(sql, args).fetchall()

    def has_column(self, table, column):
        """True if `table` carries `column` in this database as it stands."""
        key = (table, column)
        if key not in self._column_cache:
            self._column_cache[key] = any(
                row[1] == column
                for row in self.conn.execute(f"PRAGMA table_info({table})"))
        return self._column_cache[key]

    def controls(self, campaign_id=None):
        """Every recorded drift control, newest last. The audit trail P5 asks for."""
        if not self.has_column("trials", "control_replica_id"):
            return []
        sql = "SELECT * FROM trials WHERE control_replica_id IS NOT NULL"
        args = []
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
        sql += " ORDER BY created_at"
        return self.conn.execute(sql, args).fetchall()

    def summary(self, campaign_id=None):
        sql = "SELECT status, COUNT(*) AS n FROM trials"
        args = []
        if campaign_id:
            sql += " WHERE campaign_id = ?"
            args.append(campaign_id)
        sql += " GROUP BY status ORDER BY n DESC"
        return {row["status"]: row["n"] for row in self.conn.execute(sql, args)}


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "stage3_trials.sqlite"
    with Ledger(path) as ledger:
        print(f"Ledger at {ledger.path}")
        print("  trials by status:", ledger.summary() or "(empty)")
