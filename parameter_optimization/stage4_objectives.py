"""Stage 4 objective registry -- what "minimize QP generation" means, switchably.

Every objective is a function of ONE trial's (position, replica) blocks, so
switching objective never changes what was simulated, only how it is scored.
The name is recorded in the ledger and in the campaign manifest, so two
campaigns optimizing different objectives can never be merged by accident.

Uncertainty, and why it is not Poisson
--------------------------------------
`total_QPs` is a COMPOUND count: each recorded hit contributes
`round(E_dep / setTopGap)` quasiparticles (mean ~2.7 with a tail), and the 16
injection sites span 9.18x in yield. The measured overdispersion is Fano ~ 5,
i.e. errors 2.3x larger than Poisson -- so every Poisson z printed anywhere in
this project is optimistic by that factor.

Two different uncertainties are reported, because they answer different
questions and mixing them is how a ranking gets over-claimed:

* `se`  -- STOCHASTIC error at fixed quadrature, estimated from the spread
           BETWEEN REPLICAS AT THE SAME SITE. This is the right error bar for
           "is candidate A below candidate B", because the 16 sites are common
           to every candidate and cancel in a paired difference.
* `bootstrap_ci` -- resamples whole SITES (both replicas together). This is the
           right interval for an ABSOLUTE yield claim, and it is much wider
           because the 16-site quadrature is not converged (one site carried
           49.6% of the baseline's QPs).
"""

import math
from dataclasses import dataclass, field

import numpy as np

OBJECTIVES = {}


@dataclass
class ObjectiveValue:
    name: str
    value: float                 # what the optimizer minimizes (natural units)
    raw: float                   # untransformed headline number, for reporting
    se: float = None             # stochastic 1 sigma on `value` (replica-based)
    n_blocks: int = 0
    transform: str = "log"
    detail: dict = field(default_factory=dict)

    def surrogate_y(self, floor=None):
        """The response a surrogate is fitted on.

        `log` rather than `log1p`: the objective is ~1e-4 QPs per primary event,
        where log1p(x) ~ x and the transform would do nothing. The floor is one
        half QP over the trial's event budget -- the smallest resolvable
        non-zero yield -- so a legitimate zero is representable without -inf.
        """
        if self.transform == "identity":
            return float(self.value)
        floor = floor or self.detail.get("value_floor") or 1e-12
        return float(math.log(max(float(self.value), floor)))

    def surrogate_sigma(self, floor=None):
        """1 sigma of `surrogate_y`, propagated through the transform."""
        if self.se is None or not np.isfinite(self.se):
            return None
        if self.transform == "identity":
            return float(self.se)
        floor = floor or self.detail.get("value_floor") or 1e-12
        return float(self.se / max(float(self.value), floor))


class Objective:
    def __init__(self, name, fn, direction="minimize", transform="log",
                 needs_blocks=True, doc=""):
        self.name, self.fn = name, fn
        self.direction, self.transform = direction, transform
        self.needs_blocks, self.doc = needs_blocks, doc

    def __call__(self, result, **params):
        if self.needs_blocks and not getattr(result, "blocks", None):
            raise ValueError(
                f"objective {self.name!r} needs per-(position, replica) blocks and "
                f"this trial has none (it predates per-sub-run scoring). Re-run it "
                f"rather than scoring it with a different objective.")
        value = self.fn(result, **params)
        value.name = self.name
        value.transform = self.transform
        return value


def register(name, **kw):
    def wrap(fn):
        OBJECTIVES[name] = Objective(name, fn, doc=(fn.__doc__ or "").strip(), **kw)
        return fn
    return wrap


def get(name):
    if name not in OBJECTIVES:
        raise KeyError(f"unknown objective {name!r}; available: {sorted(OBJECTIVES)}")
    return OBJECTIVES[name]


def available():
    return {n: o.doc.splitlines()[0] if o.doc else "" for n, o in sorted(OBJECTIVES.items())}


# ---------------------------------------------------------------------------
# Block statistics
# ---------------------------------------------------------------------------
def _block_table(result):
    """(positions, replicas) -> QP counts, plus the per-block event count."""
    blocks = result.blocks
    positions = sorted({b["position"] for b in blocks})
    replicas = sorted({b["replica"] for b in blocks})
    table = np.full((len(positions), len(replicas)), np.nan)
    pi = {p: i for i, p in enumerate(positions)}
    ri = {r: i for i, r in enumerate(replicas)}
    for b in blocks:
        table[pi[b["position"]], ri[b["replica"]]] = b["total_qps"]
    if np.isnan(table).any():
        raise ValueError("block table has holes: an incomplete set must never be scored")
    events = int(blocks[0]["events"])
    return table, events, positions, replicas


