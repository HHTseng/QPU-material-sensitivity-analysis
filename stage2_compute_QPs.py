"""
Stage 2 of the Morris sensitivity pipeline: compute quasiparticles (QP) and
the decoherence-rate ODE (xQP) for every sample of a completed simulation
run, adapted from Paul Baity's SensitivityAnalysis.ipynb.

This is intentionally decoupled from Geant4/G4CMP and from the macro
template: it only needs the run's `qp_manifest.jsonl` (written by
stage1_run_simulations.py at macro-generation time) and the
hits files that simulation stage produced. That split exists because running
the simulations and computing QPs together made a single slow run out of two
independently-runnable stages.

Usage:
    python stage2_compute_QPs.py --results-dir /path/to/results/<run_id>
"""

import argparse
from pathlib import Path
import csv
import json
import os
import shutil

import numpy as np
import pandas as pd

QP_SNAPSHOT_COUNT = 1001
QP_SNAPSHOT_MAX_NS = 300000.0


def calculate_QPs(rec, gap, qx, qy, chip_z):
    """Adapted from Paul Baity's SensitivityAnalysis.ipynb (calculate_QPs).
    Bins phonon energy deposited on the sensor surface into quasiparticles
    generated on the nearest electrode, as a function of time.

    Args:
        rec: DataFrame loaded from a Morris_*_hitsfile.txt
        gap: QP creation energy [eV] (that sample's setTopGap value)
        qx, qy: electrode X/Y positions [mm]
        chip_z: sensor-surface End Z [m] to select top-surface hits

    Returns:
        snapshot_t: time bin centers [ns], shape (QP_SNAPSHOT_COUNT,)
        QPNos: quasiparticles generated per electrode per time bin,
            shape (len(qx), QP_SNAPSHOT_COUNT)
    """
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
    """Adapted from Paul Baity's SensitivityAnalysis.ipynb (calculate_xQPs,
    final/cell-19 form). Solves a quasiparticle-density ODE per electrode
    and converts it to a qubit decoherence-rate contribution (MHz).

    f_01 and s are scalars here (broadcast across every electrode) rather
    than Paul's per-qubit vectors, since this geometry has more electrodes
    than his 6-qubit device had calibration values for.

    n_sim (the manifest's actual /run/beamOn primary-event count) replaces
    Paul's hardcoded N_sim, which was inconsistent across his notebook cells.

    Args:
        snapshot_t: time bin centers [ns] from calculate_QPs
        QPNos: quasiparticles generated per electrode per time bin
        AlGap: superconducting gap frequency [Hz] (= gap[eV] / 4.135667696e-15)
        f_01: qubit resonant frequency [Hz]
        r: QP-QP recombination coefficient
        s: QP loss rate
        I_ph: injection current-like scale factor
        pt: pulse time [us]
        n_cooper: Cooper pair density
        height, width: electrode lateral dimensions [um]
        thickness: top-layer thickness [um]
        n_sim: number of simulated primary events (this sample's /run/beamOn)

    Returns:
        t: time vector [us], shape (len(snapshot_t) + round(pt/dt),)
        DG: decoherence-rate contribution per electrode [MHz],
            shape (QPNos.shape[0], len(t))
    """
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


def write_qp_summary_row(summary_file, sample_name, gap, num_hits, qp_totals, peak_DG, total_integrated_DG,
                         n_sim=float("nan"), design_point="", replica=-1):
    write_header = not os.path.exists(summary_file)
    with open(summary_file, "a", newline="") as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            header = ["sample_name", "design_point", "replica", "n_sim", "top_gap_eV", "num_surface_hits"]
            header += ["electrode_" + str(i) + "_QPs" for i in range(len(qp_totals))]
            header += ["total_QPs", "max_electrode_QPs", "QP_yield_per_event",
                       "peak_DG_MHz", "total_integrated_DG"]
            writer.writerow(header)
        row = [sample_name, design_point, replica, n_sim, gap, num_hits]
        row += list(qp_totals)
        total_qps = float(np.sum(qp_totals))
        # QP_yield_per_event is the screening objective: a rate, so it is
        # comparable across different event counts and its Poisson variance
        # is known analytically (lambda/n) rather than having to be learned.
        row += [total_qps, float(np.max(qp_totals)) if len(qp_totals) else 0.0,
                total_qps / n_sim if n_sim else float("nan"),
                peak_DG, total_integrated_DG]
        writer.writerow(row)


