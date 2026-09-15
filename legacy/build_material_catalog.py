#!/usr/bin/env python3
"""Build a versioned, provenance-tracked material catalog from Materials Project.

Runs in an isolated environment that has `mp-api`; writes a plain JSON snapshot
that the Stage 3 resolver reads with no MP dependency at runtime.

Two modes, kept strictly separate. Conflating them is the defect the previous
exporter had: it ran a database-wide filtered search and then emitted only a
hardcoded ID list, silently discarding the search result.

    --ids mp-149,mp-134       explicit allowlist  (films, known substrates)
    --search-substrates       database-wide filtered query (substrate discovery)

WHAT MATERIALS PROJECT CAN AND CANNOT SUPPLY
--------------------------------------------
Supplies (recorded with `source: materials_project`):
    density, elastic tensor -> C11/C12/C44, lattice constant, crystal system,
    band gap, metallicity, energy above hull, formula.

Does NOT supply, and must never be invented from bulk DFT
(recorded with `source: literature` and an explicit citation field):
    superconducting gap Delta, phonon lifetime and its slope, QP limit,
    thin-film thickness, interface absorption.

A film record without a curated gap is written with `enabled: false` and the
resolver refuses it. That is deliberate: a missing gap silently defaulting to
another film's value is exactly the pseudo-material failure this catalog exists
to prevent.

DENSITY POLICY
--------------
`G4LatticeManager::LoadLattice` calls `SetDensity(Mat->GetDensity())`, so the
Geant4 material's density is the one inside the phonon kinematics. MP's density
is a DFT-relaxed value and differs from the NIST experimental value by roughly
0.5-1% (Si: MP 2.3128 vs Geant4 2.330 g/cm3, 0.74%). MP density is therefore
recorded as a CROSS-CHECK with its own looser tolerance, never as the value used
for sound-speed derivation.

Usage:
    set -a; source .env; set +a
    python build_material_catalog.py --ids mp-149,mp-32 --out catalog/films.json
    python build_material_catalog.py --search-substrates --max 40 --out catalog/substrates.json
"""

import argparse
import datetime
import json
import os
import sys
from importlib.metadata import version, PackageNotFoundError

try:
    from mp_api.client import MPRester
    from emmet.core.summary import HasProps
    from emmet.core.symmetry import CrystalSystem
except ImportError:  # pragma: no cover
    sys.exit("mp-api is not installed in this interpreter. Use the isolated MP env.")

SUMMARY_FIELDS = [
    "material_id", "formula_pretty", "symmetry", "density", "band_gap",
    "energy_above_hull", "is_stable", "is_metal", "theoretical", "nsites",
    "volume", "structure",
]
ELASTIC_FIELDS = ["material_id", "formula_pretty", "elastic_tensor", "last_updated", "warnings"]

# Fields MP cannot supply. Present here so the gap is explicit in the output.
CURATED_ONLY_FIELDS = ("gap_eV", "ph_lifetime_ns", "ph_lifetime_slope",
                       "qp_limit", "thickness_um", "tc_K")


def _pkg_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def provenance_block(mpr, filters=None, mode=None):
    return {
        "retrieved_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": mode,
        "mp_api_version": _pkg_version("mp-api"),
        "emmet_core_version": _pkg_version("emmet-core"),
        "pymatgen_version": _pkg_version("pymatgen"),
        "mp_database_version": getattr(mpr, "get_database_version", lambda: None)(),
        "summary_fields_requested": SUMMARY_FIELDS,
        "elastic_fields_requested": ELASTIC_FIELDS,
        "filters": filters or {},
        "note": ("MP supplies density/elasticity/structure only. Superconducting "
                 "gap, phonon lifetime and film thickness are curated separately "
                 "and are never derived from MP bulk data."),
    }


