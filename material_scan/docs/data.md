# Result files and recovery

Source code, experiment definitions, small checking fixtures, and the few
figures cited directly by the current report belong in Git. Raw hit files, task
logs, complete generated figure collections, SQLite result files, and
compressed copies belong under a configured data root outside the source tree.

Current commands use `python scan.py` from the repository root. Historical
source is retained in Git at `c8cdb996209711bef0e266c7315bfa518eda47e8`;
its removal from the current source tree does not remove experimental data.

## New directory names

```text
material_scan_data/
  experiments/
    <scientific-name>/
      experiment.yaml
      resolved.json
      results.sqlite
      tasks/
      reports/
      plots/
  history/
    <date>-<scientific-name>/
      inventory.sqlite
      files.tar.gz
      files.tar.gz.sha256
```

Names state the scientific purpose, for example `orientation-pilot`,
`spatial-strata-512`, or `optimizer-comparison-seed-02`. They do not contain a
development stage number or an informal event-count class. Every report states
the exact total primaries, primaries per task, number of sites, and replicas.

## Write rules

- One experiment has one `resolved.json` and one SQLite result file.
- Only the controller writes SQLite. Workers return an attempt token and files.
- A late result cannot replace a newer attempt.
- A worker writes in a temporary directory. The controller checks the hit file
  and completion witness, calculates checksums, and then renames the directory
  atomically.
- Missing tasks, malformed files, numerical failure, a physical zero, and a
  completed measurement are distinct states.
- Opening an existing SQLite file never changes its schema. Schema changes use
  a stopped copy and an explicit conversion command.

## Earlier results

Earlier numbered-stage data is read-only. Its original relative path, row
identifier, validity state, size, and SHA-256 are recorded before copying.
Absolute paths are mapped relative to their recorded root; they are never
changed by blind text replacement.

Known retained conditions are:

- 32 foreign-key violations belong to successful tasks whose parent pilot row
  is absent.
- two obsolete 512-site attempt directories remain after their unfinished
  database rows were removed; they are diagnostic evidence, not reusable
  measurements.
- two successful linked-material trees ending `4bba865c33c9` and
  `c156fc847c51` are absent from the work tree; their tracked files remain in
  Git history.
- older records do not always identify every executable, library, and external
  data file, so they are comparison evidence rather than automatic inputs to a
  new simulation.

## Copy and recovery rules

`python scan.py archive` creates a deterministic compressed copy and
does not remove the source. Its inventory records every relative path, size,
and SHA-256. Verification rejects missing, extra, duplicate, absolute,
parent-traversal, symbolic-link, truncated, or checksum-mismatched entries.

Do not remove a source until two independently stored copies have both passed
verification. Recover into a new empty destination; never unpack over an active
experiment.

```bash
python scan.py archive material_scan_data/experiments/reference-eight \
  --output /tmp/reference-eight.tar.gz
python scan.py verify-archive /tmp/reference-eight.tar.gz
python scan.py recover /tmp/reference-eight.tar.gz --output /tmp/recovered
python scan.py analyse /tmp/recovered --output /tmp/reanalysis.json
```

Recovered analysis and figures bind recorded file paths to the recovered
directory without modifying the original database. `history` reads an older
SQLite file without schema changes; supply
`--invalidations legacy/stage4_invalidations.yaml` for the invalidity register.
`convert-copy` changes paths only in a stopped copy and keeps the source intact.
