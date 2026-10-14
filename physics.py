"""Elastic waves, calibrated interfaces, and junction quasiparticles; SI except noted."""

from functools import lru_cache
import math

import numpy as np

QP_SNAPSHOT_COUNT = 1001
QP_SNAPSHOT_MAX_NS = 300_000.0
KB_EV_PER_K = 8.617333262e-5
SI_BASELINE_ABS = {"junction_Al": 0.795, "top_film_Nb": 0.745, "bottom_film_Cu": 0.736}
BASELINE_FILM = {
    "junction_Al": {"g4_material": "G4_Al", "vsound_m_s": 3582.0, "density_kg_m3": 2699.0},
    "top_film_Nb": {"g4_material": "G4_Nb", "vsound_m_s": 2444.0, "density_kg_m3": 8570.0},
    "bottom_film_Cu": {"g4_material": "G4_Cu", "vsound_m_s": 2608.0, "density_kg_m3": 8960.0},
}


@lru_cache(maxsize=4)
def fibonacci_sphere(n_points=10_000):
    if not isinstance(n_points, int) or n_points < 1:
        raise ValueError("n_points must be a positive integer")
    indices = np.arange(n_points, dtype=float)
    z = 1.0 - 2.0 * (indices + 0.5) / n_points
    radius = np.sqrt(1.0 - z ** 2)
    phi = np.pi * (3.0 - np.sqrt(5.0)) * indices
    directions = np.column_stack((radius * np.cos(phi), radius * np.sin(phi), z))
    directions.setflags(write=False)
    return directions


def voigt_to_tensor(C):
    """C[6,6] -> C[i,j,k,l], engineering strain order 11,22,33,23,13,12."""
    C = np.asarray(C, dtype=float)
    if C.shape != (6, 6) or not np.isfinite(C).all():
        raise ValueError("C must be a finite 6 by 6 stiffness matrix")
    pairs = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))
    tensor = np.zeros((3, 3, 3, 3))
    for I, (i, j) in enumerate(pairs):
        for J, (k, l) in enumerate(pairs):
            tensor[i, j, k, l] = tensor[j, i, k, l] = C[I, J]
            tensor[i, j, l, k] = tensor[j, i, l, k] = C[I, J]
    return tensor


def acoustic_velocities(C, rho, direction, units="GPa"):
    """Christoffel modes for one direction; speeds m/s, density kg/m³."""
    if not math.isfinite(rho) or rho <= 0 or units not in ("Pa", "MPa", "GPa"):
        raise ValueError("positive finite density and stiffness units Pa/MPa/GPa required")
    n = np.asarray(direction, dtype=float)
    if n.shape != (3,) or not np.isfinite(n).all() or np.linalg.norm(n) == 0:
        raise ValueError("direction must be a finite nonzero three-vector")
    n = n / np.linalg.norm(n)
    gamma = np.einsum("ijkl,j,l->ik", voigt_to_tensor(C) * {"Pa": 1., "MPa": 1e6, "GPa": 1e9}[units], n, n)
    gamma = 0.5 * (gamma + gamma.T)
    eigenvalues, polarization = np.linalg.eigh(gamma)
    if np.any(eigenvalues < -1e-6):
        raise ValueError("negative Christoffel eigenvalue")
    velocities = np.sqrt(np.maximum(eigenvalues, 0.) / rho)
    longitudinal = int(np.argmax(np.abs(polarization.T @ n) ** 2))
    transverse = [i for i in range(3) if i != longitudinal]
    return {"direction": n, "christoffel": gamma, "velocities": velocities,
            "polarizations": polarization, "longitudinal_index": longitudinal,
            "transverse_indices": transverse, "v_L": float(velocities[longitudinal]),
            "v_T": velocities[transverse]}


