"""Stage 4 optimizer registry -- interchangeable search strategies.

Every optimizer implements the same ask/tell contract, so switching one for
another changes nothing else in the campaign and all of them can be compared on
identical axes (best-so-far objective versus cumulative simulated events):

    opt = create("bo_gp", space, seed=1)
    for batch in ...:
        points = opt.ask(n)                       # feasible by construction
        for p, value in evaluate_all(points):
            opt.tell(p, value)                    # ObjectiveValue, or
        opt.tell_rejected(p, reason)              # a gate/simulation failure

Shipped strategies:

    random    uniform in the transformed box            (mandatory baseline)
    sobol     scrambled Sobol space-filling design      (initial design + baseline)
    bo_gp     GP + log-EI Bayesian optimization         (the requested baseline model)
    cmaes     CMA-ES, noise-aware                       (recommended companion)
    llm_agent / llm_bo  registered by stage4_llm.py     (agentic, Ollama)

Third-party adapters (`tpe_optuna`, `botorch_qnei`) register themselves only if
their package imports, so `--optimizer tpe_optuna` either works or says exactly
which install enables it. The G4CMP environment carries numpy/scipy only, and
Stage 3's record is explicit that it must not be disturbed -- hence the
self-contained GP and CMA-ES below rather than a dependency.

A note on what "feasible" means here: `ask` never returns a point that fails the
cheap gates (bounds, Born stability, bottom-gap range). It CAN return one that
fails the exact gates, which cost 22 ms each because of the Christoffel average;
those are re-checked by the resolver before Geant4 starts and come back as
`constraint_rejected`, which is fed to `tell_rejected` and never becomes an
observation. Failures are not zeros.
"""

import math
import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm, qmc

from stage4_space import DEFAULT_SPACE, precheck_cheap

OPTIMIZERS = {}


def register(name):
    def wrap(cls):
        OPTIMIZERS[name] = cls
        cls.optimizer_name = name
        return cls
    return wrap


def create(name, space=None, seed=0, **kwargs):
    if name not in OPTIMIZERS:
        raise KeyError(f"unknown optimizer {name!r}; available: {sorted(OPTIMIZERS)}")
    return OPTIMIZERS[name](space or DEFAULT_SPACE, seed=seed, **kwargs)


