# Independent audit of Stage 1–3 QP sensitivity results

Date: 2026-07-28

This document audits the updated simulation pipeline described in
[`README.md`](README.md) and the conclusions reported in
[`RESULTS_stage1_to_stage3.md`](RESULTS_stage1_to_stage3.md). The checks used
the stage-1 through stage-3 source code, the complete Morris design and QP
summary, the 200-point noise-floor run, and selected raw hits files.

The two principal result directories are:

- full Morris screen:
  `results/morris_mimir_c7a18a17-91ce-491b-8124-78a4ff16576a`;
- noise-floor run:
  `results/morris_mimir_f52fb742-8b08-4ecf-9e46-d869a77750cd`.

No simulation results were modified during this audit.

## Executive assessment

The new screen is much more credible than the previous low-statistics run.
The main qualitative physics conclusions are reasonable:

- backside phonon absorption and Cu-island coverage strongly suppress QP
  generation;
- the Al junction gap, absorption, thickness, sound speed, and phonon
  lifetime strongly affect QP generation;
- larger junction collection area produces more junction QPs;
- the sub-meV source cannot activate the Nb top-film pair-breaking channel;
- source energy is influential but is an environmental variable rather than
  a material design variable.

The signs of the elementary effects agree with the relevant film-absorption,
pair-breaking, and backside-down-conversion mechanisms. The leading ranking
also remains stable when the two independent sets of 64 Morris trajectories
are analyzed separately.

Nevertheless, the following conclusions in the current results report should
be corrected before beginning material optimization:

1. `clock()` seeding is not safe: exact repeated nonempty hits files prove
   that random streams are being reused.
2. The six dummy variables are useful, but the current threshold ignores
   uncertainty in the maximum dummy response and slightly overstates the
   number of discoveries.
3. `setIsland` and `setIslandSpacing` are backside-Cu pattern variables, not
   qubit dimensions; they are primary mitigation controls.
4. The measured Fano factor is configuration dependent and should not be
   fixed at 2.27 throughout a Bayesian optimizer.
5. The proposed ten continuous optimization coordinates do not form a
   physically coherent material space.
6. Sixteen fixed source positions reduce one source of variation but do not
   demonstrate convergence of the spatial average.

The existing run is suitable as a strong qualitative screening experiment.
It is not yet a quantitatively reproducible optimizer training set.

## 1. Pipeline and accounting checks

The stored data reproduce the reported accounting:

| Quantity | Independently checked value |
|---|---:|
| Morris variables | 53 |
| Physical/non-dummy variables | 47 |
| Morris trajectories | 128 |
| Design points | 6,912 |
| QP-summary rows | 13,824 |
| Replicas per design point | exactly 2 |
| Events per replica | 2,000,000 |
| Effective events per design-point mean | 4,000,000 |
| Missing QP yields | 0 |
| Aggregated design points with zero QPs | 8 of 6,912 |
| Points with only one zero replica | 41 |
| Points with both replicas positive | 6,863 |

The stage-1 manifest pooling and stage-2 normalization are internally
consistent. Averaging two independent 2-million-event yields is statistically
equivalent to a 4-million-event evaluation when the simulations are genuinely
independent.

The old structural-zero problem is also fixed:

```math
E_{\mathrm{gun,min}}=0.600\ \mathrm{meV}
>
2\Delta_{\mathrm{Al,max}}=0.573\ \mathrm{meV}.
```

Thus every source energy in the new Morris box can break pairs for every
sampled Al gap. The remaining zero outcomes occur in highly suppressing
configurations or by finite-count randomness, rather than because the primary
phonon is killed below the Al pair-breaking threshold.

## 2. Source energy and tracking cutoff

### 2.1 The range 0.6–1.5 meV successfully probes Al QP generation

The new run gives direct empirical evidence that the range is active:

- `gun/setEnergy` has `mu* = 0.734`;
- its mean signed elementary effect is positive;
- 93% of its elementary effects are positive;
- nearly all design points generate nonzero QPs.

Consequently, this is a useful monoenergetic Green's-function range for
studying the present Al junctions.

It should not, however, be an unconstrained coordinate in a material
optimizer. Gun energy describes the excitation environment. For a
material-only optimization it should be:

- fixed, for example at 1 meV; or
- treated as a fixed robustness ensemble that is evaluated for every material
  candidate.

### 2.2 The Nb top-film channel is inaccessible throughout the range

The minimum sampled top-film gap is

```math
\Delta_{\mathrm{TopFilm,min}}
=0.5(1.5384\ \mathrm{meV})
=0.7692\ \mathrm{meV}.
```

Therefore,

```math
2\Delta_{\mathrm{TopFilm,min}}
=1.5384\ \mathrm{meV}
>
E_{\mathrm{gun,max}}=1.5\ \mathrm{meV}.
```

All top-film pair-breaking parameters should therefore be null in this
experiment, within Monte Carlo noise. Their observed placement among the
dummies is a useful internal validation.

The statement that Nb pair breaking and phonon transport are nearly mutually
exclusive is too strong. A 4–5 meV primary may down-convert near its source
into multiple lower-energy phonons that can subsequently propagate. A
realistic cascade must be simulated before concluding that the two processes
cannot coexist.

### 2.3 `minEPhonons = 38.2 µeV` no longer preempts pair breaking

The cutoff is below:

- the minimum Al pair-breaking threshold, approximately 191 µeV; and
- the minimum bottom gap threshold, 90 µeV.

It therefore does not directly remove a phonon that can still trigger one of
the modeled QP-generating processes. This is a substantial correction over
the old 382 µeV cutoff.

The statement that 38.2 µeV is an order of magnitude below every physical
threshold is not numerically correct: it is about a factor of 5 below the
minimum Al pair threshold and about 2.4 below the minimum bottom threshold.
The chosen value may still be sufficient for the QP objective, but it should
be validated with a cutoff-convergence test rather than justified by the
"order of magnitude" statement.

## 3. Noise-floor results

The reported distribution from 200 identical 4-million-event evaluations
reproduces exactly:

| Statistic | Value |
|---|---:|
| Mean total QPs | 147.445 |
| Standard deviation | 18.277 |
| Relative standard deviation | 12.4% |
| Minimum / maximum | 98 / 200 |
| Zero fraction | 0 |
| Variance / mean | 2.266 |

