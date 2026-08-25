"""Stage 4 property space: decision variables, transforms, hard gates, resolver.

Stage 3 selected among *real material triplets*; the property values travelled
as a linked bundle from the catalog. Stage 4 opens the second interpretation of
`STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md` sec 3.2: the tunable properties
themselves are the decision variables, inside a physically constrained box. The
result is a property *target*, and a pseudo-material need not exist -- which is
exactly why `stage4_project_material.py` exists.

Three things this module owns:

1. **The box.** Every variable in the "Tune the following parameters" block of
   `parameter_set.txt`, with bounds grounded in measured data (the 396 accepted
   cubic materials in `catalog/substrates_raw.json`, the seven shipped G4CMP
   lattice records, and the curated films in `material_catalog.yaml`) rather
   than in arbitrary 0.5x-1.5x factors.

2. **The gates.** Born stability, Christoffel positivity, mode ordering,
   interface probabilities in [0,1], the excitation chain, and regime
   consistency -- all evaluated in pure Python BEFORE any Geant4 process starts.
   G1-G3 are not hygiene: sampling `vtrans > vsound` crashed 31/31 affected
   design points mid-run on the first material branch, leaving a zero-byte hits
   file (Fisher exact p = 4.3e-18).

3. **The resolver.** `resolve(contract, candidate) -> (resolved, derived)`,
   shaped exactly like the Stage 3 catalog resolver so the audited evaluator in
   `stage3_trial_runner.py` runs a pseudo-material through the same path --
   same planned identities, same cache key, same completeness rule.

What a pseudo-material IS, stated so no reader has to infer it: the **Si G4CMP
lattice record with the scanned fields overridden**. `dyn` (third-order
elasticity), the Tamura LDOS/STDOS/FTDOS mode fractions, `Debye`, and the
charge-carrier block stay at Si's values. `Debye` and the lattice constant are
*measured* inert for this objective (see MODULE NOTES below); `dyn` and the DOS
fractions are held fixed by the parameter contract and belong in the reported
limitations.

MODULE NOTES -- what is deliberately not scanned
------------------------------------------------
* **Substrate mass density** is quantized by Geant4: G4CMP takes it from the
  G4Material (`G4LatticeManager::LoadLattice` -> `SetDensity(Mat->GetDensity())`)
  and the executable only accepts a NIST material *name*. It is not a free
  variable, and it costs almost nothing: density enters the objective through
  `v = sqrt(C/rho)` and through the boundary impedance `Z = rho * v_eff`, and
  C11/C12/C44 are free over a 20x range while the two film densities retain the
  impedance channel. `SUBSTRATE_CARRIERS` holds the three verified carriers.
* **Debye** is inert for the `phonon_Caustic` gun: measured 144 vs 144 QPs,
  bit-identical, for Ge at 2 vs 7.8 THz. It is consumed by
  `G4CMPEnergyPartition::GeneratePhonons()`, which this gun bypasses.
* **The lattice constant `cubic a`** sets `fBasis`, used for the Miller
  *direction* (scale-invariant for a cubic cell) and for `G4CMPChargeCloud`
  (charge carriers only). Written into the config for provenance, excluded from
  the active search, inertness proved by an A/B trial rather than assumed.
* **Film densities** never reach Geant4 at all -- no `GetDensity` call exists in
  the Main sources or in `G4CMPKaplanQP`. They are decision variables here
  because the interface model needs them, and because the projection step needs
  a (rho, v) pair to compare against real metals.
"""

import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stage1_run_simulations as harness                         # noqa: E402
from interface_transmission import (                             # noqa: E402
    resolve_all_interfaces, InterfaceModelError)


class GateError(ValueError):
    """A candidate violated a hard physics gate. Never an observation."""

    def __init__(self, gate, message):
        super().__init__(f"[{gate}] {message}")
        self.gate = gate


# ---------------------------------------------------------------------------
# Geant4 density carriers. Measured from live runs, not tabulated: the NIST
# material Geant4 actually builds is what G4CMP puts into the phonon kinematics.
# Extend with stage4_probe_g4_density.py, never by hand.
# ---------------------------------------------------------------------------
SUBSTRATE_CARRIERS = {
    "G4_Si": 2330.0,
    "G4_Ge": 5323.0,
    "G4_GALLIUM_ARSENIDE": 5310.0,
    # Measured 2026-08-21 with stage4_probe_g4_density.py (one-event runs, density
    # read back out of the Geant4 log). These extend the reachable density range
    # DOWNWARD, which is the direction a stiff, light, fast substrate needs -- the
    # first campaign's optimum asked for v_L ~ 13.9 km/s, and none of the original
    # three carriers can host a crystal that fast at a plausible tensor.
    "G4_CALCIUM_FLUORIDE": 3180.0,   # also has a complete G4CMP lattice record
    "G4_LITHIUM_FLUORIDE": 2635.0,   # also has a complete G4CMP lattice record
    "G4_LITHIUM_HYDRIDE": 820.0,
    "G4_ALUMINUM_OXIDE": 3970.0,
    "G4_BORON_CARBIDE": 2520.0,
}

# ---------------------------------------------------------------------------
# Candidate realization -- how a property vector becomes a simulable material.
#
# P0 of STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md: the projection step used to
# attach `point["substrate_carrier"]` as a loose extra dict key, and every
# downstream normaliser (`Space.complete`, `candidate_payload`,
# `stage4_confirm.collect_points`) silently dropped it -- so six projection runs
# that were supposed to carry SiC's 3180 kg/m3 were simulated as G4_Si at 2330.
#
# The fix is to stop relying on arbitrary extra keys: PHYSICS DECISION VARIABLES
# and MATERIAL REALIZATION METADATA are now different things with different
# lifetimes. The realization travels as one explicit, versioned, validated block
# under `_realization`, is preserved by every normaliser, and enters the ledger
# payload -- and therefore the cache key.
#
# Modes:
#   pseudo_si_base  -- the Si G4CMP record with the scanned fields overridden,
#                      density carried by a measured NIST material. What the
#                      search itself proposes. Always labelled a pseudo-material.
#   native_g4cmp    -- a real material with a COMPLETE G4CMP lattice record: its
#                      own tensor, dyn, scat, decay, decayTT, DOS and Debye, at
#                      its own Geant4 density. The substrate property variables
#                      are taken FROM the record, not from the proposal.
#   custom_material -- a real material registered explicitly with both a measured
#                      Geant4 density and a complete lattice record on disk.
#                      Refuses to simulate if either is absent.
# ---------------------------------------------------------------------------
REALIZATION_KEY = "_realization"
REALIZATION_MODES = ("pseudo_si_base", "native_g4cmp", "custom_material")
REALIZATION_SCHEMA = "stage4_realization_v1"
DEFAULT_CARRIER = "G4_Si"

