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
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


exec2x2 = load_sibling(
    "exec_alloc_for_support_selector_v92",
    "run_eth_alignment_execution_allocation_2x2_1912961.py",
)
base = exec2x2.base
v38 = exec2x2.v38
v1 = exec2x2.v1


class BaselineCapSharedAllocationSelector(exec2x2.LegacyCapSharedAllocation):
    """Behavior-identical baseline; records whether a post-birth responsibility is blocked only by V89D's global cap."""

    def __init__(self, *args, **kwargs):
        self.supportFrontierRows: list[dict] = []
        self._lastSupportSignature = None
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
            return None
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
            "stage": "PRE_V89D",
            "secondsLeft": seconds_left,
            "parentId": pid,
            "parentSide": side,
            "parentBornAt": born,
            "v89OverflowBirthClockKnown": birth_clock_known,
            "v89cActiveCompositeSubmits": cap_count,
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
        pre = self._classify_current_parent(int(t))
        if pre is not None:
            sig = (
                pre.get("parentId"), pre.get("reason"), pre.get("armed"), pre.get("hardConfirmed"),
                pre.get("activeAlreadyOwned"), pre.get("churnCount"),
                None if pre.get("liveAsk") is None else round(float(pre["liveAsk"]), 6),
            )
            if sig != self._lastSupportSignature or pre.get("reason") == "V89D_TWO_HANDOFF_SMOKE_CAP_REACHED":
                self.supportFrontierRows.append(pre)
                self._lastSupportSignature = sig
        return super()._maybe_hard_active(t)

    def run_selector(self, models, winner):
        result = self.run_alignment(models, winner)
        result["supportFrontierRows"] = self.supportFrontierRows[:1600]
        return result


def strict_cap_only(row: dict) -> bool:
    if str(row.get("reason")) != "V89D_TWO_HANDOFF_SMOKE_CAP_REACHED":
        return False
    legal = row.get("legalPhysicalQty")
    ask = row.get("liveAsk")
    return (
        int(row.get("v89cActiveCompositeSubmits") or 0) >= 2
        and bool(row.get("v89OverflowBirthClockKnown"))
        and bool(row.get("armed"))
        and not bool(row.get("hardConfirmed"))
        and not bool(row.get("activeAlreadyOwned"))
        and int(row.get("churnCount") or 0) > 0
        and not bool(row.get("paymentProgressSinceArm"))
        and float(row.get("floor") or 0.0) < -EPS
        and float(row.get("managerGap") or 0.0) > EPS
        and float(row.get("secondsLeft") or 0.0) > 180.0
        and ask is not None
        and legal is not None
        and math.isfinite(float(legal))
        and float(legal) > EPS
        and float(legal) <= 12.0 + EPS
    )


def compact_row(row: dict) -> dict:
    return {
        "t": int(row.get("t") or -1),
        "secondsLeft": float(row.get("secondsLeft") or 0.0),
        "parentId": int(row.get("parentId") or -1),
        "parentSide": row.get("parentSide"),
        "parentBornAt": int(row.get("parentBornAt") or -1),
        "v89cActiveCompositeSubmits": int(row.get("v89cActiveCompositeSubmits") or 0),
        "armed": bool(row.get("armed")),
        "churnCount": int(row.get("churnCount") or 0),
        "paymentProgressSinceArm": bool(row.get("paymentProgressSinceArm")),
        "floor": float(row.get("floor") or 0.0),
        "managerGap": float(row.get("managerGap") or 0.0),
        "liveAsk": row.get("liveAsk"),
        "legalPhysicalQty": row.get("legalPhysicalQty"),
        "reason": row.get("reason"),
    }


def main():
    parser = argparse.ArgumentParser()
    for name in [
        "bundle", "lifecycle-model", "capability-model", "dagger-cache", "timing-model",
        "economic-model", "price-model", "surplus-model", "v44-model", "v47-model",
    ]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output_path = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if args.output.upper() == "AUTO" else Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="eth_perresp_support_selector_v92_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "PERRESP_SUPPORT_SELECTOR_V92", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "PERRESP_SUPPORT_SELECTOR_V92_START"}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort_doc = json.load(open(temporary / "cohort.json", encoding="utf-8"))
        cohort_rows = cohort_doc.get("rows", []) if isinstance(cohort_doc, dict) else []
        cohort = {int(row["marketId"]): row for row in cohort_rows}
        market_ids = [int(row["marketId"]) for row in cohort_rows]

        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]

        rows = []
        for idx, mid in enumerate(market_ids, start=1):
            print(json.dumps({"phase": "SCAN", "idx": idx, "of": len(market_ids), "marketId": mid}), flush=True)
            tape = temporary / "tapes" / f"{mid}.json.xz"
            sim = base.make_simulator(
                BaselineCapSharedAllocationSelector,
                tape,
                models, life, capability, timing, economic, price, surplus, teacher, generation_teacher,
            )
            try:
                result = sim.run_selector(models, cohort[mid]["winner"])
            finally:
                sim.close()
            frontier = result.get("supportFrontierRows", []) or []
            strict = [r for r in frontier if strict_cap_only(r)]
            reason_counts = {}
            for r in frontier:
                reason = str(r.get("reason"))
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            row = {
                "marketId": mid,
                "repairParentBirths": int(result.get("repairParentBirths") or 0),
                "repairParentCompletions": int(result.get("repairParentCompletions") or 0),
                "activeCompositeSubmits": int(result.get("v89cActiveCompositeSubmits") or 0),
                "overflowBirthClockCount": len(result.get("v89OverflowBirthClocks") or []),
                "frontierRows": len(frontier),
                "strictCapOnlyRows": len(strict),
                "strictCapOnlyParentIds": sorted({int(r.get("parentId") or -1) for r in strict}),
                "reasonCounts": reason_counts,
                "strictSample": [compact_row(r) for r in strict[:8]],
            }
            rows.append(row)
            print(json.dumps({"phase": "SCANNED", "marketId": mid, "strictCapOnlyRows": len(strict), "activeCompositeSubmits": row["activeCompositeSubmits"], "repairParentBirths": row["repairParentBirths"]}), flush=True)

        support = [r for r in rows if int(r["strictCapOnlyRows"]) > 0]
        selected = [int(r["marketId"]) for r in support[:4]]
        decision = "PASS_SUPPORT_POOL_TO_FIXED_AB" if len(support) >= 3 else "SUPPORT_SCARCE_EXPAND_BASELINE_ONLY_POOL"
        output = {
            "version": "PER_RESPONSIBILITY_EXECUTION_SUPPORT_SELECTOR_V92_FRESH16_RESULT_20260904",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "selectionUsesCandidateOutcome": False,
            "selectionUsesWinnerOrPnl": False,
            "decision": decision,
            "poolMarketIds": market_ids,
            "supportMarketCount": len(support),
            "selectedChronologicalSupportMarkets": selected,
            "fixedABMarketsIfPass": selected[:3],
            "reservedSupportMarketIfAvailable": selected[3] if len(selected) >= 4 else None,
            "rows": rows,
            "boundary": [
                "behavior-inert baseline-only support selection",
                "strict support requires all per-responsibility gates to pass except the research-only global two-handoff cap",
                "no candidate run during selection",
                "winner/PnL ignored for selection and omitted from output",
                "SharedParentDebtAllocationLedgerV2 diagnostic accounting enabled",
                "no pExpand/qty/price/threshold tuning",
                "no Target runtime input",
                "realistic HFT only; no dream fill",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "supportMarketCount": len(support), "selected": selected}), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
