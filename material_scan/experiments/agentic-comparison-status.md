# Material-search result

All methods began with the same 32 newly evaluated points. Each later candidate used 16,000,000 source phonons. The values here are for comparative search; leading points require the full spatial design and unused random seeds before a material conclusion. An incomplete run contributes only its completed points and is labelled by its completion count.

The best common starting value was `5.062199e-04`.

Runtime policies differ across retained records (per-task timeouts: 0, 600 seconds). Objective values and completed-evaluation curves are comparable; wall-time and failure efficiency are not equal-policy evidence.

| Run | Method | Completed | Best | Source | Improvement | Best step | Failed proposals |
|---|---|---:|---:|---|---:|---:|---:|
| agentic-101 | agentic | 120/120 | 3.654020e-04 ± 5.36e-05 | optimizer | 27.8% | 118 | 1 |
<!-- agentic-101: agent-selected=120, GP-fallback=0 -->
| agentic-202 | agentic | 120/120 | 4.290081e-04 ± 6.26e-05 | optimizer | 15.3% | 107 | 0 |
<!-- agentic-202: agent-selected=120, GP-fallback=0 -->
| agentic-303 | agentic | 120/120 | 3.870108e-04 ± 6.00e-05 | optimizer | 23.5% | 108 | 2 |
<!-- agentic-303: agent-selected=120, GP-fallback=0 -->
| cma-101 | cmaes | 120/120 | 2.595938e-04 ± 5.57e-05 | optimizer | 48.7% | 87 | 0 |
| cma-202 | cmaes | 120/120 | 2.537300e-04 ± 5.26e-05 | optimizer | 49.9% | 108 | 0 |
| cma-303 | cmaes | 120/120 | 2.321996e-04 ± 4.30e-05 | optimizer | 54.1% | 72 | 0 |
| random-101 | random | 64/64 | 5.062199e-04 ± 8.74e-05 | common-start | 0.0% | 0 | 0 |
| sobol-101 | sobol | 49/64 | 5.062199e-04 ± 8.74e-05 | common-start | 0.0% | 0 | 0 |

Recorded incomplete/stalled attempts (these are runtime observations, not objective measurements):

| Attempt | Method | Search completions | Stalled point | Task progress | Observed elapsed | Candidate lower bound |
|---|---|---:|---:|---:|---:|---:|
| bo-101 | bo_gp (stalled) | 0/120 | 1 | 0/512 (60 concurrent) | ≥1.00 h | ≥8.53 h |
| sobol-101 | sobol (stalled) | 49/64 | 50 | 0/512 (20 concurrent) | ≥1.00 h | ≥25.60 h |

Descriptive method summary over recorded runs (candidate budgets may differ):

- agentic: 3/3 requested runs complete; median best `3.870108e-04`; range `3.654020e-04` to `4.290081e-04`; median improvement 23.5%; optimizer-selected-only median `3.870108e-04`.
- cmaes: 3/3 requested runs complete; median best `2.537300e-04`; range `2.321996e-04` to `2.595938e-04`; median improvement 49.9%; optimizer-selected-only median `2.537300e-04`.
- random: 1/1 requested runs complete; median best `5.062199e-04`; range `5.062199e-04` to `5.062199e-04`; median improvement 0.0%; optimizer-selected-only median `7.252207e-04`.
- sobol: 0/1 requested runs complete; median best `5.062199e-04`; range `5.062199e-04` to `5.062199e-04`; median improvement 0.0%; optimizer-selected-only median `7.566498e-04`.

Branch names identify source history, not a numerical comparison axis. Curves are included only when the experiment identity and common starting-point identity match; older branches with different objectives or spatial designs are intentionally excluded.

The agent prompt deliberately carries forward lessons from the recorded BO/Sobol stalls and aggregate CMA-ES behavior. This is a retrospective workflow comparison, not a blinded optimizer benchmark, even when source-phonon counts match.

**Agentic conclusion: full screening campaign complete.** Across 3 equal-budget runs, the median best value was `3.870108e-04`, a 23.5% reduction from the common incumbent. The agentic median remained 52.5% higher than the CMA-ES median, so this benchmark does not show agentic superiority.

![Best-so-far search curves](../docs/figures/agentic-comparison-status.png)
