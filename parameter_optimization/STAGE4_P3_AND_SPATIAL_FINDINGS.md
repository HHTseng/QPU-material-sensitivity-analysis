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

| | unconstrained corner | **best Nb-compatible** |
|---|---:|---:|
| *J* | 5.30e-5 | **6.55e-5** |
| ground-plane `T_c` | 0.33 K | **10.99 K** (Nb is 10.1) |
| 2Δ_film / 2Δ_Al | 0.26 | **8.75** |
| phonon sink? | yes | **no** |
| log₁₀(QP density / Nb) | **+374** | **−33** |
| variables at a box wall | 13/16 | 5/16 |

The constrained winner is not merely acceptable — its ground plane is *better
than Nb* on both `T_c` and equilibrium QP density, and it sits far away from the
box corner (5 walls, not 13), so it is a more credible interior point.

Its mechanism is also different: `sub_c44` goes to its **lower** bound where the
unconstrained corner drove it to the upper. Without a phonon sink next to the
qubits, the optimizer solves the problem a different way.

**Recommendation for the P3 decision:** impose **ground-plane `T_c` ≥ 9 K**
(Nb-like). It is the strictest of the options, costs the same 1.24× as the
loosest, keeps 69 already-simulated points as a warm start, and removes the
entire class of designs whose QP density is hundreds of orders of magnitude
worse than the film they replace. There is no evidence of a trade-off worth
preserving between 0.33 K and 9 K.

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

1. **P3 is decided cheaply**: `T_c` ≥ 9 K, at a measured cost of 1.24×.
2. **Do not quote a reduction magnitude** until the site sweep lands.
3. The constrained widened box should be built around the **Nb-compatible**
   point, not the unconstrained corner — it is an interior point, its ground
   plane beats Nb, and 69 already-simulated candidates satisfy the constraint
   and can seed the search.
