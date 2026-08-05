"""
Unified Morris sensitivity-analysis simulation runner (stage 1; see
stage2_compute_QPs.py for stage 2).

SENSITIVITY_DEBUG_MODE=1 -> serial, live-streamed output, verbose diagnostics
SENSITIVITY_DEBUG_MODE=0 -> parallel (SENSITIVITY_MAX_WORKERS workers),
                             log-only output [default]

Both modes share the same Morris design (params from sensitivity_params.py)
and write the same qp_manifest.jsonl; only how each sample is launched and
reported differs.

The same file runs on the M4 Max Mac and on the BNL server mimir: the
Geant4/G4CMP/conda install layout and G4SYSTEM are selected from sys.platform
(see IS_MACOS below), and every path remains overridable via SENSITIVITY_*
environment variables. Keeping one file rather than a per-machine fork is
deliberate -- see the "single source of truth" rationale in
SensitivityAnalysis_Morris_run_record_Mac_M4Max.md.
"""

import numpy as np
from scipy.stats import qmc
import hashlib
import math
import os
import platform
from datetime import datetime, timezone
import sys
import csv
import json
import shlex
import signal
import subprocess
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import uuid
from SALib.sample import morris as morris_samp
from SALib.analyze import morris
unique_id = str(uuid.uuid4())

# ThreadPoolExecutor, not ProcessPoolExecutor: every task here only spawns a
# subprocess and waits on it, so it is I/O bound and threads are sufficient.
# The decisive reason is the memory guard -- with threads the parent process
# owns every sample pid directly and can police total RSS across the whole
# batch. With a process pool the child pids live in worker processes and the
# parent cannot see, sum, or kill them.
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from sensitivity_params import (
    electrode_params,
    detector_params,
    G4CMP_params,
    config_params,
    PINNED_G4CMP_COMMANDS,
    QPDE_params,
    QPDE_PARAM_NAMES,
)
from sensitivity_utils import (
    format_duration,
    format_config_entry,
    find_macro_value,
    should_keep_success_log,
    finalize_log_file,
    replace_line,
)
from sensitivity_memguard import MemoryGuard, host_available_gb


def expand_path(path):
    return os.path.abspath(os.path.expanduser(path))


HOME_DIR = expand_path("~")

# Platform-specific installation layout. The Mac (M4 Max) and the BNL server
# (mimir) keep Geant4/G4CMP/conda in different places and build for different
# G4SYSTEM targets, so the defaults are selected by platform instead of being
# forked into two scripts that could drift apart. Every value below is still
# overridable by its SENSITIVITY_* environment variable, so an unrecognized
# host only needs env vars, not a code change.
IS_MACOS = sys.platform == "darwin"

if IS_MACOS:
    _DEFAULT_G4SYSTEM = "Darwin-clang"
    _DEFAULT_CONDA_ENV = "/opt/anaconda3/envs/G4CMP"
    _DEFAULT_GEANT4_ROOT = os.path.join(HOME_DIR, "Geant4")
    _DEFAULT_G4CMP_ROOT = os.path.join(_DEFAULT_GEANT4_ROOT, "G4CMP")
    _DEFAULT_MAX_WORKERS = "2"
    _PRODUCTION_RUN_ID_PREFIX = "morris_mac_"
else:
    # Linux (mimir): Geant4 is a shared site install, G4CMP is a per-user
    # checkout under ~/src, and conda lives in ~/.conda rather than /opt.
    _DEFAULT_G4SYSTEM = "Linux-g++"
    _DEFAULT_CONDA_ENV = os.path.join(HOME_DIR, ".conda", "envs", "G4CMP")
    _DEFAULT_GEANT4_ROOT = "/home/software/Geant4"
    _DEFAULT_G4CMP_ROOT = os.path.join(HOME_DIR, "src", "G4CMP_htseng")
    _DEFAULT_MAX_WORKERS = "4"
    _PRODUCTION_RUN_ID_PREFIX = "morris_mimir_"

GEANT4_ROOT = expand_path(os.environ.get("SENSITIVITY_GEANT4_ROOT", _DEFAULT_GEANT4_ROOT))
G4CMP_ROOT = expand_path(os.environ.get("SENSITIVITY_G4CMP_ROOT", _DEFAULT_G4CMP_ROOT))
G4WORKDIR = expand_path(os.environ.get("SENSITIVITY_G4WORKDIR", os.path.join(HOME_DIR, "geant4_workdir")))
G4SYSTEM = os.environ.get("SENSITIVITY_G4SYSTEM", _DEFAULT_G4SYSTEM)
CONDA_ENV = expand_path(os.environ.get("SENSITIVITY_CONDA_ENV", _DEFAULT_CONDA_ENV))

PATH_TO_GEANT4_ENV = expand_path(
    os.environ.get(
        "SENSITIVITY_GEANT4_ENV",
        os.path.join(
            GEANT4_ROOT,
            "Geant4-Install",
            "share",
            "Geant4-10.7.4",
            "geant4make",
            "geant4make.sh",
        ),
    )
)
PATH_TO_G4CMP_ENV = expand_path(
    os.environ.get("SENSITIVITY_G4CMP_ENV", os.path.join(G4CMP_ROOT, "g4cmp_env.sh"))
)
G4CMP_SOURCE_CRYSTALMAPS = expand_path(
    os.environ.get(
        "SENSITIVITY_G4CMP_CRYSTALMAPS",
        os.path.join(G4CMP_ROOT, "CrystalMaps"),
    )
)
G4CMPLIB = expand_path(
    os.environ.get("SENSITIVITY_G4CMPLIB", os.path.join(G4WORKDIR, "lib", G4SYSTEM))
)
G4CMPBIN = expand_path(
    os.environ.get("SENSITIVITY_G4CMPBIN", os.path.join(G4WORKDIR, "bin", G4SYSTEM))
)
MAIN_EXE = expand_path(os.environ.get("SENSITIVITY_MAIN_EXE", os.path.join(G4CMPBIN, "Main")))
RUN_WORKDIR = expand_path(os.environ.get("SENSITIVITY_RUN_WORKDIR", SCRIPT_DIR))
MACRO_TEMPLATE = os.environ.get("SENSITIVITY_MACRO_TEMPLATE", os.path.join(SCRIPT_DIR, "sensitivity_template_beamOn1e6.mac"),)
CONFIG_TEMPLATE = os.path.join(G4CMP_SOURCE_CRYSTALMAPS, "Si", "config.txt")
LATTICE_MATERIAL = "Si"

DEBUG_MODE = os.environ.get("SENSITIVITY_DEBUG_MODE", "0") == "1"
MAX_WORKERS = int(os.environ.get("SENSITIVITY_MAX_WORKERS", _DEFAULT_MAX_WORKERS))
GENERATE_ONLY = os.environ.get("SENSITIVITY_GENERATE_ONLY", "0") == "1"
VERBOSE = os.environ.get("SENSITIVITY_VERBOSE", "0") == "1"
PROGRESS_EVERY = int(os.environ.get("SENSITIVITY_PROGRESS_EVERY", "25"))
MAX_SAMPLES = int(os.environ.get("SENSITIVITY_MAX_SAMPLES", "0"))
# Morris trajectories are drawn from numpy's global RNG, so two machines
# produce different designs unless this is set. Setting it to the same integer
# on the Mac and on mimir makes both generate byte-identical macros/configs,
# which is what makes a cross-machine comparison meaningful. Unset (default)
# keeps the historical non-reproducible behavior.
_MORRIS_SEED_RAW = os.environ.get("SENSITIVITY_MORRIS_SEED", "").strip()
MORRIS_SEED = int(_MORRIS_SEED_RAW) if _MORRIS_SEED_RAW else None
# --- Explicit-design mode (Stage 4) ------------------------------------------
# Path to a CSV of design points to simulate INSTEAD of generating a Morris
# design. The header must match the canonical design column names exactly, in
# order -- the same header write_morris_sequence() emits -- because everything
# downstream indexes the sample row POSITIONALLY (generate_runfiles walks one
# cursor across macro_params then config_params, and extract_qpde_params indexes
# by name into that same row). A file with the right columns in the wrong order
# would write every value into the wrong macro command and still run to
# completion, so the header is compared element-by-element and mismatches are
# refused rather than reordered.
#
# This is deliberately the smallest possible extension: the Morris path is
# untouched when the variable is unset, and an explicit design reuses the whole
# existing validation, macro-writing, manifest and seeding chain.
DESIGN_FILE = expand_path(os.environ.get("SENSITIVITY_DESIGN_FILE", "").strip()) if \
    os.environ.get("SENSITIVITY_DESIGN_FILE", "").strip() else ""
