#!/usr/bin/env python3
"""Plot the matched integer-direction and continuous-sphere pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    document = json.loads(args.result.read_text(encoding="utf-8"))
    records = document["results"]
    groups = (
        ("integer_", "former 13 directions", "#3264a8"),
        ("continuous_", "16 uniform sphere points", "#d1495b"),
    )

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 5.0), constrained_layout=True)
    summaries = []
    for group_index, (prefix, label, color) in enumerate(groups):
        rows = sorted((key, value) for key, value in records.items()
                      if key.startswith(prefix))
        values = np.asarray([row[1]["value"] for row in rows], dtype=float)
        errors = np.asarray([row[1].get("se") or 0.0 for row in rows], dtype=float)
        x = np.arange(len(rows))
        axes[0].errorbar(x, values * 1e6, yerr=errors * 1e6, fmt="o",
                         color=color, ecolor=color, alpha=0.75, capsize=2,
                         label=label)
        jitter = np.linspace(-0.10, 0.10, len(values))
        axes[1].scatter(np.full(len(values), group_index) + jitter,
                        values * 1e6, s=36, color=color, alpha=0.85)
        axes[1].hlines(np.median(values) * 1e6, group_index - 0.22,
                       group_index + 0.22, color="black", linewidth=2)
        summaries.append((label, float(np.min(values)), float(np.median(values)),
                          float(np.max(values))))

    axes[0].set_title("Each tested direction")
    axes[0].set_xlabel("point index within each set")
    axes[0].set_ylabel("junction quasiparticles per primary × 10⁶")
    axes[0].grid(axis="y", color="#d9e2ec", linewidth=0.7)
    axes[0].legend(frameon=False)

    axes[1].set_title("Group spread; bar is the median")
    axes[1].set_xticks([0, 1], [item[1] for item in groups], rotation=10)
    axes[1].set_ylabel("junction quasiparticles per primary × 10⁶")
    axes[1].grid(axis="y", color="#d9e2ec", linewidth=0.7)

    reference = records["baseline"]["value"]
    fig.suptitle(
        "Crystal-direction pilot: 4,000,000 primaries per point, "
        f"reference = {reference:.4e}", fontsize=13
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180)
    plt.close(fig)

    for label, minimum, median, maximum in summaries:
        print(f"{label}: min={minimum:.6e}, median={median:.6e}, max={maximum:.6e}")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
