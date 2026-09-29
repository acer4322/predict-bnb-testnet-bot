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


def load_sibling(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


x2 = load_sibling("alignment_exec_alloc_2x2_generic_dep", "run_eth_alignment_execution_allocation_2x2_1912961.py")
base = x2.base
v90 = x2.v90
v80 = x2.v80
v38 = x2.v38


def active_keys(result: dict) -> set[str]:
    keys = set(result.get("residualActiveKeys", []) or [])
    for e in result.get("v89cEvents", []) or []:
        if str(e.get("event") or "").endswith("ACTIVE_SUBMIT") and e.get("key"):
            keys.add(str(e["key"]))
    for e in result.get("residualActiveEvents", []) or []:
        if e.get("event") == "RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT" and e.get("submitOk") and e.get("activeKey"):
            keys.add(str(e["activeKey"]))
    return keys


def summarize(mid: int, result: dict) -> dict:
    alloc_events = result.get("alignmentAllocationEvents", []) or []
    akeys = active_keys(result)
    active_alloc_fills = [
        e for e in alloc_events
        if e.get("event") == "ALIGNMENT_ALLOCATION_V2_FILL"
        and str(e.get("key")) in akeys
        and float(e.get("fillInc") or 0.0) > EPS
    ]
    composite_active_fills = [
        e for e in active_alloc_fills
        if float(e.get("repairInc") or 0.0) > EPS and float(e.get("overflowInc") or 0.0) > EPS
    ]
    overflow_payments = [
        e for e in alloc_events
        if e.get("event") == "ALIGNMENT_ALLOCATION_V2_OVERFLOW_PAYMENT"
        and float(e.get("paid") or 0.0) > EPS
    ]
    completed_cycles = []
    for fill in composite_active_fills:
        key = str(fill.get("key"))
        t0 = int(fill.get("t") or -1)
        pays = [e for e in overflow_payments if str(e.get("compositeKey")) == key and int(e.get("t") or -1) > t0]
        if pays:
            completed_cycles.append({"activeFill": fill, "laterRepairPayments": pays[:8]})

    safety = x2.safety_summary(result, shared=True)
    safety_zero = (
        float(safety.get("truthMismatch") or 0.0) <= EPS
        and float(safety.get("responsibilityOverfill") or 0.0) <= EPS
        and float(safety.get("preBirthLeak") or 0.0) <= EPS
        and float(safety.get("duplicateDebt") or 0.0) <= EPS
        and float(safety.get("sharedOverfill") or 0.0) <= EPS
        and float(safety.get("unexplainedOverOwned") or 0.0) <= EPS
        and float(safety.get("unexplainedRepairDrift") or 0.0) <= EPS
    )
    conservation = abs(
        float(result.get("v84CompositeFillQty") or 0.0)
        - float(result.get("v84RepairAllocatedQty") or 0.0)
        - float(result.get("v84OverflowAllocatedQty") or 0.0)
    ) <= 1e-7
    parents = result.get("alignmentAllocationParents") or {}
    parent_bounded = all(
        float(p.get("repairPaid") or 0.0) <= float(p.get("initialDebt") or 0.0) + 1e-7
        and float(p.get("remainingDebt") or 0.0) >= -EPS
        for p in parents.values()
    )
    generic_submits = [e for e in result.get("v89cEvents", []) or [] if str(e.get("event") or "").endswith("ACTIVE_SUBMIT")]
    residual_submits = [e for e in result.get("residualActiveEvents", []) or [] if e.get("event") == "RESIDUAL_REPAIR_ACTIVE_HANDOFF_SUBMIT" and e.get("submitOk")]
    summary = {
        "marketId": mid,
        "fills": int(result.get("actualFillEvents") or 0),
        "pnlDiagnosticOnly": float(result.get("pnlDiagnosticOnly") or 0.0),
        "floor": float(result.get("floor") or 0.0),
        "semanticRounds": result.get("v70dSemanticRounds"),
        "repairParentBirths": int(result.get("repairParentBirths") or 0),
        "repairParentCompletions": int(result.get("repairParentCompletions") or 0),
        "genericActiveSubmits": len(generic_submits),
        "residualActiveSubmits": len(residual_submits),
        "activePhysicalFillQty": sum(float(e.get("fillInc") or 0.0) for e in active_alloc_fills),
        "activeCompositeFillCount": len(composite_active_fills),
        "completedRepairOverflowRepairCycles": len(completed_cycles),
        "overflowAllocatedQty": float(result.get("v84OverflowAllocatedQty") or 0.0),
        "overflowPaidQty": float(result.get("v84OverflowPaidQty") or 0.0),
        "overflowRemainingQty": float(result.get("v84OverflowRemainingQty") or 0.0),
        "allocationConservation": conservation,
        "sharedParentDebtBounded": parent_bounded,
        "safetyZero": safety_zero,
        "safety": safety,
        "completedCycleSamples": completed_cycles[:4],
    }
    if not safety_zero or not conservation or not parent_bounded:
        decision = "REJECT_SAFETY_OR_ACCOUNTING"
    elif summary["fills"] <= 0:
        decision = "SAFE_NO_PHYSICAL_ACTIVITY"
    elif len(completed_cycles) > 0:
        decision = "FUNCTIONAL_RECURSIVE_RELAY_OBSERVED"
    elif active_alloc_fills:
        decision = "SAFE_ACTIVE_EXECUTION_NO_COMPLETE_RELAY"
    else:
        decision = "SAFE_ACTIVITY_NO_ACTIVE_RELAY_REACHABILITY"
    summary["decision"] = decision
    return summary


def main():
    ap = argparse.ArgumentParser()
    for n in [
        "bundle", "lifecycle-model", "capability-model", "dagger-cache", "timing-model",
        "economic-model", "price-model", "surplus-model", "v44-model", "v47-model",
    ]:
        ap.add_argument("--" + n, required=True)
    ap.add_argument("--market-id", type=int, required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    mid = int(a.market_id)
    out_path = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if a.output.upper() == "AUTO" else Path(a.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"eth_alignment_dcell_{mid}_"))
    stop = threading.Event()

    def hb():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "ALIGNMENT_DCELL_GENERIC_HFT", "market": mid, "ts": time.time()}), flush=True)

    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({"heartbeat": "ALIGNMENT_DCELL_GENERIC_START", "market": mid}), flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        rows = json.load(open(tmp / "cohort.json", encoding="utf-8"))["rows"]
        cohort = {int(r["marketId"]): r for r in rows}
        if mid not in cohort:
            raise KeyError(f"market {mid} absent from bundle")
        tape = tmp / "tapes" / f"{mid}.json.xz"
        if not tape.exists():
            raise FileNotFoundError(tape)
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(a)
        teacher = joblib.load(a.v44_model)["models"]["EVENT_VALUE_NORM"]
        gen_teacher = joblib.load(a.v47_model)["models"]["GENERATION_AWARE_NORM"]
        sim = base.make_simulator(
            x2.UncappedSharedAllocation, tape, models, life, capability, timing,
            economic, price, surplus, teacher, gen_teacher
        )
        try:
            result = sim.run_alignment(models, cohort[mid]["winner"])
        finally:
            sim.close()
        summary = summarize(mid, result)
        out = {
            "version": "ETH_ALIGNMENT_DCELL_GENERIC_HFT_V1",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": mid,
            "winnerPostHocOnly": cohort[mid]["winner"],
            "split": cohort[mid].get("split"),
            "decision": summary["decision"],
            "summary": summary,
            "v89cEvents": (result.get("v89cEvents") or [])[:240],
            "residualActiveEvents": (result.get("residualActiveEvents") or [])[:240],
            "allocationEvents": (result.get("alignmentAllocationEvents") or [])[:480],
            "allocationParents": result.get("alignmentAllocationParents") or {},
            "boundary": [
                "D-cell only: per-responsibility uncapped RepairExecutionRouter + shared-parent AllocationLedger V2",
                "research-only V89D global two-handoff smoke cap removed; all per-responsibility ownership/churn/payment/legality checks retained",
                "V90D incremental Repair and residual Active handoff frozen",
                "<=180s new speculative overflow fence retained",
                "strict-past OUR state only; winner post-hoc scoring only",
                "no Target runtime input", "no dream fill", "no 8781",
            ],
        }
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": out["decision"], "summary": summary}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
