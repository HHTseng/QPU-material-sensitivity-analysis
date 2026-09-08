"""Electrode-aware stratified injection quadrature (Sep-8 plan, Priorities 2-4).

WHY THIS EXISTS
---------------
The 16 -> 32 -> 64 uniform-Sobol sweep did not converge: three of four
candidates moved far outside their error bars from 32 to 64 sites, and the
ranking flipped. The cause is not sample size. QP yield is a sharp peak on the
17 electrode islands -- 3.3% of the chip area carrying ~39% of the objective --
and a uniform draw resolves it only by luck. At 16 sites the EXPECTED number of
on-electrode points is 0.53, so the estimator is dominated by a coin flip.

Measured from the 64-site baseline block data:

    stratum          W_h     mu_h        s_h        share of J
    H0 on-footprint  0.0333  4.583e-03   3.815e-03      38.7%
    H1 <=0.2mm       0.1002  8.086e-04   2.769e-04      20.5%
    H2 <=0.5mm       0.2755  3.193e-04   8.325e-05      22.3%
    H3 bulk          0.5910  1.239e-04   5.776e-05      18.5%

Sites needed for a 5% standard error on J:

    uniform / SRS            2931
    proportional strata      1273
    Neyman allocation         115      <-- 25.5x variance reduction

We ran 64. The uniform design is ~46x short of its own convergence criterion,
which is why more events could never have fixed it.

WHAT THIS MODULE GUARANTEES
---------------------------
* Strata come from the contract's own electrode table, not from a picture.
* Every electrode gets explicit coverage; H0 is never left to chance.
* The estimator is sum_h W_h * mu_h, so OVERSAMPLING H0 DOES NOT BIAS IT.
  Pooling all sites with equal weight would -- that is the bug being fixed.
* Designs are NESTED under doubling: sites(2N)[:len(sites(N))] == sites(N),
  within every stratum. A refinement therefore reuses every earlier sub-run.
"""
import hashlib
import json
import math

import numpy as np
from scipy.stats import qmc

# Frozen before the convergence run, per Priority 2. One registered
# threshold-sensitivity check is allowed afterwards; changing these silently
# would make two campaigns incomparable, so they are hashed into the design.
BOUNDARY_MM = 0.2
TRANSITION_MM = 0.5
STRATA = ("H0", "H1", "H2", "H3")

# Minimum sites per stratum. H0's floor is one per electrode: the whole point
# is that no electrode's footprint is left to a random draw.
MIN_SITES = {"H0": 17, "H1": 8, "H2": 8, "H3": 8}


def electrode_table(contract_fixed):
    xs = np.asarray(contract_fixed["electrode_x_mm"], dtype=float)
    ys = np.asarray(contract_fixed["electrode_y_mm"], dtype=float)
    if xs.shape != ys.shape:
        raise ValueError("electrode_x_mm and electrode_y_mm differ in length")
    return xs, ys


def island_radius_mm(contract_fixed):
    return float(contract_fixed["electrode_island_um"]) / 1000.0


def classify(x, y, xs, ys, r_island):
    """Stratum label and nearest-electrode index for one point.

    Distance is to the island BOUNDARY, not to the centre (Priority 2).
    """
    d = np.hypot(x - xs, y - ys)
    e = int(np.argmin(d))
    dmin = float(d[e])
    if dmin <= r_island:
        return "H0", e
    db = dmin - r_island
    if db <= BOUNDARY_MM:
        return "H1", e
    if db <= TRANSITION_MM:
        return "H2", e
    return "H3", e


