# Two findings from data already in hand — 2026-09-08

Both derived at **zero additional simulation cost** by re-reading the block-level
data in the ledger. Together they change what the next campaign should be.

---

## 1. P3 is nearly free — the low-gap phonon sink buys almost nothing

The concern behind P3 was that the objective counts junction QPs only, so the
optimizer could win by installing a low-gap ground plane that ruins the device.
It did exactly that: the unconstrained winners sit at `T_c` ≈ 0.33 K, **+374
orders of magnitude** in equilibrium QP density against Nb.

The unexamined assumption was that forbidding this would be *expensive*. It is
not. Screening all **402** simulated benchmark points against each candidate
floor:

| device-quality floor | survivors | best *J* | cost vs unconstrained |
|---|---:|---:|---:|
| none (current) | 402/402 | 5.30e-5 | 1.00× |
| 2Δ_film ≥ 0.50 × 2Δ_Al | 279/402 | 6.55e-5 | **1.24×** |
| 2Δ_film ≥ 2Δ_Al | 224/402 | 6.55e-5 | **1.24×** |
| ground-plane `T_c` ≥ 1.2 K (Al-like) | 226/402 | 6.55e-5 | **1.24×** |
| ground-plane `T_c` ≥ 4 K | 134/402 | 6.55e-5 | **1.24×** |
| **ground-plane `T_c` ≥ 9 K (Nb-like)** | 69/402 | **6.55e-5** | **1.24×** |

**Every floor, including the strictest, is satisfied by the same point** — and it
costs 24%. The best Nb-compatible design found so far (cmaes seed 1, trial
`5c1e62ff8466`):

| | unconstrained corner | **best Nb-gap-compatible point found** |
|---|---:|---:|
| *J* (S tier) | 5.30e-5 | **6.55e-5 ± 1.24e-5** |
| `topfilm_gap` | 5.0e-5 eV | **1.670e-3 eV** (modeled Nb: 1.5384e-3) |
| 2Δ_film / 2Δ_Al | 0.26 | 8.75 |
| phonon sink? | yes | **no** |
| BCS-inferred `T_c` | 0.33 K | 10.99 K |
| log₁₀(QP proxy / Nb) | **+374** | **−33** |
| variables at a box wall | 13/16 | **5/16** |

**Corrected claim, 2026-09-08.** An earlier draft called this an "interior
point" whose ground plane "beats Nb". Both were overstated:

* **It is not interior.** Five of sixteen variables sit on walls —
  `topfilm_vsound` = 6.0 (upper), `bot_ph_lifetime` = 0.5 (lower),
  `sub_c12` = 0 (lower), `sub_c44` = 5 (lower), `sub_scat` = 1e-44 (lower).
  Call it the **best Nb-gap-compatible point found**, not an optimum. Fewer
  walls than the corner is a weaker statement than "interior".
* **"Beats Nb" narrows to:** its *modeled* gap exceeds Nb's *modeled* gap, and
  its equilibrium-QP *proxy* is lower. `T_c` = 10.99 K is a weak-coupling BCS
  conversion applied to a **pseudo-film**, not a measured property, and its
  particular combination of density, sound speed and phonon lifetime may
  correspond to **no fabricable superconductor**. Nothing here identifies a
  material.

Its mechanism does differ from the corner's: `sub_c44` goes to its **lower**
bound where the unconstrained corner drove it to the upper. Without a phonon
sink beside the qubits, the optimizer solves the problem a different way — worth
understanding before the box is widened in either direction.

**Recommendation for the P3 decision (revised 2026-09-08):** impose a **direct
gap floor**

```yaml
topfilm_gap_min_eV: 1.5384e-3     # the modeled Nb gap
```

rather than `T_c ≥ 9 K`. The gap is what the simulation actually consumes;
inferring a `T_c` floor through weak-coupling BCS adds a conversion the model
never uses and that does not hold for a pseudo-film. The two select the same
points here, so nothing is lost and one modelling assumption is removed.

Whichever form, the finding stands: **every floor costs the same 1.24×**, and
there is no evidence of a trade-off worth preserving between 0.33 K and 9 K.

**The 69 feasible points are a warm start only if the objective does not
change.** They were measured on the 16-site scenario, and
J₁₆(x) ≠ J₃₂(x) ≠ J₆₄(x). The optimizer is not multi-fidelity, so those values
cannot be inserted into a 32- or 64-site GP as if they were observations of it.
See the decision rule below.

---

## 2. A single injection site carries most of every headline reduction

`STAGE4_RESULTS.md` §5.5 has always noted that one site carried ~50% of the
baseline's QPs, with the mitigation that "paired differences cancel it". That is
true for the **significance test** and false for the **magnitude** — and every
headline in this project is a magnitude.

Leave-one-site-out over the converged L-tier data:

| candidate | all 16 sites | drop the heaviest | LOO range over all 16 |
|---|---:|---:|---|
| ideal target | **−70.0%** | −54.3% | −71.3% … −54.3% (17.0 pt) |
| elasticity of SiC | **−53.3%** | −26.2% | −55.6% … −26.2% (**29.3 pt**) |
| `GaAs/Nb/Cu` | **−31.2%** | −22.8% | −31.9% … −22.8% (9.1 pt) |

Dropping one of sixteen sites **halves** SiC's claimed reduction.

The cause is geometric, not statistical. Site leverage is strongly
candidate-dependent — the baseline draws 49.3% of its QPs from one site, the
optimised candidates about 20% — and every reduction is a ratio of the two.

| site | position | distance to nearest electrode | share of baseline QPs |
|---|---|---:|---:|
| **11** | (−1.882, −0.015) mm | **0.119 mm** | **49.3%** |
| 13 | (+0.631, −1.127) mm | 0.390 mm | 8.4% |
| 12 | (−0.314, +2.428) mm | 0.530 mm | 5.6% |
| … | | median 0.734 mm | |
| 9 | (+3.782, −3.251) mm | 2.178 mm | 1.0% |

Site 11 lands **inside the 0.200 mm electrode island**. QP yield falls steeply
with distance to an electrode, so a 16-point quadrature is dominated by whichever
point happens to land nearest one. **The headline reduction is partly a property
of the Sobol draw, not of the material.**

This does not overturn the ranking — every candidate is measured on the same
sites, and the ordering is stable under LOO. It does mean the **magnitudes**
(−70%, −53%, −31%) are provisional until the quadrature is shown to converge.

A nested **16 → 32 → 64** site test is running at the M tier on the baseline,
the ideal target, SiC and the bo_gp corner. A different site count is a different
scenario, so those trials cannot be paired across counts; what is comparable is
the absolute yield and the ranking.

---

## Consequence for the next campaign

1. **P3 is decided cheaply**: a direct `topfilm_gap_min_eV: 1.5384e-3` floor, at
   a measured cost of 1.24×.
2. **Do not quote a reduction magnitude** until the site sweep lands.
3. Anchor the constrained search on the **best Nb-gap-compatible point found**
   (`5c1e62ff8466`) — as an incumbent, not as the geometric centre of every
   bound, and not as an interior optimum: five of its sixteen variables are on
   walls.

## Decision rule for the site sweep

Judge convergence primarily from **32 → 64**, on five quantities: absolute *J*,
percentage reduction from baseline, candidate ranking, maximum-site leverage,
and the change relative to the combined uncertainty.

| outcome | what to do |
|---|---|
| 16, 32 and 64 agree | keep the 16-site contract; the 69 feasible points are usable directly |
| 16 moves, 32 ≈ 64 | adopt 32 or 64 sites; **re-evaluate a diverse subset** of the 69 before feeding the optimizer |
| 32 and 64 still disagree | **do not optimize.** Redesign the quadrature — probably electrode-aware stratification, since uniform Sobol converges slowly around the sharp near-electrode peak |

**Warm-start rules, whichever branch.** Load the 69 as **raw physical vectors**
and re-transform them under the new bounds — never reuse old unit coordinates.
Add fresh Sobol points covering the newly opened directions, and record
historical versus new observations separately. For CMA-ES the 69 are **not**
completed generations: use them to choose an initial mean and perhaps an
elite-based covariance, then start generation accounting from zero.

If the campaign moves to 32/64 sites, re-evaluate ~16–24 diverse points from the
69 (including the current best) at the new site count, supplement with points
covering the widened directions, and build a fresh 32-point BO initialisation.

## Gap in the running sweep, and how it is handled

The sweep launched on 2026-09-08 covers the baseline, the old ideal target, SiC
and the bo_gp low-gap corner. **It does not include the Nb-gap-compatible
anchor**, so it cannot say whether `5c1e62ff8466` is stable under site
refinement.

The active point file is deliberately **not** being edited: changing it mid-chain
would make the 32- and 64-site stages evaluate a different candidate set from
the 16-site stage, destroying the comparison the sweep exists to make. The
anchor will be run afterwards at the same 16/32/64, M-tier, held-out-bank-9
protocol.

## How to construct the new box (after the site result)

1. Impose `topfilm_gap_min_eV: 1.5384e-3` — a direct gap floor, not an inferred
   `T_c`.
2. Re-audit the five active walls against physically credible material ranges.
3. Widen only justified directions:
   * higher `topfilm_vsound` **if** real candidate superconductors support it;
   * lower `sub_scat` down to a documented physical floor;
   * **do not** drive `sub_c44` toward zero just because the optimizer did;
   * **do not** allow negative `sub_c12` merely because the wall was reached;
   * **do not** lower `bot_ph_lifetime` without a measured or bracketed basis.
4. Perturb inward and outward around those five walls under the adopted site
   design.
5. Use the anchor as incumbent, not as the centre of every bound.
