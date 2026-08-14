# Stage 3 Small-Material Start Checklist

## Scope of the first campaign

The first campaign is a **small, explicit material study**, not a Materials
Project database scan. Treat these as the three selectable device layers:

1. crystalline substrate;
2. top ground-plane film, excluding the 17 Al junction electrodes;
3. bottom film.

Keep the Al junction material and its junction parameters fixed. The three
interface controls still change when their adjoining materials change:

| Macro control | Physical interface | Depends on |
|---|---|---|
| `setTopAbs` | substrate / fixed Al junction | substrate |
| `setTopFilmAbs` | substrate / top ground plane | substrate + top film |
| `setBotAbs` | substrate / bottom film | substrate + bottom film |

For a first end-to-end study, use only two candidates per selectable layer.
That is an eight-combination factorial, small enough to evaluate exhaustively:

- substrate: Si baseline and Ge;
- top ground plane: Nb baseline and one second **high-gap** film for which all
  cryogenic film inputs are available;
- bottom film: Cu baseline and Ti, provided both are intentionally represented
  by the same normal-film model.

Do **not** use Al as the second top-ground-plane candidate under the fixed
1 meV injection protocol. The current objective contract requires
`E_gun < 2*top_film_gap`; Al would violate that inequality and would change the
objective from junction-only QPs to junction plus ground-plane interactions.
Either select a film with gap greater than 0.5 meV or define and validate a new
objective before admitting it.

With eight combinations, exhaustive comparison plus uncertainty estimates is
more reliable and simpler than Bayesian optimization. Bayesian or
multi-fidelity optimization becomes useful after the evaluator is validated
and the candidate/continuous design space is enlarged.

## Required sequence before optimization

Complete these nine items in order. An item passes only when its exit check is
recorded; creating a file without passing the check is not completion.

### 1. Freeze the Stage 3 experiment contract

Create `stage3_config.yaml` and classify every value as exactly one of:
`fixed`, `decision`, or `derived`.

Record:

- the three material allowlists and fixed Al junction;
- the full declared injection-site set (currently 16 harness positions), its
  order, and its weights; keep this distinct from the 17 readout electrodes;
- `E_gun = 10.0e-3 eV` (muon-strike maximum) and `minEPhonons = 0.0000382 eV`;
- the F1/F2 event budgets, replica count, explicit seed banks, timeouts, and
  memory limits;
- the objective as the sum of QPs over all 17 electrodes and all declared
  scenarios, normalized per primary event when event counts differ;
- hashes or Git revisions for the executable, templates, Stage 2 converter,
  lattice records, and material catalog.

**Exit check:** the threshold assertion passes for every enabled top-film
candidate:

```text
minEPhonons < 2*Al_junction_gap <= E_gun        (hard gates)
```

The former upper bound `E_gun < 2*top_film_gap` is **withdrawn**: film
absorptions are never recorded under the Junction hit type, so the objective is
junction-only at any energy (measured at 10 meV: 68/68 recorded hits inside
junction footprints). The film gap now classifies the regime — whether the
ground plane competes for phonons — which must be constant across a comparison
set and is recorded, not gated.

### 2. Build the single-candidate Stage 3 evaluator

Implement a new evaluator; do not modify or invoke
`stage2_compute_QPs_sensitivity_analysis.py`. The evaluator should accept one
resolved material triplet, a fidelity, the full scenario set, and a seed bank.
It must:

1. create all position/replica sub-runs;
2. write the planned identities before launching them;
3. run with the existing timeout and memory guard;
4. require a matching post-`beamOn` completion marker;
5. parse QPs using reusable Stage 2 calculation code;
6. refuse to score any missing, duplicated, corrupt, or mismatched sub-run;
7. return total and per-electrode QPs, events, runtime, and status.

The process exit status must be nonzero when a requested candidate cannot be
scored. A diagnostic partial result may be displayed, but it is never an
optimizer observation.

**Exit check:** the Si/Nb/Cu baseline runs twice with the same seeds and gives
identical raw outputs and objective.

### 3. Add a restartable ledger and exact cache identity

Use SQLite with one candidate table and one sub-run table. A cache key must
include, at minimum:

- resolved material records and derived interface values;
- crystal orientation and lattice configuration;
- energy, cutoff, event count, ordered positions, and weights;
- every seed and replica identity;
- executable, template, resolver, and Stage 2 code hashes.

Write `planned` rows before launch, then transition them to terminal statuses.
Never infer a zero-QP success from a missing output.

**Exit check:** interrupt a test campaign, resume it, and verify that completed
sub-runs are reused exactly once while incomplete ones are rerun.

### 4. Implement complete material propagation

This item answers: **what physical material is Geant4/G4CMP actually
simulating?** A candidate is not implemented merely because its elasticity
tensor was copied into `Si/config.txt`.

#### 4.1 Use different records for substrate and films

Each substrate record must contain:

- internal ID and optional Materials Project ID;
- formula, provenance, units, and retrieval/database version;
- Geant4 material name and density;
- G4CMP lattice-map name and crystal orientation;
- full elasticity tensor in a declared convention, or validated cubic
  `C11`, `C12`, `C44` values;
- all G4CMP lattice parameters used by `config.txt`, including scattering,
  decay, `decayTT`, Debye/DOS inputs, and any other active line;
- derived longitudinal and transverse sound speeds.

Each film record must contain:

