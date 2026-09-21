"""Small, explicit SQLite store for material-scan experiments.

The database has one controller writer.  Worker processes never receive this
object; they return results carrying an attempt token.  The controller commits
such a result only when that token is still the task's active token.  A late
worker from an abandoned attempt therefore cannot overwrite a retry.

Schema creation is explicit through :meth:`Store.create`.  Opening an existing
database validates the schema version but never migrates it.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1

RUN_STATES = ("planned", "running", "terminal")
COMPLETENESS_STATES = ("pending", "complete", "incomplete")
VALIDITY_STATES = ("unchecked", "valid", "invalid")
OBSERVATION_KINDS = (
    "measured",
    "physical_zero",
    "right_censored",
    "non_observation",
)


class StoreError(RuntimeError):
    """Base class for store failures."""


class SchemaError(StoreError):
    """The database is absent, uninitialised, or has another schema version."""


class ConflictError(StoreError):
    """An immutable identifier was reused with different content."""


class LeaseBusy(StoreError):
    """Another unexpired controller owns the writer lease."""


class LeaseLost(StoreError):
    """The supplied controller lease is missing, expired, or superseded."""


class AttemptBusy(StoreError):
    """A task already has a live attempt."""


class StaleAttempt(StoreError):
    """A result belongs to an attempt which is no longer active."""


@dataclass(frozen=True)
class ControllerLease:
    owner: str
    token: str
    expires_at: float


@dataclass(frozen=True)
class AttemptToken:
    task_id: str
    token: str
    number: int


def canonical_json(value: Any) -> str:
    """Return the stable JSON form used for identity and equality checks."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: str | None) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


_SCHEMA = f"""
CREATE TABLE metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE controller_lease (
    singleton    INTEGER PRIMARY KEY CHECK (singleton = 1),
    owner        TEXT NOT NULL,
    token        TEXT NOT NULL UNIQUE,
    acquired_at  REAL NOT NULL,
    heartbeat_at REAL NOT NULL,
    expires_at   REAL NOT NULL
);

CREATE TABLE experiment (
    experiment_id      TEXT PRIMARY KEY,
    spec_hash          TEXT NOT NULL,
    spec_json          TEXT NOT NULL,
    run_state          TEXT NOT NULL DEFAULT 'planned'
                       CHECK (run_state IN {RUN_STATES}),
    completeness_state TEXT NOT NULL DEFAULT 'pending'
                       CHECK (completeness_state IN {COMPLETENESS_STATES}),
    validity_state     TEXT NOT NULL DEFAULT 'unchecked'
                       CHECK (validity_state IN {VALIDITY_STATES}),
    failure_kind       TEXT,
    failure_detail     TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);

CREATE TABLE simulation (
    simulation_id      TEXT PRIMARY KEY,
    experiment_id      TEXT NOT NULL REFERENCES experiment(experiment_id),
    simulation_key     TEXT NOT NULL,
    input_json         TEXT NOT NULL,
    declared_task_count INTEGER NOT NULL CHECK (declared_task_count >= 0),
    run_state          TEXT NOT NULL DEFAULT 'planned'
                       CHECK (run_state IN {RUN_STATES}),
    completeness_state TEXT NOT NULL DEFAULT 'pending'
                       CHECK (completeness_state IN {COMPLETENESS_STATES}),
    validity_state     TEXT NOT NULL DEFAULT 'unchecked'
                       CHECK (validity_state IN {VALIDITY_STATES}),
    failure_kind       TEXT,
    failure_detail     TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    UNIQUE (experiment_id, simulation_key)
);

CREATE TABLE task (
    task_id             TEXT PRIMARY KEY,
    simulation_id       TEXT NOT NULL REFERENCES simulation(simulation_id),
    task_index           INTEGER NOT NULL CHECK (task_index >= 0),
    task_key             TEXT NOT NULL,
    input_json           TEXT NOT NULL,
    run_state            TEXT NOT NULL DEFAULT 'planned'
                         CHECK (run_state IN {RUN_STATES}),
    completeness_state   TEXT NOT NULL DEFAULT 'pending'
                         CHECK (completeness_state IN {COMPLETENESS_STATES}),
    validity_state       TEXT NOT NULL DEFAULT 'unchecked'
                         CHECK (validity_state IN {VALIDITY_STATES}),
    failure_kind         TEXT,
    failure_detail       TEXT,
    active_attempt_token TEXT,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    UNIQUE (simulation_id, task_index),
    UNIQUE (simulation_id, task_key)
);

CREATE TABLE attempt (
    token               TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES task(task_id),
    attempt_number      INTEGER NOT NULL CHECK (attempt_number >= 1),
    controller_token    TEXT NOT NULL,
    run_state           TEXT NOT NULL DEFAULT 'running'
                        CHECK (run_state IN {RUN_STATES}),
    completeness_state  TEXT NOT NULL DEFAULT 'pending'
                        CHECK (completeness_state IN {COMPLETENESS_STATES}),
    validity_state      TEXT NOT NULL DEFAULT 'unchecked'
                        CHECK (validity_state IN {VALIDITY_STATES}),
    failure_kind        TEXT,
    failure_detail      TEXT,
    artifact_path       TEXT,
    artifact_sha256     TEXT,
    started_at          REAL NOT NULL,
    finished_at         REAL,
    UNIQUE (task_id, attempt_number)
);

CREATE TABLE observation (
    observation_id      TEXT PRIMARY KEY,
    simulation_id       TEXT NOT NULL REFERENCES simulation(simulation_id),
    task_id             TEXT REFERENCES task(task_id),
    attempt_token       TEXT REFERENCES attempt(token),
    analysis_key        TEXT NOT NULL,
    name                TEXT NOT NULL,
    observation_kind    TEXT NOT NULL
                        CHECK (observation_kind IN {OBSERVATION_KINDS}),
    value_json          TEXT NOT NULL,
    completeness_state  TEXT NOT NULL
                        CHECK (completeness_state IN {COMPLETENESS_STATES}),
    validity_state      TEXT NOT NULL
                        CHECK (validity_state IN {VALIDITY_STATES}),
    failure_kind        TEXT,
    failure_detail      TEXT,
    created_at          REAL NOT NULL
);

CREATE INDEX task_by_simulation ON task(simulation_id, task_index);
CREATE INDEX attempt_by_task ON attempt(task_id, attempt_number);
CREATE INDEX observation_by_simulation ON observation(simulation_id, analysis_key);
"""


