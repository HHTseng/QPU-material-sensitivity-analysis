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

from collections import Counter, defaultdict

# Fail closed on incomplete source-position sets unless explicitly overridden
# (--allow-partial-positions). See process_manifest_entry for why a partial
# design point is a different measurement, not a noisier one.
REQUIRE_COMPLETE_POSITIONS = True

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


def resolve_hits_path(path, results_dir):
    """Absolute path for a manifest hits entry.

    New manifests store paths relative to results/<run_id> so a run directory is
    portable; legacy manifests store absolute paths. Both are accepted, and the
    relative form is resolved against the directory the caller actually named --
    which is what makes a copied or relocated run read its OWN hits rather than
    the original's.
    """
    if not path:
        return None
    return path if os.path.isabs(path) else os.path.join(str(results_dir), path)


def entry_hits_paths(entry, results_dir):
    """Resolved hits paths for one manifest entry, in manifest order."""
    raw = entry.get("hits_files") or [entry.get("hits_file")]
    return [resolve_hits_path(p, results_dir) for p in raw if p]


def preflight_manifest(entries, results_dir, expected_positions=None,
                       expected_replicas=None, expected_entries=None,
                       require_complete_positions=True):
    """Validate the WHOLE run before anything is deleted or written.

    Stage 2's wipe of qps/ and qp_summary.csv is destructive and unconditional,
    so any check that fires during processing fires too late -- the previous
    results are already gone and the run is left with neither the old outputs
    nor new ones. That failure shape is documented in the README (a check that
    validated an artifact using a quantity derived from that same artifact), and
    a per-entry completeness check reintroduces a variant of it. Hence: every
    structural check runs here, up front, and returns ALL problems rather than
    the first, so one pass tells the caller everything to fix.

    Checks, in the order they can mislead:
      * duplicate sample_name          -- two rows would both be processed and
                                          the second would overwrite the first's
                                          npz while both land in the summary
      * duplicate (design_point, replica)
      * replica set has holes          -- a design point averaged over {0, 2}
                                          looks identical to one over {0, 1}
      * duplicated hits path within an entry -- the same sub-run pooled twice,
                                          which double-counts its statistics
      * position count complete        -- fixed quadrature, see
                                          process_manifest_entry
    Returns a list of human-readable problem strings; empty means clean.
    """
    problems = []
    seen_names = Counter(e.get("sample_name", "<unnamed>") for e in entries)
    for name, count in sorted(seen_names.items()):
        if count > 1:
            problems.append(f"duplicate sample_name {name!r} appears {count}x")

    # Entry count against the DECLARED design, not against itself. A manifest
    # that lost its last replica everywhere has a perfectly contiguous 0..R-2
    # set in every design point and is invisible to self-consistency checks.
    if expected_entries is not None and len(entries) != expected_entries:
        problems.append(
            f"manifest has {len(entries)} entries, run_metadata declares "
            f"{expected_entries} (n_design_points x n_replicas)")

    by_point = defaultdict(list)
    for entry in entries:
        by_point[entry.get("design_point", entry.get("sample_name"))].append(
            entry.get("replica", -1))
    for point, replicas in sorted(by_point.items()):
        counted = Counter(replicas)
        dupes = sorted(r for r, c in counted.items() if c > 1)
        if dupes:
            problems.append(f"{point}: duplicate replica id(s) {dupes}")
        present = sorted(counted)
        if expected_replicas is not None:
            if present != list(range(expected_replicas)):
                problems.append(
                    f"{point}: replica ids {present}, expected "
                    f"{list(range(expected_replicas))} from run_metadata")
        elif present and present != list(range(len(present))):
            problems.append(
                f"{point}: replica ids {present} are not a contiguous 0..{len(present)-1} "
                f"set -- a missing replica silently changes the average")

    # Hits paths must be globally unique, not merely unique within an entry:
    # two entries sharing a path double-count that sub-run across replicas,
    # which correlates measurements that are meant to be independent.
    global_paths = Counter()
    for entry in entries:
        for path in entry_hits_paths(entry, results_dir):
            global_paths[path] += 1
    shared = sorted(p for p, c in global_paths.items() if c > 1)
    for path in shared[:10]:
        owners = sorted(e.get("sample_name", "<unnamed>") for e in entries
                        if path in entry_hits_paths(e, results_dir))
        problems.append(
            f"hits path {os.path.basename(path)} is claimed by {len(owners)} entries "
            f"({owners[:4]}) -- would be counted more than once")
    if len(shared) > 10:
        problems.append(f"... and {len(shared) - 10} further shared hits path(s)")

    for entry in entries:
        name = entry.get("sample_name", "<unnamed>")
        files = entry_hits_paths(entry, results_dir)
        # LISTED count against the declared P. An entry that lists only 31 of 32
        # positions is self-consistent -- present == listed -- and so passes any
        # check that compares the two.
        if expected_positions is not None and len(files) != expected_positions:
            problems.append(
                f"{name}: manifest lists {len(files)} hits files, run_metadata "
                f"declares n_positions={expected_positions}")
        repeated = sorted(f for f, c in Counter(files).items() if c > 1)
        if repeated:
            problems.append(
                f"{name}: hits path(s) listed more than once "
                f"{[os.path.basename(f) for f in repeated]} -- would be pooled twice")
        present = sum(1 for f in files
                      if os.path.exists(f) and os.path.getsize(f) > 0)
        expected_present = expected_positions if expected_positions is not None else len(files)
        if require_complete_positions and present < expected_present:
            problems.append(
                f"{name}: {present}/{expected_present} position files present")

    return problems


