# Experiment index

This table gives human names to the retained studies. The legacy campaign and
trial IDs remain authoritative inside their original files.

| New family | Legacy names | Status | Present interpretation |
|---|---|---|---|
| `material-factorial` | `stage3_factorial_v1` | complete | Discrete substrate/top-film/bottom-film comparison. |
| `property-search-original` | `stage4_property_v1_{rand,sobol,bo_gp,cmaes}` | complete, historically qualified | Early optimizer labels and material projection include recorded invalidations; use the corrected reports. |
| `optimizer-benchmark` | `stage4_property_v1_bench_*` | complete | Equal-budget rerun establishes method performance only for the tested box and seeds. |
| `projection-corrected` | `stage4_property_v1_smokeP0`, corrected S/M/L/XL confirmations | complete where recorded | Carrier-aware material checks; invalid pre-fix rows remain retained but excluded. |
| `spatial-uniform` | `stage4_property_v1_sites16/32/64` | complete, non-converged | Demonstrated strong site leverage and motivated stratification. |
| `spatial-strata` | `stage4_property_v1_strat128/256/512` | complete | Contains both old `H*` and corrected `S*` design generations; never merge them by campaign name. |

`spatial-strata-512` finished on 2026-09-09 at 08:05. Its two unfinished
ledger records were deleted. The saved result JSON still has an incomplete
`bo_gp_corner` report placeholder with no trial ID; this is a closed incomplete
entry, not a live task and not a measurement.

No new production campaign is launched by this restructuring branch. Saved
artifacts are the first parity oracle. A new low-event smoke becomes appropriate
only after resolution, rendering, scorer, design, objective, and store gates
all pass.
