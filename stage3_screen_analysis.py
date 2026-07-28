"""Stage 3 screening analysis: noise floor, and Morris indices with a
dummy-parameter significance threshold.

Two modes.

--mode noise-floor
    Reads a run produced with SENSITIVITY_NOISE_FLOOR_N (N identical design
    points at default values, so every parameter is constant). All spread in
    the outcome is therefore Monte-Carlo noise by construction. Reports the
    measured distribution, checks it against the Poisson expectation, tests
    for correlation between temporally adjacent runs (the clock()-seed
    question), and prints the smallest fractional effect the screen can
    resolve at this fidelity.

--mode morris
    Computes Morris mu*/sigma on the screening objective and applies a
    significance threshold derived from DUMMY PARAMETERS.

Why the dummy-parameter threshold is not optional:
    mu* = mean|elementary effect|. Under pure noise the elementary effects are
    centred on zero but mu* averages their ABSOLUTE value, and
    E|N(0, s)| = s * sqrt(2/pi) > 0. So mu* is positively biased and has no
    null value: a parameter with exactly zero true effect still earns a
    strictly positive mu*, and the ranking will happily order pure noise. This
    is why the previous screen ranked `pt` and `r` highly despite neither
    having any code path into calculate_QPs.

    The six QPDE parameters (f_01, r, s, I_ph, pt, n_cooper) are ideal
    controls: they are sampled and recorded like any other parameter, but they
    are consumed only by the post-hoc ODE, never by Geant4, so they CANNOT
    influence total_QPs. Whatever mu* they earn is, by construction, the mu*
    that noise alone produces at this fidelity and design size. A physical
    parameter is called significant only when a PAIRED TRAJECTORY BOOTSTRAP of
    (its mu* - the largest dummy mu*) stays positive at the 5th percentile.
    Resampling both sides inside the same draw propagates the uncertainty in
    the threshold, which is itself a noisy statistic estimated from only six
    parameters; comparing against its point estimate overstates the number of
    discoveries. The threshold self-calibrates: raise the event count and it
    falls automatically.

    This is a screening rule, not a family-wise-error-controlled test. Report
    results as "above the empirical dummy screening threshold".

    The dummies are valid controls ONLY for objectives built purely from
    generated QPs (total_QPs, QP_yield_per_event, max_electrode_QPs). They
    enter calculate_xQPs, so they are NOT null for peak_DG_MHz or
    total_integrated_DG; the script refuses those.
"""

import argparse
import json
import re
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

QPDE_DUMMIES = ("f_01", "r", "s", "I_ph", "pt", "n_cooper")
# The dummy test is valid ONLY for objectives built purely from generated QPs.
# A WHITELIST, not a blacklist: a blacklist of known-bad columns silently admits
# any future QPDE-dependent column added under a different name, and the failure
# mode -- a threshold inflated by real signal -- looks like a normal result.
QP_ONLY_OBJECTIVES = ("QP_yield_per_event", "total_QPs", "max_electrode_QPs")
DEFAULT_OBJECTIVE = "QP_yield_per_event"


def load_expected_replicas(results_dir):
    """design-point index -> frozenset of sample names the manifest declares.

    Same authority and same construction as stage 2b's build_replica_index():
    stage 1 writes the manifest at macro-generation time, before anything runs,
    so it is the only artifact that records what *should* exist. Returns {} when
    no manifest is present, and the caller then degrades loudly.
    """
    manifest = os.path.join(results_dir, "qp_manifest.jsonl")
    if not os.path.exists(manifest):
        return {}
    expected = {}
    with open(manifest) as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            label = entry.get("design_point") or entry.get("sample_name", "")
            match = re.match(r"Morris_(\d+)", str(label))
            if not match:
                continue
            expected.setdefault(int(match.group(1)), set()).add(str(entry.get("sample_name", "")))
    return {k: frozenset(v) for k, v in expected.items()}


def load_run(results_dir):
    summary = pd.read_csv(os.path.join(results_dir, "qp_summary.csv"))
    design_file = os.path.join(results_dir, "MorrisSequence.csv")
    design = pd.read_csv(design_file) if os.path.exists(design_file) else None
    return summary, design


