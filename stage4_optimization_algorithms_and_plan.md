# Stage 4: Material-Optimization Plan and Algorithm Selection

**Date:** 2026-07-29 (revision 4 feasibility audit)
**Status:** implementation plan; no Stage-4 optimizer has been implemented yet
**Supersedes:** revision 3 (physics-model audit), revision 2 (cost-measured),
and revision 1 (pre-measurement)

Revision 2 fixed the cost model: it replaced the *assumption* that evaluations
are expensive with the measurement that they are cheap, and moved the centre of
gravity from the optimizer to the objective and the systematics. The measured
core-time and replica-variance values remain useful; revision 4 corrects the
linear-scaling, spatial-convergence, and fidelity conclusions drawn from them.

**Revision 3 fixes the physics model.** Revision 2 chose its design space from
the *statistics* of the Morris screen — marginal swings over sampled ranges —
without reading what the backside absorber code actually computes. Doing so
(§1.6) changes the plan more than the cost measurement did:

* The backside film is a **downconverter with a leak**, not a black body.
  Incident phonons are absorbed almost completely at the default 1 µm, but the
  film re-emits down-converted phonons through **4× less optical depth**, and
  `WaffleKaplanElectrode` re-emits only those still above \(2\Delta_{\rm top}\)
  — so everything that leaks back is still dangerous. `setBotThickness` is
  therefore **live out to ≈10 µm** through that channel.
* The backside film is **not geometry**. It is an analytic surface mask plus a
  material-property table, so "does the geometry build above 1.5 µm" is not a
  question that exists.
* **Fixing the geometry does not disable the material knobs.** Geometry enters
  at one of six gates in the absorption chain, and its exact degeneracy with
  \(v_s\tau\) *transfers* the lever to the material rather than cancelling it
  (§3.3c). Material identity is therefore a real lever — and is deliberately
  **out of Stage-4 scope** per the 2026-07-29 decision (§0). Within a fixed Cu
  preset, the live coordinates are backside thickness and constrained coverage.

**A correction within this revision.** §1.6 as first drafted claimed thickness
was saturated and that the Milestone-0 scan would return a flat line. That was
wrong — it traced the incident-phonon escape channel only, and used `frac = 4`
from `G4CMPKaplanQP` where `G4CMPNormal` uses `2`. The claim is withdrawn and
revision 2's thickness scan is restored. See §1.6a for the full record and for
what survived.

It also records two scoping decisions taken 2026-07-28: the first campaign is
**Cu-only** (§3.3) and holds the **entire top/junction stack fixed** (§3.1).
Together these leave two actionable continuous coordinates, thickness and
coverage, which removes the immediate need for a Bayesian optimizer (§4).

It retains the event-accounting change (commit `501a557`), which makes the
**total** phonon count per design point the configured knob and derives
`/run/beamOn` from it. That change has direct consequences for the Stage-4
fidelity contract, including one configuration revision 1 recommended that
Stage 1 will now refuse to run.

Finally, it adds §5.5 — the Stage-1 integration constraints that any Stage-4
controller must design around, discovered by reading `stage1_run_simulations.py`
rather than by reasoning about it.

**Revision 4 re-evaluates this plan against the code and artifacts present on
2026-07-29.** The scientific direction is feasible and meaningful, but the
revision-3 execution plan is not internally executable as written. The required
corrections are:

1. The stored P=16 noise-floor run measures variation over the *chosen* 16
   positions; it does not prove spatial-quadrature convergence. A small,
   design-stratified P16/P32/P64 or independent-Sobol-scramble check is still
   required before P16 is declared ranking-safe.
2. `TOTAL=4,032,000` is a convenient low-fidelity total, not a 5%-precision
   fidelity. Repartitioning a fixed total among replicas cannot create
   information. Use a cheap exploration tier and increase the *total* events
   only for selected confirmation points.
3. Stage 1 has no arbitrary explicit-design input, so the proposed five-point
   thickness/reference pilot cannot be represented reproducibly by the current
   Morris-only interface. A minimal explicit-design path is a prerequisite,
   unless the pilot is run by an intentionally separate, checked manual driver.
4. The preset directory contains six `setBot2*.mac` files, not five.
   `setBot2Ti.mac` is a second apparently complete physical preset, and
   `setBot2Al.mac` remains defective. *(Superseded by §0: Ti is out of scope, so
   its validation is struck; the Al file is still fixed or deleted as hygiene.)*
5. The event-accounting commit removed two memory-budget validation guards.
   Positive budgets and
   `SENSITIVITY_PER_SAMPLE_MEM_GB <= SENSITIVITY_TOTAL_MEM_GB` must be restored
   before the Stage-4 controller relies on fail-fast configuration validation.

These are amendments to execution and strength of inference, not a rejection of
the Cu thickness/coverage study. The first campaign should be described as a
**model-qualification and Cu geometry/thickness response study**. It becomes a
material-selection optimization only after at least two physically validated
presets exist.

---

## 0. Scope (decided 2026-07-29 — binding)

**Stage 4 is Cu backside-stack optimization. It is not material-identity
selection.**

| In scope | Out of scope |
|---|---|
| Backside **thickness** (1–10 µm; live, confirmed at z = 7.5, §6a) | **Ti, and any Cu-versus-Ti comparison** |
| Backside **coverage**, constrained or priced (§3.3a) | **Extending the preset library** (W, Au, Pd, Nb …) |
| `setBotAbs` and film lifetimes as **uncertainty/robustness axes** (§3.6) | Optimizing over **material identity** |
| **Source-scenario** robustness for the Cu stack (§6 item 6) | The top/junction stack (fixed, §3.1) |

**Why this is a coherent boundary rather than a truncation.** §3.3c shows the
material lever is real: with thickness fixed, \(v_s\tau\) inherits its full
range. Declining to use it is a scope decision, not a claim that it does not
exist. Every Stage-4 result must therefore be phrased as *"within the Cu
backside stack"* and must not be read as evidence that Cu is the best backside
material — a question this campaign does not ask.

**What this decision removes from the plan.** The preset-library work (former
M0e), the Ti audit (former half of M0b), and the material axis (former M3) are
struck. **Retained from M0b: validation of the Cu preset's normal-metal routing
and constants**, which is a prerequisite for trusting any Cu result and is
independent of the material question.

**Consequence for §3.3c.** Its conclusion — that the live lever once geometry is
fixed is the choice of metal — remains physically correct and becomes an
explicitly deferred finding rather than a call to action. It is the strongest
candidate for a *future* campaign and should be recorded as such in any write-up.

---

## Executive summary

1. **Low-fidelity evaluations are cheap.** ≈5.1 worker-seconds per sub-run and
   ≈2.7 core-minutes per design point at 4e6 events are measured. The quoted
   ≈1.6-hour whole-screen time at 192 workers is an optimistic linear-scaling
   projection, not a measured wall time at 192-way concurrency.
2. **Large effects are cheap to find; 5% confirmation is not free.** About
   1.7e8 events per independently simulated candidate is the existing
   2σ planning estimate for a 5% two-candidate comparison. That is affordable
   for finalists, but should not be silently assigned to every exploration
   point.
3. **The backside film is a downconverter with a leak, not an absorber**
   (§1.6). Incident phonons are absorbed with ~97% probability at the default
   1 µm, but the film re-emits down-converted phonons that escape through **4×
   less optical depth**, and `WaffleKaplanElectrode` only re-emits those still
   above \(2\Delta_{\rm top}\) — so **everything that leaks back is still
   dangerous**. Leakage runs 0.54 at 1 µm to 0.018 at 10 µm.
4. **Therefore `setBotThickness` is live out to ≈10 µm**, and revision 2's
   no-film → 1, 2, 5, 10 µm positive control is the right experiment. (An
   earlier draft of revision 3 claimed thickness was saturated; that was wrong
   and is corrected in §1.6a.)
5. **Fixing the geometry does not disable the material knobs** (§3.3c).
   Geometry appears at exactly one of six gates in the absorption chain.
   Moreover thickness and \(v_s\tau\) are exactly degenerate, so fixing
   thickness **transfers** the lever to the material rather than removing it.
6. **The live lever, once geometry is fixed, is the coupled backside-material
   preset — and it is deliberately out of scope (§0).** Stage 4 is Cu-only
   backside-stack optimization. Ti, preset-library extension, and any
   material-identity comparison are struck; the Cu preset's routing and
   constants are still validated (retained M0b).
7. **The first Cu-only design space is 2-D** (thickness × coverage, plus
   `setBotAbs` as a robustness/scenario axis). Use an interpretable factorial
   grid first and optionally add a modest scrambled-Sobol fill. A Bayesian
   optimizer is unnecessary unless validated material/cost/robustness axes make
   the space genuinely larger or the response unexpectedly non-monotone.
8. **The campaign's deliverable is a response surface and a
   reference-inspired comparison, not an optimum.** Yelton's 10 µm islands
   versus 1 µm film pits
   a ~30× leakage reduction against a coverage reduction — comparable and
   opposing, so the model can genuinely express either ordering (§6 item 1).
9. **The stored run does not answer position convergence.** It accurately
   measures heterogeneity over one fixed P=16 set, but cannot measure the bias
   of that set relative to the source-position integral. Run a small nested or
   independently scrambled check at the baseline and a few response-surface
   extremes; this is not a full production campaign.
10. **The principal risks are systematic**, although finalist precision still
    costs statistics: an objective
    degenerate under electrode size, a monochromatic ballistic source standing
    in for a real cascade, a mislabelled preset file, and — as §1.6a records —
    reasoning about a mechanism from one of its two code paths.
11. **The event-accounting change tightens the fidelity contract.**
   `TOTAL = 4,032,000` is a useful common pilot total for
   P∈{16,32,64}, R∈{1,…,8}. For adaptive confirmation, compute
   \(\lceil N_{\rm target}/(PR)\rceil PR\); the total must grow with the desired
   precision.

---

## 1. Measured facts that determine this plan

Every number in this section was measured from artifacts already on disk, not
assumed. Nothing here required a new simulation.

### 1.1 Evaluation cost

Derived from the Stage-1 run record for
`results/morris_mimir_c7a18a17-91ce-491b-8124-78a4ff16576a` (221,184 sub-runs,
9h 43m, 32 workers):

| Quantity | Value |
|---|---:|
| Per sub-run | **≈5.1 core-seconds** |
| Throughput | ≈30,000 phonon events / core-second |
| Per design point (4e6 events, 32 sub-runs) | **≈2.7 core-minutes** |
| Full 6,912-point screen on 192 workers | **≈1.6 hours projected** |
| Design points per hour on 192 workers | **≈4,000 projected** |

Cross-check: the noise-floor run (3,200 sub-runs, 45 min, 8 workers) gives
≈6.8 core-seconds per sub-run at 250k events — consistent to within the
difference in per-sub-run event count.

Host: 192 cores, 503 GB RAM, 8 × NVIDIA RTX A6000 (48 GB each). Peak RSS during
the screen was 2.15 GB aggregate across 32 concurrent sub-runs (≈67 MB each), so
memory does not constrain worker count; 192 concurrent sub-runs is ≈13 GB.

The 192-worker values assume linear scaling from the measured 32-worker run.
Process launch, filesystem metadata, output collection, and shared-host
contention can break that assumption. Treat core-time as the portable cost
measure and the 192-worker wall times as optimistic lower bounds until a short
192-worker scaling test is recorded.

**Consequence.** Sample efficiency is not the binding constraint for the
two-dimensional Cu study. It may become relevant after material, uncertainty,
source-scenario, or fabrication-cost axes are added; the backend boundary should
therefore remain algorithm-neutral without implementing Bayesian optimization
prematurely.

### 1.2 Noise, re-measured across the design box

The README's 12.4% figure is a *single-configuration* noise-floor statistic. The
operative noise across the actual design box is larger. Measured from the paired
replicas of all 6,912 complete design points:

| Quantity | Value |
|---|---:|
| Per-replica relative SD (from paired differences) | **16.3%** |
| Relative SD of the 2-replica mean | **11.5%** |
| Mean `total_QPs` per design point | 125.6 |

Use 16.3% per replica (one replica = 16 positions × 125k = 2e6 events) for all
Stage-4 budget planning. The Fano factor is not constant — it runs 2.2 in the
lowest count decile to 4.1 in the highest — so candidate-specific replicated
variance estimates remain mandatory.

### 1.3 Positional variance: measured heterogeneity, not convergence

The noise-floor run is 200 *identical* configurations × the same 16 positions
with all 3,200 hits files retained. It cleanly separates Monte Carlo variation
from variation among those 16 sampled positions. It does **not** establish
convergence of the 16-point spatial quadrature to the continuous source-position
integral, because there is no independent position set or denser nested set in
that artifact.

A direct per-position reduction of those files reproduces λ = **147.4**, the
value in the README — confirming the reduction agrees with Stage 2.

| Quantity | Value |
|---|---:|
| Per-position mean QP (250k events each) | **2.5 (p9) → 23.1 (p11)** — a 9× spread |
| MC standard deviation within a position | 50.3% |
| True between-position standard deviation | **60.5%** |
| Ratio between-position / MC variance | **1.45** |
| Effective number of positions (participation ratio) | **≈12 of 16** |

Positional variance therefore *exceeds* Monte Carlo variance. But the 16
positions are a **fixed common set reused for every design point**, so this term
is a systematic offset, not evaluation-to-evaluation noise:

| Component at P=16 | SE | Behaviour |
|---|---:|---|
| SD-over-positions divided by √16 | **15.1%** | heuristic scale; Sobol points are not IID |
| Monte Carlo (250k evt/pos) | **12.56%** | falls as 1/√events |

The 12.56% MC term matches the README's observed 12.4% run-to-run SD. Reusing a
fixed position set is a useful blocking design: position-set error is shared by
candidates. Shared error cancels only to the extent that the position-response
profile is invariant over the Stage-4 design space.

**Does it cancel in comparisons?** Only if the positional response *pattern* is
design-independent. Crystal orientation is the worst case, since rotating the
lattice rotates the caustics against a fixed injection set. Tested on the screen
at 22.5° vs 67.5° (a 45° rotation — maximal for a cubic lattice), 250 design
points per group, normalized per-position QP shares:

> **profile correlation r = 0.9878; L1 distance = 0.0798; max share change
> 0.0161 against a uniform share of 0.0625.**

This is reassuring evidence for orientation groups within the Morris ensemble,
not a direct test of thickness/coverage extremes. It is also not a convergence
test, because both groups use the same P=16 set.

**Verdict.**

- **P=16 is a reasonable provisional exploration fidelity**, not yet a proven
  ranking fidelity.
- Before the main response surface, compare P=16 against P=32/P=64 or at least
  two independent scrambled position sets at: baseline, thin/low coverage,
  thick/high coverage, and each validated material preset. Use nested sets if
  possible so differences can be attributed cleanly.
- If rankings and candidate ratios are stable within the intended decision
  tolerance, keep P=16 for exploration and use the demonstrated denser fidelity
  for finalists and absolute comparisons.
- The earlier 1–3% “systematic floor” is an order-of-magnitude hypothesis, not a
  measured bound. Do not use it as a stopping rule until this check supplies an
  empirical between-position-set error.

### 1.4 Statistical power, in the new total-events currency

Replicas required to resolve a true fractional difference at 2σ in a paired
two-candidate comparison, given 16.3% per-replica SD. Expressed as **total
events per candidate**, matching the post-`501a557` interface:

| Target resolution | Total events/candidate | core-hours | ideal wall @192 continuously occupied cores |
|---:|---:|---:|---:|
| 30% | 4.7e6 | 0.04 | **1 s** |
| 20% | 1.1e7 | 0.10 | **2 s** |
| 10% | 4.3e7 | 0.39 | **7 s** |
| **5%** | **1.7e8** | **1.57** | **30 s** |
| 2% | 1.1e9 | 9.8 | 184 s |

These are planning estimates for independently simulated candidates using the
measured 16.3% per-replica relative SD and ideal machine occupancy. A single
candidate with only \(P\times R=32\) sub-runs cannot occupy 192 cores by itself,
so dividing core-hours by 192 understates its latency unless several candidates
run concurrently. Candidate-specific variance and the position-convergence
check must replace the generic estimate when data are available.

