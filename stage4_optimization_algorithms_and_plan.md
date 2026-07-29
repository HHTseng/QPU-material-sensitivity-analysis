# Stage 4: Material-Optimization Algorithms and Implementation Plan

**Date:** 2026-07-28  
**Status:** implementation plan; no Stage-4 optimizer has been implemented yet

This document has three purposes:

1. re-audit the Stage-3 replica and Morris-trajectory fixes;
2. select optimization algorithms appropriate for long, noisy Geant4/G4CMP
   evaluations; and
3. define a small, algorithm-independent software structure for material
   optimization.

The plan is based on the current code and artifacts on the
`Material_optimization` branch, especially:

- `stage1_run_simulations.py`;
- `stage2_compute_QPs.py`;
- `stage3_screen_analysis.py`;
- `sensitivity_params.py`;
- `RESULTS_stage1_to_stage3.md`; and
- `AUDIT_RESULTS_stage1_to_stage3.md`, including Amendments 2a and 2b.

## Executive decision

The corrected Stage-3 result is valid for the stored complete run. The two
main defects identified previously are fixed on the production path:

- responses are joined to `MorrisSequence.csv` by parsed design-point label,
  not by row position; and
- malformed or incomplete Morris trajectories are rejected rather than
  silently producing unequal elementary-effect samples.

The stored run contains 6,912 complete design points, 128 valid trajectories,
two replicas per point, and 128 elementary effects for each of 53 parameters.
The reported result of 18 physical parameters above the empirical dummy
threshold is therefore unchanged by this audit.

There are nevertheless four fail-closed improvements to make before Stage 4:

1. make the manifest mandatory for production analysis;
2. compare replica **multiplicities**, not only sets;
3. reject any hole in the manifest's design-point index; and
4. verify that every expected source-position file contributed to every
   replica, not merely that a summary row with the right replica name exists.

For optimization, the recommended primary algorithm is **noisy batch Bayesian
optimization with Ax/BoTorch**, initially using a single QP objective and
`qLogNoisyExpectedImprovement` (`qLogNEI`). SMAC3 is the most useful
independent benchmark when the design space becomes strongly categorical,
conditional, or discontinuous. Multi-objective and multi-fidelity methods
should be added only after the single-objective data contract and fidelity
relationships have been validated.

An open-model LLM agent can be useful for orchestration, monitoring, log
triage, and reporting. It must not replace the numerical optimizer or bypass
the deterministic material, physics, and completeness validators.

---

## 1. Stage-3 re-audit

### 1.1 What is fixed correctly

| Previous issue | Current implementation | Verdict |
|---|---|---|
| A missing summary row shifted every later response onto the wrong Morris design row | `report_morris()` parses `Morris_i` and assigns the response at design index `i` | Fixed |
| A point with an incomplete replica set could be averaged silently | `load_expected_replicas()` obtains expected sample identities from `qp_manifest.jsonl`; a mismatched point removes its whole trajectory | Fixed for missing/wrong identities |
| Expected replica count was inferred from the median of damaged output | The manifest is the authority when it exists | Fixed on the production path |
| A step changing zero or two parameters was silently skipped | `morris_elementary_effects()` exits if a step changes anything other than one coordinate | Fixed |
| One parameter could receive fewer effects than another | `validate_effect_structure()` requires one effect per parameter per retained trajectory | Fixed |
| A truncated number of design rows could be analyzed | Stage 3 requires a whole number of \(K+1\)-point trajectories | Fixed |

Dropping an entire trajectory is the correct response to an incomplete
endpoint. An elementary effect is a difference between adjacent endpoints, so
one bad point contaminates up to two effects; keeping the rest of that
trajectory would also destroy the one-effect-per-parameter Morris structure.

### 1.2 Independent checks on the stored production artifact

The current artifact
`results/morris_mimir_c7a18a17-91ce-491b-8124-78a4ff16576a` was checked
directly, independently of the saved Stage-3 claims.

| Check | Measured result |
|---|---:|
| Manifest entries | 13,824 |
| Summary rows | 13,824 |
| Design points | 6,912 |
| Parameters | 53 |
| Trajectory length | 54 |
| Complete trajectories | 128 |
| Manifest design indices | exactly 0 through 6,911 |
| Expected replicas per design point | exactly 2 |
| Observed replicas per design point | exactly 2 |
| Duplicate manifest sample names | 0 |
| Duplicate summary sample names | 0 |
| Manifest/summary replica-identity mismatches | 0 |
| Morris steps changing other than one coordinate | 0 |
| Trajectories not changing every parameter once | 0 |
| Saved Stage-3 rows | 53 |
| Saved `n_effects` values | exactly 128 for every row |
| Significant physical parameters | 18 |

`python -m py_compile` also succeeds for the Stage-1, Stage-2, Stage-2b, and
Stage-3 scripts. The current shell does not contain NumPy, pandas, or SciPy, so
the full numerical Stage-3 program could not be rerun in this shell. The
artifact structure, replica identities, trajectory mathematics, saved effect
counts, and Python syntax were all checked without those packages.

### 1.3 Residual validation gaps

These gaps do **not** affect the stored complete artifact, but they matter for
future incomplete runs and especially for an autonomous optimization loop.

