from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import yaml

from material_scan.config import (
    ExperimentSpec,
    load_experiment_spec,
    load_parameter_catalog,
    resolve_experiment,
)
from material_scan.design import extend_recorded_design
from material_scan.simulation import (
    _link_or_copy,
    _reusable_tasks,
    render_lattice,
    render_macro,
)
from material_scan.space import (
    ExperimentSpace,
    exact_physics_check,
    sphere_to_miller,
    values_from_resolved,
)
from material_scan.search import SearchController
from material_scan.store import Store


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "material_scan/parameters.yaml"
BASELINE = ROOT / "material_scan/experiments/spatial-strata-512-baseline.yaml"


class ExperimentSpaceTests(unittest.TestCase):
    def test_sphere_conversion_uses_the_validated_integer_resolution(self):
        self.assertEqual(sphere_to_miller(1.0, 0.37), [0, 0, 1])
        self.assertEqual(sphere_to_miller(0.0, 0.0), [1, 0, 0])
        direction = np.asarray(
            sphere_to_miller(3.0 / math.sqrt(17.0), 0.125), dtype=float
        )
        direction /= np.linalg.norm(direction)
        expected = np.asarray([2.0, 2.0, 3.0]) / math.sqrt(17.0)
        angular_error = math.degrees(
            math.acos(float(np.clip(direction @ expected, -1.0, 1.0)))
        )
        self.assertLess(angular_error, 2.0e-4)

    def test_space_comes_only_from_experiment_bounds_and_keeps_fixed_values(self):
        catalog = load_parameter_catalog(CATALOG)
        source = load_experiment_spec(BASELINE, catalog).to_manifest()
        source["parameters"]["topfilm_gap"] = {
            "mode": "search",
            "bounds": [1.5384e-3, 3.5e-3],
            "scale": "log",
            "default": 1.5384e-3,
            "prior": {"kind": "log-uniform"},
        }
        spec = ExperimentSpec.from_mapping(source, catalog)
        space = ExperimentSpace(spec)
        self.assertEqual(space.names, ["topfilm_gap"])
        point = space.from_unit([1.0])
        self.assertAlmostEqual(point["topfilm_gap"], 3.5e-3, places=15)
        self.assertEqual(point["sub_c11"], 165.6)
        self.assertTrue(space.in_bounds(point)[0])
        self.assertFalse(space.in_bounds({**point, "topfilm_gap": 1.0e-3})[0])

    def test_log_scaled_box_faces_return_declared_endpoints_exactly(self):
        catalog = load_parameter_catalog(CATALOG)
        source = load_experiment_spec(BASELINE, catalog).to_manifest()
        source["parameters"]["sub_decay"] = {
            "mode": "search",
            "bounds": [5.0e-57, 5.0e-54],
            "scale": "log",
            "default": 5.0e-56,
            "prior": {"kind": "log-uniform"},
        }
        spec = ExperimentSpec.from_mapping(source, catalog)
        variable = ExperimentSpace(spec).variables[0]
        self.assertEqual(variable.from_unit(0.0), variable.low)
        self.assertEqual(variable.from_unit(1.0), variable.high)

    def test_optimizer_proposals_use_the_same_bounds_and_pass_full_physics(self):
        catalog = load_parameter_catalog(CATALOG)
        experiment_path = ROOT / "material_scan/experiments/spatial-refinement.yaml"
        spec = load_experiment_spec(experiment_path, catalog)
        space = ExperimentSpace(spec)
        controller = SearchController("random", seed=73, space=space)
        accepted = 0
        for point in controller.ask(24):
            self.assertTrue(space.in_bounds(point)[0])
            try:
                resolved = resolve_experiment(
                    catalog, spec, {name: point[name] for name in space.names}
                )
                exact_physics_check(values_from_resolved(resolved), resolved)
            except ValueError as error:
                controller.reject(point, str(error))
            else:
                accepted += 1
        self.assertGreater(accepted, 0)
        checkpoint = controller.checkpoint()
        self.assertEqual(
            json.dumps(checkpoint["space"], sort_keys=True),
            json.dumps(space.to_manifest(), sort_keys=True),
        )
        self.assertEqual(len(checkpoint["implementation_sha256"]), 64)


