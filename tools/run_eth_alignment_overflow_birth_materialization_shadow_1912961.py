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
    "residual_active_handoff_for_materialization_shadow",
    "run_eth_alignment_residual_repair_active_handoff_behavior_smoke_1912961.py",
)
base = active.base
v90 = active.v90
v80 = active.v80
v38 = active.v38
v1 = active.v1


class OverflowBirthMaterializationShadow(active.ResidualRepairActiveHandoffBehavior):
    def __init__(self, *args, **kwargs):
        self.materializationShadowRows: list[dict] = []
        self._last_materialization_signature = None
        super().__init__(*args, **kwargs)

    def _overflow_state(self):
        rows = []
        for key, state in getattr(self, "v84Composite", {}).items():
            born = state.get("overflowBornAt")
            debt = float(state.get("overflowDebt") or 0.0)
            paid = float(state.get("overflowPaid") or 0.0)
            rem = max(0.0, debt - paid)
            if born is None or rem <= EPS:
                continue
            repair_side = "DOWN" if str(state.get("side")).upper() == "UP" else "UP"
            rows.append({
                "compositeKey": str(key),
                "sourceSide": state.get("side"),
                "repairSide": repair_side,
                "bornAt": int(born),
                "overflowDebt": debt,
                "overflowPaid": paid,
                "overflowRemaining": rem,
                "lane": state.get("lane"),
            })
        return rows

    def _capture_materialization_state(self, t: int, source: str):
        overflow = self._overflow_state()
        if not overflow:
            return
        ai = self.auth_inv()
        weak = "UP" if float(ai["UP"]) < float(ai["DOWN"]) - EPS else "DOWN" if float(ai["DOWN"]) < float(ai["UP"]) - EPS else None
        parent = getattr(self, "repairParent", None)
        parent_row = None if not isinstance(parent, dict) else {
            "id": int(parent.get("id") or -1),
            "side": parent.get("side"),
            "bornAt": int(parent.get("bornAt") or -1),
        }
        cap = None
        try:
            snap = self.capState.snapshot(int(t), int(self.capEnd))
            pred = self.capability.predict(snap)
            cap = {
                "repair_obligation_30s": float(pred.get("repair_obligation_30s") or 0.0),
                "expand_opportunity_30s": float(pred.get("expand_opportunity_30s") or 0.0),
                "both_responsibilities_30s": float(pred.get("both_responsibilities_30s") or 0.0),
                "activity_urgency_30s": float(pred.get("activity_urgency_30s") or 0.0),
            }
        except Exception as exc:
            cap = {"error": f"{type(exc).__name__}:{exc}"}
        unresolved = []
        try:
            for key, entry, rem in self.unresolved():
                unresolved.append({
                    "key": str(key),
                    "side": entry.get("side"),
                    "objectiveRole": entry.get("objectiveRole"),
                    "parentId": entry.get("parentId"),
                    "remaining": float(rem),
                    "lane": entry.get("lane"),
                })
        except Exception:
            pass
        row = {
            "t": int(t),
            "source": source,
            "secondsLeftDiagnosticOnly": (int(self.capEnd) - int(t)) / 1000.0,
            "authInv": {"UP": float(ai["UP"]), "DOWN": float(ai["DOWN"])},
            "weakSide": weak,
            "repairParent": parent_row,
            "repairParentBirths": int(getattr(self, "repairParentBirths", 0) or 0),
            "repairParentCompletions": int(getattr(self, "repairParentCompletions", 0) or 0),
            "overflow": overflow,
            "capability": cap,
            "unresolved": unresolved,
        }
        sig = (
            weak,
            None if parent_row is None else (parent_row["id"], parent_row["side"], parent_row["bornAt"]),
            tuple((x["compositeKey"], round(x["overflowRemaining"], 12)) for x in overflow),
            tuple((x["key"], round(x["remaining"], 12)) for x in unresolved),
            None if not isinstance(cap, dict) else round(float(cap.get("repair_obligation_30s") or 0.0), 6),
        )
        if sig != self._last_materialization_signature or len(self.materializationShadowRows) < 4:
            self.materializationShadowRows.append(row)
            self._last_materialization_signature = sig

    def process(self, t):
        super().process(t)
        self._capture_materialization_state(int(t), "process")

    def cancel_expired(self, t):
        super().cancel_expired(t)
        self._capture_materialization_state(int(t), "cancel_expired")

    def run_shadow(self, models, winner):
        result = self.run_alignment(models, winner)
        result["materializationShadowRows"] = self.materializationShadowRows[:480]
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
    temporary = Path(tempfile.mkdtemp(prefix="eth_alignment_overflow_materialization_"))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            print(json.dumps({"heartbeat": "OVERFLOW_BIRTH_MATERIALIZATION_SHADOW", "ts": time.time()}), flush=True)

    threading.Thread(target=heartbeat, daemon=True).start()
    print(json.dumps({"heartbeat": "OVERFLOW_BIRTH_MATERIALIZATION_SHADOW_START", "market": MID}), flush=True)
    try:
        zipfile.ZipFile(args.bundle).extractall(temporary)
        cohort = {int(row["marketId"]): row for row in json.load(open(temporary / "cohort.json", encoding="utf-8"))["rows"]}
        models, life, capability, timing, economic, price, surplus = v38.v36.v34.v30.load_runtime(args)
        teacher = joblib.load(args.v44_model)["models"]["EVENT_VALUE_NORM"]
        generation_teacher = joblib.load(args.v47_model)["models"]["GENERATION_AWARE_NORM"]
        simulator = base.make_simulator(
            OverflowBirthMaterializationShadow,
            temporary / "tapes" / f"{MID}.json.xz",
            models, life, capability, timing, economic, price, surplus, teacher, generation_teacher,
        )
        try:
            result = simulator.run_shadow(models, cohort[MID]["winner"])
        finally:
            simulator.close()
        rows = result.get("materializationShadowRows", []) or []
        active_overflow = [
            r for r in rows
            if any(x.get("lane") == "ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF" for x in r.get("overflow", []))
        ]
        correct_new_parent = [
            r for r in active_overflow
            if r.get("repairParent") is not None
            and r.get("weakSide") is not None
            and r["repairParent"].get("side") == r.get("weakSide")
            and int(r["repairParent"].get("bornAt") or -1) >= min(int(x["bornAt"]) for x in r.get("overflow", []) if x.get("lane") == "ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF")
        ]
        no_parent = [r for r in active_overflow if r.get("repairParent") is None]
        stale_parent = [
            r for r in active_overflow
            if r.get("repairParent") is not None and r.get("weakSide") is not None and r["repairParent"].get("side") != r.get("weakSide")
        ]
        low_cap = [
            r for r in active_overflow
            if isinstance(r.get("capability"), dict)
            and "error" not in r["capability"]
            and float(r["capability"].get("repair_obligation_30s") or 0.0) < 0.5
        ]
        decision = (
            "MATERIALIZATION_PRESENT_DIAGNOSE_EXECUTION_NEXT"
            if correct_new_parent else
            "OVERFLOW_BIRTH_STRANDED_BY_PARENT_MATERIALIZATION_SEAM"
        )
        output = {
            "version": "OVERFLOW_BIRTH_MATERIALIZATION_SHADOW_1912961",
            "date": "2026-09-04",
            "researchOnly": True,
            "runtimeAuthority": False,
            "behaviorMutationBeyondResidualActiveCandidate": False,
            "marketId": MID,
            "decision": decision,
            "summary": {
                "rows": len(rows),
                "activeOverflowRows": len(active_overflow),
                "correctNewParentRows": len(correct_new_parent),
                "noParentRows": len(no_parent),
                "staleParentRows": len(stale_parent),
                "lowCapabilityRows": len(low_cap),
                "terminalRepairParentActive": int(result.get("repairParentActiveAtEnd") or 0),
                "repairParentBirths": int(result.get("repairParentBirths") or 0),
                "repairParentCompletions": int(result.get("repairParentCompletions") or 0),
                "terminalFloor": result.get("floor"),
                "pnlDiagnosticOnly": result.get("pnlDiagnosticOnly"),
            },
            "firstActiveOverflowRows": active_overflow[:80],
            "safety": v90.safety_summary(result),
            "boundary": [
                "behavior-inert diagnostic shadow over the already-tested residual Active handoff candidate",
                "no new submit/cancel/reprice/admission mutation",
                "tests whether confirmed overflow debt becomes an authoritative next Repair responsibility",
                "capability prediction is logged only; it does not control this shadow",
                "no Target runtime input",
                "no dream fill",
                "no 8781",
            ],
        }
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "decision": decision, "summary": output["summary"], "sample": active_overflow[:5]}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
