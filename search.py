"""Search physical properties x through normalized u in [0,1]^d.

J: selected measured response; y=log(max(J,J_floor)) by default; s_y its error.
"""

import copy
import json
import math
from pathlib import Path

import numpy as np
from scipy.linalg import cho_solve, solve_triangular
from scipy.optimize import minimize
from scipy.stats import norm, qmc
from scipy.spatial import cKDTree

from physics import from_unit, to_unit, validate_values


class GaussianProcess:
    """Matern-5/2 GP for y(u); noise_var=s_y^2 is measured response variance."""

    def __init__(self, jitter=1e-08, sphere_columns=None):
        self.jitter = jitter
        self.sphere_columns = tuple(sphere_columns) if sphere_columns is not None else None
        self.theta = None
        self.fitted = False

    def _distance_parts(self, A, B, ell):
        A = np.atleast_2d(np.asarray(A, dtype=float))
        B = np.atleast_2d(np.asarray(B, dtype=float))
        parts = []
        sphere = set(self.sphere_columns or ())
        for column in range(A.shape[1]):
            if column in sphere:
                continue
            difference = A[:, column][:, None] - B[:, column][None, :]
            parts.append((column, difference * difference / ell[column] ** 2))
        if self.sphere_columns is not None:
            (polar_column, azimuth_column) = self.sphere_columns

            def embed(values):
                z = 2.0 * values[:, polar_column] - 1.0
                radius = np.sqrt(np.maximum(0.0, 1.0 - z * z))
                phi = 2.0 * math.pi * values[:, azimuth_column]
                return (radius * np.cos(phi), radius * np.sin(phi), z)
            (ax, ay, az) = embed(A)
            (bx, by, bz) = embed(B)
            equatorial = (ax[:, None] - bx[None, :]) ** 2 + (ay[:, None] - by[None, :]) ** 2
            polar = (az[:, None] - bz[None, :]) ** 2
            parts.append((azimuth_column, equatorial / ell[azimuth_column] ** 2))
            parts.append((polar_column, polar / ell[polar_column] ** 2))
        parts.sort(key=lambda item: item[0])
        return [part for (_, part) in parts]

    def _matern(self, A, B, ell):
        parts = self._distance_parts(A, B, ell)
        r = np.sqrt(np.maximum(np.sum(parts, axis=0), 0.0))
        s5r = math.sqrt(5.0) * r
        return ((1.0 + s5r + 5.0 / 3.0 * r ** 2) * np.exp(-s5r), r)

    def _unpack(self, theta):
        d = self.X.shape[1]
        log_sf2 = theta[0]
        log_ell = theta[1:1 + d]
        log_sn2 = theta[1 + d]
        return (math.exp(log_sf2), np.exp(log_ell), math.exp(log_sn2))

    def _build(self, theta):
        # K_ab=k(u_a,u_b)+delta_ab*(s_y[a]^2+s_n^2+jitter), standardized y.
        (sf2, ell, sn2) = self._unpack(theta)
        (m, r) = self._matern(self.X, self.X, ell)
        K = sf2 * m
        K[np.diag_indices_from(K)] += self.noise + sn2 + self.jitter
        return (K, m, r, sf2, ell, sn2)

    def _nll(self, theta):
        try:
            (K, m, r, sf2, ell, sn2) = self._build(theta)
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            return (10000000000.0, np.zeros_like(theta))
        alpha = cho_solve((L, True), self.y, check_finite=False)
        nll = 0.5 * float(self.y @ alpha) + float(np.log(np.diag(L)).sum()) + 0.5 * len(self.y) * math.log(2 * math.pi)
        Kinv = cho_solve((L, True), np.eye(len(self.y)), check_finite=False)
        W = np.outer(alpha, alpha) - Kinv
        grad = np.zeros_like(theta)
        Ksig = sf2 * m
        grad[0] = -0.5 * np.sum(W * Ksig)
        s5r = math.sqrt(5.0) * r
        pref = sf2 * (5.0 / 3.0) * (1.0 + s5r) * np.exp(-s5r)
        for (i, distance_part) in enumerate(self._distance_parts(self.X, self.X, ell)):
            dK = pref * distance_part
            grad[1 + i] = -0.5 * np.sum(W * dK)
        dK_sn2 = np.eye(len(self.y)) * sn2
        grad[1 + self.X.shape[1]] = -0.5 * np.sum(W * dK_sn2)
        return (nll, grad)

    def fit(self, X, y, noise_var=None, n_restarts=3, rng=None):
        rng = rng or np.random.default_rng(0)
        self.X = np.atleast_2d(np.asarray(X, dtype=float))
        if self.sphere_columns is not None:
            if len(self.sphere_columns) != 2 or len(set(self.sphere_columns)) != 2:
                raise ValueError('sphere_columns must name two different columns')
            if min(self.sphere_columns) < 0 or max(self.sphere_columns) >= self.X.shape[1]:
                raise ValueError('sphere column is outside the input array')
        y = np.asarray(y, dtype=float)
        self.y_mean = float(y.mean())
        self.y_std = float(y.std()) or 1.0
        # Fit standardized y; measured variances must use the same squared scale.
        self.y = (y - self.y_mean) / self.y_std
        d = self.X.shape[1]
        if noise_var is None:
            noise = np.zeros(len(y))
        else:
            noise = np.asarray(noise_var, dtype=float) / self.y_std ** 2
            finite = np.isfinite(noise)
            noise = np.where(finite, noise, np.median(noise[finite]) if finite.any() else 0.0001)
        self.noise = np.maximum(noise, 0.0)
        bounds = ([(math.log(0.001), math.log(1000.0))]
                  + [(math.log(0.02), math.log(20.0))] * d
                  + [(math.log(1e-08), math.log(10.0))])
        best = None
        starts = [np.array([0.0] + [math.log(0.5)] * d + [math.log(0.001)])]
        for _ in range(max(0, n_restarts - 1)):
            starts.append(np.array([rng.normal(0.0, 1.0)]
                                   + list(np.log(rng.uniform(0.1, 3.0, size=d)))
                                   + [math.log(10 ** rng.uniform(-6, -1))]))
        for x0 in starts:
            x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
            try:
                res = minimize(self._nll, x0, jac=True, method='L-BFGS-B', bounds=bounds, options={'maxiter': 200})
            except np.linalg.LinAlgError:
                continue
            if res.fun is not None and (best is None or res.fun < best.fun):
                best = res
        if best is None:
            raise RuntimeError('GP hyperparameter fit failed on every restart')
        self.theta = best.x
        (K, *_) = self._build(self.theta)
        self.L = np.linalg.cholesky(K)
        self.alpha = cho_solve((self.L, True), self.y, check_finite=False)
        self.fitted = True
        self.nll = float(best.fun)
        return self

    def predict(self, Xs, return_std=True):
        """Condition standardized y: mu=k_* K^-1 y, v=k_**-k_* K^-1 k_*^T.

        Return mean and latent standard deviation on the original y scale.
        """
        (sf2, ell, sn2) = self._unpack(self.theta)
        Xs = np.atleast_2d(np.asarray(Xs, dtype=float))
        (m, _) = self._matern(Xs, self.X, ell)
        Ks = sf2 * m
        mu = Ks @ self.alpha
        mu = mu * self.y_std + self.y_mean
        if not return_std:
            return mu
        v = solve_triangular(self.L, Ks.T, lower=True, check_finite=False)
        var = sf2 - np.sum(v ** 2, axis=0)
        var = np.maximum(var, 1e-12) * self.y_std ** 2
        return (mu, np.sqrt(var))

    def reset_data(self, X, y, noise_var):
        self.X = np.atleast_2d(np.asarray(X, dtype=float))
        y = np.asarray(y, dtype=float)
        self.y = (y - self.y_mean) / self.y_std
        noise = np.asarray(noise_var, dtype=float) / self.y_std ** 2
        finite = np.isfinite(noise)
        self.noise = np.maximum(np.where(finite, noise, np.median(noise[finite]) if finite.any() else 0.0001), 0.0)
        (K, *_) = self._build(self.theta)
        self.L = np.linalg.cholesky(K)
        self.alpha = cho_solve((self.L, True), self.y, check_finite=False)
        return self

    def add_fantasy(self, x, y_value, noise_value):
        x = np.atleast_2d(x)
        sf2, ell, sn2 = self._unpack(self.theta)
        cross, _ = self._matern(self.X, x, ell)
        scaled = solve_triangular(self.L, sf2 * cross[:, 0], lower=True, check_finite=False)
        diagonal = sf2 + noise_value / self.y_std ** 2 + sn2 + self.jitter
        residual = diagonal - float(scaled @ scaled)
        n = len(self.y)
        self.X = np.vstack([self.X, x])
        self.y = np.append(self.y, (y_value - self.y_mean) / self.y_std)
        self.noise = np.append(self.noise, noise_value / self.y_std ** 2)
        if residual > 0:
            enlarged = np.zeros((n + 1, n + 1))
            enlarged[:n, :n] = self.L
            enlarged[n, :n] = scaled
            enlarged[n, n] = math.sqrt(residual)
            self.L = enlarged
        else:
            K, *_ = self._build(self.theta)
            self.L = np.linalg.cholesky(K)
        self.alpha = cho_solve((self.L, True), self.y, check_finite=False)


