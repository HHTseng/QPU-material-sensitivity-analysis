# Stage 4 audit remediation — what was fixed, what it is gated by, what is left

Date: 2026-08-24
Audit: [`STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md`](STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md)
Branch: `Material_optimization_v3_scan_parameters`

Exit-gate suite: **39 → 88 gates, all passing** in the `G4CMP` environment.
Consistency audit: `python stage4_audit.py` → **no failures**.

Its warning count is deliberately not quoted here. Warnings are live state, not
a score: while a campaign is running, accurate warnings appear and disappear
(the ledger is frozen; a manifest is mid-write; the pre-fix rows still lack
provenance). A number in this file would go stale within the hour and would
invite someone to "fix" a warning that is telling the truth. Run the command.

## The one invariant that governed every change

`derived` is inside the cache key. Adding a field to it — or rewording a
provenance string in it — mints a new key for every candidate and silently
discards the campaign. So the realization envelope is written into `derived`
**only when it is not the plain Si-carried default**, and the default provenance
string is preserved byte for byte.

This is checked against the real ledger, not asserted: gate **T15h2** re-resolves
all **163** recorded Stage 4 trials and requires each to reproduce its stored
`derived` exactly. It does. The contract hash is likewise unchanged — the
`stage4_config.yaml` additions are comments only, verified by parsing the file
with and without them.

A 22-hour XL confirmation job was live throughout this work. Nothing was written
to `stage4_trials.sqlite`; the two new ledger columns are added by the migration
the next time a writer opens it, and `stage4_audit.py` reads the database
read-only and reports their absence rather than forcing it.

## One consequence you must decide about: the cache is now cold

Four of the files these fixes touched are in `CODE_IDENTITY_FILES`:
`stage3_ledger.py`, `stage3_trial_runner.py`, `stage4_space.py`,
`stage4_objectives.py`. The code fingerprint is part of every cache key, so
**none of the 163 recorded success trials is cache-reachable from the current
code** — `stage4_audit.py` reports this as a warning on every run.

That is the fingerprint working as designed, and it is deliberately blunt: the
alternative is a cache that returns results from code that no longer exists,
which this project has already been bitten by twice.

**What was measured, so the decision can be made on evidence rather than on
nerves.** For a default pseudo-material candidate the changes are provably
inert:

* all **163** trials re-resolve to a **byte-identical `derived`** (gate T15h2);
* all **163** regenerate a **byte-identical lattice `config.txt`**;
* the two artifact writers (`_write_lattice_config`, `_write_sub_run_macro`) are
  untouched — the only edits in `stage3_trial_runner.py` are the `evaluate()`
  signature, the cache-key call and the control record;
* `contract_hash()` is unchanged (the `stage4_config.yaml` additions are
  comments only, verified by parsing the file with and without them);
* the `Main` executable hash is unchanged.

So a re-run of any recorded candidate would produce the same simulation. The
options, in order of increasing commitment:

1. **Do nothing.** Correct and safe. Every future campaign starts cold; the
   recorded results stay readable and quotable, they just cannot be reused as
   cache hits. Costs compute only if you re-run the same candidates.
2. **Re-baseline the ledger**: after verifying the resolver output is identical
   (the check above, which is now a gate), stamp the new fingerprint onto the
   existing rows. Preserves the cache without weakening the rule, but it
   *rewrites ledger rows*, so it is a deliberate, logged operation and needs
   your sign-off. It is not implemented here.
3. **Exempt the edits from the fingerprint.** Do not. That is exactly the
   cache-identity hole the v2 factorial audit closed.

Also note item 4b of `STAGE4_RESULTS.md` §5.5, still open and pointing the same
way: `max_workers` and `total_mem_gb` enter `contract_hash()` although they
change no physics, so re-running a candidate with a different `--workers` mints
a new key. That one is a genuine bug in the hash's *scope* and should be fixed
before the next campaign — unlike the fingerprint change above, which is the
mechanism behaving correctly.

**A 24-hour XL confirmation was live throughout this work and is unaffected.**
Its `evaluate()` calls — and therefore its fingerprints and cache keys — were
made when it started; the remaining work is inside a single `evaluate()` call,
and nothing was written to `stage4_trials.sqlite`. See "Protecting the live run"
below for what had to be done to keep it that way.

## Protecting the live run

Preserving the XL job turned out to need an active intervention, not just
restraint.

**The threat.** `run_stage4_xl.sh` is a chain. Step 3 (the pair
`best_random elasticity_of_SiC`) was still running; steps 4 and 5 were queued
behind it. Step 4 launches a **new** `stage4_confirm.py` process, which would
have:

1. loaded the fixed modules → a **different code fingerprint** → a cache miss on
   all five candidates → re-simulation of ~5 × 3.2e9 events, days of compute,
   for numbers that already exist and whose interpretation has not changed;
2. opened a new `Ledger` → run `_migrate()` → **ALTERed the schema underneath**
   the running evaluator.

The script's own comment ("all cache hits, seconds") was true when it was
written and is now false.

**What did not work.** `kill -STOP` on the chain's shell did not persist in this
environment — the process returned to `S` within seconds, repeatedly. Signals
were not a reliable control here, and editing the script in place is worse:
`bash` reads a script by file offset as it executes, so rewriting a running
script can make it execute garbage. **`run_stage4_xl.sh` must not be edited
until the chain's shell is gone.**

**What did work — a freeze at the choke point.** `stage3_ledger.Ledger.__init__`
now checks for `<ledger>.frozen` *before* `sqlite3.connect`, and refuses with the
recorded reason. Every campaign entry point checks the same file straight after
argument parsing and exits with a readable message.

