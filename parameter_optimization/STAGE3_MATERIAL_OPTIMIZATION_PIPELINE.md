# Stage 3 Material Optimization Pipeline

## 1. Goal and scope

Stage 3 will search for physically realizable material configurations that
reduce quasiparticle (QP) damage in the existing 17-electrode qubit geometry.
For the first implementation, the optimization target is the total number of
QPs generated. Probability of Logical Error (PLE) is a later objective and must
not be mixed into the first optimization until a quantum-error-code model and
its calibration data are fixed.

The existing pipeline remains useful:

1. `stage1_run_simulations.py` generates macros/lattice configurations and runs
   Geant4/G4CMP.
2. `stage2_compute_QPs.py` converts surface energy deposits to QP counts and
   writes `total_QPs` in `qp_summary.csv`.

`stage2_compute_QPs_sensitivity_analysis.py` remains the unchanged Stage 2b
Morris/correlation tool. Stage 3 does not call it and does not require any of
its outputs.

Stage 3 should be a new orchestration layer, not another Morris mode hidden in
Stage 1. A suggested entry point is `stage3_optimize_materials.py`, with a
single-trial evaluator shared by every optimization algorithm.

## 1.1 Decision: skip a new sensitivity campaign

A new Morris, Sobol, Pearson-correlation, or other global sensitivity campaign
is **not a prerequisite** for Stage 3. The logical data dependency is

```text
candidate -> macro/config -> Geant4/G4CMP hits -> Stage 2 total_QPs -> optimizer
```

It does not include `MorrisSequence.csv`, elementary effects, correlation
plots, or `stage2_compute_QPs_sensitivity_analysis.py`. The optimizer observes
the same objective it is minimizing and learns useful response structure from
its own initial and sequential trials. Existing sensitivity results may inform
scientific interpretation, but they are neither required nor ideal for
restricting a new linked-material search because they were produced under a
different parameterization and included non-material/QP-ODE variables.

Skipping sensitivity analysis is safe provided Stage 3 still performs:

- hard physics/feasibility checks before simulation;
- a balanced Sobol or categorical initial design covering the legal space;
- repeated-seed noise measurement;
- empirical validation that low event counts predict high-fidelity rankings;
- optimizer-independent random/Hyperband baselines;
- held-out high-fidelity confirmation of the finalists.

Sensitivity analysis remains optional after the campaign for explanation,
debugging, or fabrication-tolerance analysis. It must not block startup and
must use a new Stage 3-specific script if later requested; the previous Stage
2b script should stay untouched.

## 2. Exact objective

For trial configuration \(x\), replicate/seed \(r\), and a fixed number of
primary events \(N\), define

\[
Q(x,r,N)=\sum_{e=1}^{17}\sum_t QP_{e,t}.
\]

This is exactly the `total_QPs` field already produced by Stage 2. It uses the
existing rule that a valid top-surface deposit contributes
`round(E_deposited / setTopGap)` QPs to its nearest electrode.

Use two stored forms:

- Primary reported objective: raw `total_QPs` at the target event count.
- Cross-fidelity modeling response: \(q=Q/N\), QPs per primary event.

The optimizer should minimize a robust estimate over stochastic replicates,
not the smallest lucky realization:

\[
J(x)=\operatorname{mean}_r[q(x,r)]+\lambda\,
\operatorname{SE}_r[q(x,r)],
\]

with \(\lambda=1\) initially. Report the mean, median, standard error, confidence
interval, and raw replicate counts. Fit surrogates to `log1p(Q)` or an
appropriate count likelihood, but rank final candidates using untransformed QP
counts at the same target fidelity.

A missing, timed-out, truncated, or corrupt simulation is a failed observation,
never `Q=0`. The evaluator needs a positive completion marker so a legitimate
zero-QP simulation can be distinguished from an empty/incomplete hits file.

## 3. Parameter contract

### 3.1 Fixed controls

These values define a controlled comparison and must not become optimization
dimensions in Stage 3:

| Setting | Fixed value / rule |
|---|---|
| top junction QP limit | `setTopQPLim = 3` |
| top-film QP limit | `setTopFilmQPLim = 3` |
| bottom QP limit | `setBotQPLim = 3` |
| Al junction sound speed | `setTopVSound = 3.582` |
| Al junction gap | `setTopGap = 191e-6` |
| Al phonon lifetime / slope | `0.242 / 0.29` |
| specularity | top, top-film, bottom `0.8`; wall `0` |
| wall absorption | `0.02` |
| top / top-film / bottom thickness | `0.12 / 0.075 / 1 um` |
| top-film lifetime slope | `0.29` |
| bottom gap | `0` (a `[0,0]` interval is not a decision variable) |
| `minEPhonons` | **`0.0000382 eV`** (38.2 µeV) — see §3.1.1 and §12.5 |
| gun energy | **`10.0e-3 eV`** (10 meV — the muon-strike maximum), fixed for every candidate — see §3.1.1 |
| charge / phonon bounces | `1 / 10000` |
| clearance | `1e-6 mm` |
| electron / hole trapping MFP | `0.3 / 0.3 mm` |
| third-order stiffness `dyn` | `[-42.9,-94.5,52.4,68.0] GPa` |
| 17 electrode coordinates | exactly the X/Y lists in `parameter_set.txt` |
| geometry and injection conditions | frozen for every candidate in a comparison |
| QP ODE parameters | frozen; they do not affect the total-QP objective |

### 3.1.1 Excitation-threshold contract

The gun energy must be fixed, not optimized as a material property. "At or above
`minEPhonons`" is **not** a sufficient condition and must not be used as a gate:
`minEPhonons` is a numerical tracking cut, not a physical threshold. Since it
dropped to 38.2 µeV (§12.5) that test is 10× weaker than it was — 50, 100 or
300 µeV all pass it and every one of them produces **zero QPs forever**, because
no phonon can break a Cooper pair at the junction below `2 × setTopGap`.

The gate is the following chain, every link of which must hold:

```text
minEPhonons  <  2*setTopGap   <=   E_gun          (hard gates)
   38.2 µeV       382 µeV        10000 µeV

E_gun vs 2*setTopFilmGap  ->  regime classification, NOT a gate
```

| Link | Value | Why it is required |
|---|---|---|
| `minEPhonons < 2*setTopGap` | 38.2 < 382 µeV | Otherwise a numerical cut preempts the physical one and truncates the downconversion cascade — the mechanism `scat`/`decay`/`decayTT`/the tensor act through. |
| `E_gun >= 2*setTopGap` | 1000 >= 382 µeV | `JunctionKaplanElectrode::IsNearElectrode` requires `PhEnergy >= 2*GapJunc`. Below it the objective is identically zero regardless of material. |
| `E_gun` strictly above, not equal | 2.62× margin | At exact equality the result depends on `>=` vs `>` in one C++ comparison and on float representation. Do not sit on the gate. |
| ~~`E_gun < 2*setTopFilmGap`~~ | **WITHDRAWN** | This was wrong. `PhononSensitivity::IsHit` with `setHitType Junction` requires `GetJunctionHit()`, so a ground-film absorption is never *recorded*. Measured at 10 meV (3.25× the Nb gap): 68/68 recorded hits inside junction footprints, 0 outside. The objective is junction-only at any energy. What the film gap controls is the **regime** — above it the ground plane competes for phonons — which must be constant across a comparison set and is recorded as `ground_plane_active_absorber`. |
| transport regime | note, not a limit | Isotope MFP scales as ω⁻⁴: 137 µm at 1.5 meV, **~0.07 µm at 10 meV**. At 10 meV transport is strongly diffusive with rapid downconversion near the injection site — the correct picture for a muon strike, and measured to give ~10× the QP yield per event. |

If a future campaign injects a **spectrum** rather than a monoenergetic primary,
the contract becomes: declare the excitation spectrum and the fraction of its
weight lying in `[2*setTopGap, 2*setTopFilmGap)`. A spectrum whose mass sits
mostly below the junction gate is a physics-free design however high its mean.

All four thresholds move if `setTopGap` or `setTopFilmGap` is unfrozen.
Recompute the chain, do not carry the numbers over.

This chain is enforced at run time by `assert_excitation_thresholds()` in
`stage1_run_simulations.py`, so a violating configuration fails before launch
rather than returning a confident zero.

### 3.2 Material decisions

The listed tunable properties are:

- top-film `VSound`, gap, and phonon lifetime;
- bottom-film `VSound`, gap threshold, and phonon lifetime;
- temperature in `[0, 0.1] K`;
- cubic lattice constant and `C11`, `C12`, `C44`;
- phonon scattering, decay, and `decayTT`;
- lattice orientation.

There are two possible interpretations, and they must not be conflated:

1. **Real-material search (recommended):** substrate/top-film/bottom-film IDs
   are categorical decisions. Their property values travel as a linked bundle
   from a versioned material catalog. The optimizer must not combine the gap of
   one material with the density or stiffness of another and call the result a
   material.
2. **Property-space design:** continuous properties can be optimized inside a
   physically constrained region to learn an ideal target specification. The
   output is a property target, not a claim that a real material exists. A
   second nearest-candidate/projection step is required.

