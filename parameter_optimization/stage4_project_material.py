#!/usr/bin/env python3
"""Project a Stage 4 property target onto real materials, then re-simulate them.

    python stage4_project_material.py --manifest runs/<campaign>/campaign_bo_gp_*.json
    python stage4_project_material.py --manifest ... --verify --verify-events 4000000

The optimizer returns a property vector that need not correspond to anything
that exists. This turns it back into a fabrication statement, in three steps and
with the honesty of each step made explicit:

1. **Distance in PHYSICAL space, not decision space.** Two materials with
   different densities can behave identically, so candidates are compared on
   (v_L, v_T, anisotropy, acoustic impedance, and the phonon constants where
   they exist) computed from each material's OWN elastic tensor and density --
   never on the raw C11/C12/C44 the optimizer proposed against a fixed carrier
   density.

2. **Missing data is missing, not imputed.** A real material that has no sourced
   phonon lifetime is ranked on the features it does have, and the count of
   matched features travels with the distance. `d = 0.31 (4/7 matched)` is a
   weaker claim than `d = 0.31 (7/7)` and the table shows which one it is.

3. **A distance is not a result.** `--verify` re-simulates each finalist with
   its own measured properties and reports

       realized_fraction = (J_baseline - J_projected) / (J_baseline - J_ideal)

   i.e. how much of the ideal improvement survives projection onto something
   that exists. If that is small, the finding is "the optimum is not reachable
   with catalogued materials" -- a real and useful fabrication result, and the
   one this script exists to be able to state.
"""

import argparse
import json
import math
import os
import sys

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stage1_run_simulations as harness                        # noqa: E402
import stage4_space as S                                        # noqa: E402

POOL_PATH = os.path.join(HERE, "stage4_material_pool.yaml")
MP_PATH = os.path.join(HERE, "catalog", "substrates_raw.json")

# Which features define "close" for each layer, and how each is compared.
# `log` for anything spanning decades: a factor-two error in an isotope
# scattering constant that spans 1470x across real crystals is not the same
# size of mistake as a factor-two error in a sound speed that spans 3x.
SUBSTRATE_FEATURES = {
    "sub_vsound_m_s": "linear",
    "sub_vtrans_m_s": "linear",
    "sub_anisotropy": "linear",
    "sub_impedance": "linear",
    "sub_scat": "log",
    "sub_decay": "log",
    "sub_decayTT": "linear",
}
TOP_FILM_FEATURES = {
    "topfilm_vsound_m_s": "linear",
    "topfilm_gap_eV": "log",
    "topfilm_ph_lifetime_ns": "log",
    "topfilm_impedance": "linear",
}
BOTTOM_FILM_FEATURES = {
    "bot_vsound_m_s": "linear",
    "bot_ph_lifetime_ns": "log",
    "bot_impedance": "linear",
}


def load_pool(path=POOL_PATH):
    with open(path) as handle:
        return yaml.safe_load(handle)


def debye_speed(v_l, v_t):
    """3/v_D^3 = 1/v_L^3 + 2/v_T^3, the standard Debye mode average."""
    if not v_l or not v_t:
        return None
    return (3.0 / (1.0 / v_l ** 3 + 2.0 / v_t ** 3)) ** (1.0 / 3.0)


def film_speed(record, convention):
    """The scalar speed to compare, under a DECLARED convention.

    The project's own film speeds are inconsistent (Ta/Au longitudinal,
    Nb/Cu Debye-like), so the convention is a parameter and the report shows the
    ranking under more than one rather than hiding the choice.
    """
    v_l = record.get("vsound_longitudinal_m_s")
    v_t = record.get("vsound_transverse_m_s")
    if convention == "catalog_or_longitudinal":
        return record.get("vsound_catalog_m_s") or v_l
    if convention == "longitudinal":
        return v_l
    if convention == "debye":
        return debye_speed(v_l, v_t) or record.get("vsound_catalog_m_s")
    raise ValueError(f"unknown film speed convention {convention!r}")


# ---------------------------------------------------------------------------
# Candidate pools
# ---------------------------------------------------------------------------
def _catalog_substrate_keys():
    """Substrates the audited Stage 3 catalog can resolve natively."""
    try:
        from stage3_trial_runner import _catalog_section
        return {k for k, v in (_catalog_section("substrates") or {}).items()
                if v.get("config_mode") == "native_g4cmp" and v.get("enabled") is not False}
    except Exception:                                        # noqa: BLE001
        return set()



