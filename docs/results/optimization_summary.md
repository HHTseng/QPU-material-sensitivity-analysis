# Optimization comparison

| Method | Confirmed QPs/injected-eV | Additional reduction from initial best | Additional reduction from Sobol |
|---|---:|---:|---:|
| random | 0.000570905 | 0% | 0% |
| sobol | 0.000570905 | 0% | 0% |
| cmaes | 0.000425301 | 25.5% | 25.5% |
| bo | 0.000410339 | 28.1% | 28.1% |
| bo_improved | 0.000402924 | 29.4% | 29.4% |
| llm_proposals | 0.000447069 | 21.7% | 21.7% |
| llm_tools | 0.000438348 | 23.2% | 23.2% |
| bo_robust | 0.000407383 | 28.6% | 28.6% |
| corners | 0.000404828 | 29.1% | 29.1% |

Each gain uses independent confirmation of that seed’s leader and common initial winner. Shared positions and streams are retained across targets.

- Three independent search seeds give exploratory evidence; position layouts are not extra optimizer repetitions.
- Source designs remain at most 1024 sites; this cap does not prove spatial convergence.
- Confirmed properties are model targets, not identified new materials. Calibrated interfaces require independent measurements.
- Incomplete search arms and failed attempts remain in the comparison; absent responses are never zero.
- Across-seed intervals condition on the four measured layouts; paired layout intervals separately describe position uncertainty. Neither supplies an exact combined superiority test.

[Numerical results, per-seed values and verified archives](optimization_summary.json).
