"""Small, filesystem-isolated tests for the first restructuring slice."""

from dataclasses import FrozenInstanceError
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from material_scan.config import (
    ConfigError,
    ExperimentSpec,
    ParameterCatalog,
    load_resolved_experiment,
    resolve_experiment,
    write_resolved_experiment,
)
from material_scan.identity import (
    IdentityError,
    analysis_key,
    canonical_hash,
    canonical_json,
    search_key,
    simulation_key,
    task_key,
)
from material_scan.sampling import (
    RecordedStratifiedDesign,
    SamplingError,
    build_recorded_plan,
    legacy_design_hash,
    legacy_position_seed,
    load_recorded_stratified_design,
)


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def recorded_design(prefix="S"):
    record = {
        "sites_mm": [[-1.0, 0.0, 0.25], [0.0, 1.0, 0.25], [1.0, 0.0, 0.25]],
        "stratum": [f"{prefix}0", f"{prefix}1", f"{prefix}1"],
        "nearest_electrode": [0, 0, 1],
        "stratum_weights": {f"{prefix}0": 0.25, f"{prefix}1": 0.75},
        "stratum_counts": {f"{prefix}0": 1, f"{prefix}1": 2},
        "band_edges_mm": [0.2],
        "junction_half_extent_mm": [0.005, 0.005],
        "injection_law": "uniform_surface",
        "position_seed": 20260727,
        "gun_energy_eV": 0.01,
        "normalization_basis": "per_injected_eV",
        "electrode_weights": [0.5, 0.5],
    }
    record["design_hash"] = legacy_design_hash(record)
    return record


def parameter_catalog():
    return {
        "schema": "material-scan-parameters-1",
        "parameters": {
            "gun_energy": {
                "unit": "eV",
                "type": "float",
                "target": "macro:/main/gun/setEnergy",
                "valid_range": [1.0e-6, 1.0],
                "source": "beam protocol",
            },
            "miller": {
                "unit": "1",
                "type": "choice",
                "target": "macro:/main/detector_param/setMiller",
                "choices": [[0, 0, 1], [1, 1, 1]],
                "source": "cubic reduced directions",
            },
            "sub_c44": {
                "unit": "GPa",
                "type": "float",
                "target": "lattice:stiffness 4 4",
                "valid_range": [0.01, 1200.0],
                "source": "elastic stability domain",
            },
            "topfilm_gap": {
                "unit": "eV",
                "type": "float",
                "target": "macro:/main/detector_param/setTopFilmGap",
                "valid_range": [1.0e-6, 0.01],
                "source": "superconducting-gap domain",
            },
        },
        "meta": {"revision": "fixture-1"},
    }


def experiment(design=None):
    design = recorded_design() if design is None else design
    return {
        "schema": "material-scan-experiment-1",
        "id": "20990101-fixture",
        "purpose": "Exercise immutable configuration without simulation.",
        "parameters": {
            "gun_energy": {"mode": "fixed", "value": 0.01},
            "miller": {"mode": "fixed", "value": [0, 0, 1]},
            "sub_c44": {
                "mode": "search",
                "bounds": [5.0, 200.0],
                "scale": "linear",
                "default": 79.5,
                "prior": {"kind": "uniform"},
            },
            "topfilm_gap": {
                "mode": "search",
                "bounds": [5.0e-5, 3.5e-3],
                "scale": "log",
                "default": 1.5384e-3,
                "prior": {"kind": "log-uniform"},
            },
        },
        "constraints": [
            {
                "name": "positive-shear-margin",
                "kind": "linear",
                "terms": {"sub_c44": 1.0},
                "operator": ">=",
                "rhs": 5.0,
            }
        ],
        "sampling": {
            "kind": "recorded-stratified",
            "design": design,
            "replicas": 2,
            "seed": {"algorithm": "legacy-position-v1", "base": 20260728, "bank_id": 9},
        },
        "fidelity": {"name": "fixture", "events_per_task": 10, "events_total": 60},
        "physics": {"realization_schema": "fixture-realization-1"},
        "analysis": {
            "objective": "device_weighted_junction_qps_per_energy",
            "bootstrap": {"draws": 1000, "seed": 7},
            "environment": {
                "python": "3.12.10",
                "numpy": "2.2.0",
                "pandas": "2.2.0",
                "blas": "openblas fixture",
            },
        },
        "search": {
            "method": "bo-gp",
            "kernel": "matern-5/2",
            "seed": 1,
            "implementation_sha256": HEX_C,
            "environment": {
                "python": "3.12.10",
                "numpy": "2.2.0",
                "scipy": "1.15.0",
                "blas": "openblas fixture",
            },
        },
        "build": {
            "mode": "verified",
            "executable_sha256": HEX_A,
            "runtime_input_sha256": {
                "libMain.so": HEX_B,
                "libG4cmp.so": HEX_C,
            },
            "geant4_revision": "10.7.4",
            "g4cmp_revision": "fixture-g4cmp",
            "detector_revision": "fixture-detector",
        },
        "provenance": {"fixture": True},
    }


