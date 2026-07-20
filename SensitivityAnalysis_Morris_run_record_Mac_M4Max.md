# SensitivityAnalysis_Morris Mac M4 Max Run Record

Last updated: 2026-07-20

This note records the current local macOS setup for
`stage1_run_simulations.py` in:

```text
/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
```

It mirrors the Mimir run record, but the paths and operational rules here
are for this Mac setup only. The local Mac runner should not depend on the
previous project folder `BNL_G4CMP_HT_Feb27`.

Since 2026-07-20 this same script file also runs on the BNL server `mimir`:
it selects its Geant4/G4CMP/conda layout and `G4SYSTEM` from `sys.platform`
at import time, so the Mac and mimir copies are byte-identical rather than
forked. The mimir-side paths, the sync record, and the cross-machine
verification results live in `SensitivityAnalysis_Morris_run_record_Mimir.md`.

## Local Machine And Python

Confirmed local platform:

- macOS: `15.7.7`
- architecture: `arm64`
- working shell: `zsh`
- project directory:
  `/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac`
- dedicated conda environment:
  `/opt/anaconda3/envs/G4CMP`
- recommended Python:
  `/opt/anaconda3/envs/G4CMP/bin/python`
- SALib is installed in:
  `/opt/anaconda3/envs/G4CMP/lib/python3.12/site-packages/SALib`

The CPU brand string could not be queried from this sandboxed session, so
`M4 Max` is the machine label used for this note rather than an independently
verified `sysctl` value.

## Current Geant4 And G4CMP Setup

The current local script defaults to these paths:

- Geant4 root:
  `/Users/huan-hsintseng/Geant4`
- Geant4 environment script:
  `/Users/huan-hsintseng/Geant4/Geant4-Install/share/Geant4-10.7.4/geant4make/geant4make.sh`
- G4CMP environment script:
  `/Users/huan-hsintseng/Geant4/G4CMP/g4cmp_env.sh`
- source G4CMP CrystalMaps directory:
  `/Users/huan-hsintseng/Geant4/G4CMP/CrystalMaps`
- stock source lattice config:
  `/Users/huan-hsintseng/Geant4/G4CMP/CrystalMaps/Si/config.txt`
- Geant4 work directory:
  `/Users/huan-hsintseng/geant4_workdir`
- Geant4/G4CMP system name:
  `Darwin-clang`
- built binary directory:
  `/Users/huan-hsintseng/geant4_workdir/bin/Darwin-clang`
- built library directory:
  `/Users/huan-hsintseng/geant4_workdir/lib/Darwin-clang`
- detector application executable:
  `/Users/huan-hsintseng/geant4_workdir/bin/Darwin-clang/Main`

These key files were confirmed present on 2026-07-13:

- `geant4make.sh`
- `g4cmp_env.sh`
- `G4CMP/CrystalMaps/Si/config.txt`
- `geant4_workdir/bin/Darwin-clang/Main`

## Important Project Files

- unified Mac runner, QP pipeline stage 1 (simulation only; writes
  `qp_manifest.jsonl` for stage 2). One script, one `SENSITIVITY_DEBUG_MODE`
  switch selects serial-debug vs. parallel-production behavior (see
  "Current Active Script Defaults" below):
  `stage1_run_simulations.py`
- QP pipeline stage 2 (quasiparticle / decoherence-rate post-processing;
  no Geant4/G4CMP dependency; consumes the manifest from stage 1):
  `stage2_compute_QPs.py`
- shared parameter definitions (`electrode_params`, `detector_params`,
  `G4CMP_params`, `config_params`, `QPDE_params`, `PINNED_G4CMP_COMMANDS`),
  the single source of truth so the design can't drift:
  `sensitivity_params.py`
- shared small helpers (`format_duration`, `format_config_entry`,
  `find_macro_value`, `should_keep_success_log`, `finalize_log_file`,
  `replace_line`) used by the unified runner:
  `sensitivity_utils.py`