#### Gap A — no-manifest mode still falls back to the observed median

If `qp_manifest.jsonl` is absent, Stage 3 warns and returns to:

```python
int(aggregated["n_replicas"].median())
```

That is the exact quantity that Amendment 2b correctly identified as unsafe.
A warning is better than silent degradation, but a production analysis can
still finish and overwrite `stage3_morris_screen.csv` with unverifiable data.

**Required behavior for Stage 4:** fail if the manifest is absent. If legacy
analysis must remain possible, require an explicit
`--allow-unverified-legacy-input` flag and mark its output as unverified.

#### Gap B — set equality does not reject all duplicate rows

The current comparison converts observed names to a `frozenset`. It detects
two `r0` rows with `r1` absent, because `{r0} != {r0,r1}`. It does **not**
detect `{r0,r0,r1}`, because both sides collapse to `{r0,r1}`. The earlier
`aggregate_replicas()` call then weights `r0` twice and biases the response.

**Required behavior:** reject duplicate `sample_name` rows first, or compare
`collections.Counter` objects rather than sets. Each manifest sample identity
must occur exactly once in the summary.

#### Gap C — a design point absent from the manifest can pass

The current loop does:

```python
expected = expected_by_index.get(idx)
if expected is None:
    continue
```

If the summary contains `Morris_i` but the manifest has a hole at `i`, that
point can pass. This contradicts the intended rule that the pre-run manifest
is the authority.

**Required behavior:** for the analyzed prefix, require manifest keys to equal
`set(range(n_design))`. A missing expected entry is a fatal manifest error,
not permission to trust the downstream summary.

#### Gap D — replica identity does not prove position completeness

Stage 2 deliberately permits a replica to be computed from fewer than all
position files. It rescales `n_sim` and emits a summary row with the expected
sample name. Stage 3 checks that name, so a replica made from, for example,
15 of 16 source positions passes the replica-identity test.

The rate rescaling is reasonable for randomly missing events, but the 16
positions are a fixed spatial quadrature/common-scenario set. Removing one
position changes that quadrature and breaks the intended common spatial set
across design points. The missing position may also be a high-QP caustic, so
the loss need not be ignorable.

**Required behavior:** preserve `n_positions_expected` and
`n_positions_present` in `qp_summary.csv`, and require equality for every
replica used by screening or optimization. Comparing observed `n_sim` with
the manifest-declared `n_sim` is a useful additional check. A strict Stage-4
run should retry the missing sub-run or reject the evaluation.

### 1.4 Minimal Stage-3 hardening

Do not build a second replica validator for Stage 4. Move the following logic
into one small shared module and call it from Stage 2b, Stage 3, and Stage 4:

```text
validate_result_completeness(manifest, summary, design_ids):
    require manifest
    parse labels strictly
    require exactly the requested design IDs in the manifest
    require every manifest sample_name to be unique
    require every summary sample_name to be unique
    require Counter(observed names) == Counter(expected names) per design
    require replica number and design label to agree with the manifest
    require positions_present == positions_expected
    require observed n_sim == expected n_sim
    reject unexpected rows
```

This is a small prerequisite, not a redesign of Stage 3.

---

## 2. Formulation of the Stage-4 problem

### 2.1 Separate design, environment, fidelity, and random noise

Let:

- \(x\) be the material/fabrication design;
- \(u\) be a source/environment scenario, including source energy and
  position set;
- \(b\) be the computational budget/fidelity;
- \(r\) be an independent Monte Carlo replica; and
- \(Y(x,u,b,r)\) be the measured QP rate.

For a count \(C\) accumulated over \(N\) simulated primary events,

\[
Y(x,u,b,r)=\frac{C(x,u,b,r)}{N(b)}.
\]

The simulator provides a noisy observation of an underlying response:

\[
Y(x,u,b,r)=f(x,u)+\epsilon(x,u,b,r).
\]

The noise is heteroscedastic and compound-Poisson-like. The Stage-3 data
already show that the dispersion changes across the design box, so a single
global Fano factor must not be imposed on every candidate.

These four concepts must remain different in the code:

- **design variables** are changed by the optimizer;
- **environment scenarios** are averaged or treated robustly, never
  optimized to make the device look good;
- **fidelity** controls computational effort; and
- **replicas** estimate Monte Carlo uncertainty.

### 2.2 Initial objective

Use one objective for the minimum viable optimizer:

\[
J_{\rm mean}(x)
  = \sum_u w_u\,
    \mathbb{E}_r\!\left[
      \frac{\mathrm{total\_QPs}(x,u,r)}{N(x,u,r)}
    \right],
\qquad \sum_u w_u=1.
\]

The first implementation can use the fixed Stage-3 source protocol and equal
scenario weights. Store, but do not initially optimize, these secondary
metrics:

- maximum electrode QP rate;
- upper quantile over source positions or source-energy scenarios;
- fraction of events/design replicas with no QP signal;
- wall-clock cost; and
- feasibility/completeness status.

This keeps the first optimizer statistically and operationally simple while
preserving the information needed for later multi-objective work.

`max_electrode_QPs` in the current summary is the maximum over electrodes
after position pooling. It is not a worst-source-position statistic. Before
using a positional tail-risk objective, Stage 2 must retain per-position
metrics.