@lru_cache(maxsize=4096)
def derive_cubic_sound_speeds(c11, c12, c44, density=2330.0):
    """Spherical mean (vL,vT) in m/s; stiffness inputs in GPa, 10,000 directions."""
    if not all(math.isfinite(v) for v in (c11, c12, c44, density)) or density <= 0:
        raise ValueError("stiffness and density must be finite; density must be positive")
    if not (c11 - c12 > 0 and c11 + 2 * c12 > 0 and c44 > 0):
        raise ValueError(f"Born-unstable cubic stiffness: C11={c11:g}, C12={c12:g}, C44={c44:g} GPa")
    n = fibonacci_sphere()
    gamma = np.empty((len(n), 3, 3))
    for i in range(3):
        gamma[:, i, i] = c44 + (c11 - c44) * n[:, i] ** 2
        for j in range(i + 1, 3):
            gamma[:, i, j] = (c12 + c44) * n[:, i] * n[:, j]
            gamma[:, j, i] = gamma[:, i, j]
    eigenvalues, eigenvectors = np.linalg.eigh(gamma * 1e9)
    if np.any(eigenvalues <= 0):
        raise ValueError("non-positive cubic Christoffel eigenvalue")
    velocities = np.sqrt(eigenvalues / density)
    longitudinal_fraction = np.abs(np.einsum("nij,ni->nj", eigenvectors, n)) ** 2
    longitudinal = velocities[np.arange(len(n)), np.argmax(longitudinal_fraction, axis=1)]
    transverse = (velocities.sum(axis=1) - longitudinal) / 2.0
    return float(longitudinal.mean()), float(transverse.mean())


