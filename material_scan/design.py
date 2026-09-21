"""Generate nested extensions of a recorded electrode-aware site design."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.stats import qmc

from .sampling import RecordedStratifiedDesign, historical_design_hash


STRATA = ("S0_junction", "S1_le_0.05", "S2_le_0.20", "S3_le_0.50", "S4_bulk")


def _classify(
    x: float,
    y: float,
    xs: np.ndarray,
    ys: np.ndarray,
    half_width: float,
    half_height: float,
    edges: Sequence[float],
) -> tuple[str, int]:
    dx = np.maximum(np.abs(x - xs) - half_width, 0.0)
    dy = np.maximum(np.abs(y - ys) - half_height, 0.0)
    distance = np.hypot(dx, dy)
    electrode = int(np.argmin(distance))
    value = float(distance[electrode])
    if value <= 0.0:
        return STRATA[0], electrode
    for label, edge in zip(STRATA[1:-1], edges):
        if value <= float(edge):
            return label, electrode
    return STRATA[-1], electrode


def _points(
    label: str,
    count: int,
    *,
    xs: np.ndarray,
    ys: np.ndarray,
    half_width: float,
    half_height: float,
    half_span: float,
    edges: Sequence[float],
    seed: int,
) -> list[tuple[float, float, int]]:
    points: list[tuple[float, float, int]] = []
    if label == STRATA[0]:
        random = np.random.default_rng(seed)
        for index in range(count):
            electrode = index % len(xs)
            points.append(
                (
                    round(float(xs[electrode]) + float(random.uniform(-half_width, half_width)), 6),
                    round(float(ys[electrode]) + float(random.uniform(-half_height, half_height)), 6),
                    electrode,
                )
            )
        return points

    engine = qmc.Sobol(d=2, scramble=True, seed=seed)
    batches = 0
    while len(points) < count:
        batches += 1
        if batches > 8000:
            raise RuntimeError(f"could not fill {label!r} to {count} sites")
        for unit in engine.random(8192):
            x = round(float(unit[0]) * 2.0 * half_span - half_span, 6)
            y = round(float(unit[1]) * 2.0 * half_span - half_span, 6)
            observed, electrode = _classify(
                x, y, xs, ys, half_width, half_height, edges
            )
            if observed == label:
                points.append((x, y, electrode))
                if len(points) == count:
                    break
    return points


def extend_recorded_design(
    source: Mapping[str, Any],
    counts: Mapping[str, int],
    *,
    electrode_x_mm: Sequence[float],
    electrode_y_mm: Sequence[float],
) -> dict[str, Any]:
    """Return a larger design and prove every old per-stratum prefix is unchanged."""

    old = RecordedStratifiedDesign.from_mapping(source)
    record = old.to_manifest()
    old_counts = dict(old.stratum_counts)
    names = tuple(record["stratum_weights"])
    if set(names) != set(STRATA):
        raise ValueError(f"unexpected strata {names}")
    target = {name: int(counts.get(name, old_counts[name])) for name in names}
    for name in names:
        if target[name] < old_counts[name]:
            raise ValueError(f"extension would remove sites from {name}")

    xs = np.asarray(electrode_x_mm, dtype=float)
    ys = np.asarray(electrode_y_mm, dtype=float)
    if xs.shape != ys.shape or xs.ndim != 1 or xs.size == 0:
        raise ValueError("electrode coordinate arrays differ or are empty")
    half_width, half_height = (float(value) for value in record["junction_half_extent_mm"])
    edges = tuple(float(value) for value in record["band_edges_mm"])
    half_span = float(record["half_span_mm"])
    base_seed = int(record["position_seed"])

    old_by_label: dict[str, list[tuple[list[float], int | None]]] = {name: [] for name in names}
    for site, label, electrode in zip(
        record["sites_mm"], record["stratum"], record["nearest_electrode"]
    ):
        old_by_label[label].append((site, electrode))

    # Preserve the complete earlier design as a global prefix.  Position is part
    # of the seed rule, so inserting new S1 points before old S2 points would
    # keep coordinates nested while silently changing the old random streams.
    sites: list[list[float]] = [list(site) for site in record["sites_mm"]]
    labels: list[str] = list(record["stratum"])
    nearest: list[int] = list(record["nearest_electrode"])
    z = float(record["sites_mm"][0][2])
    for label in STRATA:
        sub_seed = (base_seed * 1_000_003 + STRATA.index(label)) % (2**31 - 1)
        generated = _points(
            label,
            target[label],
            xs=xs,
            ys=ys,
            half_width=half_width,
            half_height=half_height,
            half_span=half_span,
            edges=edges,
            seed=sub_seed,
        )
        expected_prefix = [([x, y, z], electrode) for x, y, electrode in generated[:old_counts[label]]]
        if json.dumps(expected_prefix, separators=(",", ":")) != json.dumps(
            old_by_label[label], separators=(",", ":")
        ):
            raise ValueError(f"recorded {label} sites are not the generator prefix")
        for x, y, electrode in generated[old_counts[label]:]:
            sites.append([x, y, z])
            labels.append(label)
            nearest.append(electrode)

    record["sites_mm"] = sites
    record["stratum"] = labels
    record["nearest_electrode"] = nearest
    record["stratum_counts"] = target
    record.pop("design_hash", None)
    record["design_hash"] = historical_design_hash(record)
    # Final validation also checks weights, labels, and the recomputed hash.
    return RecordedStratifiedDesign.from_mapping(record).to_manifest()