def load_pooled_hits(entry):
    """Concatenated hits for one manifest entry.

    An entry may span several source positions (`hits_files`), which are pooled
    because the design point's response is the device average over injection
    sites. Older single-position manifests carry `hits_file` instead; both are
    accepted so this stage still reads runs made before the screening protocol.

    Returns (DataFrame, n_present, n_expected). Missing or unreadable files are
    skipped individually rather than discarding the whole design point: losing
    1 of 16 positions to a SIGSEGV should cost 1/16 of the statistics, not the
    entire sample. n_present/n_expected let the caller rescale n_sim so the
    yield-per-event stays correct when a position is lost.
    """
    hits_files = entry.get("hits_files") or [entry["hits_file"]]
    frames = []
    for hits_file in hits_files:
        if not os.path.exists(hits_file) or os.path.getsize(hits_file) == 0:
            continue
        try:
            frames.append(pd.read_csv(hits_file))
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    if not frames:
        return None, 0, len(hits_files)
    return pd.concat(frames, ignore_index=True), len(frames), len(hits_files)


def process_manifest_entry(entry, qps_dir, summary_file):
    sample_name = entry["sample_name"]
    rec, n_present, n_expected = load_pooled_hits(entry)
    if rec is None:
        print(
            f"QP: skipping {sample_name}; none of its {n_expected} hits file(s) "
            f"were readable (missing, empty, or corrupt)"
        )
        return
    if n_present < n_expected:
        print(
            f"QP: {sample_name}: only {n_present}/{n_expected} position files "
            f"readable; n_sim scaled down accordingly"
        )
    qx = np.array(entry["qx"])
    qy = np.array(entry["qy"])
    gap = entry["gap"]
    chip_z = entry["chip_z"]
    # Rescale for any position files that were lost, so QPs-per-event (the
    # quantity the screen actually compares) stays unbiased.
    n_sim = entry["n_sim"] * (n_present / n_expected)

    times, QPNos = calculate_QPs(rec, gap, qx, qy, chip_z)
    qp_totals = QPNos.sum(axis=1)

    np.savez(
        os.path.join(qps_dir, sample_name + "_QPs.npz"),
        times=times,
        QPNos=QPNos,
        qx=qx,
        qy=qy,
        gap=gap,
        chip_z=chip_z,
    )

    AlGap = gap / 4.135667696e-15
    t, DG = calculate_xQPs(
        times,
        QPNos,
        AlGap,
        entry["f_01"],
        entry["r"],
        entry["s"],
        entry["I_ph"],
        entry["pt"],
        entry["n_cooper"],
        entry["height"],
        entry["width"],
        entry["thickness"],
        n_sim,
    )
    peak_DG = float(DG.max()) if DG.size else 0.0
    total_integrated_DG = float(np.sum(np.trapezoid(DG, t, axis=1))) if DG.size else 0.0

    np.savez(
        os.path.join(qps_dir, sample_name + "_xQPs.npz"),
        t=t,
        DG=DG,
        AlGap=AlGap,
        height=entry["height"],
        width=entry["width"],
        thickness=entry["thickness"],
        n_sim=n_sim,
        f_01=entry["f_01"],
        r=entry["r"],
        s=entry["s"],
        I_ph=entry["I_ph"],
        pt=entry["pt"],
        n_cooper=entry["n_cooper"],
    )

    write_qp_summary_row(summary_file, sample_name, gap, len(rec), qp_totals, peak_DG, total_integrated_DG,
                         n_sim=n_sim, design_point=entry.get("design_point", ""),
                         replica=entry.get("replica", -1))

    total_qps = float(qp_totals.sum())
    print(
        f"QP: {sample_name} gap={gap} eV, surface hits={len(rec)}, "
        f"total QPs={total_qps:.0f}, per-electrode max={qp_totals.max():.0f}, "
        f"peak DG={peak_DG:.4g} MHz, n_sim={n_sim:.0f}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Stage 2: compute quasiparticles/decoherence rate from a "
        "completed Morris simulation run's hits files."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help=(
            "Path to the results/<run_id> directory written by "
            "stage1_run_simulations.py. The directory must contain "
            "qp_manifest.jsonl and hits/. REQUIRED: this stage deletes and "
            "rewrites the target run's qps/ and qp_summary.csv, so it must "
            "never be able to act on a directory the caller did not name. "
            "(It previously defaulted to a hardcoded run id, which destroyed "
            "that run's qp_summary.csv when the run was invoked with no args "
            "after its hits/ had been archived away.)"),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=int(os.environ.get("SENSITIVITY_PROGRESS_EVERY", "25")),
        help="Print a progress line every N samples (default: 25).",
    )
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    manifest_file = os.path.join(results_dir, "qp_manifest.jsonl")
    if not os.path.exists(manifest_file):
        raise FileNotFoundError(
            f"QP manifest not found: {manifest_file}. Run the simulation "
            "stage (stage1_run_simulations.py) first."
        )

    qps_dir = os.path.join(results_dir, "qps")
    summary_file = os.path.join(results_dir, "qp_summary.csv")

    entries = load_manifest(manifest_file)

    # Validate the INPUTS before destroying the OUTPUTS.
    #
    # The wipe below is destructive and unconditional, so if it runs against a
    # directory whose hits/ is missing, every sample is skipped, nothing is
    # rewritten, and the previous qp_summary.csv is simply gone. That is not
    # hypothetical: it destroyed a completed run's summary when this stage was
    # invoked with no arguments and its hardcoded default pointed at a run
    # whose hits/ had been archived. Losing derived data because its source is
    # absent is exactly backwards -- the summary is the only surviving record
    # once hits/ is gone.
    #
    # So: refuse to touch anything unless at least one hits file is actually
    # readable. A run with no readable hits has nothing to recompute FROM, and
    # the correct action is to leave its existing results alone.
    readable = 0
    for entry in entries:
        for hits_file in (entry.get("hits_files") or [entry.get("hits_file")]):
            if hits_file and os.path.exists(hits_file) and os.path.getsize(hits_file) > 0:
                readable += 1
                break
        if readable:
            break
    if readable == 0:
        raise SystemExit(
            f"Refusing to run: no readable hits file for any of the {len(entries)} "
            f"manifest entries under {results_dir}.\n"
            f"Nothing would be recomputed, but qps/ and qp_summary.csv would be "
            f"deleted first, destroying the only surviving results for this run.\n"
            f"If hits/ was archived or removed deliberately, this run's stage 2 "
            f"output is already final and must not be regenerated."
        )

    # Every re-run recomputes from scratch: wipe prior qps/*.npz and
    # qp_summary.csv first, rather than appending to them. Without this, a
    # second run would duplicate qp_summary.csv rows, and stale npz/rows
    # would linger for samples dropped from a regenerated manifest.
    shutil.rmtree(qps_dir, ignore_errors=True)
    os.makedirs(qps_dir, exist_ok=True)
    if os.path.exists(summary_file):
        os.remove(summary_file)

    print(f"Loaded {len(entries)} sample manifest entries from {manifest_file}")
    print(f"Writing per-sample QP results to: {qps_dir}")
    print(f"QP summary CSV: {summary_file}")

    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        process_manifest_entry(entry, qps_dir, summary_file)
        if index <= 5 or index % args.progress_every == 0 or index == total:
            print(f"Processed {index}/{total} samples")


if __name__ == "__main__":
    main()
