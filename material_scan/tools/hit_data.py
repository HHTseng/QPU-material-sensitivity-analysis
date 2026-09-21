#!/usr/bin/env python3
"""Create dependency-free hit-count diagnostics for every experiment campaign.

The Stage 4 database stores the number of CSV records in each completed hits
file.  Older Stage 3 databases do not, so this script counts data lines on disk
for those files.  Files without a ``.done`` marker are reported as incomplete
and are never mixed into the statistical distributions.

The SVG output deliberately uses log1p axes.  Campaigns range from zero-hit
1,000-primary smoke-test files to tens of thousands of hits in 100-million-
primary confirmation files; a linear axis would make the useful low-count
structure invisible.  Axis labels remain in the original hit-count units.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / "data"
RUNS = ROOT / "runs"
DATABASES = (
    ROOT / "stage3_trials.sqlite",
    ROOT / "stage3_trials_e7.sqlite",
    ROOT / "stage3_trials_e8.sqlite",
    ROOT / "stage4_trials.sqlite",
)
FILE_RE = re.compile(r"_r(?P<replica>\d+)_p(?P<position>\d+)_hitsfile\.txt$")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    location = (len(ordered) - 1) * fraction
    lo = int(math.floor(location))
    hi = int(math.ceil(location))
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (location - lo))


def fmt(value: float) -> str:
    if not math.isfinite(value):
        return "NA"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def data_row_count(path: Path) -> int:
    """Count newline-delimited CSV records, excluding the single header."""
    lines = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            lines += block.count(b"\n")
    # Writers terminate records with newlines. Handle an interrupted-looking
    # final line defensively even though only .done files reach this function.
    if path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                lines += 1
    return max(0, lines - 1)


def database_metadata() -> dict[str, dict[str, object]]:
    """Index hit path -> count/event/campaign metadata across all databases."""
    result: dict[str, dict[str, object]] = {}
    for database in DATABASES:
        if not database.exists():
            continue
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        sub_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sub_runs)")
        }
        n_hits_expr = "s.n_hits" if "n_hits" in sub_columns else "NULL"
        query = f"""
            SELECT t.campaign_id, t.trial_id, t.events_per_sub_run,
                   s.replica, s.position, s.hits_file, s.status,
                   {n_hits_expr} AS n_hits
            FROM sub_runs AS s JOIN trials AS t ON t.trial_id = s.trial_id
        """
        for row in connection.execute(query):
            if not row["hits_file"]:
                continue
            stored = row["hits_file"]
            # Stored artifact paths are repository-relative since 2026-09-14;
            # resolving them against the CWD would silently miss every file.
            path = Path(stored)
            if not path.is_absolute():
                path = ROOT.parent / stored
            path = str(path.resolve())
            result[path] = dict(row)
        connection.close()
    return result


def collect() -> tuple[dict[str, list[dict[str, object]]], dict[str, int],
                       dict[tuple[str, str], int]]:
    metadata = database_metadata()
    campaigns: dict[str, list[dict[str, object]]] = defaultdict(list)
    incomplete: dict[str, int] = defaultdict(int)
    incomplete_trials: dict[tuple[str, str], int] = defaultdict(int)
    visited: set[str] = set()

    # Start from planned database rows.  This catches a more serious form of
    # incompleteness than a zero-byte artifact: a planned file never created.
    for path_text, meta in sorted(metadata.items()):
        path = Path(path_text)
        try:
            relative = path.relative_to(RUNS.resolve())
        except ValueError:
            continue
        if len(relative.parts) < 4 or relative.parts[2] != "hits":
            continue
        visited.add(path_text)
        campaign = relative.parts[0]
        if not path.exists() or not Path(str(path) + ".done").exists():
            incomplete[campaign] += 1
            incomplete_trials[(campaign, str(meta.get("trial_id", relative.parts[1])))] += 1
            continue
        n_hits = meta.get("n_hits")
        source = "database"
        if n_hits is None:
            n_hits = data_row_count(path)
            source = "disk"
        campaigns[campaign].append({
            "path": str(path),
            "trial_id": meta.get("trial_id", relative.parts[1]),
            "replica": int(meta.get("replica", 0)),
            "position": int(meta.get("position", 0)),
            "events": int(meta["events_per_sub_run"]) if meta.get("events_per_sub_run") else None,
            "n_hits": int(n_hits),
            "source": source,
        })

    # Include completed artifacts not represented by a current database row
    # (for example, an abandoned/relaunched pre-migration trial directory).
    for path in sorted(RUNS.glob("*/*/hits/*_hitsfile.txt")):
        if str(path.resolve()) in visited:
            continue
        relative = path.relative_to(RUNS)
        campaign = relative.parts[0]
        if not Path(str(path) + ".done").exists():
            incomplete[campaign] += 1
            incomplete_trials[(campaign, relative.parts[1])] += 1
            continue
        meta = metadata.get(str(path.resolve()), {})
        n_hits = meta.get("n_hits")
        source = "database"
        if n_hits is None:
            n_hits = data_row_count(path)
            source = "disk"
        match = FILE_RE.search(path.name)
        campaigns[campaign].append({
            "path": str(path),
            "trial_id": meta.get("trial_id", relative.parts[1]),
            "replica": int(meta.get("replica", match.group("replica") if match else 0)),
            "position": int(meta.get("position", match.group("position") if match else 0)),
            "events": int(meta["events_per_sub_run"]) if meta.get("events_per_sub_run") else None,
            "n_hits": int(n_hits),
            "source": source,
        })
    return campaigns, incomplete, incomplete_trials


def write_index(summaries: list[dict[str, object]], output: Path) -> None:
    cards = []
    for row in summaries:
        campaign = str(row["campaign"])
        cards.append(f"""
        <article>
          <h2>{esc(campaign)}</h2>
          <p>{int(row['completed_files']):,} completed; {int(row['incomplete_files']):,} incomplete;
             median {fmt(float(row['median_rate_per_million']))} hits / million primaries</p>
          <a href="{esc(campaign)}.svg"><img src="{esc(campaign)}.svg" alt="Hit diagnostics for {esc(campaign)}"></a>
        </article>""")
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Hit-file diagnostics</title>
<style>
body {{ margin: 2rem auto; max-width: 1500px; font-family: system-ui, sans-serif; color: #243b53; }}
h1 {{ color: #102a43; }}
article {{ border-top: 1px solid #bcccdc; margin-top: 2rem; padding-top: 1rem; }}
img {{ width: 100%; height: auto; background: #fbfcfe; }}
.overview {{ border: 1px solid #bcccdc; }}
</style></head><body>
<h1>Hit-file diagnostics</h1>
<p>Completed <code>.done</code> files only. Counts are CSV data records excluding the header.
Incomplete includes planned database files that are absent, zero-byte, or lack a completion marker.</p>
<a href="overview.svg"><img class="overview" src="overview.svg" alt="Campaign overview"></a>
{''.join(cards)}
</body></html>
"""
    (output / "index.html").write_text(document, encoding="utf-8")


