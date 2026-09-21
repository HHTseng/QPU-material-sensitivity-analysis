"""Strict, immutable experiment configuration.

Parameters have three deliberately separate layers:

1. :class:`ParameterDefinition` records meaning, units, physical validity, and
   the destination in the physics model.
2. :class:`ExperimentParameter` records this experiment's fixed choice or
   search interval.  Search bounds are not global material facts.
3. :class:`ResolvedParameter` records the value actually used and whether it
   came from a proposal, a measured material, or a fixed control.

``resolve_experiment`` expands all three layers, the complete recorded sampling
design, and every task seed into one immutable manifest before launch.  There
is no API for applying scientific command-line overrides to that object.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple

from .identity import canonical_hash
from .sampling import (
    RecordedStratifiedDesign,
    SamplingError,
    SamplingPlan,
    build_recorded_plan,
    load_recorded_stratified_design,
)


CATALOG_SCHEMA = "material-scan-parameters-1"
EXPERIMENT_SCHEMA = "material-scan-experiment-1"
RESOLVED_SCHEMA = "material-scan-resolved-experiment-1"


class ConfigError(ValueError):
    """Configuration is incomplete, ambiguous, or physically invalid."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: Any, label: str, *, nonempty: bool = False) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be a mapping")
    if nonempty and not value:
        raise ConfigError(f"{label} must not be empty")
    if any(not isinstance(key, str) for key in value):
        raise ConfigError(f"{label} keys must be strings")
    return value


def _reject_unknown(source: Mapping[str, Any], allowed: Sequence[str], label: str) -> None:
    unknown = sorted(set(source) - set(allowed))
    if unknown:
        raise ConfigError(f"{label} has unknown field(s) {unknown}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{label} must be finite")
    return result


def _load_document(source: Any) -> Tuple[Mapping[str, Any], Optional[Path]]:
    if isinstance(source, Mapping):
        return source, None
    path = Path(source).expanduser().resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            document = json.loads(text)
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError as exc:
                raise ConfigError(
                    "YAML input requires PyYAML; JSON uses only the standard library"
                ) from exc
            try:
                document = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ConfigError(f"cannot parse {path}: {exc}") from exc
        else:
            raise ConfigError(f"unsupported configuration suffix {suffix!r}")
    except (json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    return _mapping(document, str(path)), path.parent


def _full_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value.lower()
    ):
        raise ConfigError(f"{label} must be a full hexadecimal SHA-256")
    return value.lower()