class CMAES:
    """Gaussian steps u_raw=m+sigma*L*z, z~N(0,I), L*L^T=Cov."""

    def __init__(self, dimension, options=None):
        options = options or {}
        self.d = int(dimension)
        if self.d < 1:
            raise ValueError('CMA-ES needs at least one coordinate')
        self.sigma0 = float(options.get('sigma0', 0.3))
        self.sigma_min = float(options.get('sigma_min', 0.03))
        self.stagnation = int(options.get('stagnation', 8))
        self.max_popsize = int(options.get('max_popsize', 64))
        self.synchronous = bool(options.get('synchronous', True))
        self.initial_mean = np.asarray(options.get('initial_mean', [0.5] * self.d), dtype=float)
        if (self.initial_mean.shape != (self.d,) or not np.isfinite(self.initial_mean).all()
                or np.any((self.initial_mean < 0) | (self.initial_mean > 1))):
            raise ValueError('CMA initial_mean must be a finite vector in [0,1]^d')
        self.restarts = 0
        self.best_at_restart = None
        self.gens_without_gain = 0
        self.reset(int(options.get('popsize') or max(8, 4 + int(3 * math.log(self.d)))))

    def reset(self, population):
        d = self.d
        self.lam = int(population)
        if self.lam < 4:
            raise ValueError('CMA-ES population must be at least four')
        self.mu = self.lam // 2
        w = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.w = w / w.sum()
        self.mueff = 1.0 / np.sum(self.w ** 2)
        self.cc = (4 + self.mueff / d) / (d + 4 + 2 * self.mueff / d)
        self.cs = (self.mueff + 2) / (d + self.mueff + 5)
        self.c1 = 2 / ((d + 1.3) ** 2 + self.mueff)
        self.cmu = min(1 - self.c1,
                       2 * (self.mueff - 2 + 1 / self.mueff) / ((d + 2) ** 2 + self.mueff))
        self.damps = 1 + 2 * max(0, math.sqrt((self.mueff - 1) / (d + 1)) - 1) + self.cs
        self.chiN = math.sqrt(d) * (1 - 1 / (4 * d) + 1 / (21 * d ** 2))
        self.mean = self.initial_mean.copy()
        self.sigma = self.sigma0
        self.pc = np.zeros(d)
        self.ps = np.zeros(d)
        self.Cov = np.eye(d)
        self.generation = 0
        self.points = []
        self.results = {}

    def update(self):
        keys = [key for key, _ in self.points]
        required = self.lam if self.synchronous else max(4, self.mu + 1)
        if (len(keys) < required
                or not all(key in self.results for key in keys)):
            return []
        U = np.array([u for _, u in self.points])
        order = np.argsort([self.results[key] for key in keys])
        mu = min(self.mu, len(order) - 1)
        w = self.w[:mu] / self.w[:mu].sum()
        old_mean = self.mean.copy()
        selected = U[order[:mu]]
        # m_new=sum_a w_a*u_(a), where (a) ranks measured y from lowest to highest.
        self.mean = w @ selected
        y_w = (self.mean - old_mean) / self.sigma
        try:
            L = np.linalg.cholesky(self.Cov + 1e-12 * np.eye(self.d))
            scaled_step = solve_triangular(L, y_w, lower=True, check_finite=False)
        except np.linalg.LinAlgError:
            scaled_step = y_w
        self.ps = ((1 - self.cs) * self.ps
                   + math.sqrt(self.cs * (2 - self.cs) * self.mueff) * scaled_step)
        hsig = (np.linalg.norm(self.ps)
                / math.sqrt(1 - (1 - self.cs) ** (2 * (self.generation + 1)))
                / self.chiN) < (1.4 + 2 / (self.d + 1))
        self.pc = ((1 - self.cc) * self.pc
                   + hsig * math.sqrt(self.cc * (2 - self.cc) * self.mueff) * y_w)
        artmp = (selected - old_mean) / self.sigma
        self.Cov = ((1 - self.c1 - self.cmu) * self.Cov
                    + self.c1 * (np.outer(self.pc, self.pc)
                                 + (not hsig) * self.cc * (2 - self.cc) * self.Cov)
                    + self.cmu * (artmp.T * w) @ artmp)
        self.sigma *= math.exp((self.cs / self.damps)
                               * (np.linalg.norm(self.ps) / self.chiN - 1))
        self.sigma = float(np.clip(self.sigma, 1e-4, 1.0))
        self.generation += 1
        self.points, self.results = [], {}
        return keys


def expected_improvement(mu, sigma, best, xi=0.0):
    """EI=E[max(y_best-y-xi,0)]; y~N(mu,sigma^2), lower y is preferred."""
    sigma = np.maximum(sigma, 1e-12)
    improvement = best - mu - xi
    z = improvement / sigma
    return improvement * norm.cdf(z) + sigma * norm.pdf(z)


