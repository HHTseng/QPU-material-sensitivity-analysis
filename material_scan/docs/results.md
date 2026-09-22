# Experiments and current conclusions

Last updated: 2026-09-22.

## What has been completed

### Linked real-material screen

The first material study simulated all 18 combinations of three substrates
(Si, Ge, GaAs), three upper films (Nb, Ta, Ti), and two lower films (Cu, Au).
Each candidate used 4,000,000 primaries, 16 common injection sites, and two
replicas, for 576 complete simulation tasks.

The strongest stable result is the lower film: Cu beat Au in all nine matched
comparisons. The nominal best combination was GaAs/Nb/Cu at
`2.385e-4` junction quasiparticles per primary, 39.5% below Si/Nb/Cu.
Ge/Nb/Cu was only 1.9% higher, so GaAs versus Ge was unresolved. This screen
ranked linked material property sets; it did not identify a single causal
constant.

### Continuous-property search before the device correction

A corrected equal-compute comparison ran 96 evaluations for each method at
4,000,000 primaries per candidate, 16 sites, and eight replicas:

| Method | Best value | Median value | Values below `1.15e-4` |
|---|---:|---:|---:|
| Gaussian-process Bayesian search | `5.60e-5` | `1.08e-4` | 50/96 |
| CMA-ES | `5.30e-5` | `1.64e-4` | 28/96 |
| Sobol | `7.35e-5` | `3.62e-4` | 2/96 |
| random | `1.02e-4` | `4.02e-4` | 1/96 |

The adaptive methods clearly concentrated evaluations in lower-response
regions. They are not ranked against each other: their best values overlap the
measured uncertainty and only one optimizer seed was run. The comparison used
69 genuine Gaussian-process acquisition points and eight complete CMA-ES
generations, fixing the earlier source-label errors.

Sixteen fresh reference evaluations had a coefficient of variation of 0.25%,
well below their roughly 5% statistical error. No run-time drift was detected.

The unconstrained winners are not useful material designs. They sit on many
search limits and lower the upper-film gap to a modeled critical temperature
near 0.33 K. A direct gap requirement of `1.5384e-3 eV` keeps 69 of 402 measured
points. The best surviving point has `J = 6.55e-5 ± 1.24e-5`, only 24% above
the unconstrained value, but five coordinates still lie on search limits. It
is a useful starting point, not an optimum or a real material.

### Agentic-search implementation status

The `Agentic_Material_optimization` branch adds a guarded Ollama/GP method. A
pinned Qwen model proposes a diverse candidate pool from the versioned physics,
measured values with uncertainty, and recorded failures; strict code validates
the pool and the existing Gaussian process chooses the candidate. The language
model cannot supply objective values or change the experiment.

The parser, GP selection, saved source record, strict failure behavior, and
network-free transcript replay pass mocked tests. The exact production model
passed its four-GPU, 65,536-token-context residency preflight. All three
120-point searches then completed: seeds 101, 202, and 303 reached
`3.654020e-4`, `4.290081e-4`, and `3.870108e-4`, improving the common incumbent
by 27.8%, 15.3%, and 23.5%. No GP fallback was used. Three pathological
simulation attempts were censored by the recorded 600-second per-task policy.

The agentic median best value was `3.870108e-4`, 52.5% higher than the CMA-ES
median `2.537300e-4` at the same 120-point budget. Agentic search therefore
improved the common incumbent in every seed but did not outperform CMA-ES. No
agentic finalist was promoted to costly full-spatial confirmation. See the
[current comparison](../experiments/agentic-comparison-status.md) and
[preflight record](../experiments/agentic-preflight.json).

### Corrected real-material projection

An earlier projection accidentally ran several substrates with silicon
density. Corrected simulations now preserve the chosen Geant4 material,
measured density, full lattice record, and unclipped measured elastic
constants.

Under the old 16-site quantity, the comparison converged between
32,000,000 and 320,000,000 total primaries per candidate:

| Candidate | Junction-QP change from reference | Fraction of ideal reduction |
|---|---:|---:|
| ideal property target | -70.0% | 100% |
| SiC elasticity | -53.3% | 76.2% |
| GaAs/Nb/Cu | -31.2% | 44.6% |
| Be₂C elasticity | -52.0% at 32,000,000 primaries | 73.6% |

Bracketing SiC's unmeasured scattering and decay constants across the measured
G4CMP range changed its recovered fraction only from 73.8% to 76.9%. Thus the
borrowed values did not control that result. Their true literature or
first-principles values should still be obtained before a fabrication claim.

Be₃N₂ and BP have not been simulated. Their densities cannot be represented
within 3% by the available named Geant4 materials. Supporting them requires an
explicit material-density construction in the detector code and a new
executable identity.

