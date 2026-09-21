# Agentic material optimization plan

Status: implemented and covered by local replay tests on branch
`Agentic_Material_optimization`, which began from
`Material_optimization_v3_scan_parameters_revised` at commit `da7637f`. A live
Ollama comparison has not run because the required model and working GPU driver
are not available in the current shell.

## Decision

Use one small, auditable agent loop around the existing optimizer interface:

1. A local Ollama model reads the versioned parameter definitions, constraints,
   measured project findings, completed observations, uncertainty, and failures.
2. It proposes a diverse pool of complete candidate vectors, each with a short
   physical hypothesis and runtime-risk assessment.
3. Deterministic code rejects malformed, duplicate, out-of-range, and physically
   invalid vectors. Values are never silently clipped.
4. The existing noise-aware Gaussian process ranks the accepted pool by expected
   improvement, with the model's runtime-risk assessment recorded separately.
5. Only the selected vector is sent to the existing Geant4/G4CMP evaluator.
6. The measured objective, uncertainty, elapsed time, or failure is returned to
   the next agent turn.

The LLM is therefore a proposal and hypothesis engine, not a surrogate simulator,
constraint authority, or source of fabricated material constants. The agent may
recommend a point; only the existing validated simulation may score it.

```text
versioned physics + measured history + failures
                     |
                     v
             Ollama candidate pool
                     |
                     v
       fields -> ranges -> physics -> duplicate checks
                     |
                     v
             GP expected-improvement rank
                     |
                     v
       Geant4/G4CMP -> objective + SE + elapsed time
                     |
                     +---- recorded feedback ----+
```

This is deliberately not a multi-framework autonomous system. One client, one
prompt builder, one parser, and one optimizer adapter are enough for the task.

## Why the earlier methods need help

The comparison must start from what the repository actually measured, not from a
generic claim that LLMs optimize better.

- The revised BO attempt completed `0/120` adaptive points. Its first expected-
  improvement point selected the minimum substrate-decay face and near-minimum
  scattering; none of its 512 tasks finished in an hour. The recorded lower
  bounds are 8.53 hours for that candidate and 42.7 days if all 120 behaved that
  way (`material_scan/experiments/material-search-bo-attempt.json`). The GP ranks
  objective improvement but has no runtime/cost model, so one extreme proposal
  can block the serial ask/evaluate/tell loop.
- A separate Sobol point produced the same class of straggler, with a 25.6-hour
  candidate lower bound. This means runtime pathology is not unique to BO, but a
  cost-blind adaptive acquisition can repeatedly prefer that region.
- CMA-ES did produce useful screening improvements: three 120-point seeds lowered
  the common-start best by roughly 49--54%. However, its update ranks noisy point
  estimates without using their stored standard errors, it learns only after a
  complete synchronous population, and its best vectors repeatedly land on box
  faces. A slow member can hold a generation, and parameter semantics cannot
  suggest a justified jump to a different physical mechanism.
- The earlier GP and CMA-ES runs also exposed provenance errors that were later
  fixed. Agentic decisions add another non-deterministic component, so recording
  exact model identity, prompt, response, rejection reasons, and selected-source
  metadata is mandatory.

The agent can compensate for these weaknesses by reasoning over named physical
mechanisms and observed failures, proposing non-local alternatives, and revising
hypotheses after results. It cannot remove Monte Carlo noise, prove that a
pseudo-material is fabricable, or turn missing cryogenic constants into data.

## Model choice and knowledge policy

Production model: pin
`qwen3:235b-a22b-thinking-2507-q4_K_M` in Ollama rather than using a moving
alias. It is a 235B-total/22B-active open-weight model with tool-oriented
reasoning and a 142 GB Ollama artifact. The unquantized Qwen3-235B thinking
model family has published science and materials-reasoning results; the exact
Ollama Q4 artifact has not itself been validated on MatSciBench. Four 48 GB RTX
A6000 GPUs provide 192 GB gross memory, enough to attempt the quantized weights
plus a conservative context/cache, subject to the mandatory residency check.

Development model: `qwen3.8:27b`. It fits on one A6000 and is suitable for parser,
prompt, replay, and short acceptance tests. Development results from this model
must not be mixed with the production model's campaign.

Relevant primary/model sources:

- [Qwen3-235B Thinking model card](https://huggingface.co/Qwen/Qwen3-235B-A22B-Thinking-2507)
- [Ollama Qwen3 tags and artifact sizes](https://ollama.com/library/qwen3/tags)
- [MatSciBench materials-science evaluation](https://arxiv.org/abs/2510.12171)
- [ScienceEval benchmark repository](https://github.com/ScienceOne-AI/ScienceEval)
- [Ollama context guidance](https://github.com/ollama/ollama/blob/main/docs/context-length.mdx)

No model weights are treated as a live or authoritative materials database. The
first implementation grounds the model in a short, checked-in knowledge file,
the curated `stage4_material_pool.yaml` records, and the derived-with-warnings
`sic_phonon_constants.yaml` record. These come from this repository's parameter
catalog, scientific notes, and measured results, and their exact bytes enter the
optimizer hash. Unknown scattering, decay, lifetime, and interface values stay
explicitly unknown; the model must request bracketing rather than inventing them.

A later, separately reviewed extension may expose read-only structured lookups to
the [Materials Project API](https://docs.materialsproject.org/downloading-data/using-the-api/getting-started)
or [NOMAD](https://docs.nomad-lab.eu/). It is intentionally outside the first
implementation because neither database supplies all of the cryogenic G4CMP
parameters in this search, and an open-web RAG layer would add complexity without
making the simulation objective more trustworthy.

## Minimal code changes

- Add `material_scan/agentic.py`: standard-library Ollama transport, strict JSON
  parser, grounded prompt, guarded candidate pool, GP ranking, audit trace, and
  network-free transcript replay.
- Add `material_scan/agent_knowledge.md`: concise, provenance-linked project facts
  and explicit unknowns. Include its bytes in the optimizer implementation hash.
- Extend `material_scan/search.py`: register the `agentic` method and replay saved
  agent asks from the transcript without calling Ollama again.
- Extend `material_scan/optimization.py` and `material_scan/cli.py`: pass explicit
  model settings, capture evaluation elapsed time, and expose a preflight command.
- Generalize `material_scan/tools/search_report.py`: accept `agentic` alongside
  BO, CMA-ES, Sobol, and random, reject mismatched experiment identities, and plot
  all methods with identical completed-candidate and source-phonon counts.
- Add one Mimir launcher that exposes at most four selected GPUs and refuses to
  launch unless Ollama, the pinned model, and GPU execution are visible.
- Add focused tests using a canned Ollama client. Tests cover malformed output,
  hard checks, fallback labeling, model metadata, and resume without a network
  call.

## Safety, reproducibility, and failure policy

- Record the requested model tag and the Ollama content digest. A tag match with a
  digest change is a different campaign.
- Use temperature `0` for comparison runs and a recorded prompt seed. This is
  an auditability choice rather than a claim that zero temperature is generally
  best; diversity comes from competing mechanisms and a candidate pool.
- Save each exact system prompt, user prompt, reasoning/response fields, parse
  errors, accepted candidates, and final selection in a per-call JSON trace.
- Keep rationales outside parameter dictionaries so they cannot leak into the
  strict experiment resolver.
- The default comparison mode fails closed if Ollama or the pinned model is not
  available. An explicit `--allow-model-fallback` may use ordinary GP-EI for
  operational continuity, but such points are labelled `gp_fallback` and do not
  count as evidence for the agentic method.
- Persist every ask before starting its expensive simulation. Resume reconstructs
  prior optimizer state from the saved selected points and results and evaluates
  the same pending point; it never asks the LLM to recreate an old decision.
- A model response cannot change bounds, constraints, objective definitions,
  event counts, seeds, build identities, or confirmation rules.
- Simulation failures/timeouts are non-observations. They are recorded and shown
  to later turns, never converted to a zero or a favorable score.

## Four-GPU Mimir deployment

Mimir exposes eight RTX A6000 devices through PCI, but this current shell cannot
communicate with the NVIDIA driver and has no `ollama` executable. Therefore live
inference is blocked here and no GPU result will be claimed during implementation.

The production launcher will:

1. accept exactly one to four explicit GPU UUIDs (four recommended for the 142 GB
   production model);
2. set `CUDA_VISIBLE_DEVICES` to only those devices;
3. set `OLLAMA_CONTEXT_LENGTH=65536`, `OLLAMA_NUM_PARALLEL=1`, and
   `OLLAMA_MAX_LOADED_MODELS=1`, disable Ollama's Vulkan backend, and expose the
   chosen UUIDs through CUDA only;
4. start an isolated Ollama server, verify the pinned tag/full digest and server
   version, and require the API process record to report exact full-model GPU
   residency and the requested context after a structured-output warm-up;
5. fall back to a 32768-token context only if the 64K preflight is out of memory;
6. leave the Geant4 worker count independent of the LLM GPU allocation.

The prompt is intentionally compact, so the campaign does not depend on the
model's advertised maximum context.

## Fair experiment and visualization

Branch names are provenance, not a comparison axis. Results from different
experiment specifications, spatial designs, event totals, objectives, or build
hashes must not be placed on one performance curve.

The agent prompt intentionally includes lessons from the already observed BO
and Sobol stalls and the aggregate CMA-ES behavior. The resulting comparison is
therefore a retrospective workflow comparison, not a blinded contest of innate
optimizer sample efficiency. Equal starting observations and source-phonon counts
remain mandatory, and this information advantage must stay visible in reports.

The agentic campaign will use the exact same `material-search.yaml`, 32 completed
common starting points, 120 adaptive evaluations per seed, source-phonon count per
candidate, and seeds `101`, `202`, and `303` used for the retained CMA-ES runs.
BO, CMA-ES, Sobol, random, and agentic inputs must share the same
`experiment_spec_key` and `initial_points_key`.

The report will show:

- best-so-far objective versus completed adaptive candidates;
- best-so-far objective versus cumulative simulated source phonons;
- completed, failed, and fallback proposal counts;
- median and range over independent optimizer seeds;
- uncertainty of each selected best point and distance to search boundaries;
- wall time as an operational diagnostic, never as a substitute for equal
  simulated source-phonon count.

The primary question is whether the three agentic seeds improve the distribution
of best confirmed objective values at equal simulation cost. A lower screening
minimum alone is not enough. Leading candidates must be re-run with unused random
seeds and the converged 2,361-site design before claiming an improvement. If no
agentic run is complete, the plot and report must say `not run`; if intervals
overlap, the conclusion is `no resolved improvement`. A future blinded methods
benchmark would need a frozen common prior-information cutoff for every method.

## Implementation checks

1. Unit tests and mocked-agent replay pass in the `G4CMP` environment.
2. The preflight sees the exact production model digest and four or fewer GPUs.
3. A no-simulation proposal smoke test produces valid, nonduplicate points and a
   complete audit trace.
4. Run a short, separately labelled pilot before the three full seeds.
5. Generate the equal-source-count comparison, then confirm only genuinely competitive
   candidates with the full spatial design.
