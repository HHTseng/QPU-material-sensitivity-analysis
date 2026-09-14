from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

import archive as archive_module  # noqa: E402
from archive import (  # noqa: E402
    ARCHIVE_SCHEMA,
    MANIFEST_NAME,
    ArchiveConflict,
    ArchiveError,
    UnsafeArchivePath,
    create_archive,
    recover_archive,
    verify_archive,
    verify_tree,
)
from historical import (  # noqa: E402
    GitTree,
    HistoricalError,
    HistoricalDatabase,
    UnsafeHistoricalPath,
    build_inventory,
    read_inventory_sqlite,
    remap_absolute_path,
    sha256_file,
    write_inventory_sqlite,
)
from store import (  # noqa: E402
    AttemptBusy,
    ConflictError,
    LeaseBusy,
    LeaseLost,
    SchemaError,
    StaleAttempt,
    Store,
)


class TokenSource:
    def __init__(self) -> None:
        self.number = 0

    def __call__(self) -> str:
        self.number += 1
        return f"token-{self.number:03d}"


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "experiment.sqlite"
        self.tokens = TokenSource()
        self.store = Store.create(self.path, token_factory=self.tokens)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _planned(self, declared_tasks: int = 1):
        lease = self.store.acquire_controller("controller-a", 100, now=1)
        self.store.register_experiment(lease, "experiment", {"gun_eV": 0.01}, now=2)
        self.store.plan_simulation(
            lease, "simulation", "experiment", "simulation-key",
            {"carrier": "G4_Si"}, declared_tasks, now=2,
        )
        return lease

    def _complete_task(self, lease, task_id: str, index: int):
        self.store.plan_task(
            lease, task_id, "simulation", index, f"task-key-{index}",
            {"position": index}, now=3,
        )
        attempt = self.store.start_attempt(lease, task_id, now=4)
        self.store.finish_attempt(
            lease, attempt, completeness="complete", validity="valid",
            artifact_path=f"{task_id}.hits", artifact_sha256="a" * 64, now=5,
        )
        return attempt

    def test_foreign_keys_are_enabled_and_schema_is_exact(self) -> None:
        self.assertTrue(self.store.foreign_keys_enabled)
        lease = self.store.acquire_controller("writer", 10, now=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.plan_simulation(
                lease, "orphan", "absent", "key", {}, 0, now=2,
            )
        self.assertEqual(self.store.integrity_report()["foreign_key_violations"], ())

        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA user_version=999")
        connection.close()
        with self.assertRaises(SchemaError):
            Store.open(self.path)
        # Let tearDown close an already closed sqlite connection safely.

    def test_immutable_planning_is_idempotent_but_conflicts_fail(self) -> None:
        lease = self._planned()
        self.assertFalse(
            self.store.register_experiment(lease, "experiment", {"gun_eV": 0.01}, now=8)
        )
        with self.assertRaises(ConflictError):
            self.store.register_experiment(lease, "experiment", {"gun_eV": 0.02}, now=8)
        self.assertFalse(
            self.store.plan_simulation(
                lease, "simulation", "experiment", "simulation-key",
                {"carrier": "G4_Si"}, 1, now=8,
            )
        )

    def test_only_exact_declared_task_set_can_be_complete(self) -> None:
        lease = self._planned(declared_tasks=2)
        self._complete_task(lease, "task-0", 0)
        with self.assertRaises(ConflictError):
            self.store.finish_simulation(
                lease, "simulation", completeness="complete", validity="valid", now=6,
            )
        with self.assertRaises(ConflictError):
            self.store.plan_task(
                lease, "task-2", "simulation", 2, "outside", {}, now=6,
            )
        self._complete_task(lease, "task-1", 1)
        self.store.finish_simulation(
            lease, "simulation", completeness="complete", validity="valid", now=7,
        )
        with self.assertRaises(ConflictError):
            self.store.start_attempt(lease, "task-0", now=8)

    def test_failure_validity_and_completeness_are_orthogonal(self) -> None:
        lease = self._planned()
        self.store.plan_task(lease, "task", "simulation", 0, "task-key", {}, now=3)
        attempt = self.store.start_attempt(lease, "task", now=4)
        self.store.finish_attempt(
            lease, attempt, completeness="complete", validity="invalid",
            failure_kind="material_mismatch", failure_detail="carrier differs", now=5,
            artifact_path="task.hits", artifact_sha256="b" * 64,
        )
        row = self.store.task_row("task")
        self.assertEqual(row["completeness_state"], "complete")
        self.assertEqual(row["validity_state"], "invalid")
        self.assertEqual(row["failure_kind"], "material_mismatch")
        self.store.finish_simulation(
            lease, "simulation", completeness="complete", validity="invalid",
            failure_kind="material_mismatch", now=6,
        )
        self.assertTrue(self.store.record_observation(
            lease, "observation", "simulation", "analysis-v1", "total_qps", 12.0,
            task_id="task", attempt_token=attempt.token,
            observation_kind="measured", validity="invalid", now=7,
        ))

    def test_physical_zero_and_non_observation_are_distinct(self) -> None:
        lease = self._planned()
        attempt = self._complete_task(lease, "task", 0)
        self.store.finish_simulation(
            lease, "simulation", completeness="complete", validity="valid", now=6,
        )
        self.store.record_observation(
            lease, "zero", "simulation", "analysis-v1", "total_qps", 0.0,
            task_id="task", attempt_token=attempt.token,
            observation_kind="physical_zero", now=7,
        )
        with self.assertRaises(ValueError):
            self.store.record_observation(
                lease, "bad", "simulation", "analysis-v1", "total_qps", 0.0,
                observation_kind="non_observation", completeness="complete", now=7,
            )

        self.store.plan_simulation(
            lease, "censored-simulation", "experiment", "censored-key", {}, 1,
            now=8,
        )
        self.store.plan_task(
            lease, "censored-task", "censored-simulation", 0, "censored-task-key",
            {}, now=8,
        )
        censored_attempt = self.store.start_attempt(lease, "censored-task", now=8)
        self.store.finish_attempt(
            lease, censored_attempt, completeness="incomplete", validity="unchecked",
            failure_kind="timeout", now=8,
        )
        self.store.finish_simulation(
            lease, "censored-simulation", completeness="incomplete",
            validity="unchecked", failure_kind="timeout", now=8,
        )
        self.assertTrue(self.store.record_observation(
            lease, "censored", "censored-simulation", "analysis-v1", "runtime_limit",
            {"seconds": 3600}, task_id="censored-task",
            attempt_token=censored_attempt.token, observation_kind="right_censored",
            completeness="incomplete", validity="valid", now=8,
        ))
        with self.assertRaises(ConflictError):
            self.store.record_observation(
                lease, "fake-measurement", "censored-simulation", "analysis-v1",
                "total_qps", 0.0, observation_kind="physical_zero", now=8,
            )

    def test_controller_lease_and_attempt_token_recovery(self) -> None:
        lease_a = self._planned()
        self.store.plan_task(lease_a, "task", "simulation", 0, "task-key", {}, now=3)
        attempt_a = self.store.start_attempt(lease_a, "task", now=4)
        with self.assertRaises(AttemptBusy):
            self.store.start_attempt(lease_a, "task", now=5)
        with self.assertRaises(LeaseBusy):
            self.store.acquire_controller("controller-b", 100, now=50)

        lease_b = self.store.acquire_controller("controller-b", 100, now=102)
        with self.assertRaises(LeaseLost):
            self.store.register_experiment(lease_a, "late", {}, now=103)
        self.assertEqual(self.store.recover_running_attempts(lease_b, now=103), 1)
        attempt_b = self.store.start_attempt(lease_b, "task", now=104)
        with self.assertRaises(StaleAttempt):
            self.store.finish_attempt(
                lease_b, attempt_a, completeness="complete", validity="valid", now=105,
                artifact_path="old.hits", artifact_sha256="c" * 64,
            )
        self.store.finish_attempt(
            lease_b, attempt_b, completeness="complete", validity="valid", now=105,
            artifact_path="new.hits", artifact_sha256="d" * 64,
        )
        rows = self.store.attempt_rows("task")
        self.assertEqual([row["failure_kind"] for row in rows],
                         ["controller_lost", None])
        self.assertEqual(self.store.task_row("task")["completeness_state"], "complete")


def make_historical_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        PRAGMA foreign_keys=OFF;
        CREATE TABLE trials (
            trial_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            status TEXT NOT NULL,
            run_dir TEXT
        );
        CREATE TABLE sub_runs (
            trial_id TEXT NOT NULL REFERENCES trials(trial_id),
            replica INTEGER NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY(trial_id, replica, position)
        );
        INSERT INTO trials VALUES('complete','campaign','success','/old/root/run');
        INSERT INTO sub_runs VALUES('complete',0,0,'success');
        INSERT INTO sub_runs VALUES('missing-parent',0,0,'success');
    """)
    connection.commit()
    connection.close()


class HistoricalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reader_never_migrates_and_reports_foreign_key_orphan(self) -> None:
        database_path = self.root / "historical.sqlite"
        make_historical_database(database_path)
        before_hash = sha256_file(database_path)
        before_columns = sqlite3.connect(database_path).execute(
            "PRAGMA table_info(trials)"
        ).fetchall()
        with HistoricalDatabase(database_path, static_snapshot=True) as database:
            self.assertEqual([row["trial_id"] for row in database.trials()], ["complete"])
            self.assertEqual(len(database.integrity_report()["foreign_key_violations"]), 1)
            with self.assertRaises(sqlite3.OperationalError):
                database._connection.execute("ALTER TABLE trials ADD COLUMN forbidden TEXT")
        self.assertEqual(sha256_file(database_path), before_hash)
        after = sqlite3.connect(database_path)
        self.assertEqual(after.execute("PRAGMA table_info(trials)").fetchall(), before_columns)
        after.close()

    def test_logical_backup_is_explicit_atomic_and_source_preserving(self) -> None:
        database_path = self.root / "historical.sqlite"
        make_historical_database(database_path)
        original = sha256_file(database_path)
        with HistoricalDatabase(database_path, static_snapshot=True) as database:
            backup = database.backup_to(self.root / "snapshots" / "database.sqlite")
        self.assertEqual(sha256_file(database_path), original)
        check = sqlite3.connect(backup)
        self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(check.execute("SELECT count(*) FROM sub_runs").fetchone()[0], 2)
        check.close()

    def test_absolute_path_remap_refuses_prefix_and_traversal(self) -> None:
        mapped = remap_absolute_path("/old/root/runs/a", "/old/root", self.root)
        self.assertEqual(mapped, self.root / "runs" / "a")
        for value in ("/old/root-other/file", "/old/root/../escape", "relative/file"):
            with self.subTest(value=value), self.assertRaises(UnsafeHistoricalPath):
                remap_absolute_path(value, "/old/root", self.root)

    def test_git_aware_inventory_finds_present_modified_deleted_and_untracked(self) -> None:
        repository = self.root / "repository"
        data = repository / "data"
        data.mkdir(parents=True)
        (data / "same.txt").write_text("same", encoding="utf-8")
        (data / "modified.txt").write_text("old", encoding="utf-8")
        (data / "deleted.txt").write_text("recover me", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email",
                        "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name",
                        "Archive Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "data"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "fixture"],
                       check=True)
        (data / "modified.txt").write_text("new", encoding="utf-8")
        (data / "deleted.txt").unlink()
        (data / "untracked.txt").write_text("local", encoding="utf-8")

        inventory = build_inventory(data, git_tree=GitTree(repository), git_prefix="data")
        records = {record.path: record for record in inventory.records}
        self.assertEqual(records["same.txt"].git_state, "same")
        self.assertEqual(records["modified.txt"].git_state, "modified")
        self.assertEqual(records["deleted.txt"].availability, "git-only")
        self.assertEqual(records["deleted.txt"].sha256,
                         hashlib.sha256(b"recover me").hexdigest())
        self.assertEqual(records["untracked.txt"].git_state, "untracked")

        saved = write_inventory_sqlite(inventory, self.root / "inventory.sqlite")
        self.assertEqual(read_inventory_sqlite(saved).digest, inventory.digest)
        self.assertEqual(write_inventory_sqlite(inventory, saved), saved)
        (data / "another.txt").write_text("different", encoding="utf-8")
        changed = build_inventory(data, git_tree=GitTree(repository), git_prefix="data")
        with self.assertRaises(HistoricalError):
            write_inventory_sqlite(changed, saved)


def add_regular(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "empty.done").write_bytes(b"")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "hits.txt").write_bytes(b"header\n1,2,3\n")
        fixed_ns = 1_700_000_000_123_456_789
        for path in (self.source / "empty.done", self.source / "nested" / "hits.txt"):
            os.utime(path, ns=(fixed_ns, fixed_ns))
        self.members = ("empty.done", "nested/hits.txt")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_archive_is_deterministic_verified_and_idempotent(self) -> None:
        first = create_archive(self.source, reversed(self.members),
                               self.root / "first.tar.gz")
        second = create_archive(self.source, self.members, self.root / "second.tar.gz")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.members, 2)
        again = create_archive(self.source, self.members, self.root / "first.tar.gz")
        self.assertEqual(again.sha256, first.sha256)

    def test_recovery_is_atomic_exact_and_idempotent(self) -> None:
        receipt = create_archive(self.source, self.members, self.root / "trial.tar.gz")
        destination = recover_archive(
            receipt.path, self.root / "restored",
            expected_archive_sha256=receipt.sha256,
        )
        verify_tree(destination, receipt.manifest)
        self.assertEqual(recover_archive(receipt.path, destination), destination)
        (destination / "extra").write_text("not declared", encoding="utf-8")
        with self.assertRaises(ArchiveConflict):
            recover_archive(receipt.path, destination)

    def test_failed_publication_and_recovery_leave_no_partial_result(self) -> None:
        destination = self.root / "trial.tar.gz"
        real_verify = archive_module.verify_archive

        def fail_temporary(path, **kwargs):
            if str(path).endswith(".partial"):
                raise ArchiveError("injected verification failure")
            return real_verify(path, **kwargs)

        with mock.patch.object(archive_module, "verify_archive", side_effect=fail_temporary):
            with self.assertRaises(ArchiveError):
                create_archive(self.source, self.members, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".trial.tar.gz.*.partial")), [])

        receipt = create_archive(self.source, self.members, destination)
        restored = self.root / "restored"
        with mock.patch.object(archive_module, "verify_tree",
                               side_effect=ArchiveError("injected recovery failure")):
            with self.assertRaises(ArchiveError):
                recover_archive(receipt.path, restored)
        self.assertFalse(restored.exists())
        self.assertEqual(list(self.root.glob(".restored.partial-*")), [])

    def test_source_and_tar_traversal_or_symlinks_are_rejected(self) -> None:
        with self.assertRaises(UnsafeArchivePath):
            create_archive(self.source, ["../outside"], self.root / "bad.tar")
        outside = self.root / "outside"
        outside.write_text("secret", encoding="utf-8")
        os.symlink(outside, self.source / "link")
        with self.assertRaises(UnsafeArchivePath):
            create_archive(self.source, ["link"], self.root / "link.tar")

        malicious = self.root / "malicious.tar"
        with tarfile.open(malicious, "w") as tar:
            manifest = json.dumps({"schema": ARCHIVE_SCHEMA, "files": []}).encode()
            add_regular(tar, MANIFEST_NAME, manifest)
            add_regular(tar, "../escape", b"bad")
        with self.assertRaises(UnsafeArchivePath):
            verify_archive(malicious)

        linked = self.root / "linked.tar"
        with tarfile.open(linked, "w") as tar:
            manifest = json.dumps({"schema": ARCHIVE_SCHEMA, "files": []}).encode()
            add_regular(tar, MANIFEST_NAME, manifest)
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../outside"
            tar.addfile(info)
        with self.assertRaises(UnsafeArchivePath):
            verify_archive(linked)

    def test_corruption_and_manifest_missing_or_extra_members_are_rejected(self) -> None:
        receipt = create_archive(self.source, self.members, self.root / "good.tar.gz")
        corrupt = self.root / "corrupt.tar.gz"
        data = bytearray(receipt.path.read_bytes())
        data[len(data) // 2] ^= 0x01
        corrupt.write_bytes(data)
        with self.assertRaises(ArchiveError):
            verify_archive(corrupt)

        bad = self.root / "extra.tar"
        payload = b"a"
        entry = {
            "path": "a", "size": 1,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "mode": 0o644, "mtime_ns": 0,
        }
        with tarfile.open(bad, "w") as tar:
            manifest = json.dumps(
                {"schema": ARCHIVE_SCHEMA, "files": [entry]},
                sort_keys=True, separators=(",", ":"),
            ).encode()
            add_regular(tar, MANIFEST_NAME, manifest)
            add_regular(tar, "a", payload)
            add_regular(tar, "extra", b"extra")
        with self.assertRaises(ArchiveError):
            verify_archive(bad)

        missing = self.root / "missing.tar"
        with tarfile.open(missing, "w") as tar:
            add_regular(tar, MANIFEST_NAME, manifest)
        with self.assertRaises(ArchiveError):
            verify_archive(missing)


if __name__ == "__main__":
    unittest.main()
