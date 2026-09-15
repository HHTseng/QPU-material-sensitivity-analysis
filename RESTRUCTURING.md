# Repository restructuring — 2026-09-14 / 15

Two phases. Phase 1 moved the code out; phase 2 renamed the evidence directory
and made the ledger paths portable.

Branch: `Material_optimization_v3_scan_parameters_revised`

Flattens the tree to **three top-level directories with one job each**, moves all
code out of `parameter_optimization/`, and leaves the experimental evidence
exactly where it is. No raw data, ledger, or result file was deleted or moved.

## Before and after

```
BEFORE                                  AFTER
.                                       .
├── stage1_run_simulations.py           ├── material_scan/      current search
├── stage2_compute_QPs.py               ├── legacy/             retained runtime
├── stage2_..._sensitivity_analysis.py  └── parameter_optimization/   evidence only
├── sensitivity_params.py
├── sensitivity_utils.py                    (14 loose root files -> 0)
├── sensitivity_memguard.py
├── plot_detector_layout.py
├── sensitivity_template_*.mac  (6)
├── run_2stage_1e5.sh
├── SensitivityAnalysis_Paul.ipynb
├── docs/sensitivity-analysis.md
├── parameter_optimization/   CODE + DATA mixed
└── material_scan/
```

Root went from 23 tracked entries to 6: `README.md`, `RESTRUCTURING.md`,
`pyproject.toml`, `.gitignore`, and the three directories (plus the reference
PDF and detector figure).

## What moved

| From | To | Count |
|---|---|---|
| `parameter_optimization/*.py` | `legacy/` | 26 |
| `parameter_optimization/*.{yaml,txt}` | `legacy/` | 6 |
| `parameter_optimization/*.sh` | `legacy/scripts/` | 10 |
| `parameter_optimization/catalog/*.json` | `legacy/catalog/` | 2 |
| root `stage1/stage2/sensitivity_*.py`, `plot_detector_layout.py` | `legacy/` | 7 |
| root `sensitivity_template_*.mac` | `legacy/macros/` | 6 |
| root `run_2stage_1e5.sh` | `legacy/scripts/` | 1 |
| root `SensitivityAnalysis_Paul.ipynb` | `legacy/` | 1 |
| `docs/sensitivity-analysis.md` | `legacy/sensitivity-analysis.md` | 1 |

All moves used `git mv`, so file history is preserved.

## Why `legacy/` is one flat directory

The Stage 1–4 modules import each other by **bare module name**
(`from stage4_space import ...`, `import stage1_run_simulations as harness`).
Before this change they were split across the repository root and
`parameter_optimization/`, so the runtime needed *two* `sys.path` entries and
imports only resolved from particular working directories.

Putting them in one directory is both flatter and strictly more robust: one
`sys.path` entry now satisfies every bare-name import. The Morris analysis is
not separable from the scan code — `stage4_space` imports
`stage1_run_simulations` — so they belong in the same directory.

## What was removed

| Removed | Reason |
|---|---|
| `docs/` | emptied by the move; its one file now sits with the code it documents |
| `material_scan/schemas/` | empty directory, no references |
| `material_scan/docs/history/` | empty directory |
| `parameter_optimization/catalog/` | emptied by the move |
| all `__pycache__/` | regenerable, untracked |

**No source file was deleted.** The import graph showed that every module with
no importer is a command-line entry point (`stage4_report.py`,
`analyze_site_convergence.py`, and similar), not dead code. Two candidates were
deliberately kept:

- `stage2_compute_QPs_sensitivity_analysis.py` — the blueprint proposed deleting
  it as duplication, but `legacy/sensitivity-analysis.md` documents it as step 3
  of the Morris workflow. It stays until Morris is formally retired.
- `run_2stage_1e5.sh` — a working driver for that workflow, not a relic.

## Phase 1 limitation (lifted in phase 2)

At the end of phase 1 `parameter_optimization/` kept its name, because two
constraints pinned it:

