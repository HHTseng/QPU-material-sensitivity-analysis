# Historical experiments and interpretation

Historical measurements through 2026-09-14; source and interpretation notes
updated 2026-10-15.

The measurements below describe the earlier source revision. A later inherited
2,361-site comparison and CMA-ES confirmations are distinguished below. The
earlier measurements are archived outside the working tree. They are not
responses from the fresh queue. The source rewrite does not rerun them or
independently validate their material predictions. Current commands use
`python scan.py`. [Numerical verification](../../verification/round2_results.json)
describes the software comparison separately.

The 16-site tables use summed junction QPs per primary. The spatial-integration
tables use equal-junction-weighted QPs per injected eV. Their values cannot be
compared directly. The substrate is 10 × 10 mm², but the recorded injection
region is 8 × 8 mm²; references to spatial convergence apply to that region.

## Earlier completed studies

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

The earlier report lists 96 responses per method, nominally at 4,000,000
primaries per candidate, 16 sites and eight replicas:

| Method | Best value | Median value | Values below `1.15e-4` |
|---|---:|---:|---:|
| Gaussian-process Bayesian search | `5.60e-5` | `1.08e-4` | 50/96 |
| CMA-ES | `5.30e-5` | `1.64e-4` | 28/96 |
| Sobol | `7.35e-5` | `3.62e-4` | 2/96 |
| random | `1.02e-4` | `4.02e-4` | 1/96 |

The adaptive methods concentrated responses in lower-response regions. They
are not ranked against each other: their best values overlap the measured
uncertainty and only one optimizer seed was run. The reports identify 69 GP
acquisition points and eight CMA-ES generations. However, recorded primary use
and accumulated ledger success counts differ between methods; 96 response
entries do not establish 96 fresh independent evaluations or equal computation.
The [current audit](optimization.md) retains this limitation and distinguishes
the later physically constrained search.

Sixteen fresh reference evaluations had a coefficient of variation of 0.25%,
well below their roughly 5% statistical error. No run-time drift was detected.

The unconstrained winners are not useful material designs. They sit on many
search limits and lower the upper-film gap to a modeled critical temperature
near 0.33 K. A direct gap requirement of `1.5384e-3 eV` keeps 69 of 402 measured
points. The best surviving point has `6.55e-5 ± 1.24e-5` summed junction QPs
per primary, only 24% above
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

Uniform Sobol injection over the declared 8 × 8 mm² region did not converge
because isolated sites landed very near or on junctions. One of 16 sites
supplied about half the reference yield; dropping it changed the SiC reduction
from -53.3% to -26.2%. These sites did not sample the outer 36 mm² of the chip.

Two earlier proximity studies used 0.2 mm discs around electrode centres as
their innermost region. That disc has about 1,254 times the area of the actual
10 µm by 10 µm junction rectangle, so those studies cannot establish spatial
convergence. Their site data remains useful: it was reassigned to the corrected
rectangle-distance regions when estimating the allocation below.

The corrected study samples five geometry-derived regions separately and
weights them by their fraction of the 64 mm² injection area. It ran 128, 256,
and 512 nested sites with 31,250 primaries for every site-replica task.
Historical values below are weighted junction QPs per injected eV:

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
injection area still fail their separate convergence checks. At this 512-site
level, the objective was therefore not declared spatially converged.

### Hit-file checks

At 31,250 primaries per task, all three stratified levels have a median of four
hit rows per file, or 128 hit rows per million primaries. About 22% of files are
header-only physical zeros. Normalized medians are stable by spatial region,
so there is no sign of a general output or missing-hit failure.

Sparse far-from-electrode files limit upper-tail precision before they threaten
the weighted mean. Raw row counts are not independent primary-event counts and
cannot alone establish precision. Historical diagnostics included an all-study
overview, 128/256/512-site studies, and reference/SiC comparisons. These images
belong to the historical archive and earlier Git revisions; they are not
figures from the fresh queue.
For a current recorded run, `python scan.py plot RUN --kind hits --output FIGURE`
plots its measured hit counts; it does not reconstruct the historical combined
figures from unrecorded inputs.

### Continuous crystal-direction pilot