This is enforcement rather than hope, and it has the right asymmetry: it can
only affect **new** processes. The running evaluator holds its own connection
and its own in-memory copy of the module, so the freeze cannot reach it. Step 4
will now start, print the reason, exit non-zero, and be logged by the chain's
own `|| say "WARNING: ..."`. No ledger, no migration, no simulation.

Verified live: a new `stage4_confirm.py` is refused, and the 32 Geant4 workers
and their parent keep running.

**Steps 4 and 5 still get done**, without compute: `stage4_assemble_xl.py`
merges the per-pair confirmation JSONs into the combined 1e8 table and can run
the four-tier ladder. It opens no ledger and starts no Geant4 process. Rows a
finding invalidated are carried through with `validity: "invalid"` and the
finding that invalidated them, rather than dropped — a labelled bad row is
easier to audit than a missing one.

## Post-XL runbook

`./stage4_post_xl.sh {check|snapshot|validate|migrate|unfreeze|all}` — steps 2
and 3 of the decision.  The first implementation is useful as a runbook, but a
follow-up audit found that its state transitions are **not yet enforced**; see
"New major findings" below.  Do not run `all`, `migrate`, or `unfreeze` until
those findings are fixed and gated.

| step | what it does | writes to the ledger? |
|---|---|---|
| `check` | is the XL trial terminal? reports sub-run completion, artifact size and age | no |
| `snapshot` | `sqlite3 .backup` (WAL-consistent) into `snapshots/pre_migration_<date>/`, plus `results/`, the XL run directory, and `SHA256SUMS` | no |
| `validate` | `PRAGMA integrity_check`, live-vs-snapshot row counts, status histogram, then the full audit **against the snapshot** | no |
| `migrate` | opens the ledger once so `_migrate()` adds the ten new columns; currently checks only that the snapshot checksum file exists | yes, additive only |
| `unfreeze` | moves the freeze file into the snapshot as `frozen.reason.txt`; currently checks only that the snapshot checksum file exists | no |

`check` does refuse to let `snapshot` run while the XL row is `running`.
However, the checksum file is created by `snapshot`, before `validate`, so its
existence is not proof that validation or migration succeeded.  The intended
ordering can currently be bypassed by invoking the subcommands separately.

## Liveness: why a running trial looked stale

The first version of the audit's activity check declared the live XL trial
stale. It was wrong, and the way it was wrong matters: **a false positive here
invites someone to kill a job that is doing real work.**

It judged on ledger timestamps and on a process listing. Neither is sound:

* the trial row is not touched while Geant4 workers run, and sub-run rows are
  written only at completion — so a healthy 29-hour trial and an abandoned one
  have identical timestamps;
* a PID namespace can hide a genuine host process from the auditor.

The check now reasons from evidence, in order of strength, and has **three**
outcomes rather than two:

1. **heartbeat** — `heartbeat_at`, written *during* the work by the process
   holding the lease, with `hostname`. Strongest.
2. **artifact growth** — a hits file modified recently proves work is happening
   even with no heartbeat and no visible process. This is what correctly
   identified the live XL trial.
3. **process visibility** — a live process naming the trial. Weakest, because
   its absence proves nothing.

If none of the three is available the verdict is **`unverifiable`**, never
`stale`, and the message says so explicitly. Only a *stale heartbeat* — positive
evidence that the owner stopped beating — justifies `abandoned`.

Recorded for every trial from now on (columns added by the migration, so
historical rows keep `NULL` and are reported as `unverifiable` rather than
misjudged):

| column | why |
|---|---|
| `lease_uuid` | unique per evaluator attempt; only the holder may beat, so a stale second process cannot make an abandoned trial look alive |
| `heartbeat_at` | updated every `STAGE4_HEARTBEAT_SECONDS` (default 120 s) by a daemon thread that runs for the whole Geant4 wait |
| `hostname` | which machine, on a shared cluster |
| `owner_pid`, `owner_ppid` | trace back to the launching shell |
| `scheduler_job_id` | first of `SLURM_JOB_ID`, `PBS_JOBID`, `LSB_JOBID`, `SGE_JOB_ID`, `JOB_ID`, `TMUX_PANE`, `STY` — so a trial is traceable to its job after the process is gone |

Gates T20a–T20j cover the freeze guard and all three liveness verdicts.

## Status by finding

| | Finding | Code | Gates | Still needs simulation budget |
|---|---|---|---|---|
| **P0** | Projected substrate carrier lost before evaluation | **fixed** | T15a–T15j, T15h2 | rerun every projected/real-material verification |
| **P1** | Optimizer labels did not match the proposals evaluated | **fixed** | T16a–T16i | rerun the optimizer comparison, ≥3 seeds |
| **P2** | Search not converged; binds against the box | **tooling fixed** | — | continue the search in a widened box |
| **P3** | Objective counts junction QPs only | **fixed** | T19a–T19g | the *choice* of constraint is a decision, then a rerun |
| **P4** | Distance compares incomplete feature vectors | **fixed** | T18a–T18f | rerun the projection with brackets |
| **P5** | Drift controls were cache hits | **fixed** | T17a–T17d | run controls in the next campaign |
| **P6** | Spatial / interface / lifetime systematics unfinished | not started | — | 16/32/64 sites, lifetime brackets, alternate interface model |
| **P7** | Documentation and ledger status inconsistent | **fixed** | `stage4_audit.py` | — |

## P0 — real-material propagation

**Root cause.** `stage4_project_material.py` attached `point["substrate_carrier"]`
as a loose extra dictionary key. `Space.complete()`, `candidate_payload()` and
`stage4_confirm.collect_points()` each rebuilt the point from the variable table
alone, so the key was dropped three separate times and `stage4_space.resolve()`
defaulted to `G4_Si`. Geant4 takes the substrate density from the `G4Material`
(`G4LatticeManager::LoadLattice` → `SetDensity(Mat->GetDensity())`), so every
derived speed and impedance in the affected runs is the Si-density one.

