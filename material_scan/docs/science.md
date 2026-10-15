# Scientific definition

## Quantity being minimized

For material properties `x ∈ X ⊂ R^d`, injection position `ξ` in the declared
surface region, transport realization `ω`, and junction region `e`, let
`Q_e(x, ξ, ω)` be junction quasiparticles per primary phonon. The quantity
being minimized is

\[
J(x)=\mathbb E_{\xi,\omega}\left[
  \frac{1}{E_{\rm gun}}\sum_e w_e Q_e(x,\xi,\omega)
\right],\qquad w_e\geq0,\quad\sum_e w_e=1.
\]

`E_gun` is the source energy in eV; `J` has units weighted junction QPs per
injected eV. The reference uses a 10 meV phonon gun. Equal junction weights
are used unless measured device importance supports another fixed choice.
For a junction hit of deposited energy `E_k`, the scorer retains
`q_k = round(E_k / Δ_Al)`, with `Δ_Al = 191 μeV` in the reference. The historical
`total_qps_per_primary` response sums over junctions without energy
normalization; it is a separately named quantity.

This is junction quasiparticle yield under the stated injection model. It is
not a logical-error probability. Such a claim would require a calibrated
time-dependent conversion from local quasiparticle density to qubit error.

The program also reports

\[
R_{0.95}=\operatorname{CVaR}_{\xi,0.95}
\left[\mathbb E_\omega\left[\max_e Q_e/E_{\rm gun}\right]\right],
\]

the mean worst-junction burden among the upper five percent of spatial area.
The calculation averages each site's finite-primary measurements over
realizations before taking the area-weighted spatial tail. It is not the
upper tail of individual primary-event QP counts.
There is no independently justified upper limit yet, so this remains a second
Pareto quantity. Enforcing a limit chosen from the same results would be an
unregistered, result-dependent choice.

## Spatial integral

Uniform injection over the declared surface region is the present assumption.
The substrate is 10 × 10 mm², spanning `[-5, 5] mm` in both surface coordinates.
The recorded designs instead inject over `[-4, 4] mm × [-4, 4] mm`, an
8 × 8 mm² region. Region weights refer to this 64 mm² injection area, not the
100 mm² chip area. For example, the seventeen junction rectangles occupy
`17 × (0.010 mm)^2 / (8 mm)^2 = 0.0000265625` of the injection region.

This region is split by distance from the exact 10 µm by 10 µm junction
rectangles:

The following 128-site example is historical; it is not the revised queue's
128-site allocation.

| Region | Area fraction | Historical 128-site example |
|---|---:|---:|
| inside an electrode rectangle | 0.0000266 | 19 |
| within 0.05 mm of an electrode | 0.002637 | 23 |
| 0.05–0.20 mm away | 0.032862 | 28 |
| 0.20–0.50 mm away | 0.178481 | 22 |
| remaining injection area | 0.785995 | 36 |

The revised queue uses these explicit counts, in the same region order:

| Sites | Junctions | 0–0.05 mm | 0.05–0.20 mm | 0.20–0.50 mm | Bulk |
|---|---:|---:|---:|---:|---:|
| 512 | 68 | 64 | 128 | 128 | 124 |
| 2,653 | 68 | 64 | 256 | 415 | 1,850 |
| 3,041 | 136 | 128 | 512 | 415 | 1,850 |

The 2,653-site design appends to the fresh queue's recorded 2,361 sites; the
3,041-site design appends to those 2,653 sites. Existing positions and area
weights remain unchanged. This sequence is separate from the inherited
historical layouts and from the revised 512-site search layout. Independent
position layouts use their own position seeds and the same selected counts and
area weights; they do not retain a previous layout's prefix.

Near electrodes are deliberately oversampled. If region `h` has true area
fraction `W_h` and mean response `μ_h`, the estimator is

\[
\widehat J=\sum_h W_h\widehat\mu_h.
\]

An equal average over all sites is wrong because it gives the oversampled
electrode regions too much physical area. Candidate and reference comparisons
use the same sites, seeds, event count, and region weights.

Spatial convergence applies to the recorded injection region and model.
The later inherited 2,361-site convergence report uses `half_span_mm = 4`
and the same area weights. It does not establish an integral over the outer
36 mm² of the chip. A whole-chip source assumption requires new sites, new
weights, and its own convergence check.

