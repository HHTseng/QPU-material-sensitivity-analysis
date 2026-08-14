#!/usr/bin/env python3
"""Export the Stage 3 ledger to CSV and show which parameters actually varied.

Answers three questions directly from recorded data, never from memory:

  1. Which `parameter_set.txt` "tune" entries did the campaign actually vary,
     and to what values?
  2. Which did NOT vary, and why?
  3. What is the ranking, with per-trial provenance?

The parameter mapping matters because Stage 3 runs a **linked-material** search
(pipeline sec 3.2, interpretation 1): a candidate material is chosen as a unit
and its properties travel together. It is NOT a free sweep of the individual
macro parameters -- that is interpretation 2, and it can invent pseudo-materials
by combining one material's gap with another's stiffness. So each
`parameter_set.txt` entry is varied *through* a material choice, or is held
fixed. This report makes that mapping explicit rather than implied.

Usage:
    python stage3_report.py                       # summary + mapping
    python stage3_report.py --csv results.csv     # full per-trial export
"""

import argparse
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from stage3_ledger import Ledger   # noqa: E402

# parameter_set.txt "Tune the following parameters" -> how Stage 3 varies it.
# `via` is the decision that carries it; None means it was held fixed.
TUNE_MAP = [
    ("/main/detector_param/setTopFilmVSound",  "top_ground_film", "vsound_km_s"),
    ("/main/detector_param/setTopFilmGap",     "top_ground_film", "gap_eV"),
    ("/main/detector_param/setTopFilmPhLifetime", "top_ground_film", "ph_lifetime_ns"),
    ("/main/detector_param/setBotVSound",      "bottom_film", "vsound_km_s"),
    ("/main/detector_param/setBotGap",         None, "fixed 0.0 -- a [0,0] interval is not a decision"),
    ("/main/detector_param/setBotGapThres",    None, "fixed 180e-6 -- same for every enabled bottom film"),
    ("/main/detector_param/setBotPhLifetime",  "bottom_film", "ph_lifetime_ns"),
    ("/g4cmp/temperature",                     None, "fixed 0.0 K -- not varied in this campaign"),
    ("cubic",                                  "substrate", "native lattice constant"),
    ("stiffness 1 1",                          "substrate", "c11_GPa"),
    ("stiffness 1 2",                          "substrate", "c12_GPa"),
    ("stiffness 4 4",                          "substrate", "c44_GPa"),
    ("scat",                                   "substrate", "native G4CMP record"),
    ("decay",                                  "substrate", "native G4CMP record"),
    ("decayTT",                                "substrate", "native G4CMP record"),
    ("/main/detector_param/setMiller",         None, "fixed (0,0,1) -- orientation not varied"),
    ("/main/detector_param/setLatticeDeg",     None, "fixed 45 deg -- orientation not varied"),
]

# parameter_set.txt "Calculated and determined by the tuned parameters"
DERIVED_MAP = [
    ("vsound",        "derived from the candidate tensor + Geant4 density"),
    ("vtrans",        "derived likewise; 0 < vtrans < vsound enforced"),
    ("setTopAbs",     "interface model, substrate / fixed Al junction"),
    ("setTopFilmAbs", "interface model, substrate / top ground film"),
    ("setBotAbs",     "interface model, substrate / bottom film"),
    ("Debye",         "taken from the candidate's native G4CMP record"),
]