def substrate_pool(pool, mp_path=MP_PATH, include_mp=True, max_hull=0.05):
    """Real cubic substrates with their own (C, rho)-derived phonon behaviour.

    The MP snapshot was already filtered to cubic, non-metallic, non-magnetic,
    experimentally observed, Born-stable materials with an elastic tensor; this
    adds the stability cut and the derivation, and merges in the five materials
    that also carry a complete G4CMP lattice record (the only ones whose
    anharmonic constants are known at all).
    """
    out = []
    g4 = pool.get("g4cmp_substrates") or {}
    catalog_keys = _catalog_substrate_keys()
    for name, rec in g4.items():
        item = dict(rec)
        lattice_map = rec.get("lattice_map") or name
        try:                                     # complete record on disk?
            S.read_lattice_record(lattice_map)
            has_native = True
        except S.GateError:
            has_native = False
        item.update({"id": name, "formula": name, "origin": "g4cmp_record",
                     "material_id": None, "simulable": bool(rec.get("g4_carrier")),
                     "lattice_map": lattice_map,
                     "has_native_record": has_native,
                     "catalog_key": name if name in catalog_keys else None})
        out.append(item)
    if include_mp and os.path.isfile(mp_path):
        with open(mp_path) as handle:
            raw = json.load(handle)
        known = {r["formula"] for r in out}
        for rec in raw.get("accepted", []):
            if not (rec.get("cubic_validation") or {}).get("ok"):
                continue
            if rec.get("energy_above_hull_eV") is None or \
                    rec["energy_above_hull_eV"] > max_hull:
                continue
            if rec["formula"] in known:
                continue                     # the G4CMP record is richer; keep it
            out.append({
                "id": f"{rec['formula']} ({rec['material_id']})",
                "formula": rec["formula"], "material_id": rec["material_id"],
                "origin": "materials_project", "verified": False,
                "density_kg_m3": rec["mp_density_kg_m3"],
                "lattice_a_ang": rec["lattice_a_ang"],
                "c11_GPa": rec["c11_GPa"], "c12_GPa": rec["c12_GPa"],
                "c44_GPa": rec["c44_GPa"],
                "band_gap_eV": rec["band_gap_eV"],
                "energy_above_hull_eV": rec["energy_above_hull_eV"],
                "scat_s3": None, "decay_s4": None, "decayTT": None,
                "g4_carrier": None, "simulable": False,
                "source": f"Materials Project {rec['material_id']} "
                          f"(DFT elasticity, experimentally observed)",
            })
    feats = []
    for item in out:
        try:
            vs, vt = harness.derive_cubic_sound_speeds(
                item["c11_GPa"], item["c12_GPa"], item["c44_GPa"],
                density=item["density_kg_m3"])
        except ValueError:
            continue                                  # not a stable crystal
        if not 0 < vt < vs:
            continue
        item["features"] = {
            "sub_vsound_m_s": vs, "sub_vtrans_m_s": vt,
            "sub_anisotropy": 2.0 * item["c44_GPa"] / (item["c11_GPa"] - item["c12_GPa"]),
            "sub_impedance": item["density_kg_m3"] * (vs + 2 * vt) / 3.0,
            "sub_scat": item.get("scat_s3"), "sub_decay": item.get("decay_s4"),
            "sub_decayTT": item.get("decayTT"),
        }
        item["derived_vsound_m_s"], item["derived_vtrans_m_s"] = vs, vt
        feats.append(item)
    return feats


def film_pool(pool, kind, convention="catalog_or_longitudinal"):
    section = pool["top_films"] if kind == "top" else pool["bottom_films"]
    out = []
    for name, rec in section.items():
        v = film_speed(rec, convention)
        rho = rec.get("density_kg_m3")
        item = dict(rec)
        item["id"] = name
        item["speed_used_m_s"] = v
        item["speed_convention"] = convention
        if kind == "top":
            item["features"] = {
                "topfilm_vsound_m_s": v,
                "topfilm_gap_eV": rec.get("gap_eV"),
                "topfilm_ph_lifetime_ns": rec.get("ph_lifetime_ns"),
                "topfilm_impedance": (rho * v) if (rho and v) else None,
            }
        else:
            item["features"] = {
                "bot_vsound_m_s": v,
                "bot_ph_lifetime_ns": rec.get("ph_lifetime_ns"),
                "bot_impedance": (rho * v) if (rho and v) else None,
            }
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------
def feature_scales(candidates, feature_spec):
    """Robust spread per feature, so the metric is scale-free.

    Median absolute deviation rather than standard deviation: the pools contain
    genuine outliers (diamond-class stiffness, Au-class density) that would
    otherwise set the scale for everything else.
    """
    scales = {}
    for name, mode in feature_spec.items():
        vals = []
        for c in candidates:
            v = c["features"].get(name)
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                continue
            if mode == "log":
                if v <= 0:
                    continue
                vals.append(math.log(v))
            else:
                vals.append(float(v))
        if len(vals) < 2:
            scales[name] = 1.0
            continue
        arr = np.array(vals)
        mad = float(np.median(np.abs(arr - np.median(arr)))) * 1.4826
        scales[name] = mad if mad > 0 else (float(arr.std()) or 1.0)
    return scales


def distance(target, candidate, feature_spec, scales, weights=None):
    weights = weights or {}
    num, den, matched, gaps, per = 0.0, 0.0, 0, [], {}
    for name, mode in feature_spec.items():
        t, c = target.get(name), candidate["features"].get(name)
        if t is None or c is None:
            gaps.append(name)
            continue
        if mode == "log":
            if t <= 0 or c <= 0:
                gaps.append(name)
                continue
            z = (math.log(c) - math.log(t)) / scales[name]
        else:
            z = (float(c) - float(t)) / scales[name]
        w = float(weights.get(name, 1.0))
        num += w * z * z
        den += w
        matched += 1
        per[name] = round(z, 4)
    if matched == 0:
        return None
    return {"distance": math.sqrt(num / den), "matched": matched,
            "total": len(feature_spec), "unmatched_features": gaps, "z": per}


