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

import inputs
import records
import simulate


ROOT = Path(__file__).resolve().parent


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
        self.database.abandon("a")
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
        self.assertEqual(self.database.observations()[0]["kind"], "measured")

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
        self.database.invalidate("a", "saved file checksum differs")
        self.assertEqual(len(self.database.observations()), 0)
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
        target = records.publish_directory(source, self.root / "complete")
        self.assertFalse(source.exists())
        self.assertEqual((target / "measured.csv").read_text(), "measured")
        source.mkdir(); (source / "measured.csv").write_text("different")
        with self.assertRaises(FileExistsError):
            records.publish_directory(source, target)
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
        self.database.rescore(task["task_id"], new)
        self.assertTrue(self.database.completed("candidate"))
        self.assertEqual(self.database.tasks("candidate")[0]["result"]["per_electrode_qps"], [2, 4])
        self.assertEqual(len(self.database.rows("attempts")), 2)
        previous = json.loads(self.database.rows("task_history")[0]["result_json"])
        self.assertEqual(previous["per_electrode_qps"], [1, 2])
        self.assertEqual(len(self.database.rows("observation_history")), 1)
        for bad in ({**new, "per_electrode_qps": [2]}, {**new, "sha256": "0" * 64},
                    {**new, "per_electrode_qps": [-1, 0]}, {**new, "per_electrode_qps": [float("nan"), 0]}):
            with self.assertRaises(ValueError):
                self.database.rescore(task["task_id"], bad)

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
        default = inputs.load_study({"schema": "material-scan-study-2", "sampling": {"sites": 2, "replicas": 2}})
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
                inputs.load_study(raw)

    def test_site_and_worker_counts_require_integers(self):
        for area, name, value in (("sampling", "sites", 2.5), ("sampling", "sites", True),
                                  ("execution", "workers", 1.5), ("execution", "workers", True)):
            raw = copy.deepcopy(self.raw)
            raw[area][name] = value
            with self.subTest(area=area, name=name, value=value), self.assertRaises(ValueError):
                inputs.load_study(raw)

    def test_recorded_sites_seeds_and_event_counts_are_repeatable(self):
        study = inputs.load_study(self.raw)
        prepared = inputs.prepare_values(study, {"name": "reference", "values": {}})
        tasks = inputs.task_plan(study, prepared, {"build": "fixed"})
        repeated = inputs.task_plan(inputs.load_study(self.raw), prepared, {"build": "fixed"})
        self.assertEqual(tasks, repeated)
        self.assertEqual(len(tasks), 4)
        self.assertEqual(sum(row["events"] for row in tasks), 40)
        self.assertEqual(len({row["task_id"] for row in tasks}), 4)
        self.assertEqual(len({tuple(row["seeds"]) for row in tasks}), 4)

    def test_macro_seed_position_units_and_path_with_spaces(self):
        study = inputs.load_study(self.raw)
        prepared = inputs.prepare_values(study, {"values": {}})
        task = inputs.task_plan(study, prepared, {"build": "fixed"})[0]
        environment, identity = simulate.prepare_environment(study["installation"])
        result = simulate.run_task(task, prepared, study["installation"], environment, self.root / "attempt with spaces")
        self.assertEqual(result["status"], "complete")
        macro = Path(result["paths"]["macro"]).read_text()
        self.assertIn(f"/random/setSeeds {task['seeds'][0]} {task['seeds'][1]}\n/run/beamOn 10", macro)
        self.assertIn("/main/gun/setEnergy 0.01 eV", macro)
        self.assertIn("/main/gun/setPosition " + " ".join(map(str, task["site_mm"])) + " mm", macro)
        self.assertEqual(result["sha256"], records.sha256_file(result["output"]))
        self.assertEqual(identity["executable_sha256"], records.sha256_file(self.executable))

    def test_macro_string_paths_are_not_parsed_as_numbers(self):
        simulate.validate_macro("/g4cmp/HitsFile /tmp/measurement 123abc/hits.csv\n"
                                "/random/setSeeds 1 2\n/run/beamOn 10\n/control/shell touch /tmp/complete\n")
        with self.assertRaises(ValueError):
            simulate.validate_macro("/random/setSeeds 1 2\n/run/beamOn 10.5bad\n/control/shell touch /tmp/complete\n")

    def test_missing_witness_malformed_output_and_exit_fault_are_distinguished(self):
        study = inputs.load_study(self.raw)
        prepared = inputs.prepare_values(study, {"values": {}})
        task = inputs.task_plan(study, prepared, {"build": "fixed"})[0]
        for mode, status in (("incomplete", "invalid"), ("malformed", "invalid"), ("fail", "invalid"), ("teardown", "complete")):
            self.make_executable(mode)
            environment, _ = simulate.prepare_environment(study["installation"])
            result = simulate.run_task(task, prepared, study["installation"], environment, self.root / mode)
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
            self.assertEqual(len(database.observations()), 1)
        scan.run_study(self.raw, output)
        self.assertEqual(self.calls.read_text().splitlines(), first_calls)

    def test_zero_and_timeout_have_different_scientific_results(self):
        import scan
        self.make_executable("zero")
        output = self.root / "zero"
        scan.run_study(self.raw, output)
        with records.Results(output / "results.sqlite", create=False) as database:
            self.assertTrue(database.observations(), database.tasks())
            self.assertTrue(all(row["result"]["total_qps"] == 0 for row in database.tasks()))
        self.make_executable("timeout")
        raw = copy.deepcopy(self.raw)
        raw["execution"].update(timeout_s=.05)
        output = self.root / "timeout"
        scan.run_study(raw, output)
        with records.Results(output / "results.sqlite", create=False) as database:
            self.assertTrue(any(row["status"] == "timeout" for row in database.tasks()))
            self.assertFalse(any(row["kind"] in ("measured", "physical_zero") for row in database.observations()))
            self.assertTrue(all(not row["result"].get("measured", False) for row in database.tasks()))

    def test_recorded_result_figures(self):
        import plots
        import scan
        raw = copy.deepcopy(self.raw)
        raw["analysis"]["time_resolved"] = True
        output = self.root / "figures"
        scan.run_study(raw, output)
        for kind in ("summary", "hits", "time"):
            figure = plots.plot_result(output, output / (kind + ".png"), kind)
            self.assertGreater(figure.stat().st_size, 1000)
        geometry = {"geometry": inputs.load_study(raw)["fixed"], "sites_mm": [[0, 0, .259875]]}
        self.assertTrue(plots.plot_result(geometry, output / "layout.svg", "layout").is_file())

    def test_figure_labels_follow_the_recorded_response_units(self):
        import matplotlib
        import plots
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
            self.assertEqual(len(database.observations()), 1)

    def test_recovered_results_can_be_analysed_without_the_original_tree(self):
        import scan
        output = self.root / "original"
        expected = scan.run_study(self.raw, output)
        receipt = records.create_archive(output, None, self.root / "copy.tar.gz")
        restored = records.recover_archive(receipt["path"], self.root / "restored")
        shutil.rmtree(output)
        before = records.sha256_file(restored / "results.sqlite")
        result = scan.read_analysis(restored)
        self.assertEqual(result["results"][0]["value"], expected["results"][0]["value"])
        self.assertEqual(result["results"][0]["standard_error"], expected["results"][0]["standard_error"])
        self.assertTrue(all(Path(block["output"]).is_file() for block in result["results"][0]["blocks"]))
        self.assertEqual(records.sha256_file(restored / "results.sqlite"), before)
        self.assertEqual(len(self.calls.read_text().splitlines()), 4)

    def test_recovered_time_figure_uses_restored_files(self):
        import plots
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
        study = inputs.load_study(path)
        self.assertEqual(Path(study["installation"]["executable"]).resolve(), self.executable)
        self.assertEqual(scan.main(["check", str(path)]), 0)


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
        loaded = inputs.load_study(child)
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
            captured.append(inputs.load_study(child))
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
            old = inputs.task_plan(coarse, {'physical': 'same'}, {'build': 'same'})
            new = {(row['position'], row['replica']): row for row in inputs.task_plan(fine, {'physical': 'same'}, {'build': 'same'})}
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
        with patch('scan.run_study', side_effect=run), patch('inputs.build_design', wraps=inputs.build_design) as build:
            summary = scan.run_series(raw, self.root / 'area')
        self.assertEqual(sum(call.args[1]['kind'] == 'stratified' for call in build.call_args_list), 1)
        self.assertFalse(summary['convergence']['converged'])
        comparison = summary['convergence']['comparisons'][0]
        self.assertTrue(comparison['ranking_checked'])
        self.assertFalse(comparison['candidates']['reference']['checks']['every_region'])
        a = inputs.load_study(calls[0]); b = inputs.load_study(calls[1])
        self.assertEqual(set(a['design']['stratum']), set(inputs.REGIONS))
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
        table = inputs.load_document(ROOT / 'parameters.yaml')['parameters']
        raw.update(kind='search', parameters={name: {'mode': 'fixed', 'value': row['default']}
                                             for name, row in table.items()})
        raw['parameters']['topfilm_vsound'] = {'mode': 'search', 'bounds': [1.5, 6.]}
        raw['search'] = {'method': method, 'seed': 47, 'evaluations': evaluations,
                         'parallel_candidates': 2, 'options': {'popsize': 4}}
        return raw

    def test_fixed_temperature_controls_values_macro_and_search_coordinates(self):
        raw = copy.deepcopy(self.raw)
        raw.update(kind='search', fixed={'temperature_K': .02})
        study = inputs.load_study(raw)
        self.assertEqual(study['space']['baseline']['temperature'], .02)
        self.assertNotIn('temperature', study['space']['names'])
        prepared = inputs.prepare_values(study, {'values': {}})
        self.assertEqual(prepared['values']['temperature'], .02)
        task = inputs.task_plan(study, prepared, {'build': 'fixed'})[0]
        paths = simulate.render_inputs(prepared, task, study['installation'], self.root / 'temperature-inputs')
        self.assertIn('/g4cmp/temperature 0.02 K', Path(paths['macro']).read_text())
        raw['parameters'] = {'temperature': {'mode': 'fixed', 'value': .03}}
        with self.assertRaisesRegex(ValueError, 'temperature'):
            inputs.load_study(raw)

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
            captured.append(inputs.load_study(child))
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
            progress = database.state('search_progress')
        saved = search.load_state(progress['optimizer'])
        self.assertEqual(len(saved['pending']), 2)
        self.assertEqual(progress['rows'], [])
        report = scan.run_study(raw, output)
        self.assertTrue(report['complete'])
        self.assertEqual([row['parameters'] for row in report['results'][:2]], saved['pending'])
        self.assertEqual(len(self.calls.read_text().splitlines()), 12)

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
            progress = database.state('search_progress')
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
        original = simulate.run_task; calls = []; lock = threading.Lock()
        def failed_once(*args, **kwargs):
            with lock:
                first = not calls; calls.append(True)
            if first:
                return {'status': 'failed', 'reason': 'unmeasured simulated failure'}
            return original(*args, **kwargs)
        with patch('simulate.run_task', side_effect=failed_once):
            report = scan.run_study(raw, output)
        with records.Results(output / 'results.sqlite', create=False) as database:
            progress = database.state('search_progress')
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


if __name__ == "__main__":
    unittest.main()
