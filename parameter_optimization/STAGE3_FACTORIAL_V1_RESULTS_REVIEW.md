# Stage 3 factorial-v1: results interpretation and implementation audit

Date reviewed: 2026-08-13

## Executive conclusion

The recorded `stage3_factorial_v1` campaign is technically complete and internally reproducible. All 18 material triplets completed, all 576 planned sub-runs are present and successful, the macros and native lattice records match the resolved candidates, and rescoring the hit files reproduces the SQLite totals exactly. Re-running `stage3_report.py` also reproduces `results/factorial_v1.csv` byte-for-byte.

The campaign is a useful screening experiment, but it is not yet a defensible final material selection. Its strongest robust signal is that every Cu-bottom candidate beats the corresponding Au-bottom candidate in this model. The nominal winner is `GaAs/Nb/Cu`, 39.5% below the `Si/Nb/Cu` baseline, but it is only 1.9% below `Ge/Nb/Cu`. That first-versus-second ordering is not resolved by the present design.

The main reason is spatial quadrature, not merely event count. Sobol position 11 lies at approximately `(-1.882, -0.015) mm`, almost directly below electrode 3 at `(-2, 0) mm`. It contributes 782 of the baseline's 1578 QPs (49.6%). Electrode 3 itself receives 760 QPs. A single uniformly weighted site therefore controls roughly half the result. The planned nested 16/32/64-position convergence test remains mandatory before material selection.

There are also two implementation blockers before new campaigns are trusted: the cache does not include all material/interface/template inputs, and the report is not campaign-filtered. These do not invalidate the already recorded nominal campaign because its generated macros and results were audited directly, but they can silently contaminate lifetime-bracket or future-campaign runs.

## What was actually run

The command-line overrides produced the following design:

| Quantity | Actual value |
|---|---:|
| Candidate space | 3 substrates × 3 top films × 2 bottom films = 18 |
| Substrates | Si, Ge, GaAs |
| Top ground films | Nb, Ta, Ti |
| Bottom films | Cu, Au |
| Primary events per candidate | 4,000,000 |
| Injection sites | 16 scrambled-Sobol positions |
| Replicas | 2 |
| Sub-runs per candidate | 32 |
| Events per sub-run | 125,000 |
| Total sub-runs | **576**, not 288 |
| Total primary events | 72,000,000 |
| Workers | 32 within each candidate |
| Gun energy | **10 meV** |
| Tracking cutoff | **38.2 µeV** |
| Scored junction gap | 191 µeV |

The earlier statement of 288 sub-runs is inconsistent with both the contract and the database. Eighteen candidates times 16 sites times two replicas is 576.

## Objective and physics meaning

The objective is the number of quasiparticles generated at the 17 fixed Al junction electrodes per simulated primary:

\[
J = \frac{1}{N_{\mathrm{primary}}}
    \sum_{h\;\mathrm{on\;top\;junctions}}
    \operatorname{round}\left(\frac{E_{\mathrm{dep},h}}{\Delta_{\mathrm{Al}}}\right),
\qquad \Delta_{\mathrm{Al}}=191\;\mu\mathrm{eV}.
\]

Each positive-energy hit on the top surface is assigned to the nearest electrode; the objective sums all time bins, all electrodes, all 16 sites, and both replicas. It is therefore a spatially averaged junction-QP yield for this fixed source distribution. It is not yet a logical-error probability, a peak per-qubit burden, or a time-resolved correlated-error metric.

The simulated physical chain is:

1. A 10 meV athermal phonon is injected near the upper surface of the 525 µm substrate.
2. The candidate substrate's native G4CMP tensor, anharmonic decay, isotope scattering, density, Debye scale, and derived sound speeds control propagation and focusing.
3. Candidate-aware effective interface probabilities control whether phonons enter the Al junctions, top ground film, or bottom film.
4. Absorption and downconversion in the films depend on film gap, sound speed, thickness, and phonon-lifetime parameters.
5. Energy deposited at the Al junction footprints is converted to the QP count above.

### Energy regime

The actual inequality is