- Geant4 material name and density;
- thickness, sound speed, superconducting/normal classification;
- gap, QP limit, phonon lifetime, lifetime slope, and any active absorption or
  subgap setting;
- the source and temperature/film assumptions for every cryogenic value.

Materials Project can supply useful bulk crystal structure, density, and
elasticity data, but it does **not** complete this film record. In particular,
do not invent low-temperature thin-film gaps, phonon lifetimes, or interface
parameters from Materials Project bulk data. Curate those from measurements or
appropriate literature and record the assumptions.

#### 4.2 Resolve one triplet without fallbacks

`material_resolver.py` should convert exactly one
`(substrate, top_ground_film, bottom_film)` triplet into:

- a Geant4 material selection for all three volumes;
- a candidate-specific G4CMP lattice directory/configuration;
- all macro commands for top, top-film, and bottom properties;
- the three derived interface values from item 5;
- a machine-readable resolved snapshot stored with the run.

It must fail before simulation if a required value is missing. It must never
borrow Si, Nb, or Cu values merely because a candidate field is absent.

#### 4.3 Keep the first pilot within supported material paths

For the initial substrate pair, Si and Ge are practical because the detector
code already recognizes `G4_Si`/`G4_Ge` and local `Si`/`Ge` G4CMP lattice maps
exist. This avoids writing a custom Geant4 material registry during the first
pilot. A later compound candidate is allowed only after both a valid Geant4
material/density and a compatible G4CMP lattice map are installed and checked.

For every resolved candidate, compare the density used by the Geant4 material
against the catalog density. A mismatch beyond a declared tolerance is a hard
error, not a warning.

#### 4.4 Validate propagation layer by layer

Run these resolver-only tests before physics runs:

1. baseline Si/Nb/Cu reproduces every current macro and lattice value;
2. change Si to Ge and verify only substrate-dependent and interface-dependent
   fields change;
3. change Nb to the second top film and verify the junction remains Al;
4. change Cu to the second bottom film and verify the bottom material and
   normal/superconducting mode change exactly as declared;
5. insert one deliberately missing field and confirm resolution fails.

**Exit check:** the resolved snapshot proves the requested identities,
densities, tensors, film properties, and derived speeds are the values consumed
by the generated inputs.

### 5. Implement and validate interface coupling

This item answers: **what happens when a substrate phonon reaches each film?**
It is separate from item 4. Correct bulk materials do not imply a correct
boundary model.

#### 5.1 Define the model before coding it

At minimum, an acoustic-mismatch calculation uses each side's density and
longitudinal/transverse sound speeds. The normal-incidence scalar expression

```text
T = 4 Z1 Z2 / (Z1 + Z2)^2,   Z = density * sound_speed
```

is useful as a unit test, but it is not by itself a defensible replacement for
the three production absorption values. The crystal problem includes angle,
polarization, anisotropy, critical angles, and mode conversion. Also, the
G4CMP `filmAbsorption` probability is an **effective boundary parameter**; it
need not equal a bare normal-incidence transmission coefficient.

For the first small study, choose and document one of these policies:

- **Recommended pilot policy — baseline-calibrated effective AMM.** Calculate
  a consistently averaged acoustic-mismatch transmission for each interface,
  then scale it relative to the matching Si baseline:

  ```text
  A_candidate = clip(A_Si_baseline * T_candidate / T_Si_baseline, 0, 1)
  ```

  Use `A_Si/Al=0.795`, `A_Si/Nb=0.745`, and `A_Si/Cu=0.736` for the three
  separately calibrated families. This preserves the known baseline while
  making the candidate dependence explicit. Label results as model-dependent.

- **Later high-fidelity policy — anisotropic AMM.** Solve angle- and
  mode-resolved transmission from density, tensors/sound modes, interface
  orientation, and an angular incident distribution, then flux-average the
  result. Use this for final physical claims if interface coupling materially
  changes rankings.

Do not mix raw normal-incidence `T`, a fitted effective probability, and the
three old constants across candidates.

#### 5.2 Compute all three controls per triplet

For every candidate:

```text
setTopAbs     = model(substrate, fixed Al junction)
setTopFilmAbs = model(substrate, selected top ground film)
setBotAbs     = model(substrate, selected bottom film)
```

Changing only the film with a fixed substrate changes its corresponding
interface. Changing the substrate requires recomputing **all three** values.

#### 5.3 Add hard validation tests

Test:

- all outputs are finite and in `[0,1]`;
- the three Si baseline pairs reproduce 0.795, 0.745, and 0.736 within a
  declared numerical tolerance;
- identical materials pass the chosen model's analytic limit;
- swapping input order behaves according to the declared energy-flux
  convention;
- SI inputs and macro units are converted explicitly;
- changing density or sound speed changes the result and cannot hit a cached
  value for another material;
- an unavailable film property causes rejection, not fallback.

#### 5.4 Quantify model uncertainty

Before ranking materials, rerun the eight combinations with plausible upper
and lower interface values or with both the calibrated and higher-fidelity
models. If material rankings reverse, the interface model is a dominant
uncertainty and optimization should pause for better interface data.

**Exit check:** all three baseline values are reconstructed, every enabled
triplet gets three candidate-specific values, and the ranking-stability test is
reported.

### 6. Build a tiny, reproducible material catalog