def aggregate_replicas(summary, objective):
    """Mean objective per design point, plus the empirical within-point spread.

    Replicas are independent CLHEP realizations of the SAME configuration, so
    their spread is a direct measurement of the noise, and their mean is the
    quantity the elementary effects should be built from.
    """
    if "design_point" not in summary.columns or summary["design_point"].isna().all():
        summary = summary.assign(design_point=summary["sample_name"])
    grouped = summary.groupby("design_point", sort=False)[objective]
    out = grouped.agg(["mean", "std", "count"]).reset_index()
    out.columns = ["design_point", "y", "y_std", "n_replicas"]
    # Preserve the design-point ordering of the original file (Morris
    # trajectories are order-sensitive; sorting would scramble them).
    order = summary["design_point"].drop_duplicates().tolist()
    out["__order"] = out["design_point"].map({name: i for i, name in enumerate(order)})
    return out.sort_values("__order").drop(columns="__order").reset_index(drop=True)


def report_noise_floor(results_dir, objective):
    summary, _ = load_run(results_dir)
    print(f"Noise-floor run: {results_dir}")
    print(f"Design points (all identical): {len(summary)}")
    if len(summary) < 2:
        raise SystemExit("Need at least 2 points to measure a floor.")

    counts = summary["total_QPs"].to_numpy(dtype=float)
    n_sim = summary["n_sim"].to_numpy(dtype=float)
    yields = summary[objective].to_numpy(dtype=float)

    lam = counts.mean()
    print("\n--- Outcome distribution (identical configurations) ---")
    print(f"  events per design point : {n_sim[0]:,.0f}")
    print(f"  total_QPs  mean         : {lam:.3f}")
    print(f"  total_QPs  std          : {counts.std(ddof=1):.3f}")
    print(f"  total_QPs  min / max    : {counts.min():.0f} / {counts.max():.0f}")
    print(f"  fraction exactly zero   : {(counts == 0).mean():.3f}")
    print(f"  relative SD of one run  : {100 * counts.std(ddof=1) / lam:.1f}%" if lam > 0 else "  (no signal)")
    print(f"  {objective} mean        : {yields.mean():.6g}")

    print("\n--- Is the spread consistent with Poisson? ---")
    if lam > 0:
        dispersion = counts.var(ddof=1) / lam
        chi2 = (len(counts) - 1) * dispersion
        p_value = 1 - stats.chi2.cdf(chi2, len(counts) - 1)
        print(f"  Fano factor (var/mean)  : {dispersion:.3f}   (1.0 = Poisson)")
        print(f"  dispersion test p       : {p_value:.3g}")
        if dispersion > 1.5:
            print("  -> OVERDISPERSED. Extra variance beyond counting statistics;")
            print("     source-position sampling or seed structure is contributing.")
        elif dispersion < 0.67:
            print("  -> UNDERDISPERSED. Suspect correlated seeds across runs.")
        else:
            print("  -> consistent with pure Poisson counting noise.")

    print("\n--- Temporal correlation (the clock()-seed question) ---")
    print("  Design points are identical, so ANY autocorrelation along run order")
    print("  is seed structure, not design structure -- the confound that made")
    print("  this untestable in the previous Morris run is absent here.")
    index = summary["sample_name"].str.extract(r"(\d+)").astype(int)[0].to_numpy()
    order = np.argsort(index)
    ordered = counts[order]
    for lag in (1, 2, 3, 5, 10):
        if len(ordered) > lag + 2:
            r, p = stats.pearsonr(ordered[:-lag], ordered[lag:])
            flag = "   <== SIGNIFICANT" if p < 0.01 else ""
            print(f"  lag {lag:2d}: r = {r:+.4f}  p = {p:.3g}{flag}")

    print("\n--- Resolving power at this fidelity ---")
    print("  Uses the MEASURED spread, not the Poisson idealisation. The counts")
    print("  here are compound Poisson (a Poisson number of pair-breaking phonons,")
    print("  each yielding a variable number of QPs), so the variance exceeds the")
    print("  mean by the Fano factor F and 'lambda > 18/delta^2' understates the")
    print("  requirement by exactly F. Variance scales as 1/events, so reaching a")
    print("  target resolution costs F times more events than the Poisson formula")
    print("  suggests.")
    if lam > 0:
        sigma = counts.std(ddof=1)
        fano = sigma ** 2 / lam
        # SE of the difference of two independent evaluations, relative to the mean.
        rel_se_diff = np.sqrt(2) * sigma / lam
        smallest = 3 * rel_se_diff
        print(f"  measured lambda         : {lam:.1f}")
        print(f"  measured sigma          : {sigma:.2f}   (Fano {fano:.2f})")
        print(f"  smallest resolvable eff.: {100 * smallest:.0f}%  (3 sigma, 1 replica)")
        print(f"    [the pure-Poisson formula would have claimed "
              f"{100 * np.sqrt(18 / lam):.0f}% -- optimistic by sqrt(F) = {np.sqrt(fano):.2f}x]")
        for delta in (0.1, 0.2, 0.3, 0.5):
            # need 3*sqrt(2)*sigma_scaled/lambda_scaled < delta, with both scaling
            # from `scale` times more events: lambda -> s*lam, sigma^2 -> s*sigma^2.
            scale = (smallest / delta) ** 2
            print(f"    to resolve {delta * 100:3.0f}%: {scale:6.2f}x more events "
                  f"({n_sim[0] * scale:.2e} per design point, lambda ~ {lam * scale:.0f})")
    return lam


