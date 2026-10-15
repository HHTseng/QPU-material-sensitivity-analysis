"""Elastic waves, calibrated interfaces and junction QPs; SI except noted.

x: physical properties; u: normalized coordinates; g: identity or logarithm.
q_k=round(E_k/Delta_Al) per hit; N[e] sums q_k over junction e in one task.
"""

from functools import lru_cache
import math
import copy

import numpy as np
from scipy.stats import qmc

QP_SNAPSHOT_COUNT = 1001
QP_SNAPSHOT_MAX_NS = 300_000.0
KB_EV_PER_K = 8.617333262e-5
SI_BASELINE_ABS = {"junction_Al": 0.795, "top_film_Nb": 0.745, "bottom_film_Cu": 0.736}
BASELINE_FILM = {
    "junction_Al": {"g4_material": "G4_Al", "vsound_m_s": 3582.0, "density_kg_m3": 2699.0},
    "top_film_Nb": {"g4_material": "G4_Nb", "vsound_m_s": 2444.0, "density_kg_m3": 8570.0},
    "bottom_film_Cu": {"g4_material": "G4_Cu", "vsound_m_s": 2608.0, "density_kg_m3": 8960.0},
}

REGIONS = ('S0_junction', 'S1_le_0.05', 'S2_le_0.20', 'S3_le_0.50', 'S4_bulk')
DENSITIES = {'G4_Si': 2330., 'G4_Ge': 5323., 'G4_GALLIUM_ARSENIDE': 5310.,
             'G4_CALCIUM_FLUORIDE': 3180., 'G4_LITHIUM_FLUORIDE': 2635.,
             'G4_LITHIUM_HYDRIDE':820., 'G4_ALUMINUM_OXIDE':3970., 'G4_BORON_CARBIDE':2520.}


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


def acoustic_velocities(C, rho, direction, units="GPa"):
    """Gamma_ik=C_ijkl*n_j*n_l; eigenvalues rho*v_a^2, unit direction n.

    C uses Voigt notation; Gamma in Pa, rho in kg/m^3 and mode speeds in m/s.
    """
    if not math.isfinite(rho) or rho <= 0 or units not in ("Pa", "MPa", "GPa"):
        raise ValueError("positive finite density and stiffness units Pa/MPa/GPa required")
    n = np.asarray(direction, dtype=float)
    if n.shape != (3,) or not np.isfinite(n).all() or np.linalg.norm(n) == 0:
        raise ValueError("direction must be a finite nonzero three-vector")
    n = n / np.linalg.norm(n)
    C = np.asarray(C, dtype=float)
    if C.shape != (6, 6) or not np.isfinite(C).all():
        raise ValueError("C must be a finite 6 by 6 stiffness matrix")
    pairs = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))
    tensor = np.zeros((3, 3, 3, 3))
    for I, (i, j) in enumerate(pairs):
        for J, (k, l) in enumerate(pairs):
            tensor[i, j, k, l] = tensor[j, i, k, l] = C[I, J]
            tensor[i, j, l, k] = tensor[j, i, l, k] = C[I, J]
    gamma = np.einsum("ijkl,j,l->ik", tensor * {"Pa": 1., "MPa": 1e6, "GPa": 1e9}[units], n, n)
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
    """Means <v_L>_n and <(v_T1+v_T2)/2>_n over 10,000 unit directions n.

    Cubic stiffness inputs are GPa; mean speeds are m/s.
    """
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




def qp_counts(energy_deposited_eV, end_x_m, end_y_m, end_z_m, final_time_ns,
              gap_eV, electrode_x_mm, electrode_y_mm, chip_surface_z_m, *,
              time_resolved=False, snapshot_count=QP_SNAPSHOT_COUNT, snapshot_max_ns=QP_SNAPSHOT_MAX_NS):
    """Positive sensor-surface hits -> N[e]=sum_{k->e} round(E_k/Delta_Al).

    Energies are eV; optional N[e,j] bins arrival times t_j in ns. Ties use first.
    """
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


