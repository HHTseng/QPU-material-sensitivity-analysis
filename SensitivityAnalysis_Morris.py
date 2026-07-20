import numpy as np
from scipy.stats import qmc
import os
import csv
import shlex
import subprocess
import hashlib
import tempfile
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
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
MAX_WORKERS = int(os.environ.get("SENSITIVITY_MAX_WORKERS", "32"))
VERBOSE = os.environ.get("SENSITIVITY_VERBOSE", "0") == "1"
PROGRESS_EVERY = int(os.environ.get("SENSITIVITY_PROGRESS_EVERY", "25"))
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

def print_startup_configuration():
    print("Using macro template:", MACRO_TEMPLATE)
    print("Writing generated macros/configs to:", OUTPUT_DIR)
    print("Writing sensitivity results to:", RESULTS_DIR)
    print("Total Morris samples:", TOTAL_SAMPLES)
    print("Worker processes:", MAX_WORKERS)
    print("Progress update interval:", PROGRESS_EVERY)
    print("Log mode:", LOG_MODE)
    if LOG_MODE == "random":
        print("Log fraction:", LOG_FRACTION)
        print("Log seed:", LOG_SEED)
    print("Thread environment variables:")
    for env_name in THREAD_ENV_VARS:
        print(f"  {env_name}={os.environ.get(env_name, '<unset>')}")

RUN_ID = "morris_" + unique_id
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", RUN_ID)
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", RUN_ID)
HITS_DIR = os.path.join(RESULTS_DIR, "hits")
LOGS_DIR = os.path.join(RESULTS_DIR, "logs")
MACROS_DIR = os.path.join(OUTPUT_DIR, "macros")
CRYSTALMAPS_DIR = os.path.join(OUTPUT_DIR, "CrystalMaps")

for path in (HITS_DIR, LOGS_DIR, MACROS_DIR, CRYSTALMAPS_DIR):
    os.makedirs(path, exist_ok=False)

validate_logging_configuration()

lbf = 0.5 # lower bound factor
ubf = 1.5 # upper bound factor

#BNL_G4CMP electrode parameters. Format is (macro command, default value, range, unit (optional)).
electrode_params = [
# The sensitivity template uses phonon_Caustic; GeV energies produce invalid phonon tracks.
("/main/gun/setEnergy ", 2*191.0e-6, [(2*191.0e-6)*lbf,(2*191.0e-6)*ubf], " eV"),
("/main/electrode_param/setHeight ", 10, [10*lbf,10*ubf]),
("/main/electrode_param/setWidth ", 10, [10*lbf,10*ubf]),
#("/main/electrode_param/setXLocations ", 0, [-3.98,3.98]),
#("/main/electrode_param/setYLocations ", 0, [-3.98,3.98]),
("/main/electrode_param/setIsland ", 200, [200*lbf,200*ubf], " um"),
("/main/electrode_param/setIslandSpacing ", 50, [50*lbf,50*ubf], " um")
#("/main/electrode_param/setGapThres ", 0.0, [0.0,0.0003595/2]),
]

