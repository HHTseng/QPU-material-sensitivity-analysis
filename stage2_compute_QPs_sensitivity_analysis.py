"""
Stage 2b: parameter-vs-outcome correlation analysis for a Morris run,
reproducing Paul Baity's SensitivityAnalysis.ipynb (cells In[3]/In[5]).
`--method` selects one or both analyses.

  method="integrated" (default; Paul's experiment-free cell In[5]/In[7]):
    Reduce each sample to one scalar -- log10 of the time-integrated decoherence
    summed over all electrodes (exactly `total_integrated_DG` from
    stage2_compute_QPs.py) -- then take the Pearson correlation (np.corrcoef) of
    that scalar against every model parameter and plot it (the In[5] figure).
    Needs no experimental data and no per-electrode mapping, so it is the
    physically clean choice for "which parameters drive QP / decoherence".

  method="chi2" (faithful cell In[3]; REQUIRES per-electrode experimental data):
    For every sample and every electrode, compute a chi^2 goodness-of-fit
    *distance* between that electrode's simulated decoherence curve DG_e(t) and
    a REAL measured Delta-Gamma-vs-delay curve for that same electrode location
    (see chi2_one_curve), then Pearson-correlate each electrode's chi^2 against
    every parameter. "chi^2 correlation" == "Pearson correlation of the chi^2
    values", not a different correlation formula.

    NO RECONCILIATION. This method needs one measured reference curve *per
    electrode* of the device being simulated, index-aligned to the electrodes
    (curve e is the measurement at electrode e's location). It deliberately does
    NOT map a small set of measured curves onto more electrodes: a measurement
    made at one location is not a valid target for a different location, and
    Paul's 6-qubit NbGND data is a *different device* from this 17-electrode
    design. If --experimental-dir does not supply exactly one curve per
    electrode, run_chi2_analysis raises NotImplementedError -- a deliberate
    placeholder that stays "broken" until real per-electrode data exists, rather
    than fabricating a misleading fit. (The earlier nearest-qubit reconciliation
    was removed for exactly this reason; see the git history / README.)

Model-parameter values come from the run's MorrisSequence.csv (the canonical
record of each Morris_i.mac configuration), the analog of Paul's SobolSequence.
Per-sample DG(t) curves are read from the qps/*_xQPs.npz files that
stage2_compute_QPs.py already wrote, so no ODE is recomputed.

Experimental-data contract (chi^2 only): --experimental-dir must contain one
Paul-format curve per electrode -- a pair <stem>_DuringPulse.txt and <stem>.txt,
each 3 rows [delay us, Delta-Gamma MHz, error MHz] -- named so that sorted
filename order matches electrode index order. DEFAULT_EXPERIMENTAL_DIR holds
only Paul's 6-qubit measurement (a different device, 6 curves), which is why
chi^2 is a not-yet-runnable placeholder for this 17-electrode run.

Usage:
    python stage2_compute_QPs_sensitivity_analysis.py --results-dir /path/to/results/<run_id>
    python stage2_compute_QPs_sensitivity_analysis.py --method chi2 --experimental-dir /path/to/per_electrode_data

Outputs (written into the results directory):
    sensitivity_correlations.csv / .png       integrated-DG method (In[5] style)
    sensitivity_corr_matrix.png               integrated-DG method (In[3] matshow)
    sensitivity_chi2_correlations.csv / .png  chi^2 method (only once per-electrode data is supplied)
    sensitivity_chi2_corr_matrix.png          chi^2 method (In[3] matshow)
"""

import argparse
import csv
import glob
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # mimir is headless; render straight to PNG
import matplotlib.pyplot as plt

QP_SNAPSHOT_COUNT = 1001
QP_SNAPSHOT_MAX_NS = 300000.0

# Planck constant [eV*s], for gap[eV] -> gap frequency [Hz] (Paul's AlGap).
H_PLANCK_EV_S = 4.135667696e-15

