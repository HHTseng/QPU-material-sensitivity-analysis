import numpy as np
from scipy.stats import qmc
import os
import csv
import shlex
import subprocess
import hashlib
import tempfile
import time
import uuid
from SALib.sample import morris as morris_samp
from SALib.analyze import morris
unique_id = str(uuid.uuid4())

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_TO_GEANT4_ENV = "/home/software/Geant4/Geant4-Install/share/Geant4-10.7.4/geant4make/geant4make.sh"
PATH_TO_G4CMP_ENV = "/home/htseng/src/G4CMP_htseng/g4cmp_env.sh"
G4CMP_SOURCE_CRYSTALMAPS = "/home/htseng/src/G4CMP_htseng/CrystalMaps"
G4CMPLIB = "/home/htseng/geant4_workdir/lib/Linux-g++"
BNL_MAIN_DIR = "/home/htseng/BNL_G4CMP_HT_Feb27/Main"
MAIN_EXE = "/home/htseng/geant4_workdir/bin/Linux-g++/Main"
MACRO_TEMPLATE = os.environ.get(
    "SENSITIVITY_MACRO_TEMPLATE",
    os.path.join(SCRIPT_DIR, "sensitivity_template_beamOn10000.mac"),
)
CONFIG_TEMPLATE = os.path.join(G4CMP_SOURCE_CRYSTALMAPS, "Si", "config.txt")
LATTICE_MATERIAL = "Si"
VERBOSE = os.environ.get("SENSITIVITY_VERBOSE", "0") == "1"
PROGRESS_EVERY = int(os.environ.get("SENSITIVITY_PROGRESS_EVERY", "25"))
MAX_SAMPLES = int(os.environ.get("SENSITIVITY_MAX_SAMPLES", "0"))
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


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def validate_logging_configuration():
    valid_modes = {"full", "failures", "none", "random"}
    if LOG_MODE not in valid_modes:
        raise ValueError(
            "Unsupported SENSITIVITY_LOG_MODE: "
            + LOG_MODE
            + ". Expected one of: full, failures, none, random"
        )
    if not 0.0 <= LOG_FRACTION <= 1.0:
        raise ValueError(
            "SENSITIVITY_LOG_FRACTION must be between 0 and 1. Got: "
            + str(LOG_FRACTION)
        )


def get_sample_lattice_root(sample_name):
    return os.path.join(CRYSTALMAPS_DIR, sample_name)


def get_sample_config_file(sample_name):
    return os.path.join(get_sample_lattice_root(sample_name), LATTICE_MATERIAL, "config.txt")


def get_sample_hits_file(sample_name):
    return os.path.join(HITS_DIR, sample_name + "_hitsfile.txt")


def get_sample_log_file(sample_name):
    return os.path.join(LOGS_DIR, sample_name + ".log")


def should_keep_success_log(sample_name):
    if LOG_MODE == "full":
        return True
    if LOG_MODE in {"failures", "none"}:
        return False

    digest = hashlib.sha256(f"{LOG_SEED}:{sample_name}".encode("ascii")).digest()
    sample_value = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return sample_value < LOG_FRACTION


def finalize_log_file(temp_log_file, final_log_file, keep_log):
    if keep_log:
        os.replace(temp_log_file, final_log_file)
    else:
        os.unlink(temp_log_file)


def format_config_entry(key, value, suffix=""):
    if suffix:
        return f"{key} {value}{suffix}"
    return f"{key} {value}"


def find_macro_value(macroname, command_name):
    with open(macroname, "r") as file:
        for line in file:
            stripped = line.strip()
            if stripped.startswith(command_name):
                return stripped[len(command_name):].strip()
    return "<missing>"


RUN_ID = "morris_debug_serial_" + unique_id
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", RUN_ID)
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", RUN_ID)
HITS_DIR = os.path.join(RESULTS_DIR, "hits")
LOGS_DIR = os.path.join(RESULTS_DIR, "logs")
MACROS_DIR = os.path.join(OUTPUT_DIR, "macros")
CRYSTALMAPS_DIR = os.path.join(OUTPUT_DIR, "CrystalMaps")

