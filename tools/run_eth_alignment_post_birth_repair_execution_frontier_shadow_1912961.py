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
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


active = load_sibling(
    "residual_active_for_post_birth_frontier",
    "run_eth_alignment_residual_repair_active_handoff_behavior_smoke_1912961.py",
)
base = active.base
v90 = active.v90
v80 = active.v80
v38 = active.v38
v1 = active.v1


class PostBirthRepairExecutionFrontierShadow(active.ResidualRepairActiveHandoffBehavior):
    def __init__(self, *args, **kwargs):
        self.postBirthExecutionRows: list[dict] = []
        self._lastExecutionSignature = None
        super().__init__(*args, **kwargs)

    def _alignment_overflow_births(self):
        births = []
        for key, state in getattr(self, "v84Composite", {}).items():
            if state.get("lane") != "ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF":
                continue
            born = state.get("overflowBornAt")
            rem = max(0.0, float(state.get("overflowDebt") or 0.0) - float(state.get("overflowPaid") or 0.0))
            if born is None or rem <= EPS:
                continue
            births.append({
                "sourceKey": str(key),
                "bornAt": int(born),
                "repairSide": "DOWN" if str(state.get("side")).upper() == "UP" else "UP",
                "remaining": rem,
            })
        return births

    def _classify_current_parent(self, t: int):
        births = self._alignment_overflow_births()
        if not births:
            return None
        rp = getattr(self, "repairParent", None)
        if not isinstance(rp, dict):
            return {
                "t": int(t),
                "reason": "NO_REPAIR_PARENT_AFTER_ALIGNMENT_OVERFLOW",
                "alignmentOverflowBirths": births,
            }
        pid = int(rp.get("id") or -1)
        side = str(rp.get("side") or "")
        born = int(rp.get("bornAt") or -1)
        if not any(born >= int(x["bornAt"]) and side == x["repairSide"] for x in births):
            return None

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
        unresolved = []
        try:
            for key, entry, rem in self.unresolved():
                if int(entry.get("parentId") or -1) == pid:
                    unresolved.append({
                        "key": str(key),
                        "side": entry.get("side"),
                        "role": entry.get("objectiveRole"),
                        "lane": entry.get("lane"),
                        "remaining": float(rem),
                        "terminalConfirmed": bool(entry.get("terminalConfirmed")),
                        "cancelRequested": bool(entry.get("cancelRequested")),
                    })
        except Exception:
            pass

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
            "alignmentOverflowBirths": births,
            "v89OverflowBirthClockKnown": birth_clock_known,
            "v89cActiveCompositeSubmits": cap_count,
            "armed": armed,
            "hardConfirmed": hard,
            "activeAlreadyOwned": active_owned,
            "churnCount": len(churn),
            "churnRows": churn[-4:],
            "armFillBase": base_fill,
            "parentActualFill": now_fill,
            "paymentProgressSinceArm": progress,
            "floor": float(pay["floor"]),
            "managerGap": float(pay["gap"]),
            "liveAsk": ask,
            "legalPhysicalQty": legal,
            "unresolvedParentCarriers": unresolved,
            "reason": reason,
        }

    def _maybe_hard_active(self, t):
        pre = self._classify_current_parent(int(t))
        if pre is not None:
            sig = (
                pre.get("parentId"), pre.get("reason"), pre.get("armed"), pre.get("hardConfirmed"),
                pre.get("activeAlreadyOwned"), pre.get("churnCount"),
                tuple((x["key"], round(float(x["remaining"]), 9)) for x in pre.get("unresolvedParentCarriers", [])),
                None if pre.get("liveAsk") is None else round(float(pre["liveAsk"]), 6),
            )
            if sig != self._lastExecutionSignature or pre.get("reason") == "ELIGIBLE_FOR_V89D_ACTIVE_REPAIR":
                self.postBirthExecutionRows.append({**pre, "stage": "PRE_V89D"})
                self._lastExecutionSignature = sig
        before_submits = int(getattr(self, "v89cActiveCompositeSubmits", 0) or 0)
        result = super()._maybe_hard_active(t)
        after_submits = int(getattr(self, "v89cActiveCompositeSubmits", 0) or 0)
        if pre is not None and (result or after_submits != before_submits):
            self.postBirthExecutionRows.append({
                **pre,
                "stage": "POST_V89D",
                "submitResult": bool(result),
                "activeSubmitCountBefore": before_submits,
                "activeSubmitCountAfter": after_submits,
            })
        return result

    def run_shadow(self, models, winner):
        result = self.run_alignment(models, winner)
        result["postBirthExecutionRows"] = self.postBirthExecutionRows[:1200]
        return result


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
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_post_birth_exec_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "POST_BIRTH_REPAIR_EXECUTION_FRONTIER", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "POST_BIRTH_REPAIR_EXECUTION_FRONTIER_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {int(row["marketId"]): row for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]}
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        simulator = base.make_simulator(
            PostBirthRepairExecutionFrontierShadow,
            temporary / "tapes" / f"{MID}.json.xz",
            models, life, capability, timing, economic, price, surplus, teacher, generation_teacher,
        )
        try:
            result = simulator.run_shadow(models, cohort[MID]["winner"])
        finally:
            simulator.close()
        rows = result.get("postBirthExecutionRows", []) or []
        counts = {}
        for row in rows:
            if row.get("stage") != "PRE_V89D":
                continue
            reason = str(row.get("reason"))
            counts[reason] = counts.get(reason, 0) + 1
        eligible = [r for r in rows if r.get("stage") == "PRE_V89D" and r.get("reason") == "ELIGIBLE_FOR_V89D_ACTIVE_REPAIR"]
        submitted = [r for r in rows if r.get("stage") == "POST_V89D" and r.get("submitResult")]
        decision = "V89D_ELIGIBLE_BUT_NOT_SUBMITTED_DIAGNOSE_CALL_PATH" if eligible and not submitted else (
            "V89D_SUBMITTED_DIAGNOSE_FILL_PATH" if submitted else "POST_BIRTH_EXECUTION_BLOCKER_LOCALIZED"
        )
        output = {
            "version": "POST_BIRTH_REPAIR_EXECUTION_FRONTIER_SHADOW_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "behaviorMutationBeyondResidualActiveCandidate": False,
            "marketId": MID,
            "decision": decision,
            "summary": {
                "rows": len(rows),
                "reasonCounts": counts,
                "eligibleRows": len(eligible),
                "submittedRows": len(submitted),
                "repairParentBirths": int(result.get("repairParentBirths") or 0),
                "repairParentCompletions": int(result.get("repairParentCompletions") or 0),
                "v89cActiveCompositeSubmits": int(result.get("v89cActiveCompositeSubmits") or 0),
                "activeFillQty": float(result.get("v89cActiveCompositeFillQty") or 0.0),
                "terminalFloor": result.get("floor"),
                "pnlDiagnosticOnly": result.get("pnlDiagnosticOnly"),
            },
            "firstRows": rows[:160],
            "eligibleRowsSample": eligible[:40],
            "safety": v90.safety_summary(result),
            "boundary": [
                "behavior-inert diagnostic shadow over residual Repair Active-handoff candidate",
                "exactly mirrors V89D Active Repair preconditions; does not mutate them",
                "no submit/cancel/reprice/admission change from shadow",
                "no Target runtime input",
                "no dream fill",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "summary": output["summary"], "firstRows": rows[:12]}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
