# Experiments and current conclusions

Last updated: 2026-09-14.

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
weights them by true area. It ran 128, 256, and 512 nested sites with 31,250
primaries for every site-replica task. Current values are:

| Candidate | 128 sites | 256 sites | 512 sites |
|---|---:|---:|---:|
| reference | `2.4292e-3` | `2.3980e-3` | `2.4155e-3` |
| SiC elasticity | `1.0871e-3` | `1.1764e-3` | `1.2561e-3` |
| niobium-gap-compatible point | `3.9432e-4` | `4.6706e-4` | `4.4877e-4` |
| ideal target | `8.1515e-4` | `8.0900e-4` | `8.3134e-4` |

The 512-site work finished on 2026-09-09 at 08:05. The two obsolete unfinished
database records were deleted by the user. A partial low-gap corner attempt is
not assigned a 512-site value and is not needed under the direct gap rule.

Overall reference, ideal-target, and niobium-gap-compatible values are stable
from 256 to 512 within the stated checks. SiC moves 6.3%, exceeding the 5%
condition, and the regions within 0.05 mm, 0.20–0.50 mm, and the remaining
device area still fail their separate convergence checks. The objective is
therefore not yet declared spatially converged.

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
2. Adaptive search reaches low simulated junction-QP regions far more often
   than random or Sobol sampling. Gaussian-process search versus CMA-ES remains
   unresolved.
3. The unconstrained lowest-gap corner is not a credible device design. The
   modeled niobium gap should be a direct lower bound.
4. Corrected SiC elastic properties remain promising, but its old 16-site
   percentage is not a device-average claim.
5. Geometry-aware weighting fixed the known electrode-centre and equal-site
   biases. Spatial refinement is still needed for SiC and three regions.
6. The reported upper-tail quantity is diagnostic only until its site-level
   interval and an external engineering limit are available.
7. No fabrication optimum or logical-error reduction has been established.

## Recommended order of new work

1. Add nested sites only in the three spatial regions that failed. Keep 31,250
   primaries per task and the same paired seeds. Recheck SiC and the three
   stable anchor points.
2. Obtain SiC scattering, total decay, and transverse-transverse decay values
   from literature or first-principles calculation.
3. Fix operating temperature and impose the direct niobium gap bound. Then
   construct a physically supported wider range around the best compatible
   point.
4. Re-evaluate 16–24 diverse compatible earlier points under the final spatial
   definition, and add new Sobol points for the opened ranges.
5. Run at least three seeds each for Gaussian-process search and CMA-ES, with
   the same 96 evaluations per seed. Keep Sobol and random references.
6. Confirm the leading feasible points with unused seeds and higher event
   counts.
7. Before fabrication guidance, compare 16, 32, and 64 replicas or an equivalent
   uncertainty design, bracket film lifetimes, and test an independently
   justified interface model.

Do the SiC constant search before extensive model-systematic simulation on SiC:
new physical values could change which candidate deserves that expensive work.
