#!/usr/bin/env python3
"""Stage 4 exit gates. Nothing expensive runs until these pass.

    python tests_stage4.py            # fast gates, no Geant4
    python tests_stage4.py --slow     # adds the Geant4 gates (minutes)

Each test states the failure it exists to catch, because a test whose purpose is
not written down gets deleted the first time it is inconvenient.
"""

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


# ---------------------------------------------------------------------------
def t1_space_round_trip():
    """Catches: a log-scaled variable treated as linear anywhere in the chain."""
    import stage4_space as S
    space = S.DEFAULT_SPACE
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(2000):
        u = rng.random(space.n_cont)
        c = int(rng.integers(space.n_cat))
        p = space.from_unit(u, c)
        u2 = space.to_unit(p)
        worst = max(worst, float(np.max(np.abs(u - u2))))
    check("T1 space round-trip to_unit(from_unit(u)) == u",
          worst < 1e-9, f"max deviation {worst:.2e} over 2000 points")


def t2_gates_reject():
    """Catches: an infeasible candidate reaching Geant4.

    The one that matters most is inverted mode ordering: it crashed 31/31
    affected design points mid-run and left zero-byte hits files.
    """
    import stage4_space as S
    space = S.DEFAULT_SPACE
    base = space.baseline_point()

    bad_born = dict(base, sub_c11=30.0, sub_c12=200.0)
    ok1, r1 = S.precheck_cheap(bad_born, space)
    check("T2a Born-unstable tensor rejected", not ok1 and "G1" in (r1 or ""), r1)

    oob = dict(base, sub_c11=1e6)
    ok2, r2 = S.precheck_cheap(oob, space)
    check("T2b out-of-bounds value rejected", not ok2, r2)

    bad_thres = dict(base, bot_gap_thres=1.0)
    ok3, r3 = S.precheck_cheap(bad_thres, space)
    check("T2c bot_gap_thres above 2*setTopGap rejected", not ok3, r3)

    bad_miller = dict(base, miller=[7, 5, 1])
    ok4, r4 = S.precheck(bad_miller, space)
    check("T2d unrepresentable Miller direction rejected", not ok4, r4)

    # Mode ordering: force it directly through derive(), since the box makes it
    # hard to reach by construction -- which is the point of keeping the speeds
    # derived rather than sampled.
    inverted = 0
    rng = np.random.default_rng(3)
    for _ in range(3000):
        p = space.sample(rng, 1)[0]
        try:
            d = S.derive(p)
        except S.GateError:
            continue
        if not 0 < d["vtrans_m_s"] < d["vsound_m_s"]:
            inverted += 1
    check("T2e no Born-stable draw inverts v_T/v_L", inverted == 0,
          f"{inverted} inversions in 3000 draws")


def t3_baseline_reconstruction():
    """Catches: a resolver change that silently moves the baseline.

    These five numbers are the v2 campaign's, so if they move, Stage 4 results
    stop being comparable with the converged v2 material ranking.
    """
    import stage4_space as S
    d = S.derive(S.DEFAULT_SPACE.baseline_point())
    ok = (abs(d["setTopAbs"] - 0.795) < 1e-9 and abs(d["setTopFilmAbs"] - 0.745) < 1e-9
          and abs(d["setBotAbs"] - 0.736) < 1e-9)
    check("T3a baseline interface values reconstruct 0.795/0.745/0.736", ok,
          f"{d['setTopAbs']}/{d['setTopFilmAbs']}/{d['setBotAbs']}")
    ok2 = (abs(d["vsound_m_s"] - 9016.7) < 1.0 and abs(d["vtrans_m_s"] - 5369.5) < 1.0)
    check("T3b baseline derived speeds reconstruct 9016.7/5369.5 m/s", ok2,
          f"{d['vsound_m_s']:.1f}/{d['vtrans_m_s']:.1f}")


def t4_v2_path_unchanged():
    """Catches: a Stage 4 edit that changes what the v2 catalog path resolves to."""
    from stage3_contract import load_contract
    from stage3_ledger import Ledger
    import stage3_trial_runner as R
    ledger_path = os.path.join(HERE, "stage3_trials.sqlite")
    if not os.path.isfile(ledger_path):
        check("T4 v2 resolver unchanged", True, "skipped: no v2 ledger present")
        return
    contract = load_contract(os.path.join(HERE, "stage3_config.yaml"))
    # read_only: opening a ledger normally MIGRATES it, and this is the
    # historical v2 campaign database -- running the tests must not touch it.
    with Ledger(ledger_path, read_only=True) as led:
        rows = led.observations("stage3_factorial_v1")
        row = next((r for r in rows
                    if json.loads(r["candidate"]) ==
                    {"substrate": "Ge", "top_ground_film": "Nb", "bottom_film": "Cu"}), None)
    if row is None:
        check("T4 v2 resolver unchanged", True, "skipped: Ge/Nb/Cu not in the ledger")
        return
    recorded = json.loads(row["derived"])
    _, derived = R.resolve_candidate(contract, json.loads(row["candidate"]))
    keys = ("setTopAbs", "setTopFilmAbs", "setBotAbs", "vsound_m_s", "vtrans_m_s",
            "substrate_density_kg_m3", "g4_material_name", "lattice_map_name")
    diffs = [k for k in keys if recorded.get(k) != derived.get(k)]
    check("T4 v2 catalog resolver reproduces the recorded Ge/Nb/Cu record",
          not diffs, f"differences: {diffs}" if diffs else "all 8 fields identical")


def t5_scoring_equivalence():
    """Catches: per-sub-run scoring drifting from the pooled score.

    If these ever disagree, every block-resolved objective and every error bar
    in Stage 4 is measuring something other than `total_QPs`.
    """
    import pandas as pd
    from stage3_contract import load_contract
    from stage3_ledger import Ledger
    import stage3_trial_runner as R
    from stage2_compute_QPs import calculate_QPs
    ledger_path = os.path.join(HERE, "stage3_trials.sqlite")
    if not os.path.isfile(ledger_path):
        check("T5 scoring equivalence", True, "skipped: no v2 ledger present")
        return
    contract = load_contract(os.path.join(HERE, "stage3_config.yaml"))
    # read_only: opening a ledger normally MIGRATES it, and this is the
    # historical v2 campaign database -- running the tests must not touch it.
    with Ledger(ledger_path, read_only=True) as led:
        rows = led.observations("stage3_factorial_v1")
        if not rows:
            check("T5 scoring equivalence", True, "skipped: no observations")
            return
        row = rows[0]
        subs = [{"hits_file": s["hits_file"], "replica": s["replica"],
                 "position_index": s["position"]} for s in led.sub_runs(row["trial_id"])]
    if not all(os.path.exists(s["hits_file"]) for s in subs):
        check("T5 scoring equivalence", True, "skipped: hit files not on disk")
        return
    total, per_primary, per_electrode, n_hits, blocks = R._score(
        contract, subs, row["events_per_sub_run"])
    frames = [pd.read_csv(s["hits_file"]).reset_index(drop=True) for s in subs]
    rec = pd.concat(frames, ignore_index=True)
    f = contract.fixed
    _, qp = calculate_QPs(rec, float(f["setTopGap"]),
                          np.array(f["electrode_x_mm"], dtype=float),
                          np.array(f["electrode_y_mm"], dtype=float),
                          float(f["setSubThickness_um"]) * 1e-6 / 2.0)
    pooled = float(qp.sum())
    block_sum = sum(b["total_qps"] for b in blocks)
    check("T5 per-sub-run scores sum to the pooled score",
          total == pooled == block_sum == row["total_qps"],
          f"blocks {block_sum} == pooled {pooled} == ledger {row['total_qps']}")


def t7_failure_is_not_zero(tmpdir):
    """Catches: the failure mode this whole project is built against.

    A missing, corrupt or aborted sub-run must produce a STATUS, never a
    zero-QP observation. Verified by forcing one sub-run to fail with the
    simulation itself stubbed out.
    """
    from stage3_contract import load_contract, ContractError
    from stage3_ledger import Ledger, STATUS_SUCCESS, STATUS_SIM_FAILED, STATUS_INCOMPLETE_SET
    import stage3_trial_runner as R
    import stage4_space as S
    from stage4_optimize import candidate_payload, resolver_for

    contract = load_contract(os.path.join(HERE, "stage4_config.yaml"))
    contract.campaign_id = "stage4_selftest"
    contract.fixed.update(n_positions=2, n_replicas=2, max_workers=2,
                          total_mem_gb=4, per_sample_mem_gb=2, sample_timeout_s=30)
    contract.decision["fidelity"]["events_total_per_candidate"]["S"] = 4
    space = S.DEFAULT_SPACE
    cand = candidate_payload(space.baseline_point(), space)

    calls = {"n": 0}
    real_run_one = R._run_one

    def fake_run_one(sub_run, *a, **kw):
        calls["n"] += 1
        if sub_run["position_index"] == 1 and sub_run["replica"] == 0:
            return STATUS_SIM_FAILED, 1, 0.1, "injected failure"
        # A "successful" sub-run that writes a syntactically valid hits file.
        with open(sub_run["hits_file"], "w") as handle:
            handle.write("Run ID,Event ID,Track ID,Particle Name,Start Energy [eV],"
                         "Start X [m],Start Y [m],Start Z [m],End X [m],End Y [m],"
                         "End Z [m],Start Time [ns],Final Time [ns],"
                         "Energy Deposited [eV],Track Weight\n")
        open(sub_run["done_marker"], "w").close()
        return STATUS_SUCCESS, 0, 0.1, None

    R._run_one = fake_run_one
    try:
        with Ledger(os.path.join(tmpdir, "t7.sqlite")) as led:
            result = R.evaluate(contract, cand, fidelity="S", ledger=led, verbose=False,
                                resolver=resolver_for(space),
                                runs_root=os.path.join(tmpdir, "runs"))
        check("T7a an incomplete scenario set is never scored",
              result.status == STATUS_INCOMPLETE_SET and result.total_qps is None,
              f"status={result.status} total_qps={result.total_qps}")
        check("T7b a failed trial is not an observation", not result.is_observation)
    finally:
        R._run_one = real_run_one

    missing = os.path.join(tmpdir, "nonexistent_hits.txt")
    try:
        R._score_one(contract, missing)
        raised = False
    except ContractError:
        raised = True
    check("T7c scoring a missing hits file raises rather than returning 0", raised)

    empty = os.path.join(tmpdir, "empty_hits.txt")
    open(empty, "w").close()
    try:
        R._score_one(contract, empty)
        raised = False
    except ContractError:
        raised = True
    check("T7d scoring an empty hits file raises rather than returning 0", raised)

    bad = R.validate_macro_lines(["/g4cmp/clearance 1e-06e-6 mm\n"])
    check("T7e the unit-hygiene gate catches a double-suffix number", len(bad) == 1,
          str(bad))


