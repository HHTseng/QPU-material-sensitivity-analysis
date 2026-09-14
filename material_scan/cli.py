"""Single command-line entry point for the revised material scan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

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
from .store import SchemaError, Store


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


def _run_disabled(_arguments: argparse.Namespace) -> int:
    raise ConfigError(
        "production run is deliberately disabled in the compatibility slice; "
        "finish macro/lattice parity and the disposable live smoke first"
    )


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

    command = commands.add_parser("run", help="reserved until production cutover gates pass")
    command.add_argument("resolved_experiment")
    command.set_defaults(action=_run_disabled)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.action(arguments))
    except (ArchiveError, ConfigError, HistoricalError, SchemaError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