- archived predecessor scripts (superseded by the unified runner above;
  kept for reference only, not run directly):
  `old_version/SensitivityAnalysis_Morris_debug_mac.py`
  `old_version/SensitivityAnalysis_Morris_debug_mac_QP.py`
  `old_version/SensitivityAnalysis_Morris_Mac_M4Max.py`
- active Mac macro template:
  `sensitivity_template_beamOn1e6.mac`
- older or alternate templates:
  `sensitivity_template_beamOn10000.mac`
  `sensitivity_template_beamOn1e8.mac`
  `sensitivity_template_v0.mac`
- generated macros:
  `output/<run_id>/macros/Morris_N.mac`
- generated per-sample G4CMP lattice configs:
  `output/<run_id>/CrystalMaps/Morris_N/Si/config.txt`
- Geant4 logs:
  `results/<run_id>/logs/Morris_N.log`
- hit files:
  `results/<run_id>/hits/Morris_N_hitsfile.txt`
- Morris sample sequence (all sampled parameter values, one row per sample):
  `results/<run_id>/MorrisSequence.csv`
- QP pipeline manifest (stage 1 output, stage 2 input):
  `results/<run_id>/qp_manifest.jsonl`
- QP pipeline per-sample results (stage 2 output):
  `results/<run_id>/qps/Morris_N_QPs.npz`
  `results/<run_id>/qps/Morris_N_xQPs.npz`
- QP pipeline summary CSV (stage 2 output):
  `results/<run_id>/qp_summary.csv`

## Current Active Script Defaults

`stage1_run_simulations.py` reads a single
`SENSITIVITY_DEBUG_MODE` environment variable to pick its execution mode;
both modes generate the identical Morris design and macros, only how each
sample is launched/reported differs:

- `SENSITIVITY_DEBUG_MODE=1` -- serial debug mode:
  - workers: `1`
  - run id prefix: `morris_debug_serial_`
  - Geant4/G4CMP stdout/stderr streamed live to the screen (and saved to
    log files)
- `SENSITIVITY_DEBUG_MODE=0` (default) -- parallel production mode:
  - workers: `SENSITIVITY_MAX_WORKERS` (default `2`)
  - run id prefix: `morris_mac_`
  - Geant4/G4CMP output captured straight to log files only

Shared defaults regardless of mode:

- default macro template:
  `/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac/sensitivity_template_beamOn1e6.mac`
- default run working directory:
  `/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac`
- default output directory:
  `output/<run id prefix><uuid>`
- default results directory:
  `results/<run id prefix><uuid>`
- default log mode:
  `full`
- default progress update interval:
  `25`
- default sample limit:
  none, unless `SENSITIVITY_MAX_SAMPLES` is set

The current Morris design has (confirmed by running with
`SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=1` on 2026-07-16, after
the QP pipeline split described below, and unaffected by the later merge
into one script):

- parameter groups: `50`
- expanded variables: `53`
- trajectories: `128`
- num levels: `4`
- full sample count: `6912`

On 2026-07-20 the three `QPLim` parameters' lower bound was raised from `1`
to `2` in `sensitivity_params.py` (recommendation 1 of
`G4CMP_crash_and_memory_analysis.md`), which removes the
`G4CMPKaplanQP::AbsorbPhonon` infinite-loop/runaway-memory failure mode. The
group and variable counts above are unchanged, but **the sampled design is
different**, so runs from before that date are not directly comparable to
runs after it.

The full sample count comes from:

```text
128 * (53 + 1) = 6912
```

All parameter groups (`electrode_params`, `detector_params`, `G4CMP_params`,
`config_params`, `QPDE_params`) live in `sensitivity_params.py`, a single
source of truth imported by the unified runner and by `stage2_compute_QPs.py`
indirectly via the manifest -- there is no longer a second script whose
design could drift out of sync.

## Current Template

The active Mac template is:

```text
/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac/sensitivity_template_beamOn1e6.mac
```

Important active template values:

```text
/main/gun/setGunType phonon_Caustic
/main/gun/setEnergy 382.0e-6
/g4cmp/HitsFile test_output.txt
/run/beamOn 1000000
```