**Measured damage**, from the stored macros and ledger rows:

| labelled | intended carrier | ran as | density error | speed error | impedance error |
|---|---|---|---:|---:|---:|
| elasticity of SiC | G4_CALCIUM_FLUORIDE (3180) | G4_Si (2330) | −26.7% | **+16.8%** | −14.4% |
| elasticity of Be₂C | G4_BORON_CARBIDE (2520) | G4_Si (2330) | −7.5% | +4.0% | −3.8% |
| GaAs/Nb/Cu | G4_GALLIUM_ARSENIDE (5310) | G4_Si (2330) | −56.1% | **+51.0%** | −33.8% |
| Si/Nb/Cu | G4_Si (2330) | G4_Si (2330) | 0% | 0% | 0% |

All six generated macros carry `setSubstrateG4Name G4_Si`. The `Si/Nb/Cu` row is
recorded as **valid** — checked, not assumed: Si's intended carrier *is* `G4_Si`
and the pseudo path's base record is Si, so every override was written back at
its own value.

**Fix — separate physics decisions from material realization.** A versioned
`_realization` block now travels with the candidate:

```json
{"schema": "stage4_realization_v1",
 "mode": "pseudo_si_base",
 "substrate_carrier": "G4_CALCIUM_FLUORIDE",
 "base_lattice_map": "Si",
 "material_of_record": "SiC (mp-8062)",
 "material_density_kg_m3": 3226.6,
 "own_fields": ["c11", "c12", "c44", "lattice_a", "density(via carrier)"],
 "target_fields": ["scat", "decay", "decayTT"]}
```

* It is **preserved** by `Space.complete()`, `candidate_payload()` and
  `stage4_confirm.collect_points()`, and **validated** at each — an unknown
  carrier or a missing lattice record fails before a trial is planned.
* It is **routed** by mode. `native_g4cmp` resolves through the audited Stage 3
  catalog and copies the material's own complete record verbatim (`dyn`, DOS,
  Debye and all — `config_overrides` is empty); `custom_material` demands both a
  measured Geant4 density and a complete record on disk and refuses otherwise;
  `pseudo_si_base` is the Si-based pseudo-material, and says so in its
  provenance string.
* Substrate variables the realization declares as the material's own are
  **exempt from the search box**. Clipping a measured constant simulates a
  different crystal under the real one's name — which is how SiC ran at
  `C44` = 200 GPa instead of its measured 241.
* A declared real density that no carrier can represent within tolerance is a
  **hard gate**, not a silent substitution.
* Legacy points carrying a bare top-level `substrate_carrier` are migrated, not
  ignored.

**Data disposition.** Ten trials are registered in
[`stage4_invalidations.yaml`](stage4_invalidations.yaml) with their intended and
actual carrier, the measured error, and a verdict of `invalid` or `valid`. They
are **retained** — they are the evidence the defect was real. `stage4_audit.py`
fails if one is missing from the ledger and warns if a result file still quotes
one.

**Not fixed here.** Geant4 still accepts only a NIST material *name*, so a real
density is reachable only when some NIST material happens to sit within
tolerance. Be₃N₂ (3.3% away) and BP (6.7%) remain unverifiable. The proper fix
is a `BuildMaterialWithNewDensity` command in `PhononDetectorConstruction`; it
changes the executable and therefore the code fingerprint, so it invalidates the
cache by design and belongs at the start of a campaign, not in the middle of one.

## P1 — proposal provenance

Two defects, both confirmed in the stored data: the `bo_gp` ledger holds 24
trials and **one** non-null acquisition value, and the winning point was proposed
before the GP phase; the `cmaes` manifest records **one** completed generation
while 33 trials were labelled `cmaes`.

* Every proposal is stamped, before evaluation, with `proposal_source`
  (`sobol_init` / `gp_ei` / `cma_generation` / `cma_incumbent_reeval` /
  `random_filler` / `random_baseline` / `llm` / `llm_init` / `llm_gp_ei` /
  `fallback`), its generation or GP-fit number, its acquisition value, and
  whether it entered the optimizer update. Nothing leaves `ask()` untagged.
* Three ledger columns carry it: `proposal_source`, `optimizer_generation`,
  `used_in_optimizer_update`. `used_in_optimizer_update` is written *after* the
  tell, because a CMA point only learns once its whole generation reports.
* `GPBayesOpt` counts **pending** initial-design points against `n_init`, and
  returns **nothing** while the design is dispatched but unreported — waiting
  beats fitting a GP on three points and calling the result Bayesian.
* `CMAES` is **synchronous by default**: a full, unreported generation proposes
  nothing and the slot idles. Asynchronous mode still exists and labels its
  filler `random_filler`.
* The driver tolerates an optimizer that legitimately proposes nothing.
* Manifests now carry `ledger_status_counts` read from the ledger, so a campaign
  killed by the operator can no longer report zero failures.

## P2 — convergence

`stage4_tolerance.py` now computes a **paired, site-matched difference** for
every one-factor move (the 16 sites are common to both trials and cancel), and
applies the audit's stopping rule, printing the verdict:

> locally converged within the declared box ⟺ no tested move improves the paired
> objective by more than 2% **and** by more than two paired standard errors,
> **and** no variable sits within 2% of a box wall.

Criteria (4) and (5) — stability across model updates and across optimizer seeds
— are campaign-level and are reported as **untested**, not assumed. The current
point fails (1) and (3). Evidence-based widened limits, each with the material
that justifies it (diamond, cBN, SiC for the tensor; isotopically pure ²⁸Si for
`scat`; Ir for `topfilm_gap`), are documented next to the `space` block in
`stage4_config.yaml` — commented out, because `space` is hashed and widening a
bound must be a deliberate new campaign.

