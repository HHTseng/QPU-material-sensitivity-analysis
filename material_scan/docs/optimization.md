# Measured optimization gains and next comparisons

Checked 2026-10-15. The current 1,024-site queue is complete and stopped for
review. No further simulations were started for this assessment.

## Response and comparison

For physical properties $x\in\mathcal X\subset\mathbb R^d$, let $J(x)$ be
area-weighted junction quasiparticles per injected eV. For a measured starting
point $x_b$, define the fractional reduction

$$R(x\mid x_b)=1-\frac{J(x)}{J(x_b)}.$$

The measured minimum is the smallest confirmed response among tested points.
It does not establish $\min_{x\in\mathcal X}J(x)$ or a realizable material.
Comparisons below retain their own response definitions, source layouts,
physical limits and reference points.

## Completed source checks

The queue finished **2026-10-15 at 10:38:38 EDT**, after **11 h 43 min**:
10 studies, 160,000 completed tasks and 13.832 billion primary phonons.
Remaining queued time is zero. All twenty study-archive copies match their
recorded checksums. The earlier 11–19 h estimate covered the measured duration.

Four independent 1,024-site layouts give:

| Predefined case | $J$, QPs/eV | Standard error | Mean paired reduction, 95% interval |
|---|---:|---:|---:|
| Reference | 0.00234472 | 0.00000806 | — |
| $C_{44}=1$ GPa | 0.00223919 | 0.00000840 | 4.50% [2.99%, 6.00%] |
| SiC-derived model | 0.00154571 | 0.00000634 | 34.08% [32.98%, 35.17%] |

The reduction interval uses the four paired layout fractions
$R_k=1-J_k(x)/J_k(x_b)$ and a Student-t interval with three degrees of freedom.
These three cases were specified before transport; their reductions are not
gains found by BO or CMA-ES. The SiC rates and interfaces remain assumptions.

Primary-count comparisons passed at 125,000 and 500,000 primaries per task.
Reference transport relative standard error fell from 0.788% at 31,250 to
0.292% at 500,000; including position uncertainty, the latter estimate still
has 2.58% relative standard error on its fixed 512-site layout. Raising the
primary count everywhere would mainly improve an already smaller error source.

Spatial equivalence did not pass. Nested 512-to-1,024 total changes are only
1.46%, 0.71% and 2.75%, respectively, but their 95% intervals extend beyond
the declared ±5% margin. The 0–0.05 mm region changes by 21–29%. Independent
layout means have 0.34–0.41% relative standard error; their agreement intervals
with the retained layout remain broader than ±5%. This is incomplete evidence
of equivalence, rather than a resolved large total discrepancy.

[Numerical results, regional checks and archive verification](../../verification/initial_1024_results.json).

## Contributions from earlier searches

The most relevant completed search used 20 mK, an upper-film gap of at least
1.5384 meV, fourteen variable properties and $C_{44}\geq5$ GPa. Three CMA-ES
runs started from the same 32 measured points and each completed 120 additional
measurements. Screening used 128 sites, four realizations and 31,250 primaries
per task. All three improved on the common best response, $5.06220\times10^{-4}$.

Confirmation used 2,361 historical sites, eight realizations and unused streams.
Those experiments predate the current cap; no larger design was rerun here.
The matched confirmation reference is $2.35511\times10^{-3}$ QPs/eV and the
compatible starting anchor is $5.93600\times10^{-4}$ QPs/eV.

| CMA-ES seed | Screening reduction from common start | Confirmed $J$, QPs/eV | Confirmed reduction from compatible anchor | Total reduction from matched reference |
|---|---:|---:|---:|---:|
| 101 | 48.7% | 0.000311815 | 47.5% ± 3.9 points | 86.8% |
| 202 | 49.9% | 0.000363836 | 38.7% ± 4.1 points | 84.6% |
| 303 | 54.1% | 0.000306878 | 48.3% ± 4.2 points | 87.0% |

The ± values are one paired-difference standard error divided by the observed
anchor. They do not include a separate uncertain-denominator calculation.
Paired absolute-difference intervals resolve improvement over the anchor;
seeds 101 and 303 remain tied. The anchor was already 74.8% below the matched
reference. Later CMA-ES added **9.8–12.2 percentage points of reference-relative
reduction**, rather than finding the entire 84.6–87.0% reduction by itself.
All finalists reach the $C_{44}=5$ GPa limit, so an interior minimum is unproven.

The constrained BO attempt completed no new measurement after its first
proposal stalled. Its gain is **unmeasured**. Random and Sobol extensions did
not improve the common start, but used fewer new evaluations than CMA-ES;
they do not supply an equal-length comparison.

An earlier reported 96-response table gives the following summed junction
QPs per primary on sixteen sites:

| Method | Reported best response | Below the reported best Sobol response | Below the reported best random response |
|---|---:|---:|---:|
| GP Bayesian search | 0.0000560 | 23.8% | 45.1% |
| CMA-ES | 0.0000530 | 27.9% | 48.0% |
| Sobol | 0.0000735 | — | 27.9% |
| Random | 0.000102 | — | — |

BO supplied 50/96 entries below $1.15\times10^{-4}$, versus 28/96 for CMA-ES,
2/96 for Sobol and 1/96 for random. This supports useful concentration of
search points. The lowest gaps violate the present Nb limit, temperature was
variable, and accumulated ledger counts and recorded primary use differ.
Therefore this table does not establish equal fresh computation, a current
device improvement, or a statistically resolved BO-versus-CMA ranking. No
measured TPE result was found.

The [optimizer audit](../../verification/optimizer_contributions.json) retains
exact values, paired intervals, original Git paths and file checksums. Historical
figures are evidence from their own source revision, not fresh confirmations
under the rewritten workflow.

