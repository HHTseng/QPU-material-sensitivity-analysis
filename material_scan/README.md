# Material scan

`material_scan` is the revised interface for the material-parameter studies.
It validates experiments, freezes their full task plan, reproduces the legacy
QP analysis, and provides safe run-state and archive primitives. The original
`parameter_optimization/` tree remains the scientific reference during the
transition.

## Data flow

1. `parameters.yaml` owns parameter types, units, physical ranges, and simulator
   targets. A file in `experiments/` owns one study's values or search bounds,
   recorded design, fidelity, objective, and build identity.
2. `check` validates the experiment. `freeze` resolves candidate values and
   writes an immutable manifest containing every site, replica, seed, and event
   count. Resume reads this file; it never redraws the experiment.
3. Task, simulation, analysis, and search identities are separate. A report
   change cannot invalidate an identical simulation, while a physics input,
   site, seed, executable, or artifact change cannot collide silently.
4. A task succeeds only with a valid hit file and a post-`beamOn` witness. The
   runner publishes it atomically. The one-writer SQLite store uses a controller
   lease and attempt token so a late worker cannot overwrite a retry.
5. The scorer produces per-electrode QPs. Analysis requires the exact declared
   site-by-replica set and computes the area-weighted objective, uncertainty,
   spatial `R0.95`, and paired differences. Partial or malformed data fail.
6. Random, Sobol, BO-GP, and CMA-ES use one recorded `ask`/`tell` interface.
   This compatibility layer still calls the tested legacy optimizer math.

Material realization and macro/lattice rendering have not yet passed parity.
Therefore `python -m material_scan run ...` refuses to launch Geant4. This
branch has not produced or silently reused a new physics result.

## Use

From the repository root in the G4CMP Python environment:

```bash
python -m material_scan check material_scan/experiments/spatial-strata-512-baseline.yaml
python -m material_scan freeze EXPERIMENT.yaml --values VALUES.yaml --output resolved.json
python -m material_scan legacy-summary parameter_optimization/stage4_trials.sqlite
python -m unittest discover -s material_scan/tests -v
```

`freeze` accepts candidate values only through one JSON/YAML mapping; it has no
scientific scalar overrides. `legacy-summary` is read-only. See `--help` for
the explicit `inventory`, `archive`, `verify-archive`, `recover`, and
`store-doctor` maintenance commands. Archive creation never deletes its source.

## Legacy comparison

A checksummed 32-file legacy trial reproduces exactly 113 hits, 672 QPs, all
17 electrode totals, the full time matrix, and `1.68e-4` QPs per primary. The
corrected stratified anchors also reproduce (objective units: area-weighted
junction QPs per injected eV):

| Sites | Baseline | SiC | Paired difference | Relative change |
|---:|---:|---:|---:|---:|
| 128 | 2.42916e-3 | 1.08713e-3 | -1.34202e-3 | -55.25% |
| 512 | 2.41552e-3 | 1.25609e-3 | -1.15943e-3 | -48.00% |

The recorded standard errors and spatial `R0.95` values match as well. These
are parity anchors, not a claim that 128 sites were converged. On 2026-09-14,
62/62 revised tests, 190/190 legacy gates, and 15/15 Stage-4 audit checks pass;
the legacy ledger checksum is unchanged.

`spatial-strata-512` finished on 2026-09-09 at 08:05. Its two unfinished ledger
records were deleted by the user. Surviving orphan directories are evidence,
not cacheable results.

Before enabling `run`: port and compare material realization; require normalized
macro/lattice parity; verify the complete executable, libraries, data, and
analysis environment; then run one matched low-event baseline smoke.

See the [restructuring blueprint](../parameter_optimization/REPOSITORY_RESTRUCTURING_BLUEPRINT.md),
[experiment map](docs/experiments.md), and [data rules](docs/data.md).
