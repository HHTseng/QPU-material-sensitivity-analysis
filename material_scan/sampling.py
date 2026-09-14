"""Recorded sampling designs and immutable task plans.

This module never estimates stratum area.  A production design must carry the
coordinates, labels, weights, allocation, and historical design hash that were
recorded when the experiment was planned.  Loading such a design performs
validation only; it does not run Sobol generators or Monte Carlo integration.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from .identity import canonical_hash, canonical_json


class SamplingError(ValueError):
    """A recorded design or task plan is incomplete or inconsistent."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SamplingError(f"{label} must be a sequence")
    return value


def historical_design_hash(record: Mapping[str, Any]) -> str:
    """Reproduce the 16-hex hash written by historical ``stage4_strata.py``."""

    payload = {key: value for key, value in record.items() if key != "design_hash"}
    # Validate first, then reproduce json.dumps' historical defaults exactly (notably
    # ensure_ascii=True and the spelling of negative zero).
    canonical_json(payload)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class RecordedStratifiedDesign:
    """A byte-independent, immutable view of an already generated design.

    ``recorded_hash`` is the historical 16-character hash stored with the original
    result.  ``record_hash`` hashes every recorded field in the new identity
    domain.  ``design_key`` currently equals ``record_hash`` and is named
    separately so callers never confuse either full key with the historical hash.
    """

    sites_mm: Tuple[Tuple[float, float, float], ...]
    strata: Tuple[str, ...]
    stratum_weights: Tuple[Tuple[str, float], ...]
    stratum_counts: Tuple[Tuple[str, int], ...]
    nearest_electrode: Tuple[Optional[int], ...]
    electrode_weights: Tuple[float, ...]
    recorded_hash: str
    record_hash: str
    design_key: str
    _record: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls, source: Mapping[str, Any], *, verify_recorded_hash: bool = True
    ) -> "RecordedStratifiedDesign":
        if not isinstance(source, Mapping):
            raise SamplingError("recorded design must be a mapping")
        if "stratified_design" in source:
            source = source["stratified_design"]
            if not isinstance(source, Mapping):
                raise SamplingError("stratified_design must be a mapping")
        if any(not isinstance(key, str) for key in source):
            raise SamplingError("recorded design keys must be strings")
        record = {key: _thaw(value) for key, value in source.items()}
        required = {
            "sites_mm",
            "stratum",
            "stratum_weights",
            "stratum_counts",
            "design_hash",
        }
        missing = sorted(required - set(record))
        if missing:
            raise SamplingError(f"recorded stratified design is missing {missing}")

        raw_sites = _sequence(record["sites_mm"], "sites_mm")
        if not raw_sites:
            raise SamplingError("sites_mm must not be empty")
        sites = []
        for index, raw_site in enumerate(raw_sites):
            values = _sequence(raw_site, f"sites_mm[{index}]")
            if len(values) != 3:
                raise SamplingError(f"sites_mm[{index}] must have x, y, and z")
            try:
                site = tuple(float(value) for value in values)
            except (TypeError, ValueError) as exc:
                raise SamplingError(f"sites_mm[{index}] is not numeric") from exc
            if any(not math.isfinite(value) for value in site):
                raise SamplingError(f"sites_mm[{index}] contains a non-finite value")
            sites.append(site)

        strata = tuple(str(item) for item in _sequence(record["stratum"], "stratum"))
        if len(strata) != len(sites) or any(not label for label in strata):
            raise SamplingError("stratum must contain one non-empty label per site")

        weights_raw = record["stratum_weights"]
        counts_raw = record["stratum_counts"]
        if not isinstance(weights_raw, Mapping) or not isinstance(counts_raw, Mapping):
            raise SamplingError("stratum_weights and stratum_counts must be mappings")
        weight_names = set(str(key) for key in weights_raw)
        observed_names = set(strata)
        if weight_names != observed_names:
            raise SamplingError(
                "stratum_weights keys must exactly equal the labels present in stratum"
            )
        weights = []
        for name, raw_weight in weights_raw.items():
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise SamplingError(f"weight for {name!r} is not numeric") from exc
            if not math.isfinite(weight) or weight <= 0.0:
                raise SamplingError(f"weight for {name!r} must be finite and positive")
            weights.append((str(name), weight))
        if not math.isclose(sum(weight for _, weight in weights), 1.0, rel_tol=0.0,
                            abs_tol=1.0e-9):
            raise SamplingError("stratum_weights must sum to one")

        observed = Counter(strata)
        supplied_counts = {str(name): int(value) for name, value in counts_raw.items()}
        if supplied_counts != dict(observed):
            raise SamplingError(
                f"stratum_counts {supplied_counts} do not match recorded labels "
                f"{dict(observed)}"
            )
        counts = tuple((str(name), int(value)) for name, value in counts_raw.items())

        raw_nearest = record.get("nearest_electrode")
        if raw_nearest is None:
            nearest = tuple(None for _ in sites)
        else:
            nearest_values = _sequence(raw_nearest, "nearest_electrode")
            if len(nearest_values) != len(sites):
                raise SamplingError("nearest_electrode must contain one value per site")
            nearest = tuple(None if value is None else int(value) for value in nearest_values)
            if any(value is not None and value < 0 for value in nearest):
                raise SamplingError("nearest_electrode values must be non-negative")

        raw_electrode_weights = record.get("electrode_weights", ())
        electrode_values = _sequence(raw_electrode_weights, "electrode_weights")
        electrode_weights = tuple(float(value) for value in electrode_values)
        if electrode_weights:
            if any(not math.isfinite(value) or value < 0.0 for value in electrode_weights):
                raise SamplingError("electrode_weights must be finite and non-negative")
            if not math.isclose(sum(electrode_weights), 1.0, rel_tol=0.0,
                                abs_tol=1.0e-9):
                raise SamplingError("electrode_weights must sum to one")

        recorded_hash = str(record["design_hash"]).lower()
        if len(recorded_hash) != 16 or any(
            char not in "0123456789abcdef" for char in recorded_hash
        ):
            raise SamplingError("design_hash must be the recorded 16-hex historical hash")
        calculated_historical = historical_design_hash(record)
        if verify_recorded_hash and calculated_historical != recorded_hash:
            raise SamplingError(
                f"recorded design hash mismatch: stored {recorded_hash}, "
                f"calculated {calculated_historical}"
            )

        hash_payload = {key: value for key, value in record.items() if key != "design_hash"}
        record_hash = canonical_hash("recorded-stratified-design/v1", hash_payload)
        return cls(
            sites_mm=tuple(sites),
            strata=strata,
            stratum_weights=tuple(weights),
            stratum_counts=counts,
            nearest_electrode=nearest,
            electrode_weights=electrode_weights,
            recorded_hash=recorded_hash,
            record_hash=record_hash,
            design_key=record_hash,
            _record=_freeze(record),
        )

    @property
    def n_sites(self) -> int:
        return len(self.sites_mm)

    def weight_for(self, stratum: str) -> float:
        try:
            return dict(self.stratum_weights)[stratum]
        except KeyError as exc:
            raise SamplingError(f"unknown stratum {stratum!r}") from exc

    def count_for(self, stratum: str) -> int:
        try:
            return dict(self.stratum_counts)[stratum]
        except KeyError as exc:
            raise SamplingError(f"unknown stratum {stratum!r}") from exc

    def to_manifest(self) -> Mapping[str, Any]:
        """Return a detached copy of the exact recorded design fields."""

        return _thaw(self._record)