# Default experimental Delta-Gamma-vs-delay directory. This holds only Paul's
# 6-qubit NbGND measurement (a DIFFERENT device); it does NOT provide one curve
# per electrode of this 17-electrode design, so the chi^2 method treats it as
# insufficient and refuses to run (see run_chi2_analysis). Point
# --experimental-dir at real per-electrode data (one curve per electrode,
# index-aligned) to enable chi^2. Each curve is a pair of Paul-format files:
# <stem>_DuringPulse.txt and <stem>.txt, each 3 rows [delay us, DG MHz, err MHz].
DEFAULT_EXPERIMENTAL_DIR = (
    "/home/htseng/BNL_G4CMP_HT_Feb27/Scripts/python/NbGND_1umCu_B2_Q0Inj_150us_staggered"
)


# --------------------------------------------------------------------------- #
# QP physics (identical to stage2_compute_QPs.py; used only by the integrated  #
# method's self-contained fallback when qp_summary.csv is absent).             #
# --------------------------------------------------------------------------- #
def calculate_QPs(rec, gap, qx, qy, chip_z):
    """Bins phonon energy deposited on the sensor surface into quasiparticles on
    the nearest electrode, per time bin. Returns (snapshot_t[ns], QPNos)."""
    snapshot_t = np.linspace(0, QP_SNAPSHOT_MAX_NS, QP_SNAPSHOT_COUNT)
    QPNos = np.zeros((len(qx), QP_SNAPSHOT_COUNT))

    if len(rec) == 0:
        return snapshot_t, QPNos

    hits = np.where((rec["Energy Deposited [eV]"] > 0) & np.isclose(rec["End Z [m]"], chip_z))[0]

    for h in hits:
        r = ((1000 * rec["End X [m]"][h] - qx) ** 2 + (1000 * rec["End Y [m]"][h] - qy) ** 2) ** 0.5
        q = np.argmin(r)
        t_idx = np.argmin(np.abs(rec["Final Time [ns]"][h] - snapshot_t))
        QPNos[q, t_idx] += int(np.round(rec["Energy Deposited [eV]"][h] / gap))

    return snapshot_t, QPNos


def calculate_xQPs(snapshot_t, QPNos, AlGap, f_01, r, s, I_ph, pt, n_cooper, height, width, thickness, n_sim):
    """Solves the quasiparticle-density ODE per electrode and converts it to a
    decoherence-rate contribution (MHz). Returns (t[us], DG[electrode, time])."""
    dt = np.mean(np.diff(snapshot_t)) * 0.001  # ns -> us
    t = np.linspace(0.001 * snapshot_t[0], 0.001 * snapshot_t[-1] + pt, len(snapshot_t) + int(np.round(pt / dt)))
    num_electrodes = QPNos.shape[0]
    g_QP = np.zeros((num_electrodes, len(t)))
    x_QP = np.zeros((num_electrodes, len(t)))
    DG = np.zeros((num_electrodes, len(t)))
    Factor = 2 * np.sqrt(2 * AlGap * f_01) * 1e-6

    for k in range(num_electrodes):
        for i in range(int(np.round(pt / dt))):
            g_QP[k, i:len(QPNos[k, :]) + i] += QPNos[k, :] * I_ph / (n_cooper * height * width * thickness * n_sim)

        for i in range(len(g_QP[k, :]) - 1):
            dx = (-r * x_QP[k, i] ** 2 - s * x_QP[k, i] + g_QP[k, i]) * dt
            x_QP[k, i + 1] = x_QP[k, i] + dx

        DG[k, :] = x_QP[k, :] * Factor

    return t, DG


def load_manifest(manifest_file):
    with open(manifest_file, "r") as f:
        return [json.loads(line) for line in f if line.strip()]


