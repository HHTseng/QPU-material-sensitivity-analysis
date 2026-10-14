"""Physical inputs, search coordinates, recorded sites and random streams."""

import copy
import itertools
import json
import math
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import qmc

import physics
import records

ROOT = Path(__file__).resolve().parent
REGIONS = ('S0_junction', 'S1_le_0.05', 'S2_le_0.20', 'S3_le_0.50', 'S4_bulk')
DENSITIES = {'G4_Si': 2330., 'G4_Ge': 5323., 'G4_GALLIUM_ARSENIDE': 5310.,
             'G4_CALCIUM_FLUORIDE': 3180., 'G4_LITHIUM_FLUORIDE': 2635.,
             'G4_LITHIUM_HYDRIDE':820., 'G4_ALUMINUM_OXIDE':3970., 'G4_BORON_CARBIDE':2520.}


def load_document(path):
    if isinstance(path, dict):
        return copy.deepcopy(path)
    path = Path(path).expanduser()
    value = json.loads(path.read_text()) if path.suffix.lower() == '.json' else yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a mapping')
    return value


def search_space(parameters=None, *, kind='search', constraints=None, table=None):
    table = table or load_document(ROOT / 'parameters.yaml')['parameters']
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
    values = np.array([point[name] for name in space['names']], dtype=float)
    lo, hi = np.asarray(space['low']), np.asarray(space['high'])
    logarithmic = np.array([s == 'log' for s in space['scales']])
    values[logarithmic] = np.log(values[logarithmic])
    lo = np.where(logarithmic, np.log(np.maximum(lo, 1e-300)), lo)
    hi = np.where(logarithmic, np.log(np.maximum(hi, 1e-300)), hi)
    return (values - lo) / (hi - lo)


def from_unit(u, space):
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


def validate_values(point, space, exact=False):
    values = dict(space['baseline']); values.update(point)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
           for k, v in values.items() if not k.startswith('_') and k != 'miller'):
        raise ValueError('material properties must be finite numbers')
    for name, lo, hi in zip(space['names'], space['low'], space['high']):
        if not lo <= values[name] <= hi:
            raise ValueError(f'{name}: outside declared search interval')
    ranges=space.get('valid_ranges',{})
    for name,value in values.items():
        if name in ranges:
            low,high=ranges[name]
            if not low<=value<=high:
                raise ValueError(f'{name}: outside physical input range')
    c11, c12, c44 = (values[n] for n in ('sub_c11', 'sub_c12', 'sub_c44'))
    if not (c44 > 0 and c11 > c12 and c11 + 2*c12 > 0):
        raise ValueError('unstable cubic stiffness')
    if not 0 < values['bot_gap_thres'] <= 2*space.get('junction_gap_eV', 191e-6) + 1e-12:
        raise ValueError('bottom threshold exceeds junction pair-breaking threshold')
    constraints = space.get('constraints') or {}
    if isinstance(constraints, list):
        for row in constraints:
            lhs = sum(values[k]*float(v) for k, v in row['terms'].items()); rhs = float(row['rhs'])
            valid = {'>': lhs > rhs, '>=': lhs >= rhs, '<': lhs < rhs, '<=': lhs <= rhs, '==': lhs == rhs}
            if not valid.get(row['operator'], False):
                raise ValueError(f"failed physical condition {row['name']}")
    else:
        gap = values['topfilm_gap']
        minimum = constraints.get('topfilm_gap_min_eV', constraints.get('min_topfilm_gap_eV'))
        if minimum is not None and gap < float(minimum) - 1e-15:
            raise ValueError('upper-film gap below declared minimum')
        tc = physics.film_tc_K(gap)
        minimum = constraints.get('topfilm_tc_min_K')
        if minimum is not None and tc < float(minimum) - 1e-12:
            raise ValueError('upper-film critical temperature below declared minimum')
        minimum = constraints.get('topfilm_gap_over_junction_min')
        if minimum is not None and gap / space.get('junction_gap_eV', 191e-6) < float(minimum) - 1e-12:
            raise ValueError('upper-film to junction gap ratio below declared minimum')
        temperature = constraints.get('operating_temperature_K')
        if temperature is not None and 'minimum_tc_over_temperature' in constraints:
            if tc < float(constraints['minimum_tc_over_temperature']) * float(temperature) - 1e-12:
                raise ValueError('critical-temperature condition failed')
    if not exact:
        return {}
    return physics.check_values(values, space.get('density_kg_m3', 2330.), constraints,
                                junction_gap_eV=space.get('junction_gap_eV', 191e-6),
                                gun_energy_eV=space.get('gun_energy_eV', .01),
                                cutoff_eV=space.get('cutoff_eV', 38.2e-6),
                                ground_plane_active_absorber=space.get('ground_plane_active_absorber', True))