def write_qp_summary_row(summary_file, sample_name, gap, num_hits, qp_totals, peak_DG, total_integrated_DG,
                         n_sim=float("nan"), design_point="", replica=-1,
                         position_QPs=None, position_max_electrode=None,
                         positions_present=-1, positions_expected=-1):
    """Append one (design point, replica) row.

    The per-position columns are APPENDED at the end and every existing column
    keeps its name and meaning, because stage 2b and stage 3 select columns by
    name (pandas) rather than by position. Adding them in the middle would be
    silently destructive for any consumer that did slice.
    """
    write_header = not os.path.exists(summary_file)
    with open(summary_file, "a", newline="") as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            header = ["sample_name", "design_point", "replica", "n_sim", "top_gap_eV", "num_surface_hits"]
            header += ["electrode_" + str(i) + "_QPs" for i in range(len(qp_totals))]
            header += ["total_QPs", "max_electrode_QPs", "QP_yield_per_event",
                       "peak_DG_MHz", "total_integrated_DG"]
            # Per-position block (appended 2026-07-29). Absolute QP yield at
            # P=16 carries a spatial-quadrature term that more events cannot
            # reduce, so these record how much of the design point's signal came
            # from how few injection sites.
            header += ["positions_present", "positions_expected",
                       "position_QP_mean", "position_QP_sd",
                       "position_QP_max", "position_QP_min",
                       "position_effective_n", "position_max_electrode_QPs"]
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

        pq = np.asarray(position_QPs if position_QPs is not None else [], dtype=float)
        pm = np.asarray(position_max_electrode if position_max_electrode is not None else [], dtype=float)
        nan = float("nan")
        row += [
            positions_present, positions_expected,
            float(pq.mean()) if pq.size else nan,
            # ddof=1: the spread ACROSS positions is an estimate from a finite
            # set of sites, not the population value of the site distribution.
            float(pq.std(ddof=1)) if pq.size > 1 else nan,
            float(pq.max()) if pq.size else nan,
            float(pq.min()) if pq.size else nan,
            participation_ratio(pq) if pq.size else nan,
            float(pm.max()) if pm.size else nan,
        ]
        writer.writerow(row)


