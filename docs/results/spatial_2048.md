# Limited 2048-site verification

Six primary spatial contrasts use paired layout ratios with Bonferroni family error 0.05. The nested 1024 subset and full 2048 use the same per-position primaries.

| Candidate | Spatial response change | Simultaneous interval | Equivalent within 5% |
|---|---:|---:|---|
| Si-45deg | -0.54% | [-5.46%, +4.38%] | False |
| Si-0deg | -0.37% | [-9.31%, +8.57%] | False |
| Ge-45deg | -1.03% | [-7.37%, +5.31%] | False |
| Ge-0deg | -0.82% | [-4.69%, +3.05%] | True |
| GaAs-45deg | -0.37% | [-5.25%, +4.50%] | False |
| GaAs-0deg | -0.90% | [-9.49%, +7.69%] | False |

Completed at 06:09 EDT on 2026-10-30: four studies, 196,608 new tasks and 3.072 billion primaries. Simulation and analysis totaled 2.75 hours; elapsed time including preservation was approximately 3 hours. All four summary checksums and both copies of every archive were reverified on October 30.

All six spatial intervals include zero. Only Ge at 0 degrees establishes agreement within 5% simultaneously; an unresolved result does not establish disagreement. The observed changes, -1.03% to -0.37%, do not justify extending every study to 2,048 sites.

Let the reference be the retained 1,024 positions at 31,250 primaries per task. The following paired layout-ratio changes use descriptive individual 95% intervals. Fixed-total-primary compares all 2,048 new positions at 15,625 primaries; half-primary-count uses only the original 1,024 positions at 15,625.

| Candidate | Fixed-total-primary change [95% interval] | Half-primary-count change [95% interval] |
|---|---:|---:|
| Si-45deg | -0.50% [-3.54%, +2.53%] | +0.04% [-0.89%, +0.96%] |
| Si-0deg | +0.29% [-5.15%, +5.73%] | +0.65% [-0.58%, +1.88%] |
| Ge-45deg | -1.50% [-3.25%, +0.25%] | -0.46% [-2.11%, +1.20%] |
| Ge-0deg | -0.73% [-2.36%, +0.89%] | +0.09% [-1.08%, +1.27%] |
| GaAs-45deg | -0.24% [-2.55%, +2.07%] | +0.15% [-1.75%, +2.04%] |
| GaAs-0deg | -1.52% [-4.38%, +1.35%] | -0.60% [-2.33%, +1.12%] |

- This verification covers six fixed native 0/45-degree cases, not every orientation or optimized leader.
- Four independent layouts give broad Student-t intervals, especially for rare concentrated responses. Failure to establish equivalence does not establish a physical change.
- The pure spatial contrast uses the new nested subset at the same per-position primary count. Comparing against the older 1024 cohort also changes per-position primaries.
- Mean layout ratios differ from a ratio of pooled means. Area-weight uncertainty and model assumptions are not inferred from these checks.
- All original measurements remain unchanged. Two verified archives retain each new layout before raw working data are cleared.

[Measurements, reference data and archives](spatial_2048.json).