@dataclass(frozen=True)
class ParameterDefinition:
    name: str
    unit: str
    value_type: str
    target: str
    valid_min: Optional[float]
    valid_max: Optional[float]
    choices: Tuple[Any, ...]
    source: Any
    description: str = ""

    @classmethod
    def from_mapping(cls, name: str, source: Mapping[str, Any]) -> "ParameterDefinition":
        source = _mapping(source, f"parameter definition {name!r}")
        _reject_unknown(
            source,
            ("unit", "type", "target", "valid_range", "choices", "source", "description"),
            f"parameter definition {name!r}",
        )
        unit = source.get("unit")
        target = source.get("target")
        value_type = source.get("type")
        if not isinstance(unit, str) or not unit:
            raise ConfigError(f"parameter {name!r} needs an explicit unit; use '1' if dimensionless")
        if not isinstance(target, str) or not target:
            raise ConfigError(f"parameter {name!r} needs a non-empty physics target")
        if value_type not in ("float", "int", "choice", "bool"):
            raise ConfigError(
                f"parameter {name!r} type must be float, int, choice, or bool"
            )
        valid_min = valid_max = None
        choices: Tuple[Any, ...] = ()
        if value_type in ("float", "int"):
            valid = source.get("valid_range")
            if not isinstance(valid, Sequence) or isinstance(valid, (str, bytes)) or len(valid) != 2:
                raise ConfigError(f"numeric parameter {name!r} needs valid_range: [min, max]")
            valid_min = _number(valid[0], f"{name}.valid_range[0]")
            valid_max = _number(valid[1], f"{name}.valid_range[1]")
            if valid_min > valid_max:
                raise ConfigError(f"parameter {name!r} has reversed valid_range")
            if "choices" in source:
                raise ConfigError(f"numeric parameter {name!r} cannot declare choices")
        elif value_type == "choice":
            raw_choices = source.get("choices")
            if not isinstance(raw_choices, Sequence) or isinstance(raw_choices, (str, bytes)) \
                    or not raw_choices:
                raise ConfigError(f"choice parameter {name!r} needs a non-empty choices list")
            choices = tuple(_freeze(value) for value in raw_choices)
            if "valid_range" in source:
                raise ConfigError(f"choice parameter {name!r} cannot declare valid_range")
        elif "valid_range" in source or "choices" in source:
            raise ConfigError(f"boolean parameter {name!r} cannot declare a range or choices")
        if "source" not in source:
            raise ConfigError(f"parameter {name!r} needs field-level source provenance")
        definition = cls(
            name=name,
            unit=unit,
            value_type=value_type,
            target=target,
            valid_min=valid_min,
            valid_max=valid_max,
            choices=choices,
            source=_freeze(source["source"]),
            description=str(source.get("description", "")),
        )
        # Validate choice entries and boolean semantics at catalog load.
        for choice in definition.choices:
            definition.normalize_value(choice)
        return definition

    def normalize_value(self, value: Any) -> Any:
        if self.value_type == "float":
            result = _number(value, self.name)
        elif self.value_type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigError(f"{self.name} must be an integer")
            result = value
        elif self.value_type == "bool":
            if not isinstance(value, bool):
                raise ConfigError(f"{self.name} must be true or false")
            return value
        else:
            normalized = _freeze(value)
            if normalized not in self.choices:
                raise ConfigError(f"{self.name}={value!r} is not one of {self.choices!r}")
            return normalized
        if result < self.valid_min or result > self.valid_max:  # type: ignore[operator]
            raise ConfigError(
                f"{self.name}={result:g} {self.unit} is outside physical validity "
                f"[{self.valid_min:g}, {self.valid_max:g}]"
            )
        return result

    def to_manifest(self) -> Mapping[str, Any]:
        out = {
            "name": self.name,
            "unit": self.unit,
            "type": self.value_type,
            "target": self.target,
            "source": _thaw(self.source),
        }
        if self.value_type in ("float", "int"):
            out["valid_range"] = [self.valid_min, self.valid_max]
        elif self.value_type == "choice":
            out["choices"] = _thaw(self.choices)
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class ParameterCatalog:
    definitions: Tuple[ParameterDefinition, ...]
    meta: Mapping[str, Any]
    catalog_key: str

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any]) -> "ParameterCatalog":
        source = _mapping(source, "parameter catalog")
        _reject_unknown(source, ("schema", "parameters", "meta"), "parameter catalog")
        if source.get("schema") != CATALOG_SCHEMA:
            raise ConfigError(f"parameter catalog schema must be {CATALOG_SCHEMA!r}")
        raw = _mapping(source.get("parameters"), "parameter catalog parameters", nonempty=True)
        definitions = tuple(
            ParameterDefinition.from_mapping(name, raw[name]) for name in sorted(raw)
        )
        meta = _freeze(source.get("meta", {}))
        payload = {
            "schema": CATALOG_SCHEMA,
            "parameters": {definition.name: definition.to_manifest() for definition in definitions},
            "meta": _thaw(meta),
        }
        return cls(
            definitions=definitions,
            meta=meta,
            catalog_key=canonical_hash("parameter-catalog/v1", payload),
        )

    def get(self, name: str) -> ParameterDefinition:
        for definition in self.definitions:
            if definition.name == name:
                return definition
        raise ConfigError(f"experiment names undefined parameter {name!r}")


@dataclass(frozen=True)
class ExperimentParameter:
    name: str
    mode: str
    scale: Optional[str]
    lower: Optional[float]
    upper: Optional[float]
    fixed_value: Any
    default_value: Any
    prior: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls, definition: ParameterDefinition, source: Mapping[str, Any]
    ) -> "ExperimentParameter":
        source = _mapping(source, f"experiment parameter {definition.name!r}")
        mode = source.get("mode")
        if mode == "fixed":
            _reject_unknown(source, ("mode", "value"), f"experiment parameter {definition.name!r}")
            if "value" not in source:
                raise ConfigError(f"fixed parameter {definition.name!r} needs value")
            return cls(
                name=definition.name,
                mode=mode,
                scale=None,
                lower=None,
                upper=None,
                fixed_value=definition.normalize_value(source["value"]),
                default_value=None,
                prior=_freeze({}),
            )
        if mode != "search":
            raise ConfigError(f"parameter {definition.name!r} mode must be fixed or search")
        _reject_unknown(source, ("mode", "bounds", "scale", "default", "prior"),
                        f"experiment parameter {definition.name!r}")
        if definition.value_type not in ("float", "int"):
            raise ConfigError(f"search parameter {definition.name!r} must be numeric")
        bounds = source.get("bounds")
        if not isinstance(bounds, Sequence) or isinstance(bounds, (str, bytes)) or len(bounds) != 2:
            raise ConfigError(f"search parameter {definition.name!r} needs bounds: [low, high]")
        low = definition.normalize_value(bounds[0])
        high = definition.normalize_value(bounds[1])
        if low >= high:
            raise ConfigError(f"search parameter {definition.name!r} needs low < high")
        scale = source.get("scale", "linear")
        if scale not in ("linear", "log"):
            raise ConfigError(f"search parameter {definition.name!r} scale must be linear or log")
        if scale == "log" and low <= 0:
            raise ConfigError(f"log search parameter {definition.name!r} needs a positive lower bound")
        if "default" not in source:
            raise ConfigError(
                f"search parameter {definition.name!r} needs an explicit experiment default"
            )
        default = definition.normalize_value(source["default"])
        if not low <= default <= high:
            raise ConfigError(
                f"search parameter {definition.name!r} default is outside its bounds"
            )
        prior = _mapping(source.get("prior"), f"{definition.name}.prior", nonempty=True)
        _reject_unknown(prior, ("kind", "mean", "std"), f"{definition.name}.prior")
        kind = prior.get("kind")
        if kind not in ("uniform", "log-uniform", "normal"):
            raise ConfigError(
                f"search parameter {definition.name!r} prior kind must be uniform, "
                "log-uniform, or normal"
            )
        if kind == "log-uniform" and scale != "log":
            raise ConfigError(f"{definition.name}: log-uniform prior requires log scale")
        if kind in ("uniform", "log-uniform") and set(prior) != {"kind"}:
            raise ConfigError(f"{definition.name}: {kind} prior takes no mean or std")
        if kind == "normal":
            if set(prior) != {"kind", "mean", "std"}:
                raise ConfigError(f"{definition.name}: normal prior needs mean and std")
            mean = _number(prior["mean"], f"{definition.name}.prior.mean")
            std = _number(prior["std"], f"{definition.name}.prior.std")
            if std <= 0.0:
                raise ConfigError(f"{definition.name}: normal prior std must be positive")
            prior = {"kind": kind, "mean": mean, "std": std}
        return cls(
            definition.name, mode, scale, float(low), float(high), None,
            _freeze(default), _freeze(prior),
        )

    def to_manifest(self) -> Mapping[str, Any]:
        if self.mode == "fixed":
            return {"name": self.name, "mode": self.mode, "value": _thaw(self.fixed_value)}
        return {
            "name": self.name,
            "mode": self.mode,
            "bounds": [self.lower, self.upper],
            "scale": self.scale,
            "default": _thaw(self.default_value),
            "prior": _thaw(self.prior),
        }


