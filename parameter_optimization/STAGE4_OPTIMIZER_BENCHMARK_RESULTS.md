# Optimizer benchmark — seed 1 results

Design: [`BO_GP_and_CMA_ES_Rerun_Recommendations.md`](BO_GP_and_CMA_ES_Rerun_Recommendations.md).
Run 2026-09-04 → 09-05 on mimir, 96 worker slots, 33 h wall, **85 slot-hours of
Geant4**, 1452 M paid primary events.

> **Scope.** S-tier search only (4e6 events per candidate, 16 sites × 8
> replicas). The recommendation's §7 decision rule needs ≥3 optimizer seeds and
> held-out M/L confirmation of the leaders; **neither has been done**, so nothing
> here is a final ranking. What it does settle is whether the corrected protocol
> works and roughly where the methods stand.

## 1. Every method met its protocol

| method | obs | proposal sources | protocol requirement | met? |
|---|---:|---|---|---|
| bo_gp | 96 | `gp_ei` 69, `sobol_init` 37 | ≥50 genuine GP–EI acquisitions | **yes (69)**, 14 GP fits |
| cmaes | 96 | `cma_generation` 96, `cma_incumbent_reeval` 1 | ≥8 complete generations | **yes (8)**, popsize 12, 0 restarts, 95 used in update |
| random | 96 | `random_baseline` 100 | 96 independent draws | yes |
| sobol | 96 | `sobol_init` 99 | 96 space-filling | yes |

The historical campaigns managed **one** genuine GP acquisition and **one** CMA
generation. Their ranking was withdrawn as unrecoverable; this replaces it.

## 2. Results

Best-so-far *J* against cumulative paid primary events — the fair axis, since a
method that spends more should find more:

| method | 92 Me | 148 Me | 192 Me | 288 Me | **384 Me (full)** | median *J* | trials below 1.15e-4 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **bo_gp** | 1.50e-4 | 7.20e-5 | 6.70e-5 | 6.70e-5 | **5.60e-5** | **1.08e-4** | **50/96 (52%)** |
| **cmaes** | 1.54e-4 | 5.85e-5 | 5.85e-5 | 5.85e-5 | **5.30e-5** | 1.64e-4 | 28/96 (29%) |
| sobol | 1.10e-4 | 1.10e-4 | 1.10e-4 | 1.10e-4 | 7.35e-5 | 3.62e-4 | 2/96 (2%) |
| random | 1.24e-4 | 1.24e-4 | 1.24e-4 | 1.24e-4 | 1.02e-4 | 4.02e-4 | 1/96 (1%) |

**The adaptive methods clearly beat the baselines, and the margin is not in the
best value — it is in concentration.** bo_gp put **52%** of its budget below the
old campaign's best; random managed **1%**. That is the signature of a surrogate
doing its job, and it is robust to the noise that a single best observation is
not.

**bo_gp versus cmaes is NOT resolved.** 5.60e-5 against 5.30e-5, with S-tier
replica errors of ±1–2e-5 on each — the gap is inside the error. On
concentration bo_gp leads clearly (52% vs 29%, median 1.08e-4 vs 1.64e-4); on
single best they are tied. One seed cannot separate them, exactly as §7 warns.

## 3. How much did the rerun improve things?

| | old (withdrawn) | corrected rerun | |
|---|---:|---:|---|
| bo_gp best | 1.150e-4 | **5.60e-5** | **2.05× better** |
| cmaes best | 1.185e-4 | **5.30e-5** | **2.24× better** |
| random best | 1.680e-4 | 1.02e-4 | 1.65× better |

But **at matched budget the picture is different, and more honest**: at 92 Me the
new bo_gp is at 1.50e-4 against the old 1.15e-4 — *worse*. The old campaigns'
headline came from lucky Sobol draws under a 2-replica contract whose per-trial
error was roughly twice today's. The improvement is real but it is bought with
**4× the evaluations**, not with a better early trajectory.

The defensible statement: **the corrected protocol finds points ~2× better than
anything the original campaigns reached, and for the first time the result is
attributable** — 69 acquisitions with recorded provenance, not one.