# Substrate variables whose value is a MATERIAL FACT in native/custom mode and a
# free decision only in pseudo mode. In the two real-material modes they are read
# from the lattice record and are exempt from the search box -- clipping a real
# material into the box is what produced the "SiC C44 = 241 GPa clipped to 200"
# caveat in STAGE4_RESULTS.md sec 4.3.
SUBSTRATE_MATERIAL_VARIABLES = ("sub_c11", "sub_c12", "sub_c44", "sub_scat",
                                "sub_decay", "sub_decayTT", "sub_lattice_a")

# Fields a lattice record must carry before it may be called complete. `Debye`
# and the charge-carrier block are deliberately NOT required: Debye is measured
# inert for this gun (see MODULE NOTES) and no charge carriers are transported.
REQUIRED_LATTICE_FIELDS = ("cubic", "stiffness 1 1", "stiffness 1 2",
                           "stiffness 4 4", "dyn", "scat", "decay", "decayTT",
                           "LDOS", "STDOS", "FTDOS")


def default_realization():
    """The realization a bare property vector means: a Si-based pseudo-material."""
    return {"schema": REALIZATION_SCHEMA, "mode": "pseudo_si_base",
            "substrate_carrier": DEFAULT_CARRIER,
            "base_lattice_map": BASE_LATTICE_MAP,
            "lattice_map": PSEUDO_LATTICE_NAME}


def _lattice_config_path(name):
    return os.path.join(harness.G4CMP_SOURCE_CRYSTALMAPS, name, "config.txt")


_LATTICE_CACHE = {}


def read_lattice_record(name):
    """Parse a G4CMP `config.txt` into {field: [numbers]}, or raise GateError.

    Used to (a) prove a real material's record is complete before a Geant4
    process is created, and (b) take the substrate physics FROM that record in
    native/custom mode instead of from the optimizer's proposal.
    """
    if name in _LATTICE_CACHE:
        return _LATTICE_CACHE[name]
    path = _lattice_config_path(name)
    if not os.path.isfile(path):
        raise GateError("G-realization",
                        f"no G4CMP lattice record for {name!r} at {path}")
    fields = {}
    with open(path) as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            key, rest = parts[0], parts[1:]
            if key == "stiffness" and len(rest) >= 3:
                key = f"stiffness {rest[0]} {rest[1]}"
                rest = rest[2:]
            nums = []
            for tok in rest:
                try:
                    nums.append(float(tok))
                except ValueError:
                    break
            fields.setdefault(key, nums)
    missing = [f for f in REQUIRED_LATTICE_FIELDS if not fields.get(f)]
    if missing:
        raise GateError("G-realization",
                        f"lattice record {name!r} is incomplete: missing {missing}. "
                        f"A partial record would be silently completed from Si, "
                        f"which is the pseudo-material this mode exists to avoid.")
    _LATTICE_CACHE[name] = fields
    return fields


def native_substrate_values(lattice_map):
    """Substrate decision-variable values as recorded for a real material."""
    rec = read_lattice_record(lattice_map)
    return {
        "sub_c11": float(rec["stiffness 1 1"][0]),
        "sub_c12": float(rec["stiffness 1 2"][0]),
        "sub_c44": float(rec["stiffness 4 4"][0]),
        "sub_scat": float(rec["scat"][0]),
        "sub_decay": float(rec["decay"][0]),
        "sub_decayTT": float(rec["decayTT"][0]),
        "sub_lattice_a": float(rec["cubic"][0]),
    }


def _catalog_substrate(name):
    """Stage 3 substrate record, so native mode routes through the audited catalog."""
    from stage3_trial_runner import _catalog_section
    table = _catalog_section("substrates")
    if name not in table:
        raise GateError("G-realization",
                        f"substrate {name!r} is not in material_catalog.yaml "
                        f"(available: {sorted(table)}). A real-material claim must "
                        f"come from the catalog, not from an ad-hoc dict.")
    rec = table[name]
    if rec.get("enabled") is False:
        raise GateError("G-realization",
                        f"substrate {name!r} is disabled in the catalog: "
                        f"{rec.get('enabled_blocked_by', 'no reason recorded')}")
    return rec