No site is placed at an electrode centre merely to ensure coverage. A pilot
showed that exact-centre injection produced 43 times the response of another
point in the same footprint and increased the estimated on-electrode mean by a
factor of 24. Each footprint instead receives a uniform interior point. The
corrected mean, `4.80e-3`, agrees with the independent earlier-data estimate,
`4.58e-3`. These pilot observations retain their historical source and scoring
definitions.

## Source-position and transport uncertainty

Let `k = 1,…,K` index independent position layouts, `r = 1,…,R` complete
transport realizations, and `i` a site in region `h`, with `n_h` sites per
region in each layout. Write `Q_kire` for junction `e`'s QPs per primary at
that site and realization. The area-weighted realization and layout means are

\[
J_{kr}=\sum_h\frac{W_h}{n_h}\sum_{i\in h}
       \frac{\sum_e w_e Q_{kire}}{E_{\rm gun}},\qquad
\bar J_k=\frac1R\sum_r J_{kr},\qquad
\bar J=\frac1K\sum_k\bar J_k.
\]

For sample standard deviation `s`, the two standard errors have different
domains:

\[
\operatorname{SE}_{\rm layout}=\frac{s_k(\bar J_k)}{\sqrt K},\qquad
\operatorname{SE}_{{\rm transport},k}=\frac{s_r(J_{kr})}{\sqrt R}.
\]

The first includes layout variation and finite transport noise. The second
conditions on layout `k` and measures transport noise alone. More primaries
can reduce transport noise while leaving spatial integration error unresolved.
The revised queue uses four independent layouts and a Student-t 95% interval
`bar J ± t_(0.975,K−1) SE_layout`, with relative precision
`SE_layout / |bar J| ≤ 0.02`.

The independent-layout mean must also agree with the selected retained-layout
estimate `J_ret`, whose hierarchical standard error is `SE_ret`. Define

\[
\Delta_{\rm pos}=\bar J-J_{\rm ret},\qquad
\operatorname{SE}_{\rm pos}=\sqrt{
  \operatorname{SE}_{\rm layout}^2+\operatorname{SE}_{\rm ret}^2}.
\]

Its entire 95% interval
`Δ_pos ± t_(0.975,3) SE_pos` must lie within
`(−0.05 |bar J|, +0.05 |bar J|)`. The three-degree-of-freedom critical value
is conservative for the combined standard error. Region mean shifts are also
reported. All these checks hold `W_h` fixed; they support statistical equivalence
of the integral approximations under the model and do not prove absence of
systematic bias.

For a nested coarse/fine comparison, the site bootstrap retains shared-site
covariance and each region's own site-count denominator. With
`ΔJ = J_fine − J_coarse`, the full 95% interval must lie inside
`(−0.05 J_fine, +0.05 J_fine)`. Each region that gains sites must also have
its mean-change interval inside `±0.05 |μ_fine,h|`. A small or unresolved
point difference alone does not pass these tests. Nested intervals describe
that retained-layout contrast; the independent-layout precision check supplies
separate evidence about position integration. Source validation stops the
queue before later expensive studies if the required checks fail.

## Crystal direction

The former search selected one of 13 integer directions. The current search
uses two continuous coordinates on the unit sphere:

- `orientation_polar_cos = cos(θ)` in `[-1, 1]`;
- `orientation_azimuth_turns = φ/(2π)` in `[0, 1)`.

Uniform draws in these two coordinates are uniform in sphere area. Azimuth
zero and one are the same direction, and azimuth is irrelevant at either pole.

The installed G4CMP function accepts three integers, not three real numbers.
Passing real coordinates directly would truncate them. Before writing a macro,
the code therefore converts the unit vector to a primitive integer triple at a
scale of 1,000,000. Its worst angular rounding is below about `2e-4` degrees and
every component remains far inside a signed 32-bit integer. The original two
sphere coordinates remain the searched and recorded values.

This interpretation is valid for the cubic Stage-4 lattice: its reciprocal
basis is proportional to the Cartesian basis. A later non-cubic material must
convert a plane normal through that material's reciprocal lattice rather than
reuse this cubic shortcut.

## Bayesian covariance function

