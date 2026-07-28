"""
Single source of truth for the Morris sensitivity-analysis parameter
definitions (macro command, default value, sampling bounds, unit), shared by
every stage-1 "physics simulation" runner:

- SensitivityAnalysis_Morris_debug_mac_QP.py (serial)
- SensitivityAnalysis_Morris_Mac_M4Max.py (parallel)

Both scripts import these lists instead of defining their own copies, so
they can't silently drift apart and stop simulating the same physics. Each
script still builds its own `all_params`/`macro_params`/Morris design from
these lists -- this module only holds the parameter data itself.

Each parameter entry is a tuple: (macro command, default value, bounds, unit
(optional)). A 3-element tuple has no unit; a 4-element tuple's last item is
either a G4 unit suffix string (e.g. " um") or the literal "int" to round
the sampled value to an integer. A list-valued default/bounds means the
command takes a vector (each element sampled independently).

Commented-out entries are macro commands that are supported but not
currently swept -- kept visible here as the reference for what could be
added to the Morris design.
"""

lbf = 0.5  # lower bound factor
ubf = 1.5  # upper bound factor

# The three QPLim parameters below are swept over [2, 5], not [1, 5]. A film
# with lowQPLimit = 1 makes G4CMPKaplanQP::AbsorbPhonon loop forever the first
# time a phonon breaks a pair there: the phonon-emission sampler's floor is
# strictly above the gap energy, so the survival test qpE >= 1*gapEnergy can
# never fail and the quasiparticle is immortal. Measured consequences (see
# G4CMP_crash_and_memory_analysis.md, Finding 1): the sample hangs at 100% CPU
# and can grow to tens of GB of RSS; at 1e6 events the trigger probability for
# an at-risk sample is ~1. QPLim = 1 is not a physically meaningful operating
# point in this model anyway -- it asks quasiparticles to radiate down to
# exactly the gap, which the sampler cannot reach by construction.
# Do not lower these back to 1 without first fixing G4CMPKaplanQP upstream.

# Detector electrode parameters. Format is (macro command, default value, range, unit (optional)).
electrode_params = [
# The sensitivity template uses phonon_Caustic; GeV energies produce invalid phonon tracks.
#
# Gun energy is swept over [0.6, 1.5] meV, NOT +/-50% of 2*Delta_Al as before.
# Rationale (see Stage3 screening protocol):
#   * Lower bound 0.6 meV = 2*Delta_Al at the TOP of the setTopGap sweep
#     (2 * 191 ueV * 1.5 = 573 ueV). Below this, samples that draw a high
#     setTopGap are structurally dead: a phonon with hbar*w < 2*Delta cannot
#     break a Cooper pair, deposits nothing, and yields no hit at all. The old
#     range [191, 573] ueV put 2 of 4 Morris levels under that gate, which is
#     why ~47% of the previous design simulated nothing.
#   * Upper bound 1.5 meV is set by phonon transport, not by the gap. Both
#     scattering rates are steep power laws in this lattice
#     (Gamma_anh ~ w^5 via `decay`, Gamma_iso ~ w^4 via `scat`), so the
#     isotope mean free path collapses fast: 695 um at 1.0 meV, 137 um at
#     1.5 meV, 43 um at 2.0 meV, 8.6 um at 3.0 meV. Against a 525 um
#     substrate, anything above ~1.5-2 meV thermalizes before it can cross the
#     chip, so the run stops measuring transport-to-the-qubit (and the
#     phonon_Caustic gun, a ballistic construct, stops meaning anything).
# The gate on QP production is 2*Delta, NOT /g4cmp/minEPhonons -- lowering
# minEPhonons alone does not revive a below-gap sample.
("/main/gun/setEnergy ", 1.0e-3, [0.6e-3, 1.5e-3], " eV"),
("/main/electrode_param/setHeight ", 10, [10 * lbf, 10 * ubf]),
("/main/electrode_param/setWidth ", 10, [10 * lbf, 10 * ubf]),
#("/main/electrode_param/setXLocations ", 0, [-3.98,3.98]),
#("/main/electrode_param/setYLocations ", 0, [-3.98,3.98]),
# CAUTION -- these two are NOT qubit-electrode dimensions, despite living under
# /main/electrode_param/ alongside setWidth/setHeight. They are consumed only by
# WaffleKaplanElectrode, which PhononDetectorConstruction attaches to
# botSurfProp / botSCSurfProp -- the BOTTOM surface. They define the backside
# normal-metal (Cu) "waffle" absorber pattern: a phonon is absorbed only when it
# lands on an island, with
#     l_cell = l_island + l_spacing,  coverage ~ (l_island / l_cell)^2.
# setWidth/setHeight, by contrast, are used only by JunctionKaplanElectrode on
# the TOP surface and ARE the qubit junction dimensions (WaffleKaplanElectrode
# references neither).
# Consequence for interpretation: these are backside-mitigation fabrication
# knobs (coverage and pitch), not a degenerate "make the qubit smaller" lever.
# The measured signs agree -- larger islands lower QPs (mean EE -0.57), wider
# spacing raises them (+0.60). Prefer reparameterising as coverage fraction and
# pattern pitch for optimisation.
("/main/electrode_param/setIsland ", 200, [200 * lbf, 200 * ubf], " um"),
("/main/electrode_param/setIslandSpacing ", 50, [50 * lbf, 50 * ubf], " um")
#("/main/electrode_param/setGapThres ", 0.0, [0.0,0.0003595/2]),
]