def normalize_realization(candidate, strict=True):
    """Validated realization block for a candidate. Never returns None.

    Accepts the versioned `_realization` block, and -- for the ledger rows and
    point files written before this envelope existed -- a bare top-level
    `substrate_carrier`, which it migrates rather than ignores.
    """
    raw = candidate.get(REALIZATION_KEY) if isinstance(candidate, dict) else None
    legacy = candidate.get("substrate_carrier") if isinstance(candidate, dict) else None
    if raw is None and legacy is None:
        return default_realization()
    if raw is None:
        raw = {"mode": "pseudo_si_base", "substrate_carrier": legacy,
               "migrated_from": "legacy top-level substrate_carrier"}
    if not isinstance(raw, dict):
        raise GateError("G-realization",
                        f"{REALIZATION_KEY} must be an object, got {type(raw).__name__}")
    if legacy is not None and raw.get("substrate_carrier") not in (None, legacy):
        raise GateError("G-realization",
                        f"conflicting carriers: {REALIZATION_KEY}."
                        f"substrate_carrier={raw.get('substrate_carrier')!r} vs "
                        f"legacy substrate_carrier={legacy!r}")

    out = dict(raw)
    out["schema"] = REALIZATION_SCHEMA
    mode = out.get("mode") or "pseudo_si_base"
    if mode not in REALIZATION_MODES:
        raise GateError("G-realization",
                        f"unknown realization mode {mode!r}; allowed: "
                        f"{list(REALIZATION_MODES)}")
    out["mode"] = mode

    if mode == "pseudo_si_base":
        carrier = out.get("substrate_carrier") or DEFAULT_CARRIER
        if carrier not in SUBSTRATE_CARRIERS:
            raise GateError("G-realization",
                            f"{carrier!r} has no measured Geant4 density; run "
                            f"stage4_probe_g4_density.py before using it as a "
                            f"density carrier")
        out["substrate_carrier"] = carrier
        out.setdefault("base_lattice_map", BASE_LATTICE_MAP)
        out["lattice_map"] = PSEUDO_LATTICE_NAME
        # If the caller states the real material's density, the carrier must
        # actually be able to represent it. Geant4 only accepts a NIST material
        # NAME, so a real density is reachable only when some NIST material
        # happens to sit within tolerance -- and a run that quietly used a
        # carrier 15% away would report the wrong crystal under the right label.
        declared = out.get("material_density_kg_m3")
        if declared is not None:
            dev = abs(SUBSTRATE_CARRIERS[carrier] - float(declared)) / float(declared)
            out["density_carrier_deviation"] = round(float(dev), 6)
            if dev > float(out.get("density_tolerance", 0.03)):
                raise GateError(
                    "G-realization",
                    f"{out.get('material_of_record', 'requested material')} has "
                    f"density {float(declared):.0f} kg/m3 but the nearest measured "
                    f"Geant4 carrier {carrier} is {SUBSTRATE_CARRIERS[carrier]:.0f} "
                    f"({dev:.1%} away, tolerance "
                    f"{float(out.get('density_tolerance', 0.03)):.0%}). Geant4 takes "
                    f"only a NIST material NAME, so this material cannot be carried "
                    f"faithfully without a registered material at its own density.")
        if strict and out["base_lattice_map"] != BASE_LATTICE_MAP:
            read_lattice_record(out["base_lattice_map"])
    elif mode == "native_g4cmp":
        material = out.get("material")
        if not material:
            raise GateError("G-realization",
                            "native_g4cmp needs `material`: the catalog key of a "
                            "real substrate with a complete G4CMP record")
        rec = _catalog_substrate(material)
        if rec.get("config_mode") != "native_g4cmp":
            raise GateError("G-realization",
                            f"substrate {material!r} declares config_mode="
                            f"{rec.get('config_mode')!r}, not 'native_g4cmp'")
        carrier = rec.get("g4_material")
        lattice_map = rec.get("lattice_map")
        if not carrier or not lattice_map:
            raise GateError("G-realization",
                            f"catalog record for {material!r} lacks g4_material or "
                            f"lattice_map")
        if carrier not in SUBSTRATE_CARRIERS:
            raise GateError("G-realization",
                            f"{material!r} needs Geant4 material {carrier!r}, whose "
                            f"density has not been measured; run "
                            f"stage4_probe_g4_density.py")
        expected = rec.get("expected_density_kg_m3")
        if expected is None:
            raise GateError("G-realization",
                            f"catalog record for {material!r} has no "
                            f"expected_density_kg_m3; refusing to simulate it")
        dev = abs(SUBSTRATE_CARRIERS[carrier] - float(expected)) / float(expected)
        if dev > 0.03:
            raise GateError("G-realization",
                            f"{material!r}: Geant4 {carrier} density "
                            f"{SUBSTRATE_CARRIERS[carrier]:.0f} differs from the "
                            f"record's {float(expected):.0f} kg/m3 by {dev:.1%}")
        if strict:
            read_lattice_record(lattice_map)
        out.update({"substrate_carrier": carrier, "lattice_map": lattice_map,
                    "base_lattice_map": lattice_map,
                    "expected_density_kg_m3": float(expected)})
    else:                                   # custom_material
        carrier = out.get("substrate_carrier")
        lattice_map = out.get("lattice_map")
        if not carrier or not lattice_map:
            raise GateError("G-realization",
                            "custom_material needs BOTH `substrate_carrier` (a "
                            "Geant4 material with a measured density) and "
                            "`lattice_map` (a complete G4CMP record). Refusing "
                            "rather than completing either from Si.")
        if carrier not in SUBSTRATE_CARRIERS:
            raise GateError("G-realization",
                            f"custom_material carrier {carrier!r} has no measured "
                            f"Geant4 density; run stage4_probe_g4_density.py")
        if strict:
            read_lattice_record(lattice_map)
        out["base_lattice_map"] = lattice_map
    return out


def realization_uses_native_record(realization):
    """True when the substrate physics comes from a real material's own record."""
    return realization.get("mode") in ("native_g4cmp", "custom_material")


# own_fields label -> decision variable it fixes.
_OWN_FIELD_VARIABLE = {
    "c11": "sub_c11", "c12": "sub_c12", "c44": "sub_c44",
    "lattice_a": "sub_lattice_a", "scat": "sub_scat", "decay": "sub_decay",
    "decayTT": "sub_decayTT",
}


def box_exempt_variables(realization):
    """Variables whose value is a MEASURED MATERIAL FACT for this realization.

    The search box is a statement about where the campaign looked, not about
    what physics is possible. Clipping a real crystal into it simulates a
    different crystal and reports it under the real one's name -- which is how
    the first projection run recorded 3C-SiC with C44 = 200 GPa instead of its
    measured 241. Facts are exempt; proposals never are.
    """
    if not realization:
        return ()
    if realization_uses_native_record(realization):
        return tuple(SUBSTRATE_MATERIAL_VARIABLES)
    own = realization.get("own_fields") or ()
    return tuple(_OWN_FIELD_VARIABLE[f] for f in own if f in _OWN_FIELD_VARIABLE)


# NIST carriers for the two films. Their density and composition are inert for
# this objective (no GetDensity call exists on the film path); they carry the
# G4MaterialPropertiesTable that the macro fills in with the scanned values.
TOP_FILM_CARRIER = "G4_Nb"
BOTTOM_FILM_CARRIER = "G4_Cu"

# Base lattice record the pseudo-material overrides. Si, because it is the
# project baseline and its record is complete.
BASE_LATTICE_MAP = "Si"
PSEUDO_LATTICE_NAME = "PseudoCubic"

# Fixed film fields that the parameter contract does not open (thicknesses, QP
# limits, the top-film lifetime slope, the bottom-film gap).
FIXED_TOP_FILM = {"ph_lifetime_slope": 0.29, "qp_limit": 3, "thickness_um": 0.075}
FIXED_BOTTOM_FILM = {"gap_eV": 0.0, "qp_limit": 3, "thickness_um": 1.0,
                     "normal_metal": True, "ph_lifetime_slope": 0.0}


# ---------------------------------------------------------------------------
# Orientation: integer Miller triples only.
#
# G4LatticePhysical::SetMillerOrientation(G4int h, G4int k, G4int l, G4double)
# takes INTEGERS. A Fibonacci-sphere direction cannot be represented and would
# truncate toward zero -- so orientation is a finite categorical design, exactly
# as the Stage 3 record concluded.
# ---------------------------------------------------------------------------
def cubic_symmetry_reduced_millers(max_index=3):
    """Unique directions under the cubic point group, sorted by |(h,k,l)|.

    Two directions are equivalent if one is a signed permutation of the other,
    so the canonical form is the sorted tuple of absolute values. Antipodal
    duplicates go with them.
    """
    seen, out = set(), []
    for h in range(0, max_index + 1):
        for k in range(0, max_index + 1):
            for l in range(0, max_index + 1):
                if h == k == l == 0:
                    continue
                if math.gcd(math.gcd(h, k), l) != 1:      # (0,0,2) == (0,0,1)
                    continue
                key = tuple(sorted((h, k, l)))
                if key in seen:
                    continue
                seen.add(key)
                out.append(key)
    out.sort(key=lambda t: (t[0] ** 2 + t[1] ** 2 + t[2] ** 2, t))
    return [list(t) for t in out]


