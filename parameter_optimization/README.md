# `parameter_optimization/` — what is current, what is history

Two campaigns live here. They optimize the same objective (`total_QPs` at the 17
Al junctions, per primary event) with two different notions of "candidate".

| | Stage 3 (`Material_optimization_v2`) | Stage 4 (`Material_optimization_v3_scan_parameters`) |
|---|---|---|
| Candidate | a triplet of **real materials** (substrate / top ground film / bottom film) | a **continuous property vector** — a pseudo-material that need not exist |
| Search | exhaustive enumeration of 18 triplets | Bayesian optimization, CMA-ES, Sobol/random baselines, LLM-agentic proposal |
| Result | `Ge/Nb/Cu`, −36.0% vs the `Si/Nb/Cu` baseline, converged at 1e7 events/sub-run | **−69.9%** junction QPs at held-out seeds, converged at 1e7 events/sub-run. The real-material projection is **not yet validated** — see the notice below |

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
> Checked mechanically: **9 of the ledger's 11 projection-derived trials are
> affected; nothing else is.** The 1e6 / 1e7 / 1e8 fidelity ladder does not need
> repeating — only the real-material projection does.
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
| `stage4_assemble_xl.py` | merges confirmation JSONs into the combined 1e8 table — **no simulation, no ledger** |
| `stage4_post_xl.sh` | fail-closed runbook: check → snapshot → validate → migrate → unfreeze, with receipts |
| `tests_stage4.py` | the exit-gate suite; run before any campaign |

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
