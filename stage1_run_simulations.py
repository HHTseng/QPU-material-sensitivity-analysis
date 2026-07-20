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
from concurrent.futures import ProcessPoolExecutor, as_completed
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
# Per-sample wall-clock limit in seconds; 0 (default) disables it. Guards
# against the G4CMPKaplanQP infinite loop documented in
# G4CMP_crash_and_memory_analysis.md (a film with QPLim=1 spins forever and
# can grow to tens of GB of RSS). The kill targets the whole process group,
# because killing the bash wrapper alone leaves an orphaned `Main` at 100% CPU.
SAMPLE_TIMEOUT = float(os.environ.get("SENSITIVITY_SAMPLE_TIMEOUT", "0"))
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
        "hits_file": os.path.join(HITS_DIR, sample_name + "_hitsfile.txt"),
        "gap": gap,
        "qx": qx.tolist(),
        "qy": qy.tolist(),
        "chip_z": chip_z,
        "height": height,
        "width": width,
        "thickness": thickness,
        "n_sim": n_sim,
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
    hits_file = os.path.join(HITS_DIR, sample_name + "_hitsfile.txt")
    lines = replace_line(
        lines,
        "/g4cmp/HitsFile",
        "/g4cmp/HitsFile " + hits_file,
        required=True,
    )
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

    # write the macro here
    output_macro_filename = os.path.join(MACROS_DIR, sample_name + ".mac")
    with open(output_macro_filename, 'w') as file:
        file.writelines(lines)
    file.close()

    # Modify the config.txt file for Si with the new parameters
    with open(CONFIG_TEMPLATE, 'r') as file:
        lines = file.readlines()
    file.close()

    for param in config_params:
        if type(param[1]) is not list:
            if len(param) == 3:
                command_string = format_config_entry(param[0], samp[k])
                debug_print(command_string)
                k += 1
            elif len(param) == 4:
                if param[3] == "int":
                    command_string = format_config_entry(param[0], int(np.round(samp[k])))
                    debug_print(command_string)
                    k += 1
                else:
                    command_string = format_config_entry(param[0], samp[k], param[3])
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

    return sample_name


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


def build_run_command(macroname):
    sample_name = os.path.splitext(os.path.basename(macroname))[0]
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


def run_sample_debug(macroname, run_index, total_runs):
    """Serial: streams Geant4/G4CMP stdout live plus verbose diagnostics.
    Only used in DEBUG_MODE, where execution is guaranteed single-process."""
    sample_name = os.path.splitext(os.path.basename(macroname))[0]
    lattice_root = os.path.join(CRYSTALMAPS_DIR, sample_name)
    config_file = os.path.join(CRYSTALMAPS_DIR, sample_name, LATTICE_MATERIAL, "config.txt")
    hits_file = os.path.join(HITS_DIR, sample_name + "_hitsfile.txt")
    command = build_run_command(macroname)

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


def run_sample_parallel(macroname):
    """Parallel: captures Geant4/G4CMP output straight to the log file (no
    live streaming, no per-sample prints). Safe inside a pool worker."""
    sample_name = os.path.splitext(os.path.basename(macroname))[0]
    command = build_run_command(macroname)

    log_file = os.path.join(LOGS_DIR, sample_name + ".log")
    temp_log = tempfile.NamedTemporaryFile(
        mode="w",
        dir=LOGS_DIR,
        prefix=sample_name + ".",
        suffix=".logtmp",
        delete=False,
    )
    temp_log_file = temp_log.name
    keep_success_log = should_keep_success_log(sample_name, LOG_MODE, LOG_FRACTION, LOG_SEED)

    try:
        with temp_log:
            process = subprocess.Popen(
                ["bash", "-lc", command],
                stdout=temp_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            watchdog = _start_timeout_watchdog(process)
            try:
                return_code = process.wait()
            finally:
                _stop_timeout_watchdog(watchdog)
    except Exception:
        finalize_log_file(temp_log_file, log_file, True)
        raise

    if watchdog is not None and watchdog.fired:
        finalize_log_file(temp_log_file, log_file, True)
        raise SampleTimeout(
            f"{sample_name} exceeded SENSITIVITY_SAMPLE_TIMEOUT={SAMPLE_TIMEOUT:g}s and was killed"
        )
    if return_code != 0:
        finalize_log_file(temp_log_file, log_file, True)
        raise subprocess.CalledProcessError(return_code, ["bash", "-lc", command])

    finalize_log_file(temp_log_file, log_file, keep_success_log)
    return sample_name


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
        print(f"Starting {total_runs} Morris runs in serial debug mode.")
        for run_index, macroname in enumerate(macro_files, start=1):
            sample_name = os.path.splitext(os.path.basename(macroname))[0]
            try:
                run_sample_debug(macroname, run_index, total_runs)
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
        print(f"Starting {total_runs} Morris runs with {MAX_WORKERS} workers.")
        completed = 0
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            macro_iter = iter(macro_files)
            in_flight = {}  # future -> macroname, so a failed future can still be identified

            for _ in range(min(MAX_WORKERS, total_runs)):
                try:
                    macroname = next(macro_iter)
                except StopIteration:
                    break
                in_flight[executor.submit(run_sample_parallel, macroname)] = macroname

            while in_flight:
                future = next(as_completed(in_flight))
                macroname = in_flight.pop(future)
                completed += 1
                sample_name = os.path.splitext(os.path.basename(macroname))[0]

                try:
                    sample_name = future.result()
                except Exception as exc:
                    failures.append((sample_name, exc))
                    print(f"[{completed}/{total_runs}] FAILED {sample_name}: {exc}")

                try:
                    next_macroname = next(macro_iter)
                except StopIteration:
                    pass
                else:
                    in_flight[executor.submit(run_sample_parallel, next_macroname)] = next_macroname

                if completed <= 5 or completed % PROGRESS_EVERY == 0 or completed == total_runs:
                    elapsed = time.monotonic() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0.0
                    remaining = total_runs - completed
                    eta_seconds = remaining / rate if rate > 0 else 0.0
                    print(
                        f"Completed {completed}/{total_runs} runs; "
                        f"latest={sample_name}; "
                        f"elapsed={format_duration(elapsed)}; "
                        f"eta={format_duration(eta_seconds)}"
                    )

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

    names, samples = build_morris_design()
    total_samples = samples.shape[0]
    sequence_file = write_morris_sequence(names, samples)
    print("Wrote Morris sample sequence (all sampled parameter values) to:", sequence_file)
    print_startup_configuration(total_samples)

    param_index = {name: idx for idx, name in enumerate(names)}

    macro_files = []
    generation_start = time.monotonic()
    print(f"Generating {total_samples} Morris macros/configs.")
    for s, samp in enumerate(samples):
        sample_name = generate_runfiles(samp, s)
        macroname = os.path.join(MACROS_DIR, "Morris_" + str(s) + ".mac")
        macro_files.append(macroname)
        write_qp_manifest_entry(build_qp_manifest_entry(sample_name, macroname, extract_qpde_params(samp, param_index)))
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

    if GENERATE_ONLY:
        print("Generation-only mode complete; Geant4/G4CMP simulations were not started.")
        print("Generated run files:", OUTPUT_DIR)
    else:
        main(macro_files)