class NestedDesignTests(unittest.TestCase):
    def test_only_failed_strata_can_be_extended_without_moving_old_points(self):
        source = json.loads((ROOT / "data/results/stage4_strat_512.json").read_text())[
            "stratified_design"
        ]
        counts = dict(source["stratum_counts"])
        for name in ("S1_le_0.05", "S3_le_0.50", "S4_bulk"):
            counts[name] *= 2
        extended = extend_recorded_design(
            source,
            counts,
            electrode_x_mm=[-2, 0, 2, -2, 0, 2, -2, 0, 2, 1, -1, 1, -1, -3, 1, -1, 3],
            electrode_y_mm=[-2, -2, -2, 0, 0, 0, 2, 2, 2, -3, -1, 1, 3, -1, -1, 1, 1],
        )
        self.assertEqual(extended["stratum_counts"], counts)
        self.assertEqual(len(extended["sites_mm"]), 871)
        self.assertEqual(extended["sites_mm"][:512], source["sites_mm"])
        self.assertEqual(extended["stratum"][:512], source["stratum"])
        self.assertEqual(extended["nearest_electrode"][:512], source["nearest_electrode"])


class CompletedTaskReuseTests(unittest.TestCase):
    def test_only_complete_valid_task_identity_is_indexed(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            store = Store.create(directory / "results.sqlite")
            lease = store.acquire_controller("test", 60.0)
            store.register_experiment(lease, "experiment", {"purpose": "test"})
            store.plan_simulation(
                lease, "simulation", "experiment", "simulation-key", {}, 1
            )
            store.plan_task(
                lease, "task-identity", "simulation", 0, "task-identity", {}
            )
            attempt = store.start_attempt(lease, "task-identity")
            store.finish_attempt(
                lease, attempt, completeness="complete", validity="valid",
                artifact_path="hits.txt", artifact_sha256="a" * 64,
            )
            store.release_controller(lease)
            store.close()
            self.assertEqual(
                _reusable_tasks([directory]),
                {"task-identity": (directory.resolve(), "a" * 64)},
            )

    def test_reused_file_is_published_without_partial_content(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = directory / "source.txt"
            destination = directory / "nested/destination.txt"
            source.write_bytes(b"complete artifact\n")
            _link_or_copy(source, destination)
            self.assertEqual(destination.read_bytes(), source.read_bytes())


class RenderingParityTests(unittest.TestCase):
    def test_baseline_lattice_and_active_macro_values_match_retained_run(self):
        catalog = load_parameter_catalog(CATALOG)
        spec = load_experiment_spec(BASELINE, catalog)
        experiment = resolve_experiment(catalog, spec, {})
        values = values_from_resolved(experiment)
        derived = exact_physics_check(values, experiment)
        old_root = ROOT / (
            "data/runs/stage4_property_v1_strat512/"
            "stage4_property_v1_strat512_e39029f467c3"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lattice = render_lattice(
                values, derived, root / "CrystalMaps/PseudoCubic/config.txt"
            )
            self.assertEqual(
                lattice,
                (old_root / "CrystalMaps/PseudoCubic/config.txt").read_bytes(),
            )
            new_macro = render_macro(
                experiment, values, derived, experiment.sampling.tasks[0],
                hit_path=root / "hits.partial", witness_path=root / "done",
            )

        old_macro = (old_root / "macros/stage4_property_v1_strat512_e39029f467c3_r0_p0.mac").read_text()

        def commands(text: str) -> dict[str, list[str]]:
            ignored = {"/g4cmp/HitsFile", "/control/shell"}
            result = {}
            for raw in text.splitlines():
                line = raw.strip()
                if not line.startswith("/"):
                    continue
                name, *arguments = line.split()
                if name in ignored:
                    continue
                result.setdefault(name, []).append(arguments)
            return result

        old = commands(old_macro)
        new = commands(new_macro)
        self.assertEqual(set(old), set(new))
        for name in old:
            self.assertEqual(len(old[name]), len(new[name]), name)
            for expected, observed in zip(old[name], new[name]):
                self.assertEqual(len(expected), len(observed), name)
                for left, right in zip(expected, observed):
                    try:
                        self.assertAlmostEqual(float(left.rstrip(",")), float(right.rstrip(",")), places=14)
                    except ValueError:
                        self.assertEqual(left, right, name)

    def test_unrepresentable_material_density_is_refused(self):
        catalog = load_parameter_catalog(CATALOG)
        spec = load_experiment_spec(
            ROOT / "material_scan/experiments/spatial-refinement.yaml", catalog
        )
        values = yaml.safe_load(
            (ROOT / "material_scan/experiments/spatial-refinement-baseline.yaml").read_text()
        )
        values["physics"]["realization"]["material_density_kg_m3"] = 5000.0
        values["physics"]["realization"]["density_tolerance"] = 0.03
        resolved = resolve_experiment(catalog, spec, values)
        with self.assertRaisesRegex(ValueError, "above the declared"):
            exact_physics_check(values_from_resolved(resolved), resolved)


if __name__ == "__main__":
    unittest.main()
