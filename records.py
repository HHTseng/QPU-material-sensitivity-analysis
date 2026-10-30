"""Recorded inputs, measured results, and verified copies."""

import contextlib
import copy
import csv
import ctypes
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import yaml


HIT_COLUMNS = ("Energy Deposited [eV]", "End X [m]", "End Y [m]",
               "End Z [m]", "Final Time [ns]")
ARCHIVE_SCHEMA = "material-scan-archive-1"
SCHEMA_VERSION = 4


def _json_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot record {type(value).__name__} as JSON")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=_json_value)


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def public_summary(value):
    """Keep estimates and archive identities; per-site blocks stay in archives."""
    if isinstance(value, dict):
        return {key: public_summary(item) for key, item in value.items() if key != 'blocks'}
    if isinstance(value, list):
        return [public_summary(item) for item in value]
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish(temporary, target, overwrite=False):
    temporary, target = Path(temporary).absolute(), Path(target).absolute()
    if temporary.is_symlink() or not (temporary.is_dir() or temporary.is_file()):
        raise ValueError("publication source must be a real file or directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    if temporary.is_dir():
        if overwrite:
            raise ValueError("directories cannot replace an existing result")
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(target), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    elif overwrite:
        os.replace(temporary, target)
    else:
        os.link(temporary, target)
        Path(temporary).unlink()
    descriptor = os.open(Path(target).parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


def write_json(path, value, overwrite=False):
    target = Path(path).absolute()
    encoded = (canonical_json(value) + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and target.is_file() and target.read_bytes() == encoded:
        return target
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            publish(name, target, overwrite)
        except FileExistsError:
            if overwrite or not target.is_file() or target.read_bytes() != encoded:
                raise
    finally:
        Path(name).unlink(missing_ok=True)
    return target




def read_hits(path, chunk_rows=65536):
    """Yield selected float64 columns; header-only data yield no chunks."""
    if not isinstance(chunk_rows, int) or chunk_rows <= 0:
        raise ValueError("chunk_rows must be a positive integer")
    source = Path(path)
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError(f"missing or zero-byte hit file: {source}")
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        fields = next(reader, [])
        if len(fields) != len(set(fields)) or not set(HIT_COLUMNS).issubset(fields):
            raise ValueError(f"missing or repeated hit columns: {source}")
        indices = [fields.index(name) for name in HIT_COLUMNS]
        batch = []
        for number, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(fields):
                raise ValueError(f"wrong column count in {source}:{number}")
            try:
                values = tuple(float(row[index]) for index in indices)
            except ValueError as error:
                raise ValueError(f"invalid hit number in {source}:{number}") from error
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"non-finite hit number in {source}:{number}")
            batch.append(values)
            if len(batch) == chunk_rows:
                array = np.asarray(batch, dtype=np.float64)
                yield tuple(array[:, index] for index in range(5))
                batch.clear()
        if batch:
            array = np.asarray(batch, dtype=np.float64)
            yield tuple(array[:, index] for index in range(5))


def load_document(path):
    if isinstance(path, dict):
        return copy.deepcopy(path)
    path = Path(path).expanduser()
    value = json.loads(path.read_text()) if path.suffix.lower() == '.json' else yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a mapping')
    return value


def lattice_record(path, text=None):
    fields = {}
    required = {'cubic': (1, 'Ang'), 'stiffness 1 1': (1, 'GPa'), 'stiffness 1 2': (1, 'GPa'),
                'stiffness 4 4': (1, 'GPa'), 'dyn': (4, 'GPa'), 'scat': (1, 's3'), 'decay': (1, 's4'),
                'decayTT': (1, ''), 'LDOS': (1, ''), 'STDOS': (1, ''), 'FTDOS': (1, '')}
    for raw in (Path(path).read_text() if text is None else text).splitlines():
        parts = raw.split('#')[0].split()
        if not parts:
            continue
        key, rest = parts[0], parts[1:]
        if key == 'stiffness':
            key = ' '.join(parts[:3]); rest = parts[3:]
        numbers = []
        for token in rest:
            try:
                numbers.append(float(token))
            except ValueError:
                break
        if key in required:
            count, unit = required[key]
            actual_unit = ' '.join(rest[len(numbers):])
            if key in fields:
                raise ValueError(f'{path}: duplicate lattice field {key}')
            if len(numbers) != count or not np.isfinite(numbers).all():
                raise ValueError(f'{path}: invalid lattice numbers for {key}')
            if actual_unit != unit and not (key == 'dyn' and actual_unit == ''):
                raise ValueError(f'{path}: {key} requires {unit or "dimensionless values"}')
        fields.setdefault(key, numbers)
    absent = [name for name in required if not fields.get(name)]
    if absent:
        raise ValueError(f'incomplete lattice record {path}: {absent}')
    return fields


def material_inputs(materials_file, lattice_data, names=("Si",)):
    root = Path(lattice_data).resolve()
    lattices = {}
    for name in sorted(set(names) | {"Si"}):
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("lattice map must be one directory name")
        source = root / name / "config.txt"
        encoded = source.read_bytes()
        text = encoded.decode("utf-8")
        lattices[name] = {"fields": lattice_record(source, text), "source": str(source),
                          "sha256": hashlib.sha256(encoded).hexdigest(), "text": text}
    return {"catalog": load_document(materials_file), "lattices": lattices}


def load_result(path, include_inputs=False, *, candidate_id=None):
    """Read normalized result rows and bind recovered files without rewriting data."""
    root = origin = None
    if isinstance(path, dict):
        data = copy.deepcopy(path)
    else:
        source = Path(path).resolve()
        if source.is_dir():
            choices = ("results.sqlite", "summary.json", "analysis.json", "results.json", "experiment.sqlite")
            source = next((source / name for name in choices if (source / name).is_file()), None)
            if source is None:
                raise ValueError(f"no recorded result in {path}")
        root = source.parent
        database_path = source if source.suffix in (".sqlite", ".sqlite3", ".db") else root / "results.sqlite"
        if source == database_path:
            data = None
        elif source.suffix == ".npz":
            with np.load(source, allow_pickle=False) as values:
                data = {"time_ns": values["time_ns"], "qps_by_electrode_time": values["qps"]}
        elif source.suffix == ".csv":
            data = {"hits_file": str(source)}
        else:
            data = json.loads(source.read_text())
        if database_path.is_file():
            with Results(database_path, create=False) as database:
                origin = database.read("state", "output_root") if "state" in database.tables else None
                if data is None:
                    data = database.read("state", "summary") if "state" in database.tables else None
                    if data is None and "observations" in database.tables:
                        data = {"results": database.read("observations"), "tasks": database.tasks()}
                    elif data is None:
                        data = {"results": database.rows("trials") if "trials" in database.tables else []}
                        if "trials" in database.tables:
                            annotations = {row["trial_id"]: row for row in history_summary(database_path)["trials"]}
                            data["results"] = [{**row, **annotations.get(row.get("trial_id"), {})} for row in data["results"]]
                if include_inputs:
                    data = data if isinstance(data, dict) else {"results": data}
                    data["candidate_inputs"] = {row["candidate_id"]: row["input"] for row in database.read("candidates")} if "candidates" in database.tables else {}
                if candidate_id is not None:
                    data['results']=[row for row in data.get('results',[]) if row.get('candidate_id')==candidate_id]
                    if not data['results']: raise ValueError('one recorded candidate is required')
                    data['results']=data['results'][:1]
                    row=data['results'][0]
                    if 'blocks' not in row:
                        tasks=database.tasks(candidate_id)
                        if not tasks or any(task['status']!='complete' for task in tasks):
                            raise ValueError('candidate does not have a complete recorded task set')
                        row['blocks']=[task['result'] for task in tasks]
        if include_inputs and (root / "resolved.json").is_file():
            data = data if isinstance(data, dict) else {"results": data}
            data["study"] = load_document(root / "resolved.json")
    data = data if isinstance(data, dict) else {"results": data}
    rows = data.get("results", data.get("rows", data.get("observations", data.get("candidates", []))))
    if not rows and any(name in data for name in ("mean", "value", "yield")):
        rows = [dict(data)]
    if isinstance(rows, dict):
        rows = [{"name": name, **(value if isinstance(value, dict) else {"value": value})} for name, value in rows.items()]
    normalized = []
    for raw in rows:
        row = {**raw, **raw["value"]} if isinstance(raw.get("value"), dict) else dict(raw)
        for name in ("value", "mean", "estimate", "yield", "J", "qp_mean", "mean_qp_per_eV", "total_QPs_per_primary"):
            value = row.get(name)
            if isinstance(value, (int, float, np.number)) and not isinstance(value, (bool, np.bool_)):
                row["value"] = float(value)
                break
        else:
            row["value"] = None
        error = next((row[name] for name in ("standard_error", "se", "stderr", "error") if row.get(name) is not None), None)
        row["standard_error"] = float(error) if error is not None else None
        row.setdefault("units", data.get("units"))
        normalized.append(row)
    data["results"] = normalized
    for name in ("rows", "observations"):
        data.pop(name, None)
    blocks = [block for row in normalized for block in row.get("blocks", [])]
    blocks += [row["result"] for row in data.get("tasks", []) if row.get("result") is not None]
    if root is not None:
        for block in blocks:
            recorded = block.get("output", "")
            if not origin and "/tasks/" not in recorded:
                continue
            old_root = Path(origin) if origin else Path(recorded.split("/tasks/", 1)[0])
            for field in ("output", "time_data"):
                if field in block:
                    relative = Path(block[field]).relative_to(old_root)
                    if ".." in relative.parts:
                        raise ValueError("recorded result path escapes its output directory")
                    block[field] = str(root / relative)
        data["root"] = str(root)
        data["recorded_root"] = origin
    return data


def fetch_catalog(destination, ids=None, max_results=50, api_key=None):
    """Save bulk structure and elasticity; film cryogenic fields remain absent."""
    import datetime
    import importlib.metadata
    import os
    try:
        from mp_api.client import MPRester
        from emmet.core.summary import HasProps
        from emmet.core.symmetry import CrystalSystem
    except ImportError as exc:
        raise RuntimeError('catalog retrieval requires mp-api in a separate environment') from exc
    api_key = api_key or os.environ.get('MP_API_KEY')
    if not api_key:
        raise ValueError('MP_API_KEY is required for catalog retrieval')
    if not isinstance(max_results, int) or max_results < 1:
        raise ValueError('max_results must be a positive integer')
    identifiers = [str(value).strip() for value in ids] if ids is not None else None
    if identifiers is not None and (not identifiers or not all(identifiers)):
        raise ValueError('explicit catalog IDs must be nonempty')
    summary_fields = ['material_id', 'formula_pretty', 'symmetry', 'density', 'band_gap',
                      'energy_above_hull', 'is_stable', 'is_metal', 'theoretical', 'structure']
    elastic_fields = ['material_id', 'elastic_tensor', 'last_updated', 'warnings']
    filters = {'explicit_ids': identifiers} if identifiers is not None else {
        'has_props': ['elasticity'], 'crystal_system': 'Cubic', 'energy_above_hull': [0., .10],
        'exclude_elements': ['U', 'Th', 'Np'], 'is_metal': False,
        'num_magnetic_sites': 0, 'theoretical': False}
    accepted, rejected = [], []
    with MPRester(api_key) as client:
        if identifiers is not None:
            summaries = list(client.materials.summary.search(material_ids=identifiers, fields=summary_fields))
        else:
            summaries = list(client.materials.summary.search(
                has_props=[HasProps.elasticity], crystal_system=CrystalSystem.cubic,
                energy_above_hull=(0., .10), exclude_elements=['U', 'Th', 'Np'],
                is_metal=False, num_magnetic_sites=0, theoretical=False, fields=summary_fields))[:max_results]
        by_id = {str(row.material_id): row for row in summaries}
        elastic = {str(row.material_id): row for row in client.materials.elasticity.search(
            material_ids=list(by_id), fields=elastic_fields)} if by_id else {}
        for identifier in identifiers or list(by_id):
            if identifier not in by_id:
                rejected.append({'material_id': identifier, 'reason': 'not found in Materials Project'})
                continue
            summary, mechanical = by_id[identifier], elastic.get(identifier)
            tensor = getattr(getattr(mechanical, 'elastic_tensor', None), 'ieee_format', None)
            c11 = c12 = c44 = None
            diagnostic = {'ok': False, 'reason': 'no elastic tensor'}
            if tensor is not None:
                tensor = np.asarray(tensor, dtype=float)
                if tensor.shape != (6, 6) or not np.isfinite(tensor).all():
                    diagnostic = {'ok': False, 'reason': 'elastic tensor is not finite 6 by 6'}
                else:
                    c11 = float(np.mean(np.diag(tensor)[:3]))
                    c12 = float(np.mean([tensor[0, 1], tensor[0, 2], tensor[1, 2]]))
                    c44 = float(np.mean(np.diag(tensor)[3:]))
                    cubic = np.zeros((6, 6)); cubic[:3, :3] = c12
                    np.fill_diagonal(cubic, [c11] * 3 + [c44] * 3)
                    spread = float(np.max(np.abs(tensor - cubic)) / max(abs(c11), 1e-9))
                    stable = c11 > c12 and c11 + 2 * c12 > 0 and c44 > 0
                    diagnostic = {'ok': stable and spread < .05, 'born_stable': stable,
                                  'cubic_spread_fraction': spread,
                                  'reason': None if stable and spread < .05 else
                                            'fails Born stability or cubic tensor symmetry'}
            density = getattr(summary, 'density', None)
            lattice = getattr(getattr(summary, 'structure', None), 'lattice', None)
            symmetry = getattr(summary, 'symmetry', None)
            updated = getattr(mechanical, 'last_updated', None)
            record = {'material_id': identifier, 'formula': summary.formula_pretty,
                      'source': 'materials_project',
                      'crystal_system': str(symmetry.crystal_system) if symmetry else None,
                      'spacegroup': getattr(symmetry, 'symbol', None),
                      'mp_density_g_cm3': density,
                      'mp_density_kg_m3': float(density) * 1000 if density is not None else None,
                      'lattice_a_ang': float(lattice.abc[0]) if lattice is not None else None,
                      'band_gap_eV': getattr(summary, 'band_gap', None),
                      'energy_above_hull_eV': getattr(summary, 'energy_above_hull', None),
                      'is_metal': getattr(summary, 'is_metal', None),
                      'is_stable': getattr(summary, 'is_stable', None),
                      'theoretical': getattr(summary, 'theoretical', None),
                      'elastic_tensor_GPa': tensor.tolist() if tensor is not None else None,
                      'tensor_convention': 'IEEE Voigt engineering strain, GPa',
                      'c11_GPa': c11, 'c12_GPa': c12, 'c44_GPa': c44,
                      'cubic_validation': diagnostic,
                      'elastic_last_updated': updated.isoformat() if updated else None,
                      'elastic_warnings': list(getattr(mechanical, 'warnings', None) or []),
                      'field_sources': {'density': 'Materials Project bulk DFT; cross-check only',
                                        'elasticity': 'Materials Project IEEE tensor',
                                        'structure': 'Materials Project relaxed structure'},
                      'curated_fields_required': ['gap_eV', 'ph_lifetime_ns', 'ph_lifetime_slope',
                                                  'qp_limit', 'thickness_um', 'tc_K'],
                      'enabled': False,
                      'enabled_blocked_by': 'complete transport record and cryogenic film fields are not supplied'}
            if identifiers is None and not diagnostic['ok']:
                rejected.append({'material_id': identifier, 'formula': record['formula'],
                                 'reason': diagnostic['reason']})
            else:
                accepted.append(record)
        database_version = client.get_database_version()
    snapshot = {'provenance': {'retrieved_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'mp_database_version': database_version,
                              'mp_api_version': importlib.metadata.version('mp-api'),
                              'mode': 'explicit_ids' if identifiers is not None else 'filtered_search',
                              'filters': filters, 'summary_fields_requested': summary_fields,
                              'elastic_fields_requested': elastic_fields},
                'accepted': accepted, 'rejected': rejected,
                'counts': {'accepted': len(accepted), 'rejected': len(rejected)}}
    write_json(destination, snapshot)
    return snapshot


class Results:
    """One parent writer; create=False is read-only.

    Measured J(x) requires every declared site/realization task (i,r) to finish.
    """

    def __init__(self, path, create=True):
        self.path = Path(path).resolve()
        self.read_only = not create
        self.owner = (os.getpid(), threading.get_ident())
        self.lock = None
        if create:
            if self.path.exists() and self.path.stat().st_nlink > 1:
                raise ValueError("writable result databases cannot have hard-linked aliases")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock = open(str(self.path) + ".writer.lock", "a+")
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.lock.close()
                self.lock = None
                raise RuntimeError(f"another writer has {self.path}") from None
        elif not self.path.is_file():
            raise FileNotFoundError(self.path)
        try:
            if create:
                self.connection = sqlite3.connect(self.path, timeout=30)
            else:
                self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro",
                                                  uri=True, timeout=30)
                self.connection.execute("PRAGMA query_only=ON")
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.tables = tuple(row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
            if create and not self.tables:
                self.connection.executescript("""
                    CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE experiments (experiment_id TEXT PRIMARY KEY,
                        input_json TEXT NOT NULL, simulation_hash TEXT NOT NULL,
                        analysis_hash TEXT NOT NULL, search_hash TEXT NOT NULL);
                    CREATE TABLE candidates (candidate_id TEXT PRIMARY KEY,
                        experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                        input_json TEXT NOT NULL, task_set_json TEXT NOT NULL);
                    CREATE TABLE tasks (task_id TEXT PRIMARY KEY, input_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'planned', active_token TEXT,
                        result_json TEXT, updated REAL NOT NULL);
                    CREATE TABLE candidate_tasks (candidate_id TEXT REFERENCES candidates(candidate_id),
                        task_index INTEGER NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                        PRIMARY KEY(candidate_id,task_index), UNIQUE(candidate_id,task_id));
                    CREATE TABLE attempts (token TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(task_id), number INTEGER NOT NULL,
                        status TEXT NOT NULL, started REAL NOT NULL, finished REAL, result_json TEXT,
                        UNIQUE(task_id,number));
                    CREATE TABLE observations (candidate_id TEXT REFERENCES candidates(candidate_id),
                        analysis_hash TEXT NOT NULL, kind TEXT NOT NULL, value_json TEXT NOT NULL,
                        created REAL NOT NULL, PRIMARY KEY(candidate_id,analysis_hash));
                    CREATE TABLE observation_history (candidate_id TEXT NOT NULL,
                        analysis_hash TEXT NOT NULL, kind TEXT NOT NULL, value_json TEXT NOT NULL,
                        created REAL NOT NULL, invalidated REAL NOT NULL, reason TEXT NOT NULL);
                    CREATE TABLE task_history (task_id TEXT NOT NULL REFERENCES tasks(task_id),
                        result_json TEXT NOT NULL, created REAL NOT NULL, reason TEXT NOT NULL);
                    CREATE TABLE state (name TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                    CREATE INDEX candidate_task_ids ON candidate_tasks(task_id);
                """)
                self.connection.execute("INSERT INTO metadata VALUES('schema_version',?)",
                                        (str(SCHEMA_VERSION),))
                self.connection.commit()
                self.tables = tuple(row[0] for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
            if create:
                version = self.connection.execute(
                    "SELECT value FROM metadata WHERE name='schema_version'").fetchone()
                if version is None or version[0] != str(SCHEMA_VERSION):
                    raise ValueError("existing database has another schema; use a read-only reader")
                with self.connection:
                    self.connection.execute("UPDATE attempts SET status='abandoned',finished=? "
                                            "WHERE status='running'", (time.time(),))
                    self.connection.execute("UPDATE tasks SET status='planned',active_token=NULL "
                                            "WHERE status='running'")
        except BaseException:
            self.close()
            raise

    def close(self):
        if hasattr(self, "connection"):
            self.connection.close()
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        self.close()

    def _write(self):
        if self.read_only or self.owner != (os.getpid(), threading.get_ident()):
            raise RuntimeError("only the parent writer may change results")

    def rows(self, table):
        if table not in self.tables:
            raise ValueError(f"unknown table: {table}")
        quoted = '"' + table.replace('"', '""') + '"'
        return [dict(row) for row in self.connection.execute(f"SELECT * FROM {quoted}")]

    def read(self, table, key=None, default=None):
        if table not in ("state", "candidates", "observations"):
            raise ValueError(f"unknown decoded result table: {table}")
        if table == "state":
            row = self.connection.execute("SELECT value_json FROM state WHERE name=?", (key,)).fetchone()
            return json.loads(row[0]) if row is not None else default
        query = f"SELECT * FROM {table}" + (" WHERE candidate_id=?" if key is not None else "")
        rows = [dict(row) for row in self.connection.execute(query, () if key is None else (key,))]
        if table == "candidates":
            return [{**row, "input": json.loads(row["input_json"]), "task_ids": json.loads(row["task_set_json"])} for row in rows]
        return [{**row, "value": json.loads(row["value_json"])} for row in rows]

    def plan(self, experiment, candidate_id, candidate, tasks):
        self._write()
        experiment = {"experiment_id": experiment} if isinstance(experiment, str) else dict(experiment)
        experiment_id = str(experiment["experiment_id"] if "experiment_id" in experiment else
                            experiment["name"] if "name" in experiment else content_hash(experiment))
        task_list = [dict(task) for task in tasks]
        task_ids = [str(task["task_id"]) for task in task_list]
        if not task_ids or len(task_ids) != len(set(task_ids)):
            raise ValueError("a candidate needs a nonempty, unique task set")
        input_json = canonical_json(candidate)
        task_set_json = canonical_json(task_ids)
        simulation = experiment["simulation_hash"] if "simulation_hash" in experiment else content_hash(experiment.get("simulation", experiment))
        analysis = experiment["analysis_hash"] if "analysis_hash" in experiment else content_hash(experiment.get("analysis", {}))
        search = experiment["search_hash"] if "search_hash" in experiment else content_hash(experiment.get("search", {}))
        record = (experiment_id, canonical_json(experiment), simulation, analysis, search)
        now = time.time()
        with self.connection:
            previous = self.connection.execute("SELECT * FROM experiments WHERE experiment_id=?",
                                               (experiment_id,)).fetchone()
            if previous is not None and tuple(previous) != record:
                raise ValueError("experiment identifier was reused with different inputs")
            self.connection.execute("INSERT OR IGNORE INTO experiments VALUES(?,?,?,?,?)", record)
            previous = self.connection.execute("SELECT * FROM candidates WHERE candidate_id=?",
                                               (candidate_id,)).fetchone()
            declaration = (candidate_id, experiment_id, input_json, task_set_json)
            if previous is not None and tuple(previous) != declaration:
                raise ValueError("candidate identifier or declared task set changed")
            self.connection.execute("INSERT OR IGNORE INTO candidates VALUES(?,?,?,?)", declaration)
            for index, task in enumerate(task_list):
                task_id = task_ids[index]
                encoded = canonical_json(task)
                previous = self.connection.execute("SELECT input_json FROM tasks WHERE task_id=?",
                                                   (task_id,)).fetchone()
                if previous is not None and previous[0] != encoded:
                    raise ValueError("task identifier was reused with different inputs")
                self.connection.execute("INSERT OR IGNORE INTO tasks(task_id,input_json,updated) VALUES(?,?,?)",
                                        (task_id, encoded, now))
                self.connection.execute("INSERT OR IGNORE INTO candidate_tasks VALUES(?,?,?)",
                                        (candidate_id, index, task_id))
        return task_ids

    def tasks(self, candidate_id=None):
        if candidate_id is None:
            selected = self.connection.execute("SELECT * FROM tasks ORDER BY task_id")
        else:
            selected = self.connection.execute("SELECT t.* FROM tasks t JOIN candidate_tasks c "
                                               "ON t.task_id=c.task_id WHERE c.candidate_id=? "
                                               "ORDER BY c.task_index", (candidate_id,))
        result = []
        for row in selected:
            item = dict(row)
            item["input"] = json.loads(item["input_json"])
            item["result"] = json.loads(item["result_json"]) if item["result_json"] else None
            result.append(item)
        return result


    def completed(self, candidate_id, tasks=None, verified=None):
        declaration = self.connection.execute("SELECT task_set_json FROM candidates WHERE candidate_id=?",
                                              (candidate_id,)).fetchone()
        if declaration is None:
            return False
        if tasks is None:
            tasks = self.tasks(candidate_id)
        else:
            current = self.connection.execute("SELECT t.task_id,t.status,t.updated FROM tasks t JOIN candidate_tasks c "
                                              "ON t.task_id=c.task_id WHERE c.candidate_id=? ORDER BY c.task_index",
                                              (candidate_id,)).fetchall()
            if [tuple(row) for row in current] != [(row["task_id"], row["status"], row.get("updated")) for row in tasks]:
                return False
        if [task["task_id"] for task in tasks] != json.loads(declaration[0]):
            return False
        for task in tasks:
            value = task["result"]
            if task["status"] != "complete" or value is None:
                return False
            if "output" in value or "sha256" in value:
                if "output" not in value or "sha256" not in value:
                    return False
                path = Path(value["output"]).resolve()
                try:
                    before = path.stat()
                except OSError:
                    return False
                if not stat.S_ISREG(before.st_mode):
                    return False
                signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                key = (str(path), value["sha256"])
                if verified is None or verified.get(key) != signature:
                    try:
                        digest = sha256_file(path)
                        after = path.stat()
                    except OSError:
                        return False
                    if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or digest != value["sha256"]:
                        return False
                    if verified is not None:
                        verified[key] = signature
        return True

    def start(self, task_id):
        self._write()
        token = secrets.token_hex(20)
        with self.connection:
            row = self.connection.execute("SELECT status,active_token FROM tasks WHERE task_id=?",
                                          (task_id,)).fetchone()
            if row is None:
                raise ValueError("task was not declared")
            if row[0] in ("complete", "running") or row[1] is not None:
                raise RuntimeError("task is complete or already running")
            number = self.connection.execute("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE task_id=?",
                                             (task_id,)).fetchone()[0]
            now = time.time()
            self.connection.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?)",
                                    (token, task_id, number, "running", now, None, None))
            self.connection.execute("UPDATE tasks SET status='running',active_token=?,result_json=NULL,updated=? "
                                    "WHERE task_id=?", (token, now, task_id))
        return token

    def finish(self, task_id, token, status, result):
        self._write()
        status = {"done": "complete", "ok": "complete"}.get(status, status)
        if status not in ("complete", "timeout", "failed", "incomplete", "invalid", "interrupted"):
            raise ValueError(f"unknown task state: {status}")
        value = dict(result or {})
        if status == "complete":
            if value.get("validity", "valid") != "valid" or not value.get("complete", True):
                raise ValueError("invalid or incomplete output cannot have a measured yield")
            if "qps" not in value and "per_electrode_qps" not in value and "electrode_totals" not in value:
                raise ValueError("complete task is missing its measured electrode counts")
            counts = value.get("qps", value.get("per_electrode_qps", value.get("electrode_totals")))
            counts = np.asarray(counts, dtype=float)
            if counts.ndim != 1 or not counts.size or not np.isfinite(counts).all() or (counts < 0).any():
                raise ValueError("measured electrode counts must be finite and nonnegative")
            value["completeness"] = "complete"
            value["validity"] = "valid"
            value["measured"] = True
        else:
            for name in ("qps", "per_electrode_qps", "electrode_totals", "total_qps", "yield", "value"):
                value.pop(name, None)
            value.update(completeness=value.get("completeness", "incomplete"), measured=False)
        encoded = canonical_json(value)
        with self.connection:
            row = self.connection.execute("SELECT active_token,status FROM tasks WHERE task_id=?",
                                          (task_id,)).fetchone()
            if row is None or row[0] != token or row[1] != "running":
                raise RuntimeError("late result from an inactive attempt")
            now = time.time()
            self.connection.execute("UPDATE attempts SET status=?,finished=?,result_json=? WHERE token=?",
                                    (status, now, encoded, token))
            self.connection.execute("UPDATE tasks SET status=?,active_token=NULL,result_json=?,updated=? "
                                    "WHERE task_id=?", (status, encoded, now, task_id))
        return value


    def revise(self, task_id, result=None, reason=None):
        self._write()
        with self.connection:
            row = self.connection.execute("SELECT status,result_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None or row[0] == "running":
                raise ValueError("cannot revise an absent or running task")
            old = json.loads(row[1]) if row[1] else {}
            if result is None:
                status = "invalid"
                reason = reason or "recorded output invalidated"
                value = {key: item for key, item in old.items() if key not in
                         ("qps", "per_electrode_qps", "electrode_totals", "total_qps", "yield", "value")}
                value.update(measured=False, validity="invalid", reason=reason)
            else:
                if row[0] != "complete":
                    raise ValueError("only a complete task can be scored again")
                status = "complete"
                reason = reason or "scoring calculation changed; raw output retained"
                value = dict(result)
                for name in ("output", "sha256", "events", "position", "replica", "n_hits"):
                    if value.get(name) != old.get(name):
                        raise ValueError(f"scoring cannot change recorded {name}")
                if not value.get("output") or not value.get("sha256") or not Path(value["output"]).is_file() or sha256_file(value["output"]) != value["sha256"]:
                    raise ValueError("raw output differs from the recorded bytes")
                key = next((name for name in ("per_electrode_qps", "qps", "electrode_totals") if name in old), None)
                if key is None or key not in value:
                    raise ValueError("missing measured electrode counts")
                counts = np.asarray(value[key], dtype=float)
                if counts.ndim != 1 or counts.shape != np.asarray(old[key]).shape or not np.isfinite(counts).all() or (counts < 0).any():
                    raise ValueError("scored counts have invalid values or electrode shape")
                if value.get("validity", "valid") != "valid" or not value.get("complete", True):
                    raise ValueError("rescored output must remain complete and valid")
                value.update(completeness="complete", validity="valid", measured=True)
            encoded = canonical_json(value)
            if encoded == row[1] and row[0] == status:
                return value
            now = time.time()
            if row[1] is not None:
                self.connection.execute("INSERT INTO task_history VALUES(?,?,?,?)", (task_id, row[1], now, reason))
            self.connection.execute("UPDATE tasks SET status=?,active_token=NULL,result_json=?,updated=? WHERE task_id=?",
                                    (status, encoded, now, task_id))
            self.connection.execute("INSERT INTO observation_history SELECT candidate_id,analysis_hash,kind,value_json,created,?,? "
                                    "FROM observations WHERE candidate_id IN (SELECT candidate_id FROM candidate_tasks WHERE task_id=?)",
                                    (now, reason, task_id))
            self.connection.execute("DELETE FROM observations WHERE candidate_id IN "
                                    "(SELECT candidate_id FROM candidate_tasks WHERE task_id=?)", (task_id,))
        return value

    def observation(self, candidate_id, value, analysis=None, kind="measured", tasks=None, verified=None):
        """Store J(x) and SE(J) with complete tasks; censored records have no yield."""
        self._write()
        if kind not in ("measured", "physical_zero", "right_censored", "non_observation"):
            raise ValueError("unknown observation kind")
        if kind in ("measured", "physical_zero") and not self.completed(candidate_id, tasks, verified):
            raise ValueError("a measured observation requires the exact complete task set")
        if kind in ("right_censored", "non_observation"):
            if not isinstance(value, dict) or any(value.get(name) is not None for name in
                    ("yield", "mean", "value", "estimate", "qps", "per_electrode_qps", "electrode_totals")):
                raise ValueError("an unmeasured observation cannot contain a measured yield")
        analysis_hash = analysis if isinstance(analysis, str) else content_hash(analysis or {})
        encoded = canonical_json(value)
        with self.connection:
            previous = self.connection.execute("SELECT kind,value_json FROM observations WHERE candidate_id=? "
                                               "AND analysis_hash=?", (candidate_id, analysis_hash)).fetchone()
            if previous is not None and tuple(previous) != (kind, encoded):
                raise ValueError("an existing observation cannot be overwritten")
            self.connection.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)",
                                    (candidate_id, analysis_hash, kind, encoded, time.time()))
        return analysis_hash


    def save_state(self, name, value):
        self._write()
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (name, canonical_json(value)))


    def inspect(self):
        report = {"path": str(self.path), "tables": list(self.tables), "read_only": self.read_only,
                  "integrity": self.connection.execute("PRAGMA integrity_check").fetchone()[0],
                  "foreign_key_violations": [tuple(row) for row in self.connection.execute("PRAGMA foreign_key_check")]}
        if "trials" in self.tables:
            report["trials"] = self.rows("trials")
        if "tasks" in self.tables:
            report["tasks"] = self.rows("tasks")
        return report

    def backup(self, destination):
        target = Path(destination).absolute()
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        try:
            with sqlite3.connect(name) as output:
                self.connection.backup(output)
                if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite backup failed integrity checking")
            with open(name, "rb") as handle:
                os.fsync(handle.fileno())
            publish(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
        return target


def _safe_name(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("unsafe archive path")
    if value == "MANIFEST.json" or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"unsafe archive path: {value}")
    return value


def inventory(root):
    """Hash regular files while checking concurrent writes and symlinks."""
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("inventory root must be a real directory")
    entries = []
    for directory, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f"inventory refuses symlink: {Path(directory) / name}")
        for name in names:
            path = Path(directory) / name
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"inventory refuses non-regular file: {path}")
            digest = sha256_file(path)
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError(f"file changed during inventory: {path}")
            entries.append({"path": _safe_name(path.relative_to(root).as_posix()), "size": after.st_size,
                            "sha256": digest, "mode": stat.S_IMODE(after.st_mode), "mtime_ns": after.st_mtime_ns})
    entries.sort(key=lambda entry: entry['path'])
    return {"schema": ARCHIVE_SCHEMA, "files": entries}


