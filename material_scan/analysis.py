"""Explicit, fail-closed objective calculations for material-scan results."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import numpy as np


class AnalysisError(ValueError):
    """A declared experiment design and its completed blocks disagree."""


@dataclass(frozen=True, slots=True)
class Block:
    """One scored site/replica task."""

    position: int
    replica: int
    events: int
    total_qps: float
    per_electrode_qps: tuple[float, ...] | None
    n_hits: int | None = None


@dataclass(frozen=True, slots=True)
class StratifiedDesign:
    """Everything needed to interpret a complete stratified trial."""

    position_strata: tuple[str, ...]
    stratum_weights: tuple[tuple[str, float], ...]
    replica_ids: tuple[int, ...]
    events_per_task: int
    gun_energy_eV: float
    electrode_weights: tuple[float, ...]
    design_hash: str
    injection_law: str = "uniform_surface"

    @classmethod
    def from_values(
        cls,
        *,
        position_strata: Sequence[str],
        stratum_weights: Mapping[str, float],
        replica_ids: Sequence[int],
        events_per_task: int,
        gun_energy_eV: float,
        electrode_weights: Sequence[float],
        design_hash: str,
        injection_law: str = "uniform_surface",
    ) -> "StratifiedDesign":
        design = cls(
            position_strata=tuple(str(value) for value in position_strata),
            stratum_weights=tuple((str(key), float(value)) for key, value in stratum_weights.items()),
            replica_ids=tuple(int(value) for value in replica_ids),
            events_per_task=int(events_per_task),
            gun_energy_eV=float(gun_energy_eV),
            electrode_weights=tuple(float(value) for value in electrode_weights),
            design_hash=str(design_hash),
            injection_law=str(injection_law),
        )
        design.validate()
        return design

    @property
    def weights(self) -> dict[str, float]:
        return dict(self.stratum_weights)

    @property
    def positions(self) -> tuple[int, ...]:
        return tuple(range(len(self.position_strata)))

    def validate(self) -> None:
        if not self.position_strata:
            raise AnalysisError("a stratified design must declare at least one position")
        if not self.replica_ids or len(set(self.replica_ids)) != len(self.replica_ids):
            raise AnalysisError("replica IDs must be non-empty and unique")
        if self.events_per_task <= 0:
            raise AnalysisError("events_per_task must be positive")
        if not math.isfinite(self.gun_energy_eV) or self.gun_energy_eV <= 0.0:
            raise AnalysisError("gun_energy_eV must be positive and finite")
        if not self.design_hash:
            raise AnalysisError("design_hash is required")
        if self.injection_law != "uniform_surface":
            raise AnalysisError("area weights are valid only for uniform_surface injection")

        weights = self.weights
        if len(weights) != len(self.stratum_weights) or not weights:
            raise AnalysisError("stratum names must be non-empty and unique")
        if any(not math.isfinite(value) or value <= 0.0 for value in weights.values()):
            raise AnalysisError("every stratum weight must be positive and finite")
        if not math.isclose(sum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise AnalysisError("stratum weights must sum to one")
        labels = set(self.position_strata)
        if labels != set(weights):
            raise AnalysisError(
                "position labels and weighted strata differ: "
                f"labels={sorted(labels)}, weights={sorted(weights)}"
            )

        if not self.electrode_weights:
            raise AnalysisError("electrode_weights must be declared")
        if any(
            not math.isfinite(value) or value < 0.0 for value in self.electrode_weights
        ):
            raise AnalysisError("electrode weights must be finite and non-negative")
        if not math.isclose(sum(self.electrode_weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise AnalysisError("electrode weights must sum to one")


@dataclass(frozen=True, slots=True)
class StratifiedEstimate:
    value: float
    standard_error: float
    relative_standard_error: float | None
    means_by_stratum: dict[str, float]
    sites_by_stratum: dict[str, int]
    weights_by_stratum: dict[str, float]
    maximum_site_leverage: float
    spatial_r95: float
    spatial_r95_standard_error: float
    naive_equal_site_mean: float
    n_sites: int
    n_replicas: int
    events_per_task: int
    design_hash: str
    units: str = "weighted_junction_QPs_per_injected_eV"


@dataclass(frozen=True, slots=True)
class PairedDifference:
    value: float
    standard_error: float
    relative_to_b: float | None
    z: float | None
    confidence_interval_95: tuple[float, float]
    n_sites: int
    sites_favouring_a: int


def _coerce_block(value: Block | Mapping[str, object]) -> Block:
    if isinstance(value, Block):
        return value
    per_electrode = value.get("per_electrode_qps")
    return Block(
        position=int(value["position"]),
        replica=int(value["replica"]),
        events=int(value["events"]),
        total_qps=float(value["total_qps"]),
        per_electrode_qps=(
            None if per_electrode is None else tuple(float(item) for item in per_electrode)
        ),
        n_hits=(None if value.get("n_hits") is None else int(value["n_hits"])),
    )


def validate_complete_blocks(
    blocks: Sequence[Block | Mapping[str, object]],
    design: StratifiedDesign,
    *,
    require_electrodes: bool = False,
) -> dict[tuple[int, int], Block]:
    """Validate the exact declared position-by-replica Cartesian product.

    Scalar historical objectives may use blocks without an electrode vector.  The
    electrode-aware device objective opts into the stronger requirement.
    """

    design.validate()
    expected = {(position, replica) for position in design.positions for replica in design.replica_ids}
    indexed: dict[tuple[int, int], Block] = {}
    for raw in blocks:
        block = _coerce_block(raw)
        key = (block.position, block.replica)
        if key in indexed:
            raise AnalysisError(f"duplicate block for position={key[0]}, replica={key[1]}")
        if key not in expected:
            raise AnalysisError(f"undeclared block for position={key[0]}, replica={key[1]}")
        if block.events != design.events_per_task:
            raise AnalysisError(
                f"block {key} has {block.events} events, expected {design.events_per_task}"
            )
        if not math.isfinite(block.total_qps) or block.total_qps < 0.0:
            raise AnalysisError(f"block {key} has invalid total_qps")
        if require_electrodes and block.per_electrode_qps is None:
            raise AnalysisError(f"block {key} has no per-electrode QP vector")
        if block.per_electrode_qps is not None and len(block.per_electrode_qps) != len(design.electrode_weights):
            raise AnalysisError(
                f"block {key} has {len(block.per_electrode_qps)} electrodes, "
                f"expected {len(design.electrode_weights)}"
            )
        if block.per_electrode_qps is not None and any(
            not math.isfinite(item) or item < 0.0 for item in block.per_electrode_qps
        ):
            raise AnalysisError(f"block {key} has invalid per-electrode QPs")
        electrode_total = (
            None if block.per_electrode_qps is None else math.fsum(block.per_electrode_qps)
        )
        if electrode_total is not None and not math.isclose(
            electrode_total, block.total_qps, rel_tol=0.0, abs_tol=1e-9
        ):
            raise AnalysisError(
                f"block {key} total_qps={block.total_qps} but electrodes sum to "
                f"{electrode_total}"
            )
        if block.n_hits is not None and block.n_hits < 0:
            raise AnalysisError(f"block {key} has negative n_hits")
        indexed[key] = block

    missing = expected - set(indexed)
    if missing:
        first = min(missing)
        raise AnalysisError(
            f"incomplete block set: missing {len(missing)} of {len(expected)} blocks; "
            f"first missing position={first[0]}, replica={first[1]}"
        )
    return indexed


def _burdens(
    blocks: Sequence[Block | Mapping[str, object]], design: StratifiedDesign
) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
    indexed = validate_complete_blocks(blocks, design, require_electrodes=True)
    electrode_weights = np.asarray(design.electrode_weights, dtype=np.float64)
    burden_by_position: dict[int, list[float]] = {}
    worst_by_position: dict[int, list[float]] = {}
    scale = design.events_per_task * design.gun_energy_eV
    for position in design.positions:
        burden_by_position[position] = []
        worst_by_position[position] = []
        for replica in design.replica_ids:
            block = indexed[(position, replica)]
            per_electrode = np.asarray(block.per_electrode_qps, dtype=np.float64)
            burden_by_position[position].append(float(per_electrode @ electrode_weights) / scale)
            worst_by_position[position].append(float(per_electrode.max()) / scale)
    return burden_by_position, worst_by_position


def _positions_by_stratum(design: StratifiedDesign) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for position, stratum in enumerate(design.position_strata):
        grouped.setdefault(stratum, []).append(position)
    return grouped


def _hierarchical_standard_error(
    values: Mapping[int, Sequence[float]],
    design: StratifiedDesign,
    *,
    draws: int,
    seed: int,
) -> float:
    rng = np.random.default_rng(seed)
    grouped = _positions_by_stratum(design)
    weights = design.weights
    estimates = np.empty(draws)
    for draw in range(draws):
        total = 0.0
        for stratum, positions in grouped.items():
            matrix = np.asarray([values[position] for position in positions])
            selected = rng.choice(len(positions), size=len(positions), replace=True)
            selected_values = matrix[selected]
            replica_index = rng.choice(
                selected_values.shape[1], size=selected_values.shape, replace=True
            )
            replica_means = np.take_along_axis(
                selected_values, replica_index, axis=1
            ).mean(axis=1)
            total += weights[stratum] * float(replica_means.mean())
        estimates[draw] = total
    return float(estimates.std(ddof=1))


def _weighted_tail_mean(
    per_site: Mapping[int, float], design: StratifiedDesign, tail_probability: float = 0.05
) -> float:
    grouped = _positions_by_stratum(design)
    weights = design.weights
    pairs = sorted(
        (
            (value, weights[design.position_strata[position]] / len(grouped[design.position_strata[position]]))
            for position, value in per_site.items()
        ),
        reverse=True,
    )
    cutoff = tail_probability * sum(weight for _, weight in pairs)
    numerator = 0.0
    mass = 0.0
    for value, weight in pairs:
        take = min(weight, cutoff - mass)
        if take <= 0.0:
            break
        numerator += value * take
        mass += take
    if mass <= 0.0:
        raise AnalysisError("cannot calculate spatial tail risk from an empty design")
    return float(numerator / mass)


def _tail_standard_error(
    values: Mapping[int, Sequence[float]],
    design: StratifiedDesign,
    *,
    draws: int,
    seed: int,
) -> float:
    rng = np.random.default_rng(seed)
    grouped = _positions_by_stratum(design)
    weights = design.weights
    estimates = np.empty(draws)
    for draw in range(draws):
        sampled: list[tuple[float, float]] = []
        for stratum, positions in grouped.items():
            matrix = np.asarray([values[position] for position in positions])
            selected = rng.choice(len(positions), size=len(positions), replace=True)
            selected_values = matrix[selected]
            replica_index = rng.choice(
                selected_values.shape[1], size=selected_values.shape, replace=True
            )
            replica_means = np.take_along_axis(
                selected_values, replica_index, axis=1
            ).mean(axis=1)
            for value in replica_means:
                sampled.append((value, weights[stratum] / len(positions)))
        sampled.sort(reverse=True)
        cutoff = 0.05 * sum(weight for _, weight in sampled)
        numerator = 0.0
        mass = 0.0
        for value, weight in sampled:
            take = min(weight, cutoff - mass)
            if take <= 0.0:
                break
            numerator += value * take
            mass += take
        estimates[draw] = numerator / mass
    return float(np.nanstd(estimates, ddof=1))


def stratified_estimate(
    blocks: Sequence[Block | Mapping[str, object]],
    design: StratifiedDesign,
    *,
    bootstrap_draws: int = 1000,
    bootstrap_seed: int = 7,
    tail_bootstrap_draws: int = 500,
    tail_bootstrap_seed: int = 11,
) -> StratifiedEstimate:
    """Calculate ``sum(W_h * mean_h)`` for the complete declared design."""

    if bootstrap_draws < 2 or tail_bootstrap_draws < 2:
        raise AnalysisError("bootstrap draw counts must be at least two")
    burden, worst = _burdens(blocks, design)
    grouped = _positions_by_stratum(design)
    weights = design.weights
    per_site = {position: float(np.mean(values)) for position, values in burden.items()}
    worst_per_site = {position: float(np.mean(values)) for position, values in worst.items()}
    means = {
        stratum: float(np.mean([per_site[position] for position in positions]))
        for stratum, positions in grouped.items()
    }
    value = sum(weights[stratum] * means[stratum] for stratum in grouped)
    standard_error = _hierarchical_standard_error(
        burden, design, draws=bootstrap_draws, seed=bootstrap_seed
    )
    tail = _weighted_tail_mean(worst_per_site, design)
    tail_standard_error = _tail_standard_error(
        worst, design, draws=tail_bootstrap_draws, seed=tail_bootstrap_seed
    )
    leverage = {
        position: weights[design.position_strata[position]]
        * per_site[position]
        / len(grouped[design.position_strata[position]])
        / value
        for position in design.positions
    }
    return StratifiedEstimate(
        value=float(value),
        standard_error=standard_error,
        relative_standard_error=(standard_error / value if value else None),
        means_by_stratum=means,
        sites_by_stratum={key: len(value) for key, value in grouped.items()},
        weights_by_stratum=weights,
        maximum_site_leverage=max(leverage.values()),
        spatial_r95=tail,
        spatial_r95_standard_error=tail_standard_error,
        naive_equal_site_mean=float(np.mean(list(per_site.values()))),
        n_sites=len(design.positions),
        n_replicas=len(design.replica_ids),
        events_per_task=design.events_per_task,
        design_hash=design.design_hash,
    )


def paired_stratified_difference(
    blocks_a: Sequence[Block | Mapping[str, object]],
    blocks_b: Sequence[Block | Mapping[str, object]],
    design: StratifiedDesign,
    *,
    bootstrap_draws: int = 1000,
    bootstrap_seed: int = 23,
) -> PairedDifference:
    """Site- and replica-matched ``A - B`` under the stratified estimator."""

    if bootstrap_draws < 2:
        raise AnalysisError("bootstrap_draws must be at least two")
    burden_a, _ = _burdens(blocks_a, design)
    burden_b, _ = _burdens(blocks_b, design)
    delta = {
        position: [a - b for a, b in zip(burden_a[position], burden_b[position])]
        for position in design.positions
    }
    grouped = _positions_by_stratum(design)
    weights = design.weights
    value = sum(
        weights[stratum]
        * float(np.mean([float(np.mean(delta[position])) for position in positions]))
        for stratum, positions in grouped.items()
    )

    rng = np.random.default_rng(bootstrap_seed)
    estimates = np.empty(bootstrap_draws)
    for draw in range(bootstrap_draws):
        total = 0.0
        for stratum, positions in grouped.items():
            matrix = np.asarray([delta[position] for position in positions])
            selected = rng.choice(len(positions), size=len(positions), replace=True)
            selected_values = matrix[selected]
            replica_index = rng.choice(
                selected_values.shape[1], size=selected_values.shape, replace=True
            )
            replica_means = np.take_along_axis(
                selected_values, replica_index, axis=1
            ).mean(axis=1)
            total += weights[stratum] * float(replica_means.mean())
        estimates[draw] = total
    standard_error = float(estimates.std(ddof=1))
    baseline = sum(
        weights[stratum]
        * float(np.mean([float(np.mean(burden_b[position])) for position in positions]))
        for stratum, positions in grouped.items()
    )
    sites_favouring_a = sum(
        float(np.mean(delta[position])) < 0.0 for position in design.positions
    )
    return PairedDifference(
        value=float(value),
        standard_error=standard_error,
        relative_to_b=(float(value / baseline) if baseline else None),
        z=(float(value / standard_error) if standard_error else None),
        confidence_interval_95=(
            float(value - 1.96 * standard_error),
            float(value + 1.96 * standard_error),
        ),
        n_sites=len(design.positions),
        sites_favouring_a=int(sites_favouring_a),
    )