# --------------------------------------------------------------------------- #
# Method 1: integrated-decoherence correlation#
# --------------------------------------------------------------------------- #
def sample_integrated_DG(entry):
    """Derive the single-aggregate integrated decoherence response for one sample
    directly from its hits file (sum over electrodes of the time integral of
    DG(t)); reproduces stage2_compute_QPs.py's `total_integrated_DG`. Returns
    np.nan when the hits file is missing/empty/unreadable."""
    hits_file = entry["hits_file"]
    if not os.path.exists(hits_file) or os.path.getsize(hits_file) == 0:
        return np.nan
    try:
        rec = pd.read_csv(hits_file)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return np.nan

    qx = np.array(entry["qx"])
    qy = np.array(entry["qy"])
    gap = entry["gap"]
    chip_z = entry["chip_z"]

    times, QPNos = calculate_QPs(rec, gap, qx, qy, chip_z)
    AlGap = gap / H_PLANCK_EV_S
    t, DG = calculate_xQPs(
        times, QPNos, AlGap,
        entry["f_01"], entry["r"], entry["s"], entry["I_ph"],
        entry["pt"], entry["n_cooper"],
        entry["height"], entry["width"], entry["thickness"], entry["n_sim"],
    )
    if DG.size == 0:
        return np.nan
    return float(np.sum(np.trapezoid(DG, t, axis=1)))


def load_outcomes(results_dir, entries, progress_every):
    """Map sample_name -> total_integrated_DG. Prefers the qp_summary.csv already
    produced by stage2_compute_QPs.py; falls back to deriving from the hits files
    (self-contained) if that summary is missing or incomplete."""
    # A complete summary has one row per sample EXCEPT those whose hits file is
    # missing/empty (crashed samples), which stage 2 skips. Only a genuinely
    # interrupted stage-2 run falls short of that count.
    n_skippable = sum(
        1 for e in entries
        if not os.path.exists(e["hits_file"]) or os.path.getsize(e["hits_file"]) == 0
    )
    expected_rows = len(entries) - n_skippable

    summary_file = os.path.join(results_dir, "qp_summary.csv")
    if os.path.exists(summary_file):
        summ = pd.read_csv(summary_file)
        if len(summ) >= expected_rows:
            outcomes = dict(zip(summ["sample_name"].astype(str), summ["total_integrated_DG"].astype(float)))
            print(
                f"Using existing outcomes from {summary_file} ({len(outcomes)} rows; "
                f"{n_skippable} samples have no hits file and are skipped)"
            )
            return outcomes
        print(
            f"qp_summary.csv is incomplete ({len(summ)} rows < {expected_rows} expected); "
            "deriving integrated decoherence from hits files instead."
        )
    else:
        print(f"No qp_summary.csv found; deriving integrated decoherence from hits files ({len(entries)} samples).")
    outcomes = {}
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        outcomes[str(entry["sample_name"])] = sample_integrated_DG(entry)
        if index <= 5 or index % progress_every == 0 or index == total:
            print(f"Derived {index}/{total} outcomes")
    return outcomes


def build_trials(sequence, outcomes_by_name):
    """Assemble [model parameters | log10(integrated DG)], one row per usable
    sample (finite, positive outcome). Returns (trials, n_used, n_dropped)."""
    param_values = sequence.values
    rows = []
    dropped = 0
    for idx in range(param_values.shape[0]):
        val = outcomes_by_name.get(f"Morris_{idx}", np.nan)
        if not np.isfinite(val) or val <= 0:
            dropped += 1
            continue
        rows.append(np.append(param_values[idx, :], np.log10(val)))
    return np.array(rows, dtype=float), len(rows), dropped


def write_correlations_csv(out_file, param_names, corr):
    order = np.argsort(-np.abs(np.nan_to_num(corr)))  # strongest |corr| first
    with open(out_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["model_parameter", "correlation_coefficient", "abs_correlation"])
        for i in order:
            writer.writerow([param_names[i], corr[i], abs(corr[i])])


