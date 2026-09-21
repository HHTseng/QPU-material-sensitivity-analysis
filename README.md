# Phonon and quasiparticle material studies

This repository uses Geant4 and G4CMP to study how substrate and film
properties change phonon transport and quasiparticle production in a
superconducting-qubit device.

## Layout

| Directory | Contents |
|---|---|
| `material_scan/` | the current material-property search — start at its [README](material_scan/README.md) |
| `legacy/` | the retained Stage 1–4 runtime and the earlier Morris analysis |
| `data/` | **experimental evidence only** — SQLite records, raw runs, results |

Three directories, each with one job. [`data/`](data/README.md) holds no code.
Its SQLite records store artifact paths **relative to the repository root**, so a
checkout is portable and the directory is no longer pinned by its own name.

`legacy/` is one flat directory on purpose. Its modules import each other by
bare module name (`from stage4_space import ...`, `import stage1_run_simulations`),
so they must sit together on one `sys.path` entry. Splitting them would mean
rewriting imports across about thirty files for no scientific gain.

## Current documentation

| File | Purpose |
|---|---|
| [material_scan/README.md](material_scan/README.md) | commands, source layout, implementation status |
| [material_scan/docs/science.md](material_scan/docs/science.md) | physical model, objective, crystal direction, validity checks |
| [material_scan/docs/results.md](material_scan/docs/results.md) | completed experiments, conclusions, next work |
| [material_scan/docs/data.md](material_scan/docs/data.md) | result layout, preservation, recovery |
| [material_scan/docs/issues.yaml](material_scan/docs/issues.yaml) | open preservation and result-reuse conditions |
| [data/README.md](data/README.md) | evidence layout, relative paths, restoring a backup |
| [legacy/sensitivity-analysis.md](legacy/sensitivity-analysis.md) | earlier Morris sensitivity workflow |
| [RESTRUCTURING.md](RESTRUCTURING.md) | what moved on 2026-09-14/15 and why |
| [AGENTIC_MATERIAL_OPTIMIZATION_PLAN.md](AGENTIC_MATERIAL_OPTIMIZATION_PLAN.md) | guarded Ollama/GP material-search plan and comparison rules |

Do not begin a new study under `legacy/`. Current scientific conclusions,
limitations, and next experiments are in
[material_scan/docs/results.md](material_scan/docs/results.md).

All event counts are written as numbers. A result name describes its purpose
and spatial design; it does not encode an informal size class.

## Current result

The device-weighted spatial calculation is converged at 2,361 sites and eight
replicas. Three 120-point CMA-ES runs produced full-spatial results 38.7% to
48.3% below the niobium-gap-compatible starting point. Seeds 101 and 303 are a
statistically tied leading region; neither is a unique optimum, and both touch
search limits. Ten generations were useful because seed 202 found its best
point at step 108.

The widened-range expected-improvement Gaussian-process run produced no new
measurement: its first proposed point completed none of 512 tasks in one hour.
More steps should be tried only after defining a physically and computationally
feasible local region. A 34.9% shift of the compatible reference point between
independent random-number sets also requires a focused repeat before selecting
a final property vector. Exact values and the next experiment are in the
[results](material_scan/docs/results.md#gap-compatible-material-search-with-the-device-weighted-objective).

## Environment

Run Python commands in the existing G4CMP Conda environment:

```bash
# current package
conda run -n G4CMP python -m unittest discover -s material_scan/tests -v
conda run -n G4CMP python -m material_scan check \
  material_scan/experiments/spatial-strata-512-baseline.yaml

# retained legacy suite (194 checks)
cd legacy && conda run -n G4CMP python tests_stage4.py
```

The Geant4 executable, G4CMP source and data files are external installations.
Their identities must be recorded with every new simulation.
