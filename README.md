# QPU Material Sensitivity Analysis

Morris (elementary-effects) sensitivity analysis of superconducting-qubit
substrate and film parameters, driving Geant4/G4CMP phonon simulations of a
BNL detector geometry and converting the resulting phonon hits into
quasiparticle counts and a qubit decoherence-rate contribution.

The sweep covers 50 parameter groups / 53 expanded variables across detector
geometry, film properties, G4CMP transport settings, silicon lattice
constants, and the quasiparticle ODE. With 128 trajectories at 4 levels the
full design is `128 * (53 + 1) = 6912` design points.

## How to read `T=128`, replicas, and source positions

The subtitle of `figures/stage3_screen_results.png` describes three different
levels of the calculation:

```text
128 Morris trajectories
└── 54 parameter configurations per trajectory
    └── 2 Monte Carlo replicas per configuration
        └── 16 source positions per replica
            └── 125,000 primary events per Geant4 process
                (= 4,000,000 total per design point / (16 x 2))
```

A **Morris trajectory is a path through parameter space**, not a Geant4
particle or phonon trajectory.

### Phonon event accounting

Each level multiplies the one below it, so the number quoted depends entirely
on which level is meant. All four are used in this repository:

**One number is configured: the TOTAL phonons per design point.** Everything
else is derived from it, so no count can silently mean something different
because an unrelated setting changed.

| Level | Phonon events | Where it appears |
|---|---:|---|
| **TOTAL per design point — the configured knob** | **4,000,000** | `SENSITIVITY_TOTAL_EVENTS`; `/run/beamOn` in `sensitivity_template_screen.mac`; `n_sim_total_design_point` in the manifest |
| ÷ 2 replicas = one replica | 2,000,000 | `n_sim` in `qp_manifest.jsonl` / `qp_summary.csv` |
| ÷ 16 positions = one sub-run (**derived** `/run/beamOn`) | 125,000 | every generated `Morris_<i>_r<r>_p<p>.mac` |
| × 6,912 design points = the whole screen | 2.76 × 10¹⁰ | 221,184 sub-runs |

Stage 1 computes `events_per_sub_run = TOTAL / (N_POSITIONS × N_REPLICAS)` and
**refuses to run if that is not an exact integer** — rounding would give design
points unequal statistics. With one position and one replica the total and the
per-run count coincide, so a legacy `sensitivity_template_beamOn*.mac` behaves
exactly as before.

The primary is a single `phonon_Caustic` phonon per event, sampled from
[0.6, 1.5] meV, so "events" and "primary phonons" are the same count here.

**Why it cannot drift.** Every human-facing number is the total, so the
template and the environment variable cannot disagree about what they mean —
running the template with no env var and running it with
`SENSITIVITY_TOTAL_EVENTS=4000000` give byte-identical macros. On top of that:

1. **Stage 1 always rewrites `/run/beamOn`**, never inherits it silently, and
   prints the total with its provenance at startup:
   `TOTAL phonons per design point: 4,000,000 (from SENSITIVITY_TOTAL_EVENTS)`.
2. **A total that does not split evenly is refused**, not rounded.
3. **Every generated macro is verified** to carry the derived count before any
   simulation starts.
4. **`SENSITIVITY_EVENTS_PER_POSITION` is removed** and raises an error naming
   its replacement, so an old command line cannot quietly run the old
   semantics.

A wrong-but-valid event count is undetectable downstream — the run completes
normally and only the physics is wrong — so every check is at generation time.
(A non-numeric placeholder would be worse than a stale number: Geant4 rejects
`/run/beamOn`, but the hits file is already created at `/run/initialize`, so the
run exits 0 with a header-only file indistinguishable from the 39,430 sub-runs
that legitimately produced no hits.)

The executed macros are the 221,184 generated files under
`output/<run_id>/macros/` — gitignored, but regenerating byte-identically from
`SENSITIVITY_MORRIS_SEED=20260727` plus the env block below.

### Definitions

| Symbol | Meaning | This screen |
|---|---|---:|
| \(K\) | number of expanded Morris variables | 53 |
| \(T\) | number of Morris trajectories | 128 |
| \(p\) | Morris grid levels | 4 |
| \(L=K+1\) | design points per trajectory | 54 |
| \(R\) | Monte Carlo replicas per design point | 2 |
| \(P\) | source positions per replica | 16 |
| \(n_{\rm evt}\) | primary events per source-position process | 125,000 |

### What one Morris trajectory does

Trajectory \(t\) is an ordered one-factor-at-a-time path,

