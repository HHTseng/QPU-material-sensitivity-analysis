"""Stage 3 single-candidate trial evaluator.

    evaluate(contract, candidate, fidelity, seed_bank_id) -> TrialResult

This is the Stage 3 orchestration layer, not another mode inside the Morris
runner. It reuses the parts of the existing harness that are candidate-agnostic
-- macro generation helpers, the derived-speed calculation, the memory guard,
and the Stage 2 QP scoring -- and adds what optimization needs: planned
identities, a cache key, a restartable ledger, and a strict completeness rule.

Scope of this slice: **Si/Nb/Cu baseline only.** There is deliberately no
material science here. `resolve_candidate()` raises for anything else and names
what has to exist first (density propagation, candidate-aware config overrides,
the interface-transmission model). Every later checklist item is then a resolver
change rather than an evaluator change.

Rules that are not negotiable, and why:

* A missing or failed sub-run is never scored as zero QPs.
* An incomplete scenario set is never scored at all. The injection sites are a
  fixed spatial quadrature with 9.18x site-to-site spread, and sub-run failure
  correlates with the candidate, so a partial set is biased toward whichever
  sites survived -- in the flattering direction.
* Completeness is judged against the identities planned before launch, never
  against whatever happens to be on disk.

Usage:
    python stage3_trial_runner.py --contract stage3_config.yaml
    python stage3_trial_runner.py --contract stage3_config.yaml --replay   # exit check
"""

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd
from scipy.stats import qmc

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from stage3_contract import load_contract, ContractError            # noqa: E402
from stage3_ledger import (                                          # noqa: E402
    Ledger, compute_cache_key,
    STATUS_SUCCESS, STATUS_SUCCESS_ZERO, STATUS_TIMEOUT, STATUS_MEMORY_KILLED,
    STATUS_MACRO_ABORTED, STATUS_SIM_FAILED, STATUS_TEARDOWN_SEGV,
    STATUS_CORRUPT, STATUS_INCOMPLETE_SET, STATUS_RUNNING,
)
from sensitivity_utils import replace_line, find_macro_value, format_config_entry  # noqa: E402
from sensitivity_memguard import MemoryGuard                          # noqa: E402

# How often a running trial says it is still alive. Small enough that a stale
# trial is obvious within minutes, large enough to be free next to a 29-hour
# Geant4 job.
HEARTBEAT_SECONDS = float(os.environ.get("STAGE4_HEARTBEAT_SECONDS", 120))

# A transient SQLite lock must not end the heartbeat: a silent stop turns a live
# 29-hour job into a stale-heartbeat false positive, which is exactly the reading
# that gets real work killed.
HEARTBEAT_MAX_FAILURES = int(os.environ.get("STAGE4_HEARTBEAT_MAX_FAILURES", 6))
HEARTBEAT_MAX_BACKOFF = float(os.environ.get("STAGE4_HEARTBEAT_MAX_BACKOFF", 900))

# Environment variables the common batch schedulers set. Recorded so a trial can
# be traced back to the job that ran it even after the process is gone.
_SCHEDULER_VARS = ("SLURM_JOB_ID", "PBS_JOBID", "LSB_JOBID", "SGE_JOB_ID",
                   "JOB_ID", "TMUX_PANE", "STY")


def proc_pid_namespace_ok():
    """Is /proc showing OUR pid namespace?

    In a container whose /proc is the host's, `os.getpid()` returns a namespace
    pid (7) while `/proc/self/stat` reports the host pid (35492). Every lookup
    keyed on a pid we hold -- our own, or a child's -- then reads a DIFFERENT
    process or none at all. Code that assumes the mapping is silently wrong
    rather than loudly broken, which is the dangerous kind.

    Cached: the answer cannot change within a process.
    """
    global _PROC_NS_OK
    if _PROC_NS_OK is None:
        try:
            with open("/proc/self/stat", "rb") as handle:
                _PROC_NS_OK = int(handle.read().split(b" ", 1)[0]) == os.getpid()
        except (OSError, ValueError, IndexError):
            _PROC_NS_OK = False
    return _PROC_NS_OK


_PROC_NS_OK = None


def _scheduler_job_id():
    for var in _SCHEDULER_VARS:
        value = os.environ.get(var)
        if value:
            return f"{var}={value}"
    return None

import stage1_run_simulations as harness                              # noqa: E402
from stage2_compute_QPs import calculate_QPs                          # noqa: E402
from interface_transmission import (                                  # noqa: E402
    resolve_all_interfaces, validation_report, InterfaceModelError)


# --------------------------------------------------------------------------
# Material records. A curated catalog replaces these dicts; the SHAPE is what
# `material_resolver.py` must produce, so the evaluator will not change when the
# catalog arrives.
#
# `config_mode: native_g4cmp` means the candidate's own complete G4CMP lattice
# record is used verbatim -- its lattice constant, tensor, dyn, scat, decay,
# decayTT, DOS fractions and Debye. The legacy FIXED_CONFIG_COMMANDS (Si's dyn
# and Debye 15 THz) are NOT applied on this path; they stay on the old Si
# sensitivity path only. Mixing a candidate tensor with Si's third-order
# elasticity or Debye would be a pseudo-material.
# --------------------------------------------------------------------------
def _load_catalog(path=None):
    """Curated catalog -> the record shape the resolver already expects.

    Disabled records are kept, not dropped: the resolver then reports WHY a
    material is unavailable instead of pretending it does not exist.
    """
    import yaml
    path = path or os.environ.get("STAGE3_CATALOG",
                                  os.path.join(HERE, "material_catalog.yaml"))
    if not os.path.isfile(path):
        return None
    with open(path) as handle:
        raw = yaml.safe_load(handle) or {}
    return raw


_CATALOG = _load_catalog()


def _catalog_section(name):
    if not _CATALOG:
        return {}
    return {k: dict(v) for k, v in (_CATALOG.get(name) or {}).items()}


_FALLBACK_SUBSTRATES = {
    "Si": {
        "g4_material": "G4_Si",
        "lattice_map": "Si",
        "expected_density_kg_m3": 2329.0,
        "c11_GPa": 165.6, "c12_GPa": 63.9, "c44_GPa": 79.5,
        "native_vsound_m_s": 9000.0, "native_vtrans_m_s": 5400.0,
        "config_mode": "native_g4cmp",
        "provenance": "G4CMP CrystalMaps/Si/config.txt; density = Geant4 G4_Si (2.330 g/cm3)",
    },
    "Ge": {
        "g4_material": "G4_Ge",
        "lattice_map": "Ge",
        "expected_density_kg_m3": 5323.0,
        "c11_GPa": 126.0, "c12_GPa": 44.0, "c44_GPa": 67.0,
        "native_vsound_m_s": 5324.2077, "native_vtrans_m_s": 3258.7879,
        "config_mode": "native_g4cmp",
        "provenance": ("G4CMP CrystalMaps/Ge/config.txt; density = Geant4 G4_Ge "
                       "(5.323 g/cm3, measured from a live run, not a table)"),
    },
}
_FALLBACK_TOP_FILMS = {
    "Nb": {"g4_material": "G4_Nb", "gap_eV": 1.5384e-3, "vsound_km_s": 2.444,
           "ph_lifetime_ns": 0.00417, "ph_lifetime_slope": 0.29, "qp_limit": 3,
           "thickness_um": 0.075, "provenance": "harness baseline"},
}
_FALLBACK_BOTTOM_FILMS = {
    "Cu": {"g4_material": "G4_Cu", "gap_eV": 0.0, "gap_threshold_eV": 180e-6,
           "vsound_km_s": 2.608, "ph_lifetime_ns": 5.1, "qp_limit": 3,
           "thickness_um": 1.0, "normal_metal": True, "provenance": "harness baseline"},
}
SUBSTRATES = _catalog_section("substrates") or _FALLBACK_SUBSTRATES
TOP_FILMS = _catalog_section("top_films") or _FALLBACK_TOP_FILMS
BOTTOM_FILMS = _catalog_section("bottom_films") or _FALLBACK_BOTTOM_FILMS

JUNCTION_FILM = {"g4_material": "G4_Al", "vsound_km_s": 3.582,
                 "provenance": "fixed Al junction"}

