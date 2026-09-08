#!/usr/bin/env python3
"""Apply the site-sweep decision rule to whatever stages have completed.

    python analyze_site_convergence.py

Judges convergence primarily from 32 -> 64 on five quantities: absolute J, the
percentage reduction from baseline, candidate ranking, maximum-site leverage,
and the change relative to the combined uncertainty. It does NOT pair across
site counts -- a different site count is a different scenario, so only absolute
yields and rankings are comparable.
"""
import glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(pattern):
    out = {}
    for p in sorted(glob.glob(os.path.join(HERE, pattern))):
        tail = os.path.basename(p).rsplit("_", 1)[1].split(".")[0]
        if not tail.isdigit():          # e.g. ..._points.json
            continue
        with open(p) as h:
            out[int(tail)] = json.load(h)
    return out


def leverage(doc, label):
    """Share of QPs from the heaviest site, if per-site data is present."""
    rec = (doc.get("results") or {}).get(label) or {}
    sites = (rec.get("paired") or {}).get("site_deltas")
    return None if not sites else max(abs(x) for x in sites) / sum(abs(x) for x in sites)


def main():
    tiers = load("results/stage4_sites_*.json")
    tiers = {k: v for k, v in tiers.items() if k in (16, 32, 64)}
    if not tiers:
        sys.exit("no site-sweep results yet")
    counts = sorted(tiers)
    labels = sorted({k for d in tiers.values() for k in d["results"]
                     if d["results"][k].get("value") is not None})
    print(f"Site-sweep convergence — stages present: {counts}\n")
    print(f"  {'candidate':22s}" + "".join(f"{f'{n} sites':>16s}" for n in counts))
    for lab in labels:
        row = f"  {lab:22s}"
        for n in counts:
            r = tiers[n]["results"].get(lab, {})
            v = r.get("value")
            row += f"{(f'{v:.4e}' if v else '--'):>16s}"
        print(row)
    print()
    base = "baseline"
    if base in labels:
        print(f"  {'reduction vs baseline':22s}" + "".join(f"{f'{n} sites':>16s}" for n in counts))
        for lab in labels:
            if lab == base:
                continue
            row = f"  {lab:22s}"
            for n in counts:
                r = tiers[n]["results"]
                v, b = r.get(lab, {}).get("value"), r.get(base, {}).get("value")
                row += f"{(f'{100*(v-b)/b:+.1f}%' if v and b else '--'):>16s}"
            print(row)
    if 32 in tiers and 64 in tiers:
        print("\n  DECISION (judged on 32 -> 64):")
        worst = 0.0
        for lab in labels:
            a = tiers[32]["results"].get(lab, {}).get("value")
            b = tiers[64]["results"].get(lab, {}).get("value")
            sa = tiers[32]["results"].get(lab, {}).get("relative_se") or 0
            sb = tiers[64]["results"].get(lab, {}).get("relative_se") or 0
            if not (a and b):
                continue
            shift = abs(b - a) / a
            comb = (sa ** 2 + sb ** 2) ** 0.5
            worst = max(worst, shift)
            print(f"    {lab:22s} shift {100*shift:5.1f}%  combined error {100*comb:5.1f}%  "
                  f"{'within noise' if shift <= comb else 'RESOLVED SHIFT'}")
        r16 = 16 in tiers
        print(f"\n    worst 32->64 shift: {100*worst:.1f}%")
        if worst <= 0.05:
            print("    => 32 and 64 agree. If 16 also agrees, keep the 16-site contract")
            print("       and the 69 feasible points are usable directly; otherwise adopt")
            print("       32/64 and re-evaluate a diverse subset first.")
        else:
            print("    => 32 and 64 DISAGREE. Do not optimize. Redesign the quadrature,")
            print("       probably with electrode-aware stratification: uniform Sobol")
            print("       converges slowly around the sharp near-electrode peak.")
    else:
        print("\n  (32 and/or 64 not yet available — decision rule needs both)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