Run the real-material search first. Use property-space optimization only as an
interpretable follow-up or when fabrication can independently tune those
properties.

### 3.3 Dependent values

These are outputs of a material record, never independent decision variables:

- substrate `vsound` and `vtrans`, calculated from the elastic tensor and mass
  density using the Christoffel/Fibonacci-sphere calculation in
  `SoundfromTensor.py`;
- `setTopAbs`, `setTopFilmAbs`, and `setBotAbs`, calculated from the candidate
  density/phonon velocities and the fixed interface material using the promised
  transmission-coefficient script;
- Geant4 substrate material/density;
- any Debye check used to establish that the low-energy approximation remains
  valid.

Hard gates before a candidate can run:

- density is finite and positive;
- elastic tensor is symmetric and in the correct orientation/units;
- cubic stability: `C11-C12 > 0`, `C11+2*C12 > 0`, and `C44 > 0`;
- all Christoffel eigenvalues are positive;
- `0 < vtrans < vsound`;
- all interface transmission probabilities are in `[0,1]`;
- Debye energy/temperature passes the agreed physics threshold;
- the material can actually be represented in Geant4/G4CMP.

The current Stage 1 still instantiates `G4_Si` and uses `rho=2329 kg/m^3` for
derived sound speeds. This is valid only for the current Si sensitivity run.
Changing stiffness/config values while leaving the Geant4 mass density as Si
does **not** simulate a new substrate material. Candidate-specific Geant4
material creation and density propagation are therefore mandatory before
claiming a non-Si optimization result.

The interface absorption calculator is not currently in this repository. Until
it is supplied and validated against the baseline values `0.795`, `0.745`, and
`0.736`, those values can support only a provisional sensitivity study, not a
physically complete cross-material ranking.

## 4. Material catalog

Create a versioned, immutable candidate table before optimization. One row per
candidate should contain at least:

- material ID, formula, source database/version, retrieval timestamp;
- crystal system and conventional-cell definition;
- density, lattice constant, full 6x6 tensor, and tensor convention;
- `C11`, `C12`, `C44` after symmetry validation;
- band gap/metallicity, magnetic/radioactive flags, stability and energy above
  hull;
- Debye information when available;
- top/bottom-film gap, sound speed, phonon lifetime, and provenance;
- missing-data flags and uncertainty intervals.

`ElasticityTensors.py` was the useful start. It has since been **replaced by
`build_material_catalog.py`** and removed (2026-08-21); the list below is the
change set that replacement had to implement, kept because it documents why the
builder looks the way it does:

1. write density and all query filters into its CSV/JSON outputs;
2. distinguish the explicit `MATERIAL_IDS` mode from a database-wide filtered
   query;
3. store API/database version, retrieval date, tensor convention, and units;
4. validate cubic symmetry and mechanical stability;
5. freeze the returned candidate snapshot so an optimization can be reproduced;
6. never silently impute a missing property.

Missing film properties should either exclude a candidate or create an explicit
uncertain parameter with a justified prior. Do not use arbitrary `0.5x–1.5x`
bounds as material data.

## 5. Orientation

Treat orientation as a finite, reproducible categorical design:

1. generate directions with a Fibonacci sphere;
2. remove antipodal duplicates when the physics is invariant under `n -> -n`;
3. reduce by cubic crystal symmetry;
4. map each retained direction to the exact representation accepted by
   `setMiller`/`setLatticeDeg`;
5. store both the normalized direction and written macro values in the ledger.

Start with 32–128 symmetry-reduced orientations. Refining the orientation grid
is another fidelity level. Do not optimize three unconstrained Cartesian
components independently.

## 6. Reproducibility and noise model

The present Geant4 executable seeds CLHEP from `clock()`, so the simulation is
not reproducible. Fix this before Bayesian optimization:

1. add a run/macro seed accepted by `Main`;
2. derive it deterministically from `(campaign_id, scenario_id, replicate_id)`;
3. use common random numbers: compare competing candidates with the same seed
   set at a given fidelity;
4. keep different confirmation seeds completely held out from optimization.

QP observations are sparse counts and can be overdispersed. At minimum, retain
replicates and pass empirical observation variance to the surrogate. Better
options are a log-normal approximation to `log1p(Q/N)`, a Poisson model when
variance is near the mean, or a negative-binomial/count surrogate when it is
overdispersed. Zero-inflation should be assessed rather than discarded.

## 7. Trial evaluator and ledger

Implement one deterministic interface:

```text
evaluate(configuration, fidelity, scenario, seed) -> TrialResult
```

The evaluator should:

