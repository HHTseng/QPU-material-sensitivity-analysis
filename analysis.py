"""Area-weighted QP yields and uncertainty from complete simulation arrays.

i=site, r=realization, e=junction, h=surface region; N[i,r,e] sums hit QPs.
Q=N/N_p per primary; w_e junction weights, W_h area fractions, E_g source eV.
"""

import math
import copy
import inspect
import json
from pathlib import Path

import physics
import records
from figures.plot import export_presentation, plot_result

ROOT = Path(__file__).resolve().parent

import numpy as np


def prepare_counts(blocks, design=None):
    """N[i,r,e] -> b[i,r]=sum_e w_e*N[i,r,e]/(N_p*E_g), weighted QPs/eV."""
    if isinstance(blocks,dict) and 'groups' in blocks and 'totals' in blocks: return blocks
    require_electrodes=False
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
    totals=np.asarray([[indexed[(i,r)]['total_qps'] for r in replicas] for i in range(len(labels))])
    counts=None; weighted=None; worst=None
    if all(block.get('per_electrode_qps') is not None for block in indexed.values()):
        counts=np.asarray([[indexed[(i,r)]['per_electrode_qps'] for r in replicas] for i in range(len(labels))])
        weighted=np.asarray([[float(row@ew)/(events*energy) for row in site] for site in counts])
        worst=counts.max(axis=2)/(events*energy)
    grouped={}
    for i,label in enumerate(labels): grouped.setdefault(label,[]).append(i)
    return {'design':design,'totals':totals,'counts':counts,'weighted':weighted,'worst':worst,
            'groups':[(label,positions,weights[label]) for label,positions in grouped.items()]}


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
    """SE(J)=sd(J*): resample sites within h, then realizations within each site."""
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


def paired_difference(blocks_a, blocks_b, design, *, bootstrap_draws=1000, bootstrap_seed=23,
                      objective='device_junction_qps_per_eV'):
    """D[i,r]=b_A-b_B; dJ=sum_h W_h*mean_{i in h,r}(D), with matched streams."""
    a=prepare_counts(blocks_a,design); b=prepare_counts(blocks_b,design)
    historical=objective in ('total_qps_per_primary','total_QPs_per_primary','junction_qps_per_primary')
    if historical:
        av=a['totals']/design['events_per_task']; bv=b['totals']/design['events_per_task']
        groups=[('surface',list(range(len(av))),1.)]
    else:
        if objective not in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy') or a['weighted'] is None or b['weighted'] is None:
            raise ValueError('paired objective requires complete electrode counts')
        av=a['weighted']; bv=b['weighted']; groups=a['groups']
    delta=av-bv; b=bv
    regions=[(label,positions,weight,delta[positions]) for label,positions,weight in groups]
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


