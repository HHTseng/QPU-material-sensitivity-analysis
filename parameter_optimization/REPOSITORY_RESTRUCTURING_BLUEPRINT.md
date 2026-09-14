# Material-scan repository restructuring blueprint

**Verified implementation plan.** The legacy tree remains the reference until
the parity gates in this document pass. New code is introduced beside it; this
document does not authorize rewriting a legacy ledger or deleting raw data.

- Source branch reviewed: `Material_optimization_v3_scan_parameters`
- Implementation branch: `Material_optimization_v3_scan_parameters_revised`
- Repository snapshot reviewed: 2026-09-14

## 1. Decision in one page

The scientific work should be kept. The current arrangement around it should
be replaced.

The clean target is:

1. one frozen experiment file;
2. one command-line program;
3. one simulation path;
4. one ledger per experiment;
5. one explicit objective calculation;
6. one archive location;
7. short names based on scientific purpose, not development stage;
8. old results preserved byte-for-byte, with checksums and a path map.

The refactor must not alter the physics while it changes the structure. In
particular, it must preserve material realization, the exact Geant4/G4CMP
commands, sites, strata, weights, seeds, event counts, completeness rules,
per-electrode counts, QP scoring, and validity labels.

The recommended active code is one shallow Python package under a directory
named `material_scan/`, split only at the boundaries listed in Section 5.
Campaign shell scripts, numbered `stage3_*` and
`stage4_*` modules, `.done` files, and separate `runs/`, `results/`, and log
trees disappear from the active design. Historical files remain available in
an immutable archive and in Git history.

The earlier `strat512` warning is resolved: that experiment finished on
2026-09-09 at 08:05, its two unfinished records were deleted by the user, the
legacy ledger now has no `running` row, and no matching process was found at
the implementation checkpoint. The work tree still contains user changes;
they are preserved as input state and are not silently folded into this
refactor. The current result JSON still has an incomplete `bo_gp_corner`
placeholder without a trial ID; the compatibility view preserves it as a
closed incomplete report entry, not as a running or successful trial.

## 2. What was reviewed

The review covered the active and historical Python, shell, YAML, text, and
Markdown files outside generated run directories. It also traced the four
SQLite ledgers, the current result directories, the macro/lattice generation
path, the optimizer ask/tell path, confirmation and projection paths, the
uniform and stratified site designs, scoring, audits, and report generation.

Measured repository state at the review snapshot:

| Item | Size or count | Meaning |
|---|---:|---|
| Tracked files | 4,412 | The Git history is dominated by experiment artifacts. |
| Tracked files under `parameter_optimization/runs/` | 4,268 | 96.7% of all tracked files. |
| Files currently under `parameter_optimization/runs/` | 563,615 | Per-sub-run macros, hit files, markers, logs, and copies cause the explosion. |
| Size of `parameter_optimization/runs/` | 8.2 GB | This should be experiment data, not source code. |
| Python source and scripts | 39 files, 19,711 lines | Several modules repeat orchestration or mix unrelated jobs. |
| Shell orchestration | 11 files, 1,111 lines | Campaign state and ordering are spread across filenames and shell conditions. |
| Main Markdown records | 18 files | Current conclusions, plans, corrections, and history overlap. |
| Stage 3 ledgers | 3 | Separate files encode fidelity in misleading names. |
| Stage 4 ledger | 1 | It contains many unrelated campaigns and has had live schema changes. |

These counts are an inventory, not a deletion list.

## 3. Main problems, in fix order

### R0 — Do not lose or silently reinterpret experimental evidence

The repository contains valid, invalidated, superseded, incomplete, and live
results. Their status is scientifically meaningful. A cleanup that merely
moves files can accidentally break ledger paths, drop invalidation labels, or
make a partial run look complete.

**Fix:** make a checksum inventory first. Copy before moving. Keep old ledgers
read-only. Every old path gets one new archive ID and retains its original
validity state. Nothing is deleted until two verified archive copies exist.

### R1 — There are several competing sources of truth

Parameter meaning is split among `parameter_set.txt`, `stage3_config.yaml`,
`stage4_config.yaml`, `stage4_space.py`, the material YAML files, command-line
overrides, and long comments. Bounds and defaults can be declared in both YAML
and Python. This is the same class of problem that allowed the 1 meV/10 meV
energy drift.

**Fix:** use three explicit layers. `parameters.yaml` owns each parameter's
name, unit, type, physical validity rules, and projection target.
An experiment file owns the search bounds, scale, prior, default, and joint
constraints for that study. The frozen candidate/material snapshot owns the
actual value sent to the simulator. `materials.yaml` keeps simulation material
records distinct from fabrication-projection records when their conventions
or evidence differ; the selected record is expanded into the frozen snapshot.
Unknown, missing, unitless, non-finite, or out-of-domain fields fail before any
run is planned. `parameter_set.txt` becomes generated documentation and is
never read by the program.

### R2 — The code is divided by project history rather than responsibility

`stage3_contract.py`, `stage3_ledger.py`, and the 1,430-line
`stage3_trial_runner.py` are the shared runtime for stage 4. Conversely,
stage-4 modules repeat contract mutation, ledger setup, execution, and output
writing. A filename no longer tells a reader what the code does.

**Fix:** replace `stage3_*` and `stage4_*` runtime names with the small
purpose-based modules described in Section 5. There is one evaluation function used by
factorials, searches, confirmations, projections, tolerance scans, and site
studies.

### R3 — A launched experiment is not a single immutable object

`stage4_optimize.py`, `stage4_confirm.py`, `stage4_pilot.py`, and
`stage4_tolerance.py` can alter campaign IDs, sites, replicas, events,
objectives, and resources after loading YAML. Important meaning therefore lives
in a command line or shell script rather than in the saved experiment.

**Fix:** validate and freeze a complete experiment file before planning rows.
Physics, design, objective, and optimizer fields cannot be overridden on the
command line. Only non-scientific launch resources, such as worker count, may
be supplied separately, and those are saved in `launch.json`.

### R4 — Hidden state couples the sampling design to the objective

The stratified objective reads module-global `_ACTIVE_DESIGN`, set through
`set_stratified_design()`. A test, thread, or later run can inherit the wrong
design without showing it in a function call.

