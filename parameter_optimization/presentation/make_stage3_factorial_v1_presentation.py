#!/usr/bin/env python3
"""Build the Stage 3 factorial-v1 results presentation and its figures.

Run from parameter_optimization with the G4CMP environment:

    conda run -n G4CMP python presentation/make_stage3_factorial_v1_presentation.py
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import patches
import numpy as np
import pandas as pd
import yaml
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent / "assets"
OUT = Path(__file__).resolve().parent / "STAGE3_FACTORIAL_V1_RESULTS_PRESENTATION.pptx"
CSV = ROOT / "results" / "factorial_v1.csv"
DB = ROOT / "stage3_trials.sqlite"
CONFIG = ROOT / "stage3_config.yaml"

NAVY = "10243E"
NAVY2 = "173B57"
TEAL = "00A6A6"
CYAN = "4CC9D8"
ORANGE = "F59E42"
RED = "D9534F"
GREEN = "41A36F"
PALE = "EDF4F7"
MID = "AFC4CF"
GRAY = "526573"
WHITE = "FFFFFF"
BLACK = "17242C"


def rgb(hex_value: str) -> RGBColor:
    return RGBColor.from_string(hex_value)


def load_data():
    df = pd.read_csv(CSV).sort_values("total_qps").reset_index(drop=True)
    df["pct_vs_baseline"] = 100 * (
        df.total_qps / float(df.loc[df.name == "Si/Nb/Cu", "total_qps"].iloc[0]) - 1
    )
    cfg = yaml.safe_load(CONFIG.read_text())
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    trials = list(con.execute("SELECT * FROM trials ORDER BY total_qps"))
    subruns = list(con.execute("SELECT * FROM sub_runs ORDER BY trial_id, replica, position"))
    scenario = json.loads(trials[0]["scenario"])
    per_electrode = {}
    for row in trials:
        c = json.loads(row["candidate"])
        name = f"{c['substrate']}/{c['top_ground_film']}/{c['bottom_film']}"
        per_electrode[name] = json.loads(row["per_electrode_qps"])
    con.close()
    return df, cfg, trials, subruns, scenario, per_electrode


def mpl_style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.titleweight": "bold",
        "axes.edgecolor": "#AFC4CF",
        "axes.labelcolor": "#17242C",
        "xtick.color": "#526573",
        "ytick.color": "#526573",
        "figure.facecolor": "white",
    })


def make_schematic(path: Path, cfg, scenario):
    mpl_style()
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.3, 6.2), gridspec_kw={"width_ratios": [1.2, 1]})
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)
    ax.axis("off")

    # Cross section, deliberately not to scale in z.
    ax.add_patch(patches.Rectangle((0.7, 0.8), 8.6, 0.45, color="#C99A42"))
    ax.text(5, 1.02, "Bottom normal film: Cu or Au (1 µm)", ha="center", va="center", color="white", weight="bold")
    ax.add_patch(patches.Rectangle((0.7, 1.25), 8.6, 3.8, color="#BFE3EA", ec="#4B8090", lw=1.5))
    ax.text(5, 2.05, "Single-crystal substrate\nSi / Ge / GaAs, 525 µm", ha="center", va="center", color="#173B57", fontsize=14, weight="bold")
    ax.add_patch(patches.Rectangle((0.7, 5.05), 8.6, 0.30, color="#617B88"))
    ax.text(5, 5.2, "Top superconducting ground film: Nb / Ta / Ti (75 nm)", ha="center", va="center", color="white", fontsize=10, weight="bold")
    for x in [1.4, 2.55, 3.7, 4.85, 6.0, 7.15, 8.3]:
        ax.add_patch(patches.Rectangle((x, 5.35), 0.55, 0.24, color="#F59E42", ec="#8F5320"))
    ax.text(5, 5.78, "17 fixed Al junction electrodes (120 nm)", ha="center", color="#8F5320", fontsize=11, weight="bold")

    ax.annotate("10 meV athermal phonon", xy=(3.0, 4.75), xytext=(1.2, 6.55),
                arrowprops=dict(arrowstyle="-|>", lw=2.8, color="#D9534F"), color="#D9534F", weight="bold")
    paths = [
        [(3.0, 4.75), (4.2, 3.9), (3.5, 2.8), (5.0, 1.4)],
        [(3.0, 4.75), (2.0, 3.7), (3.2, 2.6), (2.0, 1.4)],
        [(3.0, 4.75), (5.5, 3.7), (7.4, 4.6), (7.4, 5.05)],
    ]
    for pts, col in zip(paths, [TEAL, CYAN, ORANGE]):
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=f"#{col}", lw=2.1, alpha=0.9)
        for j in range(len(pts) - 1):
            ax.annotate("", xy=pts[j + 1], xytext=pts[j], arrowprops=dict(arrowstyle="->", color=f"#{col}", lw=1.5))
    ax.text(6.3, 3.0, "anisotropic propagation\n+ scattering + decay", color="#173B57", ha="center")
    ax.text(5.0, 0.35, "Interfaces determine absorption vs reflection; films downconvert energy", ha="center", color="#526573")

    # Top view: electrodes and Sobol injection sites.
    ax2.set_aspect("equal")
    ax2.set_xlim(-5.2, 5.2)
    ax2.set_ylim(-5.2, 5.2)
    ax2.add_patch(patches.Rectangle((-5, -5), 10, 10, fc="#EDF4F7", ec="#173B57", lw=2))
    xs = cfg["fixed"]["electrode_x_mm"]
    ys = cfg["fixed"]["electrode_y_mm"]
    ax2.scatter(xs, ys, marker="s", s=135, color="#F59E42", edgecolor="#8F5320", label="17 Al electrodes", zorder=4)
    sites = np.array(scenario["sites_mm"], dtype=float)
    ax2.scatter(sites[:, 0], sites[:, 1], s=46, color="#00A6A6", edgecolor="white", linewidth=0.7, label="16 Sobol injection sites", zorder=5)
    for i, (x, y, _) in enumerate(sites):
        if i in (0, 11):
            ax2.text(x + 0.13, y + 0.12, str(i), fontsize=9, color="#173B57", weight="bold")
    ax2.plot([sites[11, 0], xs[3]], [sites[11, 1], ys[3]], color="#D9534F", lw=2)
    ax2.scatter([sites[11, 0]], [sites[11, 1]], s=155, facecolor="none", edgecolor="#D9534F", linewidth=2.5, zorder=6)
    ax2.text(-4.75, 4.55, "10 × 10 mm chip", color="#173B57", weight="bold")
    ax2.text(-4.75, -4.65, "Site 11 is 0.119 mm from electrode 3", color="#D9534F", weight="bold", fontsize=10)
    ax2.set_xlabel("x (mm)")
    ax2.set_ylabel("y (mm)")
    ax2.legend(loc="upper right", frameon=False, fontsize=9)
    ax2.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def make_ranking(path: Path, df):
    mpl_style()
    plot = df.sort_values("total_qps", ascending=True).copy()
    fig, ax = plt.subplots(figsize=(11.8, 7.2))
    colors = ["#41A36F" if b == "Cu" else "#AFC4CF" for b in plot.bottom_film]
    bars = ax.barh(plot.name, plot.qps_per_primary * 1e4, color=colors, edgecolor="white")
    base = float(plot.loc[plot.name == "Si/Nb/Cu", "qps_per_primary"].iloc[0] * 1e4)
    ax.axvline(base, color="#F59E42", lw=2, ls="--", label="Si/Nb/Cu baseline")
    for bar, q, pct in zip(bars, plot.total_qps, plot.pct_vs_baseline):
        ax.text(bar.get_width() + 0.08, bar.get_y() + bar.get_height()/2,
                f"{int(q):,}  ({pct:+.1f}%)", va="center", fontsize=9, color="#17242C")
    ax.set_xlabel("QP per primary × 10⁴  (lower is better)")
    ax.set_title("Nominal 10 meV factorial ranking")
    ax.set_xlim(0, plot.qps_per_primary.max() * 1e4 * 1.28)
    ax.grid(axis="x", alpha=0.2)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(frameon=False, loc="lower right")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def make_effects(path: Path, df):
    mpl_style()
    fig, axes = plt.subplots(1, 3, figsize=(13.0, 4.2))
    orders = [("substrate", ["Ge", "GaAs", "Si"]), ("top_ground_film", ["Ti", "Nb", "Ta"]), ("bottom_film", ["Cu", "Au"])]
    titles = ["Substrate marginal mean", "Top-film marginal mean", "Bottom-film marginal mean"]
    for ax, (field, order), title in zip(axes, orders, titles):
        g = df.groupby(field).qps_per_primary.mean().reindex(order) * 1e4
        cols = ["#00A6A6" if i == 0 else "#AFC4CF" for i in range(len(g))]
        bars = ax.bar(g.index, g.values, color=cols)
        for b, v in zip(bars, g.values):
            ax.text(b.get_x()+b.get_width()/2, v+0.12, f"{v:.2f}", ha="center", fontsize=10, weight="bold")
        ax.set_title(title, fontsize=12)
        ax.set_ylabel("QP/primary × 10⁴" if ax is axes[0] else "")
        ax.grid(axis="y", alpha=0.2)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.set_ylim(0, max(g.values)*1.22)
    fig.suptitle("Marginal effects summarize the tested levels—not independent scalar causality", fontsize=15, weight="bold", color="#173B57")
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def make_interactions(path: Path, df):
    mpl_style()
    fig, axes = plt.subplots(1, 3, figsize=(13.0, 4.3), sharey=True)
    for ax, substrate in zip(axes, ["Si", "Ge", "GaAs"]):
        d = df[df.substrate == substrate]
        piv = d.pivot(index="top_ground_film", columns="bottom_film", values="qps_per_primary").reindex(["Nb", "Ta", "Ti"]) * 1e4
        ax.plot(piv.index, piv["Cu"], marker="o", lw=2.5, color="#41A36F", label="Cu bottom")
        ax.plot(piv.index, piv["Au"], marker="o", lw=2.5, color="#526573", label="Au bottom")
        ax.set_title(substrate, fontsize=14)
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xlabel("Top ground film")
    axes[0].set_ylabel("QP/primary × 10⁴")
    axes[2].legend(frameon=False)
    fig.suptitle("Top-film ordering depends on the bottom film", fontsize=15, weight="bold", color="#173B57")
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def score_subruns(trials):
    out = []
    for t in trials:
        c = json.loads(t["candidate"])
        name = f"{c['substrate']}/{c['top_ground_film']}/{c['bottom_film']}"
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        rows = list(con.execute("SELECT * FROM sub_runs WHERE trial_id=? ORDER BY replica, position", (t["trial_id"],)))
        con.close()
        for sr in rows:
            hits = pd.read_csv(sr["hits_file"])
            top = hits[(hits["Energy Deposited [eV]"] > 0) & np.isclose(hits["End Z [m]"], 0.0002625)]
            qp = float(np.rint(top["Energy Deposited [eV]"].to_numpy() / 0.000191).sum())
            out.append({"name": name, "position": sr["position"], "replica": sr["replica"], "qps": qp})
    return pd.DataFrame(out)


def make_spatial(path: Path, subdf, cfg, scenario, per_electrode):
    mpl_style()
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.0, 5.1), gridspec_kw={"width_ratios": [1.2, 1]})
    base = subdf[subdf.name == "Si/Nb/Cu"].groupby("position").qps.sum().reindex(range(16))
    colors = ["#D9534F" if i == 11 else "#00A6A6" for i in range(16)]
    ax.bar(range(16), base.values, color=colors)
    ax.set_xlabel("Sobol position index (two replicas summed)")
    ax.set_ylabel("QPs")
    ax.set_title("One site contributes 49.6% of baseline QPs")
    ax.text(11, base.loc[11] + 24, "782 QPs", ha="center", color="#D9534F", weight="bold")
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)

    xs = cfg["fixed"]["electrode_x_mm"]
    ys = cfg["fixed"]["electrode_y_mm"]
    q = np.asarray(per_electrode["Si/Nb/Cu"], dtype=float)
    sizes = 65 + 700 * q / q.max()
    ax2.set_aspect("equal")
    ax2.add_patch(patches.Rectangle((-5, -5), 10, 10, fc="#EDF4F7", ec="#173B57", lw=1.5))
    ax2.scatter(xs, ys, s=sizes, c=q, cmap="YlOrRd", edgecolor="#8F5320", zorder=3)
    sites = np.array(scenario["sites_mm"], dtype=float)
    ax2.scatter(sites[:, 0], sites[:, 1], marker="x", color="#173B57", s=28, label="injection sites")
    ax2.scatter([sites[11, 0]], [sites[11, 1]], s=170, facecolor="none", edgecolor="#D9534F", lw=2.5, zorder=5)
    ax2.plot([sites[11, 0], xs[3]], [sites[11, 1], ys[3]], color="#D9534F", lw=2)
    ax2.text(xs[3] + 0.15, ys[3] + 0.2, "electrode 3\n760 QPs", color="#8F5320", weight="bold", fontsize=9)
    ax2.set_xlim(-5.2, 5.2); ax2.set_ylim(-5.2, 5.2)
    ax2.set_xlabel("x (mm)"); ax2.set_ylabel("y (mm)")
    ax2.set_title("Direct-neighborhood aliasing")
    ax2.legend(frameon=False, loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def generate_figures(df, cfg, trials, scenario, per_electrode):
    ASSETS.mkdir(parents=True, exist_ok=True)
    make_schematic(ASSETS / "device_and_sites.png", cfg, scenario)
    make_ranking(ASSETS / "ranking.png", df)
    make_effects(ASSETS / "marginal_effects.png", df)
    make_interactions(ASSETS / "interactions.png", df)
    subdf = score_subruns(trials)
    make_spatial(ASSETS / "spatial_dominance.png", subdf, cfg, scenario, per_electrode)
    return subdf


def add_bg(slide, color=WHITE):
    shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    shape.fill.solid(); shape.fill.fore_color.rgb = rgb(color)
    shape.line.fill.background()
    slide.shapes._spTree.remove(shape._element)
    slide.shapes._spTree.insert(2, shape._element)


def textbox(slide, x, y, w, h, text="", size=18, color=BLACK, bold=False,
            font="Aptos", align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP, margin=0.05):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear(); tf.margin_left = tf.margin_right = Inches(margin); tf.margin_top = tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run(); run.text = text
    run.font.name = font; run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = rgb(color)
    return box


def title(slide, text, subtitle=None, dark=False):
    color = WHITE if dark else NAVY
    textbox(slide, 0.62, 0.34, 12.1, 0.48, text, 25, color, True)
    line = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0.65), Inches(0.94), Inches(1.05), Inches(0.06))
    line.fill.solid(); line.fill.fore_color.rgb = rgb(TEAL); line.line.fill.background()
    if subtitle:
        textbox(slide, 1.86, 0.82, 10.7, 0.32, subtitle, 10.5, PALE if dark else GRAY)


def footer(slide, page, source=None, dark=False):
    col = MID if dark else GRAY
    if source:
        textbox(slide, 0.65, 7.13, 11.7, 0.20, source, 7.5, col)
    textbox(slide, 12.35, 7.08, 0.35, 0.24, str(page), 8, col, False, align=PP_ALIGN.RIGHT)


def card(slide, x, y, w, h, heading, body, accent=TEAL, body_size=14):
    s = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid(); s.fill.fore_color.rgb = rgb(PALE); s.line.color.rgb = rgb(MID)
    strip = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(x), Inches(y), Inches(0.10), Inches(h))
    strip.fill.solid(); strip.fill.fore_color.rgb = rgb(accent); strip.line.fill.background()
    textbox(slide, x+0.24, y+0.15, w-0.38, 0.34, heading, 14, NAVY, True)
    textbox(slide, x+0.24, y+0.58, w-0.38, h-0.68, body, body_size, BLACK)
    return s


def bullets(slide, x, y, w, h, items, size=17, color=BLACK, bullet_color=None, spacing=8):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame; tf.clear(); tf.word_wrap = True
    tf.margin_left = Inches(0.05); tf.margin_right = Inches(0.03)
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = item; p.level = 0; p.font.name = "Aptos"; p.font.size = Pt(size); p.font.color.rgb = rgb(color)
        p.space_after = Pt(spacing); p.text = "•  " + p.text
    return box


def add_image(slide, path, x, y, w, h=None):
    return slide.shapes.add_picture(str(path), Inches(x), Inches(y), width=Inches(w), height=Inches(h) if h else None)


def workflow_slide(slide):
    steps = [
        ("1", "Candidate", "substrate + top + bottom"),
        ("2", "Resolver", "density, tensor, native lattice, interfaces"),
        ("3", "Scenario", "16 sites × 2 replicas\n125k events each"),
        ("4", "Geant4/G4CMP", "32 guarded sub-runs\nper candidate"),
        ("5", "Proof + ledger", "marker, material check,\nSQLite identities"),
        ("6", "Score", "Σ junction QPs /\n4M primaries"),
    ]
    xs = [0.55, 2.68, 4.81, 6.94, 9.07, 11.20]
    for i, ((num, head, body), x) in enumerate(zip(steps, xs)):
        s = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(x), Inches(2.18), Inches(1.58), Inches(2.08))
        s.fill.solid(); s.fill.fore_color.rgb = rgb(PALE); s.line.color.rgb = rgb(TEAL)
        circ = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, Inches(x+0.55), Inches(1.70), Inches(0.48), Inches(0.48))
        circ.fill.solid(); circ.fill.fore_color.rgb = rgb(TEAL); circ.line.fill.background()
        textbox(slide, x+0.55, 1.76, 0.48, 0.24, num, 12, WHITE, True, align=PP_ALIGN.CENTER)
        textbox(slide, x+0.12, 2.42, 1.34, 0.32, head, 13, NAVY, True, align=PP_ALIGN.CENTER)
        textbox(slide, x+0.12, 2.93, 1.34, 0.92, body, 10.5, GRAY, align=PP_ALIGN.CENTER)
        if i < len(steps)-1:
            conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x+1.60), Inches(3.18), Inches(xs[i+1]), Inches(3.18))
            conn.line.color.rgb = rgb(ORANGE); conn.line.width = Pt(2.5); conn.line.end_arrowhead = True
    card(slide, 1.0, 5.0, 3.4, 1.18, "Common comparison block", "Same ordered sites and nominal seed bank for every material triplet", GREEN, 12)
    card(slide, 4.95, 5.0, 3.4, 1.18, "Strict completeness", "A partial site set is never scored and a missing result is never zero", ORANGE, 12)
    card(slide, 8.9, 5.0, 3.4, 1.18, "Current search method", "Exhaustive 18-cell factorial; no Bayesian surrogate is needed yet", TEAL, 12)


def build_presentation(df, cfg, trials, subruns, scenario, per_electrode, subdf):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1 — title
    s = prs.slides.add_slide(blank); add_bg(s, NAVY)
    textbox(s, 0.75, 1.15, 11.8, 0.55, "Stage 3 Material Optimization", 31, WHITE, True)
    textbox(s, 0.75, 1.92, 11.8, 0.55, "Factorial-v1 results, physics interpretation, and readiness audit", 20, CYAN)
    textbox(s, 0.75, 3.0, 4.1, 1.15, "18 material triplets\n72 million primary events", 22, WHITE, True)
    textbox(s, 4.85, 3.0, 3.8, 1.15, "16 Sobol sites\n× 2 replicas", 22, WHITE, True)
    textbox(s, 8.7, 3.0, 3.8, 1.15, "Objective\ntotal junction QPs", 22, WHITE, True)
    textbox(s, 0.75, 5.55, 11.6, 0.54, "Outcome: technically complete; promising screen; not yet a final material decision", 19, ORANGE, True)
    textbox(s, 0.75, 6.45, 11.6, 0.32, "Reviewed 13 August 2026", 11, MID)
    footer(s, 1, dark=True)

    # 2 — why QPs
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Why minimize quasiparticle generation?")
    card(s, 0.72, 1.35, 3.65, 4.8, "1  Energy deposition", "Ionizing radiation, stress-release events, or injected athermal phonons deposit energy into the cryogenic chip substrate.", RED, 17)
    card(s, 4.83, 1.35, 3.65, 4.8, "2  Phonon cascade", "Athermal acoustic phonons propagate anisotropically, scatter, decay, and repeatedly encounter device interfaces.", TEAL, 17)
    card(s, 8.94, 1.35, 3.65, 4.8, "3  Correlated qubit damage", "Pair-breaking phonons create nonequilibrium QPs in superconducting structures. QPs shorten T₁ and can correlate errors across many qubits.", ORANGE, 17)
    textbox(s, 1.0, 6.42, 11.3, 0.38, "Stage 3 asks which realizable layer triplet redirects or downconverts phonon energy before it reaches the Al junctions.", 16, NAVY, True, align=PP_ALIGN.CENTER)
    footer(s, 2, "Context: Martinis, npj Quantum Information 7, 90 (2021); Wilen et al., Nature Communications 13, 6425 (2022).")

    # 3 — setup schematic
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "What was simulated", "Cross-section and top-view geometry")
    add_image(s, ASSETS / "device_and_sites.png", 0.55, 1.13, 12.25, 5.75)
    footer(s, 3, "Illustration generated from stage3_config.yaml and the SQLite-recorded Sobol scenario; vertical thicknesses are schematic.")

    # 4 — energy protocol
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Energy protocol: a deliberate all-films-active regime")
    textbox(s, 0.85, 1.32, 11.7, 0.55, "38.2 µeV  <  382 µeV  <  10,000 µeV", 28, NAVY, True, align=PP_ALIGN.CENTER)
    textbox(s, 0.85, 1.90, 11.7, 0.34, "tracking cutoff       Al junction pair threshold       injected phonon", 13, GRAY, align=PP_ALIGN.CENTER)
    # Energy axis
    x0, x1, y = 1.0, 12.2, 3.18
    line = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x0), Inches(y), Inches(x1), Inches(y)); line.line.color.rgb = rgb(NAVY); line.line.width = Pt(3)
    energies = [(38.2, "cutoff", TEAL), (122, "Ti 2Δ", ORANGE), (382, "Al 2Δ", RED), (1400, "Ta 2Δ", ORANGE), (3076.8, "Nb 2Δ", ORANGE), (10000, "gun", NAVY)]
    lo, hi = math.log10(30), math.log10(12000)
    for e, lab, col in energies:
        xx = x0 + (math.log10(e)-lo)/(hi-lo)*(x1-x0)
        tick = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(xx), Inches(y-0.16), Inches(xx), Inches(y+0.20)); tick.line.color.rgb = rgb(col); tick.line.width = Pt(2.5)
        textbox(s, xx-0.43, y+0.27, 0.86, 0.55, f"{lab}\n{e:g} µeV", 9.5, col, True, align=PP_ALIGN.CENTER)
    card(s, 0.8, 4.55, 3.65, 1.35, "What this guarantees", "All three top films can compete for incident phonons; the comparison stays in one qualitative absorption regime.", GREEN, 13)
    card(s, 4.83, 4.55, 3.65, 1.35, "What remains junction-only", "The Junction hit filter records energy at the 17 Al junction footprints—not QPs generated in the ground plane.", TEAL, 13)
    card(s, 8.86, 4.55, 3.65, 1.35, "Interpretation boundary", "These results describe 10 meV injection. They do not directly predict the former 1 meV protocol.", ORANGE, 13)
    footer(s, 4, "G4CMP transport context: Agnese et al., arXiv:2302.05998 / FERMILAB-PUB-23-065-ND.")

    # 5 — workflow
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Campaign workflow and data contract")
    workflow_slide(s)
    footer(s, 5)

    # 6 — audit
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Artifact audit: the recorded campaign is complete")
    stats = [
        ("18 / 18", "trials successful"), ("576 / 576", "sub-runs successful"),
        ("576", "macros + hits + markers + logs"), ("0", "score mismatches"),
    ]
    for i, (big, small) in enumerate(stats):
        x = 0.72 + i*3.14
        card(s, x, 1.35, 2.75, 1.35, big, small, GREEN if i < 3 else TEAL, 13)
    bullets(s, 0.9, 3.15, 5.75, 2.8, [
        "All candidates use the same ordered scenario and seed map",
        "Runtime logs match the intended substrate and Geant4 density",
        "Generated lattice files preserve every native field except derived vL/vT",
        "Independent rescoring reproduces every SQLite total",
    ], 15)
    bullets(s, 6.9, 3.15, 5.55, 2.8, [
        "CSV rebuilt byte-for-byte from the current single-campaign ledger",
        "Each candidate: 32 × 125,000 = 4,000,000 primaries",
        "Total executed: 72,000,000 primaries",
        "Correction: the database contains 576 sub-runs, not 288",
    ], 15)
    footer(s, 6, "Audit details: STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md")

    # 7 — ranking
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Nominal ranking", "Green bars use Cu; gray bars use Au")
    add_image(s, ASSETS / "ranking.png", 0.65, 1.08, 12.05, 5.92)
    footer(s, 7, "Percentages are relative to Si/Nb/Cu. The printed Poisson z values are not used as confidence intervals here.")

    # 8 — effects
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Factor-level interpretation")
    add_image(s, ASSETS / "marginal_effects.png", 0.55, 1.16, 12.2, 3.88)
    card(s, 0.75, 5.35, 3.7, 1.15, "Strongest robust signal", "Au is worse than Cu in all 9 matched comparisons; mean yield is 2.14× higher.", GREEN, 12.5)
    card(s, 4.82, 5.35, 3.7, 1.15, "Substrate screen", "Ge and GaAs beat Si in all 6 matched film combinations.", TEAL, 12.5)
    card(s, 8.89, 5.35, 3.7, 1.15, "Causal caution", "Each material changes several linked properties; this is not scalar-parameter sensitivity analysis.", ORANGE, 12.5)
    footer(s, 8)

    # 9 — interaction
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "The top-film effect is context dependent")
    add_image(s, ASSETS / "interactions.png", 0.62, 1.22, 12.0, 4.05)
    textbox(s, 0.95, 5.62, 11.4, 0.60, "Nb is best with Cu for all three substrates; Ti is best with Au for all three. A single global “best top film” does not exist in this model.", 17, NAVY, True, align=PP_ALIGN.CENTER)
    textbox(s, 1.2, 6.36, 10.9, 0.34, "This interaction can arise from gap, lifetime, sound speed, density, and interface absorption moving together.", 13, GRAY, align=PP_ALIGN.CENTER)
    footer(s, 9)

    # 10 — spatial convergence
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Why 16 → 32 → 64 position convergence cannot be skipped")
    add_image(s, ASSETS / "spatial_dominance.png", 0.55, 1.14, 12.2, 4.80)
    card(s, 0.9, 6.08, 3.65, 0.78, "Position 11", "782 / 1578 baseline QPs = 49.6%", RED, 11.5)
    card(s, 4.85, 6.08, 3.65, 0.78, "Winner gap", "GaAs/Nb/Cu beats Ge/Nb/Cu by only 18 QPs", ORANGE, 11.5)
    card(s, 8.80, 6.08, 3.65, 0.78, "Decision", "Treat GaAs and Ge as tied until nested convergence", TEAL, 11.5)
    footer(s, 10)

    # 11 — code audit blockers
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Implementation review: what must change before new campaigns")
    card(s, 0.72, 1.25, 5.95, 2.1, "BLOCKER 1 — incomplete cache identity", "The cache omits the catalog, interface code, macro template, and several resolved film values. Changing Ta lifetime 0.0227 → 0.040 ns changes the macro but not the current cache payload. Lifetime brackets can silently return nominal results.", RED, 14)
    card(s, 6.82, 1.25, 5.78, 2.1, "BLOCKER 2 — unfiltered reporting", "stage3_report.py reads every observation in the ledger. Once 32/64-position or lifetime-bracket trials are added, one CSV can mix incompatible contracts unless campaign/contract/fidelity filters are required.", RED, 14)
    card(s, 0.72, 3.68, 3.75, 2.15, "Model caveat", "Effective AMM reproduces 0.795/0.745/0.736 by construction. It is calibrated plumbing, not independent interface-physics validation.", ORANGE, 13)
    card(s, 4.78, 3.68, 3.75, 2.15, "Threshold mismatch", "Bottom gapThreshold is 180 µeV, while the Al gap is 191 µeV. The local normal-film model uses this termination scale. Prefer 191 µeV unless 180 is explicitly calibrated.", ORANGE, 13)
    card(s, 8.84, 3.68, 3.75, 2.15, "Input uncertainty", "Au lifetime is unsourced; Ta/Ti are supplied estimates. Low/nominal/high brackets are required before materials claims.", ORANGE, 13)
    textbox(s, 0.9, 6.25, 11.5, 0.42, "These issues do not erase factorial-v1: its actual macros and outputs were audited directly. They do block trustworthy reuse and extension.", 15, NAVY, True, align=PP_ALIGN.CENTER)
    footer(s, 11)

    # 12 — roadmap
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Recommended next sequence")
    left = [
        "1  Archive factorial-v1 as nominal / 10 meV / 16 sites / 2 replicas",
        "2  Fix cache identity; add cache-invalidation regression tests",
        "3  Add campaign + contract + fidelity filters to the report",
        "4  Resolve bottom threshold: 191 µeV recommended",
        "5  Run nested 32-site finalists + one Au control",
    ]
    right = [
        "6  Extend surviving candidates to 64 sites",
        "7  Add seed banks / replicas and matched-block uncertainty",
        "8  Run low / nominal / high lifetime brackets",
        "9  Test alternate or measured interface physics",
        "10 Confirm finalists at high event fidelity",
    ]
    bullets(s, 0.82, 1.28, 5.85, 4.9, left, 16, spacing=13)
    bullets(s, 6.88, 1.28, 5.55, 4.9, right, 16, spacing=13)
    textbox(s, 0.9, 6.35, 11.5, 0.38, "Only expand to Bayesian optimization when the candidate space becomes larger than an exhaustive budget.", 17, TEAL, True, align=PP_ALIGN.CENTER)
    footer(s, 12)

    # 13 — conclusion
    s = prs.slides.add_slide(blank); add_bg(s, NAVY); title(s, "Take-home message", dark=True)
    textbox(s, 0.82, 1.45, 11.7, 0.72, "The run is real, complete, and reproducible.", 29, WHITE, True, align=PP_ALIGN.CENTER)
    textbox(s, 1.1, 2.55, 11.1, 0.75, "Cu-bottom candidates are consistently favored in the implemented model.", 22, CYAN, True, align=PP_ALIGN.CENTER)
    textbox(s, 1.1, 3.62, 11.1, 0.75, "GaAs/Nb/Cu is the nominal winner—but effectively tied with Ge/Nb/Cu.", 22, ORANGE, True, align=PP_ALIGN.CENTER)
    textbox(s, 1.1, 4.70, 11.1, 0.90, "Fix cache/report identity, then establish spatial, lifetime, interface, and high-fidelity stability before selecting a material stack.", 20, WHITE, True, align=PP_ALIGN.CENTER)
    footer(s, 13, dark=True)

    # 14 — appendix table and references
    s = prs.slides.add_slide(blank); add_bg(s); title(s, "Appendix: scope and sources")
    card(s, 0.72, 1.25, 3.85, 2.05, "Objective scope", "Sum of rounded Edep/ΔAl for positive top-surface hits, assigned to the nearest of 17 electrodes and normalized by all primaries. Not PLE, peak-QP, or time-resolved correlated damage.", TEAL, 12.5)
    card(s, 4.75, 1.25, 3.85, 2.05, "Artifact scope", "SQLite: 18 trial rows + 576 sub-run rows. CSV: 18-row per-trial projection. Runs: exact macros, native configs, hit files, markers, and logs.", GREEN, 12.5)
    card(s, 8.78, 1.25, 3.85, 2.05, "Claim scope", "A model-dependent screening result for a 10 meV phonon source and the fixed 17-electrode geometry—not an experimentally validated material ranking.", ORANGE, 12.5)
    refs = (
        "Agnese et al., G4CMP: Condensed Matter Physics Simulation Using the Geant4 Toolkit, arXiv:2302.05998.\n\n"
        "Martinis, Saving superconducting quantum processors from decay and correlated errors generated by gamma and cosmic rays, npj Quantum Information 7, 90 (2021).\n\n"
        "McEwen et al., Resolving catastrophic error bursts from cosmic rays in large arrays of superconducting qubits, Nature Physics 18, 107–111 (2022).\n\n"
        "Wilen et al., Phonon downconversion to suppress correlated errors in superconducting qubits, Nature Communications 13, 6425 (2022)."
    )
    textbox(s, 0.85, 3.72, 11.7, 2.55, refs, 12.5, BLACK)
    footer(s, 14, "Full numerical audit and ranking: STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md")

    prs.core_properties.title = "Stage 3 Material Optimization — factorial-v1 results"
    prs.core_properties.subject = "QP-minimizing material stack screening with Geant4/G4CMP"
    prs.core_properties.author = "Stage 3 project team"
    prs.core_properties.comments = "Generated from stage3_trials.sqlite and results/factorial_v1.csv"
    prs.save(OUT)
    return prs


def validate_presentation(path: Path, expected_slides: int):
    check = Presentation(path)
    assert len(check.slides) == expected_slides, (len(check.slides), expected_slides)
    assert path.stat().st_size > 100_000
    titles = []
    for slide in check.slides:
        texts = [shape.text.strip() for shape in slide.shapes if hasattr(shape, "text_frame") and shape.text.strip()]
        titles.append(texts[0] if texts else "")
    return titles


def main():
    df, cfg, trials, subruns, scenario, per_electrode = load_data()
    assert len(df) == 18
    assert len(trials) == 18
    assert len(subruns) == 576
    subdf = generate_figures(df, cfg, trials, scenario, per_electrode)
    assert len(subdf) == 576
    prs = build_presentation(df, cfg, trials, subruns, scenario, per_electrode, subdf)
    titles = validate_presentation(OUT, len(prs.slides))
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes, {len(titles)} slides)")
    for i, heading in enumerate(titles, 1):
        print(f"  {i:02d} {heading[:90]}")


if __name__ == "__main__":
    main()