for path in (HITS_DIR, LOGS_DIR, MACROS_DIR, CRYSTALMAPS_DIR):
    os.makedirs(path, exist_ok=False)

validate_logging_configuration()


def print_startup_configuration():
    print("Debug run mode: serial / no multiprocessing")
    print("Geant4/G4CMP stdout/stderr: live on screen and saved to log files")
    print("Using macro template:", MACRO_TEMPLATE)
    print("Writing generated macros/configs to:", OUTPUT_DIR)
    print("Writing sensitivity results to:", RESULTS_DIR)
    print("Total Morris samples:", TOTAL_SAMPLES)
    if MAX_SAMPLES > 0:
        print("Debug sample limit:", MAX_SAMPLES)
    print("Worker processes: 1")
    print("Progress update interval:", PROGRESS_EVERY)
    print("Log mode:", LOG_MODE)
    if LOG_MODE == "random":
        print("Log fraction:", LOG_FRACTION)
        print("Log seed:", LOG_SEED)
    print("Thread environment variables:")
    for env_name in THREAD_ENV_VARS:
        print(f"  {env_name}={os.environ.get(env_name, '<unset>')}")


lbf = 0.5  # lower bound factor
ubf = 1.5  # upper bound factor

#BNL_G4CMP electrode parameters. Format is (macro command, default value, range, unit (optional)).
electrode_params = [
# The sensitivity template uses phonon_Caustic; GeV energies produce invalid phonon tracks.
("/main/gun/setEnergy ", 2 * 191.0e-6, [(2 * 191.0e-6) * lbf, (2 * 191.0e-6) * ubf], " eV"),
("/main/electrode_param/setHeight ", 10, [10 * lbf, 10 * ubf]),
("/main/electrode_param/setWidth ", 10, [10 * lbf, 10 * ubf]),
#("/main/electrode_param/setXLocations ", 0, [-3.98,3.98]),
#("/main/electrode_param/setYLocations ", 0, [-3.98,3.98]),
("/main/electrode_param/setIsland ", 200, [200 * lbf, 200 * ubf], " um"),
("/main/electrode_param/setIslandSpacing ", 50, [50 * lbf, 50 * ubf], " um")
#("/main/electrode_param/setGapThres ", 0.0, [0.0,0.0003595/2]),
]

