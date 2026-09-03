#!/usr/bin/env python3
"""Stage 4 campaign driver: search the property space for minimum QP generation.

    python stage4_optimize.py --optimizer bo_gp --objective total_qps_per_primary \\
        --trials 80 --batch 4 --parallel 4 --fidelity S --tag pilot

One driver for every optimizer and every objective, because the comparison
between them is only meaningful if nothing else differs: same contract, same 16
injection sites, same seed bank, same evaluator, same ledger.

What this file is responsible for, and what it refuses to do:

* It dispatches candidates and records results. It never decides that a failed
  simulation was a zero, never scores an incomplete scenario set, and never
  hands the optimizer an observation that did not come from a completed trial.
* It re-loads its own history from the ledger on restart, so a campaign can be
  stopped and resumed around another user's load on the machine.
* It periodically re-evaluates the frozen baseline as a control. If the
  baseline moves, something in the machine or the code moved, and the campaign
  is suspect -- that check is cheap and it has caught real drift before.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from stage3_contract import load_contract, ContractError          # noqa: E402
from stage3_ledger import Ledger, STATUS_SUCCESS, STATUS_SUCCESS_ZERO  # noqa: E402
from stage3_trial_runner import evaluate, _blocks_from_ledger     # noqa: E402
import stage4_space as space_mod                                  # noqa: E402
import stage4_objectives as objectives                            # noqa: E402
import stage4_optimizers as optimizers                            # noqa: E402
import stage4_llm  # noqa: F401,E402  (registers llm_agent / llm_bo)


class _Row:
    """Minimal stand-in for a TrialResult when replaying the ledger."""

    def __init__(self, blocks, total_qps, candidate):
        self.blocks = blocks
        self.total_qps = total_qps
        self.candidate = candidate


def build_space(contract):
    """Space from the contract's `space` section, so bounds are hashed with it."""
    spec = (contract.raw or {}).get("space") or {}
    variables = []
    overrides = spec.get("variables") or {}
    active_only = spec.get("active")
    for v in space_mod.VARIABLES:
        o = overrides.get(v.name) or {}
        active = v.active if active_only is None else (v.name in active_only)
        if "active" in o:
            active = bool(o["active"])
        variables.append(space_mod.Variable(
            v.name, v.unit, float(o.get("baseline", v.baseline)),
            float(o.get("low", v.low)), float(o.get("high", v.high)),
            o.get("scale", v.scale), v.target, v.doc, active=active))
    millers = spec.get("millers") or contract.decision["orientation"]["allowed_miller"]
    return space_mod.Space(variables, millers=[list(m) for m in millers],
                           constraints=spec.get("constraints") or {})


def resolver_for(space):
    def _resolve(contract, candidate):
        return space_mod.resolve(contract, candidate, space=space)
    return _resolve


def candidate_payload(point, space):
    """What goes into the ledger and the cache key: values only, no rationale.

    The realization block is part of the candidate's IDENTITY, not decoration:
    the same property vector carried by G4_Si (2330 kg/m3) and by
    G4_CALCIUM_FLUORIDE (3180 kg/m3) is two different crystals, with sound
    speeds 17% apart. It is validated here -- so an unknown carrier or an
    incomplete lattice record fails before a trial is planned -- and emitted
    into the payload, which puts it in the cache key.
    """
    p = space.complete(point)
    out = {n: float(p[n]) for n in [v.name for v in space.all_variables]}
    out["miller"] = [int(x) for x in p["miller"]]
    out["_space"] = "stage4_property_v1"
    realization = space_mod.normalize_realization(p)
    if realization != space_mod.default_realization():
        out[space_mod.REALIZATION_KEY] = realization
    return out


