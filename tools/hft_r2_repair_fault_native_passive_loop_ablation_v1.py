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
from tools.hft_r2_repair_fault_passive_return_basic_exam_v1 import (
    FreezeDirectTakersAfterPairFault,
    _paired_coverage,
    _post_second_maker_fills,
    _recovery_landmark,
    _safe_reduction,
)

OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_REPAIR_FAULT_NATIVE_PASSIVE_LOOP_ABLATION_V1"
PREREGISTRATION = "hft_r2_repair_fault_native_passive_loop_ablation_preregistered.json"
NOFILL_PREREGISTRATION = "hft_r2_repair_fault_native_passive_loop_nofill_preregistered.json"
SOURCE = OUT / "hft_r2_repair_fault_passive_return_basic_exam_v1_report.json"


def run_branch(market_id: int, fault_mode: str, freeze_makers: bool) -> dict[str, Any]:
    policy = TwoStagePolicy("WAIT_FOR_CLARITY")
    fault = FirstPairCompletionFault(fault_mode)
    behavior = FreezeDirectTakersAfterPairFault(fault, freeze_makers=freeze_makers)
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
    return {
        "marketId": int(market_id),
        "role": (
            "FAULT_MATCHED_NO_NEW_EXECUTION_CHILDREN"
            if freeze_makers
            else "NATIVE_PASSIVE_LOOP"
        ),
        "faultMode": fault_mode,
        "faultUsed": bool(fault.used),
        "secondState": policy.second_state,
        "secondStateHash": state_hash(policy.second_state),
        "directTakerFreezeLatched": behavior.latched,
        "freezeProposalCount": behavior.proposals,
        "mechanism": {
            "makerSubmits": int(report["r2ObjectiveExecution"]["makerSubmits"]),
            "makerFills": int(report["r2ObjectiveExecution"]["makerFills"]),
            "postSecondConfirmedMakerFills": _post_second_maker_fills(report, second_at),
            "makerExecutionBlocks": sum(
                event.get("proposal", {}).get("blocked") == "MAKER_EXECUTION_CHILD"
                for event in report["r2ObjectiveExecution"].get("behaviorOverrideEvents", [])
            ),
            "desiredMutationCount": sum(
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
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "runtimeSeconds": float(report["runtimeSeconds"]),
    }


def run_no_repair(market_id: int, fault_mode: str = "SUBMIT_REJECT") -> dict[str, Any]:
    return run_branch(market_id, fault_mode, freeze_makers=True)


def source_native_row(market_id: int) -> dict[str, Any]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    row = next(
        row
        for row in source["rows"]
        if int(row["marketId"]) == market_id and row["branch"] == "WAIT"
    )
    return row


def summarize(native: dict[str, Any], no_repair: dict[str, Any]) -> dict[str, Any]:
    matched = native["secondStateHash"] == no_repair["secondStateHash"]
    native_landmark = native["recoveryLandmark"]
    no_repair_landmark = no_repair["recoveryLandmark"]
    native_halved = native_landmark.get("halvedAtMs") is not None
    no_repair_halved = no_repair_landmark.get("halvedAtMs") is not None
    area_reduction = _safe_reduction(
        no_repair["terminal"]["trackingErrorAreaShareSeconds"],
        native["terminal"]["trackingErrorAreaShareSeconds"],
    )
    residual_reduction = _safe_reduction(
        no_repair["terminal"]["finalAbsTrackingError"],
        native["terminal"]["finalAbsTrackingError"],
    )
    safety = (
        matched
        and native["faultUsed"]
        and no_repair["faultUsed"]
        and native["cycleInvariantViolationCount"] == 0
        and no_repair["cycleInvariantViolationCount"] == 0
        and no_repair["mechanism"]["desiredMutationCount"] == 0
    )
    local_gate = native_halved and not no_repair_halved
    material_global = bool(
        (area_reduction is not None and area_reduction >= 0.3)
        or (residual_reduction is not None and residual_reduction >= 0.3)
    )
    confirmed_native_fill = (
        native["mechanism"]["postSecondConfirmedMakerFills"]["count"] >= 1
    )
    keep = safety and confirmed_native_fill and local_gate and material_global
    if keep:
        decision = "KEEP_NATIVE_PASSIVE_REPAIR_EVIDENCE"
    elif safety and confirmed_native_fill and material_global:
        decision = "NEED_MORE_DATA_OBLIGATION_ATTRIBUTION"
    else:
        decision = "REJECT_NATIVE_PASSIVE_REPAIR_ON_THIS_CONTEXT"
    return {
        "matchedSecondState": matched,
        "faultUsedBoth": native["faultUsed"] and no_repair["faultUsed"],
        "waitAct": {
            "lifecycleWAIT": 2,
            "lifecycleACT": 0,
            "nativePassivePostSecondConfirmedFills": native["mechanism"][
                "postSecondConfirmedMakerFills"
            ]["count"],
            "noRepairPostSecondConfirmedFills": no_repair["mechanism"][
                "postSecondConfirmedMakerFills"
            ]["count"],
        },
        "confirmedNativePostSecondMakerFillGate": confirmed_native_fill,
        "localRecovery": {
            "nativePassive": native_landmark,
            "noRepair": no_repair_landmark,
            "nativeHalvedAndNoRepairDidNot": local_gate,
        },
        "globalRecovery": {
            "trackingErrorAreaNative": native["terminal"]["trackingErrorAreaShareSeconds"],
            "trackingErrorAreaNoRepair": no_repair["terminal"]["trackingErrorAreaShareSeconds"],
            "trackingErrorAreaReduction": area_reduction,
            "terminalResidualNative": native["terminal"]["finalAbsTrackingError"],
            "terminalResidualNoRepair": no_repair["terminal"]["finalAbsTrackingError"],
            "terminalResidualReduction": residual_reduction,
            "material30PercentGate": material_global,
        },
        "safetyPass": safety,
        "decision": decision,
        "scope": "mechanism evidence only; one opened train market, not chronological unseen OOS or economic promotion evidence",
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1569361)
    parser.add_argument(
        "--fault-mode",
        choices=["SUBMIT_REJECT", "NO_FILL_STALL"],
        default="SUBMIT_REJECT",
    )
    parser.add_argument(
        "--output", default="hft_r2_repair_fault_native_passive_loop_ablation_v1_report.json"
    )
    args = parser.parse_args()
    if args.fault_mode == "SUBMIT_REJECT":
        native = source_native_row(args.market_id)
        source_report: str | None = SOURCE.name
        preregistration = PREREGISTRATION
    else:
        native = run_branch(args.market_id, args.fault_mode, freeze_makers=False)
        source_report = None
        preregistration = NOFILL_PREREGISTRATION
    no_repair = run_no_repair(args.market_id, args.fault_mode)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": preregistration,
        "sourceReport": source_report,
        "marketId": int(args.market_id),
        "fault": f"FIRST_PAIR_COMPLETION_REPLACE_{args.fault_mode}",
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
        "nativePassiveLoop": native,
        "noRepairAblation": no_repair,
        "summary": summarize(native, no_repair),
    }
    output = OUT / args.output
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "output": str(output), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