\[
38.2\;\mu\mathrm{eV} < 2\Delta_{\mathrm{Al}}=382\;\mu\mathrm{eV}
< E_{\mathrm{gun}}=10{,}000\;\mu\mathrm{eV}.
\]

The top-film pair-breaking thresholds are about 122 µeV for Ti, 1400 µeV for Ta, and 3076.8 µeV for Nb. Thus 10 meV is above all three: all top films are active competing absorbers. The `/main/sensor/setHitType Junction` filter keeps the recorded objective junction-only, but ground-film absorption still changes which phonons reach the junctions. This is a deliberate high-energy comparison regime and is not the former 1 meV protocol.

## Complete ranking

Lower is better. Percentages are relative to the `Si/Nb/Cu` baseline.

| Rank | Candidate | Total QPs | QPs/primary | Versus baseline |
|---:|---|---:|---:|---:|
| 1 | GaAs/Nb/Cu | 954 | 2.385e-4 | -39.5% |
| 2 | Ge/Nb/Cu | 972 | 2.430e-4 | -38.4% |
| 3 | Ge/Ti/Cu | 1142 | 2.855e-4 | -27.6% |
| 4 | Ge/Ta/Cu | 1150 | 2.875e-4 | -27.1% |
| 5 | GaAs/Ti/Cu | 1276 | 3.190e-4 | -19.1% |
| 6 | GaAs/Ta/Cu | 1502 | 3.755e-4 | -4.8% |
| 7 | Si/Nb/Cu | 1578 | 3.945e-4 | baseline |
| 8 | Si/Ti/Cu | 1608 | 4.020e-4 | +1.9% |
| 9 | Si/Ta/Cu | 1800 | 4.500e-4 | +14.1% |
| 10 | Ge/Ti/Au | 1968 | 4.920e-4 | +24.7% |
| 11 | GaAs/Ti/Au | 2266 | 5.665e-4 | +43.6% |
| 12 | Ge/Nb/Au | 2318 | 5.795e-4 | +46.9% |
| 13 | Ge/Ta/Au | 2506 | 6.265e-4 | +58.8% |
| 14 | GaAs/Nb/Au | 2768 | 6.920e-4 | +75.4% |
| 15 | Si/Ti/Au | 2950 | 7.375e-4 | +86.9% |
| 16 | GaAs/Ta/Au | 3110 | 7.775e-4 | +97.1% |
| 17 | Si/Nb/Au | 3600 | 9.000e-4 | +128.1% |
| 18 | Si/Ta/Au | 4160 | 1.040e-3 | +163.6% |

## What can be inferred

### Bottom film is the strongest observed factor

The marginal mean is 3.328e-4 QPs/primary for Cu and 7.124e-4 for Au: Au is 2.14 times higher on average. More importantly, Au is worse in all nine matched substrate/top-film comparisons; the Au/Cu ratio ranges from 1.72 to 2.90. This sign consistency makes `Cu > Au` the most stable result in the current factorial.

This is still a linked-material result, not proof that one scalar causes the change. Moving from Cu to Au changes interface absorption, sound speed, density, and phonon lifetime together. In the implemented effective-interface model, Au's `setBotAbs` is substantially lower than Cu's for every substrate; Au also uses a longer, presently unsourced lifetime. Either or both can alter how energy returns to the substrate and reaches the junctions. Lifetime brackets and interface-model uncertainty are needed before assigning causality.

### Substrate has a large effect, but GaAs versus Ge is unresolved

Marginal means are:

| Substrate | Mean QPs/primary | Relative to Ge |
|---|---:|---:|
| Ge | 4.190e-4 | best |
| GaAs | 4.948e-4 | +18.1% |
| Si | 6.540e-4 | +56.1% |

Ge beats Si in all six matched film combinations; GaAs also beats Si in all six. These comparisons include each substrate's density, tensor-derived speeds, complete native lattice record, and all three recomputed interfaces. They do not identify which substrate property is causal.

The particular nominal winners `GaAs/Nb/Cu` and `Ge/Nb/Cu` differ by only 18 QPs out of roughly 960 (1.9%). Their ordering should be treated as a tie until the spatial-convergence and confirmation runs are complete.