def available():
    return sorted(OPTIMIZERS)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class BaseOptimizer:
    """History bookkeeping shared by every strategy.

    Internally a candidate is (u, c): u in the unit hypercube of the ACTIVE
    continuous variables, c the index of the Miller direction. Natural units
    exist only at the boundary, so an optimizer can never accidentally treat a
    log-scaled variable as linear.
    """

    optimizer_name = "base"

    def __init__(self, space=None, seed=0, max_resample=500, **kwargs):
        self.space = space or DEFAULT_SPACE
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.max_resample = max_resample
        self.U, self.C, self.Y, self.SIG = [], [], [], []
        self.points, self.values = [], []
        self.rejected = []
        self.pending = []                     # asked, not yet told
        self.iteration = 0
        self.extra = dict(kwargs)
        # -- proposal provenance (audit P1) ---------------------------------
        # Every point this optimizer hands out is stamped with WHERE it came
        # from, before it is evaluated. Without this the campaign report can
        # only say which optimizer object produced a trial, not whether the
        # trial came from that optimizer's model: the first campaign's winning
        # point was a Sobol initialisation draw and was reported as a GP
        # Expected-Improvement result.
        self._provenance = {}
        self._used_in_update = {}

    # -- history ------------------------------------------------------------
    def tell(self, point, value):
        u = self.space.to_unit(self.space.complete(point))
        self.U.append(u)
        self.C.append(self.space.miller_index(self.space.complete(point)))
        self.Y.append(value.surrogate_y())
        sigma = value.surrogate_sigma()
        self.SIG.append(sigma if sigma is not None else float("nan"))
        self.points.append(dict(point))
        self.values.append(value)
        self._drop_pending(point)
        self._on_tell(point, value)

    def tell_rejected(self, point, reason):
        """A gate rejection or a simulation failure. NEVER an observation.

        Recorded so the campaign report can show what each optimizer spent its
        budget on: a strategy that "wins" by proposing points that fail cheaply
        must be visible, not silently flattering.
        """
        self.rejected.append({"point": dict(point), "reason": str(reason),
                              "iteration": self.iteration})
        self._drop_pending(point)
        self._on_rejected(point, reason)

    def _drop_pending(self, point):
        key = self._key(point)
        self.pending = [p for p in self.pending if self._key(p) != key]

    def _key(self, point):
        p = self.space.complete(point)
        return (tuple(round(float(p[n]), 12) for n in self.space.names),
                tuple(p["miller"]))

    # -- provenance ---------------------------------------------------------
    def _tag(self, point, source, **extra):
        """Record where one proposal came from. Called by `_ask` implementations."""
        rec = {"proposal_source": source, "iteration": self.iteration,
               "optimizer": self.optimizer_name}
        rec.update({k: v for k, v in extra.items() if v is not None})
        self._provenance[self._key(point)] = rec
        return point

    def provenance_of(self, point):
        """Immutable per-trial provenance: source, generation/fit, acquisition.

        `used_in_optimizer_update` is filled in later -- a point participates in
        a CMA generation update only once its siblings have also reported.
        """
        rec = dict(self._provenance.get(self._key(point))
                   or {"proposal_source": f"untagged_{self.optimizer_name}",
                       "optimizer": self.optimizer_name})
        used = self._used_in_update.get(self._key(point))
        if used is not None:
            rec["used_in_optimizer_update"] = bool(used)
        return rec

    def _mark_used(self, point_key, used=True):
        self._used_in_update[point_key] = bool(used)

    def provenance_counts(self):
        """{source: n} over every point this optimizer has proposed."""
        out = {}
        for rec in self._provenance.values():
            out[rec["proposal_source"]] = out.get(rec["proposal_source"], 0) + 1
        return dict(sorted(out.items()))

    def _on_tell(self, point, value):
        pass

    def _on_rejected(self, point, reason):
        pass

    @property
    def n_observations(self):
        return len(self.Y)

    def best(self):
        if not self.values:
            return None, None
        i = int(np.argmin([v.value for v in self.values]))
        return self.points[i], self.values[i]

    # -- proposals ----------------------------------------------------------
    def _make_point(self, u, c):
        return self.space.from_unit(np.clip(u, 0.0, 1.0), c)

    def _feasible(self, point):
        return precheck_cheap(point, self.space)[0]

    def _random_feasible(self, n):
        out = []
        tries = 0
        while len(out) < n and tries < self.max_resample * max(1, n):
            tries += 1
            u = self.rng.random(self.space.n_cont)
            c = int(self.rng.integers(self.space.n_cat))
            p = self._make_point(u, c)
            if self._feasible(p) and not self._is_duplicate(p):
                out.append(p)
        if len(out) < n:
            raise RuntimeError(
                f"could not draw {n} feasible candidates in {tries} tries; the "
                f"feasible region may be empty under the current bounds")
        return out

    def _is_duplicate(self, point, tol=1e-6):
        """Reject a proposal that repeats an evaluated or pending point.

        Re-evaluating the same vector under the same seed bank would return the
        cached trial -- a free observation that teaches the surrogate nothing
        while looking like progress.
        """
        u = self.space.to_unit(self.space.complete(point))
        c = self.space.miller_index(self.space.complete(point))
        for uu, cc in zip(self.U, self.C):
            if cc == c and np.max(np.abs(uu - u)) < tol:
                return True
        for p in self.pending:
            pu = self.space.to_unit(self.space.complete(p))
            if (self.space.miller_index(self.space.complete(p)) == c
                    and np.max(np.abs(pu - u)) < tol):
                return True
        return False

    def ask(self, n=1):
        self.iteration += 1
        points = self._ask(n)
        for p in points:                      # nothing leaves untagged
            if self._key(p) not in self._provenance:
                self._tag(p, self.optimizer_name)
        self.pending.extend(points)
        return points

    @property
    def n_pending(self):
        return len(self.pending)

    def _ask(self, n):
        raise NotImplementedError

    # -- persistence --------------------------------------------------------
    def state(self):
        return {"optimizer": self.optimizer_name, "seed": self.seed,
                "iteration": self.iteration,
                "n_observations": self.n_observations,
                "n_pending": len(self.pending),
                "n_rejected": len(self.rejected),
                "proposal_sources": self.provenance_counts()}

    def load_state(self, state):
        self.iteration = int(state.get("iteration", 0))


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------
@register("random")
class RandomSearch(BaseOptimizer):
    """Uniform random search in the transformed box.

    Mandatory reference: a model-based method that cannot beat this at equal
    cumulative event cost has not earned its complexity, and the Stage 3
    pipeline document requires it to be reported alongside.
    """

    def _ask(self, n):
        return [self._tag(p, "random_baseline") for p in self._random_feasible(n)]


@register("sobol")
class SobolSearch(BaseOptimizer):
    """Scrambled Sobol design; also the shared initial design for the others.

    Balance is only guaranteed at powers of two, so the engine is advanced in
    powers of two and the categorical dimension is assigned round-robin rather
    than sampled -- a uniform draw over 13 directions would leave some
    orientations unvisited in a 64-point design.
    """

    def __init__(self, space=None, seed=0, **kw):
        super().__init__(space, seed=seed, **kw)
        self.engine = qmc.Sobol(d=self.space.n_cont, scramble=True, seed=seed)
        self._cat_cursor = 0

    def _ask(self, n):
        out = []
        guard = 0
        while len(out) < n and guard < 64 * max(1, n):
            guard += 1
            u = self.engine.random(1)[0]
            c = self._cat_cursor % self.space.n_cat
            p = self._make_point(u, c)
            if self._feasible(p) and not self._is_duplicate(p):
                out.append(self._tag(p, "sobol_init", design_index=self._cat_cursor))
                self._cat_cursor += 1
        if len(out) < n:
            out.extend(self._tag(p, "random_filler",
                                 reason="Sobol engine could not produce a feasible, "
                                        "non-duplicate point")
                       for p in self._random_feasible(n - len(out)))
        return out


