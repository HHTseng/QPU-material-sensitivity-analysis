"""Electrode-aware stratified injection quadrature (Sep-8 plan, Priorities 2-4).

GEOMETRY -- CORRECTED 2026-09-08
--------------------------------
The junction hit test is a RECTANGLE, not a disk. JunctionKaplanElectrode.cc
takes the branch at the bottom of IsNearElectrode() whenever the location
vectors are non-empty (ours carry 17 entries), and that branch tests

    |x - X_i| < fWidth/2   and   |y - Y_i| < fHeight/2

with fWidth = fHeight = 10 um from /main/electrode_param/setWidth,setHeight.
The 200 um `setIsland` parameter belongs to WaffleElectrodeMessenger -- a
different electrode class entirely -- and is never consulted on this path.

The first version of this module used a 200 um DISK as the "footprint". That
disk is ~1254x the area of the real junction, so:

  * the true junction mass is W = 17*(0.01mm)^2 / 64mm^2 = 2.656e-5, not 0.033;
  * a uniform point in the disk lands in the real junction with probability
    8.0e-4, so the 14 off-centre "on-footprint" draws contained an expected
    0.011 actual junction hits -- the stratum sampled almost none of it;
  * the "43x centre anomaly" was never a centre-vs-elsewhere effect. It was
    DIRECT JUNCTION injection versus NEAR-junction injection. Real physics,
    mislabelled.

Both facts can be true at once, and both are: seeding coverage with exact
centres did bias the disk's own mean (a centre is not a uniform draw from a
disk), AND the disk was the wrong region. The fix below addresses the second,
which subsumes the first: coverage points are now uniform inside the actual
10x10 um rectangle, which is simultaneously unbiased for that stratum and
guarantees every electrode is sampled.

Numerically the correction is modest for the baseline -- carving the true
junction out of the near band moves J by +1.3%, because W is so small that even
mu ~ 0.21 QPs/primary contributes only 1.3% of the total. It matters anyway:
the estimand must be the one the plan declares, the term is candidate-dependent,
and a design that never samples the junction cannot see a candidate that moves
yield onto it.

STRATA
------
Distance is to the RECTANGLE BOUNDARY (zero inside), per Priority 2.

    S0_junction   inside a junction rectangle        W = 2.656e-5 (exact)
    S1_le_0.05    0 < d <= 0.05 mm                   W ~ 2.64e-3
    S2_le_0.20    0.05 < d <= 0.20 mm                W ~ 3.29e-2
    S3_le_0.50    0.20 < d <= 0.50 mm                W ~ 1.78e-1
    S4_bulk       d > 0.50 mm                        W ~ 7.86e-1

The 0.20 mm proximity region is kept as its own stratum because the pilot shows
it carries ~45% of J. The extra 0.05 mm band splits the steepest part of the
gradient so Neyman allocation can resolve it.

WHAT THIS MODULE GUARANTEES
---------------------------
* Strata come from the contract's own electrode table and the real hit test.
* Every electrode is sampled inside its actual junction rectangle.
* The estimator is sum_h W_h * mu_h, so OVERSAMPLING DOES NOT BIAS IT.
* Designs are NESTED under doubling, within every stratum.
"""
import hashlib
import json
import math

import numpy as np
from scipy.stats import qmc

# Frozen before the convergence run (Priority 2). Hashed into the design, so two
# campaigns cannot silently disagree about the partition.
BAND_EDGES_MM = (0.05, 0.20, 0.50)
STRATA = ("S0_junction", "S1_le_0.05", "S2_le_0.20", "S3_le_0.50", "S4_bulk")

# Floors. S0's floor is one site per electrode: the direct-junction region is
# 0.0027% of the chip and a random draw would never find it.
MIN_SITES = {"S0_junction": 17, "S1_le_0.05": 8, "S2_le_0.20": 8,
             "S3_le_0.50": 8, "S4_bulk": 8}


def electrode_table(contract_fixed):
    xs = np.asarray(contract_fixed["electrode_x_mm"], dtype=float)
    ys = np.asarray(contract_fixed["electrode_y_mm"], dtype=float)
    if xs.shape != ys.shape:
        raise ValueError("electrode_x_mm and electrode_y_mm differ in length")
    return xs, ys


