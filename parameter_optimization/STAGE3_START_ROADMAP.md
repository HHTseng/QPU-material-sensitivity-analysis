# Concrete Stage 3 Start Roadmap

> For the actionable nine-gate checklist tailored to an initial 2 x 2 x 2
> substrate/top-ground-film/bottom-film study, use
> [`STAGE3_SMALL_MATERIAL_START.md`](STAGE3_SMALL_MATERIAL_START.md). It expands
> material propagation and interface coupling in detail and supersedes any
> less-specific execution ordering below; this file remains the architectural
> roadmap.

## Decision

Proceed directly to constrained, multi-fidelity material optimization. Do not
run another Morris or global sensitivity analysis first. Keep
`stage2_compute_QPs_sensitivity_analysis.py` unchanged and outside the Stage 3
execution graph.

The Stage 3 graph is:

```text
material catalog
      |
      v
constraint/dependent-value resolver
      |
      v
single-trial Geant4/G4CMP runner
      |
      v
Stage 2 QP conversion -> total_QPs
      |
      v
trial ledger -> optimizer -> next candidates
```

## Milestone 0 — freeze the contract (one short working session)

Actions:

1. Tag or record the current Git commit and hashes of all five beamOn templates.
2. Copy the fixed settings from `sensitivity_params.py` into a new Stage 3
   schema; Stage 3 must not build a Morris problem or sample QP-ODE variables.
3. Choose one fixed injection scenario for the first plumbing test only:
   position, direction, gun type, energy, and event count. **This single site is
   for plumbing, not for selection** — see Milestone 5. Define the full
   injection-site set here too, so it is frozen before any candidate is scored.
4. Choose the final comparison objective:
   `total_QPs` summed over all 17 electrodes.
5. Decide whether the first campaign selects only the substrate or jointly
   selects substrate/top film/bottom film. Start substrate-only if film-property
   data are incomplete.

New file:

- `parameter_optimization/stage3_config.yaml` — fixed controls, allowed
  decisions, fidelity levels, scenario definitions, timeouts, and seeds.

Exit gate:

- Every field is classified as `fixed`, `decision`, or `derived`; none appears
  in two categories.
- The **excitation-threshold contract** holds (pipeline §3.1.1):

  ```text
  minEPhonons  <  2*setTopGap   <=   E_gun   <   2*setTopFilmGap
     38.2 µeV       382 µeV        1000 µeV       >= 1538 µeV
  ```

  "Not below `minEPhonons`" is **not** the gate and must not be used as one.
  `minEPhonons` is a numerical tracking cut, not a physical threshold; at
  38.2 µeV a 100 µeV primary passes that test and still yields exactly zero QPs
  forever, because no phonon breaks a pair below `2*setTopGap`. The upper link
  matters too: above `2*setTopFilmGap` the Nb ground plane also absorbs and
  `total_QPs` silently stops being purely junction QPs.

  Enforced by `assert_excitation_thresholds()` in `stage1_run_simulations.py`,
  once **per design point** — `setTopGap`/`setTopFilmGap` may be swept, so the
  thresholds move from sample to sample.

## Milestone 1 — close the physics gaps (blocking)

Actions:

1. Add candidate density and material identity to Geant4 material creation.
   Editing only a `Si/config.txt` tensor while retaining `G4_Si` density is not
   a valid non-Si material simulation.
2. ~~Add an explicit CLHEP seed accepted by the executable~~ — **DONE, no C++
   needed.** `Main.cc:41` seeds from `clock()` *before* the UI manager executes
   the macro, so `/random/setSeeds` in the macro overrides it. Shipped in
   `stage1_run_simulations.py` as `SENSITIVITY_EXPLICIT_SEEDS`, keyed by
   `(trajectory, replica, position)` and written immediately before
   `/run/beamOn` (anything in between consumes draws and shifts the stream).
3. ~~Add an unambiguous successful-completion marker~~ — **DONE.** Every macro
   writes `/control/shell touch <hits_file>.done` on the line after
   `/run/beamOn`, verified at generation time and re-checked by stage 2. Exit 0
   without the marker is a hard `MacroAborted`; the known teardown SIGSEGV is
   accepted only when the marker proves the event loop finished.
4. Add the missing acoustic-interface transmission calculation for
   `setTopAbs`, `setTopFilmAbs`, and `setBotAbs`.
5. Validate the transmission calculation reproduces the Si baselines `0.795`,
   `0.745`, and `0.736` within a declared tolerance.
6. Verify `SoundfromTensor.py`/the derived-speed implementation using Si:
   approximately `vsound=9018.61 m/s`, `vtrans=5370.66 m/s` for the current
   tensor and density convention.

New or changed components:

