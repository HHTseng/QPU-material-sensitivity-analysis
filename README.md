# Material optimization for phonon-mediated quasiparticles

This branch, `Material_optimization_v3_scan_parameters_revised`, uses Geant4
and G4CMP to find substrate and film properties that reduce quasiparticle (QP)
generation in a superconducting-qubit device. It contains the compact,
deterministic simulation and optimization path. The separate
`Agentic_Material_optimization` branch tests local-language-model proposals on
the same numerical problem.

The numerical summary and comparison figure below are a checked snapshot from
commit `5c1fe2c` of that branch. The spatial calculation and traditional
optimizers use the same physical definitions descended from this branch; only
the language-model proposal method is absent here.

## Scientific quantity

Let

- $x \in \mathcal X \subset \mathbb R^{14}$ be a material-property vector;
- $\xi$ be a sampled injection position;
- $\omega$ be an independent phonon-transport realization;
- $Q_e(x,\xi,\omega)$ be the QPs from one source phonon that reach electrode $e$;
- $w_e$ be fixed electrode weights, with $\sum_e w_e=1$;
- $E_{\mathrm{gun}}=10\,\mathrm{meV}$ be the injected phonon energy.

The simulation maps $x$ to the non-negative device response
$J:\mathcal X\rightarrow\mathbb R_{\geq0}$,

$$
J(x)=\mathbb E_{\xi,\omega}\left[
\frac{1}{E_{\mathrm{gun}}}\sum_e w_e Q_e(x,\xi,\omega)
\right].
$$

The search solves

$$
x^*=\arg\min_{x\in\mathcal X}J(x)
\quad\text{subject to}\quad g_j(x)\leq0,
$$

where $g_j$ enforce cubic elastic stability, positive transport values,
$v_T<v_L$, valid interface probabilities, and an upper-film gap of at least
$1.5384\times10^{-3}\,\mathrm{eV}$ for niobium-compatible designs.

This is QP yield under the stated injection model. It is not a logical-error
probability.

## Device-area integration

Near-electrode positions are sampled more densely because their response is
large. If region $h$ covers true area fraction $W_h$, contains $n_h$ sampled
positions, and has $R$ transport realizations, let $Q_{e,i,r}$ be the total QPs
in one task of $N_{\mathrm{task}}$ source phonons. The estimator is

$$
\widehat J(x)=
\sum_h W_h\frac{1}{n_hR}
\sum_{i\in h}\sum_{r=1}^{R}
\frac{\sum_e w_eQ_{e,i,r}(x)}{N_{\mathrm{task}}E_{\mathrm{gun}}},
\qquad \sum_hW_h=1.
$$

The $W_h$ factors restore the true device-area measure after deliberate
near-electrode oversampling. A plain mean over sampled positions is biased.

The final integration uses 2,361 positions, eight realizations, and 31,250
source phonons for each position-realization task: $5.9025\times10^8$ source
phonons per material point. All declared spatial checks pass.
Here $\mathrm{SE}$ denotes one standard error.

| Material point | $\widehat J\pm\mathrm{SE}$ | Change from 1,617 positions | Largest position share |
|---|---:|---:|---:|
| Reference | $(2.348403\pm0.046453)\times10^{-3}$ | -0.28% | 0.76% |
| SiC-elasticity test | $(1.197658\pm0.033950)\times10^{-3}$ | -0.01% | 0.58% |
| Niobium-gap-compatible point | $(4.398849\pm0.170543)\times10^{-4}$ | +0.24% | 0.67% |

The SiC row changes other properties and uses a non-niobium-compatible upper
film gap; it is a numerical convergence point, not an isolated SiC claim.

## Optimization comparison

Every method started from the same 32 newly simulated points. Each later
search point used 128 positions, four realizations, and
$1.6\times10^7$ source phonons. Define

$$
J_0=(5.062199\pm0.873542)\times10^{-4}
$$

as the best of those 32 points, and define improvement after $t$ completed
proposals by

$$
I_t=1-\frac{\min\{J_0,J_1,\ldots,J_t\}}{J_0}.
$$

| Method | Independent runs | Completed proposals per run | Median best new $J$ | Median $I_t$ |
|---|---:|---:|---:|---:|
| CMA-ES | 3 | 120 | $2.537300\times10^{-4}$ | 49.9% |
| Local-language-model proposals + GP ranking | 3 | 120 | $3.870108\times10^{-4}$ | 23.5% |
| Random | 1 | 64 | $7.252207\times10^{-4}$ | 0.0% |
| Sobol | 1 | 49 of 64 | $7.566498\times10^{-4}$ | 0.0% |
| GP expected improvement over the widened range | 1 | 0 of 120 | no measured proposal | 0.0% |