@dataclass(frozen=True)
class ResolvedParameter:
    definition: ParameterDefinition
    experiment: ExperimentParameter
    value: Any
    origin: str
    source: Any

    def to_manifest(self) -> Mapping[str, Any]:
        return {
            "definition": self.definition.to_manifest(),
            "experiment": self.experiment.to_manifest(),
            "resolved": {
                "value": _thaw(self.value),
                "origin": self.origin,
                "source": _thaw(self.source),
            },
        }


@dataclass(frozen=True)
class JointConstraint:
    """A small, auditable linear constraint over resolved numeric parameters."""

    name: str
    terms: Tuple[Tuple[str, float], ...]
    operator: str
    rhs: float

    @classmethod
    def from_mapping(
        cls, source: Mapping[str, Any], declared_parameters: Sequence[str]
    ) -> "JointConstraint":
        source = _mapping(source, "joint constraint")
        _reject_unknown(source, ("name", "kind", "terms", "operator", "rhs"),
                        "joint constraint")
        if source.get("kind") != "linear":
            raise ConfigError("joint constraint kind must be 'linear'")
        name = source.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError("joint constraint needs a non-empty name")
        raw_terms = _mapping(source.get("terms"), f"constraint {name}.terms", nonempty=True)
        unknown = sorted(set(raw_terms) - set(declared_parameters))
        if unknown:
            raise ConfigError(f"constraint {name!r} names undeclared parameters {unknown}")
        terms = tuple(
            (parameter, _number(coefficient, f"constraint {name}.{parameter}"))
            for parameter, coefficient in sorted(raw_terms.items())
        )
        if all(coefficient == 0.0 for _, coefficient in terms):
            raise ConfigError(f"constraint {name!r} has only zero coefficients")
        operator = source.get("operator")
        if operator not in (">", ">=", "<", "<="):
            raise ConfigError(f"constraint {name!r} has unsupported operator {operator!r}")
        rhs = _number(source.get("rhs"), f"constraint {name}.rhs")
        return cls(name=name, terms=terms, operator=operator, rhs=rhs)

    def evaluate(self, values: Mapping[str, Any]) -> bool:
        left = sum(coefficient * _number(values[name], name) for name, coefficient in self.terms)
        return {
            ">": left > self.rhs,
            ">=": left >= self.rhs,
            "<": left < self.rhs,
            "<=": left <= self.rhs,
        }[self.operator]

    def to_manifest(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "kind": "linear",
            "terms": dict(self.terms),
            "operator": self.operator,
            "rhs": self.rhs,
        }