def junction_half_extent_mm(contract_fixed):
    """Half-width and half-height of the junction rectangle, in mm.

    These are /main/electrode_param/setWidth and setHeight -- the values the
    junction hit test actually uses. NOT electrode_island_um, which belongs to
    the waffle electrode class.
    """
    return (float(contract_fixed["electrode_width_um"]) / 2000.0,
            float(contract_fixed["electrode_height_um"]) / 2000.0)


def dist_to_rect(x, y, xs, ys, hw, hh):
    """Distance from (x, y) to the nearest junction rectangle's boundary.

    Zero when inside any rectangle. Standard axis-aligned box distance.
    """
    dx = np.maximum(np.abs(x - xs) - hw, 0.0)
    dy = np.maximum(np.abs(y - ys) - hh, 0.0)
    d = np.hypot(dx, dy)
    e = int(np.argmin(d))
    return float(d[e]), e


def classify(x, y, xs, ys, hw, hh):
    """Stratum label and nearest-electrode index for one point."""
    d, e = dist_to_rect(x, y, xs, ys, hw, hh)
    if d <= 0.0:
        return "S0_junction", e
    a, b, c = BAND_EDGES_MM
    if d <= a:
        return "S1_le_0.05", e
    if d <= b:
        return "S2_le_0.20", e
    if d <= c:
        return "S3_le_0.50", e
    return "S4_bulk", e


def stratum_weights(contract_fixed, n_mc=8_000_000, seed=0):
    """Probability mass of each stratum under UNIFORM SURFACE injection.

    S0 is computed ANALYTICALLY -- 17 rectangles is an exact area, and at
    W = 2.7e-5 a Monte-Carlo estimate would carry ~7% relative noise even at
    8e6 draws (it read 2.89e-5 against a true 2.66e-5). The remaining bands are
    estimated by Monte Carlo and renormalised to 1 - W_S0.
    """
    xs, ys = electrode_table(contract_fixed)
    hw, hh = junction_half_extent_mm(contract_fixed)
    span = float(contract_fixed["position_half_span_mm"])
    w_s0 = len(xs) * (2 * hw) * (2 * hh) / ((2 * span) ** 2)

    rng = np.random.default_rng(seed)
    p = rng.uniform(-span, span, size=(n_mc, 2))
    dx = np.maximum(np.abs(p[:, 0:1] - xs) - hw, 0.0)
    dy = np.maximum(np.abs(p[:, 1:2] - ys) - hh, 0.0)
    d = np.sqrt(dx ** 2 + dy ** 2).min(axis=1)
    out = d > 0.0
    a, b, c = BAND_EDGES_MM
    raw = {
        "S1_le_0.05": float((out & (d <= a)).mean()),
        "S2_le_0.20": float((out & (d > a) & (d <= b)).mean()),
        "S3_le_0.50": float((out & (d > b) & (d <= c)).mean()),
        "S4_bulk": float((out & (d > c)).mean()),
    }
    scale = (1.0 - w_s0) / sum(raw.values())
    w = {"S0_junction": w_s0}
    w.update({k: v * scale for k, v in raw.items()})
    return w