### Top-film effects interact with substrate and bottom film

The marginal ordering is Ti (4.671e-4), Nb (5.079e-4), Ta (5.928e-4), but it is not universal. Nb wins for all Cu-bottom substrates, while Ti wins for all Au-bottom substrates. This is a real interaction in the simulated model: there is no context-free statement that one top film is best.

Again, a top-film choice moves its gap, sound speed, lifetime, density, and interface probability together. The factorial ranks realizable linked candidates; it does not provide independent sensitivity coefficients for those scalar properties.

## Statistical and numerical limits

### Do not interpret the printed Poisson z values as confidence levels

The report's `z = difference/sqrt(count_1 + count_2)` assumes independent Poisson QPs. Here, QPs arrive in clustered phonon cascades, candidate results share spatial sites and nominal seed banks, and site-to-site heterogeneity is extreme. Two replicas are also insufficient to estimate a stable between-replica variance. The z column is a rough count-scale diagnostic only.

The two replica totals differ by as much as -23.1% to +16.3% across candidates. Matched-site comparisons are highly correlated with the baseline (correlations 0.91-0.99), which helps comparisons, but does not solve the spatial quadrature error.

### The 16-site quadrature is visibly under-resolved

For `Si/Nb/Cu`, the QPs summed over both replicas by position are:

`[32, 42, 58, 14, 94, 96, 16, 20, 24, 16, 22, 782, 78, 122, 92, 70]`.

Position 11 alone contributes 49.6%. It is only about 0.119 mm from electrode 3, explaining the sharp direct-hit contribution. At 16 uniformly weighted positions that neighborhood receives weight 1/16, much larger than its physical area fraction. The 32- and 64-site nested Sobol sets are needed to see whether this peak integrates stably and whether GaAs and Ge exchange rank.

### Recommended statistical reporting for confirmation

Keep position and replica identities rather than reducing immediately to one count. Report:

- candidate-minus-baseline differences at matched `(position, replica)` blocks;
- the objective and rank at nested 16, 32, and 64 positions;
- replica-to-replica dispersion at each position;
- bootstrap intervals over spatial blocks only as a descriptive diagnostic, not as a substitute for convergence;
- ranking stability under low/nominal/high film-lifetime brackets and at least one alternate interface model.

## Implementation audit

### Checks passed

- 18/18 trials have observable `success` status.
- 576/576 sub-runs have `success`, return code 0, a macro, hit file, completion marker, and log.
- Every candidate has exactly 16 positions × 2 replicas × 125,000 events = 4,000,000 events.
- Ordered scenarios and seed maps are identical across candidates.
- Runtime logs report the intended substrate material and density.
- Generated lattice configs equal the candidate's native G4CMP config except for the explicitly derived `vsound` and `vtrans` lines.
- All hit tables have one consistent schema and valid numeric scoring fields.
- Independent rescoring of all hit files exactly reproduces every SQLite `total_qps`.
- `stage3_report.py` regenerates the checked-in CSV byte-for-byte.
- The stored code fingerprint equals the current fingerprint for the files that are presently included.
- Python compilation succeeds for the Stage 3 modules.

### Critical before any new or lifetime-bracket campaign

1. **Complete the cache identity.** `CODE_IDENTITY_FILES` omits `material_catalog.yaml`, `interface_transmission.py`, and the macro template. More seriously, the cache payload's `derived` record omits several resolved film inputs such as numerical phonon lifetime, sound speed, thickness, QP limit, and bottom threshold. Changing Ta lifetime from 0.0227 to 0.040 ns changes the generated macro but leaves the current cache payload unchanged. A low/high lifetime-bracket run can therefore return nominal cached results. Hash the complete resolved candidate record and the exact template/catalog/interface-model files before running brackets or new campaigns.

2. **Filter reports by campaign/contract.** `stage3_report.py` currently calls `ledger.observations()` without a campaign ID. This is safe only while the ledger contains one campaign. Future 32/64-position or lifetime-bracket rows would be mixed into the same table. Add a required or defaulted `--campaign-id`, and ideally filter by contract hash and fidelity as well.

### Important physics/model caveats

