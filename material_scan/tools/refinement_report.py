"""Compare a completed nested spatial refinement with its previous level."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

from material_scan.analysis import Block, StratifiedDesign, paired_stratified_difference
from material_scan.config import load_resolved_experiment


def _current(directory: Path) -> tuple[dict[str, Any], list[Block], StratifiedDesign]:
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    experiment = load_resolved_experiment(directory / "resolved.json")
    blocks = []
    for path in sorted((directory / "scores").glob("*.json")):
        score = json.loads(path.read_text(encoding="utf-8"))
        blocks.append(Block(
            position=int(score["position"]), replica=int(score["replica"]),
            events=int(score["events"]), total_qps=float(score["total_qps"]),
            per_electrode_qps=tuple(float(value) for value in score["per_electrode_qps"]),
            n_hits=int(score["n_hits"]),
        ))
    source = experiment.sampling.design.to_manifest()
    design = StratifiedDesign.from_values(
        position_strata=source["stratum"], stratum_weights=source["stratum_weights"],
        replica_ids=range(experiment.sampling.replicas),
        events_per_task=experiment.sampling.events_per_task,
        gun_energy_eV=float(experiment.event_counts["gun_energy_eV"]),
        electrode_weights=source["electrode_weights"],
        design_hash=source["design_hash"], injection_law=source["injection_law"],
    )
    return result, blocks, design


def _label(argument: str) -> tuple[str, Path]:
    try:
        name, path = argument.split("=", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("candidate must be NAME=DIRECTORY") from error
    return name, Path(path)


def build_report(previous_path: Path, directories: dict[str, Path]) -> dict[str, Any]:
    previous_document = json.loads(previous_path.read_text(encoding="utf-8"))
    if "results" in previous_document:
        previous = previous_document["results"]
        previous_design_hash = previous_document["stratified_design"]["design_hash"]
        previous_sites = int(previous_document["n_positions"])
    elif previous_document.get("schema") == "material-scan-refinement-report-1":
        previous = {
            name: {
                "value": item["value"],
                "se": item["standard_error"],
                "detail": {"mu_h": item["means_by_stratum"]},
            }
            for name, item in previous_document["candidates"].items()
        }
        previous_design_hash = previous_document["design_hash"]
        previous_sites = int(previous_document["sites"])
    else:
        raise ValueError("previous result has an unsupported schema")
    current: dict[str, dict[str, Any]] = {}
    blocks: dict[str, list[Block]] = {}
    designs: dict[str, StratifiedDesign] = {}
    for name, directory in directories.items():
        current[name], blocks[name], designs[name] = _current(directory)
    if len({design.design_hash for design in designs.values()}) != 1:
        raise ValueError("current candidates do not share one spatial design")
    if "baseline" not in current:
        raise ValueError("a baseline result is required")

    base_old = float(previous["baseline"]["value"])
    base_new = float(current["baseline"]["value"])
    candidate_reports: dict[str, Any] = {}
    refined = ("S1_le_0.05", "S3_le_0.50", "S4_bulk")
    for name, result in current.items():
        old = previous[name]
        old_value = float(old["value"])
        new_value = float(result["value"])
        old_se = float(old["se"])
        new_se = float(result["standard_error"])
        difference = new_value - old_value
        combined = math.hypot(old_se, new_se)
        reduction_old = None if name == "baseline" else old_value / base_old - 1.0
        reduction_new = None if name == "baseline" else new_value / base_new - 1.0
        stratum_change = {
            label: result["means_by_stratum"][label] / old["detail"]["mu_h"][label] - 1.0
            for label in refined
        }
        criteria = {
            "overall_change_at_most_5_percent": abs(difference) / new_value <= 0.05,
            "overall_change_within_two_combined_standard_errors": abs(difference) <= 2.0 * combined,
            "reduction_change_at_most_5_percentage_points": (
                True if name == "baseline"
                else abs(float(reduction_new) - float(reduction_old)) <= 0.05
            ),
            "maximum_site_leverage_at_most_10_percent": float(result["maximum_site_leverage"]) <= 0.10,
            "each_refined_stratum_changes_at_most_5_percent": all(
                abs(value) <= 0.05 for value in stratum_change.values()
            ),
        }
        paired = None
        if name != "baseline":
            paired = asdict(paired_stratified_difference(
                blocks[name], blocks["baseline"], designs[name],
            ))
        weights = designs[name].weights
        contributions = {
            label: weights[label] * float(result["means_by_stratum"][label])
            for label in result["means_by_stratum"]
        }
        candidate_reports[name] = {
            "previous_value": old_value,
            "value": new_value,
            "standard_error": new_se,
            "relative_standard_error": result["relative_standard_error"],
            "change_from_previous": difference / old_value,
            "combined_standard_error": combined,
            "change_in_combined_standard_errors": difference / combined if combined else None,
            "reduction_from_baseline_previous": reduction_old,
            "reduction_from_baseline": reduction_new,
            "reduction_change_percentage_points": (
                None if name == "baseline" else 100.0 * (float(reduction_new) - float(reduction_old))
            ),
            "maximum_site_leverage": result["maximum_site_leverage"],
            "spatial_r95": result["spatial_r95"],
            "spatial_r95_standard_error": result["spatial_r95_standard_error"],
            "zero_hit_fraction": result["zero_hit_tasks"] / (result["sites"] * result["replicas"]),
            "means_by_stratum": result["means_by_stratum"],
            "contributions_by_stratum": contributions,
            "contribution_fraction_by_stratum": {
                label: value / new_value for label, value in contributions.items()
            },
            "refined_stratum_change": stratum_change,
            "paired_difference_from_baseline": paired,
            "criteria": criteria,
            "passes_candidate_checks": all(criteria.values()),
        }

    old_order = sorted(current, key=lambda name: float(previous[name]["value"]))
    new_order = sorted(current, key=lambda name: float(current[name]["value"]))
    resolved_inversions = []
    for left_index, left in enumerate(old_order):
        for right in old_order[left_index + 1:]:
            if new_order.index(left) <= new_order.index(right):
                continue
            separation = abs(float(current[left]["value"]) - float(current[right]["value"]))
            pooled = math.hypot(
                float(current[left]["standard_error"]), float(current[right]["standard_error"])
            )
            if separation > 2.0 * pooled:
                resolved_inversions.append([left, right])
    ranking_passes = not resolved_inversions
    converged = ranking_passes and all(
        item["passes_candidate_checks"] for item in candidate_reports.values()
    )
    return {
        "schema": "material-scan-refinement-report-1",
        "previous_design_hash": previous_design_hash,
        "design_hash": next(iter(designs.values())).design_hash,
        "previous_sites": previous_sites,
        "sites": next(iter(current.values()))["sites"],
        "replicas": next(iter(current.values()))["replicas"],
        "events_per_task": next(iter(current.values()))["events_per_task"],
        "candidates": candidate_reports,
        "ranking_previous": old_order,
        "ranking": new_order,
        "resolved_ranking_inversions": resolved_inversions,
        "ranking_passes": ranking_passes,
        "spatial_converged": converged,
    }


def markdown(report: dict[str, Any]) -> str:
    display_names = {
        "baseline": "reference",
        "elasticity_of_SiC": "SiC elasticity",
        "nb_gap_compatible_anchor": "niobium-gap-compatible point",
    }
    criterion_text = {
        "overall_change_at_most_5_percent": "the overall objective moved by more than 5%",
        "overall_change_within_two_combined_standard_errors": (
            "the overall shift exceeded two combined standard errors"
        ),
        "reduction_change_at_most_5_percentage_points": (
            "the change relative to the reference moved by more than 5 percentage points"
        ),
        "maximum_site_leverage_at_most_10_percent": (
            "one site supplied more than 10% of the objective"
        ),
        "each_refined_stratum_changes_at_most_5_percent": (
            "at least one refined region moved by more than 5%"
        ),
    }
    lines = [
        "# Targeted spatial-refinement result",
        "",
        f"The design increased from {report['previous_sites']} to {report['sites']} sites. "
        f"It retained {report['replicas']} replicas and {report['events_per_task']:,} primaries per task.",
        "",
        "The objective is the equal-electrode-weighted junction-QP yield per injected eV. "
        "Uncertainties below are one standard error.",
        "",
        "| Point | Objective | Change from previous | Paired change from reference | Largest site share | Decision |",
        "|---|---:|---:|---:|---:|---|",
    ]
    reference_value = report["candidates"]["baseline"]["value"]
    for name, item in report["candidates"].items():
        paired = item["paired_difference_from_baseline"]
        paired_text = "--"
        if paired is not None:
            paired_text = (
                f"{100*paired['relative_to_b']:+.1f}% ± "
                f"{100*paired['standard_error']/reference_value:.1f} points"
            )
        lines.append(
            f"| {display_names.get(name, name)} | "
            f"{item['value']:.6e} ± {item['standard_error']:.2e} | "
            f"{100*item['change_from_previous']:+.1f}% | {paired_text} | "
            f"{100*item['maximum_site_leverage']:.1f}% | "
            f"{'pass' if item['passes_candidate_checks'] else 'not converged'} |"
        )
    lines.extend(["", "## Predeclared checks", ""])
    for name, item in report["candidates"].items():
        failed = [criterion_text[key] for key, value in item["criteria"].items() if not value]
        lines.append(
            f"- {display_names.get(name, name)}: "
            + ("all checks pass" if not failed else "; ".join(failed))
        )
    lines.extend([
        "",
        "Ranking: " + " < ".join(display_names.get(name, name) for name in report["ranking"]),
        "",
        "Overall spatial decision: **" + ("converged" if report["spatial_converged"] else "not converged") + "**.",
        "",
        "## Region-level changes",
        "",
        "| Point | within 0.05 mm | 0.20–0.50 mm | remaining area |",
        "|---|---:|---:|---:|",
    ])
    for name, item in report["candidates"].items():
        change = item["refined_stratum_change"]
        lines.append(
            f"| {display_names.get(name, name)} | {100*change['S1_le_0.05']:+.1f}% | "
            f"{100*change['S3_le_0.50']:+.1f}% | {100*change['S4_bulk']:+.1f}% |"
        )
    lines.extend([
        "",
        "The spatial upper-tail value is reported as a diagnostic, not enforced as a selection limit.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", action="append", default=[], type=_label)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    arguments = parser.parse_args()
    directories = {"baseline": arguments.baseline, **dict(arguments.candidate)}
    report = build_report(arguments.previous, directories)
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    arguments.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({
        "json": str(arguments.json), "markdown": str(arguments.markdown),
        "spatial_converged": report["spatial_converged"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
