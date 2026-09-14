"""Golden parity tests for the new scorer and stratified objective.

The fixture files are small byte-for-byte extracts of completed legacy output.
Tests deliberately do not import the legacy implementation: it was used once
to establish the committed answers and is not a second production dependency.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from material_scan.analysis import (
    AnalysisError,
    Block,
    StratifiedDesign,
    paired_stratified_difference,
    stratified_estimate,
    validate_complete_blocks,
)
from material_scan.physics import (
    PhysicsError,
    calculate_qps,
    calculate_xqps,
    score_hit_file,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
RANDOM_FIXTURE = FIXTURES / "random_trial_9a3f0ab28d9d"
STRATIFIED_FIXTURE = FIXTURES / "stratified"


def _required(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"required parity fixture is missing: {path}")
    return path


class HitScoringParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = json.loads(_required(RANDOM_FIXTURE / "fixture.json").read_text())
        cls.hit_files = sorted(_required(RANDOM_FIXTURE / "hits").glob("*.txt"))
        expected_count = cls.metadata["file_count"]
        if len(cls.hit_files) != expected_count:
            raise AssertionError(
                f"random-trial fixture has {len(cls.hit_files)} files, expected {expected_count}"
            )
        size = sum(path.stat().st_size for path in cls.hit_files)
        if size != cls.metadata["total_bytes"]:
            raise AssertionError(
                f"random-trial fixture is {size} bytes, expected {cls.metadata['total_bytes']}"
            )
        digest = hashlib.sha256()
        for path in cls.hit_files:
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        actual = digest.hexdigest()
        if actual != cls.metadata["fixture_sha256"]:
            raise AssertionError(
                f"random-trial fixture checksum is {actual}, expected "
                f"{cls.metadata['fixture_sha256']}"
            )
        expected_path = _required(RANDOM_FIXTURE / "expected_qps.npz")
        expected_digest = hashlib.sha256(expected_path.read_bytes()).hexdigest()
        if expected_digest != cls.metadata["expected_qps_npz_sha256"]:
            raise AssertionError(
                f"random-trial expected matrix checksum is {expected_digest}, expected "
                f"{cls.metadata['expected_qps_npz_sha256']}"
            )
        cls.expected_matrix = np.load(expected_path, allow_pickle=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.expected_matrix.close()

    def _score(self, path: Path):
        return score_hit_file(
            path,
            self.metadata["gap_eV"],
            self.metadata["electrode_x_mm"],
            self.metadata["electrode_y_mm"],
            self.metadata["chip_surface_z_m"],
        )

    def test_completed_random_trial_matches_recorded_counts_exactly(self) -> None:
        scores = [self._score(path) for path in self.hit_files]
        per_electrode = np.sum(
            np.asarray([score.per_electrode_qps for score in scores]), axis=0
        )
        expected = self.metadata["expected"]
        self.assertEqual(sum(score.n_hits for score in scores), expected["n_hits"])
        self.assertEqual(sum(score.total_qps for score in scores), expected["total_qps"])
        np.testing.assert_array_equal(per_electrode, expected["per_electrode_qps"])
        aggregate_matrix = np.sum(
            np.asarray([score.qps_by_electrode_time for score in scores]), axis=0
        )
        np.testing.assert_array_equal(
            scores[0].snapshot_times_ns,
            self.expected_matrix["snapshot_times_ns"],
        )
        np.testing.assert_array_equal(
            aggregate_matrix,
            self.expected_matrix["qps_by_electrode_time"],
        )
        events = self.metadata["events_per_file"] * len(scores)
        self.assertEqual(sum(score.total_qps for score in scores) / events,
                         expected["qps_per_primary"])

    def test_header_only_completed_file_is_a_measured_zero(self) -> None:
        zero_files = [path for path in self.hit_files if path.stat().st_size == 190]
        self.assertGreater(len(zero_files), 0, "golden trial must retain a real zero-hit task")
        score = self._score(zero_files[0])
        self.assertEqual(score.n_hits, 0)
        self.assertEqual(score.total_qps, 0.0)
        self.assertEqual(score.per_electrode_qps, (0.0,) * 17)

    def test_zero_byte_missing_and_malformed_files_are_not_zero_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.csv"
            empty.touch()
            malformed = root / "malformed.csv"
            malformed.write_text("End X [m],End Y [m]\n0,0\n")
            missing = root / "missing.csv"
            for path in (empty, malformed, missing):
                with self.subTest(path=path.name), self.assertRaises(PhysicsError):
                    self._score(path)

    def test_rounding_ties_electrode_ties_time_ties_and_surface_filter_match_legacy(self) -> None:
        surface = 0.0002625
        snapshots, qps = calculate_qps(
            energy_deposited_eV=[1.5, 2.5, 0.5, 3.5, 100.0, -10.0],
            end_x_m=[0.0, 0.001, -0.001, -0.001, 0.001, -0.001],
            end_y_m=[0.0] * 6,
            end_z_m=[surface, surface, surface, surface + 5e-9,
                     surface + 5e-8, surface],
            final_time_ns=[150.0, 450.0, 0.0, 0.0, 0.0, 0.0],
            gap_eV=1.0,
            electrode_x_mm=[-1.0, 1.0],
            electrode_y_mm=[0.0, 0.0],
            chip_surface_z_m=surface,
        )
        self.assertEqual(snapshots[0], 0.0)
        self.assertEqual(snapshots[1], 300.0)
        self.assertEqual(qps[0, 0], 6.0)  # 1.5 -> 2; 3.5 -> 4; tie -> electrode 0/bin 0
        self.assertEqual(qps[1, 1], 2.0)  # 2.5 -> 2; 450 ns -> lower 300 ns bin
        self.assertEqual(float(qps.sum()), 8.0)  # 0.5 -> 0; off-surface/negative ignored

    def test_nonfinite_hit_data_fails_closed(self) -> None:
        with self.assertRaises(PhysicsError):
            calculate_qps(
                [1.0], [float("nan")], [0.0], [0.0002625], [0.0],
                0.000191, [0.0], [0.0], 0.0002625,
            )

    def test_qp_density_ode_matches_legacy_golden_arrays(self) -> None:
        time_us, decoherence = calculate_xqps(
            snapshot_times_ns=[0.0, 300.0, 600.0],
            qps_by_electrode_time=[[1.0, 2.0, 0.0], [0.0, 1.0, 3.0]],
            aluminum_gap_hz=4.0e10,
            qubit_frequency_hz=5.0e9,
            recombination=0.1,
            loss_rate=0.2,
            injection_scale=0.3,
            pulse_time_us=0.6,
            cooper_pairs_per_um3=4.0,
            electrode_height_um=5.0,
            electrode_width_um=6.0,
            film_thickness_um=0.1,
            simulated_primaries=1000,
        )
        expected_time = np.array([0.0, 0.3, 0.6, 0.8999999999999999, 1.2])
        expected_decoherence = np.array(
            [
                [0.0, 0.3, 1.1819999325, 1.7110788887071193, 1.6084119595414699],
                [0.0, 0.0, 0.3, 1.4819999325, 2.2930782893071497],
            ]
        )
        np.testing.assert_allclose(time_us, expected_time, rtol=1e-12, atol=1e-15)
        np.testing.assert_allclose(
            decoherence, expected_decoherence, rtol=1e-12, atol=1e-15
        )

    def test_qp_density_ode_rejects_unphysical_rate_and_scale_inputs(self) -> None:
        base = {
            "snapshot_times_ns": [0.0, 300.0],
            "qps_by_electrode_time": [[1.0, 0.0]],
            "aluminum_gap_hz": 4.0e10,
            "qubit_frequency_hz": 5.0e9,
            "recombination": 0.1,
            "loss_rate": 0.2,
            "injection_scale": 0.3,
            "pulse_time_us": 0.6,
            "cooper_pairs_per_um3": 4.0,
            "electrode_height_um": 5.0,
            "electrode_width_um": 6.0,
            "film_thickness_um": 0.1,
            "simulated_primaries": 1000,
        }
        for field, value in (
            ("recombination", -0.1),
            ("loss_rate", -0.1),
            ("injection_scale", -0.1),
            ("recombination", float("nan")),
            ("loss_rate", float("inf")),
            ("injection_scale", float("nan")),
        ):
            invalid = dict(base)
            invalid[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(PhysicsError):
                calculate_xqps(**invalid)


class StratifiedObjectiveParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = json.loads(_required(STRATIFIED_FIXTURE / "fixture.json").read_text())
        npz_path = _required(STRATIFIED_FIXTURE / "blocks.npz")
        digest = hashlib.sha256(npz_path.read_bytes()).hexdigest()
        if digest != cls.metadata["blocks_npz_sha256"]:
            raise AssertionError(
                f"stratified fixture checksum is {digest}, expected "
                f"{cls.metadata['blocks_npz_sha256']}"
            )
        cls.data = np.load(npz_path, allow_pickle=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.data.close()

    def _case(self, name: str) -> tuple[list[Block], StratifiedDesign]:
        level = 128 if name.startswith("s128") else 512
        total = self.data[name + "_total_qps"]
        per_electrode = self.data[name + "_per_electrode_qps"]
        n_hits = self.data[name + "_n_hits"]
        events = int(self.data[name + "_events"])
        blocks = [
            Block(
                position=position,
                replica=replica,
                events=events,
                total_qps=float(total[replica, position]),
                per_electrode_qps=tuple(
                    float(value) for value in per_electrode[replica, position]
                ),
                n_hits=int(n_hits[replica, position]),
            )
            for replica in range(total.shape[0])
            for position in range(total.shape[1])
        ]
        prefix = f"s{level}_design_"
        weight_names = [str(value) for value in self.data[prefix + "weight_names"]]
        weights = [float(value) for value in self.data[prefix + "weights"]]
        design = StratifiedDesign.from_values(
            position_strata=[str(value) for value in self.data[prefix + "strata"]],
            stratum_weights=dict(zip(weight_names, weights)),
            replica_ids=range(total.shape[0]),
            events_per_task=events,
            gun_energy_eV=float(self.data[prefix + "gun_energy_eV"]),
            electrode_weights=self.data[prefix + "electrode_weights"],
            design_hash=str(self.data[prefix + "hash"]),
        )
        return blocks, design

    def assertClose(self, actual: float, expected: float) -> None:
        self.assertTrue(
            np.isclose(actual, expected, rtol=1e-12, atol=1e-15),
            f"{actual:.17g} != {expected:.17g}",
        )

    def test_stratified_128_and_512_anchors_match_recorded_results(self) -> None:
        for name, expected in self.metadata["cases"].items():
            with self.subTest(case=name):
                blocks, design = self._case(name)
                result = stratified_estimate(blocks, design)
                self.assertClose(result.value, expected["J"])
                self.assertClose(result.standard_error, expected["se"])
                self.assertClose(result.spatial_r95, expected["spatial_R95"])
                self.assertClose(
                    result.spatial_r95_standard_error, expected["spatial_R95_se"]
                )
                self.assertEqual(result.n_sites, 128 if name.startswith("s128") else 512)
                self.assertEqual(result.n_replicas, 8)
                self.assertEqual(result.design_hash, design.design_hash)

    def test_recorded_sic_baseline_paired_differences_match(self) -> None:
        for level in (128, 512):
            sic, design = self._case(f"s{level}_sic")
            baseline, baseline_design = self._case(f"s{level}_baseline")
            self.assertEqual(design, baseline_design)
            result = paired_stratified_difference(sic, baseline, design)
            expected = self.metadata["cases"][f"s{level}_sic"]
            self.assertClose(result.value, expected["paired_delta_from_baseline"])

    def _small_complete_case(self) -> tuple[list[Block], StratifiedDesign]:
        design = StratifiedDesign.from_values(
            position_strata=("near", "bulk"),
            stratum_weights={"near": 0.25, "bulk": 0.75},
            replica_ids=(0, 1),
            events_per_task=100,
            gun_energy_eV=0.01,
            electrode_weights=(0.5, 0.5),
            design_hash="strict-test",
        )
        blocks = [
            Block(position=position, replica=replica, events=100,
                  total_qps=total, per_electrode_qps=electrodes, n_hits=1)
            for position, total, electrodes in (
                (0, 4.0, (1.0, 3.0)),
                (1, 2.0, (2.0, 0.0)),
            )
            for replica in (0, 1)
        ]
        return blocks, design

    def test_incomplete_duplicate_and_undeclared_blocks_fail_closed(self) -> None:
        blocks, design = self._small_complete_case()
        cases = {
            "missing": blocks[:-1],
            "duplicate": blocks + [blocks[0]],
            "undeclared position": blocks + [
                Block(2, 0, 100, 0.0, (0.0, 0.0), 0)
            ],
        }
        for label, bad in cases.items():
            with self.subTest(case=label), self.assertRaises(AnalysisError):
                stratified_estimate(bad, design, bootstrap_draws=2, tail_bootstrap_draws=2)

    def test_inconsistent_block_payloads_fail_closed(self) -> None:
        blocks, design = self._small_complete_case()
        replacements = {
            "event count": Block(0, 0, 99, 4.0, (1.0, 3.0), 1),
            "missing electrodes": Block(0, 0, 100, 4.0, None, 1),
            "electrode length": Block(0, 0, 100, 4.0, (4.0,), 1),
            "electrode sum": Block(0, 0, 100, 5.0, (1.0, 3.0), 1),
            "negative total": Block(0, 0, 100, -1.0, (0.0, 0.0), 1),
        }
        for label, replacement in replacements.items():
            bad = [replacement if (item.position, item.replica) == (0, 0) else item
                   for item in blocks]
            with self.subTest(case=label), self.assertRaises(AnalysisError):
                stratified_estimate(bad, design, bootstrap_draws=2, tail_bootstrap_draws=2)

    def test_scalar_legacy_validation_does_not_require_electrode_vectors(self) -> None:
        blocks, design = self._small_complete_case()
        scalar_blocks = [
            Block(item.position, item.replica, item.events, item.total_qps, None, item.n_hits)
            for item in blocks
        ]
        validated = validate_complete_blocks(scalar_blocks, design)
        self.assertEqual(len(validated), 4)
        with self.assertRaises(AnalysisError):
            stratified_estimate(
                scalar_blocks, design, bootstrap_draws=2, tail_bootstrap_draws=2
            )

    def test_missing_weighted_stratum_cannot_be_renormalized(self) -> None:
        with self.assertRaises(AnalysisError):
            StratifiedDesign.from_values(
                position_strata=("near",),
                stratum_weights={"near": 0.25, "bulk": 0.75},
                replica_ids=(0,),
                events_per_task=100,
                gun_energy_eV=0.01,
                electrode_weights=(1.0,),
                design_hash="bad",
            )


if __name__ == "__main__":
    unittest.main()