def plot_correlations(out_file, param_names, corr, n_used):
    """The In[5]-style figure for the integrated-DG method (single series)."""
    fig, ax = plt.subplots(figsize=(15, 5))
    x = np.arange(len(param_names))
    ax.plot(x, corr, "o-", color="C0", label=r"log$_{10}$(integrated $\Delta\Gamma$)")
    ax.axhline(0, linestyle="--", color="k")
    ax.set_xticks(x)
    ax.set_xticklabels(param_names, rotation=90, fontsize=8)
    ax.set_ylabel(r"$\Delta\Gamma$ Correlation Coefficient")
    ax.set_xlabel("Model Parameter")
    ax.set_title(f"Parameter sensitivity by correlation (n = {n_used} usable samples)")
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(out_file, dpi=200)
    plt.close(fig)


def plot_corr_matrix(out_file, labels, A):
    """The In[3]-style full-correlation matshow of [parameters | outcome(s)]."""
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.matshow(A, cmap="coolwarm", vmin=-1, vmax=1)
    fig.colorbar(im)
    ax.set_xticks(np.arange(len(labels)))
    ax.set_yticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=6)
    ax.set_yticklabels(labels, fontsize=6)
    fig.tight_layout()
    fig.savefig(out_file, dpi=200)
    plt.close(fig)


def run_integrated_analysis(results_dir, sequence, entries, progress_every):
    param_names = list(sequence.columns)
    outcomes_by_name = load_outcomes(results_dir, entries, progress_every)

    trials, n_used, n_dropped = build_trials(sequence, outcomes_by_name)
    print(
        f"[integrated] correlation input: {n_used} usable samples "
        f"(dropped {n_dropped} with no/zero integrated decoherence)."
    )
    if n_used < 3:
        print("[integrated] too few usable samples; skipping.")
        return

    A = np.corrcoef(trials, rowvar=False)
    corr = A[-1, :-1]  # outcome (last) row vs each parameter column

    n_constant = int(np.sum(~np.isfinite(corr)))
    if n_constant:
        print(f"[integrated] NOTE: {n_constant} parameter(s) had zero variance (NaN correlation).")

    corr_csv = os.path.join(results_dir, "sensitivity_correlations.csv")
    corr_png = os.path.join(results_dir, "sensitivity_correlations.png")
    matrix_png = os.path.join(results_dir, "sensitivity_corr_matrix.png")
    write_correlations_csv(corr_csv, param_names, corr)
    plot_correlations(corr_png, param_names, corr, n_used)
    plot_corr_matrix(matrix_png, param_names + ["log10(integ_DG)"], A)
    print(f"[integrated] wrote {corr_csv}")
    print(f"[integrated] wrote {corr_png}")
    print(f"[integrated] wrote {matrix_png}")

    order = np.argsort(-np.abs(np.nan_to_num(corr)))
    print("[integrated] top parameter correlations with log10(integrated decoherence):")
    for i in order[:10]:
        print(f"    {corr[i]:+.4f}   {param_names[i]}")


# --------------------------------------------------------------------------- #
# Method 2: per-qubit chi^2 vs experimental delay data (faithful In[3]/In[5]). #
# --------------------------------------------------------------------------- #
def load_experimental_curves(exp_dir):
    """Load every experimental Delta-Gamma-vs-delay curve found in `exp_dir`,
    in sorted filename order. Returns a list of (x, y, dy) arrays.

    Each curve is one real measurement at ONE location. This loader imposes no
    spatial interpretation and invents no mapping -- the caller
    (run_chi2_analysis) requires exactly one curve per electrode, index-aligned
    (curve i belongs to electrode i), and errors out otherwise rather than
    reconciling a count mismatch.

    File convention (Paul's In[3] format): each curve is a pair
    <stem>_DuringPulse.txt (during the injection pulse) and <stem>.txt (after);
    each file is 3 rows [delay us, Delta-Gamma MHz, error MHz]. The two halves
    are concatenated and the delay axis is shifted so the earliest during-pulse
    point sits at 0 (t = pulse length then marks the pulse end).
    """
    curves = []
    during_files = sorted(glob.glob(os.path.join(exp_dir, "*_DuringPulse.txt")))
    for during_path in during_files:
        after_path = during_path[: -len("_DuringPulse.txt")] + ".txt"
        if not os.path.exists(after_path):
            continue  # unpaired during-pulse file; skip
        during = np.loadtxt(during_path)
        after = np.loadtxt(after_path)
        x = np.append(during[0], after[0]) - np.min(during[0])
        y = np.append(during[1], after[1])
        dy = np.append(during[2], after[2])
        curves.append((x, y, dy))
    return curves