### Spatial integration study

Uniform whole-chip Sobol sites did not converge because isolated sites landed
very near or on electrodes. One of 16 sites supplied about half the reference
yield; dropping it changed the SiC reduction from -53.3% to -26.2%.

Two earlier proximity studies used 0.2 mm discs around electrode centres as
their innermost region. That disc has about 1,254 times the area of the actual
10 µm by 10 µm junction rectangle, so those studies cannot establish spatial
convergence. Their site data remains useful: it was reassigned to the corrected
rectangle-distance regions when estimating the allocation below.

The corrected study samples five geometry-derived regions separately and
weights them by true area. It first ran 128, 256, and 512 nested sites with
31,250 source phonons per site-replica task. Targeted extensions then increased
only regions that failed their separate 5% checks. The sequence was 871,
1,245, 1,617, and finally 2,361 sites, always with eight replicas. Exact
completed tasks were reused at every extension.

The 2,361-site design contains 18,888 tasks and 590,250,000 source phonons per
point. All three matched points pass the overall-change, combined-uncertainty,
paired-change, maximum-site, region-change, and ranking checks:

| Point | Final objective | Change from 1,617 sites | Largest site share |
|---|---:|---:|---:|
| reference | `2.348403e-3 ± 4.65e-5` | -0.28% | 0.76% |
| SiC elasticity | `1.197658e-3 ± 3.39e-5` | -0.01% | 0.58% |
| niobium-gap-compatible point | `4.398849e-4 ± 1.71e-5` | +0.24% | 0.67% |

The 512-site work finished on 2026-09-09 at 08:05. The two obsolete unfinished
database records were deleted by the user. A partial low-gap corner attempt is
not assigned a 512-site value and is not needed under the direct gap rule.

The final paired results put SiC `49.0% ± 1.7` percentage points below the
reference and the niobium-gap-compatible point `81.3% ± 1.8` points below it.
The ranking is stable at every recorded spatial level:

`niobium-gap-compatible point < SiC elasticity < reference`.

These are comparisons of complete parameter vectors, not isolated substrate
effects. In particular, the point labelled SiC elasticity retains an
upper-film gap of `5.4e-4 eV`, below the direct niobium-compatible limit of
`1.5384e-3 eV`, as well as different film values and a different temperature.
Its reduction therefore validates this convergence anchor but is not a
feasible SiC/niobium device claim. The niobium-gap-compatible point satisfies
the direct gap limit but remains a pseudo-material vector, not a named
compound.

The last extension changed only the 0.20–0.50 mm region, from 744 to 1,488
sites. Its final changes, together with the unchanged regions, are:

| Point | within 0.05 mm | 0.20–0.50 mm | remaining area |
|---|---:|---:|---:|
| reference | 0.0% | -1.1% | 0.0% |
| SiC elasticity | 0.0% | -0.03% | 0.0% |
| niobium-gap-compatible point | 0.0% | +1.0% | 0.0% |

The spatial objective is therefore converged under the declared checks.

For material-search screening, the 128-site/four-replica subset preserves the
same ranking. Relative to the final result, it differs by +1.1% for the
reference, +5.3% for SiC, and -0.5% for the niobium-compatible point. It is
used only to find candidates; final claims use the complete spatial design and
unused random replicas.

The current execution path was also checked against every task in the retained
512-site prefix. Rare task-level Geant4/G4CMP variation is present, but its
aggregate effect is negligible:

| Point | Exact task QP totals | Weighted-objective replay change |
|---|---:|---:|
| reference | 3,864/4,096 | -0.0018% |
| SiC elasticity | 3,782/4,096 | -0.0236% |
| niobium-gap-compatible point | 4,053/4,096 | -0.1168% |

Each replay change is far below that point's statistical error. The live logs
also confirm `G4_Si` for the reference and niobium-compatible point and
`G4_CALCIUM_FLUORIDE` for SiC, closing the earlier carrier error.

Header-only physical-zero files reflect low yield rather than missing output:
every declared task completed, and each point's largest site supplies less
than 0.8% of the objective.

### Gap-compatible material search with the device-weighted objective

The new search fixes temperature at 20 mK, requires an upper-film gap of at
least `1.5384e-3 eV`, and varies 14 material properties. Every method began
with the same 32 newly simulated points. Each later screening point used 128
sites, four replicas, and 16,000,000 source phonons.

Three independent CMA-ES runs each evaluated 120 points: ten complete
generations with 12 members per generation. Random sampling completed 64
points. Sobol sampling completed 49 of 64 points; its fiftieth point had no
completed task after one hour and was stopped without assigning an objective.

