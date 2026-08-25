#!/usr/bin/env python3
"""Stage 4 campaign report: what each optimizer found, and at what cost.

    python stage4_report.py                      # every campaign manifest found
    python stage4_report.py --csv results/stage4_trials.csv --plot

Three things it will not do, because each would overstate the result:

* It does not print a Poisson z. The counts are overdispersed (measured Fano
  ~ 5), so a Poisson interval is optimistic by ~2.3x. Comparisons use the
  replica-based error already carried by each objective value.
* It does not merge campaigns that used different objectives, contracts or
  fidelities. Those are different experiments.
* It does not call the best screening trial a result. The promotion list it
  prints is the input to a higher-fidelity confirmation run with HELD-OUT
  seeds, which is a separate step.
"""

import argparse
import glob
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The converged v2 material study, for context. Same objective, same 16 sites,
# same seed bank, 3.2e8 events per candidate (tier L).
V2_REFERENCE = {
    "Si/Nb/Cu (v2 baseline)": 3.900e-4,
    "Ge/Nb/Cu (v2 best)": 2.494e-4,
    "GaAs/Nb/Cu": 2.686e-4,
    "Si/Ta/Au (v2 worst)": 1.070e-3,
}


def load_manifests(root, pattern):
    out = []
    for path in sorted(glob.glob(os.path.join(root, pattern))):
        try:
            with open(path) as handle:
                man = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        man["_path"] = path
        out.append(man)
    return out


def best_so_far(trials):
    """Best-so-far against cumulative PAID trials.

    A trial replayed from the ledger on restart counts: it cost its events, just
    in an earlier process. Only a true cache hit -- the same vector re-proposed
    inside one campaign, e.g. the baseline control -- is free, and manifests
    written before 2026-08-21 conflate the two, which slightly overcounts.
    """
    xs, ys, best, paid = [], [], math.inf, 0
    for t in trials:
        free = t.get("cached") and not t.get("resumed", True)
        paid += 0 if free else 1
        best = min(best, t["value"])
        xs.append(paid)
        ys.append(best)
    return xs, ys


def summarize(man):
    trials = man.get("trials") or []
    values = [t["value"] for t in trials]
    best = man.get("best") or {}
    return {
        "optimizer": man.get("optimizer"),
        "objective": man.get("objective"),
        "campaign": man.get("campaign_id"),
        "fidelity": man.get("fidelity"),
        "events_per_candidate": man.get("events_total_per_candidate"),
        "n": len(values),
        "rejected": man.get("n_rejected", 0),
        "failed": man.get("n_failed", 0),
        "events_used": man.get("events_used", 0),
        "elapsed_h": (man.get("elapsed_s") or 0) / 3600.0,
        "best": best.get("value"),
        "best_se": best.get("se"),
        "median": float(np.median(values)) if values else None,
        "p10": float(np.percentile(values, 10)) if values else None,
        # Controls used to be a bare list of floats; they are now full records
        # (fresh/cached, host, worker shape, code fingerprint, runtime), because
        # the pre-audit "0% spread" was a list of cache hits (P5). Both shapes
        # are read so historical manifests still report.
        "baseline_controls": man.get("baseline_controls") or [],
        "baseline_control_values": man.get("baseline_control_values"),
        "proposal_sources": man.get("proposal_sources") or {},
        "ledger_status_counts": man.get("ledger_status_counts") or {},
        "llm": man.get("llm"),
    }