def lattice_record(path):
    fields = {}
    required = {'cubic': (1, 'Ang'), 'stiffness 1 1': (1, 'GPa'), 'stiffness 1 2': (1, 'GPa'),
                'stiffness 4 4': (1, 'GPa'), 'dyn': (4, 'GPa'), 'scat': (1, 's3'), 'decay': (1, 's4'),
                'decayTT': (1, ''), 'LDOS': (1, ''), 'STDOS': (1, ''), 'FTDOS': (1, '')}
    for raw in Path(path).read_text().splitlines():
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


def prepare_values(study, candidate=None):
    candidate = copy.deepcopy(candidate or {})
    values = dict(study['space']['baseline'])
    changes=candidate.get('values', {k:v for k,v in candidate.items() if k not in ('name','materials','realization','macro','config','ode','inputs','trajectory','fixed','design_index','transport_group','problem','parameter_specs')})
    values.update(changes)
    values.pop('name', None)
    for name,spec in study.get('parameters',{}).items():
        if not isinstance(spec,dict): spec={'mode':'fixed','value':spec}
        if spec.get('mode')=='fixed' and name in changes and changes[name]!=study['space']['baseline'][name]:
            raise ValueError(f'{name}: explicitly fixed input cannot be changed by a candidate')
    if candidate.get('fixed'):
        study=copy.deepcopy(study); study['fixed'].update(candidate['fixed'])
    validate_fixed(study['fixed'])
    if 'miller' in values:
        values['orientation_polar_cos'],values['orientation_azimuth_turns']=physics.miller_to_sphere(values.pop('miller'))
    realization = copy.deepcopy(candidate.get('realization', study.get('realization', {})))
    realization.update(values.pop('_realization', {}))
    mode = realization.get('mode', 'pseudo_si_base')
    if mode not in ('pseudo_si_base', 'native_g4cmp', 'custom_material'):
        raise ValueError(f'unsupported material mode {mode}')
    carrier = realization.get('substrate_carrier', 'G4_Si')
    catalog = load_document(study.get('materials_file', ROOT / 'legacy/material_catalog.yaml'))
    top = dict(catalog['top_films']['Nb']); bottom = dict(catalog['bottom_films']['Cu'])
    base = realization.get('base_lattice_map', 'Si')
    lattice = realization.get('lattice_map', 'PseudoCubic')
    if mode=='native_g4cmp':
        material=realization.get('material')
        if material not in catalog['substrates']:
            raise ValueError('native material needs a complete named catalog substrate')
        sub=catalog['substrates'][material]
        if sub.get('enabled') is False or sub.get('config_mode')!='native_g4cmp':
            raise ValueError('native material catalog record is incomplete or disabled')
        carrier,base,lattice=sub['g4_material'],sub['lattice_map'],sub['lattice_map']
        realization['expected_density_kg_m3']=sub['expected_density_kg_m3']
    if mode=='custom_material' and not all(realization.get(k) for k in ('substrate_carrier','lattice_map')):
        raise ValueError('custom material needs a measured carrier and a complete lattice record')
    linked = candidate.get('materials')
    if linked:
        subname, topname, botname = linked if isinstance(linked, list) else linked.split('/')
        sub = catalog['substrates'][subname]; top = dict(catalog['top_films'][topname]); bottom = dict(catalog['bottom_films'][botname])
        if any(row.get('enabled') is False for row in (sub, top, bottom)):
            raise ValueError('selected material is disabled or incomplete')
        carrier, base, lattice, mode = sub['g4_material'], sub['lattice_map'], sub['lattice_map'], 'native_g4cmp'
        values.update(topfilm_vsound=top['vsound_km_s'], topfilm_gap=top['gap_eV'],
                      topfilm_ph_lifetime=top['ph_lifetime_ns'], topfilm_density=top['density_kg_m3'],
                      bot_vsound=bottom['vsound_km_s'], bot_gap_thres=bottom['gap_threshold_eV'],
                      bot_ph_lifetime=bottom['ph_lifetime_ns'], bot_density=bottom['density_kg_m3'])
    if linked:
        realization['expected_density_kg_m3']=sub['expected_density_kg_m3']
    if carrier not in DENSITIES:
        raise ValueError(f'{carrier}: no measured simulator density')
    expected=realization.get('material_density_kg_m3',realization.get('expected_density_kg_m3',DENSITIES[carrier]))
    if not math.isfinite(expected) or expected<=0 or abs(DENSITIES[carrier]/expected-1)>.03:
        raise ValueError('selected simulator material cannot represent the requested density')
    if mode != 'pseudo_si_base':
        base = lattice
    source = Path(study['installation']['lattice_data']) / base / 'config.txt'
    field = lattice_record(source)
    if mode != 'pseudo_si_base':
        for name, key in {'sub_c11':'stiffness 1 1','sub_c12':'stiffness 1 2','sub_c44':'stiffness 4 4',
                          'sub_scat':'scat','sub_decay':'decay','sub_decayTT':'decayTT','sub_lattice_a':'cubic'}.items():
            values[name] = field[key][0]
    space = copy.deepcopy(study['space'])
    if mode != 'pseudo_si_base':
        exempt={'sub_c11','sub_c12','sub_c44','sub_scat','sub_decay','sub_decayTT','sub_lattice_a'}
        keep=[i for i,name in enumerate(space['names']) if name not in exempt]
        for field_name in ('names','low','high','scales'):
            space[field_name]=[space[field_name][i] for i in keep]
    space['density_kg_m3'] = DENSITIES[carrier]
    unknown = set(values) - set(space['baseline']) - {'miller'}
    if unknown:
        raise ValueError(f'unknown material properties: {sorted(unknown)}')
    derived = validate_values(values, space, exact=True)
    top.update(gap_eV=values['topfilm_gap'], vsound_km_s=values['topfilm_vsound'],
               ph_lifetime_ns=values['topfilm_ph_lifetime'], density_kg_m3=values['topfilm_density'])
    bottom.update(gap_threshold_eV=values['bot_gap_thres'], vsound_km_s=values['bot_vsound'],
                  ph_lifetime_ns=values['bot_ph_lifetime'], density_kg_m3=values['bot_density'])
    for film in (top,bottom):
        if film['qp_limit'] not in (2,3,4,5) or film['thickness_um']<=0:
            raise ValueError('film requires a safe QP limit and positive thickness')
    si_l, si_t = physics.derive_cubic_sound_speeds(165.6, 63.9, 79.5, density=2330.)
    absorption, diagnostics = physics.resolve_all_interfaces(
        {'density_kg_m3':DENSITIES[carrier], 'vsound_m_s':derived['vsound_m_s'], 'vtrans_m_s':derived['vtrans_m_s']},
        {'g4_material':'G4_Al','vsound_m_s':3582.},
        {'g4_material':top['g4_material'],'vsound_m_s':top['vsound_km_s']*1000.,'density_kg_m3':top['density_kg_m3']},
        {'g4_material':bottom['g4_material'],'vsound_m_s':bottom['vsound_km_s']*1000.,'density_kg_m3':bottom['density_kg_m3']},
        {'density_kg_m3':2330.,'vsound_m_s':si_l,'vtrans_m_s':si_t})
    derived.update({key:round(value,6) for key,value in absorption.items()})
    derived.update(g4_material_name=carrier, lattice_map_name=lattice, base_lattice_map=base,
                   config_mode=mode, interface_diagnostics=diagnostics, point=values,
                   config_source_sha256=records.sha256_file(source))
    return {'values':values,'top_film':top,'bottom_film':bottom,'derived':derived,
            'source_config':str(source),'fixed':study['fixed']}


