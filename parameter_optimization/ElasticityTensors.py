#!/usr/bin/env python3

"""
Download elastic stiffness tensors Cij from Materials Project.

Outputs:
    cij_tensors.csv
    cij_tensors.json
    cij_availability.csv

Install:
    pip install mp-api

Set your Materials Project API key:
    export MP_API_KEY="your_api_key_here"

Then run:
    python download_cij.py
"""

import csv
import json
import os

from mp_api.client import MPRester
from emmet.core.summary import HasProps
from emmet.core.symmetry import CrystalSystem


# ============================================================
# USER INPUT
# ============================================================

MATERIAL_IDS = [
    "mp-149",
    "mp-13",
    "mp-2534",
    # add more MP IDs here
]

# Choose:
#   "ieee" -> standardized IEEE orientation
#   "raw"  -> tensor in the orientation used in the MP calculation
TENSOR_FORMAT = "ieee"

CSV_FILE = "cij_tensors.csv"
JSON_FILE = "cij_tensors.json"
AVAILABILITY_FILE = "cij_availability.csv"


# ============================================================
# Helper functions
# ============================================================

def flatten_cij(cij):
    """
    Convert a 6x6 Cij matrix into:
        C11, C12, ..., C66
    """
    result = {}

    for i in range(6):
        for j in range(6):
            result[f"C{i+1}{j+1}"] = cij[i][j]

    return result


def get_tensor(elastic_doc, tensor_format="ieee"):
    """
    Extract either the IEEE-standardized or raw elastic tensor.
    """

    tensor = elastic_doc.elastic_tensor

    if tensor is None:
        return None

    if tensor_format == "ieee":
        return tensor.ieee_format

    elif tensor_format == "raw":
        return tensor.raw

    else:
        raise ValueError(
            "TENSOR_FORMAT must be either 'ieee' or 'raw'"
        )


# ============================================================
# Main
# ============================================================

def main():

    api_key = os.environ.get("MP_API_KEY")

    if not api_key:
        raise RuntimeError(
            "MP_API_KEY environment variable is not set.\n"
            'Example:\n'
            '    export MP_API_KEY="your_api_key_here"'
        )

    material_ids = list(dict.fromkeys(MATERIAL_IDS))

    print(f"Checking {len(material_ids)} Materials Project IDs...")

    with MPRester(api_key) as mpr:

        # ----------------------------------------------------
        # 1. Check whether each material actually exists
        # ----------------------------------------------------

