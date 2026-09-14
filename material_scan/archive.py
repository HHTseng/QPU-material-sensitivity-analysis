"""Deterministic, verified archives for completed legacy or new trials.

Archiving is never triggered by import, scoring, or task completion.  A caller
must invoke :func:`create_archive` explicitly.  Publication is atomic: a
temporary archive is completely written and verified before it is renamed to
the requested destination.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


MANIFEST_NAME = "MANIFEST.json"
ARCHIVE_SCHEMA = "material-scan-archive-1"
MAX_MANIFEST_BYTES = 256 * 1024 * 1024


class ArchiveError(RuntimeError):
    """An archive or requested archive member is unsafe or inconsistent."""


class UnsafeArchivePath(ArchiveError):
    """An absolute, traversing, ambiguous, or linked path was supplied."""


class ArchiveConflict(ArchiveError):
    """An existing destination does not contain the requested content."""


@dataclass(frozen=True)
class ArchiveReceipt:
    path: Path
    sha256: str
    members: int
    bytes: int
    manifest: Mapping[str, Any]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _member_name(value: os.PathLike[str] | str) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise UnsafeArchivePath(f"archive member is not a path: {value!r}") from exc
    if not isinstance(raw, str):
        raise UnsafeArchivePath("archive member must be text, not bytes")
    if not raw or "\x00" in raw or "\\" in raw:
        raise UnsafeArchivePath(f"unsafe archive member: {raw!r}")
    if any(part in ("", ".", "..") for part in raw.split("/")):
        raise UnsafeArchivePath(f"non-canonical archive member: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UnsafeArchivePath(f"unsafe archive member: {raw!r}")
    name = path.as_posix()
    if name == MANIFEST_NAME:
        raise UnsafeArchivePath(f"{MANIFEST_NAME} is reserved")
    return name


def _source_file(root: Path, member: str) -> tuple[Path, os.stat_result]:
    current = root
    for part in PurePosixPath(member).parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError as exc:
            raise ArchiveError(f"archive source is missing: {member}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise UnsafeArchivePath(f"archive source uses a symlink: {member}")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafeArchivePath(f"archive source escapes its root: {member}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise UnsafeArchivePath(f"archive source is not a regular file: {member}")
    return resolved, info


def _entry(root: Path, member: str) -> Mapping[str, Any]:
    source, info = _source_file(root, member)
    mode = stat.S_IMODE(info.st_mode)
    if mode > 0o777:
        raise UnsafeArchivePath(f"archive source has special permission bits: {member}")
    return {
        "path": member,
        "size": int(info.st_size),
        "sha256": _sha256_file(source),
        "mode": mode,
        "mtime_ns": int(info.st_mtime_ns),
    }


def _manifest(entries: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return {
        "schema": ARCHIVE_SCHEMA,
        "files": list(entries),
    }


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = 0
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.pax_headers = {}
    return info


def _write_tar(root: Path, entries: Sequence[Mapping[str, Any]], output: Path,
               compression: str) -> None:
    raw = open(output, "wb")
    compressor: gzip.GzipFile | None = None
    try:
        if compression == "gzip":
            compressor = gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                       compresslevel=6, mtime=0)
            stream = compressor
        elif compression == "none":
            stream = raw
        else:
            raise ValueError("compression must be 'gzip' or 'none'")
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            encoded_manifest = _manifest_bytes(_manifest(entries))
            archive.addfile(_tar_info(MANIFEST_NAME, len(encoded_manifest)),
                            io.BytesIO(encoded_manifest))
            for entry in entries:
                source, before = _source_file(root, entry["path"])
                before_signature = (
                    int(before.st_size), int(before.st_mtime_ns),
                    stat.S_IMODE(before.st_mode),
                )
                expected_signature = (
                    entry["size"], entry["mtime_ns"], entry["mode"],
                )
                if before_signature != expected_signature:
                    raise ArchiveError(f"archive source changed before reading: {entry['path']}")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(source, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    opened_signature = (
                        int(opened.st_size), int(opened.st_mtime_ns),
                        stat.S_IMODE(opened.st_mode),
                    )
                    if opened_signature != expected_signature:
                        raise ArchiveError(
                            f"archive source changed while opening: {entry['path']}"
                        )
                    archive.addfile(_tar_info(entry["path"], entry["size"]), handle)
                    after = os.fstat(handle.fileno())
                    if (int(after.st_size), int(after.st_mtime_ns),
                            stat.S_IMODE(after.st_mode)) != expected_signature:
                        raise ArchiveError(
                            f"archive source changed while reading: {entry['path']}"
                        )
        if compressor is not None:
            compressor.close()
        raw.flush()
        os.fsync(raw.fileno())
    finally:
        if compressor is not None and not compressor.closed:
            compressor.close()
        raw.close()


def create_archive(source_root: os.PathLike[str] | str,
                   members: Iterable[os.PathLike[str] | str],
                   destination: os.PathLike[str] | str, *,
                   compression: str = "gzip") -> ArchiveReceipt:
    """Explicitly create and verify an archive without overwriting evidence.

    Calling this function again with the same source and destination is safe:
    the existing archive is returned only if its manifest is identical.
    """

    root = Path(source_root).resolve()
    if not root.is_dir() or Path(source_root).is_symlink():
        raise UnsafeArchivePath(f"source root must be a real directory: {root}")
    names = tuple(sorted(_member_name(item) for item in members))
    if len(names) != len(set(names)):
        raise ArchiveError("duplicate archive member")
    entries = tuple(_entry(root, name) for name in names)
    expected_manifest = _manifest(entries)
    target = Path(destination).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        pass
    else:
        raise ArchiveError("archive destination must be outside its source tree")
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        receipt = verify_archive(target, expected_paths=names)
        if receipt.manifest != expected_manifest:
            raise ArchiveConflict(f"existing archive has different content: {target}")
        return receipt

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".partial", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _write_tar(root, entries, temporary, compression)
        receipt = verify_archive(temporary, expected_paths=names)
        if receipt.manifest != expected_manifest:
            raise ArchiveError("archive changed between inventory and verification")
        # Refuse a destination which appeared while the temporary was built.
        if target.exists():
            existing = verify_archive(target, expected_paths=names)
            if existing.manifest != expected_manifest:
                raise ArchiveConflict(f"destination appeared with different content: {target}")
            temporary.unlink()
            return existing
        try:
            os.link(temporary, target)
        except FileExistsError:
            existing = verify_archive(target, expected_paths=names)
            if existing.manifest != expected_manifest:
                raise ArchiveConflict(f"destination appeared with different content: {target}")
            temporary.unlink()
            return existing
        temporary.unlink()
        _fsync_directory(target.parent)
        return verify_archive(target, expected_paths=names,
                              expected_archive_sha256=receipt.sha256)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _validated_manifest(value: Any) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    if not isinstance(value, dict) or set(value) != {"schema", "files"}:
        raise ArchiveError("archive manifest has unexpected fields")
    if value["schema"] != ARCHIVE_SCHEMA or not isinstance(value["files"], list):
        raise ArchiveError("archive manifest schema is unsupported")
    entries: list[Mapping[str, Any]] = []
    names: list[str] = []
    required = {"path", "size", "sha256", "mode", "mtime_ns"}
    for raw in value["files"]:
        if not isinstance(raw, dict) or set(raw) != required:
            raise ArchiveError("archive manifest file entry is malformed")
        name = _member_name(raw["path"])
        if not isinstance(raw["size"], int) or raw["size"] < 0:
            raise ArchiveError(f"invalid size for {name}")
        if (not isinstance(raw["sha256"], str)
                or not re_full_sha256(raw["sha256"])):
            raise ArchiveError(f"invalid SHA-256 for {name}")
        if not isinstance(raw["mode"], int) or not 0 <= raw["mode"] <= 0o777:
            raise ArchiveError(f"invalid mode for {name}")
        if not isinstance(raw["mtime_ns"], int):
            raise ArchiveError(f"invalid mtime for {name}")
        entries.append(raw)
        names.append(name)
    if names != sorted(names) or len(names) != len(set(names)):
        raise ArchiveError("manifest paths must be sorted and unique")
    return value, tuple(entries)


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def verify_archive(path: os.PathLike[str] | str, *,
                   expected_paths: Iterable[str] | None = None,
                   expected_archive_sha256: str | None = None) -> ArchiveReceipt:
    """Verify container checksum, safe members, exact manifest, and file bytes."""

    source = Path(path).resolve()
    if not source.is_file() or source.is_symlink():
        raise ArchiveError(f"archive is not a regular file: {source}")
    archive_digest = _sha256_file(source)
    if expected_archive_sha256 is not None and archive_digest != expected_archive_sha256:
        raise ArchiveError("whole-archive SHA-256 does not match its receipt")
    try:
        with tarfile.open(source, mode="r:*") as archive:
            members = archive.getmembers()
            names: list[str] = []
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                if member.name == MANIFEST_NAME:
                    name = MANIFEST_NAME
                else:
                    name = _member_name(member.name)
                if name in by_name:
                    raise ArchiveError(f"duplicate tar member: {name}")
                if not member.isreg() or member.issym() or member.islnk():
                    raise UnsafeArchivePath(f"non-regular tar member: {name}")
                names.append(name)
                by_name[name] = member
            if names.count(MANIFEST_NAME) != 1:
                raise ArchiveError("archive must contain exactly one manifest")
            if by_name[MANIFEST_NAME].size > MAX_MANIFEST_BYTES:
                raise ArchiveError("archive manifest is unreasonably large")
            manifest_handle = archive.extractfile(by_name[MANIFEST_NAME])
            if manifest_handle is None:
                raise ArchiveError("manifest is unreadable")
            try:
                raw_manifest = manifest_handle.read()
                parsed = json.loads(raw_manifest.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchiveError("manifest is not valid canonical JSON") from exc
            manifest, entries = _validated_manifest(parsed)
            if raw_manifest != _manifest_bytes(manifest):
                raise ArchiveError("manifest encoding is not canonical")
            declared = tuple(entry["path"] for entry in entries)
            actual = tuple(sorted(name for name in names if name != MANIFEST_NAME))
            if actual != declared:
                missing = sorted(set(declared) - set(actual))
                extra = sorted(set(actual) - set(declared))
                raise ArchiveError(f"archive member mismatch; missing={missing}, extra={extra}")
            if expected_paths is not None:
                expected = tuple(sorted(_member_name(name) for name in expected_paths))
                if declared != expected:
                    raise ArchiveError(
                        f"archive paths differ from expectation; expected={expected}, got={declared}"
                    )
            total_bytes = 0
            for entry in entries:
                member = by_name[entry["path"]]
                if member.size != entry["size"]:
                    raise ArchiveError(f"tar size differs from manifest: {entry['path']}")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ArchiveError(f"unreadable archive member: {entry['path']}")
                digest = hashlib.sha256()
                read_bytes = 0
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    read_bytes += len(block)
                    digest.update(block)
                if read_bytes != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise ArchiveError(f"content checksum differs: {entry['path']}")
                total_bytes += read_bytes
    except (tarfile.TarError, OSError, EOFError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"cannot read archive {source}: {exc}") from exc
    return ArchiveReceipt(source, archive_digest, len(entries), total_bytes, manifest)


def verify_tree(root: os.PathLike[str] | str,
                manifest: Mapping[str, Any]) -> None:
    """Require a recovered tree to contain exactly the manifested files."""

    destination = Path(root).resolve()
    if not destination.is_dir() or Path(root).is_symlink():
        raise ArchiveError(f"recovery destination is not a real directory: {destination}")
    _, entries = _validated_manifest(manifest)
    expected = {entry["path"]: entry for entry in entries}
    actual: dict[str, Path] = {}
    for directory, dir_names, file_names in os.walk(destination, followlinks=False):
        directory_path = Path(directory)
        for name in dir_names:
            if (directory_path / name).is_symlink():
                raise UnsafeArchivePath("recovered tree contains a symlink")
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(destination).as_posix()
            _member_name(relative)
            if candidate.is_symlink() or not candidate.is_file():
                raise UnsafeArchivePath(f"recovered member is not regular: {relative}")
            actual[relative] = candidate
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ArchiveError(f"recovered tree mismatch; missing={missing}, extra={extra}")
    for name, entry in expected.items():
        candidate = actual[name]
        if candidate.stat().st_size != entry["size"] or _sha256_file(candidate) != entry["sha256"]:
            raise ArchiveError(f"recovered content differs: {name}")
        if stat.S_IMODE(candidate.stat().st_mode) != entry["mode"]:
            raise ArchiveError(f"recovered mode differs: {name}")


def recover_archive(path: os.PathLike[str] | str,
                    destination: os.PathLike[str] | str, *,
                    expected_archive_sha256: str | None = None) -> Path:
    """Verify, recover to a sibling staging directory, then publish atomically."""

    receipt = verify_archive(path, expected_archive_sha256=expected_archive_sha256)
    target = Path(destination).resolve()
    if target.exists():
        try:
            verify_tree(target, receipt.manifest)
        except ArchiveError as exc:
            raise ArchiveConflict(f"existing recovery differs: {target}") from exc
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.partial-", dir=target.parent))
    try:
        _, entries = _validated_manifest(receipt.manifest)
        with tarfile.open(Path(path).resolve(), mode="r:*") as archive:
            by_name = {member.name: member for member in archive.getmembers()}
            for entry in entries:
                output = staging.joinpath(*PurePosixPath(entry["path"]).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(by_name[entry["path"]])
                if source is None:
                    raise ArchiveError(f"unreadable member during recovery: {entry['path']}")
                descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                                     entry["mode"] or 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(output, entry["mode"])
                os.utime(output, ns=(entry["mtime_ns"], entry["mtime_ns"]))
        verify_tree(staging, receipt.manifest)
        if target.exists():
            raise ArchiveConflict(f"recovery destination appeared: {target}")
        os.replace(staging, target)
        _fsync_directory(target.parent)
        return target
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