Use an explicit allowlist of Materials Project IDs. Do not call a database-wide
`summary.search()` in the first campaign. Query only the requested summary and
elasticity fields, and save:

- raw response snapshot;
- Materials Project ID and formula;
- requested fields and units;
- Materials Project database version;
- retrieval time and `mp-api` package version;
- manually curated non-MP fields with separate provenance.

Set the API key locally:

```bash
cd /home/htseng/Sensitivity_Analysis_HT/parameter_optimization
cp .env.example .env
# Edit .env and replace the placeholder. Do not commit or paste the key.
set -a
source .env
set +a
```

The client can then read `MP_API_KEY` from the environment. The repository
contains only the placeholder. Use `small_material_candidates.example.yaml` as
the allowlist template; placeholder candidates remain disabled until every
required field is supplied.

**Exit check:** rebuilding the catalog from the same explicit IDs produces a
complete, versioned snapshot, and no secret appears in Git status, logs, or run
artifacts.

### 7. Run one-factor physics pilots

Use common random numbers: identical positions and seeds for every comparison.
Run baseline, then replace one layer at a time:

1. Si/Nb/Cu;
2. Ge/Nb/Cu;
3. Si/second-top/Cu;
4. Si/Nb/second-bottom.

Check generated files, completion, energy accounting, QP location totals, and
the top-film threshold contract. These are correctness tests, not material
rankings.

**Exit check:** each observed change can be traced to a resolved bulk or
interface change, with no stale files or baseline fallbacks.

### 8. Calibrate numerical convergence and resource limits

For the baseline and at least one contrasting material triplet, test:

- the 38.2 micro-eV tracking cutoff against one lower cutoff;
- spatial quadrature at increasing position counts;
- F1 versus F2 event counts and replicate counts;
- 32 workers first, then 64 only after peak RSS, timeout, and failure behavior
  are acceptable under the memory guard.

Define quantitative acceptance tolerances before looking at the result.

**Exit check:** a short report fixes the lowest fidelity that preserves the
ranking signal and the safe worker count.

### 9. Validate failure handling, then enumerate the small factorial

Inject test failures: missing marker, truncated hits, duplicate position,
wrong seed/event identity, timeout, and interrupted ledger. Each must be
rejected or resumed correctly.

Then evaluate all eight enabled combinations at screening fidelity using the
same positions and seed bank. Promote competitive combinations to confirmation
fidelity and held-out seeds. Report paired differences from the baseline with
uncertainty, not just the smallest single noisy count.

Only after this exhaustive pilot should the material set be enlarged. At that
point choose the optimizer based on the space:

- small categorical set: exhaustive enumeration or successive halving;
- larger mixed categorical/continuous set: random-forest SMAC;
- mostly continuous, expensive, low-dimensional set: Gaussian-process Bayesian
  optimization;
- multiple fidelities with predictive low-fidelity ranks: asynchronous
  successive halving/Hyperband or multi-fidelity SMAC.

**Exit check:** every enabled combination has a complete comparable score,
held-out confirmation agrees with the selected ranking, and all results can be
reproduced from the ledger and snapshots.

## Review of this sequence (2026-08-13)

Verdict: **the sequence is right and the ordering is right.** Nothing in it
should be reordered. What follows is a triage of what is genuinely blocking,
what can be deferred, and three findings that change the size of two items.

### Already done, ahead of the checklist

Items in this document that the harness now satisfies, so they need building
only into the Stage 3 evaluator rather than from scratch:

| Checklist requirement | Status |
|---|---|
| §2.3 timeout and memory guard | Done — `sensitivity_memguard.py`, per-sample + aggregate caps |
| §2.4 post-`beamOn` completion marker | Done — `/control/shell touch <hits>.done`, order-verified at generation |
| §2.6 refuse missing/corrupt/mismatched sub-runs | Done — strict by default in stage 2 |
| §1 threshold assertion | Done — `assert_excitation_thresholds()`, per design point |
| Declared injection-site set | Done — scrambled Sobol, identical across candidates |
| Explicit seed banks | Done — `/random/setSeeds`, keyed and verified |

### Fixed during this review

**Marker-list and position-identity validation (was a real gap).** The
completion check was written as `if markers and index < len(markers)`, so a
*ragged* `done_markers` list silently stopped checking the trailing sub-runs
while the summary still reported the set as complete — proof that quietly stops
applying is worse than no proof. `assert_manifest_identities()` now requires
`hits_files`, `done_markers`, `seeds` and `position_indices` to be the same
length, and `position_indices` to be exactly `{0..N-1}`. A duplicated site would
otherwise have been pooled as if it were two distinct sites, double-weighting
one region of the chip.

**Nonzero timeout (was a real gap).** `SENSITIVITY_SAMPLE_TIMEOUT` defaulted to
`0` — disabled. The memory guard only catches runaways that *grow*, so a hang
with flat RSS holds a worker slot for the whole campaign. Unset now **derives** a
bound (`max(1800 s, 100 × events × 1.3e-4 s)`, ~100× measured throughput) and
says so; explicit `0` still disables it but prints a warning. This is a watchdog
bound, not a calibration — item 8 must replace it with a measured value.

### Two findings that make item 4 larger than it reads

Item 4 is described as propagation plumbing. It is worse than that, and both
failure modes are silent:

1. **The Si density hardcode is a ~50% error, not a correction.**
   `derive_cubic_sound_speeds` divides by `SUBSTRATE_DENSITY_KG_M3 = 2329`
   (Si). Measured with the Ge tensor:

   | | v_L (m/s) | v_T (m/s) |
   |---|---:|---:|
   | Ge tensor + **Si** density (today's code) | 7966 | 4890 |
   | Ge tensor + **Ge** density (correct) | 5270 | 3234 |
   | shipped `CrystalMaps/Ge/config.txt` | 5324 | 3259 |

   The good news: with the correct density the code reproduces the independently
   shipped Ge config to **~1%**, so the Christoffel maths is validated against a
   second material. Only the density is wrong — and wrong by 50%.

2. **`FIXED_CONFIG_COMMANDS` actively overwrites candidate lattice values.** It
   forces `Debye 15 THz` and Si's third-order `dyn` constants over whatever the
   lattice template contains. Ge's own config declares **`Debye 2 THz`**, so a Ge
   run would be overwritten with a Si value **7.5× too large**. This is an
   overwrite, not an inherited default, and it would not appear anywhere in the
   output.

**Mitigation now in place:** `assert_substrate_is_supported()` refuses any
non-Si `LATTICE_MATERIAL` with both reasons spelled out, so the pseudo-material
cannot be produced by accident before item 4 lands.

### Item 4.3 is more achievable than assumed

The document assumes Si and Ge. The G4CMP checkout actually ships lattice maps
for **Al2O3 (sapphire), CaF2, CaWO4, GaAs, Ge, LiF and Si**. Combined with NIST
`G4_*` names, several of these need no custom Geant4 material registry — so the
substrate allowlist can grow without the "later compound candidate" work, once
density propagation exists. Sapphire is worth considering: it is a real qubit
substrate, not just a convenient second point. (Note it is not cubic, so the
cubic-specific derivation and Born checks would need the general tensor path.)

### Triage: what blocks, what can wait

| Item | Blocking? | Note |
|---|---|---|
| 1 Contract (`stage3_config.yaml`) | **Yes** | Cheap. Nothing downstream is reproducible without it |
| 2 Evaluator | **Yes** | The critical path. Everything else plugs into it |
| 3 Ledger + cache identity | **Yes, but can be thin** | Start with the schema, `planned` rows and the hash. Resume/retry can land one slice later — but the *hash* must be right from the first row, or early trials are unusable |
| 4 Material propagation | **Yes** | Larger than it reads — see above. Blocks any non-Si number |
| 5 Interface model | **Yes for cross-material claims** | Not needed for the Si/Nb/Cu baseline slice; needed before any candidate is *ranked* |
| 6 Tiny catalog | **Deferrable** | For an 8-combination study a hand-written YAML with provenance fields is sufficient. The MP exporter matters when the set grows |
| 7 One-factor pilots | **Yes** | Cheap and catches resolver errors that unit tests miss |
| 8 Convergence/resources | **Partly deferrable** | The 16/32/64 position convergence is the one that must not be skipped — the site set is the objective. Cutoff and F1/F2 can follow |
| 9 Failure handling + factorial | **Yes** | This is the deliverable |

**Deferrable without regret:** item 6's Materials Project machinery (use a
curated YAML first), and the higher-fidelity anisotropic interface model in
§5.1 — the baseline-calibrated policy is adequate provided §5.4's
ranking-stability check is actually run.

**Not deferrable, and easy to under-rate:** the ledger *hash* (item 3) and the
position-convergence pilot (item 8). Both are cheap now and expensive to
retrofit, because both invalidate every trial recorded before them.

### One inconsistency with the current code

§8 says "32 workers first, then 64". The runner's default is now **64**. For the
first pilots pass `SENSITIVITY_MAX_WORKERS=32` explicitly, as §8 intends — 32 is
the level validated end-to-end (221,184 sub-runs, 0 failures, 2.15 GB peak);
64 is extrapolation.

### Suggested first slice

Smaller than "evaluator + ledger", and it de-risks the rest:

1. `stage3_config.yaml` with the contract, and a loader that fails on any field
   not classified `fixed`/`decision`/`derived`.
2. The evaluator for **Si/Nb/Cu only**, reusing the existing generation and
   `stage2_compute_QPs` scoring, returning a `TrialResult`.
3. The ledger schema with the full cache hash, `planned` rows, and terminal
   statuses — resume can come next.
4. The §2 exit check: baseline twice with the same seeds, identical objective.

That is a working, restartable, single-candidate evaluator with no material
science in it yet — which is exactly the point, because every later item is then
a resolver change rather than an evaluator change.


## Implementation status (2026-08-13)

The **first slice is built and its exit checks pass against real Geant4**. What
follows records what exists, what was deliberately deferred, and the one
question that matters: when optimization may officially start.

### Built

| File | Role |
|---|---|
| `stage3_config.yaml` | The frozen contract (item 1) |
| `stage3_contract.py` | Loader + classification validator + threshold gate + code fingerprint |
| `stage3_ledger.py` | SQLite ledger, cache key, planned rows, terminal statuses |
| `stage3_trial_runner.py` | Single-candidate evaluator, `evaluate() -> TrialResult` |

Item 1 is enforced, not asserted: the loader refuses a contract where any key is
unclassified or appears in two of `fixed`/`decision`/`derived`, where a decision
has no `allowed` set, or where its value is outside that set.

### Exit checks passed