def calculate_xqps(snapshot_times_ns, qps_by_electrode_time, aluminum_gap_hz,
                   qubit_frequency_hz, recombination, loss_rate, injection_scale,
                   pulse_time_us, cooper_pairs_per_um3, electrode_height_um,
                   electrode_width_um, film_thickness_um, simulated_primaries):
    """z=n_qp/n_cp: dz/dt=-r*z^2-s*z+g; explicit Euler, t in us.

    r=recombination, s=loss_rate; Gamma=2*sqrt(2*Delta_Hz*f_q)*z in Hz; return MHz.
    """
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
    # density=z[e,j], generation=g[e,j]; qps=N[e,j] summed over primaries.
    generation = np.zeros((qps.shape[0], time_us.size))
    density = np.zeros_like(generation)
    factor = 2. * math.sqrt(2. * aluminum_gap_hz * qubit_frequency_hz) * 1e-6
    scale = injection_scale / (cooper_pairs_per_um3 * electrode_height_um * electrode_width_um * film_thickness_um * simulated_primaries)
    injection = qps * scale
    for offset in range(extension):
        generation[:, offset:snapshots.size + offset] += injection
    for index in range(time_us.size - 1):
        current = density[:, index]
        change = (-recombination * current ** 2 - loss_rate * current + generation[:, index]) * dt
        density[:, index + 1] = current + change
    decoherence = density * factor
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


def search_space(parameters=None, *, kind='search', constraints=None, table=None):
    if table is None:
        raise ValueError('search_space requires a loaded parameter table')
    parameters = parameters or {}
    unknown = set(parameters) - set(table)
    if unknown:
        raise ValueError(f'unknown parameters: {sorted(unknown)}')
    baseline = {name: float(row['default']) for name, row in table.items()}
    names, low, high, scales = [], [], [], []
    for name, row in table.items():
        spec = parameters.get(name, {})
        if isinstance(spec, (int, float)):
            spec = {'mode': 'fixed', 'value': spec}
        mode = spec.get('mode', 'search' if kind == 'search' and row.get('active') else 'fixed')
        if mode not in ('fixed', 'search'):
            raise ValueError(f'{name}: unsupported mode {mode}')
        value = spec.get('value', spec.get('baseline', row['default']))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{name}: expected a finite number')
        baseline[name] = float(value)
        vmin, vmax = row['valid_range']
        if not float(vmin) <= value <= float(vmax):
            raise ValueError(f'{name}: value outside physical input range')
        if mode == 'search':
            limits = spec.get('bounds', spec.get('range', row['search_range']))
            lo, hi = map(float, limits)
            scale = spec.get('scale', row.get('scale', 'linear'))
            if not vmin <= lo < hi <= vmax or not lo <= value <= hi:
                raise ValueError(f'{name}: invalid search interval or starting value')
            if scale not in ('linear', 'log') or scale == 'log' and lo <= 0:
                raise ValueError(f'{name}: invalid coordinate scale')
            names.append(name); low.append(lo); high.append(hi); scales.append(scale)
    return {'names': names, 'low': low, 'high': high, 'scales': scales,
            'baseline': baseline, 'constraints': constraints or {}, 'density_kg_m3': 2330.,
            'valid_ranges':{name:row['valid_range'] for name,row in table.items()}}


def to_unit(point, space):
    """x -> u in [0,1]^d: u_j=(g(x_j)-g(lo_j))/(g(hi_j)-g(lo_j))."""
    values = np.array([point[name] for name in space['names']], dtype=float)
    lo, hi = np.asarray(space['low']), np.asarray(space['high'])
    logarithmic = np.array([s == 'log' for s in space['scales']])
    values[logarithmic] = np.log(values[logarithmic])
    lo = np.where(logarithmic, np.log(np.maximum(lo, 1e-300)), lo)
    hi = np.where(logarithmic, np.log(np.maximum(hi, 1e-300)), hi)
    return (values - lo) / (hi - lo)


