"""Experiment-local numerical space used by every search method.

The parameter catalog states broad physical validity.  This module uses the
fixed values and search intervals from one experiment; it never imports the
older global search box.  The retained numerical optimizers need only the
small interface implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import numpy as np

from .config import ConfigError, ExperimentSpec, ResolvedExperiment


POLAR = "orientation_polar_cos"
AZIMUTH = "orientation_azimuth_turns"
ORIENTATION_INTEGER_SCALE = 1_000_000


def sphere_to_miller(
    polar_cos: float,
    azimuth_turns: float,
    scale: int = ORIENTATION_INTEGER_SCALE,
) -> list[int]:
    """Map a point on the unit sphere to a primitive integer direction."""

    z = float(polar_cos)
    turn = float(azimuth_turns)
    if not math.isfinite(z) or not -1.0 <= z <= 1.0:
        raise ConfigError("orientation_polar_cos must be finite and in [-1, 1]")
    if not math.isfinite(turn):
        raise ConfigError("orientation_azimuth_turns must be finite")
    turn %= 1.0
    radius = math.sqrt(max(0.0, 1.0 - z * z))
    phi = 2.0 * math.pi * turn
    integer = [
        int(round(scale * radius * math.cos(phi))),
        int(round(scale * radius * math.sin(phi))),
        int(round(scale * z)),
    ]
    if integer == [0, 0, 0]:
        raise ConfigError("sphere direction quantised to zero")
    divisor = math.gcd(math.gcd(abs(integer[0]), abs(integer[1])), abs(integer[2]))
    return [value // max(1, divisor) for value in integer]


@dataclass(frozen=True)
class SearchVariable:
    name: str
    low: float
    high: float
    baseline: float
    scale: str

    def to_unit(self, value: float) -> float:
        value = float(value)
        if self.scale == "log":
            return (math.log(value) - math.log(self.low)) / (
                math.log(self.high) - math.log(self.low)
            )
        return (value - self.low) / (self.high - self.low)

    def from_unit(self, value: float) -> float:
        value = min(1.0, max(0.0, float(value)))
        # Optimizers often land exactly on a box face.  Computing a log-scaled
        # endpoint through exp(log(x)) can round one ulp outside the declared
        # interval, so preserve both endpoints exactly and clamp the interior.
        if value == 0.0:
            return self.low
        if value == 1.0:
            return self.high
        if self.scale == "log":
            result = math.exp(
                math.log(self.low) + value * (math.log(self.high) - math.log(self.low))
            )
        else:
            result = self.low + value * (self.high - self.low)
        return min(self.high, max(self.low, result))


class ExperimentSpace:
    """The exact fixed values, transforms, bounds, and constraints in one spec."""

    def __init__(self, spec: ExperimentSpec):
        self.spec = spec
        self.fixed = {
            parameter.name: parameter.fixed_value
            for parameter in spec.parameters
            if parameter.mode == "fixed"
        }
        self.variables = [
            SearchVariable(
                parameter.name,
                float(parameter.lower),
                float(parameter.upper),
                float(parameter.default_value),
                str(parameter.scale),
            )
            for parameter in spec.parameters
            if parameter.mode == "search"
        ]
        self.names = [variable.name for variable in self.variables]
        self.n_cont = len(self.variables)
        if (POLAR in self.names) != (AZIMUTH in self.names):
            raise ConfigError("the two continuous crystal-direction coordinates must vary together")
        self.orientation_columns = (
            (self.names.index(POLAR), self.names.index(AZIMUTH))
            if POLAR in self.names
            else None
        )
        # The retained optimizer calls this attribute for the direct gap rule.
        gap = next((item for item in self.variables if item.name == "topfilm_gap"), None)
        self.constraints = ({"topfilm_gap_min_eV": gap.low} if gap is not None else {})

    def baseline_point(self) -> dict[str, Any]:
        point = dict(self.fixed)
        point.update({variable.name: variable.baseline for variable in self.variables})
        return self.complete(point)

    def complete(self, point: Mapping[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(point) - set(self.fixed) - set(self.names) - {"miller", "_realization"})
        if unknown:
            raise ConfigError(f"proposal has undeclared parameter(s) {unknown}")
        out = dict(self.fixed)
        out.update({variable.name: float(point.get(variable.name, variable.baseline))
                    for variable in self.variables})
        if AZIMUTH in out:
            out[AZIMUTH] %= 1.0
        if POLAR in out and AZIMUTH in out:
            out["miller"] = sphere_to_miller(out[POLAR], out[AZIMUTH])
        elif "miller" in point:
            out["miller"] = [int(value) for value in point["miller"]]
        if "_realization" in point:
            out["_realization"] = dict(point["_realization"])
        return out

    def to_unit(self, point: Mapping[str, Any]) -> np.ndarray:
        complete = self.complete(point)
        return np.asarray([variable.to_unit(complete[variable.name])
                           for variable in self.variables], dtype=float)

    def from_unit(self, values: Sequence[float], base: Mapping[str, Any] | None = None) -> dict[str, Any]:
        values = np.asarray(values, dtype=float).ravel()
        if values.size != self.n_cont:
            raise ConfigError(f"unit vector has {values.size} values; expected {self.n_cont}")
        point = self.baseline_point() if base is None else self.complete(base)
        for variable, value in zip(self.variables, values):
            point[variable.name] = variable.from_unit(float(value))
        return self.complete(point)

    def sample(self, rng: np.random.Generator, n: int = 1) -> list[dict[str, Any]]:
        return [self.from_unit(row) for row in rng.random((n, self.n_cont))]

    def in_bounds(self, point: Mapping[str, Any], exempt: Sequence[str] = ()) -> tuple[bool, list[str]]:
        complete = self.complete(point)
        exempted = set(exempt)
        bad = [
            f"{variable.name}={complete[variable.name]:g} outside "
            f"[{variable.low:g}, {variable.high:g}]"
            for variable in self.variables
            if variable.name not in exempted
            and not variable.low - 1e-12 <= complete[variable.name] <= variable.high + 1e-12
        ]
        for constraint in self.spec.constraints:
            if not constraint.evaluate(complete):
                bad.append(f"joint constraint {constraint.name!r} failed")
        return not bad, bad

    def to_manifest(self) -> dict[str, Any]:
        return {
            "fixed": dict(self.fixed),
            "search": [
                {
                    "name": variable.name,
                    "low": variable.low,
                    "high": variable.high,
                    "default": variable.baseline,
                    "scale": variable.scale,
                }
                for variable in self.variables
            ],
            "constraints": [constraint.to_manifest() for constraint in self.spec.constraints],
            "orientation_columns": self.orientation_columns,
        }


def values_from_resolved(experiment: ResolvedExperiment) -> dict[str, Any]:
    values = {layer.definition.name: layer.value for layer in experiment.parameter_layers}
    if "miller" in experiment.physics:
        values["miller"] = [int(value) for value in experiment.physics["miller"]]
    elif POLAR in values and AZIMUTH in values:
        values["miller"] = sphere_to_miller(values[POLAR], values[AZIMUTH])
    realization = experiment.physics.get("realization")
    if realization:
        values["_realization"] = dict(realization)
    return values


def exact_physics_check(values: Mapping[str, Any], experiment: ResolvedExperiment) -> Mapping[str, Any]:
    """Run the full material derivation before a Geant4 process may start."""

    if float(values["bot_gap_thres"]) > 2.0 * float(experiment.physics["junction_gap_eV"]):
        raise ConfigError("bottom-film threshold exceeds twice the junction gap")
    if 2.0 * float(values["topfilm_gap"]) > float(experiment.event_counts["gun_energy_eV"]):
        raise ConfigError("twice the upper-film gap exceeds the source-phonon energy")
    legacy_dir = Path(__file__).resolve().parents[1] / "legacy"
    if str(legacy_dir) not in sys.path:
        sys.path.insert(0, str(legacy_dir))
    import stage4_space  # type: ignore

    realization = values.get("_realization") or experiment.physics.get("realization") or {}
    if realization.get("mode", "pseudo_si_base") != "pseudo_si_base":
        raise ConfigError(
            "the current renderer supports pseudo_si_base only; native and custom "
            "material records need their own tested copy path"
        )
    carrier = realization.get("substrate_carrier", "G4_Si")
    if carrier not in stage4_space.SUBSTRATE_CARRIERS:
        raise ConfigError(f"no measured Geant4 density is recorded for {carrier!r}")
    carrier_density = float(stage4_space.SUBSTRATE_CARRIERS[carrier])
    if realization.get("material_density_kg_m3") is not None:
        material_density = float(realization["material_density_kg_m3"])
        tolerance = float(realization.get("density_tolerance", 0.03))
        deviation = abs(carrier_density - material_density) / material_density
        if deviation > tolerance:
            raise ConfigError(
                f"{carrier} density {carrier_density:g} kg/m3 differs from the "
                f"material density {material_density:g} kg/m3 by {deviation:.1%}, "
                f"above the declared {tolerance:.1%} limit"
            )
    derived = dict(stage4_space.derive(values, carrier=carrier))
    derived["g4_material_name"] = carrier
    derived["lattice_map_name"] = realization.get("lattice_map", "PseudoCubic")
    derived["base_lattice_map"] = realization.get("base_lattice_map", "Si")
    derived["expected_density_kg_m3"] = carrier_density
    return derived
