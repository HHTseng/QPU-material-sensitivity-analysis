"""Figure for the stage-3 screening results.

Three panels, telling the screen's argument in order:

  A (left)      Ranked mu* for all 53 design variables, with bootstrap CIs, the
                dummy-parameter noise threshold, and the six dummies shown in
                place. Reading the dummies interleaved with the real parameters
                is the point: it shows *where* the noise floor sits rather than
                asserting it.
  B (top right) The noise floor itself -- total_QPs across 200 IDENTICAL
                configurations -- against the Poisson distribution with the same
                mean. The excess width is the compound-Poisson Fano factor.
  C (bot right) The canonical Morris mu*-sigma plane. sigma >> mu* means the
                effect is non-linear or interaction-driven, so a parameter's
                position off the diagonal says how it acts, not just how much.

Usage:
    python stage3_plot_results.py --results-dir results/<screen_run> \
                                  --noise-floor-dir results/<noise_floor_run>
"""

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from scipy import stats

# Palette roles (validated default instance; slots 1 and 2 are the documented
# all-pairs-safe opening of the categorical order). Text never wears the series
# colour -- a coloured mark beside it carries identity.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
C_SIGNIFICANT = "#2a78d6"   # slot 1, blue
C_DUMMY = "#eb6834"         # slot 2, orange
C_BELOW = "#b9b7ae"         # de-emphasised neutral: not a series, a non-result


def shorten(name):
    """Drop the Geant4 command path so the axis reads as parameter names."""
    return (name.replace("/main/detector_param/", "")
                .replace("/main/electrode_param/", "")
                .replace("/main/gun/", "gun ")
                .replace("/g4cmp/", "g4cmp ")
                .strip())


def panel_ranking(ax, table, threshold):
    d = table.sort_values("mu_star", ascending=True).reset_index(drop=True)
    colors = [C_DUMMY if r.is_dummy else (C_SIGNIFICANT if r.significant else C_BELOW)
              for r in d.itertuples()]
    y = np.arange(len(d))
    err = np.vstack([d.mu_star - d.mu_star_lo, d.mu_star_hi - d.mu_star])

    # Dummies carry a texture as well as a hue, so identity is never colour-alone
    # and survives greyscale/CVD. Drawn as hatch on the bar rather than a glyph in
    # the margin, which collided with the tick labels.
    hatches = ["///" if r.is_dummy else "" for r in d.itertuples()]
    bars = ax.barh(y, d.mu_star, height=0.68, color=colors, zorder=3)
    for bar, hatch in zip(bars, hatches):
        if hatch:
            bar.set_hatch(hatch)
            bar.set_edgecolor(SURFACE)
            bar.set_linewidth(0.0)
    ax.errorbar(d.mu_star, y, xerr=err, fmt="none", ecolor=TEXT_SECONDARY,
                elinewidth=1.0, capsize=2.0, alpha=0.7, zorder=4)

    ax.axvline(threshold, color=C_DUMMY, lw=1.6, ls="--", zorder=5)
    ax.text(threshold, len(d) - 0.2, f"  noise floor {threshold:.3f}",
            color=C_DUMMY, fontsize=8, va="top", ha="left", zorder=6)

    ax.set_yticks(y)
    ax.set_yticklabels([shorten(n) for n in d.parameter], fontsize=7.5, color=TEXT_SECONDARY)
    for tick, is_dummy in zip(ax.get_yticklabels(), d.is_dummy):
        if is_dummy:
            tick.set_color(C_DUMMY)
            tick.set_fontweight("bold")
    ax.set_xlabel("μ*  —  mean |fractional change in QP yield| per full-range sweep",
                  fontsize=9, color=TEXT_SECONDARY)
    ax.set_title("A  Parameter ranking, all 53 design variables",
                 fontsize=10.5, color=TEXT_PRIMARY, loc="left", pad=8)
    from matplotlib.patches import Patch
    leg = ax.legend(handles=[
        Patch(facecolor=C_SIGNIFICANT, label="clears noise floor"),
        Patch(facecolor=C_BELOW, label="below noise floor"),
        Patch(facecolor=C_DUMMY, hatch="///", edgecolor=SURFACE, label="QPDE dummy control"),
    ], fontsize=8, frameon=False, loc="lower right")
    for t in leg.get_texts():
        t.set_color(TEXT_SECONDARY)
    ax.set_ylim(-1, len(d))
    ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=TEXT_MUTED, length=0)


def panel_noise_floor(ax, counts):
    lam = counts.mean()
    sigma = counts.std(ddof=1)
    fano = sigma ** 2 / lam

    bins = np.arange(counts.min() - 5, counts.max() + 7, 5)
    ax.hist(counts, bins=bins, color=C_SIGNIFICANT, alpha=0.85, zorder=3,
            label=f"measured  (σ={sigma:.1f})")
    # Poisson with the same mean, scaled to the same total area.
    x = np.arange(max(0, int(lam - 5 * np.sqrt(lam))), int(lam + 5 * np.sqrt(lam)))
    pois = stats.poisson.pmf(x, lam) * len(counts) * (bins[1] - bins[0])
    ax.plot(x, pois, color=C_DUMMY, lw=2.0, zorder=4,
            label=f"Poisson, same mean  (σ={np.sqrt(lam):.1f})")

    ax.axvline(lam, color=TEXT_SECONDARY, lw=1.2, ls=":", zorder=5)
    ax.set_xlabel("total_QPs per design point", fontsize=9, color=TEXT_SECONDARY)
    ax.set_ylabel("design points", fontsize=9, color=TEXT_SECONDARY)
    ax.set_title(f"B  Noise floor: 200 identical configurations  (Fano {fano:.2f})",
                 fontsize=10.5, color=TEXT_PRIMARY, loc="left", pad=8)
    leg = ax.legend(fontsize=8, frameon=False, loc="upper right")
    for t in leg.get_texts():
        t.set_color(TEXT_SECONDARY)
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=TEXT_MUTED, labelsize=8)