**Fix:** pass an immutable experiment context to every objective. The objective
receives blocks, sites, strata, weights, event energy, and electrode geometry
explicitly. No module-level mutable state is allowed.

### R5 — Every workflow has its own driver

Optimization, pilot, confirmation, tolerance, projection verification,
warm-start building, XL assembly, reconciliation, and convergence checks have
separate entry points. Eleven shell scripts then compose those entry points by
testing for particular filenames. This is difficult to resume and easy to run
out of order.

**Fix:** one `scan.py` program with small subcommands. A saved experiment plan
declares dependencies. The ledger, not the presence of a result filename,
decides what is complete.

### R6 — The artifact layout multiplies files

Each sub-run repeats a long trial ID in macro, hit, log, and `.done` filenames.
A `.done` marker doubles the important file count and is only an indirect proof
that the hit file is valid. Active output, final results, logs, and plots are
spread across unrelated roots.

**Fix:** retain an attempt-local transient completion witness because Geant4
can exit zero without executing `/run/beamOn`. Write each attempt in a temporary
work directory, require the expected witness and a valid hit file, checksum the
output, then atomically rename it from `.partial` to its final name and commit
the matching attempt token in the ledger. The witness is not a permanent data
file after that commit. Package raw files only through an explicit, quiescent
archive command; keep running and failed attempts unpacked for diagnosis.

### R7 — Cache identity is tied to incidental source edits

The current code fingerprint hashes a list of Python files. A comment, audit
guard, or restructuring edit can make scientifically identical trials
unreachable, even when the generated lattice and macro inputs are identical.

**Fix:** separate the identity of the simulation from the identity of the
analysis and source tree. Hash the normalized physical input, generated
simulation commands, seeds, executable, and required external data. Record the
Git revision for audit, but do not make unrelated source text part of the
simulation key.

### R8 — The ledger has too many writers and migration can happen implicitly

Thread-local connections, leases, heartbeats, freeze files, and opening-time
schema changes grew in response to real failures. The protections are valuable,
but their present form is hard to reason about.

**Fix:** one controller owns the writable SQLite connection and an expiring
database lease. Workers run Geant4 and return messages; they never write the
ledger. Every launch gets an attempt token and every state change uses a
compare-and-swap condition, so a stale worker cannot publish over a retry.
Durable attempt directories allow controller-crash reconciliation. Schema
changes occur only through an explicit `migrate` command against a stopped
experiment. Existing strict planning, heartbeat, failure, and completeness
states remain.

### R9 — Simulation, scoring, and reporting are mixed or duplicated

The QP functions in `stage2_compute_QPs.py` are repeated in
`stage2_compute_QPs_sensitivity_analysis.py`. The trial runner resolves
materials, writes inputs, controls processes, parses hits, computes QPs, and
updates storage. Several report programs recalculate related summaries.

**Fix:** one physics implementation computes derived values and QPs. Raw hit
files are parsed once per analysis version; compact per-task summaries are
stored. Objectives and plots consume those summaries without rereading every
large hit file.

### R10 — The optimizer and optional LLM add a large core surface

The handwritten GP, expected improvement, CMA-ES, registries, adapters, and LLM
client occupy roughly 1,500 lines before the driver. They are useful experiments
but should not be imported by every normal run.

**Fix:** retain random, Sobol, BO-GP, and CMA-ES behind one small `ask()` /
`tell()` interface. First wrap the current implementations for exact replay.
Only after parity tests should a pinned, well-tested library replace them. Move
the LLM proposer out of the core path; it remains an optional historical tool,
not an automatic import.

### R11 — Current facts and chronological history are mixed

The README and several large Markdown files repeat plans, corrections, current
claims, and old claims. Correct warnings are easy to miss because they sit next
to superseded instructions.

**Fix:** keep four short current documents. Move existing long records,
unchanged, into a dated history directory with an index and checksums.

### R12 — The executable environment is not fully reproducible from this repo

The ledger records the hash of
`/home/htseng/geant4_workdir/bin/Linux-g++/Main`, but the source/build recipe,
Geant4 version, G4CMP revision, compiler, flags, and patched material behavior
are not all contained in the experiment definition.

**Fix:** every frozen experiment includes a build and analysis-environment
manifest. At minimum it records SHA-256 for `Main`, `libMain.so`,
`libG4cmp.so`, the resolved dynamic dependencies and required external data;
Geant4/G4CMP revisions; compiler and flags; source revision; geometry and
template hashes; Python/package/BLAS versions; and local patch hashes. A run
fails if any required measured hash differs. Legacy rows with only a `Main`
hash remain readable but are not automatically reusable.

## 4. Rules the new design must obey

These are the permanent rules, not temporary migration details.

### Scientific rules

- A missing, failed, timed-out, killed, corrupt, or incomplete task is never a
  zero-QP observation.
- A trial is scored only when its entire declared task set is complete.
- The scorer requires the exact declared `(site, replica)` keys, every declared
  stratum, one consistent positive event count, and the declared electrode
  vector length and order. It never renormalizes a partial design.
- Site coordinates, stratum assignment, area weight, replica, seed, and event
  count are stored before launch.
- Stratified device estimates use `sum(weight[h] * mean[h])`; oversampling a
  stratum never changes its physical area weight.
- Candidate comparisons use matched sites and seed banks when the design says
  they are paired.
- Material realization is part of simulation identity. Carrier material,
  density, lattice record, and custom overrides cannot be discarded by a
  normalizer.
- Measured material values are not clipped to an optimization search box.
- Units are present in field names and validated at file load.
- Raw simulation output is retained so a revised objective can be calculated
  without rerunning Geant4 when the required observables already exist.
- Invalid and superseded results remain findable and clearly labelled. They are
  never silently removed from an average.
- Execution state, failure kind, data completeness, observation kind
  (`measured`, `physical-zero`, `right-censored`, or `non-observation`), and
  scientific validity are separate fields. One label never stands in for the
  others.

### Code rules

- No global mutable experiment state.
- No scientific command-line override.
- No implicit database migration.
- No success inferred solely from a filename.
- No resume regenerates sites, seeds, weights, material records, or candidate
  choices. It reads the immutable resolved manifest created before launch.
- No broad exception that converts an error into an empty result.
- No duplicated physics formula.
- No `stage`, `v1`, `new`, `final`, or `fixed` in a new active module name.
- Comments explain equations, units, or a non-obvious safety rule. Historical
  narrative belongs in `docs/history/`.
