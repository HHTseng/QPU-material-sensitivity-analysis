"""Grounded Ollama proposals ranked by the existing Gaussian-process model.

The language model proposes candidates; deterministic code validates and ranks
them; the simulator remains the only source of objective values.  This module
has no Ollama Python dependency and does not contact a model while replaying a
saved search transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np


LEGACY = Path(__file__).resolve().parents[1] / "legacy"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from stage4_optimizers import GPBayesOpt, expected_improvement  # type: ignore  # noqa: E402


DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:235b-a22b-thinking-2507-q4_K_M"
DEFAULT_CONTEXT = 65536
KNOWLEDGE_PATH = Path(__file__).with_name("agent_knowledge.md")
MATERIAL_POOL_PATH = LEGACY / "stage4_material_pool.yaml"
SIC_CONSTANTS_PATH = Path(__file__).with_name("sic_phonon_constants.yaml")


class AgenticError(RuntimeError):
    """The model service or its response cannot support a guarded proposal."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _grounded_knowledge() -> tuple[str, str]:
    """Load the small versioned material records without open-web retrieval."""

    sections = [
        ("Project rules and measured findings", KNOWLEDGE_PATH,
         "material_scan/agent_knowledge.md"),
        ("Curated material projection records", MATERIAL_POOL_PATH,
         "legacy/stage4_material_pool.yaml"),
        ("Derived SiC constants and warnings", SIC_CONSTANTS_PATH,
         "material_scan/sic_phonon_constants.yaml"),
    ]
    digest = hashlib.sha256()
    text = []
    for title, path, label in sections:
        raw = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
        text.append(f"## {title}\n\nSource file: `{label}`\n\n{raw.decode('utf-8')}")
    return "\n\n".join(text), digest.hexdigest()


def _read_json_response(response: Any) -> Mapping[str, Any]:
    raw = response.read()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgenticError("Ollama returned a non-JSON HTTP response") from error
    if not isinstance(value, Mapping):
        raise AgenticError("Ollama returned a JSON value that is not an object")
    return value


