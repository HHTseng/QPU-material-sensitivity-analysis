"""
Stage 2b of the Morris pipeline: parameter-vs-outcome correlation analysis.

This reproduces the correlation analysis in Paul Baity's SensitivityAnalysis.ipynb
(cells In[3] and In[5]) and adapts it to this Morris run. Two methods are
provided; `--method` selects one or both:

  method="chi2" (faithful reproduction of cell In[3]):
    For every sample, Paul turns the hits file into a per-qubit decoherence
    curve DG(t), computes a chi^2 goodness-of-fit of that curve to *experimental*
    Delta-Gamma-vs-delay data (one value per qubit), builds a matrix
    [model parameters | 6 chi^2], takes np.corrcoef, and plots each qubit's
    correlation against every model parameter (the In[5] figure).

  method="integrated" (Paul's experiment-free sibling cell In[5]/In[7]):
    The per-sample scalar is log10 of the time-integrated DG instead of a chi^2.
    Everything downstream (np.corrcoef + the In[5] plot) is identical. This is
    exactly `total_integrated_DG` from stage2_compute_QPs.py.

Correspondence choices made to fit this run's setting (Paul's notebook was
edited in place many times and carries several stale/contradictory fragments;
where his intent is ambiguous the most logical reading is used, and the
departure is called out here and in the code):

  * Experimental data. Paul fits against the real NbGND 150 us Q0-injection
    delay curves. Those files are not in this project but ARE on mimir at
    DEFAULT_EXPERIMENTAL_DIR; the 150 us set is chosen because it matches this
    run's default ODE pulse time (pt = 150 us). Override with --experimental-dir.

  * 6 qubits vs 17 electrodes (--chi2-channels). Paul measured 6 qubits, so only
    6 experimental curves exist -- a chi^2 needs a measured target, so the number
    of channels is bounded by the data, not the 17 electrodes. Two modes:
      - "electrodes" (default, 17 channels): score every electrode against the
        experimental curve of the nearest qubit. The 6 curves are reused by
        proximity, so these are 17 simulated electrodes vs 6 measured references,
        not 17 independent experimental fits. Fits this device's geometry.
      - "qubits" (6 channels): Paul's literal reproduction -- each experimental
        qubit vs its nearest electrode; the six map cleanly onto the y = +/-2 rows.

  * Qubit<->curve pairing. Cell In[3] pairs experimental Qubit_(5-j) with
    simulated curve j (the file loads are reversed relative to the curve index).
    That is almost certainly a copy/paste slip from repeated editing; here
    experimental Qubit_k is paired with the curve for qubit-location k, the
    physically consistent choice.

  * chi^2 weighting. Paul ran an unweighted sum of squared residuals; his
    `#/y0_data[j]` comment shows an intended-but-disabled division by the
    experimental value. --chi2-normalize {none,y,dy2} exposes this; default
    "none" reproduces exactly what he ran. (dy2 = a proper statistical chi^2
    using the error column he loaded but never applied.)

  * Scale caveat. This run used 1e5 events with a localized phonon_Caustic
    injection, so simulated DG (~0.5 MHz median, tens of MHz peak) is 1-2 orders
    of magnitude larger than the experimental Delta-Gamma (~0.01 MHz) and is
    concentrated on whichever electrode the phonons happen to strike. The raw
    chi^2 is therefore dominated by simulated magnitude/hit-location rather than
    curve shape -- faithful to Paul's construction, but read the chi^2
    correlations as "which parameters drive the simulated response," not as a
    literal calibration to experiment.

Model-parameter values come from the run's MorrisSequence.csv (the canonical
record of each Morris_i.mac configuration), the analog of Paul's SobolSequence.
Per-sample DG(t) curves are read from the qps/*_xQPs.npz files that
stage2_compute_QPs.py already wrote, so no ODE is recomputed.

Usage:
    python stage2_compute_QPs_sensitivity_analysis.py --results-dir /path/to/results/<run_id>
    python stage2_compute_QPs_sensitivity_analysis.py --method chi2 --chi2-normalize dy2

Outputs (written into the results directory):
    sensitivity_correlations.csv / .png       integrated-DG method (In[5] style)
    sensitivity_corr_matrix.png               integrated-DG method (In[3] matshow)
    sensitivity_chi2_correlations.csv / .png  chi^2 method, one line per qubit
    sensitivity_chi2_corr_matrix.png          chi^2 method (In[3] matshow)
"""

import argparse
import csv
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

