# Native-material orientation search

Completed 2026-10-30 at 03:07 EDT: 39 studies, 1,449,984 new transport tasks and 45.312 billion primaries. Elapsed time was 36.4 hours including preservation. All 18 search arms reached their targets; 27 distinct designs were confirmed on four unused layouts. All 39 summary checksums and both copies of every study archive were reverified on October 30.

The response $J$ is area- and junction-weighted QPs per injected eV. Temperature was 20 mK, source energy 10 meV, and the film stack Nb/Cu. Density, lattice, elasticity and phonon rates remained coupled to each substrate. Only the three crystal-orientation coordinates varied. With normalized coordinates $u\in[0,1]^3$, the map is $(\psi,\mu,a)=(359^\circ u_1,2u_2-1,u_3)$: $\psi$ is the in-plane angle, $\mu=\cos\theta$ specifies the cut normal, and $a=\phi/(2\pi)$ its azimuth. The omitted final degree does not permit an exact global-minimum claim.

Each of three search seeds shared 12 measured starting orientations between BO and Sobol, followed by 16 new responses per method. Screening used 512 positions; independent confirmation used 1,024 positions. Both used four transport realizations and 31,250 primaries per task. Confirmation data were excluded from selection.

Let $g_s=1-\overline J_{s,\mathrm{selected}}/\overline J_{s,\mathrm{initial}}$, where each mean uses the four confirmation layouts and $s$ denotes the search seed. Positive $g_s$ means improvement.

| Material | Method | Confirmed mean J [QP/eV] | Mean added reduction |
|---|---|---:|---:|
| Si | BO | 0.00237321071 | +1.03% |
| Si | Sobol | 0.00239595253 | +0.08% |
| Ge | BO | 0.00258210121 | +0.11% |
| Ge | Sobol | 0.00259401241 | -0.35% |
| GaAs | BO | 0.00234816901 | -1.59% |
| GaAs | Sobol | 0.00233708151 | -1.12% |

None of the BO-versus-Sobol intervals excludes zero. They use paired search seeds and 98.33% intervals, adjusted across the three substrates. These comparisons condition on four measured confirmation layouts; three search seeds do not establish a general algorithm ranking. Individual paired layout intervals are retained in the numerical record. Negative confirmed gains show that selecting a lower screening response need not improve the independent spatial integral.

## Fixed native-material comparisons

For layout $l$, define $d_l=J_{A,l}/J_{B,l}-1$. The table gives the mean of eight paired layout ratios. These are not ratios of pooled means. The five predeclared contrasts use 99% Student-t intervals, giving simultaneous coverage of at least 95%.

| A / B | Mean response change | Simultaneous interval | Within +/-5% |
|---|---:|---:|---|
| Si-0deg / Si-45deg | +0.25% | [-3.94%, +4.44%] | Yes |
| Ge-0deg / Ge-45deg | -0.29% | [-2.57%, +1.99%] | Yes |
| GaAs-0deg / GaAs-45deg | +0.64% | [-1.58%, +2.87%] | Yes |
| Ge-0deg / Si-0deg | +8.79% | [+5.66%, +11.92%] | No |
| GaAs-0deg / Si-0deg | -1.39% | [-4.31%, +1.52%] | Yes |

The 0/45-degree native pairs agree within 5%. Ge at 0 degrees gives at least 5% higher response than Si under this stack and source model. GaAs and Si at 0 degrees agree within 5%. The earlier 30.22% rotation reduction applies to a different combined property target; it is not a demonstrated native-material benefit.

## Spatial precision and next comparison

Balanced 512-position subsets and their full 1,024-position outputs agreed within 5% in all six cases across eight layouts, using six adjusted intervals. This supports the reallocated sampling for those fixed cases; it is not convergence over all orientations. The separate [2,048-site verification](spatial_2048.md) has only four layouts and wider adjusted intervals.

Across the six native benchmarks, relative standard errors over layouts were 0.68-1.07%, while transport standard errors were 0.35-0.46%. These finite-sample estimates suggest that further independent positions are more useful than more primaries at the same positions. All six descriptive half-primary-count comparisons changed by less than 0.65%, with individual 95% intervals inside approximately +/-2.4%; they are not simultaneous guarantees over the orientation domain.

1. Keep 1,024-site confirmation and require independent confirmation of gains. Do not extend all studies to 2,048 sites from these results.
2. Test two independent 512-position layouts at 15,625 primaries per task against one 512-position layout at 31,250. At four realizations, both use 64 million primaries per response. Keep common layouts across methods and separate unused confirmation layouts. Measure elapsed time as the two-layout choice doubles the number of tasks.
3. Introduce design variables with measured physical bounds, particularly film and interface properties suggested by the earlier sensitivity results. Verify that each variable changes the intended transport input; do not replace a native material by unrelated elastic constants or invented lifetimes. Start with Si and GaAs for this stack and retain Ge as a reference.
4. Compare BO with the same starting data and response count as Sobol, and declare the meaningful confirmed gain before running, for example 5%. The GP already embeds the cut normal as a unit vector. If orientation remains an input, test a representation that also respects the in-plane periodicity and crystal-equivalent rotations; this is secondary to finding measurable physical variation.
5. Use LLM assistance for source and unit checking if useful. Earlier AI-assisted searches demonstrated no added gain, and these native studies performed no new inference.

These results demonstrate optimization of a physical response in the model. They establish neither an identified new material, a fabrication optimum, a global orientation minimum nor a reduction in qubit error. Film lifetimes and interface fields retain model assumptions.

[Numerical estimates, per-seed results, physical inputs and archive identities](native_optimization.json). Per-site arrays remain in two verified archive copies; the public JSON retains estimates and uncertainty without duplicating those arrays.
