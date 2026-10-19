"""Inputs, execution, restart, ownership, and preservation checks."""

import copy
import csv
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest

import numpy as np
import yaml

import scan
import physics
import analysis
import records


ROOT = Path(__file__).resolve().parents[1]


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = records.Results(self.root / "results.sqlite")
        self.database.plan("example", "candidate", {"x": 1}, [{"task_id": "a"}, {"task_id": "b"}])

    def tearDown(self):
        self.database.close()
        self.directory.cleanup()

    def test_exact_task_set_and_conflicting_declarations(self):
        self.database.plan("example", "candidate", {"x": 1}, [{"task_id": "a"}, {"task_id": "b"}])
        with self.assertRaises(ValueError):
            self.database.plan("example", "candidate", {"x": 1}, [{"task_id": "a"}])
        with self.assertRaises(ValueError):
            self.database.plan("example", "candidate", {"x": 2}, [{"task_id": "a"}, {"task_id": "b"}])
        token = self.database.start("a")
        self.database.finish("a", token, "complete", {"per_electrode_qps": [0, 2]})
        self.assertFalse(self.database.completed("candidate"))
        with self.assertRaises(ValueError):
            self.database.observation("candidate", {"mean": 1})

    def test_late_attempt_cannot_replace_new_result(self):
        old = self.database.start("a")
        self.database.finish("a", old, "interrupted", {"reason": "test interruption"})
        current = self.database.start("a")
        with self.assertRaises(RuntimeError):
            self.database.finish("a", old, "complete", {"qps": [999]})
        self.database.finish("a", current, "complete", {"qps": [1]})
        self.assertEqual(self.database.tasks("candidate")[0]["result"]["qps"], [1])

    def test_timeout_is_absent_measurement_and_header_only_is_zero(self):
        token = self.database.start("a")
        failed = self.database.finish("a", token, "timeout", {"qps": [0], "total_qps": 0, "seconds": 5})
        self.assertFalse(failed["measured"])
        self.assertNotIn("qps", failed)
        self.assertNotIn("total_qps", failed)
        with self.assertRaises(ValueError):
            self.database.observation("candidate", 0, kind="physical_zero")
        for task in self.database.tasks("candidate"):
            token = self.database.start(task["task_id"])
            self.database.finish(task["task_id"], token, "complete", {"qps": [0]})
        self.assertTrue(self.database.completed("candidate"))
        self.database.observation("candidate", {"mean": 0, "standard_error": 0})
        self.assertEqual(self.database.read("observations")[0]["kind"], "measured")

    def test_writer_is_exclusive_and_thread_bound(self):
        with self.assertRaises(RuntimeError):
            records.Results(self.database.path)
        failures = []
        def attempt():
            try:
                self.database.start("a")
            except RuntimeError as error:
                failures.append(str(error))
        thread = threading.Thread(target=attempt)
        thread.start(); thread.join()
        self.assertEqual(len(failures), 1)

    def test_database_alias_cannot_bypass_the_live_writer(self):
        alias = self.root / "alias.sqlite"
        alias.symlink_to(self.database.path)
        old = self.database.start("a")
        with self.assertRaises((ValueError, RuntimeError)):
            with records.Results(alias):
                pass
        self.assertEqual(self.database.tasks("candidate")[0]["active_token"], old)

    def test_writable_hardlinked_database_is_rejected(self):
        alias = self.root / "hardlinked.sqlite"
        os.link(self.database.path, alias)
        with self.assertRaises(ValueError):
            records.Results(alias)
        with records.Results(alias, create=False) as reader:
            self.assertEqual(reader.tasks("candidate")[0]["task_id"], "a")

    def test_restart_discards_old_ownership(self):
        old = self.database.start("a")
        self.database.close()
        self.database = records.Results(self.root / "results.sqlite")
        current = self.database.start("a")
        self.assertNotEqual(old, current)
        with self.assertRaises(RuntimeError):
            self.database.finish("a", old, "complete", {"qps": [1]})
        self.assertEqual([row["status"] for row in self.database.rows("attempts")], ["abandoned", "running"])

    def test_file_checksum_invalidates_cache_without_erasing_history(self):
        for task in self.database.tasks("candidate"):
            path = self.root / (task["task_id"] + ".csv")
            path.write_text("original")
            token = self.database.start(task["task_id"])
            self.database.finish(task["task_id"], token, "complete", {
                "qps": [1], "output": str(path), "sha256": records.sha256_file(path)})
        self.database.observation("candidate", {"mean": 1})
        (self.root / "a.csv").write_text("changed")
        self.assertFalse(self.database.completed("candidate"))
        self.database.revise("a", reason="saved file checksum differs")
        self.assertEqual(len(self.database.read("observations")), 0)
        self.assertEqual(len(self.database.rows("observation_history")), 1)
        self.assertEqual(len(self.database.rows("attempts")), 2)
        self.assertIsInstance(self.database.start("a"), str)

    def test_simulation_analysis_and_search_hashes_are_separate(self):
        experiment = {"id": "separate", "simulation_hash": records.content_hash({"energy": .01}),
                      "analysis_hash": records.content_hash({"objective": "weighted"}),
                      "search_hash": records.content_hash({"method": "sobol"})}
        self.database.plan(experiment, "other", {}, [{"task_id": "shared"}])
        row = self.database.rows("experiments")[-1]
        self.assertEqual(row["simulation_hash"], experiment["simulation_hash"])
        self.assertNotEqual(row["simulation_hash"], row["analysis_hash"])
        self.assertNotEqual(row["analysis_hash"], row["search_hash"])

    def test_atomic_json_does_not_overwrite(self):
        path = self.root / "result.json"
        records.write_json(path, {"a": np.array([1, 2])})
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            records.write_json(path, {"a": [3]})
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            records.write_json(self.root / "nan.json", {"a": float("nan")})
        self.assertFalse((self.root / "nan.json").exists())

    def test_directory_publication_never_replaces_a_destination(self):
        source = self.root / "temporary"
        source.mkdir(); (source / "measured.csv").write_text("measured")
        target = records.publish(source, self.root / "complete")
        self.assertFalse(source.exists())
        self.assertEqual((target / "measured.csv").read_text(), "measured")
        source.mkdir(); (source / "measured.csv").write_text("different")
        with self.assertRaises(FileExistsError):
            records.publish(source, target)
        self.assertEqual((target / "measured.csv").read_text(), "measured")
        self.assertEqual((source / "measured.csv").read_text(), "different")

    def test_rescoring_keeps_raw_output_attempt_and_old_scientific_values(self):
        for task in self.database.tasks("candidate"):
            path = self.root / (task["task_id"] + ".csv")
            path.write_text("unchanged raw simulation")
            token = self.database.start(task["task_id"])
            self.database.finish(task["task_id"], token, "complete", {
                "per_electrode_qps": [1, 2], "output": str(path), "sha256": records.sha256_file(path),
                "score_hash": "old"})
        self.database.observation("candidate", {"mean": 1.5})
        task = self.database.tasks("candidate")[0]
        old = task["result"]
        new = {**old, "per_electrode_qps": [2, 4], "score_hash": "new"}
        self.database.revise(task["task_id"], new)
        self.assertTrue(self.database.completed("candidate"))
        self.assertEqual(self.database.tasks("candidate")[0]["result"]["per_electrode_qps"], [2, 4])
        self.assertEqual(len(self.database.rows("attempts")), 2)
        previous = json.loads(self.database.rows("task_history")[0]["result_json"])
        self.assertEqual(previous["per_electrode_qps"], [1, 2])
        self.assertEqual(len(self.database.rows("observation_history")), 1)
        for bad in ({**new, "per_electrode_qps": [2]}, {**new, "sha256": "0" * 64},
                    {**new, "per_electrode_qps": [-1, 0]}, {**new, "per_electrode_qps": [float("nan"), 0]}):
            with self.assertRaises(ValueError):
                self.database.revise(task["task_id"], bad)

    def test_selected_column_chunks_and_malformed_files(self):
        path = self.root / "hits.csv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([*records.HIT_COLUMNS, "unused"])
            for index in range(5):
                writer.writerow([.00191, index * .001, 0, .0002625, 100, "text"])
        chunks = list(records.read_hits(path, chunk_rows=2))
        self.assertEqual([len(chunk[0]) for chunk in chunks], [2, 2, 1])
        self.assertEqual(len(chunks[0]), 5)
        path.write_text(",".join(records.HIT_COLUMNS) + "\n")
        self.assertEqual(list(records.read_hits(path)), [])
        for text in ("", "wrong,header\n", ",".join(records.HIT_COLUMNS) + "\nnan,0,0,0,1\n"):
            path.write_text(text)
            with self.assertRaises(ValueError):
                list(records.read_hits(path))


class HitHistogramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="surface hit counts ")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_saved_measurements_count_records_in_each_file(self):
        from figures.plot import count_hit_files
        folder = ROOT / "tests/fixtures/random_trial_9a3f0ab28d9d/hits"
        expected = [[2, 1, 4, 1, 3, 1, 0, 1, 1, 1, 1, 7, 4, 4, 2, 8],
                    [0, 0, 12, 0, 13, 0, 0, 4, 3, 0, 2, 17, 8, 8, 4, 1]]
        report = count_hit_files(sorted(folder.glob("*_hitsfile.txt")))
        measured = {Path(row["path"]).name: row["surface_hit_records"] for row in report["files"]}
        for replica, counts in enumerate(expected):
            for site, count in enumerate(counts):
                self.assertEqual(measured[f"stage4_property_v1_rand_9a3f0ab28d9d_r{replica}_p{site}_hitsfile.txt"], count)
        self.assertEqual(report["n_files"], 32)
        self.assertEqual(report["surface_hit_records"], 113)
        self.assertEqual(report["zero_hit_files"], 7)
        self.assertEqual((report["min_records"], report["max_records"]), (0, 17))
        self.assertAlmostEqual(report["mean_records"], 113 / 32)
        self.assertEqual(report["median_records"], 2)

    def test_records_include_both_surfaces_and_zero_deposited_energy(self):
        from figures.plot import count_hit_files
        path = self.root / "hits.csv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(records.HIT_COLUMNS)
            writer.writerows([[.00191, 0, 0, .0002625, 100],
                              [.00191, 0, 0, -.0002625, 200],
                              [0, 0, 0, .0002625, 300]])
        empty = self.root / "no hits.csv"
        empty.write_text(",".join(records.HIT_COLUMNS) + "\n")
        alias = self.root / "alias.csv"
        alias.symlink_to(path)
        report = count_hit_files([path, alias, path, empty])
        self.assertEqual(report["n_files"], 2)
        self.assertEqual(report["surface_hit_records"], 3)
        self.assertEqual(report["zero_hit_files"], 1)
        self.assertEqual({row["path"] for row in report["files"]}, {str(path.resolve()), str(empty.resolve())})

    def test_missing_empty_and_malformed_files_are_absent_measurements(self):
        from figures.plot import count_hit_files
        with self.assertRaises((ValueError, FileNotFoundError)):
            count_hit_files([self.root / "missing.csv"])
        path = self.root / "bad.csv"
        header = ",".join(records.HIT_COLUMNS) + "\n"
        for text in ("", "wrong,header\n", header + "nan,0,0,0,1\n", header + "1,0\n"):
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    count_hit_files([path])
        with self.assertRaises(ValueError):
            count_hit_files([])

    def test_large_file_count_includes_every_chunk(self):
        from figures.plot import count_hit_files
        path = self.root / "large.csv"
        expected = 65539
        path.write_text(",".join(records.HIT_COLUMNS) + "\n" + "1,0,0,0,1\n" * expected)
        report = count_hit_files([path])
        self.assertEqual(report["surface_hit_records"], expected)
        self.assertEqual(report["files"][0]["surface_hit_records"], expected)

    def test_changing_file_cannot_enter_a_completed_file_histogram(self):
        from unittest.mock import patch
        from figures.plot import count_hit_files
        path = self.root / "changing.csv"
        path.write_text(",".join(records.HIT_COLUMNS) + "\n1,0,0,0,1\n")
        read_hits = records.read_hits
        def changing_hits(source, *args, **kwargs):
            yield from read_hits(source, *args, **kwargs)
            with Path(source).open("a") as handle:
                handle.write("1,0,0,0,2\n")
        with patch.object(records, "read_hits", side_effect=changing_hits):
            with self.assertRaises((ValueError, RuntimeError)):
                count_hit_files([path])

    def test_histogram_and_command_line_keep_zero_hit_files(self):
        from figures.plot import count_hit_files, plot_hit_histogram
        folder = ROOT / "tests/fixtures/random_trial_9a3f0ab28d9d/hits"
        files = sorted(folder.glob("*_hitsfile.txt"))
        report = count_hit_files(files)
        figure = plot_hit_histogram(report, self.root / "histogram.png", bins=8, log_x=True)
        self.assertEqual(Path(figure).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(Path(figure).stat().st_size, 1000)
        output = self.root / "command histogram.png"
        table = self.root / "counts.csv"
        process = subprocess.run([sys.executable, str(ROOT / "figures/plot.py"), "hits",
                                  *map(str, files), "--output", str(output), "--counts", str(table),
                                  "--bins", "8"], cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(output.is_file())
        with table.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 32)
        self.assertEqual(sum(int(row["surface_hit_records"]) for row in rows), 113)
        self.assertEqual(sum(int(row["surface_hit_records"]) == 0 for row in rows), 7)

    def test_study_directory_excludes_live_and_failed_outputs_and_checks_saved_hashes(self):
        study = self.root / "running study"
        study.mkdir()
        output = self.root / "study histogram.png"
        table = self.root / "study counts.csv"
        command = [sys.executable, str(ROOT / "figures/plot.py"), "hits", str(study),
                   "--output", str(output), "--counts", str(table)]
        with records.Results(study / "results.sqlite") as database:
            database.plan("study", "candidate", {}, [{"task_id": name} for name in ("zero", "measured", "live", "failed")])
            for name, number in (("zero", 0), ("measured", 3), ("live", 20), ("failed", 20)):
                directory = study / "tasks" / name / "attempt"
                directory.mkdir(parents=True)
                path = directory / "hits.csv"
                path.write_text(",".join(records.HIT_COLUMNS) + "\n" + "1,0,0,0,1\n" * number)
                witness = directory / "complete"
                witness.touch()
                token = database.start(name)
                if name != "live":
                    database.finish(name, token, "failed" if name == "failed" else "complete", {
                        "qps": [number], "output": str(path), "sha256": records.sha256_file(path),
                        "paths": {"witness": str(witness)}})
            process = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            with table.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(sorted(int(row["surface_hit_records"]) for row in rows), [0, 3])
            path = study / "tasks/measured/attempt/hits.csv"
            with path.open("a") as handle:
                handle.write("1,0,0,0,2\n")
            changed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("checksum", changed.stderr.lower())

    def test_unfinished_raw_attempt_is_excluded_until_post_transport_marker(self):
        path = self.root / "tasks" / "task" / "attempt" / "hits.csv"
        path.parent.mkdir(parents=True)
        path.write_text(",".join(records.HIT_COLUMNS) + "\n1,0,0,0,1\n")
        output = self.root / "attempt.png"
        command = [sys.executable, str(ROOT / "figures/plot.py"), "hits", str(path.parent),
                   "--output", str(output)]
        pending = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(pending.returncode, 0)
        self.assertFalse(output.exists())
        (path.parent / "complete").touch()
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(output.is_file())

    def test_command_line_rejects_destructive_output_paths_and_zero_bins(self):
        source = self.root / "measured.csv"
        source.write_text(",".join(records.HIT_COLUMNS) + "\n1,0,0,0,1\n")
        checksum = records.sha256_file(source)
        output = self.root / "histogram.png"
        alias = self.root / "alias.csv"
        alias.symlink_to(source)
        cases = [["--output", str(output), "--counts", str(source)],
                 ["--output", str(output), "--counts", str(alias)],
                 ["--output", str(output), "--counts", str(output)],
                 ["--output", str(output), "--bins", "0"]]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                process = subprocess.run([sys.executable, str(ROOT / "figures/plot.py"), "hits", str(source),
                                          *arguments], cwd=self.root, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(records.sha256_file(source), checksum)
                self.assertFalse(output.exists())


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_history_is_read_only_and_conversion_is_an_explicit_copy(self):
        source = self.root / "old.sqlite"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE trials (trial_id TEXT, status TEXT, run_dir TEXT, validity_state TEXT)")
            connection.execute("INSERT INTO trials VALUES(?,?,?,?)", ("a", "done", "/old/a", "invalidated_calibration"))
        original = records.sha256_file(source)
        with records.Results(source, create=False) as reader:
            self.assertEqual(reader.rows("trials")[0]["validity_state"], "invalidated_calibration")
            with self.assertRaises(sqlite3.OperationalError):
                reader.connection.execute("DELETE FROM trials")
        converted = records.convert_copy(source, self.root / "copy.sqlite", "/old", "/new", ["unchanged earlier notice"])
        with records.Results(converted["path"], create=False) as reader:
            row = reader.rows("trials")[0]
            self.assertEqual(row["run_dir"], "/new/a")
            self.assertEqual(row["validity_state"], "invalidated_calibration")
            self.assertEqual(len(reader.rows("conversion_notes")), 1)
        self.assertEqual(records.sha256_file(source), original)

    def test_still_running_copy_cannot_be_converted(self):
        source = self.root / "running.sqlite"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE trials (trial_id TEXT, status TEXT)")
            connection.execute("INSERT INTO trials VALUES('a','running')")
        with self.assertRaises(ValueError):
            records.convert_copy(source, self.root / "copy.sqlite")
        self.assertFalse((self.root / "copy.sqlite").exists())

    def test_history_report_retains_registered_invalidity_and_unchecked_trials(self):
        source = self.root / "old.sqlite"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE trials (trial_id TEXT, status TEXT)")
            connection.executemany("INSERT INTO trials VALUES(?,?)", [("bad", "done"), ("unlisted", "done"), ("checked", "done")])
        register = self.root / "invalidations.yaml"
        register.write_text(yaml.safe_dump({"schema": "stage4_invalidations_v1", "findings": {"wrong_material": {
            "title": "Wrong substrate carrier", "trials": [{"trial_id": "bad", "verdict": "invalid"},
                                                              {"trial_id": "checked", "verdict": "valid"}]}},
            "decisions": {"optimizer_provenance_unrecoverable": {"title": "Earlier optimizer source unavailable",
                          "campaigns": ["earlier_bo"], "consequence": "Efficiency comparison withdrawn"}}}))
        before = records.sha256_file(source)
        report = records.history_summary(source, register)
        trials = {row["trial_id"]: row for row in report["trials"]}
        self.assertEqual(trials["bad"]["registered_validity"], "invalid")
        self.assertEqual(trials["unlisted"]["registered_validity"], "unchecked")
        self.assertEqual(trials["checked"]["registered_findings"][0]["verdict"], "valid")
        self.assertFalse(report["automatic_reuse"])
        self.assertEqual(report["unavailable_optimizer_labels"], ["earlier_bo"])
        self.assertEqual(report["status_counts"]["trials"], {"done": 3})
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(records.sha256_file(source), before)

    def test_git_inventory_retains_missing_tracked_evidence(self):
        repository = self.root / "repository"
        repository.mkdir()
        source = repository / "results"
        source.mkdir()
        (source / "missing.csv").write_text("recorded simulation\n")
        (source / "changed.json").write_text("old")
        (source / "same.json").write_text("same")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "results"], check=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "saved evidence"], check=True)
        digest = records.sha256_file(source / "missing.csv")
        (source / "missing.csv").unlink()
        (source / "changed.json").write_text("new")
        (source / "new.json").write_text("new evidence")
        before = subprocess.run(["git", "-C", str(repository), "status", "--porcelain"], check=True, capture_output=True).stdout
        report = records.git_inventory(source, repository)
        files = {row["path"]: row for row in report["files"]}
        self.assertEqual(files["missing.csv"]["status"], "Git-only")
        self.assertEqual(files["missing.csv"]["git_sha256"], digest)
        self.assertEqual(files["changed.json"]["status"], "modified")
        self.assertEqual(files["same.json"]["status"], "unchanged")
        self.assertEqual(files["new.json"]["status"], "disk-only")
        after = subprocess.run(["git", "-C", str(repository), "status", "--porcelain"], check=True, capture_output=True).stdout
        self.assertEqual(before, after)
        self.assertFalse((source / "missing.csv").exists())

    def test_git_inventory_cannot_bind_an_unrelated_directory(self):
        repository = self.root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            records.git_inventory(outside, repository)
        alias = repository / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            records.git_inventory(alias, repository)

    def test_sqlite_backup_is_consistent_and_never_overwrites(self):
        source = self.root / "source.sqlite"
        with records.Results(source) as database:
            database.plan("e", "c", {}, [{"task_id": "t"}])
            output = database.backup(self.root / "copy.sqlite")
            with self.assertRaises(FileExistsError):
                database.backup(output)
        with records.Results(output, create=False) as reader:
            self.assertEqual(reader.inspect()["integrity"], "ok")
            self.assertEqual(reader.tasks("c")[0]["task_id"], "t")

    def test_archive_refuses_a_running_writer(self):
        source = self.root / "source"
        with records.Results(source / "results.sqlite"):
            with self.assertRaises(RuntimeError):
                records.create_archive(source, None, self.root / "live.tar.gz")
        self.assertFalse((self.root / "live.tar.gz").exists())

    def test_archive_and_recovery_preserve_bytes_modes_and_times(self):
        source = self.root / "source"
        source.mkdir()
        (source / "result.json").write_text('{"mean":0}')
        os.chmod(source / "result.json", 0o640)
        before = records.inventory(source)
        receipt = records.create_archive(source, None, self.root / "copy.tar.gz")
        repeated = records.create_archive(source, None, self.root / "second.tar.gz")
        self.assertEqual(receipt["sha256"], repeated["sha256"])
        self.assertEqual(records.verify_archive(receipt["path"])["manifest"], before)
        restored = records.recover_archive(receipt["path"], self.root / "restored")
        self.assertEqual(records.inventory(restored), before)
        self.assertEqual(records.recover_archive(receipt["path"], restored), restored)
        self.assertEqual(records.inventory(source), before)
        (restored / "extra").write_text("extra")
        with self.assertRaises(FileExistsError):
            records.recover_archive(receipt["path"], restored)

    def test_archive_refuses_corruption_and_unsafe_members(self):
        source = self.root / "source"
        source.mkdir(); (source / "a").write_text("measured")
        receipt = records.create_archive(source, None, self.root / "good.tar.gz")
        corrupted = self.root / "corrupt.tar.gz"
        corrupted.write_bytes(Path(receipt["path"]).read_bytes()[:40])
        with self.assertRaises((ValueError, tarfile.TarError, EOFError, OSError)):
            records.verify_archive(corrupted)
        for index, name in enumerate(("../outside", "/absolute", "a/../b", "safe/link")):
            path = self.root / f"bad{index}.tar"
            with tarfile.open(path, "w") as archive:
                entry = tarfile.TarInfo(name)
                if name.endswith("link"):
                    entry.type = tarfile.SYMTYPE; entry.linkname = "/tmp/outside"
                else:
                    entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                records.verify_archive(path)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="material workflow ")
        self.root = Path(self.directory.name)
        self.calls = self.root / "calls.txt"
        self.executable = self.root / "fake simulator"
        default = scan.load_study({"schema": "material-scan-study-2", "sampling": {"sites": 2, "replicas": 2}})
        self.template = Path(default["installation"]["macro_template"])
        if not self.template.is_file():
            raise AssertionError(f"checked macro template is missing: {self.template}")
        lattice = self.root / "maps" / "Si"
        lattice.mkdir(parents=True)
        (lattice / "config.txt").write_text(
            "cubic 5.43095 Ang\nstiffness 1 1 165.6 GPa\nstiffness 1 2 63.9 GPa\n"
            "stiffness 4 4 79.5 GPa\ndyn -0.349 -0.429 0.035 -0.256\n"
            "scat 2.43e-42 s3\ndecay 4.1e-55 s4\ndecayTT 0.74\nLDOS 0.093\nSTDOS 0.376\nFTDOS 0.531\n")
        self.raw = {"schema": "material-scan-study-2", "id": "fake-check", "kind": "confirmation",
                    "sampling": {"sites": 2, "replicas": 2, "events_per_task": 10},
                    "execution": {"workers": 2, "timeout_s": 10, "stall_timeout_s": 0},
                    "analysis": {"bootstrap_draws": 32},
                    "installation": {"executable": str(self.executable), "lattice_data": str(lattice.parent),
                                     "macro_template": str(self.template), "environment_scripts": [],
                                     "library_dir": str(self.root)}}
        self.make_executable("normal")

    def tearDown(self):
        self.directory.cleanup()

    def make_executable(self, mode):
        script = f'''#!{sys.executable}
import csv, pathlib, shlex, sys, time
with open({str(self.calls)!r}, 'a') as handle: handle.write('call\\n')
mode={mode!r}
if mode == 'timeout': time.sleep(5)
if mode == 'fail': sys.exit(2)
print('Material: G4_Si density: 2.33', flush=True)
output=None
for raw in pathlib.Path(sys.argv[1]).read_text().splitlines():
    row=raw.strip()
    if row.startswith('/g4cmp/HitsFile '): output=pathlib.Path(row.split(None,1)[1])
    if row.startswith('/run/beamOn '):
        with output.open('w',newline='') as handle:
            writer=csv.writer(handle); writer.writerow({records.HIT_COLUMNS!r})
            if mode not in ('zero','incomplete'): writer.writerow([.00191,0,0,.0002625,100])
        if mode == 'malformed': output.write_text('bad header\\n')
        if mode == 'incomplete': sys.exit(0)
    if row.startswith('/control/shell '):
        command=shlex.split(row.split(None,1)[1])
        if command[0]=='touch': pathlib.Path(command[1]).touch()
if mode == 'teardown': sys.exit(139)
'''
        self.executable.write_text(script)
        self.executable.chmod(0o755)

    def test_input_rejects_unknown_values_and_wrong_primary_count(self):
        for change in ({"unknown": 1}, {"schema": "unsupported"},
                       {"parameters": {"sub_c44": {"value": float("nan")}}},
                       {"sampling": {"events_per_task": 0}}, {"sampling": {"replicas": True}},
                       {"event_counts": {"events_total": 99}}, {"parameters": {"unknown": 1}}):
            raw = copy.deepcopy(self.raw)
            raw.update(change)
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                scan.load_study(raw)

    def test_site_and_worker_counts_require_integers(self):
        for area, name, value in (("sampling", "sites", 2.5), ("sampling", "sites", True),
                                  ("execution", "workers", 1.5), ("execution", "workers", True)):
            raw = copy.deepcopy(self.raw)
            raw[area][name] = value
            with self.subTest(area=area, name=name, value=value), self.assertRaises(ValueError):
                scan.load_study(raw)

    def test_recorded_sites_seeds_and_event_counts_are_repeatable(self):
        study = scan.load_study(self.raw)
        prepared = physics.prepare_values(study, {"name": "reference", "values": {}})
        tasks = scan.task_plan(study, prepared, {"build": "fixed"})
        repeated = scan.task_plan(scan.load_study(self.raw), prepared, {"build": "fixed"})
        self.assertEqual(tasks, repeated)
        self.assertEqual(len(tasks), 4)
        self.assertEqual(sum(row["events"] for row in tasks), 40)
        self.assertEqual(len({row["task_id"] for row in tasks}), 4)
        self.assertEqual(len({tuple(row["seeds"]) for row in tasks}), 4)

    def test_independent_candidate_seed_pairs_preserve_optional_paired_streams(self):
        raw=copy.deepcopy(self.raw)
        for independent in (False,True):
            raw['sampling']['independent_candidate_streams']=independent
            study=scan.load_study(raw)
            first=physics.prepare_values(study,{'values':{}})
            second=physics.prepare_values(study,{'values':{'sub_c44':40.}})
            a=scan.task_plan(study,first,{'build':'fixed'})
            b=scan.task_plan(study,second,{'build':'fixed'})
            self.assertEqual(a,scan.task_plan(study,first,{'build':'fixed'}))
            self.assertEqual([row['seeds'] for row in a]==[row['seeds'] for row in b],not independent)
            if independent:
                self.assertTrue(all(1<=seed<=900000000 for row in a for seed in row['seeds']))
                self.assertTrue(any(row['seeds'][1]!=row['seeds'][0]+1 for row in a))

    def test_equal_physical_targets_keep_names_and_share_complete_tasks(self):
        raw=copy.deepcopy(self.raw)
        raw['candidates']=[{'name':'first','values':{}},{'name':'second','values':{}}]
        raw['analysis']['store_blocks']=False
        output=self.root/'equal targets'; report=scan.run_study(raw,output)
        self.assertTrue(report['complete'])
        self.assertEqual([row['name'] for row in report['results']],['first','second'])
        self.assertEqual(report['results'][0]['candidate_id'],report['results'][1]['candidate_id'])
        self.assertEqual(len(self.calls.read_text().splitlines()),4)
        selected=records.load_result(output,candidate_id=report['results'][1]['candidate_id'])
        self.assertEqual(len(selected['results']),1)
        self.assertEqual(len(selected['results'][0]['blocks']),4)

    def test_macro_seed_position_units_and_path_with_spaces(self):
        study = scan.load_study(self.raw)
        prepared = physics.prepare_values(study, {"values": {}})
        task = scan.task_plan(study, prepared, {"build": "fixed"})[0]
        environment, identity = scan.prepare_environment(study["installation"])
        result = scan.run_task(task, prepared, study["installation"], environment, self.root / "attempt with spaces")
        self.assertEqual(result["status"], "complete")
        macro = Path(result["paths"]["macro"]).read_text()
        self.assertIn(f"/random/setSeeds {task['seeds'][0]} {task['seeds'][1]}\n/run/beamOn 10", macro)
        self.assertIn("/main/gun/setEnergy 0.01 eV", macro)
        self.assertIn("/main/gun/setPosition " + " ".join(map(str, task["site_mm"])) + " mm", macro)
        self.assertEqual(result["sha256"], records.sha256_file(result["output"]))
        self.assertEqual(identity["executable_sha256"], records.sha256_file(self.executable))

    def test_macro_string_paths_are_not_parsed_as_numbers(self):
        scan.validate_macro("/g4cmp/HitsFile /tmp/measurement 123abc/hits.csv\n"
                                "/random/setSeeds 1 2\n/run/beamOn 10\n/control/shell touch /tmp/complete\n")
        with self.assertRaises(ValueError):
            scan.validate_macro("/random/setSeeds 1 2\n/run/beamOn 10.5bad\n/control/shell touch /tmp/complete\n")

    def test_missing_witness_malformed_output_and_exit_fault_are_distinguished(self):
        study = scan.load_study(self.raw)
        prepared = physics.prepare_values(study, {"values": {}})
        task = scan.task_plan(study, prepared, {"build": "fixed"})[0]
        for mode, status in (("incomplete", "invalid"), ("malformed", "invalid"), ("fail", "invalid"), ("teardown", "complete")):
            self.make_executable(mode)
            environment, _ = scan.prepare_environment(study["installation"])
            result = scan.run_task(task, prepared, study["installation"], environment, self.root / mode)
            with self.subTest(mode=mode):
                self.assertEqual(result["status"], status)
                if mode == "teardown":
                    self.assertTrue(result["teardown_fault"])
                else:
                    self.assertNotIn("per_electrode_qps", result)

    def test_complete_workflow_reuses_tasks_after_restart(self):
        import scan
        output = self.root / "evaluation"
        summary = scan.run_study(self.raw, output)
        self.assertIsInstance(summary, dict)
        first_calls = self.calls.read_text().splitlines()
        self.assertEqual(len(first_calls), 4)
        with records.Results(output / "results.sqlite", create=False) as database:
            self.assertEqual(len(database.tasks()), 4)
            self.assertTrue(all(row["status"] == "complete" for row in database.tasks()))
            self.assertEqual(len(database.read('observations')), 1)
        scan.run_study(self.raw, output)
        self.assertEqual(self.calls.read_text().splitlines(), first_calls)

    def test_zero_and_timeout_have_different_scientific_results(self):
        import scan
        self.make_executable("zero")
        output = self.root / "zero"
        scan.run_study(self.raw, output)
        with records.Results(output / "results.sqlite", create=False) as database:
            self.assertTrue(database.read('observations'), database.tasks())
            self.assertTrue(all(row["result"]["total_qps"] == 0 for row in database.tasks()))
        self.make_executable("timeout")
        raw = copy.deepcopy(self.raw)
        raw["execution"].update(timeout_s=.05)
        output = self.root / "timeout"
        scan.run_study(raw, output)
        with records.Results(output / "results.sqlite", create=False) as database:
            self.assertTrue(any(row["status"] == "timeout" for row in database.tasks()))
            self.assertFalse(any(row["kind"] in ("measured", "physical_zero") for row in database.read('observations')))
            self.assertTrue(all(not row["result"].get("measured", False) for row in database.tasks()))

    def test_recorded_result_figures(self):
        import analysis as plots
        import scan
        raw = copy.deepcopy(self.raw)
        raw["analysis"]["time_resolved"] = True
        output = self.root / "figures"
        scan.run_study(raw, output)
        for kind in ("summary", "hits", "time"):
            figure = plots.plot_result(output, output / (kind + ".png"), kind)
            self.assertGreater(figure.stat().st_size, 1000)
        geometry = {"geometry": scan.load_study(raw)["fixed"], "sites_mm": [[0, 0, .259875]]}
        self.assertTrue(plots.plot_result(geometry, output / "layout.svg", "layout").is_file())

    def test_figure_labels_follow_the_recorded_response_units(self):
        import matplotlib
        import analysis as plots
        with matplotlib.rc_context({"svg.fonttype": "none"}):
            for units, label in (("summed_junction_QPs_per_primary", "summed junction QPs / primary"),
                                 ("weighted_junction_QPs_per_injected_eV", "weighted junction QPs / injected eV"),
                                 ("MHz us", "integrated decoherence rate [MHz μs]")):
                path = self.root / (units.replace("/", "-") + ".svg")
                plots.plot_result({"rows": [{"mean": 1, "units": units}]}, path)
                self.assertIn(label, path.read_text())
        with self.assertRaises(ValueError):
            plots.plot_result({"rows": [{"mean": 1, "units": "MHz us"},
                                        {"mean": 1, "units": "summed_junction_QPs_per_primary"}]}, self.root / "mixed.png")

    def test_workflow_repairs_only_the_corrupt_saved_task(self):
        import scan
        output = self.root / "repair"
        scan.run_study(self.raw, output)
        with records.Results(output / "results.sqlite", create=False) as database:
            path = Path(database.tasks()[0]["result"]["output"])
        path.write_text("corrupted after completion")
        scan.run_study(self.raw, output)
        self.assertEqual(len(self.calls.read_text().splitlines()), 5)
        with records.Results(output / "results.sqlite", create=False) as database:
            self.assertEqual(len(database.rows("attempts")), 5)
            self.assertEqual(len(database.rows("observation_history")), 1)
            self.assertEqual(len(database.read('observations')), 1)

    def test_recovered_results_can_be_analysed_without_the_original_tree(self):
        import scan
        output = self.root / "original"
        expected = scan.run_study(self.raw, output)
        receipt = records.create_archive(output, None, self.root / "copy.tar.gz")
        restored = records.recover_archive(receipt["path"], self.root / "restored")
        shutil.rmtree(output)
        before = records.sha256_file(restored / "results.sqlite")
        result = analysis.read_analysis(restored)
        self.assertEqual(result["results"][0]["value"], expected["results"][0]["value"])
        self.assertEqual(result["results"][0]["standard_error"], expected["results"][0]["standard_error"])
        self.assertTrue(all(Path(block["output"]).is_file() for block in result["results"][0]["blocks"]))
        self.assertEqual(records.sha256_file(restored / "results.sqlite"), before)
        self.assertEqual(len(self.calls.read_text().splitlines()), 4)

    def test_recovered_time_figure_uses_restored_files(self):
        import analysis as plots
        import scan
        raw = copy.deepcopy(self.raw)
        raw["analysis"]["time_resolved"] = True
        output = self.root / "original"
        scan.run_study(raw, output)
        receipt = records.create_archive(output, None, self.root / "copy.tar.gz")
        restored = records.recover_archive(receipt["path"], self.root / "restored")
        shutil.rmtree(output)
        figure = plots.plot_result(restored, self.root / "restored-time.png", "time")
        self.assertGreater(figure.stat().st_size, 1000)
        time_file = next(restored.glob("tasks/*/*/qps.npz"))
        with np.load(time_file) as values:
            times, qps = values["time_ns"], values["qps"].copy()
        np.savez_compressed(time_file, time_ns=times, qps=qps + 1)
        with self.assertRaises(ValueError):
            plots.plot_result(restored, self.root / "corrupt-time.png", "time")

    def test_check_command_reads_relative_study_paths(self):
        import scan
        raw = copy.deepcopy(self.raw)
        raw["installation"]["executable"] = self.executable.name
        raw["installation"]["lattice_data"] = "maps"
        path = self.root / "study.yaml"
        path.write_text(yaml.safe_dump(raw))
        study = scan.load_study(path)
        self.assertEqual(Path(study["installation"]["executable"]).resolve(), self.executable)
        self.assertEqual(scan.main(["check", str(path)]), 0)


    def test_disabled_comparisons_survive_compact_reanalysis(self):
        from unittest.mock import patch
        raw=copy.deepcopy(self.raw)
        raw['analysis'].update(objective='device_junction_qps_per_eV',
                               comparisons=False,diagnostics=False,store_blocks=False)
        raw['candidates']=[{'name':'first-move','values':{}},
                           {'name':'second-move','values':{'topfilm_vsound':4.}}]
        output=self.root/'moves-without-reference'
        with patch.object(analysis,'paired_difference',side_effect=AssertionError('automatic pairing requested')):
            report=scan.run_study(raw,output)
            reread=analysis.read_analysis(output)
        for result in (report,reread):
            self.assertTrue(result['complete'])
            self.assertEqual(result['comparisons'],[])
            self.assertTrue(all('blocks' not in row for row in result['results']))
        self.assertEqual([(row['value'],row['standard_error']) for row in reread['results']],
                         [(row['value'],row['standard_error']) for row in report['results']])
        enabled=analysis.read_analysis(output,settings={'comparisons':True})
        self.assertEqual(len(enabled['comparisons']),1)
        self.assertEqual(enabled['comparisons'][0]['reference'],'first-move')
        self.assertEqual(enabled['comparisons'][0]['candidate'],'second-move')
        self.assertTrue(all('blocks' not in row for row in enabled['results']))
        self.assertEqual(len(self.calls.read_text().splitlines()),8)

    def test_reanalysis_preserves_tail_draws_and_explicit_diagnostics(self):
        raw=copy.deepcopy(self.raw)
        raw['analysis'].update(bootstrap_draws=19,tail_bootstrap_draws=13,diagnostics=True)
        output=self.root/'tail-roundtrip'
        run=scan.run_study(raw,output)
        reread=analysis.read_analysis(output)
        self.assertEqual(run['results'][0]['estimate'],reread['results'][0]['estimate'])


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.raw = {'schema': 'material-scan-study-2', 'id': 'nested-check', 'kind': 'convergence',
                    'sampling': {'kind': 'sobol', 'sites': 4, 'replicas': 2, 'events_per_task': 10},
                    'analysis': {'objective': 'device_junction_qps_per_eV'},
                    'candidates': [{'name': 'reference', 'values': {}}, {'name': 'alternative', 'values': {'sub_c44': 70.}}],
                    'levels': [{'sites': 4}, {'sites': 8}, {'sites': 16, 'replicas': 3}]}

    def tearDown(self):
        self.directory.cleanup()

    def report(self, child, index=0, changing_region=False):
        loaded = scan.load_study(child)
        rows = []
        for candidate, value in [('reference', 10.), ('alternative', 8.)]:
            means = {name: value + .005 * index for name in loaded['design']['stratum_weights']}
            if changing_region and index:
                means['S1_le_0.05'] *= 1.5
            estimate = {'value': value + .005 * index, 'standard_error': .2,
                        'means_by_stratum': means, 'maximum_site_leverage': .05}
            rows.append({'name': candidate, 'status': 'complete', 'estimate': estimate})
        return {'id': child['id'], 'objective': loaded['analysis']['objective'],
                'complete': True, 'results': rows}

    def test_levels_append_sites_and_preserve_old_indices_and_seeds(self):
        from unittest.mock import patch
        import scan
        captured = []
        def run(child, destination, execute=True):
            captured.append(scan.load_study(child))
            return self.report(child, len(captured) - 1)
        with patch('scan.run_study', side_effect=run):
            summary = scan.run_series(self.raw, self.root / 'series')
        self.assertTrue(summary['complete'])
        self.assertTrue(summary['same_physics_goal'])
        self.assertTrue(summary['convergence']['converged'])
        self.assertTrue(summary['convergence']['ranking_checked'])
        self.assertEqual(len({study['id'] for study in captured}), 3)
        for coarse, fine in zip(captured, captured[1:]):
            n = len(coarse['design']['sites_mm'])
            self.assertEqual(coarse['design']['sites_mm'], fine['design']['sites_mm'][:n])
            self.assertEqual(coarse['design']['stratum'], fine['design']['stratum'][:n])
            self.assertEqual(coarse['design']['stratum_weights'], fine['design']['stratum_weights'])
            old = scan.task_plan(coarse, {'physical': 'same'}, {'build': 'same'})
            new = {(row['position'], row['replica']): row for row in scan.task_plan(fine, {'physical': 'same'}, {'build': 'same'})}
            for row in old:
                match = new[(row['position'], row['replica'])]
                self.assertEqual(row['site_mm'], match['site_mm'])
                self.assertEqual(row['seeds'], match['seeds'])
                self.assertEqual(row['events'], match['events'])

    def test_area_weights_are_computed_once_and_total_stability_is_insufficient(self):
        from unittest.mock import patch
        import scan
        raw = copy.deepcopy(self.raw)
        raw['sampling'].update(kind='stratified', sites=64, area_samples=20000)
        raw['levels'] = [{'sites': 64}, {'sites': 96}]
        calls = []
        def run(child, destination, execute=True):
            calls.append(child)
            return self.report(child, len(calls) - 1, changing_region=True)
        with patch('scan.run_study', side_effect=run), patch('physics.build_design', wraps=physics.build_design) as build:
            summary = scan.run_series(raw, self.root / 'area')
        self.assertEqual(sum(call.args[1]['kind'] == 'stratified' and call.kwargs.get('previous') is None for call in build.call_args_list), 1)
        self.assertFalse(summary['convergence']['converged'])
        comparison = summary['convergence']['comparisons'][0]
        self.assertTrue(comparison['ranking_checked'])
        self.assertFalse(comparison['candidates']['reference']['checks']['every_region'])
        a = scan.load_study(calls[0]); b = scan.load_study(calls[1])
        self.assertEqual(set(a['design']['stratum']), set(physics.REGIONS))
        self.assertEqual(a['design']['stratum_weights'], b['design']['stratum_weights'])
        self.assertEqual(a['design']['sites_mm'], b['design']['sites_mm'][:64])

    def test_steps_stop_on_returned_failure_even_with_existing_summary_files(self):
        from unittest.mock import patch
        import scan
        child = copy.deepcopy(self.raw)
        child.pop('levels'); child['kind'] = 'confirmation'
        sequence = {'schema': 'material-scan-study-2', 'id': 'ordered', 'kind': 'series',
                    'steps': [copy.deepcopy(child) for _ in range(3)]}
        calls = []
        def run(study, destination, execute=True):
            Path(destination).mkdir(parents=True, exist_ok=True)
            (Path(destination) / 'summary.json').write_text('{"complete":true}')
            calls.append(study)
            if len(calls) == 2:
                return {'id': study['id'], 'complete': False, 'status': 'incomplete'}
            return self.report(study)
        with patch('scan.run_study', side_effect=run):
            summary = scan.run_series(sequence, self.root / 'steps')
        self.assertEqual(len(calls), 2)
        self.assertFalse(summary['complete'])
        self.assertEqual(summary['declared_studies'], 3)
        self.assertFalse(summary['convergence']['converged'])

    def test_single_candidate_or_repeated_resolution_cannot_claim_convergence(self):
        from unittest.mock import patch
        import scan
        raw = copy.deepcopy(self.raw)
        raw['levels'] = [{'sites': 16}, {'sites': 16}]
        with patch('scan.run_study', side_effect=lambda child, destination, execute=True: self.report(child)):
            repeated = scan.run_series(raw, self.root / 'repeated')
        self.assertTrue(repeated['complete'])
        self.assertFalse(repeated['convergence']['converged'])
        self.assertEqual(repeated['convergence']['comparisons'][0]['refined_axes'], [])
        raw['levels'] = [{'sites': 16}, {'sites': 32}]
        def run(child, destination, execute=True):
            report = self.report(child); report['results'] = report['results'][:1]
            return report
        with patch('scan.run_study', side_effect=run):
            single = scan.run_series(raw, self.root / 'single')
        self.assertFalse(single['convergence']['converged'])
        self.assertFalse(single['convergence']['ranking_checked'])


