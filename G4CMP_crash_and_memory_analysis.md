# Geant4/G4CMP Crash & Memory Analysis — Morris Sensitivity Pipeline

Date: 2026-07-16
Analyst: Claude Code (investigation run live on the M4 Max machine, all
numbers below are measured, not estimated, unless labeled otherwise)

This report documents the root-cause investigation of two distinct failure
modes observed while dry-running the Morris sensitivity pipeline (at the
time, `SensitivityAnalysis_Morris_Mac_Unified.py` /
`SensitivityAnalysis_Morris_Mac_M4Max.py` driving the BNL_G4CMP `Main`
application; both have since been merged into
`stage1_run_simulations.py`, and the old scripts
archived under `old_version/` -- see
`SensitivityAnalysis_Morris_run_record_Mac_M4Max.md` for the current
layout. The findings and root causes below are unaffected by that merge,
since the underlying Geant4/G4CMP execution path did not change):

1. **A segmentation fault (SIGSEGV, exit code 139) at process teardown**,
   first seen on sample `Morris_9` of the 2026-07-16 parallel dry run.
2. **A runaway process observed at 28 GB → 79 GB RSS and climbing** with no
   event-loop progress, first seen on a `beamOn 1e6` debug-mode sample,
   killed manually before it exhausted the machine's 128 GB.

