# Stage 3: Material-Parameter Optimization for Quasiparticle-Poisoning Mitigation

> **SUPERSEDED — read `RESULTS_stage1_to_stage3.md` first (2026-07-28).**
>
> This document predates the stage-3 corrections and is kept for its framing
> and rationale only. It is **not** the current protocol. Specifically, it was
> written before:
>
> * the source range moved to [0.6, 1.5] meV and `minEPhonons` to 38.2 µeV;
> * `vtrans` became the ratio v_T/v_L (independent sweeps crashed G4CMP);
> * 16 source positions x 2 replicas per design point;
> * explicit recorded CLHEP seeds (`clock()` seeding was reusing streams);
> * the dummy-calibrated screen, which found **18 of 47** parameters above the
>   noise floor.
>
> Two of its conclusions are now known to be wrong:
>
> * `setIsland` / `setIslandSpacing` are **backside Cu absorber** coverage and
>   pitch, not qubit-electrode geometry — they belong in the actionable design
>   space, not discarded with the degenerate geometry.
> * a flat list of independent continuous material constants is **not** a
>   usable optimisation space; use coherent material presets plus backside
>   stack geometry (see `RESULTS_stage1_to_stage3.md` §6).


## Purpose

Stage 3 will investigate how material, film, boundary, and geometry
parameters affect quasiparticle (QP) generation in superconducting qubit
electrodes using Geant4/G4CMP simulations. The eventual goal is to identify
physically realizable device configurations that minimize QP poisoning and
the associated damage to qubits.

This note records:

1. the recommended subset of simulation parameters for Stage 3;
2. parameters that should be fixed, constrained, calibrated, or excluded;
3. important validity concerns in the current simulation and sensitivity
   pipeline;
4. suitable objective functions for QP mitigation;
5. recommended AI/ML and black-box optimization algorithms; and
6. a concrete staged optimization workflow.

The recommendations are based on the current versions of:

- `sensitivity_params.py`;
- `sensitivity_template_beamOn1e5.mac`;
- `stage1_run_simulations.py`;
- `stage2_compute_QPs.py`;
- the existing Morris run records and correlation analysis;
- the G4CMP Kaplan-film implementation and the BNL detector electrode code;
  and
- E. Yelton et al., *Modeling phonon-mediated quasiparticle poisoning in
  superconducting qubit arrays*, Physical Review B **110**, 024519 (2024),
  <https://arxiv.org/abs/2402.15471>.

## Executive recommendation

The Stage 3 optimizer should not search over all 53 currently expanded
Morris variables.

The initial optimization should use an approximately eight-dimensional,
physically constrained design space centered on backside phonon
down-conversion:

1. backside material preset;
2. backside-film thickness;
3. backside metal coverage;
4. backside pattern pitch;
5. junction material preset;
6. junction-film thickness;
7. edge/package phonon absorption; and
8. crystal orientation.

Material-dependent quantities such as gap, sound speed, absorption
probability, and phonon lifetime should be bundled into coherent material
presets or calibrated uncertainty models. They should not be varied
independently in combinations that cannot correspond to a real material.

The existing 6912-point Morris run is useful for diagnosing the pipeline,
estimating rough simulation scales, and identifying failure modes. It
should not be used naively as quantitative warm-start training data for the
Stage 3 optimizer. Its QP response is affected by a source/tracking
threshold mismatch, severe signal sparsity, unpaired random seeds, inactive
dimensions, and the inclusion of post-processing ODE parameters that are not
QP-generation controls.

## 1. Important findings in the current pipeline

### 1.1 The existing source sweep is dominated by a tracking threshold

The current configuration pins:

```text
/g4cmp/minEPhonons 0.000382 eV
```

The Morris sweep for `/main/gun/setEnergy` produces four levels:

```text
191, 318, 446, and 573 micro-eV
```

The 191 and 318 micro-eV primary phonons lie below the 382 micro-eV tracking
threshold and are killed at creation. Those samples therefore simulate
essentially no phonon transport or QP production.

In the stored `beamOn 1e5` run:

- 6906 samples completed with a QP summary;
- only 1501 produced nonzero total QPs;
- approximately 78% of completed samples had zero QPs; and
- a large part of that sparsity comes from the source-threshold mismatch
  rather than favorable material performance.

This makes a zero-valued result ambiguous: it may represent a genuinely
effective material design, a source killed by the tracking threshold, or a
low-probability Monte Carlo realization with no electrode hit.

### 1.2 `total_QPs` is already junction-localized

The macro contains:

```text
/main/sensor/setHitType Junction
```

In the detector code, a `Junction` hit is recorded only when:

- a phonon reaches an electrode rectangle;
- it is accepted according to the junction film-absorption probability;
- it is absorbed by the Kaplan electrode process; and
- it produces positive non-ionizing energy deposition.