def svg_text(x: float, y: float, text: object, size: int = 13,
             anchor: str = "start", weight: str = "normal",
             fill: str = "#263238", rotate: int | None = None) -> str:
    transform = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" '
            f'font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}"{transform}>{esc(text)}</text>')


def nice_raw_ticks(maximum: int, count: int = 6) -> list[int]:
    if maximum <= 0:
        return [0]
    transformed_max = math.log1p(maximum)
    values = {0, maximum}
    for i in range(1, count):
        values.add(int(round(math.expm1(transformed_max * i / count))))
    return sorted(values)


def bucket_envelope(values: list[int], limit: int = 1800) -> list[tuple[float, int, int, float]]:
    """Return x, min, max, median buckets without hiding narrow spikes."""
    if len(values) <= limit:
        return [(float(i), value, value, float(value)) for i, value in enumerate(values)]
    width = math.ceil(len(values) / limit)
    output = []
    for start in range(0, len(values), width):
        block = values[start:start + width]
        output.append((start + (len(block) - 1) / 2, min(block), max(block),
                       percentile(block, 0.5)))
    return output


def log_histogram(values: list[int]) -> tuple[list[tuple[float, float, int]], int]:
    """Histogram in log1p space; bin labels remain raw hit counts."""
    if not values:
        return [], 0
    maximum = max(values)
    if maximum == 0:
        return [(0.0, 0.0, len(values))], len(values)
    # Sturges is stable for discrete, strongly skewed data. Cap the count so
    # campaign figures remain readable even with tens of thousands of files.
    bins = max(8, min(36, int(math.ceil(math.log2(len(values)) + 1))))
    transformed_max = math.log1p(maximum)
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int(math.log1p(value) / transformed_max * bins))
        counts[index] += 1
    result = []
    for index, count in enumerate(counts):
        lo = math.expm1(transformed_max * index / bins)
        hi = math.expm1(transformed_max * (index + 1) / bins)
        result.append((lo, hi, count))
    return result, max(counts)