class SearchResumeTests(unittest.TestCase):
    setUp = WorkflowTests.setUp
    tearDown = WorkflowTests.tearDown
    make_executable = WorkflowTests.make_executable

    def test_check_series_validates_every_child_without_running_transport(self):
        import contextlib
        import scan
        path = self.root / 'sequence.yaml'
        sequence = {'schema': 'material-scan-study-2', 'id': 'checked-sequence', 'kind': 'sequence',
                    'steps': [copy.deepcopy(self.raw), copy.deepcopy(self.raw)]}
        path.write_text(yaml.safe_dump(sequence))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(scan.main(['check', str(path)]), 0)
        self.assertEqual(json.loads(output.getvalue())['checked_studies'], 2)
        self.assertFalse(self.calls.exists())
        sequence['steps'][1]['candidates'] = [{'name': 'unstable', 'values': {'sub_c44': -1.}}]
        path.write_text(yaml.safe_dump(sequence))
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(scan.main(['check', str(path)]), 2)
        self.assertRegex(errors.getvalue(), 'sub_c44|unstable')
        self.assertFalse(self.calls.exists())

    def search_input(self, method='random', evaluations=3):
        raw = copy.deepcopy(self.raw)
        table = records.load_document(ROOT / 'parameters.yaml')['parameters']
        raw.update(kind='search', parameters={name: {'mode': 'fixed', 'value': row['default']}
                                             for name, row in table.items()})
        raw['parameters']['topfilm_vsound'] = {'mode': 'search', 'bounds': [1.5, 6.]}
        raw['search'] = {'method': method, 'seed': 47, 'evaluations': evaluations,
                         'parallel_candidates': 2, 'options': {'popsize': 4}}
        return raw

    def test_fixed_temperature_controls_values_macro_and_search_coordinates(self):
        raw = copy.deepcopy(self.raw)
        raw.update(kind='search', fixed={'temperature_K': .02})
        study = scan.load_study(raw)
        self.assertEqual(study['space']['baseline']['temperature'], .02)
        self.assertNotIn('temperature', study['space']['names'])
        prepared = physics.prepare_values(study, {'values': {}})
        self.assertEqual(prepared['values']['temperature'], .02)
        task = scan.task_plan(study, prepared, {'build': 'fixed'})[0]
        paths = scan.render_inputs(prepared, task, study['installation'], self.root / 'temperature-inputs')
        self.assertIn('/g4cmp/temperature 0.02 K', Path(paths['macro']).read_text())
        raw['parameters'] = {'temperature': {'mode': 'fixed', 'value': .03}}
        with self.assertRaisesRegex(ValueError, 'temperature'):
            scan.load_study(raw)

    def test_series_preserves_variable_temperature_for_search_and_morris(self):
        from unittest.mock import patch
        import scan
        search_step = copy.deepcopy(self.raw); search_step['kind'] = 'search'
        morris_step = copy.deepcopy(self.raw)
        morris_step.update(kind='sensitivity', morris={'parameters': ['/g4cmp/temperature'], 'trajectories': 2, 'seed': 41})
        sequence = {'schema': 'material-scan-study-2', 'id': 'temperature-steps', 'kind': 'sequence',
                    'steps': [search_step, morris_step]}
        original = scan.run_study; captured = []
        def run(child, destination, execute=True):
            captured.append(scan.load_study(child))
            return original(child, destination, execute=execute)
        with patch('scan.run_study', side_effect=run):
            result = scan.run_series(sequence, self.root / 'variable-temperature', execute=False)
        self.assertEqual(result['status'], 'prepared')
        self.assertIn('temperature', captured[0]['space']['names'])
        self.assertFalse(self.calls.exists())

    def test_integrated_density_confirmation_computes_the_density_response(self):
        import scan
        raw = copy.deepcopy(self.raw)
        raw['analysis']['objective'] = 'integrated_density'
        report = scan.run_study(raw, self.root / 'density-confirmation')
        row = report['results'][0]
        self.assertTrue(report['complete'])
        self.assertEqual(row['value'], row['integrated_density'])
        self.assertNotEqual(row['value'], row['historical']['value'])
        self.assertEqual(row['units'], 'MHz us')
        self.assertTrue(all(Path(block['time_data']).is_file() for block in row['blocks']))

    def test_pending_proposals_resume_without_new_draws(self):
        from unittest.mock import patch
        import scan
        import search
        raw = self.search_input(); output = self.root / 'pending'
        with patch('scan.evaluate_points', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            scan.run_study(raw, output)
        with records.Results(output / 'results.sqlite', create=False) as database:
            progress = database.read('state', 'search_progress')
        saved = search.load_state(progress['optimizer'])
        self.assertEqual(len(saved['pending']), 2)
        self.assertEqual(saved['pending_ids'], [0, 1])
        self.assertEqual(progress['rows'], [])
        report = scan.run_study(raw, output)
        self.assertTrue(report['complete'])
        self.assertEqual([row['parameters'] for row in report['results'][:2]], saved['pending'])
        self.assertEqual([row['proposal_id'] for row in report['results']], [0, 1, 2])
        self.assertEqual(len(self.calls.read_text().splitlines()), 12)

    def test_tiny_rates_keep_distinct_ids_across_cma_failure_and_restart(self):
        import search
        table = records.load_document(ROOT / 'parameters.yaml')['parameters']
        for name in ('sub_scat', 'sub_decay'):
            with self.subTest(parameter=name):
                space = physics.search_space({name: {'mode': 'search'}}, kind='fixed', table=table)
                state = search.new_state('cmaes', seed=7, options={'popsize': 4, 'reeval_every': 0})
                points = search.propose('cmaes', state, space, 4)
                self.assertEqual(state['pending_ids'], [0, 1, 2, 3])
                self.assertEqual(len({point[name] for point in points}), 4)
                self.assertTrue(all(round(point[name], 12) == 0 for point in points))
                self.assertTrue(all('proposal_id' not in point for point in points))
                search.update('cmaes', state, points[0], 2., .1, proposal_id=0)
                self.assertEqual(state['pending_ids'], [1, 2, 3])
                self.assertEqual(state['_cma'].generation, 0)
                search.update('cmaes', state, points[1], None, rejected_reason='timeout', proposal_id=1)
                restored = search.load_state(json.loads(json.dumps(search.save_state(state))))
                for current in (state, restored):
                    for identifier in (2, 3):
                        search.update('cmaes', current, points[identifier], float(identifier), .1,
                                      proposal_id=identifier)
                    self.assertEqual(current['_cma'].generation, 0)
                replacement = search.propose('cmaes', state, space, 1)
                self.assertEqual(replacement, search.propose('cmaes', restored, space, 1))
                self.assertEqual(state['pending_ids'], [4])
                for current in (state, restored):
                    search.update('cmaes', current, replacement[0], 1., .1, proposal_id=4)
                    self.assertEqual(current['_cma'].generation, 1)
                    self.assertEqual(current['observation_ids'], [0, 2, 3, 4])
                    self.assertEqual(current['used'], {'0': True, '1': False, '2': True, '3': True, '4': True})
                self.assertEqual(search.save_state(state), search.save_state(restored))
                self.assertEqual(search.propose('cmaes', state, space, 4),
                                 search.propose('cmaes', restored, space, 4))

    def test_proposal_id_rejects_wrong_point_and_old_rounded_checkpoint(self):
        import search
        table = records.load_document(ROOT / 'parameters.yaml')['parameters']
        space = physics.search_space({'sub_scat': {'mode': 'search'}}, kind='fixed', table=table)
        state = search.new_state('random', seed=7)
        points = search.propose('random', state, space, 2)
        before = search.save_state(state)
        with self.assertRaisesRegex(ValueError, 'differ'):
            search.update('random', state, points[1], 1., proposal_id=0)
        self.assertEqual(search.save_state(state), before)
        search.update('random', state, points[0], 1.)
        self.assertEqual(state['pending_ids'], [1])
        with self.assertRaisesRegex(ValueError, 'rounded physical keys'):
            search.load_state({'schema': 'material-search-1', 'state': {}})

    def test_fitted_gp_with_pending_points_resumes_without_a_new_fit(self):
        from unittest.mock import patch
        import search
        table = records.load_document(ROOT / 'parameters.yaml')['parameters']
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed', table=table)
        state = search.new_state('bo_gp', seed=7,
                                 options={'n_init': 4, 'n_candidates': 32, 'n_polish': 1,
                                          'n_restarts': 1, 'refit_every': 3})
        for index, point in enumerate(search.propose('bo_gp', state, space, 4)):
            search.update('bo_gp', state, point, 1. + (point['topfilm_vsound'] - 3.)**2,
                          .01 * (index + 1))
        proposals = search.propose('bo_gp', state, space, 2)
        search.update('bo_gp', state, proposals[0], 1.1, .03)
        restored = search.load_state(json.loads(json.dumps(search.save_state(state))))
        self.assertEqual(restored['pending_ids'], [5])
        with patch.object(search.GaussianProcess, 'fit', side_effect=AssertionError('unexpected new fit')):
            points = search.propose('bo_gp', state, space, 2)
            self.assertEqual(points, search.propose('bo_gp', restored, space, 2))
        self.assertEqual(state['pending_ids'], [5, 6, 7])
        self.assertEqual(search.save_state(state), search.save_state(restored))

    def test_interruption_after_atomic_batch_save_preserves_history_and_target(self):
        from unittest.mock import patch
        import scan
        import search
        raw = self.search_input(); output = self.root / 'atomic'
        original = records.Results.save_state
        def interrupted(database, name, value):
            original(database, name, value)
            if name == 'search_progress' and len(value['rows']) == 2:
                raise KeyboardInterrupt
        with patch.object(records.Results, 'save_state', interrupted), self.assertRaises(KeyboardInterrupt):
            scan.run_study(raw, output)
        with records.Results(output / 'results.sqlite', create=False) as database:
            progress = database.read('state', 'search_progress')
        self.assertEqual(len(progress['rows']), 2)
        self.assertEqual(len(search.load_state(progress['optimizer'])['Y']), 2)
        report = scan.run_study(raw, output)
        self.assertTrue(report['complete'])
        self.assertEqual([row['name'] for row in report['results']], ['random_0000', 'random_0001', 'random_0002'])
        self.assertEqual(len(self.calls.read_text().splitlines()), 12)

    def test_failed_cma_member_is_replaced_and_never_becomes_zero_yield(self):
        from unittest.mock import patch
        import scan
        import search
        raw = self.search_input('cmaes', evaluations=5); output = self.root / 'cma'
        original = scan.run_task; calls = []; lock = threading.Lock()
        def failed_once(*args, **kwargs):
            with lock:
                first = not calls; calls.append(True)
            if first:
                return {'status': 'failed', 'reason': 'unmeasured simulated failure'}
            return original(*args, **kwargs)
        with patch('scan.run_task', side_effect=failed_once):
            report = scan.run_study(raw, output)
        with records.Results(output / 'results.sqlite', create=False) as database:
            progress = database.read('state', 'search_progress')
        state = search.load_state(progress['optimizer'])
        self.assertFalse(report['complete'])
        self.assertEqual(len(state['Y']), 4)
        self.assertEqual(len(state['rejected']), 1)
        self.assertEqual(state['_cma'].generation, 1)
        self.assertEqual(sum(state['used'].values()), 4)
        self.assertEqual(sum(row['proposal']['used_in_optimizer_update'] for row in report['results']), 4)
        rejected = [row for row in report['results'] if row['status'] != 'complete']
        self.assertEqual(len(rejected), 1)
        self.assertIsNone(rejected[0]['value'])

    def test_ode_only_morris_moves_reuse_transport_and_change_density_response(self):
        import scan
        raw = copy.deepcopy(self.raw)
        raw.update(kind='sensitivity', morris={'parameters': ['I_ph'], 'trajectories': 2, 'seed': 41})
        output = self.root / 'morris-ode'
        report = scan.run_study(raw, output)
        self.assertTrue(report['complete'])
        self.assertEqual(report['sensitivity']['status'], 'complete')
        self.assertEqual(len(report['results']), 4)
        with records.Results(output / 'results.sqlite', create=False) as database:
            self.assertEqual(len(database.tasks()), 8)
        self.assertEqual(len(self.calls.read_text().splitlines()), 8)
        by_trajectory = {}
        for row in report['results']:
            by_trajectory.setdefault(row['trajectory'], []).append(row['integrated_density'])
        self.assertTrue(all(len(values) == 2 and values[0] != values[1] for values in by_trajectory.values()))


class RoundTwoEfficiencyTests(unittest.TestCase):
    setUp=WorkflowTests.setUp
    tearDown=WorkflowTests.tearDown
    make_executable=WorkflowTests.make_executable

    def test_every_declared_example_passes_physical_preparation(self):
        from contextlib import redirect_stdout
        for path in sorted((ROOT/'studies').glob('*.yaml')):
            with self.subTest(study=path.name),redirect_stdout(io.StringIO()):
                self.assertEqual(scan.main(['check',str(path)]),0)

    def test_cached_evaluation_prepares_and_verifies_each_input_once(self):
        from unittest.mock import patch
        output=self.root/'efficiency'
        first=scan.run_study(self.raw,output)
        original_hash=records.sha256_file
        original_tasks=records.Results.tasks
        original_counts=analysis.prepare_counts
        raw_hashes=[]; arrays=[]
        def measured_hash(path):
            if Path(path).name=='hits.csv': raw_hashes.append(str(path))
            return original_hash(path)
        def measured_counts(blocks,*args,**kwargs):
            if not isinstance(blocks,dict): arrays.append(blocks)
            return original_counts(blocks,*args,**kwargs)
        with patch.object(records,'sha256_file',side_effect=measured_hash), \
             patch.object(records.Results,'tasks',autospec=True,side_effect=original_tasks) as tasks, \
             patch.object(records,'read_hits',wraps=records.read_hits) as hits, \
             patch.object(analysis,'prepare_counts',side_effect=measured_counts), \
             patch.object(analysis,'scoring_identity',wraps=analysis.scoring_identity) as scorer, \
             patch.object(scan,'input_text',wraps=scan.input_text) as rendering:
            second=scan.run_study(self.raw,output)
        self.assertEqual(tasks.call_count,1)
        self.assertEqual(hits.call_count,0)
        self.assertEqual(scorer.call_count,1)
        self.assertEqual(rendering.call_count,1)
        self.assertEqual(len(arrays),1)
        self.assertEqual(len(raw_hashes),4)
        self.assertEqual(len(set(raw_hashes)),4)
        self.assertEqual(first['results'][0]['value'],second['results'][0]['value'])
        self.assertEqual(first['results'][0]['standard_error'],second['results'][0]['standard_error'])
        self.assertEqual(self.calls.read_text().count('call'),4)


class RecordsRoundTwoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_material_snapshot_reads_each_selected_lattice_once(self):
        from unittest.mock import patch
        catalog = self.root / "materials.yaml"
        catalog.write_text("substrates:\n  Ge:\n    enabled: true\n    lattice_map: Ge\n")
        source = self.root / "maps" / "Si" / "config.txt"
        source.parent.mkdir(parents=True)
        original = ("cubic 5.43095 Ang\nstiffness 1 1 165.6 GPa\nstiffness 1 2 63.9 GPa\n"
                    "stiffness 4 4 79.5 GPa\ndyn -0.349 -0.429 0.035 -0.256 GPa\n"
                    "scat 2.43e-42 s3\ndecay 4.1e-55 s4\ndecayTT 0.74\nLDOS 0.093\nSTDOS 0.376\nFTDOS 0.531\n")
        source.write_text(original)
        reads = []
        read_bytes = Path.read_bytes
        def count(path):
            reads.append(path)
            return read_bytes(path)
        with patch.object(Path, "read_bytes", count):
            snapshot = records.material_inputs(catalog, source.parent.parent)
        self.assertEqual(reads, [source])
        self.assertEqual(set(snapshot["lattices"]), {"Si"})
        frozen = snapshot["lattices"]["Si"]
        self.assertEqual(frozen["sha256"], records.sha256_file(source))
        source.write_text("changed external source")
        self.assertEqual(frozen["text"], original)
        self.assertEqual(frozen["fields"]["stiffness 4 4"], [79.5])

    def test_json_loading_preserves_exponent_numbers_and_copies_dicts(self):
        path = self.root / "record.json"
        records.write_json(path, {"tiny": 1e-6})
        self.assertEqual(records.load_document(path), {"tiny": 1e-6})
        original = {"nested": {"value": 1}}
        loaded = records.load_document(original)
        loaded["nested"]["value"] = 2
        self.assertEqual(original["nested"]["value"], 1)

    def test_old_result_names_normalize_without_changing_input_blocks(self):
        original = {"rows": [{"name": "a", "mean": 1.25, "se": .2, "units": "QPs / primary",
                              "blocks": [{"per_electrode_qps": [0., 2.], "status": "complete"}]}]}
        before = records.canonical_json(original)
        loaded = records.load_result(original)
        self.assertEqual(loaded["results"][0]["value"], 1.25)
        self.assertEqual(loaded["results"][0]["standard_error"], .2)
        self.assertEqual(loaded["results"][0]["units"], "QPs / primary")
        self.assertEqual(loaded["results"][0]["blocks"], original["rows"][0]["blocks"])
        self.assertEqual(records.canonical_json(original), before)

    def test_recovered_loader_preserves_values_inputs_and_database_bytes(self):
        source = self.root / "original"
        source.mkdir()
        raw = source / "tasks" / "t" / "attempt" / "hits.csv"
        raw.parent.mkdir(parents=True)
        raw.write_text(",".join(records.HIT_COLUMNS) + "\n")
        prepared = {"fixed": {"setTopGap": .000191}}
        with records.Results(source / "results.sqlite") as database:
            database.plan("e", "c", prepared, [{"task_id": "t"}])
            token = database.start("t")
            block = database.finish("t", token, "complete", {"per_electrode_qps": [0., 0.],
                                    "output": str(raw), "sha256": records.sha256_file(raw)})
            database.save_state("output_root", str(source))
            database.save_state("summary", {"results": [{"candidate_id": "c", "value": 0.,
                                "standard_error": 0., "units": "QPs / primary", "blocks": [block]}]})
        records.write_json(source / "resolved.json", {"id": "example", "tiny": 1e-6})
        archive = records.create_archive(source, None, self.root / "copy.tar.gz")
        restored = records.recover_archive(archive["path"], self.root / "restored")
        shutil.rmtree(source)
        before = records.sha256_file(restored / "results.sqlite")
        loaded = records.load_result(restored, include_inputs=True)
        row = loaded["results"][0]
        self.assertEqual((row["value"], row["standard_error"]), (0., 0.))
        self.assertEqual(row["blocks"][0]["per_electrode_qps"], [0., 0.])
        self.assertTrue(Path(row["blocks"][0]["output"]).is_file())
        self.assertEqual(loaded["candidate_inputs"], {"c": prepared})
        self.assertEqual(loaded["study"]["tiny"], 1e-6)
        self.assertEqual(records.sha256_file(restored / "results.sqlite"), before)

    def test_complete_set_reuses_one_hash_but_detects_later_file_changes(self):
        from unittest.mock import patch
        path = self.root / "hits.csv"
        path.write_text("old")
        with records.Results(self.root / "results.sqlite") as database:
            database.plan("e", "c", {}, [{"task_id": "t"}])
            token = database.start("t")
            database.finish("t", token, "complete", {"per_electrode_qps": [1.],
                            "output": str(path), "sha256": records.sha256_file(path)})
            tasks = database.tasks("c")
            verified = {}
            with patch.object(records, "sha256_file", wraps=records.sha256_file) as hashes, patch.object(database, "tasks", side_effect=AssertionError("unneeded task query")):
                self.assertTrue(database.completed("c", tasks=tasks, verified=verified))
                database.observation("c", {"value": 1.}, tasks=tasks, verified=verified)
                self.assertEqual(hashes.call_count, 1)
                self.assertFalse(database.completed("c", tasks=tasks + tasks, verified=verified))
                path.write_text("new")
                self.assertFalse(database.completed("c", tasks=tasks, verified=verified))
                self.assertEqual(hashes.call_count, 2)

    def test_supplied_hashes_do_not_evaluate_fallback_hashes(self):
        from unittest.mock import patch
        experiment = {"experiment_id": "e", "simulation_hash": "a" * 64,
                      "analysis_hash": "b" * 64, "search_hash": "c" * 64}
        with records.Results(self.root / "results.sqlite") as database:
            with patch.object(records, "content_hash", side_effect=AssertionError("unneeded fallback hash")):
                database.plan(experiment, "c", {}, [{"task_id": "t"}])
            self.assertEqual(len(database.tasks("c")), 1)

    def test_preloaded_task_rows_cannot_hide_later_invalidation(self):
        with records.Results(self.root / "results.sqlite") as database:
            database.plan("e", "c", {}, [{"task_id": "t"}])
            token = database.start("t")
            database.finish("t", token, "complete", {"per_electrode_qps": [1.]})
            earlier = database.tasks("c")
            database.revise("t", reason="physical input was invalid")
            history = json.loads(database.rows("task_history")[0]["result_json"])
            self.assertEqual(history["per_electrode_qps"], [1.])
            self.assertFalse(database.completed("c", tasks=earlier, verified={}))
            with self.assertRaises(ValueError):
                database.observation("c", {"value": 1.}, tasks=earlier, verified={})

    def test_revision_cannot_replace_a_running_or_unmeasured_attempt(self):
        with records.Results(self.root / "results.sqlite") as database:
            database.plan("e", "c", {}, [{"task_id": "t"}])
            token = database.start("t")
            with self.assertRaises(ValueError):
                database.revise("t", reason="still owned")
            database.finish("t", token, "interrupted", {"reason": "stopped"})
            with self.assertRaises(ValueError):
                database.revise("t", {"per_electrode_qps": [0.]})
            self.assertIsNone(database.tasks("c")[0]["active_token"])
            self.assertFalse(database.completed("c"))

    def test_decoded_reader_limits_tables_and_preserves_state_defaults(self):
        with records.Results(self.root / "results.sqlite") as database:
            database.plan("e", "c", {"x": 2.}, [{"task_id": "t"}])
            database.save_state("restart", {"pending": [1, 2]})
            self.assertEqual(database.read("state", "restart"), {"pending": [1, 2]})
            self.assertEqual(database.read("state", "absent", {"pending": []}), {"pending": []})
            self.assertEqual(database.read("candidates", "c")[0]["input"], {"x": 2.})
            self.assertEqual(database.read("candidates", "other"), [])
            with self.assertRaises(ValueError):
                database.read("tasks; DELETE FROM tasks")
            self.assertEqual(len(database.tasks()), 1)


class RoundTwoSearchIntegrationTests(unittest.TestCase):
    setUp = WorkflowTests.setUp
    tearDown = WorkflowTests.tearDown
    make_executable = WorkflowTests.make_executable
    search_input = SearchResumeTests.search_input

    def completed_old_source(self, name):
        from unittest.mock import patch
        prepare = scan.prepare_environment
        def old_source(installation):
            environment, build = prepare(installation)
            return environment, {**build, 'renderer_sha256': '1' * 64}
        output = self.root / name
        with patch('scan.prepare_environment', side_effect=old_source):
            report = scan.run_study(self.raw, output)
        self.assertTrue(report['complete'])
        return output, report

    def test_old_source_exact_inputs_allow_cached_analysis_with_separate_evidence(self):
        from unittest.mock import patch
        output, old_report = self.completed_old_source('old-source-cache')
        frozen_build = (output / 'build.json').read_bytes()
        calls = len(self.calls.read_text().splitlines())
        with patch('scan.run_task', side_effect=AssertionError('cached transport reran')):
            current = scan.run_study(self.raw, output)
        self.assertTrue(current['complete'])
        self.assertEqual(current['results'][0]['value'], old_report['results'][0]['value'])
        self.assertEqual(current['build'], old_report['build'])
        self.assertEqual((output / 'build.json').read_bytes(), frozen_build)
        self.assertEqual(len(self.calls.read_text().splitlines()), calls)
        evidence = list(output.glob('source-comparison-*.json'))
        self.assertEqual(len(evidence), 1)
        comparison = records.load_document(evidence[0])
        self.assertEqual(comparison['previous_build'], old_report['build'])
        self.assertNotEqual(comparison['current_source_build']['renderer_sha256'],
                            comparison['previous_build']['renderer_sha256'])
        self.assertEqual(len(comparison['checked_tasks']), 4)

    def test_old_source_reuse_rejects_changed_build_and_search_state(self):
        output, _ = self.completed_old_source('old-source-build')
        study = scan.load_study(self.raw)
        _, build = scan.prepare_environment(study['installation'])
        with self.assertRaisesRegex(ValueError, 'build changed'):
            scan.verify_previous_inputs(study, scan.candidate_table(study), output,
                                        {**build, 'executable_sha256': '2' * 64})
        with self.assertRaises(ValueError):
            scan.verify_previous_inputs(study, None, output, build)
        self.assertEqual(list(output.glob('source-comparison-*.json')), [])

    def test_old_source_reuse_rejects_corrupt_recorded_macro(self):
        output, _ = self.completed_old_source('old-source-macro')
        with records.Results(output / 'results.sqlite', create=False) as database:
            macro = Path(database.tasks()[0]['result']['paths']['macro'])
        macro.write_text(macro.read_text() + '# changed input\n')
        study = scan.load_study(self.raw)
        _, build = scan.prepare_environment(study['installation'])
        with self.assertRaisesRegex(ValueError, 'macro.*checksum'):
            scan.verify_previous_inputs(study, scan.candidate_table(study), output, build)
        self.assertEqual(list(output.glob('source-comparison-*.json')), [])

    def test_old_source_never_dispatches_if_cached_output_disappears(self):
        from unittest.mock import patch
        for phase in ('before verification', 'after verification'):
            with self.subTest(phase=phase):
                output, _ = self.completed_old_source('missing-' + phase)
                with records.Results(output / 'results.sqlite', create=False) as database:
                    raw_output = Path(database.tasks()[0]['result']['output'])
                verify = scan.verify_previous_inputs
                checked = []
                def deleted_after_check(*args, **kwargs):
                    previous = verify(*args, **kwargs)
                    checked.append(True)
                    raw_output.unlink()
                    return previous
                if phase == 'before verification':
                    raw_output.unlink()
                calls = len(self.calls.read_text().splitlines())
                with patch('scan.run_task', side_effect=AssertionError('old renderer identity used for new transport')):
                    if phase == 'after verification':
                        with patch('scan.verify_previous_inputs', side_effect=deleted_after_check):
                            with self.assertRaises((ValueError, RuntimeError)):
                                scan.run_study(self.raw, output)
                    else:
                        with self.assertRaises((ValueError, RuntimeError)):
                            scan.run_study(self.raw, output)
                self.assertEqual(len(self.calls.read_text().splitlines()), calls)
                if phase == 'after verification':
                    self.assertEqual(checked, [True])

    def test_task_ids_match_previous_canonical_physical_inputs(self):
        study = scan.load_study(self.raw)
        prepared = physics.prepare_values(study, {'values': {}})
        physical = {key: value for key, value in prepared.items() if key != 'source_text'}
        build = {'executable_sha256': 'a' * 64, 'libraries': {'/physical/library.so': 'b' * 64},
                 'renderer_sha256': 'c' * 64}
        for trajectory in (0, 3):
            candidate = {**physical, 'trajectory': trajectory, 'ode': {'I_ph': 2.},
                         'design_index': 17, 'transport_group': 'shared'}
            tasks = scan.task_plan(study, candidate, build)
            for task in tasks:
                transport = {key: value for key, value in candidate.items() if key not in
                             ('ode', 'trajectory', 'design_index', 'transport_group', 'problem', 'parameter_specs')}
                raw = {key: value for key, value in task.items() if key != 'task_id'}
                expected = records.content_hash({'physics': transport, 'task': raw, 'build': build})
                self.assertEqual(task['task_id'], expected)
            tagged = {**candidate, 'proposal_id': 1234, 'source_text': 'cached lattice text',
                      'input_text': 'rendered inputs'}
            self.assertEqual(tasks, scan.task_plan(study, tagged, build))
            changed = copy.deepcopy(candidate)
            changed['values']['sub_scat'] *= 1.01
            self.assertNotEqual([row['task_id'] for row in tasks],
                                [row['task_id'] for row in scan.task_plan(study, changed, build)])
            self.assertNotEqual([row['task_id'] for row in tasks],
                                [row['task_id'] for row in scan.task_plan(study, candidate,
                                    {**build, 'executable_sha256': 'd' * 64})])

    def test_tiny_rate_cma_failure_and_atomic_restart_preserve_proposal_ids(self):
        from unittest.mock import patch
        import search
        raw = self.search_input('cmaes', evaluations=5)
        table = records.load_document(ROOT / 'parameters.yaml')['parameters']
        raw['parameters']['topfilm_vsound'] = {'mode': 'fixed', 'value': table['topfilm_vsound']['default']}
        raw['parameters']['sub_scat'] = {'mode': 'search'}
        output = self.root / 'rate-search-resume'
        run_task = scan.run_task; save_state = records.Results.save_state
        calls = []; lock = threading.Lock()
        def fail_once(*args, **kwargs):
            with lock:
                first = not calls; calls.append(True)
            return {'status': 'failed', 'reason': 'unmeasured failure'} if first else run_task(*args, **kwargs)
        def interrupt_after_batch(database, name, value):
            save_state(database, name, value)
            if name == 'search_progress' and len(value['rows']) == 4:
                raise KeyboardInterrupt
        with patch('scan.run_task', side_effect=fail_once), patch.object(records.Results, 'save_state', interrupt_after_batch):
            with self.assertRaises(KeyboardInterrupt):
                scan.run_study(raw, output)
        with records.Results(output / 'results.sqlite', create=False) as database:
            progress = database.read('state', 'search_progress')
        state = search.load_state(progress['optimizer'])
        self.assertEqual([row['proposal_id'] for row in progress['rows']], [0, 1, 2, 3])
        self.assertEqual(state['pending_ids'], [])
        self.assertEqual(len(state['Y']), 3)
        self.assertEqual(state['_cma'].generation, 0)
        self.assertEqual(len({row['parameters']['sub_scat'] for row in progress['rows']}), 4)
        failed = next(row for row in progress['rows'] if row['status'] != 'complete')
        self.assertEqual(state['rejected'][0]['proposal_id'], failed['proposal_id'])
        old_rows = copy.deepcopy(progress['rows'])
        calls_before = len(self.calls.read_text().splitlines())
        report = scan.run_study(raw, output)
        self.assertFalse(report['complete'])
        self.assertEqual([row['proposal_id'] for row in report['results']], [0, 1, 2, 3, 4])
        self.assertEqual([row['candidate_id'] for row in report['results'][:4]],
                         [row['candidate_id'] for row in old_rows])
        self.assertEqual(len(self.calls.read_text().splitlines()) - calls_before, 4)
        with records.Results(output / 'results.sqlite', create=False) as database:
            restored = search.load_state(database.read('state', 'search_progress')['optimizer'])
        self.assertEqual(restored['_cma'].generation, 1)
        self.assertEqual(len(restored['Y']), 4)
        self.assertNotIn(failed['proposal_id'], restored['observation_ids'])
        self.assertEqual(sum(row['proposal']['used_in_optimizer_update'] for row in report['results']), 4)
        for row in report['results']:
            identifier = row['proposal_id']
            self.assertEqual(row['proposal']['proposal_id'], identifier)
            self.assertEqual(row['proposal']['used_in_optimizer_update'], restored['used'][str(identifier)])
        saved = json.loads((output / 'summary.json').read_text())
        self.assertEqual([row['proposal_id'] for row in saved['results']], [0, 1, 2, 3, 4])


class LongRunTests(unittest.TestCase):
    setUp = WorkflowTests.setUp
    tearDown = WorkflowTests.tearDown
    make_executable = WorkflowTests.make_executable
    search_input = SearchResumeTests.search_input

    def test_ai_outage_pauses_before_transport_and_resumes_measured_history(self):
        from unittest.mock import patch
        import search
        raw=self.search_input(method='llm_bo',evaluations=2)
        raw['search'].update(measured_evaluations=True,parallel_candidates=1)
        raw['search']['options'].update(n_init=1,n_candidates=16,extra_candidates=1,n_restarts=1,
                                       model='local:1',model_digest='weights')
        output=self.root/'ai-pause'
        def unavailable(state,*args,**kwargs):
            state['unavailable']='Ollama unavailable: test service outage'
            raise RuntimeError(state['unavailable'])
        with patch('search.ollama_proposals',side_effect=unavailable):
            paused=scan.run_study(raw,output)
        self.assertFalse(paused['complete'])
        self.assertEqual(paused['status'],'paused')
        self.assertFalse(paused['ai']['valid'])
        self.assertEqual(paused['search_counts']['measured'],1)
        self.assertEqual(len(self.calls.read_text().splitlines()),4)
        self.assertEqual(analysis.read_analysis(output)['status'],'paused')
        def available(state,space,*args,**kwargs):
            state.pop('unavailable',None)
            state['llm_stats']={'calls':1,'accepted':1,'tools':0}
            state['llm_model']='local:1'; state['llm_digest']='weights'
            state['llm_records']=[{'model':'local:1','digest':'weights','elapsed_s':1.}]
            return [physics.from_unit([.75],space)]
        with patch('search.ollama_proposals',side_effect=available):
            resumed=scan.run_study(raw,output)
        self.assertTrue(resumed['complete'])
        self.assertTrue(resumed['ai']['valid'])
        self.assertEqual(resumed['search_counts']['measured'],2)
        self.assertEqual(resumed['results'][0],paused['results'][0])
        self.assertEqual(len(self.calls.read_text().splitlines()),8)
        self.assertEqual(analysis.read_analysis(output)['ai'],resumed['ai'])

    def test_ai_zero_valid_proposals_cannot_be_reported_as_complete(self):
        from unittest.mock import patch
        raw=self.search_input(method='llm_bo',evaluations=2)
        raw['search'].update(measured_evaluations=True,parallel_candidates=1)
        raw['search']['options'].update(n_init=1,n_candidates=16,extra_candidates=1,n_restarts=1)
        with patch('search.ollama_proposals',return_value=[]):
            report=scan.run_study(raw,self.root/'ai-zero')
        self.assertEqual(report['status'],'paused')
        self.assertFalse(report['complete'])
        self.assertFalse(report['ai']['valid'])
        self.assertEqual(report['search_counts']['measured'],1)
        self.assertEqual(len(self.calls.read_text().splitlines()),4)

    def test_later_ai_failure_retains_measured_history_and_precise_cause(self):
        from unittest.mock import patch
        import search
        raw=self.search_input(method='llm_bo',evaluations=3)
        raw['search'].update(measured_evaluations=True,parallel_candidates=1)
        raw['search']['options'].update(n_init=1,n_candidates=16,extra_candidates=1,n_restarts=1,
                                       llm_every=1,model='local:1',model_digest='weights')
        output=self.root/'ai-later-failure'
        calls=[]
        reason='Ollama final output exhausted the generation limit; no candidate JSON'
        def proposals(state,space,*args,**kwargs):
            calls.append(True)
            if len(calls)>1:
                state['unavailable']=reason
                state['llm_records'].append({'model':'local:1','digest':'weights','error':reason})
                raise RuntimeError(reason)
            state['llm_stats']={'calls':1,'accepted':1,'tools':0}
            state['llm_model']='local:1'; state['llm_digest']='weights'
            state['llm_records']=[{'model':'local:1','digest':'weights','elapsed_s':1.}]
            return [physics.from_unit([.75],space)]
        with patch('search.ollama_proposals',side_effect=proposals):
            paused=scan.run_study(raw,output)
        self.assertEqual(paused['status'],'paused')
        self.assertEqual(paused['incomplete_reason'],reason)
        self.assertEqual(paused['search_counts']['measured'],2)
        self.assertEqual(paused['ai']['stats']['calls'],1)
        self.assertEqual(paused['ai']['stats']['accepted'],1)
        self.assertEqual(paused['ai']['records'][-1]['error'],reason)
        self.assertEqual(len(self.calls.read_text().splitlines()),8)
        self.assertTrue(all(row['status']=='complete' and row['value'] is not None for row in paused['results']))
        with records.Results(output/'results.sqlite',create=False) as database:
            saved=search.load_state(database.read('state','search_progress')['optimizer'])
        self.assertEqual(len(saved['Y']),2)
        self.assertEqual(saved['pending'],[])
        self.assertEqual(saved['unavailable'],reason)
        self.assertEqual(analysis.read_analysis(output)['incomplete_reason'],reason)

    def test_shared_measured_starts_count_only_new_responses_and_resume(self):
        from unittest.mock import patch
        import search
        raw=self.search_input(evaluations=2)
        study=scan.load_study(raw)
        values=physics.prepare_values(study,{'values':{}})['values']
        initial={'objective':study['analysis']['objective'],'design_hash':study['design']['design_hash'],
                 'events_per_task':study['sampling']['events_per_task'],'replicas':study['sampling']['replicas'],
                 'observations':[{'values':values,'value':.01,'standard_error':.002,
                                  'transport_standard_error':.0001,'elapsed_s':1.}]}
        path=self.root/'common.json'; records.write_json(path,initial)
        raw['search'].update(initial_observations_file=str(path),measured_evaluations=True,
                             conditional_transport_noise=True,record_runtime=True)
        output=self.root/'shared-start'; original=search.update
        with patch('search.update',wraps=original) as update:
            report=scan.run_study(raw,output)
        self.assertEqual(report['search_counts']['initial'],1)
        self.assertEqual(report['search_counts']['measured'],2)
        self.assertEqual(len(self.calls.read_text().splitlines()),8)
        measured=[call for call in update.call_args_list if call.kwargs.get('status')=='complete']
        self.assertEqual(len(measured),3)
        for call,row in zip(measured[1:],report['results']):
            self.assertEqual(call.kwargs['se'],row['transport_standard_error'])
            self.assertGreater(call.kwargs['elapsed_s'],0.)
        resumed=scan.run_study(raw,output)
        self.assertEqual(resumed['results'],report['results'])
        self.assertEqual(analysis.read_analysis(output)['initial_results'],report['initial_results'])
        self.assertEqual(len(self.calls.read_text().splitlines()),8)
        for field,value in [('objective','different objective'),('replicas',3)]:
            invalid=copy.deepcopy(initial); invalid[field]=value
            records.write_json(path,invalid,overwrite=True)
            with self.assertRaisesRegex(ValueError,'objective or sampling'): scan.load_study(raw)
        invalid=copy.deepcopy(initial); invalid['observations'][0]['values']['temperature']=.01
        records.write_json(path,invalid,overwrite=True)
        with self.assertRaisesRegex(ValueError,'fixed physical property'): scan.load_study(raw)

    def test_search_elapsed_limit_stops_without_recording_false_zeros(self):
        self.make_executable('timeout')
        raw=self.search_input(evaluations=2)
        raw['search'].update(measured_evaluations=True,max_attempts=4,max_elapsed_s=.4,record_runtime=True)
        started=time.monotonic(); report=scan.run_study(raw,self.root/'elapsed-limit')
        self.assertLess(time.monotonic()-started,4.)
        self.assertFalse(report['complete'])
        self.assertEqual(report['search_counts']['measured'],0)
        self.assertTrue(all(row['value'] is None for row in report['results']))
        self.assertTrue(all(row['elapsed_s']>0 for row in report['results']))

    def test_term_and_hup_reap_children_and_resume_saved_proposals(self):
        import signal
        import search
        for kind in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=kind):
                self.make_executable('normal')
                mode = self.root / f'mode-{kind}'
                pids = self.root / f'pids-{kind}'
                mode.write_text('sleep')
                script = self.executable.read_text().splitlines(keepends=True)
                script.insert(1, f"import os, pathlib, time\nwith open({str(pids)!r}, 'a') as handle: handle.write(str(os.getpid())+'\\n')\nif pathlib.Path({str(mode)!r}).read_text() == 'sleep': time.sleep(30)\n")
                self.executable.write_text(''.join(script))
                raw = self.search_input(evaluations=3)
                raw['execution'].update(workers=2, timeout_s=60, stall_timeout_s=0)
                path = self.root / f'study-{kind}.yaml'
                path.write_text(yaml.safe_dump(raw))
                output = self.root / f'signal-{kind}'
                driver = subprocess.Popen([sys.executable, str(ROOT / 'scan.py'), 'run', str(path), '--output', str(output)],
                                          cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                started = []
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if pids.is_file(): started = [int(pid) for pid in pids.read_text().splitlines()]
                        if len(started) >= 2: break
                        if driver.poll() is not None: break
                        time.sleep(.03)
                    self.assertGreaterEqual(len(started), 2)
                    driver.send_signal(kind)
                    _, error = driver.communicate(timeout=5)
                    self.assertEqual(driver.returncode, 130, error)
                    self.assertIn('interrupted:', error)
                    self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in started))
                    with records.Results(output / 'results.sqlite', create=False) as database:
                        progress = database.read('state', 'search_progress')
                    saved = search.load_state(progress['optimizer'])
                    self.assertEqual(saved['pending_ids'], [0, 1])
                    self.assertEqual(progress['rows'], [])
                    mode.write_text('normal')
                    report = scan.run_study(raw, output)
                    self.assertTrue(report['complete'])
                    self.assertEqual([row['parameters'] for row in report['results'][:2]], saved['pending'])
                finally:
                    if driver.poll() is None:
                        driver.kill(); driver.wait()
                    for pid in started:
                        try: os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError: pass

    def test_cleanup_gate_blocks_a_worker_already_rendering(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        self.make_executable('timeout')
        study = scan.load_study(self.raw)
        prepared = physics.prepare_values(study)
        task = scan.task_plan(study, prepared, {'build': 'fixed'})[0]
        environment, _ = scan.prepare_environment(study['installation'])
        active = {}; lock = threading.Lock(); stop = threading.Event()
        paused = threading.Event(); release = threading.Event()
        original = scan.render_inputs
        def render(point, row, installation, directory):
            if row['task_id'] == 'waiting':
                paused.set(); release.wait(timeout=5)
            return original(point, row, installation, directory)
        pool = ThreadPoolExecutor(max_workers=2); futures = []
        try:
            with patch('scan.render_inputs', side_effect=render):
                futures.append(pool.submit(scan.run_task, task, prepared, study['installation'], environment,
                                           self.root / 'live-child', active, lock, stop))
                deadline = time.monotonic() + 5
                while not active and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(active)
                pid = next(iter(active.values()))['process'].pid
                futures.append(pool.submit(scan.run_task, {**task, 'task_id': 'waiting'}, prepared,
                                           study['installation'], environment, self.root / 'waiting-child', active, lock, stop))
                self.assertTrue(paused.wait(timeout=5))
                thread = threading.Thread(target=lambda: (stop.wait(timeout=5), release.set()))
                thread.start()
                scan.close_workers(pool, futures, active, lock, stop)
                thread.join(timeout=5)
            self.assertEqual(active, {})
            self.assertFalse(Path(f'/proc/{pid}').exists())
            self.assertEqual(futures[1].result()['status'], 'interrupted')
            self.assertFalse((self.root / 'waiting-child' / 'complete').exists())
        finally:
            release.set()
            scan.close_workers(pool, futures, active, lock, stop)

    def test_memory_guard_counts_parent_and_respects_ancestor_headroom(self):
        from unittest.mock import Mock, patch
        gib = 1024**3; log = self.root / 'guard.log'; log.write_text('')
        row = {'process': Mock(pid=456), 'log': str(log), 'output': str(log),
               'started': time.monotonic(), 'last_change': time.monotonic()}
        def stats(pid, children=True): return (0, .6*gib if pid == os.getpid() else .6*gib)
        with patch('scan.process_stats', side_effect=stats), patch('scan.memory_headroom', return_value={'available_bytes': 100*gib}), patch('scan.stop_process') as stop:
            scan.monitor_processes({'task': row}, threading.Lock(), {'total_mem_gb': 1})
            self.assertEqual(row['stopped_reason'], 'total_memory_limit')
            stop.assert_called_once_with(row['process'])
        gate = threading.Event(); row.pop('stopped_reason')
        with patch('scan.process_stats', side_effect=stats), patch('scan.memory_headroom', return_value={'available_bytes': 8*gib}), patch('scan.stop_process') as stop:
            with self.assertRaisesRegex(RuntimeError, 'memory reserve'):
                scan.monitor_processes({'task': row}, threading.Lock(), {'total_mem_gb': 10, 'system_mem_reserve_gb': 16}, gate)
            self.assertTrue(gate.is_set())
            self.assertEqual(row['stopped_reason'], 'system_memory_limit')
            stop.assert_called_once_with(row['process'])

    def test_memory_headroom_uses_smallest_finite_ancestor_limit(self):
        from unittest.mock import patch
        gib = 1024**3
        files = {'/proc/meminfo': f'MemAvailable: {128*gib//1024} kB\n', '/proc/self/cgroup': '0::/parent/child\n',
                 '/sys/fs/cgroup/parent/child/memory.max': 'max', '/sys/fs/cgroup/parent/child/memory.current': str(gib),
                 '/sys/fs/cgroup/parent/memory.max': str(64*gib), '/sys/fs/cgroup/parent/memory.current': str(50*gib),
                 '/sys/fs/cgroup/memory.max': str(256*gib), '/sys/fs/cgroup/memory.current': str(200*gib)}
        with patch.object(Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in files), patch.object(Path, 'read_text', autospec=True, side_effect=lambda path: files[str(path)]):
            self.assertEqual(scan.memory_headroom(), {'available_bytes': 14*gib, 'limiting_cgroup': '/sys/fs/cgroup/parent',
                                                    'raw_unreclaimed_headroom_bytes': 14*gib})
        files['/sys/fs/cgroup/parent/memory.stat']='inactive_file '+str(20*gib)+'\nanon '+str(25*gib)+'\nkernel '+str(5*gib)+'\n'
        with patch.object(Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in files), patch.object(Path, 'read_text', autospec=True, side_effect=lambda path: files[str(path)]):
            self.assertEqual(scan.memory_headroom()['available_bytes'],34*gib)
            self.assertEqual(scan.memory_headroom()['raw_unreclaimed_headroom_bytes'],14*gib)
        files['/sys/fs/cgroup/parent/memory.current']=str(60*gib)
        files['/sys/fs/cgroup/parent/memory.stat']='inactive_file 0\nanon '+str(55*gib)+'\nkernel '+str(5*gib)+'\n'
        with patch.object(Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in files), patch.object(Path, 'read_text', autospec=True, side_effect=lambda path: files[str(path)]):
            self.assertEqual(scan.memory_headroom()['available_bytes'],4*gib)

    def test_measured_search_replaces_failure_and_stores_compact_history(self):
        from unittest.mock import patch
        import search
        raw = self.search_input(evaluations=2)
        raw['search'].update(measured_evaluations=True, max_attempts=4)
        raw['analysis'].update(diagnostics=False, store_search_blocks=False)
        output = self.root / 'measured-target'
        original = scan.run_task; calls = []; lock = threading.Lock()
        def fail_once(*args, **kwargs):
            with lock:
                first = not calls; calls.append(True)
            return {'status': 'failed', 'reason': 'unmeasured failure'} if first else original(*args, **kwargs)
        with patch('scan.run_task', side_effect=fail_once): report = scan.run_study(raw, output)
        self.assertTrue(report['complete'])
        self.assertEqual(report['search_counts']['measured'], 2)
        self.assertEqual(report['search_counts']['attempts'], 3)
        self.assertTrue(all('blocks' not in row for row in report['results']))
        with records.Results(output / 'results.sqlite', create=False) as database:
            progress = database.read('state', 'search_progress')
            state = search.load_state(progress['optimizer'])
            self.assertEqual(len(database.tasks()), 12)
        self.assertEqual(len(state['Y']), 2)
        self.assertEqual(len(state['rejected']), 1)
        self.assertTrue(all('blocks' not in row for row in progress['rows']))
        self.assertIsNone(next(row for row in report['results'] if row['status'] != 'complete')['value'])
        before={str(path):records.sha256_file(path) for path in output.rglob('*') if path.is_file()}
        reread=analysis.read_analysis(output)
        self.assertEqual([(row['value'],row['standard_error']) for row in reread['results']],
                         [(row['value'],row['standard_error']) for row in report['results']])
        self.assertTrue(all('blocks' not in row for row in reread['results']))
        self.assertEqual(reread['search_counts'],report['search_counts'])
        self.assertEqual(reread['complete'],report['complete'])
        self.assertEqual({str(path):records.sha256_file(path) for path in output.rglob('*') if path.is_file()},before)

    def test_invalid_limits_and_compact_diagnostics_fail_before_transport(self):
        for name,value in (('per_task_mem_gb',0),('total_mem_gb',float('inf')),
                           ('system_mem_reserve_gb',float('nan')),('timeout_s',float('inf'))):
            raw=copy.deepcopy(self.raw); raw['execution'][name]=value
            with self.subTest(limit=name),self.assertRaises(ValueError): scan.load_study(raw)
        raw=self.search_input(); raw['analysis'].update(store_search_blocks=False,diagnostics=True)
        with self.assertRaisesRegex(ValueError,'diagnostics'):
            scan.run_study(raw,self.root/'bad-compact')
        self.assertFalse(self.calls.exists())

    def test_measured_search_stops_incomplete_at_maximum_attempts(self):
        from unittest.mock import patch
        raw = self.search_input(evaluations=2)
        raw['search'].update(measured_evaluations=True, max_attempts=3)
        with patch('scan.run_task', return_value={'status': 'failed', 'reason': 'unmeasured'}):
            report = scan.run_study(raw, self.root / 'max-attempts')
        self.assertFalse(report['complete'])
        self.assertEqual(report['search_counts']['measured'], 0)
        self.assertEqual(report['search_counts']['attempts'], 3)
        self.assertIn('maximum attempts', report['incomplete_reason'])
        self.assertTrue(all(row['value'] is None for row in report['results']))


class ArchiveScaleTests(unittest.TestCase):
    setUp = WorkflowTests.setUp
    tearDown = WorkflowTests.tearDown
    make_executable = WorkflowTests.make_executable

    def test_archive_holds_stopped_writer_lock_until_publication(self):
        from unittest.mock import patch
        source = self.root / 'locked'; source.mkdir()
        with records.Results(source / 'results.sqlite') as database:
            database.save_state('measured', True)
        original = records.inventory
        def inspect(root):
            with self.assertRaisesRegex(RuntimeError, 'writer'):
                with records.Results(source / 'results.sqlite'): pass
            return original(root)
        with patch.object(records, 'inventory', side_effect=inspect):
            receipt = records.create_archive(source, None, self.root / 'locked.tar.gz', include_manifest=False)
        self.assertTrue(receipt['verified'])
        with records.Results(source / 'results.sqlite') as database:
            self.assertTrue(database.read('state', 'measured'))

    def legacy_archive(self, source, destination, *, manifest=None, payloads=None):
        import gzip
        manifest = records.inventory(source) if manifest is None else manifest
        with destination.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=6) as compressed, tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            encoded = (records.canonical_json(manifest) + '\n').encode()
            header = tarfile.TarInfo('MANIFEST.json'); header.size = len(encoded)
            archive.addfile(header, io.BytesIO(encoded))
            for name, data in (payloads if payloads is not None else [(entry['path'], (source / entry['path']).read_bytes()) for entry in manifest['files']]):
                header = tarfile.TarInfo(name); header.size = len(data)
                archive.addfile(header, io.BytesIO(data))
        return destination

    def test_compact_receipt_preserves_legacy_archive_bytes_and_metadata(self):
        source = self.root / 'small'; source.mkdir(); (source / 'a').write_bytes(b'scientific measurement')
        (source / 'nested').mkdir(); (source / 'nested/b').write_bytes(b'\x00\x01\xff')
        os.chmod(source / 'a', 0o640); os.utime(source / 'a', ns=(1200000000000, 1200000000000))
        expected = records.inventory(source)
        legacy = self.legacy_archive(source, self.root / 'legacy.tar.gz')
        compact = records.create_archive(source, None, self.root / 'streamed.tar.gz', include_manifest=False)
        self.assertNotIn('manifest', compact)
        self.assertTrue(compact['verified'])
        self.assertLess(len(json.dumps(compact)), 1024)
        self.assertEqual(compact['sha256'], records.sha256_file(legacy))
        verified = records.verify_archive(legacy)
        self.assertEqual(verified['manifest'], expected)
        self.assertEqual(verified['manifest_sha256'], compact['manifest_sha256'])
        restored = records.recover_archive(legacy, self.root / 'restored', compact['sha256'])
        self.assertEqual(records.inventory(restored), expected)
        self.assertEqual(records.create_archive(source, None, self.root / 'streamed.tar.gz', include_manifest=False), compact)
        (source / 'a').write_bytes(b'different')
        with self.assertRaises(FileExistsError):
            records.create_archive(source, None, self.root / 'streamed.tar.gz', include_manifest=False)

    def test_large_member_iteration_keeps_tar_metadata_cache_constant(self):
        from unittest.mock import patch
        source = self.root / 'many'; source.mkdir()
        for index in range(1024):
            folder = source / f'{index:05d}'; folder.mkdir()
            (folder / 'hits.csv').write_text('measured rows\n')
            (folder / 'run.log').write_text('complete\n')
        original_add = tarfile.TarFile.addfile; original_next = tarfile.TarFile.next
        writer_peak = []; reader_peak = []
        def add(archive, *args, **kwargs):
            result = original_add(archive, *args, **kwargs)
            writer_peak.append(len(archive.members)); return result
        def next_member(archive, *args, **kwargs):
            result = original_next(archive, *args, **kwargs)
            reader_peak.append(len(archive.members)); return result
        with patch.object(tarfile.TarFile, 'addfile', new=add), patch.object(tarfile.TarFile, 'next', new=next_member):
            compact = records.create_archive(source, None, self.root / 'many.tar.gz', include_manifest=False)
            restored = records.recover_archive(compact['path'], self.root / 'many-restored', compact['sha256'])
        self.assertEqual(compact['members'], 2048)
        self.assertLessEqual(max(writer_peak), 1)
        self.assertLessEqual(max(reader_peak), 1)
        self.assertEqual(records.inventory(restored), records.inventory(source))

    def test_stream_verification_rejects_payload_member_and_metadata_tampering(self):
        source = self.root / 'tamper'; source.mkdir()
        (source / 'a').write_bytes(b'abc'); (source / 'b').write_bytes(b'def')
        manifest = records.inventory(source)
        variants = [([('b', b'def'), ('a', b'abc')], manifest),
                    ([('a', b'abc'), ('a', b'def')], manifest),
                    ([('a', b'abc'), ('b', b'def'), ('extra', b'x')], manifest),
                    ([('a', b'bad'), ('b', b'def')], manifest)]
        wrong_mode = copy.deepcopy(manifest); wrong_mode['files'][0]['mode'] = 0o1000
        variants.append(([('a', b'abc'), ('b', b'def')], wrong_mode))
        duplicate = copy.deepcopy(manifest); duplicate['files'].append(copy.deepcopy(duplicate['files'][-1]))
        variants.append(([('a', b'abc'), ('b', b'def'), ('b', b'def')], duplicate))
        for index, (payloads, declared) in enumerate(variants):
            with self.subTest(index=index):
                path = self.legacy_archive(source, self.root / f'bad-{index}.tar.gz', manifest=declared, payloads=payloads)
                with self.assertRaises(ValueError): records.verify_archive(path, include_manifest=False)
        good = self.legacy_archive(source, self.root / 'good.tar.gz')
        with self.assertRaises(ValueError): records.verify_archive(good, expected_paths=['a'])
        with self.assertRaises(ValueError): records.verify_archive(good, expected_archive_sha256='0' * 64)
        damaged = bytearray(good.read_bytes()); damaged[-8] ^= 1
        corrupted = self.root / 'bad-crc.tar.gz'; corrupted.write_bytes(damaged)
        with self.assertRaises((ValueError, OSError, EOFError)): records.verify_archive(corrupted)

    def test_archived_study_keeps_summary_and_recovers_raw_reanalysis(self):
        output = self.root / 'completed'
        expected = scan.run_study(self.raw, output)
        original_sqlite = records.sha256_file(output / 'results.sqlite')
        receipt = records.create_archive(output, None, self.root / 'completed.tar.gz', include_manifest=False)
        kept = {name: (output / name).read_bytes() for name in ('summary.json', 'resolved.json', 'build.json')}
        summary = json.loads(kept['summary.json'])
        summary.update(raw_files_present=False, archive=receipt)
        shutil.rmtree(output); output.mkdir()
        for name, data in kept.items(): (output / name).write_bytes(data)
        records.write_json(output / 'summary.json', summary, overwrite=True)
        records.write_json(output / 'archive.json', receipt)
        compact = records.load_result(output)
        self.assertTrue(compact['complete'])
        self.assertFalse(compact['raw_files_present'])
        self.assertTrue(compact['archive']['verified'])
        self.assertTrue(all(not Path(block['output']).exists() for row in compact['results'] for block in row['blocks']))
        with self.assertRaisesRegex(ValueError, 'recover.*archive'):
            analysis.read_analysis(output)
        with self.assertRaisesRegex(ValueError, 'recover.*archive'):
            scan.run_study(self.raw, output)
        restored = records.recover_archive(receipt['path'], self.root / 'raw-restored', receipt['sha256'])
        self.assertEqual(records.sha256_file(restored / 'results.sqlite'), original_sqlite)
        result = analysis.read_analysis(restored)
        self.assertEqual(result['results'][0]['value'], expected['results'][0]['value'])
        self.assertEqual(result['results'][0]['standard_error'], expected['results'][0]['standard_error'])
        self.assertTrue(all(Path(block['output']).is_file() for row in result['results'] for block in row['blocks']))
        self.assertEqual(records.sha256_file(restored / 'results.sqlite'), original_sqlite)

    def test_confirmed_pair_statistics_survive_compact_storage_and_raw_recovery(self):
        script = self.executable.read_text().replace('output=None', 'output=None\nsound=2.444\nseed=0')
        script = script.replace("    row=raw.strip()", "    row=raw.strip()\n    if row.startswith('/main/detector_param/setTopFilmVSound '): sound=float(row.split()[1])\n    if row.startswith('/random/setSeeds '): seed=int(row.split()[1])")
        script = script.replace('writer.writerow([.00191,0,0,.0002625,100])',
                                'writer.writerow([.000191*(10+seed%7+(1+seed%3)*int(sound>3)),0,0,.0002625,100])')
        self.executable.write_text(script)
        raw = copy.deepcopy(self.raw)
        raw['analysis']['objective'] = 'device_junction_qps_per_eV'
        raw['candidates'] = [{'name': 'reference', 'values': {}}, {'name': 'changed-speed', 'values': {'topfilm_vsound': 4.}}]
        output = self.root / 'paired-confirmation'
        expected = scan.run_study(raw, output)
        self.assertGreater(expected['comparisons'][0]['standard_error'], 0.)
        receipt = records.create_archive(output, None, self.root / 'paired.tar.gz', include_manifest=False)
        kept = {name: (output / name).read_bytes() for name in ('summary.json', 'resolved.json', 'build.json')}
        original_sqlite = records.sha256_file(output / 'results.sqlite')
        compact = json.loads(kept['summary.json']); compact.update(raw_files_present=False, archive=receipt)
        shutil.rmtree(output); output.mkdir()
        for name, data in kept.items(): (output / name).write_bytes(data)
        records.write_json(output / 'summary.json', compact, overwrite=True)
        self.assertEqual(records.load_result(output)['comparisons'], expected['comparisons'])
        restored = records.recover_archive(receipt['path'], self.root / 'paired-restored', receipt['sha256'])
        recomputed = analysis.read_analysis(restored)
        self.assertEqual(recomputed['comparisons'], expected['comparisons'])
        self.assertEqual([(row['value'], row['standard_error']) for row in recomputed['results']],
                         [(row['value'], row['standard_error']) for row in expected['results']])
        self.assertEqual(records.sha256_file(restored / 'results.sqlite'), original_sqlite)


