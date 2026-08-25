# Stage 4 implementation audit and prioritized fix plan

Date: 2026-08-24  
Branch reviewed: `Material_optimization_v3_scan_parameters`  
Scope: Stage 3 real-material enumeration, Stage 4 property-space search,
high-fidelity confirmation, and nearest-real-material projection.

## Executive verdict

The evaluator, ledger, physical gates, block-resolved objective, and
high-fidelity confirmation machinery are generally well designed. The strongest
result that the current artifacts support is:

> Under the frozen G4CMP model, calibrated interface model, fixed 16-site
> scenario set, and junction-only QP objective, the search found a
> pseudo-material property vector whose confirmed yield is about 70% below the
> `Si/Nb/Cu`-equivalent baseline.

Three stronger claims are not currently supported:

1. The point is not a converged property optimum.
2. The campaign does not demonstrate that GP Bayesian optimization outperformed
   random/Sobol search or CMA-ES.
3. The real-material verification is invalid because the intended substrate
   carrier and density were dropped before simulation. In particular, the claim
   that SiC recovers 77% of the ideal gain must be withdrawn until it is rerun.

The issues below are ordered by the sequence in which they should be fixed. Do
not spend more simulation budget on material projection or optimizer comparison
until P0 and P1 are closed.

## Priority summary

| Priority | Issue | Impact on current claims | Recommended disposition |
|---|---|---|---|
| **P0** | Projected substrate carrier is lost before evaluation | Invalidates all projected/real-substrate verification values, including SiC 77% | Fix propagation and native-material routing; invalidate and rerun projection results |
| **P1** | BO/CMA campaigns do not actually test the advertised algorithms deeply | Invalidates the optimizer-efficiency ranking, but not the measured property vectors | Record proposal provenance; fix async logic; rerun with adequate generations/acquisitions |
| **P2** | Search is not converged and binds against the box | “Optimum” is an overclaim; high-fidelity value of the selected point remains valid | Rename it “best point found,” widen/refine the space, and require local convergence |
| **P3** | Objective counts junction QPs only | A low-gap ground plane may look good while creating unpenalized film QPs/loss | Make the engineering goal explicit and add loss/QP constraints or objectives |
| **P4** | Projection distance compares incomplete feature vectors | Be3N2/SiC/BP are only nearest in the measured elasticity subspace | Stratify by coverage and model missing transport properties as uncertainty |
| **P5** | Baseline drift controls are cache hits | Invalidates the “0% drift across six re-evaluations” validation claim | Add independently recorded forced controls or a dedicated control table |
| **P6** | Spatial and model-systematic convergence is unfinished | Limits fabrication/generalization claims, not fixed-site paired comparisons | Run 16/32/64 sites, lifetime brackets, and an alternate interface treatment |
| **P7** | Documentation and ledger status are inconsistent | Creates reproducibility and status ambiguity | Synchronize energy protocol, current headline, and stale running/incomplete rows |

## P0 — Repair real-material propagation before any projection rerun

### Finding

`stage4_project_material.py` adds `point["substrate_carrier"]` in both the
elasticity-variant and realizable-material paths. That field is then discarded:

- `stage4_optimize.candidate_payload()` calls `space.complete(point)` and emits
  only variables in `space.all_variables`, `miller`, and `_space`.
- `stage4_confirm.collect_points()` also calls `space.complete()` on point files,
  dropping any extra carrier metadata.
- `stage4_space.resolve()` consequently defaults to `G4_Si`.

The stored run proves the defect reached production. The generated SiC macro
contains:

```text
/main/detector_param/setSubstrateG4Name G4_Si
/main/detector_param/setSubstrateName PseudoCubic
```

The ledger likewise records no `substrate_carrier`, derives `G4_Si`, and uses
2330 kg/m3 for the projection and confirmation rows inspected. The report says
the SiC elasticity variant should use `G4_CALCIUM_FLUORIDE` as a 3180 kg/m3
density carrier, but the generated run did not.

There is a second semantic problem: the Stage 4 resolver always starts from the
Si lattice record. Even after fixing density propagation, its unlisted `dyn`,
LDOS/STDOS/FTDOS, Debye, and charge-carrier fields remain Si's. That is acceptable
for a clearly labeled pseudo-material sensitivity calculation, but not for a
claim that a complete real G4CMP material was re-simulated with its own record.

### Best recommended fix

