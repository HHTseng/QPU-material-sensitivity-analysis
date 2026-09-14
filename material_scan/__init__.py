"""Validated core for reproducible material-property experiments.

The package resolves complete experiment inputs, records stable identities,
checks earlier results, and analyzes completed simulations. Direct Geant4
launch remains disabled until macro and lattice rendering match the checked
compatibility implementation.
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
    historical_design_hash,
    historical_position_seed,
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
    "historical_design_hash",
    "historical_position_seed",
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