def new_state(method, seed=0, options=None):
    if method not in ('random', 'sobol', 'bo_gp', 'cmaes', 'tpe_optuna', 'llm_agent', 'llm_bo'):
        raise ValueError(f'unknown search method {method!r}')
    return {'method': method, 'seed': int(seed), 'options': dict(options or {}),
            'rng': np.random.default_rng(seed), 'iteration': 0, 'U': [], 'Y': [],
            'SIG': [], 'points': [], 'values': [], 'se': [], 'pending': [],
            'pending_ids': [], 'observation_ids': [], 'proposal_units': [], 'next_proposal_id': 0,
            'rejected': [], 'proposals': {}, 'used': {}, 'sobol_draws': 0,
            'design_cursor': 0, 'gp_fits': 0, 'algorithm_version': 3,
            'attempts': [], 'initial_count': 0, 'gp_pool_round': 0}


def import_observations(state, space, observations):
    """Import the same measured starts; these are not new optimizer evaluations."""
    if state['Y'] or state['pending'] or state['rejected']:
        raise ValueError('common starts require an empty optimizer state')
    if not observations:
        raise ValueError('common starting observations are empty')
    checked = []
    for row in observations:
        point = dict(space['baseline']); point.update(row['values'])
        if any(name not in row['values'] for name in space['names']):
            raise ValueError('common start lacks a searched coordinate')
        if any(name not in space['names'] and point.get(name) != value
               for name, value in space['baseline'].items()):
            raise ValueError('common start changes a fixed property')
        validate_values(point, space, exact=False)
        error = row.get('transport_standard_error', row.get('standard_error'))
        if (not math.isfinite(float(row['value'])) or float(row['value']) < 0
                or error is not None and (not math.isfinite(float(error)) or float(error) < 0)):
            raise ValueError('common starts require a finite nonnegative response and error')
        if row.get('elapsed_s') is not None and (not math.isfinite(float(row['elapsed_s'])) or float(row['elapsed_s']) < 0):
            raise ValueError('common start has invalid elapsed_s')
        if any(np.max(np.abs(to_unit(point, space) - to_unit(p, space))) < 1e-6 for p, _ in checked):
            raise ValueError('duplicate common starting point')
        checked.append((point, row))
    state['space'] = copy.deepcopy(space)
    state['_dispatch'] = {}
    for point, row in checked:
        _tag(state, space, point, 'shared_initial', candidate_id=row.get('candidate_id'), observation_source=row.get('source'))
        identifier = state['_dispatch'][id(point)][0]
        state['pending'].append(point); state['pending_ids'].append(identifier)
        update(state['method'], state, point, row['value'],
               se=row.get('transport_standard_error', row.get('standard_error')),
               proposal_id=identifier, elapsed_s=row.get('elapsed_s'), status='complete')
    state.pop('_dispatch')
    state['initial_count'] = len(checked)
    state['initial_observations'] = copy.deepcopy(observations)
    state['options']['n_init'] = len(checked)
    state['options']['initial_mean'] = state['U'][int(np.argmin(state['values']))].tolist()
    return state


def pending_id(state, point, space=None):
    """Find one pending proposal by its exact normalized coordinates."""
    u = to_unit(point, space or state['space'])
    matches = [identifier for identifier in state['pending_ids']
               if np.array_equal(state['proposal_units'][identifier], u)]
    if len(matches) != 1:
        raise ValueError('pending proposal is missing or ambiguous; supply its integer proposal_id')
    return matches[0]


def _feasible(point, space):
    try:
        validate_values(point, space, exact=False)
        return True
    except ValueError:
        return False


def _duplicate(point, state, space, extra=()):
    u = to_unit(point, space)
    observed = np.asarray(state['U'])
    if observed.size and np.any(np.max(np.abs(observed - u), axis=1) < 1e-6):
        return True
    return any(np.max(np.abs(to_unit(p, space) - u)) < 1e-6
               for p in [*state['pending'], *extra])


def _draw_points(state, space, count, extra=(), *, sobol=False):
    if sobol and '_sobol' not in state:
        state['_sobol'] = qmc.Sobol(d=len(space['names']), scramble=True, seed=state['seed'])
        if state['sobol_draws']:
            state['_sobol'].fast_forward(state['sobol_draws'])
    out = []
    attempts = 64 if sobol else int(state['options'].get('max_resample', 500))
    for _ in range(attempts * max(1, count)):
        u = state['_sobol'].random(1)[0] if sobol else state['rng'].random(len(space['names']))
        if sobol:
            state['sobol_draws'] += 1
        point = from_unit(u, space)
        if _feasible(point, space) and not _duplicate(point, state, space, [*extra, *out]):
            if sobol:
                _tag(state, space, point, 'sobol_init', design_index=state['design_cursor'])
                state['design_cursor'] += 1
            out.append(point)
            if len(out) == count:
                return out
    if not sobol:
        raise RuntimeError(f'could not draw {count} distinct feasible points')
    for point in _draw_points(state, space, count - len(out), [*extra, *out]):
        out.append(_tag(state, space, point, 'random_filler', reason='Sobol points failed the gates'))
    return out


def _tag(state, space, point, source, **details):
    dispatched = state['_dispatch']
    if id(point) in dispatched:
        identifier = dispatched[id(point)][0]
    else:
        identifier = state['next_proposal_id']
        state['next_proposal_id'] += 1
        state['proposal_units'].append(to_unit(point, space))
        dispatched[id(point)] = (identifier, point)
    metadata = {
        'proposal_id': identifier,
        'proposal_source': source, 'optimizer': state['method'],
        'iteration': state['iteration'], **details}
    for original, rationale in state.get('_llm_rationales', []):
        if np.array_equal(to_unit(original, space), state['proposal_units'][identifier]):
            metadata['rationale'] = rationale
            break
    for original, source_ids in state.get('_llm_sources', []):
        if np.array_equal(to_unit(original, space), state['proposal_units'][identifier]):
            metadata['source_ids'] = source_ids
            break
    state['proposals'][str(identifier)] = metadata
    return point


def _feasible_pool(U, space, excluded):
    """Remove invalid and repeated normalized vectors with bounded array work."""
    U = np.asarray([u for u in U if _feasible(from_unit(u, space), space)])
    if not U.size:
        return np.empty((0, len(space['names'])))
    if len(excluded):
        distance, _ = cKDTree(np.asarray(excluded)).query(U, p=np.inf)
        U = U[distance >= 1e-6]
    repeated = {j for _, j in cKDTree(U).query_pairs(1e-6, p=np.inf)}
    return U[[i for i in range(len(U)) if i not in repeated]]