def stratum_weights(contract_fixed, n_mc=4_000_000, seed=0):
    """Exact-by-Monte-Carlo probability mass W_h of each stratum.

    These are AREA weights, correct only for uniform surface injection. The
    injection law is recorded alongside them by `build_design` so that a later
    non-uniform law cannot silently inherit them.
    """
    xs, ys = electrode_table(contract_fixed)
    r = island_radius_mm(contract_fixed)
    span = float(contract_fixed["position_half_span_mm"])
    rng = np.random.default_rng(seed)
    p = rng.uniform(-span, span, size=(n_mc, 2))
    d = np.sqrt((p[:, 0:1] - xs) ** 2 + (p[:, 1:2] - ys) ** 2).min(axis=1)
    db = np.maximum(d - r, 0.0)
    on = d <= r
    w = {
        "H0": float(on.mean()),
        "H1": float((~on & (db <= BOUNDARY_MM)).mean()),
        "H2": float((~on & (db > BOUNDARY_MM) & (db <= TRANSITION_MM)).mean()),
        "H3": float((db > TRANSITION_MM).mean()),
    }
    total = sum(w.values())
    if abs(total - 1.0) > 1e-9:
        w = {k: v / total for k, v in w.items()}
    return w


def neyman_allocation(n_total, weights, sds, costs=None, min_sites=None):
    """Cost-aware Neyman allocation, n_h proportional to W_h * s_h / sqrt(c_h).

    Floors are applied first and the remainder is allocated, so a floor can
    never be violated by rounding. Largest-remainder rounding keeps the sum
    exactly n_total.
    """
    min_sites = dict(MIN_SITES if min_sites is None else min_sites)
    costs = costs or {h: 1.0 for h in weights}
    floor_total = sum(min_sites.get(h, 0) for h in weights)
    if n_total < floor_total:
        raise ValueError(
            f"n_total={n_total} is below the per-stratum floors ({floor_total}); "
            f"H0's floor is one site per electrode by design")
    share = {h: weights[h] * sds[h] / math.sqrt(costs[h]) for h in weights}
    denom = sum(share.values())
    free = n_total - floor_total
    exact = {h: free * share[h] / denom for h in weights}
    alloc = {h: min_sites.get(h, 0) + int(math.floor(exact[h])) for h in weights}
    rem = n_total - sum(alloc.values())
    for h in sorted(weights, key=lambda k: exact[k] - math.floor(exact[k]),
                    reverse=True)[:rem]:
        alloc[h] += 1
    return alloc


def _sites_in_stratum(stratum, n, contract_fixed, seed):
    """n scrambled-Sobol points inside one stratum, by rejection.

    Nested by construction: the generator is drawn once and consumed in order,
    so the first k accepted points of an n-point request are exactly the
    k-point request. H0 is seeded with one point per electrode centre FIRST, so
    every electrode has guaranteed coverage before any random point is added.
    """
    xs, ys = electrode_table(contract_fixed)
    r = island_radius_mm(contract_fixed)
    span = float(contract_fixed["position_half_span_mm"])
    out = []
    if stratum == "H0":
        # One guaranteed point per electrode -- but a UNIFORM point inside the
        # footprint, never the centre. Measured 2026-09-08 on a 64-site pilot:
        # an injection at the exact electrode centre yields 43x what the same
        # footprint yields elsewhere, so seeding coverage with centres biased
        # mu_H0 by 24x and the whole objective by ~10x. The stratum mean must
        # estimate the footprint AVERAGE, and a centre is the extreme value of
        # the very peak this stratum exists to resolve.
        cov = np.random.default_rng(seed)
        for e in range(len(xs)):
            if len(out) >= n:
                break
            rad = r * math.sqrt(float(cov.random()))
            ang = 2.0 * math.pi * float(cov.random())
            out.append((round(float(xs[e]) + rad * math.cos(ang), 6),
                        round(float(ys[e]) + rad * math.sin(ang), 6), e))
    eng = qmc.Sobol(d=2, scramble=True, seed=seed)
    batch = 4096
    guard = 0
    while len(out) < n:
        guard += 1
        if guard > 4000:
            raise RuntimeError(
                f"stratum {stratum} could not be filled to {n} sites; it is "
                f"probably too thin for this electrode layout")
        for u in eng.random(batch):
            if len(out) >= n:
                break
            x = round(float(u[0]) * 2 * span - span, 6)
            y = round(float(u[1]) * 2 * span - span, 6)
            lab, e = classify(x, y, xs, ys, r)
            if lab == stratum:
                out.append((x, y, e))
    return out[:n]


