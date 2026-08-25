"""Agentic candidate proposal through Ollama open-weight models.

What this is, stated before the code because the distinction decides whether the
result is trustworthy: the model is a **proposal generator inside a validated
loop**, never an authority. Every proposal passes the same hard gates, is
simulated by the same evaluator, and is scored by the same objective as a
proposal from Sobol or from the GP. The model never reports a number it did not
get from a simulation, and nothing it says enters the ledger except its
proposals and its stated rationale.

Two registered strategies:

* `llm_agent` -- the model sees the campaign context and the trial history and
  proposes the next batch directly. The unassisted version, useful as a
  measurement of what an LLM proposer is worth on its own.
* `llm_bo` -- the model proposes an over-complete pool and the GP surrogate
  ranks it by Expected Improvement, dispatching only the best few. This is the
  recommended agentic mode: the model supplies physics-informed diversity, the
  GP supplies calibration, and a hallucinated proposal costs one acquisition
  evaluation rather than one Geant4 campaign slot.

Transport is Ollama's HTTP API, so no new Python dependency (`requests` is
already present) and no library-version risk in the G4CMP environment.

Failure policy, in order:
    malformed JSON        -> repair message, retry (bounded)
    out-of-bounds value   -> clipped, flagged, counted
    gate-infeasible       -> rejected with the gate name fed back
    duplicate of history  -> dropped
    still short / no host -> the fallback optimizer supplies the remainder,
                             and the campaign report says so
Nothing here can inject an invalid candidate into the campaign.
"""

import json
import os
import re
import time

import numpy as np

from stage4_space import DEFAULT_SPACE, precheck_cheap, precheck
from stage4_optimizers import (BaseOptimizer, GPBayesOpt, SobolSearch, register,
                               expected_improvement)

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("STAGE4_LLM_MODEL", "qwen3:32b")

# Quoted, not paraphrased, from the project's own measured record. An LLM asked
# to reason about phonon transport without these will invent plausible-sounding
# but wrong mechanisms; with them it at least argues from this model's physics.
PHYSICS_NOTES = """\
- A 10 meV athermal phonon is injected just below the top surface of a 525 um
  cubic substrate; QPs are counted only where phonons are absorbed in the 17
  aluminium junctions (gap 191 ueV, so each recorded hit yields
  round(E_dep/191 ueV) quasiparticles).
- The phonon downconverts on its way: anharmonic decay rate ~ decay*omega^5,
  isotope/mass-defect scattering ~ scat*omega^4. Faster downconversion means
  more, softer phonons that travel shorter distances; below 2*191 ueV a phonon
  can no longer break a pair at a junction at all.
- The substrate elastic tensor and the crystal orientation steer phonon
  focusing (caustics). Measured: injection site alone moves the objective by
  9.18x with the material held fixed, so focusing is a first-order lever.
- Each boundary has an absorption probability derived from the acoustic
  impedance mismatch Z = rho*v between substrate and film. Energy absorbed by
  the ground plane or the bottom film does NOT reach the junctions. In the
  converged v2 material study the bottom film dominated: Cu beat Au everywhere,
  and Au's longer phonon lifetime (16 vs 5.1 ns) plus its lower interface
  absorption both kept more energy in the substrate.
- A film's phonon lifetime sets how long a pair-breaking phonon survives inside
  that film before it is re-emitted into the substrate; a larger film gap means
  fewer phonons can break pairs there, so the film competes less for energy.
- Substrate: Si 165.6/63.9/79.5 GPa at 2330 kg/m3 gives v_L 9017, v_T 5370 m/s
  and 3.900e-4 QPs per primary. Ge (126/44/67 GPa at 5323 kg/m3) gives
  2.494e-4, i.e. -36%: slower, denser, and better."""

SCHEMA_HINT = """\
{"candidates": [{"values": {"<variable>": <number>, ..., "miller": [h,k,l]},
                 "rationale": "<one sentence, <=200 chars>"}]}"""