- Functions receive plain immutable data and return plain results. File writes
  occur only in the controller and storage boundary.
- A reader can trace any reported number to an experiment file, ledger rows,
  analysis version, and raw artifact checksums.

## 5. Proposed source tree

Rename `parameter_optimization/` to `material_scan/` on the future branch and
use this shallow structure:

```text
material_scan/
├── README.md
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── identity.py
├── physics.py
├── sampling.py
├── runner.py
├── store.py
├── search.py
├── analysis.py
├── legacy.py
├── archive.py
├── parameters.yaml
├── materials.yaml
├── experiments/
│   ├── index.yaml
│   ├── material-factorial.yaml
│   ├── property-search.yaml
│   └── spatial-convergence.yaml
├── tests/
│   ├── test_config.py
│   ├── test_physics.py
│   ├── test_sampling.py
│   ├── test_runner.py
│   ├── test_store.py
│   ├── test_search.py
│   ├── test_analysis.py
│   └── test_end_to_end.py
└── docs/
    ├── physics.md
    ├── experiments.md
    ├── data.md
    ├── issues.yaml
    └── history/
```

This is deliberately one package level, not a framework. The module count is
not a target: split a file only where data ownership or side effects change,
and merge tiny pass-through modules. The boundaries are:

| Module | Owns | Must not own |
|---|---|---|
| `cli.py`, `__main__.py` | command parsing and dependency order | physics equations, SQL, optimizer math |
| `config.py` | strict loading, units, immutable records, normalized hashes | process launch, reports |
| `identity.py` | canonical bytes and task/simulation/analysis/search keys | scientific defaults, cache policy |
| `physics.py` | material realization, sound speeds, interfaces, gates, hit-to-QP scoring | campaign state, filesystem layout |
| `sampling.py` | sites, strata, area weights, replicas, seeds, fidelity task plan | objectives, subprocesses |
| `runner.py` | macro/lattice rendering, Geant4 process, memory/stall checks, output validation | SQL and optimizer decisions |
| `store.py` | SQLite schema, controller lease, state changes, cache lookup | scientific formulas, tar handling |
| `search.py` | random, Sobol, BO-GP, CMA-ES through `ask`/`tell` | launching simulations |
| `analysis.py` | objectives, uncertainty, paired differences, convergence, projection, reports, plots | mutable run control |
| `legacy.py` | read-only legacy ledgers, paths and status translation | rewriting or repairing old evidence |
| `archive.py` | inventory and verified explicit bundles | trial scoring or automatic background cleanup |

If a module becomes large, split only at a real data boundary. Do not recreate
one file per experiment type.

## 6. One data flow for every experiment

Factorial, optimizer, confirmation, projection verification, tolerance, and
site convergence differ in how they choose candidates or designs. They should
not differ in how a candidate is simulated.

```python
def execute_experiment(experiment_file, launch_options):
    spec = load_and_validate(experiment_file, "parameters.yaml", "materials.yaml")
    build = verify_executable(spec.build)
    # Resolve every scientific input before the manifest becomes launchable.
    materials = resolve_selected_material_records(spec)
    design = load_or_build_sampling_design(spec.sampling)
    proposals = initialize_candidate_source(spec.search, spec.candidates)
    tasks = plan_declared_tasks(design, spec.fidelity)
    frozen = freeze(spec, build, materials, design, proposals, tasks)
    save_resolved_manifest_once(frozen)        # no later scientific mutation

    store = open_experiment_store(frozen.id)
    store.require_schema_version(SCHEMA_VERSION)
    store.acquire_controller_lease()
    store.save_frozen_spec_once(frozen)
    store.save_launch_record(launch_options)

    chooser = restore_candidate_source(frozen, store)

    while not chooser.finished():
        # Persist RNG/optimizer state and proposals before any launch.
        proposed = store.record_ask_and_state(chooser.ask(), chooser.state())
        accepted = []

        for point in proposed:
            realized = realize_and_validate(point, frozen, materials)
            realized_tasks = bind_candidate(frozen.tasks, realized)
            identity = simulation_identity(realized, realized_tasks, build)

            cached = store.find_complete_simulation(identity, frozen.reuse_policy)
            if cached:
                accepted.append(analyze(cached.blocks, frozen.analysis))
            else:
                store.plan_trial(identity, realized, realized_tasks)  # before launch
                accepted.append(run_trial(identity, realized_tasks, frozen, store))

        complete = [result for result in accepted if result.is_complete]
        chooser.tell(complete)                 # never tell it failures as zero
        store.record_tell_and_state(complete, chooser.state())

    write_summary(store, frozen.analysis)
```

The trial execution has one writer and explicit atomic completion:

```python
def run_trial(identity, tasks, frozen, store):
    pending = store.pending_tasks(identity)

    for message in run_workers(pending, frozen.resources):
        if message.kind == "heartbeat":
            store.heartbeat(message.task_id, message.attempt_token)
        elif message.kind == "finished":
            # The transient witness proves beamOn was reached; exit code alone
            # is not sufficient for this executable.
            validated = validate_hit_file(
                message.partial_output,
                witness=message.completion_witness,
            )
            final_path = atomic_publish(validated)  # rename on same filesystem
            block = score_once(final_path, frozen.scoring)
            store.finish_task_if_current(
                message.task_id, message.attempt_token, final_path, block
            )
        elif message.kind == "failed":
            store.fail_task_if_current(
                message.task_id, message.attempt_token, message.reason
            )

    if not store.full_declared_set_is_complete(identity):
        return IncompleteTrial(identity)

    result = store.finish_trial(identity)
    return result
```

The old permanent `.done` file is replaced by an attempt-local witness plus an
atomic publication and database compare-and-swap. Archiving is a later explicit
command, never an asynchronous side effect of scientific completion.

BO may be asynchronous and completion-order dependent; CMA-ES is synchronous
by generation. The controller must preserve the scheduler policy declared by
the experiment instead of forcing every optimizer through the same batch loop.

## 7. A single frozen experiment file

New experiments should be small and complete. The exact schema can be refined,
but its separation of scientific input from launch resources should remain.