MILLER_SET = cubic_symmetry_reduced_millers(3)


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
class Variable:
    """One continuous decision variable.

    `target` records where the value lands, so nothing in this table is a
    number without a destination:
        macro:<command>   a Geant4 UI command written into every sub-run macro
        config:<key>      a line in the pseudo-material's G4CMP lattice record
        model:interface   consumed only by interface_transmission.py
    """

    __slots__ = ("name", "unit", "baseline", "low", "high", "scale", "target",
                 "doc", "active")

    def __init__(self, name, unit, baseline, low, high, scale, target, doc,
                 active=True):
        if not low <= baseline <= high:
            raise ValueError(f"{name}: baseline {baseline} outside [{low}, {high}]")
        if scale == "log" and low <= 0:
            raise ValueError(f"{name}: log scale needs a positive lower bound")
        self.name, self.unit, self.baseline = name, unit, baseline
        self.low, self.high, self.scale = low, high, scale
        self.target, self.doc, self.active = target, doc, active

    def to_unit(self, value):
        if self.scale == "log":
            return ((math.log(value) - math.log(self.low))
                    / (math.log(self.high) - math.log(self.low)))
        return (value - self.low) / (self.high - self.low)

    def from_unit(self, u):
        u = min(1.0, max(0.0, float(u)))
        if self.scale == "log":
            return math.exp(math.log(self.low)
                            + u * (math.log(self.high) - math.log(self.low)))
        return self.low + u * (self.high - self.low)

    def __repr__(self):
        return f"Variable({self.name}, [{self.low:g}, {self.high:g}], {self.scale})"


# Bounds evidence is in the `doc` string of each variable, not in a comment
# elsewhere that can drift away from the number.
VARIABLES = [
    Variable("topfilm_vsound", "km/s", 2.444, 1.5, 6.0, "linear",
             "macro:/main/detector_param/setTopFilmVSound",
             "Longitudinal sound speed of the top ground film. Range covers the "
             "elemental superconductors: Pb 2.16, Nb 2.444, Al 3.582, Ti 4.14, "
             "Ta 4.159 km/s."),
    Variable("topfilm_gap", "eV", 1.5384e-3, 5.0e-5, 3.5e-3, "log",
             "macro:/main/detector_param/setTopFilmGap",
             "Single-QP gap Delta of the top film. Ti 0.061 meV .. Nb 1.5384 meV "
             "measured; the ceiling reaches Nb3Sn/NbN-class gaps while keeping "
             "2*Delta below E_gun = 10 meV so the ground-plane regime is constant."),
    Variable("topfilm_ph_lifetime", "ns", 0.00417, 1.0e-3, 1.0, "log",
             "macro:/main/detector_param/setTopFilmPhLifetime",
             "Pair-breaking phonon lifetime in the top film. Nb 0.00417, "
             "Ta 0.0227, Ti 0.414 ns; all three are supplied values with "
             "declared uncertainty, so the box is deliberately wider."),
    Variable("topfilm_density", "kg/m3", 8570.0, 2000.0, 20000.0, "linear",
             "model:interface",
             "Top-film mass density. Reaches Geant4 nowhere -- it enters only the "
             "acoustic impedance Z = rho*v in the interface model, and the "
             "projection metric. Ti 4540 .. Ta 16650 kg/m3."),

    Variable("bot_vsound", "km/s", 2.608, 1.5, 6.0, "linear",
             "macro:/main/detector_param/setBotVSound",
             "Longitudinal sound speed of the bottom normal-metal film. "
             "Cu 2.608, Au 3.240 km/s."),
    Variable("bot_gap_thres", "eV", 180.0e-6, 5.0e-5, 4.0e-4, "linear",
             "macro:/main/detector_param/setBotGapThres",
             "Minimum 2*Delta a phonon must carry to break pairs after "
             "downconversion in the normal film. Baseline 180 ueV; capped at "
             "2*setTopGap = 382 ueV, above which the bottom film would gate "
             "harder than the junction it feeds."),
    Variable("bot_ph_lifetime", "ns", 5.1, 0.5, 50.0, "log",
             "macro:/main/detector_param/setBotPhLifetime",
             "Phonon lifetime in the bottom film. Cu 5.1, Au 16.0 ns (Au's is "
             "sourced with a [10, 30] ns uncertainty)."),
    Variable("bot_density", "kg/m3", 8960.0, 2000.0, 22000.0, "linear",
             "model:interface",
             "Bottom-film mass density; interface model and projection only. "
             "Cu 8960, Au 19300 kg/m3."),

    Variable("sub_c11", "GPa", 165.6, 20.0, 450.0, "linear",
             "config:stiffness 1 1",
             "Cubic elastic constant. MP cubic snapshot: p5 16, p50 103, p95 342, "
             "max 570 GPa across 396 accepted materials."),
    Variable("sub_c12", "GPa", 63.9, 0.0, 250.0, "linear",
             "config:stiffness 1 2",
             "MP snapshot p5 5, p50 31.5, p95 118, max 191 GPa. Born stability "
             "couples this to C11 and is enforced as gate G1."),
    Variable("sub_c44", "GPa", 79.5, 5.0, 200.0, "linear",
             "config:stiffness 4 4",
             "MP snapshot p5 5, p50 33, p95 124, max 241 GPa. Note C11 > C44 is "
             "NOT implied by the Born conditions; mode ordering is gate G3."),
    Variable("sub_scat", "s3", 2.43e-42, 1.0e-44, 5.0e-41, "log",
             "config:scat",
             "Isotope/mass-defect scattering constant, rate ~ scat*omega^4. "
             "Shipped G4CMP records span Al2O3 0.025e-42 .. Ge 3.67e-41 s3, a "
             "factor 1470; the box brackets that range."),
    Variable("sub_decay", "s4", 7.41e-56, 5.0e-57, 5.0e-54, "log",
             "config:decay",
             "Anharmonic (three-phonon) decay constant, rate ~ decay*omega^5. "
             "Shipped records span Si 7.41e-56 .. Ge 1.6456e-54 s4."),
    Variable("sub_decayTT", "", 0.74, 0.50, 1.00, "linear",
             "config:decayTT",
             "Fraction of anharmonic decays going L -> T+T rather than L -> L+T. "
             "Shipped records span LiF 0.68 .. GaAs 0.778; parameter_set.txt "
             "allows up to 1."),

    Variable("temperature", "K", 0.0, 0.0, 0.1, "linear",
             "macro:/g4cmp/temperature",
             "Lattice temperature. parameter_set.txt bounds. Suspected inert for "
             "a monoenergetic athermal phonon gun -- tested by A/B, not assumed."),
    Variable("lattice_deg", "deg", 45.0, 0.0, 90.0, "linear",
             "macro:/main/detector_param/setLatticeDeg",
             "Rotation of the crystal about the Miller axis. 90 deg is the cubic "
             "period about a <001> axis."),

    # Inert-by-construction; written for provenance and used by the projection
    # metric, excluded from the active search until the A/B proves otherwise.
    Variable("sub_lattice_a", "Ang", 5.431, 3.0, 10.0, "linear",
             "config:cubic",
             "Conventional cubic lattice constant. Sets fBasis, which for a cubic "
             "cell affects only the Miller DIRECTION (scale-invariant) and "
             "G4CMPChargeCloud (charge carriers). MP snapshot spans 2.84 .. 10.7 "
             "Ang. Inactive by default -- see MODULE NOTES.",
             active=False),
]