The Gaussian process embeds the two sphere coordinates as

\[
(x,y,z)=(\sqrt{1-z^2}\cos\phi,
         \sqrt{1-z^2}\sin\phi,
         z).
\]

Its Matérn-5/2 covariance uses one fitted length scale for equatorial motion
and one for polar motion. Other physical inputs retain independent fitted
length scales. This closes the azimuth seam and makes azimuth changes have zero
distance at a pole. Analytic derivatives of both sphere length scales are
checked against finite differences.

## Physical validity checks

Candidates are rejected before Geant4 when any of these conditions fails:

- cubic stability: `C44 > 0`, `C11 - C12 > 0`, and
  `C11 + 2 C12 > 0`;
- positive density, sound speeds, and lifetimes; non-negative scattering and
  decay constants within their declared ranges;
- transverse sound speed below longitudinal sound speed;
- every interface probability inside `[0, 1]`;
- the numerical phonon cutoff below the junction pair-breaking threshold;
- the source energy at least the junction pair-breaking threshold;
- a real material has a complete lattice record and a representable measured
  Geant4 density;
- the modeled upper-film gap is at least `1.5384e-3 eV` for a
  niobium-compatible search.

Temperature should be fixed at the intended operating temperature in the next
search. It is an operating condition, not a material design coordinate.

The interface absorption model rescales the Si reference absorptions by an
effective acoustic-impedance transmission ratio and clips probabilities to
`[0, 1]`. Each clipped value is an individual boundary/branch absorption
probability; these values are not a normalized set whose sum is asserted to
equal one. The model preserves the calibrated baseline; it is not an
independently validated film-interface calculation.

The existing model derives representative longitudinal and shear speeds from
the elastic tensor and density. With direction `n` on the sphere,
`v_L = ⟨v_l(n)⟩` and `v_S = ⟨[v_t1(n)+v_t2(n)]/2⟩`; the averages use 10,000
Fibonacci directions. These replace the native record's scalar
`vsound/vtrans`, even for linked Si, Ge and GaAs:

| Substrate | Derived `v_L / v_S` (m/s) | Native `vsound / vtrans` (m/s) |
|---|---:|---:|
| Si | 9,016.675 / 5,369.509 | 9,000.000 / 5,400.000 |
| Ge | 5,269.519 / 3,234.400 | 5,324.208 / 3,258.788 |
| GaAs | 5,217.598 / 2,990.253 | 5,130.000 / 3,020.000 |

Thus linked-material results retain this sound-speed modeling choice rather
than reproducing every native scalar input. Native Si, Ge and GaAs keep their
own complete lattice records. The pseudo-material search and the SiC surrogate
instead borrow Si's `dyn` anharmonic fields and branch density-of-states
fractions. Replacing stiffnesses and selected rates does not supply a complete
measured lattice for the proposed material.

## Time-dependent response

The optional postprocessing evolves the dimensionless quasiparticle density
`x_qp` by the retained explicit Euler equation

\[
\frac{d x_{\rm qp}}{dt}=-r x_{\rm qp}^2-s x_{\rm qp}+g(t),
\]

where `t` is in μs, `r` and `s` are the stated recombination and loss
coefficients, and `g(t)` comes from time-binned junction QPs and the stated
pulse, volume, Cooper-pair density, and primary count. The reported rate is
`Γ = 2 sqrt(2 (Δ_Al/h) f_01) x_qp`, converted to MHz. The legacy name
`integrated_density` denotes `∫ sum_e Γ_e(t) dt`, in MHz μs; it is not a
volume density. Aggregated nonlinear evolution and per-realization evolution
are reported separately. This model needs experimental calibration before
interpreting its rate as measured qubit decoherence.

## Material projection

The continuous search produces a property target, not necessarily a real
compound. Projection to fabrication candidates must:

1. compare only measured fields;
2. state which fields are missing for each material;
3. use the material's own density and complete lattice record;
4. bracket unmeasured scattering and decay constants;
5. simulate the shortlisted real material with the same spatial design;
6. avoid calling nearest elastic constants a fabricated optimum.

For SiC, sourcing its scattering coefficient, total decay coefficient, and
transverse-transverse decay fraction has greater scientific value than simply
adding more events with assumed silicon values.
