# QPU Material Sensitivity Analysis

Morris (elementary-effects) sensitivity analysis of superconducting-qubit
substrate and film parameters, driving Geant4/G4CMP phonon simulations of a
BNL detector geometry and converting the resulting phonon hits into
quasiparticle counts and a qubit decoherence-rate contribution.

The sweep covers 50 parameter groups / 53 expanded variables across detector
geometry, film properties, G4CMP transport settings, silicon lattice
constants, and the quasiparticle ODE. With 128 trajectories at 4 levels the
full design is `128 * (53 + 1) = 6912` samples.

## Two-stage pipeline

Running the simulation and the quasiparticle calculation together made one
slow job out of two independently runnable halves, so they are split:

| Stage | Script | Needs Geant4/G4CMP? | Produces |
|---|---|---|---|
| 1 — simulation | `stage1_run_simulations.py` | yes | per-sample macros, lattice configs, hits files, and `qp_manifest.jsonl` |
| 2 — quasiparticles | `stage2_compute_QPs.py` | no (numpy/pandas only) | `qps/*.npz`, `qp_summary.csv` |
| 2b — sensitivity correlations | `stage2_compute_QPs_sensitivity_analysis.py` | no (numpy/pandas/matplotlib only) | `sensitivity_correlations.{csv,png}`, `sensitivity_chi2_correlations.{csv,png}`, `sensitivity_corr_matrix*.png` |

Stage 1 writes one manifest line per sample *at macro-generation time*,
before the sample runs, because everything stage 2 needs is determined by the
macro and that sample's Morris row. Stage 2 therefore never needs the macro
template, the Geant4 environment, or G4CMP — only the manifest plus the hits
files. It can run while stage 1 is still going, skipping samples whose hits
file does not exist yet, and it wipes its own prior outputs on every run so
re-running never duplicates rows.

## Layout

| File | Role |
|---|---|
| `stage1_run_simulations.py` | stage 1 runner; serial-debug and parallel-production modes behind one switch |
| `stage2_compute_QPs.py` | stage 2; quasiparticle binning and the decoherence-rate ODE |
| `stage2_compute_QPs_sensitivity_analysis.py` | stage 2b; parameter-vs-outcome correlation analysis (see below) |
| `SensitivityAnalysis_correlation_math.md` | equations + annotated code walkthrough of stage 2b's two methods |
| `sensitivity_params.py` | **single source of truth** for every swept parameter (command, default, bounds, unit) |
| `sensitivity_utils.py` | shared helpers (macro line rewriting, log retention, formatting) |
| `sensitivity_template_*.mac` | Geant4 macro templates; the suffix is the `/run/beamOn` count |
| `SensitivityAnalysis_Morris_run_record_Mac_M4Max.md` | macOS setup, defaults, launch commands, operational rules |
| `SensitivityAnalysis_Morris_run_record_Mimir.md` | BNL mimir setup, Mac↔mimir path correspondence, cross-machine verification |
| `G4CMP_crash_and_memory_analysis.md` | root-cause analysis of two Geant4/G4CMP failure modes (see below) |
| `SensitivityAnalysis_Paul.ipynb` | Paul Baity's original Sobol-based notebook; source material for stage 2/2b's QP/ODE/correlation logic (messy, edited in place many times — see caveats below) |
| `Modeling phonon-mediated quasiparticle poisoning in superconducting qubit arrays.pdf` | reference paper this project's QP-poisoning model is based on |

`SensitivityAnalysis_Morris*.py` at the top level (other than the two stage
scripts) and `SensitivityAnalysis_{Sobol,Percentage}.py` are the previous
generation, kept for reference. They are not maintained against the current
`sensitivity_params.py`/`sensitivity_utils.py`.

Generated `output/` and `results/` directories are **not** tracked — each run
mints a fresh UUID directory and they reach hundreds of MB. They are
reproducible from the code plus a fixed `SENSITIVITY_MORRIS_SEED`.

## Running

One file runs on both the development Mac and the BNL server `mimir`: the
Geant4/G4CMP/conda layout and `G4SYSTEM` are selected from `sys.platform`,
and every path stays overridable via a `SENSITIVITY_*` environment variable.
There is deliberately no per-machine fork.

Generate the run files without launching Geant4:

```bash
SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 python -u stage1_run_simulations.py
```

A small real run, then stage 2:

```bash
SENSITIVITY_MAX_SAMPLES=54 SENSITIVITY_SAMPLE_TIMEOUT=600 \
SENSITIVITY_MACRO_TEMPLATE="$PWD/sensitivity_template_beamOn10000.mac" \
python -u stage1_run_simulations.py

python stage2_compute_QPs.py results/<run_id>
```

Stage 1 prints the exact stage 2 command for the run it just finished.

### Key environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SENSITIVITY_DEBUG_MODE` | `0` | `1` = serial, live-streamed Geant4 output; `0` = parallel, log-only |
| `SENSITIVITY_MAX_WORKERS` | 2 (macOS) / 4 (Linux) | parallel worker processes |
| `SENSITIVITY_MAX_SAMPLES` | all | cap the design to the first N samples |
| `SENSITIVITY_GENERATE_ONLY` | `0` | write macros/configs but never launch Geant4 |
| `SENSITIVITY_MACRO_TEMPLATE` | `sensitivity_template_beamOn1e6.mac` | which template (and so which `/run/beamOn`) to use |
| `SENSITIVITY_MORRIS_SEED` | unset | seed the Morris sampler; identical seeds give identical designs across machines |
| `SENSITIVITY_SAMPLE_TIMEOUT` | `0` (off) | per-sample wall-clock limit in seconds; kills the whole process group |