def from_unit(u, space):
    """u -> x: x_j=g^-1(g(lo_j)+u_j*(g(hi_j)-g(lo_j))); fixed fields retained."""
    u = np.asarray(u, dtype=float)
    if u.shape != (len(space['names']),) or not np.isfinite(u).all():
        raise ValueError('coordinate dimensions or values are invalid')
    u = np.clip(u, 0., 1.)
    point = dict(space['baseline'])
    for name, value, lo, hi, scale in zip(space['names'], u, space['low'], space['high'], space['scales']):
        natural = math.exp(math.log(lo) + value * (math.log(hi)-math.log(lo))) if scale == 'log' else lo + value*(hi-lo)
        point[name] = float(min(hi,max(lo,natural)))
    if 'orientation_azimuth_turns' in point:
        point['orientation_azimuth_turns'] %= 1.0
    return point


def validate_fixed(fixed):
    for name in ('setSubWidth_mm','setSubHeight_mm','setSubThickness_um','electrode_width_um',
                 'electrode_height_um','setTopThickness_um','setTopGap','gun_energy_eV','min_e_phonons_eV'):
        value=fixed[name]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError(f'{name}: positive finite physical value required')
    xy=np.asarray([fixed['electrode_x_mm'],fixed['electrode_y_mm']],dtype=float)
    if xy.ndim!=2 or xy.shape[0]!=2 or not xy.shape[1] or not np.isfinite(xy).all():
        raise ValueError('electrode coordinates must be matching finite vectors')
    for name in ('setWallAbs','setTopPSpecProb','setTopFilmPSpecProb','setBotPSpecProb','setWallPSpecProb'):
        if not math.isfinite(fixed[name]) or not 0<=fixed[name]<=1:
            raise ValueError(f'{name}: probability outside [0,1]')
    limit=fixed['setTopQPLim']
    if isinstance(limit,bool) or not isinstance(limit,int) or not 2<=limit<=5:
        raise ValueError('junction QP limit must be an integer from two through five')
    if fixed.get('gun_type','phonon_Caustic')!='phonon_Caustic':
        raise ValueError('this study requires a phonon_Caustic source')


def region_distances(xy, fixed):
    xy = np.asarray(xy, dtype=float)
    centers = np.array([fixed['electrode_x_mm'], fixed['electrode_y_mm']]).T
    half = np.array([fixed['electrode_width_um'], fixed['electrode_height_um']])/2000.
    delta = np.maximum(np.abs(xy[:,None,:]-centers[None,:,:])-half,0.)
    distance = np.sqrt(np.sum(delta*delta,axis=2)).min(axis=1)
    return np.searchsorted([0., .05, .20, .50], distance, side='left')


def resolve_all_interfaces(substrate, junction_film, top_film, bottom_film, si_reference):
    """A=clip(A_Si*T/T_Si,0,1), T=4*Z1*Z2/(Z1+Z2)^2; calibrated model.

    Z=rho*v; the substrate uses v=(v_L+2*v_T)/3, films use their scalar v.
    """
    for record in (substrate, si_reference):
        rho, vl, vt = (float(record[k]) for k in ('density_kg_m3','vsound_m_s','vtrans_m_s'))
        if not all(math.isfinite(v) and v > 0 for v in (rho, vl, vt)) or vt >= vl:
            raise ValueError('interfaces require positive density and vL > vT > 0')
    z_cand = substrate['density_kg_m3'] * ((substrate['vsound_m_s'] + 2*substrate['vtrans_m_s']) / 3)
    z_si = si_reference['density_kg_m3'] * ((si_reference['vsound_m_s'] + 2*si_reference['vtrans_m_s']) / 3)
    values, diagnostics = {}, {}
    for name, key, interface, film in (
        ('junction','setTopAbs','junction_Al',junction_film),
        ('top_film','setTopFilmAbs','top_film_Nb',top_film),
        ('bottom_film','setBotAbs','bottom_film_Cu',bottom_film)):
        rho = film.get('density_kg_m3', 2699. if film['g4_material']=='G4_Al' else None)
        speed = float(film['vsound_m_s'])
        if rho is None or not all(math.isfinite(v) and v > 0 for v in (rho,speed)):
            raise ValueError('film density and speed must be positive and finite')
        reference = BASELINE_FILM[interface]
        z_film = rho * speed; z_ref = reference['density_kg_m3'] * reference['vsound_m_s']
        t_cand = 4*z_cand*z_film/(z_cand+z_film)**2
        t_si = 4*z_si*z_ref/(z_si+z_ref)**2
        absorption = SI_BASELINE_ABS[interface] * t_cand/t_si
        clipped = min(1.,max(0.,absorption)); values[key] = clipped
        diagnostics[name] = {'interface':interface,'film_material':film['g4_material'],
            'film_density_kg_m3':rho,'film_speed_m_s':speed,'Z_film':z_film,
            'Z_substrate':z_cand,'Z_substrate_Si':z_si,'Z_baseline_film':z_ref,
            'baseline_film':reference['g4_material'],'T_candidate':t_cand,'T_Si':t_si,
            'ratio_T':t_cand/t_si,'A_si_baseline':SI_BASELINE_ABS[interface],
            'A_unclipped':absorption,'A':clipped,'clipped':absorption!=clipped,
            'model':'baseline_calibrated_effective_AMM'}
    return values, diagnostics