Every "optimum" in the results document is now "best point found" or "best
confirmed property vector found".

## P3 — the objective versus the fabrication goal

The objective is junction QPs and nothing else, and the largest lever the search
found exploits exactly that. The trade-off is now **measured**, and
**constrainable**, without being decided:

* `engineering_diagnostics()` reports, from the BCS gap: implied ground-plane
  `T_c`, `2Δ_film / 2Δ_Al`, the phonon-sink flag, equilibrium thermal-QP density,
  and log₁₀ ratios of QP density and surface-loss factor against Nb. For the best
  point found at 20 mK: `T_c` 10.12 K → **0.54 K**, and **+366 / +367 orders of
  magnitude** on the two axes the objective cannot see. (Log₁₀ because the linear
  ratio underflows — that spread is the finding.)
* Gate **G8** takes `topfilm_tc_min_K`, `topfilm_gap_min_eV` or
  `topfilm_gap_over_junction_min` from `space.constraints` and rejects a
  candidate before Geant4 starts. **Off by default**, and deliberately absent
  from the YAML rather than present-and-empty, because `space` is hashed.
* `pareto_front()` returns non-dominated finalists when no design wins on every
  axis.
* `junction_qps_per_primary` is registered as the accurate name for the default
  objective, and the docstrings now say "junction QPs", never "total QPs".

These are equilibrium BCS estimates using Al's density of states. They rank
ground-plane risk; they are not a predicted *Q*.

## P4 — the projection metric

Rankings are **stratified by feature coverage**: coverage sorts first, distance
second, and no table can place a 4/7 distance next to a 7/7 one without saying
so. The effect is stark — the 7/7 stratum's nearest substrate is Si at 5.47,
the 4/7 stratum's is Be₃N₂ at 0.38. They are different questions and the old
single ranking answered neither cleanly.

Also added: `missing_feature_report()` (which constants are unsourced, for how
many candidates), and `--phonon-brackets`, which simulates the low and high edges
of the range spanned by the **measured** G4CMP records for each borrowed
constant, so an unmeasured direction is reported as a bracket rather than as the
design target's number wearing a material's name. `--flat-ranking` reproduces the
pre-audit ordering for comparison.

## P5 — drift controls

`baseline_control()` called the evaluator without `force`, so the cache returned
the original row and "6 re-evaluations, 0.0% spread" measured the cache. Each
control now carries a `control_replica_id` — the only non-physics field in the
cache key, omitted from the payload entirely when absent so no existing key
changes — which makes it execute fresh **and** mint its own row. Controls record
host, worker shape, code fingerprint, timestamp and runtime; the log
distinguishes `fresh` from `CACHE HIT -- not a fresh control!`; and
`Ledger.observations()` excludes them so they never reach the surrogate or the
event-efficiency curve. `Ledger.controls()` returns the audit trail.

Drift cannot mean bit equality on this executable (a repeat with identical seeds
diverges in ~1 run in 6), so it is judged against the measured stochastic repeat
distribution.

## P7 — documentation and status

`stage4_audit.py` checks, on every run:

* the gun energy and `minEPhonons` agree across the contract, all five macro
  templates and `parameter_set.txt`, and that `minEPhonons < 2Δ_Al ≤ E_gun`;
* every invalidated trial still exists in the ledger, and no result file quotes
  one unlabelled;
* every `running` row is classified from heartbeat, recent artifact growth and
  visible-process evidence, with `unverifiable` kept distinct from abandoned;
* manifests agree with the ledger on failed/incomplete counts;
* controls and proposal provenance are being recorded;
* no document still **asserts** a withdrawn claim — the phrases come from the
  registry, and an occurrence inside a withdrawal notice is not a violation;
* quoted headline reductions match the ledger's best confirmed value, computed
  within a single campaign and excluding invalidated rows.

**Fixed by this audit:** `parameter_set.txt` documented a 1 meV gun energy while
the contracts and every template have used 10 meV since the 2026-08-12 protocol
change — the human-facing parameter list described a different experiment from
the one that ran.

## New major findings — fix in this order

These were found by the follow-up review after the 88/88 code gates passed.
They do not invalidate the carrier, optimizer-provenance, objective or
projection fixes above.  They do mean that the post-XL transition and the next
long campaigns are not yet ready to run.  The existing gates cover the ledger
freeze and liveness logic; they do **not** execute `stage4_post_xl.sh`.

### N0 — the post-XL runbook does not enforce its state machine

**Severity: blocking before the XL job finishes.**  `snapshot` creates
`SHA256SUMS`; `migrate` and `unfreeze` treat the existence of that file as proof
that the snapshot was validated.  Therefore `snapshot -> migrate -> unfreeze`
currently bypasses `validate`.  In addition:

* `validate` discards a non-zero audit result with `|| true`;
* migration prints `MISSING` for an absent required column but still succeeds;
* `unfreeze` does not require successful migration;
* the checksum manifest is never verified and its generating `find` can include
  `SHA256SUMS` itself;
* artifact-copy errors are suppressed; and
* there is no end-to-end temporary-ledger gate for the shell runbook.

**Best fix.**  Make the steps an explicit, fail-closed state machine:

1. Generate the checksum manifest while explicitly excluding the manifest
   itself, fail on every copy error, and run `sha256sum --check`.
2. Write an immutable `.validated` receipt only after `integrity_check`, row and
   status invariants, checksum verification and a zero-failure audit pass.  The
   receipt should contain the checksum-manifest digest.
3. Require that receipt for migration.  Fail unless all required columns exist
   and the pre/post row counts and status histogram are identical; then write a
   `.migrated` receipt.
