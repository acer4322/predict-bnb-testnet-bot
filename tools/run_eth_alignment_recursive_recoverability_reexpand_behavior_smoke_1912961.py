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


def load_sibling(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    if not path.exists() and filename == "recoverability.py":
        path = Path(__file__).with_name("eth_repair_modular") / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v90d = load_sibling(
    "eth_v90d_for_recursive_reexpand_smoke",
    "run_eth_repair_v90d_min_legal_incremental_repair_1912961.py",
)
recoverability = load_sibling("recursive_recoverability_policy", "recoverability.py")
v90 = v90d.v90
v80 = v90d.v80
v38 = v90d.v38
v1 = v90d.v1


class RecursiveRecoverabilityResidualDebtReexpand(v90d.V90DMinLegalIncremental):
    """Change only post-V90D residual-debt recoverability interpretation."""

    def __init__(self, *args, **kwargs):
        self.recursivePolicy = recoverability.RecursiveCompositeCurrentCoordinateRecoverabilityPolicy()
        self.recursiveChecks = 0
        self.recursivePasses = 0
        self.recursiveEvents: list[dict] = []
        self.alignmentFillEvents: list[dict] = []
        self._alignmentFillSeen: dict[str, float] = {}
        self._firstV90DFillObservedAt: int | None = None
        super().__init__(*args, **kwargs)

    def _confirmed_v90d_fill_qty(self) -> float:
        return sum(
            float(self.carrierLedger.get(key, {}).get("actualFilled") or 0.0)
            for key in getattr(self, "v90dSubmitKeys", []) or []
        )

    def _residual_activation(self, t: int) -> dict | None:
        self._refresh_carrier_ledger(int(t))
        filled = self._confirmed_v90d_fill_qty()
        if filled <= EPS:
            return None
        repair_parent = getattr(self, "repairParent", None)
        if not isinstance(repair_parent, dict):
            return None
        parent_id = int(repair_parent.get("id") or -1)
        if parent_id not in set(getattr(self, "v90dPaidParents", set()) or set()):
            return None
        inventory = self.auth_inv()
        residual = abs(float(inventory["UP"]) - float(inventory["DOWN"]))
        if residual <= EPS:
            return None
        return {
            "parentId": parent_id,
            "v90dFilledQty": filled,
            "residualDebt": residual,
        }

    def _v75_recoverability(self, t, side, qv):
        legacy = dict(super()._v75_recoverability(t, side, qv))
        legacy["legacyRecoverable"] = bool(legacy.get("recoverable"))
        legacy["legacyReason"] = legacy.get("reason")
        if legacy.get("recoverable"):
            return legacy
        activation = self._residual_activation(int(t))
        if activation is None or side not in ("UP", "DOWN") or not qv:
            return legacy
        up_bid = float(qv["UP"].get("bid") or 0.0)
        down_bid = float(qv["DOWN"].get("bid") or 0.0)
        expand_price = float(qv[side].get("bid") or 0.0)
        if not (EPS < expand_price < 1.0 - EPS):
            return legacy
        expand_qty = 1.0 / expand_price
        if not math.isfinite(expand_qty) or expand_qty <= EPS or expand_qty > 12.0 + EPS:
            return legacy
        floor_before, up_qty, down_qty, cost = self._raw_floor()
        hyp_up = float(up_qty) + (expand_qty if side == "UP" else 0.0)
        hyp_down = float(down_qty) + (expand_qty if side == "DOWN" else 0.0)
        hyp_cost = float(cost) + expand_qty * expand_price
        floor_after_expand = min(hyp_up, hyp_down) - hyp_cost
        repair_debt = abs(hyp_up - hyp_down)
        context = recoverability.RecursiveCompositeRecoverabilityContext(
            floor_before_expand=float(floor_before),
            floor_after_expand=float(floor_after_expand),
            debt_side=str(side),
            repair_debt=float(repair_debt),
            up_bid=up_bid,
            down_bid=down_bid,
            max_carriers=4,
            max_venue_qty=12.0,
        )
        decision = self.recursivePolicy.evaluate(context)
        self.recursiveChecks += 1
        event = {
            "t": int(t),
            "side": str(side),
            "legacyReason": legacy.get("reason"),
            "parentId": activation["parentId"],
            "v90dFilledQty": activation["v90dFilledQty"],
            "residualDebtBeforeReexpand": activation["residualDebt"],
            "floorBeforeExpand": float(floor_before),
            "floorAfterHypExpand": float(floor_after_expand),
            "expandPrice": expand_price,
            "expandQty": expand_qty,
            "repairDebtAfterHypExpand": repair_debt,
            "recoverable": bool(decision.recoverable),
            "reason": decision.reason,
            "recoveredStep": decision.recovered_step,
            "terminalFloor": decision.terminal_floor,
            "terminalDebt": decision.terminal_debt,
            "maxDebt": decision.max_debt,
            "pairSum": decision.pair_sum,
            "path": list(decision.path),
        }
        if len(self.recursiveEvents) < 240:
            self.recursiveEvents.append(event)
        legacy.update(
            {
                "recursiveCompositeChecked": True,
                "recursiveCompositeDecision": event,
            }
        )
        if decision.recoverable:
            self.recursivePasses += 1
            legacy["recoverable"] = True
            legacy["reason"] = "RECURSIVE_COMPOSITE_CURRENT_COORDINATE_RECOVERABLE"
        return legacy

    def process(self, t):
        super().process(t)
        self._refresh_carrier_ledger(int(t))
        for key, entry in self.carrierLedger.items():
            current = float(entry.get("actualFilled") or 0.0)
            previous = float(self._alignmentFillSeen.get(str(key), 0.0))
            if current <= previous + EPS:
                continue
            role = str(entry.get("objectiveRole") or "UNKNOWN")
            row = {
                "t": int(t),
                "key": str(key),
                "side": entry.get("side"),
                "objectiveRole": role,
                "parentId": entry.get("parentId"),
                "fillDelta": current - previous,
                "actualFilled": current,
                "submittedQty": entry.get("submittedQty"),
                "price": entry.get("price"),
                "floorAfterReceipt": float(self._raw_floor()[0]),
                "residualAbsGapAfterReceipt": abs(
                    float(self.auth_inv()["UP"]) - float(self.auth_inv()["DOWN"])
                ),
            }
            self.alignmentFillEvents.append(row)
            self._alignmentFillSeen[str(key)] = current
            if str(key) in set(getattr(self, "v90dSubmitKeys", []) or []):
                if self._firstV90DFillObservedAt is None:
                    self._firstV90DFillObservedAt = int(t)

    def run_alignment(self, models, winner):
        result = self.run_v90d(models, winner)
        result.update(
            {
                "recursiveRecoverabilityChecks": self.recursiveChecks,
                "recursiveRecoverabilityPasses": self.recursivePasses,
                "recursiveRecoverabilityEvents": self.recursiveEvents,
                "alignmentFillEvents": self.alignmentFillEvents,
                "firstV90DFillObservedAt": self._firstV90DFillObservedAt,
            }
        )
        return result


def summarize_candidate(result: dict) -> dict:
    first_v90d_fill = result.get("firstV90DFillObservedAt")
    admissions = [
        row
        for row in result.get("v83Admissions", []) or []
        if row.get("submit")
        and first_v90d_fill is not None
        and int(row.get("t") or -1) >= int(first_v90d_fill)
        and isinstance(row.get("recoverability"), dict)
        and row["recoverability"].get("reason")
        == "RECURSIVE_COMPOSITE_CURRENT_COORDINATE_RECOVERABLE"
    ]
    reexpand_keys = {str(row.get("key")) for row in admissions if row.get("key")}
    fills = list(result.get("alignmentFillEvents", []) or [])
    reexpand_fills = [row for row in fills if str(row.get("key")) in reexpand_keys]
    first_reexpand_fill_t = min((int(row["t"]) for row in reexpand_fills), default=None)
    later_repair_fills = [
        row
        for row in fills
        if first_reexpand_fill_t is not None
        and int(row.get("t") or -1) > first_reexpand_fill_t
        and str(row.get("objectiveRole")) == "REPAIR"
        and float(row.get("fillDelta") or 0.0) > EPS
    ]
    selected_event = None
    if admissions:
        rec = admissions[0].get("recoverability") or {}
        selected_event = rec.get("recursiveCompositeDecision")
    floor_before = float(selected_event.get("floorBeforeExpand")) if selected_event else None
    best_later_floor = max(
        (float(row.get("floorAfterReceipt")) for row in later_repair_fills),
        default=None,
    )
    return {
        "firstV90DFillObservedAt": first_v90d_fill,
        "reexpandAdmissions": admissions,
        "reexpandKeys": sorted(reexpand_keys),
        "reexpandFillEvents": reexpand_fills,
        "firstReexpandFillAt": first_reexpand_fill_t,
        "laterRepairFillEvents": later_repair_fills,
        "selectedRecursiveDecision": selected_event,
        "preReexpandFloor": floor_before,
        "bestObservedFloorAfterLaterRepair": best_later_floor,
    }


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
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_recursive_reexpand_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "RECURSIVE_REEXPAND_SMOKE", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "RECURSIVE_REEXPAND_SMOKE_START", "market": MID}), flush=True)
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

        def make(cls):
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

        baseline = make(v90d.V90DMinLegalIncremental)
        try:
            baseline_result = baseline.run_v90d(models, cohort[MID]["winner"])
        finally:
            baseline.close()
        candidate = make(RecursiveRecoverabilityResidualDebtReexpand)
        try:
            candidate_result = candidate.run_alignment(models, cohort[MID]["winner"])
        finally:
            candidate.close()

        lifecycle = summarize_candidate(candidate_result)
        safety = v90.safety_summary(candidate_result)
        safety_zero = all(float(value) <= EPS for value in safety.values())
        conservation = abs(
            float(candidate_result.get("v84CompositeFillQty") or 0.0)
            - float(candidate_result.get("v84RepairAllocatedQty") or 0.0)
            - float(candidate_result.get("v84OverflowAllocatedQty") or 0.0)
        ) <= 1e-7
        pre_floor = lifecycle["preReexpandFloor"]
        later_floor = lifecycle["bestObservedFloorAfterLaterRepair"]
        gates = {
            "v90dPartialRepairStillMaterializes": float(candidate_result.get("v90dFillQty") or 0.0) > EPS,
            "positiveResidualDebtPrecedesReexpand": bool(lifecycle["reexpandAdmissions"])
            and float(
                lifecycle["selectedRecursiveDecision"].get("residualDebtBeforeReexpand")
                if lifecycle["selectedRecursiveDecision"]
                else 0.0
            )
            > EPS,
            "recursiveRecoverabilityReclassificationExercised": int(
                candidate_result.get("recursiveRecoverabilityPasses") or 0
            )
            > 0,
            "reexpandAdmittedBeforeDebtZero": bool(lifecycle["reexpandAdmissions"]),
            "reexpandPhysicalFillObserved": bool(lifecycle["reexpandFillEvents"]),
            "laterRepairPhysicalFillObserved": bool(lifecycle["laterRepairFillEvents"]),
            "strictLifecycleOrder": lifecycle["firstV90DFillObservedAt"] is not None
            and lifecycle["firstReexpandFillAt"] is not None
            and bool(lifecycle["laterRepairFillEvents"])
            and int(lifecycle["firstV90DFillObservedAt"])
            < int(lifecycle["firstReexpandFillAt"])
            < min(int(row["t"]) for row in lifecycle["laterRepairFillEvents"]),
            "floorRestoredToPreReexpandCoordinate": pre_floor is not None
            and later_floor is not None
            and float(later_floor) >= float(pre_floor) - EPS,
            "physicalAllocationConservation": conservation,
            "safetyZero": safety_zero,
        }
        if not safety_zero or not conservation:
            decision = "REJECT_RECURSIVE_REEXPAND_SAFETY_OR_CONSERVATION"
        elif all(gates.values()):
            decision = "FUNCTIONAL_PASS_TO_ONE_FRESH_REPLICATION"
        elif gates["reexpandAdmittedBeforeDebtZero"] and not gates["reexpandPhysicalFillObserved"]:
            decision = "EXECUTION_INCONCLUSIVE_REEXPAND_SUBMIT_NO_FILL"
        elif gates["reexpandPhysicalFillObserved"] and not gates["laterRepairPhysicalFillObserved"]:
            decision = "PARTIAL_LIFECYCLE_REEXPAND_FILL_NO_LATER_REPAIR"
        else:
            decision = "DIAGNOSE_NEXT_POST_V90D_LIFECYCLE_GATE"
        output = {
            "version": "RECURSIVE_COMPOSITE_REEXPAND_BEHAVIOR_SMOKE_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": MID,
            "winnerPostHocOnly": cohort[MID]["winner"],
            "decision": decision,
            "gates": gates,
            "baselineV90D": {
                "fills": baseline_result.get("actualFillEvents"),
                "pnlDiagnosticOnly": baseline_result.get("pnlDiagnosticOnly"),
                "floor": baseline_result.get("floor"),
                "v90dFillQty": baseline_result.get("v90dFillQty"),
                "terminalResidualDebt": baseline_result.get("v90dTerminalAbsGap"),
                "v83AdmissionAllows": baseline_result.get("v83AdmissionAllows"),
                "semanticRounds": baseline_result.get("v70dSemanticRounds"),
            },
            "candidate": {
                "fills": candidate_result.get("actualFillEvents"),
                "pnlDiagnosticOnly": candidate_result.get("pnlDiagnosticOnly"),
                "floor": candidate_result.get("floor"),
                "v90dFillQty": candidate_result.get("v90dFillQty"),
                "terminalResidualDebt": candidate_result.get("v90dTerminalAbsGap"),
                "v83AdmissionAllows": candidate_result.get("v83AdmissionAllows"),
                "semanticRounds": candidate_result.get("v70dSemanticRounds"),
                "recursiveChecks": candidate_result.get("recursiveRecoverabilityChecks"),
                "recursivePasses": candidate_result.get("recursiveRecoverabilityPasses"),
            },
            "lifecycle": lifecycle,
            "safety": safety,
            "recursiveEvents": candidate_result.get("recursiveRecoverabilityEvents", []),
            "alignmentFillEvents": candidate_result.get("alignmentFillEvents", []),
            "v83Admissions": candidate_result.get("v83Admissions", []),
            "transitionEvents": candidate_result.get("transitionEvents", []),
            "allocationEvents": candidate_result.get("allocationV2Events", []),
            "boundary": [
                "single development market realistic HFT",
                "only post-confirmed-V90D residual-debt recoverability interpretation changed",
                "four-carrier current-coordinate bound preregistered",
                "V90D tranche and one-payment-per-parent rule frozen",
                "pExpand threshold, ownership, ResponsibilityTransition, AllocationLedger V2, RepairExecutionRouter, price and qty frozen",
                "strict-past current OUR quotes/state only",
                "Target actions and settlement never enter runtime authority",
                "<=180s speculative fence inherited",
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
