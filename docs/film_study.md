# Native film design: measured comparisons

This study separates three questions: does changing the physical design reduce
the junction response, does BO improve beyond simple starting choices, and does
sampling more independent positions improve precision at equal primary count?
The response remains area- and junction-weighted QPs per injected eV.

## Physical inputs

Native Si and GaAs retain their density, lattice, elasticity and phonon rates.
Nb and Cu retain their catalog density, speed, gap and lifetime. The temperature
is 20 mK, injected energy is 10 meV, crystal normal is [001], and in-plane angle
is 45 degrees. The Nb gap is 1.5384 meV.

For normalized inputs $u\in[0,1]^2$, the two thicknesses are

$$
t_t=0.075(0.100/0.075)^{u_1}\ \mu\mathrm m,
\qquad t_b=10^{u_2}\ \mu\mathrm m.
$$

Here $t_t$ denotes Nb thickness and $t_b$ Cu thickness. The Cu island pitch is
$p=250\ \mu\mathrm m$. Its area fraction $f$, island width $a$ and gap $s$ obey

$$
ft_b=0.64\ \mu\mathrm m,\qquad a=p\sqrt f,\qquad s=p-a.
$$

Thus Cu coverage decreases from 64% to 6.4% as thickness increases from 1 to
10 micrometres. Total Cu volume stays at 0.064 mm3 on the 10 x 10 mm2 chip.
The pitch divides both chip dimensions, so edge cells do not change that volume.
Nb volume is allowed to change. The source region remains 8 x 8 mm2.

Experimental 1/10 micrometre Cu islands and 200 micrometre islands with
50 micrometre gaps are reported by
[Yelton et al.](https://doi.org/10.1103/PhysRevB.110.024519).
Fabricated 100 and 200 nm Nb films are described by
[Su et al.](https://doi.org/10.1088/1674-1056/ac11d7) and
[Satrya et al.](https://doi.org/10.1038/s41467-025-58919-8).
These define a chosen design interval, not measured uncertainty. Keeping the
same Nb gap and film lifetimes across thickness is a model assumption.
The retained 200 nm timing check required about 13 times the reference CPU
time, largely from repeated subgap-phonon escape trials in the existing film
model. The main search therefore uses 75-100 nm; this restriction was based on
runtime before optimization, independently of response values.

## Simulation and optimization

| Calculation | Independent groups | Primaries |
|---|---:|---:|
| Single versus paired position layouts, eight fixed cases | 8 pairs; 16 studies | 8.192 billion |
| Shared starts and BO/Sobol on Si and GaAs | 6 search seeds per substrate; 36 studies | 46.080 billion |
| Confirmation, including all fixed corners and selected leaders | 8 unused layouts | At most 22.528 billion |

There are 60 measured studies and one numerical comparison step. The upper
total is 76.8 billion primaries. Identical confirmation designs are measured
once; their names remain as aliases.

Each search seed has 12 common measured starts, including all four thickness
corners, followed by 24 new responses for each method. A corner is permitted to
remain best: changing the physical design does not by itself demonstrate an
added optimizer gain. Native material properties are never varied independently.

Screening uses two independently generated 512-position layouts, four transport
realizations and 15,625 primaries per position and realization. Regional counts
are [17, 256, 51, 85, 103] in each component; area weights are unchanged. Their
combined 1,024-position response is

$$
\widehat J_D=\tfrac12(\widehat J_{D_1}+\widehat J_{D_2}).
$$

This uses 64 million primaries, as does one 512-position layout at 31,250.
The sampling comparison retains the first component's positions and streams.
It changes both spatial sampling and primaries per position; it is not a pure
spatial-convergence test. Sampling results do not choose the optimization method
or alter the predeclared paired search design.

BO and Sobol import the same validated starting file for each seed and substrate.
GP observation noise uses transport variation over four realizations at fixed
positions. The component layouts do not become eight transport realizations.
The eight confirmation layouts and their streams remain unused until selection.

## Meaningful changes and uncertainty

Let $s$ index search seeds and $l$ unused confirmation layouts. For a comparison
of designs A and B, define $d_{sl}=J_{A,sl}/J_{B,sl}-1$. Negative values favor A.
The six primary comparisons are BO/initial winner, Sobol/initial winner, and
BO/Sobol, separately for the two substrates. Their mean change uses

$$
\mathrm{SE}^2=
\frac{\operatorname{Var}_s(\overline d_{s\cdot})}{6}
+\frac{\operatorname{Var}_l(\overline d_{\cdot l})}{8}.
$$

Student-t intervals use five degrees of freedom and confidence $1-0.05/6$.
This conservative approximation counts seed-layout interaction twice; it does
not treat all 48 cells as independent observations. A meaningful reduction
requires the entire interval below -5% and all six planned matched seeds complete.

Physical changes relative to the original Nb/Cu geometry are separate secondary
comparisons, using eight paired layouts and a 44-comparison correction fixed
before transport. The original geometry is already one shared starting corner.
Failed and incomplete measurements remain absent. A null or unresolved added
BO gain is retained as a scientific result.

## Execution

The maintained project still has ten scripts. One sequential queue uses at most
86 transport workers within the 96-CPU combined limit, 64 GiB process memory,
16 GiB available host-memory reserve and 20 GiB free-disk reserves. No GPU
inference is included. Raw work is on `/data`; each archive also has a verified
copy on the independent home filesystem before raw work is removed.

The initial elapsed-time estimate is 72-120 hours, based on the previous native
run and allowance for more tasks. Film coverage can change transport time.
The launch record and completed sampling comparisons support later estimates.

```bash
bash run_all.sh status material_scan_data/film_design_20261030_bounded
tmux -L material-scan attach -t film_design_20261030_bounded
```

[Exact inputs and order](../studies/films/queue.yaml).

These simulations can establish a model design improvement and an optimizer
comparison. They do not establish a new compound, a fabrication optimum or a
reduction in qubit error.