Therefore, the present `total_QPs` column is already a sum of QPs generated
in qubit/junction electrodes. It is not a total over all QPs or all absorbed
energy elsewhere on the chip.

The per-electrode QP counts remain important because the same total can
represent either weak poisoning distributed across many qubits or severe
poisoning concentrated in one qubit.

### 1.3 The current Nb top-film parameters are inactive for the present source

The top ground-plane film is configured as Nb with:

```text
Delta_Nb = 1.5384 meV
```

Pair breaking in this film requires:

```text
E_ph >= 2 Delta_Nb = 3.0768 meV
```

Even the smallest swept Nb gap still gives a pair-breaking threshold well
above the maximum 0.573 meV gun energy. Consequently, the following
parameters cannot significantly control the current sub-meV injection
simulation:

- `setTopFilmAbs`;
- `setTopFilmVSound`;
- `setTopFilmGap`;
- `setTopFilmQPLim`;
- `setTopFilmPhLifetime`;
- `setTopFilmPhLifetimeSlope`; and
- `setTopFilmThickness`.

These parameters should be reconsidered when the source is changed to a
realistic high-energy gamma/electron-hole/phonon cascade, since such a
cascade can initially produce phonons above the Nb pair-breaking threshold.

### 1.4 Unpaired Monte Carlo randomness contaminates Morris elementary effects

Geant4 currently seeds CLHEP from `clock()`. Adjacent Morris samples do not
use a shared or controlled random stream.

This is especially problematic for a Morris trajectory: two adjacent rows
differ in one nominal model parameter, but they also receive unrelated
phonon histories. The observed elementary effect is therefore:

```text
parameter effect + difference between two Monte Carlo realizations
```

As a check, Morris indices were computed for `total_QPs`. Parameters such
as `pt` and `r` ranked highly even though they have no code path into
`calculate_QPs`; they are used only later in `calculate_xQPs`. Their apparent
effect on `total_QPs` must therefore come from sampling noise rather than
physics.

The previous integrated-decoherence ranking is still meaningful for
understanding its own stage-2 ODE calculation, where `pt`, `r`, `s`, and
other QPDE parameters really do enter. It should not be interpreted as a
ranking of material controls over QP generation.

For reliable material sensitivity:

- use explicit Geant4 random seeds;
- evaluate paired configurations with the same seed bank;
- use multiple independent replicas; and
- validate finalists with fresh, previously unused seeds.

### 1.5 Backside Cu is the main modeled mitigation structure

The current geometry uses patterned Cu islands on the backside. A phonon
must:

1. intersect a Cu island;
2. pass the effective `BotAbs` acceptance probability; and
3. fail to escape the Cu film before down-conversion.

Backside coverage, thickness, absorption, phonon lifetime, and sound speed
therefore directly control the probability and effectiveness of phonon
down-conversion.

This agrees with the reference paper: devices with backside Cu strongly
reduce remote QP poisoning, and 10-micro-meter Cu islands outperform
1-micro-meter Cu films. The improvement is especially important for
limiting the spatial footprint of correlated poisoning rather than the
response of the qubit immediately adjacent to an impact.

## 2. Relevant physical mechanisms

### 2.1 Pair-breaking threshold

A phonon can break a Cooper pair in a superconducting junction only if:

```math
E_{\mathrm{ph}} \geq 2\Delta .
```

The superconducting gap therefore controls:

- whether an incident phonon can generate QPs;
- the number of QPs represented by a given deposited energy; and
- the qubit's electrical and superconducting properties.

An optimizer must not be allowed to increase the gap without accounting for
the associated changes to the qubit design. Gap should be tied to material,
film thickness, and fabrication constraints.

### 2.2 Film absorption and phonon escape

For a superconducting film, the Kaplan model uses an energy-dependent phonon
mean free path of the approximate form:

```math
\ell_{\mathrm{ph}}(E)
=
\frac{v_s \tau_{\mathrm{ph}}}
{1+a(E/\Delta-2)} ,
```

where:

- `v_s` is the film sound speed;
- `tau_ph` is the phonon lifetime;
- `a` is `phononLifetimeSlope`; and
- `Delta` is the superconducting gap.

The probability of absorption increases as the film thickness becomes large
relative to this mean free path. Consequently:

- increasing thickness generally increases capture in that film;
- decreasing phonon lifetime generally increases capture/down-conversion;
- decreasing sound speed generally reduces the mean free path; and
- the lifetime slope matters mainly for phonons appreciably above
  `2 Delta`.

For the present source near `2 Delta_Al`, the lifetime slope is much less
important than thickness, absorption, gap, and the base phonon lifetime. It
can become important for a realistic broad high-energy phonon spectrum.

### 2.3 Substrate scattering versus down-conversion

The Si lattice configuration contains both scattering and decay parameters.
They should be interpreted differently:

- `scat` represents elastic isotope/defect scattering. It changes momentum
  and propagation direction but does not directly remove phonon energy. Its
  effect on poisoning may be non-monotonic.
- `decay` represents anharmonic phonon down-conversion. It reduces the
  population of high-energy pair-breaking phonons by splitting them into
  lower-energy phonons.

The elastic constants, dynamical constants, sound speeds, and Debye
frequency are mutually related properties of the selected substrate. They
should not be independently optimized as if they were unrelated fabrication
knobs.

### 2.4 Boundary absorption and specularity

`WallAbs` represents loss at the diced chip edges or an effective package
boundary. Increasing it can shorten the lifetime of ballistic athermal
phonons.

Specularity controls whether reflected phonons retain a mirror-like
direction or are diffusely redistributed. It can strongly alter:

- phonon caustics;
- the spatial distribution of poisoning;
- the probability of reaching remote qubits; and
- the correlation length of radiation-induced errors.

Specularity may affect the worst-qubit and spatial-footprint objectives more
strongly than the chip-integrated QP total.

## 3. Recommended optimization variables

### 3.1 Primary fabrication/design space

The recommended first Stage 3 design space is:

| Variable | Existing command(s) | Recommended treatment | Physical role |
|---|---|---|---|
| Backside material | `setBotSourceMat` plus associated bottom-film properties | Categorical material preset | Determines phonon-to-electron down-conversion and acoustic properties |
| Backside thickness | `setBotThickness` | Discrete screening, then continuous refinement | Controls probability of phonon down-conversion before escape |
| Backside coverage | Derived from `setIsland` and `setIslandSpacing` | Continuous constrained variable | Controls probability that a phonon encounters the backside film |
| Backside pitch | Derived from `setIsland` and `setIslandSpacing` | Continuous or fabrication-grid variable | Controls pattern scale and spatial interception |
| Junction material | `setTopSourceMat` plus coupled top properties | Categorical preset, or fixed to Al initially | Sets gap and Kaplan-film properties |
| Junction thickness | `setTopThickness` | Continuous with electrical/fabrication constraints | Controls junction-film phonon capture |
| Edge/package absorption | `setWallAbs` | Continuous effective design parameter | Removes phonons at chip/package boundaries |
| Crystal orientation | `setLatticeDeg` or coherent lattice preset | Discrete manufacturable orientations | Controls caustics and poisoning footprint |

### 3.2 Suggested initial ranges

#### Backside thickness

The present range of 0.5--1.5 micro-meters is too narrow to study the
mitigation demonstrated in the reference work.

Use an initial categorical design such as:

```text
no film, 1, 3, 5, and 10 um
```

After identifying a useful range, refine continuously over approximately:

```text
0.5--10 um
```

The `no film` case should use a proper no-film configuration or zero
absorption, rather than relying on a zero-thickness solid if that produces
invalid geometry.

#### Backside coverage and pitch

Let:

```math
p=I+S,
\qquad
f_{\mathrm{cov}}=\left(\frac{I}{I+S}\right)^2 ,
```

where:

- `I` is `setIsland`; and
- `S` is `setIslandSpacing`.

The existing independent bounds roughly span:

```text
coverage = 0.33--0.88
pitch    = 125--375 um
```

A convenient initial optimization space is therefore:

```text
coverage = 0.3--0.9
pitch    = 125--375 um
```

subject to lithography, adhesion, thermal, and microwave constraints.

For a proposed `(coverage, pitch)` pair, convert back to the macro commands:

```math
I=p\sqrt{f_{\mathrm{cov}}},
\qquad
S=p-I .
```

This reparameterization separates the amount of metal from the pattern
length scale and avoids a strongly coupled search in island size and
spacing.

#### Junction thickness

The present:

```text
0.06--0.18 um
```

range is suitable as a first physics range, provided it remains compatible
with qubit fabrication and electrical requirements.

#### Boundary absorption

Retain:

```text
WallAbs = 0--0.5
```

initially. Explore values closer to 1 only if a physical edge coating,
mounting scheme, or phonon sink can plausibly realize the corresponding
loss.

#### Crystal orientation

Use discrete, manufacturable wafer cuts and in-plane rotations rather than
treating every angle as an independently realizable material. A small
screening set such as:

```text
0, 22.5, and 45 degrees
```

can be used first, with the set adjusted for crystal symmetries and the
actual wafer orientation.

### 3.3 Coherent material presets

A material preset should set all mutually dependent properties together.
For example, a bottom-film preset should specify:

- material identity;
- absorption probability or calibrated interface efficiency;
- sound speed;
- film gap or normal-metal treatment;
- gap threshold used for down-conversion;
- phonon lifetime;
- lifetime slope, where applicable;
- QP cutoff fixed to a safe value; and
- subgap absorption, where applicable.