## 4. The finding that matters more than the ranking

**The winners are box corners.**

| method | best *J* | variables pinned to a box wall | implied ground-plane `T_c` | 2Δ_film/2Δ_Al |
|---|---:|---:|---:|---:|
| bo_gp | 5.60e-5 | **13 of 16** | 0.33 K | 0.26 |
| cmaes | 5.30e-5 | 6 of 16 | 0.33 K | 0.26 |
| sobol | 7.35e-5 | 2 of 16 | 0.59 K | 0.47 |
| random | 1.02e-4 | 0 of 16 | 1.25 K | 0.99 |

Both adaptive methods drove to the same extreme vertex: `topfilm_gap` at its
lower bound, `sub_c11` and `sub_c44` at their upper bounds, `sub_c12` at zero.
The objective is essentially **monotonic in most directions inside the current
box**, so "the optimum" is a statement about where the box was drawn.

Two consequences:

* **The optimizer comparison is valid; the material answer is not.** All four
  methods searched a box whose optimum lies on its boundary. Ranking the
  algorithms is fair. Quoting 5.3e-5 as a design target is not.
* **P3 is now unavoidable.** These winners imply a ground plane at `T_c` ≈ 0.33 K
  and **+374 orders of magnitude** in equilibrium QP density against Nb. The
  correlation between "wins on this objective" and "pinned to the low-gap wall"
  is the whole story: random search, which never reached the corner, is also the
  only method whose winner has a plausible ground plane (`T_c` 1.25 K,
  2Δ_film ≈ 2Δ_Al).

This is precisely Priority 6 of the recommendation: **widen bounds only after
the device-quality constraint is defined.** Widening first would just find a
deeper corner.

## 5. P5 — the project's first real drift evidence

**16 fresh baseline re-evaluations** over 33 h, 4 per campaign, each executed
under its own `control_replica_id` rather than returned from cache:

```
3.9100e-4  3.9150e-4  3.9200e-4  3.9250e-4  3.9400e-4  3.9500e-4
mean 3.9222e-4   CV 0.25%   full spread 1.02%
```

Against a replica-based stochastic error of ~5% at this tier, a 0.25% control CV
means **no machine, executable or runtime drift is detectable**. This is the
claim the withdrawn "6 re-evaluations, 0.0% spread" was trying to make — that
one measured the cache; this one measures the machine.

> **A bug found while reading these numbers.** All 16 controls ran genuinely
> fresh, but all 16 wrote to the **same trial row**: `_evaluate_one` accepted
> `control_replica_id`, used it for the ledger record, and never passed it to
> `evaluate()`, so it never reached the cache key. The values survived only in
> the campaign manifests, and P5's "N controls leave N inspectable rows" did not
> hold. Fixed and gated (T17g).

## 6. Cost, and the trial cap

85 slot-hours, 1452 M events, 33 h wall at 96 slots. The **4 h trial cap fired
once** in 420 trial rows — on the pathological candidate that had previously
blocked a campaign for 26 h. It was reported to the optimizer as a right-censored
observation, and bo_gp did not return to that region.

Sustained utilisation was **~47% of the 96 allocated slots**: with `--parallel 4`
each candidate gets 24 workers, and a 128 sub-run trial runs 6 waves whose last
uses 8 of 24. `--parallel 6` (16 workers each) would divide 128 exactly and is
worth roughly another 1.5×; it was not changed because 4 is pinned in the
recommendation and asynchrony affects how much the GP must fantasise.

## 7. What is still required before any ranking is quotable

Per §7 of the recommendation, all of these remain undone:

1. **Seeds 2 and 3** — one trajectory cannot separate bo_gp from cmaes, and the
   rule requires a median and range over ≥3 seeds. **~66 h** at the measured rate.
2. **Held-out M/L confirmation** of the top 3–5 candidates per method
   (§5 funnel). The v2 lesson stands: the S tier has already flipped a top-1 in
   this project.
3. **The P3 decision** — declare the minimum ground-plane gap/`T_c`, or the
   multi-objective criterion. Everything above says the current objective is
   answerable only by a device that probably cannot work.