def validate_values(point, space, exact=False, *, films=None):
    """Check cheap physical limits; exact=True also derives modes and interfaces."""
    p = dict(space.get('baseline',{})); p.update(point)
    if any(not isinstance(v,(int,float,np.number)) or isinstance(v,bool) or not math.isfinite(v)
           for k,v in p.items() if not k.startswith('_') and k!='miller'):
        raise ValueError('material properties must be finite numbers')
    for name,lo,hi in zip(space.get('names',[]),space.get('low',[]),space.get('high',[])):
        if not lo <= p[name] <= hi:
            raise ValueError(f'{name}: outside declared search interval')
    for name,(lo,hi) in space.get('valid_ranges',{}).items():
        if name in p and not lo <= p[name] <= hi:
            raise ValueError(f'{name}: outside physical input range')
    c11,c12,c44 = (float(p[k]) for k in ('sub_c11','sub_c12','sub_c44'))
    if not (c44>0 and c11>c12 and c11+2*c12>0):
        raise ValueError('unstable cubic stiffness')
    density = float(space.get('density_kg_m3',2330.))
    gap_j = float(space.get('junction_gap_eV',191e-6))
    energy = float(space.get('gun_energy_eV',.01)); cutoff = float(space.get('cutoff_eV',38.2e-6))
    if not all(math.isfinite(v) and v>0 for v in (density,gap_j,energy,cutoff)) or not cutoff < 2*gap_j <= energy:
        raise ValueError('require positive density and cutoff < 2*junction_gap <= gun_energy')
    # B=sub_scat, A=sub_decay: rates B*nu^4 and A*nu^5, nu=E/h in Hz.
    for name in ('sub_scat','sub_decay','sub_decayTT','temperature'):
        if name in p and p[name]<0: raise ValueError(f'{name} must be non-negative')
    for name in ('topfilm_gap','topfilm_ph_lifetime','bot_ph_lifetime','sub_lattice_a'):
        if name in p and p[name]<=0: raise ValueError(f'{name} must be positive')
    if 'bot_gap_thres' in p and not 0 < p['bot_gap_thres'] <= 2*gap_j+1e-12:
        raise ValueError('bottom threshold must be in (0,2*junction_gap]')
    gap = float(p['topfilm_gap']); constraints = space.get('constraints') or {}
    if isinstance(constraints,list):
        for row in constraints:
            lhs = sum(p[k]*float(v) for k,v in row['terms'].items()); rhs = float(row['rhs'])
            if not {'>':lhs>rhs,'>=':lhs>=rhs,'<':lhs<rhs,'<=':lhs<=rhs,'==':lhs==rhs}.get(row['operator'],False):
                raise ValueError(f"failed physical condition {row['name']}")
    else:
        minimum = constraints.get('topfilm_gap_min_eV',constraints.get('min_topfilm_gap_eV',0.))
        if gap < minimum-1e-15: raise ValueError('upper-film gap below declared minimum')
        tc_min = max(float(constraints.get('topfilm_tc_min_K',0.)),
                     float(constraints.get('minimum_tc_over_temperature',0.))*float(constraints.get('operating_temperature_K',0.)))
        if film_tc_K(gap) < tc_min-1e-12: raise ValueError('film critical temperature below declared minimum')
        if gap/gap_j < constraints.get('topfilm_gap_over_junction_min',0.)-1e-12:
            raise ValueError('film-to-junction gap ratio below declared minimum')
    active = energy >= 2*gap
    declared = space.get('ground_plane_active_absorber',True)
    if declared is not None and active!=bool(declared):
        raise ValueError('candidate changes declared ground-plane absorption regime')
    if not exact: return {}
    vl,vt = derive_cubic_sound_speeds(c11,c12,c44,density)
    if not 0 < vt < vl: raise ValueError('require mean longitudinal speed > transverse speed > 0')
    si_l,si_t = derive_cubic_sound_speeds(165.6,63.9,79.5,2330.)
    if films is None:
        films = ({'g4_material':'G4_Nb','vsound_km_s':p['topfilm_vsound'],'density_kg_m3':p['topfilm_density']},
                 {'g4_material':'G4_Cu','vsound_km_s':p['bot_vsound'],'density_kg_m3':p['bot_density']})
    absorption,diagnostics = resolve_all_interfaces(
        {'density_kg_m3':density,'vsound_m_s':round(vl,6),'vtrans_m_s':round(vt,6)},
        BASELINE_FILM['junction_Al'],
        *[{'g4_material':f['g4_material'],'vsound_m_s':f['vsound_km_s']*1000.,'density_kg_m3':f['density_kg_m3']} for f in films],
        {'density_kg_m3':2330.,'vsound_m_s':si_l,'vtrans_m_s':si_t})
    result = {'substrate_density_kg_m3':density,'vsound_m_s':round(vl,6),'vtrans_m_s':round(vt,6),
        'anisotropy':round(2*c44/(c11-c12),6),'impedance_substrate':round(density*(vl+2*vt)/3,3),
        **{k:round(v,6) for k,v in absorption.items()},'interface_model':'baseline_calibrated_effective_AMM',
        'interface_diagnostics':diagnostics,'excitation_gate':{'min_e_phonons_eV':cutoff,'junction_gate_eV':2*gap_j,
            'gun_energy_eV':energy,'film_gate_eV':2*gap,'margin_above_junction_gate':energy/(2*gap_j),
            'ground_plane_active_absorber':active,'e_gun_over_film_gate':energy/(2*gap)}}
    if 'orientation_polar_cos' in p and 'orientation_azimuth_turns' in p:
        result['miller'] = sphere_to_miller(p['orientation_polar_cos'],p['orientation_azimuth_turns'])
    elif 'miller' in p:
        miller_to_sphere(p['miller']); result['miller'] = list(p['miller'])
    return result


