# Grounded knowledge for the material-search agent

This file is prompt context, not an authority that may override the experiment
specification. Numeric bounds, fixed values, constraints, objective values, and
uncertainties are supplied from the live experiment and result records.

## Objective and evaluator

- Minimize the spatially weighted junction quasiparticle yield per injected eV.
  It is not a logical-error probability. Source: `docs/science.md`.
- A candidate uses a 10 meV source phonon, 128 recorded spatial sites, four
  replicas, and 16,000,000 source phonons. It is a screening calculation; a
  finalist needs unused seeds and the converged 2,361-site design. Sources:
  `docs/science.md`, `docs/results.md`.
- The simulator is the only source of objective values. Never estimate, invent,
  or repeat an objective value for an unevaluated vector.

## Physical mechanisms represented by the variables

- Substrate anharmonic downconversion scales approximately as
  `sub_decay * frequency^5`; mass-defect scattering scales approximately as
  `sub_scat * frequency^4`. Lower values can preserve energetic, long-travel
  phonons and can also produce extremely slow simulation points. Source:
  `sic_phonon_constants.yaml` and the G4CMP model summarized in
  `../legacy/stage4_llm.py`.
- `sub_c11`, `sub_c12`, and `sub_c44` control cubic elastic propagation and
  phonon focusing. Cubic Born stability and derived sound-speed checks are hard
  constraints, not suggestions. Source: `docs/science.md`.
- Film density and sound speed enter the effective acoustic-impedance interface
  model. Film gap/threshold and phonon lifetime affect whether energy is absorbed,
  survives, or is returned to the substrate. Treat this as the implemented model,
  not as a universal interface law. Sources: `parameters.yaml`,
  `../legacy/stage4_material_pool.yaml`.
- The primary search fixes temperature, lattice constant, and crystal direction.
  Do not propose changes to fixed coordinates. Source: `docs/science.md`.

## Measured project findings

- Cu beat Au in all nine matched linked-material comparisons. The bottom film was
  the strongest stable factor in that screen. Source: `docs/results.md`.
- Earlier equal-compute continuous searches concentrated low responses better
  than random/Sobol, but unconstrained winners sat on several bounds and used an
  unrealistically low upper-film gap. The revised search directly enforces the
  niobium-compatible lower gap. Source: `docs/results.md`.
- In the revised 14-parameter search, three CMA-ES seeds improved the common-start
  best by about 49--54%, but their best estimates remain noisy and touch multiple
  box faces. This is evidence for promising regions, not fabrication readiness.
  Source: `experiments/material-search-results.json`.
- The first revised GP-EI candidate chose the minimum substrate-decay face and
  near-minimum scattering. None of 512 tasks completed in one hour; the recorded
  lower bound is 8.53 hours for the candidate. Avoid repeatedly proposing this
  known slow corner unless the expected scientific value clearly justifies a
  staged runtime probe. Source: `experiments/material-search-bo-attempt.json`.
- One broad Sobol point also stalled, so runtime risk is not a license to avoid all
  exploration. Propose competing mechanisms and include at least one conservative
  interpolation or near-incumbent candidate in each pool. Source:
  `experiments/material-search-sobol-attempt.json`.

## Material-data discipline

- Continuous vectors are pseudo-material targets. A vector is not a real compound
  merely because it is numerically valid. Source: `docs/science.md`.
- The projection pool marks values as verified, unverified, or null. Null means
  unknown and must never be imputed from model memory. Film sound-speed conventions
  are inconsistent in the historical catalog and must remain explicit. Source:
  `../legacy/stage4_material_pool.yaml`.
- SiC scattering, total decay, and transverse-transverse branching values in
  `sic_phonon_constants.yaml` are derived rather than measured. Its decay estimate
  lies well outside the earlier tested bracket. Treat it as a hypothesis needing a
  downward re-bracket, not as settled material data.

## Proposal rules

- Give every searched variable exactly once and no fixed or undeclared variable.
- Prefer a pool spanning distinct physical hypotheses over small numerical
  variants of one hypothesis.
- Use the supplied observations and standard errors. Differences below the noise
  scale are not resolved evidence.
- Report runtime risk as `low`, `medium`, or `high` and say which coordinates drive
  it. Runtime risk is advisory; deterministic code records it but does not change
  the physical search bounds.
- Keep each rationale short, causal, and tied to the implemented model or a
  measured project result. State uncertainty instead of filling a knowledge gap.