def chi2_one_curve(sim_t, sim_dg, x_data, y_data, dy_data, normalize):
    """Paul's chi^2 (cell In[3]): a goodness-of-fit distance between two
    decoherence-rate-vs-time curves -- one simulated at an electrode, one the
    real measured reference curve for that electrode's location. For each
    experimental delay point, take the simulated Delta-Gamma at the nearest
    simulated time and sum the squared residual. `normalize` in {none, y, dy2}
    selects the weighting; "none" reproduces exactly what Paul ran.

    This chi^2 is a *distance*, not a correlation: it answers "does this
    sample's simulation match the measured device?" The correlation step
    (np.corrcoef in run_chi2_analysis, downstream of this function) is a
    separate question layered on top: "across many samples, does a model
    parameter's value tend to move together with this chi^2 distance?"

    Args:
        sim_t: simulated time axis [us] -- time since the QP-injection pulse
            began (t=0). From this sample's xQPs.npz `t` array: the
            decoherence ODE's integrated time grid (calculate_xQPs).
        sim_dg: simulated decoherence rate DG_sim(t) [MHz] for one electrode,
            aligned with sim_t. From xQPs.npz `DG[e]` -- this sample's
            Morris_i.mac configuration, ODE-solved for electrode e.
        x_data: experimental delay axis [us] -- the measurement's time points
            for THIS electrode's own reference curve (during-pulse + after-pulse
            files concatenated and shifted so t = pulse length marks the end of
            the injection pulse; see load_experimental_curves).
        y_data: measured decoherence rate DG_exp [MHz] -- the measured
            decoherence contribution at this electrode at each x_data point.
        dy_data: measurement uncertainty [MHz] on each y_data point (the
            error bar recorded alongside the lab measurement).
        normalize: "none" = raw MHz^2 residuals (what Paul ran); "y" =
            divide by y_data (his disabled `#/y0_data[j]` hint); "dy2" =
            divide by dy_data**2 (a proper variance-weighted chi^2, using
            the error column Paul loaded but never applied).

    Returns:
        A scalar chi^2: the total (optionally weighted) squared mismatch
        between the simulated and measured curves, summed over every
        experimental delay point. Small = simulation looks like the real
        device at this configuration; large = it doesn't.
    """
    # Nearest-neighbor time matching: sim_t and x_data are different time
    # grids (the ODE's uniform snapshot grid vs. the lab's staggered
    # measurement grid), so for each measured delay point we pick the
    # closest simulated time bin rather than interpolating -- exactly
    # Paul's `point = np.argmin(np.abs(x0_data[j]-output_x[i]))`.
    idx = np.array([np.argmin(np.abs(xj - sim_t)) for xj in x_data])
    # Residual = simulated DG minus measured DG at each matched delay point
    # [MHz]; squaring keeps it positive and penalizes larger mismatches more.
    resid2 = (sim_dg[idx] - y_data) ** 2
    if normalize == "y":  # Paul's commented-out `#/y0_data[j]`
        good = np.abs(y_data) > 1e-12
        return float(np.sum(resid2[good] / y_data[good]))
    if normalize == "dy2":  # proper statistical chi^2 using the loaded errors
        good = np.abs(dy_data) > 1e-12
        return float(np.sum(resid2[good] / dy_data[good] ** 2))
    return float(np.sum(resid2))