VARIABLES_BY_NAME = {v.name: v for v in VARIABLES}


class Space:
    """The searchable box: active continuous variables + the Miller categorical."""

    def __init__(self, variables=None, millers=None, include_inactive=False,
                 constraints=None):
        variables = variables if variables is not None else VARIABLES
        self.all_variables = list(variables)
        self.variables = [v for v in self.all_variables if v.active or include_inactive]
        self.names = [v.name for v in self.variables]
        self.millers = [list(m) for m in (millers if millers is not None else MILLER_SET)]
        self.n_cont = len(self.variables)
        self.n_cat = len(self.millers)
        # Engineering constraints (audit P3). Empty by default, so a campaign
        # that declares none behaves exactly as before. Declared in the contract
        # under `space.constraints`, and therefore hashed with it: a campaign
        # run with a T_c floor is a different campaign, not the same one
        # re-reported.
        self.constraints = dict(constraints or {})

    # -- points -------------------------------------------------------------
    def baseline_point(self):
        p = {v.name: v.baseline for v in self.all_variables}
        p["miller"] = [0, 0, 1]
        return p

    def complete(self, point):
        """Fill any variable the caller left out with its baseline value.

        The realization block travels with the point. Dropping it here is
        exactly the P0 defect: `complete()` is called by `candidate_payload()`,
        by `stage4_confirm.collect_points()` and by the projection, so a
        normaliser that keeps only the variable table silently converts every
        real-material request back into the Si-carried default.
        """
        out = {v.name: float(point.get(v.name, v.baseline)) for v in self.all_variables}
        miller = point.get("miller", [0, 0, 1])
        out["miller"] = [int(x) for x in miller]
        real = point.get(REALIZATION_KEY) if isinstance(point, dict) else None
        if real is None and isinstance(point, dict) and point.get("substrate_carrier"):
            real = normalize_realization(point)
        if real is not None:
            out[REALIZATION_KEY] = json.loads(json.dumps(real, sort_keys=True))
        return out

    def to_unit(self, point):
        return np.array([v.to_unit(float(point[v.name])) for v in self.variables],
                        dtype=float)

    def miller_index(self, point):
        target = [int(x) for x in point["miller"]]
        for i, m in enumerate(self.millers):
            if m == target:
                return i
        raise GateError("G-orientation", f"miller {target} is not in the allowed set")

    def from_unit(self, u, miller_index=0, base=None):
        point = dict(base) if base else self.baseline_point()
        for v, uu in zip(self.variables, np.asarray(u, dtype=float).ravel()):
            point[v.name] = v.from_unit(uu)
        point["miller"] = list(self.millers[int(miller_index) % self.n_cat])
        return point

    def sample(self, rng, n=1):
        """Uniform draws in the transformed box (log where declared)."""
        u = rng.random((n, self.n_cont))
        idx = rng.integers(0, self.n_cat, size=n)
        return [self.from_unit(u[i], idx[i]) for i in range(n)]

    def clip(self, point):
        out = dict(point)
        for v in self.all_variables:
            out[v.name] = float(min(v.high, max(v.low, float(out.get(v.name, v.baseline)))))
        return out

    def in_bounds(self, point, exempt=()):
        """Box check. `exempt` names variables whose value is a material fact
        rather than a proposal (see `precheck`)."""
        bad = []
        exempt = set(exempt or ())
        for v in self.variables:
            if v.name in exempt:
                continue
            x = float(point.get(v.name, v.baseline))
            if not (v.low - 1e-12 <= x <= v.high + 1e-12):
                bad.append(f"{v.name}={x:g} outside [{v.low:g}, {v.high:g}]")
        return (not bad), bad

    def describe(self):
        rows = [f"{'variable':22s} {'unit':6s} {'baseline':>11s} {'low':>11s} "
                f"{'high':>11s}  scale"]
        for v in self.variables:
            rows.append(f"{v.name:22s} {v.unit:6s} {v.baseline:11.4g} {v.low:11.4g} "
                        f"{v.high:11.4g}  {v.scale}")
        rows.append(f"{'miller':22s} {'':6s} {'[0,0,1]':>11s}  categorical, "
                    f"{self.n_cat} symmetry-reduced directions")
        return "\n".join(rows)


DEFAULT_SPACE = Space()


