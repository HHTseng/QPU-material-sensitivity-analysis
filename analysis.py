"""Explicit weighted statistics and comparisons of complete simulation arrays."""

import math

import numpy as np


def validate_complete_blocks(blocks, design, *, require_electrodes=False):
    labels = tuple(str(v) for v in design["position_strata"])
    weights = dict(design["stratum_weights"])
    replicas = tuple(design["replica_ids"])
    ew = np.asarray(design["electrode_weights"], dtype=float)
    if not labels or not replicas or len(set(replicas)) != len(replicas):
        raise ValueError("positions and unique replica IDs must be nonempty")
    if not design.get("design_hash"):
        raise ValueError("design_hash is required")
    if design.get("injection_law", "uniform_surface") != "uniform_surface":
        raise ValueError("area weighting requires uniform_surface injection")
    if set(labels) != set(weights) or not all(math.isfinite(v) and v > 0 for v in weights.values()):
        raise ValueError("position labels must match the positive finite area weights")
    if not math.isclose(sum(weights.values()), 1., rel_tol=0., abs_tol=1e-12):
        raise ValueError("area weights must sum to one")
    if ew.ndim != 1 or not ew.size or not np.isfinite(ew).all() or np.any(ew < 0) or not np.isclose(ew.sum(), 1., rtol=0., atol=1e-12):
        raise ValueError("electrode weights must be non-negative and sum to one")
    events, energy = design["events_per_task"], design["gun_energy_eV"]
    if not isinstance(events, (int, np.integer)) or events <= 0 or not math.isfinite(energy) or energy <= 0:
        raise ValueError("positive integer events and finite positive excitation required")
    expected = {(p, r) for p in range(len(labels)) for r in replicas}
    indexed = {}
    for block in blocks:
        key = (int(block["position"]), int(block["replica"]))
        if key in indexed or key not in expected:
            raise ValueError(f"duplicate or undeclared block {key}")
        if block["events"] != events:
            raise ValueError(f"block {key} has {block['events']} events, expected {events}")
        total = block["total_qps"]
        if not math.isfinite(total) or total < 0:
            raise ValueError(f"invalid QP total in block {key}")
        electrodes = block.get("per_electrode_qps")
        if electrodes is None:
            if require_electrodes:
                raise ValueError(f"block {key} lacks electrode counts")
        else:
            a = np.asarray(electrodes, dtype=float)
            if a.shape != ew.shape or not np.isfinite(a).all() or np.any(a < 0):
                raise ValueError(f"invalid electrode counts in block {key}")
            if not math.isclose(math.fsum(a), total, rel_tol=0., abs_tol=1e-9):
                raise ValueError(f"electrode counts disagree with total in block {key}")
        if block.get("n_hits") is not None and block["n_hits"] < 0:
            raise ValueError(f"negative hit count in block {key}")
        indexed[key] = block
    if set(indexed) != expected:
        raise ValueError(f"incomplete block set: missing {len(expected - set(indexed))} of {len(expected)}")
    return indexed


def _burdens(blocks, design, objective='device_junction_qps_per_eV'):
    indexed = validate_complete_blocks(blocks, design, require_electrodes=True)
    counts = np.asarray([[indexed[(p, r)]["per_electrode_qps"] for r in design["replica_ids"]]
                         for p in range(len(design["position_strata"]))], dtype=float)
    if objective in ('total_qps_per_primary', 'total_QPs_per_primary', 'junction_qps_per_primary'):
        return counts.sum(axis=2) / design['events_per_task'], counts.max(axis=2) / design['events_per_task']
    if objective not in ('device_junction_qps_per_eV', 'device_weighted_junction_qps_per_energy'):
        raise ValueError(f'per-task response is unavailable for objective {objective!r}')
    scale = design["events_per_task"] * design["gun_energy_eV"]
    ew = np.asarray(design["electrode_weights"], dtype=float)
    # Keep the retained one-vector dot order rather than a different batched BLAS sum.
    burden = np.asarray([[float(row @ ew) / scale for row in site] for site in counts])
    return burden, counts.max(axis=2) / scale


def _regions(values, design, *, equal_sites=False):
    if equal_sites:
        return [('surface', list(range(len(values))), 1., values)]
    grouped = {}
    for position, label in enumerate(design["position_strata"]):
        grouped.setdefault(str(label), []).append(position)
    weights = dict(design["stratum_weights"])
    return [(label, positions, weights[label], values[positions]) for label, positions in grouped.items()]


