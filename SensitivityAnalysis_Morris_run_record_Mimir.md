# SensitivityAnalysis_Morris Mimir Run Record

Last updated: 2026-07-20

This note records the current `mimir` setup for the unified Morris runner in:

```text
/home/htseng/Sensitivity_Analysis_HT
```

As of 2026-07-20 mimir runs the **same script file** as the Mac,
`stage1_run_simulations.py`, synced from
`/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac`. The file is
byte-identical on both machines; it selects its install layout from
`sys.platform` at import time. There is deliberately no `..._Mimir.py` fork
of the current pipeline, for the same "single source of truth" reason the
Mac record gives for merging the three predecessor scripts.

The older `SensitivityAnalysis_Morris.py` and its debug variants remain in
the directory as the previous generation. They are not part of this pipeline
and are not kept in sync.

## GitHub

The mimir directory is the git working tree for:

```text
https://github.com/HHTseng/QPU-material-sensitivity-analysis
```

(private repo, branch `main`, pushed via the `gh` HTTPS token already
configured on mimir — SSH to github.com is blocked from this host).

`output/` and `results/` are excluded by `.gitignore`, along with
`__pycache__/`, `.vscode/`, and `backup_pre_mac_sync_*/`. They total ~290 MB
and every run mints a fresh UUID directory, so they are regenerated rather
than tracked; a fixed `SENSITIVITY_MORRIS_SEED` reproduces the design exactly.

Push further changes from mimir with:

```bash
cd /home/htseng/Sensitivity_Analysis_HT
git add -A && git commit -m "..." && git push
```

The Mac directory is **not** a git working tree. It also holds
`old_version/` and `Sensitivity_Analysis_Paul/`, which exist only on the Mac
and are therefore not in the repo.

## Server And Python

- host: `mimir.sdcc.bnl.gov` (via `ssh mimir`, or
  `ssh -At htseng@ssh.sdcc.bnl.gov ssh -t htseng@mimir.sdcc.bnl.gov`)
- OS: RHEL 9, kernel `5.14.0-570.49.1.el9_6.x86_64`, `x86_64`
- cores: `192`
- memory: `503 GB`
- conda environment: `/home/htseng/.conda/envs/G4CMP`
- Python: `/home/htseng/.conda/envs/G4CMP/bin/python` (3.12.13)
- numpy `2.4.6`, scipy `1.17.1`, pandas `3.0.3`, SALib present

## Path Correspondence: Mac vs Mimir

The script derives every path below from the platform block; all of them
remain overridable by the matching `SENSITIVITY_*` environment variable.

| Role | Mac (M4 Max) | Mimir |
|---|---|---|
| project directory | `/Users/huan-hsintseng/Downloads/Sensitivity_Analysis_HT_Mac` | `/home/htseng/Sensitivity_Analysis_HT` |
| conda env | `/opt/anaconda3/envs/G4CMP` | `/home/htseng/.conda/envs/G4CMP` |
| Geant4 root | `/Users/huan-hsintseng/Geant4` | `/home/software/Geant4` (shared site install) |
| Geant4 env script | `$GEANT4_ROOT/Geant4-Install/share/Geant4-10.7.4/geant4make/geant4make.sh` | same relative path under `/home/software/Geant4` |
| G4CMP root | `/Users/huan-hsintseng/Geant4/G4CMP` | `/home/htseng/src/G4CMP_htseng` |
| G4CMP env script | `$G4CMP_ROOT/g4cmp_env.sh` | `$G4CMP_ROOT/g4cmp_env.sh` |
| stock lattice config | `$G4CMP_ROOT/CrystalMaps/Si/config.txt` | `$G4CMP_ROOT/CrystalMaps/Si/config.txt` |
| Geant4 work directory | `/Users/huan-hsintseng/geant4_workdir` | `/home/htseng/geant4_workdir` |
| G4SYSTEM | `Darwin-clang` | `Linux-g++` |
| `Main` executable | `$G4WORKDIR/bin/Darwin-clang/Main` | `$G4WORKDIR/bin/Linux-g++/Main` |
| library path variable | `DYLD_LIBRARY_PATH` (+ `LD_LIBRARY_PATH`) | `LD_LIBRARY_PATH` only |
| production run id prefix | `morris_mac_` | `morris_mimir_` |
| default workers | `2` | `4` |

The two roots that are *not* simple home-directory renames are the ones to
remember: Geant4 is a shared install under `/home/software`, and G4CMP is a
personal checkout under `~/src/G4CMP_htseng` rather than beside Geant4.