# ---------------------------------------------------------------------------
# Gaussian process
# ---------------------------------------------------------------------------
class GaussianProcess:
    """Matern-5/2 (ARD) x categorical-overlap GP with heteroscedastic noise.

    Written out rather than imported because the G4CMP environment has numpy and
    scipy only. Three choices worth stating:

    * **The observation noise is measured, not fitted.** Each trial supplies its
      own sigma from the replica spread at matched injection sites, so the
      diagonal carries real information instead of one global nugget absorbing
      both model error and count noise. A small fitted jitter remains, because
      the sigma estimate itself is noisy with two replicas.
    * **Categorical orientation gets an overlap kernel** (correlation rho
      between different Miller directions, fitted), not one-hot coordinates.
      One-hot in a Matern kernel asserts that all directions are equidistant in
      a metric the physics does not have.
    * **Analytic gradients.** Finite differences over 19 hyperparameters would
      cost 20 Choleskys per gradient step and dominate the campaign's wall time
      once n reaches a few hundred.
    """

    def __init__(self, jitter=1e-8):
        self.jitter = jitter
        self.theta = None
        self.fitted = False

    # -- kernel -------------------------------------------------------------
    @staticmethod
    def _sqdist_scaled(A, B, ell):
        Aw, Bw = A / ell, B / ell
        d2 = (np.sum(Aw ** 2, axis=1)[:, None] + np.sum(Bw ** 2, axis=1)[None, :]
              - 2.0 * Aw @ Bw.T)
        return np.maximum(d2, 0.0)

    def _matern(self, A, B, ell):
        r = np.sqrt(self._sqdist_scaled(A, B, ell))
        s5r = math.sqrt(5.0) * r
        return (1.0 + s5r + (5.0 / 3.0) * r ** 2) * np.exp(-s5r), r

    def _cat_factor(self, ca, cb, rho):
        same = (np.asarray(ca)[:, None] == np.asarray(cb)[None, :])
        return np.where(same, 1.0, rho)

    def _unpack(self, theta):
        d = self.X.shape[1]
        log_sf2 = theta[0]
        log_ell = theta[1:1 + d]
        log_sn2 = theta[1 + d]
        logit_rho = theta[2 + d]
        rho = 1.0 / (1.0 + math.exp(-logit_rho))
        return math.exp(log_sf2), np.exp(log_ell), math.exp(log_sn2), rho

    def _build(self, theta):
        sf2, ell, sn2, rho = self._unpack(theta)
        m, r = self._matern(self.X, self.X, ell)
        cat = self._cat_factor(self.C, self.C, rho)
        K = sf2 * m * cat
        K[np.diag_indices_from(K)] += self.noise + sn2 + self.jitter
        return K, m, r, cat, sf2, ell, sn2, rho

    # -- likelihood ---------------------------------------------------------
    def _nll(self, theta):
        try:
            K, m, r, cat, sf2, ell, sn2, rho = self._build(theta)
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            return 1e10, np.zeros_like(theta)
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, self.y))
        nll = (0.5 * float(self.y @ alpha) + float(np.log(np.diag(L)).sum())
               + 0.5 * len(self.y) * math.log(2 * math.pi))

        Kinv = np.linalg.solve(L.T, np.linalg.solve(L, np.eye(len(self.y))))
        W = np.outer(alpha, alpha) - Kinv                # dNLL/dK = -0.5 W
        grad = np.zeros_like(theta)

        Ksig = sf2 * m * cat                             # signal part
        grad[0] = -0.5 * np.sum(W * Ksig)                # d/dlog sf2

        s5r = math.sqrt(5.0) * r
        pref = sf2 * (5.0 / 3.0) * (1.0 + s5r) * np.exp(-s5r) * cat
        for i in range(self.X.shape[1]):
            diff2 = (self.X[:, i][:, None] - self.X[:, i][None, :]) ** 2
            dK = pref * diff2 / (ell[i] ** 2)            # d/dlog ell_i
            grad[1 + i] = -0.5 * np.sum(W * dK)

        dK_sn2 = np.eye(len(self.y)) * sn2
        grad[1 + self.X.shape[1]] = -0.5 * np.sum(W * dK_sn2)

        same = (np.asarray(self.C)[:, None] == np.asarray(self.C)[None, :])
        dK_rho = np.where(same, 0.0, sf2 * m) * (rho * (1.0 - rho))
        grad[2 + self.X.shape[1]] = -0.5 * np.sum(W * dK_rho)
        return nll, grad

    def fit(self, X, C, y, noise_var=None, n_restarts=3, rng=None):
        rng = rng or np.random.default_rng(0)
        self.X = np.atleast_2d(np.asarray(X, dtype=float))
        self.C = np.asarray(C, dtype=int)
        y = np.asarray(y, dtype=float)
        self.y_mean = float(y.mean())
        self.y_std = float(y.std()) or 1.0
        self.y = (y - self.y_mean) / self.y_std
        d = self.X.shape[1]

        if noise_var is None:
            noise = np.zeros(len(y))
        else:
            noise = np.asarray(noise_var, dtype=float) / (self.y_std ** 2)
            # A trial whose SE could not be estimated gets the median rather than
            # zero: claiming a noiseless observation is the more dangerous error.
            finite = np.isfinite(noise)
            noise = np.where(finite, noise, np.median(noise[finite]) if finite.any() else 1e-4)
        self.noise = np.maximum(noise, 0.0)

        bounds = ([(math.log(1e-3), math.log(1e3))]                 # log sf2
                  + [(math.log(0.02), math.log(20.0))] * d          # log ell
                  + [(math.log(1e-8), math.log(10.0))]              # log sn2
                  + [(-6.0, 6.0)])                                  # logit rho
        best = None
        starts = [np.array([0.0] + [math.log(0.5)] * d + [math.log(1e-3), 1.0])]
        for _ in range(max(0, n_restarts - 1)):
            starts.append(np.array(
                [rng.normal(0.0, 1.0)]
                + list(np.log(rng.uniform(0.1, 3.0, size=d)))
                + [math.log(10 ** rng.uniform(-6, -1)), rng.normal(1.0, 1.5)]))
        for x0 in starts:
            x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
            try:
                res = minimize(self._nll, x0, jac=True, method="L-BFGS-B",
                               bounds=bounds, options={"maxiter": 200})
            except np.linalg.LinAlgError:
                continue
            if res.fun is not None and (best is None or res.fun < best.fun):
                best = res
        if best is None:
            raise RuntimeError("GP hyperparameter fit failed on every restart")
        self.theta = best.x
        K, *_ = self._build(self.theta)
        self.L = np.linalg.cholesky(K)
        self.alpha = np.linalg.solve(self.L.T, np.linalg.solve(self.L, self.y))
        self.fitted = True
        self.nll = float(best.fun)
        return self

    # -- prediction ---------------------------------------------------------
    def predict(self, Xs, Cs, return_std=True):
        sf2, ell, sn2, rho = self._unpack(self.theta)
        Xs = np.atleast_2d(np.asarray(Xs, dtype=float))
        Cs = np.asarray(Cs, dtype=int)
        m, _ = self._matern(Xs, self.X, ell)
        Ks = sf2 * m * self._cat_factor(Cs, self.C, rho)
        mu = Ks @ self.alpha
        mu = mu * self.y_std + self.y_mean
        if not return_std:
            return mu
        v = np.linalg.solve(self.L, Ks.T)
        var = sf2 - np.sum(v ** 2, axis=0)
        var = np.maximum(var, 1e-12) * (self.y_std ** 2)
        return mu, np.sqrt(var)

    def reset_data(self, X, C, y, noise_var):
        """Rebuild on this data with the CURRENT hyperparameters.

        Used before each asynchronous proposal: fantasies for in-flight points
        must not accumulate across asks, and refitting hyperparameters every
        time would cost more than it buys.
        """
        self.X = np.atleast_2d(np.asarray(X, dtype=float))
        self.C = np.asarray(C, dtype=int)
        y = np.asarray(y, dtype=float)
        self.y = (y - self.y_mean) / self.y_std
        noise = np.asarray(noise_var, dtype=float) / (self.y_std ** 2)
        finite = np.isfinite(noise)
        self.noise = np.maximum(
            np.where(finite, noise, np.median(noise[finite]) if finite.any() else 1e-4),
            0.0)
        K, *_ = self._build(self.theta)
        self.L = np.linalg.cholesky(K)
        self.alpha = np.linalg.solve(self.L.T, np.linalg.solve(self.L, self.y))
        return self

    def add_fantasy(self, x, c, y_value, noise_value):
        """Constant-liar update: append a believed observation, refactorize.

        Hyperparameters are NOT refitted -- the point of the liar is to
        discourage a batch from collapsing onto one acquisition peak, not to
        pretend new information arrived.
        """
        self.X = np.vstack([self.X, np.atleast_2d(x)])
        self.C = np.append(self.C, int(c))
        self.y = np.append(self.y, (y_value - self.y_mean) / self.y_std)
        self.noise = np.append(self.noise, noise_value / (self.y_std ** 2))
        K, *_ = self._build(self.theta)
        self.L = np.linalg.cholesky(K)
        self.alpha = np.linalg.solve(self.L.T, np.linalg.solve(self.L, self.y))


