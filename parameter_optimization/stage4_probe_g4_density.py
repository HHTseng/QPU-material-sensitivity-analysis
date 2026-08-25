#!/usr/bin/env python3
"""Measure the density Geant4 actually builds for a NIST material name.

    python stage4_probe_g4_density.py G4_Ge G4_GALLIUM_ARSENIDE G4_ALUMINUM_OXIDE

Exists because `SUBSTRATE_CARRIERS` in stage4_space.py must contain measured
densities, not tabulated ones. `G4LatticeManager::LoadLattice` calls
`SetDensity(Mat->GetDensity())`, so the Geant4 material IS the density inside
the phonon kinematics; a table value that disagrees with what Geant4 builds
produces a candidate whose derived sound speeds describe a different material
than the one simulated. The Stage 3 record hit exactly that (Ge tensor with Si
density: 7966 vs 5324 m/s, a 50% error).

The probe runs a one-event simulation and reads the density back out of the
Geant4 log, which is the only source that cannot disagree with the run.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stage1_run_simulations as harness                       # noqa: E402
from sensitivity_utils import replace_line                     # noqa: E402


def probe(g4_name, lattice="Si", events=1, timeout=600):
    workdir = tempfile.mkdtemp(prefix=f"probe_{g4_name}_")
    try:
        template = os.environ.get(
            "SENSITIVITY_MACRO_TEMPLATE",
            os.path.join(REPO_ROOT, "sensitivity_template_beamOn1e6.mac"))
        with open(template) as handle:
            lines = handle.readlines()
        hits = os.path.join(workdir, "probe_hits.txt")
        lines = replace_line(lines, "/main/detector_param/setSubstrateG4Name ",
                             f"/main/detector_param/setSubstrateG4Name {g4_name}",
                             allow_commented=True, required=True)
        lines = replace_line(lines, "/main/detector_param/setSubstrateName ",
                             f"/main/detector_param/setSubstrateName {lattice}",
                             allow_commented=True, required=True)
        lines = replace_line(lines, "/g4cmp/HitsFile", "/g4cmp/HitsFile " + hits,
                             required=True)
        lines = replace_line(lines, "/run/beamOn ", f"/run/beamOn {events}", required=True)
        macro = os.path.join(workdir, "probe.mac")
        with open(macro, "w") as handle:
            handle.writelines(lines)

        lattice_root = os.path.join(workdir, "CrystalMaps")
        src = os.path.join(harness.G4CMP_SOURCE_CRYSTALMAPS, lattice)
        os.makedirs(lattice_root, exist_ok=True)
        shutil.copytree(src, os.path.join(lattice_root, lattice))

        import shlex
        command = harness.build_run_command(macro, lattice_name=None)
        command = command.replace(
            "export G4LATTICEDATA=" + shlex.quote(
                os.path.join(harness.CRYSTALMAPS_DIR, "probe")),
            "export G4LATTICEDATA=" + shlex.quote(lattice_root))
        proc = subprocess.run(["bash", "-lc", command], capture_output=True,
                              text=True, timeout=timeout)
        text = proc.stdout + proc.stderr
        for line in text.splitlines():
            if "Material:" in line and "density:" in line:
                parts = line.split()
                name = parts[parts.index("Material:") + 1]
                density = float(parts[parts.index("density:") + 1])
                unit = parts[parts.index("density:") + 2]
                factor = {"g/cm3": 1000.0, "mg/cm3": 1.0, "kg/m3": 1.0}.get(unit, 1000.0)
                return {"requested": g4_name, "built": name,
                        "density_kg_m3": density * factor, "unit": unit,
                        "matches": name == g4_name, "returncode": proc.returncode}
        return {"requested": g4_name, "error": "no material/density line in the log",
                "returncode": proc.returncode, "log_tail": text[-800:]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("materials", nargs="+")
    ap.add_argument("--lattice", default="Si",
                    help="lattice record to pair with (any; only the density is read)")
    args = ap.parse_args()
    print(f"{'requested':28s} {'built':28s} {'density [kg/m3]':>16s}  ok")
    ok_all = True
    for name in args.materials:
        r = probe(name, args.lattice)
        if "error" in r:
            print(f"{name:28s} {'--':28s} {'--':>16s}  FAILED: {r['error']}")
            ok_all = False
            continue
        print(f"{r['requested']:28s} {r['built']:28s} {r['density_kg_m3']:16.1f}  "
              f"{'yes' if r['matches'] else 'NAME MISMATCH'}")
        ok_all &= r["matches"]
    print("\nAdd a verified entry to SUBSTRATE_CARRIERS in stage4_space.py to make a "
          "material usable as a density carrier.")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
