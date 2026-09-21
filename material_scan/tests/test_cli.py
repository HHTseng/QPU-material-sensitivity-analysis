from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from material_scan.cli import main
from material_scan.config import load_resolved_experiment


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "material_scan/experiments/spatial-strata-512-baseline.yaml"
CATALOG = ROOT / "material_scan/parameters.yaml"


class CliTests(unittest.TestCase):
    def test_checked_example_uses_corrected_recorded_design(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["check", str(EXPERIMENT), "--catalog", str(CATALOG)])
        summary = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(summary["design_hash"], "fa659c7d860acdee")
        self.assertEqual(summary["tasks"], 4096)
        self.assertEqual(summary["events_total"], 128_000_000)
        self.assertEqual(summary["build_mode"], "historical-unresolved")

    def test_freeze_round_trip_uses_no_scientific_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "resolved.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "freeze", str(EXPERIMENT), "--catalog", str(CATALOG),
                    "--output", str(destination),
                ])
            self.assertEqual(code, 0)
            resolved = load_resolved_experiment(destination)
            self.assertEqual(resolved.sampling.design.recorded_hash, "fa659c7d860acdee")
            self.assertEqual(len(resolved.sampling.tasks), 4096)

    def test_run_refuses_an_unresolved_historical_build(self):
        with tempfile.TemporaryDirectory() as directory:
            resolved_path = Path(directory) / "resolved.json"
            self.assertEqual(main([
                "freeze", str(EXPERIMENT), "--catalog", str(CATALOG),
                "--output", str(resolved_path),
            ]), 0)
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main([
                    "run", str(resolved_path),
                    "--output", str(Path(directory) / "run"),
                ])
        self.assertEqual(code, 2)
        self.assertIn("build.mode=verified", errors.getvalue())

    def test_agentic_search_requires_a_pinned_model_digest(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main([
                "search", "unused-experiment.yaml",
                "--initial-results", "unused-results.json",
                "--method", "agentic",
                "--seed", "101",
                "--output", "unused-output",
            ])
        self.assertEqual(code, 2)
        self.assertIn("requires --ollama-model-digest", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