def sphere_to_miller(polar_cos, azimuth_turns, scale=1_000_000):
    z, turn = float(polar_cos), float(azimuth_turns)
    if not math.isfinite(z) or not -1 <= z <= 1 or not math.isfinite(turn):
        raise ValueError("finite polar cosine in [-1,1] and azimuth required")
    if not isinstance(scale, int) or scale < 1:
        raise ValueError("Miller conversion scale must be a positive integer")
    radius = math.sqrt(max(0.0, 1.0 - z * z))
    phi = 2 * math.pi * (turn % 1.0)
    integer = [int(round(scale * value)) for value in (radius * math.cos(phi), radius * math.sin(phi), z)]
    if integer == [0, 0, 0]:
        raise ValueError("sphere direction quantized to zero")
    divisor = math.gcd(math.gcd(abs(integer[0]), abs(integer[1])), abs(integer[2]))
    return [value // max(1, divisor) for value in integer]


def miller_to_sphere(miller):
    n = np.asarray(miller, dtype=float)
    if n.shape != (3,) or not np.isfinite(n).all() or np.linalg.norm(n) == 0:
        raise ValueError("Miller direction must be a finite nonzero three-vector")
    return float(n[2] / np.linalg.norm(n)), float((math.atan2(n[1], n[0]) / (2 * math.pi)) % 1.0)


def interface_absorption(interface, substrate, film, si_reference):
    """A = clip(A_Si T/T_Si,0,1), T=4 Z₁Z₂/(Z₁+Z₂)²; calibrated model."""
    if interface not in SI_BASELINE_ABS:
        raise ValueError(f"unknown interface {interface!r}")
    rho_f = film.get("density_kg_m3", 2699.0 if film["g4_material"] == "G4_Al" else None)
    if rho_f is None:
        raise ValueError(f"missing measured film density for {film['g4_material']}")
    speed_f = float(film["vsound_m_s"])
    reference = BASELINE_FILM[interface]
    for record in (substrate, si_reference):
        rho, vl, vt = (float(record[key]) for key in ("density_kg_m3", "vsound_m_s", "vtrans_m_s"))
        if not all(math.isfinite(v) and v > 0 for v in (rho, vl, vt)) or vt >= vl:
            raise ValueError("interface needs positive density and vL > vT > 0")
    if not all(math.isfinite(v) and v > 0 for v in (rho_f, speed_f)):
        raise ValueError("film density and speed must be finite and positive")
    z_film = rho_f * speed_f
    z_cand = substrate["density_kg_m3"] * ((substrate["vsound_m_s"] + 2 * substrate["vtrans_m_s"]) / 3)
    z_si = si_reference["density_kg_m3"] * ((si_reference["vsound_m_s"] + 2 * si_reference["vtrans_m_s"]) / 3)
    z_ref = reference["density_kg_m3"] * reference["vsound_m_s"]
    t_cand = 4 * z_cand * z_film / (z_cand + z_film) ** 2
    t_si = 4 * z_si * z_ref / (z_si + z_ref) ** 2
    absorption = SI_BASELINE_ABS[interface] * t_cand / t_si
    clipped = min(1.0, max(0.0, absorption))
    return clipped, {"interface": interface, "film_material": film["g4_material"],
        "film_density_kg_m3": rho_f, "film_speed_m_s": speed_f, "Z_film": z_film,
        "Z_substrate": z_cand, "Z_substrate_Si": z_si, "Z_baseline_film": z_ref,
        "baseline_film": reference["g4_material"], "T_candidate": t_cand, "T_Si": t_si,
        "ratio_T": t_cand / t_si, "A_si_baseline": SI_BASELINE_ABS[interface],
        "A_unclipped": absorption, "A": clipped, "clipped": absorption != clipped,
        "model": "baseline_calibrated_effective_AMM"}


def resolve_all_interfaces(substrate, junction_film, top_film, bottom_film, si_reference):
    values, diagnostics = {}, {}
    for name, key, interface, film in (
        ("junction", "setTopAbs", "junction_Al", junction_film),
        ("top_film", "setTopFilmAbs", "top_film_Nb", top_film),
        ("bottom_film", "setBotAbs", "bottom_film_Cu", bottom_film)):
        values[key], diagnostics[name] = interface_absorption(interface, substrate, film, si_reference)
    return values, diagnostics


def check_values(values, density_kg_m3=2330.0, constraints=None, junction_gap_eV=191e-6,
                 gun_energy_eV=.01, cutoff_eV=1e-6, *, ground_plane_active_absorber=None):
    """Validate physical inputs before transport; return derived simulation fields."""
    p, constraints = values, constraints or {}
    if isinstance(constraints, list):
        for row in constraints:
            lhs = sum(p[k] * float(v) for k, v in row["terms"].items())
            rhs = float(row["rhs"])
            valid = {">": lhs > rhs, ">=": lhs >= rhs, "<": lhs < rhs, "<=": lhs <= rhs, "==": lhs == rhs}
            if not valid.get(row["operator"], False):
                raise ValueError(f"failed physical condition {row['name']}")
        constraints = {}
    for key, value in p.items():
        if isinstance(value, (int, float, np.number)) and not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
    if not all(math.isfinite(v) and v > 0 for v in (density_kg_m3, junction_gap_eV, gun_energy_eV, cutoff_eV)):
        raise ValueError("density, excitation, cutoff and gap must be finite and positive")
    if not cutoff_eV < 2 * junction_gap_eV <= gun_energy_eV:
        raise ValueError("require cutoff < 2*junction_gap <= gun_energy")
    for key in ("sub_scat", "sub_decay", "sub_decayTT", "temperature"):
        if key in p and float(p[key]) < 0:
            raise ValueError(f"{key} must be non-negative")
    for key in ("topfilm_gap", "topfilm_ph_lifetime", "bot_ph_lifetime", "sub_lattice_a"):
        if key in p and float(p[key]) <= 0:
            raise ValueError(f"{key} must be positive")
    if "bot_gap_thres" in p and not 0 < float(p["bot_gap_thres"]) <= 2 * junction_gap_eV + 1e-12:
        raise ValueError("bottom threshold must be in (0,2*junction_gap]")
    gap = float(p["topfilm_gap"])
    if gap < float(constraints.get("topfilm_gap_min_eV", 0)) - 1e-15:
        raise ValueError("film gap is below the declared minimum")
    if gap / (1.764 * KB_EV_PER_K) < float(constraints.get("topfilm_tc_min_K", 0)) - 1e-12:
        raise ValueError("film critical temperature is below the declared minimum")
    if gap / junction_gap_eV < float(constraints.get("topfilm_gap_over_junction_min", 0)) - 1e-12:
        raise ValueError("film-to-junction gap ratio is below the declared minimum")
    active = gun_energy_eV >= 2 * gap
    if ground_plane_active_absorber is not None and active != bool(ground_plane_active_absorber):
        raise ValueError("candidate changes the declared ground-plane absorption regime")
    vl, vt = derive_cubic_sound_speeds(*(float(p[k]) for k in ("sub_c11", "sub_c12", "sub_c44")), density=density_kg_m3)
    if not 0 < vt < vl:
        raise ValueError("require mean longitudinal speed > transverse speed > 0")
    si_vl, si_vt = derive_cubic_sound_speeds(165.6, 63.9, 79.5, density=2330.)
    substrate = {"density_kg_m3": density_kg_m3, "vsound_m_s": vl, "vtrans_m_s": vt}
    si = {"density_kg_m3": 2330., "vsound_m_s": si_vl, "vtrans_m_s": si_vt}
    interface, diagnostics = resolve_all_interfaces(substrate, BASELINE_FILM["junction_Al"],
        {"g4_material": "G4_Nb", "vsound_m_s": float(p["topfilm_vsound"]) * 1000, "density_kg_m3": float(p["topfilm_density"])},
        {"g4_material": "G4_Cu", "vsound_m_s": float(p["bot_vsound"]) * 1000, "density_kg_m3": float(p["bot_density"])}, si)
    result = {"substrate_density_kg_m3": density_kg_m3, "vsound_m_s": round(vl, 6), "vtrans_m_s": round(vt, 6),
        "anisotropy": round(2 * p["sub_c44"] / (p["sub_c11"] - p["sub_c12"]), 6),
        "impedance_substrate": round(density_kg_m3 * (vl + 2 * vt) / 3, 3),
        **{k: round(v, 6) for k, v in interface.items()}, "interface_model": "baseline_calibrated_effective_AMM",
        "interface_diagnostics": {k: {n: round(v, 6) if isinstance(v, float) else v for n, v in d.items()} for k, d in diagnostics.items()},
        "excitation_gate": {"min_e_phonons_eV": cutoff_eV, "junction_gate_eV": 2 * junction_gap_eV,
            "gun_energy_eV": gun_energy_eV, "film_gate_eV": 2 * gap,
            "margin_above_junction_gate": gun_energy_eV / (2 * junction_gap_eV),
            "ground_plane_active_absorber": active, "e_gun_over_film_gate": gun_energy_eV / (2 * gap)}}
    if "orientation_polar_cos" in p and "orientation_azimuth_turns" in p:
        result["miller"] = sphere_to_miller(p["orientation_polar_cos"], p["orientation_azimuth_turns"])
    elif "miller" in p:
        miller_to_sphere(p["miller"])
        result["miller"] = list(p["miller"])
    return result


def qp_counts(energy_deposited_eV, end_x_m, end_y_m, end_z_m, final_time_ns,
              gap_eV, electrode_x_mm, electrode_y_mm, chip_surface_z_m, *,
              time_resolved=False, snapshot_count=QP_SNAPSHOT_COUNT, snapshot_max_ns=QP_SNAPSHOT_MAX_NS):
    """Surface hits -> electrode counts; optional time bins preserve all legacy ties."""
    if not math.isfinite(gap_eV) or gap_eV <= 0 or not math.isfinite(chip_surface_z_m):
        raise ValueError("positive finite gap and finite sensor surface required")
    xy = tuple(np.asarray(v, dtype=float) for v in (electrode_x_mm, electrode_y_mm))
    if any(v.ndim != 1 or not v.size or not np.isfinite(v).all() for v in xy) or xy[0].shape != xy[1].shape:
        raise ValueError("electrode coordinates must be finite matching nonempty vectors")
    columns = tuple(np.asarray(v, dtype=float) for v in (energy_deposited_eV, end_x_m, end_y_m, end_z_m, final_time_ns))
    if any(v.ndim != 1 or not np.isfinite(v).all() for v in columns) or len({v.size for v in columns}) != 1:
        raise ValueError("hit columns must be finite vectors with equal lengths")
    if not isinstance(snapshot_count, int) or snapshot_count < 2 or not math.isfinite(snapshot_max_ns) or snapshot_max_ns <= 0:
        raise ValueError("positive time range and at least two integer snapshots required")
    energy, x, y, z, time = columns
    selected = (energy > 0) & np.isclose(z, chip_surface_z_m)
    energy, x, y, time = energy[selected], x[selected], y[selected], time[selected]
    distance = ((1000 * x[:, None] - xy[0][None, :]) ** 2 + (1000 * y[:, None] - xy[1][None, :]) ** 2) ** .5
    electrode = np.argmin(distance, axis=1)
    rounded = np.round(energy / gap_eV)
    if not np.isfinite(rounded).all() or np.max(np.abs(rounded), initial=0.) > 2 ** 53:
        raise ValueError("QP count is not exactly representable as a float integer")
    if not time_resolved:
        totals = np.zeros(xy[0].size)
        np.add.at(totals, electrode, rounded)
        return totals
    snapshots = np.linspace(0., snapshot_max_ns, snapshot_count)
    right = np.searchsorted(snapshots, time, side="left")
    ri, li = np.clip(right, 0, snapshot_count - 1), np.clip(right - 1, 0, snapshot_count - 1)
    nearest = np.where(np.abs(snapshots[ri] - time) < np.abs(time - snapshots[li]), ri, li)
    qps = np.zeros((xy[0].size, snapshot_count))
    np.add.at(qps, (electrode, nearest), rounded)
    return snapshots, qps


def calculate_qps(energy_deposited_eV, end_x_m, end_y_m, end_z_m, final_time_ns,
                  gap_eV, electrode_x_mm, electrode_y_mm, chip_surface_z_m, *,
                  snapshot_count=QP_SNAPSHOT_COUNT, snapshot_max_ns=QP_SNAPSHOT_MAX_NS):
    """Compatibility array API for time-dependent calculations."""
    return qp_counts(energy_deposited_eV, end_x_m, end_y_m, end_z_m, final_time_ns,
        gap_eV, electrode_x_mm, electrode_y_mm, chip_surface_z_m, time_resolved=True,
        snapshot_count=snapshot_count, snapshot_max_ns=snapshot_max_ns)


def calculate_xqps(snapshot_times_ns, qps_by_electrode_time, aluminum_gap_hz,
                   qubit_frequency_hz, recombination, loss_rate, injection_scale,
                   pulse_time_us, cooper_pairs_per_um3, electrode_height_um,
                   electrode_width_um, film_thickness_um, simulated_primaries):
    """Retained explicit Euler density evolution; times μs, decoherence MHz."""
    snapshots, qps = np.asarray(snapshot_times_ns, dtype=float), np.asarray(qps_by_electrode_time, dtype=float)
    if snapshots.ndim != 1 or snapshots.size < 2 or not np.isfinite(snapshots).all() or np.any(np.diff(snapshots) <= 0):
        raise ValueError("snapshot times must be finite and strictly increasing")
    if qps.ndim != 2 or qps.shape[1] != snapshots.size or not np.isfinite(qps).all() or np.any(qps < 0):
        raise ValueError("QP array must be finite, non-negative, and match the time grid")
    if not all(math.isfinite(v) and v > 0 for v in (aluminum_gap_hz, qubit_frequency_hz, cooper_pairs_per_um3, electrode_height_um, electrode_width_um, film_thickness_um)):
        raise ValueError("frequencies, Cooper-pair density and dimensions must be positive and finite")
    if not isinstance(simulated_primaries, int) or simulated_primaries <= 0:
        raise ValueError("simulated_primaries must be a positive integer")
    if not all(math.isfinite(v) and v >= 0 for v in (pulse_time_us, recombination, loss_rate, injection_scale)):
        raise ValueError("pulse duration, rates and injection must be finite and non-negative")
    dt = float(np.mean(np.diff(snapshots))) * .001
    extension = int(np.round(float(pulse_time_us) / dt))
    time_us = np.linspace(.001 * snapshots[0], .001 * snapshots[-1] + float(pulse_time_us), snapshots.size + extension)
    generation = np.zeros((qps.shape[0], time_us.size))
    density, decoherence = np.zeros_like(generation), np.zeros_like(generation)
    factor = 2. * math.sqrt(2. * aluminum_gap_hz * qubit_frequency_hz) * 1e-6
    scale = injection_scale / (cooper_pairs_per_um3 * electrode_height_um * electrode_width_um * film_thickness_um * simulated_primaries)
    for electrode in range(qps.shape[0]):
        for offset in range(extension):
            generation[electrode, offset:snapshots.size + offset] += qps[electrode] * scale
        for index in range(time_us.size - 1):
            change = (-recombination * density[electrode, index] ** 2 - loss_rate * density[electrode, index] + generation[electrode, index]) * dt
            density[electrode, index + 1] = density[electrode, index] + change
        decoherence[electrode] = density[electrode] * factor
    return time_us, decoherence


def film_tc_K(gap_eV):
    """BCS critical temperature K from pairing gap eV."""
    return float(gap_eV) / (1.764 * KB_EV_PER_K)


def thermal_qp_density(gap_eV, temperature_K, n0_per_um3_eV=1.74e10):
    """BCS equilibrium density μm⁻³; default single-spin DOS is aluminium's."""
    if not all(math.isfinite(v) for v in (gap_eV, temperature_K, n0_per_um3_eV)) or n0_per_um3_eV <= 0:
        raise ValueError("thermal inputs must be finite with positive density of states")
    if gap_eV <= 0:
        return float("inf")
    kt = KB_EV_PER_K * temperature_K
    return 0. if kt <= 0 else float(2 * n0_per_um3_eV * math.sqrt(2 * math.pi * kt * gap_eV) * math.exp(-gap_eV / kt))


def engineering_diagnostics(point, operating_temperature_K=.02, junction_gap_eV=191e-6):
    gap, nb_gap = float(point["topfilm_gap"]), 1.5384e-3
    if gap <= 0 or junction_gap_eV <= 0:
        raise ValueError("film and junction gaps must be positive")
    kt = KB_EV_PER_K * float(operating_temperature_K)
    loss = (nb_gap - gap) / kt / math.log(10.) if kt > 0 else 0.
    qp_ratio = .5 * math.log10(gap / nb_gap) + loss if kt > 0 else 0.
    return {"operating_temperature_K": float(operating_temperature_K), "topfilm_gap_eV": gap,
        "topfilm_tc_K": film_tc_K(gap), "topfilm_gap_ratio": gap / junction_gap_eV,
        "ground_plane_is_phonon_sink": gap < junction_gap_eV,
        "thermal_qp_density_um3": thermal_qp_density(gap, operating_temperature_K),
        "log10_thermal_qp_density_ratio_vs_Nb": qp_ratio, "log10_loss_proxy_vs_Nb": loss,
        "caveat": "Equilibrium BCS scaling with aluminium DOS; not a measured film density or predicted Q."}
