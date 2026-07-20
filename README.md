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
| `sensitivity_params.py` | **single source of truth** for every swept parameter (command, default, bounds, unit) |
| `sensitivity_utils.py` | shared helpers (macro line rewriting, log retention, formatting) |
| `sensitivity_template_*.mac` | Geant4 macro templates; the suffix is the `/run/beamOn` count |
| `SensitivityAnalysis_Morris_run_record_Mac_M4Max.md` | macOS setup, defaults, launch commands, operational rules |
| `SensitivityAnalysis_Morris_run_record_Mimir.md` | BNL mimir setup, Mac↔mimir path correspondence, cross-machine verification |
| `G4CMP_crash_and_memory_analysis.md` | root-cause analysis of two Geant4/G4CMP failure modes (see below) |

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

## Reproducibility caveat

Geant4's `Main.cc` seeds CLHEP from `clock()`, so hits files are **not**
reproducible across machines or reruns. What *is* reproducible with a fixed
`SENSITIVITY_MORRIS_SEED` is the design, the generated macros and lattice
configs, and the manifest — all verified byte-identical between macOS and
mimir, as is stage 2's output given identical hits input.

## Credits

The quasiparticle and decoherence-rate calculations in `stage2_compute_QPs.py`
are adapted from Paul Baity's `calculate_QPs` / `calculate_xQPs`.