def rank(target_features, candidates, feature_spec, weights=None, top=10,
         stratify=True):
    """Rank by distance, STRATIFIED BY FEATURE COVERAGE (audit P4).

    A 4/7 distance and a 7/7 distance answer different questions. The old
    metric divided by the sum of MATCHED weights and then sorted everything into
    one list, so a candidate missing `scat`, `decay` and `decayTT` -- the three
    strongest optimization directions in this space -- could outrank a
    fully-characterised one purely by being unmeasured. The nearest substrates
    in STAGE4_RESULTS.md sec 4.1 were all 4/7 and were printed alongside 7/7
    rows without a visible penalty.

    Coverage now sorts FIRST (most features matched first), distance second.
    `stratify=False` restores the flat ordering for the within-stratum views
    that already control coverage themselves.
    """
    scales = feature_scales(candidates, feature_spec)
    rows = []
    for c in candidates:
        d = distance(target_features, c, feature_spec, scales, weights)
        if d is None:
            continue
        d["coverage"] = d["matched"] / d["total"] if d["total"] else 0.0
        d["stratum"] = f"{d['matched']}/{d['total']}"
        rows.append({**d, "id": c["id"], "candidate": c})
    if stratify:
        rows.sort(key=lambda r: (-r["matched"], r["distance"]))
    else:
        rows.sort(key=lambda r: (r["distance"], -r["matched"]))
    return rows[:top], scales


def strata(rows):
    """{'7/7': [...], '4/7': [...]} -- rows grouped by feature coverage.

    Exists so no table can put two coverages side by side without saying so.
    """
    out = {}
    for r in rows:
        out.setdefault(r["stratum"], []).append(r)
    for group in out.values():
        group.sort(key=lambda r: r["distance"])
    return dict(sorted(out.items(), key=lambda kv: -kv[1][0]["matched"]))


def missing_feature_report(rows, feature_spec):
    """Which features are missing, and from how many of the ranked candidates.

    The single biggest thing standing between this projection and a fabrication
    decision is that `scat`, `decay` and `decayTT` are unknown for every cubic
    material outside the shipped G4CMP records. That is a property of the input
    data, so it is reported as data coverage rather than hidden in a distance.
    """
    counts = {name: 0 for name in feature_spec}
    for r in rows:
        for name in r["unmatched_features"]:
            counts[name] = counts.get(name, 0) + 1
    return {"n_candidates": len(rows),
            "missing_counts": {k: v for k, v in sorted(counts.items()) if v},
            "fully_covered": sum(1 for r in rows if r["matched"] == r["total"])}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def nearest_carrier(density_kg_m3, tolerance=0.03):
    """Closest verified Geant4 density carrier, or (None, deviation).

    G4CMP takes the substrate density from the G4Material and the executable
    accepts only a NIST material NAME, so a real material can be simulated at
    its own density only if some NIST material happens to sit within tolerance.
    The deviation is returned either way and is reported, never hidden: at 3% in
    density the derived speeds move by 1.5%, which is inside the tier's ~5%
    stochastic error but is still a stated approximation.
    """
    best, best_dev = None, float("inf")
    for name, rho in S.SUBSTRATE_CARRIERS.items():
        dev = abs(rho - density_kg_m3) / density_kg_m3
        if dev < best_dev:
            best, best_dev = name, dev
    return (best if best_dev <= tolerance else None), best_dev


def elasticity_variant_point(target_point, substrate, top, bottom, space=None,
                             tolerance=0.03, allow_target_lifetime=True):
    """A candidate carrying a REAL material's elasticity and density, with the
    optimizer's target anharmonic constants kept.

    Why this exists: the nearest real substrates to a Stage 4 optimum are cubic
    crystals with no G4CMP lattice record, so `scat`, `decay` and `decayTT` are
    simply unknown for them and `realizable_point` refuses. Refusing is right for
    a materials claim, but it leaves an obvious question unanswered -- how much
    of the ideal is recoverable from the part of the material that IS measured.

    This variant answers exactly that, and its label says what it is: the
    material's measured tensor and density, the DESIGN TARGET's phonon
    constants. It is not a claim about that material; it is a bound on what its
    elasticity alone buys, and it is only as good as the assumption that the
    target's `scat`/`decay` are achievable in it.
    """
    space = space or S.DEFAULT_SPACE
    point = dict(space.complete(target_point))
    carrier, dev = nearest_carrier(substrate["density_kg_m3"], tolerance)
    notes = []
    if carrier is None:
        return None, [f"substrate {substrate['id']}: no Geant4 density carrier within "
                      f"{tolerance:.0%} of {substrate['density_kg_m3']:.0f} kg/m3 "
                      f"(nearest is {dev:.1%} away)"]
    if top.get("speed_used_m_s") is None or bottom.get("speed_used_m_s") is None:
        return None, [f"film speed missing for {top['id']} or {bottom['id']}"]
    point.update({
        "sub_c11": substrate["c11_GPa"], "sub_c12": substrate["c12_GPa"],
        "sub_c44": substrate["c44_GPa"],
        "sub_lattice_a": substrate.get("lattice_a_ang", point["sub_lattice_a"]),
        "topfilm_vsound": top["speed_used_m_s"] / 1000.0,
        "topfilm_gap": top["gap_eV"],
        "topfilm_ph_lifetime": top.get("ph_lifetime_ns") or point["topfilm_ph_lifetime"],
        "topfilm_density": top["density_kg_m3"],
        "bot_vsound": bottom["speed_used_m_s"] / 1000.0,
        "bot_ph_lifetime": bottom.get("ph_lifetime_ns") or point["bot_ph_lifetime"],
        "bot_density": bottom["density_kg_m3"],
    })
    point[S.REALIZATION_KEY] = S.normalize_realization({
        S.REALIZATION_KEY: {
            "mode": "pseudo_si_base",
            "substrate_carrier": carrier,
            "base_lattice_map": S.BASE_LATTICE_MAP,
            "label": f"elasticity_of_{substrate['id']}",
            "material_of_record": substrate["id"],
            "material_density_kg_m3": float(substrate["density_kg_m3"]),
            "density_tolerance": float(tolerance),
            "density_carrier_deviation": round(float(dev), 6),
            "own_fields": ["c11", "c12", "c44", "lattice_a", "density(via carrier)"],
            "target_fields": ["scat", "decay", "decayTT"],
        }})
    notes.append(f"density realised by {carrier} ({S.SUBSTRATE_CARRIERS[carrier]:.0f} "
                 f"vs {substrate['density_kg_m3']:.0f} kg/m3, {dev:+.1%})")
    notes.append("scat/decay/decayTT are the DESIGN TARGET's, not this material's "
                 "(they are unmeasured for it)")
    if top.get("ph_lifetime_ns") is None:
        notes.append(f"top film {top['id']} lifetime unsourced -- target's kept")
    if bottom.get("ph_lifetime_ns") is None:
        notes.append(f"bottom film {bottom['id']} lifetime unsourced -- target's kept")
    exempt = set(S.box_exempt_variables(point[S.REALIZATION_KEY]))
    for v in space.all_variables:
        x = float(point[v.name])
        if v.low <= x <= v.high:
            continue
        if v.name in exempt:
            # A measured constant of the material being projected. Clipping it
            # would simulate a different crystal under this material's name --
            # which is exactly how "SiC" was previously run at C44 = 200 GPa
            # instead of its measured 241.
            notes.append(f"{v.name}={x:g} is OUTSIDE the searched box "
                         f"[{v.low:g}, {v.high:g}] and is kept: it is this "
                         f"material's measured value, not a proposal")
            continue
        notes.append(f"{v.name}={x:g} clipped into the searched box "
                     f"[{v.low:g}, {v.high:g}]")
        point[v.name] = min(v.high, max(v.low, x))
    return point, notes