| Check | Result |
|---|---|
| Item 2 — baseline twice, same seeds, identical output | **PASS** — `total_QPs=10` and identical per-electrode vector both runs |
| Item 3 — interrupt, resume, reuse completed sub-runs once | **PASS** — 3 of 8 sub-runs failed and rerun, 5 reused, total identical to the uninterrupted baseline |
| Cache identity | **PASS** — identical request returns the cached trial; cache keys match across replay |
| Incomplete set never scored | **PASS** — `incomplete_scenario_set`, `total_QPs = None`, never `0.0` |
| Non-baseline material refused | **PASS** — Ge and Al rejected naming exactly what must exist first |

Measured on the way: **2.5e-5 QPs per primary event** at 400k events (close to
the 3.69e-5 planning figure), and ~1.2e-4 core-seconds per event, which confirms
the cost model used to size the fidelity ladder.

### Three bugs found by the tests, all fixed

1. **Retries corrupted the hits file.** `PhononSensitivity` opens it with
   `std::ios_base::app` (`PhononSensitivity.cc:84`), so a rerun *appended* to the
   previous attempt and embedded a second header row mid-file, which then parsed
   as strings in a numeric column. Retries now clear the hits file and the marker
   first. This one only appears once retry exists -- the Morris runner never
   retried, so it was latent.
2. **One bad sub-run aborted the whole trial.** An unexpected exception
   propagated out of the worker and discarded every sibling sub-run that had
   already succeeded. It is now recorded as a sub-run status, and the trial is
   judged incomplete like any other partial set.
3. **`--force` wrote nothing to the ledger.** It minted a new `trial_id` under
   the same `cache_key`, so the UNIQUE constraint silently dropped the row. Force
   now re-runs in place. A silent no-op write is worse than an error.

### Deliberately deferred, with the reason

| Deferred | Why it is safe to defer |
|---|---|
| Material resolver beyond Si/Nb/Cu | The evaluator refuses non-baseline candidates by name, so the pseudo-material cannot be produced by accident. Adding materials is now a resolver change, not an evaluator change. |
| Interface-transmission model | Baseline uses the calibrated 0.795/0.745/0.736 constants, labelled `interface_model: si_baseline_constants` in every ledger row. Required before any *cross-material* claim, not before the baseline is validated. |
| Materials Project exporter | An eight-combination study needs a curated YAML with provenance, not a database scan. The exporter matters when the set grows. |
| Anisotropic AMM (§5.1 high-fidelity) | The calibrated policy is adequate **provided §5.4's ranking-stability check is run**. |
| Async batching across candidates | The evaluator already parallelises sub-runs within a trial; cross-candidate batching is an optimizer-loop concern. |
| Retry orchestration beyond resume | Resume covers the interrupted-campaign case. Automatic in-run retry of a failed sub-run is a convenience, not a correctness gap -- an incomplete trial is refused, never scored. |

### When you can officially start optimization

**Not yet, and the remaining gate is short and specific.** The evaluator,
ledger, contract and completeness rules are done and tested. What is still
missing is entirely *material physics*, and all of it is on the objective path:

1. **Item 4 — candidate density propagation and candidate-aware config
   overrides.** Blocking. Measured: the Ge tensor with Si's density gives
   v_L = 7966 m/s against Ge's true 5324 -- a ~50% error -- and
   `FIXED_CONFIG_COMMANDS` would force Ge's `Debye 2 THz` to Si's `15 THz`.
   Both are silent today; the evaluator refuses non-Si until this lands.
2. **Item 5 — interface transmission**, validated to reproduce
   0.795 / 0.745 / 0.736. Blocking for any cross-material ranking, because with
   the substrate changed all three interface values change together.
3. **Item 6 — the small curated catalog** (two candidates per layer, with
   provenance for every cryogenic film value). Cheap.
4. **Item 7 — one-factor pilots**, then **item 8's position-convergence check**
   (16/32/64). The convergence check is the one that must not be skipped: the
   site set *is* the objective, and changing it later invalidates every trial
   recorded before it.

Once 1-4 pass, the eight-combination factorial is a loop over `evaluate()` --
the infrastructure will not need to change again. **Do not enlarge the material
set or reach for a Bayesian optimizer before then**: with eight combinations,
exhaustive enumeration with paired seeds is both simpler and more reliable, and
the optimizer choice only becomes interesting once the space is larger than the
budget.

## Item 4 decision record — candidate configuration policy

**Decision (2026-08-13):** for the first Si/Ge pilot, use each material's
complete native G4CMP configuration together with its matching Geant4 material
and density. Apply only explicit, provenance-backed overrides. Never create a
candidate by starting from the Si configuration and replacing a few Ge fields.

The concrete Ge configuration is:

```text
Geant4 material       G4_Ge
G4CMP lattice map     Ge
density for Cij/rho   actual G4_Ge material density
density for AMM       the same actual G4_Ge material density
lattice configuration complete native CrystalMaps/Ge/config.txt bundle
legacy Si overrides   disabled
```

### Why this is the correct hierarchy

G4CMP does not read substrate density from `config.txt`. On lattice loading,
`G4LatticeManager::LoadLattice()` executes
`newLat->SetDensity(Mat->GetDensity())`; therefore the selected `G4Material` is
the density actually used by the phonon kinematics. Using Materials Project
density in the resolver while Geant4 uses a different NIST density would still
produce an internally inconsistent candidate.