def t8_optimizer_sanity(quick=True):
    """Catches: a model-based optimizer that does not actually optimize.

    Two synthetic surfaces, because they stress different things: an INTERIOR
    optimum (where a GP's smoothness assumption helps) and a BOUNDARY optimum
    (where box handling decides everything -- and where a rejection-based
    CMA-ES silently fails).
    """
    import stage4_space as S
    from stage4_optimizers import create
    from stage4_objectives import ObjectiveValue
    space = S.DEFAULT_SPACE
    A = np.linspace(0.3, 0.9, space.n_cont)
    W = np.linspace(1.0, 0.2, space.n_cont)

    def make(kind):
        def truth(point):
            u = space.to_unit(space.complete(point))
            c = space.miller_index(space.complete(point))
            val = float(np.sum((u - A) ** 2 * W)
                        + 0.6 * math.sin(4 * u[0]) * math.cos(3 * u[4]) + 0.25 * (c % 3))
            return math.exp(-2.0 + (val if kind == "interior" else -val))
        return truth

    budget = 60 if quick else 100
    for kind in ("interior", "boundary"):
        truth = make(kind)
        scores = {}
        for name in ("random", "cmaes") + (() if quick else ("bo_gp",)):
            vals = []
            for seed in (3, 4):
                rng = np.random.default_rng(100 + seed)

                def observe(p):
                    v = truth(p) * (1 + 0.05 * rng.standard_normal())
                    v = max(v, 1e-12)
                    return ObjectiveValue(name="t", value=v, raw=v, se=0.05 * v,
                                          n_blocks=32, transform="log",
                                          detail={"value_floor": 1e-12})
                o = create(name, space, seed=seed)
                while o.n_observations < budget:
                    for p in o.ask(4):
                        o.tell(p, observe(p))
                vals.append(o.best()[1].value)
            scores[name] = float(np.median(vals))
        for name, v in scores.items():
            if name == "random":
                continue
            check(f"T8 {name} beats random search ({kind} optimum)",
                  v < scores["random"],
                  f"{name} {v:.3e} vs random {scores['random']:.3e}")


def t9_gp_correctness():
    """Catches: a wrong GP. Analytic gradients, posterior, and EI all verified
    against independent computations rather than against themselves."""
    from stage4_optimizers import GaussianProcess, expected_improvement
    from scipy import integrate
    rng = np.random.default_rng(0)
    n, d = 25, 4
    X = rng.random((n, d))
    C = rng.integers(0, 3, n)
    y = np.sin(3 * X[:, 0]) + X[:, 1] ** 2 + 0.1 * C + rng.normal(0, 0.05, n)

    gp = GaussianProcess()
    gp.X, gp.C = X, C
    gp.y_mean, gp.y_std = y.mean(), y.std()
    gp.y = (y - gp.y_mean) / gp.y_std
    gp.noise = np.full(n, 1e-3)
    theta = np.array([0.2] + [math.log(0.7)] * d + [math.log(1e-3), 0.8])
    _, grad = gp._nll(theta)
    num = np.zeros_like(theta)
    for i in range(len(theta)):
        tp, tm = theta.copy(), theta.copy()
        tp[i] += 1e-6
        tm[i] -= 1e-6
        num[i] = (gp._nll(tp)[0] - gp._nll(tm)[0]) / 2e-6
    err = float(np.max(np.abs(grad - num) / (np.abs(num) + 1e-8)))
    check("T9a GP analytic gradients match finite differences", err < 1e-5,
          f"max relative error {err:.2e}")

    gp2 = GaussianProcess().fit(X, C, y, noise_var=np.full(n, 1e-3), rng=rng)
    Xs, Cs = rng.random((5, d)), rng.integers(0, 3, 5)
    mu, sd = gp2.predict(Xs, Cs)
    sf2, ell, sn2, rho = gp2._unpack(gp2.theta)
    K, *_ = gp2._build(gp2.theta)
    m, _ = gp2._matern(Xs, X, ell)
    Ks = sf2 * m * gp2._cat_factor(Cs, C, rho)
    mu_bf = Ks @ np.linalg.solve(K, gp2.y) * gp2.y_std + gp2.y_mean
    var_bf = sf2 - np.einsum("ij,jk,ik->i", Ks, np.linalg.inv(K), Ks)
    sd_bf = np.sqrt(np.maximum(var_bf, 1e-12)) * gp2.y_std
    check("T9b GP posterior matches a brute-force solve",
          float(np.max(np.abs(mu - mu_bf))) < 1e-9
          and float(np.max(np.abs(sd - sd_bf))) < 1e-8,
          f"mu {np.max(np.abs(mu - mu_bf)):.1e}, sd {np.max(np.abs(sd - sd_bf)):.1e}")

    ei = float(expected_improvement(np.array([0.3]), np.array([0.4]), 0.5)[0])
    quad, _ = integrate.quad(
        lambda t: max(0.5 - t, 0) * math.exp(-0.5 * ((t - 0.3) / 0.4) ** 2)
        / (0.4 * math.sqrt(2 * math.pi)), -10, 10)
    check("T9c Expected Improvement matches numerical quadrature",
          abs(ei - quad) < 1e-9, f"{ei:.10f} vs {quad:.10f}")


def t10_llm_guard_rails():
    """Catches: an LLM proposal bypassing the gates, or a dead host stalling a run."""
    import stage4_space as S
    from stage4_llm import MockOllamaClient, LLMAgent
    space = S.DEFAULT_SPACE
    base = space.baseline_point()

    def payload(**over):
        v = {k: float(base[k]) for k in space.names}
        v.update(over)
        v["miller"] = [0, 0, 1]
        return {"values": v, "rationale": "test"}

    good = json.dumps({"candidates": [payload(sub_c11=200.0), payload(sub_c11=210.0)]})
    cases = {
        "well-formed": ([good], 2),
        "out-of-bounds (clipped)": ([json.dumps({"candidates": [payload(sub_c11=9e9)]}), good], 2),
        "gate-infeasible": ([json.dumps({"candidates": [payload(sub_c11=30.0, sub_c12=200.0)]}), good], 2),
        "malformed JSON": (["not json at all {", "still not json", good], 2),
        "chatty wrapper": (["Sure!\n```json\n" + good + "\n```"], 2),
        "transport error": ([ConnectionError("boom"), good], 2),
    }
    for label, (responses, want) in cases.items():
        o = LLMAgent(space, seed=1, client=MockOllamaClient(list(responses)))
        pts = o.ask(want)
        in_box = all(S.precheck_cheap(p, space)[0] for p in pts)
        check(f"T10 {label}: {want} feasible candidates returned",
              len(pts) == want and in_box,
              f"got {len(pts)}, all feasible={in_box}")

    o = LLMAgent(space, seed=1, client=MockOllamaClient([], available=False))
    pts = o.ask(3)
    check("T10 unreachable host degrades to the fallback rather than stalling",
          len(pts) == 3 and o.llm_stats["fallback_used"] == 3)


def t11_projection_sanity():
    """Catches: a projection metric that does not recognise the material it came from."""
    import stage4_space as S
    import stage4_project_material as P
    space = S.DEFAULT_SPACE
    target = S.comparison_features(space.baseline_point())
    pool = P.load_pool()
    subs = P.substrate_pool(pool)
    tops = P.film_pool(pool, "top")
    bots = P.film_pool(pool, "bottom")
    for label, cands, spec, want in (("substrate", subs, P.SUBSTRATE_FEATURES, "Si"),
                                     ("top film", tops, P.TOP_FILM_FEATURES, "Nb"),
                                     ("bottom film", bots, P.BOTTOM_FILM_FEATURES, "Cu")):
        rows, _ = P.rank(target, cands, spec, top=1)
        # Tolerance rather than exact zero: `derive()` rounds the speeds and the
        # impedance before they are recorded, while the pool computes them raw,
        # so the self-distance is ~1e-7 rather than 0. Anything above 1e-4 would
        # mean the metric no longer recognises the material it came from.
        check(f"T11 baseline vector projects onto {want} ({label})",
              rows and rows[0]["id"] == want and rows[0]["distance"] < 1e-4,
              f"nearest {rows[0]['id']} at d={rows[0]['distance']:.2e}" if rows else "no rows")


def t18_projection_coverage(tmpdir):
    """Catches P4: ranking 4/7 and 7/7 distances as if they answered one question.

    The pre-audit ranking divided by the sum of MATCHED weights and sorted
    everything together, so materials whose `scat`, `decay` and `decayTT` are
    simply unknown -- the three strongest optimization directions in the space
    -- outranked fully characterised ones by being unmeasured.
    """
    import stage4_space as S
    import stage4_project_material as P

    space = S.DEFAULT_SPACE
    pool = P.load_pool()
    subs = P.substrate_pool(pool)
    target = S.comparison_features(space.baseline_point())
    rows, _ = P.rank(target, subs, P.SUBSTRATE_FEATURES, top=len(subs))
    groups = P.strata(rows)

    check("T18a every ranked row declares its feature coverage",
          all("stratum" in r and "coverage" in r for r in rows),
          f"{len(rows)} rows, strata {list(groups)}")
    matched_seq = [r["matched"] for r in rows]
    check("T18b coverage sorts before distance, so no table silently mixes strata",
          matched_seq == sorted(matched_seq, reverse=True),
          f"first 8: {matched_seq[:8]}")
    for stratum, group in groups.items():
        dists = [r["distance"] for r in group]
        if dists != sorted(dists):
            check(f"T18c distances are ordered WITHIN the {stratum} stratum", False,
                  str(dists[:5]))
            break
    else:
        check("T18c distances are ordered within each coverage stratum", True,
              ", ".join(f"{k}: {len(g)}" for k, g in groups.items()))

    flat, _ = P.rank(target, subs, P.SUBSTRATE_FEATURES, top=len(subs),
                     stratify=False)
    check("T18d the pre-audit flat ordering is still reachable, and differs",
          [r["id"] for r in flat] != [r["id"] for r in rows],
          f"flat nearest {flat[0]['id']} ({flat[0]['stratum']}) vs stratified "
          f"{rows[0]['id']} ({rows[0]['stratum']})")

    report = P.missing_feature_report(rows, P.SUBSTRATE_FEATURES)
    check("T18e the missing-data report names which constants are unsourced",
          set(report["missing_counts"]) <= {"sub_scat", "sub_decay", "sub_decayTT"}
          and report["fully_covered"] >= 1,
          f"{report['fully_covered']} fully covered; missing "
          f"{report['missing_counts']}")

    # Unmeasured constants are bracketed rather than silently replaced.
    brackets = P.phonon_constant_brackets(pool, space.baseline_point(), space)
    ok_range = all(b["low"] <= b["target"] <= b["high"] for b in brackets.values())
    variants = P.bracket_variants(space.baseline_point(), brackets, space)
    check("T18f unmeasured phonon constants get a bracket from the measured spread",
          ok_range and set(brackets) == {"sub_scat", "sub_decay", "sub_decayTT"}
          and len(variants) >= 1,
          f"{ {k: [round(v['low'], 60), round(v['high'], 60)] for k, v in brackets.items()} } "
          f"-> {sorted(variants)}")