### 2.3 Later robust or multi-objective formulation

Once per-position/per-scenario output is available, reasonable extensions are:

\[
J_{\rm robust}(x)
  = \mathbb{E}_u[Y(x,u)]
    +\lambda\,Q_{0.90,u}[Y(x,u)],
\]

or a two-objective Pareto problem:

\[
\min_x\left(
  \mathbb{E}_u[Y_{\rm total}(x,u)],
  Q_{0.90,u}[Y_{\rm max\ electrode}(x,u)]
\right).
\]

Fabrication cost, microwave loss, critical current, qubit frequency, thermal
load, and film stress are constraints or additional objectives. They must not
be hidden inside an arbitrary weighted QP score before their acceptable scales
are defined.

---

## 3. Physically meaningful Stage-4 design variables

The optimizer must not search the 53 Morris columns independently. Morris
identified sensitive simulation parameters, but many of those parameters are
correlated properties of one real material or are not fabrication knobs.

### 3.1 Recommended semantic search space

| Semantic coordinate | Type | Expansion into current simulation | Initial treatment |
|---|---|---|---|
| Backside material preset | categorical | bottom gap threshold, sound speed, phonon lifetime, absorption/interface properties, source material | Include only calibrated, internally coherent presets plus a proper no-film control |
| Backside thickness | continuous or fabrication grid | `setBotThickness` | Explore approximately 0.5–10 µm after geometry validation; no-film is a separate preset |
| Backside coverage \(f_{\rm cov}\) | continuous | derives `setIsland` and `setIslandSpacing` | Approximately 0.3–0.9, subject to fabrication/microwave constraints |
| Backside pitch \(p\) | continuous or fabrication grid | derives `setIsland` and `setIslandSpacing` | Approximately 125–375 µm initially |
| Junction/top material preset | categorical | top gap, sound speed, lifetime, absorption/interface properties | Fix to the calibrated Al baseline in the MVP; vary only when electrical qubit constraints are available |
| Junction/top thickness | continuous | `setTopThickness` | Include within a fabricable, electrically acceptable range |
| Package/edge treatment preset | categorical | calibrated `setWallAbs`/specularity behavior | Defer or use a few calibrated treatments; do not optimize an unconstrained effective probability |
| Substrate/orientation preset | categorical scenario or design | coherent lattice constants and manufacturable orientation | Fix Si baseline initially; never sweep elastic constants independently |

For backside patterning,

\[
p=I+S,\qquad
f_{\rm cov}=\left(\frac{I}{I+S}\right)^2,
\]

so the deterministic inverse map is

\[
I=p\sqrt{f_{\rm cov}},\qquad
S=p\left(1-\sqrt{f_{\rm cov}}\right).
\]

This parameterization is preferable to independently optimizing island size
and spacing because coverage and pitch have direct fabrication meanings.

### 3.2 Parameters to fix or treat as scenarios

Do not expose these as Stage-4 design coordinates:

- `/main/gun/setEnergy`, source type, and source position;
- `/g4cmp/minEPhonons`, bounce limits, and numerical tolerances;
- the six QPDE dummy parameters;
- qubit width and height unless electrical/device constraints are included;
- `QPLim`, which is a model/numerical control and must remain at a safe value;
- individual Si stiffness, dynamical, Debye, sound-speed, scattering, and
  decay constants;
- material gap, sound speed, lifetime, and absorption as mutually independent
  continuous knobs; and
- effective absorption probabilities for which no fabrication-to-model map
  exists.

The high Morris influence of `setTopGap`, `setTopAbs`, `setTopVSound`,
`setTopPhLifetime`, and their bottom-film analogues is physically useful. It
means material choice and interface engineering matter. It does **not** imply
that an optimizer should combine the gap of one material, velocity of another,
and lifetime of a third.

### 3.3 Hard constraints before simulation

The deterministic design validator should reject a proposal unless:

- every scalar is finite and within its declared semantic bound;
- the material preset exists and expands all coupled properties;
- thickness, pitch, island size, and spacing are geometrically valid;
- coverage lies strictly between zero and one for a patterned-film preset;
- `QPLim >= 2`;
- transverse sound speed remains below longitudinal sound speed;
- any variable cubic crystal satisfies the Born stability constraints;
- numerical tracking thresholds remain below every relevant physical
  threshold;
- macro/config commands and units round-trip to the intended values; and
- electrical/fabrication constraints attached to the chosen material preset
  pass.

Rejecting an invalid proposal before Geant4 is much cheaper and less biased
than assigning it a made-up QP penalty after a crash.

---

## 4. Optimization algorithms

### 4.1 Ranked recommendation