def prepare_values(study, candidate=None):
    """x -> checked material, G4CMP lattice and calibrated boundary inputs."""
    candidate = candidate or {}; values = dict(study['space']['baseline'])
    changes = candidate.get('values',{k:v for k,v in candidate.items() if k not in
        ('name','materials','realization','macro','config','ode','inputs','trajectory','fixed','design_index','transport_group','problem','parameter_specs','tolerance')})
    values.update(changes)
    for name,spec in study.get('parameters',{}).items():
        spec = spec if isinstance(spec,dict) else {'mode':'fixed','value':spec}
        if spec.get('mode')=='fixed' and name in changes and changes[name]!=study['space']['baseline'][name]:
            raise ValueError(f'{name}: explicitly fixed input cannot be changed by a candidate')
    fixed = dict(study['fixed']); fixed.update(candidate.get('fixed',{})); validate_fixed(fixed)
    if 'miller' in values:
        values['orientation_polar_cos'],values['orientation_azimuth_turns'] = miller_to_sphere(values.pop('miller'))
    realization = dict(candidate.get('realization',study.get('realization',{})))
    realization.update(values.pop('_realization',{}))
    mode = realization.get('mode','pseudo_si_base'); carrier = realization.get('substrate_carrier','G4_Si')
    if mode not in ('pseudo_si_base','native_g4cmp','custom_material'):
        raise ValueError(f'unsupported material mode {mode}')
    catalog = study['material_catalog']; top = dict(catalog['top_films']['Nb']); bottom = dict(catalog['bottom_films']['Cu'])
    base = realization.get('base_lattice_map','Si'); lattice = realization.get('lattice_map','PseudoCubic')
    linked = candidate.get('materials'); sub = None
    if linked:
        subname,topname,botname = linked if isinstance(linked,list) else linked.split('/')
        sub = catalog['substrates'][subname]; top = dict(catalog['top_films'][topname]); bottom = dict(catalog['bottom_films'][botname])
        if any(row.get('enabled') is False for row in (sub,top,bottom)):
            raise ValueError('selected material is disabled or incomplete')
        mode = 'native_g4cmp'
        values.update(topfilm_vsound=top['vsound_km_s'],topfilm_gap=top['gap_eV'],topfilm_ph_lifetime=top['ph_lifetime_ns'],
                      topfilm_density=top['density_kg_m3'],bot_vsound=bottom['vsound_km_s'],bot_gap_thres=bottom['gap_threshold_eV'],
                      bot_ph_lifetime=bottom['ph_lifetime_ns'],bot_density=bottom['density_kg_m3'])
    elif mode=='native_g4cmp':
        sub = catalog['substrates'].get(realization.get('material'))
        if sub is None or sub.get('enabled') is False or sub.get('config_mode')!='native_g4cmp':
            raise ValueError('native material requires a complete named catalog substrate')
    elif mode=='custom_material' and not all(realization.get(k) for k in ('substrate_carrier','lattice_map')):
        raise ValueError('custom material needs a measured carrier and a complete lattice record')
    if sub is not None:
        carrier,base,lattice = sub['g4_material'],sub['lattice_map'],sub['lattice_map']
        realization['expected_density_kg_m3'] = sub['expected_density_kg_m3']
    if carrier not in DENSITIES: raise ValueError(f'{carrier}: no measured simulator density')
    expected = realization.get('material_density_kg_m3',realization.get('expected_density_kg_m3',DENSITIES[carrier]))
    if not math.isfinite(expected) or expected<=0 or abs(DENSITIES[carrier]/expected-1)>.03:
        raise ValueError('selected simulator material cannot represent requested density')
    if mode!='pseudo_si_base': base = lattice
    record = study['lattices'].get(base)
    if record is None: raise ValueError(f'lattice {base!r} was not loaded for this study')
    if mode!='pseudo_si_base':
        for name,key in {'sub_c11':'stiffness 1 1','sub_c12':'stiffness 1 2','sub_c44':'stiffness 4 4',
                         'sub_scat':'scat','sub_decay':'decay','sub_decayTT':'decayTT','sub_lattice_a':'cubic'}.items():
            values[name] = record['fields'][key][0]
    space = dict(study['space'])
    if mode!='pseudo_si_base':
        exempt = {'sub_c11','sub_c12','sub_c44','sub_scat','sub_decay','sub_decayTT','sub_lattice_a'}
        keep = [i for i,name in enumerate(space['names']) if name not in exempt]
        for key in ('names','low','high','scales'): space[key] = [space[key][i] for i in keep]
    space.update(density_kg_m3=DENSITIES[carrier],junction_gap_eV=fixed['setTopGap'],
                 gun_energy_eV=fixed['gun_energy_eV'],cutoff_eV=fixed['min_e_phonons_eV'])
    unknown = set(values)-set(space['baseline'])
    if unknown: raise ValueError(f'unknown material properties: {sorted(unknown)}')
    top.update(gap_eV=values['topfilm_gap'],vsound_km_s=values['topfilm_vsound'],
               ph_lifetime_ns=values['topfilm_ph_lifetime'],density_kg_m3=values['topfilm_density'])
    bottom.update(gap_threshold_eV=values['bot_gap_thres'],vsound_km_s=values['bot_vsound'],
                  ph_lifetime_ns=values['bot_ph_lifetime'],density_kg_m3=values['bot_density'])
    for film in (top,bottom):
        if film['qp_limit'] not in (2,3,4,5) or film['thickness_um']<=0:
            raise ValueError('film requires a safe QP limit and positive thickness')
    derived = validate_values(values,space,exact=True,films=(top,bottom))
    derived.update(g4_material_name=carrier,lattice_map_name=lattice,base_lattice_map=base,config_mode=mode,
                   point=values,config_source_sha256=record['sha256'])
    return {'values':values,'top_film':top,'bottom_film':bottom,'derived':derived,
            'source_config':record['source'],'source_text':record['text'],'fixed':fixed}