Separate **physics decision variables** from **material realization metadata**
instead of relying on arbitrary extra dictionary fields.

1. Define a versioned candidate envelope, for example:

   ```json
   {
     "space_id": "stage4_property_v1",
     "properties": {"sub_c11": 384.0},
     "orientation": {"miller": [2, 2, 3]},
     "realization": {
       "mode": "pseudo_si_base",
       "substrate_carrier": "G4_CALCIUM_FLUORIDE",
       "base_lattice_map": "Si"
     }
   }
   ```

2. Include the entire realization block in the ledger payload and cache key.
3. Make `candidate_payload()` preserve and validate this block explicitly.
4. Make `stage4_confirm.collect_points()` preserve it rather than normalizing it
   away.
5. Route candidates by realization mode:

   - `native_g4cmp`: use the Stage 3 material resolver and the material's complete
     native lattice record.
   - `pseudo_si_base`: use Si's unlisted lattice fields and label the result a
     pseudo-material or elasticity surrogate.
   - `custom_material`: require an explicitly registered Geant4 material and a
     complete lattice record; refuse simulation if either is absent.

6. Do not represent arbitrary real densities using nearby, compositionally
   unrelated NIST materials for a final material claim. Add a small C++ material
   registry or macro command that creates a material at the requested density.
   Until that exists, report the density-carrier approximation prominently.

### Required regression tests

- Construct the SiC elasticity variant and assert that `substrate_carrier`
  survives projection -> points JSON -> confirmation -> ledger.
- Generate one macro and assert its `setSubstrateG4Name` equals the requested
  carrier rather than `G4_Si`.
- Inspect the runtime material log and assert name and density match the resolved
  snapshot within tolerance.
- Assert that changing only the carrier changes the cache key.
- For a native material such as Ge or GaAs, assert that the generated lattice
  config comes from its native record and retains its own `dyn`, DOS, and Debye.
- Add a negative test proving a requested real material with incomplete density
  or lattice data is refused before launching Geant4.

### Data disposition

Mark the following as invalid for material inference, while retaining them as
historical debugging artifacts:

- `elasticity_of_SiC` at every fidelity;
- `elasticity_of_Be2C` at every fidelity;
- Stage 4 projected `Si/Nb/Cu` and `GaAs/Nb/Cu` verification rows;
- the derived “fraction of ideal gain realized,” especially the SiC 77% claim.

The ideal pseudo-property vector and its baseline comparison are not invalidated
by this defect because both intentionally use the Stage 4 pseudo-material path.

## P1 — Make optimizer labels match the proposals actually evaluated

### Finding: BO campaign

The BO manifest records 23 observations, `n_init = 20`, and only one GP fit. The
ledger contains only one trial with a non-null acquisition value. The winning
point was proposed before the GP phase and therefore came from the Sobol initial
design. The current statement that BO found the winner faster than the other
algorithms attributes a Sobol result to GP Expected Improvement.

The asynchronous initialization test uses only `n_observations`; it does not
count pending initial-design points. Variable trial runtime can therefore cause
more than `n_init` Sobol points to be dispatched before 20 have completed.

### Finding: CMA-ES campaign

The CMA manifest records one completed generation, population 12, and zero
restarts. When a generation is full but still waiting on a slow member, `_ask()`
returns a random feasible point. That point is stored and reported as a CMA-ES
observation even though it does not participate in the generation update.

The current run therefore does not test multiple-generation CMA adaptation or
IPOP behavior, and its trial-count trajectory mixes CMA proposals with filler
random proposals.

### Best recommended fix

1. Add immutable per-trial fields:

   - `proposal_source`: `sobol_init`, `gp_ei`, `cma_generation`,
     `random_filler`, `random_baseline`, `llm`, or `fallback`;
   - optimizer generation/model-fit number;
   - acquisition value where applicable;
   - whether the point participated in an optimizer update.

2. For BO, use `n_observations + n_pending_initial` when enforcing `n_init`.
   After the initial design is dispatched, wait until enough results exist to fit
   the first GP, then use fantasies for pending GP proposals.
3. For CMA-ES, prefer synchronous population batches because simulation runtime
   is highly variable and the budget is small. If keeping asynchronous execution,
   implement a documented asynchronous CMA variant. Do not label unrelated
   random filler as CMA-ES.
4. Preserve and report partial-candidate cost. Fifteen incomplete search rows are
   present in the ledger, although the final manifests report zero failures.