The `HitsFile` template value is intentionally overwritten by the Python
script for each Morris sample. The generated macro should contain an absolute
run-local hits path before any long run is started.

The substrate lines are commented in the template:

```text
#/main/detector_param/setSubstrateG4Name G4_Si
#/main/detector_param/setSubstrateName Si
```

The Python script activates and rewrites them in each generated macro:

```text
/main/detector_param/setSubstrateG4Name G4_Si
/main/detector_param/setSubstrateName Si
```

## Launch Commands

Generate all Morris run files without launching Geant4 (serial debug mode):

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
conda activate G4CMP
SENSITIVITY_DEBUG_MODE=1 SENSITIVITY_GENERATE_ONLY=1 python stage1_run_simulations.py
```

Generate only a small debug subset:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
conda activate G4CMP
SENSITIVITY_DEBUG_MODE=1 SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 python stage1_run_simulations.py
```

Run a small Geant4 debug subset (serial, live-streamed output):

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
conda activate G4CMP
SENSITIVITY_DEBUG_MODE=1 SENSITIVITY_MAX_SAMPLES=2 python stage1_run_simulations.py
```

Run the full serial Morris simulation:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
conda activate G4CMP
SENSITIVITY_DEBUG_MODE=1 python stage1_run_simulations.py
```

Generate a small subset in parallel production mode without launching Geant4
(`SENSITIVITY_DEBUG_MODE` unset or `0` is the default):

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 \
/opt/anaconda3/envs/G4CMP/bin/python stage1_run_simulations.py
```

Run the full parallel production design with its default worker count and
one-million-event template:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
/opt/anaconda3/envs/G4CMP/bin/python -u stage1_run_simulations.py
```

Set the worker count explicitly when needed:

```zsh
SENSITIVITY_MAX_WORKERS=4 \
/opt/anaconda3/envs/G4CMP/bin/python -u stage1_run_simulations.py
```

