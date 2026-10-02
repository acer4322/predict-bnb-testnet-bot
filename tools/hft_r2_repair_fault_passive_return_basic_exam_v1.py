from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_fault_recovery_second_action_curriculum_v1 import (
    FirstPairCompletionFault,
    TwoStagePolicy,
    state_hash,
)

OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_REPAIR_FAULT_PASSIVE_RETURN_BASIC_EXAM_V1"
PREREGISTRATION = "hft_r2_repair_fault_passive_return_basic_preregistered.json"
BRANCHES = {
    "WAIT": "WAIT_FOR_CLARITY",
    "PASSIVE_RETURN": "RETURN_TO_PASSIVE_REPAIR",
}


class FreezeDirectTakersAfterPairFault:
    """Isolate the residual-repair route without changing R2 targets or makers."""

    def __init__(self, fault: FirstPairCompletionFault, freeze_makers: bool = False) -> None:
        self.fault = fault
        self.freeze_makers = freeze_makers
        self.latched = False
        self.proposals = 0

    def __call__(self, state: dict[str, Any]) -> dict[str, Any] | None:
        memory = (state.get("executionState") or {}).get("behaviorMemory") or {}
        observed_pair_fault = (
            self.fault.used
            and str(memory.get("lastAction") or "") == "PAIR_COMPLETION_REPLACE"
            and str(memory.get("lastOutcome") or "")
            in {"SUBMIT_REJECT", "TERMINAL_ZERO_FILL", "NO_FILL_STALL", "PARTIAL"}
        )
        if observed_pair_fault:
            self.latched = True
        if not self.latched:
            return None
        self.proposals += 1
        proposal = {"freezeFrozenR2TakerIntents": True}
        if self.freeze_makers:
            proposal["freezeMakerExecutionChildren"] = True
        return proposal


def _safe_reduction(baseline: float, candidate: float) -> float | None:
    if baseline <= 1e-12:
        return None
    return (baseline - candidate) / baseline


def _paired_coverage(portfolio: dict[str, Any]) -> float | None:
    reported = portfolio.get("combined_paired_coverage")
    if reported is not None:
        return float(reported)
    up = float(portfolio.get("maker_up") or 0.0) + float(portfolio.get("taker_up") or 0.0)
    down = float(portfolio.get("maker_down") or 0.0) + float(portfolio.get("taker_down") or 0.0)
    gross = max(up, down)
    return min(up, down) / gross if gross > 1e-12 else None


def _post_second_maker_fills(report: dict[str, Any], at_ms: int | None) -> dict[str, Any]:
    if at_ms is None:
        rows: list[dict[str, Any]] = []
    else:
        rows = [
            row
            for row in report.get("actualFillFeedbackCycles", [])
            if row.get("role") == "MAKER" and int(row.get("fillObservedAtMs") or -1) > at_ms
        ]
    side_counts = {
        side: sum(row.get("side") == side for row in rows)
        for side in ("UP", "DOWN")
    }
    return {
        "count": len(rows),
        "shares": sum(float(row.get("shares") or 0.0) for row in rows),
        "sideCounts": side_counts,
        "fills": rows,
    }


def _recovery_landmark(report: dict[str, Any], at_ms: int | None) -> dict[str, Any]:
    states = [
        row
        for row in report.get("strictPastExecutionStates", [])
        if at_ms is not None and int(row.get("atMs") or -1) >= at_ms
    ]
    errors = [abs(float(row.get("trackingError") or 0.0)) for row in states]
    if not errors:
        return {
            "postSecondStates": 0,
            "firstAbsTrackingError": None,
            "minimumAbsTrackingError": None,
            "halvedAtMs": None,
            "timeToHalveMs": None,
        }
    first = errors[0]
    threshold = first / 2.0
    halved = next(
        (row for row in states if abs(float(row.get("trackingError") or 0.0)) <= threshold),
        None,
    )
    halved_at = None if halved is None else int(halved["atMs"])
    return {
        "postSecondStates": len(states),
        "firstAbsTrackingError": first,
        "minimumAbsTrackingError": min(errors),
        "halveThreshold": threshold,
        "halvedAtMs": halved_at,
        "timeToHalveMs": None if halved_at is None or at_ms is None else halved_at - at_ms,
    }


