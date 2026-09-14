"""Read-only access and inventory tools for the numbered-stage data tree.

Nothing in this module imports the legacy ledger implementation.  That is
intentional: constructing the old ``Ledger`` class can create tables and run
``ALTER TABLE`` migrations.  Here SQLite is opened with ``mode=ro`` and
``query_only`` and every filesystem operation is observational unless the
caller explicitly requests a backup or inventory export to a new path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Sequence


class LegacyError(RuntimeError):
    """A legacy source is unsafe, inconsistent, or unsupported."""


class UnsafeLegacyPath(LegacyError):
    """A stored path cannot be safely mapped below its declared root."""


def sha256_file(path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory_hash(path: Path, expected: os.stat_result) -> str:
    """Hash one regular file and refuse a concurrent replacement or write."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        signature = lambda value: (
            value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            stat.S_IMODE(value.st_mode),
        )
        if signature(opened) != signature(expected) or not stat.S_ISREG(opened.st_mode):
            raise LegacyError(f"file changed while inventorying: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if signature(after) != signature(opened) or signature(current) != signature(opened):
            raise LegacyError(f"file changed while inventorying: {path}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_relative(path: str) -> str:
    if not path or "\x00" in path or "\\" in path:
        raise UnsafeLegacyPath(f"unsafe relative path: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise UnsafeLegacyPath(f"non-canonical relative path: {path!r}")
    value = PurePosixPath(path)
    if value.is_absolute() or any(part in ("", ".", "..") for part in value.parts):
        raise UnsafeLegacyPath(f"unsafe relative path: {path!r}")
    return value.as_posix()


def remap_absolute_path(stored_path: str, recorded_root: os.PathLike[str] | str,
                        actual_root: os.PathLike[str] | str) -> Path:
    """Map one old absolute path without rewriting or trusting string prefixes."""

    if not os.path.isabs(stored_path) or not os.path.isabs(os.fspath(recorded_root)):
        raise UnsafeLegacyPath("stored path and recorded root must both be absolute")
    old_root = Path(os.path.normpath(os.fspath(recorded_root)))
    old_path = Path(os.path.normpath(stored_path))
    try:
        relative = old_path.relative_to(old_root)
    except ValueError as exc:
        raise UnsafeLegacyPath(
            f"stored path {stored_path!r} is outside {str(old_root)!r}"
        ) from exc
    relative_text = _safe_relative(relative.as_posix())
    new_root = Path(actual_root).resolve()
    candidate = (new_root / relative_text).resolve(strict=False)
    try:
        candidate.relative_to(new_root)
    except ValueError as exc:
        raise UnsafeLegacyPath("mapped path escapes the actual root") from exc
    return candidate


class LegacyLedger:
    """A legacy SQLite reader which cannot create or migrate schema."""

    def __init__(self, path: os.PathLike[str] | str, *, static_snapshot: bool = False):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise LegacyError(f"legacy ledger does not exist: {self.path}")
        wal = Path(str(self.path) + "-wal")
        if static_snapshot and wal.exists() and wal.stat().st_size:
            raise LegacyError("immutable mode would ignore the non-empty WAL")
        uri = self.path.as_uri() + "?mode=ro"
        if static_snapshot:
            uri += "&immutable=1"
        self._connection = sqlite3.connect(uri, uri=True, isolation_level=None,
                                           timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only=ON")
        self._tables = frozenset(
            row[0] for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "LegacyLedger":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(sorted(self._tables))

    def columns(self, table: str) -> tuple[str, ...]:
        if table not in self._tables:
            raise LegacyError(f"missing legacy table: {table}")
        # The table name came from sqlite_master, not untrusted caller text.
        return tuple(row[1] for row in self._connection.execute(
            f"PRAGMA table_info({table})"
        ))

    def rows(self, table: str) -> Iterator[Mapping[str, Any]]:
        if table not in self._tables:
            raise LegacyError(f"missing legacy table: {table}")
        order = "trial_id"
        columns = self.columns(table)
        if table == "sub_runs" and {"replica", "position"}.issubset(columns):
            order = "trial_id, replica, position"
        elif "trial_id" not in columns:
            order = "rowid"
        for row in self._connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            yield dict(row)

    def trials(self) -> Iterator[Mapping[str, Any]]:
        return self.rows("trials")

    def sub_runs(self) -> Iterator[Mapping[str, Any]]:
        return self.rows("sub_runs")

    def active_trials(self) -> tuple[Mapping[str, Any], ...]:
        if "trials" not in self._tables or "status" not in self.columns("trials"):
            return ()
        return tuple(dict(row) for row in self._connection.execute(
            "SELECT * FROM trials WHERE status IN ('planned','running') "
            "ORDER BY trial_id"
        ))

    def integrity_report(self) -> Mapping[str, Any]:
        return {
            "integrity": self._connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": tuple(
                tuple(row) for row in self._connection.execute("PRAGMA foreign_key_check")
            ),
            "user_version": self._connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": self.tables,
        }

    def backup_to(self, destination: os.PathLike[str] | str) -> Path:
        """Create a consistent logical backup at a new explicit destination."""

        target = Path(destination).resolve()
        if target.exists():
            raise LegacyError(f"backup destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".partial", dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            output = sqlite3.connect(temporary)
            try:
                self._connection.backup(output)
                output.commit()
                if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise LegacyError("logical SQLite backup failed integrity_check")
            finally:
                output.close()
            with open(temporary, "rb") as handle:
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise LegacyError(f"backup destination appeared: {target}") from exc
            temporary.unlink()
            _fsync_directory(target.parent)
            return target
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


@dataclass(frozen=True)
class GitEntry:
    path: str
    mode: str
    object_id: str
    object_type: str


_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


class GitTree:
    """Read blobs from a Git tree without checkout, reset, or index writes."""

    def __init__(self, repository: os.PathLike[str] | str,
                 revision: str = "HEAD"):
        self.repository = Path(repository).resolve()
        if not (self.repository / ".git").exists():
            raise LegacyError(f"not a Git work tree: {self.repository}")
        if not _REVISION.fullmatch(revision) or revision.startswith("-") or ".." in revision:
            raise LegacyError(f"unsafe Git revision: {revision!r}")
        self.revision = revision

    def _run(self, arguments: Sequence[str], *, binary: bool = True) -> bytes | str:
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=False, capture_output=True, env=environment,
        )
        if result.returncode:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise LegacyError(f"git {' '.join(arguments[:2])} failed: {message}")
        return result.stdout if binary else result.stdout.decode("utf-8")

    def entries(self, prefix: str = "") -> tuple[GitEntry, ...]:
        clean_prefix = "" if not prefix else _safe_relative(prefix.rstrip("/"))
        arguments = ["ls-tree", "-r", "-z", self.revision]
        if clean_prefix:
            arguments.extend(["--", clean_prefix])
        raw = self._run(arguments)
        assert isinstance(raw, bytes)
        entries: list[GitEntry] = []
        for item in raw.split(b"\0"):
            if not item:
                continue
            header, raw_path = item.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split()
            path = raw_path.decode("utf-8", "surrogateescape")
            entries.append(GitEntry(_safe_relative(path), mode, object_id, object_type))
        return tuple(sorted(entries, key=lambda item: item.path))

    def blob_size(self, object_id: str) -> int:
        return self.blob_metadata((object_id,))[object_id][0]

    def blob_sha256(self, object_id: str) -> str:
        return self.blob_metadata((object_id,))[object_id][1]

    def blob_metadata(self, object_ids: Iterable[str]) -> Mapping[str, tuple[int, str]]:
        """Stream sizes and SHA-256 values through one ``git cat-file`` process."""

        identifiers = tuple(dict.fromkeys(object_ids))
        if not identifiers:
            return {}
        if any(not re.fullmatch(r"[0-9a-fA-F]+", value) for value in identifiers):
            raise LegacyError("Git object IDs must be hexadecimal")
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        process = subprocess.Popen(
            ["git", "-C", str(self.repository), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        output: dict[str, tuple[int, str]] = {}
        try:
            for object_id in identifiers:
                process.stdin.write(object_id.encode("ascii") + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().decode("ascii", "replace").strip()
                fields = header.split()
                if len(fields) != 3 or fields[1] != "blob":
                    raise LegacyError(f"Git object is not a readable blob: {header}")
                size = int(fields[2])
                remaining = size
                digest = hashlib.sha256()
                while remaining:
                    block = process.stdout.read(min(1024 * 1024, remaining))
                    if not block:
                        raise LegacyError("git cat-file ended inside a blob")
                    remaining -= len(block)
                    digest.update(block)
                if process.stdout.read(1) != b"\n":
                    raise LegacyError("git cat-file blob separator is malformed")
                output[object_id] = (size, digest.hexdigest())
            process.stdin.close()
            process.stdout.close()
            stderr = process.stderr.read() if process.stderr is not None else b""
            if process.stderr is not None:
                process.stderr.close()
            return_code = process.wait()
            if return_code:
                raise LegacyError(stderr.decode("utf-8", "replace").strip())
            return output
        except BaseException:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                process.stdout.close()
            except OSError:
                pass
            process.kill()
            process.wait()
            if process.stderr is not None:
                process.stderr.close()
            raise


@dataclass(frozen=True)
class InventoryRecord:
    path: str
    role: str
    file_type: str
    availability: str
    git_state: str
    size: int | None
    mtime_ns: int | None
    sha256: str | None
    git_object_id: str | None = None
    git_sha256: str | None = None


@dataclass(frozen=True)
class Inventory:
    root: str
    records: tuple[InventoryRecord, ...]

    @property
    def digest(self) -> str:
        payload = [asdict(record) for record in self.records]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def classify_legacy_artifact(path: str) -> str:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if "macros" in parts and name.endswith(".mac"):
        return "task-macro"
    if "hits" in parts and name.endswith(".done"):
        return "task-completion-marker"
    if "hits" in parts and name.endswith("_hitsfile.txt"):
        return "task-hits"
    if "logs" in parts and name.endswith(".log"):
        return "task-log"
    if "CrystalMaps" in parts and name == "config.txt":
        return "lattice-config"
    if name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")):
        return "ledger"
    if name.endswith((".json", ".csv", ".yaml", ".yml")):
        return "result-or-metadata"
    if name.endswith((".png", ".svg", ".pdf")):
        return "plot"
    return "other"


def _filesystem_records(root: Path) -> dict[str, InventoryRecord]:
    records: dict[str, InventoryRecord] = {}
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        # Record and do not traverse symlinked directories.
        for name in tuple(dir_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                dir_names.remove(name)
                relative = candidate.relative_to(root).as_posix()
                info = os.lstat(candidate)
                target = os.readlink(candidate).encode("utf-8", "surrogateescape")
                records[relative] = InventoryRecord(
                    relative, classify_legacy_artifact(relative), "symlink",
                    "present", "untracked", info.st_size, info.st_mtime_ns,
                    hashlib.sha256(target).hexdigest(),
                )
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            _safe_relative(relative)
            info = os.lstat(candidate)
            if stat.S_ISREG(info.st_mode):
                file_type = "regular"
                digest = _inventory_hash(candidate, info)
            elif stat.S_ISLNK(info.st_mode):
                file_type = "symlink"
                target = os.readlink(candidate).encode("utf-8", "surrogateescape")
                digest = hashlib.sha256(target).hexdigest()
            else:
                file_type = "special"
                digest = None
            records[relative] = InventoryRecord(
                relative, classify_legacy_artifact(relative), file_type,
                "present", "untracked", info.st_size, info.st_mtime_ns, digest,
            )
    return records


def build_inventory(root: os.PathLike[str] | str, *,
                    git_tree: GitTree | None = None,
                    git_prefix: str = "") -> Inventory:
    """Inventory current files and Git-only files without modifying either."""

    source = Path(root).resolve()
    if not source.is_dir() or source.is_symlink():
        raise LegacyError(f"inventory root must be a real directory: {source}")
    records = _filesystem_records(source)
    if git_tree is not None:
        clean_prefix = "" if not git_prefix else _safe_relative(git_prefix.rstrip("/"))
        tree_root = (git_tree.repository / clean_prefix).resolve()
        if tree_root != source:
            raise LegacyError(
                f"Git prefix {clean_prefix!r} resolves to {tree_root}, not inventory root {source}"
            )
        prefix_with_slash = clean_prefix + "/" if clean_prefix else ""
        entries = tuple(entry for entry in git_tree.entries(clean_prefix)
                        if entry.object_type == "blob"
                        and entry.path.startswith(prefix_with_slash))
        blob_metadata = git_tree.blob_metadata(entry.object_id for entry in entries)
        for entry in entries:
            if entry.object_type != "blob" or not entry.path.startswith(prefix_with_slash):
                continue
            relative = entry.path[len(prefix_with_slash):]
            _safe_relative(relative)
            git_size, git_digest = blob_metadata[entry.object_id]
            present = records.get(relative)
            if present is None:
                records[relative] = InventoryRecord(
                    relative, classify_legacy_artifact(relative),
                    "symlink" if entry.mode == "120000" else "regular",
                    "git-only", "deleted", git_size,
                    None, git_digest, entry.object_id, git_digest,
                )
            else:
                git_state = "same" if present.sha256 == git_digest else "modified"
                records[relative] = InventoryRecord(
                    present.path, present.role, present.file_type,
                    present.availability, git_state, present.size, present.mtime_ns,
                    present.sha256, entry.object_id, git_digest,
                )
    return Inventory(str(source), tuple(records[key] for key in sorted(records)))


_INVENTORY_SCHEMA_VERSION = 1


def read_inventory_sqlite(path: os.PathLike[str] | str) -> Inventory:
    """Read and self-check an inventory database without changing it."""

    source = Path(path).resolve()
    if not source.is_file():
        raise LegacyError(f"inventory does not exist: {source}")
    connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != _INVENTORY_SCHEMA_VERSION:
            raise LegacyError(f"unsupported inventory schema version: {version}")
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        if (set(metadata) != {"schema", "root", "digest", "records"}
                or metadata["schema"] != "material-scan-legacy-inventory-1"):
            raise LegacyError("inventory metadata schema is malformed")
        rows = connection.execute("SELECT * FROM artifact ORDER BY path").fetchall()
        records = tuple(InventoryRecord(**dict(row)) for row in rows)
        inventory = Inventory(metadata["root"], records)
        if int(metadata["records"]) != len(records) or metadata["digest"] != inventory.digest:
            raise LegacyError("inventory metadata does not match its artifact rows")
        return inventory
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise LegacyError(f"malformed inventory database: {source}") from exc
    finally:
        connection.close()


def write_inventory_sqlite(inventory: Inventory,
                           destination: os.PathLike[str] | str) -> Path:
    """Atomically publish the scalable file inventory to a new SQLite file.

    The operation is idempotent only for identical content.  It never upgrades
    or overwrites an existing, different inventory.
    """

    target = Path(destination).resolve()
    inventory_root = Path(inventory.root).resolve()
    try:
        target.relative_to(inventory_root)
    except ValueError:
        pass
    else:
        raise LegacyError("inventory database must be outside the inventoried tree")
    if target.exists():
        existing = read_inventory_sqlite(target)
        if existing.digest != inventory.digest or existing.root != inventory.root:
            raise LegacyError(f"inventory destination has different content: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".partial", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE artifact (
                path          TEXT PRIMARY KEY,
                role          TEXT NOT NULL,
                file_type     TEXT NOT NULL,
                availability  TEXT NOT NULL,
                git_state     TEXT NOT NULL,
                size          INTEGER,
                mtime_ns      INTEGER,
                sha256        TEXT,
                git_object_id TEXT,
                git_sha256    TEXT
            );
        """)
        connection.executemany(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (("schema", "material-scan-legacy-inventory-1"),
             ("root", inventory.root),
             ("digest", inventory.digest),
             ("records", str(len(inventory.records)))),
        )
        connection.executemany(
            "INSERT INTO artifact "
            "(path,role,file_type,availability,git_state,size,mtime_ns,sha256,"
            "git_object_id,git_sha256) VALUES "
            "(:path,:role,:file_type,:availability,:git_state,:size,:mtime_ns,"
            ":sha256,:git_object_id,:git_sha256)",
            (asdict(record) for record in inventory.records),
        )
        connection.execute(f"PRAGMA user_version={_INVENTORY_SCHEMA_VERSION}")
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise LegacyError("new inventory failed SQLite integrity_check")
        connection.close()
        connection = None
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            existing = read_inventory_sqlite(target)
            if existing.digest != inventory.digest or existing.root != inventory.root:
                raise LegacyError(f"inventory destination appeared with different content: {target}")
            temporary.unlink()
            return target
        temporary.unlink()
        _fsync_directory(target.parent)
        # Re-open through the read-only validator before reporting success.
        read_inventory_sqlite(target)
        return target
    except BaseException:
        if connection is not None:
            connection.close()
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
