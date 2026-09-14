# Morris sensitivity analysis

This is the earlier analysis of 50 parameter groups, expanded to 53 numerical
variables. With 128 Morris trajectories and four levels, the complete design
contains `128 × (53 + 1) = 6,912` samples.

## Calculation

1. `stage1_run_simulations.py` creates the Morris design, writes one Geant4
   macro and lattice configuration per sample, and runs G4CMP.
2. `stage2_compute_QPs.py` converts accepted surface energy into
   quasiparticles, assigns each hit to the nearest electrode, and integrates
   the quasiparticle-density equation.
3. `stage2_compute_QPs_sensitivity_analysis.py` relates each input to the
   output. The usable default is the integrated-response Pearson analysis.

The alternative chi-squared comparison is incomplete: it needs one measured
time curve for each of the 17 simulated electrodes, while the available six
curves came from another device. The program refuses that mismatch.

`sensitivity_params.py` is the single parameter table. `sensitivity_utils.py`
contains macro-editing and output helpers. The macro filename states the exact
`/run/beamOn` value.

## Running

Generate two samples without starting Geant4:

```bash
SENSITIVITY_GENERATE_ONLY=1 SENSITIVITY_MAX_SAMPLES=2 \
  conda run -n G4CMP python -u stage1_run_simulations.py
```

Run a small study and then calculate quasiparticles:

```bash
SENSITIVITY_MAX_SAMPLES=54 \
SENSITIVITY_SAMPLE_TIMEOUT=600 \
SENSITIVITY_MACRO_TEMPLATE="$PWD/sensitivity_template_beamOn10000.mac" \
  conda run -n G4CMP python -u stage1_run_simulations.py

conda run -n G4CMP python stage2_compute_QPs.py results/<run_id>
conda run -n G4CMP python stage2_compute_QPs_sensitivity_analysis.py \
  --results-dir results/<run_id>
```

Important environment variables are:

| Name | Meaning |
|---|---|
| `SENSITIVITY_DEBUG_MODE` | serial output when `1`; parallel execution when `0` |
| `SENSITIVITY_MAX_WORKERS` | maximum simultaneous Geant4 processes |
| `SENSITIVITY_MAX_SAMPLES` | number of Morris samples to run |
| `SENSITIVITY_GENERATE_ONLY` | write inputs without launching Geant4 |
| `SENSITIVITY_MACRO_TEMPLATE` | macro template and exact primary-event count |
| `SENSITIVITY_MORRIS_SEED` | seed for reproducing the Morris design |
| `SENSITIVITY_SAMPLE_TIMEOUT` | optional wall-time limit per sample |

## Numerical cautions

- G4CMP can loop indefinitely when a film quasiparticle limit is one. Current
  scans restrict these limits to two through five and retain a process guard.
- A completed hit file can survive an exit-time segmentation fault caused by a
  stale Geant4 touchable object. Completion is decided from the expected output
  witness and a valid hit file, not from the exit code alone.
- `/g4cmp/minEPhonons` must remain below the physical pair-breaking threshold.
  Earlier low-energy Morris settings produced many physical zeros because the
  tracking cutoff exceeded the injected energy.
- Geant4 seeds its random engine internally from clock time in this executable.
  The Morris design and generated inputs reproduce from their recorded seed;
  individual hit files do not reproduce bit for bit.
- Pearson correlation can miss a strong non-monotonic effect. Treat it as
  screening evidence and compare it with Morris elementary effects.

Generated `output/` and `results/` directories can be very large and are not
source files. Preserve any result cited in a report with its inputs, software
revision, exact event count, and file checksums.