For campaign 1, the resolver must therefore:

1. select `G4_Si` with the Si lattice or `G4_Ge` with the Ge lattice;
2. obtain or verify the corresponding Geant4 material density;
3. use that same density for sound-speed derivation and interface coupling;
4. compare it with the catalog's expected density and reject a mismatch above
   a declared tolerance (initial recommendation: 0.5%);
5. record both the expected and effective runtime densities in the resolved
   snapshot and cache identity.

If a later campaign requires an exact Materials Project density, it must create
a custom `G4Material` with that density. Changing the Python-side density alone
does not change the density used inside G4CMP.

### Native bundle versus curated override

The resolver may support exactly two modes:

```yaml
config_mode: native_g4cmp
```

This copies the complete material-native lattice record. It is the recommended
mode for the first Si/Ge campaign.

```yaml
config_mode: curated_override
```

This is allowed only when the catalog supplies a coherent, provenance-backed
record for every active phonon field: lattice constant, elastic tensor and
convention, `dyn`, `scat`, `decay`, `decayTT`, `LDOS/STDOS/FTDOS`, `Debye`,
`vsound/vtrans`, density, and orientation. A partial override must fail.

In particular, do not combine a Materials Project tensor with Si third-order
elasticity, scattering, decay, DOS, or Debye values and label the result as the
candidate material. Materials Project data are useful for discovery and
cross-checks; they do not automatically replace a complete cryogenic G4CMP
record.

### Ge-specific policy

Use Ge's native values as one coherent pilot bundle, including its lattice
constant, `C11/C12/C44`, `dyn`, `scat`, `decay`, `decayTT`, DOS fractions, and
scalar speeds. Disable the legacy global `FIXED_CONFIG_COMMANDS` on the Stage 3
path; those commands contain Si's `dyn` and `Debye 15 THz` and must remain only
on the old Si sensitivity path.

The native Ge file's `Debye 2 THz` is configuration-correct relative to
silently inserting Si's 15 THz, but it is not accepted as unquestioned physical
ground truth. A commonly reported Ge Debye temperature near 374 K corresponds
to approximately 7.8 THz (`k_B*Theta_D/h`). In this G4CMP source, Debye energy
is used by `G4CMPEnergyPartition::GeneratePhonons()` to partition an energy
deposit into primary phonons. The current `phonon_Caustic` gun creates its
primary phonon directly, so the field is expected to be inactive for this
objective, but that expectation must be tested rather than assumed.

Run a small Ge A/B diagnostic with identical positions and seeds:

```text
Ge native Debye = 2 THz
Ge checked Debye = approximately 7.8 THz
```

Require statistically indistinguishable QP results. If they differ, stop and
trace the active code path before ranking Ge. Do not substitute Si's 15 THz.

### Item 4 acceptance tests

Item 4 passes only when all of these tests pass:

- Si resolves to `G4_Si`, the Si lattice, and the effective Si density;
- Ge resolves to `G4_Ge`, the Ge lattice, and the effective Ge density;
- Ge sound speeds use Ge density and reconstruct the native values within the
  declared tolerance; the known Si-density result near 7966 m/s is rejected;
- generated Ge config retains Ge `dyn`, scattering, decay, DOS, and Debye
  fields rather than Si values;
- runtime output confirms the material/lattice identity and effective density;
- a missing candidate field or density mismatch fails before Geant4 runs;
- the complete resolved record and its provenance enter the trial cache key.

### Consequences for items 5 and 8

For item 5, reproducing 0.795/0.745/0.736 with a model calibrated to those same
numbers is a **baseline reconstruction test**, not independent physics
validation. Record these separately:

```text
baseline_reconstruction_passed
physics_validation_passed
```

Until independent interface data or a higher-fidelity calculation supports the
second flag, bracket plausible interface probabilities and require the
candidate ranking to remain stable.

For the 16/32/64 position-convergence study, use nested Sobol prefixes with one
seed, so the 16 sites are contained in 32 and the 32 in 64. Keep **events per
position and replicas fixed**, not total events per candidate; otherwise the
test confounds spatial convergence with increasing Monte Carlo noise. Compare
the spatial mean QPs per primary and require both objective and material-ranking
stability under a tolerance declared before viewing the results.

### Reference invocation

```bash
cd parameter_optimization
python stage3_trial_runner.py --contract stage3_config.yaml            # one trial
python stage3_trial_runner.py --contract stage3_config.yaml --replay   # exit check
python stage3_ledger.py stage3_trials.sqlite                           # status tally
```

The contract ships `max_workers: 32` deliberately -- that is the validated
level, and item 8 raises it to 64 only after peak RSS and failure behaviour are
checked.


## Item 4 + 5 implementation results (2026-08-13)

All six decisions implemented and verified against **real Geant4 runs**.

### 1. `G4_Ge` density — measured, not tabulated

Ran a live Geant4 job with `setSubstrateG4Name G4_Ge` and read the density back
from its own output: **5.323 g/cm3**. That value, not a catalog number, is what
the resolver derives speeds and impedances with, because
`G4LatticeManager::LoadLattice` calls `SetDensity(Mat->GetDensity())` -- the
`G4Material` *is* the density inside the phonon kinematics.

`_verify_runtime_material()` re-checks it from every sub-run's log and returns
`corrupt_or_incomplete` on a mismatch: a run that completed on a different
material than the one resolved is not a success, because the speeds, impedances
and interface values then describe something that was not simulated.

