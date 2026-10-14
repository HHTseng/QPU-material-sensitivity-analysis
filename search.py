"""Search in normalized parameter coordinates, with measured observation errors."""

import copy
import json
import math
from pathlib import Path

import numpy as np
from scipy.linalg import cho_solve, solve_triangular
from scipy.optimize import minimize
from scipy.stats import norm, qmc

from inputs import from_unit, to_unit, validate_values


class GaussianProcess:
    """Matern-5/2 Gaussian process; noise_var is the measured variance."""

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
    """CMA-ES mean, covariance, evolution paths, and restart parameters."""

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
        self.mean = np.full(d, 0.5)
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
    """Expected decrease below best for a normally distributed prediction."""
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
            'rejected': [], 'proposals': {}, 'used': {}, 'sobol_draws': 0,
            'design_cursor': 0, 'gp_fits': 0}


def point_key(point, space):
    return json.dumps([round(float(point[name]), 12) for name in space['names']],
                      separators=(',', ':'))


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


def _random_points(state, space, count, extra=()):
    out = []
    for _ in range(int(state['options'].get('max_resample', 500)) * max(1, count)):
        point = from_unit(state['rng'].random(len(space['names'])), space)
        if _feasible(point, space) and not _duplicate(point, state, space, [*extra, *out]):
            out.append(point)
            if len(out) == count:
                return out
    raise RuntimeError(f'could not draw {count} distinct feasible points')


def _tag(state, space, point, source, **details):
    state['proposals'][point_key(point, space)] = {
        'proposal_source': source, 'optimizer': state['method'],
        'iteration': state['iteration'], **details}
    return point


def _sobol_points(state, space, count):
    if '_sobol' not in state:
        state['_sobol'] = qmc.Sobol(d=len(space['names']), scramble=True, seed=state['seed'])
        if state['sobol_draws']:
            state['_sobol'].fast_forward(state['sobol_draws'])
    out = []
    for _ in range(64 * max(1, count)):
        u = state['_sobol'].random(1)[0]
        state['sobol_draws'] += 1
        point = from_unit(u, space)
        if _feasible(point, space) and not _duplicate(point, state, space, out):
            out.append(_tag(state, space, point, 'sobol_init',
                            design_index=state['design_cursor']))
            state['design_cursor'] += 1
            if len(out) == count:
                return out
    for point in _random_points(state, space, count - len(out), out):
        out.append(_tag(state, space, point, 'random_filler', reason='Sobol points failed the gates'))
    return out


def _fit_gp(state, space):
    names = space['names']
    polar, azimuth = 'orientation_polar_cos', 'orientation_azimuth_turns'
    sphere = (names.index(polar), names.index(azimuth)) if polar in names and azimuth in names else None
    state['_gp'] = GaussianProcess(sphere_columns=sphere).fit(
        np.array(state['U']), np.array(state['Y']),
        noise_var=np.array(state['SIG'], dtype=float) ** 2, rng=state['rng'],
        n_restarts=int(state['options'].get('n_restarts', 3)))
    state['gp_fits'] += 1
    state['gp_fit_observations'] = len(state['Y'])
    return state['_gp']