def nested_spatial_difference(coarse_blocks, fine_blocks, coarse_design, fine_design, *,
                              same_random_streams=True, bootstrap_draws=1000,
                              bootstrap_seed=23, relative_limit=.05, sigma_limit=2.):
    """Estimate dJ=J_fine-J_coarse for nested, stratified source positions.

    Shared sites are resampled jointly; their realizations are paired only when
    explicitly declared to share streams. In region h the two means retain their
    own denominators n_coarse,h and n_fine,h. The hierarchical interval describes
    this nested-layout contrast, not independent position-layout uncertainty.
    Passing requires interval equivalence globally and in every refined region;
    a statistically resolved shift can still be smaller than the physical margin.
    Requested matching seeds do not guarantee identical transport executions.
    """
    if not isinstance(bootstrap_draws, int) or bootstrap_draws < 2:
        raise ValueError('at least two integer resampling draws required')
    if not math.isfinite(relative_limit) or relative_limit <= 0 or not math.isfinite(sigma_limit) or sigma_limit <= 0:
        raise ValueError('positive finite relative and sigma limits required')
    a=prepare_counts(coarse_blocks,coarse_design); b=prepare_counts(fine_blocks,fine_design)
    coarse_design=a['design']; fine_design=b['design']
    if a['weighted'] is None or b['weighted'] is None:
        raise ValueError('nested spatial comparison requires complete electrode counts')
    if coarse_design['stratum_weights'] != fine_design['stratum_weights']:
        raise ValueError('nested comparison requires the same physical regions and area weights')
    if not np.array_equal(coarse_design['electrode_weights'],fine_design['electrode_weights']) or coarse_design['gun_energy_eV'] != fine_design['gun_energy_eV']:
        raise ValueError('nested comparison requires the same electrode weights and source energy')
    if same_random_streams and (not np.array_equal(coarse_design['replica_ids'],fine_design['replica_ids']) or coarse_design['events_per_task'] != fine_design['events_per_task']):
        raise ValueError('shared streams require matching replica IDs and events per task')
    identities=[]
    for design in (coarse_design,fine_design):
        ids=design.get('site_ids')
        if ids is None:
            sites=np.asarray(design.get('sites_mm',[]),dtype=float)
            if sites.ndim != 2 or len(sites) != len(design['position_strata']) or not np.isfinite(sites).all():
                raise ValueError('nested comparison requires explicit site_ids or finite sites_mm')
            ids=[tuple(site) for site in sites]
        if len(ids) != len(design['position_strata']) or len(set(ids)) != len(ids):
            raise ValueError('site identities must be unique and match the position count')
        identities.append(ids)
    fine_index={site:i for i,site in enumerate(identities[1])}
    if not set(identities[0]) <= set(fine_index):
        raise ValueError('every coarse site must be present in the fine design')
    shared=np.asarray([fine_index[site] for site in identities[0]],dtype=int)
    if any(coarse_design['position_strata'][i] != fine_design['position_strata'][j] for i,j in enumerate(shared)):
        raise ValueError('shared sites must retain their physical region')
    estimates_a=summarize(a,diagnostics=False,uncertainty=False)
    estimates_b=summarize(b,diagnostics=False,uncertainty=False)
    coarse_value=estimates_a['value']; fine_value=estimates_b['value']
    if fine_value <= 0:
        raise ValueError('positive fine yield required for relative convergence')
    rng=np.random.default_rng(bootstrap_seed)
    region_draws=np.empty((bootstrap_draws,len(a['groups'])))
    regions={}
    fine_groups={label:positions for label,positions,_ in b['groups']}
    for h,(label,positions,weight) in enumerate(a['groups']):
        matched=shared[positions]; extra=np.asarray(sorted(set(fine_groups[label])-set(matched)),dtype=int)
        ca=a['weighted'][positions]; fb=b['weighted'][matched]; added=b['weighted'][extra]
        n_coarse=len(ca); n_fine=n_coarse+len(added)
        for draw in range(bootstrap_draws):
            sites=rng.integers(n_coarse,size=n_coarse)
            replicas=rng.integers(ca.shape[1],size=(n_coarse,ca.shape[1]))
            old=np.take_along_axis(ca[sites],replicas,axis=1).mean(axis=1)
            if not same_random_streams:
                replicas=rng.integers(fb.shape[1],size=(n_coarse,fb.shape[1]))
            retained=np.take_along_axis(fb[sites],replicas,axis=1).mean(axis=1)
            new_sum=float(retained.sum())
            if len(added):
                sites=rng.integers(len(added),size=len(added))
                replicas=rng.integers(added.shape[1],size=(len(added),added.shape[1]))
                new_sum+=float(np.take_along_axis(added[sites],replicas,axis=1).mean(axis=1).sum())
            region_draws[draw,h]=new_sum/n_fine-float(old.mean())
        old=estimates_a['means_by_stratum'][label]; new=estimates_b['means_by_stratum'][label]
        interval=np.percentile(region_draws[:,h],[2.5,97.5]).tolist()
        margin=relative_limit*abs(new)
        regions[label]={'coarse_mean':old,'fine_mean':new,'difference':new-old,
                        'standard_error':float(region_draws[:,h].std(ddof=1)),
                        'confidence_interval_95':interval,'n_coarse':n_coarse,'n_fine':n_fine,
                        'n_added':len(added),'area_weight':weight,'refinement_assessed':len(added) > 0,
                        'relative_shift':abs(new-old)/abs(new) if new else (0. if old == 0 else None),
                        'equivalent_within_margin':interval[0] > -margin and interval[1] < margin}
    draws=region_draws@np.asarray([weight for _,_,weight in a['groups']])
    difference=fine_value-coarse_value; se=float(draws.std(ddof=1))
    interval=np.percentile(draws,[2.5,97.5]).tolist(); margin=relative_limit*fine_value
    checks={'relative_shift':abs(difference)/fine_value <= relative_limit,
            'within_difference_error':abs(difference) <= sigma_limit*se,
            'confidence_interval_within_margin':interval[0] > -margin and interval[1] < margin,
            'refined_regions_within_margin':all(row['equivalent_within_margin'] for row in regions.values() if row['refinement_assessed']),
            'every_region':all(row['relative_shift'] is not None and row['relative_shift'] <= relative_limit for row in regions.values())}
    ra=estimates_a['by_replica']; rb=estimates_b['by_replica']
    transport=compare_repeated(rb,ra,paired=same_random_streams,relative_margin=relative_limit) if min(len(ra),len(rb)) >= 2 else {'status':'fewer than two realizations'}
    transport['interpretation']='Transport uncertainty conditional on these sites; excludes source-position integration uncertainty.'
    return {'coarse_value':coarse_value,'fine_value':fine_value,'difference':difference,
            'standard_error':se,'confidence_interval_95':interval,'relative_shift':abs(difference)/fine_value,
            'relative_difference':difference/fine_value,'equivalence_margin':margin,
            'equivalent_within_margin':checks['confidence_interval_within_margin'],
            'checks':checks,'passed':checks['confidence_interval_within_margin'] and checks['refined_regions_within_margin'],'regions':regions,
            'same_random_streams':bool(same_random_streams),'fixed_site_transport_difference':transport,
            'uncertainty_method':'paired site bootstrap with paired realizations' if same_random_streams else 'paired site bootstrap with independent realizations',
            'limitation':'Sites are treated as exchangeable within each region, with fixed area weights. Independent randomized position layouts are required to assess integration uncertainty and layout bias.'}