1. **Interface probabilities are calibrated, not validated.** The model rescales a normal-incidence scalar acoustic-mismatch transmission to recover 0.795/0.745/0.736 for the Si baseline. Reproducing those anchors tests plumbing by construction. It does not validate angle, polarization, anisotropy, critical-angle, roughness, or mode-conversion physics. The GaAs/Ge ranking is therefore interface-model dependent.

2. **Normal-metal gap threshold is inconsistent with its own documentation.** The catalog says the bottom `gapThreshold` is the Al junction gap, but the simulation uses 180 µeV while `setTopGap` is 191 µeV. The local `G4CMPNormal` source uses this threshold to terminate normal-film downconversion relative to the Al pair-breaking scale. Decide on 191 µeV (recommended for internal consistency) or explicitly justify 180 µeV as a calibrated value, then re-run confirmation. The 5.8% difference is shared across candidates but can interact with Cu/Au downconversion.

3. **Lifetime provenance is incomplete.** Au's 16 ns is explicitly marked `NOT SOURCED`; Ta and Ti are described as supplied values. Treat all three as uncertain model inputs and complete the planned bracket. Do not present the nominal ranking as a materials measurement.

4. **Several catalog comments are stale.** At 10 meV, Ta's 1.40 meV pair threshold is 0.14× the gun energy, not 1.40×; the analogous Pb comment is also stale. The disabled Pb provenance text appears to contain the Au normal-film lifetime note. These comments did not alter the generated macros, but they should be corrected before the catalog is used as a provenance record.

5. **The report is a per-trial projection, not the whole database.** It faithfully rebuilds its 18-row summary from SQLite, but it does not export every ledger field or the 576 sub-run rows. Keep the wording “rebuilds the per-trial summary from SQLite.”

## Go/no-go decision and concrete next sequence

Do not launch Bayesian optimization yet. With only 18 discrete combinations, exhaustive enumeration remains more appropriate than a surrogate; the immediate problem is model and objective convergence, not search efficiency.

1. Archive this run as `factorial_v1_nominal_10meV_16pos_2rep`; do not overwrite it.
2. Fix cache identity and report filtering, then add regression tests that prove a lifetime or interface change produces a new cache key.
3. Resolve 180 versus 191 µeV for `setBotGapThres`; use 191 µeV unless there is an explicit calibration reason not to.
4. Run the nested 32-position set for at least `GaAs/Nb/Cu`, `Ge/Nb/Cu`, `Si/Nb/Cu`, and one Au control. Because Sobol sets are nested, retain and combine the original first 16 only if identity and code changes have not altered the experiment; otherwise rerun all sites under a new contract.
5. Extend the same finalists to 64 positions. Require the objective changes and ordering to stabilize under 16→32→64.
6. Increase replicas for finalists or use additional seed banks; report matched-block uncertainty rather than Poisson z.
7. Run low/nominal/high lifetime brackets after the cache fix.
8. Repeat finalists with a defensible alternate interface treatment or measured interface data. Separate “robust across interface models” from “best under effective AMM.”
9. Confirm the surviving material triplets at the higher event fidelity without changing the site set.
10. Only after the discrete catalog expands beyond what can be enumerated should an optimizer be introduced. For a larger categorical/continuous mixed space, use a cost-aware Bayesian optimizer with categorical kernels or a tree-structured Parzen estimator, failed-trial censoring, common scenario blocks, and mandatory high-fidelity confirmation.

## Literature context used in the presentation

- R. Agnese et al., *G4CMP: Condensed Matter Physics Simulation Using the Geant4 Toolkit*, arXiv:2302.05998 / FERMILAB-PUB-23-065-ND.
- J. M. Martinis, *Saving superconducting quantum processors from decay and correlated errors generated by gamma and cosmic rays*, npj Quantum Information 7, 90 (2021).
- M. McEwen et al., *Resolving catastrophic error bursts from cosmic rays in large arrays of superconducting qubits*, Nature Physics 18, 107-111 (2022).
- C. D. Wilen et al., *Phonon downconversion to suppress correlated errors in superconducting qubits*, Nature Communications 13, 6425 (2022).