# Detector geometry/material parameters. Format is (macro command, default value, range, unit (optional)).
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
("/main/detector_param/setTopQPLim ", 3, [2, 5], "int"),
("/main/detector_param/setTopPhLifetime ", 0.242, [0.242 * lbf, 0.242 * ubf]),
("/main/detector_param/setTopPhLifetimeSlope ", 0.29, [0.29 * lbf, 0.29 * ubf]),
#("/main/detector_param/setTopSubGapAbs ", 0.0, [0.0,179.75e-6]),
("/main/detector_param/setTopPSpecProb ", 0.0, [0, 1]),
#("/main/detector_param/setTopFilmSourceMat ", "G4_Nb")
("/main/detector_param/setTopFilmAbs ", 0.745, [0.745 * lbf, 1]),
("/main/detector_param/setTopFilmVSound ", 2.444, [2.444 * lbf, 2.444 * ubf]),
("/main/detector_param/setTopFilmGap ", 1.5384e-3, [1.5384e-3 * lbf, 1.5384e-3 * ubf]),
("/main/detector_param/setTopFilmQPLim ", 3, [2, 5], "int"),
("/main/detector_param/setTopFilmPhLifetime ", 0.00417, [0.00417 * lbf, 0.00417 * ubf]),
("/main/detector_param/setTopFilmPhLifetimeSlope ", 0.29, [0.29 * lbf, 0.29 * ubf]),
#("/main/detector_param/setTopFilmSubGapAbs ", 0.0, [0,1.5384e-3]),
("/main/detector_param/setTopFilmPSpecProb ", 0.0, [0, 1]),
#("/main/detector_param/setBotSourceMat ", "G4_Cu")
("/main/detector_param/setBotAbs ", 0.736, [0.736 * lbf, 1]),
("/main/detector_param/setBotVSound ", 2.608, [2.608 * lbf, 2.608 * ubf]),
#("/main/detector_param/setBotGap ", 0.0, [0.0,0.0]),
("/main/detector_param/setBotGapThres ", 180e-6, [180e-6 * lbf, 180e-6 * ubf]),
("/main/detector_param/setBotQPLim ", 2, [2, 5], "int"),
("/main/detector_param/setBotPhLifetime ", 5.1, [5.1 * lbf, 5.1 * ubf]),
#("/main/detector_param/setBotPhLifetimeSlope ", 5.3, [5.3*lbf,5.3*ubf]),
#("/main/detector_param/setBotSubGapAbs ", 0.0, [0.0,0.0]),
("/main/detector_param/setBotPSpecProb ", 0.0, [0, 1]),
("/main/detector_param/setWallAbs ", 0.2, [0.0, 0.5]),
("/main/detector_param/setWallPSpecProb ", 0.0, [0, 1]),
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
("/g4cmp/clearance ", 1e-06, [1, 10], "e-6 mm"),
#"/g4cmp/voltage ",
#"/g4cmp/EPotFile ",
#"/g4cmp/scaleEPot ",
#"/g4cmp/minimumStep ",
# ("/g4cmp/chargeBounces ", 1, [1,100], "int"),
# phononBounces is pinned to 1000 (not swept); written as a fixed value via
# PINNED_G4CMP_COMMANDS in each script's generate_runfiles().
# ("/g4cmp/phononBounces ", 1000, [1,10000], "int"),
#("/g4cmp/producePhonons ", 1),
#("/g4cmp/produceCharges ", 1),
#("/g4cmp/sampleLuke ", 1),
#("/g4cmp/maxLukePhonons ", -1),
#("/g4cmp/samplingEnergy ", -1),
#("/g4cmp/combiningStepLength ", 0.0), # mm
# minEPhonons is pinned to 0.000382 eV (not swept); written as a fixed value via
# PINNED_G4CMP_COMMANDS in each script's generate_runfiles().
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
("/g4cmp/temperature ", 0.0, [0.0, 0.2], " K"),
#("/g4cmp/NIELPartition ", "19G4CMPLewinSmithNIEL"),
#"/g4cmp/createChargeCloud ",
#"/g4cmp/orientation ",
#"/g4cmp/HitsFile ",
]

