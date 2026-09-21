"""One recorded ask/tell interface for the validated historical optimizers.

The numerical implementations are intentionally not rewritten during the
structural change.  This adapter records the complete operation transcript, so
an interrupted optimizer is restored by deterministic replay and every replay
checks that it proposes the same points.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class SearchReplayError(RuntimeError):
    """The saved optimizer history cannot be reproduced exactly."""


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _historical_module():
    historical_dir = Path(__file__).resolve().parents[1] / "legacy"
    path = str(historical_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
    import stage4_optimizers  # type: ignore

    return stage4_optimizers


def implementation_hash(method: str | None = None) -> str:
    """Hash every source file used to turn an experiment into proposals."""

    digest = hashlib.sha256()
    paths = [
        Path(__file__).resolve(),
        Path(__file__).with_name("space.py").resolve(),
        Path(__file__).with_name("optimization.py").resolve(),
        Path(_historical_module().__file__).resolve(),
    ]
    if method == "agentic":
        paths.extend([
            Path(__file__).with_name("agentic.py").resolve(),
            Path(__file__).with_name("agent_knowledge.md").resolve(),
            Path(__file__).with_name("parameters.yaml").resolve(),
            Path(__file__).with_name("sic_phonon_constants.yaml").resolve(),
            Path(__file__).resolve().parents[1] / "legacy/stage4_material_pool.yaml",
        ])
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _space_manifest(space: Any) -> Any:
    if space is not None and hasattr(space, "to_manifest"):
        return _plain(space.to_manifest())
    actual = space or _historical_module().DEFAULT_SPACE
    return {
        "compatibility_space": type(actual).__name__,
        "variables": [
            {
                "name": variable.name,
                "low": variable.low,
                "high": variable.high,
                "default": variable.baseline,
                "scale": variable.scale,
            }
            for variable in actual.variables
        ],
    }


@dataclass(frozen=True)
class Observation:
    value: float
    se: float | None = None
    transform: str = "log"
    floor: float = 1e-12
    cost_seconds: float | None = None

    def surrogate_y(self) -> float:
        if self.transform == "identity":
            return float(self.value)
        return float(math.log(max(float(self.value), self.floor)))

    def surrogate_sigma(self) -> float | None:
        if self.se is None or not math.isfinite(self.se):
            return None
        if self.transform == "identity":
            return float(self.se)
        return float(self.se / max(float(self.value), self.floor))


class SearchController:
    """Optimizer plus an append-only, JSON-safe operation transcript."""

    SCHEMA = "material-scan-search-transcript-1"

    def __init__(
        self,
        method: str,
        *,
        seed: int,
        space: Any = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self.method = str(method)
        self.seed = int(seed)
        self.options = dict(options or {})
        self.space = space
        if self.method == "agentic":
            from .agentic import AgenticOptimizer

            self.optimizer = AgenticOptimizer(
                space=self.space, seed=self.seed, **self.options
            )
        else:
            self.optimizer = _historical_module().create(
                self.method, space=self.space, seed=self.seed, **self.options
            )
        self.events: list[dict[str, Any]] = []

    @property
    def scheduler_policy(self) -> str:
        return "synchronous-generation" if self.method == "cmaes" else "asynchronous"

    def ask(self, count: int = 1) -> list[dict[str, Any]]:
        if count <= 0:
            raise ValueError("ask count must be positive")
        points = [_plain(point) for point in self.optimizer.ask(count)]
        provenance = [_plain(self.optimizer.provenance_of(point)) for point in points]
        self.events.append(
            {"kind": "ask", "count": int(count), "points": points, "provenance": provenance}
        )
        return points

    def tell(
        self,
        point: Mapping[str, Any],
        value: float,
        *,
        se: float | None = None,
        cost_seconds: float | None = None,
        transform: str = "log",
        floor: float = 1e-12,
    ) -> None:
        observation = Observation(
            value=float(value),
            se=se,
            cost_seconds=cost_seconds,
            transform=transform,
            floor=float(floor),
        )
        self.optimizer.tell(dict(point), observation)
        self.events.append(
            {
                "kind": "tell",
                "point": _plain(point),
                "value": observation.value,
                "se": observation.se,
                "cost_seconds": observation.cost_seconds,
                "transform": observation.transform,
                "floor": observation.floor,
            }
        )

    def reject(self, point: Mapping[str, Any], reason: str) -> None:
        self.optimizer.tell_rejected(dict(point), str(reason))
        self.events.append(
            {"kind": "reject", "point": _plain(point), "reason": str(reason)}
        )

    def checkpoint(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "method": self.method,
            "seed": self.seed,
            "options": _plain(self.options),
            "scheduler_policy": self.scheduler_policy,
            "implementation_sha256": implementation_hash(self.method),
            "space": _space_manifest(self.space),
            "events": _plain(self.events),
            "state_summary": _plain(self.optimizer.state()),
        }

    @classmethod
    def restore(cls, record: Mapping[str, Any], *, space: Any = None) -> "SearchController":
        if record.get("schema") != cls.SCHEMA:
            raise SearchReplayError(f"unsupported transcript schema: {record.get('schema')!r}")
        restored = cls(
            str(record["method"]),
            seed=int(record["seed"]),
            space=space,
            options=dict(record.get("options") or {}),
        )
        current = restored.checkpoint()
        current_hash = current["implementation_sha256"]
        if record.get("implementation_sha256") != current_hash:
            raise SearchReplayError("optimizer implementation hash changed")
        if _canonical(record.get("space")) != _canonical(current["space"]):
            raise SearchReplayError("optimizer space changed")

        for event in record.get("events", []):
            kind = event.get("kind")
            if kind == "ask":
                if hasattr(restored.optimizer, "replay_ask"):
                    produced = restored.optimizer.replay_ask(
                        event["points"], event.get("provenance", [])
                    )
                    restored.events.append(_plain(event))
                else:
                    produced = restored.ask(int(event["count"]))
                if _canonical(produced) != _canonical(event["points"]):
                    raise SearchReplayError("optimizer proposal replay diverged")
            elif kind == "tell":
                restored.tell(
                    event["point"],
                    float(event["value"]),
                    se=event.get("se"),
                    cost_seconds=event.get("cost_seconds"),
                    transform=str(event.get("transform", "log")),
                    floor=float(event.get("floor", 1e-12)),
                )
            elif kind == "reject":
                restored.reject(event["point"], str(event["reason"]))
            else:
                raise SearchReplayError(f"unknown transcript event: {kind!r}")
        if _canonical(restored.optimizer.state()) != _canonical(record.get("state_summary")):
            raise SearchReplayError("optimizer state summary differs after replay")
        return restored


def available() -> list[str]:
    """Names exposed by the pinned compatibility implementation."""

    return sorted({*_historical_module().available(), "agentic"})