class OllamaClient:
    """Minimal Ollama chat client. Requests only; no SDK, no version risk."""

    def __init__(self, host=None, model=None, timeout=180.0, temperature=0.7,
                 num_ctx=8192):
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = float(timeout)
        self.temperature = float(temperature)
        self.num_ctx = int(num_ctx)

    def available(self):
        """(reachable, detail). Never raises -- an unreachable host is a
        documented campaign condition, not a crash."""
        try:
            import requests
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            if r.status_code != 200:
                return False, f"{self.host} returned HTTP {r.status_code}"
            models = [m.get("name", "") for m in (r.json().get("models") or [])]
            if self.model not in models:
                return False, (f"model {self.model!r} not pulled on {self.host}; "
                               f"available: {models[:8]}")
            return True, f"{self.model} on {self.host}"
        except Exception as exc:                       # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    def chat(self, system, user, seed=0):
        import requests
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature, "seed": int(seed),
                        "num_ctx": self.num_ctx},
        }
        r = requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return (r.json().get("message") or {}).get("content", "")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def variable_block(space):
    rows = []
    for v in space.variables:
        rows.append(f"- {v.name} [{v.unit or 'dimensionless'}]: bounds "
                    f"[{v.low:g}, {v.high:g}], {v.scale} scale, baseline "
                    f"{v.baseline:g}. {v.doc.splitlines()[0]}")
    rows.append(f"- miller: one of {[list(m) for m in space.millers]} "
                f"(integer crystal direction; baseline [0,0,1])")
    return "\n".join(rows)


CONSTRAINTS = """\
- Born stability of the cubic tensor: sub_c11 - sub_c12 > 0, sub_c11 + 2*sub_c12 > 0,
  sub_c44 > 0. Roughly 29% of uniform draws fail this; check it yourself.
- The derived speeds must satisfy 0 < v_T < v_L. They are computed from the
  tensor and the fixed substrate density (2330 kg/m3), never proposed.
- bot_gap_thres must not exceed 2*191 ueV = 3.82e-4 eV.
- 2*topfilm_gap must stay below the 10 meV injection energy, so the ground
  plane remains an active absorber for every candidate in this campaign.
- Every value must lie inside its stated bounds. Values outside are clipped and
  the proposal is counted as an error."""


def build_prompt(space, history, incumbent, n, objective_name, n_best=8, n_recent=6,
                 repair=None):
    lines = [f"## Variables ({space.n_cont} continuous + orientation)",
             variable_block(space),
             "\n## Hard constraints", CONSTRAINTS,
             "\n## Physics of this simulation", PHYSICS_NOTES,
             f"\n## Objective\nMinimize `{objective_name}`. Lower is better. "
             f"Observations carry roughly 5% stochastic error, so a 3% "
             f"difference between two trials is not yet evidence."]

    if history:
        ordered = sorted(history, key=lambda h: h["value"])
        lines.append(f"\n## Best {min(n_best, len(ordered))} trials so far")
        for h in ordered[:n_best]:
            lines.append(f"- value={h['value']:.4e}  {_compact(h['point'])}")
        recent = history[-n_recent:]
        if recent:
            lines.append(f"\n## Most recent {len(recent)} trials")
            for h in recent:
                lines.append(f"- value={h['value']:.4e}  {_compact(h['point'])}")
    else:
        lines.append("\n## History\n(empty -- this is the first batch; spread out)")

    if incumbent:
        lines.append(f"\n## Incumbent\nvalue={incumbent['value']:.4e}  "
                     f"{_compact(incumbent['point'])}")

    if repair:
        lines.append("\n## Your previous answer was rejected\n" + repair
                     + "\nFix these and answer again.")

    lines.append(f"""
## Task
Propose {n} NEW candidate vectors, diverse from each other and from the history,
each with a one-sentence physical rationale grounded in the notes above.
Give EVERY variable explicitly. Answer with JSON only, exactly this shape:
{SCHEMA_HINT}""")
    return "\n".join(lines)