# ---------------------------------------------------------------------------
# Derivation and gates
# ---------------------------------------------------------------------------
def derive(point, carrier="G4_Si", junction=None, si_reference=None,
           density_kg_m3=None):
    """Everything the simulation consumes that is not proposed directly.

    Raises GateError on any hard-gate violation, always BEFORE a Geant4 process
    exists. Returns a dict of derived values plus the gate diagnostics.

    `density_kg_m3` is never guessed: it defaults to the MEASURED density of the
    named Geant4 carrier, which is the density G4CMP will actually use
    (`G4LatticeManager::LoadLattice` -> `SetDensity(Mat->GetDensity())`). It is
    an explicit argument only so a caller can assert it, never so it can differ.
    """
    p = dict(point)
    if carrier not in SUBSTRATE_CARRIERS:
        raise GateError("G-carrier",
                        f"{carrier!r} has no measured Geant4 density; run "
                        f"stage4_probe_g4_density.py before using it")
    density = SUBSTRATE_CARRIERS[carrier]
    if density_kg_m3 is not None and abs(float(density_kg_m3) - density) / density > 1e-9:
        raise GateError("G-carrier",
                        f"caller asked for {float(density_kg_m3):.1f} kg/m3 but "
                        f"Geant4 {carrier} is {density:.1f} kg/m3; G4CMP would use "
                        f"the Geant4 value while the resolver used the other one")

    c11, c12, c44 = float(p["sub_c11"]), float(p["sub_c12"]), float(p["sub_c44"])

    # G1 -- Born stability for a cubic crystal. NOTE this does not imply
    # C11 > C44, so it does not imply mode ordering; G3 is a separate gate.
    if not (c11 - c12 > 0 and c11 + 2 * c12 > 0 and c44 > 0):
        raise GateError("G1", f"Born-unstable cubic tensor C11={c11:g} C12={c12:g} "
                              f"C44={c44:g} GPa")

    # G2 -- Christoffel positivity (derive_cubic_sound_speeds raises on a
    # non-positive eigenvalue) and the spherical-average speeds.
    try:
        vsound, vtrans = harness.derive_cubic_sound_speeds(c11, c12, c44, density=density)
    except ValueError as exc:
        raise GateError("G2", str(exc)) from exc

    # G3 -- mode ordering. G4CMP's group-velocity map assumes v_L > v_T and
    # indexes off the end of its table otherwise: a mid-run SIGSEGV with a
    # zero-byte hits file, 31/31 affected design points on the first branch.
    if not 0 < vtrans < vsound:
        raise GateError("G3", f"vtrans={vtrans:.1f} not in (0, vsound={vsound:.1f})")

    # G4 -- interface probabilities from the candidate's own (rho, v) pairs.
    substrate = {"density_kg_m3": density, "vsound_m_s": vsound, "vtrans_m_s": vtrans}
    si_reference = si_reference or _si_reference()
    junction = junction or {"g4_material": "G4_Al", "vsound_m_s": 3582.0}
    try:
        abs_values, abs_diag = resolve_all_interfaces(
            substrate, junction,
            {"g4_material": TOP_FILM_CARRIER,
             "vsound_m_s": float(p["topfilm_vsound"]) * 1000.0,
             "density_kg_m3": float(p["topfilm_density"])},
            {"g4_material": BOTTOM_FILM_CARRIER,
             "vsound_m_s": float(p["bot_vsound"]) * 1000.0,
             "density_kg_m3": float(p["bot_density"])},
            si_reference)
    except InterfaceModelError as exc:
        raise GateError("G4", f"interface model failed: {exc}") from exc
    for key, value in abs_values.items():
        if not 0.0 <= value <= 1.0:
            raise GateError("G4", f"{key}={value:g} outside [0,1]")

    return {
        "substrate_density_kg_m3": density,
        "substrate_density_source": f"Geant4 {carrier}",
        "g4_material_name": carrier,
        "lattice_map_name": PSEUDO_LATTICE_NAME,
        "base_lattice_map": BASE_LATTICE_MAP,
        "config_mode": "pseudo_material_over_" + BASE_LATTICE_MAP,
        "vsound_m_s": round(vsound, 6),
        "vtrans_m_s": round(vtrans, 6),
        "anisotropy": round(2.0 * c44 / (c11 - c12), 6),
        "impedance_substrate": round(density * (vsound + 2.0 * vtrans) / 3.0, 3),
        "setTopAbs": round(abs_values["setTopAbs"], 6),
        "setTopFilmAbs": round(abs_values["setTopFilmAbs"], 6),
        "setBotAbs": round(abs_values["setBotAbs"], 6),
        "interface_model": "baseline_calibrated_effective_AMM",
        "interface_diagnostics": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                                      for kk, vv in d.items()}
                                  for k, d in abs_diag.items()},
    }


_SI_REF_CACHE = {}


def _si_reference():
    """Si substrate record for the interface model's calibration ratio."""
    if not _SI_REF_CACHE:
        rho = SUBSTRATE_CARRIERS["G4_Si"]
        vs, vt = harness.derive_cubic_sound_speeds(165.6, 63.9, 79.5, density=rho)
        _SI_REF_CACHE.update({"density_kg_m3": rho, "vsound_m_s": vs, "vtrans_m_s": vt})
    return dict(_SI_REF_CACHE)


def precheck_cheap(point, space=None):
    """Microsecond feasibility filter: bounds, Born stability, G7, orientation.

    Exists because the full check costs ~22 ms (the Christoffel average runs an
    eigen-decomposition over the whole Fibonacci direction set), and an
    acquisition maximizer wants to reject thousands of proposals per second.
    G2/G3/G4 are then re-checked exactly on the handful of points that survive
    into a batch -- they are almost never the binding constraint (G1 rejects
    ~29% of uniform draws; G2/G3 essentially never fire once G1 passes), but
    "almost never" is not "never", so nothing is skipped, only deferred.
    """
    space = space or DEFAULT_SPACE
    ok, bad = space.in_bounds(point)
    if not ok:
        return False, "; ".join(bad)
    c11, c12, c44 = (float(point["sub_c11"]), float(point["sub_c12"]),
                     float(point["sub_c44"]))
    if not (c11 - c12 > 0 and c11 + 2 * c12 > 0 and c44 > 0):
        return False, f"[G1] Born-unstable C11={c11:g} C12={c12:g} C44={c44:g}"
    if not 0 < float(point["bot_gap_thres"]) <= 2 * 191.0e-6 + 1e-12:
        return False, f"[G7] bot_gap_thres={point['bot_gap_thres']:g} eV out of range"
    ok, why = check_engineering_constraints(point, getattr(space, "constraints", None))
    if not ok:
        return False, why
    return True, None


def check_engineering_constraints(point, constraints):
    """G8 -- declared fabrication limits the QP objective cannot express.

    Junction QPs alone can be reduced by installing a low-gap ground plane next
    to the qubits: it out-competes the junctions for phonons. That is a real
    mechanism, and it is also a design that hosts its own thermal
    quasiparticles and has far higher microwave loss than Nb or Ta -- neither of
    which this objective can see (audit P3). A campaign that cares about the
    trade-off declares the floor here instead of discovering it afterwards.

    Empty by default. Every limit is opt-in and hashed with the contract.
    """
    if not constraints:
        return True, None
    gap = float(point["topfilm_gap"])
    lo = constraints.get("topfilm_gap_min_eV")
    if lo is not None and gap < float(lo) - 1e-15:
        return False, (f"[G8] topfilm_gap={gap:g} eV is below the declared "
                       f"fabrication floor {float(lo):g} eV "
                       f"(Tc {gap / (1.764 * 8.617333262e-5):.3f} K vs "
                       f"{float(lo) / (1.764 * 8.617333262e-5):.3f} K)")
    tc_lo = constraints.get("topfilm_tc_min_K")
    if tc_lo is not None:
        tc = gap / (1.764 * 8.617333262e-5)
        if tc < float(tc_lo) - 1e-12:
            return False, (f"[G8] ground-plane Tc={tc:.3f} K is below the declared "
                           f"minimum {float(tc_lo):.3f} K")
    ratio_lo = constraints.get("topfilm_gap_over_junction_min")
    if ratio_lo is not None and gap / 191.0e-6 < float(ratio_lo) - 1e-12:
        return False, (f"[G8] 2*Delta_film / 2*Delta_Al = {gap / 191.0e-6:.3f} is "
                       f"below the declared minimum {float(ratio_lo):.3f}; the "
                       f"ground plane would absorb across the whole band the "
                       f"junction uses")
    return True, None