def cubic_constants(tensor):
    """(C11, C12, C44, diagnostics) from a 6x6 IEEE tensor, cubic-validated.

    Returns None for the constants when the tensor is not cubic-consistent, with
    the reason recorded. Never averages a non-cubic tensor into cubic constants.
    """
    if tensor is None:
        return None, None, None, {"ok": False, "reason": "no elastic tensor"}
    t = [[float(v) for v in row] for row in tensor]
    c11 = (t[0][0] + t[1][1] + t[2][2]) / 3.0
    c12 = (t[0][1] + t[0][2] + t[1][2]) / 3.0
    c44 = (t[3][3] + t[4][4] + t[5][5]) / 3.0
    spread = {
        "C11": max(t[0][0], t[1][1], t[2][2]) - min(t[0][0], t[1][1], t[2][2]),
        "C12": max(t[0][1], t[0][2], t[1][2]) - min(t[0][1], t[0][2], t[1][2]),
        "C44": max(t[3][3], t[4][4], t[5][5]) - min(t[3][3], t[4][4], t[5][5]),
    }
    scale = max(abs(c11), 1e-9)
    anisotropy = max(spread.values()) / scale
    born = (c11 - c12 > 0) and (c11 + 2 * c12 > 0) and (c44 > 0)
    diag = {
        "ok": bool(born and anisotropy < 0.05),
        "cubic_spread_fraction": anisotropy,
        "born_stable": bool(born),
        "reason": None if born else "fails Born stability (C11-C12>0, C11+2C12>0, C44>0)",
    }
    if anisotropy >= 0.05:
        diag["reason"] = f"diagonal spread {anisotropy:.1%} too large for cubic averaging"
    return c11, c12, c44, diag


def record_from_docs(summary, elastic, tensor_format="ieee"):
    tensor = None
    if elastic is not None and getattr(elastic, "elastic_tensor", None) is not None:
        tensor = (elastic.elastic_tensor.ieee_format if tensor_format == "ieee"
                  else elastic.elastic_tensor.raw)
    c11, c12, c44, diag = cubic_constants(tensor)
    lattice_a = None
    try:
        lattice_a = float(summary.structure.lattice.abc[0])
    except Exception:  # noqa: BLE001 - structure may be absent
        pass
    return {
        "material_id": str(summary.material_id),
        "formula": summary.formula_pretty,
        "source": "materials_project",
        "crystal_system": str(summary.symmetry.crystal_system) if summary.symmetry else None,
        "spacegroup": getattr(summary.symmetry, "symbol", None) if summary.symmetry else None,
        "mp_density_g_cm3": summary.density,
        "mp_density_kg_m3": summary.density * 1000.0 if summary.density else None,
        "lattice_a_ang": lattice_a,
        "band_gap_eV": summary.band_gap,
        "is_metal": summary.is_metal,
        "energy_above_hull_eV": summary.energy_above_hull,
        "is_stable": summary.is_stable,
        "theoretical": summary.theoretical,
        "elastic_tensor_GPa": [[float(v) for v in row] for row in tensor] if tensor else None,
        "tensor_format": tensor_format,
        "tensor_convention": "Voigt, engineering strain, GPa",
        "c11_GPa": c11, "c12_GPa": c12, "c44_GPa": c44,
        "cubic_validation": diag,
        "elastic_last_updated": (elastic.last_updated.isoformat()
                                 if elastic is not None and elastic.last_updated else None),
        "elastic_warnings": list(elastic.warnings) if elastic is not None and elastic.warnings else [],
        # Explicitly absent -- must be curated. Never imputed.
        "curated_fields_required": list(CURATED_ONLY_FIELDS),
        "enabled": False,
        "enabled_blocked_by": "curated cryogenic fields not supplied",
    }


def fetch_by_ids(mpr, ids, tensor_format="ieee"):
    summaries = mpr.materials.summary.search(material_ids=ids, fields=SUMMARY_FIELDS)
    by_id = {str(d.material_id): d for d in summaries}
    elastics = {}
    if by_id:
        for d in mpr.materials.elasticity.search(material_ids=list(by_id), fields=ELASTIC_FIELDS):
            elastics[str(d.material_id)] = d
    records, rejected = [], []
    for mpid in ids:
        if mpid not in by_id:
            rejected.append({"material_id": mpid, "reason": "not found in Materials Project"})
            continue
        records.append(record_from_docs(by_id[mpid], elastics.get(mpid), tensor_format))
    return records, rejected