def _compact(point):
    parts = []
    for k, v in point.items():
        if k == "miller":
            parts.append(f"miller={list(v)}")
        else:
            parts.append(f"{k}={float(v):.4g}")
    return " ".join(parts)


SYSTEM_PROMPT = """\
You propose candidate parameter vectors for a Geant4/G4CMP simulation that counts
quasiparticles generated at 17 aluminium junctions by an injected 10 meV phonon
burst in a 525 um substrate. Lower is better.

Rules:
1. Answer with JSON only, matching the schema you are given. No prose outside JSON.
2. Every variable must be inside its stated bounds, and the hard constraints must
   hold for every candidate you propose.
3. Never invent measured values, never claim a result you were not given, and
   never report an objective value for a candidate that has not been simulated.
4. Prefer proposals a phonon-transport argument supports over interpolation of
   the history alone."""


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------
def parse_candidates(raw, space):
    """(candidates, errors, stats). Tolerant of framing, strict about content."""
    stats = {"proposed": 0, "clipped": 0, "infeasible": 0, "malformed": 0}
    errors = []
    data = _loads(raw)
    if data is None:
        return [], ["response was not valid JSON"], stats
    items = data.get("candidates") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return [], ["JSON did not contain a `candidates` list"], stats

    out = []
    for i, item in enumerate(items):
        stats["proposed"] += 1
        if not isinstance(item, dict):
            stats["malformed"] += 1
            errors.append(f"candidate {i}: not an object")
            continue
        values = item.get("values", item)
        if not isinstance(values, dict):
            stats["malformed"] += 1
            errors.append(f"candidate {i}: `values` is not an object")
            continue
        point, missing, clipped = {}, [], []
        for v in space.variables:
            if v.name not in values:
                missing.append(v.name)
                continue
            try:
                x = float(values[v.name])
            except (TypeError, ValueError):
                missing.append(v.name)
                continue
            if not (v.low <= x <= v.high):
                clipped.append(f"{v.name}={x:g} -> [{v.low:g}, {v.high:g}]")
                x = min(v.high, max(v.low, x))
            point[v.name] = x
        if missing:
            stats["malformed"] += 1
            errors.append(f"candidate {i}: missing or non-numeric {missing}")
            continue
        miller = values.get("miller", [0, 0, 1])
        try:
            miller = [int(x) for x in miller]
        except (TypeError, ValueError):
            miller = [0, 0, 1]
            clipped.append("miller -> [0,0,1]")
        if miller not in space.millers:
            allowed = space.millers
            miller = min(allowed, key=lambda m: sum((a - b) ** 2
                                                    for a, b in zip(m, miller)))
            clipped.append(f"miller -> {miller}")
        point["miller"] = miller
        if clipped:
            stats["clipped"] += 1
            errors.append(f"candidate {i}: out of bounds, clipped: {clipped}")
        ok, reason = precheck_cheap(space.complete(point), space)
        if not ok:
            stats["infeasible"] += 1
            errors.append(f"candidate {i}: rejected by a hard gate: {reason}")
            continue
        point["_rationale"] = str(item.get("rationale", ""))[:200]
        out.append(point)
    return out, errors, stats


