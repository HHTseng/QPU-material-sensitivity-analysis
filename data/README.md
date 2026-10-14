# Preserved experimental evidence

This directory contains historical SQLite results, raw simulations, summaries,
snapshots, figures, and logs. Its name changed from `parameter_optimization/`
on 2026-09-15. The 2026-10-14 code restructure does not alter these measurements.
The earlier presentation generator and simulation source are available at
Git revision `c8cdb996209711bef0e266c7315bfa518eda47e8`.

Current execution uses the five Python scripts at the repository root.
New output belongs under `material_scan_data/`. See
[recovery instructions](../material_scan/docs/data.md) and
[numerical verification](../verification/round2_results.json).

| Path | Contents |
|---|---|
| `runs/` | Historical macros, hit files and completion markers |
| `stage3_trials*.sqlite`, `stage4_trials.sqlite` | Historical result databases |
| `*.sqlite.bak-*` | Earlier database copies; some retain absolute paths |
| `premigration_backup_20260914/` | Local copies made before path conversion |
| `results/` | Recorded JSON and CSV summaries |
| `snapshots/` | Pre-migration snapshot with `SHA256SUMS` |
| `logs_stage4/`, `logs_beamon/` | Historical execution logs |
| `presentation/` | Earlier presentation and assets |

`python scan.py history DATABASE --invalidations legacy/stage4_invalidations.yaml`
reads historical records without changing them. Unlisted old trials remain
unchecked; they do not automatically become reusable new measurements.

Database copies and snapshots preserve their original bytes. Use
`python scan.py convert-copy SOURCE --output NEW_COPY --old-root OLD --new-root NEW`
for explicit path conversion of a stopped copy. Never rewrite the original
snapshot or its checksums. Before any future deletion of raw measurements,
verify two independently stored copies as required by
`material_scan/docs/issues.yaml`.