# Geant4 NIST densities for the substrate materials, used to verify that the
# density the resolver derived speeds with is the density G4CMP actually used.
# G4LatticeManager::LoadLattice does newLat->SetDensity(Mat->GetDensity()), so
# the G4Material IS the density inside the phonon kinematics -- a resolver-side
# density that disagrees with it would be an internally inconsistent candidate.
G4_MATERIAL_DENSITY_KG_M3 = {
    "G4_Si": 2330.0,                 # measured from a live Geant4 run
    "G4_Ge": 5323.0,                 # measured from a live Geant4 run
    "G4_GALLIUM_ARSENIDE": 5310.0,   # measured from a live Geant4 run
}

# Fractional tolerances, declared here rather than inline.
DENSITY_TOLERANCE = 0.005      # 0.5%, per the checklist
# 2% on the derived-vs-native speed reconstruction. Measured convention spread
# between the spherical Christoffel average and the shipped scalar values is
# 0.2-0.6% (Si) and 0.8-1.0% (Ge); the failure this guards against -- using the
# wrong density -- is ~50% (Ge tensor with Si density gives 7966 vs 5324 m/s).
# The tolerance therefore separates "averaging convention" from "wrong material"
# by more than an order of magnitude.
DERIVED_SPEED_TOLERANCE = 0.02


@dataclass
class TrialResult:
    trial_id: str
    cache_key: str
    status: str
    candidate: dict
    derived: dict
    fidelity: str
    events_total: int
    events_per_sub_run: int
    n_positions: int
    n_replicas: int
    total_qps: float = None
    qps_per_primary: float = None
    per_electrode_qps: list = field(default_factory=list)
    runtime_s: float = None
    peak_rss_gb: float = None
    failure_reason: str = None
    run_dir: str = None
    cached: bool = False
    blocks: list = field(default_factory=list)      # per (position, replica)

    @property
    def is_observation(self):
        return self.status in (STATUS_SUCCESS, STATUS_SUCCESS_ZERO)


# --------------------------------------------------------------------------
# Resolver (baseline-only slice)
# --------------------------------------------------------------------------
def resolve_candidate(contract, candidate, debye_override_THz=None):
    """Resolve one (substrate, top film, bottom film) triplet.

    Every value the simulation consumes is derived here from the candidate's own
    record. Nothing is borrowed from another material, and every gate below
    fails BEFORE Geant4 runs.
    """
    sub, top, bot = candidate["substrate"], candidate["top_ground_film"], candidate["bottom_film"]
    for name, table, what in ((sub, SUBSTRATES, "substrate"),
                              (top, TOP_FILMS, "top_ground_film"),
                              (bot, BOTTOM_FILMS, "bottom_film")):
        if name not in table:
            raise ContractError(
                f"{what}={name!r} has no material record (available: {sorted(table)}). "
                f"A record needs, at minimum: Geant4 material name and density, "
                f"G4CMP lattice map, elastic constants with a declared convention, "
                f"and provenance for every cryogenic value. Partial records are "
                f"refused rather than completed from another material."
            )
    s_rec, t_rec, b_rec = SUBSTRATES[sub], TOP_FILMS[top], BOTTOM_FILMS[bot]

    for layer, name, record in (("substrate", sub, s_rec), ("top_ground_film", top, t_rec),
                                ("bottom_film", bot, b_rec)):
        if record.get("enabled") is False:
            raise ContractError(
                f"{layer}={name!r} is in the catalog but disabled: "
                f"{record.get('enabled_blocked_by', 'no reason recorded')}. "
                f"Supply the missing value with provenance and set enabled: true. "
                f"It is deliberately NOT defaulted from another material."
            )

    # Required fields, checked by name so a partial record fails with the field
    # that is missing rather than a bare KeyError three frames deeper.
    required = {
        "substrate": (s_rec, ("g4_material", "lattice_map", "expected_density_kg_m3",
                              "c11_GPa", "c12_GPa", "c44_GPa", "native_vsound_m_s",
                              "native_vtrans_m_s", "config_mode", "provenance")),
        "top_ground_film": (t_rec, ("g4_material", "gap_eV", "vsound_km_s",
                                    "ph_lifetime_ns", "qp_limit", "thickness_um",
                                    "density_kg_m3", "provenance")),
        "bottom_film": (b_rec, ("g4_material", "gap_eV", "gap_threshold_eV",
                                "vsound_km_s", "ph_lifetime_ns", "qp_limit",
                                "thickness_um", "density_kg_m3", "provenance")),
    }
    for layer, (record, fields) in required.items():
        absent = [f for f in fields if record.get(f) is None]
        if absent:
            raise ContractError(
                f"{layer} record is incomplete: missing {absent}. Resolution fails "
                f"rather than borrowing another material's value for the gap."
            )

    if s_rec.get("config_mode") != "native_g4cmp":
        raise ContractError(
            f"substrate {sub!r} declares config_mode="
            f"{s_rec.get('config_mode')!r}; only 'native_g4cmp' is supported. A "
            f"curated override must supply a coherent record for EVERY active "
            f"phonon field (tensor, dyn, scat, decay, decayTT, DOS, Debye, "
            f"speeds, density) -- a partial override is a pseudo-material."
        )

    # (1) Density: the G4Material is what G4CMP actually uses, so the resolver's
    # density must agree with it, not merely with a catalog.
    g4_name = s_rec["g4_material"]
    if g4_name not in G4_MATERIAL_DENSITY_KG_M3:
        raise ContractError(
            f"no verified Geant4 density for {g4_name!r}. Measure it from a live "
            f"run before using this material; a table value may disagree with the "
            f"NIST material Geant4 actually builds."
        )
    g4_density = G4_MATERIAL_DENSITY_KG_M3[g4_name]
    expected_density = s_rec["expected_density_kg_m3"]
    density_dev = abs(g4_density - expected_density) / expected_density
    if density_dev > DENSITY_TOLERANCE:
        raise ContractError(
            f"{sub}: Geant4 {g4_name} density {g4_density} kg/m3 differs from the "
            f"record's {expected_density} kg/m3 by {density_dev:.2%} (> "
            f"{DENSITY_TOLERANCE:.1%}). G4CMP would use the Geant4 value while the "
            f"resolver used the record's -- an internally inconsistent candidate."
        )
    # Everything downstream uses the density G4CMP will actually use.
    density = g4_density

    # (2) Derived speeds from the candidate tensor AND the candidate density.
    vsound, vtrans = harness.derive_cubic_sound_speeds(
        s_rec["c11_GPa"], s_rec["c12_GPa"], s_rec["c44_GPa"], density=density)
    if not 0 < vtrans < vsound:
        raise ContractError(
            f"{sub}: derived speeds violate 0 < vtrans < vsound "
            f"(vsound={vsound:.1f}, vtrans={vtrans:.1f}); G4CMP's group-velocity "
            f"map assumes v_L > v_T and would index off the end of its table."
        )

    # (3) Consistency against the material's own native scalars. This is the
    # check that catches a wrong density: the Ge tensor with Si's density gives
    # 7966 m/s against Ge's native 5324, a ~50% error, while a genuine averaging
    # convention difference is under ~1%.
    speed_dev = {
        "vsound": abs(vsound - s_rec["native_vsound_m_s"]) / s_rec["native_vsound_m_s"],
        "vtrans": abs(vtrans - s_rec["native_vtrans_m_s"]) / s_rec["native_vtrans_m_s"],
    }
    worst = max(speed_dev.values())
    if worst > DERIVED_SPEED_TOLERANCE:
        raise ContractError(
            f"{sub}: derived speeds disagree with the native lattice record by "
            f"{worst:.2%} (> {DERIVED_SPEED_TOLERANCE:.1%}).\n"
            f"  derived: vsound={vsound:.1f} vtrans={vtrans:.1f}\n"
            f"  native : vsound={s_rec['native_vsound_m_s']:.1f} "
            f"vtrans={s_rec['native_vtrans_m_s']:.1f}\n"
            f"  density used: {density} kg/m3\n"
            f"A deviation this large usually means the wrong density was used."
        )

    # (4) Candidate-specific interfaces. Changing the substrate recomputes all
    # three; changing one film recomputes only its own.
    substrate_for_model = {"density_kg_m3": density,
                           "vsound_m_s": vsound, "vtrans_m_s": vtrans}
    si_rec = SUBSTRATES["Si"]
    si_density = G4_MATERIAL_DENSITY_KG_M3[si_rec["g4_material"]]
    si_vs, si_vt = harness.derive_cubic_sound_speeds(
        si_rec["c11_GPa"], si_rec["c12_GPa"], si_rec["c44_GPa"], density=si_density)
    si_for_model = {"density_kg_m3": si_density, "vsound_m_s": si_vs, "vtrans_m_s": si_vt}
    try:
        abs_values, abs_diag = resolve_all_interfaces(
            substrate_for_model,
            {"g4_material": JUNCTION_FILM["g4_material"],
             "vsound_m_s": JUNCTION_FILM["vsound_km_s"] * 1000.0},
            {"g4_material": t_rec["g4_material"], "vsound_m_s": t_rec["vsound_km_s"] * 1000.0,
             "density_kg_m3": t_rec.get("density_kg_m3")},
            {"g4_material": b_rec["g4_material"], "vsound_m_s": b_rec["vsound_km_s"] * 1000.0,
             "density_kg_m3": b_rec.get("density_kg_m3")},
            si_for_model)
    except InterfaceModelError as exc:
        raise ContractError(f"{sub}: interface model failed: {exc}") from exc
    for key, value in abs_values.items():
        if not 0.0 <= value <= 1.0:
            raise ContractError(f"{sub}: {key}={value} outside [0,1]")

    derived = {
        "substrate_density_kg_m3": density,
        "substrate_density_expected_kg_m3": expected_density,
        "substrate_density_source": f"Geant4 {g4_name}",
        "g4_material_name": g4_name,
        "lattice_map_name": s_rec["lattice_map"],
        "config_mode": s_rec["config_mode"],
        "vsound_m_s": round(vsound, 6),
        "vtrans_m_s": round(vtrans, 6),
        "native_vsound_m_s": s_rec["native_vsound_m_s"],
        "native_vtrans_m_s": s_rec["native_vtrans_m_s"],
        "derived_speed_deviation": {k: round(v, 6) for k, v in speed_dev.items()},
        "setTopAbs": round(abs_values["setTopAbs"], 6),
        "setTopFilmAbs": round(abs_values["setTopFilmAbs"], 6),
        "setBotAbs": round(abs_values["setBotAbs"], 6),
        "interface_model": "baseline_calibrated_effective_AMM",
        "interface_diagnostics": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                                      for kk, vv in d.items()}
                                  for k, d in abs_diag.items()},
        "c11_GPa": s_rec["c11_GPa"], "c12_GPa": s_rec["c12_GPa"], "c44_GPa": s_rec["c44_GPa"],
        "top_film_gap_eV": t_rec["gap_eV"],
        "provenance": {"substrate": s_rec["provenance"], "top_film": t_rec["provenance"],
                       "bottom_film": b_rec["provenance"]},
    }
    if debye_override_THz is not None:
        # A/B diagnostic only. Recorded in `derived`, so it is part of the cache
        # key and the two arms can never collide.
        derived["debye_override_THz"] = debye_override_THz

    contract.check_excitation_thresholds(top_film_gap_eV=t_rec["gap_eV"])
    return {"substrate": s_rec, "top_film": t_rec, "bottom_film": b_rec}, derived