def precheck(point, space=None, carrier="G4_Si", realization=None,
             density_kg_m3=None):
    """Cheap feasibility test for an optimizer proposal.

    Returns (ok, reason). Pure Python, no Geant4, no contract -- so an optimizer
    can reject and re-propose thousands of times per second.

    `realization` decides whether the search box is a hard constraint. For a
    proposal (pseudo_si_base) it is: the box IS the campaign. For a real
    material re-simulated at its own measured tensor it is not -- SiC's
    C44 = 241 GPa is a fact, and clipping it to the box ceiling of 200 (which is
    what the first projection run did) silently simulates a different crystal.
    The physics gates G1-G4 still apply to every mode.
    """
    space = space or DEFAULT_SPACE
    point = space.complete(point)
    ok, bad = space.in_bounds(point, exempt=box_exempt_variables(realization))
    if not ok:
        return False, "; ".join(bad)
    try:
        space.miller_index(point)
    except GateError as exc:
        return False, str(exc)
    # G7 -- the bottom film must not gate harder than the junction it feeds.
    if not 0 < float(point["bot_gap_thres"]) <= 2 * 191.0e-6 + 1e-12:
        return False, f"[G7] bot_gap_thres={point['bot_gap_thres']:g} eV not in (0, 2*setTopGap]"
    # G8 -- declared fabrication constraints, if the campaign declared any.
    ok, why = check_engineering_constraints(point, getattr(space, "constraints", None))
    if not ok:
        return False, why
    try:
        derive(point, carrier=carrier, density_kg_m3=density_kg_m3)
    except GateError as exc:
        return False, str(exc)
    return True, None


# ---------------------------------------------------------------------------
# Resolver -- the shape stage3_trial_runner.evaluate() consumes
# ---------------------------------------------------------------------------
def resolve(contract, candidate, space=None):
    """resolve(contract, candidate) -> (resolved, derived)

    `candidate` is a plain dict of natural-unit property values plus `miller`,
    i.e. exactly what the optimizer proposes and what the ledger stores.

    Raises ContractError/GateError, both of which the driver records as
    `constraint_rejected` -- never as an observation, never as zero QPs.
    """
    from stage3_contract import ContractError

    space = space or DEFAULT_SPACE
    point = space.complete(candidate)
    realization = normalize_realization(candidate)
    carrier = realization["substrate_carrier"]

    # Real-material modes take the substrate physics FROM the material's own
    # lattice record. Anything the optimizer happened to propose for those
    # variables is overwritten and the substitution is recorded, so a projected
    # material can never be silently simulated with the target's constants.
    substituted = {}
    if realization_uses_native_record(realization):
        native = native_substrate_values(realization["lattice_map"])
        for name, value in native.items():
            proposed = float(point.get(name, value))
            # Relative, because scat/decay live at 1e-42 / 1e-56 -- an absolute
            # tolerance would call every substitution a no-op and the log would
            # claim nothing was overridden.
            if not math.isclose(proposed, value, rel_tol=1e-9, abs_tol=0.0):
                substituted[name] = {"proposed": proposed, "record": value}
            point[name] = value

    ok, reason = precheck(point, space=space, carrier=carrier,
                          realization=realization)
    if not ok:
        raise GateError("precheck", reason)

    derived = derive(point, carrier=carrier)

    top = {
        "g4_material": TOP_FILM_CARRIER,
        "gap_eV": float(point["topfilm_gap"]),
        "vsound_km_s": float(point["topfilm_vsound"]),
        "ph_lifetime_ns": float(point["topfilm_ph_lifetime"]),
        "density_kg_m3": float(point["topfilm_density"]),
        "provenance": "Stage 4 pseudo-film: scanned property vector, not a "
                      "catalog material. G4_Nb carries the properties table only.",
        **FIXED_TOP_FILM,
    }
    bot = {
        "g4_material": BOTTOM_FILM_CARRIER,
        "gap_threshold_eV": float(point["bot_gap_thres"]),
        "vsound_km_s": float(point["bot_vsound"]),
        "ph_lifetime_ns": float(point["bot_ph_lifetime"]),
        "density_kg_m3": float(point["bot_density"]),
        "provenance": "Stage 4 pseudo-film: scanned property vector, not a "
                      "catalog material. G4_Cu carries the properties table only.",
        **FIXED_BOTTOM_FILM,
    }
    if realization_uses_native_record(realization):
        sub_provenance = (
            f"Stage 4 {realization['mode']} substrate: the COMPLETE G4CMP record "
            f"for {realization['lattice_map']!r} -- its own tensor, dyn, scat, "
            f"decay, decayTT, DOS and Debye -- at the measured Geant4 density of "
            f"{carrier} ({SUBSTRATE_CARRIERS[carrier]:.0f} kg/m3). No field is "
            f"borrowed from Si.")
    elif realization == default_realization():
        # Byte-for-byte the pre-audit string. `derived` is inside the cache key,
        # so rewording it would mint a new key for every one of the 92 valid
        # pseudo-material trials and silently discard the campaign.
        sub_provenance = ("Stage 4 pseudo-substrate: the Si G4CMP record with "
                          "stiffness/scat/decay/decayTT/cubic overridden. dyn, "
                          "LDOS/STDOS/FTDOS and Debye remain Si's.")
    else:
        sub_provenance = (
            f"Stage 4 pseudo-substrate: the {BASE_LATTICE_MAP} G4CMP record with "
            f"stiffness/scat/decay/decayTT/cubic overridden, density carried by "
            f"{carrier} ({SUBSTRATE_CARRIERS[carrier]:.0f} kg/m3). dyn, "
            f"LDOS/STDOS/FTDOS and Debye remain {BASE_LATTICE_MAP}'s.")
    sub = {
        "g4_material": carrier,
        "lattice_map": realization["lattice_map"],
        "base_lattice_map": realization["base_lattice_map"],
        "expected_density_kg_m3": SUBSTRATE_CARRIERS[carrier],
        "c11_GPa": float(point["sub_c11"]),
        "c12_GPa": float(point["sub_c12"]),
        "c44_GPa": float(point["sub_c44"]),
        "config_mode": derived["config_mode"],
        "realization": realization,
        "provenance": sub_provenance,
    }

    # G5/G6 -- excitation chain and regime. topfilm_gap is a decision variable
    # now, so the regime flag varies across candidates and must be checked per
    # candidate rather than once per campaign.
    gate = contract.check_excitation_thresholds(top_film_gap_eV=top["gap_eV"])
    declared = contract.fixed.get("ground_plane_active_absorber", True)
    if bool(gate["ground_plane_active_absorber"]) != bool(declared):
        raise GateError(
            "G6",
            f"2*topfilm_gap = {2 * top['gap_eV'] * 1e6:.1f} ueV puts this candidate "
            f"in the {'active' if gate['ground_plane_active_absorber'] else 'transparent'} "
            f"ground-plane regime, but the campaign declared "
            f"{'active' if declared else 'transparent'}. Comparing across regimes "
            f"changes what the objective measures.")

    # The realization decides which lattice record is written and whether any
    # field of it is overridden at all. In native/custom mode the record is
    # copied verbatim (config_overrides empty), which is what makes the run a
    # real-material run rather than a Si pseudo-material wearing its numbers.
    native = realization_uses_native_record(realization)
    derived.update({
        "lattice_map_name": realization["lattice_map"],
        "base_lattice_map": realization["base_lattice_map"],
        "config_mode": (f"native_g4cmp:{realization['lattice_map']}" if native
                        else "pseudo_material_over_" + realization["base_lattice_map"]),
        "excitation_gate": {k: (round(v, 9) if isinstance(v, float) else v)
                            for k, v in gate.items()},
        "point": {k: (list(v) if isinstance(v, list) else float(v))
                  for k, v in point.items() if not k.startswith("_")},
        "config_overrides": ({} if native else _config_overrides(point)),
        "macro_overrides": _macro_overrides(point),
        "space_id": "stage4_property_v1",
        "provenance": {"substrate": sub["provenance"], "top_film": top["provenance"],
                       "bottom_film": bot["provenance"]},
    })
    sub["config_mode"] = derived["config_mode"]
    # Realization metadata enters `derived` -- and therefore the cache key --
    # ONLY when it is not the plain Si-carried default. A default candidate must
    # hash exactly as it did before this envelope existed, or the fix would
    # invalidate the 92 valid search trials it was never meant to touch.
    if realization != default_realization():
        derived.update({
            "realization": realization,
            "realization_substitutions": substituted,
            "box_exempt_variables": list(box_exempt_variables(realization)),
        })
    if not isinstance(contract, object):        # pragma: no cover - typing guard
        raise ContractError("contract expected")
    return {"substrate": sub, "top_film": top, "bottom_film": bot}, derived