def _tail_mean(pairs, probability=.05):
    pairs = sorted(pairs, reverse=True)
    cutoff = probability * sum(w for _, w in pairs)
    numerator = mass = 0.
    for value, weight in pairs:
        take = min(weight, cutoff - mass)
        if take <= 0:
            break
        numerator += value * take
        mass += take
    if mass <= 0:
        raise ValueError("tail mean needs positive area")
    return float(numerator / mass)


def _resample(regions, draws, seed, tail=False):
    if not isinstance(draws, int) or draws < 2:
        raise ValueError("at least two integer resampling draws required")
    rng = np.random.default_rng(seed)
    estimates = np.empty(draws)
    for draw in range(draws):
        total, sampled = 0., []
        for _, positions, weight, matrix in regions:
            selected = rng.choice(len(positions), size=len(positions), replace=True)
            selected_values = matrix[selected]
            replica_index = rng.choice(selected_values.shape[1], size=selected_values.shape, replace=True)
            replica_means = np.take_along_axis(selected_values, replica_index, axis=1).mean(axis=1)
            if tail:
                sampled.extend((value, weight / len(positions)) for value in replica_means)
            else:
                total += weight * float(replica_means.mean())
        estimates[draw] = _tail_mean(sampled) if tail else total
    return float(np.std(estimates, ddof=1))


def estimate(blocks, design, *, bootstrap_draws=1000, bootstrap_seed=7,
             tail_bootstrap_draws=500, tail_bootstrap_seed=11):
    """J = Σh Wh mean(Q·w)/(N Eg), from exactly the declared site × replica set."""
    burden, worst = _burdens(blocks, design)
    regions = _regions(burden, design)
    per_site, worst_per_site = burden.mean(axis=1), worst.mean(axis=1)
    means = {label: float(per_site[positions].mean()) for label, positions, _, _ in regions}
    value = sum(weight * means[label] for label, _, weight, _ in regions)
    se = _resample(regions, bootstrap_draws, bootstrap_seed)
    tail = _tail_mean([(worst_per_site[p], weight / len(positions))
                       for _, positions, weight, _ in regions for p in positions])
    tail_se = _resample(_regions(worst, design), tail_bootstrap_draws, tail_bootstrap_seed, tail=True)
    leverage = [weight * per_site[p] / len(positions) / value if value else 0.
                for _, positions, weight, _ in regions for p in positions]
    return {"value": float(value), "standard_error": se, "relative_standard_error": se / value if value else None,
            "means_by_stratum": means, "sites_by_stratum": {k: len(p) for k, p, _, _ in regions},
            "weights_by_stratum": dict(design["stratum_weights"]), "maximum_site_leverage": float(max(leverage)),
            "spatial_r95": tail, "spatial_r95_standard_error": tail_se,
            "naive_equal_site_mean": float(per_site.mean()), "n_sites": len(per_site),
            "n_replicas": burden.shape[1], "events_per_task": design["events_per_task"],
            "design_hash": design["design_hash"], "units": "weighted_junction_QPs_per_injected_eV"}


def paired_difference(blocks_a, blocks_b, design, *, bootstrap_draws=1000, bootstrap_seed=23,
                      objective='device_junction_qps_per_eV'):
    """A − B with matched sites, replicas and declared weights before resampling."""
    a, _ = _burdens(blocks_a, design, objective)
    b, _ = _burdens(blocks_b, design, objective)
    delta = a - b
    historical = objective in ('total_qps_per_primary', 'total_QPs_per_primary', 'junction_qps_per_primary')
    regions = _regions(delta, design, equal_sites=historical)
    value = sum(weight * float(np.mean([float(np.mean(delta[p])) for p in positions]))
                for _, positions, weight, _ in regions)
    if historical:
        se = float(np.sqrt(delta.var(axis=1, ddof=1).sum() / delta.shape[1]) / len(delta)) if delta.shape[1] >= 2 else None
    else:
        se = _resample(regions, bootstrap_draws, bootstrap_seed)
    baseline = sum(weight * float(np.mean([float(np.mean(b[p])) for p in positions]))
                   for _, positions, weight, _ in regions)
    return {"value": float(value), "standard_error": se,
            "relative_to_b": float(value / baseline) if baseline else None,
            "z": float(value / se) if se else None,
            "confidence_interval_95": [float(value - 1.96 * se), float(value + 1.96 * se)] if se is not None else None,
            "n_sites": len(delta), "sites_favouring_a": int(np.sum(delta.mean(axis=1) < 0)),
            'units': 'summed_junction_QPs_per_primary' if historical else 'weighted_junction_QPs_per_injected_eV',
            'uncertainty_method': 'within-site paired replica variance' if historical else 'hierarchical site and replica resampling'}