- Geant4 detector/material code: candidate material and density support.
- Geant4 executable: completion status only — deterministic seeding is done.
- `parameter_optimization/interface_transmission.py` — supplied/validated
  acoustic transmission calculation.
- `parameter_optimization/material_resolver.py` — validates a candidate and
  calculates all dependent values.

Exit gate:

- Two runs with identical configuration and seed produce identical QP totals.
- A different seed produces a valid independent replicate.
- The complete Si record reconstructs the baseline macro/config.

Do not begin optimization before this milestone passes.

## Milestone 2 — build and freeze the candidate catalog

Actions:

1. Correct `ElasticityTensors.py` so it exports density, tensor convention,
   units, database version, retrieval timestamp, filtering criteria, and full
   candidate provenance.
2. Separate explicit material-ID queries from database-wide filtered searches.
3. Filter out candidates with missing required properties; never fill them with
   arbitrary `0.5x–1.5x` intervals.
4. Apply hard constraints:
   cubic symmetry, positive density, `C11-C12>0`, `C11+2*C12>0`, `C44>0`,
   positive Christoffel modes, `0<vtrans<vsound`, transmission coefficients in
   `[0,1]`, and the agreed Debye requirement.
5. Store one immutable catalog snapshot with a checksum.

Outputs:

- `parameter_optimization/catalog/material_candidates.csv`
- `parameter_optimization/catalog/material_candidates.json`
- `parameter_optimization/catalog/catalog_manifest.json`

Exit gate:

- Every accepted row can be converted into a complete macro/config without
  defaults borrowed silently from another material.
- Every rejected row records a machine-readable reason.

## Milestone 3 — implement one restartable trial evaluator

Create `parameter_optimization/stage3_trial_runner.py` with this interface:

```python
evaluate(configuration, beam_on, scenario_id, seed) -> TrialResult
```

The evaluator must:

1. validate and resolve the configuration;
2. generate a unique macro and lattice/material configuration;
3. run Geant4/G4CMP with the existing process-group timeout behavior;
4. verify completion and hits integrity;
5. reuse the QP calculation in `stage2_compute_QPs.py`;
6. return `total_QPs`, per-electrode QPs, QPs/event, runtime, and status;
7. cache exact repeats by a canonical configuration/fidelity/scenario/seed hash;
8. append the result atomically to SQLite.

Create `parameter_optimization/stage3_objective.py` as a small adapter that
reads `total_QPs` from the trial result or `qp_summary.csv`. Do **not** duplicate
the Stage 2b sensitivity script: none of its correlation/chi-squared machinery
is needed to calculate the objective.

Outputs:

- `parameter_optimization/stage3_trials.sqlite`
- `parameter_optimization/runs/<campaign>/<trial>/...`

Statuses must distinguish:

- `success_nonzero`
- `success_zero`
- `timeout`
- `constraint_rejected`
- `simulation_failed`
- `teardown_sigsegv_complete`
- `corrupt_or_incomplete`

Exit gate:

- Resume/retry never duplicates a completed observation.
- Missing and failed outputs can never become zero-QP observations.
- `total_QPs` equals the sum of all 17 per-electrode totals.

## Milestone 4 — calibrate noise and fidelity before optimization

Use Si plus 5–10 physically diverse candidates. Use common random numbers:
the same seed set **and the same injection-site set** for every candidate at a
given fidelity.

Measure noise on the **full injection-site set**, not one site. The variance
structure differs: site-to-site spread was measured at 9.18x (CV 60.6%) with the
material held fixed, so a replicate policy calibrated on one site does not
transfer to 16 and would under-budget the campaign.

Initial matrix:

| Fidelity | Events (TOTAL per candidate) | Replicates | Expected `total_QPs` | Purpose |
|---|---:|---:|---:|---|
| ~~F0~~ | ~~`1e4`~~ | — | ~0.4 | **dropped** — no ranking power, do not spend the pilot on it |
| ~~F1~~ | ~~`1e5`~~ | — | ~4 | **dropped** — ~50% Poisson SE per evaluation |
| **F1** | **`1e6`** | 5 | ~37 | screening |
| **F2** | **`1e7`** | 5–10 | ~370 | selection and confirmation |

Derived from the measured yield of **3.69e-5 QPs per primary event** under the
corrected energy protocol (pipeline §12.5). The old `1e4/1e5/1e6` ladder was
guessed before that number existed and is two levels too low: at 1e5 events the
objective is a Poisson count with mean ≈ 4, which cannot rank candidates whose
differences are tens of percent. Events are the **total per candidate**, split
across `N_POSITIONS × N_REPLICAS` sub-runs by `SENSITIVITY_TOTAL_EVENTS`.

Measure:

- mean/median/variance and zero fraction of `total_QPs`;
- QPs per primary event;
- paired candidate-minus-Si differences by seed;
- Spearman rank correlation between F1 and F2;
- wall time, failures, and timeouts.

