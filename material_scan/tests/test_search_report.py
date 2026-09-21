from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from material_scan.config import load_experiment_spec, load_parameter_catalog
from material_scan.space import ExperimentSpace
from material_scan.tools.search_report import build_report, markdown


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "material_scan/experiments/material-search.yaml"
CATALOG = ROOT / "material_scan/parameters.yaml"


class SearchReportTests(unittest.TestCase):
    def test_zero_completion_stall_is_reported_without_a_curve(self):
        catalog = load_parameter_catalog(CATALOG)
        spec = load_experiment_spec(EXPERIMENT, catalog)
        baseline = {
            name: value for name, value in ExperimentSpace(spec).baseline_point().items()
            if name in ExperimentSpace(spec).names
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial.json"
            initial.write_text(json.dumps({
                "points_key": "test-start",
                "evaluations": [{
                    "name": "start",
                    "values": baseline,
                    "result": {"value": 1.0, "standard_error": 0.1},
                }],
            }))
            stalled = root / "stalled.json"
            stalled.write_text(json.dumps({
                "schema": "material-scan-incomplete-search-1",
                "experiment_spec_key": spec.spec_key,
                "method": "bo_gp",
                "seed": 101,
                "requested_model_selected_points": 120,
                "completed_model_selected_points": 0,
                "proposal": baseline,
                "simulation": {
                    "declared_tasks": 512,
                    "simultaneous_tasks": 60,
                    "completed_tasks": 0,
                    "observed_elapsed_seconds_lower_bound": 3600,
                    "candidate_completion_hours_lower_bound": 8.5333333333,
                },
                "interpretation": "Stopped without assigning an objective value.",
            }))

            report = build_report(
                EXPERIMENT, CATALOG, initial, {},
                pending={"agentic": "agentic"},
                statuses={"bo-101": stalled},
            )

        self.assertEqual(report["curves"], {})
        self.assertNotIn("bo_gp", report["methods"])
        status = report["incomplete"]["bo-101"]
        self.assertEqual(status["status"], "stalled")
        self.assertEqual(status["completed_points"], 0)
        self.assertEqual(status["completed_tasks"], 0)
        body = markdown(report, Path("comparison.png"))
        self.assertIn("bo-101 | bo_gp (stalled) | 0/120", body)
        self.assertIn("runtime observations, not objective measurements", body)


if __name__ == "__main__":
    unittest.main()
