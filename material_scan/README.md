# Material-property search

This is the current interface for material-property studies. It separates the
physical inputs, recorded injection sites, simulation, quasiparticle
calculation, optimizer, and reports so each part can be checked independently.

## How it works

1. `parameters.yaml` defines every parameter, unit, physical range, and
   destination in the simulator or interface calculation.
2. One YAML file in `experiments/` fixes the injection sites, replicas, random
   seeds, primary-event count, objective, material values, and search ranges.
3. `check` rejects missing, unknown, inconsistent, or unphysical values.
4. `freeze` resolves every site and seed into one read-only JSON description.
   Restarting uses those recorded tasks; it does not draw new sites.
5. `runner.py` starts one simulation task in a temporary directory. A task is
   accepted only when the hit file is valid and the macro wrote its expected
   completion witness. Publication is atomic.
6. `physics.py` converts energy deposited at the sensor surface into
   quasiparticles. `analysis.py` requires the full declared site-by-replica set
   and calculates the area-weighted objective and its uncertainty.
7. `search.py` gives random, Sobol, Gaussian-process Bayesian, and CMA-ES
   methods the same recorded `ask` and `tell` calls.
8. `store.py` records experiment, simulation, task, attempt, and observation
   states in SQLite. Only the controller writes to it.

The final material and macro rendering path is still being compared with the
earlier implementation. For that reason, `python -m material_scan run` refuses
to start Geant4. The temporary compatibility programs in
`parameter_optimization/` remain available for controlled comparison studies;
they are not the starting point for a new production search.

## Commands

Run from the repository root in the G4CMP Conda environment:

```bash
conda run -n G4CMP python -m material_scan check \
  material_scan/experiments/spatial-strata-512-baseline.yaml

conda run -n G4CMP python -m material_scan freeze EXPERIMENT.yaml \
  --values VALUES.yaml --output resolved.json

conda run -n G4CMP python -m unittest discover -s material_scan/tests -v
```

The maintenance commands are `inventory`, `archive`, `verify-archive`,
`recover`, `history-summary`, and `store-doctor`. Archive creation copies data;
it never deletes the source.

## Source map

| File | One responsibility |
|---|---|
| `config.py` | validate parameter and experiment files |
| `sampling.py` | load the recorded stratified spatial design |
| `identity.py` | hash physical inputs, tasks, analysis, and search separately |
| `runner.py` | execute and publish one Geant4 task |
| `physics.py` | hit-to-quasiparticle and density calculations |
| `analysis.py` | complete-set checks, weighted objective, uncertainty, paired differences |
| `search.py` | recorded optimizer interaction |
| `store.py` | single-writer SQLite state |
| `archive.py` | checksummed data packaging and recovery |
| `historical.py` | read-only inspection of earlier experiment files |
| `cli.py` | the only command-line entry point |
| `tools/plot_hits.py` | Matplotlib hit-file checks from read-only result data |
| `plot_orientation.py` | fixed integer-direction versus sphere-point comparison |

## Documentation

- [science.md](docs/science.md): equations, assumptions, crystal direction,
  constraints, and objective.
- [results.md](docs/results.md): experiment history, current conclusions,
  unresolved questions, and recommended next work.
- [data.md](docs/data.md): directory names, preservation, verification, and
  recovery.

Earlier long notes were merged into these files. Superseded instructions remain
recoverable from Git history rather than competing with the current procedure.

## Checked equivalence

A retained 32-file simulation reproduces exactly 113 hit rows, 672
quasiparticles, all 17 electrode totals, the complete time matrix, and
`1.68e-4` quasiparticles per primary. The corrected stratified reference values
also reproduce:

| Sites | Baseline | SiC | Paired difference | Relative change |
|---:|---:|---:|---:|---:|
| 128 | 2.42916e-3 | 1.08713e-3 | -1.34202e-3 | -55.25% |
| 512 | 2.41552e-3 | 1.25609e-3 | -1.15943e-3 | -48.00% |

These are software-equivalence checks, not evidence that 128 spatial sites are
enough. The 512-site experiment finished on 2026-09-09 at 08:05. Its two
unfinished records were removed by the user.