Do not allow the optimizer to create combinations such as:

```text
Cu absorption + Ti sound speed + Nb gap + arbitrary Al lifetime.
```

The existing film presets should be audited before they are used as
categorical variables. In particular,
`Main/macros/FilmPreSets/setBot2Al.mac` is labeled as an Al preset but
currently specifies:

```text
/main/detector_param/setBotSourceMat G4_Ti
```

This inconsistency must be resolved before comparing material categories.

## 4. Mechanistic and calibration parameters

The following parameters are useful for a physics sensitivity study or an
uncertainty model:

- `setTopAbs`;
- `setTopPhLifetime`;
- `setBotAbs`;
- `setBotPhLifetime`;
- substrate `decay`;
- optionally substrate `scat`;
- `setWallPSpecProb`; and
- `setBotPSpecProb`.

These quantities should not automatically be treated as freely
manufacturable design controls.

### 4.1 Film absorption probabilities

`TopAbs` and `BotAbs` are effective probabilities. They may combine:

- acoustic transmission;
- interface quality;
- film coverage;
- microscopic absorption; and
- model calibration.

If no quantitative map exists from fabrication settings to these
probabilities, they should be calibrated against experiment or assigned
uncertainty priors. The optimizer should then seek designs that are robust
over the plausible range rather than choosing an unachievable absorption
value.

### 4.2 Phonon lifetime

Film phonon lifetime is largely a material property. It can depend on:

- material identity;
- disorder;
- film purity;
- microstructure;
- strain;
- temperature; and
- fabrication conditions.

Within one well-characterized material, use a measured uncertainty range.
Across different materials, use material presets instead of independently
varying lifetime and sound speed.

### 4.3 Substrate decay and scattering

Use `decay` as a Stage 3 variable only if substrate engineering is in scope,
for example through:

- material choice;
- isotopic composition;
- defect density;
- strain; or
- engineered phononic structures.

Otherwise, calibrate the Si parameters and hold them fixed.

## 5. Parameters to fix, constrain, or exclude

### 5.1 Source and fidelity parameters

Fix or treat as scenarios:

- `/main/gun/setEnergy`;
- source position and direction;
- source type; and
- `/run/beamOn`.

These describe the radiation/injection environment or simulation fidelity,
not the material design.

If robustness to multiple sources is required, evaluate every material
configuration over a fixed ensemble of source energies, positions, and
directions.

### 5.2 Numerical controls

Do not optimize:

- `/g4cmp/clearance`;
- `/g4cmp/minEPhonons`;
- `/g4cmp/phononBounces`; or
- timeouts and logging settings.

These should be selected through numerical convergence and performance
tests.

### 5.3 QP cascade cutoffs

Do not optimize:

- `setTopQPLim`;
- `setTopFilmQPLim`; or
- `setBotQPLim`.

`QPLim` is an internal cascade cutoff rather than a direct material
property. A value of 1 causes the documented infinite loop in
`G4CMPKaplanQP::AbsorbPhonon`. Fix a validated value, normally 3, and never
use a value below 2 unless the G4CMP implementation is corrected.

### 5.4 Bottom gap threshold

`setBotGapThres` determines the energy scale below which phonons emitted
from the normal-metal down-conversion model are no longer dangerous to the
junction material.

It should be tied consistently to the junction pair-breaking threshold,
not independently optimized as if it were a separate bottom-film material
property.

### 5.5 QPDE parameters

The following parameters are used only in the stage-2 QP-density/decoherence
ODE:

- `f_01`;
- `r`;
- `s`;
- `I_ph`;
- `pt`; and
- `n_cooper`.

For a QP-generation objective, exclude all of them.

For an eventual qubit-damage objective:

- `f_01` is a qubit design/operating quantity;
- `I_ph` and `pt` describe the injection source;
- `n_cooper` is a normalization/material quantity;
- `r` describes recombination; and
- `s` describes QP loss/trapping.

The trapping rate `s` is physically interesting because QP traps, vortices,
and gap engineering can change it. However, the present simulation does not
provide a mapping from a specific trap geometry or material to `s`. Until
such a mapping is developed, treat `s` as a calibrated recovery parameter
or uncertainty scenario rather than a free optimizer control.

### 5.6 Electrode dimensions and positions

Hold the following fixed to a valid qubit design unless electrical
co-optimization is explicitly intended:

- `setWidth`;
- `setHeight`;
- electrode X/Y positions; and
- other functional qubit geometry.

If these are unconstrained, an optimizer can trivially reduce QPs by
shrinking or removing the electrodes, while producing a nonfunctional
device.

### 5.7 Coupled substrate constants

Do not independently optimize:

- `cubic`;
- `stiffness 1 1`;
- `stiffness 1 2`;
- `stiffness 4 4`;
- the four `dyn` constants;
- `Debye`;
- `vsound`; and
- `vtrans`.