# Paul's 6 hard-coded qubit locations [mm] (calculate_QPs, cells In[2]/In[19]).
PAUL_QUBIT_X = np.array([-1.64, -2.4405, 0.4, -0.4, 2.42, 1.62])
PAUL_QUBIT_Y = np.array([2.2795, -2.2735, 2.2735, -2.2795, 2.2795, -2.2735])
N_QUBITS = 6

# Real experimental Delta-Gamma-vs-delay data that Paul's In[3] fits against.
# Found on mimir outside this project; the 150 us set matches this run's default
# ODE pulse time (pt = 150 us). Each file is 3 rows: delay[us], DG[MHz], err[MHz].
DEFAULT_EXPERIMENTAL_DIR = (
    "/home/htseng/BNL_G4CMP_HT_Feb27/Scripts/python/NbGND_1umCu_B2_Q0Inj_150us_staggered"
)
EXP_FILE_STEM = "Qubit_{k}_NbGND_1umCu_VI09_B2_DeltaGamma_v_Delay_Q0inj_150us"


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
# Method 1: integrated-decoherence correlation (experiment-free, In[5]/In[7]). #
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
def load_experimental_delays(exp_dir):
    """Load Paul's per-qubit experimental curves exactly as cell In[3] does: for
    each qubit concatenate the during-pulse and post-pulse files, then shift the
    delay axis so the earliest during-pulse point sits at 0. Each file is 3 rows
    [delay us, Delta-Gamma MHz, error MHz]. Returns a list of (x, y, dy)."""
    curves = []
    for k in range(N_QUBITS):
        stem = os.path.join(exp_dir, EXP_FILE_STEM.format(k=k))
        during = np.loadtxt(stem + "_DuringPulse.txt")
        after = np.loadtxt(stem + ".txt")
        x = np.append(during[0], after[0]) - np.min(during[0])
        y = np.append(during[1], after[1])
        dy = np.append(during[2], after[2])
        curves.append((x, y, dy))
    return curves


def build_chi2_channels(entry, mode):
    """Define the chi^2 output channels for this device geometry.

    Returns (channels, labels): `channels` is a list of (electrode_index,
    qubit_index) pairs, one per chi^2 value produced; `labels` are the matching
    plot/CSV labels.

    mode="qubits" (6 channels): faithful to Paul -- one channel per experimental
      qubit, compared against the simulated curve of the NEAREST electrode. The
      other electrodes have no experimental counterpart and are not scored.

    mode="electrodes" (17 channels): one channel per electrode, compared against
      the experimental curve of the NEAREST qubit. There are only 6 experimental
      qubit curves (Paul measured 6 qubits), so nearby electrodes reuse the same
      experimental target -- these 17 channels are 17 simulated electrodes scored
      against 6 measured references, NOT 17 independent experimental fits.
    """
    ex = np.array(entry["qx"])
    ey = np.array(entry["qy"])
    if mode == "qubits":
        channels = [
            (int(np.argmin(np.hypot(ex - PAUL_QUBIT_X[k], ey - PAUL_QUBIT_Y[k]))), k)
            for k in range(N_QUBITS)
        ]
        labels = [f"Qubit {k} (elec {e})" for (e, k) in channels]
    elif mode == "electrodes":
        channels = [
            (e, int(np.argmin(np.hypot(PAUL_QUBIT_X - ex[e], PAUL_QUBIT_Y - ey[e]))))
            for e in range(len(ex))
        ]
        labels = [f"elec {e} (Q{k})" for (e, k) in channels]
    else:
        raise ValueError(f"unknown chi2 channels mode: {mode!r}")
    return channels, labels


def chi2_one_curve(sim_t, sim_dg, x_data, y_data, dy_data, normalize):
    """Paul's per-qubit chi^2 (cell In[3]): for each experimental delay point,
    take the simulated Delta-Gamma at the nearest simulated time and sum the
    squared residual. `normalize` in {none, y, dy2} selects the weighting;
    "none" reproduces exactly what Paul ran."""
    idx = np.array([np.argmin(np.abs(xj - sim_t)) for xj in x_data])
    resid2 = (sim_dg[idx] - y_data) ** 2
    if normalize == "y":  # Paul's commented-out `#/y0_data[j]`
        good = np.abs(y_data) > 1e-12
        return float(np.sum(resid2[good] / y_data[good]))
    if normalize == "dy2":  # proper statistical chi^2 using the loaded errors
        good = np.abs(dy_data) > 1e-12
        return float(np.sum(resid2[good] / dy_data[good] ** 2))
    return float(np.sum(resid2))