5. Compare algorithms over repeated optimizer seeds, not a single trajectory.

### Recommended rerun design

- Use the same frozen physics contract and screening fidelity for all methods.
- Give each method the same **paid event budget**, including partial failed work.
- BO: at least 24-32 initial space-filling points followed by 50 or more actual
  GP-guided acquisitions.
- CMA-ES: at least 8-10 complete generations before judging it; do not claim IPOP
  unless a restart actually occurs.
- Random/Sobol: retain both as separate mandatory baselines.
- Run at least three optimizer seeds when making an algorithm-performance claim.
- Promote each method's best candidates to held-out high fidelity as already done.

### Acceptance criteria

- The report can state exactly how many trials came from each proposal source.
- BO efficiency is measured only after the GP phase begins.
- CMA efficiency includes only points used by the CMA update, while filler cost is
  still charged to the campaign.
- Algorithm rankings are stable enough across optimizer seeds to report a median
  and range, rather than one lucky trajectory.

## P2 — Continue the search before calling the point an optimum

### Finding

The high-fidelity ladder shows that the objective value of the selected point is
stable with event count. It does not show that the selected coordinates minimize
the objective.

The local tolerance scan found six single-variable moves better than the centre,
including approximately `sub_c11` down by 36%, `bot_gap_thres` down by 22%, and
`topfilm_gap` down by 20%. Important variables also approach or hit the current
box limits. The campaigns stopped on wall-clock, not a convergence criterion.

The tolerance scan itself should be treated as exploratory: it compares noisy
single evaluations and thresholds primarily against the centre error rather than
the paired uncertainty of each difference.

### Best recommended fix

1. Rename the current result everywhere to **best confirmed property vector
   found**.
2. Use the current point as the centre of a smaller trust region.
3. Widen only the physically defensible binding directions, especially
   `topfilm_gap`, the substrate elastic constants, and `sub_scat`; document the
   new evidence-based limits.
4. Re-evaluate promising OFAT directions with paired block differences and a
   sufficient event count before using them to move the centre.
5. Run local BO/CMA or a derivative-free trust-region method until all feasible
   one-dimensional and sampled multivariate moves are smaller than a declared
   practical threshold.
6. Confirm the new incumbent on a held-out seed bank and at the converged event
   tier.

### Suggested stopping rule

Call a point a **locally converged optimum within the declared box** only when:

- no tested trust-region move improves the paired objective by more than 2%;
- the improvement is also below two paired standard errors;
- no active bound is being approached without a documented physical reason;
- two successive model updates fail to improve the held-out incumbent materially;
- the result is stable across at least two optimizer seeds.

This does not prove a global optimum, but it supports a precise local claim.

## P3 — Align the objective with the fabrication goal

### Finding

`total_qps_per_primary` counts QPs generated at the 17 Al junction electrodes.
It does not count ground-plane QPs, bottom-film QPs, microwave loss, thermal-QP
density, or logical errors.

The strongest current mechanism is a very low top-film gap, which makes the
ground plane an effective phonon sink. That can reduce junction QPs while
increasing QPs or microwave loss in the ground plane. The optimizer is not wrong;
it is solving the objective it was given. The fabrication interpretation is
incomplete.

### Best recommended fix

Keep junction QPs as the primary objective, but add explicit engineering
constraints or a Pareto analysis:

- junction QPs per primary;
- maximum single-junction QP load;
- ground-plane absorbed energy/QPs;
- estimated thermal-QP density at operating temperature;
- measured or modeled microwave surface loss;
- fabrication feasibility and material compatibility.

At minimum, impose a lower acceptable `T_c`/gap or microwave-loss constraint on
the top film. Report the low-gap target as a phonon-sink design hypothesis until
that tradeoff is evaluated.

### Acceptance criteria

- The report states “junction QPs,” not unqualified “QP generation.”
- A candidate cannot win solely by moving damage into an unobserved film.
- Finalists are presented as a Pareto set if no single design dominates all
  engineering objectives.

## P4 — Redesign the real-material distance metric for missing data

### Finding

The distance function skips missing features and divides by the sum of matched
weights. The nearest Materials Project substrates match only four of seven
features; `scat`, `decay`, and `decayTT` are missing. A 4/7 and a 7/7 distance are
therefore placed in one ranking even though they answer different questions.

This matters because the omitted phonon constants include strong optimization
directions. Uniform weights were also used even though the local sensitivity scan
indicates strongly nonuniform relevance.