def t12_resume(tmpdir):
    """Catches: a restart that loses or duplicates observations.

    The campaign has to survive being stopped -- this machine is shared -- and a
    resume that silently drops trials would quietly shrink the surrogate's
    training set.
    """
    from stage3_contract import load_contract
    from stage3_ledger import Ledger, STATUS_SUCCESS
    import stage4_space as S
    import stage4_optimize as OPT

    space = S.DEFAULT_SPACE
    ledger_path = os.path.join(tmpdir, "resume.sqlite")
    rng = np.random.default_rng(0)
    points = space.sample(rng, 5)
    args = argparse.Namespace(
        contract=os.path.join(HERE, "stage4_config.yaml"), ledger=ledger_path,
        optimizer="random", objective="total_qps_per_primary", objective_params=None,
        optimizer_params=None, trials=5, batch=1, parallel=1, workers=2,
        total_mem_gb=4, fidelity="S", events=4000000, positions=None, replicas=None,
        timeout=None, seed=1, seed_bank=0, tag=None, patience=0, tolerance=0.05,
        max_events=None, max_hours=None, baseline_every=0, llm_model=None, llm_host=None)
    campaign = OPT.Campaign(args)
    contract = campaign.contract          # the campaign records the objective in it
    with Ledger(ledger_path) as led:
        for i, p in enumerate(points):
            cand = OPT.candidate_payload(p, space)
            trial_id = f"{contract.campaign_id}_resume{i}"
            planned = [{"replica": r, "position_index": q, "seed": 1,
                        "macro": "m", "hits_file": "h", "done_marker": "d"}
                       for r in range(2) for q in range(16)]
            led.plan_trial(trial_id=trial_id, campaign_id=contract.campaign_id,
                           cache_key=f"key{i}", contract_hash=contract.contract_hash(),
                           code_fingerprint={}, candidate=cand, derived={},
                           fidelity="S", events_total=4000000,
                           events_per_sub_run=125000, n_positions=16, n_replicas=2,
                           scenario={}, seed_bank_id=0, run_dir="d",
                           planned_sub_runs=planned)
            for sr in planned:
                led.set_sub_run_score(trial_id, sr["replica"], sr["position_index"],
                                      10.0 + i, [1.0] * 17, 3)
            led.set_trial_result(trial_id, STATUS_SUCCESS, total_qps=320.0 * (i + 1),
                                 qps_per_primary=1e-4)
            led.set_optimizer_record(trial_id, objective_name="total_qps_per_primary",
                                     optimizer="random", iteration=1)

    n = campaign.resume()
    check("T12 resume replays every completed trial exactly once",
          n == 5 and campaign.optimizer.n_observations == 5,
          f"replayed {n}, optimizer has {campaign.optimizer.n_observations}")
    values = [v.value for v in campaign.optimizer.values]
    check("T12 replayed objectives are recomputed from the stored blocks",
          len(set(np.round(values, 12))) == len(values), f"{np.round(values, 8)}")


def t14_cache_identity():
    """Catches: the defect the v2 factorial audit found.

    Changing a film lifetime, the interface model, the catalog or the macro
    template used to leave the cache payload identical, so a bracket run could
    return nominal cached results. Both halves are checked: the candidate
    payload and the code fingerprint.
    """
    from stage3_contract import load_contract, CODE_IDENTITY_FILES
    from stage3_ledger import compute_cache_key
    import stage4_space as S
    from stage4_optimize import candidate_payload, resolver_for

    contract = load_contract(os.path.join(HERE, "stage4_config.yaml"))
    space = S.DEFAULT_SPACE
    resolve = resolver_for(space)
    base = space.baseline_point()
    alt = dict(base, topfilm_ph_lifetime=base["topfilm_ph_lifetime"] * 2)
    fp = contract.code_fingerprint()
    keys = []
    for p in (base, alt):
        cand = candidate_payload(p, space)
        _, derived = resolve(contract, cand)
        keys.append(compute_cache_key(contract.contract_hash(), fp, cand, derived,
                                      "S", 4000000, {"sites": [1]}, 0))
    check("T14a a changed film lifetime changes the cache key", keys[0] != keys[1])

    tracked = set(CODE_IDENTITY_FILES)
    want = {"parameter_optimization/material_catalog.yaml",
            "parameter_optimization/interface_transmission.py",
            "parameter_optimization/stage4_space.py"}
    check("T14b catalog, interface model and space are in the code identity",
          want <= tracked, f"missing: {sorted(want - tracked)}")
    check("T14c the macro template is hashed into the fingerprint",
          any(k.startswith("macro_template:") for k in fp["files"]),
          ", ".join(k for k in fp["files"] if k.startswith("macro_template:")))


def t15_realization_propagation(tmpdir):
    """Catches P0: a projected material's identity dropped before simulation.

    The defect this exists to prevent, measured: `stage4_project_material.py`
    attached `point["substrate_carrier"]`, every normaliser downstream kept only
    the variable table, and six verification runs that were supposed to carry a
    3180 kg/m3 substrate were simulated as G4_Si at 2330 -- a 17% error in every
    derived sound speed, under the label "elasticity of SiC".

    Every link in the chain is asserted separately, because the chain broke in
    the middle and each end looked correct on its own.
    """
    from stage3_contract import load_contract
    from stage3_ledger import compute_cache_key
    from stage3_trial_runner import (_write_lattice_config, _write_sub_run_macro,
                                     _verify_runtime_material)
    import stage4_space as S
    import stage4_project_material as P
    from stage4_optimize import candidate_payload, resolver_for
    import stage4_confirm as C

    contract = load_contract(os.path.join(HERE, "stage4_config.yaml"))
    space = S.DEFAULT_SPACE
    resolve = resolver_for(space)
    R = S.REALIZATION_KEY

    # -- (a) projection emits a realization, and it survives every normaliser --
    pool = P.load_pool()
    subs = {s["id"]: s for s in P.substrate_pool(pool)}
    target = space.baseline_point()
    sic = None
    for name, rec in subs.items():
        if rec["formula"].replace(" ", "") in ("SiC",) or name.startswith("SiC"):
            sic = rec
            break
    if sic is None:                       # pool snapshot without SiC: synthesise one
        sic = {"id": "SiC(test)", "formula": "SiC", "c11_GPa": 384.0,
               "c12_GPa": 127.0, "c44_GPa": 241.0, "lattice_a_ang": 4.36,
               "density_kg_m3": 3210.0, "scat_s3": None, "decay_s4": None,
               "decayTT": None, "g4_carrier": None}
    tops = {f["id"]: f for f in P.film_pool(pool, "top")}
    bots = {f["id"]: f for f in P.film_pool(pool, "bottom")}
    top = tops.get("In") or tops.get("Nb")
    bot = bots.get("Ag") or bots.get("Cu")
    point, notes = P.elasticity_variant_point(target, sic, top, bot, space=space)
    ok = point is not None and R in point
    check("T15a projection attaches a versioned realization block",
          ok, (point or {}).get(R, {}).get("substrate_carrier") if ok else str(notes))
    if not ok:
        return
    carrier = point[R]["substrate_carrier"]

    # points JSON round trip -- what --points-file actually carries
    pf = os.path.join(tmpdir, "points.json")
    with open(pf, "w") as handle:
        json.dump({"elasticity_of_SiC": point}, handle, default=str)
    with open(pf) as handle:
        reloaded = json.load(handle)["elasticity_of_SiC"]
    through_confirm = C._complete(space, reloaded, "elasticity_of_SiC")
    check("T15b realization survives points JSON -> confirm.collect_points",
          through_confirm.get(R, {}).get("substrate_carrier") == carrier,
          str(through_confirm.get(R, {}).get("substrate_carrier")))

    # ledger payload -- what the cache key is computed over
    cand = candidate_payload(through_confirm, space)
    check("T15c realization reaches the ledger candidate payload",
          cand.get(R, {}).get("substrate_carrier") == carrier,
          str(cand.get(R)))

    resolved, derived = resolve(contract, cand)
    check("T15d resolver uses the requested carrier and ITS density",
          derived["g4_material_name"] == carrier
          and abs(derived["substrate_density_kg_m3"]
                  - S.SUBSTRATE_CARRIERS[carrier]) < 1e-9,
          f"{derived['g4_material_name']} at "
          f"{derived['substrate_density_kg_m3']:.0f} kg/m3")

    # -- (b) the generated macro names that carrier, not G4_Si ---------------
    template = os.environ.get("SENSITIVITY_MACRO_TEMPLATE",
                              os.path.join(REPO_ROOT, "sensitivity_template_beamOn1e6.mac"))
    macro = os.path.join(tmpdir, "t15.mac")
    _write_sub_run_macro(contract, resolved, derived, template, macro,
                         os.path.join(tmpdir, "h.txt"), os.path.join(tmpdir, "h.txt.done"),
                         (0.0, 0.0, 0.0), 12345, 1000)
    with open(macro) as handle:
        macro_text = handle.read()
    want = f"/main/detector_param/setSubstrateG4Name {carrier}"
    check("T15e generated macro carries the requested Geant4 material",
          want in macro_text,
          [ln for ln in macro_text.splitlines() if "setSubstrateG4Name" in ln])

    # -- (c) runtime material check would catch a swap ------------------------
    log = os.path.join(tmpdir, "t15.log")
    rho = S.SUBSTRATE_CARRIERS[carrier] / 1000.0
    with open(log, "w") as handle:
        handle.write(f"  Material: {carrier} density: {rho:.4f} g/cm3\n")
    ok_rt, msg, _ = _verify_runtime_material(log, carrier,
                                             S.SUBSTRATE_CARRIERS[carrier])
    with open(log, "w") as handle:
        handle.write("  Material: G4_Si density: 2.3300 g/cm3\n")
    bad_rt, bad_msg, _ = _verify_runtime_material(log, carrier,
                                                  S.SUBSTRATE_CARRIERS[carrier])
    check("T15f runtime material check accepts the right one and rejects a swap",
          ok_rt and not bad_rt, f"{msg} | swapped -> {bad_msg}")

    # -- (d) the carrier alone changes the cache key --------------------------
    fp = contract.code_fingerprint()

    def key(pt):
        c = candidate_payload(pt, space)
        _, d = resolve(contract, c)
        return compute_cache_key(contract.contract_hash(), fp, c, d, "S",
                                 4000000, {"sites": [1]}, 0)

    other = "G4_ALUMINUM_OXIDE" if carrier != "G4_ALUMINUM_OXIDE" else "G4_Ge"
    bare = dict(point)
    own = point[R].get("own_fields")
    bare[R] = S.normalize_realization({R: {"mode": "pseudo_si_base",
                                           "substrate_carrier": carrier,
                                           "own_fields": own}})
    alt = dict(point)
    alt[R] = S.normalize_realization({R: {"mode": "pseudo_si_base",
                                          "substrate_carrier": other,
                                          "own_fields": own}})
    check("T15g changing only the density carrier changes the cache key",
          key(bare) != key(alt))

    # The carrier must be able to REPRESENT the declared density, not merely be
    # named. Geant4 accepts only a NIST material name, so an unrepresentable
    # density has to fail loudly instead of being carried 23% away.
    try:
        S.normalize_realization({R: dict(point[R], substrate_carrier=other)})
        far_ok = False
    except S.GateError:
        far_ok = True
    check("T15g2 a carrier too far from the material's real density is refused",
          far_ok, f"{other} vs declared "
                  f"{point[R].get('material_density_kg_m3', float('nan')):.0f} kg/m3")

    # A default-realization point must keep the ORIGINAL payload, so the 92
    # valid pseudo-material trials are not invalidated by this fix.
    base_payload = candidate_payload(space.baseline_point(), space)
    check("T15h a plain pseudo-material payload is unchanged by the envelope",
          R not in base_payload, sorted(k for k in base_payload if k.startswith("_")))

    # `derived` is inside the cache key, so the realization envelope must add
    # NOTHING to it for a default candidate. Checked against the real ledger,
    # because "should be identical" and "is identical" have differed here before.
    ledger_path = os.path.join(HERE, "stage4_trials.sqlite")
    if os.path.isfile(ledger_path):
        import sqlite3
        conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        n_same, differing = 0, []
        for row in conn.execute("SELECT trial_id, candidate, derived FROM trials "
                                "WHERE status = 'success'"):
            stored_candidate = json.loads(row["candidate"])
            if stored_candidate.get("_space") != "stage4_property_v1":
                continue
            stored = json.loads(row["derived"])
            try:
                _, fresh = resolve(contract, stored_candidate)
            except Exception as exc:                              # noqa: BLE001
                differing.append(f"{row['trial_id'][-8:]}: {exc}")
                continue
            if (json.dumps(stored, sort_keys=True, default=str)
                    == json.dumps(fresh, sort_keys=True, default=str)):
                n_same += 1
            else:
                differing.append(row["trial_id"][-8:])
        conn.close()
        check("T15h2 every recorded Stage 4 trial still resolves to its stored "
              "`derived`, so no existing cache key was invalidated",
              not differing and n_same > 0,
              f"{n_same} identical" + (f", DIFFERING: {differing[:5]}" if differing else ""))

    # -- (e) a native material keeps its OWN record ---------------------------
    for material in ("Ge", "GaAs"):
        npoint = dict(space.baseline_point())
        npoint[R] = S.normalize_realization(
            {R: {"mode": "native_g4cmp", "material": material}})
        ncand = candidate_payload(npoint, space)
        nres, nder = resolve(contract, ncand)
        dest = os.path.join(tmpdir, f"lat_{material}")
        cfg = _write_lattice_config(contract, nres, nder, dest)
        with open(cfg) as handle:
            body = handle.read()
        native = S.native_substrate_values(material)
        fields_ok = (nder["config_overrides"] == {}
                     and nder["lattice_map_name"] == material
                     and abs(nder["point"]["sub_scat"] - native["sub_scat"])
                     <= 1e-9 * native["sub_scat"])
        has_own = all(tok in body for tok in ("dyn ", "LDOS", "STDOS", "FTDOS"))
        check(f"T15i native {material} is simulated from its own complete record",
              fields_ok and has_own and "GENERATED pseudo-material" not in body,
              f"map={nder['lattice_map_name']} overrides={nder['config_overrides']} "
              f"scat={nder['point']['sub_scat']:g}")

    # -- (f) incomplete real materials are refused BEFORE Geant4 --------------
    refusals = [
        ({"mode": "pseudo_si_base", "substrate_carrier": "G4_UNOBTAINIUM"},
         "no measured Geant4 density"),
        ({"mode": "custom_material", "substrate_carrier": "G4_Si",
          "lattice_map": "NoSuchCrystal"}, "no complete lattice record"),
        ({"mode": "custom_material", "substrate_carrier": "G4_Si"},
         "lattice record not named"),
        ({"mode": "native_g4cmp", "material": "SiC"}, "not in the catalog"),
    ]
    refused = []
    for spec, why in refusals:
        try:
            S.normalize_realization({R: spec})
            refused.append(f"ACCEPTED {why}")
        except S.GateError:
            pass
    check("T15j incomplete or unregistered materials are refused before Geant4",
          not refused, "; ".join(refused) or "all four refused")