def neyman_allocation(n_total, weights, sds, costs=None, min_sites=None):
    """Cost-aware Neyman allocation, n_h proportional to W_h * s_h / sqrt(c_h).

    Floors are applied first and the remainder allocated, so a floor can never
    be violated by rounding. Largest-remainder rounding keeps the sum exact.
    """
    min_sites = dict(MIN_SITES if min_sites is None else min_sites)
    costs = costs or {h: 1.0 for h in weights}
    floor_total = sum(min_sites.get(h, 0) for h in weights)
    if n_total < floor_total:
        raise ValueError(
            f"n_total={n_total} is below the per-stratum floors ({floor_total}); "
            f"S0's floor is one site per junction by design")
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

    Nested by construction: the generator is consumed in order, so the first k
    accepted points of an n-point request are exactly the k-point request.

    S0 is seeded with one UNIFORM point inside each junction rectangle -- never
    the centre. The centre is the extreme of the very peak this stratum exists
    to resolve, and using it would bias mu_S0 upward while still claiming to
    estimate a footprint average.
    """
    xs, ys = electrode_table(contract_fixed)
    hw, hh = junction_half_extent_mm(contract_fixed)
    span = float(contract_fixed["position_half_span_mm"])
    out = []
    if stratum == "S0_junction":
        cov = np.random.default_rng(seed)
        # Cycle the electrodes so any n >= 1 spreads coverage evenly and a
        # doubling keeps the earlier points (nestedness).
        k = 0
        while len(out) < n:
            e = k % len(xs)
            out.append((round(float(xs[e]) + float(cov.uniform(-hw, hw)), 6),
                        round(float(ys[e]) + float(cov.uniform(-hh, hh)), 6), e))
            k += 1
        return out[:n]

    eng = qmc.Sobol(d=2, scramble=True, seed=seed)
    batch = 8192
    guard = 0
    while len(out) < n:
        guard += 1
        if guard > 8000:
            raise RuntimeError(
                f"stratum {stratum} could not be filled to {n} sites; it is "
                f"probably too thin for this electrode layout")
        for u in eng.random(batch):
            if len(out) >= n:
                break
            x = round(float(u[0]) * 2 * span - span, 6)
            y = round(float(u[1]) * 2 * span - span, 6)
            lab, e = classify(x, y, xs, ys, hw, hh)
            if lab == stratum:
                out.append((x, y, e))
    return out[:n]


def build_design(contract_fixed, alloc, template_z_mm, seed=None,
                 injection_law="uniform_surface", gun_energy_eV=None):
    """The full stratified design: ordered sites, labels, weights, hash.

    `gun_energy_eV` and the normalisation basis are carried INSIDE the design
    and inside its hash, so the objective cannot silently report per-primary
    numbers under a per-energy name.
    """
    if injection_law != "uniform_surface":
        raise ValueError(
            "stratum weights here are AREA weights and are correct only for "
            "uniform surface injection; supply measured masses for any other law")
    if gun_energy_eV is None:
        gun_energy_eV = contract_fixed.get("gun_energy_eV")
    gun_energy_eV = float(gun_energy_eV or 0.0)
    if gun_energy_eV <= 0.0:
        raise ValueError(
            "gun_energy_eV must be positive: the objective is named "
            "'per_energy' and must not silently fall back to per-primary")

    seed = int(contract_fixed["position_seed"]) if seed is None else int(seed)
    weights = stratum_weights(contract_fixed)
    sites, labels, electrodes = [], [], []
    for h in STRATA:
        n = int(alloc.get(h, 0))
        if n <= 0:
            continue
        sub = (seed * 1000003 + STRATA.index(h)) % (2 ** 31 - 1)
        for x, y, e in _sites_in_stratum(h, n, contract_fixed, sub):
            sites.append((x, y, template_z_mm))
            labels.append(h)
            electrodes.append(e)
    counts = {h: labels.count(h) for h in STRATA}
    hw, hh = junction_half_extent_mm(contract_fixed)
    n_el = len(contract_fixed["electrode_x_mm"])
    design = {
        "sites_mm": [list(s) for s in sites],
        "stratum": labels,
        "nearest_electrode": electrodes,
        "stratum_weights": weights,
        "stratum_counts": counts,
        "band_edges_mm": list(BAND_EDGES_MM),
        "junction_half_extent_mm": [hw, hh],
        # Names the MACRO commands, so a reader can grep the .mac and the C++
        # and confirm the strata match the hit test rather than trusting a
        # config key name.
        "geometry_source": "/main/electrode_param/setWidth,setHeight + "
                           "setXLocations,setYLocations; rectangle hit test in "
                           "JunctionKaplanElectrode::IsNearElectrode. NOT "
                           "setIsland (WaffleElectrodeMessenger).",
        "injection_law": injection_law,
        "position_seed": seed,
        "half_span_mm": float(contract_fixed["position_half_span_mm"]),
        "gun_energy_eV": gun_energy_eV,
        "normalization_basis": "per_injected_eV",
        # Predeclared electrode-criticality weights (Sep-8 plan 1.3). Uniform
        # until device calibration justifies otherwise; carried explicitly so
        # the reported magnitude is a weighted MEAN over junctions, not a sum.
        "electrode_weights": [1.0 / n_el] * n_el,
    }
    design["design_hash"] = hashlib.sha256(
        json.dumps({k: design[k] for k in sorted(design)},
                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return design