```yaml
schema: material-scan-experiment-1
id: 20260914-spatial-strata-512
purpose: Check convergence of the area-weighted device objective.

build:
  executable_sha256: "..."
  geant4_revision: "..."
  g4cmp_revision: "..."
  detector_revision: "..."
  detector_patch_sha256: "..."  # includes BuildMaterialWithNewDensity behavior
  macro_template_sha256: "..."

candidate_source:
  kind: named-points
  points: [baseline, nb-anchor, sic, ideal]

sampling:
  method: electrode-stratified
  design_id: fa659c7d860acdee
  sites: 512
  site_seed: 20260727
  replica_seed_bank: 9
  replicas: 8
  allocation: {S0_junction: 27, S1_le_0.05: 94, S2_le_0.20: 126,
               S3_le_0.50: 93, S4_bulk: 172}
  area_weights: {S0_junction: 0.0000265625,
                 S1_le_0.05: 0.0026366310973713744,
                 S2_le_0.20: 0.03286157599441309,
                 S3_le_0.50: 0.17848066274749622,
                 S4_bulk: 0.7859945676607194}

fidelity:
  events_per_task: 31250
  gun_energy_eV: 0.010

analysis:
  objective: device_junction_qps_per_eV
  risk_95: report-only
  uncertainty: replica-and-stratum
  version: 1

stopping:
  rule: declared-spatial-convergence-rule

reuse_from:
  - 20260914-spatial-strata-256
```

The example reflects the current corrected 512-site `S*` design and its eight
replicas. It is still illustrative, not an instruction to recompute or replace
recorded values. Saved campaigns contain two incompatible design generations
under the same campaign names: earlier four-stratum `H*` designs and corrected
five-stratum `S*` designs. The importer keys by the recorded design hash and
exact site table, never by campaign name, and never reinterprets `H*` rows as
`S*` rows. Production resumes load the saved sites and weights rather than
rerunning the expensive Monte Carlo area calculation.

Optimizer settings belong in the same file when applicable:

```yaml
search:
  method: bo-gp
  seed: 1
  paid_evaluations: 96
  batch_size: 4
  initial_design: {method: sobol, points: 32}
  kernel: matern-5/2
  acquisition: expected-improvement
  baseline_every: 24
```

This removes the need to reconstruct a scientific run from shell history.

## 8. Identity and cache design

Use separate identifiers because they answer different questions.

| ID | Answers | Includes |
|---|---|---|
| `experiment_id` | Which human study is this? | date and short purpose name |
| `task_key` | Is this exact site/seed simulation reusable? | build manifest, canonical physical input, full lattice bytes, one site, exact seed pair, events and gun energy |
| `simulation_key` | Is this the same complete declared simulation? | ordered declared task keys plus resolved candidate/design identity |
| `attempt_id` | Which execution produced or failed to produce the artifact? | unique token, task key, launch time and retry number; never used to merge evidence |
| `analysis_key` | Would these exact artifacts produce the same reported number? | ordered artifact checksums/attempts, scorer version, objective, weights, normalization, uncertainty method, Python/package/BLAS manifest |
| `search_key` | Is this the same optimizer history? | analysis key, bounds, constraints, method, seed, kernel/acquisition or CMA settings, budget |

The Git revision and source file hashes are still saved in the manifest. They
are evidence about how the input was produced, not a reason by themselves to
rerun an identical simulation.

The macro output filename must not affect `simulation_key`. Hash a normalized
macro in which only the declared destination path is replaced by a fixed token.
Every physics command remains in the hash. Hash the generated lattice content
directly. This makes a directory rename harmless without allowing different
physics to collide.

Do not normalize comments, command order, whitespace broadly, seed lines, site
coordinates, carrier commands, density, lattice name, energy, or `beamOn`.
Only the exact output destination and the attempt-local completion-witness line
are execution plumbing. Nested designs may reuse a task only when its full
`task_key`, including the seed pair, matches; sharing a site coordinate alone is
not sufficient.

Never rewrite old cache keys. The legacy reader maps old rows to the new
identity only after proving that the normalized simulation input is complete.
If any required field is unavailable, mark the row `legacy-unresolved` and do
not reuse it automatically.

With one ledger per experiment, cache sharing must also be explicit. A run
searches its own ledger and only the read-only experiments named in
`reuse_from`. It does not scan every archive or silently borrow a trial from an
unrelated study. The reused trial ID and source experiment are written into the
new analysis row.

A baseline control can deliberately rerun the same `simulation_key`. Express
this as `reuse_policy: forbid`; it creates a new attempt and artifact without
lying about the physical identity. Failed attempts, right-censored runs, and
non-observations are never cache hits and are never sent to an optimizer as
zero.

## 9. Storage and archive layout

Generated data should not be tracked in Git. Use one configured data root,
preferably on the large data volume:

```text
material_scan_data/
├── active/
│   └── 20260914-spatial-strata-512/
│       ├── experiment.yaml
│       ├── launch.json
│       ├── ledger.sqlite
│       ├── experiment.log
│       ├── summary.json
│       ├── plots/
│       └── scratch/
└── archive/
    ├── legacy-snapshot-20260914/
    │   ├── inventory.sqlite
    │   ├── summary.json
    │   └── ledgers/                 # each original ledger stored once
    └── 20260914-spatial-strata-512/
        ├── manifest.json
        ├── experiment.yaml
        ├── ledger.sqlite
        ├── summary.json
        ├── trials/
        │   ├── t000001.tar.gz
        │   └── t000002.tar.gz
        └── plots/
```

`MATERIAL_SCAN_DATA` selects the root. Do not repurpose `HOME`. The repository
contains only small experiment definitions, a small experiment index, curated
summaries needed by papers, and archive checksums.

Each trial bundle contains original hit files, macros, lattice configuration,
task logs, and a small manifest mapping every member to its former path and
SHA-256. Compression changes the container only; extracted file bytes must
match the source.

The historical inventory is SQLite, not a half-million-row CSV. A small JSON
summary and digest may be tracked in Git. Absolute ledger paths are mapped with
`Path.relative_to(recorded_old_root)`; do not use string replacement and do not
rewrite an old database. One snapshot stores each of the four original ledgers
once. Per-experiment manifests refer to the ledger checksum and row IDs rather
than embedding another database copy.

The active layout uses short task names because the surrounding directory and
ledger already identify the experiment and trial:

```text
scratch/t000123/r03-p012.mac
scratch/t000123/r03-p012.hits.partial
scratch/t000123/r03-p012.log
```

After success, these may become members of `trials/t000123.tar` (or a
deterministically compressed equivalent) when the operator explicitly archives
a stopped experiment. Failures remain unpacked until diagnosed or explicitly
archived as failures. Archive verification rejects missing/extra members,
duplicate names, absolute or `..` paths, symlinks, truncation, and checksum
differences; a successful `tar` exit status alone is not verification.

Do not untrack or remove source artifacts until the bundle has been extracted
and verified and a second copy has been verified on an independent filesystem.
A second directory on the same filesystem is not an independent backup.

## 10. Naming rules and current campaign rename map

### New naming rule

Use:

```text
YYYYMMDD-purpose-detail
```

Examples: `20260905-search-bo-seed01`,
`20260914-spatial-strata-512`, and `20260813-material-factorial-320m`.

- Dates come from the ledger creation time, not a guessed date in a filename.
- Event suffixes are total events per candidate: `4m`, `32m`, `320m`, `3p2b`.
- Site suffixes are actual site counts: `sites-128`.
- Seed suffixes are two digits: `seed01`.
- A result's validity is a manifest field, not a mutable filename such as
  `final`, `fixed`, or `good`.
- Keep the complete old campaign ID as `legacy_id` in the manifest.

### Existing campaign mapping

The importer should add the ledger-derived date in front of these new slugs:

| Current campaign ID | New slug | Notes |
|---|---|---|
| `stage3_factorial_v1` | `material-factorial-4m` | The ledger, not the present YAML, confirms 4 million total events. |
| `stage3_factorial_v1_e7` | `material-factorial-320m` | The old `e7` name is not meaningful to a new reader. |
| `stage3_factorial_v1_e8` | `material-factorial-3p2b` | 3.2 billion total events. |
| `stage4_pilot` | `property-pilot` | Keep its individual checks in the manifest. |
| `stage4_property_v1_bo` | `property-search-bo-original` | Preserve the withdrawn optimizer-provenance status. |
| `stage4_property_v1_cmaes` | `property-search-cma-original` | Same. |
| `stage4_property_v1_rand` | `property-search-random-original` | Same experiment family. |
| `stage4_property_v1_bench_bo_gp_s1` | `search-bo-seed01` | Formal equal-budget benchmark. |
| `stage4_property_v1_bench_cmaes_s1` | `search-cma-seed01` | Formal equal-budget benchmark. |
| `stage4_property_v1_bench_random_s1` | `search-random-seed01` | Formal equal-budget benchmark. |
| `stage4_property_v1_bench_sobol_s1` | `search-sobol-seed01` | Formal equal-budget benchmark. |
| `stage4_property_v1_confirmS` | `property-confirm-4m` | Use event count from each row if mixed. |
| `stage4_property_v1_confirmM` | `property-confirm-32m` | — |
| `stage4_property_v1_confirmL` | `property-confirm-320m` | — |
| `stage4_property_v1_confirmXL` | `property-confirm-3p2b` | — |
| `stage4_property_v1_projS` | `material-projection-pre-carrier-fix-4m` | Preserve P0 invalidation metadata. |
| `stage4_property_v1_smokeP0` | `carrier-fix-smoke-4m` | Derive the event suffix from the row. |
| `stage4_property_v1_smokeP0M` | `carrier-fix-smoke-32m` | Derive the event suffix from the row. |
| `stage4_property_v1_gapfillM` | `material-projection-gapfill-32m` | — |
| `stage4_property_v1_correctedL` | `material-projection-carrier-fixed-320m` | — |
| `stage4_property_v1_validate_bo` | `property-bo-validation` | Preserve the exact point set in the manifest. |
| `stage4_property_v1_tol` | `property-tolerance` | One-factor scan. |
| `stage4_property_v1_sites16` | `spatial-uniform-16` | Known non-converged design; retain that status. |
| `stage4_property_v1_sites32` | `spatial-uniform-32` | Same family. |
| `stage4_property_v1_sites64` | `spatial-uniform-64` | Same family. |
| `stage4_property_v1_stratsmoke` | `spatial-strata-smoke-01` | — |
| `stage4_property_v1_stratsmoke2` | `spatial-strata-smoke-02` | — |
| `stage4_property_v1_strat128` | `spatial-strata-128` | Electrode-aware design. |
| `stage4_property_v1_strat256` | `spatial-strata-256` | Electrode-aware design. |
| `stage4_property_v1_strat512` | `spatial-strata-512` | Finished 2026-09-09 08:05; preserve its `H*` and `S*` design generations separately. |

Keep a permanent two-column alias table in `experiments/index.yaml`, so an old
trial or campaign ID always resolves.

### Result, log, and plot names

Do not repeat campaign names inside their own directory:

| Current style | New style |
|---|---|
| `results/stage4_confirmation_XL_best_random+elasticity_of_SiC.json` | `<experiment>/summary.json` with named rows inside |
| `logs_stage4/benchmark_strat.log` | `<experiment>/experiment.log` |
| `stage4_property_v1_strat512_ca92..._r0_p107_hitsfile.txt.done` | archived member `t000123/r00-p107.hits`; status is in SQLite |
| `stage4_property_v1_strat512_ca92...svg` | `<experiment>/plots/hits-sic.svg` |
| `stage4_report.json` | `<experiment>/summary.json` |
| `stage4_trials.csv` | `<experiment>/trials.csv` |

For historical imports, do not rewrite JSON keys or CSV columns merely to make
them prettier. Store the original file in the bundle and generate a separate
new summary through the compatibility reader.

## 11. Documentation plan

Only these documents are current:

| File | Content |
|---|---|
| `README.md` | purpose, five common commands, present scientific status |
| `docs/physics.md` | model, units, parameter table, material realization, objective equations |
| `docs/experiments.md` | one row per experiment: purpose, status, conclusion, archive ID |
| `docs/data.md` | ledger states, directory layout, backup, restore, and provenance |
| `docs/issues.yaml` | machine-readable invalidations, open decisions, and superseded claims |

Existing long documents should be copied unchanged to `docs/history/`, then
renamed with dates and clear subjects:

| Current document | Historical name |
|---|---|
| `STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md` | `202608-stage3-method.md` |
| `STAGE3_SMALL_MATERIAL_START.md` | `202608-stage3-start-and-results.md` |
| `STAGE3_GAP_REMEDIATION.md` | `202608-stage3-fixes.md` |
| `STAGE3_FACTORIAL_V1_RESULTS_REVIEW.md` | `202608-stage3-results-review.md` |
| `STAGE4_PROPERTY_OPTIMIZATION_PLAN.md` | `202608-stage4-property-search-plan.md` |
| `STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md` | `20260824-stage4-audit.md` |
| `STAGE4_AUDIT_REMEDIATION.md` | `20260824-stage4-fixes.md` |
| `STAGE4_RESULTS.md` | `202608-stage4-results-record.md` |
| `BO_GP_and_CMA_ES_Rerun_Recommendations.md` | `202609-optimizer-benchmark-plan.md` |
| `STAGE4_OPTIMIZER_BENCHMARK_RESULTS.md` | `20260905-optimizer-benchmark-results.md` |
| `STAGE4_P3_AND_SPATIAL_FINDINGS.md` | `20260908-gap-and-spatial-findings.md` |
| `Stage4_Device_Objective_and_Electrode_Aware_Rerun_Instructions_Sep8.md` | `20260908-device-objective-plan.md` |
| `HIT_FILE_DIAGNOSTICS_2026-09-14.md` | `20260914-hit-diagnostics.md` |
| repository `G4CMP_crash_and_memory_analysis.md` | `runtime-failures-and-memory.md` |
| repository `SensitivityAnalysis_Morris_run_record_Mac_M4Max.md` | `morris-run-mac.md` |
| repository `SensitivityAnalysis_Morris_run_record_Mimir.md` | `morris-run-mimir.md` |
| repository `SensitivityAnalysis_correlation_math.md` | `morris-correlation-math.md` |

`stage4_invalidations.yaml` becomes `docs/issues.yaml` without losing any
entries. Current prose should be generated or checked against it; do not
maintain the same validity statement manually in four documents.

The repository-level `README.md` becomes the short project entry point and
links to the material scan and the historical Morris records. Historical files
remain unchanged internally; an index explains their old and new names.

## 12. Current file disposition

This table accounts for the active code groups. “Retire” means remove from the
new active branch only after parity and archival; Git history and the archive
remain available.

| Current file or group | Destination | Action |
|---|---|---|
| `stage3_contract.py`, contract parts of `stage4_space.py` | `config.py` | Rewrite as strict immutable loading and normalized identity. |
| Material resolution in `stage3_trial_runner.py` and `stage4_space.py` | `physics.py` | Merge; keep realization modes and density refusal exactly. |
| `interface_transmission.py`, `SoundfromTensor.py` | `physics.py` | Merge behind tested pure functions. |
| `stage2_compute_QPs.py` | `physics.py` | Keep as the only hit-to-QP implementation. |
| Duplicate QP code in `stage2_compute_QPs_sensitivity_analysis.py` | none | Delete duplication after tests use `physics.py`. Move analysis-only code to `analysis.py`. |
| `stage3_ledger.py` | `store.py` | Preserve states; change to one writer and explicit migration. |
| Process/macro code in `stage1_run_simulations.py`, `stage3_trial_runner.py`, `sensitivity_memguard.py`, `sensitivity_utils.py` | `runner.py` | Extract one task runner; retain proven crash and memory guards. |
| Parameter constants in `sensitivity_params.py` | `parameters.yaml` and `config.py` | One authority; generate human tables. |
| Morris-only parts of `stage1_run_simulations.py` and `stage2_compute_QPs_sensitivity_analysis.py` | historical archive | Keep only if Morris remains an active supported study; otherwise do not import them in material scan. |
| `stage4_strata.py` | `sampling.py` | Keep geometry-derived strata, nested designs, and area weights. |
| Site/seed planning in `stage3_trial_runner.py` | `sampling.py` | One deterministic planner. |
| `stage4_objectives.py` | `analysis.py` | Remove global design; pass context explicitly. |
| `stage4_optimizers.py` | `search.py` | Preserve four used methods behind one interface. |
| `stage4_llm.py` | `docs/history/` or optional external tool | Do not import in the core program. Preserve logs and prompts with its experiments. |
| `stage3_run_factorial.py`, `stage4_optimize.py`, `stage4_confirm.py`, `stage4_pilot.py`, `stage4_tolerance.py` | `scan.py` plus experiment YAML | Replace duplicated evaluators with one controller. |
| `stage4_project_material.py` | `analysis.py` | Projection selects named points; verification still uses the one runner. |
| `stage3_report.py`, `stage4_report.py`, both fidelity comparators, both convergence analyzers, `stage4_assemble_xl.py`, `stage4_build_warmstarts.py` | `analysis.py` and `scan.py analyze` | Use one stored summary model. |
| `stage4_audit.py`, `stage4_reconcile.py` | `scan.py doctor`, `store.py import-legacy` | Keep fail-closed checks; do not keep a separate permanent repair script. |
| `stage4_probe_g4_density.py`, `build_material_catalog.py` | `scan.py material probe/build` | Administrative subcommands; save provenance to `materials.yaml`. |
| Both hit diagnostic plotters | `analysis.py`, `scan.py plot hits` | Keep the Matplotlib implementation; retire the separate SVG generator. |
| `plot_detector_layout.py` | `analysis.py`, `scan.py plot geometry` | One plotting path. |
| `presentation/make_stage3_factorial_v1_presentation.py` | historical archive | Preserve with its generated presentation; not active runtime. |
| `tests_stage4.py` | `tests/test_*.py` | Split by responsibility; replace numbered tests with behavior names. |
| All eleven shell scripts | experiment YAML plus `scan.py` commands | Retire. Keep at most one short scheduler submission wrapper if the cluster requires it. |
| `stage3_config.yaml`, `stage4_config.yaml` | named files under `experiments/` | Import actual resolved settings; do not carry commented history into configuration. |
| `parameter_set.txt` | generated section of `docs/physics.md` | Stop reading it as input. |
| `material_catalog.yaml`, `stage4_material_pool.yaml` | `materials.yaml` | One record per material/property with field-level source and uncertainty. |

## 13. Simple command interface

The complete daily interface should fit on one screen:

```bash
python scan.py check experiments/property-search.yaml
python scan.py run experiments/property-search.yaml --workers 32
python scan.py resume 20260914-spatial-strata-512 --workers 32
python scan.py status 20260914-spatial-strata-512
python scan.py analyze 20260914-spatial-strata-512
python scan.py plot 20260914-spatial-strata-512 hits
python scan.py archive 20260914-spatial-strata-512
python scan.py verify-archive 20260914-spatial-strata-512
python scan.py doctor
```

Administrative commands used rarely:

```bash
python scan.py migrate EXPERIMENT_ID
python scan.py import-legacy legacy-map.yaml --plan
python scan.py material probe G4_GALLIUM_ARSENIDE
```

`--plan` is read-only and mandatory before a legacy import writes anything.

## 14. Ledger design

Use one SQLite file per experiment. A small global `experiments/index.yaml`
locates experiments; it is not another result database.

Minimum tables:

```text
experiment  frozen specification and all identity hashes
candidate   requested values, realization envelope, derived values, provenance
simulation semantic trial identity, candidate, exact design and task set
task        semantic site, stratum, weight, replica, seed pair, events, task key
attempt     token, execution state, failure kind, timing, runtime files/checksums
observation measured/physical-zero/right-censored/non-observation and completeness
block       hit count, total QPs, per-electrode QPs, scorer version and artifact
proposal    method, source, iteration/generation, acquisition, used-in-update
analysis    objective value, uncertainty, analysis key, validity/supersession
event       append-only state changes and diagnostic messages
controller  one expiring writer lease
```

Allowed task state transitions are explicit:

```text
planned -> running -> complete
                   -> failed
                   -> stalled
                   -> memory-killed
                   -> cancelled
```

Allowed trial completion is stricter:

```python
trial.complete = (
    every_declared_task_is_complete
    and every_final_file_parses
    and every_final_file_checksum_matches
    and block_count == declared_site_count * declared_replica_count
)
```

Do not store `success_zero` as a special physical result unless the validated
file genuinely contains zero scored QPs. A missing file can never reach that
state.

Every writable connection enables `PRAGMA foreign_keys=ON`. Read-only commands
never create columns or tables. An explicit migration refuses while a valid
controller lease or running attempt exists. Proposal, RNG, pending-set, and
optimizer state are checkpointed in the same transaction as every `ask` and
`tell`; BO completion order and synchronous CMA generations are reproducible.

## 15. Performance improvements that do not change physics

Apply these only after equivalence tests exist:

1. Generate a task macro immediately before launch instead of prewriting every
   macro for the whole campaign.
2. Use one controller transaction for a batch of state messages rather than a
   writable SQLite connection in every worker thread.
3. Stream or chunk hit-file parsing and read only columns needed for scoring.
   Compare totals against the current scorer before adoption.
4. Calculate per-task hit count, total QPs, and per-electrode QPs once for each
   scoring version. Later objectives read the compact `block` table.
5. Load and validate parameter/material tables once in the controller; pass a
   frozen resolved task to workers.
6. Keep scratch files on the same filesystem so publication is an atomic
   rename, not a copy.
7. Package completed trial files only through an explicit archive command after
   the experiment is quiescent. Never let background cleanup race a scorer,
   retry, or operator inspection.
8. Do not generate every plot automatically. Generate the standard summary and
   requested diagnostics only.

Do not optimize floating-point formulas, change libraries, or reorder physical
calculations during the structural refactor. Those are separate scientific
changes and require their own experiment identity.

## 16. Migration plan

### Phase 0 — Stabilize the current branch

1. Let all live workers finish, or close them through the existing documented
   recovery path.
2. Confirm the ledger has no live row and no process can write it.
3. Preserve the present work-tree changes in an explicit commit or other
   reviewed snapshot. Do not start from a silently dirty tree.
4. Record `git status`, file counts, byte counts, and all ledger integrity
   results.
5. Create the restructuring branch. Keep this branch unchanged as rollback.

Exit condition: there is a named, recoverable source snapshot and no writer.

Checkpoint on 2026-09-14: the revised branch exists, no legacy ledger row is
running, no freeze file or matching process is active, the legacy suite passes
190/190, and the audit passes 15/15. Existing user modifications and 194
tracked deletions are deliberately still present and must not be overwritten.

### Phase 1 — Inventory without moving anything

Create `legacy_inventory.sqlite` with one row per actual or expected file:

```text
old_path,size,mtime,sha256,source,ledger,campaign_id,trial_id,task_id,role,validity,presence
```

- Use SQLite's backup API for each stopped ledger, then run
  `PRAGMA integrity_check` on the backup.
- Record the checksum of the original database and the logical backup; do not
  expect those two database files to be byte-identical.
- Mark files that cannot be linked to a ledger row as `orphaned`. Retain them.
- Record invalidation and supersession information before changing names.
- Inventory both the work tree and the Git tree. A tracked-but-deleted file is
  `expected-missing`, not invisible.
- Preserve known anomalies rather than repairing them during import: 32 legacy
  foreign-key violations associated with the missing parent
  `stage4_pilot_1e56d6cc8745`; two deleted-row but still-present incomplete
  `strat512` directories (`2ffd9440d59d`, `df6e253d7c23`); and two successful
  Stage-3 trial trees absent from the work tree but recoverable as tracked blobs
  (`4bba865c33c9`, `c156fc847c51`). These are imported with explicit provenance
  and are never scored or reused automatically.

Exit condition: every file under current run, result, log, snapshot, and plot
roots appears exactly once in the inventory.

### Phase 2 — Build the new core beside the old code

1. Add the new flat modules and schemas without deleting old modules.
2. Add a compatibility reader for old YAML, ledgers, result JSON, `.done`
   markers, and paths.
3. Route one read-only report through the new `analysis.py` and compare it with
   the existing report.
4. Route one tiny disposable simulation through the new runner.

Exit condition: no production ledger or artifact has been modified.

This is the implementation branch's present scope. It includes the immutable
resolved manifest, identity functions, strict analysis, a one-writer store,
compatibility readers, archive verification, and a disposable runner smoke.
It does not yet authorize a production optimizer campaign or production-ledger
write.

### Phase 3 — Prove physics equivalence

Use representative cases:

- Si/Nb/Cu native baseline;
- a pseudo-Si property point;
- SiC with its explicit carrier realization;
- GaAs/Nb/Cu;
- a deliberately rejected density;
- uniform and stratified site designs;
- a real zero-hit task;
- incomplete, stalled, killed, and corrupt tasks.

For the same input require:

- identical resolved values and realization envelope;
- identical lattice configuration bytes;
- identical macro physics commands after normalizing only the output path;
- identical sites, strata, weights, seeds, and events;
- identical hit count, QP total, and per-electrode vector for saved hit files;
- identical objective and uncertainty within a declared numerical tolerance;
- identical accept/reject result for every physical gate.

Exit condition: all equivalence checks pass and the current audit still passes.

### Phase 4 — Make new experiments use the new writer

Start with a cheap smoke experiment. Do not convert the large historical
ledger in place. New runs receive new per-experiment ledgers; old data is read
through the compatibility reader.

Exit condition: stop/resume, failure recovery, cache reuse, baseline controls,
and archive verification all work on the smoke experiment.

### Phase 5 — Copy historical data into the archive layout

For each completed trial:

1. copy files into a temporary bundle;
2. include original relative paths and SHA-256 values in its manifest;
3. close the bundle;
4. extract it into a temporary verification directory;
5. compare member count, size, and SHA-256 with the inventory;
6. atomically publish the verified bundle;
7. record the archive path in `experiments/index.yaml`;
8. make a second verified copy on independent storage.

Do not rewrite old JSON, CSV, macros, hits, logs, or ledger rows. Generate new
views beside them.

Exit condition: every inventoried file is either in a verified trial bundle or
in a documented `orphaned` bundle, and two archive copies exist.

### Phase 6 — Reduce the Git tree

Only after Phase 5:

1. stop tracking raw run data on the restructuring branch while leaving the
   verified local archive intact;
2. ignore `material_scan_data/`, SQLite WAL files, scratch, logs, and generated
   plots;
3. retain small experiment definitions, curated summaries, archive indexes,
   and checksums;
4. move historical Markdown unchanged;
5. retire compatibility shell scripts and numbered modules only after the new
   commands replace them.

Exit condition: a fresh clone contains the full method and result index, while
the archive restore procedure retrieves all raw evidence.

This phase is blocked until an independent second storage filesystem is named
and verified. The current filesystem has enough capacity for staging, but a
second directory on it does not satisfy the preservation rule.

### Phase 7 — Remove compatibility code

Keep compatibility readers for at least one full new campaign and one archive
restore test. Then remove old runtime imports. Keep a standalone read-only
legacy importer if old bundles still require it.

Exit condition: no active command imports a `stage3_*` or `stage4_*` module.

## 17. Acceptance checklist

The restructuring is complete only when all answers are “yes.”

### Preservation

- [ ] Is every old file present in the checksum inventory?
- [ ] Can every archived file be extracted with the same SHA-256?
- [ ] Are there two verified archive copies before any source path is removed?
- [ ] Are original ledgers preserved read-only?
- [ ] Are invalid, superseded, incomplete, and censored states preserved?
- [ ] Can every published number be traced to raw task files and a scorer?

### Scientific equivalence

- [ ] Do representative old and new resolvers produce the same material
      realization and derived values?
- [ ] Are generated physics commands, lattice data, sites, weights, seeds, and
      events identical?
- [ ] Does resume consume the byte-identical resolved manifest without
      regenerating a design, seed bank, proposal, or material record?
- [ ] Does the scorer reproduce total and per-electrode QPs?
- [ ] Does the stratified estimator reproduce `sum(W_h * mean_h)`?
- [ ] Is an incomplete task set impossible to score?
- [ ] Can objective-only changes reuse raw hits without pretending the
      simulation itself changed?

### Operation

- [ ] Can a campaign be understood from one frozen YAML file?
- [ ] Can it be started, stopped, resumed, checked, analyzed, and archived with
      `scan.py` only?
- [ ] Is there exactly one ledger writer?
- [ ] Can a stale attempt or expired controller fail safely without publishing
      over the current attempt?
- [ ] Are schema migrations explicit and refused while a run is live?
- [ ] Is completion an atomic validated state rather than a `.done` marker?
- [ ] Does a baseline control always create a fresh measurement when requested?
- [ ] Does a harmless path or comment edit preserve simulation cache access?
- [ ] Does a physics input, executable, geometry, or material realization
      change always invalidate simulation cache access?

### Simplicity

- [ ] Are there no active numbered-stage modules?
- [ ] Are there no experiment-specific shell chains?
- [ ] Is each physical parameter defined in one place with units and source?
- [ ] Are current documentation and historical documentation separated?
- [ ] Are generated raw files absent from Git?
- [ ] Are trial filenames short because identity lives in the ledger?

## 18. Expected result

The active runtime falls from more than thirty interdependent Python entry
points to one shallow purpose-named package and one command. The tracked repository
should fall from 4,412 files to roughly the source, tests, experiment
definitions, current documentation, and small curated summaries—well under a
few hundred files. The 563,615 current run files remain recoverable but become
roughly one verified bundle per trial rather than a directory tree a human must
interpret.

Most importantly, the new structure makes the physics visible. A reader opens
one experiment file, follows one candidate through one resolver and one runner,
and sees one explicit objective. Old data remains evidence rather than clutter,
and future changes can be tested without reopening every historical failure
mode.

## 19. First implementation slice on the revised branch

The safest first implementation is intentionally bounded:

1. add the shallow `material_scan` package without deleting legacy modules;
2. validate and write immutable resolved manifests before launch;
3. implement task, simulation, attempt, analysis, and search identities;
4. add the one-writer store, strict state transitions, and fake-worker recovery
   tests in temporary directories;
5. implement a read-only legacy inventory/compatibility reader and verified
   archive code without archiving production data;
6. reproduce saved scorer and stratified-objective results from small immutable
   fixtures;
7. compare material resolution, lattice/macro rendering, sites, seeds, and
   optimizer replay with legacy code;
8. run one disposable, low-event smoke only after file-level parity passes;
9. stop before migrating ledgers, deleting old entry points, or launching a
   costly campaign.

Do not begin by renaming directories or rewriting a production ledger. Once
the inventory, identity rules, resolved-manifest path, and old-data reader are
proven, the remaining changes become reversible and testable. The legacy suite
remains mandatory throughout; a parity test may not silently skip because a
fixture is missing.