### 2. Complete native Ge configuration

`config_mode: native_g4cmp` copies the material's own G4CMP record verbatim.
Verified in a generated Ge trial -- every field is Ge's, none is Si's:

```text
cubic  5.658 Ang            dyn    -73.2 -70.8 37.6 56.1 GPa   (Si: -42.9 -94.5 52.4 68.0)
scat   3.67e-41 s3          decay  1.6456e-54 s4               (Si: 2.43e-42 / 7.41e-56)
LDOS   0.097834             STDOS  0.53539    FTDOS 0.36677    (Si: 0.093/0.531/0.376)
Debye  2 THz                                                    (Si: 15 THz)
vsound 5269.5 m/s           vtrans 3234.4 m/s   <- rewritten from Ge tensor + Ge density
```

Only `vsound`/`vtrans` are rewritten, so the scalars agree with the tensor and
density actually in use.

### 3. No Si `FIXED_CONFIG_COMMANDS` on the Stage 3 path

The Stage 3 lattice writer never applies them, and asserts that a native
non-Si config has not acquired Si's `Debye 15 THz`. The legacy overrides remain
only on the old Si sensitivity path.

### 4. Derived-speed consistency check

Declared tolerance **2%**, with the reasoning stated rather than hidden: the
convention spread between the spherical Christoffel average and the shipped
scalars is 0.19-0.56% (Si) and 0.75-1.03% (Ge), while the failure being guarded
against -- wrong density -- is ~50%. The tolerance separates the two by more
than an order of magnitude.

| Substrate | derived | native | deviation |
|---|---|---|---|
| Si | 9016.7 / 5369.5 | 9000 / 5400 | 0.19% / 0.56% |
| Ge | 5269.5 / 3234.4 | 5324.2 / 3258.8 | 1.03% / 0.75% |
| Ge with **Si** density (the bug) | 7966 / 4890 | 5324.2 / 3258.8 | **50.02% — rejected** |

### 5. Candidate-specific interfaces

`interface_transmission.py` implements the baseline-calibrated effective AMM.
Convention declared explicitly: substrate side uses the Debye-like mode average
`(v_L + 2 v_T)/3`, film side uses the scalar speed the macro already passes to
G4CMP, all SI units converted at the call site.

| | setTopAbs | setTopFilmAbs | setBotAbs |
|---|---|---|---|
| Si (calibration anchor) | 0.795000 | 0.745000 | 0.736000 |
| Ge | 0.725915 | 0.763178 | 0.766502 |

Changing the substrate recomputes **all three**, verified. Physically coherent:
Ge's acoustic impedance (2.08e7) sits close to Nb's and Cu's, so more phonons
transmit into those films and less into Al.

Two flags are reported separately, as required:
`baseline_reconstruction_passed = True` (max deviation 0.0) and
**`physics_validation_passed = False`** -- recovering constants the model was
calibrated to is a plumbing test, not physics. Ranking claims stay
model-dependent until independent interface data exists.

### 6. Debye A/B — resolved decisively

Ge at 4,000,000 events, 16 positions x 2 replicas, identical seeds and sites:

| Arm | total_QPs | per primary event |
|---|---:|---:|
| native `Debye 2 THz` | **144** | 3.600e-05 |
| checked `Debye 7.8 THz` | **144** | 3.600e-05 |

**Bit-identical, not merely statistically indistinguishable** (Poisson sigma
would have been 17.0). This confirms the hypothesis in the policy above: Debye
energy is consumed by `G4CMPEnergyPartition::GeneratePhonons()`, and the
`phonon_Caustic` gun creates its primary directly, bypassing energy
partitioning. The field is inactive for this objective -- now tested rather than
assumed. The two arms carry different cache keys, so they can never collide in
the ledger.

The 2 THz value stays as shipped. It is not accepted as physical ground truth,
but it provably cannot affect this objective, so it is not a blocker. **If the
gun type ever changes to one that deposits energy** (gamma, muon, eh_pair), this
result expires and Debye must be revisited.

### Item 4 acceptance tests — all pass

| # | Test | Result |
|---|---|---|
| 1 | Si -> `G4_Si`, Si lattice, effective Si density | PASS |
| 2 | Ge -> `G4_Ge`, Ge lattice, effective Ge density | PASS |
| 3 | Ge speeds use Ge density, reconstruct native within tolerance | PASS (1.03%) |
| 3b | The Si-density result near 7966 m/s is rejected | PASS (50.02% deviation) |
| 4 | Generated Ge config keeps Ge `dyn`/`scat`/`decay`/DOS/`Debye` | PASS |
| 5 | Runtime output confirms material identity and effective density | PASS |
| 6 | Missing field or density mismatch fails before Geant4 | PASS (both, by name) |
| 7 | Resolved record and provenance enter the cache key | PASS |

### Measured cost — the factorial is trivially affordable

4,000,000 events across 16 positions x 2 replicas completed in **4.1 s wall** on
32 workers (4m19s CPU). At F2 = 1e7 a candidate costs roughly 10 s, so the whole
eight-combination factorial with replicates is a couple of minutes of compute.
This settles the optimizer question: **enumerate, do not optimize.**

Yield measured at 3.600e-05 QPs per primary event, against the 3.69e-5 planning
figure used to size the fidelity ladder.

