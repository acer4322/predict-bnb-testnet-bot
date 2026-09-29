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


active = load_sibling(
    "residual_active_for_execution_allocation_2x2",
    "run_eth_alignment_residual_repair_active_handoff_behavior_smoke_1912961.py",
)
allocmod = load_sibling(
    "allocation_ledger_v2_for_execution_allocation_2x2",
    "allocation_ledger_v2.py",
    "eth_repair_modular",
)
base = active.base
v90 = active.v90
v80 = active.v80
v38 = active.v38
v1 = active.v1


class UncappedPerResponsibilityActiveMixin:
    """Exact V89D mechanics without the research-only global two-handoff cap.

    Safety remains per responsibility: overflow birth, armed state, churn,
    no payment progress, no active ownership, no hard-confirmed duplicate,
    negative floor/gap, legal Active slice, and the frozen >180s new-risk fence.
    """

    def _maybe_hard_active(self, t):
        rp = self.repairParent
        if rp is None:
            return False
        pid = int(rp.get("id"))
        side = rp.get("side")
        born = int(rp.get("bornAt") or -1)
        if (
            born not in self.v89OverflowBirthClocks
            or pid not in self._armedParents
            or pid in self.activeByParent
            or pid in self.hardConfirmed
            or side not in ("UP", "DOWN")
        ):
            return False
        churn = [x for x in self.repairChurn if int(x.get("parentId") or -1) == pid]
        if not churn:
            return False
        base_fill = float(self.armFillBase.get(pid, self._parent_actual_fill(pid)))
        now_fill = float(self._parent_actual_fill(pid))
        if now_fill > base_fill + EPS:
            self.blockedPaymentProgress += 1
            return False
        pay = self._current_payoffs()
        if pay["floor"] >= -EPS or pay["gap"] <= EPS:
            return False
        if int(self.capEnd) - int(t) <= 180000:
            return False
        last = churn[-1]
        passive_key = str(last["key"])
        entry = self.carrierLedger.get(passive_key, {})
        qv = v1.quotes(self.book)
        if not qv or side not in qv or qv[side].get("ask") is None:
            return False
        ask = float(qv[side]["ask"])
        legal = 1.0 / ask if ask > EPS else math.inf
        if not math.isfinite(legal) or legal <= EPS or legal > 12.0 + EPS:
            return False
        objective_id = entry.get("objectiveId") or (
            self.repairLaneObjective.get("id") if getattr(self, "repairLaneObjective", None) else None
        )
        self.hardConfirmed.add(pid)
        self.hardEventConfirmedCount += 1
        event = {
            "t": int(t),
            "event": "ALIGNMENT_PER_RESPONSIBILITY_ACTIVE_CONFIRMED",
            "ordinal": int(getattr(self, "v89cActiveCompositeSubmits", 0) or 0) + 1,
            "parentId": pid,
            "bornAt": born,
            "side": side,
            "passiveKey": passive_key,
            "churnCount": len(churn),
            "parentFillAtArm": base_fill,
            "parentFillNow": now_fill,
            "floor": pay["floor"],
            "managerDebt": pay["gap"],
            "liveAsk": ask,
            "legalPhysicalQty": legal,
            "overflowBudgetIfFull": max(0.0, legal - pay["gap"]),
        }
        self.v89cEvents.append(dict(event))
        self.activeEvents.append(dict(event))
        ok = self._submit_active(t, pid, passive_key, side, ask, legal, objective_id)
        if ok:
            active_key = self.activeByParent[pid]["key"]
            self.v89cActiveCompositeSubmits += 1
            self.v89cActiveKeys.add(active_key)
            self.v84CompositeSubmits += 1
            self.v84Composite[active_key] = {
                "key": active_key,
                "side": side,
                "parentId": pid,
                "price": ask,
                "submittedQty": legal,
                "gapAtSubmit": float(pay["gap"]),
                "fillSeen": 0.0,
                "repairAllocated": 0.0,
                "overflowAllocated": 0.0,
                "overflowBornAt": None,
                "overflowDebt": 0.0,
                "overflowPaid": 0.0,
                "lane": "ALIGNMENT_PER_RESPONSIBILITY_ACTIVE_COMPOSITE",
            }
            self.v89cEvents.append(
                {
                    "t": int(t),
                    "event": "ALIGNMENT_PER_RESPONSIBILITY_ACTIVE_SUBMIT",
                    "ordinal": self.v89cActiveCompositeSubmits,
                    "parentId": pid,
                    "key": active_key,
                    "side": side,
                    "price": ask,
                    "managerDebt": pay["gap"],
                    "physicalQty": legal,
                }
            )
        return bool(ok)


