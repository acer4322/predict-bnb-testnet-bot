from __future__ import annotations

import argparse
import importlib.util
import json
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
FIXED = (1915613, 1915659, 1915662)
RESERVED = 1915670


def load_sibling(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


x = load_sibling("alignment_exec_alloc_2x2_for_fresh3", "run_eth_alignment_execution_allocation_2x2_1912961.py")
base = x.active.base
v38 = x.v38


def compact_safety(result: dict) -> dict:
    return x.safety_summary(result, True)


def summarize(mid: int, label: str, result: dict) -> dict:
    parents = result.get("alignmentAllocationParents") or {}
    parent_rows = list(parents.values())
    completed = [
        p for p in parent_rows
        if float(p.get("initialDebt") or 0.0) > EPS and float(p.get("remainingDebt") or 0.0) <= 1e-7
    ]
    completed_with_overflow = [p for p in completed if float(p.get("transitionOverflow") or 0.0) > EPS]
    payment_events = [
        e for e in (result.get("alignmentAllocationEvents") or [])
        if e.get("event") == "ALIGNMENT_ALLOCATION_V2_OVERFLOW_PAYMENT" and float(e.get("paid") or 0.0) > EPS
    ]
    alloc_fill_events = [
        e for e in (result.get("alignmentAllocationEvents") or [])
        if e.get("event") == "ALIGNMENT_ALLOCATION_V2_FILL" and float(e.get("fillInc") or 0.0) > EPS
    ]
    safety = compact_safety(result)
    conservation = abs(
        float(result.get("v84CompositeFillQty") or 0.0)
        - float(result.get("v84RepairAllocatedQty") or 0.0)
        - float(result.get("v84OverflowAllocatedQty") or 0.0)
    ) <= 1e-7
    parent_bounded = all(
        float(p.get("repairPaid") or 0.0) <= float(p.get("initialDebt") or 0.0) + 1e-7
        and float(p.get("remainingDebt") or 0.0) >= -EPS
        for p in parent_rows
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
        "marketId": mid,
        "label": label,
        "fills": int(result.get("actualFillEvents") or 0),
        "pnlDiagnosticOnly": float(result.get("pnlDiagnosticOnly") or 0.0),
        "terminalFloor": float(result.get("floor") or 0.0),
        "repairParentBirths": int(result.get("repairParentBirths") or 0),
        "repairParentCompletionsLegacyCounter": int(result.get("repairParentCompletions") or 0),
        "activeCompositeSubmits": int(result.get("v89cActiveCompositeSubmits") or 0),
        "confirmedAllocationFillEvents": len(alloc_fill_events),
        "confirmedOverflowPaymentEvents": len(payment_events),
        "confirmedOverflowPaymentQty": sum(float(e.get("paid") or 0.0) for e in payment_events),
        "completedParentDebts": len(completed),
        "completedParentDebtsWithOverflowTransition": len(completed_with_overflow),
        "terminalParentDebt": sum(max(0.0, float(p.get("remainingDebt") or 0.0)) for p in parent_rows),
        "overflowAllocated": float(result.get("v84OverflowAllocatedQty") or 0.0),
        "overflowPaid": float(result.get("v84OverflowPaidQty") or 0.0),
        "overflowRemaining": float(result.get("v84OverflowRemainingQty") or 0.0),
        "coverage": int(result.get("actualFillEvents") or 0) > 0,
        "allocationConservation": conservation,
        "sharedParentDebtBounded": parent_bounded,
        "safetyZero": safety_zero,
        "safety": safety,
        "paymentSample": payment_events[:8],
        "parentSample": parent_rows[:12],
    }


def main():
    ap = argparse.ArgumentParser()
    for name in [
        "bundle", "lifecycle-model", "capability-model", "dagger-cache", "timing-model",
        "economic-model", "price-model", "surplus-model", "v44-model", "v47-model",
    ]:
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    output_path = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if args.output.upper() == "AUTO" else Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="eth_alignment_perresp_fresh3_"))
    stop = threading.Event()

    def hb():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "PER_RESPONSIBILITY_EXECUTION_FRESH3", "ts": time.time()}), flush=True)

    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({"heartbeat": "PER_RESPONSIBILITY_EXECUTION_FRESH3_START", "markets": FIXED}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(tmp)
        cohort = {int(r["marketId"]): r for r in json.load(open(tmp / "cohort.json", encoding="utf-8"))["rows"]}
        if tuple(mid for mid in FIXED if mid in cohort) != FIXED or RESERVED not in cohort:
            raise RuntimeError(f"sealed cohort mismatch: {sorted(cohort)}")
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        rows = []
        traces = {}
        for idx, mid in enumerate(FIXED, 1):
            tape = tmp / "tapes" / f"{mid}.json.xz"
            print(json.dumps({"phase": "MARKET_START", "idx": idx, "of": len(FIXED), "marketId": mid}, ensure_ascii=False), flush=True)
            for label, cls in [
                ("BASELINE_CAP_ON_SHARED_ALLOC_V2", x.LegacyCapSharedAllocation),
                ("CANDIDATE_PER_RESPONSIBILITY_SHARED_ALLOC_V2", x.UncappedSharedAllocation),
            ]:
                sim = base.make_simulator(cls, tape, models, life, capability, timing, economic, price, surplus, teacher, generation_teacher)
                try:
                    result = sim.run_alignment(models, cohort[mid]["winner"])
                finally:
                    sim.close()
                row = summarize(mid, label, result)
                row["winnerPostHocOnly"] = cohort[mid]["winner"]
                rows.append(row)
                traces[f"{mid}:{label}"] = {
                    "v89cEvents": (result.get("v89cEvents") or [])[:120],
                    "allocationEvents": (result.get("alignmentAllocationEvents") or [])[:240],
                    "allocationParents": result.get("alignmentAllocationParents") or {},
                }
                print(json.dumps({
                    "phase": "CELL_DONE", "marketId": mid, "label": label,
                    "fills": row["fills"], "pnl": row["pnlDiagnosticOnly"], "floor": row["terminalFloor"],
                    "activeSubmits": row["activeCompositeSubmits"], "completedParents": row["completedParentDebts"],
                    "paymentEvents": row["confirmedOverflowPaymentEvents"], "safetyZero": row["safetyZero"]
                }, ensure_ascii=False), flush=True)

        baseline = [r for r in rows if r["label"].startswith("BASELINE")]
        candidate = [r for r in rows if r["label"].startswith("CANDIDATE")]
        byb = {r["marketId"]: r for r in baseline}
        byc = {r["marketId"]: r for r in candidate}
        comparisons = []
        for mid in FIXED:
            b, c = byb[mid], byc[mid]
            comparisons.append({
                "marketId": mid,
                "pnlBaseline": b["pnlDiagnosticOnly"], "pnlCandidate": c["pnlDiagnosticOnly"],
                "pnlDelta": c["pnlDiagnosticOnly"] - b["pnlDiagnosticOnly"],
                "floorBaseline": b["terminalFloor"], "floorCandidate": c["terminalFloor"],
                "floorDelta": c["terminalFloor"] - b["terminalFloor"],
                "fillsDelta": c["fills"] - b["fills"],
                "activeSubmitsDelta": c["activeCompositeSubmits"] - b["activeCompositeSubmits"],
                "completedParentDebtsDelta": c["completedParentDebts"] - b["completedParentDebts"],
                "overflowPaymentEventsDelta": c["confirmedOverflowPaymentEvents"] - b["confirmedOverflowPaymentEvents"],
            })
        agg_b = sum(r["pnlDiagnosticOnly"] for r in baseline)
        agg_c = sum(r["pnlDiagnosticOnly"] for r in candidate)
        worst_b = min(r["pnlDiagnosticOnly"] for r in baseline)
        worst_c = min(r["pnlDiagnosticOnly"] for r in candidate)
        exercise = any(
            c["activeCompositeSubmits"] > byb[c["marketId"]]["activeCompositeSubmits"]
            or c["confirmedOverflowPaymentEvents"] > byb[c["marketId"]]["confirmedOverflowPaymentEvents"]
            or c["completedParentDebts"] > byb[c["marketId"]]["completedParentDebts"]
            for c in candidate
        )
        gates = {
            "candidateExercisesAdditionalLifecycle": exercise,
            "aggregatePnlNonWorse": agg_c + EPS >= agg_b,
            "worstPnlWithinPreregisteredSmokeBound": worst_c + 0.50 + EPS >= worst_b,
            "coverageNonLower": sum(r["coverage"] for r in candidate) >= sum(r["coverage"] for r in baseline),
            "safetyZeroEveryMarket": all(r["safetyZero"] for r in candidate),
            "allocationConservationEveryMarket": all(r["allocationConservation"] for r in candidate),
            "sharedParentDebtBoundedEveryMarket": all(r["sharedParentDebtBounded"] for r in candidate),
        }
        if all(gates.values()):
            decision = "FRESH3_PASS_TO_RESERVED_MARKET_CONFIRMATION"
        elif not gates["safetyZeroEveryMarket"] or not gates["allocationConservationEveryMarket"] or not gates["sharedParentDebtBoundedEveryMarket"]:
            decision = "FRESH3_REJECT_SAFETY_OR_ACCOUNTING"
        elif not gates["candidateExercisesAdditionalLifecycle"]:
            decision = "FRESH3_INCONCLUSIVE_NO_ADDITIONAL_LIFECYCLE_SUPPORT"
        else:
            decision = "FRESH3_REJECT_ECONOMIC_OR_TAIL_REGRESSION"
        out = {
            "version": "PER_RESPONSIBILITY_EXECUTION_FRESH3_RESULT_20260904",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "fixedMarkets": list(FIXED),
            "reservedUntouchedMarket": RESERVED,
            "decision": decision,
            "gates": gates,
            "aggregate": {
                "baselinePnl": agg_b,
                "candidatePnl": agg_c,
                "pnlDelta": agg_c - agg_b,
                "baselineWorstPnl": worst_b,
                "candidateWorstPnl": worst_c,
                "baselineFills": sum(r["fills"] for r in baseline),
                "candidateFills": sum(r["fills"] for r in candidate),
                "baselineActiveSubmits": sum(r["activeCompositeSubmits"] for r in baseline),
                "candidateActiveSubmits": sum(r["activeCompositeSubmits"] for r in candidate),
                "baselineCompletedParentDebts": sum(r["completedParentDebts"] for r in baseline),
                "candidateCompletedParentDebts": sum(r["completedParentDebts"] for r in candidate),
                "baselineOverflowPaymentEvents": sum(r["confirmedOverflowPaymentEvents"] for r in baseline),
                "candidateOverflowPaymentEvents": sum(r["confirmedOverflowPaymentEvents"] for r in candidate),
            },
            "comparisons": comparisons,
            "cells": rows,
            "diagnostics": traces,
            "boundary": [
                "fixed first 3 markets from sealed later V90 fresh4 bundle; fourth remains untouched",
                "candidate only removes global research two-handoff cap; all per-responsibility gates remain",
                "SharedParentDebtAllocationLedgerV2 ON in baseline and candidate",
                "no pExpand/qty/price/threshold tuning",
                "winner/PnL post-hoc only",
                "no Target runtime input",
                "realistic HFT only; no dream fill",
                "no 8781"
            ]
        }
        output_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "gates": gates, "aggregate": out["aggregate"], "comparisons": comparisons}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
