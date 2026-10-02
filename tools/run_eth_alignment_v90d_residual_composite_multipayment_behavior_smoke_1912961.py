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


v90d = load_sibling(
    "eth_v90d_for_residual_composite_multipayment",
    "run_eth_repair_v90d_min_legal_incremental_repair_1912961.py",
)
recoverability = load_sibling(
    "recursive_recoverability_for_residual_multipayment",
    "recoverability.py",
    "eth_repair_modular",
)
router_policy = load_sibling(
    "residual_multipayment_router_policy",
    "residual_multipayment.py",
    "eth_repair_modular",
)
v90 = v90d.v90
v80 = v90d.v80
v38 = v90d.v38
v1 = v90d.v1


class V90DResidualCompositeMultipayment(v90d.V90DMinLegalIncremental):
    def __init__(self, *args, **kwargs):
        self.residualMultipaymentPolicy = router_policy.ResidualCompositeMultipaymentRouterPolicyV1()
        self.recursivePolicy = recoverability.RecursiveCompositeCurrentCoordinateRecoverabilityPolicy()
        self.residualMultipaymentParents: set[int] = set()
        self.residualMultipaymentKeys: list[str] = []
        self.residualMultipaymentDecisions: list[dict] = []
        self.alignmentFillEvents: list[dict] = []
        self._alignmentFillSeen: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    def _context(self, t: int, qv, proposal):
        if proposal is None or str(proposal[3]) != "REPAIR":
            return None
        side = str(proposal[0])
        if side not in ("UP", "DOWN"):
            return None
        parent = getattr(self, "repairParent", None)
        if not isinstance(parent, dict):
            return None
        parent_id = int(parent.get("id") or -1)
        if parent_id not in set(getattr(self, "v90dPaidParents", set()) or set()):
            return None
        self._refresh_carrier_ledger(int(t))
        prior_payment_qty = sum(
            float(self.carrierLedger.get(key, {}).get("actualFilled") or 0.0)
            for key in getattr(self, "v90dSubmitKeys", []) or []
            if int(self.carrierLedger.get(key, {}).get("parentId") or -1) == parent_id
        )
        if prior_payment_qty <= EPS:
            return None
        authoritative = self.auth_inv()
        opposite = "DOWN" if side == "UP" else "UP"
        residual = max(0.0, float(authoritative[opposite]) - float(authoritative[side]))
        if residual <= EPS or not qv or qv[side].get("bid") is None:
            return None
        price = float(qv[side]["bid"])
        physical_qty = 1.0 / price if price > EPS else math.inf
        if not math.isfinite(physical_qty):
            return None
        repair_allocation = min(residual, physical_qty)
        overflow = max(0.0, physical_qty - residual)
        floor_before, up_qty, down_qty, cost = self._raw_floor()
        hyp_up = float(up_qty) + (physical_qty if side == "UP" else 0.0)
        hyp_down = float(down_qty) + (physical_qty if side == "DOWN" else 0.0)
        hyp_cost = float(cost) + physical_qty * price
        floor_after = min(hyp_up, hyp_down) - hyp_cost
        recursive = self.recursivePolicy.evaluate(
            recoverability.RecursiveCompositeRecoverabilityContext(
                floor_before_expand=float(floor_before),
                floor_after_expand=float(floor_after),
                debt_side=side,
                repair_debt=abs(hyp_up - hyp_down),
                up_bid=float(qv["UP"].get("bid") or 0.0),
                down_bid=float(qv["DOWN"].get("bid") or 0.0),
                max_carriers=4,
                max_venue_qty=12.0,
            )
        )
        truth_inventory = getattr(self, "truthInv", authoritative)
        truth_role = "REPAIR" if float(truth_inventory[side]) < float(truth_inventory[opposite]) - EPS else "EXPAND"
        same_parent_live = any(
            int(entry.get("parentId") or -1) == parent_id and float(remaining) > EPS
            for _, entry, remaining in self.lane_unresolved("REPAIR")
        )
        policy_context = router_policy.ResidualCompositeMultipaymentContext(
            t=int(t),
            seconds_left=(int(self.capEnd) - int(t)) / 1000.0,
            responsibility_id=parent_id,
            responsibility_side=side,
            authorized_role=str(proposal[3]),
            physical_truth_role=truth_role,
            prior_confirmed_payments=1,
            authoritative_residual_debt=residual,
            candidate_physical_qty=physical_qty,
            candidate_repair_allocation=repair_allocation,
            candidate_overflow_allocation=overflow,
            shared_remaining_budget=12.0,
            ledger_snapshot_current=True,
            pending_sibling_reconciliation=False,
            same_parent_live_carrier=same_parent_live,
            active_already_owned=parent_id in getattr(self, "activeByParent", {}),
            venue_legal=physical_qty <= 12.0 + EPS,
            recursive_current_coordinate_recoverable=bool(recursive.recoverable),
        )
        decision = self.residualMultipaymentPolicy.evaluate(policy_context)
        return {
            "t": int(t),
            "parentId": parent_id,
            "parentBornAt": int(parent.get("bornAt") or -1),
            "side": side,
            "price": price,
            "physicalQty": physical_qty,
            "residualDebt": residual,
            "repairAllocationIfFull": repair_allocation,
            "overflowAllocationIfFull": overflow,
            "priorV90DFillQty": prior_payment_qty,
            "floorBefore": float(floor_before),
            "floorAfterHypCarrier": float(floor_after),
            "truthRole": truth_role,
            "recursiveDecision": {
                "recoverable": recursive.recoverable,
                "reason": recursive.reason,
                "recoveredStep": recursive.recovered_step,
                "terminalFloor": recursive.terminal_floor,
                "terminalDebt": recursive.terminal_debt,
                "path": list(recursive.path),
            },
            "policyDecision": {
                "allow": decision.allow,
                "physicalQty": decision.physical_qty,
                "reason": decision.reason,
            },
        }

    def _submit_authorized(self, t, qv, proposal, roles_this_tick):
        context = self._context(int(t), qv, proposal)
        if context is None:
            return super()._submit_authorized(t, qv, proposal, roles_this_tick)
        parent_id = int(context["parentId"])
        if parent_id in self.residualMultipaymentParents:
            if len(self.residualMultipaymentDecisions) < 240:
                self.residualMultipaymentDecisions.append(
                    {**context, "submit": False, "reason": "ONE_ADDITIONAL_PAYMENT_PER_PARENT_ALREADY_USED"}
                )
            return super()._submit_authorized(t, qv, proposal, roles_this_tick)
        if not context["policyDecision"]["allow"]:
            if len(self.residualMultipaymentDecisions) < 240:
                self.residualMultipaymentDecisions.append(
                    {**context, "submit": False, "reason": context["policyDecision"]["reason"]}
                )
            return super()._submit_authorized(t, qv, proposal, roles_this_tick)
        objective_id = proposal[4]
        self._pendingAuthorizedRole = "REPAIR"
        self._pendingAuthorizedObjectiveId = objective_id
        self._pendingParentId = parent_id
        self._pendingLane = "ALIGNMENT_V90D_RESIDUAL_COMPOSITE_MULTIPAYMENT"
        before_n = self.n
        ok = self.submit(
            int(t),
            context["side"],
            float(context["price"]),
            float(context["physicalQty"]),
        )
        row = {
            **context,
            "submit": bool(ok),
            "reason": "RESIDUAL_COMPOSITE_MULTIPAYMENT_SUBMIT" if ok else "PHYSICAL_SUBMIT_REJECTED",
        }
        if ok:
            key = f'{context["side"]}_{before_n}'
            row["key"] = key
            roles_this_tick.add("REPAIR")
            self.residualMultipaymentParents.add(parent_id)
            self.residualMultipaymentKeys.append(key)
            self.packageRepairEval += 1
            self.packageRepairAccept += 1
            self.v84CompositeSubmits += 1
            self.v84Composite[key] = {
                "key": key,
                "side": context["side"],
                "parentId": parent_id,
                "price": context["price"],
                "submittedQty": context["physicalQty"],
                "gapAtSubmit": context["residualDebt"],
                "fillSeen": 0.0,
                "repairAllocated": 0.0,
                "overflowAllocated": 0.0,
                "overflowBornAt": None,
                "overflowDebt": 0.0,
                "overflowPaid": 0.0,
                "lane": "ALIGNMENT_V90D_RESIDUAL_COMPOSITE_MULTIPAYMENT",
            }
            reserve_builder = getattr(self, "reserveBuilder", None)
            if isinstance(reserve_builder, dict):
                reserve_builder.setdefault("repairKeys", []).append(key)
        self.residualMultipaymentDecisions.append(row)
        return bool(ok)

    def process(self, t):
        super().process(t)
        self._refresh_carrier_ledger(int(t))
        for key, entry in self.carrierLedger.items():
            current = float(entry.get("actualFilled") or 0.0)
            previous = float(self._alignmentFillSeen.get(str(key), 0.0))
            if current <= previous + EPS:
                continue
            self.alignmentFillEvents.append(
                {
                    "t": int(t),
                    "key": str(key),
                    "side": entry.get("side"),
                    "objectiveRole": entry.get("objectiveRole"),
                    "parentId": entry.get("parentId"),
                    "fillDelta": current - previous,
                    "actualFilled": current,
                    "floorAfterReceipt": float(self._raw_floor()[0]),
                    "absGapAfterReceipt": abs(float(self.auth_inv()["UP"]) - float(self.auth_inv()["DOWN"])),
                }
            )
            self._alignmentFillSeen[str(key)] = current

    def run_candidate(self, models, winner):
        result = self.run_v90d(models, winner)
        result.update(
            {
                "residualMultipaymentParents": sorted(self.residualMultipaymentParents),
                "residualMultipaymentKeys": self.residualMultipaymentKeys,
                "residualMultipaymentDecisions": self.residualMultipaymentDecisions,
                "alignmentFillEvents": self.alignmentFillEvents,
            }
        )
        return result