def realization_estimates(blocks, design, objective='device_junction_qps_per_eV'):
    """One declared area-average response for each transport realization."""
    values, _ = _burdens(blocks, design, objective)
    historical = objective in ('total_qps_per_primary', 'total_QPs_per_primary', 'junction_qps_per_primary')
    regions = _regions(values, design, equal_sites=historical)
    return np.sum([weight * matrix.mean(axis=0) for _, _, weight, matrix in regions], axis=0)


def historical_summary(blocks, design, *, robustness=1., objective='total_qps_per_primary', alpha=1., beta=1.):
    """Older unweighted per-primary/summed-electrode quantities, explicitly named."""
    indexed = validate_complete_blocks(blocks, design)
    totals = np.asarray([[indexed[(p, r)]["total_qps"] for p in range(len(design["position_strata"]))]
                         for r in design["replica_ids"]], dtype=float)
    per_primary = totals.mean() / design["events_per_task"]
    n_sim = design['events_per_task'] * totals.size
    se_total = float(np.sqrt(totals.shape[0] * totals.var(axis=0, ddof=1).sum())) if totals.shape[0] > 1 else None
    q = totals.ravel() / design['events_per_task']
    se_block = float(q.std(ddof=1) / np.sqrt(q.size)) if q.size > 1 else 0.
    rng = np.random.default_rng(12345)
    site_totals = totals.sum(axis=0)
    draws = site_totals[rng.integers(0, len(site_totals), size=(2000, len(site_totals)))].sum(axis=1)
    output = {"total_qps": float(totals.sum()), "total_qps_per_primary": float(per_primary),
              "junction_qps_per_primary": float(per_primary),
              "total_qps_standard_error": se_total,
              "total_qps_per_primary_standard_error": se_total / n_sim if se_total is not None else None,
              "site_bootstrap_ci_total": np.percentile(draws, [2.5, 97.5]).tolist(),
              "p90_position": float(np.percentile(totals.mean(axis=0) / design['events_per_task'], 90)),
              "robust_mean_plus_se": float(q.mean() + robustness * se_block),
              "across_block_standard_error": se_block,
              "n_sites": totals.shape[1], "n_replicas": totals.shape[0]}
    electrodes = [block.get('per_electrode_qps') for block in indexed.values()]
    if all(v is not None for v in electrodes):
        electrode_totals = np.sum(electrodes, axis=0)
        output['max_electrode_qps_per_primary'] = float(electrode_totals.max() / n_sim)
        output['weighted_sum'] = alpha * per_primary + beta * output['max_electrode_qps_per_primary']
    if objective not in output:
        raise ValueError(f'unknown or unavailable historical objective {objective!r}')
    se = (se_total if objective == 'total_qps' else
          se_block if objective == 'robust_mean_plus_se' else
          (se_total / n_sim if se_total is not None else None) if objective in ('total_qps_per_primary', 'junction_qps_per_primary') else None)
    output.update(value=output[objective], standard_error=se, objective=objective)
    return output