Use the conda Python explicitly if `conda activate` is not available in the
current terminal:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
SENSITIVITY_DEBUG_MODE=1 SENSITIVITY_MAX_SAMPLES=2 \
/opt/anaconda3/envs/G4CMP/bin/python stage1_run_simulations.py
```

## Environment Overrides

The local Mac script can be redirected without editing the file:

- `SENSITIVITY_DEBUG_MODE` (`1` = serial debug, `0`/unset = parallel
  production, the default)
- `SENSITIVITY_MORRIS_SEED` (seeds the SALib Morris sampler; unset = the
  historical non-reproducible design. Set the same integer here and on mimir
  to make both machines generate an identical design)
- `SENSITIVITY_SAMPLE_TIMEOUT` (per-sample wall-clock limit in seconds; `0`,
  the default, disables it. Kills the whole process group, so no orphaned
  `Main` is left behind -- see recommendation 2 of
  `G4CMP_crash_and_memory_analysis.md`)
- `SENSITIVITY_GEANT4_ROOT`
- `SENSITIVITY_G4CMP_ROOT`
- `SENSITIVITY_G4WORKDIR`
- `SENSITIVITY_G4SYSTEM`
- `SENSITIVITY_CONDA_ENV`
- `SENSITIVITY_GEANT4_ENV`
- `SENSITIVITY_G4CMP_ENV`
- `SENSITIVITY_G4CMP_CRYSTALMAPS`
- `SENSITIVITY_G4CMPLIB`
- `SENSITIVITY_G4CMPBIN`
- `SENSITIVITY_MAIN_EXE`
- `SENSITIVITY_RUN_WORKDIR`
- `SENSITIVITY_MACRO_TEMPLATE`
- `SENSITIVITY_MAX_WORKERS`
- `SENSITIVITY_GENERATE_ONLY`
- `SENSITIVITY_MAX_SAMPLES`
- `SENSITIVITY_VERBOSE`
- `SENSITIVITY_PROGRESS_EVERY`
- `SENSITIVITY_LOG_MODE`
- `SENSITIVITY_LOG_FRACTION`
- `SENSITIVITY_LOG_SEED`

## Mac-Specific Runtime Behavior

The parallel runner passes a stable internal run ID to macOS worker processes.
This prevents spawned workers from creating or resolving different output,
log, and CrystalMaps directories. Run-directory creation and Morris sequence
generation occur only in the parent process.

Sampled macro commands are required to exist in the selected template. A
commented command is activated when sampled; a truly missing command stops
generation with an error instead of silently creating an inactive Morris
dimension.

For each sample, the script builds a shell command that:

1. sources the local Geant4 environment script
2. unsets inherited `G4CMPINSTALL`, `G4CMPINCLUDE`, `G4LATTICEDATA`, and
   `G4CMPLIB`
3. sources the local G4CMP environment script
4. exports per-sample `G4LATTICEDATA`
5. prepends the local G4CMP binary directory to `PATH`
6. prepends the local G4CMP library directory to `DYLD_LIBRARY_PATH`
7. prepends the local G4CMP library directory to `LD_LIBRARY_PATH`
8. changes directory to the project directory
9. runs the absolute `Main` executable with the generated macro

The current default run working directory is:

```text
/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
```

This is deliberate. The local Mac script no longer changes into:

```text
/Users/huan-hsintseng/Downloads/BNL_G4CMP_HT_Feb27/Main
```

The old `BNL_G4CMP_HT_Feb27` folder is not part of this Mac runner's runtime
path model.

## CrystalMaps And G4LATTICEDATA

The baseline stock config remains in the G4CMP installation:

```text
/Users/huan-hsintseng/Geant4/G4CMP/CrystalMaps/Si/config.txt
```

The Morris run does not modify that stock file. Instead, each sample gets a
fresh copied and edited lattice config under this project:

```text
output/<run_id>/CrystalMaps/Morris_N/Si/config.txt
```

For that sample, the Geant4 process gets:

```text
G4LATTICEDATA=output/<run_id>/CrystalMaps/Morris_N
```

Because the macro uses:

```text
/main/detector_param/setSubstrateName Si
```

G4CMP then looks for:

```text
$G4LATTICEDATA/Si/config.txt
```

This is why the generated file must be:

```text
output/<run_id>/CrystalMaps/Morris_N/Si/config.txt
```

Do not change `setSubstrateName` to `Morris_N` unless the generated lattice
layout is also changed to match.

## Hits File Path Rule

The template still contains:

```text
/g4cmp/HitsFile test_output.txt
```

The script rewrites this to:

```text
/g4cmp/HitsFile /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac/results/<run_id>/hits/Morris_N_hitsfile.txt
```

This avoids writing hits into whichever directory happens to be the executable
working directory. Before a long run, inspect `Morris_0.mac` and confirm
`HitsFile` is an absolute path under `results/<run_id>/hits`.

## Sanity Check Before Long Runs

Generate a two-sample dry run:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
SENSITIVITY_DEBUG_MODE=1 SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 \
/opt/anaconda3/envs/G4CMP/bin/python stage1_run_simulations.py
```

Then inspect the generated files:

```zsh
rg -n 'setEnergy|setSubstrateName|setSubstrateG4Name|beamOn|HitsFile' \
  output/<run_id>/macros/Morris_0.mac
```

Expected important lines:

```text
/main/gun/setEnergy <sampled phonon-scale value> eV
/g4cmp/HitsFile /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac/results/<run_id>/hits/Morris_0_hitsfile.txt
/main/detector_param/setSubstrateG4Name G4_Si
/main/detector_param/setSubstrateName Si
/run/beamOn 1000000
```

Also confirm:

```text
output/<run_id>/CrystalMaps/Morris_0/Si/config.txt
```

exists before launching Geant4.

## Monitoring Commands

Check active Python or `Main` processes:

```zsh
pgrep -af 'stage1_run_simulations.py|geant4_workdir/bin/Darwin-clang/Main'
```

Count logs for a run:

```zsh
find results/<run_id>/logs -maxdepth 1 -type f | wc -l
```

Count hit files for a run:

```zsh
find results/<run_id>/hits -maxdepth 1 -type f | wc -l
```

