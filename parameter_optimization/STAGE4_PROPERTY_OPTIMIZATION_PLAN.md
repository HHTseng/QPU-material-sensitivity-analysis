# Stage 4 — Property-Space Optimization and Nearest-Material Projection

Branch: `Material_optimization_v3_scan_parameters`.
Status: plan, written before implementation. Supersedes nothing in the Stage 3
record; it opens the second of the two interpretations that
[`STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md`](STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md)
§3.2 kept explicitly separate:

> 1. **Real-material search** — material IDs are categorical, properties travel
>    as a linked bundle. *(This is what `Material_optimization_v2` did: 18
>    triplets, converged at 1e7 events per sub-run, `Ge/Nb/Cu` best at −36.0%.)*
> 2. **Property-space design** — continuous properties are optimized inside a
>    physically constrained region to learn an ideal target specification. The
>    output is a property target, not a claim that a real material exists. **A
>    second nearest-candidate/projection step is required.**

Stage 4 is interpretation 2 plus that projection step. Two deliverables:

| # | Deliverable | Artifact |
|---|---|---|
| 1 | Several interchangeable optimizers searching the tunable-parameter space to minimize QP generation | `stage4_optimize.py` + `stage4_optimizers.py` + `stage4_objectives.py` |
| 2 | The optimum, plus the real materials whose measured properties sit closest to it, plus a re-simulation of those real materials | `stage4_project_material.py` + `results/stage4_*` |

The claim this stage may support, stated up front so the design can be checked
against it:

> *"Within the declared property box, this simulation model, this fixed
> injection scenario set and this event budget, the QP-minimizing property
> target is **p\***, and the closest real, mechanically stable, non-metallic
> cubic substrate / superconducting film / normal film to **p\*** are X, Y, Z —
> which, when simulated with their own measured properties, deliver a fraction
> f of the ideal improvement."*

It may **not** support "material X is optimal", because the projection distance
and the model-dependence of the interface treatment both bound the claim.

---

## 1. What is reused unchanged, and why

Stage 4 does **not** re-derive the evaluation machinery. The following are
already audited (see `STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md`, the
2026-08-16 integrity audit in `STAGE3_GAP_REMEDIATION.md`) and are consumed as
libraries:

| Component | File | Role in Stage 4 |
|---|---|---|
| Experiment contract, classification and hashing | `stage3_contract.py` | extended with a `space` section for continuous decisions |
| Trial ledger (planned-before-launch identities, cache keys) | `stage3_ledger.py` | extended with per-sub-run QP counts and optimizer bookkeeping |
| Single-trial evaluator (macro generation, process-group timeout, memory guard, strict completeness, Stage 2 scoring) | `stage3_trial_runner.py` | extended with a **resolver hook** so a pseudo-material resolves through the same path |
| Interface model | `interface_transmission.py` | unchanged; called with pseudo-material (ρ, v) instead of catalog records |
| Derived sound speeds (Christoffel spherical average) | `stage1_run_simulations.py::derive_cubic_sound_speeds` | unchanged |
| QP scoring | `stage2_compute_QPs.py::calculate_QPs` | unchanged |
| Injection-site set (16 scrambled-Sobol positions × 2 replicas, common across candidates) | contract `fixed` | unchanged — this is the spatial common-random-number set |

The rules that must not be weakened, restated because an optimizer stresses
them harder than an enumeration did:

* a missing, failed, timed-out or aborted sub-run is **never** scored as `Q = 0`;
* an incomplete scenario set is **never** scored at all;
* completeness is judged against identities planned **before** launch;
* every value the simulation consumes is written into the cache key.

An optimizer makes the third and fourth rules load-bearing in a way enumeration
did not: it will actively seek out regions where the simulation is cheap or
fails, and a failure silently scored as zero QPs is exactly what it is looking
for. §5.6 covers this explicitly.

---

## 2. The property-space contract

### 2.1 Decision variables (the scan)

Taken from the "Tune the following parameters" block of
[`parameter_set.txt`](parameter_set.txt). Baselines are the `Si / Nb / Cu`
values that anchor every comparison. Bounds are grounded in measured data, not
in `0.5x-1.5x` factors: substrate elastic bounds come from the 396 accepted
cubic materials in `catalog/substrates_raw.json`, phonon-constant bounds from
the seven shipped G4CMP lattice records, and film bounds from the elemental
superconductors and normal metals in `material_catalog.yaml`.

| # | Variable | Unit | Baseline | Bounds | Scale | Consumed by |
|---:|---|---|---:|---|---|---|
| 1 | `topfilm_vsound` | km/s | 2.444 | [1.5, 6.0] | linear | macro `setTopFilmVSound` |
| 2 | `topfilm_gap` | eV | 1.5384e-3 | [5.0e-5, 3.5e-3] | log | macro `setTopFilmGap` |
| 3 | `topfilm_ph_lifetime` | ns | 0.00417 | [1e-3, 1.0] | log | macro `setTopFilmPhLifetime` |
| 4 | `topfilm_density` | kg/m³ | 8570 | [2000, 20000] | linear | interface model → `setTopFilmAbs` |
| 5 | `bot_vsound` | km/s | 2.608 | [1.5, 6.0] | linear | macro `setBotVSound` |
| 6 | `bot_gap_thres` | eV | 180e-6 | [5.0e-5, 4.0e-4] | linear | macro `setBotGapThres` |
| 7 | `bot_ph_lifetime` | ns | 5.1 | [0.5, 50.0] | log | macro `setBotPhLifetime` |
| 8 | `bot_density` | kg/m³ | 8960 | [2000, 22000] | linear | interface model → `setBotAbs` |
| 9 | `sub_c11` | GPa | 165.6 | [20, 450] | linear | config `stiffness 1 1`, derived speeds |
| 10 | `sub_c12` | GPa | 63.9 | [0, 250] | linear | config `stiffness 1 2`, derived speeds |
| 11 | `sub_c44` | GPa | 79.5 | [5, 200] | linear | config `stiffness 4 4`, derived speeds |
| 12 | `sub_scat` | s³ | 2.43e-42 | [1e-44, 5e-41] | log | config `scat` (isotope scattering) |
| 13 | `sub_decay` | s⁴ | 7.41e-56 | [5e-57, 5e-54] | log | config `decay` (anharmonic decay) |
| 14 | `sub_decayTT` | — | 0.74 | [0.50, 1.00] | linear | config `decayTT` |
| 15 | `temperature` | K | 0.0 | [0.0, 0.1] | linear | macro `/g4cmp/temperature` |
| 16 | `lattice_deg` | deg | 45 | [0, 90] | linear | macro `setLatticeDeg` |
| 17 | `miller` | — | (0,0,1) | 10 symmetry-reduced directions | categorical | macro `setMiller` |