This confirms substantial overdispersion relative to a Poisson count.

### 3.1 The compound-count interpretation is reasonable, but its quoted
numbers need correction

Applying the exact stage-2 top-surface filter and QP conversion to all raw
noise-run hits gives:

| Quantity | Recalculated value |
|---|---:|
| QP-producing top hits | 13,458 |
| QP-producing primary events | 13,457 |
| Mean QP multiplicity per hit | 2.191 |
| Multiplicity variance | 0.346 |
| Predicted `E[X²]/E[X]` | 2.349 |

The raw multiplicity distribution is:

| QPs represented by a hit | Number of hits |
|---:|---:|
| 2 | 12,171 |
| 3 | 1 |
| 4 | 1,286 |

These values differ from the reported mean 2.155, variance 0.287, and
prediction 2.288. The source of that numerical discrepancy should be checked.

The central mechanistic conclusion remains valid. A nonparametric bootstrap
of the 200 evaluation-level counts gives an approximate 95% interval
`[1.86, 2.70]` for the measured Fano factor, so the observed 2.266 is
compatible with the raw-hit prediction 2.349.

### 3.2 The Fano factor is heteroscedastic

Using the difference between the two full-run replicas to estimate conditional
noise gives approximate dispersion values ranging from:

- 2.15 in the lowest-count decile; to
- 4.09 in the highest-count decile.

The fixed-config value 2.27 is therefore not a universal variance law for the
full design box. Gap, source energy, absorption, and QP cascade behavior
change both the mean and count multiplicity.

For optimization, do not assume

```math
\operatorname{Var}(N_{\mathrm{QP}})=2.27\lambda
```

for every candidate. Prefer:

- replicated, candidate-specific noise estimates;
- a heteroscedastic count surrogate;
- a compound-Poisson or negative-binomial observation model; or
- a conservative upper-dispersion model when replication is limited.

### 3.3 The 53% number is not the detection limit of the whole Morris screen

The reported 53% is the three-standard-deviation resolution of one
independent pairwise comparison at the nominal configuration:

```math
3\sqrt{2}\frac{\sigma}{\lambda}\approx0.526.
```

It is not the minimum detectable mean `mu*` from 128 elementary effects.
Aggregating effects over 128 trajectories can detect a systematic effect
smaller than the noise of a single comparison, although the absolute-value
bias in `mu*` must then be calibrated with dummies.

The number remains useful for individual optimizer candidate comparisons and
final validation, but it should not be used to reject every Morris effect
smaller than 53%.

## 4. Random-number problem

The executable initializes CLHEP using:

```cpp
CLHEP::HepRandom::setTheEngine(new CLHEP::MTwistEngine);
CLHEP::HepRandom::setTheSeed((unsigned)clock());
```

See
[`BNL_G4CMP_HT_Feb27/Main/Main.cc`](../BNL_G4CMP_HT_Feb27/Main/Main.cc).

### 4.1 Exact hits-file duplicates prove seed collisions

Hashing nonempty hits files in the 200-point noise run finds:

- 23 groups of exact duplicates;
- 47 nonempty files in those groups;
- at least 24 repeated files beyond the first copy;
- all duplicate groups correspond to the same source-position index;
- separations between duplicated design-point indices range from 5 to 159.

Inspection confirms identical event IDs, track IDs, deposited energies,
continuous coordinates, and final times. Such matches are effectively
impossible for independent random streams.

In the full Morris run, comparing hits files across the 768 dummy elementary
effects finds three exact repeated nonempty sub-run pairs. This is a direct
example of seed reuse inside elementary-effect comparisons.

### 4.2 Why the autocorrelation test did not detect this

The lag test operates on total QPs after pooling 16 positions. It tests
short-range linear correlation in the run ordering. It does not test:

- nonlocal seed repetition;
- repeated streams at only one of 16 pooled positions;
- repeated streams whose outputs differ because physical configurations
  differ; or
- general dependence not expressed as lag-1 correlation.

Therefore, "no significant autocorrelation" does not imply "independent
seeds." The statement that the seed worry is resolved negatively should be
removed.

### 4.3 Required correction

Add an explicit, recorded seed to every generated macro before
`/run/initialize`. For Morris screening:

1. define a seed bank by replica and source position;
2. use the same seed for the two endpoints of an elementary effect;
3. use different seeds for independent replicas;
4. record the seeds in the manifest;
5. validate the selected parameters and optimizer finalists with a fresh seed
   bank.

The fixed source positions are common scenarios or blocking variables. They
are not full common random numbers because random directions, polarization,
scattering, boundary absorption, and other stochastic decisions are currently
unpaired.

## 5. Source-position sampling

The 16 fixed scrambled-Sobol positions are an important improvement over one
source position. The raw noise run nevertheless shows strong positional
heterogeneity:

| Spatial statistic | Value |
|---|---:|
| Lowest mean QPs at one site | 2.515 |
| Highest mean QPs at one site | 23.09 |
| Maximum/minimum ratio | 9.18 |
| CV among the 16 site means | 60.6% |

Using the same position set for every design point prevents changing source
locations from being mistaken for a parameter effect. It does not establish
that the 16-site quadrature accurately represents a uniform source
distribution.

The position design should be tested with:

- multiple independent 16-point Sobol scrambles; or
- nested 16-, 32-, and 64-point Sobol sets.

Check convergence of:

- total QPs per event;
- maximum-electrode QPs;
- the leading signed elementary effects; and
- the spatial poisoning footprint.

Position convergence is especially important because material changes can
interact with Si phonon focusing.

## 6. Dummy-calibrated Morris analysis

The QPDE parameters

```text
f_01, r, s, I_ph, pt, n_cooper
```

have no code path into `calculate_QPs` or Geant4. They are legitimate null
controls for:

- `total_QPs`;
- `QP_yield_per_event`;
- `max_electrode_QPs`; and
- other observables constructed solely from generated QPs.

They are not dummies for `peak_DG_MHz` or `total_integrated_DG`, because they
enter `calculate_xQPs`. The stage-3 script accepts an arbitrary objective and
should reject dummy-threshold significance testing when the objective depends
on those QPDE parameters.

### 6.1 The current interval is 90%, not 95%