#BNL_G4CMP detector parameters. Format is (macro command, default value, range, unit (optional)).
detector_params = [
("/main/detector_param/setTopThickness ", 0.12, [0.12 * lbf, 0.12 * ubf], " um"),
("/main/detector_param/setTopFilmThickness ", 0.075, [0.075 * lbf, 0.075 * ubf], " um"),
("/main/detector_param/setBotThickness ", 1, [1 * lbf, 1 * ubf], " um"),
#("/main/detector_param/setSubThickness ", 525, [525*lbf,525*ubf]),
#("/main/detector_param/setSubWidth ", 8, [8*lbf,8*ubf]),
#("/main/detector_param/setSubHeight ", 8, [8*lbf,8*ubf]),
#"/main/detector_param/setSubstrateName ",
#"/main/detector_param/setSubstrateG4Name ",
#("/main/detector_param/setMiller ", [0, 0, 1]),
("/main/detector_param/setLatticeDeg ", 45, [45 * lbf, 45 * ubf]),
#("/main/detector_param/setTopSourceMat ", "G4_Al"),
("/main/detector_param/setTopAbs ", 0.795, [0.795 * lbf, 1]),
("/main/detector_param/setTopVSound ", 3.582, [3.582 * lbf, 3.582 * ubf]),
("/main/detector_param/setTopGap ", 191.0e-6, [(191.0e-6) * lbf, (191.0e-6) * ubf]),
("/main/detector_param/setTopQPLim ", 3, [1, 5], "int"),
("/main/detector_param/setTopPhLifetime ", 0.242, [0.242 * lbf, 0.242 * ubf]),
("/main/detector_param/setTopPhLifetimeSlope ", 0.29, [0.29 * lbf, 0.29 * ubf]),
#("/main/detector_param/setTopSubGapAbs ", 0.0, [0.0,179.75e-6]),
("/main/detector_param/setTopPSpecProb ", 0.0, [0, 1]),
#("/main/detector_param/setTopFilmSourceMat ", "G4_Nb")
("/main/detector_param/setTopFilmAbs ", 0.745, [0.745 * lbf, 1]),
("/main/detector_param/setTopFilmVSound ", 2.444, [2.444 * lbf, 2.444 * ubf]),
("/main/detector_param/setTopFilmGap ", 1.5384e-3, [1.5384e-3 * lbf, 1.5384e-3 * ubf]),
("/main/detector_param/setTopFilmQPLim ", 3, [1, 5], "int"),
("/main/detector_param/setTopFilmPhLifetime ", 0.00417, [0.00417 * lbf, 0.00417 * ubf]),
("/main/detector_param/setTopFilmPhLifetimeSlope ", 0.29, [0.29 * lbf, 0.29 * ubf]),
#("/main/detector_param/setTopFilmSubGapAbs ", 0.0, [0,1.5384e-3]),
("/main/detector_param/setTopFilmPSpecProb ", 0.0, [0, 1]),
#("/main/detector_param/setBotSourceMat ", "G4_Cu")
("/main/detector_param/setBotAbs ", 0.736, [0.736 * lbf, 1]),
("/main/detector_param/setBotVSound ", 2.608, [2.608 * lbf, 2.608 * ubf]),
#("/main/detector_param/setBotGap ", 0.0, [0.0,0.0]),
("/main/detector_param/setBotGapThres ", 180e-6, [180e-6 * lbf, 180e-6 * ubf]),
("/main/detector_param/setBotQPLim ", 2, [1, 5], "int"),
("/main/detector_param/setBotPhLifetime ", 5.1, [5.1 * lbf, 5.1 * ubf]),
#("/main/detector_param/setBotPhLifetimeSlope ", 5.3, [5.3*lbf,5.3*ubf]),
#("/main/detector_param/setBotSubGapAbs ", 0.0, [0.0,0.0]),
("/main/detector_param/setBotPSpecProb ", 0.0, [0, 1]),
("/main/detector_param/setWallAbs ", 0.2, [0.0, 0.5]),
("/main/detector_param/setWallPSpecProb ", 0.0, [0, 1]),
]

# G4CMP Parameters
G4CMP_params = [
("/g4cmp/kaplanQPL/delta ", 0.001, [0.001 * lbf, 0.001 * ubf]),
("/g4cmp/kaplanQPL/beta ", 0.35, [0.35 * lbf, 0.35 * ubf]),
("/g4cmp/kaplanQPL/gamma ", 0.1, [0.1 * lbf, 0.1 * ubf]),
("/g4cmp/kaplanQPL/epsilon ", 0.05, [0.05 * lbf, 0.05 * ubf]),
("/g4cmp/phononPhysics/anharmonicDecay ", 43800000.0, [43800000.0 * lbf, 43800000.0 * ubf]),
("/g4cmp/phononPhysics/isotopeScattering ", 10500000.0, [10500000.0 * lbf, 10500000.0 * ubf]),
]