Path overrides (`SENSITIVITY_GEANT4_ROOT`, `SENSITIVITY_G4CMP_ROOT`,
`SENSITIVITY_G4WORKDIR`, `SENSITIVITY_G4SYSTEM`, `SENSITIVITY_MAIN_EXE`, …)
are listed in the two run records.

## Known issues worth reading before a long run

`G4CMP_crash_and_memory_analysis.md` documents two measured failure modes in
the C++ layer, both independent of this Python orchestration:

1. **Infinite loop in `G4CMPKaplanQP::AbsorbPhonon` when a film's
   `lowQPLimit` is 1** — the quasiparticle can never fall below the exit
   threshold, so the sample spins at 100% CPU and can grow to tens of GB of
   RSS. Mitigated by sweeping the three `QPLim` parameters over `[2, 5]`
   (applied 2026-07-20) and, as a backstop, by `SENSITIVITY_SAMPLE_TIMEOUT`.
2. **Exit-time SIGSEGV** (~0.4% of runs) from a stale `G4TouchableHandle`
   released after its allocator pool is torn down. Harmless: physics and the
   hits file are complete before the fault, and the runner logs the failure
   and continues the batch.

A third, open finding: `/g4cmp/minEPhonons` is pinned at `0.000382 eV`, which
is above two of the four Morris levels for `/main/gun/setEnergy`, so roughly
half the design simulates nothing. That is a sweep-design decision, not a
bug, and is still unresolved.

## Stage 2b: sensitivity correlation analysis

`stage2_compute_QPs_sensitivity_analysis.py` reproduces the parameter-vs-outcome
correlation analysis in Paul Baity's `SensitivityAnalysis_Paul.ipynb` cells
`In[3]`/`In[5]` for this Morris run. It reads `MorrisSequence.csv` (the design)
and the `qps/*_xQPs.npz` curves stage 2 already wrote (no ODE recompute) and
answers "which parameters correlate with more decoherence." Full math in
`SensitivityAnalysis_correlation_math.md`.

```bash
python stage2_compute_QPs_sensitivity_analysis.py --results-dir results/<run_id>
```

Two `--method`s (default `both`):

- **`integrated`** (experiment-free): outcome = `log10(total_integrated_DG)`,
  correlated against every parameter via `np.corrcoef`. Faithful to Paul's
  experiment-free sibling cell.
- **`chi2`** (faithful to `In[3]`): outcome = a chi-squared goodness-of-fit of
  each electrode's simulated ΔΓ(t) against real experimental
  Delta-Gamma-vs-delay data (`--experimental-dir`, default points at a 150 µs
  NbGND dataset found on mimir outside this project — see the script docstring
  for the exact path and why 150 µs was chosen).

### Known concerns / caveats

- **The chi² outcome is magnitude-dominated, not a literal experiment fit.**
  This run's localized `phonon_Caustic` injection at 1e5 events produces
  simulated ΔΓ 1–2 orders of magnitude larger than the experimental data, so
  `chi2 ≈ sum(simulated_DG^2)` (verified ratio 0.999) — read chi² correlations
  as "which parameters drive the simulated response," not as calibration
  against experiment.
- **17 electrodes vs 6 measured qubits.** Only 6 experimental delay curves
  exist. `--chi2-channels electrodes` (default) scores all 17 electrodes
  against their nearest qubit's curve — the 6 curves are reused by proximity,
  so this is 17 simulated channels vs 6 measured references, not 17
  independent fits. `--chi2-channels qubits` gives Paul's literal 6-channel
  reproduction.
- **Signal sparsity.** Only ~1500 of 6912 samples (~22%) produce non-zero
  integrated decoherence at 1e5 events; the rest are dropped from the
  correlation (same filtering spirit as Paul's `len(output_x[i])>0` guard).
- **`qp_summary.csv` must be complete before running this script.** It is
  regenerated from scratch by every `stage2_compute_QPs.py` run and by nothing
  else — if a stage-2 run is interrupted (e.g. killed mid-run from an IDE
  debugger), the summary is left truncated. This script detects and warns on
  an incomplete summary and falls back to deriving outcomes directly from the
  hits files, but that path is much slower.
- **Correlation is linear/Pearson only.** A parameter with a strong
  non-monotonic effect can show `r≈0` here even though it matters; this is a
  screening tool, not a full sensitivity index (see the Morris μ*/σ indices
  computed by stage 1's own design for a complementary, non-linear-aware view).

## Reproducibility caveat

Geant4's `Main.cc` seeds CLHEP from `clock()`, so hits files are **not**
reproducible across machines or reruns. What *is* reproducible with a fixed
`SENSITIVITY_MORRIS_SEED` is the design, the generated macros and lattice
configs, and the manifest — all verified byte-identical between macOS and
mimir, as is stage 2's output given identical hits input.

## Credits

The quasiparticle and decoherence-rate calculations in `stage2_compute_QPs.py`
are adapted from Paul Baity's `calculate_QPs` / `calculate_xQPs`.