def history_summary(database_path, invalidations_file=None):
    """Read recorded validity findings without reusing historical simulations."""
    import yaml
    register = Path(invalidations_file) if invalidations_file is not None else Path(__file__).parent / "legacy/stage4_invalidations.yaml"
    if invalidations_file is not None and not register.is_file():
        raise FileNotFoundError(register)
    findings = yaml.safe_load(register.read_text()) if register.is_file() else {}
    if not isinstance(findings, dict):
        raise ValueError("invalid validity register")
    findings = json.loads(json.dumps(findings, default=str))
    annotations = {}
    for name, finding in findings.get("findings", {}).items():
        for trial in finding.get("trials", []):
            if trial.get("verdict") not in ("valid", "invalid") or not trial.get("trial_id"):
                raise ValueError("validity finding needs a trial identifier and valid/invalid verdict")
            annotations.setdefault(str(trial["trial_id"]), []).append({
                "finding": name, "verdict": trial["verdict"], "title": finding.get("title"),
                "label": trial.get("label"), "note": trial.get("note"),
                "also_affected_by": trial.get("also_affected_by", [])})
    with Results(database_path, create=False) as database:
        report = {"path": str(database.path), "read_only": True,
                  "integrity": database.connection.execute("PRAGMA integrity_check").fetchone()[0],
                  "foreign_key_violations": [tuple(row) for row in database.connection.execute("PRAGMA foreign_key_check")],
                  "counts": {}, "status_counts": {}, "trials": [], "automatic_reuse": False}
        for table in database.tables:
            quoted = '"' + table.replace('"', '""') + '"'
            report["counts"][table] = database.connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            columns = [row[1] for row in database.connection.execute(f"PRAGMA table_info({quoted})")]
            status = next((name for name in ("status", "run_state") if name in columns), None)
            if status is not None:
                report["status_counts"][table] = {str(row[0]): row[1] for row in database.connection.execute(
                    f'SELECT "{status}",COUNT(*) FROM {quoted} GROUP BY "{status}"')}
            if table == "trials":
                selected = [name for name in ("trial_id", "status", "validity_state", "failure_kind", "proposal_source",
                            "optimizer_generation", "used_in_optimizer_update") if name in columns]
                if not selected:
                    continue
                query = ",".join('"' + name + '"' for name in selected)
                for row in database.connection.execute(f"SELECT {query} FROM {quoted}"):
                    trial = dict(row)
                    known = annotations.get(str(trial.get("trial_id")), [])
                    trial["registered_findings"] = known
                    trial["registered_validity"] = ("invalid" if any(item["verdict"] == "invalid" for item in known)
                                                     else "checked_for_listed_findings" if known else "unchecked")
                    report["trials"].append(trial)
    decisions = findings.get("decisions", {})
    report["validity_register"] = str(register) if register.is_file() else None
    report["decisions"] = decisions
    report["unavailable_optimizer_labels"] = sorted(set(str(label) for decision in decisions.values()
        if "optimizer" in str(decision.get("title", "")).lower() and "unrecoverable" in str(decision.get("title", "")).lower()
        for label in decision.get("campaigns", [])))
    if "optimizer_provenance_unrecoverable" in decisions:
        report["unavailable_optimizer_labels"] = decisions["optimizer_provenance_unrecoverable"].get("campaigns", [])
    report["validity_scope"] = "Listed verdicts concern their named findings; an unlisted trial is unchecked."
    return report


