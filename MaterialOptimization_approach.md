# Material Parameter Optimization — Approach Notes

This project has two goals:

1. **Understand** how material-parameter changes (the swept quantities behind
   each `Morris_i.mac`) affect quasiparticle (QP) generation that poisons
   qubit hardware — addressed by the Morris sensitivity sweep
   (`stage1_run_simulations.py` / `stage2_compute_QPs.py`) and the correlation
   analysis in `stage2_compute_QPs_sensitivity_analysis.py`, and grounded in
   *"Modeling phonon-mediated quasiparticle poisoning in superconducting qubit
   arrays."*
2. **Optimize**: find the material-parameter configuration (the knobs in
   `sensitivity_params.py`) that **minimizes** the resulting QP poisoning.

This document is the proposal for goal 2, carried over faithfully from the
discussion on the `2-stage-QP-Sensitivity-Analysis` branch. Goal 1's pipeline
and outputs are the starting point and warm-start data for everything below.

## What kind of optimization problem this is

From the code and the run records, minimizing QP poisoning here is:

- **Expensive** — every objective evaluation is a Geant4/G4CMP run + stage-2
  QP compute (~seconds at 1e4–1e5 events, ~13 s at 1e6, and stable numbers
  often need several events-counts/replicas).
- **Stochastic / noisy** — CLHEP is clock-seeded (per the run records: *"hits
  files are not reproducible across reruns"*), and at 1e5 events ~⅔ of
  configs produce **zero** above-threshold signal. The objective is noisy,
  heavy-tailed, and zero-inflated.
- **Black-box, gradient-free** — no derivatives w.r.t. material params;
  finite differences would drown in Monte-Carlo noise.
- **Mixed-type, box-constrained** — mostly continuous (±50% of default), plus
  integer `QPLim ∈ [2,5]`, all with bounds already defined in
  `sensitivity_params.py`.
- **Effectively low-dimensional** — nominally ~30–50 knobs, but goal 1 tells
  you only a handful move the objective. **This is the key lever.**
- **Failure-prone** — SIGSEGV (~0.1%), the `QPLim=1` hang (already excluded
  by bounding `QPLim` at 2), zero-signal configs.
- **Cheaply parallel + multi-fidelity** — mimir has 192 cores, and *event
  count is a natural fidelity knob* (1e4 cheap/noisy → 1e6 expensive/precise).
- **Warm-startable** — the existing 6912-point Morris run already spans the
  whole design box.

That profile — expensive, noisy, black-box, reducible dimension, existing
data, multi-fidelity — points squarely at **surrogate-based Bayesian
optimization**, with a couple of robust alternatives.

## Pin the objective first (one physical note)

Minimize a **quasiparticle** quantity, not the decoherence directly. The
ODE/QPDE parameters (`s, r, I_ph, f_01, pt, n_cooper`) that *dominated* the
goal-1 correlation ranking are **qubit operating conditions, not fabrication
knobs** — you can't "design" them by choosing chip materials. So:

- **Design variables** = the material/geometry/crystal knobs
  (`electrode_params`, `detector_params`, G4CMP `config_params`) — the
  things that set the phonon→QP conversion.
- **Objective** = `total_QPs` (pure Geant4/G4CMP output, independent of the
  ODE), or better, a **junction-localized QP count** (QPs landing on the
  qubit islands — poisoning is about QPs *reaching the qubit*). Both are
  already computable from the hits file in stage 2.
- Re-run the goal-1 correlation screen with `total_QPs` as the outcome (the
  column already exists in `qp_summary.csv`) so the influential-subset you
  optimize over is the one that actually drives *QP generation*, not ODE
  decoherence.

*(If the reference PDF defines a specific poisoning metric — e.g. QP density
at the junction — the objective should be aligned to it.)*

## Recommended approaches (ranked)

### 1. Bayesian Optimization (GP surrogate), warm-started + dimension-reduced — primary

The textbook fit for expensive noisy black-box problems. Concretely:

- Use goal-1 sensitivity to **fix the ~40 non-influential params at defaults
  and optimize only the ~5–10 that matter** (BO degrades past ~15–20 dims;
  this makes it tractable).
- **Warm-start the GP with the existing 6912 Morris points** — begin
  near-informed instead of cold.
- Use a **noise-aware** acquisition (noisy-EI / knowledge gradient) since the
  objective is stochastic; give the GP a learned noise term.
- **Batch/parallel** acquisition (q-EI) to use many mimir cores per
  iteration.
- Tools: **Ax/BoTorch** (best for noisy, batch, mixed-integer,
  multi-fidelity), or scikit-optimize / SMAC3 for a lighter start.

### 2. Multi-fidelity BO — the highest-leverage extension of (1)

Treat **event count as fidelity**: screen many candidates cheaply at 1e4
events, promote only the promising ones to 1e5/1e6 for precise confirmation
(BoTorch multi-fidelity, or a Hyperband/BOHB-style scheme). Given the cost
curve here, this is where the biggest speedups are.

### 3. CMA-ES — robust derivative-free fallback / continuous-subset workhorse

If BO's dimensionality or noise handling struggles, CMA-ES is excellent for
noisy, non-convex, continuous problems, needs no surrogate, and is
**embarrassingly parallel** (evaluate the whole population across cores).
Costs more evaluations than BO but is very robust; pairs well with mimir's
core count. Handle integers by rounding.

### 4. Offline surrogate + active learning — do this first, it's nearly free

Before any new simulation, fit a **random-forest / gradient-boosted-tree (or
GP) surrogate** on the 6912-point dataset already on disk, optimize the
*cheap* surrogate to propose candidate minima, then validate a handful with
real sims and iterate. Tree models handle the zero-inflated, non-smooth,
mixed-type response robustly and give feature importance for free. This
gives a first optimum candidate essentially for free and seeds (1).

### 5. (If it becomes multi-objective) NSGA-II / multi-objective BO

If the design later needs to trade QP poisoning against device performance
or fabrication constraints, a Pareto-front method (NSGA-II, or qNEHVI in
BoTorch) gives the trade-off surface rather than a single point.

## Cross-cutting practicalities (these matter more than the exact optimizer)

- **Noise control**: either fix the CLHEP seed for reproducibility, or
  average *k* replicas / raise event count, and optimize a **robust
  statistic** (mean or an upper quantile — worst-case poisoning low is
  likely the real goal, not just average).
- **Failure handling**: crashes/zero-signal → feed the optimizer a penalty
  or model failure probability (BoTorch supports outcome constraints /
  failed trials).
- **Parallelism**: batch BO or CMA-ES populations to saturate mimir; the
  existing 4-worker stage-1 harness is a starting point to build on.
- **Reuse the pipeline**: stage 1 already generates `Morris_i.mac` from a
  parameter vector and stage 2 already reduces to QP scalars — an optimizer
  just needs a thin "propose vector → write macro → run → read `total_QPs`"
  wrapper. Low integration cost.

## Suggested concrete pipeline

1. Re-screen the existing (or a fresh) sensitivity run on `total_QPs` as the
   outcome to nail down the influential material-parameter subset.
2. Fit an offline tree/GP surrogate on the existing 6912 points to get a
   first candidate minimum for free.
3. Run **multi-fidelity batch BO (Ax/BoTorch)** over the influential subset:
   cheap-screen at 1e4 events, confirm top candidates at 1e6 with replicas.
4. Cross-check the BO result with **CMA-ES** for robustness.
5. If trade-offs against other device metrics matter, extend to a
   multi-objective method (NSGA-II / qNEHVI).

## Open questions to resolve before implementation

- Does the reference PDF define a specific poisoning metric to target
  (e.g. junction-localized QP density vs. total QP count)?
- Should the objective be a mean over replicas, a worst-case quantile, or
  something else?
- Which event count is the right "confirmation fidelity" given the crash
  analysis's guidance on `QPLim` and memory behavior at high event counts?
