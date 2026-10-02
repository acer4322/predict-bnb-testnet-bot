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
MID = 1917552


def load_sibling(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


exec2x2 = load_sibling(
    "exec_alloc_for_first_handoff_frontier_1917552",
    "run_eth_alignment_execution_allocation_2x2_1912961.py",
)
base = exec2x2.base
v38 = exec2x2.v38
v1 = exec2x2.v1


class FirstActiveHandoffFrontier(exec2x2.LegacyCapSharedAllocation):
    def __init__(self, *args, **kwargs):
        self.firstHandoffReasonCounts: dict[str, int] = {}
        self.firstHandoffReasonCountsMatchedClock: dict[str, int] = {}
        self.firstHandoffRows: list[dict] = []
        self._lastFirstHandoffSignature = None
        super().__init__(*args, **kwargs)

    def _classify_first_handoff(self, t: int):
        rp = getattr(self, "repairParent", None)
        if not isinstance(rp, dict):
            return None
        pid = int(rp.get("id") or -1)
        side = str(rp.get("side") or "")
        born = int(rp.get("bornAt") or -1)
        churn = [x for x in getattr(self, "repairChurn", []) if int(x.get("parentId") or -1) == pid]
        armed = pid in getattr(self, "_armedParents", set())
        hard = pid in getattr(self, "hardConfirmed", set())
        active_owned = pid in getattr(self, "activeByParent", {})
        base_fill = float(getattr(self, "armFillBase", {}).get(pid, self._parent_actual_fill(pid)))
        now_fill = float(self._parent_actual_fill(pid))
        progress = now_fill > base_fill + EPS
        pay = self._current_payoffs()
        qv = v1.quotes(self.book)
        ask = None
        legal = None
        if qv and side in ("UP", "DOWN") and side in qv and qv[side].get("ask") is not None:
            ask = float(qv[side]["ask"])
            legal = 1.0 / ask if ask > EPS else math.inf
        seconds_left = (int(self.capEnd) - int(t)) / 1000.0
        cap_count = int(getattr(self, "v89cActiveCompositeSubmits", 0) or 0)
        birth_clock_known = born in getattr(self, "v89OverflowBirthClocks", set())

        reason = "ELIGIBLE_FOR_V89D_ACTIVE_REPAIR"
        if cap_count >= 2:
            reason = "V89D_TWO_HANDOFF_SMOKE_CAP_REACHED"
        elif not birth_clock_known:
            reason = "OVERFLOW_BIRTH_CLOCK_NOT_REGISTERED"
        elif not armed:
            reason = "PARENT_NOT_ARMED"
        elif active_owned:
            reason = "ACTIVE_ALREADY_OWNED"
        elif hard:
            reason = "PARENT_ALREADY_HARD_CONFIRMED"
        elif side not in ("UP", "DOWN"):
            reason = "INVALID_REPAIR_SIDE"
        elif not churn:
            reason = "NO_REPAIR_CHURN_EVIDENCE"
        elif progress:
            reason = "PAYMENT_PROGRESS_SINCE_ARM"
        elif float(pay["floor"]) >= -EPS:
            reason = "NO_NEGATIVE_FLOOR"
        elif float(pay["gap"]) <= EPS:
            reason = "NO_REPAIR_GAP"
        elif seconds_left <= 180.0:
            reason = "LATE_V89D_ACTIVE_BLOCK"
        elif ask is None or legal is None:
            reason = "NO_ACTIVE_ASK"
        elif not math.isfinite(float(legal)) or float(legal) <= EPS or float(legal) > 12.0 + EPS:
            reason = "ILLEGAL_ACTIVE_SLICE"

        return {
            "t": int(t),
            "secondsLeft": seconds_left,
            "parentId": pid,
            "parentSide": side,
            "parentBornAt": born,
            "overflowBirthClockKnown": birth_clock_known,
            "activeCompositeSubmits": cap_count,
            "armed": armed,
            "hardConfirmed": hard,
            "activeAlreadyOwned": active_owned,
            "churnCount": len(churn),
            "armFillBase": base_fill,
            "parentActualFill": now_fill,
            "paymentProgressSinceArm": progress,
            "floor": float(pay["floor"]),
            "managerGap": float(pay["gap"]),
            "liveAsk": ask,
            "legalPhysicalQty": legal,
            "reason": reason,
        }

    def _maybe_hard_active(self, t):
        row = self._classify_first_handoff(int(t))
        if row is not None:
            reason = str(row["reason"])
            self.firstHandoffReasonCounts[reason] = self.firstHandoffReasonCounts.get(reason, 0) + 1
            if row["overflowBirthClockKnown"]:
                self.firstHandoffReasonCountsMatchedClock[reason] = self.firstHandoffReasonCountsMatchedClock.get(reason, 0) + 1
            sig = (
                row["parentId"], row["reason"], row["overflowBirthClockKnown"], row["armed"], row["hardConfirmed"],
                row["activeAlreadyOwned"], row["churnCount"], row["paymentProgressSinceArm"],
                None if row["liveAsk"] is None else round(float(row["liveAsk"]), 6),
            )
            if sig != self._lastFirstHandoffSignature or reason == "ELIGIBLE_FOR_V89D_ACTIVE_REPAIR":
                self.firstHandoffRows.append(dict(row))
                self._lastFirstHandoffSignature = sig
        return super()._maybe_hard_active(t)

    def run_frontier(self, models, winner):
        result = self.run_alignment(models, winner)
        result["firstHandoffReasonCounts"] = dict(self.firstHandoffReasonCounts)
        result["firstHandoffReasonCountsMatchedClock"] = dict(self.firstHandoffReasonCountsMatchedClock)
        result["firstHandoffRows"] = self.firstHandoffRows[:1600]
        return result


def compact(row: dict) -> dict:
    return {k: row.get(k) for k in [
        "t", "secondsLeft", "parentId", "parentSide", "parentBornAt", "overflowBirthClockKnown",
        "activeCompositeSubmits", "armed", "hardConfirmed", "activeAlreadyOwned", "churnCount",
        "armFillBase", "parentActualFill", "paymentProgressSinceArm", "floor", "managerGap",
        "liveAsk", "legalPhysicalQty", "reason",
    ]}


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
    temporary = Path(tempfile.mkdtemp(prefix="eth_first_active_frontier_1917552_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "FIRST_ACTIVE_HANDOFF_FRONTIER_1917552", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "FIRST_ACTIVE_HANDOFF_FRONTIER_1917552_START"}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort_doc = json.load(open(temporary / "cohort.json", encoding="utf-8"))
        cohort = {int(row["marketId"]): row for row in cohort_doc["rows"]}
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        sim = base.make_simulator(
            FirstActiveHandoffFrontier,
            temporary / "tapes" / f"{MID}.json.xz",
            models, life, capability, timing, economic, price, surplus, teacher, generation_teacher,
        )
        try:
            result = sim.run_frontier(models, cohort[MID]["winner"])
        finally:
            sim.close()

        counts = result.get("firstHandoffReasonCounts", {}) or {}
        matched = result.get("firstHandoffReasonCountsMatchedClock", {}) or {}
        rows = result.get("firstHandoffRows", []) or []
        matched_rows = [r for r in rows if r.get("overflowBirthClockKnown")]
        eligible = [r for r in matched_rows if r.get("reason") == "ELIGIBLE_FOR_V89D_ACTIVE_REPAIR"]
        dominant = max(matched.items(), key=lambda kv: kv[1])[0] if matched else None
        output = {
            "version": "FIRST_ACTIVE_HANDOFF_FRONTIER_1917552_RESULT_20260904",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "marketId": MID,
            "behaviorMutation": False,
            "decision": "FIRST_HANDOFF_ELIGIBLE_BUT_NOT_SUBMITTED_DIAGNOSE_CALL_PATH" if eligible and int(result.get("v89cActiveCompositeSubmits") or 0) == 0 else (
                "FIRST_HANDOFF_BLOCKER_LOCALIZED" if matched else "NO_MATCHED_OVERFLOW_PARENT_FRONTIER"
            ),
            "summary": {
                "repairParentBirths": int(result.get("repairParentBirths") or 0),
                "repairParentCompletions": int(result.get("repairParentCompletions") or 0),
                "overflowBirthClockCount": len(result.get("v89OverflowBirthClocks") or []),
                "activeCompositeSubmits": int(result.get("v89cActiveCompositeSubmits") or 0),
                "allReasonCounts": counts,
                "matchedOverflowClockReasonCounts": matched,
                "dominantMatchedBlocker": dominant,
                "matchedTransitionRows": len(matched_rows),
                "eligibleRows": len(eligible),
            },
            "matchedRowsSample": [compact(r) for r in matched_rows[:120]],
            "eligibleRowsSample": [compact(r) for r in eligible[:40]],
            "safety": exec2x2.safety_summary(result, True),
            "boundary": [
                "behavior-inert mirror of V89D first-handoff preconditions",
                "SharedParentDebtAllocationLedgerV2 diagnostic accounting ON",
                "no submit/cancel/reprice/admission change",
                "no pExpand/qty/price/threshold tuning",
                "winner/PnL post-hoc only and not used in blocker classification",
                "no Target runtime input",
                "realistic HFT only; no dream fill",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": output["decision"], "summary": output["summary"]}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
