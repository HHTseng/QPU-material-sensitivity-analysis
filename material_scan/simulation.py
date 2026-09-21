"""Render, run, and score one fully resolved material experiment.

This is the only current-code path that starts Geant4.  It deliberately keeps
the sequence linear: verify files, derive physics, render one lattice and all
task macros, run tasks, score every hit file, then calculate one result.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Mapping, Sequence

from .analysis import Block, StratifiedDesign, stratified_estimate
from .config import ConfigError, ResolvedExperiment, load_resolved_experiment
from .identity import analysis_key, canonical_hash, simulation_key, task_key
from .physics import score_hit_file
from .runner import (
    RunError,
    inspect_artifacts,
    normalized_macro_bytes,
    run_task,
    sha256_file,
    validate_macro_lines,
)
from .space import exact_physics_check, values_from_resolved
from .store import Store


ROOT = Path(__file__).resolve().parents[1]
_SYSTEM = os.environ.get("G4SYSTEM", "Linux-g++")
_WORK_DIRECTORY = Path(os.environ.get("G4WORKDIR", str(Path.home() / "geant4_workdir")))
_G4CMP_ROOT = Path(
    os.environ.get(
        "SENSITIVITY_G4CMP_ROOT",
        os.environ.get("G4CMPINSTALL", str(Path.home() / "src/G4CMP_htseng")),
    )
)
DEFAULT_EXECUTABLE = _WORK_DIRECTORY / "bin" / _SYSTEM / "Main"
DEFAULT_LIBRARY_DIRECTORY = _WORK_DIRECTORY / "lib" / _SYSTEM
DEFAULT_CRYSTAL_MAPS = Path(
    os.environ.get("SENSITIVITY_G4CMP_CRYSTALMAPS", str(_G4CMP_ROOT / "CrystalMaps"))
)
DEFAULT_TEMPLATE = ROOT / "legacy/macros/sensitivity_template_beamOn1e6.mac"
_CONTROLLER_TTL_S = 300.0
_CONTROLLER_RENEW_INTERVAL_S = 60.0


class SimulationError(RuntimeError):
    """A resolved experiment cannot be rendered, run, or scored safely."""


def _reusable_tasks(directories: Sequence[str | Path]) -> dict[str, tuple[Path, str]]:
    """Index complete, valid task artifacts from explicitly named results."""

    indexed: dict[str, tuple[Path, str]] = {}
    for raw_directory in directories:
        directory = Path(raw_directory).resolve()
        database = directory / "results.sqlite"
        if not database.is_file():
            raise SimulationError(f"reuse source has no results.sqlite: {directory}")
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SimulationError(f"reuse source failed SQLite integrity: {directory}")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise SimulationError(f"reuse source has foreign-key violations: {directory}")
            rows = connection.execute(
                "SELECT t.task_id,a.artifact_sha256 FROM task AS t "
                "JOIN attempt AS a ON a.task_id=t.task_id "
                "WHERE t.completeness_state='complete' AND t.validity_state='valid' "
                "AND a.completeness_state='complete' AND a.validity_state='valid'"
            ).fetchall()
        except sqlite3.DatabaseError as error:
            raise SimulationError(f"cannot read reuse source {database}: {error}") from error
        finally:
            connection.close()
        for identity, artifact_sha256 in rows:
            previous = indexed.get(str(identity))
            item = (directory, str(artifact_sha256))
            if previous is not None and previous[1] != item[1]:
                raise SimulationError(f"task {identity} has conflicting reuse sources")
            if previous is None:
                indexed[str(identity)] = item
    return indexed


def _link_or_copy(source: Path, destination: Path) -> None:
    """Publish an immutable existing file without exposing a partial copy."""

    if not source.is_file():
        raise SimulationError(f"reuse artifact is absent: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(source) != sha256_file(destination):
            raise SimulationError(f"existing reused artifact differs: {destination}")
        return
    temporary = destination.with_name(f".{destination.name}.reuse-{os.getpid()}")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _replace(
    lines: list[str],
    command: str,
    value: str,
    *,
    commented: bool = False,
    append: bool = False,
) -> None:
    key = command.strip()

    def matches(text: str) -> bool:
        return text.startswith(key) and (len(text) == len(key) or text[len(key)].isspace())

    for index, raw in enumerate(lines):
        stripped = raw.lstrip()
        if not stripped.startswith("#") and matches(stripped):
            lines[index] = value + "\n"
            return
    if commented:
        for index, raw in enumerate(lines):
            stripped = raw.lstrip()
            if stripped.startswith("#") and matches(stripped[1:].lstrip()):
                lines[index] = value + "\n"
                return
    if append:
        lines.append(value + "\n")
        return
    raise SimulationError(f"required command is absent from template: {key}")


def render_lattice(
    values: Mapping[str, Any], derived: Mapping[str, Any], destination: Path
) -> bytes:
    """Render the pseudo-material lattice from its named base record."""

    base = str(derived["base_lattice_map"])
    source = DEFAULT_CRYSTAL_MAPS / base / "config.txt"
    if not source.is_file():
        raise SimulationError(f"base lattice record does not exist: {source}")
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    replacements = {
        "cubic ": (values["sub_lattice_a"], " Ang"),
        "stiffness 1 1 ": (values["sub_c11"], " GPa"),
        "stiffness 1 2 ": (values["sub_c12"], " GPa"),
        "stiffness 4 4 ": (values["sub_c44"], " GPa"),
        "scat ": (values["sub_scat"], " s3"),
        "decay ": (values["sub_decay"], " s4"),
        "decayTT ": (values["sub_decayTT"], ""),
        "vsound ": (derived["vsound_m_s"], " m/s"),
        "vtrans ": (derived["vtrans_m_s"], " m/s"),
    }
    for command, (number, unit) in replacements.items():
        _replace(lines, command, f"{command} {number}{unit}", append=True)
    changed = ", ".join(sorted(key.strip() for key in replacements if key not in ("vsound ", "vtrans ")))
    header = [
        f"# GENERATED pseudo-material: base record {base!r} with the scanned\n",
        f"# fields overridden ({changed}).\n",
        "# Unlisted fields -- dyn, LDOS/STDOS/FTDOS, Debye, the charge-carrier\n",
        f"# block -- remain {base}'s. This is not a from-scratch crystal.\n",
    ]
    data = "".join(header + lines).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != data:
        raise SimulationError(f"refusing to replace a different lattice: {destination}")
    if not destination.exists():
        destination.write_bytes(data)
    return data


def render_macro(
    experiment: ResolvedExperiment,
    values: Mapping[str, Any],
    derived: Mapping[str, Any],
    task: Any,
    *,
    hit_path: Path,
    witness_path: Path,
) -> str:
    lines = DEFAULT_TEMPLATE.read_text(encoding="utf-8").splitlines(keepends=True)
    physics = experiment.physics
    settings = {
        "/main/detector_param/setSubstrateG4Name": derived["g4_material_name"],
        "/main/detector_param/setSubstrateName": derived["lattice_map_name"],
        "/main/gun/setEnergy": f"{experiment.event_counts['gun_energy_eV']} eV",
        "/main/detector_param/setTopAbs": derived["setTopAbs"],
        "/main/detector_param/setTopFilmAbs": derived["setTopFilmAbs"],
        "/main/detector_param/setBotAbs": derived["setBotAbs"],
        "/main/detector_param/setTopFilmVSound": values["topfilm_vsound"],
        "/main/detector_param/setTopFilmGap": values["topfilm_gap"],
        "/main/detector_param/setTopFilmPhLifetime": values["topfilm_ph_lifetime"],
        "/main/detector_param/setBotVSound": values["bot_vsound"],
        "/main/detector_param/setBotGapThres": values["bot_gap_thres"],
        "/main/detector_param/setBotPhLifetime": values["bot_ph_lifetime"],
        "/g4cmp/temperature": f"{values['temperature']} K",
        "/main/detector_param/setLatticeDeg": values["lattice_deg"],
        "/main/detector_param/setMiller": " ".join(str(number) for number in values["miller"]),
        "/main/electrode_param/setXLocations": ", ".join(
            f"{float(value):.6f}" for value in physics["electrode_x_mm"]
        ),
        "/main/electrode_param/setYLocations": ", ".join(
            f"{float(value):.6f}" for value in physics["electrode_y_mm"]
        ),
        "/main/gun/setPosition": "{} {} {} mm".format(*task.site_mm),
        "/g4cmp/HitsFile": str(hit_path),
    }
    commented = {
        "/main/detector_param/setSubstrateG4Name",
        "/main/detector_param/setSubstrateName",
    }
    for command, value in settings.items():
        _replace(lines, command, f"{command} {value}", commented=command in commented)
    beam = (
        f"/random/setSeeds {task.seeds[0]} {task.seeds[1]}\n"
        f"/run/beamOn {task.events}\n"
        f"/control/shell touch {witness_path}"
    )
    _replace(lines, "/run/beamOn", beam)
    offenders = validate_macro_lines(lines)
    if offenders:
        raise SimulationError(f"rendered macro has malformed numeric tokens: {offenders[:3]}")
    return "".join(lines)


def verify_runtime(experiment: ResolvedExperiment) -> tuple[Path, dict[str, str]]:
    if experiment.build.get("mode") != "verified":
        raise SimulationError("only build.mode=verified may start Geant4")
    executable = Path(os.environ.get("MATERIAL_SCAN_EXECUTABLE", DEFAULT_EXECUTABLE))
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SimulationError(f"executable is absent or not executable: {executable}")
    expected_executable = str(experiment.build["executable_sha256"])
    if sha256_file(executable) != expected_executable:
        raise SimulationError("executable checksum differs from the resolved experiment")
    paths = {
        "libMain.so": DEFAULT_LIBRARY_DIRECTORY / "libMain.so",
        "libG4cmp.so": DEFAULT_LIBRARY_DIRECTORY / "libG4cmp.so",
        "sensitivity_template_beamOn1e6.mac": DEFAULT_TEMPLATE,
        "Si/config.txt": DEFAULT_CRYSTAL_MAPS / "Si/config.txt",
    }
    expected = dict(experiment.build["runtime_input_sha256"])
    missing = sorted(set(expected) - set(paths))
    if missing:
        raise SimulationError(f"no current path is defined for runtime input(s) {missing}")
    observed: dict[str, str] = {}
    for name, digest in expected.items():
        path = paths[name]
        if not path.is_file():
            raise SimulationError(f"runtime input is absent: {path}")
        observed[name] = sha256_file(path)
        if observed[name] != digest:
            raise SimulationError(f"runtime input checksum differs for {name}")
    return executable, observed


def _material_log_check(path: Path, name: str, density_kg_m3: float) -> None:
    observed_name = None
    observed_density = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Material:" not in line or "density:" not in line:
            continue
        tokens = line.split()
        try:
            observed_name = tokens[tokens.index("Material:") + 1]
            observed_density = float(tokens[tokens.index("density:") + 1]) * 1000.0
            break
        except (ValueError, IndexError):
            continue
    if observed_name != name or observed_density is None:
        raise SimulationError(f"run log did not confirm material {name}")
    relative = abs(observed_density - density_kg_m3) / density_kg_m3
    if relative > 0.005:
        raise SimulationError(
            f"run log density {observed_density:g} kg/m3 differs from "
            f"resolved {density_kg_m3:g} kg/m3"
        )


def _task_paths(directory: Path, task: Any) -> dict[str, Path]:
    name = f"r{task.replica:02d}_p{task.position:04d}"
    return {
        "macro": directory / "macros" / f"{name}.mac",
        "partial": directory / "hits" / f"{name}.txt.partial",
        "hit": directory / "hits" / f"{name}.txt",
        "witness": directory / "hits" / f"{name}.done",
        "log": directory / "logs" / f"{name}.log",
        "score": directory / "scores" / f"{name}.json",
    }


def _score(path: Path, experiment: ResolvedExperiment, task: Any) -> dict[str, Any]:
    physics = experiment.physics
    thickness_um = float(physics.get("substrate_thickness_um", 525.0))
    scored = score_hit_file(
        path,
        float(physics["junction_gap_eV"]),
        physics["electrode_x_mm"],
        physics["electrode_y_mm"],
        thickness_um * 1.0e-6 / 2.0,
    )
    return {
        "position": task.position,
        "replica": task.replica,
        "events": task.events,
        "n_hits": scored.n_hits,
        "total_qps": scored.total_qps,
        "per_electrode_qps": list(scored.per_electrode_qps),
        "hit_sha256": sha256_file(path),
    }


def _design(experiment: ResolvedExperiment) -> StratifiedDesign:
    source = experiment.sampling.design.to_manifest()
    return StratifiedDesign.from_values(
        position_strata=source["stratum"],
        stratum_weights=source["stratum_weights"],
        replica_ids=range(experiment.sampling.replicas),
        events_per_task=experiment.sampling.events_per_task,
        gun_energy_eV=float(experiment.event_counts["gun_energy_eV"]),
        electrode_weights=source["electrode_weights"],
        design_hash=source["design_hash"],
        injection_law=source["injection_law"],
    )


def run_resolved(
    resolved_path: str | Path,
    output: str | Path,
    *,
    workers: int = 1,
    timeout_s: float = 0.0,
    reuse_from: Sequence[str | Path] = (),
) -> Mapping[str, Any]:
    """Execute all declared tasks and return the final result mapping."""

    if workers <= 0:
        raise SimulationError("workers must be positive")
    experiment = load_resolved_experiment(resolved_path)
    executable, runtime_hashes = verify_runtime(experiment)
    values = values_from_resolved(experiment)
    derived = exact_physics_check(values, experiment)
    directory = Path(output).resolve()
    reuse_index = _reusable_tasks(reuse_from)
    if any(Path(path).resolve() == directory for path in reuse_from):
        raise SimulationError("an output directory cannot reuse from itself")
    directory.mkdir(parents=True, exist_ok=True)
    resolved_copy = directory / "resolved.json"
    source_bytes = Path(resolved_path).read_bytes()
    if resolved_copy.exists() and resolved_copy.read_bytes() != source_bytes:
        raise SimulationError(f"output belongs to a different resolved experiment: {directory}")
    if not resolved_copy.exists():
        resolved_copy.write_bytes(source_bytes)

    lattice_path = directory / "CrystalMaps" / str(derived["lattice_map_name"]) / "config.txt"
    lattice_bytes = render_lattice(values, derived, lattice_path)
    lattice_digest = hashlib.sha256(lattice_bytes).hexdigest()
    task_records = []
    for task in experiment.sampling.tasks:
        paths = _task_paths(directory, task)
        macro = render_macro(
            experiment, values, derived, task,
            hit_path=paths["partial"], witness_path=paths["witness"],
        )
        paths["macro"].parent.mkdir(parents=True, exist_ok=True)
        if paths["macro"].exists() and paths["macro"].read_text(encoding="utf-8") != macro:
            raise SimulationError(f"existing macro differs: {paths['macro']}")
        if not paths["macro"].exists():
            paths["macro"].write_text(macro, encoding="utf-8")
        macro_digest = hashlib.sha256(normalized_macro_bytes(macro)).hexdigest()
        identity = task_key(
            resolved_physics={"values": values, "derived": derived},
            site_mm=task.site_mm,
            seeds=task.seeds,
            events=task.events,
            macro_physics_sha256=macro_digest,
            lattice_sha256=lattice_digest,
            executable_sha256=str(experiment.build["executable_sha256"]),
            runtime_input_sha256=runtime_hashes,
        )
        task_records.append((task, paths, identity))

    simulation_identity = simulation_key(ordered_task_keys=[item[2] for item in task_records])
    database_path = directory / "results.sqlite"
    store = Store.open(database_path) if database_path.exists() else Store.create(database_path)
    lease = store.acquire_controller(
        f"material-scan:{os.getpid()}", _CONTROLLER_TTL_S
    )
    last_controller_renewal = time.monotonic()
    try:
        store.recover_running_attempts(lease, detail="controller restarted")
        store.register_experiment(lease, experiment.experiment_id, experiment.to_manifest())
        store.plan_simulation(
            lease, simulation_identity, experiment.experiment_id, simulation_identity,
            {"resolved_manifest_key": experiment.manifest_key, "values": values, "derived": derived},
            len(task_records),
        )
        for index, (task, _paths, identity) in enumerate(task_records):
            store.plan_task(
                lease, identity, simulation_identity, index, identity, task.to_manifest()
            )

        completed: dict[int, dict[str, Any]] = {}
        reused = 0
        environment = dict(os.environ)
        environment["G4LATTICEDATA"] = str(directory / "CrystalMaps")

        def work(task: Any, paths: Mapping[str, Path], attempt: Any) -> tuple[str, Mapping[str, Path], Any]:
            state = inspect_artifacts(
                partial_output=paths["partial"], final_output=paths["hit"],
                completion_witness=paths["witness"],
            )
            if state.state == "ready-to-publish":
                os.replace(paths["partial"], paths["hit"])
                paths["witness"].unlink()
                state = inspect_artifacts(
                    partial_output=paths["partial"], final_output=paths["hit"],
                    completion_witness=paths["witness"],
                )
            if state.state == "published-uncommitted":
                if not paths["log"].is_file():
                    raise SimulationError("published hit file has no run log for material verification")
                _material_log_check(
                    paths["log"], str(derived["g4_material_name"]),
                    float(derived["expected_density_kg_m3"]),
                )
                score = _score(paths["hit"], experiment, task)
                _write_atomic(paths["score"], score)
                return str(state.sha256), paths, score
            if state.state != "absent" or paths["log"].exists():
                abandoned = directory / "abandoned" / f"attempt-{attempt.number}-{attempt.token}"
                abandoned.mkdir(parents=True, exist_ok=False)
                for label in ("partial", "witness", "log"):
                    if paths[label].exists():
                        os.replace(paths[label], abandoned / paths[label].name)
            result = run_task(
                [str(executable), str(paths["macro"])],
                partial_output=paths["partial"], final_output=paths["hit"],
                completion_witness=paths["witness"], log_path=paths["log"],
                timeout_s=(timeout_s if timeout_s > 0 else 7 * 24 * 3600),
                environment=environment,
            )
            _material_log_check(
                result.log, str(derived["g4_material_name"]),
                float(derived["expected_density_kg_m3"]),
            )
            score = _score(result.output, experiment, task)
            _write_atomic(paths["score"], score)
            return result.output_sha256, paths, score

        queue: list[tuple[Any, Mapping[str, Path], str]] = []
        for task, paths, identity in task_records:
            row = store.task_row(identity)
            if row and row["completeness_state"] == "complete":
                if not paths["score"].is_file() or not paths["hit"].is_file():
                    raise SimulationError(f"database says complete but files are absent for {identity}")
                score = json.loads(paths["score"].read_text(encoding="utf-8"))
                if score["hit_sha256"] != sha256_file(paths["hit"]):
                    raise SimulationError(f"completed hit checksum differs for {identity}")
                completed[task.replica * experiment.sampling.design.n_sites + task.position] = score
            elif identity in reuse_index:
                source_directory, expected_sha256 = reuse_index[identity]
                source_paths = _task_paths(source_directory, task)
                if sha256_file(source_paths["hit"]) != expected_sha256:
                    raise SimulationError(f"reuse hit checksum differs for {identity}")
                if not source_paths["log"].is_file():
                    raise SimulationError(f"reuse log is absent for {identity}")
                _material_log_check(
                    source_paths["log"], str(derived["g4_material_name"]),
                    float(derived["expected_density_kg_m3"]),
                )
                attempt = store.start_attempt(lease, identity)
                try:
                    _link_or_copy(source_paths["hit"], paths["hit"])
                    _link_or_copy(source_paths["log"], paths["log"])
                    score = _score(paths["hit"], experiment, task)
                    _write_atomic(paths["score"], score)
                    store.finish_attempt(
                        lease, attempt, completeness="complete", validity="valid",
                        artifact_path=str(paths["hit"].relative_to(ROOT)),
                        artifact_sha256=expected_sha256,
                    )
                except Exception as error:
                    store.finish_attempt(
                        lease, attempt, completeness="incomplete", validity="invalid",
                        failure_kind=type(error).__name__, failure_detail=str(error),
                    )
                    raise
                completed[task.replica * experiment.sampling.design.n_sites + task.position] = score
                reused += 1
            else:
                queue.append((task, paths, identity))

        if reused:
            print(
                f"{experiment.experiment_id}: reused {reused}/{len(task_records)} "
                "verified tasks",
                flush=True,
            )

        pool = ThreadPoolExecutor(max_workers=workers)
        pending: dict[Future[Any], tuple[Any, Mapping[str, Path], Any, str]] = {}

        def submit_one(item: tuple[Any, Mapping[str, Path], str]) -> None:
            task, paths, identity = item
            attempt = store.start_attempt(lease, identity)
            future = pool.submit(work, task, paths, attempt)
            pending[future] = (task, paths, attempt, identity)

        try:
            cursor = 0
            while cursor < len(queue) and len(pending) < workers:
                submit_one(queue[cursor])
                cursor += 1
            while pending:
                done, _ = wait(
                    tuple(pending),
                    timeout=_CONTROLLER_RENEW_INTERVAL_S,
                    return_when=FIRST_COMPLETED,
                )
                if time.monotonic() - last_controller_renewal >= _CONTROLLER_RENEW_INTERVAL_S:
                    lease = store.renew_controller(lease, _CONTROLLER_TTL_S)
                    last_controller_renewal = time.monotonic()
                for future in done:
                    task, paths, attempt, identity = pending.pop(future)
                    try:
                        artifact_sha256, _, score = future.result()
                    except Exception as error:
                        store.finish_attempt(
                            lease, attempt, completeness="incomplete", validity="invalid",
                            failure_kind=type(error).__name__, failure_detail=str(error),
                        )
                        for other in pending:
                            other.cancel()
                        raise
                    store.finish_attempt(
                        lease, attempt, completeness="complete", validity="valid",
                        artifact_path=str(paths["hit"].relative_to(ROOT)),
                        artifact_sha256=artifact_sha256,
                    )
                    completed[task.replica * experiment.sampling.design.n_sites + task.position] = score
                    if len(completed) % 100 == 0 or len(completed) == len(task_records):
                        print(
                            f"{experiment.experiment_id}: {len(completed)}/{len(task_records)} "
                            "tasks complete",
                            flush=True,
                        )
                    if cursor < len(queue):
                        submit_one(queue[cursor])
                        cursor += 1
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        ordered_scores = [completed[index] for index in range(len(task_records))]
        blocks = [Block(
            position=int(score["position"]), replica=int(score["replica"]),
            events=int(score["events"]), total_qps=float(score["total_qps"]),
            per_electrode_qps=tuple(score["per_electrode_qps"]),
            n_hits=int(score["n_hits"]),
        ) for score in ordered_scores]
        estimate = stratified_estimate(blocks, _design(experiment))
        store.finish_simulation(lease, simulation_identity, completeness="complete", validity="valid")
        scorer = {
            "physics_py_sha256": sha256_file(Path(__file__).with_name("physics.py")),
            "analysis_py_sha256": sha256_file(Path(__file__).with_name("analysis.py")),
        }
        result_analysis_key = analysis_key(
            simulation=simulation_identity,
            artifact_sha256=[score["hit_sha256"] for score in ordered_scores],
            design=experiment.sampling.design.design_key,
            scorer=scorer,
            objective=dict(experiment.analysis),
        )
        result = {
            "schema": "material-scan-result-1",
            "experiment_id": experiment.experiment_id,
            "manifest_key": experiment.manifest_key,
            "simulation_key": simulation_identity,
            "analysis_key": result_analysis_key,
            "design_hash": estimate.design_hash,
            "sites": estimate.n_sites,
            "replicas": estimate.n_replicas,
            "events_per_task": estimate.events_per_task,
            "events_total": len(blocks) * estimate.events_per_task,
            "value": estimate.value,
            "standard_error": estimate.standard_error,
            "relative_standard_error": estimate.relative_standard_error,
            "means_by_stratum": estimate.means_by_stratum,
            "sites_by_stratum": estimate.sites_by_stratum,
            "maximum_site_leverage": estimate.maximum_site_leverage,
            "spatial_r95": estimate.spatial_r95,
            "spatial_r95_standard_error": estimate.spatial_r95_standard_error,
            "total_hit_rows": sum(block.n_hits or 0 for block in blocks),
            "zero_hit_tasks": sum((block.n_hits or 0) == 0 for block in blocks),
            "values": values,
            "derived": derived,
        }
        _write_atomic(directory / "result.json", result)
        store.record_observation(
            lease,
            canonical_hash("observation/v1", {"analysis_key": result_analysis_key}),
            simulation_identity, result_analysis_key,
            str(experiment.analysis["objective"]), result,
        )
        return result
    finally:
        try:
            store.release_controller(lease)
        finally:
            store.close()