def phonon_constant_brackets(pool, target_point, space=None):
    """Physically justified low/nominal/high values for the UNMEASURED constants.

    `scat`, `decay` and `decayTT` are unknown for every cubic material outside
    the shipped G4CMP records, and they are also strong optimization directions
    (`sub_scat` is 17x from baseline at the best point found). Substituting the
    design target's value and reporting one number treats an unmeasured
    quantity as a known one.

    The bracket is taken from the SPREAD OF THE MATERIALS THAT ARE MEASURED --
    the five complete G4CMP records -- rather than from a factor pulled out of
    the air, together with the target's own value. Propagating it gives an
    expected and a worst case instead of a point estimate.
    """
    space = space or S.DEFAULT_SPACE
    target = space.complete(target_point)
    known = {"sub_scat": [], "sub_decay": [], "sub_decayTT": []}
    for rec in (pool.get("g4cmp_substrates") or {}).values():
        for key, field in (("sub_scat", "scat_s3"), ("sub_decay", "decay_s4"),
                           ("sub_decayTT", "decayTT")):
            if rec.get(field) is not None:
                known[key].append(float(rec[field]))
    out = {}
    for key, values in known.items():
        if not values:
            continue
        lo, hi = min(values), max(values)
        v = space.clip({**target, key: float(target[key])})[key]
        out[key] = {
            "target": float(v),
            "low": float(min(lo, v)),
            "high": float(max(hi, v)),
            "measured_range": [float(lo), float(hi)],
            "n_measured_materials": len(values),
            "basis": "spread over the complete G4CMP lattice records, widened to "
                     "include the design target's own value",
        }
    return out


def bracket_variants(base_point, brackets, space=None, which=("low", "high")):
    """{suffix: point} -- the base point with one unmeasured constant moved.

    One variable at a time: with three unmeasured constants a full factorial is
    27 simulations, and the point of the bracket is to bound each unmeasured
    direction, not to resolve their interactions (which this data cannot).
    """
    space = space or S.DEFAULT_SPACE
    out = {}
    for key, spec in (brackets or {}).items():
        for side in which:
            value = spec.get(side)
            if value is None or math.isclose(value, spec["target"], rel_tol=1e-9):
                continue
            point = dict(base_point)
            point[key] = float(value)
            out[f"{key}_{side}"] = point
    return out