#G4CMP config.txt parameters. Format is (macro command, default value, range, unit (optional)).
config_params = [
# Crystal parameters
("cubic ", 5.431, [5.431*lbf, 5.431*ubf], " Ang"),
("stiffness 1 1 ", 165.6, [165.6*lbf, 165.6*ubf], " GPa"),
("stiffness 1 2 ",  63.9, [63.9*lbf, 63.9*ubf], " GPa"),
("stiffness 4 4 ",  79.5, [79.5*lbf, 79.5*ubf], " GPa"),
# Phonon parameters
("dyn ", [-42.9, -94.5, 52.4, 68.0], [[-42.9*ubf,-42.9*lbf], [-94.5*ubf,-94.5*lbf], [52.4*lbf,52.4*ubf], [68.0*lbf,68.0*ubf]], " GPa"),
("scat ", 2.43e-42, [(2.43)*lbf,(2.43)*ubf], "e-42 s3"),
("decay ", 7.41e-56, [(7.41)*lbf,(7.41)*ubf], "e-56 s4"),
("decayTT ", 0.74, [0.74*lbf,1]),
# From S. Tamura et al., PRB44(7), 1991
#("LDOS ",  0.093), #Need to figure out how to vary while maintaining LDOS + STDOS + FTDOS = 1
#("STDOS ", 0.531),
#("FTDOS ", 0.376),
("Debye ", 15, [15*lbf,15*ubf], " THz"),
# Charge carrier parameters
# ("bandgap ", 1.17, [1.17*lbf,1.17*ubf], " eV"),
# ("pairEnergy ", 3.81, [3.81*lbf,3.81*ubf], " eV"),
# ("fanoFactor ", 0.15, [0.15*lbf,0.15*ubf]),
("vsound ", 9000, [9000*lbf,9000*ubf], " m/s"),
("vtrans ", 5400, [5400*lbf,5400*ubf], " m/s"),
# ("l0_e ", 16.9e-6, [16.9e-6*lbf,16.9e-6*ubf], " m"),
# ("l0_h ", 7.5e-5, [7.5e-5*lbf,7.5e-5*ubf], " m"),
# #hole and electron masses taken from Robert's thesis
# ("hmass ", 0.50, [0.50*lbf,0.50*ubf]),
# ("emass ", [0.91, 0.19, 0.19], [[0.91*lbf,0.91*ubf], [0.19*lbf,0.19*ubf], [0.19*lbf,0.19*ubf]]),
# #("valleyDir ", [1, 0, 0]),
# #("valleyDir ", [0, 1, 0]),
# #("valleyDir ", [0, 0, 1]),
# # Intervalley scattering (matrix elements)
# ("alpha ", 0.5, [0.5*lbf,0.5*ubf], " /eV"),
# ("acDeform_e ", 9, [9*lbf,9*ubf], " eV"),
# ("acDeform_h ", 5, [5*lbf,5*ubf], " eV"),
# ("ivDeform ", [0.5e8, 0.8e8, 11e8, 0.3e8, 2e8, 2e8], [[0.5e8*lbf,0.5e8*ubf], [0.8e8*lbf,0.8e8*ubf], [11e8*lbf,11e8*ubf], [0.3e8*lbf,0.3e8*ubf], [2e8*lbf,2e8*ubf], [2e8*lbf,2e8*ubf]], " eV/cm"),
# ("ivEnergy ", [12.0e-3, 18.4e-3, 61.8e-3, 18.9e-3, 47.2e-3, 58.8e-3], [[12.0e-3*lbf,12.0e-3*ubf], [18.4e-3*lbf,18.4e-3*ubf], [61.8e-3*lbf,61.8e-3*ubf], [18.9e-3*lbf,18.9e-3*ubf], [47.2e-3*lbf,47.2e-3*ubf], [58.8e-3*lbf,58.8e-3*ubf]], " eV"),
# ("neutDens ", 1e11, [1e11*lbf,1e11*ubf], " /cm3"),
# ("epsilon ", 11.68, [11.68*lbf,11.68*ubf]),
# # Intervalley scattering (Linear and Quadratic models)
# #("ivModel ", "Linear"),
# ("ivLinRate0 ",  1.5e6, [1.5e6*lbf,1.5e6*ubf], " Hz"),
# ("ivLinRate1 ",  1.5, [1.5*lbf,1.5*ubf], " Hz"),
# ("ivLinPower ",  4.0, [4.0*lbf,4.0*ubf]),
# ("ivQuadRate ",  3.5e-20, [3.5*lbf,3.5*ubf], "e-20 Hz"),
# ("ivQuadField ", 3395, [3395*lbf,3395*ubf], " V/m"),
# ("ivQuadPower ", 7.47, [7.47*lbf,7.47*ubf])
]