4. Require `.migrated` before moving the freeze file.
5. Gate the complete command sequence on a disposable WAL-mode ledger, including
   deliberate bad checksum, failed audit, missing column and out-of-order cases.

Until that is implemented, **do not run** `stage4_post_xl.sh all`, `migrate` or
`unfreeze`.  Read-only `check` remains safe.

### N1 — XL `check` accepts an incomplete result as completion

**Severity: blocking for automatic unfreeze.**  The current check accepts
`incomplete_scenario_set` and merely prints the number of `.done` files.  Thus
`all` could migrate and unfreeze after an incomplete XL attempt.

For this planned confirmation, progression should require `success` or
`success_zero_qp`, exactly 32 planned sub-runs, all 32 in accepted terminal-good
statuses, 32 done markers, non-empty hit artifacts, the expected pair result
JSON, and termination of the parent confirmation process.  An incomplete
attempt may be snapshotted for recovery, but it must take a separate recovery
path and must not be called a completed confirmation.

### N2 — campaign identity and simulation cache identity are conflated

**Severity: fix before any corrected campaign.**  `contract_hash()` includes
`campaign_id` and the whole `fixed` mapping.  Consequently `max_workers`,
`total_mem_gb`, `per_sample_mem_gb` and `sample_timeout_s` mint different cache
keys even though they cannot change a successfully completed simulation.  The
same design also prevents clean reuse when only the optimizer, objective,
search-box metadata or campaign name changes.

**Best fix.**  Introduce two hashes rather than an exclusion patch:

* `campaign_contract_hash` — the complete search/protocol declaration for
  provenance; and
* `simulation_identity_hash` — only inputs that can change the generated macro,
  resolved material, ordered scenario, seeds, event count or scored physics.

Store both in the ledger and use only `simulation_identity_hash` in the cache
key.  Resource limits, timeout, output paths, optimizer/objective choice and
campaign naming belong only to execution or campaign provenance.  Make this
change after the old XL ledger is safely migrated, but before the P0 smoke test;
it will intentionally change the code fingerprint once more.

### N3 — the new lease records ownership but does not enforce it

**Severity: fix before long parallel optimizer campaigns.**  `claim_trial()`
unconditionally replaces the lease, so two evaluators can still execute the
same cache key and write the same run directory.  Also, the heartbeat thread
exits permanently and silently after one database exception.  A transient lock
can therefore turn a live job into a stale-heartbeat false positive.

**Best fix.**  Claim with an atomic compare-and-swap transaction; refuse a
second owner while a non-expired lease exists; permit takeover only through an
explicit stale-run recovery operation; make terminal writes conditional on the
lease; and retry transient heartbeat failures with bounded backoff and visible
diagnostics.  Without corroborating scheduler/process evidence, a stale
heartbeat should be reported as suspected/unverifiable rather than proof that
the evaluator died.

### N4 — projection `parallel` reduces resources without running in parallel

**Severity: major efficiency issue, not a scientific invalidation.**
`stage4_project_material.verify()` divides workers and memory by `parallel`,
then iterates over candidate points sequentially.  With `--parallel 2`, each
candidate receives half the machine while the other half remains idle.  Use a
bounded candidate executor with one ledger connection per thread, as
`stage4_confirm.py` does, or remove the option and allocate the full resource
budget to the sequential evaluator.

The rerun boundary remains correct: nine of eleven projection-derived ledger
rows are affected, while the pseudo-material fidelity ladder is not.  “Rerun
only the affected rows” is an invalidation statement, not the complete compute
count for a corrected comparison: the new projection must also run a fresh
matched baseline/control and ideal target.  The projection driver already asks
for both; include them in the budget.

## N0–N4 — what was fixed, and what each fix is gated by

All five findings were reproduced against the code before anything was changed.
Three further real bugs surfaced while fixing them -- one in the N0 fix, two
in the ledger's concurrency, plus a defect in the N3 fix itself. Every one
was found by a gate rather than by reading, and one of them only by running
the gate repeatedly. They are recorded in place below, because "the fix
introduced a bug" is exactly the thing a remediation report is tempted to
leave out.

| | Finding | Status | Gates |
|---|---|---|---|
| **N0** | runbook state machine bypassable | **fixed** | T21a–T21j |
| **N1** | `check` accepted an incomplete result | **fixed** | T21b, T21j |
| **N2** | campaign vs simulation identity conflated | **fixed** | T22a–T22i |
| **N3** | lease recorded but not enforced | **fixed** | T23a–T23i, T20h |
| **N4** | projection `parallel` halved resources without parallelising | **fixed** | T24a–T24e |

### N0 — the runbook is now a fail-closed state machine

Each step writes a **receipt** only on success, and the next step refuses
without it: `.snapshot_complete` → `.validated` → `.migrated` → `.unfrozen`.
The old version keyed `migrate` and `unfreeze` on `SHA256SUMS`, which `snapshot`
itself writes, so `snapshot → migrate → unfreeze` skipped validation entirely.

Specifically:

* the checksum manifest is built through a temp file **outside** the snapshot
  and excludes `SHA256SUMS*`, then `validate` runs `sha256sum --check`;
* `validate` no longer swallows the audit — `AUDIT_CMD` failing is fatal, and
  `.validated` records the manifest digest it certified;
* `migrate` refuses unless that digest still matches, **fails** if any required
  column is absent after migration (it used to print `MISSING` and succeed), and
  compares the pre/post row counts and status histogram;
* `unfreeze` requires `.migrated`;
* artifact copy errors are fatal instead of suppressed;
* a `recovery` snapshot is marked `mode=recovery` and `validate` refuses to
  certify it, so an incomplete attempt can be preserved but never promoted;
* new `status` subcommand prints which receipts exist.