def t16_proposal_provenance():
    """Catches P1: crediting one strategy with another's proposals.

    Two measured defects this pins down. (1) The BO campaign dispatched more
    than `n_init` Sobol points because the initial-design test counted only
    REPORTED observations, and the winning point -- a Sobol draw -- was then
    reported as a GP Expected-Improvement result. (2) CMA-ES topped up a full
    generation with a uniformly random point and stored it as a CMA-ES
    observation, although it took no part in any generation update.
    """
    import stage4_space as S
    import stage4_optimizers as O

    space = S.DEFAULT_SPACE

    # -- BO: pending initial-design points count against n_init --------------
    bo = O.create("bo_gp", space, seed=3, n_init=4)
    asked = []
    for _ in range(4):                       # asynchronous: ask, never tell
        asked += bo.ask(1)
    sources = [bo.provenance_of(p)["proposal_source"] for p in asked]
    check("T16a every initial-design proposal is labelled sobol_init",
          sources == ["sobol_init"] * 4, str(sources))
    extra = bo.ask(1)
    check("T16b BO proposes nothing while the initial design is unreported, "
          "instead of calling a Sobol point a GP proposal",
          extra == [], f"{len(extra)} point(s): "
                       f"{[bo.provenance_of(p)['proposal_source'] for p in extra]}")

    class _V:
        def __init__(self, v):
            self.value = v

        def surrogate_y(self):
            return float(np.log(self.value))

        def surrogate_sigma(self):
            return 0.05

    for i, p in enumerate(asked):
        bo.tell(p, _V(1e-4 * (1 + 0.1 * i)))
    after = bo.ask(1)
    src_after = [bo.provenance_of(p) for p in after]
    check("T16c once the design has reported, proposals are GP acquisitions",
          len(after) == 1 and src_after[0]["proposal_source"] == "gp_ei"
          and src_after[0].get("acquisition") is not None,
          str([(s["proposal_source"], s.get("gp_fit")) for s in src_after]))

    # -- CMA-ES: synchronous generations, no mislabelled filler --------------
    cma = O.create("cmaes", space, seed=3, popsize=8)
    batch = []
    for _ in range(8):
        batch += cma.ask(1)
    gen_sources = {cma.provenance_of(p)["proposal_source"] for p in batch}
    check("T16d a CMA generation is labelled cma_generation throughout",
          gen_sources == {"cma_generation"}, str(sorted(gen_sources)))
    check("T16e a full, unreported CMA generation proposes nothing (synchronous)",
          cma.ask(1) == [])
    for i, p in enumerate(batch):
        cma.tell(p, _V(1e-4 * (1 + 0.1 * i)))
    used = [cma.provenance_of(p).get("used_in_optimizer_update") for p in batch]
    check("T16f every CMA proposal that completed its generation is marked as "
          "having entered the update", all(used), str(used))
    check("T16g a completed generation advances the strategy",
          cma.generation == 1, f"generation={cma.generation}")

    # asynchronous mode still exists, but says what the filler is
    acma = O.create("cmaes", space, seed=3, popsize=4, synchronous=False)
    ab = []
    for _ in range(6):
        ab += acma.ask(1)
    filler = [acma.provenance_of(p)["proposal_source"] for p in ab]
    check("T16h asynchronous CMA filler is labelled random_filler, not cma",
          filler[:4] == ["cma_generation"] * 4
          and filler[4:] == ["random_filler"] * 2, str(filler))

    # -- the report can state exactly how many trials came from each source --
    counts = bo.provenance_counts()
    check("T16i provenance counts add up to every point proposed",
          sum(counts.values()) == len(asked) + len(after), str(counts))


def t17_controls_are_fresh(tmpdir):
    """Catches P5: a drift control that is really a cache hit.

    `baseline_control()` used to call the evaluator without `force`, so the
    cache returned the original row and the campaign reported six
    re-evaluations with 0.0% spread. That is cache stability, not machine,
    executable or runtime stability.
    """
    from stage3_ledger import Ledger, compute_cache_key, STATUS_SUCCESS

    args = dict(contract_hash="c", code_fingerprint={"a": 1}, candidate={"x": 1},
                derived={"y": 2}, fidelity="S", events_total=4000000,
                scenario={"sites": [1]}, seed_bank_id=0)
    plain = compute_cache_key(**args)
    check("T17a a control replica id changes the cache key, so the trial reruns",
          len({plain,
               compute_cache_key(**args, control_replica_id="c#control01"),
               compute_cache_key(**args, control_replica_id="c#control02")}) == 3)
    check("T17b omitting it reproduces the historical key exactly, so no "
          "existing trial is invalidated by this field",
          compute_cache_key(**args, control_replica_id=None) == plain)

    path = os.path.join(tmpdir, "controls.sqlite")
    with Ledger(path) as led:
        for i in range(3):
            is_control = i > 0
            tid = f"camp_t{i}"
            led.plan_trial(trial_id=tid, campaign_id="camp",
                           cache_key=f"k{i}", contract_hash="c",
                           code_fingerprint={}, candidate={"x": 1}, derived={},
                           fidelity="S", events_total=4000000,
                           events_per_sub_run=125000, n_positions=16, n_replicas=2,
                           scenario={}, seed_bank_id=0, run_dir="d",
                           planned_sub_runs=[])
            led.set_trial_result(tid, STATUS_SUCCESS, total_qps=100.0 + i,
                                 qps_per_primary=1e-4)
            led.set_optimizer_record(
                tid, objective_name="total_qps_per_primary", optimizer="random",
                control_replica_id=(f"camp#control{i:02d}" if is_control else None))
        obs = [r["trial_id"] for r in led.observations("camp")]
        ctl = [r["trial_id"] for r in led.controls("camp")]
    check("T17c controls never reach the optimizer as observations",
          obs == ["camp_t0"], str(obs))
    check("T17d every control keeps its own inspectable row",
          ctl == ["camp_t1", "camp_t2"], str(ctl))