Search logs for fatal or event-abort problems:

```zsh
rg -n 'Fatal|Aborting|Unable to open|ProcMan201|Event Must Be Aborted' \
  results/<run_id>/logs
```

Search logs for G4CMP warnings:

```zsh
rg -n 'G4Exception|Boundary010|This is just a warning' \
  results/<run_id>/logs
```

Inspect a generated macro:

```zsh
rg -n 'setEnergy|setSubstrateName|setSubstrateG4Name|beamOn|HitsFile' \
  output/<run_id>/macros/Morris_0.mac
```

## Two-Stage QP Pipeline

Running Geant4/G4CMP and computing quasiparticles inline made every sample
take much longer than either half alone, so the pipeline is split into two
independently runnable stages:

1. **Stage 1 -- simulation**
   (`stage1_run_simulations.py`, either mode):
   generates Morris macros/configs, runs Geant4/G4CMP, and writes hits
   files. Also writes a `qp_manifest.jsonl` recording everything the QP
   calculation needs for every sample, in the schema described below.
2. **Stage 2 -- QP computation** (`stage2_compute_QPs.py`): reads that
   manifest and each sample's hits file, and computes quasiparticle counts
   and the decoherence-rate ODE, independent of Geant4/G4CMP entirely.

Adapted from Paul Baity's `calculate_QPs`/`calculate_xQPs` functions in
`Sensitivity_Analysis_Paul/SensitivityAnalysis.ipynb`.

This two-stage split originated when serial-debug and parallel-production
were separate scripts (`SensitivityAnalysis_Morris_debug_mac_QP.py` and
`SensitivityAnalysis_Morris_Mac_M4Max.py`, now archived under
`old_version/`) that both had to feed `stage2_compute_QPs.py` with an
identical manifest schema; keeping their `QPDE_params` blocks in sync was a
manual, error-prone step. Merging both modes into one script
(`stage1_run_simulations.py`) and moving all
parameter groups into `sensitivity_params.py` removed that risk entirely --
there is now exactly one `QPDE_params` definition, imported by the one
runner.

While wiring the manifest into the (then-separate) M4Max script, a
pre-existing syntax error was also found and fixed: a stray `nstoo` typo
immediately after the `QPDE_params` list's closing bracket made the file
fail to even import. That predates the QP pipeline split and was unrelated
to it; it's preserved fixed in the current unified script.

### Why the manifest is enough to decouple the two stages

Every value stage 2 needs (QP creation energy, electrode positions, sensor
surface Z, electrode dimensions, simulated event count, and the
quasiparticle-ODE parameters) is fully determined by the generated macro and
that sample's Morris row -- none of it depends on the Geant4/G4CMP run
actually finishing. Stage 1 therefore writes one manifest line per sample
immediately after generating that sample's macro, before the sample is even
run. This means stage 2 never needs the macro template, the Geant4
environment, or G4CMP -- only the manifest plus `results/<run_id>/hits/`.

### Manifest schema

`results/<run_id>/qp_manifest.jsonl` -- one JSON object per line, one line
per sample:

| Field | Meaning |
|---|---|
| `sample_name` | e.g. `Morris_0` |
| `hits_file` | absolute path to that sample's hits file |
| `gap` | QP creation energy [eV], from that sample's `/main/detector_param/setTopGap` |
| `qx`, `qy` | electrode X/Y positions [mm], from `setXLocations`/`setYLocations` |
| `chip_z` | sensor-surface End Z [m], derived from `setSubThickness / 2` |
| `height`, `width` | electrode lateral dimensions [um], from `setHeight`/`setWidth` |
| `thickness` | top-layer thickness [um], from `setTopThickness` |
| `n_sim` | number of simulated primary events, from that sample's `/run/beamOn` |
| `f_01`, `r`, `s`, `I_ph`, `pt`, `n_cooper` | quasiparticle-ODE parameters, sampled per-run by Morris (see `QPDE_params` in `sensitivity_params.py`) |