| Search | Completed points | Best newly proposed value | Change from common best |
|---|---:|---:|---:|
| CMA-ES, seed 101 | 120/120 | `2.595938e-4 ± 5.57e-5` | -48.7% |
| CMA-ES, seed 202 | 120/120 | `2.537300e-4 ± 5.26e-5` | -49.9% |
| CMA-ES, seed 303 | 120/120 | `2.321996e-4 ± 4.30e-5` | -54.1% |
| random, seed 101 | 64/64 | `7.252207e-4 ± 9.97e-5` | did not improve it |
| Sobol, seed 101 | 49/64 | `7.566498e-4 ± 7.67e-5` | did not improve it |

More CMA-ES steps were useful. Comparing step 69 with step 120, the incumbent
improved by 0.77%, 29.20%, and 0.31% for seeds 101, 202, and 303. The median
incumbent improved by 3.01%. Seed 202 found its best point at step 108, so the
earlier 69-point stopping point would have missed a confirmed finalist.

The expected-improvement Gaussian-process run used a Matérn-5/2 covariance
with one length scale per parameter, log-transformed objective values,
measured unequal noise, refitting every five observations, 16,384 candidate
points, and local refinement of the leading 16. Its first proposal selected
the exact minimum substrate-decay value and several other search faces. None
of its 512 tasks finished in one hour with 60 tasks running simultaneously.
That gives a lower bound of 8.53 hours for this one screening point and 42.7
days for 120 similar points. The run was stopped with zero new observations;
the stalled point was not treated as a favorable result.

Consequently, this experiment cannot answer whether Gaussian-process steps
70–120 would help. The present acquisition rule over the widened range first
needs a physically and computationally feasible region. Simply requesting
more steps would repeat an unevaluable choice rather than explore the material
space.

The common anchor and the best point from each CMA-ES seed were then repeated
with the complete 2,361-site design, eight unused replicas, and 590,250,000
source phonons per point:

| Point | Full-spatial objective | Paired change from compatible anchor |
|---|---:|---:|
| compatible anchor | `5.936004e-4 ± 2.09e-5` | -- |
| CMA-ES seed 101 | `3.118155e-4 ± 1.32e-5` | -47.5% |
| CMA-ES seed 202 | `3.638355e-4 ± 1.55e-5` | -38.7% |
| CMA-ES seed 303 | `3.068781e-4 ± 1.80e-5` | -48.3% |

All three CMA-ES reductions are resolved in paired comparisons. Seeds 101 and
303 are not resolved from each other: their difference is 1.6%, with a 95%
interval from -10.5% to +13.7% relative to the seed-303 value. Both are
resolved below seed 202. The result is therefore a leading region represented
by seeds 101 and 303, not a unique winning vector.

Every confirmed CMA-ES finalist has `sub_c44 = 5 GPa`, the exact lower search
limit. Other film lifetime, scattering, elastic, or sound-speed values also
touch limits. These vectors show where this model decreases the objective, but
they do not establish an interior optimum or a fabricable material.

An additional repeat exposed uncertainty not captured by a single random-number
set. The identical compatible anchor was `4.398849e-4 ± 1.71e-5` in the spatial
study and `5.936004e-4 ± 2.09e-5` in confirmation: a 34.9% change, about 5.7
times the combined reported standard error. The simultaneous reference changed
only 0.29%. The paired CMA-ES comparisons within confirmation remain valid, but
absolute low-yield values and their ordering across independent random-number
sets need another repeat.

The completed common points, searches, and confirmations used 11,031,250,000
source phonons. Interrupted tasks are excluded. The checked numerical records
are [search results](../experiments/material-search-results.json),
[confirmation results](../experiments/material-search-confirmation-results.json),
[Gaussian-process attempt](../experiments/material-search-bo-attempt.json), and
[Sobol attempt](../experiments/material-search-sobol-attempt.json). See the
[search curve](figures/material-search-best.png).

### Hit-file checks

At 31,250 primaries per task, all three stratified levels have a median of four
hit rows per file, or 128 hit rows per million primaries. About 22% of files are
header-only physical zeros. Normalized medians are stable by spatial region,
so there is no sign of a general output or missing-hit failure.

Sparse far-from-electrode files limit upper-tail precision before they threaten
the weighted mean. Raw row counts are not independent primary-event counts and
cannot alone establish precision. The most useful plots are:

- [all-study overview](figures/hit-overview.png)
- [128-site study](figures/hit-spatial-128.png)
- [256-site study](figures/hit-spatial-256.png)
- [512-site study](figures/hit-spatial-512.png)
- [512-site reference](figures/hit-reference-512.png)
- [512-site SiC](figures/hit-sic-512.png)

Regenerate these figures with
`conda run -n G4CMP python -m material_scan.tools.plot_hits`.

### Continuous crystal-direction pilot