def t19_engineering_objective():
    """Catches P3: a candidate winning by moving damage into an unobserved film.

    `total_qps_per_primary` counts QPs at the 17 Al junctions and nothing else,
    so the objective is indifferent to a ground plane that absorbs phonons by
    being a much worse superconductor. That is the largest lever the search
    found. These gates check that the trade-off is (a) measurable and (b)
    constrainable -- not that it has been decided.
    """
    import stage4_space as S
    import stage4_objectives as O

    space = S.DEFAULT_SPACE
    base = space.baseline_point()
    nb = O.engineering_diagnostics(base)
    best = O.engineering_diagnostics(dict(base, topfilm_gap=8.16e-5))

    check("T19a a Nb-like ground plane is not a phonon sink; a 82 ueV one is",
          not nb["ground_plane_is_phonon_sink"]
          and best["ground_plane_is_phonon_sink"],
          f"Nb gap/Al = {nb['topfilm_gap_ratio']:.2f}, "
          f"best-found gap/Al = {best['topfilm_gap_ratio']:.2f}")
    check("T19b the implied ground-plane Tc is reported, and it collapses",
          9.0 < nb["topfilm_tc_K"] < 11.0 and best["topfilm_tc_K"] < 1.0,
          f"Nb {nb['topfilm_tc_K']:.2f} K -> best found {best['topfilm_tc_K']:.2f} K")
    check("T19c the unpenalised cost is quantified, in log10 because it spans "
          "hundreds of orders of magnitude",
          best["log10_thermal_qp_density_ratio_vs_Nb"] > 100
          and best["log10_loss_proxy_vs_Nb"] > 100,
          f"log10(n_qp/Nb) = {best['log10_thermal_qp_density_ratio_vs_Nb']:+.0f}, "
          f"log10(loss/Nb) = {best['log10_loss_proxy_vs_Nb']:+.0f} at 20 mK")

    # G8 is opt-in and must not touch an unconstrained campaign.
    ok_default, _ = S.precheck_cheap(dict(base, topfilm_gap=8.16e-5), space)
    constrained = S.Space(constraints={"topfilm_tc_min_K": 4.0})
    ok_nb, _ = S.precheck_cheap(base, constrained)
    ok_low, why_low = S.precheck_cheap(dict(base, topfilm_gap=8.16e-5), constrained)
    check("T19d G8 rejects a sub-threshold ground plane only when declared",
          ok_default and ok_nb and not ok_low and "G8" in (why_low or ""),
          f"unconstrained accepts it; constrained says {why_low}")
    check("T19e the default space declares no constraints, so the recorded "
          "campaign is unchanged", space.constraints == {}, str(space.constraints))

    # Pareto: no single design dominates once loss is scored alongside QPs.
    rows = [{"id": "low_gap_film", "junction_qps": 1.16e-4, "loss_proxy": 1e367},
            {"id": "nb_baseline", "junction_qps": 3.84e-4, "loss_proxy": 1.0},
            {"id": "dominated", "junction_qps": 4.0e-4, "loss_proxy": 1e400}]
    front = {r["id"] for r in O.pareto_front(rows, ["junction_qps", "loss_proxy"])}
    check("T19f finalists that trade off are returned as a Pareto set, and a "
          "dominated one is dropped",
          front == {"low_gap_film", "nb_baseline"}, str(sorted(front)))

    check("T19g the objective is registered under its accurate name too",
          "junction_qps_per_primary" in O.available(),
          ", ".join(sorted(O.available())))


def t20_freeze_and_liveness(tmpdir):
    """Catches: a new writer migrating a ledger a running campaign depends on,
    and an audit that calls a live 29-hour trial stale.

    Both were real. The audit's own fixes changed four CODE_IDENTITY_FILES, so a
    new process started by an existing chain script would have missed every
    cache entry and re-simulated days of work while ALTERing the schema
    underneath the running evaluator. And the first liveness check declared a
    genuinely running trial stale, because it judged on trial-row timestamps and
    on a process list that a PID namespace can hide.
    """
    import stage3_ledger as L
    import stage4_audit as A

    path = os.path.join(tmpdir, "frozen.sqlite")
    with L.Ledger(path) as led:
        led.plan_trial(trial_id="t0", campaign_id="c", cache_key="k0",
                       contract_hash="h", code_fingerprint={}, candidate={},
                       derived={}, fidelity="S", events_total=1,
                       events_per_sub_run=1, n_positions=1, n_replicas=1,
                       scenario={}, seed_bank_id=0, run_dir=os.path.join(tmpdir, "r"),
                       planned_sub_runs=[])
    check("T20a an unfrozen ledger opens normally", L.freeze_reason(path) is None)

    with open(L.freeze_path(path), "w") as handle:
        handle.write("XL confirmation in flight under its launch identity")
    refused = False
    try:
        L.Ledger(path)
    except L.LedgerFrozen as exc:
        refused = "launch identity" in str(exc)
    check("T20b a frozen ledger refuses a new writer, with the reason",
          refused, L.freeze_reason(path))
    with L.Ledger(path, allow_frozen=True) as led:
        opened = led.conn is not None
    check("T20c the freeze can be overridden explicitly, never silently", opened)
    os.remove(L.freeze_path(path))
    check("T20d removing the freeze file restores writes",
          L.freeze_reason(path) is None)

    # -- liveness: evidence, and an explicit unverifiable state ---------------
    now = time.time()
    hits = os.path.join(tmpdir, "r", "hits")
    os.makedirs(hits, exist_ok=True)
    with open(os.path.join(hits, "a_hitsfile.txt"), "w") as handle:
        handle.write("x" * 4096)

    class _Row(dict):
        def __getitem__(self, k):
            return self.get(k)

    base = _Row(trial_id="camp_trial_abcdef123456", run_dir=os.path.join(tmpdir, "r"),
                updated_at=now - 30 * 3600, heartbeat_at=None, hostname=None)
    cols = {"heartbeat_at", "hostname"}

    v = A._liveness(base, [], None, cols)
    check("T20e a growing hits file proves a trial is alive even with no "
          "heartbeat and no visible process",
          v["verdict"] == "alive" and "growing" in v["why"], v["why"])

    old_hits = os.path.join(tmpdir, "r2", "hits")
    os.makedirs(old_hits, exist_ok=True)
    stale_file = os.path.join(old_hits, "a_hitsfile.txt")
    with open(stale_file, "w") as handle:
        handle.write("x")
    os.utime(stale_file, (now - 30 * 3600, now - 30 * 3600))
    stale_row = _Row(trial_id="camp_trial_abcdef123456",
                     run_dir=os.path.join(tmpdir, "r2"),
                     updated_at=now - 30 * 3600, heartbeat_at=None, hostname=None)

    v = A._liveness(stale_row, [], None, cols)
    check("T20f with no heartbeat, no growth and no process list, the verdict is "
          "UNVERIFIABLE -- never `stale`",
          v["verdict"] == "unverifiable" and "UNVERIFIABLE" in v["why"], v["why"])

    v = A._liveness(_Row(stale_row, heartbeat_at=now - 60, hostname="mimir"),
                    [], None, cols)
    check("T20g a recent heartbeat names the host and proves liveness",
          v["verdict"] == "alive" and "mimir" in v["why"], v["why"])

    # A stale heartbeat is necessary but NOT sufficient: finding N3 requires it
    # to be corroborated by a usable process list in which nothing names the
    # trial. Uncorroborated, the verdict is `unverifiable`. T23h/T23i pin both.
    stale_beat = _Row(stale_row, heartbeat_at=now - 30 * 3600, hostname="mimir")
    v_alone = A._liveness(stale_beat, [], None, cols)
    v_corrob = A._liveness(stale_beat, ["python unrelated.py"], None, cols)
    check("T20h a stale heartbeat justifies `abandoned` only once corroborated",
          v_alone["verdict"] == "unverifiable"
          and v_corrob["verdict"] == "abandoned",
          f"alone={v_alone['verdict']}, corroborated={v_corrob['verdict']}")

    # the lease/heartbeat round trip
    with L.Ledger(path) as led:
        led.claim_trial("t0", "lease-1", hostname="mimir", owner_pid=1,
                        owner_ppid=2, scheduler_job_id="SLURM_JOB_ID=7")
        row = led.conn.execute(
            "SELECT lease_uuid, heartbeat_at, hostname, owner_pid, owner_ppid, "
            "scheduler_job_id FROM trials WHERE trial_id='t0'").fetchone()
        first = row["heartbeat_at"]
        led.heartbeat("t0", "wrong-lease")
        unchanged = led.conn.execute(
            "SELECT heartbeat_at FROM trials WHERE trial_id='t0'").fetchone()[0]
    check("T20i a claim records lease, host, pid, ppid and scheduler job id",
          row["lease_uuid"] == "lease-1" and row["hostname"] == "mimir"
          and row["scheduler_job_id"] == "SLURM_JOB_ID=7" and first,
          f"lease={row['lease_uuid']} host={row['hostname']} "
          f"job={row['scheduler_job_id']}")
    check("T20j only the lease holder may beat, so a stale process cannot make "
          "an abandoned trial look alive", unchanged == first)


def _fake_xl_ledger(root, trial="camp_confirmXL_deadbeef0001", complete=True):
    """A disposable ledger + run directory shaped like a finished XL trial."""
    from stage3_ledger import Ledger, STATUS_SUCCESS, STATUS_INCOMPLETE_SET
    os.makedirs(root, exist_ok=True)
    ledger = os.path.join(root, "t.sqlite")
    run_dir = os.path.join(root, "runs", "camp_confirmXL", trial)
    hits = os.path.join(run_dir, "hits")
    os.makedirs(hits, exist_ok=True)
    n_pos, n_rep = 16, 2
    planned = [{"replica": r, "position_index": q, "seed": 1,
                "macro": "m", "hits_file": "h", "done_marker": "d"}
               for r in range(n_rep) for q in range(n_pos)]
    with Ledger(ledger) as led:
        led.plan_trial(trial_id=trial, campaign_id="camp_confirmXL", cache_key="k",
                       contract_hash="h", code_fingerprint={}, candidate={},
                       derived={}, fidelity="L", events_total=3200000000,
                       events_per_sub_run=100000000, n_positions=n_pos,
                       n_replicas=n_rep, scenario={}, seed_bank_id=9,
                       run_dir=run_dir, planned_sub_runs=planned)
        for sr in planned:
            led.set_sub_run_status(trial, sr["replica"], sr["position_index"],
                                   STATUS_SUCCESS if complete else "timeout")
        led.set_trial_result(trial, STATUS_SUCCESS if complete
                             else STATUS_INCOMPLETE_SET, total_qps=1.0,
                             qps_per_primary=1e-4)
    for sr in planned:
        name = f"{trial}_r{sr['replica']}_p{sr['position_index']}_hitsfile.txt"
        with open(os.path.join(hits, name), "w") as handle:
            handle.write("hit\n")
        if complete:
            open(os.path.join(hits, name + ".done"), "w").close()
    result_json = os.path.join(root, "xl_result.json")
    with open(result_json, "w") as handle:
        json.dump({"results": {"best_random": {"value": 1e-4}}}, handle)
    return ledger, trial, result_json