def morris_elementary_effects(design, y, num_levels=4):
    """Dimensionless elementary effects per parameter.

    The design is a sequence of trajectories of (k+1) rows each, where
    consecutive rows differ in exactly one parameter.

    Both axes are normalised, and this is essential rather than cosmetic:

    * x is scaled by each parameter's own sampled range, so a step is measured
      as a fraction of that parameter's full sweep. A raw EE = dy/dx carries
      the units of x, so parameters on different scales produce mu* values
      that differ by orders of magnitude for reasons that have nothing to do
      with influence. In this design the QPDE controls alone span ~1e-5 (`r`)
      to ~1e9 (`f_01`), which made raw mu* incomparable across parameters and
      made any threshold built from them meaningless.
    * y is scaled by its mean, so an effect reads as the fractional change in
      the objective produced by sweeping that parameter across its full range.

    A mu* of 0.2 therefore means "moving this parameter across its whole range
    moves QP yield by ~20% on average", which is directly comparable to the
    resolution figures from the noise-floor run.
    """
    k = design.shape[1]
    traj_len = k + 1
    n_traj = len(design) // traj_len
    # Sampled range per parameter, from the design itself (equals the declared
    # bounds for a full design; using the realised range keeps this correct for
    # truncated or subset designs too).
    spans = np.array((design.max() - design.min()).to_numpy(dtype=float), copy=True)
    spans[spans == 0] = np.nan  # a constant column has no elementary effect
    y_scale = np.nanmean(y)
    if not np.isfinite(y_scale) or y_scale == 0:
        y_scale = 1.0

    effects = {name: [] for name in design.columns}
    for t in range(n_traj):
        block = design.iloc[t * traj_len:(t + 1) * traj_len]
        y_block = y[t * traj_len:(t + 1) * traj_len]
        values = block.to_numpy(dtype=float)
        for step in range(k):
            delta_vec = values[step + 1] - values[step]
            changed = np.flatnonzero(np.abs(delta_vec) > 0)
            if len(changed) != 1:
                # A Morris step MUST move exactly one coordinate. Skipping a
                # violation silently drops elementary effects and quietly
                # changes what mu* averages over, so this is fatal instead.
                raise SystemExit(
                    f"Malformed Morris design: trajectory {t}, step {step} changes "
                    f"{len(changed)} parameters, expected exactly 1. The design is "
                    f"not a valid one-factor-at-a-time path; refusing to compute "
                    f"elementary effects."
                )
            j = changed[0]
            dy = y_block[step + 1] - y_block[step]
            dx_scaled = delta_vec[j] / spans[j]
            if np.isfinite(dx_scaled) and dx_scaled != 0 and np.isfinite(dy):
                effects[design.columns[j]].append((dy / y_scale) / dx_scaled)
    return {name: np.array(vals) for name, vals in effects.items()}


BOOT_LO_PCT, BOOT_HI_PCT = 5, 95   # two-sided 90% == one-sided 95% lower bound


def validate_effect_structure(effects, design):
    """Assert the retained design produced a complete, well-formed effect set.

    Every parameter must receive exactly one elementary effect per retained
    trajectory: a Morris trajectory of K+1 points changes each of the K
    parameters exactly once. Anything else means the design was truncated or
    a parameter was moved twice, and mu* would then be averaged over a
    different number of effects per parameter -- silently incomparable across
    the ranking, which is the whole output.
    """
    traj_len = design.shape[1] + 1
    n_traj = len(design) // traj_len
    counts = {name: len(vals) for name, vals in effects.items()}
    wrong = {n: c for n, c in counts.items() if c != n_traj}
    if wrong:
        sample = ", ".join(f"{n.strip()}={c}" for n, c in list(wrong.items())[:5])
        raise SystemExit(
            f"Effect structure invalid: expected {n_traj} effects per parameter "
            f"(one per retained trajectory); {len(wrong)} parameter(s) differ "
            f"({sample}). mu* would average over unequal samples."
        )
    print(f"  effect structure : {n_traj} effects x {len(counts)} parameters, validated")