def replica_se_total(table):
    """Stochastic 1 sigma on the SUM, from within-site replica spread.

    Var(sum) = sum over sites and replicas of the per-run variance, estimated
    site by site so the 9.18x site heterogeneity -- which is common to every
    candidate and cancels in paired comparisons -- does not leak into the
    stochastic error bar.
    """
    n_pos, n_rep = table.shape
    if n_rep < 2:
        return None
    per_site_var = table.var(axis=1, ddof=1)          # unbiased, per site
    return float(math.sqrt(n_rep * per_site_var.sum()))


def site_bootstrap_ci(table, n_boot=2000, alpha=0.05, seed=12345):
    """Interval for an ABSOLUTE yield claim: resample whole sites with replacement.

    Wide by construction, and it should be: the 16-site quadrature is a fixed
    design that has not been shown converged.
    """
    rng = np.random.default_rng(seed)
    site_totals = table.sum(axis=1)
    n = len(site_totals)
    draws = site_totals[rng.integers(0, n, size=(n_boot, n))].sum(axis=1)
    lo, hi = np.percentile(draws, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


# ---------------------------------------------------------------------------
# Objectives
# ---------------------------------------------------------------------------
@register("total_qps_per_primary")
def _total_per_primary(result):
    """JUNCTION QPs per primary event, summed over the 17 Al electrodes.

    Default, so a Stage 4 property target is directly comparable with the
    converged v2 material ranking (Si/Nb/Cu = 3.900e-4, Ge/Nb/Cu = 2.494e-4).

    Named "total" for continuity with Stage 3, but the measured quantity is
    JUNCTION QP yield, not total QP generation in the device: ground-plane QPs,
    bottom-film QPs, microwave loss and thermal quasiparticles are all outside
    it (audit P3). `engineering_diagnostics()` reports what it cannot see.
    """
    table, events, positions, _ = _block_table(result)
    total = float(table.sum())
    n_sim = events * table.size
    se_total = replica_se_total(table)
    lo, hi = site_bootstrap_ci(table)
    return ObjectiveValue(
        name="", value=total / n_sim, raw=total,
        se=(se_total / n_sim if se_total is not None else None),
        n_blocks=table.size,
        detail={
            "total_qps": total, "n_sim": n_sim,
            "value_floor": 0.5 / n_sim,
            "relative_se": (se_total / total if se_total and total else None),
            "site_bootstrap_ci_total": [lo, hi],
            "site_totals": [float(v) for v in table.sum(axis=1)],
            "positions": positions,
        })


@register("junction_qps_per_primary")
def _junction_per_primary(result):
    """Same measurement as `total_qps_per_primary`, under its accurate name.

    Registered so a campaign can be explicit about what is being minimised. The
    two are numerically identical; the objective name is recorded in the ledger
    and hashed into the contract, so they are not interchangeable mid-campaign.
    """
    return _total_per_primary(result)


@register("total_qps", transform="identity")
def _total(result):
    """Raw junction QP count at this event budget (reporting form)."""
    v = _total_per_primary(result)
    return ObjectiveValue(name="", value=v.raw, raw=v.raw,
                          se=(v.se * v.detail["n_sim"] if v.se is not None else None),
                          n_blocks=v.n_blocks, detail=v.detail)


@register("robust_mean_plus_se")
def _robust(result, lam=1.0):
    """mean_b(q_b) + lambda * SE_b(q_b) over all (site, replica) blocks.

    The pipeline's J(x). The SE here is deliberately the ACROSS-BLOCK one, so
    the penalty includes site-to-site spread: this objective prefers a candidate
    that is uniformly acceptable over one that is excellent at 15 sites and
    catastrophic at the 16th.
    """
    table, events, positions, _ = _block_table(result)
    q = table.ravel() / events
    mean, se = float(q.mean()), float(q.std(ddof=1) / math.sqrt(q.size))
    total = float(table.sum())
    return ObjectiveValue(
        name="", value=mean + lam * se, raw=total, se=se, n_blocks=q.size,
        detail={"mean_q": mean, "across_block_se": se, "lambda": lam,
                "total_qps": total, "value_floor": 0.5 / (events * table.size)})


@register("p90_position")
def _p90(result):
    """90th percentile of the 16 per-site yields -- optimize the bad sites.

    Injection site moves the objective by 9.18x with the material held fixed,
    and the elastic tensor plus orientation STEER the phonon caustic. A
    mean-only objective can therefore be improved by moving focusing away from
    one site rather than by reducing device-wide damage; this one cannot.
    """
    table, events, positions, _ = _block_table(result)
    site_q = table.mean(axis=1) / events
    value = float(np.percentile(site_q, 90))
    return ObjectiveValue(
        name="", value=value, raw=float(table.sum()),
        se=None, n_blocks=table.size,
        detail={"site_q": [float(v) for v in site_q], "positions": positions,
                "value_floor": 0.5 / (events * table.shape[1])})


@register("max_electrode_qps_per_primary")
def _max_electrode(result):
    """Worst single electrode's QP yield -- peak per-qubit burden.

    Closer to what a surface code cares about than the device sum, without
    pretending to be a logical error rate. A candidate that halves the total
    while concentrating everything on one qubit is not an improvement.
    """
    blocks = result.blocks
    per_electrode = np.zeros(len(blocks[0]["per_electrode_qps"]), dtype=float)
    for b in blocks:
        per_electrode += np.asarray(b["per_electrode_qps"], dtype=float)
    events = int(blocks[0]["events"])
    n_sim = events * len(blocks)
    worst = int(np.argmax(per_electrode))
    return ObjectiveValue(
        name="", value=float(per_electrode[worst]) / n_sim,
        raw=float(per_electrode.sum()), se=None, n_blocks=len(blocks),
        detail={"worst_electrode": worst,
                "per_electrode_qps": [float(v) for v in per_electrode],
                "n_sim": n_sim, "value_floor": 0.5 / n_sim})


@register("weighted_sum")
def _weighted(result, alpha=1.0, beta=1.0):
    """alpha * (total per primary) + beta * (worst electrode per primary).

    An explicit trade-off. alpha and beta come from the campaign config and
    therefore enter the contract hash: two campaigns with different weights are
    different campaigns, not the same one re-reported.
    """
    a = _total_per_primary(result)
    b = _max_electrode(result)
    return ObjectiveValue(
        name="", value=alpha * a.value + beta * b.value, raw=a.raw,
        se=(alpha * a.se if a.se is not None else None), n_blocks=a.n_blocks,
        detail={"alpha": alpha, "beta": beta, "total_per_primary": a.value,
                "max_electrode_per_primary": b.value,
                "value_floor": alpha * a.detail["value_floor"]})


# ---------------------------------------------------------------------------
# Engineering diagnostics -- what the objective does NOT measure (audit P3)
#
# `total_qps_per_primary` counts QPs at the 17 Al junction electrodes and
# nothing else. The strongest lever the search found is a very low top-film gap,
# which turns the ground plane into a phonon sink next to the qubits -- and
# nothing in this objective penalises the ground plane's own quasiparticles or
# its microwave loss. The optimizer is not wrong; the fabrication reading of its
# answer is incomplete.
#
# These are DIAGNOSTICS, computed from the candidate rather than the simulation,
# and they are deliberately NOT part of `derived` (which is inside the cache
# key) and NOT summed into the objective by default. They exist so a finalist
# can be presented as a Pareto set instead of a single winner, and so a campaign
# can impose an explicit constraint rather than discovering the trade-off later.
# ---------------------------------------------------------------------------
KB_EV_PER_K = 8.617333262e-5
BCS_GAP_OVER_KTC = 1.764              # Delta(0) = 1.764 kB Tc
AL_GAP_EV = 191.0e-6                  # the junction this device actually reads


def film_tc_K(gap_eV):
    """BCS critical temperature implied by a pairing gap."""
    return float(gap_eV) / (BCS_GAP_OVER_KTC * KB_EV_PER_K)


def thermal_qp_density(gap_eV, temperature_K, n0_per_um3_eV=1.74e10):
    """Equilibrium quasiparticle density n_qp = 2 N0 sqrt(2 pi kT Delta) e^(-Delta/kT).

    The standard BCS low-temperature expression. `n0_per_um3_eV` is the
    single-spin density of states at the Fermi level; the default is aluminium's
    (1.74e10 um^-3 eV^-1), used as a stand-in because the film is a pseudo-metal
    with no density of states of its own. It therefore gives the SCALING with
    gap and temperature, which is the exponential part that matters, and not an
    absolute density for a named metal.

    Returned per cubic micrometre. Reported, never optimized: it is an
    equilibrium estimate for a device whose interesting quasiparticles are
    non-equilibrium.
    """
    gap = float(gap_eV)
    kt = KB_EV_PER_K * float(temperature_K)
    if gap <= 0:
        return float("inf")
    if kt <= 0:
        return 0.0
    return float(2 * n0_per_um3_eV * math.sqrt(2 * math.pi * kt * gap)
                 * math.exp(-gap / kt))


def engineering_diagnostics(point, operating_temperature_K=0.02,
                            junction_gap_eV=AL_GAP_EV):
    """Everything the junction-QP objective cannot see, for one candidate.

    Fields:
      topfilm_tc_K              implied Tc of the ground plane
      topfilm_gap_ratio         2*Delta_film / 2*Delta_junction; below 1 the film
                                absorbs across the whole band the junction uses
      ground_plane_is_phonon_sink  that condition, stated as a flag
      thermal_qp_density_um3    equilibrium QP density in the ground plane at
                                the operating temperature (scaling, not absolute)
      thermal_qp_density_ratio  the same, relative to a Nb ground plane
      loss_proxy                exp(-Delta/kT) relative to Nb: the temperature
                                factor in the BCS surface resistance, so it
                                tracks microwave loss without claiming a Q value
    """
    gap = float(point["topfilm_gap"])
    nb_gap = 1.5384e-3
    n_qp = thermal_qp_density(gap, operating_temperature_K)
    kt = KB_EV_PER_K * float(operating_temperature_K)
    # Ratios are reported as LOG10. At 20 mK, exp(-Delta_Nb/kT) is e^-893: the
    # ratio itself underflows to 0 and its reciprocal overflows to inf, so a
    # linear ratio here would print "0" or "inf" for a difference of hundreds of
    # orders of magnitude -- which is the whole finding.
    if kt > 0:
        log10_qp_ratio = (0.5 * math.log10(gap / nb_gap)
                          + (nb_gap - gap) / kt / math.log(10.0))
        log10_loss_ratio = (nb_gap - gap) / kt / math.log(10.0)
    else:
        log10_qp_ratio = log10_loss_ratio = 0.0
    return {
        "operating_temperature_K": float(operating_temperature_K),
        "topfilm_gap_eV": gap,
        "topfilm_tc_K": film_tc_K(gap),
        "topfilm_gap_ratio": gap / float(junction_gap_eV),
        "ground_plane_is_phonon_sink": bool(gap < float(junction_gap_eV)),
        "thermal_qp_density_um3": n_qp,
        "log10_thermal_qp_density_ratio_vs_Nb": log10_qp_ratio,
        "log10_loss_proxy_vs_Nb": log10_loss_ratio,
        "caveat": "Equilibrium BCS estimates from the film gap alone, with Al's "
                  "density of states. They rank candidates by ground-plane risk; "
                  "they are not a predicted Q or a predicted QP density.",
    }


def pareto_front(rows, keys, lower_is_better=None):
    """Non-dominated subset of `rows` over `keys`.

    Used when no candidate dominates on every engineering objective, which is
    the expected outcome once ground-plane loss is scored alongside junction
    QPs: reporting one winner would then be a choice of weighting disguised as
    a measurement.
    """
    lower = set(lower_is_better if lower_is_better is not None else keys)
    usable = [r for r in rows if all(r.get(k) is not None for k in keys)]

    def dominates(a, b):
        better_anywhere = False
        for k in keys:
            av, bv = float(a[k]), float(b[k])
            if k not in lower:
                av, bv = -av, -bv
            if av > bv:
                return False
            if av < bv:
                better_anywhere = True
        return better_anywhere

    return [a for a in usable if not any(dominates(b, a) for b in usable if b is not a)]


def paired_difference(result_a, result_b):
    """Site-matched difference A - B, with the paired stochastic error.

    Comparisons are paired by construction -- the same 16 sites and the same
    seed bank for every candidate -- so the site term cancels here instead of
    inflating the interval.
    """
    ta, ea, pa, _ = _block_table(result_a)
    tb, eb, pb, _ = _block_table(result_b)
    if pa != pb or ea != eb or ta.shape != tb.shape:
        raise ValueError("cannot pair trials with different site sets or budgets")
    n_sim = ea * ta.size
    delta_counts = ta - tb                       # site- and replica-matched
    site_diff = delta_counts.sum(axis=1)
    # Paired stochastic error: the site term is differenced away, so the spread
    # BETWEEN matched replica pairs is the whole error. With R replicas the
    # variance of the paired total is R * sum_sites Var_r(delta).
    n_rep = delta_counts.shape[1]
    se_counts = (float(math.sqrt(n_rep * delta_counts.var(axis=1, ddof=1).sum()))
                 if n_rep >= 2 else None)
    return {
        "delta_per_primary": float(delta_counts.sum()) / n_sim,
        "delta_total_qps": float(delta_counts.sum()),
        "se_delta_total_qps": se_counts,
        "z_paired": (float(delta_counts.sum() / se_counts)
                     if se_counts else None),
        "relative": float((ta.sum() - tb.sum()) / tb.sum()) if tb.sum() else None,
        "site_deltas": [float(v) for v in site_diff],
        "n_sites_favouring_a": int((site_diff < 0).sum()),
        "n_sites": int(len(site_diff)),
    }


if __name__ == "__main__":
    print("Stage 4 objectives:")
    for name, doc in available().items():
        print(f"  {name:32s} {doc}")
