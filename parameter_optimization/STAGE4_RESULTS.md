# Stage 4 results — best property vector found, and its nearest real materials

Campaign `stage4_property_v1`, run 2026-08-21 on mimir (shared host, ~95%
occupied by another user throughout).
Method and design: [`STAGE4_PROPERTY_OPTIMIZATION_PLAN.md`](STAGE4_PROPERTY_OPTIMIZATION_PLAN.md).
Audit and remediation: [`STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md`](STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md),
[`STAGE4_AUDIT_REMEDIATION.md`](STAGE4_AUDIT_REMEDIATION.md).

> ### Validity notice — read before quoting any number here
>
> An implementation audit on **2026-08-24** found that the projected substrate's
> Geant4 density carrier was dropped between the projection step and the
> simulation, so **every real-material verification run was simulated as G4_Si
> at 2330 kg/m³** whatever material it was labelled with. §4.3 is therefore
> **withdrawn**, including the claim that SiC recovers 77% of the gain. The
> defect is fixed and gated (`tests_stage4.py` T15a–T15j); the affected rows are
> registered, with their measured error, in
> [`stage4_invalidations.yaml`](stage4_invalidations.yaml).
>
> **The corrected reruns are COMPLETE and CONVERGED** (2026-08-25), through the
> S, M and L tiers under the corrected 8-replica contract, and are in §4.3 —
> which is authoritative. Where this document's older prose and §4.3's table
> disagree, **the table is right**. Headline: SiC recovers **76.2%** of the ideal
> gain (−53.3% vs baseline), `GaAs/Nb/Cu` **44.6%** (−31.2%); M→L RMS shift
> 0.7%, inside the ≤1.6% convergence criterion.
>
> The audit also withdrew the **optimizer-efficiency ranking** of §2 (the
> winning point came from the Sobol initialisation, not from a GP acquisition)
> and the **drift-control claim** of §5.3 (the controls were cache hits). The
> **property-space result itself — §0, §3, §5b, §6 — is unaffected**: those
> candidates are pseudo-materials whose intended carrier *is* G4_Si, so the
> defect could not reach them. All 163 recorded trials were re-checked and every
> one still resolves to its stored identity (gate T15h2).
>
> **Which rows need rerunning, checked mechanically.** The whole ledger was
> swept: every stored candidate matched against every projection point the
> campaign could have produced, and its intended carrier compared with what
> `derived` actually recorded. **11 projection-derived trials exist; 9 are
> affected; every other trial is untouched** — the defect can only bite a
> candidate whose intended carrier was not `G4_Si`, and only the projection ever
> set one. So the fidelity ladder of §6 stands as it is, and the 1e6 / 1e7 / 1e8
> tiers do **not** need repeating for the baseline or for any optimizer's best
> point. `STAGE4_AUDIT_REMEDIATION.md` has the row-by-row table.
>
> Note also that the defect reached the **point files**, not only the results:
> `results/stage4_confirm_points*.json`, `stage4_points_fair.json` and
> `_xl_pair.json` all store `substrate_carrier: null`. They must be regenerated
> from `stage4_project_material.py`, never reused for the rerun.
>
> Run `python stage4_audit.py` to re-check every claim in this file against the
> ledger, the contract and the templates.

> **Scope of every number below.** Simulated **junction** quasiparticle yield —
> QPs counted at the 17 Al junction electrodes, not total QP generation in the
> device — under this G4CMP model, this fixed 16-site injection scenario set,
> this calibrated (not independently validated) interface model, and this event
> budget. Ground-plane QPs, bottom-film QPs, microwave loss and thermal
> quasiparticles are all **outside** it (§3, device caveat). It is **not** a
> logical-error rate, and a pseudo-material property vector is a property
> **target**, not a material — which is what §4 exists to convert.

## 0. Headline

All figures at **1e7 events per sub-run** (3.2e8 per candidate) on a **held-out
seed bank** — the budget at which v2 showed the ranking converges, and at which
this campaign's values move by ≤1.6% when the budget is raised another 10×. §6
has the full 1.25e5 → 1e6 → 1e7 → 1e8 ladder.

| | **1e7/sub-run** | 1e8/sub-run | vs baseline | status |
|---|---:|---:|---:|---|
| Baseline (`Si / Nb / Cu` property vector) | 3.843e-4 | 3.886e-4 | — | valid |
| v2 best **real** triplet `Ge/Nb/Cu` (converged) | 2.494e-4 | — | −36% | valid |
| **Best confirmed property vector found** | **1.158e-4** | 1.176e-4 | **−69.9%** | valid |
| — best point found by random search | 1.359e-4 | **not obtainable** | −64.6% | valid at 1e7 |
| — best point found by CMA-ES | 1.433e-4 | 1.447e-4 | −62.7% | valid |
| ~~with a real SiC substrate tensor and density~~ | ~~1.865e-4~~ | ~~1.895e-4~~ | ~~−51.5%~~ | **INVALID — ran as G4_Si** |

The paired, site-matched comparison of the best point found against the baseline
is **z = −15.3** at 1e6, winning at **16 of the 16 injection sites**. Raising the
budget a further 10× (to 3.2e9 primary events per candidate) moves every
completed value by **≤1.6%**, so the *value of this point* is converged. That is
not the same as the *search* being converged — §5b shows six single-variable
moves that beat it, so it is the **best point found**, not an optimum.

**What the last row was supposed to say, and why it cannot.** The point of the
exercise is that the property-space gain is real and confirmed but **not
reachable with the catalogued material triplets**, so the question is how much
of it a real, purchasable substrate recovers. The SiC row was the answer, and it
was simulated at Si's density (2330 kg/m³) instead of the intended
`G4_CALCIUM_FLUORIDE` carrier's 3180 — a **−26.7% density error**, hence sound
speeds **+16.8%** too fast and acoustic impedance **−14.4%** too low, on top of
SiC's measured `C44` = 241 GPa having been clipped to the search box's ceiling of
200. It has to be rerun; the fix is in place and gated, the rerun is not done.

## 1. What was searched