The crystal-direction pilot compared the former 13 integer directions with 16
uniform sphere points. Every candidate kept the reference material values,
16 injection sites, seed set 41, and 4,000,000 total primaries. All 29 unique
simulations and 3,712 site-replica tasks completed; `[001]` and the named
reference were the same input and correctly shared one result.

| Direction set | Minimum | Median | Maximum |
|---|---:|---:|---:|
| former 13 directions | `3.695e-4` | `3.765e-4` | `3.770e-4` |
| 16 uniform sphere points | `3.750e-4` | `3.760e-4` | `3.775e-4` |

The continuous median is 0.13% lower, while its best point is 1.49% higher than
the best former direction. Both differences are far below the approximately
6.6% uncertainty of an individual candidate, and matched comparison scores are
near zero. No directional improvement is resolved. Keep the continuous
coordinates because they represent the intended space and work correctly in
the Bayesian covariance function, but do not spend a separate high-event
direction study unless a later optimized material shows strong direction
sensitivity. See the [comparison plot](../experiments/orientation-pilot/comparison.png)
the [tested directions](../experiments/orientation-pilot/points.json), and the
[checked numerical summary](../experiments/orientation-pilot/summary.json).
The complete local JSON and SQLite files are retained beside that summary; its
checksums identify them without rewriting their original field names.

## Current defensible conclusions

1. Cu is more favorable than Au in every matched linked-material comparison
   under this model.
2. The device-weighted spatial objective passes the declared convergence
   checks at 2,361 sites and eight replicas.
3. Three 120-point CMA-ES runs found candidates that remain 38.7% to 48.3%
   below the compatible anchor in paired full-spatial confirmation. Seeds 101
   and 303 describe a statistically tied leading region.
4. Ten complete CMA-ES generations were worthwhile: one seed found a much
   better point after step 100. The present expected-improvement
   Gaussian-process search cannot yet be assessed because its first proposed
   point was not computationally evaluable.
5. The unconstrained lowest-gap corner is not a credible device design. The
   modeled niobium gap should be a direct lower bound.
6. Corrected SiC elastic properties remain promising, but the current SiC
   anchor changes film properties and temperature and violates the direct
   niobium-compatible gap limit. Its reduction is not an isolated or feasible
   SiC substrate claim.
7. All confirmed CMA-ES finalists touch multiple search limits, and every one
   uses the lowest allowed `sub_c44`. They are pseudo-material directions, not
   an interior or fabricable optimum.
8. The 34.9% compatible-anchor shift between independent random-number sets
   shows that the current single-run standard error does not describe all
   repeat-to-repeat variation for sparse low-hit candidates.
9. The reported upper-tail quantity is diagnostic only until its site-level
   interval and an external engineering limit are available.
10. Three 120-point agentic runs improved the common incumbent by a median
    23.5% without fallback, but their median best value remained 52.5% above
    CMA-ES. The current comparison does not show agentic superiority.
11. No fabrication optimum or logical-error reduction has been established.

## Recommended order of new work

1. Repeat the compatible anchor and the tied CMA-ES 101/303 finalists with a
   second unused random-number set. Reusing the converged sites is sufficient.
   Report variation between random-number sets as well as variation within one
   set before selecting a final property vector.
2. Replace the independent rectangular ranges near the leading region with
   physically supported material relations and fabrication limits. First
   investigate the shared `sub_c44 = 5 GPa` face and coupled elastic stability;
   do not widen an arbitrary lower limit merely because the search reached it.
3. Measure one-dimensional profiles and paired finite differences around the
   tied 101/303 region. This will identify which limit-touching parameters
   control the reduction and which are incidental.
4. Continue CMA-ES as the primary numerical search with at least three seeds
   and 8–10 complete generations. For Gaussian-process search, use a local
   feasible region and reject or model measured run time before expected
   improvement is evaluated. Do not convert timeouts into low-QP observations.
5. Run the agentic seeds 101, 202, and 303 for 120 points each under the retained
   no-timeout policy before making an agentic-versus-CMA claim. Confirm only a
   genuinely competitive agentic point with unused seeds and the 2,361-site
   design.
6. Obtain SiC scattering, total decay, and transverse-transverse decay values
   from literature or first-principles calculation before spending extensive
   simulation time on SiC systematics.
7. Project the leading physically allowed region onto named compounds. Add the
   explicit density construction before testing Be3N2 or BP.
8. Before fabrication guidance, bracket film lifetimes, test an independently
   justified interface model, and repeat the leading comparisons with
   additional random-number sets. Revisit crystal direction only if those
   checks reveal meaningful direction sensitivity.

Do the SiC constant search before extensive model-systematic simulation on SiC:
new physical values could change which candidate deserves that expensive work.
