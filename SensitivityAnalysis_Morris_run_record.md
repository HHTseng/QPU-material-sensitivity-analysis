# SensitivityAnalysis_Morris Run Record

Last updated: 2026-06-10 18:19:11 EDT

This note records what was debugged and what is currently running for
`SensitivityAnalysis_Morris.py` on `mimir`.

## Current Active Run

Corrected Morris run:

- tmux session: `morris_beam10000_ev_20260610_181415`
- Python: `/home/htseng/.conda/envs/G4CMP/bin/python`
- Template: `/home/htseng/Sensitivity_Analysis/sensitivity_template_beamOn10000.mac`
- Workers: `SENSITIVITY_MAX_WORKERS=4`
- Output directory: `/home/htseng/Sensitivity_Analysis/output/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0`
- Results directory: `/home/htseng/Sensitivity_Analysis/results/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0`
- Launch log: `/home/htseng/Sensitivity_Analysis/results/launch_logs/morris_beam10000_ev_20260610_181415.log`

At the update time above, the run was active and processing roughly
`Morris_422.mac` through `Morris_425.mac`. Counts observed at that time:

- log files: `457`
- hit files: `456`
- fatal-error search in current logs: no matches for `Fatal`, `Aborting`,
  `Unable to open`, `ProcMan201`, or `Event Must Be Aborted`
- expected warning seen in early logs: `Boundary010`, reported by Geant4 as
  `This is just a warning message.`

Attach to the running session:

```bash
tmux attach -t morris_beam10000_ev_20260610_181415
```

Stop the current run if needed:

```bash
tmux kill-session -t morris_beam10000_ev_20260610_181415
```

## Launch Command Used

The corrected run was started with:

```bash
cd /home/htseng/Sensitivity_Analysis_HT
env \
  SENSITIVITY_MACRO_TEMPLATE=/home/htseng/Sensitivity_Analysis_HT/sensitivity_template_beamOn10000.mac \
  SENSITIVITY_MAX_WORKERS=4 \
  /home/htseng/.conda/envs/G4CMP/bin/python -u SensitivityAnalysis_Morris.py
```

The command is running inside tmux so it survives the terminal/tool session.

## Important Files

- `SensitivityAnalysis_Morris.py`
- `sensitivity_template.mac`
- `sensitivity_template_beamOn10000.mac`
- generated macros: `output/<run_id>/macros/Morris_N.mac`
- generated G4CMP lattice configs:
  `output/<run_id>/CrystalMaps/Morris_N/Si/config.txt`
- Geant4 logs: `results/<run_id>/logs/Morris_N.log`
- hit files: `results/<run_id>/hits/Morris_N_hitsfile.txt`
- Morris sequence CSV: `results/<run_id>/MorrisSequence.csv`

## Current Template

`sensitivity_template_beamOn10000.mac` is a duplicate of
`sensitivity_template.mac`, except its run length is reduced:

```text
/run/beamOn 10000
```

The original `sensitivity_template.mac` still has:

```text
/run/beamOn 1000000000
```

`SensitivityAnalysis_Morris.py` now uses the shorter HT template by default,
and can be pointed at the shorter template explicitly with:

```bash
SENSITIVITY_MACRO_TEMPLATE=/home/htseng/Sensitivity_Analysis_HT/sensitivity_template_beamOn10000.mac
```

## Path And I/O Fixes Applied

The initial fatal error was:

```text
G4LatticeReader::MakeLattice
Unable to open Si/config.txt
G4Exception: Aborting execution
```

Cause:

- the script generated per-sample lattice folders such as
  `CrystalMaps/Morris_0/config.txt`
- G4CMP looks for lattice data as:
  `$G4LATTICEDATA/<SubstrateName>/config.txt`
- with substrate name `Si`, that means the real required path is:
  `.../CrystalMaps/Morris_0/Si/config.txt`
- the earlier scripts wrote the config file one directory too shallow and
  pointed `G4LATTICEDATA` at the wrong level for that layout

Fix:

- each sample now gets its own lattice root:
  `output/<run_id>/CrystalMaps/Morris_N`
- each sample config is written to:
  `output/<run_id>/CrystalMaps/Morris_N/Si/config.txt`
- each Geant4 run exports:
  `G4LATTICEDATA=output/<run_id>/CrystalMaps/Morris_N`
- generated macros now keep substrate name aligned with the lattice folder:

```text
/main/detector_param/setSubstrateG4Name G4_Si
/main/detector_param/setSubstrateName Si
```

- `replace_line()` can replace commented template lines, so commented template
  defaults such as `#/main/detector_param/setSubstrateName Si` and
  `#/main/detector_param/setSubstrateG4Name G4_Si` are now activated safely

The important rule is:

- `setSubstrateName` and the last directory under `G4LATTICEDATA` must agree
- for the current HT scripts, that name is `Si`
- do not switch substrate name to `Morris_N` unless the lattice folder itself
  contains `Morris_N/config.txt` and the C++ application is meant to load that
  substrate name

## Hits File Path Fix

Another path bug showed up in the debug runs:

- the template files still contained:

```text
/g4cmp/HitsFile test_output.txt
```

- if that line is not rewritten, Geant4 writes the hits file relative to the
  executable working directory, which in these runs is:
  `/home/htseng/BNL_G4CMP_HT_Feb27/Main`
- this makes it look like no hits file was produced under the run results
  folder, even though output was written elsewhere

Fix:

- both `SensitivityAnalysis_Morris.py` and
  `SensitivityAnalysis_Morris_debug_serial.py` now replace that template line
  with a per-sample absolute path:

```text
/g4cmp/HitsFile /home/htseng/Sensitivity_Analysis_HT/results/<run_id>/hits/Morris_N_hitsfile.txt
```

