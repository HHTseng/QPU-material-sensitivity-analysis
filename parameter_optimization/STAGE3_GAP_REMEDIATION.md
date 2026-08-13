# Stage 3 Gap Remediation Record

Findings from the 2026-08-12 readiness review of:

- **(a)** whether `parameter_optimization/parameter_set.txt` is correctly applied
  in `stage1_run_simulations.py`, `sensitivity_params.py`,
  `stage2_compute_QPs_sensitivity_analysis.py`, and the five
  `sensitivity_template_beamOn*.mac` templates;
- **(b)** whether `STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md` and
  `STAGE3_START_ROADMAP.md` are executable as written.

Verdict: **(a) correctly applied, 8 gaps.** **(b) strategically sound, but three
technical assumptions are wrong as written and the algorithm choice is
mis-sized for the measured evaluation cost.**

Every claim below was verified against the code, the Geant4/G4CMP sources under
`~/src/G4CMP_htseng` and `~/BNL_G4CMP_HT_Feb27`, or the existing run data under
`results/`. File references are `path:line`.

---

## What was verified correct (no action)

| Item | Enforcement point | Evidence |
|---|---|---|
| 3 film thicknesses commented out **and** pinned | `FIXED_MACRO_COMMANDS` | `sensitivity_params.py:164-166` |
| Charge-carrier / intervalley block commented out | `config_params` | `sensitivity_params.py:209-238` |
| 4 specularities (0.8 / 0.8 / 0 / 0.8) | `FIXED_MACRO_COMMANDS` | `sensitivity_params.py:172-175` |
| Al junction VSound / Gap / PhLifetime / slope | `FIXED_MACRO_COMMANDS` | `sensitivity_params.py:167-171` |
| `setTopQPLim`, `setTopFilmQPLim`, `setBotQPLim` = 3 | `FIXED_MACRO_COMMANDS` | `sensitivity_params.py:169,176,177` |
| `setWallAbs` 0.02, `setBotGap` 0.0 | `FIXED_MACRO_COMMANDS` | `sensitivity_params.py:178,180` |
| `minEPhonons`, `chargeBounces`, `phononBounces`, `clearance`, `e/hTrappingMFP` | `FIXED_MACRO_COMMANDS` | `sensitivity_params.py:181-186` |
| `dyn`, `Debye` | `FIXED_CONFIG_COMMANDS` | `sensitivity_params.py:245-248` |
| All 15 "tune" parameters present as active sweep entries | `electrode/detector/G4CMP/config_params` | `sensitivity_params.py` |
| `vsound`/`vtrans` derived, never sampled | `derive_cubic_sound_speeds` | `stage1_run_simulations.py:189-223` |
| Derived-speed implementation matches `SoundfromTensor.py` | executed | Si → **9018.610 / 5370.662 m/s**, matching the roadmap targets |
| Born stability + positive Christoffel eigenvalues gate the design | trajectory rejection | `stage1_run_simulations.py:184-186, 409-454` |
| 17 electrode X/Y coordinates | all five templates | byte-exact match to `parameter_set.txt` in all 5 |

---

## A1 — `setBotQPLim` label/value contradiction — **RESOLVED 2026-08-12**

`parameter_set.txt` carried a label and a tuple value that disagreed. Rule
adopted: **the value in the `fix at *` label is authoritative.** Applying it
resolved five stale tuples, all of which the code already implemented
correctly — so this was a documentation defect only, no code change:

| Line | Was | Now | Code already used |
|---|---|---|---|
| `setTopFilmPSpecProb` | `0.0` | `0.8` | 0.8 |
| `setTopPSpecProb` | `0.0` | `0.8` | 0.8 |
| `setBotPSpecProb` | `0.0` | `0.8` | 0.8 |
| `setBotQPLim` | `2` | `3` | 3 |
| `phononBounces` | `1000` | `10000` | 10000 |

Note on physics: the bottom boundary is a normal metal (`setBotNormal true`,
`setBotGap 0.0`), so `lowQPLimit` there is almost certainly inert; 3 vs 2 is
not expected to change results. The G4 default is `2.`.

---

## Change log

### 2026-08-12 — Change 1 APPLIED: injection energy protocol

Adopted the corrected energy protocol from the `Material_optimization` branch
(`STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md` §12.5). **This supersedes A3's
earlier recommendation to freeze at 382 µeV, which is withdrawn.**

| Setting | Before | After |
|---|---|---|
| `/main/gun/setEnergy` | swept over [191, 573] µeV | **fixed at `1.0e-3 eV`** |
| `/g4cmp/minEPhonons` | `0.000382 eV` (382 µeV) | **`0.0000382 eV` (38.2 µeV)** |

**Why.** At the old setting the primary phonon sat *exactly* on the Al
pair-breaking gate — `JunctionKaplanElectrode::IsNearElectrode` requires
`PhEnergy >= 2*GapJunc`, and `2*setTopGap = 2 × 191 µeV = 382 µeV` — while
`minEPhonons = 382 µeV` killed every downconversion daughter at the first decay.
The phonon cascade was therefore not simulated at all, which suppresses exactly
the mechanism Stage 3 optimizes: `scat`, `decay`, `decayTT` and the elastic
tensor all act *through* that cascade. The statistical gain is a measured ~4×
in QPs per primary event (9.3e-6 → 3.69e-5); the physics gain is that the tuned
substrate parameters can influence the objective at all.

**Why 1.0 meV specifically.** Mid-range of the [0.6, 1.5] meV window validated
on the branch. Floor: the 382 µeV gate, with margin rather than knife-edge
equality (1.0 meV = 2.62× the gate). Ceiling: isotope MFP falls to 137 µm at
1.5 meV against a 525 µm substrate. This is the one value in this change worth
revisiting — see "open decisions" below.

**Corroboration:** `sensitivity_template_v0.mac` shipped with
`minEPhonons 0.0000382 eV`. The 382 µeV value was a later edit that placed a
numerical cut above the physical one; this change restores the original.

Files changed:

- All five `sensitivity_template_beamOn*.mac`: `/main/gun/setEnergy 1.0e-3 eV`,
  `/g4cmp/minEPhonons 0.0000382 eV`.
- `sensitivity_params.py`: `/main/gun/setEnergy` commented out of
  `electrode_params` and added to `FIXED_MACRO_COMMANDS` (it is an injection
  condition, not a material property — this also closes the `setEnergy` half of
  **A2**); `minEPhonons` fixed line updated.
- `parameter_optimization/parameter_set.txt`: both recorded in the fix block
  with rationale.

**Consequential change to the Morris design:** removing `setEnergy` from the
sweep takes the design from **26 groups / 3456 samples to 25 groups / 3328
samples** (`128 × 26`). Any Morris run predating this change is not comparable.

Verified:

- `/main/gun/setEnergy` is `G4UIcmdWithADoubleAndUnit` with default unit `eV`
  (`Main/src/PrimaryActionMessenger.cc:35-40`), so `1.0e-3 eV` is unambiguous —
  checked deliberately, because a bad unit suffix is the Finding 5 silent-abort
  trap (§12.1b).
- Generate-only run (`SENSITIVITY_MAX_SAMPLES=2`, seed 12345) produces
  well-formed macros: no malformed numeric-with-double-suffix lines, gate
  `setTopGap 191.0e-6` intact, `minEPhonons` below the gate, and derived
  `vsound 8829.6 > vtrans 5471.0 m/s`.
- `minEPhonons (38.2 µeV) < 2*setTopGap (382 µeV)` — the numerical cut is now
  below the physical threshold, which is the invariant that must hold.