def load_position_hits(entry, results_dir):
    """Per-source-position hits for one manifest entry, kept SEPARATE.

    An entry spans several source positions (`hits_files`). The design point's
    response is the device average over injection sites, so the positions are
    ultimately pooled -- but they are loaded separately here so per-position
    metrics can be computed on the way (see process_manifest_entry). Older
    single-position manifests carry `hits_file` instead; both are accepted so
    this stage still reads runs made before the screening protocol.

    Returns (frames, labels, n_present, n_expected). Missing or unreadable files
    are skipped individually rather than discarding the whole design point:
    losing 1 of 16 positions to a SIGSEGV should cost 1/16 of the statistics,
    not the entire sample. n_present/n_expected let the caller rescale n_sim so
    the yield-per-event stays correct when a position is lost.

    Frames are index-reset because calculate_QPs indexes its Series by label
    after taking POSITIONAL indices from np.where; the two coincide only on a
    contiguous 0..n-1 index.
    """
    hits_files = entry_hits_paths(entry, results_dir)
    frames, labels = [], []
    for hits_file in hits_files:
        if not os.path.exists(hits_file) or os.path.getsize(hits_file) == 0:
            continue
        try:
            frames.append(pd.read_csv(hits_file).reset_index(drop=True))
            labels.append(os.path.basename(hits_file))
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    return frames, labels, len(frames), len(hits_files)


def participation_ratio(values):
    """Effective number of contributing positions, (sum x)^2 / sum x^2.

    The diagnostic used in the Stage-4 plan's positional-variance analysis: it
    is len(values) when every position contributes equally and falls toward 1
    when a single caustic dominates. Reported per design point so the P=16
    quadrature can be re-checked on any new run rather than trusted from the
    one noise-floor study that established it.
    """
    values = np.asarray(values, dtype=float)
    denominator = float(np.sum(values ** 2))
    if denominator <= 0:
        return float("nan")
    return float(np.sum(values) ** 2 / denominator)


