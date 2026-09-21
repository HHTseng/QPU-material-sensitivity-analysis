# Agentic material optimization plan

Status: implemented and covered by local replay tests on branch
`Agentic_Material_optimization`, which began from
`Material_optimization_v3_scan_parameters_revised` at commit `da7637f`. The exact
production model passed the four-GPU preflight, and separately labelled one-point
deployment and equal-policy pilots completed end to end. The equal-policy pilot
validates execution, not optimizer effectiveness. The three 120-point comparison
runs and full-spatial confirmation remain pending; no agentic improvement is
resolved.

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
Ollama Q4 artifact has not itself been validated on MatSciBench. The 142 GB
artifact plus its 65,536-token context passed the mandatory exact-residency check
on four 48 GB RTX A6000 GPUs.

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
  best or deterministic; diversity comes from competing mechanisms and a
  candidate pool. The two live pilots used the same prompt hash, model digest,
  seed, implementation hash, and Ollama version yet generated different pools.
  Saved traces, not regenerated responses, are authoritative.
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

The isolated production deployment passed on Mimir with exactly four of the
server's eight RTX A6000 GPUs exposed. Ollama `0.34.1` loaded
`qwen3:235b-a22b-thinking-2507-q4_K_M` at digest
`754a872f1290d6a685e7be7997962d6518823c32036358794b650db505f6bf99`.
The structured warm-up reported a 65,536-token context and exact full residency:
`size_vram = size = 158172908091` bytes. The checked record is
[`agentic-preflight.json`](material_scan/experiments/agentic-preflight.json).

The production launcher now:

1. accepts exactly one to four explicit GPU UUIDs (four recommended for the 142 GB
   production model);
2. sets `CUDA_VISIBLE_DEVICES` to only those devices;
3. sets `OLLAMA_CONTEXT_LENGTH=65536`, `OLLAMA_NUM_PARALLEL=1`, and
   `OLLAMA_MAX_LOADED_MODELS=1`, disables Ollama's Vulkan backend, and exposes the
   chosen UUIDs through CUDA only;
4. starts an isolated Ollama server, verifies the pinned tag/full digest and server
   version, and requires the API process record to report exact full-model GPU
   residency and the requested context after a structured-output warm-up;
5. falls back to a 32768-token context only if the 64K preflight is out of memory;
6. leaves the Geant4 worker count independent of the LLM GPU allocation.

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
seeds and the converged 2,361-site design before claiming an improvement. A
pilot and a pending full run must be labelled separately. Improvement is resolved
only when the predeclared paired agentic-versus-comparator difference interval
excludes zero in the favorable direction. A future blinded methods benchmark
would need a frozen common prior-information cutoff for every method.

The equal-policy one-point pilot generated 12 valid candidates, selected one by
GP expected improvement without fallback, and completed all 512 simulation tasks
in 21.75 seconds. Its objective was `5.395803e-4 ± 8.31e-5`, 6.59% above the
common-start best point estimate of `5.062199e-4 ± 8.74e-5`; the reported
one-standard-error intervals overlap. This is no resolved improvement or
regression, and one point cannot rank methods. An earlier one-point deployment
run used a 3,600-second task timeout and is excluded from the equal-policy report.

## Implementation checks

1. Complete: unit tests and mocked-agent replay pass in the `G4CMP` environment.
2. Complete: the preflight saw the exact production digest on exactly four GPUs.
3. Complete: structured warm-up and live calls produced valid, nonduplicate pools
   and complete audit traces.
4. Complete: separately labelled deployment and equal-policy pilots ran before
   the three full seeds.
5. Pending: run all three 120-point seeds, generate the equal-source-count
   comparison, and confirm only genuinely competitive candidates with the full
   spatial design.
