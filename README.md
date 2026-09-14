# Phonon and quasiparticle material studies

This repository uses Geant4 and G4CMP to study how substrate and film
properties change phonon transport and quasiparticle production in a
superconducting-qubit device. It contains two related analyses:

- `material_scan/` is the current material-property search. Start with its
  [README](material_scan/README.md).
- `stage1_run_simulations.py` and `stage2_compute_QPs.py` implement the earlier
  Morris sensitivity analysis. Its methods and run instructions are in
  [docs/sensitivity-analysis.md](docs/sensitivity-analysis.md).

The numbered files under `parameter_optimization/` are retained only while the
new interface is checked against earlier results. Do not begin a new study
there. Current scientific conclusions, limitations, and next experiments are
in [material_scan/docs/results.md](material_scan/docs/results.md).

## Current documentation

| File | Purpose |
|---|---|
| [material_scan/README.md](material_scan/README.md) | commands, source layout, and present implementation status |
| [material_scan/docs/science.md](material_scan/docs/science.md) | physical model, objective, crystal direction, and validity checks |
| [material_scan/docs/results.md](material_scan/docs/results.md) | completed experiments, current conclusions, and next work |
| [material_scan/docs/data.md](material_scan/docs/data.md) | result layout, preservation, and recovery |
| [docs/sensitivity-analysis.md](docs/sensitivity-analysis.md) | earlier Morris sensitivity workflow |

All event counts are written as numbers. A result name describes its purpose
and spatial design; it does not encode an informal size class.

## Environment

Run Python commands in the existing G4CMP Conda environment:

```bash
conda run -n G4CMP python -m unittest discover -s material_scan/tests -v
conda run -n G4CMP python -m material_scan check \
  material_scan/experiments/spatial-strata-512-baseline.yaml
```

The Geant4 executable, G4CMP source and data files are external installations.
Their identities must be recorded with every new simulation.
