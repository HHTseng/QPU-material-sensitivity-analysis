"""Small, fail-closed core for reproducible material-scan experiments.

The legacy Stage 3/4 runtime remains untouched while this package is validated.
This first slice only resolves immutable experiment inputs and identities; it
does not launch Geant4 or open a production ledger.
"""

from .config import (
    ConfigError,
    ExperimentSpec,
    JointConstraint,
    ParameterCatalog,
    ParameterDefinition,
    ResolvedExperiment,
    ResolvedParameter,
    load_experiment_spec,
    load_parameter_catalog,
    load_resolved_experiment,
    resolve_experiment,
    write_resolved_experiment,
)
from .identity import (
    IdentityError,
    analysis_key,
    canonical_hash,
    canonical_json,
    search_key,
    simulation_key,
    task_key,
)
from .sampling import (
    RecordedStratifiedDesign,
    SampleTask,
    SamplingError,
    SamplingPlan,
    build_recorded_plan,
    legacy_design_hash,
    legacy_position_seed,
    load_recorded_stratified_design,
)


__all__ = [
    "ConfigError",
    "ExperimentSpec",
    "IdentityError",
    "JointConstraint",
    "ParameterCatalog",
    "ParameterDefinition",
    "RecordedStratifiedDesign",
    "ResolvedExperiment",
    "ResolvedParameter",
    "SampleTask",
    "SamplingError",
    "SamplingPlan",
    "analysis_key",
    "build_recorded_plan",
    "canonical_hash",
    "canonical_json",
    "legacy_design_hash",
    "legacy_position_seed",
    "load_experiment_spec",
    "load_parameter_catalog",
    "load_recorded_stratified_design",
    "load_resolved_experiment",
    "resolve_experiment",
    "search_key",
    "simulation_key",
    "task_key",
    "write_resolved_experiment",
]