#BNL_G4CMP detector parameters. Format is (macro command, default value, range, unit (optional)).
detector_params = [
("/main/detector_param/setTopThickness ", 0.12, [0.12*lbf,0.12*ubf], " um"),
("/main/detector_param/setTopFilmThickness ", 0.075, [0.075*lbf,0.075*ubf], " um"),
("/main/detector_param/setBotThickness ", 1, [1*lbf,1*ubf], " um"),
#("/main/detector_param/setSubThickness ", 525, [525*lbf,525*ubf]),
#("/main/detector_param/setSubWidth ", 8, [8*lbf,8*ubf]),
#("/main/detector_param/setSubHeight ", 8, [8*lbf,8*ubf]),
#"/main/detector_param/setSubstrateName ",
#"/main/detector_param/setSubstrateG4Name ",
#("/main/detector_param/setMiller ", [0, 0, 1]),
("/main/detector_param/setLatticeDeg ", 45, [45*lbf,45*ubf]),
#("/main/detector_param/setTopSourceMat ", "G4_Al"),
("/main/detector_param/setTopAbs ", 0.795, [0.795*lbf,1]),
("/main/detector_param/setTopVSound ", 3.582, [3.582*lbf,3.582*ubf]),
("/main/detector_param/setTopGap ", 191.0e-6, [(191.0e-6)*lbf,(191.0e-6)*ubf]),
("/main/detector_param/setTopQPLim ", 3, [1,5], "int"),
("/main/detector_param/setTopPhLifetime ", 0.242, [0.242*lbf,0.242*ubf]),
("/main/detector_param/setTopPhLifetimeSlope ", 0.29, [0.29*lbf,0.29*ubf]),
#("/main/detector_param/setTopSubGapAbs ", 0.0, [0.0,179.75e-6]),
("/main/detector_param/setTopPSpecProb ", 0.0, [0,1]),
#("/main/detector_param/setTopFilmSourceMat ", "G4_Nb")
("/main/detector_param/setTopFilmAbs ", 0.745, [0.745*lbf,1]),
("/main/detector_param/setTopFilmVSound ", 2.444, [2.444*lbf,2.444*ubf]),
("/main/detector_param/setTopFilmGap ", 1.5384e-3, [1.5384e-3*lbf,1.5384e-3*ubf]),
("/main/detector_param/setTopFilmQPLim ", 3, [1,5], "int"),
("/main/detector_param/setTopFilmPhLifetime ", 0.00417, [0.00417*lbf,0.00417*ubf]),
("/main/detector_param/setTopFilmPhLifetimeSlope ", 0.29, [0.29*lbf,0.29*ubf]),
#("/main/detector_param/setTopFilmSubGapAbs ", 0.0, [0,1.5384e-3]),
("/main/detector_param/setTopFilmPSpecProb ", 0.0, [0,1]),
#("/main/detector_param/setBotSourceMat ", "G4_Cu")
("/main/detector_param/setBotAbs ", 0.736, [0.736*lbf,1]),
("/main/detector_param/setBotVSound ", 2.608, [2.608*lbf,2.608*ubf]),
#("/main/detector_param/setBotGap ", 0.0, [0.0,0.0]),
("/main/detector_param/setBotGapThres ", 180e-6, [180e-6*lbf,180e-6*ubf]),
("/main/detector_param/setBotQPLim ", 2, [1,5], "int"),
("/main/detector_param/setBotPhLifetime ", 5.1, [5.1*lbf,5.1*ubf]),
#("/main/detector_param/setBotPhLifetimeSlope ", 5.3, [5.3*lbf,5.3*ubf]),
#("/main/detector_param/setBotSubGapAbs ", 0.0, [0.0,0.0]),
("/main/detector_param/setBotPSpecProb ", 0.0, [0,1]),
("/main/detector_param/setWallAbs ", 0.2, [0.0,0.5]),
("/main/detector_param/setWallPSpecProb ", 0.0, [0,1]),
#"/main/detector_param/setBotNormal ",
# Microstructure Parameters:
#"/main/detector_param/setHoleDepth ",
#"/main/detector_param/setHoleRadius ",
#"/main/detector_param/setHoleSpacing ",
#"/main/detector_param/setChannelDepth ",
#"/main/detector_param/setMitigation ",
#"/main/detector_param/setRandomHoleMitigation ",
#"/main/detector_param/setHoleXLoc ",
#"/main/detector_param/setHoleYLoc ",
#"/main/detector_param/setHoleRadiusList ",
#"/main/detector_param/setBoxXLoc ",
#"/main/detector_param/setBoxYLoc ",
#"/main/detector_param/setBoxHeights ",
#"/main/detector_param/setBoxWidths ",
#"/main/detector_param/setBoxAngles ",
#"/main/detector_param/setBitMapSize ",
#"/main/detector_param/setBitMapVals ",
]

