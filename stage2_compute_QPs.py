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
    python stage2_compute_QPs.py /path/to/results/<run_id>
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
    """From Paul's SensitivityAnalysis.ipynb: Bins phonon energy deposited on the sensor surface into quasiparticles
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


def resolve_hits_path(path, results_dir):
    """Absolute path for a manifest hits entry.

    Current manifests store paths relative to results/<run_id> so a run
    directory is portable; legacy manifests store absolute paths. Both are
    accepted, and the relative form is resolved against the directory the caller
    actually named -- which is what makes a copied or relocated run read its OWN
    hits rather than the original's.
    """
    if not path:
        return None
    return path if os.path.isabs(path) else os.path.join(str(results_dir), path)


def entry_hits_paths(entry, results_dir):
    """Resolved hits paths for one manifest entry, in manifest order."""
    raw = entry.get("hits_files") or [entry.get("hits_file")]
    return [resolve_hits_path(p, results_dir) for p in raw if p]


def assert_manifest_identities(entries, results_dir):
    """Validate every entry's declared sub-run identity before any scoring.

    Closes two gaps that a per-file existence check cannot see:

    * **Ragged identity lists.** `hits_files`, `done_markers`, `seeds` and
      `position_indices` must all be the same length. If `done_markers` were
      short, the completion check would be silently skipped for the trailing
      positions -- proof-of-completion that quietly stops applying is worse than
      none, because the summary still says the set was complete.
    * **Position identity.** `position_indices` must be exactly {0..N-1}: no
      duplicates, no gaps. Two hits files for the same site would be pooled as
      if they were two different sites, double-weighting one region of the chip.
    """
    for entry in entries:
        name = entry.get("sample_name", "<unnamed>")
        hits = entry.get("hits_files")
        if hits is None:  # legacy single-file manifest
            continue
        n = len(hits)
        for field in ("done_markers", "seeds", "position_indices"):
            value = entry.get(field)
            if value is None:
                raise ValueError(
                    f"{name}: manifest declares {n} hits file(s) but has no {field!r}. "
                    f"This manifest predates the completion-marker/identity contract; "
                    f"re-generate it with the current stage 1."
                )
            if len(value) != n:
                raise ValueError(
                    f"{name}: {field!r} has {len(value)} entries but there are {n} "
                    f"hits files. A ragged identity list means the check silently "
                    f"stops applying to the trailing sub-runs."
                )
        positions = list(entry["position_indices"])
        if sorted(positions) != list(range(n)):
            raise ValueError(
                f"{name}: position_indices {positions} is not exactly 0..{n - 1}. "
                f"Duplicated or missing sites would be pooled as if they were "
                f"distinct injection points, mis-weighting the device average."
            )


def assert_no_duplicate_identities(entries):
    """Every (design_point, replica) must appear exactly once.

    A duplicated identity would be pooled or double-counted downstream while
    every file still looked well formed -- the silent-degradation shape. Cheap
    to check, impossible to notice later.
    """
    seen = {}
    for entry in entries:
        key = (entry.get("design_point", entry.get("sample_name")), entry.get("replica", 0))
        seen.setdefault(key, 0)
        seen[key] += 1
    dupes = {k: n for k, n in seen.items() if n > 1}
    if dupes:
        listed = ", ".join(f"{dp!r} r{r} x{n}" for (dp, r), n in list(dupes.items())[:5])
        raise ValueError(
            f"{len(dupes)} duplicate (design_point, replica) identit(y/ies) in the "
            f"manifest: {listed}. Refusing to run -- duplicates would be silently "
            f"double-counted."
        )


def load_position_hits(entry, results_dir, require_complete=True):
    """Per-source-position hits for one manifest entry.

    An entry spans several injection positions (`hits_files`); the design
    point's response is the device average over sites, so they are pooled.
    Older single-position manifests carry `hits_file` instead and are accepted.

    ``require_complete`` (the default) means EVERY declared position must be
    present and readable, or the entry yields nothing. This is the correct
    policy for an optimization objective and is deliberately strict:

    * the positions are a fixed spatial quadrature, not interchangeable IID
      draws, so dropping one changes the estimand rather than just its variance;
    * site-to-site QP yield was measured to span 9.18x (CV 60.6%) at fixed
      material, so losing a high-QP site makes a candidate look artificially
      good -- a bias, and one pointing in the flattering direction;
    * sub-run failure is not independent of the candidate. The measured
      `vtrans > vsound` SIGSEGV was deterministic per design point, so exactly
      the materials that crash would be the ones scored on a favourable subset.

    Passing ``require_complete=False`` restores best-effort pooling with n_sim
    rescaling. That is a labelled DIAGNOSTIC path only -- never the objective.

    Frames are index-reset because calculate_QPs indexes its Series by label
    after taking POSITIONAL indices from np.where; the two coincide only on a
    contiguous 0..n-1 index. Concatenating without this silently mis-pairs rows.
    """
    hits_files = entry_hits_paths(entry, results_dir)
    markers = [resolve_hits_path(m, results_dir) for m in entry.get("done_markers", [])]
    frames, labels, missing = [], [], []
    for index, hits_file in enumerate(hits_files):
        # Positive completion proof when stage 1 recorded one. The hits header is
        # written when /run/beamOn STARTS, so file existence alone cannot
        # distinguish a finished run from one killed mid-loop.
        if markers and not os.path.exists(markers[index]):
            missing.append((os.path.basename(hits_file), "no completion marker"))
            continue
        if not os.path.exists(hits_file):
            missing.append((os.path.basename(hits_file), "missing"))
            continue
        if os.path.getsize(hits_file) == 0:
            missing.append((os.path.basename(hits_file), "zero-byte"))
            continue
        try:
            frames.append(pd.read_csv(hits_file).reset_index(drop=True))
            labels.append(os.path.basename(hits_file))
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            missing.append((os.path.basename(hits_file), f"unparseable ({type(exc).__name__})"))
    if require_complete and missing:
        return [], [], 0, len(hits_files), missing
    return frames, labels, len(frames), len(hits_files), missing


def write_qp_summary_row(summary_file, sample_name, gap, num_hits, qp_totals, peak_DG,
                         total_integrated_DG, design_point=None, replica=None,
                         n_sim=None, n_positions_present=None, n_positions_expected=None,
                         positions_complete=None):
    write_header = not os.path.exists(summary_file)
    with open(summary_file, "a", newline="") as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            # design_point/replica identify the row: with replicas kept separate,
            # sample_name alone no longer identifies a configuration.
            header = ["sample_name", "design_point", "replica", "top_gap_eV",
                      "num_surface_hits"]
            header += ["electrode_" + str(i) + "_QPs" for i in range(len(qp_totals))]
            header += ["total_QPs", "peak_DG_MHz", "total_integrated_DG",
                       "n_sim", "n_positions_present", "n_positions_expected",
                       "positions_complete", "QPs_per_primary"]
            writer.writerow(header)
        total_qps = float(np.sum(qp_totals))
        row = [sample_name, design_point, replica, gap, num_hits]
        row += list(qp_totals)
        row += [total_qps, peak_DG, total_integrated_DG,
                n_sim, n_positions_present, n_positions_expected, positions_complete,
                (total_qps / n_sim) if n_sim else ""]
        writer.writerow(row)


def process_manifest_entry(entry, qps_dir, summary_file, results_dir,
                           require_complete=True):
    sample_name = entry["sample_name"]
    frames, labels, n_present, n_expected, missing = load_position_hits(
        entry, results_dir, require_complete=require_complete)
    if require_complete and missing:
        detail = ", ".join(f"{name} ({why})" for name, why in missing[:4])
        print(
            f"QP: INCOMPLETE {sample_name}; {len(missing)}/{n_expected} sub-run(s) "
            f"unusable [{detail}]. Not scored -- the injection sites are a fixed "
            f"spatial quadrature, so a partial set is a different estimand, biased "
            f"toward whichever sites survived. Re-run the missing sub-run(s)."
        )
        return
    if n_present == 0:
        print(
            f"QP: skipping {sample_name}; none of its {n_expected} hits file(s) is "
            f"readable (missing, empty, or corrupt)"
        )
        return
    if n_present < n_expected:
        print(
            f"QP: WARNING {sample_name} pooled {n_present}/{n_expected} positions "
            f"(--allow-partial-positions). DIAGNOSTIC ONLY -- biased toward the "
            f"surviving sites; do not use for candidate selection."
        )

    rec = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    qx = np.array(entry["qx"])
    qy = np.array(entry["qy"])
    gap = entry["gap"]
    chip_z = entry["chip_z"]

    # n_sim must count only the events that actually contributed, or a lost
    # position would look like a genuine drop in QP yield.
    n_sim_per_position = entry.get("n_sim_per_position", entry["n_sim"])
    n_sim = n_sim_per_position * n_present

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
        n_sim=n_sim,
        n_positions_present=n_present,
        n_positions_expected=n_expected,
        position_files=np.array(labels),
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
        n_sim_manifest=entry["n_sim"],
        n_positions_present=n_present,
        n_positions_expected=n_expected,
        f_01=entry["f_01"],
        r=entry["r"],
        s=entry["s"],
        I_ph=entry["I_ph"],
        pt=entry["pt"],
        n_cooper=entry["n_cooper"],
    )

    write_qp_summary_row(
        summary_file, sample_name, gap, len(rec), qp_totals, peak_DG, total_integrated_DG,
        design_point=entry.get("design_point"), replica=entry.get("replica"),
        n_sim=n_sim, n_positions_present=n_present, n_positions_expected=n_expected,
        positions_complete=(n_present == n_expected),
    )

    total_qps = float(qp_totals.sum())
    print(
        f"QP: {sample_name} gap={gap} eV, positions={n_present}/{n_expected}, "
        f"surface hits={len(rec)}, total QPs={total_qps:.0f}, "
        f"per-electrode max={qp_totals.max():.0f}, "
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
        default=Path(
            "./results/"
            "morris_mimir_95494c3c-4c72-4d47-8e92-fbc88f3a0517"),
        help=(
            "Path to the results/<run_id> directory written by "
            "stage1_run_simulations.py. The directory must contain "
            "qp_manifest.jsonl and hits/. "
            "Default: %(default)s"),
    )
    parser.add_argument(
        "--allow-partial-positions",
        action="store_true",
        help=(
            "DIAGNOSTIC ONLY. Pool a design point's surviving injection positions "
            "when some are missing, instead of refusing to score it. The positions "
            "are a fixed spatial quadrature (9.18x site-to-site spread), so a "
            "partial set is biased toward the sites that survived and must never "
            "feed candidate selection."),
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

    # Every re-run recomputes from scratch: wipe prior qps/*.npz and
    # qp_summary.csv first, rather than appending to them. Without this, a
    # second run would duplicate qp_summary.csv rows, and stale npz/rows
    # would linger for samples dropped from a regenerated manifest.
    shutil.rmtree(qps_dir, ignore_errors=True)
    os.makedirs(qps_dir, exist_ok=True)
    if os.path.exists(summary_file):
        os.remove(summary_file)

    entries = load_manifest(manifest_file)
    assert_no_duplicate_identities(entries)
    assert_manifest_identities(entries, results_dir)
    print(f"Loaded {len(entries)} sample manifest entries from {manifest_file}")
    if args.allow_partial_positions:
        print(
            "WARNING: --allow-partial-positions is set. Incomplete scenario sets "
            "will be pooled and marked positions_complete=False. This is a "
            "diagnostic mode; do not select or promote candidates from these rows."
        )
    print(f"Writing per-sample QP results to: {qps_dir}")
    print(f"QP summary CSV: {summary_file}")

    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        process_manifest_entry(entry, qps_dir, summary_file, results_dir,
                               require_complete=not args.allow_partial_positions)
        if index <= 5 or index % args.progress_every == 0 or index == total:
            print(f"Processed {index}/{total} samples")

    # Completeness is the headline number for an optimization run: a silently
    # short scenario set is the failure this stage exists to prevent.
    if os.path.exists(summary_file):
        scored = pd.read_csv(summary_file)
        complete = int(scored["positions_complete"].sum()) if "positions_complete" in scored else len(scored)
        print(
            f"\nScored {len(scored)}/{total} manifest entries; "
            f"{complete} with a COMPLETE scenario set, {len(scored) - complete} partial, "
            f"{total - len(scored)} not scored."
        )
        if total - len(scored):
            print("Re-run the missing sub-runs before using these results for selection.")
    else:
        print(f"\nNo entries scored out of {total}.")


if __name__ == "__main__":
    main()
