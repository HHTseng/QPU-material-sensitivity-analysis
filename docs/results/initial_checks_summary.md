# Initial source and primary-count checks

Maximum source sites: 1024. State: awaiting review. All 10 measured studies and two-copy archive checks finished.

Finished 2026-10-15 at 10:38:38 EDT, after 11 h 43 min: 160,000 complete tasks and 13.832 billion primary phonons. No simulations remain queued. All twenty study-archive copies were checked against their recorded SHA256 values.

Spatial criteria passed: False. Final primary-count criteria passed: True.

| Independent-layout mean | QPs / injected eV | Standard error | Relative standard error |
|---|---:|---:|---:|
| reference | 0.00234472 | 8.06e-06 | 0.344% |
| c44-1gpa | 0.00223919 | 8.4e-06 | 0.375% |
| SiC-derived-rates | 0.00154571 | 6.34e-06 | 0.41% |

Four position-layout means give the reported Student-t interval and standard error. Fixed-layout event-count uncertainty is stored separately. The nested total changes are 0.71–2.75%, but their 95% intervals exceed the ±5% equivalence margin; near-junction regional changes remain larger. Increasing primaries reduces transport noise while source-position uncertainty remains.

These are predefined test cases, not optimizer gains. The [optimization assessment](../../material_scan/docs/optimization.md) separates these results from historical search improvements and proposes further work within 1,024 sites.

- The site limit is a chosen stopping point, not evidence of spatial convergence.
- Nested contrasts and four independent layouts assess the tested source designs; area weights remain fixed.
- Event-count precision is conditional on fixed source positions.
- SiC-derived rates are model assumptions; no material ranking or optimizer result is produced.

[Numerical results, regional checks and archive verification](../../verification/initial_1024_results.json).