def build_design(contract_fixed, alloc, template_z_mm, seed=None,
                 injection_law="uniform_surface"):
    """The full stratified design: ordered sites, labels, weights, hash.

    Returned in a shape `build_scenario` can drop straight into the contract,
    and hashed so that two campaigns cannot silently disagree about it.
    """
    if injection_law != "uniform_surface":
        raise ValueError(
            "stratum weights here are AREA weights and are correct only for "
            "uniform surface injection; supply measured masses for any other law")
    seed = int(contract_fixed["position_seed"]) if seed is None else int(seed)
    weights = stratum_weights(contract_fixed)
    sites, labels, electrodes, strat_of_site = [], [], [], []
    for h in STRATA:
        n = int(alloc.get(h, 0))
        if n <= 0:
            continue
        # Distinct sub-seed per stratum so the rejection streams are independent
        # but each is individually reproducible and nested.
        sub = (seed * 1000003 + STRATA.index(h)) % (2 ** 31 - 1)
        for x, y, e in _sites_in_stratum(h, n, contract_fixed, sub):
            sites.append((x, y, template_z_mm))
            labels.append(h)
            electrodes.append(e)
            strat_of_site.append(h)
    counts = {h: labels.count(h) for h in STRATA}
    design = {
        "sites_mm": [list(s) for s in sites],
        "stratum": labels,
        "nearest_electrode": electrodes,
        "stratum_weights": weights,
        "stratum_counts": counts,
        "boundary_mm": BOUNDARY_MM,
        "transition_mm": TRANSITION_MM,
        "injection_law": injection_law,
        "position_seed": seed,
        "half_span_mm": float(contract_fixed["position_half_span_mm"]),
        "island_radius_mm": island_radius_mm(contract_fixed),
    }
    design["design_hash"] = hashlib.sha256(
        json.dumps({k: design[k] for k in sorted(design)},
                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return design


def stratified_mean(values_by_site, labels, weights):
    """J = sum_h W_h * mu_h.

    `values_by_site` maps site index -> value (already averaged over replicas).
    Strata with no observed site are dropped and the remaining weights are
    renormalised, which is reported rather than silently absorbed.
    """
    present, mu = [], {}
    for h in STRATA:
        vals = [values_by_site[i] for i, lab in enumerate(labels)
                if lab == h and i in values_by_site]
        if vals:
            mu[h] = float(np.mean(vals))
            present.append(h)
    if not present:
        raise ValueError("no observed sites")
    wsum = sum(weights[h] for h in present)
    j = sum(weights[h] * mu[h] for h in present) / wsum
    return j, mu, present, wsum


def bootstrap_se(values_by_site_replica, labels, weights, n_boot=2000, seed=0):
    """Hierarchical bootstrap: resample sites within stratum, replicas within site.

    Separates the two levels rather than pooling them, so the spatial and the
    stochastic contribution can be reported apart (Priority 2.3).
    """
    rng = np.random.default_rng(seed)
    by_h = {h: [i for i, lab in enumerate(labels) if lab == h
                and i in values_by_site_replica] for h in STRATA}
    by_h = {h: v for h, v in by_h.items() if v}
    wsum = sum(weights[h] for h in by_h)
    draws = np.empty(n_boot)
    for b in range(n_boot):
        tot = 0.0
        for h, idx in by_h.items():
            pick = rng.choice(idx, size=len(idx), replace=True)
            means = []
            for i in pick:
                reps = values_by_site_replica[i]
                means.append(float(np.mean(rng.choice(reps, size=len(reps),
                                                      replace=True))))
            tot += weights[h] * float(np.mean(means))
        draws[b] = tot / wsum
    return float(draws.std(ddof=1)), draws


def node_leverage(values_by_site, labels, weights):
    """Weighted contribution of each site to J, as a fraction.

    Priority 4 requires that no weighted quadrature node exceed 10%. Under the
    uniform design site 36 alone carried 30-40%, which is the failure this
    module removes.
    """
    j, mu, present, wsum = stratified_mean(values_by_site, labels, weights)
    counts = {h: sum(1 for i, lab in enumerate(labels)
                     if lab == h and i in values_by_site) for h in present}
    return {i: (weights[labels[i]] / wsum) * values_by_site[i]
               / counts[labels[i]] / j
            for i in values_by_site if labels[i] in present}
