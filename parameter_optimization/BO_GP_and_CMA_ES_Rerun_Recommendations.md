# BO–GP and CMA-ES Rerun Recommendations

## Executive recommendation

The completed campaigns are insufficient for comparing optimizer performance:

- BO–GP completed 23 candidate evaluations but only **one genuine GP–Expected-Improvement (GP–EI) cycle**. Its best candidate came from the Sobol initialization.
- CMA-ES recorded 32 candidate evaluations but completed only **one genuine population update**, with population size 12. The remaining historical labels included random filler points.

The recommended formal rerun is:

| Method | Design per optimizer seed | Evaluations per seed | Independent seeds |
|---|---:|---:|---:|
| BO–GP | 32 Sobol initial points + 64 GP–EI cycles | 96 | at least 3 |
| CMA-ES | 8 complete generations × 12 candidates | 96 | at least 3 |
| Random search | 96 independent candidates | 96 | at least 3 |
| Sobol search | 96 space-filling candidates | 96 | at least 3 |

All methods should use the same frozen physical contract, S-tier simulation, injection sites, physics seed bank, objective, and **paid primary-phonon count**. Compare them using best-so-far objective versus cumulative simulated primary events—not merely trial number or wall time.

---

## 1. Common optimization problem

The decision space is

\[
\mathcal X=[0,1]^{16}\times\mathcal M,
\qquad |\mathcal M|=13,
\]

where the 16 continuous material/environment variables are transformed to the unit hypercube and \(\mathcal M\) is the set of allowed Miller directions.

The minimized quantity is

\[
J(x)=\frac{N_{\mathrm{QP}}^{\mathrm{junction}}(x)}{N_{\mathrm{primary}}},
\]

where \(N_{\mathrm{QP}}^{\mathrm{junction}}\) is the total QP count at the 17 Al junction electrodes. The search fidelity is

\[
N_{\mathrm{primary}}=4\times10^6
\]

per candidate, evaluated at the same 16 injection positions. Under the current contract, each position has eight independent replicas.

The optimizer changes only how the next property vector is proposed. G4CMP evaluation, physical feasibility checks, objective calculation, data recording, and later material projection remain identical.

---

## 2. Recommended BO–GP campaign

### 2.1 Design

Use

\[
N_{\mathrm{init}}=32,
\qquad
N_{\mathrm{GP-EI}}=64,
\qquad
N_{\mathrm{total}}=96
\]

per optimizer seed.

Reasons:

1. **Thirty-two is a natural Sobol design size.** Sobol coverage is best at powers of two, and 32 supplies approximately two initial samples per continuous dimension.
2. **The problem is mixed and moderately high-dimensional.** Sixteen continuous variables and 13 crystal directions are too complex for one or a few adaptive cycles.
3. **The objective is noisy.** Replica-based simulation uncertainty can reorder close candidates; at least 50 genuine acquisitions are needed before evaluating BO efficiency. Sixty-four provides a modest safety margin.
4. **GP model risk remains significant.** Multiple optimizer seeds are necessary because one search trajectory can favor a lucky initial point or one local basin.

### 2.2 Cycle

For data

\[
\mathcal D_t=\{(x_i,J_i,\widehat\sigma_i)\}_{i=1}^{n_t},
\]

one adaptive cycle is

\[
\mathcal D_t
\xrightarrow{\text{fit/update GP}}
(\mu_t,\sigma_t)
\xrightarrow{\arg\max \mathrm{EI}}
x_{t+1}
\xrightarrow{\mathrm{G4CMP}}
J_{t+1}
\xrightarrow{\text{update}}
\mathcal D_{t+1}.
\]

The repository uses a Matérn-5/2 ARD kernel, a learned overlap correlation for Miller directions, heteroscedastic replica-derived noise, and Expected Improvement for minimization. It fits the GP to \(\log J\).

Condensed implementation:

```python
if initial_points_dispatched < n_init:
    point = sobol_initial_point()
else:
    gp.fit(U, miller_categories, log_objectives, noise_variances)
    pool = generate_4096_candidates()
    point = polish_top_four_EI_candidates(pool)

result = evaluate_with_g4cmp(point)
optimizer.tell(point, result.objective)
```

### 2.3 Pinned settings

| Parameter | Recommended value |
|---|---:|
| Sobol initial points | 32 |
| Genuine GP–EI cycles | 64 |
| Total evaluations | 96 |
| GP kernel | Matérn-5/2 ARD × categorical overlap |
| GP response | \(\log J\) |
| Acquisition | Expected Improvement |
| EI exploration offset \(\xi\) | 0 |
| Acquisition candidate pool | 4096 |
| Locally generated candidate fraction | 1/4 |
| Local perturbation width | 0.08 in unit coordinates |
| EI candidates polished | 4 |
| GP hyperparameter refit cadence | every 5 observations |
| GP hyperparameter restarts | 3 |
| Parallel candidates | 4 |
| Optimizer seeds | at least 3 |

### 2.4 Example command