# --------------------------------------------------------------------------
# Scenario (injection sites)
# --------------------------------------------------------------------------
def build_scenario(contract, template_z_mm):
    """Ordered injection-site set. Identical for every candidate by construction."""
    n = int(contract.fixed["n_positions"])
    if n < 1:
        raise ContractError("n_positions must be >= 1")
    if n > 1 and (n & (n - 1)):
        raise ContractError(
            f"n_positions={n} is not a power of two; a scrambled Sobol set is only "
            f"balanced at powers of two and an unbalanced site set biases the "
            f"device average."
        )
    if n == 1:
        sites = [None]
    else:
        engine = qmc.Sobol(d=2, scramble=True, seed=int(contract.fixed["position_seed"]))
        span = float(contract.fixed["position_half_span_mm"])
        sites = [(round(float(u[0]) * 2 * span - span, 6),
                  round(float(u[1]) * 2 * span - span, 6),
                  template_z_mm) for u in engine.random(n)]
    return {
        "sites_mm": sites,
        "weights": contract.fixed.get("position_weights", "uniform"),
        "position_seed": int(contract.fixed["position_seed"]),
        "half_span_mm": float(contract.fixed["position_half_span_mm"]),
    }


def candidate_seed(seed_base, seed_bank_id, candidate_key, replica, position):
    """Seed for one sub-run.

    Keyed by NOTHING candidate-specific: every candidate shares one bank at
    matched (replica, position), so candidate-vs-candidate comparisons are
    paired. This is the explicit-design keying the Morris runner deliberately
    did not implement -- there are no trajectories here, and giving each
    candidate an independent stream would discard the pairing.

    Caveat against over-claiming: two candidates with different material
    parameters consume different numbers of draws, so their streams decorrelate
    after the first divergent event. The pairing is free and cannot hurt, but
    the variance reduction is unmeasured -- do not budget for it.
    """
    raw = (int(seed_base) + int(seed_bank_id) * 7_919_003
           + int(replica) * 10_007 + int(position) * 101)
    return raw % 900_000_000 + 1


