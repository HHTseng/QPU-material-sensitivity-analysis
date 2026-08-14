"""
Single source of truth for the active Morris sensitivity-analysis parameters
and the fixed settings used by stage1_run_simulations.py.

Each parameter entry is a tuple: (macro command, default value, bounds, unit
(optional)). A 3-element tuple has no unit; a 4-element tuple's last item is
either a G4 unit suffix string (e.g. " um") or the literal "int" to round
the sampled value to an integer. A list-valued default/bounds means the
command takes a vector (each element sampled independently).

Commented-out entries are supported commands that are not independently
swept. Fixed commands are enforced through FIXED_MACRO_COMMANDS and
FIXED_CONFIG_COMMANDS so a template cannot silently override them.

This file implements parameter_optimization/parameter_set.txt. The current
runner still uses a Si Geant4 substrate (rho=2329 kg/m^3). Consequently,
substrate vsound/vtrans are dependent values derived by Stage 1 from the
sampled cubic stiffness tensor and that fixed density. The three interface
absorption probabilities are also dependent in the intended material model,
but the promised transmission-coefficient calculator and candidate-material
densities are not present yet; they therefore remain at the documented Si
baseline in the macro templates and are not independent Morris dimensions.
"""

lbf = 0.5  # lower bound factor
ubf = 1.5  # upper bound factor

# QPLim is fixed at 3, never 1. A film
# with lowQPLimit = 1 makes G4CMPKaplanQP::AbsorbPhonon loop forever the first
# time a phonon breaks a pair there: the phonon-emission sampler's floor is
# strictly above the gap energy, so the survival test qpE >= 1*gapEnergy can
# never fail and the quasiparticle is immortal. Measured consequences (see
# G4CMP_crash_and_memory_analysis.md, Finding 1): the sample hangs at 100% CPU
# and can grow to tens of GB of RSS; at 1e6 events the trigger probability for
# an at-risk sample is ~1. QPLim = 1 is not a physically meaningful operating
# point in this model anyway -- it asks quasiparticles to radiate down to
# exactly the gap, which the sampler cannot reach by construction.
# Do not lower the fixed values to 1 without first fixing G4CMPKaplanQP upstream.

# Detector electrode parameters. Format is (macro command, default value, range, unit (optional)).
electrode_params = [
# The sensitivity template uses phonon_Caustic; GeV energies produce invalid phonon tracks.
# Gun energy is an INJECTION CONDITION, not a material property, so Stage 3 must
# hold it fixed for every candidate; it is enforced through FIXED_MACRO_COMMANDS.
# It is also no longer 382 ueV -- see the FIXED_MACRO_COMMANDS entry for why.
# ("/main/gun/setEnergy ", 2 * 191.0e-6, [(2 * 191.0e-6) * lbf, (2 * 191.0e-6) * ubf], " eV"),
("/main/electrode_param/setHeight ", 10, [10 * lbf, 10 * ubf]),
("/main/electrode_param/setWidth ", 10, [10 * lbf, 10 * ubf]),
#("/main/electrode_param/setXLocations ", 0, [-3.98,3.98]),
#("/main/electrode_param/setYLocations ", 0, [-3.98,3.98]),
("/main/electrode_param/setIsland ", 200, [200 * lbf, 200 * ubf], " um"),
("/main/electrode_param/setIslandSpacing ", 50, [50 * lbf, 50 * ubf], " um")
#("/main/electrode_param/setGapThres ", 0.0, [0.0,0.0003595/2]),
]