def expected_improvement(mu, sigma, best, xi=0.0):
    """EI for MINIMIZATION, with the degenerate-sigma branch handled explicitly."""
    sigma = np.maximum(sigma, 1e-12)
    imp = best - mu - xi
    z = imp / sigma
    return imp * norm.cdf(z) + sigma * norm.pdf(z)


@register("bo_gp")
class GPBayesOpt(BaseOptimizer):
    """GP + Expected Improvement, with a Sobol initial design and batch liar.

    The improvement target is the best POSTERIOR MEAN over evaluated points, not
    the best observed value. With 5%-noise observations the best observation is
    frequently a lucky draw, and chasing it makes EI over-exploit a point the
    model does not actually believe in.
    """

    def __init__(self, space=None, seed=0, n_init=24, refit_every=5,
                 n_candidates=4096, n_polish=4, xi=0.0, n_min_fit=None, **kw):
        super().__init__(space, seed=seed, **kw)
        self.n_init = int(n_init)
        # Below this many REPORTED observations the GP is not fitted at all and
        # `_ask` returns nothing rather than a proposal it would have to call
        # model-guided. Defaults to the whole initial design.
        self.n_min_fit = int(n_min_fit if n_min_fit is not None else n_init)
        self.refit_every = int(refit_every)
        self.n_candidates = int(n_candidates)
        self.n_polish = int(n_polish)
        self.xi = float(xi)
        self.init_design = SobolSearch(self.space, seed=seed)
        self.gp = None
        self._fits = 0
        self.last_acquisition = {}

    def _on_tell(self, point, value):
        self.init_design.U = self.U
        self.init_design.C = self.C
        # Every reported point enters the GP's training set on the next refit,
        # whatever proposed it -- unlike CMA-ES, where only a complete
        # generation contributes to the update.
        self._mark_used(self._key(point), True)

    def _refit(self):
        noise = np.array(self.SIG, dtype=float) ** 2
        self.gp = GaussianProcess().fit(
            np.array(self.U), np.array(self.C), np.array(self.Y),
            noise_var=noise, rng=self.rng)
        self._fits += 1
        return self.gp

    def _candidate_pool(self, incumbent_u=None):
        """Sobol coverage plus local perturbations around the incumbent.

        Pure Sobol coverage in 16 dimensions is thin near any single point, so
        the maximizer would systematically under-resolve the neighbourhood of
        the best-known design -- exactly where EI is usually largest late in a
        campaign.
        """
        n = self.n_candidates
        eng = qmc.Sobol(d=self.space.n_cont, scramble=True,
                        seed=int(self.rng.integers(1 << 31)))
        U = eng.random(n)
        if incumbent_u is not None:
            k = n // 4
            local = np.clip(incumbent_u[None, :]
                            + self.rng.normal(0, 0.08, size=(k, self.space.n_cont)),
                            0.0, 1.0)
            U = np.vstack([U[:n - k], local])
        C = self.rng.integers(0, self.space.n_cat, size=len(U))
        keep = [i for i in range(len(U))
                if precheck_cheap(self._make_point(U[i], C[i]), self.space)[0]]
        return U[keep], C[keep]

    def _polish(self, u0, c, best):
        def neg_ei(u):
            mu, sd = self.gp.predict(np.clip(u, 0, 1)[None, :], [c])
            return -float(expected_improvement(mu, sd, best, self.xi)[0])
        res = minimize(neg_ei, u0, method="L-BFGS-B",
                       bounds=[(0.0, 1.0)] * self.space.n_cont,
                       options={"maxiter": 60})
        u = np.clip(res.x, 0, 1)
        if precheck_cheap(self._make_point(u, c), self.space)[0]:
            return u, -float(res.fun)
        return u0, -neg_ei(u0)

    def _n_initial_dispatched(self):
        """Initial-design points already handed out, EVALUATED OR IN FLIGHT.

        Counting only `n_observations` is the audit's P1 defect: with q workers
        and highly variable trial runtime, up to q-1 extra Sobol points get
        dispatched after the design is complete, and every one of them is then
        reported as a GP proposal. `n_init` is a design size, not a completion
        count, so pending initial points must count against it.
        """
        pending_init = sum(
            1 for p in self.pending
            if str(self._provenance.get(self._key(p), {}).get("proposal_source", ""))
            .endswith("_init"))
        return self.n_observations + pending_init

    def _ask(self, n):
        dispatched = self._n_initial_dispatched()
        if dispatched < self.n_init:
            k = min(n, self.n_init - dispatched)
            out = [self._tag(p, "sobol_init", design_index=dispatched + i)
                   for i, p in enumerate(self.init_design.ask(k))]
            if len(out) < n:
                # The initial design is exhausted mid-batch. The remaining
                # slots cannot be GP proposals -- no GP exists yet -- so they
                # are labelled what they are rather than credited to the model.
                out += [self._tag(p, "random_filler", reason="initial design "
                                                             "exhausted, no GP yet")
                        for p in self._random_feasible(n - len(out))]
            return out
        if self.n_observations < min(self.n_init, self.n_min_fit):
            # Design fully dispatched but not yet reported: waiting is better
            # than fitting a GP on 3 points and calling the result Bayesian.
            return []
        if self.gp is None or (self.n_observations % self.refit_every == 0):
            self._refit()
        else:
            # Drop any fantasies left over from the previous ask before adding
            # this ask's; otherwise an asynchronous campaign accumulates beliefs
            # it never tested.
            self.gp.reset_data(np.array(self.U), np.array(self.C),
                               np.array(self.Y), np.array(self.SIG, dtype=float) ** 2)

        out = []
        median_noise = float(np.nanmedian(np.array(self.SIG, dtype=float) ** 2))
        if not np.isfinite(median_noise):
            median_noise = 1e-4
        # In-flight proposals are fantasised too. Without this, an asynchronous
        # campaign with q workers proposes q near-identical points around the
        # same acquisition peak and learns q times less than it paid for.
        for p in self.pending:
            try:
                u_p = self.space.to_unit(self.space.complete(p))
                c_p = self.space.miller_index(self.space.complete(p))
            except Exception:                                   # noqa: BLE001
                continue
            mu_p, _ = self.gp.predict(u_p[None, :], [c_p])
            self.gp.add_fantasy(u_p, c_p, float(mu_p[0]), median_noise)
        for _ in range(n):
            mu_obs, _ = self.gp.predict(np.array(self.U), np.array(self.C))
            best = float(np.min(mu_obs))
            U, C = self._candidate_pool(np.array(self.U[int(np.argmin(mu_obs))]))
            if len(U) == 0:
                out.extend(self._tag(p, "random_filler",
                                     reason="acquisition candidate pool empty "
                                            "after the cheap feasibility filter")
                           for p in self._random_feasible(1))
                continue
            mu, sd = self.gp.predict(U, C)
            ei = expected_improvement(mu, sd, best, self.xi)
            order = np.argsort(-ei)[:max(1, self.n_polish)]
            cands = []
            for idx in order:
                u_p, ei_p = self._polish(U[idx], int(C[idx]), best)
                cands.append((ei_p, u_p, int(C[idx])))
            cands.sort(key=lambda t: -t[0])
            chosen = None
            for ei_p, u_p, c_p in cands:
                p = self._make_point(u_p, c_p)
                if not self._is_duplicate(p):
                    chosen = (ei_p, u_p, c_p, p)
                    break
            if chosen is None:
                p = self._random_feasible(1)[0]
                chosen = (0.0, self.space.to_unit(p), self.space.miller_index(p), p)
                self._tag(p, "random_filler",
                          reason="every acquisition maximiser candidate duplicated "
                                 "an evaluated or pending point")
            ei_p, u_p, c_p, p = chosen
            self.last_acquisition[self._key(p)] = float(ei_p)
            if self._provenance.get(self._key(p), {}).get("proposal_source") \
                    != "random_filler":
                self._tag(p, "gp_ei", acquisition=float(ei_p), gp_fit=self._fits,
                          n_observations_at_proposal=self.n_observations)
            out.append(p)
            # Constant liar so the rest of the batch does not pile onto the same peak.
            mu_p, _ = self.gp.predict(u_p[None, :], [c_p])
            self.gp.add_fantasy(u_p, c_p, float(mu_p[0]), median_noise)
        return out

    def acquisition_of(self, point):
        return self.last_acquisition.get(self._key(point))

    def state(self):
        s = super().state()
        s.update({"gp_fits": self._fits,
                  "gp_nll": getattr(self.gp, "nll", None),
                  "n_init": self.n_init})
        return s