def _validated_build(source: Any) -> Mapping[str, Any]:
    build = _mapping(source, "build", nonempty=True)
    mode = build.get("mode")
    if mode == "historical-unresolved":
        _reject_unknown(build, ("mode", "reason", "recorded"), "build")
        if not isinstance(build.get("reason"), str) or not build["reason"].strip():
            raise ConfigError("historical-unresolved build needs a non-empty reason")
        return _freeze(build)
    if mode != "verified":
        raise ConfigError("build.mode must be 'verified' or 'historical-unresolved'")
    allowed = (
        "mode", "executable_sha256", "runtime_input_sha256", "geant4_revision",
        "g4cmp_revision", "detector_revision", "compiler", "compiler_flags",
    )
    _reject_unknown(build, allowed, "build")
    normalized = dict(build)
    normalized["executable_sha256"] = _full_hash(
        build.get("executable_sha256"), "build.executable_sha256"
    )
    runtime = _mapping(
        build.get("runtime_input_sha256"), "build.runtime_input_sha256", nonempty=True
    )
    missing = sorted({"libMain.so", "libG4cmp.so"} - set(runtime))
    if missing:
        raise ConfigError(f"verified build is missing runtime library hash(es) {missing}")
    normalized["runtime_input_sha256"] = {
        name: _full_hash(digest, f"build.runtime_input_sha256[{name!r}]")
        for name, digest in runtime.items()
    }
    for name in ("geant4_revision", "g4cmp_revision", "detector_revision"):
        if not isinstance(build.get(name), str) or not build[name].strip():
            raise ConfigError(f"verified build needs non-empty {name}")
    return _freeze(normalized)


def _validated_analysis(source: Any) -> Mapping[str, Any]:
    analysis = _mapping(source, "analysis", nonempty=True)
    if not isinstance(analysis.get("objective"), str) or not analysis["objective"].strip():
        raise ConfigError("analysis needs a non-empty objective")
    environment = _mapping(
        analysis.get("environment"), "analysis.environment", nonempty=True
    )
    missing = sorted({"python", "numpy", "pandas", "blas"} - set(environment))
    if missing:
        raise ConfigError(f"analysis.environment is missing version(s) {missing}")
    if any(not isinstance(version, str) or not version.strip()
           for version in environment.values()):
        raise ConfigError("analysis.environment versions must be non-empty strings")
    # Objective-specific settings remain data, but the whole mapping is frozen
    # and enters both the resolved manifest and analysis identity.
    return _freeze(analysis)