def _runtime_weights(state, U):
    """Estimate p_complete(u)/t(u); timeout durations are lower bounds on t."""
    attempts = [a for a in state.get('attempts', []) if a.get('elapsed_s') is not None]
    if not attempts:
        return np.ones(len(U))
    X = np.asarray([a['u'] for a in attempts])
    width = float(state['options'].get('runtime_width', 0.2))
    if width <= 0:
        raise ValueError('runtime_width must be positive')
    weights = np.exp(-np.mean((U[:, None, :] - X[None, :, :]) ** 2, axis=2) / (2 * width ** 2))
    complete = np.asarray([a['status'] == 'complete' for a in attempts], dtype=float)
    # Beta(1,1) smoothing prevents a handful of failures becoming a hard exclusion.
    probability = (1 + weights @ complete) / (2 + weights.sum(axis=1))
    durations = np.asarray([max(a['elapsed_s'], 1e-3) for a in attempts])
    done = complete.astype(bool)
    typical = float(np.median(durations[done])) if done.any() else float(np.median(durations))
    completed_weights = weights * complete[None, :]
    estimate = np.exp((math.log(typical) + completed_weights @ np.log(durations))
                      / (1 + completed_weights.sum(axis=1)))
    # Local timeouts can only raise estimated cost; they are not completed runtimes.
    for j, attempt in enumerate(attempts):
        if attempt['status'] == 'timeout':
            estimate = np.maximum(estimate, durations[j] * weights[:, j])
    return probability / estimate