1. **677 absolute ledger paths.** Every `run_dir` value in
   `stage4_trials.sqlite` is absolute and contains
   `.../parameter_optimization/runs/`. Renaming the directory silently breaks
   every historical run lookup; the only repair is rewriting a ledger that must
   stay read-only.
2. **An open gate.** `material_scan/docs/issues.yaml` carries
   `second-independent-copy` at severity `must-be-resolved-before-deletion`:
   *"Do not untrack, move, or delete historical raw artifacts."*

Phase 2 below performs exactly that deliberate migration.

## References repaired

The move broke real things; each was found by the test suites, not by guesswork.

| File | Change |
|---|---|
| `legacy/experiment_definition.py` | `CODE_IDENTITY_FILES` — all 12 identity paths repointed. `code_fingerprint()` **raises** on a missing required file, so this was load-bearing. |
| `legacy/tests_stage4.py` | added `DATA_ROOT`; repointed ledgers, `results/`, macro template, and the two runbook scripts |
| `legacy/stage4_invalidations.yaml` | `accepted_changed_identity_files` — a **live** audit comparison, not a historical record |
| `legacy/stage4_audit.py` | module rename map |
| `legacy/{stage3_trial_runner,stage4_optimize,stage4_probe_g4_density,stage1_run_simulations}.py` | default macro template now `legacy/macros/` |
| `legacy/scripts/finish_property_event_comparison.sh` | anchors at `legacy/` for code, `$DATA` for evidence |
| `material_scan/search.py` | `sys.path` insert `parameter_optimization` → `legacy` |
| `material_scan/docs/issues.yaml` | invalidations path → `../../legacy/` |
| `material_scan/README.md`, `README.md`, `.gitignore` | layout and ignore rules |

Left unchanged in phase 1: `material_scan/tools/hit_data.py` and
`material_scan/experiments/spatial-strata-512-baseline.yaml` pointed at the
evidence directory for **data**, which was correct; phase 2 repointed them.

## Verification

| Check | Before | After |
|---|---|---|
| `legacy/tests_stage4.py` | 194/194 | **194/194** |
| `material_scan/tests` (6 modules, 62 tests) | all OK | **all OK** |
| `python -m material_scan --help` | works | works |
| `code_fingerprint()` | 13 files, none missing | 13 files, none missing |

The gate count is the reason this record can be trusted. An intermediate state
passed **192/192** — green, but two gates had silently stopped running because
their data paths still pointed at the code directory. `T15h2` (the 637-trial
cache check) was guarded by `os.path.isfile`, and `T27h` vanished through a bare
`continue` with no check emitted at all. Both are exactly the failure the
blueprint warns about: *"a parity test may not silently skip because a fixture
is missing."* Restoring the full 194 required chasing the count back, not just
seeing green.

## Not addressed here

- The 194 uncommitted deletions under `parameter_optimization/runs/` predate this
  work and were left untouched; they are still recoverable with `git restore`.
- `parameter_optimization/results/stage4_report.json` still holds uncommitted
  benchmark results.
- Nothing in this change alters physics, cache identity contents, or any result.


---

# Phase 2 — `data/` rename and repository-relative paths (2026-09-15)

Lifts the phase 1 limitation. `parameter_optimization/` is now `data/`, and
ledger artifact paths are repository-relative instead of absolute.

## The gate

`second-independent-copy` in `material_scan/docs/issues.yaml` was suspended once,
on explicit instruction, and restored immediately afterwards. Its open rules are
now byte-identical to their pre-migration text. **Deletion stayed forbidden
throughout**: this migration renamed and rewrote, and deleted nothing.

Before touching anything, all four live ledgers were snapshotted with SQLite's
backup API into `data/premigration_backup_20260914/` (untracked).

## Scale

The pin was far larger than the 677 `run_dir` values phase 1 identified. Every
sub-run carried three more absolute paths:

| Ledger | run_dir | macro | hits_file | done_marker |
|---|---:|---:|---:|---:|
| `stage4_trials.sqlite` | 677 | 134,048 | 134,048 | 134,048 |
| `stage3_trials.sqlite` | 20 | 640 | 640 | 640 |
| `stage3_trials_e7.sqlite` | 18 | 576 | 576 | 576 |
| `stage3_trials_e8.sqlite` | 6 | 192 | 192 | 192 |

**407,089 stored paths rewritten**, verified at zero absolute remaining.

## Design: relative on disk, absolute in memory

Paths flow through the codebase as plain strings handed to `open()` and
`os.path.exists()`, so changing their meaning everywhere would have been a wide,
risky edit. Instead the conversion happens at one boundary in
`legacy/experiment_database.py`:

- `resolve_artifact_path()` — relative → absolute, applied on read
- `relativize_artifact_path()` — absolute → relative, applied on write

Every caller still sees absolute paths, so no consumer logic changed. Values
already absolute pass through untouched, so a part-migrated ledger still reads.

A global `row_factory` returning dicts would have been the tidier choke point,
but `has_column()` does positional `row[1]` access on PRAGMA results over the
same connection, so resolution is applied in the five accessors that return
path-bearing rows instead.

## Changed

| File | Change |
|---|---|
| `legacy/experiment_database.py` | resolve/relativize helpers; resolution in `find_by_cache_key`, `sub_runs`, `incomplete_sub_runs`, `observations`, `controls`; relativization in `plan_trial` |
| `legacy/migrate_ledger_paths.py` | **new** — idempotent migration tool with `--check` |
| `legacy/stage4_audit.py` | resolves `run_dir` before touching the filesystem (raw connection) |
| `legacy/tests_stage4.py` | `DATA_ROOT` → `data` |
| `legacy/scripts/finish_property_event_comparison.sh` | `$DATA` → `../data` |
| `material_scan/tools/hit_data.py` | `ROOT` → `data`; relative paths resolve against the repo root, not the CWD |
| `material_scan/experiments/spatial-strata-512-baseline.yaml` | design and historical-result paths |
| `README.md`, `data/README.md` | layout and the relative-path contract |

`legacy/stage3_report.py` needed no change: it reads through `observations()`,
which now resolves.

## Not rewritten, deliberately

`*.sqlite.bak-*` and `snapshots/` still hold absolute paths. They are byte-exact
records of a past state, and `snapshots/` carries its own `SHA256SUMS` that an
in-place edit would invalidate. Restoring one requires running
`legacy/migrate_ledger_paths.py` on it afterwards — recorded in `data/README.md`.

## Verification

| Check | Result |
|---|---|
| absolute paths left in live ledgers | **0** of 407,089 |
| resolved `run_dir` / `hits_file` sampled | **240/240 exist on disk** |
| `hit_data` sub-run index | 135,424 indexed, 134,619 present |
| missing files | 805 — all `planned` (797) or `timeout` (8); **no successful sub-run lost** |
| `legacy/tests_stage4.py` | **194/194** |
| `material_scan/tests` | **6/6 modules OK** |
| `material_scan check` on the 512-site experiment | 4,096 tasks resolved |

The 194 tracked deletions under `runs/` that predated this work were restored
with `git restore` — `git mv` refuses to move tracked files missing from disk,
and deleting them was forbidden, so restoring was both required and correct.

## A pre-existing flaky gate, fixed

`T26d reconcile is idempotent` failed intermittently during this work. It was
**not** caused by the migration: `git diff` shows `stage4_reconcile.py` moved
with 0 insertions and 0 deletions, and HEAD carries the identical defect.

The cause is real rather than environmental. `stage4_reconcile.py` rewrote its
invalidation notice unconditionally, including
`"labelled_at": time.strftime("%F %T")` at second resolution. T26d runs reconcile
twice and compares the output, so it passed only when both runs landed inside the
same wall-clock second — roughly a coin flip.

The gate asserted a property the code did not have, so the fix is in the code,
not the test: the previous `labelled_at` is preserved when nothing else about the
notice changed, and a genuine change still re-stamps. Reconcile is now actually
idempotent, and the suite passed 194/194 three times consecutively.