def _validated_search(source: Any) -> Mapping[str, Any]:
    search = _mapping(source, "search")
    if not search:
        return _freeze({})
    if not isinstance(search.get("method"), str) or not search["method"].strip():
        raise ConfigError("search needs a non-empty method")
    _full_hash(search.get("implementation_sha256"), "search.implementation_sha256")
    environment = _mapping(search.get("environment"), "search.environment", nonempty=True)
    missing = sorted({"python", "numpy", "scipy", "blas"} - set(environment))
    if missing:
        raise ConfigError(f"search.environment is missing version(s) {missing}")
    if any(not isinstance(version, str) or not version.strip()
           for version in environment.values()):
        raise ConfigError("search.environment versions must be non-empty strings")
    return _freeze(search)


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    purpose: str
    parameters: Tuple[ExperimentParameter, ...]
    constraints: Tuple[JointConstraint, ...]
    design: RecordedStratifiedDesign
    replicas: int
    seed_algorithm: str
    seed_base: int
    seed_bank_id: int
    event_counts: Mapping[str, Any]
    physics: Mapping[str, Any]
    analysis: Mapping[str, Any]
    search: Mapping[str, Any]
    build: Mapping[str, Any]
    provenance: Mapping[str, Any]
    spec_key: str

    def parameter(self, name: str) -> ExperimentParameter:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise ConfigError(f"resolved value supplied for undeclared parameter {name!r}")

    def to_manifest(self) -> Mapping[str, Any]:
        return {
            "schema": EXPERIMENT_SCHEMA,
            "id": self.experiment_id,
            "purpose": self.purpose,
            "parameters": {
                parameter.name: {
                    key: value for key, value in parameter.to_manifest().items() if key != "name"
                }
                for parameter in self.parameters
            },
            "constraints": [constraint.to_manifest() for constraint in self.constraints],
            "sampling": {
                "kind": "recorded-stratified",
                "design": self.design.to_manifest(),
                "replicas": self.replicas,
                "seed": {
                    "algorithm": self.seed_algorithm,
                    "base": self.seed_base,
                    "bank_id": self.seed_bank_id,
                },
            },
            "event_counts": _thaw(self.event_counts),
            "physics": _thaw(self.physics),
            "analysis": _thaw(self.analysis),
            "search": _thaw(self.search),
            "build": _thaw(self.build),
            "provenance": _thaw(self.provenance),
        }

    @classmethod
    def from_mapping(
        cls,
        source: Mapping[str, Any],
        catalog: ParameterCatalog,
        *,
        base_dir: Optional[Path] = None,
    ) -> "ExperimentSpec":
        source = _mapping(source, "experiment")
        allowed = (
            "schema", "id", "purpose", "parameters", "constraints", "sampling", "event_counts",
            "physics", "analysis", "search", "build", "provenance",
        )
        _reject_unknown(source, allowed, "experiment")
        if source.get("schema") != EXPERIMENT_SCHEMA:
            raise ConfigError(f"experiment schema must be {EXPERIMENT_SCHEMA!r}")
        experiment_id = source.get("id")
        purpose = source.get("purpose")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise ConfigError("experiment id must be a non-empty string")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ConfigError("experiment purpose must be a non-empty string")
        raw_parameters = _mapping(source.get("parameters"), "experiment parameters", nonempty=True)
        parameters = tuple(
            ExperimentParameter.from_mapping(catalog.get(name), raw_parameters[name])
            for name in sorted(raw_parameters)
        )
        raw_constraints = source.get("constraints", ())
        if not isinstance(raw_constraints, Sequence) or isinstance(
            raw_constraints, (str, bytes)
        ):
            raise ConfigError("constraints must be a sequence")
        constraints = tuple(
            JointConstraint.from_mapping(item, tuple(raw_parameters))
            for item in raw_constraints
        )
        names = [constraint.name for constraint in constraints]
        if len(names) != len(set(names)):
            raise ConfigError("joint constraint names must be unique")

        sampling = _mapping(source.get("sampling"), "sampling", nonempty=True)
        _reject_unknown(
            sampling, ("kind", "design", "design_file", "replicas", "seed"), "sampling"
        )
        if sampling.get("kind") != "recorded-stratified":
            raise ConfigError("this safe slice accepts only a recorded-stratified design")
        if ("design" in sampling) == ("design_file" in sampling):
            raise ConfigError("sampling must contain exactly one of design or design_file")
        try:
            if "design" in sampling:
                design = load_recorded_stratified_design(sampling["design"])
            else:
                design_path = Path(str(sampling["design_file"]))
                if not design_path.is_absolute():
                    if base_dir is None:
                        raise ConfigError("relative design_file needs a file-backed experiment")
                    design_path = base_dir / design_path
                design = load_recorded_stratified_design(design_path)
        except SamplingError as exc:
            raise ConfigError(str(exc)) from exc
        replicas = sampling.get("replicas")
        if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas <= 0:
            raise ConfigError("sampling.replicas must be a positive integer")
        seed = _mapping(sampling.get("seed"), "sampling.seed", nonempty=True)
        _reject_unknown(seed, ("algorithm", "base", "bank_id"), "sampling.seed")
        if set(seed) != {"algorithm", "base", "bank_id"}:
            raise ConfigError("sampling.seed requires algorithm, base, and bank_id")
        if not isinstance(seed["base"], int) or isinstance(seed["base"], bool):
            raise ConfigError("sampling.seed.base must be an integer")
        if not isinstance(seed["bank_id"], int) or isinstance(seed["bank_id"], bool):
            raise ConfigError("sampling.seed.bank_id must be an integer")

        event_counts = _mapping(source.get("event_counts"), "event_counts", nonempty=True)
        events = event_counts.get("events_per_task")
        if not isinstance(events, int) or isinstance(events, bool) or events <= 0:
            raise ConfigError("event_counts.events_per_task must be a positive integer")
        expected_total = design.n_sites * replicas * events
        if "events_total" in event_counts and event_counts["events_total"] != expected_total:
            raise ConfigError(
                f"event_counts.events_total={event_counts['events_total']} disagrees with "
                f"{design.n_sites} sites x {replicas} replicas x {events} = {expected_total}"
            )
        # Store the total explicitly even if the concise source omitted it.
        event_counts_resolved = dict(event_counts)
        event_counts_resolved["events_total"] = expected_total

        physics = _mapping(source.get("physics"), "physics", nonempty=True)
        analysis = _validated_analysis(source.get("analysis"))
        build = _validated_build(source.get("build"))
        search = _validated_search(source.get("search", {}))
        provenance = _mapping(source.get("provenance", {}), "provenance")

        provisional = cls(
            experiment_id=experiment_id,
            purpose=purpose,
            parameters=parameters,
            constraints=constraints,
            design=design,
            replicas=replicas,
            seed_algorithm=str(seed["algorithm"]),
            seed_base=seed["base"],
            seed_bank_id=seed["bank_id"],
            event_counts=_freeze(event_counts_resolved),
            physics=_freeze(physics),
            analysis=analysis,
            search=search,
            build=build,
            provenance=_freeze(provenance),
            spec_key="",
        )
        spec_key = canonical_hash("experiment-spec/v1", provisional.to_manifest())
        return cls(**{**provisional.__dict__, "spec_key": spec_key})


