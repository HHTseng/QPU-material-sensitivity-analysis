# `parameter_optimization/` — what is current, what is history

Two campaigns live here. They optimize the same objective (`total_QPs` at the 17
Al junctions, per primary event) with two different notions of "candidate".

| | Stage 3 (`Material_optimization_v2`) | Stage 4 (`Material_optimization_v3_scan_parameters`) |
|---|---|---|
| Candidate | a triplet of **real materials** (substrate / top ground film / bottom film) | a **continuous property vector** — a pseudo-material that need not exist |
| Search | exhaustive enumeration of 18 triplets | Bayesian optimization, CMA-ES, Sobol/random baselines, LLM-agentic proposal |
| Result | `Ge/Nb/Cu`, −36.0% vs the `Si/Nb/Cu` baseline, converged at 1e7 events/sub-run | **−70.0%** junction QPs at held-out seeds (converged). Nearest real substrate **3C-SiC recovers 76.2%** of that gain; the catalogued triplet `GaAs/Nb/Cu` recovers **44.6%** |

> ### Validity notice — 2026-08-24
>
> An implementation audit found that the projected substrate's Geant4 density
> carrier was dropped before simulation, so **every real-material verification
> run was simulated as `G4_Si` at 2330 kg/m³** whatever it was labelled. The
> nearest-material claims — including "SiC recovers 77% of the gain" — are
> **withdrawn** pending a rerun. The **property-space result itself is
> unaffected** (those candidates are pseudo-materials whose intended carrier
> *is* `G4_Si`; all 163 recorded trials were re-checked and still resolve to
> their stored identity).
>
> Two further claims are withdrawn: the **optimizer-efficiency ranking** (the
> winning point came from the Sobol initialisation, not a GP acquisition) and
> the **"six baseline re-evaluations, 0% drift"** validation (the controls were
> cache hits). All three defects are fixed and gated; the reruns are not done.
>
> **Resolved 2026-08-25.** The corrected projection has been rerun through the
> S, M and L tiers under the fixed carrier propagation and the 8-replica
> contract, and is **converged** (M→L RMS shift 0.7%). SiC recovers **76.2%** of
> the ideal gain, `GaAs/Nb/Cu` **44.6%** — so the withdrawn claim that
> catalogued materials realise "essentially none" of it is dead: GaAs realises
> nearly half. The optimizer-efficiency ranking stays withdrawn permanently
> (its provenance was never recorded). See `STAGE4_RESULTS.md` §4.3.
>
> `python parameter_optimization/stage4_audit.py` re-checks every claim against
> the ledger, the contract and the macro templates.

> ### Ledger frozen — 2026-08-24
>
> `stage4_trials.sqlite` is **closed to new writers** while a 1e8/sub-run
> confirmation finishes under its launch code identity. Any campaign script will
> refuse to start and tell you why. This is deliberate: a new process would have
> a different code fingerprint (four `CODE_IDENTITY_FILES` changed), so it would
> miss every cache entry and re-simulate days of work, and opening the ledger
> would migrate the schema underneath the running job.
>
> Lift it with `./stage4_post_xl.sh all` once the job lands. Every step writes a
> receipt only on success and the next step refuses without it, so validation
> cannot be skipped and an incomplete XL attempt can never be promoted.
> `./stage4_post_xl.sh status` shows how far the sequence has got.

## Current — Stage 4