| Rank | Algorithm | Best use here | Why it fits | Main limitation |
|---:|---|---|---|---|
| 1 | Ax/BoTorch noisy batch Bayesian optimization with `qLogNEI` | Primary single-objective optimizer | Designed for expensive noisy black boxes; uncertainty-aware; proposes parallel batches; supports constraints and measured standard errors | GP modeling is less natural for many categorical/conditional variables and eventually scales poorly with very large observation sets |
| 2 | SMAC3 random-forest Bayesian optimization | Independent benchmark; primary alternative for a strongly mixed space | Handles categorical, integer, conditional, discontinuous, and failure-heavy spaces naturally; random forests do not require a smooth response | Usually less sample-efficient than a well-specified low-dimensional GP on a smooth continuous subspace; uncertainty is less principled |
| 3 | Multi-fidelity BO (`qMFKG`) or SMAC/Hyperband | Later cost-saving extension | Can allocate expensive target-fidelity evaluations only where lower-cost data are informative | Event count mainly changes variance, not the mean response; blindly treating it as a biased fidelity can promote noise |
| 4 | TuRBO-style local Bayesian optimization | Local refinement after a promising preset/region is found | Trust regions work well for moderately higher-dimensional or locally rugged continuous problems | The reference TuRBO implementation is for noise-free observations; it is not the first choice for this heteroscedastic screen |
| 5 | CMA-ES | Derivative-free cross-check on a fixed continuous preset | Robust to nonlinearity, simple, and parallel | Requires many expensive simulator calls and handles categorical material choices awkwardly |
| 6 | qLogNEHVI or NSGA-II | Later multi-objective optimization | Returns a Pareto set for QP damage versus fabrication/device trade-offs | Multi-objective learning needs more high-quality data; NSGA-II is especially evaluation-hungry |
| Baseline | Scrambled Sobol/random search | Initialization, tests, and performance baseline | Reproducible, space-filling, dependency-light, and difficult to fool | Does not learn from previous evaluations |

### 4.2 Primary algorithm: Ax/BoTorch with `qLogNEI`

Bayesian optimization is the best match to the dominant cost: each new
Geant4/G4CMP evaluation is expensive, while fitting and optimizing a surrogate
is cheap by comparison.

Use:

- a small semantic design space, ideally about four to eight active
  coordinates after material expansion;
- a scrambled-Sobol initial design containing the present baseline, the
  no-backside-film control, and literature/physics-motivated controls;
- a GP supplied with candidate-specific observation variance from replicas;
- `qLogNoisyExpectedImprovement` for a noisy single objective;
- batch size \(q\) chosen to keep the available simulation workers busy; and
- deterministic feasibility constraints before candidate launch.

The logarithmic EI variants are preferred to legacy `qNEI`/`qNEHVI` because
current BoTorch documentation recommends them for better numerical behavior.

For a few material presets, keep `preset_id` as a genuine categorical
coordinate in the core domain. The Ax adapter can initially use a mixed search
space. If categorical behavior is poor, the adapter—not the physics
pipeline—can enumerate preset combinations as arms and run a continuous model
inside each arm. The rest of the code should not change.

### 4.3 SMAC3 as the required benchmark

SMAC3 is especially attractive if Stage 4 later contains:

- several discrete material and package choices;
- conditional parameters, such as thickness and coverage existing only when a
  film is present;
- fabrication-grid integers;
- sharp threshold behavior; or
- a design-dependent simulator failure boundary.

Its random-forest surrogate is less tied to smooth Euclidean geometry than a
standard GP. Run SMAC on the same database and budget as the Ax backend. If
both methods converge to the same material region, confidence rises; if they
do not, compare them on fresh high-fidelity evaluations rather than choosing
by their own surrogate predictions.

### 4.4 Multi-fidelity requires a careful definition

Changing event count \(N\) or replica count \(R\) primarily changes estimator
variance:

\[
\operatorname{Var}(\bar Y)
  \propto \frac{F(x)}{N R},
\]

where the effective dispersion \(F(x)\) depends on the candidate. This is
better treated initially as **adaptive precision/replication**, not as a
different physical response.

A safe first policy is:

1. evaluate every new candidate at a pilot precision high enough to avoid a
   mostly-zero response;
2. estimate its uncertainty from independent replicas;
3. allocate more events or replicas to candidates that are promising but
   insufficiently resolved; and
4. validate finalists at the target precision with fresh seed banks.

Source-position count, independent Sobol scrambles, simplified source spectra,
or approximate geometries can be true fidelity axes only if nested pilot runs
show that they preserve candidate ranking and correlate strongly with the
target response. Once that is demonstrated, cost-aware `qMFKG` or
SMAC/Hyperband becomes justified.

### 4.5 Multi-objective optimization

After the single-objective loop works and secondary outputs are trustworthy,
use BoTorch `qLogNEHVI` to learn a Pareto front. Good initial pairs are:

- mean total QP rate versus upper-quantile maximum-electrode QP rate; or
- QP rate versus a quantified fabrication/electrical cost.

Do not start with NSGA-II on the raw simulator. It is a useful cross-check or
surrogate optimizer, but its population requires far more expensive
evaluations than a noise-aware BO method.

### 4.6 Algorithms not recommended as the primary method

- A deep neural-network surrogate needs far more coherent Stage-4 training
  data than are currently available.
- Reinforcement learning adds no natural advantage to a static black-box
  design problem.
- Pure genetic algorithms are robust but too evaluation-hungry at the present
  simulation cost.
- A large language model is not a calibrated numerical optimizer and should
  not choose material parameters directly.
- The old 6,912-point Morris run should inform variable selection and physical
  priors, but should not be ingested as equal-quality Stage-4 training data:
  it contains independently varied nonphysical material combinations,
  elastically unstable crystal points, and a pre-fix random-stream history.