$$
\boldsymbol{\theta}^{(t,0)}
\rightarrow
\boldsymbol{\theta}^{(t,1)}
\rightarrow\cdots\rightarrow
\boldsymbol{\theta}^{(t,K)},
$$

where consecutive points differ in exactly one of the \(K=53\) coordinates.
Every parameter changes once, so one trajectory supplies one elementary effect
for every parameter. Therefore,

$$
N_{\rm design}=T(K+1)=128(53+1)=6912
$$

parameter configurations, and

$$
N_{\rm EE}=TK=128\times53=6784
$$

elementary effects in total: 128 effects for each parameter. In code, stage 1
generates the design with

```python
morris_samp.sample(setup, 128, num_levels=4, seed=MORRIS_SEED)
```

and stage 3 divides `MorrisSequence.csv` into consecutive blocks of
\(K+1=54\) rows.

With \(p=4\) levels, the normalized Morris grid is based on
\(\{0,1/3,2/3,1\}\), and the conventional step magnitude is

$$
\Delta=\frac{p}{2(p-1)}=\frac{2}{3}.
$$

The analyzer nevertheless reads the actual step from the stored design rather
than hardcoding \(2/3\).

### What 16 source positions mean

Let \(N_{{\rm QP},irq}\) be the QPs generated at design point \(i\), replica
\(r\), and source position \(q\). Stage 2 pools the 16 position hits files
within one replica:

$$
Y_{ir}
=
\frac{\sum_{q=1}^{P}N_{{\rm QP},irq}}
{P\,n_{\rm evt}}.
$$

Thus one replica represents

$$
16\times125{,}000=2{,}000{,}000
$$

primary events. The same scrambled-Sobol position set is reused for every
material configuration. This makes source position a fixed spatial scenario
or blocking variable, preventing a different set of injection sites from
being mistaken for a parameter effect. The 16 positions are not additional
Morris points and do not produce 16 separate bars in the figure.

### What two replicas mean

The two replicas have the same material configuration and the same 16 source
locations but different replica seed banks. Stage 3 assigns their mean to
design point \(i\):

$$
Y_i=\frac{1}{R}\sum_{r=1}^{R}Y_{ir}
=\frac{Y_{i1}+Y_{i2}}{2}.
$$

Consequently, the response used by Morris represents

$$
R P n_{\rm evt}
=2\times16\times125{,}000
=4{,}000{,}000
$$

events per design point. Their difference also measures Monte Carlo
variability, although two replicas alone provide a noisy point-specific
variance estimate.

Every design point therefore requires \(R P=32\) separate Geant4 processes,
and the full screen contains

$$
6912\times2\times16=221{,}184
$$

sub-runs. One elementary effect compares two endpoints, each based on 32
sub-runs, although endpoints are shared by neighboring trajectory steps and
are not rerun separately for each parameter.

### Elementary effect, \(\mu^\ast\), and \(\sigma\)

If parameter \(j\) changes between two consecutive points in trajectory \(t\),
the dimensionless elementary effect used here is

$$
{\rm EE}_j^{(t)}
=
\frac{
(Y_{t,+}-Y_{t,-})/\overline{Y}
}{
(\theta_{j,t,+}-\theta_{j,t,-})/(b_j-a_j)
},
$$

where \([a_j,b_j]\) is the sampled range and \(\overline{Y}\) is the mean QP
yield over all design points. Stage 3 summarizes the 128 effects for parameter
\(j\) as

$$
\mu_j^\ast
=
\frac{1}{T}\sum_{t=1}^{T}\left|{\rm EE}_j^{(t)}\right|,
$$

and

$$
\sigma_j
=
\sqrt{
\frac{1}{T-1}
\sum_{t=1}^{T}
\left({\rm EE}_j^{(t)}-\mu_j\right)^2
}.
$$

- Large \(\mu^\ast\): large overall influence on QP yield over the selected
  range.
- Small \(\mu^\ast\): weak effect or an effect hidden by simulation noise.
- Large \(\sigma\): nonlinear, interaction-dependent, or noisy effect.
- Small \(\sigma\): comparatively consistent effect across the design box.

Because the input step is normalized by the full sampled range,
\(\mu^\ast\) is a normalized full-range slope estimate. It is not necessarily
the literal endpoint-to-endpoint change for a nonlinear model, and its value
depends on the chosen bounds.

In the figure:

- panel A shows \(\mu^\ast\), its 5th--95th percentile bootstrap interval, and
  the dummy-calibrated noise threshold;
- panel B is a **separate** run of 200 identical configurations, each with
  \(16\times250{,}000=4\times10^6\) events; it is not a histogram of the two
  full-screen replicas;