class QueueTests(unittest.TestCase):
    setUp = WorkflowTests.setUp
    tearDown = WorkflowTests.tearDown
    make_executable = WorkflowTests.make_executable

    def test_optimization_configuration_keeps_equal_work_and_reserved_resources(self):
        cfg=records.load_document(ROOT/'studies/optimization/queue.yaml')
        self.assertEqual(len(cfg['search_bounds']),6)
        self.assertEqual(len(cfg['methods']),7)
        self.assertEqual(len(cfg['search_seeds']),3)
        self.assertLessEqual(cfg['execution']['max_gpus'],6)
        self.assertEqual(cfg['execution']['workers']+2+cfg['execution']['llm_cpu_count'],96)
        self.assertLessEqual(max(cfg['defaults']['design_counts']),1024)
        calibration=records.load_document(ROOT/cfg['calibration_file'])
        self.assertEqual(len(calibration['candidates']),12)
        self.assertTrue(calibration['old_values_not_reused'])
        namespace=self.queue_python(scope="mode in ('check', 'prepare')",start='queue =',
            stop="groups =",cfg=cfg,defaults=cfg['defaults'],optimization=True,source=ROOT,run=self.root)
        queue=namespace['queue']
        self.assertEqual(len(queue),56)
        self.assertEqual(sum(step['kind']=='optimization_initial' for step in queue),4)
        main=[step for step in queue if step['kind']=='optimization_search' and not step['id'].startswith('pilot-')]
        self.assertEqual(len(main),21)
        self.assertTrue(all(step['count']==32 for step in main))
        self.assertEqual(sum(step['kind']=='optimization_drift' for step in queue),21)
        planned=records.load_document(self.root/'planned-computation.json')
        self.assertEqual(planned['primaries_upper_bound'],106240000000)
        self.assertEqual(552+192,planned['new_measurements_screening'])

    def test_physical_change_study_accepts_varied_inputs_and_keeps_matched_streams(self):
        fixed=records.load_document(ROOT/'studies/optimization/refinement.yaml')['fixed_studies'][0]
        raw=copy.deepcopy(self.raw)
        raw.setdefault('parameters',{})
        raw['sampling'].update(independent_candidate_streams=True)
        reference=scan.load_study(raw)
        background=physics.prepare_values(reference)['values']
        records.write_json(self.root/'calibration-background.json',{'candidate':{'values':background}})
        namespace=self.queue_python(branch="kind == 'optimization_fixed'",scope='stage',
                                    step=fixed,raw=raw,reference=reference,identifier=fixed['id'],run=self.root)
        prepared_study=scan.load_study(namespace['raw'])
        self.assertFalse(prepared_study['sampling']['independent_candidate_streams'])
        self.assertFalse(prepared_study['provenance']['selection_uses_confirmation'])
        points=[physics.prepare_values(prepared_study,point) for point in prepared_study['candidates']]
        self.assertEqual(len(points),17)
        self.assertEqual(len({records.content_hash(point['values']) for point in points}),17)
        plans=[scan.task_plan(prepared_study,point,{'build':'same'}) for point in points]
        streams=[[(task['site_mm'],task['replica'],task['seeds'],task['events']) for task in plan]
                 for plan in plans]
        self.assertTrue(all(stream==streams[0] for stream in streams))
        self.assertTrue(all(point['fixed']['gun_energy_eV']==.01 for point in points))

    def test_added_methods_preserve_original_comparisons_and_reserved_confirmation(self):
        cfg=records.load_document(ROOT/'studies/optimization/queue.yaml')
        refinement=records.load_document(ROOT/'studies/optimization/refinement.yaml')
        original_dir=self.root/'original'; original_dir.mkdir()
        added_dir=self.root/'added'; added_dir.mkdir()
        original=self.queue_python(scope="mode in ('check', 'prepare')",start='queue =',stop='groups =',
            cfg=cfg,defaults=cfg['defaults'],optimization=True,source=ROOT,run=original_dir)['queue']
        extended=copy.deepcopy(cfg)
        extended['original_methods']=list(cfg['methods'])
        extended['methods']+=refinement['additional_methods']
        extended['method_settings'].update(refinement['method_settings'])
        added=self.queue_python(scope="mode in ('check', 'prepare')",start='queue =',stop='groups =',
            cfg=extended,defaults=extended['defaults'],optimization=True,source=ROOT,run=added_dir,
            refinement=refinement)['queue']
        self.assertEqual(len(original),56)
        self.assertEqual(len(added),70)
        by_id={step['id']:step for step in added}
        self.assertTrue(all(by_id[step['id']]==step for step in original))
        extra=[step for step in added if step['kind']=='optimization_search'
               and step['method'] in refinement['additional_methods']]
        self.assertEqual(len(extra),6)
        self.assertTrue(all(step['count']==32 and step['initial']==f"initial-{step['seed']}" for step in extra))
        main_sampling=next(step['sampling'] for step in original
                           if step['kind']=='optimization_search' and not step['id'].startswith('pilot-'))
        self.assertTrue(all(step['sampling']==main_sampling for step in extra))
        fixed=[step for step in added if step['kind']=='optimization_fixed']
        self.assertEqual(sum(len(step['candidates']) for step in fixed),23)
        confirmation=[step for step in added if step['kind']=='optimization_confirmation']
        self.assertEqual(len(confirmation),4)
        self.assertTrue(all(step['sampling']['design']==1024 for step in confirmation))
        self.assertTrue(all(step['bank'] not in {row['bank'] for row in added if row['kind']!='optimization_confirmation'}
                            for step in confirmation))
        planned=records.load_document(added_dir/'planned-computation.json')
        self.assertEqual(planned['new_measurements_screening'],936)
        self.assertEqual(planned['confirmation_targets_upper_bound'],35)
        self.assertEqual(planned['primaries_upper_bound'],136768000000)

    def test_native_material_winner_keeps_its_lattice_and_films_in_confirmation(self):
        germanium=self.root/'maps/Ge'; germanium.mkdir()
        text=(self.root/'maps/Si/config.txt').read_text()
        (germanium/'config.txt').write_text(text.replace('5.43095','5.658')
            .replace('165.6','128.5').replace('63.9','48.3').replace('79.5','66.8'))
        native={'name':'Ge-Nb-Au','materials':['Ge','Nb','Au']}
        native_raw={**copy.deepcopy(self.raw),'candidates':[native]}
        prepared=physics.prepare_values(scan.load_study(native_raw),native)
        records.write_json(self.root/'studies/native-materials/summary.json',{
            'complete':True,'results':[{'name':native['name'],'status':'complete','value':.1,
                'candidate_id':'measured-native','parameters':prepared['values']}]})
        records.write_json(self.root/'definitions/native-materials.yaml',native_raw)
        raw=copy.deepcopy(self.raw)
        namespace=self.queue_python(branch="kind == 'optimization_confirmation'",scope='stage',
            run=self.root,raw=raw,queue=[{'id':'native-materials','kind':'optimization_fixed'}],
            cfg={'confirmation_anchors':['reference']},calibration={'candidates':[{'name':'reference','values':{}}]},
            step={'position_seed':20261025},defaults={'bootstrap_draws':32})
        selection=records.load_document(self.root/'selected-leaders.json')
        self.assertFalse(selection['selection_uses_confirmation'])
        winner=next(point for point in namespace['raw']['candidates'] if point['name']=='native-materials')
        self.assertEqual(winner['materials'],['Ge','Nb','Au'])
        confirmed=physics.prepare_values(scan.load_study(namespace['raw']),winner)
        self.assertEqual(confirmed['derived']['g4_material_name'],'G4_Ge')
        self.assertEqual(confirmed['derived']['lattice_map_name'],'Ge')
        self.assertEqual(confirmed['derived']['config_mode'],'native_g4cmp')
        self.assertEqual(confirmed['values'],prepared['values'])
        self.assertEqual(confirmed['top_film'],prepared['top_film'])
        self.assertEqual(confirmed['bottom_film'],prepared['bottom_film'])

    def test_restart_reuses_verified_cpu_measurements_only_with_identical_physical_inputs(self):
        from unittest.mock import patch
        previous=self.root/'previous'; restarted=self.root/'restarted'
        queue=[{'id':'bo-101','kind':'optimization_search','method':'bo'},
               {'id':'llm_proposals-101','kind':'optimization_search','method':'llm_proposals'},
               {'id':'bo-202','kind':'optimization_search','method':'bo'}]
        snapshot={'source_sha256':{name:'same-code' for name in
                                  ['scan.py','physics.py','analysis.py','records.py','parameters.yaml']},
                  'files_sha256':{'bin/Main':'same-transport','lib/libG4CMP.so':'same-library',
                                  'CrystalMaps/Si/config.txt':'same-lattice','studies/reference.yaml':'same-reference',
                                  'studies/optimization/calibration.yaml':'same-calibration'}}
        for run in (previous,restarted):
            records.write_json(run/'source-ready.json',snapshot)
            records.write_json(run/'designs/stratified_2.json',{'sites_mm':[[0,0,0],[1,1,0]]})
        records.write_json(previous/'queue.json',queue)
        output=previous/'studies/bo-101'
        report={'complete':True,'results':[{'status':'complete','value':.1,'candidate_id':'measured'}]}
        records.write_json(output/'summary.json',report)
        (output/'measured.txt').write_text('saved complete numerical fixture\n')
        receipt=records.create_archive(output,None,previous/'archives/bo-101.tar.gz',include_manifest=False)
        independent=previous/'second/bo-101.tar.gz'; independent.parent.mkdir()
        shutil.copy2(receipt['path'],independent)
        receipt.update(independent_copy=str(independent),independent_copy_verified=True,
                       summary_content_hash=records.content_hash(report))
        records.write_json(output/'summary.json',{**report,'archive':receipt},overwrite=True)
        records.write_json(previous/'studies/llm_proposals-101/summary.json',{
            **report,'archive':{'verified':True},'ai':{'protocol':3,'valid':True}})
        records.write_json(previous/'studies/bo-202/summary.json',{'complete':False,'results':[]})
        before=records.inventory(previous)
        def reuse():
            return self.queue_python('verify_saved_archive',scope='reuse',start='previous =',
                run=restarted,queue=queue,defaults={'design_counts':[2]},
                cfg={'calibration_file':'studies/optimization/calibration.yaml'})
        with patch.object(sys,'argv',['-',str(previous)]):
            for change,message in [('code','physical evaluation'),('library','transport executable'),
                                   ('design','source positions')]:
                changed=copy.deepcopy(snapshot)
                if change=='code': changed['source_sha256']['physics.py']='changed-equation'
                if change=='library': changed['files_sha256']['lib/libG4CMP.so']='changed-library'
                records.write_json(restarted/'source-ready.json',changed,overwrite=True)
                records.write_json(restarted/'designs/stratified_2.json',
                    {'sites_mm':[[0,0,0],[2 if change=='design' else 1,1,0]]},overwrite=True)
                with self.subTest(change=change),self.assertRaisesRegex(ValueError,message): reuse()
                self.assertFalse((restarted/'studies').exists())
            records.write_json(restarted/'source-ready.json',snapshot,overwrite=True)
            shutil.copy2(previous/'designs/stratified_2.json',restarted/'designs/stratified_2.json')
            with self.assertRaises(SystemExit) as stopped: reuse()
            self.assertEqual(stopped.exception.code,0)
        retained=records.load_document(restarted/'reuse.json')
        self.assertEqual([row['id'] for row in retained['retained']],['bo-101'])
        self.assertEqual(set(retained['not_reused']),{'llm_proposals-101','bo-202'})
        self.assertEqual(records.load_document(restarted/'studies/bo-101/summary.json')['archive']['path'],receipt['path'])
        self.assertFalse((restarted/'studies/llm_proposals-101').exists())
        self.assertEqual(records.inventory(previous),before)

    def test_queue_memory_guard_includes_only_the_identified_llm_service(self):
        script=(ROOT/'run_all.sh').read_text()
        fragment=script[script.index("    service=root/'ollama-service.json'"):]
        fragment=fragment[:fragment.index('    while True:')]
        import textwrap
        created=int(Path(f'/proc/{os.getpid()}/stat').read_text().split(') ',1)[1].split()[19])
        for start,expected in ((created,True),(str(created),True),(created+1,False)):
            records.write_json(self.root/'ollama-service.json',{'pid':os.getpid(),'starttime':start},overwrite=True)
            namespace={'root':self.root,'Path':Path,'json':json,'descendants':set()}
            exec(textwrap.dedent(fragment),namespace)
            self.assertEqual(os.getpid() in namespace['descendants'],expected)

    def test_ai_evidence_requires_inference_proposals_and_tools_but_not_selection(self):
        step={'kind':'optimization_search','method':'llm_tools','id':'llm_tools-101'}
        records.write_json(self.root/'gpu_benchmark.json',{'chosen_model':'local:1','digest':'weights'})
        (self.root/'definitions').mkdir()
        records.write_json(self.root/'definitions/llm_tools-101.yaml',{'search':{'options':{'model':'local:1','model_digest':'weights'}}})
        namespace=self.queue_python('require_ai_evidence',run=self.root)
        gate=namespace['require_ai_evidence']
        valid={'ai':{'required':True,'valid':True,'model':'local:1','digest':'weights',
            'stats':{'calls':1,'accepted':1,'tools':1},'records':[{'model':'local:1','digest':'weights'},
            {'model':'local:1','error':'earlier connection failure'}],
            'selected_llm_points':0,'measured_llm_points':0}}
        gate(step,valid)
        for change in ('missing','calls','accepted','tools','identity'):
            report=copy.deepcopy(valid)
            if change=='missing': report.pop('ai')
            elif change=='identity': report['ai']['records'][0]['digest']='different'
            else: report['ai']['stats'][change]=0
            with self.subTest(change=change),self.assertRaises(SystemExit) as stopped: gate(step,report)
            self.assertEqual(stopped.exception.code,75)
            self.assertEqual(records.load_document(self.root/'queue_status.json')['state'],'paused')

    def test_ai_pause_reports_current_failure_despite_successful_prior_inference(self):
        step={'kind':'optimization_search','method':'llm_tools','id':'llm_tools-101'}
        reason='Ollama final output reached the generation limit without candidate JSON'
        report={'complete':False,'status':'paused','incomplete_reason':reason,
                'ai':{'required':True,'valid':False,'model':'local:1','digest':'weights',
                      'stats':{'calls':15,'accepted':16,'tools':12},
                      'records':[{'model':'local:1','digest':'weights','phase':'tools'},
                                 {'model':'local:1','digest':'weights','output_error':reason}],
                      'selected_llm_points':4,'measured_llm_points':4}}
        before=copy.deepcopy(report)
        gate=self.queue_python('require_ai_evidence',run=self.root)['require_ai_evidence']
        with self.assertRaises(SystemExit) as stopped:
            gate(step,report)
        self.assertEqual(stopped.exception.code,75)
        status=records.load_document(self.root/'queue_status.json')
        self.assertEqual(status['state'],'paused')
        self.assertEqual(status['stage'],step['id'])
        self.assertEqual(status['reason'],reason)
        self.assertEqual(report,before)

    def test_only_ai_failures_are_deferred_and_confirmation_waits_for_screening(self):
        queue=[{'id':'llm_tools-101','kind':'optimization_search','method':'llm_tools'},
               {'id':'bo-202','kind':'optimization_search','method':'bo'},
               {'id':'confirmation-01','kind':'optimization_confirmation'}]
        reason='Ollama final output exhausted the generation limit'
        report={'complete':False,'status':'paused','incomplete_reason':reason,
                'search_counts':{'measured':20},'ai':{'valid':False,'stats':{'calls':15,'tools':12,'accepted':16}}}
        records.write_json(self.root/'studies/llm_tools-101/summary.json',report)
        records.write_json(self.root/'studies/bo-202/summary.json',{'complete':True})
        records.write_json(self.root/'queue_status.json',{'state':'paused','reason':reason})
        with self.assertRaises(SystemExit) as stopped:
            self.queue_python(scope='defer-ai',start='step =',queue=queue,index=0,run=self.root)
        self.assertEqual(stopped.exception.code,0)
        saved=records.load_document(self.root/'ai-paused.json')
        self.assertEqual(saved['llm_tools-101']['reason'],reason)
        self.assertEqual(saved['llm_tools-101']['stats'],report['ai']['stats'])
        self.assertEqual(saved['llm_tools-101']['measured'],20)
        with self.assertRaises(SystemExit) as stopped:
            self.queue_python(scope='defer-ai',start='step =',queue=queue,index=1,run=self.root)
        self.assertEqual(stopped.exception.code,1)
        self.assertEqual(records.load_document(self.root/'ai-paused.json'),saved)
        with self.assertRaises(SystemExit) as stopped:
            self.queue_python(scope='stage',start='step =',stop="if step['kind'] == 'optimization_drift':",
                              queue=queue,index=2,run=self.root)
        self.assertEqual(stopped.exception.code,0)
        self.assertEqual(records.load_document(self.root/'confirmation-waiting.json')['pending'],['llm_tools-101'])
        self.assertFalse((self.root/'selected-leaders.json').exists())

    def test_nonzero_search_cannot_reuse_a_stale_exhaustion_summary(self):
        from unittest.mock import patch
        report={'complete':False,'search_counts':{'measured':2},
                'incomplete_reason':'elapsed limit or maximum attempts reached before the measured target'}
        path=self.root/'summary.json'; records.write_json(path,report)
        stamp=path.stat().st_mtime_ns
        script=(ROOT/'run_all.sh').read_text(); start=script.index("limited=report.get('incomplete_reason')")
        fragment=script[start:script.index('\nPY',start)]
        for launch,code in ((stamp,0),(stamp+1,1)):
            with patch.object(sys,'argv',['-',str(self.root),'0',str(self.root),str(launch)]):
                with self.assertRaises(SystemExit) as stopped:
                    exec(fragment,{'report':report,'path':path,'step':{'kind':'optimization_search'},'sys':sys})
            self.assertEqual(stopped.exception.code,code)

    def queue_command(self, mode, run, *arguments):
        environment={**os.environ,'MATERIAL_REPOSITORY':str(ROOT),'MATERIAL_PYTHON':sys.executable}
        return subprocess.run(['bash',str(ROOT/'run_all.sh'),mode,str(run),*map(str,arguments)],
                              cwd=self.root,env=environment,capture_output=True,text=True,timeout=30)

    def queue_python(self, *functions, branch=None, scope=None, start=None, stop=None, **values):
        import ast
        import itertools
        import math
        text=(ROOT/'run_all.sh').read_text().rsplit("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        tree=ast.parse(text)
        code=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in functions]
        self.assertEqual(len(code),len(functions))
        if 'require_ai_evidence' not in functions:
            code.extend(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='require_ai_evidence')
        if scope is not None:
            condition=scope if scope.startswith('mode ') else f'mode == {scope!r}'
            scopes=[node for node in ast.walk(tree) if isinstance(node,ast.If) and ast.unparse(node.test)==condition]
            self.assertEqual(len(scopes),1)
            tree=scopes[0]
        if branch is not None:
            matched=[node for node in ast.walk(tree) if isinstance(node,ast.If) and ast.unparse(node.test)==branch]
            self.assertEqual(len(matched),1)
            code.extend(matched[0].body)
        if start is not None:
            first=next(i for i,node in enumerate(tree.body) if ast.unparse(node).startswith(start))
            last=next((i for i,node in enumerate(tree.body[first+1:],first+1)
                       if stop and ast.unparse(node).startswith(stop)),len(tree.body))
            code.extend(tree.body[first:last])
        namespace={'records':records,'analysis':analysis,'scan':scan,'physics':physics,
                   'np':np,'copy':copy,'math':math,'itertools':itertools,'Path':Path,
                   'os':os,'time':time,'sys':sys,'json':json,'shutil':shutil,
                   'max_sites':1024,'initial_checks':False,'optimization':False,'refinement':{},**values}
        exec(compile(ast.Module(body=code,type_ignores=[]),'isolated queue Python','exec'),namespace)
        return namespace

    def test_layout_averages_keep_paired_relative_errors(self):
        reports=[]
        references=[10.,20.,30.,40.]; ratios=[.9,.8,.7,.6]
        for index,(reference,ratio) in enumerate(zip(references,ratios)):
            rows=[{'name':name,'value':value,'standard_error':99.,'units':'QP/eV',
                   'estimate':{'means_by_stratum':{'region':value},'maximum_site_leverage':.1}}
                  for name,value in [('reference',reference),('target',reference*ratio)]]
            reports.append({'id':f'layout-{index}','results':rows})
        namespace=self.queue_python('mean_interval','layout_summary')
        result=namespace['layout_summary'](reports)
        pair=result['comparisons'][0]
        relative=np.asarray(ratios)-1
        self.assertAlmostEqual(pair['relative_to_b'],float(relative.mean()))
        self.assertAlmostEqual(pair['relative_change']['standard_error'],float(relative.std(ddof=1)/2))
        self.assertAlmostEqual(pair['standard_error'],float(np.std(np.asarray(references)*relative,ddof=1)/2))
        self.assertNotAlmostEqual(pair['relative_to_b'],result['results'][1]['value']/result['results'][0]['value']-1)
        self.assertEqual(pair['relative_change']['n_layouts'],4)
        self.assertEqual(result['position_layouts'],[f'layout-{index}' for index in range(4)])

    def test_source_limit_checks_every_declared_layout(self):
        cfg={'studies':[{'kind':'convergence','sites':[512,1024]},
                        {'kind':'independent_positions','sampling':{'design':1024}},
                        {'kind':'search_comparison','sampling':{'sites':512}}]}
        defaults={'design_counts':[128,512,1024]}
        self.queue_python(scope="mode in ('check', 'prepare')",start='requested =',stop='allowed =',
                          defaults=defaults,cfg=cfg)
        for section,value in (('design',2653),('independent',2048),('convergence',1536),('design',True)):
            with self.subTest(section=section,value=value):
                changed_cfg=copy.deepcopy(cfg); changed_defaults=copy.deepcopy(defaults)
                if section=='design': changed_defaults['design_counts'].append(value)
                elif section=='independent': changed_cfg['studies'][1]['sampling']['design']=value
                else: changed_cfg['studies'][0]['sites'].append(value)
                with self.assertRaisesRegex(ValueError,'1 to 1024 sites'):
                    self.queue_python(scope="mode in ('check', 'prepare')",start='requested =',stop='allowed =',
                                      defaults=changed_defaults,cfg=changed_cfg)
        cfg=records.load_document(ROOT/'studies/full_scale/queue.yaml')
        limits={'workers':2,'total_mem_gb':1.,'per_task_mem_gb':.5,'system_mem_reserve_gb':0.}
        namespace=self.queue_python('base_study',cfg=cfg,defaults=cfg['defaults'],limits=limits,
                                    repo=ROOT,source=self.root/'absent',run=self.root)
        with self.assertRaisesRegex(ValueError,'exceeds the 1024-site limit'):
            namespace['base_study']('too-large',{'sites':2048,'replicas':2,'events_per_task':1})

    def test_recorded_512_layout_is_retained_when_extended_to_1024(self):
        run=self.root/'nested layouts'; run.mkdir()
        fixed=copy.deepcopy(scan.load_study(self.raw)['fixed'])
        allocation=dict(zip(physics.REGIONS,[68,64,128,128,124]))
        previous=physics.build_design(fixed,{'kind':'stratified','sites':512,'position_seed':303,
                                            'allocation':allocation,'area_samples':65536,'area_seed':0})
        previous['design_hash']=records.content_hash({key:value for key,value in previous.items() if key!='design_hash'})
        base_file=self.root/'recorded_512.json'; records.write_json(base_file,previous)
        defaults=copy.deepcopy(records.load_document(ROOT/'studies/full_scale/queue.yaml')['defaults'])
        defaults.update(base_design_file=str(base_file),design_counts=[128,512,1024],area_samples=65536)
        defaults['allocations'].update({'512':list(allocation.values()),'1024':[136,128,256,256,248]})
        cfg={'studies':[{'kind':'convergence','sites':[512,1024]}]}
        self.queue_python(scope="mode in ('check', 'prepare')",start='fixed = reference',stop='independent_root =',
                          reference={'fixed':fixed},defaults=defaults,cfg=cfg,repo=ROOT,run=run)
        kept=records.load_document(run/'designs/stratified_512.json')
        fine=records.load_document(run/'designs/stratified_1024.json')
        self.assertEqual(kept,previous)
        self.assertEqual(fine['sites_mm'][:512],previous['sites_mm'])
        self.assertEqual(fine['stratum'][:512],previous['stratum'])
        self.assertEqual(fine['stratum_weights'],previous['stratum_weights'])
        self.assertEqual(fine['stratum_counts'],dict(zip(physics.REGIONS,[136,128,256,256,248])))
        layouts=[records.load_document(run/'designs'/f'source-independent-{index:02d}_1024.json')
                 for index in range(1,5)]
        self.assertEqual(len({records.content_hash(layout['sites_mm']) for layout in layouts}),4)
        self.assertTrue(all(layout['sites_mm']!=fine['sites_mm'] for layout in layouts))
        self.assertTrue(all(layout['stratum_counts']==fine['stratum_counts'] for layout in layouts))
        self.assertEqual(records.sha256_file(base_file),records.load_document(run/'base-design.json')['file_sha256'])

    def test_stage_rejects_recorded_layout_larger_than_its_declaration(self):
        fixed=scan.load_study(self.raw)['fixed']
        for actual,allocation in ((512,[68,64,128,128,124]),(2653,[68,64,256,415,1850])):
            with self.subTest(actual_sites=actual):
                run=self.root/f'misdeclared-{actual}'; run.mkdir()
                design=physics.build_design(fixed,{'kind':'stratified','sites':actual,'position_seed':303,
                    'allocation':dict(zip(physics.REGIONS,allocation)),'area_samples':65536,'area_seed':0})
                records.write_json(run/'designs/stratified_128.json',design)
                records.write_json(run/'queue.json',[{'id':'misdeclared','kind':'pilot',
                    'sampling':{'design':128,'replicas':2,'events_per_task':1,'seed_bank':2100}}])
                rejected=self.queue_command('stage',run,0)
                self.assertNotEqual(rejected.returncode,0)
                self.assertIn('recorded source design differs from its declared count or exceeds the site limit',rejected.stderr)
                self.assertFalse((run/'queue_status.json').exists())
                self.assertFalse((run/'studies/misdeclared/results.sqlite').exists())
        self.assertFalse(self.calls.exists())

    def test_initial_queue_stops_before_search_and_material_studies(self):
        cfg=copy.deepcopy(records.load_document(ROOT/'studies/full_scale/queue.yaml'))
        convergence=next(group for group in cfg['studies'] if group['kind']=='convergence')
        convergence['sites']=[512,1024]
        namespace=self.queue_python(scope="mode in ('check', 'prepare')",start="queue =",
                                    stop="records.write_json(run / 'queue.json'",cfg=cfg,defaults=cfg['defaults'],
                                    initial_checks=True,run=self.root)
        queue=namespace['queue']; kinds={step['kind'] for step in queue}
        self.assertEqual(kinds,{'pilot','convergence','independent_positions','source_validation','event_count_convergence'})
        self.assertEqual([step['id'] for step in queue if step['kind']=='convergence'],['source-512','source-1024'])
        self.assertEqual(sum(step['kind']=='independent_positions' for step in queue),4)
        self.assertEqual(queue[-1]['kind'],'event_count_convergence')
        self.assertFalse(kinds&{'search','confirmation','morris','explicit_tolerance','model_ranges'})

    def test_source_validation_rejects_imprecision_and_retained_layout_bias(self):
        for label,values,retained,precise in (('imprecise',[1.,10.,1.,10.],5.5,False),
                                             ('biased',[10.]*4,8.,True)):
            with self.subTest(label=label):
                run=self.root/label; run.mkdir()
                records.write_json(run/'queue.json',[{'id':'source-validation','kind':'source_validation'}])
                records.write_json(run/'studies/source-1024/summary.json',{
                    'spatial_check':{'passed':True},'results':[{'name':'reference','value':retained,
                    'standard_error':0.,'estimate':{'means_by_stratum':{'region':retained}}}]})
                for index,value in enumerate(values,1):
                    records.write_json(run/'studies'/f'source-independent-{index:02d}'/'summary.json',
                                       {'id':f'source-independent-{index:02d}','results':[{
                                           'name':'reference','value':value,'units':'QP/eV',
                                           'estimate':{'means_by_stratum':{'region':value},'maximum_site_leverage':.001}}]})
                cfg={'studies':[{'kind':'convergence','sites':[512,1024]},
                                {'kind':'independent_positions','check':{'relative_limit':.05}}]}
                with self.assertRaisesRegex(ValueError,'source-position checks did not pass'):
                    self.queue_python('mean_interval','layout_summary','selected_spatial_sites',
                                      scope='stage',branch="step['kind'] in ('source_validation', 'finalist_validation')",
                                      step={'kind':'source_validation'},identifier='source-validation',cfg=cfg,run=run)
                validation=records.load_document(run/'source-validation.json')
                self.assertTrue(validation['nested']['passed'])
                self.assertFalse(validation['passed'])
                self.assertEqual(validation['candidates']['reference']['passed'],precise)
                if precise:
                    self.assertFalse(validation['retained_layout_agreement']['reference']['passed'])
                    self.assertEqual(validation['retained_layout_agreement']['reference']['difference'],2.)
                self.assertEqual(records.load_document(run/'queue_status.json')['state'],'needs_more_positions')
                self.assertFalse((run/'source-validation-skipped.json').exists())

    def test_initial_source_validation_retains_failed_checks_without_claiming_convergence(self):
        from contextlib import redirect_stdout
        run=self.root/'diagnostic validation'; run.mkdir()
        records.write_json(run/'studies/source-1024/summary.json',{
            'spatial_check':{'passed':False},'results':[{'name':'reference','value':10.,
            'standard_error':0.,'estimate':{'means_by_stratum':{'region':10.}}}]})
        for index,value in enumerate([1.,10.,1.,10.],1):
            records.write_json(run/'studies'/f'source-independent-{index:02d}'/'summary.json',
                               {'id':f'source-independent-{index:02d}','results':[{
                                   'name':'reference','value':value,'units':'QP/eV',
                                   'estimate':{'means_by_stratum':{'region':value},'maximum_site_leverage':.001}}]})
        cfg={'studies':[{'kind':'convergence','sites':[512,1024]},
                        {'kind':'independent_positions','check':{'relative_limit':.05}}]}
        output=io.StringIO()
        with redirect_stdout(output),self.assertRaises(SystemExit) as stopped:
            self.queue_python('mean_interval','layout_summary','selected_spatial_sites',scope='stage',
                              branch="step['kind'] in ('source_validation', 'finalist_validation')",
                              step={'kind':'source_validation'},identifier='source-validation',cfg=cfg,run=run,
                              initial_checks=True)
        self.assertEqual(stopped.exception.code,0)
        self.assertEqual(output.getvalue().strip(),'SKIP')
        validation=records.load_document(run/'source-validation.json')
        self.assertFalse(validation['passed'])
        self.assertFalse(validation['nested']['passed'])
        self.assertFalse(validation['candidates']['reference']['passed'])
        self.assertFalse(records.load_document(run/'source-validation-skipped.json')['passed'])
        self.assertFalse((run/'queue_status.json').exists())

    def test_event_count_gate_uses_transport_precision_and_retains_area_error(self):
        run=self.root/'event gate'; output=run/'studies/events-125000'; output.mkdir(parents=True)
        records.write_json(run/'studies/events-31250/summary.json',
                           {'results':[{'name':'reference','estimate':{'by_replica':[10.]*4}}]})
        step={'kind':'event_count_convergence','events_per_task':125000,
              'check':{'relative_limit':.05,'relative_standard_error_limit':.02}}
        cfg={'studies':[{'kind':'event_count_convergence','events_per_task':[31250,125000,500000]}]}
        for replicas,area_error,passed in (([10.]*4,8.,True),([5.,10.,15.,10.],0.,False)):
            with self.subTest(replicas=replicas):
                report={'results':[{'name':'reference','standard_error':area_error,
                                    'estimate':{'by_replica':replicas}}]}
                self.queue_python(branch="step['kind'] == 'event_count_convergence'",
                                  step=step,cfg=cfg,run=run,output=output,report=report)
                check=report['event_count_check']['candidates']['reference']
                self.assertEqual(check['area_standard_error'],area_error)
                self.assertEqual(check['passed'],passed)
                self.assertEqual(report['event_count_check']['passed'],passed)
                self.assertAlmostEqual(check['transport']['standard_error'],float(np.std(replicas,ddof=1)/2))

    def test_tolerance_stages_measure_centers_once_and_emit_only_moves_in_batches(self):
        run=self.root/'tolerance stages'; run.mkdir()
        selected=[{'name':'best','values':{'sub_c44':70.}},{'name':'other','values':{'sub_c44':90.}}]
        records.write_json(run/'finalist-validation.json',{'independent_layouts':{'results':[
            {'name':'best','value':1.},{'name':'other','value':2.}]}})
        step={'kind':'explicit_tolerance','candidate_limit':1,'parameters':['sub_c44'],
              'physical_fraction':[.05],'center_study':'physical-tolerance-centers',
              'move_start':0,'move_count':2}
        branch="kind in ('explicit_tolerance', 'model_ranges')"
        for centers_only in (True,False):
            with self.subTest(centers_only=centers_only):
                raw=copy.deepcopy(self.raw)
                self.queue_python(branch=branch,scope='stage',kind='explicit_tolerance',step={**step,'centers_only':centers_only},
                                  raw=raw,run=run,selected_finalists=lambda:selected)
                self.assertFalse(raw['analysis']['comparisons'])
                self.assertEqual(raw['provenance']['center_study'],'physical-tolerance-centers')
                self.assertEqual(raw['provenance']['centers'],['best'])
                names=[row['name'] for row in raw['candidates']]
                if centers_only:
                    self.assertEqual(names,['best'])
                    self.assertEqual(raw['provenance']['moves'],[])
                else:
                    self.assertEqual(len(names),2)
                    self.assertTrue(all(name.startswith('best:sub_c44:') for name in names))
                    self.assertNotIn('best',names)
                    self.assertEqual([row['values']['sub_c44'] for row in raw['candidates']],[66.5,73.5])

    def test_infeasible_move_batch_is_preserved_without_reference_transport(self):
        from contextlib import redirect_stdout
        run=self.root/'infeasible batch'; run.mkdir()
        provenance={'center_study':'centers','infeasible_moves':[
            {'name':'center:invalid','status':'physically_infeasible','value':None,'reason':'Born stability'}]}
        output=io.StringIO()
        with redirect_stdout(output),self.assertRaises(SystemExit) as stopped:
            self.queue_python(branch="kind in ('explicit_tolerance', 'model_ranges') and (not kept)",
                              scope='stage',kind='explicit_tolerance',kept=[],run=run,
                              identifier='moves',raw={'candidates':[],'provenance':provenance})
        self.assertEqual(stopped.exception.code,0)
        self.assertEqual(output.getvalue().strip(),'SKIP')
        self.assertEqual(records.load_document(run/'moves-skipped.json')['physical_moves'],provenance)
        self.assertFalse((run/'definitions/moves.yaml').exists())
        self.assertFalse(self.calls.exists())

    def test_restored_center_is_reused_and_rejects_changed_recovery(self):
        run=self.root/'center reuse'; output=run/'studies/centers'
        report=scan.run_study(self.raw,output)
        receipt=records.create_archive(output,None,run/'archives/centers.tar.gz',include_manifest=False)
        independent=self.root/'independent-center.tar.gz'; shutil.copy2(receipt['path'],independent)
        receipt.update(independent_copy=str(independent),independent_copy_verified=True,
                       summary_content_hash=records.content_hash(report))
        report.update(archive=receipt,raw_files_present=False)
        records.write_json(output/'summary.json',report,overwrite=True)
        namespace=self.queue_python('verify_saved_archive','restored_center',run=run)
        restored,_=namespace['restored_center']('centers')
        inventory=records.inventory(restored)
        reused,_=namespace['restored_center']('centers')
        self.assertEqual(restored,reused)
        self.assertEqual(records.inventory(reused),inventory)
        self.assertEqual(len(self.calls.read_text().splitlines()),4)
        next(restored.glob('tasks/*/*/hits.csv')).write_text('changed raw measurement\n')
        with self.assertRaisesRegex(FileExistsError,'existing recovery contains different files'):
            namespace['restored_center']('centers')

    def test_center_comparisons_require_exact_design_and_streams(self):
        run=self.root/'center matching'; output=run/'studies/moves'; output.mkdir(parents=True)
        records.write_json(run/'definitions/moves.yaml',{'provenance':{'center_study':'centers'}})
        center={'design':{'sites_mm':[[0,0,0]],'events_per_task':10},
                'streams':{'seed_base':10,'seed_bank':20},'results':[]}
        step={'kind':'explicit_tolerance','id':'moves','center_study':'centers'}
        for area,key,value in (('design','sites_mm',[[1,0,0]]),('design','events_per_task',11),
                               ('streams','seed_base',11),('streams','seed_bank',21)):
            with self.subTest(area=area,key=key):
                report=copy.deepcopy(center); report[area][key]=value
                with self.assertRaisesRegex(ValueError,'identical sites, primaries and random streams'):
                    self.queue_python(branch="step['kind'] in ('explicit_tolerance', 'model_ranges') and (not step.get('centers_only'))",
                                      run=run,output=output,report=report,step=step,
                                      restored_center=lambda identifier:(self.root,center))

    def test_transport_monitor_memory_stop_pauses_waits_and_retries(self):
        import shlex
        run=self.root/'memory queue'; source=run/'source'; source.mkdir(parents=True)
        records.write_json(run/'queue.json',[{'id':'trial'}])
        records.write_json(run/'resources.json',{'total_mem_gb':64,'system_mem_reserve_gb':0,'minimum_free_disk_gb':0})
        (run/'trial.yaml').write_text('id: trial\n')
        (source/'scan.py').write_text('''import pathlib,sys

def memory_headroom(): return {'available_bytes':2**60}

if __name__=='__main__':
    root=pathlib.Path(sys.argv[-1]).parents[1]
    counter=root/'attempts.txt'
    attempt=int(counter.read_text())+1 if counter.exists() else 1
    counter.write_text(str(attempt))
    if attempt==1:
        print('host or cgroup memory reserve reached; restart after memory is available',file=sys.stderr)
        sys.exit(1)
''')
        (source/'records.py').write_text('''import json

def write_json(path,value,overwrite=False): path.write_text(json.dumps(value))
''')
        shell=(ROOT/'run_all.sh').read_text().split('if [[ "$mode" == execute ]]; then\n',1)[1]
        shell=shell.split('if [[ "$mode" == publish ]]; then\n',1)[0]
        script=source/'run_all.sh'
        script.write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
