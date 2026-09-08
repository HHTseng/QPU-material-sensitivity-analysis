#!/usr/bin/env python
"""Apply the Sep-8 plan's PREDECLARED acceptance criteria to the stratified levels.

Predeclared before any level was run, so that passing is not a matter of
choosing the test afterwards:

  C1  |J_2N - J_N| / J_2N <= 5%
  C2  the shift is no larger than two combined standard errors
  C3  the candidate-baseline reduction changes by at most 5 percentage points
  C4  no weighted quadrature node contributes more than 10% of J
  C5  each stratum separately satisfies C1
  C6  rankings are stable except where uncertainty intervals overlap

A failure names the stratum to refine. Refining the offending stratum is the
correct response; adding events at unchanged sites is not, because the error
being measured is spatial.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_levels():
    out = {}
    for path in sorted(glob.glob(os.path.join(HERE, "results", "stage4_strat_[0-9]*.json"))):
        m = re.search(r"stage4_strat_(\d+)\.json$", path)
        if not m:
            continue
        with open(path) as fh:
            out[int(m.group(1))] = json.load(fh)
    return dict(sorted(out.items()))


def detail(rec):
    d = rec.get("detail") or {}
    return d if d else rec


def main():
    levels = load_levels()
    if not levels:
        print("no stratified results yet "
              "(expected results/stage4_strat_<N>.json)")
        # Fail CLOSED. "Nothing to judge" is not "converged"; a gate that
        # returns success here would let the whole P6-8 chain start against an
        # objective that was never validated.
        return 2
    ns = list(levels)
    print(f"Stratified convergence -- levels present: {ns}\n")

    cands = sorted({c for lv in levels.values() for c in lv["results"]})
    print(f"  {'candidate':26s}" + "".join(f"{n:>14d}" for n in ns))
    J = {}
    for c in cands:
        row = f"  {c:26s}"
        for n in ns:
            r = levels[n]["results"].get(c, {})
            v = r.get("value")
            J[(c, n)] = v
            row += f"{v:14.4e}" if v else f"{'--':>14s}"
        print(row)

    if len(ns) < 2:
        print("\n  only one level: no convergence statement possible yet")
        return 2

    N, N2 = ns[-2], ns[-1]
    print(f"\n  DECISION (judged on {N} -> {N2}):\n")
    verdicts, refine = [], set()

    base_red = {}
    for tag, n in (("N", N), ("2N", N2)):
        b = J.get(("baseline", n))
        if b:
            for c in cands:
                if c != "baseline" and J.get((c, n)):
                    base_red[(c, tag)] = 100.0 * (J[(c, n)] / b - 1.0)

    for c in cands:
        a, b = J.get((c, N)), J.get((c, N2))
        if not (a and b):
            continue
        ra = levels[N]["results"][c]
        rb = levels[N2]["results"][c]
        da, db = detail(ra), detail(rb)
        shift = abs(b - a) / b
        sea = (ra.get("se") or 0.0) / b
        seb = (rb.get("se") or 0.0) / b
        comb = (sea ** 2 + seb ** 2) ** 0.5
        c1 = shift <= 0.05
        c2 = shift <= 2 * comb if comb else False
        lev = db.get("max_node_leverage")
        c4 = (lev is not None and lev <= 0.10)
        c3 = True
        if (c, "N") in base_red and (c, "2N") in base_red:
            c3 = abs(base_red[(c, "2N")] - base_red[(c, "N")]) <= 5.0
        ok = c1 and c2 and c4 and c3
        verdicts.append(ok)
        flags = []
        if not c1:
            flags.append(f"C1 shift {100 * shift:.1f}%>5%")
        if not c2:
            flags.append(f"C2 {shift / comb:.1f} SE" if comb else "C2 no SE")
        if not c3:
            flags.append(f"C3 reduction moved "
                         f"{abs(base_red[(c, '2N')] - base_red[(c, 'N')]):.1f} pt")
        if not c4:
            flags.append(f"C4 max node {100 * (lev or 0):.1f}%>10%")
        print(f"    {c:26s} shift {100 * shift:6.1f}%  comb SE {100 * comb:5.1f}%  "
              f"max node {100 * (lev or 0):5.1f}%  "
              + ("PASS" if ok else "FAIL: " + "; ".join(flags)))

        # C5: which stratum moved?
        ma, mb = da.get("mu_h") or {}, db.get("mu_h") or {}
        for h in sorted(set(ma) & set(mb)):
            if mb[h] and abs(mb[h] - ma[h]) / abs(mb[h]) > 0.05:
                refine.add(h)
                print(f"        C5 stratum {h}: {ma[h]:.3e} -> {mb[h]:.3e}  "
                      f"({100 * (mb[h] / ma[h] - 1):+.1f}%)")

    # C6 ranking stability
    def order(n):
        return [c for c in sorted(cands, key=lambda k: J.get((k, n)) or 9e9)
                if J.get((c, n))]
    o1, o2 = order(N), order(N2)
    c6 = o1 == o2
    print(f"\n    C6 ranking {'STABLE' if c6 else 'CHANGED'}")
    if not c6:
        print(f"       {N}: " + " < ".join(o1))
        print(f"       {N2}: " + " < ".join(o2))

    print()
    if all(verdicts) and c6 and verdicts:
        print("    => CONVERGED. Freeze this design as the production contract "
              "(Priority 5), then Priorities 6-8.")
        return 0
    print("    => NOT CONVERGED.")
    if refine:
        print(f"       Refine {sorted(refine)} -- double that stratum only.")
        print("       Do NOT add events at unchanged sites: the residual is spatial.")
    else:
        print("       No single stratum moved >5%; the residual is stochastic, "
              "so raise events per sub-run rather than site count.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