def git_inventory(root, repository, revision="HEAD"):
    """Compare a bound repository subtree with disk, retaining Git-only files."""
    source = Path(root).resolve()
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(["git", "-C", str(Path(repository).resolve()), "rev-parse", "--show-toplevel"],
                            env=environment, capture_output=True, check=True, text=True)
    repository = Path(result.stdout.strip()).resolve()
    if Path(root).is_symlink() or not source.is_dir() or not source.is_relative_to(repository):
        raise ValueError("inventory root must be a real directory inside the selected repository")
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", revision) or ".." in revision:
        raise ValueError("unsafe Git revision")
    tree = subprocess.run(["git", "-C", str(repository), "rev-parse", "--verify", revision + "^{tree}"],
                          env=environment, capture_output=True, check=True, text=True).stdout.strip()
    prefix = source.relative_to(repository).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    command = ["git", "--literal-pathspecs", "-C", str(repository), "ls-tree", "-r", "-z", tree]
    if prefix:
        command += ["--", prefix.rstrip("/")]
    listing = subprocess.run(command, env=environment, capture_output=True, check=True).stdout
    entries = {}
    for raw in listing.split(b"\0"):
        if raw:
            header, path = raw.split(b"\t", 1)
            mode, kind, identifier = header.decode("ascii").split()
            name = path.decode("utf-8")
            if kind == "blob" and name.startswith(prefix):
                entries[name[len(prefix):]] = {"git_object_id": identifier, "git_mode": mode}
    disk = {}
    for directory, directories, names in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name != ".git"]
        for name in directories + names:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"inventory refuses symlink: {path}")
        for name in names:
            path = Path(directory) / name
            if path.name == ".git" and source == repository:
                continue
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("inventory refuses a non-regular file")
            digest = sha256_file(path)
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("file changed during inventory")
            disk[path.relative_to(source).as_posix()] = {"size": after.st_size, "sha256": digest}
    metadata = {}
    with subprocess.Popen(["git", "-C", str(repository), "cat-file", "--batch"], env=environment,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        for identifier in dict.fromkeys(entry["git_object_id"] for entry in entries.values()):
            process.stdin.write(identifier.encode("ascii") + b"\n"); process.stdin.flush()
            header = process.stdout.readline().decode("ascii").split()
            if len(header) != 3 or header[1] != "blob":
                raise ValueError("Git object is not a readable blob")
            size = int(header[2]); remaining = size; digest = hashlib.sha256()
            while remaining:
                block = process.stdout.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("Git blob ended early")
                remaining -= len(block); digest.update(block)
            if process.stdout.read(1) != b"\n":
                raise ValueError("invalid Git blob separator")
            metadata[identifier] = {"git_size": size, "git_sha256": digest.hexdigest()}
        process.stdin.close()
        error = process.stderr.read()
        if process.wait():
            raise ValueError(error.decode("utf-8", "replace"))
    rows = []
    for name in sorted(set(entries) | set(disk)):
        row = {"path": name, **disk.get(name, {}), **entries.get(name, {})}
        if name in entries:
            row.update(metadata[entries[name]["git_object_id"]])
        row["status"] = ("Git-only" if name not in disk else "disk-only" if name not in entries else
                         "unchanged" if row["sha256"] == row["git_sha256"] else "modified")
        rows.append(row)
    return {"root": str(source), "repository": str(repository), "revision": revision, "tree": tree, "files": rows}


def create_archive(source_root, members, destination, *, include_manifest=True):
    """Write and verify a new copy; never delete source files."""
    declared_root = Path(source_root).absolute()
    if declared_root.is_symlink() or not declared_root.is_dir():
        raise ValueError('archive root must be a real directory')
    root = declared_root.resolve()
    target = Path(destination).absolute()
    if (target.parent.resolve() / target.name).is_relative_to(root):
        raise ValueError("archive output must be outside its source directory")
    with contextlib.ExitStack() as locks:
        for lock_path in root.rglob("*.writer.lock"):
            handle = locks.enter_context(lock_path.open("r"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("stop the result writer or make a logical SQLite backup before archiving") from None
        manifest = inventory(root)
        if members is not None:
            names = [_safe_name(str(name)) for name in members]
            if len(names) != len(set(names)):
                raise ValueError("duplicate archive members")
            known = {entry["path"]: entry for entry in manifest["files"]}
            if not set(names).issubset(known):
                raise ValueError("missing archive source file")
            manifest["files"] = [known[name] for name in sorted(names)]
        for entry in manifest["files"]:
            if entry["path"].endswith(("-wal", "-journal")) and entry["size"]:
                raise ValueError("live SQLite data require a logical backup before archiving")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, manifest_name = tempfile.mkstemp(prefix=f".{target.name}.manifest.", dir=target.parent)
        os.close(descriptor)
        name = None
        try:
            with open(manifest_name, 'wb', buffering=1024 * 1024) as handle:
                handle.write(b'{"files":[')
                for index, entry in enumerate(manifest['files']):
                    if index: handle.write(b',')
                    handle.write(canonical_json(entry).encode())
                handle.write(b'],"schema":' + canonical_json(manifest['schema']).encode() + b'}\n')
            manifest_digest = sha256_file(manifest_name)
            if target.exists():
                receipt = verify_archive(target, include_manifest=False)
                if receipt['manifest_sha256'] != manifest_digest:
                    raise FileExistsError("existing archive contains different files")
            else:
                descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                os.close(descriptor)
                with (open(name, "wb") as raw,
                      gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as compressed,
                      tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive):
                    info = tarfile.TarInfo("MANIFEST.json")
                    info.size = Path(manifest_name).stat().st_size
                    with open(manifest_name, 'rb') as handle:
                        archive.addfile(info, handle)
                    archive.members.clear()
                    for entry in manifest["files"]:
                        source = root / entry["path"]
                        before = source.stat()
                        if source.is_symlink() or (before.st_size, before.st_mtime_ns, stat.S_IMODE(before.st_mode)) != (entry["size"], entry["mtime_ns"], entry['mode']):
                            raise ValueError("source changed after inventory")
                        info = tarfile.TarInfo(entry["path"])
                        info.size = entry["size"]
                        with source.open("rb") as handle:
                            archive.addfile(info, handle)
                        archive.members.clear()
                receipt = verify_archive(name, include_manifest=False)
                if receipt['manifest_sha256'] != manifest_digest:
                    raise ValueError("archive verification changed its manifest")
                with open(name, "rb") as handle:
                    os.fsync(handle.fileno())
                publish(name, target)
            receipt['path'] = str(target)
            if include_manifest: receipt['manifest'] = manifest
            return receipt
        finally:
            Path(manifest_name).unlink(missing_ok=True)
            if name is not None: Path(name).unlink(missing_ok=True)


def verify_archive(path, expected_paths=None, expected_archive_sha256=None, *, include_manifest=True):
    source = Path(path).absolute()
    if source.is_symlink() or not source.is_file():
        raise ValueError("archive must be a regular file")
    before = source.stat()
    archive_digest = sha256_file(source)
    if expected_archive_sha256 is not None and archive_digest != expected_archive_sha256:
        raise ValueError("archive checksum differs")
    requested = sorted(_safe_name(str(name)) for name in expected_paths) if expected_paths is not None else None
    with source.open('rb') as raw:
        compressed = raw.read(2) == b'\x1f\x8b'; raw.seek(0)
        stream = gzip.GzipFile(fileobj=raw, mode='rb') if compressed else raw
        try:
            with tarfile.open(fileobj=stream, mode='r|*') as archive:
                header = archive.next()
                if header is None or header.name != 'MANIFEST.json' or not header.isreg() or not 0 <= header.size <= 2 * 1024 ** 3:
                    raise ValueError("missing or excessively large archive manifest")
                encoded = archive.extractfile(header).read()
                manifest_digest = hashlib.sha256(encoded).hexdigest()
                manifest = json.loads(encoded)
                if not isinstance(manifest, dict) or set(manifest) != {"schema", "files"} or manifest['schema'] != ARCHIVE_SCHEMA or not isinstance(manifest['files'], list):
                    raise ValueError("unknown archive manifest")
                canonical = hashlib.sha256(b'{"files":[')
                for index, entry in enumerate(manifest['files']):
                    if index: canonical.update(b',')
                    canonical.update(canonical_json(entry).encode())
                canonical.update(b'],"schema":' + canonical_json(manifest['schema']).encode() + b'}\n')
                if canonical.hexdigest() != manifest_digest:
                    raise ValueError("archive manifest has noncanonical JSON")
                del encoded
                archive.members.clear()
                total = 0; previous = ''; count = 0
                for entry in manifest['files']:
                    if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256", "mode", "mtime_ns"}:
                        raise ValueError("malformed archive file entry")
                    name = _safe_name(entry['path'])
                    if name <= previous:
                        raise ValueError('archive file set differs from manifest')
                    previous = name
                    if type(entry['size']) is not int or entry['size'] < 0 or type(entry['mode']) is not int or not 0 <= entry['mode'] <= 0o777:
                        raise ValueError('invalid archive size or permissions')
                    if type(entry['mtime_ns']) is not int:
                        raise ValueError('invalid archive modification time')
                    if not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
                        raise ValueError('invalid archive checksum')
                    member = archive.next()
                    if member is None or not member.isreg() or member.name != name or member.size != entry['size']:
                        raise ValueError('archive file missing or size differs')
                    if requested is not None and (count >= len(requested) or requested[count] != name):
                        raise ValueError('archive file set differs from requested files')
                    digest = hashlib.sha256(); size = 0
                    with archive.extractfile(member) as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b''):
                            digest.update(block); size += len(block)
                    if size != entry['size'] or digest.hexdigest() != entry['sha256']:
                        raise ValueError('archive file checksum differs')
                    total += size; count += 1
                    archive.members.clear()
                if archive.next() is not None:
                    raise ValueError('archive file set differs from manifest')
                if requested is not None and count != len(requested):
                    raise ValueError('archive file set differs from requested files')
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                if any(block): raise ValueError('archive has data after its final member')
        finally:
            if compressed: stream.close()
    after = source.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('archive changed during verification')
    receipt = {'path': str(source), 'sha256': archive_digest, 'members': count, 'bytes': total,
               'schema': ARCHIVE_SCHEMA, 'manifest_sha256': manifest_digest, 'verified': True}
    if include_manifest: receipt['manifest'] = manifest
    return receipt


def recover_archive(path, destination, expected_archive_sha256=None):
    receipt = verify_archive(path, expected_archive_sha256=expected_archive_sha256)
    target = Path(destination).absolute()
    if target.exists():
        if inventory(target) != receipt['manifest']:
            raise FileExistsError('existing recovery contains different files')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f'.{target.name}.', dir=target.parent))
    try:
        with tarfile.open(path, 'r|*') as archive:
            header = archive.next()
            if header is None or header.name != 'MANIFEST.json' or not header.isreg():
                raise ValueError('archive changed before recovery')
            digest = hashlib.sha256()
            with archive.extractfile(header) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''): digest.update(block)
            if digest.hexdigest() != receipt['manifest_sha256']:
                raise ValueError('archive manifest changed before recovery')
            archive.members.clear()
            for entry in receipt['manifest']['files']:
                member = archive.next()
                if member is None or not member.isreg() or member.name != entry['path'] or member.size != entry['size']:
                    raise ValueError('archive changed before recovery')
                output = temporary / entry['path']
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, output.open('xb') as handle:
                    shutil.copyfileobj(source, handle, 1024 * 1024)
                    handle.flush(); os.fsync(handle.fileno())
                os.chmod(output, entry['mode'])
                os.utime(output, ns=(entry['mtime_ns'], entry['mtime_ns']))
                archive.members.clear()
            if archive.next() is not None:
                raise ValueError('archive changed before recovery')
        if inventory(temporary) != receipt['manifest']:
            raise ValueError('recovered files differ from the archive')
        publish(temporary, target)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    return target