def _gp_points(state, space, count, pool=None):
    options = state['options']
    n_init = int(options.get('n_init', 24))
    n_observed = len(state['Y'])
    dispatched = n_observed + sum(
        state['proposals'][point_key(p, space)]['proposal_source'].endswith('_init')
        for p in state['pending'])
    if dispatched < n_init:
        take = min(count, n_init - dispatched)
        out = _sobol_points(state, space, take)
        for i, point in enumerate(out):
            _tag(state, space, point, 'sobol_init', design_index=dispatched + i)
        if take < count:
            out += [_tag(state, space, p, 'random_filler', reason='initial design finished')
                    for p in _random_points(state, space, count - take, out)]
        return out
    if n_observed < min(n_init, int(options.get('n_min_fit', n_init))):
        return []
    refit_every = max(1, int(options.get('refit_every', 5)))
    if ('_gp' not in state or (n_observed % refit_every == 0
                              and state.get('gp_fit_observations') != n_observed)):
        gp = _fit_gp(state, space)
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
    if pool is not None:
        pool = list(pool)
    for _ in range(count):
        mu_observed, _ = gp.predict(np.array(state['U']))
        best = float(np.min(mu_observed))
        if pool is None:
            n = int(options.get('n_candidates', 4096))
            engine = qmc.Sobol(d=d, scramble=True, seed=int(state['rng'].integers(1 << 31)))
            U = engine.random(n)
            k = n // 4
            incumbent = np.array(state['U'][int(np.argmin(mu_observed))])
            local = np.clip(incumbent[None, :] + state['rng'].normal(0, 0.08, size=(k, d)), 0, 1)
            U = np.vstack([U[:n - k], local])
            U = np.array([u for u in U if _feasible(from_unit(u, space), space)])
        else:
            U = np.array([to_unit(p, space) for p in pool
                          if not _duplicate(p, state, space, out)])
        candidates = []
        if U.size:
            mu, sd = gp.predict(U)
            ei = expected_improvement(mu, sd, best, xi)
            order = np.argsort(-ei)[:max(1, int(options.get('n_polish', 4)))]
            for index in order:
                u0 = U[index]
                if pool is None:
                    def negative_ei(u):
                        mu_u, sd_u = gp.predict(np.clip(u, 0, 1)[None, :])
                        return -float(expected_improvement(mu_u, sd_u, best, xi)[0])
                    result = minimize(negative_ei, u0, method='L-BFGS-B',
                                      bounds=[(0.0, 1.0)] * d, options={'maxiter': 60})
                    u = np.clip(result.x, 0, 1)
                    score = -float(result.fun)
                    if not _feasible(from_unit(u, space), space):
                        u, score = u0, -negative_ei(u0)
                else:
                    u, score = u0, float(ei[index])
                candidates.append((score, u))
        candidates.sort(key=lambda pair: -pair[0])
        chosen = None
        for score, u in candidates:
            point = from_unit(u, space)
            if not _duplicate(point, state, space, out):
                chosen = score, u, point
                break
        if chosen is None:
            point = _random_points(state, space, 1, out)[0]
            chosen = 0.0, to_unit(point, space), point
            _tag(state, space, point, 'random_filler', reason='no distinct acquisition maximum')
        else:
            _tag(state, space, chosen[2], 'llm_gp_ei' if pool is not None else 'gp_ei',
                 acquisition=float(chosen[0]), gp_fit=state['gp_fits'],
                 n_observations_at_proposal=n_observed)
        score, u, point = chosen
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
            for point in _random_points(state, space, 1, out):
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
                point = _random_points(state, space, 1, out)[0]
                u = to_unit(point, space)
            source = 'cma_generation'
        _tag(state, space, point, source, optimizer_generation=cma.generation,
             population_index=len(cma.points), popsize=cma.lam,
             sigma=float(cma.sigma), restarts=cma.restarts)
        cma.points.append((point_key(point, space), to_unit(point, space)))
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
            state['tpe_trials'][point_key(point, space)] = trial.number
            out.append(_tag(state, space, point, 'tpe_optuna', trial_number=trial.number))
            if len(out) == count:
                break
        else:
            study.tell(trial, state=optuna.trial.TrialState.PRUNED)
    if len(out) < count:
        out += [_tag(state, space, p, 'random_filler', reason='TPE points failed the gates')
                for p in _random_points(state, space, count - len(out), out)]
    return out