# ---------------------------------------------------------------------------
# CMA-ES
# ---------------------------------------------------------------------------
@register("cmaes")
class CMAES(BaseOptimizer):
    """(mu/mu_w, lambda)-CMA-ES on the continuous box, noise-aware.

    Recommended alongside the GP rather than instead of it: at 16 dimensions
    with ~5% observation noise, a GP's model risk is real, and CMA-ES is the
    standard robust answer in exactly that regime. Two deviations from the
    textbook defaults, both because the objective is noisy:

    * a larger population (12 rather than 4+3ln d = 12 -> kept, but never
      below 8), since rank information averages noise out;
    * the incumbent is re-proposed periodically, so drift in the machine or a
      lucky early draw becomes visible instead of being locked in.

    Orientation is categorical and CMA-ES is not: the direction is drawn from a
    softmax over each direction's observed mean, with a floor, so a promising
    orientation is exploited without the others being abandoned.
    """

    def __init__(self, space=None, seed=0, popsize=None, sigma0=0.3,
                 reeval_every=6, sigma_min=0.03, stagnation=8, max_popsize=64,
                 synchronous=True, **kw):
        super().__init__(space, seed=seed, **kw)
        # Synchronous by default (audit P1). Asynchronously, a full generation
        # waiting on one slow member used to be topped up with a uniformly
        # random point, which was then stored and reported as a CMA-ES
        # observation despite taking no part in any generation update. The
        # first campaign's 32 "cmaes" trials completed ONE generation that way.
        # Synchronous batches propose nothing while a generation is complete
        # and unreported: the slot idles, which is honest, and the driver is
        # free to spend it on another optimizer.
        self.synchronous = bool(synchronous)
        d = self.space.n_cont
        self.d = d
        self.sigma0 = float(sigma0)
        self.sigma_min = float(sigma_min)
        self.stagnation = int(stagnation)
        self.max_popsize = int(max_popsize)
        self.reeval_every = int(reeval_every)
        self.restarts = 0
        self._best_at_restart = None
        self._gens_without_gain = 0
        self._init_strategy(int(popsize or max(8, 4 + int(3 * math.log(d)))))

    def _init_strategy(self, lam):
        d = self.d
        self.lam = int(lam)
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
        self._gen_points, self._gen_results = [], {}

    def _maybe_restart(self):
        """IPOP-style restart: re-centre on the incumbent, double the population.

        Two triggers, both symptoms of a noisy landscape rather than of
        convergence: the step size collapsing below a scale the objective's own
        5% noise cannot resolve, and a run of generations with no gain. Without
        this the strategy locks onto whichever basin its early -- and partly
        random -- rank ordering happened to favour, which is what it did on the
        synthetic benchmark before this was added.
        """
        best_y = min(self.Y) if self.Y else None
        if best_y is not None:
            if self._best_at_restart is None or best_y < self._best_at_restart - 1e-9:
                self._best_at_restart = best_y
                self._gens_without_gain = 0
            else:
                self._gens_without_gain += 1
        if not (self.sigma < self.sigma_min or self._gens_without_gain >= self.stagnation):
            return
        bp, _ = self.best()
        new_lam = min(self.max_popsize, self.lam * 2)
        self._init_strategy(new_lam)
        if bp is not None:
            self.mean = np.clip(self.space.to_unit(self.space.complete(bp)), 0.05, 0.95)
        self.restarts += 1
        self._gens_without_gain = 0

    def _sample_category(self):
        if not self.values:
            return int(self.rng.integers(self.space.n_cat))
        means = np.full(self.space.n_cat, np.nan)
        for c in range(self.space.n_cat):
            ys = [y for y, cc in zip(self.Y, self.C) if cc == c]
            if ys:
                means[c] = np.mean(ys)
        if np.all(np.isnan(means)):
            return int(self.rng.integers(self.space.n_cat))
        filled = np.where(np.isnan(means), np.nanmax(means), means)
        scale = np.nanstd(filled) or 1.0
        logits = -(filled - np.nanmin(filled)) / scale
        p = np.exp(logits - logits.max())
        p = 0.85 * p / p.sum() + 0.15 / self.space.n_cat     # exploration floor
        return int(self.rng.choice(self.space.n_cat, p=p / p.sum()))

    def _draw(self):
        try:
            A = np.linalg.cholesky(self.Cov + 1e-12 * np.eye(self.d))
        except np.linalg.LinAlgError:
            self.Cov = np.eye(self.d)
            A = np.eye(self.d)
        # Box handling is CLIP, not reject. Rejecting out-of-box draws would bias
        # the distribution away from the walls, and several of these variables
        # plausibly optimize AT a bound (an interface absorption saturating, a
        # lifetime as short as the box allows). The clipped point is the one
        # evaluated and the one fed back into the covariance update, so the
        # strategy can press against a wall and stay there.
        for _ in range(self.max_resample):
            z = self.rng.standard_normal(self.d)
            u = np.clip(self.mean + self.sigma * (A @ z), 0.0, 1.0)
            p = self._make_point(u, self._sample_category())
            if self._feasible(p) and not self._is_duplicate(p):
                return u, p
        u = np.clip(self.mean + self.sigma * self.rng.standard_normal(self.d) * 0.3, 0, 1)
        return u, self._make_point(u, self._sample_category())

    def _ask(self, n):
        out = []
        for _ in range(n):
            if len(self._gen_points) >= self.lam:
                if self.synchronous:
                    break            # generation full: idle rather than mislabel
                out.extend(self._tag(p, "random_filler",
                                     reason="asynchronous filler while generation "
                                            f"{self.generation} waits on a slow "
                                            "member; takes no part in the update",
                                     optimizer_generation=self.generation)
                           for p in self._random_feasible(1))
                continue
            if (self.reeval_every and self.generation > 0
                    and len(self._gen_points) == 0
                    and self.generation % self.reeval_every == 0 and self.values):
                # Re-propose the incumbent so noise drift is observable. The
                # duplicate guard would normally block it, so it is jittered by
                # one part in 10^4 -- far below any physical tolerance.
                bp, _ = self.best()
                u = np.clip(self.space.to_unit(bp)
                            + self.rng.normal(0, 1e-4, self.d), 0, 1)
                c = self.space.miller_index(self.space.complete(bp))
                p = self._make_point(u, c)
                source = "cma_incumbent_reeval"
            else:
                u, p = self._draw()
                source = "cma_generation"
            self._tag(p, source, optimizer_generation=self.generation,
                      population_index=len(self._gen_points), popsize=self.lam,
                      sigma=float(self.sigma), restarts=self.restarts)
            self._gen_points.append((self._key(p), np.array(self.space.to_unit(p))))
            out.append(p)
        return out

    def _on_tell(self, point, value):
        self._gen_results[self._key(point)] = value.surrogate_y()
        self._maybe_update()

    def _on_rejected(self, point, reason):
        # A rejected proposal leaves the generation short; dropping it keeps the
        # rank-mu update on genuinely evaluated points only.
        key = self._key(point)
        self._gen_points = [gp for gp in self._gen_points if gp[0] != key]
        self._maybe_update()

    def _maybe_update(self):
        keys = [k for k, _ in self._gen_points]
        if not keys or not all(k in self._gen_results for k in keys):
            return
        if len(keys) < max(4, self.mu + 1):
            return
        for k in keys:
            self._mark_used(k, True)
        U = np.array([u for _, u in self._gen_points])
        y = np.array([self._gen_results[k] for k in keys])
        order = np.argsort(y)
        mu = min(self.mu, len(order) - 1)
        w = self.w[:mu] / self.w[:mu].sum()
        old_mean = self.mean.copy()
        sel = U[order[:mu]]
        self.mean = w @ sel

        y_w = (self.mean - old_mean) / self.sigma
        try:
            invsqrtC = np.linalg.inv(np.linalg.cholesky(self.Cov + 1e-12 * np.eye(self.d)))
        except np.linalg.LinAlgError:
            invsqrtC = np.eye(self.d)
        self.ps = ((1 - self.cs) * self.ps
                   + math.sqrt(self.cs * (2 - self.cs) * self.mueff) * (invsqrtC @ y_w))
        hsig = (np.linalg.norm(self.ps)
                / math.sqrt(1 - (1 - self.cs) ** (2 * (self.generation + 1)))
                / self.chiN) < (1.4 + 2 / (self.d + 1))
        self.pc = ((1 - self.cc) * self.pc
                   + hsig * math.sqrt(self.cc * (2 - self.cc) * self.mueff) * y_w)
        artmp = (sel - old_mean) / self.sigma
        self.Cov = ((1 - self.c1 - self.cmu) * self.Cov
                    + self.c1 * (np.outer(self.pc, self.pc)
                                 + (not hsig) * self.cc * (2 - self.cc) * self.Cov)
                    + self.cmu * (artmp.T * w) @ artmp)
        self.sigma *= math.exp((self.cs / self.damps)
                               * (np.linalg.norm(self.ps) / self.chiN - 1))
        self.sigma = float(np.clip(self.sigma, 1e-4, 1.0))
        self.generation += 1
        self._gen_points, self._gen_results = [], {}
        self._maybe_restart()

    def state(self):
        s = super().state()
        s.update({"generation": self.generation, "sigma": self.sigma,
                  "popsize": self.lam, "restarts": self.restarts,
                  "synchronous": self.synchronous,
                  "generation_filled": len(self._gen_points),
                  "generation_reported": len(self._gen_results),
                  "n_used_in_update": sum(1 for v in self._used_in_update.values() if v)})
        return s