def sensitivity(samples, outcomes, names=None, *, problem=None, method="pearson", log_outcome=False, seed=7, num_levels=4):
    """Pearson correlations or Morris EE_j=[J(u+delta*e_j)-J(u)]/delta.

    u is normalized, e_j a unit vector; mu=mean(EE), mu_star=mean(abs(EE)), sigma=sd(EE).
    """
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


def project_materials(target, candidates, feature_spec, weights=None, top=10):
    """Rank materials by coverage, then D^2=sum_j a_j*z_j^2/sum_j a_j.

    z_j=[g(m_j)-g(x_j)]/s_j; a_j weights, g=id/log, s_j robust catalog scale.
    """
    weights=weights or {}; scales={}; rows=[]
    for name,mode in feature_spec.items():
        if mode not in ('log','linear'): raise ValueError(f'unknown feature scale {mode!r}')
        values=[float(c['features'][name]) for c in candidates if c['features'].get(name) is not None and np.isfinite(c['features'][name]) and (mode!='log' or c['features'][name]>0)]
        values=np.log(values) if mode=='log' else np.asarray(values)
        mad=float(np.median(np.abs(values-np.median(values))))*1.4826 if len(values)>=2 else 0.
        scales[name]=mad if mad>0 else (float(np.std(values)) or 1.) if len(values)>=2 else 1.
    for candidate in candidates:
        num=den=0.; missing=[]; z_values={}
        for name,mode in feature_spec.items():
            t,c=target.get(name),candidate['features'].get(name)
            if t is None or c is None or not np.isfinite(t) or not np.isfinite(c) or (mode=='log' and (t<=0 or c<=0)):
                missing.append(name); continue
            weight,scale=float(weights.get(name,1.)),scales[name]
            if not math.isfinite(weight) or weight<0 or not math.isfinite(scale) or scale<=0:
                raise ValueError('feature weights must be non-negative and scales positive')
            z=(math.log(c)-math.log(t))/scale if mode=='log' else (float(c)-float(t))/scale
            num+=weight*z*z; den+=weight; z_values[name]=round(z,4)
        if not z_values or den==0: continue
        matched,total=len(z_values),len(feature_spec)
        rows.append({'distance':math.sqrt(num/den),'matched':matched,'total':total,'unmatched_features':missing,
                     'z':z_values,'coverage':matched/total,'stratum':f'{matched}/{total}','id':candidate['id'],'candidate':candidate})
    rows.sort(key=lambda row:(-row['matched'],row['distance']))
    return {'rows':rows[:top],'scales':scales,'missing_features':{name:sum(name in row['unmatched_features'] for row in rows) for name in feature_spec},
            'n_candidates':len(rows),'fully_covered':sum(row['matched']==row['total'] for row in rows)}


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
    """dJ=mean(a)-mean(b); Student-t interval dJ +/- t*SE(dJ).

    Equivalence requires the entire interval inside +/- relative_margin*abs(mean(b)).
    """
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