def ollama_proposals(state, space, count, options=None):
    """Read proposals only; measured yields always come from the simulation."""
    import os
    import urllib.error
    import urllib.request
    options = options or state['options']
    host = options.get('host', os.environ.get('OLLAMA_HOST', 'http://localhost:11434')).rstrip('/')
    model = options.get('model', os.environ.get('STAGE4_LLM_MODEL', 'qwen3:32b'))
    stats = state.setdefault('llm_stats', {'calls': 0, 'proposed': 0, 'clipped': 0,
                                         'infeasible': 0, 'malformed': 0, 'accepted': 0})
    try:
        with urllib.request.urlopen(host + '/api/tags', timeout=5) as response:
            models = [row['name'] for row in json.load(response).get('models', [])]
        if model not in models:
            raise RuntimeError(f'Ollama model {model!r} is absent at {host}')
    except (OSError, ValueError, RuntimeError) as exc:
        state['unavailable'] = f'Ollama unavailable: {exc}'
        raise RuntimeError(state['unavailable']) from exc
    history = sorted(zip(state['points'], state['values']), key=lambda pair: pair[1])[:8]
    prompt = {'request': f'Propose {count} distinct parameter vectors minimizing junction QPs per primary.',
              'variables': [{'name': name, 'low': float(low), 'high': float(high), 'scale': scale}
                            for name, low, high, scale in zip(space['names'], space['low'], space['high'], space['scales'])],
              'baseline': space['baseline'], 'constraints': space.get('constraints', {}),
              'measured': [{'point': p, 'value': value} for p, value in history],
              'physics': 'Born stability: C11>C12, C11+2*C12>0, C44>0. '
                         'Scattering rate is scat*omega^4; decay rate is decay*omega^5. '
                         'The elastic tensor and crystal direction affect phonon focusing. '
                         'Boundary absorption and film pair breaking compete with junction absorption. '
                         'Do not claim yields for unmeasured points.',
              'reply': {'candidates': [{'values': {name: 'number' for name in space['names']},
                                       'rationale': 'one physical sentence'}]}}
    out, errors = [], []
    for attempt in range(int(options.get('retries', 3))):
        payload = {'model': model, 'stream': False, 'format': 'json',
                   'messages': [{'role': 'system', 'content': 'Propose physical parameter vectors. Return JSON only.'},
                                {'role': 'user', 'content': json.dumps({**prompt, 'errors': errors})}],
                   'options': {'temperature': float(options.get('temperature', 0.7)),
                               'seed': state['seed'] * 1000 + state['iteration'] * 10 + attempt,
                               'num_ctx': int(options.get('num_ctx', 8192))}}
        request = urllib.request.Request(host + '/api/chat', data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=float(options.get('timeout', 180))) as response:
                raw = json.load(response).get('message', {}).get('content', '')
            stats['calls'] += 1
            reply = json.loads(raw)
            candidates = reply['candidates']
            if not isinstance(candidates, list):
                raise ValueError('candidates must be a list')
        except (ValueError, KeyError, TypeError) as exc:
            stats['malformed'] += 1
            errors = [str(exc)]
            continue
        except OSError as exc:
            state['unavailable'] = f'Ollama request failed: {exc}'
            if not out:
                raise RuntimeError(state['unavailable']) from exc
            break
        errors = []
        for item in candidates:
            stats['proposed'] += 1
            try:
                values = item['values']
                point = dict(space['baseline'])
                clipped = False
                for name, low, high in zip(space['names'], space['low'], space['high']):
                    value = float(values[name])
                    if not math.isfinite(value):
                        raise ValueError(f'{name} is not finite')
                    clipped |= not low <= value <= high
                    point[name] = float(np.clip(value, low, high))
                point = from_unit(to_unit(point, space), space)
                stats['clipped'] += int(clipped)
            except (KeyError, ValueError, TypeError) as exc:
                stats['malformed'] += 1
                errors.append(str(exc))
                continue
            if not _feasible(point, space):
                stats['infeasible'] += 1
                errors.append('physical gate rejected ' + json.dumps(values))
            elif not _duplicate(point, state, space, out):
                out.append(point)
                state.setdefault('rationales', {})[point_key(point, space)] = str(item.get('rationale', ''))[:200]
                if len(out) == count:
                    break
        if len(out) == count:
            break
    stats['accepted'] += len(out)
    state['llm_model'], state['llm_host'] = model, host
    return out


def propose(method, state, space, count=1, options=None):
    if method != state['method']:
        raise ValueError('search state belongs to another method')
    if not isinstance(count, int) or count < 1:
        raise ValueError('proposal count must be a positive integer')
    if options:
        state['options'].update(options)
    if 'space' in state:
        old = state['space']
        for name in ('names', 'low', 'high', 'scales', 'baseline', 'constraints',
                     'density_kg_m3', 'junction_gap_eV', 'gun_energy_eV', 'cutoff_eV',
                     'ground_plane_active_absorber'):
            if (json.dumps(_encode(old.get(name)), sort_keys=True)
                    != json.dumps(_encode(space.get(name)), sort_keys=True)):
                raise ValueError(f'search parameter space changed: {name}')
    state['space'] = copy.deepcopy(space)
    state['iteration'] += 1
    if method == 'random':
        points = [_tag(state, space, p, 'random_baseline') for p in _random_points(state, space, count)]
    elif method == 'sobol':
        points = _sobol_points(state, space, count)
    elif method == 'bo_gp':
        points = _gp_points(state, space, count)
    elif method == 'cmaes':
        points = _cma_points(state, space, count)
    elif method == 'tpe_optuna':
        points = _tpe_points(state, space, count)
    elif method in ('llm_agent', 'llm_bo'):
        factor = int(state['options'].get('pool_factor', 4)) if method == 'llm_bo' else 1
        points = ollama_proposals(state, space, count * factor)
        if method == 'llm_bo' and len(state['Y']) >= int(state['options'].get('n_init', 24)):
            points = _gp_points(state, space, count, pool=points)
        else:
            points = [_tag(state, space, p, 'llm_init' if method == 'llm_bo' else 'llm')
                      for p in points[:count]]
        if len(points) < count:
            state['incomplete_proposal'] = f'Ollama returned {len(points)}/{count} valid distinct proposals'
    else:
        raise ValueError(f'unknown search method {method!r}')
    state['pending'].extend(points)
    return points