def realizable_point(target_point, substrate, top, bottom, space=None,
                     allow_target_lifetime=False):
    """Property vector of a REAL triplet, or (None, reason) if it cannot be built.

    Refuses rather than substitutes. A substrate with no G4CMP anharmonic record
    or no verified Geant4 density carrier cannot be simulated faithfully, and
    filling the hole from Si would recreate exactly the pseudo-material this
    step exists to escape.
    """
    space = space or S.DEFAULT_SPACE
    point = dict(space.complete(target_point))
    missing = []
    if not substrate.get("g4_carrier"):
        missing.append(f"substrate {substrate['id']}: no verified Geant4 density "
                       f"carrier (run stage4_probe_g4_density.py)")
    for key, field in (("sub_scat", "scat_s3"), ("sub_decay", "decay_s4"),
                       ("sub_decayTT", "decayTT")):
        if substrate.get(field) is None:
            missing.append(f"substrate {substrate['id']}: no sourced {field}")
    if top.get("ph_lifetime_ns") is None and not allow_target_lifetime:
        missing.append(f"top film {top['id']}: no sourced phonon lifetime")
    if bottom.get("ph_lifetime_ns") is None and not allow_target_lifetime:
        missing.append(f"bottom film {bottom['id']}: no sourced phonon lifetime")
    if top.get("speed_used_m_s") is None:
        missing.append(f"top film {top['id']}: no sound speed")
    if bottom.get("speed_used_m_s") is None:
        missing.append(f"bottom film {bottom['id']}: no sound speed")
    if missing:
        return None, missing

    point.update({
        "sub_c11": substrate["c11_GPa"], "sub_c12": substrate["c12_GPa"],
        "sub_c44": substrate["c44_GPa"], "sub_scat": substrate["scat_s3"],
        "sub_decay": substrate["decay_s4"], "sub_decayTT": substrate["decayTT"],
        "sub_lattice_a": substrate.get("lattice_a_ang", point["sub_lattice_a"]),
        "topfilm_vsound": top["speed_used_m_s"] / 1000.0,
        "topfilm_gap": top["gap_eV"],
        "topfilm_ph_lifetime": (top.get("ph_lifetime_ns")
                                or point["topfilm_ph_lifetime"]),
        "topfilm_density": top["density_kg_m3"],
        "bot_vsound": bottom["speed_used_m_s"] / 1000.0,
        "bot_ph_lifetime": (bottom.get("ph_lifetime_ns")
                            or point["bot_ph_lifetime"]),
        "bot_density": bottom["density_kg_m3"],
    })
    # A substrate with a COMPLETE G4CMP record is simulated as itself, from its
    # own record; one that merely has sourced constants is a pseudo-material
    # with every substrate field overridden, and says so.
    if substrate.get("catalog_key") and substrate.get("has_native_record"):
        point[S.REALIZATION_KEY] = S.normalize_realization({
            S.REALIZATION_KEY: {"mode": "native_g4cmp",
                                "material": substrate["catalog_key"],
                                "label": f"native_{substrate['id']}"}})
    elif substrate.get("has_native_record") and substrate.get("lattice_map"):
        point[S.REALIZATION_KEY] = S.normalize_realization({
            S.REALIZATION_KEY: {"mode": "custom_material",
                                "substrate_carrier": substrate["g4_carrier"],
                                "lattice_map": substrate["lattice_map"],
                                "material_of_record": substrate["id"],
                                "label": f"custom_{substrate['id']}"}})
    else:
        point[S.REALIZATION_KEY] = S.normalize_realization({
            S.REALIZATION_KEY: {
                "mode": "pseudo_si_base",
                "substrate_carrier": substrate["g4_carrier"],
                "base_lattice_map": S.BASE_LATTICE_MAP,
                "label": f"sourced_{substrate['id']}",
                "material_of_record": substrate["id"],
                "own_fields": ["c11", "c12", "c44", "lattice_a", "scat", "decay",
                               "decayTT", "density(via carrier)"],
                "target_fields": [],
            }})
    # Orientation, lattice rotation, temperature and the bottom gap threshold are
    # NOT material properties of the projected triplet -- they stay at the
    # target's values, which is what a fabricator would actually set.
    exempt = set(S.box_exempt_variables(point[S.REALIZATION_KEY]))
    clipped = []
    for v in space.all_variables:
        x = float(point[v.name])
        if v.low <= x <= v.high:
            continue
        if v.name in exempt:
            clipped.append(f"{v.name}={x:g} outside the searched box "
                           f"[{v.low:g}, {v.high:g}] and KEPT (measured value)")
            continue
        clipped.append(f"{v.name}={x:g} outside the searched box "
                       f"[{v.low:g}, {v.high:g}] -- clipped")
        point[v.name] = min(v.high, max(v.low, x))
    return point, clipped