---

## 5. Algorithm-independent code architecture

### 5.1 Design principles

1. **The optimizer proposes semantic designs only.** It never writes a Geant4
   command.
2. **One deterministic domain layer expands and validates every design.**
   Ax, SMAC, Sobol, and future algorithms use the same layer.
3. **The existing Stage-1 macro/config writer remains the single physics
   writer.** Add an explicit-design input mode instead of copying it.
4. **A database, not optimizer memory, is the source of truth.** An optimizer
   backend can be replaced or restarted from completed observations.
5. **Proposal, launch, collection, and recommendation are separate commands.**
   A multi-day campaign must resume cleanly after a shell, node, or agent
   failure.
6. **Raw evaluations are retained.** Never store only an averaged objective.
7. **Failures and infeasible designs are not QP measurements.** Track their
   types separately.
8. **Every result is tied to a protocol version.** The template, executable,
   material-preset table, source scenarios, cuts, event budget, position set,
   and seed policy are part of provenance.

### 5.2 Minimal proposed package

```text
stage4_config.toml
stage4/
    __init__.py
    types.py
    space.py
    store.py
    completeness.py
    runner.py
    controller.py
    cli.py
    backends/
        __init__.py
        base.py
        sobol.py
        ax_botorch.py
        smac.py                 # optional after the Ax MVP
tests/
    test_stage4_space.py
    test_stage4_completeness.py
    test_stage4_store.py
    test_stage4_fake_loop.py
```

This is deliberately small. `smac.py` and any LLM orchestration are not needed
for the first executable milestone.

### 5.3 Stable core records

Use frozen dataclasses or equivalent typed records:

```python
Candidate:
    candidate_id
    semantic_parameters
    design_hash
    provenance              # algorithm, batch, seed, human/LLM if applicable

ExpandedDesign:
    candidate_id
    semantic_parameters
    macro_parameters
    lattice_parameters
    preset_ids
    validation_version

Fidelity:
    fidelity_id
    events_per_position
    n_positions
    n_replicas
    position_set_id
    seed_bank_id

Evaluation:
    candidate_id
    fidelity_id
    scenario_id
    replica
    status
    raw_metrics
    n_events_expected
    n_events_observed
    n_positions_expected
    n_positions_observed
    wall_time
    results_dir
    failure_kind

Observation:
    candidate_id
    objective_mean
    objective_sem
    secondary_metrics
    total_cost
    complete
```

The optimizer sees `Candidate`, complete `Observation` records, and pending
candidate IDs. It does not see result-directory conventions, macros, hits
files, or scheduler commands.

### 5.4 Backend interface

Keep the common interface narrower than any one library:

```python
class OptimizerBackend(Protocol):
    name: str

    def propose(
        self,
        space: SearchSpace,
        observations: Sequence[Observation],
        pending: Sequence[Candidate],
        batch_size: int,
        seed: int,
    ) -> list[dict[str, object]]:
        """Return semantic parameter dictionaries only."""
```

A registry resolves the configured name:

```text
sobol          -> SobolBackend
ax_qlognei     -> AxBoTorchBackend
smac_rf        -> SmacBackend
```

Each backend converts the neutral `SearchSpace` and observations to its own
library objects. Imports must be lazy, so selecting `sobol` does not require
Ax, PyTorch, or SMAC to be installed.

Rebuild the backend from the experiment database at each proposal round.
Native snapshots may be cached for speed, but must not be the only recoverable
state. This makes switching from Ax to SMAC—or back to Sobol for a diagnostic
batch—a configuration change rather than a pipeline rewrite.

### 5.5 Search-space and material-preset layer

`space.py` should own:

- neutral continuous, integer, categorical, and conditional parameter specs;
- normalization and de-normalization;
- canonical parameter ordering;
- material-preset lookup;
- coverage/pitch conversion;
- expansion to the full Stage-1 parameter vector;
- bounds and coupled-physics validation;
- canonical JSON serialization; and
- a SHA-256 design hash.

Do not put Stage-4 presets directly into the Morris parameter lists.
`sensitivity_params.py` describes the Stage-3 screening space; a separate
versioned preset table should describe coherent candidate materials.

The preset file should contain calibrated values and citations/provenance. It
should not contain guessed Cu, Al, Ta, Nb, TiN, or interface constants merely
to make the optimizer runnable. An incomplete preset should fail validation.

### 5.6 Configuration

Use one human-readable TOML file. Python's standard `tomllib` avoids another
configuration dependency.

Conceptually:

```toml
[experiment]
name = "backside_material_v1"
protocol_version = "stage4-v1"
objective = "mean_qp_yield"

[algorithm]
name = "ax_qlognei"
seed = 20260728
batch_size = 8

[fidelity.pilot]
total_events_per_design_point = 4_000_000   # stage 1 derives /run/beamOn = total / (positions x replicas)
n_positions = 16
n_replicas = 2
position_set_id = "sobol-20260727-p16"

[fidelity.validation]
events_per_position = 625000
n_positions = 16
n_replicas = 3
position_set_id = "sobol-holdout-v1-p16"
```

The numerical values above illustrate the schema; they are not a final
fidelity recommendation. Pilot counts must be selected from a fresh
post-seed-fix variance study, and position count requires the pending
16/32/64 convergence test.

