# Physics contract

The simulator injects phonons into a 10 mm by 10 mm, 525 µm substrate and
counts pair-breaking energy deposited at 17 aluminium junction electrodes. The
current injection energy is 0.010 eV. It is part of simulation identity even
though dividing every result by that fixed energy only changes the scale.

For each accepted surface hit, the legacy scorer uses

```text
QP = round(energy_deposited / aluminium_gap)
```

with NumPy's ties-to-even rounding. Hits away from the declared top surface and
non-positive deposits are excluded. An equidistant hit belongs to the
lowest-index electrode. A time exactly between two snapshots belongs to the
lower bin. These details are parity-tested because changing any of them changes
the reported count.

The electrode-aware device objective for a complete recorded design is

```text
J = sum_h W_h mean_{site in h, replica}(sum_e w_e QP_e / (events E_gun))
```

`W_h` is physical area mass. The number of sampled sites in a stratum is an
allocation choice and does not replace `W_h`. Every declared site and replica
must exist, every task must have the declared positive event count, and every
per-electrode vector must have length 17. Missing data causes an error; weights
are never renormalized over what happened to finish.

`R0.95` remains a reported spatial-tail diagnostic, not a hidden constraint.
The optimization objective and engineering constraints are frozen separately.
In particular, a widened search must declare a minimum acceptable ground-plane
gap or transition temperature before it launches.

Material realization includes the carrier material, density, source lattice
record, explicit overrides, and derived constants. Measured material values
are not clipped to search bounds. A density that cannot be represented by the
selected construction mode is rejected before simulation.