`f_01` and `s` are single scalars broadcast across every electrode, not
per-qubit vectors like in Paul's notebook -- his vectors were calibrated to
his specific 6-qubit device, and this template's geometry has 17 electrodes
instead. `n_sim` replaces Paul's hardcoded `N_sim` constant (inconsistent
across his notebook cells, 1e8-1.25e9) with the actual `/run/beamOn` value
used for that sample.

### Stage 1 usage

Serial debug mode:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
conda activate G4CMP
SENSITIVITY_DEBUG_MODE=1 SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 python stage1_run_simulations.py
```

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
conda activate G4CMP
SENSITIVITY_DEBUG_MODE=1 python stage1_run_simulations.py
```

Parallel production mode:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 \
/opt/anaconda3/envs/G4CMP/bin/python stage1_run_simulations.py
```

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
/opt/anaconda3/envs/G4CMP/bin/python -u stage1_run_simulations.py
```

Either mode prints the exact stage 2 command to run next at the end of a
run, e.g.:

```text
Simulation stage complete. To compute quasiparticles/decoherence
rate from the hits files just generated, run:
    python stage2_compute_QPs.py results/morris_debug_serial_<run-id>
```

or, from parallel production mode:

```text
Simulation stage complete. To compute quasiparticles/decoherence
rate from the hits files just generated, run:
    python stage2_compute_QPs.py results/morris_mac_<run-id>
```

### Stage 2 usage

Only needs `numpy`/`pandas` -- no `conda activate G4CMP` or Geant4 env
sourcing required, though using the same conda env is harmless:

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
python3 stage2_compute_QPs.py results/morris_debug_serial_<run-id>
```

Optional flag:

```zsh
python3 stage2_compute_QPs.py results/morris_debug_serial_<run-id> --progress-every 10
```

`--progress-every N` controls how often a progress line is printed (default
`25`, or `$SENSITIVITY_PROGRESS_EVERY` if set).

Stage 2 can be run any time after stage 1 has started writing hits files --
even before all samples finish, since it just skips any sample whose hits
file isn't present yet (printing a `QP: skipping ...` line for that sample).

### Idempotency

Every stage 2 run wipes `qps/` and `qp_summary.csv` in that results
directory before processing, so re-running it (e.g. after more samples have
finished, or after changing the QP calculation) never produces duplicate
`qp_summary.csv` rows or stale `.npz` files left over from a prior manifest.
Stage 1 is unaffected by this: it always starts a fresh `RUN_ID`
(new UUID), so `qp_manifest.jsonl` is never appended to across runs.

## Interpretation Notes

- `stage1_run_simulations.py` is the only runner
  script now in active use; it merges what used to be three separate
  scripts (`SensitivityAnalysis_Morris_debug_mac.py`,
  `SensitivityAnalysis_Morris_debug_mac_QP.py`,
  `SensitivityAnalysis_Morris_Mac_M4Max.py`) behind the
  `SENSITIVITY_DEBUG_MODE` switch. Those three are kept, unmodified, under
  `old_version/` for reference only -- do not run them directly, since
  `sensitivity_params.py`/`sensitivity_utils.py` changes are not
  guaranteed to stay compatible with them.
- `SensitivityAnalysis_Morris_Mac_M4Max.py` (the archived parallel
  predecessor) was derived from `SensitivityAnalysis_Morris_Mimir.py`.
- `SensitivityAnalysis_Morris_debug.py` is still the old Mimir-oriented script
  and contains Mimir path assumptions.
- The local Mac runner uses `beamOn 1000000` by default because the active
  template is `sensitivity_template_beamOn1e6.mac`.
- The script samples phonon-scale gun energy in eV. It should not use GeV
  energy with `phonon_Caustic`.
- The generated Morris `CrystalMaps/Morris_N` folders are reproducible run
  artifacts and can be regenerated.
- The stock
  `/Users/huan-hsintseng/Geant4/G4CMP/CrystalMaps/Si/config.txt` is the
  baseline template and should be kept as part of the G4CMP installation.
- The previous `BNL_G4CMP_HT_Feb27` project folder is not required by the
  current Mac run script.