[`stage3_screen_analysis.py`](stage3_screen_analysis.py) takes the 5th and
95th bootstrap percentiles. This is a two-sided 90% interval or a one-sided
95% lower bound. The report should state this explicitly.

### 6.2 The maximum dummy has uncertainty

The current rule is:

```text
physical bootstrap lower bound > point estimate of maximum dummy mu*
```

It treats the maximum dummy estimate as known exactly. It also does not give a
formal family-wise false-positive rate for 47 comparisons.

A joint trajectory bootstrap was therefore performed. For each bootstrap
sample, all physical and dummy `mu*` values were recomputed and each physical
parameter was compared with the maximum dummy in that same bootstrap draw.

Under the joint rule:

- 18 rather than 20 parameters have a positive 5th-percentile difference;
- `setTopPhLifetimeSlope` becomes borderline;
- `setWallAbs` becomes borderline;
- `stiffness 4 4` and `stiffness 1 2` remain only narrowly above the dummy
  maximum.

The practical reporting tiers should be:

1. strong physical/model effects through `setBotGapThres`;
2. `setBotQPLim` as a strong numerical-model effect, not a material result;
3. marginal stiffness effects, interpreted cautiously;
4. `TopPhLifetimeSlope` and `WallAbs` as suggestive rather than confirmed.

The phrase "significant" should be qualified as "above the empirical dummy
screening threshold." This is a useful screening rule but not a conventional
multiple-testing-controlled significance test.

## 7. Independent stability checks

### 7.1 First 64 versus second 64 trajectories

The saved T=64 result uses the first half of T=128. Comparing it to the full
T=128 result is not an independent-half test because the full result contains
that first half.

Analyzing trajectories 1–64 and 65–128 separately gives:

| Stability diagnostic | Result |
|---|---:|
| Spearman rank correlation across 47 physical parameters | 0.829 |
| Rank-correlation p-value | `6.4e-13` |
| Top-ten overlap | 9 of 10 |
| First-half empirical discoveries reproduced in second half | 15 of 15 |
| First-half dummy threshold | 0.198 |
| Second-half dummy threshold | 0.158 |

This is strong evidence that the leaders are reproducible across independent
Morris trajectories. The result report's stability conclusion is supportable,
but it should cite this actual first-half/second-half analysis.

### 7.2 Elastic-stability filtering

The report correctly notes that 538/6,912 points violate the stated cubic
elastic-stability condition. Ninety-six of the 128 trajectories contain only
stable points.

Recomputing the screen using only these 96 all-stable trajectories changes
the leading `mu*` estimates by less than approximately 10%. Thus the invalid
elastic corners do not generate the leading film, source, or geometry
rankings.

The stiffness effects themselves remain marginal and should not be treated as
fabrication controls. The substrate constants are mutually coupled and should
be replaced by coherent lattice/material presets.

The velocity-ratio reparameterization successfully prevents the G4CMP crash,
but it does not by itself create a physically coherent elastic model.
Moreover, the cubic Born stability conditions do not generally include
`C11 > C44`; the ratio constraint should be described as a G4CMP/acoustic-mode
requirement rather than that specific Born criterion.

## 8. Physical interpretation of the leading effects

### 8.1 Backside absorber

The strongest result is a coherent group:

```text
BotAbs
Island / IslandSpacing
BotPhLifetime
BotThickness
BotVSound
BotGapThres
```

Their directions agree with a patterned normal-metal phonon sink:

- increasing `BotAbs` lowers top-junction QPs;
- increasing Cu island size lowers QPs;
- increasing island spacing raises QPs;
- increasing Cu thickness lowers QPs;
- increasing bottom-film phonon lifetime or sound speed raises QPs by making
  escape more likely.

This is consistent with the backside-Cu mitigation studied in the Yelton
paper.

### 8.2 `setIsland` and `setIslandSpacing` were misclassified

These parameters do not describe qubit electrode area. The Waffle electrode
code constructs the backside unit cell using:

```cpp
l_cell = l_island + l_spacing;
```

and accepts absorption only when the boundary point lies on an island. Their
approximate geometrical coverage scales as:

```math
f_{\mathrm{coverage}}
\sim
\left(
\frac{l_{\mathrm{island}}}
{l_{\mathrm{island}}+l_{\mathrm{spacing}}}
\right)^2.
```

They should be restored to the primary fabrication/design space and
reparameterized as:

- backside coverage fraction; and
- backside pattern pitch.

This distinction also resolves their signs: larger islands reduce QPs,
whereas larger spacing increases them.

### 8.3 Junction properties

The following effects are physically self-consistent:

- increasing `TopGap` reduces QPs;
- increasing `TopAbs` increases QPs;
- increasing `TopThickness` increases QPs;
- increasing `TopVSound` reduces absorption and QPs;
- increasing `TopPhLifetime` reduces absorption and QPs.

The `TopGap` response contains both:

1. the physical pair-breaking threshold; and
2. the conversion `round(E_deposited/Delta)` used to define the QP count.

It should not be interpreted as evidence that the gap can be increased
without consequences for critical current, junction design, qubit frequency,
loss, or fabrication.

### 8.4 Junction width and height

Both have positive, very large effects because a larger junction collection
area intercepts more phonons. This is reasonable but produces a degenerate
optimization if the electrical qubit design is ignored.

Hold them fixed or impose device-performance constraints unless electrical
co-design is explicitly included.

### 8.5 Range dependence

Morris `mu*` ranks influence over the selected parameter ranges. It does not
measure an intrinsic, range-independent material importance.

For example:

- bottom thickness was varied only from 0.5 to 1.5 µm;
- the relevant backside-Cu literature includes thicknesses up to roughly
  10 µm;
- absorption probabilities were varied over large effective ranges.

The statement that `BotAbs` is the strongest lever is valid within the chosen
box. It is not a universal comparison of what fabrication change will yield
the largest improvement.

## 9. Consequences for optimization

The proposed continuous ten-variable space:

```text
BotAbs, TopGap, TopAbs, TopThickness, TopVSound, TopPhLifetime,
BotPhLifetime, BotThickness, BotVSound, BotGapThres
```

is numerically tractable but not physically coherent. Independent optimization
could combine:

- the gap of one material;
- the sound speed of another;
- an unrelated phonon lifetime;
- an arbitrary absorption probability; and
- a thickness that invalidates the assumed interface calibration.