The local-language-model comparison was performed on branch
`Agentic_Material_optimization`; the values and figure copied here correspond
to commit `5c1fe2c`. Its median best value is 52.5% higher than the CMA-ES
median. It improved all three runs relative to $J_0$, but it did not outperform
CMA-ES. The GP-only run is inconclusive: its first boundary proposal completed
none of 512 tasks in one hour, so no objective value was assigned.

![Search comparison](material_scan/docs/figures/agentic-comparison-status.png)

The left panel compares completed proposals, the middle panel compares
simulated source-phonon count, and the right panel shows recorded wall time for
the three language-model-assisted runs. Older methods did not record equivalent
timing data, so the right panel is not a timing comparison between methods.

Exact plotted values remain in the source branch's
[`agentic-comparison-status.json`](https://github.com/HHTseng/QPU-material-sensitivity-analysis/blob/Agentic_Material_optimization/material_scan/experiments/agentic-comparison-status.json).

## Full-spatial confirmation

The best point from each CMA-ES run was repeated with the complete 2,361-position
design and unused random-number sequences.

| Point | Confirmed $J\pm\mathrm{SE}$ | Paired change from compatible point |
|---|---:|---:|
| Compatible point | $(5.936004\pm0.209039)\times10^{-4}$ | reference |
| CMA-ES 101 | $(3.118155\pm0.132476)\times10^{-4}$ | -47.5% |
| CMA-ES 202 | $(3.638355\pm0.155497)\times10^{-4}$ | -38.7% |
| CMA-ES 303 | $(3.068781\pm0.180000)\times10^{-4}$ | -48.3% |

CMA-ES 101 and 303 are statistically unresolved from each other, while both
are resolved below CMA-ES 202. The result is therefore a leading property
region, not one unique optimum. The complete paired results are in
[`material-search-confirmation-results.json`](material_scan/experiments/material-search-confirmation-results.json).

## What the results mean

1. Ten complete CMA-ES generations were useful. One run found its best point
   at proposal 108; stopping at 69 would have missed it.
2. More GP expected-improvement steps should not be launched over the same
   widened range. First restrict the search to physically meaningful and
   computationally measurable values.
3. Every confirmed CMA-ES point has $C_{44}=5\,\mathrm{GPa}$, the exact lower
   search limit, and touches at least one other limit. These are simulated
   property targets, not known fabricable compounds.
4. The identical compatible point changed by 34.9% between two independent
   random-number sets, while the ordinary reference changed by only 0.29%.
   Repeat-to-repeat variation for sparse low-QP points is larger than the
   within-run standard error suggests.
5. No agent-proposed point has full-spatial confirmation. Its present result is
   a method comparison, not a new material conclusion.

## Recommended next experiments

1. Repeat the compatible point and CMA-ES 101/303 with another unused
   random-number set.
2. Measure paired one-parameter profiles around the tied CMA-ES region,
   especially near the $C_{44}$ limit.
3. Replace independent numerical ranges with relations and limits supported by
   real materials, then project the leading region onto named compounds.
4. Continue CMA-ES with at least three independent runs and 8–10 complete
   generations. Retry GP expected improvement only in a local region whose
   simulation time is known to be practical.
5. Obtain SiC scattering, total decay, and transverse-transverse decay values
   from literature or first-principles calculations before a fabrication claim.

## Repository map

| Path | Purpose |
|---|---|
| [`material_scan/`](material_scan/) | current simulation, objective, search, tests, and reports |
| [`material_scan/README.md`](material_scan/README.md) | commands and code map |
| [`material_scan/docs/science.md`](material_scan/docs/science.md) | physical assumptions and equations |
| [`material_scan/docs/results.md`](material_scan/docs/results.md) | experiment history through the base branch |
| [`material_scan/docs/data.md`](material_scan/docs/data.md) | result storage and recovery |
| [`legacy/`](legacy/) | retained earlier implementation; do not start new work here |
| [`data/`](data/) | preserved raw simulations and SQLite records; no source code |

## Verification

Run from the repository root in the existing G4CMP Conda environment:

```bash
conda run -n G4CMP python -m unittest discover -s material_scan/tests -v
conda run -n G4CMP python legacy/tests_stage4.py
```

The Geant4 executable, G4CMP source, and G4CMP data are external installations.
Their exact identities must be recorded for every new simulation.