Both are now fully root-caused. They are **unrelated to each other** and
**unrelated to the Python orchestration** — both live in the C++ layer
(one in the G4CMP library's physics loop, one in G4CMP's interaction with
Geant4's exit-time static destruction).

---

## Executive summary

| # | Symptom | Root cause | Severity for production |
|---|---------|-----------|------------------------|
| 1 | Process hangs forever; memory flat **or** growing without bound (observed up to 79 GB) | **Infinite loop in `G4CMPKaplanQP::AbsorbPhonon`** when a film's `lowQPLimit` (macro `setTopQPLim` etc.) is **1**: a quasiparticle can then never fall below the exit threshold, because the phonon-emission sampler's floor is strictly above the gap energy. Triggered the first time a phonon breaks a Cooper pair in that film. | **Critical.** The Morris sweep samples QPLim from [1,5]; every sample that draws QPLim=1 on a pair-breaking-capable film **will** hang at production event counts (trigger probability ≈ 1 at 1e6 events). ≈ ¼ of live samples are at risk. |
| 2 | SIGSEGV during `exit()`, after all physics and output completed ("Visualization Manager deleting..." is the last log line) | **Use-after-free in static destruction order**: `G4CMPParticleChangeForPhonon` (member of `G4CMPPhononBoundaryProcess`) grabs the track's `G4TouchableHandle` on *every step* in `Initialize()` but only clears it when a reflection is applied. The stale handle held after the last phonon step is released inside `~G4ProcessTable()` at exit — *after* the touchable's allocator pool has already been torn down. | **Cosmetic / operational.** Physics and hits output are complete before the crash. Stochastic (~0.4% of runs; depends on heap page reuse). The orchestrators' per-sample exception handling (added 2026-07-16) already contains it. |
| 3 | "Is ~80 GB normal for the 1e6-phonon template?" | **No.** A healthy 1e6-event run peaks at **65–80 MB** RSS and takes **~13 s**. Anything in the GB range means the run is inside failure mode #1. | — |

A fourth, bonus finding (sweep design, not a crash): the pinned
`/g4cmp/minEPhonons 0.000382 eV` kills any primary phonon whose sampled gun
energy is below 0.000382 eV **at creation**. Two of the four Morris levels
for `/main/gun/setEnergy` (0.000191, 0.000318 eV) are below this threshold,
so ~half of all samples simulate literally nothing (fast runs, empty hits
files). This exactly matches the old production run
(`morris_mac_77e4cc29`): samples 0–41 (E=0.000318) → 0 hits; samples 42–89
(E=0.000573) → 2–47 hits.

---

## Environment under test

- App: `~/geant4_workdir/bin/Darwin-clang/Main` (built 2026-04-16), source at
  `~/Downloads/BNL_G4CMP_HT_Feb27/Main` (identical to `BNL_G4CMP-main` copy).
- G4CMP: `~/Geant4/G4CMP`, tag **g4cmp-V10-01-01** (HEAD `558c3ce5`,
  2026-02-24; library built 2026-03-30, so all fixes through G4CMP-585 are in
  the binary).
- Geant4 10.7.4, macOS 15.7.7 arm64, 128 GB RAM.
- Templates: `sensitivity_template_beamOn10000.mac` (fast probe) and
  `sensitivity_template_beamOn1e6.mac` (production), identical except
  `/run/beamOn`.
- Test harness (macro generator, per-sample lattice generator, env wrapper
  with watchdog + peak-RSS capture) was built in the session scratchpad
  (`.../scratchpad/g4probe/`); it is disposable, all conclusions are
  reproducible from the commands in the Appendix.

---

## Finding 1 — Infinite loop / unbounded memory in `G4CMPKaplanQP` when QPLim = 1

### The mechanism (source-level)

File: `~/Geant4/G4CMP/library/src/G4CMPKaplanQP.cc` (V10-01-01).

When a phonon is absorbed into a superconducting film,
`G4CMPKaplanQP::AbsorbPhonon()` runs a lumped Kaplan cascade:

```cpp
while (!qpEnergyList.empty() || !phononEnergyList.empty()) {
  ... CalcQPEnergies(...)        // phonons above 2Δ break pairs → 2 QPs
  ... CalcPhononEnergies(...)    // every QP emits one phonon per pass
  ... CalcReflectedPhononEnergies(...) // phonons escape / are kept / dropped
}
```

A quasiparticle leaves the cascade only via `CalcQPAbsorption()`:

```cpp
if (qpE >= lowQPLimit*gapEnergy) {
  qpEnergies.push_back(qpE);          // QP survives to next pass
} else if (qpE > gapEnergy) {
  ...                                 // QP absorbed (radiates remainder)
} else {
  EDep += qpE;                        // QP absorbed
}
```

and the QP's energy after each phonon emission comes from
`PhononEnergyRand()`, whose rejection sampler has a hard floor:

```cpp
G4double xmin = gapEnergy + gapEnergy/BUFF;   // BUFF = 1000
// new QP energy = xtest ∈ (xmin, currentE)  → ALWAYS > gapEnergy
```

Therefore the post-emission QP energy is **always strictly greater than
`gapEnergy`**. With `lowQPLimit = 1`, the survival condition
`qpE >= 1·gapEnergy` is **always true**: the QP is immortal. It re-emits a
phonon on every pass, its energy converges asymptotically to
`gap·(1+1/1000)` from above, and the `while` loop never terminates. The
event never finishes; the process appears "hung" at 100% CPU.

For any `lowQPLimit ≥ 2` the loop is well-behaved: after at most a few
emissions, `qpE` drops below `2·gapEnergy` and the QP is absorbed.

### Where the memory goes

Each pass of the stuck loop appends emitted phonon energies to internal
vectors; phonons which "escape" the film are appended to the caller's
`reflectedEnergies` vector, which **grows monotonically and is never
drained** because `AbsorbPhonon` never returns. The observed growth *rate*
depends on secondary parameters (film thickness, phonon lifetime → escape
probability per pass; `/g4cmp/temperature` → thermal dropping of low-energy
phonons; `kaplanKeepPhonons` config), which is why two hangs can look
different:

- The 2026-07-16 killed debug run (`TopQPLim 1`, gun 0.000573 eV,
  `TopGap 0.000286` eV, i.e. E ≈ 2Δ within 0.2%): **fast-growth regime**,
  measured 28 GB → 79 GB in ≈ 6 minutes (≈ 140 MB/s), still climbing when
  killed at 16 min.
- The controlled reproduction below (defaults, `TopGap 191 µeV`, E = 3Δ):
  **flat-memory regime**, RSS pinned at 74 MB while spinning forever (list
  growth is slow because per-pass cost grows with the retained-phonon list).

Both regimes are the same bug; only the bookkeeping rate differs.

### Preconditions (all three required)

1. A film with `lowQPLimit = 1` — in this app: `setTopQPLim 1`,
   `setTopFilmQPLim 1`, or `setBotQPLim 1` (macro int-rounded from the
   Morris sample; the [1,5] sweep at 4 levels yields {1, 2, 4, 5}).
2. The incident phonon can break a pair in that film: `E ≥ 2·gap(film)`.
   - Top junction (Al): gap sweep 95.5–286.5 µeV → 2Δ = 191–573 µeV; the
     live gun energies (446, 573 µeV) satisfy this for **all** sampled gaps.
   - Top film (Nb): 2Δ ≥ 1.54 meV > any gun energy → **never** at risk.
   - Bottom (Cu): `BotGap = 0` → no pair breaking → **never** at risk.
   So in this template **only `setTopQPLim` matters**.
3. A phonon actually reaches a top-junction island and is absorbed there.
   Measured interaction rate ≈ 10⁻⁵–10⁻⁴ per event (geometry-dependent;
   islands cover ≈ 0.7% of the top surface and the caustic gun fires
   downward). Hence:
   - at 10⁴ events: ≈ 10–30% chance per at-risk sample (observed: 3 of 11
     at-risk samples hung in one batch, 3 of 55 at-risk reruns in another);
   - at 10⁶ events (production): trigger probability ≈ **1**. Every at-risk
     sample hangs.

### Empirical proof

Identical macros, stock crystal config, only `setTopQPLim` differing:

| Run | Events | Result |
|-----|--------|--------|
| `setTopQPLim 2` | 1e6 | **12.6 s, 66 MB RSS, 15 hits, exit 0** |
| `setTopQPLim 1` | 1e6 | **killed by watchdog at 70 s and again at 75 s (two runs); never completes** |
| `setTopQPLim 1` | 1e4 | completes (interaction usually doesn't occur in 10⁴ events) |

Batch evidence (40 fresh Morris samples, 10⁴ events each): the design's
first trajectory gave samples 0–10 `TopQPLim=1` + live energy + `WallAbs=0`
(no wall absorption → maximal island traffic). **All 6 hangs observed today
(Morris_1, 5, 10 in the first pass; Morris_2, 6, 7 across 5 stress
repetitions) were in this at-risk group; none of the 29 non-risk samples
ever hung (182/182 clean).**

Retrospective evidence: in the old successful production run
(`morris_mac_77e4cc29`, 90 × 1e6 events, ~10 s each), **no live sample had
TopQPLim = 1** (all were 2 or 4 — verified from its `MorrisSequence.csv`).
That run survived by luck of the Morris trajectory, not because the problem
is rare.

### Why this answers the "is 79 GB normal?" question

No. Healthy runs of the 1e6-event template measured today:

| Configuration | Events | Wall time | Peak RSS |
|---------------|--------|-----------|----------|
| defaults | 1e4 | 0.9 s | 75 MB |
| defaults | 5e4 | 1.2 s | 67 MB |
| defaults | 2e5 | 2.7 s | 72 MB |
| defaults (QPLim=2) | 1e6 | 12.6 s | 66 MB |
| killed-run macro replica (all 30+ macro params, stock lattice) | 1e4 | 0.5 s | 75 MB |
| 17 single-parameter crystal-config extremes (cubic, stiffness, dyn, scat, decay, decayTT, Debye, vsound, vtrans at sweep bounds) | 5e4 each | 1.0–1.2 s | 65–81 MB |

Memory is flat in event count (no per-event leak in V10-01-01 at these
settings) and insensitive to every swept parameter except the QPLim
pathology. **A Morris sample using this template should never exceed
~100 MB; multi-GB RSS means the sample is hung in the Kaplan loop and its
output will never arrive.**

### Recommendations (Finding 1)

1. **Change the sweep bounds**: in `sensitivity_params.py`, raise the lower
   bound of the three QPLim parameters from 1 to 2
   (`("...setTopQPLim ", 3, [2, 5], "int")`, same for TopFilm/Bot). This
   eliminates the hang class entirely with minimal loss of design space —
   QPLim=1 isn't a physically meaningful operating point in this model
   anyway (it asks QPs to radiate down to exactly the gap, which the
   sampler cannot reach by construction).

   **STATUS: applied 2026-07-20.** All three parameters now sweep `[2, 5]`;
   a full 6912-sample design was verified to contain zero QPLim=1 rows. Note
   that this changed the Morris design, so results predating that date are
   not directly comparable. See
   `SensitivityAnalysis_Morris_run_record_Mimir.md`.
2. **Add a per-sample timeout** to the runners (e.g., kill `Main` after
   N× the median sample time). Today a hung sample occupies a worker slot
   forever, and — as observed — the killed Python wrapper does **not**
   kill the `Main` grandchild: seven orphaned `Main` processes at 100% CPU
   had to be cleaned up manually after today's tests. Any timeout
   implementation must kill the whole process group.

   **STATUS: applied 2026-07-20** as `SENSITIVITY_SAMPLE_TIMEOUT` (seconds;
   `0`, the default, disables it). It kills the whole process group, verified
   on mimir: after a forced timeout, `pgrep` found no orphaned `Main`.
3. Optionally report upstream (G4CMP GitHub): `G4CMPKaplanQP::CalcQPAbsorption`
   should either forbid `lowQPLimit ≤ 1` or treat
   `qpE < lowQPLimit*gapEnergy + (numerical floor)` as absorbable; as
   written, any user configuration with `lowQPLimit = 1` is an infinite
   loop the first time a pair breaks.

---

## Finding 2 — Exit-time SIGSEGV (the "Morris_9" crash)

### Definitive evidence: the macOS crash report

`~/Library/Logs/DiagnosticReports/Main-2026-07-16-130449.ips` — timestamp
matches the Morris_9 failure to the minute. Faulting stack (thread 0,
`EXC_BAD_ACCESS / SIGSEGV`):

```
G4CountedObject<G4VTouchable>::Release()
G4ReferenceCountedHandle<G4VTouchable>::~G4ReferenceCountedHandle()
G4CMPParticleChangeForPhonon::~G4CMPParticleChangeForPhonon()
G4CMPPhononBoundaryProcess::~G4CMPPhononBoundaryProcess()
G4ProcessTable::~G4ProcessTable()
G4ThreadLocalSingleton<G4ProcessTable>::Clear()
__cxa_finalize_ranges → exit → main returns
```

The crash is inside `exit()`, i.e., **after `main()` finished** — after
`delete visManager; delete runManager;` (hence "Visualization Manager
deleting..." being the last log line) and after all event processing and
hits-file writing.

### The mechanism (source-level)

`G4CMPPhononBoundaryProcess` holds a `G4CMPParticleChangeForPhonon` **by
value**. In G4CMP V10-01-01
(`library/src/G4CMPParticleChangeForPhonon.cc`):

```cpp
void G4CMPParticleChangeForPhonon::Initialize(const G4Track& track) {
  updateVol = false;
  theTouchableHandle = track.GetTouchableHandle();   // grabbed EVERY step
  G4ParticleChange::Initialize(track);
}

G4Step* G4CMPParticleChangeForPhonon::UpdateStepForPostStep(G4Step* pStep) {
  if (updateVol) {          // only when a reflection proposed a touchable
    ...
    theTouchableHandle = 0; // cleared ONLY on this branch
    updateVol = false;
  }
  ...
}
```

`Initialize()` runs on every phonon step and stores a **reference-counted**
handle to that track's touchable. The handle is cleared only when a
boundary reflection is actually applied (`updateVol == true`). After the
final phonon step of the run (almost always a non-reflection step), the
member keeps a live reference for the remainder of the process lifetime.

At `exit()`, C++ static/thread-local destructors run in an order Geant4
does not fully control. The touchable objects live in `G4Allocator` pools
that are torn down early; `G4ProcessTable`'s thread-local singleton is
destroyed later, deleting `G4CMPPhononBoundaryProcess`, whose particle
change finally releases the stale handle — decrementing a reference count
**inside already-freed pool memory**. Whether this segfaults or silently
scribbles depends on whether that heap page was unmapped/reused, which
varies run-to-run with event content (and with the `clock()`-based RNG
seed in `Main.cc`). Hence the observed stochasticity:

- 1 crash in 14 completions (2026-07-16 parallel dry run, 10⁴ events),
- 0 crashes in 90 completions (old 1e6 production run),
- 0 crashes in 219 short completions today.
- Aggregate ≈ **0.4% of completed runs**, order-of-magnitude.

### Upstream history — this is a known-adjacent bug, half-fixed twice

From the G4CMP ChangeHistory / git log:

- **G4CMP-547** (2025-11-28, `3bc72167` "Ensure that ParticleChange
  touchable is cleared after every track"): added an *unconditional*
  `theTouchableHandle = 0` in `UpdateStepForPostStep` — i.e., upstream has
  already been burned by exactly this stale-handle problem. The same commit
  also noted "Implement empty destructor to avoid deleting
  G4TouchableHandle" — an explicit crash workaround.
- **G4CMP-563** (2026-01-07): removed copy operators from the class.
- **G4CMP-585** (2026-02-20, `60d07b2b` "Resolve memory leak by localizing
  clearance of touchable handle"): moved the clearing **back inside**
  `if (updateVol)` and restored the defaulted destructor — fixing the leak
  but **reopening the exit-time release-after-free window**, because
  `Initialize()` still populates the handle on every step.

So the crash seen here is the residual of an upstream leak-vs-crash
tug-of-war in `G4CMPParticleChangeForPhonon`, present in the latest tagged
release (V10-01-01, 2026-02-24).

### Impact assessment

- The simulation, hits file, and log are complete before the fault; the
  Morris_9 hits file was verified intact. (Caveat: the hits `ofstream` is
  owned by `PhononSensitivity`, also destroyed at exit; if its flush ever
  ordered *after* the crashing destructor, final buffered rows could be
  lost. Empirically the file has always been intact; treat rc=139 hits
  files as usable but worth a row-count sanity glance.)
- The orchestration layer now tolerates it: per-sample exception handling
  added on 2026-07-16 (originally to `SensitivityAnalysis_Morris_Mac_Unified.py`
  and `..._M4Max.py`, now carried by their successor
  `stage1_run_simulations.py`) logs the failure and
  continues the batch, and `stage2_compute_QPs.py` (formerly `compute_QPs.py`)
  skips nothing (the hits file exists).

### Recommendations (Finding 2)

1. **Accept and contain** (current state): the per-sample catch already
   prevents batch loss. Optionally special-case exit code 139 in the
   runners' failure summary as "teardown-only crash — hits file expected
   intact" to avoid alarming log noise.
2. **Local patch option** (if the log noise matters): add
   `theTouchableHandle = 0;` at the top of
   `G4CMPPhononBoundaryProcess::EndTracking()` (or give
   `G4CMPParticleChangeForPhonon` a `Clear()` called from there). Releasing
   the handle at end-of-track — while allocator pools are alive — is safe,
   leak-free, and removes the exit-time window. Requires rebuilding
   `libG4cmp.dylib` only.
3. **Report upstream** with the `.ips` stack above; it is precisely the
   case G4CMP-547/585 danced around.

---

## Finding 3 — Sweep-design flaw: half the energy levels are physics-free

`/g4cmp/minEPhonons 0.000382 eV` is pinned (not swept). The gun-energy
sweep `2·191 µeV × [0.5, 1.5]` produces Morris levels
{191, 318, 446, 573} µeV. Primaries at 191 and 318 µeV are below
`minEPhonons` and are killed at creation:

- Old production run: all 42 samples with E = 318 µeV → 0 hits, ~7 s wall
  (pure event-loop overhead); all live samples had E = 573 µeV (2–47 hits).
- Consequence: for ~half the design, *every* detector/crystal parameter's
  elementary effect is computed on a null model — wasted compute and
  distorted Morris statistics for the energy dimension.

**Recommendation:** either raise the gun-energy sweep floor above
0.000382 eV (e.g., `[0.000382, 0.000573]`), or sweep `minEPhonons`
consistently with energy, or accept and document that the low-energy half
of the design measures "nothing happens".

Related observation (reproducibility): `Main.cc` seeds CLHEP with
`(unsigned)clock()` — CPU-time at startup, which is nearly identical across
processes. Seeds therefore cluster tightly; occasional seed collisions
between samples are possible, and runs are not reproducible by design.
Consider seeding from the sample index (a macro-settable seed) if
reproducibility ever matters.

---

## What was ruled out

- **Python orchestration**: identical macros run directly through a bash
  harness reproduce both failure modes; the unified/M4Max scripts only
  observe them.
- **Per-event memory leak at defaults**: RSS flat 67–75 MB from 10⁴ to 10⁶
  events. (G4CMP's Feb-2026 leak fixes are in this build.)
- **Any single crystal-`config.txt` parameter**: all 17 sweep-bound
  extremes ran clean (table above).
- **The killed run's macro parameters minus QPLim**: full replica with
  stock lattice ran clean at 10⁴ events; the QPLim=1 + high event count
  combination is what detonates.
- **Geometry/machine issues**: old 90×1e6 production run completed on this
  same binary and machine in < 5 minutes total.

## Production sizing guidance (healthy pipeline)

With QPLim ≥ 2 enforced: a 1e6-event sample ≈ 13 s / ≈ 70 MB. Four workers
≈ 280 MB total — memory is a non-issue. A full 6912-sample design at 1e6
events ≈ 6912 × 13 s / 4 workers ≈ **6.3 hours** wall (plus ~25% of samples
finishing in ~7 s because they're below the phonon threshold — see
Finding 3). Budget for ~0.4% of samples ending with the harmless teardown
segfault; the runners continue past them.

---

## Appendix — reproduction commands

Infinite loop (hangs; kill manually or wrap in a timeout):

```zsh
cd /Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac
# make a macro from the 10000 template with: HitsFile→/tmp, substrate Si,
# phononBounces 1000, minEPhonons 0.000382 eV, gun 0.000573 eV,
# setTopQPLim 1, beamOn 1000000 — then:
source ~/Geant4/Geant4-Install/share/Geant4-10.7.4/geant4make/geant4make.sh
source ~/Geant4/G4CMP/g4cmp_env.sh
export G4LATTICEDATA=~/Geant4/G4CMP/CrystalMaps
~/geant4_workdir/bin/Darwin-clang/Main <macro>   # spins forever at QPLim=1
```

Control: the same macro with `setTopQPLim 2` finishes in ~13 s with ~15
hits.

Segfault evidence: `~/Library/Logs/DiagnosticReports/Main-2026-07-16-130449.ips`
(keep this file — it is the primary artifact for any upstream report).

Key source files:

- `~/Geant4/G4CMP/library/src/G4CMPKaplanQP.cc` — `AbsorbPhonon`,
  `CalcQPAbsorption`, `PhononEnergyRand` (Finding 1).
- `~/Geant4/G4CMP/library/src/G4CMPParticleChangeForPhonon.cc` and
  `.hh` — `Initialize` / `UpdateStepForPostStep` (Finding 2).
- `~/Downloads/BNL_G4CMP_HT_Feb27/Main/src/PhononDetectorConstruction.cc` —
  wires `setTopQPLim` → MPT `lowQPLimit` (line ~118).
- `~/Downloads/BNL_G4CMP_HT_Feb27/Main/Main.cc` — teardown order and
  `clock()` seeding.

Session test data (36 single-run probes + 40-sample batch + 185-run stress
batch) lived in the session scratchpad and `/tmp` and has been cleaned up;
every number in this report is regenerable from the recipes above.