def sample_chi2(npz_file, exp_curves, normalize):
    """Compute one chi^2 (see chi2_one_curve) per electrode for a sample, from
    its saved xQPs.npz. Electrode e's simulated curve -- npz's (t, DG[e]) -- is
    scored against electrode e's OWN measured reference curve exp_curves[e] =
    (x, y, dy) (strict 1:1 index alignment; no reconciliation). Returns an
    array of length len(exp_curves), or None if the npz is missing."""
    if not os.path.exists(npz_file):
        return None
    d = np.load(npz_file)
    t = d["t"]
    DG = d["DG"]
    out = np.empty(len(exp_curves))
    for e, (x, y, dy) in enumerate(exp_curves):
        out[e] = chi2_one_curve(t, DG[e], x, y, dy, normalize)
    return out


def write_chi2_csv(out_file, param_names, corr, channel_labels):
    """corr has shape (n_channels, n_params). Write one row per parameter with the
    per-channel correlations plus the mean |correlation|, ranked by the mean."""
    mean_abs = np.nanmean(np.abs(corr), axis=0)
    order = np.argsort(-np.nan_to_num(mean_abs))
    header_labels = [lab.replace(" ", "_").replace("(", "").replace(")", "") for lab in channel_labels]
    with open(out_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["model_parameter"] + [f"corr_{lab}" for lab in header_labels] + ["mean_abs_correlation"])
        for i in order:
            writer.writerow([param_names[i]] + [corr[c, i] for c in range(corr.shape[0])] + [mean_abs[i]])


def plot_chi2_correlations(out_file, param_names, corr, channel_labels, n_used, normalize):
    """The In[5]-style figure: one line per electrode's chi^2 correlation vs
    every model parameter (Paul's per-qubit plot, generalized to N electrodes)."""
    n_channels = corr.shape[0]
    fig, ax = plt.subplots(figsize=(15, 5.5))
    x = np.arange(len(param_names))
    # tab20 gives up to 20 visually distinct colors, so many per-electrode lines
    # stay distinguishable (the default C0-C9 cycle repeats after 10).
    colors = plt.cm.tab20(np.linspace(0, 1, max(n_channels, 1)))
    for c in range(n_channels):
        ax.plot(x, corr[c], "o-", markersize=3, color=colors[c], label=channel_labels[c])
    ax.axhline(0, linestyle="--", color="k")
    ax.set_xticks(x)
    ax.set_xticklabels(param_names, rotation=90, fontsize=8)
    ax.set_ylabel(r"$\chi^2$ Correlation Coefficient")
    ax.set_xlabel("Model Parameter")
    ax.set_title(
        f"Per-channel $\\chi^2$-to-experiment sensitivity "
        f"(normalize={normalize}, n = {n_used} samples, {n_channels} channels)"
    )
    ax.legend(loc="lower left", fontsize=7, ncol=2 if n_channels > 8 else 1)
    fig.tight_layout()
    fig.savefig(out_file, dpi=200)
    plt.close(fig)


