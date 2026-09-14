#!/usr/bin/env python3
"""Render the hit-file diagnostics with Matplotlib.

Run with the project G4CMP environment, which already provides Matplotlib::

    conda run -n G4CMP python -m material_scan.tools.plot_hits

The data definition is shared with :mod:`plot_hit_diagnostics`: only files with
a ``.done`` marker enter distributions, the CSV header is excluded, database
``n_hits`` is used when available, and older files are counted directly.
Missing planned database files are reported as incomplete.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter, MaxNLocator

from . import hit_data as core


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "material_scan_data" / "plots" / "hit-files"


def ordered(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (
        str(row["trial_id"]), int(row["replica"]), int(row["position"])))


def raw_log_ticks(maximum: int, count: int = 7) -> tuple[list[float], list[str]]:
    raw = core.nice_raw_ticks(maximum, count)
    return [math.log1p(value) for value in raw], [f"{value:,}" for value in raw]


def draw_diagnostic(title: str, rows: list[dict[str, object]], incomplete: int,
                    output: Path, dpi: int) -> None:
    records = ordered(rows)
    hits = np.asarray([int(row["n_hits"]) for row in records], dtype=float)
    rates = np.asarray([
        int(row["n_hits"]) / int(row["events"]) * 1e6
        for row in records if row["events"]
    ], dtype=float)
    empty = int(np.count_nonzero(hits == 0))
    empty_pct = 100.0 * empty / len(hits) if len(hits) else math.nan

    fig, axes = plt.subplots(2, 1, figsize=(14, 8.5),
                             gridspec_kw={"height_ratios": [1.28, 1]},
                             constrained_layout=True)
    fig.suptitle(title, fontsize=15, fontweight="bold", color="#102a43")
    stats = (
        f"completed files: {len(hits):,}   incomplete/excluded: {incomplete:,}   "
        f"empty: {empty:,} ({empty_pct:.1f}%)   "
        f"median: {core.fmt(core.percentile(hits.tolist(), .5))}   "
        f"p95: {core.fmt(core.percentile(hits.tolist(), .95))}   "
        f"max: {int(np.max(hits)) if len(hits) else 0:,}"
    )
    if len(rates):
        stats += (f"\nnormalized median/p95: {core.fmt(core.percentile(rates.tolist(), .5))} / "
                  f"{core.fmt(core.percentile(rates.tolist(), .95))} hits per million primaries")
    fig.text(0.5, 0.925, stats, ha="center", va="top", fontsize=9.5,
             color="#334e68")

    # Ordered file profile. A symlog axis keeps genuine zeros while resolving
    # both one-hit files and rare O(10^4--10^5) files.
    ax = axes[0]
    envelope = core.bucket_envelope([int(value) for value in hits], limit=2200)
    x = np.asarray([item[0] for item in envelope])
    low = np.asarray([item[1] for item in envelope])
    high = np.asarray([item[2] for item in envelope])
    med = np.asarray([item[3] for item in envelope])
    if np.any(high != low):
        ax.vlines(x, low, high, color="#9fb3c8", linewidth=0.5, alpha=0.65,
                  label="min–max within display bucket")
    ax.scatter(x, med, s=5, color="#147d92", alpha=0.82,
               label="file count" if len(envelope) == len(hits) else "bucket median")
    ax.set_yscale("symlog", linthresh=1, linscale=0.8, base=10)
    ax.set_xlim(-0.5, max(0.5, len(hits) - 0.5))
    ax.set_xlabel("completed hits-file index, sorted by trial / replica / position")
    ax.set_ylabel("surface-hit records / file")
    ax.set_title("Ordered file profile", loc="left", fontsize=11, fontweight="bold")
    ax.grid(True, which="both", color="#d9e2ec", linewidth=0.6, alpha=0.8)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    if len(envelope) < len(hits):
        ax.legend(loc="upper right", fontsize=8, frameon=False)

    # Histogram in log1p space. This produces true log-spaced bins in raw hit
    # counts while retaining a finite, visible zero bin.
    ax = axes[1]
    transformed = np.log1p(hits)
    bin_count = max(8, min(36, int(math.ceil(math.log2(max(1, len(hits))) + 1))))
    if len(hits) and float(np.max(transformed)) > 0:
        edges = np.linspace(0.0, float(np.max(transformed)), bin_count + 1)
    else:
        edges = np.linspace(0.0, 1.0, bin_count + 1)
    counts, edges = np.histogram(transformed, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2
    widths = np.diff(edges) * 0.91
    ax.bar(centers, counts, width=widths, color="#3e8ed0", alpha=0.84,
           edgecolor="#266b9a", linewidth=0.35)
    tick_locations, tick_labels = raw_log_ticks(int(np.max(hits)) if len(hits) else 0)
    ax.set_xticks(tick_locations, tick_labels)
    ax.set_xlabel("surface-hit records / file (log-spaced bins; labels are raw counts)")
    ax.set_ylabel("number of files")
    ax.set_title("Hit-count distribution", loc="left", fontsize=11,
                 fontweight="bold")
    ax.grid(True, axis="y", color="#d9e2ec", linewidth=0.6, alpha=0.8)
    ax.yaxis.set_major_locator(MaxNLocator(6, integer=True))
    ax.text(1.0, -0.29,
            "Zero-hit completed files are observations; unfinished files are excluded.",
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            color="#627d98")

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, facecolor="#fbfcfe")
    plt.close(fig)


def draw_overview(summaries: list[dict[str, object]], output: Path, dpi: int) -> None:
    summaries = list(summaries)
    height = max(9.0, 0.38 * len(summaries) + 2.0)
    fig, ax = plt.subplots(figsize=(14, height), constrained_layout=True)
    y = np.arange(len(summaries))
    median = np.asarray([float(row["median_rate_per_million"]) for row in summaries])
    p05 = np.asarray([float(row["p05_rate_per_million"]) for row in summaries])
    p95 = np.asarray([float(row["p95_rate_per_million"]) for row in summaries])
    xerr = np.vstack((np.maximum(0, median - p05), np.maximum(0, p95 - median)))
    ax.errorbar(median, y, xerr=xerr, fmt="o", markersize=5,
                color="#007c91", ecolor="#486581", elinewidth=2, capsize=3)
    ax.set_xscale("symlog", linthresh=1, linscale=0.8, base=10)
    ax.set_yticks(y, [str(row["campaign"]) for row in summaries])
    ax.invert_yaxis()
    ax.set_xlabel("surface-hit records per million primaries (dot=median; bar=p05–p95)")
    ax.set_title("Hit-file diagnostics across experiment campaigns",
                 loc="left", fontsize=15, fontweight="bold", color="#102a43")
    ax.grid(True, axis="x", which="both", color="#d9e2ec", linewidth=0.7)
    ax.tick_params(axis="y", labelsize=8.5)
    for index, row in enumerate(summaries):
        ax.annotate(
            f" zero {float(row['empty_pct']):.1f}% | "
            f"{int(row['completed_files']):,}/{int(row['completed_files']) + int(row['incomplete_files']):,} done",
            (p95[index], y[index]), xytext=(7, 0), textcoords="offset points",
            va="center", fontsize=7.5, color="#52606d")
    fig.savefig(output, dpi=dpi, facecolor="#fbfcfe")
    plt.close(fig)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--campaign-only", action="store_true",
                        help="Skip the hundreds of individual trial PNGs")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    campaigns, incomplete, incomplete_trials = core.collect()
    summaries: list[dict[str, object]] = []
    trial_summaries: list[dict[str, object]] = []
    for campaign in sorted(campaigns):
        rows = campaigns[campaign]
        draw_diagnostic(campaign, rows, incomplete.get(campaign, 0),
                        args.output / f"{campaign}.png", args.dpi)
        summaries.append(core.summarize(campaign, rows, incomplete.get(campaign, 0)))

        if args.campaign_only:
            continue
        by_trial: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            by_trial[str(row["trial_id"])].append(row)
        for trial_id, trial_rows in sorted(by_trial.items()):
            missing = incomplete_trials.get((campaign, trial_id), 0)
            draw_diagnostic(
                f"{campaign} / {trial_id}", trial_rows, missing,
                args.output / "trials" / campaign / f"{trial_id}.png", args.dpi)
            record = core.summarize(campaign, trial_rows, missing)
            trial_summaries.append({
                "campaign": campaign,
                "trial_id": trial_id,
                **{key: value for key, value in record.items() if key != "campaign"},
            })

    draw_overview(summaries, args.output / "overview.png", args.dpi)
    write_csv(args.output / "hit_summary.csv", summaries)
    write_csv(args.output / "trial_summary.csv", trial_summaries)
    print(f"Wrote {len(summaries)} campaign PNGs"
          + ("" if args.campaign_only else f" and {len(trial_summaries)} trial PNGs")
          + f" to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
