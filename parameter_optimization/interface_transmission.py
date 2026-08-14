"""Acoustic interface transmission for the substrate/film boundaries.

Implements the **baseline-calibrated effective AMM** policy of
STAGE3_SMALL_MATERIAL_START.md sec 5.1:

    A_candidate = clip(A_Si_baseline * T_candidate / T_Si_baseline, 0, 1)

with T the normal-incidence acoustic-mismatch transmission

    T = 4 Z1 Z2 / (Z1 + Z2)^2 ,   Z = density * sound_speed

Two things this is NOT, stated up front because the distinction decides what may
be claimed from a ranking:

* **It is not a first-principles interface calculation.** The crystal problem
  includes angle, polarisation, anisotropy, critical angles and mode conversion,
  and G4CMP's `filmAbsorption` is an *effective boundary probability* that need
  not equal a bare transmission coefficient. Results are model-dependent.
* **Reproducing 0.795 / 0.745 / 0.736 is not validation.** The model is
  calibrated to those numbers by construction, so recovering them tests the
  plumbing, not the physics. The two flags are reported separately:
  `baseline_reconstruction_passed` and `physics_validation_passed`, and the
  second stays False until independent interface data or an anisotropic
  calculation supports it.

Convention (declared, because mixing conventions across candidates is the error
this module exists to prevent):

* substrate side uses a Debye-like mode average `v_eff = (v_L + 2 v_T) / 3`,
  which weights the one longitudinal and two transverse branches equally;
* film side uses the single scalar sound speed that the macro already passes to
  G4CMP (`setTop/TopFilm/BotVSound`), since that is the speed the film model
  itself uses;
* densities are in kg/m^3 and speeds in m/s throughout; macro units (km/s) are
  converted explicitly at the call site, never implicitly.
"""

import math

# Si baseline absorption probabilities, one per interface family. These are the
# calibration anchors, not predictions.
SI_BASELINE_ABS = {
    "junction_Al": 0.795,
    "top_film_Nb": 0.745,
    "bottom_film_Cu": 0.736,
}

# The film each anchor was calibrated WITH. The reference transmission must be
# T(Si, baseline film) -- not T(Si, candidate film) -- or the candidate film
# cancels out of the ratio and every film on Si inherits the baseline film's
# absorption. (That was a real defect here, caught 2026-08-13: Si/Nb, Si/Ta and
# Si/Ti all returned 0.745.)
BASELINE_FILM = {
    "junction_Al":    {"g4_material": "G4_Al", "vsound_m_s": 3582.0, "density_kg_m3": 2699.0},
    "top_film_Nb":    {"g4_material": "G4_Nb", "vsound_m_s": 2444.0, "density_kg_m3": 8570.0},
    "bottom_film_Cu": {"g4_material": "G4_Cu", "vsound_m_s": 2608.0, "density_kg_m3": 8960.0},
}

# Film densities [kg/m^3]. These enter only the interface model -- G4CMP's
# phonon kinematics uses the SUBSTRATE lattice density, which comes from the
# selected G4Material. Values are the Geant4 NIST densities for the same
# material names the macros select, so the two stay consistent.
FILM_DENSITY_KG_M3 = {
    "G4_Al": 2699.0,   # fixed Al junction; not a catalog entry
}


class InterfaceModelError(ValueError):
    """Raised when an interface cannot be resolved from the supplied records."""


def substrate_effective_speed(vsound_m_s, vtrans_m_s):
    """Debye-like mode average: one longitudinal branch, two transverse."""
    if not (vsound_m_s > 0 and vtrans_m_s > 0):
        raise InterfaceModelError(
            f"non-positive substrate speeds (vsound={vsound_m_s}, vtrans={vtrans_m_s})")
    if vtrans_m_s >= vsound_m_s:
        raise InterfaceModelError(
            f"vtrans ({vtrans_m_s}) >= vsound ({vsound_m_s}); not a physical crystal "
            f"and G4CMP's group-velocity map assumes v_L > v_T")
    return (vsound_m_s + 2.0 * vtrans_m_s) / 3.0


def impedance(density_kg_m3, speed_m_s):
    if not (density_kg_m3 > 0 and speed_m_s > 0):
        raise InterfaceModelError(
            f"non-positive impedance input (rho={density_kg_m3}, v={speed_m_s})")
    return density_kg_m3 * speed_m_s


def normal_incidence_transmission(z1, z2):
    """T = 4 Z1 Z2 / (Z1 + Z2)^2. Symmetric, equals 1 for matched impedances."""
    if z1 <= 0 or z2 <= 0:
        raise InterfaceModelError(f"non-positive impedance ({z1}, {z2})")
    return 4.0 * z1 * z2 / (z1 + z2) ** 2