- panel C plots \(\mu^\ast\) horizontally and \(\sigma\) vertically.

The existing figure was generated from the completed screen that predated the
explicit-seed fix documented below. Its two replica processes were intended to
be independent, but the later audit found some repeated `clock()` streams.
Current stage-1 code instead keys explicit seeds by
`(trajectory, replica, position)`: endpoints within a trajectory share a
stream for variance-reducing common random numbers, while replicas and
trajectories receive different streams.

The same derivation is available as a standalone LaTeX document:
[`Morris_trajectory_replica_position_math.tex`](Morris_trajectory_replica_position_math.tex).

## Pipeline

Running the simulation and the quasiparticle calculation together made one
slow job out of two independently runnable halves, so they are split:

| Stage | Script | Needs Geant4/G4CMP? | Produces |
|---|---|---|---|
| 1 — simulation | `stage1_run_simulations.py` | yes | per-sub-run macros, lattice configs, hits files, and `qp_manifest.jsonl` |
| 2 — quasiparticles | `stage2_compute_QPs.py` | no (numpy/pandas only) | `qps/*.npz`, `qp_summary.csv` |
| 2b — sensitivity correlations | `stage2_compute_QPs_sensitivity_analysis.py` | no (numpy/pandas/matplotlib only) | `sensitivity_correlations.{csv,png}`, `sensitivity_chi2_correlations.{csv,png}`, `sensitivity_corr_matrix*.png` |
| 3 — screening analysis | `stage3_screen_analysis.py` | no | noise-floor report; Morris μ*/σ with a dummy-parameter significance threshold (`stage3_morris_screen.csv`) |

## Stage-3 screening protocol (current configuration, 2026-07-28)

The earlier configuration could not resolve *any* parameter: at 1e5 events the
objective was a Poisson count with λ ≈ 0.93 (~104% relative error per
evaluation, smallest resolvable effect ~440%), and the ODE parameters — which
have no code path into Geant4 — ranked at the top of the correlation table.
Four changes fixed that.

| Change | From | To | Why |
|---|---|---|---|
| `/main/gun/setEnergy` | ±50% of 2Δ_Al, i.e. [191, 573] µeV | **[0.6, 1.5] meV** | Two of the four Morris levels sat below the 2Δ pair-breaking gate, so ~47% of the design simulated nothing. Floor 0.6 meV = 2Δ_Al at the *top* of the `setTopGap` sweep; ceiling 1.5 meV set by phonon transport (isotope MFP falls to 137 µm at 1.5 meV vs a 525 µm substrate). |
| `/g4cmp/minEPhonons` | 382 µeV | **38.2 µeV** | It sat *above* the lowest physical threshold in the design (2Δ_Al = 191 µeV at the bottom of the gap sweep), so a numerical cut was preempting a physical one and truncating the downconversion cascade. |
| `vtrans` | absolute velocity, [2700, 8100] m/s | **ratio v_T/v_L ∈ [0.3, 0.9]** | Independent sweeps let v_T exceed v_L (ranges overlap on [4500, 8100]), which is not a crystal: G4CMP's group-velocity map assumes v_L > v_T. (This is an acoustic-mode/G4CMP requirement, **not** a Born criterion — the cubic Born conditions are C₁₁>\|C₁₂\|, C₁₁+2C₁₂>0, C₄₄>0, none of which implies C₁₁>C₄₄.) See failure mode 3 below. |
| Sampling per design point | 1 run, 1 fixed source position | **16 Sobol source positions × 2 replicas** | Si phonon focusing is strongly anisotropic, so one fixed injection site measures one caustic rather than the device. Positions are identical across all design points, making them a common spatial scenario set; explicit seeds are paired within a trajectory and differ between replicas. |

Result at 4e6 events per design point (16 positions × 2 replicas × 125,000):

| | Before | After |
|---|---|---|
| λ (`total_QPs`) | 0.93 | **147.4** |
| Relative SD per evaluation | 104% | **12.4%** |
| Design points with zero signal | 78% | **0%** (noise-floor run); 0.12% (8/6912) in the screen |
| Smallest resolvable effect | ~440% | **53%** (single comparison) |

**The counts are compound Poisson, not Poisson.** Fano factor **2.27**: a
Poisson number of pair-breaking phonons, each yielding a variable number of
QPs. Over *all* noise-run hits (13,458 QP-producing top-surface hits) the
multiplicity has mean **2.191**, variance **0.346**, so the compound-Poisson
prediction is `E[X²]/E[X] = ` **2.349** against a measured 2.266 — compatible,
since a bootstrap of the 200 evaluations puts the measured Fano at
[1.86, 2.70]. (An earlier version of this file quoted 2.155 / 0.287 / 2.288
from a ~40-file subset; those numbers were undersampled and the near-exact
agreement they showed was spurious.)