### 2026-08-13 — Stage 3 first slice implemented: contract, ledger, evaluator

Built and validated the triage's recommended first slice. Four new files in
`parameter_optimization/`: `stage3_config.yaml`, `stage3_contract.py`,
`stage3_ledger.py`, `stage3_trial_runner.py`. Full status recorded in
`STAGE3_SMALL_MATERIAL_START.md`.

**Exit checks passed against real Geant4** (not mocked):

| Check | Result |
|---|---|
| Baseline twice, same seeds, identical output | PASS — `total_QPs=10`, identical per-electrode vector |
| Interrupt/resume reuses completed sub-runs once | PASS — 3 of 8 rerun, 5 reused, total identical to uninterrupted baseline |
| Cache identity | PASS — same request returns the cached trial |
| Incomplete set never scored | PASS — `total_QPs = None`, never `0.0` |
| Non-baseline material refused | PASS — Ge and Al rejected, naming what must exist first |

Measured en route: **2.5e-5 QPs/primary event** at 400k events (vs the 3.69e-5
planning figure) and ~1.2e-4 core-s/event, confirming the cost model behind the
fidelity ladder.

**Three bugs surfaced by the tests, all fixed:**

1. **Retries corrupted the hits file.** `PhononSensitivity` opens it with
   `std::ios_base::app` (`PhononSensitivity.cc:84`), so a rerun appended to the
   previous attempt and embedded a second header row mid-file, which then parsed
   as strings in a numeric column. Retries now clear the hits file and marker
   first. Latent in the Morris runner, which never retried — it only appears
   once restartability exists.
2. **One bad sub-run aborted the whole trial.** An unexpected exception
   propagated out of the worker and discarded every sibling that had already
   succeeded. Now recorded as a sub-run status; the trial is judged incomplete
   like any other partial set.
3. **`--force` wrote nothing to the ledger.** It minted a new `trial_id` under
   the same `cache_key`, so the UNIQUE constraint silently dropped the row.
   Force now re-runs in place — a silent no-op write is worse than an error.

**Deliberately deferred** (with the safety argument in the checklist): the
material resolver beyond Si/Nb/Cu, the interface-transmission model, the
Materials Project exporter, anisotropic AMM, cross-candidate batching, and
in-run retry beyond resume. None is a correctness gap, because the evaluator
refuses non-baseline candidates by name and never scores an incomplete set.

**Remaining gate before optimization may start:** items 4 (density propagation
+ candidate-aware config overrides), 5 (interface transmission validated to
0.795/0.745/0.736), 6 (small curated catalog), 7 (one-factor pilots) and the
16/32/64 position-convergence check. All are material physics; the
infrastructure will not need to change again.

### 2026-08-13 — Small-material checklist review; identity, timeout and non-Si guards

Reviewed `STAGE3_SMALL_MATERIAL_START.md`. The nine-item sequence and its
ordering are sound and were not changed; the review section appended to that
document records the triage. Three code gaps were fixed here.

**Marker-list / position-identity validation.** The stage-2 completion check
read `if markers and index < len(markers)`, so a *ragged* `done_markers` list
silently stopped checking the trailing sub-runs while the summary still reported
the scenario set complete. Added `assert_manifest_identities()`: `hits_files`,
`done_markers`, `seeds` and `position_indices` must be equal length, and
`position_indices` must be exactly `{0..N-1}`. Verified it blocks ragged marker
lists, duplicate indices, index gaps, and manifests predating the marker
contract.

**Nonzero timeout.** `SENSITIVITY_SAMPLE_TIMEOUT` defaulted to 0 (disabled). The
memory guard only catches runaways that *grow*; a flat-RSS hang holds a worker
slot indefinitely. Unset now derives `max(1800 s, 100 x events x 1.3e-4 s)` and
prints how it was obtained; explicit 0 still disables but warns. Labelled an
uncalibrated watchdog bound, to be replaced by checklist item 8.

**Non-Si substrate guard — and why it matters more than expected.** Measured,
not assumed:

| | v_L (m/s) | v_T (m/s) |
|---|---:|---:|
| Ge tensor + **Si** density (today's code) | 7966 | 4890 |
| Ge tensor + **Ge** density (correct) | 5270 | 3234 |
| shipped `CrystalMaps/Ge/config.txt` | 5324 | 3259 |

So the Si density hardcode is a **~50% error** on a non-Si substrate, not a
correction — while the same code reproduces the shipped Ge config to **~1%** once
the density is right, which independently validates the Christoffel path against
a second material.

Separately, `FIXED_CONFIG_COMMANDS` **actively overwrites** `Debye` and the
third-order `dyn` constants. Ge's own config declares `Debye 2 THz`; a Ge run
would be forced to Si's `15 THz`, **7.5x too large**, with nothing in the output
showing it. This is an overwrite, not the inherited-default hazard recorded as
A6.

`assert_substrate_is_supported()` now refuses a non-Si `LATTICE_MATERIAL` with
both reasons stated. `derive_cubic_sound_speeds` also takes `density` as an
explicit argument (defaulting to the Si constant) so the fix path is open.

**Also found:** the G4CMP checkout ships lattice maps for **Al2O3, CaF2, CaWO4,
GaAs, Ge, LiF, Si** — more substrate options than the checklist assumes, several
needing no custom Geant4 material registry. Sapphire is a real qubit substrate
and worth considering, though it is not cubic and would need the general tensor
path rather than the cubic shortcut.

**Inconsistency flagged, not silently fixed:** the checklist's item 8 says "32
workers first, then 64", but the runner now defaults to 64. The checklist is
right — pass `SENSITIVITY_MAX_WORKERS=32` for the first pilots.

### 2026-08-13 — Stage 2b restored; scenario completeness and completion proof

**Stage 2b restored.** `stage2_compute_QPs_sensitivity_analysis.py` is back at
its committed state (`git checkout`); the `assert_single_replica` guard added on
2026-08-12 is gone. Stage 3 does not run sensitivity analysis, so the guard's
purpose (protecting a Morris consumer from replica-named samples) no longer
applies. Note the consequence: on a multi-position run the script will not find
`hits_file`/`Morris_<i>` as it expects. It is out of the Stage 3 graph and left
untouched, as the pipeline document requires.

#### Assessment of the six reported problems

| # | Blocks Stage 3 execution now? | Action |
|---|---|---|
| 1 | Partial scenario sets silently accepted | **Yes — on the objective path.** Fixed |
| 2 | No positive completion proof | **Yes — same path, and it makes #1 practical.** Fixed |
| 3 | Morris runner, not a Stage 3 evaluator | No — unbuilt, not broken. This *is* the next work item |
| 4 | Material physics Si-specific | No — correctly blocking in the roadmap; do not run non-Si tensors yet |
| 5 | `ElasticityTensors.py` not roadmap-ready | No — already logged as B7 / Step 6 |
| 6 | Stage 2b modified | Restored (above) |

Items 3–5 are accurate and already recorded; nothing to fix, and no code exists
yet that they could break. Items 1 and 2 were fixed because both sit on the
future objective path and, left alone, would be inherited by
`stage3_trial_runner.py` as defaults.

#### Fix 1 — scenario completeness is now strict by identity

`stage2_compute_QPs.py` **refuses to score a design point whose declared
injection positions are not all present and complete.** Rationale, as reported:
the sites are a fixed spatial quadrature, not interchangeable IID draws, so a
partial set changes the estimand rather than merely its variance. Three specifics
make it worse than a variance argument:

- site-to-site QP yield spans **9.18× (CV 60.6%)** at fixed material, so losing a
  high-QP site biases the candidate in the *flattering* direction;
- failure is **not independent of the candidate** — the measured
  `vtrans > vsound` SIGSEGV was deterministic per design point, so exactly the
  materials that crash would be scored on a favourable subset;
- nothing in the output previously distinguished the two cases.

Implemented:

- `require_complete=True` by default; an incomplete entry is reported as
  `QP: INCOMPLETE <name>` with per-file reasons and is **not scored**.
- `--allow-partial-positions` restores best-effort pooling as an explicitly
  labelled **diagnostic** path: a loud banner, a per-row `WARNING`, and a
  `positions_complete=False` column so the rows cannot be mistaken for
  selectable results.
- `assert_no_duplicate_identities()` rejects a repeated
  `(design_point, replica)` before any work is done — duplicates would be
  double-counted while every file still looked well formed.
- A closing completeness tally: scored / complete / partial / not-scored, with a
  reminder to re-run missing sub-runs before using the results for selection.

Retry and ledger state remain Stage 3 work (item 3); this change guarantees a
partial set is *detected and refused*, which is the part that must not be
deferred.

#### Fix 2 — positive completion proof, and why it matters more than it looks

Stage 1 macros now write a marker with Geant4 itself, on the line **after**
`/run/beamOn`:

```
/run/beamOn <N>
/control/shell touch <hits_file>.done
```

(`/control/shell` confirmed present in this Geant4 10.7.4 build.) The hits header
is written when `beamOn` *starts*, so file existence could never distinguish a
finished run from one killed mid-loop. The marker can only exist if the event
loop returned. `verify_completion_markers()` asserts at generation time that the
line follows `beamOn` and that its path matches the sub-run, so two sub-runs
cannot certify each other. Marker paths are recorded in the manifest and checked
again by stage 2.

**The non-obvious benefit: this makes Fix 1 affordable.** The known exit-time
SIGSEGV (~0.4% of runs, physics complete) previously raised
`CalledProcessError`, discarding a *valid* sub-run. At 32 sub-runs per candidate
that is **P(≥1 affected) ≈ 12%** — so under strict completeness, roughly one
candidate in eight would have needed a retry for a fault that damaged nothing.
Return code 139 / `-SIGSEGV` is now accepted **only on positive proof** (marker
present), never on the return code alone, taking that retry burden to ~0.

Status taxonomy now distinguishes: `MemoryKilled` (SIGKILL, a budget event),
teardown SIGSEGV **with** marker (accepted, logged), `MacroAborted` (exit 0
without marker — the silent-abort mode), and hits-file-missing-despite-marker.

Remaining for the Stage 3 evaluator (item 3): retry orchestration, resume state,
scenario/config hashing, the SQLite ledger, and matching *declared* seed and
event count against what the log reports. The roadmap's Milestone 1 item 3 can
now be marked done rather than "partly done".

### 2026-08-13 — Concurrency and memory budget raised

Requested change: `SENSITIVITY_MAX_WORKERS` default **4 → 64** (Linux only) and
`SENSITIVITY_TOTAL_MEM_GB` default **300 → 200 GB**.

| Setting | Before | After |
|---|---:|---:|
| `SENSITIVITY_MAX_WORKERS` (Linux) | 4 | **64** |
| `SENSITIVITY_MAX_WORKERS` (macOS) | 2 | 2 (unchanged) |
| `SENSITIVITY_TOTAL_MEM_GB` | 300 | **200** |
| `SENSITIVITY_PER_SAMPLE_MEM_GB` | 4 | 4 (unchanged) |

macOS is deliberately left at 2: the default is chosen per platform and 64 is a
mimir-scale number.

**Budget arithmetic on mimir** (192 cores, 485 GB `MemAvailable`):

- 64 workers is ~1/3 of the box; sub-runs are single-threaded.
- Expected steady usage **~4.5 GB** (64 × 0.07 GB healthy RSS) → **~45×
  headroom** under the 200 GB ceiling.
- Lowering 300 → 200 GB *tightens* safety on a shared host while remaining far
  above anything a healthy batch needs.
- `MAX_WORKERS × PER_SAMPLE_MEM_GB` = 64 × 4 = **256 GB, deliberately above the
  200 GB ceiling.** In a mass runaway the aggregate cap fires first and kills
  largest-offender-first; the per-sample cap exists to catch a *single* runaway
  quickly, not to bound the total. This is the intended ordering, not an
  inconsistency.

**Caveat worth carrying into the first campaign: 64 is 2× the validated level.**
The 221,184-sub-run screen that measured 0 failures and 2.15 GB peak ran at 32.
Nothing suggests 64 is unsafe — the memory headroom is enormous and the guard is
now in place — but it is extrapolation. The runner prints `Peak tracked RSS ...`
at the end of every batch and in each progress line; check it on the first long
run before trusting 64 for a multi-hour campaign.

Also added:

- **Oversubscription warning** (not an error): if `MAX_WORKERS` exceeds
  `os.cpu_count()`, the runner warns that sub-runs are single-threaded so the
  excess only adds contention. Verified at `MAX_WORKERS=500`.
- **Budget arithmetic printed at startup**, so the headroom is visible rather
  than implicit:
  `Memory guard: total 200 GB, per-sample 4 GB; expected steady ~4.5 GB (45x headroom), host MemAvailable 485 GB`

Docs updated to match: pipeline §12.4 and its two batch-size references, and
roadmap Milestone 5 item 5 (which no longer tells the reader to pass the worker
count explicitly, since it is now the default).

### 2026-08-13 — Doc/code consistency pass: seven stale items

All seven were verified as genuinely present before being changed; none was a
false alarm. Five are consequences of Change 1 and Change 2 not being propagated
back into the two planning documents.

| # | Problem | Severity | Fix |
|---|---|---|---|
| 1 | Pipeline §3.1 fixed-controls table still listed `minEPhonons = 0.000382 eV` while §12.5 says 38.2 µeV | **High** — normative table; `stage3_config.yaml` would be built from it | Table now reads `0.0000382 eV`, plus an explicit `gun energy = 1.0e-3 eV` row |
| 2 | Roadmap M0 exit gate only required gun energy "not below `minEPhonons`" | **High** — would pass physics-free configs | Replaced with the excitation-threshold chain (new pipeline §3.1.1) **and enforced in code** |
| 3 | M1 still asked for a C++ seed interface | Medium — wasted work on a *blocking* milestone | Marked done; `/random/setSeeds` needs no C++. Item 3 downgraded to "partly done" |
| 4 | M4 ladder `1e4/1e5/1e6` | **High** — pilot would measure two useless fidelities | Replaced with `1e6/1e7`, derived from the measured 3.69e-5 QPs/event |
| 5 | "At most four workers" in roadmap M5 and twice in the pipeline | Medium — 8× under-utilisation | Raised to 32 with the validating evidence; noted the runner default is still 4 |
| 6 | Full scenario set appeared only in M6 confirmation | **High** — selection would optimise the wrong objective | Moved into M4 and M5 as a selection requirement; M6 now reuses the same set |
| 7 | Obsolete "freeze at 382 µeV" remedies in this file were only contradicted elsewhere | Medium — documentation hazard | A3 carries an in-place `SUPERSEDED — DO NOT FOLLOW` banner; A2's remedy corrected in place |

#### The energy gate is now enforced, not merely documented

Problem 2 is the one that could have silently wasted a campaign, and prose alone
is what allowed it to drift, so it is now checked in code:
`assert_excitation_thresholds()` in `stage1_run_simulations.py` enforces

```text
minEPhonons  <  2*setTopGap   <=   E_gun   <   2*setTopFilmGap
```

Why the old gate was insufficient: `minEPhonons` is a numerical tracking cut,
not a physical threshold. Since Change 1 lowered it to 38.2 µeV the old test
became 10× weaker — verified that **50, 100 and 300 µeV all pass "above
`minEPhonons`" and every one yields exactly zero QPs forever**, because
`JunctionKaplanElectrode::IsNearElectrode` requires `PhEnergy >= 2*GapJunc`.

The upper link is equally load-bearing and was not in any earlier draft: above
`2*setTopFilmGap` the Nb ground plane also absorbs, so `total_QPs` stops being
purely junction QPs and the objective silently changes meaning.

Checked **once per design point, not once per run** — `setTopGap` and
`setTopFilmGap` may be swept, so the thresholds move from sample to sample. This
surfaced a fact worth recording: with `setTopFilmGap` swept over ±50%,
`2*setTopFilmGap` ranges **1538 – 4615 µeV**, so the 1.0 meV gun clears the
low end by only **1.54×**. Safe, but it is the binding constraint on raising the
gun energy, not the 1.5 meV transport ceiling.

Verified: passes on the shipped configuration (2.62× above the junction gate),
and blocks all four violation classes — below the gate, exactly on the gate,
`minEPhonons` above the gate (the pre-Change-1 bug), and above the ground-film
gap. Confirmed across 30 design points with per-sample thresholds.

#### Note on problem 6

This is the same caustic-steering hazard as Change 2, and the roadmap had fallen
behind the code: multi-position is now implemented, but the plan still scored
candidates on one site and only introduced the site set at confirmation. That
ordering guarantees the confirmation disagrees with the selection. M5 item 7 now
states the requirement with the measured justification (9.18× site-to-site
spread, CV 60.6%, against a 53% smallest resolvable effect), and M6 explicitly
reuses the selection site set rather than introducing one.

### 2026-08-12 — Change 2 APPLIED: multi-position harness + memory guard

**Supersedes the "ON HOLD" entry below, which is retained for the decision
history.** Ported from `origin/Material_optimization`, adapted to the current
25-variable design and the new energy protocol.

#### New file

- **`sensitivity_memguard.py`** — copied verbatim from the branch (183 lines).
  RSS-based, not `RLIMIT_AS`: a healthy `Main` is ~70 MB RSS but reserves far
  more virtual address space at startup, so any `RLIMIT_AS` low enough to stop a
  runaway promptly would also kill healthy runs. Two independent caps
  (per-sample, aggregate), enforced by killing the whole process group.

#### `stage1_run_simulations.py`

| Area | Change |
|---|---|
| Executor | `ProcessPoolExecutor` → **`ThreadPoolExecutor`**. Workers only spawn `Main` and block in `wait()`, so there is no GIL contention, and the child pid stays visible to the parent's `MemoryGuard`. A process pool could not expose what to kill. |
| Sub-runs | `generate_runfiles` now returns `(sample_name, sub_runs)` and emits one macro per `(replica, position)`. One lattice `config.txt` per **design point**, so `build_run_command` takes `lattice_name` — macro `Morris_7_r1_p3.mac` belongs to design point `Morris_7`. |
| Positions | `build_source_positions()` — scrambled Sobol over ±4 mm of the 10×10 mm substrate (1 mm clear of the walls), z held at the template value. Identical sites for every design point, so position is a common random number. |
| Seeds | `/random/setSeeds` written **immediately before** `/run/beamOn` (anything between them — notably `/main/detector_param/update` — consumes draws and shifts the stream). Keyed by `(trajectory, replica, position)`, so both endpoints of an elementary effect share a stream. |
| Events | `SENSITIVITY_TOTAL_EVENTS` is the single knob; per-sub-run `/run/beamOn` is **derived** and a non-exact split is refused. |
| Failure taxonomy | New `MemoryKilled` (return code `-9`, a budget event, not a physics failure) and `MacroAborted` (**exit 0 but no hits file** — Finding 5). |
| Manifest | Hits paths stored **relative** to `results/<run_id>` so a run directory is portable. |

Deliberately **not** ported, to avoid shipping dead code: the branch's
noise-floor and explicit-design seed keyings. Both are documented in a comment
at `SEED_BASE` because Stage 3 will need them — a noise-floor mode must key by
design point (N identical configurations need N independent streams, or the
measured noise floor is spuriously precise), and a candidate mode must key by
nothing at all (all candidates share one bank so comparisons are paired).

#### `stage2_compute_QPs.py`

- `resolve_hits_path` / `entry_hits_paths` / `load_position_hits` — pool a
  replica's position hits files; accept legacy absolute paths and legacy
  single-`hits_file` manifests.
- **Frames are index-reset before concatenation.** `calculate_QPs` takes
  *positional* indices from `np.where` and then indexes its Series *by label*;
  the two coincide only on a contiguous `0..n-1` index. Verified load-bearing —
  see test 8 below.
- A missing/corrupt position is skipped **individually** and `n_sim` is rescaled
  to the positions that actually contributed, so losing 1 of 16 costs 1/16 of
  the statistics rather than the whole design point — and, critically, does not
  masquerade as a genuine drop in QP yield.
- `qp_summary.csv` gains `design_point`, `replica`, `n_sim`,
  `n_positions_present`, `n_positions_expected`, `QPs_per_primary`. With
  replicas kept separate, `sample_name` alone no longer identifies a
  configuration.

#### `stage2_compute_QPs_sensitivity_analysis.py` (one protective change)

Stage 2b addresses samples as `Morris_<i>` and has no replica handling, so a
multi-replica run would have missed every lookup and reported all-NaN outcomes
— the §12.7 silent-degradation shape exactly. Added `assert_single_replica()`,
which **hard-fails with an explanatory message**. Multiple *positions* still
pass, since those are pooled inside one manifest entry and the name is
unchanged. No other behaviour altered; the script remains outside the Stage 3
graph.

#### Verification performed

| # | Check | Result |
|---|---|---|
| 1 | Single-position regression (defaults) | 1 bank, 1 sub-run/point, names and manifest unchanged in shape |
| 2 | 4 positions × 2 replicas, 80,000 total | 16 sub-runs, `/run/beamOn` **10,000** derived, 8 seed banks |
| 3 | One lattice config per design point | `CrystalMaps/{Morris_0, Morris_1}` only — not per sub-run |
| 4 | Positions differ within a point; identical **across** points and replicas | confirmed — the common-random-number property |
| 5 | Seeds identical across design points in one trajectory | `diff` clean → elementary effects are paired on one stream |
| 6 | `/random/setSeeds` immediately precedes `/run/beamOn` | verified in all generated macros by `verify_generated_seeds` |
| 7 | Pooling arithmetic (synthetic hits, 1+2+3+4 per position) | 10 hits → `total_QPs` 20, `n_sim` 40,000 |
| 8 | Index-reset is load-bearing | without it, pooling raises `ValueError: operands could not be broadcast (2,) (1001,)`; with it, per-electrode `[6, 10]` = expected |
| 9 | Lost position (deleted file) | 3/4 positions, `n_sim` 30,000, `QPs_per_primary` correct |
| 10 | Zero-byte position | 3/4 positions, survivors pooled |
| 11 | **All** positions lost | entry **skipped**, no summary row — never a zero |
| 12 | Non-dividing total (80,001 / 8) | refused with the exact shortfall |
| 13 | Non-power-of-two positions (17) | refused — scrambled Sobol is only balanced at powers of two |
| 14 | `TOTAL_MEM_GB` above host `MemAvailable` | refused (shared host) |
| 15 | Legacy `SENSITIVITY_EVENTS_PER_POSITION` | refused with migration instructions |
| 16 | Memory guard kills a runaway | child grown past a 0.25 GB cap through a `bash -lc` wrapper → group SIGKILLed in 2.0 s, peak 0.252 GB, mapped to `MemoryKilled` |
| 17 | Manifest portability | run copied elsewhere and the **original's** hits emptied → copy still reported 10 hits / 20 QPs from its own files |
| 18 | Stage 2b replica guard | `Morris_0`/`Morris_1` pass; `Morris_0_r0` refused with an explanatory error |

Test 16 specifically exercises the process-*tree* walk: the tracked pid is the
`bash` wrapper and the allocator is a grandchild, which is the same shape as
`bash -lc … Main`. Killing only the wrapper would leave `Main` orphaned at 100%
CPU.

#### New environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SENSITIVITY_N_POSITIONS` | `1` | injection sites per design point (power of two; identical across points) |
| `SENSITIVITY_N_REPLICAS` | `1` | independent CLHEP realisations per design point, kept separate |
| `SENSITIVITY_TOTAL_EVENTS` | `0` (template) | **TOTAL** primaries per design point; per-sub-run `/run/beamOn` is derived |
| `SENSITIVITY_POSITION_SEED` | `20260727` | scrambled-Sobol seed for the site set |
| `SENSITIVITY_POSITION_HALF_SPAN_MM` | `4.0` | half-span of the sampled area |
| `SENSITIVITY_EXPLICIT_SEEDS` | `1` | emit `/random/setSeeds`; `0` reverts to `Main.cc`'s unsafe `clock()` |
| `SENSITIVITY_SEED_BASE` | `20260728` | base of the seed bank |
| `SENSITIVITY_SEED_BANK_ID` | `0` | independent stream family, for held-out confirmation |
| `SENSITIVITY_TOTAL_MEM_GB` | `200` | aggregate RSS ceiling; refuses to start above host `MemAvailable` |
| `SENSITIVITY_PER_SAMPLE_MEM_GB` | `4` | per-sub-run RSS cap (~55× a healthy 70 MB run) |
| `SENSITIVITY_MEM_POLL_SECONDS` | `5` | guard poll interval |

Reference invocation matching the branch's validated protocol:

```bash
SENSITIVITY_N_POSITIONS=16 SENSITIVITY_N_REPLICAS=2 \
SENSITIVITY_TOTAL_EVENTS=4000000 \
SENSITIVITY_MORRIS_SEED=... python -u stage1_run_simulations.py
# workers (64) and the memory budget (200 GB) are now defaults; override only
# to reproduce the original 32-worker screen: SENSITIVITY_MAX_WORKERS=32
```

#### Follow-ups this creates

1. ~~`SENSITIVITY_MAX_WORKERS` still defaults to 4.~~ **Changed 2026-08-13 to
   64, with the aggregate budget lowered to 200 GB** — see the entry below.
2. **16 sites is not established as converged** — run the nested 16/32/64 Sobol
   check before treating the site set as final (§12.6).
3. **Stage 2b is now replica-incompatible by design.** Any Morris screening that
   needs it must run with `SENSITIVITY_N_REPLICAS=1`.
4. The four seam guardrails from the on-hold entry are now satisfied by the
   implementation itself, except the **trial-cache hash** — there is no Stage 3
   trial cache yet. When `stage3_trial_runner.py` is written, its cache key and
   ledger rows must include the position set and `SEED_BANK_ID`.

### 2026-08-12 — Change 2 ON HOLD: multi-position injection scenario

**(Superseded by the entry above; retained as decision history.)**

**Deferred by decision.** Stage 3 continues on the single template injection
site `3.47 -2.249825 0.259875 mm` for now. Recorded here so the decision is
explicit rather than an omission.

**What is being deferred:** 16 scrambled-Sobol injection sites × 2 replicas,
identical across candidates, per §12.6.

**Known cost of deferring, quantified.** Across those 16 sites *with the
material held fixed*, mean QPs per site ranged **2.515 → 23.09 (9.18×), CV
60.6%**. That is the same order as the candidate effects Stage 3 must resolve
(smallest resolvable effect 53%; leading μ\* ~124% of mean). Injection site is
therefore a lever as strong as the material signal — and because the elastic
tensor and orientation *steer the caustic*, a candidate can post a large
apparent improvement by moving focusing away from the single test site without
reducing device-wide QP damage. Morris was passive and merely absorbed this as
variance; an optimizer will actively find and exploit it.

**Where the boundary falls:**

| Milestone | Safe on one site? |
|---|---|
| M1 physics gaps, M2 catalog, M3 evaluator | yes — unaffected |
| M4 noise/fidelity calibration | must be **redone** after positions are added; variance structure differs. Cheap (5–10 candidates). |
| M5 optimization campaign | **no** — hard boundary |
| M6 confirmation | no — must match M5 |

**Seam to build now, so adopting it later stays cheap** (~20 lines of
difference versus a launcher rewrite):

1. `evaluate(config, fidelity, scenario, seed)` — `scenario` carries a position
   **list**, even at length 1. Never hardcode a single position.
2. **Hash the position set into the trial cache key and write it into every
   ledger row.** Old trials are then invalidated automatically instead of being
   silently pooled with new ones — that pooling is precisely the §12.7
   silent-degradation shape.
3. Sum over positions in the objective adapter, not in the runner, so the
   pooling path exists from day one with n=1.
4. Write the launcher as a fan-out over a list: §12.2 forces one process per
   position regardless, since `/g4cmp/HitsFile` cannot be re-pointed after
   `/run/initialize` and a second `/run/beamOn` truncates.

**When adopted**, also run the convergence check the audit asks for — nested
16/32/64-point Sobol sets or independent scrambles — since 16 sites is *not*
established as converged.

### 2026-08-12 — Corrections to this document and to the pipeline doc

- **§12.5 of `STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md` overstated the energy
  protocol's benefit.** The headline "λ 0.93 → 147.4" is not like-for-like:
  0.93 was measured at 1e5 events, 147.4 at 4e6. Normalised, the protocol is
  worth ~**4×** in yield per event, not two decades; the rest is the 40× larger
  event budget. Most of the 104% → 12.4% relative-SD gain is likewise the event
  count (`SD/mean = 1/sqrt(λ)`). The section now carries the decomposition.
  The corrected claim is a physics argument, not a statistics one.
- **The "78% zero signal" figure does not transfer to Stage 3.** It was
  dominated by `setEnergy` being *swept* across the 2Δ gate (~47% of design
  points structurally dead). Stage 3 fixes the gun energy, so that failure mode
  disappears regardless of which value is chosen.
- **16 Sobol positions vs 17 electrodes — unrelated quantities**, clarified in
  §12.6. The 17 are `/main/electrode_param/setXLocations`/`setYLocations`: Al
  junctions where QPs are *counted*, present in every run. The 16 are
  `/main/gun/setPosition`: where the phonon burst *originates*, sampled over
  ±4 mm of the 10×10 mm substrate. 16 is a power of two because scrambled Sobol
  only carries its balance guarantee at powers of two. The near-match in count
  is coincidence.

### Open decisions carried forward

1. **The 1.0 meV value.** The [0.6, 1.5] meV window's floor was `2Δ_Al` at the
   *top* of the old `setTopGap` sweep. Stage 3 freezes `setTopGap` at 191 µeV,
   so the gate is 382 µeV and a lower fixed energy would also be legal. 1.0 meV
   is a defensible mid-range choice, not a derived optimum.
2. **Transport regime across candidates.** The 1.5 meV ceiling came from Si's
   isotope MFP (137 µm vs a 525 µm substrate). That is a *Si* argument. Across a
   candidate set the same energy may be ballistic for one material and diffusive
   for another. Check the MFP spread over the catalog before claiming a
   like-for-like comparison.
3. **The single injection site is poorly placed for the interim.**
   `(3.47, -2.25)` sits outside the ±3 mm electrode span, near a corner —
   plausibly a worst case for caustic sensitivity. Moving it toward the centre
   would make M1–M3 plumbing data less misleading. Not done, because it is part
   of the on-hold Change 2 and would alter the objective.
4. **Re-run the Si baseline.** Any Si control or noise number measured under the
   old energy protocol is void.

---

## Remediation plan, in execution order

### Step 1 — Freeze the contract (unblocks Milestone 0)

Covers **A7**, **A8**.

**A8 — Nothing is committable yet.** `sensitivity_params.py`,
`stage1_run_simulations.py` and all five `sensitivity_template_beamOn*.mac` are
modified in the working tree; `parameter_optimization/` is untracked. Milestone 0
("tag the commit, hash the five templates") has nothing to freeze against.

> **Remedy:** commit the `parameter_set.txt` application as one changeset, add
> `parameter_optimization/`, then tag it (e.g. `stage3-contract-v1`) and record
> the five template SHA-256 hashes in `stage3_config.yaml`.

**A7 — README and mimir run record are stale.** Both state 50 parameter groups /
53 expanded variables / `128 * 54 = 6912` samples. The current set is
**26 groups / 26 variables / `128 * 27 = 3456` samples** (5 electrode + 7
detector + 1 G4CMP + 7 config + 6 QP-ODE). The cross-machine md5 verification
recorded in `SensitivityAnalysis_Morris_run_record_Mimir.md` therefore validates
the *old* design and is no longer evidence of anything.

> **Remedy:** update the counts in `README.md` and both run records; re-run the
> Mac↔mimir byte-identity check with `SENSITIVITY_MORRIS_SEED=12345` against the
> 26-variable design and replace the md5 table.

---

### Step 2 — Parameter-contract and template fixes (most of Milestone 1, no C++)

Covers **A2**, **A3**, **A4**, **A5** (partly), plus **B3**.

**A2 — Five swept variables that `parameter_set.txt` never classifies.**
`/main/gun/setEnergy`, `/main/electrode_param/setHeight`, `setWidth`,
`setIsland`, `setIslandSpacing` are still Morris dimensions
(`sensitivity_params.py:43-49`). None belongs in a Stage 3 material search:
the first is an injection condition, the rest are device geometry.

Sub-note: `setIsland` / `setIslandSpacing` are **not** inert. They drive the
*bottom*-surface `WaffleKaplanElectrode` pattern
(`PhononDetectorConstruction.cc:383,395`), not the 17 top junctions.

> **Remedy (partly APPLIED 2026-08-12):** move all five into the Stage 3 `fixed`
> block of `stage3_config.yaml` and into `FIXED_MACRO_COMMANDS`.
> `setEnergy` is **done** — but frozen at **`1.0e-3 eV`, NOT the `382.0e-6`
> originally suggested here** (see Change 1). Remaining frozen values:
> `setHeight 10.`, `setWidth 10.`, `setIsland 200. um`,
> `setIslandSpacing 50. um`.

**A3 — Gun energy sits exactly on the `minEPhonons` cut.**

> ### SUPERSEDED 2026-08-13 — DO NOT FOLLOW THE REMEDY IN THIS SECTION
>
> The analysis below is still accurate as a description of the *old*
> configuration, but its recommendation to freeze at 382 µeV is **withdrawn**.
> The energy protocol now freezes the gun at **1.0 meV** with `minEPhonons` at
> **38.2 µeV** (Change 1 / pipeline §12.5), and the correct gate is the
> excitation-threshold chain in pipeline §3.1.1, not "above `minEPhonons`".
> Retained for decision history only.

`G4CMPTrackLimiter::BelowEnergyCut` uses a strict `<`
(`~/src/G4CMP_htseng/library/src/G4CMPTrackLimiter.cc:82`), and
`382.0e-6 == 0.000382` is exactly true in IEEE-754 double. So the frozen
382 µeV primary **does** survive the cut — the fixed scenario is usable. But
every downconversion daughter dies immediately, and the Morris range
`[191, 573] µeV` leaves roughly half the old design simulating nothing.

> **Remedy:** freeze at 382 µeV and record in `stage3_config.yaml` that the
> value is exactly `minEPhonons` and survives only because the comparison is
> strict. Add a Phase-B assertion that a 1e4-event Si run at this energy
> produces a non-zero hits file, so a future G4CMP change from `<` to `<=`
> fails loudly instead of silently returning `Q = 0` for every candidate.

**A4 — The three interface-absorption values are unenforced.** `setTopAbs`
0.795, `setTopFilmAbs` 0.745, `setBotAbs` 0.736 sit in the templates but appear
neither in the sweep nor in `FIXED_MACRO_COMMANDS`. A template edit changes them
silently, and they become *computed* values in Stage 3.

> **Remedy:** add all three to `FIXED_MACRO_COMMANDS` now (pinning the Si
> baseline), then have the Stage 3 resolver overwrite them per candidate once
> `interface_transmission.py` exists (Step 4).

**A5 — `setMiller` is never written by Stage 1.** It is commented out in
`detector_params` (`sensitivity_params.py:64`) and absent from
`FIXED_MACRO_COMMANDS`, so the template's `0 0 1` is silently inherited.
`parameter_set.txt` asks for a Fibonacci-sphere scan here — see **B1**, which is
the real problem.

> **Remedy (this step):** pin `setMiller 0 0 1` in `FIXED_MACRO_COMMANDS` so the
> baseline is explicit. Promote it to a decision variable in Step 3.

**B3 — Deterministic seeding and completion markers are free.** `Main.cc:40-41`
calls `CLHEP::HepRandom::setTheSeed((unsigned)clock())` at the top of `main()`,
**before** the UI manager executes the macro. Geant4's built-in
`/random/setSeeds <s1> <s2>` placed in the macro therefore overrides it with no
C++ change. The same applies to the completion marker via `/control/shell`.

> **Remedy:** add to each Stage 3 macro —
> `/random/setSeeds <s1> <s2>` before `/run/beamOn`, derived deterministically
> from `(campaign_id, scenario_id, replicate_id)`; and
> `/control/shell touch <trial_dir>/DONE` as the final line. Treat a trial as
> `success_*` only when `DONE` exists **and** the hits file parses **and** the
> log contains `Run terminated`. This collapses Milestone 1 items 2 and 3 from
> C++ work to template edits.
>
> Verify in Phase B that two runs with the same seed produce byte-identical hits
> files — this is the single most important gate before any optimization,
> because common random numbers are what make paired candidate comparisons
> affordable at these QP counts.

---

### Step 3 — Rewrite the orientation design (B1, hard blocker)

**B1 — The Fibonacci-sphere orientation scan cannot be implemented as written.**
`STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md` §5 says to generate directions with a
Fibonacci sphere and "map each retained direction to the exact representation
accepted by `setMiller`". No such mapping exists:

```cpp
// ~/src/G4CMP_htseng/library/include/G4LatticePhysical.hh:71
void SetMillerOrientation(G4int h, G4int k, G4int l, G4double rot = 0.);
```

`fMillerIndices` is a `std::vector<G4double>` whose elements are passed straight
into that `G4int` signature (`PhononDetectorConstruction.cc:324,328`). Arbitrary
unit vectors are **truncated toward zero**, so most Fibonacci directions would
silently collapse to `(0,0,0)` — degenerate, since
`norm = (h*b0 + k*b1 + l*b2).unit()` — or to `(1,0,0)`. A naive scan would look
like it ran and would actually sample two or three orientations.

> **Remedy:** replace §5 with an integer-Miller enumeration.
> 1. Enumerate `(h,k,l)` with `|h|,|k|,|l| <= N` (start `N = 3`), reject `(0,0,0)`.
> 2. Reduce by the 48 operations of the cubic point group `m-3m`, and by
>    `n -> -n`. For `N = 3` this yields the physical family
>    {001, 011, 111, 012, 112, 013, 113, 122, 123, 133, 233, 223, …} — on the
>    order of 10–20 distinct normals, which is the right size for a first pass.
> 3. Cross each retained normal with the **continuous** `setLatticeDeg`
>    rotation, which is a genuine `G4UIcmdWithADouble`
>    (`PhononDetectorMessenger.cc:149`) and needs no discretization.
> 4. Store the integer triple, the resulting unit normal, and the written macro
>    lines in the ledger.
>
> Refining `N` is the natural orientation fidelity level, replacing "32–128
> symmetry-reduced Fibonacci directions".

---

### Step 4 — Interface transmission calculator (still the one missing physics)

The acoustic transmission calculator promised in `parameter_set.txt`
("I will provide a script") is not in the repository. It is the only genuinely
absent piece of physics; everything else is plumbing.

> **Remedy:** write `parameter_optimization/interface_transmission.py` computing
> the phonon transmission probability from candidate density + derived
> velocities against the fixed interface materials (Al junction, Nb ground
> plane, Cu back plane). Unit-test that it reproduces the Si baselines
> **0.795 / 0.745 / 0.736** within a declared tolerance. Gate: reject any
> candidate whose coefficients fall outside `[0,1]`.

---

### Step 5 — Restrict campaign 1 to NIST-representable substrates (B2)

**B2 — The hybrid-pseudo-material blocker is real, but smaller than the plan
assumes.** Confirmed at source: the lattice density comes from the **G4Material**,
not from `config.txt` —

```cpp
// ~/src/G4CMP_htseng/library/src/G4LatticeManager.cc:126
newLat->SetDensity(Mat->GetDensity());
```

— and `G4CMPPhononKinematics` divides the Christoffel matrix by it
(`G4CMPPhononKinematics.cc:28,120`). Note also that although `useKVsolver`
defaults to `0` (`G4CMPConfigManager.cc:106`), `G4LatticeLogical::Initialize`
still calls `FillMaps()`, which builds the group-velocity lookup table from the
tensor **and** that density (`G4LatticeLogical.cc:229-247`). So editing the
tensor while leaving `G4_Si` really does produce an inconsistent pseudo-material,
exactly as the pipeline document warns.

The good news the plan misses: **`/main/detector_param/setSubstrateG4Name`
already exists** and feeds `G4NistManager::FindOrBuildMaterial`
(`PhononDetectorConstruction.cc:114`). A first campaign restricted to
NIST-representable substrates needs **zero C++ changes**.

> **Remedy:**
> 1. Scope campaign 1 to NIST materials — `G4_Ge`, `G4_GaAs`, `G4_DIAMOND`,
>    `G4_ALUMINUM_OXIDE`, `G4_SILICON_DIOXIDE`, … — plus the `G4_Si` control.
> 2. Stage 1 hardcodes `LATTICE_MATERIAL = "Si"`
>    (`stage1_run_simulations.py:133`) and writes
>    `CrystalMaps/<sample>/Si/config.txt`. The lattice directory name must track
>    `setSubstrateName`; parameterize both together.
> 3. Add a null guard: `FindOrBuildMaterial` returns `nullptr` for an unknown
>    name and nothing currently checks it.
> 4. Defer arbitrary Materials Project candidates — those need new C++ to build
>    a `G4Material` from formula + density — to campaign 2.
>
> **A6 (related) — commented-out config entries inherit Si values, not nothing.**
> `generate_runfiles` rewrites lines in the *Si* `config.txt` template
> (`stage1_run_simulations.py:617-701`), so every commented-out entry persists at
> its Si value in every generated config. Most are charge-carrier parameters,
> unused by a phonon-only gun. Two are not:
> **`LDOS/STDOS/FTDOS = 0.093/0.531/0.376`** are Tamura's Si phonon
> mode-density fractions — material-dependent and phonon-relevant — and `Debye`
> is *force*-pinned to Si's `15 THz` by `FIXED_CONFIG_COMMANDS`. Both must
> become per-candidate values (or explicit, recorded approximations) before any
> non-Si result is claimed. Also note `scat`, `decay`, `decayTT` are currently
> tuned freely rather than derived from the candidate.

**B8 (related) — films are hybrids too.** Tuning `setTopFilmVSound/Gap/
PhLifetime` while `setTopFilmSourceMat` stays `G4_Nb` is self-consistent for
KaplanQP (all film properties come from the `G4MaterialPropertiesTable`,
`PhononDetectorConstruction.cc:152-170`), but `setTopFilmAbs` must be recomputed
from the film's own density and velocities, and no film catalog exists.

> **Remedy:** start substrate-only, as `STAGE3_START_ROADMAP.md` Milestone 0
> item 5 already permits. Make that the default rather than a fallback.

---

### Step 6 — Fix the catalog builder (B7)

**B7 — `ElasticityTensors.py` discards its own database query.** Beyond the
issues the plan lists, there is a concrete bug: `summary_docs` is a
database-wide filtered search (cubic, `energy_above_hull <= 0.10`, non-metal,
non-magnetic, experimentally verified) at
`ElasticityTensors.py:124-147`, but the output loop iterates the **three
hardcoded IDs**:

```python
# ElasticityTensors.py:187
for mpid in material_ids:
```

Only IDs present in *both* survive; the entire filtered result is thrown away.
`mp-13` (Fe) is a metal, is removed by `is_metal=False`, and is then reported as
`NOT FOUND` — a misleading message for a material that plainly exists.

Additionally: `density` is requested in `fields` but written to neither the CSV
nor the JSON; `C11`/`C12`/`C44` are never extracted from the 6×6; there is no
cubic-symmetry or Born-stability validation; and no API version, retrieval
timestamp, tensor convention, or units are recorded.

> **Remedy:** split into two explicit modes (`--material-ids` vs
> `--filtered-search`) and iterate whichever produced the docs. Emit density,
> `C11/C12/C44`, tensor convention, units, MP API/database version, retrieval
> timestamp, and full filter provenance. Validate cubic symmetry and
> `C11-C12>0`, `C11+2*C12>0`, `C44>0` per row; record a machine-readable
> rejection reason for every dropped candidate; never impute a missing property
> with a `0.5x–1.5x` interval. Freeze the snapshot with a checksum.
>
> `mp_api` is **not installed** in the `G4CMP` conda env — see Step 7.

---

### Step 7 — Measure cost, then choose the optimizer (B5, B6, B4, B8)

**B4 — The objective definition is sound; no change needed.** With
`/main/sensor/setHitType Junction`, `PhononSensitivity::IsHit` requires
`fJunctionElectrode->GetJunctionHit()`
(`~/BNL_G4CMP_HT_Feb27/Main/src/PhononSensitivity.cc:122-124`), so **only Al
junction absorptions are written to the hits file**. Nb ground-plane absorptions
are excluded — and are mostly impossible anyway, since sub-gap 382 µeV phonons
cannot reach Nb's `2Δ = 3.08 meV`
(`JunctionKaplanElectrode.cc`, film branch requires `PhEnergy >= 2*GapFilm`).

Measured on run `morris_mimir_f52fb742`: of **1754** recorded top-surface hits,
**1753** fall inside a 10 µm junction footprint. So `total_QPs` genuinely is
junction QP damage. With `setTopGap` frozen at 191 µeV the conversion
`round(E_dep / gap)` is comparable across candidates. The QP-ODE parameters do
not affect `total_QPs` (they only enter `DG`), so freezing them is correct.

Minor: `calculate_QPs` assigns hits to the nearest electrode with no radius
cutoff (`stage2_compute_QPs.py:52-58`) — harmless here precisely because only
junction hits are recorded, but it would silently mis-bin if the hit type ever
changed to `Top`.

**B5 — The fidelity ladder is off by roughly two decades.**

> **SUPERSEDED 2026-08-12 — see `STAGE3_MATERIAL_OPTIMIZATION_PIPELINE.md`
> §12.5.** The low yield quantified below is largely an *artifact* of the
> `382 µeV` gun energy against a `382 µeV` `minEPhonons` cut, and the
> `Material_optimization` branch already fixed it: gun energy → [0.6, 1.5] meV,
> `minEPhonons` → 38.2 µeV, which lifts λ from **0.93 to 147.4** per 4e6 events
> and cuts per-evaluation relative SD from **104% to 12.4%**. The direction of
> the finding stands — the plan's `1e4/1e5/1e6` ladder is wrong — but re-derive
> the corrected ladder against λ ≈ 147, not against the numbers below. Also
> note A3's recommendation to freeze at 382 µeV is withdrawn for the same
> reason.

Across the 12 existing runs with a readable `qp_summary.csv`, the measured yield is
**3–5 × 10⁻⁵ QPs per primary phonon** (`n_sim` 2×10⁶–1×10⁷, `total_QPs`
68–259, zero fraction 0.00 everywhere). Therefore:

| Plan level | Events | Expected `total_QPs` | Poisson SE | Usable? |
|---|---:|---:|---:|---|
| F0 | 1e4 | ~0.4 | — | no — drop now, do not spend a pilot on it |
| F1 | 1e5 | ~4 | ~50% | no — cannot rank candidates |
| F2 | 1e6 | ~40 | ~16% | screening only |
| — | **1e7** | ~400 | ~5% | selection / confirmation |

> **Remedy:** replace the `1e4 / 1e5 / 1e6` ladder with **F1 = 1e6,
> F2 = 1e7**. Milestone 4 should *confirm* this, not discover it. Keep the
> rank-correlation measurement, but run it between 1e6 and 1e7.

**B6 — The algorithm recommendation is mis-sized for the measured cost.**
The mimir verification run did 54 samples at 1e4 events on 4 workers in
**18 s** (`SensitivityAnalysis_Morris_run_record_Mimir.md`) ≈ **1.3 core-seconds
per sample**, so roughly 130 core-seconds at 1e6. mimir has **192 cores and
503 GB RAM**; the roadmap's "at most four concurrent simulations" is a legacy
constraint inherited from the `QPLim = 1` memory blowup, which `QPLim = 3` plus
`SENSITIVITY_SAMPLE_TIMEOUT` already neutralizes.

At 32 workers, one candidate at 1e6 × 5 seeds costs ~11 core-minutes, so a
**1000-material catalog enumerates in about 6 wall-clock hours**. Multi-fidelity
SMAC/Hyperband is machinery for expensive evaluations; these are not expensive
evaluations.

> **Remedy:**
> 1. **Primary method: full catalog enumeration at F1 = 1e6 with 5 common
>    seeds, followed by racing** — allocate additional seeds only to the
>    contending top ~20, then confirm the top 3–10 at F2 = 1e7 with held-out
>    seeds. Exact, no surrogate to validate, and it dominates any Bayesian
>    method when you can afford to evaluate everything.
> 2. **Keep SMAC / Ax-BoTorch for the continuous property-space study only**
>    (pipeline §3.2 interpretation 2), where the space is not enumerable. That
>    is where a surrogate actually earns its complexity.
> 3. Random search stays as the reported baseline, but against exhaustive
>    enumeration it is a formality.
> 4. **Before committing:** re-measure per-trial wall time and peak RSS at 1e6
>    events with 32 workers. The 1.3 s figure comes from a run in which roughly
>    half the samples were below `minEPhonons` and exited immediately, so it is
>    a lower bound.
>
> Statistical note: at ~40 counts per trial, prefer a Poisson or
> negative-binomial treatment of `q = Q/N` over the plan's
> `mean + λ·SE` with `λ = 1`; report bootstrap paired-difference intervals
> against the Si control.

**B8 — Environment and scenario set.**

*Missing packages.* None of `smac`, `ConfigSpace`, `optuna`, `ax`, `botorch`,
`torch`, `mp_api` are installed in the `G4CMP` conda env, which currently has
numpy 2.4.6 / pandas 3.0.3 / scipy 1.17.1 — new enough that SMAC's pins may
conflict.

> **Remedy:** build a **separate** conda env for catalog + optimization work.
> Do not disturb the working `G4CMP` env, which Stage 1 depends on.

*Single injection point.* The templates define exactly one:
`/main/gun/setPosition 3.47 -2.249825 0.259875 mm` — asymmetric, off to one side
of the electrode array (which spans ±3 mm), and 2.6 µm below the top surface.
Optimizing against one favourable injection point can select a material that
performs worse elsewhere, as pipeline §8 warns.

> **Remedy — check this before writing new code.** Results directory
> `morris_mimir_f52fb742-8b08-4ecf-9e46-d869a77750cd` was produced by a
> **newer pipeline that is not in this repository**. Its `qp_manifest.jsonl`
> carries `n_sim_per_position: 250000`, a 16-entry `hits_files` list per sample
> (`_p0` … `_p15`, 4×10⁶ events total), and `design_point` / `replica` fields.
> That is precisely the multi-position scenario set and replicate structure
> §8 asks for, and it has already run successfully. Recover that code before
> reimplementing it.

---

## Open items deliberately not scheduled

- **`chi2` method in `stage2_compute_QPs_sensitivity_analysis.py`** remains a
  documented `NotImplementedError` placeholder pending 17 per-electrode measured
  curves. Stage 3 does not call it, and the roadmap correctly keeps Stage 2b out
  of the execution graph. No action.
- **`fJunctionHit` is a mutable member** read across `IsNearElectrode` /
  `AbsorbAtElectrode` / `PhononSensitivity::IsHit`. Safe under the current
  process-level parallelism; it would break under Geant4 multithreading. Record
  only — do not enable G4 MT.
- **Third-order stiffness `dyn`** stays pinned at Si values for all candidates,
  per `parameter_set.txt` ("too complicated to calculate, no observed
  sensitivity"). Accepted, but it is a Si-specific constant in a non-Si
  simulation and belongs in the reported limitations.

---

## Claim scope for the final report

Unchanged from the pipeline document, and worth restating because Step 5 narrows
it further: the defensible claim is *"candidate X reduces expected total
junction QPs relative to the frozen Si baseline, under scenario set S, event
count N, NIST-representable substrates only, and this simulation model,"* with
uncertainty — not a claim about logical error rates, and not a claim about
arbitrary Materials Project compounds until campaign 2 adds candidate-specific
`G4Material` construction.