def panel_mustar_sigma(ax, table, threshold):
    for subset, color, marker, label in (
        (table[table.is_dummy], C_DUMMY, "D", "QPDE dummy control"),
        (table[~table.is_dummy & ~table.significant], C_BELOW, "o", "below noise floor"),
        (table[~table.is_dummy & table.significant], C_SIGNIFICANT, "o", "significant"),
    ):
        if len(subset):
            ax.scatter(subset.mu_star, subset.sigma, s=46, c=color, marker=marker,
                       edgecolors=SURFACE, linewidths=1.2, zorder=4, label=label)

    xlim = table.mu_star.max() * 1.18
    ylim = table.sigma.max() * 1.12
    diag = min(xlim, ylim)
    ax.plot([0, diag], [0, diag], color=BASELINE, lw=1.2, ls="--", zorder=2)
    ax.text(diag * 0.98, diag * 0.94, "σ = μ*", fontsize=8, color=TEXT_MUTED,
            ha="right", va="top")
    ax.axvline(threshold, color=C_DUMMY, lw=1.4, ls="--", zorder=3)
    ax.text(threshold, ylim * 0.99, " noise floor", fontsize=8, color=C_DUMMY,
            ha="left", va="top")

    # Direct-label only the leaders, never every point; alternate the offset so
    # the tight cluster near mu* ~ 0.8-1.2 does not overprint itself.
    offsets = [(7, 4), (7, -11), (-7, 6), (7, 4), (7, 7), (-7, -12)]
    for (r, off) in zip(table.nlargest(6, "mu_star").itertuples(), offsets):
        ax.annotate(shorten(r.parameter), (r.mu_star, r.sigma), fontsize=7.5,
                    color=TEXT_SECONDARY, xytext=off, textcoords="offset points",
                    ha="right" if off[0] < 0 else "left")

    ax.set_xlabel("μ*  (overall influence)", fontsize=9, color=TEXT_SECONDARY)
    ax.set_ylabel("σ  (non-linearity / interaction)", fontsize=9, color=TEXT_SECONDARY)
    ax.set_title("C  Morris μ*–σ plane", fontsize=10.5, color=TEXT_PRIMARY, loc="left", pad=8)
    ax.set_xlim(0, xlim)
    ax.set_ylim(0, ylim)
    leg = ax.legend(fontsize=8, frameon=False, loc="lower right")
    for t in leg.get_texts():
        t.set_color(TEXT_SECONDARY)
    ax.grid(color=GRID, lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=TEXT_MUTED, labelsize=8)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, required=True,
                    help="screen run containing stage3_morris_screen.csv")
    ap.add_argument("--noise-floor-dir", type=Path, required=True,
                    help="noise-floor run containing qp_summary.csv")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    table = pd.read_csv(os.path.join(args.results_dir, "stage3_morris_screen.csv"))
    counts = pd.read_csv(os.path.join(args.noise_floor_dir, "qp_summary.csv"))["total_QPs"].to_numpy(float)
    threshold = table.loc[table.is_dummy, "mu_star"].max()

    fig = plt.figure(figsize=(15.5, 11.5), facecolor=SURFACE)
    gs = GridSpec(2, 2, figure=fig, width_ratios=[1.15, 1.0], height_ratios=[1.0, 1.15],
                  wspace=0.28, hspace=0.30, left=0.155, right=0.975, top=0.895, bottom=0.065)
    ax_rank = fig.add_subplot(gs[:, 0])
    ax_noise = fig.add_subplot(gs[0, 1])
    ax_plane = fig.add_subplot(gs[1, 1])
    for ax in (ax_rank, ax_noise, ax_plane):
        ax.set_facecolor(SURFACE)

    panel_ranking(ax_rank, table, threshold)
    panel_noise_floor(ax_noise, counts)
    panel_mustar_sigma(ax_plane, table, threshold)

    n_sig = int((table.significant & ~table.is_dummy).sum())
    n_phys = int((~table.is_dummy).sum())
    fig.suptitle("Stage-3 Morris screen — QP yield per event", fontsize=14,
                 color=TEXT_PRIMARY, x=0.012, ha="left", y=0.972)
    fig.text(0.012, 0.936,
             f"T=128 trajectories · 6912 design points × 2 replicas × 16 source positions · "
             f"4×10⁶ events per point · {n_sig} of {n_phys} physical parameters clear the "
             f"dummy-calibrated noise floor",
             fontsize=9.5, color=TEXT_SECONDARY, ha="left")

    out = args.out or os.path.join(args.results_dir, "stage3_screen_results.png")
    fig.savefig(out, dpi=170, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