Consequence: the textbook `λ > 18/δ²` planning formula **understates the
required statistics by exactly F**, so `stage3_screen_analysis.py` uses the
measured spread rather than the Poisson idealisation.

**F is not a constant.** Estimated from the paired replicas of the screen, the
dispersion runs from **2.2 in the lowest count decile to 4.1 in the highest**.
Do not assume `Var = 2.27 λ` across the design box — for optimisation, use
candidate-specific replicated estimates or a heteroscedastic count model.

**`setIsland` / `setIslandSpacing` are backside parameters, not qubit geometry.**
Despite living under `/main/electrode_param/`, they are consumed only by
`WaffleKaplanElectrode`, which is attached to the **bottom** surface; they set
the backside Cu absorber pattern (`l_cell = l_island + l_spacing`, coverage
~ `(l_island/l_cell)²`). `setWidth`/`setHeight` are used only by
`JunctionKaplanElectrode` on the top surface and *are* the qubit junction
dimensions. So the island pair belongs to the backside mitigation stack, not to
the degenerate "shrink the qubit" geometry group.

**Dummy-parameter significance threshold.** μ\* = mean|elementary effect| is
positively biased under noise and has no null value: `E|N(0,σ)| = σ√(2/π) > 0`,
so a parameter with exactly zero true effect still earns a positive μ\* and the
ranking will happily order pure noise. The six QPDE parameters (`f_01`, `r`,
`s`, `I_ph`, `pt`, `n_cooper`) are sampled and recorded like any other
parameter but are consumed only by the post-hoc ODE, so they cannot influence
`total_QPs`. Their μ\* *is* the noise floor, and a physical parameter counts as
significant only when a **paired trajectory bootstrap** of (its μ\* − the
largest dummy μ\*) stays positive at the 5th percentile. Resampling both sides
within each draw propagates the uncertainty in the threshold, which is itself a
noisy statistic estimated from six parameters; comparing against its point
estimate overstates the count (20 vs the correct **18**). Intervals are 90%
two-sided (95% one-sided lower bound).

This is a screening rule, not a family-wise-error-controlled test over 47
comparisons — report results as "above the empirical dummy screening
threshold". The dummies are valid controls only for objectives built purely
from generated QPs; they enter `calculate_xQPs`, so the script refuses
`peak_DG_MHz` and `total_integrated_DG`.

Stage 1 writes one manifest line per **(design point, replica)** *at
macro-generation time*, before anything runs, because everything stage 2 needs
is determined by the macro and that point's Morris row. Each entry lists the
`hits_files` of all its source positions, which stage 2 pools; if some are lost
it rescales `n_sim` so QP-yield-per-event stays unbiased rather than discarding
the point. Stage 2 therefore never needs the macro template, the Geant4
environment, or G4CMP — only the manifest plus the hits files. It can run while
stage 1 is still going, skipping entries whose hits files do not exist yet, and
it wipes its own prior outputs on every run so re-running never duplicates rows
(hence the input-validation guard, failure mode 5 below).

One design point is `N_POSITIONS × N_REPLICAS` separate Geant4 processes, not
one. That is forced by two measured constraints: `/g4cmp/HitsFile` cannot be
re-pointed after `/run/initialize`, and a second `/run/beamOn` **truncates**
the hits file rather than appending — so one hits file per source position
requires one process per source position — which also makes a replica a
separate process. (CLHEP seeding *is* controllable from the macro via
`/random/setSeeds`; see the Reproducibility section. The hits-file constraint
is what forces the process split, not the seeding.)

### Latest screening run

`results/morris_mimir_c7a18a17-91ce-491b-8124-78a4ff16576a` (2026-07-28),
T=128, `SENSITIVITY_MORRIS_SEED=20260727`, on mimir with 32 workers:

| | |
|---|---|
| Design | 6912 points × 2 replicas × 16 positions = **221,184 sub-runs** |
| Events | 125,000 per sub-run → **4e6 per design point** (2.8e10 total) |
| Stage 1 | **9h 43m**, 221,184/221,184 hits files, **0 failures**, 0 zero-byte (i.e. no crashes; 39,430 sub-runs legitimately recorded no hits) |
| Peak RSS | **2.15 GB** across all concurrent sub-runs (300 GB budget, 0 guard kills) |
| Stage 2 | 13,824/13,824 entries, 0 skipped, 0 partial-position |

