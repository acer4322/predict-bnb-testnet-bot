from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import joblib

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EPS = 1e-9
MID = 1912961


def load_sibling(name: str, filename: str, fallback: str | None = None):
    path = Path(__file__).with_name(filename)
    if not path.exists() and fallback:
        path = Path(__file__).with_name(fallback) / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_sibling(
    "residual_multipayment_for_active_handoff",
    "run_eth_alignment_v90d_residual_composite_multipayment_behavior_smoke_1912961.py",
)
handoff = load_sibling(
    "residual_active_handoff_policy_runtime",
    "residual_active_handoff.py",
    "eth_repair_modular",
)
v90 = base.v90
v80 = base.v80
v38 = base.v38
v1 = base.v1


class ResidualRepairActiveHandoffBehavior(base.V90DResidualCompositeMultipayment):
    def __init__(self, *args, **kwargs):
        self.residualActivePolicy = handoff.ResidualRepairActiveHandoffPolicyV1()
        self.residualActiveParents: set[int] = set()
        self.residualActiveKeys: list[str] = []
        self.residualActiveEvents: list[dict] = []
        self._multipaymentSubmittedAt: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    def _submit_authorized(self, t, qv, proposal, roles_this_tick):
        before = set(self.residualMultipaymentKeys)
        result = super()._submit_authorized(t, qv, proposal, roles_this_tick)
        for key in set(self.residualMultipaymentKeys) - before:
            self._multipaymentSubmittedAt[str(key)] = int(t)
        return result

    def _maybe_residual_active_handoff(self, t: int) -> bool:
        if not self.residualMultipaymentKeys:
            return False
        self._refresh_carrier_ledger(int(t))
        qv = v1.quotes(self.book)
        if not qv:
            return False
        for passive_key in list(self.residualMultipaymentKeys):
            entry = self.carrierLedger.get(passive_key)
            if not entry:
                continue
            parent_id = int(entry.get("parentId") or -1)
            if parent_id < 0 or parent_id in self.residualActiveParents:
                continue
            side = str(entry.get("side") or "")
            if side not in ("UP", "DOWN") or side not in qv or qv[side].get("ask") is None:
                continue
            actual_filled = float(entry.get("actualFilled") or 0.0)
            try:
                remaining = float(self._ledger_remaining(entry))
            except Exception:
                remaining = max(0.0, float(entry.get("submittedQty") or 0.0) - actual_filled)
            order = self.orders.get(passive_key)
            try:
                snap = self.snap(order) if order is not None else {}
            except Exception:
                snap = {}
            venue_live = bool(order is not None and v1.live(snap.get("status")))
            release_confirmed = bool(entry.get("terminalConfirmed") and remaining <= EPS)
            same_parent_unresolved = [
                str(key)
                for key, other, rem in self.unresolved()
                if str(key) != str(passive_key)
                and int(other.get("parentId") or -1) == parent_id
                and float(rem) > EPS
            ]
            authoritative = self.auth_inv()
            opposite = "DOWN" if side == "UP" else "UP"
            residual = max(0.0, float(authoritative[opposite]) - float(authoritative[side]))
            truth_inventory = getattr(self, "truthInv", authoritative)
            truth_role = "REPAIR" if float(truth_inventory[side]) < float(truth_inventory[opposite]) - EPS else "EXPAND"
            ask = float(qv[side]["ask"])
            legal = 1.0 / ask if ask > EPS else math.inf
            repair_allocation = min(residual, legal) if math.isfinite(legal) else 0.0
            overflow = max(0.0, legal - residual) if math.isfinite(legal) else math.inf
            floor_before, up_qty, down_qty, cost = self._raw_floor()
            recursive = None
            if math.isfinite(legal):
                hyp_up = float(up_qty) + (legal if side == "UP" else 0.0)
                hyp_down = float(down_qty) + (legal if side == "DOWN" else 0.0)
                hyp_cost = float(cost) + legal * ask
                hyp_floor = min(hyp_up, hyp_down) - hyp_cost
                recursive = self.recursivePolicy.evaluate(
                    base.recoverability.RecursiveCompositeRecoverabilityContext(
                        floor_before_expand=float(floor_before),
                        floor_after_expand=float(hyp_floor),
                        debt_side=side,
                        repair_debt=abs(hyp_up - hyp_down),
                        up_bid=float(qv["UP"].get("bid") or 0.0),
                        down_bid=float(qv["DOWN"].get("bid") or 0.0),
                        max_carriers=4,
                        max_venue_qty=12.0,
                    )
                )
            ctx = handoff.ResidualRepairActiveHandoffContext(
                t=int(t),
                seconds_left=(int(self.capEnd) - int(t)) / 1000.0,
                responsibility_id=parent_id,
                responsibility_side=side,
                prior_confirmed_payment_qty=float(
                    sum(
                        float(self.carrierLedger.get(key, {}).get("actualFilled") or 0.0)
                        for key in getattr(self, "v90dSubmitKeys", []) or []
                        if int(self.carrierLedger.get(key, {}).get("parentId") or -1) == parent_id
                    )
                ),
                authoritative_residual_debt=residual,
                physical_truth_role=truth_role,
                passive_release_confirmed=release_confirmed,
                passive_live=venue_live,
                same_parent_unresolved_carriers=len(same_parent_unresolved),
                active_already_owned=parent_id in getattr(self, "activeByParent", {}),
                live_ask=ask,
                legal_physical_qty=legal,
                candidate_repair_allocation=repair_allocation,
                candidate_overflow_allocation=overflow,
                recursive_current_coordinate_recoverable=bool(recursive and recursive.recoverable),
            )
            decision = self.residualActivePolicy.evaluate(ctx)
            event = {
                "t": int(t),
                "event": "RESIDUAL_REPAIR_ACTIVE_HANDOFF_CHECK",
                "parentId": parent_id,
                "passiveKey": passive_key,
                "side": side,
                "passiveActualFill": actual_filled,
                "passiveRemaining": remaining,
                "passiveVenueLive": venue_live,
                "passiveReleaseConfirmed": release_confirmed,
                "sameParentUnresolvedKeys": same_parent_unresolved,
                "residualDebt": residual,
                "truthRole": truth_role,
                "liveAsk": ask,
                "legalPhysicalQty": legal,
                "repairAllocationIfFull": repair_allocation,
                "overflowAllocationIfFull": overflow,
                "recursiveRecoverable": bool(recursive and recursive.recoverable),
                "decision": decision.reason,
                "allow": decision.allow,
            }
            prev = self.residualActiveEvents[-1] if self.residualActiveEvents else None
            if prev is None or prev.get("decision") != event["decision"] or event["allow"]:
                self.residualActiveEvents.append(dict(event))
            if not decision.allow:
                continue
            oid = entry.get("objectiveId") or (self.repairLaneObjective.get("id") if getattr(self, "repairLaneObjective", None) else None)
            self.hardConfirmed.add(parent_id)
            self.hardEventConfirmedCount += 1
            ok = self._submit_active(int(t), parent_id, passive_key, side, ask, float(decision.physical_qty), oid)
            if not ok:
                self.residualActiveEvents.append({**event, "event": "RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT", "submitOk": False})
                return False
            active_key = str(self.activeByParent[parent_id]["key"])
            self.residualActiveParents.add(parent_id)
            self.residualActiveKeys.append(active_key)
            self.v84CompositeSubmits += 1
            self.v84Composite[active_key] = {
                "key": active_key,
                "side": side,
                "parentId": parent_id,
                "price": ask,
                "submittedQty": float(decision.physical_qty),
                "gapAtSubmit": residual,
                "fillSeen": 0.0,
                "repairAllocated": 0.0,
                "overflowAllocated": 0.0,
                "overflowBornAt": None,
                "overflowDebt": 0.0,
                "overflowPaid": 0.0,
                "lane": "ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF",
            }
            self.residualActiveEvents.append({**event, "event": "RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT", "submitOk": True, "activeKey": active_key})
            return True
        return False

    def process(self, t):
        super().process(t)
        self._maybe_residual_active_handoff(int(t))

    def cancel_expired(self, t):
        super().cancel_expired(t)
        self._maybe_residual_active_handoff(int(t))

    def run_alignment(self, models, winner):
        result = self.run_candidate(models, winner)
        result.update(
            {
                "residualActiveParents": sorted(self.residualActiveParents),
                "residualActiveKeys": list(self.residualActiveKeys),
                "residualActiveEvents": self.residualActiveEvents[:320],
                "residualActivePolicy": self.residualActivePolicy.name,
            }
        )
        return result


