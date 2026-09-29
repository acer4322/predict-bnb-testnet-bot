from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke

OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_FAULT_RECOVERY_SECOND_ACTION_CURRICULUM_V1"
BRANCHES = {
    "WAIT": "WAIT_FOR_CLARITY",
    "ACTIVE_ONCE": "REPLACE_ROUTE",
    "RETIRE": "RETIRE_OBLIGATION",
}
FAULTS = ("SUBMIT_REJECT", "NO_FILL_STALL")


class TwoStagePolicy:
    def __init__(self, second_action: str) -> None:
        self.second_action = second_action
        self.calls = 0
        self.second_state: dict[str, Any] | None = None

    def __call__(self, state: dict[str, Any]) -> str:
        self.calls += 1
        if self.calls == 1:
            return "REPLACE_ROUTE"
        if self.second_state is None:
            self.second_state = {
                "atMs": int(state["atMs"]),
                "side": str(state["side"]),
                "trackingError": float(state["trackingError"]),
                "checkpointDelayMs": int(state["checkpointDelayMs"]),
                "features": state["features"],
                "executionState": state.get("executionState"),
            }
        if self.calls == 2:
            return self.second_action
        # One counterfactual decision only. Afterwards preserve control rather than
        # allowing repeated active retries to contaminate the value of the second action.
        return "WAIT_FOR_CLARITY"


class FirstPairCompletionFault:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.used = False
        self.calls: list[dict[str, Any]] = []

    def __call__(self, state: dict[str, Any]) -> str | None:
        self.calls.append(dict(state))
        if not self.used and str(state.get("kind")) == "PAIR_COMPLETION_REPLACE":
            self.used = True
            return self.mode
        return None


def state_hash(state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_branch(market_id: int, fault: str, branch: str) -> dict[str, Any]:
    policy = TwoStagePolicy(BRANCHES[branch])
    injector = FirstPairCompletionFault(fault)
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        lifecycle_action_override=policy,
        taker_submit_fault_override=injector,
        fault_reentry_enabled=True,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
    )
    portfolio = report["actualExecution"]["finalPortfolio"]
    return {
        "marketId": int(market_id),
        "fault": fault,
        "branch": branch,
        "faultUsed": injector.used,
        "policyCalls": policy.calls,
        "secondStateHash": state_hash(policy.second_state),
        "secondState": policy.second_state,
        "terminal": {
            "worstCaseFloor": float(portfolio.get("worst_case_floor") or 0.0),
            "realizedPnl": report["actualExecution"].get("realizedPnl"),
            "finalAbsTrackingError": float(report["actualExecution"].get("finalAbsTrackingError") or 0.0),
            "takerFilledShares": float(report["actualExecution"].get("takerFilledShares") or 0.0),
        },
        "lifecycle": {
            "actionCounts": report["lifecycleAudit"]["actionCounts"],
            "unresolvedTakerReturns": report["lifecycleAudit"]["unresolvedTakerReturns"],
            "unresolvedCauseCounts": report["lifecycleAudit"]["unresolvedCauseCounts"],
            "ownershipEventCounts": report["lifecycleAudit"]["ownershipEventCounts"],
            "faultRecoveryReentryArmed": sum(
                row.get("action") == "FAULT_RECOVERY_REENTRY_ARMED"
                for row in report["lifecycleAudit"]["decisions"]
            ),
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts = []
    keys = sorted({(r["marketId"], r["fault"]) for r in rows})
    for market_id, fault in keys:
        group = [r for r in rows if r["marketId"] == market_id and r["fault"] == fault]
        by = {r["branch"]: r for r in group}
        if set(by) != set(BRANCHES):
            continue
        hashes = {r["secondStateHash"] for r in group if r["secondStateHash"] is not None}
        usable = all(r["faultUsed"] for r in group) and len(hashes) == 1 and all(
            r["cycleInvariantViolationCount"] == 0 for r in group
        )
        floor = {b: by[b]["terminal"]["worstCaseFloor"] for b in BRANCHES}
        pnl = {b: by[b]["terminal"]["realizedPnl"] for b in BRANCHES}
        tracking = {b: by[b]["terminal"]["finalAbsTrackingError"] for b in BRANCHES}
        oracle = max(floor, key=lambda b: (floor[b], -tracking[b], b == "WAIT")) if usable else None
        contexts.append({
            "marketId": market_id,
            "fault": fault,
            "usable": usable,
            "matchedSecondState": len(hashes) == 1,
            "secondStateHash": next(iter(hashes)) if len(hashes) == 1 else None,
            "floorByBranch": floor,
            "pnlByBranch": pnl,
            "finalAbsTrackingErrorByBranch": tracking,
            "oracleByWorstCaseFloor": oracle,
        })
    return contexts


def save(path: Path, market_ids: list[int], rows: list[dict[str, Any]], complete: bool) -> None:
    contexts = summarize(rows)
    usable = [c for c in contexts if c["usable"]]
    report = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "r2_fault_conditioned_recovery_curriculum_v1_preregistered.json",
        "complete": bool(complete),
        "marketIds": market_ids,
        "faults": list(FAULTS),
        "branches": BRANCHES,
        "rows": rows,
        "contexts": contexts,
        "summary": {
            "runs": len(rows),
            "contexts": len(contexts),
            "usableContexts": len(usable),
            "oracleCounts": {b: sum(c["oracleByWorstCaseFloor"] == b for c in usable) for b in BRANCHES},
            "semanticViolationRuns": sum(r["cycleInvariantViolationCount"] != 0 for r in rows),
        },
        "guard": "Synthetic faults are resilience curriculum only. They are not venue fill-probability or economic promotion evidence.",
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", required=True)
    ap.add_argument("--output", default="hft_r2_fault_recovery_second_action_curriculum_v1_report.json")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    market_ids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    output = OUT / args.output
    rows: list[dict[str, Any]] = []
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [r for r in prior.get("rows", []) if int(r.get("marketId", -1)) in set(market_ids)]
    done = {(r["marketId"], r["fault"], r["branch"]) for r in rows}
    total = len(market_ids) * len(FAULTS) * len(BRANCHES)
    for market_id in market_ids:
        for fault in FAULTS:
            for branch in BRANCHES:
                key = (market_id, fault, branch)
                if key in done:
                    continue
                row = run_branch(market_id, fault, branch)
                rows.append(row)
                save(output, market_ids, rows, complete=False)
                print(json.dumps({
                    "progress": f"{len(rows)}/{total}",
                    "marketId": market_id,
                    "fault": fault,
                    "branch": branch,
                    "faultUsed": row["faultUsed"],
                    "floor": row["terminal"]["worstCaseFloor"],
                    "tracking": row["terminal"]["finalAbsTrackingError"],
                    "violations": row["cycleInvariantViolationCount"],
                }, ensure_ascii=False), flush=True)
    save(output, market_ids, rows, complete=True)
    final = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "output": str(output), **final["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