def _gp_points(state, space, count, pool=None):
    options = state['options']
    n_init = int(options.get('n_init', 24))
    n_observed = len(state['Y'])
    dispatched = n_observed + sum(
        state['proposals'][str(identifier)]['proposal_source'].endswith('_init')
        for identifier in state['pending_ids'])
    if dispatched < n_init:
        take = min(count, n_init - dispatched)
        out = _draw_points(state, space, take, sobol=True)
        for i, point in enumerate(out):
            _tag(state, space, point, 'sobol_init', design_index=dispatched + i)
        if take < count:
            out += [_tag(state, space, p, 'random_filler', reason='initial design finished')
                    for p in _draw_points(state, space, count - take, out)]
        return out
    if n_observed < min(n_init, int(options.get('n_min_fit', n_init))):
        return []
    refit_every = max(1, int(options.get('refit_every', 5)))
    if ('_gp' not in state or (n_observed % refit_every == 0
                              and state.get('gp_fit_observations') != n_observed)):
        names = space['names']
        polar, azimuth = 'orientation_polar_cos', 'orientation_azimuth_turns'
        sphere = (names.index(polar), names.index(azimuth)) if polar in names and azimuth in names else None
        gp = GaussianProcess(sphere_columns=sphere).fit(
            np.array(state['U']), np.array(state['Y']),
            noise_var=np.array(state['SIG'], dtype=float) ** 2, rng=state['rng'],
            n_restarts=int(options.get('n_restarts', 3)))
        state['_gp'] = gp
        state['gp_fits'] += 1
        state['gp_fit_observations'] = n_observed
    else:
        gp = state['_gp'].reset_data(np.array(state['U']), np.array(state['Y']),
                                     np.array(state['SIG'], dtype=float) ** 2)
    noise = np.array(state['SIG'], dtype=float) ** 2
    median_noise = float(np.median(noise[np.isfinite(noise)])) if np.isfinite(noise).any() else 1e-4
    for point in state['pending']:
        u = to_unit(point, space)
        mu, _ = gp.predict(u[None, :])
        gp.add_fantasy(u, float(mu[0]), median_noise)
    out = []
    d = len(space['names'])
    xi = float(options.get('xi', 0.0))
    additional = list(pool or [])
    for _ in range(count):
        mu_observed, _ = gp.predict(np.array(state['U']))
        best = float(np.min(mu_observed))
        incumbent = np.array(state['U'][int(np.argmin(mu_observed))])
        n = int(options.get('n_candidates', 4096))
        extra = int(options.get('extra_candidates', 8))
        if n < 4 or extra < 0:
            raise ValueError('GP candidate counts must be at least four and nonnegative')
        # Equal global Sobol streams across arms; local points follow each measured best.
        seed = (int(options.get('pool_seed', state['seed'])) * 1000003 + state['gp_pool_round']) % (1 << 31)
        engine = qmc.Sobol(d=d, scramble=True, seed=seed)
        local_rng = np.random.default_rng(seed)
        width = float(state.get('local_width', options.get('local_width', 0.08)))
        raw = engine.random_base2(int(math.ceil(math.log2(n))))[:n]
        raw[-(n // 4):] = np.clip(incumbent + local_rng.normal(0, width, (n // 4, d)), 0, 1)
        observed = np.asarray([*state['U'], *[to_unit(p, space) for p in [*state['pending'], *out]]])
        U = _feasible_pool(raw, space, observed)
        attempts = 0
        while len(U) < n:
            attempts += 1
            if attempts > 1000:
                raise RuntimeError('could not fill the feasible GP pool')
            fill = _feasible_pool(local_rng.random((2 * (n - len(U)), d)), space,
                                  np.vstack([observed, U]))
            U = np.vstack([U, fill[:n - len(U)]])
        sources = ['ordinary'] * n
        n_model = 0
        for point in additional:
            u = to_unit(point, space)
            if (n_model < extra and _feasible(point, space) and not _duplicate(point, state, space, out)
                    and not np.any(np.max(np.abs(U - u), axis=1) < 1e-6)):
                U = np.vstack([U, u]); sources.append('llm'); n_model += 1
        while len(U) < n + extra:
            attempts += 1
            if attempts > 1000:
                raise RuntimeError('could not fill the additional GP pool')
            fill = _feasible_pool(engine.random(2 * (n + extra - len(U))), space,
                                  np.vstack([observed, U]))
            fill = fill[:n + extra - len(U)]
            U = np.vstack([U, fill]); sources.extend(['sobol_extra'] * len(fill))
        state['gp_pool_round'] += 1
        state['last_pool'] = {'ordinary': n, 'additional': extra, 'llm': n_model,
                              'total': len(U), 'seed': seed, 'local_width': width}
        mu, sd = gp.predict(U)
        scores = expected_improvement(mu, sd, best, xi)
        if options.get('runtime_aware', False):
            scores *= _runtime_weights(state, U)
        order = np.argsort(-scores)[:max(1, int(options.get('n_polish', 4)))]
        candidates = []
        for index in order:
            u0 = U[index]
            def negative_acquisition(u):
                u = np.clip(u, 0, 1)[None, :]
                mu_u, sd_u = gp.predict(u)
                score = expected_improvement(mu_u, sd_u, best, xi)
                if options.get('runtime_aware', False):
                    score *= _runtime_weights(state, u)
                return -float(score[0])
            result = minimize(negative_acquisition, u0, method='L-BFGS-B',
                              bounds=[(0.0, 1.0)] * d, options={'maxiter': 60})
            u, score = np.clip(result.x, 0, 1), -float(result.fun)
            if not _feasible(from_unit(u, space), space):
                u, score = u0, float(scores[index])
            candidates.extend([(score, u, sources[index], True, u0),
                               (float(scores[index]), u0, sources[index], False, u0)])
        candidates.sort(key=lambda item: -item[0])
        chosen = next(((score, u, from_unit(u, space), source, polished, u0)
                       for score, u, source, polished, u0 in candidates
                       if not _duplicate(from_unit(u, space), state, space, out)), None)
        if chosen is None:
            point = _draw_points(state, space, 1, out)[0]
            chosen = 0.0, to_unit(point, space), point, 'random', False, to_unit(point, space)
        score, u, point, source, polished, u0 = chosen
        origin = {}
        if source == 'llm':
            origin = {'seed_values': from_unit(u0, space)}
            for original, rationale in state.get('_llm_rationales', []):
                if np.array_equal(to_unit(original, space), u0):
                    origin['rationale'] = rationale
            for original, source_ids in state.get('_llm_sources', []):
                if np.array_equal(to_unit(original, space), u0):
                    origin['source_ids'] = source_ids
        _tag(state, space, point, 'llm_gp_ei' if source == 'llm' else 'gp_ei',
             acquisition=float(score), gp_fit=state['gp_fits'], pool=state['last_pool'].copy(),
             acquisition_units=('response' if options.get('transform') == 'identity' else 'log_response')
                                + ('_per_s' if options.get('runtime_aware') else ''),
             candidate_origin=source, polished=polished, n_observations_at_proposal=n_observed, **origin)
        out.append(point)
        mu, _ = gp.predict(u[None, :])
        gp.add_fantasy(u, float(mu[0]), median_noise)
    return out


def _cma_points(state, space, count):
    options = state['options']
    if '_cma' not in state:
        state['_cma'] = CMAES(len(space['names']), options)
    cma = state['_cma']
    rng, d = state['rng'], cma.d
    out, A = [], None
    for _ in range(count):
        if len(cma.points) >= cma.lam:
            if options.get('synchronous', True):
                break
            for point in _draw_points(state, space, 1, out):
                out.append(_tag(state, space, point, 'random_filler',
                                reason='generation awaits results', optimizer_generation=cma.generation))
            continue
        every = int(options.get('reeval_every', 6))
        if every and cma.generation > 0 and not cma.points and cma.generation % every == 0 and state['values']:
            incumbent = state['points'][int(np.argmin(state['values']))]
            u = np.clip(to_unit(incumbent, space) + rng.normal(0, 1e-4, d), 0, 1)
            point = from_unit(u, space)
            source = 'cma_incumbent_reeval'
        else:
            if A is None:
                try:
                    A = np.linalg.cholesky(cma.Cov + 1e-12 * np.eye(d))
                except np.linalg.LinAlgError:
                    cma.Cov = A = np.eye(d)
            point = None
            for _ in range(int(options.get('max_resample', 500))):
                u = np.clip(cma.mean + cma.sigma * (A @ rng.standard_normal(d)), 0, 1)
                trial = from_unit(u, space)
                if _feasible(trial, space) and not _duplicate(trial, state, space, out):
                    point = trial
                    break
            if point is None:
                point = _draw_points(state, space, 1, out)[0]
                u = to_unit(point, space)
            source = 'cma_generation'
        _tag(state, space, point, source, optimizer_generation=cma.generation,
             population_index=len(cma.points), popsize=cma.lam,
             sigma=float(cma.sigma), restarts=cma.restarts)
        cma.points.append((state['_dispatch'][id(point)][0], to_unit(point, space)))
        out.append(point)
    return out

def _tpe_points(state, space, count):
    try:
        import optuna
    except ImportError as exc:
        state['unavailable'] = 'tpe_optuna requires optuna'
        raise RuntimeError(state['unavailable']) from exc
    if '_study' not in state:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        state['_study'] = optuna.create_study(direction='minimize',
            sampler=optuna.samplers.TPESampler(seed=state['seed'], multivariate=True))
        state['tpe_trials'] = {}
    study = state['_study']
    out = []
    for _ in range(count * 40):
        trial = study.ask()
        point = from_unit(np.array([trial.suggest_float(name, 0, 1)
                                    for name in space['names']]), space)
        if _feasible(point, space) and not _duplicate(point, state, space, out):
            out.append(_tag(state, space, point, 'tpe_optuna', trial_number=trial.number))
            state['tpe_trials'][str(state['_dispatch'][id(point)][0])] = trial.number
            if len(out) == count:
                break
        else:
            study.tell(trial, state=optuna.trial.TrialState.PRUNED)
    if len(out) < count:
        out += [_tag(state, space, p, 'random_filler', reason='TPE points failed the gates')
                for p in _draw_points(state, space, count - len(out), out)]
    return out


def ollama_proposals(state, space, count, options=None):
    """A bounded Ollama loop proposes x; only transport supplies measured J."""
    import hashlib
    import os
    import time
    import urllib.request
    options = options or state['options']
    host = options.get('host', os.environ.get('OLLAMA_HOST', 'http://localhost:11434')).rstrip('/')
    model = options.get('model', os.environ.get('STAGE4_LLM_MODEL', 'qwen3:32b'))
    stats = state.setdefault('llm_stats', {})
    for name in ('calls', 'proposed', 'clipped', 'infeasible', 'malformed', 'accepted',
                 'tools', 'repairs', 'fallbacks'):
        stats.setdefault(name, 0)
    try:
        with urllib.request.urlopen(host + '/api/tags', timeout=5) as response:
            models = {row['name']: row for row in json.load(response).get('models', [])}
        if model not in models:
            raise RuntimeError(f'Ollama model {model!r} is absent at {host}')
        digest = models[model].get('digest')
        if options.get('model_digest') and options['model_digest'] != digest:
            raise RuntimeError('Ollama model digest differs from the declared weights')
    except (OSError, ValueError, RuntimeError) as exc:
        state['unavailable'] = f'Ollama unavailable: {exc}'
        raise RuntimeError(state['unavailable']) from exc
    state.pop('unavailable', None)
    sources = options.get('source_records', {})
    if not isinstance(sources, dict):
        raise ValueError('source_records must map fixed source IDs to permitted extracts')
    best = sorted(range(len(state['values'])), key=state['values'].__getitem__)[:8]
    indices = sorted(set(best + list(range(max(0, len(state['values']) - 8), len(state['values'])))))
    measured = [{'point': state['points'][i], 'J': state['values'][i], 'transport_se': state['se'][i],
                 'proposal_id': state['observation_ids'][i]} for i in indices]
    objective = options.get('objective', 'device_junction_qps_per_eV')
    definition = ('J=E_position,transport[sum_e w_e Q_e]/E_g; sum_e w_e=1; '
                  'Q_e is junction QPs/primary; E_g=0.010 eV; J is weighted junction QPs/injected-eV.')
    if objective in ('total_qps_per_primary', 'total_QPs_per_primary', 'junction_qps_per_primary'):
        definition = 'J=E_position,transport[sum_e Q_e]; Q_e is junction QPs/primary; J is summed junction QPs/primary.'
    elif objective == 'integrated_density':
        definition = 'J is the recorded time-integrated quasiparticle density; retain the units specified in the supplied source records.'
    prompt = {
        'request': f'Propose {count} distinct physical vectors. Never invent a measured response.',
        'objective': objective, 'definition': definition,
        'variables': [{'name': name, 'low': float(low), 'high': float(high), 'scale': scale,
                       'unit': options.get('units', {}).get(name, 'see frozen physical sources')}
                      for name, low, high, scale in zip(space['names'], space['low'], space['high'], space['scales'])],
        'baseline': space['baseline'], 'constraints': space.get('constraints', {}),
        'measured': measured, 'failed': state['rejected'][-8:],
        'execution': state.get('attempts', [])[-16:], 'sources': sources,
        'physics': 'Born stability: C11>C12, C11+2*C12>0, C44>0. '
                   'Rates are scat*nu^4 and decay*nu^5; nu=E/h in Hz. '
                   'Elasticity and crystal direction affect focusing. Film pair breaking and boundary '
                   'absorption compete with junction absorption. Missing rates remain unknown.',
        'source_rule': 'Cite only the supplied source IDs; use [] for a numerical hypothesis without a source.'}
    values_schema = {'type': 'object', 'additionalProperties': False, 'required': space['names'],
                     'properties': {name: {'type': 'number', 'minimum': float(lo), 'maximum': float(hi)}
                                    for name, lo, hi in zip(space['names'], space['low'], space['high'])}}
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['candidates'],
              'properties': {'candidates': {'type': 'array', 'maxItems': count, 'items': {
                  'type': 'object', 'additionalProperties': False, 'required': ['values', 'rationale', 'source_ids'],
                  'properties': {'values': values_schema, 'rationale': {'type': 'string', 'maxLength': 200},
                                 'source_ids': {'type': 'array', 'items': {'type': 'string'}}}}}}}
    tools = [
        {'type': 'function', 'function': {'name': 'read_source', 'description': 'Read one permitted physical source.',
             'parameters': {'type': 'object', 'properties': {'source_id': {'type': 'string', 'enum': list(sources)}},
                            'required': ['source_id'], 'additionalProperties': False}}},
        {'type': 'function', 'function': {'name': 'check_physics', 'description': 'Check bounds, units and elastic/interface validity.',
             'parameters': {'type': 'object', 'properties': {'values': values_schema},
                            'required': ['values'], 'additionalProperties': False}}},
        {'type': 'function', 'function': {'name': 'summarize_measurements', 'description': 'Read permitted measured training points.',
             'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
    messages = [{'role': 'system', 'content': 'Propose physical inputs using measured training data and supplied sources. '
                 'Do not claim unmeasured response, material identity or missing properties. Return the requested JSON.'},
                {'role': 'user', 'content': json.dumps(prompt, allow_nan=False)}]
    events = state.setdefault('llm_records', [])
    allowed_tools = min(2, max(0, int(options.get('max_tool_calls', 2)))) if options.get('agent_tools') else 0
    out, errors, tool_count, repairs = [], [], 0, 0
    max_repairs = min(1, max(0, int(options.get('retries', 2)) - 1))
    for step in range(allowed_tools + max_repairs + 1):
        use_tools = tool_count < allowed_tools
        payload = {'model': model, 'stream': False, 'messages': messages,
                   'options': {'temperature': float(options.get('temperature', 0.7)),
                               'seed': state['seed'] * 1000 + state['iteration'] * 10 + step,
                               'num_ctx': int(options.get('num_ctx', 16384)),
                               'num_predict': int(options.get('num_predict', 4096)),
                               'num_thread': int(options.get('num_thread', 8))},
                   'keep_alive': options.get('keep_alive', '10m')}
        if use_tools:
            payload['tools'] = tools
        else:
            payload['format'] = schema
        payload['think'] = options.get('think', True)
        request = urllib.request.Request(host + '/api/chat', data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=float(options.get('timeout', 180))) as response:
                result = json.load(response)
            message = result.get('message', {})
            if not isinstance(message, dict):
                raise ValueError('Ollama message must be an object')
            stats['calls'] += 1
            event = {'model': model, 'digest': digest, 'iteration': state['iteration'],
                     'elapsed_s': time.perf_counter() - started, 'tools_enabled': use_tools,
                     'prompt_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                     'prompt_eval_count': result.get('prompt_eval_count'), 'eval_count': result.get('eval_count'),
                     'total_duration_ns': result.get('total_duration'), 'content': message.get('content', '')}
            events.append(event)
            calls = message.get('tool_calls', [])
            if calls:
                if not use_tools or not isinstance(calls, list) or len(calls) > allowed_tools - tool_count:
                    raise ValueError('tool request exceeds the permitted tools or count')
                messages.append({'role': 'assistant', 'content': message.get('content', ''), 'tool_calls': calls})
                event['tool_results'] = []
                for call in calls:
                    function = call['function']; name = function['name']; arguments = function.get('arguments', {})
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    try:
                        if name == 'read_source' and set(arguments) == {'source_id'}:
                            answer = {'source_id': arguments['source_id'], 'record': sources[arguments['source_id']]}
                        elif name == 'summarize_measurements' and not arguments:
                            answer = {'measured': measured, 'failed': state['rejected'][-8:]}
                        elif name == 'check_physics' and set(arguments) == {'values'}:
                            values = arguments['values']
                            if set(values) != set(space['names']):
                                raise ValueError('checker requires every searched coordinate and no additional field')
                            checked = dict(space['baseline']); checked.update(values)
                            answer = {'valid': True, 'derived': validate_values(checked, space, exact=True)}
                        else:
                            raise ValueError('unknown tool or invalid arguments')
                    except (ValueError, TypeError, KeyError) as exc:
                        answer = {'valid': False, 'error': str(exc)}
                    tool_count += 1; stats['tools'] += 1
                    event['tool_results'].append({'name': name, 'arguments': arguments, 'answer': answer})
                    messages.append({'role': 'tool', 'tool_name': name, 'content': json.dumps(answer)})
                messages.append({'role': 'user', 'content': 'Use the tool answers; return final candidates now or request a remaining permitted check.'})
                continue
            if use_tools:
                messages.append({'role': 'assistant', 'content': message.get('content', '')})
                messages.append({'role': 'user', 'content': 'Return the final candidates using the required JSON schema.'})
                tool_count = allowed_tools
                continue
            reply = json.loads(message.get('content', ''))
            if set(reply) != {'candidates'} or not isinstance(reply['candidates'], list):
                raise ValueError('reply must contain only a candidates list')
            errors = []
            for item in reply['candidates']:
                stats['proposed'] += 1
                try:
                    if set(item) != {'values', 'rationale', 'source_ids'}:
                        raise ValueError('candidate requires values, rationale and source_ids only')
                    values = item['values']
                    if set(values) != set(space['names']):
                        raise ValueError('candidate must contain every searched coordinate and no additional field')
                    source_ids = item['source_ids']
                    if (not isinstance(source_ids, list) or any(not isinstance(sid, str) or sid not in sources for sid in source_ids)):
                        raise ValueError('candidate cites an unknown source ID')
                    if not isinstance(item['rationale'], str) or len(item['rationale']) > 200:
                        raise ValueError('rationale must be a sentence of at most 200 characters')
                    point = dict(space['baseline']); point.update(values)
                    validate_values(point, space, exact=True)
                    if _duplicate(point, state, space, out):
                        raise ValueError('candidate duplicates an observed or pending vector')
                    out.append(point)
                    state.setdefault('_llm_rationales', []).append((point, item['rationale']))
                    state.setdefault('_llm_sources', []).append((point, list(source_ids)))
                    if len(out) == count:
                        break
                except (KeyError, ValueError, TypeError) as exc:
                    stats['infeasible'] += 1
                    errors.append(str(exc))
            if len(out) == count:
                break
            if not errors:
                errors = [f'Only {len(out)} valid distinct proposals; {count} requested']
        except (ValueError, KeyError, TypeError) as exc:
            stats['malformed'] += 1; errors = [str(exc)]
        except OSError as exc:
            state['unavailable'] = f'Ollama request failed: {exc}'
            events.append({'model': model, 'iteration': state['iteration'], 'error': state['unavailable'],
                           'elapsed_s': time.perf_counter() - started})
            break
        if repairs >= max_repairs:
            break
        repairs += 1; stats['repairs'] += 1
        tool_count = allowed_tools
        messages.append({'role': 'user', 'content': json.dumps({'errors': errors, 'request': 'Repair once and return final candidate JSON.'})})
    stats['accepted'] += len(out)
    state['llm_model'], state['llm_host'], state['llm_digest'] = model, host, digest
    if errors:
        state.setdefault('llm_rejected', []).append({'iteration': state['iteration'], 'reasons': errors})
    return out

def propose(method, state, space, count=1, options=None):
    if method != state['method']:
        raise ValueError('search state belongs to another method')
    if not isinstance(count, int) or count < 1:
        raise ValueError('proposal count must be a positive integer')
    if options:
        state['options'].update(options)
    if state.get('algorithm_version', 2) < 3 and method in ('bo_gp', 'llm_bo', 'llm_agent'):
        raise ValueError('older GP/LLM checkpoints use a different proposal pool; start a new comparison')
    if 'space' in state:
        old = state['space']
        for name in ('names', 'low', 'high', 'scales', 'baseline', 'constraints',
                     'density_kg_m3', 'junction_gap_eV', 'gun_energy_eV', 'cutoff_eV',
                     'ground_plane_active_absorber'):
            if (json.dumps(old.get(name), sort_keys=True, default=lambda x: np.asarray(x).tolist())
                    != json.dumps(space.get(name), sort_keys=True, default=lambda x: np.asarray(x).tolist())):
                raise ValueError(f'search parameter space changed: {name}')
    state['space'] = copy.deepcopy(space)
    state['iteration'] += 1
    state['_dispatch'] = {}
    state.pop('_llm_rationales', None)
    state.pop('_llm_sources', None)
    if method == 'random':
        points = [_tag(state, space, p, 'random_baseline') for p in _draw_points(state, space, count)]
    elif method == 'sobol':
        points = _draw_points(state, space, count, sobol=True)
    elif method == 'bo_gp':
        points = _gp_points(state, space, count)
    elif method == 'cmaes':
        points = _cma_points(state, space, count)
    elif method == 'tpe_optuna':
        points = _tpe_points(state, space, count)
    elif method in ('llm_agent', 'llm_bo'):
        if method == 'llm_bo':
            if len(state['Y']) < int(state['options'].get('n_init', 24)):
                points = _gp_points(state, space, count)
            else:
                every = max(1, int(state['options'].get('llm_every', 4)))
                observed = len(state['Y'])
                if observed - state.get('llm_at_observations', -every) >= every:
                    wanted = int(state['options'].get('extra_candidates', 8))
                    try:
                        supplied = ollama_proposals(state, space, wanted)
                    except RuntimeError as exc:
                        supplied = []
                        state.setdefault('llm_rejected', []).append({'iteration': state['iteration'], 'reasons': [str(exc)]})
                    state['llm_pool'] = [
                        {'point': p,
                         'rationale': next((reason for original, reason in state.get('_llm_rationales', []) if original == p), ''),
                         'source_ids': next((ids for original, ids in state.get('_llm_sources', []) if original == p), [])}
                        for p in supplied]
                    state['llm_at_observations'] = observed
                    state.setdefault('llm_stats', {}).setdefault('fallbacks', 0)
                    state['llm_stats']['fallbacks'] += wanted - len(supplied)
                valid = [row for row in state.get('llm_pool', [])
                         if not _duplicate(row['point'], state, space)]
                state['_llm_rationales'] = [(row['point'], row['rationale']) for row in valid]
                state['_llm_sources'] = [(row['point'], row['source_ids']) for row in valid]
                points = _gp_points(state, space, count, pool=[row['point'] for row in valid])
        else:
            points = [_tag(state, space, p, 'llm') for p in ollama_proposals(state, space, count)]
        if len(points) < count:
            state['incomplete_proposal'] = f'Ollama returned {len(points)}/{count} valid distinct proposals'
    else:
        raise ValueError(f'unknown search method {method!r}')
    state['pending_ids'].extend(state['_dispatch'][id(point)][0] for point in points)
    state['pending'].extend(points)
    state.pop('_dispatch')
    state.pop('_llm_rationales', None)
    state.pop('_llm_sources', None)
    return points


def update(method, state, point, value, se=None, rejected_reason=None, proposal_id=None,
           elapsed_s=None, status=None):
    if method != state['method']:
        raise ValueError('search state belongs to another method')
    space = state['space']
    identifier = pending_id(state, point, space) if proposal_id is None else proposal_id
    if (not isinstance(identifier, int) or isinstance(identifier, bool)
            or identifier not in state['pending_ids']):
        raise ValueError('observation must name a pending integer proposal_id')
    if not np.array_equal(to_unit(point, space), state['proposal_units'][identifier]):
        raise ValueError('observation values differ from the named pending proposal')
    key = str(identifier)
    shared = state['proposals'][key]['proposal_source'] == 'shared_initial'
    if elapsed_s is not None and (not math.isfinite(float(elapsed_s)) or float(elapsed_s) < 0):
        raise ValueError('elapsed_s must be finite and nonnegative')
    previous_best = min(state['values'], default=math.inf)
    if rejected_reason is None:
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError('yield must be finite and nonnegative; use rejected_reason for an unmeasured trial')
        if se is not None and (not math.isfinite(float(se)) or float(se) < 0):
            raise ValueError('standard error must be finite and nonnegative, or absent')
        options = state['options']
        floor = float(options.get('floor', 1e-12))
        if floor <= 0:
            raise ValueError('logarithm floor must be positive')
        transform = options.get('transform', 'log')
        if transform not in ('log', 'identity'):
            raise ValueError('response transform must be log or identity')
        identity = transform == 'identity'
        # Log response: y=log(max(J,J_floor)), s_y approximately SE(J)/max(J,J_floor).
        y = float(value) if identity else math.log(max(float(value), floor))
        sigma = (float(se) if identity else float(se) / max(float(value), floor)) if se is not None else None
        if sigma is not None:
            sigma = max(float(options.get('noise_floor', 1e-6)), sigma)
        state['U'].append(to_unit(point, space))
        state['Y'].append(y)
        state['SIG'].append(sigma)
        state['points'].append(dict(point))
        state['values'].append(float(value))
        state['se'].append(float(se) if se is not None else None)
        state['observation_ids'].append(identifier)
        state['used'][key] = method in ('bo_gp', 'llm_bo', 'tpe_optuna')
        if method == 'cmaes' and not shared:
            state['_cma'].results[identifier] = y
    else:
        state['rejected'].append({'proposal_id': identifier, 'point': dict(point), 'reason': str(rejected_reason),
                                  'iteration': state['iteration']})
        state['used'][key] = False
        if method == 'cmaes' and not shared:
            cma = state['_cma']
            cma.points = [p for p in cma.points if p[0] != identifier]
            cma.results.pop(identifier, None)
    pending_index = state['pending_ids'].index(identifier)
    state['pending_ids'].pop(pending_index)
    state['pending'].pop(pending_index)
    state.setdefault('attempts', []).append({'proposal_id': identifier, 'u': to_unit(point, space).tolist(),
        'elapsed_s': float(elapsed_s) if elapsed_s is not None else None,
        'status': 'complete' if rejected_reason is None else
                  ('timeout' if 'timeout' in (str(status) + ' ' + str(rejected_reason)).lower()
                   else str(status or 'failed'))})
    initial = shared or state['proposals'][key]['proposal_source'].endswith('_init')
    if rejected_reason is None and not initial and state['options'].get('local_adapt', False):
        gained = float(value) < previous_best * (1 - float(state['options'].get('improve_rel', 0.001)))
        state['local_successes'] = state.get('local_successes', 0) + 1 if gained else 0
        state['local_failures'] = 0 if gained else state.get('local_failures', 0) + 1
        width = float(state.get('local_width', state['options'].get('local_width', 0.08)))
        if state['local_successes'] >= 3 or state['local_failures'] >= 4:
            factor = 2.0 if state['local_successes'] >= 3 else 0.5
            state['local_width'] = float(np.clip(width * factor, 0.01, 0.4))
            state['local_successes'] = state['local_failures'] = 0
    if method == 'cmaes' and not shared:
        cma = state['_cma']
        used = cma.update()
        for used_key in used:
            state['used'][str(used_key)] = True
        if used and state['Y']:
            best = min(state['Y'])
            if cma.best_at_restart is None or best < cma.best_at_restart - 1e-9:
                cma.best_at_restart, cma.gens_without_gain = best, 0
            else:
                cma.gens_without_gain += 1
            if cma.sigma < cma.sigma_min or cma.gens_without_gain >= cma.stagnation:
                incumbent = state['points'][int(np.argmin(state['values']))]
                cma.reset(min(cma.max_popsize, cma.lam * 2))
                cma.mean = np.clip(to_unit(incumbent, space), 0.05, 0.95)
                cma.restarts += 1
                cma.gens_without_gain = 0
    if method == 'tpe_optuna' and key in state.get('tpe_trials', {}):
        import optuna
        number = state['tpe_trials'].pop(key)
        if rejected_reason is None:
            state['_study'].tell(number, state['Y'][-1])
        else:
            state['_study'].tell(number, state=optuna.trial.TrialState.PRUNED)


def save_state(state, path=None):
    """Save fitted arrays, evolution paths, pending points, and random state."""
    numerical = ('rng', '_gp', '_cma', '_sobol', '_study')
    record = {'schema': 'material-search-2',
              'state': {key: value for key, value in state.items() if key not in numerical},
              'rng': state['rng'].bit_generator.state}
    for name in ('_gp', '_cma'):
        if name in state:
            record[name] = vars(state[name])
    if '_study' in state:
        import optuna
        study = state['_study']
        record['tpe'] = {
            'trials': [{'state': trial.state.name, 'params': trial.params, 'values': trial.values,
                        'distributions': {name: optuna.distributions.distribution_to_json(dist)
                                          for name, dist in trial.distributions.items()}}
                       for trial in study.trials],
            'rng': study.sampler._rng.rng.get_state(),
            'random_rng': study.sampler._random_sampler._rng.rng.get_state()}
    raw = json.dumps(record, sort_keys=True, allow_nan=False, separators=(',', ':'),
                     default=lambda value: np.asarray(value).tolist())
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(raw + '\n')
        temporary.replace(path)
    return json.loads(raw)


def load_state(record_or_path):
    if isinstance(record_or_path, (str, Path)):
        record = json.loads(Path(record_or_path).read_text())
    else:
        record = record_or_path
    if record.get('schema') == 'material-search-1':
        raise ValueError('old search checkpoint uses rounded physical keys; restart with integer proposal IDs')
    if record.get('schema') != 'material-search-2':
        raise ValueError('unsupported saved search state')
    state = copy.deepcopy(record['state'])
    generator = np.random.Generator(getattr(np.random, record['rng']['bit_generator'])())
    generator.bit_generator.state = record['rng']
    state['rng'] = generator
    for name, cls, arrays in (
            ('_gp', GaussianProcess, ('theta', 'X', 'y', 'noise', 'L', 'alpha')),
            ('_cma', CMAES, ('w', 'initial_mean', 'mean', 'pc', 'ps', 'Cov'))):
        if name not in record:
            continue
        numerical = cls.__new__(cls)
        numerical.__dict__.update(copy.deepcopy(record[name]))
        if name == '_cma' and not hasattr(numerical, 'initial_mean'):
            numerical.initial_mean = np.full(numerical.d, 0.5)
        for field in arrays:
            if getattr(numerical, field, None) is not None:
                setattr(numerical, field, np.asarray(getattr(numerical, field), dtype=float))
        if name == '_gp' and numerical.sphere_columns is not None:
            numerical.sphere_columns = tuple(numerical.sphere_columns)
        if name == '_cma':
            numerical.points = [(int(key), np.asarray(u)) for key, u in numerical.points]
            numerical.results = {int(key): value for key, value in numerical.results.items()}
        state[name] = numerical
    if 'tpe' in record:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction='minimize', sampler=optuna.samplers.TPESampler(multivariate=True))
        for trial in record['tpe']['trials']:
            study.add_trial(optuna.trial.create_trial(
                state=getattr(optuna.trial.TrialState, trial['state']), params=trial['params'],
                values=trial['values'], distributions={name: optuna.distributions.json_to_distribution(dist)
                                                      for name, dist in trial['distributions'].items()}))
        for field, sampler in (('rng', study.sampler), ('random_rng', study.sampler._random_sampler)):
            rng = record['tpe'][field]
            sampler._rng.rng.set_state((rng[0], np.asarray(rng[1], dtype=np.uint32), *rng[2:]))
        state['_study'] = study
    if state['method'] not in ('random', 'sobol', 'bo_gp', 'cmaes', 'tpe_optuna', 'llm_agent', 'llm_bo'):
        raise ValueError('unknown saved search method')
    if (len(state['pending_ids']) != len(state['pending'])
            or len(set(state['pending_ids'])) != len(state['pending_ids'])
            or state['next_proposal_id'] != len(state['proposal_units'])
            or len(state['observation_ids']) != len(state['Y'])):
        raise ValueError('saved search proposal IDs are inconsistent')
    for identifier, point in zip(state['pending_ids'], state['pending']):
        if (not isinstance(identifier, int) or isinstance(identifier, bool)
                or not 0 <= identifier < state['next_proposal_id']
                or str(identifier) not in state['proposals']
                or not np.array_equal(to_unit(point, state['space']), state['proposal_units'][identifier])):
            raise ValueError('saved pending proposal is inconsistent')
    return state
