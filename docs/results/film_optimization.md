# Native film design and optimization

Only Nb and Cu thickness vary. Cu coverage changes at constant Cu volume. The native substrate and film property records remain coupled. All four thickness corners were included in every common starting table.

| Substrate | Comparison | Mean response change | Simultaneous interval | At least 5% reduction established |
|---|---|---:|---:|---|
| Si | bo_vs_initial | +0.06% | [-2.05%, +2.17%] | No |
| Si | sobol_vs_initial | +0.15% | [-1.00%, +1.30%] | No |
| Si | bo_vs_sobol | -0.08% | [-1.52%, +1.35%] | No |
| GaAs | bo_vs_initial | +0.56% | [-1.86%, +2.99%] | No |
| GaAs | sobol_vs_initial | +0.00% | [+0.00%, +0.00%] | No |
| GaAs | bo_vs_sobol | +0.56% | [-1.86%, +2.99%] | No |

For search seed s and confirmation layout l, the compared quantity is d[s,l]=J_A[s,l]/J_B[s,l]-1. The standard error is the square root of the sum of seed-mean and layout-mean variance estimates. The Student-t degrees of freedom are the smaller of the two counts minus one; six comparisons share family error 0.05. This is a conservative approximation and counts seed-layout interaction twice. Incomplete search arms cannot establish the full primary conclusion.

Physical design gains relative to the original Nb/Cu geometry are reported separately from optimizer gains beyond the shared starting winner. The numerical record retains unsuccessful comparisons, measured counts, timing, sampling contrasts, exact geometry and archive identities.

- These are model predictions for named substrates with fixed Nb/Cu property records, not discovery of a new compound or measured qubit-error reduction.
- Nb thickness 75 to 100 nm is a chosen design interval anchored by the 75 nm reference and demonstrated 100 nm films. A 200 nm timing diagnostic was about 13 times slower and is retained outside the optimization. Fixed Nb gap and lifetime across this interval remain model assumptions.
- Cu thickness 1 to 10 um at 250 um pitch keeps 0.064 mm3 Cu per 10 x 10 mm2 chip. Greater thickness reduces coverage; this family is not a measured device set.
- All four corners are in each shared initial table. Monotonic response can make a corner optimal, so no additional BO gain is promised.
- Seed and layout variance are estimated separately. Their sum is a conservative approximation counting seed-layout interaction twice, rather than an exact combined test.
- Eight unused confirmation layouts assess the selected points, not uniform convergence over every physical design.

[Numerical results and archives](film_optimization.json).
