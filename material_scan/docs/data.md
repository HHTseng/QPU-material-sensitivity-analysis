# Result files and recovery

Source code, experiment definitions, small checking fixtures, and the few
figures cited directly by the current report belong in Git. Raw hit files, task
logs, complete generated figure collections, SQLite result files, and
compressed copies belong under a configured data root outside the source tree.

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

`python -m material_scan archive` creates a deterministic compressed copy and
does not remove the source. Its inventory records every relative path, size,
and SHA-256. Verification rejects missing, extra, duplicate, absolute,
parent-traversal, symbolic-link, truncated, or checksum-mismatched entries.

Do not remove a source until two independently stored copies have both passed
verification. Recover into a new empty destination; never unpack over an active
experiment.