Such a result would not describe a fabricable material.

### 9.1 Recommended optimization variables

Use:

1. backside material as a coherent categorical preset;
2. backside thickness, initially including no film and approximately
   1, 3, 5, and 10 µm;
3. backside coverage fraction;
4. backside pattern pitch;
5. package/edge treatment;
6. constrained Al junction thickness;
7. a coherent junction-material preset only if electrical qubit constraints
   are included.

Treat calibrated effective quantities such as `TopAbs`, `BotAbs`, and film
lifetimes as uncertainties or linked preset properties unless a validated
fabrication-to-parameter map is available.

### 9.2 Variables to fix or use as scenarios

Fix or scenario-average:

- gun energy and source type;
- source locations and directions;
- qubit width and height;
- operating temperature;
- numerical tracking controls;
- substrate constants, except through coherent material presets.

Fix:

- all `QPLim` values at a validated value, normally 3;
- `BotGapThres` consistently with the junction pair-breaking threshold.

Do not include the six QPDE dummy coordinates in the final optimizer. They are
useful controls during a Morris screen but waste dimensions in optimization.

### 9.3 Objective

`QP_yield_per_event` is appropriate for the present screening step. It is not
yet a complete qubit-damage objective.

For optimization, retain at least:

- total QPs per event;
- maximum-electrode QPs per event;
- maximum QP density;
- a high quantile over source position/energy;
- poisoning footprint;
- recovery or time-integrated QP density;
- failure and validity status.

The current stage-2 pooling averages the 16 positions before computing the
electrode objective. For a robust positional objective, stage 2 should retain
per-position QP summaries instead of only the pooled result.

## 10. Recommended next actions

In order of priority:

1. Replace `clock()` with explicit recorded seeds and use paired seed banks.
2. Correct the seed conclusion in `README.md` and
   `RESULTS_stage1_to_stage3.md`.
3. Correct the Cu-island interpretation and restore coverage/pitch to the
   actionable design space.
4. Replace the fixed dummy threshold with a joint trajectory bootstrap or
   another calibrated empirical-null test.
5. Label the current bootstrap interval correctly.
6. Correct the compound-count multiplicity numbers.
7. Test independent Sobol position scrambles or nested position counts.
8. Perform a `minEPhonons` convergence test.
9. Replace independent material constants with coherent presets and
   constraints.
10. Run a smaller confirmation screen after these corrections rather than
    immediately repeating all 6,912 points at higher fidelity.
11. Use at least three paired replicas and approximately `1e7` events for
    close candidate comparisons or final validation.

## Final conclusion

The new results do support the central material-physics conclusion:

> Patterned backside normal-metal absorption and the Al junction
> pair-breaking/film-capture parameters are the dominant modeled controls on
> junction-localized QP generation in the present sub-meV source experiment.

The most reliable design insight is not simply "`BotAbs` is number one." It
is that a physically coherent backside stack—material, thickness, coverage,
pitch, interface absorption, and phonon escape—acts as one coupled mitigation
system. The signs of all important variables in that system agree with the
expected down-conversion mechanism.

The run should therefore be treated as a successful qualitative screen with
strong evidence for backside mitigation. Before quantitative optimization,
the random streams, spatial convergence, empirical-null inference,
configuration-dependent noise, and material-parameter coupling must be
corrected.

---

# Amendment — response and resolution status (2026-07-28)

Added after the audit. **Every one of the 14 concerns was independently
reproduced from the stored data and source, and all are accepted as valid.**
The verification commands and the corrected artifacts are in the repository;
nothing in the audit was found to be mistaken.

Two minor numerical differences, both traceable to a normalisation choice
rather than an error: recomputing the half-split with μ\* normalised over the
*full* run rather than per half gives top-10 overlap 8/10 (audit: 9/10) and
half thresholds 0.175 / 0.176 (audit: 0.198 / 0.158). The audit's per-half
normalisation is the cleaner independent test; the conclusion (ρ = 0.829,
p = 6.4e-13) is identical either way.

## Resolution table

| # | Audit concern | Verified | Status | Where fixed |
|---|---|---|---|---|
| 1 | `clock()` seeding reuses streams | 23 groups/47 files (noise floor); 30/60 (screen); 11 pairs adjacent-in-trajectory | **Fixed + claim retracted** | `stage1_run_simulations.py` (`SEED_BASE`, `/random/setSeeds`); `README.md`; `RESULTS §3.1` |
| 2 | Dummy threshold ignores its own uncertainty | Joint bootstrap → 18, losing `setTopPhLifetimeSlope`, `setWallAbs` | **Fixed** — joint rule is now the default | `stage3_screen_analysis.py::joint_dummy_test` |
| 3 | `setIsland`/`setIslandSpacing` misclassified | `WaffleKaplanElectrode` → `botSurfProp`; uses `fIsland`/`fSpacing`, no width/height | **Fixed** | `sensitivity_params.py`; `RESULTS §4.1–4.2` |
| 4 | Fano is configuration-dependent | 2.2 (lowest decile) → 4.1 (highest) | **Fixed** | `README`; `RESULTS §3.1`, §6 noise model |
| 5 | Ten-variable space not physically coherent | — (judgement, accepted) | **Fixed** — replaced with preset-based space | `RESULTS §6` |
| 6 | 16 positions not shown to converge | site means 2.5→23.1 QPs, CV 61% | **Documented as a limitation** | `RESULTS §5` |
| 7 | Compound-count numbers wrong | All hits: mean 2.191, var 0.346, pred 2.349 (was 2.155/0.287/2.288 from a ~40-file subset) | **Fixed** | `README`; `RESULTS §3.1` |
| 8 | "Independent halves" claim invalid | T=64 ⊂ T=128; proper split ρ=0.829, p=6.4e-13 | **Fixed** — real half-split now computed and reported | `stage3_screen_analysis.py`; `RESULTS §3.3` |
| 9 | Interval is 90%, not 95% | 5th/95th percentiles | **Fixed** — level now stated | `stage3_screen_analysis.py`; `README` |
| 10 | 53% misused as a Morris detection limit | — | **Fixed** | `RESULTS §0`, §3.1 |
| 11 | Nb "mutually exclusive" too strong | 2Δ_TopFilm,min = 1.538 meV > E_gun,max = 1.5 meV | **Fixed** — softened; cascade argument accepted | `RESULTS §4.3` |
| 12 | Dummies invalid for `*_DG` objectives | — | **Fixed** — script now refuses them | `stage3_screen_analysis.py` |
| 13 | Born criterion misstated | Cubic Born: C₁₁>\|C₁₂\|, C₁₁+2C₁₂>0, C₄₄>0 — C₁₁>C₄₄ is not among them | **Fixed** | `sensitivity_params.py`; `README`; `RESULTS §5` |
| 14 | `minEPhonons` "order of magnitude" wrong | 5.0× and 2.4× | **Fixed** — restated, and flagged as an untested assumption | `sensitivity_params.py`; `RESULTS §5` |