> **A bug the new gate found in the fix itself.** Writing the manifest to
> `SHA256SUMS.tmp` *inside* the snapshot moved the race rather than removing it:
> `find` picked up the temp file, the manifest listed a path that the following
> `mv` then deleted, and `sha256sum --check` failed on **every** snapshot. The
> gate caught it immediately; the manifest is now built outside the tree.

### N1 — `check` requires genuine completion

Progression now requires **all** of: trial status `success`/`success_zero_qp`;
exactly 32 planned sub-run rows; all 32 in accepted terminal-good statuses; 32
done markers; no empty hit artifacts; the expected pair result JSON present and
containing at least one scored candidate; and the parent confirmation process
gone (asserted via `XL_OWNER_PID`, or by a process scan matched on the trial id
*or* on a confirm process writing this exact result JSON — a bare
`stage4_confirm.py` match false-positives on any unrelated run).

An incomplete attempt fails `check`, cannot be snapshotted as complete, and its
recovery snapshot is never certified — T21j asserts all four.

### N2 — two identities instead of one

`Contract` now exposes:

* **`campaign_contract_hash()`** — every section including the campaign name.
  Used for provenance and by `resume()` to decide which trials may be replayed
  into one optimizer history. `contract_hash()` is kept as an alias for it, so
  the ledger column and `resume()` keep their meaning.
* **`simulation_identity_hash()`** — what a re-run would have to match. This is
  what the cache key is built from, and it is stored in the new
  `simulation_identity_hash` column.

Excluded from the simulation identity, each with a justification in the code:

| excluded | why it cannot change a completed result |
|---|---|
| `max_workers` | thread-pool width over independent sub-runs |
| `total_mem_gb`, `per_sample_mem_gb` | memory-guard ceilings |
| `sample_timeout_s` | a trial the watchdog kills is INCOMPLETE and never scored |
| `campaign_id` | a name |
| `space`, `stage4`, `optimizer`, `description` | what *proposed* a point, and how it is scored afterwards, not what was simulated |

The default for an unclassified field is still "it might matter" — everything
else in `fixed`, all of `decision`, and every other section stay in. T22f asserts
that eight physical/scenario fields still move the hash; T22i asserts every key
claimed execution-only actually exists in the contract, so a typo cannot quietly
exclude nothing.

**Measured effect:** relaunching the ladder at a different `--workers` is now a
cache **hit** (T22g). That is the defect from `STAGE4_RESULTS.md` §5.5 item 4b,
which abandoned four in-flight trials; item 4b is now closed.

### N3 — the lease is enforced, and a stale heartbeat is not proof of death

* `claim_trial()` is a **compare-and-swap** inside a `BEGIN IMMEDIATE`
  transaction. It returns `False` if another lease is live (heartbeat within
  `LEASE_EXPIRY_SECONDS`, 1800 s), succeeds when unleased/expired/refreshing,
  and `takeover=True` raises `LeaseHeld` against an owner that is still beating
  — recovery is for a dead owner, not a slow one.
* `evaluate()` **refuses to run** a trial leased by a live evaluator, instead of
  writing a second process into the same run directory and cache key.
* Terminal writes are lease-conditional: a displaced owner cannot overwrite the
  new owner's result, and is told so rather than failing silently.
* The heartbeat retries transient failures with bounded exponential backoff
  (`HEARTBEAT_MAX_FAILURES`, `HEARTBEAT_MAX_BACKOFF`) and prints what happened.
  It previously returned permanently on the first exception, so one SQLite lock
  turned a live job into a stale-heartbeat false positive. Losing the lease
  stops it — and says so.
* The audit now requires **corroboration**: a stale heartbeat with no usable
  process list is `unverifiable` and reads "SUSPECTED dead, but unconfirmed".
  Only a stale heartbeat *plus* a process list in which nothing names the trial
  gives `abandoned`.

### N4 — `--parallel` now parallelises

`verify()` runs candidates in a bounded `ThreadPoolExecutor` with one ledger
connection per thread, as `stage4_confirm.py` does, and splits workers only
across slots that are really occupied — with a single candidate it now uses the
whole machine instead of `1/parallel` of it.

> **Two further bugs this gate found**, both reachable from every
> multi-threaded entry point on any new ledger's first use — that is, from the
> corrected campaigns that were about to be run.
>
> 1. **`duplicate column name: proposal_source`.** Several threads opening a
>    fresh ledger at once each saw a column missing and each issued
>    `ALTER TABLE ADD COLUMN`; all but the first died. SQLite has no
>    `ADD COLUMN IF NOT EXISTS`, so `_migrate()` treats the duplicate as
>    success. T24e opens one ledger from 16 threads.
> 2. **`database is locked`** — caught only because the gate was run
>    repeatedly: it failed in **2 of 3** runs before the fix, which alone would
>    have looked like an unlucky one-off. It then took **two attempts** to fix,
>    and the second attempt is the interesting one.
>
>    The first fix retried the `ALTER TABLE` and serialized it in-process. The
>    gate kept failing — because *opening* a ledger is not a read-only act:
>    `PRAGMA journal_mode=WAL` and `executescript(SCHEMA)` are themselves
>    schema-level operations that take locks, and they ran **outside** the lock
>    the fix had added. The whole of `Ledger.__init__` is now serialized
>    in-process and each schema-level step is retried with bounded backoff, with
>    the busy timeout raised to 60 s. Verified at 32 threads opening one fresh
>    database, five rounds, zero errors.
>
>    The lesson worth keeping: "I added a lock" is not the same as "the critical
>    section is covered", and only re-running the gate showed the difference.
>    T24e opens 16 threads on one ledger; T24f hammers open + claim + heartbeat
>    from 8 threads across 4 ledgers.
>
> Chasing (2) also exposed a defect in the **N3 fix itself**: `claim_trial()`
> used `with self.conn:` *and* an inner `BEGIN IMMEDIATE`. The context manager
> already opens a transaction, so that was a nested `BEGIN`, and the `ROLLBACK`
> in the refusal path fought the manager's commit. The compare-and-swap now
> manages its own transaction explicitly.