def bootstrap_mu_star(values, n_boot=2000, seed=0):
    """Point estimate and a two-sided 90% bootstrap interval (5th/95th pct).

    Note the level: this is a 90% interval, equivalently a one-sided 95% lower
    bound, which is what the screening rule actually uses. It is not a 95%
    two-sided interval.
    """
    if len(values) == 0:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(n_boot, len(values)), replace=True)
    boot = np.abs(draws).mean(axis=1)
    return np.abs(values).mean(), np.percentile(boot, BOOT_LO_PCT), np.percentile(boot, BOOT_HI_PCT)


def joint_dummy_test(effects_matrix, is_dummy, n_boot=4000, seed=0):
    """Paired trajectory bootstrap of (mu*_param - max dummy mu*).

    The simpler rule -- compare each parameter's bootstrap lower bound against
    the POINT estimate of the largest dummy mu* -- treats that threshold as
    known exactly, when it is itself a noisy statistic estimated from six
    parameters. Resampling trajectories and recomputing BOTH sides within each
    draw propagates that uncertainty, and because the comparison is paired
    inside a draw it also respects the correlation between a parameter and the
    dummies (they share the same trajectories and the same simulation noise).

    Returns the 5th percentile of the difference; > 0 means the parameter beats
    the empirical null at that level.
    """
    rng = np.random.default_rng(seed)
    n_traj = effects_matrix.shape[1]
    diffs = np.empty((n_boot, effects_matrix.shape[0]))
    for b in range(n_boot):
        idx = rng.integers(0, n_traj, n_traj)
        mu = np.abs(effects_matrix[:, idx]).mean(axis=1)
        diffs[b] = mu - mu[is_dummy].max()
    return np.percentile(diffs, BOOT_LO_PCT, axis=0)