### What remains before optimization

1. **Second top film and second bottom film records** (gap > 0.5 meV for the top
   film, so `E_gun < 2*gap` holds). Ge/Si substrates are done.
2. **Item 6** — the curated catalog file, with provenance for every cryogenic
   value.
3. **Item 7** — one-factor pilots at F1.
4. **Item 8** — the 16/32/64 nested-Sobol position-convergence check, with
   events per position and replicas held fixed.
5. **Item 9** — injected-failure tests, then the eight-combination enumeration.

Items 1-5 of the go/no-go list are now satisfied for the Si/Ge substrate axis.


## First factorial result (2026-08-13)

**3 substrates x 3 top films x 2 bottom films = 18 combinations**, all evaluated
exhaustively at 4,000,000 events over 16 common injection sites x 2 replicas,
with a shared seed bank so every comparison is paired. Total compute ~12 min on
32 workers. Ledger: 18/18 `success`, 288 sub-runs, zero failures.

E_gun = 10 meV (muon-strike maximum). Baseline Si/Nb/Cu = 1578 QPs.

| rank | candidate | total_QPs | vs baseline | z |
|---:|---|---:|---:|---:|
| 1 | **GaAs/Nb/Cu** | 954 | -39.5% | -12.4 |
| 2 | **Ge/Nb/Cu** | 972 | -38.4% | -12.0 |
| 3 | Ge/Ti/Cu | 1142 | -27.6% | -8.4 |
| 4 | Ge/Ta/Cu | 1150 | -27.1% | -8.2 |
| 5 | GaAs/Ti/Cu | 1276 | -19.1% | -5.7 |
| 6 | GaAs/Ta/Cu | 1502 | -4.8% | -1.4 |
| 7 | Si/Nb/Cu (baseline) | 1578 | — | — |
| 8 | Si/Ti/Cu | 1608 | +1.9% | +0.5 |
| 9 | Si/Ta/Cu | 1800 | +14.1% | +3.8 |
| 10-18 | all `*/*/Au` | 1968-4160 | +25% to +164% | +6.5 to +34.1 |

### What the factorial says

1. **The bottom film dominates.** Every Cu combination beats every Au
   combination. Au roughly doubles QP damage. Two model inputs drive this and
   both point the same way: Au's longer phonon lifetime (16 ns vs 5.1 ns) keeps
   phonons alive in the film, and its lower interface absorption (0.49 vs 0.74
   on Si) means less energy is taken out of the substrate -- so more phonons
   survive to reach the junctions.
2. **Substrate is second.** GaAs and Ge are each ~39% better than Si and are
   statistically indistinguishable from each other (z = 0.4 between them).
3. **Top film matters least**, at least with a Cu bottom: Nb < Ti < Ta, spanning
   only ~14%.
4. Best (GaAs/Nb/Cu, 954) to worst (Si/Ta/Au, 4160) is a **4.4x** spread.

### Caveats that bound these numbers

- **Model-dependent.** The interface model's `physics_validation_passed` is
  still **False** -- it is calibrated to 0.795/0.745/0.736, not independently
  validated. The Au-vs-Cu gap is partly an interface-model result, so it is the
  conclusion most exposed to that model being wrong.
- **The supplied uncertainties have not yet been propagated.** Ta [0.015, 0.040],
  Au [10.0, 30.0], Ti [0.25, 0.70] ns. Section 5.4's ranking-stability test --
  rerun the factorial at the bracket ends and check whether the ranking survives
  -- is the immediate next step and is cheap (~25 min for both ends).
- One orientation (001, 45 deg), one energy, one scenario set. The 16/32/64
  position-convergence check has not been run.
- Two replicas only, so the z values use Poisson counting statistics rather than
  a measured replicate variance.

### Ti is a film here, not a substrate

Ti was requested as a substrate but has been added as a **top film**. It cannot
be a substrate in this model: there is no G4CMP lattice map for Ti (only Al2O3,
Al2O3_SULI, CaF2, CaWO4, GaAs, Ge, LiF, Si), and the substrate must be a
phonon-carrying crystal with a complete lattice record rather than a metal. The
supplied data itself labels Ti `model: superconducting`, which is a film
classification, so that is where it went. GaAs *was* added as a substrate --
Geant4 density measured at 5.310 g/cm3 from a live run, derived speeds agreeing
with its native record to 1.71%/0.99%.

## Go/no-go summary

It is safe to start optimization only when all nine checks pass. It is safe to
start **implementation now**, in this order:

```text
contract -> evaluator/ledger -> material resolver -> interface model
         -> tiny catalog -> pilots -> convergence -> eight-combination study
```

The first useful coding slice is the baseline-only evaluator plus ledger. The
next slice is items 4 and 5 with Si/Nb/Cu reconstruction tests. Do not add more
materials until those baseline tests are exact.

## Materials Project API references

- [Getting started with the Materials Project API](https://docs.materialsproject.org/downloading-data/using-the-api/getting-started)
- [Querying data, including explicit material-ID queries](https://docs.materialsproject.org/downloading-data/using-the-api/querying-data)
- [`SummaryRester.search` API reference](https://materialsproject.github.io/api/_autosummary/mp_api.client.routes.materials.summary.SummaryRester.html)
- [`MPRester` API reference](https://materialsproject.github.io/api/_autosummary/mp_api.client.mprester.MPRester.html)
