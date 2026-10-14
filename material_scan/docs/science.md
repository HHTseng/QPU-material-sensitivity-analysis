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

| Region | Area fraction | Sites at the 128-site level |
|---|---:|---:|
| inside an electrode rectangle | 0.0000266 | 19 |
| within 0.05 mm of an electrode | 0.002637 | 23 |
| 0.05–0.20 mm away | 0.032862 | 28 |
| 0.20–0.50 mm away | 0.178481 | 22 |
| remaining injection area | 0.785995 | 36 |

The sites are nested when the total is increased from 128 to 256 to 512. Near
electrodes are deliberately oversampled. If region `h` has true area fraction
`W_h` and mean response `μ_h`, the estimator is

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
`4.58e-3`.

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
`[0, 1]`. It preserves the calibrated baseline; it is not an independently
validated film-interface calculation.

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
