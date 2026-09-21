"""Prepare common starting points, evaluate them, and run restartable searches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.stats import qmc

from .config import (
    ConfigError,
    ExperimentSpec,
    ParameterCatalog,
    load_experiment_spec,
    load_parameter_catalog,
    load_resolved_experiment,
    resolve_experiment,
    write_resolved_experiment,
)
from .identity import canonical_hash, canonical_json
from .search import SearchController
from .simulation import ROOT, SimulationError, run_resolved
from .space import ExperimentSpace, exact_physics_check, values_from_resolved


class OptimizationError(RuntimeError):
    """A search input, saved state, or candidate result is inconsistent."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _search_values(space: ExperimentSpace, source: Mapping[str, Any]) -> dict[str, float]:
    return {name: float(source[name]) for name in space.names}


def _values_from_file(path: str | Path, space: ExperimentSpace) -> dict[str, float]:
    source = Path(path)
    try:
        resolved = load_resolved_experiment(source)
    except ConfigError:
        document = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise OptimizationError(f"point file is not a mapping: {source}")
        values = document.get("values", document)
        if not isinstance(values, Mapping):
            raise OptimizationError(f"point file has no value mapping: {source}")
        return _search_values(space, values)
    return _search_values(space, values_from_resolved(resolved))


def _validate_point(
    catalog: ParameterCatalog,
    spec: ExperimentSpec,
    space: ExperimentSpace,
    point: Mapping[str, Any],
) -> dict[str, float]:
    values = _search_values(space, space.complete(point))
    ok, reasons = space.in_bounds(values)
    if not ok:
        raise OptimizationError("; ".join(reasons))
    resolved = resolve_experiment(catalog, spec, values)
    exact_physics_check(space.complete(values), resolved)
    return values