```bash
python stage4_optimize.py \
  --optimizer bo_gp \
  --optimizer-params '{"n_init":32,"refit_every":5,"n_candidates":4096,"n_polish":4,"xi":0.0}' \
  --trials 96 \
  --parallel 4 \
  --workers 32 \
  --fidelity S \
  --seed 1 \
  --seed-bank 0 \
  --tag bo_seed1
```

Repeat with optimizer seeds 2 and 3 while keeping the physics seed bank fixed. Use a different held-out physics seed bank for confirmation.

---

## 3. Recommended CMA-ES campaign

### 3.1 Relationship to BO

CMA-ES uses the same outer interface,

```text
ask for candidate → G4CMP evaluation → compute J(x) → return result,
```

but it does not use a GP surrogate or Expected Improvement. It replaces the BO proposal mechanism with population-based distribution adaptation.

At generation \(g\), CMA-ES maintains

\[
m_g\in\mathbb R^{16},\qquad
C_g\in\mathbb R^{16\times16},\qquad
\sigma_g>0.
\]

It samples

\[
u_k=\operatorname{clip}_{[0,1]}
\left(m_g+\sigma_g A_gz_k\right),
\qquad
z_k\sim\mathcal N(0,I),
\qquad
A_gA_g^{\mathsf T}=C_g,
\]

evaluates the complete population, ranks candidates by \(\log J\), and updates the mean, covariance, and step size using the best half of the population.

Condensed implementation:

```python
for k in range(popsize):
    z = rng.standard_normal(d)
    u = np.clip(mean + sigma * (chol(Cov) @ z), 0.0, 1.0)
    population.append(space.from_unit(u, sample_miller()))

values = evaluate_population_with_g4cmp(population)
selected = population[np.argsort(values)[:mu]]
mean = weights @ selected
Cov = rank_one_and_rank_mu_update(Cov, selected)
sigma = cumulative_step_size_update(sigma)
```

Unlike BO, one CMA-ES iteration means one **complete generation**, not one function evaluation.

### 3.2 Design

For \(d=16\), the repository defaults to

\[
\lambda=12,
\qquad
\mu=6.
\]

For an equal-cost comparison with the recommended 96-evaluation BO campaign, run

\[
8\text{ generations}\times12\text{ candidates}=96\text{ evaluations}
\]

per seed. A secondary 10-generation run may be used to study later CMA adaptation, but its additional simulation cost must be shown on the same cumulative-event axis.

### 3.3 Pinned settings

| Parameter | Recommended value | Purpose |
|---|---:|---|
| Continuous dimension \(d\) | 16 | Current property space |
| Population \(\lambda\) | 12 | Noise-robust rank comparison |
| Selected parents \(\mu\) | 6 | Best half updates distribution |
| Initial mean | \((0.5,\ldots,0.5)\) | Center of transformed space |
| Initial covariance | \(I_{16}\) | Initially isotropic search |
| Initial step size `sigma0` | 0.3 | Broad initial exploration |
| Minimum restart step size | 0.03 | Detect collapsed search scale |
| Stagnation trigger | 8 generations | Restart after no improvement |
| Incumbent reevaluation | every 6 generations | Detect noise or simulation drift |
| Maximum population | 64 | IPOP restart cap |
| Restart rule | double population | 12 → 24 → 48 → 64 |
| Miller exploration floor | 15% uniform | Prevent loss of crystal directions |
| Update mode | synchronous | Only complete generations update CMA |
| Formal run length | 8 generations | 96 equal-cost evaluations |
| Optimizer seeds | at least 3 | Measure trajectory variation |

### 3.4 Example command

```bash
python stage4_optimize.py \
  --optimizer cmaes \
  --optimizer-params '{"popsize":12,"sigma0":0.3,"reeval_every":6,"sigma_min":0.03,"stagnation":8,"max_popsize":64,"synchronous":true}' \
  --trials 96 \
  --parallel 4 \
  --workers 32 \
  --fidelity S \
  --seed 1 \
  --seed-bank 0 \
  --tag cma_seed1
```

Repeat for optimizer seeds 2 and 3.

---

## 4. Fair comparison and reporting

For each method, report

\[
J_{\mathrm{best}}(B)
=
\min_{i:\,B_i\le B}J(x_i),
\]

where \(B\) is the cumulative number of paid primary events, including partially completed or failed simulations.

Required outputs:

1. Median and range over at least three optimizer seeds.
2. Best-so-far \(J\) versus cumulative primary events.
3. True proposal counts: `sobol_init`, `gp_ei`, `cma_generation`, `cma_incumbent_reeval`, `random_baseline`, and failures.
4. Number of GP fits and genuine GP acquisitions.
5. Number of complete CMA generations and actual restarts.
6. Candidate-specific uncertainty and held-out confirmation results.

Do not rank methods using their single best noisy S-tier observation alone.

---

## 5. High-fidelity promotion

Use S-tier only for search. Then apply a common confirmation funnel:

1. Select the top 3–5 candidates from each optimizer seed.
2. Deduplicate physically equivalent or nearly identical vectors.
3. Re-simulate the combined shortlist at M using a held-out physics seed bank.
4. Promote only statistically resolved candidates to L.
5. Run the nearest-real-material projection only from a confirmed property target.