def make_simulator(cls, tape, models, life, capability, timing, economic, price, surplus, teacher, generation_teacher):
    return cls(
        tape,
        "BOOK_IMBALANCE",
        models,
        life,
        0,
        0,
        capability=capability,
        timing=timing,
        economic=economic,
        price_envelope=price,
        surplus_value=surplus,
        teacher=teacher,
        genTeacher=generation_teacher,
        policy_profile=v80.economic_v1_profile(),
    )


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
    output_path = (
        Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json"
        if args.output.upper() == "AUTO"
        else Path(args.output)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_v90d_residual_multi_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "V90D_RESIDUAL_COMPOSITE_MULTIPAYMENT", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "V90D_RESIDUAL_COMPOSITE_MULTIPAYMENT_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {
            int(row["marketId"]): row
            for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]
        }
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        tape = temporary / "tapes" / f"{MID}.json.xz"
        baseline = make_simulator(
            v90d.V90DMinLegalIncremental,
            tape,
            models,
            life,
            capability,
            timing,
            economic,
            price,
            surplus,
            teacher,
            generation_teacher,
        )
        try:
            baseline_result = baseline.run_v90d(models, cohort[MID]["winner"])
        finally:
            baseline.close()
        candidate = make_simulator(
            V90DResidualCompositeMultipayment,
            tape,
            models,
            life,
            capability,
            timing,
            economic,
            price,
            surplus,
            teacher,
            generation_teacher,
        )
        try:
            candidate_result = candidate.run_candidate(models, cohort[MID]["winner"])
        finally:
            candidate.close()
        keys = set(candidate_result.get("residualMultipaymentKeys", []) or [])
        fill_events = candidate_result.get("alignmentFillEvents", []) or []
        multipayment_fills = [row for row in fill_events if str(row.get("key")) in keys]
        first_multi_fill_t = min((int(row["t"]) for row in multipayment_fills), default=None)
        later_repair_fills = [
            row
            for row in fill_events
            if first_multi_fill_t is not None
            and int(row.get("t") or -1) > first_multi_fill_t
            and str(row.get("objectiveRole")) == "REPAIR"
        ]
        safety = v90.safety_summary(candidate_result)
        safety_zero = all(float(value) <= EPS for value in safety.values())
        conservation = abs(
            float(candidate_result.get("v84CompositeFillQty") or 0.0)
            - float(candidate_result.get("v84RepairAllocatedQty") or 0.0)
            - float(candidate_result.get("v84OverflowAllocatedQty") or 0.0)
        ) <= 1e-7
        submitted = [row for row in candidate_result.get("residualMultipaymentDecisions", []) or [] if row.get("submit")]
        gates = {
            "routerPolicyExercised": bool(submitted),
            "sameParentSecondPaymentSubmitted": bool(submitted)
            and int(submitted[0]["parentId"]) in set(candidate_result.get("v90dPaidParents", []) or []),
            "authorizedAndTruthRolesBothRepair": bool(submitted) and submitted[0].get("truthRole") == "REPAIR",
            "residualDebtPositiveAtSubmit": bool(submitted) and float(submitted[0].get("residualDebt") or 0.0) > EPS,
            "physicalMultipaymentFillObserved": bool(multipayment_fills),
            "laterRepairFillObserved": bool(later_repair_fills),
            "physicalAllocationConservation": conservation,
            "safetyZero": safety_zero,
        }
        if not safety_zero or not conservation:
            decision = "REJECT_RESIDUAL_MULTIPAYMENT_SAFETY_OR_CONSERVATION"
        elif not submitted:
            decision = "ROUTER_POLICY_NOT_EXERCISED_DIAGNOSE"
        elif not multipayment_fills:
            decision = "EXECUTION_INCONCLUSIVE_PASSIVE_MULTIPAYMENT_SUBMIT_NO_FILL"
        elif not later_repair_fills:
            decision = "PARTIAL_LIFECYCLE_MULTIPAYMENT_FILL_NO_LATER_REPAIR"
        else:
            decision = "FUNCTIONAL_PASS_TO_ONE_FRESH_REPLICATION"
        output = {
            "version": "V90D_RESIDUAL_COMPOSITE_MULTIPAYMENT_BEHAVIOR_SMOKE_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": MID,
            "winnerPostHocOnly": cohort[MID]["winner"],
            "decision": decision,
            "gates": gates,
            "baselineV90D": {
                "fills": baseline_result.get("actualFillEvents"),
                "floor": baseline_result.get("floor"),
                "pnlDiagnosticOnly": baseline_result.get("pnlDiagnosticOnly"),
                "v90dFillQty": baseline_result.get("v90dFillQty"),
                "terminalResidualDebt": baseline_result.get("v90dTerminalAbsGap"),
            },
            "candidate": {
                "fills": candidate_result.get("actualFillEvents"),
                "floor": candidate_result.get("floor"),
                "pnlDiagnosticOnly": candidate_result.get("pnlDiagnosticOnly"),
                "v90dFillQty": candidate_result.get("v90dFillQty"),
                "terminalResidualDebt": candidate_result.get("v90dTerminalAbsGap"),
                "multipaymentKeys": sorted(keys),
                "multipaymentFills": multipayment_fills,
                "laterRepairFills": later_repair_fills,
                "semanticRounds": candidate_result.get("v70dSemanticRounds"),
                "overflowAllocated": candidate_result.get("v84OverflowAllocatedQty"),
                "overflowPaid": candidate_result.get("v84OverflowPaidQty"),
                "overflowRemaining": candidate_result.get("v84OverflowRemainingQty"),
            },
            "safety": safety,
            "routerDecisions": candidate_result.get("residualMultipaymentDecisions", []),
            "alignmentFillEvents": fill_events,
            "allocationEvents": candidate_result.get("v84Events", []),
            "transitionEvents": candidate_result.get("transitionEvents", []),
            "boundary": [
                "single development market realistic HFT",
                "only RepairExecutionRouter residual-composite multipayment decision changed",
                "authorized role and physical truth role must both be Repair",
                "one additional passive venue-min carrier per already-paid parent",
                "AllocationLedger V2 Repair-first overflow-second accounting frozen",
                "ResponsibilityTransition, Manager, ownership, pExpand, price, qty, and timing frozen",
                "<=180s overflow remains blocked",
                "strict-past OUR state only",
                "no Target runtime input",
                "no dream fills",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "decision": decision,
                    "gates": gates,
                    "baseline": output["baselineV90D"],
                    "candidate": output["candidate"],
                    "safety": safety,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