# Fixed (non-swept) G4CMP macro overrides applied by every stage-1 script's
# generate_runfiles(), regardless of what the macro template currently says.
# Kept here -- not just in G4CMP_params' comments above -- so the pinned
# values themselves have one canonical source too.
PINNED_G4CMP_COMMANDS = (
    ("/g4cmp/phononBounces ", "/g4cmp/phononBounces 1000"),
    # Lowered 0.000382 -> 0.0000382 eV (2026-07-27). minEPhonons is a numerical
    # track-culling cut, and at 382 ueV it sat exactly at 2*Delta_Al and ABOVE
    # the lowest physical threshold anywhere in the sweep (2*Delta_Al = 191 ueV
    # at the bottom of the setTopGap range; setBotGapThres reaches 90 ueV). A
    # numerical cut must never preempt a physical one, or the down-conversion
    # cascade is truncated before the physics decides the phonon's fate.
    # 38.2 ueV sits below every physical threshold in the design, but NOT by an
    # order of magnitude: it is 5.0x below the minimum Al pair-breaking
    # threshold (2*Delta_Al = 191 ueV) and 2.4x below the minimum
    # setBotGapThres (90 ueV). The ordering is what matters -- a numerical cut
    # must never preempt a physical one -- but the margin has NOT been
    # established by a cutoff-convergence test, so it remains an assumption.
    # It costs CPU (more harmless low-energy tracks are followed) and
    # buys correctness insurance; measured cost is acceptable at ~25 s / 1e6.
    ("/g4cmp/minEPhonons ", "/g4cmp/minEPhonons 0.0000382 eV"),
)

# G4CMP config.txt parameters. Format is (macro command, default value, range, unit (optional)).
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
# vtrans is swept as the RATIO v_T/v_L, not as an absolute velocity, and the
# absolute value is reconstructed as ratio * vsound when the config is written.
#
# Sweeping the two velocities independently over +/-50% lets vtrans exceed
# vsound (their ranges overlap on [4500, 8100] m/s), which is not a slow
# crystal -- it is not a crystal at all. For any mechanically stable cubic
# solid v_T/v_L = sqrt(C44/C11) < 1 along [100]. NOTE: this is an acoustic-mode
# / G4CMP requirement, NOT a Born stability criterion -- the cubic Born
# conditions are C11 > |C12|, C11 + 2*C12 > 0 and C44 > 0, and none of them
# implies C11 > C44. C44 < C11 holds for Si and for most cubic crystals
# empirically, and G4CMP's group-velocity map assumes it, but it is not a
# stability requirement.
# 17.3% of the previous T=128 design violated this.
#
# G4CMP does not tolerate it: G4LatticeLogical::LookupKtoVg builds the
# group-velocity map assuming v_L > v_T, and an inverted crystal indexes off
# the end of that table and SIGSEGVs. Measured on the aborted 2026-07-27
# screen: vtrans > vsound predicted the crash with perfect separation (31/31
# crashed vs 0/30, Fisher exact p = 4.3e-18). The crash is stochastic per
# event (p ~ 6e-5 for an affected point), so at 125k events per sub-run every
# sub-run of an affected design point dies and the point yields no data at all.
#
# The ratio parameterisation makes the invalid region unreachable by
# construction rather than filtering it after the fact, so no design point is
# lost and the Morris trajectory structure stays intact. Range [0.3, 0.9]
# brackets the Si default (5400/9000 = 0.6) and stays strictly below 1.
("vtrans ", 0.6, [0.3, 0.9], "ratio:vsound "),
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