# ---------------------------------------------------------------------------
# Optional third-party adapters
# ---------------------------------------------------------------------------
def _register_optuna():
    try:
        import optuna
    except ImportError:
        return
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    @register("tpe_optuna")
    class OptunaTPE(BaseOptimizer):
        """Optuna's TPE. Engineering baseline for mixed spaces; needs `pip install optuna`."""

        def __init__(self, space=None, seed=0, **kw):
            super().__init__(space, seed=seed, **kw)
            self.study = optuna.create_study(
                direction="minimize",
                sampler=optuna.samplers.TPESampler(seed=seed, multivariate=True))
            self._trials = {}

        def _ask(self, n):
            out = []
            for _ in range(n * 40):
                if len(out) >= n:
                    break
                trial = self.study.ask()
                u = np.array([trial.suggest_float(v.name, 0.0, 1.0)
                              for v in self.space.variables])
                c = trial.suggest_int("miller", 0, self.space.n_cat - 1)
                p = self._make_point(u, c)
                if self._feasible(p) and not self._is_duplicate(p):
                    self._trials[self._key(p)] = trial
                    out.append(p)
                else:
                    self.study.tell(trial, state=optuna.trial.TrialState.PRUNED)
            return out or self._random_feasible(n)

        def _on_tell(self, point, value):
            trial = self._trials.pop(self._key(point), None)
            if trial is not None:
                self.study.tell(trial, value.surrogate_y())

        def _on_rejected(self, point, reason):
            trial = self._trials.pop(self._key(point), None)
            if trial is not None:
                self.study.tell(trial, state=optuna.trial.TrialState.PRUNED)


_register_optuna()


def missing_adapters():
    """Which optional strategies are unavailable here, and how to enable them.

    Reported by the campaign rather than raised, so a missing package is a
    documented gap in the comparison instead of a crashed run.
    """
    out = {}
    for name, package, why in (
            ("tpe_optuna", "optuna",
             "tree-structured Parzen estimator baseline for mixed spaces"),
            ("botorch_qnei", "botorch",
             "qLogNoisyEI and multi-fidelity KG; needs torch"),
    ):
        if name not in OPTIMIZERS:
            out[name] = (f"pip install {package}  "
                         f"(in a SEPARATE env from G4CMP) -- {why}")
    return out


if __name__ == "__main__":
    print("Stage 4 optimizers:", available())
    for name, how in missing_adapters().items():
        print(f"  unavailable: {name:14s} {how}")