def load_recorded_stratified_design(
    source: Any, *, verify_recorded_hash: bool = True
) -> RecordedStratifiedDesign:
    """Load a mapping or JSON result without generating any sampling data."""

    if isinstance(source, Mapping):
        document = source
    else:
        path = Path(source)
        try:
            with path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise SamplingError(f"cannot load recorded design from {path}: {exc}") from exc
    return RecordedStratifiedDesign.from_mapping(
        document, verify_recorded_hash=verify_recorded_hash
    )


def historical_position_seed(seed_base: int, seed_bank_id: int, replica: int,
                         position: int) -> int:
    """Exact seed rule used by the existing Stage 3/4 evaluator."""

    values = (seed_base, seed_bank_id, replica, position)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise SamplingError("seed inputs must be integers")
    if any(value < 0 for value in values):
        raise SamplingError("seed inputs, replica, and position must be non-negative")
    raw = seed_base + seed_bank_id * 7_919_003 + replica * 10_007 + position * 101
    return raw % 900_000_000 + 1


@dataclass(frozen=True)
class SampleTask:
    position: int
    replica: int
    site_mm: Tuple[float, float, float]
    stratum: str
    stratum_weight: float
    node_weight: float
    nearest_electrode: Optional[int]
    seeds: Tuple[int, int]
    events: int

    def to_manifest(self) -> Mapping[str, Any]:
        return {
            "position": self.position,
            "replica": self.replica,
            "site_mm": list(self.site_mm),
            "stratum": self.stratum,
            "stratum_weight": self.stratum_weight,
            "node_weight": self.node_weight,
            "nearest_electrode": self.nearest_electrode,
            "seeds": list(self.seeds),
            "events": self.events,
        }