@dataclass(frozen=True)
class ResolvedExperiment:
    experiment_id: str
    purpose: str
    parameter_layers: Tuple[ResolvedParameter, ...]
    constraints: Tuple[JointConstraint, ...]
    sampling: SamplingPlan
    event_counts: Mapping[str, Any]
    physics: Mapping[str, Any]
    analysis: Mapping[str, Any]
    search: Mapping[str, Any]
    build: Mapping[str, Any]
    provenance: Mapping[str, Any]
    catalog_key: str
    experiment_spec_key: str

    @property
    def manifest_key(self) -> str:
        return canonical_hash("resolved-experiment/v1", self.to_manifest(include_key=False))

    def value(self, name: str) -> Any:
        for layer in self.parameter_layers:
            if layer.definition.name == name:
                return layer.value
        raise ConfigError(f"resolved experiment has no parameter {name!r}")

    def to_manifest(self, *, include_key: bool = True) -> Mapping[str, Any]:
        out = {
            "schema": RESOLVED_SCHEMA,
            "id": self.experiment_id,
            "purpose": self.purpose,
            "parameter_layers": {
                layer.definition.name: layer.to_manifest() for layer in self.parameter_layers
            },
            "constraints": [constraint.to_manifest() for constraint in self.constraints],
            "sampling": self.sampling.to_manifest(),
            "event_counts": _thaw(self.event_counts),
            "physics": _thaw(self.physics),
            "analysis": _thaw(self.analysis),
            "search": _thaw(self.search),
            "build": _thaw(self.build),
            "provenance": _thaw(self.provenance),
            "catalog_key": self.catalog_key,
            "experiment_spec_key": self.experiment_spec_key,
        }
        if include_key:
            out["manifest_key"] = canonical_hash("resolved-experiment/v1", out)
        return out


def load_parameter_catalog(source: Any) -> ParameterCatalog:
    document, _ = _load_document(source)
    return ParameterCatalog.from_mapping(document)


def load_experiment_spec(source: Any, catalog: ParameterCatalog) -> ExperimentSpec:
    document, base_dir = _load_document(source)
    return ExperimentSpec.from_mapping(document, catalog, base_dir=base_dir)


def _resolved_value(raw: Any, name: str) -> Tuple[Any, str, Any]:
    if isinstance(raw, Mapping):
        _reject_unknown(raw, ("value", "origin", "source"), f"resolved parameter {name!r}")
        if "value" not in raw:
            raise ConfigError(f"resolved parameter {name!r} needs value")
        origin = raw.get("origin", "proposal")
        source = raw.get("source", "optimizer proposal")
        value = raw["value"]
    else:
        value, origin, source = raw, "proposal", "optimizer proposal"
    if origin not in ("proposal", "material"):
        raise ConfigError(f"search parameter {name!r} origin must be proposal or material")
    if origin == "material" and (source is None or source == ""):
        raise ConfigError(f"material value for {name!r} needs source provenance")
    return value, origin, source