class OllamaClient:
    """Minimal client for the two Ollama endpoints used by the optimizer."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: float = 900.0,
        temperature: float = 0.0,
        num_ctx: int = DEFAULT_CONTEXT,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = str(model)
        self.timeout = float(timeout)
        self.temperature = float(temperature)
        self.num_ctx = int(num_ctx)

    def _request(
        self, path: str, payload: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Mapping[str, Any]:
        data = None if payload is None else _json_bytes(payload)
        request = Request(
            f"{self.host}{path}",
            data=data,
            method="GET" if data is None else "POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:  # noqa: S310
                return _read_json_response(response)
        except HTTPError as error:
            detail = error.read(500).decode("utf-8", errors="replace")
            raise AgenticError(
                f"Ollama {path} returned HTTP {error.code}: {detail}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise AgenticError(f"cannot reach Ollama at {self.host}: {error}") from error

    def inspect_model(self) -> Mapping[str, Any]:
        """Return the exact installed model record, including its content digest."""

        response = self._request("/api/tags", timeout=min(self.timeout, 10.0))
        models = response.get("models", [])
        for record in models if isinstance(models, list) else []:
            if not isinstance(record, Mapping):
                continue
            name = str(record.get("name") or record.get("model") or "")
            if name == self.model:
                digest = str(record.get("digest") or "")
                if not digest:
                    raise AgenticError(f"installed model {self.model!r} has no digest")
                return {
                    "name": name,
                    "digest": digest,
                    "size": int(record.get("size") or 0),
                }
        installed = sorted(
            str(record.get("name") or record.get("model"))
            for record in models
            if isinstance(record, Mapping)
        )
        raise AgenticError(
            f"exact model {self.model!r} is not installed at {self.host}; "
            f"installed models: {installed[:12]}"
        )

    def server_version(self) -> str:
        response = self._request("/api/version", timeout=min(self.timeout, 10.0))
        version = str(response.get("version") or "")
        if not version:
            raise AgenticError("Ollama did not report a server version")
        return version

    def chat(
        self,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        *,
        seed: int,
    ) -> Mapping[str, Any]:
        response = self._request(
            "/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "keep_alive": -1,
                "format": schema,
                "think": True,
                "options": {
                    "temperature": self.temperature,
                    "seed": int(seed),
                    "num_ctx": self.num_ctx,
                },
            },
        )
        message = response.get("message")
        if not isinstance(message, Mapping):
            raise AgenticError("Ollama response has no message object")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AgenticError("Ollama response has no structured content")
        response_model = str(response.get("model") or "")
        if response_model != self.model:
            raise AgenticError(
                f"Ollama answered with model {response_model!r}, expected {self.model!r}"
            )
        return {
            "model": response_model,
            "content": content,
            "thinking": str(message.get("thinking") or ""),
            "done_reason": response.get("done_reason"),
            "eval_count": response.get("eval_count"),
            "prompt_eval_count": response.get("prompt_eval_count"),
        }

    def running_models(self) -> Sequence[Mapping[str, Any]]:
        response = self._request("/api/ps", timeout=min(self.timeout, 10.0))
        models = response.get("models", [])
        return [record for record in models if isinstance(record, Mapping)] \
            if isinstance(models, list) else []


@dataclass(frozen=True)
class Candidate:
    point: Mapping[str, Any]
    rationale: str
    hypothesis: str
    runtime_risk: str
    runtime_reason: str
    response_index: int
    prompt_sha256: str


def _response_schema(space: Any, count: int) -> Mapping[str, Any]:
    numeric = {variable.name: {"type": "number"} for variable in space.variables}
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": int(count),
                "maxItems": int(max(count, count * 2)),
                "items": {
                    "type": "object",
                    "properties": {
                        "values": {
                            "type": "object",
                            "properties": numeric,
                            "required": list(space.names),
                            "additionalProperties": False,
                        },
                        "hypothesis": {"type": "string"},
                        "rationale": {"type": "string"},
                        "runtime_risk": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                        "runtime_reason": {"type": "string"},
                    },
                    "required": [
                        "values", "hypothesis", "rationale",
                        "runtime_risk", "runtime_reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """You are a candidate-proposal agent for a validated
Geant4/G4CMP material-parameter search. Minimize the declared objective.

