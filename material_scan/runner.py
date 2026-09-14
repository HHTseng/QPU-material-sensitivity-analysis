"""Run one already-resolved simulation task.

This module knows about processes and files, not candidates, objectives, SQL,
or optimizer policy.  The controller must render the final macro before calling
``run_task`` and must commit the returned checksums to the store itself.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


class RunError(RuntimeError):
    """A task did not produce a publishable raw artifact."""


@dataclass(frozen=True)
class RunResult:
    returncode: int
    elapsed_s: float
    output: Path
    output_sha256: str
    output_bytes: int
    log: Path
    log_sha256: str


@dataclass(frozen=True)
class ArtifactState:
    """Read-only evidence available after a worker or controller restart."""

    state: str
    path: Path | None
    sha256: str | None


_NUMERIC_START = tuple("+-.0123456789")


def validate_macro_lines(lines: Sequence[str]) -> list[str]:
    """Return Geant command lines containing malformed numeric tokens.

    This deliberately matches the legacy unit-hygiene rule.  A malformed
    number can make Geant4 skip ``beamOn`` yet exit with status zero.
    """

    offenders: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("/"):
            continue
        tokens = line.split()
        if tokens[0] in ("/control/shell", "/control/execute"):
            continue
        for token in tokens[1:]:
            token = token.rstrip(",")
            if not token or not token.startswith(_NUMERIC_START):
                continue
            try:
                float(token)
            except ValueError:
                offenders.append(line)
                break
    return offenders


def normalized_macro_bytes(text: str) -> bytes:
    """Canonical macro bytes for task identity.

    Only the hit destination and the attempt-local witness are plumbing.  All
    other bytes, including comments, whitespace, command order, seeds, site,
    material commands, and event count remain significant.
    """

    normalized: list[str] = []
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        ending = raw[len(body) :]
        stripped = body.lstrip()
        indent = body[: len(body) - len(stripped)]
        if stripped.startswith("/g4cmp/HitsFile "):
            body = f"{indent}/g4cmp/HitsFile <OUTPUT>"
        elif stripped.startswith("/control/shell touch "):
            body = f"{indent}/control/shell touch <WITNESS>"
        normalized.append(body + ending)
    return "".join(normalized).encode("utf-8")


def sha256_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def validate_nonempty_output(path: Path) -> None:
    """Minimal raw-output gate; a scientific parser may add stricter checks."""

    if not path.is_file():
        raise RunError(f"expected output was not created: {path}")
    if path.stat().st_size == 0:
        raise RunError(f"output is empty: {path}")


def inspect_artifacts(
    *,
    partial_output: Path,
    final_output: Path,
    completion_witness: Path,
    validate: Callable[[Path], None] = validate_nonempty_output,
) -> ArtifactState:
    """Classify an attempt after a controller restart without changing files.

    A valid final file is a publish-before-database-commit case and may be
    reconciled using the same attempt token. A partial file plus witness is
    ready to publish. Every other combination needs inspection or a retry; it
    is never converted to a zero observation.
    """

    partial_output = Path(partial_output)
    final_output = Path(final_output)
    completion_witness = Path(completion_witness)
    if final_output.exists():
        if partial_output.exists():
            return ArtifactState("conflict", None, None)
        try:
            validate(final_output)
        except Exception:
            return ArtifactState("invalid-final", final_output, None)
        return ArtifactState("published-uncommitted", final_output, sha256_file(final_output))
    if partial_output.exists() and completion_witness.exists():
        try:
            validate(partial_output)
        except Exception:
            return ArtifactState("invalid-partial", partial_output, None)
        return ArtifactState("ready-to-publish", partial_output, sha256_file(partial_output))
    if partial_output.exists():
        return ArtifactState("incomplete-partial", partial_output, None)
    if completion_witness.exists():
        return ArtifactState("witness-without-output", None, None)
    return ArtifactState("absent", None, None)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_task(
    command: Sequence[str],
    *,
    partial_output: Path,
    final_output: Path,
    completion_witness: Path,
    log_path: Path,
    timeout_s: float,
    validate: Callable[[Path], None] = validate_nonempty_output,
    environment: dict[str, str] | None = None,
) -> RunResult:
    """Run and atomically publish one task.

    ``command`` is complete and is executed without a shell.  The simulation
    must create both ``partial_output`` and ``completion_witness``.  The witness
    should be written by the macro immediately after ``beamOn``.  Existing
    final output is refused so retries cannot overwrite evidence.
    """

    if not command:
        raise ValueError("command must not be empty")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    partial_output = Path(partial_output)
    final_output = Path(final_output)
    completion_witness = Path(completion_witness)
    log_path = Path(log_path)
    for path in (partial_output, final_output, completion_witness, log_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    if final_output.exists():
        raise RunError(f"refusing to overwrite final output: {final_output}")
    for stale in (partial_output, completion_witness):
        if stale.exists():
            raise RunError(f"attempt path already exists: {stale}")

    started = time.monotonic()
    try:
        with log_path.open("xb") as log:
            completed = subprocess.run(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_s,
                env=environment,
            )
            log.flush()
            os.fsync(log.fileno())
    except subprocess.TimeoutExpired as exc:
        raise RunError(f"task exceeded {timeout_s:g} s") from exc

    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        raise RunError(f"task exited with status {completed.returncode}")
    if not completion_witness.is_file():
        raise RunError("task exited zero without the post-beamOn witness")
    validate(partial_output)

    with partial_output.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(partial_output, final_output)
    _fsync_directory(final_output.parent)
    completion_witness.unlink()

    return RunResult(
        returncode=completed.returncode,
        elapsed_s=elapsed,
        output=final_output,
        output_sha256=sha256_file(final_output),
        output_bytes=final_output.stat().st_size,
        log=log_path,
        log_sha256=sha256_file(log_path),
    )
