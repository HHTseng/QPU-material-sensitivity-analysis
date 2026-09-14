# Material scan

`material_scan` is the tested replacement interface for the material-parameter
experiments. It currently validates and freezes experiments, reproduces the
legacy QP analysis, records run state safely, and inventories or archives old
data. The original `parameter_optimization/` tree remains the scientific
reference during this transition.

## How it works now

1. `parameters.yaml` defines every parameter's unit, type, physical range, and
   simulator target.
2. One file in `experiments/` fixes the scientific controls, recorded sampling
   design, fidelity, objective, search settings, and build identity.
3. `check` validates that file. `freeze` resolves all candidate values and
   expands every site, replica, seed, and event count into one immutable JSON
   manifest. A resume must read that manifest; it cannot redraw the experiment.
4. Each task has a physics-based identity. The analysis and optimizer histories
   have separate identities, so changing a report does not pretend the
   simulation changed.
5. The runner requires both a valid hit file and a post-`beamOn` witness, then
   publishes the file atomically. A zero-byte, malformed, missing, or partial
   output is not a zero-hit observation.
6. The SQLite store has one controller writer, explicit schema creation, a
   renewable lease, and per-attempt tokens. A late worker cannot overwrite a
   retry.
7. The scorer produces per-electrode QPs. The analysis requires the complete
   declared site-by-replica set and computes the recorded area-weighted
   stratified objective, uncertainty, spatial `R0.95`, and paired differences.

The implementation is intentionally fail-closed. Material/macro/lattice
rendering has not yet been ported and proven against the old runner, so the
production `run` command refuses to launch Geant4. This branch therefore does
not create a new physics result or silently reuse a legacy trial.

## Commands

Run these from the repository root with the G4CMP Python environment:

```bash
python -m material_scan check material_scan/experiments/spatial-strata-512-baseline.yaml
python -m material_scan freeze EXPERIMENT.yaml --values VALUES.yaml --output resolved.json
python -m material_scan legacy-summary parameter_optimization/stage4_trials.sqlite
python -m material_scan inventory PATH --output inventory.sqlite
python -m material_scan archive ROOT --member RELATIVE_PATH --output bundle.tar.gz
python -m material_scan verify-archive bundle.tar.gz --sha256 EXPECTED_SHA256
python -m material_scan recover bundle.tar.gz --output EMPTY_DIRECTORY
python -m material_scan store-doctor ledger.sqlite
python -m unittest discover -s material_scan/tests -v
```

`freeze` accepts candidate values only through one JSON/YAML mapping; it has no
scientific scalar overrides. `legacy-summary` opens the old ledger read-only.
Archive creation is explicit and does not remove its source. Do not remove or
untrack raw data until two copies have been verified on independent filesystems.

## Comparison with the old code

The compatibility tests use checksummed extracts of completed legacy output,
without importing the legacy scorer as a second implementation:

- 32 real hit files reproduce exactly 113 hits, 672 QPs, all 17 electrode
  totals, the full time-bin matrix, and `1.68e-4` QPs per primary.
- Header-only completed files remain measured physical zeros. Missing,
  zero-byte, malformed, duplicate, incomplete, and wrong-design inputs fail.
- The QP-density ODE, numerical tie rules, recorded site design, task seeds,
  optimizer replay, runner restart states, store leases, and archive recovery
  have independent regression tests.

The corrected stratified anchors also reproduce exactly (objective units are
area-weighted junction QPs per injected eV):

| Sites | Baseline | SiC | Paired SiC − baseline | Relative change |
|---:|---:|---:|---:|---:|
| 128 | 2.42916e-3 | 1.08713e-3 | -1.34202e-3 | -55.25% |
| 512 | 2.41552e-3 | 1.25609e-3 | -1.15943e-3 | -48.00% |

Their recorded standard errors and spatial `R0.95` values also match to
floating-point tolerance. These are compatibility anchors, not a new claim
that the 128-site design was converged.

`spatial-strata-512` is complete; it finished on 2026-09-09 at 08:05. The two
unfinished ledger records were deleted by the user. Any surviving orphan
directories are retained as evidence but are not valid cache entries.

Verification on 2026-09-14: 62/62 revised tests, 190/190 legacy gates, and
15/15 Stage-4 audit checks pass. The legacy ledger checksum remained
`603488162f459432b701e8d41acc9614d0f0535d404d53dc90f53cdf6c31d18c`
before and after the checks.

## What remains before a live comparison

- Port and compare material realization, including carrier and density rules.
- Render one macro and lattice with both paths and require normalized byte
  parity for every physics command.
- Verify the complete executable, loaded libraries, data, and analysis
  environment in a launchable manifest.
- Run one disposable low-event baseline smoke with matched sites and seeds;
  compare artifact validity, hit counts, QPs, and objective before enabling
  production runs.

Module responsibilities and migration rules are in the
[`REPOSITORY_RESTRUCTURING_BLUEPRINT.md`](../parameter_optimization/REPOSITORY_RESTRUCTURING_BLUEPRINT.md).
Current experiment names and legacy mappings are in
[`docs/experiments.md`](docs/experiments.md); data and recovery rules are in
[`docs/data.md`](docs/data.md).
