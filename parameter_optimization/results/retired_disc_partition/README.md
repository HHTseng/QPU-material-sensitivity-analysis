# Retired: the 0.2 mm disc partition (2026-09-08)

These two levels were run against a partition whose "on-footprint" stratum was
a **0.2 mm disc** around each electrode centre. That is not the junction.

`JunctionKaplanElectrode::IsNearElectrode` takes the rectangle branch whenever
the location vectors are non-empty (ours carry 17 entries), and that branch
tests `|x - X_i| < setWidth/2` and `|y - Y_i| < setHeight/2` with
setWidth = setHeight = **10 um**. The 200 um `setIsland` belongs to
`WaffleElectrodeMessenger`, a different electrode class, and is never consulted
on this path.

The disc is **~1254x** the true junction area, so a uniform point inside it
landed in the real junction with probability 8.0e-4.

**They remain valid as proximity-band pilots.** Their per-site data was
reclassified under the corrected rectangle-distance partition to produce
`results/stage4_pilot_sd.json`, the within-stratum spread that drives the
Neyman allocation of the production design. Nothing was discarded.

**They must not certify the production quadrature**, and they carry no
`detail` block, so the convergence gate could not evaluate C4 or C5 from them
in any case.