1. validate the candidate and all hard constraints;
2. resolve linked properties and compute dependent values;
3. create one macro/config in a unique trial directory;
4. run Geant4/G4CMP with a process-group timeout;
5. distinguish success, valid-zero, timeout, physics rejection, teardown
   SIGSEGV, and corrupt output;
6. run the QP calculation and return raw/per-electrode totals;
7. append an atomic result to a SQLite database or JSONL ledger;
8. cache by a canonical hash of code, templates, complete configuration,
   fidelity, scenario, and seed.

Minimum ledger fields:

```text
campaign_id, trial_id, candidate IDs, independent variables,
all derived values, orientation, scenario, seed, beamOn,
macro/config/code hashes, status, return code, runtime, peak RSS,
hits path/hash, total_QPs, QPs_per_primary, per-electrode QPs,
failure reason, optimizer iteration, acquisition value
```

Do not let the optimizer infer quality from a process return code. The known
exit-time code 139 can occur after physics output is complete; accept it only
when an explicit completion marker and hits-file integrity checks pass.

## 8. Fidelity ladder

Use event count as the main fidelity, but calibrate the ladder empirically:

| Level | Initial suggestion | Purpose |
|---|---:|---|
| F0 | `1e4` | plumbing only; often too sparse for ranking |
| F1 | `1e5` | broad screening |
| F2 | `1e6` | reliable optimization/selection |
| F3 | `1e8` only if justified | final rare-event confirmation |

Run the Si baseline and a few deliberately different candidates at every level
with at least 5 seeds. Measure rank correlation and bias between fidelities. If
F0 does not predict F2 rankings, remove it from optimization rather than saving
compute with misleading data. Promote candidates based on confidence intervals,
not a single low count.

Also define a scenario set for injection location/energy/direction. Optimize
either the expected response over a documented scenario distribution or a
robust response such as the 90th percentile. Optimizing one favorable injection
point can produce a material that performs poorly elsewhere.

## 9. Algorithm choices

### Recommended default: constrained discrete racing, then model-based search

1. Enumerate every feasible material tuple at F0/F1 when the catalog is small.
2. Use Successive Halving/Hyperband with repeated seeds to discard clearly bad
   candidates and promote uncertain/promising ones.
3. On the surviving mixed search space, use SMAC's random-forest surrogate and
   multi-fidelity facade. Random forests handle categorical, conditional, and
   non-smooth choices more naturally than a standard continuous GP.
4. Confirm the top 3–10 configurations at F2/F3 with held-out seeds.

