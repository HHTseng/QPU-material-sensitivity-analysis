"""Stage 3 experiment contract: load, validate, and hash.

Checklist item 1 requires every value to be classified as exactly one of
`fixed`, `decision`, or `derived`, with none appearing in two classes. That is
enforced here rather than asserted in prose, because a value that is both fixed
and selectable is the kind of error that produces a plausible-looking campaign.

The contract also carries the code identity that goes into every cache key: if
the executable, the templates, the resolver or the Stage 2 scorer changes, trials
recorded before the change must not be reused.

Usage:
    from stage3_contract import load_contract
    contract = load_contract("stage3_config.yaml")
    contract.check_excitation_thresholds()
    print(contract.code_fingerprint())
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files whose content defines "the same computation". A change to any of them
# invalidates prior trials, so they are hashed into every cache key.
CODE_IDENTITY_FILES = (
    "stage1_run_simulations.py",
    "stage2_compute_QPs.py",
    "sensitivity_params.py",
    "sensitivity_utils.py",
    "sensitivity_memguard.py",
    "parameter_optimization/stage3_contract.py",
    "parameter_optimization/stage3_ledger.py",
    "parameter_optimization/stage3_trial_runner.py",
    # Added 2026-08-21 (Stage 4). The factorial audit found these three missing:
    # changing a film lifetime in the catalog, or the interface model, or the
    # macro template changed the generated macro while leaving the cache payload
    # identical -- so a lifetime-bracket run could return nominal cached results.
    "parameter_optimization/material_catalog.yaml",
    "parameter_optimization/interface_transmission.py",
    # Stage 4 property-space modules. Absent on a v2-only checkout, which is why
    # `code_fingerprint` tolerates a missing OPTIONAL file but not a required one.
    "parameter_optimization/stage4_space.py",
    "parameter_optimization/stage4_objectives.py",
)

# Files that may legitimately not exist (a Stage 3-only checkout). They are still
# hashed when present, so adding one later changes the fingerprint -- which is
# correct: the computation did change.
OPTIONAL_IDENTITY_FILES = (
    "parameter_optimization/stage4_space.py",
    "parameter_optimization/stage4_objectives.py",
)

REQUIRED_SECTIONS = ("fixed", "decision", "derived")


class ContractError(ValueError):
    """Raised when the contract is incomplete, ambiguous, or self-inconsistent."""


def _sha256_file(path):
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision():
    try:
        out = subprocess.run(
            ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        rev = out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None
    if not rev:
        return None
    try:
        dirty = subprocess.run(
            ["git", "-C", REPO_ROOT, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            rev += "-dirty"
    except (OSError, subprocess.SubprocessError):
        pass
    return rev


@dataclass
class Contract:
    campaign_id: str
    fixed: dict
    decision: dict
    derived: list
    path: str
    raw: dict = field(repr=False, default_factory=dict)

    # -- classification -----------------------------------------------------
    def _flat_fixed_keys(self):
        return set(self.fixed)

    def _flat_decision_keys(self):
        return set(self.decision)

    def validate(self):
        """Every key classified exactly once; decisions well-formed."""
        overlap = self._flat_fixed_keys() & self._flat_decision_keys()
        if overlap:
            raise ContractError(
                f"{sorted(overlap)} appear in both `fixed` and `decision`. A value "
                f"cannot be both a controlled constant and a selectable dimension."
            )
        derived_set = set(self.derived)
        for section, keys in (("fixed", self._flat_fixed_keys()),
                              ("decision", self._flat_decision_keys())):
            clash = derived_set & keys
            if clash:
                raise ContractError(
                    f"{sorted(clash)} are listed as `derived` but also set in "
                    f"`{section}`. Derived values are computed from a decision and "
                    f"must never be set by hand -- that is how a candidate ends up "
                    f"with one material's tensor and another's sound speed."
                )
        # Each decision must declare its allowed set and a value drawn from it.
        for name, spec in self.decision.items():
            if not isinstance(spec, dict):
                raise ContractError(f"decision.{name} must be a mapping, got {type(spec).__name__}")
            allowed_key = next((k for k in spec if k.startswith("allowed")), None)
            value_key = "miller" if name == "orientation" else "value"
            if allowed_key is None and "bounds" not in spec:
                raise ContractError(
                    f"decision.{name} declares neither an `allowed*` set nor "
                    f"`bounds`. An unbounded decision cannot be enumerated or "
                    f"validated."
                )
            if value_key not in spec:
                raise ContractError(f"decision.{name} declares no `{value_key}`.")
            if allowed_key is not None:
                if spec[value_key] not in spec[allowed_key]:
                    raise ContractError(
                        f"decision.{name}.{value_key}={spec[value_key]!r} is not in "
                        f"{allowed_key}={spec[allowed_key]!r}."
                    )
            else:
                # Continuous decision (Stage 4 property space): a closed interval
                # plus a default inside it. The full variable table lives in the
                # `space` section and is hashed with the contract.
                low, high = spec["bounds"]
                if not low <= spec[value_key] <= high:
                    raise ContractError(
                        f"decision.{name}.{value_key}={spec[value_key]!r} is outside "
                        f"bounds [{low}, {high}]."
                    )
        return self

    # -- physics gate -------------------------------------------------------
    def check_excitation_thresholds(self, top_film_gap_eV):
        """Hard gates: minEPhonons < 2*setTopGap <= E_gun.

        "Above minEPhonons" is not the gate: minEPhonons is a numerical tracking
        cut, not a physical threshold. Below 2*setTopGap no phonon can break a
        pair at the junction and the objective is identically zero for every
        material.

        The top-film gap is NOT an upper gate. It classifies the regime -- see
        the note at `ground_plane_active` below.
        """
        e_gun = float(self.fixed["gun_energy_eV"])
        min_e = float(self.fixed["min_e_phonons_eV"])
        junction_gate = 2.0 * float(self.fixed["setTopGap"])
        film_gate = 2.0 * float(top_film_gap_eV)

        def ueV(x):
            return f"{x * 1e6:.1f} ueV"

        if not min_e < junction_gate:
            raise ContractError(
                f"minEPhonons ({ueV(min_e)}) is not below the junction gate "
                f"({ueV(junction_gate)}): a numerical cut would preempt the physical "
                f"one and truncate the downconversion cascade."
            )
        if e_gun < junction_gate:
            raise ContractError(
                f"gun energy ({ueV(e_gun)}) is below the junction pair-breaking gate "
                f"({ueV(junction_gate)}); total_QPs would be identically zero for "
                f"every material. Being above minEPhonons is NOT sufficient."
            )
        if e_gun == junction_gate:
            raise ContractError(
                f"gun energy sits exactly on the gate ({ueV(e_gun)}); the outcome "
                f"would depend on one '>=' vs '>' comparison. Use positive margin."
            )
        # Not a failure: the Junction hit type means film absorptions are never
        # recorded, so the objective stays junction-only at any energy (measured
        # at 10 meV: 68/68 recorded hits inside junction footprints). Above the
        # film gap the ground plane becomes an ACTIVE ABSORBER competing for
        # phonons -- a physics regime that must be identical across a comparison
        # set, so it is recorded and checked for consistency, not forbidden.
        ground_plane_active = e_gun >= film_gate
        return {
            "min_e_phonons_eV": min_e,
            "junction_gate_eV": junction_gate,
            "gun_energy_eV": e_gun,
            "film_gate_eV": film_gate,
            "margin_above_junction_gate": e_gun / junction_gate,
            "ground_plane_active_absorber": bool(ground_plane_active),
            "e_gun_over_film_gate": e_gun / film_gate,
        }

    # -- event accounting ---------------------------------------------------
    def events_per_sub_run(self, fidelity=None):
        """Exact split of the per-candidate total across sub-runs.

        Refused rather than rounded: unequal statistics between candidates would
        be invisible downstream.
        """
        fidelity = fidelity or self.decision["fidelity"]["value"]
        total = int(self.decision["fidelity"]["events_total_per_candidate"][fidelity])
        sub_runs = int(self.fixed["n_positions"]) * int(self.fixed["n_replicas"])
        if total % sub_runs != 0:
            raise ContractError(
                f"fidelity {fidelity}: {total:,} events do not divide evenly into "
                f"{sub_runs} sub-runs ({total / sub_runs:.4f} each). Rounding would "
                f"give candidates unequal statistics."
            )
        return total // sub_runs

    # -- identity -----------------------------------------------------------
    def code_fingerprint(self):
        """Hashes of everything that defines 'the same computation'.

        Includes the macro template, because the template is an input to every
        generated sub-run: two campaigns run from different templates are not
        the same computation and must not share a cache key.
        """
        files = {name: _sha256_file(os.path.join(REPO_ROOT, name))
                 for name in CODE_IDENTITY_FILES}
        template = os.environ.get(
            "SENSITIVITY_MACRO_TEMPLATE",
            os.path.join(REPO_ROOT, "sensitivity_template_beamOn1e6.mac"))
        files["macro_template:" + os.path.basename(template)] = _sha256_file(template)
        missing = [n for n, h in files.items()
                   if h is None and n not in OPTIONAL_IDENTITY_FILES]
        files = {n: h for n, h in files.items() if h is not None}
        if missing:
            raise ContractError(
                f"Cannot fingerprint the code: missing {missing}. A cache key built "
                f"without them could reuse a trial produced by different code."
            )
        exe = os.environ.get(
            "SENSITIVITY_MAIN_EXE",
            os.path.join(os.path.expanduser("~"), "geant4_workdir", "bin", "Linux-g++", "Main"),
        )
        return {
            "git_revision": _git_revision(),
            "files": files,
            "main_exe": exe,
            "main_exe_sha256": _sha256_file(exe),
        }

    # ----------------------------------------------------------------------
    # Two identities, not one (audit finding N2)
    #
    # `contract_hash()` used to be both "which campaign is this" and "would a
    # re-run reproduce this simulation". Those are different questions, and
    # conflating them cost real compute: `max_workers`, `total_mem_gb`,
    # `per_sample_mem_gb` and `sample_timeout_s` live in `fixed`, so relaunching
    # the fidelity ladder from 32 to 64 workers minted new cache keys and
    # abandoned four in-flight trials that would otherwise have resumed. None of
    # those four values can change a completed simulation's result.
    #
    # The same conflation blocked legitimate reuse in the other direction: an
    # identical property vector evaluated under a different optimizer, a
    # different objective, a widened search box or a renamed campaign is the
    # SAME simulation and should hit the cache.
    #
    #   campaign_contract_hash()   -- the whole declaration, for provenance and
    #                                 for deciding which trials may be replayed
    #                                 into one optimizer's history.
    #   simulation_identity_hash() -- only what can change the generated macro,
    #                                 the resolved material, the ordered
    #                                 scenario, the seeds, the event count or the
    #                                 scored physics. This is what the cache key
    #                                 is built from.
    # ----------------------------------------------------------------------

    # Execution-only knobs: how hard the machine is driven, never what is
    # computed. Adding one here is a claim that it cannot change a completed
    # trial's result -- justify it in the comment.
    EXECUTION_ONLY_FIXED_KEYS = (
        "max_workers",          # thread-pool width over independent sub-runs
        "total_mem_gb",         # memory guard ceiling
        "per_sample_mem_gb",    # per-sub-run memory estimate for that guard
        "sample_timeout_s",     # watchdog; a trial it kills is INCOMPLETE, never
                                # scored, so it cannot alter a successful result
        "sample_stall_timeout_s",   # progress watchdog, same argument
    )

    # Top-level sections that describe the SEARCH rather than the simulation.
    # A point is simulated identically whatever proposed it.
    CAMPAIGN_ONLY_SECTIONS = ("space", "stage4", "optimizer", "description")

    def _identity_payload(self, simulation_only):
        raw = self.raw or {}
        skip = ("campaign_id", "fixed", "decision", "derived")
        extra = {k: v for k, v in raw.items() if k not in skip}
        fixed = dict(self.fixed)
        if simulation_only:
            for key in self.EXECUTION_ONLY_FIXED_KEYS:
                fixed.pop(key, None)
            extra = {k: v for k, v in extra.items()
                     if k not in self.CAMPAIGN_ONLY_SECTIONS}
        payload = {"fixed": fixed, "decision": self.decision,
                   "derived": sorted(self.derived), "other_sections": extra}
        if not simulation_only:
            payload["campaign_id"] = self.campaign_id
        return json.dumps(payload, sort_keys=True, default=str)

    def campaign_contract_hash(self):
        """Identity of the CAMPAIGN: every section, including its name.

        Two campaigns with different bounds, objectives or names are different
        campaigns and their trials must not be merged into one optimizer
        history -- which is what `stage4_optimize.resume()` uses this for.
        """
        return hashlib.sha256(self._identity_payload(False).encode()).hexdigest()

    def simulation_identity_hash(self):
        """Identity of the SIMULATION: what a re-run would have to match.

        Excludes resource limits, the watchdog, the campaign name, and the
        search/scoring metadata. Everything else in the contract is included,
        because the safe default for an unclassified field is "it might matter".
        """
        return hashlib.sha256(self._identity_payload(True).encode()).hexdigest()

    def contract_hash(self):
        """Backwards-compatible alias for the CAMPAIGN hash.

        Kept because it is what the ledger's `contract_hash` column has always
        held and what `resume()` compares against. New code should say which
        identity it means.
        """
        return self.campaign_contract_hash()


def load_contract(path):
    path = os.path.abspath(path)
    with open(path) as handle:
        raw = yaml.safe_load(handle)
    missing = [s for s in REQUIRED_SECTIONS if s not in raw]
    if missing:
        raise ContractError(f"{path}: missing required section(s) {missing}")
    if "campaign_id" not in raw:
        raise ContractError(f"{path}: missing campaign_id")
    contract = Contract(
        campaign_id=raw["campaign_id"],
        fixed=raw["fixed"] or {},
        decision=raw["decision"] or {},
        derived=list(raw["derived"] or []),
        path=path,
        raw=raw,
    )
    return contract.validate()


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "stage3_config.yaml")
    c = load_contract(target)
    print(f"Contract OK: {c.campaign_id}")
    print(f"  fixed={len(c.fixed)} decision={len(c.decision)} derived={len(c.derived)}")
    print(f"  campaign_contract_hash  ={c.campaign_contract_hash()[:16]}")
    print(f"  simulation_identity_hash={c.simulation_identity_hash()[:16]}")
    # Nb baseline gap; the resolver supplies this per candidate.
    gate = c.check_excitation_thresholds(top_film_gap_eV=1.5384e-3)
    print(f"  thresholds OK: gun is {gate['margin_above_junction_gate']:.2f}x the "
          f"junction gate; ground plane "
          f"{'ACTIVE absorber' if gate['ground_plane_active_absorber'] else 'transparent'} "
          f"(E_gun / 2*film_gap = {gate['e_gun_over_film_gate']:.2f})")
    for fid in c.decision["fidelity"]["allowed"]:
        print(f"  {fid}: {c.events_per_sub_run(fid):,} events per sub-run")
    fp = c.code_fingerprint()
    print(f"  git={fp['git_revision']}")