For fixed Si, these are material constants with correlated uncertainty. For
substrate selection, use coherent Si, sapphire, or other material lattice
configurations as categorical choices.

### 5.8 Temperature

`/g4cmp/temperature` should normally be fixed at the device operating
temperature and later varied in a robustness analysis.

The present model does not automatically update all superconducting
properties consistently as temperature changes. Optimizing temperature
independently of the temperature dependence of the gap, lifetimes, and
thermal QP population would be physically incomplete.

## 6. Required simulation corrections before optimization

### 6.1 Correct the source/tracking-threshold relationship

For the phonon-injection model, use a fixed source energy above the tracking
threshold.

For radiation mitigation, use a physically representative:

- gamma impact;
- electron-hole cascade; or
- broad high-energy phonon spectrum.

Do not optimize general radiation resilience against only a single phonon
initialized exactly at `2 Delta_Al`.

If the junction gap is varied, require:

```math
E_{\min}^{\mathrm{track}}
\leq
2\min(\Delta_{\mathrm{junction}}).
```

Otherwise, phonons that are physically able to break pairs in low-gap
junctions will be discarded numerically.

The minimum tracking energy should be established by a convergence study:
decrease it until the QP objective and poisoning footprint stop changing
within the required tolerance.

### 6.2 Add explicit random seeds

Add an explicit Geant4 seed command, such as `/random/setSeeds`, to every
generated macro.

For sensitivity and optimization:

1. define a bank of seeds;
2. use the same seed bank for each candidate configuration;
3. pair source locations and energies across candidates;
4. average or model the replicate outcomes; and
5. validate finalists with a new seed bank.

Common random numbers will reduce the variance of comparisons, while fresh
validation seeds protect against overfitting to the original seed bank.

### 6.3 Increase ordinary evaluation fidelity

The current 1e5-event results are too sparse for reliable optimization.
Based on the documented run time of approximately 13 seconds for 1e6
events, use:

- approximately 1e6 events for ordinary candidate evaluation;
- 3--5 replicas evaluated in parallel; and
- approximately 1e7 events or an equivalent precision criterion for final
  validation.

An adaptive stopping rule based on the confidence interval of QPs per event
is preferable to using the same event count for every candidate.

### 6.4 Handle crashes and invalid trials explicitly

Classify each unsuccessful evaluation as one of:

- numerical/geometry invalidity;
- timeout;
- teardown-only segmentation fault with a usable hits file;
- missing/corrupt output; or
- valid zero-QP result.

Do not silently convert every failure to zero. A failure interpreted as
zero would look like an ideal material to a minimization algorithm.

Use a penalty, a feasibility classifier, or an outcome constraint for
invalid configurations.

## 7. Recommended objective functions

### 7.1 Why `total_QPs` is not sufficient

`total_QPs` is a useful diagnostic, but by itself it has limitations:

- it does not distinguish one badly poisoned qubit from many weakly
  poisoned qubits;
- it scales with event count;
- it changes mechanically with the assumed gap through
  `round(E_deposited/Delta)`;
- it does not normalize for electrode volume or Cooper-pair population;
- it does not measure recovery time; and
- it can favor changing functional qubit geometry.

### 7.2 Primary QP-generation objective

A suitable robust primary objective is:

```math
J(\theta)
=
Q_{0.95,\xi}
\left[
\max_e
\frac{
N_{\mathrm{QP},e}(\theta,\xi)
}{
N_{\mathrm{event}}\,n_{\mathrm{cp}}V_e
}
\right],
```

where:

- `theta` is the material/design configuration;
- `xi` indexes seeds, impact locations, directions, and source energies;
- `e` indexes electrodes;
- `N_QP,e` is the generated QP count for electrode `e`;
- `N_event` is the number of simulated primaries;
- `n_cp V_e` is the number of Cooper pairs in that electrode; and
- `Q_0.95` is a high quantile over the radiation/scenario ensemble.

This objective minimizes a robust worst-electrode QP density rather than
only the mean chip-wide count.

If the QP-density normalization is not yet sufficiently calibrated, begin
with:

```math
J_{\mathrm{count}}(\theta)
=
Q_{0.95,\xi}
\left[
\max_e
\frac{N_{\mathrm{QP},e}}{N_{\mathrm{event}}}
\right].
```

### 7.3 Secondary observables

Record all of the following even when only one is used for optimization:

- total QPs per primary event;
- QPs per injected/deposited source energy;
- maximum QPs in one electrode;
- maximum QP density;
- mean and high-quantile QP density;
- time-integrated QP density;
- peak `Delta Gamma`;
- number or fraction of electrodes above a QP threshold;
- number or fraction of qubits with `T1_QP < 10 us`;
- spatial radius or area of the poisoning footprint;
- recovery time below a chosen threshold; and
- simulation cost and failure status.