Current SMAC documentation explicitly supports categorical configuration
spaces and multi-fidelity Successive Halving/Hyperband through its
`MultiFidelityFacade`: [SMAC configuration and facade overview](https://automl.github.io/SMAC3/latest/3_getting_started/),
[SMAC multi-fidelity guide](https://automl.github.io/SMAC3/latest/advanced_usage/2_multi_fidelity/).

### Bayesian alternative for a mostly continuous property-space study

Use Ax/BoTorch when the final space is low-dimensional and mostly continuous,
with categorical material identity handled as a finite candidate set or a
carefully designed mixed kernel:

- noisy batch optimization: `qLogNoisyExpectedImprovement`;
- parallel candidates per iteration matched to the worker count (64 by
  default, not the legacy 4 — see §12.4);
- explicit feasibility constraints or rejection before simulation;
- observation noise from replicates;
- cost-aware multi-fidelity Knowledge Gradient when event count is a useful,
  correlated fidelity.

BoTorch documents continuous multi-fidelity KG and cost-aware utilities here:
[multi-fidelity KG](https://botorch.org/docs/v0.14.0/tutorials/multi_fidelity_bo),
and its current tutorial index recommends Ax for experiment orchestration:
[BoTorch tutorials](https://botorch.org/docs/tutorials).

### Lightweight baseline

Optuna TPE is a reasonable engineering baseline for mixed categorical and
conditional spaces. It is simpler to deploy and should be benchmarked against
random search, but it is not the first choice for a carefully calibrated
multi-fidelity scientific campaign. Never report a Bayesian method without
also comparing best-found-objective versus cumulative simulation cost against:

- random material enumeration/search;
- Sobol initial design;
- Hyperband without a surrogate.

### Methods not recommended as the only optimizer

- A vanilla GP over one-hot material IDs: distances between categories have no
  physical meaning and performance degrades as the catalog grows.
- Gradient optimization: Geant4/G4CMP is stochastic and non-differentiable.
- A genetic algorithm at full fidelity: robust but usually too evaluation
  hungry; retain it only as a benchmark if the catalog is huge.
- Independent continuous optimization of every material property: it can
  create impossible pseudo-materials.

When PLE becomes available as a second objective, keep total QPs and PLE
separate and use constrained or multi-objective optimization. Do not hide their
tradeoff in an arbitrary weighted sum. A noisy hypervolume method such as
qNEHVI/MF-HVKG is then appropriate after the PLE model is validated.

## 10. Execution phases

### Phase A — physics/data readiness

- Freeze the current templates, fixed values, 17 coordinates, software
  versions, and code hashes.
- Supply and unit-test the interface transmission calculator.
- Add candidate density/material propagation to Geant4.
- Add deterministic CLHEP seeding and completion markers.
- Build and freeze the material catalog.
- Establish all feasibility and Debye gates.

Exit criterion: the Si catalog row reproduces the current Si macro/config,
derived speeds (approximately `9018.61/5370.66 m/s` for the current tensor),
and baseline interface coefficients within agreed tolerances.

### Phase B — evaluator validation

- Run one candidate twice with the same seed and require byte/numeric identity.
- Run different seeds to estimate variance.
- Inject controlled failures and verify they are never recorded as zero QPs.
- Check total QPs equals the sum over all 17 electrodes and time bins.
- Check raw totals scale consistently with event count.

Exit criterion: deterministic replay, correct status classification, and an
atomic restartable ledger.

### Phase C — pilot and fidelity calibration

- Evaluate Si plus 5–10 diverse feasible candidates at F0/F1/F2.
- Use at least 5 common seeds per candidate.
- Estimate fidelity rank correlation, count dispersion, runtime, and timeout.
- Choose the lowest useful fidelity and number of replicates.

Exit criterion: a written budget and evidence that promoted low-fidelity
candidates predict high-fidelity performance.

### Phase D — optimization campaign

- Seed with complete low-fidelity enumeration when affordable; otherwise use a
  balanced categorical/Sobol design.
- Run asynchronous batches no larger than the safe worker count (64 by
  default; see §12.4).
- Refit the surrogate only from valid observations with recorded noise.
- Allocate more seeds/fidelity near close incumbent comparisons.
- Periodically insert Si controls to detect software or machine drift.

Suggested stopping rules:

- fixed total event/CPU budget is exhausted;
- no practically meaningful improvement for 10–20 completed batches;
- acquisition value is below a predeclared threshold;
- top-candidate ranking is stable under held-out-seed confidence intervals.

### Phase E — confirmation and reporting

- Re-evaluate the top 3–10 candidates at target fidelity with at least 10 new
  held-out seeds and the full scenario set.
- Compare paired common-seed differences against Si with bootstrap confidence
  intervals.
- Report failures and feasibility rejections, not only successful trials.
- Archive catalog, ledger, macros/configs, hashes, optimizer state, and plots.
- Optionally perform a post-hoc ablation/importance analysis on feasible linked
  properties for interpretation; this is not part of candidate selection and
  is not required to finish Stage 3.

The winning claim should be phrased as: “candidate X reduces expected total QPs
relative to the frozen Si baseline under scenarios S, event count N, and this
simulation model,” with uncertainty. It should not claim improved logical error
until the separate PLE stage exists.

## 11. Immediate implementation order

1. Obtain the missing interface transmission script and validate the three Si
   baseline coefficients.
2. Modify the material data exporter to include density/provenance and freeze a
   first candidate catalog.
3. Add candidate-specific Geant4 material density and deterministic RNG seeds.
4. Extract a single-trial Stage 1 evaluator without changing the Morris runner.
5. Add completion/status metadata and an append-only trial ledger.
6. Run the fidelity/noise pilot.
7. Implement SMAC MultiFidelityFacade plus random/Hyperband baselines.
8. Add Ax/BoTorch only if the pilot shows a sufficiently smooth, correlated
   continuous/fidelity response that justifies the extra model complexity.

No expensive optimization campaign should start before steps 1–6 pass. Without
candidate density, reproducible seeds, and interface transmission coefficients,
the optimizer could efficiently converge to a numerically attractive but
physically inconsistent pseudo-material.

## 12. Operational hazards inherited from the `Material_optimization` branch

Everything below was **measured on mimir at production scale** on the earlier
`Material_optimization` branch (`origin/Material_optimization`, 2026-07-28) and
is not hypothetical. Read this before writing the trial evaluator: three of
these items are architectural constraints on how a trial can be launched at
all, and two supersede numbers stated earlier in this document.

Sources: `G4CMP_crash_and_memory_analysis.md` Findings 4 and 5 on that branch,
its `README.md` "Known issues" and "Latest screening run" sections, and
`sensitivity_memguard.py`.

### 12.1 Two crash modes beyond the two already documented

**(a) SIGSEGV in `G4LatticeLogical::LookupKtoVg` when `vtrans > vsound` — the
dangerous one.** Distinct from the known harmless exit-time SIGSEGV: this one
strikes **mid-run** and leaves a **zero-byte hits file**, so the trial yields no
data at all. G4CMP builds its phonon group-velocity map assuming
`v_L > v_T`; an inverted "crystal" indexes off the end of that table.

- Perfect separation on the aborted screen: **31/31** affected design points
  crashed, **0/30** unaffected ones did (Fisher exact **p = 4.3e-18**).
- Stochastic *per event* (`p ≈ 6e-5`) but deterministic *per design point*:
  2/8 crashed at 5k events, 3/8 at 20k, **8/8 at 80k**. At production event
  counts every sub-run of an affected point dies.
- It affected **17.3%** of that design (1197/6912 points).
- Fixed there by sweeping `vtrans` as the **ratio** `v_T/v_L ∈ [0.3, 0.9]` and
  reconstructing the absolute value at config-write time, making the invalid
  region unreachable by construction rather than filtered afterwards.

> **Implication for Stage 3 — verified, and reassuring.** Stage 3 does *not*
> sample `vsound`/`vtrans`; it derives both from the candidate's elastic tensor
> and density via the Christoffel/Fibonacci calculation (§3.3). That derivation
> **cannot** invert them: scanned over the full Born-stable region of the
> current `C11/C12/C44` box, **0 of 2980** sampled tensors produced
> `⟨v_T⟩ >= ⟨v_L⟩`, and neither did any of 11 real cubic materials (Si, Ge,
> GaAs, diamond, MgO, NaCl, Cu, W, CaF₂, YAG, c-Si₃N₄) nor deliberately
> pathological tensors with `C44` up to 4×`C11`. The polarization-based mode
> assignment protects the ordering.
>
> So the rule to carry forward is simply: **never re-expose `vsound` or
> `vtrans` as independent decision variables.** Keeping them derived is what
> makes this crash structurally unreachable. Add a cheap belt-and-braces assert
> `0 < vtrans < vsound` in the resolver before writing `config.txt` anyway
> (it is already listed as a hard gate in §3.3) — and note this is an
> **acoustic-mode/G4CMP requirement, not a Born criterion**: the cubic Born
> conditions `C11>|C12|`, `C11+2*C12>0`, `C44>0` do not imply `C11 > C44`.

**(b) A macro that aborts still exits 0.** A single malformed command — e.g. a
bad unit suffix such as `/g4cmp/clearance 1e-06e-6 mm`, produced when a default
is expressed in different units from its bounds — makes Geant4 emit a
G4Exception **warning**, skip `/run/beamOn` entirely, and **exit 0**. Observed
once at scale: **3,200 "successful" sub-runs that produced no hits file at
all.** Downstream this looks like uniformly zero signal, which is very hard to
distinguish from a weak physical response.

> **Implication for Stage 3.** This is precisely the "failed observation must
> never become `Q = 0`" hazard of §2, with a concrete mechanism. The evaluator
> must treat **"exit 0 but no hits file"** as a hard failure status
> (`MacroAborted`), not a zero. The hits file is created at `/run/beamOn` — its
> header is written even for a run that records no hits — so its *absence* is
> reliable proof the event loop never executed. This check is stronger than the
> completion marker proposed in §7 and should be implemented alongside it.
> Validate every generated macro's unit suffixes before launch.

### 12.2 Architectural constraints on how a trial may be launched

Two measured Geant4/G4CMP behaviours constrain the evaluator's design:

- **`/g4cmp/HitsFile` cannot be re-pointed after `/run/initialize`.**
- **A second `/run/beamOn` truncates the hits file rather than appending.**

> Therefore **one source position = one Geant4 process**, and one replicate =
> one process. A design point is `N_positions × N_replicas` separate processes,
> not one run. The §8 scenario set is not a loop inside a macro; it is a fan-out
> of processes whose per-position hits files the evaluator must collect and sum.
> Budget and ledger schema must account for this from the start.

### 12.3 Deterministic seeding is confirmed working

`/random/setSeeds` in the macro **does** override `Main.cc`'s
`setTheSeed((unsigned)clock())`, because that call happens before the UI manager
executes the macro. The branch shipped this as `SENSITIVITY_EXPLICIT_SEEDS=1`
with seeds keyed by `(trajectory, replica, position)` from
`SENSITIVITY_SEED_BASE`, recorded in the manifest.

> **Implication:** §6's "add a run/macro seed accepted by `Main`" needs **no C++
> change**. Reuse the branch's seed-key scheme; keep confirmation seeds held
> out.

### 12.4 Memory and concurrency — the 4-worker limit is obsolete

Measured behaviour, production screen of **221,184 sub-runs**:

| Quantity | Measured |
|---|---|
| Workers | **32** (not 4) |
| Wall time | 9h 43m for 221,184 sub-runs at 125k events each |
| Failures | **0**; zero-byte hits files **0** |
| Peak RSS | **2.15 GB** total across all concurrent sub-runs |
| Healthy single run | **~70 MB** RSS |
| Runaway (`QPLim=1` KaplanQP loop) | observed **28 GB → 79 GB** and still climbing |

> **Implication for §9 item 5.** "Dispatch at most four concurrent simulations,
> matching the current safe Mimir worker count" is a legacy constraint.
> **The runner now defaults to 64 workers with a 200 GB aggregate budget**
> (adopted 2026-08-13). 32 is the level validated end-to-end; 64 is double that
> and not yet measured at scale, so watch the "Peak tracked RSS" line on the
> first long run. At 64 workers the expected steady usage is ~4.5 GB
> (64 × 0.07 GB), i.e. ~45× headroom under the 200 GB ceiling, while leaving
> well over half of the 485 GB host to other users. Per-sub-run cost works out to roughly **5
> core-seconds per 125k events**, i.e. ~40 core-seconds per 1e6 events —
> cheaper than assumed. Reuse `sensitivity_memguard.py` rather than rewriting
> it: RSS-based (not `RLIMIT_AS`, which would kill healthy runs during Geant4's
> large speculative VA reservations at startup), with a per-sample cap
> (`SENSITIVITY_PER_SAMPLE_MEM_GB=4`, ~55× a healthy run) and an aggregate
> ceiling (`SENSITIVITY_TOTAL_MEM_GB=200`, refusing to start above host
> `MemAvailable`), killing the whole process group largest-offender-first.
> Note `MAX_WORKERS × PER_SAMPLE_MEM_GB` (64 × 4 = 256 GB) deliberately exceeds
> the aggregate ceiling: in a mass runaway the aggregate cap fires first, which
> is the wanted behaviour on a shared host. The per-sample cap exists to catch a
> *single* runaway quickly, not to bound the total.
> Killing only the `bash` wrapper leaves an orphaned `Main` at 100% CPU.

### 12.5 The energy protocol — supersedes the §8 fidelity ladder

The sparsity that motivates §8's ladder was **largely an artifact of the
gun-energy / `minEPhonons` configuration**, and it has already been fixed:

| Change | From | To | Why |
|---|---|---|---|
| `/main/gun/setEnergy` | [191, 573] µeV | **[0.6, 1.5] meV** | Two of four Morris levels sat below the 2Δ pair-breaking gate (~47% of the design simulated nothing). Floor = 2Δ_Al at the *top* of the `setTopGap` sweep; ceiling set by phonon transport — isotope MFP falls to 137 µm at 1.5 meV against a 525 µm substrate. |
| `/g4cmp/minEPhonons` | 382 µeV | **38.2 µeV** | It sat *above* the lowest physical threshold in the design (2Δ_Al = 191 µeV), so a numerical cut was preempting a physical one and truncating the downconversion cascade. |

Effect at 4e6 events per design point (16 Sobol positions × 2 replicas × 125k):

| | Before | After |
|---|---|---|
| λ (`total_QPs`) | 0.93 | **147.4** |
| Relative SD per evaluation | 104% | **12.4%** |
| Design points with zero signal | 78% | **0%** (noise floor); 0.12% in the screen |
| Smallest resolvable effect | ~440% | **53%** (single comparison) |

**Read that table carefully — it is not a like-for-like comparison.** The
"before" λ = 0.93 was measured at **1e5** events; the "after" λ = 147.4 at
**4e6**. Normalising, the yield went from `9.3e-6` to `3.69e-5` QPs per primary
event, so the *energy protocol itself* is worth about **4×**, and the remaining
~40× of the λ improvement is simply the 40× larger event budget. Most of the
relative-SD gain (104% → 12.4%) is likewise the event count: for a Poisson
count, `SD/mean = 1/sqrt(λ)`, i.e. 104% at λ=0.93 and 8.2% at λ=147.4, with the
residual gap to 12.4% being real overdispersion.

Similarly, the "78% zero signal" figure is dominated by a problem Stage 3 does
not have: `setEnergy` was *swept* across the 2Δ gate, so ~47% of design points
were structurally dead. Stage 3 **fixes** the gun energy, so that failure mode
disappears whichever value is chosen.

> **Implication.** Still adopt the corrected protocol — gun energy in
> [0.6, 1.5] meV and `minEPhonons = 38.2 µeV` — but for the **physics** reason,
> not the headline statistics. At `382 µeV` with a `382 µeV` cut, the primary
> sits exactly on the Al pair-breaking gate and every downconversion daughter
> is killed at the first decay, so the cascade is not simulated at all. That
> suppresses precisely the mechanism Stage 3 is optimising: `scat`, `decay`,
> `decayTT` and the elastic tensor act *through* the downconversion cascade and
> have little room to act without it. The statistical benefit is a real but
> modest ~4× reduction in events for equal precision; the physics benefit is
> that the tuned substrate parameters can actually influence the objective.
>
> Budget against the measured `3.69e-5` QPs/event under the new protocol, not
> against a "two decades cheaper" ladder.
>
> Caveat when the *film* materials change: the 0.6 meV floor is `2Δ_Al` at the
> top of the gap sweep. If a future campaign tunes `setTopGap`, recompute the
> floor — and `minEPhonons` with it, as `parameter_set.txt` already warns.

### 12.6 Scenario set: 16 Sobol positions, identical across candidates

A single fixed injection site measures one phonon caustic, not the device — Si
focusing is strongly anisotropic. The branch used **16 scrambled-Sobol source
positions × 2 replicas**, with positions **identical across all design points**
so they form a common spatial scenario set, and explicit seeds paired within a
trajectory and differing between replicas.

Note these are `/main/gun/setPosition` **injection** sites — where the phonon
burst originates — sampled over ±4 mm of the 10×10 mm substrate (1 mm clear of
the walls), with z held at the template value just inside the top surface. They
are unrelated to the **17 electrode locations**, which are the Al junctions
where QPs are counted and which are present in every run. 16 is a power of two
because a scrambled Sobol set only carries its balance guarantee at powers of
two.

**Measured spatial heterogeneity, same material, across the 16 sites:**

| Statistic | Value |
|---|---:|
| Lowest mean QPs at one site | 2.515 |
| Highest mean QPs at one site | 23.09 |
| Max/min ratio | **9.18×** |
| CV among the 16 site means | **60.6%** |

That 60.6% is the same order as the candidate effects Stage 3 is trying to
resolve (the screen's smallest resolvable effect was 53%; the leading μ\* was
~124% of the mean). So injection site is not a second-order nuisance — it is a
lever as strong as the material signal itself, and one the optimizer can pull.

The audit also records that **16 sites is not established as converged**. When
the position set is adopted, test it with nested 16/32/64-point Sobol sets (or
multiple independent scrambles) and check convergence of total QPs per event,
maximum-electrode QPs, the leading signed effects, and the spatial poisoning
footprint.

> **Implication for §8.** This is the scenario set §8 asks for, already built
> and already run. Recover it from the branch (`SENSITIVITY_N_POSITIONS`,
> `SENSITIVITY_N_REPLICAS`, `SENSITIVITY_TOTAL_EVENTS`, which derives per-sub-run
> `/run/beamOn` and requires exact division) rather than reimplementing it.
> Common positions across candidates are the spatial analogue of common random
> numbers and are what make paired candidate-minus-Si comparisons meaningful.

### 12.7 Silent degradation in the analysis layer — design the ledger against it

Three separate bugs on that branch shared one shape: **a check that validated an
artifact using a quantity derived from that same artifact.** Stage 2 wiped
`qps/` and `qp_summary.csv` *before* verifying it had readable inputs; Stage 2b
inferred each design point's expected replica set from `qp_summary.csv`, so a
truncated file silently averaged a point over one replica (carrying √2× its
neighbours' noise while looking identical); Stage 3 inferred the expected
replica count as the *median* of observed counts, which tracks the damage, and
counted rows rather than identities, so two copies of `r0` passed as `{r0, r1}`.

> **Implication for §7.** Judge completeness only against an artifact written
> **before** the work and degradable by nothing downstream — the manifest, or
> the Stage 3 ledger's planned-trial rows. Never infer the expected replicate or
> position count from the observed results. Compare **identities**, not counts.
> Hard-fail on a malformed record rather than skipping it. These failures do not
> announce themselves: they degrade an optimizer's noise estimates while every
> file still looks well-formed.

### 12.8 Still open on that branch

**7.8% of the design (538/6912) violated the cubic Born stability conditions**
and still ran — those points produce numbers but are not real crystals. The
current branch's Stage 1 already rejects whole Morris trajectories that enter
the unstable region (`cubic_stiffness_is_stable`), and §3.3 lists Born
stability as a hard gate, so Stage 3 inherits the fix. Keep the gate; do not
relax it to per-point filtering, which breaks trajectory structure.
