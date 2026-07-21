# Correlation Sensitivity Analysis — Math Walkthrough

A guided, equation-by-equation reading of
[`stage2_compute_QPs_sensitivity_analysis.py`](stage2_compute_QPs_sensitivity_analysis.py),
which reproduces the correlation analysis in Paul Baity's
`SensitivityAnalysis.ipynb` (cells `In[3]`/`In[5]`) for this Morris run.

Everything below was numerically verified against the on-disk outputs of run
`morris_mimir_95494c3c-…` (6912 samples, 1e5 phonons each); the specific numbers
quoted are from that run.

---

## 1. The idea in one sentence

> Vary many model parameters at once, reduce each simulation to **one number**
> (an "outcome"), and ask *which parameters move that number* — measured by the
> **Pearson correlation coefficient** between each parameter and the outcome.

A correlation near $+1$ or $-1$ means the parameter strongly drives the outcome;
near $0$ means it barely matters. This is a **screening** sensitivity method: it
is linear and one-parameter-at-a-time in its *readout*, even though the design
varies all parameters simultaneously.

---

## 2. Notation and inputs

| Symbol | Meaning | Source in this run |
|---|---|---|
| $N$ | number of design samples | 6912 (`MorrisSequence.csv` rows) |
| $P$ | number of model parameters | 53 (`MorrisSequence.csv` columns) |
| $\theta_{i,p}$ | value of parameter $p$ in sample $i$ | `MorrisSequence.csv[i, p]` |
| $E$ | number of electrodes | 17 (`qp_manifest.jsonl` `qx`/`qy`) |
| $\mathrm{DG}_{i,e}(t)$ | decoherence-rate curve [MHz] of electrode $e$, sample $i$ | `qps/Morris_i_xQPs.npz` |
| $Y_i$ | scalar **outcome** for sample $i$ | defined per method below |

The design matrix $\Theta \in \mathbb{R}^{N\times P}$ is the analog of Paul's
`SobolSequence.csv`. Row $i$ **is** the parameter configuration of `Morris_i.mac`
(verified: `MorrisSequence` row $i$ matches manifest entry $i$ exactly for
`f_01, r, s, I_ph, pt, n_cooper, gap`).

---

## 3. Where the outcome comes from — the physics pipeline

Both methods reduce a sample to a scalar built from the per-electrode
decoherence curves $\mathrm{DG}_{i,e}(t)$. Those curves are produced by two
functions (identical to `stage2_compute_QPs.py`) and cached in the
`qps/*_xQPs.npz` files, so this script never re-runs the ODE.

### 3.1 `calculate_QPs` — hits → quasiparticles per electrode per time bin

Each surface hit $h$ (with $E^{\text{dep}}_h>0$ landing on the top surface) is
assigned to its **nearest electrode** and to a time bin $m$ on the grid
$t_m = m\cdot\tfrac{300{,}000\,\text{ns}}{1000}$, $m=0,\dots,1000$:

$$
q(h) = \arg\min_{e}\sqrt{(10^3 X_h - q^x_e)^2 + (10^3 Y_h - q^y_e)^2},
\qquad
m(h) = \arg\min_{m}\bigl|T_h - t_m\bigr|.
$$

The number of quasiparticles a hit creates is its deposited energy divided by
the gap energy $\Delta = $ `setTopGap` [eV]:

$$
\mathrm{QP}_{i}[e,m] \;=\!\!\sum_{\substack{h:\,q(h)=e\\ m(h)=m}}\!\!
\operatorname{round}\!\left(\frac{E^{\text{dep}}_h}{\Delta}\right).
$$

```python
q     = np.argmin(r)                                  # nearest electrode
t_idx = np.argmin(np.abs(rec["Final Time [ns]"][h] - snapshot_t))
QPNos[q, t_idx] += int(np.round(rec["Energy Deposited [eV]"][h] / gap))
```

### 3.2 `calculate_xQPs` — quasiparticle ODE → decoherence rate

For each electrode $e$, the injected QP population is spread over the pulse
duration $p_t$ (a boxcar convolution) and normalised by the electrode volume and
event count:

$$
g_{i,e}(t) \;=\; \bigl(\mathrm{QP}_{i}[e,\cdot]\ast \Pi_{p_t}\bigr)(t)\;
\frac{I_{\text{ph}}}{n_{\text{cooper}}\,H\,W\,d\,N_{\text{sim}}},
$$

where $H,W$ are the electrode width/height, $d$ the top-layer thickness, and
$N_{\text{sim}}$ the actual `/run/beamOn` count (here $10^5$). The QP density
$x_{i,e}(t)$ then obeys a generation–loss–recombination ODE, integrated by
forward Euler:

$$
\boxed{\;\frac{dx_{i,e}}{dt} \;=\; -\,r\,x_{i,e}^2 \;-\; s\,x_{i,e} \;+\; g_{i,e}(t)\;}
$$

and the decoherence-rate contribution is proportional to the density:

$$
\mathrm{DG}_{i,e}(t) \;=\; x_{i,e}(t)\,\underbrace{2\sqrt{2\,\Delta_f\,f_{01}}\times 10^{-6}}_{\text{Factor}},
\qquad \Delta_f = \frac{\Delta}{h}\ \text{[Hz]}.
$$

```python
dx = (-r * x_QP[k, i]**2 - s * x_QP[k, i] + g_QP[k, i]) * dt
x_QP[k, i+1] = x_QP[k, i] + dx
DG[k, :] = x_QP[k, :] * Factor          # Factor = 2*sqrt(2*AlGap*f_01)*1e-6
```

Read off the roles that will explain the signs later:
$\mathrm{DG}\propto\sqrt{f_{01}}$, $g\propto I_{\text{ph}}$,
$g\propto 1/(n_{\text{cooper}}Hd)$, longer $p_t$ pumps more $g$, and $s$ is the
**loss** rate that suppresses $x$.

---

## 4. Method 1 — integrated decoherence (experiment-free)

This is Paul's sibling cell `In[5]`/`In[7]`: reduce each sample to the
**log of its total integrated decoherence**.

### 4.1 Outcome

$$
Y_i \;=\; \log_{10}\!\Bigl(\underbrace{\textstyle\sum_{e=1}^{E}\int \mathrm{DG}_{i,e}(t)\,dt}_{\texttt{total\_integrated\_DG}_i}\Bigr).
$$