One additional error found while responding, not raised by the audit: the
reported "0% zero-signal" applies to the noise-floor run; the screen has
8/6912 (0.12%) zero design points. Corrected in `README.md`.

## Seeding fix — verification

`Main.cc` seeds from `clock()` at line 41 but executes the macro at line 91, so
a macro-level `/random/setSeeds` overrides it. Measured:

- same seed → **byte-identical hits content** (only the Event ID column shifts,
  which stage 2 never reads: it uses energies, positions and times);
- different seed → different stream;
- the command must sit **immediately before `/run/beamOn`** — anything between
  them (notably `/main/detector_param/update`) consumes draws and offsets the
  stream relative to the event loop.

Seeds are keyed by `(trajectory, replica, position)` and recorded in the
manifest. Keying by *trajectory* rather than design point implements the
audit's paired-endpoint recommendation: every design point within a trajectory
shares a seed bank, so both endpoints of each elementary effect run on the same
stream (common random numbers, variance-reducing for the difference), while
independent trajectories and replicas keep independent streams.

## One point of emphasis where this response differs

The audit ranks the seeding defect first and concludes the run "is not yet a
quantitatively reproducible optimizer training set". Both are accepted, and the
retraction stands. On the *magnitude* of the consequence for this screen's
rankings, though: the duplicate rate is 0.033% of sub-runs, and a shared stream
between the two endpoints of an elementary effect is variance-**reducing**
rather than biasing. The rankings survive — independently confirmed by the
half-split (ρ = 0.829). So the correct reading is that the seeding defect
invalidates *reproducibility* and invalidated a *claim that was made about it*,
not the screening conclusions.

## Not addressed here

Items requiring new simulation rather than correction, carried into the next
run: a `minEPhonons` cutoff-convergence test; independent/nested Sobol position
sets; per-position stage-2 summaries for positional objectives; coherent
material presets replacing the independent substrate constants; and a
re-screen under explicit seeds.

---

# Second-round audit — new issues found after the amendment (2026-07-28)

## Scope and overall assessment

This second review checked the amended claims against the current Python
source and independently reconstructed the Morris elementary effects directly
from `MorrisSequence.csv` and `qp_summary.csv`.

The stored Stage-3 screen itself is internally consistent:

- 6,912 design points form 128 complete trajectories of 54 points;
- all 6,912 trajectories steps change exactly one of the 53 variables;
- all 13,824 summary rows are present, with exactly two replicas per design
  point;
- every stored replica has `n_sim = 2,000,000`;
- all saved `mu_star` values agree with an independent CSV-only
  reconstruction to approximately \(4\times10^{-15}\); and
- the corrected joint dummy rule gives 18 physical parameters above the
  empirical dummy floor.

Therefore, the issues below do **not** reveal a hidden arithmetic error in the
saved 18-parameter ranking. They affect future noise-floor runs, failure
handling, auxiliary analysis, and the interpretation of “minimise QP damage.”

## New issue 1 — noise-floor mode reuses only four explicit seed banks

**Severity:** high for the next noise-floor campaign; no effect on the stored
pre-fix noise-floor result.

The explicit seed function computes

```python
trajectory = design_point_index // trajectory_length
```

and keys the seed by `(trajectory, replica, position)`. This is the intended
common-random-number construction for a Morris design: all 54 endpoints in one
trajectory share a seed bank, while different trajectories use different
banks.

However, `SENSITIVITY_NOISE_FLOOR_N=200` creates 200 independent copies of one
default configuration. These rows are **not** points along Morris trajectories.
Applying the 54-point grouping anyway produces:

```text
200 nominally independent noise-floor points
→ ceil(200 / 54) = 4 distinct seed banks
→ as many as 54 identical configurations reuse exactly the same seeds
```

Because configuration, source positions, and seeds are then identical, those
repeated points are exact Monte Carlo replicates rather than independent
draws. The effective sample size is approximately four, not 200. A reported
standard error or bootstrap interval based on 200 rows would therefore be
spuriously precise.

**Required correction:**

- in Morris mode, key by `(trajectory, replica, position)`;
- in noise-floor mode, key by
  `(design_point_index, replica, position)`; and
- add a pre-run assertion that the noise-floor design has exactly
  `NOISE_FLOOR_N × N_REPLICAS × N_POSITIONS` unique intended sub-run seed
  tuples.

The manifests should continue to record every seed. The noise-floor report
should also verify the number of unique seed banks before estimating a noise
distribution.

## New issue 2 — Stage 3 can silently associate outcomes with the wrong design

**Severity:** high for an incomplete future Morris run; no effect on the
current complete run.

`aggregate_replicas()` preserves the order in which surviving design-point
labels appear in `qp_summary.csv`. `report_morris()` then takes

```python
n_points = min(len(design), len(aggregated))
design = design.iloc[:n_points]
y = aggregated["y"].to_numpy()[:n_points]
```

This is safe only when every design point is present and ordered exactly as
`MorrisSequence.csv`. Stage 2 explicitly permits a manifest entry to be
skipped when none of its hits files is readable. If, for example, `Morris_100`
is absent, the next surviving response (`Morris_101`) is paired positionally
with design row 100. Every later response is shifted by one row even though
the resulting arrays still have plausible shapes.

This is more dangerous than a hard failure because it can generate a
reasonable-looking but physically meaningless Morris table.

**Required correction:**

1. Parse the integer index from every `design_point` label.
2. Join the aggregated response to `MorrisSequence.csv` by that index rather
   than by row position.
3. Verify the expected replica count for every design point.
4. Reject or explicitly remove an entire 54-point trajectory if any endpoint
   is absent or has insufficient replicas.