### Sequencing, unchanged by these fixes

The ledger stays **frozen**; N2 deliberately changes the code fingerprint once
more, which costs nothing while the cache is already cold. Order remains: let XL
finish → `./stage4_post_xl.sh all` → P0 propagation smoke test → corrected
projection. N2 must land before the smoke test so the corrected campaigns are
the first to benefit from execution-independent cache keys.

## N5 — the runbook could deadlock on a trial that could never succeed

**Found by reality, 2026-08-25.** `best_random` at 1e8 events/sub-run hit the
41.7 h watchdog on all 32 sub-runs and was recorded `incomplete_scenario_set`.
That left a campaign that was **over** — nothing running, nothing to corrupt —
and a runbook whose `snapshot` demanded the XL trial had *succeeded*. The ledger
would have stayed frozen and unmigrated indefinitely.

The N0/N1 fixes were right to refuse to call a timeout a completed
confirmation. The mistake was conflating that with migration safety. **What
makes migration unsafe is a live writer, not an unsuccessful trial.** A gate
that can never be satisfied is not fail-closed, it is stuck.

Now separated:

* **`no-live-writer`** — the migration-safety condition, and it no longer
  guesses from process command lines. Matching argv is hopeless here: a shell
  whose command line merely *mentions* `stage4_confirm.py` — an editor, a grep,
  this script's own heredoc — looks exactly like the writer, and both earlier
  attempts false-positived on the test runner and then on themselves. It now
  scans `/proc/*/fd` for processes **holding the ledger, its `-wal` or its
  `-shm` open**. An open file descriptor does not lie. Verified both ways
  against a real writer connection.
* **`check`** — unchanged: the scientific question, still strict.
* **`close-incomplete <reason>`** — an explicit, recorded operator decision.
  It refuses without a reason, refuses while a writer is live, and writes
  `.campaign_closed` with the reason, host, trial and timestamp.
* `snapshot` then labels the snapshot **`mode=closed_incomplete`**, and
  `validate` certifies `complete` or `closed_incomplete` but never `recovery`.

Gates T21j (an incomplete attempt still cannot be certified), T21k (closing
requires a reason), T21l (a closed campaign migrates, and the snapshot carries
the honest label).

Two smaller defects fixed alongside, both surfaced by the gates: the
snapshot-exists guard tested for the *directory*, which `close-incomplete`
creates first, making the closed path unreachable; and the block edit that
introduced `no_live_writer` had silently dropped `snapshot_recovery` entirely.

## Post-XL transition — completed 2026-08-25

| step | result |
|---|---|
| `close-incomplete` | reason recorded: best_random timed out at 41.7 h, not obtainable at this watchdog, re-queued |
| `snapshot` | 188 MB, `mode=closed_incomplete`, checksum manifest written |
| `validate` | checksums verified, `integrity_check ok`, live-vs-snapshot row counts equal, audit clean |
| `migrate` | 30 → **41 columns**; **184 trials and 5920 sub_runs preserved**; status histogram unchanged |
| `unfreeze` | freeze lifted; its reason preserved as `snapshots/pre_migration_20260825/frozen.reason.txt` |

The ledger is writable again and the corrected campaigns are unblocked. The
snapshot directory is gitignored — 188 MB, and it is a backup, not source.

**Re-queued for the corrected campaigns:** `best_random` at 1e8 events/sub-run,
with a watchdog sized from the *converged*-tier cost model rather than the
screening one (see `STAGE4_RESULTS.md` §5.4). It runs alongside the corrected
projection; it does not gate it.

## Best major scientific step next

After N0–N3 are fixed and the XL snapshot/validation/migration has completed,
the next simulation should be the cheap **P0 propagation smoke test**, not a
large optimizer campaign:

1. Regenerate every contaminated point file from
   `stage4_project_material.py`; never reuse the files with
   `substrate_carrier: null`.
2. Run SiC and GaAs at S fidelity together with a fresh matched baseline/control
   and ideal target on the held-out seed bank.
3. Before promotion, inspect the generated macro carrier, Geant4 runtime
   density, lattice-record source, unclipped measured constants, ledger
   candidate/derived realization, cache key and independently rescored blocks.
4. If propagation is correct, perform the corrected S projection and promote
   only survivors to M/L/XL.

Before spending the much larger P1/P2 optimizer budget, make the **P3
engineering decision**: declare the minimum acceptable ground-plane gap/Tc or
the multi-objective loss criterion.  Otherwise the optimizer can again spend
days finding a numerically excellent QP-only point whose implied equilibrium
quasiparticle/loss proxy is unusable.  Then run BO/CMA/random with at least three
seeds, equal paid event budgets, at least 50 genuine GP acquisitions and 8–10
complete CMA generations.  P6 spatial, lifetime and interface-model systematics
remain required before making a fabrication claim.

## What has NOT been done

Everything below needs simulation budget on a machine that is currently running
a 22-hour job, and none of it can be inferred from the existing data:

1. **The P0 rerun.** A cheap SiC/GaAs propagation smoke test first — inspect the
   macro, the runtime density in the Geant4 log, the lattice source, the ledger
   payload and the cache key by hand — then the full projected-material
   verification on held-out seeds, then promote survivors to high fidelity.
   Until then there is **no** valid estimate of how much of the property-space
   gain a real substrate recovers.
2. **The P1 optimizer rerun** — ≥3 seeds per method, equal paid event budget,
   ≥50 real GP acquisitions, ≥8–10 complete CMA generations. Until then no
   algorithm-efficiency claim is available at all.