def spatial_convergence(coarse, fine, *, baseline_coarse=None, baseline_fine=None,
                        relative_limit=.05, sigma_limit=2., leverage_limit=.10):
    """Declared area-convergence checks; region shifts identify sites to refine."""
    a, b = float(coarse["value"]), float(fine["value"])
    if a <= 0 or b <= 0:
        raise ValueError("positive measured yields required for relative convergence")
    shift = abs(b - a) / b
    combined = math.hypot(coarse["standard_error"], fine["standard_error"])
    region_shift = {}
    if set(coarse["means_by_stratum"]) != set(fine["means_by_stratum"]):
        raise ValueError("convergence comparison requires the same physical regions")
    for name, value in fine["means_by_stratum"].items():
        old = coarse["means_by_stratum"][name]
        region_shift[name] = abs(value - old) / abs(value) if value else (0. if old == 0 else float("inf"))
    reduction_shift = None
    if baseline_coarse is not None and baseline_fine is not None:
        if baseline_coarse <= 0 or baseline_fine <= 0:
            raise ValueError("baseline yields must be positive")
        reduction_shift = abs(a / baseline_coarse - b / baseline_fine)
    checks = {"relative_shift": shift <= relative_limit,
              "within_combined_error": combined > 0 and abs(b - a) <= sigma_limit * combined,
              "reduction_stability": reduction_shift is None or reduction_shift <= relative_limit,
              "maximum_site_leverage": fine["maximum_site_leverage"] <= leverage_limit,
              "every_region": all(v <= relative_limit for v in region_shift.values())}
    return {"passed": all(checks.values()), "checks": checks, "relative_shift": shift,
            "combined_standard_error": combined, "reduction_shift": reduction_shift,
            "region_relative_shifts": region_shift,
            "refine_regions": [k for k, v in region_shift.items() if v > relative_limit],
            "ranking_checked": False}


def sensitivity(samples, outcomes, names=None, *, problem=None, method="pearson", log_outcome=False, seed=7, num_levels=4):
    """Pearson correlations or Morris μ, μ*, σ; failed outcomes remain absent."""
    X, y = np.asarray(samples, dtype=float), np.asarray(outcomes, dtype=float)
    if X.ndim != 2 or y.shape != (X.shape[0],) or not np.isfinite(X).all() or not np.isfinite(y).all():
        raise ValueError("finite sample matrix and matching complete outcome vector required")
    names = list(names or [f"x{i}" for i in range(X.shape[1])])
    if len(names) != X.shape[1]:
        raise ValueError("parameter names do not match sample columns")
    if log_outcome:
        if np.any(y <= 0):
            raise ValueError("log outcomes must be positive; measured zeros cannot be discarded silently")
        y = np.log10(y)
    if method == "morris":
        if problem is None:
            raise ValueError("Morris analysis needs the original parameter definition")
        from SALib.analyze.morris import analyze
        if len(y) % (X.shape[1] + 1):
            raise ValueError('Morris analysis requires complete trajectories of d+1 samples')
        result = analyze(problem, X, y, seed=seed, num_levels=num_levels, print_to_console=False)
        return {"method": "morris", "names": names, **{k: np.asarray(result[k]).tolist() for k in ("mu", "mu_star", "sigma", "mu_star_conf")}}
    if method != "pearson" or X.shape[0] < 3:
        raise ValueError("Pearson analysis needs at least three samples; unknown method")
    with np.errstate(divide="ignore", invalid="ignore"):
        correlation = np.corrcoef(np.column_stack((X, y)), rowvar=False)[-1, :-1]
    return {"method": "pearson", "names": names, "correlation": [float(c) if np.isfinite(c) else None for c in correlation],
            "constant_parameters": [n for n, c in zip(names, correlation) if not np.isfinite(c)],
            "n_samples": len(y), "log_outcome": log_outcome}


def rank_stability(a, b):
    """Compare matching candidates; flag inversions only when both are resolved."""
    if set(a) != set(b) or len(a) < 2:
        raise ValueError("at least two matching candidates required")
    from scipy.stats import spearmanr, kendalltau
    names = sorted(a)
    av, bv = [a[k]["value"] for k in names], [b[k]["value"] for k in names]
    inversions = []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            da, db = a[first]["value"] - a[second]["value"], b[first]["value"] - b[second]["value"]
            ea = 1.96 * math.hypot(a[first]["standard_error"], a[second]["standard_error"])
            eb = 1.96 * math.hypot(b[first]["standard_error"], b[second]["standard_error"])
            if da * db < 0 and abs(da) > ea and abs(db) > eb:
                inversions.append([first, second])
    return {"spearman": float(spearmanr(av, bv).statistic), "kendall": float(kendalltau(av, bv).statistic),
            "resolved_inversions": inversions, "stable_within_uncertainty": not inversions}


