"""Compare nested site and replica subsets of completed candidate simulations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from material_scan.analysis import Block, StratifiedDesign, stratified_estimate
from material_scan.config import load_resolved_experiment


def _design(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("stratified_design", value)


def _blocks(directory: Path) -> dict[tuple[int, int], Block]:
    found: dict[tuple[int, int], Block] = {}
    for path in (directory / "scores").glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        block = Block(
            position=int(value["position"]),
            replica=int(value["replica"]),
            events=int(value["events"]),
            total_qps=float(value["total_qps"]),
            per_electrode_qps=tuple(float(x) for x in value["per_electrode_qps"]),
            n_hits=int(value["n_hits"]),
        )
        found[(block.position, block.replica)] = block
    return found


def _estimate(directory: Path, target: Mapping[str, Any], replicas: int):
    experiment = load_resolved_experiment(directory / "resolved.json")
    full = experiment.sampling.design.to_manifest()
    position = {tuple(site): index for index, site in enumerate(full["sites_mm"])}
    source = _blocks(directory)
    selected = []
    for target_position, site in enumerate(target["sites_mm"]):
        full_position = position[tuple(site)]
        for replica in range(replicas):
            old = source[(full_position, replica)]
            selected.append(Block(
                position=target_position,
                replica=replica,
                events=old.events,
                total_qps=old.total_qps,
                per_electrode_qps=old.per_electrode_qps,
                n_hits=old.n_hits,
            ))
    design = StratifiedDesign.from_values(
        position_strata=target["stratum"],
        stratum_weights=target["stratum_weights"],
        replica_ids=range(replicas),
        events_per_task=experiment.sampling.events_per_task,
        gun_energy_eV=float(experiment.event_counts["gun_energy_eV"]),
        electrode_weights=target["electrode_weights"],
        design_hash=target["design_hash"],
        injection_law=target["injection_law"],
    )
    result = stratified_estimate(selected, design)
    return {
        "value": result.value,
        "standard_error": result.standard_error,
        "relative_standard_error": result.relative_standard_error,
        "maximum_site_leverage": result.maximum_site_leverage,
        "zero_hit_fraction": sum((block.n_hits or 0) == 0 for block in selected) / len(selected),
    }


def build_report(
    candidates: Mapping[str, Path], design_paths: Mapping[int, Path]
) -> Mapping[str, Any]:
    designs = {sites: _design(path) for sites, path in design_paths.items()}
    levels = [
        (128, 2), (128, 4), (128, 8),
        (256, 2), (256, 4), (256, 8),
        (512, 2), (512, 8),
    ]
    results: dict[str, Any] = {}
    for name, directory in candidates.items():
        levels_for_candidate = {
            f"{sites}-sites-{replicas}-replicas": _estimate(
                directory, designs[sites], replicas
            )
            for sites, replicas in levels
        }
        full = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        levels_for_candidate[f"{full['sites']}-sites-{full['replicas']}-replicas"] = {
            "value": full["value"],
            "standard_error": full["standard_error"],
            "relative_standard_error": full["relative_standard_error"],
            "maximum_site_leverage": full["maximum_site_leverage"],
            "zero_hit_fraction": full["zero_hit_tasks"] / (full["sites"] * full["replicas"]),
        }
        results[name] = levels_for_candidate
    keys = list(next(iter(results.values())))
    ranking = {
        key: sorted(results, key=lambda name: results[name][key]["value"])
        for key in keys
    }
    full_key = keys[-1]
    for name in results:
        target = results[name][full_key]["value"]
        for key in keys:
            results[name][key]["relative_to_full"] = results[name][key]["value"] / target - 1.0
    return {
        "schema": "material-scan-resolution-report-1",
        "candidates": results,
        "ranking": ranking,
        "full_level": full_key,
    }


def markdown(report: Mapping[str, Any]) -> str:
    names = {
        "baseline": "reference",
        "sic": "SiC anchor",
        "nb-anchor": "niobium-compatible anchor",
    }
    levels = list(next(iter(report["candidates"].values())))
    lines = [
        "# Search-resolution check",
        "",
        "Each smaller result is recomputed from matching recorded sites in the completed "
        f"{report['full_level'].split('-', 1)[0]}-site simulation. The two-replica result "
        "uses replicas 0 and 1. It tests "
        "screening precision; final candidates still require the full spatial design.",
        "",
        "| Point | " + " | ".join(level.replace("-", " ") for level in levels) + " |",
        "|---|" + "---:|" * len(levels),
    ]
    for name, values in report["candidates"].items():
        cells = []
        for level in levels:
            item = values[level]
            cells.append(
                f"{item['value']:.4e} ± {item['standard_error']:.1e} "
                f"({100*item['relative_to_full']:+.1f}%)"
            )
        lines.append(f"| {names.get(name, name)} | " + " | ".join(cells) + " |")
    lines.extend(["", "Ranking by level:", ""])
    for level, order in report["ranking"].items():
        lines.append(
            f"- {level.replace('-', ' ')}: "
            + " < ".join(names.get(name, name) for name in order)
        )
    lines.extend([
        "",
        "A 128-site, two-replica result is acceptable for comparative search only if "
        "the ranking is unchanged and its deviations are small enough not to hide the "
        "large improvements sought here. It is not a replacement for full-resolution "
        "confirmation.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--design-128", required=True, type=Path)
    parser.add_argument("--design-256", required=True, type=Path)
    parser.add_argument("--design-512", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    arguments = parser.parse_args()
    candidates = {}
    for item in arguments.candidate:
        name, path = item.split("=", 1)
        candidates[name] = Path(path)
    report = build_report(candidates, {
        128: arguments.design_128,
        256: arguments.design_256,
        512: arguments.design_512,
    })
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    arguments.markdown.write_text(markdown(report))
    print(json.dumps({"json": str(arguments.json), "markdown": str(arguments.markdown)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