## Files Synced From The Mac (2026-07-20)

Verified byte-identical by md5 on both machines:

- `stage1_run_simulations.py` (stage 1)
- `stage2_compute_QPs.py` (stage 2)
- `sensitivity_params.py`
- `sensitivity_utils.py`
- `SensitivityAnalysis_Morris_run_record_Mac_M4Max.md`
- `G4CMP_crash_and_memory_analysis.md` (reference for the hang/segfault modes)
- `sensitivity_template_beamOn10000.mac`, `sensitivity_template_beamOn1e6.mac`

The two templates mimir previously had were identical to the Mac copies
except for `/g4cmp/minEPhonons 0.0000382 eV` (one order of magnitude below
the Mac's `0.000382 eV`). That line is overwritten by `PINNED_G4CMP_COMMANDS`
in every generated macro regardless, so the difference never reached a run,
but the templates are now identical too. The originals are preserved in
`/home/htseng/Sensitivity_Analysis_HT/backup_pre_mac_sync_20260720/`.

## New Environment Variables

Two knobs were added while porting; both default to the previous behavior,
so Mac results are unchanged when they are unset.

- `SENSITIVITY_MORRIS_SEED` — seeds the SALib Morris sampler. Unset (default)
  keeps the historical non-reproducible design. Setting the same integer on
  both machines makes them generate identical designs, which is what makes a
  cross-machine comparison meaningful at all.
- `SENSITIVITY_SAMPLE_TIMEOUT` — per-sample wall-clock limit in seconds; `0`
  (default) disables it. This is recommendation 2 of
  `G4CMP_crash_and_memory_analysis.md`: a sample that draws `QPLim=1` on a
  pair-breaking film spins forever in `G4CMPKaplanQP::AbsorbPhonon` and can
  grow to tens of GB. The kill targets the whole process group, because
  killing the bash wrapper alone leaves an orphaned `Main` burning a core —
  verified on mimir (see below).

All the `SENSITIVITY_*` variables listed in the Mac record still apply, plus
`SENSITIVITY_G4CMP_ROOT`, which is new and points at the G4CMP checkout.

## Verification Run, 2026-07-20

Design: `SENSITIVITY_MORRIS_SEED=12345`, first 54 samples (one full Morris
trajectory), `sensitivity_template_beamOn10000.mac` (10^4 phonons per sample,
per the memory-safety guidance in the crash analysis), 4 workers.

Launch command used:

```bash
cd /home/htseng/Sensitivity_Analysis_HT
SENSITIVITY_MORRIS_SEED=12345 SENSITIVITY_MAX_SAMPLES=54 \
SENSITIVITY_SAMPLE_TIMEOUT=600 SENSITIVITY_MAX_WORKERS=4 \
SENSITIVITY_MACRO_TEMPLATE=/home/htseng/Sensitivity_Analysis_HT/sensitivity_template_beamOn10000.mac \
/home/htseng/.conda/envs/G4CMP/bin/python -u stage1_run_simulations.py
```

Run kept at:
`results/morris_mimir_f10e6126-2d46-4022-beb5-5052a328482d`

This run uses the `QPLim` lower bound of `2` adopted on 2026-07-20 (see
"QPLim lower bound" below); it supersedes the earlier
`morris_mimir_22ffe694…` run, which was generated with the old `[1, 5]`
bounds and has been removed to avoid mixing two designs.

Results:

| Check | Mac | Mimir | Verdict |
|---|---|---|---|
| parameter groups / expanded variables | 50 / 53 | 50 / 53 | match |
| `MorrisSequence.csv` md5 | `5d0d2a65…` | `5d0d2a65…` | **identical** |
| generated `Morris_27/Si/config.txt` md5 | `b4d0bf15…` | `b4d0bf15…` | **identical** |
| generated `Morris_27.mac` | — | — | identical except the `HitsFile` line, which is per-machine by design |
| `QPLim` values written across all 54 macros | {2,3,4,5} | {2,3,4,5} | no `1` present |
| 54 samples wall time, 4 workers | 18 s | 18 s | match |
| failed samples | 0 | 0 | match |
| fatal/abort strings in logs | none | none | clean |

### Stage 2 determinism

Geant4 seeds CLHEP from `clock()` (see the crash analysis), so hits files are
**not** reproducible across machines or even across reruns — mimir produced 10
hit rows across 5 samples where the Mac produced 13 across 7. At 10^4 events
that is expected sampling noise, not a porting defect: only about half the
design is above the pinned `minEPhonons` threshold at all, and the crash
analysis measures the island interaction rate at 10^-5–10^-4 per event.

To verify the numerics rather than the RNG, the Mac run's hits files and
manifest were copied to mimir and stage 2 was re-run there over that
identical input:

```text
mimir qp_summary.csv over the Mac's hits:  adfcf0199c8d52e6e9771b12ed402f14
Mac   qp_summary.csv over the same hits:   adfcf0199c8d52e6e9771b12ed402f14
```

Byte-identical, despite numpy 2.3.2 → 2.4.6 and pandas 2.3.1 → 3.0.3 between
the machines. Stage 2 is confirmed portable.

### Timeout enforcement

Deliberately forced with `SENSITIVITY_SAMPLE_TIMEOUT=2` against the 10^6
template:

```text
[1/2] FAILED Morris_0: Morris_0 exceeded SENSITIVITY_SAMPLE_TIMEOUT=2s and was killed
[2/2] FAILED Morris_1: Morris_1 exceeded SENSITIVITY_SAMPLE_TIMEOUT=2s and was killed
WARNING: 2/2 samples failed:
```

`pgrep -af 'geant4_workdir/bin/Linux-g\+\+/Main'` afterwards returned
nothing — no orphaned `Main`, which was the specific failure the crash
analysis reported after manual kills. The batch continued past both failures
and exited non-zero, as intended.

## QPLim Lower Bound Raised To 2 (2026-07-20)

Recommendation 1 of `G4CMP_crash_and_memory_analysis.md` has been applied:
`setTopQPLim`, `setTopFilmQPLim`, and `setBotQPLim` in `sensitivity_params.py`
now sweep `[2, 5]` instead of `[1, 5]`. With 4 Morris levels the sampled
values are exactly `{2, 3, 4, 5}`; a full 6912-sample design was checked and
contains **zero** `QPLim = 1` rows in any of the three parameters, so the
`G4CMPKaplanQP::AbsorbPhonon` infinite loop can no longer be reached.

Consequences to be aware of:

- **The Morris design changed.** Any results produced before this date used
  different trajectories and are not directly comparable. The pre-change
  mimir run `morris_mimir_22ffe694…` was deleted rather than kept alongside.
- Only `setTopQPLim` was ever actually at risk in this template (the Nb top
  film's `2Δ` exceeds every sampled gun energy, and the Cu bottom layer has
  `BotGap = 0`, so neither can break pairs). The other two were raised anyway
  for consistency, as the crash analysis recommends.
- `SENSITIVITY_SAMPLE_TIMEOUT` is now a backstop rather than the primary
  mitigation, but it is still worth setting for long production runs: it also
  bounds any *other* pathology that stalls a sample.

## Still Open

- Finding 3 of the crash analysis still applies and is **not** addressed by
  the QPLim change: roughly half the design has gun energy below the pinned
  `/g4cmp/minEPhonons 0.000382 eV` and therefore simulates nothing. In the
  verification run's first 54 samples (seed 12345), 0–23 are dead and 24–53
  are live — 30/54. Fixing it means raising the gun-energy sweep floor above
  `0.000382 eV`, sweeping `minEPhonons` with it, or accepting and documenting
  that half the design measures "nothing happens".

## Monitoring Commands

Run long jobs under tmux so they survive the SSH session:

```bash
tmux new -s morris_mimir
```

Check active processes:

```bash
pgrep -af 'stage1_run_simulations.py|geant4_workdir/bin/Linux-g\+\+/Main'
```

Watch for the runaway-memory signature (a healthy sample stays near 70 MB;
GB-scale RSS means the Kaplan hang):

```bash
ps -o pid,rss,etime,cmd -C Main
```

Count logs and hit files for a run:

```bash
find results/<run_id>/logs -maxdepth 1 -type f | wc -l
find results/<run_id>/hits -maxdepth 1 -type f | wc -l
```

Scan logs for fatal or event-abort problems:

```bash
grep -rnE 'Fatal|Aborting|Unable to open|ProcMan201|Event Must Be Aborted' results/<run_id>/logs
```

Inspect a generated macro before a long run:

```bash
grep -nE 'setEnergy|setSubstrateName|setSubstrateG4Name|beamOn|HitsFile' \
  output/<run_id>/macros/Morris_0.mac
```