def run_chi2_analysis(results_dir, sequence, entries, exp_dir, normalize):
    param_names = list(sequence.columns)
    n_params = len(param_names)
    qps_dir = os.path.join(results_dir, "qps")
    n_electrodes = len(entries[0]["qx"])

    # --- Experimental-data gate (NO RECONCILIATION) -------------------------- #
    # The chi^2 method needs one measured Delta-Gamma-vs-delay curve per
    # electrode of THIS device, index-aligned (curve e measured at electrode e).
    # Anything short of that is a hard, deliberate placeholder: we refuse to
    # fabricate targets by reusing/mapping a mismatched set of curves. This is
    # the "let it hang there, broken, until real per-electrode data exists"
    # contract -- swap --experimental-dir for real data to enable the method.
    exp_curves = load_experimental_curves(exp_dir) if os.path.isdir(exp_dir) else []
    if len(exp_curves) != n_electrodes:
        raise NotImplementedError(
            "[chi2] PLACEHOLDER -- not enough experimental data to run without "
            "reconciliation.\n"
            f"  This device has {n_electrodes} electrodes, so the chi^2 method "
            f"needs {n_electrodes} measured Delta-Gamma-vs-delay curves (one per "
            "electrode, index-aligned: curve e measured at electrode e's "
            "location).\n"
            f"  --experimental-dir = {exp_dir}\n"
            f"  found there        = {len(exp_curves)} curve(s).\n"
            "  The default directory holds only Paul's 6-qubit NbGND measurement, "
            "which is a DIFFERENT device; mapping those 6 onto "
            f"{n_electrodes} electrodes would be a physically invalid "
            "reconciliation (see the module docstring), so it is refused.\n"
            "  To enable chi^2: provide one Paul-format curve per electrode in "
            "--experimental-dir (paired files <stem>_DuringPulse.txt / "
            "<stem>.txt, each 3 rows [delay us, DG MHz, err MHz]), named so "
            "sorted filename order == electrode index order.\n"
            "  For an experiment-free sensitivity analysis that needs no such "
            "data, use --method integrated instead."
        )

    # Strict 1:1 electrode<->curve pairing; no spatial argmin, no reuse.
    channel_labels = [f"elec {e}" for e in range(n_electrodes)]
    print(f"[chi2] experimental data : {exp_dir}")
    print(f"[chi2] curves/electrodes : {len(exp_curves)} curves for {n_electrodes} electrodes (1:1)")
    print(f"[chi2] weighting         : {normalize}")

    # Validity filter: samples that actually produced decoherence signal
    # (total_integrated_DG > 0). Stricter than Paul's `len(output_x[i])>0`, but
    # it avoids a large block of identical all-zero curves that would otherwise
    # dominate the correlation. Falls back to "npz exists" if no summary.
    summary_file = os.path.join(results_dir, "qp_summary.csv")
    signal_names = None
    if os.path.exists(summary_file):
        summ = pd.read_csv(summary_file)
        signal_names = set(summ.loc[summ["total_integrated_DG"] > 0, "sample_name"].astype(str))

    rows = []
    missing = 0
    for idx in range(len(sequence)):
        name = f"Morris_{idx}"
        if signal_names is not None and name not in signal_names:
            continue
        chi2 = sample_chi2(os.path.join(qps_dir, name + "_xQPs.npz"), exp_curves, normalize)
        if chi2 is None:
            missing += 1
            continue
        rows.append(np.concatenate([sequence.values[idx, :], chi2]))

    n_used = len(rows)
    print(f"[chi2] correlation input : {n_used} usable samples ({missing} missing npz).")
    if n_used < 3:
        print("[chi2] too few usable samples; skipping.")
        return

    trials = np.array(rows, dtype=float)
    # trials columns = [n_params model parameters | n_electrodes chi^2 distances].
    # np.corrcoef always computes ordinary Pearson correlation -- it has no idea
    # some columns are chi^2 values. "chi2 correlation" below means "the Pearson
    # correlation OF each electrode's chi^2 column against each parameter column",
    # i.e. does a parameter's value move together with how far that electrode's
    # simulation sits from its measured curve -- not a chi^2-flavored formula.
    A = np.corrcoef(trials, rowvar=False)
    corr = A[n_params:, :n_params]  # (n_electrodes, n_params): each electrode's chi^2 vs params

    n_constant = int(np.sum(~np.isfinite(corr).all(axis=0)))
    if n_constant:
        print(
            f"[chi2] NOTE: {n_constant} electrode(s) never varied (never lit up "
            "across the usable samples), giving NaN correlations (left as NaN)."
        )

    corr_csv = os.path.join(results_dir, "sensitivity_chi2_correlations.csv")
    corr_png = os.path.join(results_dir, "sensitivity_chi2_correlations.png")
    matrix_png = os.path.join(results_dir, "sensitivity_chi2_corr_matrix.png")
    write_chi2_csv(corr_csv, param_names, corr, channel_labels)
    plot_chi2_correlations(corr_png, param_names, corr, channel_labels, n_used, normalize)
    matrix_labels = param_names + [f"chi2_{lab.replace(' ', '')}" for lab in channel_labels]
    plot_corr_matrix(matrix_png, matrix_labels, A)
    print(f"[chi2] wrote {corr_csv}")
    print(f"[chi2] wrote {corr_png}")
    print(f"[chi2] wrote {matrix_png}")

    mean_abs = np.nanmean(np.abs(corr), axis=0)
    order = np.argsort(-np.nan_to_num(mean_abs))
    print("[chi2] top parameters by mean |chi^2 correlation| across electrodes:")
    for i in order[:10]:
        print(f"    mean|r|={mean_abs[i]:.4f}   {param_names[i]}")