#        summary_docs = mpr.materials.summary.search(
#            material_ids=material_ids,
#            fields=[
#                "material_id",
#                "formula_pretty",
#            ],
#        )
        
        summary_docs = mpr.summary.search(
            has_props=[
#                HasProps.dielectric,
                HasProps.elasticity,
            ],
            crystal_system=CrystalSystem.cubic, # constrain search to only cubic crystal symmetries
#            band_gap=(0.01, None),  #checks that the material has a bandgap
            energy_above_hull=(0.0, 0.10), # make sure that the materials are thermally stable
            exclude_elements=["U", "Th", "Np"], # exclude these elements to keep the FBI away from visiting your home (also it will cause a query error)
            is_metal=False, # checks that the materials is not a metal
            num_magnetic_sites=0, # exclude any magnetic materials
            theoretical=False, # limit to experimentally verified materials

            fields=[
                "material_id",
                "formula_pretty",
                "symmetry",
                "band_gap",
                "density",
                "e_total",
                "energy_above_hull",
                "is_stable",
            ],
        )

        existing_materials = {
            str(doc.material_id): doc
            for doc in summary_docs
        }

        # ----------------------------------------------------
        # 2. Query elasticity endpoint
        #
        # Important:
        # Materials with no elasticity calculation simply
        # will not appear in this returned list.
        # ----------------------------------------------------

        mpids = [str(doc.material_id) for doc in summary_docs]
        elastic_docs = mpr.materials.elasticity.search(
            material_ids=mpids,
            fields=[
                "material_id",
                "formula_pretty",
                "elastic_tensor",
                "last_updated",
                "warnings",
            ],
        )

        elastic_by_id = {
            str(doc.material_id): doc
            for doc in elastic_docs
        }

    # --------------------------------------------------------
    # 3. Check availability and extract tensors
    # --------------------------------------------------------

    availability_rows = []
    tensor_rows = []
    json_results = []

    for mpid in material_ids:

        # Material ID was not found at all
        if mpid not in existing_materials:

            print(f"{mpid:12s}  NOT FOUND")

            availability_rows.append(
                {
                    "material_id": mpid,
                    "formula": "",
                    "material_exists": False,
                    "elasticity_available": False,
                    "status": "material_not_found",
                }
            )

            continue

        summary_doc = existing_materials[mpid]
        formula = summary_doc.formula_pretty

        # Material exists but there is no elasticity document
        if mpid not in elastic_by_id:

            print(
                f"{mpid:12s}  {formula:15s}  "
                "NO ELASTICITY DATA"
            )

            availability_rows.append(
                {
                    "material_id": mpid,
                    "formula": formula,
                    "material_exists": True,
                    "elasticity_available": False,
                    "status": "no_elasticity_data",
                }
            )

            continue

        elastic_doc = elastic_by_id[mpid]

        cij = get_tensor(
            elastic_doc,
            tensor_format=TENSOR_FORMAT,
        )

        # Elasticity document exists, but tensor itself is missing
        if cij is None:

            print(
                f"{mpid:12s}  {formula:15s}  "
                "ELASTICITY RECORD EXISTS, Cij MISSING"
            )

            availability_rows.append(
                {
                    "material_id": mpid,
                    "formula": formula,
                    "material_exists": True,
                    "elasticity_available": False,
                    "status": "tensor_missing",
                }
            )

            continue

        # ----------------------------------------------------
        # Tensor successfully obtained
        # ----------------------------------------------------

        print(
            f"{mpid:12s}  {formula:15s}  "
            "Cij AVAILABLE"
        )

        availability_rows.append(
            {
                "material_id": mpid,
                "formula": formula,
                "material_exists": True,
                "elasticity_available": True,
                "status": "available",
            }
        )

        # Flatten tensor for CSV
        row = {
            "material_id": mpid,
            "formula": formula,
            "tensor_format": TENSOR_FORMAT,
        }

        row.update(flatten_cij(cij))

        tensor_rows.append(row)

        # Keep full 6x6 tensor in JSON
        json_results.append(
            {
                "material_id": mpid,
                "formula": formula,
                "tensor_format": TENSOR_FORMAT,
                "Cij_GPa": [
                    list(r) for r in cij
                ],
                "last_updated": (
                    elastic_doc.last_updated.isoformat()
                    if elastic_doc.last_updated
                    else None
                ),
                "warnings": elastic_doc.warnings,
            }
        )

    # ========================================================
    # 4. Write availability CSV
    # ========================================================

    with open(
        AVAILABILITY_FILE,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        fieldnames = [
            "material_id",
            "formula",
            "material_exists",
            "elasticity_available",
            "status",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(availability_rows)

    # ========================================================
    # 5. Write Cij CSV
    # ========================================================

    if tensor_rows:

        cij_columns = [
            f"C{i}{j}"
            for i in range(1, 7)
            for j in range(1, 7)
        ]

        fieldnames = [
            "material_id",
            "formula",
            "tensor_format",
        ] + cij_columns

        with open(
            CSV_FILE,
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(tensor_rows)

    # ========================================================
    # 6. Write full tensors to JSON
    # ========================================================

    with open(
        JSON_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            json_results,
            f,
            indent=2,
        )

    # ========================================================
    # Summary
    # ========================================================

    n_available = sum(
        row["elasticity_available"]
        for row in availability_rows
    )

    print()
    print("----------------------------------------")
    print(f"Requested materials : {len(material_ids)}")
    print(f"Cij available       : {n_available}")
    print(
        f"Cij unavailable     : "
        f"{len(material_ids) - n_available}"
    )
    print("----------------------------------------")

    print(f"Availability: {AVAILABILITY_FILE}")

    if tensor_rows:
        print(f"Cij CSV     : {CSV_FILE}")
        print(f"Cij JSON    : {JSON_FILE}")


if __name__ == "__main__":
    main()
