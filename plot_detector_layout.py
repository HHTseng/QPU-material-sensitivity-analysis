"""
Plots the Geant4/G4CMP simulation-configuration layout for this device: the 17
electrode (qubit) sites read straight from the macro template's
setXLocations/setYLocations, overlaid with the 16 scrambled-Sobol phonon
injection sites stage1_run_simulations.py uses for the reference protocol
(N_POSITIONS=16, SENSITIVITY_POSITION_SEED, SENSITIVITY_POSITION_HALF_SPAN_MM).

The injection sites are regenerated with the identical scipy.stats.qmc.Sobol
call stage1_run_simulations.py.build_source_positions() makes, so this figure
is a faithful picture of "what stage 1 actually launches phonons at" -- not an
approximation.

Usage:
    python plot_detector_layout.py [--out detector_layout_electrodes_injection_sites.png]
"""

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from scipy.stats import qmc

# --------------------------------------------------------------------------- #
# Electrode (qubit-island) positions, mm -- from
# sensitivity_template_beamOn1e5.mac's setXLocations/setYLocations. These are
# detectors, present in every run regardless of injection-site count.
# --------------------------------------------------------------------------- #
ELECTRODE_X = [-2, 0, 2, -2, 0, 2, -2, 0, 2, 1, -1, 1, -1, -3, 1, -1, 3]
ELECTRODE_Y = [-2, -2, -2, 0, 0, 0, 2, 2, 2, -3, -1, 1, 3, -1, -1, 1, 1]

# Chip substrate footprint, mm -- /main/detector_param/setSubWidth/setSubHeight.
CHIP_WIDTH_MM = 10.0
CHIP_HEIGHT_MM = 10.0

# Injection-site protocol -- must match stage1_run_simulations.py's defaults
# (SENSITIVITY_N_POSITIONS, SENSITIVITY_POSITION_SEED,
# SENSITIVITY_POSITION_HALF_SPAN_MM) for this figure to be faithful.
N_POSITIONS = 16
POSITION_SEED = 20260727
POSITION_HALF_SPAN_MM = 4.0

# Reference-palette categorical slots (dataviz skill): slot 1 blue = detectors
# (fixed, present every run), slot 2 orange = injection sites (the swept
# stimulus). Different marker shapes too, so identity never rests on color alone.
COLOR_ELECTRODE = "#2a78d6"
COLOR_INJECTION = "#eb6834"
COLOR_TEXT_PRIMARY = "#0b0b0b"
COLOR_TEXT_SECONDARY = "#52514e"
COLOR_SURFACE = "#fcfcfb"


def build_injection_sites():
    """Same call as stage1_run_simulations.py's build_source_positions(),
    lateral (x, y) only -- z is fixed at the template's surface value and is
    irrelevant to this 2D layout."""
    engine = qmc.Sobol(d=2, scramble=True, seed=POSITION_SEED)
    unit = engine.random(N_POSITIONS)
    span = POSITION_HALF_SPAN_MM
    return (
        [round(float(u[0]) * 2 * span - span, 6) for u in unit],
        [round(float(u[1]) * 2 * span - span, 6) for u in unit],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="detector_layout_electrodes_injection_sites.png",
        help="Output PNG path (default: %(default)s)",
    )
    args = parser.parse_args()

    inj_x, inj_y = build_injection_sites()

    fig, ax = plt.subplots(figsize=(7.5, 7.5), dpi=150)
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    # Chip substrate outline, centered at the origin.
    ax.add_patch(
        Rectangle(
            (-CHIP_WIDTH_MM / 2, -CHIP_HEIGHT_MM / 2),
            CHIP_WIDTH_MM,
            CHIP_HEIGHT_MM,
            fill=False,
            edgecolor=COLOR_TEXT_SECONDARY,
            linewidth=1.5,
            linestyle="--",
            zorder=1,
        )
    )

    # Injection sites drawn first (below electrodes), with a soft halo evoking
    # the phonon caustic they launch.
    ax.scatter(
        inj_x,
        inj_y,
        s=220,
        marker="*",
        facecolor=COLOR_INJECTION,
        edgecolor=COLOR_SURFACE,
        linewidth=0.8,
        alpha=0.9,
        zorder=3,
        label=f"Sobol phonon injection site (n={N_POSITIONS})",
    )
    for i, (x, y) in enumerate(zip(inj_x, inj_y)):
        ax.annotate(
            str(i),
            (x, y),
            textcoords="offset points",
            xytext=(0, 9),
            ha="center",
            fontsize=7,
            color=COLOR_INJECTION,
        )

    # Electrodes: squares, since they are physical islands, not point stimuli.
    ax.scatter(
        ELECTRODE_X,
        ELECTRODE_Y,
        s=170,
        marker="s",
        facecolor=COLOR_ELECTRODE,
        edgecolor=COLOR_SURFACE,
        linewidth=0.8,
        zorder=4,
        label=f"electrode / qubit island (n={len(ELECTRODE_X)})",
    )
    for i, (x, y) in enumerate(zip(ELECTRODE_X, ELECTRODE_Y)):
        ax.annotate(
            str(i),
            (x, y),
            textcoords="offset points",
            xytext=(0, -13),
            ha="center",
            fontsize=7,
            color=COLOR_ELECTRODE,
            fontweight="bold",
        )

    ax.set_xlim(-CHIP_WIDTH_MM / 2 - 0.5, CHIP_WIDTH_MM / 2 + 0.5)
    ax.set_ylim(-CHIP_HEIGHT_MM / 2 - 0.5, CHIP_HEIGHT_MM / 2 + 0.5)
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]", color=COLOR_TEXT_PRIMARY)
    ax.set_ylabel("y [mm]", color=COLOR_TEXT_PRIMARY)
    ax.set_title(
        "Detector layout: 17 electrodes vs. 16 Sobol phonon injection sites",
        color=COLOR_TEXT_PRIMARY,
        fontsize=13,
        pad=12,
    )
    ax.tick_params(colors=COLOR_TEXT_SECONDARY)
    for spine in ax.spines.values():
        spine.set_color(COLOR_TEXT_SECONDARY)
    ax.grid(True, color=COLOR_TEXT_SECONDARY, alpha=0.15, linewidth=0.6)
    legend = ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), frameon=False)
    for text in legend.get_texts():
        text.set_color(COLOR_TEXT_PRIMARY)

    fig.tight_layout()
    fig.savefig(args.out, facecolor=COLOR_SURFACE, bbox_inches="tight")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
