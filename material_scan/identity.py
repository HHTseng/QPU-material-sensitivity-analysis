"""Content identities for material-scan experiments.

The four public identities deliberately describe different layers:

``task_key``
    Raw-output semantics for one Geant4 invocation.  Attempt identifiers,
    paths, host names, worker counts, and wall-clock data are excluded.

``simulation_key``
    The ordered declared task set for one candidate.  It is independent of the
    objective and optimizer.

``analysis_key``
    A particular interpretation of particular raw artifacts.  It therefore
    includes artifact checksums, the recorded sampling design, scorer details,
    and objective/uncertainty settings.

``search_key``
    An adaptive search protocol.  It includes the parameter space, analysis
    definition, optimizer implementation, scheduler, and censoring policy.

All hashes use one strict canonical JSON encoder.  Inputs which JSON would
silently reinterpret (NaN, infinity, sets, bytes, or non-string mapping keys)
are refused.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import hashlib
import json
import math
from pathlib import PurePath
from typing import Any, Mapping, Sequence


IDENTITY_SCHEMA = "material-scan-identity-1"


class IdentityError(ValueError):
    """An identity payload is incomplete or cannot be canonicalized."""


def _plain(value: Any) -> Any:
    """Return a JSON-compatible value without silently losing information."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IdentityError("identity values must be finite (no NaN or infinity)")
        # Negative zero has no physical distinction here and is easy to create
        # through numerical transforms.  Canonicalize it to one spelling.
        return 0.0 if value == 0.0 else value
    if isinstance(value, PurePath):
        # Paths are allowed in manifests, but public identity builders below do
        # not accept operational paths in their semantic payloads.
        return str(value)
    if is_dataclass(value):
        if hasattr(value, "to_manifest"):
            return _plain(value.to_manifest())
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise IdentityError(
                    f"identity mapping keys must be strings, got {type(key).__name__}"
                )
            out[key] = _plain(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset, bytes, bytearray)):
        raise IdentityError(
            f"unordered or binary {type(value).__name__} is not a canonical JSON value"
        )
    raise IdentityError(f"unsupported identity value {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize *value* to deterministic, whitespace-free UTF-8 JSON."""

    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash(domain: str, payload: Any) -> str:
    """Hash a payload in a named domain so unlike identities cannot collide."""

    if not isinstance(domain, str) or not domain.strip():
        raise IdentityError("identity domain must be a non-empty string")
    envelope = {
        "identity_schema": IDENTITY_SCHEMA,
        "domain": domain.strip(),
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


def _sha256(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise IdentityError(f"{label} must be a hexadecimal SHA-256 string")
    normalized = value.lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise IdentityError(f"{label} must be a 64-character hexadecimal SHA-256")
    return normalized


def _key(value: str, label: str) -> str:
    # All material-scan keys are full SHA-256 values too.
    return _sha256(value, label)


def task_key(
    *,
    resolved_physics: Mapping[str, Any],
    site_mm: Sequence[float],
    seeds: Sequence[int],
    events: int,
    macro_physics_sha256: str,
    lattice_sha256: str,
    executable_sha256: str,
    runtime_input_sha256: Mapping[str, str],
) -> str:
    """Return the raw-output identity of one Geant4 task.

    ``resolved_physics`` must be fully expanded: no mutable material aliases or
    defaults may remain.  ``runtime_input_sha256`` names dynamically loaded
    physics inputs such as G4CMP libraries and Geant4 data sets.  Output paths,
    trial labels, attempt IDs, resource limits, and timestamps are intentionally
    absent; they cannot change a *successfully completed* task's physics.
    """

    if not isinstance(resolved_physics, Mapping) or not resolved_physics:
        raise IdentityError("resolved_physics must be a non-empty mapping")
    try:
        xyz = tuple(float(v) for v in site_mm)
    except (TypeError, ValueError) as exc:
        raise IdentityError("site_mm must contain exactly three finite numbers") from exc
    if len(xyz) != 3 or any(not math.isfinite(v) for v in xyz):
        raise IdentityError("site_mm must contain exactly three finite numbers")
    if not isinstance(events, int) or isinstance(events, bool) or events <= 0:
        raise IdentityError("events must be a positive integer")
    if not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)):
        raise IdentityError("seeds must be a non-empty sequence of positive integers")
    seed_values = tuple(seeds)
    if not seed_values or any(
        not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0
        for seed in seed_values
    ):
        raise IdentityError("seeds must be a non-empty sequence of positive integers")
    if not isinstance(runtime_input_sha256, Mapping) or not runtime_input_sha256:
        raise IdentityError("runtime_input_sha256 must name at least one runtime input")
    runtime = {
        name: _sha256(digest, f"runtime_input_sha256[{name!r}]")
        for name, digest in runtime_input_sha256.items()
    }
    payload = {
        "resolved_physics": resolved_physics,
        "site_mm": xyz,
        "seeds": seed_values,
        "events": events,
        "macro_physics_sha256": _sha256(
            macro_physics_sha256, "macro_physics_sha256"
        ),
        "lattice_sha256": _sha256(lattice_sha256, "lattice_sha256"),
        "executable_sha256": _sha256(executable_sha256, "executable_sha256"),
        "runtime_input_sha256": runtime,
    }
    return canonical_hash("task/v1", payload)


def simulation_key(*, ordered_task_keys: Sequence[str]) -> str:
    """Return the identity of an ordered, fully declared candidate task set.

    Order is retained conservatively because task position is meaningful to the
    stored block vector.  Exact task-level reuse can still be performed with
    ``task_key``; a site alone is never enough for reuse because its seed or
    event count may differ.
    """

    if not isinstance(ordered_task_keys, Sequence) or isinstance(
        ordered_task_keys, (str, bytes)
    ):
        raise IdentityError("ordered_task_keys must be a non-empty sequence")
    keys = tuple(_key(value, "task key") for value in ordered_task_keys)
    if not keys:
        raise IdentityError("ordered_task_keys must be a non-empty sequence")
    return canonical_hash("simulation/v1", {"ordered_task_keys": keys})


def analysis_key(
    *,
    simulation: str,
    artifact_sha256: Sequence[str],
    design: str,
    scorer: Mapping[str, Any],
    objective: Mapping[str, Any],
) -> str:
    """Return the identity of a result calculated from concrete raw artifacts.

    Artifact hashes distinguish fresh attempts of the same simulation.  The
    scorer mapping should include its implementation hash and Python package
    versions; the objective mapping includes normalization, electrode weights,
    bootstrap sizes/seeds, and uncertainty rules.
    """

    if not isinstance(artifact_sha256, Sequence) or isinstance(
        artifact_sha256, (str, bytes)
    ):
        raise IdentityError("artifact_sha256 must be a non-empty ordered sequence")
    artifacts = tuple(_sha256(v, "artifact SHA-256") for v in artifact_sha256)
    if not artifacts:
        raise IdentityError("artifact_sha256 must be a non-empty ordered sequence")
    if not isinstance(scorer, Mapping) or not scorer:
        raise IdentityError("scorer must be a non-empty mapping")
    if not isinstance(objective, Mapping) or not objective:
        raise IdentityError("objective must be a non-empty mapping")
    return canonical_hash(
        "analysis/v1",
        {
            "simulation_key": _key(simulation, "simulation key"),
            "artifact_sha256": artifacts,
            "design_key": _key(design, "design key"),
            "scorer": scorer,
            "objective": objective,
        },
    )


def search_key(
    *,
    experiment_manifest: str,
    parameter_space: Mapping[str, Any],
    analysis_definition: Mapping[str, Any],
    optimizer: Mapping[str, Any],
    scheduler: Mapping[str, Any],
    censoring: Mapping[str, Any],
) -> str:
    """Return the identity of an adaptive proposal history.

    Scheduler and censoring policy are scientific provenance for asynchronous
    optimization: concurrency and completion order can change future proposals
    even though they do not change a completed Geant4 task.
    """

    sections = {
        "parameter_space": parameter_space,
        "analysis_definition": analysis_definition,
        "optimizer": optimizer,
        "scheduler": scheduler,
        "censoring": censoring,
    }
    empty = [name for name, value in sections.items() if not isinstance(value, Mapping) or not value]
    if empty:
        raise IdentityError(f"search identity requires non-empty mappings: {empty}")
    return canonical_hash(
        "search/v1",
        {"experiment_manifest_key": _key(experiment_manifest, "experiment manifest key"),
         **sections},
    )