def region_distances(xy, fixed):
    xy = np.asarray(xy, dtype=float)
    centers = np.array([fixed['electrode_x_mm'], fixed['electrode_y_mm']]).T
    half = np.array([fixed['electrode_width_um'], fixed['electrode_height_um']])/2000.
    delta = np.maximum(np.abs(xy[:,None,:]-centers[None,:,:])-half,0.)
    distance = np.sqrt(np.sum(delta*delta,axis=2)).min(axis=1)
    return np.searchsorted([0., .05, .20, .50], distance, side='left')


def build_design(fixed, sampling):
    n = sampling.get('sites',16); seed = sampling.get('position_seed',20260727)
    if isinstance(n,bool) or not isinstance(n,int) or isinstance(seed,bool) or not isinstance(seed,int):
        raise ValueError('site count and position seed must be integers')
    span = float(fixed.get('position_half_span_mm',4.)); z = float(sampling.get('z_mm', .259875))
    if n < 1:
        raise ValueError('site count must be positive')
    kind = sampling.get('kind','sobol')
    if kind == 'recorded-stratified':
        document = load_document(sampling['design_file']); design = document.get('stratified_design', document.get('design', document))
        if 'sites_mm' not in design:
            raise ValueError('recorded design lacks sites_mm')
        design = copy.deepcopy(design)
    elif kind == 'sobol':
        if n & (n-1):
            raise ValueError('uniform Sobol site count must be a power of two')
        xy = qmc.Sobol(d=2,scramble=True,seed=seed).random_base2(int(math.log2(n))) * 2*span-span
        design = {'sites_mm':[[round(float(a),6),round(float(b),6),z] for a,b in xy],
                  'stratum':['surface']*n,'stratum_weights':{'surface':1.},'stratum_counts':{'surface':n}}
    elif kind == 'stratified':
        rng = np.random.default_rng(int(sampling.get('area_seed',0)))
        count = np.zeros(5,dtype=np.int64); total = int(sampling.get('area_samples',8000000))
        if total < 1:
            raise ValueError('area sample count must be positive')
        for start in range(0,total,65536):
            count += np.bincount(region_distances(rng.uniform(-span,span,(min(65536,total-start),2)),fixed),minlength=5)
        w0 = len(fixed['electrode_x_mm'])*fixed['electrode_width_um']*fixed['electrode_height_um']*1e-6/(2*span)**2
        weights = np.r_[w0, count[1:]/count[1:].sum()*(1-w0)]
        floor = np.array([len(fixed['electrode_x_mm']),8,8,8,8]); remaining = n-int(floor.sum())
        if remaining < 0:
            raise ValueError('site count below spatial-region minimum')
        if sampling.get('allocation'):
            allocation = np.array([sampling['allocation'][h] for h in REGIONS],dtype=int)
        else:
            sigma = np.array([sampling.get('region_sd',{}).get(h,1.) for h in REGIONS])
            shares = remaining*weights*sigma/(weights@sigma); allocation = floor+np.floor(shares).astype(int)
            for index in np.argsort(-(shares-np.floor(shares)))[:n-int(allocation.sum())]:
                allocation[index] += 1
        if allocation.sum()!=n or np.any(allocation<floor):
            raise ValueError('invalid spatial allocation')
        sites, labels = [], []
        for h, needed in enumerate(allocation):
            sub_seed = (seed*1000003+h) % (2**31-1)
            accepted = []
            if h==0:
                eng=np.random.default_rng(sub_seed)
                for i in range(needed):
                    e=i%len(fixed['electrode_x_mm'])
                    accepted.append([round(fixed['electrode_x_mm'][e]+eng.uniform(-fixed['electrode_width_um']/2000,fixed['electrode_width_um']/2000),6),
                                     round(fixed['electrode_y_mm'][e]+eng.uniform(-fixed['electrode_height_um']/2000,fixed['electrode_height_um']/2000),6)])
            else:
                eng=qmc.Sobol(d=2,scramble=True,seed=sub_seed)
                for _ in range(8000):
                    xy=np.round(eng.random(8192)*2*span-span,6)
                    accepted.extend(xy[region_distances(xy,fixed)==h].tolist())
                    if len(accepted)>=needed:
                        break
                if len(accepted)<needed:
                    raise ValueError(f'cannot fill spatial region {REGIONS[h]}')
            sites.extend([a,b,z] for a,b in accepted[:needed]); labels.extend([REGIONS[h]]*needed)
        design={'sites_mm':sites,'stratum':labels,'stratum_weights':dict(zip(REGIONS,map(float,weights))),
                'stratum_counts':dict(zip(REGIONS,map(int,allocation)))}
    else:
        raise ValueError(f'unsupported spatial sampling {kind}')
    sites=np.asarray(design['sites_mm'],dtype=float); labels=design.get('stratum',design.get('position_strata'))
    weights=design['stratum_weights']
    if sites.ndim!=2 or sites.shape[1]!=3 or not np.isfinite(sites).all() or len(labels)!=len(sites):
        raise ValueError('invalid recorded sites')
    if set(labels)!=set(weights) or any(v<=0 or not math.isfinite(v) for v in weights.values()) or not math.isclose(sum(weights.values()),1.,abs_tol=1e-8):
        raise ValueError('invalid or uncovered spatial weights')
    design['stratum']=list(labels)
    design['electrode_weights']=design.get('electrode_weights',[1/len(fixed['electrode_x_mm'])]*len(fixed['electrode_x_mm']))
    design['design_hash']=design.get('design_hash',records.content_hash(design))
    return design