def _require_nonempty(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _check_assessment(completeness: str, validity: str,
                      failure_kind: str | None) -> None:
    if completeness not in COMPLETENESS_STATES:
        raise ValueError(f"unknown completeness state: {completeness!r}")
    if validity not in VALIDITY_STATES:
        raise ValueError(f"unknown validity state: {validity!r}")
    if failure_kind is not None:
        _require_nonempty("failure_kind", failure_kind)


def _schema_hash(connection: sqlite3.Connection) -> str:
    rows = [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]
    return content_hash(rows)


class Store:
    """Versioned experiment store with an application-level writer lease."""

    def __init__(self, path: Path, connection: sqlite3.Connection, *,
                 read_only: bool, clock: Callable[[], float] = time.time,
                 token_factory: Callable[[], str] | None = None):
        self.path = Path(path).resolve()
        self._connection = connection
        self.read_only = read_only
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_hex(16))

    @classmethod
    def create(cls, path: os.PathLike[str] | str, *,
               clock: Callable[[], float] = time.time,
               token_factory: Callable[[], str] | None = None) -> "Store":
        """Create a new store; never overwrite or upgrade an existing file."""

        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise SchemaError(f"store already exists: {target}") from exc
        os.close(descriptor)
        connection: sqlite3.Connection | None = None
        try:
            connection = cls._connect(target, read_only=False)
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                raise SchemaError("new store could not enable WAL journal mode")
            connection.executescript(_SCHEMA)
            connection.executemany(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (("schema_version", str(SCHEMA_VERSION)),
                 ("schema_hash", _schema_hash(connection))),
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
            return cls(target, connection, read_only=False, clock=clock,
                       token_factory=token_factory)
        except Exception:
            if connection is not None:
                connection.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(str(target) + suffix)
                except FileNotFoundError:
                    pass
            raise

    @classmethod
    def open(cls, path: os.PathLike[str] | str, *, read_only: bool = False,
             clock: Callable[[], float] = time.time,
             token_factory: Callable[[], str] | None = None) -> "Store":
        """Open an existing store after exact-version validation.

        This method deliberately has no migration path.  A future migration is
        a separate stopped-store command which produces a new verified file.
        """

        target = Path(path).resolve()
        if not target.is_file():
            raise SchemaError(f"store does not exist: {target}")
        connection = cls._connect(target, read_only=read_only)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if (version != SCHEMA_VERSION
                    or metadata.get("schema_version") != str(SCHEMA_VERSION)):
                raise SchemaError(
                    f"schema version {version}/{metadata.get('schema_version')} "
                    f"does not equal required {SCHEMA_VERSION}"
                )
            actual_schema_hash = _schema_hash(connection)
            if metadata.get("schema_hash") != actual_schema_hash:
                raise SchemaError("store schema does not match its recorded schema hash")
        except Exception:
            connection.close()
            raise
        return cls(target, connection, read_only=read_only, clock=clock,
                   token_factory=token_factory)

    @staticmethod
    def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            uri = f"file:{path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None,
                                         timeout=30.0)
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(path, isolation_level=None, timeout=30.0)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=30000")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            connection.close()
            raise SchemaError("SQLite foreign-key enforcement could not be enabled")
        return connection

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    @property
    def foreign_keys_enabled(self) -> bool:
        return self._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def integrity_report(self) -> Mapping[str, Any]:
        return {
            "integrity": self._connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": tuple(
                tuple(row) for row in self._connection.execute("PRAGMA foreign_key_check")
            ),
        }

    def _require_writable(self) -> None:
        if self.read_only:
            raise StoreError("store was opened read-only")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._require_writable()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _require_lease(self, connection: sqlite3.Connection,
                       lease: ControllerLease, now: float) -> None:
        row = connection.execute(
            "SELECT owner,token,expires_at FROM controller_lease WHERE singleton=1"
        ).fetchone()
        if (row is None or row["owner"] != lease.owner or row["token"] != lease.token
                or float(row["expires_at"]) <= now):
            raise LeaseLost("controller lease is absent, expired, or has been replaced")

    def acquire_controller(self, owner: str, ttl_s: float, *,
                           now: float | None = None) -> ControllerLease:
        _require_nonempty("owner", owner)
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        moment = self._clock() if now is None else float(now)
        token = self._token_factory()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT owner,expires_at FROM controller_lease WHERE singleton=1"
            ).fetchone()
            if row is not None and float(row["expires_at"]) > moment:
                raise LeaseBusy(
                    f"controller {row['owner']!r} owns the lease until "
                    f"{float(row['expires_at']):.6f}"
                )
            connection.execute("DELETE FROM controller_lease WHERE singleton=1")
            expires = moment + float(ttl_s)
            connection.execute(
                "INSERT INTO controller_lease "
                "(singleton,owner,token,acquired_at,heartbeat_at,expires_at) "
                "VALUES(1,?,?,?,?,?)",
                (owner, token, moment, moment, expires),
            )
        return ControllerLease(owner, token, expires)

    def renew_controller(self, lease: ControllerLease, ttl_s: float, *,
                         now: float | None = None) -> ControllerLease:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        moment = self._clock() if now is None else float(now)
        with self._transaction() as connection:
            self._require_lease(connection, lease, moment)
            expires = moment + float(ttl_s)
            changed = connection.execute(
                "UPDATE controller_lease SET heartbeat_at=?,expires_at=? "
                "WHERE singleton=1 AND owner=? AND token=?",
                (moment, expires, lease.owner, lease.token),
            ).rowcount
            if changed != 1:
                raise LeaseLost("controller lease changed during renewal")
        return ControllerLease(lease.owner, lease.token, expires)

    def release_controller(self, lease: ControllerLease) -> bool:
        """Release only the matching lease; stale owners cannot release a retry."""

        with self._transaction() as connection:
            changed = connection.execute(
                "DELETE FROM controller_lease WHERE singleton=1 AND owner=? AND token=?",
                (lease.owner, lease.token),
            ).rowcount
        return changed == 1

    def clear_abandoned_controller(
        self,
        expected_owner: str,
        expected_token: str,
        inactive_s: float,
        *,
        now: float | None = None,
    ) -> bool:
        """Fence one exact controller after its heartbeat has stopped."""

        _require_nonempty("expected_owner", expected_owner)
        _require_nonempty("expected_token", expected_token)
        if inactive_s <= 0:
            raise ValueError("inactive_s must be positive")
        moment = self._clock() if now is None else float(now)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT owner,token,heartbeat_at FROM controller_lease WHERE singleton=1"
            ).fetchone()
            if row is None:
                return False
            if row["owner"] != expected_owner or row["token"] != expected_token:
                raise LeaseBusy("the current controller does not match the expected owner and token")
            inactive_for = moment - float(row["heartbeat_at"])
            if inactive_for < float(inactive_s):
                raise LeaseBusy(
                    f"controller heartbeat is only {inactive_for:.1f} seconds old; "
                    f"required {float(inactive_s):.1f}"
                )
            changed = connection.execute(
                "DELETE FROM controller_lease "
                "WHERE singleton=1 AND owner=? AND token=? AND heartbeat_at=?",
                (expected_owner, expected_token, float(row["heartbeat_at"])),
            ).rowcount
        return changed == 1

    def _leased_transaction(self, lease: ControllerLease, now: float | None = None):
        moment = self._clock() if now is None else float(now)

        @contextmanager
        def guarded() -> Iterator[tuple[sqlite3.Connection, float]]:
            with self._transaction() as connection:
                self._require_lease(connection, lease, moment)
                yield connection, moment

        return guarded()

    @staticmethod
    def _immutable_insert(connection: sqlite3.Connection, table: str,
                          id_column: str, identifier: str,
                          values: Mapping[str, Any], compare: Sequence[str]) -> bool:
        row = connection.execute(
            f"SELECT * FROM {table} WHERE {id_column}=?", (identifier,)
        ).fetchone()
        if row is not None:
            different = [column for column in compare if row[column] != values[column]]
            if different:
                raise ConflictError(
                    f"{table} {identifier!r} already exists with different "
                    f"{', '.join(different)}"
                )
            return False
        columns = (id_column, *values.keys())
        placeholders = ",".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
            (identifier, *values.values()),
        )
        return True

    def register_experiment(self, lease: ControllerLease, experiment_id: str,
                            spec: Any, *, now: float | None = None) -> bool:
        _require_nonempty("experiment_id", experiment_id)
        spec_json = canonical_json(spec)
        moment = self._clock() if now is None else float(now)
        values = {
            "spec_hash": hashlib.sha256(spec_json.encode()).hexdigest(),
            "spec_json": spec_json,
            "created_at": moment,
            "updated_at": moment,
        }
        with self._leased_transaction(lease, moment) as (connection, _):
            return self._immutable_insert(
                connection, "experiment", "experiment_id", experiment_id,
                values, ("spec_hash", "spec_json"),
            )

    def plan_simulation(self, lease: ControllerLease, simulation_id: str,
                        experiment_id: str, simulation_key: str, inputs: Any,
                        declared_task_count: int, *,
                        now: float | None = None) -> bool:
        for label, value in (("simulation_id", simulation_id),
                             ("experiment_id", experiment_id),
                             ("simulation_key", simulation_key)):
            _require_nonempty(label, value)
        if int(declared_task_count) < 0:
            raise ValueError("declared_task_count must be non-negative")
        moment = self._clock() if now is None else float(now)
        values = {
            "experiment_id": experiment_id,
            "simulation_key": simulation_key,
            "input_json": canonical_json(inputs),
            "declared_task_count": int(declared_task_count),
            "created_at": moment,
            "updated_at": moment,
        }
        with self._leased_transaction(lease, moment) as (connection, _):
            same_key = connection.execute(
                "SELECT simulation_id FROM simulation "
                "WHERE experiment_id=? AND simulation_key=?",
                (experiment_id, simulation_key),
            ).fetchone()
            if same_key is not None and same_key["simulation_id"] != simulation_id:
                raise ConflictError(
                    f"simulation key already belongs to {same_key['simulation_id']!r}"
                )
            return self._immutable_insert(
                connection, "simulation", "simulation_id", simulation_id,
                values, ("experiment_id", "simulation_key", "input_json",
                         "declared_task_count"),
            )

    def plan_task(self, lease: ControllerLease, task_id: str, simulation_id: str,
                  task_index: int, task_key: str, inputs: Any, *,
                  now: float | None = None) -> bool:
        for label, value in (("task_id", task_id),
                             ("simulation_id", simulation_id),
                             ("task_key", task_key)):
            _require_nonempty(label, value)
        if int(task_index) < 0:
            raise ValueError("task_index must be non-negative")
        moment = self._clock() if now is None else float(now)
        values = {
            "simulation_id": simulation_id,
            "task_index": int(task_index),
            "task_key": task_key,
            "input_json": canonical_json(inputs),
            "created_at": moment,
            "updated_at": moment,
        }
        with self._leased_transaction(lease, moment) as (connection, _):
            simulation = connection.execute(
                "SELECT declared_task_count,completeness_state FROM simulation "
                "WHERE simulation_id=?", (simulation_id,)
            ).fetchone()
            if simulation is None:
                raise KeyError(simulation_id)
            if simulation["completeness_state"] == "complete":
                raise ConflictError("cannot add a task to a complete simulation")
            if int(task_index) >= int(simulation["declared_task_count"]):
                raise ConflictError(
                    f"task index {task_index} is outside the declared set of "
                    f"{simulation['declared_task_count']} tasks"
                )
            same_slot = connection.execute(
                "SELECT task_id,task_index,task_key FROM task WHERE simulation_id=? "
                "AND (task_index=? OR task_key=?)",
                (simulation_id, int(task_index), task_key),
            ).fetchone()
            if same_slot is not None and same_slot["task_id"] != task_id:
                raise ConflictError(
                    f"task index or key already belongs to {same_slot['task_id']!r}"
                )
            return self._immutable_insert(
                connection, "task", "task_id", task_id, values,
                ("simulation_id", "task_index", "task_key", "input_json"),
            )

    def start_attempt(self, lease: ControllerLease, task_id: str, *,
                      now: float | None = None) -> AttemptToken:
        _require_nonempty("task_id", task_id)
        moment = self._clock() if now is None else float(now)
        with self._leased_transaction(lease, moment) as (connection, _):
            task = connection.execute(
                "SELECT task.*,simulation.completeness_state AS simulation_completeness "
                "FROM task JOIN simulation USING(simulation_id) WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if task["active_attempt_token"] is not None:
                raise AttemptBusy(f"task {task_id!r} already has an active attempt")
            if task["simulation_completeness"] == "complete":
                raise ConflictError("cannot retry a task after its simulation is complete")
            number = connection.execute(
                "SELECT COALESCE(MAX(attempt_number),0)+1 FROM attempt WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            token = self._token_factory()
            connection.execute(
                "INSERT INTO attempt "
                "(token,task_id,attempt_number,controller_token,run_state,started_at) "
                "VALUES(?,?,?,?, 'running', ?)",
                (token, task_id, number, lease.token, moment),
            )
            changed = connection.execute(
                "UPDATE task SET run_state='running',completeness_state='pending',"
                "validity_state='unchecked',failure_kind=NULL,failure_detail=NULL,"
                "active_attempt_token=?,updated_at=? "
                "WHERE task_id=? AND active_attempt_token IS NULL",
                (token, moment, task_id),
            ).rowcount
            if changed != 1:
                raise AttemptBusy(f"task {task_id!r} changed while starting")
        return AttemptToken(task_id, token, int(number))

    def finish_attempt(self, lease: ControllerLease, attempt: AttemptToken, *,
                       completeness: str, validity: str,
                       failure_kind: str | None = None,
                       failure_detail: str | None = None,
                       artifact_path: str | None = None,
                       artifact_sha256: str | None = None,
                       now: float | None = None) -> None:
        if completeness == "pending":
            raise ValueError("a terminal attempt cannot remain pending")
        _check_assessment(completeness, validity, failure_kind)
        if completeness == "complete":
            _require_nonempty("artifact_path", artifact_path or "")
            if not _is_sha256(artifact_sha256):
                raise ValueError("a complete attempt requires a lowercase SHA-256")
        moment = self._clock() if now is None else float(now)
        with self._leased_transaction(lease, moment) as (connection, _):
            changed = connection.execute(
                "UPDATE attempt SET run_state='terminal',completeness_state=?,"
                "validity_state=?,failure_kind=?,failure_detail=?,artifact_path=?,"
                "artifact_sha256=?,finished_at=? "
                "WHERE token=? AND task_id=? AND run_state='running'",
                (completeness, validity, failure_kind, failure_detail,
                 artifact_path, artifact_sha256, moment, attempt.token,
                 attempt.task_id),
            ).rowcount
            if changed != 1:
                raise StaleAttempt(f"attempt {attempt.token!r} is not running")
            changed = connection.execute(
                "UPDATE task SET run_state='terminal',completeness_state=?,"
                "validity_state=?,failure_kind=?,failure_detail=?,"
                "active_attempt_token=NULL,updated_at=? "
                "WHERE task_id=? AND active_attempt_token=?",
                (completeness, validity, failure_kind, failure_detail, moment,
                 attempt.task_id, attempt.token),
            ).rowcount
            if changed != 1:
                raise StaleAttempt(
                    f"attempt {attempt.token!r} was superseded before commit"
                )

    def recover_running_attempts(self, lease: ControllerLease, *,
                                 reason: str = "controller_lost",
                                 detail: str | None = None,
                                 now: float | None = None) -> int:
        """Close abandoned attempts after a new controller acquires the lease."""

        _require_nonempty("reason", reason)
        moment = self._clock() if now is None else float(now)
        with self._leased_transaction(lease, moment) as (connection, _):
            rows = connection.execute(
                "SELECT token,task_id FROM attempt WHERE run_state='running' "
                "ORDER BY task_id,attempt_number"
            ).fetchall()
            recovered = 0
            for row in rows:
                token, task_id = row["token"], row["task_id"]
                changed = connection.execute(
                    "UPDATE attempt SET run_state='terminal',"
                    "completeness_state='incomplete',validity_state='unchecked',"
                    "failure_kind=?,failure_detail=?,finished_at=? "
                    "WHERE token=? AND run_state='running'",
                    (reason, detail, moment, token),
                ).rowcount
                if changed != 1:
                    continue
                connection.execute(
                    "UPDATE task SET run_state='terminal',"
                    "completeness_state='incomplete',validity_state='unchecked',"
                    "failure_kind=?,failure_detail=?,active_attempt_token=NULL,"
                    "updated_at=? WHERE task_id=? AND active_attempt_token=?",
                    (reason, detail, moment, task_id, token),
                )
                recovered += 1
        return recovered

    def finish_simulation(self, lease: ControllerLease, simulation_id: str, *,
                          completeness: str, validity: str,
                          failure_kind: str | None = None,
                          failure_detail: str | None = None,
                          now: float | None = None) -> None:
        """Set a terminal assessment, enforcing the exact declared task set.

        Completeness and validity remain separate.  A simulation may have all
        files present yet be invalid physics.  Conversely, no simulation can be
        marked complete merely because the tasks that happen to exist finished.
        """

        if completeness == "pending":
            raise ValueError("a terminal simulation cannot remain pending")
        _check_assessment(completeness, validity, failure_kind)
        moment = self._clock() if now is None else float(now)
        with self._leased_transaction(lease, moment) as (connection, _):
            simulation = connection.execute(
                "SELECT declared_task_count FROM simulation WHERE simulation_id=?",
                (simulation_id,),
            ).fetchone()
            if simulation is None:
                raise KeyError(simulation_id)
            tasks = connection.execute(
                "SELECT task_index,completeness_state,validity_state "
                "FROM task WHERE simulation_id=? ORDER BY task_index",
                (simulation_id,),
            ).fetchall()
            if completeness == "complete":
                declared = int(simulation["declared_task_count"])
                indices = [int(row["task_index"]) for row in tasks]
                if indices != list(range(declared)):
                    raise ConflictError(
                        f"simulation declares tasks 0..{declared - 1}, got {indices}"
                    )
                if any(row["completeness_state"] != "complete" for row in tasks):
                    raise ConflictError("simulation has an incomplete declared task")
                if validity == "valid" and any(
                        row["validity_state"] != "valid" for row in tasks):
                    raise ConflictError("a valid simulation contains an unchecked or invalid task")
            changed = connection.execute(
                "UPDATE simulation SET run_state='terminal',completeness_state=?,"
                "validity_state=?,failure_kind=?,failure_detail=?,updated_at=? "
                "WHERE simulation_id=?",
                (completeness, validity, failure_kind, failure_detail, moment,
                 simulation_id),
            ).rowcount
            if changed != 1:
                raise KeyError(simulation_id)

    def record_observation(self, lease: ControllerLease, observation_id: str,
                           simulation_id: str, analysis_key: str, name: str,
                           value: Any, *, task_id: str | None = None,
                           attempt_token: str | None = None,
                           observation_kind: str = "measured",
                           completeness: str = "complete",
                           validity: str = "valid",
                           failure_kind: str | None = None,
                           failure_detail: str | None = None,
                           now: float | None = None) -> bool:
        for label, item in (("observation_id", observation_id),
                            ("simulation_id", simulation_id),
                            ("analysis_key", analysis_key), ("name", name)):
            _require_nonempty(label, item)
        _check_assessment(completeness, validity, failure_kind)
        if observation_kind not in OBSERVATION_KINDS:
            raise ValueError(f"unknown observation kind: {observation_kind!r}")
        if observation_kind in ("measured", "physical_zero") and completeness != "complete":
            raise ValueError("a measured or physical-zero observation must be complete")
        if observation_kind in ("right_censored", "non_observation") and completeness != "incomplete":
            raise ValueError("a censored or non-observation record must be incomplete")
        moment = self._clock() if now is None else float(now)
        values = {
            "simulation_id": simulation_id,
            "task_id": task_id,
            "attempt_token": attempt_token,
            "analysis_key": analysis_key,
            "name": name,
            "observation_kind": observation_kind,
            "value_json": canonical_json(value),
            "completeness_state": completeness,
            "validity_state": validity,
            "failure_kind": failure_kind,
            "failure_detail": failure_detail,
            "created_at": moment,
        }
        with self._leased_transaction(lease, moment) as (connection, _):
            simulation = connection.execute(
                "SELECT completeness_state FROM simulation WHERE simulation_id=?",
                (simulation_id,),
            ).fetchone()
            if simulation is None:
                raise KeyError(simulation_id)
            expected_complete = observation_kind in ("measured", "physical_zero")
            if ((simulation["completeness_state"] == "complete")
                    != expected_complete):
                raise ConflictError(
                    "observation kind disagrees with simulation completeness"
                )
            if task_id is not None:
                task = connection.execute(
                    "SELECT simulation_id,completeness_state FROM task WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                if task is None or task["simulation_id"] != simulation_id:
                    raise ConflictError("observation task does not belong to simulation")
                if ((task["completeness_state"] == "complete")
                        != expected_complete):
                    raise ConflictError(
                        "observation kind disagrees with task completeness"
                    )
            if attempt_token is not None:
                attempt_row = connection.execute(
                    "SELECT task_id,run_state,completeness_state FROM attempt WHERE token=?",
                    (attempt_token,),
                ).fetchone()
                if (attempt_row is None or attempt_row["run_state"] != "terminal"
                        or ((attempt_row["completeness_state"] == "complete")
                            != expected_complete)
                        or task_id is None or attempt_row["task_id"] != task_id):
                    raise ConflictError("observation kind disagrees with its attempt outcome")
            return self._immutable_insert(
                connection, "observation", "observation_id", observation_id,
                values,
                ("simulation_id", "task_id", "attempt_token", "analysis_key",
                 "name", "value_json", "completeness_state", "validity_state",
                 "observation_kind", "failure_kind", "failure_detail"),
            )

    def task_row(self, task_id: str) -> Mapping[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM task WHERE task_id=?", (task_id,)
        ).fetchone()
        return None if row is None else dict(row)

    def attempt_rows(self, task_id: str) -> list[Mapping[str, Any]]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM attempt WHERE task_id=? ORDER BY attempt_number", (task_id,)
        )]