Preceded by the noise-floor run
`results/morris_mimir_f52fb742-8b08-4ecf-9e46-d869a77750cd` (200 identical
points, 3200 sub-runs, 45 min, 0 failures), which produced the λ, Fano and
autocorrelation numbers quoted above.

## Layout

| File | Role |
|---|---|
| `stage1_run_simulations.py` | stage 1 runner; serial-debug and parallel-production modes behind one switch |
| `stage2_compute_QPs.py` | stage 2; quasiparticle binning and the decoherence-rate ODE |
| `stage2_compute_QPs_sensitivity_analysis.py` | stage 2b; parameter-vs-outcome correlation analysis (see below) |
| `stage3_screen_analysis.py` | stage 3; `--mode noise-floor` and `--mode morris` (dummy-calibrated screening) |
| `stage3_plot_results.py` | stage-3 figure: ranked μ* with CIs, the noise floor vs Poisson, and the μ*–σ plane |
| `RESULTS_stage1_to_stage3.md` | **results write-up** for the current screen: what μ* means, run stats, rankings, physics reading, caveats |
| `SensitivityAnalysis_correlation_math.md` | equations + annotated code walkthrough of stage 2b's two methods |
| `sensitivity_params.py` | **single source of truth** for every swept parameter (command, default, bounds, unit) |
| `sensitivity_utils.py` | shared helpers (macro line rewriting, log retention, formatting) |
| `sensitivity_memguard.py` | RSS-based memory guard: per-sample and aggregate caps, enforced by killing the process group |
| `sensitivity_template_screen.mac` | **current** template for the stage-3 screen (1 meV gun, `minEPhonons` 38.2 µeV; events/positions injected by stage 1) |
| `sensitivity_template_beamOn*.mac` | older templates; the suffix is the `/run/beamOn` count |
| `SensitivityAnalysis_Morris_run_record_Mac_M4Max.md` | macOS setup, defaults, launch commands, operational rules |
| `SensitivityAnalysis_Morris_run_record_Mimir.md` | BNL mimir setup, Mac↔mimir path correspondence, cross-machine verification |
| `G4CMP_crash_and_memory_analysis.md` | root-cause analysis of two Geant4/G4CMP failure modes (see below) |
| `SensitivityAnalysis_Paul.ipynb` | Paul Baity's original Sobol-based notebook; source material for stage 2/2b's QP/ODE/correlation logic (messy, edited in place many times — see caveats below) |
| `Modeling phonon-mediated quasiparticle poisoning in superconducting qubit arrays.pdf` | reference paper this project's QP-poisoning model is based on |

`SensitivityAnalysis_Morris*.py` at the top level (other than the two stage
scripts) and `SensitivityAnalysis_{Sobol,Percentage}.py` are the previous
generation, kept for reference. They are not maintained against the current
`sensitivity_params.py`/`sensitivity_utils.py`.

Generated `output/` and `results/` directories are **not** tracked — each run
mints a fresh UUID directory and they reach hundreds of MB. They are
reproducible from the code plus a fixed `SENSITIVITY_MORRIS_SEED`.

## Running

One file runs on both the development Mac and the BNL server `mimir`: the
Geant4/G4CMP/conda layout and `G4SYSTEM` are selected from `sys.platform`,
and every path stays overridable via a `SENSITIVITY_*` environment variable.
There is deliberately no per-machine fork.

Generate the run files without launching Geant4:

```bash
SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 python -u stage1_run_simulations.py
```

A small real run, then stage 2:

```bash
SENSITIVITY_MAX_SAMPLES=54 SENSITIVITY_SAMPLE_TIMEOUT=600 \
SENSITIVITY_MACRO_TEMPLATE="$PWD/sensitivity_template_beamOn10000.mac" \
python -u stage1_run_simulations.py

python stage2_compute_QPs.py --results-dir results/<run_id>
```

Stage 1 prints the exact stage 2 command for the run it just finished.