def load_study(path):
    source = Path(path).resolve() if not isinstance(path,dict) else ROOT/'study.yaml'
    raw = load_document(path)
    if 'resolved_hash' in raw:
        expected=raw.pop('resolved_hash')
        if records.content_hash(raw)!=expected:
            raise ValueError('resolved study was changed after recording')
        if raw['events_total']!=len(raw['design']['sites_mm'])*raw['sampling']['replicas']*raw['sampling']['events_per_task']:
            raise ValueError('resolved task event counts disagree')
        raw['resolved_hash']=expected
        return raw
    allowed={'schema','id','kind','description','purpose','parameters','fixed','sampling','analysis','execution','candidates','search','constraints',
             'installation','realization','materials_file','output','steps','event_counts','physics','build','provenance','tolerance','levels','morris'}
    if set(raw)-allowed:
        raise ValueError(f'unknown study fields: {sorted(set(raw)-allowed)}')
    if raw.get('schema') not in ('material-scan-study-2','material-scan-experiment-1'):
        raise ValueError('unsupported study schema')
    if raw.get('kind','confirmation') not in ('confirmation','materials','search','sensitivity','tolerance','convergence','sequence'):
        raise ValueError('unknown scientific study kind')
    reference = load_document(ROOT/'studies/reference.yaml')
    fixed = dict(reference['fixed']); fixed.update(raw.get('fixed',{}))
    if set(raw.get('fixed',{}))-set(reference['fixed']):
        raise ValueError('unknown fixed physical fields')
    phys = raw.get('physics',{})
    for name in ('electrode_x_mm','electrode_y_mm'):
        if name in phys: fixed[name]=phys[name]
    fixed['setTopGap']=phys.get('junction_gap_eV',fixed['setTopGap'])
    install={'executable':str(Path.home()/'geant4_workdir/bin/Linux-g++/Main'),
             'lattice_data':str(Path.home()/'src/G4CMP_htseng/CrystalMaps'),
             'environment_scripts':['/home/software/Geant4/Geant4-Install/share/Geant4-10.7.4/geant4make/geant4make.sh',str(Path.home()/'src/G4CMP_htseng/g4cmp_env.sh')],
             'library_dir':str(Path.home()/'geant4_workdir/lib/Linux-g++'),
             'macro_template':str(ROOT/'legacy/macros/sensitivity_template_beamOn1e6.mac')}
    install.update(raw.get('installation',{}))
    for name in ('executable','lattice_data','macro_template','library_dir'):
        value=Path(install[name]).expanduser()
        install[name]=str(value if value.is_absolute() else source.parent/value)
    install['environment_scripts']=[str((source.parent/Path(p).expanduser()).resolve()) if not Path(p).expanduser().is_absolute() else str(Path(p).expanduser()) for p in install.get('environment_scripts',[])]
    sampling=dict(reference['sampling']); sampling.update(raw.get('sampling',{}))
    if 'seed' in sampling:
        sampling['seed_base']=sampling['seed']['base']; sampling['seed_bank']=sampling['seed']['bank_id']
    sampling['events_per_task']=raw.get('event_counts',{}).get('events_per_task',sampling['events_per_task'])
    fixed['gun_energy_eV']=raw.get('event_counts',{}).get('gun_energy_eV',fixed['gun_energy_eV'])
    if 'design_file' in sampling:
        sampling['design_file']=str((source.parent/sampling['design_file']).resolve())
    for name in ('replicas','events_per_task','seed_base','seed_bank','position_seed'):
        value=sampling[name]
        if isinstance(value,bool) or not isinstance(value,int) or value < (1 if name in ('replicas','events_per_task') else 0):
            raise ValueError(f'{name}: invalid integer')
    execution=dict(reference['execution']); execution.update(raw.get('execution',{}))
    if isinstance(execution['workers'],bool) or not isinstance(execution['workers'],int):
        raise ValueError('workers must be a positive integer')
    if execution['workers']<1 or execution['timeout_s']<0 or execution['stall_timeout_s']<0:
        raise ValueError('invalid process limits')
    parameters=copy.deepcopy(raw.get('parameters',{}))
    if 'temperature_K' in raw.get('fixed',{}) and 'temperature' not in parameters:
        parameters['temperature']={'mode':'fixed','value':fixed['temperature_K']}
    space=search_space(parameters,kind=raw.get('kind','confirmation'),constraints=raw.get('constraints'))
    if 'temperature' not in raw.get('parameters',{}):
        space['baseline']['temperature']=fixed['temperature_K']
    elif 'temperature_K' in raw.get('fixed',{}):
        if 'temperature' in space['names'] or space['baseline']['temperature']!=fixed['temperature_K']:
            raise ValueError('fixed temperature and parameter temperature disagree')
    space.update(junction_gap_eV=fixed['setTopGap'],gun_energy_eV=fixed['gun_energy_eV'],cutoff_eV=fixed['min_e_phonons_eV'])
    study={**raw,'id':raw.get('id',source.stem),'kind':raw.get('kind','confirmation'),'parameters':parameters,'fixed':fixed,'space':space,'installation':install,
           'sampling':sampling,'execution':execution,'analysis':{**reference['analysis'],**raw.get('analysis',{})}}
    if study['kind']=='sensitivity' and 'objective' not in raw.get('analysis',{}):
        study['analysis']['objective']='integrated_density'
    if study['analysis']['objective'] not in ('total_qps_per_primary','total_QPs_per_primary','junction_qps_per_primary',
                                             'device_junction_qps_per_eV','device_weighted_junction_qps_per_energy','integrated_density'):
        raise ValueError('unknown physical objective')
    study['realization']=raw.get('realization',phys.get('realization',{}))
    if 'materials_file' in raw:
        study['materials_file']=str((source.parent/raw['materials_file']).resolve())
    study['design']=build_design(fixed,sampling)
    count=len(study['design']['sites_mm'])*sampling['replicas']*sampling['events_per_task']
    if raw.get('event_counts',{}).get('events_total',count)!=count:
        raise ValueError('declared total primary count differs from task grid')
    study['events_total']=count
    study['resolved_hash']=records.content_hash(study)
    return study