def main():
    parser = argparse.ArgumentParser(
        description="Stage 2b: correlate Morris model parameters against the QP "
        "decoherence response (reproduces SensitivityAnalysis.ipynb In[3]/In[5])."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("./results/morris_mimir_95494c3c-4c72-4d47-8e92-fbc88f3a0517"),
        help="results/<run_id> directory (must contain qp_manifest.jsonl, "
        "MorrisSequence.csv, and qps/*_xQPs.npz). Default: %(default)s",
    )
    parser.add_argument(
        "--method",
        choices=["integrated", "chi2", "both"],
        default="integrated",
        help="Which analysis to run. 'integrated' (default) is experiment-free "
        "and always runs; 'chi2' requires one measured curve per electrode and "
        "raises a clear placeholder error until such data is supplied.",
    )
    parser.add_argument(
        "--experimental-dir",
        default=DEFAULT_EXPERIMENTAL_DIR,
        help="Directory of per-electrode Paul-format Delta-Gamma-vs-delay files "
        "for the chi^2 method (one curve per electrode, index-aligned). The "
        "default holds only Paul's 6-qubit data (a different device), so chi^2 "
        "stays a placeholder until this points at real per-electrode data. "
        "Default: %(default)s",
    )
    parser.add_argument(
        "--chi2-normalize",
        choices=["none", "y", "dy2"],
        default="none",
        help="chi^2 weighting: none=raw SSE (what Paul ran), y=divide by "
        "experimental value (his commented hint), dy2=divide by variance "
        "(proper chi^2). Default: none.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=int(os.environ.get("SENSITIVITY_PROGRESS_EVERY", "50000")),
        help="Progress interval when deriving integrated outcomes from hits (default: 50000).",
    )
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    manifest_file = os.path.join(results_dir, "qp_manifest.jsonl")
    sequence_file = os.path.join(results_dir, "MorrisSequence.csv")
    for label, path in (("QP manifest", manifest_file), ("MorrisSequence.csv", sequence_file)):
        if not os.path.exists(path):
            raise FileNotFoundError(f"{label} not found: {path}. Run stage1_run_simulations.py first.")

    entries = load_manifest(manifest_file)
    sequence = pd.read_csv(sequence_file)
    print(f"Loaded {len(entries)} manifest entries and MorrisSequence {sequence.shape} from {results_dir}")
    if len(sequence) != len(entries):
        print(
            f"WARNING: MorrisSequence rows ({len(sequence)}) != manifest entries "
            f"({len(entries)}); aligning by sample index Morris_i."
        )

    if args.method in ("integrated", "both"):
        run_integrated_analysis(results_dir, sequence, entries, args.progress_every)
    if args.method in ("chi2", "both"):
        run_chi2_analysis(
            results_dir, sequence, entries, args.experimental_dir, args.chi2_normalize,
        )


if __name__ == "__main__":
    main()