The inner sum over electrodes is the **single aggregate** (your earlier choice);
it is exactly the `total_integrated_DG` column already in `qp_summary.csv`. The
$\log_{10}$ compresses a heavy-tailed positive quantity, so only samples with
$\texttt{total\_integrated\_DG}_i>0$ are usable — **1501 of 6912** here (the
other 5411 produced no above-threshold surface signal; this is the analog of
Paul's `np.sum(output_y[i])>0` filter).

```python
val = outcomes_by_name.get(f"Morris_{idx}", np.nan)
if not np.isfinite(val) or val <= 0:      # drop no-signal samples
    dropped += 1; continue
rows.append(np.append(param_values[idx, :], np.log10(val)))
```

### 4.2 The trials matrix and the correlation

Stack the usable rows into $M\in\mathbb{R}^{n\times(P+1)}$ ($n=1501$), the
parameters followed by the outcome:

$$
M = \bigl[\,\Theta^{\text{usable}}\;\big|\;\mathbf{Y}\,\bigr],
\qquad
M_{i,\cdot} = (\theta_{i,1},\dots,\theta_{i,P},\,Y_i).
$$

The Pearson correlation of parameter $p$ with the outcome is

$$
\boxed{\;
r_p \;=\;
\frac{\sum_{i}(\theta_{i,p}-\bar\theta_p)(Y_i-\bar Y)}
     {\sqrt{\sum_{i}(\theta_{i,p}-\bar\theta_p)^2}\,\sqrt{\sum_{i}(Y_i-\bar Y)^2}}
\;\in[-1,1].\;}
$$

`np.corrcoef` computes the **full** $(P+1)\times(P+1)$ matrix
$A_{ab}=\operatorname{corr}(M_{\cdot,a},M_{\cdot,b})$ at once; the outcome is the
last column, so the row of interest is its last row minus its own entry:

$$
r_p = A_{P,\,p} \quad\Longleftrightarrow\quad \texttt{corr = A[-1, :-1]}.
$$

```python
A = np.corrcoef(trials, rowvar=False)   # (P+1) x (P+1)
corr = A[-1, :-1]                        # outcome-row vs each parameter column
```

*(Verified: `A[-1,:-1]` equals `scipy.stats.pearsonr` per column to $4\times10^{-16}$,
and equals the on-disk `sensitivity_correlations.csv` to $10^{-16}$.)*

### 4.3 What it says for your run

Ranked by $|r_p|$ (from `sensitivity_correlations.csv`):

| parameter | $r_p$ | why the sign is right |
|---|---|---|
| `s` | $-0.648$ | linear **loss** term $-s\,x$ — more loss ⇒ less decoherence |
| `pt` | $+0.414$ | longer pulse pumps more generation $g$ |
| `I_ph` | $+0.310$ | $g\propto I_{\text{ph}}$ |
| `f_01` | $+0.257$ | $\mathrm{DG}\propto\sqrt{f_{01}}$ |
| `vtrans` | $+0.223$ | crystal phonon transport (upstream QP yield) |
| `setTopThickness` | $-0.204$ | $g\propto 1/d$ |
| `n_cooper` | $-0.105$ | $g\propto 1/n_{\text{cooper}}$ |
| `r` | $+0.023\approx0$ | quadratic recomb. negligible at small $x$ |

Every ODE parameter lands with the sign its equation predicts — strong evidence
the pipeline is wired correctly. The four ODE/QPDE parameters dominate because
they scale $\mathrm{DG}$ directly; the swept G4CMP physics parameters
(`vtrans`, thicknesses, …) enter upstream through QP production and correlate
more weakly.

---

## 5. Method 2 — per-channel $\chi^2$ vs experimental delay data (faithful `In[3]`)

Here the outcome is a **goodness-of-fit** of each simulated curve to Paul's real
measured decoherence-vs-delay data.

### 5.1 Experimental curves

For each measured qubit $k\in\{0,\dots,5\}$, two files (during-pulse + after) are
concatenated and the delay axis is shifted so the earliest point sits at 0
(exactly as `In[3]`):

$$
x_k = \bigl[x^{\text{dur}}_k \,\Vert\, x^{\text{aft}}_k\bigr]-\min x^{\text{dur}}_k,
\quad
y_k = \bigl[y^{\text{dur}}_k \,\Vert\, y^{\text{aft}}_k\bigr],
\quad
\delta y_k = \bigl[\delta y^{\text{dur}}_k \,\Vert\, \delta y^{\text{aft}}_k\bigr].
$$

Each file is 3 rows: $x_k$ = delay [µs], $y_k$ = measured $\Delta\Gamma$ [MHz],
$\delta y_k$ = error. The **150 µs** dataset is used because it matches this
run's default ODE pulse time $p_t=150\,\mu s$.

### 5.2 The $\chi^2$ per channel

A "channel" $c$ pairs an electrode $e_c$ with a target qubit $k_c$. For each
experimental delay point $x_{k_c}[j]$ we read the simulated curve at the
**nearest simulated time** $t^*_j=\arg\min_t|x_{k_c}[j]-t|$ and sum squared
residuals:

$$
\boxed{\;
\chi^2_{i,c} \;=\; \sum_{j} w_j\,\bigl(\mathrm{DG}_{i,e_c}(t^*_j) - y_{k_c}[j]\bigr)^2,
\;}
\qquad
w_j=\begin{cases}1 & \text{none (default, what Paul ran)}\\[2pt] 1/y_{k_c}[j] & \text{\texttt{y}}\\[2pt] 1/\delta y_{k_c}[j]^2 & \text{\texttt{dy2}}\end{cases}
$$

```python
idx    = np.array([np.argmin(np.abs(xj - sim_t)) for xj in x_data])
resid2 = (sim_dg[idx] - y_data) ** 2
return float(np.sum(resid2))              # normalize="none"
```

*(Verified identical to a literal transcription of Paul's `In[3]` inner loop to
machine precision, all channels.)*

### 5.3 Channel definitions (`--chi2-channels`)

Only **6** experimental curves exist, so the number of independent $\chi^2$
targets is bounded by the data, not by the 17 electrodes.

- **`electrodes` (default, 17 channels):** every electrode $e$ is scored against
  the curve of its nearest measured qubit,
  $k_c=\arg\min_k \lVert(q^x_e,q^y_e)-(q^{x,\text{Paul}}_k,q^{y,\text{Paul}}_k)\rVert$.
  The 6 curves are **reused by proximity** — these are 17 simulated electrodes
  vs 6 measured references, *not* 17 independent fits.
- **`qubits` (6 channels):** Paul's literal reproduction — each measured qubit
  vs its single nearest electrode $\{6,0,7,1,8,2\}$ (the $y=\pm2$ rows).

### 5.4 Correlation, same machinery

Stack $[\Theta^{\text{usable}} \mid X]$ with $X$ the $n\times C$ block of
per-channel $\chi^2$ ($C=17$ or $6$), take `np.corrcoef`, and read the
channel-rows against the parameter-columns:

$$
\rho_{c,p} = A_{P+c,\;p}
\quad\Longleftrightarrow\quad
\texttt{corr = A[n\_params:, :n\_params]}\ \in\mathbb{R}^{C\times P}.
$$

Each channel $c$ becomes one line in the `In[5]`-style plot; parameter $p$ is one
x-tick.

### 5.5 Read this one with care — the scale caveat

This run used $10^5$ events with a **localized** `phonon_Caustic` injection, so
simulated $\mathrm{DG}$ (median peak $\sim0.5$ MHz, up to tens of MHz,
concentrated on whichever electrode the phonons strike) dwarfs the experimental
$\Delta\Gamma$ ($\sim0.01$ MHz). Expanding the square,

$$
\chi^2_{i,c}
= \sum_j \mathrm{DG}_{i,e_c}(t^*_j)^2
- 2\sum_j \mathrm{DG}_{i,e_c}(t^*_j)\,y_{k_c}[j]
+ \sum_j y_{k_c}[j]^2
\;\approx\; \sum_j \mathrm{DG}_{i,e_c}(t^*_j)^2 ,
$$

because the cross- and experimental terms are $\sim10^2$ smaller. Measured
ratio $\chi^2/\sum \mathrm{DG}^2 = 0.999$. So **the $\chi^2$ here is effectively
the squared magnitude of the simulated response** sampled at the experimental
delays — a faithful reproduction of Paul's construction, but read it as *"which
parameters drive the simulated response at that electrode,"* not as a literal
calibration to experiment. Consequences:

- correlations are weak ($\max|\rho|\approx0.07$) and per-electrode-noisy,
  because a localized injection lights up only one or two of the 6/17 channels
  per sample;
- `none` and `dy2` weightings give nearly identical rankings (the weighting
  barely matters when the residual is dominated by $\mathrm{DG}^2$);
- the top parameters (`s`, thicknesses, `pt`, …) echo Method 1, as expected
  since both ultimately track $\mathrm{DG}$ magnitude.

---

## 6. How to read each output file

| File | Method | What each axis/line is |
|---|---|---|
| `sensitivity_correlations.png` | 1 | one line; x = 53 parameters, y = $r_p$ vs $\log_{10}$(integrated $\Delta\Gamma$) |
| `sensitivity_correlations.csv` | 1 | parameters ranked by $|r_p|$ |
| `sensitivity_corr_matrix.png` | 1 | the $54\times54$ matrix $A$ (params + outcome); last row/col = outcome |
| `sensitivity_chi2_correlations.png` | 2 | one line per channel; x = 53 parameters, y = $\rho_{c,p}$; legend `elec e (Qk)` = electrode ← target qubit |
| `sensitivity_chi2_correlations.csv` | 2 | rows = parameters, columns = `corr_elec_e_Qk`, plus `mean_abs_correlation`, ranked |
| `sensitivity_chi2_corr_matrix.png` | 2 | the $(53+C)\times(53+C)$ matrix; bottom-left block = $\rho$ |

In the matshow, the near-white off-diagonal among the first 53×53 entries is a
good sign: the Morris design samples parameters **independently**, so parameters
are mutually uncorrelated and each $r_p$ is a clean one-parameter readout.

---

## 7. Correspondence to Paul's messy notebook

| Paul (`SensitivityAnalysis.ipynb`) | Here | Note |
|---|---|---|
| `SobolSequence.csv` | `MorrisSequence.csv` | design matrix $\Theta$ |
| 6 hard-coded qubit locations | 17 electrodes; nearest-qubit mapping | `--chi2-channels` |
| `Qubit_(5-j)` ↔ curve `j` (reversed) | `Qubit_k` ↔ curve `k` | corrected copy/paste slip |
| `#/y0_data[j]` (commented) | `--chi2-normalize {none,y,dy2}` | default `none` = what he ran |
| `np.corrcoef(trials)` | identical | extraction `A[-1,:-1]` / `A[n_params:,:n_params]` |
| `/Users/paulbaity/...` Mac paths | derived from `--results-dir` / `--experimental-dir` | no hard-coded paths |

---

## 8. Limitations (so you know the boundaries)

1. **Linear, monotonic readout.** Pearson $r$ only sees linear association; a
   parameter with a strong non-monotonic (e.g. U-shaped) effect can show
   $r\approx0$. For the formal Morris elementary-effects indices ($\mu^\*,\sigma$)
   computed from this same design, ask — that is a different, complementary tool.
2. **Signal-only subset.** Correlations use the 1501 samples with non-zero
   decoherence; they describe sensitivity *among samples that decohere at all*.
3. **$\chi^2$ is magnitude-dominated** (Section 5.5): not a literal experimental
   calibration in this run's injection/statistics regime.
4. **Reused experimental targets** in `electrodes` mode: 17 channels share 6
   measured curves by proximity.
