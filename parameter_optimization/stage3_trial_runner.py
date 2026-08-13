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
import subprocess
import sys
import tempfile
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
import stage1_run_simulations as harness                              # noqa: E402
from stage2_compute_QPs import calculate_QPs                          # noqa: E402


# --------------------------------------------------------------------------
# Baseline material records. A real catalog replaces this dict; the shape is
# what `material_resolver.py` must produce, so the evaluator does not change
# when the catalog arrives.
# --------------------------------------------------------------------------
BASELINE_SUBSTRATES = {
    "Si": {
        "g4_material": "G4_Si",
        "lattice_map": "Si",
        "density_kg_m3": 2329.0,
        "c11_GPa": 165.6, "c12_GPa": 63.9, "c44_GPa": 79.5,
        "provenance": "harness baseline (CrystalMaps/Si/config.txt)",
    },
}
BASELINE_TOP_FILMS = {
    "Nb": {"g4_material": "G4_Nb", "gap_eV": 1.5384e-3, "vsound_km_s": 2.444,
           "ph_lifetime_ns": 0.00417, "ph_lifetime_slope": 0.29, "qp_limit": 3,
           "thickness_um": 0.075, "abs_baseline": 0.745,
           "provenance": "harness baseline"},
}
BASELINE_BOTTOM_FILMS = {
    "Cu": {"g4_material": "G4_Cu", "gap_eV": 0.0, "gap_threshold_eV": 180e-6,
           "vsound_km_s": 2.608, "ph_lifetime_ns": 5.1, "qp_limit": 3,
           "thickness_um": 1.0, "normal_metal": True, "abs_baseline": 0.736,
           "provenance": "harness baseline"},
}
AL_JUNCTION_ABS_BASELINE = 0.795


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

    @property
    def is_observation(self):
        return self.status in (STATUS_SUCCESS, STATUS_SUCCESS_ZERO)