def sample_chi2(npz_file, channels, exp_curves, normalize):
    """Compute one chi^2 per channel for a sample from its saved xQPs.npz (t, DG).
    Each channel is an (electrode_index, qubit_index) pair: the simulated curve of
    that electrode is scored against that qubit's experimental curve. Returns an
    array of length len(channels), or None if the npz is missing."""
    if not os.path.exists(npz_file):
        return None
    d = np.load(npz_file)
    t = d["t"]
    DG = d["DG"]
    out = np.empty(len(channels))
    for c, (e, k) in enumerate(channels):
        x, y, dy = exp_curves[k]
        out[c] = chi2_one_curve(t, DG[e], x, y, dy, normalize)
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
    """The In[5]-style figure: one line per channel's chi^2 correlation vs every
    model parameter (Paul's six-qubit plot, generalized to N channels)."""
    n_channels = corr.shape[0]
    fig, ax = plt.subplots(figsize=(15, 5.5))
    x = np.arange(len(param_names))
    # tab20 gives up to 20 visually distinct colors, so the 17-electrode mode's
    # lines stay distinguishable (the default C0-C9 cycle repeats after 10).
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


def run_chi2_analysis(results_dir, sequence, entries, exp_dir, normalize, channels_mode):
    param_names = list(sequence.columns)
    n_params = len(param_names)
    qps_dir = os.path.join(results_dir, "qps")

    if not os.path.isdir(exp_dir):
        print(
            f"[chi2] experimental delay directory not found: {exp_dir}\n"
            "[chi2] skipping chi^2 method (pass --experimental-dir to point at the "
            "NbGND Delta-Gamma-vs-delay files)."
        )
        return

    exp_curves = load_experimental_delays(exp_dir)
    channels, channel_labels = build_chi2_channels(entries[0], channels_mode)
    print(f"[chi2] experimental data : {exp_dir}")
    print(f"[chi2] channels mode     : {channels_mode} ({len(channels)} channels)")
    print("[chi2] channels (elec<-Q): " + ", ".join(f"e{e}<-Q{k}" for (e, k) in channels))
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
        chi2 = sample_chi2(os.path.join(qps_dir, name + "_xQPs.npz"), channels, exp_curves, normalize)
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
    A = np.corrcoef(trials, rowvar=False)
    corr = A[n_params:, :n_params]  # (n_channels, n_params): each channel's chi^2 vs params

    n_constant = int(np.sum(~np.isfinite(corr).all(axis=0)))
    if n_constant:
        print(
            f"[chi2] NOTE: {n_constant} channel(s) never varied (electrode never lit up "
            "across the usable samples), giving NaN correlations (left as NaN)."
        )

    corr_csv = os.path.join(results_dir, "sensitivity_chi2_correlations.csv")
    corr_png = os.path.join(results_dir, "sensitivity_chi2_correlations.png")
    matrix_png = os.path.join(results_dir, "sensitivity_chi2_corr_matrix.png")
    write_chi2_csv(corr_csv, param_names, corr, channel_labels)
    plot_chi2_correlations(corr_png, param_names, corr, channel_labels, n_used, normalize)
    matrix_labels = param_names + [f"chi2_e{e}" for (e, k) in channels]
    plot_corr_matrix(matrix_png, matrix_labels, A)
    print(f"[chi2] wrote {corr_csv}")
    print(f"[chi2] wrote {corr_png}")
    print(f"[chi2] wrote {matrix_png}")

    mean_abs = np.nanmean(np.abs(corr), axis=0)
    order = np.argsort(-np.nan_to_num(mean_abs))
    print("[chi2] top parameters by mean |chi^2 correlation| across channels:")
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
        default="both",
        help="Which analysis to run (default: both).",
    )
    parser.add_argument(
        "--experimental-dir",
        default=DEFAULT_EXPERIMENTAL_DIR,
        help="Directory of Paul-format Qubit_k Delta-Gamma-vs-delay files for the "
        "chi^2 method. Default: %(default)s",
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
        "--chi2-channels",
        choices=["qubits", "electrodes"],
        default="electrodes",
        help="chi^2 output channels: 'electrodes' scores all 17 electrodes, each "
        "against the nearest experimental qubit's curve (17 lines, fits this "
        "device); 'qubits' scores only the 6 experimental qubits against their "
        "nearest electrode (Paul's literal 6-line reproduction). Default: electrodes.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=int(os.environ.get("SENSITIVITY_PROGRESS_EVERY", "500")),
        help="Progress interval when deriving integrated outcomes from hits (default: 500).",
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
            results_dir, sequence, entries, args.experimental_dir,
            args.chi2_normalize, args.chi2_channels,
        )


if __name__ == "__main__":
    main()
