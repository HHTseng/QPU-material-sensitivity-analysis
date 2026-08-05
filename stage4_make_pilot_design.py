"""Design-file generator for Stage-4 Cu backside-stack studies.

(The module name says "pilot" for historical reasons -- it began as the M0a
pilot only. It now holds every Stage-4 point set. Referenced by filename in the
plan's reproduce commands, so it is not renamed.)

Writes CSVs for stage 1's explicit-design mode (SENSITIVITY_DESIGN_FILE). No new
physics-writing code is needed for any of these studies: every quantity they
vary is already a swept column in sensitivity_params.py, so each study is just
rows of a design CSV consumed by the existing macro writer.

    setBotThickness    the thickness scan
    setBotAbs          0.0 gives the no-film control
    setIsland /        island size and pitch give coverage and pattern pitch;
    setIslandSpacing   (8000, 0) is a blanket film and (0, 8000) is bare Si,
                       matching FilmPreSets/setBot2BlanketFilm.mac and
                       setBot2NoGeom.mac
    setBotGapThres     DERIVED from setTopGap (see below)

Every other column is held at its default, so the top/junction stack is fixed
per the 2026-07-28 scoping decision (plan sec. 0, 3.1) and only the backside
moves. Stage 4 is Cu-only: this is backside-stack optimization, not
material-identity selection.

Coverage: WaffleKaplanElectrode absorbs on a cell of l_cell = l_island +
l_spacing with coverage ~ (l_island/l_cell)^2, so (200, 50) um is coverage 0.64
at pitch 250 um. island_spacing() inverts this for a target coverage and pitch.

setBotGapThres is derived, not free: G4CMPNormal ends the in-film cascade at
setBotQPLim x setBotGapThres while WaffleKaplanElectrode re-emits a secondary
only above 2 x setTopGap, and with setBotQPLim = 2 those coincide exactly iff
setBotGapThres == setTopGap (plan sec. 3.7, 6e). build_pilot_rows() enforces
this; stage 1's verify_derived_thresholds() re-checks it in every generated
macro.

Usage:
    python stage4_make_pilot_design.py --set pilot -o m0a_pilot_design.csv
    python stage4_make_pilot_design.py --set corner32 -o cu_corner32_design.csv


================================ EXECUTION LOG ================================

All runs on mimir, 32 workers, top stack fixed, Cu, 0 failures and 0 zero-byte
hits throughout. "bank" is SENSITIVITY_SEED_BANK_ID; a fresh bank means the
result was confirmed on random streams it was not selected on.

  #  set        run id (results/morris_mimir_...)      P   R  TOTAL/point  bank
  1  pilot      cc758fb7-315d-4ea6-96fd-9b869c436028  16   2   4,032,000     0
  2  promote    bb23db68-f2c7-4971-9552-beb39355ad53  16   8  40,320,000     1
  3  reference  5c8de0e4-76c2-46fa-99fd-abcac848a31f  16   8  40,320,000     2
  4  pitch      20f9ab95-0b06-4922-b2ad-c38549a9de26  16   8  40,320,000     3
  5  pitch      f3d38891-ea4b-4c6b-b4e1-50331cc0bb97  16   8  40,320,000     3
  6  gapthres   78e2b67c-0594-4f4f-9ae9-dfeb8178c102  16   8  40,320,000     4
  7  corner32   490102a3-f99b-4efb-9745-d7f033b0d11c  32   8  80,640,000     5
  8  m2         8bd59ac2-fce2-496f-8eff-6e8e1c9fcacd  16   4   8,064,000     6
  9  m2promote  9d1248ef-329a-4577-ac50-f6339e14b024  16   8  40,320,000     7

Runs 4 and 5 are the same design at two POSITION seeds (20260727, 20260801).
Run 7 uses the default position seed so its first 16 Sobol sites are exactly the
P=16 set (verified), making P16 a strict nested subset of P32.

RESULTS

1. Pilot (mean total_QPs). Mechanism confirmed: removing the absorber raises
   yield ~10x. Thickness monotone.
       no-film 775 | zero-coverage 866 | islands t=1 81, t=2 66, t=5 49,
       t=10 37 | blanket t=1 31, t=10 19

2. Promotion (R=8, all contrasts resolved at 2 sigma):
       islands t=1   195.0 +- 7.5
       islands t=10  130.5 +- 4.3     t=1 vs t=10:     -49.4%, z = 7.48
       blanket t=1    93.5 +- 5.0     t=10 vs blanket: -39.6%, z = 5.60
                                      t=1 vs blanket: -108.6%, z = 11.28
   The 10 um islands vs 1 um blanket ordering is INVERTED relative to Yelton et
   al. 2024 sec. 11, and the inversion is resolved rather than noise. Leading
   explanation (plan sec. 3.3a): this model contains no term penalising
   coverage, so it answers only the QP half of a two-term objective.

3. Reference, fresh bank: 185.8 +- 4.5 vs 195.0 +- 7.5 on bank 1, z = +1.06 --
   consistent. THE STAGE-4 CU REFERENCE VALUE is 185.8 +- 4.5 total_QPs at
   n_sim = 5,040,000/replica (QP yield 3.686e-05).

4/5. Pitch equivalence at constant coverage 0.64, two position seeds:
       posseed 20260727: +2.7%, z = 0.41
       posseed 20260801: +2.1%, z = 0.27
       pooled: +2.4% +- 4.9%  =>  |pitch effect| < 12% at 2 sigma
       interaction across position sets: z = +0.09 (not position-dependent)
   Pitch stays OUT of the QP objective and is handed to the EM study as a free
   lever. Note the position set shifted the ABSOLUTE level by ~12% while moving
   both geometries almost equally -- a common quadrature offset that cancels in
   contrasts but not in absolute yields.

6. setBotGapThres 180 vs 191 ueV: 181.5 +- 6.5 vs 181.5 +- 8.3, z = 0.00,
   95% CI +-11.7%. The dead band [360, 382] ueV carries no measurable signal.
   Pinned to the derived value; no earlier result changes.

7. Nested P16/P32 at the real M2 box corner (t = 10 um, coverage 0.90, pitch
   250 um -> island 237.17, spacing 12.83). Per-position mean yield, exposure
   matched at 315,000 events per position-replica:
       corner:    P16 3.91 +- 0.35   P32 4.23 +- 0.27   Y32/Y16-1 = +8.4 +- 11.9%
       reference: P16 11.59 +- 0.48  P32 11.95 +- 0.49  Y32/Y16-1 = +3.0 +- 6.0%
       corner/reference: 0.337 +- 0.033 (P16) -> 0.355 +- 0.027 (P32)
   Both pre-registered gates PASS (|Y32/Y16 - 1| < 10%; ordering unchanged), so
   M2's exploration grid may run at P=16, promoting the reference and any
   observed minimum to P=32 before a quantitative minimum claim. Caveats: the
   corner passes on +8.4% against +-11.9%, i.e. "not shown to exceed 10%", and
   one nested refinement shows stability for this Sobol refinement, not
   convergence to the continuous spatial integral.


8. M2 exploration grid, 28 points (t x f_cov), 1792 sub-runs. Coverage carries
   the surface: 15 of 24 adjacent coverage steps resolved, 4 of 21 thickness
   steps, and ZERO resolved increases on either axis. Fitted trend
   log(yield) ~ 1 + log(t) + f + f^2 has 9.8% RMS residual, 27.9% max, 11.4%
   leave-one-out. Grid minimum appeared at t=10, f=0.85.

9. M2 promotion, fresh bank 7 -- REVERSED the grid minimum. In the grid f=0.85
   beat f=0.90 by 10.6% but UNRESOLVED ([-13.6%, +36.1%]), so rule 4 forbade
   calling them ordered. At promotion fidelity f=0.90 beats f=0.85 by 21.3%
   [-28.4%, -10.7%], resolved. The f=0.85 cell moved +34% between banks while the
   reference moved -1.5%; it had the grid's worst conditioning (14.5% rel SEM,
   24 QPs, raw n_eff 6.2). Without the pre-registered rule this run would have
   reported a spurious interior minimum.

   BOX MINIMUM: t = 10 um, f_cov = 0.90, yield 1.225e-05, which is
   -66.1% [-68.9%, -62.5%] versus the reference -- consistent with the P32 corner
   run (-64.6% at P32, -66.3% at P16) across three runs, two fidelities, three
   seed banks. This is the minimum modeled QP yield WITHIN THE SAMPLED BOX, not a
   device optimum (plan sec. 3.3a, 6h).

Analysis artifacts: provenance/m2_grid_analysis.txt, provenance/m2_promotion.txt


PROVENANCE CAVEAT -- READ BEFORE REGENERATING AN OLD DESIGN

setBotGapThres derivation was added to build_pilot_rows() AFTER runs 1-6 were
generated, and sensitivity_params.py's default changed 180e-6 -> 191e-6 at the
same time. Verified from the stored macros:

    runs 1-6  setBotGapThres = 180e-6  (independent of setTopGap = 191e-6)
    run 7     setBotGapThres = 191e-6  (derived)

So regenerating the pilot/promote/reference/pitch CSVs today yields 191e-6 and
will NOT reproduce runs 1-6 byte-identically. Their results remain directly
comparable -- run 6 measured that exact difference at z = 0.00 -- but the
distinction is real and must not be silently papered over. To reproduce a stored
run exactly, pin setBotGapThres to 180e-6 in the point set.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
import math
import csv

import numpy as np

import stage1_run_simulations as s


# (label, {column substring: value}) -- label is documentation only; stage 1
# names design points by row index.
#
# Fragments carry the trailing space of the macro command, because "setIsland"
# is a prefix of "setIslandSpacing" and would otherwise match both. Ambiguity is
# an error, not a first-match, so this is enforced rather than remembered.
ISLAND = "setIsland "
SPACING = "setIslandSpacing "
THICK = "setBotThickness "
ABS = "setBotAbs "

PILOT_POINTS = [
    ("no_film_control",       {ABS: 0.0}),
    ("islands_t1_baseline",   {THICK: 1.0}),
    ("islands_t2",            {THICK: 2.0}),
    ("islands_t5",            {THICK: 5.0}),
    ("islands_t10",           {THICK: 10.0}),
    ("blanket_t1",            {THICK: 1.0, ISLAND: 8000.0, SPACING: 0.0}),
    ("blanket_t10",           {THICK: 10.0, ISLAND: 8000.0, SPACING: 0.0}),
    ("no_geom_zero_coverage", {ISLAND: 0.0, SPACING: 8000.0}),
]

# The pilot leaves exactly one comparison unresolved: 10 um islands versus a
# 1 um blanket film -- the analogue of the Yelton ordering, and the only pair
# whose central values invert relative to the paper. At pilot fidelity the 2s
# resolution of a two-candidate difference is ~23%, and the observed gap is
# ~18%, so the sign is not established. These three points are the promotion
# set: the contested pair plus the baseline for scale.
PROMOTE_POINTS = [
    ("islands_t1_baseline",   {THICK: 1.0}),
    ("islands_t10",           {THICK: 10.0}),
    ("blanket_t1",            {THICK: 1.0, ISLAND: 8000.0, SPACING: 0.0}),
]

# The Stage-4 reference geometry: Cu, 64% coverage (island 200 um, spacing
# 50 um -> pitch 250 um), 1 um thick, top stack fixed. This is the as-designed
# device geometry and the M0a islands baseline. Run on a fresh seed bank it is
# the final-validation point every later Cu result is quoted against.
REFERENCE_POINTS = [
    ("cu_reference_t1_cov064_pitch250", {THICK: 1.0}),
]

# Pitch-equivalence check at CONSTANT coverage 0.64: (200, 50) and (400, 100)
# both give (I/(I+S))^2 = 0.64 at pitch 250 um and 500 um.
#
# This set is only interpretable when run at two or more POSITION seeds. The 16
# injection sites are fixed ABSOLUTE Sobol coordinates over the substrate, while
# WaffleKaplanElectrode phases on fmod(|x|, l_island + l_spacing) -- so doubling
# the pitch re-phases every site against the island pattern. A pitch difference
# measured at one position set is therefore confounded with a change in which
# part of the cell each caustic happens to land on, and no amount of extra
# Geant4 events at those same 16 sites removes it: more events shrink the Monte
# Carlo term, not the spatial-quadrature term.
PITCH_POINTS = [
    ("cu_pitch250_cov064", {THICK: 1.0, ISLAND: 200.0, SPACING: 50.0}),
    ("cu_pitch500_cov064", {THICK: 1.0, ISLAND: 400.0, SPACING: 100.0}),
]

GAPTHRES = "setBotGapThres "

# setBotGapThres sensitivity, at reference geometry.
#
# G4CMPNormal terminates the in-film cascade at lowQPLimit x gapThreshold, and
# WaffleKaplanElectrode re-emits a secondary only above 2 x setTopGap. With
# setBotQPLim = 2 the two coincide exactly iff setBotGapThres == setTopGap.
# The template ships 180 ueV against setTopGap = 191 ueV, so the cascade floor
# (360 ueV) sits below the re-emission gate (382 ueV), leaving a band in which
# quasiparticles are tracked but their phonons are discarded. Plan sec. 3.7 says
# this threshold must be DERIVED from the junction gap, not floated.
#
# 191e-6 is the derived value. This set measures whether the correction moves
# the answer, rather than arguing about it.
GAPTHRES_POINTS = [
    ("gapthres_180_current", {THICK: 1.0, GAPTHRES: 180e-6}),
    ("gapthres_191_derived", {THICK: 1.0, GAPTHRES: 191e-6}),
]

def island_spacing(coverage, pitch_um):
    """(island, spacing) in um for a target coverage at a given pitch.

    Inverse of coverage = (I/(I+S))^2 with pitch p = I + S:
        I = p*sqrt(f),  S = p*(1 - sqrt(f)).
    """
    island = pitch_um * math.sqrt(coverage)
    return island, pitch_um - island


# Nested P16/P32 spatial-convergence check, run at the ACTUAL low-QP corner of
# the documented M2 box -- t = 10 um, coverage 0.9, pitch 250 um -- not at the
# blanket film. blanket (coverage 1.0) is outside the box [0.3, 0.9] and is a
# qualitatively different no-gap geometry, so it is the wrong gate for a study
# whose coverage axis stops at 0.9.
#
# The reference travels with it so the corner/reference RATIO can be compared at
# P16 and P32: the ratio is what M2 actually reports, and it is more robust than
# either absolute value.
_CORNER_I, _CORNER_S = island_spacing(0.90, 250.0)
CORNER32_POINTS = [
    ("cu_corner_t10_cov090",    {THICK: 10.0, ISLAND: _CORNER_I, SPACING: _CORNER_S}),
    ("cu_reference_t1_cov064",  {THICK: 1.0,  ISLAND: 200.0,     SPACING: 50.0}),
]

# --- M2: the thickness x coverage response surface -------------------------
#
# 4 thicknesses x 7 coverages = 28 points at fixed pitch 250 um. Coverage grid
# includes both physical boundaries of the box and the exact reference value
# 0.64, so the reference is a grid point rather than an interpolation.
#
# Coverage is NOT optimized here (plan sec. 3.3a): the model contains no term
# penalising it, so its unconstrained optimum is trivially the maximum. The
# deliverable is the surface plus the trade-off it implies, and any minimum is
# reported as "minimum modeled QP yield within the sampled box".
M2_THICKNESSES_UM = (1.0, 2.0, 5.0, 10.0)
M2_COVERAGES = (0.30, 0.45, 0.60, 0.64, 0.75, 0.85, 0.90)
M2_PITCH_UM = 250.0

M2_POINTS = []
for _t in M2_THICKNESSES_UM:
    for _f in M2_COVERAGES:
        _i, _s = island_spacing(_f, M2_PITCH_UM)
        M2_POINTS.append((f"m2_t{_t:g}_cov{_f:.2f}",
                          {THICK: _t, ISLAND: _i, SPACING: _s}))

# M2 promotion. The exploration grid's lowest cell was t=10 um, f=0.85 -- NOT the
# anticipated box corner f=0.90 -- and the 0.85 vs 0.90 step is unresolved
# (+10.6%, 95% CI [-13.6%, +36.1%]). Per the pre-registered rules an unexpected
# minimum must be promoted before it is compared quantitatively with the corner,
# so both candidates plus the reference run together at promotion fidelity on a
# fresh seed bank. Reference travels along so the contrast is paired within one
# run rather than across seed banks.
_M2P_I85, _M2P_S85 = island_spacing(0.85, M2_PITCH_UM)
_M2P_I90, _M2P_S90 = island_spacing(0.90, M2_PITCH_UM)
M2PROMOTE_POINTS = [
    ("m2p_t10_cov085_observed_min", {THICK: 10.0, ISLAND: _M2P_I85, SPACING: _M2P_S85}),
    ("m2p_t10_cov090_box_corner",   {THICK: 10.0, ISLAND: _M2P_I90, SPACING: _M2P_S90}),
    ("m2p_reference_t1_cov064",     {THICK: 1.0,  ISLAND: 200.0,    SPACING: 50.0}),
]

# Thickness trade-off at the front's high-coverage end. t=5 vs t=10 at f=0.90 is
# the most consequential unresolved thickness step: if thickness is a formal cost
# axis (Cu volume / stress), 5 um at statistical parity would dominate 10 um. Run
# at P=32 because the front sits where the spatial quadrature is thinnest.
_T90_I, _T90_S = island_spacing(0.90, M2_PITCH_UM)
THICK90_POINTS = [
    ("t5_cov090",  {THICK: 5.0,  ISLAND: _T90_I, SPACING: _T90_S}),
    ("t10_cov090", {THICK: 10.0, ISLAND: _T90_I, SPACING: _T90_S}),
]

POINT_SETS = {"pilot": PILOT_POINTS, "promote": PROMOTE_POINTS,
              "thick90": THICK90_POINTS,
              "reference": REFERENCE_POINTS, "pitch": PITCH_POINTS,
              "gapthres": GAPTHRES_POINTS, "corner32": CORNER32_POINTS,
              "m2": M2_POINTS, "m2promote": M2PROMOTE_POINTS}


def resolve_column(names, fragment):
    """Index of the one design column containing `fragment`.

    Ambiguity is refused rather than resolved by picking the first match: the
    row is consumed positionally by generate_runfiles(), so silently writing a
    value into a neighbouring column would run to completion with wrong physics.
    """
    hits = [i for i, name in enumerate(names) if fragment in name]
    if len(hits) != 1:
        raise ValueError(
            f"{fragment!r} matches {len(hits)} design columns "
            f"({[names[i] for i in hits]}); expected exactly one."
        )
    return hits[0]


TOPGAP = "setTopGap "


def build_pilot_rows(points):
    """Rows for one point set, with setBotGapThres DERIVED from setTopGap.

    Plan sec. 3.7: setBotGapThres is the energy below which down-converted
    phonons stop being dangerous to the junction, so it is a function of the
    junction gap, not an independent bottom-film property. Deriving it here --
    in the domain layer that builds designs -- rather than trusting the default
    means it stays correct even if someone edits sensitivity_params.py.

    The derivation is applied BEFORE the per-point overrides, so a point set
    that deliberately varies the threshold (GAPTHRES_POINTS) still wins.
    """
    names, defaults = s.build_default_design(1)
    base = defaults[0]
    i_thres, i_topgap = resolve_column(names, GAPTHRES), resolve_column(names, TOPGAP)

    rows, labels = [], []
    for label, overrides in points:
        row = np.array(base, dtype=float)
        row[i_thres] = row[i_topgap]          # derived
        for fragment, value in overrides.items():
            row[resolve_column(names, fragment)] = value
        rows.append(row)
        labels.append(label)
    return names, rows, labels


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--out", default="m0a_pilot_design.csv",
                        help="output CSV path (default: m0a_pilot_design.csv)")
    parser.add_argument("--set", choices=sorted(POINT_SETS), default="pilot",
                        help="which point set to write (default: pilot)")
    args = parser.parse_args()

    names, rows, labels = build_pilot_rows(POINT_SETS[args.set])

    with open(args.out, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(names)
        writer.writerows(rows)

    # Semantic sidecar. Stage 1's run_metadata.json records the design CSV's
    # sha256 but not what its rows MEAN -- the labels live here, and the same
    # hash is the join key. Without this, a stored run is a matrix of numbers
    # whose row 3 is "t = 5 um" only in someone's memory.
    cols = [THICK, ABS, ISLAND, SPACING, GAPTHRES, TOPGAP]
    idx = {c: resolve_column(names, c) for c in cols}
    sidecar = {
        "design_file": os.path.basename(args.out),
        "design_file_sha256": hashlib.sha256(
            open(args.out, "rb").read()).hexdigest(),
        "point_set": args.set,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator_sha256": hashlib.sha256(
            open(__file__, "rb").read()).hexdigest(),
        "rows": [],
    }
    for r, (row, label) in enumerate(zip(rows, labels)):
        island, spacing = row[idx[ISLAND]], row[idx[SPACING]]
        cell = island + spacing
        # Cast out of numpy: np.float64/np.bool_ are not JSON-serializable, and
        # a provenance file that fails to write is worse than none at all.
        sidecar["rows"].append({
            "row": r,
            "sample_name": f"<prefix>{r}",
            "label": label,
            "setBotThickness_um": float(row[idx[THICK]]),
            "setBotAbs": float(row[idx[ABS]]),
            "island_um": float(island),
            "spacing_um": float(spacing),
            "pitch_um": float(cell),
            "coverage": float((island / cell) ** 2) if cell > 0 else None,
            "setBotGapThres_eV": float(row[idx[GAPTHRES]]),
            "setTopGap_eV": float(row[idx[TOPGAP]]),
            "gapthres_is_derived": bool(row[idx[GAPTHRES]] == row[idx[TOPGAP]]),
        })
    sidecar_path = os.path.splitext(args.out)[0] + ".labels.json"
    with open(sidecar_path, "w") as handle:
        json.dump(sidecar, handle, indent=2)

    print(f"Wrote {len(rows)} design points x {len(names)} parameters to {args.out}")
    print(f"Wrote semantic labels + hashes to {sidecar_path}")
    print("")
    cols = [THICK, ABS, ISLAND, SPACING]
    idx = [resolve_column(names, c) for c in cols]
    print(f"{'row':>3}  {'label':22s} " + " ".join(f"{c.strip():>17s}" for c in cols) + "   coverage")
    for r, (row, label) in enumerate(zip(rows, labels)):
        island, spacing = row[idx[2]], row[idx[3]]
        cell = island + spacing
        coverage = (island / cell) ** 2 if cell > 0 else float("nan")
        print(f"{r:>3}  {label:22s} " + " ".join(f"{row[i]:17g}" for i in idx)
              + f"   {coverage:.3f}")


if __name__ == "__main__":
    main()