5. Before computing effects, assert that each retained trajectory has 54 rows
   and that every consecutive step changes exactly one parameter.

Partially missing source-position files are a related fidelity issue. Scaling
`n_sim` preserves an unbiased rate estimate under random file loss, but
different position subsets can have very different expected yields because
the measured site means vary by a factor of 9.2. A Morris trajectory should
preferably use the same surviving position set at every endpoint, or be marked
invalid for paired comparison.

## New issue 3 — generated QP count is not a complete measure of qubit damage

**Severity:** high for material optimization; the present objective remains
valid for screening phonon-to-QP generation.

`QP_yield_per_event` measures the number of QPs generated in the modeled
junction electrodes per primary event. It does not by itself model all of the
processes that determine qubit poisoning:

- QP diffusion through superconducting films and between device regions;
- trapping into normal-metal or lower-gap superconducting structures;
- QP tunneling across the Josephson junction;
- proximity-induced subgap states near a normal-metal trap;
- electrode-dependent recombination and loss; or
- the resulting time-dependent \(T_1\), parity-switching, and correlated-error
  footprint under calibrated device parameters.

The current Stage-2 ODE treats each electrode independently after assigning
the generated QPs. Its six QPDE quantities are useful as post-processing
parameters, but they do not provide a spatial diffusion/trap model. In
particular, the present simulation cannot evaluate important trap design
variables such as trap material, gap offset, interface transparency, distance
from the junction, or trap area.

There is also a degenerate-objective problem. `setWidth`, `setHeight`,
`setTopAbs`, and `setTopThickness` have large effects on the number collected
by the junction. Unconstrained minimization can therefore “succeed” by making
the junction collection area or absorption vanish, even if the resulting
object is no longer a viable qubit.

**Required correction for optimization:**

- retain `QP_yield_per_event` as a mechanistic generation objective;
- add a per-electrode QP-density or calibrated QP-induced relaxation
  objective;
- retain a high quantile over source position and energy, not only the mean;
- include poisoning duration and number of simultaneously affected
  electrodes; and
- impose electrical constraints such as \(f_{01}\), \(E_J/E_C\), capacitance,
  microwave quality factor, junction critical current, and fabrication
  feasibility.

Local QP-trap optimization requires extending or coupling the current model
to QP diffusion, trapping, and junction-tunneling physics.

## New issue 4 — the source is a Green's-function probe, not a realistic
poisoning ensemble

**Severity:** high for extrapolation to radiation-hard materials; no problem
for the stated sub-meV phonon-transport experiment.

The 0.6–1.5 meV monoenergetic phonon source is well chosen to probe Al pair
breaking and transport to the junction. It should be interpreted as a
controlled Green's-function experiment. A gamma ray, cosmic ray, stress
release, or energetic electron does not begin as one phonon in this range. It
produces a broad electron-hole and phonon cascade whose spectrum and spatial
distribution evolve before reaching the qubits.

Important consequences:

- the Nb top-film pair-breaking channel is essentially inaccessible in the
  current source range, but it can be active early in a realistic cascade;
- rankings of ground-plane material, high-gap films, anharmonic decay, and
  high-energy film lifetimes may change under a broad source;
- a design optimized for a 1-meV injected phonon need not minimize the
  spatial or temporal footprint of a 100-keV-scale substrate impact; and
- phonon, gamma/electron-hole, infrared/photon, and stress-release poisoning
  mechanisms need not have the same optimum.

**Required correction:**

Use source conditions as scenarios rather than design variables. Validate
candidate material stacks over at least:

1. the present controlled pair-breaking-phonon source;
2. a physically motivated injected-phonon energy distribution;
3. realistic gamma/electron-hole cascades at several impact locations; and
4. package-relevant boundary and thermalization conditions.

The optimization target should be an expectation or upper quantile over this
source ensemble. Gun energy should not be offered to the optimizer as a way
to reduce the objective.

## New issue 5 — Stage 2b does not aggregate the new replica-labelled outputs

**Severity:** medium; affects the auxiliary correlation analysis, not
`stage3_screen_analysis.py` or the saved Stage-3 Morris table.

The current Stage-1/Stage-2 pipeline names replica products and summary rows
as:

```text
Morris_i_r0
Morris_i_r1
```

but `stage2_compute_QPs_sensitivity_analysis.py` still requests:

```python
outcomes_by_name.get(f"Morris_{idx}", ...)
```

and its chi-squared path similarly looks for `Morris_i_xQPs.npz`. With the new
replica layout, those exact names do not exist. Depending on the analysis
path, the script will drop the points or report missing NPZ files rather than
calculate the intended correlations.

**Required correction:**

- group outcomes by the manifest's `design_point`;
- average or otherwise combine replicas according to an explicitly stated
  estimator;
- preserve within-point replica variance;
- support the pooled-position file layout; and
- reject a design point when the required replica set is incomplete.

The same aggregation logic should be shared with Stage 3 so the two analysis
paths cannot drift.

## Additional lower-priority safeguards

These do not invalidate the present results but should be corrected:

1. The dummy-objective check uses a blacklist containing only
   `peak_DG_MHz` and `total_integrated_DG`. A future QPDE-dependent column with
   another name would bypass it. Prefer a whitelist of objectives known to be
   constructed only from generated QPs.
2. Raw `total_QPs` and `max_electrode_QPs` are comparable only when event
   counts and surviving position counts match. Multi-fidelity optimization
   needs per-event versions of both.
3. The comment in `sensitivity_params.py` still says that 38.2 µeV is an
   order of magnitude below every physical threshold. The correct factors are
   5.0 below \(2\Delta_{\rm Al,min}\) and 2.4 below the minimum bottom
   threshold.
4. The comment calling fixed source positions “common random numbers” is
   imprecise. They are common spatial scenarios or blocking points. The
   explicit CLHEP streams provide the random-number pairing.
5. `Stage3_material_QP_optimization_recommendations.md` and
   `MaterialOptimization_approach.md` contain portions written before the
   latest source, cutoff, seed, and replica corrections. They should not be
   treated as the final protocol until reconciled with `README.md`,
   `RESULTS_stage1_to_stage3.md`, and this audit.

## Second-round action order

Before another expensive campaign:

1. fix the noise-floor seed key and assert seed-bank uniqueness;
2. replace positional Stage-3 alignment with label-based joins and
   complete-trajectory validation;
3. make Stage 2b replica-aware;
4. retain per-position Stage-2 summaries;
5. complete cutoff and nested-position convergence tests;
6. implement coherent material/stack presets; and
7. rerun the noise floor and Morris screen with explicit recorded seeds.

Before optimization:

8. define electrical/fabrication constraints that prevent degenerate qubit
   geometries;
9. add robust per-electrode, temporal, and spatial poisoning objectives;
10. extend the physics if local QP traps are design variables; and
11. validate finalists across realistic source and package scenarios using
    fresh seed banks.

## Second-round conclusion

The current 18-parameter Stage-3 table remains a credible qualitative
screen. Its strongest result—a coupled backside phonon-downconversion stack,
alongside junction gap and film-capture physics—is not overturned.

The pipeline is nevertheless not ready for a new noise-floor run or
failure-tolerant optimizer without the seed-key and response-alignment fixes.
Nor should the current QP-generation objective alone be described as a
complete optimization of qubit damage. Those distinctions should remain
explicit in all subsequent reports.


---

# Amendment 2 — response to the second-round audit (2026-07-28)

**All five new issues and all five lower-priority safeguards are valid and were
reproduced from the source and stored data.** Two were urgent; both are fixed
and verified. Nothing in this round changes the 18-parameter Stage-3 table —
confirmed by regression (18 of 47, threshold 0.1734, half-split rho = 0.829,
unchanged).

Two of these are failures of my own work, and I want that recorded plainly:

* **New issue 1 is a bug I introduced in Amendment 1.** The seed fix keyed by
  `design_point // 54` unconditionally. That is correct for Morris and is the
  whole point of the construction, but in noise-floor mode the rows are
  independent repeats of one configuration, not a trajectory. Measured: **64
  unique seeds for 3,200 sub-runs**, with up to 54 identical configurations
  sharing a stream *and* a position set. The effective sample size would have
  collapsed from 200 to ~4 while the report quoted statistics from 200 rows —
  silently destroying the very quantity that mode exists to measure.
* **Lower-priority item 3 shows I reported a fix that had not landed.** The
  `minEPhonons` comment edit silently failed to match, my verification grep
  came back empty, I did not notice, and Amendment 1's resolution table
  recorded it as fixed. The audit was right not to trust that table.

## Resolution table — second round

| # | Issue | Verified how | Status |
|---|---|---|---|
| 1 | Noise-floor mode reuses 4 seed banks | Enumerated seeds: **64 unique / 3,200** | **Fixed** — `sub_run_seed(..., noise_floor=)` keys by design point in that mode → 3,200/3,200; Morris keeps 2,048 banks (128 traj × 16 pos). Added `assert_seed_bank_is_sound()`, which fires before any launch |
| 2 | Stage 3 silently misaligns responses | Dropped `Morris_100` from the summary: design row 100 received `Morris_101`'s response, shifting **every later row** with no error | **Fixed** — label-based join on the parsed design index; incomplete points drop their **whole 54-point trajectory**, because an elementary effect is a difference and one bad endpoint corrupts the two effects touching it |
| 3 | QP count ≠ complete qubit damage | — (scope; accepted) | **Documented, not fixable in code.** Needs QP diffusion/trapping/tunnelling physics and electrical constraints |
| 4 | Source is a Green's-function probe | — (scope; accepted) | **Documented.** Needs a source-scenario ensemble; gun energy stays a scenario, never an optimiser coordinate |
| 5 | Stage 2b not replica-aware | Ran it: `KeyError: 'hits_file'`, i.e. it also broke on the new manifest schema, not only the naming | **Fixed in two passes** — see "Amendment 2a" below. The first pass fixed only the *complete-run* path; the incomplete-summary and missing-replica paths stayed broken |
| L1 | Blacklist of QPDE-dependent objectives | — | **Fixed** — replaced with a whitelist (`QP_ONLY_OBJECTIVES`); an unknown column is now refused rather than admitted |
| L2 | Raw counts need matched fidelity | — | **Documented**; `QP_yield_per_event` already carries the per-event form |
| L3 | `minEPhonons` comment | grep: the "order of magnitude" claim was still present | **Fixed** (genuinely, re-verified by grep) |
| L4 | "common random numbers" for positions | — | **Accepted with a qualification.** Fixed positions are a common *scenario*/blocking set, and the CLHEP streams are the actual RNG pairing. They do still cancel position variance in the paired difference, which is why the variance-reduction language arose; the wording is now precise about which mechanism does what |
| L5 | Stale planning documents | — | **Flagged.** `Stage3_material_QP_optimization_recommendations.md` and `MaterialOptimization_approach.md` predate the source/cutoff/seed/replica corrections and are not the current protocol |

## Verification

```
noise-floor seeds : 3200 unique / 3200 sub-runs      (was 64/3200)
morris seeds      : 2048 banks = 128 traj x 16 pos   (endpoints paired, by design)
seed assertion    : fires on the degenerate layout, passes on both correct ones
alignment         : Morris_100 dropped -> 1 trajectory removed, 127 retained,
                    no silent shift
regression        : complete run unchanged - 18 of 47, threshold 0.1734, rho 0.829
stage 2b          : runs to completion on the replica-labelled run; top physical
                    correlations (setBotAbs -0.307, setTopAbs +0.207,
                    setTopPhLifetime -0.153, setTopGap -0.150) agree with the
                    Morris screen. n_cooper and pt rank high here legitimately -
                    total_integrated_DG IS ODE-dependent, which is exactly why
                    the dummy test now refuses that objective
```

## Scope, restated

The second-round conclusion is accepted as written: the 18-parameter table is a
credible qualitative screen whose central result — a coupled backside
phonon-downconversion stack alongside junction gap and film-capture physics — is
not overturned; and the current objective must not be described as a complete
optimisation of qubit damage.

The two urgent items were **latent hazards for the next campaign**, not errors
in the stored run. Issue 1 could not have affected the existing noise floor
(that run predates explicit seeding), and issue 2 could not have affected the
existing screen (it is complete, so nothing was dropped).

## Still outstanding — requires new simulation, not correction