16 continuous properties + 13 symmetry-reduced crystal orientations, all taken
from the "Tune the following parameters" block of `parameter_set.txt`:
top-film sound speed / gap / phonon lifetime / density, bottom-film sound speed
/ pair-breaking threshold / phonon lifetime / density, substrate `C11`, `C12`,
`C44`, isotope scattering `scat`, anharmonic decay `decay`, `decayTT`, plus
lattice temperature, lattice rotation and Miller direction.

Deliberately held out, each with a measured reason rather than an assumption:
substrate **density** (Geant4 takes it from the NIST material, and it is
redundant given a free elastic tensor), **Debye** (bit-identical A/B), and the
**lattice constant** (bit-identical A/B — 1578 vs 1578 QPs, §5.2).

Every candidate passed seven hard gates (Born stability, Christoffel
positivity, mode ordering, interface probabilities in [0,1], the excitation
chain, ground-plane regime consistency, bottom-threshold range) **before** a
Geant4 process was created — the optimizers propose only pre-screened points, so
across 92 scored trials there were **0 gate rejections and 0 failed
observations**. Three further trials were still in flight when the campaigns
were stopped and had accumulated **12 watchdog timeouts** between them; they are
recorded as incomplete scenario sets and were never scored (§5.4).

## 2. Optimizer comparison

All three ran the same contract, the same 16 injection sites, the same seed
bank, the same objective and the same evaluator; only the search strategy
differed. Screening fidelity: 4e6 primary events per candidate (~5% 1σ).

| optimizer | trials | events | best objective | vs baseline | median trial |
|---|---:|---:|---:|---:|---:|
| **bo_gp** (GP + Expected Improvement) | 23 | 92M | **1.150e-4** | **−70.8%** | 3.80e-4 |
| cmaes (with IPOP restarts) | 32 | 128M | 1.185e-4 | −70.0% | 4.05e-4 |
| random (mandatory baseline) | 37 | 148M | 1.680e-4 | −57.4% | 4.80e-4 |

Best-so-far against cumulative cost — the only fair axis, since a method that
spends more evaluations should find more:

| trials evaluated | 8 | 16 | 24 | 32 |
|---|---:|---:|---:|---:|
| bo_gp | 1.60e-4 | **1.15e-4** | — | — |
| cmaes | 2.25e-4 | 2.25e-4 | 1.58e-4 | 1.19e-4 |
| random | 2.37e-4 | 1.70e-4 | 1.70e-4 | 1.68e-4 |