# --------------------------------------------------------------------------
# Macro / lattice generation
# --------------------------------------------------------------------------
def _write_lattice_config(contract, resolved, derived, dest_dir):
    """Write the candidate's lattice config.

    `native_g4cmp` mode copies the material's own complete G4CMP record
    VERBATIM -- lattice constant, tensor, `dyn`, `scat`, `decay`, `decayTT`, DOS
    fractions and `Debye`. The legacy FIXED_CONFIG_COMMANDS are deliberately NOT
    applied: they carry Si's third-order `dyn` and `Debye 15 THz`, and forcing
    those onto a Ge lattice would overwrite `Debye 2 THz` with a value 7.5x too
    large while leaving everything else looking Ge-like.

    Only two lines are rewritten, both derived from the candidate's own record:
    `vsound` and `vtrans`, so the scalars agree with the tensor and the density
    actually in use. An explicit `debye_override_THz` (A/B diagnostic) is the
    single sanctioned exception and is recorded in the cache key.
    """
    lattice = derived["lattice_map_name"]
    # A Stage 4 pseudo-material writes a DIFFERENT record than it reads: it
    # starts from a real base record (Si) and overrides the scanned fields. The
    # destination name is the one the macro selects with setSubstrateName.
    base = derived.get("base_lattice_map", lattice)
    src = os.path.join(harness.G4CMP_SOURCE_CRYSTALMAPS, base, "config.txt")
    if not os.path.isfile(src):
        raise ContractError(f"No G4CMP lattice map for {base!r}: {src}")
    with open(src) as handle:
        lines = handle.readlines()

    # Guard: the Si-only overrides must never reach this path.
    forced = {prefix.strip() for prefix, _ in getattr(harness, "FIXED_CONFIG_COMMANDS", ())}
    # Judged on the SOURCE record: a Stage 4 pseudo-material is built on Si, so
    # it legitimately carries Si's Debye (measured inert for this gun type). What
    # must never happen is Si's values landing on a DIFFERENT material's record.
    if forced and base != "Si":
        # Nothing applies them here; this asserts that a future edit cannot.
        for prefix in forced:
            for line in lines:
                if line.strip().startswith(prefix) and "15 THz" in line and prefix == "Debye":
                    raise ContractError(
                        f"native {lattice} config unexpectedly carries Si's Debye 15 THz")

    for key, value in (("vsound ", derived["vsound_m_s"]), ("vtrans ", derived["vtrans_m_s"])):
        lines = replace_line(lines, key, format_config_entry(key, value, " m/s"),
                             append_if_missing=True)
    if derived.get("debye_override_THz") is not None:
        lines = replace_line(lines, "Debye ",
                             format_config_entry("Debye ", derived["debye_override_THz"], " THz"),
                             append_if_missing=True)

    # Stage 4: scanned lattice fields. Everything NOT listed here keeps the base
    # record's value -- which is the definition of the pseudo-material, so it is
    # stamped into the file rather than left to be inferred.
    overrides = derived.get("config_overrides") or {}
    if overrides:
        for key, spec in sorted(overrides.items()):
            value, unit = (spec if isinstance(spec, (list, tuple)) else (spec, ""))
            lines = replace_line(lines, key, format_config_entry(key, value, unit),
                                 append_if_missing=True)
        header = [
            f"# GENERATED pseudo-material: base record {base!r} with the scanned\n",
            f"# fields overridden ({', '.join(sorted(k.strip() for k in overrides))}).\n",
            "# Unlisted fields -- dyn, LDOS/STDOS/FTDOS, Debye, the charge-carrier\n",
            f"# block -- remain {base}'s. This is not a from-scratch crystal.\n",
        ]
        lines = header + lines

    out = os.path.join(dest_dir, lattice, "config.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        handle.writelines(lines)
    return out


def _verify_runtime_material(log_path, expected_g4_name, expected_density_kg_m3):
    """Confirm from the Geant4 log that the intended material and density were used.

    The resolver derives speeds and impedances from a density; this checks that
    Geant4 built the same one. `G4LatticeManager::LoadLattice` calls
    `SetDensity(Mat->GetDensity())`, so a mismatch means the phonon kinematics
    ran on a different material than the one that was resolved.

    Returns (ok, message, observed_density_g_cm3 or None).
    """
    try:
        with open(log_path, errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return False, f"cannot read log: {exc}", None
    marker = "Material:"
    observed_name, observed_density = None, None
    for line in text.splitlines():
        if marker in line and "density:" in line:
            parts = line.split()
            try:
                observed_name = parts[parts.index("Material:") + 1]
                observed_density = float(parts[parts.index("density:") + 1])
            except (ValueError, IndexError):
                continue
            break
    if observed_name is None:
        return False, "no material/density line in the Geant4 log", None
    if observed_name != expected_g4_name:
        return False, f"ran {observed_name}, expected {expected_g4_name}", observed_density
    dev = abs(observed_density * 1000.0 - expected_density_kg_m3) / expected_density_kg_m3
    if dev > DENSITY_TOLERANCE:
        return (False,
                f"{observed_name} density {observed_density} g/cm3 differs from the "
                f"resolved {expected_density_kg_m3 / 1000.0:.4f} g/cm3 by {dev:.2%}",
                observed_density)
    return True, f"{observed_name} at {observed_density} g/cm3", observed_density


def _write_sub_run_macro(contract, resolved, derived, template, macro_path,
                         hits_file, done_marker, site, seed, events):
    with open(template) as handle:
        lines = handle.readlines()
    f = contract.fixed
    t, b = resolved["top_film"], resolved["bottom_film"]

    settings = [
        ("/main/detector_param/setSubstrateG4Name ", derived["g4_material_name"]),
        ("/main/detector_param/setSubstrateName ", derived["lattice_map_name"]),
        ("/main/gun/setEnergy ", f"{f['gun_energy_eV']} eV"),
        ("/g4cmp/minEPhonons ", f"{f['min_e_phonons_eV']} eV"),
        ("/main/detector_param/setTopAbs ", derived["setTopAbs"]),
        ("/main/detector_param/setTopFilmAbs ", derived["setTopFilmAbs"]),
        ("/main/detector_param/setBotAbs ", derived["setBotAbs"]),
        ("/main/detector_param/setTopVSound ", f["setTopVSound"]),
        ("/main/detector_param/setTopGap ", f["setTopGap"]),
        ("/main/detector_param/setTopQPLim ", f["setTopQPLim"]),
        ("/main/detector_param/setTopPhLifetime ", f["setTopPhLifetime"]),
        ("/main/detector_param/setTopPhLifetimeSlope ", f["setTopPhLifetimeSlope"]),
        ("/main/detector_param/setTopThickness ", f"{f['setTopThickness_um']} um"),
        ("/main/detector_param/setTopFilmSourceMat ", t["g4_material"]),
        ("/main/detector_param/setTopFilmVSound ", t["vsound_km_s"]),
        ("/main/detector_param/setTopFilmGap ", t["gap_eV"]),
        ("/main/detector_param/setTopFilmQPLim ", t["qp_limit"]),
        ("/main/detector_param/setTopFilmPhLifetime ", t["ph_lifetime_ns"]),
        ("/main/detector_param/setTopFilmPhLifetimeSlope ", t["ph_lifetime_slope"]),
        ("/main/detector_param/setTopFilmThickness ", f"{t['thickness_um']} um"),
        ("/main/detector_param/setBotSourceMat ", b["g4_material"]),
        ("/main/detector_param/setBotVSound ", b["vsound_km_s"]),
        ("/main/detector_param/setBotGap ", b["gap_eV"]),
        ("/main/detector_param/setBotGapThres ", b["gap_threshold_eV"]),
        ("/main/detector_param/setBotQPLim ", b["qp_limit"]),
        ("/main/detector_param/setBotPhLifetime ", b["ph_lifetime_ns"]),
        ("/main/detector_param/setBotThickness ", f"{b['thickness_um']} um"),
        ("/main/detector_param/setWallAbs ", f["setWallAbs"]),
        ("/main/detector_param/setTopPSpecProb ", f["setTopPSpecProb"]),
        ("/main/detector_param/setTopFilmPSpecProb ", f["setTopFilmPSpecProb"]),
        ("/main/detector_param/setBotPSpecProb ", f["setBotPSpecProb"]),
        ("/main/detector_param/setWallPSpecProb ", f["setWallPSpecProb"]),
        ("/g4cmp/chargeBounces ", f["chargeBounces"]),
        ("/g4cmp/phononBounces ", f["phononBounces"]),
        ("/g4cmp/clearance ", f"{f['clearance_mm']} mm"),
        ("/g4cmp/eTrappingMFP ", f"{f['eTrappingMFP_mm']} mm"),
        ("/g4cmp/hTrappingMFP ", f"{f['hTrappingMFP_mm']} mm"),
        # Stage 4 declares temperature a DECISION variable and supplies it per
        # candidate through macro_overrides, so it is deliberately absent from
        # that campaign's `fixed` block -- a value must never be both.
        *((("/g4cmp/temperature ", f"{f['temperature_K']} K"),)
          if "temperature_K" in f else ()),
        ("/main/electrode_param/setWidth ", f["electrode_width_um"]),
        ("/main/electrode_param/setHeight ", f["electrode_height_um"]),
        ("/main/electrode_param/setIsland ", f"{f['electrode_island_um']} um"),
        ("/main/electrode_param/setIslandSpacing ", f"{f['electrode_island_spacing_um']} um"),
        ("/main/detector_param/setLatticeDeg ", contract.decision["orientation"]["lattice_deg"]),
    ]
    for command, value in settings:
        lines = replace_line(lines, command, f"{command}{value}",
                             allow_commented=True, required=True)

    miller = contract.decision["orientation"]["miller"]
    lines = replace_line(lines, "/main/detector_param/setMiller ",
                         "/main/detector_param/setMiller " + " ".join(str(int(v)) for v in miller),
                         allow_commented=True, required=True)
    lines = replace_line(lines, "/main/electrode_param/setXLocations ",
                         "/main/electrode_param/setXLocations "
                         + ", ".join(f"{v:.6f}" for v in f["electrode_x_mm"]), required=True)
    lines = replace_line(lines, "/main/electrode_param/setYLocations ",
                         "/main/electrode_param/setYLocations "
                         + ", ".join(f"{v:.6f}" for v in f["electrode_y_mm"]), required=True)
    # Stage 4: per-candidate commands (orientation, temperature) that the fixed
    # block above wrote from the contract default. Applied last so the candidate
    # wins, and re-checked below by the unit-hygiene gate.
    for command, value in sorted((derived.get("macro_overrides") or {}).items()):
        lines = replace_line(lines, command, f"{command}{value}",
                             allow_commented=True, required=True)

    lines = replace_line(lines, "/g4cmp/HitsFile", "/g4cmp/HitsFile " + hits_file, required=True)
    if site is not None:
        lines = replace_line(lines, "/main/gun/setPosition ",
                             "/main/gun/setPosition {0} {1} {2} mm".format(*site), required=True)

    # Seed immediately before beamOn; marker immediately after. Anything between
    # the seed and beamOn consumes draws and shifts the stream.
    block = f"/random/setSeeds {seed} {seed + 1}\n/run/beamOn {events}\n" \
            f"/control/shell touch {shlex.quote(done_marker)}"
    lines = replace_line(lines, "/run/beamOn ", block, required=True)

    bad = validate_macro_lines(lines)
    if bad:
        raise ContractError(
            f"generated macro would abort: {bad[:3]}. A malformed command makes "
            f"Geant4 emit a warning, skip /run/beamOn and EXIT 0 -- 3,200 "
            f"'successful' sub-runs with no hits file were produced that way once. "
            f"The macro is refused here instead.")

    with open(macro_path, "w") as handle:
        handle.writelines(lines)
    return macro_path


_NUMERIC_START = ("+", "-", ".", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")


def validate_macro_lines(lines):
    """Unit-hygiene gate (G9). Returns a list of offending lines.

    The failure this prevents: `/g4cmp/clearance 1e-06e-6 mm`, produced when a
    default expressed in one unit meets bounds expressed in another. Geant4
    treats it as a G4Exception WARNING, skips the run, and exits 0 -- which
    downstream is indistinguishable from a weak physical response.

    Rule: any argument token that STARTS like a number must PARSE as one.
    """
    offenders = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("/"):
            continue
        tokens = line.split()
        if tokens[0] in ("/control/shell", "/control/execute"):
            continue                      # a shell path is not a G4 argument list
        for tok in tokens[1:]:
            tok = tok.rstrip(",")
            if not tok or not tok.startswith(_NUMERIC_START):
                continue
            try:
                float(tok)
            except ValueError:
                offenders.append(line)
                break
    return offenders


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def _run_one(sub_run, lattice_root, timeout_s, guard, logs_dir,
             expected_g4_name=None, expected_density=None, stall_timeout_s=0):
    """Run one sub-run; return (status, return_code, runtime, reason).

    Two independent watchdogs, and the difference between them matters:

    `timeout_s` is an ABSOLUTE wall-clock limit. It is a biased instrument: how
    long a sub-run takes is a function of the physics being simulated -- a
    low-absorption design lets phonons bounce toward the 10 000-bounce limit
    before they are absorbed -- so a wall-clock kill preferentially destroys
    exactly the candidates the optimizer prefers. It censors on candidate
    quality. Measured: `best_random` at 1e8 events/sub-run lost all 32 sub-runs
    to a 41.7 h limit, ~1300 core-hours that produced nothing, while the
    baseline (three times the QP yield) finished the same tier in 8.3 h.
    Set it to 0 for no wall-clock limit; the run is still physically bounded by
    `/g4cmp/phononBounces`.

    `stall_timeout_s` is a PROGRESS watchdog: it kills only when the process has
    written nothing for that long. A slow-but-progressing candidate is never
    touched, however slow, so it cannot correlate with candidate quality -- it
    catches the thing a watchdog should catch, a hung process, and nothing else.
    This is the one to use.
    """
    command = harness.build_run_command(sub_run["macro"], lattice_name=None)
    command = command.replace(
        "export G4LATTICEDATA=" + shlex.quote(
            os.path.join(harness.CRYSTALMAPS_DIR,
                         os.path.splitext(os.path.basename(sub_run["macro"]))[0])),
        "export G4LATTICEDATA=" + shlex.quote(lattice_root))
    # PhononSensitivity opens the hits file with std::ios_base::app
    # (PhononSensitivity.cc:84), so a RETRY would append to the previous
    # attempt's rows and embed a second header line mid-file -- which then
    # parses as strings in a numeric column. A retry must start clean, so both
    # the hits file and the completion marker are removed first. Removing the
    # marker also prevents a stale one from certifying a run that never happened.
    for stale in (sub_run["hits_file"], sub_run["done_marker"]):
        try:
            os.unlink(stale)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # Cannot guarantee a clean slate, so do not run: appending to the
            # previous attempt would corrupt the hits file rather than replace it.
            return STATUS_CORRUPT, None, 0.0, f"cannot clear {os.path.basename(stale)}: {exc}"

    log_file = os.path.join(logs_dir, sub_run["name"] + ".log")
    start = time.monotonic()
    fired = {"timeout": False, "reason": None}
    with open(log_file, "w") as log:
        process = subprocess.Popen(["bash", "-lc", command], stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        if guard is not None:
            guard.register(process.pid, sub_run["name"])
        def _kill(reason):
            if process.poll() is None:
                fired["timeout"] = True
                fired["reason"] = reason
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()

        def _progress():
            """A signal that advances whenever the sub-run is doing ANYTHING.

            Two independent components, deliberately:

            * bytes written to the log and the hits file -- measured on a live
              trial, both grow continuously (46 of 77 files grew in 90 s);
            * the process group's accumulated CPU time.

            The CPU term is what makes a false kill essentially impossible. A
            process burning CPU is not hung even if it has written nothing for
            hours, and output cadence is a property of Geant4's buffering that
            this code does not control. Only a run that is BOTH silent AND
            consuming no CPU is stalled -- which is the actual failure being
            guarded against.
            """
            total = 0
            for path in (log_file, sub_run["hits_file"]):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
            cpu = _cpu_ticks(process.pid)
            if cpu is None:
                return None                      # cannot measure -> cannot judge
            return total + cpu

        def _cpu_ticks(pid):
            """CPU ticks burned by the whole PROCESS GROUP, not just `pid`.

            Reading only `/proc/<pid>/stat` happens to work today, because the
            Geant4 binary is the LAST statement of the generated command and
            bash therefore exec-optimises itself into it -- `pid` *is* Main. That
            is an implementation detail of bash, not a guarantee: append one
            cleanup line after the binary and bash stays as the parent, Main
            becomes a grandchild, and `cutime` stays at 0 until the child is
            reaped. The CPU term would then read as permanently stuck and the
            stall watchdog would silently lose its most important signal.

            Summing over the process group is exec-independent. `_run_one`
            launches with `start_new_session=True`, so every descendant shares
            this group.
            """
            if not proc_pid_namespace_ok():
                # /proc is not ours: every pid lookup below would read some
                # other process. Returning 0 would make the CPU term a constant
                # and the stall detector would fall back to file size alone --
                # exactly the false-kill risk this term exists to remove. The
                # caller disables the stall watchdog entirely instead.
                return None
            try:
                pgid = os.getpgid(pid)
            except OSError:
                return 0
            total = 0
            try:
                entries = os.listdir("/proc")
            except OSError:
                return 0
            for entry in entries:
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/stat", "rb") as handle:
                        fields = handle.read().rsplit(b")", 1)[1].split()
                    # After the comm field: index j is stat field j+3, so
                    # pgrp=5 -> 2, utime=14 -> 11, stime=15 -> 12.
                    if int(fields[2]) != pgid:
                        continue
                    total += int(fields[11]) + int(fields[12])
                except (OSError, IndexError, ValueError):
                    continue
            return total

        stop_watch = threading.Event()
        # A stall detector that cannot see CPU must not run. It would judge on
        # file size alone and kill a healthy CPU-busy run that happens to be
        # buffering -- a false positive that destroys real work, which is worse
        # than having no stall detector at all. The absolute limit (if any) is
        # unaffected.
        stall_usable = stall_timeout_s and stall_timeout_s > 0 and _progress() is not None
        if stall_timeout_s and stall_timeout_s > 0 and not stall_usable:
            print(f"  WATCHDOG: /proc does not show this pid namespace "
                  f"(os.getpid()={os.getpid()} disagrees with /proc/self/stat), "
                  f"so CPU progress cannot be measured. The stall detector is "
                  f"DISABLED for {sub_run['name']} rather than judging on file "
                  f"size alone. Absolute limit: "
                  + ("none" if not (timeout_s and timeout_s > 0)
                     else f"{timeout_s:g}s"), flush=True)

        def _watch():
            deadline = (start + timeout_s) if (timeout_s and timeout_s > 0) else None
            last_bytes, last_change = _progress(), time.monotonic()
            # The poll interval must be short enough to honour whichever limit
            # is actually set -- keying it off the stall timeout alone meant an
            # explicit 3 s absolute limit was not noticed until the first poll
            # 60 s later, so the limit silently did nothing.
            limits = [x for x in (stall_timeout_s if stall_usable else 0,
                                  timeout_s) if x and x > 0]
            poll = min([60.0] + [x / 4.0 for x in limits])
            poll = max(0.25, poll)
            while not stop_watch.wait(poll):
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    _kill(f"exceeded the absolute wall-clock limit {timeout_s:g}s")
                    return
                if stall_usable:
                    current = _progress()
                    if current is None:          # became unmeasurable mid-run
                        return
                    if current != last_bytes:
                        last_bytes, last_change = current, now
                    elif now - last_change >= stall_timeout_s:
                        _kill(f"no output AND no CPU for {stall_timeout_s:g}s "
                              f"(progress counter stuck at {current:,}); the "
                              f"process is genuinely stalled, not merely slow")
                        return

        watcher = None
        if (timeout_s and timeout_s > 0) or stall_usable:
            watcher = threading.Thread(target=_watch,
                                       name=f"watchdog-{sub_run['name'][-12:]}",
                                       daemon=True)
            watcher.start()
        try:
            rc = process.wait()
        finally:
            stop_watch.set()
            if guard is not None:
                guard.unregister(process.pid)
    runtime = time.monotonic() - start
    completed = os.path.exists(sub_run["done_marker"])

    if fired["timeout"]:
        return STATUS_TIMEOUT, rc, runtime, fired.get("reason", f"exceeded {timeout_s:g}s")
    if rc != 0:
        if rc == -signal.SIGKILL:
            return STATUS_MEMORY_KILLED, rc, runtime, "SIGKILLed (memory guard or external)"
        # Teardown SIGSEGV is harmless ONLY with positive proof of completion.
        if rc in (139, -signal.SIGSEGV) and completed:
            return STATUS_TEARDOWN_SEGV, rc, runtime, "exit-time SIGSEGV after completion"
        return STATUS_SIM_FAILED, rc, runtime, f"return code {rc}"
    if not completed:
        return STATUS_MACRO_ABORTED, rc, runtime, "exit 0 without completion marker"
    if not os.path.exists(sub_run["hits_file"]):
        return STATUS_CORRUPT, rc, runtime, "completion marker present but no hits file"
    if expected_g4_name is not None:
        ok, message, _ = _verify_runtime_material(log_file, expected_g4_name, expected_density)
        if not ok:
            # The run completed, but not on the material that was resolved -- so
            # the speeds, impedances and interface values do not describe what
            # was simulated. That is corrupt, not successful.
            return STATUS_CORRUPT, rc, runtime, f"material mismatch: {message}"
    return STATUS_SUCCESS, rc, runtime, None


def _score_one(contract, hits_path):
    """Score a single sub-run's hits file. Returns (per_electrode, n_hits)."""
    if not os.path.exists(hits_path) or os.path.getsize(hits_path) == 0:
        raise ContractError(f"scoring an incomplete set: {os.path.basename(hits_path)}")
    rec = pd.read_csv(hits_path).reset_index(drop=True)
    f = contract.fixed
    qx = np.array(f["electrode_x_mm"], dtype=float)
    qy = np.array(f["electrode_y_mm"], dtype=float)
    chip_z = (float(f["setSubThickness_um"]) * 1e-6) / 2.0
    _, qp_nos = calculate_QPs(rec, float(f["setTopGap"]), qx, qy, chip_z)
    return qp_nos.sum(axis=1), len(rec)


def _score(contract, sub_runs, events_per_sub_run):
    """Score every completed sub-run separately, then pool.

    Per-sub-run scoring is EXACTLY equivalent to pooling the frames first --
    `calculate_QPs` rounds `E_dep/gap` and picks the nearest electrode per hit,
    with no cross-file term -- and it yields the (position, replica) blocks that
    every uncertainty statement here depends on. The counts are not Poisson
    (measured Fano ~ 5), so an interval has to come from the observed block
    spread, and the block-resolved objectives (p90 over sites, worst electrode)
    cannot be recovered from a pooled sum.

    Strict: every planned sub-run must be usable. Callers check completeness
    before reaching here; this re-checks rather than trusting.

    Returns (total, per_primary, per_electrode, n_hits, blocks).
    """
    blocks, n_hits_total = [], 0
    per_electrode = np.zeros(len(contract.fixed["electrode_x_mm"]), dtype=float)
    for sr in sub_runs:
        block_pe, n_hits = _score_one(contract, sr["hits_file"])
        per_electrode += block_pe
        n_hits_total += n_hits
        blocks.append({
            "replica": sr["replica"],
            "position": sr["position_index"],
            "total_qps": float(block_pe.sum()),
            "per_electrode_qps": [float(v) for v in block_pe],
            "n_hits": int(n_hits),
            "events": int(events_per_sub_run),
        })
    total = float(per_electrode.sum())
    n_sim = events_per_sub_run * len(sub_runs)
    return total, total / n_sim, [float(v) for v in per_electrode], n_hits_total, blocks


def _blocks_from_ledger(ledger, trial_id, events_per_sub_run):
    """Rebuild the (position, replica) blocks of a cached trial.

    A cache hit must be usable by a block-resolved objective, otherwise the
    objective would silently change meaning between a fresh and a cached
    evaluation. Returns [] for trials recorded before per-sub-run scoring
    existed -- the objective then reports that it cannot be computed rather
    than substituting a pooled value.
    """
    blocks = []
    for row in ledger.sub_runs(trial_id):
        if row["total_qps"] is None:
            return []
        blocks.append({
            "replica": row["replica"], "position": row["position"],
            "total_qps": float(row["total_qps"]),
            "per_electrode_qps": json.loads(row["per_electrode_qps"] or "[]"),
            "n_hits": row["n_hits"], "events": int(events_per_sub_run),
        })
    return blocks


def evaluate(contract, candidate, fidelity=None, seed_bank_id=None, ledger=None,
             runs_root=None, force=False, verbose=True, debye_override_THz=None,
             resolver=None, control_replica_id=None):
    """Evaluate one candidate. Returns a TrialResult; never raises on a physics
    failure (that is a status), only on a contract/programming error.

    `resolver(contract, candidate) -> (resolved, derived)` lets a different kind
    of candidate reuse this evaluator unchanged. Stage 4 passes
    `stage4_space.resolve`, which returns the same shapes plus two extra keys in
    `derived` -- `config_overrides` and `macro_overrides`. Because `derived` is
    already inside the cache key, those overrides enter the trial identity for
    free. Default is the Stage 3 catalog resolver, so the v2 path is unchanged.
    """
    fidelity = fidelity or contract.decision["fidelity"]["value"]
    seed_bank_id = (contract.fixed["seed_bank_id"] if seed_bank_id is None else seed_bank_id)
    if resolver is None:
        resolved, derived = resolve_candidate(contract, candidate,
                                             debye_override_THz=debye_override_THz)
    else:
        resolved, derived = resolver(contract, candidate)
        if debye_override_THz is not None:
            derived["debye_override_THz"] = debye_override_THz

    events_total = int(contract.decision["fidelity"]["events_total_per_candidate"][fidelity])
    events_per_sub_run = contract.events_per_sub_run(fidelity)
    n_positions = int(contract.fixed["n_positions"])
    n_replicas = int(contract.fixed["n_replicas"])

    template = os.environ.get("SENSITIVITY_MACRO_TEMPLATE",
                              os.path.join(REPO_ROOT, "sensitivity_template_beamOn1e6.mac"))
    template_z = float(find_macro_value(template, "/main/gun/setPosition").split()[2])
    scenario = build_scenario(contract, template_z)

    code_fp = contract.code_fingerprint()
    # Finding N2: the cache key is built from the SIMULATION identity, not the
    # campaign's. Resource limits, the watchdog, the campaign name, the search
    # box and the objective cannot change a completed trial's result, and
    # including them meant that relaunching the ladder at a different worker
    # count abandoned in-flight trials that should have resumed.
    sim_hash = contract.simulation_identity_hash()
    cache_key = compute_cache_key(
        sim_hash, code_fp, candidate, derived, fidelity,
        events_total, scenario, seed_bank_id,
        control_replica_id=control_replica_id)

    runs_root = runs_root or os.path.join(HERE, "runs", contract.campaign_id)
    ledger_owned = ledger is None
    ledger = ledger or Ledger(os.path.join(HERE, "stage3_trials.sqlite"))
    try:
        existing = ledger.find_by_cache_key(cache_key)
        if existing is not None:
            if (existing["status"] in (STATUS_SUCCESS, STATUS_SUCCESS_ZERO)
                    and not force):
                if verbose:
                    print(f"  cache hit: {existing['trial_id']} "
                          f"total_QPs={existing['total_qps']:.0f}")
                return TrialResult(
                    trial_id=existing["trial_id"], cache_key=cache_key,
                    status=existing["status"], candidate=candidate, derived=derived,
                    fidelity=fidelity, events_total=events_total,
                    events_per_sub_run=events_per_sub_run, n_positions=n_positions,
                    n_replicas=n_replicas, total_qps=existing["total_qps"],
                    qps_per_primary=existing["qps_per_primary"],
                    per_electrode_qps=json.loads(existing["per_electrode_qps"] or "[]"),
                    runtime_s=existing["runtime_s"], run_dir=existing["run_dir"],
                    cached=True,
                    blocks=_blocks_from_ledger(ledger, existing["trial_id"],
                                               existing["events_per_sub_run"]))
            # Re-run IN PLACE, forced or not. Minting a new trial_id under the
            # same cache key would hit the UNIQUE constraint, `INSERT OR IGNORE`
            # would swallow the trial row, and `set_trial_result` would update
            # nothing -- the run would execute, produce files, and never be
            # recorded. (That is exactly what --force did before 2026-08-21.)
            trial_id = existing["trial_id"]
            run_dir = existing["run_dir"]
            if force:
                with ledger.conn:
                    ledger.conn.execute(
                        "UPDATE sub_runs SET status=? WHERE trial_id=?",
                        ("planned", trial_id))
        else:
            trial_id = f"{contract.campaign_id}_{uuid.uuid4().hex[:12]}"
            run_dir = os.path.join(runs_root, trial_id)

        hits_dir = os.path.join(run_dir, "hits")
        macros_dir = os.path.join(run_dir, "macros")
        logs_dir = os.path.join(run_dir, "logs")
        lattice_root = os.path.join(run_dir, "CrystalMaps")
        for d in (hits_dir, macros_dir, logs_dir, lattice_root):
            os.makedirs(d, exist_ok=True)

        _write_lattice_config(contract, resolved, derived, lattice_root)

        planned = []
        for replica in range(n_replicas):
            for position in range(n_positions):
                name = f"{trial_id}_r{replica}_p{position}"
                hits_file = os.path.join(hits_dir, name + "_hitsfile.txt")
                planned.append({
                    "name": name, "replica": replica, "position_index": position,
                    "seed": candidate_seed(contract.fixed["seed_base"], seed_bank_id,
                                           candidate, replica, position),
                    "macro": os.path.join(macros_dir, name + ".mac"),
                    "hits_file": hits_file,
                    "done_marker": hits_file + ".done",
                })

        # Planned identities are recorded BEFORE anything launches, so
        # completeness is judged against intent, not against the filesystem.
        ledger.plan_trial(
            trial_id=trial_id, campaign_id=contract.campaign_id, cache_key=cache_key,
            contract_hash=contract.campaign_contract_hash(),
            simulation_identity_hash=sim_hash, code_fingerprint=code_fp,
            candidate=candidate, derived=derived, fidelity=fidelity,
            events_total=events_total, events_per_sub_run=events_per_sub_run,
            n_positions=n_positions, n_replicas=n_replicas, scenario=scenario,
            seed_bank_id=seed_bank_id, run_dir=run_dir, planned_sub_runs=planned)
        if control_replica_id is not None:
            ledger.set_optimizer_record(trial_id,
                                        control_replica_id=str(control_replica_id))

        for sr in planned:
            _write_sub_run_macro(contract, resolved, derived, template, sr["macro"],
                                 sr["hits_file"], sr["done_marker"],
                                 scenario["sites_mm"][sr["position_index"]],
                                 sr["seed"], events_per_sub_run)

        # Resume: rerun only sub-runs not already terminal-good.
        done_keys = {(r["replica"], r["position"]) for r in ledger.sub_runs(trial_id)
                     if r["status"] in (STATUS_SUCCESS, STATUS_SUCCESS_ZERO, STATUS_TEARDOWN_SEGV)}
        todo = [sr for sr in planned if (sr["replica"], sr["position_index"]) not in done_keys]
        if verbose:
            print(f"  trial {trial_id}: {len(todo)}/{len(planned)} sub-run(s) to run"
                  + (f" ({len(done_keys)} reused)" if done_keys else ""))
        ledger.set_trial_result(trial_id, STATUS_RUNNING)
        # Liveness: record WHO is working on this trial, then beat while the
        # Geant4 workers run. Without this a `running` row is indistinguishable
        # from an abandoned one -- the trial row is not touched during the work
        # and sub-run rows are only written at completion, so a healthy 29-hour
        # trial and a dead one look identical. Process visibility is not a
        # substitute: a PID namespace can hide a genuine host process.
        lease_uuid = uuid.uuid4().hex
        try:
            acquired = ledger.claim_trial(
                trial_id, lease_uuid, hostname=socket.gethostname(),
                owner_pid=os.getpid(), owner_ppid=os.getppid(),
                scheduler_job_id=_scheduler_job_id())
        except Exception as exc:                                  # noqa: BLE001
            acquired = None
            if verbose:
                print(f"  (liveness not recorded: {exc})")
        if acquired is False:
            # Finding N3: another evaluator is live on this exact trial. Running
            # anyway means two processes writing one run directory and one
            # cache key -- the failure mode the lease exists to prevent.
            raise ContractError(
                f"{trial_id} is leased by a live evaluator (heartbeat within "
                f"{ledger.LEASE_EXPIRY_SECONDS:.0f}s). Refusing to run it twice. "
                f"If that owner is genuinely dead, recover it explicitly with "
                f"claim_trial(..., takeover=True) once the lease has expired.")
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def _beat():
            """Heartbeat with bounded backoff and visible diagnostics.

            Finding N3: this used to `return` on the first exception, so one
            transient SQLite lock silently ended the heartbeat and turned a live
            job into a stale-heartbeat false positive. Transient failures are
            now retried; only losing the lease, or exhausting the retries, stops
            it -- and both say so.
            """
            hb, consecutive = None, 0
            try:
                hb = Ledger(ledger.path, allow_frozen=True)   # own connection:
                while not stop_heartbeat.wait(HEARTBEAT_SECONDS):  # sqlite objects
                    try:                                           # are per-thread
                        if not hb.heartbeat(trial_id, lease_uuid):
                            lease_lost.set()
                            print(f"  HEARTBEAT: lease {lease_uuid[:8]} on "
                                  f"{trial_id} was taken away; this evaluator no "
                                  f"longer owns it", flush=True)
                            return
                        consecutive = 0
                    except Exception as exc:                  # noqa: BLE001
                        consecutive += 1
                        if consecutive >= HEARTBEAT_MAX_FAILURES:
                            print(f"  HEARTBEAT: giving up after {consecutive} "
                                  f"consecutive failures on {trial_id}: {exc}. "
                                  f"The trial is STILL RUNNING -- treat a stale "
                                  f"heartbeat as unverifiable, not as death.",
                                  flush=True)
                            return
                        backoff = min(HEARTBEAT_SECONDS * (2 ** consecutive),
                                      HEARTBEAT_MAX_BACKOFF)
                        print(f"  HEARTBEAT: transient failure {consecutive}/"
                              f"{HEARTBEAT_MAX_FAILURES} on {trial_id} ({exc}); "
                              f"retrying in {backoff:.0f}s", flush=True)
                        if stop_heartbeat.wait(backoff):
                            return
            except Exception as exc:                          # noqa: BLE001
                print(f"  HEARTBEAT: could not start for {trial_id}: {exc}",
                      flush=True)
            finally:
                if hb is not None:
                    try:
                        hb.close()
                    except Exception:                             # noqa: BLE001
                        pass

        beater = threading.Thread(target=_beat, name=f"heartbeat-{trial_id[-8:]}",
                                  daemon=True)
        beater.start()

        timeout_s = float(contract.fixed.get("sample_timeout_s") or 0)
        stall_timeout_s = float(contract.fixed.get("sample_stall_timeout_s") or 0)
        if verbose:
            print(f"  watchdog: "
                  + ("no wall-clock limit" if timeout_s <= 0
                     else f"absolute {timeout_s:g}s")
                  + (f", stall {stall_timeout_s:g}s" if stall_timeout_s > 0
                     else ", no stall detector"))
        workers = int(contract.fixed.get("max_workers", 32))
        guard = MemoryGuard(total_gb=float(contract.fixed["total_mem_gb"]),
                            per_sample_gb=float(contract.fixed["per_sample_mem_gb"]),
                            poll_seconds=5.0).start()
        start = time.monotonic()
        failures = []
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo) or 1))) as pool:
                futures = {pool.submit(_run_one, sr, lattice_root, timeout_s, guard,
                                       logs_dir, derived["g4_material_name"],
                                       derived["substrate_density_kg_m3"],
                                       stall_timeout_s): sr
                           for sr in todo}
                for future in as_completed(futures):
                    sr = futures[future]
                    try:
                        status, rc, runtime, reason = future.result()
                    except Exception as exc:  # noqa: BLE001 - one sub-run must not
                        # abort the trial: an unexpected error is a sub-run status,
                        # and the trial is then judged incomplete like any other
                        # partial set. Letting it propagate would lose the results
                        # of every sibling sub-run that already succeeded.
                        status, rc, runtime = STATUS_SIM_FAILED, None, None
                        reason = f"{type(exc).__name__}: {exc}"
                    ledger.set_sub_run_status(trial_id, sr["replica"], sr["position_index"],
                                              status, rc, runtime, reason)
                    if status not in (STATUS_SUCCESS, STATUS_TEARDOWN_SEGV):
                        failures.append((sr["name"], status, reason))
        finally:
            guard.stop()
            stop_heartbeat.set()
        runtime_total = time.monotonic() - start
        peak_rss_gb = guard.peak_total_bytes / 1024 ** 3

        # Strict completeness, judged against the planned identities.
        good = {(r["replica"], r["position"]) for r in ledger.sub_runs(trial_id)
                if r["status"] in (STATUS_SUCCESS, STATUS_SUCCESS_ZERO, STATUS_TEARDOWN_SEGV)}
        expected = {(sr["replica"], sr["position_index"]) for sr in planned}
        if good != expected:
            missing = sorted(expected - good)
            reason = (f"{len(missing)}/{len(expected)} sub-run(s) incomplete "
                      f"(first: r{missing[0][0]}p{missing[0][1]}); "
                      f"failures={failures[:3]}")
            ledger.set_trial_result(trial_id, STATUS_INCOMPLETE_SET, lease_uuid=lease_uuid,
                                    runtime_s=runtime_total, peak_rss_gb=peak_rss_gb,
                                    failure_reason=reason)
            if verbose:
                print(f"  INCOMPLETE: {reason}")
                print("  Not scored: the injection sites are a fixed spatial "
                      "quadrature; a partial set is biased toward the survivors.")
            return TrialResult(trial_id=trial_id, cache_key=cache_key,
                               status=STATUS_INCOMPLETE_SET, candidate=candidate,
                               derived=derived, fidelity=fidelity,
                               events_total=events_total,
                               events_per_sub_run=events_per_sub_run,
                               n_positions=n_positions, n_replicas=n_replicas,
                               runtime_s=runtime_total, peak_rss_gb=peak_rss_gb,
                               failure_reason=reason, run_dir=run_dir)

        total, per_primary, per_electrode, n_hits, blocks = _score(
            contract, planned, events_per_sub_run)
        status = STATUS_SUCCESS if total > 0 else STATUS_SUCCESS_ZERO
        for block in blocks:
            ledger.set_sub_run_score(trial_id, block["replica"], block["position"],
                                     block["total_qps"], block["per_electrode_qps"],
                                     block["n_hits"])
        wrote = ledger.set_trial_result(trial_id, status, total_qps=total,
                                        qps_per_primary=per_primary,
                                        per_electrode_qps=per_electrode,
                                        runtime_s=runtime_total,
                                        peak_rss_gb=peak_rss_gb,
                                        lease_uuid=lease_uuid)
        if not wrote:
            # The lease was taken over while this trial ran. Refusing to write
            # is the point: the new owner's result stands, and this one is
            # reported rather than silently discarded (finding N3).
            raise ContractError(
                f"{trial_id}: the lease was taken over while this evaluator was "
                f"running, so its result was NOT written. Another owner is "
                f"authoritative for this trial. Re-run only after establishing "
                f"which owner actually completed it.")
        if verbose:
            print(f"  {status}: total_QPs={total:.0f} "
                  f"({per_primary:.3e}/event, {n_hits} surface hits, "
                  f"{runtime_total:.1f}s, peak {peak_rss_gb:.2f} GB)")
        return TrialResult(trial_id=trial_id, cache_key=cache_key, status=status,
                           candidate=candidate, derived=derived, fidelity=fidelity,
                           events_total=events_total,
                           events_per_sub_run=events_per_sub_run,
                           n_positions=n_positions, n_replicas=n_replicas,
                           total_qps=total, qps_per_primary=per_primary,
                           per_electrode_qps=per_electrode, runtime_s=runtime_total,
                           peak_rss_gb=peak_rss_gb, run_dir=run_dir, blocks=blocks)
    finally:
        if ledger_owned:
            ledger.close()