def _loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", str(raw), flags=re.S)     # fenced or chatty output
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# Optimizers
# ---------------------------------------------------------------------------
class _LLMMixin:
    def _setup_llm(self, client=None, log_dir=None, retries=3, objective_name="objective",
                   fallback="sobol", pool_factor=4, **kw):
        self.client = client or OllamaClient(**{k: v for k, v in kw.items()
                                                if k in ("host", "model", "timeout",
                                                         "temperature", "num_ctx")})
        self.retries = int(retries)
        self.objective_name = objective_name
        self.pool_factor = int(pool_factor)
        self.log_dir = log_dir
        self.llm_stats = {"calls": 0, "accepted": 0, "proposed": 0, "clipped": 0,
                          "infeasible": 0, "malformed": 0, "fallback_used": 0,
                          "unreachable": 0}
        self.rationales = {}
        ok, detail = self.client.available()
        self.llm_available, self.llm_detail = ok, detail
        self._fallback = SobolSearch(self.space, seed=self.seed + 1)

    def _history(self):
        return [{"point": {k: v for k, v in p.items() if not k.startswith("_")},
                 "value": val.value}
                for p, val in zip(self.points, self.values)]

    def _log(self, tag, payload):
        if not self.log_dir:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, f"{tag}_{int(time.time() * 1000)}.json")
        with open(path, "w") as handle:
            json.dump(payload, handle, indent=1, default=str)

    def _llm_propose(self, n):
        """Guarded proposal loop. Returns candidates (possibly fewer than n)."""
        if not self.llm_available:
            self.llm_stats["unreachable"] += 1
            return []
        bp, bv = self.best()
        incumbent = ({"point": {k: v for k, v in bp.items() if not k.startswith("_")},
                      "value": bv.value} if bp else None)
        repair, collected = None, []
        for attempt in range(self.retries):
            prompt = build_prompt(self.space, self._history(), incumbent,
                                  n - len(collected), self.objective_name, repair=repair)
            try:
                raw = self.client.chat(SYSTEM_PROMPT, prompt,
                                       seed=self.seed * 1000 + self.iteration * 10 + attempt)
            except Exception as exc:                    # noqa: BLE001
                self.llm_stats["unreachable"] += 1
                self.llm_stats["accepted"] += len(collected)
                self._log("error", {"attempt": attempt, "error": f"{type(exc).__name__}: {exc}"})
                for c in collected:
                    self.rationales[self._key(c)] = c.get("_rationale", "")
                return collected
            self.llm_stats["calls"] += 1
            cands, errors, stats = parse_candidates(raw, self.space)
            for key in ("proposed", "clipped", "infeasible", "malformed"):
                self.llm_stats[key] += stats[key]
            self._log("chat", {"iteration": self.iteration, "attempt": attempt,
                               "prompt": prompt, "raw": raw, "errors": errors,
                               "accepted": len(cands)})
            for c in cands:
                if not self._is_duplicate(c) and not any(
                        self._key(c) == self._key(x) for x in collected):
                    collected.append(c)
            if len(collected) >= n:
                break
            repair = ("\n".join(f"- {e}" for e in errors[:8])
                      or "- you returned fewer candidates than requested")
        self.llm_stats["accepted"] += len(collected)
        for c in collected:
            self.rationales[self._key(c)] = c.get("_rationale", "")
        return collected[:n]

    def llm_report(self):
        return {"available": self.llm_available, "detail": self.llm_detail,
                "model": self.client.model, "host": self.client.host,
                **self.llm_stats}


@register("llm_agent")
class LLMAgent(_LLMMixin, BaseOptimizer):
    """Open-weight LLM proposes the next batch directly, inside the same gates.

    Honest but unassisted: nothing calibrates the model's proposals except the
    history it is shown. Run it to measure what an LLM proposer is worth on its
    own; run `llm_bo` to actually use one.
    """

    def __init__(self, space=None, seed=0, **kw):
        BaseOptimizer.__init__(self, space, seed=seed)
        self._setup_llm(**kw)

    def _ask(self, n):
        out = [self._tag(p, "llm") for p in self._llm_propose(n)]
        if len(out) < n:
            self.llm_stats["fallback_used"] += n - len(out)
            out = out + [self._tag(p, "fallback",
                                   reason="LLM returned too few usable proposals")
                         for p in self._fallback.ask(n - len(out))]
        return out


