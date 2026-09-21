# Scientific definition

## Quantity being minimized

For material parameters `x`, injection condition `ξ`, transport realization
`ω`, and electrode `e`, let `Q_e(x, ξ, ω)` be the quasiparticles delivered to
that electrode. The present computable quantity is

\[
J(x)=\mathbb E_{\xi,\omega}\left[
  \frac{1}{E_{\rm gun}}\sum_e w_e Q_e(x,\xi,\omega)
\right],\qquad \sum_e w_e=1.
\]

The current run has one 10 meV phonon gun and fixed energy, so division by
energy changes every value by the same constant. It is nevertheless explicit
so a later energy spectrum cannot be compared silently with per-primary
values. Equal electrode weights are used unless measured device importance
supports another fixed choice.

This is junction quasiparticle yield under the stated injection model. It is
not a logical-error probability. Such a claim would require a calibrated
time-dependent conversion from local quasiparticle density to qubit error.

The program also reports

\[
R_{0.95}=\operatorname{CVaR}_{0.95}
\left[\max_e Q_e/E_{\rm gun}\right],
\]

the mean worst-electrode burden among the upper five percent of injections.
There is no independently justified upper limit yet, so this remains a second
Pareto quantity. Enforcing a limit chosen from the same results would be an
unregistered, result-dependent choice.

## Spatial integral

Uniform surface injection is the present physical assumption. The device area
is split by distance from the exact 10 µm by 10 µm electrode rectangles:

| Region | Area fraction | Sites at the 128-site level |
|---|---:|---:|
| inside an electrode rectangle | 0.0000266 | 19 |
| within 0.05 mm of an electrode | 0.002637 | 23 |
| 0.05–0.20 mm away | 0.032862 | 28 |
| 0.20–0.50 mm away | 0.178481 | 22 |
| remaining device area | 0.785995 | 36 |

The sites are nested when the total is increased from 128 to 256 to 512. Near
electrodes are deliberately oversampled. If region `h` has true area fraction
`W_h` and mean response `μ_h`, the estimator is

\[
\widehat J=\sum_h W_h\widehat\mu_h.
\]

An equal average over all sites is wrong because it gives the oversampled
electrode regions too much physical area. Candidate and reference comparisons
use the same sites, seeds, event count, and region weights.

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
- positive density, sound speeds, lifetimes, scattering, and decay constants;
- transverse sound speed below longitudinal sound speed;
- every interface probability inside `[0, 1]`;
- the numerical phonon cutoff below the junction pair-breaking threshold;
- the 10 meV gun above that physical threshold;
- a real material has a complete lattice record and a representable measured
  Geant4 density;
- the modeled upper-film gap is at least `1.5384e-3 eV` for a
  niobium-compatible search.

The current material search fixes temperature at `0.020 K`. Temperature is an
operating condition, not a material design coordinate. Lattice constant and
crystal direction are also fixed in the primary 14-property search; direction
can be studied conditionally after material finalists exist.

## Current material search

The varying coordinates are upper- and lower-film speed, gap or threshold,
lifetime, and density; substrate `C11`, `C12`, `C44`, scattering, total decay,
and transverse-transverse decay fraction. The upper-film gap is constrained to
at least `1.5384e-3 eV`. The experimentally supported extensions are
`C11 <= 1100 GPa`, `C12 <= 400 GPa`, `C44 <= 600 GPa`, and
`sub_scat >= 1e-45 s3`.

Each search calculation uses 128 recorded spatial sites, four replicas, and
31,250 source phonons per site-replica task: 16,000,000 source phonons per
candidate. This setting preserved the ordering of the three spatial-check
anchors and put each within 5.3% of its refined value. It is used only to find
promising vectors. Finalists must be repeated with the refined spatial design
and unused random seeds.

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