### Key environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SENSITIVITY_DEBUG_MODE` | `0` | `1` = serial, live-streamed Geant4 output; `0` = parallel, log-only |
| `SENSITIVITY_MAX_WORKERS` | 2 (macOS) / 4 (Linux) | parallel worker processes |
| `SENSITIVITY_MAX_SAMPLES` | all | cap the design to the first N samples |
| `SENSITIVITY_GENERATE_ONLY` | `0` | write macros/configs but never launch Geant4 |
| `SENSITIVITY_MACRO_TEMPLATE` | `sensitivity_template_beamOn1e6.mac` | which template (and so which `/run/beamOn`) to use |
| `SENSITIVITY_MORRIS_SEED` | unset | seed the Morris sampler; identical seeds give identical designs across machines |
| `SENSITIVITY_SAMPLE_TIMEOUT` | `0` (off) | per-sample wall-clock limit in seconds; kills the whole process group |
| `SENSITIVITY_N_POSITIONS` | `1` | source positions per design point (scrambled Sobol, identical across points) |
| `SENSITIVITY_N_REPLICAS` | `1` | independent CLHEP realizations per design point; kept separate in the manifest |
| `SENSITIVITY_TOTAL_EVENTS` | `0` (use template) | **TOTAL** primary phonons per design point; `/run/beamOn` per sub-run is derived as this ÷ (positions × replicas), and must divide exactly |
| `SENSITIVITY_TOTAL_MEM_GB` | `300` | aggregate RSS ceiling across all concurrent sub-runs; refuses to start above host `MemAvailable` |
| `SENSITIVITY_PER_SAMPLE_MEM_GB` | `4` | per-sub-run RSS cap (~55× a healthy 70 MB run) |
| `SENSITIVITY_NOISE_FLOOR_N` | `0` (off) | replace the Morris design with N *identical* default points, to measure the noise floor |
| `SENSITIVITY_EXPLICIT_SEEDS` | `1` | emit `/random/setSeeds` per sub-run. `0` reverts to `Main.cc`'s unsafe `clock()` seeding |
| `SENSITIVITY_SEED_BASE` | `20260728` | base of the seed bank; seeds are keyed by (trajectory, replica, position) and recorded in the manifest |

Path overrides (`SENSITIVITY_GEANT4_ROOT`, `SENSITIVITY_G4CMP_ROOT`,
`SENSITIVITY_G4WORKDIR`, `SENSITIVITY_G4SYSTEM`, `SENSITIVITY_MAIN_EXE`, …)
are listed in the two run records.

## Known issues worth reading before a long run

`G4CMP_crash_and_memory_analysis.md` covers the Geant4/G4CMP-layer ones in
detail — its Finding 1, 2, 4 and 5 correspond to items 1, 2, 3 and 4 below (its
Finding 3, the physics-free energy levels, is now **resolved** by the stage-3
protocol). Item 5 below is in this repository's Python layer and is documented
here only. All are in the C++ layer or the sweep
design, not in this Python orchestration.

1. **Infinite loop in `G4CMPKaplanQP::AbsorbPhonon` when a film's
   `lowQPLimit` is 1** — the quasiparticle can never fall below the exit
   threshold, so the sample spins at 100% CPU and can grow to tens of GB of
   RSS. Mitigated by sweeping the three `QPLim` parameters over `[2, 5]`
   (applied 2026-07-20) and, as a backstop, by `SENSITIVITY_SAMPLE_TIMEOUT`.
2. **Exit-time SIGSEGV** (~0.4% of runs) from a stale `G4TouchableHandle`
   released after its allocator pool is torn down. Harmless: physics and the
   hits file are complete before the fault, and the runner logs the failure
   and continues the batch.
3. **SIGSEGV in `G4LatticeLogical::LookupKtoVg` when `vtrans > vsound`**
   (**FIXED**). Distinct from mode 2 and *not* harmless — it strikes mid-run
   and leaves a **zero-byte** hits file. G4CMP builds its phonon
   group-velocity map assuming v_L > v_T, and an inverted crystal indexes off
   the end of that table. Perfect separation on the aborted screen: 31/31
   affected design points crashed, 0/30 unaffected ones did (Fisher exact
   p = 4.3e-18). It is stochastic *per event* (p ≈ 6e-5) but deterministic per
   design point, so at 125k events per sub-run **every** sub-run of an
   affected point dies — the point yields no data at all, and 17.3% of the
   T=128 design was affected. Fixed by sweeping `vtrans` as the ratio
   v_T/v_L ∈ [0.3, 0.9]; verified 0/6912 configs invalid and 0 crashes in 40
   design points at the event count that previously gave 8/8.
4. **A macro that aborts still exits 0** (**guarded**). A malformed command
   (e.g. a bad unit suffix) makes Geant4 emit a G4Exception *warning*, skip
   `/run/beamOn` entirely, and exit 0 — so the batch records successes that
   simulated nothing. Stage 1 now treats "exit 0 but no hits file" as a hard
   `MacroAborted` failure.