@dataclass(frozen=True)
class SamplingPlan:
    """An expanded task plan saved before any process launches."""

    design: RecordedStratifiedDesign
    replicas: int
    events_per_task: int
    seed_algorithm: str
    seed_base: int
    seed_bank_id: int
    tasks: Tuple[SampleTask, ...]
    plan_key: str

    @classmethod
    def from_manifest(cls, source: Mapping[str, Any]) -> "SamplingPlan":
        """Validate a saved expanded plan and return its immutable form."""

        if not isinstance(source, Mapping):
            raise SamplingError("sampling manifest must be a mapping")
        unknown = sorted(
            set(source)
            - {
                "kind", "design", "design_key", "replicas", "events_per_task",
                "seed", "tasks", "plan_key",
            }
        )
        if unknown:
            raise SamplingError(f"sampling manifest has unknown field(s) {unknown}")
        if source.get("kind") != "recorded-stratified":
            raise SamplingError("only kind='recorded-stratified' is supported")
        design = RecordedStratifiedDesign.from_mapping(source.get("design", {}))
        if source.get("design_key") != design.design_key:
            raise SamplingError("sampling manifest design_key does not match its design")
        seed = source.get("seed")
        if not isinstance(seed, Mapping):
            raise SamplingError("sampling manifest seed must be a mapping")
        try:
            rebuilt = build_recorded_plan(
                design,
                replicas=source["replicas"],
                events_per_task=source["events_per_task"],
                seed_base=seed["base"],
                seed_bank_id=seed["bank_id"],
                seed_algorithm=seed["algorithm"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, SamplingError):
                raise
            raise SamplingError(f"invalid saved sampling plan: {exc}") from exc
        supplied_tasks = source.get("tasks")
        if canonical_json(supplied_tasks) != canonical_json(rebuilt.to_manifest()["tasks"]):
            raise SamplingError(
                "saved tasks differ from the declared design, replicas, events, or seed rule"
            )
        if source.get("plan_key") != rebuilt.plan_key:
            raise SamplingError("sampling manifest plan_key does not match its task plan")
        return rebuilt

    def to_manifest(self) -> Mapping[str, Any]:
        return {
            "kind": "recorded-stratified",
            "design": self.design.to_manifest(),
            "design_key": self.design.design_key,
            "replicas": self.replicas,
            "events_per_task": self.events_per_task,
            "seed": {
                "algorithm": self.seed_algorithm,
                "base": self.seed_base,
                "bank_id": self.seed_bank_id,
            },
            "tasks": [task.to_manifest() for task in self.tasks],
            "plan_key": self.plan_key,
        }


def build_recorded_plan(
    design: RecordedStratifiedDesign,
    *,
    replicas: int,
    events_per_task: int,
    seed_base: int,
    seed_bank_id: int,
    seed_algorithm: str = "historical-position-v1",
) -> SamplingPlan:
    """Expand a recorded design to immutable tasks without regenerating sites."""

    if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas <= 0:
        raise SamplingError("replicas must be a positive integer")
    if (
        not isinstance(events_per_task, int)
        or isinstance(events_per_task, bool)
        or events_per_task <= 0
    ):
        raise SamplingError("events_per_task must be a positive integer")
    if seed_algorithm != "historical-position-v1":
        raise SamplingError(f"unsupported seed algorithm {seed_algorithm!r}")
    # Validate both integers even for a design with one task.
    historical_position_seed(seed_base, seed_bank_id, 0, 0)

    tasks = []
    for replica in range(replicas):
        for position, (site, stratum, nearest) in enumerate(
            zip(design.sites_mm, design.strata, design.nearest_electrode)
        ):
            first_seed = historical_position_seed(
                seed_base, seed_bank_id, replica, position
            )
            mass = design.weight_for(stratum)
            tasks.append(
                SampleTask(
                    position=position,
                    replica=replica,
                    site_mm=site,
                    stratum=stratum,
                    stratum_weight=mass,
                    node_weight=mass / design.count_for(stratum),
                    nearest_electrode=nearest,
                    seeds=(first_seed, first_seed + 1),
                    events=events_per_task,
                )
            )
    payload = {
        "design_key": design.design_key,
        "replicas": replicas,
        "events_per_task": events_per_task,
        "seed": {
            "algorithm": seed_algorithm,
            "base": seed_base,
            "bank_id": seed_bank_id,
        },
        "tasks": [task.to_manifest() for task in tasks],
    }
    plan_key = canonical_hash("sampling-plan/v1", payload)
    return SamplingPlan(
        design=design,
        replicas=replicas,
        events_per_task=events_per_task,
        seed_algorithm=seed_algorithm,
        seed_base=seed_base,
        seed_bank_id=seed_bank_id,
        tasks=tuple(tasks),
        plan_key=plan_key,
    )