def run_branch(market_id: int, branch: str) -> dict[str, Any]:
    policy = TwoStagePolicy(BRANCHES[branch])
    fault = FirstPairCompletionFault("SUBMIT_REJECT")
    behavior = FreezeDirectTakersAfterPairFault(fault)
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        lifecycle_action_override=policy,
        taker_submit_fault_override=fault,
        fault_reentry_enabled=True,
        behavior_policy_override=behavior,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
    )
    actual = report["actualExecution"]
    portfolio = actual["finalPortfolio"]
    second_at = None if policy.second_state is None else int(policy.second_state["atMs"])
    decisions = report["lifecycleAudit"]["decisions"]
    return {
        "marketId": int(market_id),
        "fault": "SUBMIT_REJECT",
        "faultTarget": "FIRST_PAIR_COMPLETION_REPLACE",
        "branch": branch,
        "secondAction": BRANCHES[branch],
        "runtimeSeconds": float(report["runtimeSeconds"]),
        "faultUsed": bool(fault.used),
        "faultCalls": fault.calls,
        "policyCalls": int(policy.calls),
        "secondState": policy.second_state,
        "secondStateHash": state_hash(policy.second_state),
        "directTakerFreezeLatched": behavior.latched,
        "directTakerFreezeProposalCount": behavior.proposals,
        "waitAct": {
            "WAIT": int(branch == "WAIT"),
            "ACT": int(branch == "PASSIVE_RETURN"),
            "actRate": float(branch == "PASSIVE_RETURN"),
            "definition": "Locked second lifecycle action: formal WAIT versus explicit passive repair ownership return.",
        },
        "mechanism": {
            "makerSubmits": int(report["r2ObjectiveExecution"]["makerSubmits"]),
            "makerFills": int(report["r2ObjectiveExecution"]["makerFills"]),
            "postSecondConfirmedMakerFills": _post_second_maker_fills(report, second_at),
            "returnActionCount": int(
                report["lifecycleAudit"]["actionCounts"].get("RETURN_TO_PASSIVE_REPAIR", 0)
            ),
            "ownershipReturnCount": int(
                report["lifecycleAudit"]["ownershipEventCounts"].get(
                    "OWNERSHIP_RETURNED_TO_PASSIVE_REPAIR", 0
                )
            ),
            "passiveReturnBlockedLiveChildCount": sum(
                row.get("action") == "PASSIVE_RETURN_BLOCKED_LIVE_CHILD" for row in decisions
            ),
            "behaviorDesiredMutationCount": sum(
                event.get("beforeDesired") != event.get("afterDesired")
                for event in report["r2ObjectiveExecution"].get("behaviorOverrideEvents", [])
                if "beforeDesired" in event
            ),
        },
        "recoveryLandmark": _recovery_landmark(report, second_at),
        "terminal": {
            "trackingErrorAreaShareSeconds": float(actual["targetErrorAreaShareSeconds"]),
            "finalAbsTrackingError": float(actual["finalAbsTrackingError"]),
            "pairedCoverage": _paired_coverage(portfolio),
            "makerFilledShares": float(actual["makerFilledShares"]),
            "takerFilledShares": float(actual["takerFilledShares"]),
            "worstCaseFloorAuditOnly": float(portfolio.get("worst_case_floor") or 0.0),
            "realizedPnlAuditOnly": actual.get("realizedPnl"),
        },
        "lifecycle": {
            "actionCounts": report["lifecycleAudit"]["actionCounts"],
            "ownershipEventCounts": report["lifecycleAudit"]["ownershipEventCounts"],
            "remainderOwnershipAtEnd": report["lifecycleAudit"]["remainderOwnershipAtEnd"],
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_branch = {row["branch"]: row for row in rows}
    if set(by_branch) != set(BRANCHES):
        return {"complete": False, "decision": "INCOMPLETE"}
    wait = by_branch["WAIT"]
    candidate = by_branch["PASSIVE_RETURN"]
    hashes = {row["secondStateHash"] for row in rows if row["secondStateHash"] is not None}
    matched = len(hashes) == 1
    area_reduction = _safe_reduction(
        wait["terminal"]["trackingErrorAreaShareSeconds"],
        candidate["terminal"]["trackingErrorAreaShareSeconds"],
    )
    residual_reduction = _safe_reduction(
        wait["terminal"]["finalAbsTrackingError"],
        candidate["terminal"]["finalAbsTrackingError"],
    )
    mechanism_gate = {
        "faultUsedBothBranches": all(row["faultUsed"] for row in rows),
        "matchedSecondStateHash": matched,
        "candidateReturnActionCountAtLeastOne": candidate["mechanism"]["returnActionCount"] >= 1,
        "candidateOwnershipReturnCountAtLeastOne": candidate["mechanism"]["ownershipReturnCount"] >= 1,
        "candidatePassiveReturnBlockedLiveChildCountZero": candidate["mechanism"][
            "passiveReturnBlockedLiveChildCount"
        ]
        == 0,
        "desiredMutationCountZero": all(
            row["mechanism"]["behaviorDesiredMutationCount"] == 0 for row in rows
        ),
        "cycleInvariantViolationCountZero": all(
            row["cycleInvariantViolationCount"] == 0 for row in rows
        ),
    }
    maker_fill_gate = candidate["mechanism"]["postSecondConfirmedMakerFills"]["count"] >= 1
    material_gate = bool(
        (area_reduction is not None and area_reduction >= 0.3)
        or (residual_reduction is not None and residual_reduction >= 0.3)
    )
    gate_pass = all(mechanism_gate.values()) and maker_fill_gate and material_gate
    oracle_area = min(
        wait["terminal"]["trackingErrorAreaShareSeconds"],
        candidate["terminal"]["trackingErrorAreaShareSeconds"],
    )
    oracle_residual = min(
        wait["terminal"]["finalAbsTrackingError"],
        candidate["terminal"]["finalAbsTrackingError"],
    )
    return {
        "complete": True,
        "contexts": 1,
        "matchedSecondState": matched,
        "secondStateHash": next(iter(hashes)) if matched else None,
        "waitAct": {
            "WAIT": 1,
            "ACT": 1,
            "actRate": 0.5,
            "candidatePolicyActRate": 1.0,
        },
        "mechanismGate": mechanism_gate,
        "confirmedPostSecondMakerFillGate": maker_fill_gate,
        "realizedRecovery": {
            "trackingErrorAreaWait": wait["terminal"]["trackingErrorAreaShareSeconds"],
            "trackingErrorAreaCandidate": candidate["terminal"]["trackingErrorAreaShareSeconds"],
            "trackingErrorAreaReduction": area_reduction,
            "terminalResidualWait": wait["terminal"]["finalAbsTrackingError"],
            "terminalResidualCandidate": candidate["terminal"]["finalAbsTrackingError"],
            "terminalResidualReduction": residual_reduction,
            "pairedCoverageWait": wait["terminal"]["pairedCoverage"],
            "pairedCoverageCandidate": candidate["terminal"]["pairedCoverage"],
        },
        "twoActionOracleCeiling": {
            "trackingErrorArea": oracle_area,
            "trackingErrorAreaReductionVsWait": _safe_reduction(
                wait["terminal"]["trackingErrorAreaShareSeconds"], oracle_area
            ),
            "terminalResidual": oracle_residual,
            "terminalResidualReductionVsWait": _safe_reduction(
                wait["terminal"]["finalAbsTrackingError"], oracle_residual
            ),
        },
        "materialRecoveryGate": material_gate,
        "gatePass": gate_pass,
        "decision": "KEEP_PILOT" if gate_pass else "REJECT_PASSIVE_RETURN_BASIC_PILOT",
        "chronologicalUnseenOos": False,
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1569361)
    parser.add_argument(
        "--output", default="hft_r2_repair_fault_passive_return_basic_exam_v1_report.json"
    )
    args = parser.parse_args()
    rows = []
    for branch in BRANCHES:
        row = run_branch(args.market_id, branch)
        rows.append(row)
        print(
            json.dumps(
                {
                    "marketId": args.market_id,
                    "branch": branch,
                    "faultUsed": row["faultUsed"],
                    "secondStateHash": row["secondStateHash"],
                    "area": row["terminal"]["trackingErrorAreaShareSeconds"],
                    "residual": row["terminal"]["finalAbsTrackingError"],
                    "postSecondMakerFills": row["mechanism"][
                        "postSecondConfirmedMakerFills"
                    ]["count"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    report = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": PREREGISTRATION,
        "marketId": int(args.market_id),
        "fault": "SUBMIT_REJECT",
        "branches": BRANCHES,
        "executionSemantics": {
            "simulator": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk queue",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "partialFills": True,
            "actualFillOwnStateFeedback": True,
            "dreamFill": False,
        },
        "rows": rows,
        "summary": summarize(rows),
        "guard": "Basic repair behavior exam only. PnL is audit-only and this opened train market is not unseen OOS evidence.",
    }
    output = OUT / args.output
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "output": str(output), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