# --------------------------------------------------------------------------
# Resolver (baseline-only slice)
# --------------------------------------------------------------------------
def resolve_candidate(contract, candidate):
    """Resolve one (substrate, top film, bottom film) triplet to concrete values.

    Baseline-only for now. Anything else fails loudly with the reason, because
    the two Si-specific hazards in the harness are silent:
      * derived speeds divide by a hardcoded Si density (~50% error for Ge);
      * FIXED_CONFIG_COMMANDS force Si's Debye/dyn over the candidate's.
    """
    sub, top, bot = candidate["substrate"], candidate["top_ground_film"], candidate["bottom_film"]
    for name, table, what in ((sub, BASELINE_SUBSTRATES, "substrate"),
                              (top, BASELINE_TOP_FILMS, "top_ground_film"),
                              (bot, BASELINE_BOTTOM_FILMS, "bottom_film")):
        if name not in table:
            raise ContractError(
                f"{what}={name!r} is not implemented in this slice (available: "
                f"{sorted(table)}). Implementing it requires, at minimum: a material "
                f"record with density and provenance, candidate density propagated "
                f"into the derived sound speeds, candidate-aware config overrides "
                f"(Debye/dyn are currently forced to Si values), and the interface "
                f"transmission model for setTopAbs/setTopFilmAbs/setBotAbs."
            )
    s = BASELINE_SUBSTRATES[sub]
    t = BASELINE_TOP_FILMS[top]
    b = BASELINE_BOTTOM_FILMS[bot]

    # Derived speeds: density passed explicitly, never taken from the module
    # constant, so a future non-Si record cannot silently inherit Si's.
    vsound, vtrans = harness.derive_cubic_sound_speeds(
        s["c11_GPa"], s["c12_GPa"], s["c44_GPa"], density=s["density_kg_m3"])
    if not 0 < vtrans < vsound:
        raise ContractError(
            f"derived speeds violate 0 < vtrans < vsound (vsound={vsound:.1f}, "
            f"vtrans={vtrans:.1f}); G4CMP's group-velocity map assumes v_L > v_T "
            f"and would index off the end of its table."
        )

    # Interface absorption. Baseline slice uses the calibrated Si values
    # directly; the interface model replaces this and must reproduce them.
    derived = {
        "substrate_density_kg_m3": s["density_kg_m3"],
        "g4_material_name": s["g4_material"],
        "lattice_map_name": s["lattice_map"],
        "vsound_m_s": round(vsound, 6),
        "vtrans_m_s": round(vtrans, 6),
        "setTopAbs": AL_JUNCTION_ABS_BASELINE,
        "setTopFilmAbs": t["abs_baseline"],
        "setBotAbs": b["abs_baseline"],
        "interface_model": "si_baseline_constants",
        "c11_GPa": s["c11_GPa"], "c12_GPa": s["c12_GPa"], "c44_GPa": s["c44_GPa"],
        "top_film_gap_eV": t["gap_eV"],
    }
    contract.check_excitation_thresholds(top_film_gap_eV=t["gap_eV"])
    return {"substrate": s, "top_film": t, "bottom_film": b}, derived


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
    """Candidate lattice config, written from the material's own template."""
    lattice = derived["lattice_map_name"]
    src = os.path.join(harness.G4CMP_SOURCE_CRYSTALMAPS, lattice, "config.txt")
    if not os.path.isfile(src):
        raise ContractError(f"No G4CMP lattice map for {lattice!r}: {src}")
    with open(src) as handle:
        lines = handle.readlines()
    s = resolved["substrate"]
    for key, value, unit in (("cubic ", None, None),
                             ("stiffness 1 1 ", s["c11_GPa"], " GPa"),
                             ("stiffness 1 2 ", s["c12_GPa"], " GPa"),
                             ("stiffness 4 4 ", s["c44_GPa"], " GPa")):
        if value is None:
            continue
        lines = replace_line(lines, key, format_config_entry(key, value, unit),
                             append_if_missing=True)
    for key, value in (("vsound ", derived["vsound_m_s"]), ("vtrans ", derived["vtrans_m_s"])):
        lines = replace_line(lines, key, format_config_entry(key, value, " m/s"),
                             append_if_missing=True)
    out = os.path.join(dest_dir, lattice, "config.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        handle.writelines(lines)
    return out


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
        ("/g4cmp/temperature ", f"{f['temperature_K']} K"),
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
    lines = replace_line(lines, "/g4cmp/HitsFile", "/g4cmp/HitsFile " + hits_file, required=True)
    if site is not None:
        lines = replace_line(lines, "/main/gun/setPosition ",
                             "/main/gun/setPosition {0} {1} {2} mm".format(*site), required=True)

    # Seed immediately before beamOn; marker immediately after. Anything between
    # the seed and beamOn consumes draws and shifts the stream.
    block = f"/random/setSeeds {seed} {seed + 1}\n/run/beamOn {events}\n" \
            f"/control/shell touch {shlex.quote(done_marker)}"
    lines = replace_line(lines, "/run/beamOn ", block, required=True)

    with open(macro_path, "w") as handle:
        handle.writelines(lines)
    return macro_path


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def _run_one(sub_run, lattice_root, timeout_s, guard, logs_dir):
    """Run one sub-run; return (status, return_code, runtime, reason)."""
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
    fired = {"timeout": False}
    with open(log_file, "w") as log:
        process = subprocess.Popen(["bash", "-lc", command], stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        if guard is not None:
            guard.register(process.pid, sub_run["name"])
        timer = None
        if timeout_s and timeout_s > 0:
            import threading

            def on_timeout():
                if process.poll() is None:
                    fired["timeout"] = True
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        process.kill()

            timer = threading.Timer(timeout_s, on_timeout)
            timer.daemon = True
            timer.start()
        try:
            rc = process.wait()
        finally:
            if timer is not None:
                timer.cancel()
            if guard is not None:
                guard.unregister(process.pid)
    runtime = time.monotonic() - start
    completed = os.path.exists(sub_run["done_marker"])

    if fired["timeout"]:
        return STATUS_TIMEOUT, rc, runtime, f"exceeded {timeout_s:g}s"
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
    return STATUS_SUCCESS, rc, runtime, None


def _score(contract, sub_runs, events_per_sub_run):
    """Pool the completed sub-runs and compute the objective.

    Strict: every planned sub-run must be usable. Callers check completeness
    before reaching here; this re-checks rather than trusting.
    """
    frames = []
    for sr in sub_runs:
        path = sr["hits_file"]
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise ContractError(f"scoring an incomplete set: {os.path.basename(path)}")
        frames.append(pd.read_csv(path).reset_index(drop=True))
    rec = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    f = contract.fixed
    qx = np.array(f["electrode_x_mm"], dtype=float)
    qy = np.array(f["electrode_y_mm"], dtype=float)
    chip_z = (float(f["setSubThickness_um"]) * 1e-6) / 2.0
    _, qp_nos = calculate_QPs(rec, float(f["setTopGap"]), qx, qy, chip_z)
    per_electrode = qp_nos.sum(axis=1)
    total = float(per_electrode.sum())
    n_sim = events_per_sub_run * len(sub_runs)
    return total, total / n_sim, [float(v) for v in per_electrode], len(rec)


def evaluate(contract, candidate, fidelity=None, seed_bank_id=None, ledger=None,
             runs_root=None, force=False, verbose=True):
    """Evaluate one candidate. Returns a TrialResult; never raises on a physics
    failure (that is a status), only on a contract/programming error."""
    fidelity = fidelity or contract.decision["fidelity"]["value"]
    seed_bank_id = (contract.fixed["seed_bank_id"] if seed_bank_id is None else seed_bank_id)
    resolved, derived = resolve_candidate(contract, candidate)

    events_total = int(contract.decision["fidelity"]["events_total_per_candidate"][fidelity])
    events_per_sub_run = contract.events_per_sub_run(fidelity)
    n_positions = int(contract.fixed["n_positions"])
    n_replicas = int(contract.fixed["n_replicas"])

    template = os.environ.get("SENSITIVITY_MACRO_TEMPLATE",
                              os.path.join(REPO_ROOT, "sensitivity_template_beamOn1e6.mac"))
    template_z = float(find_macro_value(template, "/main/gun/setPosition").split()[2])
    scenario = build_scenario(contract, template_z)

    code_fp = contract.code_fingerprint()
    cache_key = compute_cache_key(
        contract.contract_hash(), code_fp, candidate, derived, fidelity,
        events_total, scenario, seed_bank_id)

    runs_root = runs_root or os.path.join(HERE, "runs", contract.campaign_id)
    ledger_owned = ledger is None
    ledger = ledger or Ledger(os.path.join(HERE, "stage3_trials.sqlite"))
    try:
        existing = ledger.find_by_cache_key(cache_key)
        if existing is not None and not force:
            if existing["status"] in (STATUS_SUCCESS, STATUS_SUCCESS_ZERO):
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
                    cached=True)
            # Re-run IN PLACE. Minting a new trial_id under the same cache key
            # would hit the UNIQUE constraint and the ledger write would be
            # silently ignored -- a no-op write is worse than an error, because
            # the run appears to have been recorded.
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
            contract_hash=contract.contract_hash(), code_fingerprint=code_fp,
            candidate=candidate, derived=derived, fidelity=fidelity,
            events_total=events_total, events_per_sub_run=events_per_sub_run,
            n_positions=n_positions, n_replicas=n_replicas, scenario=scenario,
            seed_bank_id=seed_bank_id, run_dir=run_dir, planned_sub_runs=planned)

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

        timeout_s = float(contract.fixed.get("sample_timeout_s") or 0)
        workers = int(contract.fixed.get("max_workers", 32))
        guard = MemoryGuard(total_gb=float(contract.fixed["total_mem_gb"]),
                            per_sample_gb=float(contract.fixed["per_sample_mem_gb"]),
                            poll_seconds=5.0).start()
        start = time.monotonic()
        failures = []
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo) or 1))) as pool:
                futures = {pool.submit(_run_one, sr, lattice_root, timeout_s, guard, logs_dir): sr
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
            ledger.set_trial_result(trial_id, STATUS_INCOMPLETE_SET,
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

        total, per_primary, per_electrode, n_hits = _score(contract, planned, events_per_sub_run)
        status = STATUS_SUCCESS if total > 0 else STATUS_SUCCESS_ZERO
        ledger.set_trial_result(trial_id, status, total_qps=total,
                                qps_per_primary=per_primary,
                                per_electrode_qps=per_electrode,
                                runtime_s=runtime_total, peak_rss_gb=peak_rss_gb)
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
                           peak_rss_gb=peak_rss_gb, run_dir=run_dir)
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