class SharedParentAllocationMixin:
    def __init__(self, *args, **kwargs):
        self.alignmentAllocationLedgerV2 = allocmod.SharedParentDebtAllocationLedgerV2()
        self.alignmentAllocationEvents: list[dict] = []
        self.alignmentPureOverflowFirstFills = 0
        super().__init__(*args, **kwargs)

    def _seed_parent_debt_alignment(self, pid: int, fallback: float) -> float:
        if self.alignmentAllocationLedgerV2.describe_parent(pid) is not None:
            return self.alignmentAllocationLedgerV2.remaining(pid, fallback)
        candidates = []
        for key, state in getattr(self, "v84Composite", {}).items():
            try:
                if int(state.get("parentId")) == int(pid):
                    candidates.append((str(key), float(state.get("gapAtSubmit") or 0.0)))
            except Exception:
                pass
        debt = float(candidates[0][1]) if candidates else max(0.0, float(fallback or 0.0))
        key = candidates[0][0] if candidates else f"PARENT_{pid}_SEED"
        self.alignmentAllocationLedgerV2.register_carrier(key, int(pid), debt)
        return self.alignmentAllocationLedgerV2.remaining(pid, debt)

    def _repair_payment_cumulative_alignment(self, key: str, entry: dict) -> float:
        state = getattr(self, "v84Composite", {}).get(key)
        if state is not None:
            return float(state.get("repairAllocated") or 0.0)
        return float(entry.get("actualFilled") or 0.0)

    def _scan_v84(self, t):
        # Register all sibling physical carriers against one manager debt per parent.
        for key, state in list(self.v84Composite.items()):
            pid = state.get("parentId")
            if pid is None:
                continue
            self.alignmentAllocationLedgerV2.register_carrier(
                str(key), int(pid), float(state.get("gapAtSubmit") or 0.0)
            )

        # Confirmed fills allocate against shared parent debt first.
        for key, state in list(self.v84Composite.items()):
            entry = self.carrierLedger.get(key, {})
            current = float(entry.get("actualFilled") or 0.0)
            old = float(state.get("fillSeen") or 0.0)
            if current <= old + EPS:
                continue
            pid = int(state.get("parentId"))
            first = old <= EPS
            allocation = self.alignmentAllocationLedgerV2.allocate_cumulative(
                str(key), pid, current, float(state.get("gapAtSubmit") or 0.0)
            )
            if allocation is None:
                continue
            inc = float(allocation.fill_increment)
            state["fillSeen"] = current
            self.v84CompositeFillQty += inc
            repair_inc = float(allocation.repair_increment)
            overflow_inc = float(allocation.overflow_increment)
            state["repairAllocated"] += repair_inc
            state["overflowAllocated"] += overflow_inc
            self.v84RepairAllocated += repair_inc
            self.v84OverflowAllocated += overflow_inc
            event = {
                "t": int(t),
                "event": "ALIGNMENT_ALLOCATION_V2_FILL",
                "key": key,
                "parentId": pid,
                "fillInc": inc,
                "parentDebtBefore": allocation.debt_before,
                "repairInc": repair_inc,
                "overflowInc": overflow_inc,
                "parentDebtAfter": allocation.debt_after,
                "transitionOverflowTotal": allocation.transition_overflow_total,
                "repairCum": state["repairAllocated"],
                "overflowCum": state["overflowAllocated"],
            }
            if first and repair_inc <= EPS and overflow_inc > EPS:
                self.alignmentPureOverflowFirstFills += 1
                event["pureOverflowSiblingFirstFill"] = True
            if overflow_inc > EPS:
                if state.get("overflowBornAt") is None:
                    state["overflowBornAt"] = int(t)
                    opposite = "DOWN" if state["side"] == "UP" else "UP"
                    baseline = {}
                    for repair_key, repair_entry in self.carrierLedger.items():
                        if (
                            str(repair_entry.get("objectiveRole") or "") == "REPAIR"
                            and repair_entry.get("side") == opposite
                        ):
                            seen = self._repair_payment_cumulative_alignment(repair_key, repair_entry)
                            self.v84OppSeen[repair_key] = seen
                            if seen > EPS:
                                baseline[repair_key] = seen
                    event["overflowBirth"] = True
                    event["oppositeRepairBaseline"] = baseline
                    if hasattr(self, "v89OverflowBirthClocks"):
                        self.v89OverflowBirthClocks.add(int(t))
                        if hasattr(self, "v89Events"):
                            self.v89Events.append(
                                {
                                    "t": int(t),
                                    "event": "V89_OVERFLOW_PARENT_BIRTH_CLOCK",
                                    "compositeKey": key,
                                    "overflowDebt": overflow_inc,
                                    "nextRepairSide": opposite,
                                    "source": "ALIGNMENT_ALLOCATION_LEDGER_V2",
                                }
                            )
                state["overflowDebt"] += overflow_inc
                self.v84OverflowDebt += overflow_inc
            self.v84Events.append(dict(event))
            self.alignmentAllocationEvents.append(dict(event))

        # Only post-birth opposite Repair allocation can pay prior overflow debt.
        for key, state in list(self.v84Composite.items()):
            born = state.get("overflowBornAt")
            remaining = max(
                0.0,
                float(state.get("overflowDebt") or 0.0) - float(state.get("overflowPaid") or 0.0),
            )
            if born is None or remaining <= EPS:
                continue
            opposite = "DOWN" if state["side"] == "UP" else "UP"
            for repair_key, repair_entry in list(self.carrierLedger.items()):
                if (
                    str(repair_entry.get("objectiveRole") or "") != "REPAIR"
                    or repair_entry.get("side") != opposite
                ):
                    continue
                current = self._repair_payment_cumulative_alignment(repair_key, repair_entry)
                old = float(self.v84OppSeen.get(repair_key, 0.0))
                if int(t) <= int(born):
                    if current > old + EPS:
                        self.v84PreBirthPaymentLeak += current - old
                    self.v84OppSeen[repair_key] = max(old, current)
                    continue
                if current > old + EPS:
                    inc = current - old
                    paid = min(inc, remaining)
                    state["overflowPaid"] += paid
                    self.v84OverflowPaid += paid
                    remaining -= paid
                    if paid > EPS:
                        payment_event = {
                            "t": int(t),
                            "event": "ALIGNMENT_ALLOCATION_V2_OVERFLOW_PAYMENT",
                            "compositeKey": key,
                            "repairKey": repair_key,
                            "repairAllocationInc": inc,
                            "paid": paid,
                            "overflowPaidCum": state["overflowPaid"],
                            "overflowDebt": state["overflowDebt"],
                        }
                        self.v84Events.append(payment_event)
                        self.alignmentAllocationEvents.append(dict(payment_event))
                self.v84OppSeen[repair_key] = max(old, current)
                if remaining <= EPS:
                    break

    def run_alignment(self, models, winner):
        result = super().run_alignment(models, winner)
        result.update(
            {
                "alignmentAllocationLedgerV2": self.alignmentAllocationLedgerV2.name,
                "alignmentAllocationParents": {
                    str(pid): self.alignmentAllocationLedgerV2.describe_parent(pid)
                    for pid in self.alignmentAllocationLedgerV2.parents
                },
                "alignmentAllocationEvents": self.alignmentAllocationEvents[:480],
                "alignmentPureOverflowFirstFills": self.alignmentPureOverflowFirstFills,
            }
        )
        return result