def _config_overrides(point):
    """Lattice-record lines the pseudo-material rewrites, with their units.

    Everything not listed here stays at the base record's value -- which is the
    definition of the pseudo-material and is stamped into the generated file.
    """
    return {
        "cubic ": [float(point["sub_lattice_a"]), " Ang"],
        "stiffness 1 1 ": [float(point["sub_c11"]), " GPa"],
        "stiffness 1 2 ": [float(point["sub_c12"]), " GPa"],
        "stiffness 4 4 ": [float(point["sub_c44"]), " GPa"],
        "scat ": [float(point["sub_scat"]), " s3"],
        "decay ": [float(point["sub_decay"]), " s4"],
        "decayTT ": [float(point["sub_decayTT"]), ""],
    }


def _macro_overrides(point):
    """Per-candidate macro commands applied after the standard settings block."""
    miller = " ".join(str(int(v)) for v in point["miller"])
    return {
        "/g4cmp/temperature ": f"{float(point['temperature'])} K",
        "/main/detector_param/setLatticeDeg ": f"{float(point['lattice_deg'])}",
        "/main/detector_param/setMiller ": miller,
    }


# ---------------------------------------------------------------------------
# Comparison features -- the space the projection metric lives in (sec 8.1)
# ---------------------------------------------------------------------------
def comparison_features(point, derived=None, carrier="G4_Si"):
    """Physical features a real material can be scored against.

    Deliberately NOT the raw decision vector: two materials with different
    densities can have identical phonon behaviour, so the comparison is done in
    (v_L, v_T, anisotropy, impedance, phonon constants) space where a real
    material's own density has already been folded in.
    """
    point = DEFAULT_SPACE.complete(point)
    d = derived or derive(point, carrier=carrier)
    return {
        "sub_vsound_m_s": d["vsound_m_s"],
        "sub_vtrans_m_s": d["vtrans_m_s"],
        "sub_anisotropy": d["anisotropy"],
        "sub_impedance": d["impedance_substrate"],
        "sub_scat": float(point["sub_scat"]),
        "sub_decay": float(point["sub_decay"]),
        "sub_decayTT": float(point["sub_decayTT"]),
        "topfilm_vsound_m_s": float(point["topfilm_vsound"]) * 1000.0,
        "topfilm_gap_eV": float(point["topfilm_gap"]),
        "topfilm_ph_lifetime_ns": float(point["topfilm_ph_lifetime"]),
        "topfilm_impedance": float(point["topfilm_density"]) * float(point["topfilm_vsound"]) * 1000.0,
        "bot_vsound_m_s": float(point["bot_vsound"]) * 1000.0,
        "bot_ph_lifetime_ns": float(point["bot_ph_lifetime"]),
        "bot_impedance": float(point["bot_density"]) * float(point["bot_vsound"]) * 1000.0,
    }


if __name__ == "__main__":
    space = DEFAULT_SPACE
    print(f"Stage 4 property space: {space.n_cont} continuous + 1 categorical "
          f"({space.n_cat} directions)\n")
    print(space.describe())
    print("\nMiller set:", space.millers)
    base = space.baseline_point()
    ok, reason = precheck(base)
    print(f"\nbaseline feasible: {ok} {reason or ''}")
    d = derive(base)
    print(f"  vsound  {d['vsound_m_s']:.1f} m/s   vtrans {d['vtrans_m_s']:.1f} m/s")
    print(f"  setTopAbs {d['setTopAbs']:.6f}  setTopFilmAbs {d['setTopFilmAbs']:.6f}  "
          f"setBotAbs {d['setBotAbs']:.6f}")
    rng = np.random.default_rng(0)
    pts = space.sample(rng, 2000)
    good = sum(1 for p in pts if precheck(p)[0])
    print(f"\nfeasible fraction of 2000 uniform draws: {good / len(pts):.1%}")