def comparison_features(point, derived):
    """Property vector -> velocities, impedances and measured transport features."""
    return {'sub_vsound_m_s': derived['vsound_m_s'], 'sub_vtrans_m_s': derived['vtrans_m_s'],
            'sub_anisotropy': derived['anisotropy'], 'sub_impedance': derived['impedance_substrate'],
            **{k: float(point[k]) for k in ('sub_scat', 'sub_decay', 'sub_decayTT')},
            'topfilm_vsound_m_s': float(point['topfilm_vsound']) * 1000.,
            'topfilm_gap_eV': float(point['topfilm_gap']),
            'topfilm_ph_lifetime_ns': float(point['topfilm_ph_lifetime']),
            'topfilm_impedance': float(point['topfilm_density']) * float(point['topfilm_vsound']) * 1000.,
            'bot_vsound_m_s': float(point['bot_vsound']) * 1000.,
            'bot_ph_lifetime_ns': float(point['bot_ph_lifetime']),
            'bot_impedance': float(point['bot_density']) * float(point['bot_vsound']) * 1000.}


def curve_distance(sim_t, sim_dg, x_data, y_data, dy_data=None, normalize='none'):
    """Nearest-time residual sum for an explicitly matched measured electrode."""
    t, rate, x, y = [np.asarray(v, dtype=float) for v in (sim_t, sim_dg, x_data, y_data)]
    if t.ndim != 1 or not t.size or rate.shape != t.shape or x.ndim != 1 or y.shape != x.shape or any(not np.isfinite(v).all() for v in (t, rate, x, y)):
        raise ValueError('finite matching simulation and measured time-series required')
    if normalize not in ('none', 'y', 'dy2'):
        raise ValueError('curve normalization must be none, y, or dy2')
    indices = np.array([np.argmin(np.abs(value - t)) for value in x], dtype=int)
    squared = (rate[indices] - y) ** 2
    if normalize == 'y':
        good = np.abs(y) > 1e-12
        return float(np.sum(squared[good] / y[good]))
    if normalize == 'dy2':
        dy = np.asarray(dy_data, dtype=float)
        if dy.shape != y.shape or not np.isfinite(dy).all():
            raise ValueError('finite matching measured uncertainties required')
        good = np.abs(dy) > 1e-12
        return float(np.sum(squared[good] / dy[good] ** 2))
    return float(squared.sum())


def feature_scales(candidates, feature_spec):
    scales = {}
    for name, mode in feature_spec.items():
        if mode not in ("log", "linear"):
            raise ValueError(f"unknown feature scale {mode!r}")
        values = [float(c["features"][name]) for c in candidates
                  if c["features"].get(name) is not None and np.isfinite(c["features"][name])
                  and (mode != "log" or c["features"][name] > 0)]
        values = np.log(values) if mode == "log" else np.asarray(values)
        mad = float(np.median(np.abs(values - np.median(values)))) * 1.4826 if len(values) >= 2 else 0.
        scales[name] = mad if mad > 0 else (float(np.std(values)) or 1.) if len(values) >= 2 else 1.
    return scales


def distance(target, candidate, feature_spec, scales, weights=None):
    weights = weights or {}
    num = den = 0.
    missing, z_values = [], {}
    for name, mode in feature_spec.items():
        t, c = target.get(name), candidate["features"].get(name)
        if t is None or c is None or not np.isfinite(t) or not np.isfinite(c) or (mode == "log" and (t <= 0 or c <= 0)):
            missing.append(name)
            continue
        weight, scale = float(weights.get(name, 1.)), float(scales[name])
        if not math.isfinite(weight) or weight < 0 or not math.isfinite(scale) or scale <= 0:
            raise ValueError("feature weights must be non-negative and scales positive")
        z = (math.log(c) - math.log(t)) / scale if mode == "log" else (float(c) - float(t)) / scale
        num += weight * z * z
        den += weight
        z_values[name] = round(z, 4)
    if not z_values or den == 0:
        return None
    return {"distance": math.sqrt(num / den), "matched": len(z_values), "total": len(feature_spec),
            "unmatched_features": missing, "z": z_values}


def project_materials(target, candidates, feature_spec, weights=None, top=10):
    """Measured feature coverage sorts first, then distance within that coverage."""
    scales, rows = feature_scales(candidates, feature_spec), []
    for candidate in candidates:
        d = distance(target, candidate, feature_spec, scales, weights)
        if d is not None:
            rows.append({**d, "coverage": d["matched"] / d["total"],
                         "stratum": f"{d['matched']}/{d['total']}", "id": candidate["id"], "candidate": candidate})
    rows.sort(key=lambda r: (-r["matched"], r["distance"]))
    missing = {name: sum(name in r["unmatched_features"] for r in rows) for name in feature_spec}
    return {"rows": rows[:top], "scales": scales, "missing_features": missing,
            "n_candidates": len(rows), "fully_covered": sum(r["matched"] == r["total"] for r in rows)}


