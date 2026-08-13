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

import hashlib
import json
import os
import sqlite3
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
    updated_at        REAL NOT NULL
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
    PRIMARY KEY (trial_id, replica, position),
    FOREIGN KEY (trial_id) REFERENCES trials(trial_id)
);

CREATE INDEX IF NOT EXISTS idx_trials_cache ON trials(cache_key);
CREATE INDEX IF NOT EXISTS idx_subruns_status ON sub_runs(trial_id, status);
"""


def compute_cache_key(contract_hash, code_fingerprint, candidate, derived,
                      fidelity, events_total, scenario, seed_bank_id):
    """Canonical hash of everything that would have to be identical for a
    re-run to reproduce this trial.

    Order matters and is preserved: the injection sites are an ordered
    quadrature, so a permuted site list is a different scenario even though the
    set of points is the same.
    """
    payload = json.dumps(
        {
            "contract": contract_hash,
            "code": code_fingerprint,
            "candidate": candidate,
            "derived": derived,
            "fidelity": fidelity,
            "events_total": events_total,
            "scenario": scenario,
            "seed_bank_id": seed_bank_id,
        },
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        # WAL so a reader (a monitoring script) cannot block the writer.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
                   seed_bank_id, run_dir, planned_sub_runs):
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

    def set_trial_result(self, trial_id, status, total_qps=None, qps_per_primary=None,
                         per_electrode_qps=None, runtime_s=None, peak_rss_gb=None,
                         failure_reason=None):
        with self.conn:
            self.conn.execute(
                "UPDATE trials SET status=?, total_qps=?, qps_per_primary=?, "
                "per_electrode_qps=?, runtime_s=?, peak_rss_gb=?, failure_reason=?, "
                "updated_at=? WHERE trial_id=?",
                (status, total_qps, qps_per_primary,
                 json.dumps(per_electrode_qps) if per_electrode_qps is not None else None,
                 runtime_s, peak_rss_gb, failure_reason, time.time(), trial_id),
            )

    # -- reporting ----------------------------------------------------------
    def observations(self, campaign_id=None):
        """Only rows an optimizer may consume. A failure is never an observation."""
        sql = ("SELECT * FROM trials WHERE status IN (?, ?)")
        args = [STATUS_SUCCESS, STATUS_SUCCESS_ZERO]
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
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