class LegacyCapLegacyAllocation(active.ResidualRepairActiveHandoffBehavior):
    pass


class UncappedLegacyAllocation(UncappedPerResponsibilityActiveMixin, active.ResidualRepairActiveHandoffBehavior):
    pass


class LegacyCapSharedAllocation(SharedParentAllocationMixin, active.ResidualRepairActiveHandoffBehavior):
    pass


class UncappedSharedAllocation(
    SharedParentAllocationMixin,
    UncappedPerResponsibilityActiveMixin,
    active.ResidualRepairActiveHandoffBehavior,
):
    pass


def safety_summary(result: dict, shared: bool) -> dict:
    base_safety = v90.safety_summary(result)
    raw_drift = int(result.get("repairToExpandAtFirstFill") or 0)
    covered = int(result.get("alignmentPureOverflowFirstFills") or 0) if shared else 0
    return {
        **base_safety,
        "legacyRepairDriftRaw": raw_drift,
        "coveredPureOverflowTransitions": covered,
        "unexplainedRepairDrift": max(0, raw_drift - covered) if shared else raw_drift,
    }


def summarize(label: str, result: dict, shared: bool, uncapped: bool) -> dict:
    fills = result.get("alignmentFillEvents", []) or []
    v90d_keys = set(result.get("v90dSubmitKeys", []) or [])
    residual_active_keys = set(result.get("residualActiveKeys", []) or [])
    v90d_fills = [r for r in fills if str(r.get("key")) in v90d_keys and float(r.get("fillDelta") or 0.0) > EPS]
    residual_active_fills = [r for r in fills if str(r.get("key")) in residual_active_keys and float(r.get("fillDelta") or 0.0) > EPS]
    first_residual_active_t = min((int(r["t"]) for r in residual_active_fills), default=None)
    later_repairs = [
        r for r in fills
        if first_residual_active_t is not None
        and int(r.get("t") or -1) > first_residual_active_t
        and str(r.get("objectiveRole")) == "REPAIR"
        and str(r.get("key")) not in residual_active_keys
    ]
    safety = safety_summary(result, shared)
    conservation = abs(
        float(result.get("v84CompositeFillQty") or 0.0)
        - float(result.get("v84RepairAllocatedQty") or 0.0)
        - float(result.get("v84OverflowAllocatedQty") or 0.0)
    ) <= 1e-7
    parent_ok = True
    if shared:
        parent_ok = all(
            float(state.get("repairPaid") or 0.0) <= float(state.get("initialDebt") or 0.0) + 1e-7
            and float(state.get("remainingDebt") or 0.0) >= -EPS
            for state in (result.get("alignmentAllocationParents") or {}).values()
        )
    safety_zero = (
        float(safety.get("truthMismatch") or 0.0) <= EPS
        and float(safety.get("responsibilityOverfill") or 0.0) <= EPS
        and float(safety.get("preBirthLeak") or 0.0) <= EPS
        and float(safety.get("duplicateDebt") or 0.0) <= EPS
        and float(safety.get("sharedOverfill") or 0.0) <= EPS
        and float(safety.get("unexplainedOverOwned") or 0.0) <= EPS
        and float(safety.get("unexplainedRepairDrift") or 0.0) <= EPS
    )
    return {
        "label": label,
        "uncappedPerResponsibilityExecution": uncapped,
        "sharedParentAllocationV2": shared,
        "fills": int(result.get("actualFillEvents") or 0),
        "pnlDiagnosticOnly": float(result.get("pnlDiagnosticOnly") or 0.0),
        "floor": float(result.get("floor") or 0.0),
        "repairParentBirths": int(result.get("repairParentBirths") or 0),
        "repairParentCompletions": int(result.get("repairParentCompletions") or 0),
        "v89cActiveCompositeSubmits": int(result.get("v89cActiveCompositeSubmits") or 0),
        "v89cActiveCompositeFillQty": float(result.get("v89cActiveCompositeFillQty") or 0.0),
        "residualActiveSubmits": len([e for e in result.get("residualActiveEvents", []) or [] if e.get("event") == "RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT" and e.get("submitOk")]),
        "residualActiveFillQty": sum(float(r.get("fillDelta") or 0.0) for r in residual_active_fills),
        "laterRepairFillEvents": len(later_repairs),
        "laterRepairFillQty": sum(float(r.get("fillDelta") or 0.0) for r in later_repairs),
        "v84OverflowAllocated": float(result.get("v84OverflowAllocatedQty") or 0.0),
        "v84OverflowPaid": float(result.get("v84OverflowPaidQty") or 0.0),
        "v84OverflowRemaining": float(result.get("v84OverflowRemainingQty") or 0.0),
        "allocationConservation": conservation,
        "sharedParentDebtBounded": parent_ok,
        "safetyZero": safety_zero,
        "safety": safety,
        "fullRequestedLifecyclePhysical": bool(v90d_fills and residual_active_fills and later_repairs),
        "laterRepairSample": later_repairs[:8],
    }