def control_values(rows):
    """(values, n_fresh, n_cached, n_unknown) over every campaign's controls.

    A control that came back cached measures the cache, not the machine. The
    distinction did not exist in the pre-audit manifests, so those controls are
    counted as `unknown` rather than credited as fresh.
    """
    values, fresh, cached, unknown = [], 0, 0, 0
    for r in rows:
        for c in r["baseline_controls"]:
            if isinstance(c, dict):
                if c.get("value") is None:
                    continue
                values.append(float(c["value"]))
                if c.get("cached"):
                    cached += 1
                else:
                    fresh += 1
            else:
                values.append(float(c))
                unknown += 1
        for v in (r.get("baseline_control_values") or []):
            if not r["baseline_controls"]:
                values.append(float(v))
                unknown += 1
    return values, fresh, cached, unknown


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", default=os.path.join(HERE, "runs"))
    ap.add_argument("--pattern", default="stage4_*/campaign_*.json")
    ap.add_argument("--exclude", nargs="*", default=["drysmoke", "smoke", "selftest"],
                    help="substrings of campaign paths to leave out (probes and "
                         "plumbing tests are not campaigns)")
    ap.add_argument("--baseline", type=float, default=None,
                    help="baseline objective; default = the campaign's own control")
    ap.add_argument("--top", type=int, default=10, help="candidates to promote")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"),
                    help="joined for per-trial wall time; a cheap objective is not "
                         "free if it costs 10x the CPU")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "stage4_report.json"))
    args = ap.parse_args()

    mans = [m for m in load_manifests(args.runs_root, args.pattern)
            if not any(x in m["_path"] for x in (args.exclude or []))]
    if not mans:
        sys.exit(f"no campaign manifests under {args.runs_root}/{args.pattern}")

    rows = [summarize(m) for m in mans]
    objectives = {r["objective"] for r in rows}
    fidelities = {r["fidelity"] for r in rows}
    print("Stage 4 campaign report")
    print(f"  {len(mans)} campaign(s); objective(s) {sorted(objectives)}; "
          f"fidelity {sorted(fidelities)}")
    if len(objectives) > 1:
        print("  NOTE: campaigns with different objectives are listed but NOT compared.")

    baseline = args.baseline
    controls, n_fresh, n_cached, n_unknown = control_values(rows)
    if baseline is None and controls:
        baseline = float(np.median(controls))
    if baseline is not None and controls:
        spread = ((max(controls) - min(controls)) / baseline) if len(controls) > 1 else 0.0
        print(f"  baseline control: {baseline:.4e} QPs/event from {len(controls)} "
              f"re-evaluation(s), spread {spread:.1%}")
        detail = (f"{n_fresh} fresh, {n_cached} cached, {n_unknown} unrecorded")
        print(f"    provenance: {detail}")
        if n_cached or n_unknown:
            print("    WARNING: a cached control measures the cache, not the "
                  "machine or the executable. Only the fresh ones are drift "
                  "evidence (audit P5).")

    # Proposal provenance: exactly how many trials each strategy actually
    # produced, versus how many were labelled with its name (audit P1).
    if any(r["proposal_sources"] for r in rows):
        print("\n  proposal provenance (trials by where the point came from):")
        for r in rows:
            if not r["proposal_sources"]:
                continue
            parts = ", ".join(f"{k} {v}" for k, v in sorted(
                r["proposal_sources"].items()))
            print(f"    {str(r['optimizer']):10s} {parts}")
    else:
        print("\n  proposal provenance: NOT RECORDED in these manifests -- they "
              "predate the P1 fix, so a trial labelled with an optimizer's name "
              "is not evidence that optimizer's model produced it.")

    print(f"\n{'optimizer':10s} {'obs':>4s} {'rej':>4s} {'fail':>4s} {'events':>9s} "
          f"{'hours':>6s} {'best':>11s} {'vs base':>8s} {'median':>11s}")
    for r in sorted(rows, key=lambda x: (x["best"] is None, x["best"])):
        vs = (f"{100 * (r['best'] - baseline) / baseline:+7.1f}%"
              if (baseline and r["best"]) else "     --")
        print(f"{str(r['optimizer']):10s} {r['n']:4d} {r['rejected']:4d} {r['failed']:4d} "
              f"{r['events_used'] / 1e6:8.0f}M {r['elapsed_h']:6.2f} "
              f"{(r['best'] or float('nan')):11.4e} {vs} "
              f"{(r['median'] or float('nan')):11.4e}")
        if r["llm"] and not r["llm"].get("available"):
            print(f"{'':10s}   LLM unavailable ({r['llm']['detail'][:70]}) -- "
                  f"fell back {r['llm'].get('fallback_used', 0)} time(s)")
        elif r["llm"]:
            print(f"{'':10s}   LLM {r['llm']['model']}: {r['llm']['accepted']} accepted, "
                  f"{r['llm']['clipped']} clipped, {r['llm']['infeasible']} infeasible, "
                  f"{r['llm']['malformed']} malformed, "
                  f"{r['llm'].get('fallback_used', 0)} fallback")

    # Best-so-far versus cumulative cost -- the only fair way to compare
    # strategies, since a method that spends more events should find more.
    print("\nBest-so-far by cumulative trial count (lower is better):")
    marks = [8, 16, 24, 32, 48, 64, 96, 128]
    header = "  " + f"{'optimizer':10s}" + "".join(f"{m:>11d}" for m in marks)
    print(header)
    for man in sorted(mans, key=lambda m: str(m.get("optimizer"))):
        xs, ys = best_so_far(man.get("trials") or [])
        line = f"  {str(man.get('optimizer')):10s}"
        for m in marks:
            v = next((y for x, y in zip(xs, ys) if x >= m), None)
            line += f"{v:11.3e}" if v else f"{'--':>11s}"
        print(line)

    # Promotion list: pooled across optimizers, deduplicated by trial.
    pool = []
    for man in mans:
        for t in man.get("trials") or []:
            pool.append({"optimizer": man.get("optimizer"), **t})
    pool.sort(key=lambda t: t["value"])
    seen, promote = set(), []
    for t in pool:
        if t.get("trial_id") in seen:
            continue
        seen.add(t.get("trial_id"))
        promote.append(t)
        if len(promote) >= args.top:
            break
    print(f"\nTop {len(promote)} screening candidates (promote these to a higher "
          f"fidelity with HELD-OUT seeds; screening rank is not a result):")
    print(f"  {'#':>2s} {'optimizer':10s} {'objective':>11s} {'+-':>9s} {'vs base':>8s}  trial")
    for i, t in enumerate(promote, 1):
        vs = (f"{100 * (t['value'] - baseline) / baseline:+7.1f}%" if baseline else "   --")
        se = f"{t['se']:9.2e}" if t.get("se") else f"{'--':>9s}"
        print(f"  {i:2d} {str(t['optimizer']):10s} {t['value']:11.4e} {se} {vs}  "
              f"{str(t.get('trial_id'))[-12:]}")

    if baseline:
        print("\nContext -- the converged v2 REAL-material study (tier L, same sites/seeds):")
        for name, v in sorted(V2_REFERENCE.items(), key=lambda kv: kv[1]):
            print(f"  {name:28s} {v:.4e}  ({100 * (v - V2_REFERENCE['Si/Nb/Cu (v2 baseline)']) / V2_REFERENCE['Si/Nb/Cu (v2 baseline)']:+6.1f}% vs its own baseline)")
        print("  NOTE: v2 numbers are at 3.2e8 events per candidate; Stage 4 screening "
              "is at 4e6, where the 1 sigma error is ~5%.")

    # Cost. Simulation time is not constant across this space: a candidate that
    # absorbs less and lives longer takes more CPU per event, which is the same
    # direction as a low objective. If the two are strongly correlated the
    # campaign is spending its budget disproportionately on its own favourites,
    # and a cost-aware acquisition would be the next thing to add.
    runtimes = {}
    if args.ledger and os.path.isfile(args.ledger):
        import sqlite3
        conn = sqlite3.connect(args.ledger)
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT trial_id, runtime_s FROM trials"):
            runtimes[row["trial_id"]] = row["runtime_s"]
        conn.close()
    paired = [(t["value"], runtimes.get(t.get("trial_id")))
              for t in pool if runtimes.get(t.get("trial_id"))]
    if len(paired) >= 8:
        v = np.array([p[0] for p in paired])
        r = np.array([p[1] for p in paired])
        rank_v = np.argsort(np.argsort(v))
        rank_r = np.argsort(np.argsort(r))
        rho = float(np.corrcoef(rank_v, rank_r)[0, 1])
        print(f"\nCost: median {np.median(r):.0f}s per trial "
              f"(range {r.min():.0f}-{r.max():.0f}s). Spearman(objective, runtime) "
              f"= {rho:+.2f}")
        if rho < -0.3:
            print("  Lower objective costs MORE CPU -- the search is biased toward "
                  "expensive candidates. Consider a cost-aware acquisition, and note "
                  "that a per-sub-run timeout would preferentially kill the best "
                  "candidates and must not be tightened.")

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        import csv
        keys = None
        with open(args.csv, "w", newline="") as handle:
            writer = None
            for t in pool:
                row = {"optimizer": t["optimizer"], "trial_id": t.get("trial_id"),
                       "value": t["value"], "se": t.get("se"), "raw_total_qps": t.get("raw"),
                       "cached": t.get("cached"), **t["point"]}
                row["miller"] = str(row.get("miller"))
                if writer is None:
                    keys = list(row)
                    writer = csv.DictWriter(handle, fieldnames=keys)
                    writer.writeheader()
                writer.writerow({k: row.get(k) for k in keys})
        print(f"\nCSV: {args.csv} ({len(pool)} trials)")

    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(7.5, 4.6))
            for man in sorted(mans, key=lambda m: str(m.get("optimizer"))):
                xs, ys = best_so_far(man.get("trials") or [])
                if xs:
                    ax.step(xs, ys, where="post", label=str(man.get("optimizer")))
            if baseline:
                ax.axhline(baseline, ls="--", lw=1, color="0.4",
                           label="baseline (Si/Nb/Cu-equivalent)")
            ax.set_xlabel("trials evaluated (each = 4e6 primary events)")
            ax.set_ylabel("best total_QPs per primary event")
            ax.set_yscale("log")
            ax.set_title("Stage 4 property-space search: best-so-far vs cost")
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)
            fig.tight_layout()
            path = os.path.join(HERE, "results", "stage4_convergence.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fig.savefig(path, dpi=140)
            print(f"Plot: {path}")
        except ImportError:
            print("matplotlib not available; skipped the plot")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump({"baseline": baseline, "campaigns": rows,
                   "promote": promote, "v2_reference": V2_REFERENCE},
                  handle, indent=1, default=str)
    print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