# Detector geometry/material parameters. Format is (macro command, default value, range, unit (optional)).
detector_params = [
# Film thicknesses are fixed; see FIXED_MACRO_COMMANDS.
# ("/main/detector_param/setTopThickness ", 0.12, [0.12 * lbf, 0.12 * ubf], " um"),
# ("/main/detector_param/setTopFilmThickness ", 0.075, [0.075 * lbf, 0.075 * ubf], " um"),
# ("/main/detector_param/setBotThickness ", 1, [1 * lbf, 1 * ubf], " um"),
#("/main/detector_param/setSubThickness ", 525, [525*lbf,525*ubf]),
#("/main/detector_param/setSubWidth ", 8, [8*lbf,8*ubf]),
#("/main/detector_param/setSubHeight ", 8, [8*lbf,8*ubf]),
#"/main/detector_param/setSubstrateName ",
#"/main/detector_param/setSubstrateG4Name ",
#("/main/detector_param/setMiller ", [0, 0, 1]),
("/main/detector_param/setLatticeDeg ", 45, [45 * lbf, 45 * ubf]),
#("/main/detector_param/setTopSourceMat ", "G4_Al"),
# setTopAbs is a dependent interface-transmission value, held at its
# template baseline until the material transmission calculator is supplied.
# ("/main/detector_param/setTopAbs ", 0.795, [0.795 * lbf, 1]),
# Aluminum junction properties are fixed; see FIXED_MACRO_COMMANDS.
# ("/main/detector_param/setTopVSound ", 3.582, [3.582 * lbf, 3.582 * ubf]),
# ("/main/detector_param/setTopGap ", 191.0e-6, [(191.0e-6) * lbf, (191.0e-6) * ubf]),
# ("/main/detector_param/setTopQPLim ", 3, [2, 5], "int"),
# ("/main/detector_param/setTopPhLifetime ", 0.242, [0.242 * lbf, 0.242 * ubf]),
# ("/main/detector_param/setTopPhLifetimeSlope ", 0.29, [0.29 * lbf, 0.29 * ubf]),
#("/main/detector_param/setTopSubGapAbs ", 0.0, [0.0,179.75e-6]),
# ("/main/detector_param/setTopPSpecProb ", 0.8, [0, 1]),
#("/main/detector_param/setTopFilmSourceMat ", "G4_Nb")
# Dependent interface-transmission value; see setTopAbs above.
# ("/main/detector_param/setTopFilmAbs ", 0.745, [0.745 * lbf, 1]),
("/main/detector_param/setTopFilmVSound ", 2.444, [2.444 * lbf, 2.444 * ubf]),
("/main/detector_param/setTopFilmGap ", 1.5384e-3, [1.5384e-3 * lbf, 1.5384e-3 * ubf]),
# ("/main/detector_param/setTopFilmQPLim ", 3, [2, 5], "int"),
("/main/detector_param/setTopFilmPhLifetime ", 0.00417, [0.00417 * lbf, 0.00417 * ubf]),
# ("/main/detector_param/setTopFilmPhLifetimeSlope ", 0.29, [0.29 * lbf, 0.29 * ubf]),
#("/main/detector_param/setTopFilmSubGapAbs ", 0.0, [0,1.5384e-3]),
# ("/main/detector_param/setTopFilmPSpecProb ", 0.8, [0, 1]),
#("/main/detector_param/setBotSourceMat ", "G4_Cu")
# Dependent interface-transmission value; see setTopAbs above.
# ("/main/detector_param/setBotAbs ", 0.736, [0.736 * lbf, 1]),
("/main/detector_param/setBotVSound ", 2.608, [2.608 * lbf, 2.608 * ubf]),
# A [0, 0] interval is not optimizable; keep the normal-metal gap fixed at 0.
# ("/main/detector_param/setBotGap ", 0.0, [0.0, 0.0]),
("/main/detector_param/setBotGapThres ", 180e-6, [180e-6 * lbf, 180e-6 * ubf]),
# ("/main/detector_param/setBotQPLim ", 3, [2, 5], "int"),
("/main/detector_param/setBotPhLifetime ", 5.1, [5.1 * lbf, 5.1 * ubf]),
#("/main/detector_param/setBotPhLifetimeSlope ", 5.3, [5.3*lbf,5.3*ubf]),
#("/main/detector_param/setBotSubGapAbs ", 0.0, [0.0,0.0]),
# ("/main/detector_param/setBotPSpecProb ", 0.8, [0, 1]),
# ("/main/detector_param/setWallAbs ", 0.02, [0.0, 0.5]),
# ("/main/detector_param/setWallPSpecProb ", 0.0, [0, 1]),
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

# G4CMP parameters. Format is (macro command, default value, range, unit (optional)).
G4CMP_params = [
#"/g4cmp/LatticeData ",
#"/g4cmp/verbose ",
# ("/g4cmp/clearance ", 1e-06, [1, 10], "e-6 mm"),
#"/g4cmp/voltage ",
#"/g4cmp/EPotFile ",
#"/g4cmp/scaleEPot ",
#"/g4cmp/minimumStep ",
# ("/g4cmp/chargeBounces ", 1, [1,100], "int"),
# phononBounces is fixed at 10000 (not swept); see FIXED_MACRO_COMMANDS.
# ("/g4cmp/phononBounces ", 10000, [1,10000], "int"),
#("/g4cmp/producePhonons ", 1),
#("/g4cmp/produceCharges ", 1),
#("/g4cmp/sampleLuke ", 1),
#("/g4cmp/maxLukePhonons ", -1),
#("/g4cmp/samplingEnergy ", -1),
#("/g4cmp/combiningStepLength ", 0.0), # mm
# minEPhonons is fixed at 0.0000382 eV (38.2 ueV, not swept); see FIXED_MACRO_COMMANDS.
# ("/g4cmp/minEPhonons ", 0.000382, [0.000382*lbf,0.000382*ubf], " eV"),
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
("/g4cmp/temperature ", 0.0, [0.0, 0.1], " K"),
#("/g4cmp/NIELPartition ", "19G4CMPLewinSmithNIEL"),
#"/g4cmp/createChargeCloud ",
#"/g4cmp/orientation ",
#"/g4cmp/HitsFile ",
]

# Fixed (non-swept) overrides applied by Stage 1 regardless of the selected
# macro template. Values come from parameter_optimization/parameter_set.txt.
FIXED_MACRO_COMMANDS = (
    # --- Injection scenario (energy protocol, adopted 2026-08-12) -------------
    # Gun energy 10 meV and minEPhonons 38.2 ueV replace the previous
    # 382 ueV / 382 ueV pair. At the old setting the primary sat EXACTLY on the
    # Al pair-breaking gate (2*Delta_Al = 2*191 ueV = 382 ueV, and
    # JunctionKaplanElectrode requires PhEnergy >= 2*GapJunc), while
    # minEPhonons killed every downconversion daughter at the first decay -- so
    # the phonon cascade was not simulated at all. That suppresses exactly the
    # mechanism Stage 3 optimizes: scat, decay, decayTT and the elastic tensor
    # act THROUGH the cascade.
    #
    # 10 meV is the largest phonon energy produced in the muon-strike
    # simulations, so it is the physically motivated injection energy for this
    # objective. It is 26x the 382 ueV junction pair-breaking gate. It is also
    # ABOVE 2*setTopFilmGap, which is deliberate and not a violation: the
    # Junction hit type records only junction absorptions, so the objective
    # stays junction-only (measured: 68/68 recorded hits inside junction
    # footprints at 10 meV). The ground plane becomes an active competitor for
    # phonons, which is a regime to hold constant across candidates, not a fault.
    # Measured at 10 meV: 3.9e-4 QPs per primary event, ~10x the 1 meV rate. See
    # parameter_optimization/STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md sec 12.5.
    #
    # minEPhonons 38.2 ueV also restores the value the v0 template shipped with;
    # 382 ueV was a later change that put a numerical cut above the physical one.
    # If setTopGap is ever unfrozen, recompute BOTH: the gate is 2*setTopGap.
    ("/main/gun/setEnergy ", "/main/gun/setEnergy 10.0e-3 eV"),
    ("/main/detector_param/setTopThickness ", "/main/detector_param/setTopThickness 0.12 um"),
    ("/main/detector_param/setTopFilmThickness ", "/main/detector_param/setTopFilmThickness 0.075 um"),
    ("/main/detector_param/setBotThickness ", "/main/detector_param/setBotThickness 1 um"),
    ("/main/detector_param/setTopVSound ", "/main/detector_param/setTopVSound 3.582"),
    ("/main/detector_param/setTopGap ", "/main/detector_param/setTopGap 191.0e-6"),
    ("/main/detector_param/setTopQPLim ", "/main/detector_param/setTopQPLim 3"),
    ("/main/detector_param/setTopPhLifetime ", "/main/detector_param/setTopPhLifetime 0.242"),
    ("/main/detector_param/setTopPhLifetimeSlope ", "/main/detector_param/setTopPhLifetimeSlope 0.29"),
    ("/main/detector_param/setTopFilmPSpecProb ", "/main/detector_param/setTopFilmPSpecProb 0.8"),
    ("/main/detector_param/setTopPSpecProb ", "/main/detector_param/setTopPSpecProb 0.8"),
    ("/main/detector_param/setWallPSpecProb ", "/main/detector_param/setWallPSpecProb 0"),
    ("/main/detector_param/setBotPSpecProb ", "/main/detector_param/setBotPSpecProb 0.8"),
    ("/main/detector_param/setTopFilmQPLim ", "/main/detector_param/setTopFilmQPLim 3"),
    ("/main/detector_param/setBotQPLim ", "/main/detector_param/setBotQPLim 3"),
    ("/main/detector_param/setWallAbs ", "/main/detector_param/setWallAbs 0.02"),
    ("/main/detector_param/setTopFilmPhLifetimeSlope ", "/main/detector_param/setTopFilmPhLifetimeSlope 0.29"),
    ("/main/detector_param/setBotGap ", "/main/detector_param/setBotGap 0.0"),
    ("/g4cmp/chargeBounces ", "/g4cmp/chargeBounces 1"),
    ("/g4cmp/phononBounces ", "/g4cmp/phononBounces 10000"),
    ("/g4cmp/minEPhonons ", "/g4cmp/minEPhonons 0.0000382 eV"),
    ("/g4cmp/clearance ", "/g4cmp/clearance 1e-6 mm"),
    ("/g4cmp/eTrappingMFP ", "/g4cmp/eTrappingMFP 0.3 mm"),
    ("/g4cmp/hTrappingMFP ", "/g4cmp/hTrappingMFP 0.3 mm"),
)

# G4CMP config.txt parameters. Format is (macro command, default value, range, unit (optional)).
config_params = [
# Crystal parameters
("cubic ", 5.431, [5.431*lbf, 5.431*ubf], " Ang"),
("stiffness 1 1 ", 165.6, [165.6*lbf, 165.6*ubf], " GPa"),
("stiffness 1 2 ",  63.9, [63.9*lbf, 63.9*ubf], " GPa"),
("stiffness 4 4 ",  79.5, [79.5*lbf, 79.5*ubf], " GPa"),
# Phonon parameters
# Third-order stiffness is fixed; see FIXED_CONFIG_COMMANDS.
# ("dyn ", [-42.9, -94.5, 52.4, 68.0], [[-42.9*ubf,-42.9*lbf], [-94.5*ubf,-94.5*lbf], [52.4*lbf,52.4*ubf], [68.0*lbf,68.0*ubf]], " GPa"),
("scat ", 2.43e-42, [(2.43)*lbf,(2.43)*ubf], "e-42 s3"),
("decay ", 7.41e-56, [(7.41)*lbf,(7.41)*ubf], "e-56 s4"),
("decayTT ", 0.74, [0.74*lbf,1]),
# From S. Tamura et al., PRB44(7), 1991
#("LDOS ",  0.093), #Need to figure out how to vary while maintaining LDOS + STDOS + FTDOS = 1
#("STDOS ", 0.531),
#("FTDOS ", 0.376),
# Debye is checked per candidate but is not an optimization dimension.
# ("Debye ", 15, [15*lbf,15*ubf], " THz"),
# Charge carrier parameters
# ("bandgap ", 1.17, [1.17*lbf,1.17*ubf], " eV"),
# ("pairEnergy ", 3.81, [3.81*lbf,3.81*ubf], " eV"),
# ("fanoFactor ", 0.15, [0.15*lbf,0.15*ubf]),
# Derived by Stage 1 from C11/C12/C44 and SUBSTRATE_DENSITY_KG_M3.
# ("vsound ", 9000, [9000*lbf,9000*ubf], " m/s"),
# ("vtrans ", 5400, [5400*lbf,5400*ubf], " m/s"),
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

# The current Geant4 material remains G4_Si. Stage 3 must replace this with the
# selected candidate's density before evaluating non-Si materials.
SUBSTRATE_DENSITY_KG_M3 = 2329.0

FIXED_CONFIG_COMMANDS = (
    ("dyn ", "dyn -42.9 -94.5 52.4 68.0 GPa"),
    ("Debye ", "Debye 15 THz"),
)

# Parameters for the quasiparticle decoherence-rate ODE (calculate_xQPs in
# stage2_compute_QPs.py), sourced from Paul Baity's notebook. f_01 and s are
# per-qubit vectors of length 6 in his scripts, tuned to his specific
# 6-qubit BNL device; this template's geometry has 17 electrodes instead, so
# both are kept as the scalar fallback Paul himself left commented out in
# his own parameter lists, broadcast uniformly across every electrode
# rather than invented per-electrode calibration data.
#
# stage2_compute_QPs.py expects f_01/s to be scalars -- do not change these back to
# vectors without also updating stage2_compute_QPs.py's calculate_xQPs call.
QPDE_params = [
#("AlGap", 44e9, [44e9*lbf,44e9*ubf]), # Use /main/detector_param/setTopGap as parameter in ODE.
("f_01", 4.30e9, [4.30e9*lbf,4.30e9*ubf]),
("r", 25e-6, [25e-6*lbf,25e-6*ubf]),
("s", 0.05, [0.01,0.1]),
("I_ph", 1.673*8.916e5, [1.673*8.916e5*lbf,1.673*8.916e5*ubf]),
("pt", 150, [150*lbf,150*ubf]),
("n_cooper",4e6,[4e6*lbf,4e6*ubf])
]
QPDE_PARAM_NAMES = ("f_01", "r", "s", "I_ph", "pt", "n_cooper")