def resolved_fixture(c44_origin="material"):
    catalog = ParameterCatalog.from_mapping(parameter_catalog())
    spec = ExperimentSpec.from_mapping(experiment(), catalog)
    c44 = (
        {"value": 241.0, "origin": "material", "source": "measured SiC"}
        if c44_origin == "material"
        else 179.0
    )
    return resolve_experiment(
        catalog,
        spec,
        {"sub_c44": c44, "topfilm_gap": 1.0e-3},
    )


class RecordedDesignTests(unittest.TestCase):
    def test_loads_exact_record_without_recomputing_weights(self):
        source = recorded_design()
        design = RecordedStratifiedDesign.from_mapping(source)
        self.assertEqual(design.to_manifest(), source)
        self.assertEqual(dict(design.stratum_weights), source["stratum_weights"])
        self.assertEqual(design.recorded_hash, source["design_hash"])
        self.assertEqual(len(design.recorded_hash), 16)
        self.assertEqual(len(design.design_key), 64)

    def test_reads_design_nested_in_a_saved_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps({"stratified_design": recorded_design()}), encoding="utf-8")
            loaded = load_recorded_stratified_design(path)
        self.assertEqual(loaded.n_sites, 3)
        self.assertEqual(loaded.recorded_hash, recorded_design()["design_hash"])

    def test_refuses_changed_record_under_stale_hash(self):
        source = recorded_design()
        source["stratum_weights"]["S0"] = 0.30
        source["stratum_weights"]["S1"] = 0.70
        with self.assertRaisesRegex(SamplingError, "hash mismatch"):
            RecordedStratifiedDesign.from_mapping(source)

    def test_old_and_new_stratum_names_coexist_with_distinct_keys(self):
        old = RecordedStratifiedDesign.from_mapping(recorded_design("H"))
        new = RecordedStratifiedDesign.from_mapping(recorded_design("S"))
        self.assertNotEqual(old.recorded_hash, new.recorded_hash)
        self.assertNotEqual(old.design_key, new.design_key)
        self.assertEqual(old.n_sites, new.n_sites)

    def test_plan_expands_every_seed_and_task_before_launch(self):
        design = RecordedStratifiedDesign.from_mapping(recorded_design())
        plan = build_recorded_plan(
            design,
            replicas=2,
            events_per_task=10,
            seed_base=20260728,
            seed_bank_id=9,
        )
        self.assertEqual(len(plan.tasks), 6)
        self.assertEqual(plan.tasks[0].seeds[0], legacy_position_seed(20260728, 9, 0, 0))
        self.assertEqual(plan.tasks[-1].seeds[0], legacy_position_seed(20260728, 9, 1, 2))
        self.assertEqual(sum(task.events for task in plan.tasks), 60)
        self.assertAlmostEqual(
            sum(task.node_weight for task in plan.tasks[:3]), 1.0
        )


