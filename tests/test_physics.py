"""Saved numerical answers, independent physical limits, and search mathematics."""

import csv
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import analysis
import physics

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures'


def fixture_case(data, name):
    prefix = 's128_design_' if name.startswith('s128') else 's512_design_'
    total = data[name + '_total_qps']
    electrodes = data[name + '_per_electrode_qps']
    events = int(data[name + '_events'])
    blocks = [dict(position=p, replica=r, events=events, total_qps=float(total[r, p]),
                   per_electrode_qps=electrodes[r, p].tolist(), n_hits=int(data[name + '_n_hits'][r, p]))
              for r in range(total.shape[0]) for p in range(total.shape[1])]
    design = dict(position_strata=data[prefix + 'strata'].tolist(),
                  stratum_weights=dict(zip(data[prefix + 'weight_names'].tolist(), data[prefix + 'weights'].tolist())),
                  replica_ids=list(range(total.shape[0])), events_per_task=events,
                  gun_energy_eV=float(data[prefix + 'gun_energy_eV']),
                  electrode_weights=data[prefix + 'electrode_weights'].tolist(),
                  design_hash=str(data[prefix + 'hash']))
    return blocks, design


class PhysicsTests(unittest.TestCase):
    def test_saved_hits_reproduce_every_electrode_and_time_bin(self):
        folder = FIXTURES / 'random_trial_9a3f0ab28d9d'
        meta = json.loads((folder / 'fixture.json').read_text())
        paths = sorted((folder / 'hits').glob('*.txt'))
        self.assertEqual(len(paths), meta['file_count'])
        self.assertEqual(sum(p.stat().st_size for p in paths), meta['total_bytes'])
        digest, n_hits = hashlib.sha256(), 0
        actual = np.zeros((len(meta['electrode_x_mm']), 1001))
        totals = np.zeros(actual.shape[0])
        names = ('Energy Deposited [eV]', 'End X [m]', 'End Y [m]', 'End Z [m]', 'Final Time [ns]')
        for path in paths:
            digest.update(path.name.encode()); digest.update(b'\0'); digest.update(path.read_bytes()); digest.update(b'\0')
            with path.open(newline='') as handle:
                rows = list(csv.DictReader(handle))
            columns = [[float(row[name]) for row in rows] for name in names]
            n_hits += len(rows)
            args = (*columns, meta['gap_eV'], meta['electrode_x_mm'], meta['electrode_y_mm'], meta['chip_surface_z_m'])
            time, counts = physics.qp_counts(*args, time_resolved=True)
            counts_only = physics.qp_counts(*args)
            np.testing.assert_array_equal(counts_only, counts.sum(axis=1))
            actual += counts; totals += counts_only
        self.assertEqual(digest.hexdigest(), meta['fixture_sha256'])
        expected_file = folder / 'expected_qps.npz'
        self.assertEqual(hashlib.sha256(expected_file.read_bytes()).hexdigest(), meta['expected_qps_npz_sha256'])
        with np.load(expected_file, allow_pickle=False) as expected:
            np.testing.assert_array_equal(time, expected['snapshot_times_ns'])
            np.testing.assert_array_equal(actual, expected['qps_by_electrode_time'])
        np.testing.assert_array_equal(totals, meta['expected']['per_electrode_qps'])
        self.assertEqual(n_hits, meta['expected']['n_hits'])
        self.assertEqual(actual.sum(), meta['expected']['total_qps'])
        self.assertEqual(actual.sum() / (len(paths) * meta['events_per_file']), meta['expected']['qps_per_primary'])

    def test_scoring_ties_and_surface_selection(self):
        surface = .0002625
        args = ([1.5, 2.5, .5, 3.5, 100., -10.], [0., .001, -.001, -.001, .001, -.001],
                [0.] * 6, [surface, surface, surface, surface + 5e-9, surface + 5e-8, surface],
                [150., 450., 0., 0., 0., 0.], 1., [-1., 1.], [0., 0.], surface)
        time, qps = physics.qp_counts(*args, time_resolved=True)
        self.assertEqual(time[1], 300.)
        self.assertEqual(qps[0, 0], 6.)
        self.assertEqual(qps[1, 1], 2.)
        self.assertEqual(qps.sum(), 8.)
        np.testing.assert_array_equal(physics.qp_counts(*args), [6., 2.])

    def test_hit_validation_and_measured_zero(self):
        args = ([], [], [], [], [], .000191, [0.], [0.], .0002625)
        self.assertEqual(float(physics.qp_counts(*args).sum()), 0.)
        self.assertEqual(float(physics.qp_counts(*args, time_resolved=True)[1].sum()), 0.)
        for invalid in ([float('nan')], [float('inf')]):
            with self.assertRaises(ValueError):
                physics.qp_counts([1.], invalid, [0.], [.0002625], [0.], .000191, [0.], [0.], .0002625)
        with self.assertRaises(ValueError):
            physics.qp_counts([1.], [], [0.], [.0002625], [0.], .000191, [0.], [0.], .0002625)

    def test_density_evolution_retains_golden_euler_result(self):
        args = ([0., 300., 600.], [[1., 2., 0.], [0., 1., 3.]], 4e10, 5e9, .1, .2, .3, .6, 4., 5., 6., .1, 1000)
        time, decoherence = physics.calculate_xqps(*args)
        np.testing.assert_allclose(time, [0., .3, .6, .8999999999999999, 1.2], rtol=1e-12, atol=1e-15)
        np.testing.assert_allclose(decoherence,
            [[0., .3, 1.1819999325, 1.7110788887071193, 1.6084119595414699],
             [0., 0., .3, 1.4819999325, 2.2930782893071497]], rtol=1e-12, atol=1e-15)
        for index in (4, 5, 6, 7):
            bad = list(args); bad[index] = -1.
            with self.assertRaises(ValueError):
                physics.calculate_xqps(*bad)

    def test_isotropic_christoffel_speeds_and_units(self):
        lam, mu, rho = 40., 30., 2500.
        matrix = np.zeros((6, 6)); matrix[:3, :3] = lam
        np.fill_diagonal(matrix[:3, :3], lam + 2 * mu)
        np.fill_diagonal(matrix[3:, 3:], mu)
        expected = [math.sqrt((lam + 2 * mu) * 1e9 / rho), math.sqrt(mu * 1e9 / rho)]
        for direction in ([1, 0, 0], [1, 1, 1], [.31, -.67, .9]):
            modes = physics.acoustic_velocities(matrix, rho, direction)
            self.assertAlmostEqual(modes['v_L'], expected[0], places=10)
            np.testing.assert_allclose(modes['v_T'], [expected[1]] * 2, rtol=1e-14)
            pa_modes = physics.acoustic_velocities(matrix * 1e9, rho, direction, units='Pa')
            np.testing.assert_array_equal(modes['velocities'], pa_modes['velocities'])
        np.testing.assert_allclose(physics.derive_cubic_sound_speeds(lam + 2 * mu, lam, mu, rho), expected, rtol=1e-14)
        speeds = physics.derive_cubic_sound_speeds(165.6, 63.9, 79.5)
        np.testing.assert_allclose(speeds, [9016.674620124095, 5369.509100019798], rtol=1e-13)
        np.testing.assert_allclose(physics.derive_cubic_sound_speeds(165.6, 63.9, 79.5, 4 * 2330.), np.array(speeds) / 2, rtol=1e-14)
        for C in ((1., 2., 1.), (1., -1., 1.), (1., 0., 0.)):
            with self.assertRaises(ValueError):
                physics.derive_cubic_sound_speeds(*C)

    def test_orientation_embedding_and_quantization(self):
        for z, turn in ((0., 0.), (.1, .333), (-.9, .61), (1., .7), (-1., .3)):
            miller = physics.sphere_to_miller(z, turn)
            actual_z, actual_turn = physics.miller_to_sphere(miller)
            self.assertLess(abs(z - actual_z), 2e-6)
            if abs(z) != 1:
                self.assertLess(abs(((actual_turn - turn + .5) % 1) - .5), 2e-6)
        self.assertEqual(physics.sphere_to_miller(.2, 0.), physics.sphere_to_miller(.2, 1.))
        self.assertEqual(physics.sphere_to_miller(1., .2), [0, 0, 1])
        with self.assertRaises(ValueError):
            physics.miller_to_sphere([0, 0, 0])

    def test_interface_baseline_and_film_dependence(self):
        vl, vt = physics.derive_cubic_sound_speeds(165.6, 63.9, 79.5)
        si = dict(density_kg_m3=2330., vsound_m_s=vl, vtrans_m_s=vt)
        al, nb, cu = [physics.BASELINE_FILM[k] for k in ('junction_Al', 'top_film_Nb', 'bottom_film_Cu')]
        values, diagnostics = physics.resolve_all_interfaces(si, al, nb, cu, si)
        np.testing.assert_allclose(list(values.values()), [.795, .745, .736], rtol=0, atol=1e-15)
        changed, _ = physics.resolve_all_interfaces(si, al, {**nb, 'density_kg_m3': 4500.}, cu, si)
        self.assertNotEqual(changed['setTopFilmAbs'], values['setTopFilmAbs'])
        self.assertEqual(changed['setTopAbs'], values['setTopAbs'])
        self.assertEqual(changed['setBotAbs'], values['setBotAbs'])
        self.assertEqual(diagnostics['top_film']['baseline_film'], 'G4_Nb')
        with self.assertRaises(ValueError):
            physics.resolve_all_interfaces(si, al, {**nb, 'density_kg_m3': float('nan')}, cu, si)

    def test_physical_thresholds_and_declared_film_gap(self):
        point = dict(sub_c11=165.6, sub_c12=63.9, sub_c44=79.5, topfilm_gap=.0015384,
                     topfilm_vsound=2.444, topfilm_density=8570., bot_vsound=2.608,
                     bot_density=8960., bot_gap_thres=.000382)
        space = {'baseline': point, 'ground_plane_active_absorber': None}
        derived = physics.validate_values(point, space, exact=True)
        self.assertEqual(derived['setTopAbs'], .795)
        self.assertTrue(derived['excitation_gate']['ground_plane_active_absorber'])
        self.assertFalse(physics.validate_values(point, {**space, 'gun_energy_eV': .000382}, exact=True)['excitation_gate']['ground_plane_active_absorber'])
        cases = ({'gun_energy_eV': .000381}, {'cutoff_eV': .000382}, {'ground_plane_active_absorber': False})
        for kwargs in cases:
            with self.assertRaises(ValueError):
                physics.validate_values(point, {**space, **kwargs}, exact=True)
        with self.assertRaises(ValueError):
            physics.validate_values({**point, 'topfilm_gap': .0001}, {**space, 'constraints': {'topfilm_gap_min_eV': .0015384}}, exact=True)
        with self.assertRaises(ValueError):
            physics.validate_values({**point, 'bot_gap_thres': .0004}, space, exact=True)

    def test_lattice_inputs_reject_ambiguous_fields_and_units(self):
        import records
        text = ('cubic 5.431 Ang\nstiffness 1 1 165.6 GPa\nstiffness 1 2 63.9 GPa\n'
                'stiffness 4 4 79.5 GPa\ndyn -42.9 -94.5 52.4 68.0 GPa\n'
                'scat 2.43e-42 s3\ndecay 7.41e-56 s4\ndecayTT 0.74\n'
                'LDOS 0.093\nSTDOS 0.531\nFTDOS 0.376\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.txt'
            path.write_text(text)
            self.assertEqual(records.lattice_record(path)['stiffness 1 1'], [165.6])
            for invalid in (text + 'stiffness 1 1 170 GPa\n',
                            text.replace('165.6 GPa', '1.656e11 Pa'),
                            text.replace('165.6 GPa', 'nan GPa')):
                path.write_text(invalid)
                with self.assertRaises(ValueError):
                    records.lattice_record(path)

    def test_thermal_scaling_and_finite_log_ratios(self):
        al, nb = .000191, .0015384
        self.assertGreater(physics.thermal_qp_density(al, .02), physics.thermal_qp_density(nb, .02))
        self.assertAlmostEqual(physics.film_tc_K(nb), nb / (1.764 * 8.617333262e-5))
        d = physics.engineering_diagnostics({'topfilm_gap': .00005})
        self.assertTrue(d['ground_plane_is_phonon_sink'])
        self.assertGreater(d['log10_loss_proxy_vs_Nb'], 300.)
        self.assertTrue(math.isfinite(d['log10_thermal_qp_density_ratio_vs_Nb']))

    def test_candidate_preparation_uses_loaded_records_and_one_interface_calculation(self):
        import records
        table = records.load_document(ROOT / 'parameters.yaml')['parameters']
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        catalog = records.load_document(ROOT / 'legacy/material_catalog.yaml')
        fields = {'cubic': [5.431], 'stiffness 1 1': [165.6], 'stiffness 1 2': [63.9],
                  'stiffness 4 4': [79.5], 'scat': [2.43e-42], 'decay': [7.41e-56], 'decayTT': [.74]}
        study = {'space': physics.search_space(kind='confirmation', table=table), 'fixed': fixed,
                 'material_catalog': catalog, 'lattices': {'Si': {'fields': fields,
                     'source': '/deliberately-unavailable/config.txt', 'sha256': 'recorded-hash', 'text': 'recorded source'}}}
        original = json.dumps(study, sort_keys=True)
        with patch('builtins.open', side_effect=AssertionError('candidate opened a file')), \
             patch.object(physics, 'resolve_all_interfaces', wraps=physics.resolve_all_interfaces) as interfaces:
            result = physics.prepare_values(study)
        self.assertEqual(interfaces.call_count, 1)
        self.assertEqual(result['derived']['setTopAbs'], .795)
        self.assertEqual(result['source_text'], 'recorded source')
        self.assertEqual(json.dumps(study, sort_keys=True), original)

    def test_spatial_planner_retains_recorded_prefix_and_checked_random_stream(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        sampling = {'kind': 'sobol', 'sites': 16, 'position_seed': 20260727}
        initial = physics.build_design(fixed, sampling)
        extended = physics.build_design(fixed, {**sampling, 'sites': 32}, previous=initial)
        self.assertEqual(extended['sites_mm'][:16], initial['sites_mm'])
        self.assertEqual(extended['sites_mm'][-2:], [[2.722533, 3.739975, .259875], [-2.285265, -.939001, .259875]])
        sampling.update(kind='stratified', sites=128, area_samples=65536)
        initial = physics.build_design(fixed, sampling)
        extended = physics.build_design(fixed, {**sampling, 'sites': 160}, previous=initial)
        self.assertEqual(extended['sites_mm'][:128], initial['sites_mm'])
        self.assertEqual(extended['stratum_weights'], initial['stratum_weights'])
        self.assertEqual(extended['sites_mm'][-2:], [[3.944305, 3.419599, .259875], [-3.035756, -2.719161, .259875]])
        self.assertEqual(len({tuple(p) for p in extended['sites_mm']}), 160)
        with self.assertRaises(ValueError):
            physics.build_design(fixed, {**sampling, 'sites': 64}, previous=initial)

    def test_explicit_spatial_allocation_appends_exact_nested_region_counts(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        sampling = {'kind':'stratified', 'sites':2361, 'position_seed':20261014,
                    'area_samples':65536, 'allocation':dict(zip(physics.REGIONS,[17,8,71,415,1850]))}
        initial = physics.build_design(fixed,sampling)
        # Deliberately reverse weight order: allocation is keyed by region.
        initial['stratum_weights'] = dict(reversed(list(initial['stratum_weights'].items())))
        original = json.dumps(initial,sort_keys=True)
        previous = initial
        for counts in ([68,64,256,415,1850],[136,128,512,415,1850]):
            allocation = dict(zip(physics.REGIONS,counts))
            extended = physics.build_design(fixed,{**sampling,'sites':sum(counts),'allocation':allocation},previous=previous)
            self.assertEqual(extended['stratum_counts'],allocation)
            self.assertEqual(extended['sites_mm'][:len(previous['sites_mm'])],previous['sites_mm'])
            self.assertEqual(extended['stratum'][:len(previous['stratum'])],previous['stratum'])
            self.assertEqual(extended['stratum_weights'],initial['stratum_weights'])
            self.assertEqual(extended['electrode_weights'],initial['electrode_weights'])
            self.assertEqual(len({tuple(p) for p in extended['sites_mm']}),sum(counts))
            self.assertEqual(extended,physics.build_design(fixed,{**sampling,'sites':sum(counts),'allocation':allocation},previous=previous))
            previous = extended
        self.assertEqual(json.dumps(initial,sort_keys=True),original)

    def test_explicit_spatial_allocation_validates_counts_and_existing_region_coverage(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        allocation = dict(zip(physics.REGIONS,[17,8,8,8,23]))
        sampling = {'kind':'stratified','sites':64,'position_seed':20261014,'area_samples':65536,'allocation':allocation}
        initial = physics.build_design(fixed,sampling)
        invalid = [None,{},list(allocation.values()),{k:v for k,v in allocation.items() if k!='S4_bulk'},
                   {**allocation,'surface':0},{**allocation,'S4_bulk':24},
                   {**allocation,'S4_bulk':-1}]
        invalid += [{**allocation,'S4_bulk':v} for v in (23.,23.5,True,float('nan'))]
        for requested in invalid:
            for previous in (None,initial):
                with self.subTest(allocation=requested,previous=previous is not None), self.assertRaises(ValueError):
                    physics.build_design(fixed,{**sampling,'allocation':requested},previous=previous)
        for counts in ([16,9,8,8,23],[16,24,8,8,24]):
            with self.subTest(counts=counts), self.assertRaisesRegex(ValueError,'cannot remove earlier region sites'):
                physics.build_design(fixed,{**sampling,'sites':sum(counts),'allocation':dict(zip(physics.REGIONS,counts))},previous=initial)
        with self.assertRaisesRegex(ValueError,'invalid spatial allocation'):
            physics.build_design(fixed,{**sampling,'allocation':{**allocation,'S1_le_0.05':0,'S4_bulk':31}})
        surface = physics.build_design(fixed,{'kind':'sobol','sites':16,'position_seed':20261014})
        with self.assertRaisesRegex(ValueError,'requires five physical regions'):
            physics.build_design(fixed,sampling,previous=surface)

    def test_independent_position_seeds_keep_explicit_allocation_and_area_weights(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        allocation = dict(zip(physics.REGIONS,[68,64,256,415,1850]))
        sampling = {'kind':'stratified','sites':2653,'area_samples':65536,'allocation':allocation}
        designs = [physics.build_design(fixed,{**sampling,'position_seed':seed}) for seed in (101,202,303,404)]
        for design in designs:
            self.assertEqual(design['stratum_counts'],allocation)
            self.assertEqual(design['stratum_weights'],designs[0]['stratum_weights'])
        for i,left in enumerate(designs):
            for right in designs[i+1:]:
                for region in physics.REGIONS:
                    self.assertNotEqual([p for p,h in zip(left['sites_mm'],left['stratum']) if h==region],
                                        [p for p,h in zip(right['sites_mm'],right['stratum']) if h==region])
        self.assertEqual(designs[0],physics.build_design(fixed,{**sampling,'position_seed':101}))

    def test_recorded_area_weights_preserve_fresh_design_and_hash_without_area_draws(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        sampling = {'kind':'stratified','sites':128,'position_seed':20261014,'area_samples':65536}
        for allocation in (None,dict(zip(physics.REGIONS,[34,16,24,24,30]))):
            current = {**sampling,**({'allocation':allocation} if allocation is not None else {})}
            original = physics.build_design(fixed,current)
            weights = dict(reversed(list(original['stratum_weights'].items())))
            # Zero area draws is permitted when the area fractions are supplied.
            cached = physics.build_design(fixed,{**current,'stratum_weights':weights,'area_samples':0})
            self.assertEqual(cached,original)
            self.assertEqual(records.content_hash(cached),records.content_hash(original))
            independent = physics.build_design(fixed,{**current,'stratum_weights':weights,'area_samples':0,'position_seed':20261015})
            self.assertEqual(independent['stratum_weights'],weights)
            self.assertEqual(independent['stratum_counts'],original['stratum_counts'])
            self.assertNotEqual(independent['sites_mm'],original['sites_mm'])

    def test_recorded_area_weights_reject_invalid_fractions_and_changed_junction_area(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        sampling = {'kind':'stratified','sites':64,'position_seed':20261014,'area_samples':65536}
        weights = physics.build_design(fixed,sampling)['stratum_weights']
        invalid = [None,{},list(weights.values()),{k:v for k,v in weights.items() if k!='S4_bulk'},
                   {**weights,'surface':0.1},{**weights,'S4_bulk':weights['S4_bulk']+.1}]
        invalid += [{**weights,'S4_bulk':v} for v in (0.,-.1,float('nan'),float('inf'),True,'0.5')]
        invalid.append({**weights,'S0_junction':2*weights['S0_junction'],
                        'S4_bulk':weights['S4_bulk']-weights['S0_junction']})
        for supplied in invalid:
            with self.subTest(weights=supplied), self.assertRaises(ValueError):
                physics.build_design(fixed,{**sampling,'stratum_weights':supplied})
        with self.assertRaisesRegex(ValueError,'exact junction area'):
            physics.build_design({**fixed,'electrode_width_um':20.},{**sampling,'stratum_weights':weights})

    def test_recorded_spatial_geometry_matches_source_region_and_junctions(self):
        import records
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        centers = np.column_stack((fixed['electrode_x_mm'],fixed['electrode_y_mm'])).tolist()
        design = {'sites_mm':[[-2.,-2.,.259875],[-1.98,-2.,.259875],[-1.9,-2.,.259875],
                              [-1.7,-2.,.259875],[3.8,3.8,.259875]],
                  'stratum':list(physics.REGIONS),'stratum_weights':dict(zip(physics.REGIONS,[.01,.02,.07,.2,.7])),
                  'half_span_mm':4.,'junction_half_extent_mm':[.005,.005],'electrode_centers_mm':centers}
        sampling = {'kind':'recorded-stratified','sites':5}
        original = json.dumps(design,sort_keys=True)
        result = physics.build_design(fixed,sampling,recorded={'stratified_design':design})
        self.assertEqual(result['sites_mm'],design['sites_mm'])
        self.assertEqual(result['stratum_weights'],design['stratum_weights'])
        self.assertEqual(json.dumps(design,sort_keys=True),original)
        for field,value in (('position_half_span_mm',5.),('electrode_width_um',20.),
                            ('electrode_height_um',20.),('electrode_x_mm',[x+.1 for x in fixed['electrode_x_mm']])):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'differs from study geometry'):
                physics.build_design({**fixed,field:value},sampling,recorded=design)
        for name,value in (('position_half_span_mm',5.),('junction_half_extent_mm',[.01,.005]),
                           ('junction_centers_mm',[[x+.1,y] for x,y in centers]),('half_span_mm',float('nan'))):
            with self.subTest(alias=name), self.assertRaisesRegex(ValueError,'differs from study geometry'):
                physics.build_design(fixed,sampling,recorded={'design':design,'physics':{name:value}})
        older = {key:value for key,value in design.items() if key not in
                 ('half_span_mm','junction_half_extent_mm','electrode_centers_mm')}
        self.assertEqual(physics.build_design(fixed,sampling,recorded=older)['sites_mm'],design['sites_mm'])
        with self.assertRaisesRegex(ValueError,'site regions differ'):
            physics.build_design({**fixed,'electrode_x_mm':[x+.1 for x in fixed['electrode_x_mm']]},sampling,recorded=older)
        with self.assertRaisesRegex(ValueError,'geometry must be a mapping'):
            physics.build_design(fixed,sampling,recorded={'design':design,'geometry':None})


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = FIXTURES / 'stratified'
        cls.metadata = json.loads((folder / 'fixture.json').read_text())
        path = folder / 'blocks.npz'
        if hashlib.sha256(path.read_bytes()).hexdigest() != cls.metadata['blocks_npz_sha256']:
            raise AssertionError('stratified fixture checksum differs')
        with np.load(path, allow_pickle=False) as data:
            cls.data = dict(data)

    def test_spatial_128_and_512_saved_means_and_errors(self):
        for name, expected in self.metadata['cases'].items():
            blocks, design = fixture_case(self.data, name)
            result = analysis.summarize(blocks, design)
            for key, old in (('value', 'J'), ('standard_error', 'se'), ('spatial_r95', 'spatial_R95'), ('spatial_r95_standard_error', 'spatial_R95_se')):
                with self.subTest(name=name, key=key):
                    np.testing.assert_allclose(result[key], expected[old], rtol=1e-12, atol=1e-15)
            self.assertEqual(result['n_replicas'], 8)
            self.assertEqual(result['design_hash'], design['design_hash'])

    def test_paired_saved_differences_and_identity(self):
        for n in (128, 512):
            a, design = fixture_case(self.data, f's{n}_sic')
            b, _ = fixture_case(self.data, f's{n}_baseline')
            result = analysis.paired_difference(a, b, design)
            np.testing.assert_allclose(result['value'], self.metadata['cases'][f's{n}_sic']['paired_delta_from_baseline'], rtol=1e-12, atol=1e-15)
            self.assertLess(result['confidence_interval_95'][1], 0.)
            identical = analysis.paired_difference(a, a, design, bootstrap_draws=20)
            self.assertEqual(identical['value'], 0.)
            self.assertEqual(identical['standard_error'], 0.)

    def test_incomplete_and_inconsistent_data_are_absent_observations(self):
        blocks, design = fixture_case(self.data, 's128_baseline')
        bad_cases = (blocks[:-1], blocks + blocks[:1], [dict(blocks[0], events=0)] + blocks[1:],
                     [dict(blocks[0], total_qps=blocks[0]['total_qps'] + 1)] + blocks[1:],
                     [dict(blocks[0], per_electrode_qps=None)] + blocks[1:])
        for bad in bad_cases:
            with self.assertRaises(ValueError):
                analysis.summarize(bad, design, bootstrap_draws=2, tail_bootstrap_draws=2)
        bad = dict(design, stratum_weights={'missing': 1.})
        with self.assertRaises(ValueError):
            analysis.prepare_counts(blocks, bad)

    def test_weighting_and_zero_yield(self):
        design = dict(position_strata=['near', 'bulk'], stratum_weights={'near': .25, 'bulk': .75},
                      replica_ids=[0, 1], events_per_task=100, gun_energy_eV=.01,
                      electrode_weights=[.5, .5], design_hash='synthetic')
        blocks = [dict(position=p, replica=r, events=100, total_qps=total, per_electrode_qps=q)
                  for p, total, q in ((0, 4., [1., 3.]), (1, 2., [2., 0.])) for r in (0, 1)]
        result = analysis.summarize(blocks, design, bootstrap_draws=20, tail_bootstrap_draws=20)
        self.assertEqual(result['value'], 1.25)
        self.assertEqual(result['standard_error'], 0.)
        self.assertEqual(result['naive_equal_site_mean'], 1.5)
        zero = [dict(b, total_qps=0., per_electrode_qps=[0., 0.]) for b in blocks]
        result = analysis.summarize(zero, design, bootstrap_draws=20, tail_bootstrap_draws=20)
        self.assertEqual(result['value'], 0.)
        self.assertEqual(result['maximum_site_leverage'], 0.)
        self.assertIsNone(result['relative_standard_error'])

    def test_equivalence_is_stronger_than_no_significant_difference(self):
        good = analysis.compare_repeated([100., 101., 99., 100., 100.], [100., 100., 101., 99., 100.])
        self.assertTrue(good['equivalent_within_margin'])
        broad = analysis.compare_repeated([20., 180., 100.], [180., 20., 100.])
        self.assertTrue(broad['consistent_with_zero_difference'])
        self.assertFalse(broad['equivalent_within_margin'])
        small = analysis.compare_repeated([170., 174., 164., 204.], [170., 164., 164., 204.], paired=True)
        self.assertEqual(small['degrees_of_freedom'], 3)
        self.assertTrue(small['consistent_with_zero_difference'])
        self.assertFalse(small['equivalent_within_margin'])
        self.assertGreater(small['confidence_interval'][1], 1.96 * small['standard_error'] + small['difference'])

    def test_historical_objective_units_and_within_site_error(self):
        design = dict(position_strata=['surface', 'surface'], stratum_weights={'surface': 1.},
                      replica_ids=[0, 1], events_per_task=100, gun_energy_eV=.01,
                      electrode_weights=[.5, .5], design_hash='historical')
        blocks = [dict(position=p, replica=r, events=100, total_qps=sum(q), per_electrode_qps=q)
                  for p, r, q in ((0, 0, [1., 3.]), (0, 1, [3., 3.]),
                                  (1, 0, [2., 0.]), (1, 1, [4., 0.]))]
        result = analysis.summarize(blocks, design, objective='total_qps_per_primary')
        self.assertEqual(result['value'], .04)
        self.assertAlmostEqual(result['standard_error'], math.sqrt(8.) / 400.)
        self.assertEqual(result['total_qps'], 16.)
        self.assertAlmostEqual(result['max_electrode_qps_per_primary'], .025)
        self.assertAlmostEqual(result['p90_position'], .048)
        self.assertAlmostEqual(result['robust_mean_plus_se'], .04 + np.std([.04, .06, .02, .04], ddof=1) / 2.)
        zero = [dict(row, total_qps=0., per_electrode_qps=[0., 0.]) for row in blocks]
        paired = analysis.paired_difference(blocks, zero, design, objective='total_qps_per_primary')
        self.assertAlmostEqual(paired['value'], result['value'])
        self.assertAlmostEqual(paired['standard_error'], result['standard_error'])
        self.assertEqual(paired['units'], 'summed_junction_QPs_per_primary')

    def test_realization_comparison_uses_area_weights_and_selected_response(self):
        design = dict(position_strata=['near', 'bulk'], stratum_weights={'near': .01, 'bulk': .99},
                      replica_ids=[0, 1], events_per_task=100, gun_energy_eV=.01,
                      electrode_weights=[1.], design_hash='weighted-realizations')
        blocks = [dict(position=p, replica=r, events=100, total_qps=q, per_electrode_qps=[q])
                  for p, r, q in ((0, 0, 100.), (0, 1, 200.), (1, 0, 1.), (1, 1, 2.))]
        np.testing.assert_allclose(analysis.summarize(blocks, design, diagnostics=False, uncertainty=False)['by_replica'], [1.99, 3.98])
        np.testing.assert_allclose(analysis.summarize(blocks, design, objective='total_qps_per_primary', diagnostics=False, uncertainty=False)['by_replica'], [.505, 1.01])

    def test_measured_curve_distance_preserves_nearest_time_ties(self):
        args = ([0., 2., 4.], [1., 3., 4.], [1., 3.], [.5, 1.])
        self.assertEqual(analysis.curve_distance(*args), 4.25)
        self.assertEqual(analysis.curve_distance(*args, normalize='y'), 4.5)
        self.assertEqual(analysis.curve_distance(*args, dy_data=[.5, 2.], normalize='dy2'), 2.)

    def test_projection_separates_feature_coverage(self):
        candidates = [dict(id='partial', features={'speed': 2.}), dict(id='complete', features={'speed': 3., 'scat': .1})]
        result = analysis.project_materials({'speed': 2., 'scat': .1}, candidates, {'speed': 'linear', 'scat': 'log'})
        self.assertEqual(result['rows'][0]['id'], 'complete')
        self.assertEqual(result['rows'][1]['stratum'], '1/2')
        self.assertEqual(result['missing_features']['scat'], 1)
        self.assertEqual(analysis.project_materials({'speed': 2.}, [{'id': 'missing', 'features': {'speed': float('nan')}}], {'speed': 'linear'})['rows'], [])

    def test_sensitivity_and_tolerance_do_not_invent_information(self):
        X = np.column_stack((np.arange(10.), np.ones(10)))
        result = analysis.sensitivity(X, np.arange(10.), names=['linear', 'fixed'])
        self.assertAlmostEqual(result['correlation'][0], 1.)
        self.assertEqual(result['constant_parameters'], ['fixed'])
        tolerance = analysis.tolerance_summary({'value': 1., 'standard_error': .01},
                      {'x:minus': {'value': .99}, 'x:plus': {'value': 1.01}}, coordinates=[.5], names=['x'])
        self.assertFalse(tolerance['locally_converged'])
        self.assertFalse(tolerance['complete_paired_tests'])
        absent = analysis.tolerance_summary({'value': 1., 'standard_error': None},
                     {'x:minus': {'value': .99}, 'x:plus': {'value': 1.01}}, coordinates=[.5], names=['x'])
        self.assertFalse(absent['locally_converged'])
        self.assertIsNone(absent['variables']['x']['resolved'])

    def test_morris_reanalysis_uses_recorded_inputs_and_complete_trajectories(self):
        import scan
        study = scan.load_study({'schema': 'material-scan-study-2', 'id': 'recorded-morris',
                                   'kind': 'sensitivity', 'analysis': {'objective': 'integrated_density'}})
        problem = {'num_vars': 1, 'names': ['r'], 'bounds': [[0., 1.]]}
        study['morris_design'] = {'problem': problem, 'seed': 7, 'levels': 4}
        rows = [dict(name=f'morris_{i}', status='complete', inputs={'r': value}, trajectory=i // 2,
                     design_index=i, value=1. + value, integrated_density=1. + value)
                for i, value in enumerate((0., 2. / 3., 1., 1. / 3.))]
        with patch.object(scan, 'candidate_table', side_effect=AssertionError('saved inputs were regenerated')):
            result = analysis.analyse_results(study, rows)['sensitivity']
            np.testing.assert_allclose(result['mu'], [1.], rtol=1e-12)
            self.assertEqual(result['morris_design']['problem'], problem)
            rows[-1]['status'] = 'incomplete'
            result = analysis.analyse_results(study, rows)['sensitivity']
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(result['missing_trajectories'], [1])

    def test_spatial_convergence_requires_every_region(self):
        a = {'value': 1., 'standard_error': .02, 'means_by_stratum': {'near': 1., 'bulk': 1.}, 'maximum_site_leverage': .01}
        b = {**a, 'value': 1.01, 'means_by_stratum': {'near': 1.2, 'bulk': .99}}
        result = analysis.spatial_convergence(a, b)
        self.assertFalse(result['passed'])
        self.assertEqual(result['refine_regions'], ['near'])

    def test_pareto_front_retains_unresolved_film_tradeoff(self):
        rows = [{'id': 'low_gap', 'qp': 1.16e-4, 'loss': float('inf')},
                {'id': 'niobium', 'qp': 3.84e-4, 'loss': 1.},
                {'id': 'dominated', 'qp': 4e-4, 'loss': float('inf')}]
        self.assertEqual({r['id'] for r in analysis.pareto_front(rows, ['qp', 'loss'])}, {'low_gap', 'niobium'})


class SearchTests(unittest.TestCase):
    def test_gp_likelihood_gradient_including_spherical_coordinates(self):
        from search import GaussianProcess
        rng = np.random.default_rng(7341)
        for sphere in (None, (0, 1)):
            gp = GaussianProcess(sphere_columns=sphere)
            gp.X = rng.uniform(.1, .9, (9, 3)); gp.y = rng.normal(size=9); gp.noise = rng.uniform(.001, .04, 9)
            theta = np.array([-.3, -.7, -.5, -.2, -3.])
            value, gradient = gp._nll(theta)
            numerical = np.empty_like(theta)
            for i in range(len(theta)):
                step = np.zeros_like(theta); step[i] = 1e-5
                numerical[i] = (gp._nll(theta + step)[0] - gp._nll(theta - step)[0]) / 2e-5
            self.assertTrue(math.isfinite(value))
            np.testing.assert_allclose(gradient, numerical, rtol=2e-5, atol=2e-6)

    def test_gp_sphere_seams_poles_and_heteroscedastic_noise(self):
        from search import GaussianProcess, expected_improvement
        gp = GaussianProcess(sphere_columns=(0, 1))
        X = np.array([[.5, 0.], [.5, 1.], [1., .1], [1., .8]])
        matrix, _ = gp._matern(X, X, np.ones(2))
        self.assertAlmostEqual(matrix[0, 1], 1., places=14)
        self.assertEqual(matrix[2, 3], 1.)
        gp = GaussianProcess()
        gp.fit([[.1], [.5], [.9]], [0., 1., 0.], noise_var=[.01, .2, .04], n_restarts=1)
        np.testing.assert_allclose(gp.noise, np.array([.01, .2, .04]) / gp.y_std ** 2, rtol=1e-14)
        mu, sigma = gp.predict([[.2], [.6]])
        self.assertTrue(np.isfinite(mu).all()); self.assertTrue((sigma > 0).all())
        np.testing.assert_allclose(expected_improvement(np.array([1.]), np.array([1.]), 1.), [1 / math.sqrt(2 * math.pi)])

    def test_gp_posterior_and_improvement_against_independent_solve(self):
        from scipy.integrate import quad
        from search import GaussianProcess, expected_improvement
        rng = np.random.default_rng(19)
        X = rng.random((15, 3)); y = np.sin(3 * X[:, 0]) + X[:, 1] ** 2
        gp = GaussianProcess().fit(X, y, noise_var=np.full(len(y), .001), n_restarts=1)
        query = rng.random((5, 3)); mean, std = gp.predict(query)
        sf2, ell, _ = gp._unpack(gp.theta)
        K = gp._build(gp.theta)[0]
        cross = sf2 * gp._matern(query, X, ell)[0]
        expected_mean = cross @ np.linalg.solve(K, gp.y) * gp.y_std + gp.y_mean
        expected_var = sf2 - np.einsum('ij,ji->i', cross, np.linalg.solve(K, cross.T))
        np.testing.assert_allclose(mean, expected_mean, rtol=1e-10, atol=1e-10)
        np.testing.assert_allclose(std, np.sqrt(np.maximum(expected_var, 1e-12)) * gp.y_std, rtol=1e-8, atol=1e-9)
        integrated = quad(lambda value: max(.5 - value, 0) * math.exp(-.5 * ((value - .3) / .4) ** 2) / (.4 * math.sqrt(2 * math.pi)), -10, 10)[0]
        self.assertAlmostEqual(float(expected_improvement(np.array([.3]), np.array([.4]), .5)[0]), integrated, places=9)

    def test_search_restart_and_rejection_preserve_random_streams(self):
        from physics import search_space
        import records
        from search import load_state, new_state, propose, save_state, update
        space = search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                             table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        for method in ('random', 'sobol', 'cmaes'):
            with self.subTest(method=method):
                state = new_state(method, seed=71, options={'popsize': 4, 'reeval_every': 0})
                points = propose(method, state, space, 4)
                update(method, state, points[0], None, rejected_reason='timeout')
                self.assertEqual(len(state['Y']), 0)
                for i, point in enumerate(points[1:]):
                    update(method, state, point, float(i + 1), .01)
                restored = load_state(save_state(state))
                self.assertEqual(propose(method, state, space, 2), propose(method, restored, space, 2))

    def test_cma_waits_for_declared_generation(self):
        from physics import search_space
        import records
        from search import new_state, propose, update
        space = search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                             table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = new_state('cmaes', seed=71, options={'popsize': 4, 'reeval_every': 0})
        points = propose('cmaes', state, space, 4)
        self.assertEqual(propose('cmaes', state, space, 1), [])
        for i, point in enumerate(points[:-1]):
            update('cmaes', state, point, i + 1., .01)
        self.assertEqual(state['_cma'].generation, 0)
        update('cmaes', state, points[-1], 4., .01)
        self.assertEqual(state['_cma'].generation, 1)
        self.assertEqual(len(propose('cmaes', state, space, 1)), 1)

    def test_fitted_gp_arrays_restore_without_refitting(self):
        from search import GaussianProcess, load_state, new_state, save_state
        gp = GaussianProcess().fit([[.1], [.5], [.9]], [0., 1., .2], n_restarts=1)
        state = new_state('bo_gp', seed=7); state['_gp'] = gp
        with patch.object(GaussianProcess, 'fit', side_effect=AssertionError('restoration refitted GP')):
            restored = load_state(save_state(state))
        for x, y in zip(gp.predict([[.2], [.8]]), restored['_gp'].predict([[.2], [.8]])):
            np.testing.assert_array_equal(x, y)

    def test_common_measurements_initialize_cma_at_measured_best_with_transport_noise(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        starts = [{'values': physics.from_unit([u], space), 'value': value,
                   'standard_error': 2., 'transport_standard_error': .02, 'elapsed_s': 3.}
                  for u, value in ((.2, 3.), (.4, 1.), (.8, 2.))]
        state = search.import_observations(search.new_state('cmaes', options={'popsize': 4}), space, starts)
        np.testing.assert_allclose(state['SIG'], [.02 / 3, .02, .01])
        self.assertEqual(state['initial_count'], 3)
        self.assertEqual(state['pending'], [])
        search.propose('cmaes', state, space, 1)
        np.testing.assert_allclose(state['_cma'].mean, [.4])
        self.assertEqual(state['_cma'].generation, 0)
        self.assertEqual(len(state['_cma'].points), 1)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            search.import_observations(search.new_state('bo_gp'), space, [starts[0], starts[0]])
        bad = dict(starts[0], values={**starts[0]['values'], 'sub_c44': 1.})
        with self.assertRaisesRegex(ValueError, 'fixed property'):
            search.import_observations(search.new_state('bo_gp'), space, [bad])

    def test_runtime_failure_is_absent_response_and_timeout_is_lower_bound(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('random', seed=14)
        points = search.propose('random', state, space, 2)
        search.update('random', state, points[0], 2., .01, elapsed_s=10., status='complete')
        search.update('random', state, points[1], None, rejected_reason='task timeout', elapsed_s=1000., status='incomplete')
        self.assertEqual(state['values'], [2.])
        self.assertEqual(state['attempts'][1]['status'], 'timeout')
        U = np.array([physics.to_unit(p, space) for p in points])
        weights = search._runtime_weights(state, U)
        self.assertLess(weights[1], weights[0])
        self.assertLessEqual(weights[1], 1. / 1000.)
        restored = search.load_state(search.save_state(state))
        np.testing.assert_array_equal(weights, search._runtime_weights(restored, U))

    def test_hybrid_gp_retains_equal_ordinary_pool_and_eight_additional_points(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        starts = [{'values': physics.from_unit([u], space), 'value': 1. + u * u,
                   'transport_standard_error': .01} for u in (.2, .5, .8)]
        options = {'n_candidates': 32, 'extra_candidates': 8, 'n_restarts': 1,
                   'n_polish': 1, 'pool_seed': 59}
        states = [search.import_observations(search.new_state(method, seed=9, options=options), space, starts)
                  for method in ('bo_gp', 'llm_bo')]
        supplied = [physics.from_unit([u], space) for u in (.03, .07, .13, .27, .37, .63, .73, .93)]
        pools = []
        original = search.GaussianProcess.predict
        def observed_pool(gp, X, *args, **kwargs):
            if len(np.atleast_2d(X)) == 40:
                pools.append(np.asarray(X).copy())
            return original(gp, X, *args, **kwargs)
        with patch.object(search.GaussianProcess, 'predict', observed_pool), \
             patch.object(search, 'ollama_proposals', return_value=supplied) as llm:
            for state in states:
                result = search.propose(state['method'], state, space)
                self.assertEqual(len(result), 1)
                self.assertEqual(state['last_pool']['total'], 40)
                self.assertEqual(state['last_pool']['ordinary'], 32)
            llm.assert_called_once()
        self.assertEqual(states[1]['last_pool']['llm'], 8)
        np.testing.assert_array_equal(pools[0][:32], pools[1][:32])
        self.assertEqual(len(np.unique(pools[1], axis=0)), 40)
        for point in supplied:
            self.assertTrue(np.any(np.all(pools[1] == physics.to_unit(point, space), axis=1)))

    def test_ollama_tools_are_bounded_and_final_schema_contains_no_measured_response(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('llm_bo', options={'model': 'test:1', 'model_digest': 'abc',
                    'agent_tools': True, 'source_records': {'fixed-physics': {'unit': 'km/s'}}})
        point = physics.from_unit([.4], space)
        replies = [{'models': [{'name': 'test:1', 'digest': 'abc'}]},
                   {'message': {'tool_calls': [{'function': {'name': 'read_source',
                      'arguments': {'source_id': 'fixed-physics'}}}]}},
                   {'message': {'tool_calls': [{'function': {'name': 'check_physics',
                      'arguments': {'values': {'topfilm_vsound': point['topfilm_vsound']}}}}]}},
                   {'done_reason': 'stop', 'eval_count': 234, 'message': {'thinking': 'discarded', 'content': json.dumps({'candidates': [{
                       'values': {'topfilm_vsound': point['topfilm_vsound']},
                       'rationale': 'Test the permitted film speed.', 'source_ids': ['fixed-physics']}]})}}]
        payloads = []
        def respond(request, **kwargs):
            if not isinstance(request, str):
                payloads.append(json.loads(request.data))
            return io.BytesIO(json.dumps(replies.pop(0)).encode())
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = respond
            out = search.ollama_proposals(state, space, 1)
        self.assertEqual(out, [point])
        self.assertEqual(state['llm_stats']['tools'], 2)
        self.assertEqual(state['llm_stats']['repairs'], 0)
        self.assertEqual(state['values'], [])
        self.assertIn('format', payloads[-1])
        self.assertNotIn('tools', payloads[-1])
        self.assertIn('weighted junction QPs/injected-eV', payloads[0]['messages'][1]['content'])
        prompt = json.loads(payloads[0]['messages'][1]['content'])
        self.assertEqual(prompt['direction'], 'minimize')
        self.assertEqual(prompt['tool_limit'], 2)
        self.assertIn('minimize J', prompt['request'])
        self.assertIn('Smaller J is better', prompt['request'])
        self.assertIn('Minimize the measured response J', payloads[0]['messages'][0]['content'])
        self.assertIn('at most 2 tool calls in total', payloads[0]['messages'][0]['content'])
        self.assertEqual(state['llm_stats']['tool_requests'], 2)
        self.assertEqual([row['requested_tool_count'] for row in state['llm_records']], [1, 1, 0])
        self.assertNotIn('J', payloads[-1]['format']['properties']['candidates']['items']['properties'])
        self.assertNotIn('thinking', state['llm_records'][-1])
        self.assertEqual(state['llm_records'][-1]['thinking_characters'], len('discarded'))
        self.assertEqual(state['llm_records'][-1]['done_reason'], 'stop')
        self.assertEqual(state['llm_records'][-1]['eval_count'], 234)
        self.assertEqual(state['llm_records'][-1]['num_predict'], 4096)
        self.assertEqual(state['llm_records'][-1]['num_ctx'], 16384)
        self.assertTrue(payloads[0]['think'])
        self.assertTrue(payloads[1]['think'])
        self.assertFalse(payloads[-1]['think'])
        self.assertEqual(state['llm_records'][-1]['phase'], 'final')
        self.assertFalse(state['llm_records'][-1]['think'])

    def test_thinking_exhaustion_after_tools_gets_one_schema_only_repair(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('llm_bo', options={'model': 'test:1', 'agent_tools': True,
                    'think': True, 'final_think': False, 'num_predict': 8192,
                    'source_records': {'fixed-physics': {'unit': 'km/s'}}})
        point = physics.from_unit([.4], space)
        candidate = {'values': {'topfilm_vsound': point['topfilm_vsound']},
                     'rationale': 'Test the permitted film speed.', 'source_ids': ['fixed-physics']}
        replies = [{'models': [{'name': 'test:1', 'digest': 'abc'}]},
                   {'message': {'tool_calls': [
                       {'function': {'name': 'read_source', 'arguments': {'source_id': 'fixed-physics'}}},
                       {'function': {'name': 'summarize_measurements', 'arguments': {}}}]}},
                   {'done_reason': 'length', 'eval_count': 8192,
                    'message': {'thinking': 'Long reasoning without a final answer.', 'content': ''}},
                   {'done_reason': 'stop', 'message': {'content': json.dumps({'candidates': [candidate]})}}]
        payloads = []
        def respond(request, **kwargs):
            if not isinstance(request, str): payloads.append(json.loads(request.data))
            return io.BytesIO(json.dumps(replies.pop(0)).encode())
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = respond
            self.assertEqual(search.ollama_proposals(state, space, 1), [point])
        self.assertEqual(replies, [])
        self.assertEqual(state['llm_stats']['tools'], 2)
        self.assertEqual(state['llm_stats']['calls'], 3)
        self.assertEqual(state['llm_stats']['repairs'], 1)
        self.assertEqual(state['llm_stats']['accepted'], 1)
        self.assertTrue(payloads[0]['think'])
        for payload in payloads[1:]:
            self.assertFalse(payload['think'])
            self.assertIn('format', payload)
            self.assertNotIn('tools', payload)
        truncated = state['llm_records'][1]
        self.assertEqual(truncated['done_reason'], 'length')
        self.assertEqual(truncated['phase'], 'final')
        self.assertTrue(truncated['output_error'])
        self.assertNotIn('thinking', truncated)
        self.assertEqual(state['values'], [])

    def test_repeated_empty_final_output_has_precise_bounded_failure_records(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('llm_bo', options={'model': 'test:1', 'final_think': False})
        exhausted = {'done_reason': 'length', 'eval_count': 4096,
                     'message': {'thinking': 'Unfinished calculation.', 'content': ''}}
        replies = [{'models': [{'name': 'test:1', 'digest': 'abc'}]}, exhausted, exhausted]
        payloads = []
        def respond(request, **kwargs):
            if not isinstance(request, str): payloads.append(json.loads(request.data))
            return io.BytesIO(json.dumps(replies.pop(0)).encode())
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = respond
            self.assertEqual(search.ollama_proposals(state, space, 1), [])
        self.assertEqual(len(payloads), 2)
        self.assertTrue(all(payload['think'] is False for payload in payloads))
        self.assertEqual(state['llm_stats']['repairs'], 1)
        self.assertEqual(state['llm_stats']['accepted'], 0)
        self.assertTrue(all(row['output_error'] for row in state['llm_records']))
        self.assertTrue(any('length' in row['output_error'].lower()
                            or 'limit' in row['output_error'].lower() for row in state['llm_records']))
        self.assertEqual(state['pending'], [])
        self.assertEqual(state['values'], [])

    def test_ai_settings_remain_fixed_across_a_saved_comparison(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('llm_bo', options={'model': 'test:1', 'model_digest': 'abc',
                    'final_think': False, 'source_records': {'physics': {'units': 'km/s'}}})
        candidate = {'values': {'topfilm_vsound': physics.from_unit([.4], space)['topfilm_vsound']},
                     'rationale': 'Test the permitted film speed.', 'source_ids': ['physics']}
        replies = [{'models': [{'name': 'test:1', 'digest': 'abc'}]},
                   {'message': {'content': json.dumps({'candidates': [candidate]})}}]
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(replies.pop(0)).encode())
            self.assertEqual(len(search.ollama_proposals(state, space, 1)), 1)
        frozen = search.save_state(state)
        for field, value in [('final_think', True), ('temperature', .01),
                             ('source_records', {'physics': {'units': 'm/s'}}),
                             ('model_digest', 'other-weights')]:
            restored = search.load_state(frozen)
            restored['options'][field] = value
            with self.subTest(field=field), patch('urllib.request.build_opener') as build:
                with self.assertRaisesRegex(ValueError, 'AI settings changed'):
                    search.ollama_proposals(restored, space, 1)
                with self.assertRaisesRegex(ValueError, 'AI settings changed'):
                    search.propose('llm_bo', restored, space, 1)
                build.assert_not_called()
            self.assertEqual(restored['llm_settings_sha256'], state['llm_settings_sha256'])

    def test_ollama_repairs_unknown_sources_once_without_accepting_fabricated_response(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('llm_bo', options={'model': 'test:1', 'host': 'http://ollama.example:11435'})
        item = {'values': {'topfilm_vsound': physics.from_unit([.4], space)['topfilm_vsound']},
                'rationale': 'Numerical hypothesis.', 'source_ids': []}
        replies = [{'models': [{'name': 'test:1'}]},
                   {'message': {'content': json.dumps({'candidates': [{**item, 'J': 0.}]})}},
                   {'message': {'content': json.dumps({'candidates': [{**item, 'source_ids': ['invented']}]})}}]
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(replies.pop(0)).encode())
            out = search.ollama_proposals(state, space, 1)
            build.assert_called_once_with()
        self.assertEqual(out, [])
        self.assertEqual(state['llm_stats']['repairs'], 1)
        self.assertEqual(state['llm_stats']['calls'], 2)
        self.assertEqual(state['values'], [])
        self.assertTrue(state['llm_rejected'])

    def test_excess_tools_are_recorded_and_never_executed(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('llm_bo', options={'model': 'test:1', 'agent_tools': True})
        excess = {'message': {'tool_calls': [{'function': {'name': 'summarize_measurements', 'arguments': {}}}] * 3}}
        replies = [{'models': [{'name': 'test:1'}]}, excess, excess]
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(replies.pop(0)).encode())
            self.assertEqual(search.ollama_proposals(state, space, 1), [])
        self.assertEqual(state['llm_stats']['calls'], 2)
        self.assertEqual(state['llm_stats']['tool_requests'], 6)
        self.assertEqual(state['llm_stats']['tools'], 0)
        self.assertEqual([row['requested_tool_count'] for row in state['llm_records']], [3, 3])
        self.assertFalse(any('tool_results' in row for row in state['llm_records']))
        self.assertEqual(state['llm_stats']['repairs'], 1)

    def test_loopback_ollama_bypasses_bad_proxy_and_outages_stop_before_transport(self):
        import http.server
        import os
        import threading
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        item = {'values': {'topfilm_vsound': physics.from_unit([.4], space)['topfilm_vsound']},
                'rationale': 'Numerical hypothesis.', 'source_ids': []}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.routes.append(('GET', self.path))
                self.send_response(503 if self.server.failure == 'tags' else 200)
                self.end_headers()
                model = {'name': 'test:1', 'digest': 'abc'}
                if self.path == '/api/ps':
                    model.update(size=1000, size_vram=0 if self.server.failure == 'cpu_fallback' else 1000)
                    if self.server.failure == 'gpu_digest':
                        model['digest'] = 'changed'
                self.wfile.write(json.dumps({'models': [model]}).encode())

            def do_POST(self):
                self.server.routes.append(('POST', self.path))
                self.server.payloads.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(503 if self.server.failure == 'chat' else 200)
                self.end_headers()
                reply = {'error': 'model unavailable'} if self.server.failure == 'inference' else {
                    'message': {'content': json.dumps({'candidates': [item]})}, 'done': True}
                self.wfile.write(json.dumps(reply).encode())

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.failure, server.routes, server.payloads = None, [], []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bad_proxy = {'http_proxy': 'http://127.0.0.1:1', 'HTTP_PROXY': 'http://127.0.0.1:1',
                     'https_proxy': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1',
                     'no_proxy': '', 'NO_PROXY': ''}
        try:
            with patch.dict(os.environ, bad_proxy):
                for hostname in ('127.0.0.1', 'localhost'):
                    state = search.new_state('llm_bo', options={'model': 'test:1', 'model_digest': 'abc',
                          'host': f'http://{hostname}:{server.server_port}', 'require_gpu': True})
                    self.assertEqual(search.ollama_proposals(state, space, 1)[0]['topfilm_vsound'], item['values']['topfilm_vsound'])
                    self.assertEqual(state['llm_route']['mode'], 'direct_loopback')
                    self.assertEqual(state['llm_stats']['calls'], 1)
                    self.assertEqual(json.loads(server.payloads[-1]['messages'][1]['content'])['tool_limit'], 0)
                    self.assertEqual(state['llm_records'][-1]['gpu_residency']['size_vram'], 1000)
                self.assertEqual(server.routes, [('GET', '/api/tags'), ('POST', '/api/chat'), ('GET', '/api/ps')] * 2)
                for failure in ('tags', 'chat', 'inference', 'missing_model', 'digest', 'cpu_fallback', 'gpu_digest'):
                    with self.subTest(failure=failure):
                        server.failure = failure
                        state = search.new_state('llm_bo', options={'model': 'test:1', 'model_digest': 'abc',
                              'host': f'http://127.0.0.1:{server.server_port}', 'require_gpu': True})
                        starts = [{'values': physics.from_unit([.2], space), 'value': 1., 'transport_standard_error': .01}]
                        search.import_observations(state, space, starts)
                        if failure == 'missing_model':
                            state['options']['model'] = 'absent:1'
                        if failure == 'digest':
                            state['options']['model_digest'] = 'changed'
                        with patch.object(search, '_gp_points', side_effect=AssertionError('unavailable model fell back to GP')):
                            with self.assertRaisesRegex(RuntimeError, 'Ollama'):
                                search.propose('llm_bo', state, space)
                        self.assertEqual(state['llm_stats']['calls'], int(failure in ('cpu_fallback', 'gpu_digest')))
                        self.assertEqual(state['llm_stats']['accepted'], 0)
                        self.assertEqual(state['llm_stats']['request_failures'], int(failure in ('chat', 'inference', 'cpu_fallback', 'gpu_digest')))
                        self.assertEqual(state['pending'], [])
                        self.assertEqual(state['iteration'], 0)
                        self.assertIn('unavailable', state)
                        restored = search.load_state(search.save_state(state))
                        self.assertEqual(restored['unavailable'], state['unavailable'])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_agent_requires_a_permitted_check_before_accepting_proposals(self):
        import io
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        item = {'values': {'topfilm_vsound': physics.from_unit([.4], space)['topfilm_vsound']},
                'rationale': 'Numerical hypothesis.', 'source_ids': []}
        final = {'message': {'content': json.dumps({'candidates': [item]})}}
        for attempts in ([final, final], [{'message': {'tool_calls': [
                {'function': {'name': 'invented', 'arguments': {}}},
                {'function': {'name': 'read_source', 'arguments': {'source_id': 'absent'}}}]}}, final, final]):
            state = search.new_state('llm_bo', options={'model': 'test:1', 'agent_tools': True})
            replies = [{'models': [{'name': 'test:1'}]}] + attempts
            with patch('urllib.request.build_opener') as build:
                build.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(replies.pop(0)).encode())
                self.assertEqual(search.ollama_proposals(state, space, 1), [])
            self.assertEqual(state['llm_stats']['accepted'], 0)
            self.assertEqual(state['llm_stats']['tools'], 0)
            self.assertEqual(state['llm_stats']['calls'], len(attempts))
            self.assertEqual(state['llm_stats']['repairs'], 1)
            self.assertTrue(state['llm_rejected'])
        state = search.import_observations(search.new_state('llm_bo'), space,
                  [{'values': physics.from_unit([.2], space), 'value': 1., 'transport_standard_error': .01}])
        with patch.object(search, 'ollama_proposals', return_value=[]), \
             patch.object(search, '_gp_points', side_effect=AssertionError('no valid LLM inputs fell back to GP')):
            with self.assertRaisesRegex(RuntimeError, 'no valid physical proposals'):
                search.propose('llm_bo', state, space)
        self.assertEqual(state['pending'], [])
        self.assertEqual(state['iteration'], 0)

    def test_old_ai_state_cannot_resume_as_corrected_ai(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        for method in ('llm_bo', 'llm_agent'):
            for version in (None, 2, 3):
                state = search.new_state(method)
                if version is None:
                    state.pop('llm_protocol_version')
                else:
                    state['llm_protocol_version'] = version
                with self.assertRaisesRegex(ValueError, 'different final-output settings'):
                    search.propose(method, state, space)
        for method in ('bo_gp', 'cmaes'):
            state = search.new_state(method)
            state.pop('llm_protocol_version')
            self.assertEqual(len(search.propose(method, state, space)), 1)

    def test_local_width_adapts_only_after_measured_results_and_old_gp_cannot_resume(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        start = [{'values': physics.from_unit([.4], space), 'value': 1., 'transport_standard_error': .01}]
        state = search.import_observations(search.new_state('random', options={'local_adapt': True}), space, start)
        for point in search.propose('random', state, space, 4):
            search.update('random', state, point, 2., .01)
        self.assertEqual(state['local_width'], .04)
        for value, point in zip((.9, .8, .7), search.propose('random', state, space, 3)):
            search.update('random', state, point, value, .01)
        self.assertEqual(state['local_width'], .08)
        old = search.new_state('bo_gp'); old.pop('algorithm_version')
        with self.assertRaisesRegex(ValueError, 'different proposal pool'):
            search.propose('bo_gp', old, space)

    def test_minimum_input_distance_filters_observed_and_pending_vectors(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'},
                                     'bot_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.new_state('random', seed=8, options={'proposal_min_distance': .02})
        initial = [{'values': physics.from_unit([.4, .6], space), 'value': 1.,
                    'transport_standard_error': .01}]
        search.import_observations(state, space, initial)
        self.assertTrue(search._duplicate(physics.from_unit([.419, .619], space), state, space))
        self.assertFalse(search._duplicate(physics.from_unit([.421, .6], space), state, space))
        pending = search.propose('random', state, space, 1)[0]
        self.assertTrue(search._duplicate(pending, state, space))
        restored = search.load_state(search.save_state(state))
        self.assertTrue(search._duplicate(pending, restored, space))
        separated = search.propose('random', state, space, 12)
        points = np.array([physics.to_unit(row, space) for row in
                           [initial[0]['values'], pending, *separated]])
        distance = np.max(np.abs(points[:, None] - points[None]), axis=2)
        np.fill_diagonal(distance, np.inf)
        self.assertGreaterEqual(distance.min(), .02)

    def test_local_adaptation_requires_improvement_larger_than_transport_error(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.import_observations(search.new_state('random', seed=31,
                    options={'local_adapt': True, 'local_success_z': 1.96}), space,
                    [{'values': physics.from_unit([.4], space), 'value': 1.,
                      'transport_standard_error': .05}])
        points = search.propose('random', state, space, 4)
        for point, value in zip(points, [.99, .98, .97, .96]):
            search.update('random', state, point, value, .05)
        self.assertEqual(state['local_width'], .04)
        for point, value in zip(search.propose('random', state, space, 3), [.7, .4, .1]):
            search.update('random', state, point, value, .005)
        self.assertEqual(state['local_width'], .08)
        before = search.save_state(state)
        point = search.propose('random', state, space, 1)[0]
        search.update('random', state, point, None, rejected_reason='unmeasured timeout')
        self.assertEqual(state['local_width'], before['state']['local_width'])
        self.assertEqual(state['local_successes'], before['state']['local_successes'])
        self.assertEqual(state['local_failures'], before['state']['local_failures'])

    def test_local_stagnation_restores_width_and_records_global_search_steps(self):
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.import_observations(search.new_state('random', seed=31,
                    options={'local_adapt': True, 'local_success_z': 1.96,
                             'local_restart_after': 2, 'local_global_steps': 4}), space,
                    [{'values': physics.from_unit([.4], space), 'value': 1.,
                      'transport_standard_error': .05}])
        for point in search.propose('random', state, space, 8):
            search.update('random', state, point, 1., .05)
        self.assertEqual(state['local_width'], .08)
        self.assertEqual(state['local_global_remaining'], 4)
        restored = search.load_state(search.save_state(state))
        self.assertEqual(restored['local_global_remaining'], 4)
        self.assertEqual(restored['local_width'], .08)

    def test_restarted_gp_uses_global_sobol_pool_and_resumes_remaining_steps(self):
        from scipy.stats import qmc
        import records
        import search
        space = physics.search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        state = search.import_observations(search.new_state('bo_gp', seed=31,
                    options={'n_init': 3, 'n_candidates': 16, 'extra_candidates': 4,
                             'n_restarts': 1, 'n_polish': 1, 'local_restart_after': 2}), space,
                    [{'values': physics.from_unit([u], space), 'value': 1.+u,
                      'transport_standard_error': .01} for u in [.2, .5, .8]])
        state['local_global_remaining'] = 2
        pools = []
        original = search.GaussianProcess.predict
        def observe(gp, U, *args, **kwargs):
            if len(np.atleast_2d(U)) == 20: pools.append(np.asarray(U).copy())
            return original(gp, U, *args, **kwargs)
        with patch.object(search.GaussianProcess, 'predict', observe):
            point = search.propose('bo_gp', state, space, 1)[0]
        self.assertEqual(len(pools), 1)
        seed = state['last_pool']['seed']
        expected = qmc.Sobol(d=1, scramble=True, seed=seed).random_base2(4)
        np.testing.assert_array_equal(pools[0][:16], expected)
        self.assertTrue(state['last_pool']['global_only'])
        self.assertEqual(state['local_global_remaining'], 1)
        search.update('bo_gp', state, point, 2., .01)
        restored = search.load_state(search.save_state(state))
        self.assertEqual(search.propose('bo_gp', state, space, 1),
                         search.propose('bo_gp', restored, space, 1))
        self.assertEqual(state['local_global_remaining'], 0)
        self.assertTrue(state['last_pool']['global_only'])

    def test_corner_search_omits_unstable_inputs_and_resumes_coordinate_refinement(self):
        import records
        import search
        space = physics.search_space({'sub_c11': {'mode': 'search', 'bounds': [20., 450.]},
                                     'sub_c12': {'mode': 'search', 'bounds': [0., 250.]}}, kind='fixed',
                    table=records.load_document(ROOT / 'parameters.yaml')['parameters'])
        initial_point = physics.from_unit([.5, .2], space)
        state = search.import_observations(search.new_state('corners', seed=31), space,
                    [{'values': initial_point, 'value': 1., 'transport_standard_error': .01}])
        corners = search.propose('corners', state, space, 3)
        self.assertEqual(len(corners), 3)
        units = np.array([physics.to_unit(point, space) for point in corners])
        self.assertTrue(np.isin(units, [0., 1.]).all())
        self.assertEqual(len(np.unique(units, axis=0)), 3)
        self.assertTrue(all(point['sub_c11']>point['sub_c12'] for point in corners))
        for point, value in zip(corners, [.8, .5, .9]):
            search.update('corners', state, point, value, .01)
        restored = search.load_state(search.save_state(state))
        refined = search.propose('corners', state, space, 2)
        self.assertEqual(refined, search.propose('corners', restored, space, 2))
        self.assertTrue(all(point['sub_c11']>point['sub_c12'] for point in refined))
        self.assertFalse(any(point == previous for point in refined for previous in corners))
        self.assertTrue(any(np.any((physics.to_unit(point, space)>0)
                                  & (physics.to_unit(point, space)<1)) for point in refined))



class RoundTwoAnalysisTests(unittest.TestCase):
    def test_retained_spatial_inputs_preserve_recorded_geometry_and_prefix(self):
        import records
        initial = records.load_document(ROOT / 'studies/designs/stratified_128.json')
        self.assertEqual(initial['design_hash'], 'dbcabbb8eed5b3c0')
        self.assertEqual(len(initial['sites_mm']), 128)
        self.assertEqual(list(initial['stratum_counts'].values()), [19, 23, 28, 22, 36])
        small = records.load_document(ROOT / 'studies/designs/stratified_512.json')
        large = records.load_document(ROOT / 'studies/designs/stratified_2361.json')
        self.assertEqual(small['design_hash'], 'fa659c7d860acdee')
        self.assertEqual(large['design_hash'], '8d6f9b224ce5f834')
        self.assertEqual(len(small['sites_mm']), 512)
        self.assertEqual(len(large['sites_mm']), 2361)
        self.assertEqual(large['sites_mm'][:512], small['sites_mm'])
        self.assertEqual(large['stratum'][:512], small['stratum'])
        self.assertEqual(large['stratum_weights'], small['stratum_weights'])
        self.assertEqual(list(small['stratum_counts'].values()), [27, 94, 126, 93, 172])
        self.assertEqual(list(large['stratum_counts'].values()), [27, 376, 126, 1488, 344])
        fixed = records.load_document(ROOT / 'studies/reference.yaml')['fixed']
        for count, design in ((128, initial), (512, small), (2361, large)):
            self.assertEqual(design['half_span_mm'], 4.)
            self.assertEqual(design['junction_half_extent_mm'], [.005, .005])
            self.assertFalse({'results', 'value', 'tasks', 'blocks', 'standard_error'} & design.keys())
            checked = physics.build_design(fixed, {'kind': 'recorded-stratified', 'sites': count}, recorded=design)
            self.assertEqual(checked['sites_mm'], design['sites_mm'])
            self.assertEqual(checked['design_hash'], design['design_hash'])

    def density_fixture(self, root):
        import records
        prepared = {'fixed': records.load_document(ROOT / 'studies/reference.yaml')['fixed']}
        defaults = {row[0]: row[1] for row in records.load_document(ROOT / 'parameters.yaml')['morris']['QPDE_params']}
        blocks, arrays = [], []
        for position in range(3):
            for replica in range(2):
                qps = np.zeros((17, 1001))
                qps[(position + replica) % 17, [0, 1, 5, 700]] = [position + 1, replica + 2, 7, 2]
                path = root / f'time_{position}_{replica}.npz'
                np.savez_compressed(path, time_ns=np.linspace(0, 300000, 1001), qps=qps)
                blocks.append(dict(position=position, replica=replica, events=10 + position,
                                   time_data=str(path), time_data_sha256=records.sha256_file(path)))
                arrays.append(qps)
        return prepared, defaults, blocks, arrays

    def old_density(self, blocks, arrays, prepared, defaults):
        ode = dict(defaults); ode.update(prepared.get('ode', {})); fixed = prepared['fixed']
        replicas = sorted({row['replica'] for row in blocks}); outcomes = []
        for group in [list(range(len(blocks)))] + [[i for i, row in enumerate(blocks) if row['replica'] == replica] for replica in replicas]:
            qps = sum((arrays[i] for i in group), np.zeros_like(arrays[0]))
            events = sum(blocks[i]['events'] for i in group)
            times, rate = physics.calculate_xqps(np.linspace(0, 300000, 1001), qps,
                fixed['setTopGap'] / 4.135667696e-15, ode['f_01'], ode['r'], ode['s'], ode['I_ph'],
                ode['pt'], ode['n_cooper'], fixed['electrode_height_um'], fixed['electrode_width_um'],
                fixed['setTopThickness_um'], events)
            outcomes.append(float(np.trapezoid(rate, times, axis=1).sum()))
        return outcomes

    def recorded_fixture(self, root, *, stale=True, time_data=True):
        import records
        root.mkdir(); tasks = root / 'tasks'; tasks.mkdir()
        prepared = {'fixed': records.load_document(ROOT / 'studies/reference.yaml')['fixed']}
        fixed = prepared['fixed']
        study = dict(id='read-only-density', kind='confirmation', fixed=fixed,
                     analysis={'objective': 'total_qps_per_primary', 'bootstrap_draws': 16},
                     sampling=dict(replicas=2, events_per_task=10, seed_base=1, seed_bank=2, position_seed=3),
                     design=dict(sites_mm=[[0., 0.], [1., 1.]], stratum=['surface', 'surface'],
                                 stratum_weights={'surface': 1.}, electrode_weights=[1. / 17] * 17,
                                 design_hash='recorded-design'), events_total=40)
        study['resolved_hash'] = records.content_hash(study)
        records.write_json(root / 'resolved.json', study)
        blocks, declared = [], []
        for position in range(2):
            for replica in range(2):
                directory = tasks / f'{position}_{replica}'; directory.mkdir()
                path = directory / 'hits.csv'
                with path.open('w', newline='') as handle:
                    writer = csv.writer(handle); writer.writerow(records.HIT_COLUMNS)
                    writer.writerow([.00191, 0., 0., fixed['setSubThickness_um'] * 1e-6 / 2, 150. + 300. * position])
                block = dict(position=position, replica=replica, events=10, output=str(path),
                             sha256=records.sha256_file(path), total_qps=0., per_electrode_qps=[0.] * 17,
                             n_hits=1, score_hash='previous-scoring' if stale else analysis.scoring_identity())
                if not stale:
                    block['total_qps'] = 10.; block['per_electrode_qps'][4] = 10.
                if time_data:
                    time_path = directory / 'qps.npz'
                    np.savez_compressed(time_path, time_ns=np.linspace(0, 300000, 1001), qps=np.zeros((17, 1001)))
                    block.update(time_data=str(time_path), time_data_sha256=records.sha256_file(time_path))
                blocks.append(block); declared.append(dict(task_id=f'{position}_{replica}', position=position, replica=replica, events=10))
        with records.Results(root / 'results.sqlite') as database:
            database.plan('read-only', 'candidate', prepared, declared)
            for task, block in zip(declared, blocks):
                database.finish(task['task_id'], database.start(task['task_id']), 'complete', block)
            database.save_state('output_root', str(root))
            database.save_state('summary', {'results': [dict(candidate_id='candidate', name='reference', parameters={},
                                                          status='complete', value=0., standard_error=0., blocks=blocks)]})
        return prepared, blocks

    def test_streamed_density_matches_previous_pooled_and_replica_order_exactly(self):
        import records
        with tempfile.TemporaryDirectory() as directory:
            prepared, defaults, blocks, arrays = self.density_fixture(Path(directory))
            expected = self.old_density(blocks, arrays, prepared, defaults)
            cache = {}
            with patch.object(records, 'sha256_file', wraps=records.sha256_file) as checksum, patch.object(np, 'load', wraps=np.load) as load:
                first = analysis.density_response(blocks, prepared, defaults, cache)
                changed = {**prepared, 'ode': {'s': defaults['s'] + .002}}
                second = analysis.density_response(blocks, changed, defaults, cache)
                self.assertEqual(checksum.call_count, len(blocks))
                self.assertEqual(load.call_count, len(blocks))
            self.assertEqual([first['integrated_density']] + first['integrated_density_by_replica'], expected)
            self.assertEqual([second['integrated_density']] + second['integrated_density_by_replica'], self.old_density(blocks, arrays, changed, defaults))
            self.assertNotEqual(first['integrated_density'], second['integrated_density'])
            self.assertEqual(len(cache), 1)

    def test_density_cache_detects_corruption_and_changed_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared, defaults, blocks, arrays = self.density_fixture(Path(directory)); cache = {}
            initial = analysis.density_response(blocks, prepared, defaults, cache)
            changed = [dict(block, events=block['events'] * 2, replica=3 - block['replica']) for block in blocks]
            outcome = analysis.density_response(changed, prepared, defaults, cache)
            self.assertEqual([outcome['integrated_density']] + outcome['integrated_density_by_replica'], self.old_density(changed, arrays, prepared, defaults))
            self.assertNotEqual(initial['integrated_density'], outcome['integrated_density'])
            path = Path(blocks[0]['time_data']); data = bytearray(path.read_bytes()); data[-15] ^= 1; path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'checksum differs'):
                analysis.density_response(changed, prepared, defaults, cache)

    def test_identical_time_bytes_with_changed_signature_reuse_arrays_after_hash(self):
        import records
        with tempfile.TemporaryDirectory() as directory:
            prepared, defaults, blocks, _ = self.density_fixture(Path(directory)); cache = {}
            initial = analysis.density_response(blocks, prepared, defaults, cache)
            path = Path(blocks[0]['time_data']); replacement = path.with_suffix('.replacement')
            replacement.write_bytes(path.read_bytes()); replacement.replace(path)
            with patch.object(records, 'sha256_file', wraps=records.sha256_file) as checksum, patch.object(np, 'load', side_effect=AssertionError('unchanged bytes reloaded')):
                self.assertEqual(analysis.density_response(blocks, prepared, defaults, cache), initial)
                self.assertEqual(checksum.call_count, 1)
                self.assertEqual(analysis.density_response(blocks, prepared, defaults, cache), initial)
                self.assertEqual(checksum.call_count, 1)

    def test_old_scoring_density_reanalysis_preserves_raw_database_and_old_time_files(self):
        import records
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'recorded'; prepared, blocks = self.recorded_fixture(root)
            before = {str(path.relative_to(root)): records.sha256_file(path) for path in root.rglob('*') if path.is_file()}
            report = analysis.read_analysis(root, settings={'objective': 'integrated_density', 'diagnostics': False})
            row = report['results'][0]
            arrays = []
            for block in blocks:
                qps = np.zeros((17, 1001)); qps[4, block['position']] = 10.
                arrays.append(qps)
            defaults = {entry[0]: entry[1] for entry in records.load_document(ROOT / 'parameters.yaml')['morris']['QPDE_params']}
            expected = self.old_density(blocks, arrays, prepared, defaults)
            self.assertEqual([row['integrated_density']] + row['integrated_density_by_replica'], expected)
            self.assertGreater(row['value'], 0.)
            self.assertTrue(all(block['total_qps'] == 10. for block in row['blocks']))
            self.assertTrue(all('time_data' not in block for block in row['blocks']))
            self.assertEqual(before, {str(path.relative_to(root)): records.sha256_file(path) for path in root.rglob('*') if path.is_file()})

    def test_density_can_be_added_read_only_when_current_scoring_has_no_time_files(self):
        import records
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'recorded'; self.recorded_fixture(root, stale=False, time_data=False)
            before = records.sha256_file(root / 'results.sqlite')
            report = analysis.read_analysis(root, settings={'objective': 'integrated_density', 'diagnostics': False})
            self.assertGreater(report['results'][0]['value'], 0.)
            self.assertIsNone(report['results'][0]['standard_error'])
            self.assertEqual(records.sha256_file(root / 'results.sqlite'), before)
            self.assertEqual(list(root.rglob('*.npz')), [])

    def test_raw_density_distinguishes_header_only_zero_from_missing_file(self):
        import records
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.csv'
            path.write_text(','.join(records.HIT_COLUMNS) + '\n')
            prepared = {'fixed': records.load_document(ROOT / 'studies/reference.yaml')['fixed']}
            block = dict(output=str(path), sha256=records.sha256_file(path), position=0, replica=0, events=10)
            result = analysis.density_response([block], prepared, from_hits=True)
            self.assertEqual(result['integrated_density'], 0.)
            self.assertEqual(result['integrated_density_by_replica'], [0.])
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                analysis.density_response([block], prepared, from_hits=True)

    def test_figures_preserve_missing_uncertainty_and_explicit_zero(self):
        from matplotlib.axes import Axes
        original = Axes.errorbar; captured = []
        def capture(axis, *args, **kwargs):
            captured.append(np.asarray(kwargs['xerr']))
            return original(axis, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.object(Axes, 'errorbar', new=capture):
            output = Path(directory) / 'uncertainty.svg'
            analysis.plot_result({'results': [dict(name='unmeasured-error', value=1., standard_error=None),
                                             dict(name='measured-zero-error', value=2., standard_error=0.)]}, output)
            self.assertTrue(np.isnan(captured[0][0]))
            self.assertEqual(captured[0][1], 0.)
            self.assertIn('uncertainty unavailable', output.read_text())

    def test_projection_figure_keeps_material_ids_and_feature_coverage(self):
        result = analysis.project_materials({'speed': 2., 'scat': .1},
                    [dict(id='mp-complete', features={'speed': 3., 'scat': .1}),
                     dict(id='mp-partial', features={'speed': 2.})], {'speed': 'linear', 'scat': 'log'})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'projection.svg'
            analysis.plot_result({'projection': result}, output, kind='projection')
            text = output.read_text()
            self.assertIn('mp-complete [2/2]', text)
            self.assertIn('mp-partial [1/2]', text)
            self.assertLess(text.index('mp-complete [2/2]'), text.index('mp-partial [1/2]'))


if __name__ == '__main__':
    unittest.main()