class Campaign:
    def __init__(self, args):
        self.args = args
        self.contract = load_contract(args.contract)
        self.space = build_space(self.contract)
        self.objective = objectives.get(args.objective)
        self.objective_params = json.loads(args.objective_params or "{}")
        self._apply_overrides()
        self.ledger_path = args.ledger
        self.campaign_id = self.contract.campaign_id
        self.resolver = resolver_for(self.space)
        self.local = threading.local()
        self.lock = threading.Lock()
        self.history = []
        self.events_used = 0
        self.n_rejected = 0
        self.n_failed = 0
        self.baseline_values = []
        self.baseline_controls = []
        self.started = time.time()

        opt_kw = json.loads(args.optimizer_params or "{}")
        if args.optimizer in ("llm_agent", "llm_bo"):
            opt_kw.setdefault("objective_name", args.objective)
            opt_kw.setdefault("log_dir", os.path.join(
                HERE, "runs", self.campaign_id, "llm"))
            if args.llm_model:
                opt_kw.setdefault("model", args.llm_model)
            if args.llm_host:
                opt_kw.setdefault("host", args.llm_host)
        self.optimizer = optimizers.create(args.optimizer, self.space,
                                           seed=args.seed, **opt_kw)

    # -- setup --------------------------------------------------------------
    def _apply_overrides(self):
        a, f = self.args, self.contract.fixed
        if a.tag:
            self.contract.campaign_id = f"{self.contract.campaign_id}_{a.tag}"
        if a.positions:
            f["n_positions"] = a.positions
        if a.replicas:
            f["n_replicas"] = a.replicas
        if a.timeout:
            f["sample_timeout_s"] = a.timeout
        fid = a.fidelity or self.contract.decision["fidelity"]["value"]
        self.contract.decision["fidelity"]["value"] = fid
        if a.events:
            self.contract.decision["fidelity"]["events_total_per_candidate"][fid] = a.events
        self.fidelity = fid
        self.events_total = int(
            self.contract.decision["fidelity"]["events_total_per_candidate"][fid])
        # Workers and the memory budget are split across concurrently evaluated
        # candidates: each evaluate() runs its own pool and its own guard, and a
        # guard only sees the sub-runs it started.
        total_workers = a.workers or int(f.get("max_workers", 32))
        f["max_workers"] = max(1, total_workers // max(1, a.parallel))
        f["total_mem_gb"] = float(a.total_mem_gb or f.get("total_mem_gb", 200)) / max(1, a.parallel)
        self.total_workers = total_workers
        # The objective's meaning is part of the campaign identity, so it is
        # hashed with the contract rather than living only in a CLI flag.
        self.contract.raw.setdefault("stage4", {})
        self.contract.raw["stage4"].update({
            "objective": self.args.objective,
            "objective_params": self.objective_params,
            "space_id": "stage4_property_v1",
        })

    def ledger(self):
        if getattr(self.local, "ledger", None) is None:
            self.local.ledger = Ledger(self.ledger_path)
        return self.local.ledger

    # -- history ------------------------------------------------------------
    def resume(self):
        """Replay this campaign's completed trials into the optimizer.

        Only rows whose contract hash AND objective match are replayed: a
        campaign re-run with different bounds or a different objective is a
        different campaign, and merging them would corrupt the surrogate.
        """
        led = self.ledger()
        # Campaign identity here on purpose: a campaign re-run with different
        # bounds or a different objective is a different campaign and merging
        # their histories would corrupt the surrogate -- even though the two may
        # legitimately SHARE cached simulations (finding N2).
        want_contract = self.contract.campaign_contract_hash()
        rows = [r for r in led.observations(self.contract.campaign_id)
                if r["contract_hash"] == want_contract
                and (r["objective_name"] in (None, self.args.objective))]
        rows.sort(key=lambda r: r["created_at"])
        replayed = 0
        for row in rows:
            candidate = json.loads(row["candidate"])
            if candidate.get("_space") != "stage4_property_v1":
                continue
            blocks = _blocks_from_ledger(led, row["trial_id"], row["events_per_sub_run"])
            if not blocks:
                continue
            value = self.objective(_Row(blocks, row["total_qps"], candidate),
                                   **self.objective_params)
            point = {k: v for k, v in candidate.items() if not k.startswith("_")}
            try:
                self.optimizer.tell(point, value)
            except space_mod.GateError:
                continue
            # `cached` means "cost nothing this time". A RESUMED trial did cost
            # events, just in an earlier process, so it must still count on the
            # cost axis of the optimizer comparison.
            self.history.append({"trial_id": row["trial_id"], "point": point,
                                 "value": value, "cached": False, "resumed": True})
            self.events_used += int(row["events_total"])
            replayed += 1
        if replayed:
            print(f"Resumed {replayed} completed trial(s) from {self.ledger_path}")
        return replayed

    # -- evaluation ---------------------------------------------------------
    def _evaluate_one(self, point, iteration, force=False, control_replica_id=None):
        candidate = candidate_payload(point, self.space)
        led = self.ledger()
        try:
            result = evaluate(self.contract, candidate, fidelity=self.fidelity,
                              seed_bank_id=self.args.seed_bank, ledger=led,
                              verbose=False, resolver=self.resolver, force=force)
        except (ContractError, space_mod.GateError) as exc:
            return {"point": point, "status": "constraint_rejected",
                    "reason": str(exc), "candidate": candidate}
        if not result.is_observation:
            return {"point": point, "status": result.status,
                    "reason": result.failure_reason, "candidate": candidate,
                    "trial_id": result.trial_id}
        try:
            value = self.objective(result, **self.objective_params)
        except ValueError as exc:
            return {"point": point, "status": "unscorable", "reason": str(exc),
                    "candidate": candidate, "trial_id": result.trial_id}
        acquisition = None
        if hasattr(self.optimizer, "acquisition_of"):
            acquisition = self.optimizer.acquisition_of(point)
        prov = (self.optimizer.provenance_of(point)
                if hasattr(self.optimizer, "provenance_of") else {})
        led.set_optimizer_record(result.trial_id, objective_name=self.args.objective,
                                 objective_value=value.value, objective_se=value.se,
                                 optimizer=self.args.optimizer, iteration=iteration,
                                 acquisition=acquisition or prov.get("acquisition"),
                                 proposal_source=prov.get("proposal_source"),
                                 optimizer_generation=prov.get("optimizer_generation"),
                                 control_replica_id=control_replica_id)
        return {"point": point, "status": result.status, "value": value,
                "result": result, "candidate": candidate, "provenance": prov,
                "trial_id": result.trial_id, "cached": result.cached}

    def _record(self, rec, best_seen):
        """Fold one finished trial into the optimizer, the ledger and the log."""
        improved = False
        if rec.get("value") is not None:
            self.optimizer.tell(rec["point"], rec["value"])
            # Told AFTER the tell, because CMA-ES only learns from a point once
            # its whole generation has reported: "did this trial move the
            # search?" is not knowable at proposal time.
            if rec.get("trial_id") and hasattr(self.optimizer, "provenance_of"):
                after = self.optimizer.provenance_of(rec["point"])
                if after.get("used_in_optimizer_update") is not None:
                    self.ledger().set_optimizer_record(
                        rec["trial_id"],
                        used_in_optimizer_update=after["used_in_optimizer_update"])
            self.history.append({"trial_id": rec.get("trial_id"), "point": rec["point"],
                                 "value": rec["value"], "cached": rec.get("cached", False)})
            if not rec.get("cached"):
                self.events_used += self.events_total
            v = rec["value"].value
            if best_seen is None or v < best_seen * (1 - self.args.tolerance):
                improved = True
            if best_seen is None or v < best_seen:
                best_seen = v
            print(f"  [{len(self.history):4d}/{self.args.trials}] {rec['status']:8s} "
                  f"obj={v:.4e}"
                  + (f" +-{rec['value'].se:.1e}" if rec["value"].se else "")
                  + f"  best={best_seen:.4e}"
                  + (f"  {rec.get('runtime_s', 0):.0f}s" if rec.get("runtime_s") else "")
                  + ("  (cached)" if rec.get("cached") else ""), flush=True)
        else:
            self.optimizer.tell_rejected(rec["point"], rec.get("reason", "?"))
            if rec["status"] == "constraint_rejected":
                self.n_rejected += 1
            else:
                self.n_failed += 1
            print(f"  [ ---- ] {rec['status']:8s} {str(rec.get('reason'))[:110]}", flush=True)
        return best_seen, improved

    # -- control ------------------------------------------------------------
    def baseline_control(self, iteration):
        """Re-evaluate the frozen baseline point FRESH. Drift detector.

        Audit P5: this used to call the ordinary evaluator with no `force`, so
        the cache returned the original row every time and the campaign reported
        "6 re-evaluations, 0.0% spread". That measured the cache, not the
        machine, the executable or the runtime.

        Each control now carries its own `control_replica_id`. That is the only
        non-physics field in the cache key, and it exists so a control mints its
        own trial row instead of overwriting the previous one -- six controls
        leave six independently inspectable records with their own code
        fingerprint, host, worker shape, timestamp and runtime. Controls are
        excluded from `observations()`, so they never reach the surrogate and
        never appear on the event-efficiency curve.

        Exact replay is not available on this executable (plan sec 13.3), so
        "drift" cannot mean bit equality. It is judged against the measured
        stochastic repeat distribution instead.
        """
        replica = len(self.baseline_controls) + 1
        control_id = f"{self.contract.campaign_id}#control{replica:02d}"
        point = self.space.baseline_point()
        t0 = time.time()
        rec = self._evaluate_one(point, iteration, force=True,
                                 control_replica_id=control_id)
        entry = {"control_replica_id": control_id, "replica": replica,
                 "trial_id": rec.get("trial_id"), "status": rec.get("status"),
                 "cached": bool(rec.get("cached")), "wall_s": round(time.time() - t0, 1),
                 "host": socket.gethostname(), "workers": self.total_workers,
                 "code_fingerprint": {
                     k: self.contract.code_fingerprint().get(k)
                     for k in ("git_revision", "main_exe_sha256")},
                 "timestamp": time.time(), "value": None}
        if rec.get("value") is not None:
            entry["value"] = float(rec["value"].value)
            entry["se"] = rec["value"].se
            self.baseline_values.append(entry["value"])
            values = np.array(self.baseline_values, dtype=float)
            first = values[0]
            drift = (values[-1] - first) / first if first else 0.0
            spread = float(values.std(ddof=1) / values.mean()) if len(values) > 1 else 0.0
            entry.update({"drift_vs_first": drift, "spread_cv": spread})
            # A control that came back cached is a BUG here, not a result.
            tag = "CACHE HIT -- not a fresh control!" if rec.get("cached") else "fresh"
            se = rec["value"].se or 0.0
            noise = 2.0 * (se / entry["value"]) if entry["value"] else 0.15
            flag = "  <-- DRIFT" if abs(drift) > max(0.15, noise) else ""
            print(f"  [control {replica:02d}] baseline {entry['value']:.4e} "
                  f"({tag}, drift {drift:+.1%} vs replica 1, "
                  f"spread {spread:.1%}, {entry['wall_s']:.0f}s){flag}")
        else:
            entry["reason"] = rec.get("reason")
            print(f"  [control {replica:02d}] baseline FAILED: {rec.get('reason')}")
        self.baseline_controls.append(entry)
        return rec

    # -- main loop ----------------------------------------------------------
    def run(self):
        a = self.args
        print(f"Campaign {self.contract.campaign_id} | optimizer {a.optimizer} | "
              f"objective {a.objective}")
        print(f"  space: {self.space.n_cont} continuous + {self.space.n_cat} orientations")
        print(f"  fidelity {self.fidelity}: {self.events_total:,} events per candidate "
              f"({self.contract.fixed['n_positions']} sites x "
              f"{self.contract.fixed['n_replicas']} replicas x "
              f"{self.contract.events_per_sub_run(self.fidelity):,})")
        print(f"  {a.parallel} candidate(s) at a time x "
              f"{self.contract.fixed['max_workers']} sub-run workers = "
              f"{a.parallel * self.contract.fixed['max_workers']} cores, "
              f"{self.contract.fixed['total_mem_gb']:.0f} GB budget each")
        if hasattr(self.optimizer, "llm_report"):
            rep = self.optimizer.llm_report()
            print(f"  LLM: {'available' if rep['available'] else 'UNAVAILABLE'} -- "
                  f"{rep['detail']}")
        print(f"  ledger {self.ledger_path}")

        self.resume()
        best_seen = min([h["value"].value for h in self.history], default=None)
        stagnant = 0
        target = a.trials

        # ASYNCHRONOUS dispatch: keep `parallel` candidates in flight and refill
        # a slot the moment one finishes. Simulation cost varies by more than an
        # order of magnitude across this space -- a candidate that absorbs less
        # and lives longer burns far more CPU per event -- so a synchronous batch
        # runs at the speed of its slowest member and wastes most of the machine.
        stop_reason = None
        inflight = {}
        pool = ThreadPoolExecutor(max_workers=max(1, a.parallel))
        try:
            while True:
                if stop_reason is None:
                    if a.max_hours and (time.time() - self.started) / 3600 > a.max_hours:
                        stop_reason = f"wall-clock budget {a.max_hours} h exhausted"
                    elif a.max_events and self.events_used >= a.max_events:
                        stop_reason = f"event budget {a.max_events:,} exhausted"
                    elif a.patience and stagnant >= a.patience:
                        stop_reason = (f"no improvement beyond {a.tolerance:.0%} for "
                                       f"{stagnant} completions")
                # Top up the in-flight set.
                while (stop_reason is None
                       and len(inflight) < a.parallel
                       and len(self.history) + len(inflight) < target):
                    iteration = self.optimizer.iteration + 1
                    try:
                        points = self.optimizer.ask(1)
                    except RuntimeError as exc:
                        stop_reason = f"optimizer cannot propose: {exc}"
                        break
                    if not points:
                        # A synchronous optimizer with a full, unreported
                        # generation legitimately has nothing to propose. Idling
                        # the slot is the honest answer; filling it with a random
                        # draw and calling it CMA-ES is the P1 defect.
                        if not inflight:
                            stop_reason = ("optimizer has nothing to propose and "
                                           "nothing is in flight")
                        break
                    for p in points:
                        fut = pool.submit(self._evaluate_one, p, iteration)
                        inflight[fut] = (p, time.time())
                if not inflight:
                    break
                done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
                for fut in done:
                    point, t0 = inflight.pop(fut)
                    try:
                        rec = fut.result()
                    except Exception as exc:              # noqa: BLE001
                        rec = {"point": point, "status": "driver_error",
                               "reason": f"{type(exc).__name__}: {exc}"}
                    rec.setdefault("runtime_s", time.time() - t0)
                    best_seen, improved = self._record(rec, best_seen)
                    stagnant = 0 if improved else stagnant + 1
                    completed = len(self.history) + self.n_rejected + self.n_failed
                    # `--baseline-every N` means every N COMPLETED TRIALS, which
                    # is what the name says. It used to be multiplied by
                    # `--batch` (default 4), so `--baseline-every 24` silently
                    # meant "every 96" -- one control per 96-trial campaign, or
                    # none if the loop hit its target first. That is how P5
                    # ended up with zero controls recorded even after the
                    # machinery was fixed: the flag was doing a quarter of what
                    # it appeared to.
                    if a.baseline_every and completed % a.baseline_every == 0:
                        self.baseline_control(self.optimizer.iteration)
                    self.save_manifest()
                if len(self.history) >= target:
                    stop_reason = stop_reason or "trial budget reached"
                    if not inflight:
                        break
        finally:
            pool.shutdown(wait=True)
        if stop_reason:
            print(f"STOP: {stop_reason}")

        self.save_manifest()
        self.report()
        return 0 if self.history else 1

    # -- output -------------------------------------------------------------
    def manifest_path(self):
        d = os.path.join(HERE, "runs", self.contract.campaign_id)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"campaign_{self.args.optimizer}_{self.args.objective}.json")

    def _ledger_status_counts(self):
        """{status: n} for this campaign, straight from the ledger."""
        try:
            rows = self.ledger().conn.execute(
                "SELECT status, count(*) n FROM trials WHERE campaign_id=? "
                "GROUP BY status", (self.contract.campaign_id,)).fetchall()
            return {r["status"]: r["n"] for r in rows}
        except Exception as exc:                             # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    def save_manifest(self):
        bp, bv = self.optimizer.best()
        payload = {
            "campaign_id": self.contract.campaign_id,
            "contract_hash": self.contract.campaign_contract_hash(),
            "simulation_identity_hash": self.contract.simulation_identity_hash(),
            "contract_path": self.args.contract,
            "optimizer": self.args.optimizer,
            "optimizer_state": self.optimizer.state(),
            "objective": self.args.objective,
            "objective_params": self.objective_params,
            "fidelity": self.fidelity,
            "events_total_per_candidate": self.events_total,
            "seed": self.args.seed,
            "seed_bank": self.args.seed_bank,
            "n_observations": len(self.history),
            "n_rejected": self.n_rejected,
            "n_failed": self.n_failed,
            "events_used": self.events_used,
            "elapsed_s": round(time.time() - self.started, 1),
            # The values alone were what the "0% drift" claim was computed from;
            # the full records are what makes that claim checkable.
            "baseline_control_values": self.baseline_values,
            "baseline_controls": self.baseline_controls,
            "proposal_sources": (self.optimizer.provenance_counts()
                                 if hasattr(self.optimizer, "provenance_counts") else {}),
            "n_incomplete_or_failed": self.n_failed,
            "n_constraint_rejected": self.n_rejected,
            # Counted from the LEDGER, not from the in-process counters. A
            # campaign killed by the operator or the scheduler leaves rows the
            # driver never observed, so the manifests used to report zero
            # failures while the ledger held incomplete and operator-stopped
            # trials whose partial cost was charged to nothing (audit P7).
            "ledger_status_counts": self._ledger_status_counts(),
            "best": ({"point": {k: v for k, v in bp.items() if not k.startswith("_")},
                      "value": bv.value, "se": bv.se, "raw_total_qps": bv.raw,
                      "detail": bv.detail} if bp else None),
            "trials": [{"trial_id": h.get("trial_id"),
                        "value": h["value"].value, "se": h["value"].se,
                        "raw": h["value"].raw, "cached": h.get("cached", False),
                        "resumed": h.get("resumed", False),
                        "point": {k: v for k, v in h["point"].items()
                                  if not k.startswith("_")}}
                       for h in self.history],
        }
        if hasattr(self.optimizer, "llm_report"):
            payload["llm"] = self.optimizer.llm_report()
        tmp = self.manifest_path() + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(payload, handle, indent=1, default=str)
        os.replace(tmp, self.manifest_path())

    def report(self):
        bp, bv = self.optimizer.best()
        print("\n" + "=" * 78)
        print(f"Campaign {self.contract.campaign_id} [{self.args.optimizer}] finished: "
              f"{len(self.history)} observations, {self.n_rejected} gate rejections, "
              f"{self.n_failed} failures, {self.events_used / 1e6:.0f}e6 events, "
              f"{(time.time() - self.started) / 60:.0f} min")
        if bp is None:
            print("No observation was recorded.")
            return
        print(f"Best {self.args.objective} = {bv.value:.6e}"
              + (f" +- {bv.se:.2e}" if bv.se else "")
              + f"   (raw total_QPs = {bv.raw:.0f})")
        base = [h for h in self.history
                if all(abs(h["point"][v.name] - v.baseline) < 1e-12
                       for v in self.space.variables)]
        if base:
            b = base[0]["value"].value
            print(f"Baseline (Si/Nb/Cu-equivalent) = {b:.6e}  -> "
                  f"{100 * (bv.value - b) / b:+.1f}%")
        print("Best point:")
        for v in self.space.variables:
            print(f"  {v.name:22s} {bv.detail.get(v.name, bp[v.name]):12.6g} {v.unit}")
        print(f"  {'miller':22s} {str(bp['miller']):>12s}")
        if hasattr(self.optimizer, "llm_report"):
            print("LLM:", json.dumps(self.optimizer.llm_report()))
        print(f"Manifest: {self.manifest_path()}")
        print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contract", default=os.path.join(HERE, "stage4_config.yaml"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "stage4_trials.sqlite"))
    ap.add_argument("--optimizer", default="bo_gp", choices=optimizers.available()
                    + ["llm_agent", "llm_bo"])
    ap.add_argument("--objective", default="total_qps_per_primary")
    ap.add_argument("--objective-params", default=None,
                    help='JSON, e.g. \'{"lam": 2.0}\'')
    ap.add_argument("--optimizer-params", default=None,
                    help='JSON, e.g. \'{"n_init": 32}\'')
    ap.add_argument("--trials", type=int, default=60, help="target observations")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--parallel", type=int, default=4,
                    help="candidates evaluated concurrently")
    ap.add_argument("--workers", type=int, default=None,
                    help="TOTAL sub-run workers across all concurrent candidates")
    ap.add_argument("--total-mem-gb", type=float, default=None)
    ap.add_argument("--fidelity", default=None, choices=(None, "S", "M", "L"))
    ap.add_argument("--events", type=int, default=None,
                    help="override events per candidate for this fidelity")
    ap.add_argument("--positions", type=int, default=None)
    ap.add_argument("--replicas", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=None, help="per sub-run seconds")
    ap.add_argument("--seed", type=int, default=1, help="optimizer seed (not physics)")
    ap.add_argument("--seed-bank", type=int, default=None,
                    help="physics seed bank; hold one out for confirmation")
    ap.add_argument("--tag", default=None, help="campaign_id suffix")
    ap.add_argument("--patience", type=int, default=0,
                    help="stop after N batches without improvement (0 = never)")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="relative improvement that counts as progress")
    ap.add_argument("--max-events", type=int, default=None)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--baseline-every", type=int, default=0,
                    help="re-evaluate the frozen baseline every N COMPLETED "
                         "TRIALS (0 = never). Each control executes fresh under "
                         "its own control_replica_id, is excluded from the "
                         "optimizer's observations and from the event-efficiency "
                         "curve, and leaves its own inspectable ledger row.")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--llm-host", default=None)
    args = ap.parse_args()
    return Campaign(args).run()


if __name__ == "__main__":
    sys.exit(main())
