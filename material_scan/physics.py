"""Pure hit-to-quasiparticle calculations used by material-scan analyses.

The simulation writes one CSV file per site and replica.  This module reads
those files without knowing anything about campaigns, databases, or directories.
Its numerical choices intentionally match the historical scorer: NumPy's
ties-to-even rounding, the lower-index electrode on a distance tie, and the
lower time bin on a time tie.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np


QP_SNAPSHOT_COUNT = 1001
QP_SNAPSHOT_MAX_NS = 300_000.0

_ENERGY = "Energy Deposited [eV]"
_END_X = "End X [m]"
_END_Y = "End Y [m]"
_END_Z = "End Z [m]"
_FINAL_TIME = "Final Time [ns]"
_REQUIRED_HIT_COLUMNS = (_ENERGY, _END_X, _END_Y, _END_Z, _FINAL_TIME)


class PhysicsError(ValueError):
    """A hit file or physical input cannot be scored."""


@dataclass(frozen=True, slots=True)
class HitScore:
    """Scored contents of one complete hit file.

    ``n_hits`` is the historical database quantity: the number of CSV data rows,
    including rows that deposit no energy on the sensor surface.
    """

    n_hits: int
    snapshot_times_ns: np.ndarray
    qps_by_electrode_time: np.ndarray

    @property
    def per_electrode_qps(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.qps_by_electrode_time.sum(axis=1))

    @property
    def total_qps(self) -> float:
        return float(self.qps_by_electrode_time.sum())


def _positive_finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise PhysicsError(f"{name} must be a positive finite number")
    return value


def _electrodes(
    electrode_x_mm: Sequence[float], electrode_y_mm: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(electrode_x_mm, dtype=np.float64)
    y = np.asarray(electrode_y_mm, dtype=np.float64)
    if x.ndim != 1 or y.ndim != 1 or x.size == 0 or x.shape != y.shape:
        raise PhysicsError("electrode x/y coordinates must be non-empty vectors of equal length")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise PhysicsError("electrode coordinates must be finite")
    return x, y


def _nearest_time_indices(times_ns: np.ndarray, snapshots_ns: np.ndarray) -> np.ndarray:
    """Indices of the nearest ordered snapshots, choosing the lower one on ties."""

    right = np.searchsorted(snapshots_ns, times_ns, side="left")
    right_index = np.clip(right, 0, snapshots_ns.size - 1)
    left_index = np.clip(right - 1, 0, snapshots_ns.size - 1)
    right_is_closer = (
        np.abs(snapshots_ns[right_index] - times_ns)
        < np.abs(times_ns - snapshots_ns[left_index])
    )
    return np.where(right_is_closer, right_index, left_index)


def calculate_qps(
    energy_deposited_eV: Sequence[float] | np.ndarray,
    end_x_m: Sequence[float] | np.ndarray,
    end_y_m: Sequence[float] | np.ndarray,
    end_z_m: Sequence[float] | np.ndarray,
    final_time_ns: Sequence[float] | np.ndarray,
    gap_eV: float,
    electrode_x_mm: Sequence[float],
    electrode_y_mm: Sequence[float],
    chip_surface_z_m: float,
    *,
    snapshot_count: int = QP_SNAPSHOT_COUNT,
    snapshot_max_ns: float = QP_SNAPSHOT_MAX_NS,
) -> tuple[np.ndarray, np.ndarray]:
    """Bin surface-deposited energy into QPs by electrode and arrival time.

    The return shapes are ``(snapshot_count,)`` and
    ``(number_of_electrodes, snapshot_count)``.  Track weights are deliberately
    absent because the historical calculation did not apply them.
    """

    gap = _positive_finite(gap_eV, "gap_eV")
    surface_z = float(chip_surface_z_m)
    if not math.isfinite(surface_z):
        raise PhysicsError("chip_surface_z_m must be finite")
    if not isinstance(snapshot_count, int) or snapshot_count < 2:
        raise PhysicsError("snapshot_count must be an integer of at least two")
    snapshot_max = _positive_finite(snapshot_max_ns, "snapshot_max_ns")
    qx, qy = _electrodes(electrode_x_mm, electrode_y_mm)

    columns = tuple(
        np.asarray(values, dtype=np.float64)
        for values in (energy_deposited_eV, end_x_m, end_y_m, end_z_m, final_time_ns)
    )
    if any(column.ndim != 1 for column in columns):
        raise PhysicsError("hit columns must be one-dimensional")
    lengths = {column.size for column in columns}
    if len(lengths) != 1:
        raise PhysicsError("hit columns have different lengths")
    if any(not np.isfinite(column).all() for column in columns):
        raise PhysicsError("hit columns contain a non-finite value")

    snapshots = np.linspace(0.0, snapshot_max, snapshot_count, dtype=np.float64)
    qps = np.zeros((qx.size, snapshot_count), dtype=np.float64)
    if not columns[0].size:
        return snapshots, qps

    energy, end_x, end_y, end_z, final_time = columns
    selected = (energy > 0.0) & np.isclose(end_z, surface_z)
    if not selected.any():
        return snapshots, qps

    energy = energy[selected]
    end_x = end_x[selected]
    end_y = end_y[selected]
    final_time = final_time[selected]

    # Match the historical expression and np.argmin.  np.argmin returns the first
    # minimum, which fixes an exact distance tie to the lower electrode index.
    distance = (
        (1000.0 * end_x[:, None] - qx[None, :]) ** 2
        + (1000.0 * end_y[:, None] - qy[None, :]) ** 2
    ) ** 0.5
    electrode_index = np.argmin(distance, axis=1)
    time_index = _nearest_time_indices(final_time, snapshots)

    rounded = np.round(energy / gap)
    if not np.isfinite(rounded).all() or np.max(np.abs(rounded), initial=0.0) > 2**53:
        raise PhysicsError("QP count is not exactly representable as a float integer")
    np.add.at(qps, (electrode_index, time_index), rounded)
    return snapshots, qps


def _hit_chunks(path: Path, chunk_rows: int) -> Iterator[tuple[np.ndarray, ...]]:
    if chunk_rows <= 0:
        raise PhysicsError("chunk_rows must be positive")
    if not path.is_file():
        raise PhysicsError(f"hit file does not exist: {path}")
    if path.stat().st_size == 0:
        raise PhysicsError(f"hit file is zero bytes: {path}")

    try:
        handle = path.open("r", newline="", encoding="utf-8")
    except OSError as error:
        raise PhysicsError(f"cannot open hit file {path}: {error}") from error

    with handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        missing = [name for name in _REQUIRED_HIT_COLUMNS if name not in fields]
        if missing:
            raise PhysicsError(f"hit file {path} is missing columns: {missing}")

        batch: list[tuple[float, float, float, float, float]] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                values = tuple(float(row[name]) for name in _REQUIRED_HIT_COLUMNS)
            except (KeyError, TypeError, ValueError) as error:
                raise PhysicsError(
                    f"hit file {path} has invalid numeric data on line {line_number}"
                ) from error
            batch.append(values)
            if len(batch) == chunk_rows:
                array = np.asarray(batch, dtype=np.float64)
                yield tuple(array[:, index] for index in range(array.shape[1]))
                batch.clear()
        if batch:
            array = np.asarray(batch, dtype=np.float64)
            yield tuple(array[:, index] for index in range(array.shape[1]))


def score_hit_file(
    path: str | Path,
    gap_eV: float,
    electrode_x_mm: Sequence[float],
    electrode_y_mm: Sequence[float],
    chip_surface_z_m: float,
    *,
    chunk_rows: int = 65_536,
) -> HitScore:
    """Read and score one validated CSV hit file.

    A header-only CSV is a valid measured zero.  A missing, zero-byte,
    malformed, or non-finite file raises :class:`PhysicsError` and can never be
    converted into a zero observation.
    """

    qx, qy = _electrodes(electrode_x_mm, electrode_y_mm)
    snapshots = np.linspace(0.0, QP_SNAPSHOT_MAX_NS, QP_SNAPSHOT_COUNT)
    qps = np.zeros((qx.size, QP_SNAPSHOT_COUNT), dtype=np.float64)
    n_hits = 0
    for energy, end_x, end_y, end_z, final_time in _hit_chunks(Path(path), chunk_rows):
        chunk_times, chunk_qps = calculate_qps(
            energy,
            end_x,
            end_y,
            end_z,
            final_time,
            gap_eV,
            qx,
            qy,
            chip_surface_z_m,
        )
        if not np.array_equal(chunk_times, snapshots):
            raise AssertionError("internal snapshot grid mismatch")
        qps += chunk_qps
        n_hits += energy.size

    snapshots.setflags(write=False)
    qps.setflags(write=False)
    return HitScore(n_hits=n_hits, snapshot_times_ns=snapshots, qps_by_electrode_time=qps)


def calculate_xqps(
    snapshot_times_ns: Sequence[float] | np.ndarray,
    qps_by_electrode_time: Sequence[Sequence[float]] | np.ndarray,
    aluminum_gap_hz: float,
    qubit_frequency_hz: float,
    recombination: float,
    loss_rate: float,
    injection_scale: float,
    pulse_time_us: float,
    cooper_pairs_per_um3: float,
    electrode_height_um: float,
    electrode_width_um: float,
    film_thickness_um: float,
    simulated_primaries: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Historical quasiparticle-density ODE, kept as one tested implementation."""

    snapshots = np.asarray(snapshot_times_ns, dtype=np.float64)
    qps = np.asarray(qps_by_electrode_time, dtype=np.float64)
    if snapshots.ndim != 1 or snapshots.size < 2 or not np.isfinite(snapshots).all():
        raise PhysicsError("snapshot_times_ns must contain at least two finite values")
    if qps.ndim != 2 or qps.shape[1] != snapshots.size or not np.isfinite(qps).all():
        raise PhysicsError("QP array must be finite and match the snapshot grid")
    if np.any(qps < 0.0):
        raise PhysicsError("QP counts must be non-negative")
    if np.any(np.diff(snapshots) <= 0.0):
        raise PhysicsError("snapshot times must increase strictly")
    for value, name in (
        (aluminum_gap_hz, "aluminum_gap_hz"),
        (qubit_frequency_hz, "qubit_frequency_hz"),
        (cooper_pairs_per_um3, "cooper_pairs_per_um3"),
        (electrode_height_um, "electrode_height_um"),
        (electrode_width_um, "electrode_width_um"),
        (film_thickness_um, "film_thickness_um"),
    ):
        _positive_finite(value, name)
    if not isinstance(simulated_primaries, int) or simulated_primaries <= 0:
        raise PhysicsError("simulated_primaries must be a positive integer")
    if pulse_time_us < 0.0 or not math.isfinite(float(pulse_time_us)):
        raise PhysicsError("pulse_time_us must be finite and non-negative")
    for value, name in (
        (recombination, "recombination"),
        (loss_rate, "loss_rate"),
        (injection_scale, "injection_scale"),
    ):
        if not math.isfinite(float(value)) or value < 0.0:
            raise PhysicsError(f"{name} must be finite and non-negative")

    dt = float(np.mean(np.diff(snapshots))) * 0.001
    extension = int(np.round(float(pulse_time_us) / dt))
    time_us = np.linspace(
        0.001 * snapshots[0],
        0.001 * snapshots[-1] + float(pulse_time_us),
        snapshots.size + extension,
    )
    generation = np.zeros((qps.shape[0], time_us.size))
    density = np.zeros_like(generation)
    decoherence_mhz = np.zeros_like(generation)
    factor = 2.0 * math.sqrt(2.0 * aluminum_gap_hz * qubit_frequency_hz) * 1e-6
    scale = injection_scale / (
        cooper_pairs_per_um3
        * electrode_height_um
        * electrode_width_um
        * film_thickness_um
        * simulated_primaries
    )

    for electrode in range(qps.shape[0]):
        for offset in range(extension):
            generation[electrode, offset : snapshots.size + offset] += qps[electrode] * scale
        for index in range(time_us.size - 1):
            change = (
                -recombination * density[electrode, index] ** 2
                - loss_rate * density[electrode, index]
                + generation[electrode, index]
            ) * dt
            density[electrode, index + 1] = density[electrode, index] + change
        decoherence_mhz[electrode] = density[electrode] * factor
    return time_us, decoherence_mhz