def convert_copy(source, destination, old_root=None, new_root=None, invalidations=None):
    """Change only an explicit stopped SQLite copy, retaining original labels."""
    if (old_root is None) != (new_root is None):
        raise ValueError("both old_root and new_root are needed")
    with Results(source, create=False) as reader:
        report = reader.inspect()
        for table in ("tasks", "trials", "sub_runs"):
            if table in reader.tables:
                for row in reader.rows(table):
                    if row.get("status", row.get("run_state")) in ("running", "planned"):
                        raise ValueError("conversion requires a stopped result copy")
        target = reader.backup(destination)
    changes = []
    try:
        with sqlite3.connect(target) as connection:
            for table in report["tables"]:
                if not table.replace("_", "").isalnum():
                    continue
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                path_columns = [name for name in columns if name.endswith(("_path", "_dir")) or name in ("path", "run_dir")]
                if old_root is not None:
                    old = Path(old_root).absolute()
                    new = Path(new_root).absolute()
                    for name in path_columns:
                        for rowid, value in connection.execute(f'SELECT rowid,"{name}" FROM "{table}"'):
                            if not isinstance(value, str) or not Path(value).is_absolute():
                                continue
                            try:
                                relative = Path(value).relative_to(old)
                            except ValueError:
                                continue
                            if ".." in relative.parts:
                                raise ValueError("stored path escapes its declared root")
                            mapped = str(new / relative)
                            connection.execute(f'UPDATE "{table}" SET "{name}"=? WHERE rowid=?', (mapped, rowid))
                            changes.append({"table": table, "rowid": rowid, "column": name, "old": value, "new": mapped})
            connection.execute("CREATE TABLE IF NOT EXISTS conversion_notes (created REAL, input_json TEXT NOT NULL)")
            connection.execute("INSERT INTO conversion_notes VALUES(?,?)", (time.time(), canonical_json({
                "source": str(Path(source).absolute()), "source_sha256": sha256_file(source),
                "path_changes": changes, "invalidations": invalidations or []})))
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("converted copy failed integrity checking")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"path": str(target), "changes": changes, "invalidations": invalidations or []}
