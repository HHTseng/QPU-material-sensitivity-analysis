# Result files and recovery

Source code, experiment definitions, small checking fixtures, and the few
figures cited directly by the current report belong in Git. Raw hit files, task
logs, complete generated figure collections, SQLite result files, and
compressed copies belong under a configured data root outside the source tree.

Current commands use `python scan.py` from the repository root. Historical
source is retained in Git at `c8cdb996209711bef0e266c7315bfa518eda47e8`;
Earlier measurements are archived outside the working tree; the fresh execution
queue uses new output directories and does not reuse those measurements.
The two test files and their small checking data are under `tests/` and
`tests/fixtures/` at the repository root.

## New directory names

```text
material_scan_data/
  fresh/
    <study-name>/
      resolved.json
      build.json
      results.sqlite
      tasks/
      summary.json
  ollama/
    models/

<archive-directory>/
  old-runs.tar.gz
  manifest.jsonl
  SHA256SUMS
```

Names state the scientific purpose, for example `orientation-pilot`,
`spatial-strata-512`, or `optimizer-comparison-seed-02`. They do not contain a
development stage number or an informal event-count class. Every report states
the exact total primaries, primaries per task, number of sites, and replicas.
Study YAML files remain under `studies/`; `resolved.json` records the resolved
study beside its measurements. Analysis and figure commands write to their
explicit output paths.

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

Earlier numbered-stage data is archived. Recover a stopped copy before reading
its databases. Its original relative path, row
identifier, validity state, size, and SHA-256 are recorded before copying.
Absolute paths are mapped relative to their recorded root; they are never
changed by blind text replacement.

Known historical conditions are:

- 32 foreign-key violations belong to successful tasks whose parent pilot row
  is absent.
- two obsolete 512-site attempt directories were retained after their unfinished
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

Recover into a new empty destination; never unpack over an active experiment.
The reset of historical output is recorded separately from the fresh results.

```bash
python scan.py archive material_scan_data/fresh/reference-eight \
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

The retained `studies/designs/stratified_128.json`, `stratified_512.json`, and
`stratified_2361.json` contain only injection coordinates, region labels, area
weights, geometry and sampling metadata. Their recorded design hashes are
unchanged. They contain no old response values. The 2,361-site design keeps the
512-site prefix; the separate 128-site design has its own region allocation.
The fresh queue can generate a new common nested design and unused random
streams. Material records and raw measured-property tables remain under
`legacy/`; small numerical regression fixtures remain under `tests/fixtures/`.