def main():
    parser = argparse.ArgumentParser()
    for name in [
        "bundle",
        "lifecycle-model",
        "capability-model",
        "dagger-cache",
        "timing-model",
        "economic-model",
        "price-model",
        "surplus-model",
        "v44-model",
        "v47-model",
    ]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--market-id", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.market_id != MID:
        raise ValueError(args.market_id)
    output_path = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if args.output.upper() == "AUTO" else Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_residual_active_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "RESIDUAL_REPAIR_ACTIVE_HANDOFF", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "RESIDUAL_REPAIR_ACTIVE_HANDOFF_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {int(row["marketId"]): row for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]}
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        tape = temporary / "tapes" / f"{MID}.json.xz"
        baseline = base.make_simulator(base.V90DResidualCompositeMultipayment, tape, models, life, capability, timing, economic, price, surplus, teacher, generation_teacher)
        try:
            baseline_result = baseline.run_candidate(models, cohort[MID]["winner"])
        finally:
            baseline.close()
        candidate = base.make_simulator(ResidualRepairActiveHandoffBehavior, tape, models, life, capability, timing, economic, price, surplus, teacher, generation_teacher)
        try:
            candidate_result = candidate.run_alignment(models, cohort[MID]["winner"])
        finally:
            candidate.close()

        active_keys = set(candidate_result.get("residualActiveKeys", []) or [])
        fills = candidate_result.get("alignmentFillEvents", []) or []
        v90d_keys = set(candidate_result.get("v90dSubmitKeys", []) or [])
        v90d_fills = [row for row in fills if str(row.get("key")) in v90d_keys and float(row.get("fillDelta") or 0.0) > EPS]
        active_fills = [row for row in fills if str(row.get("key")) in active_keys and float(row.get("fillDelta") or 0.0) > EPS]
        first_v90d_t = min((int(row["t"]) for row in v90d_fills), default=None)
        first_active_t = min((int(row["t"]) for row in active_fills), default=None)
        later_repair_fills = [
            row for row in fills
            if first_active_t is not None
            and int(row.get("t") or -1) > first_active_t
            and str(row.get("objectiveRole")) == "REPAIR"
            and str(row.get("key")) not in active_keys
        ]
        active_states = [state for state in (candidate_result.get("v84CompositeState") or {}).values() if state.get("lane") == "ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF"]
        repair_bound = all(float(state.get("repairAllocated") or 0.0) <= float(state.get("gapAtSubmit") or 0.0) + 1e-7 for state in active_states)
        conservation = abs(float(candidate_result.get("v84CompositeFillQty") or 0.0) - float(candidate_result.get("v84RepairAllocatedQty") or 0.0) - float(candidate_result.get("v84OverflowAllocatedQty") or 0.0)) <= 1e-7
        safety = v90.safety_summary(candidate_result)
        safety_zero = all(float(value) <= EPS for value in safety.values())
        submit_events = [row for row in candidate_result.get("residualActiveEvents", []) or [] if row.get("event") == "RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT" and row.get("submitOk")]
        residual_at_submit = float(submit_events[0].get("residualDebt") or 0.0) if submit_events else 0.0
        overflow_at_submit = float(submit_events[0].get("overflowAllocationIfFull") or 0.0) if submit_events else 0.0
        active_fill_qty = sum(float(row.get("fillDelta") or 0.0) for row in active_fills)
        overflow_allocated = sum(float(state.get("overflowAllocated") or 0.0) for state in active_states)
        gates = {
            "v90dPartialRepairPhysicalFillObserved": bool(v90d_fills),
            "positiveResidualDebtAtActiveAdmission": residual_at_submit > EPS,
            "activeHandoffPolicyExercised": bool(submit_events),
            "physicalCarrierAuthorizedAsRepair": bool(submit_events) and submit_events[0].get("truthRole") == "REPAIR",
            "activePhysicalFillObserved": active_fill_qty > EPS,
            "repairFirstAllocationBoundedByResidualDebt": repair_bound,
            "overflowBornFromConfirmedPhysicalFill": overflow_allocated > EPS,
            "laterRepairPhysicalFillObserved": bool(later_repair_fills),
            "strictLifecycleOrder": first_v90d_t is not None and first_active_t is not None and first_v90d_t < first_active_t and (not later_repair_fills or first_active_t < min(int(row["t"]) for row in later_repair_fills)),
            "physicalAllocationConservation": conservation,
            "safetyZero": safety_zero,
        }
        if not safety_zero or not conservation or not repair_bound:
            decision = "REJECT_RESIDUAL_ACTIVE_HANDOFF_SAFETY_OR_ACCOUNTING"
        elif not submit_events:
            decision = "ACTIVE_HANDOFF_POLICY_NOT_EXERCISED_DIAGNOSE"
        elif not active_fills:
            decision = "EXECUTION_INCONCLUSIVE_ACTIVE_HANDOFF_SUBMIT_NO_FILL"
        elif overflow_allocated <= EPS:
            decision = "PARTIAL_REPAIR_FILL_NO_OVERFLOW_BIRTH"
        elif not later_repair_fills:
            decision = "PARTIAL_CYCLE_ACTIVE_COMPOSITE_FILL_NO_LATER_REPAIR"
        else:
            decision = "FUNCTIONAL_FULL_CYCLE_PASS_TO_ONE_FRESH_REPLICATION"
        output = {
            "version": "RESIDUAL_REPAIR_ACTIVE_HANDOFF_BEHAVIOR_SMOKE_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": MID,
            "winnerPostHocOnly": cohort[MID]["winner"],
            "decision": decision,
            "gates": gates,
            "baseline": {
                "fills": baseline_result.get("actualFillEvents"),
                "floor": baseline_result.get("floor"),
                "pnlDiagnosticOnly": baseline_result.get("pnlDiagnosticOnly"),
                "terminalResidualDebt": baseline_result.get("v90dTerminalAbsGap"),
            },
            "candidate": {
                "fills": candidate_result.get("actualFillEvents"),
                "floor": candidate_result.get("floor"),
                "pnlDiagnosticOnly": candidate_result.get("pnlDiagnosticOnly"),
                "terminalResidualDebt": candidate_result.get("v90dTerminalAbsGap"),
                "activeSubmitCount": len(submit_events),
                "activeFillQty": active_fill_qty,
                "overflowAllocatedQty": overflow_allocated,
                "semanticRounds": candidate_result.get("v70dSemanticRounds"),
            },
            "safety": safety,
            "activeHandoffEvents": candidate_result.get("residualActiveEvents", []),
            "activeCompositeStates": active_states,
            "alignmentFillEvents": fills,
            "laterRepairFills": later_repair_fills,
            "allocationEvents": candidate_result.get("v84Events", []),
            "transitionEvents": candidate_result.get("transitionEvents", []),
            "boundary": [
                "single consumed development market realistic HFT",
                "single module addition: residual Repair active execution handoff after passive terminal release",
                "physical carrier remains Repair while authoritative residual debt exists",
                "Repair-first confirmed-fill allocation; only confirmed overflow may birth next responsibility",
                "no direct pure-Expand authorization while truth role is Repair",
                "recursive current-coordinate recoverability only guards overflow geometry",
                "<=180s boundary frozen for this module",
                "Manager, pExpand, V90D tranche, ResponsibilityTransition, AllocationLedger and ownership semantics otherwise frozen",
                "strict-past OUR state only",
                "no Target runtime input",
                "no dream fills",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "gates": gates, "baseline": output["baseline"], "candidate": output["candidate"], "safety": safety}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
