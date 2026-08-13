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
            if allowed_key is None:
                raise ContractError(
                    f"decision.{name} declares no `allowed*` set. An unbounded "
                    f"decision cannot be enumerated or validated."
                )
            value_key = "miller" if name == "orientation" else "value"
            if value_key not in spec:
                raise ContractError(f"decision.{name} declares no `{value_key}`.")
            if spec[value_key] not in spec[allowed_key]:
                raise ContractError(
                    f"decision.{name}.{value_key}={spec[value_key]!r} is not in "
                    f"{allowed_key}={spec[allowed_key]!r}."
                )
        return self

    # -- physics gate -------------------------------------------------------
    def check_excitation_thresholds(self, top_film_gap_eV):
        """minEPhonons < 2*setTopGap <= E_gun < 2*top_film_gap.

        "Above minEPhonons" is not the gate: minEPhonons is a numerical tracking
        cut, not a physical threshold. Below 2*setTopGap no phonon can break a
        pair at the junction and the objective is identically zero for every
        material; at or above 2*top_film_gap the ground plane also absorbs and
        the objective stops being junction-only.
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
        if e_gun >= film_gate:
            raise ContractError(
                f"gun energy ({ueV(e_gun)}) is at or above the top-film gap "
                f"({ueV(film_gate)}); the ground plane would also absorb and the "
                f"objective would no longer be junction-only QPs."
            )
        return {
            "min_e_phonons_eV": min_e,
            "junction_gate_eV": junction_gate,
            "gun_energy_eV": e_gun,
            "film_gate_eV": film_gate,
            "margin_above_junction_gate": e_gun / junction_gate,
            "margin_below_film_gate": film_gate / e_gun,
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
        """Hashes of everything that defines 'the same computation'."""
        files = {name: _sha256_file(os.path.join(REPO_ROOT, name))
                 for name in CODE_IDENTITY_FILES}
        missing = [n for n, h in files.items() if h is None]
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

    def contract_hash(self):
        """Stable hash of the whole contract, for the ledger and cache key."""
        payload = json.dumps(
            {"campaign_id": self.campaign_id, "fixed": self.fixed,
             "decision": self.decision, "derived": sorted(self.derived)},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


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
    print(f"  contract_hash={c.contract_hash()[:16]}")
    # Nb baseline gap; the resolver supplies this per candidate.
    gate = c.check_excitation_thresholds(top_film_gap_eV=1.5384e-3)
    print(f"  thresholds OK: gun is {gate['margin_above_junction_gate']:.2f}x the "
          f"junction gate and {gate['margin_below_film_gate']:.2f}x below the film gate")
    for fid in c.decision["fidelity"]["allowed"]:
        print(f"  {fid}: {c.events_per_sub_run(fid):,} events per sub-run")
    fp = c.code_fingerprint()
    print(f"  git={fp['git_revision']}")