| File | Role |
|---|---|
| [`STAGE4_RESULTS.md`](STAGE4_RESULTS.md) | **the result**: −69.9% junction QPs at held-out seeds, the best confirmed property vector, and the real materials nearest to it (with validity labels) |
| [`STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md`](STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md) | the 2026-08-24 implementation audit: seven findings, P0–P7 |
| [`STAGE4_AUDIT_REMEDIATION.md`](STAGE4_AUDIT_REMEDIATION.md) | what was fixed for each finding, what it is gated by, and what still needs simulation budget |
| [`stage4_invalidations.yaml`](stage4_invalidations.yaml) | machine-readable register of results a known finding invalidated, with the measured error |
| [`STAGE4_PROPERTY_OPTIMIZATION_PLAN.md`](STAGE4_PROPERTY_OPTIMIZATION_PLAN.md) | the design: the space, the gates, the optimizers, the projection step, the test gates, and the implementation record |
| `stage4_config.yaml` | campaign contract: space, objective, optimizer, budget, fidelity |
| `stage4_space.py` | decision variables, transforms, hard gates, pseudo-material resolver |
| `stage4_objectives.py` | objective registry (switch with `--objective`) |
| `stage4_optimizers.py` | optimizer registry (switch with `--optimizer`) |
| `stage4_llm.py` | Ollama-backed agentic proposer and its guard rails |
| `stage4_optimize.py` | campaign driver |
| `stage4_project_material.py` | nearest-real-material projection + verification runs |
| `stage4_report.py` | cross-optimizer comparison, best-so-far vs cost, promotion list |
| `stage4_confirm.py` | re-runs finalists at higher fidelity on held-out seeds, with paired site-matched comparisons |
| `stage4_tolerance.py` | one-factor tolerance scan around an optimum; produces the projection weights |
| `stage4_pilot.py` | determinism, noise, throughput and the inertness A/Bs; run before a campaign |
| `stage4_probe_g4_density.py` | measures a NIST material's Geant4 density so it can be used as a density carrier |
| `stage4_audit.py` | consistency audit: energy protocol, invalidated results, ledger vs manifests, liveness, code identity, doc headlines |
| `stage4_invalidations.yaml` | register of invalidated results, contaminated inputs and **recorded decisions**; read by `stage4_audit.py` |
| `stage4_reconcile.py` | labels invalidated rows in result files and backfills the ledger's counts into historical manifests — additive, idempotent, never overwrites a value |
| [`BO_GP_and_CMA_ES_Rerun_Recommendations.md`](BO_GP_and_CMA_ES_Rerun_Recommendations.md) | the design of the formal optimizer comparison: 96 evaluations per seed, ≥3 seeds, equal paid event budget |
| `run_stage4_optimizer_benchmark.sh` | runs that comparison — 4 methods × 3 seeds × 96 S-tier evaluations, restartable, no absolute timeouts |
| `stage4_assemble_xl.py` | merges confirmation JSONs into the combined 1e8 table — **no simulation, no ledger** |
| `stage4_post_xl.sh` | fail-closed runbook: check → snapshot → validate → migrate → unfreeze, with receipts |
| `tests_stage4.py` | the exit-gate suite; run before any campaign |

## The formal optimizer comparison — seed 1 complete

The original campaigns cannot support an algorithm ranking: `bo_gp` completed
**one** genuine GP acquisition (its winner came from the Sobol initialisation)
and `cmaes` completed **one** generation, with random filler labelled as its
own. Those measured property vectors remain valid; the efficiency ranking was
withdrawn and is permanently unrecoverable for those runs
(`optimizer_provenance_unrecoverable` in `stage4_invalidations.yaml`).

The replacement, per
[`BO_GP_and_CMA_ES_Rerun_Recommendations.md`](BO_GP_and_CMA_ES_Rerun_Recommendations.md):

| method | design per seed | evaluations | seeds |
|---|---|---:|---:|
| BO–GP | 32 Sobol init + 64 genuine GP–EI cycles | 96 | 3 |
| CMA-ES | 8 complete generations × 12 | 96 | 3 |
| random | 96 independent draws | 96 | 3 |
| Sobol | 96 space-filling points | 96 | 3 |

**1152 evaluations, 4.6e9 primary events.** Every method shares the same frozen
contract, injection sites, physics seed bank, objective and paid event budget —
the optimizer is the only variable (Priority 0 of the recommendation). Compared
on best-so-far against *cumulative paid events*, not trial count.

Two things this run produces that the project has never had: **true proposal
provenance** for every trial (`sobol_init` / `gp_ei` / `cma_generation` / …), and
its **first drift controls** — `--baseline-every 24` gives 4 fresh baseline
re-evaluations per campaign, 48 across the benchmark, each with its own ledger
row. Until now the project had **zero** recorded controls.