### Best recommended fix

Use a staged projection rather than one scalar ranking:

1. **Coverage strata:** rank 7/7 candidates separately from 4/7 candidates.
2. **Measured-feature distance:** explicitly call the MP result an
   elasticity/impedance ranking.
3. **Missing-property uncertainty:** assign physically justified ranges or
   priors to `scat`, `decay`, and `decayTT`, propagate them through the simulator,
   and report expected/worst-case performance.
4. **Sensitivity weighting:** use confirmed local or global sensitivities, not a
   noisy single OFAT scan, to weight features.
5. **Joint triplet evaluation:** shortlist substrate/top/bottom combinations but
   rank actual QP performance only after simulation, because the interfaces make
   the layers nonseparable.

### Acceptance criteria

- No table directly compares distances with different feature coverage without a
  visible penalty or separate stratum.
- “Nearest material” always names the subspace and data coverage.
- A fabrication recommendation includes uncertainty from missing phonon
  constants rather than silently substituting the pseudo target.

## P5 — Make baseline controls real re-evaluations

### Finding

`baseline_control()` calls the normal evaluator without `force=True`. The cache
therefore returns the original successful row. The repeated value and zero spread
measure cache stability, not machine, executable, or runtime drift.

Using the existing forced-rerun implementation alone is also insufficient for an
audit trail because a force rerun updates the same row in place instead of
retaining a sequence of control results.

### Best recommended fix

Add a dedicated `controls` table or give each control a unique, non-physics
`control_replica_id` that is excluded from candidate identity but included in the
run record. Each control should:

- execute fresh rather than return from cache;
- use a declared fixed seed bank when detecting executable drift;
- retain every historical result rather than overwrite one row;
- store code fingerprint, host, worker shape, timestamp, and runtime;
- be excluded from optimizer observations and event-efficiency curves.

If exact replay remains unavailable, define drift using agreement within the
measured stochastic repeat distribution rather than bit equality.

### Acceptance criteria

- Logs distinguish `fresh control` from `cache hit`.
- Six controls create six independently inspectable run records.
- The drift report is computed from those records and never from duplicated
  cached values.

## P6 — Close spatial, interface, and lifetime systematics

### Finding

The 16-site set is fixed and under-resolved: one site contributed about half of
the Stage 3 baseline yield. The same sites help paired comparisons, but increasing
events per site cannot establish convergence over injection position.

The effective acoustic-mismatch interface model is calibrated to reproduce the
baseline constants but is not independently validated. Film lifetimes, especially
for some nonbaseline films, also remain uncertain. These systematic terms are
larger risks to fabrication inference than another 10x increase in events on the
same sites.

### Best recommended fix

1. Run nested 16/32/64 Sobol positions for the baseline, Stage 3 real winner, the
   current best property point, and eventual projected finalists.
2. Require stable objective and ranking under 16 -> 32 -> 64 sites.
3. Run low/nominal/high lifetime brackets using corrected cache identity.
4. Compare the calibrated effective AMM with at least one defensible alternate
   interface treatment or measured interface dataset.
5. Separate stochastic replica error, spatial quadrature variation, interface
   model uncertainty, and material-property uncertainty in the report.

### Acceptance criteria

- Absolute yield and ordering stabilize under the nested site sequence.
- A material recommendation is robust across declared lifetime and interface
  variants, or its dependency is stated explicitly.
- More event budget is not called “converged” unless both event-count and spatial
  convergence have passed.

## P7 — Synchronize documentation and run status

### Finding

- `parameter_set.txt` still documents a fixed gun energy of 1 meV, while the
  actual Stage 3/4 contract and macro templates use 10 meV.
- Some summaries retain the 4M held-out headline of -67.1%, while the latest
  valid high-fidelity pseudo-property result is about -69.9% at 1e7 events per
  sub-run and -69.7% at 1e8.
- The random-search XL row remains `running` in SQLite, but no matching process
  was active at the time of this audit. Treat it as stale/incomplete until it is
  explicitly resumed or closed.
- Search manifests report zero failures, while the ledger contains incomplete
  and operator-stopped candidate rows whose partial computation cost is omitted
  from trial-count efficiency plots.

### Best recommended fix

Choose one machine-readable contract as authoritative, generate the human-facing
parameter summary from it, and add an audit command that checks:

- energy/cutoff agreement across contract and templates;
- campaign status against live process and completion markers;
- manifest counts against ledger success/incomplete/running rows;
- headline values against the latest designated result files;
- whether a result has been invalidated by a known audit finding.

## Correctly implemented parts to preserve

The fix work should not discard the following strengths:

- Stage 3 treats real materials as linked property bundles and exhaustively
  evaluates the complete 18-triplet space.
- The Stage 3 native lattice path preserves the material's complete G4CMP record.
- Failed, corrupt, missing, or incomplete scenario sets cannot be scored as zero.
- Candidate, contract, code, template, and scenario identities are recorded in a
  restartable ledger.
- The objective is block-resolved by position and replica and avoids naive
  Poisson uncertainty for the main Stage 4 reporting.
- Physical gates reject unstable tensors and invalid interface/regime values
  before starting expensive simulations.
- Held-out seed confirmation and the event-fidelity ladder are appropriate and
  show that the selected pseudo-property vector's yield is stable.
- The Stage 4 non-slow exit-gate suite passed 39/39 in the `G4CMP` environment at
  the time of this audit. Its main gap is end-to-end verification of projected
  material identity.

## Current experiment interpretation

### Stage 3 — real material triplets

- Screening: 18 triplets at 4.0e6 events per candidate. `GaAs/Nb/Cu` appeared
  first at 2.385e-4 QPs/primary, narrowly ahead of `Ge/Nb/Cu` at 2.430e-4.
- Converged event tier: all 18 at 3.2e8 events per candidate, with six finalists
  at 3.2e9. The top order flipped and stabilized: `Ge/Nb/Cu` is best at about
  2.494e-4, roughly 36% below `Si/Nb/Cu`.
- The strongest robust factor is the bottom-film bundle: every Cu-bottom
  candidate beats its matched Au-bottom candidate under this model.
- This remains conditional on the 16-site quadrature, calibrated interface model,
  and unpropagated lifetime uncertainty.

### Stage 4 — pseudo-property search

- Screening: 92 scored property vectors at 4.0e6 events each: 23 in the BO
  campaign, 32 in CMA-ES, and 37 in random search.
- Best screening point: 1.150e-4 QPs/primary. It came from the BO campaign's Sobol
  initialization, not from a GP acquisition.
- Held-out 3.2e8-event confirmation: 1.158e-4 versus baseline 3.843e-4, a 69.9%
  reduction. The random winner measured about 64.6% below baseline and the CMA
  winner about 62.7% below baseline.
- At 3.2e9 events, the best property point is 1.176e-4 versus baseline 3.886e-4,
  a 69.7% reduction. This confirms the point's value, not search convergence.
- The LLM proposer was implemented and guardrail-tested but was not used in a
  physics optimization campaign.
- Real-material projection values are not scientifically usable until P0 is
  fixed and the affected candidates are rerun.

## Recommended execution order

1. Freeze and archive the current artifacts; mark projection-derived material
   claims invalid without deleting their debugging evidence.
2. Implement P0 candidate realization and end-to-end material identity tests.
3. Rerun one cheap SiC/GaAs propagation smoke test and inspect macro, runtime
   density, lattice source, ledger payload, and cache key manually.
4. Implement P1 proposal-source accounting and asynchronous optimizer fixes.
5. Address P2 by widening/refining the search around the best confirmed vector.
6. Define the P3 engineering objective/constraints before selecting a ground-film
   material.
7. Rebuild the P4 material shortlist with coverage-aware uncertainty.
8. Run corrected real-material verification on held-out seeds, then promote only
   surviving candidates to high fidelity.
9. Complete P5/P6 drift and systematic convergence before fabrication selection.
10. Synchronize documentation and ledger state under P7 and issue a revised
    results summary with explicit validity labels.

## Final claim language after this audit

Use:

> The Stage 4 campaign found and high-fidelity-confirmed a pseudo-material
> property vector that reduces simulated junction QP yield by about 70% relative
> to the Si/Nb/Cu-equivalent baseline under the current fixed model and 16-site
> scenario set. The search and real-material projection are not yet converged or
> validated for fabrication.

Do not currently use:

- “global property optimum”;
- “GP Bayesian optimization was the best algorithm”;
- “SiC recovers 77% of the gain”;
- “nearest real material” without naming the matched feature subspace;
- “total QP generation” when the measured quantity is junction QP yield;
- “six fresh baseline re-evaluations showed zero drift.”