The crystal-direction pilot compared the former 13 integer directions with 16
uniform sphere points. Every candidate kept the reference material values,
16 injection sites, seed set 41, and 4,000,000 total primaries. All 29 unique
simulations and 3,712 site-replica tasks completed; `[001]` and the named
reference were the same input and correctly shared one result.

The following responses are summed junction QPs per primary.

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
sensitivity. The comparison plot, direction inputs and numerical summary are
in the historical archive. Their recorded checksums identify the original
JSON and SQLite files; these files are excluded from the fresh results.

## Later inherited spatial results

Archived reports originally under `material_scan_data/experiments/spatial-refinement-2361/`
state that refinement from 1,617 to 2,361 sites passed the declared overall and
region-level checks for the reference, SiC-elasticity, and Nb-gap-compatible
anchors. Their retained manifests all identify spatial design
`8d6f9b224ce5f834`, eight realizations, 31,250 primaries per task, and
`half_span_mm = 4`. This is convergence for the declared 8 × 8 mm² injection
region, not the full 10 × 10 mm² substrate or a different source distribution.
The manifests use the same area fractions as the earlier 512-site design.

Later CMA-ES confirmations used 2,361 sites and unused random streams. The
leaders from optimizer seeds 101 and 303 remain statistically unresolved;
the finalists reach the `C44 = 5 GPa` lower search limit. These local reports
are inherited evidence, not experiments repeated by the rewrite. Check their
source identities, recorded inputs, and raw-task completeness before reusing
them for a new study. Neither a boundary minimum nor convergence under one
source model establishes a fabricable material optimum.

## Conclusions from the earlier experiments

1. Cu is more favorable than Au in every matched linked-material comparison
   under this model.
2. Adaptive search reaches low simulated junction-QP regions far more often
   than random or Sobol sampling. Gaussian-process search versus CMA-ES remains
   unresolved.
3. The unconstrained lowest-gap corner is not a credible device design. The
   modeled niobium gap should be a direct lower bound.
4. Corrected SiC elastic properties remain promising, but its old 16-site
   percentage is not a device-average claim.
5. Geometry-aware weighting fixed the known junction-centre and equal-site
   biases. The 512-site study needed refinement; the later inherited report
   states convergence at 2,361 sites within the declared injection region.
   Whole-chip injection remains a separate untested spatial assumption.
6. The reported upper-tail quantity is diagnostic only until its site-level
   interval and an external engineering limit are available.
7. No fabrication optimum or logical-error reduction has been established.

## Fresh comparisons completed on 2026-10-15

The [queue definition](../../studies/full_scale/queue.yaml) now limits source
designs to 1,024 sites and stops for review after spatial and primary-count
comparisons. All ten measured studies finished at 10:38 EDT on October 15:
160,000 completed tasks and 13.832 billion primaries in 11 h 43 min. Twenty
study-archive copies match their recorded checksums. The unfinished 2,653-site
study was cancelled; its completed measurements remain separately preserved.

Four independent 1,024-site layouts give $J=0.00234472$ QPs/eV for the reference,
$0.00223919$ for the predefined $C_{44}=1$ GPa test and $0.00154571$ for the
SiC-derived model. Their paired reductions are 4.50% and 34.08%, respectively.
These test points were specified before transport and are not optimizer gains.
Primary-count checks passed; spatial equivalence remains unconfirmed because
some total and regional intervals exceed the declared ±5% margin.

The [completed summary](../../docs/results/initial_checks_summary.md) and
[numerical checks](../../verification/initial_1024_results.json) give errors,
intervals, counts and archive verification. Further search, sensitivity,
linked-material, tolerance and model-range studies are deferred. The
[optimization assessment](optimization.md) separates historical algorithm gains
from these new measurements and proposes comparisons within the current cap.

The SiC literature check did not establish the required measured 20 mK acoustic
coefficients. The new ranges therefore remain explicit assumptions. MP
elasticity and density are DFT calculations; verified native phonon fields and
curated film fields are kept separate when comparing property targets.

## Earlier recommended order of new work

This historical order predates the 1,024-site cap. The current proposal is in
[optimization.md](optimization.md); no larger design or later study starts
automatically.

1. Verify the retained definitions and task completeness of the 2,361-site
   result before reusing its converged design. If the physical source should
   cover the whole chip, construct new region weights and sites for that area
   and check convergence again.
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