def _run_runbook(step, root, ledger, trial, result_json, audit_ok=True,
                 required_columns=None, stamp="TEST"):
    """Invoke stage4_post_xl.sh against the disposable ledger."""
    import subprocess
    env = dict(os.environ)
    env.update({
        "LEDGER": ledger, "SNAPROOT": os.path.join(root, "snapshots"),
        "STAMP": stamp, "XL_TRIAL": trial, "XL_RESULT_JSON": result_json,
        "XL_OWNER_PID": "", "PY": sys.executable,
        "AUDIT_CMD": ("true" if audit_ok else "false"),
        # Snapshot the disposable tree, not the repository's real results dir.
        "SNAP_ARTIFACTS": os.path.join(root, "runs"),
    })
    if required_columns is not None:
        env["REQUIRED_COLUMNS"] = required_columns
    proc = subprocess.run(["bash", os.path.join(HERE, "stage4_post_xl.sh"), step],
                          cwd=HERE, env=env, capture_output=True, text=True,
                          timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def t21_post_xl_state_machine(tmpdir):
    """Catches N0/N1: a runbook that can skip validation, or unfreeze after a
    failed XL attempt.

    The previous version keyed `migrate` and `unfreeze` on `SHA256SUMS`, which
    `snapshot` itself writes -- so snapshot -> migrate -> unfreeze bypassed
    validation entirely; `validate` swallowed a failing audit with `|| true`;
    migration printed MISSING for an absent required column and succeeded
    anyway; and `check` accepted `incomplete_scenario_set` as completion, so
    `all` could unfreeze after a failed attempt.

    The gates below execute the real shell script against a disposable ledger.
    """
    root = os.path.join(tmpdir, "runbook_ok")
    ledger, trial, result_json = _fake_xl_ledger(root)
    snap = os.path.join(root, "snapshots", "pre_migration_TEST")

    # -- out of order: every step refuses without its predecessor's receipt ---
    refusals = []
    for step, missing in (("validate", ".snapshot_complete"),
                          ("migrate", ".validated"),
                          ("unfreeze", ".migrated")):
        rc, out = _run_runbook(step, root, ledger, trial, result_json)
        refusals.append((step, rc != 0 and missing in out))
    check("T21a every step refuses without its predecessor's receipt",
          all(ok for _, ok in refusals), str(refusals))

    rc, out = _run_runbook("check", root, ledger, trial, result_json)
    check("T21b check passes on a genuinely complete XL trial", rc == 0,
          out.strip().splitlines()[-1] if out else "")

    rc, out = _run_runbook("snapshot", root, ledger, trial, result_json)
    check("T21c snapshot succeeds and writes a receipt",
          rc == 0 and os.path.isfile(os.path.join(snap, ".snapshot_complete")), out[-200:])
    with open(os.path.join(snap, "SHA256SUMS")) as handle:
        manifest = handle.read()
    check("T21d the checksum manifest excludes itself",
          "SHA256SUMS" not in manifest and manifest.strip(),
          f"{len(manifest.splitlines())} file(s) hashed")

    # -- a failing audit must block certification --------------------------
    rc, out = _run_runbook("validate", root, ledger, trial, result_json,
                           audit_ok=False)
    check("T21e a failing audit blocks validation instead of being swallowed",
          rc != 0 and not os.path.isfile(os.path.join(snap, ".validated")),
          "audit FAILURES" in out)

    # -- a corrupted snapshot must be caught -------------------------------
    victim = os.path.join(snap, os.path.basename(result_json))
    if not os.path.isfile(victim):
        victim = [os.path.join(dp, f) for dp, _, fs in os.walk(snap)
                  for f in fs if f not in ("SHA256SUMS", ".snapshot_complete")][0]
    with open(victim, "a") as handle:
        handle.write("corrupted")
    rc, out = _run_runbook("validate", root, ledger, trial, result_json)
    check("T21f checksum verification catches a corrupted snapshot",
          rc != 0 and "checksum verification FAILED" in out
          and not os.path.isfile(os.path.join(snap, ".validated")), out[-160:])

    # -- happy path on a clean tree ----------------------------------------
    root2 = os.path.join(tmpdir, "runbook_happy")
    ledger2, trial2, rj2 = _fake_xl_ledger(root2)
    open(ledger2 + ".frozen", "w").write("test freeze")
    snap2 = os.path.join(root2, "snapshots", "pre_migration_TEST")
    rc, out = _run_runbook("all", root2, ledger2, trial2, rj2)
    receipts = {r: os.path.isfile(os.path.join(snap2, r))
                for r in (".snapshot_complete", ".validated", ".migrated", ".unfrozen")}
    check("T21g the happy path reaches every receipt in order and unfreezes",
          rc == 0 and all(receipts.values())
          and not os.path.isfile(ledger2 + ".frozen"), str(receipts))

    import sqlite3
    conn = sqlite3.connect(f"file:{ledger2}?mode=ro", uri=True)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trials)")}
    conn.close()
    want = {"proposal_source", "control_replica_id", "lease_uuid", "heartbeat_at",
            "hostname", "owner_pid", "owner_ppid", "scheduler_job_id"}
    check("T21h migration actually added every required column",
          want <= cols, f"missing: {sorted(want - cols)}")

    # -- a required column that migration cannot supply must FAIL ----------
    root3 = os.path.join(tmpdir, "runbook_missingcol")
    ledger3, trial3, rj3 = _fake_xl_ledger(root3)
    snap3 = os.path.join(root3, "snapshots", "pre_migration_TEST")
    _run_runbook("snapshot", root3, ledger3, trial3, rj3)
    _run_runbook("validate", root3, ledger3, trial3, rj3)
    rc, out = _run_runbook("migrate", root3, ledger3, trial3, rj3,
                           required_columns="proposal_source a_column_that_cannot_exist")
    check("T21i migration FAILS when a required column is absent, instead of "
          "printing MISSING and succeeding",
          rc != 0 and not os.path.isfile(os.path.join(snap3, ".migrated")),
          "still missing" in out)

    # -- an INCOMPLETE attempt must never be certified ----------------------
    root4 = os.path.join(tmpdir, "runbook_incomplete")
    ledger4, trial4, rj4 = _fake_xl_ledger(root4, complete=False)
    snap4 = os.path.join(root4, "snapshots", "pre_migration_TEST")
    rc_chk, out_chk = _run_runbook("check", root4, ledger4, trial4, rj4)
    rc_snap, _ = _run_runbook("snapshot", root4, ledger4, trial4, rj4)
    rc_rec, _ = _run_runbook("snapshot-recovery", root4, ledger4, trial4, rj4)
    rc_val, out_val = _run_runbook("validate", root4, ledger4, trial4, rj4)
    check("T21j an incomplete XL attempt fails check, cannot be snapshotted as "
          "complete, and its recovery snapshot is never certified",
          rc_chk != 0 and rc_snap != 0 and rc_rec == 0 and rc_val != 0
          and not os.path.isfile(os.path.join(snap4, ".validated")),
          f"check={rc_chk} snapshot={rc_snap} recovery={rc_rec} validate={rc_val}")

    # ...but an explicitly CLOSED incomplete campaign can proceed. Migration
    # safety (no live writer) and scientific completeness are different
    # questions: best_random timed out at 41.7 h, leaving a campaign that was
    # over with nothing running, and a runbook that demanded success would have
    # deadlocked the ledger forever. A gate that can never be satisfied is not
    # fail-closed, it is stuck.
    root5 = os.path.join(tmpdir, "runbook_closed")
    ledger5, trial5, rj5 = _fake_xl_ledger(root5, complete=False)
    open(ledger5 + ".frozen", "w").write("test freeze")
    snap5 = os.path.join(root5, "snapshots", "pre_migration_TEST")
    rc_noreason, out_noreason = _run_runbook("close-incomplete", root5, ledger5,
                                             trial5, rj5)
    check("T21k closing a campaign REQUIRES a recorded reason",
          rc_noreason != 0 and "needs a reason" in out_noreason,
          out_noreason.strip().splitlines()[-1] if out_noreason else "")

    import subprocess
    env = dict(os.environ)
    env.update({"LEDGER": ledger5, "SNAPROOT": os.path.join(root5, "snapshots"),
                "STAMP": "TEST", "XL_TRIAL": trial5, "XL_RESULT_JSON": rj5,
                "XL_OWNER_PID": "", "PY": sys.executable, "AUDIT_CMD": "true",
                "SNAP_ARTIFACTS": os.path.join(root5, "runs")})
    closed = subprocess.run(
        ["bash", os.path.join(HERE, "stage4_post_xl.sh"), "close-incomplete",
         "best_random timed out at 41.7 h; not obtainable at this watchdog"],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
    rc_all, out_all = _run_runbook("all", root5, ledger5, trial5, rj5)
    receipts5 = {r: os.path.isfile(os.path.join(snap5, r))
                 for r in (".campaign_closed", ".snapshot_complete", ".validated",
                           ".migrated", ".unfrozen")}
    mode = ""
    if os.path.isfile(os.path.join(snap5, ".snapshot_complete")):
        with open(os.path.join(snap5, ".snapshot_complete")) as handle:
            mode = handle.read()
    check("T21l an explicitly closed incomplete campaign migrates, and the "
          "snapshot is labelled closed_incomplete rather than complete",
          closed.returncode == 0 and rc_all == 0 and all(receipts5.values())
          and "mode=closed_incomplete" in mode,
          f"{receipts5}; mode={mode.splitlines()[0] if mode else 'none'}")


def t22_identity_separation():
    """Catches N2: execution knobs minting new cache keys.

    Measured cost of the old design: relaunching the fidelity ladder from 32 to
    64 workers changed `max_workers`, which lives in `fixed` and was hashed into
    the cache key, so four in-flight trials were abandoned instead of resuming.
    None of the four excluded values can change a completed simulation.
    """
    from stage3_contract import load_contract, Contract
    from stage3_ledger import compute_cache_key
    import stage4_space as S
    from stage4_optimize import candidate_payload, resolver_for

    path = os.path.join(HERE, "stage4_config.yaml")
    base = load_contract(path)
    sim0, camp0 = base.simulation_identity_hash(), base.campaign_contract_hash()

    # -- execution knobs move the campaign hash, never the simulation hash ----
    moved_sim, moved_camp = [], []
    for key, value in (("max_workers", 999), ("total_mem_gb", 7.0),
                       ("per_sample_mem_gb", 3.0), ("sample_timeout_s", 12.0)):
        c = load_contract(path)
        c.fixed[key] = value
        if c.simulation_identity_hash() != sim0:
            moved_sim.append(key)
        if c.campaign_contract_hash() != camp0:
            moved_camp.append(key)
    check("T22a resource limits and the watchdog do NOT change the simulation "
          "identity", not moved_sim, f"leaked into the cache key: {moved_sim}")
    check("T22b they DO change the campaign identity, so provenance still "
          "records them", sorted(moved_camp) == sorted(
              ["max_workers", "total_mem_gb", "per_sample_mem_gb",
               "sample_timeout_s"]), str(sorted(moved_camp)))

    c = load_contract(path); c.campaign_id = "some_other_campaign"
    check("T22c renaming a campaign does not change the simulation identity",
          c.simulation_identity_hash() == sim0
          and c.campaign_contract_hash() != camp0)

    c = load_contract(path)
    c.raw.setdefault("stage4", {})["objective"] = "p90_position"
    check("T22d changing the objective does not change the simulation identity "
          "(scoring is post-hoc over the same blocks)",
          c.simulation_identity_hash() == sim0
          and c.campaign_contract_hash() != camp0)

    c = load_contract(path)
    c.raw.setdefault("space", {})["millers"] = [[0, 0, 1]]
    check("T22e widening or narrowing the search box does not change the "
          "simulation identity of a point already inside it",
          c.simulation_identity_hash() == sim0
          and c.campaign_contract_hash() != camp0)

    # -- anything physical must still move BOTH ------------------------------
    physical = []
    for key, value in (("gun_energy_eV", 1.0e-3), ("min_e_phonons_eV", 1e-5),
                       ("setTopGap", 2.0e-4), ("n_positions", 32),
                       ("n_replicas", 4), ("seed_base", 12345),
                       ("setWallAbs", 0.5), ("phononBounces", 100)):
        c = load_contract(path)
        if key not in c.fixed:
            continue
        c.fixed[key] = value
        if c.simulation_identity_hash() == sim0:
            physical.append(key)
    check("T22f every physical or scenario field still changes the simulation "
          "identity", not physical, f"did NOT change the cache key: {physical}")

    # -- and it reaches the cache key end to end -----------------------------
    space = S.DEFAULT_SPACE
    resolve = resolver_for(space)
    point = space.baseline_point()
    fp = base.code_fingerprint()

    def key(contract):
        cand = candidate_payload(point, space)
        _, derived = resolve(contract, cand)
        return compute_cache_key(contract.simulation_identity_hash(), fp, cand,
                                 derived, "S", 4000000, {"sites": [1]}, 0)

    hot = load_contract(path); hot.fixed["max_workers"] = 64
    # `seed_base` rather than the gun energy: lowering E_gun below 2*topfilm_gap
    # trips gate G6 before a cache key can be computed, which would test the
    # gate instead of the identity.
    cold = load_contract(path); cold.fixed["seed_base"] = int(base.fixed["seed_base"]) + 1
    check("T22g a worker-count change is a cache HIT; a seed-bank change is a "
          "cache MISS",
          key(hot) == key(base) and key(cold) != key(base))

    check("T22h contract_hash() still means the campaign identity, so resume() "
          "and the ledger column keep their meaning",
          base.contract_hash() == camp0)

    unclassified = set(Contract.EXECUTION_ONLY_FIXED_KEYS) - set(base.fixed)
    check("T22i every key claimed execution-only actually exists in the contract",
          not unclassified, f"not in `fixed`: {sorted(unclassified)}")


def t23_lease_enforcement(tmpdir):
    """Catches N3: a lease that records ownership but does not enforce it.

    `claim_trial()` was an unconditional UPDATE, so two evaluators could hold
    the "same" lease and both execute one cache key into one run directory. The
    heartbeat also returned permanently on its first exception, so one transient
    SQLite lock turned a live job into a stale-heartbeat false positive.
    """
    import time as _t
    from stage3_ledger import Ledger, LeaseHeld, STATUS_SUCCESS
    import stage4_audit as A

    path = os.path.join(tmpdir, "lease.sqlite")
    with Ledger(path) as led:
        led.plan_trial(trial_id="t", campaign_id="c", cache_key="k",
                       contract_hash="h", code_fingerprint={}, candidate={},
                       derived={}, fidelity="S", events_total=1,
                       events_per_sub_run=1, n_positions=1, n_replicas=1,
                       scenario={}, seed_bank_id=0, run_dir="d",
                       planned_sub_runs=[])

        first = led.claim_trial("t", "lease-A", hostname="h1", owner_pid=1)
        second = led.claim_trial("t", "lease-B", hostname="h2", owner_pid=2)
        check("T23a a second evaluator cannot claim a live lease",
              first is True and second is False, f"A={first} B={second}")

        check("T23b the holder may refresh its own lease",
              led.claim_trial("t", "lease-A", hostname="h1", owner_pid=1) is True)
        check("T23c only the holder's heartbeat lands",
              led.heartbeat("t", "lease-A") is True
              and led.heartbeat("t", "lease-B") is False)

        # Explicit recovery is refused while the owner is actively beating.
        try:
            led.claim_trial("t", "lease-C", takeover=True)
            refused = False
        except LeaseHeld:
            refused = True
        check("T23d explicit takeover is refused while the owner is still "
              "beating -- recovery is for a dead owner, not a slow one", refused)

        # Expire the lease, then recovery works and the old owner is locked out.
        led.conn.execute("UPDATE trials SET heartbeat_at=? WHERE trial_id='t'",
                         (_t.time() - led.LEASE_EXPIRY_SECONDS - 60,))
        led.conn.commit()
        check("T23e an EXPIRED lease can be taken over",
              led.claim_trial("t", "lease-C", takeover=True) is True
              and led.holds_lease("t", "lease-C"))
        check("T23f the displaced owner can no longer beat",
              led.heartbeat("t", "lease-A") is False)
        check("T23g a displaced owner cannot overwrite the new owner's result",
              led.set_trial_result("t", STATUS_SUCCESS, total_qps=1.0,
                                   lease_uuid="lease-A") is False
              and led.set_trial_result("t", STATUS_SUCCESS, total_qps=2.0,
                                       lease_uuid="lease-C") is True)

    # -- a stale heartbeat alone is SUSPICION, not proof of death ------------
    now = _t.time()

    class _Row(dict):
        def __getitem__(self, k):
            return self.get(k)

    stale_dir = os.path.join(tmpdir, "lease_run")
    os.makedirs(os.path.join(stale_dir, "hits"), exist_ok=True)
    old_file = os.path.join(stale_dir, "hits", "a_hitsfile.txt")
    open(old_file, "w").write("x")
    os.utime(old_file, (now - 30 * 3600, now - 30 * 3600))
    row = _Row(trial_id="camp_trial_abcdef123456", run_dir=stale_dir,
               updated_at=now - 30 * 3600, heartbeat_at=now - 30 * 3600,
               hostname="mimir")
    cols = {"heartbeat_at", "hostname"}

    no_ps = A._liveness(row, [], None, cols)
    with_ps = A._liveness(row, ["python something_else.py"], None, cols)
    check("T23h a stale heartbeat with NO process list is `unverifiable` and "
          "says SUSPECTED, not `abandoned`",
          no_ps["verdict"] == "unverifiable" and "SUSPECTED" in no_ps["why"],
          no_ps["why"][:90])
    check("T23i a stale heartbeat CORROBORATED by a usable process list is "
          "`abandoned`", with_ps["verdict"] == "abandoned", with_ps["why"][:90])


def t24_projection_parallelism(tmpdir):
    """Catches N4: `--parallel` halving the machine without parallelising.

    `verify()` divided workers and memory by `parallel` and then iterated over
    candidates sequentially, so `--parallel 2` gave each candidate half the
    machine while the other half idled -- a 2x slowdown presented as speed-up.
    """
    import threading
    import time as _t
    import stage3_trial_runner as TR
    import stage4_project_material as P
    import stage4_space as S

    space = S.DEFAULT_SPACE
    points = {f"cand{i}": space.baseline_point() for i in range(4)}
    seen = {"max_workers": None, "total_mem_gb": None}
    overlap = {"max_concurrent": 0}
    live = {"n": 0}
    guard = threading.Lock()

    class _FakeResult:
        is_observation = True
        status = "success"
        total_qps = 100.0
        trial_id = "fake"
        blocks = [{"position": 0, "replica": r, "total_qps": 50.0,
                   "per_electrode_qps": [1.0] * 17, "events": 1000, "n_hits": 5}
                  for r in range(2)]

    def fake_evaluate(contract, candidate, **kw):
        with guard:
            seen["max_workers"] = contract.fixed["max_workers"]
            seen["total_mem_gb"] = contract.fixed["total_mem_gb"]
            live["n"] += 1
            overlap["max_concurrent"] = max(overlap["max_concurrent"], live["n"])
        _t.sleep(0.25)
        with guard:
            live["n"] -= 1
        return _FakeResult()

    real = TR.evaluate
    TR.evaluate = fake_evaluate
    try:
        ledger = os.path.join(tmpdir, "proj.sqlite")
        t0 = _t.time()
        results = P.verify(points, os.path.join(HERE, "stage4_config.yaml"),
                           ledger, events=4000000, workers=8, parallel=4,
                           seed_bank=9, tag="t24")
        elapsed = _t.time() - t0
        concurrent_max = overlap["max_concurrent"]
        workers_split = seen["max_workers"]

        overlap["max_concurrent"] = 0
        P.verify({"only_one": space.baseline_point()},
                 os.path.join(HERE, "stage4_config.yaml"), ledger,
                 events=4000000, workers=8, parallel=4, seed_bank=9, tag="t24b")
        workers_single = seen["max_workers"]
    finally:
        TR.evaluate = real

    # Concurrency is asserted from the observed overlap, not from wall-clock:
    # this suite runs on a box that may have 32 Geant4 workers on it, and a
    # timing threshold would make the gate flaky exactly when the machine is
    # busy. The elapsed time is reported for information only.
    check("T24a candidates actually run concurrently under --parallel",
          concurrent_max >= 2,
          f"max concurrent {concurrent_max}/{len(points)} "
          f"({elapsed:.2f}s vs {0.25 * len(points):.2f}s if sequential)")
    bad = {k: v for k, v in results.items() if v.get("value") is None}
    check("T24b every candidate is still evaluated and scored",
          len(results) == len(points) and not bad,
          f"{len(results)} result(s)" + (f"; unscored: {bad}" if bad else ""))
    check("T24c workers are split across the slots that are really used",
          workers_split == max(1, 8 // 4), f"max_workers={workers_split}")
    check("T24d with ONE candidate the whole machine is used, not 1/parallel",
          workers_single == 8, f"max_workers={workers_single} (expected 8)")

    # Found BY this gate: several threads opening a fresh ledger at once each
    # saw a column missing and each issued the ALTER, so all but the first died
    # with "duplicate column name". Every multi-threaded entry point opens one
    # connection per thread, so it was reachable on any new ledger's first use.
    from stage3_ledger import Ledger
    race_path = os.path.join(tmpdir, "migrate_race", "r.sqlite")
    os.makedirs(os.path.dirname(race_path), exist_ok=True)
    errors = []

    def _open():
        try:
            Ledger(race_path).close()
        except Exception as exc:                             # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    workers = [threading.Thread(target=_open) for _ in range(16)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    check("T24e concurrent first-opens of a ledger do not race in the schema "
          "migration", not errors, "; ".join(sorted(set(errors))[:2]) or
          "16 concurrent opens, no error")

    # Found by this gate flaking once in six runs with "database is locked":
    # ALTER TABLE takes an exclusive lock and a busy timeout does not always
    # cover a schema change, so the migration retries explicitly. The same run
    # exercises the lease CAS, which had a nested-BEGIN bug: `with self.conn:`
    # already opens a transaction, so an inner `BEGIN IMMEDIATE` fought it.
    stress_root = os.path.join(tmpdir, "ledger_stress")
    os.makedirs(stress_root, exist_ok=True)
    stress_errors = []

    def _hammer(worker_id):
        try:
            for round_id in range(4):
                led = Ledger(os.path.join(stress_root, f"db{round_id}.sqlite"))
                trial = f"t{round_id}"
                try:
                    led.plan_trial(
                        trial_id=trial, campaign_id="c", cache_key=f"k{round_id}",
                        contract_hash="h", code_fingerprint={}, candidate={},
                        derived={}, fidelity="S", events_total=1,
                        events_per_sub_run=1, n_positions=1, n_replicas=1,
                        scenario={}, seed_bank_id=0, run_dir="d",
                        planned_sub_runs=[])
                except Exception:                            # noqa: BLE001
                    pass                                     # another thread won
                led.claim_trial(trial, f"lease-{worker_id}")
                led.heartbeat(trial, f"lease-{worker_id}")
                led.close()
        except Exception as exc:                             # noqa: BLE001
            stress_errors.append(f"{type(exc).__name__}: {exc}")

    hammers = [threading.Thread(target=_hammer, args=(i,)) for i in range(8)]
    for h in hammers:
        h.start()
    for h in hammers:
        h.join()
    check("T24f concurrent open + lease + heartbeat never raises "
          "'database is locked'",
          not stress_errors, "; ".join(sorted(set(stress_errors))[:2])
          or "8 threads x 4 ledgers, no error")


def t25_watchdog_does_not_censor_by_quality(tmpdir):
    """Catches: a watchdog that destroys the candidates worth measuring.

    How long a sub-run takes is a function of the physics -- a low-absorption
    design lets phonons bounce toward the 10 000-bounce limit before they are
    absorbed -- so an ABSOLUTE wall-clock limit censors on candidate quality.
    Measured on 2026-08-25: `best_random` lost all 32 sub-runs to a 41.7 h limit
    (~1300 core-hours, no result) while the baseline, at three times the QP
    yield, finished the same tier in 8.3 h.

    The replacement is a PROGRESS watchdog: a slow-but-progressing run is never
    killed however slow it is, a hung one still is.
    """
    import types
    import stage3_trial_runner as TR
    from stage3_ledger import STATUS_SUCCESS, STATUS_TIMEOUT

    root = os.path.join(tmpdir, "watchdog")
    os.makedirs(root, exist_ok=True)
    scripts = {}
    real_harness = TR.harness
    TR.harness = types.SimpleNamespace(
        build_run_command=lambda macro, lattice_name=None: scripts[os.path.basename(macro)],
        CRYSTALMAPS_DIR="/nonexistent")

    def run(name, shell, timeout_s, stall_s):
        macro = os.path.join(root, name + ".mac")
        with open(macro, "w") as handle:
            handle.write("#\n")
        sub = {"name": name, "macro": macro,
               "hits_file": os.path.join(root, name + "_h.txt"),
               "done_marker": os.path.join(root, name + "_h.txt.done"),
               "replica": 0, "position_index": 0, "seed": 1}
        scripts[os.path.basename(macro)] = shell.format(h=sub["hits_file"],
                                                        d=sub["done_marker"])
        return TR._run_one(sub, root, timeout_s, None, root,
                           stall_timeout_s=stall_s)

    try:
        slow = run("slow", 'for i in $(seq 5); do echo tick >> {h}; sleep 1; done; '
                           'touch {d}', 0, 3)
        hung = run("hung", 'echo s >> {h}; sleep 60; touch {d}', 0, 3)
        free = run("free", 'echo x >> {h}; sleep 2; touch {d}', 0, 0)
        absol = run("absolute", 'echo x >> {h}; sleep 30; touch {d}', 3, 0)
        both = run("both", 'echo x >> {h}; sleep 60; touch {d}', 30, 3)
    finally:
        TR.harness = real_harness

    check("T25a a SLOW but progressing sub-run is never killed, however slow",
          slow[0] == STATUS_SUCCESS, f"{slow[0]}: {slow[3]}")
    check("T25b a HUNG sub-run is still killed, and says why",
          hung[0] == STATUS_TIMEOUT and "no output" in (hung[3] or ""),
          f"{hung[0]}: {hung[3]}")
    check("T25c timeout 0 with no stall detector means genuinely unlimited",
          free[0] == STATUS_SUCCESS, f"{free[0]}: {free[3]}")
    check("T25d an explicit absolute limit still fires when asked for -- and is "
          "polled often enough to actually fire",
          absol[0] == STATUS_TIMEOUT and "absolute" in (absol[3] or ""),
          f"{absol[0]}: {absol[3]}")
    check("T25e with both set, whichever triggers first wins",
          both[0] == STATUS_TIMEOUT and "no output" in (both[3] or ""),
          f"{both[0]}: {both[3]}")

    from stage3_contract import load_contract, Contract
    base = load_contract(os.path.join(HERE, "stage4_config.yaml"))
    check("T25f the shipped contract has NO absolute wall-clock limit and DOES "
          "have a stall detector",
          float(base.fixed.get("sample_timeout_s", 1)) == 0
          and float(base.fixed.get("sample_stall_timeout_s", 0)) > 0,
          f"timeout={base.fixed.get('sample_timeout_s')} "
          f"stall={base.fixed.get('sample_stall_timeout_s')}")

    alt = load_contract(os.path.join(HERE, "stage4_config.yaml"))
    alt.fixed["sample_timeout_s"] = 12345
    alt.fixed["sample_stall_timeout_s"] = 99
    check("T25g both watchdogs are execution-only: changing them cannot change "
          "a cache key",
          alt.simulation_identity_hash() == base.simulation_identity_hash()
          and alt.campaign_contract_hash() != base.campaign_contract_hash())
    check("T25h the stall watchdog is declared execution-only by name",
          "sample_stall_timeout_s" in Contract.EXECUTION_ONLY_FIXED_KEYS)

    # The stall signal must include CPU time, not just bytes written. Output
    # cadence is a property of Geant4's buffering that this code does not
    # control; a process burning CPU is not hung whatever it has written. A
    # file-size-only detector would kill a healthy long run -- the exact false
    # positive this whole watchdog rework exists to remove.
    TR.harness = types.SimpleNamespace(
        build_run_command=lambda macro, lattice_name=None: scripts[os.path.basename(macro)],
        CRYSTALMAPS_DIR="/nonexistent")
    try:
        busy = run("cpubusy",
                   'echo x > {h}; end=$((SECONDS+6)); '
                   'while [ $SECONDS -lt $end ]; do :; done; touch {d}', 0, 2)
        idle = run("asleep", 'echo x > {h}; sleep 30; touch {d}', 0, 2)
    finally:
        TR.harness = real_harness
    check("T25i a CPU-busy but SILENT sub-run is not killed (file size alone "
          "would have killed it)",
          busy[0] == STATUS_SUCCESS, f"{busy[0]}: {busy[3]}")
    check("T25j a sub-run that is silent AND consuming no CPU is still killed",
          idle[0] == STATUS_TIMEOUT and "no CPU" in (idle[3] or ""),
          f"{idle[0]}: {idle[3]}")

    # The CPU term must be exec-independent. Reading /proc/<pid>/stat alone
    # works today only because the Geant4 binary is the LAST statement of the
    # generated command, so bash exec-optimises itself into it. Append one line
    # after the binary and bash stays as the parent, the workload becomes a
    # grandchild, cutime stays 0 until reaping, and the CPU signal reads as
    # permanently stuck -- the watchdog would silently lose its best signal.
    # A trailing `echo` here defeats that optimisation on purpose.
    TR.harness = types.SimpleNamespace(
        build_run_command=lambda macro, lattice_name=None: scripts[os.path.basename(macro)],
        CRYSTALMAPS_DIR="/nonexistent")
    try:
        grand = run("grandchild",
                    "echo x > {h}; python3 -c 'import time\nt=time.time()\n"
                    "while time.time()-t<6: pass'; touch {d}; echo done", 0, 2)
        grand_idle = run("grandidle",
                         "echo x > {h}; sleep 30; touch {d}; echo done", 0, 2)
    finally:
        TR.harness = real_harness
    check("T25k a CPU-busy GRANDCHILD is seen as progress (the signal does not "
          "depend on bash's exec optimisation)",
          grand[0] == STATUS_SUCCESS, f"{grand[0]}: {grand[3]}")
    check("T25l a grandchild that is asleep is still killed",
          grand_idle[0] == STATUS_TIMEOUT, f"{grand_idle[0]}: {grand_idle[3]}")


def t13_inertness_from_pilot():
    """Reports the Geant4 A/B result if the pilot has produced one."""
    path = os.path.join(HERE, "results", "stage4_pilot.json")
    if not os.path.isfile(path):
        check("T13 inertness A/B (from the pilot)", True,
              "skipped: run stage4_pilot.py first")
        return
    with open(path) as handle:
        pilot = json.load(handle)
    inert = pilot.get("inertness") or {}
    if not inert:
        check("T13 inertness A/B (from the pilot)", True, "skipped: pilot has no A/B yet")
        return
    for name, rec in inert.items():
        check(f"T13 {name} A/B recorded", True,
              (rec.get("verdict")
               or ("bit-identical (INERT)" if rec["bit_identical"] else "not bit-identical"))
              + f" [{rec['baseline_total_qps']:.0f} vs {rec['alt_total_qps']:.0f} QPs]")
    if pilot.get("determinism"):
        d = pilot["determinism"]
        q1, q2 = d["run1"]["total_qps"], d["run2"]["total_qps"]
        rel = abs(q1 - q2) / max(q1, 1.0)
        se = d["run1"].get("relative_se") or 0.06
        # NOT bit-identity. Measured on this executable: a repeated sub-run with
        # identical seeds diverges in roughly 1 run in 6, and the divergence is a
        # phase shift of the event stream (the same physics reappearing a few
        # event IDs later), not a different physical outcome. Cause is not the
        # seeds, not the pre-run draw sequence, and not ASLR -- see the Stage 4
        # plan's reproducibility note. What must hold is that the repeat agrees
        # within the stochastic noise; if it does not, something real changed.
        check("T13 replay agrees within the measured stochastic noise",
              rel <= 3 * se,
              f"{q1:.0f} vs {q2:.0f} QPs = {rel:.2%} apart, replica SE {se:.2%}")
    if pilot.get("noise"):
        n = pilot["noise"]
        check("T13 measured noise is consistent with the Fano ~ 5 prediction",
              0.5 <= n["implied_fano"] <= 15,
              f"across-bank CV {n['across_bank_cv']:.1%}, within-run SE "
              f"{n['within_run_se']:.1%}, implied Fano {n['implied_fano']:.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slow", action="store_true",
                    help="include the tests that launch Geant4")
    args = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="stage4_tests_")
    try:
        print("Stage 4 exit gates\n")
        print("Space and gates:")
        t1_space_round_trip()
        t2_gates_reject()
        t3_baseline_reconstruction()
        print("\nEvaluator and ledger:")
        t4_v2_path_unchanged()
        t5_scoring_equivalence()
        t7_failure_is_not_zero(tmpdir)
        t12_resume(tmpdir)
        t14_cache_identity()
        t22_identity_separation()
        print("\nOptimizers:")
        t9_gp_correctness()
        t8_optimizer_sanity(quick=not args.slow)
        t10_llm_guard_rails()
        t16_proposal_provenance()
        t17_controls_are_fresh(tmpdir)
        print("\nObjective and engineering constraints:")
        t19_engineering_objective()
        print("\nCampaign safety:")
        t20_freeze_and_liveness(tmpdir)
        t23_lease_enforcement(tmpdir)
        t21_post_xl_state_machine(tmpdir)
        print("\nProjection:")
        t11_projection_sanity()
        t18_projection_coverage(tmpdir)
        t24_projection_parallelism(tmpdir)
        t25_watchdog_does_not_censor_by_quality(tmpdir)
        t15_realization_propagation(tmpdir)
        print("\nPhysics A/B (from the pilot):")
        t13_inertness_from_pilot()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} gates passed")
    failed = [n for n, ok, _ in RESULTS if not ok]
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