5. **Analysis scripts degraded silently on incomplete runs** (**fixed**).
   Three distinct instances, all of the same shape — a check that validated an
   artifact using a quantity derived from that same artifact:
   * Stage 2 wiped `qps/` and `qp_summary.csv` *before* checking it had
     readable inputs, so running it against a run whose `hits/` had been
     archived destroyed the summary and rewrote nothing.
   * Stage 2b inferred each design point's expected replica set from
     `qp_summary.csv`; with that file missing every point was silently dropped,
     and with it truncated a point was averaged over one replica (carrying
     √2× its neighbours' noise while looking identical).
   * Stage 3 inferred the expected replica count as the *median* of the
     observed counts, which tracks the damage — reduce 5,000 of 6,912 points to
     one replica and the median becomes 1, so nothing is flagged — and counted
     rows rather than identities, so two copies of `r0` passed as `{r0, r1}`.

   All three now judge completeness against `qp_manifest.jsonl`, which stage 1
   writes at macro-generation time and which no downstream failure can degrade.
   Stage 3 additionally hard-fails on a malformed Morris step (rather than
   skipping it) and asserts every parameter received exactly one elementary
   effect per retained trajectory.


Resolved and no longer an issue: `/g4cmp/minEPhonons` sitting above two of the
four `/main/gun/setEnergy` Morris levels, which made ~47% of the design
simulate nothing. Both were changed — see the stage-3 protocol table above.

## Stage 2b: sensitivity correlation analysis

`stage2_compute_QPs_sensitivity_analysis.py` reproduces the parameter-vs-outcome
correlation analysis in Paul Baity's `SensitivityAnalysis_Paul.ipynb` cells
`In[3]`/`In[5]` for this Morris run. It reads `MorrisSequence.csv` (the design)
and the `qps/*_xQPs.npz` curves stage 2 already wrote (no ODE recompute) and
answers "which parameters correlate with more decoherence." Full math in
`SensitivityAnalysis_correlation_math.md`.

```bash
python stage2_compute_QPs_sensitivity_analysis.py --results-dir results/<run_id>
```

Two `--method`s (default `both`):

- **`integrated`** (experiment-free): outcome = `log10(total_integrated_DG)`,
  correlated against every parameter via `np.corrcoef`. Faithful to Paul's
  experiment-free sibling cell.
- **`chi2`** (faithful to `In[3]`): outcome = a chi-squared goodness-of-fit of
  each electrode's simulated ΔΓ(t) against real experimental
  Delta-Gamma-vs-delay data (`--experimental-dir`, default points at a 150 µs
  NbGND dataset found on mimir outside this project — see the script docstring
  for the exact path and why 150 µs was chosen).

### Known concerns / caveats

- **The chi² outcome is magnitude-dominated, not a literal experiment fit.**
  Measured on the old 1e5-event run, and not re-verified since the stage-3
  protocol changed the injection energy and event count — treat the 0.999
  ratio below as indicative, not current. That run's localized
  `phonon_Caustic` injection produces
  simulated ΔΓ 1–2 orders of magnitude larger than the experimental data, so
  `chi2 ≈ sum(simulated_DG^2)` (verified ratio 0.999) — read chi² correlations
  as "which parameters drive the simulated response," not as calibration
  against experiment.
- **17 electrodes vs 6 measured qubits.** Only 6 experimental delay curves
  exist. `--chi2-channels electrodes` (default) scores all 17 electrodes
  against their nearest qubit's curve — the 6 curves are reused by proximity,
  so this is 17 simulated channels vs 6 measured references, not 17
  independent fits. `--chi2-channels qubits` gives Paul's literal 6-channel
  reproduction.
- **Signal sparsity — historical, fixed by the stage-3 protocol.** In the old
  1e5-event configuration only ~1500 of 6912 samples (~22%) produced non-zero
  integrated decoherence and the rest were dropped from the correlation (same
  filtering spirit as Paul's `len(output_x[i])>0` guard). At the current 4e6
  events per design point **0%** of points are zero-signal, so this filter no
  longer removes anything meaningful.
- **`qp_summary.csv` must be complete before running this script.** It is
  regenerated from scratch by every `stage2_compute_QPs.py` run and by nothing
  else — if a stage-2 run is interrupted (e.g. killed mid-run from an IDE
  debugger), the summary is left truncated. This script detects and warns on
  an incomplete summary and falls back to deriving outcomes directly from the
  hits files, but that path is much slower.
- **Correlation is linear/Pearson only.** A parameter with a strong
  non-monotonic effect can show `r≈0` here even though it matters; this is a
  screening tool, not a full sensitivity index (see the Morris μ*/σ indices
  computed by stage 1's own design for a complementary, non-linear-aware view).

## Reproducibility caveat

Geant4's `Main.cc` seeds CLHEP from `clock()`, so hits files are **not**
reproducible across machines or reruns. What *is* reproducible with a fixed
`SENSITIVITY_MORRIS_SEED` is the design, the generated macros and lattice
configs, and the manifest — all verified byte-identical between macOS and
mimir, as is stage 2's output given identical hits input.

**RETRACTED (2026-07-28): `clock()` seeding DOES reuse random streams.** An
earlier version of this file concluded the opposite, on the strength of a
run-order autocorrelation test (lag 1 r = +0.108, p = 0.13). That test was
incapable of detecting the problem: it looks for *lag-structured linear
correlation*, not for stream *repetition*, which is non-local. An earlier
duplicate scan also missed it because it ran on a design where every
configuration differs, so a reused seed still produces different bytes.

Direct evidence, from the 200-point noise-floor run where every configuration
is identical (so a reused stream must yield a byte-identical hits file):

| Run | Duplicate groups | Files involved |
|---|---|---|
| noise floor (3,200 sub-runs) | 23 | 47 |
| full screen (221,184 sub-runs) | 30 | 60 |

Eleven of the screen's duplicate pairs sit at **adjacent design points inside
the same trajectory**, i.e. inside an elementary effect. `clock()` measures CPU
time consumed since process start, which for a freshly launched Geant4 job is
nearly constant, so the effective seed space is small.

**Fixed.** `Main.cc` executes the macro *after* seeding, so `/random/setSeeds`
in the macro overrides it — verified: with an explicit seed the hits content is
byte-identical across runs (only the Event ID column shifts, which stage 2 never
reads), and a different seed gives a different stream. Stage 1 now emits a seed
keyed by `(trajectory, replica, position)` and records it in the manifest; see
`SENSITIVITY_SEED_BASE`. Keying by trajectory rather than by design point means
both endpoints of every elementary effect share a stream — common random
numbers, which reduces the variance of the difference.

Scope of the damage to the existing screen: 0.033% of sub-runs, and a shared
stream between the endpoints of an effect is variance-*reducing*, not biasing.
The rankings are not invalidated — the leaders reproduce across independent
trajectory halves at Spearman rho = 0.829 — but the run is not bit-reproducible
and should not be treated as a quantitative optimizer training set.

## Reproducing the current screen

```bash
# 1. Noise floor (200 identical points; ~45 min on 8 workers)
SENSITIVITY_NOISE_FLOOR_N=200 SENSITIVITY_MACRO_TEMPLATE="$PWD/sensitivity_template_screen.mac" \
SENSITIVITY_N_POSITIONS=16 SENSITIVITY_N_REPLICAS=1 SENSITIVITY_TOTAL_EVENTS=4000000 \
SENSITIVITY_MAX_WORKERS=8 SENSITIVITY_SAMPLE_TIMEOUT=1800 SENSITIVITY_LOG_MODE=failures \
python -u stage1_run_simulations.py

# 2. Full T=128 screen (221,184 sub-runs; ~9.7 h on 32 workers)
SENSITIVITY_MACRO_TEMPLATE="$PWD/sensitivity_template_screen.mac" \
SENSITIVITY_MORRIS_SEED=20260727 \
SENSITIVITY_N_POSITIONS=16 SENSITIVITY_N_REPLICAS=2 SENSITIVITY_TOTAL_EVENTS=4000000 \
SENSITIVITY_MAX_WORKERS=32 SENSITIVITY_TOTAL_MEM_GB=300 SENSITIVITY_PER_SAMPLE_MEM_GB=4 \
SENSITIVITY_SAMPLE_TIMEOUT=1800 SENSITIVITY_LOG_MODE=failures \
python -u stage1_run_simulations.py

python stage2_compute_QPs.py --results-dir results/<run_id>
python stage3_screen_analysis.py --mode morris --results-dir results/<run_id>
```

Launch long runs with `setsid` (e.g. `setsid nohup … &`). `nohup` alone only
ignores SIGHUP, so a job started from a shell that is later killed as a
process group dies with it — this silently truncated one screen at 576 of
221,184 sub-runs.

**T=64 comes free.** With the same seed, SALib's T=64 design is exactly the
first half of the T=128 design (verified), so a T=64 screen is the first
`64 × 54 = 3456` design points of a T=128 run — no separate run needed.

## Credits

The quasiparticle and decoherence-rate calculations in `stage2_compute_QPs.py`
are adapted from Paul Baity's `calculate_QPs` / `calculate_xQPs`.