# Sample-name prefix. "Morris_" keeps existing runs byte-identical; an explicit
# design defaults to "Design_" so its artifacts cannot be mistaken for a screen.
SAMPLE_PREFIX = os.environ.get(
    "SENSITIVITY_SAMPLE_PREFIX", "Design_" if DESIGN_FILE else "Morris_"
)
# Per-sample wall-clock limit in seconds; 0 (default) disables it. Guards
# against the G4CMPKaplanQP infinite loop documented in
# G4CMP_crash_and_memory_analysis.md (a film with QPLim=1 spins forever and
# can grow to tens of GB of RSS). The kill targets the whole process group,
# because killing the bash wrapper alone leaves an orphaned `Main` at 100% CPU.
SAMPLE_TIMEOUT = float(os.environ.get("SENSITIVITY_SAMPLE_TIMEOUT", "0"))
# --- Stage-3 screening protocol controls -------------------------------------
# Each Morris design point is evaluated as N_POSITIONS x N_REPLICAS separate
# Geant4 processes, because two things cannot be done inside one process:
#   * /g4cmp/HitsFile cannot be re-pointed after /run/initialize (measured: a
#     second /g4cmp/HitsFile mid-macro silently writes nothing), and a second
#     /run/beamOn TRUNCATES the hits file rather than appending (measured: a
#     3 x beamOn macro left only the last run's rows). So one hits file per
#     source position requires one process per source position.
#   * one /run/beamOn per process, so a replica is a separate process too.
#     (CLHEP seeding IS controllable from the macro via /random/setSeeds --
#     see SEED_BASE below -- but the hits-file constraint still forces one
#     process per position/replica.)
N_POSITIONS = int(os.environ.get("SENSITIVITY_N_POSITIONS", "1"))
N_REPLICAS = int(os.environ.get("SENSITIVITY_N_REPLICAS", "1"))
# TOTAL primary phonons per design point -- the ONE event-count knob.
#
# This used to be a per-sub-run count (SENSITIVITY_EVENTS_PER_POSITION), which
# meant the same number silently stood for N_POSITIONS x N_REPLICAS times more
# events depending on unrelated settings. Every human-facing number is now the
# total; stage 1 derives the per-sub-run /run/beamOn as
#
#     events_per_sub_run = TOTAL_EVENTS / (N_POSITIONS * N_REPLICAS)
#
# and refuses to run if that is not an exact positive integer. With one position
# and one replica the two coincide, so a legacy single-position template's
# /run/beamOn *is* the total and nothing changes for it.
#
# 0 means "take the total from the macro template's /run/beamOn".
TOTAL_EVENTS = int(os.environ.get("SENSITIVITY_TOTAL_EVENTS", "0"))
if os.environ.get("SENSITIVITY_EVENTS_PER_POSITION"):
    raise ValueError(
        "SENSITIVITY_EVENTS_PER_POSITION has been removed because its meaning "
        "depended on SENSITIVITY_N_POSITIONS and SENSITIVITY_N_REPLICAS.\n"
        "Use SENSITIVITY_TOTAL_EVENTS -- the TOTAL primary phonons per design "
        "point -- and stage 1 will derive the per-sub-run /run/beamOn.\n"
        "The 2026-07-28 screen used SENSITIVITY_TOTAL_EVENTS=4000000 "
        "(= the old 125000 x 16 positions x 2 replicas)."
    )
# Injection sites are drawn once from a fixed scrambled-Sobol set and reused
# for EVERY design point. Identical sites across samples is the whole point:
# it makes source position a common random number, so position variance
# cancels in the elementary-effect differences instead of inflating them.
POSITION_SEED = int(os.environ.get("SENSITIVITY_POSITION_SEED", "20260727"))
# --- CLHEP seeding -----------------------------------------------------------
# Main.cc does `setTheSeed((unsigned)clock())` at startup, which is NOT safe:
# clock() at process start is nearly constant (it measures CPU time consumed so
# far, dominated by identical initialisation work), so the effective seed space
# is small and streams are reused across sub-runs. Measured on the 200-point
# noise-floor run, where every configuration is identical so a reused stream
# shows up as a byte-identical hits file: 23 duplicate groups / 47 files. The
# full screen had 30 groups / 60 files, 11 of them at ADJACENT design points
# inside a trajectory, i.e. inside an elementary effect.
#
# Main.cc executes the macro *after* seeding, so `/random/setSeeds` in the macro
# overrides the clock() seed. Verified: with an explicit seed the hits content
# is byte-identical across runs (only the Event ID column shifts, which stage 2
# never reads), and a different seed gives a different stream.
#
# The seed is a function of (trajectory, replica, position) and deliberately NOT
# of the step within the trajectory. Every design point in a trajectory
# therefore shares a seed bank, so the two endpoints of every elementary effect
# are evaluated on the SAME random stream -- common random numbers, which is
# variance-reducing for the difference rather than merely tidy. Independent
# trajectories and replicas still get independent streams.
SEED_BASE = int(os.environ.get("SENSITIVITY_SEED_BASE", "20260728"))
EXPLICIT_SEEDS = os.environ.get("SENSITIVITY_EXPLICIT_SEEDS", "1") == "1"
# Selects an independent family of streams without touching SEED_BASE. 0 (the
# default) contributes nothing, so every existing run reproduces byte-identically.
# Stage 4 uses a fresh bank id for final validation, so a candidate that was
# selected on bank 0 is confirmed on streams it has never been evaluated on --
# otherwise the winner is partly a winner of its own noise realization.
SEED_BANK_ID = int(os.environ.get("SENSITIVITY_SEED_BANK_ID", "0"))


def sub_run_seed(design_point_index, replica, position_index, trajectory_length,
                 noise_floor=False, explicit_design=False):
    """Deterministic CLHEP seed for one sub-run (see SEED_BASE rationale).

    The grouping key differs by design type, and getting this wrong is silently
    destructive:

    * Morris mode keys by TRAJECTORY, so all 54 endpoints of a trajectory share
      a bank and every elementary effect is a paired comparison on one stream.
      That is the point of the construction.
    * Noise-floor mode keys by DESIGN POINT, because those N rows are N
      independent repeats of ONE configuration, not a path through parameter
      space. Applying the trajectory grouping there gave ceil(N/54) banks -- 4
      for N=200 -- so up to 54 identical configurations shared a seed AND a
      position set and were therefore byte-identical replicates. The effective
      sample size collapses from 200 to ~4 and any spread, standard error or
      bootstrap interval computed from the rows is spuriously precise. Since
      the whole purpose of that mode is to measure the noise, this would
      silently destroy the measurement rather than degrade it.
    * Explicit-design mode keys by NOTHING -- every design point shares one bank
      at matched (replica, position). An explicit design is a set of candidates
      to be compared against each other, not a path or a repeat, so the
      trajectory grouping is meaningless (there are no trajectories) and the
      noise-floor grouping is actively wrong (it would give each candidate an
      independent stream, discarding the pairing). Sharing the bank makes every
      candidate-vs-candidate comparison a paired one.

      Caveat, so nobody over-claims the benefit: two candidates with different
      material parameters consume different numbers of random draws, so their
      streams decorrelate after the first divergent event and stay decorrelated
      for the remaining ~125,000. The pairing is free and cannot hurt, but the
      variance reduction has NOT been measured here -- do not budget for it.
    """
    if explicit_design:
        group = 0
    elif noise_floor:
        group = design_point_index
    else:
        group = design_point_index // trajectory_length
    # Mixed with distinct odd multipliers so neighbouring (group, replica,
    # position) triples do not land on nearby seeds.
    raw = (SEED_BASE
           + SEED_BANK_ID * 7_919_003
           + group * 1_000_003
           + replica * 10_007
           + position_index * 101)
    return raw % 900_000_000 + 1  # keep well inside CLHEP's positive-int range