mode=$1
material_run=$2
source_dir="$material_run/source"
material_python='''+shlex.quote(sys.executable)+'''
if [[ "$mode" != execute ]]; then
    case "$mode" in
        verify-source) exit 0 ;;
        stage) printf '%s\\n' "$material_run/trial.yaml"; exit 0 ;;
        pause|wait|record|finish|publish)
            printf '%s\\n' "$mode" >> "$material_run/actions.txt"
            [[ "$mode" != finish ]] || printf '{"state":"%s"}' "${MATERIAL_TEST_STATE:-complete}" > "$material_run/queue_status.json"
            exit 0 ;;
    esac
fi
if [[ "$mode" == execute ]]; then
'''+shell)
        result=subprocess.run(['bash',str(script),'execute',str(run)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((run/'attempts.txt').read_text(),'2')
        self.assertEqual((run/'actions.txt').read_text().splitlines(),['pause','wait','record','finish','publish'])
        for name in ('attempts.txt','actions.txt'): (run/name).unlink()
        reviewed=subprocess.run(['bash',str(script),'execute',str(run)],capture_output=True,text=True,timeout=30,
                                env={**os.environ,'MATERIAL_TEST_STATE':'awaiting_review'})
        self.assertEqual(reviewed.returncode,0,reviewed.stderr)
        self.assertEqual((run/'attempts.txt').read_text(),'2')
        self.assertEqual((run/'actions.txt').read_text().splitlines(),['pause','wait','record','finish'])

    def test_bash_syntax_and_check_respect_overrides_and_half_affinity(self):
        syntax=subprocess.run(['bash','-n',str(ROOT/'run_all.sh')],capture_output=True,text=True)
        self.assertEqual(syntax.returncode,0,syntax.stderr)
        allowed=sorted(os.sched_getaffinity(0))[:8]
        if len(allowed)<4: self.skipTest('four CPUs are needed for this affinity check')
        run=self.root/'check only'
        environment={**os.environ,'MATERIAL_REPOSITORY':str(ROOT),'MATERIAL_PYTHON':sys.executable}
        result=subprocess.run(['taskset','-c',','.join(map(str,allowed)),'bash',str(ROOT/'run_all.sh'),'check',str(run)],
                              cwd=self.root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        measured=json.loads(result.stdout)
        self.assertEqual(measured['cpu_cap'],len(allowed)//2)
        self.assertLessEqual(measured['workers'],max(1,len(allowed)//2-2))
        self.assertEqual(measured['search_dimensions'],17)
        self.assertEqual(measured['native_material_cases'],18)
        self.assertFalse(run.exists())

    def test_stop_rejects_a_reused_pid_with_a_different_start_time(self):
        run=self.root/'stop check'; run.mkdir()
        sleeper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        try:
            created=int(Path(f'/proc/{sleeper.pid}/stat').read_text().split(') ',1)[1].split()[19])
            records.write_json(run/'queue_status.json',{'state':'running','driver_pid':sleeper.pid,'driver_starttime':created+1})
            stopped=self.queue_command('stop',run)
            self.assertEqual(stopped.returncode,0,stopped.stderr)
            self.assertIsNone(sleeper.poll())
        finally:
            sleeper.terminate(); sleeper.wait(timeout=5)

    def test_archived_stage_is_skipped_only_with_recoverable_verified_copies(self):
        run=self.root/'archived queue'; run.mkdir()
        identifier='archived-stage'; output=run/'studies'/identifier
        raw=copy.deepcopy(self.raw); raw['id']=identifier
        report=scan.run_study(raw,output)
        archive=run/'archives'/f'{identifier}.tar.gz'
        receipt=records.create_archive(output,None,archive,include_manifest=False)
        independent=self.root/'independent.tar.gz'; shutil.copy2(archive,independent)
        receipt.update(independent_copy=str(independent),independent_copy_verified=True,
                       summary_content_hash=records.content_hash(report))
        report.update(raw_files_present=False,archive=receipt)
        records.write_json(output/'summary.json',report,overwrite=True)
        records.write_json(output/'archive.json',receipt)
        for path in output.iterdir():
            if path.name in ('summary.json','resolved.json','build.json','archive.json'): continue
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
        records.write_json(run/'queue.json',[{'id':identifier,'kind':'confirmation','anchors':['reference'],
                                             'sampling':{'kind':'sobol','sites':2,'replicas':2,'events_per_task':10}}])
        result=self.queue_command('stage',run,0)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'SKIP')
        archive.write_bytes(b'corrupt archive'); independent.write_bytes(b'corrupt independent copy')
        rejected=self.queue_command('stage',run,0)
        self.assertNotEqual(rejected.returncode,0)
        self.assertNotEqual(rejected.stdout.strip(),'SKIP')
        self.assertFalse((output/'results.sqlite').exists())

    def test_each_optimizer_run_keeps_its_best_even_when_best_vectors_tie(self):
        import ast
        text=(ROOT/'run_all.sh').read_text().rsplit("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        tree=ast.parse(text)
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='selected_finalists')
        run=self.root/'selection'; run.mkdir(); queue=[]
        for name,runner in (('bo_gp-101',2.),('cmaes-101',3.)):
            queue.append({'id':name,'kind':'search'})
            records.write_json(run/'studies'/name/'summary.json',{'complete':True,'search_counts':{'target_reached':True},
                               'results':[{'status':'complete','parameters':{'sub_c44':1.},'value':1.,'candidate_id':'best'},
                                          {'status':'complete','parameters':{'sub_c44':runner},'value':2.,'candidate_id':'runner'}]})
        namespace={'records':records,'run':run,'queue':queue}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'queue selection','exec'),namespace)
        chosen=namespace['selected_finalists']()
        self.assertEqual([point['name'] for point in chosen],['bo_gp-101','cmaes-101'])
        self.assertEqual([point['values'] for point in chosen],[{'sub_c44':1.},{'sub_c44':1.}])
        self.assertEqual([point['selected_candidate_id'] for point in chosen],['best','best'])

    def test_initial_finish_stops_for_review_and_reports_failed_precision_honestly(self):
        run=self.root/'initial queue'; source=run/'source'; source.mkdir(parents=True)
        for name in ('scan.py','physics.py','analysis.py','records.py','search.py','parameters.yaml'):
            shutil.copy2(ROOT/name,source/name)
        cfg=copy.deepcopy(records.load_document(ROOT/'studies/full_scale/queue.yaml'))
        cfg['execution']['stop_after_initial_checks']=True
        cfg['defaults']['max_sites']=1024
        records.write_json(source/'studies/full_scale/queue.yaml',cfg)
        records.write_json(run/'source-ready.json',{'test':'synthetic measurements; no physical conclusion'})
        records.write_json(run/'base-design.json',{'sites':512,'design_hash':'synthetic-prefix-check'})
        public=self.root/'initial public'; public.mkdir()
        before='Before\n<!-- full-scale-start -->pending<!-- full-scale-end -->\nAfter\n'
        (public/'README.md').write_text(before)
        subprocess.run(['git','init','-b','test-results',str(public)],capture_output=True,check=True)
        remote=str(self.root/'initial-test-remote.git')
        subprocess.run(['git','remote','add','origin',remote],cwd=public,check=True)
        records.write_json(run/'publication.json',{'branch':'test-results','remote':remote})
        baseline=scan.run_study(self.raw,self.root/'initial baseline')
        names=['source-512','source-1024',*[f'source-independent-{index:02d}' for index in range(1,5)],'events-500000']
        records.write_json(run/'queue.json',[{'id':name,'kind':'confirmation'} for name in names])
        independent=[]
        for name in names:
            output=run/'studies'/name; report=copy.deepcopy(baseline); report['id']=name
            if name.startswith('source-independent-'):
                index=int(name.rsplit('-',1)[1]); report['results'][0]['value']*=1+.1*index
                independent.append(copy.deepcopy(report))
            if name=='source-512': report['spatial_check']={'baseline':True,'passed':None}
            if name=='source-1024': report['spatial_check']={'passed':False,'coarse_sites':512,'fine_sites':1024}
            if name=='events-500000': report['event_count_check']={'passed':False}
            records.write_json(output/'summary.json',report)
            receipt=records.create_archive(output,None,run/'archives'/f'{name}.tar.gz',include_manifest=False)
            second=self.root/'initial independent'/f'{name}.tar.gz'; second.parent.mkdir(exist_ok=True)
            shutil.copy2(receipt['path'],second)
            receipt.update(independent_copy=str(second),independent_copy_verified=True,
                           summary_content_hash=records.content_hash(report))
            report.update(archive=receipt,raw_files_present=False)
            records.write_json(output/'summary.json',report,overwrite=True)
        namespace=self.queue_python('mean_interval','layout_summary')
        aggregate=namespace['layout_summary'](independent)
        records.write_json(run/'source-validation.json',{'passed':False,'independent_layouts':aggregate})
        environment={**os.environ,'MATERIAL_REPOSITORY':str(public),'MATERIAL_PYTHON':sys.executable}
        records.write_json(run/'publication.json',{'branch':'changed-branch','remote':remote},overwrite=True)
        rejected=subprocess.run(['bash',str(ROOT/'run_all.sh'),'finish',str(run)],env=environment,
                                capture_output=True,text=True,timeout=60)
        self.assertNotEqual(rejected.returncode,0)
        self.assertIn('publication branch or remote changed',rejected.stderr)
        self.assertEqual((public/'README.md').read_text(),before)
        self.assertFalse((public/'docs/results/initial_checks_summary.json').exists())
        records.write_json(run/'publication.json',{'branch':'test-results','remote':remote},overwrite=True)
        finished=subprocess.run(['bash',str(ROOT/'run_all.sh'),'finish',str(run)],env=environment,
                                capture_output=True,text=True,timeout=60)
        self.assertEqual(finished.returncode,0,finished.stderr)
        result=records.load_document(public/'docs/results/initial_checks_summary.json')
        self.assertEqual(result['state'],'awaiting_review')
        self.assertEqual(result['max_sites'],1024)
        self.assertEqual(result['base_design']['sites'],512)
        self.assertFalse(result['scientific_checks']['source_spatial'])
        self.assertFalse(result['scientific_checks']['event_count'])
        self.assertNotIn('confirmed_finalists',result)
        self.assertNotIn('method_comparison',result)
        self.assertIn('not evidence of spatial convergence',result['limitations'][0])
        self.assertEqual(records.load_document(run/'queue_status.json')['state'],'awaiting_review')
        self.assertGreater((public/'figures/initial_source_checks.png').stat().st_size,1000)
        self.assertIn('waiting for review',(public/'README.md').read_text())
        self.assertIn('Spatial criteria passed: False',(public/'docs/results/initial_checks_summary.md').read_text())

    def test_final_report_keeps_paired_errors_sensitivity_and_incomplete_physics(self):
        run=self.root/'final queue'; source=run/'source'; source.mkdir(parents=True)
        for name in ('scan.py','physics.py','analysis.py','records.py','search.py','parameters.yaml'):
            shutil.copy2(ROOT/name,source/name)
        for name in ('legacy','studies','figures'):
            shutil.copytree(ROOT/name,source/name,ignore=shutil.ignore_patterns('.env','*.env','__pycache__'))
        cfg=records.load_document(source/'studies/full_scale/queue.yaml')
        cfg['execution']['stop_after_initial_checks']=False
        records.write_json(source/'studies/full_scale/queue.yaml',cfg,overwrite=True)
        records.write_json(run/'source-ready.json',{'test':'synthetic measurements; no physical conclusion'})
        public=self.root/'public'; public.mkdir()
        (public/'README.md').write_text('Before\n<!-- full-scale-start -->pending<!-- full-scale-end -->\nAfter\n')
        subprocess.run(['git','init','-b','test-results',str(public)],capture_output=True,check=True)
        remote=str(self.root/'unused-test-remote.git')
        subprocess.run(['git','remote','add','origin',remote],cwd=public,check=True)
        records.write_json(run/'publication.json',{'branch':'test-results','remote':remote})
        baseline=scan.run_study(self.raw,self.root/'baseline')
        seeds=[f'{method}-{seed}' for seed in (101,202,303) for method in ('bo_gp','cmaes')]
        confirmations=[f'unused-stream-confirmation-{index:02d}' for index in range(1,5)]
        queue=[{'id':name,'kind':'search'} for name in seeds]
        queue += [{'id':name,'kind':'confirmation'} for name in
                  ['source-1024',*confirmations,'events-500000','material-transport-morris','density-morris']]
        records.write_json(run/'queue.json',queue)
        layout_reports=[]
        for step in queue:
            name=step['id']; output=run/'studies'/name; output.mkdir(parents=True)
            report=copy.deepcopy(baseline); report['id']=name
            if name in seeds:
                report['search_counts']={'target_reached':True,'measured':120,'target':120}
            elif name in confirmations:
                layout=confirmations.index(name)
                report['results']=[copy.deepcopy(baseline['results'][0]) for _ in range(7)]
                for index,(row,label) in enumerate(zip(report['results'],['reference',*seeds])):
                    factor=(1+.1*layout)*(1 if index==0 else .9+.01*layout+.005*(index-1))
                    row.update(name=label,candidate_id=f'{name}-{label}',value=row['value']*factor)
                    row['estimate']['means_by_stratum']={h:value*factor for h,value in row['estimate']['means_by_stratum'].items()}
                raw=copy.deepcopy(self.raw); raw['id']=name
                raw['candidates']=[{'name':row['name'],'values':row['parameters']} for row in report['results']]
                records.write_json(run/'definitions'/f'{name}.yaml',raw)
                layout_reports.append(copy.deepcopy(report))
            if name=='source-1024': report['spatial_check']={'passed':False}
            if name=='events-500000': report['event_count_check']={'passed':False}
            if name.endswith('morris'): report['sensitivity']={'status':'complete','method':'morris','names':['a'],'mu':[1.],'mu_star':[1.],'mu_star_conf':[0.],'sigma':[0.]}
            records.write_json(output/'summary.json',report)
            receipt=records.create_archive(output,None,run/'archives'/f'{name}.tar.gz',include_manifest=False)
            second=self.root/'independent'/f'{name}.tar.gz'; second.parent.mkdir(exist_ok=True); shutil.copy2(receipt['path'],second)
            receipt.update(independent_copy=str(second),independent_copy_verified=True,summary_content_hash=records.content_hash(report))
            report.update(archive=receipt,raw_files_present=False); records.write_json(output/'summary.json',report,overwrite=True)
        namespace=self.queue_python('mean_interval','layout_summary')
        confirmation=namespace['layout_summary'](layout_reports)
        records.write_json(run/'finalist-validation.json',{'passed':False,'independent_layouts':confirmation})
        records.write_json(run/'source-validation.json',{'passed':False})
        environment={**os.environ,'MATERIAL_REPOSITORY':str(public),'MATERIAL_PYTHON':sys.executable}
        # A destination change must reject publication before either tracked report changes.
        records.write_json(run/'publication.json',{'branch':'other-branch','remote':remote},overwrite=True)
        rejected=subprocess.run(['bash',str(ROOT/'run_all.sh'),'finish',str(run)],env=environment,capture_output=True,text=True,timeout=60)
        self.assertNotEqual(rejected.returncode,0)
        self.assertIn('publication branch or remote changed',rejected.stderr)
        self.assertEqual((public/'README.md').read_text(),'Before\n<!-- full-scale-start -->pending<!-- full-scale-end -->\nAfter\n')
        self.assertFalse((public/'docs/results/full_scale_summary.json').exists())
        records.write_json(run/'publication.json',{'branch':'test-results','remote':remote},overwrite=True)
        finished=subprocess.run(['bash',str(ROOT/'run_all.sh'),'finish',str(run)],env=environment,capture_output=True,text=True,timeout=60)
        self.assertEqual(finished.returncode,0,finished.stderr)
        result=records.load_document(public/'docs/results/full_scale_summary.json')
        self.assertEqual(len(result['confirmed_finalists']),6)
        layout_comparison=result['method_comparison']['position_layout_difference']
        self.assertTrue(layout_comparison['paired'])
        self.assertEqual(layout_comparison['n_new'],4)
        self.assertEqual(layout_comparison['degrees_of_freedom'],3)
        self.assertIn('shared measurement uncertainty',result['method_comparison']['interpretation'])
        first=result['confirmed_finalists'][0]
        self.assertEqual(first['paired_difference_from_reference'],confirmation['comparisons'][0])
        self.assertAlmostEqual(first['relative_to_reference'],-.085)
        self.assertEqual(len(first['confirmation_candidate_ids']),4)
        relative_se=first['paired_difference_from_reference']['relative_change']['standard_error']
        self.assertAlmostEqual(relative_se,float(np.std([-.10,-.09,-.08,-.07],ddof=1)/2))
        self.assertIn('sensitivity',result['studies']['density-morris'])
        self.assertFalse(result['scientific_checks']['finalist_spatial'])
        self.assertIn('Refine sites',result['next_measurements'][0])
        self.assertEqual(len(result['projections']),6)
        for projected in result['projections']:
            self.assertIn('top_film',projected); self.assertIn('bottom_film',projected)
            native=[row for row in projected['projection']['rows'] if row['candidate'].get('phonon_source')]
            self.assertTrue(native)
        text=(public/'README.md').read_text()
        self.assertTrue(text.startswith('Before\n'))
        self.assertTrue(text.endswith('\nAfter\n'))
        self.assertIn('Paired standard error (percentage points)',text)
        self.assertIn(f'| {100*relative_se:.3g} |',text)

class SpatialUncertaintyTests(unittest.TestCase):
    def inputs(self, ids, regions, values):
        design={'position_strata':regions,'stratum_weights':{'left':.25,'right':.75},
                'replica_ids':[0,1,2],'events_per_task':1,'gun_energy_eV':1.,
                'electrode_weights':[1.],'design_hash':'test','site_ids':ids}
        blocks=[{'position':i,'replica':r,'events':1,'total_qps':float(value),
                 'per_electrode_qps':[float(value)]}
                for i,row in enumerate(values) for r,value in enumerate(row)]
        return blocks,design

    def test_nested_identical_layout_cancels_shared_sites_and_streams(self):
        blocks,design=self.inputs(['a','b','c'],['left','left','right'],
                                 [[1,4,7],[15,21,30],[100,110,130]])
        result=analysis.nested_spatial_difference(blocks,blocks,design,design,bootstrap_draws=64)
        self.assertEqual(result['difference'],0.)
        self.assertEqual(result['standard_error'],0.)
        self.assertEqual(result['confidence_interval_95'],[0.,0.])
        self.assertTrue(result['equivalent_within_margin'])
        self.assertTrue(result['fixed_site_transport_difference']['paired'])

    def test_nested_site_ids_and_each_regional_denominator_are_preserved(self):
        coarse,cd=self.inputs(['a','b','c'],['left','left','right'],[[2]*3,[6]*3,[20]*3])
        # Shared sites need not occupy the same array indices.
        fine,fd=self.inputs(['c','x','b','a','y','z'],
                            ['right','left','left','left','right','right'],
                            [[20]*3,[10]*3,[6]*3,[2]*3,[40]*3,[60]*3])
        result=analysis.nested_spatial_difference(coarse,fine,cd,fd,bootstrap_draws=128)
        self.assertEqual(result['coarse_value'],16.)
        self.assertEqual(result['fine_value'],31.5)
        self.assertEqual(result['difference'],15.5)
        self.assertEqual(result['regions']['left']['n_coarse'],2)
        self.assertEqual(result['regions']['left']['n_fine'],3)
        self.assertEqual(result['regions']['right']['n_added'],2)
        self.assertEqual(result['regions']['left']['difference'],2.)
        self.assertEqual(result['regions']['right']['difference'],20.)
        self.assertEqual(result['fixed_site_transport_difference']['standard_error'],0.)

    def test_independent_transport_is_separate_from_site_pairing(self):
        blocks,design=self.inputs(['a','b'],['left','right'],[[1,2,3],[4,8,12]])
        result=analysis.nested_spatial_difference(blocks,blocks,design,design,
                                                 same_random_streams=False,bootstrap_draws=128)
        self.assertEqual(result['difference'],0.)
        self.assertGreater(result['standard_error'],0.)
        transport=result['fixed_site_transport_difference']
        self.assertFalse(transport['paired'])
        self.assertGreater(transport['standard_error'],0.)
        invalid=copy.deepcopy(design); invalid['site_ids'][0]='unshared'
        with self.assertRaisesRegex(ValueError,'every coarse site'):
            analysis.nested_spatial_difference(blocks,blocks,invalid,design,bootstrap_draws=8)

    def test_small_resolved_spatial_shift_passes_equivalence_margin(self):
        coarse,cd=self.inputs(['a','b'],['left','right'],[[100]*3,[100]*3])
        fine,fd=self.inputs(['a','b','x','y'],['left','right','left','right'],
                            [[100]*3,[100]*3,[102]*3,[102]*3])
        result=analysis.nested_spatial_difference(coarse,fine,cd,fd,bootstrap_draws=128)
        self.assertEqual(result['difference'],1.)
        self.assertEqual(result['standard_error'],0.)
        self.assertFalse(result['checks']['within_difference_error'])
        self.assertTrue(result['passed'])
        self.assertTrue(all(row['refinement_assessed'] for row in result['regions'].values()))
        unchanged=analysis.nested_spatial_difference(coarse,coarse,cd,cd,bootstrap_draws=128)
        self.assertTrue(all(not row['refinement_assessed'] for row in unchanged['regions'].values()))

    def test_fixed_site_transport_precision_excludes_position_variation(self):
        blocks,design=self.inputs(['a','b','c','d'],['left','left','right','right'],
                                 [[1]*3,[9]*3,[20]*3,[80]*3])
        estimate=analysis.summarize(analysis.prepare_counts(blocks,design),
                                    diagnostics=False,bootstrap_draws=128)
        conditional=analysis.transport_precision(estimate['by_replica'])
        self.assertEqual(conditional['mean'],38.75)
        self.assertEqual(conditional['standard_error'],0.)
        self.assertEqual(conditional['confidence_interval'],[38.75,38.75])
        self.assertGreater(estimate['standard_error'],0.)

    def test_fixed_site_transport_uses_student_t_across_replica_means(self):
        from scipy.stats import t
        result=analysis.transport_precision([8.,10.,12.])
        expected_se=2./np.sqrt(3.)
        self.assertEqual(result['mean'],10.)
        self.assertAlmostEqual(result['standard_error'],expected_se)
        self.assertAlmostEqual(result['relative_standard_error'],expected_se/10.)
        self.assertEqual(result['degrees_of_freedom'],2)
        np.testing.assert_allclose(result['confidence_interval'],
                                   [10.-t.ppf(.975,2)*expected_se,10.+t.ppf(.975,2)*expected_se])
        with self.assertRaises(ValueError):
            analysis.transport_precision([10.])


class MorrisPrecisionTests(unittest.TestCase):
    def test_density_morris_keeps_physical_bounds_and_elementary_steps(self):
        study = scan.load_study({'schema': 'material-scan-study-2', 'id': 'density-precision',
                                 'kind': 'sensitivity', 'morris': {
                                     'parameters': ['f_01', 'r', 's', 'I_ph', 'pt', 'n_cooper'],
                                     'trajectories': 4, 'levels': 4, 'seed': 41}})
        points = scan.candidate_table(study)
        problem = study['morris_design']['problem']
        names = problem['names']
        bounds = np.asarray(problem['bounds'])
        values = np.asarray([[point['inputs'][name] for name in names] for point in points])
        self.assertTrue(np.all(values >= bounds[:, 0]))
        self.assertTrue(np.all(values <= bounds[:, 1]))
        for point in points:
            self.assertEqual(point['ode'], point['inputs'])
        paths = values.reshape(4, len(names) + 1, len(names))
        steps = np.abs(np.diff(paths, axis=1))
        self.assertTrue(np.all(np.count_nonzero(steps, axis=2) == 1))
        for column in range(len(names)):
            changed = steps[:, :, column][steps[:, :, column] != 0]
            np.testing.assert_allclose(changed, (2 / 3) * np.diff(bounds[column])[0],
                                       rtol=1e-14, atol=0)
        r_values = values[:, names.index('r')]
        self.assertTrue(np.any(r_values != np.round(r_values, 6)))

    def test_morris_macro_and_lattice_keep_precision_until_rendering(self):
        study = scan.load_study({'schema': 'material-scan-study-2', 'id': 'transport-precision',
                                 'kind': 'sensitivity', 'morris': {
                                     'parameters': ['/main/detector_param/setTopFilmGap', 'scat'],
                                     'trajectories': 2, 'levels': 4, 'seed': 41}})
        for section, name, bounds in (
            ('detector_params', '/main/detector_param/setTopFilmGap',
             [0.00123456789123, 0.00212345678912]),
            ('config_params', 'scat', [1.215123456789, 3.645987654321]),
        ):
            entry = next(row for row in study['parameter_table']['morris'][section]
                         if row[0].strip() == name)
            entry[2] = bounds
        for candidate in scan.candidate_table(study):
            gap = candidate['inputs']['/main/detector_param/setTopFilmGap']
            self.assertNotEqual(gap, round(gap, 6))
            scat = candidate['inputs']['scat'] * 1e-42
            self.assertEqual(candidate['macro']['/main/detector_param/setTopFilmGap'], f'{gap:.12g}')
            self.assertEqual(candidate['config']['scat'], [scat, 's3'])
            self.assertEqual(candidate['values']['sub_scat'], scat)
            prepared = physics.prepare_values(study, candidate)
            prepared.update({key: candidate[key] for key in ('macro', 'config', 'ode')})
            lattice, macro = scan.input_text(prepared, study['installation'])
            self.assertIn(f'scat {scat:.12g} s3\n', lattice)
            self.assertIn(f'/main/detector_param/setTopFilmGap {gap:.12g}\n', macro)


if __name__ == "__main__":
    unittest.main()