**Use two tiers.**

- **Exploration:** about 4.032e6 total events per candidate, intended to map
  large effects and fit a smooth response surface. With two replicas its
  observed relative SD of the mean is about 11.5%, not 5%.
- **Confirmation:** increase total events for the reference, endpoints, and a
  small finalist set. About 1.7e8 events/candidate is the present independent-run
  planning value for a 5% two-candidate difference at 2σ. Stop at a precision
  justified by the measured spatial and model systematics.

**Free variance reduction available.** These figures assume independent
replicas. `sub_run_seed()` currently keys the seed bank by *trajectory*
(`design_point_index // trajectory_length`), which is meaningless outside
Morris. Sharing a seed bank across *candidates* at matched (position, replica)
permits a paired analysis and is worth implementing. It does not guarantee
useful common random numbers: different physics paths can consume different
numbers of random draws and rapidly decorrelate. Measure the paired-difference
correlation on the positive-control A/B run before crediting any variance
reduction in the budget.

### 1.5 Scoping the design space from stored data

Full-range marginal swings — median yield in the top decile of each knob divided
by the median in the bottom decile, over all 6,912 design points. These are
**marginal** relationships in a 53-dimensional one-factor-at-a-time design with
every other parameter varying, so interactions are hidden. Treat as scoping, not
as truth.

| Knob | Swing | Reading |
|---|---:|---|
| `setBotAbs` *(material property)* | **3.6×** | strongest lever overall |
| `setTopGap` *(material property)* | **2.8×** | material identity dominates |
| **coverage** (derived from island/spacing) | **2.1×** | best actionable geometry knob |
| `setTopThickness` | 2.1× | thinner is better |
| `setBotThickness` | 1.3× | range is 20× too narrow to conclude anything |
| `setLatticeDeg` | 1.3× | within the positional-artifact scale of §1.3 |
| `setWallAbs` | 1.14× | ≈dead, and the sign is wrong |
| **pitch** (derived) | **1.02×** | **dead** |

**Consequences.**

- The previous plan's "minimal first optimization" listed seven design
  variables. **Pitch and wall absorption look dead**; that reduces the space to
  four or five coordinates, comfortably inside the regime where a space-filling
  design simply solves the problem.
- **Material identity out-dominates all geometry.** In revision 2 this was the
  argument for making preset choice the primary axis. §1.6 and §3.3 supersede
  it: the dominance is real, but it is carried almost entirely by `setBotAbs`,
  which is a calibration parameter rather than a fabrication knob. Per §0,
  material identity is out of Stage-4 scope regardless; Cu is fixed.
- `setLatticeDeg`'s apparent 1.3× effect is the same size as the positional
  quadrature scale. Either drop crystal orientation from the design space or
  test it against a denser, rotation-averaged position set. Do not optimize it
  on P=16.
- **`setBotThickness`'s 1.3× understates it, and §1.6 explains why.** Over the
  sampled [0.5, 1.5] µm the *incident* channel is already saturating, so most of
  the measurable response comes from the secondary leak, which §1.6 predicts
  moves only 0.73 → 0.42 over that interval — a 1.7× effect before dilution by
  the other gates, consistent with the measured 1.3×. Widening to 10 µm reaches
  0.018, so the range genuinely was too narrow, exactly as revision 2 supposed.

### 1.6 What the backside absorber model actually computes

Everything above is statistics over stored outputs. This section is the
mechanism, read directly from the code that produced them. It is the single
largest change in this revision, and it invalidates revision 2's highest-priority
Milestone-0 item.

**Routing.** The Cu backside preset sets `/main/detector_param/setBotNormal
true`, so `WaffleKaplanElectrode::AbsorbAtElectrode` dispatches to
`G4CMP::KaplanNormal` — *not* to `G4CMPKaplanQP`. The two models have different
thickness laws, and the backside uses the normal-metal one.

*Provenance check:* four copies of `G4CMPNormal.cc` exist on mimir
(`BNL_G4CMP_HT_Feb27/Main/src/`, and three under `src/G4CMP_htseng/`). They are
**byte-identical**, so the law below holds regardless of which tree built
`geant4_workdir/bin/*/Main`.

**The law.** From `G4CMPNormal::CalcEscapeProbability`:

```text
mfp    = vSound * phononLifetime / (energy / k_Boltzmann)
escape = exp(-2 * frac * filmThickness / mfp)
```

Note `energy / k_Boltzmann` is a temperature (≈11.6 K at 1 meV), so `mfp` is the
film's phonon mean free path scaled by that factor. At the Cu preset values
(`setBotVSound` 2.608 km/s, `setBotPhLifetime` 5.1 ns) `vSound·τ` = 13.3 µm, so
`mfp` = **1.15 µm at 1 meV** but **3.0 µm at 382 µeV**. That energy dependence is
what produces the two-channel structure below.

**`frac` is not one number, and this is the crux.** The same function is called
at three different optical depths:

| Call site | `frac` | Effective exponent | Governs |
|---|---:|---|---|
| `AbsorbPhonon`, incident phonon | **2.0** | `exp(-4t/mfp)` | is the arriving phonon absorbed at all |
| `CalcReflectedPhononEnergies`, away from substrate | **1.5** | `exp(-3t/mfp)` | does a *re-emitted* phonon leak back out |
| `CalcReflectedPhononEnergies`, toward substrate | **0.5** | `exp(-t/mfp)` | ditto, spawned on the substrate side |

Secondaries escape through **4× less optical depth** than the incident phonon,
at **lower energy where `mfp` is 2–3× longer**. The film is therefore not an
absorber with a single attenuation length; it is a **downconverter with a leak**,
and the leak is the channel that governs the design.

**Two channels, tabulated** (Cu preset; secondary column is the 50/50 average
over `frac` = 0.5 and 1.5):

| t (µm) | incident escape @1 meV | **secondary leak @382 µeV** | secondary leak @600 µeV |
|---:|---:|---:|---:|
| 0.5 | 0.175 | **0.727** | 0.613 |
| **1.0** (default) | 0.031 | **0.542** | 0.400 |
| 1.5 | 0.0053 | **0.415** | 0.275 |
| 2 | 9.3e-4 | **0.324** | 0.197 |
| 3 | 2.8e-5 | **0.209** | 0.108 |
| 5 | 2.6e-8 | **0.098** | 0.037 |
| 10 | 7.0e-16 | **0.018** | 0.003 |

The incident channel saturates by ≈2 µm. **The secondary channel does not
saturate until ≈10 µm**, falling 0.54 → 0.018 — a 30× reduction in leaked
pair-breaking phonons between the default thickness and the reference paper's.

**Why the leaked secondaries are the dangerous ones.** For a normal metal
`setBotGap = 0`, so `IsSubgap()` is identically false: every phonon entering the
film breaks pairs, and the cascade runs until a quasiparticle falls below
`setBotQPLim × setBotGapThres` (= 2 × 180 µeV = 360 µeV) and is deposited as
heat. Phonons that leak out before that are re-emitted into the substrate — but
`WaffleKaplanElectrode` emits a secondary only if `E ≥ 2 × setTopGap` = 382 µeV,
i.e. **only if it can still break a pair in the junction**. Everything that leaks
back is dangerous by construction.

**Consequences.**

1. **`setBotThickness` is a live design coordinate out to ≈10 µm**, via the
   secondary channel. An earlier draft of this section claimed the opposite,
   having traced only the incident channel and having used `frac = 4` (the
   `G4CMPKaplanQP` value) instead of `frac = 2`. That claim is **withdrawn**.
   Revision 2's Milestone-0 scan of no-film → 1, 2, 5, 10 µm is the **correct**
   experiment and is restored in §6 item 1.
2. **The reference comparison is reproducible in principle.** Yelton et al.'s
   10 µm islands versus 1 µm film pits a ~30× leakage reduction against a
   coverage reduction — opposing effects of comparable size. This model *can*
   express the paper's ordering, which makes the run a real test rather than a
   foregone conclusion in either direction.
3. **Backside absorptivity does not factorise into geometry alone:**

   ```text
   A_backside ≈ coverage × setBotAbs × (1 − escape_inc) × (1 − leak_sec)
   ```

   The last factor is 0.46 at the default thickness and 0.98 at 10 µm. **Geometry
   sets how much flux enters the film; material parameters set how much comes
   back out.** These are separate multiplicative channels — which is precisely
   why fixing the geometry does not disable the material knobs (§3.3c).
4. **Thickness and `vSound × phononLifetime` are exactly degenerate**, entering
   only as `t / (v_s τ)`. This degeneracy cuts the useful way: **fixing thickness
   transfers the entire lever to `v_s τ`** rather than removing it. Choosing a
   backside *material* is therefore a substitute for choosing a thickness — the
   strongest deferred finding of this plan, and out of scope per §0. Within
   Cu-only Stage 4, thickness carries the lever and `v_s τ` is fixed.
5. **There is still no geometry to validate.** `IsNearElectrode` is a pure
   analytic `fmod(|x|, l_cell)` mask and `setBotThickness` reaches the physics
   only as a `G4MaterialPropertiesTable` entry, so no solid is built at any
   thickness and there is no overlap or meshing limit to check. But per (1) there
   is now a real *physics* reason to run the thickness scan.

### 1.6a Correction record

Revision 3 as first written claimed thickness was saturated, that the 1→10 µm
scan would return a flat line, and that coverage was the only live fabrication
coordinate. All three were wrong, from tracing one of two escape channels and
carrying `frac = 4` over from a different class. The affected claims are
corrected in place above and in §3.3, §4.1, §6 and §7. What survives unchanged:
the film is not geometry and `A_junction` normalization is a units convention
(§3.1). Revision 4 further corrects the preset inventory and treats
`TOTAL = 4,032,000` as a pilot convenience rather than a universal fidelity.

---

## 2. What the event-accounting change means for Stage 4

Commit `501a557` inverted the configuration direction: `SENSITIVITY_TOTAL_EVENTS`
(the total primary phonons per design point) is now the configured knob, and

```text
/run/beamOn = TOTAL / (N_POSITIONS × N_REPLICAS)
```

is derived, with Stage 1 refusing any total that does not divide exactly.
`SENSITIVITY_EVENTS_PER_POSITION` is removed and errors out. The manifest now
records `n_sim_total_design_point` alongside `n_sim` (per replica) and
`n_sim_per_position`.

The physics of the stored screen is unchanged (4e6 total, 125k per sub-run), so
every measurement in §1 stands. Four things change for Stage 4.

### 2.1 The fidelity record now has the right shape

The previous plan's TOML sketch configured `events_per_position`. That variable
no longer exists. The `Fidelity` record and the config must be:

```toml
[fidelity.pilot]
total_events = 4_032_000      # TOTAL per design point; beamOn is derived
n_positions  = 16
n_replicas   = 2
position_set_id = "sobol-20260727-p16"
seed_bank_id    = "pilot-v1"
```

This is strictly better for Stage 4, because **total events is the quantity the
cost model and the power analysis are both denominated in** (§1.1, §1.4). The
optimizer's precision-promotion policy is now expressed in the same unit the
simulator is configured in, with no conversion step in which a factor of
`P × R` can be lost. That was precisely the failure class the commit removes.

### 2.2 Exact divisibility becomes an optimizer-facing constraint

This is the sharp edge. A Stage-4 controller that promotes a candidate to more
replicas cannot simply increment `R`: the total must remain divisible by
`P × R`. At the current `TOTAL = 4,000,000` and `P = 16`:

| R | 4,000,000 / (16·R) | Stage 1 |
|---:|---:|---|
| 1 | 250,000 | OK |
| 2 | 125,000 | OK |
| **3** | **83,333.33** | **REFUSED** |
| 4 | 62,500 | OK |
| 5 | 50,000 | OK |
| **6** | **41,666.67** | **REFUSED** |
| **7** | — | **REFUSED** |
| 8 | 31,250 | OK |

**A validation fidelity of three paired-seed replicas — the number this project
has previously recommended — cannot launch at 4,000,000 total events.** This
is not a defect in the new accounting — the refusal is correct, and under the
old per-position semantics the same request would have silently changed the
total instead. But it must be designed around rather than discovered at launch
time.

**Adopt `TOTAL = 4,032,000` as the common pilot total**, not as the whole
promotion ladder. It is the smallest multiple of `lcm(P·R)` over
`P ∈ {16, 32, 64}` and `R ∈ {1..8}`, so every listed split divides exactly. It
is 0.80% above the current 4,000,000, so pilot results remain directly
comparable to the stored screen.

| Total | Illegal (P, R) combinations over P∈{16,32,64}, R∈1..8 |
|---|---:|
| 4,000,000 (current) | 10 |
| 4,005,120 | 1 |
| **4,032,000** | **0** |

At fixed total, increasing \(R\) only repartitions the same events; it improves
variance estimation but does not reduce the event-count contribution to the
uncertainty of the pooled mean. Adaptive confirmation must increase total
events. `space.py` should therefore expose

\[
N_{\rm legal}(N_{\rm target},P,R)
=\left\lceil \frac{N_{\rm target}}{PR}\right\rceil PR
\]

and the controller should validate each target fidelity at `init` time. This
keeps the exact-divisibility safety property without confusing a convenient
common multiple with a statistical fidelity ladder.

### 2.3 The completeness validator gains a cheap, strong invariant

With three recorded levels, the validator can check the derivation chain rather
than a single number:

```text
n_sim_total_design_point == n_sim_per_position × N_POSITIONS × N_REPLICAS
n_sim (per replica)      == n_sim_per_position × N_POSITIONS
observed beamOn in macro == n_sim_per_position
```

Any inconsistency is a generation-time bug, not a physics result. This subsumes
the previous plan's Gap D ("compare observed `n_sim` with the manifest-declared
`n_sim`") and strengthens it, at no extra cost.

Note that Stage 2's rescaling on missing positions operates on `n_sim`. A
partial-position replica therefore *breaks* the chain above by construction.
That is a feature: Stage 4 should require
`positions_present == positions_expected` and reject the evaluation rather than
accept a rescaled one. §1.3 shows why — the positions are a fixed quadrature
with a 9× spread, so dropping p11 removes 16% of the signal while dropping p9
removes 2%.

### 2.4 Stage-1 round-trip verification is partly done

`verify_generated_beam_on()` asserts every generated macro carries the intended
count before any simulation starts. That is exactly the round-trip check the
previous plan asked for, for one parameter. The Stage-4 explicit-design mode
inherits it for free and should extend the same pattern to the full expanded
parameter vector.

---

## 3. Problem formulation

### 3.1 The objective decision is the most consequential open item

`setWidth` and `setHeight` rank **#2 and #4** in the Morris μ* table. That is
not mitigation physics — it is "a larger electrode intercepts more phonons."

> **If `total_QPs` is optimized with electrode dimensions free, the optimizer
> will shrink the qubit and report a large, meaningless win.**

Fix electrode dimensions and optimize a **QP density per junction area**, or
adopt a per-qubit worst-case statistic. This decision must be made with the
physics — from the reference paper's poisoning metric — before any optimizer
runs. It changes what the answer means far more than the choice of acquisition
function does.

`total_QPs` is already junction-localized (it counts top-surface hits binned to
the nearest electrode), so the fix is to hold the geometry fixed and normalize,
not to rebuild the observable.

**Correction to how that fix was stated.** Dividing by `A_junction` while
electrode dimensions are held fixed divides every candidate by the same
constant. It changes no ranking, no ratio and no significance test. **The
degeneracy is cured by the fixing, not by the normalization** — the normalization
is a units convention that makes the quantity comparable to future runs at other
geometries. Stating it the other way round invites a later reader to conclude
that the normalization licenses freeing the geometry again, which it does not.

**Decision (2026-07-28): the entire top/junction stack is held fixed for the
first campaign.** Revision 2 was internally inconsistent here — §3.3 listed no
top-film coordinate while §4.1 proposed enumerating "three top presets." Fixing
the whole stack resolves it in the safe direction, and buys three things:

* **The §3.1 gap confound disappears exactly, rather than being reported
  around.** With `setTopGap` constant, `round(E_deposited / gap)` is a fixed
  linear map, so a change in `total_QPs` is unambiguously a change in energy
  reaching the junction. No paired reporting of QP count and deposited energy is
  needed to interpret the result.
* **It removes the coupled qubit-design consequence.** Varying the junction gap
  changes critical current, qubit frequency, junction design and loss — none of
  which this model sees (§4.2 of `RESULTS_stage1_to_stage3.md`). A "better"
  design found by moving the gap could be an unbuildable qubit.
* **It removes `setWidth`/`setHeight` from play entirely**, which is the
  shrink-the-qubit degeneracy above.

Cost: the `setTopGap` (#3), `setTopAbs` (#5) and `setTopThickness` (#7) axes are
given up. All three are real effects, but all three are confounded — the first
definitionally, the latter two with the calibration-parameter problem of §3.6.
They are candidates for a second campaign once the backside question is settled.

**A second, independent confound: the QP count is mechanically tied to the
gap.** `stage2_compute_QPs.py`'s `calculate_QPs()` bins every hit as

```python
QPNos[q, t_idx] += int(np.round(rec["Energy Deposited [eV]"][h] / gap))
```

So for a fixed deposited energy, `total_QPs` scales as `1/gap` **by
construction**, on top of whatever the Kaplan absorption model's genuine
gap-dependence contributes physically. `setTopGap` is the #2 material lever in
§1.5 (2.8× swing) — some fraction of that swing is this arithmetic quantization
effect, not a change in how many phonons the film actually captures. Any
objective or design search that lets the gap vary must either report QP count
and deposited energy separately, or accept that "fewer QPs" from a larger gap
partly means "the same energy divided by a bigger number," not "less
poisoning energy reached the junction."

### 3.2 Separate design, scenario, fidelity, and replica

- **design variables** \(x\) — changed by the optimizer;
- **scenario** \(u\) — source energy and position set; averaged or treated
  robustly, never optimized to flatter the device;
- **fidelity** \(b\) — now a single `total_events` plus its `(P, R)` split;
- **replica** \(r\) — Monte Carlo realization.

Initial objective, one scalar:

\[
J(x) = \mathbb{E}_r\!\left[
  \frac{\mathrm{QP\ count}(x,u_0,r)}{A_{\rm junction}\,N_{\rm total}}
\right]
\]

with the Stage-3 source protocol fixed and electrode geometry held constant.
Note that with the geometry fixed, \(A_{\rm junction}\) is a constant and the
normalization is a units convention only — see the correction in §3.1. Define
it unambiguously as the **total active area of the 17 counted electrodes** if
the numerator is the sum over all 17. Dividing a 17-electrode total by the area
of one electrode would be a factor-of-17 units error. Also retain the
per-electrode vector so mean and worst-electrode objectives can be reconstructed
without rerunning Geant4.

Store but do not optimize: max-electrode QP, per-position breakdown, upper
quantiles, wall time, completeness status, and deposited energy alongside QP
count. An estimated recovery time or fraction of electrodes with predicted
`T1_QP < 10 µs` may be stored as **exploratory post-processing only** until the
QP-to-\(T_1\) mapping, diffusion/recombination parameters, device alignment, and
normalization are independently calibrated. The six causally disconnected QPDE
screening parameters cannot validate that mapping.

*(Deposited energy is no longer needed to separate the §3.1 gap confound — with
the top stack fixed, `gap` is constant and the confound is absent by
construction. Record it anyway: it is free, and it is the quantity that becomes
load-bearing the moment a second campaign unfreezes the gap.)*

### 3.3 Design space

**Decision (2026-07-28, hardened 2026-07-29): Stage 4 is Cu-only — see §0.**
Cu is not a starting point pending a wider library; it is the scope.

Revision 2 proposed enumerating "at most six bottom presets," on the strength of
§1.5's finding that material identity dominates geometry. Reading
`BNL_G4CMP_HT_Feb27/Main/macros/FilmPreSets/` shows that library does not exist:

| File | What it actually is |
|---|---|
| `setBot2Cu.mac` | a real material — normal metal, complete property set |
| `setBot2Ti.mac` | a second physical candidate — Ti constants with a 59.15 µeV gap, opening comment incorrectly says aluminum. **Out of scope (§0)**; retained only as a note for a future campaign |
| `setBot2Al.mac` | **defective** — sets `setBotSourceMat G4_Ti` under an aluminum banner while combining it with Al-like constants; do not expose it to Stage 4 |
| `setBot2NoFilm.mac` | **not a material** — sets only `setBotAbs 0.0`; a control |
| `setBot2BlanketFilm.mac` | **not a material** — sets island/spacing for full coverage |
| `setBot2NoGeom.mac` | **not a material** — sets island/spacing for zero coverage |

There are therefore **six files: three controls, Cu, a Ti candidate, and one
defective Al-labelled file**. Per §0, **only `setBot2Cu.mac` is used.**

* **Ti is out of scope**, so its audit is struck. Recorded for a future
  campaign: its constants need provenance, its opening comment says aluminum,
  and — decisively — it sets `setBotNormal false`, routing to `G4CMPKaplanQP`
  instead of Cu's `G4CMP::KaplanNormal`. A Cu/Ti comparison would therefore be a
  model comparison as well as a material one (different mean-free-path laws:
  1.15 µm vs 0.28 µm at 1 meV) and needs that stated, not just cited constants.
* **`setBot2Al.mac` remains defective** — `setBotSourceMat G4_Ti` under an
  aluminum banner, no `setBotGapThres`. Fix or delete it so it cannot be
  selected by accident. This is hygiene, not a campaign prerequisite.
* **Extending the library** (W, Au, Pd, Nb …) is struck from Stage 4. When it is
  revived, each preset must carry a cited gap, sound speed, phonon lifetime,
  absorption probability, down-conversion threshold and QP cutoff, all mutually
  consistent (§3.5).

**Retained from the preset work:** validating the Cu preset's normal-metal
routing and constants. That is a prerequisite for trusting any Stage-4 number
and is independent of the material-identity question.

**Resulting design space.**

| Coordinate | Type | Initial treatment |
|---|---|---|
| Backside thickness \(t\) | continuous | **1–10 µm** — live via the secondary channel (§1.6) |
| Backside coverage \(f_{\rm cov}\) | continuous | **0.3–0.9, and never optimized unconstrained** — see §3.3a |
| Backside material preset | — | **fix at Cu (§0)** — not a coordinate in Stage 4 |
| Backside \(\texttt{setBotAbs}\) | uncertainty | **robustness axis**, not a design variable (§3.6) |
| Backside \(v_s\tau\), \(\texttt{setBotGapThres}\), \(\texttt{setBotQPLim}\) | — | **fix** — carried by the preset, but see §3.3c |
| Junction/top stack (gap, abs, thickness) | — | **fix** — see §3.1 decision |
| Junction width/height | — | **fix** — shrink-the-qubit degeneracy |
| Backside pitch \(p\) | — | **drop** (1.02× swing, §1.5) |
| Wall absorption | — | **drop** (1.14×, wrong sign) |
| Crystal orientation | — | **defer** — confounded with the P=16 quadrature |

With \(p = I + S\) and \(f_{\rm cov} = (I/(I+S))^2\), the inverse map is
\(I = p\sqrt{f_{\rm cov}}\), \(S = p(1-\sqrt{f_{\rm cov}})\). Retain the
reparameterization even with pitch fixed, since coverage must be expressed
through it. At the current default pitch \(p = 250\) µm, \(f_{\rm cov}\in[0.3,0.9]\)
maps to \(I\in[137,237]\) µm and \(S\in[113,13]\) µm — inside the ranges the
screen already exercised, so no new validation is required. Confirm with a
generate-only pass at both ends before the campaign, since the corner
(large island, 13 µm spacing) is a box corner rather than a sampled centre.

**That is two continuous coordinates plus one robustness axis** — a 2-D response
surface, small enough for a scan or a modest Sobol design and too small to
justify a Bayesian optimizer (§4).

### 3.3a Coverage is a degenerate coordinate and must be constrained or priced

**Decision (2026-07-29).** `total_QPs` is monotone decreasing in coverage, and
this simulation contains **no term that penalises coverage**. Left free, M2 will
therefore return \(f_{\rm cov} \to 1\) — a blanket backside film — as the
optimum. That answer is arithmetically correct for the QP-only objective and
unsuitable for a qubit chip.

This is the **same failure class as the electrode-size degeneracy of §3.1**, and
it must be handled the same way. §3.1 was caught because `setWidth`/`setHeight`
ranked #2 and #4 and the degeneracy was obvious; coverage was not caught because
its degeneracy only becomes visible once thickness is settled and coverage is
left as the dominant free axis.

**What the model cannot see.** A continuous normal-metal film on the substrate
back sits inside the qubit's mode volume. The omitted costs are at minimum
microwave loss from a lossy conductor coupling to substrate and package modes,
plus film stress, adhesion, and thermalisation constraints. None of these has
any representation in this codebase: `calculate_xQPs` computes a *QP-induced*
decoherence rate only, and there is no EM, dielectric, or conductor-loss content
anywhere in the pipeline. **The penalty cannot be derived from this simulation
and must come from the device team, a separate EM simulation, or literature.**

**Three admissible treatments**, in the order they should be preferred:

1. **Report the trade-off curve, do not optimize** *(recommended default)*.
   Sweep coverage, report QP yield against it, and let whoever owns the
   microwave budget pick the operating point. This requires no penalty model,
   commits to nothing unsourced, and is already what §4.1 says the deliverable
   is. It converts a degenerate optimization into a well-posed measurement.
2. **Bound coverage to a fabrication-acceptable range.** Honest and simple, but
   the bound is arbitrary unless sourced — record where it came from. Note the
   as-designed device sits at \(f_{\rm cov} = 0.64\) (island 200 µm, spacing
   50 µm), and the Morris box spanned ≈[0.33, 0.85].
3. **Add an explicit loss/fabrication penalty and optimize the sum.** Only once
   a defensible loss-versus-coverage relation exists. This is the
   `qLogNEHVI` direction of §4.3 and the one genuine future use for a
   multi-objective method.

**Do not** report a coverage optimum from the QP objective alone.

### 3.3c Do the material variables still matter once geometry is fixed?

Yes — and the mechanism matters more than the answer, because it determines
*which* ones survive. From the chain in §1.6, a phonon arriving at the backside
passes through six gates:

| # | Gate | Set by | Kind |
|---|---|---|---|
| 1 | lands on an island? | coverage = \((I/(I+S))^2\) | **geometry** |
| 2 | `G4UniformRand() < filmAbsorption` | `setBotAbs` | **material** |
| 3 | incident escape `exp(-4t/mfp)` | \(t\), \(v_s\tau\) | mixed |
| 4 | cascade stops, energy → heat | `setBotQPLim × setBotGapThres` | **material** |
| 5 | secondary leak `exp(-{1,3}t/mfp)` | \(t\), \(v_s\tau\), \(E\) | mixed |
| 6 | re-emitted only if \(E \ge 2\Delta_{\rm top}\) | `setTopGap` | **material** (fixed) |

**Geometry appears at exactly one gate.** Coverage sets what fraction of the
backside flux enters the film at all. Everything after that — whether the
absorbed energy stays absorbed, how far the cascade runs before it is dumped as
heat, and how much comes back out as still-dangerous phonons — is material.
Fixing gate 1 rescales the whole chain by a constant; it does not touch gates
2, 4, 5 or 6.

**The degeneracy of §1.6 (4) reinforces this rather than undermining it.**
Thickness and \(v_s\tau\) enter only as \(t/(v_s\tau)\). A degeneracy between a
geometry variable and a material variable means that fixing the geometry one
**transfers the full lever to the material one** — it does not cancel the effect.
Fixing \(t = 1\) µm leaves \(v_s\tau\) spanning the same 0.14 → 0.75 range of
secondary leak that thickness would have spanned (§1.6 table), because both act
on the same ratio.

Quantitatively, at fixed \(t = 1\) µm, scaling \(v_s\tau\) across the ±50%
Morris box moves the 382 µeV secondary leak by **5.4×**:

| \(v_s\tau\) scale | 0.25 | 0.5 | 1.0 | 1.5 | 2.25 |
|---|---:|---:|---:|---:|---:|
| mfp (µm) | 0.75 | 1.50 | 3.00 | 4.50 | 6.75 |
| secondary leak | 0.141 | 0.324 | **0.542** | 0.657 | 0.752 |

This is the mechanism behind the screen's ranking of `setBotPhLifetime` (#12,
μ\* 0.495) and `setBotVSound` (#14, 0.392) — parameters that a geometry-only
reading would have predicted to be dead. Likewise `setBotGapThres` (#15, 0.326)
and `setBotQPLim` (#16, 0.320) act purely at gate 4, which has no geometry
content whatsoever.

**The caveat that actually bites.** These material parameters are live in the
*physics*, but they are not independent *fabrication* knobs — \(v_s\), \(\tau\),
`gapThreshold` and `setBotAbs` are all properties of "which metal you deposited"
and must move together (§3.5). So the correct conclusion is not "optimize
`setBotVSound` freely," which would synthesize a fictional metal, but:

> **With geometry fixed, the live remaining lever is the choice of backside
> material.**

**This is the strongest deferred finding in the plan, and §0 puts it out of
scope.** It is recorded here so a future campaign can pick it up, and so no
Stage-4 write-up implies the question was asked and answered. Stage 4 is a
response study of *one* material: 2-D in thickness × constrained coverage, with
`setBotAbs` and the film lifetimes as robustness axes (§3.6). It establishes
nothing about whether Cu is the right backside metal.

### 3.4 Physical mechanisms governing the design space

Background needed to interpret any Stage-4 result correctly, not new
measurements.

- **Pair-breaking threshold.** A phonon can break a Cooper pair only if
  \(E_{\rm ph} \ge 2\Delta\). The gap sets whether a phonon can generate QPs at
  all, how many QPs one deposit represents (§3.1's confound), and the
  junction's own electrical properties — an optimizer must never vary gap
  without accounting for the coupled qubit-design consequence.
- **Film absorption and escape.** The Kaplan model's energy-dependent phonon
  mean free path is approximately
  \(\ell_{\rm ph}(E) = v_s\tau_{\rm ph} / (1 + a(E/\Delta - 2))\), with
  \(v_s\) the film sound speed, \(\tau_{\rm ph}\) the phonon lifetime, and
  \(a\) the lifetime slope. Thicker films and shorter lifetimes increase
  capture; lower sound speed shortens the mean free path. Near the present
  source energy (close to \(2\Delta_{\rm Al}\)), thickness/absorption/gap/base
  lifetime dominate and the lifetime slope barely matters — it becomes
  important only for a broad, realistic high-energy spectrum (§6, Milestone 0
  item 6).
- **Substrate scattering versus down-conversion are different mechanisms.**
  `scat` (elastic isotope/defect scattering) redirects phonons without removing
  energy and can have a non-monotonic effect on poisoning. `decay` (anharmonic
  down-conversion) actually depletes the population of pair-breaking-capable
  phonons. Do not treat them as interchangeable "loss" knobs, and do not vary
  the Si elastic/dynamical constants, sound speeds, and Debye frequency
  independently — they are properties of one substrate choice.
- **Boundary absorption and specularity may matter more for spatial risk than
  for the chip-wide total.** `setWallAbs` shortens ballistic phonon lifetime at
  chip edges. Specularity controls whether reflections stay mirror-like or
  diffuse, which can strongly reshape caustics, the spatial footprint of
  poisoning, and the correlation length of radiation-induced errors — exactly
  the kind of effect a mean-total objective is blind to but a worst-qubit or
  footprint objective (§3.2) would see.

### 3.5 Coherent material-preset schema

A preset must set every mutually dependent property together, or the search
can silently synthesize an unphysical material (e.g., Cu absorption + Ti sound
speed + Nb gap + an arbitrary Al lifetime). At minimum, a bottom-film preset
must specify: material identity; absorption probability or a calibrated
interface efficiency; sound speed; film gap or normal-metal treatment; the
down-conversion gap threshold; phonon lifetime and, where applicable, its
slope; a safe fixed QP cutoff; and subgap absorption where applicable. The
Milestone-0 preset audit (§6 item 3) should check every preset against this
list, not just check that it parses.

### 3.6 A third tier: calibration/uncertainty parameters

Not every non-design parameter belongs in the "fix and forget" bucket. A
middle tier — `setTopAbs`, `setTopPhLifetime`, `setBotAbs`, `setBotPhLifetime`,
substrate `decay`, optionally `scat`, `setWallPSpecProb`, `setBotPSpecProb` —
represents real material properties that are *not* well-mapped from
fabrication settings to a single calibrated number. `TopAbs`/`BotAbs` in
particular bundle acoustic transmission, interface quality, coverage, and
model calibration into one effective probability. Where no such map exists,
these should carry an uncertainty range (from calibration or literature) and
the campaign should look for designs that are **robust across that range**,
rather than either optimizing them freely (they are not fabrication knobs) or
pinning them to one point value (that overstates confidence). This is
distinct from the design variables of §3.3 and from the source/fidelity
scenario axis of §3.2.

### 3.7 Additional parameters to hold fixed or constrained

Beyond the electrode-geometry and QPDE exclusions already covered: numerical
controls (`/g4cmp/clearance`, `minEPhonons`, `phononBounces`, timeouts) are set
by convergence testing, not by the optimizer. `setBotGapThres` must be tied
to the junction pair-breaking threshold rather than varied as an independent
bottom-film property — it defines the energy below which down-converted
phonons stop being dangerous to the junction, so it is derived, not free.
`/g4cmp/temperature` should be fixed at the device operating point; the model
does not self-consistently update gap, lifetimes, and thermal QP population
together as temperature changes, so sweeping it independently would be
physically incomplete.

---

## 4. Algorithm selection

### 4.1 First campaign: structured grid, with optional scrambled-Sobol fill

Revision 2 kept three methods (Sobol, raw-BoTorch `qLogNEI`, preset
enumeration) on the argument that cheap evaluations make sample efficiency
worthless. That argument is correct but not decisive on its own — a cheap
evaluation still leaves you wanting a good proposal rule if the space is large.
§3.3 supplies the decisive part: with the top stack fixed and Cu as the only
currently qualified backside material, **the campaign-1 space is two continuous
coordinates.**

| # | Method | Role | Cost to implement |
|---|---|---|---|
| 1 | **Factorial grid** over physically chosen thickness and coverage levels | Primary interpretable campaign | minimal once explicit-design input exists |
| 2 | **Scrambled Sobol** over (thickness, coverage) | Optional residual fill / backend smoke test | ~30 lines |

Because the mechanism predicts monotone saturation, begin with a compact grid
whose levels carry physical meaning: include no-film as a control and, for the
film, \(t=\{1,2,5,10\}\) µm crossed with about 5–9 coverage levels including the
reference-like and boundary cases. Replicate the baseline and endpoints more
heavily. Add roughly 32–64 scrambled-Sobol points only if interpolation error,
interaction structure, or an algorithm-switching demonstration justifies them.
The exact count should be chosen after the positive control and position check,
not fixed in advance at 128–256.

**Why not BoTorch on a 2-D surface.** A GP over two coordinates with ~200 points
is not solving a problem the structured design leaves open. A sampled grid is
**not a posterior** and by itself supplies no calibrated predictive uncertainty;
it is simply dense, interpretable data on which interpolation, a GP, or a
monotone/saturating regression can later be fitted. BoTorch earns its place when
coordinates are added (a validated preset library, uncertainty scenarios, or
the top stack unfrozen), or if the measured 2-D surface is unexpectedly rugged.
Keep the proposal protocol generic so changing backends is a configuration
change.

**The deliverable is a response surface plus a reference-inspired comparison,
not a prematurely claimed optimum.** The stored Morris marginal and the code
model predict monotone coverage and monotone-saturating thickness, but neither
trend has yet been isolated experimentally. The campaign tests those
predictions, reports where each axis flattens if they hold, and checks the
island-versus-blanket ordering under this setup (§6 item 1).

**If BoTorch is ever needed, use raw BoTorch, not Ax.** `SingleTaskGP(X, Y,
Yvar)` + `qLogNoisyExpectedImprovement` + `optimize_acqf` is about forty lines,
maps exactly onto the `propose()` protocol in §5.1, and takes candidate-specific
observation variance directly from replicas. Ax is itself an
experiment-management framework — with the SQLite store and controller specified
below, running Ax means running two, and its internal state fights the
"rebuild the backend from the database each round" rule. Ax 1.x is also a
recent, still-moving API. This judgement is unchanged; only its priority is,
from "second thing to build" to "build if a real multi-coordinate space appears."

*Verified*: `botorch 0.18.1` and `ax-platform 1.3.1` both resolve cleanly
against the `QDX_torch` environment (Python 3.14.6, torch 2.13.0). This is a
complexity judgement, not a compatibility one.

**Model the log.** Whenever a surrogate is eventually fitted, fit on
`log(QP density)` with delta-method variance from replicas. Zero-signal points
are now 0.12% of the design, so the hurdle and zero-inflation machinery the
earlier plan contemplated is unnecessary.

**Preset enumeration is removed from Stage 4, not deferred within it.**
Revision 2 justified it with “at most six bottom presets and three top presets.”
§3.3 shows the six bottom files are three controls, Cu, a Ti candidate, and
defective Al. With the top stack fixed (§3.1) and Cu fixed as the scope (§0),
**there is no categorical axis at all** — Stage 4 has no use for preset
enumeration, and a material campaign is future work rather than a later
milestone of this one.

### 4.2 Drop four

| Dropped | Reason |
|---|---|
| **SMAC3** | Its advantage is mixed/conditional/failure-heavy spaces. Campaign 1 has no categorical coordinate. Its larger dependency and state-management footprint is unjustified for the 2-D Cu surface; reconsider only when several validated presets or conditional fabrication choices exist. |
| **Multi-fidelity (`qMFKG`, Hyperband)** | Event count primarily changes estimator variance rather than the target mean, so a specialized bias-across-fidelities model is unnecessary for campaign 1. Use explicit promotion/replication with measured SEM. The statistics are simple, but safe scheduling, seed provenance, merging, and duplicate prevention are not “ten lines.” |
| **TuRBO** | Three to five dimensions do not need trust regions, and the reference implementation assumes noise-free observations. |
| **NSGA-II** | Evaluation-hungry and unnecessary before a fabrication-cost objective is defined. It is not universally dominated; for a future cheap surrogate it can be a useful independent Pareto-front cross-check, while `qLogNEHVI` is usually more sample-efficient for expensive noisy evaluations. |

Also dropped, and this was correct in the previous plan: **warm-starting from
the 6,912 Morris points.** They contain independently varied nonphysical
material combinations, elastically unstable crystal points, and a pre-seed-fix
random-stream history. Their legitimate use is exactly what §1.5 does with them
— choosing coordinates and bounds.

### 4.3 Optional tier two

Both of these presuppose a design space larger than the one §3.3 leaves. They
are recorded for the campaign that follows a cited preset library or an
unfrozen top stack, and neither should be built for the Cu-only coverage scan.

- **CMA-ES** (`pip install cma`, ~30 lines) as the independent cross-check
  *instead of* SMAC3. Cheap evaluations invert the earlier ranking: CMA-ES's
  evaluation-hunger is now affordable, its dependency footprint is trivial, and
  it tests "did the surrogate find a robust basin" more directly than a second
  Bayesian method would. *(Premature on a 2-D monotone surface — there is no
  basin to find until the preset axis and/or the top stack are added.)*
- **`qLogNEHVI`** for a QP-versus-fabrication Pareto front, once the secondary
  metrics are trustworthy and the fabrication cost has an agreed scale. **This
  is the most likely genuine future need**: coverage is monotone in QP yield but
  presumably not free to fabricate, so the real question is the trade-off curve,
  and a fabrication cost scale would turn a boundary answer into a decision.

---

## 5. Architecture

Keep the previous plan's principles — they were right and are unchanged:

1. the optimizer proposes semantic designs only and never writes a Geant4
   command;
2. one deterministic domain layer expands and validates every design;
3. the existing Stage-1 macro writer stays the single physics writer, extended
   with an explicit-design mode;
4. a database, not optimizer memory, is the source of truth;
5. propose / prepare / run / collect / recommend are separate resumable
   commands;
6. raw evaluations are retained, never only an aggregate;
7. failures and infeasible designs are not QP measurements;
8. every result carries a protocol version.

**Trim the package.** For three backends and ≤5 coordinates, eight modules is
over-built:

```text
stage4_config.toml
stage4/
    space.py          # coordinates, presets, coverage/pitch map, validation, design hash
    store.py          # SQLite: candidates, evaluations, batches
    completeness.py   # shared validator, called by Stage 2b, Stage 3, and Stage 4
    controller.py     # state machine + runner adapter (split later if it hurts)
    cli.py
    backends/
        base.py  sobol.py  botorch_qlognei.py
tests/
    test_space.py  test_completeness.py  test_store.py  test_fake_loop.py
```

`types.py` and `runner.py` fold into `controller.py` until they earn separation.

### 5.1 Backend protocol

```python
class OptimizerBackend(Protocol):
    name: str
    def propose(self, space, observations, pending, batch_size, seed
                ) -> list[dict[str, object]]:
        """Return semantic parameter dictionaries only."""
```

Registry: `sobol -> SobolBackend`, `botorch_qlognei -> BoTorchBackend`. Imports
must be lazy so selecting `sobol` does not require torch. Rebuild the backend
from the database each round.

### 5.2 Records

`Candidate` (id, semantic params, design hash, provenance) · `ExpandedDesign`
(macro params, lattice params, preset ids, validation version) · `Fidelity`
(**`total_events`**, `n_positions`, `n_replicas`, `position_set_id`,
`seed_bank_id`) · `Evaluation` (status, raw metrics, expected/observed event and
position counts, wall time, results dir, failure kind) · `Observation`
(objective mean, SEM, secondary metrics, cost, complete).

SQLite unique key on
`(candidate_id, fidelity_id, scenario_id, replica, seed_bank_id)`; only the
controller writes.

### 5.3 Seeds

Two seed policies are valid, but they cannot be written as one formula:

- **paired/common-bank experiment:** derive the random seed from
  `(experiment_seed, scenario_id, seed_bank_id, replica, position)` and omit the
  design hash, so matched candidates really receive the same initial stream;
- **independent-candidate experiment:** additionally include `design_hash`.

The evaluation identity still includes `candidate_id` under either policy.
Record `seed_policy` explicitly, make retries reproduce the exact seed, and use
a fresh holdout `seed_bank_id` for final validation. Measure whether the paired
policy actually reduces A/B difference variance before using that gain in power
calculations (§1.4 and §5.5).

### 5.4 Failure handling

Unchanged from the previous plan and still correct: reject invalid designs
before simulation; stop the batch on a macro round-trip mismatch; retry
transient failures with the same seed; record reproducible design-dependent
failures as a feasibility outcome. **Never convert a crash into a QP value** —
that mixes software reliability with material physics and can manufacture a
false optimum away from a technical failure region.

### 5.5 Stage-1 integration constraints

Revision 2 specified "the existing Stage-1 macro writer stays the single physics
writer, extended with an explicit-design mode" without recording what that
extension has to work around. These are read from `stage1_run_simulations.py`,
and each one will cause a rewrite if discovered during implementation instead of
before it.

1. **Stage 1 is configured at import time.** `N_POSITIONS`, `N_REPLICAS`,
   `TOTAL_EVENTS`, `SEED_BASE`, `MACRO_TEMPLATE`, `RUN_ID` and the memory caps
   are all module-level constants read from the environment when the module is
   imported (lines ~140–230). A controller cannot import Stage 1 and call it
   twice at two fidelities. **The runner adapter must `subprocess` Stage 1 with
   an environment block**, exactly as the README's launch commands do.
2. **Each invocation mints a fresh UUID run directory.** `RUN_ID` is a new UUID
   per process unless `SENSITIVITY_INTERNAL_RUN_ID` is set (which exists for
   worker re-import, not for reuse). A campaign of *N* batches produces *N*
   `results/<run_id>/` trees, so the store must key an evaluation on
   `(run_id, sample_name)`; `sample_name` alone is not unique across a campaign.
3. **`generate_runfiles(samp, samp_No)` consumes a positional vector.** It walks
   a single cursor `k` across `macro_params + config_params`, and ratio
   parameters (`vtrans`) are resolved against config values written earlier in
   the same pass. An explicit-design mode must therefore hand Stage 1 a
   **full-length `samp` row in exactly that order** — defaults from
   `sensitivity_params.py`, overridden at the design coordinates' indices — not
   a parallel writer. This is what keeps "one physics writer" true, and it makes
   the Morris path byte-identical by construction rather than by care.
4. **Presets cannot be expressed through the `samp` vector at all.** A backside
   preset sets `setBotNormal`, `setBotSourceMat`, `setBotGap` and
   `setBotSubGapAbs` — none of which appear in `sensitivity_params.py`, because
   they were never swept. Also, `SENSITIVITY_MACRO_TEMPLATE` is a module-level
   value, so one Stage-1 invocation cannot use a different derived template for
   every candidate. For the Cu-only campaign use one pinned Cu base template.
   For a later mixed-preset campaign, the minimal safe option is to **group
   candidates by preset** and invoke Stage 1 once per derived template; a later
   generalized writer may apply validated pinned overrides per expanded design.
   Do not launch one Stage-1 process per candidate merely to change templates.
   Avoid `/control/execute`: control presets carry geometry commands whose
   placement relative to `/run/initialize` must remain correct.
5. **`sub_run_seed()` has exactly two modes and needs a third.** It groups by
   `design_point_index // trajectory_length` (Morris) or by design point
   (noise floor). Neither is meaningful for an explicit design. Add a mode
   keyed according to the explicit `seed_policy` of §5.3, and extend
   `assert_seed_bank_is_sound()` with a matching third branch, or it will fail
   closed on a legitimate campaign.
6. **Temper the expected gain from paired seeds.** §1.4 claims sharing the bank
   across candidates "reduces the variance of the difference at no cost." The
   "no cost" is right; the size is not established. Two candidates with
   different material parameters consume different numbers of random draws, so
   their streams decorrelate after the first divergent event and stay
   decorrelated for the remaining ~125,000. Keep the pairing — it is free and
   cannot hurt — but **do not budget the variance reduction** until it is
   measured on a real A/B pair.
7. **Restore the memory-budget configuration invariants.** The current code
   checks whether `SENSITIVITY_TOTAL_MEM_GB` exceeds host-available memory, but
   it no longer rejects non-positive total/per-sample budgets or
   `SENSITIVITY_PER_SAMPLE_MEM_GB > SENSITIVITY_TOTAL_MEM_GB`. Those checks were
   present before the event-accounting edit and are prerequisites for the
   controller's fail-fast contract. Add unit tests for all three invalid cases.

**A free exact regression gate.** All 221,184 generated macros from the current
screen are still on disk under
`output/morris_mimir_c7a18a17-91ce-491b-8124-78a4ff16576a/macros/`. M2's
requirement that "with no `--design-file`, the Morris path must stay
byte-for-byte identical" is therefore directly testable: regenerate with
`SENSITIVITY_GENERATE_ONLY=1`, `SENSITIVITY_MORRIS_SEED=20260727` and
`SENSITIVITY_TOTAL_EVENTS=4000000` (the original total, not 4,032,000) and
compare hashes. This turns the strongest correctness requirement in the plan
from an aspiration into a gate, at the cost of one generate-only pass.

---

## 6. Milestone 0 — what to establish before any optimizer

Ordered by value. The first four are cheap and block everything.

1. **Positive control plus reference-inspired comparison.** *(Restored to revision 2's
   form; the intervening claim that it would return a flat line is withdrawn —
   §1.6a.)* One one-factor scan: no-film → 1, 2, 5, 10 µm backside film,
   everything else at baseline. Begin at the pilot total and promote only the
   reference and endpoints if their confidence intervals require it. §1.6
   predicts a monotone reduction driven by the secondary-leak channel, roughly
   30× in leaked pair-breaking flux from 1 to 10 µm, diluted by the other gates.
   Run **blanket (`setBot2BlanketFilm.mac`) versus islands at matched thickness**
   in the same batch. This is inspired by Yelton et al. 2024 §11 (10 µm Cu
   islands outperforming a 1 µm Cu film), but it is not a direct reproduction
   unless source spectrum, layout, boundary conditions, and observable also
   match.
   * If the thickness response is monotone and the island/blanket ordering
     matches the paper, the mechanism is present and the campaign is sound.
   * If thickness is flat, the §1.6 reading is wrong about the binary and must
     be re-derived before anything else proceeds.
   * If the ordering inverts, investigate source/geometry/model differences
     before using the setup for a Yelton-based claim. The internal Cu response
     study may still be meaningful, but its external validity is then
     unestablished.

   This remains the highest-value single run available, and it is now both a
   positive control *and* a comparison against an external result.
2. **Confirm the two-channel structure directly.** Revision 3's error (§1.6a)
   came from reading one code path and reasoning about the other, so verify the
   mechanism rather than the conclusion: run with `/g4cmp/verbose 3` on a small
   sample and confirm from `G4CMPNormal`'s own output that (a) incident escape
   at 1 µm is ~3%, (b) `CalcReflectedPhononEnergies` is reached and leaks at the
   predicted rate, and (c) re-emitted secondaries are gated at
   \(2 \times\) `setTopGap`. Minutes. *(Replaces "geometry validation above 1.5
   µm," which is not a question — no solid is built at any thickness, §1.6 (5).)*
3. **Cu preset audit — the only preset work in scope (§0).** Trace
   `setBot2Cu.mac`'s gap threshold, sound speed, phonon lifetime and absorption
   probability to citations, and confirm the normal-metal routing
   (`setBotNormal true` -> `G4CMP::KaplanNormal`, mfp = `v·τ/(E/k_B)`,
   incident `frac = 2`) is the intended physics rather than an inherited
   default. This blocks every Stage-4 number. Separately, **fix or delete
   `setBot2Al.mac`** so a defective preset cannot be selected by accident —
   hygiene, not a prerequisite. The Ti audit and library extension are struck.
4. **Per-position metrics in Stage 2.** Required for robust and tail-risk
   objectives, for the completeness invariant of §2.3, and to repeat the
   diagnostics of §1.3 on new runs. The hits files are already per-position, so
   this is bookkeeping.
5. **Decide the objective** (§3.1) — the electrode-size degeneracy. **Partly
   settled 2026-07-28**: the top/junction stack is fixed, which removes the
   degeneracy and the gap confound. What remains is choosing between the mean
   and a worst-electrode statistic, which depends on item 4.
6. **Source-spectrum validity.** `phonon_Caustic` at 0.6–1.5 meV is a
   monochromatic ballistic construct; real poisoning comes from radioactivity
   and cosmics depositing keV–MeV through a full downconversion cascade. The
   optimal backside stack for ballistic 1 meV phonons need not be optimal for a
   real cascade. Validate finalists under two or three source scenarios at
   minimum. This is a larger validity risk than any algorithm choice, and it
   interacts with the known finding that the Nb top film is inactive at present
   source energies.
7. **`minEPhonons` convergence.** Still unverified after a 10× move.
8. **Position-set convergence for the actual Stage-4 contrasts.** Run the small
   baseline/extreme/material-stratified check defined in §1.3. This can be tens
   of points, not a production-scale campaign.
9. **Fidelity legality and power.** Use 4,032,000 as the common pilot total,
   compute larger legal totals from the target precision (§2.2), and validate
   every triple at `init`.
10. **Restore Stage-1 memory-budget guards** (§5.5 item 7) before a controller
    can launch unattended batches.
11. **Post-seed-fix reproducibility check.** One command; closes out the
   retracted `clock()` finding.

**Not required:** a full production-scale 16/32/64 campaign, or backside-solid
geometry validation above 1.5 µm (§1.6 — no solid film geometry is built).
A small position-set convergence check **is** required.

---

## 6a. M0p and M0a results (2026-07-29)

**M0p is complete.** Memory guards restored; explicit-design mode added to
Stage 1 (`SENSITIVITY_DESIGN_FILE`, `SENSITIVITY_SAMPLE_PREFIX`,
`SENSITIVITY_SEED_BANK_ID`); third seed mode and validator branch added.

*Regression gate passed.* Regenerating the first 54 design points against the
historical template (`git show a3390bf:sensitivity_template_screen.mac`, which is
the version the screen actually ran — not the current file) with
`SENSITIVITY_EXPLICIT_SEEDS=0` reproduces **1,728 / 1,728 macros and 54 / 54
lattice configs byte-identically**, after normalizing only the run-id embedded in
the `/g4cmp/HitsFile` path. The Morris path is unchanged.

### M0a pilot — 8 points, `TOTAL = 4,032,000`, 256 sub-runs, 1m 48s, 0 failures

`results/morris_mimir_cc758fb7-315d-4ea6-96fd-9b869c436028`

| row | design | mean `total_QPs` | vs t=1 |
|---:|---|---:|---:|
| 0 | no-film control (`setBotAbs` 0) | 775 | 9.57 |
| 1 | islands, t=1 µm (baseline) | 81 | 1.00 |
| 2 | islands, t=2 µm | 66 | 0.82 |
| 3 | islands, t=5 µm | 49 | 0.61 |
| 4 | islands, t=10 µm | 37 | 0.46 |
| 5 | blanket, t=1 µm | 31 | 0.38 |
| 6 | blanket, t=10 µm | 19 | 0.24 |
| 7 | zero coverage | 866 | 10.69 |

### M0a promotion — 3 points, `TOTAL = 40,320,000`, R=8, fresh seed bank 1

`results/morris_mimir_bb23db68-f2c7-4971-9552-beb39355ad53` (384 sub-runs,
2m 31s, 0 failures). Selected on bank 0, confirmed on bank 1.

| design | mean | SEM | rel. SEM |
|---|---:|---:|---:|
| islands, t=1 µm | 195.0 | 7.5 | 3.8% |
| islands, t=10 µm | 130.5 | 4.3 | 3.3% |
| blanket, t=1 µm | 93.5 | 5.0 | 5.4% |

| comparison | difference | z | verdict |
|---|---:|---:|---|
| islands t=1 vs t=10 | +49.4% | +7.48 | **resolved** |
| islands t=10 vs blanket t=1 | +39.6% | +5.60 | **resolved** |
| islands t=1 vs blanket t=1 | +108.6% | +11.28 | **resolved** |

*(Counts scale with `n_sim`, so pilot and promotion absolute values differ by the
2.5× event ratio; 81 × 2.5 = 203 ≈ 195 confirms consistency. Comparisons within a
run are at matched `n_sim`.)*

### What the three Milestone-0 branches returned

1. **The mitigation mechanism exists.** Removing the absorber — by either
   `setBotAbs = 0` or zero coverage — raises QP yield ~10×. The backside stack is
   doing real work.
2. **Thickness is live and monotone**, 1 → 10 µm giving a 49% reduction at
   z = 7.5. This **confirms the corrected §1.6** and definitively closes out the
   withdrawn saturation claim of §1.6a. The observed 2.2× (pilot) is the
   predicted ~30× secondary-leak reduction diluted by the other five gates,
   which is the expected order.
3. **The reference ordering is inverted, and the inversion is resolved.** Yelton
   et al. report 10 µm Cu islands outperforming a 1 µm Cu film. This model
   reports the **opposite**: the 1 µm blanket film beats 10 µm islands by 39.6%
   at z = 5.6. This is not a noise artifact and will not be fixed by more
   statistics.

**Consequence — this is the §6 item 1 third branch.** Per the plan's own
pre-registered rule: *"If the ordering inverts, investigate source/geometry/model
differences before using the setup for a Yelton-based claim. The internal Cu
response study may still be meaningful, but its external validity is then
unestablished."*

**The leading explanation is that the inversion is not an error.** `total_QPs`
is monotone in coverage and this model prices nothing against coverage (§3.3a),
so "blanket beats islands" is the correct answer to the QP-only question. A real
device pays for backside metallisation in microwave loss, stress and
thermalisation, none of which is represented anywhere in this pipeline. On that
reading the model and the experiment are not in conflict at all: **the model
reports one term of a two-term objective, and the sign of the total is set by
the term it cannot see.** The measured 39.6% QP advantage of the blanket film is
then a quantitative statement of how much QP suppression a patterned backside
gives up — which is exactly the input the trade-off curve of §3.3a needs, and is
more useful than a bare ordering.

Candidate explanations, in the order to test them:

* **Missing penalty term** *(leading — §3.3a)*. Testable outside this
  simulation: obtain or estimate a microwave-loss-versus-coverage relation and
  check whether it reverses the ordering at the paper's operating point. If it
  does, the model is validated rather than impeached.
* **Source spectrum** (§6 item 6) — a monochromatic 0.6–1.5 meV ballistic probe
  versus a real keV–MeV cascade. Coverage and thickness weight differently under
  a broad spectrum, since `mfp ∝ 1/E` in the normal-metal law. Testable here.
* **Not like-for-like** — layout, substrate thickness, boundary conditions and
  observable all differ from the paper's device.

**M2 may proceed once coverage is constrained or priced per §3.3a**, and should
be reported as a trade-off surface rather than an optimum. What must not happen
is an unconstrained coverage optimization, which would recommend the blanket
film on the strength of the one term the model happens to contain.

---

## 6b. Decided path (2026-07-29)

**Status: decided, not executed.** No M2 design file, run directory, or result
exists as of this writing; the newest stored runs are the M0a pilot
(`cc758fb7`) and promotion (`bb23db68`) of §6a. Everything below is the agreed
forward program.

1. **M2 takes §3.3a Option 1.** Publish the thickness × coverage QP response
   surface. No penalty model is assumed and no operating point is chosen.
2. **Reporting rule, binding on every M2 artifact.** Do not write "100% coverage
   is optimal." Write that maximum coverage *minimizes the modeled QP term*, and
   state in the same sentence that the model contains no microwave-loss, stress,
   or fabrication term. The two claims must not be separable by a reader who
   sees only a figure caption.
3. **Material optimization runs at fixed coverage = 0.64** — the as-designed
   geometry (island 200 µm, spacing 50 µm). This is also the M0a islands
   baseline, so later Cu results are directly comparable to it without a
   bridging run. Revalidated on fresh seed bank 2 — see §6c.
4. **Material comparisons run under multiple source scenarios and
   interface-parameter uncertainty** — §6 item 6 for the spectrum axis, §3.6 for
   the calibration tier (`setBotAbs`, `setTopAbs`, film lifetimes), which carry
   uncertainty ranges rather than point values.
5. **Microwave loss versus coverage and pitch is obtained externally** — CST,
   HFSS, COMSOL, another EM solver, measurement, or the device team. It cannot
   come from this pipeline (§3.3a).
6. **The final problem becomes constrained or multi-objective** once (5) lands —
   the `qLogNEHVI` direction of §4.3.
7. **A design point is selected only after the acceptable microwave/fabrication
   region is defined.** Not before, and not from the QP term alone.

### Two prerequisites this path inherits

**The Cu preset's routing must still be validated (retained M0b).** Ti is out
of scope (§0), but the Cu audit is not: `setBot2Cu.mac` sets
`setBotNormal true`, which routes to `G4CMP::KaplanNormal` with
mfp = `v·τ/(E/k_B)` and incident `frac = 2`. Every §6a number depends on that
being the intended physics rather than an inherited default. This blocks
publication of any Stage-4 result, and it is cheap.

**Pitch is QP-neutral but may not be EM-neutral, which makes it a free lever.**
§1.5 measured pitch at a 1.02× swing — dead for QP yield — and it was dropped
from the design space on that basis. But item (5) asks for loss versus coverage
*and pitch*. If loss depends on pitch while QP yield does not, then pitch is a
coordinate only the penalty term can see, and it can be tuned to reduce
microwave loss **at zero QP cost**. That is the cheapest win available in the
whole multi-objective formulation, and it exists only because the two objectives
have different null spaces. Ask the EM side for the pitch dependence explicitly
rather than for coverage alone.

**Pitch stays out of the QP objective — now measured, not inherited.** §1.5's
1.02× was a *marginal* swing from the Morris screen. It has since been checked
directly at fixed coverage 0.64, across two independent position sets:
**+2.4% ± 4.9%, |effect| < 12% at 2σ**. See §6d. The position-phase confound
that would have invalidated a single-position-set version of this test was
controlled for and does not bite.

---

## 6c. Cu reference, final validation (2026-07-29)

`results/morris_mimir_5c8de0e4-76c2-46fa-99fd-abcac848a31f` — 1 design point,
`TOTAL = 40,320,000`, P=16, R=8, **fresh seed bank 2**, 128 sub-runs, 0 failures.

**Reference geometry:** Cu, `setBotThickness` 1 µm, island 200 µm / spacing
50 µm → coverage **0.640**, pitch **250 µm**, top stack fixed, `setBotAbs` 0.736.

| bank | role | R | mean `total_QPs` | SEM |
|---|---|---:|---:|---:|
| 1 | selection (§6a promotion) | 8 | 195.0 | 7.5 (3.8%) |
| **2** | **final validation** | 8 | **185.8** | **4.5 (2.4%)** |

Difference +5.0%, SE 8.7, **z = +1.06 — consistent.** The reference reproduces
on streams it was never selected on, so the §6a promotion results are not an
artifact of seed bank 1.

**The Stage-4 Cu reference value:**

> **185.8 ± 4.5 `total_QPs`** at `n_sim` = 5,040,000 per replica
> (QP yield per event **3.686e-05**), R=8, seed bank 2.

Every later Cu result is quoted against this. Because the material comparisons
of §6b item 3 run at this same geometry, they need no bridging run. Use a fresh
`SENSITIVITY_SEED_BANK_ID` for each subsequent validation so no result is
confirmed on the bank that selected it.

Reproduce with:

```bash
python stage4_make_pilot_design.py --set reference -o cu_reference_design.csv
SENSITIVITY_DESIGN_FILE="$PWD/cu_reference_design.csv" \
SENSITIVITY_MACRO_TEMPLATE="$PWD/sensitivity_template_screen.mac" \
SENSITIVITY_SAMPLE_PREFIX="CuRef_" \
SENSITIVITY_N_POSITIONS=16 SENSITIVITY_N_REPLICAS=8 \
SENSITIVITY_TOTAL_EVENTS=40320000 SENSITIVITY_SEED_BANK_ID=2 \
SENSITIVITY_MAX_WORKERS=32 SENSITIVITY_SAMPLE_TIMEOUT=1800 \
python -u stage1_run_simulations.py
```

---

## 6d. Pitch equivalence and the Cu routing audit (2026-07-29)

### The position-phase confound, and why extra events cannot fix it

The 16 injection sites are **fixed absolute Sobol coordinates** over ±4 mm
(`build_source_positions`), chosen once from `SENSITIVITY_POSITION_SEED` and
wholly independent of the backside pattern. `WaffleKaplanElectrode` phases on
`fmod(|x|, l_island + l_spacing)`. **Doubling the pitch therefore re-phases every
site against the island pattern**, so a pitch contrast measured at one position
set is confounded with a change in where in the cell each caustic lands.

More Geant4 events do not remove this. Events shrink the Monte-Carlo term;
the position term is a fixed spatial-quadrature offset (§1.3, ≈15% at P=16) that
is unchanged by event count. The control is a **second independently scrambled
position set**, which costs one environment variable.

### Result — pitch equivalence at constant coverage 0.64

Two runs, identical except for `SENSITIVITY_POSITION_SEED`; each 2 design points
× P=16 × R=8 at `TOTAL = 40,320,000`, seed bank 3, 256 sub-runs, 0 failures.
Geometries: island/spacing 200/50 (pitch 250 µm) and 400/100 (pitch 500 µm),
both coverage exactly 0.640.

| position seed | run | pitch 250 | pitch 500 | 500 − 250 | z |
|---|---|---:|---:|---:|---:|
| 20260727 | `20f9ab95` | 177.8 ± 8.4 | 182.5 ± 7.9 | +2.7% | +0.41 |
| 20260801 | `f3d38891` | 158.5 ± 6.9 | 161.8 ± 9.9 | +2.1% | +0.27 |

**Pooled: +2.4% ± 4.9%, z = +0.49, 95% interval [−7.5%, +12.3%].**

Three readings, in order of importance:

1. **Pitch is QP-equivalent to within ±12% at 2σ** — consistent with zero, and
   far below the levers this campaign does move (thickness 1→10 µm gives −49% at
   z = 7.5; islands→blanket gives −109% at z = 11.3). Note this is an *upper
   bound*, not a demonstration of exactly zero; the honest claim is "pitch
   changes QP yield by less than ~12%," which is what licenses excluding it.
2. **The effect does not depend on the position set** — interaction
   +1.5 ± 16.6, z = +0.09. The confound was a real methodological requirement
   and it does not bite here.
3. **The position set shifts the absolute level by ~12%**, and shifts *both*
   geometries by nearly the same amount (+19.2 and +20.8 QPs). That is the
   signature of a common spatial-quadrature offset rather than noise, and it is
   §1.3's blocking argument confirmed directly: the positional term is a
   systematic offset that **cancels in a contrast** while dominating the
   absolute value. It is also why §1.3's warning stands — absolute yields from
   P=16 are not trustworthy even though contrasts are.

**Decision: pitch stays out of the QP objective** and is handed to the EM study
as a free lever (§6b item 5). Any *absolute* Cu yield must still carry the P=16
quadrature caveat.

### Cu preset audit (retained M0b) — routing confirmed, one constants finding

**Routing is correct and internally consistent.** `setBot2Cu.mac` /
`sensitivity_template_screen.mac` set `setBotNormal true` before
`/main/detector_param/update` and `/run/initialize`;
`PhononDetectorConstruction` then takes the `fBotNormal` branch, and
`WaffleKaplanElectrode::AbsorbAtElectrode` dispatches to `G4CMP::KaplanNormal`.
Verified consistent:

* The normal branch populates `filmThickness`, `gapEnergy`, `gapThreshold`,
  `lowQPLimit`, `phononLifetime`, `vSound` — exactly the six
  `G4CMPNormal::SetFilmProperties` requires, and **deliberately not**
  `phononLifetimeSlope`, which the normal-metal mfp law does not use. This
  matches `setBotPhLifetimeSlope` being commented out in
  `sensitivity_params.py` and in the preset macro.
* `setBotGap 0.0` makes `IsSubgap()` identically false, which is the intended
  normal-metal limit, and `G4CMPNormal` has the
  `if (gapEnergy <= 0.) return 1.` short-circuit **commented out**, so a zero gap
  does not collapse escape probability to 1. Deliberate, and load-bearing.

**Finding — `setBotGapThres` is not tied to the junction threshold, contrary to
§3.7.** The QP cascade in the backside film terminates at
`setBotQPLim × setBotGapThres` = 2 × 180 µeV = **360 µeV**, while
`WaffleKaplanElectrode` re-emits a secondary only above
`2 × setTopGap` = **382 µeV**. §3.7 requires the bottom threshold to be *derived*
from the junction pair-breaking threshold; it is currently an independent 180 µeV
that merely resembles Δ_Al = 191 µeV. Consequences:

* a 6% mismatch between two quantities the model treats as the same physics;
* a dead band [360, 382] µeV in which quasiparticles are still tracked in the
  film but any phonon they emit is discarded at the gate.

Neither invalidates §6a — both thresholds were fixed across every design point,
so this is a common offset, not a differential effect. But `setBotGapThres`
should be **derived from `setTopGap`** before any absolute yield is published,
and the choice recorded.

**Still open (literature, not code):** provenance for `setBotAbs` 0.736,
`setBotVSound` 2.608 km/s, `setBotPhLifetime` 5.1 ns, `setBotQPLim` 2. Until
these are cited, §6a numbers are conditional on unsourced constants. **M0b
remains incomplete.**

---

## 6e. `setBotGapThres` settled, and M0c's finding (2026-07-29)

### Step 1 — `setBotGapThres` is below noise; pin it as derived

`results/morris_mimir_78e2b67c-0594-4f4f-9ae9-dfeb8178c102` — reference geometry,
2 points, P=16, R=8, `TOTAL = 40,320,000`, fresh seed bank 4, 0 failures.

| `setBotGapThres` | cascade floor | mean `total_QPs` | SEM |
|---|---:|---:|---:|
| 180 µeV (as shipped) | 360 µeV | 181.5 | 6.5 |
| **191 µeV (= `setTopGap`)** | **382 µeV** | **181.5** | 8.3 |

**Difference +0.0%, z = +0.00, 95% interval [−11.7%, +11.7%].**

The §6d dead band [360, 382] µeV is real but carries no measurable signal — the
QP population in that window is negligible, so closing it changes nothing.

**Decision: set `setBotGapThres = setTopGap` (191 µeV), derived per §3.7.** It is
free, it removes the 6% mismatch and the dead band, and — now measured — it does
not perturb any existing result. §6a and §6c numbers stand unchanged. Note the
bound is ±12%, so this licenses "no detectable effect," not "provably zero."

### Step 2 — M0c implemented

Stage 2 now computes per-position metrics. `calculate_QPs` only *accumulates*
into `QPNos[electrode, time]`, so summing the per-position arrays is exactly the
pooled result — the increments are `int(round(E/gap))` and integer sums do not
reassociate. Per-position therefore costs **no extra work**, not double.

*Regression check:* reprocessing the M0a pilot reproduces **all 28 pre-existing
columns identically**; eight columns are appended (`positions_present`,
`positions_expected`, `position_QP_{mean,sd,max,min}`, `position_effective_n`,
`position_max_electrode_QPs`). Stage 2b and Stage 3 select by column name, so
appending is safe. `qps/*_QPs.npz` additionally retains the full
`per_position_QPNos` (position × electrode × time), so a spatial or tail-risk
objective can be built later without re-reading hits files.

### The finding: positional concentration collapses as the absorber improves

Effective number of contributing sites, \((\sum x)^2/\sum x^2\), over the M0a
pilot:

| design | `total_QPs` | **eff. sites (of 16)** | max site | min site |
|---|---:|---:|---:|---:|
| no-film control | 775 | **14.9** | 68 | 24 |
| zero coverage | 866 | **14.9** | 84 | 31 |
| islands t=1 | 81 | 9.4 | 14 | 0 |
| islands t=2 | 66 | 8.8 | 14 | 0 |
| islands t=5 | 49 | 6.8 | 12 | 0 |
| islands t=10 | 37 | 7.7 | 8 | 0 |
| blanket t=1 | 31 | 5.1 | 10 | 0 |
| **blanket t=10** | **19** | **4.3** | 7 | 0 |

Absorber off: 14.9 effective sites. Absorber on: 4.3–9.4, mean 7.0. **Several
sites contribute exactly zero** once the absorber works.

**Why this matters for M2, and it is not a small point.** §1.3 licensed P=16 for
*ranking* on the grounds that the positional response pattern is
design-independent (profile r = 0.9878 under lattice rotation) so the quadrature
error cancels in contrasts. That test varied crystal orientation. It did **not**
test the backside stack, and the backside stack changes the pattern's
*concentration* by more than 3×.

Consequences:

1. **The best designs have the worst spatial integral.** The low-QP corner — the
   part of the M2 surface the campaign exists to find — is integrated over
   effectively 4–7 sites, not 16. More events cannot help; this is quadrature,
   not Monte Carlo.
2. **§6d's reassurance does not extend here.** The pitch contrast cancelled
   cleanly across position sets, but both its geometries had similar
   concentration (eff ≈ 9). A contrast between designs with eff 15 and eff 4.3
   is a different situation, and the cancellation argument is correspondingly
   weaker. This applies to the islands-vs-blanket comparison of §6a (eff 7.7 vs
   5.1) — it does not overturn z = 5.6, but the quadrature term there is not
   demonstrably common-mode.
3. **§6 item 8 is now specific.** The pending position-convergence check should
   be run **at the low-QP corner** (blanket t=10, eff 4.3), not at baseline
   where P=16 is comfortable. P=16 vs P=32 at that one design point is the
   cheap, decisive test.

**Recommended M2 amendment:** run the grid at P=16, but promote the low-QP
corner and the reference to P=32 before quoting the surface's minimum. Report
`position_effective_n` alongside every design point so a reader can see where
the quadrature is thin.

> **CORRECTED in §6f — the concentration claim above is largely a low-count
> artifact.** Raw `position_effective_n` is biased downward by counting noise,
> and the pilot's small totals made the bias dominant. Debiased, the spread is
> 15.5 (absorber off) to ~10–12 (absorber on), not 14.9 → 4.3. The recommendation
> to check P=32 at the corner survives; the alarm about it does not.

---

## 6f. Operational fixes, debiased concentration, and the P16/P32 gate (2026-07-29)

### 1. The 191 µeV decision is now applied, not just decided

§6e decided `setBotGapThres = setTopGap` but changed no file. Applied:

* `sensitivity_params.py` — default `180e-6 → 191e-6`, **bounds deliberately
  unchanged**. `build_morris_design()` reads only `param[2]` (bounds) while
  `build_default_design()` reads `param[1]`, so this moves the Stage-4 operating
  point while leaving the stored 2026-07-28 screen exactly reproducible.
* `sensitivity_template_screen.mac` — `180e-6 → 191e-6`, so a direct template
  run is not a different experiment.
* `stage4_make_pilot_design.py` — derives `setBotGapThres` from `setTopGap` for
  every point set, applied *before* per-point overrides so the deliberate
  `gapthres` study still works. Derivation lives in the domain layer, not in a
  default that can be edited away.
* `stage1_run_simulations.py` — `verify_derived_thresholds()` asserts
  `setBotGapThres == setTopGap` in **every generated macro**, in explicit-design
  mode only (the Morris screen swept them independently and is historically
  correct). Escape hatch: `SENSITIVITY_CHECK_DERIVED_THRESHOLDS=0`.
  Confirmed firing: *"Verified setBotGapThres == setTopGap in all 512 generated
  macros."*

### 2. Position completeness now fails closed

`stage2_compute_QPs.py` **rejects** a design point with
`positions_present < positions_expected` unless `--allow-partial-positions` is
passed. Rationale is the §2.3 one: the sites are a fixed quadrature with ~9×
spread in per-site yield, so a missing site biases the result by a
design-dependent amount rather than merely reducing precision — rescaling `n_sim`
is only unbiased if the lost site was average, which it is not.

*(The full shared `completeness.py` of M0c — used by Stage 2b, Stage 3 and
Stage 4 — is still **not** built. This is the position-completeness piece only.
M0c remains partially complete.)*

### 3. `position_effective_n` is biased low — and it changed §6e's conclusion

The raw participation ratio is depressed by counting noise alone. For P sites
carrying N counts at Fano factor F,

\[
n_{\rm eff,raw} \;\approx\; \frac{PN}{N + PF}
\]

so P=16 equivalent sites with N=19 give 8.7 at F=1 and **5.5 at the measured
F≈2.3** — not 16. §6e's headline "4.3 effective sites" was therefore mostly
measurement artifact, not physics.

`stage4_position_diagnostics.py` now reports the raw metric with its noise floor
plus a **replica-debiased** estimate that removes the Monte-Carlo term using the
replica structure directly:

```text
V_total = Var_q( mean_r x_qr )         spread of position means
V_mc    = mean_q( Var_r(x_qr) ) / R    MC contribution to that spread
n_eff   = P / (1 + max(V_total - V_mc, 0) / xbar^2)
```

The last line is exact, since `n_eff = P/(1 + CV²)` for any count vector.

**Debiased results (R=8, the well-conditioned data):**

| design | raw | **debiased** |
|---|---:|---:|
| no-film / zero coverage (R=2 pilot) | 15.5 / 15.2 | **16.0 / 15.5** |
| islands t=1 | 10.2–11.3 | **10.1–11.3** |
| islands t=10 | 10.8 (R=2) | **~10–12** |
| blanket t=1 | 11.4 | **11.6** |
| pitch 250 / 500 | 11.3 / 12.1 | **11.3 / 12.1** |

**Revised finding.** Absorber-off designs integrate over ≈15.5 of 16 effective
sites; absorber-on designs over ≈10–12. That is a real but modest ≈30%
reduction, **not** the 3.5× collapse §6e reported, and it is nearly
design-independent across the absorber-on family — which is what §1.3's
cancellation argument needs. Use the debiased metric, never raw, as a gate.

### 4. Nested P16/P32 at the actual M2 corner — both gates pass

`results/morris_mimir_490102a3-f99b-4efb-9745-d7f033b0d11c` — P=32, R=8,
`TOTAL = 80,640,000` (→ 315,000 events per position-replica, **matching the P16
promotion exposure**, so the concentration diagnostic is not further biased),
fresh seed bank 5, default position seed, 512 sub-runs, 0 failures.

*Nesting verified directly*: `qmc.Sobol(scramble, seed).random(32)` has
`random(16)` as its exact first 16 rows, so P16 is a strict subset of P32.

Corner is the documented box corner **t = 10 µm, f_cov = 0.90, pitch 250 µm**
(→ island 237.17 µm, spacing 12.83 µm), *not* the blanket film, which lies
outside `f_cov ∈ [0.3, 0.9]` and is a qualitatively different no-gap geometry.

Per-position mean yield (exposure matched, so P16 and P32 are comparable):

| design | P16 (nested) | P32 (all) | 2nd-16 | Y32/Y16 − 1 |
|---|---:|---:|---:|---:|
| corner t=10, f=0.90 | 3.91 ± 0.35 | 4.23 ± 0.27 | 4.56 ± 0.27 | **+8.4% ± 11.9%** |
| reference t=1, f=0.64 | 11.59 ± 0.48 | 11.95 ± 0.49 | 12.30 ± 0.57 | **+3.0% ± 6.0%** |

| gate | result |
|---|---|
| \|Y32/Y16 − 1\| < 10% | **PASS** (corner +8.4%, reference +3.0%) |
| corner/reference ordering unchanged | **PASS** — 0.337 ± 0.033 at P16, 0.355 ± 0.027 at P32; corner below reference in both |

Debiased effective sites scale as expected with P (corner 9.4 → 20.9, reference
12.1 → 24.6), i.e. the added sites contribute comparably to the originals rather
than the signal staying pinned to the original 16. *(Values corrected: the
spatial term now uses ddof=0, since n_eff = P/(1 + CV²) is an identity over the
finite position vector and therefore takes the population variance. The MC term
keeps ddof=1, being a sample estimate from R replicas.)*

**Uncertainties are PAIRED** (corrected 2026-07-29). P16 is a subset of P32, so
treating them as independent overstates the error on the ratio. Replica-block
bootstrap — resampling the 8 replica indices jointly, which preserves the shared
position set and the common-random-number structure — gives:

| design | Y32/Y16 − 1 | 1σ | 95% interval | P(\|ratio−1\| > 10%) |
|---|---:|---:|---:|---:|
| corner | +8.4% | **4.1%** | [+1.6%, +17.4%] | **35.1%** |
| reference | +3.0% | **1.5%** | [+0.1%, +6.0%] | 0.0% |

(The unpaired figures were 11.9% and 6.0% — overstated. Note also that the
*mean of per-replica ratios* gives +10.8%, upward-biased by a noisy denominator;
the ratio-of-means with a bootstrap SE is the right estimator.)

**Two honest caveats.** The corner's 95% interval still extends above 10% and a
third of the bootstrap mass exceeds it, so the correct claim is: *P16 is stable
enough for exploration under the pre-registered point-estimate gate; strict ±10%
statistical equivalence has NOT been demonstrated.* And a single nested
refinement demonstrates stability **for this Sobol refinement**; it does not
prove convergence to the continuous spatial integral.

**Decision: M2's exploration grid may run at P=16.** Promote the reference and
the observed minimum/finalists to P=32 before any quantitative minimum claim,
and report debiased `n_eff` with totals for every design point.

---

## 6g. M2 preflight: completeness, provenance, and a portability trap (2026-07-29)

Four corrections applied before M2. Two are recorded in §6f (paired nested
uncertainties; the `ddof` fix). The other two are infrastructure.

### Whole-run preflight, before anything is deleted

Stage 2's wipe of `qps/` and `qp_summary.csv` is destructive and unconditional,
so the fail-closed position check added earlier fired **during** processing --
after the previous outputs were already gone. That is a variant of the exact
failure the README documents (a check that validates an artifact using a
quantity derived from that same artifact), reintroduced by the fix for a
different problem.

`preflight_manifest()` now runs before the wipe and returns **all** problems
rather than the first, so one pass lists everything to fix:

| check | what it prevents |
|---|---|
| duplicate `sample_name` | second entry overwrites the first's npz while both land in the summary |
| duplicate `(design_point, replica)` | a replica counted twice in the mean |
| replica-set holes | a point averaged over `{0, 2}` is indistinguishable from one over `{0, 1}` |
| duplicated hits path in an entry | the same sub-run pooled twice, double-counting its statistics |
| position completeness | fixed quadrature -- a missing site biases by a design-dependent amount |

Unit-tested per case: clean -> 0 problems; missing position -> 1; duplicate name
-> 2; replica hole `[0,2]` -> 1; duplicated path -> 1; and
`--allow-partial-positions` suppresses only the completeness class.

**Extended 2026-07-29 after three malformed manifests were shown to pass.** The
first version checked only self-consistency, so it accepted: (a) an entry listing
31 of 32 positions (`present == listed`, so nothing looked wrong), (b) every
design missing trailing replica 7 (leaving an apparently contiguous 0-6), and
(c) one hits path claimed by two different entries (duplicate detection was
within-entry only). All three now fail, because the preflight reads the
*declared* design from `run_metadata.json` rather than inferring it:

| additional check | source of truth |
|---|---|
| `len(entries) == n_designs x R` | `fidelity.n_manifest_entries` |
| `{replicas} == {0..R-1}` per design | `fidelity.n_replicas` |
| hits files listed per entry `== P` | `fidelity.n_positions` |
| hits paths globally unique | cross-entry `Counter` |

Verified per case: 31-of-32 -> 2 problems; missing r7 -> 3; shared path -> 1;
clean -> 0. Legacy runs have no `run_metadata.json` and fall back to
self-consistency **with a loud warning**, because inferring "expected" from the
observed data is exactly the failure the README documents (Stage 3 once took the
median observed replica count as the expectation, which tracks the damage rather
than detecting it). Confirmed: without declared expectations, cases (a) and (b)
still pass — which is what the warning is for.

*Still outstanding:* this is Stage-2-local. The shared `completeness.py` that
Stage 2b and Stage 3 are also meant to call does not exist, so **M0c remains
partially complete**.

### Machine-readable provenance

A prose execution log drifts from the artifacts it describes. Three files now
close that gap, joined by the design CSV's hash:

* **`results/<run_id>/run_metadata.json`**, written by Stage 1 at generation
  time: protocol version, run id, design source and **design-file sha256**, full
  fidelity block, seeding block, template and lattice-config hashes, and sha256
  of the four pipeline scripts. The seeding block records the **literal list of
  source positions**, not just the seed -- a nested P16/P32 comparison is only
  interpretable if the exact sites are known rather than re-derived.
* **`<design>.labels.json`**, written by the design generator: row -> label,
  thickness, coverage, island/spacing/pitch, and `gapthres_is_derived`.
* **`provenance/morris_regression.log`** now stores the byte-identity result
  itself -- command, environment, code and template hashes, verdict -- rather
  than leaving it reproducible-on-demand but unrecorded. Current run:
  **1728/1728 macros and 54/54 lattice configs identical, PASS.**

### A portability trap found while testing the preflight

The first attempt to test fail-closed behaviour copied a run directory and
removed one hits file; Stage 2 processed 16/16 cleanly anyway. Cause: **the
manifest stores ABSOLUTE hits paths**, so a copied or relocated results
directory silently reads the *original* run's hits files.

Consequences worth knowing before archiving anything:

* a copied run is not self-contained, and validating the copy validates the
  original;
* moving a run to another host or path breaks Stage 2 entirely rather than
  degrading gracefully;
* the preflight cannot detect this, because from its side every file exists.

**Fixed 2026-07-29, backward-compatibly.** New manifests store paths relative to
`results/<run_id>` (e.g. `hits/M2_0_r0_p0_hitsfile.txt`); Stage 2 resolves them
against the directory it was actually pointed at, and absolute paths from the six
legacy runs still work unchanged. The macro keeps the absolute path, since Geant4
runs from a different cwd. `run_metadata.json` records
`manifest.hits_paths_relative` so the format is self-describing.

Verified by moving a generated run directory and confirming its manifest resolves
under the new location rather than the original. New M2 results are therefore
movable; the six stored runs remain absolute and must not be relocated.

---

## 6h. M2 — the thickness x coverage response surface (2026-07-29)

**Executed.** Exploration grid `results/morris_mimir_8bd59ac2-fce2-496f-8eff-6e8e1c9fcacd`
(28 points, P=16, R=4, TOTAL = 8,064,000, seed bank 6, **1792 sub-runs, 0
failures, 0 zero-byte**) and promotion
`results/morris_mimir_9d1248ef-329a-4577-ac50-f6339e14b024` (3 points, P=16,
R=8, TOTAL = 40,320,000, **fresh seed bank 7**, 384 sub-runs, 0 failures).
Grid: t = {1, 2, 5, 10} um x f_cov = {0.30, 0.45, 0.60, 0.64, 0.75, 0.85, 0.90},
pitch 250 um, Cu, top stack fixed. Objective `QP_yield_per_event`.

Preflight validated the manifest against the *declared* design
(P=16, R=4, 112 entries) and passed. Full output:
`provenance/m2_grid_analysis.txt`, `provenance/m2_promotion.txt`.

### The headline result

| | yield/event | vs reference |
|---|---:|---:|
| reference t=1 um, f=0.64 | 3.62e-05 | — |
| box minimum t=10 um, f=0.90 | **1.23e-05** | **-66.1% [-68.9%, -62.5%]** |

Resolved, on a fresh seed bank, and independently consistent with the §6f P32
corner run (-64.6% at P32, -66.3% at P16) — three runs, two fidelities, three
seed banks agreeing to within ~2%.

### Coverage dominates; thickness is real but largely unresolved at R=4

| axis | adjacent steps | resolved | resolved increases |
|---|---:|---:|---:|
| coverage (at fixed t) | 24 | **15** | **0** |
| thickness (at fixed f) | 21 | 4 | **0** |

**Zero resolved increases on either axis** — no non-monotonicity was detected, so
rule 5 had nothing to flag. Coverage carries the surface: the strongest single
steps are f 0.30 -> 0.45 (about -36% to -40% at every thickness, all resolved).

The thickness axis being mostly unresolved is the *expected* consequence of the
exploration fidelity, not evidence against it: §6a resolved t=1 vs t=10 at
z = 7.5 with R=8 and 10x the events. **The "plateau onset" column in
`provenance/m2_grid_analysis.txt` is therefore fidelity-limited and must not be
read as physics** — it reports the first adjacent step this run cannot resolve,
which at six of seven coverages is already 1 -> 2 um.

### The promotion reversed the grid's apparent minimum

This is the single most important methodological outcome, and it is exactly what
rules 4 and 5 were pre-registered to catch.

| candidate | grid (R=4, bank 6) | promotion (R=8, bank 7) |
|---|---:|---:|
| t=10, f=0.85 | 1.166e-05 *(grid minimum)* | 1.558e-05 |
| t=10, f=0.90 | 1.290e-05 | **1.225e-05** |
| reference | 3.671e-05 | 3.616e-05 |

In the grid, f=0.85 appeared to beat f=0.90 by 10.6% — but the paired contrast
was **unresolved** ([-13.6%, +36.1%]), so under rule 4 the two were not called
ordered. Promotion inverts it: **f=0.90 beats f=0.85 by 21.3%
[-28.4%, -10.7%], resolved.**

The f=0.85 cell moved +34% between banks while the reference moved -1.5%. That
cell had the grid's joint worst conditioning — 14.5% relative SEM, only 24 QPs,
raw `n_eff` 6.2 — so it is precisely where a noise excursion was likeliest. Had
rule 4 not been pre-registered, this run would have reported a spurious interior
minimum at f=0.85 and a false non-monotonicity in coverage.

**Consequence:** the promoted minimum *is* the anticipated box corner, so per the
launch sequence's step 5 the existing §6f P32 run already supplies its
high-fidelity **spatial** confirmation. No further promotion is required.

### Fit quality (rule 3)

`log(yield) ~ 1 + log(t) + f + f^2`:
`intercept -8.578, log(t) -0.162, f -2.950, f^2 +0.524`.
In-sample RMS residual **9.8%**, max |residual| **27.9%**, leave-one-out RMS
**11.4%**. The trend reproduces the bulk of the surface but misses individual
cells by up to 28%, which is why every raw cell mean and SEM is reported
alongside it (rule 1) and why no ordering is claimed without a direct paired
contrast (rule 4).

### What this does and does not establish

* **Does:** within the sampled box, the lowest modeled QP yield is at
  t = 10 um, f_cov = 0.90, at -66% relative to the as-designed reference,
  resolved and reproduced across banks and fidelities.
* **Does not:** identify a device optimum. Coverage is monotone-beneficial in
  this model *by construction* because the model contains no microwave-loss,
  stress or fabrication term (§3.3a), so the box edge is where the QP-only
  objective must land. The result is the **minimum modeled QP yield within the
  sampled box** and the input to a constrained or Pareto study, not a
  recommendation.
* **Diagnostic only (rule 6):** raw `position_effective_n` spans 4.9-12.0 across
  the grid. At R=4 with low corner counts this is biased downward and indicates
  where the spatial quadrature is thin, not a physical observable (§6f).
* Absolute yields still carry the P=16 quadrature caveat of §1.3/§6d, and remain
  conditional on the unsourced Cu constants of §6d (M0b incomplete).

---

## 6i. Preliminary Pareto front, and the EM blocker (2026-07-29)

### The EM surface cannot be produced here

Checked rather than assumed: no CST, HFSS, ANSYS, COMSOL, Sonnet, openEMS or
Meep binary on this host; no `skrf`, `pyaedt`, `femwell` or `gdsfactory` in any
environment; no loss or S-parameter data anywhere in the project. **The
microwave-loss surface is an external deliverable.** Inventing a
loss-versus-coverage curve would be the fabricated-constant failure this plan
warns against twice (§3.5, §8.3), and it would determine the answer, since the
choice among front designs is *entirely* set by that curve.

`stage4_pareto.py --em-loss <file>` refuses to run rather than combine against a
guessed schema. What is needed:

```text
coverage,pitch_um,loss_metric,loss_value,loss_sigma,source
0.30,250,Qi_internal,1.85e6,0.09e6,HFSS-2026-08-03
```

plus three things not inferable here: whether the metric improves or degrades
with increasing value, the **acceptable region** that makes the problem
constrained, and whether pitch is genuinely free or fabrication-bounded.
`loss_sigma` is required — the robust front needs it.

### The front itself needs no EM numbers — only monotonicity

If loss is monotone non-decreasing in coverage at fixed pitch, then for designs
at equal pitch

```text
B dominates A  <=>  coverage_B <= coverage_A  and  QP_B <= QP_A
```

because a monotone loss function preserves the coverage ordering. **The
Pareto-optimal set is therefore computable from M2 alone.** The EM curve is
needed to pick a point *on* the front, not to find it.

Assumptions, stated: **A1** loss monotone in coverage (the premise of the whole
exercise); **A2** pitch fixed at 250 µm — note pitch neutrality was measured only
at t=1 µm, f=0.64 (§6d), *not* at the high-coverage corner where the front lives;
**A3** thickness carries no EM cost, so it is reported as a separate cost axis
rather than optimized away.

Domination requires a **resolved** paired contrast (rule 4). Contrasts between a
grid cell and a promoted cell are computed **unpaired**, since they sit on
different seed banks and pairing them would be fiction.

### Result: 16 of 28 designs on the robust front, collapsing to 7

Promoted measurements are substituted where they exist — without this the front
would publish the known-bad f=0.85 grid value (a +34% noise excursion, §6h):

| coverage | representative | t (µm) | yield/event | same-coverage designs tied with it |
|---:|---|---:|---:|---|
| 0.30 | `m2_t5_cov0.30` | 5 | 6.20e-05 | t=10 |
| 0.45 | `m2_t5_cov0.45` | 5 | 3.92e-05 | t=10 |
| 0.60 | `m2_t1_cov0.60` | 1 | 3.67e-05 | t=2, 5, 10 |
| 0.64 | `m2_t5_cov0.64` | 5 | 2.38e-05 | t=10 |
| 0.75 | `m2_t2_cov0.75` | 2 | 2.60e-05 | t=5, 10 |
| 0.85 | `m2_t2_cov0.85` | 2 | 2.01e-05 | t=10 |
| 0.90 | `m2_t10_cov0.90` | 10 | 1.23e-05 | — |

Collapse rule: among same-coverage designs whose QP differences are
**unresolved**, keep the thinnest film. That is a tie-break on the A3 cost axis,
not an EM assumption — it never discards a resolvedly better design. The wide
tie sets (coverage 0.60 ties across all four thicknesses) are the R=4 exploration
fidelity showing through, consistent with §6h.

**The front spans the full coverage axis.** That is the substantive finding: no
coverage level is eliminated by the QP data, so the decision is entirely an EM
and fabrication decision. Narrowing to 3–5 validation candidates cannot be done
here.

Full output: `provenance/pareto_preliminary.txt`.

### Pre-registered validation protocol for the shortlist

To run once the EM curve selects 3–5 candidates, agreed before any of it is
executed so the acceptance criteria cannot drift:

1. **Re-run the shortlist in G4CMP at P=32 on a fresh seed bank.** P=32 because
   the front sits at high coverage where the spatial quadrature is thinnest
   (raw `n_eff` 4.9–6.2, §6h); a fresh bank because the shortlist was selected
   partly on QP values, and confirming on the selecting streams is circular.
2. **Include the EM-selected pitch.** If it differs materially from 250 µm the
   §6d neutrality result does **not** transfer: it was measured at t=1 µm,
   f=0.64 only, and pitch re-phases every injection site against the island
   pattern (§6d), so any new pitch needs its own two-position-seed check at the
   shortlist geometry.
3. **Propagate uncertainty in the unresolved M0b constants** — `setBotAbs`
   0.736, `setBotVSound` 2.608 km/s, `setBotPhLifetime` 5.1 ns, `setBotQPLim` 2
   — as a robustness axis (§3.6), not as free parameters. Note thickness and
   `v_s·τ` are exactly degenerate (§1.6), so lifetime/sound-speed uncertainty
   maps directly onto an effective-thickness uncertainty.
4. **Test realistic source scenarios**, not only the monochromatic ~1 meV probe.
   Gun energy is already a design column, so 2–3 scenarios cost one extra grid
   axis. This is the larger validity risk (§6 item 6) and it interacts with the
   Yelton inversion (§6h).
5. **Recompute the robust front with both QP and EM uncertainty**, using
   `loss_sigma` and replica-block resampling, and report the front as a band
   rather than a curve.

**Sequence:** external EM/fabrication surface → preliminary front (done, above)
→ EM-driven shortlist → targeted high-fidelity G4CMP + EM validation → robust
recommendation. No further broad QP campaign is required before the EM work.

---

## 6j. Front statistics corrected; thickness settled; the decision blocker (2026-07-29)

### Two bootstrap defects, both of which corrupted the published front

1. **Index sets were sized to the grid's R and reused for promoted cells.**
   `boot_idx` had shape `(BOOT, 4)` with values in `[0,4)`; promoted cells hold
   R=8. A promoted-vs-promoted contrast therefore resampled **only replicas 0-3
   of 8** — and that is precisely the t=10 f=0.85 vs f=0.90 comparison at the top
   of the front.
2. **The unpaired branch drew from a shared RNG inside the comparison loop**, so
   the front depended on the order comparisons happened to be made in and was not
   reproducible.

Replaced by `build_bootstrap()`: one index matrix per `(bank, R)` group, drawn
once from a seeded RNG. Same-group cells share indices, preserving the
common-random-number pairing the seed banks exist for; different-group cells get
independent indices, which is honest because they share no streams.

### Front-membership probability instead of binary inclusion

Binary inclusion at a 95% cutoff answers "can this be excluded?", which is not
the decision question. `P(front)` — how often a design is Pareto-optimal across
bootstrap draws — separates strong members from ones that merely survive:

| coverage | best member | P(front) | marginal members at same coverage |
|---:|---|---:|---|
| 0.30 | `t10_cov0.30` | **96.4%** | t5 (3.6%) |
| 0.45 | `t10_cov0.45` | 69.1% | t5 (33.2%) |
| 0.60 | `t2_cov0.60` | 48.7% | t5 (37.0%), t10 (9.3%), t1 (4.9%) |
| 0.64 | `t5_cov0.64` | 74.6% | t10 (8.2%) |
| 0.75 | `t10_cov0.75` | 55.2% | t5 (12.2%), t2 (5.4%) |
| 0.85 | `t10_cov0.85` | **91.4%** | t2 (5.0%) |
| 0.90 | `t10_cov0.90` | **96.5%** | — |

Five of the sixteen 2-objective front members sit below 9% and are on the front
only because they cannot be *resolvedly* excluded. That is a far more useful
statement than sixteen equal-looking rows.

### Thickness is a cost, but NOT a formal objective — the 3-objective front is degenerate

Making thickness a third Pareto objective puts **all 28 of 28 designs on the
front**, and the degeneracy is structural rather than a fidelity artifact: QP
decreases monotonically with thickness, so a thinner design can never dominate
on QP and a thicker one can never dominate on cost. Every combination is
non-dominated by construction, `P(front)` saturates, and the front loses all
discriminating power.

**Decision: thickness enters as a CONSTRAINT (`thickness <= X`), not an
objective** — supplied externally, exactly like a coverage cap. The operative
front is the 2-objective one, read subject to a thickness cap. This also
supersedes the earlier "keep the thinnest of a tied set" tie-break, which was an
implicit and less honest version of the same thing.

### t = 5 vs 10 µm at f = 0.90 — the consequential thickness trade-off, resolved

`results/morris_mimir_ef6dec99-8bba-4082-b504-9bea6f558fa9` — P=32, R=8,
TOTAL = 80,640,000, **fresh seed bank 8**, 512 sub-runs, 0 failures. P=32 because
the front's high-coverage end is where the spatial quadrature is thinnest.

| design | yield/event | rel SEM |
|---|---:|---:|
| t=5 µm, f=0.90 | 1.4211e-05 | 5.2% |
| t=10 µm, f=0.90 | **1.2376e-05** | 3.1% |

**t=10 beats t=5 by 12.9% [3.9%, 21.8%] — RESOLVED.** So 10 µm is not merely the
cheaper-to-justify choice at statistical parity; it is genuinely better, and a
thickness cap below 10 µm costs a real ~13% in QP yield rather than nothing.
(Cross-check: this run's t=10 value 1.238e-05 on bank 8 at P=32 agrees with the
bank-7 P=16 promotion value 1.225e-05 to 1%.)

### The blocker, stated plainly

**No optimizer, surrogate or LLM can infer a missing physical objective.** The
front spans the entire coverage range because the QP data eliminates no coverage
level; the choice among its members is set entirely by information this pipeline
does not contain. A full loss surface is ideal, but **any one** of the following
collapses the pool to a decision:

1. **maximum acceptable coverage** — enough on its own for a conditional
   recommendation *today*, since the front is monotone in coverage: given a cap,
   the answer is the front member at that cap;
2. allowed **pitch range** — fixes assumption A2, and if it excludes 250 µm the
   pitch-neutrality check (§6d) must be repeated at the front geometry, since it
   was measured only at t=1 µm, f=0.64;
3. **thickness / stress / Cu-volume limit** — now quantified: capping below
   10 µm at f=0.90 costs 12.9% [3.9%, 21.8%];
4. **max added 1/Q or minimum Q_i** — the constrained form proper;
5. a device-team list of **fabrication-admissible geometries** — intersect with
   the front and report the best admissible member.

Until one arrives, §6i/§6j is a valid conditional trade-off analysis and **cannot
produce a device recommendation**. Stopping here is the correct outcome, not a
gap to be filled by modelling.

Full output: `provenance/pareto_preliminary.txt`.

---

## 7. Implementation sequence

Revision 1 reached its first real result at Milestone 3–4, after roughly 1,500
lines of infrastructure written against an unvalidated cost model. Revision 4
keeps the early qualification runs, but corrects one implementation assumption:
the current Stage-1 path generates Morris rows or identical noise-floor rows; it
does not accept an arbitrary list such as no-film/1/2/5/10 µm. A very small
explicit-design input path is therefore required before the pilot can be run
cleanly and reproducibly.

| | Work | Needs new code? | Why it is here |
|---|---|---|---|
| **M0p** | Restore memory guards; add the minimal fixed-design-file path and seed policy to Stage 1; run the byte-identical Morris regression | Small | Required to represent M0a without ad hoc template edits |
| **M0a** | Milestone-0 items 1 + 2: thickness/control scan, blanket-vs-islands comparison, and two-channel mechanism check | No additional controller code | Can invalidate downstream interpretation; start at pilot fidelity |
| **M0b** | Audit the **Cu** preset's routing and constants; quarantine/fix the defective Al preset | Config + literature validation | Blocks every Stage-4 number (§0: Ti audit struck) |
| **M0c** | Per-position metrics in Stage 2 + shared `completeness.py` | Small | Unblocks tail-risk objectives and the §2.3 invariant |
| **M0d** | Confirm objective and run the small position-set convergence check | Small run | Decides what the answer means and whether P16 ranks reliably |
| **M1** | `stage4/space.py`, minimal store/controller, and proposal-file contract | Yes | Resumability and backend switching without duplicating physics writing |
| **M2** | Cu factorial grid, optional 32–64 Sobol fill, pilot total | No new backend | The first response-surface campaign |
| **M2b** | Promote only reference/endpoints/finalists to legal high-stat totals with fresh validation seeds | Small scheduling addition | Turns the exploratory map into defensible comparisons |
| ~~M3~~ | ~~Material/preset axis~~ | — | **Struck (§0)** — recorded as the strongest deferred finding, not Stage-4 work |
| **M4** | BoTorch `qLogNEI` | Only if M3 is rugged | Probably never reached |
| **M5** | Adaptive replication on increasing legal totals | Moderate | Must schedule/merge new sub-runs, preserve seed provenance, and avoid double counting |
| **M6** | Robust and multi-objective | Later | Needs a fabrication cost scale |
| **M7** | Optional agent | Last | After the CLI is reliable |

**Do M0p, then M0a, before writing the database/controller layer.** M0a is the
only early run that can show the intended Cu mechanism is absent, but the
current code needs the small M0p input path to express that run reproducibly.

Detail retained for the milestones that do involve code:

**M0c / completeness.** Shared validator with the fail-closed rules: require the
manifest; compare `Counter` rather than `frozenset` so duplicate rows cannot
pass; reject manifest index holes; require
`positions_present == positions_expected`; check the §2.3 derivation chain.
Stage 2 currently pools positions *before* computing electrode quantities, so
per-position metrics mean looping `hits_files` inside `process_manifest_entry()`
and storing a per-position array alongside the pooled one — additive, and it
does not change any existing column.

**M0p / explicit-design mode.** `--design-file`, `--run-kind optimization`,
`--experiment-id`. With no `--design-file`, the Morris path must stay
byte-for-byte identical — now testable exactly, per §5.5. Generalize
`generate_runfiles()` to take a sample name and seed group instead of building
`Morris_<row>` internally. Extend `verify_generated_beam_on()`'s pattern to the
full parameter vector. Honour the §5.5 constraints: subprocess invocation, full
positional `samp` rows, third seed mode, `(run_id, sample_name)` keying.

**M2 / the first campaign.** Requires the §3.3a coverage decision first —
constrained range or explicit penalty — since an unconstrained coverage axis has
a trivial optimum at the blanket film. Use the structured Cu grid of §4.1 over
backside thickness and coverage at fixed pitch 250 µm, top stack fixed, with
`TOTAL = 4,032,000` as exploration fidelity. Fit and report the response with
heteroscedastic uncertainty; do not label every pilot point “5% resolved.”
Promote the reference, endpoints, and candidate plateau/boundary points under
M2b. Expect both axes to be monotone and saturating, but treat that as a
testable mechanism prediction. **If either axis is non-monotone, that is the
interesting result** and may justify a GP or a denser residual design.

---

## 8. Agentic AI with an open model

### 8.1 Hardware reality

8 × RTX A6000 (384 GB VRAM total), 192 cores, 503 GB RAM. This can serve a
strong open model locally, but usable context length and throughput must include
KV cache, quantization, tensor/pipeline partitioning, and the machine's actual
GPU interconnect topology.

- **Qwen3-Coder-30B-A3B is a reasonable candidate, not a physics-plan
  dependency.** Its official model card reports 30.5B total and 3.3B activated
  parameters. Benchmark it against at least one smaller model on this project's
  real tool schemas and logs before selecting it.
- **Correct the interconnect claim.** NVIDIA specifies that RTX A6000 supports
  NVLink between two cards at 112.5 GB/s bidirectional. That does not create an
  eight-GPU all-to-all fabric; the installed bridge topology and PCIe layout
  still determine scaling. Choose tensor-parallel degree and model architecture
  from a measured serving benchmark, not from the blanket statement that
  A6000s lack NVLink.
- Select on **tool-calling reliability and schema validity**, not coding
  leaderboards. Serve through vLLM with constrained structured output.

### 8.2 Do not build an agent framework

The tool list the previous plan specified is, literally, a CLI with JSON output.
Build `python -m stage4.cli <verb> --json` and any agent — a local Qwen over
vLLM, or Claude Code — drives it with zero bespoke agent code. Milestone 7
collapses to "good JSON output plus a tool schema file."

### 8.3 Where an LLM actually pays off here

- **Log triage and failure classification.** 221,184 sub-runs and four
  documented failure modes (exit-0-with-no-hits, exit-time SIGSEGV,
  `vtrans > vsound` zero-byte, memory-guard kills) that are tedious to regex and
  easy to classify.
- Drafting campaign write-ups and comparing observations against Stage-3
  physical expectations.
- Monitoring and retrying pre-approved transient failures.

**Not** orchestration — the controller state machine is deterministic and
belongs in cron — and **not** anything in the numerical path: no generating
material constants, no editing presets, no changing objectives, bounds, or
fidelity, no converting a crash into an objective value, no launching a
high-cost batch without approval.

### 8.4 A reproducibility hazard neither earlier document raised

**An LLM in the loop is a provenance liability for a physics result.** If the
agent ever influences *which* candidates run, its model ID, quantization,
sampling parameters, and full transcript become part of the experiment record.
For the first campaign, keep it strictly read-only plus report drafting.

---

## 9. Tests and acceptance

**Unit.** Coverage/pitch conversion and boundaries · preset expansion sets every
coupled property · incomplete presets fail · nonfinite, out-of-bounds, invalid
velocity, unstable crystal, unsafe `QPLim` fail before macro generation ·
canonical hashes stable under dict ordering · paired and independent seed
policies each match their declared formulas and are independent of batch row
order · **a `total_events` not divisible by `P × R` is rejected at config load,
and `legal_total()` rounds upward correctly** · non-positive memory budgets and
per-sample budget above total budget fail at import/config validation · **the
§2.3 derivation chain holds** ·
duplicate manifest/summary identities fail · a manifest index hole fails · a
missing position fails · a missing replica fails · SQLite uniqueness prevents
duplicate launches · every backend satisfies the same proposal contract.

**Integration.** Generate-only Stage-4 baseline round-trips every macro value ·
two tiny candidates through Stages 1 and 2 · interrupt and resume between every
controller state · retry a deliberately missing sub-run with the same seed ·
switch Sobol → BoTorch on the same database · a synthetic noisy objective with a
known optimum through every backend · technical failures never appear as QP
values · **the existing Stage-3 result is unchanged after the shared-validator
refactor** (18 of 47, threshold 0.1734, ρ = 0.829).

**Ready for a real adaptive campaign when:** a candidate can be proposed,
expanded, validated, simulated, collected, and recorded with no
algorithm-specific physics code; restart cannot duplicate a simulation; missing
replicas and positions fail closed; the position-set convergence check has
defined an acceptable exploration fidelity; **the M2 response surface has shown
that an adaptive method has a real job to do** (for example a non-monotone,
cost-constrained, robust, or higher-dimensional landscape); the backend rebuilds
from the shared store; changing the backend is a configuration change; and final
validation uses fresh seeds at an explicitly versioned target fidelity on a
legal `(total_events, P, R)` triple.

**Additionally, before M0p is considered done:** the Morris path regenerates
byte-identically against the 221,184 stored macros (§5.5).

---

## 10. The one-paragraph version

The Stage-4 direction is feasible and scientifically meaningful after the
revision-4 prerequisites. The absorber model still predicts a Cu backside film
that absorbs incident phonons but leaks lower-energy, still pair-breaking
secondaries; this keeps thickness live to about 10 µm and makes coupled material
properties such as \(v_s\tau\), thresholds, and cutoffs important. Start with a
minimal explicit-design path, restore Stage-1 memory guards, then run the
no-film/thickness/blanket controls and a small source-position convergence
check. Map Cu thickness × coverage at about 4.032e6 events per exploration point
with an interpretable grid and optional Sobol fill; promote only reference,
endpoint, and finalist designs to the much larger legal totals needed for 5%
confirmation. This study qualifies the model and maps the Cu backside stack. Per §0 it is
**not** material selection, and Stage 4 does not become material selection
later — that is deferred to a future campaign, not a later milestone of this
one. Report it as *Cu backside-stack optimization* throughout.

---

## 11. References

- BoTorch overview: <https://botorch.org/docs/overview>
- BoTorch constrained batch `qLogNEI`:
  <https://botorch.org/docs/next/tutorials/closed_loop_botorch_only>
- BoTorch multi-objective and `qLogNEHVI`:
  <https://botorch.org/docs/v0.16.0/multi_objective>
- CMA-ES tutorial (Hansen): <https://arxiv.org/abs/1604.00772>
- vLLM structured tool calling:
  <https://docs.vllm.ai/en/stable/features/tool_calling/>
- Qwen3-Coder: <https://qwenlm.github.io/blog/qwen3-coder/>
- Official Qwen3-Coder-30B-A3B-Instruct model card:
  <https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct>
- NVIDIA RTX A6000 datasheet (pairwise NVLink support and bandwidth):
  <https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/proviz-print-nvidia-rtx-a6000-datasheet-us-nvidia-1454980-r9-web%20%281%29.pdf>
- E. Yelton et al., *Modeling phonon-mediated quasiparticle poisoning in
  superconducting qubit arrays*, Phys. Rev. B **110**, 024519 (2024),
  <https://arxiv.org/abs/2402.15471> — source of the backside-film mitigation
  benchmark used in §6 item 1, and of the `T1_QP` footprint metric in §3.2.

Internal: `README.md` (phonon event accounting; screening protocol),
`RESULTS_stage1_to_stage3.md`, `AUDIT_RESULTS_stage1_to_stage3.md`,
`G4CMP_crash_and_memory_analysis.md`. (The earlier
`Stage3_material_QP_optimization_recommendations.md` has been merged into this
document and removed; its algorithm evaluation predated the cost measurements
in §1 and its physics content is now in §3.4–§3.7.)