# QPDE Parameters
QPDE_params = [
("QPstrength", 1.0, [1.0 * lbf, 1.0 * ubf]),
("PhononLifetime", 1.0, [1.0 * lbf, 1.0 * ubf]),
("Absorption", 1.0, [1.0 * lbf, 1.0 * ubf]),
("SensorArea", 1.0, [1.0 * lbf, 1.0 * ubf]),
]

all_params = electrode_params + detector_params + G4CMP_params + config_params + QPDE_params
print("Number of parameter groups:", len(all_params))
macro_params = electrode_params + detector_params + G4CMP_params

bounds = []
names = []
for param in all_params:
    debug_print(param[0])
    if type(param[1]) is not list:
        bounds.append(param[2])
        names.append(param[0])
    else:
        n = 1
        for p in param[2]:
            bounds.append(p)
            names.append(param[0] + "_" + str(n))
            n += 1

VNUM = len(bounds)
Morris_Setup = {
    "num_vars": VNUM,
    "names": names,
    "bounds": bounds,
}

samples = np.round(morris_samp.sample(Morris_Setup, 128, num_levels=4), 6)
if MAX_SAMPLES > 0:
    samples = samples[:MAX_SAMPLES]
print("Morris sample shape:", samples.shape, len(samples[0]))
TOTAL_SAMPLES = samples.shape[0]
print_startup_configuration()


def replace_line(lines, start_string, new_line, allow_commented=False, append_if_missing=False):
    command = start_string.strip()

    def matches(candidate):
        if not candidate.startswith(command):
            return False
        if allow_commented:
            return True
        return not candidate.startswith("#")

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if matches(stripped):
            lines[idx] = new_line + "\n"
            return lines
        if allow_commented and stripped.startswith("#") and stripped[1:].strip().startswith(command):
            lines[idx] = new_line + "\n"
            return lines

    if append_if_missing:
        lines.append(new_line + "\n")
    return lines


def generate_runfiles(samp, s):
    sample_name = "Morris_" + str(s)
    k = 0

    with open(MACRO_TEMPLATE, "r") as file:
        lines = file.readlines()

    hits_file = get_sample_hits_file(sample_name)
    lines = replace_line(lines, "/g4cmp/HitsFile", "/g4cmp/HitsFile " + hits_file)
    lines = replace_line(
        lines,
        "/main/detector_param/setSubstrateG4Name ",
        "/main/detector_param/setSubstrateG4Name G4_Si",
        allow_commented=True,
    )
    lines = replace_line(
        lines,
        "/main/detector_param/setSubstrateName ",
        "/main/detector_param/setSubstrateName " + LATTICE_MATERIAL,
        allow_commented=True,
    )

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
                print("The parameter " + param[0] + " does not conform to the expected format.")
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
                        vec = vec + str(int(np.round(samp[k]))) + ","
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

        lines = replace_line(lines, param[0], command_string)

    output_macro_filename = os.path.join(MACROS_DIR, sample_name + ".mac")
    with open(output_macro_filename, "w") as file:
        file.writelines(lines)

    with open(CONFIG_TEMPLATE, "r") as file:
        lines = file.readlines()

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

    output_config_dir = os.path.join(get_sample_lattice_root(sample_name), LATTICE_MATERIAL)
    os.makedirs(output_config_dir, exist_ok=True)

    output_config_filename = os.path.join(output_config_dir, "config.txt")
    with open(output_config_filename, "w") as file:
        file.writelines(lines)

    return sample_name


