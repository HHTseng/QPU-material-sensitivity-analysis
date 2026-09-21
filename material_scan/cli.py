"""Single command-line entry point for the revised material scan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .agentic import (
    AgenticError,
    DEFAULT_CONTEXT as DEFAULT_AGENT_CONTEXT,
    DEFAULT_HOST as DEFAULT_OLLAMA_HOST,
    DEFAULT_MODEL as DEFAULT_OLLAMA_MODEL,
    preflight as agentic_preflight,
)
from .archive import ArchiveError, create_archive, recover_archive, verify_archive
from .config import (
    ConfigError,
    load_experiment_spec,
    load_parameter_catalog,
    resolve_experiment,
    write_resolved_experiment,
)
from .historical import (
    GitTree,
    HistoricalError,
    HistoricalDatabase,
    build_inventory,
    write_inventory_sqlite,
)
from .design import extend_recorded_design
from .store import SchemaError, Store, StoreError
from .simulation import SimulationError, run_resolved
from .optimization import (
    OptimizationError,
    evaluate_starting_points,
    run_search,
    select_starting_points,
)


DEFAULT_CATALOG = Path(__file__).with_name("parameters.yaml")


def _values_file(path: str | None) -> Mapping[str, Any]:
    if path is None:
        return {}
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        value = json.loads(text)
    elif source.suffix.lower() in (".yaml", ".yml"):
        import yaml

        value = yaml.safe_load(text)
    else:
        raise ConfigError("resolved values file must be JSON or YAML")
    if not isinstance(value, Mapping):
        raise ConfigError("resolved values file must contain one mapping")
    return value


def _load_spec(arguments: argparse.Namespace):
    catalog = load_parameter_catalog(arguments.catalog)
    return catalog, load_experiment_spec(arguments.experiment, catalog)


def _check(arguments: argparse.Namespace) -> int:
    catalog, spec = _load_spec(arguments)
    summary = {
        "experiment_id": spec.experiment_id,
        "spec_key": spec.spec_key,
        "catalog_key": catalog.catalog_key,
        "design_hash": spec.design.recorded_hash,
        "design_key": spec.design.design_key,
        "sites": spec.design.n_sites,
        "replicas": spec.replicas,
        "tasks": spec.design.n_sites * spec.replicas,
        "events_total": spec.event_counts["events_total"],
        "fixed_parameters": sum(item.mode == "fixed" for item in spec.parameters),
        "search_parameters": sum(item.mode == "search" for item in spec.parameters),
        "build_mode": spec.build["mode"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _freeze(arguments: argparse.Namespace) -> int:
    catalog, spec = _load_spec(arguments)
    resolved = resolve_experiment(catalog, spec, _values_file(arguments.values))
    destination = write_resolved_experiment(arguments.output, resolved)
    print(json.dumps({"path": str(destination), "manifest_key": resolved.manifest_key},
                     indent=2, sort_keys=True))
    return 0


def _historical_summary(arguments: argparse.Namespace) -> int:
    source = Path(arguments.database)
    before = (source.stat().st_mtime_ns, source.stat().st_size)
    with HistoricalDatabase(source) as database:
        report = dict(database.integrity_report())
        report["path"] = str(source.resolve())
        report["active_trials"] = len(database.active_trials())
        report["rows"] = {
            table: sum(1 for _ in database.rows(table))
            for table in database.tables
        }
        report["foreign_key_violations"] = len(report["foreign_key_violations"])
    after = (source.stat().st_mtime_ns, source.stat().st_size)
    if before != after:
        raise HistoricalError("historical database changed during a read-only command")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _inventory(arguments: argparse.Namespace) -> int:
    tree = GitTree(arguments.git_repository, arguments.revision) \
        if arguments.git_repository else None
    inventory = build_inventory(
        arguments.root,
        git_tree=tree,
        git_prefix=arguments.git_prefix or "",
    )
    destination = write_inventory_sqlite(inventory, arguments.output)
    print(json.dumps({"path": str(destination), "records": len(inventory.records),
                      "digest": inventory.digest}, indent=2, sort_keys=True))
    return 0


def _archive(arguments: argparse.Namespace) -> int:
    receipt = create_archive(
        arguments.root,
        arguments.member,
        arguments.output,
        compression=arguments.compression,
    )
    print(json.dumps({"path": str(receipt.path), "sha256": receipt.sha256,
                      "members": receipt.members, "bytes": receipt.bytes},
                     indent=2, sort_keys=True))
    return 0


def _verify_archive(arguments: argparse.Namespace) -> int:
    receipt = verify_archive(arguments.archive, expected_archive_sha256=arguments.sha256)
    print(json.dumps({"path": str(receipt.path), "sha256": receipt.sha256,
                      "members": receipt.members, "bytes": receipt.bytes},
                     indent=2, sort_keys=True))
    return 0


def _recover(arguments: argparse.Namespace) -> int:
    result = recover_archive(
        arguments.archive,
        arguments.output,
        expected_archive_sha256=arguments.sha256,
    )
    print(result)
    return 0


def _store_doctor(arguments: argparse.Namespace) -> int:
    with Store.open(arguments.store, read_only=True) as store:
        report = dict(store.integrity_report())
    report["foreign_key_violations"] = len(report["foreign_key_violations"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["integrity"] == "ok" and not report["foreign_key_violations"] else 1


def _recover_controller(arguments: argparse.Namespace) -> int:
    with Store.open(arguments.store) as store:
        cleared = store.clear_abandoned_controller(
            arguments.expected_owner,
            arguments.expected_token,
            arguments.inactive_seconds,
        )
    print(json.dumps({"cleared": cleared, "store": str(arguments.store)}, sort_keys=True))
    return 0


def _extend_design(arguments: argparse.Namespace) -> int:
    catalog, spec = _load_spec(arguments)
    source = json.loads(Path(arguments.recorded_design).read_text(encoding="utf-8"))
    if "stratified_design" in source:
        source = source["stratified_design"]
    counts = dict(source["stratum_counts"])
    for item in arguments.count:
        try:
            name, raw = item.split("=", 1)
            counts[name] = int(raw)
        except (ValueError, TypeError) as error:
            raise ConfigError(f"invalid --count {item!r}; expected NAME=INTEGER") from error
    design = extend_recorded_design(
        source,
        counts,
        electrode_x_mm=spec.physics["electrode_x_mm"],
        electrode_y_mm=spec.physics["electrode_y_mm"],
    )
    destination = Path(arguments.output)
    if destination.exists():
        raise ConfigError(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(design, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "path": str(destination.resolve()),
        "design_hash": design["design_hash"],
        "sites": len(design["sites_mm"]),
        "stratum_counts": design["stratum_counts"],
    }, indent=2, sort_keys=True))
    return 0


def _run(arguments: argparse.Namespace) -> int:
    result = run_resolved(
        arguments.resolved_experiment,
        arguments.output,
        workers=arguments.workers,
        timeout_s=arguments.timeout,
        reuse_from=arguments.reuse_from,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _select_starting_points(arguments: argparse.Namespace) -> int:
    result = select_starting_points(
        arguments.experiment,
        arguments.catalog,
        arguments.historical_database,
        arguments.reference,
        arguments.anchor,
        arguments.output,
        historical_count=arguments.historical_count,
        sobol_count=arguments.sobol_count,
        seed=arguments.seed,
    )
    print(json.dumps({
        "path": str(Path(arguments.output).resolve()),
        "points": len(result["points"]),
        "historical_points_available": result["historical_points_available"],
        "points_key": result["points_key"],
    }, indent=2, sort_keys=True))
    return 0


def _evaluate_starting_points(arguments: argparse.Namespace) -> int:
    result = evaluate_starting_points(
        arguments.experiment,
        arguments.catalog,
        arguments.points,
        arguments.output,
        parallel_points=arguments.parallel_points,
        workers_per_point=arguments.workers_per_point,
        limit=arguments.limit,
    )
    print(json.dumps({
        "path": str((Path(arguments.output) / "summary.json").resolve()),
        "complete": result["complete"],
        "requested": result["requested"],
    }, indent=2, sort_keys=True))
    return 0


def _search(arguments: argparse.Namespace) -> int:
    agent_options = None
    if arguments.method == "agentic":
        if not arguments.ollama_model_digest:
            raise AgenticError(
                "agentic search requires --ollama-model-digest to pin model content"
            )
        agent_options = {
            "host": arguments.ollama_host,
            "model": arguments.ollama_model,
            "expected_model_digest": arguments.ollama_model_digest,
            "timeout": arguments.agent_timeout,
            "temperature": arguments.agent_temperature,
            "num_ctx": arguments.agent_context,
            "pool_size": arguments.agent_pool_size,
            "retries": arguments.agent_retries,
            "allow_model_fallback": arguments.allow_model_fallback,
        }
    result = run_search(
        arguments.experiment,
        arguments.catalog,
        arguments.initial_results,
        arguments.output,
        method=arguments.method,
        seed=arguments.seed,
        steps=arguments.steps,
        workers=arguments.workers,
        max_attempts=arguments.max_attempts,
        task_timeout_s=arguments.task_timeout,
        agent_options=agent_options,
    )
    print(json.dumps({
        "path": str((Path(arguments.output) / "search.json").resolve()),
        "complete_steps": result["complete_steps"],
        "requested_steps": result["requested_steps"],
        "adaptive_best": result.get("adaptive_best"),
    }, indent=2, sort_keys=True))
    return 0


def _agentic_preflight(arguments: argparse.Namespace) -> int:
    if (arguments.warmup or arguments.require_gpu) and not arguments.expected_digest:
        raise AgenticError(
            "warm/GPU agentic preflight requires --expected-digest"
        )
    result = agentic_preflight(
        host=arguments.ollama_host,
        model=arguments.ollama_model,
        expected_digest=arguments.expected_digest,
        num_ctx=arguments.context,
        timeout=arguments.timeout,
        warmup=arguments.warmup,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if arguments.require_gpu and not result["ready"]:
        raise AgenticError(
            "the exact model is not 100% GPU-resident at the requested context"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="material-scan")
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text, action in (
        ("check", "validate an experiment definition", _check),
        ("freeze", "write one immutable resolved experiment", _freeze),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("experiment")
        command.add_argument("--catalog", default=str(DEFAULT_CATALOG))
        if name == "freeze":
            command.add_argument("--values", help="JSON/YAML candidate values; no scalar overrides")
            command.add_argument("--output", required=True)
        command.set_defaults(action=action)

    command = commands.add_parser("history-summary", help="inspect an old database read-only")
    command.add_argument("database")
    command.set_defaults(action=_historical_summary)

    command = commands.add_parser("inventory", help="write a checksum inventory to a new SQLite file")
    command.add_argument("root")
    command.add_argument("--output", required=True)
    command.add_argument("--git-repository")
    command.add_argument("--git-prefix")
    command.add_argument("--revision", default="HEAD")
    command.set_defaults(action=_inventory)

    command = commands.add_parser("archive", help="explicitly create a verified stopped-data bundle")
    command.add_argument("root")
    command.add_argument("--member", action="append", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--compression", choices=("none", "gzip"), default="gzip")
    command.set_defaults(action=_archive)

    command = commands.add_parser("verify-archive", help="verify container and every member")
    command.add_argument("archive")
    command.add_argument("--sha256")
    command.set_defaults(action=_verify_archive)

    command = commands.add_parser("recover", help="verify and atomically recover an archive")
    command.add_argument("archive")
    command.add_argument("--output", required=True)
    command.add_argument("--sha256")
    command.set_defaults(action=_recover)

    command = commands.add_parser("store-doctor", help="check a revised database read-only")
    command.add_argument("store")
    command.set_defaults(action=_store_doctor)

    command = commands.add_parser(
        "recover-controller",
        help="clear one exact controller whose heartbeat has stopped",
    )
    command.add_argument("store")
    command.add_argument("--expected-owner", required=True)
    command.add_argument("--expected-token", required=True)
    command.add_argument("--inactive-seconds", type=float, default=600.0)
    command.set_defaults(action=_recover_controller)

    command = commands.add_parser("extend-design", help="extend selected strata without moving old sites")
    command.add_argument("recorded_design")
    command.add_argument("--experiment", required=True)
    command.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    command.add_argument("--count", action="append", required=True, metavar="NAME=INTEGER")
    command.add_argument("--output", required=True)
    command.set_defaults(action=_extend_design)

    command = commands.add_parser("run", help="run one fully resolved, verified experiment")
    command.add_argument("resolved_experiment")
    command.add_argument("--output", required=True)
    command.add_argument("--workers", type=int, default=1)
    command.add_argument("--timeout", type=float, default=0.0,
                         help="absolute seconds per task; 0 leaves the physical bounce limit in control")
    command.add_argument(
        "--reuse-from", action="append", default=[], metavar="COMPLETED_RESULT_DIRECTORY",
        help="reuse exact complete task identities after checksum, carrier, and score checks",
    )
    command.set_defaults(action=_run)

    command = commands.add_parser(
        "select-starting-points",
        help="select named anchors, diverse earlier vectors, and fresh Sobol vectors",
    )
    command.add_argument("experiment")
    command.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    command.add_argument("--historical-database", required=True)
    command.add_argument("--reference", required=True)
    command.add_argument("--anchor", required=True)
    command.add_argument("--historical-count", type=int, default=14)
    command.add_argument("--sobol-count", type=int, default=16)
    command.add_argument("--seed", type=int, default=20260921)
    command.add_argument("--output", required=True)
    command.set_defaults(action=_select_starting_points)

    command = commands.add_parser(
        "evaluate-starting-points", help="evaluate the common optimizer starting points"
    )
    command.add_argument("experiment")
    command.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    command.add_argument("--points", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--parallel-points", type=int, default=1)
    command.add_argument("--workers-per-point", type=int, default=1)
    command.add_argument("--limit", type=int, help="evaluate only this many leading points")
    command.set_defaults(action=_evaluate_starting_points)

    command = commands.add_parser(
        "search", help="run one restartable optimizer comparison"
    )
    command.add_argument("experiment")
    command.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    command.add_argument("--initial-results", required=True)
    command.add_argument(
        "--method", choices=("bo_gp", "cmaes", "sobol", "random", "agentic"),
        required=True,
    )
    command.add_argument("--seed", type=int, required=True)
    command.add_argument("--steps", type=int, default=120)
    command.add_argument("--workers", type=int, default=1)
    command.add_argument("--max-attempts", type=int)
    command.add_argument(
        "--task-timeout", type=float, default=0.0,
        help="absolute seconds per simulation task; 0 uses the simulator default",
    )
    command.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST)
    command.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    command.add_argument(
        "--ollama-model-digest",
        help="required exact Ollama content digest for an agentic search",
    )
    command.add_argument("--agent-timeout", type=float, default=900.0)
    command.add_argument("--agent-temperature", type=float, default=0.0)
    command.add_argument("--agent-context", type=int, default=DEFAULT_AGENT_CONTEXT)
    command.add_argument("--agent-pool-size", type=int, default=12)
    command.add_argument("--agent-retries", type=int, default=2)
    command.add_argument(
        "--allow-model-fallback", action="store_true",
        help="label and use ordinary GP-EI if the model cannot provide a valid pool",
    )
    command.add_argument("--output", required=True)
    command.set_defaults(action=_search)

    command = commands.add_parser(
        "agentic-preflight", help="check exact Ollama model identity and GPU residency"
    )
    command.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST)
    command.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    command.add_argument("--expected-digest")
    command.add_argument("--context", type=int, default=DEFAULT_AGENT_CONTEXT)
    command.add_argument("--timeout", type=float, default=30.0)
    command.add_argument("--warmup", action="store_true")
    command.add_argument("--require-gpu", action="store_true")
    command.set_defaults(action=_agentic_preflight)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.action(arguments))
    except (
        ArchiveError, ConfigError, HistoricalError, SchemaError, StoreError,
        AgenticError, SimulationError, OptimizationError, OSError, ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