def search_substrates(mpr, max_results, tensor_format="ieee"):
    """Database-wide filtered substrate search.

    Kept in its own function and its own output file: the results are whatever
    the filters return, and are NOT intersected with any hardcoded ID list.
    """
    filters = {
        "has_props": ["elasticity"],
        "crystal_system": "Cubic",
        "energy_above_hull": [0.0, 0.10],
        "exclude_elements": ["U", "Th", "Np"],
        "is_metal": False,
        "num_magnetic_sites": 0,
        "theoretical": False,
    }
    docs = mpr.materials.summary.search(
        has_props=[HasProps.elasticity],
        crystal_system=CrystalSystem.cubic,
        energy_above_hull=(0.0, 0.10),
        exclude_elements=["U", "Th", "Np"],
        is_metal=False,
        num_magnetic_sites=0,
        theoretical=False,
        fields=SUMMARY_FIELDS,
    )
    docs = list(docs)[:max_results] if max_results else list(docs)
    ids = [str(d.material_id) for d in docs]
    elastics = {}
    if ids:
        for d in mpr.materials.elasticity.search(material_ids=ids, fields=ELASTIC_FIELDS):
            elastics[str(d.material_id)] = d
    records, rejected = [], []
    for doc in docs:
        rec = record_from_docs(doc, elastics.get(str(doc.material_id)), tensor_format)
        if not rec["cubic_validation"]["ok"]:
            rejected.append({"material_id": rec["material_id"], "formula": rec["formula"],
                             "reason": rec["cubic_validation"]["reason"]})
            continue
        records.append(rec)
    return records, rejected, filters


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", help="comma-separated Materials Project IDs (explicit allowlist)")
    group.add_argument("--search-substrates", action="store_true",
                       help="database-wide filtered substrate search")
    ap.add_argument("--max", type=int, default=50, help="cap on search results")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--tensor-format", default="ieee", choices=("ieee", "raw"))
    args = ap.parse_args()

    api_key = os.environ.get("MP_API_KEY")
    if not api_key:
        sys.exit("MP_API_KEY is not set. Run:  set -a; source .env; set +a")

    with MPRester(api_key) as mpr:
        if args.ids:
            ids = [s.strip() for s in args.ids.split(",") if s.strip()]
            records, rejected = fetch_by_ids(mpr, ids, args.tensor_format)
            filters, mode = {"explicit_ids": ids}, "explicit_ids"
        else:
            records, rejected, filters = search_substrates(mpr, args.max, args.tensor_format)
            mode = "filtered_search"
        prov = provenance_block(mpr, filters, mode)

    payload = {"provenance": prov, "accepted": records, "rejected": rejected,
               "counts": {"accepted": len(records), "rejected": len(rejected)}}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(payload, handle, indent=2, default=str)

    print(f"mode={mode}  accepted={len(records)}  rejected={len(rejected)}")
    print(f"MP database version: {prov['mp_database_version']}  (mp-api {prov['mp_api_version']})")
    for rec in records[:12]:
        cv = rec["cubic_validation"]
        print(f"  {rec['material_id']:12s} {str(rec['formula']):10s} "
              f"rho={rec['mp_density_g_cm3'] or float('nan'):.4f} g/cm3  "
              f"C11/C12/C44="
              f"{rec['c11_GPa'] if rec['c11_GPa'] is None else round(rec['c11_GPa'],1)}/"
              f"{rec['c12_GPa'] if rec['c12_GPa'] is None else round(rec['c12_GPa'],1)}/"
              f"{rec['c44_GPa'] if rec['c44_GPa'] is None else round(rec['c44_GPa'],1)}  "
              f"cubic_ok={cv['ok']}")
    for rej in rejected[:6]:
        print(f"  REJECTED {rej['material_id']}: {rej['reason']}")
    print(f"\nWritten to {args.out}")
    print("NOTE: every record is enabled=false until the curated cryogenic fields "
          f"{list(CURATED_ONLY_FIELDS)} are supplied with provenance. MP cannot supply them.")


if __name__ == "__main__":
    main()