Algorithm-specific options belong under
`[algorithm.options]`. Physics/domain code must never branch on those options.

### 5.7 SQLite experiment store

Use the standard-library `sqlite3` module with three small tables:

```text
candidates
    candidate_id, design_hash UNIQUE, semantic_json, expanded_json,
    proposer, batch_id, created_at

evaluations
    evaluation_id, candidate_id, fidelity_id, scenario_id, replica,
    seed_bank_id, status, metrics_json, expected/observed counts,
    results_dir, wall_time, failure_kind

batches
    batch_id, algorithm, algorithm_options_json, status, created_at
```

SQLite is preferable to several mutable CSV files because transactions and
unique constraints prevent duplicate launches during restart or concurrent
monitoring. Existing Geant4 artifacts remain on disk; the database stores
their paths and checksums, not large hits data.

Use a unique key on:

```text
(candidate_id, fidelity_id, scenario_id, replica, seed_bank_id)
```

and use short transactions. Only the controller writes the database; workers
return result files/status to the controller.

### 5.8 Reuse Stage 1 instead of creating a second simulation writer

Add one explicit-design mode to `stage1_run_simulations.py`:

```text
--design-file <expanded_candidates.jsonl>
--run-kind optimization
--experiment-id <id>
```

With no `--design-file`, the current Morris/noise-floor behavior must remain
byte-for-byte compatible.

The explicit file should contain stable candidate labels and a complete
expanded parameter mapping. Stage 1 should:

1. validate every required current parameter is present exactly once;
2. call the existing macro/config substitution code;
3. write the manifest before launch;
4. record candidate ID, design hash, protocol version, fidelity, replica,
   position, seeds, and expected event count; and
5. use optimization-specific sample names such as `Opt_<short_id>`, not
   `Morris_i`.

Generalize `generate_runfiles()` to accept a sample name and seed group rather
than creating `Morris_<row>` internally. Do not duplicate its macro and lattice
writing code in `stage4/runner.py`.

### 5.9 Stage-4 seed policy

The Morris seed rule is trajectory-specific and must not be reused
accidentally for optimization. Derive a deterministic seed from:

```text
(experiment_seed, design_hash, scenario_id, seed_bank_id, replica, position)
```

Properties:

- retrying the same failed sub-run reproduces it exactly;
- different replicas and positions receive different streams;
- restarting a batch cannot silently recycle row-index-based seeds;
- adding a candidate in a later round cannot change older seeds; and
- final validation uses a holdout `seed_bank_id`.

Common-random-number comparisons can be added deliberately later, but an
arbitrary `candidate_index // 54` grouping has no meaning outside Morris.

### 5.10 Runner and controller responsibilities

`runner.py` is a thin adapter around the existing stages. It may:

- write the explicit-design batch;
- invoke Stage 1 or produce scheduler commands;
- invoke Stage 2 after simulations finish;
- call the shared completeness validator;
- aggregate raw replica metrics into observations; and
- record artifact paths and wall time.

It must not fit a surrogate or decide the next material.

`controller.py` owns the state machine:

```text
propose -> validate -> deduplicate -> prepare -> launch
        -> collect -> validate completeness -> aggregate -> record
        -> propose next batch
```

Expose separate resumable CLI commands:

```bash
python -m stage4.cli init      --config stage4_config.toml
python -m stage4.cli propose   --experiment <id>
python -m stage4.cli prepare   --batch <id>
python -m stage4.cli run       --batch <id>
python -m stage4.cli collect   --batch <id>
python -m stage4.cli status    --experiment <id>
python -m stage4.cli recommend --experiment <id>
```

An optional `cycle` command can compose these after every component is tested.
Do not begin with a monolithic command that hides a multi-day run and makes
recovery ambiguous.

### 5.11 Failure handling

Classify failures before informing an optimizer:

| Failure kind | Treatment |
|---|---|
| Invalid semantic/material design | Reject before simulation; ask backend for a replacement |
| Macro/config round-trip mismatch | Programming/configuration error; stop the batch |
| Missing position/replica or transient node failure | Retry the exact sub-run with the same seed |
| Known harmless exit-time fault with complete validated output | Record warning and accept only if completeness checks pass |
| Reproducible design-dependent simulator failure | Record as a feasibility outcome; do not invent a QP value |
| Incomplete output after retry limit | Mark failed and exclude from objective fitting, or use an explicit failure model |

Assigning every crash a very large QP penalty mixes software reliability with
material physics and can create a false optimum away from a technical failure
region.

---

## 6. Minimal implementation sequence

### Milestone 0 — qualify and freeze the protocol

Before fitting an optimizer:

1. apply the four Stage-3/completeness hardenings in Section 1.3;
2. rerun a small post-seed-fix identical-configuration replication;
3. perform the pending `minEPhonons` cutoff-convergence check;
4. compare nested or independently scrambled 16/32/64 position sets;
5. define the source-energy/environment scenario set;
6. choose the fixed Al baseline and calibrated backside presets;
7. define fabrication and electrical constraints; and
8. assign a protocol version and checksums.

Changing any item that alters the objective definition should create a new
protocol version, not silently append incompatible observations to an existing
experiment.