Decision rule:

- Confirm the yield assumption (3.69e-5 QPs/event) on this configuration before
  trusting the table; re-derive the ladder if it differs materially.
- Use F1 for screening only if its ranking is predictive of F2.
- Increase replicates when stochastic uncertainty is comparable to candidate
  differences.

Exit gate:

- A written fidelity/noise report establishes the lowest trustworthy budget and
  replicate policy.

## Milestone 5 — run optimization with baselines

Recommended first implementation:

1. Generate a balanced categorical/Sobol initial design over feasible material
   records and orientation choices.
2. Run random search as the mandatory baseline.
3. Run model-free Hyperband/Successive Halving as the fidelity baseline.
4. Run SMAC `MultiFidelityFacade` with a random-forest surrogate for the mixed
   categorical/conditional space.
5. Dispatch concurrent sub-runs at the runner's default of **64 workers** with
   a **200 GB** aggregate memory budget (both are now the defaults on Linux —
   no need to pass them). Four was a legacy constraint from the `QPLim=1`
   runaway, which `QPLim=3` plus `SENSITIVITY_SAMPLE_TIMEOUT` and
   `sensitivity_memguard.py` now neutralise. **32** is the level validated
   end-to-end (221,184 sub-runs, 0 failures, 2.15 GB peak RSS); 64 is double
   that and not yet measured at scale, so check the "Peak tracked RSS" line the
   runner prints on the first long run before trusting it for a multi-hour
   campaign.
6. Optimize a noise-aware score such as mean QPs/event plus one standard error;
   retain all raw counts.
7. **Score every candidate on the full injection-site set, identical across
   candidates** (`SENSITIVITY_N_POSITIONS=16`, `SENSITIVITY_N_REPLICAS=2`,
   `SENSITIVITY_TOTAL_EVENTS` split across them). This is a *selection*
   requirement, not a confirmation one, and it is the single most important
   line in this milestone:

   - Injection site moves the objective by **9.18x (CV 60.6%)** with the
     material held fixed — the same order as the candidate effects being
     resolved (smallest resolvable effect 53%).
   - The elastic tensor and orientation **steer the phonon caustic**, and both
     are decision variables. A single-site objective can therefore be improved
     by moving focusing away from that one site without reducing device-wide QP
     damage. Morris merely absorbed this as variance because screening is
     passive; an optimizer will actively find and exploit it.
   - Selecting on one site and confirming on 16 guarantees the confirmation
     disagrees with the selection, and the campaign has to be re-run.

   Sites must be **identical across candidates** (a common random number), so
   site variance cancels in candidate-minus-candidate differences instead of
   inflating them.

New file:

- `parameter_optimization/stage3_optimize_materials.py` — ask/tell optimizer,
  asynchronous dispatch, ledger integration, stopping rules, and restart.

Suggested pilot budget after Milestone 4:

- 20–40 initial feasible configurations at the selected screening fidelity;
- 50–100 sequential/promoted evaluations;
- promote the top 10 to F2;
- do not use F3/`1e8` until evidence shows F2 is insufficient.

Stopping rules:

- fixed total event or CPU budget;
- no practically meaningful improvement for 10–20 completed batches;
- stable top-candidate set after uncertainty-aware reranking.

Exit gate:

- SMAC beats or matches random search and Hyperband at equal cumulative event
  cost; otherwise retain the simpler method.

## Milestone 6 — held-out confirmation

Actions:

1. Select the top 3–10 candidates without looking at confirmation seeds.
2. Run each at F2 or the declared target fidelity using at least 10 new seeds.
3. Evaluate the **same** full injection-site set used for selection (Milestone
   5 item 7) — confirmation adds held-out *seeds* and higher fidelity, it does
   not introduce the scenario set for the first time. Changing the objective
   between selection and confirmation would invalidate the comparison.
4. Compare each candidate to Si with paired-seed bootstrap confidence intervals.
5. Archive catalog, configuration, database, code/template hashes, generated
   macros/configs, logs, and optimizer state.

Final report:

- raw and normalized QP counts;
- uncertainty and paired improvement relative to Si;
- failure/rejection rates;
- objective-versus-cumulative-cost curves for every optimizer;
- clear scope: lower simulated QP generation, not yet lower PLE.

## Optional work after Stage 3

Only after a successful optimization campaign, create a new post-hoc analysis
script if parameter importance or fabrication tolerance is scientifically
useful. Possible methods include permutation importance on the optimizer's
surrogate, local ablation around finalists, or a constrained Sobol study over a
small realizable neighborhood. None is required to start, select, or confirm
Stage 3 candidates, and none should modify the previous Stage 2b script.
