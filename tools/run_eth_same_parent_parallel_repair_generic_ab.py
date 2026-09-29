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
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
EPS = 1e-9


def load_sibling(name: str, filename: str):
    p = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, p)
    if spec is None or spec.loader is None:
        raise ImportError(p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


old = load_sibling("same_parent_parallel_existing_generic_dep", "run_eth_v83_same_parent_parallel_repair_hft_smoke_1916869.py")
base = old.base
v38 = old.v38
v80 = old.v80


def make(cls, tape, models, life, cap, tim, econ, price, sur, t44, t47):
    return cls(
        tape, "BOOK_IMBALANCE", models, life, 0, 0,
        capability=cap, timing=tim, economic=econ, price_envelope=price,
        surplus_value=sur, teacher=t44, genTeacher=t47,
        policy_profile=v80.economic_v1_profile(),
    )


def safety(result: dict) -> dict:
    return old.birth.base.front.safety(result)


def allocation_summary(sim, result: dict) -> tuple[bool, bool, dict]:
    cons = abs(
        float(result.get("v84CompositeFillQty") or 0.0)
        - float(result.get("v84RepairAllocatedQty") or 0.0)
        - float(result.get("v84OverflowAllocatedQty") or 0.0)
    ) <= 1e-7
    parents = result.get("allocationV2Parents") or {}
    if not parents and hasattr(sim, "allocationLedgerV2"):
        parents = {
            str(pid): sim.allocationLedgerV2.describe_parent(pid)
            for pid in getattr(sim.allocationLedgerV2, "parents", {})
        }
    bounded = all(
        p is not None
        and float(p.get("repairPaid") or 0.0) <= float(p.get("initialDebt") or 0.0) + 1e-7
        and float(p.get("remainingDebt") or 0.0) >= -EPS
        for p in parents.values()
    )
    return cons, bounded, parents


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
    tmp = Path(tempfile.mkdtemp(prefix=f"same_parent_parallel_ab_{mid}_"))
    stop = threading.Event()

    def hb():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "SAME_PARENT_PARALLEL_GENERIC_AB", "market": mid, "ts": time.time()}), flush=True)

    threading.Thread(target=hb, daemon=True).start()
    print(json.dumps({"heartbeat": "SAME_PARENT_PARALLEL_GENERIC_AB_START", "market": mid}), flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = {int(r["marketId"]): r for r in json.load(open(tmp / "cohort.json", encoding="utf-8"))["rows"]}
        if mid not in cohort:
            raise KeyError(mid)
        models, life, cap, tim, econ, price, sur = v38.v36.v34.v30.load_runtime(a)
        t44 = joblib.load(a.v44_model)["models"]["EVENT_VALUE_NORM"]
        t47 = joblib.load(a.v47_model)["models"]["GENERATION_AWARE_NORM"]
        tape = tmp / "tapes" / f"{mid}.json.xz"

        b = make(base.ExistingParentResponsibilityGenerationEpochHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            br = b.run_candidate(models, cohort[mid]["winner"])
        finally:
            b.close()

        c = make(old.SameParentParallelRepairHFT, tape, models, life, cap, tim, econ, price, sur, t44, t47)
        try:
            rr = c.run_parallel(models, cohort[mid]["winner"])
            cons, parent_bounded, parents = allocation_summary(c, rr)
        finally:
            c.close()

        ss = safety(rr)
        safety_zero = all(float(v or 0.0) <= EPS for v in ss.values())
        active_fill = float(rr.get("parallelRepairActiveFillQty") or 0.0)
        baseline_fills = int(br.get("actualFillEvents") or 0)
        candidate_fills = int(rr.get("actualFillEvents") or 0)
        baseline_rounds = int(br.get("v70dSemanticRounds") or br.get("rounds") or 0)
        candidate_rounds = int(rr.get("v70dSemanticRounds") or rr.get("rounds") or 0)
        floor_nonworse = float(rr.get("floor") or 0.0) >= float(br.get("floor") or 0.0) - 1e-7
        duplicate_parent_birth = int(rr.get("repairParentBirths") or 0) > int(br.get("repairParentBirths") or 0)
        eligible = int(rr.get("parallelRepairEligible") or 0)
        submits = int(rr.get("parallelRepairSubmits") or 0)

        gates = {
            "seamReachable": eligible > 0,
            "activeSubmitIfReachable": eligible <= 0 or submits > 0,
            "physicalSupportIfReachable": eligible <= 0 or active_fill > EPS or candidate_rounds > baseline_rounds,
            "safetyZero": safety_zero,
            "allocationConservation": cons,
            "sharedParentDebtBounded": parent_bounded,
            "noDuplicateRepairParentBirth": not duplicate_parent_birth,
            "terminalFloorNonWorse": floor_nonworse,
        }
        if not safety_zero or not cons or not parent_bounded or duplicate_parent_birth or not floor_nonworse:
            decision = "REJECT_SAME_PARENT_PARALLEL_FRESH_SAFETY_OR_FLOOR"
        elif eligible <= 0:
            decision = "REACHABILITY_NULL_NO_POLICY_MUTATION_EVIDENCE"
        elif active_fill <= EPS and candidate_rounds <= baseline_rounds:
            decision = "EXECUTION_INCONCLUSIVE_REACHABLE_NO_PHYSICAL_SUPPORT"
        else:
            decision = "FRESH_FUNCTIONAL_PASS_FOR_DCELL_INTEGRATION"

        out = {
            "version": "ETH_SAME_PARENT_PARALLEL_REPAIR_GENERIC_AB_V1",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": mid,
            "winnerPostHocOnly": cohort[mid]["winner"],
            "split": cohort[mid].get("split"),
            "decision": decision,
            "gates": gates,
            "baseline": {
                "fills": baseline_fills,
                "rounds": baseline_rounds,
                "pnlDiagnosticOnly": float(br.get("pnlDiagnosticOnly") or 0.0),
                "floor": float(br.get("floor") or 0.0),
                "repairParentBirths": int(br.get("repairParentBirths") or 0),
            },
            "candidate": {
                "fills": candidate_fills,
                "rounds": candidate_rounds,
                "pnlDiagnosticOnly": float(rr.get("pnlDiagnosticOnly") or 0.0),
                "floor": float(rr.get("floor") or 0.0),
                "repairParentBirths": int(rr.get("repairParentBirths") or 0),
                "parallelRepairChecks": int(rr.get("parallelRepairChecks") or 0),
                "parallelRepairEligible": eligible,
                "parallelRepairSubmits": submits,
                "parallelRepairActiveFillQty": active_fill,
                "overflowAllocatedQty": float(rr.get("v84OverflowAllocatedQty") or 0.0),
                "overflowPaidQty": float(rr.get("v84OverflowPaidQty") or 0.0),
            },
            "safety": ss,
            "allocationParents": parents,
            "parallelRepairEvents": (rr.get("parallelRepairEvents") or [])[:480],
            "allocationEvents": (rr.get("allocationV2Events") or rr.get("v84Events") or [])[:480],
            "boundary": [
                "one fresh market A/B; baseline ExistingParentResponsibilityGenerationEpochHFT vs same-parent parallel Active capability",
                "same-parent aggregate Repair debt only; one min-legal Active child; Passive may remain live",
                "shared AllocationLedger V2 Repair-first/overflow-second",
                "inherited OUR economic ceiling frozen; no threshold/qty/price/delay tuning",
                "payment progress blocks additional Active",
                "<=180s new Active exposure fence frozen",
                "winner post-hoc scoring only; no Target runtime input; no dream fill; no 8781",
            ],
        }
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "gates": gates, "baseline": out["baseline"], "candidate": out["candidate"], "safety": ss}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