def verify(points, contract_path, ledger_path, events, workers, parallel,
           seed_bank, tag, timeout=None):
    """Simulate the projected triplets and the ideal target under held-out seeds."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from stage3_contract import load_contract
    from stage3_ledger import Ledger
    from stage3_trial_runner import evaluate
    import stage4_objectives as O
    from stage4_optimize import candidate_payload, resolver_for

    contract = load_contract(contract_path)
    contract.campaign_id = f"{contract.campaign_id}_{tag}"
    fid = contract.decision["fidelity"]["value"]
    contract.decision["fidelity"]["events_total_per_candidate"][fid] = events
    # Finding N4: this used to divide workers and memory by `parallel` and then
    # iterate SEQUENTIALLY, so `--parallel 2` gave each candidate half the
    # machine while the other half sat idle -- a straight 2x slowdown dressed up
    # as parallelism. `parallel` now actually bounds concurrent candidates, and
    # the split is only applied when there is really more than one in flight.
    parallel = max(1, int(parallel))
    n_slots = min(parallel, max(1, len(points)))
    contract.fixed["max_workers"] = max(1, workers // n_slots)
    contract.fixed["total_mem_gb"] = float(contract.fixed["total_mem_gb"]) / n_slots
    if timeout:
        contract.fixed["sample_timeout_s"] = timeout
    space = S.DEFAULT_SPACE
    results = {}
    print(f"  {n_slots} candidate(s) at a time x "
          f"{contract.fixed['max_workers']} sub-run workers "
          f"= {n_slots * contract.fixed['max_workers']} cores")

    # One ledger connection per thread: sqlite3 objects are not shareable
    # across threads, which is why stage4_confirm.py does the same.
    local = threading.local()

    def led():
        if getattr(local, "l", None) is None:
            local.l = Ledger(ledger_path)
        return local.l

    def run_one(label, point):
        try:
            r = evaluate(contract, candidate_payload(point, space), fidelity=fid,
                         seed_bank_id=seed_bank, ledger=led(), verbose=False,
                         resolver=resolver_for(space))
        except Exception as exc:                           # noqa: BLE001
            return label, {"status": "rejected", "reason": str(exc)}
        if not r.is_observation:
            return label, {"status": r.status, "reason": r.failure_reason}
        v = O.get("total_qps_per_primary")(r)
        return label, {"status": r.status, "value": v.value, "se": v.se,
                       "total_qps": r.total_qps, "trial_id": r.trial_id,
                       "relative_se": v.detail.get("relative_se")}

    with ThreadPoolExecutor(max_workers=n_slots) as pool:
        futures = [pool.submit(run_one, k, v) for k, v in points.items()]
        for fut in as_completed(futures):
            label, rec = fut.result()
            results[label] = rec
            if rec.get("value") is not None:
                print(f"  {label:28s} {rec['value']:.4e}/event  "
                      f"(total {rec['total_qps']:.0f}, "
                      f"+-{100 * (rec.get('relative_se') or 0):.1f}%)")
            else:
                print(f"  {label:28s} {rec['status']}: "
                      f"{str(rec.get('reason'))[:80]}")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def load_target(args, space):
    if args.manifest:
        with open(args.manifest) as handle:
            man = json.load(handle)
        best = man.get("best")
        if not best:
            sys.exit(f"{args.manifest} records no best point")
        return space.complete(best["point"]), man
    if args.point:
        return space.complete(json.loads(args.point)), None
    return space.baseline_point(), None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=None, help="campaign manifest JSON")
    ap.add_argument("--point", default=None, help="explicit property vector as JSON")
    ap.add_argument("--pool", default=POOL_PATH)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--film-speed-convention", default="catalog_or_longitudinal",
                    choices=("catalog_or_longitudinal", "longitudinal", "debye"))
    ap.add_argument("--weights", default=None,
                    help="JSON feature->weight, e.g. from a sensitivity scan")
    ap.add_argument("--max-hull", type=float, default=0.05,
                    help="eV/atom above hull for MP substrates")
    ap.add_argument("--no-mp", action="store_true",
                    help="restrict substrates to those with a G4CMP lattice record")
    ap.add_argument("--flat-ranking", action="store_true",
                    help="rank by distance alone, ignoring feature coverage. The "
                         "pre-audit behaviour; it puts a 4/7 distance and a 7/7 "
                         "distance in one ordering, which is what P4 flagged.")
    ap.add_argument("--out", default=os.path.join(HERE, "results",
                                                  "stage4_projection.json"))
    # verification
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--verify-top", type=int, default=3)
    ap.add_argument("--verify-events", type=int, default=4000000)
    ap.add_argument("--contract", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--seed-bank", type=int, default=9,
                    help="HELD-OUT bank: confirmation must not reuse selection seeds")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--tag", default="projection")
    ap.add_argument("--verify-simulable-only", action="store_true", default=True,
                    help="build the verification shortlist only from candidates that "
                         "can be simulated as-is (the default; the unrestricted "
                         "ranking is still printed and saved)")
    ap.add_argument("--verify-any", dest="verify_simulable_only", action="store_false",
                    help="allow the shortlist to include candidates that cannot be "
                         "simulated; they will be reported as refused, with why")
    ap.add_argument("--elasticity-variants", type=int, default=3,
                    help="how many of the nearest NON-simulable substrates to test "
                         "as elasticity-only variants (their measured tensor and "
                         "density, the target's phonon constants)")
    ap.add_argument("--carrier-tolerance", type=float, default=0.03,
                    help="max density deviation when realising a real material's "
                         "density with a NIST carrier")
    ap.add_argument("--phonon-brackets", action="store_true",
                    help="for each elasticity variant, also simulate the low and "
                         "high edges of scat / decay / decayTT, so the unmeasured "
                         "constants are reported as a range instead of being "
                         "silently replaced by the design target's value (P4)")
    ap.add_argument("--allow-target-lifetime", action="store_true",
                    help="simulate a film whose lifetime is unsourced by keeping the "
                         "target's value; the result is then NOT a materials claim")
    args = ap.parse_args()

    space = S.DEFAULT_SPACE
    target_point, manifest = load_target(args, space)
    target = S.comparison_features(target_point)
    pool = load_pool(args.pool)
    weights = json.loads(args.weights) if args.weights else None

    print("Stage 4 nearest-material projection")
    print(f"  target from: {args.manifest or args.point or 'baseline'}")
    if manifest and manifest.get("best"):
        print(f"  target objective: {manifest['best']['value']:.4e} "
              f"({manifest.get('objective')}, optimizer {manifest.get('optimizer')})")
    print(f"  film speed convention: {args.film_speed_convention}")
    print("\nTarget in physical space:")
    for k, v in target.items():
        print(f"  {k:26s} {v:12.5g}")

    subs = substrate_pool(pool, include_mp=not args.no_mp, max_hull=args.max_hull)
    tops = film_pool(pool, "top", args.film_speed_convention)
    bots = film_pool(pool, "bottom", args.film_speed_convention)
    print(f"\nPools: {len(subs)} substrates, {len(tops)} top films, "
          f"{len(bots)} bottom films")

    out = {"target_point": {k: (list(v) if isinstance(v, list) else v)
                            for k, v in target_point.items()},
           "target_features": target,
           "film_speed_convention": args.film_speed_convention,
           "weights": weights, "layers": {}}
    full_rankings = {}

    for label, cands, spec in (("substrate", subs, SUBSTRATE_FEATURES),
                               ("top_film", tops, TOP_FILM_FEATURES),
                               ("bottom_film", bots, BOTTOM_FILM_FEATURES)):
        # Rank the WHOLE pool, then slice for printing. The simulable-only view
        # below filters this ranking, so truncating first would hide a candidate
        # that is checkable but not in the overall top few.
        all_rows, scales = rank(target, cands, spec, weights, top=len(cands),
                                stratify=not args.flat_ranking)
        full_rankings[label] = all_rows
        groups = strata(all_rows)
        coverage = missing_feature_report(all_rows, spec)
        print(f"\n{label}: {len(cands)} candidates in "
              f"{len(groups)} coverage stratum/strata "
              f"({coverage['fully_covered']} fully characterised)")
        if coverage["missing_counts"]:
            print("  unsourced for some candidates: "
                  + ", ".join(f"{k} ({v})" for k, v
                              in coverage["missing_counts"].items()))
        for stratum, group in groups.items():
            shown = group[:args.top]
            print(f"  -- {stratum} features matched: nearest {len(shown)} "
                  f"of {len(group)}")
            print(f"     {'rank':>4s} {'candidate':34s} {'distance':>9s}  notes")
            for i, r in enumerate(shown, 1):
                c = r["candidate"]
                notes = []
                if c.get("verified") is False:
                    notes.append("unverified data")
                if c.get("simulable") is False and label == "substrate":
                    notes.append("not simulable as-is")
                if r["unmatched_features"]:
                    notes.append("unsourced: " + ", ".join(r["unmatched_features"]))
                print(f"     {i:4d} {c['id'][:34]:34s} {r['distance']:9.3f}  "
                      f"{'; '.join(notes)}")
        if len(groups) > 1:
            print("  NOTE: distances from different strata answer different "
                  "questions and are never comparable as one ranking. A 4/7 "
                  "distance is an elasticity/impedance distance.")
        out["layers"][label] = {
            "scales": scales,
            "coverage": coverage,
            "strata": {k: [{"rank": i + 1, "id": r["id"],
                            "distance": r["distance"],
                            "unmatched_features": r["unmatched_features"]}
                           for i, r in enumerate(g[:max(args.top, 20)])]
                       for k, g in groups.items()},
            "ranking_order": ("coverage-stratified" if not args.flat_ranking
                              else "flat (coverage ignored -- --flat-ranking)"),
            "ranking": [{"rank": i + 1, "id": r["id"], "distance": r["distance"],
                         "matched": r["matched"], "total": r["total"],
                         "unmatched_features": r["unmatched_features"], "z": r["z"],
                         "stratum": r["stratum"], "coverage": r["coverage"],
                         "verified": r["candidate"].get("verified"),
                         "source": r["candidate"].get("source"),
                         "features": r["candidate"]["features"]}
                        for i, r in enumerate(all_rows[:max(args.top, 40)])],
        }

    # A second ranking, restricted to what can actually be SIMULATED today: a
    # substrate needs both a complete G4CMP anharmonic record and a measured
    # Geant4 density carrier, and a film needs a sourced phonon lifetime. The
    # unrestricted ranking above answers "what is closest"; this one answers
    # "what can be checked", and the two are different questions that must not
    # be blurred into one table.
    simulable_ids = {
        "substrate": {c["id"] for c in subs if c.get("simulable")},
        "top_film": {c["id"] for c in tops if c.get("ph_lifetime_ns") is not None},
        "bottom_film": {c["id"] for c in bots if c.get("ph_lifetime_ns") is not None},
    }
    print("\nRestricted to candidates that can be simulated as-is "
          "(complete record + measured Geant4 density / sourced film lifetime):")
    for label in ("substrate", "top_film", "bottom_film"):
        # From the FULL ranking, not the truncated stored one: with 378 cubic
        # substrates in the pool, a checkable material can easily sit outside the
        # overall top 40 and still be the best thing that can actually be run.
        rows = [{"rank": i + 1, "id": r["id"], "distance": r["distance"],
                 "matched": r["matched"], "total": r["total"],
                 "unmatched_features": r["unmatched_features"], "z": r["z"]}
                for i, r in enumerate(full_rankings[label])
                if r["id"] in simulable_ids[label]][:6]
        out["layers"][label]["simulable_ranking"] = rows
        pretty = ", ".join(f"{r['id']} (d={r['distance']:.3f})" for r in rows) or "none"
        print(f"  {label:12s} {pretty}")

    # Combined triplets: the objective is not separable across layers, so the
    # summed distance is a SHORTLIST, never a ranking of QP performance. That is
    # what --verify is for.
    def layer_rows(label):
        rows = (out["layers"][label]["simulable_ranking"]
                if args.verify_simulable_only
                else [{"id": r["id"], "distance": r["distance"]}
                      for r in full_rankings[label]])
        return rows[:args.verify_top]

    top_s, top_t, top_b = (layer_rows("substrate"), layer_rows("top_film"),
                           layer_rows("bottom_film"))
    combos = []
    for s in top_s:
        for t in top_t:
            for b in top_b:
                combos.append({"triplet": f"{s['id']}/{t['id']}/{b['id']}",
                               "distance_sum": s["distance"] + t["distance"] + b["distance"],
                               "parts": [s["id"], t["id"], b["id"]]})
    combos.sort(key=lambda c: c["distance_sum"])
    out["shortlist"] = combos[:12]
    print("\nShortlist (summed distance -- a shortlist, NOT a QP ranking):")
    for c in combos[:8]:
        print(f"  {c['distance_sum']:8.3f}  {c['triplet']}")

    subs_by_id = {c["id"]: c for c in subs}
    tops_by_id = {c["id"]: c for c in tops}
    bots_by_id = {c["id"]: c for c in bots}
    subs_by_distance = [subs_by_id[r["id"]] for r in full_rankings["substrate"]]
    tops_by_distance = [tops_by_id[r["id"]] for r in full_rankings["top_film"]]
    bots_by_distance = [bots_by_id[r["id"]] for r in full_rankings["bottom_film"]]

    if args.verify:
        by_id = subs_by_id
        top_by, bot_by = tops_by_id, bots_by_id
        points, notes = {"ideal_target": target_point}, {}

        # Elasticity-only variants of the nearest substrates overall (not just
        # the simulable ones). These are the interesting cases: the optimum
        # generally wants a stiffer, lighter crystal than anything with a G4CMP
        # record, so without this the verification can only report how far short
        # the already-catalogued materials fall.
        if args.elasticity_variants:
            best_top = tops_by_distance[0] if tops_by_distance else None
            best_bot = bots_by_distance[0] if bots_by_distance else None
            for cand in subs_by_distance[:args.elasticity_variants]:
                if cand.get("simulable"):
                    continue
                point, why = elasticity_variant_point(
                    target_point, cand, best_top, best_bot, space,
                    tolerance=args.carrier_tolerance)
                label = f"elasticity_of_{cand['id'].split(' ')[0]}"
                if point is None:
                    notes[label] = why
                    continue
                points[label] = point
                notes[label] = why
                # The three constants this variant had to borrow from the design
                # target are unmeasured for this material. Bracket them so the
                # verification reports a range, not a point estimate dressed up
                # as a material property.
                if args.phonon_brackets:
                    brackets = phonon_constant_brackets(pool, point, space)
                    out.setdefault("phonon_brackets", {})[label] = brackets
                    for suffix, variant in bracket_variants(
                            point, brackets, space).items():
                        points[f"{label}__{suffix}"] = variant
                        notes[f"{label}__{suffix}"] = [
                            f"{suffix}: unmeasured constant moved to the edge of "
                            f"the range spanned by the measured G4CMP records; "
                            f"this is an UNCERTAINTY BRACKET, not a prediction"]

        n_triplets = 0
        for c in combos:
            if n_triplets >= args.verify_top:
                break
            s, t, b = (by_id[c["parts"][0]], top_by[c["parts"][1]],
                       bot_by[c["parts"][2]])
            point, problem = realizable_point(target_point, s, t, b, space,
                                              args.allow_target_lifetime)
            if point is None:
                notes[c["triplet"]] = problem
                continue
            points[c["triplet"]] = point
            n_triplets += 1
            if problem:
                notes[c["triplet"]] = problem          # clipped-to-box warnings
        print(f"\nVerification: {len(points)} simulation(s) at "
              f"{args.verify_events:,} events, HELD-OUT seed bank {args.seed_bank}")
        for triplet, why in notes.items():
            print(f"  note {triplet}: {why}")
        results = verify(points, args.contract, args.ledger, args.verify_events,
                         args.workers, args.parallel, args.seed_bank, args.tag,
                         args.timeout)
        out["verification"] = {"results": results, "notes": notes,
                               "events": args.verify_events,
                               "seed_bank": args.seed_bank}
        ideal = results.get("ideal_target", {}).get("value")
        base = None
        try:
            base_point = space.baseline_point()
            base_res = verify({"baseline": base_point}, args.contract, args.ledger,
                              args.verify_events, args.workers, args.parallel,
                              args.seed_bank, args.tag, args.timeout)
            base = base_res.get("baseline", {}).get("value")
            out["verification"]["baseline"] = base_res.get("baseline")
        except Exception as exc:                            # noqa: BLE001
            print(f"  baseline control failed: {exc}")
        if ideal and base:
            print(f"\n  {'candidate':34s} {'QPs/event':>11s} {'vs baseline':>12s} "
                  f"{'realized':>9s}")
            print(f"  {'baseline (Si/Nb/Cu-equivalent)':34s} {base:11.4e} "
                  f"{'--':>12s} {'--':>9s}")
            print(f"  {'ideal property target':34s} {ideal:11.4e} "
                  f"{100 * (ideal - base) / base:11.1f}% {'100%':>9s}")
            for label, res in results.items():
                if label == "ideal_target" or res.get("value") is None:
                    continue
                frac = ((base - res["value"]) / (base - ideal)) if base != ideal else float("nan")
                print(f"  {label[:34]:34s} {res['value']:11.4e} "
                      f"{100 * (res['value'] - base) / base:11.1f}% {100 * frac:8.0f}%")
                res["realized_fraction"] = frac

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