def transport_precision(by_replica, *, confidence=.95):
    """Conditional transport precision: SE(J)=sd(J_r)/sqrt(R), with fixed sites.

    J_r is the complete area-weighted layout mean for realization r. Replicas
    must use independent streams; this interval excludes position integration.
    """
    from scipy.stats import t
    values=np.asarray(by_replica,dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError('at least two finite independent replica means required')
    if not 0 < confidence < 1:
        raise ValueError('confidence must be in (0,1)')
    mean=float(values.mean()); se=float(values.std(ddof=1)/math.sqrt(len(values)))
    radius=float(t.ppf((1+confidence)/2,len(values)-1)*se)
    return {'mean':mean,'standard_error':se,'relative_standard_error':se/abs(mean) if mean else None,
            'confidence':confidence,'confidence_interval':[mean-radius,mean+radius],
            'n_replicas':len(values),'degrees_of_freedom':len(values)-1,
            'uncertainty_method':'Student-t interval across independent replicas at fixed source positions'}


def analysis_design(study):
    design=study['design']; sampling=study['sampling']
    return {'position_strata':design['stratum'],'stratum_weights':design['stratum_weights'],
            'replica_ids':list(range(sampling['replicas'])),'events_per_task':sampling['events_per_task'],
            'gun_energy_eV':study['fixed']['gun_energy_eV'],'electrode_weights':design['electrode_weights'],
            'design_hash':design['design_hash'],'injection_law':design.get('injection_law','uniform_surface'),
            'sites_mm':design['sites_mm']}


def score_output(result, prepared, task, *, time_resolved=False, score_hash=None):
    """Hit chunks -> N[i,r,e]; retain N[e,j] time bins only when requested."""
    fixed=prepared['fixed']; totals=np.zeros(len(fixed['electrode_x_mm'])); n_hits=0; matrix=None
    surface=fixed['setSubThickness_um']*1e-6/2
    gap=fixed['setTopGap']
    for energy,x,y,z,arrival in records.read_hits(result['output']):
        count=physics.qp_counts(energy,x,y,z,arrival,gap,fixed['electrode_x_mm'],fixed['electrode_y_mm'],surface,time_resolved=time_resolved)
        if time_resolved:
            times,qps=count
            matrix=qps if matrix is None else matrix+qps
            totals+=qps.sum(axis=1)
        else: totals+=count
        n_hits+=len(energy)
    block={'position':task['position'],'replica':task['replica'],'events':task['events'],'total_qps':float(totals.sum()),
           'per_electrode_qps':totals.tolist(),'n_hits':n_hits}
    if time_resolved:
        if matrix is None:
            times=np.linspace(0,300000,1001); matrix=np.zeros((len(totals),1001))
        destination=Path(result['output']).with_name('qps.npz')
        np.savez_compressed(destination,time_ns=times,qps=matrix)
        block['time_data']=str(destination)
        block['time_data_sha256']=records.sha256_file(destination)
    block['score_hash']=score_hash or scoring_identity()
    return {**result,**block}


def density_response(blocks, prepared, defaults=None, cache=None, *, from_hits=False):
    """S=sum_e integral Gamma_e(t) dt, in MHz*us, from the pooled QP source.

    Evolve pooled and per-realization sources separately; the ODE is nonlinear.
    """
    ode=dict(defaults or {row[0]:row[1] for row in records.load_document(ROOT/'parameters.yaml')['morris']['QPDE_params']})
    ode.update(prepared.get('ode',{})); fixed=prepared['fixed']
    if not blocks: raise ValueError('density evolution needs complete simulation blocks')
    source,checksum=('output','sha256') if from_hits else ('time_data','time_data_sha256')
    geometry=(fixed['setTopGap'],tuple(fixed['electrode_x_mm']),tuple(fixed['electrode_y_mm']),fixed['setSubThickness_um']) if from_hits else None
    key=(from_hits,geometry,tuple((block[source],block[checksum],block['replica'],block['events']) for block in blocks))
    saved=cache.get(key) if cache is not None else None
    signatures=[]
    for block in blocks:
        stat=Path(block[source]).stat()
        signatures.append((stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
    for i,block in enumerate(blocks):
        if saved is None or signatures[i]!=saved[3][i]:
            if records.sha256_file(block[source])!=block[checksum]: raise ValueError('raw hit checksum differs' if from_hits else 'time data checksum differs')
            stat=Path(block[source]).stat()
            if signatures[i]!=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns):
                raise ValueError('density input changed while reading')
    if saved is None:
        replicas=sorted({block['replica'] for block in blocks}); sums={}; events={}; pooled=None; times=None
        for i,block in enumerate(blocks):
            if from_hits:
                time_grid=np.linspace(0,300000,1001); qps=np.zeros((len(fixed['electrode_x_mm']),1001))
                for columns in records.read_hits(block['output']):
                    time_grid,counts=physics.qp_counts(*columns,fixed['setTopGap'],fixed['electrode_x_mm'],fixed['electrode_y_mm'],fixed['setSubThickness_um']*1e-6/2,time_resolved=True)
                    qps+=counts
            else:
                with np.load(block['time_data'],allow_pickle=False) as data:
                    time_grid=data['time_ns']; qps=data['qps']
            stat=Path(block[source]).stat()
            if signatures[i]!=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns):
                raise ValueError('density input changed while reading')
            if times is None: times=time_grid; pooled=np.zeros_like(qps)
            elif not np.array_equal(times,time_grid): raise ValueError('recorded time grids differ')
            replica=block['replica']
            if replica not in sums: sums[replica]=np.zeros_like(qps); events[replica]=0
            pooled+=qps; sums[replica]+=qps; events[replica]+=block['events']
        saved=(times,[pooled]+[sums[r] for r in replicas],[sum(events.values())]+[events[r] for r in replicas],signatures)
        if cache is not None:
            cache.clear(); cache[key]=saved
    elif saved[3]!=signatures:
        saved=(*saved[:3],signatures)
        cache[key]=saved
    times,arrays,event_counts=saved[:3]; outcomes=[]
    for qps,events in zip(arrays,event_counts):
        t,dg=physics.calculate_xqps(times,qps,fixed['setTopGap']/4.135667696e-15,ode['f_01'],ode['r'],ode['s'],ode['I_ph'],ode['pt'],ode['n_cooper'],fixed['electrode_height_um'],fixed['electrode_width_um'],fixed['setTopThickness_um'],events)
        outcomes.append(float(np.trapezoid(dg,t,axis=1).sum()))
    return {'integrated_density':outcomes[0],'integrated_density_by_replica':outcomes[1:],'density_parameters':ode,
            'density_sites':len({row['position'] for row in blocks})}


def analyse_results(study, rows):
    complete=[row for row in rows if row['status']=='complete']
    objective=study['analysis']['objective']; design=analysis_design(study); comparisons=[]
    reference=rows[0] if rows and rows[0]['status']=='complete' else None
    paired_requested=study['analysis'].get('comparisons',True) and (study['kind'] not in ('search','sensitivity') or (study['kind']=='search' and study['analysis'].get('diagnostics',False)))
    if reference is not None and paired_requested:
        for candidate in complete:
            if candidate is reference: continue
            if objective=='integrated_density':
                delta=candidate['value']-reference['value']
                paired={'value':delta,'standard_error':None,'relative_to_b':delta/reference['value'] if reference['value'] else None,
                        'z':None,'units':candidate.get('units'),'uncertainty_status':'aggregate nonlinear density evolution has no measured paired uncertainty'}
            else:
                paired=paired_difference(candidate['blocks'],reference['blocks'],design,objective=objective,
                                                   bootstrap_draws=int(study['analysis'].get('bootstrap_draws',1000)))
            comparisons.append({'candidate':candidate['name'],'reference':reference['name'],**paired})
    geometry={key:value for key,value in study['fixed'].items() if key.startswith(('electrode_','setSub'))}
    report={'id':study['id'],'kind':study['kind'],'objective':objective,'events_total_per_candidate':study['events_total'],
            'sites':len(study['design']['sites_mm']),'replicas':study['sampling']['replicas'],
            'events_per_task':study['sampling']['events_per_task'],'design':design,'results':rows,'comparisons':comparisons,
            'geometry':geometry,'sites_mm':study['design']['sites_mm'],
            'streams':{k:study['sampling'][k] for k in ('seed_base','seed_bank','position_seed')},
            'complete':bool(rows) and len(complete)==len(rows),'limitations':['Calibrated interface model.','Property targets need measured-material validation.',
                                                            'QP yield is not a logical-error probability.']}
    if study['kind']=='sensitivity':
        recorded=study.get('morris_design') and all(all(key in row for key in ('inputs','trajectory','design_index')) for row in rows)
        if not recorded: raise ValueError('Morris reanalysis requires recorded sample inputs')
        candidates=sorted(rows,key=lambda row:row['design_index'])
        lookup={row['name']:row for row in rows}
        morris=study['morris_design']; problem=morris['problem']; names=problem['names']
        grouped={}
        for candidate in candidates: grouped.setdefault(candidate['trajectory'],[]).append(candidate)
        missing=[trajectory for trajectory,points in grouped.items()
                 if len(points)!=len(names)+1 or any(lookup.get(p['name'],{}).get('status')!='complete' for p in points)]
        if missing:
            report['sensitivity']={'status':'incomplete','missing_trajectories':missing,'declared_trajectories':len(grouped),
                                   'reason':'missing outcomes cannot be removed from Morris trajectories'}
        else:
            matrix=[[candidate['inputs'][name] for name in names] for candidate in candidates]
            response=[lookup[candidate['name']]['integrated_density'] if objective=='integrated_density' else lookup[candidate['name']]['value'] for candidate in candidates]
            method=study['analysis'].get('sensitivity_method','morris')
            result=sensitivity(matrix,response,names,problem=problem,method=method,
                                        log_outcome=study['analysis'].get('log_outcome',method=='pearson'),
                                        seed=morris['seed'],num_levels=morris['levels'])
            report['sensitivity']={**result,'status':'complete','response':objective,'morris_design':morris}
    if study['kind']=='tolerance':
        if reference is None:
            report['tolerance']={'locally_converged':False,'reason':'reference measurement is incomplete'}
        else:
            moves={}; paired={}; compared={row['candidate']:row for row in comparisons}
            for row in rows[1:]:
                name,side=row['name'].rsplit('_',1)
                label=name+(':minus' if side=='-1' else ':plus')
                moves[label]={'value':row.get('value')}
                if row['name'] in compared: paired[label]=compared[row['name']]
            for name in study.get('tolerance',{}):
                moves.setdefault(name+':minus',{'value':None}); moves.setdefault(name+':plus',{'value':None})
            varied={label.rsplit(':',1)[0] for label in moves}
            coordinates=physics.to_unit(reference['parameters'],study['space']) if varied<=set(study['space']['names']) else None
            report['tolerance']=tolerance_summary(reference,moves,paired=paired,coordinates=coordinates,names=study['space']['names'])
            report['tolerance']['untested_moves']=study.get('tolerance_skipped',[])
    return report


def read_analysis(path, output=None, settings=None):
    data=records.load_result(path,include_inputs=True); study=data['study']
    if data.get('raw_files_present') is False:
        raise ValueError('recover the recorded archive before raw-data reanalysis')
    expected=study.pop('resolved_hash')
    if records.content_hash(study)!=expected: raise ValueError('resolved study was changed after recording')
    study['resolved_hash']=expected
    if settings: study['analysis'].update(records.load_document(settings))
    declarations=data['candidate_inputs']; identity=scoring_identity(); design=analysis_design(study)
    defaults={entry[0]:entry[1] for entry in records.load_document(ROOT/'parameters.yaml')['morris']['QPDE_params']}
    cache={}
    compact_rows=[]
    paired_requested=study['analysis'].get('comparisons',True) and (study['kind'] not in ('search','sensitivity') or (study['kind']=='search' and study['analysis'].get('diagnostics',False)))
    for row in data['results']:
        if row.get('status')!='complete': continue
        compact='blocks' not in row
        if compact: compact_rows.append(row)
        stored=records.load_result(path,include_inputs=True,candidate_id=row['candidate_id'])['results'][0] if compact else row
        blocks=[]; from_hits=False
        for block in stored['blocks']:
            if not Path(block['output']).is_file() or records.sha256_file(block['output'])!=block['sha256']:
                raise ValueError('saved measurements no longer match their raw files')
            if block.get('score_hash')!=identity:
                from_hits=True
                block={key:value for key,value in block.items() if key not in ('time_data','time_data_sha256')}
                block=score_output(block,declarations[row['candidate_id']],block,score_hash=identity)
            if not block.get('time_data') or not block.get('time_data_sha256'): from_hits=True
            blocks.append(block)
        counts=prepare_counts(blocks,design); options=study['analysis']; diagnostics=bool(options.get('diagnostics',study['kind']!='search'))
        if not compact or paired_requested: row['blocks']=blocks
        row['estimate']=summarize(counts,bootstrap_draws=int(options.get('bootstrap_draws',1000)),
            tail_bootstrap_draws=int(options.get('tail_bootstrap_draws',500)),diagnostics=diagnostics,
            uncertainty=options['objective'] in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy') or diagnostics)
        row['historical']=summarize(counts,objective='total_qps_per_primary',diagnostics=diagnostics)
        objective=options['objective']
        selected=row['estimate'] if objective in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy') else row['historical']
        row.update(value=selected['value'],standard_error=selected['standard_error'],units=selected['units'])
        if objective=='integrated_density':
            row.update(density_response(blocks,declarations[row['candidate_id']],defaults,cache,from_hits=from_hits))
            row.update(value=row['integrated_density'],standard_error=None,units='MHz us')
    report=analyse_results(study,data['results'])
    for row in compact_rows: row.pop('blocks',None)
    if 'search_counts' in data:
        report['search_counts']=data['search_counts']
        if study.get('search',{}).get('measured_evaluations',False):
            report['complete']=bool(data['search_counts']['target_reached'])
            report['status']='complete' if report['complete'] else 'incomplete'
        if 'incomplete_reason' in data: report['incomplete_reason']=data['incomplete_reason']
    if output: records.write_json(output,report)
    return report


def compare_runs(first, second, output=None, margin=.05):
    a=records.load_result(first); b=records.load_result(second)
    if a['objective']!=b['objective'] or a['design']!=b['design']:
        raise ValueError('comparison requires the same objective and site/realization design')
    for key in ('geometry','sites_mm'):
        if key in a or key in b:
            if a.get(key)!=b.get(key): raise ValueError(f'comparison differs in physical {key}')
    if not np.isfinite(margin) or margin<=0: raise ValueError('relative equivalence margin must be positive and finite')
    rows=[]
    by_name={row['name']:row for row in b['results']}
    if set(by_name)!={row['name'] for row in a['results']}: raise ValueError('comparison requires matching candidate sets')
    for row in a['results']:
        other=by_name.get(row['name'])
        if row['status']!='complete' or not other or other['status']!='complete':
            raise ValueError('comparison requires complete matched candidates')
        if row['parameters']!=other['parameters']:
            raise ValueError('comparison material properties differ')
        if a['objective']=='integrated_density':
            rows.append({'name':row['name'],'difference':row['value']-other['value'],
                         'equivalent_within_margin':False,'uncertainty_status':'per-realization density evolution is required to measure uncertainty'})
            continue
        paired=paired_difference(row['blocks'],other['blocks'],a['design'],objective=a['objective'])
        ra=summarize(row['blocks'],a['design'],objective=a['objective'],diagnostics=False,uncertainty=False)['by_replica']
        rb=summarize(other['blocks'],b['design'],objective=b['objective'],diagnostics=False,uncertainty=False)['by_replica']
        matched=compare_repeated(ra,rb,relative_margin=margin,paired=True) if min(len(ra),len(rb))>=2 else {'status':'fewer than two realizations','equivalent_within_margin':False}
        repeated=compare_repeated(ra,rb,relative_margin=margin) if min(len(ra),len(rb))>=2 else {'status':'fewer than two realizations'}
        repeated['interpretation']='Welch interval ignores pairing; a diagnostic check, not a strict independent-sample experiment when random streams are shared.'
        rows.append({'name':row['name'],'paired':paired,'paired_realization_check':matched,'realization_check':repeated,
                     'equivalent_within_margin':matched['equivalent_within_margin']})
    report={'comparison':'selected response with matched sites and realizations','relative_equivalence_margin':margin,
            'objective':a['objective'],'same_random_streams':a.get('streams')==b.get('streams'),'results':rows}
    if output: records.write_json(output,report)
    return report


def summarize(blocks, design=None, *, objective='device_junction_qps_per_eV', diagnostics=True,
              uncertainty=True, bootstrap_draws=1000, bootstrap_seed=7,
              tail_bootstrap_draws=500, tail_bootstrap_seed=11, robustness=1., alpha=1., beta=1.):
    """J=sum_h W_h*mean_{i in h,r}(b[i,r]); report J and hierarchical SE(J).

    Historical unweighted QP counts retain their separately named responses.
    """
    data=prepare_counts(blocks,design); design=data['design']; totals=data['totals'].T
    events=design['events_per_task']; historical=objective in ('total_qps','total_qps_per_primary','total_QPs_per_primary',
        'junction_qps_per_primary','robust_mean_plus_se','max_electrode_qps_per_primary','weighted_sum')
    if historical:
        n_sim=events*totals.size; q=totals.ravel()/events
        se_total=float(np.sqrt(totals.shape[0]*totals.var(axis=0,ddof=1).sum())) if totals.shape[0]>1 else None
        se_block=float(q.std(ddof=1)/np.sqrt(q.size)) if q.size>1 else 0.
        value=float(totals.mean()/events)
        output={'total_qps':float(totals.sum()),'total_qps_per_primary':value,'junction_qps_per_primary':value,
                'total_qps_standard_error':se_total,'total_qps_per_primary_standard_error':se_total/n_sim if se_total is not None else None,
                'robust_mean_plus_se':float(q.mean()+robustness*se_block),'across_block_standard_error':se_block,
                'n_sites':totals.shape[1],'n_replicas':totals.shape[0],'by_replica':(totals.mean(axis=1)/events).tolist()}
        if diagnostics:
            rng=np.random.default_rng(12345); site_totals=totals.sum(axis=0)
            draws=site_totals[rng.integers(0,len(site_totals),size=(2000,len(site_totals)))].sum(axis=1)
            output.update(site_bootstrap_ci_total=np.percentile(draws,[2.5,97.5]).tolist(),
                          p90_position=float(np.percentile(totals.mean(axis=0)/events,90)))
        if data['counts'] is not None:
            electrode_totals=data['counts'].sum(axis=(0,1))
            output['max_electrode_qps_per_primary']=float(electrode_totals.max()/n_sim)
            output['weighted_sum']=alpha*value+beta*output['max_electrode_qps_per_primary']
        key='total_qps_per_primary' if objective=='total_QPs_per_primary' else objective
        if key not in output: raise ValueError(f'unknown or unavailable historical objective {objective!r}')
        se=se_total if key=='total_qps' else se_block if key=='robust_mean_plus_se' else se_total/n_sim if key in ('total_qps_per_primary','junction_qps_per_primary') and se_total is not None else None
        output.update(value=output[key],standard_error=se if uncertainty else None,objective=objective,
                      units='summed_junction_QPs_per_primary' if key!='total_qps' else 'summed_junction_QPs')
        return output
    if objective not in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy') or data['weighted'] is None:
        raise ValueError(f'per-electrode response is unavailable for objective {objective!r}')
    burden=data['weighted']; regions=[(label,positions,weight,burden[positions]) for label,positions,weight in data['groups']]
    per_site=burden.mean(axis=1)
    means={label:float(per_site[positions].mean()) for label,positions,_,_ in regions}
    value=sum(weight*means[label] for label,_,weight,_ in regions)
    se=_resample(regions,bootstrap_draws,bootstrap_seed) if uncertainty else None
    output={'value':float(value),'standard_error':se,'relative_standard_error':se/value if se is not None and value else None,
            'means_by_stratum':means,'sites_by_stratum':{label:len(positions) for label,positions,_,_ in regions},
            'weights_by_stratum':dict(design['stratum_weights']),'naive_equal_site_mean':float(per_site.mean()),
            'maximum_site_leverage':float(max(weight*per_site[i]/len(positions)/value if value else 0. for _,positions,weight,_ in regions for i in positions)),
            'n_sites':len(per_site),'n_replicas':burden.shape[1],'events_per_task':events,'design_hash':design['design_hash'],
            'by_replica':np.sum([weight*matrix.mean(axis=0) for _,_,weight,matrix in regions],axis=0).tolist(),
            'units':'weighted_junction_QPs_per_injected_eV'}
    if diagnostics:
        worst=data['worst']; per_site=worst.mean(axis=1)
        output['spatial_r95']=_tail_mean([(per_site[i],weight/len(positions)) for _,positions,weight,_ in regions for i in positions])
        output['spatial_r95_standard_error']=_resample([(label,positions,weight,worst[positions]) for label,positions,weight,_ in regions],tail_bootstrap_draws,tail_bootstrap_seed,tail=True) if uncertainty else None
    return output


def scoring_identity():
    return records.content_hash({'physics':records.sha256_file(physics.__file__),
                                 'calculation':inspect.getsource(score_output)})