def report_morris(results_dir, objective, num_levels, max_design_points=0):
    summary, design = load_run(results_dir)
    if design is None:
        raise SystemExit("MorrisSequence.csv not found; cannot compute Morris indices.")

    aggregated = aggregate_replicas(summary, objective)

    # Join responses to the design BY LABEL, never by row position.
    #
    # Stage 2 legitimately skips a manifest entry when none of its hits files is
    # readable, so `aggregated` can be shorter than the design. Truncating both
    # to the shorter length and zipping them -- as an earlier version did --
    # pairs every response after the gap with the WRONG design row: drop
    # Morris_100 and design row 100 silently receives Morris_101's response,
    # and so on for the remaining 6,800 rows. The arrays keep plausible shapes
    # and nothing raises, so the output is a confident, meaningless table. That
    # is worse than a crash.
    index = aggregated["design_point"].str.extract(r"Morris_(\d+)", expand=False)
    if index.isna().any():
        raise SystemExit("Could not parse a design-point index from every summary row.")
    aggregated = aggregated.assign(design_index=index.astype(int))

    n_design = len(design) if not max_design_points else min(len(design), max_design_points)
    response = pd.Series(np.nan, index=range(n_design), dtype=float)
    in_range = aggregated[aggregated.design_index < n_design]
    response.loc[in_range.design_index.to_numpy()] = in_range["y"].to_numpy(dtype=float)

    # Completeness is decided against the MANIFEST, never against the data.
    #
    # Inferring the expected replica count from the summary -- e.g.
    # `int(aggregated["n_replicas"].median())` -- fails in three ways, all
    # silent:
    #   * the median tracks the damage. Reduce 5,000 of 6,912 points to one
    #     replica and the median becomes 1, so zero points are flagged short
    #     and every crippled point passes as complete;
    #   * `n_replicas` counts ROWS, not distinct replica identities, so two
    #     copies of `r0` with `r1` missing scores 2 and passes;
    #   * the exact identity set {r0, r1} is never checked, so a point can hold
    #     the right number of the wrong rows.
    # The manifest is written by stage 1 at macro-generation time and is the
    # only record of what should exist, so it is the authority here -- the same
    # rule stage 2b's build_replica_index() applies.
    expected_by_index = load_expected_replicas(results_dir)
    missing = set(np.flatnonzero(response.isna().to_numpy()).tolist())
    mismatched = set()
    if expected_by_index:
        observed = (
            summary.assign(
                design_index=summary["design_point"].str.extract(r"Morris_(\d+)", expand=False)
            )
            .dropna(subset=["design_index"])
            .astype({"design_index": int})
            .groupby("design_index")["sample_name"]
            .apply(lambda names: frozenset(names.astype(str)))
        )
        for idx in range(n_design):
            expected = expected_by_index.get(idx)
            if expected is None:
                continue
            if observed.get(idx) != expected:
                mismatched.add(idx)
        expected_replicas = max(len(v) for v in expected_by_index.values())
        print(f"  manifest declares {len(expected_by_index)} design points, "
              f"{expected_replicas} replica(s) each")
    else:
        expected_replicas = int(aggregated["n_replicas"].median())
        print("  WARNING: no manifest found; falling back to inferring the replica "
              "count from the summary, which cannot detect duplicate or "
              "wrong-identity rows.")
        mismatched = set(
            in_range[in_range.n_replicas != expected_replicas].design_index.tolist()
        )
    incomplete = np.array(sorted(missing | mismatched), dtype=int)

    design = design.iloc[:n_design].reset_index(drop=True)
    y = response.to_numpy(dtype=float)

    if len(incomplete):
        # An elementary effect is a DIFFERENCE between consecutive endpoints, so
        # one bad endpoint corrupts the two effects touching it. Rather than
        # silently propagating that, drop the whole trajectory it belongs to.
        traj_len = design.shape[1] + 1
        bad_traj = set((incomplete // traj_len).tolist())
        keep = np.array([i for i in range(n_design) if (i // traj_len) not in bad_traj])
        print(f"  WARNING: {len(incomplete)} design point(s) missing or short on replicas; "
              f"dropping {len(bad_traj)} whole trajectory/ies "
              f"({n_design - len(keep)} points) so no elementary effect spans a gap.")
        design = design.iloc[keep].reset_index(drop=True)
        y = y[keep]

    if len(design) % (design.shape[1] + 1) != 0:
        raise SystemExit(
            f"Retained {len(design)} design rows, not a whole number of "
            f"{design.shape[1] + 1}-point trajectories; refusing to compute effects."
        )

    print(f"Morris screen: {results_dir}")
    print(f"  design points   : {len(design)}"
          + (f" (of {n_design}; incomplete trajectories dropped)" if len(design) != n_design else ""))
    print(f"  trajectories    : {len(design) // (design.shape[1] + 1)}")
    print(f"  objective       : {objective}")
    print(f"  replicas/point  : {expected_replicas}")

    effects = morris_elementary_effects(design, y, num_levels=num_levels)
    validate_effect_structure(effects, design)
    names = list(effects)
    matrix = np.array([effects[k] for k in names])
    dummy_mask = np.array([k.strip() in QPDE_DUMMIES for k in names])
    joint_lo = joint_dummy_test(matrix, dummy_mask)
    rows = []
    for i, (name, values) in enumerate(effects.items()):
        mu_star, lo, hi = bootstrap_mu_star(values, seed=i)
        rows.append({
            "parameter": name.strip(),
            "mu_star": mu_star,
            "mu_star_lo": lo,
            "mu_star_hi": hi,
            "sigma": values.std(ddof=1) if len(values) > 1 else np.nan,
            "n_effects": len(values),
            "is_dummy": name.strip() in QPDE_DUMMIES,
            "joint_lo": joint_lo[i],
        })
    table = pd.DataFrame(rows)

    dummies = table[table.is_dummy]
    if dummies.empty:
        raise SystemExit("No dummy parameters found; cannot calibrate a threshold.")
    threshold = dummies["mu_star"].max()
    worst_dummy = dummies.loc[dummies["mu_star"].idxmax(), "parameter"]

    table["significant_naive"] = table["mu_star_lo"] > threshold
    table["significant"] = table["joint_lo"] > 0
    table = table.sort_values("mu_star", ascending=False).reset_index(drop=True)

    print(f"\n--- Bootstrap interval: {BOOT_HI_PCT - BOOT_LO_PCT}% two-sided "
          f"({100 - BOOT_LO_PCT}% one-sided lower bound) ---")
    print("\n--- Dummy-parameter noise threshold ---")
    print("  These cannot physically affect total_QPs; their mu* is what noise alone earns.")
    for _, row in dummies.sort_values("mu_star", ascending=False).iterrows():
        print(f"    {row.parameter:12s} mu* = {row.mu_star:.4g}")
    print(f"  threshold = max dummy mu* = {threshold:.4g}  (from '{worst_dummy}')")
    n_naive = int((table.significant_naive & ~table.is_dummy).sum())
    n_joint = int((table.significant & ~table.is_dummy).sum())
    print(f"\n  Significance rule: PAIRED TRAJECTORY BOOTSTRAP of "
          f"(mu* - max dummy mu*), which propagates the uncertainty in the")
    print(f"  threshold itself. Comparing a bootstrap lower bound against the "
          f"threshold's POINT estimate would claim {n_naive}; the joint rule gives {n_joint}.")
    demoted = table[(table.significant_naive) & (~table.significant) & (~table.is_dummy)]
    if len(demoted):
        print(f"  Demoted by the joint rule (suggestive, not confirmed): "
              f"{', '.join(demoted.parameter)}")
    print("  NB 'significant' here means 'above the empirical dummy screening "
          "threshold'. It is\n  not a family-wise-error-controlled test over 47 comparisons.")

    significant = table[table.significant & ~table.is_dummy]
    print(f"\n--- Significant physical parameters: {len(significant)} of "
          f"{(~table.is_dummy).sum()} ---")
    if significant.empty:
        print("  NONE clear the noise floor. The screen cannot resolve any parameter")
        print("  at this fidelity; raise the event count rather than trusting the ranking.")
    else:
        print(f"  {'parameter':44s} {'mu*':>10s} {'CI low':>10s} {'sigma':>10s}")
        for _, row in significant.iterrows():
            print(f"  {row.parameter:44s} {row.mu_star:10.4g} {row.mu_star_lo:10.4g} {row.sigma:10.4g}")

    below = table[~table.significant & ~table.is_dummy]
    print(f"\n--- Below threshold (indistinguishable from noise): {len(below)} ---")
    print("  " + ", ".join(below.parameter.head(20)) + (" ..." if len(below) > 20 else ""))

    # Independent-halves stability. NOTE: a T=64 run truncated from a T=128
    # design is the FIRST HALF of it, so comparing the two is not independent.
    # Splitting the trajectories is.
    if matrix.shape[1] >= 4:
        from scipy.stats import spearmanr
        half = matrix.shape[1] // 2
        h1 = np.abs(matrix[:, :half]).mean(axis=1)
        h2 = np.abs(matrix[:, half:]).mean(axis=1)
        phys = ~dummy_mask
        rho, pval = spearmanr(h1[phys], h2[phys])
        order1 = set(np.array(names)[phys][np.argsort(h1[phys])[::-1][:10]])
        order2 = set(np.array(names)[phys][np.argsort(h2[phys])[::-1][:10]])
        print(f"\n--- Independent-halves stability (trajectories 1-{half} vs {half+1}-{matrix.shape[1]}) ---")
        print(f"  Spearman rho over {int(phys.sum())} physical parameters : {rho:.3f}  (p = {pval:.2g})")
        print(f"  top-10 overlap                                : {len(order1 & order2)} of 10")

    out_file = os.path.join(results_dir, "stage3_morris_screen.csv")
    table.to_csv(out_file, index=False)
    print(f"\nWrote {out_file}")
    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("noise-floor", "morris"), required=True)
    parser.add_argument("--objective", default=DEFAULT_OBJECTIVE,
                        help="qp_summary.csv column to screen on (default: %(default)s)")
    parser.add_argument("--num-levels", type=int, default=4)
    parser.add_argument("--max-design-points", type=int, default=0,
                        help="Analyse only the first N design points. With a fixed seed "
                             "SALib's T=64 design is exactly the first half of T=128, so "
                             "--max-design-points 3456 gives the T=64 screen from a T=128 run.")
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    if args.mode == "morris" and args.objective not in QP_ONLY_OBJECTIVES:
        raise SystemExit(
            f"Refusing to run the dummy-calibrated test on '{args.objective}'.\n"
            f"The test is only valid for objectives built purely from generated "
            f"QPs, because the six QPDE parameters {QPDE_DUMMIES} enter "
            f"calculate_xQPs and would then be real signal rather than null "
            f"controls -- inflating the threshold and invalidating the test.\n"
            f"Allowed: {', '.join(QP_ONLY_OBJECTIVES)}.\n"
            f"If '{args.objective}' is genuinely QP-only, add it to "
            f"QP_ONLY_OBJECTIVES explicitly."
        )
    if args.mode == "noise-floor":
        report_noise_floor(results_dir, args.objective)
    else:
        report_morris(results_dir, args.objective, args.num_levels, args.max_design_points)


if __name__ == "__main__":
    main()