#G4CMP prameters. Format is (macro command, default value, range, unit (optional)).
G4CMP_params = [
#"/g4cmp/LatticeData ",
#"/g4cmp/verbose ",
("/g4cmp/clearance ", 1e-06, [1,10], "e-6 mm"),
#"/g4cmp/voltage ",
#"/g4cmp/EPotFile ",
#"/g4cmp/scaleEPot ",
#"/g4cmp/minimumStep ",
# ("/g4cmp/chargeBounces ", 1, [1,100], "int"),
("/g4cmp/phononBounces ", 1000, [1,10000], "int"),
#("/g4cmp/producePhonons ", 1),
#("/g4cmp/produceCharges ", 1),
#("/g4cmp/sampleLuke ", 1),
#("/g4cmp/maxLukePhonons ", -1),
#("/g4cmp/samplingEnergy ", -1),
#("/g4cmp/combiningStepLength ", 0.0), # mm
("/g4cmp/minEPhonons ", 0.000382, [0.000382*lbf,0.000382*ubf], " eV"),
#("/g4cmp/minECharges ", 0.0), # eV
#("/grcmp/recordMinETracks ", 1),
#("/g4mcp/useKVsolver ", 0),
#("/g4cmp/enableFanoStatistics ", 1),
#("/g4cmp/kaplanKeepPhonons ", 1),
#("/g4cmp/IVRateModel ", "Quadratic"),
# ("/g4cmp/eTrappingMFP ", 0.3, [0.1,0.5], " mm"),
# ("/g4cmp/hTrappingMFP ", 0.3, [0.1,0.5], " mm"),
#("/g4cmp/eDTrapIonizationMFP ", 1.79769e+308, [0,np.sqrt(2)*10], " mm"),
#("/g4cmp/eATrapIonizationMFP ", 1.79769e+308, [0,np.sqrt(2)*10], " mm"),
#("/g4cmp/hDTrapIonizationMFP ", 1.79769e+308, [0,np.sqrt(2)*10], " mm"),
#("/g4cmp/hATrapIonizationMFP ", 1.79769e+308, [0,np.sqrt(2)*10], " mm"),
("/g4cmp/temperature ", 0.0, [0.0, 0.2], " K"),

#("/g4cmp/NIELPartition ", "19G4CMPLewinSmithNIEL"),
#"/g4cmp/createChargeCloud ",
#"/g4cmp/orientation ",
#"/g4cmp/HitsFile ",
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

#Parameters for modeling the quasiparticle differential equation
QPDE_params = [
#("AlGap", 44e9, [44e9*lbf,44e9*ubf]), # Use /main/detector_param/setTopGap as parameter in ODE.
("f_01", [4.62e9,4.30e9,4.32e9,4.36e9,4.34e9,4.32e9], [[4.62e9*lbf,4.3e9*ubf], [4.30e9*lbf,4.30e9*ubf], [4.32e9*lbf,4.32e9*ubf], [4.36e9*lbf,4.36e9*ubf], [4.34e9*lbf,4.34e9*ubf], [4.32e9*lbf,4.32e9*ubf]]),
#("f_01", 4.30e9, [4.30e9*lbf,4.30e9*ubf]),
("r", 25e-6, [25e-6*lbf,25e-6*ubf]),
("s", [0.0543,0.0693,0.0634,0.0342,0.0512,0.0342], [[0.01,0.1], [0.01,0.1], [0.01,0.1], [0.01,0.1], [0.01,0.1], [0.01,0.1]]),
#("s", 0.05, [0.01,0.1]),
("I_ph", 1.673*8.916e5, [1.673*8.916e5*lbf,1.673*8.916e5*ubf]),
("pt", 150, [150*lbf,150*ubf]),
("n_cooper",4e6,[4e6*lbf,4e6*ubf])
]

#Define the set for all parameters
all_params = electrode_params + detector_params + G4CMP_params + config_params + QPDE_params
print("Number of parameter groups:", len(all_params))
#Define the set of parameters for the macro
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
Morris_Setup ={
    'num_vars': VNUM,
    'names': names,
    'bounds': bounds,
}

samples = np.round(morris_samp.sample(Morris_Setup, 128, num_levels=4),6)
print("Morris sample shape:", samples.shape, len(samples[0]))
TOTAL_SAMPLES = samples.shape[0]
print_startup_configuration()

def replace_line(lines, start_string, new_line, allow_commented=False, append_if_missing=False):
    command = start_string.strip()

    def matches(candidate):
        if not candidate.startswith(command):
            return False
        return len(candidate) == len(command) or candidate[len(command)].isspace()

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#") and matches(stripped):
            lines[i] = new_line + '\n'
            return lines

    if allow_commented:
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("#") and matches(stripped[1:].lstrip()):
                lines[i] = new_line + '\n'
                return lines

    if append_if_missing:
        lines.append(new_line + '\n')

    return lines

with open(os.path.join(RESULTS_DIR, "MorrisSequence.csv"), 'w', newline='') as csvfile:
    spamwriter = csv.writer(csvfile, delimiter=',')
    spamwriter.writerow(names)
    for i in range(samples.shape[0]):
        spamwriter.writerow(samples[i,:])
csvfile.close()

def generate_runfiles(samp,samp_No):
    # Write a new macro with the sample parameters
    with open(MACRO_TEMPLATE, 'r') as file:
        lines = file.readlines()
    file.close()
    
    sample_name = "Morris_" + str(samp_No)
    hits_file = get_sample_hits_file(sample_name)
    lines = replace_line(lines,"/g4cmp/HitsFile","/g4cmp/HitsFile " + hits_file)
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
                
            lines = replace_line(lines,param[0],command_string)
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

            lines = replace_line(lines,param[0],command_string)
            
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

        lines = replace_line(lines,param[0],command_string, append_if_missing=True)
                    
    # Write config.txt here
    output_config_dir = os.path.join(get_sample_lattice_root(sample_name), LATTICE_MATERIAL)
    os.makedirs(output_config_dir, exist_ok=True)

    output_config_filename = os.path.join(output_config_dir, "config.txt")
    with open(output_config_filename, 'w') as file:
        file.writelines(lines)
    file.close()
    
    return sample_name
    
    
def run_sample(macroname):
    """
    Helper function that runs the input filename in the macrospath folder.
    """
    if not os.path.exists(MAIN_EXE):
        raise FileNotFoundError(
            "Missing Main executable: "
            + MAIN_EXE
            + ". Build it with: cd "
            + BNL_MAIN_DIR
            + " && make"
        )

    sample_name = os.path.splitext(os.path.basename(macroname))[0]
    command = (
        "set -e; "
        "set +u; "
        + "source " + shlex.quote(PATH_TO_GEANT4_ENV) + "; "
        + "unset G4CMPINSTALL G4CMPINCLUDE G4LATTICEDATA G4CMPLIB; "
        + "source " + shlex.quote(PATH_TO_G4CMP_ENV) + "; "
        + "set -u; "
        + "export G4LATTICEDATA=" + shlex.quote(get_sample_lattice_root(sample_name)) + "; "
        + "case \":${LD_LIBRARY_PATH:-}:\" in *\":"
        + G4CMPLIB
        + ":\"*) ;; *) export LD_LIBRARY_PATH="
        + shlex.quote(G4CMPLIB)
        + "${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} ;; esac; "
        + "cd " + shlex.quote(BNL_MAIN_DIR) + "; "
        + shlex.quote(MAIN_EXE) + " " + shlex.quote(macroname)
    )

    log_file = get_sample_log_file(sample_name)
    temp_log = tempfile.NamedTemporaryFile(
        mode="w",
        dir=LOGS_DIR,
        prefix=sample_name + ".",
        suffix=".logtmp",
        delete=False,
    )
    temp_log_file = temp_log.name
    keep_success_log = should_keep_success_log(sample_name)

    try:
        with temp_log:
            subprocess.run(
                ["bash", "-lc", command],
                check=True,
                stdout=temp_log,
                stderr=subprocess.STDOUT,
            )
    except Exception:
        finalize_log_file(temp_log_file, log_file, True)
        raise
    else:
        finalize_log_file(temp_log_file, log_file, keep_success_log)

    return sample_name

def main(macro_files):
    completed = 0
    start_time = time.monotonic()

    print(
        f"Starting {len(macro_files)} Morris runs with {MAX_WORKERS} workers."
    )

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        macro_iter = iter(macro_files)
        in_flight = deque()

        for _ in range(min(MAX_WORKERS, len(macro_files))):
            try:
                in_flight.append(executor.submit(run_sample, next(macro_iter)))
            except StopIteration:
                break

        while in_flight:
            future = next(as_completed(in_flight))
            in_flight.remove(future)
            sample_name = future.result()
            completed += 1

            try:
                in_flight.append(executor.submit(run_sample, next(macro_iter)))
            except StopIteration:
                pass

            if completed <= 5 or completed % PROGRESS_EVERY == 0 or completed == len(macro_files):
                elapsed = time.monotonic() - start_time
                rate = completed / elapsed if elapsed > 0 else 0.0
                remaining = len(macro_files) - completed
                eta_seconds = remaining / rate if rate > 0 else 0.0
                print(
                    f"Completed {completed}/{len(macro_files)} runs; "
                    f"latest={sample_name}; "
                    f"elapsed={format_duration(elapsed)}; "
                    f"eta={format_duration(eta_seconds)}"
                )
#    for mf in macro_files:
#        run_sample(mf)

if __name__ == "__main__":
    macro_files = []
    generation_start = time.monotonic()
    print(f"Generating {TOTAL_SAMPLES} Morris macros/configs.")
    for s, samp in enumerate(samples):
        sample_name = generate_runfiles(samp,s)
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