def assert_seed_bank_is_sound(n_design_points, noise_floor, explicit_design=False):
    """Fail before launching if the seed layout is not what the mode requires.

    Morris mode intends one bank per trajectory (shared within it); noise-floor
    mode intends a unique seed for every sub-run; explicit-design mode intends
    exactly one bank shared by every candidate. All three are checked here so a
    mis-keyed bank cannot reach a multi-hour campaign.
    """
    if not EXPLICIT_SEEDS:
        return
    seeds = [
        sub_run_seed(dp, r, p, TRAJECTORY_LENGTH, noise_floor=noise_floor,
                     explicit_design=explicit_design)
        for dp in range(n_design_points)
        for r in range(N_REPLICAS)
        for p in range(N_POSITIONS)
    ]
    unique = len(set(seeds))
    if explicit_design:
        # One bank shared across candidates: exactly N_REPLICAS x N_POSITIONS
        # distinct streams, however many design points there are. More than that
        # means the pairing broke; fewer means two positions or replicas of the
        # same candidate collided, which would silently correlate what are meant
        # to be independent samples.
        expected = N_REPLICAS * N_POSITIONS
        if unique != expected:
            raise ValueError(
                f"Explicit-design seed bank is wrong: {unique} unique seeds, expected "
                f"{expected} ({N_REPLICAS} replicas x {N_POSITIONS} positions, shared "
                f"across all {n_design_points} design points so comparisons are paired)."
            )
        print(f"Seed check: {unique} streams ({N_REPLICAS} replicas x {N_POSITIONS} positions) "
              f"shared across all {n_design_points} design points; comparisons are paired. "
              f"Bank id {SEED_BANK_ID}.")
    elif noise_floor:
        expected = n_design_points * N_REPLICAS * N_POSITIONS
        if unique != expected:
            raise ValueError(
                f"Noise-floor seed bank is degenerate: {unique} unique seeds for "
                f"{expected} sub-runs. Identical configurations would share a "
                f"stream, collapsing the effective sample size and making the "
                f"measured noise floor spuriously precise."
            )
        print(f"Seed check: {unique} unique seeds for {expected} sub-runs (all independent).")
    else:
        n_traj = max(1, -(-n_design_points // TRAJECTORY_LENGTH))
        expected = n_traj * N_REPLICAS * N_POSITIONS
        if unique != expected:
            raise ValueError(
                f"Morris seed bank is wrong: {unique} unique seeds, expected "
                f"{expected} (one bank per trajectory x replica x position)."
            )
        print(f"Seed check: {unique} seed banks over {n_traj} trajectories; "
              f"endpoints within a trajectory are paired.")
# Substrate is 10 x 10 mm centred on the origin; stay 1 mm clear of the walls
# so the sampled sites probe the device, not the edge boundary condition.
POSITION_HALF_SPAN_MM = float(os.environ.get("SENSITIVITY_POSITION_HALF_SPAN_MM", "4.0"))

# --- Memory guard (shared host) ----------------------------------------------
# mimir is shared. A healthy sample is ~0.07 GB RSS, so PER_SAMPLE_MEM_GB=4 is
# ~55x headroom over normal and still catches the KaplanQP runaway (measured at
# 28 -> 79 GB) long before it matters. TOTAL_MEM_GB is the hard ceiling across
# all concurrent samples.
TOTAL_MEM_GB = float(os.environ.get("SENSITIVITY_TOTAL_MEM_GB", "300"))
PER_SAMPLE_MEM_GB = float(os.environ.get("SENSITIVITY_PER_SAMPLE_MEM_GB", "4"))
MEM_POLL_SECONDS = float(os.environ.get("SENSITIVITY_MEM_POLL_SECONDS", "5"))

LOG_MODE = os.environ.get("SENSITIVITY_LOG_MODE", "full").strip().lower()
LOG_FRACTION = float(os.environ.get("SENSITIVITY_LOG_FRACTION", "0.01"))
LOG_SEED = os.environ.get("SENSITIVITY_LOG_SEED", "morris")
THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def debug_print(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)


def validate_logging_configuration():
    valid_modes = {"full", "failures", "none", "random"}
    if LOG_MODE not in valid_modes:
        raise ValueError(f"Unsupported SENSITIVITY_LOG_MODE: {LOG_MODE}. Expected one of: {valid_modes}")
    if not 0.0 <= LOG_FRACTION <= 1.0:
        raise ValueError(f"SENSITIVITY_LOG_FRACTION must be between 0 and 1. Got: {LOG_FRACTION}")


def get_sample_electrode_positions(macroname):
    """Electrode island X/Y locations (mm) used by this macro."""
    x_str = find_macro_value(macroname, "/main/electrode_param/setXLocations")
    y_str = find_macro_value(macroname, "/main/electrode_param/setYLocations")
    if x_str == "<missing>" or y_str == "<missing>":
        raise ValueError(
            "Macro " + macroname + " does not define "
            "/main/electrode_param/setXLocations and setYLocations; "
            "cannot determine electrode positions for QP binning."
        )
    qx = np.array([float(token.strip()) for token in x_str.split(",") if token.strip() != ""])
    qy = np.array([float(token.strip()) for token in y_str.split(",") if token.strip() != ""])
    if qx.shape != qy.shape or qx.size == 0:
        raise ValueError(
            "Macro " + macroname + " has mismatched or empty electrode "
            "X/Y location lists (" + x_str + ") vs (" + y_str + ")."
        )
    return qx, qy


def get_sample_top_gap(macroname):
    """setTopGap value (eV) used by this macro; Morris varies it per sample."""
    gap_str = find_macro_value(macroname, "/main/detector_param/setTopGap")
    if gap_str == "<missing>":
        raise ValueError(
            "Macro " + macroname + " does not define "
            "/main/detector_param/setTopGap; cannot compute QP creation energy."
        )
    return float(gap_str)


def get_sample_chip_z(macroname):
    """Top-surface Z [m] for selecting sensor-surface hits: chip is centered
    on Z=0, so top = +setSubThickness/2 (um, not swept by Morris)."""
    thickness_str = find_macro_value(macroname, "/main/detector_param/setSubThickness")
    if thickness_str == "<missing>":
        raise ValueError(
            "Macro " + macroname + " does not define "
            "/main/detector_param/setSubThickness; cannot determine the "
            "sensor surface Z position for QP binning."
        )
    thickness_um = float(thickness_str)
    return (thickness_um * 1e-6) / 2.0


def get_sample_electrode_size(macroname):
    """Electrode height/width [um] (Morris-swept), for normalizing QP
    generation rate per unit electrode volume in calculate_xQPs."""
    height_str = find_macro_value(macroname, "/main/electrode_param/setHeight")
    width_str = find_macro_value(macroname, "/main/electrode_param/setWidth")
    if height_str == "<missing>" or width_str == "<missing>":
        raise ValueError(
            "Macro " + macroname + " does not define "
            "/main/electrode_param/setHeight and setWidth."
        )
    return float(height_str), float(width_str)


def get_sample_top_thickness(macroname):
    """Top-layer thickness [um] (Morris-swept), the third electrode-volume
    dimension for calculate_xQPs."""
    thickness_str = find_macro_value(macroname, "/main/detector_param/setTopThickness")
    if thickness_str == "<missing>":
        raise ValueError(
            "Macro " + macroname + " does not define "
            "/main/detector_param/setTopThickness."
        )
    # Written with an explicit " um" suffix (unlike setSubThickness), e.g. "0.14 um".
    return float(thickness_str.split()[0])


def get_sample_beam_on(macroname):
    """/run/beamOn primary-event count for this macro (derived from the
    macro itself rather than a hardcoded N_sim)."""
    beam_on_str = find_macro_value(macroname, "/run/beamOn")
    if beam_on_str == "<missing>":
        raise ValueError("Macro " + macroname + " does not define /run/beamOn.")
    return float(beam_on_str)


def build_qp_manifest_entries(sample_name, sub_runs, qpde_params):
    """One manifest entry per (sample, replica).

    Positions are POOLED inside an entry -- the design point's response is the
    device-average over injection sites, so stage 2 concatenates that replica's
    position hits files and n_sim is the summed event count.

    Replicas are kept SEPARATE -- they are independent CLHEP realizations of
    the same physical configuration, so the spread across them is a direct
    empirical measurement of the Monte-Carlo noise floor. Averaging them here
    would destroy exactly the quantity the screening protocol needs in order to
    tell a real elementary effect from a noise realization.
    """
    representative_macro = sub_runs[0]["macro"]
    entries = []
    for replica in range(N_REPLICAS):
        replica_runs = [run for run in sub_runs if run["replica"] == replica]
        entry = build_qp_manifest_entry(sample_name, representative_macro, qpde_params)
        entry["sample_name"] = replica_runs[0]["sub_name"] if N_POSITIONS == 1 else (
            sample_name + ("_r" + str(replica) if N_REPLICAS > 1 else "")
        )
        entry["design_point"] = sample_name
        entry["replica"] = replica
        # Stored RELATIVE to RESULTS_DIR (e.g. "hits/M2_0_r0_p0_hitsfile.txt").
        # Absolute paths made a results directory non-portable: a copied or moved
        # run silently read the ORIGINAL run's hits files, so validating the copy
        # validated the original and moving a run to another host broke stage 2
        # outright. The macro still carries the absolute path -- Geant4 runs from
        # a different cwd -- so only the manifest changes. Stage 2 resolves
        # relative entries against the results dir it was pointed at and still
        # accepts absolute paths from legacy manifests.
        entry["hits_files"] = [os.path.relpath(run["hits_file"], RESULTS_DIR)
                               for run in replica_runs]
        entry["seeds"] = [run.get("seed") for run in replica_runs]
        entry.pop("hits_file", None)
        entry["n_sim"] = entry["n_sim_per_position"] * len(replica_runs)
        # Total across every replica of this design point -- the number the
        # screen is actually configured with, recorded so provenance survives.
        entry["n_sim_total_design_point"] = entry["n_sim_per_position"] * N_POSITIONS * N_REPLICAS
        entries.append(entry)
    return entries


def build_qp_manifest_entry(sample_name, macroname, qpde_params):
    """Everything stage2_compute_QPs.py needs for this sample, all derived
    from the macro/Morris row (not from the run's success), so it can be
    written at generation time, decoupled from the simulation stage."""
    qx, qy = get_sample_electrode_positions(macroname)
    gap = get_sample_top_gap(macroname)
    chip_z = get_sample_chip_z(macroname)
    height, width = get_sample_electrode_size(macroname)
    thickness = get_sample_top_thickness(macroname)
    n_sim = get_sample_beam_on(macroname)

    entry = {
        "sample_name": sample_name,
        "hits_file": os.path.relpath(os.path.join(HITS_DIR, sample_name + "_hitsfile.txt"), RESULTS_DIR),
        "gap": gap,
        "qx": qx.tolist(),
        "qy": qy.tolist(),
        "chip_z": chip_z,
        "height": height,
        "width": width,
        "thickness": thickness,
        "n_sim": n_sim,
        # per-position event count, before pooling positions
        "n_sim_per_position": n_sim,
    }
    entry.update(qpde_params)
    return entry


def write_qp_manifest_entry(entry):
    with open(QP_MANIFEST_FILE, "a") as manifest_file:
        manifest_file.write(json.dumps(entry) + "\n")


# Parallel-mode workers re-import this module (macOS spawn), so RUN_ID is
# propagated via SENSITIVITY_INTERNAL_RUN_ID to keep all workers on the same
# output/results dirs instead of each minting its own UUID.
_RUN_ID_PREFIX = "morris_debug_serial_" if DEBUG_MODE else _PRODUCTION_RUN_ID_PREFIX
RUN_ID = os.environ.get("SENSITIVITY_INTERNAL_RUN_ID", _RUN_ID_PREFIX + unique_id)
os.environ["SENSITIVITY_INTERNAL_RUN_ID"] = RUN_ID
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", RUN_ID)
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", RUN_ID)
HITS_DIR = os.path.join(RESULTS_DIR, "hits")
LOGS_DIR = os.path.join(RESULTS_DIR, "logs")
MACROS_DIR = os.path.join(OUTPUT_DIR, "macros")
CRYSTALMAPS_DIR = os.path.join(OUTPUT_DIR, "CrystalMaps")

# Read by stage2_compute_QPs.py to compute QPs/decoherence rate per sample,
# without needing the macro template, Geant4, or G4CMP.
QP_MANIFEST_FILE = os.path.join(RESULTS_DIR, "qp_manifest.jsonl")

validate_logging_configuration()

if MAX_WORKERS < 1:
    raise ValueError("SENSITIVITY_MAX_WORKERS must be at least 1")
if MAX_SAMPLES < 0:
    raise ValueError("SENSITIVITY_MAX_SAMPLES must be at least 0")
if PROGRESS_EVERY < 1:
    raise ValueError("SENSITIVITY_PROGRESS_EVERY must be at least 1")
if SAMPLE_TIMEOUT < 0:
    raise ValueError("SENSITIVITY_SAMPLE_TIMEOUT must be at least 0 (0 disables the timeout)")
if N_POSITIONS < 1:
    raise ValueError("SENSITIVITY_N_POSITIONS must be at least 1")
if N_REPLICAS < 1:
    raise ValueError("SENSITIVITY_N_REPLICAS must be at least 1")
if TOTAL_EVENTS < 0:
    raise ValueError("SENSITIVITY_TOTAL_EVENTS must be at least 0 (0 takes the total from the template)")
# Restored 2026-07-29. Commit 501a557 dropped these two checks while rewriting
# the event-accounting block around them. Without them a non-positive budget
# disables the guard silently (MemoryGuard compares RSS against a ceiling of 0
# or less, which nothing can stay under, or which nothing can exceed), and a
# per-sample cap above the aggregate cap is incoherent: the per-sample limit can
# never bind, so a single runaway sub-run is only caught by the aggregate ceiling
# it has already blown through. Both matter more for Stage 4 than for the screen,
# because the controller launches batches unattended.
if TOTAL_MEM_GB <= 0 or PER_SAMPLE_MEM_GB <= 0:
    raise ValueError(
        f"Memory budgets must be positive: SENSITIVITY_TOTAL_MEM_GB={TOTAL_MEM_GB:g}, "
        f"SENSITIVITY_PER_SAMPLE_MEM_GB={PER_SAMPLE_MEM_GB:g}"
    )
if PER_SAMPLE_MEM_GB > TOTAL_MEM_GB:
    raise ValueError(
        f"SENSITIVITY_PER_SAMPLE_MEM_GB ({PER_SAMPLE_MEM_GB:g} GB) cannot exceed "
        f"SENSITIVITY_TOTAL_MEM_GB ({TOTAL_MEM_GB:g} GB); the per-sample cap could never bind."
    )
_available_gb = host_available_gb()
if _available_gb == _available_gb and TOTAL_MEM_GB > _available_gb:
    raise ValueError(
        f"SENSITIVITY_TOTAL_MEM_GB={TOTAL_MEM_GB:g} exceeds host MemAvailable "
        f"({_available_gb:.0f} GB). This is a shared machine; lower the budget."
    )


# Morris trajectory length = (number of design variables) + 1. Defined here so
# the seed bank can be keyed by trajectory (see sub_run_seed).
TRAJECTORY_LENGTH = 1 + sum(
    len(p[1]) if type(p[1]) is list else 1
    for p in (electrode_params + detector_params + G4CMP_params + config_params + QPDE_params)
)


def resolve_event_counts():
    """(total per design point, per-sub-run /run/beamOn, provenance string).

    ONE number is configured -- the total primary phonons per design point --
    and the per-process count is derived. The previous scheme configured the
    per-sub-run count instead, so the same number meant N_POSITIONS x
    N_REPLICAS times more events depending on settings elsewhere; a
    wrong-but-valid count of that kind is undetectable downstream, because the
    run completes normally and only the physics is wrong.

    Splitting is exact by construction: a total that does not divide evenly into
    N_POSITIONS x N_REPLICAS sub-runs is refused rather than silently rounded,
    since rounding would make the design points carry unequal statistics.
    """
    sub_runs_per_point = N_POSITIONS * N_REPLICAS

    if TOTAL_EVENTS > 0:
        total, source = TOTAL_EVENTS, "SENSITIVITY_TOTAL_EVENTS"
    else:
        raw = find_macro_value(MACRO_TEMPLATE, "/run/beamOn")
        if raw == "<missing>":
            raise ValueError(f"Macro template defines no /run/beamOn: {MACRO_TEMPLATE}")
        try:
            total = int(float(raw.split()[0]))
        except (ValueError, IndexError):
            raise ValueError(
                f"Macro template's /run/beamOn is not a number: {raw!r} in "
                f"{MACRO_TEMPLATE}. Set SENSITIVITY_TOTAL_EVENTS explicitly."
            )
        source = f"template {os.path.basename(MACRO_TEMPLATE)} (read as the TOTAL)"

    if total <= 0:
        raise ValueError(f"Total events per design point must be positive, got {total}.")
    if total % sub_runs_per_point != 0:
        raise ValueError(
            f"Total events per design point ({total:,}) does not divide evenly into "
            f"{sub_runs_per_point} sub-runs ({N_POSITIONS} positions x {N_REPLICAS} "
            f"replicas); {total / sub_runs_per_point:.4f} events each.\n"
            f"Choose a total that is a multiple of {sub_runs_per_point} -- rounding "
            f"would give design points unequal statistics."
        )
    return total, total // sub_runs_per_point, source


def verify_generated_beam_on(macro_files, expected):
    """Assert every generated macro carries the intended event count.

    Cheap insurance against a substitution silently not applying -- a failure
    mode this project has already hit twice (an edit whose pattern did not
    match, reported as done). A wrong-but-valid event count cannot be detected
    downstream: the run completes normally and only the physics is wrong.
    """
    wrong = []
    for sub_run in macro_files:
        value = find_macro_value(sub_run["macro"], "/run/beamOn")
        if value == "<missing>" or int(float(value.split()[0])) != expected:
            wrong.append((sub_run["sub_name"], value))
    if wrong:
        sample = ", ".join(f"{n}={v}" for n, v in wrong[:5])
        raise ValueError(
            f"{len(wrong)} generated macro(s) do not carry /run/beamOn {expected}: {sample}"
        )
    print(f"Verified /run/beamOn {expected:,} in all {len(macro_files)} generated macros "
          f"({len(macro_files)} sub-runs x {expected:,} = {len(macro_files) * expected:,} events).")


DERIVED_THRESHOLD_CHECK = os.environ.get("SENSITIVITY_CHECK_DERIVED_THRESHOLDS", "1") == "1"


def verify_derived_thresholds(macro_files):
    """Assert setBotGapThres == setTopGap in every generated macro.

    setBotGapThres is not a free bottom-film property: G4CMPNormal ends the
    in-film cascade at setBotQPLim x setBotGapThres, while WaffleKaplanElectrode
    re-emits a secondary only above 2 x setTopGap. With setBotQPLim = 2 those
    coincide exactly iff setBotGapThres == setTopGap; otherwise there is a band
    in which quasiparticles are tracked but the phonons they emit are silently
    discarded. Nothing downstream can detect that -- the run completes normally
    and only the physics is wrong -- so it is checked at generation time.

    Explicit-design (Stage-4) mode only. The stored Morris screen deliberately
    swept the two independently, so applying this to the Morris path would fail
    a design that is historically correct. Disable with
    SENSITIVITY_CHECK_DERIVED_THRESHOLDS=0 to run a deliberate decoupling study
    (see the plan's GAPTHRES point set).
    """
    if not (DESIGN_FILE and DERIVED_THRESHOLD_CHECK):
        return
    mismatched = []
    for sub_run in macro_files:
        thres = find_macro_value(sub_run["macro"], "/main/detector_param/setBotGapThres ")
        topgap = find_macro_value(sub_run["macro"], "/main/detector_param/setTopGap ")
        try:
            ok = math.isclose(float(thres.split()[0]), float(topgap.split()[0]), rel_tol=1e-9)
        except (ValueError, IndexError, AttributeError):
            ok = False
        if not ok:
            mismatched.append((sub_run["sub_name"], thres, topgap))
    if mismatched:
        sample = ", ".join(f"{n}: thres={t} vs topGap={g}" for n, t, g in mismatched[:5])
        raise ValueError(
            f"{len(mismatched)} generated macro(s) have setBotGapThres != setTopGap: {sample}\n"
            f"setBotGapThres is derived from the junction gap (plan sec. 3.7). Fix the design "
            f"file, or set SENSITIVITY_CHECK_DERIVED_THRESHOLDS=0 for a deliberate study."
        )
    print(f"Verified setBotGapThres == setTopGap in all {len(macro_files)} generated macros.")


STAGE4_PROTOCOL_VERSION = "stage4-2026-07-29"


def _sha256(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def write_run_metadata(n_design_points, source_positions):
    """Machine-readable provenance for this run, written at generation time.

    A prose execution log in a docstring drifts from the artifacts it describes;
    this cannot, because it is emitted by the run itself. Everything needed to
    say what a stored result actually is -- fidelity, seeding, spatial set, and
    the exact bytes of the design, template and code that produced it -- lands in
    one JSON file next to the manifest.

    Hashes rather than paths, because paths are not portable and a file at the
    same path can differ. The design CSV's hash is the join key to the semantic
    labels its generator writes alongside it.
    """
    metadata = {
        "protocol_version": STAGE4_PROTOCOL_VERSION,
        "run_id": RUN_ID,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.node(),
        "design": {
            "source": ("explicit" if DESIGN_FILE else
                       "noise_floor" if NOISE_FLOOR_N > 0 else "morris"),
            "design_file": DESIGN_FILE or None,
            "design_file_sha256": _sha256(DESIGN_FILE) if DESIGN_FILE else None,
            "sample_prefix": SAMPLE_PREFIX,
            "n_design_points": n_design_points,
            "morris_seed": MORRIS_SEED,
            "max_samples": MAX_SAMPLES or None,
            "noise_floor_n": NOISE_FLOOR_N or None,
        },
        "fidelity": {
            "total_events_per_design_point": TOTAL_PER_POINT,
            "events_per_sub_run": EVENTS_PER_SUB_RUN,
            "events_source": EVENTS_SOURCE,
            "n_positions": N_POSITIONS,
            "n_replicas": N_REPLICAS,
            "n_sub_runs": n_design_points * N_POSITIONS * N_REPLICAS,
            # Authoritative expectations for stage 2's preflight. Without these
            # it can only check self-consistency, and a manifest that is missing
            # its last replica everywhere looks contiguous.
            "n_design_points": n_design_points,
            "n_manifest_entries": n_design_points * N_REPLICAS,
        },
        "manifest": {"hits_paths_relative": True},
        "seeding": {
            "explicit_seeds": EXPLICIT_SEEDS,
            "seed_base": SEED_BASE,
            "seed_bank_id": SEED_BANK_ID,
            "position_seed": POSITION_SEED,
            "position_half_span_mm": POSITION_HALF_SPAN_MM,
            # Recorded in full: the spatial quadrature is a fixed scenario, and
            # a nested P16/P32 comparison is only interpretable if the exact
            # sites are known rather than re-derived from a seed.
            "source_positions_mm": [list(p) if p else None for p in source_positions],
        },
        "inputs": {
            "macro_template": MACRO_TEMPLATE,
            "macro_template_sha256": _sha256(MACRO_TEMPLATE),
            "lattice_config_template": CONFIG_TEMPLATE,
            "lattice_config_template_sha256": _sha256(CONFIG_TEMPLATE),
            "main_executable": MAIN_EXE,
        },
        "code_sha256": {
            name: _sha256(os.path.join(SCRIPT_DIR, name))
            for name in ("stage1_run_simulations.py", "sensitivity_params.py",
                         "sensitivity_utils.py", "stage2_compute_QPs.py")
        },
    }
    path = os.path.join(RESULTS_DIR, "run_metadata.json")
    with open(path, "w") as handle:
        json.dump(metadata, handle, indent=2)
    print("Wrote run provenance to:", path)
    return path


def build_source_positions(template_z_mm):
    """Fixed scrambled-Sobol injection sites over the substrate, in mm.

    Returns [(x, y, z)] of length N_POSITIONS. z is held at the template's
    value (just inside the top surface); only the lateral site moves, since
    that is what selects which phonon caustic is launched.
    """
    if N_POSITIONS == 1:
        return [None]  # sentinel: leave the template's position untouched
    engine = qmc.Sobol(d=2, scramble=True, seed=POSITION_SEED)
    unit = engine.random(N_POSITIONS)
    span = POSITION_HALF_SPAN_MM
    return [
        (round(float(u[0]) * 2 * span - span, 6), round(float(u[1]) * 2 * span - span, 6), template_z_mm)
        for u in unit
    ]


def read_template_source_z():
    """z (mm) of /main/gun/setPosition in the template."""
    value = find_macro_value(MACRO_TEMPLATE, "/main/gun/setPosition")
    if value == "<missing>":
        raise ValueError("Macro template does not define /main/gun/setPosition: " + MACRO_TEMPLATE)
    tokens = value.split()
    return float(tokens[2])

# Define the set of parameters written into Geant4 macros.
macro_params = electrode_params + detector_params + G4CMP_params


def extract_qpde_params(samp, param_index):
    """f_01/r/s/I_ph/pt/n_cooper for this sample: sampled but never written
    to a macro/config file, only used for the post-hoc QP calculation."""
    return {name: float(samp[param_index[name]]) for name in QPDE_PARAM_NAMES}


# Step 2 of the screening protocol: replace the Morris design with N copies of
# the SAME default configuration. Nothing varies, so all spread in the outcome
# is Monte-Carlo noise by construction. That measured spread is the floor every
# significance threshold must be calibrated against -- without it there is no
# way to tell a real elementary effect from a noise realization, which is
# exactly how `pt` and `r` came to rank highly in the previous screen.
NOISE_FLOOR_N = int(os.environ.get("SENSITIVITY_NOISE_FLOOR_N", "0"))


def build_default_design(n_points):
    """n_points identical rows, each at every parameter's default value.

    Column order must match build_morris_design() exactly, since the rest of
    the pipeline indexes samples positionally.
    """
    all_params = electrode_params + detector_params + G4CMP_params + config_params + QPDE_params
    names = []
    defaults = []
    rescaled = []

    def pick(name, default_value, bounds):
        """Default if it is expressed in the sampler's units, else the midpoint.

        A few entries declare the default in ABSOLUTE units while their bounds
        carry the mantissa only, with the exponent living in the unit suffix --
        e.g. ("/g4cmp/clearance ", 1e-06, [1, 10], "e-6 mm"), and likewise
        `scat` (e-42 s3) and `decay` (e-56 s4). The macro writer appends the
        suffix to whatever value it is handed, so feeding it the declared
        default emits "/g4cmp/clearance 1e-06e-6 mm", which Geant4 rejects as
        an illegal parameter. That aborts the macro before /run/beamOn -- and
        Geant4 still exits 0, so the run looks like a success and silently
        simulates nothing.

        Sampled Morris designs never hit this because they draw from the
        bounds. Detect the inconsistency by testing whether the default lies
        inside its own bounds, and fall back to the midpoint when it does not.
        """
        low, high = float(min(bounds)), float(max(bounds))
        if low <= float(default_value) <= high:
            return float(default_value)
        midpoint = 0.5 * (low + high)
        rescaled.append((name, float(default_value), midpoint, (low, high)))
        return midpoint

    for param in all_params:
        if type(param[1]) is not list:
            names.append(design_name(param))
            defaults.append(pick(param[0], param[1], param[2]))
        else:
            for index, (default_value, bounds) in enumerate(zip(param[1], param[2]), start=1):
                names.append(param[0] + "_" + str(index))
                defaults.append(pick(param[0] + "_" + str(index), default_value, bounds))

    samples = np.tile(np.array(defaults, dtype=float), (n_points, 1))
    print(f"Noise-floor design: {n_points} identical points at default values "
          f"({len(names)} parameters, zero variation).")
    if rescaled:
        print("  Declared default outside its own bounds (unit-convention mismatch); "
              "using the bounds midpoint instead:")
        for name, declared, used, (low, high) in rescaled:
            print(f"    {name.strip():24s} declared={declared:g} bounds=[{low:g}, {high:g}] -> using {used:g}")
    return names, samples


def canonical_design_names():
    """The design column names, in the order the rest of the pipeline expects.

    Single source of truth for both the Morris design and an explicit one, so
    the two cannot drift into disagreeing about column order -- which is the one
    error an explicit design could make that still runs to completion.
    """
    all_params = electrode_params + detector_params + G4CMP_params + config_params + QPDE_params
    names = []
    for param in all_params:
        if type(param[1]) is not list:
            names.append(design_name(param))
        else:
            for index in range(1, len(param[1]) + 1):
                names.append(param[0] + "_" + str(index))
    return names


def build_explicit_design(path):
    """Read design points from a CSV instead of sampling a Morris design.

    The header must equal canonical_design_names() exactly, in order. This is
    checked rather than trusted because generate_runfiles() consumes the row
    POSITIONALLY: a file carrying the right columns in the wrong order writes
    every value into the wrong macro command, and the run then completes
    normally with only the physics wrong -- the same undetectable-downstream
    failure class the event-accounting commit was written to remove.

    Values are used as-is, including outside the Morris sampling bounds: an
    explicit design exists precisely to reach places the screen did not (e.g.
    setBotThickness at 10 um against a sampled range of [0.5, 1.5]). Bounds are
    a sampling instruction, not a physics limit. Non-finite values ARE refused,
    since they cannot describe a device.
    """
    expected = canonical_design_names()
    with open(path, newline="") as csvfile:
        reader = csv.reader(csvfile)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"Design file is empty: {path}")
        rows = [row for row in reader if any(cell.strip() for cell in row)]

    if header != expected:
        missing = [n for n in expected if n not in header]
        unknown = [n for n in header if n not in expected]
        detail = []
        if missing:
            detail.append(f"missing {len(missing)}: {missing[:5]}")
        if unknown:
            detail.append(f"unknown {len(unknown)}: {unknown[:5]}")
        if not detail:
            first = next((i for i, (a, b) in enumerate(zip(header, expected)) if a != b), None)
            detail.append(f"same columns, wrong ORDER (first difference at index {first}: "
                          f"{header[first]!r} where {expected[first]!r} was expected)")
        raise ValueError(
            f"Design file header does not match the canonical design columns.\n"
            f"  file: {path}\n"
            f"  expected {len(expected)} columns, got {len(header)}\n"
            f"  " + "; ".join(detail) + "\n"
            f"Generate a template with:\n"
            f"  python -c \"import stage1_run_simulations as s; "
            f"print(','.join(s.canonical_design_names()))\""
        )
    if not rows:
        raise ValueError(f"Design file has a valid header but no design points: {path}")

    samples = []
    for line_no, row in enumerate(rows, start=2):
        if len(row) != len(expected):
            raise ValueError(
                f"{path} line {line_no}: {len(row)} values for {len(expected)} columns."
            )
        try:
            values = [float(cell) for cell in row]
        except ValueError as exc:
            raise ValueError(f"{path} line {line_no}: non-numeric value ({exc}).")
        bad = [expected[i] for i, v in enumerate(values) if not np.isfinite(v)]
        if bad:
            raise ValueError(f"{path} line {line_no}: non-finite value(s) in {bad[:5]}.")
        samples.append(values)

    samples = np.array(samples, dtype=float)
    if MAX_SAMPLES > 0:
        samples = samples[:MAX_SAMPLES]
    print(f"Explicit design: {samples.shape[0]} design point(s) x {len(expected)} parameters "
          f"from {path}")
    return expected, samples


# A parameter whose unit field is "ratio:<other>" is swept as a dimensionless
# fraction of <other> rather than in its own units; generate_runfiles multiplies
# it back out when writing the file. This exists to make physically impossible
# corners of the design box unreachable by construction -- see the `vtrans`
# entry in sensitivity_params.py.
RATIO_PREFIX = "ratio:"


def is_ratio_param(param):
    return len(param) == 4 and isinstance(param[3], str) and param[3].startswith(RATIO_PREFIX)


def ratio_reference(param):
    """Name of the parameter this ratio is taken against."""
    return param[3][len(RATIO_PREFIX):]


def design_name(param):
    """Column name for the Morris design.

    A ratio parameter's design column holds the RATIO, not the absolute value,
    so it is named accordingly -- otherwise MorrisSequence.csv would appear to
    say the transverse sound speed is 0.6 m/s.
    """
    if is_ratio_param(param):
        return param[0].strip() + "/" + ratio_reference(param).strip() + " "
    return param[0]


def build_morris_design():
    all_params = electrode_params + detector_params + G4CMP_params + config_params + QPDE_params
    bounds = []
    names = []

    for param in all_params:
        debug_print(param[0])
        if type(param[1]) is not list:
            bounds.append(param[2])
            names.append(design_name(param))
        else:
            for index, parameter_bounds in enumerate(param[2], start=1):
                bounds.append(parameter_bounds)
                names.append(param[0] + "_" + str(index))

    setup = {
        "num_vars": len(bounds),
        "names": names,
        "bounds": bounds,
    }
    samples = np.round(morris_samp.sample(setup, 128, num_levels=4, seed=MORRIS_SEED), 6)
    if MAX_SAMPLES > 0:
        samples = samples[:MAX_SAMPLES]

    print("Number of parameter groups:", len(all_params))
    print("Morris sample shape:", samples.shape, len(samples[0]))
    return names, samples


def write_morris_sequence(names, samples):
    sequence_file = os.path.join(RESULTS_DIR, "MorrisSequence.csv")
    with open(sequence_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile, delimiter=",")
        writer.writerow(names)
        writer.writerows(samples)
    return sequence_file


def print_startup_configuration(total_samples):
    if DEBUG_MODE:
        print("Run mode: serial debug (SENSITIVITY_DEBUG_MODE=1)")
        print("Geant4/G4CMP stdout/stderr: live on screen and saved to log files")
    else:
        print("Run mode: parallel production")
    print("Platform:", sys.platform, "(macOS defaults)" if IS_MACOS else "(Linux defaults)")
    print("G4SYSTEM:", G4SYSTEM)
    print("Recommended Python:", os.path.join(CONDA_ENV, "bin", "python"))
    print("Geant4 environment script:", PATH_TO_GEANT4_ENV)
    print("G4CMP environment script:", PATH_TO_G4CMP_ENV)
    print("G4CMP CrystalMaps source:", G4CMP_SOURCE_CRYSTALMAPS)
    print("G4CMP binary directory:", G4CMPBIN)
    print("G4CMP library directory:", G4CMPLIB)
    print("Main executable:", MAIN_EXE)
    print("Run working directory:", RUN_WORKDIR)
    print("Using macro template:", MACRO_TEMPLATE)
    print("Writing generated macros/configs to:", OUTPUT_DIR)
    print("Writing sensitivity results to:", RESULTS_DIR)
    print("Writing QP manifest for stage 2 (stage2_compute_QPs.py) to:", QP_MANIFEST_FILE)
    print("Generate only:", GENERATE_ONLY)
    print("Total Morris samples:", total_samples)
    if MAX_SAMPLES > 0:
        print("Sample limit:", MAX_SAMPLES)
    print("Worker processes:", 1 if DEBUG_MODE else MAX_WORKERS)
    print("Source positions per design point:", N_POSITIONS,
          f"(scrambled Sobol, seed {POSITION_SEED}, +/-{POSITION_HALF_SPAN_MM:g} mm)" if N_POSITIONS > 1
          else "(template position, unchanged)")
    print("Replicas per design point:", N_REPLICAS)
    if DESIGN_FILE:
        print("Design source:", f"explicit design file {DESIGN_FILE}")
        print("Sample name prefix:", SAMPLE_PREFIX)
    else:
        print("Design source:", "noise-floor (identical defaults)" if NOISE_FLOOR_N > 0
              else "Morris sampler")
    if EXPLICIT_SEEDS:
        _keyed = ("(replica, position), shared across design points" if DESIGN_FILE
                  else "(design point, replica, position)" if NOISE_FLOOR_N > 0
                  else "(trajectory, replica, position)")
        print("CLHEP seeding:", f"explicit, base {SEED_BASE}, bank {SEED_BANK_ID}, keyed by {_keyed}")
    else:
        print("CLHEP seeding: <clock()-derived: NOT reproducible, streams are reused>")
    print(f"TOTAL phonons per design point: {TOTAL_PER_POINT:,} (from {EVENTS_SOURCE})")
    print(f"  -> /run/beamOn per sub-run: {EVENTS_PER_SUB_RUN:,} "
          f"({N_POSITIONS} positions x {N_REPLICAS} replicas = "
          f"{N_POSITIONS * N_REPLICAS} sub-runs per design point)")
    print("Total sub-runs:", total_samples * N_REPLICAS * N_POSITIONS)
    print("Memory budget: total", f"{TOTAL_MEM_GB:g} GB,",
          "per-sample", f"{PER_SAMPLE_MEM_GB:g} GB;",
          "host MemAvailable", f"{host_available_gb():.0f} GB")
    print("Morris seed:", MORRIS_SEED if MORRIS_SEED is not None else "<unset: design is not reproducible>")
    print("Per-sample timeout:", f"{SAMPLE_TIMEOUT:g}s" if SAMPLE_TIMEOUT > 0 else "<disabled>")
    print("Progress update interval:", PROGRESS_EVERY)
    print("Log mode:", LOG_MODE)
    if LOG_MODE == "random":
        print("Log fraction:", LOG_FRACTION)
        print("Log seed:", LOG_SEED)
    print("Thread environment variables:")
    for env_name in THREAD_ENV_VARS:
        print(f"  {env_name}={os.environ.get(env_name, '<unset>')}")


def generate_runfiles(samp, samp_No):
    # Write a new macro with the sample parameters
    with open(MACRO_TEMPLATE, 'r') as file:
        lines = file.readlines()
    file.close()

    sample_name = SAMPLE_PREFIX + str(samp_No)
    # /g4cmp/HitsFile is NOT written here: it differs per sub-run and is
    # substituted at the very end, once per (replica, position).
    lines = replace_line(
        lines,
        "/main/detector_param/setSubstrateG4Name ",
        "/main/detector_param/setSubstrateG4Name G4_Si",
        allow_commented=True,
        required=True,
    )
    lines = replace_line(
        lines,
        "/main/detector_param/setSubstrateName ",
        "/main/detector_param/setSubstrateName " + LATTICE_MATERIAL,
        allow_commented=True,
        required=True,
    )
    # Pinned (non-swept) G4CMP transport limits. Written in place so the value is
    # guaranteed regardless of the template, and macro line order is preserved.
    for command_prefix, pinned_line in PINNED_G4CMP_COMMANDS:
        lines = replace_line(lines, command_prefix, pinned_line, allow_commented=True, required=True)

    k = 0
    for param in macro_params:
        if type(param[1]) is not list:
            if len(param) == 3:
                command_string = param[0] + str(samp[k])
                debug_print(command_string)
                k += 1
            elif len(param) == 4:
                if param[3] == "int":
                    command_string = param[0] + str(int(np.round(samp[k])))
                    debug_print(command_string)
                    k += 1
                else:
                    command_string = param[0] + str(samp[k]) + param[3]
                    debug_print(command_string)
                    k += 1
            else:
                print("The parameter " + param[0] + "does not conform to the expected format.")

            lines = replace_line(
                lines,
                param[0],
                command_string,
                allow_commented=True,
                required=True,
            )
        else:
            if len(param) == 3:
                vec = ""
                for p in range(len(param[1])):
                    vec = vec + str(samp[k]) + ","
                    k += 1
                command_string = param[0] + vec[:-1]
                debug_print(command_string)

            elif len(param) == 4:
                if param[3] == "int":
                    vec = ""
                    for p in range(len(param[1])):
                        vec = vec + str(int(np.round(samp[k]))) + " "
                        k += 1
                        command_string = param[0] + vec[:-1]
                        debug_print(command_string)
                else:
                    vec = ""
                    for p in range(len(param[1])):
                        vec = vec + str(samp[k]) + ","
                        k += 1
                    command_string = param[0] + vec[:-1] + param[3]
                    debug_print(command_string)

            else:
                print("The parameter " + param[0] + "does not conform to the expected format.")

            lines = replace_line(
                lines,
                param[0],
                command_string,
                allow_commented=True,
                required=True,
            )

    # Emit one macro per (replica, position). All share this sample's swept
    # parameter values and its single lattice config; they differ only in the
    # source position, the hits file, and the CLHEP realization they will draw
    # at run time.
    sub_runs = []
    for replica in range(N_REPLICAS):
        for position_index, position in enumerate(SOURCE_POSITIONS):
            sub_name = sample_name
            if N_REPLICAS > 1:
                sub_name += "_r" + str(replica)
            if N_POSITIONS > 1:
                sub_name += "_p" + str(position_index)
            sub_lines = list(lines)
            sub_hits = os.path.join(HITS_DIR, sub_name + "_hitsfile.txt")
            sub_lines = replace_line(
                sub_lines, "/g4cmp/HitsFile", "/g4cmp/HitsFile " + sub_hits, required=True
            )
            if position is not None:
                sub_lines = replace_line(
                    sub_lines,
                    "/main/gun/setPosition ",
                    "/main/gun/setPosition {0} {1} {2} mm".format(*position),
                    required=True,
                )
            # The seed must sit immediately before /run/beamOn: anything between
            # them (notably /main/detector_param/update) consumes draws and
            # shifts the stream relative to the event loop.
            seed = None
            beam_on_line = "/run/beamOn " + str(EVENTS_PER_SUB_RUN)
            if EXPLICIT_SEEDS:
                seed = sub_run_seed(samp_No, replica, position_index, TRAJECTORY_LENGTH,
                                    noise_floor=NOISE_FLOOR_N > 0,
                                    explicit_design=bool(DESIGN_FILE))
                beam_on_line = f"/random/setSeeds {seed} {seed + 1}\n{beam_on_line}"
            sub_lines = replace_line(sub_lines, "/run/beamOn ", beam_on_line, required=True)

            sub_macro = os.path.join(MACROS_DIR, sub_name + ".mac")
            with open(sub_macro, "w") as file:
                file.writelines(sub_lines)
            sub_runs.append({"sub_name": sub_name, "macro": sub_macro, "hits_file": sub_hits,
                             "replica": replica, "position_index": position_index, "seed": seed})

    # Modify the config.txt file for Si with the new parameters
    with open(CONFIG_TEMPLATE, 'r') as file:
        lines = file.readlines()
    file.close()

    # Absolute values of scalar config entries already written, so a ratio
    # parameter can be resolved against the one it references. config_params
    # lists `vsound` before `vtrans`, so the reference is always resolved first;
    # a forward reference raises rather than silently writing a wrong crystal.
    config_values = {}

    for param in config_params:
        if type(param[1]) is not list:
            if len(param) == 3:
                command_string = format_config_entry(param[0], samp[k])
                config_values[param[0]] = samp[k]
                debug_print(command_string)
                k += 1
            elif len(param) == 4:
                if param[3] == "int":
                    command_string = format_config_entry(param[0], int(np.round(samp[k])))
                    config_values[param[0]] = int(np.round(samp[k]))
                    debug_print(command_string)
                    k += 1
                elif is_ratio_param(param):
                    reference = ratio_reference(param)
                    if reference not in config_values:
                        raise ValueError(
                            f"Ratio parameter {param[0].strip()!r} references "
                            f"{reference.strip()!r}, which has not been written yet. "
                            f"Order config_params so the reference comes first."
                        )
                    absolute = samp[k] * config_values[reference]
                    # Unit suffix is taken from the referenced parameter, since the
                    # reconstructed value carries that parameter's units.
                    reference_param = next(p for p in config_params if p[0] == reference)
                    unit = reference_param[3] if len(reference_param) == 4 else None
                    command_string = (format_config_entry(param[0], absolute, unit) if unit
                                      else format_config_entry(param[0], absolute))
                    config_values[param[0]] = absolute
                    debug_print(command_string)
                    k += 1
                else:
                    command_string = format_config_entry(param[0], samp[k], param[3])
                    config_values[param[0]] = samp[k]
                    debug_print(command_string)
                    k += 1
            else:
                print("The parameter " + param[0] + " does not conform to the expected format.")
        else:
            if len(param) == 3:
                vec = ""
                for p in range(len(param[1])):
                    vec = vec + str(samp[k]) + " "
                    k += 1
                command_string = format_config_entry(param[0], vec[:-1])
                debug_print(command_string)

            elif len(param) == 4:
                if param[3] == "int":
                    vec = ""
                    for p in range(len(param[1])):
                        vec = vec + str(int(np.round(samp[k]))) + " "
                        k += 1
                    command_string = format_config_entry(param[0], vec[:-1])
                    debug_print(command_string)
                else:
                    vec = ""
                    for p in range(len(param[1])):
                        vec = vec + str(samp[k]) + " "
                        k += 1
                    command_string = format_config_entry(param[0], vec[:-1], param[3])
                    debug_print(command_string)

            else:
                print("The parameter " + param[0] + " does not conform to the expected format.")

        lines = replace_line(lines, param[0], command_string, append_if_missing=True)

    # Write config.txt here
    output_config_filename = os.path.join(CRYSTALMAPS_DIR, sample_name, LATTICE_MATERIAL, "config.txt")
    output_config_dir = os.path.dirname(output_config_filename)
    os.makedirs(output_config_dir, exist_ok=True)
    with open(output_config_filename, 'w') as file:
        file.writelines(lines)
    file.close()

    return sample_name, sub_runs


def _prepend_path_fragment(var_name, directory):
    """Shell snippet prepending `directory` to a colon-separated PATH-like
    variable, but only if it isn't already there."""
    return (
        "case \":${" + var_name + ":-}:\" in *\":"
        + directory
        + ":\"*) ;; *) export " + var_name + "="
        + shlex.quote(directory)
        + "${" + var_name + ":+:$" + var_name + "} ;; esac; "
    )


def build_run_command(macroname, lattice_name=None):
    """Shell command for one sub-run.

    lattice_name is the *sample* name, which is NOT the macro basename once
    replicas/positions are in play (macro `Morris_7_r1_p3.mac` belongs to
    sample `Morris_7`). The lattice config is written once per sample, so
    G4LATTICEDATA must point at the sample directory, not the sub-run.
    """
    if lattice_name is None:
        lattice_name = os.path.splitext(os.path.basename(macroname))[0]
    sample_name = lattice_name
    # The dynamic-loader search path variable is DYLD_LIBRARY_PATH on macOS
    # and LD_LIBRARY_PATH on Linux. LD_LIBRARY_PATH is set on both: macOS
    # ignores it for loading, but G4CMP's own scripts read it.
    library_path_vars = ["DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"] if IS_MACOS else ["LD_LIBRARY_PATH"]
    return (
        "set -e; "
        "set +u; "
        + "source " + shlex.quote(PATH_TO_GEANT4_ENV) + "; "
        + "unset G4CMPINSTALL G4CMPINCLUDE G4LATTICEDATA G4CMPLIB; "
        + "source " + shlex.quote(PATH_TO_G4CMP_ENV) + "; "
        + "set -u; "
        + "export G4LATTICEDATA=" + shlex.quote(os.path.join(CRYSTALMAPS_DIR, sample_name)) + "; "
        + _prepend_path_fragment("PATH", G4CMPBIN)
        + "".join(_prepend_path_fragment(var, G4CMPLIB) for var in library_path_vars)
        + "cd " + shlex.quote(RUN_WORKDIR) + "; "
        + shlex.quote(MAIN_EXE) + " " + shlex.quote(macroname)
    )


class SampleTimeout(Exception):
    """Raised when a sample exceeds SENSITIVITY_SAMPLE_TIMEOUT seconds."""


class MemoryKilled(Exception):
    """Raised when a sub-run was SIGKILLed, i.e. the memory guard fired."""


class MacroAborted(Exception):
    """Raised when a sub-run exited 0 without ever reaching /run/beamOn."""


def _kill_process_group(process):
    """Kill the sample's whole process group. Killing only the bash wrapper
    leaves `Main` orphaned and spinning (see G4CMP_crash_and_memory_analysis.md)."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _start_timeout_watchdog(process):
    """Arm a timer that kills `process`'s group once SENSITIVITY_SAMPLE_TIMEOUT
    elapses. Returns None when the timeout is disabled (the default), so the
    no-timeout path behaves exactly as it did before."""
    if SAMPLE_TIMEOUT <= 0:
        return None

    def on_timeout():
        # The process may have exited in the race between wait() returning and
        # cancel(); killing then could hit a recycled process group.
        if process.poll() is not None:
            return
        timer.fired = True
        _kill_process_group(process)

    timer = threading.Timer(SAMPLE_TIMEOUT, on_timeout)
    timer.fired = False
    timer.daemon = True
    timer.start()
    return timer


def _stop_timeout_watchdog(watchdog):
    if watchdog is not None:
        watchdog.cancel()


def run_sample_debug(sub_run, run_index, total_runs):
    """Serial: streams Geant4/G4CMP stdout live plus verbose diagnostics.
    Only used in DEBUG_MODE, where execution is guaranteed single-process."""
    macroname = sub_run["macro"]
    sample_name = sub_run["sub_name"]
    lattice_root = os.path.join(CRYSTALMAPS_DIR, sub_run["sample_name"])
    config_file = os.path.join(lattice_root, LATTICE_MATERIAL, "config.txt")
    hits_file = sub_run["hits_file"]
    command = build_run_command(macroname, lattice_name=sub_run["sample_name"])

    log_file = os.path.join(LOGS_DIR, sample_name + ".log")
    start_time = time.monotonic()
    keep_success_log = should_keep_success_log(sample_name, LOG_MODE, LOG_FRACTION, LOG_SEED)

    print("")
    print(f"[{run_index}/{total_runs}] Starting {sample_name}")
    print(f"[{run_index}/{total_runs}] Macro: {macroname}")
    print(f"[{run_index}/{total_runs}] Log:   {log_file}")
    print(f"[{run_index}/{total_runs}] G4LATTICEDATA: {lattice_root}")
    print(f"[{run_index}/{total_runs}] Expected config: {config_file}")
    print(f"[{run_index}/{total_runs}] Expected hits:   {hits_file}")
    print(f"[{run_index}/{total_runs}] Macro HitsFile:  {find_macro_value(macroname, '/g4cmp/HitsFile')}")
    print(f"[{run_index}/{total_runs}] Macro beamOn:    {find_macro_value(macroname, '/run/beamOn')}")
    print(f"[{run_index}/{total_runs}] Geant4 env:      {PATH_TO_GEANT4_ENV}")
    print(f"[{run_index}/{total_runs}] G4CMP env:       {PATH_TO_G4CMP_ENV}")
    print(f"[{run_index}/{total_runs}] Command: {MAIN_EXE} {macroname}")

    if not os.path.exists(config_file):
        raise FileNotFoundError("Missing generated lattice config: " + config_file)

    temp_log = tempfile.NamedTemporaryFile(
        mode="w",
        dir=LOGS_DIR,
        prefix=sample_name + ".",
        suffix=".logtmp",
        delete=False,
    )
    temp_log_file = temp_log.name
    try:
        with temp_log:
            process = subprocess.Popen(
                ["bash", "-lc", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            # A hung sample emits no further output, so the read loop below
            # would block forever; a watchdog thread does the killing, which
            # closes the pipe and unblocks the loop.
            watchdog = _start_timeout_watchdog(process)
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    print(line, end="")
                    temp_log.write(line)
                return_code = process.wait()
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                _stop_timeout_watchdog(watchdog)
    except Exception:
        finalize_log_file(temp_log_file, log_file, True)
        raise

    if watchdog is not None and watchdog.fired:
        finalize_log_file(temp_log_file, log_file, True)
        raise SampleTimeout(
            f"{sample_name} exceeded SENSITIVITY_SAMPLE_TIMEOUT={SAMPLE_TIMEOUT:g}s and was killed"
        )

    elapsed = time.monotonic() - start_time
    if return_code != 0:
        finalize_log_file(temp_log_file, log_file, True)
        raise subprocess.CalledProcessError(return_code, ["bash", "-lc", command])

    finalize_log_file(temp_log_file, log_file, keep_success_log)
    print(f"[{run_index}/{total_runs}] Finished {sample_name} in {format_duration(elapsed)}")
    if os.path.exists(hits_file):
        print(f"[{run_index}/{total_runs}] Hits file written: {hits_file}")
    else:
        print(f"[{run_index}/{total_runs}] WARNING: hits file was not written: {hits_file}")
    return sample_name


def run_sample_parallel(sub_run, guard=None):
    """Run one sub-run: captures Geant4/G4CMP output straight to the log file.

    Runs on a pool *thread*, so the pid it spawns is visible to the parent's
    MemoryGuard; the sub-run is registered for the whole life of the process
    and unregistered in a finally, so a crash cannot leak a tracked pid.
    """
    macroname = sub_run["macro"]
    sub_name = sub_run["sub_name"]
    command = build_run_command(macroname, lattice_name=sub_run["sample_name"])

    log_file = os.path.join(LOGS_DIR, sub_name + ".log")
    temp_log = tempfile.NamedTemporaryFile(
        mode="w",
        dir=LOGS_DIR,
        prefix=sub_name + ".",
        suffix=".logtmp",
        delete=False,
    )
    temp_log_file = temp_log.name
    keep_success_log = should_keep_success_log(sub_name, LOG_MODE, LOG_FRACTION, LOG_SEED)

    try:
        with temp_log:
            process = subprocess.Popen(
                ["bash", "-lc", command],
                stdout=temp_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if guard is not None:
                guard.register(process.pid, sub_name)
            watchdog = _start_timeout_watchdog(process)
            try:
                return_code = process.wait()
            finally:
                _stop_timeout_watchdog(watchdog)
                if guard is not None:
                    guard.unregister(process.pid)
    except Exception:
        finalize_log_file(temp_log_file, log_file, True)
        raise

    if watchdog is not None and watchdog.fired:
        finalize_log_file(temp_log_file, log_file, True)
        raise SampleTimeout(
            f"{sub_name} exceeded SENSITIVITY_SAMPLE_TIMEOUT={SAMPLE_TIMEOUT:g}s and was killed"
        )
    if return_code != 0:
        finalize_log_file(temp_log_file, log_file, True)
        # SIGKILL (-9) is what the memory guard uses, so report it distinctly:
        # a memory kill is a budget event, not a physics failure.
        if return_code == -signal.SIGKILL:
            raise MemoryKilled(f"{sub_name} was SIGKILLed (memory guard or external kill)")
        raise subprocess.CalledProcessError(return_code, ["bash", "-lc", command])

    # Exit 0 is NOT sufficient evidence that the sample ran. A malformed macro
    # command (e.g. a bad unit suffix) makes Geant4 abort the macro with only a
    # G4Exception *warning*, skip /run/beamOn entirely, and still exit 0. The
    # batch then records thousands of "successes" that simulated nothing, and
    # the emptiness only surfaces in stage 2 as universally zero signal --
    # indistinguishable, at a glance, from a genuinely weak physical response.
    # The hits file is created at /run/beamOn (header row written even when the
    # run produces no hits), so its absence is a reliable, cheap proof that the
    # event loop never executed.
    if not os.path.exists(sub_run["hits_file"]):
        finalize_log_file(temp_log_file, log_file, True)
        raise MacroAborted(
            f"{sub_name} exited 0 but wrote no hits file: the macro was aborted before "
            f"/run/beamOn (check the log for 'Illegal parameter' / 'Command aborted')"
        )

    finalize_log_file(temp_log_file, log_file, keep_success_log)
    return sub_name


def validate_runtime_paths():
    required_files = {
        "Geant4 environment script": PATH_TO_GEANT4_ENV,
        "G4CMP environment script": PATH_TO_G4CMP_ENV,
        "G4CMP Si config template": CONFIG_TEMPLATE,
        "macro template": MACRO_TEMPLATE,
    }
    if not GENERATE_ONLY:
        required_files["Main executable"] = MAIN_EXE

    missing = [f"{label}: {path}" for label, path in required_files.items() if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError("Missing required files:\n  " + "\n  ".join(missing))

    if not GENERATE_ONLY:
        required_dirs = {
            "G4CMP library directory": G4CMPLIB,
            "G4CMP binary directory": G4CMPBIN,
        }
        missing_dirs = [f"{label}: {path}" for label, path in required_dirs.items() if not os.path.isdir(path)]
        if missing_dirs:
            raise FileNotFoundError("Missing required directories:\n  " + "\n  ".join(missing_dirs))

    if not os.path.isdir(RUN_WORKDIR):
        raise FileNotFoundError("Missing run working directory: " + RUN_WORKDIR)


def main(macro_files):
    start_time = time.monotonic()
    total_runs = len(macro_files)
    # A single sample's crash must not abort the rest of the batch; failures
    # are caught, summarized at the end, and cause a non-zero exit.
    failures = []

    if DEBUG_MODE:
        print(f"Starting {total_runs} sub-runs in serial debug mode.")
        for run_index, sub_run in enumerate(macro_files, start=1):
            sample_name = sub_run["sub_name"]
            try:
                run_sample_debug(sub_run, run_index, total_runs)
            except Exception as exc:
                failures.append((sample_name, exc))
                print(f"[{run_index}/{total_runs}] FAILED {sample_name}: {exc}")
            if run_index <= 5 or run_index % PROGRESS_EVERY == 0 or run_index == total_runs:
                elapsed = time.monotonic() - start_time
                rate = run_index / elapsed if elapsed > 0 else 0.0
                remaining = total_runs - run_index
                eta_seconds = remaining / rate if rate > 0 else 0.0
                print(
                    f"Completed {run_index}/{total_runs} runs; "
                    f"elapsed={format_duration(elapsed)}; "
                    f"eta={format_duration(eta_seconds)}"
                )
    else:
        print(f"Starting {total_runs} sub-runs with {MAX_WORKERS} workers.")
        print(
            f"Memory guard: total {TOTAL_MEM_GB:g} GB, per-sample {PER_SAMPLE_MEM_GB:g} GB, "
            f"polling every {MEM_POLL_SECONDS:g}s (host MemAvailable {host_available_gb():.0f} GB)."
        )
        completed = 0
        guard = MemoryGuard(
            total_gb=TOTAL_MEM_GB,
            per_sample_gb=PER_SAMPLE_MEM_GB,
            poll_seconds=MEM_POLL_SECONDS,
        ).start()
        try:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                run_iter = iter(macro_files)
                in_flight = {}  # future -> sub_run, so a failed future can still be identified

                for _ in range(min(MAX_WORKERS, total_runs)):
                    try:
                        sub_run = next(run_iter)
                    except StopIteration:
                        break
                    in_flight[executor.submit(run_sample_parallel, sub_run, guard)] = sub_run

                while in_flight:
                    future = next(as_completed(in_flight))
                    sub_run = in_flight.pop(future)
                    completed += 1
                    sub_name = sub_run["sub_name"]

                    try:
                        sub_name = future.result()
                    except Exception as exc:
                        failures.append((sub_name, exc))
                        print(f"[{completed}/{total_runs}] FAILED {sub_name}: {exc}")

                    try:
                        next_sub_run = next(run_iter)
                    except StopIteration:
                        pass
                    else:
                        in_flight[executor.submit(run_sample_parallel, next_sub_run, guard)] = next_sub_run

                    if completed <= 5 or completed % PROGRESS_EVERY == 0 or completed == total_runs:
                        elapsed = time.monotonic() - start_time
                        rate = completed / elapsed if elapsed > 0 else 0.0
                        remaining = total_runs - completed
                        eta_seconds = remaining / rate if rate > 0 else 0.0
                        print(
                            f"Completed {completed}/{total_runs} sub-runs; "
                            f"latest={sub_name}; "
                            f"peakRSS={guard.peak_total_bytes / 1024 ** 3:.2f}GB; "
                            f"elapsed={format_duration(elapsed)}; "
                            f"eta={format_duration(eta_seconds)}"
                        )
        finally:
            guard.stop()
            print(f"Peak tracked RSS across all concurrent sub-runs: "
                  f"{guard.peak_total_bytes / 1024 ** 3:.2f} GB (budget {TOTAL_MEM_GB:g} GB)")
            if guard.killed:
                print(f"Memory guard killed {len(guard.killed)} sub-run(s).")

    if failures:
        print("")
        print(f"WARNING: {len(failures)}/{total_runs} samples failed:")
        for sample_name, exc in failures:
            print(f"  {sample_name}: {exc}")

    print("")
    print("Simulation stage complete. To compute quasiparticles/decoherence")
    print("rate from the hits files just generated, run:")
    print(f"    python stage2_compute_QPs.py --results-dir {RESULTS_DIR}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    validate_runtime_paths()
    for path in (HITS_DIR, LOGS_DIR, MACROS_DIR, CRYSTALMAPS_DIR):
        os.makedirs(path, exist_ok=False)

    TOTAL_PER_POINT, EVENTS_PER_SUB_RUN, EVENTS_SOURCE = resolve_event_counts()
    SOURCE_POSITIONS = build_source_positions(read_template_source_z())

    if DESIGN_FILE and NOISE_FLOOR_N > 0:
        raise ValueError(
            "SENSITIVITY_DESIGN_FILE and SENSITIVITY_NOISE_FLOOR_N are mutually "
            "exclusive: the first supplies the design, the second replaces it."
        )
    if DESIGN_FILE:
        names, samples = build_explicit_design(DESIGN_FILE)
    elif NOISE_FLOOR_N > 0:
        names, samples = build_default_design(NOISE_FLOOR_N)
    else:
        names, samples = build_morris_design()
    # Every path must agree on column order, since generate_runfiles indexes the
    # row positionally. Checked here rather than trusted so a future edit to one
    # builder cannot silently misalign the others.
    _canonical = canonical_design_names()
    if list(names) != _canonical:
        raise ValueError(
            f"Design column order disagrees with canonical_design_names() "
            f"({len(names)} vs {len(_canonical)} columns). The macro writer indexes "
            f"rows positionally, so this would write values into the wrong commands."
        )
    total_samples = samples.shape[0]
    assert_seed_bank_is_sound(total_samples, noise_floor=NOISE_FLOOR_N > 0,
                              explicit_design=bool(DESIGN_FILE))
    sequence_file = write_morris_sequence(names, samples)
    print("Wrote Morris sample sequence (all sampled parameter values) to:", sequence_file)
    print_startup_configuration(total_samples)

    param_index = {name: idx for idx, name in enumerate(names)}

    macro_files = []
    generation_start = time.monotonic()
    print(
        f"Generating {total_samples} {'explicit' if DESIGN_FILE else 'Morris'} design points "
        f"x {N_REPLICAS} replica(s) x {N_POSITIONS} position(s) "
        f"= {total_samples * N_REPLICAS * N_POSITIONS} sub-runs."
    )
    for s, samp in enumerate(samples):
        sample_name, sub_runs = generate_runfiles(samp, s)
        for sub_run in sub_runs:
            sub_run["sample_name"] = sample_name
        macro_files.extend(sub_runs)
        for entry in build_qp_manifest_entries(sample_name, sub_runs, extract_qpde_params(samp, param_index)):
            write_qp_manifest_entry(entry)
        written = s + 1
        if written <= 5 or written % PROGRESS_EVERY == 0 or written == total_samples:
            elapsed = time.monotonic() - generation_start
            rate = written / elapsed if elapsed > 0 else 0.0
            remaining = total_samples - written
            eta_seconds = remaining / rate if rate > 0 else 0.0
            print(
                f"Wrote {written}/{total_samples} macros/configs; "
                f"latest={sample_name}.mac; "
                f"elapsed={format_duration(elapsed)}; "
                f"eta={format_duration(eta_seconds)}"
            )

    verify_generated_beam_on(macro_files, EVENTS_PER_SUB_RUN)
    verify_derived_thresholds(macro_files)
    write_run_metadata(total_samples, SOURCE_POSITIONS)

    if GENERATE_ONLY:
        print("Generation-only mode complete; Geant4/G4CMP simulations were not started.")
        print("Generated run files:", OUTPUT_DIR)
    else:
        main(macro_files)