> ### This ranking is WITHDRAWN (audit P1)
>
> The table above is what the campaign reported. It does not survive the audit:
>
> * The `bo_gp` manifest records 23 observations, `n_init = 20`, and **one** GP
>   fit; the ledger holds **one** trial with a non-null acquisition value. The
>   winning point **1.150e-4 was proposed before the GP phase** — it is a Sobol
>   initial-design draw, and crediting it to GP Expected Improvement attributes a
>   quasi-random result to Bayesian optimization. The asynchronous initial-design
>   test counted only completed observations, not pending ones, so more than
>   `n_init` Sobol points were dispatched.
> * The `cmaes` manifest records **one** completed generation, population 12,
>   zero restarts. When a generation was full but waiting on a slow member the
>   strategy returned a uniformly random feasible point, and that point was
>   stored and reported as a CMA-ES observation although it took no part in any
>   generation update. So the run tested neither multi-generation adaptation nor
>   IPOP.
> * One trajectory per method. No repeated optimizer seeds, so even a correct
>   ranking would be one draw.
>
> **What survives:** the measured property vectors and their confirmed
> objective values (each method's best point is a real, re-simulated candidate).
> **What does not:** any statement that one algorithm was more sample-efficient
> than another.
>
> Fixed in code: every proposal now carries an immutable `proposal_source`
> (`sobol_init` / `gp_ei` / `cma_generation` / `cma_incumbent_reeval` /
> `random_filler` / `random_baseline` / `llm` / `fallback`), its generation or
> GP-fit number, its acquisition value, and whether it entered the optimizer
> update — all written to the ledger. CMA-ES is synchronous by default and idles
> a slot rather than mislabelling filler. Gates T16a–T16i.
>
> **Rerun design** before this section may be restated: same frozen contract and
> screening fidelity for every method; the same *paid event budget* including
> partial failed work; BO with ≥24–32 space-filling points followed by ≥50 real
> GP acquisitions; CMA-ES for ≥8–10 complete generations, with IPOP claimed only
> if a restart actually happens; random and Sobol both retained as baselines;
> **≥3 optimizer seeds**, reported as a median and range.

> **But the screening tier ranked the runners-up wrongly.** Re-simulated at
> 1e7 events per sub-run on held-out seeds, **random search's winner (1.359e-4)
> beats CMA-ES's winner (1.433e-4)** — the opposite of the screening tier, where
> CMA-ES's looked 42% better. bo_gp's winner is unaffected (1.150e-4 →
> 1.158e-4). So the defensible claim is **bo_gp first, and CMA-ES versus random
> unresolved by the screening tier**; §6 has the full ladder. Sample efficiency
> — how fast each strategy drove the objective down — is unchanged, because that
> is measured on each strategy's own trajectory.

Random search is not embarrassed: it found −57% on its own (and −64.6% once
re-measured), which says the property space is broadly favourable rather than
containing one narrow optimum.

## 3. The best point found, and what it is doing physically

| variable | baseline (`Si/Nb/Cu`) | best found | change |
|---|---:|---:|---|
| `sub_c11` / `sub_c12` / `sub_c44` [GPa] | 165.6 / 63.9 / 79.5 | 399.6 / 156.4 / 179.6 | ~2.3× stiffer |
| → derived `v_L` / `v_T` [m/s] | 9017 / 5370 | **13858 / 8153** | +54% |
| `sub_scat` [s³] | 2.43e-42 | **1.46e-43** | **17× less** |
| `sub_decay` [s⁴] | 7.41e-56 | 6.56e-56 | −11% |
| `sub_decayTT` | 0.74 | 0.943 | +27% |
| `topfilm_gap` [eV] | 1.5384e-3 | **8.16e-5** | **19× smaller** |
| `topfilm_ph_lifetime` [ns] | 0.00417 | 0.00324 | −22% |
| `topfilm_vsound` [km/s] / density | 2.444 / 8570 | 2.518 / 9032 | ~unchanged |
| `bot_vsound` [km/s] / density | 2.608 / 8960 | 3.314 / 5070 | impedance 2.34e7 → **1.68e7** |
| `bot_ph_lifetime` [ns] | 5.1 | 2.66 | **half** |
| `bot_gap_thres` [eV] | 180e-6 | 220e-6 | +22% |
| orientation | (0,0,1) at 45° | **(2,2,3) at 63.8°** | off-axis |
| temperature [K] | 0 | 0.056 | not resolved (§5.2) |

Read as a mechanism, and consistent across all three optimizers independently:

1. **Make the ground plane out-compete the junctions for phonons.** The best
   point found drives the top-film gap down to 82 µeV, i.e. `2Δ_film = 163 µeV`, *below* the
   Al junction's 382 µeV pair-breaking gate. A film with that gap absorbs
   phonons across the whole band the junction can use, and below it as well. It
   is a phonon sink installed next to the qubits. This is the single largest
   lever in the space, and it is the property-space generalisation of the v2
   observation that Ti (gap 61 µeV) was competitive as a ground film.
2. **Cut isotope scattering by ~17×.** Less mass-defect scattering keeps the
   cascade ballistic instead of diffusive, so energy reaches the metal
   boundaries (where the films take it) rather than random-walking in the
   substrate under the electrode array. At 10 meV the isotope mean free path is
   sub-micron in Si, so this is a first-order change to the transport regime.
3. **Stiffen and speed up the crystal.** `v_L` +54% at fixed density moves
   energy out of the injection neighbourhood faster, and changes the focusing
   caustics that the 17 junctions sit in.
4. **Let the back side swallow energy and not give it back.** Lower bottom-film
   acoustic impedance (1.68e7 vs 2.34e7) plus half the phonon lifetime: more
   transmission into the backing metal, less re-emission into the substrate.
   This is the same direction as v2's strongest result (Cu beat Au everywhere;
   Au's longer lifetime and lower absorption were the reason).
5. **Point the crystal off-axis.** (2,2,3) at 63.8° rather than (0,0,1) at 45°.
   Orientation steers the phonon caustics; a high-index direction defocuses them
   relative to the junction array.

> ### Device caveat this model does not capture — now quantified (audit P3)
>
> A ground plane with an 82 µeV gap means a low-Tc superconductor (In, Sn, Zn,
> Ti). Such a film hosts its own thermal quasiparticles and has substantially
> higher microwave loss than Nb or Ta. The optimizer is free to install a phonon
> sink next to the qubits because **nothing in this objective penalises
> ground-plane loss or the ground plane's own quasiparticles** — it counts QPs at
> the 17 Al junctions and nothing else.
>
> `stage4_objectives.engineering_diagnostics()` now puts a number on it. At a
> 20 mK operating temperature, from the BCS gap alone:
>
> | ground plane | implied `T_c` | `2Δ_film / 2Δ_Al` | phonon sink? | log₁₀(n_qp / Nb) | log₁₀(loss / Nb) |
> |---|---:|---:|---|---:|---:|
> | Nb (baseline) | 10.12 K | 8.05 | no | 0 | 0 |
> | best point found (82 µeV) | **0.54 K** | **0.43** | **yes** | **+366** | **+367** |
>
> Those are equilibrium BCS estimates using Al's density of states — they rank
> ground-plane risk, they are not a predicted *Q* or a predicted QP density. But
> the exponent is the finding: the design the objective prefers is hundreds of
> orders of magnitude worse on the axis the objective cannot see. **Lever 1 is a
> phonon-sink design hypothesis, not a recommendation.**
>
> A campaign that wants the trade-off enforced rather than discovered declares it
> in the contract: gate **G8** takes `topfilm_tc_min_K`, `topfilm_gap_min_eV` or
> `topfilm_gap_over_junction_min` under `space.constraints`, and rejects the
> candidate before Geant4 starts. It is **off** in `stage4_config.yaml` — the
> campaign on record ran unconstrained, and switching it on retroactively would
> change what these numbers mean. Where no candidate dominates on both axes,
> `stage4_objectives.pareto_front()` returns the finalists as a Pareto set
> instead of picking a winner by an unstated weighting.

## 4. Nearest real materials (the projection step)

### 4.1 Distance in physical space

Candidates are compared on `(v_L, v_T, anisotropy, acoustic impedance, scat,
decay, decayTT)` for substrates and `(sound speed, gap, phonon lifetime,
impedance)` for films — each computed from the material's **own** tensor and
density, never from the optimizer's raw constants. Features a material has no
sourced value for are excluded from its distance and reported as unmatched.
Pool: the 378 experimentally-observed, non-metallic, non-magnetic, Born-stable
cubic materials of the frozen Materials Project snapshot, plus the five
crystals with a complete G4CMP lattice record, plus 14 superconducting and 8
normal-metal films.

Rankings are now **stratified by feature coverage** (audit P4). A 4/7 distance
and a 7/7 distance answer different questions, and the old single ranking put
them in one list — where a material could place first by being *unmeasured*,
since the missing features were simply dropped from its distance. The three
missing ones (`scat`, `decay`, `decayTT`) are also the strongest optimization
directions in the space.

**Substrate — 7/7 features matched** (complete G4CMP record; 5 of 378 candidates):

| rank | material | distance |
|---:|---|---:|
| 1 | Si | 5.47 |
| 2 | GaAs | 5.75 |
| 3 | CaF₂ | 6.04 |
| 4 | Ge | 7.00 |
| 5 | LiF | 7.38 |

**Substrate — 4/7 features matched** (elasticity + impedance only; `scat`,
`decay`, `decayTT` unsourced; 373 candidates):

| rank | material | distance |
|---:|---|---:|
| 1 | Be₃N₂ (mp-18337) | 0.38 |
| 2 | SiC (mp-8062) | 0.68 |
| 3 | BP (mp-1479) | 0.79 |
| 4 | Be₂C (mp-1569) | 0.94 |
| 5 | LiH (mp-23703) | 1.26 |

**Films**, same treatment: top film 4/4 — Nb 0.96 · Ti 1.26 · Al 1.33 · Ta 2.88;
top film 3/4 (lifetime unsourced) — In 0.76 · Sn 0.79 · Zn 0.80 · Pb 1.06;
bottom film 3/3 — Cu 0.53 · Au 1.44; bottom film 2/3 — Ag 0.47 · Pd 1.05 ·
Pt 1.43.

**The two substrate tables must not be read as one.** The 4/7 numbers are an
**elasticity-and-impedance distance**, nothing more: `scat`, `decay` and
`decayTT` are **unknown for every cubic material outside the shipped G4CMP
records**. That is not a defect of the metric; it is the state of the input
data, and it remains the single biggest thing standing between this result and a
fabrication decision. Where those constants must be borrowed,
`stage4_project_material.py --phonon-brackets` now simulates the **low and high
edges of the range spanned by the measured records** alongside the nominal
value, so an unmeasured direction is reported as a bracket rather than as the
design target's number wearing a material's name.

The film rankings are consistent with §3: the optimizer wants a low-gap ground
plane (In 0.54 meV, Sn 0.58 meV, Zn 0.13 meV) and a low-impedance, short-lifetime
bottom film (Ag, Cu).

### 4.2 What can be simulated as-is

| layer | closest candidate with a complete record **and** a measured Geant4 density |
|---|---|
| substrate | Si (5.47) · GaAs (5.75) · CaF₂ (6.04) · Ge (7.00) · LiF (7.38) |
| top film | Nb (0.96) · Ti (1.26) · Al (1.33) · Ta (2.88) |
| bottom film | Cu (0.54) · Au (1.44) |

The substrate distances are an order of magnitude worse than the MP candidates'
— every catalogued substrate is far slower and softer than the optimum wants.

### 4.3 Verification — WITHDRAWN, must be rerun

> **Every real-material row of this section is invalid.** The projection
> attached the intended Geant4 density carrier to the candidate as a loose extra
> dictionary key; `Space.complete()`, `candidate_payload()` and
> `stage4_confirm.collect_points()` each kept only the variable table, so the
> resolver fell back to `G4_Si` and **all six verification runs were simulated
> at 2330 kg/m³**. G4CMP takes the substrate density from the `G4Material`
> (`G4LatticeManager::LoadLattice` → `SetDensity(Mat->GetDensity())`), so this is
> not a labelling slip: every derived sound speed and impedance in those runs is
> the Si-density one.

What was reported, and what was actually simulated:

| candidate | reported | intended carrier | actually ran | density error | ⇒ speed error | verdict |
|---|---:|---|---|---:|---:|---|
| baseline (`Si/Nb/Cu` property vector) | 3.675e-4 | G4_Si | G4_Si | 0% | 0% | **valid** |
| ideal property target | 1.210e-4 | G4_Si | G4_Si | 0% | 0% | **valid** |
| elasticity of SiC | 1.785e-4 (−51.4%) | G4_CALCIUM_FLUORIDE (3180) | G4_Si (2330) | −26.7% | **+16.8%** | **INVALID** |
| elasticity of Be₂C | 2.095e-4 (−43.0%) | G4_BORON_CARBIDE (2520) | G4_Si (2330) | −7.5% | +4.0% | **INVALID** |
| `GaAs/Nb/Cu` triplet | 3.510e-4 (−4.5%) | G4_GALLIUM_ARSENIDE (5310) | G4_Si (2330) | −56.1% | **+51.0%** | **INVALID** |
| `Si/Nb/Cu` triplet | 3.730e-4 (+1.5%) | G4_Si | G4_Si | 0% | 0% | **valid** (checked, not assumed) |

The higher-fidelity repeats of the SiC and Be₂C rows (M, L and XL tiers) carry
the same defect. All ten rows are listed with their trial ids in
[`stage4_invalidations.yaml`](stage4_invalidations.yaml) and are **retained**,
not deleted — they are the evidence that the defect was real.

**Claims withdrawn:** "SiC recovers 77% of the ideal gain"; "elasticity of SiC =
−51.4%"; "elasticity of Be₂C = −43.0%, 64% of the gain"; "GaAs/Nb/Cu = −4.5%";
and the whole "fraction of ideal gain realized" column.

**Update 2026-08-25 — the corrected rerun landed, and it revises this.** The
claim above ("catalogued triplets realize essentially none of the gain") rested
on the defective `GaAs/Nb/Cu` row. Re-simulated with GaAs's own density *and*
its own complete G4CMP record, at the same tier and seed bank:

| candidate | S (4e6) | of ideal | **M (32e6)** | **of ideal** | ~~withdrawn M~~ |
|---|---:|---:|---:|---:|---:|
| ideal target | 1.210e-4 (−67.1%) | 100% | **1.170e-4 (−70.5%)** | **100%** | — |
| elasticity of SiC | 1.935e-4 (−47.3%) | 70.6% | **1.821e-4 (−54.1%)** | **76.8%** | ~~1.930e-4, −51.4%, 64%~~ |
| `GaAs/Nb/Cu` | 2.890e-4 (−21.4%) | 31.8% | **2.659e-4 (−33.0%)** | **46.8%** | ~~3.449e-4, −13.2%~~ |

Paired and site-matched at M: SiC z = −12.3 (13/16 sites), GaAs z = −5.1 (14/16).

> **The 77% headline is back — by a different route, and that is a coincidence,
> not a vindication.** The withdrawn claim was 77% *at the S tier with the wrong
> density*. The corrected number happens to be 76.8% *at the M tier with the
> right one*. The old number was wrong for its stated reasons; do not read this
> as the original claim having been correct all along.
>
> **Both candidates improve markedly from S to M** (SiC 70.6% → 76.8%, GaAs
> 31.8% → 46.8%), which is the v2 screening-tier lesson repeating: the S tier
> gets the ordering right and the magnitudes wrong. Neither is converged — the
> project's converged tier is 1e7/sub-run (L), which has **not** been run for
> these.

`Si/Nb/Cu` still returns the baseline, so the property target is genuinely not a
re-labelling of the v2 study. But **GaAs recovers about a third of the ideal
gain, not 7%**, so "essentially none" is withdrawn too. The honest form: the
property target is roughly three times better than the best catalogued triplet
in recovered gain, not an order of magnitude.

### The corrected projection is now CONVERGED (2026-08-25)

The full ladder, held-out seed bank 9, under the corrected 8-replica contract:

| candidate | S (1.25e5/sub) | M (1e6/sub) | **L (2.5e6/sub)** | of ideal gain | M→L shift |
|---|---:|---:|---:|---:|---:|
| ideal target | 1.210e-4 (−67.1%) | 1.170e-4 (−70.5%) | **1.174e-4 (−70.0%)** | 100% | ±0.0 pt |
| elasticity of SiC | 1.935e-4 (−47.3%) | 1.821e-4 (−54.1%) | **1.825e-4 (−53.3%)** | **76.2%** | −0.6 pt |
| `GaAs/Nb/Cu` | 2.890e-4 (−21.4%) | 2.659e-4 (−33.0%) | **2.689e-4 (−31.2%)** | **44.6%** | −2.2 pt |
| baseline | 3.675e-4 | 3.970e-4 | 3.910e-4 | — | — |

At L: errors **±0.7–1.5%**, paired z of −68.1 (ideal), −47.2 (SiC) and −23.7
(GaAs), winning 16/16, 16/16 and 15/16 sites. Rank agreement across every tier
pair is ρ = τ = 1.000, and the **M→L RMS value shift is 0.7%** — inside the
project's ≤1.6% convergence criterion. Both adjacent pairs are resolved at 2σ
with wide margins (55.5% gap vs 3.7% error; 47.4% vs 2.5%).

**An independent cross-check the replica change made possible.** The original
campaign measured the ideal target at **−69.9%** using 32 sub-runs of 1e7
events. This one measures **−70.0%** using 128 sub-runs of 2.5e6 — a different
sub-run split, different seeds, different block structure, and a code base whose
fingerprint has changed five times since. Agreement to 0.1 points is strong
evidence that none of the audit's changes moved the physics.

**These numbers are now quotable**, with the standing scope caveat: simulated
*junction* QP yield, under this model, this 16-site scenario set, this
calibrated interface model. Not a logical-error rate, and not yet a fabrication
recommendation — §5.5's limitations and the P3 ground-plane trade-off still
apply, and P6's spatial/lifetime/interface systematics remain unrun.

**What the rerun must do differently.** Beyond the carrier fix:

* SiC's measured `C44` = 241 GPa was **clipped to the search box's ceiling of
  200**, and Be₂C's `C11` = 570 / `C44` = 201 were clipped to 450 / 200. A
  measured constant is a fact, not a proposal: the box is now waived for
  variables the realization declares as the material's own, and the clip is
  reported instead of applied.
* GaAs has a **complete G4CMP record**, so it should never have gone through the
  pseudo-material path at all — it ran with Si's `dyn`, DOS and Debye wearing
  GaAs's tensor. It now routes through `native_g4cmp`, which copies its own
  record verbatim.
* The In/Ag film lifetimes remain unsourced. Keeping the target's value makes
  the row a bound, not a materials claim, and it must be labelled as one.
* `scat`/`decay`/`decayTT` remain unmeasured for SiC. The target asks for 17×
  less isotope scattering than Si; for SiC that means isotopic purification
  (natural Si is already 92.2% ²⁸Si and natural C 98.9% ¹²C, so ³⁰Si/¹³C
  enrichment is the physical knob). Run the rerun with `--phonon-brackets` so
  the answer is a range.

Be₃N₂ (the nearest substrate in the 4/7 stratum) and BP still cannot be verified
at all: no NIST material sits within 3% of their density (3.3% and 6.7% away).
That gap is now a **hard gate** — a realization declaring a real density the
carrier cannot represent is refused before Geant4 starts, rather than silently
carried. Closing it properly needs a one-command
`BuildMaterialWithNewDensity` addition to `PhononDetectorConstruction`, which
would change the executable and therefore the code fingerprint, so it is a
deliberate, separate step.

## 5. Validation, cost and caveats

### 5.1 Exit gates

`tests_stage4.py` — **39/39 pass**, including: the baseline property vector
reconstructs the v2 interface constants (0.795 / 0.745 / 0.736) and speeds
(9016.7 / 5369.5 m/s) exactly and, simulated, reproduces the v2 `Si/Nb/Cu`
total of **1578 QPs**; per-sub-run scoring is exactly equal to pooled scoring on
a recorded v2 trial; a failed or incomplete trial can never become a zero; the
GP's gradients, posterior and acquisition match independent computations; and
the agentic optimizer's guard rails hold under malformed, out-of-bounds,
infeasible, chatty and unreachable LLM responses.

### 5.2 Measured physics A/Bs

| variable | result |
|---|---|
| lattice constant `cubic a` (5.431 → 6.5 Å) | **bit-identical** (1578 vs 1578 QPs) → inert, correctly held out |
| `/g4cmp/temperature` (0 → 0.1 K) | 1578 → 1498 QPs, −5.1% against a 5.9% replica SE → **not resolved**; kept in the search |

### 5.3 Noise

Across three independent seed banks at the baseline: across-bank CV **4.6%**,
within-run replica SE **5.6%**, Poisson would be 2.55% → **implied Fano 4.9**,
independently reproducing the v2 scaling study's ~5. Every uncertainty quoted
here is the replica-based one, never Poisson.

Two consequences visible in this campaign's own numbers:

* the baseline reads **3.945e-4** on seed bank 0 and **3.675e-4** on the
  held-out bank 9 — **7.3% apart**, which is why every comparison in this
  document is made against the baseline *from the same bank*, never across banks;
* ~~the drift control returned 6 re-evaluations with 0.0% spread, so nothing in
  the machine or the code moved under the campaign.~~ **WITHDRAWN (audit P5).**
  `baseline_control()` called the ordinary evaluator without `force`, so the
  cache returned the original row every time. The 0.0% spread measured **cache
  stability**, not machine, executable or runtime stability, and the campaign
  therefore has **no** drift evidence at all.

  Fixed: each control now carries its own `control_replica_id` — the one
  non-physics field in the cache key — so it executes fresh and mints its own
  trial row instead of overwriting the previous one. Six controls leave six
  independently inspectable records with their own code fingerprint, host,
  worker shape, timestamp and runtime; the log distinguishes `fresh` from
  `CACHE HIT -- not a fresh control!`; and controls are excluded from
  `observations()`, so they never reach the surrogate and never appear on the
  event-efficiency curve. Since exact replay is unavailable on this executable
  (§5.5 item 4), drift is judged against the measured stochastic repeat
  distribution rather than by bit equality. Gates T17a–T17d.

### 5.4 Cost, and a real dynamic

Median trial 341 s (range 80–2796 s) at 16 sub-run workers on a host that was
~95% occupied by another user. Spearman(objective, runtime) = **+0.59** at the
screening tier (n = 95, p = 4e-10; reproduced exactly on 2026-08-25): higher QP
yield costs more CPU *at this fidelity*.

> **Do not carry that forward to the converged tier.** This section previously
> concluded "so the optimizer's preferred region is the cheaper one". That
> inference is **withdrawn** — it is a screening-tier statement being used as a
> budgeting rule, and the confirmation tiers do not support it:
>
> | tier | events/candidate | Spearman(objective, runtime) | n | p |
> |---|---:|---:|---:|---:|
> | screening | 4.0e6 | **+0.59** | 95 | 4e-10 |
> | L | 3.2e8 | −0.60 | 5 | 0.28 |
> | XL | 3.2e9 | −0.40 | 4 | 0.6 |
>
> The confirmation tiers are **not evidence of a reversal** — n = 4–5, p ≫ 0.05,
> and they are range-restricted by construction (they contain the *winners*, so
> the objective barely varies). The honest reading is that the screening
> correlation simply does not transfer, not that it flips.
>
> What *is* unambiguous needs no correlation at all: at 1e8 events/sub-run the
> two lowest-objective candidates were the two most expensive — `best_bo_gp`
> **28.9 h** and `best_random` **>41.7 h (timed out)** — against the baseline's
> **8.3 h** at more than three times the QP yield. §6 already said this ("the
> low-absorption designs the search prefers are the expensive ones"); §5.4
> contradicted it, and §6 is the one the data supports.
>
> **Budgeting consequence, and it is the practical point:** for the corrected
> campaigns, assume the *good* candidates are the expensive ones at high
> fidelity. Sizing a confirmation budget from screening-tier runtimes will
> under-provision exactly the candidates worth confirming — which is how a 41.7 h
> watchdog came to be too short.

But the tail matters: **12 sub-runs hit the 1-hour watchdog**, all in a region
of very low absorption where phonons approach the 10 000-bounce limit before
being absorbed. Those trials are recorded as incomplete and are correctly never
scored as zero — but they consume slots, and because a rejection carries no
objective value, the surrogate does not learn to avoid them. Two consequences,
both actionable rather than fatal:

* the `bo_gp` campaign spent its last hour with all four slots in that region,
  which is why it has 23 trials to random search's 37;
* **do not fix this by shortening the watchdog** — that would preferentially
  kill exactly the low-absorption candidates the search is drawn to and bias the
  result. The right fix is to model failures (a feasibility classifier or
  censored-observation treatment), which is the first item in §6.

### 5.5 Limitations carried forward

1. **The interface model is calibrated, not validated**
   (`physics_validation_passed = False`). Both films act on the objective mainly
   through interface absorption, so the film half of this result is the part
   most exposed to that model being wrong.
2. **The 16-site quadrature is not converged.** One site carried ~50% of the
   baseline's QPs in the v2 audit. Paired differences (which is how every
   comparison here is made) cancel it; absolute yields do not.
3. **`dyn`, the Tamura DOS fractions and `Debye` are Si's** in every
   pseudo-material.
4. **Exact replay is not available on this executable** (§13.3 of the plan): a
   repeat with identical seeds diverges in ~1 run in 6, worth 0.76% at the trial
   level against a 5.9% stochastic error.
4b. ~~**The worker count and memory budget enter the contract hash**~~ —
   **CLOSED 2026-08-24 (audit finding N2).** Re-running a candidate with a
   different `--workers` used to mint a new cache key instead of reusing the
   completed trial; it was found when the fidelity ladder was relaunched from 32
   to 64 workers, which abandoned four in-flight trials that would otherwise
   have resumed.

   The fix separates the two identities that `contract_hash()` was conflating:
   **`campaign_contract_hash()`** (every section, including the campaign name —
   used for provenance and for deciding which trials may be replayed into one
   optimizer history) and **`simulation_identity_hash()`** (only what can change
   the generated macro, the resolved material, the ordered scenario, the seeds,
   the event count or the scored physics — and this is what the cache key is
   built from). `max_workers`, `total_mem_gb`, `per_sample_mem_gb`,
   `sample_timeout_s`, `campaign_id` and the search/scoring sections are
   excluded, each with its justification in the code. Gates T22a–T22i; both
   hashes are stored in the ledger.

   It was safe to do now precisely because the cache is already cold and the
   ledger is frozen — the concern that deferred it (invalidating trials the
   running chain depends on) no longer applies.
5. **The search box binds.** `sub_c11` (399.6 of 450), `sub_scat` (1.46e-43 of
   1e-44) and `topfilm_gap` (8.16e-5 of 5e-5) all optimize toward their bounds,
   and real SiC's `C44` had to be clipped. The next campaign should widen the
   box in exactly those directions.
6. **Total QPs is not a logical error rate**, and the ground-plane loss
   trade-off of §3 is outside this objective entirely.

## 5b. Local tolerance around the best point found

One-factor-at-a-time scan, ±12% of each variable's box width in its transformed
coordinate (so a log-scaled variable moves by a factor), at the same 4e6-event
fidelity and seed bank as the campaign. 31 of 32 perturbations completed around a
centre of **1.1500e-04** (`topfilm_ph_lifetime:minus` did not complete: its sub-runs were killed when the machine was re-tasked to the 64-worker fidelity-ladder run, so that one side is missing rather than slow).

The noise threshold is **2 × 12.4%**, twice this point's replica error as measured
*independently* in the held-out-seed confirmation run. Its own screening trial
reported 20%, but with two replicas per site that estimator is itself very noisy,
and the confirmation figure is also what 1/√N predicts from the baseline's 5.9%
at 3.4× the counts. Below the threshold a swing cannot be distinguished from noise
at this fidelity — which is itself the fabrication message: a specification the
objective cannot feel is one the process does not have to hold tightly.

This is a **local** statement about this optimum, not a global sensitivity
analysis; interactions are not resolved and it does not claim to resolve them.

| variable | max \|ΔJ\|/J at ±12% | reading |
|---|---:|---|
| `sub_c44` | 57% | **resolved** |
| `sub_c11` | 45% | **resolved** |
| `bot_gap_thres` | 22% | marginal |
| `sub_scat` | 21% | marginal |
| `topfilm_vsound` | 21% | marginal |
| `topfilm_gap` | 20% | marginal |
| `bot_density` | 19% | marginal |
| `temperature` | 17% | marginal |
| `sub_decay` | 16% | marginal |
| `sub_c12` | 15% | marginal |
| `lattice_deg` | 12% | below noise at this fidelity |
| `bot_ph_lifetime` | 12% | below noise at this fidelity |
| `sub_decayTT` | 9% | below noise at this fidelity |
| `topfilm_ph_lifetime` | 9% | below noise at this fidelity (one side only) |
| `topfilm_density` | 7% | below noise at this fidelity |
| `bot_vsound` | 1% | below noise at this fidelity |

Reading it: the **substrate elastic constants dominate the local landscape**, which
is consistent with §4 finding that the substrate is also where the optimum is
furthest from anything catalogued. The film properties are locally flat at ±12% —
they got the objective most of the way down and then stopped mattering, which is
why the film projection tolerates several near-equivalent metals.

**This is the best point found, not an optimum.** 6 single-variable moves beat
the centre by more than its own error, the largest being `sub_c11` down (−36%),
`bot_gap_thres` down (−22%), `topfilm_gap` down (−20%). The campaigns were
stopped on wall-clock, not on a convergence criterion, so this is expected — and
it is the cheapest available evidence of how much is still on the table.

> **How this scan is now judged (audit P2).** The numbers above compare noisy
> single values against the centre's own error. That is the wrong comparison:
> the 16 sites are common to both trials and cancel, so `stage4_tolerance.py`
> now computes a **paired, site-matched difference** for every move and reports
> its own `z`. It then applies a declared stopping rule and prints the verdict:
>
> A point is a **locally converged optimum within the declared box** only when
> (1) no tested trust-region move improves the paired objective by more than 2%,
> (2) that improvement is also below two paired standard errors, (3) no active
> bound is approached without a documented physical reason, (4) two successive
> model updates fail to improve the held-out incumbent, and (5) the result is
> stable across at least two optimizer seeds. The scan tests (1)–(3) and reports
> (4)–(5) as untested rather than assuming them.
>
> This point fails (1) and (3): `sub_c11` (399.6 of 450), `sub_c44` (179.6 of
> 200), `sub_scat` and `topfilm_gap` all sit at or near a box wall. The
> evidence-based widened limits for the next campaign — with the material that
> justifies each — are written into `stage4_config.yaml` next to the `space`
> block.

**Projection weights.** Feature weights proportional to the measured local
sensitivity, for `stage4_project_material.py --weights`. Treat them as
provisional: they come from this single OFAT scan, which the audit classes as
exploratory, and the paired-difference version above should be used to
regenerate them before any material is ordered.

```json
{"sub_vsound_m_s": 1.885, "sub_vtrans_m_s": 1.885, "sub_anisotropy": 1.885, "sub_impedance": 1.885, "sub_scat": 1.034, "topfilm_vsound_m_s": 1.034, "topfilm_impedance": 0.696, "topfilm_gap_eV": 0.95, "bot_impedance": 0.485, "sub_decay": 0.76, "bot_ph_lifetime_ns": 0.57, "sub_decayTT": 0.443, "topfilm_ph_lifetime_ns": 0.422, "bot_vsound_m_s": 0.063}
```

The §4 projection was run with **uniform** weights, the conservative choice, and
that is what its rankings reflect. Re-running it weighted — which would push the
substrate features up and `bot_vsound` down — is a one-line change and is worth
doing before any material is ordered.

## 6. Fidelity ladder — 1.25e5 → 1e6 → 1e7 → 1e8 events per sub-run

This is the question the v2 beamOn scaling study forced onto every campaign
here: at **1.25e5 events per sub-run** that study got the broad material ordering
right (Spearman 0.994) and **still flipped the top-1**. A screening tier is only
allowed to select if its ranking survives the move to a converged budget.

Every candidate below was re-simulated on the **same 16 injection sites** and the
**held-out seed bank 9**, at four budgets spanning a factor of 800. The set is
the fair one: the baseline plus **each optimizer's own best point**, plus the
SiC elasticity variant.

| candidate | 1.25e5 (screening) | 1e6 | 1e7 | 1e8 |
|---|---:|---:|---:|---:|
| **best of bo_gp** | 1.150e-4 | 1.170e-4 (−70.5%) | **1.158e-4 (−69.9%)** | **1.176e-4 (−69.7%)** |
| best of random | 1.680e-4 | 1.344e-4 (−66.1%) | 1.359e-4 (−64.6%) | **timed out** |
| best of cmaes | 1.185e-4 | 1.428e-4 (−64.0%) | 1.433e-4 (−62.7%) | 1.447e-4 (−62.8%) |
| elasticity of SiC | — | 1.930e-4 (−51.4%) | 1.865e-4 (−51.5%) | 1.895e-4 (−51.2%) |
| baseline | — | 3.970e-4 | 3.843e-4 | 3.886e-4 |

Three things this settles.

**1. The winner is converged.** From 1e7 to 1e8 — a 10× budget increase, 3.2e9
primary events per candidate — every completed value moved by **≤1.6%**
(bo_gp +1.6%, cmaes +1.0%, SiC +1.6%, baseline +1.1%). That reproduces the v2
finding (e7 vs e8 agreed to 1.6%, RMS 0.9%) on a completely different candidate
set, and it means **1e7 per sub-run is the converged tier for this objective**.
The headline −70% is a converged number, not a screening artefact.

**2. The screening tier ranked the runners-up wrongly.** It had CMA-ES's winner
at 1.185e-4 and random's at 1.680e-4 — a 42% gap in CMA-ES's favour. At 1e6 and
again at 1e7 the order is **reversed**: random 1.344e-4 / 1.359e-4 against CMA-ES
1.428e-4 / 1.433e-4. Both estimates moved by ~20% in opposite directions, which
is the winner's curse doing exactly what it does at a ±12–20% error. bo_gp's
winner did not move (+0.7% from screening to 1e7), so the top-1 held — but the
2nd/3rd order did not, and this is the v2 lesson repeating in a new setting.

**3. The error does scale as 1/√N after all.** §6.1 previously flagged that the
replica error had barely moved from 1.25e5 to 1e6 (5.6% → 4.9%) and might not be
shrinking. The 1e7 tier resolves it: the baseline's replica error is **0.6%** at
1.23e5 counts, against a Poisson 0.29% — an implied **Fano ≈ 4.3**, matching the
v2 estimate of ~5 and consistent with ordinary 1/√N scaling. The 4.9% measured at
1e6 was an excursion of the two-replica estimator (16 degrees of freedom), not a
breakdown of the scaling law. Errors quoted at 1e7 are 0.6–1.9%.

> ### `best_random` at 1e8 could not be measured (2026-08-25)
>
> All **32 of 32** sub-runs hit the 41.7 h watchdog (`--timeout 150000`). The
> trial is recorded `incomplete_scenario_set` and was **never scored** — which
> is the evaluator behaving correctly: a partial site set is biased toward the
> survivors and must never become a number. Its 1e7 value (1.359e-4, −64.6%)
> stands; its 1e8 value does not exist and is not pending.
>
> This does not weaken the ladder. Convergence rests on the candidates that did
> complete — every one moved ≤1.6% from 1e7 to 1e8 — and 1e7/sub-run remains the
> converged tier for this objective. It has been re-queued for the corrected
> campaigns with a longer watchdog; it is not a blocker.

Cost, measured: per-candidate wall time scales linearly with events at a fixed
worker shape (baseline 0.9 h at 1e7 → 8.3 h at 1e8; bo_gp 2.9 h → 28.9 h), and
varies ~5× **between candidates** at the same budget — the low-absorption designs
the search prefers are the expensive ones.

## 7. What to do next, in order

The audit's execution order, with what is already done marked. Everything marked
**code done** is implemented and gated by `tests_stage4.py`; everything marked
**needs budget** is a simulation campaign that has not been run.

| # | Action | Audit item | Status |
|---:|---|---|---|
| 1 | Freeze the artifacts; mark projection-derived material claims invalid without deleting the evidence | P0 | **done** — `stage4_invalidations.yaml`, §4.3 |
| 2 | Candidate realization envelope + end-to-end material identity tests | P0 | **code done** — T15a–T15j |
| 3 | Cheap SiC/GaAs propagation smoke test: inspect macro, runtime density, lattice source, ledger payload, cache key | P0 | **needs budget** (~1 h at 4e6 events) |
| 4 | Proposal-source accounting and the asynchronous optimizer fixes | P1 | **code done** — T16a–T16i |
| 5 | Re-run the optimizer comparison: ≥3 seeds, equal paid event budget, ≥50 real GP acquisitions, ≥8 CMA generations | P1 | **needs budget** (the largest item) |
| 6 | Widen the box around the best confirmed vector and require local convergence | P2 | **code done** (paired scan + stopping rule + documented limits); **needs budget** to run |
| 7 | Define the engineering objective/constraints before selecting a ground film | P3 | **code done** — G8, `engineering_diagnostics`, `pareto_front`; the *choice* of floor is a decision, not a computation |
| 8 | Rebuild the material shortlist with coverage-aware uncertainty | P4 | **code done** — stratified ranking, `--phonon-brackets` |
| 9 | Corrected real-material verification on held-out seeds, then promote survivors to high fidelity | P0/P4 | **needs budget** |
| 10 | Drift controls and systematic convergence (16/32/64 sites, lifetime brackets, alternate interface treatment) | P5/P6 | P5 **code done** — T17a–T17d; P6 **needs budget** |
| 11 | Synchronize documentation and ledger state; issue a revised summary with validity labels | P7 | **done** — this file, `stage4_audit.py` |

Carried over from the pre-audit list, still open and still worth doing:

* **Model the failure region** (feasibility classifier or censored observations)
  so the optimizer stops spending slots where the simulation cannot finish. Do
  **not** shorten the watchdog — that would preferentially kill exactly the
  low-absorption candidates the search is drawn to and bias the result.
* **Source `scat` / `decay` / `decayTT` for SiC, BP and Be₃N₂** (literature or
  DFT). Three numbers per material move them out of the 4/7 stratum and convert
  the projection from "closest by elasticity" into a checkable candidate.
* **Add a Geant4 density path** — the one-command `BuildMaterialWithNewDensity`
  addition to `PhononDetectorConstruction` — so Be₃N₂ and BP can be verified at
  their own densities instead of through a nearby, compositionally unrelated
  NIST carrier. This changes the executable and therefore the code fingerprint,
  so it invalidates the cache by design and belongs at the start of a campaign.
* **Put the interface model on an independent footing.** It is calibrated to
  reproduce the baseline constants but not independently validated
  (`physics_validation_passed = False`), and both films act on the objective
  mainly through interface absorption.
* **Enable the agentic optimizer** (§7.4 of the plan) if an LLM-in-the-loop
  comparison is wanted; it is implemented, guard-rail tested and now
  provenance-tagged (`llm`, `llm_init`, `llm_gp_ei`, `fallback`), but Ollama is
  not installed on this host.

## 8. Artifacts

| Path | Contents |
|---|---|
| `results/stage4_report.json`, `results/stage4_trials.csv` | every trial, every optimizer |
| `results/stage4_convergence.png` | best-so-far vs cost |
| `results/stage4_confirmation.json`, `..._S.json`, `..._M.json`, `..._L.json` | held-out-seed confirmation at 1.25e5, 1e6 and 1e7 events per sub-run |
| `results/stage4_fidelity_comparison.json` | rank agreement between budgets |
| `results/stage4_projection.json` | full rankings, shortlist, verification |
| `results/stage4_pilot.json` | determinism, noise, throughput, A/Bs |
| `results/stage4_tolerance.json`, `logs_stage4/tolerance.log` | local sensitivity and projection weights (the log carries every evaluated point; the JSON is written when the scan's last point finishes) |
| `stage4_trials.sqlite` | the ledger: every trial, sub-run, seed, status |
| `runs/stage4_property_v1_*/` | macros, lattice configs, hits files, logs, manifests |
