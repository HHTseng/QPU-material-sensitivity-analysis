"""Compare full-spatial material-search confirmations with their screening values."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from itertools import combinations
import json
from pathlib import Path
from typing import Any, Mapping

from material_scan.analysis import paired_stratified_difference
from material_scan.tools.refinement_report import _current


def _named_path(argument: str) -> tuple[str, Path]:
    name, path = argument.split("=", 1)
    return name, Path(path)


def _named_float(argument: str) -> tuple[str, float]:
    name, value = argument.split("=", 1)
    return name, float(value)


def build_report(
    reference_directory: Path,
    candidates: Mapping[str, Path],
    screening: Mapping[str, float],
) -> Mapping[str, Any]:
    reference, reference_blocks, reference_design = _current(reference_directory)
    loaded: dict[str, tuple[Mapping[str, Any], Any, Any]] = {}
    for name, directory in candidates.items():
        result, blocks, design = _current(directory)
        if design.design_hash != reference_design.design_hash:
            raise ValueError(f"{name} does not use the reference spatial design")
        loaded[name] = (result, blocks, design)
    anchor = loaded.get("compatible-anchor")
    items: dict[str, Any] = {
        "reference": {
            "screening_value": screening.get("reference"),
            "confirmed_value": float(reference["value"]),
            "confirmed_standard_error": float(reference["standard_error"]),
            "screening_to_confirmation": (
                None if "reference" not in screening
                else float(screening["reference"] / reference["value"] - 1.0)
            ),
            "paired_difference_from_reference": None,
            "resolved_below_reference": None,
            "paired_difference_from_compatible_anchor": None,
            "resolved_below_compatible_anchor": None,
        }
    }
    for name, (result, blocks, design) in loaded.items():
        paired = paired_stratified_difference(blocks, reference_blocks, design)
        paired_record = asdict(paired)
        paired_anchor = None
        if anchor is not None and name != "compatible-anchor":
            paired_anchor = paired_stratified_difference(blocks, anchor[1], design)
        items[name] = {
            "screening_value": screening.get(name),
            "confirmed_value": float(result["value"]),
            "confirmed_standard_error": float(result["standard_error"]),
            "screening_to_confirmation": (
                None if name not in screening
                else float(screening[name] / result["value"] - 1.0)
            ),
            "paired_difference_from_reference": paired_record,
            "resolved_below_reference": paired.confidence_interval_95[1] < 0.0,
            "paired_difference_from_compatible_anchor": (
                None if paired_anchor is None else asdict(paired_anchor)
            ),
            "resolved_below_compatible_anchor": (
                None if paired_anchor is None
                else paired_anchor.confidence_interval_95[1] < 0.0
            ),
        }
    pairwise: dict[str, Any] = {}
    finalist_names = [name for name in loaded if name != "compatible-anchor"]
    for a, b in combinations(finalist_names, 2):
        difference = paired_stratified_difference(
            loaded[a][1], loaded[b][1], reference_design
        )
        lower, upper = difference.confidence_interval_95
        pairwise[f"{a}-minus-{b}"] = {
            **asdict(difference),
            "a": a,
            "b": b,
            "resolved_lower": a if upper < 0.0 else (b if lower > 0.0 else None),
        }
    return {
        "schema": "material-scan-confirmation-report-1",
        "design_hash": reference_design.design_hash,
        "sites": reference["sites"],
        "replicas": reference["replicas"],
        "events_per_task": reference["events_per_task"],
        "ranking": sorted(items, key=lambda name: items[name]["confirmed_value"]),
        "candidates": items,
        "pairwise_finalists": pairwise,
    }


def markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Material-search confirmation",
        "",
        f"Every point uses {report['sites']:,} spatial sites, {report['replicas']} unused "
        f"random replicas, and {report['events_per_task']:,} source phonons per task. "
        "Uncertainties are one standard error.",
        "",
        "| Point | Screening | Confirmation | Screening offset | Paired change from reference | Paired change from compatible anchor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    reference = report["candidates"]["reference"]["confirmed_value"]
    for name, item in report["candidates"].items():
        screen = item["screening_value"]
        offset = item["screening_to_confirmation"]
        paired = item["paired_difference_from_reference"]
        paired_text = "--" if paired is None else (
            f"{100*paired['relative_to_b']:+.1f}% ± "
            f"{100*paired['standard_error']/reference:.1f} points"
        )
        paired_anchor = item["paired_difference_from_compatible_anchor"]
        anchor_value = report["candidates"].get("compatible-anchor", {}).get("confirmed_value")
        paired_anchor_text = "--" if paired_anchor is None else (
            f"{100*paired_anchor['relative_to_b']:+.1f}% ± "
            f"{100*paired_anchor['standard_error']/anchor_value:.1f} points"
        )
        lines.append(
            f"| {name} | {'--' if screen is None else f'{screen:.6e}'} | "
            f"{item['confirmed_value']:.6e} ± {item['confirmed_standard_error']:.2e} | "
            f"{'--' if offset is None else f'{100*offset:+.1f}%'} | {paired_text} | "
            f"{paired_anchor_text} |"
        )
    lines.extend([
        "",
        "Confirmed ranking: " + " < ".join(report["ranking"]),
        "",
        "Pairwise finalist comparisons use the same sites and random seeds:",
        "",
    ])
    for item in report["pairwise_finalists"].values():
        decision = item["resolved_lower"] or "unresolved"
        lines.append(
            f"- {item['a']} minus {item['b']}: {100*item['relative_to_b']:+.1f}% "
            f"(95% interval `{item['confidence_interval_95'][0]:.3e}` to "
            f"`{item['confidence_interval_95'][1]:.3e}`); lower result: {decision}."
        )
    lines.extend([
        "",
        "A screening minimum is retained only when the full-spatial result supports it. "
        "The upper-tail quantity remains descriptive and is not a selection limit.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", action="append", required=True, type=_named_path)
    parser.add_argument("--screen", action="append", default=[], type=_named_float)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    arguments = parser.parse_args()
    report = build_report(
        arguments.reference, dict(arguments.candidate), dict(arguments.screen)
    )
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    arguments.markdown.write_text(markdown(report))
    print(json.dumps({"json": str(arguments.json), "markdown": str(arguments.markdown)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