def update(method, state, point, value, se=None, rejected_reason=None):
    if method != state['method']:
        raise ValueError('search state belongs to another method')
    space = state['space']
    key = point_key(point, space)
    if not any(point_key(p, space) == key for p in state['pending']):
        raise ValueError('observation must correspond to a pending proposal')
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
        y = float(value) if identity else math.log(max(float(value), floor))
        sigma = (float(se) if identity else float(se) / max(float(value), floor)) if se is not None else None
        state['U'].append(to_unit(point, space))
        state['Y'].append(y)
        state['SIG'].append(sigma)
        state['points'].append(dict(point))
        state['values'].append(float(value))
        state['se'].append(float(se) if se is not None else None)
        state['used'][key] = method in ('bo_gp', 'llm_bo', 'tpe_optuna')
        if method == 'cmaes':
            state['_cma'].results[key] = y
    else:
        state['rejected'].append({'point': dict(point), 'reason': str(rejected_reason),
                                  'iteration': state['iteration']})
        state['used'][key] = False
        if method == 'cmaes':
            cma = state['_cma']
            cma.points = [p for p in cma.points if p[0] != key]
            cma.results.pop(key, None)
    state['pending'] = [p for p in state['pending'] if point_key(p, space) != key]
    if method == 'cmaes':
        cma = state['_cma']
        used = cma.update()
        for used_key in used:
            state['used'][used_key] = True
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


def _encode(value):
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items() if key != '_sobol'}
    if isinstance(value, (list, tuple)):
        return {'type': 'tuple', 'data': [_encode(item) for item in value]} if isinstance(value, tuple) else [_encode(item) for item in value]
    if isinstance(value, np.ndarray):
        return {'type': 'array', 'dtype': str(value.dtype), 'data': value.tolist()}
    if isinstance(value, np.random.Generator):
        return {'type': 'rng', 'data': _encode(value.bit_generator.state)}
    if isinstance(value, (GaussianProcess, CMAES)):
        return {'type': type(value).__name__, 'data': _encode(vars(value))}
    if type(value).__module__.startswith('optuna'):
        import optuna
        trials = [{'state': trial.state.name, 'params': trial.params, 'values': trial.values,
                   'distributions': {name: optuna.distributions.distribution_to_json(dist)
                                     for name, dist in trial.distributions.items()}}
                  for trial in value.trials]
        return {'type': 'tpe', 'trials': trials,
                'rng': _encode(value.sampler._rng.rng.get_state()),
                'random_rng': _encode(value.sampler._random_sampler._rng.rng.get_state())}
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('saved search state contains a nonfinite number')
    return value


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get('type')
    if kind == 'array':
        return np.asarray(value['data'], dtype=value['dtype'])
    if kind == 'tuple':
        return tuple(_decode(item) for item in value['data'])
    if kind == 'rng':
        data = _decode(value['data'])
        generator = np.random.Generator(getattr(np.random, data['bit_generator'])())
        generator.bit_generator.state = data
        return generator
    if kind in ('GaussianProcess', 'CMAES'):
        cls = GaussianProcess if kind == 'GaussianProcess' else CMAES
        numerical = cls.__new__(cls)
        numerical.__dict__.update(_decode(value['data']))
        return numerical
    if kind == 'tpe':
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction='minimize', sampler=optuna.samplers.TPESampler(multivariate=True))
        for trial in value['trials']:
            study.add_trial(optuna.trial.create_trial(
                state=getattr(optuna.trial.TrialState, trial['state']),
                params=trial['params'], values=trial['values'],
                distributions={name: optuna.distributions.json_to_distribution(dist)
                               for name, dist in trial['distributions'].items()}))
        study.sampler._rng.rng.set_state(_decode(value['rng']))
        study.sampler._random_sampler._rng.rng.set_state(_decode(value['random_rng']))
        return study
    return {key: _decode(item) for key, item in value.items()}


def save_state(state, path=None):
    """Save fitted arrays, evolution paths, pending points, and random state."""
    record = {'schema': 'material-search-1', 'state': _encode(state)}
    raw = json.dumps(record, sort_keys=True, allow_nan=False, separators=(',', ':'))
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(raw + '\n')
        temporary.replace(path)
    return record


def load_state(record_or_path):
    if isinstance(record_or_path, (str, Path)):
        record = json.loads(Path(record_or_path).read_text())
    else:
        record = record_or_path
    if record.get('schema') != 'material-search-1':
        raise ValueError('unsupported saved search state')
    state = _decode(record['state'])
    if state['method'] not in ('random', 'sobol', 'bo_gp', 'cmaes', 'tpe_optuna', 'llm_agent', 'llm_bo'):
        raise ValueError('unknown saved search method')
    return state