The `T1_QP < 10 us` footprint follows the type of metric used in the
reference paper and directly reflects the correlated-error risk to a qubit
array.

### 7.4 Multi-objective formulation

If QP suppression competes with device or fabrication requirements, optimize
a Pareto front instead of forcing an arbitrary weighted sum.

Candidate objectives/constraints include:

- QP density or poisoning footprint;
- Cu thickness;
- backside metal coverage;
- added heat capacity;
- microwave loss;
- film stress and adhesion;
- thermalization;
- qubit frequency and anharmonicity;
- junction critical current;
- fabrication complexity; and
- cost/yield.

## 8. Evaluation of optimization algorithms

The Stage 3 problem is:

- expensive relative to ordinary ML inference;
- stochastic and approximately count-valued;
- zero-inflated at insufficient event counts;
- derivative-free;
- mixed continuous/categorical;
- constrained by material consistency and fabrication;
- failure-prone;
- parallelizable; and
- naturally multi-fidelity through event count and replica count.

### 8.1 Noise-aware batch Bayesian optimization

#### Suitability

This is the preferred method for optimizing a low-dimensional continuous
space within one fixed pair of material presets.

Advantages:

- high sample efficiency;
- explicit predictive uncertainty;
- acquisition functions designed for noisy observations;
- batch candidate generation for Mimir;
- support for constraints;
- support for multi-fidelity optimization; and
- straightforward use of replicated observations.

Recommended components:

- Gaussian-process surrogate;
- batch noisy expected improvement, such as `qLogNEI`;
- explicit observation noise estimated from replicas;
- cost-aware acquisition if event count varies;
- constrained acquisition for invalid or nonfabricable designs; and
- a trust-region or sparse/SAAS GP if dimensionality grows.

#### Count and zero-inflation treatment

A standard Gaussian model of raw QP counts may perform poorly. Better
choices include:

1. a two-part or hurdle model:
   - classifier for `P(QP > 0)`; and
   - regression model for `log(QP)` conditional on a positive response;
2. a Poisson or negative-binomial likelihood with event count as exposure;
   or
3. a stabilized transform such as `log1p(QP/event)` with heteroscedastic
   noise.

The first two approaches most directly represent the physical count process.

#### Limitations

- ordinary GPs degrade as the dimension becomes large;
- categorical materials require special kernels or enumeration;
- discontinuities at pair-breaking thresholds can be difficult;
- physically inconsistent inputs must be removed before fitting; and
- low-fidelity false zeros can mislead the acquisition function.

Relevant BoTorch references:

- batch constrained/noisy BO:
  <https://botorch.org/docs/next/tutorials/closed_loop_botorch_only>;
- multi-fidelity BO:
  <https://botorch.org/docs/v0.16.0/tutorials/multi_fidelity_bo>.

### 8.2 SMAC with a random-forest surrogate

#### Suitability

SMAC is the preferred first optimizer if material identity, no-film/film
choices, discrete orientations, integer fabrication settings, or
conditional parameters are included in one search space.

Advantages:

- handles mixed and conditional spaces naturally;
- random forests tolerate discontinuities and interactions;
- less sensitive than a smooth GP to threshold behavior;
- can incorporate instances such as seeds or impact scenarios;
- supports multi-fidelity budgets and Hyperband-style allocation;
- accommodates failed/penalized trials; and
- provides a strong practical baseline.

#### Limitations

- usually less sample-efficient than a well-specified low-dimensional GP on
  a smooth problem;
- predictive uncertainty is less physically interpretable;
- ordinary Hyperband can discard good materials because of low-budget false
  zeros; and
- a generic tree model does not automatically use the count/exposure
  structure.

Use confidence-aware promotion rather than ranking candidates solely by
their raw low-event-count result.

SMAC documentation:

<https://automl.github.io/SMAC3/latest/3_getting_started/>.

### 8.3 CMA-ES

#### Suitability

CMA-ES is a strong independent validation or fallback method for one fixed
material combination and an approximately continuous 5--10 dimensional
space.

Advantages:

- derivative-free;
- robust to nonlinear, nonconvex response surfaces;
- tolerant of moderate noise with resampling;
- naturally evaluates populations in parallel; and
- makes few assumptions about response smoothness.

#### Limitations

- typically needs more simulations than Bayesian optimization;
- categorical variables are awkward;
- hard physical constraints require repair or penalty logic;
- integer variables require rounding; and
- noisy evaluations require repeated samples or increasing population size.

Use CMA-ES to test whether the Bayesian optimizer has converged to a robust
basin rather than as the only primary method.

Reference:

N. Hansen, *The CMA Evolution Strategy: A Tutorial*,
<https://arxiv.org/abs/1604.00772>.

### 8.4 Multi-objective Bayesian optimization and NSGA-II