def run_sample(macroname, run_index, total_runs):
    if not os.path.exists(MAIN_EXE):
        raise FileNotFoundError(
            "Missing Main executable: "
            + MAIN_EXE
            + ". Build it with: cd "
            + BNL_MAIN_DIR
            + " && make"
        )

    sample_name = os.path.splitext(os.path.basename(macroname))[0]
    lattice_root = get_sample_lattice_root(sample_name)
    config_file = get_sample_config_file(sample_name)
    hits_file = get_sample_hits_file(sample_name)
    command = (
        "set -e; "
        "set +u; "
        + "source " + shlex.quote(PATH_TO_GEANT4_ENV) + "; "
        + "unset G4CMPINSTALL G4CMPINCLUDE G4LATTICEDATA G4CMPLIB; "
        + "source " + shlex.quote(PATH_TO_G4CMP_ENV) + "; "
        + "set -u; "
        + "export G4LATTICEDATA=" + shlex.quote(lattice_root) + "; "
        + "case \":${LD_LIBRARY_PATH:-}:\" in *\":"
        + G4CMPLIB
        + ":\"*) ;; *) export LD_LIBRARY_PATH="
        + shlex.quote(G4CMPLIB)
        + "${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} ;; esac; "
        + "cd " + shlex.quote(BNL_MAIN_DIR) + "; "
        + shlex.quote(MAIN_EXE) + " " + shlex.quote(macroname)
    )

    log_file = get_sample_log_file(sample_name)
    start_time = time.monotonic()
    keep_success_log = should_keep_success_log(sample_name)

    print("")
    print(f"[{run_index}/{total_runs}] Starting {sample_name}")
    print(f"[{run_index}/{total_runs}] Macro: {macroname}")
    print(f"[{run_index}/{total_runs}] Log:   {log_file}")
    print(f"[{run_index}/{total_runs}] G4LATTICEDATA: {lattice_root}")
    print(f"[{run_index}/{total_runs}] Expected config: {config_file}")
    print(f"[{run_index}/{total_runs}] Expected hits:   {hits_file}")
    print(f"[{run_index}/{total_runs}] Macro HitsFile:  {find_macro_value(macroname, '/g4cmp/HitsFile')}")
    print(f"[{run_index}/{total_runs}] Macro beamOn:    {find_macro_value(macroname, '/run/beamOn')}")
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
            )
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    print(line, end="")
                    temp_log.write(line)
                return_code = process.wait()
            finally:
                if process.stdout is not None:
                    process.stdout.close()
    except Exception:
        finalize_log_file(temp_log_file, log_file, True)
        raise

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


def main(macro_files):
    start_time = time.monotonic()
    total_runs = len(macro_files)
    print(f"Starting {total_runs} Morris runs in serial debug mode.")

    for run_index, macroname in enumerate(macro_files, start=1):
        sample_name = run_sample(macroname, run_index, total_runs)
        if run_index <= 5 or run_index % PROGRESS_EVERY == 0 or run_index == total_runs:
            elapsed = time.monotonic() - start_time
            rate = run_index / elapsed if elapsed > 0 else 0.0
            remaining = total_runs - run_index
            eta_seconds = remaining / rate if rate > 0 else 0.0
            print(
                f"Completed {run_index}/{total_runs} runs; "
                f"latest={sample_name}; "
                f"elapsed={format_duration(elapsed)}; "
                f"eta={format_duration(eta_seconds)}"
            )


if __name__ == "__main__":
    macro_files = []
    generation_start = time.monotonic()
    print(f"Generating {TOTAL_SAMPLES} Morris macros/configs.")
    for s, samp in enumerate(samples):
        sample_name = generate_runfiles(samp, s)
        macro_files.append(os.path.join(MACROS_DIR, "Morris_" + str(s) + ".mac"))
        written = s + 1
        if written <= 5 or written % PROGRESS_EVERY == 0 or written == TOTAL_SAMPLES:
            elapsed = time.monotonic() - generation_start
            rate = written / elapsed if elapsed > 0 else 0.0
            remaining = TOTAL_SAMPLES - written
            eta_seconds = remaining / rate if rate > 0 else 0.0
            print(
                f"Wrote {written}/{TOTAL_SAMPLES} macros/configs; "
                f"latest={sample_name}.mac; "
                f"elapsed={format_duration(elapsed)}; "
                f"eta={format_duration(eta_seconds)}"
            )

    main(macro_files)