def resolve_experiment(
    catalog: ParameterCatalog,
    spec: ExperimentSpec,
    resolved_values: Mapping[str, Any],
) -> ResolvedExperiment:
    """Resolve a source experiment once; the returned object cannot be mutated."""

    resolved_values = _mapping(resolved_values, "resolved values")
    physics_values: Mapping[str, Any] = {}
    if "parameters" in resolved_values or "physics" in resolved_values:
        _reject_unknown(resolved_values, ("parameters", "physics"), "resolved values envelope")
        physics_values = _mapping(resolved_values.get("physics", {}), "resolved physics values")
        resolved_values = _mapping(
            resolved_values.get("parameters", {}), "resolved parameter values"
        )
    declared = {parameter.name for parameter in spec.parameters}
    unknown = sorted(set(resolved_values) - declared)
    if unknown:
        raise ConfigError(f"resolved values contain undeclared parameter(s) {unknown}")
    layers = []
    for experiment_parameter in spec.parameters:
        definition = catalog.get(experiment_parameter.name)
        name = definition.name
        if experiment_parameter.mode == "fixed":
            if name in resolved_values:
                raise ConfigError(
                    f"fixed scientific parameter {name!r} cannot be overridden at resolution"
                )
            value = experiment_parameter.fixed_value
            origin, source = "fixed", "experiment specification"
        else:
            if name not in resolved_values:
                raise ConfigError(f"search parameter {name!r} has no resolved value")
            raw_value, origin, source = _resolved_value(resolved_values[name], name)
            value = definition.normalize_value(raw_value)
            # A real material is a measured fact and is checked against physical
            # validity, not clipped to an optimizer's box.  Its origin and source
            # remain explicit.  Proposals must remain inside this experiment's box.
            if origin == "proposal" and not (
                experiment_parameter.lower <= value <= experiment_parameter.upper
            ):
                raise ConfigError(
                    f"proposal {name}={value:g} {definition.unit} is outside this "
                    f"experiment's search bounds [{experiment_parameter.lower:g}, "
                    f"{experiment_parameter.upper:g}]"
                )
        layers.append(
            ResolvedParameter(
                definition=definition,
                experiment=experiment_parameter,
                value=_freeze(value),
                origin=origin,
                source=_freeze(source),
            )
        )

    values = {layer.definition.name: layer.value for layer in layers}
    failed_constraints = [
        constraint.name for constraint in spec.constraints if not constraint.evaluate(values)
    ]
    if failed_constraints:
        raise ConfigError(f"resolved values violate joint constraint(s) {failed_constraints}")

    try:
        sampling = build_recorded_plan(
            spec.design,
            replicas=spec.replicas,
            events_per_task=int(spec.event_counts["events_per_task"]),
            seed_base=spec.seed_base,
            seed_bank_id=spec.seed_bank_id,
            seed_algorithm=spec.seed_algorithm,
        )
    except SamplingError as exc:
        raise ConfigError(str(exc)) from exc
    physics = dict(_thaw(spec.physics))
    permitted_physics = physics.pop("resolvable", [])
    if not isinstance(permitted_physics, Sequence) or isinstance(permitted_physics, (str, bytes)):
        raise ConfigError("physics.resolvable must be a list of field names")
    unknown_physics = sorted(set(physics_values) - set(permitted_physics))
    if unknown_physics:
        raise ConfigError(f"resolved physics values contain undeclared field(s) {unknown_physics}")
    if "miller" in physics_values:
        miller = physics_values["miller"]
        if (
            not isinstance(miller, Sequence)
            or isinstance(miller, (str, bytes))
            or len(miller) != 3
            or any(not isinstance(value, int) or isinstance(value, bool) for value in miller)
            or all(value == 0 for value in miller)
        ):
            raise ConfigError("resolved physics miller must be three integers, not all zero")
    if "realization" in physics_values:
        realization = _mapping(physics_values["realization"], "resolved realization", nonempty=True)
        required = {"schema", "mode", "substrate_carrier", "base_lattice_map", "lattice_map"}
        missing = sorted(required - set(realization))
        if missing:
            raise ConfigError(f"resolved realization is missing {missing}")
        if realization["schema"] != "stage4_realization_v1":
            raise ConfigError("resolved realization has an unsupported schema")
        if realization["mode"] not in ("pseudo_si_base", "native_g4cmp", "custom_material"):
            raise ConfigError("resolved realization has an unsupported mode")
        if any(not isinstance(realization[name], str) or not realization[name]
               for name in ("substrate_carrier", "base_lattice_map", "lattice_map")):
            raise ConfigError("resolved realization names must be non-empty strings")
    physics.update(_thaw(physics_values))

    return ResolvedExperiment(
        experiment_id=spec.experiment_id,
        purpose=spec.purpose,
        parameter_layers=tuple(layers),
        constraints=spec.constraints,
        sampling=sampling,
        event_counts=spec.event_counts,
        physics=_freeze(physics),
        analysis=spec.analysis,
        search=spec.search,
        build=spec.build,
        provenance=spec.provenance,
        catalog_key=catalog.catalog_key,
        experiment_spec_key=spec.spec_key,
    )