Use a multi-objective method when QP suppression must be traded against:

- metal thickness/coverage;
- microwave performance;
- qubit electrical properties;
- fabrication complexity; or
- other engineering costs.

#### qLogNEHVI/qNEHVI

Advantages:

- evaluation-efficient for expensive simulations;
- directly models uncertainty in the Pareto front;
- supports noisy objectives; and
- can produce candidates in parallel.

Limitations:

- becomes computationally heavier with many objectives;
- requires careful objective scaling; and
- mixed categorical spaces remain challenging.

BoTorch multi-objective documentation:

<https://botorch.org/docs/v0.16.0/multi_objective>.

#### NSGA-II

Advantages:

- flexible for non-smooth and mixed-variable objectives;
- produces a broad Pareto front; and
- straightforward to parallelize.

Limitations:

- generally much more evaluation-hungry than multi-objective BO;
- needs explicit constraint handling; and
- should be reserved for cases where simulations are cheap enough or a
  reliable surrogate is already available.

### 8.5 Offline tree surrogate and active learning

Random forests or gradient-boosted trees remain useful for:

- diagnosing interactions;
- screening variables;
- learning a feasibility/failure model;
- estimating rough response regions; and
- proposing an initial set of candidates.

However, the existing 6912-point dataset should not be treated as a clean
offline training set for the final Stage 3 objective because:

- half of the gun-energy levels are below the tracking threshold;
- most QP outputs are zero;
- adjacent samples use unpaired random seeds;
- several swept variables cannot affect QP generation;
- material constants are independently varied into potentially
  nonphysical combinations;
- the Stage 3 source and parameter ranges should change; and
- the desired robust, per-electrode objective is different from the
  previous integrated-decoherence target.

The old data can be used as qualitative prior information or for pipeline
debugging. A fresh Stage 3 design is needed for quantitative optimization.

### 8.6 Methods not recommended initially

#### Deep neural networks

The dataset is too small and too noisy relative to the complexity of a
general neural surrogate. The relevant input dimension should also be
reduced enough that GPs or tree models are more data-efficient.

#### Reinforcement learning

The problem is a static black-box design optimization, not a sequential
control problem. Reinforcement learning adds unnecessary complexity.

#### Gradient descent and finite differences

No reliable gradient is available through Geant4/G4CMP, and finite
differences would be dominated by Monte Carlo noise unless each point used
very high statistics and carefully paired seeds.

## 9. Recommended optimization architecture

The cleanest architecture separates categorical material choice from
continuous geometry optimization.

### Outer material comparison

Use:

- a small enumeration of physically coherent material presets; or
- SMAC if the categorical/conditional space is larger.

Example categories:

- backside: no film, Cu, Ti, or another validated down-converter;
- junction/ground-plane system: initially the validated Al/Nb design, with
  alternative systems added only when their qubit behavior is constrained;
- substrate: fixed Si initially, with categorical substrate alternatives
  considered later.

### Inner continuous optimization

For each viable categorical material combination, use batch
noise-aware Bayesian optimization over:

- backside thickness;
- backside coverage;
- backside pitch;
- junction thickness;
- wall absorption;
- a boundary specularity parameter, if included; and
- other genuinely controllable continuous quantities.

### Independent validation

Run CMA-ES over the same continuous space for the best material combination.
Agreement between BO and CMA-ES provides evidence that the result is not
only a surrogate/acquisition artifact.

## 10. Multi-fidelity strategy

Event count is a natural fidelity variable, but it must be handled as
statistical exposure rather than as a raw objective dimension.

Always normalize counts by event count:

```math
\widehat{\lambda}=\frac{N_{\mathrm{QP}}}{N_{\mathrm{event}}}.
```

Record the underlying count and exposure so the optimizer can estimate
uncertainty.

A possible fidelity ladder is:

```text
1e5 -> 1e6 -> 1e7 events
```

with the following cautions:

- `1e5` is currently too sparse to declare a candidate optimal;
- a zero at low fidelity should produce an upper confidence limit, not an
  objective value of exactly zero;
- promotion should depend on uncertainty and expected value;
- replicas and event count can both be increased adaptively; and
- the source-threshold flaw must be fixed before low-fidelity evaluations
  are meaningful.

Since `1e6` is inexpensive on the current installation, it is reasonable to
make `1e6` the normal minimum fidelity and use lower counts only to reject
obviously high-QP or invalid candidates.

## 11. Concrete Stage 3 workflow

### Step 1: Correct and validate the physics configuration

1. Fix the gun-energy/minimum-tracking-energy relationship.
2. Add explicit seed control.
3. Select the intended source model:
   - injection experiment; or
   - realistic radiation impact.
4. Audit material presets and fix inconsistent material names/properties.
5. Tie `BotGapThres` consistently to the junction gap.
6. Fix all QPLim values at a validated value of at least 2.
7. Perform convergence tests for:
   - `minEPhonons`;
   - `phononBounces`;
   - event count; and
   - QP time binning.