You do not simulate. Never invent an objective value or a missing material
constant. Propose complete numeric vectors only inside the supplied bounds and
constraints. Use distinct physical hypotheses, account for observation
uncertainty and known slow regions, and include one conservative candidate near
a measured good region. Return only the requested JSON object. The evaluator,
not you, decides whether a proposal is good."""


def _parameter_catalog() -> Mapping[str, Any]:
    try:
        import yaml

        value = yaml.safe_load(Path(__file__).with_name("parameters.yaml").read_text())
    except (OSError, ValueError, ImportError):
        return {}
    parameters = value.get("parameters", {}) if isinstance(value, Mapping) else {}
    return parameters if isinstance(parameters, Mapping) else {}


def _point_values(space: Any, point: Mapping[str, Any]) -> Mapping[str, float]:
    complete = space.complete(point)
    return {name: float(complete[name]) for name in space.names}


def _observation_record(space: Any, point: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    return {
        "value": float(value.value),
        "standard_error": None if value.se is None else float(value.se),
        "elapsed_seconds": (
            None if getattr(value, "cost_seconds", None) is None
            else float(value.cost_seconds)
        ),
        "values": _point_values(space, point),
    }


def build_prompt(
    space: Any,
    points: Sequence[Mapping[str, Any]],
    observations: Sequence[Any],
    rejected: Sequence[Mapping[str, Any]],
    *,
    count: int,
    knowledge: str,
    repair: Sequence[str] = (),
) -> str:
    catalog = _parameter_catalog()
    variables = []
    for variable in space.variables:
        definition = catalog.get(variable.name, {})
        variables.append({
            "name": variable.name,
            "unit": definition.get("unit", "unknown")
            if isinstance(definition, Mapping) else "unknown",
            "bounds": [float(variable.low), float(variable.high)],
            "scale": str(variable.scale),
            "default": float(variable.baseline),
            "meaning": definition.get("source", "")
            if isinstance(definition, Mapping) else "",
        })

    history = [
        _observation_record(space, point, value)
        for point, value in zip(points, observations)
    ]
    best = sorted(history, key=lambda item: item["value"])[:8]
    recent = history[-8:]
    failures = [
        {
            "reason": str(item.get("reason", ""))[:500],
            "values": _point_values(space, item.get("point", {})),
        }
        for item in rejected[-8:]
    ]
    objective = getattr(getattr(space, "spec", None), "analysis", {}).get(
        "objective", "minimize the recorded objective"
    )
    context = {
        "objective": objective,
        "variables": variables,
        "constraints": space.to_manifest().get("constraints", [])
        if hasattr(space, "to_manifest") else [],
        "best_observations": best,
        "recent_observations": recent,
        "recent_rejections_or_failures": failures,
        "requested_candidates": int(count),
        "repair_feedback": list(repair),
    }
    return (
        "## Versioned project knowledge\n\n"
        + knowledge.strip()
        + "\n\n## Live campaign context\n\n"
        + json.dumps(context, indent=2, sort_keys=True, allow_nan=False)
        + "\n\nReturn exactly the schema supplied by the caller."
    )


def _loads_object(raw: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AgenticError(f"model content is not valid JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise AgenticError("model content must be a JSON object")
    return value


def _write_trace(directory: str | None, payload: Mapping[str, Any]) -> str | None:
    if not directory:
        return None
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    kind = str(payload.get("trace_kind", "agent-call"))
    stem = f"{kind}-{payload['iteration']:04d}-{payload['attempt']:02d}"
    payload_sha256 = hashlib.sha256(_json_bytes(payload)).hexdigest()
    destination = root / (
        f"{stem}-{str(payload['prompt_sha256'])[:8]}-{payload_sha256[:8]}.json"
    )
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return str(destination)


class AgenticOptimizer(GPBayesOpt):
    """Ollama candidate pool with deterministic GP-EI selection."""

    optimizer_name = "agentic"

    def __init__(
        self,
        space: Any = None,
        seed: int = 0,
        *,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: float = 900.0,
        temperature: float = 0.0,
        num_ctx: int = DEFAULT_CONTEXT,
        pool_size: int = 12,
        retries: int = 2,
        allow_model_fallback: bool = False,
        expected_model_digest: str | None = None,
        trace_dir: str | None = None,
        client: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(space, seed=seed, **kwargs)
        if pool_size < 2:
            raise ValueError("agent pool_size must be at least two")
        if retries < 1:
            raise ValueError("agent retries must be positive")
        self.client = client or OllamaClient(
            host=host,
            model=model,
            timeout=timeout,
            temperature=temperature,
            num_ctx=num_ctx,
        )
        self.model = str(model)
        self.host = str(host)
        self.temperature = float(temperature)
        self.num_ctx = int(num_ctx)
        self.pool_size = int(pool_size)
        self.retries = int(retries)
        self.allow_model_fallback = bool(allow_model_fallback)
        self.expected_model_digest = (
            str(expected_model_digest) if expected_model_digest else None
        )
        self.trace_dir = trace_dir
        self.knowledge, self.knowledge_sha256 = _grounded_knowledge()
        self.model_digest: str | None = None
        self.ollama_version: str | None = None
        self._model_record: Mapping[str, Any] | None = None
        self._last_agent_error: str | None = None
        self._candidate_cache: list[Candidate] = []

    def _key(self, point: Mapping[str, Any]) -> tuple[float, ...]:
        """Key proposals in unit space so tiny log variables stay distinct."""

        unit = self.space.to_unit(self.space.complete(point))
        return tuple(round(float(value), 12) for value in unit)

    def _ensure_model(self) -> Mapping[str, Any]:
        # Re-read the tag before every proposal call. A tag can be moved while a
        # long campaign is running; the content digest, not the tag, is identity.
        record = dict(self.client.inspect_model())
        if str(record.get("name")) != self.model:
            raise AgenticError(
                f"Ollama resolved {record.get('name')!r}, expected exact tag {self.model!r}"
            )
        digest = str(record["digest"])
        if self.expected_model_digest is not None and digest != self.expected_model_digest:
            raise AgenticError(
                f"model digest {digest} differs from required {self.expected_model_digest}"
            )
        if self.model_digest is not None and digest != self.model_digest:
            raise AgenticError(
                f"model digest changed from {self.model_digest} to {digest}"
            )
        self.model_digest = digest
        self._model_record = record
        if hasattr(self.client, "server_version"):
            version = str(self.client.server_version())
            if self.ollama_version is not None and version != self.ollama_version:
                raise AgenticError(
                    f"Ollama version changed from {self.ollama_version} to {version}"
                )
            self.ollama_version = version
        return self._model_record

    def _is_rejected_duplicate(self, point: Mapping[str, Any]) -> bool:
        key = self._key(point)
        return any(self._key(item["point"]) == key for item in self.rejected)

    def _parse_candidates(
        self, raw: str, prompt_sha256: str
    ) -> tuple[list[Candidate], list[str]]:
        try:
            document = _loads_object(raw)
        except AgenticError as error:
            return [], [str(error)]
        items = document.get("candidates")
        if not isinstance(items, list):
            return [], ["response has no candidates array"]
        candidates: list[Candidate] = []
        errors: list[str] = []
        seen: set[Any] = set()
        expected = set(self.space.names)
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                errors.append(f"candidate {index}: not an object")
                continue
            values = item.get("values")
            if not isinstance(values, Mapping):
                errors.append(f"candidate {index}: values is not an object")
                continue
            missing = sorted(expected - set(values))
            extra = sorted(set(values) - expected)
            if missing or extra:
                errors.append(
                    f"candidate {index}: missing={missing or 'none'} extra={extra or 'none'}"
                )
                continue
            point: dict[str, float] = {}
            malformed = []
            for variable in self.space.variables:
                value = values[variable.name]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    malformed.append(variable.name)
                    continue
                number = float(value)
                if not math.isfinite(number):
                    malformed.append(variable.name)
                    continue
                point[variable.name] = number
            if malformed:
                errors.append(f"candidate {index}: non-finite/non-numeric {malformed}")
                continue
            try:
                complete = self.space.complete(point)
                in_bounds, reasons = self.space.in_bounds(complete)
            except (KeyError, TypeError, ValueError) as error:
                errors.append(f"candidate {index}: cannot resolve: {error}")
                continue
            if not in_bounds:
                errors.append(f"candidate {index}: {'; '.join(reasons)}")
                continue
            cheap, reason = self._feasible(complete), "fast physics check failed"
            if not cheap:
                errors.append(f"candidate {index}: {reason}")
                continue
            key = self._key(complete)
            if key in seen or self._is_duplicate(complete) or self._is_rejected_duplicate(complete):
                errors.append(f"candidate {index}: duplicate evaluated/pending/rejected point")
                continue
            risk = str(item.get("runtime_risk", "")).lower()
            if risk not in ("low", "medium", "high"):
                errors.append(f"candidate {index}: invalid runtime_risk {risk!r}")
                continue
            seen.add(key)
            candidates.append(Candidate(
                point=complete,
                rationale=str(item.get("rationale", ""))[:500],
                hypothesis=str(item.get("hypothesis", ""))[:200],
                runtime_risk=risk,
                runtime_reason=str(item.get("runtime_reason", ""))[:300],
                response_index=index,
                prompt_sha256=prompt_sha256,
            ))
        return candidates, errors

    def _agent_pool(self, count: int) -> list[Candidate]:
        record = self._ensure_model()
        self._last_agent_error = None
        collected: list[Candidate] = []
        repair: list[str] = []
        for attempt in range(1, self.retries + 1):
            need = count - len(collected)
            prompt = build_prompt(
                self.space,
                self.points,
                self.values,
                self.rejected,
                count=need,
                knowledge=self.knowledge,
                repair=repair,
            )
            prompt_sha256 = _sha256_text(SYSTEM_PROMPT + "\0" + prompt)
            response: Mapping[str, Any] | None = None
            errors: list[str] = []
            accepted: list[Candidate] = []
            try:
                response = self.client.chat(
                    SYSTEM_PROMPT,
                    prompt,
                    _response_schema(self.space, need),
                    seed=self.seed * 100000 + self.iteration * 100 + attempt,
                )
                # Close the tag-movement race by verifying identity immediately
                # after the response as well as immediately before it.
                self._ensure_model()
                accepted, errors = self._parse_candidates(
                    str(response["content"]), prompt_sha256
                )
            except AgenticError as error:
                errors = [str(error)]
            existing = {self._key(candidate.point) for candidate in collected}
            collected.extend(
                candidate for candidate in accepted
                if self._key(candidate.point) not in existing
            )
            trace = {
                "schema": "material-scan-agent-call-1",
                "iteration": self.iteration,
                "attempt": attempt,
                "seed": self.seed * 100000 + self.iteration * 100 + attempt,
                "model": self.model,
                "model_digest": record["digest"],
                "ollama_version": self.ollama_version,
                "host": self.host,
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "knowledge_sha256": self.knowledge_sha256,
                "prompt_sha256": prompt_sha256,
                "system_prompt": SYSTEM_PROMPT,
                "user_prompt": prompt,
                "response": response,
                "validation_errors": errors,
                "accepted": [
                    {
                        "values": _point_values(self.space, candidate.point),
                        "hypothesis": candidate.hypothesis,
                        "rationale": candidate.rationale,
                        "runtime_risk": candidate.runtime_risk,
                        "runtime_reason": candidate.runtime_reason,
                    }
                    for candidate in accepted
                ],
            }
            _write_trace(self.trace_dir, trace)
            if len(collected) >= count:
                self._last_agent_error = None
                return collected[:count]
            repair = errors[:10] or [
                f"Only {len(accepted)} usable candidates were returned; supply {need}."
            ]
            self._last_agent_error = "; ".join(repair)
        return collected[:count]

    def _prepare_gp(self) -> tuple[float, float]:
        if self.n_observations < self.n_min_fit:
            raise AgenticError(
                f"agentic GP needs {self.n_min_fit} observations; has {self.n_observations}"
            )
        if self.gp is None or self.n_observations % self.refit_every == 0:
            self._refit()
        else:
            self.gp.reset_data(
                np.asarray(self.U), np.asarray(self.Y),
                np.asarray(self.SIG, dtype=float) ** 2,
            )
        noise = float(np.nanmedian(np.asarray(self.SIG, dtype=float) ** 2))
        if not np.isfinite(noise):
            noise = 1e-4
        for point in self.pending:
            unit = self.space.to_unit(self.space.complete(point))
            prediction, _ = self.gp.predict(unit[None, :])
            self.gp.add_fantasy(unit, float(prediction[0]), noise)
        observed_mean, _ = self.gp.predict(np.asarray(self.U))
        return float(np.min(observed_mean)), noise

    def _fallback(self, count: int, reason: str) -> list[Mapping[str, Any]]:
        points = GPBayesOpt._ask(self, count)
        for point in points:
            prior = dict(self._provenance.get(self._key(point), {}))
            self._tag(
                point,
                "gp_fallback",
                reason=reason[:500],
                fallback_source=prior.get("proposal_source"),
                acquisition=prior.get("acquisition"),
                agent_model=self.model,
                agent_model_digest=self.model_digest,
                ollama_version=self.ollama_version,
                knowledge_sha256=self.knowledge_sha256,
            )
        return points

    def _candidate_record(self, candidate: Candidate) -> Mapping[str, Any]:
        return {
            "values": _point_values(self.space, candidate.point),
            "rationale": candidate.rationale,
            "hypothesis": candidate.hypothesis,
            "runtime_risk": candidate.runtime_risk,
            "runtime_reason": candidate.runtime_reason,
            "response_index": candidate.response_index,
            "prompt_sha256": candidate.prompt_sha256,
        }

    def _restore_candidate_pool(
        self, records: Sequence[Mapping[str, Any]]
    ) -> list[Candidate]:
        return [
            Candidate(
                point=self.space.complete(record["values"]),
                rationale=str(record.get("rationale", "")),
                hypothesis=str(record.get("hypothesis", "")),
                runtime_risk=str(record.get("runtime_risk", "")),
                runtime_reason=str(record.get("runtime_reason", "")),
                response_index=int(record.get("response_index", 0)),
                prompt_sha256=str(record.get("prompt_sha256", "")),
            )
            for record in records
        ]

    def _ask(self, count: int) -> list[Mapping[str, Any]]:
        if len(self._candidate_cache) < count:
            requested = max(self.pool_size, count)
            try:
                candidates = self._agent_pool(requested)
            except AgenticError as error:
                self._last_agent_error = str(error)
                if not self.allow_model_fallback:
                    raise
                return self._fallback(count, str(error))
            if len(candidates) < requested:
                reason = (
                    f"model returned {len(candidates)}/{requested} usable pool candidates"
                )
                if self._last_agent_error:
                    reason += f": {self._last_agent_error}"
                self._last_agent_error = reason
                if not self.allow_model_fallback:
                    raise AgenticError(reason)
                return self._fallback(count, reason)
            self._candidate_cache = list(candidates)

        candidates = list(self._candidate_cache)

        best, noise = self._prepare_gp()
        alive = list(candidates)
        selected: list[Mapping[str, Any]] = []
        for _ in range(count):
            units = np.asarray([
                self.space.to_unit(self.space.complete(candidate.point))
                for candidate in alive
            ])
            mean, deviation = self.gp.predict(units)
            improvement = expected_improvement(mean, deviation, best, self.xi)
            chosen_index = int(np.argmax(improvement))
            candidate = alive.pop(chosen_index)
            unit = units[chosen_index]
            acquisition = float(improvement[chosen_index])
            point = candidate.point
            self.last_acquisition[self._key(point)] = acquisition
            self._tag(
                point,
                "agent_gp_ei",
                acquisition=acquisition,
                runtime_risk=candidate.runtime_risk,
                runtime_reason=candidate.runtime_reason,
                hypothesis=candidate.hypothesis,
                rationale=candidate.rationale,
                response_index=candidate.response_index,
                prompt_sha256=candidate.prompt_sha256,
                knowledge_sha256=self.knowledge_sha256,
                agent_model=self.model,
                agent_model_digest=self.model_digest,
                ollama_version=self.ollama_version,
                gp_fit=self._fits,
                n_observations_at_proposal=self.n_observations,
            )
            selected.append(point)
            self.gp.add_fantasy(unit, float(mean[chosen_index]), noise)
        self._candidate_cache = alive
        remaining = [self._candidate_record(candidate) for candidate in alive]
        for point in selected:
            self._provenance[self._key(point)]["agent_pool_remaining"] = remaining
        selection_prompt_hash = _sha256_text("\n".join(sorted({
            candidate.prompt_sha256 for candidate in candidates
        })))
        _write_trace(self.trace_dir, {
            "schema": "material-scan-agent-selection-1",
            "trace_kind": "agent-selection",
            "iteration": self.iteration,
            "attempt": 0,
            "prompt_sha256": selection_prompt_hash,
            "model": self.model,
            "model_digest": self.model_digest,
            "ollama_version": self.ollama_version,
            "knowledge_sha256": self.knowledge_sha256,
            "selected": [
                {
                    "values": _point_values(self.space, point),
                    "provenance": self.provenance_of(point),
                }
                for point in selected
            ],
        })
        return selected

    def _restore_agent_metadata(
        self, provenance: Sequence[Mapping[str, Any]]
    ) -> None:
        models = {str(item["agent_model"]) for item in provenance if item.get("agent_model")}
        if models and models != {self.model}:
            raise AgenticError(f"saved agent model differs from {self.model!r}: {sorted(models)}")

        knowledge_digests = {
            str(item["knowledge_sha256"])
            for item in provenance
            if item.get("knowledge_sha256")
        }
        if knowledge_digests and knowledge_digests != {self.knowledge_sha256}:
            raise AgenticError("saved agent knowledge digest differs")

        model_digests = {
            str(item["agent_model_digest"])
            for item in provenance
            if item.get("agent_model_digest")
        }
        if len(model_digests) > 1:
            raise AgenticError(
                f"saved agent proposal uses multiple model digests: {sorted(model_digests)}"
            )
        if model_digests:
            digest = next(iter(model_digests))
            if self.expected_model_digest is not None and digest != self.expected_model_digest:
                raise AgenticError("saved agent model digest differs from the required digest")
            if self.model_digest is not None and digest != self.model_digest:
                raise AgenticError("saved agent model digest changed within the transcript")
            self.model_digest = digest

        versions = {
            str(item["ollama_version"])
            for item in provenance
            if item.get("ollama_version")
        }
        if len(versions) > 1:
            raise AgenticError(
                f"saved agent proposal uses multiple Ollama versions: {sorted(versions)}"
            )
        if versions:
            version = next(iter(versions))
            if self.ollama_version is not None and version != self.ollama_version:
                raise AgenticError("saved Ollama version changed within the transcript")
            self.ollama_version = version

    def replay_ask(
        self,
        points: Sequence[Mapping[str, Any]],
        provenance: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        """Reconstruct one saved ask without making an Ollama request."""

        self.iteration += 1
        sources = [str(item.get("proposal_source", "")) for item in provenance]
        self._restore_agent_metadata(provenance)
        if sources and all(source == "gp_fallback" for source in sources):
            produced = GPBayesOpt._ask(self, len(points))
            if [_point_values(self.space, point) for point in produced] != [
                _point_values(self.space, point) for point in points
            ]:
                raise AgenticError("saved GP fallback proposal did not replay")
        elif sources and all(source == "agent_gp_ei" for source in sources):
            _, noise = self._prepare_gp()
            for point, source in zip(points, provenance):
                unit = self.space.to_unit(self.space.complete(point))
                prediction, _ = self.gp.predict(unit[None, :])
                self.gp.add_fantasy(unit, float(prediction[0]), noise)
                if source.get("acquisition") is not None:
                    self.last_acquisition[self._key(point)] = float(source["acquisition"])
            remaining = provenance[-1].get("agent_pool_remaining", [])
            if not isinstance(remaining, list):
                raise AgenticError("saved agent pool is not a list")
            self._candidate_cache = self._restore_candidate_pool(remaining)
        else:
            raise AgenticError(f"cannot replay mixed or unknown agent sources: {sources}")
        for point, source in zip(points, provenance):
            self._provenance[self._key(point)] = dict(source)
        restored = [dict(point) for point in points]
        self.pending.extend(restored)
        return restored

    def state(self) -> Mapping[str, Any]:
        state = dict(super().state())
        counts = self.provenance_counts()
        state.update({
            "optimizer": self.optimizer_name,
            "agent_model": self.model,
            "agent_model_digest": self.model_digest,
            "ollama_version": self.ollama_version,
            "knowledge_sha256": self.knowledge_sha256,
            "agent_selected": counts.get("agent_gp_ei", 0),
            "fallback_selected": counts.get("gp_fallback", 0),
            "agent_pool_remaining": len(self._candidate_cache),
        })
        return state


class MockOllamaClient:
    """Canned client used by tests; it never opens a network connection."""

    def __init__(
        self,
        responses: Sequence[str | Exception],
        *,
        model: str = DEFAULT_MODEL,
        digest: str = "mock-digest",
    ) -> None:
        self.responses = list(responses)
        self.model = model
        self.digest = digest
        self.calls: list[Mapping[str, Any]] = []

    def inspect_model(self) -> Mapping[str, Any]:
        return {"name": self.model, "digest": self.digest, "size": 1}

    def server_version(self) -> str:
        return "mock-version"

    def chat(
        self,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        *,
        seed: int,
    ) -> Mapping[str, Any]:
        self.calls.append({"system": system, "user": user, "schema": schema, "seed": seed})
        if not self.responses:
            raise AgenticError("mock response list is empty")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return {
            "model": self.model,
            "content": response,
            "thinking": "",
            "done_reason": "stop",
            "eval_count": 1,
            "prompt_eval_count": 1,
        }


def preflight(
    *,
    host: str = DEFAULT_HOST,
    model: str = DEFAULT_MODEL,
    expected_digest: str | None = None,
    num_ctx: int = DEFAULT_CONTEXT,
    timeout: float = 30.0,
    warmup: bool = False,
) -> Mapping[str, Any]:
    """Check model identity, structured output, context, and GPU residency."""

    client = OllamaClient(host=host, model=model, timeout=timeout, num_ctx=num_ctx)
    record = dict(client.inspect_model())
    if expected_digest and record["digest"] != expected_digest:
        raise AgenticError(
            f"model digest {record['digest']} differs from expected {expected_digest}"
        )
    warmup_complete = False
    if warmup:
        schema = {
            "type": "object",
            "properties": {"ready": {"type": "boolean"}},
            "required": ["ready"],
            "additionalProperties": False,
        }
        response = client.chat(
            "Return the requested JSON only.",
            "Return {\"ready\": true}.",
            schema,
            seed=0,
        )
        value = _loads_object(str(response["content"]))
        if value != {"ready": True}:
            raise AgenticError(f"structured warm-up returned unexpected value: {value}")
        warmup_complete = True
    running = []
    for item in client.running_models():
        name = str(item.get("name") or item.get("model") or "")
        digest = str(item.get("digest") or "")
        if name != model or digest != record["digest"]:
            continue
        size = int(item.get("size") or 0)
        size_vram = int(item.get("size_vram") or 0)
        context_length = int(item.get("context_length") or 0)
        running.append({
            "name": name,
            "digest": digest,
            "size": size,
            "size_vram": size_vram,
            "gpu_fraction": (None if size <= 0 else size_vram / size),
            "context_length": context_length,
        })
    fully_on_gpu = any(
        item["size"] > 0 and item["size_vram"] == item["size"] for item in running
    )
    context_matches = any(item["context_length"] == int(num_ctx) for item in running)
    ready = any(
        item["size"] > 0
        and item["size_vram"] == item["size"]
        and item["context_length"] == int(num_ctx)
        for item in running
    )
    return {
        "host": host,
        "ollama_version": client.server_version(),
        "model": record,
        "requested_context_length": int(num_ctx),
        "warmup_complete": warmup_complete,
        "running": running,
        "fully_on_gpu": fully_on_gpu,
        "context_matches": context_matches,
        "ready": ready,
    }