Bounds evidence:

* `scat`: Al₂O₃ 0.025e-42 … Ge 3.67e-41 s³ (span 1470×) across the shipped
  records; the box brackets that range.
* `decay`: Si 7.41e-56 … Ge 1.6456e-54 s⁴; the box brackets it.
* `decayTT`: LiF 0.68 … GaAs 0.778 observed; `parameter_set.txt` allows up to 1.
* `C11/C12/C44`: MP cubic snapshot p5/p95 = 16/342, 5/118, 5/124 GPa,
  max 570/191/241. The box is the p5–p95 range widened toward the observed max,
  intersected with the Born-stability gate (§2.5).
* `topfilm_gap`: Ti 0.061 meV … Nb 1.5384 meV measured; the box reaches
  3.5 meV so Nb₃Sn/NbN-class gaps are representable while `2Δ_film` stays below
  `E_gun = 10 meV`, keeping the ground-plane regime constant (§2.5).

`miller` is categorical because `G4LatticePhysical::SetMillerOrientation`
takes `(G4int h, G4int k, G4int l, G4double rot)`. A Fibonacci-sphere direction
cannot be represented and would truncate toward zero — the same finding that
closed B1 in the Stage 3 record. The allowed set is generated once by cubic
symmetry reduction of integer triples with `max(|h|,|k|,|l|) <= 3`, deduplicated
under permutation and sign and reduced by their greatest common divisor, sorted
by index magnitude -- **13** directions:

```
(0,0,1) (0,1,1) (1,1,1) (0,1,2) (1,1,2) (1,2,2) (0,1,3) (1,1,3) (0,2,3)
(1,2,3) (2,2,3) (1,3,3) (2,3,3)
```

`lattice_deg` is the rotation about that axis and stays continuous.

### 2.2 Fixed (identical for every candidate)

Everything in the `fixed` block of `stage3_config.yaml` carries over unchanged:
Al junction (`setTopVSound 3.582`, `setTopGap 191 µeV`, lifetime 0.242/0.29,
`QPLim 3`, thickness 0.12 µm), the three film thicknesses, the four
specularities, `setWallAbs 0.02`, `setBotGap 0`, `chargeBounces 1`,
`phononBounces 10000`, `clearance 1e-6 mm`, e/h trapping MFP 0.3 mm, the 17
electrode coordinates, the 10×10×0.525 mm geometry, the 16 Sobol injection
sites, the seed bank, `E_gun = 10 meV`, `minEPhonons = 38.2 µeV`, and the
third-order `dyn` tensor at Si values.

Two of those deserve a comment because Stage 4 makes them *look* tunable:

* **`dyn` (third-order elasticity)** stays at Si's `[-42.9, -94.5, 52.4, 68.0]`
  GPa for every pseudo-material, per `parameter_set.txt` ("too complicated to
  calculate, no observed sensitivity"). It is a Si constant inside a non-Si
  simulation and belongs in the limitations, exactly as the Stage 3 record
  already says.
* **`LDOS/STDOS/FTDOS`** (Tamura mode fractions) also stay at Si's. A
  pseudo-material is therefore explicitly *"the Si lattice record with the
  scanned fields overridden"*, not a from-scratch crystal. This is written into
  the generated config header so no reader can mistake it.

### 2.3 Derived (computed, never proposed)

| Derived | From | Gate |
|---|---|---|
| `vsound`, `vtrans` | Christoffel spherical average of (C11, C12, C44, ρ) | `0 < vtrans < vsound` |
| `setTopAbs` | interface model: substrate (ρ, v_eff) vs fixed Al | ∈ [0,1] |
| `setTopFilmAbs` | interface model: substrate vs (`topfilm_density`, `topfilm_vsound`) | ∈ [0,1] |
| `setBotAbs` | interface model: substrate vs (`bot_density`, `bot_vsound`) | ∈ [0,1] |
| `substrate_density` | the Geant4 carrier material (§2.4) | matches the runtime log |
| `ground_plane_active_absorber` | `E_gun >= 2 * topfilm_gap` | must equal the campaign's declared regime |
| `events_per_sub_run` | `events_total / (n_positions * n_replicas)` | must divide exactly |

Keeping `vsound`/`vtrans` derived is not stylistic. Sampling them independently
is what produced the mid-run `G4LatticeLogical::LookupKtoVg` SIGSEGV on the
first material branch — 31/31 affected design points crashed with a zero-byte
hits file (Fisher exact p = 4.3e-18). Deriving them from a Born-stable tensor
makes the inverted region unreachable by construction (0 of 2980 sampled
Born-stable tensors inverted the ordering).

### 2.4 What is deliberately **not** scanned, and the evidence

**Substrate mass density.** G4CMP takes the substrate density from the Geant4
material (`G4LatticeManager::LoadLattice` → `SetDensity(Mat->GetDensity())`),
and the executable only accepts a NIST material *name*
(`/main/detector_param/setSubstrateG4Name`). There is no UI command for an
arbitrary density; `G4NistManager::BuildMaterialWithNewDensity` exists in C++
but is not exposed to the macro layer. Three consequences:

1. Density is quantized to the verified carrier set
   `{G4_Si: 2330, G4_Ge: 5323, G4_GALLIUM_ARSENIDE: 5310 kg/m³}` — the three
   whose densities were measured from live runs.
2. **Fixing it costs almost nothing**, because density enters the phonon
   objective only through `v = sqrt(C/ρ)` and through the acoustic impedance
   `Z = ρ·v_eff` at the boundaries. `C11/C12/C44` are free over a 20× range, so
   every achievable velocity is reachable at fixed ρ; and the impedance channel
   is retained through the two film densities. Density is therefore redundant
   *given* the tensor, not neglected.
3. The projection step (§8) reverses the quantization honestly: a real
   candidate is scored on **(v_L, v_T, anisotropy, Z)** computed from *its own*
   (C, ρ), never on raw C values, so a real material with a different density is
   compared like for like.

`substrate_carrier` is nevertheless implemented as a categorical decision with
the three verified carriers, defaulted to `G4_Si` and disabled in campaign 1.
`stage4_probe_g4_density.py` extends the carrier table by measuring any NIST
material's density from a one-event run, so a later campaign can widen it
without guessing.

**Debye energy.** Measured inert for this objective: Ge at 4e6 events with
`Debye 2 THz` vs `7.8 THz` gave **144 vs 144 QPs — bit-identical**, not merely
statistically indistinguishable (Poisson σ would have been 17). Debye is
consumed by `G4CMPEnergyPartition::GeneratePhonons()`, which the
`phonon_Caustic` gun bypasses. Held fixed at the base record's value; the
result expires if the gun type ever changes to one that deposits energy.

**Lattice constant `cubic a`.** Source-verified inert for phonons: it sets
`fBasis`, which is used for (a) the Miller-index *direction*
(`(h·b₀+k·b₁+l·b₂).unit()` — scale-invariant for a cubic cell) and (b)
`G4CMPChargeCloud`, which no phonon run touches. It is written into the
generated config for provenance and it participates in the projection metric,
but it is **excluded from the active search space** and its inertness is proven,
not assumed, by an A/B trial (`a = 5.431` vs `6.5 Å`, same seeds → require
bit-identical `total_QPs`), recorded like the Debye A/B.

**`/g4cmp/temperature`.** Kept **in** the space (`parameter_set.txt` lists it as
tunable, bounds [0, 0.1] K) but flagged `suspected_inert` and given the same
A/B test at the campaign pilot. If it proves bit-identical it is demoted to
`fixed` and the space drops to 15 continuous dimensions + 1 categorical, which
is a real gain in sample efficiency.

### 2.5 Hard gates — all evaluated before Geant4 starts

```
G1  Born stability          C11 - C12 > 0,  C11 + 2*C12 > 0,  C44 > 0
G2  Christoffel positivity  all eigenvalues of Gamma(n) > 0 for every sampled n
G3  Mode ordering           0 < vtrans < vsound
G4  Interface probabilities setTopAbs, setTopFilmAbs, setBotAbs all in [0,1]
G5  Excitation chain        minEPhonons < 2*setTopGap <= E_gun, with margin
G6  Regime consistency      2*topfilm_gap < E_gun  (ground plane active for all)
G7  Bottom threshold        0 < bot_gap_thres <= 2*setTopGap
G8  Event accounting        events_total divisible by n_positions * n_replicas
G9  Unit hygiene            every generated macro line re-parsed; no double
                            suffixes, no bare exponent-with-unit forms
```

G1–G3 are the crash-prevention gates. G9 exists because a single malformed unit
suffix once made Geant4 emit a warning, skip `/run/beamOn` and **exit 0** for
3,200 sub-runs — the failure mode that looks exactly like a weak physical
response. A rejected proposal is recorded as `constraint_rejected` and is
**not** an observation; the optimizer re-proposes (§5.6).

---

## 3. Evaluator changes — three hooks, no fork

`stage3_trial_runner.py` is extended, not duplicated. The Stage 3 factorial path
must keep producing byte-identical macros and identical results.

```python
# stage3_trial_runner.py

def evaluate(contract, candidate, fidelity=None, seed_bank_id=None, ledger=None,
             runs_root=None, force=False, verbose=True, debye_override_THz=None,
             resolver=None):                       # <-- NEW
    """resolver(contract, candidate) -> (resolved, derived)

    Defaults to the Stage 3 catalog resolver, so the v2 path is unchanged.
    A Stage 4 property-space candidate resolves through stage4_space.resolve,
    which returns the SAME shapes plus two extra keys in `derived`:

        derived["config_overrides"] : {"stiffness 1 1": (165.6, " GPa"), ...}
        derived["macro_overrides"]  : {"/g4cmp/temperature ": "0.0 K", ...}
    """
    resolve = resolver or resolve_candidate
    resolved, derived = resolve(contract, candidate)
    ...
```

`_write_lattice_config` applies `derived["config_overrides"]` after the
`vsound`/`vtrans` rewrite; `_write_sub_run_macro` applies
`derived["macro_overrides"]` after the standard settings list and then re-parses
every written line (gate G9). Because `derived` is already inside
`compute_cache_key`, every override enters the cache identity for free.

**Cache identity completion** (the blocker the factorial audit raised, now
fixed as part of this work):

```python
CODE_IDENTITY_FILES = (
    ...,                                   # existing eight
    "parameter_optimization/material_catalog.yaml",      # <-- was missing
    "parameter_optimization/interface_transmission.py",  # <-- was missing
    "parameter_optimization/stage4_space.py",
    "parameter_optimization/stage4_objectives.py",
)
# plus the resolved macro template's own sha256, which varies per campaign
```

This invalidates the v2 cache keys by design: a trial produced by different
inputs must not be reused. The v2 ledgers stay readable, and the recorded v2
results are unaffected — they were audited by direct re-scoring of their hit
files, not by cache identity.

**Per-sub-run scoring.** `_score` currently pools all 32 hit files and scores
once. Stage 4 scores each sub-run separately and sums, which is *exactly*
equivalent (`calculate_QPs` rounds `E_dep/gap` per hit and assigns per hit — no
cross-sub-run term exists) and yields the per-position and per-replica
breakdown that the robust objectives (§4) and the noise model (§6) need. Ledger
gains `sub_runs.total_qps` and `sub_runs.per_electrode_qps`; the equality
`sum(sub-run scores) == pooled score` is asserted on every trial and is one of
the exit gates.

---

## 4. Objectives — a registry, switchable by name

```python
# stage4_objectives.py

OBJECTIVES = {}                      # name -> Objective

@dataclass
class Objective:
    name: str
    direction: str = "minimize"
    transform: str = "log1p"         # what the surrogate is fitted on
    fn: Callable                     # (TrialResult, SubRunTable) -> ObjectiveValue

@dataclass
class ObjectiveValue:
    value: float                     # the number the optimizer minimizes
    raw: float                       # untransformed, for reporting
    se: float                        # standard error from the sub-run blocks
    n_blocks: int
    detail: dict                     # per-position / per-electrode breakdown

def register(name, **kw): ...        # decorator
```

Shipped objectives:

| name | definition | why it exists |
|---|---|---|
| `total_qps_per_primary` | `sum_subruns(QP) / N_primary` | the v2 objective; default, so v3 rankings are comparable to the converged v2 table |
| `total_qps` | raw count | reporting form; identical ranking at fixed budget |
| `robust_mean_plus_se` | `mean_b(q_b) + λ·SE_b(q_b)` over the 32 (position, replica) blocks, λ=1 | pipeline §2's J(x): prefers a candidate that is good *and* stable, not a lucky draw |
| `p90_position` | 90th percentile of the 16 site means | site yield spans 9.18× (CV 60.6%); this optimizes the bad sites instead of the average |
| `max_electrode_qps_per_primary` | worst single electrode | peak per-qubit burden — the quantity a surface code actually cares about, closer to PLE without pretending to be PLE |
| `weighted_sum` | `α·mean + β·max_electrode` | for a declared trade-off; α, β must be written into the campaign config and therefore into the cache key |

Switching is one line of config or `--objective p90_position`. The objective
name is recorded in the ledger and in the campaign manifest, so two campaigns
optimizing different objectives can never be merged by accident.

Surrogate fitting uses `log1p(q)` by default (counts, right-skewed, Fano ≈ 5),
while **ranking and reporting always use untransformed QP counts at a common
event budget**.

---

## 5. Optimizers — a registry, switchable by name

Common ask/tell interface, so every algorithm is interchangeable and can be
compared on identical axes (best-so-far vs cumulative simulated events):

```python
class Optimizer(Protocol):
    def ask(self, n: int) -> list[Candidate]: ...
    def tell(self, candidate: Candidate, value: ObjectiveValue) -> None: ...
    def tell_rejected(self, candidate: Candidate, reason: str) -> None: ...
    def state(self) -> dict: ...          # for restart
    def load_state(self, d: dict) -> None: ...
```

### 5.1 `random` — uniform baseline (mandatory)

Uniform in the *transformed* box (log where declared), rejection-resampled
against G1–G7. Reported alongside every model-based method; the pipeline
document requires it and it is the only way to tell whether the surrogate is
doing anything.

### 5.2 `sobol` — scrambled Sobol design

Space-filling initial design (and a second baseline). Power-of-two batches only;
categorical `miller` assigned by a balanced round-robin over the Sobol order so
each direction gets equal coverage.

### 5.3 `bo_gp` — Gaussian-process Bayesian optimization (the requested baseline model)

Self-contained (numpy + scipy only — the `G4CMP` env has no sklearn/botorch and
must not be disturbed):

* **Kernel** `k = Matern5/2(anisotropic ARD over the 16 continuous dims) ×
  CategoricalOverlap(miller) + WhiteNoise`.
* **Targets** `y = log1p(q)`; per-point observation variance from the
  block-bootstrap SE (heteroscedastic — the diagonal is *measured*, not fitted).
* **Hyperparameters** by maximizing the log marginal likelihood with
  L-BFGS-B from 8 restarts, refitted every iteration until n=50 then every 5.
* **Acquisition** log Expected Improvement against the best *posterior mean*
  (not the best noisy observation — the standard noisy-BO correction), maximized
  by 4096 Sobol candidates + L-BFGS-B polish on the top 8.
* **Batches** via the constant-liar (Kriging-believer) rule so `q` candidates
  can be dispatched to `q` concurrent Geant4 groups.

```python
def ask(self, n):
    if len(self.X) < self.n_init:
        return self.sobol.ask(n)
    self.gp.fit(self.X, self.y, noise_var=self.s2)
    out, fantasy = [], self.gp.copy()
    for _ in range(n):
        best_mean = fantasy.posterior_mean(self.X).min()
        x = argmax_over_feasible(lambda z: log_ei(fantasy, z, best_mean))
        out.append(x)
        fantasy.add_fantasy(x, mu=fantasy.predict(x)[0])      # constant liar
    return out
```

### 5.4 `cmaes` — CMA-ES (my recommendation alongside BO)

Rationale: 16 continuous dimensions with ~5% observation noise is the regime
where a GP's model risk starts to bite and where CMA-ES is famously robust. It
is implemented natively (~150 lines: rank-µ update, evolution path, step-size
control) with two noise-aware settings — population 12, and re-evaluation of the
incumbent every generation so drift in the machine or the model is visible.
`miller` is handled by an outer categorical loop (one CMA-ES state per active
direction, budget shared by the best-so-far rule).

### 5.5 `llm_agent` and `llm_bo` — agentic proposal via Ollama (§7)

### 5.6 Constraint and failure handling (all optimizers)

```
propose x  ->  validate(x)  ->  reject? resample (<=200 tries) & log
           ->  evaluate(x)
           ->  status success / success_zero      -> tell(value)
           ->  status constraint_rejected         -> tell_rejected, no observation
           ->  status timeout/failed/incomplete   -> tell_rejected + QUARANTINE
```

`QUARANTINE`: a candidate whose *simulation* failed is retried once; a second
failure marks the region and is reported. Failures are never zeros, never
imputed, and never silently dropped from the campaign summary — the report
prints `n_success / n_rejected / n_failed` for every optimizer, because an
optimizer that "wins" by finding a region that fails cheaply must be visible.

### 5.7 Optional third-party adapters

`optuna` (TPE) and `botorch`/`ax` (qLogNEI, multi-fidelity KG) adapters are
included behind import guards and are *not* installed by default. They register
themselves only if importable, so `--optimizer tpe_optuna` either works or says
exactly which `pip install` in which environment enables it. The Stage 3 record
requires a separate env for these; the `G4CMP` env stays untouched.

---

## 6. Fidelity, noise and replication

The v2 scaling study is the calibration and is reused rather than re-measured:

| Tier | events / sub-run | events / candidate | wall (32 workers, idle host) | corrected 1σ on `total_QPs` |
|---|---:|---:|---:|---:|
| **S** screening | 125,000 | 4.0e6 | ~41 s | **5.0%** |
| **M** selection | 1,000,000 | 3.2e7 | ~7 min | ~1.8% |
| **L** confirmation | 10,000,000 | 3.2e8 | ~1.5 h | **0.56%** |

Errors are Poisson × 2.3, the measured Fano ≈ 5 overdispersion (compound counts:
each hit contributes `round(E_dep/191 µeV)` ≈ 2.7 QPs, and the 16 sites span
9.18×). **Every naive Poisson z in this project is optimistic by 2.3×**; Stage 4
reports block-bootstrap intervals over the 32 (position, replica) blocks
instead.

Policy:

* optimize at **S**; the property-space spread is expected to exceed the v2
  material spread (4.4×), so 5% is adequate for search;
* promote the top ~10 to **M** with a *held-out seed bank* before any ranking
  claim;
* confirm the top 3 and the projected real materials at **L**, held-out seeds,
  same 16 sites;
* the site set and seed bank are identical across candidates at every tier
  (spatial + stochastic common random numbers), so paired differences cancel the
  60.6% site heterogeneity;
* `stage4_report.py` prints the S→M and M→L rank correlations. If S does not
  predict M, S is dropped from selection rather than kept because it is cheap —
  exactly what the v2 study concluded about the 125k tier for *its* final
  ranking.

Known residual systematics, unchanged by this stage and stated in every report:
the 16-site quadrature is **not** converged (position 11 alone contributed 49.6%
of the baseline's QPs), and the interface model's `physics_validation_passed` is
still `False`.

---

## 7. Agentic optimizer via Ollama

### 7.1 What the agent is, and what it is not

It is a **proposal generator inside a validated loop**, not an authority. Every
proposal passes the same G1–G9 gates, is simulated by the same evaluator, and is
scored by the same objective. The LLM never reports a number it did not get from
a simulation, and nothing it says is written to the ledger except its proposals
and its stated rationale.

Two modes:

* **`llm_agent`** — the LLM sees the campaign context and the trial history and
  proposes the next batch directly. The honest, unassisted version.
* **`llm_bo`** — the LLM proposes an over-complete pool (e.g. 32 candidates)
  and the GP surrogate ranks them by log-EI, dispatching the best `q`. This is
  the recommended agentic mode: the LLM supplies physics-informed *diversity*
  and the GP supplies *calibration*, so a hallucinated proposal costs nothing
  beyond one acquisition evaluation.

### 7.2 Transport and prompt

Ollama's HTTP API (`POST {OLLAMA_HOST}/api/chat`, `format: "json"`,
`options.temperature`, `options.seed` for reproducibility). No new Python
dependency — `requests` is already in the env. Host and model come from
`stage4_config.yaml` / `OLLAMA_HOST` / `STAGE4_LLM_MODEL`.

```
SYSTEM: You propose candidate parameter vectors for a Geant4/G4CMP simulation
        that counts quasiparticles generated at 17 Al junctions by an injected
        10 meV phonon burst in a 525 um substrate. Lower is better.
        You must answer with JSON only, matching the given schema.
        Every variable must lie inside its stated bounds.
        You must not invent measured values or claim results.

USER:   ## Variables            <name, unit, bounds, scale, physical meaning>
        ## Hard constraints     <G1..G7 in plain language>
        ## Physics notes        <phonon downconversion, pair-breaking gate,
                                 acoustic mismatch, isotope scattering; from the
                                 project documents, quoted not paraphrased>
        ## History              <the K best and K most recent trials: full
                                 vectors, objective, SE, status>
        ## Current incumbent    <vector + objective + SE>
        ## Task                 Propose N NEW candidates, diverse, each with a
                                 one-sentence physical rationale. Prefer moves
                                 that a phonon-transport argument supports.

SCHEMA: {"candidates":[{"values":{<var>: <float|str>, ...},
                        "rationale":"<=200 chars"}]}
```

### 7.3 Guard rails (the part that makes this safe to run unattended)

```python
def llm_ask(n):
    for attempt in range(RETRIES):                      # 3
        raw = ollama_chat(prompt, model, seed=campaign_seed + attempt)
        cands, errs = parse_and_validate(raw)           # schema, bounds, gates,
                                                        # dedup vs history (L2 in
                                                        # normalized space < 1e-3)
        if len(cands) >= n:
            return cands[:n]
        prompt = prompt + repair_message(errs)          # tell it exactly what failed
    log("LLM proposals insufficient -> falling back")
    return fallback.ask(n - len(cands)) + cands         # bo_gp, or sobol if cold
```

* out-of-bounds values are **clipped and flagged**, never silently accepted;
* infeasible proposals (G1–G7) are rejected and the reason is fed back in the
  repair message, which is itself a form of tool use the model can learn from
  within one campaign;
* duplicates of already-evaluated points are dropped;
* the whole conversation (prompt hash, model, seed, raw response, accepted /
  rejected counts) is written to `runs/<campaign>/llm/` so the agentic run is
  auditable;
* if Ollama is unreachable the optimizer degrades to `bo_gp` and says so in the
  report rather than stalling.

### 7.4 Deployment status on this host

`ollama` is **not installed** on mimir today (`which ollama` → not found, port
11434 closed). The five RTX A6000s make a 20–30B open-weight model comfortable.
Enabling it is one of:

```bash
# user-local, no root:
curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz \
  | tar -xz -C "$HOME/.local"
OLLAMA_MODELS=$HOME/.ollama/models "$HOME/.local/bin/ollama" serve &
"$HOME/.local/bin/ollama" pull qwen3:32b        # or gpt-oss:20b, llama3.3:70b
```

Until then `llm_agent`/`llm_bo` are implemented, unit-tested against a **mock
Ollama server** (canned good / malformed / out-of-bounds / duplicate responses),
and reported as `unavailable` in the campaign summary. No fake LLM results are
ever produced.

---

## 8. Nearest-material projection (deliverable 2)

### 8.1 The metric

Projection is done in **physical space**, not in raw decision space, because two
materials with different densities can have identical phonon behaviour:

```
d(p*, m)^2 = sum_k  w_k * ( (g_k(m) - g_k(p*)) / s_k )^2
```

with `g` the comparison features, `s_k` the spread of that feature across the
pool (so the metric is scale-free), and `w_k` a declared weight:

| Layer | Features `g` | Source for a real material |
|---|---|---|
| Substrate | `v_L`, `v_T`, anisotropy `A = 2C44/(C11−C12)`, impedance `Z = ρ·(v_L+2v_T)/3`, and — where available — `scat`, `decay`, `decayTT` | (C11,C12,C44,ρ) from the MP cubic snapshot (396 accepted, 283 stable non-metals); phonon constants only from the 7 shipped G4CMP records |
| Top film | `vsound`, `gap`, `ph_lifetime`, `Z = ρ·v` | curated elemental/compound superconductor pool (Δ from measured `T_c` where established, BCS `1.764 k T_c` otherwise, flagged which) |
| Bottom film | `vsound`, `ph_lifetime`, `Z = ρ·v` | curated normal-metal pool |

Features that a real candidate has **no sourced value for** (`scat`, `decay`,
`decayTT` outside the 7 G4CMP records; film phonon lifetimes outside the four
curated ones) are **excluded from its distance and reported as an uncertainty
gap**, never imputed. A candidate is ranked with the number of matched features
printed next to it — `d = 0.31 (4/7 features matched)` is a different claim from
`d = 0.31 (7/7)` and the table says so.

### 8.2 Feasibility filter before distance

```
substrate:  cubic, Born-stable, non-metal, E_hull <= 0.05 eV/atom,
            not radioactive, not magnetic, representable in Geant4,
            and — for the verification run — has a G4CMP lattice record
            OR a complete literature record the user supplies
top film :  superconducting, T_c > 0.3 K, 2*Delta < E_gun, deposits as a film
bottom   :  normal metal at mK, standard fabrication metal
```

### 8.3 Verification — the step that turns a distance into a result

A distance is a claim about a metric, not about QPs. So every finalist is
**re-simulated with its own measured properties** and compared to the ideal:

```
for m in top_5_projections:
    real = resolve_real_material(m)          # its own C, rho, gap, lifetime, v
    r    = evaluate(contract, real, fidelity=M or L, seed_bank=HELD_OUT)
    report(ideal=J(p*), projected=J(m), baseline=J(Si/Nb/Cu),
           realized_fraction=(J(base)-J(m))/(J(base)-J(p*)))
```

`realized_fraction` is the honest headline: *how much of the ideal improvement
survives the projection onto something that exists.* If it is small, the
finding is "the optimum is not reachable with catalogued materials", which is
itself a useful fabrication result and must be reported as such.

### 8.4 Sensitivity of the optimum (fabrication tolerance)

Around `p*`, a one-factor-at-a-time ± scan (the cheapest defensible tolerance
analysis) at tier S gives `dJ/dp_k` and the tolerance band within which the
objective degrades by less than the tier's 5% noise. This tells the fabricator
which specifications actually matter — and it is also how the projection weights
`w_k` are set: **weight ∝ local sensitivity**, so the metric cares about the
properties the objective cares about, rather than weighting all seven equally.

---

## 9. Files

**New**

```
STAGE4_PROPERTY_OPTIMIZATION_PLAN.md   this document
stage4_config.yaml                     campaign contract: space, objective,
                                       optimizer, budget, fidelity, LLM settings
stage4_space.py                        variables, transforms, gates, resolver,
                                       pseudo-material config/macro overrides
stage4_objectives.py                   objective registry (§4)
stage4_optimizers.py                   random / sobol / bo_gp / cmaes (+ adapters)
stage4_llm.py                          Ollama client, prompt, parse, guard rails
stage4_optimize.py                     campaign driver: parallel ask/tell,
                                       ledger, restart, stopping rules
stage4_project_material.py             projection + verification (§8)
stage4_material_pool.yaml              curated film pools, per-field provenance
stage4_probe_g4_density.py             measure a NIST material's Geant4 density
stage4_report.py                       tables, convergence curves, projection
tests_stage4.py                        the exit-gate test suite (§10)
```

**Modified**

```
stage3_contract.py        continuous `space` decisions; complete code identity
stage3_ledger.py          sub-run QP columns; optimizer/iteration/objective cols
stage3_trial_runner.py    resolver hook, config/macro overrides, per-sub-run
                          scoring, macro re-parse gate (G9)
```

**Removed** (superseded, with the reason recorded here rather than in a commit
message that nobody reads):

```
STAGE3_START_ROADMAP.md               its own header declares it superseded by
                                      STAGE3_SMALL_MATERIAL_START.md for
                                      execution order; its architecture content
                                      is duplicated in the pipeline document
small_material_candidates.example.yaml  a placeholder template for a catalog
                                      that now exists as material_catalog.yaml
                                      with real provenance
```

**Kept as the historical record** (v2 is the comparison baseline for v3 and its
audit trail is load-bearing): `STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md`,
`STAGE3_GAP_REMEDIATION.md`, `STAGE3_SMALL_MATERIAL_START.md`,
`STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md`, the three ledgers, `runs/`, `results/`.
A new `parameter_optimization/README.md` indexes which document is current and
which is history.

---

## 10. Test plan — the exit gates

Nothing expensive runs until these pass (`python tests_stage4.py`):

| # | Gate | Test |
|---|---|---|
| T1 | Space round-trip | `to_unit(from_unit(x)) == x` for 10⁴ random points, log dims included |
| T2 | Gates reject | Born-unstable, inverted-speed, out-of-[0,1]-absorption and 2Δ>E_gun candidates are all rejected **before** any process starts |
| T3 | Baseline reconstruction | the property vector holding the Si/Nb/Cu values resolves to `setTopAbs 0.795`, `setTopFilmAbs 0.745`, `setBotAbs 0.736`, `vsound 9016.7`, `vtrans 5369.5` — the v2 numbers, exactly |
| T4 | v2 path unchanged | `resolve_candidate` for `Ge/Nb/Cu` returns byte-identical `derived` to the recorded ledger row |
| T5 | Scoring equivalence | per-sub-run scores sum to the pooled score on an existing 32-file trial |
| T6 | Determinism | same candidate + same seed bank twice → agreement **within the measured stochastic noise**, and an identical cache key. Bit-identity was the original gate and it **fails on this executable** — see §13 |
| T7 | Failure is not zero | injected corrupt / empty / missing / exit-0-without-marker sub-runs each produce their own status and **no** observation |
| T8 | Optimizer sanity | every optimizer beats random search on noisy Branin/Hartmann-6 with matched budget and matched noise |
| T9 | GP correctness | posterior mean/variance match a brute-force Cholesky solve to 1e-10; log-EI matches numerical quadrature |
| T10 | LLM guard rails | mock server returning malformed JSON / out-of-bounds / duplicates / an unreachable port each degrade correctly and never inject an invalid candidate |
| T11 | Projection sanity | projecting the *baseline* property vector returns Si, Nb, Cu as the nearest members of their pools |
| T12 | Restart | killing the driver mid-campaign and restarting resumes without duplicating or losing an observation |
| T13 | Inertness A/B | `cubic a` and `/g4cmp/temperature` A/B trials — bit-identical or not, recorded either way |

---

## 11. Compute budget and schedule

Measured cost per candidate at tier S is ~41 s on 32 idle workers.
**mimir is currently ~95% occupied by another user** (load 200 on 192 cores,
~183 cores in use), so Stage 4 budgets against 16–32 cores, not 192, and
measures actual throughput in the pilot before sizing the campaign.

| Phase | Content | Trials | Tier | Cores | Est. wall (contended) |
|---|---|---:|---|---:|---|
| P0 | Exit-gate tests, no Geant4 | — | — | 1 | minutes |
| P1 | Pilot: baseline replay, determinism, noise across 3 seed banks, `a`/temperature A/B | ~10 | S | 16 | ~30 min |
| P2 | Sobol initial design (shared by all optimizers) | 64 | S | 32 | ~1.5 h |
| P3 | `bo_gp`, `cmaes`, `random` continuation, `llm_bo` if Ollama is up | 4×80 | S | 32 | ~6 h |
| P4 | Promote top 10 (+ v2 winner `Ge/Nb/Cu`, + baseline) to M, held-out seeds | 12 | M | 32 | ~1.5 h |
| P5 | Projection + verification of top 5 real triplets | 5–8 | M then L | 32 | ~2 h + overnight |
| P6 | Report, tolerance scan, plots | ~34 | S | 32 | ~1 h |

Total ≈ 500 trials ≈ 2.2e9 primary events. Everything is restartable, ledgered
and cache-keyed, so the campaign can be stopped and resumed around the other
user's load.

Stopping rules (whichever fires first): the event budget is exhausted; no
improvement beyond the tier's 5% noise for 15 consecutive completed batches; or
the log-EI of the best proposal falls below 1e-4 of the incumbent's objective.

---

## 12. Claim scope and known limitations

Carried forward from Stage 3, all still true and all reported with the result:

1. **The interface model is calibrated, not validated**
   (`physics_validation_passed = False`). It reproduces 0.795 / 0.745 / 0.736 by
   construction. Any ranking that turns on interface absorption — which, in a
   property-space scan, is most of the film effect — is model-dependent.
2. **The 16-site quadrature is not converged.** One site carried 49.6% of the
   baseline objective. Absolute yields are therefore quadrature-limited even
   though paired differences are not.
3. **`dyn`, the DOS fractions, `Debye` and the lattice constant are Si's** in
   every pseudo-material. The first two are unmeasured for a pseudo-crystal; the
   last two are proven inert for this gun type.
4. **A pseudo-material need not exist**, which is the point of the stage — and
   is precisely why §8 exists and why the headline number is
   `realized_fraction`, not the pseudo-material's objective.
5. **Total QPs is not a logical error rate.** No PLE claim, in either direction.
6. `total_QPs` counts junction QPs only (`setHitType Junction`); the ground
   plane competes for phonons but is never scored.

---

## 13. Implementation record (2026-08-21)

Everything in §1–§12 above was written before implementation. This section
records what was actually built, what the exit gates measured, and the three
places where measurement contradicted the plan. Read it as the correction sheet.

### 13.1 Built

| File | Lines | What it does |
|---|---:|---|
| `stage4_space.py` | ~560 | 16 continuous variables + 13 Miller directions, transforms, gates G1–G7, the pseudo-material resolver, the projection feature map |
| `stage4_objectives.py` | ~300 | 6 objectives on the (position, replica) blocks, replica-based SE, site bootstrap, paired differences |
| `stage4_optimizers.py` | ~700 | `random`, `sobol`, `bo_gp` (self-contained GP + EI), `cmaes` (with IPOP restarts), optional Optuna adapter |
| `stage4_llm.py` | ~430 | Ollama client, prompt, parser, guard rails, `llm_agent` and `llm_bo`, mock client for tests |
| `stage4_optimize.py` | ~430 | campaign driver: parallel dispatch, ledger, resume, controls, stopping rules, manifest |
| `stage4_project_material.py` | ~480 | projection metric, pools, shortlist, verification re-simulation |
| `stage4_tolerance.py` | ~230 | OFAT tolerance scan and the projection weights it produces |
| `stage4_pilot.py` | ~180 | determinism, noise, throughput, the two inertness A/Bs |
| `stage4_report.py` | ~260 | cross-optimizer comparison, best-so-far vs cost, promotion list |
| `stage4_probe_g4_density.py` | ~120 | measures a NIST material's Geant4 density from a live run |
| `tests_stage4.py` | ~520 | the exit gates; **39/39 pass** |
| `stage4_config.yaml`, `stage4_material_pool.yaml` | — | contract and projection pools |

Changed, minimally, in the shared machinery: `stage3_contract.py` (complete code
identity, whole-contract hash, continuous decisions), `stage3_ledger.py`
(per-sub-run QP columns and optimizer bookkeeping, migrated in place),
`stage3_trial_runner.py` (resolver hook, config/macro overrides, per-sub-run
scoring, the unit-hygiene gate, and a `--force` bug fixed — see §13.4).

### 13.2 What the gates measured

* **The baseline pseudo-material reproduces the v2 baseline exactly.** The
  property vector holding the Si/Nb/Cu values resolves to
  `0.795 / 0.745 / 0.736` and `9016.7 / 5369.5 m/s`, and — the stronger check —
  simulating it at 4e6 events returned **1578 QPs**, the identical total the v2
  catalog path recorded for `Si/Nb/Cu`. The pseudo-material machinery is
  therefore not a new physics path; it is the same one, differently addressed.
* **Per-sub-run scoring is exactly equivalent to pooling.** Rescoring a recorded
  v2 trial gives `blocks 1578.0 == pooled 1578.0 == ledger 1578.0`, and the 16
  site totals reproduce the audit's recorded `[32, 42, 58, 14, 94, 96, 16, 20,
  24, 16, 22, 782, 78, 122, 92, 70]`.
* **The noise model transfers.** Measured on the pilot: across-bank CV 4.6%,
  within-run replica SE 5.6%, against a Poisson 2.55% → **implied Fano 4.9**,
  matching the v2 scaling study's ~5 from a completely different estimator.
* **The GP is correct**: analytic gradients match finite differences to 1.6e-8,
  the posterior matches a brute-force Cholesky solve to 1e-13, and EI matches
  numerical quadrature to 1e-10.
* **Both model-based optimizers beat random**, on two synthetic surfaces chosen
  to stress different things (100 evaluations, 3 seeds, median of best):

  | surface | random | sobol | cmaes | bo_gp |
  |---|---:|---:|---:|---:|
  | interior optimum | 1.94e-1 | 1.57e-1 | **1.06e-1** | 1.35e-1 |
  | boundary optimum | 1.06e-2 | 1.13e-2 | 6.25e-3 | **9.3e-4** |

  This is why both ship. CMA-ES is stronger when the optimum is interior; the GP
  is an order of magnitude stronger when it sits on a wall — which is the more
  likely case here, since several of these variables plausibly optimize at a
  bound. The CMA-ES result on the boundary surface only holds because its box
  handling **clips** rather than rejects; with rejection it lost to random
  search, which is a real trap and is now a comment in the code.

### 13.3 Correction 1 — exact replay does not hold on this executable

The plan (and the Stage 3 record, §12.3) assumed `/random/setSeeds` makes a run
bit-reproducible. **It does not.** Measured:

* two forced re-runs of the identical property vector, identical seed bank,
  identical macros: **1578 vs 1590 QPs**, with 10 of the 32 sub-runs differing;
* the differing hit rows are *the same physics at shifted event IDs*
  (`0,29408,2,phononTF,0.00163974,…` vs `0,29414,2,phononTF,0.00163974,…`) —
  the signature of a stream phase shift, not of a different physical outcome;
* repeating **one** sub-run with the same seeds diverges in roughly **1 run in
  6** (5/6 identical, then 4/6 in a second series);
* it is **not** the seeds: the same macro run twice back to back was
  byte-identical in the majority of trials;
* it is **not** the pre-`beamOn` draw sequence: adding a second identical
  `/random/setSeeds` at the very top of the macro did not remove it;
* it is **not** address-space randomisation: `setarch --addr-no-randomize` did
  not remove it either.

Root cause is therefore inside the C++ (a rare uninitialized read, a
pointer-ordered container, or a timing-dependent path) and is out of scope for
this stage. What matters here:

1. **Magnitude.** 0.76% at the trial level, against a 5.9% stochastic error.
   It cannot change a ranking that the noise does not already threaten.
2. **The cache is still sound**, but its guarantee weakens from "the same
   result" to "a statistically equivalent result from identical inputs".
3. **Paired comparisons still work**, but the variance reduction from common
   random numbers is smaller than assumed — which the Stage 3 record already
   warned about for a different reason (candidates consume different numbers of
   draws and decorrelate).
4. **The exit gate changed** from bit-identity to agreement within the measured
   noise, with the divergence itself reported rather than hidden.
5. **It should be chased in the C++**, with a UB sanitizer or valgrind run on a
   single sub-run, before any claim that rests on exact reproducibility.

An unexpected corollary: the `sub_lattice_a` A/B came back **bit-identical
across all 32 sub-runs**, which under a uniform 1-in-6 divergence rate would be
a 0.3% coincidence. The divergence is therefore not uniform-random per run — it
correlates with something about how the batch runs (load, concurrency), and that
is a clue for whoever chases it.

### 13.4 Correction 2 — `--force` never recorded its re-run

`evaluate(..., force=True)` took the branch that mints a **new** `trial_id`
under the **same** cache key. `plan_trial` uses `INSERT OR IGNORE`, so the
`trials` row was silently dropped and `set_trial_result` updated nothing: the
re-run executed, produced files, consumed hours, and left no ledger row. The
code's own comment described this hazard three lines below the branch that
caused it. Fixed: a re-run, forced or not, reuses the existing `trial_id` and
`run_dir`.

### 13.5 Correction 3 — temperature is not resolved, and stays in the search

The plan proposed demoting `/g4cmp/temperature` to `fixed` if its A/B came back
inert. Measured: 1578 → 1498 QPs at 0.1 K, i.e. **−5.1% against a 5.9% replica
SE** — *not* bit-identical, and *not* significant. It is therefore neither
proven inert nor proven active, and it stays an active decision variable. The
inertness of `sub_lattice_a`, by contrast, is now measured (bit-identical) and
it stays out of the search.

### 13.6 Deployment note — the agentic optimizers

`llm_agent` and `llm_bo` are implemented and fully exercised against a mock
Ollama server (well-formed, out-of-bounds, gate-infeasible, malformed, chatty
and transport-error responses, plus an unreachable host — 7 gates, all passing).
They are **not** exercised against a real model, because Ollama is not installed
on this host. Enabling it is the three commands in §7.4; until then the campaign
report prints `LLM unavailable` and records how many proposals fell back, and no
LLM-derived result exists to be misread.

### 13.7 Campaign as executed, and two deviations from §11

The plan's phase table assumed an idle machine. mimir was ~95% occupied by
another user for the whole campaign, so the executed protocol was:

| Phase | Planned | Executed |
|---|---|---|
| P0 exit gates | minutes | 39/39 pass |
| P1 pilot | ~10 trials | 7 trials: determinism, 3 seed banks, both inertness A/Bs, throughput |
| P2+P3 search | 64 Sobol + 4×80 | `bo_gp` 23, `cmaes` 32, `random` 37 trials at 4e6 events, run concurrently on ~48 cores |
| P4 confirmation | top 10 at tier M | top 5 at tier S on **held-out seed bank 9** |
| P5 projection | top 5 triplets | full 378-material projection + 6 verification simulations |
| P6 tolerance | ~34 trials | 33-point one-factor scan around the optimum |

**Deviation 1 — asynchronous dispatch replaced synchronous batches.** The first
launch used `--batch 4` with a synchronous wait. Simulation cost varies by more
than 30× across this space, so a batch ran at the speed of its slowest member
and left most of the machine idle; the first batch took 45 minutes for 4 trials.
The driver was rewritten to keep `parallel` candidates in flight and refill a
slot the moment one finishes, with in-flight points fantasised into the GP so an
asynchronous campaign does not propose the same acquisition peak `q` times. The
campaigns were restarted and resumed their completed trials from the ledger.
Throughput roughly doubled.

**Deviation 2 — the failure region, which the plan did not anticipate.** Very
low absorption plus the 10 000-bounce limit makes part of the space
computationally intractable: 12 sub-runs hit the 1-hour watchdog, and `bo_gp`
spent its final hour with all four slots there. Rejections carry no objective
value, so the surrogate never learns to avoid it. **The tempting fix — a shorter
watchdog — is wrong**, because it would preferentially kill low-absorption
candidates, which is the direction the search is drawn to; the bias would be
invisible and in the flattering direction. The right fix is to model failure
(feasibility classifier or censored observations), and it is the first item of
the results document's next-steps list.

**One capability added mid-campaign:** five more Geant4 density carriers were
measured with `stage4_probe_g4_density.py` (`G4_CALCIUM_FLUORIDE` 3180,
`G4_LITHIUM_FLUORIDE` 2635, `G4_LITHIUM_HYDRIDE` 820, `G4_ALUMINUM_OXIDE` 3970,
`G4_BORON_CARBIDE` 2520 kg/m³). They extend the reachable density range
*downward*, which is what a stiff, light, fast substrate needs, and they are what
made the SiC and Be₂C elasticity variants of the projection verifiable at all.
They were added only after the campaigns stopped, because `stage4_space.py` is
in the cache identity and editing it mid-flight would have invalidated the
in-flight trials' keys.