class ConfigTests(unittest.TestCase):
    def test_nested_list_choice_is_normalized_before_membership_check(self):
        catalog = ParameterCatalog.from_mapping(parameter_catalog())
        miller = catalog.get("miller")
        self.assertEqual(miller.normalize_value([0, 0, 1]), (0, 0, 1))
        with self.assertRaisesRegex(ConfigError, "not one of"):
            miller.normalize_value([1, 0, 0])

    def test_three_layers_and_material_exemption_are_explicit(self):
        resolved = resolved_fixture("material")
        layer = next(
            layer for layer in resolved.parameter_layers if layer.definition.name == "sub_c44"
        )
        self.assertEqual(layer.definition.valid_max, 1200.0)
        self.assertEqual(layer.experiment.upper, 200.0)
        self.assertEqual(layer.value, 241.0)
        self.assertEqual(layer.origin, "material")
        self.assertEqual(layer.source, "measured SiC")
        self.assertEqual(resolved.value("miller"), (0, 0, 1))

    def test_proposal_cannot_escape_experiment_box(self):
        catalog = ParameterCatalog.from_mapping(parameter_catalog())
        spec = ExperimentSpec.from_mapping(experiment(), catalog)
        with self.assertRaisesRegex(ConfigError, "outside this experiment's search bounds"):
            resolve_experiment(
                catalog, spec, {"sub_c44": 241.0, "topfilm_gap": 1.0e-3}
            )

    def test_joint_constraint_applies_to_material_values_too(self):
        catalog = ParameterCatalog.from_mapping(parameter_catalog())
        spec = ExperimentSpec.from_mapping(experiment(), catalog)
        with self.assertRaisesRegex(ConfigError, "positive-shear-margin"):
            resolve_experiment(
                catalog,
                spec,
                {
                    "sub_c44": {
                        "value": 1.0,
                        "origin": "material",
                        "source": "deliberately bad fixture",
                    },
                    "topfilm_gap": 1.0e-3,
                },
            )

    def test_fixed_scientific_value_cannot_be_overridden(self):
        catalog = ParameterCatalog.from_mapping(parameter_catalog())
        spec = ExperimentSpec.from_mapping(experiment(), catalog)
        with self.assertRaisesRegex(ConfigError, "cannot be overridden"):
            resolve_experiment(
                catalog,
                spec,
                {"gun_energy": 0.02, "sub_c44": 179.0, "topfilm_gap": 1.0e-3},
            )

    def test_resolved_object_is_deeply_immutable(self):
        resolved = resolved_fixture()
        with self.assertRaises(FrozenInstanceError):
            resolved.purpose = "changed"
        with self.assertRaises(TypeError):
            resolved.analysis["objective"] = "changed"
        with self.assertRaises(TypeError):
            resolved.build["runtime_input_sha256"]["g4cmp"] = HEX_C

    def test_manifest_round_trip_and_atomic_no_overwrite(self):
        resolved = resolved_fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resolved.json"
            write_resolved_experiment(path, resolved)
            loaded = load_resolved_experiment(path)
            self.assertEqual(loaded.manifest_key, resolved.manifest_key)
            # Identical publication is idempotent and never rewrites the inode.
            inode = path.stat().st_ino
            write_resolved_experiment(path, resolved)
            self.assertEqual(path.stat().st_ino, inode)

            other_catalog = ParameterCatalog.from_mapping(parameter_catalog())
            other_spec_source = experiment()
            other_spec_source["id"] = "20990101-other"
            other_spec = ExperimentSpec.from_mapping(other_spec_source, other_catalog)
            other = resolve_experiment(
                other_catalog,
                other_spec,
                {"sub_c44": 179.0, "topfilm_gap": 1.0e-3},
            )
            with self.assertRaisesRegex(ConfigError, "different immutable manifest"):
                write_resolved_experiment(path, other)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(load_resolved_experiment(path).manifest_key, resolved.manifest_key)

    def test_tampered_manifest_is_refused(self):
        resolved = resolved_fixture()
        document = resolved.to_manifest()
        document["purpose"] = "tampered"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "manifest hash mismatch"):
                load_resolved_experiment(path)

    def test_self_consistent_hash_cannot_hide_a_changed_task_plan(self):
        document = resolved_fixture().to_manifest()
        document["sampling"]["tasks"][0]["seeds"][0] += 1
        without_key = {key: value for key, value in document.items() if key != "manifest_key"}
        document["manifest_key"] = canonical_hash("resolved-experiment/v1", without_key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed-task.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "saved tasks differ"):
                load_resolved_experiment(path)

    def test_self_consistent_hash_cannot_hide_a_changed_event_total(self):
        document = resolved_fixture().to_manifest()
        document["fidelity"]["events_total"] += 1
        without_key = {key: value for key, value in document.items() if key != "manifest_key"}
        document["manifest_key"] = canonical_hash("resolved-experiment/v1", without_key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed-fidelity.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "events_total disagrees"):
                load_resolved_experiment(path)

    def test_self_consistent_hash_cannot_hide_missing_material_source(self):
        document = resolved_fixture("material").to_manifest()
        document["parameter_layers"]["sub_c44"]["resolved"]["source"] = ""
        without_key = {key: value for key, value in document.items() if key != "manifest_key"}
        document["manifest_key"] = canonical_hash("resolved-experiment/v1", without_key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-material-source.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "needs source provenance"):
                load_resolved_experiment(path)

    def test_unknown_top_level_fields_are_not_runtime_overrides(self):
        catalog = ParameterCatalog.from_mapping(parameter_catalog())
        source = experiment()
        source["workers"] = 32
        with self.assertRaisesRegex(ConfigError, "unknown field"):
            ExperimentSpec.from_mapping(source, catalog)

    def test_verified_build_and_analysis_environment_fail_closed(self):
        catalog = ParameterCatalog.from_mapping(parameter_catalog())
        missing_library = experiment()
        del missing_library["build"]["runtime_input_sha256"]["libMain.so"]
        with self.assertRaisesRegex(ConfigError, "libMain.so"):
            ExperimentSpec.from_mapping(missing_library, catalog)

        missing_blas = experiment()
        del missing_blas["analysis"]["environment"]["blas"]
        with self.assertRaisesRegex(ConfigError, "blas"):
            ExperimentSpec.from_mapping(missing_blas, catalog)