def build_design(fixed, sampling, *, recorded=None, previous=None):
    """Uniform or stratified sites; previous retains every earlier site and stream."""
    n,seed = sampling.get('sites',16),sampling.get('position_seed',20260727)
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (n,seed)) or n<1:
        raise ValueError('positive integer site count and integer position seed required')
    span = float(fixed.get('position_half_span_mm',4.)); z = float(sampling.get('z_mm',.259875))
    kind = sampling.get('kind','sobol'); design = None
    if previous is not None:
        design = {k:copy.deepcopy(v) for k,v in previous.items() if k not in ('design_hash','position_strata')}
        if n<len(design['sites_mm']): raise ValueError('refinement cannot remove earlier sites')
    elif kind=='recorded-stratified':
        if recorded is None: raise ValueError('recorded sampling requires a loaded design')
        design = copy.deepcopy(recorded.get('stratified_design',recorded.get('design',recorded)))
    elif kind=='sobol':
        if n&(n-1): raise ValueError('uniform Sobol site count must be a power of two')
        xy = qmc.Sobol(d=2,scramble=True,seed=seed).random_base2(int(math.log2(n)))*2*span-span
        design = {'sites_mm':[[round(float(a),6),round(float(b),6),z] for a,b in xy],
                  'stratum':['surface']*n,'stratum_weights':{'surface':1.},'stratum_counts':{'surface':n}}
    elif kind!='stratified':
        raise ValueError(f'unsupported spatial sampling {kind}')
    if design is not None:
        centers = np.column_stack((fixed['electrode_x_mm'],fixed['electrode_y_mm']))
        half = np.array([fixed['electrode_width_um'],fixed['electrode_height_um']])/2000.
        expected = {'half_span_mm':span,'position_half_span_mm':span,'junction_half_extent_mm':half,
                    'electrode_x_mm':centers[:,0],'electrode_y_mm':centers[:,1],
                    'electrode_width_um':fixed['electrode_width_um'],'electrode_height_um':fixed['electrode_height_um']}
        expected.update(dict.fromkeys(('junction_centers_mm','electrode_centers_mm','electrodes_mm','electrode_xy_mm'),centers))
        sources = [design]+([recorded] if kind=='recorded-stratified' and recorded is not None else [])
        for source in sources:
            for geometry in (source,*(source.get(name,{}) for name in ('physics','geometry','fixed'))):
                if not isinstance(geometry,dict): raise ValueError('recorded geometry must be a mapping')
                for name,value in expected.items():
                    if name in geometry:
                        actual = np.asarray(geometry[name],dtype=float)
                        if actual.shape!=np.shape(value) or not np.isfinite(actual).all() or not np.allclose(actual,value,rtol=0.,atol=1e-12):
                            raise ValueError(f'recorded {name} differs from study geometry')
    if design is None:
        total = int(sampling.get('area_samples',8000000)); rng = np.random.default_rng(int(sampling.get('area_seed',0)))
        if total<1: raise ValueError('area sample count must be positive')
        count = np.zeros(5,dtype=np.int64)
        for start in range(0,total,65536):
            count += np.bincount(region_distances(rng.uniform(-span,span,(min(65536,total-start),2)),fixed),minlength=5)
        # W0=sum_e A_e/A_source; other region fractions W_h use uniform area draws.
        w0 = len(fixed['electrode_x_mm'])*fixed['electrode_width_um']*fixed['electrode_height_um']*1e-6/(2*span)**2
        weights = np.r_[w0,count[1:]/count[1:].sum()*(1-w0)]
        floor = np.array([len(fixed['electrode_x_mm']),8,8,8,8]); remaining = n-int(floor.sum())
        if remaining<0: raise ValueError('site count below spatial-region minimum')
        if sampling.get('allocation'):
            target = np.array([sampling['allocation'][h] for h in REGIONS],dtype=int)
        else:
            sigma = np.array([sampling.get('region_sd',{}).get(h,1.) for h in REGIONS])
            if not np.isfinite(sigma).all() or np.any(sigma<0) or weights@sigma<=0:
                raise ValueError('region sampling spreads must be finite and nonnegative')
            # Allocate added sites as n_h proportional to W_h*sigma_h.
            shares = remaining*weights*sigma/(weights@sigma); target = floor+np.floor(shares).astype(int)
            for h in np.argsort(-(shares-np.floor(shares)))[:n-int(target.sum())]: target[h] += 1
        if target.sum()!=n or np.any(target<floor): raise ValueError('invalid spatial allocation')
        design = {'sites_mm':[],'stratum':[],'stratum_weights':dict(zip(REGIONS,map(float,weights)))}
    else:
        target = None
    sites = design['sites_mm']; labels = design.get('stratum',design.get('position_strata'))
    weights = design['stratum_weights']; regions = list(weights)
    if labels is None or len(labels)!=len(sites): raise ValueError('invalid recorded site labels')
    if len(sites)<n:
        if set(regions) not in ({'surface'},set(REGIONS)):
            raise ValueError('refinement requires surface or five physical regions')
        counts = np.array([labels.count(h) for h in regions]); seen = {tuple(p) for p in sites}
        if len(seen)!=len(sites): raise ValueError('initial design contains repeated sites')
        sigma = np.array([sampling.get('region_sd',{}).get(h,1.) for h in regions])
        allocation = np.array([weights[h] for h in regions])*sigma
        if not np.isfinite(allocation).all() or np.any(allocation<0) or allocation.sum()<=0:
            raise ValueError('region sampling spreads must be finite and nonnegative')
        allocation /= allocation.sum(); streams,buffers,cursors = {},{},{}
        for h,name in enumerate(regions):
            sub_seed = seed if name=='surface' else (seed*1000003+REGIONS.index(name))%(2**31-1)
            streams[name] = np.random.default_rng(sub_seed) if name=='S0_junction' else qmc.Sobol(d=2,scramble=True,seed=sub_seed)
            buffers[name] = []; cursors[name] = 0
        while len(sites)<n:
            h = int(np.flatnonzero(counts<target)[0]) if target is not None else int(np.argmax(n*allocation-counts))
            name = regions[h]; point = None
            for _ in range(100000):
                if not buffers[name]:
                    if name=='S0_junction':
                        e = cursors[name]%len(fixed['electrode_x_mm']); cursors[name] += 1
                        x = fixed['electrode_x_mm'][e]+streams[name].uniform(-fixed['electrode_width_um']/2000,fixed['electrode_width_um']/2000)
                        y = fixed['electrode_y_mm'][e]+streams[name].uniform(-fixed['electrode_height_um']/2000,fixed['electrode_height_um']/2000)
                        buffers[name] = [[round(float(x),6),round(float(y),6)]]
                    else:
                        xy = np.round(streams[name].random(8192)*2*span-span,6)
                        if name!='surface': xy = xy[region_distances(xy,fixed)==REGIONS.index(name)]
                        buffers[name] = xy.tolist()[::-1]
                if not buffers[name]: continue
                candidate = [*buffers[name].pop(),z]
                if tuple(candidate) not in seen:
                    point = candidate; break
            if point is None: raise ValueError(f'cannot append a distinct site in {name}')
            sites.append(point); labels.append(name); counts[h] += 1; seen.add(tuple(point))
    sites_array = np.asarray(sites,dtype=float)
    if sites_array.ndim!=2 or sites_array.shape[1]!=3 or not np.isfinite(sites_array).all():
        raise ValueError('invalid recorded sites')
    if set(labels)!=set(weights) or any(v<=0 or not math.isfinite(v) for v in weights.values()) or not math.isclose(sum(weights.values()),1.,abs_tol=1e-8):
        raise ValueError('invalid or uncovered spatial weights')
    if set(weights)==set(REGIONS) and np.any(np.asarray(REGIONS)[region_distances(sites_array[:,:2],fixed)]!=labels):
        raise ValueError('recorded site regions differ from study junction geometry')
    design['stratum'] = list(labels); design['stratum_counts'] = {h:labels.count(h) for h in regions}
    design['electrode_weights'] = design.get('electrode_weights',[1/len(fixed['electrode_x_mm'])]*len(fixed['electrode_x_mm']))
    return design