Operational rule:

- never trust the template `HitsFile` value
- always verify the generated `Morris_0.mac` contains the run-local absolute
  hits path before launching a long run

## Template Run-Length Fix

The file name `sensitivity_template_beamOn10000.mac` was briefly misleading:

- it still contained `/run/beamOn 100000000`
- that made debug runs look hung even though Geant4 was just processing a huge
  event count

Fix:

- `sensitivity_template_beamOn10000.mac` now correctly contains:

```text
/run/beamOn 10000
```

Operational rule:

- always inspect the actual generated `Morris_0.mac`
- do not rely on the template filename alone to infer run length

Other code fixes:

- `Morris_Setup["num_vars"]` now uses the expanded variable count `VNUM`
- vector-valued config parameters are actually written to `config.txt`
- per-sample Geant4 output is captured in `results/<run_id>/logs`
- `SENSITIVITY_MAX_WORKERS` controls process concurrency
- `SENSITIVITY_VERBOSE=1` restores detailed generated-command printing
- G4CMP config keys were updated from the old single `acDeform` key to
  current keys `acDeform_e` and `acDeform_h`
- valid keys such as `l0_e` and `l0_h` are appended to generated config files
  when missing from the stock `Si/config.txt`

## Energy Bug Found During the 10000-Event Run

The first 10000-event Morris attempt used this run:

- tmux session: `morris_beam10000_20260610_181029`
- run id: `morris_42474dbe-5e7f-436c-9d67-075725e0bdf0`

It was stopped because the script was sampling:

```text
/main/gun/setEnergy 2-6 GeV
```

while the template uses:

```text
/main/gun/setGunType phonon_Caustic
```

That combination generated invalid phonon tracks. Logs showed examples like:

```text
G4Exception : ProcMan201
Negative currentInteractionLength for phononScattering
Event Must Be Aborted
Particle type : phononTS
Kinetic energy : 6 GeV
Momentum direction : (-nan,-nan,-nan)
```

Fix:

`SensitivityAnalysis_Morris.py` now samples phonon-scale energy around the
template value:

```python
("/main/gun/setEnergy ", 2*191.0e-6,
 [(2*191.0e-6)*lbf, (2*191.0e-6)*ubf], " eV")
```

A one-event smoke test after this change completed cleanly and loaded
`Morris_0` lattice data.

## Corrected Macro Sanity Check

For the active corrected run, `Morris_0.mac` contains:

```text
/main/gun/setEnergy 0.000191 eV
/g4cmp/HitsFile /home/htseng/Sensitivity_Analysis/results/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0/hits/Morris_0_hitsfile.txt
/main/detector_param/setSubstrateName Si
/run/beamOn 10000
```

This confirms the corrected run is using:

- eV phonon energy, not GeV
- run-local hit output
- run-local per-sample lattice roots with `Si/config.txt` under each one
- 10000 events per macro

## Path Checklist Before Long Runs

Before launching a large Morris run, check one generated macro and one
generated config file:

1. `Morris_0.mac` should contain:
   - `/g4cmp/HitsFile /absolute/path/to/results/<run_id>/hits/Morris_0_hitsfile.txt`
   - `/main/detector_param/setSubstrateName Si`
   - the intended `/run/beamOn ...` value
2. the generated config file should exist at:
   - `output/<run_id>/CrystalMaps/Morris_0/Si/config.txt`
3. the runtime should export:
   - `G4LATTICEDATA=output/<run_id>/CrystalMaps/Morris_0`

If any one of those three does not match, fix it before starting the full run.

## Monitor Commands

Check tmux session:

```bash
tmux ls
```

Check active Python and Geant4 processes:

```bash
pgrep -af 'SensitivityAnalysis_Morris.py|geant4_workdir/bin/Linux-g\+\+/Main'
```

Watch launch log:

```bash
tail -f /home/htseng/Sensitivity_Analysis/results/launch_logs/morris_beam10000_ev_20260610_181415.log
```

Count completed/started per-sample logs:

```bash
find /home/htseng/Sensitivity_Analysis/results/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0/logs \
  -maxdepth 1 -type f | wc -l
```

Count hit files:

```bash
find /home/htseng/Sensitivity_Analysis/results/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0/hits \
  -maxdepth 1 -type f | wc -l
```

Search for fatal or event-abort problems:

```bash
rg -n 'Fatal|Aborting|Unable to open|ProcMan201|Event Must Be Aborted' \
  /home/htseng/Sensitivity_Analysis/results/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0/logs
```

Search for warnings:

```bash
rg -n 'G4Exception|Boundary010|This is just a warning' \
  /home/htseng/Sensitivity_Analysis/results/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0/logs
```

Inspect a generated macro:

```bash
rg -n 'setEnergy|setSubstrateName|beamOn|HitsFile' \
  /home/htseng/Sensitivity_Analysis/output/morris_fa27e881-0db5-4aa3-8b31-9491004bb6d0/macros/Morris_0.mac
```

## Interpretation Notes

- `Boundary010` from `G4CMPPhononBoundary::DoReflection` is currently being
  treated as a warning because Geant4 prints `This is just a warning message.`
- The earlier `Unable to open Si/config.txt` fatal error should not appear in
  corrected runs, because the scripts now create `Morris_N/Si/config.txt` and
  export `G4LATTICEDATA` per sample while keeping substrate name `Si`.
- The earlier `ProcMan201` event abort came from GeV phonon energies and should
  not appear in corrected runs, because gun energy is now sampled in eV.
- The current Morris design has `12928` samples and `100` expanded variables:
  `128 * (100 + 1) = 12928`.
- Hit files can be small, so downstream analysis should verify whether they
  contain useful hits rather than assuming file presence means physics content.