1. `minEPhonons` cutoff-convergence test (the 5.0×/2.4× margin is an assumption).
2. Independent / nested Sobol position sets (site means span 9.2×, CV 61%).
3. Per-position Stage-2 summaries, for positional and worst-case objectives.
4. Coherent material/stack presets replacing independent substrate constants.
5. Re-run of noise floor and screen under explicit recorded seeds.
6. Robust objectives (per-electrode density, temporal footprint, high quantile)
   and electrical constraints, before any optimisation.


---

# Amendment 2a — Stage 2b: the first fix was incomplete (2026-07-28)

My Amendment 2 recorded new issue 5 as "Fixed ... verified end-to-end". That
was **over-claimed**. The fix worked for the current *complete* run, which is
the only case I exercised. The failure-prone paths — the ones that actually
matter for an incomplete, crash-prone, or optimizer-driven design — remained
broken, and re-checking found three defects.

The root cause was a design error on my part: `design_point_labels()` inferred
each design point's expected replica set **from `qp_summary.csv`**, i.e. from
the very artifact that may be truncated or absent. The expected set must come
from the **manifest**, which stage 1 writes at macro-generation time, before
anything runs, and which is the only record of what *should* exist.

| Path | Measured behaviour before | After |
|---|---|---|
| `qp_summary.csv` **missing** (fallback derives outcomes from hits, keyed `Morris_7_r0`) | helper returned the pre-replica name `Morris_7`; nothing matched, so **every one of 6,912 design points was silently dropped** and the analysis reported no usable samples | 6,912 usable |
| `qp_summary.csv` **truncated** (a point's `_r1` row absent) | returned `['Morris_7_r0']` and averaged over **one** replica with no warning: 87.79 instead of the true 81.34. That row then carries √2× its neighbours' noise while looking identical — heteroscedastic contamination of a correlation that weights rows equally | point dropped and itemised |
| **cost** | re-read the summary CSV on every call: measured 30.6 ms × 6,912 ≈ **211 s** of pure redundant I/O | one manifest pass, **18 ms** |

Note the second row is the dangerous one: it does not fail, it silently
degrades. `replica_mean()`'s own docstring claimed it dropped incomplete
points, but it could not — it was only ever handed the labels that had already
survived.

## Fix

`build_replica_index(entries)` builds `design-point index -> expected sample
names` from the manifest once. `replica_mean()` now receives that expected set
and returns NaN unless every member is present. `build_trials()` itemises drops
by cause (absent from manifest / missing an expected replica / non-positive
outcome) so an incomplete run reports what it lost instead of quietly
shrinking. The chi² path uses the same index, and both paths fall back to the
pre-replica layout when a manifest has no `design_point` key.

## Verification

```
complete run   : 6904 usable, 8 dropped (all non-positive - matches the known
                 8/6912 zero-signal points).  REGRESSION CLEAN
r1 removed from 100 points:
                 6804 usable, 108 dropped = 100 "missing an expected replica"
                 + the same 8. Exactly 100 fewer than complete, none averaged
                 over a single replica
summary missing: 6912 usable (was 0)
chi2 path      : expects Morris_7_r0/_r1, both npz present; the pre-fix lookup
                 asked for `Morris_7`, which does not exist. 0 of 6912 design
                 points missing an expected npz
index build    : 18 ms once, replacing ~211 s of repeated CSV reads
```

## Process note

This is the second time in this cycle that I marked something fixed without
exercising the path that was actually broken (the first was the `minEPhonons`
comment, whose edit silently failed to match). Both were caught by re-checking
rather than by my own verification. The general lesson for this pipeline: a
fix validated only on the healthy, complete artifact is not validated — these
scripts' failure modes are overwhelmingly *silent degradation* on incomplete
input, so the incomplete case is the one that has to be tested.


---

# Amendment 2b — Stage 3 completeness and design validation (2026-07-28)

Raised after Amendment 2a: Stage 3 still inferred the expected replica count
from the data (`int(aggregated["n_replicas"].median())`) and still skipped
malformed Morris steps silently. Both are the same class of defect as 2a — the
check trusted the artifact it was meant to police — and both are fixed.

| Failure | Measured before | After |
|---|---|---|
| **Median collapses with the damage.** Reduce 5,000 of 6,912 points to one replica | median n_replicas → **1**, so **0 points flagged short**; all 5,000 crippled points passed as complete | 5,000 flagged; 93 trajectories dropped; 35 retained, reported |
| **`n_replicas` counts rows, not identities.** Two copies of `r0`, `r1` absent | scored **2 == expected**, so it **passed** | detected by exact set comparison; trajectory dropped |
| **Malformed design step** moves two parameters | step silently skipped — effects quietly lost, `mu*` averaged over unequal samples | **hard exit** naming the trajectory and step |

## Fix

`load_expected_replicas()` reads the manifest and returns
`design-point index -> frozenset(sample names)`, the same authority and
construction as Stage 2b's `build_replica_index()`. A design point is complete
only when its **observed sample-name set equals the manifest-declared set** —
not the right count, the right identities. With no manifest the script still
runs but says so loudly, stating that duplicate and wrong-identity rows cannot
then be detected.

`morris_elementary_effects()` now raises on any step that does not move exactly
one coordinate, and `validate_effect_structure()` asserts afterwards that every
parameter received exactly one effect per retained trajectory (128 × 53 for the
complete design). Unequal effect counts would make `mu*` incomparable across
the ranking, which is the entire output.

## Verification

```
complete run : manifest declares 6912 points, 2 replicas each
               effect structure: 128 effects x 53 parameters, validated
               18 of 47, threshold 0.1734, rho 0.829   REGRESSION CLEAN
case A       : 5000 flagged -> 93 trajectories dropped, 35 retained
case B       : duplicate-identity point detected, trajectory dropped
case C       : hard exit, "trajectory 0, step 4 changes 2 parameters"
```

The stored result is unaffected: its manifest and summary contain exact
{r0, r1} sets for all 6,912 points and its design has zero malformed steps.

## Pattern

Three rounds, three instances of the same mistake by me: a check that validates
an artifact using a quantity derived from that same artifact (summary-derived
replica labels in 2a, summary-derived replica *counts* here, and earlier a
resolution table asserting a fix I had not exercised). The rule this pipeline
needs is explicit: **completeness must be judged against the manifest, which is
written before any computation and cannot be degraded by a failure downstream.**