def interface_absorption(interface, substrate, film, si_reference):
    """Candidate absorption probability for one interface.

    interface     : key into SI_BASELINE_ABS
    substrate     : {density_kg_m3, vsound_m_s, vtrans_m_s}
    film          : {g4_material, vsound_m_s}
    si_reference  : the Si substrate record, for the calibration ratio

    Returns (absorption, diagnostics).
    """
    if interface not in SI_BASELINE_ABS:
        raise InterfaceModelError(
            f"unknown interface {interface!r}; expected one of {sorted(SI_BASELINE_ABS)}")
    g4_name = film["g4_material"]
    # Density comes from the film's own catalog record when supplied, so the
    # catalog is the single source of truth. The table below is only a fallback
    # for callers that predate catalog-carried densities.
    rho_f = film.get("density_kg_m3")
    if rho_f is None:
        rho_f = FILM_DENSITY_KG_M3.get(g4_name)
    if rho_f is None:
        raise InterfaceModelError(
            f"no density for film material {g4_name!r}: not in the catalog record "
            f"and not in the fallback table. Add it with provenance rather than "
            f"falling back to another film's value.")
    v_f = film["vsound_m_s"]
    z_film = impedance(rho_f, v_f)

    z_cand = impedance(substrate["density_kg_m3"],
                       substrate_effective_speed(substrate["vsound_m_s"],
                                                 substrate["vtrans_m_s"]))
    z_si = impedance(si_reference["density_kg_m3"],
                     substrate_effective_speed(si_reference["vsound_m_s"],
                                               si_reference["vtrans_m_s"]))

    # Reference is the calibrated BASELINE PAIR: Si substrate + the film the
    # anchor was measured with. Using the candidate film on both sides would
    # cancel the film dependence entirely.
    ref = BASELINE_FILM[interface]
    z_ref_film = impedance(ref["density_kg_m3"], ref["vsound_m_s"])

    t_cand = normal_incidence_transmission(z_cand, z_film)
    t_si = normal_incidence_transmission(z_si, z_ref_film)
    if t_si <= 0:
        raise InterfaceModelError(f"degenerate Si reference transmission for {interface}")

    absorption = SI_BASELINE_ABS[interface] * t_cand / t_si
    clipped = min(1.0, max(0.0, absorption))
    if not math.isfinite(clipped):
        raise InterfaceModelError(f"non-finite absorption for {interface}")

    return clipped, {
        "interface": interface,
        "film_material": g4_name,
        "film_density_kg_m3": rho_f,
        "film_speed_m_s": v_f,
        "Z_film": z_film,
        "Z_substrate": z_cand,
        "Z_substrate_Si": z_si,
        "Z_baseline_film": z_ref_film,
        "baseline_film": ref["g4_material"],
        "T_candidate": t_cand,
        "T_Si": t_si,
        "ratio_T": t_cand / t_si,
        "A_si_baseline": SI_BASELINE_ABS[interface],
        "A_unclipped": absorption,
        "A": clipped,
        "clipped": absorption != clipped,
        "model": "baseline_calibrated_effective_AMM",
    }


def resolve_all_interfaces(substrate, junction_film, top_film, bottom_film, si_reference):
    """All three controls for one triplet.

    Changing the substrate recomputes all three; changing one film recomputes
    only its own interface. Both behaviours are asserted in the tests.
    """
    a_top, d_top = interface_absorption("junction_Al", substrate, junction_film, si_reference)
    a_film, d_film = interface_absorption("top_film_Nb", substrate, top_film, si_reference)
    a_bot, d_bot = interface_absorption("bottom_film_Cu", substrate, bottom_film, si_reference)
    return (
        {"setTopAbs": a_top, "setTopFilmAbs": a_film, "setBotAbs": a_bot},
        {"junction": d_top, "top_film": d_film, "bottom_film": d_bot},
    )


def validation_report(si_reference, junction_film, top_film, bottom_film, tolerance=1e-9):
    """Baseline reconstruction check.

    Recovering the three constants when the substrate IS Si is a plumbing test:
    the ratio is 1 by construction. It is reported as
    `baseline_reconstruction_passed`, never as physics validation.
    """
    values, diagnostics = resolve_all_interfaces(
        si_reference, junction_film, top_film, bottom_film, si_reference)
    expected = {
        "setTopAbs": SI_BASELINE_ABS["junction_Al"],
        "setTopFilmAbs": SI_BASELINE_ABS["top_film_Nb"],
        "setBotAbs": SI_BASELINE_ABS["bottom_film_Cu"],
    }
    deltas = {k: abs(values[k] - expected[k]) for k in expected}
    passed = all(d <= tolerance for d in deltas.values())
    return {
        "baseline_reconstruction_passed": passed,
        # Stays False until independent interface data or an anisotropic
        # calculation supports the model. Calibration is not validation.
        "physics_validation_passed": False,
        "values": values,
        "expected": expected,
        "max_abs_deviation": max(deltas.values()),
        "diagnostics": diagnostics,
    }


if __name__ == "__main__":
    si = {"density_kg_m3": 2329.0, "vsound_m_s": 9018.610152, "vtrans_m_s": 5370.661726}
    ge = {"density_kg_m3": 5323.0, "vsound_m_s": 5269.5, "vtrans_m_s": 3234.4}
    al = {"g4_material": "G4_Al", "vsound_m_s": 3582.0, "density_kg_m3": 2699.0}
    nb = {"g4_material": "G4_Nb", "vsound_m_s": 2444.0, "density_kg_m3": 8570.0}
    cu = {"g4_material": "G4_Cu", "vsound_m_s": 2608.0, "density_kg_m3": 8960.0}

    report = validation_report(si, al, nb, cu)
    print("Si baseline reconstruction:")
    for key, value in report["values"].items():
        print(f"  {key:16s} = {value:.6f}  (expected {report['expected'][key]})")
    print(f"  passed={report['baseline_reconstruction_passed']} "
          f"max_dev={report['max_abs_deviation']:.2e}")
    print(f"  physics_validation_passed={report['physics_validation_passed']} "
          f"(calibration is not validation)")

    values, diag = resolve_all_interfaces(ge, al, nb, cu, si)
    print("\nGe candidate (all three recomputed because the substrate changed):")
    for key, value in values.items():
        print(f"  {key:16s} = {value:.6f}")
    print(f"  Z_Si={diag['junction']['Z_substrate_Si']:.3e}  "
          f"Z_Ge={diag['junction']['Z_substrate']:.3e}  "
          f"ratio_T(Al)={diag['junction']['ratio_T']:.4f}")