@register("llm_bo")
class LLMGuidedBO(_LLMMixin, GPBayesOpt):
    """LLM proposes an over-complete pool; the GP ranks it by Expected Improvement.

    The recommended agentic mode. The model contributes physics-informed
    diversity into the acquisition pool, and the surrogate decides what is
    actually worth a Geant4 slot -- so a hallucinated proposal costs one
    acquisition evaluation, not one campaign slot. During the initial design the
    LLM proposals are used directly (there is no surrogate yet to rank them).
    """

    def __init__(self, space=None, seed=0, n_init=24, **kw):
        llm_kw = {k: kw.pop(k) for k in list(kw)
                  if k in ("client", "log_dir", "retries", "objective_name",
                           "fallback", "pool_factor", "host", "model", "timeout",
                           "temperature", "num_ctx")}
        GPBayesOpt.__init__(self, space, seed=seed, n_init=n_init, **kw)
        self._setup_llm(**llm_kw)

    def _ask(self, n):
        if self._n_initial_dispatched() < self.n_init:
            out = [self._tag(p, "llm_init") for p in self._llm_propose(n)]
            if len(out) < n:
                self.llm_stats["fallback_used"] += n - len(out)
                out = out + [self._tag(p, "sobol_init")
                             for p in self.init_design.ask(n - len(out))]
            return out

        pool = self._llm_propose(n * self.pool_factor)
        if not pool:
            self.llm_stats["fallback_used"] += n
            return GPBayesOpt._ask(self, n)

        if self.gp is None or (self.n_observations % self.refit_every == 0):
            self._refit()
        U = np.array([self.space.to_unit(self.space.complete(p)) for p in pool])
        C = np.array([self.space.miller_index(self.space.complete(p)) for p in pool])
        out = []
        median_noise = float(np.nanmedian(np.array(self.SIG, dtype=float) ** 2))
        if not np.isfinite(median_noise):
            median_noise = 1e-4
        alive = list(range(len(pool)))
        for _ in range(n):
            if not alive:
                break
            mu_obs, _ = self.gp.predict(np.array(self.U), np.array(self.C))
            best = float(np.min(mu_obs))
            mu, sd = self.gp.predict(U[alive], C[alive])
            ei = expected_improvement(mu, sd, best, self.xi)
            k = int(np.argmax(ei))
            idx = alive.pop(k)
            p = pool[idx]
            self.last_acquisition[self._key(p)] = float(ei[k])
            self._tag(p, "llm_gp_ei", acquisition=float(ei[k]), gp_fit=self._fits)
            out.append(p)
            self.gp.add_fantasy(U[idx], int(C[idx]), float(mu[k]), median_noise)
        if len(out) < n:
            out.extend(GPBayesOpt._ask(self, n - len(out)))
        return out


class MockOllamaClient:
    """Canned-response client for the guard-rail tests. Never touches a network."""

    def __init__(self, responses, model="mock", host="mock://", available=True):
        self.responses = list(responses)
        self.model, self.host = model, host
        self._available = available
        self.calls = []

    def available(self):
        return (self._available,
                "mock client" if self._available else "mock: unreachable")

    def chat(self, system, user, seed=0):
        self.calls.append({"system": system, "user": user, "seed": seed})
        if not self.responses:
            raise ConnectionError("mock: out of canned responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


if __name__ == "__main__":
    client = OllamaClient()
    ok, detail = client.available()
    print(f"Ollama at {client.host}: {'AVAILABLE' if ok else 'UNAVAILABLE'} -- {detail}")
    if not ok:
        print("\nTo enable the agentic optimizers on this host (no root required):\n"
              "  curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz "
              "| tar -xz -C \"$HOME/.local\"\n"
              "  \"$HOME/.local/bin/ollama\" serve &\n"
              f"  \"$HOME/.local/bin/ollama\" pull {client.model}\n"
              "Until then llm_agent/llm_bo fall back to their non-LLM strategy and "
              "the campaign report records that they did.")
    print("\nPrompt preview (first 40 lines):")
    print("\n".join(build_prompt(DEFAULT_SPACE, [], None, 4,
                                 "total_qps_per_primary").splitlines()[:40]))