### Step 2: Define the design and nuisance variables

Design variables:

- material presets;
- film thicknesses;
- backside coverage and pitch;
- boundary/package design; and
- crystal orientation.

Nuisance/scenario variables:

- impact position;
- impact/source energy;
- direction;
- random seed;
- calibrated absorption/lifetime uncertainties; and
- operating temperature.

### Step 3: Implement Stage 3 observables

Add per-sample output for:

- QPs per event;
- per-electrode QP count and density;
- maximum-electrode QP response;
- poisoning footprint;
- threshold-crossing fraction;
- recovery metrics; and
- failure/validity status.

Keep the raw counts, normalization values, and scenario identifiers so
results can be reanalyzed without rerunning Geant4.

### Step 4: Fresh replicated screening

Use a new Sobol, Morris, or space-filling design over the reduced physical
space.

Requirements:

- 1e6 events or an equivalent precision target;
- shared seed bank across configurations;
- multiple replicas;
- no QPDE parameters in the generation screen;
- material consistency constraints; and
- separate reporting of zero probability and positive-response magnitude.

The purpose is to verify that the nominal eight-dimensional space can be
reduced further and to identify important interactions.

### Step 5: Mixed material search

Use enumeration or SMAC to compare coherent material presets and discard:

- physically invalid systems;
- designs violating qubit constraints;
- configurations with unacceptable fabrication cost; and
- poorly performing material families.

### Step 6: Continuous Bayesian optimization

For each promising material family:

1. fit a noise-aware surrogate;
2. propose a batch of candidates;
3. run candidate replicas in parallel;
4. update the surrogate;
5. adapt fidelity based on uncertainty;
6. monitor convergence and model calibration; and
7. retain multiple competitive solutions, not only one incumbent.

### Step 7: Robust validation

Validate the best candidates using:

- fresh random seeds;
- higher event counts;
- multiple source positions;
- multiple source energies/types;
- material-property uncertainty;
- manufacturing tolerances; and
- the same functional qubit constraints.

Report confidence intervals and high-quantile performance, not only the
best observed simulation.

### Step 8: Independent optimizer cross-check

Run CMA-ES or a dense local design around the best BO configuration. If the
two methods disagree substantially, investigate:

- surrogate misspecification;
- excessive noise;
- unmodeled constraints;
- a threshold/discontinuity; or
- insufficient local sampling.

### Step 9: Multi-objective extension

If required, use qLogNEHVI/qNEHVI or NSGA-II to generate a Pareto front for
QP mitigation versus qubit/fabrication objectives.

## 12. Proposed minimal first optimization

For the first fully operational Stage 3 optimization, keep the validated
top junction/ground-plane material system fixed and optimize only:

1. backside Cu thickness;
2. backside coverage;
3. backside pitch;
4. wall absorption;
5. one boundary specularity parameter;
6. junction Al thickness, within electrical constraints; and
7. discrete crystal orientation.

Use:

- a realistic source above the tracking threshold;
- 1e6 events;
- at least three paired-seed replicas;
- a robust maximum-electrode QP-density objective;
- fixed QPLim values;
- no QPDE variables; and
- batch noisy Bayesian optimization.

This restricted problem is small enough for reliable BO, directly connected
to fabrication, and centered on the mitigation mechanism already supported
by the reference experiment.

After it is stable:

1. add backside material identity;
2. add calibrated absorption/lifetime uncertainty;
3. add realistic high-energy ground-plane physics;
4. add QP recovery/trap design; and
5. move to a multi-objective qubit-performance optimization.

## 13. Main conclusions

1. The highest-value design direction is backside phonon down-conversion,
   especially film thickness, coverage, pitch, and coherent material
   selection.
2. Junction gap, absorption, thickness, and phonon lifetime are directly
   related to QP generation, but they must obey material and qubit
   constraints.
3. The current top Nb film is inactive for the present source energy and
   should not consume optimizer dimensions until a realistic high-energy
   source is used.
4. Numerical controls, QPLim, source parameters, and QPDE parameters should
   not be mixed with material controls in the QP-generation optimizer.
5. The existing Morris QP sensitivity is not quantitatively reliable
   because the samples do not use paired random seeds and the QP counts are
   extremely sparse.
6. `total_QPs` is already junction-localized, but a robust per-electrode QP
   density or poisoning-footprint objective is better aligned with qubit
   damage.
7. Use SMAC or enumeration for material categories, batch noise-aware
   Bayesian optimization for the reduced continuous space, and CMA-ES as an
   independent cross-check.
8. Do not warm-start the final optimizer blindly with the old 6912
   observations. Generate a new, replicated Stage 3 dataset after correcting
   the source threshold and seed policy.