## Proposed next work, with at most 1,024 sites

1. **Use the measurements already saved.** Within each independent 1,024-site
   layout, select a balanced 512-site subset with half the positions in every
   region. Compute $D_k=J_{k,1024}-J_{k,512}$ from the same outputs, then the
   mean and Student-t interval across four independent $D_k$. This needs no new
   transport and preserves shared-position covariance. Retain the failed
   original criteria; this is an additional analysis, not a replacement pass.
   If needed, use pilot estimates to set $n_h\propto W_h\sigma_h$, where $W_h$
   is region area fraction and $\sigma_h$ is site-response standard deviation.
   Keep coverage of every junction, retain the 1,024 total and confirm the
   chosen allocation on unused position seeds.
2. **Remeasure useful starting points under one definition.** Use the reference,
   physically compatible historical leaders and diverse new points at 20 mK
   with the current gap bound, area weights and objective. Historical values
   may suggest positions in property space; they must not be inserted as
   current observations. Inspect $C_{44}$ near the earlier 5 GPa boundary with
   other properties held fixed before extending unsupported ranges.
3. **Start with four to six properties.** Initial candidates are the three
   elastic constants, two film lifetimes and upper-film gap, subject to
   physical checks. Choose the final subset from measured sensitivity. Fix
   weak coordinates initially, then release them if residual measurements
   justify doing so. This is a simpler first test than seventeen variables
   and only 120 responses.
4. **Include completion and runtime in selection.** For a proposed point $x$,
   let $p_c(x)$ be estimated completion probability, $t(x)>0$ predicted runtime,
   and $\mathrm{EI}(x)$ expected decrease in $J$. A useful next-point score is
   $a(x)=p_c(x)\mathrm{EI}(x)/t(x)$. Failed and timed-out trials inform completion
   and time, never a zero response. The current GP already uses measured
   observation errors and some local proposals; it lacks these runtime terms.
   Add them to the existing selection calculation before adopting a larger
   framework. [Cost-aware BO](https://botorch.org/docs/v0.17.0/tutorials/cost_aware_bayesian_optimization)
   provides the improvement-per-time principle. If the global GP still struggles,
   test an expanding/shrinking local search region, as motivated by
   [TuRBO](https://proceedings.neurips.cc/paper/2019/hash/6c990b7aca7bc7058f5e98ea909e924b-Abstract.html).
5. **Measure additional benefit fairly.** A proposed first comparison uses three
   optimizer seeds, 24 common starting measurements per seed and 48 additional
   measurements per method: BO, CMA-ES, Sobol and random. Give CMA-ES the measured
   common best point as its initial mean. Use 512 sites, four realizations and
   31,250 primaries for screening, conditional on the subset analysis above.
   This requires 648 distinct candidate measurements, or 41.472 billion primary
   phonons before failures and confirmation. Share the starting measurements
   across methods rather than repeat them. Report best confirmed $J$, reduction
   beyond the common start, completed/failed counts, actual primaries and CPU
   hours. Compare both equal completed counts and equal elapsed time. Reserve
   independent layouts and unused streams for final confirmation at 1,024 sites.
   Reuse shared confirmation anchors. The present 120-point definitions remain
   deferred; this smaller comparison is a proposal, not a scheduled run.

Lower primary counts are cheaper measurements of the same fixed-layout mean;
fewer positions can also change its integration error. Check agreement and
ranking on matched archived subsets before combining these resolutions in a
single surrogate. [BOCA](https://proceedings.mlr.press/v70/kandasamy17a.html)
supports using cheap approximations, but does not establish their reliability
for this device. Retain measured errors and resolution labels.

The code can remain in the existing scripts: selection in `search.py`, paired
comparisons in `analysis.py`, definitions in YAML and plots in `figures/plot.py`.
No new class hierarchy or optimizer service is needed for the first comparison.

## Agentic AI

Its contribution to reducing this project's measured response is currently
**unknown**: no completed agentic comparison exists. The present `llm_bo` mode
asks a local model for property vectors, applies physical checks, lets the GP
rank valid proposals and obtains responses only from Geant4.

A read-only check on 2026-10-15 found eight visible RTX A6000 GPUs, but no
`ollama` on `PATH` or responding default local model endpoint. An extracted
binary exists under `material_scan_data/ollama/v0.34.1/bin/ollama`; its presence
does not establish a loaded model. The cache contains a Qwen3 235B model manifest
and approximately 142 GB of corresponding files. The code's default model name,
`qwen3:32b`, differs from this cached model. A future test must select the actual
model and measure loading, inference time and memory use. No service was
started and no model was installed or invoked for this assessment.

AI may help select literature-supported ranges, identify missing physical
inputs, interpret failed runs and propose coupled parameter changes. Numerical
advantage is less certain in a continuous space with weakly known rates.
[LLAMBO](https://proceedings.iclr.cc/paper_files/paper/2024/hash/84b8d9fcb4e262fcd429544697e1e720-Abstract-Conference.html)
demonstrates benefits in hyperparameter-tuning benchmarks; transfer to this
phonon problem is an untested hypothesis.

Before a small comparison, correct the prompt's response label from QPs/primary
to the selected objective and its units. Supply uncertainty, elapsed time,
failed points and physical limits, alongside the measured best points. Compare
GP alone against GP plus model proposals with the same initial data, three seeds
and initially 24 additional measurements per seed. Include model inference
time, rejected/clipped proposals and confirmed response reduction. Retain AI
proposals only if they improve measured results or time to reach a predeclared
response. Keep the physical checks and simulation response unchanged. This
comparison should follow a working conventional BO run.