def task_plan(study, prepared, build_identity):
    sampling=study['sampling']; tasks=[]
    for r in range(sampling['replicas']):
        for i,site in enumerate(study['design']['sites_mm']):
            seed=(sampling['seed_base']+sampling['seed_bank']*7919003+prepared.get('trajectory',0)*1000003+r*10007+i*101)%900000000+1
            raw={'position':i,'replica':r,'site_mm':site,'seeds':[seed,seed+1],'events':sampling['events_per_task'],
                 'stratum':study['design']['stratum'][i]}
            transport={key:value for key,value in prepared.items() if key not in ('ode','trajectory','design_index','transport_group','problem','parameter_specs')}
            raw['task_id']=records.content_hash({'physics':transport,'task':raw,'build':build_identity})
            tasks.append(raw)
    return tasks


def candidate_table(study):
    kind = study['kind']; specified = study.get('candidates')
    if kind == 'materials' and not specified:
        catalog = load_document(study.get('materials_file', ROOT / 'legacy/material_catalog.yaml'))
        names = [[k for k, v in catalog[section].items() if v.get('enabled')]
                 for section in ('substrates', 'top_films', 'bottom_films')]
        specified = [{'name': '/'.join(row), 'materials': list(row)} for row in itertools.product(*names)]
    if kind == 'tolerance':
        specified = specified or [{'name': 'reference', 'values': {}}]
        base = copy.deepcopy(specified[0])
        point = {**study['space']['baseline'], **base.get('values', {})}
        specified = [base]
        table = load_document(ROOT / 'parameters.yaml')['parameters']
        skipped = []
        for name, fraction in study.get('tolerance', {}).items():
            if name not in table or name not in point or not 0 < float(fraction) < 1:
                raise ValueError('tolerance needs known parameters and fractions in (0,1)')
            spec = study.get('parameters', {}).get(name, {})
            spec = spec if isinstance(spec, dict) else {}
            lo, hi = map(float, spec.get('bounds', spec.get('range', table[name]['search_range'])))
            scale = spec.get('scale', table[name].get('scale', 'linear'))
            interval = {'names': [name], 'low': [lo], 'high': [hi], 'scales': [scale],
                        'baseline': point, 'constraints': study['space'].get('constraints', {})}
            centre = float(to_unit(point, interval)[0])
            for sign in (-1, 1):
                u = float(np.clip(centre + sign * float(fraction), 0., 1.))
                label = f'{name}_{sign:+d}'
                if abs(u - centre) < 1e-9:
                    skipped.append({'name': label, 'reason': 'already at this search bound'})
                    continue
                moved = from_unit([u], interval)
                try:
                    validate_values(moved, study['space'], exact=False)
                except ValueError as exc:
                    skipped.append({'name': label, 'reason': str(exc)})
                    continue
                specified.append({**copy.deepcopy(base), 'name': label, 'values': moved,
                                  'tolerance': {'variable': name, 'sign': sign,
                                                'fraction': float(fraction), 'unit_delta': u - centre}})
        study['tolerance_skipped'] = skipped
    if kind == 'sensitivity':
        from SALib.sample.morris import sample
        table = load_document(ROOT / 'parameters.yaml')
        rows, options = table['morris'], study.get('morris', {})
        entries = [row for section in ('electrode_params', 'detector_params', 'G4CMP_params',
                                      'config_params', 'QPDE_params') for row in rows[section]]
        requested = options.get('parameters')
        if requested:
            requested = {name.strip() for name in requested}
            unknown = requested - {row[0].strip() for row in entries}
            if unknown:
                raise ValueError(f'unknown Morris parameters: {sorted(unknown)}')
            entries = [row for row in entries if row[0].strip() in requested]
        if not entries:
            raise ValueError('no sensitivity parameters selected')
        names = [row[0].strip() for row in entries]
        problem = {'num_vars': len(names), 'names': names, 'bounds': [row[2] for row in entries]}
        trajectories = int(options.get('trajectories', 4)); levels = int(options.get('levels', 4))
        seed = int(options.get('seed', 41))
        if trajectories < 1 or levels < 2 or levels % 2:
            raise ValueError('positive trajectory count and an even number of Morris levels required')
        length = len(names) + 1
        candidate_count = 2 * trajectories
        baseline = study['space']['baseline']
        while True:
            raw = np.round(sample(problem, candidate_count, num_levels=levels, seed=seed), 6)
            paths = raw.reshape(candidate_count, length, len(names))
            stiffness = []
            for command, canonical in (('stiffness 1 1', 'sub_c11'), ('stiffness 1 2', 'sub_c12'),
                                       ('stiffness 4 4', 'sub_c44')):
                stiffness.append(paths[:, :, names.index(command)] if command in names
                                 else np.full(paths.shape[:2], baseline[canonical]))
            c11, c12, c44 = stiffness
            valid = np.all((c11 > c12) & (c11 + 2 * c12 > 0) & (c44 > 0), axis=1)
            if int(valid.sum()) >= trajectories:
                samples = paths[valid][:trajectories].reshape(trajectories * length, len(names))
                break
            candidate_count *= 2
            if candidate_count > int(options.get('max_candidate_trajectories', 1048576)):
                raise ValueError('too few complete Born-stable Morris trajectories within the requested box')
        destinations = {row['target']: name for name, row in table['parameters'].items()}
        fixed_names = {'/main/electrode_param/setHeight': 'electrode_height_um',
                       '/main/electrode_param/setWidth': 'electrode_width_um',
                       '/main/electrode_param/setIsland': 'electrode_island_um',
                       '/main/electrode_param/setIslandSpacing': 'electrode_island_spacing_um'}
        specs = [{'name': name, 'bounds': list(row[2]), 'unit': row[3].strip() if len(row) > 3 else '',
                  'destination': 'postprocess' if name in ('f_01', 'r', 's', 'I_ph', 'pt', 'n_cooper')
                                 else 'macro' if name.startswith('/') else 'lattice'}
                 for name, row in zip(names, entries)]
        study['morris_design'] = {'problem': problem, 'parameter_specs': specs, 'seed': seed,
                                  'levels': levels, 'trajectories': trajectories,
                                  'candidate_trajectories': candidate_count,
                                  'rejected_trajectories': candidate_count - int(valid.sum())}
        specified = []
        for i, sample_row in enumerate(samples):
            values, macro, config, ode, fixed = {}, {}, {}, {}, {}
            for name, entry, value in zip(names, entries, sample_row):
                value = float(value)
                unit = entry[3].strip() if len(entry) > 3 else ''
                if name in ('f_01', 'r', 's', 'I_ph', 'pt', 'n_cooper'):
                    ode[name] = value
                    continue
                if name.startswith('/'):
                    macro[name] = f'{value:.12g}' + (f' {unit}' if unit else '')
                    target = 'macro:' + name
                    if name in fixed_names:
                        fixed[fixed_names[name]] = value
                else:
                    if unit.startswith('e-'):
                        exponent, unit = unit.split(' ', 1)
                        value *= float('1' + exponent)
                    config[name] = [value, unit]
                    target = 'config:' + name
                if target in destinations:
                    values[destinations[target]] = value
            transport = {'values': values, 'macro': macro, 'config': config, 'fixed': fixed,
                         'trajectory': i // length}
            specified.append({'name': f'morris_{i:04d}', **transport, 'ode': ode,
                              'inputs': dict(zip(names, map(float, sample_row))),
                              'design_index': i, 'transport_group': records.content_hash(transport)})
    specified = specified or [{'name': 'reference', 'values': {}}]
    if len({point.get('name') for point in specified}) != len(specified):
        raise ValueError('candidate names must be unique')
    return specified


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
    records.write_json(destination, snapshot)
    return snapshot