def main():
    parser = argparse.ArgumentParser()
    for name in [
        "bundle", "lifecycle-model", "capability-model", "dagger-cache", "timing-model",
        "economic-model", "price-model", "surplus-model", "v44-model", "v47-model",
    ]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--market-id", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.market_id != MID:
        raise ValueError(args.market_id)
    output_path = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if args.output.upper() == "AUTO" else Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_exec_alloc_2x2_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "ALIGNMENT_EXECUTION_ALLOCATION_2X2", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "ALIGNMENT_EXECUTION_ALLOCATION_2X2_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {int(row["marketId"]): row for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]}
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        tape = temporary / "tapes" / f"{MID}.json.xz"

        configs = [
            ("A_CAP_ON_LEGACY_ALLOC", LegacyCapLegacyAllocation, False, False),
            ("B_CAP_OFF_LEGACY_ALLOC", UncappedLegacyAllocation, False, True),
            ("C_CAP_ON_SHARED_ALLOC_V2", LegacyCapSharedAllocation, True, False),
            ("D_CAP_OFF_SHARED_ALLOC_V2", UncappedSharedAllocation, True, True),
        ]
        rows = []
        compact_results = {}
        for idx, (label, cls, shared, uncapped) in enumerate(configs, start=1):
            print(json.dumps({"phase": "RUN", "idx": idx, "of": 4, "label": label}, ensure_ascii=False), flush=True)
            sim = base.make_simulator(
                cls, tape, models, life, capability, timing, economic, price, surplus, teacher, generation_teacher
            )
            try:
                result = sim.run_alignment(models, cohort[MID]["winner"])
            finally:
                sim.close()
            row = summarize(label, result, shared, uncapped)
            rows.append(row)
            compact_results[label] = {
                "v89cEvents": (result.get("v89cEvents") or [])[:120],
                "residualActiveEvents": (result.get("residualActiveEvents") or [])[:160],
                "allocationEvents": (result.get("alignmentAllocationEvents") or result.get("v84Events") or [])[:240],
                "allocationParents": result.get("alignmentAllocationParents") or {},
            }
            print(json.dumps({"phase": "DONE", **{k: row[k] for k in ["label", "fills", "floor", "pnlDiagnosticOnly", "v89cActiveCompositeSubmits", "residualActiveFillQty", "laterRepairFillQty", "safetyZero", "fullRequestedLifecyclePhysical"]}}, ensure_ascii=False), flush=True)

        by = {row["label"]: row for row in rows}
        a = by["A_CAP_ON_LEGACY_ALLOC"]
        b = by["B_CAP_OFF_LEGACY_ALLOC"]
        c = by["C_CAP_ON_SHARED_ALLOC_V2"]
        d = by["D_CAP_OFF_SHARED_ALLOC_V2"]
        gates = {
            "baselineReproducesNoLaterRepair": a["laterRepairFillEvents"] == 0,
            "uncappingActuallyChangesExecution": b["v89cActiveCompositeSubmits"] > a["v89cActiveCompositeSubmits"],
            "legacyAllocationUnsafeWhenUncapped": not b["safetyZero"],
            "sharedAllocationPreservesAccountingWithCap": c["safetyZero"] and c["allocationConservation"] and c["sharedParentDebtBounded"],
            "sharedAllocationMakesUncappedAccountingSafe": d["safetyZero"] and d["allocationConservation"] and d["sharedParentDebtBounded"],
            "fullRequestedLifecycleAppearsOnlyInSafeCombinedCell": d["fullRequestedLifecyclePhysical"] and d["laterRepairFillQty"] > EPS,
        }
        if gates["sharedAllocationMakesUncappedAccountingSafe"] and gates["fullRequestedLifecycleAppearsOnlyInSafeCombinedCell"]:
            decision = "INTERACTION_PASS_TO_MICROWORLD_AND_SINGLE_BEHAVIOR_CONFIRMATION"
        elif gates["uncappingActuallyChangesExecution"] and not gates["sharedAllocationMakesUncappedAccountingSafe"]:
            decision = "INTERACTION_REJECT_ACCOUNTING_NOT_SAFE"
        else:
            decision = "INTERACTION_INCOMPLETE_DIAGNOSE_NEXT_SEAM"
        output = {
            "version": "ALIGNMENT_EXECUTION_ROUTER_X_ALLOCATION_LEDGER_2X2_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": MID,
            "winnerPostHocOnly": cohort[MID]["winner"],
            "decision": decision,
            "gates": gates,
            "cells": rows,
            "diagnostics": compact_results,
            "boundary": [
                "single consumed development market realistic HFT",
                "2x2 interaction only: research-only global V89D two-handoff cap ON/OFF x AllocationLedger legacy/shared-parent V2",
                "cap OFF means per-responsibility lifecycle authority; it does not remove ownership, churn, payment-progress, legality or <=180s checks",
                "V90D incremental Repair and residual Active-handoff module frozen across all cells",
                "no pExpand/qty/price/threshold tuning",
                "no Target runtime input",
                "no dream fill",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "gates": gates, "cells": rows}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