Suggested promotion criterion:

\[
J_{\mathrm{candidate}}+2\,\mathrm{SE}_{\mathrm{candidate}}
<
J_{\mathrm{baseline}}-2\,\mathrm{SE}_{\mathrm{baseline}},
\]

supplemented by paired injection-site comparisons and stability from M to L.

---

## 6. Prioritized additional recommendations

### Priority 0 — Freeze the benchmark contract

**Action:** Keep the current search bounds, physical objective, geometry, injection sites, fidelity, and physics seed bank unchanged during the optimizer comparison.

**Reason:** Changing a bound, objective, engineering constraint, or replica structure changes the problem. Results would no longer isolate the optimizer as the only experimental variable.

### Priority 1 — Complete the fair rerun before making optimizer claims

**Action:** Run BO–GP, CMA-ES, random, and Sobol with 96 paid S-tier evaluations per seed and at least three optimizer seeds.

**Reason:** The historical BO run completed only one genuine acquisition, and the historical CMA run completed only one generation. Their measured candidate vectors remain valid, but their algorithm-efficiency ranking does not.

### Priority 2 — Add a device-quality constraint in a separate campaign

**Action:** After the frozen benchmark, start a new campaign with a minimum acceptable top-film gap or critical temperature, or use a multi-objective criterion that includes ground-plane QPs/microwave loss.

**Reason:** The current best target uses a top-film gap of approximately 82 µeV. The present objective counts QPs only at the Al junctions and can therefore favor a low-gap phonon sink that may be undesirable for the complete superconducting device.

### Priority 3 — Confirm before projecting to real materials

**Action:** Project only M/L-confirmed targets, and re-simulate every shortlisted material stack using its measured density, elastic tensor, and complete lattice record where available.

**Reason:** Distance in property space is a preselection tool, not a prediction of QP yield. Interface coupling makes the three-layer objective nonseparable.

### Priority 4 — Quantify remaining material-model uncertainty

**Action:** Bracket unsourced film phonon lifetimes and repeat key candidates under alternative interface models.

**Reason:** Substrate-constant brackets show that the SiC elasticity result is relatively insensitive to its borrowed `scat`, `decay`, and `decayTT`; unsourced film lifetimes and interface transmission are now more important uncertainties.

### Priority 5 — Test spatial convergence

**Action:** Repeat selected baseline and finalist calculations with 16, 32, and 64 injection positions.

**Reason:** Increasing events at the same 16 sites reduces Monte Carlo noise but does not establish that the chip surface is sampled adequately.

### Priority 6 — Widen parameter bounds only after the constrained campaign is defined

**Action:** Consider wider bounds around high \(C_{11}\), high \(C_{44}\), low isotope scattering, and low top-film gap, but assign a new campaign identity and impose the device-quality constraint first.

**Reason:** The current best vector approaches several search boundaries. Widening without engineering constraints would likely drive the optimizer further into the same low-gap corner rather than produce a fabrication-relevant design.

---

## 7. Recommended decision rule

The formal optimizer comparison is sufficient when all conditions below hold:

- BO completes at least 50 genuine GP–EI acquisitions per seed;
- CMA-ES completes at least eight full generations per seed;
- at least three independent optimizer seeds are available;
- every method has the same paid primary-event budget;
- proposal provenance and partial failures are included;
- the leading candidates remain favorable under held-out M/L confirmation;
- conclusions are reported as median and range over seeds.

The practical default is therefore:

\[
\boxed{
\text{BO: }32+64=96\text{ evaluations/seed},
\qquad
\text{CMA-ES: }8\times12=96\text{ evaluations/seed}.
}
\]

---

## Repository references

- [Current branch head](https://github.com/HHTseng/QPU-material-sensitivity-analysis/commit/0aeaeac9b31a0f4b457abd9f6b8a3cf489812cd2)
- [BO–GP and CMA-ES implementations](https://github.com/HHTseng/QPU-material-sensitivity-analysis/blob/0aeaeac9b31a0f4b457abd9f6b8a3cf489812cd2/parameter_optimization/stage4_optimizers.py)
- [Common asynchronous optimization driver](https://github.com/HHTseng/QPU-material-sensitivity-analysis/blob/0aeaeac9b31a0f4b457abd9f6b8a3cf489812cd2/parameter_optimization/stage4_optimize.py)
- [Frozen physics and fidelity contract](https://github.com/HHTseng/QPU-material-sensitivity-analysis/blob/0aeaeac9b31a0f4b457abd9f6b8a3cf489812cd2/parameter_optimization/stage4_config.yaml)
- [Results and audit-qualified claims](https://github.com/HHTseng/QPU-material-sensitivity-analysis/blob/0aeaeac9b31a0f4b457abd9f6b8a3cf489812cd2/parameter_optimization/STAGE4_RESULTS.md)
- [Implementation audit and rerun requirements](https://github.com/HHTseng/QPU-material-sensitivity-analysis/blob/0aeaeac9b31a0f4b457abd9f6b8a3cf489812cd2/parameter_optimization/STAGE4_IMPLEMENTATION_AUDIT_AND_FIX_PLAN.md)