def tolerance_summary(centre, moves, *, paired=None, coordinates=None, names=None,
                      improvement_threshold=.02, bound_margin=.02):
    """Local one-variable tests; unavailable evidence cannot establish convergence."""
    value, se = centre["value"], centre["standard_error"]
    if value <= 0:
        raise ValueError("positive centre yield required")
    paired, summaries, improving = paired or {}, {}, []
    measured_error = se is not None and np.isfinite(se) and se >= 0
    variables = sorted({label.rsplit(":", 1)[0] for label in moves})
    complete = bool(measured_error)
    for name in variables:
        labels = [name + ":minus", name + ":plus"]
        tested = [moves[k]["value"] for k in labels if moves.get(k, {}).get("value") is not None]
        swing = max((abs(v - value) / value for v in tested), default=None)
        summaries[name] = {"maximum_relative_swing": swing,
                           "resolved": swing > 4 * se / value if swing is not None and measured_error else None,
                           "below_two_standard_errors": swing < 2 * se / value if swing is not None and measured_error else None}
        for label in labels:
            p = paired.get(label)
            if p is None or p.get("relative_to_b") is None or p.get("z") is None:
                complete = False
                continue
            if p["relative_to_b"] <= -improvement_threshold and p["z"] <= -2.:
                improving.append({"move": label, "relative_change": p["relative_to_b"], "z": p["z"]})
    at_bound = []
    if coordinates is not None:
        at_bound = [name for name, u in zip(names or range(len(coordinates)), coordinates) if u <= bound_margin or u >= 1 - bound_margin]
    return {"variables": summaries, "materially_improving_moves": improving, "variables_near_a_bound": at_bound,
            "complete_paired_tests": complete and bool(variables),
            "locally_converged": bool(variables) and complete and not improving and not at_bound and coordinates is not None,
            "untested_criteria": ["stability after successive model updates", "stability across optimizer random seeds", "variable interactions"]}


def pareto_front(rows, keys, lower_is_better=None):
    lower = set(keys if lower_is_better is None else lower_is_better)
    usable = [r for r in rows if all(r.get(k) is not None and not np.isnan(r[k]) for k in keys)]
    values = np.asarray([[float(row[k]) * (1 if k in lower else -1) for k in keys] for row in usable])
    return [row for i, row in enumerate(usable) if not np.any(np.all(values <= values[i], axis=1) & np.any(values < values[i], axis=1))]


def compare_repeated(a, b, *, confidence=.95, relative_margin=.05, paired=False):
    """Student-t interval for matched or independent outcomes; equivalence needs a margin."""
    from scipy.stats import t
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.ndim != 1 or b.ndim != 1 or min(len(a), len(b)) < 2 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("at least two finite independent outcomes per implementation required")
    if not 0 < confidence < 1 or relative_margin <= 0:
        raise ValueError("confidence must be in (0,1) and equivalence margin positive")
    if paired:
        if a.shape != b.shape:
            raise ValueError('matched realization arrays must have equal lengths')
        se = float((a - b).std(ddof=1) / math.sqrt(len(a)))
        df = len(a) - 1
    else:
        va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
        se = math.sqrt(va + vb)
        df = (va + vb) ** 2 / (va * va / (len(a) - 1) + vb * vb / (len(b) - 1)) if se else float("inf")
    mean_a, mean_b = float(a.mean()), float(b.mean())
    delta = mean_a - mean_b
    radius = float(t.ppf((1 + confidence) / 2, df) * se)
    interval = [delta - radius, delta + radius]
    margin = relative_margin * abs(mean_b)
    return {"mean_new": mean_a, "mean_previous": mean_b, "difference": delta,
            "standard_error": se, "confidence": confidence, "confidence_interval": interval,
            "relative_difference": delta / mean_b if mean_b else None,
            "equivalence_margin": margin, "equivalent_within_margin": interval[0] > -margin and interval[1] < margin,
            "consistent_with_zero_difference": interval[0] <= 0 <= interval[1],
            "n_new": len(a), "n_previous": len(b), 'paired':paired,
            'degrees_of_freedom':int(df) if paired else float(df) if np.isfinite(df) else None}