### Milestone 1 — neutral domain and fake evaluator

Implement:

- `types.py`;
- `space.py`;
- `store.py`;
- `completeness.py`;
- `backends/base.py`;
- `backends/sobol.py`; and
- a fake deterministic/noisy evaluator.

Demonstrate a complete propose/record/resume/recommend loop without Geant4.
This catches state, hashing, duplicate, and algorithm-interface mistakes in
seconds.

### Milestone 2 — explicit Stage-1 design mode

Make the smallest Stage-1 generalization described in Section 5.8. Verify:

- the old Morris path produces the same design and generated files;
- one expanded baseline candidate produces the same macro/config values as the
  equivalent Stage-3 baseline;
- candidate-specific seeds are stable and unique; and
- manifest completeness is known before launching.

Then connect Stage 2 and the shared validator. Keep the initial Stage-4
objective equal to the existing `QP_yield_per_event`.

### Milestone 3 — Sobol baseline campaign

Run a very small real campaign containing:

- present device baseline;
- no-backside-film control;
- one calibrated thick-backside-film control; and
- several Sobol designs over thickness, coverage, pitch, and top thickness.

This tests the end-to-end physics path and estimates cost/noise. It also
provides a baseline against which every adaptive optimizer must be compared.

### Milestone 4 — Ax/BoTorch backend

Add `ax_botorch.py` with:

- the same neutral search space;
- observed mean and standard error;
- pending candidate awareness;
- `qLogNEI`;
- deterministic bounds/feasibility filtering; and
- batch proposals.

Run it first on the fake evaluator, then on the small real data store from
Milestone 3. Verify that changing `[algorithm].name` is the only controller
change.

### Milestone 5 — adaptive precision and SMAC benchmark

After enough repeated candidates exist to characterize noise:

- add a promotion rule for more events/replicas;
- keep pilot and promoted data linked to the same design;
- validate the relationship between pilot and target precision; and
- add the SMAC adapter and compare under the same simulation budget.

Only after this evidence should `qMFKG`, Hyperband, or a more specialized
heteroscedastic/count surrogate be added.

### Milestone 6 — robust objectives and multi-objective BO

Extend Stage 2 to retain per-position/per-scenario metrics. Add robust
aggregation and then `qLogNEHVI`. Preserve all raw outcomes so objective
definitions can be recomputed without rerunning Geant4.

### Milestone 7 — optional open-model agent

Add the agent only after the deterministic CLI is reliable. The agent should
call the tested CLI/tools; it should not contain a second implementation of
the experiment loop.

---

## 7. Suggested optimization campaign

### 7.1 Initial design

For each allowed material-preset combination:

1. include the current baseline;
2. include the no-film or relevant physical control;
3. include any literature-motivated geometry that lies inside validated
   fabrication constraints; and
4. add a balanced scrambled-Sobol design over the continuous coordinates.

Do not initialize with thousands of old Morris points. Their best use is
choosing the semantic coordinates, bounds, interactions to expect, and control
designs.

### 7.2 Closed-loop batches

For each round:

1. read all complete observations and pending candidates from SQLite;
2. ask the selected backend for a batch;
3. expand and validate proposals;
4. remove duplicate design hashes;
5. run the pilot precision;
6. retry incomplete sub-runs;
7. record raw replica/scenario results and candidate-specific uncertainty;
8. promote promising or unresolved candidates to higher precision; and
9. refit/rebuild the backend from the database.

Batch size should be based on available memory and workers after multiplying
by positions and replicas. Eight material candidates are not eight processes:
with \(P=16\) and \(R=2\), they are 256 Geant4 sub-runs.

### 7.3 Stopping and final validation

Stop exploration based on both compute budget and lack of useful improvement,
not on the surrogate's single best predicted point.

Validate the top few distinct candidates and the current baseline using:

- a larger event budget;
- at least three independent replicas if affordable;
- fresh holdout seed banks;
- an independently scrambled or expanded source-position set;
- the agreed source-energy/environment scenarios; and
- paired comparisons and uncertainty intervals.

The final result should be a set of statistically distinguishable,
fabricable candidates. If their intervals overlap, report that the simulation
budget cannot rank them rather than declaring a false unique optimum.

---

## 8. Agentic AI with an open model

### 8.1 Appropriate role

An LLM agent can:

- summarize experiment state from read-only database queries;
- request the next batch from the deterministic backend;
- validate that a requested launch passed the deterministic preflight;
- generate scheduler commands;
- monitor progress and retry approved transient failures;
- classify logs into known failure categories;
- compare observations with Stage-3 physical expectations;
- draft reports and plot requests; and
- alert a human when a protocol, constraint, or budget decision is required.

It must not:

- directly generate unconstrained material constants;
- change the objective, bounds, source distribution, numerical cuts, or
  fidelity without creating an approved protocol revision;
- invent or edit a material preset;
- overwrite seeds or result files;
- execute arbitrary SQL against the experiment database;
- convert a crash into an objective value;
- launch a high-cost validation batch without approval; or
- declare an optimum from pilot-fidelity predictions alone.

### 8.2 Safe tool boundary

Expose narrow structured tools such as:

```text
list_experiments
show_status
show_valid_presets
validate_candidate
request_optimizer_batch
estimate_batch_cost
prepare_batch
launch_batch             # approval required above a configured cost
collect_batch
compare_candidates
draft_report
```

Tool inputs and outputs should use strict JSON schemas. The agent receives no
general shell or direct database-write tool in production.

### 8.3 Model/runtime choices

Practical open-model candidates include:

- **Devstral Small 2 (24B)** for a locally deployable code/tool-use agent; and
- **Qwen3-Coder** when stronger agentic coding/tool behavior and sufficient
  inference hardware or a hosted endpoint are available.

Serve through a runtime with explicit tool parsing and constrained structured
output, such as vLLM. Model selection should follow a small repository-specific
evaluation: correct tool choice, schema validity, refusal to bypass launch
approval, recovery from a missing replica, and faithful explanation of the
optimization state. Public coding leaderboards do not test these scientific
safety properties.

The LLM is an orchestration and interpretation layer around:

```text
human approval
      -> agent
      -> typed Stage-4 tools
      -> deterministic validator
      -> Ax/BoTorch, SMAC, or Sobol backend
      -> scheduler and Geant4/G4CMP
      -> Stage 2 and completeness validator
      -> immutable experiment record
```

---

## 9. Tests and acceptance criteria

### 9.1 Unit tests

- coverage/pitch conversion and boundary cases;
- material-preset expansion contains every coupled property;
- incomplete/unknown presets fail;
- nonfinite, out-of-bounds, invalid-velocity, unstable-crystal, and unsafe
  `QPLim` designs fail before macro generation;
- canonical hashes are stable under dictionary ordering;
- candidate seeds are stable, distinct, and independent of batch row order;
- duplicate manifest/summary identities fail;
- a manifest index hole fails;
- a missing position fails;
- a missing replica fails;
- an unexpected extra result fails;
- SQLite unique constraints prevent duplicate candidate/evaluation launch;
- every backend satisfies the same proposal contract.

### 9.2 Integration tests

- generate-only Stage-4 baseline and round-trip every intended macro/config
  value;
- run two tiny-event candidates through Stages 1 and 2;
- interrupt and resume between every controller state;
- retry one deliberately missing sub-run with the same seed;
- switch from Sobol to Ax using the same database;
- run a synthetic noisy objective with known optimum through every backend;
- verify technical failures never appear as QP objective values; and
- verify the existing Stage-3 result remains unchanged after shared-validator
  refactoring.

### 9.3 Minimum viable completion criteria

Stage 4 is ready for a real adaptive campaign only when:

1. a candidate can be proposed, expanded, validated, simulated, collected,
   and recorded without algorithm-specific physics code;
2. interruption/restart cannot duplicate a simulation;
3. missing replicas and positions fail closed;
4. a Sobol fake and real smoke campaign completes;
5. Ax can reconstruct its proposal state from the shared observation store;
6. changing the backend requires only a configuration change; and
7. a final validation batch uses fresh seeds and an explicitly versioned
   target fidelity.

---

## 10. Official algorithm/runtime references

- BoTorch overview of noisy, expensive, batch black-box optimization:
  <https://botorch.org/docs/overview>
- BoTorch constrained batch `qLogNEI` tutorial:
  <https://botorch.org/docs/next/tutorials/closed_loop_botorch_only>
- BoTorch multi-objective optimization and `qLogNEHVI`:
  <https://botorch.org/docs/v0.16.0/multi_objective>
- BoTorch multi-fidelity knowledge-gradient tutorial:
  <https://botorch.org/docs/v0.14.0/tutorials/multi_fidelity_bo>
- SMAC3 getting started and backend characteristics:
  <https://automl.github.io/SMAC3/latest/3_getting_started/>
- SMAC3 multi-fidelity/Hyperband documentation:
  <https://automl.github.io/SMAC3/latest/advanced_usage/2_multi_fidelity/>
- Reference TuRBO implementation and its warning about noisy observations:
  <https://github.com/uber-research/TuRBO>
- vLLM structured tool-calling documentation:
  <https://docs.vllm.ai/en/stable/features/tool_calling/>
- Devstral 2 / Devstral Small 2:
  <https://mistral.ai/news/devstral-2-vibe-cli/>
- Qwen3-Coder:
  <https://qwenlm.github.io/blog/qwen3-coder/>
- Qwen-Agent tool parsing and vLLM deployment:
  <https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/quickstart/>

## Final recommendation

Implement a strict shared completeness validator first. Then build a
data-centric Stage-4 loop with:

1. coherent material presets and four-to-eight semantic coordinates;
2. a deterministic expansion/validation layer;
3. an explicit-design mode in the existing Stage-1 writer;
4. raw evaluations and aggregated observations in SQLite;
5. a narrow backend protocol with a Sobol baseline;
6. Ax/BoTorch `qLogNEI` as the primary optimizer;
7. adaptive replication before true multi-fidelity modeling;
8. SMAC3 as an independent mixed-space benchmark; and
9. an optional guarded open-model agent only after the deterministic CLI is
   reliable.

This structure keeps material physics, simulation execution, statistics, and
optimization separate. It is the smallest design that allows algorithms to be
switched without copying the most failure-prone part of the project: the
Geant4/G4CMP parameter-to-macro pipeline.
