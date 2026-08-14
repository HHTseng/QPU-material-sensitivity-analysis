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
import os
import sys
import csv
import json
import shlex
import signal
import subprocess
import tempfile
import threading
from functools import lru_cache
# Threads, not processes: each worker only spawns `Main` and blocks in wait(),
# so there is no GIL contention, and the child pid stays visible to the parent's
# MemoryGuard. With a process pool the guard could not see what to kill.
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import uuid
from SALib.sample import morris as morris_samp
from SALib.analyze import morris
unique_id = str(uuid.uuid4())

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from sensitivity_params import (
    electrode_params,
    detector_params,
    G4CMP_params,
    config_params,
    FIXED_MACRO_COMMANDS,
    FIXED_CONFIG_COMMANDS,
    SUBSTRATE_DENSITY_KG_M3,
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
    # mimir has 192 cores and a healthy sub-run is ~0.07 GB RSS, so 64 workers
    # is ~1/3 of the box and ~4.5 GB of steady memory. The historical default of
    # 4 was a hangover from the QPLim=1 runaway, which QPLim=3 plus
    # SENSITIVITY_SAMPLE_TIMEOUT and sensitivity_memguard.py now cover.
    # NOTE: 32 is the level validated end-to-end (221,184 sub-runs, 0 failures,
    # 2.15 GB peak). 64 is double that and is not yet measured at scale -- watch
    # the "Peak tracked RSS" line on the first long run.
    _DEFAULT_MAX_WORKERS = "64"
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
# Per-sample wall-clock limit in seconds; 0 (default) disables it. Guards
# against the G4CMPKaplanQP infinite loop documented in
# G4CMP_crash_and_memory_analysis.md (a film with QPLim=1 spins forever and
# can grow to tens of GB of RSS). The kill targets the whole process group,
# because killing the bash wrapper alone leaves an orphaned `Main` at 100% CPU.
# 0 disables the watchdog. Leaving it disabled is unsafe for a long campaign:
# the memory guard only catches runaways that GROW, so a hang with flat RSS
# holds a worker slot forever and the batch silently loses throughput. If unset,
# a bound is DERIVED from the event count once that is known (see
# resolve_sample_timeout) rather than defaulting to "off".
_SAMPLE_TIMEOUT_RAW = os.environ.get("SENSITIVITY_SAMPLE_TIMEOUT", "").strip()
SAMPLE_TIMEOUT = float(_SAMPLE_TIMEOUT_RAW) if _SAMPLE_TIMEOUT_RAW else -1.0  # -1 = derive
# --- Multi-position injection scenario ---------------------------------------
# Each design point is evaluated as N_POSITIONS x N_REPLICAS separate Geant4
# processes, because two things cannot be done inside one process:
#   * /g4cmp/HitsFile cannot be re-pointed after /run/initialize, and a second
#     /run/beamOn TRUNCATES the hits file rather than appending. So one hits
#     file per source position requires one process per source position.
#   * one /run/beamOn per process, so a replica is a separate process too.
#     (CLHEP seeding IS controllable from the macro via /random/setSeeds --
#     see SEED_BASE -- but the hits-file constraint forces the process split.)
#
# Why more than one position at all: a single injection site measures one
# phonon caustic, not the device. Measured across 16 sites with the material
# held fixed, mean QPs per site ranged 2.515 -> 23.09 (9.18x, CV 60.6%) -- the
# same order as the candidate effects Stage 3 must resolve. Because the elastic
# tensor and orientation steer the caustic, a single-site objective can be
# improved by moving focusing away from that one site without reducing
# device-wide QP damage, and an optimizer will find that.
N_POSITIONS = int(os.environ.get("SENSITIVITY_N_POSITIONS", "1"))
N_REPLICAS = int(os.environ.get("SENSITIVITY_N_REPLICAS", "1"))
# TOTAL primary phonons per design point -- the ONE event-count knob. The
# per-sub-run /run/beamOn is DERIVED as TOTAL / (N_POSITIONS * N_REPLICAS).
# Configuring the per-sub-run count instead would make the same number silently
# mean N_POSITIONS x N_REPLICAS times more events depending on unrelated
# settings, and a wrong-but-valid event count is undetectable downstream: the
# run completes normally and only the physics is wrong.
# 0 means "take the total from the macro template's /run/beamOn".
TOTAL_EVENTS = int(os.environ.get("SENSITIVITY_TOTAL_EVENTS", "0"))
if os.environ.get("SENSITIVITY_EVENTS_PER_POSITION"):
    raise ValueError(
        "SENSITIVITY_EVENTS_PER_POSITION is not supported because its meaning "
        "would depend on SENSITIVITY_N_POSITIONS and SENSITIVITY_N_REPLICAS.\n"
        "Use SENSITIVITY_TOTAL_EVENTS -- the TOTAL primary phonons per design "
        "point -- and stage 1 will derive the per-sub-run /run/beamOn."
    )
# Injection sites are drawn once from a fixed scrambled-Sobol set and reused for
# EVERY design point. Identical sites across candidates is the whole point: it
# makes source position a common random number, so position variance cancels in
# candidate-minus-candidate differences instead of inflating them.
POSITION_SEED = int(os.environ.get("SENSITIVITY_POSITION_SEED", "20260727"))
# Substrate is 10 x 10 mm centred on the origin; stay 1 mm clear of the walls so
# the sampled sites probe the device, not the edge boundary condition.
POSITION_HALF_SPAN_MM = float(os.environ.get("SENSITIVITY_POSITION_HALF_SPAN_MM", "4.0"))

# --- CLHEP seeding -----------------------------------------------------------
# Main.cc does `setTheSeed((unsigned)clock())` at startup, which is not safe:
# clock() at process start is nearly constant, so the effective seed space is
# small and streams are reused across sub-runs. Main.cc executes the macro
# *after* seeding, so `/random/setSeeds` in the macro overrides it -- no C++
# change is required.
#
# The seed is a function of (trajectory, replica, position) and deliberately NOT
# of the step within a trajectory, so both endpoints of every elementary effect
# are evaluated on the SAME stream (common random numbers).
#
# Stage 3 will need two further keyings that are deliberately NOT implemented
# here rather than left as dead code: a noise-floor mode keyed by design point
# (N identical configurations must get N independent streams, or the measured
# noise floor is spuriously precise), and an explicit-candidate mode keyed by
# nothing at all (every candidate shares one bank so comparisons are paired).
SEED_BASE = int(os.environ.get("SENSITIVITY_SEED_BASE", "20260728"))
EXPLICIT_SEEDS = os.environ.get("SENSITIVITY_EXPLICIT_SEEDS", "1") == "1"
# Selects an independent family of streams without touching SEED_BASE, so a
# candidate selected on one bank can be confirmed on streams it has never been
# evaluated on. 0 (the default) contributes nothing.
SEED_BANK_ID = int(os.environ.get("SENSITIVITY_SEED_BANK_ID", "0"))

# --- Memory guard (shared host) ----------------------------------------------
# mimir is shared. A healthy sub-run is ~0.07 GB RSS, so PER_SAMPLE_MEM_GB=4 is
# ~55x headroom and still catches the KaplanQP runaway (measured 28 -> 79 GB)
# long before it matters. TOTAL_MEM_GB is the ceiling across all concurrent
# sub-runs. See G4CMP_crash_and_memory_analysis.md Finding 1.
#
# 200 GB against ~4.5 GB of expected steady usage at 64 workers is ~45x
# headroom, while leaving well over half of a 485 GB host to other users.
# Note that MAX_WORKERS * PER_SAMPLE_MEM_GB (64 x 4 = 256 GB) deliberately
# EXCEEDS this ceiling: in a mass-runaway the aggregate cap fires first and
# kills largest-offender-first, which is the behaviour we want on a shared box.
# The per-sample cap is there to catch a single runaway quickly, not to bound
# the total.
TOTAL_MEM_GB = float(os.environ.get("SENSITIVITY_TOTAL_MEM_GB", "200"))
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

# Match parameter_optimization/SoundfromTensor.py: average Christoffel-mode
# velocities over approximately uniform Fibonacci-sphere directions. The
# calculation is vectorized and cached because a 4-level Morris design repeats
# the same C11/C12/C44 combinations many times.
SOUND_SPEED_DIRECTION_COUNT = 10000


def fibonacci_sphere(n_points=SOUND_SPEED_DIRECTION_COUNT):
    indices = np.arange(n_points, dtype=float)
    golden_angle = np.pi * (3.0 - np.sqrt(5.0))
    z = 1.0 - 2.0 * (indices + 0.5) / n_points
    radius = np.sqrt(1.0 - z ** 2)
    phi = golden_angle * indices
    return np.column_stack((radius * np.cos(phi), radius * np.sin(phi), z))


SOUND_SPEED_DIRECTIONS = fibonacci_sphere()


def cubic_stiffness_is_stable(c11, c12, c44):
    """Born stability conditions for a cubic elastic tensor."""
    return c11 - c12 > 0 and c11 + 2.0 * c12 > 0 and c44 > 0


@lru_cache(maxsize=None)
def derive_cubic_sound_speeds(c11, c12, c44, density=SUBSTRATE_DENSITY_KG_M3):
    """Return spherical-average (longitudinal, transverse) speeds [m/s].

    This is the vectorized equivalent of SoundfromTensor.py for a cubic tensor.
    Density is fixed to Si because Stage 1 still instantiates G4_Si; Stage 3
    must supply each candidate material's density together with its tensor.
    """
    if not cubic_stiffness_is_stable(c11, c12, c44):
        raise ValueError(
            "Mechanically unstable cubic stiffness tensor: "
            f"C11={c11:g}, C12={c12:g}, C44={c44:g} GPa"
        )

    n = SOUND_SPEED_DIRECTIONS
    gamma = np.empty((len(n), 3, 3), dtype=float)
    for i in range(3):
        gamma[:, i, i] = c44 + (c11 - c44) * n[:, i] ** 2
        for j in range(i + 1, 3):
            gamma[:, i, j] = (c12 + c44) * n[:, i] * n[:, j]
            gamma[:, j, i] = gamma[:, i, j]

    eigenvalues, eigenvectors = np.linalg.eigh(gamma * 1e9)
    if np.any(eigenvalues <= 0):
        raise ValueError(
            "Non-positive Christoffel eigenvalue for cubic stiffness tensor: "
            f"C11={c11:g}, C12={c12:g}, C44={c44:g} GPa"
        )
    velocities = np.sqrt(eigenvalues / density)
    longitudinal_fraction = np.abs(np.einsum("nij,ni->nj", eigenvectors, n)) ** 2
    longitudinal_indices = np.argmax(longitudinal_fraction, axis=1)
    row_indices = np.arange(len(n))
    longitudinal = velocities[row_indices, longitudinal_indices]
    transverse_mean = (np.sum(velocities, axis=1) - longitudinal) / 2.0
    return float(np.mean(longitudinal)), float(np.mean(transverse_mean))


def debug_print(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)


def validate_logging_configuration():
    valid_modes = {"full", "failures", "none", "random"}
    if LOG_MODE not in valid_modes:
        raise ValueError(f"Unsupported SENSITIVITY_LOG_MODE: {LOG_MODE}. Expected one of: {valid_modes}")
    if not 0.0 <= LOG_FRACTION <= 1.0:
        raise ValueError(f"SENSITIVITY_LOG_FRACTION must be between 0 and 1. Got: {LOG_FRACTION}")


def canonical_design_names():
    """Expanded design-variable names, in the order generate_runfiles consumes
    them. Factored out so TRAJECTORY_LENGTH and build_morris_design cannot
    disagree: the macro writer indexes each sample row POSITIONALLY, so a
    mismatch would write values into the wrong commands."""
    names = []
    for param in electrode_params + detector_params + G4CMP_params + config_params + QPDE_params:
        if type(param[1]) is not list:
            names.append(param[0])
        else:
            names.extend(param[0] + "_" + str(i) for i in range(1, len(param[2]) + 1))
    return names


# One Morris trajectory visits len(names) + 1 design points.
TRAJECTORY_LENGTH = len(canonical_design_names()) + 1


def sub_run_seed(design_point_index, replica, position_index):
    """Deterministic CLHEP seed for one sub-run (see SEED_BASE rationale).

    Keyed by TRAJECTORY, not by design point, so all endpoints of a trajectory
    share a bank and every elementary effect is a paired comparison on one
    stream. That is the point of the construction, not a tidiness choice.
    """
    group = design_point_index // TRAJECTORY_LENGTH
    # Mixed with distinct odd multipliers so neighbouring (group, replica,
    # position) triples do not land on nearby seeds.
    raw = (SEED_BASE
           + SEED_BANK_ID * 7_919_003
           + group * 1_000_003
           + replica * 10_007
           + position_index * 101)
    return raw % 900_000_000 + 1  # keep well inside CLHEP's positive-int range


def assert_seed_bank_is_sound(n_design_points):
    """Fail before launching if the seed layout is not one bank per trajectory.

    A mis-keyed bank is silently destructive -- it correlates sub-runs that are
    meant to be independent -- so it is checked rather than trusted.
    """
    if not EXPLICIT_SEEDS:
        print("Seed check: SKIPPED (SENSITIVITY_EXPLICIT_SEEDS=0; "
              "Main.cc's unsafe clock() seeding is in effect).")
        return
    seeds = [
        sub_run_seed(dp, r, p)
        for dp in range(n_design_points)
        for r in range(N_REPLICAS)
        for p in range(N_POSITIONS)
    ]
    unique = len(set(seeds))
    n_traj = max(1, -(-n_design_points // TRAJECTORY_LENGTH))
    expected = n_traj * N_REPLICAS * N_POSITIONS
    if unique != expected:
        raise ValueError(
            f"Morris seed bank is wrong: {unique} unique seeds, expected {expected} "
            f"(one bank per trajectory x replica x position)."
        )
    print(f"Seed check: {unique} seed banks over {n_traj} trajectories "
          f"x {N_REPLICAS} replica(s) x {N_POSITIONS} position(s); "
          f"endpoints within a trajectory are paired. Bank id {SEED_BANK_ID}.")


def read_template_source_z():
    """z (mm) of /main/gun/setPosition in the macro template."""
    value = find_macro_value(MACRO_TEMPLATE, "/main/gun/setPosition")
    if value == "<missing>":
        raise ValueError("Macro template does not define /main/gun/setPosition: " + MACRO_TEMPLATE)
    tokens = value.split()
    if len(tokens) < 3:
        raise ValueError(
            "Macro template's /main/gun/setPosition needs three coordinates, got: " + value
        )
    return float(tokens[2])


def build_source_positions(template_z_mm):
    """Fixed scrambled-Sobol injection sites over the substrate, in mm.

    Returns a list of length N_POSITIONS. z is held at the template's value
    (just inside the top surface); only the lateral site moves, since that is
    what selects which phonon caustic is launched.

    N_POSITIONS should be a power of two: a scrambled Sobol set only carries its
    balance guarantee at powers of two, and an unbalanced site set biases the
    device average. (This is also why the reference protocol used 16 sites --
    it is unrelated to the 17 electrode locations, which are detectors and are
    all present in every run.)
    """
    if N_POSITIONS == 1:
        return [None]  # sentinel: leave the template's position untouched
    if N_POSITIONS & (N_POSITIONS - 1) != 0:
        raise ValueError(
            f"SENSITIVITY_N_POSITIONS={N_POSITIONS} is not a power of two. A scrambled "
            f"Sobol set is only balanced at powers of two, and an unbalanced injection "
            f"site set biases the device average over sites."
        )
    engine = qmc.Sobol(d=2, scramble=True, seed=POSITION_SEED)
    unit = engine.random(N_POSITIONS)
    span = POSITION_HALF_SPAN_MM
    return [
        (round(float(u[0]) * 2 * span - span, 6),
         round(float(u[1]) * 2 * span - span, 6),
         template_z_mm)
        for u in unit
    ]


def assert_substrate_is_supported():
    """Refuse a non-Si substrate until candidate material propagation exists.

    Two Si-specific things are hardcoded in this runner, and BOTH silently
    produce a pseudo-material rather than failing:

    1. `SUBSTRATE_DENSITY_KG_M3` is Si's 2329 kg/m^3, and the derived sound
       speeds divide by it. Measured: the Ge tensor with Si density gives
       vL = 7966 m/s against Ge's true 5324 m/s -- a **50% error**, not a
       correction. (With the correct density the same code reproduces the
       shipped Ge config to ~1%, so the maths is right; only the density is
       wrong.)
    2. `FIXED_CONFIG_COMMANDS` force `Debye 15 THz` and Si's third-order `dyn`
       constants over whatever the lattice template says. Ge's own config
       declares `Debye 2 THz`, so a Ge run would be actively overwritten with
       a Si value 7.5x too large -- an overwrite, not merely an inherited
       default.

    Until the Stage 3 material resolver propagates candidate density and makes
    the forced config lines candidate-aware, a non-Si substrate must fail here
    rather than produce numbers that look plausible.
    """
    if LATTICE_MATERIAL != "Si":
        raise ValueError(
            f"LATTICE_MATERIAL is {LATTICE_MATERIAL!r}, but this runner is Si-only:\n"
            f"  * derived sound speeds divide by SUBSTRATE_DENSITY_KG_M3="
            f"{SUBSTRATE_DENSITY_KG_M3:g} (Si) -- ~50% error for Ge;\n"
            f"  * FIXED_CONFIG_COMMANDS force Si's Debye/dyn over the candidate's.\n"
            f"Implement candidate density propagation and candidate-aware config "
            f"overrides (Stage 3 material resolver) before changing this."
        )


def resolve_sample_timeout(events_per_sub_run):
    """Per-sub-run wall-clock bound, in seconds.

    This is a WATCHDOG BOUND, not a performance calibration: it should never
    fire in normal operation. It is deliberately ~2 orders of magnitude above
    the measured throughput (~1.3e-4 s/event on mimir) so that only a genuine
    hang trips it. Calibrate it properly during the convergence/resource item of
    the Stage 3 checklist and set SENSITIVITY_SAMPLE_TIMEOUT explicitly.

    Explicit 0 still disables it, but says so loudly.
    """
    if SAMPLE_TIMEOUT == 0:
        print(
            "WARNING: SENSITIVITY_SAMPLE_TIMEOUT=0 disables the hang watchdog. The "
            "memory guard only catches runaways that GROW; a hang with flat RSS "
            "will hold a worker slot for the whole campaign.",
            flush=True,
        )
        return 0.0
    if SAMPLE_TIMEOUT > 0:
        return SAMPLE_TIMEOUT
    derived = max(1800.0, 100.0 * events_per_sub_run * 1.3e-4)
    print(
        f"Sample timeout not set; derived {derived:,.0f}s per sub-run "
        f"(~100x the measured throughput at {events_per_sub_run:,} events). "
        f"This is an uncalibrated safety bound -- set SENSITIVITY_SAMPLE_TIMEOUT "
        f"explicitly once it has been calibrated."
    )
    return derived


def resolve_event_counts():
    """(total per design point, per-sub-run /run/beamOn, provenance string).

    Splitting is exact by construction: a total that does not divide evenly into
    N_POSITIONS x N_REPLICAS sub-runs is refused rather than silently rounded,
    since rounding would give design points unequal statistics.
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


def _macro_energy_eV(macroname, command):
    """Value of an energy-valued macro command, in eV.

    Both commands are G4UIcmdWithADoubleAndUnit with default unit eV, so a bare
    number means eV; an explicit suffix is honoured.
    """
    raw = find_macro_value(macroname, command)
    if raw == "<missing>":
        raise ValueError(f"Macro {macroname} does not define {command}")
    tokens = raw.split()
    value = float(tokens[0])
    unit = tokens[1] if len(tokens) > 1 else "eV"
    scale = {"eV": 1.0, "meV": 1e-3, "keV": 1e3, "MeV": 1e6, "GeV": 1e9}
    if unit not in scale:
        raise ValueError(f"Unsupported energy unit {unit!r} in {command} of {macroname}")
    return value * scale[unit]


def assert_excitation_thresholds(macroname, quiet=False):
    """Enforce the excitation-threshold contract before anything is launched.

    "Above minEPhonons" is NOT sufficient and must never be used as the gate:
    minEPhonons is a numerical tracking cut, not a physical threshold. With it
    at 38.2 ueV, a 100 ueV primary passes that test and still yields exactly
    zero QPs forever, because JunctionKaplanElectrode::IsNearElectrode requires
    PhEnergy >= 2*GapJunc to break a pair at all.

    The chain that must hold:

        minEPhonons < 2*setTopGap <= E_gun < 2*setTopFilmGap

    Last link: above the ground-film gap the Nb plane also absorbs, so
    total_QPs stops being purely junction QPs and the objective silently
    changes meaning. See STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md sec 3.1.1.
    """
    e_gun = _macro_energy_eV(macroname, "/main/gun/setEnergy")
    min_e = _macro_energy_eV(macroname, "/g4cmp/minEPhonons")
    junction_gate = 2.0 * float(find_macro_value(macroname, "/main/detector_param/setTopGap"))
    film_gate = 2.0 * float(find_macro_value(macroname, "/main/detector_param/setTopFilmGap"))

    def ueV(x):
        return f"{x * 1e6:.1f} ueV"

    if not min_e < junction_gate:
        raise ValueError(
            f"minEPhonons ({ueV(min_e)}) is not below the junction pair-breaking gate "
            f"2*setTopGap ({ueV(junction_gate)}): a numerical cut would preempt the "
            f"physical one and truncate the downconversion cascade."
        )
    if e_gun < junction_gate:
        raise ValueError(
            f"Gun energy ({ueV(e_gun)}) is below the junction pair-breaking gate "
            f"2*setTopGap ({ueV(junction_gate)}). No phonon could break a pair, so "
            f"total_QPs would be identically zero for every material.\n"
            f"Being above minEPhonons ({ueV(min_e)}) is NOT sufficient."
        )
    if e_gun == junction_gate:
        raise ValueError(
            f"Gun energy sits exactly on the pair-breaking gate ({ueV(e_gun)}). The "
            f"outcome then depends on a single '>=' vs '>' comparison in G4CMP and on "
            f"float representation. Choose a value with positive margin."
        )
    # NOT a hard gate. Measured 2026-08-13: with /main/sensor/setHitType Junction,
    # PhononSensitivity::IsHit requires fJunctionElectrode->GetJunctionHit(), so a
    # ground-film absorption is never RECORDED as a hit. At 10 meV (3.25x the Nb
    # gap) 68 of 68 recorded surface hits were still inside junction footprints,
    # zero outside. The objective stays junction-only at any energy.
    #
    # What the film gap does control is the physics REGIME: above 2*gap the
    # ground plane becomes an active absorber and competes with the junctions for
    # phonons. That must be held constant across a comparison set, so it is
    # classified and recorded rather than forbidden.
    ground_plane_active = e_gun >= film_gate
    if not quiet:
        print(
            f"Excitation-threshold contract OK: minEPhonons {ueV(min_e)} < "
            f"2*setTopGap {ueV(junction_gate)} <= gun {ueV(e_gun)} "
            f"({e_gun / junction_gate:.2f}x the gate); 2*setTopFilmGap "
            f"{ueV(film_gate)} -> ground plane "
            f"{'ACTIVE absorber (competes for phonons)' if ground_plane_active else 'transparent'}."
        )


def verify_generated_beam_on(sub_runs, expected):
    """Assert every generated macro carries the intended event count.

    A wrong-but-valid event count cannot be detected downstream: the run
    completes normally and only the physics is wrong. Cheap insurance against a
    substitution silently not matching.
    """
    wrong = []
    for sub_run in sub_runs:
        value = find_macro_value(sub_run["macro"], "/run/beamOn")
        if value == "<missing>" or int(float(value.split()[0])) != expected:
            wrong.append((sub_run["sub_name"], value))
    if wrong:
        sample = ", ".join(f"{n}={v}" for n, v in wrong[:5])
        raise ValueError(
            f"{len(wrong)} generated macro(s) do not carry /run/beamOn {expected}: {sample}"
        )
    print(f"Verified /run/beamOn {expected:,} in all {len(sub_runs)} generated macros "
          f"({len(sub_runs)} sub-runs x {expected:,} = {len(sub_runs) * expected:,} events).")


def verify_generated_seeds(sub_runs):
    """Assert /random/setSeeds sits immediately before /run/beamOn in every macro.

    Order matters: anything between them (notably /main/detector_param/update)
    consumes draws and shifts the stream relative to the event loop, which would
    silently break the common-random-numbers pairing.
    """
    if not EXPLICIT_SEEDS:
        return
    bad = []
    for sub_run in sub_runs:
        with open(sub_run["macro"]) as handle:
            lines = [line.strip() for line in handle if line.strip()]
        try:
            beam_idx = next(i for i, line in enumerate(lines) if line.startswith("/run/beamOn"))
        except StopIteration:
            bad.append((sub_run["sub_name"], "no /run/beamOn"))
            continue
        if beam_idx == 0 or not lines[beam_idx - 1].startswith("/random/setSeeds"):
            bad.append((sub_run["sub_name"],
                        lines[beam_idx - 1] if beam_idx else "<start of file>"))
    if bad:
        sample = ", ".join(f"{n} (preceded by {v!r})" for n, v in bad[:5])
        raise ValueError(
            f"{len(bad)} macro(s) do not have /random/setSeeds immediately before "
            f"/run/beamOn: {sample}"
        )
    print(f"Verified /random/setSeeds immediately precedes /run/beamOn in all "
          f"{len(sub_runs)} macros.")


def verify_completion_markers(sub_runs):
    """Assert every macro writes its completion marker on the line AFTER beamOn.

    Order is the whole point: a marker written before or instead of the event
    loop would prove nothing. Also checks the marker path matches the sub-run,
    so two sub-runs cannot certify each other.
    """
    bad = []
    for sub_run in sub_runs:
        with open(sub_run["macro"]) as handle:
            lines = [line.strip() for line in handle if line.strip()]
        try:
            beam_idx = next(i for i, line in enumerate(lines) if line.startswith("/run/beamOn"))
        except StopIteration:
            bad.append((sub_run["sub_name"], "no /run/beamOn"))
            continue
        if beam_idx + 1 >= len(lines) or not lines[beam_idx + 1].startswith("/control/shell touch "):
            bad.append((sub_run["sub_name"], "no marker after /run/beamOn"))
            continue
        written = lines[beam_idx + 1].split(None, 2)[2].strip("'\"")
        if written != sub_run["done_marker"]:
            bad.append((sub_run["sub_name"], f"marker path mismatch: {written}"))
    if bad:
        sample = ", ".join(f"{n} ({why})" for n, why in bad[:5])
        raise ValueError(f"{len(bad)} macro(s) have a bad completion marker: {sample}")
    print(f"Verified the completion marker follows /run/beamOn in all {len(sub_runs)} macros.")


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
    """Fixed top-layer thickness [um], the third electrode-volume
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
        # Stored RELATIVE to RESULTS_DIR. Absolute paths made a results
        # directory non-portable: a copied or moved run silently read the
        # ORIGINAL run's hits files, so validating the copy validated the
        # original. The macro still carries the absolute path, since Geant4
        # runs from a different cwd.
        "hits_file": os.path.relpath(
            os.path.join(HITS_DIR, sample_name + "_hitsfile.txt"), RESULTS_DIR),
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


def build_qp_manifest_entries(sample_name, sub_runs, qpde_params):
    """One manifest entry per (design point, replica).

    Positions are POOLED inside an entry -- the design point's response is the
    device average over injection sites, so stage 2 concatenates that replica's
    position hits files and n_sim is the summed event count.

    Replicas are kept SEPARATE -- they are independent CLHEP realizations of the
    same physical configuration, so the spread across them is a direct empirical
    measurement of the Monte-Carlo noise floor. Averaging them here would
    destroy exactly the quantity needed to tell a real effect from a noise
    realization.
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
        entry["hits_files"] = [os.path.relpath(run["hits_file"], RESULTS_DIR)
                               for run in replica_runs]
        entry["done_markers"] = [os.path.relpath(run["done_marker"], RESULTS_DIR)
                                 for run in replica_runs]
        entry["seeds"] = [run.get("seed") for run in replica_runs]
        entry["position_indices"] = [run["position_index"] for run in replica_runs]
        entry.pop("hits_file", None)
        entry["n_sim"] = entry["n_sim_per_position"] * len(replica_runs)
        # Total across every replica of this design point -- the number the run
        # is actually configured with, recorded so provenance survives.
        entry["n_sim_total_design_point"] = (
            entry["n_sim_per_position"] * N_POSITIONS * N_REPLICAS)
        entries.append(entry)
    return entries


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
if SAMPLE_TIMEOUT < 0 and _SAMPLE_TIMEOUT_RAW:
    raise ValueError("SENSITIVITY_SAMPLE_TIMEOUT must be at least 0 (0 disables the timeout)")
if N_POSITIONS < 1:
    raise ValueError("SENSITIVITY_N_POSITIONS must be at least 1")
if N_REPLICAS < 1:
    raise ValueError("SENSITIVITY_N_REPLICAS must be at least 1")
if TOTAL_EVENTS < 0:
    raise ValueError("SENSITIVITY_TOTAL_EVENTS must be at least 0 (0 = take it from the template)")
if PER_SAMPLE_MEM_GB <= 0 or TOTAL_MEM_GB <= 0:
    raise ValueError("Memory-guard budgets must be positive")
if PER_SAMPLE_MEM_GB > TOTAL_MEM_GB:
    raise ValueError(
        f"SENSITIVITY_PER_SAMPLE_MEM_GB ({PER_SAMPLE_MEM_GB:g}) exceeds "
        f"SENSITIVITY_TOTAL_MEM_GB ({TOTAL_MEM_GB:g}); the per-sample cap could never fire."
    )
# mimir is shared: promising more than the host currently has is how a batch
# takes the machine down for other users, so refuse rather than hope.
_available_gb = host_available_gb()
if _available_gb == _available_gb and TOTAL_MEM_GB > _available_gb:  # NaN-safe
    raise ValueError(
        f"SENSITIVITY_TOTAL_MEM_GB ({TOTAL_MEM_GB:g} GB) exceeds the host's current "
        f"MemAvailable ({_available_gb:.0f} GB). Lower the budget; this host is shared."
    )
# Each sub-run is one single-threaded Geant4 process, so more workers than cores
# only adds context switching -- and on a shared host it also crowds out other
# users. A warning, not an error: a deliberate short oversubscription is valid.
_cpu_count = os.cpu_count() or 0
if _cpu_count and MAX_WORKERS > _cpu_count:
    print(
        f"WARNING: SENSITIVITY_MAX_WORKERS={MAX_WORKERS} exceeds this host's "
        f"{_cpu_count} cores; sub-runs are single-threaded, so this only adds "
        f"contention.",
        flush=True,
    )

# Define the set of parameters written into Geant4 macros.
macro_params = electrode_params + detector_params + G4CMP_params


def extract_qpde_params(samp, param_index):
    """f_01/r/s/I_ph/pt/n_cooper for this sample: sampled but never written
    to a macro/config file, only used for the post-hoc QP calculation."""
    return {name: float(samp[param_index[name]]) for name in QPDE_PARAM_NAMES}


def build_morris_design():
    all_params = electrode_params + detector_params + G4CMP_params + config_params + QPDE_params
    bounds = []
    names = []

    for param in all_params:
        debug_print(param[0])
        if type(param[1]) is not list:
            bounds.append(param[2])
            names.append(param[0])
        else:
            for index, parameter_bounds in enumerate(param[2], start=1):
                bounds.append(parameter_bounds)
                names.append(param[0] + "_" + str(index))

    setup = {
        "num_vars": len(bounds),
        "names": names,
        "bounds": bounds,
    }
    # Independent rectangular C11/C12/C44 bounds include some mechanically
    # unstable combinations. Preserve complete Morris trajectories while
    # rejecting any trajectory that enters the invalid part of that box.
    # Oversampling is deterministic when MORRIS_SEED is set.
    target_trajectories = 128
    candidate_trajectories = target_trajectories * 2
    trajectory_length = len(names) + 1
    while True:
        candidate_samples = np.round(
            morris_samp.sample(
                setup,
                candidate_trajectories,
                num_levels=4,
                seed=MORRIS_SEED,
            ),
            6,
        )
        candidate_samples = candidate_samples.reshape(
            candidate_trajectories, trajectory_length, len(names)
        )
        c11_index = names.index("stiffness 1 1 ")
        c12_index = names.index("stiffness 1 2 ")
        c44_index = names.index("stiffness 4 4 ")
        valid_trajectories = []
        for trajectory in candidate_samples:
            stable = all(
                cubic_stiffness_is_stable(
                    row[c11_index], row[c12_index], row[c44_index]
                )
                for row in trajectory
            )
            if stable:
                valid_trajectories.append(trajectory)
        if len(valid_trajectories) >= target_trajectories:
            break
        candidate_trajectories *= 2

    samples = np.asarray(valid_trajectories[:target_trajectories]).reshape(
        target_trajectories * trajectory_length, len(names)
    )
    rejected = candidate_trajectories - len(valid_trajectories)
    print(
        "Cubic-stability constraint: retained "
        f"{target_trajectories}/{candidate_trajectories} candidate trajectories "
        f"({rejected} rejected)."
    )
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
    print("Injection positions per design point:", N_POSITIONS,
          f"(scrambled Sobol, seed {POSITION_SEED}, +/-{POSITION_HALF_SPAN_MM:g} mm)"
          if N_POSITIONS > 1 else "(template position)")
    print("Replicas per design point:", N_REPLICAS)
    print("Events per design point (TOTAL):", f"{TOTAL_PER_POINT:,}", f"[{EVENTS_SOURCE}]")
    print("Events per sub-run (/run/beamOn, derived):", f"{EVENTS_PER_SUB_RUN:,}")
    print("Sub-runs per design point:", N_POSITIONS * N_REPLICAS)
    print("Explicit CLHEP seeds:", EXPLICIT_SEEDS,
          f"(base {SEED_BASE}, bank {SEED_BANK_ID})" if EXPLICIT_SEEDS else "(UNSAFE clock() seeding)")
    _expected_gb = (1 if DEBUG_MODE else MAX_WORKERS) * 0.07
    print("Memory guard: total", f"{TOTAL_MEM_GB:g} GB,",
          "per-sample", f"{PER_SAMPLE_MEM_GB:g} GB;",
          f"expected steady ~{_expected_gb:.1f} GB",
          f"({TOTAL_MEM_GB / _expected_gb:.0f}x headroom),",
          f"host MemAvailable {host_available_gb():.0f} GB")
    print("Total Morris samples:", total_samples)
    if MAX_SAMPLES > 0:
        print("Sample limit:", MAX_SAMPLES)
    print("Worker processes:", 1 if DEBUG_MODE else MAX_WORKERS)
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

    sample_name = "Morris_" + str(samp_No)
    # /g4cmp/HitsFile is NOT written here: it differs per sub-run and is
    # substituted at the end, once per (replica, position).
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
    # Fixed (non-swept) settings. Written in place so the values are guaranteed
    # regardless of the selected beamOn template and line order is preserved.
    for command_prefix, fixed_line in FIXED_MACRO_COMMANDS:
        lines = replace_line(
            lines,
            command_prefix,
            fixed_line,
            allow_commented=True,
            required=True,
        )

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
    # source position, the hits file, and the CLHEP stream they draw at run time.
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
                seed = sub_run_seed(samp_No, replica, position_index)
                beam_on_line = f"/random/setSeeds {seed} {seed + 1}\n{beam_on_line}"
            # Positive completion proof, written by Geant4 itself AFTER the event
            # loop returns. "hits file exists" is not sufficient: the header is
            # written when /run/beamOn starts, so a process killed mid-run leaves
            # a parseable, apparently legitimate zero-hit file. This marker can
            # only appear if the macro reached the line after beamOn.
            done_marker = sub_hits + ".done"
            beam_on_line += "\n/control/shell touch " + shlex.quote(done_marker)
            sub_lines = replace_line(sub_lines, "/run/beamOn ", beam_on_line, required=True)

            sub_macro = os.path.join(MACROS_DIR, sub_name + ".mac")
            with open(sub_macro, "w") as file:
                file.writelines(sub_lines)
            sub_runs.append({
                "sub_name": sub_name,
                "sample_name": sample_name,
                "macro": sub_macro,
                "hits_file": sub_hits,
                "done_marker": done_marker,
                "replica": replica,
                "position_index": position_index,
                "seed": seed,
            })

    # Modify the config.txt file for Si with the new parameters
    with open(CONFIG_TEMPLATE, 'r') as file:
        lines = file.readlines()
    file.close()

    sampled_config_values = {}
    for param in config_params:
        if type(param[1]) is not list:
            if len(param) == 3:
                sampled_value = samp[k]
                command_string = format_config_entry(param[0], sampled_value)
                debug_print(command_string)
                k += 1
            elif len(param) == 4:
                if param[3] == "int":
                    sampled_value = int(np.round(samp[k]))
                    command_string = format_config_entry(param[0], sampled_value)
                    debug_print(command_string)
                    k += 1
                else:
                    sampled_value = samp[k]
                    command_string = format_config_entry(param[0], sampled_value, param[3])
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
        if type(param[1]) is not list:
            sampled_config_values[param[0].strip()] = float(sampled_value)

    for command_prefix, fixed_line in FIXED_CONFIG_COMMANDS:
        lines = replace_line(lines, command_prefix, fixed_line, append_if_missing=True)

    vsound, vtrans = derive_cubic_sound_speeds(
        sampled_config_values["stiffness 1 1"],
        sampled_config_values["stiffness 1 2"],
        sampled_config_values["stiffness 4 4"],
    )
    lines = replace_line(
        lines,
        "vsound ",
        format_config_entry("vsound ", vsound, " m/s"),
        append_if_missing=True,
    )
    lines = replace_line(
        lines,
        "vtrans ",
        format_config_entry("vtrans ", vtrans, " m/s"),
        append_if_missing=True,
    )

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

    lattice_name is the *design point* name, which is NOT the macro basename
    once replicas/positions are in play (macro `Morris_7_r1_p3.mac` belongs to
    design point `Morris_7`). The lattice config is written once per design
    point, so G4LATTICEDATA must point at the design-point directory.
    """
    sample_name = (lattice_name if lattice_name is not None
                   else os.path.splitext(os.path.basename(macroname))[0])
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
        if return_code == -signal.SIGKILL:
            raise MemoryKilled(f"{sample_name} was SIGKILLed (memory guard or external kill)")
        raise subprocess.CalledProcessError(return_code, ["bash", "-lc", command])

    # See run_sample_parallel for the full contract.
    if not os.path.exists(sub_run.get("done_marker", "")):
        finalize_log_file(temp_log_file, log_file, True)
        raise MacroAborted(
            f"{sample_name} exited 0 but has no completion marker: the macro never "
            f"reached the line after /run/beamOn"
        )
    if not os.path.exists(hits_file):
        finalize_log_file(temp_log_file, log_file, True)
        raise MacroAborted(
            f"{sample_name} reported completion but wrote no hits file: {hits_file}"
        )

    finalize_log_file(temp_log_file, log_file, keep_success_log)
    print(f"[{run_index}/{total_runs}] Finished {sample_name} in {format_duration(elapsed)}")
    print(f"[{run_index}/{total_runs}] Hits file written: {hits_file}")
    return sample_name


def run_sample_parallel(sub_run, guard=None):
    """Run one sub-run; Geant4/G4CMP output goes straight to the log file.

    Runs on a pool *thread*, so the pid it spawns is visible to the parent's
    MemoryGuard; the sub-run is registered for the whole life of the process and
    unregistered in a finally, so a crash cannot leak a tracked pid.
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
    completed = os.path.exists(sub_run.get("done_marker", ""))

    if return_code != 0:
        # SIGKILL (-9) is what the memory guard uses, so report it distinctly:
        # a memory kill is a budget event, not a physics failure.
        if return_code == -signal.SIGKILL:
            finalize_log_file(temp_log_file, log_file, True)
            raise MemoryKilled(f"{sub_name} was SIGKILLed (memory guard or external kill)")
        # Known exit-time SIGSEGV (~0.4% of runs): a stale G4TouchableHandle is
        # released after its allocator pool is torn down, AFTER the macro has
        # finished. The completion marker distinguishes that harmless case from a
        # crash that lost physics -- so accept it only on positive proof, never
        # on the return code alone. bash reports a SIGSEGV child as 139.
        if return_code in (139, -signal.SIGSEGV) and completed:
            finalize_log_file(temp_log_file, log_file, True)
            print(
                f"NOTE: {sub_name} hit the known exit-time SIGSEGV after completing "
                f"(marker present); accepted as complete.",
                flush=True,
            )
            return sub_name
        finalize_log_file(temp_log_file, log_file, True)
        raise subprocess.CalledProcessError(return_code, ["bash", "-lc", command])

    # Exit 0 is NOT sufficient evidence that the sample ran, and neither is the
    # presence of the hits file: its header is written when /run/beamOn STARTS,
    # so a process killed mid-run leaves a parseable, apparently legitimate
    # zero-hit file. The marker is written by /control/shell on the line AFTER
    # beamOn, so it can only exist if the event loop returned.
    #
    # A malformed macro command (e.g. a bad unit suffix) makes Geant4 abort the
    # macro with only a G4Exception *warning*, skip /run/beamOn entirely, and
    # still exit 0; that path leaves neither hits file nor marker.
    if not completed:
        finalize_log_file(temp_log_file, log_file, True)
        raise MacroAborted(
            f"{sub_name} exited 0 but has no completion marker: the macro never "
            f"reached the line after /run/beamOn "
            f"(hits file {'present' if os.path.exists(sub_run['hits_file']) else 'absent'}; "
            f"check the log for 'Illegal parameter' / 'command aborted')"
        )
    if not os.path.exists(sub_run["hits_file"]):
        finalize_log_file(temp_log_file, log_file, True)
        raise MacroAborted(
            f"{sub_name} reported completion but wrote no hits file: {sub_run['hits_file']}"
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
    print(f"    python stage2_compute_QPs.py {RESULTS_DIR}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    validate_runtime_paths()
    for path in (HITS_DIR, LOGS_DIR, MACROS_DIR, CRYSTALMAPS_DIR):
        os.makedirs(path, exist_ok=False)

    TOTAL_PER_POINT, EVENTS_PER_SUB_RUN, EVENTS_SOURCE = resolve_event_counts()
    SAMPLE_TIMEOUT = resolve_sample_timeout(EVENTS_PER_SUB_RUN)
    assert_substrate_is_supported()
    SOURCE_POSITIONS = build_source_positions(read_template_source_z())

    names, samples = build_morris_design()
    # Every path must agree on column order, since generate_runfiles indexes the
    # row positionally. Checked rather than trusted so a future edit to either
    # builder cannot silently misalign the other.
    _canonical = canonical_design_names()
    if list(names) != _canonical:
        raise ValueError(
            f"Design column order disagrees with canonical_design_names() "
            f"({len(names)} vs {len(_canonical)} columns). The macro writer indexes "
            f"rows positionally, so this would write values into the wrong commands."
        )
    total_samples = samples.shape[0]
    assert_seed_bank_is_sound(total_samples)
    sequence_file = write_morris_sequence(names, samples)
    print("Wrote Morris sample sequence (all sampled parameter values) to:", sequence_file)
    print_startup_configuration(total_samples)

    param_index = {name: idx for idx, name in enumerate(names)}

    macro_files = []
    generation_start = time.monotonic()
    print(
        f"Generating {total_samples} design points x {N_REPLICAS} replica(s) "
        f"x {N_POSITIONS} position(s) = "
        f"{total_samples * N_REPLICAS * N_POSITIONS} sub-runs."
    )
    for s, samp in enumerate(samples):
        sample_name, sub_runs = generate_runfiles(samp, s)
        macro_files.extend(sub_runs)
        for entry in build_qp_manifest_entries(
                sample_name, sub_runs, extract_qpde_params(samp, param_index)):
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
    verify_generated_seeds(macro_files)
    verify_completion_markers(macro_files)
    # Checked on the GENERATED macros, not the template, so it sees the values
    # actually written -- and once per design point, not once overall, because
    # setTopGap/setTopFilmGap may be swept and therefore move the thresholds
    # from sample to sample. One representative sub-run per design point is
    # enough: sub-runs of a point differ only in position, hits file and seed.
    seen = set()
    checked = 0
    for sub_run in macro_files:
        if sub_run["sample_name"] in seen:
            continue
        seen.add(sub_run["sample_name"])
        assert_excitation_thresholds(sub_run["macro"], quiet=checked > 0)
        checked += 1
    print(f"Excitation-threshold contract verified for all {checked} design point(s).")

    if GENERATE_ONLY:
        print("Generation-only mode complete; Geant4/G4CMP simulations were not started.")
        print("Generated run files:", OUTPUT_DIR)
    else:
        main(macro_files)