def process_manifest_entry(entry, qps_dir, summary_file, results_dir):
    sample_name = entry["sample_name"]
    frames, labels, n_present, n_expected = load_position_hits(entry, results_dir)
    if not frames:
        print(
            f"QP: skipping {sample_name}; none of its {n_expected} hits file(s) "
            f"were readable (missing, empty, or corrupt)"
        )
        return
    if n_present < n_expected:
        if REQUIRE_COMPLETE_POSITIONS:
            # Fail closed. The 16 sites are a fixed spatial QUADRATURE, not a
            # random sample: per-position yields span roughly 9x, so dropping a
            # high-signal site removes a large share of the signal while dropping
            # a dead one removes almost none. Rescaling n_sim keeps the RATE
            # unbiased only if the lost site was average, which it is not. For an
            # optimizer comparing candidates that is a silent, design-dependent
            # bias, so a partial design point must be rejected rather than
            # accepted at reduced weight.
            raise ValueError(
                f"{sample_name}: {n_present}/{n_expected} position files present. "
                f"Positions are a fixed quadrature, so a partial design point is "
                f"not a lower-precision measurement of the same quantity -- it is a "
                f"different one. Re-run the missing sub-runs, or pass "
                f"--allow-partial-positions to accept the rescaled estimate."
            )
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

    # Per position first, then sum. calculate_QPs only ACCUMULATES into
    # QPNos[electrode, time_bin], so summing the per-position arrays is exactly
    # the pooled result -- identical, not merely close, because the increments
    # are int(round(E/gap)) and integer sums do not reassociate. This buys the
    # per-position breakdown for no extra work: the alternative (pool once, then
    # loop again per position) would double the cost.
    per_position_QPNos = []
    times = None
    for frame in frames:
        times, QPNos_p = calculate_QPs(frame, gap, qx, qy, chip_z)
        per_position_QPNos.append(QPNos_p)
    QPNos = np.sum(per_position_QPNos, axis=0)
    qp_totals = QPNos.sum(axis=1)
    num_hits = int(sum(len(frame) for frame in frames))

    # Per-position scalars. `position_QPs` is the quantity the plan's positional
    # -variance analysis is built from; `position_max_electrode` supports a
    # worst-qubit objective without recomputation.
    position_QPs = np.array([float(p.sum()) for p in per_position_QPNos])
    position_max_electrode = np.array(
        [float(p.sum(axis=1).max()) if p.size else 0.0 for p in per_position_QPNos]
    )

    np.savez(
        os.path.join(qps_dir, sample_name + "_QPs.npz"),
        times=times,
        QPNos=QPNos,
        qx=qx,
        qy=qy,
        gap=gap,
        chip_z=chip_z,
        # Per-position breakdown. Retained in full (electrode x time per
        # position) so a spatial or tail-risk objective can be built later
        # without re-reading the hits files, which is the expensive step.
        per_position_QPNos=np.array(per_position_QPNos),
        position_QPs=position_QPs,
        position_max_electrode=position_max_electrode,
        position_files=np.array(labels),
        positions_present=n_present,
        positions_expected=n_expected,
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

    write_qp_summary_row(summary_file, sample_name, gap, num_hits, qp_totals, peak_DG, total_integrated_DG,
                         n_sim=n_sim, design_point=entry.get("design_point", ""),
                         replica=entry.get("replica", -1),
                         position_QPs=position_QPs,
                         position_max_electrode=position_max_electrode,
                         positions_present=n_present, positions_expected=n_expected)

    total_qps = float(qp_totals.sum())
    print(
        f"QP: {sample_name} gap={gap} eV, surface hits={num_hits}, "
        f"total QPs={total_qps:.0f}, per-electrode max={qp_totals.max():.0f}, "
        f"peak DG={peak_DG:.4g} MHz, n_sim={n_sim:.0f}, "
        f"positions={n_present}/{n_expected} (eff {participation_ratio(position_QPs):.1f})"
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
    parser.add_argument(
        "--allow-partial-positions",
        action="store_true",
        help=(
            "Accept a design point whose source positions are incomplete, "
            "rescaling n_sim. OFF by default: the positions are a fixed spatial "
            "quadrature with a ~9x spread in per-site yield, so a missing site "
            "biases the result by a design-dependent amount rather than merely "
            "reducing precision. Use only for exploratory reprocessing, never "
            "for a design point that will be compared against another."),
    )
    args = parser.parse_args()

    global REQUIRE_COMPLETE_POSITIONS
    REQUIRE_COMPLETE_POSITIONS = not args.allow_partial_positions

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
        for hits_file in entry_hits_paths(entry, results_dir):
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

    # Whole-run structural preflight, BEFORE the destructive wipe below. A
    # problem found here costs nothing; the same problem found during processing
    # costs the run's previous outputs.
    # Declared expectations, so the preflight checks the manifest against the
    # DESIGN rather than against itself. Legacy runs have no run_metadata.json;
    # they fall back to self-consistency with a loud warning, because inferring
    # "expected" from the observed data is precisely the failure mode the README
    # documents (stage 3 once took the MEDIAN observed replica count as expected,
    # which tracks the damage instead of detecting it).
    meta_path = os.path.join(results_dir, "run_metadata.json")
    exp_positions = exp_replicas = exp_entries = None
    if os.path.exists(meta_path):
        with open(meta_path) as handle:
            fidelity = json.load(handle).get("fidelity", {})
        exp_positions = fidelity.get("n_positions")
        exp_replicas = fidelity.get("n_replicas")
        exp_entries = fidelity.get("n_manifest_entries")
        print(f"Preflight expectations from run_metadata.json: "
              f"P={exp_positions}, R={exp_replicas}, entries={exp_entries}")
    else:
        print("WARNING: no run_metadata.json; preflight can only check "
              "self-consistency. A manifest missing its last replica everywhere, "
              "or listing P-1 positions in every entry, will NOT be detected.")

    problems = preflight_manifest(
        entries, results_dir,
        expected_positions=exp_positions, expected_replicas=exp_replicas,
        expected_entries=exp_entries,
        require_complete_positions=REQUIRE_COMPLETE_POSITIONS)
    if problems:
        listed = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise SystemExit(
            f"Refusing to run: {len(problems)} manifest problem(s) under {results_dir}.\n"
            f"  {listed}{more}\n"
            f"Nothing has been deleted. Re-run the missing sub-runs, or pass "
            f"--allow-partial-positions to accept incomplete position sets."
        )
    print(f"Preflight: {len(entries)} manifest entries validated "
          f"(names, replica sets, position completeness).")

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
        process_manifest_entry(entry, qps_dir, summary_file, results_dir)
        if index <= 5 or index % args.progress_every == 0 or index == total:
            print(f"Processed {index}/{total} samples")


if __name__ == "__main__":
    main()