def campaign_svg(campaign: str, rows: list[dict[str, object]], incomplete: int,
                 output: Path) -> None:
    rows = sorted(rows, key=lambda r: (str(r["trial_id"]), int(r["replica"]), int(r["position"])))
    hits = [int(row["n_hits"]) for row in rows]
    rates = [int(row["n_hits"]) / int(row["events"]) * 1e6
             for row in rows if row["events"]]
    width, height = 1280, 820
    left, right = 92, 42
    plot_width = width - left - right
    top_y, top_h = 150, 280
    hist_y, hist_h = 535, 210
    max_hits = max(hits, default=0)
    max_log = max(1.0, math.log1p(max_hits))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfcfe"/>',
        svg_text(left, 38, campaign, 23, weight="bold", fill="#102a43"),
        svg_text(left, 65, "Completed hits files only; one file = one site × replica sub-run", 13, fill="#52606d"),
        svg_text(left, 91,
                 f"files {len(hits):,}   incomplete/excluded {incomplete:,}   "
                 f"empty {sum(v == 0 for v in hits):,} ({(100*sum(v == 0 for v in hits)/len(hits) if hits else 0):.1f}%)   "
                 f"median {fmt(percentile(hits, .5))}   p95 {fmt(percentile(hits, .95))}   max {max_hits:,}",
                 14, weight="bold"),
    ]
    if rates:
        parts.append(svg_text(
            left, 116,
            f"normalized: median {fmt(percentile(rates, .5))} and p95 {fmt(percentile(rates, .95))} hits per million primaries",
            13, fill="#334e68"))

    # Ordered per-file plot.
    parts += [
        svg_text(left, top_y - 18, "Ordered file profile (log1p vertical scale)", 16, weight="bold"),
        f'<rect x="{left}" y="{top_y}" width="{plot_width}" height="{top_h}" fill="#ffffff" stroke="#bcccdc"/>',
    ]
    for raw_tick in nice_raw_ticks(max_hits):
        y = top_y + top_h - math.log1p(raw_tick) / max_log * top_h
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e4e7eb"/>')
        parts.append(svg_text(left - 10, y + 4, f"{raw_tick:,}", 11, anchor="end"))
    envelope = bucket_envelope(hits)
    denominator = max(1, len(hits) - 1)
    for x_index, low, high, median in envelope:
        x = left + x_index / denominator * plot_width
        y_low = top_y + top_h - math.log1p(low) / max_log * top_h
        y_high = top_y + top_h - math.log1p(high) / max_log * top_h
        y_med = top_y + top_h - math.log1p(median) / max_log * top_h
        if high != low:
            parts.append(f'<line x1="{x:.2f}" y1="{y_high:.2f}" x2="{x:.2f}" y2="{y_low:.2f}" stroke="#9fb3c8" stroke-width="1"/>')
        parts.append(f'<circle cx="{x:.2f}" cy="{y_med:.2f}" r="1.35" fill="#147d92"/>')
    parts.append(svg_text(left + plot_width / 2, top_y + top_h + 36,
                          f"completed hits-file index (0 to {max(0, len(hits)-1):,}), sorted by trial / replica / position",
                          12, anchor="middle"))
    parts.append(svg_text(24, top_y + top_h / 2, "surface-hit records / file", 12,
                          anchor="middle", rotate=-90))
    if len(envelope) < len(hits):
        parts.append(svg_text(width-right, top_y - 18,
                              "display: min–median–max envelope per index bucket",
                              11, anchor="end", fill="#627d98"))

    # Histogram.
    histogram, max_bin = log_histogram(hits)
    parts += [
        svg_text(left, hist_y - 18, "Distribution (log-spaced hit-count bins; all completed files)", 16, weight="bold"),
        f'<rect x="{left}" y="{hist_y}" width="{plot_width}" height="{hist_h}" fill="#ffffff" stroke="#bcccdc"/>',
    ]
    if histogram:
        bar_width = plot_width / len(histogram)
        for index, (_lo, _hi, count) in enumerate(histogram):
            bar_h = 0 if max_bin == 0 else count / max_bin * hist_h
            x = left + index * bar_width + 1
            y = hist_y + hist_h - bar_h
            parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(0.5, bar_width-2):.2f}" height="{bar_h:.2f}" fill="#3e8ed0" opacity="0.82"/>')
        for raw_tick in nice_raw_ticks(max_hits, 7):
            x = left + math.log1p(raw_tick) / max_log * plot_width
            parts.append(f'<line x1="{x:.1f}" y1="{hist_y+hist_h}" x2="{x:.1f}" y2="{hist_y+hist_h+5}" stroke="#52606d"/>')
            parts.append(svg_text(x, hist_y + hist_h + 22, f"{raw_tick:,}", 10, anchor="middle"))
    for i in range(5):
        count = max_bin * i / 4
        y = hist_y + hist_h - hist_h * i / 4
        parts.append(svg_text(left - 10, y + 4, f"{count:,.0f}", 11, anchor="end"))
    parts.append(svg_text(left + plot_width / 2, hist_y + hist_h + 48,
                          "surface-hit records / file", 12, anchor="middle"))
    parts.append(svg_text(24, hist_y + hist_h / 2, "number of files", 12,
                          anchor="middle", rotate=-90))
    parts.append(svg_text(left, height - 18,
                          "Zero-hit files are valid observations, not missing data. Incomplete files are excluded above.",
                          11, fill="#627d98"))
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def overview_svg(summaries: list[dict[str, object]], output: Path) -> None:
    width = 1380
    row_h = 31
    top = 112
    height = top + row_h * len(summaries) + 70
    label_x, plot_x, plot_w = 42, 450, 690
    empty_x = 1225
    maximum = max((float(r["p95_rate_per_million"]) for r in summaries
                   if math.isfinite(float(r["p95_rate_per_million"]))), default=1.0)
    max_log = max(1.0, math.log1p(maximum))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfcfe"/>',
        svg_text(label_x, 38, "Hit-file diagnostics across experiment campaigns", 23, weight="bold", fill="#102a43"),
        svg_text(label_x, 65, "Dot = median; line = p05 to p95; normalized by each file’s primary-event count", 13, fill="#52606d"),
        svg_text(plot_x + plot_w/2, 91, "surface-hit records per million primaries (log1p scale)", 13, anchor="middle", weight="bold"),
        svg_text(empty_x, 91, "zero-hit files", 13, anchor="middle", weight="bold"),
    ]
    for raw_tick in nice_raw_ticks(int(math.ceil(maximum)), 7):
        x = plot_x + math.log1p(raw_tick) / max_log * plot_w
        parts.append(f'<line x1="{x:.1f}" y1="{top-10}" x2="{x:.1f}" y2="{height-45}" stroke="#e4e7eb"/>')
        parts.append(svg_text(x, height - 25, f"{raw_tick:,}", 10, anchor="middle"))
    for index, row in enumerate(summaries):
        y = top + index * row_h
        if index % 2:
            parts.append(f'<rect x="{label_x-8}" y="{y-row_h+7}" width="{width-label_x-24}" height="{row_h}" fill="#f0f4f8"/>')
        parts.append(svg_text(label_x, y, row["campaign"], 11))
        p05 = float(row["p05_rate_per_million"])
        med = float(row["median_rate_per_million"])
        p95 = float(row["p95_rate_per_million"])
        if all(math.isfinite(v) for v in (p05, med, p95)):
            x05 = plot_x + math.log1p(p05) / max_log * plot_w
            x50 = plot_x + math.log1p(med) / max_log * plot_w
            x95 = plot_x + math.log1p(p95) / max_log * plot_w
            parts.append(f'<line x1="{x05:.1f}" y1="{y-4}" x2="{x95:.1f}" y2="{y-4}" stroke="#486581" stroke-width="3"/>')
            parts.append(f'<circle cx="{x50:.1f}" cy="{y-4}" r="5" fill="#007c91"/>')
        parts.append(svg_text(empty_x, y, f'{float(row["empty_pct"]):.1f}%', 11, anchor="middle"))
        parts.append(svg_text(1340, y, f'n={int(row["completed_files"]):,}', 10, anchor="end", fill="#627d98"))
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def summarize(campaign: str, rows: list[dict[str, object]], incomplete: int) -> dict[str, object]:
    hits = [int(row["n_hits"]) for row in rows]
    rates = [int(row["n_hits"]) / int(row["events"]) * 1e6
             for row in rows if row["events"]]
    events = [int(row["events"]) for row in rows if row["events"]]
    return {
        "campaign": campaign,
        "completed_files": len(rows),
        "incomplete_files": incomplete,
        "database_counts": sum(row["source"] == "database" for row in rows),
        "disk_counts": sum(row["source"] == "disk" for row in rows),
        "events_per_file_min": min(events) if events else "",
        "events_per_file_max": max(events) if events else "",
        "total_hits": sum(hits),
        "empty_files": sum(value == 0 for value in hits),
        "empty_pct": 100 * sum(value == 0 for value in hits) / len(hits) if hits else float("nan"),
        "min_hits": min(hits) if hits else "",
        "p05_hits": percentile(hits, .05),
        "median_hits": percentile(hits, .5),
        "mean_hits": sum(hits) / len(hits) if hits else float("nan"),
        "p95_hits": percentile(hits, .95),
        "p99_hits": percentile(hits, .99),
        "max_hits": max(hits) if hits else "",
        "p05_rate_per_million": percentile(rates, .05),
        "median_rate_per_million": percentile(rates, .5),
        "p95_rate_per_million": percentile(rates, .95),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "hit_diagnostics_20260914")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    campaigns, incomplete, incomplete_trials = collect()
    summaries = []
    for campaign in sorted(set(campaigns) | set(incomplete)):
        rows = campaigns.get(campaign, [])
        if not rows:
            continue
        campaign_svg(campaign, rows, incomplete.get(campaign, 0),
                     args.output / f"{campaign}.svg")
        summaries.append(summarize(campaign, rows, incomplete.get(campaign, 0)))
    fields = list(summaries[0]) if summaries else []
    with (args.output / "hit_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    overview_svg(summaries, args.output / "overview.svg")

    # A campaign plot answers the common high-level question, while a trial
    # plot prevents different candidate physics from being mistaken for
    # sampling noise.  Generate both because a campaign can contain many
    # materially different candidates (and, during migrations, design hashes).
    trial_dir = args.output / "trials"
    trial_summaries = []
    for campaign, rows in sorted(campaigns.items()):
        by_trial: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            by_trial[str(row["trial_id"])].append(row)
        campaign_trial_dir = trial_dir / campaign
        campaign_trial_dir.mkdir(parents=True, exist_ok=True)
        for trial_id, trial_rows in sorted(by_trial.items()):
            missing = incomplete_trials.get((campaign, trial_id), 0)
            campaign_svg(f"{campaign} / {trial_id}", trial_rows, missing,
                         campaign_trial_dir / f"{trial_id}.svg")
            record = summarize(campaign, trial_rows, missing)
            record = {"campaign": campaign, "trial_id": trial_id,
                      **{key: value for key, value in record.items() if key != "campaign"}}
            trial_summaries.append(record)
    if trial_summaries:
        with (args.output / "trial_summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(trial_summaries[0]))
            writer.writeheader()
            writer.writerows(trial_summaries)
    write_index(summaries, args.output)
    print(f"Wrote {len(summaries)} campaign and {len(trial_summaries)} trial diagrams, "
          f"overview.svg, index.html, and CSV summaries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