class IdentityTests(unittest.TestCase):
    def task(self, **change):
        values = {
            "resolved_physics": {"carrier": "G4_Si", "density_kg_m3": 2330.0},
            "site_mm": (0.0, 1.0, 0.25),
            "seeds": (101, 102),
            "events": 31_250,
            "macro_physics_sha256": HEX_A,
            "lattice_sha256": HEX_B,
            "executable_sha256": HEX_C,
            "runtime_input_sha256": {"g4cmp": HEX_D},
        }
        values.update(change)
        return task_key(**values)

    def test_canonical_json_is_order_independent_and_fail_closed(self):
        self.assertEqual(canonical_json({"b": 2, "a": 1}), canonical_json({"a": 1, "b": 2}))
        self.assertEqual(canonical_json(-0.0), canonical_json(0.0))
        with self.assertRaises(IdentityError):
            canonical_json({"bad": math.nan})
        with self.assertRaises(IdentityError):
            canonical_json({"unordered": {1, 2}})

    def test_task_identity_changes_only_with_raw_semantics(self):
        base = self.task()
        self.assertEqual(
            base,
            self.task(resolved_physics={"density_kg_m3": 2330.0, "carrier": "G4_Si"}),
        )
        self.assertNotEqual(base, self.task(site_mm=(0.0, 1.1, 0.25)))
        self.assertNotEqual(base, self.task(seeds=(103, 104)))
        self.assertNotEqual(base, self.task(events=31_251))
        self.assertNotEqual(base, self.task(executable_sha256=HEX_D))

    def test_simulation_identity_preserves_declared_task_order(self):
        first = self.task()
        second = self.task(site_mm=(1.0, 0.0, 0.25))
        self.assertNotEqual(
            simulation_key(ordered_task_keys=[first, second]),
            simulation_key(ordered_task_keys=[second, first]),
        )

    def test_analysis_identity_includes_artifacts_design_and_statistics(self):
        simulation = simulation_key(ordered_task_keys=[self.task()])
        design = canonical_hash("fixture-design", {"sites": 1})
        base = analysis_key(
            simulation=simulation,
            artifact_sha256=[HEX_A],
            design=design,
            scorer={"version": 1, "implementation_sha256": HEX_B},
            objective={"name": "J", "bootstrap": {"draws": 1000, "seed": 7}},
        )
        changed_artifact = analysis_key(
            simulation=simulation,
            artifact_sha256=[HEX_C],
            design=design,
            scorer={"version": 1, "implementation_sha256": HEX_B},
            objective={"name": "J", "bootstrap": {"draws": 1000, "seed": 7}},
        )
        changed_bootstrap = analysis_key(
            simulation=simulation,
            artifact_sha256=[HEX_A],
            design=design,
            scorer={"version": 1, "implementation_sha256": HEX_B},
            objective={"name": "J", "bootstrap": {"draws": 2000, "seed": 7}},
        )
        self.assertNotEqual(base, changed_artifact)
        self.assertNotEqual(base, changed_bootstrap)

    def test_search_identity_includes_scheduler_and_censoring(self):
        manifest = resolved_fixture().manifest_key
        common = {
            "experiment_manifest": manifest,
            "parameter_space": {"x": {"bounds": [0.0, 1.0]}},
            "analysis_definition": {"objective": "J", "version": 1},
            "optimizer": {"name": "bo-gp", "seed": 1, "implementation_sha256": HEX_A},
            "scheduler": {"mode": "async-completion-order", "parallel": 4},
            "censoring": {"max_trial_hours": 4, "penalty": 1.5},
        }
        base = search_key(**common)
        changed = copy.deepcopy(common)
        changed["scheduler"]["parallel"] = 8
        self.assertNotEqual(base, search_key(**changed))
        changed = copy.deepcopy(common)
        changed["censoring"]["penalty"] = 2.0
        self.assertNotEqual(base, search_key(**changed))


if __name__ == "__main__":
    unittest.main()
