# Experimental evidence

This directory holds **only data**: campaign ledgers, raw run artifacts, result
summaries, snapshots, and logs. All source code lives in `legacy/` and
`material_scan/`. See `RESTRUCTURING.md` for the 2026-09-14/15 reorganization.

Renamed from `parameter_optimization/` on 2026-09-15.

## Paths are repository-relative

Ledgers store `run_dir`, `macro`, `hits_file` and `done_marker` as paths
relative to the repository root, for example:

```
data/runs/stage4_pilot/stage4_pilot_f5e3faeba22e/hits/..._r0_p0_hitsfile.txt
```

They were absolute before 2026-09-15, which pinned this directory's name and
made a checkout non-portable. `legacy/experiment_database.py` resolves them to
absolute paths on read and relativizes them on write, so callers still see
absolute paths and nothing downstream had to change. Values that are already
absolute are passed through untouched, so a part-migrated ledger still reads.

Use `legacy/migrate_ledger_paths.py` to convert a ledger; it is idempotent and
supports `--check` for a dry run.

## Contents

| Path | What it is |
|---|---|
| `runs/` | 8.4 GB of raw per-sub-run macros, hit files, and markers |
| `stage3_trials*.sqlite`, `stage4_trials.sqlite` | campaign ledgers (read-only; migrated) |
| `*.sqlite.bak-*` | pre-migration ledger backups — **still hold absolute paths** |
| `premigration_backup_20260914/` | untracked safety copies taken before the path rewrite |
| `results/` | published result JSON and CSV summaries |
| `snapshots/` | verified pre-migration snapshot with `SHA256SUMS` |
| `logs_stage4/`, `logs_beamon/` | campaign logs |
| `presentation/` | Stage 3 factorial presentation and assets |
| `hit_diagnostics_*/` | generated diagnostic plots (untracked, regenerable) |

## Restoring a backup

The `.bak-*` files and `snapshots/` were deliberately **not** rewritten: they are
byte-exact records of a past state, and `snapshots/` carries its own
`SHA256SUMS` that an in-place edit would invalidate. A restored backup therefore
still contains absolute `.../parameter_optimization/...` paths. Run
`legacy/migrate_ledger_paths.py` on it after restoring.

## Still in force

`material_scan/docs/issues.yaml` keeps `second-independent-copy` open:
*"Do not untrack, move, or delete historical raw artifacts."* The rename above
was an explicitly authorized one-time exception that deleted nothing; the rule
is back in force.
