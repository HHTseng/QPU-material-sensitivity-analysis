"""Summarize comparable optimizer runs and plot best-so-far curves."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from material_scan.config import load_experiment_spec, load_parameter_catalog
from material_scan.space import ExperimentSpace


def _label(value: str) -> tuple[str, Path]:
    name, path = value.split("=", 1)
    return name, Path(path)


def _pending(value: str) -> tuple[str, str]:
    name, method = value.split("=", 1)
    return name, method


def _incomplete_status(path: Path, experiment_spec_key: str) -> Mapping[str, Any]:
    """Normalize a recorded attempt that has no complete search-state row.

    Historical long-running points were stopped before the search driver could
    persist a completed or failed evaluation.  Their dedicated records are
    operational evidence, but they are not objective measurements and therefore
    must never be converted into a best-so-far curve.
    """

    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != "material-scan-incomplete-search-1":
        raise ValueError(f"unsupported incomplete-search record: {path}")
    if record.get("experiment_spec_key") != experiment_spec_key:
        raise ValueError(f"incomplete search belongs to a different experiment: {path}")

    method = str(record.get("method") or "")
    if not method:
        raise ValueError(f"incomplete search has no method: {path}")
    requested = record.get(
        "requested_model_selected_points", record.get("requested_points")
    )
    completed = record.get(
        "completed_model_selected_points", record.get("completed_points")
    )
    if requested is None or completed is None:
        raise ValueError(f"incomplete search has no point counts: {path}")
    requested = int(requested)
    completed = int(completed)
    if requested <= 0 or completed < 0 or completed > requested:
        raise ValueError(f"invalid incomplete-search point counts: {path}")

    simulation = record.get("simulation")
    if not isinstance(simulation, Mapping):
        raise ValueError(f"incomplete search has no simulation record: {path}")
    declared_tasks = int(simulation.get("declared_tasks", 0))
    completed_tasks = int(simulation.get("completed_tasks", 0))
    simultaneous_tasks = int(simulation.get("simultaneous_tasks", 0))
    if declared_tasks <= 0 or not 0 <= completed_tasks <= declared_tasks:
        raise ValueError(f"invalid incomplete-search task counts: {path}")
    if simultaneous_tasks <= 0 or simultaneous_tasks > declared_tasks:
        raise ValueError(f"invalid incomplete-search concurrency: {path}")

    elapsed_lower = float(simulation.get("observed_elapsed_seconds_lower_bound", 0.0))
    candidate_lower = simulation.get("candidate_completion_hours_lower_bound")
    campaign_lower = simulation.get("requested_search_days_lower_bound_if_repeated")
    stalled = completed_tasks == 0 and elapsed_lower > 0.0
    return {
        "method": method,
        "seed": int(record["seed"]),
        "status": "stalled" if stalled else "incomplete",
        "completed_points": completed,
        "requested_points": requested,
        "incomplete_point": int(record.get("incomplete_point", completed + 1)),
        "declared_tasks": declared_tasks,
        "simultaneous_tasks": simultaneous_tasks,
        "completed_tasks": completed_tasks,
        "observed_elapsed_seconds_lower_bound": elapsed_lower,
        "candidate_completion_hours_lower_bound": (
            None if candidate_lower is None else float(candidate_lower)
        ),
        "requested_search_days_lower_bound_if_repeated": (
            None if campaign_lower is None else float(campaign_lower)
        ),
        "proposal": record.get("proposal"),
        "interpretation": str(record.get("interpretation") or ""),
    }


def _at(values: list[float], count: int, initial: float) -> float:
    return min([initial, *values[:count]])


def build_report(
    experiment: Path,
    catalog_path: Path,
    initial_path: Path,
    runs: Mapping[str, Path],
    pending: Mapping[str, str] | None = None,
    statuses: Mapping[str, Path] | None = None,
) -> Mapping[str, Any]:
    catalog = load_parameter_catalog(catalog_path)
    spec = load_experiment_spec(experiment, catalog)
    space = ExperimentSpace(spec)
    initial_record = json.loads(initial_path.read_text(encoding="utf-8"))
    initial_items = initial_record["evaluations"]
    initial_best_item = min(initial_items, key=lambda item: item["result"]["value"])
    initial_best = float(initial_best_item["result"]["value"])
    overlap = sorted(set(runs) & set(pending or {}))
    if overlap:
        raise ValueError(f"labels cannot be both completed and pending: {overlap}")
    overlap = sorted(set(statuses or {}) & set(pending or {}))
    if overlap:
        raise ValueError(f"labels cannot be both incomplete and pending: {overlap}")
    run_reports: dict[str, Any] = {}
    curves: dict[str, list[float]] = {}
    elapsed_curves: dict[str, list[float]] = {}
    for label, path in runs.items():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("experiment_spec_key") != spec.spec_key:
            raise ValueError(f"search belongs to a different experiment: {path}")
        if state.get("initial_points_key") != initial_record.get("points_key"):
            raise ValueError(f"search used different common starting points: {path}")
        complete = [item for item in state["evaluations"] if item["status"] == "complete"]
        if not complete:
            raise ValueError(f"search has no completed optimizer points: {path}")
        values = [float(item["result"]["value"]) for item in complete]
        best_item = min(complete, key=lambda item: item["result"]["value"])
        adaptive_best = float(best_item["result"]["value"])
        initial_remains_best = initial_best <= adaptive_best
        overall_best = min(initial_best, adaptive_best)
        chosen_values = initial_best_item["values"] if initial_remains_best else best_item["values"]
        unit = space.to_unit(chosen_values)
        bound_distance = sorted(
            (
                min(float(value), 1.0 - float(value)),
                variable.name,
                float(chosen_values[variable.name]),
            )
            for value, variable in zip(unit, space.variables)
        )
        best_curve = []
        current = initial_best
        for value in values:
            current = min(current, value)
            best_curve.append(current)
        curves[label] = best_curve
        elapsed = []
        cumulative_seconds = 0.0
        elapsed_complete = True
        for item in state["evaluations"]:
            duration = item.get("elapsed_seconds")
            if duration is None:
                elapsed_complete = False
                break
            cumulative_seconds += float(duration)
            if item["status"] == "complete":
                elapsed.append(cumulative_seconds)
        if elapsed_complete and elapsed:
            elapsed_curves[label] = elapsed
        sources = Counter(
            str(item.get("source", {}).get("proposal_source", "unknown"))
            for item in complete
        )
        optimizer_state = state["transcript"]["state_summary"]
        if state["method"] == "agentic" and int(
            optimizer_state.get("fallback_selected", 0)
        ):
            raise ValueError(
                f"agentic run used GP fallback and is not agentic comparison evidence: {path}"
            )
        checkpoints = {
            str(count): _at(values, count, initial_best)
            for count in (20, 40, 60, 69, 80, 100, 120)
            if count <= len(values)
        }
        run_reports[label] = {
            "method": state["method"],
            "seed": state["seed"],
            "complete": int(state["complete_steps"]) == int(state["requested_steps"]),
            "steps": len(values),
            "requested_steps": int(state["requested_steps"]),
            "attempts": state["attempts"],
            "failures": int(state["attempts"]) - len(values),
            "best": overall_best,
            "best_standard_error": float(
                initial_best_item["result"]["standard_error"]
                if initial_remains_best else best_item["result"]["standard_error"]
            ),
            "best_source": "common-start" if initial_remains_best else "optimizer",
            "best_step": 0 if initial_remains_best else complete.index(best_item) + 1,
            "best_proposal_attempt": (
                None if initial_remains_best
                else int(best_item["name"].split("-")[-1])
            ),
            "improvement_from_initial": 1.0 - overall_best / initial_best,
            "best_optimizer_value": adaptive_best,
            "best_optimizer_standard_error": float(best_item["result"]["standard_error"]),
            "best_by_completed_step": checkpoints,
            "proposal_sources": dict(sorted(sources.items())),
            "agent_selected": sources.get("agent_gp_ei", 0),
            "fallback_selected": sources.get("gp_fallback", 0),
            "task_timeout_s": float(state.get("task_timeout_s", 0.0)),
            "simulated_source_phonons": len(values) * int(spec.event_counts["events_total"]),
            "elapsed_seconds": elapsed[-1] if elapsed_complete and elapsed else None,
            "optimizer_state": optimizer_state,
            "implementation_sha256": state["transcript"].get("implementation_sha256"),
            "best_values": chosen_values,
            "best_optimizer_values": best_item["values"],
            "closest_bounds": [
                {"name": name, "unit_distance": distance, "value": value}
                for distance, name, value in bound_distance[:5]
            ],
        }

    status_reports = {
        label: _incomplete_status(path, spec.spec_key)
        for label, path in (statuses or {}).items()
    }
    for label in sorted(set(status_reports) & set(run_reports)):
        status = status_reports[label]
        run = run_reports[label]
        if status["method"] != run["method"] or status["seed"] != run["seed"]:
            raise ValueError(
                f"completed and incomplete records disagree for label {label!r}"
            )
        if (
            status["completed_points"] != run["steps"]
            or status["requested_points"] != run["requested_steps"]
        ):
            raise ValueError(
                f"completed and incomplete point counts disagree for label {label!r}"
            )

    timeout_policies = {item["task_timeout_s"] for item in run_reports.values()}
    if len(timeout_policies) > 1:
        raise ValueError(
            f"runs use unequal task-timeout policies: {sorted(timeout_policies)}"
        )
    agent_identities = set()
    for label, item in run_reports.items():
        if item["method"] != "agentic":
            continue
        state = json.loads(runs[label].read_text(encoding="utf-8"))
        options = {
            key: value for key, value in state["transcript"].get("options", {}).items()
            if key != "trace_dir"
        }
        agent_identities.add(json.dumps({
            "model": item["optimizer_state"].get("agent_model"),
            "model_digest": item["optimizer_state"].get("agent_model_digest"),
            "ollama_version": item["optimizer_state"].get("ollama_version"),
            "knowledge_sha256": item["optimizer_state"].get("knowledge_sha256"),
            "implementation_sha256": item["implementation_sha256"],
            "options": options,
        }, sort_keys=True))
    if len(agent_identities) > 1:
        raise ValueError("agentic runs use different model/runtime/search identities")

    requested_budgets = [
        item["requested_steps"] for item in run_reports.values()
    ] + [
        item["requested_points"] for item in status_reports.values()
    ]
    comparison_budget = max(requested_budgets, default=0)
    methods: dict[str, Any] = {}
    for method in sorted({item["method"] for item in run_reports.values()}):
        members = [item for item in run_reports.values() if item["method"] == method]
        if not members:
            continue
        best_values = [item["best"] for item in members]
        adaptive_values = [item["best_optimizer_value"] for item in members]
        improvements = [item["improvement_from_initial"] for item in members]
        methods[method] = {
            "seeds": len(members),
            "complete_runs": sum(bool(item["complete"]) for item in members),
            "best_minimum": min(best_values),
            "best_median": median(best_values),
            "best_maximum": max(best_values),
            "optimizer_only_minimum": min(adaptive_values),
            "optimizer_only_median": median(adaptive_values),
            "optimizer_only_maximum": max(adaptive_values),
            "improvement_median": median(improvements),
            "improvement_range": [min(improvements), max(improvements)],
            "pilot_runs": sum(
                item["method"] == "agentic"
                and item["requested_steps"] < comparison_budget
                for item in members
            ),
            "equal_budget_runs": sum(
                item["requested_steps"] == comparison_budget for item in members
            ),
        }
    return {
        "schema": "material-scan-search-report-1",
        "experiment_spec_key": spec.spec_key,
        "source_phonons_per_candidate": int(spec.event_counts["events_total"]),
        "initial_points": len(initial_items),
        "comparison_requested_steps": comparison_budget,
        "initial_best": {
            "name": initial_best_item["name"],
            "value": initial_best,
            "standard_error": initial_best_item["result"]["standard_error"],
            "values": initial_best_item["values"],
        },
        "runs": run_reports,
        "methods": methods,
        "curves": curves,
        "elapsed_curves": elapsed_curves,
        "pending": dict(pending or {}),
        "incomplete": status_reports,
        "agent_identity": (
            json.loads(next(iter(agent_identities))) if agent_identities else None
        ),
    }


def plot(report: Mapping[str, Any], output: Path) -> None:
    has_elapsed = bool(report.get("elapsed_curves"))
    columns = 3 if has_elapsed else 2
    figure, axes = plt.subplots(
        1, columns, figsize=(6.2 * columns, 4.8), constrained_layout=True
    )
    styles = {
        "bo_gp": [("#56B4E9", "-", "|")],
        "cmaes": [
            ("#0072B2", "-", None),
            ("#D55E00", "--", None),
            ("#CC79A7", "-.", None),
        ],
        "sobol": [("#009E73", "-", "D")],
        "random": [("#6B6B6B", ":", "s")],
        "agentic": [("#E69F00", "-", "o")],
    }
    method_counts: Counter[str] = Counter()
    run_styles: dict[str, tuple[str, str, str | None]] = {}
    for label, item in report["runs"].items():
        method = item["method"]
        variants = styles.get(method, [("#333333", "-", None)])
        run_styles[label] = variants[method_counts[method] % len(variants)]
        method_counts[method] += 1

    for label, values in report["curves"].items():
        color, linestyle, marker = run_styles[label]
        marker_positions = [len(values) - 1] if marker and values else None
        axes[0].plot(
            range(1, len(values) + 1), values, color=color, linestyle=linestyle,
            linewidth=1.8, marker=marker, markevery=marker_positions, markersize=6,
            zorder=2, label=label,
        )
        events = report["source_phonons_per_candidate"]
        axes[1].plot(
            [events * index for index in range(1, len(values) + 1)], values,
            color=color, linestyle=linestyle, linewidth=1.8, marker=marker,
            markevery=marker_positions, markersize=6, zorder=2, label=label,
        )
        elapsed = report.get("elapsed_curves", {}).get(label)
        if has_elapsed and elapsed:
            axes[2].plot(
                [seconds / 3600.0 for seconds in elapsed], values,
                color=color, linestyle=linestyle, linewidth=1.8, marker=marker,
                markevery=marker_positions, markersize=6, zorder=2, label=label,
            )
    for label, method in report.get("pending", {}).items():
        color = styles.get(method, [("#333333", "-", None)])[0][0]
        axes[0].plot(
            [], [], marker="x", linestyle="none", color=color,
            label=f"{label} (not run)",
        )
    for label, item in report.get("incomplete", {}).items():
        if label in report["curves"]:
            continue
        method = item["method"]
        color = styles.get(method, [("#333333", "-", None)])[0][0]
        axes[0].plot(
            [], [], marker="|", markersize=9, linestyle="none",
            color=color,
            label=f"{label} ({item['status']}, 0 scored)",
        )
    for axis in axes:
        axis.axhline(report["initial_best"]["value"], color="black", linestyle="--",
                     linewidth=1.2, zorder=1, label="best common start")
        axis.set_yscale("log")
        axis.grid(True, which="both", alpha=0.22)
    axes[0].set_xlabel("Completed optimizer-selected candidates")
    axes[0].set_ylabel("Best device-weighted junction QPs per injected eV")
    axes[1].set_xlabel("Cumulative simulated source phonons")
    if has_elapsed:
        axes[2].set_xlabel("Recorded candidate wall time (hours)")
    axes[0].legend(fontsize=8, ncol=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def markdown(report: Mapping[str, Any], figure: Path) -> str:
    lines = [
        "# Material-search result",
        "",
        f"All methods began with the same {report['initial_points']} newly evaluated points. "
        f"Each later candidate used {report['source_phonons_per_candidate']:,} source phonons. "
        "The values here are for comparative search; leading points require the full "
        "spatial design and unused random seeds before a material conclusion. An incomplete "
        "run contributes only its completed points and is labelled by its completion count.",
        "",
        f"The best common starting value was `{report['initial_best']['value']:.6e}`.",
        "",
        "| Run | Method | Completed | Best | Source | Improvement | Best step | Failed proposals |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for label, item in report["runs"].items():
        lines.append(
            f"| {label} | {item['method']} | {item['steps']}/{item['requested_steps']} | "
            f"{item['best']:.6e} ± "
            f"{item['best_standard_error']:.2e} | "
            f"{item['best_source']} | {100*item['improvement_from_initial']:.1f}% | "
            f"{item['best_step']} | "
            f"{item['failures']} |"
        )
        if item["method"] == "agentic":
            lines.append(
                f"<!-- {label}: agent-selected={item['agent_selected']}, "
                f"GP-fallback={item['fallback_selected']} -->"
            )
    for label, method in report.get("pending", {}).items():
        lines.append(f"| {label} | {method} | not run | — | — | — | — | — |")
    incomplete = report.get("incomplete", {})
    if incomplete:
        lines.extend([
            "",
            "Recorded incomplete/stalled attempts (these are runtime observations, not "
            "objective measurements):",
            "",
            "| Attempt | Method | Search completions | Stalled point | Task progress | "
            "Observed elapsed | Candidate lower bound |",
            "|---|---|---:|---:|---:|---:|---:|",
        ])
        for label, item in incomplete.items():
            elapsed = item["observed_elapsed_seconds_lower_bound"] / 3600.0
            lower = item["candidate_completion_hours_lower_bound"]
            lower_text = "—" if lower is None else f"≥{lower:.2f} h"
            lines.append(
                f"| {label} | {item['method']} ({item['status']}) | "
                f"{item['completed_points']}/{item['requested_points']} | "
                f"{item['incomplete_point']} | "
                f"{item['completed_tasks']}/{item['declared_tasks']} "
                f"({item['simultaneous_tasks']} concurrent) | "
                f"≥{elapsed:.2f} h | {lower_text} |"
            )
    lines.extend([
        "",
        "Descriptive method summary over recorded runs (candidate budgets may differ):",
        "",
    ])
    for method, item in report["methods"].items():
        qualifier = ""
        if method == "agentic" and item.get("pilot_runs") and not item.get(
            "equal_budget_runs"
        ):
            qualifier = " (deployment pilot; not equal-budget evidence)"
        lines.append(
            f"- {method}{qualifier}: {item['complete_runs']}/{item['seeds']} requested runs "
            "complete; median best "
            f"`{item['best_median']:.6e}`; range "
            f"`{item['best_minimum']:.6e}` to `{item['best_maximum']:.6e}`; "
            f"median improvement {100*item['improvement_median']:.1f}%; "
            f"optimizer-selected-only median `{item['optimizer_only_median']:.6e}`."
        )
    if "bo_gp" in report["methods"]:
        lines.extend([
            "",
            "To decide whether extending EI-GP beyond the earlier 69 genuine acquisitions helped, "
            "compare each run's best value at step 69 with its value at step 120 in the JSON report.",
        ])
    lines.extend([
        "",
        "Branch names identify source history, not a numerical comparison axis. Curves are included "
        "only when the experiment identity and common starting-point identity match; older "
        "branches with different objectives or spatial designs are intentionally excluded.",
        "",
        "The agent prompt deliberately carries forward lessons from the recorded BO/Sobol "
        "stalls and aggregate CMA-ES behavior. This is a retrospective workflow comparison, "
        "not a blinded optimizer benchmark, even when source-phonon counts match.",
    ])
    agentic_runs = [
        item for item in report["runs"].values() if item["method"] == "agentic"
    ]
    agentic_full_runs = [
        item for item in agentic_runs
        if item["requested_steps"] >= report.get("comparison_requested_steps", 0)
    ]
    agentic_pending = "agentic" in report.get("pending", {}).values()
    if agentic_pending and agentic_runs and not agentic_full_runs:
        best_pilot = min(agentic_runs, key=lambda item: item["best_optimizer_value"])
        candidate = best_pilot["best_optimizer_value"]
        initial = report["initial_best"]["value"]
        relative = candidate / initial - 1.0
        candidate_se = best_pilot["best_optimizer_standard_error"]
        initial_se = float(report["initial_best"]["standard_error"])
        intervals_overlap = (
            candidate - candidate_se <= initial + initial_se
            and initial - initial_se <= candidate + candidate_se
        )
        direction = "higher" if relative >= 0.0 else "lower"
        overlap_text = (
            "the reported one-standard-error intervals overlap"
            if intervals_overlap
            else "the reported one-standard-error intervals do not overlap"
        )
        lines.extend([
            "",
            "**Agentic conclusion: deployment pilot complete; full equal-budget campaign not "
            "run.** "
            f"The best agent-selected pilot candidate was `{candidate:.6e} ± "
            f"{candidate_se:.2e}`, {abs(100 * relative):.1f}% {direction} than the common "
            f"incumbent; {overlap_text}. This pilot validates the execution path but is not "
            "an equal-budget comparison or sample-efficiency evidence, so it establishes no "
            "resolved improvement.",
        ])
    elif agentic_pending:
        lines.extend([
            "",
            "**Agentic conclusion: not run.** The current evidence cannot show an improvement "
            "or a regression. The agentic marker in the legend has no result points.",
        ])
    lines.extend(["", f"![Best-so-far search curves]({figure.as_posix()})", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--initial", required=True, type=Path)
    parser.add_argument("--run", action="append", required=True, type=_label)
    parser.add_argument(
        "--pending", action="append", default=[], type=_pending,
        help="LABEL=METHOD entry to show explicitly as not run",
    )
    parser.add_argument(
        "--status", action="append", default=[], type=_label,
        help="LABEL=PATH recorded incomplete/stalled attempt with no objective value",
    )
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--figure", required=True, type=Path)
    arguments = parser.parse_args()
    report = build_report(
        arguments.experiment,
        arguments.catalog,
        arguments.initial,
        dict(arguments.run),
        dict(arguments.pending),
        dict(arguments.status),
    )
    plot(report, arguments.figure)
    body = dict(report)
    body.pop("curves")
    body.pop("elapsed_curves")
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    relative_figure = Path(os.path.relpath(arguments.figure, arguments.markdown.parent))
    arguments.markdown.write_text(markdown(report, relative_figure))
    print(json.dumps({"json": str(arguments.json), "markdown": str(arguments.markdown),
                      "figure": str(arguments.figure)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