def load_rows(ledger):
    rows = []
    for r in ledger.observations():
        cand = json.loads(r["candidate"])
        der = json.loads(r["derived"])
        rows.append({
            "trial_id": r["trial_id"],
            "name": f"{cand['substrate']}/{cand['top_ground_film']}/{cand['bottom_film']}",
            "substrate": cand["substrate"],
            "top_ground_film": cand["top_ground_film"],
            "bottom_film": cand["bottom_film"],
            "status": r["status"],
            "total_qps": r["total_qps"],
            "qps_per_primary": r["qps_per_primary"],
            "events_total": r["events_total"],
            "n_positions": r["n_positions"],
            "n_replicas": r["n_replicas"],
            "runtime_s": r["runtime_s"],
            "vsound_m_s": der.get("vsound_m_s"),
            "vtrans_m_s": der.get("vtrans_m_s"),
            "substrate_density_kg_m3": der.get("substrate_density_kg_m3"),
            "c11_GPa": der.get("c11_GPa"), "c12_GPa": der.get("c12_GPa"),
            "c44_GPa": der.get("c44_GPa"),
            "setTopAbs": der.get("setTopAbs"), "setTopFilmAbs": der.get("setTopFilmAbs"),
            "setBotAbs": der.get("setBotAbs"),
            "top_film_gap_eV": der.get("top_film_gap_eV"),
            "interface_model": der.get("interface_model"),
            "cache_key": r["cache_key"],
            "run_dir": r["run_dir"],
        })
    return sorted(rows, key=lambda d: d["total_qps"])


def print_mapping(rows, catalog_path):
    import yaml
    cat = yaml.safe_load(open(catalog_path)) if os.path.isfile(catalog_path) else {}
    used = {"substrate": sorted({r["substrate"] for r in rows}),
            "top_ground_film": sorted({r["top_ground_film"] for r in rows}),
            "bottom_film": sorted({r["bottom_film"] for r in rows})}

    print("parameter_set.txt 'tune' entries -> what this campaign actually did\n")
    print(f"{'parameter':42s} {'varied via':16s} values")
    print("-" * 100)
    varied = fixed = 0
    for param, via, field in TUNE_MAP:
        if via is None:
            print(f"{param:42s} {'(held fixed)':16s} {field}")
            fixed += 1
            continue
        varied += 1
        section = {"substrate": "substrates", "top_ground_film": "top_films",
                   "bottom_film": "bottom_films"}[via]
        vals = []
        for mat in used[via]:
            rec = (cat.get(section) or {}).get(mat, {})
            v = rec.get(field)
            vals.append(f"{mat}={v}" if v is not None else f"{mat}=<native>")
        print(f"{param:42s} {via:16s} {', '.join(vals)}")
    print("-" * 100)
    print(f"{varied} varied through a material choice, {fixed} held fixed.\n")

    print("parameter_set.txt 'calculated/determined' entries -> computed per candidate\n")
    for name, how in DERIVED_MAP:
        print(f"  {name:14s} {how}")
    print()


def print_ranking(rows, baseline):
    base = next((r for r in rows if r["name"] == baseline), None)
    print(f"RANKING ({len(rows)} scored trials, lower = less QP damage)")
    if base:
        print(f"baseline {baseline} = {base['total_qps']:.0f} QPs\n")
    print(f"{'candidate':16s} {'total_QPs':>9s} {'QPs/event':>11s} "
          f"{'vs base':>9s} {'z':>6s}  interfaces (top/film/bot)")
    for r in rows:
        if base and r["name"] != baseline:
            d = r["total_qps"] - base["total_qps"]
            sigma = math.sqrt(r["total_qps"] + base["total_qps"]) or 1.0
            cmp_s = f"{100 * d / base['total_qps']:+7.1f}% {d / sigma:+6.1f}"
        else:
            cmp_s = f"{'baseline':>16s}"
        print(f"{r['name']:16s} {r['total_qps']:9.0f} {r['qps_per_primary']:11.3e} {cmp_s}  "
              f"{r['setTopAbs']:.3f}/{r['setTopFilmAbs']:.3f}/{r['setBotAbs']:.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage3_trials.sqlite"))
    ap.add_argument("--catalog", default=os.path.join(HERE, "material_catalog.yaml"))
    ap.add_argument("--csv", default=None, help="write the full per-trial export here")
    ap.add_argument("--baseline", default="Si/Nb/Cu")
    args = ap.parse_args()

    with Ledger(args.ledger) as ledger:
        rows = load_rows(ledger)
        status = ledger.summary()
    if not rows:
        print(f"No scored trials in {args.ledger}")
        return 1

    print(f"Ledger: {args.ledger}")
    print(f"Trials by status: {status}\n")
    print_mapping(rows, args.catalog)
    print_ranking(rows, args.baseline)

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
