# Data and recovery

Source code and small immutable test fixtures live in Git. Active ledgers,
scratch files, logs, generated plots, and raw archives live below a configured
data root such as `material_scan_data/`.

One experiment has one immutable `resolved_experiment.json`, one SQLite ledger,
and short attempt paths. The database is written only by the controller. A
worker receives a resolved task and returns files plus its attempt token. A
stale token cannot commit over a retry.

Historical import is read-only. Absolute paths are mapped relative to the
recorded old root, never changed by text substitution in a ledger. The four
original databases are stored once per snapshot; experiment manifests refer to
their checksum and row IDs.

Archiving is explicit and requires a stopped experiment. Every member has its
old relative path, size, and SHA-256 in the manifest. Verification extracts to
a temporary directory and rejects missing, extra, duplicate, absolute, `..`,
symlink, truncated, or checksum-mismatched members. Do not remove or untrack a
source until two copies have been verified on independent filesystems.

Known legacy conditions that an importer must preserve:

- 32 foreign-key violations belong to successful subruns whose parent
  `stage4_pilot_1e56d6cc8745` row is absent.
- `strat512` directories ending `2ffd9440d59d` and `df6e253d7c23` remain on
  disk after their incomplete rows were deleted. They are orphan evidence, not
  cacheable results.
- two successful Stage-3 trees ending `4bba865c33c9` and `c156fc847c51` are
  absent from the work tree; tracked blobs are recoverable from Git, while
  untracked logs are expected missing.
- many legacy paths contain the old absolute repository root, and many older
  rows lack enough executable/library provenance for automatic cache reuse.