def _historical_points(database: str | Path, space: ExperimentSpace) -> list[dict[str, Any]]:
    path = Path(database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise OptimizationError(f"historical database failed integrity check: {path}")
        rows = connection.execute(
            "SELECT trial_id,candidate FROM trials WHERE status='success' "
            "AND optimizer IS NOT NULL ORDER BY trial_id"
        ).fetchall()
    finally:
        connection.close()
    found: dict[str, dict[str, Any]] = {}
    for trial_id, raw in rows:
        try:
            source = json.loads(raw)
            values = _search_values(space, source)
            ok, _ = space.in_bounds(values)
            if not ok:
                continue
        except (KeyError, TypeError, ValueError, ConfigError):
            continue
        key = canonical_json(values)
        found.setdefault(key, {"trial_id": str(trial_id), "values": values})
    return list(found.values())


def _maximin(
    pool: Sequence[Mapping[str, Any]],
    space: ExperimentSpace,
    selected: Sequence[Mapping[str, Any]],
    count: int,
) -> list[Mapping[str, Any]]:
    remaining = list(pool)
    chosen: list[Mapping[str, Any]] = []
    reference = [space.to_unit(item["values"]) for item in selected]
    while remaining and len(chosen) < count:
        scores = []
        for item in remaining:
            unit = space.to_unit(item["values"])
            distances = [float(np.linalg.norm(unit - other)) for other in reference]
            scores.append(min(distances) if distances else float("inf"))
        index = int(np.argmax(scores))
        item = remaining.pop(index)
        chosen.append(item)
        reference.append(space.to_unit(item["values"]))
    if len(chosen) != count:
        raise OptimizationError(
            f"only {len(chosen)} suitable historical points exist; requested {count}"
        )
    return chosen


def select_starting_points(
    experiment: str | Path,
    catalog_path: str | Path,
    historical_database: str | Path,
    reference_file: str | Path,
    anchor_file: str | Path,
    output: str | Path,
    *,
    historical_count: int = 14,
    sobol_count: int = 16,
    seed: int = 20260921,
) -> Mapping[str, Any]:
    """Select two named anchors, diverse earlier vectors, and fresh Sobol vectors."""

    catalog = load_parameter_catalog(catalog_path)
    spec = load_experiment_spec(experiment, catalog)
    space = ExperimentSpace(spec)
    reference = _validate_point(
        catalog, spec, space, _values_from_file(reference_file, space)
    )
    anchor = _validate_point(catalog, spec, space, _values_from_file(anchor_file, space))
    points: list[dict[str, Any]] = [
        {"name": "reference", "source": str(Path(reference_file)), "values": reference},
        {"name": "compatible-anchor", "source": str(Path(anchor_file)), "values": anchor},
    ]

    valid_history = []
    for item in _historical_points(historical_database, space):
        try:
            values = _validate_point(catalog, spec, space, item["values"])
        except (ConfigError, OptimizationError, ValueError):
            continue
        valid_history.append({**item, "values": values})
    for index, item in enumerate(_maximin(valid_history, space, points, historical_count), 1):
        points.append({
            "name": f"historical-{index:02d}",
            "source": f"historical trial {item['trial_id']}; old objective value not reused",
            "values": item["values"],
        })

    engine = qmc.Sobol(d=space.n_cont, scramble=True, seed=int(seed))
    existing = [space.to_unit(item["values"]) for item in points]
    fresh = 0
    attempts = 0
    while fresh < sobol_count and attempts < 10000:
        attempts += 1
        values = space.from_unit(engine.random(1)[0])
        try:
            values = _validate_point(catalog, spec, space, values)
        except (ConfigError, OptimizationError, ValueError):
            continue
        unit = space.to_unit(values)
        if any(float(np.max(np.abs(unit - old))) < 1e-8 for old in existing):
            continue
        fresh += 1
        existing.append(unit)
        points.append({
            "name": f"sobol-{fresh:02d}",
            "source": f"scrambled Sobol seed {seed}",
            "values": values,
        })
    if fresh != sobol_count:
        raise OptimizationError(f"could create only {fresh}/{sobol_count} Sobol points")

    record = {
        "schema": "material-scan-starting-points-1",
        "experiment_spec_key": spec.spec_key,
        "search_variables": list(space.names),
        "historical_database": str(Path(historical_database)),
        "historical_points_available": len(valid_history),
        "sobol_seed": int(seed),
        "points": points,
    }
    record["points_key"] = canonical_hash("starting-points/v1", record)
    destination = Path(output)
    if destination.exists():
        existing_record = json.loads(destination.read_text(encoding="utf-8"))
        if canonical_json(existing_record) != canonical_json(record):
            raise OptimizationError(f"refusing to replace different points: {destination}")
    else:
        _write_json(destination, record)
    return record


def _load_points(path: str | Path) -> Mapping[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("schema") != "material-scan-starting-points-1":
        raise OptimizationError("unsupported starting-point file")
    supplied = record.get("points_key")
    body = {key: value for key, value in record.items() if key != "points_key"}
    expected = canonical_hash("starting-points/v1", body)
    if supplied != expected:
        raise OptimizationError("starting-point checksum differs")
    return record


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "point"


def _evaluate_one(
    catalog: ParameterCatalog,
    spec: ExperimentSpec,
    space: ExperimentSpace,
    point: Mapping[str, Any],
    directory: Path,
    workers: int,
    reuse_from: Sequence[str | Path] = (),
    timeout_s: float = 0.0,
) -> Mapping[str, Any]:
    values = _validate_point(catalog, spec, space, point["values"])
    point_key = canonical_hash("search-point/v1", values)
    candidate_directory = directory / f"{_safe_name(str(point['name']))}-{point_key[:10]}"
    candidate_directory.mkdir(parents=True, exist_ok=True)
    resolved = resolve_experiment(catalog, spec, values)
    resolved_path = write_resolved_experiment(candidate_directory / "resolved.json", resolved)
    started = time.monotonic()
    result = run_resolved(
        resolved_path,
        candidate_directory,
        workers=workers,
        timeout_s=float(timeout_s),
        reuse_from=reuse_from,
    )
    elapsed_seconds = time.monotonic() - started
    return {
        "name": str(point["name"]),
        "source": point.get("source"),
        "point_key": point_key,
        "values": values,
        "directory": str(candidate_directory.relative_to(ROOT)),
        "elapsed_seconds": elapsed_seconds,
        "result": dict(result),
    }


def evaluate_starting_points(
    experiment: str | Path,
    catalog_path: str | Path,
    points_file: str | Path,
    output: str | Path,
    *,
    parallel_points: int = 1,
    workers_per_point: int = 1,
    limit: int | None = None,
) -> Mapping[str, Any]:
    """Evaluate all common starting points and resume from verified candidate outputs."""

    if parallel_points <= 0 or workers_per_point <= 0:
        raise OptimizationError("worker counts must be positive")
    catalog = load_parameter_catalog(catalog_path)
    spec = load_experiment_spec(experiment, catalog)
    space = ExperimentSpace(spec)
    points_record = _load_points(points_file)
    if points_record["experiment_spec_key"] != spec.spec_key:
        raise OptimizationError("starting points belong to a different experiment")
    directory = Path(output).resolve()
    if directory != ROOT and ROOT not in directory.parents:
        raise OptimizationError(f"output must be inside the repository: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / "summary.json"
    completed: dict[str, Mapping[str, Any]] = {}
    if summary_path.exists():
        prior = json.loads(summary_path.read_text(encoding="utf-8"))
        if prior.get("experiment_spec_key") != spec.spec_key \
                or prior.get("points_key") != points_record["points_key"]:
            raise OptimizationError("existing starting-point summary belongs to another study")
        completed = {item["point_key"]: item for item in prior.get("evaluations", [])}

    def task(point: Mapping[str, Any]) -> Mapping[str, Any]:
        key = canonical_hash("search-point/v1", _search_values(space, point["values"]))
        if key in completed:
            return completed[key]
        return _evaluate_one(
            catalog, spec, space, point, directory / "points", workers_per_point
        )

    points = list(points_record["points"])
    scheduled = points if limit is None else points[:max(0, int(limit))]
    with ThreadPoolExecutor(max_workers=parallel_points) as pool:
        futures = {pool.submit(task, point): point for point in scheduled}
        for future in as_completed(futures):
            item = future.result()
            completed[item["point_key"]] = item
            ordered = [
                completed[canonical_hash(
                    "search-point/v1", _search_values(space, point["values"])
                )]
                for point in points
                if canonical_hash(
                    "search-point/v1", _search_values(space, point["values"])
                ) in completed
            ]
            _write_json(summary_path, {
                "schema": "material-scan-starting-results-1",
                "experiment_spec_key": spec.spec_key,
                "points_key": points_record["points_key"],
                "requested": len(points),
                "complete": len(ordered),
                "evaluations": ordered,
            })
            print(f"starting points: {len(ordered)}/{len(points)} complete", flush=True)
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _load_initial_results(path: str | Path, spec: ExperimentSpec) -> Mapping[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("schema") != "material-scan-starting-results-1":
        raise OptimizationError("unsupported starting-result file")
    if record.get("experiment_spec_key") != spec.spec_key:
        raise OptimizationError("starting results belong to a different experiment")
    if record.get("complete") != record.get("requested") or not record.get("evaluations"):
        raise OptimizationError("starting-point evaluations are incomplete")
    return record


def run_search(
    experiment: str | Path,
    catalog_path: str | Path,
    initial_results: str | Path,
    output: str | Path,
    *,
    method: str,
    seed: int,
    steps: int,
    workers: int,
    max_attempts: int | None = None,
    task_timeout_s: float = 0.0,
    agent_options: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Run one optimizer seed, saving an exact replay record after every result."""

    if method not in ("bo_gp", "cmaes", "sobol", "random", "agentic"):
        raise OptimizationError("method must be bo_gp, cmaes, sobol, random, or agentic")
    if steps <= 0 or workers <= 0 or task_timeout_s < 0:
        raise OptimizationError("steps/workers must be positive and task timeout non-negative")
    catalog = load_parameter_catalog(catalog_path)
    spec = load_experiment_spec(experiment, catalog)
    space = ExperimentSpace(spec)
    initial = _load_initial_results(initial_results, spec)
    directory = Path(output).resolve()
    if directory != ROOT and ROOT not in directory.parents:
        raise OptimizationError(f"output must be inside the repository: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "search.json"

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("experiment_spec_key") != spec.spec_key \
                or state.get("method") != method or int(state.get("seed")) != int(seed):
            raise OptimizationError("existing search state belongs to another run")
        if float(state.get("task_timeout_s", 0.0)) != float(task_timeout_s):
            raise OptimizationError("task timeout differs from the saved run")
        if method == "agentic" and agent_options is not None:
            saved = dict(state["transcript"].get("options") or {})
            for key, value in agent_options.items():
                if saved.get(key) != value:
                    raise OptimizationError(
                        f"agent option {key!r} differs from the saved run"
                    )
        controller = SearchController.restore(state["transcript"], space=space)
    else:
        initial_evaluations = list(initial["evaluations"])
        best_initial = min(initial_evaluations, key=lambda item: item["result"]["value"])
        if method in ("bo_gp", "agentic"):
            options: dict[str, Any] = {
                "n_init": 0,
                "n_min_fit": len(initial_evaluations),
                "refit_every": 5,
                "n_candidates": 16384,
                "n_polish": 16,
                "xi": 0.0,
            }
            if method == "agentic":
                options.update(dict(agent_options or {}))
                options["trace_dir"] = str(directory / "agent-traces")
        elif method == "cmaes":
            options = {
                "popsize": 12,
                "sigma0": 0.22,
                "mean0": space.to_unit(best_initial["values"]).tolist(),
                "reeval_every": 0,
                "stagnation": 20,
                "synchronous": True,
            }
        else:
            options = {}
        controller = SearchController(method, seed=int(seed), space=space, options=options)
        for item in initial_evaluations:
            controller.tell(
                item["values"],
                float(item["result"]["value"]),
                se=float(item["result"]["standard_error"]),
                cost_seconds=item.get("elapsed_seconds"),
            )
        state = {
            "schema": "material-scan-search-run-1",
            "experiment_spec_key": spec.spec_key,
            "initial_points_key": initial["points_key"],
            "method": method,
            "seed": int(seed),
            "task_timeout_s": float(task_timeout_s),
            "requested_steps": int(steps),
            "attempts": 0,
            "complete_steps": 0,
            "initial_best": {
                "name": best_initial["name"],
                "point_key": best_initial["point_key"],
                "value": best_initial["result"]["value"],
                "standard_error": best_initial["result"]["standard_error"],
            },
            "evaluations": [],
            "transcript": controller.checkpoint(),
        }
        _write_json(state_path, state)

    if int(state["requested_steps"]) != int(steps):
        raise OptimizationError("requested step count differs from the saved run")
    allowed_attempts = int(max_attempts if max_attempts is not None else steps)
    saved_requested_attempts = state.get("requested_attempts")
    if saved_requested_attempts is not None and int(saved_requested_attempts) != allowed_attempts:
        raise OptimizationError("requested attempt count differs from the saved run")
    state.setdefault("requested_attempts", allowed_attempts)
    while int(state["complete_steps"]) < steps:
        pending_indexes = [
            index for index, saved in enumerate(state["evaluations"])
            if saved.get("status") == "pending"
        ]
        if len(pending_indexes) > 1:
            raise OptimizationError("serial search state contains multiple pending candidates")
        if pending_indexes:
            evaluation_index = pending_indexes[0]
            item = dict(state["evaluations"][evaluation_index])
            pending_points = list(controller.optimizer.pending)
            if len(pending_points) != 1:
                raise OptimizationError(
                    "saved pending evaluation disagrees with optimizer transcript"
                )
            point = pending_points[0]
            if _search_values(space, point) != _search_values(space, item["values"]):
                raise OptimizationError("saved pending point differs from optimizer transcript")
            provenance = dict(item["source"])
        else:
            if int(state["attempts"]) >= allowed_attempts:
                state["stop_reason"] = "requested attempt count reached before requested completions"
                state["transcript"] = controller.checkpoint()
                _write_json(state_path, state)
                break
            proposals = controller.ask(1)
            if not proposals:
                raise OptimizationError(
                    "optimizer returned no proposal while no task was pending"
                )
            point = proposals[0]
            state["attempts"] = int(state["attempts"]) + 1
            attempt = int(state["attempts"])
            provenance = controller.optimizer.provenance_of(point)
            item = {
                "name": f"step-{attempt:04d}",
                "source": provenance,
                "values": _search_values(space, point),
                "status": "pending",
            }
            state["evaluations"].append(item)
            evaluation_index = len(state["evaluations"]) - 1
            # Persist the exact LLM/optimizer decision before entering a long
            # simulation. A killed process resumes this point without asking
            # Ollama (or any other optimizer) to make the decision again.
            state["transcript"] = controller.checkpoint()
            _write_json(state_path, state)
        attempt_started = time.monotonic()
        try:
            evaluation = _evaluate_one(
                catalog,
                spec,
                space,
                item,
                directory / "points",
                workers,
                timeout_s=task_timeout_s,
            )
        except (ConfigError, OptimizationError, SimulationError, ValueError) as error:
            elapsed_seconds = time.monotonic() - attempt_started
            reason = (
                f"{type(error).__name__}: {error}; "
                f"elapsed_seconds={elapsed_seconds:.6f}"
            )
            controller.reject(point, reason)
            state["evaluations"][evaluation_index] = {
                "name": item["name"],
                "values": item["values"],
                "source": provenance,
                "status": "failed",
                "reason": reason,
                "elapsed_seconds": elapsed_seconds,
            }
        else:
            controller.tell(
                point,
                float(evaluation["result"]["value"]),
                se=float(evaluation["result"]["standard_error"]),
                cost_seconds=float(evaluation["elapsed_seconds"]),
            )
            evaluation = dict(evaluation)
            evaluation["status"] = "complete"
            evaluation["source"] = controller.optimizer.provenance_of(point)
            state["evaluations"][evaluation_index] = evaluation
            state["complete_steps"] = int(state["complete_steps"]) + 1
        state["transcript"] = controller.checkpoint()
        complete = [item for item in state["evaluations"] if item["status"] == "complete"]
        if complete:
            best = min(complete, key=lambda entry: entry["result"]["value"])
            state["adaptive_best"] = {
                "name": best["name"],
                "point_key": best["point_key"],
                "value": best["result"]["value"],
                "standard_error": best["result"]["standard_error"],
            }
        _write_json(state_path, state)
        print(
            f"{method} seed {seed}: {state['complete_steps']}/{steps} complete; "
            f"attempts {state['attempts']}",
            flush=True,
        )
    return state
