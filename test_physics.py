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

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'material_scan/tests/fixtures'


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
            time, counts = physics.calculate_qps(*args)
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
        time, qps = physics.calculate_qps(*args)
        self.assertEqual(time[1], 300.)
        self.assertEqual(qps[0, 0], 6.)
        self.assertEqual(qps[1, 1], 2.)
        self.assertEqual(qps.sum(), 8.)
        np.testing.assert_array_equal(physics.qp_counts(*args), [6., 2.])

    def test_hit_validation_and_measured_zero(self):
        args = ([], [], [], [], [], .000191, [0.], [0.], .0002625)
        self.assertEqual(float(physics.qp_counts(*args).sum()), 0.)
        self.assertEqual(float(physics.calculate_qps(*args)[1].sum()), 0.)
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
        derived = physics.check_values(point)
        self.assertEqual(derived['setTopAbs'], .795)
        self.assertTrue(derived['excitation_gate']['ground_plane_active_absorber'])
        self.assertFalse(physics.check_values(point,gun_energy_eV=.000382)['excitation_gate']['ground_plane_active_absorber'])
        cases = ({'gun_energy_eV': .000381}, {'cutoff_eV': .000382}, {'ground_plane_active_absorber': False})
        for kwargs in cases:
            with self.assertRaises(ValueError):
                physics.check_values(point, **kwargs)
        with self.assertRaises(ValueError):
            physics.check_values({**point, 'topfilm_gap': .0001}, constraints={'topfilm_gap_min_eV': .0015384})
        with self.assertRaises(ValueError):
            physics.check_values({**point, 'bot_gap_thres': .0004})

    def test_lattice_inputs_reject_ambiguous_fields_and_units(self):
        import inputs
        text = ('cubic 5.431 Ang\nstiffness 1 1 165.6 GPa\nstiffness 1 2 63.9 GPa\n'
                'stiffness 4 4 79.5 GPa\ndyn -42.9 -94.5 52.4 68.0 GPa\n'
                'scat 2.43e-42 s3\ndecay 7.41e-56 s4\ndecayTT 0.74\n'
                'LDOS 0.093\nSTDOS 0.531\nFTDOS 0.376\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.txt'
            path.write_text(text)
            self.assertEqual(inputs.lattice_record(path)['stiffness 1 1'], [165.6])
            for invalid in (text + 'stiffness 1 1 170 GPa\n',
                            text.replace('165.6 GPa', '1.656e11 Pa'),
                            text.replace('165.6 GPa', 'nan GPa')):
                path.write_text(invalid)
                with self.assertRaises(ValueError):
                    inputs.lattice_record(path)

    def test_thermal_scaling_and_finite_log_ratios(self):
        al, nb = .000191, .0015384
        self.assertGreater(physics.thermal_qp_density(al, .02), physics.thermal_qp_density(nb, .02))
        self.assertAlmostEqual(physics.film_tc_K(nb), nb / (1.764 * 8.617333262e-5))
        d = physics.engineering_diagnostics({'topfilm_gap': .00005})
        self.assertTrue(d['ground_plane_is_phonon_sink'])
        self.assertGreater(d['log10_loss_proxy_vs_Nb'], 300.)
        self.assertTrue(math.isfinite(d['log10_thermal_qp_density_ratio_vs_Nb']))


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
            result = analysis.estimate(blocks, design)
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
                analysis.estimate(bad, design, bootstrap_draws=2, tail_bootstrap_draws=2)
        bad = dict(design, stratum_weights={'missing': 1.})
        with self.assertRaises(ValueError):
            analysis.validate_complete_blocks(blocks, bad)

    def test_weighting_and_zero_yield(self):
        design = dict(position_strata=['near', 'bulk'], stratum_weights={'near': .25, 'bulk': .75},
                      replica_ids=[0, 1], events_per_task=100, gun_energy_eV=.01,
                      electrode_weights=[.5, .5], design_hash='synthetic')
        blocks = [dict(position=p, replica=r, events=100, total_qps=total, per_electrode_qps=q)
                  for p, total, q in ((0, 4., [1., 3.]), (1, 2., [2., 0.])) for r in (0, 1)]
        result = analysis.estimate(blocks, design, bootstrap_draws=20, tail_bootstrap_draws=20)
        self.assertEqual(result['value'], 1.25)
        self.assertEqual(result['standard_error'], 0.)
        self.assertEqual(result['naive_equal_site_mean'], 1.5)
        zero = [dict(b, total_qps=0., per_electrode_qps=[0., 0.]) for b in blocks]
        result = analysis.estimate(zero, design, bootstrap_draws=20, tail_bootstrap_draws=20)
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
        result = analysis.historical_summary(blocks, design)
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
        np.testing.assert_allclose(analysis.realization_estimates(blocks, design), [1.99, 3.98])
        np.testing.assert_allclose(analysis.realization_estimates(blocks, design, 'total_qps_per_primary'), [.505, 1.01])

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
        self.assertIsNone(analysis.distance({'speed': 2.}, {'features': {'speed': float('nan')}}, {'speed': 'linear'}, {'speed': 1.}))

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
        import inputs
        import scan
        study = inputs.load_study({'schema': 'material-scan-study-2', 'id': 'recorded-morris',
                                   'kind': 'sensitivity', 'analysis': {'objective': 'integrated_density'}})
        problem = {'num_vars': 1, 'names': ['r'], 'bounds': [[0., 1.]]}
        study['morris_design'] = {'problem': problem, 'seed': 7, 'levels': 4}
        rows = [dict(name=f'morris_{i}', status='complete', inputs={'r': value}, trajectory=i // 2,
                     design_index=i, value=1. + value, integrated_density=1. + value)
                for i, value in enumerate((0., 2. / 3., 1., 1. / 3.))]
        with patch.object(inputs, 'candidate_table', side_effect=AssertionError('saved inputs were regenerated')):
            result = scan.analyse_results(study, rows)['sensitivity']
            np.testing.assert_allclose(result['mu'], [1.], rtol=1e-12)
            self.assertEqual(result['morris_design']['problem'], problem)
            rows[-1]['status'] = 'incomplete'
            result = scan.analyse_results(study, rows)['sensitivity']
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
        from inputs import search_space
        from search import load_state, new_state, propose, save_state, update
        space = search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed')
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
        from inputs import search_space
        from search import new_state, propose, update
        space = search_space({'topfilm_vsound': {'mode': 'search'}}, kind='fixed')
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


if __name__ == '__main__':
    unittest.main()