3. **The P2 continued search** in the widened box, to a declared convergence
   criterion rather than to wall-clock.
4. **The P6 systematics**: nested 16/32/64 Sobol sites for the baseline, the
   Stage 3 winner, the current best point and the eventual finalists; low /
   nominal / high film-lifetime brackets; and at least one defensible alternate
   interface treatment against the calibrated effective AMM. The report should
   separate stochastic replica error, spatial quadrature variation, interface
   model uncertainty and material-property uncertainty.
5. **The P3 decision.** The constraint machinery exists; which floor to impose is
   a judgement about the device, not a computation.

## Which recorded experiments actually need rerunning

Asked directly: **do the 1e6 / 1e7 / 1e8 tiers have to be redone?** Almost
none of them. The answer is mechanical, not a judgement call, because the defect
has a sharp boundary: it only bites a candidate whose **intended** density
carrier was not `G4_Si`, and the only code path that ever set a carrier was
`stage4_project_material.py`. Every candidate the optimizers proposed is a
pseudo-material carried by `G4_Si` — the defect could not reach it.

That was verified by sweeping the whole ledger rather than by reasoning: every
stored candidate was matched against every projection point the campaign could
have produced (both as built today and as clipped by the pre-fix code), and its
intended carrier compared with the `g4_material_name` actually recorded in
`derived`. **11 projection-derived trials exist; 9 are affected; every other
trial in the ledger is untouched.**

| tier | candidate | intended carrier | ran as | rerun? |
|---|---|---|---|---|
| S · M · L · XL | baseline | G4_Si | G4_Si | **no** |
| S · M · L · XL | best_bo_gp / top1_bo_gp | G4_Si | G4_Si | **no** |
| S · M · L · XL | best_cmaes / top2_cmaes | G4_Si | G4_Si | **no** |
| M · L · XL | best_random | G4_Si | G4_Si | **no** — XL still running, let it finish |
| S · M | top3_bo_gp, 04_bo_gp, 05_cmaes | G4_Si | G4_Si | **no** |
| S | the 92 search trials | G4_Si | G4_Si | **no** |
| S | ideal_target | G4_Si | G4_Si | **no** |
| S · M | `Si/Nb/Cu` projected triplet | G4_Si | G4_Si | **no** — checked, not assumed |
| S · M | `GaAs/Nb/Cu` projected triplet | G4_GALLIUM_ARSENIDE | G4_Si | **YES** |
| S · M | elasticity_of_Be₂C | G4_BORON_CARBIDE | G4_Si | **YES** |
| S · M · L · XL | elasticity_of_SiC | G4_CALCIUM_FLUORIDE | G4_Si | **YES** |

So the fidelity ladder — the evidence that 1e7/sub-run is the converged tier and
that the headline moves by ≤1.6% at 1e8 — **stands as it is**. Only the
real-material projection rows need redoing, and those were already going to be
redone: their point construction changed (measured constants are no longer
clipped, GaAs now routes through its native record), so the rerun is a new
calculation rather than a repeat.

**Three consequences worth stating plainly.**

1. **Do not stop the running XL job.** It is computing `best_random` at 1e8
   events per sub-run — an unaffected candidate, and the one value
   `STAGE4_RESULTS.md` §0 still lists as *running*. Stopping it would discard
   ~29 hours of valid work and gain nothing.
2. **The elasticity_of_SiC XL run is already spent.** It completed on 2026-08-23
   at 23:43 after 9.4 hours, and it is invalid. That compute is not recoverable;
   it is recorded as evidence and must not be quoted.
3. **The point files are contaminated too, not just the results.** The carrier
   had already been dropped by the time `results/stage4_confirm_points*.json`,
   `stage4_points_fair.json` and `_xl_pair.json` were written — every one of them
   stores `substrate_carrier: null` for `elasticity_of_SiC`. Re-running from
   those files would reproduce the defect exactly. They are listed under
   `contaminated_inputs` in the registry and must be **regenerated from
   `stage4_project_material.py`**, not reused.

### What the corrected rerun costs

Only the affected rows, and only at the tiers that carry a claim:

| what | events | why |
|---|---|---|
| propagation smoke test (SiC + GaAs) | 2 × 4e6 | inspect macro, runtime density, lattice source, ledger payload, cache key by hand before spending anything |
| **fresh matched baseline + ideal target** | 2 × 4e6 **per tier** | not optional (finding N4): the baseline reads 3.945e-4 on bank 0 and 3.675e-4 on bank 9 — 7.3% apart — so every comparison must be against a baseline from the same bank, code identity and tier. The projection driver already runs both; they belong in the budget |
| projection verification, S tier | ~6 × 4e6 | rebuild the shortlist with the stratified metric |
| survivors at M, then L | ~3 × 3.2e7, then ~2 × 3.2e8 | promote only what survives |
| optional: `--phonon-brackets` | ×3 per variant | converts SiC's borrowed constants from a point estimate into a range |

The XL tier does **not** need repeating for the projection: the ladder already
shows this objective is converged at 1e7/sub-run, and that finding rests on
unaffected candidates.

## Claim language

Use:

> The Stage 4 campaign found and high-fidelity-confirmed a pseudo-material
> property vector that reduces simulated **junction** QP yield by about 70%
> relative to the `Si/Nb/Cu`-equivalent baseline, under the current fixed model
> and 16-site scenario set. The search and the real-material projection are not
> yet converged or validated for fabrication.

Do not use: "global property optimum"; "GP Bayesian optimization was the best
algorithm"; "SiC recovers 77% of the gain"; "nearest real material" without
naming the matched feature subspace; "total QP generation" for junction QP
yield; "six fresh baseline re-evaluations showed zero drift".