def main():
    ap = argparse.ArgumentParser(description="Stage 3 single-candidate evaluator")
    ap.add_argument("--contract", default=os.path.join(HERE, "stage3_config.yaml"))
    ap.add_argument("--fidelity", default=None)
    ap.add_argument("--seed-bank", type=int, default=None)
    ap.add_argument("--events-total", type=int, default=None,
                    help="Override the fidelity's event budget (testing only).")
    ap.add_argument("--replay", action="store_true",
                    help="Exit check: run the baseline twice with the same seeds "
                         "and require an identical objective.")
    ap.add_argument("--force", action="store_true", help="Ignore a cache hit.")
    args = ap.parse_args()

    contract = load_contract(args.contract)
    if args.events_total:
        fid = args.fidelity or contract.decision["fidelity"]["value"]
        contract.decision["fidelity"]["events_total_per_candidate"][fid] = args.events_total
    candidate = {
        "substrate": contract.decision["substrate"]["value"],
        "top_ground_film": contract.decision["top_ground_film"]["value"],
        "bottom_film": contract.decision["bottom_film"]["value"],
    }
    print(f"Campaign {contract.campaign_id}; candidate {candidate}")

    first = evaluate(contract, candidate, args.fidelity, args.seed_bank, force=args.force)
    if not args.replay:
        print(json.dumps({k: v for k, v in asdict(first).items()
                          if k not in ("per_electrode_qps",)}, indent=2, default=str))
        return 0 if first.is_observation else 1

    print("Replay with identical seeds (exit check):")
    second = evaluate(contract, candidate, args.fidelity, args.seed_bank, force=True)
    ok = (first.is_observation and second.is_observation
          and first.total_qps == second.total_qps
          and first.per_electrode_qps == second.per_electrode_qps)
    print(f"\n  run 1: {first.status} total_QPs={first.total_qps}")
    print(f"  run 2: {second.status} total_QPs={second.total_qps}")
    print(f"  cache keys identical: {first.cache_key == second.cache_key}")
    print(f"  EXIT CHECK {'PASS' if ok else 'FAIL'}: "
          f"identical objective under identical seeds")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