def load_resolved_experiment(source: Any) -> ResolvedExperiment:
    """Load and verify an immutable, self-contained resolved manifest."""

    document, _ = _load_document(source)
    document = _mapping(document, "resolved experiment")
    allowed = (
        "schema", "id", "purpose", "parameter_layers", "constraints", "sampling", "event_counts",
        "physics", "analysis", "search", "build", "provenance", "catalog_key",
        "experiment_spec_key", "manifest_key",
    )
    _reject_unknown(document, allowed, "resolved experiment")
    if document.get("schema") != RESOLVED_SCHEMA:
        raise ConfigError(f"resolved experiment schema must be {RESOLVED_SCHEMA!r}")
    stored_key = _full_hash(document.get("manifest_key"), "manifest_key")
    without_key = {key: value for key, value in document.items() if key != "manifest_key"}
    calculated = canonical_hash("resolved-experiment/v1", without_key)
    if stored_key != calculated:
        raise ConfigError(
            f"resolved experiment manifest hash mismatch: stored {stored_key}, "
            f"calculated {calculated}"
        )

    raw_layers = _mapping(document.get("parameter_layers"), "parameter_layers", nonempty=True)
    layers = []
    for name in sorted(raw_layers):
        entry = _mapping(raw_layers[name], f"parameter layer {name!r}")
        _reject_unknown(entry, ("definition", "experiment", "resolved"),
                        f"parameter layer {name!r}")
        definition_raw = dict(_mapping(entry.get("definition"), "definition"))
        if definition_raw.pop("name", None) != name:
            raise ConfigError(f"parameter layer key {name!r} disagrees with definition name")
        definition = ParameterDefinition.from_mapping(name, definition_raw)
        experiment_raw = dict(_mapping(entry.get("experiment"), "experiment parameter"))
        if experiment_raw.pop("name", None) != name:
            raise ConfigError(f"parameter layer key {name!r} disagrees with experiment name")
        experiment = ExperimentParameter.from_mapping(definition, experiment_raw)
        resolved = _mapping(entry.get("resolved"), "resolved parameter")
        _reject_unknown(resolved, ("value", "origin", "source"), "resolved parameter")
        if set(resolved) != {"value", "origin", "source"}:
            raise ConfigError(f"resolved parameter {name!r} needs value, origin, and source")
        value = definition.normalize_value(resolved["value"])
        origin = resolved["origin"]
        if origin not in ("fixed", "proposal", "material"):
            raise ConfigError(f"resolved parameter {name!r} has invalid origin {origin!r}")
        if experiment.mode == "fixed" and (origin != "fixed" or value != experiment.fixed_value):
            raise ConfigError(f"fixed parameter {name!r} was altered in the resolved manifest")
        if experiment.mode == "search" and origin == "fixed":
            raise ConfigError(f"search parameter {name!r} cannot have fixed origin")
        if origin == "material" and (resolved["source"] is None or resolved["source"] == ""):
            raise ConfigError(f"material parameter {name!r} needs source provenance")
        if experiment.mode == "search" and origin == "proposal" and not (
            experiment.lower <= value <= experiment.upper
        ):
            raise ConfigError(f"proposal parameter {name!r} is outside its search bounds")
        layers.append(
            ResolvedParameter(
                definition, experiment, _freeze(value), str(origin), _freeze(resolved["source"])
            )
        )
    raw_constraints = document.get("constraints", ())
    if not isinstance(raw_constraints, Sequence) or isinstance(raw_constraints, (str, bytes)):
        raise ConfigError("resolved constraints must be a sequence")
    constraints = tuple(
        JointConstraint.from_mapping(item, tuple(raw_layers)) for item in raw_constraints
    )
    values = {layer.definition.name: layer.value for layer in layers}
    failed_constraints = [
        constraint.name for constraint in constraints if not constraint.evaluate(values)
    ]
    if failed_constraints:
        raise ConfigError(
            f"resolved manifest violates joint constraint(s) {failed_constraints}"
        )
    try:
        sampling = SamplingPlan.from_manifest(document.get("sampling", {}))
    except SamplingError as exc:
        raise ConfigError(str(exc)) from exc
    event_counts = _mapping(document.get("event_counts"), "event_counts", nonempty=True)
    expected_total = len(sampling.tasks) * sampling.events_per_task
    if event_counts.get("events_per_task") != sampling.events_per_task:
        raise ConfigError("resolved event_counts events_per_task disagrees with its task plan")
    if event_counts.get("events_total") != expected_total:
        raise ConfigError(
            f"resolved event_counts events_total disagrees with its {len(sampling.tasks)} tasks"
        )
    gun_energy = event_counts.get("gun_energy_eV")
    if gun_energy is not None and (
            not isinstance(gun_energy, (int, float)) or isinstance(gun_energy, bool)
            or not math.isfinite(float(gun_energy)) or float(gun_energy) <= 0.0):
        raise ConfigError("resolved event_counts gun_energy_eV must be positive and finite")
    experiment = ResolvedExperiment(
        experiment_id=str(document.get("id", "")),
        purpose=str(document.get("purpose", "")),
        parameter_layers=tuple(layers),
        constraints=constraints,
        sampling=sampling,
        event_counts=_freeze(event_counts),
        physics=_freeze(_mapping(document.get("physics"), "physics", nonempty=True)),
        analysis=_validated_analysis(document.get("analysis")),
        search=_validated_search(document.get("search", {})),
        build=_validated_build(document.get("build")),
        provenance=_freeze(_mapping(document.get("provenance", {}), "provenance")),
        catalog_key=_full_hash(document.get("catalog_key"), "catalog_key"),
        experiment_spec_key=_full_hash(
            document.get("experiment_spec_key"), "experiment_spec_key"
        ),
    )
    if not experiment.experiment_id or not experiment.purpose:
        raise ConfigError("resolved experiment needs non-empty id and purpose")
    if experiment.manifest_key != stored_key:
        raise ConfigError("resolved experiment changed while being normalized")
    return experiment


def write_resolved_experiment(path: Any, experiment: ResolvedExperiment) -> Path:
    """Publish a complete manifest atomically without overwriting another one.

    Repeating the write with the identical manifest is idempotent.  If the path
    already names different content it is refused.  A hard-link publish is used
    because ordinary ``os.replace`` would silently overwrite the old definition.
    """

    destination = Path(path)
    payload = json.dumps(
        experiment.to_manifest(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False
    ) + "\n"
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".partial"
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            try:
                existing = load_resolved_experiment(destination)
            except ConfigError as exc:
                raise ConfigError(
                    f"refusing to overwrite unreadable immutable manifest {destination}: {exc}"
                ) from exc
            if existing.manifest_key != experiment.manifest_key:
                raise ConfigError(
                    f"refusing to overwrite different immutable manifest {destination}"
                )
        # Make the new directory entry durable where the platform supports it.
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except OSError as exc:
        raise ConfigError(f"cannot publish immutable manifest {destination}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return destination
