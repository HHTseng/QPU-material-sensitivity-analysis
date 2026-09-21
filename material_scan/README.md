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
5. `simulation.py` derives the material, renders the lattice and macros, checks
   all recorded executable and input hashes, and controls the complete task set.
   `runner.py` starts one simulation task. A task is
   accepted only when the hit file is valid and the macro wrote its expected
   completion witness. Publication is atomic.
6. `physics.py` converts energy deposited at the sensor surface into
   quasiparticles. `analysis.py` requires the full declared site-by-replica set
   and calculates the area-weighted objective and its uncertainty.
7. `search.py` gives random, Sobol, Gaussian-process Bayesian, CMA-ES, and the
   guarded Ollama/GP agentic method the same recorded `ask` and `tell` calls.
   The agent proposes and explains a pool; hard code validates it, the GP ranks
   it, and only Geant4 supplies objective values.
8. `store.py` records experiment, simulation, task, attempt, and observation
   states in SQLite. Only the controller writes to it. A running controller
   renews its ownership every minute; recovery requires the exact owner, token,
   and an inactive heartbeat.

The current renderer matches the retained 512-site lattice byte for byte and
matches every active macro command numerically. A disposable live task also
passed executable, material-name, density, hit-file, completion, scoring, and
SQLite checks. `python -m material_scan run` therefore accepts only a frozen
experiment with `build.mode: verified`; historical definitions remain
read-only and cannot launch. Programs in `legacy/` are comparison evidence,
not the execution path for new work.

## Current result

The 2,361-site spatial calculation passes every declared convergence check.
Three CMA-ES seeds then completed 120 proposed points each. Their leading
points remained 38.7% to 48.3% below the compatible anchor when repeated with
the complete spatial design and unused replicas. Seeds 101 and 303 are tied;
their full-spatial values are `3.118155e-4 ± 1.32e-5` and
`3.068781e-4 ± 1.80e-5`.

The extra CMA-ES generations helped: seed 202 found its best point at step 108.
The expected-improvement Gaussian-process method did not complete its first
new point because that boundary proposal was extremely slow, so no claim about
additional GP steps is possible yet. Every confirmed CMA-ES finalist also
touches the lower `sub_c44` limit; these are search directions, not named
materials or a fabricable optimum. See [results.md](docs/results.md) for the
full comparison and recommended next experiment.

## Commands

Run from the repository root in the G4CMP Conda environment:

```bash
conda run -n G4CMP python -m material_scan check \
  material_scan/experiments/spatial-strata-512-baseline.yaml

conda run -n G4CMP python -m material_scan freeze EXPERIMENT.yaml \
  --values VALUES.yaml --output resolved.json

conda run -n G4CMP python -m material_scan run resolved.json \
  --output material_scan_data/experiments/EXPERIMENT/CANDIDATE --workers 10

# Prepare and evaluate the 32 common material-search starting points.
conda run -n G4CMP python -m material_scan select-starting-points \
  material_scan/experiments/material-search.yaml \
  --historical-database data/stage4_trials.sqlite \
  --reference material_scan_data/experiments/spatial-refinement/baseline/resolved.json \
  --anchor material_scan_data/experiments/spatial-refinement/nb-anchor/resolved.json \
  --output material_scan/experiments/material-search-starting-points.json

conda run -n G4CMP python -m material_scan evaluate-starting-points \
  material_scan/experiments/material-search.yaml \
  --points material_scan/experiments/material-search-starting-points.json \
  --output material_scan_data/experiments/material-search/common \
  --parallel-points 6 --workers-per-point 16

# Run or resume one CMA-ES seed: 120 points are ten complete generations.
conda run -n G4CMP python -m material_scan search \
  material_scan/experiments/material-search.yaml \
  --initial-results material_scan_data/experiments/material-search/common/summary.json \
  --method cmaes --seed 101 --steps 120 --workers 20 \
  --output material_scan_data/experiments/material-search/cma-101

conda run -n G4CMP python -m unittest discover -s material_scan/tests -v
```

The optional local-language-model method has not run. Its exact model and GPU
checks are kept in the
[separate plan](../AGENTIC_MATERIAL_OPTIMIZATION_PLAN.md) so they do not obscure
the normal simulation path.

The maintenance commands are `inventory`, `archive`, `verify-archive`,
`recover`, `history-summary`, `store-doctor`, and `recover-controller`. The last
command is only for an exact controller token with an inactive heartbeat.
Archive creation copies data; it never deletes the source.

## Source map

| File | One responsibility |
|---|---|
| `config.py` | validate parameter and experiment files |
| `sampling.py` | load the recorded stratified spatial design |
| `design.py` | extend selected spatial regions while preserving every old site and seed |
| `space.py` | construct the exact experiment-local numerical search space and physical checks |
| `identity.py` | hash physical inputs, tasks, analysis, and search separately |
| `runner.py` | execute and publish one Geant4 task |
| `simulation.py` | render, control, resume, score, and record one resolved experiment |
| `optimization.py` | select common starting points and run restartable optimizer comparisons |
| `agentic.py` | guarded Ollama proposals, GP ranking, saved decisions, and network-free replay |
| `agent_knowledge.md` | compact, source-linked prompt facts and explicit unknowns |
| `physics.py` | hit-to-quasiparticle and density calculations |
| `analysis.py` | complete-set checks, weighted objective, uncertainty, paired differences |
| `search.py` | recorded optimizer interaction |
| `store.py` | single-writer SQLite state |
| `archive.py` | checksummed data packaging and recovery |
| `historical.py` | read-only inspection of earlier experiment files |
| `cli.py` | the only command-line entry point |
| `tools/plot_hits.py` | Matplotlib hit-file checks from read-only result data |
| `tools/refinement_report.py` | apply the predeclared nested spatial checks |
| `tools/resolution_report.py` | compare explicit site and replica counts |
| `tools/search_report.py` | summarize optimizer seeds and draw best-so-far curves |
| `tools/confirmation_report.py` | compare full-spatial confirmations with screening values |
| `plot_orientation.py` | fixed integer-direction versus sphere-point comparison |

## Documentation

- [science.md](docs/science.md): equations, assumptions, crystal direction,
  constraints, and objective.
- [results.md](docs/results.md): experiment history, current conclusions,
  unresolved questions, and recommended next work.
- [data.md](docs/data.md): directory names, preservation, verification, and
  recovery.
- [agentic optimization plan](../AGENTIC_MATERIAL_OPTIMIZATION_PLAN.md): model
  choice, earlier optimizer failures, deployment, and fair comparison protocol.

Earlier long notes were merged into these files. Superseded instructions remain
recoverable from Git history rather than competing with the current procedure.

Checked search records are kept in
[`material-search-results.json`](experiments/material-search-results.json) and
[`material-search-confirmation-results.json`](experiments/material-search-confirmation-results.json).
The [search curve](docs/figures/material-search-best.png) is generated by
`tools/search_report.py`.

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