`./run_stage4_optimizer_benchmark.sh` — restartable; it skips campaigns already
complete in the ledger.

**Seed 1 landed 2026-09-05** (33 h, 85 slot-hours, 1452 M events). Full analysis:
[`STAGE4_OPTIMIZER_BENCHMARK_RESULTS.md`](STAGE4_OPTIMIZER_BENCHMARK_RESULTS.md).

| method | best *J* | median *J* | trials below the old best | protocol |
|---|---:|---:|---:|---|
| bo_gp | 5.60e-5 | **1.08e-4** | **50/96** | 69 genuine GP–EI acquisitions, 14 fits |
| cmaes | **5.30e-5** | 1.64e-4 | 28/96 | 8 complete generations, 0 restarts |
| sobol | 7.35e-5 | 3.62e-4 | 2/96 | 96 space-filling |
| random | 1.02e-4 | 4.02e-4 | 1/96 | 96 draws |

Both adaptive methods clearly beat the baselines, and **the margin is in
concentration, not in the best value**: bo_gp spent 52% of its budget below the
old campaign's best against random's 1%. **bo_gp vs cmaes is not resolved** —
5.60e-5 vs 5.30e-5 sits inside the ±1–2e-5 replica error, which is why the
design calls for ≥3 seeds.

Against the withdrawn campaigns the corrected rerun is **~2× better** (5.3e-5 vs
1.15e-4) — but at 4× the evaluations; at *matched* budget the old lucky Sobol
draw was ahead. The real gain is that the result is now **attributable**.

> **The winners are box corners.** bo_gp's best pins **13 of 16** variables to a
> box wall, and both adaptive methods land on the same vertex — ground plane at
> `T_c` ≈ 0.33 K, +374 orders of magnitude in equilibrium QP density vs Nb. The
> algorithm ranking is valid; the material answer is not. Widening the box before
> making the P3 device-quality decision would only find a deeper corner.

**P5 finally has drift evidence**: 16 fresh baseline re-evaluations over 33 h,
CV **0.25%** against a ~5% stochastic error — no machine or executable drift.
The withdrawn "0.0% spread" claim measured the cache; this measures the machine.

## Shared machinery (used by both stages)

`stage3_contract.py` (contract load/validate/hash), `stage3_ledger.py` (SQLite
trial ledger), `stage3_trial_runner.py` (single-trial evaluator),
`interface_transmission.py` (acoustic interface model),
`material_catalog.yaml` (+ `build_material_catalog.py`, `catalog/`),
`SoundfromTensor.py` (reference Christoffel calculation),
`parameter_set.txt` (the authoritative fixed/tune/derived parameter list).

## Historical record — Stage 3, kept because v3 is measured against it

| File | What it records |
|---|---|
| [`STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md`](STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md) | the architecture, the parameter contract, and §12's measured operational hazards (crash modes, seeding, memory, energy protocol, scenario set) |
| [`STAGE3_SMALL_MATERIAL_START.md`](STAGE3_SMALL_MATERIAL_START.md) | the nine-gate startup checklist, the factorial result, and the beamOn scaling study (Fano ≈ 5) |
| [`STAGE3_GAP_REMEDIATION.md`](STAGE3_GAP_REMEDIATION.md) | dated change log: every gap found, what was done, what is still open |
| [`STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md`](STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md) | independent audit of the 18-triplet campaign |
| `stage3_trials*.sqlite`, `runs/`, `results/`, `logs_beamon/`, `presentation/` | the campaign data itself |

Removed 2026-08-21 as superseded: `STAGE3_START_ROADMAP.md` (its own header
deferred to the checklist; architecture duplicated in the pipeline document),
`small_material_candidates.example.yaml` (placeholder template, replaced by
`material_catalog.yaml`), `ElasticityTensors.py` (replaced by
`build_material_catalog.py`, which separates explicit-ID from filtered-search
mode instead of discarding its own query).
