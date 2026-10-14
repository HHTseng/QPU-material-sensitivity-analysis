# Material-property optimization for phonon-mediated quasiparticles

This branch implements the project in **eight Python scripts and two test
scripts**. One command runs sensitivity, linked-material comparisons,
continuous-property search, confirmation, spatial refinement, and tolerance
studies. The external Geant4/G4CMP installation supplies phonon transport.

For material properties $x\in\mathcal X$, injection position $\xi$, transport
realization $\omega$, electrode $e$, and source energy $E_{\rm gun}$, define
$Q_e(x,\xi,\omega)$ as junction quasiparticles per source phonon. The objective is

$$
J(x)=\mathbb E_{\xi,\omega}\left[
\frac{\sum_e w_e Q_e(x,\xi,\omega)}{E_{\rm gun}}\right],
\qquad w_e\geq0,\quad\sum_e w_e=1.
$$

Uniform device-area injection uses recorded spatial-region weights. Earlier
16-position results use the separately named summed QPs per primary. QP yield
does not establish logical-error probability or a fabricable material optimum.

## Run

Use the existing G4CMP environment from this repository directory. Installation
paths can be overridden in the study's `installation` section. An editable
installation is optional: `python -m pip install --no-deps -e .` exposes
`material-scan`; the commands below work without it.

```bash
conda run -n G4CMP python scan.py check studies/reference-eight.yaml
conda run -n G4CMP python scan.py run studies/reference-eight.yaml \
  --output material_scan_data/experiments/reference-eight
conda run -n G4CMP python scan.py analyse \
  material_scan_data/experiments/reference-eight --output /tmp/reference-analysis.json
conda run -n G4CMP python scan.py plot \
  material_scan_data/experiments/reference-eight --kind summary --output /tmp/reference.png
conda run -n G4CMP python -m unittest test_physics test_workflow -v
```

`run --prepare-only` records resolved inputs without simulation. Restarting the
same output directory resumes recorded work. A new physical study needs a new
directory. Raw outputs are checked before reuse; changed scoring can be
recomputed from retained hits. Failed or incomplete measurements have no yield.

The examples in [`studies/`](studies/) specify exact primary counts, sites,
realizations, seeds, objectives, and physical limits. The small reference
examples check software agreement; their 16 sites do not establish a converged
device-area integral. The search example demonstrates a declared parameter
range; its values do not constitute a fabrication recommendation. Sensitivity
requires SALib; Materials Project access, TPE, and presentation export are optional.

Other commands are `compare`, `catalog`, `project`, `probe-density`, `history`,
`inventory`, `archive`, `verify-archive`, `recover`, and `convert-copy`.
Use `python scan.py COMMAND --help` for arguments. Archive creation copies data;
it never deletes the source. Keep two independently verified copies before any
future removal of historical raw data.

## Code and evidence

| File | Purpose |
|---|---|
| [`scan.py`](scan.py) | Commands, one evaluation loop, scientific study summaries |
| [`inputs.py`](inputs.py) | Physical inputs, material records, sites, seeds, study tables |
| [`physics.py`](physics.py) | Elasticity, interfaces, QP production and density evolution |
| [`simulate.py`](simulate.py) | Input rendering, subprocesses, completion checks |
| [`analysis.py`](analysis.py) | Area weighting, uncertainty, comparisons, sensitivity, convergence |
| [`search.py`](search.py) | Random, Sobol, Gaussian-process and CMA-ES search; optional TPE/Ollama |
| [`records.py`](records.py) | One SQLite writer, raw-file reading, preservation and recovery |
| [`plots.py`](plots.py) | Figures and optional presentation export from recorded results |
| [`test_physics.py`](test_physics.py), [`test_workflow.py`](test_workflow.py) | Numerical and workflow verification |

[`WORKFLOW_VERIFICATION.md`](WORKFLOW_VERIFICATION.md) reports comparisons with
`Material_optimization_v3_scan_parameters_revised` at `c8cdb99`, measured timing,
stochastic agreement, scientific coverage, and remaining work.
[`RESTRUCTURE_PLAN.md`](RESTRUCTURE_PLAN.md) preserves the original source audit
and proposed reductions. The earlier source is available at that Git revision;
`legacy/` now retains only input tables, macros, and historical notes.

Preserved scientific results and small checking fixtures remain under
`material_scan/`; historical raw simulations and databases remain under
`data/`. New runtime output belongs under ignored `material_scan_data/`.
Read the [scientific definition](material_scan/docs/science.md),
[historical results](material_scan/docs/results.md), and
[data and recovery instructions](material_scan/docs/data.md).

## Next scientific work

1. Verify the origins and recorded definitions of the existing 2,361-site
   results before reusing their converged design. Local later reports extend
   the older 512-site results in the tracked scientific notes.
2. Fix operating temperature and retain the niobium-compatible upper-film gap
   limit. Test tolerance and physical support near the finalists' 5 GPa $C_{44}$
   lower limit; the confirmed leaders remain statistically tied.
3. Complete independent GP and CMA-ES comparisons with equal measured point
   counts, retaining slow or failed transport as absent measurements. Confirm
   finalists on the full spatial design with unused random streams.
4. Obtain measured SiC scattering and decay constants, bracket film lifetimes,
   and compare independently justified interface models before fabrication advice.
