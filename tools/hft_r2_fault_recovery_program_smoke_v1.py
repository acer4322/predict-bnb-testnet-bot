from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_FAULT_RECOVERY_PROGRAM_SMOKE_V1"
PROGRAMS = [
    {"programId": "WAIT_ALL", "maintain": "wait", "repair": "wait"},
    {"programId": "BOTH_OFFSET0", "maintain": "offset0", "repair": "offset0"},
    {"programId": "MAINTAIN_ONLY_OFFSET0", "maintain": "offset0", "repair": "wait"},
    {"programId": "REPAIR_ONLY_OFFSET0", "maintain": "wait", "repair": "offset0"},
]


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def nested(mapping: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    value: Any = mapping or {}
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


class MaintainBlackoutPolicy:
    """Drop the first N quote-active maintain decisions, then run a coarse program."""

    def __init__(self, dropped_decisions: int) -> None:
        self.dropped_decisions = int(dropped_decisions)
        self.faulted_decision_keys: list[str] = []
        self._faulted: set[str] = set()
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _decision_key(state: dict[str, Any]) -> str:
        decision = state.get("decision") or {}
        decision_id = decision.get("decisionId")
        if decision_id:
            return str(decision_id)
        return f"AT:{int(decision.get('decisionMs') or state.get('atMs') or -1)}"

    def __call__(self, state: dict[str, Any]) -> str:
        desired_action = str(state.get("desiredAction") or "NONE")
        decision_key = self._decision_key(state)
        selected_mode = str(state.get("defaultMode") or "wait")
        suppressed = False
        if desired_action == "PASSIVE_MAINTAIN":
            if decision_key in self._faulted:
                suppressed = True
            elif len(self.faulted_decision_keys) < self.dropped_decisions:
                self._faulted.add(decision_key)
                self.faulted_decision_keys.append(decision_key)
                suppressed = True
            if suppressed:
                selected_mode = "wait"
        self.calls.append(
            {
                "decisionKey": decision_key,
                "atMs": int(state.get("atMs") or -1),
                "desiredAction": desired_action,
                "side": str(state.get("side") or "NONE"),
                "selectedMode": selected_mode,
                "faultSuppressed": suppressed,
            }
        )
        return selected_mode

    def audit(self) -> dict[str, Any]:
        by_decision: dict[tuple[str, str], dict[str, Any]] = {}
        for call in self.calls:
            key = (call["decisionKey"], call["desiredAction"])
            row = by_decision.setdefault(
                key,
                {
                    "decisionKey": call["decisionKey"],
                    "atMs": call["atMs"],
                    "desiredAction": call["desiredAction"],
                    "sides": [],
                    "selectedModes": [],
                    "faultSuppressed": False,
                },
            )
            if call["side"] not in row["sides"]:
                row["sides"].append(call["side"])
            if call["selectedMode"] not in row["selectedModes"]:
                row["selectedModes"].append(call["selectedMode"])
            row["faultSuppressed"] = bool(row["faultSuppressed"] or call["faultSuppressed"])
        decisions = list(by_decision.values())
        act_decisions = sum("wait" not in row["selectedModes"] or len(row["selectedModes"]) > 1 for row in decisions)
        wait_decisions = len(decisions) - act_decisions
        return {
            "requestedDroppedMaintainDecisions": self.dropped_decisions,
            "observedDroppedMaintainDecisions": len(self.faulted_decision_keys),
            "faultedDecisionKeys": self.faulted_decision_keys,
            "quotePolicyCalls": len(self.calls),
            "quoteActiveDecisions": len(decisions),
            "passiveActDecisions": act_decisions,
            "passiveWaitDecisions": wait_decisions,
            "passiveActRate": act_decisions / len(decisions) if decisions else 0.0,
            "decisions": decisions,
        }


def summarize_run(
    market_id: int,
    program: dict[str, str],
    dropped_decisions: int,
    fault_id: str,
) -> dict[str, Any]:
    policy = MaintainBlackoutPolicy(dropped_decisions)
    started = time.perf_counter()
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={
            "PASSIVE_MAINTAIN": program["maintain"],
            "PASSIVE_REPAIR": program["repair"],
        },
        passive_policy=policy,
    )
    actual = report["actualExecution"]
    lifecycle = report["lifecycleAudit"]
    audit = policy.audit()
    return {
        **program,
        "faultId": fault_id,
        "droppedMaintainDecisions": int(dropped_decisions),
        "marketId": int(market_id),
        "runtimeSeconds": time.perf_counter() - started,
        "realizedPnlDescriptiveOnly": actual["realizedPnl"],
        "makerFilledShares": actual["makerFilledShares"],
        "takerFilledShares": actual["takerFilledShares"],
        "takerFeesUsdt": actual["takerFeesUsdt"],
        "finalAbsNet": actual["combinedFinalAbsNet"],
        "finalAbsTrackingError": actual["finalAbsTrackingError"],
        "finalWorstCaseFloor": nested(actual, "finalPortfolio", "worst_case_floor", default=0.0),
        "cycleInvariantViolationCount": report["cycleInvariantViolationCount"],
        "semanticGate": report["semanticGate"],
        "unresolvedTakerReturns": lifecycle["unresolvedTakerReturns"],
        "cancelPendingAtDataEnd": lifecycle["cancelPendingAtDataEnd"],
        "remainderOwnershipAtEnd": lifecycle["remainderOwnershipAtEnd"],
        "faultAudit": audit,
        "controller": {
            key: report["controller"][key]
            for key in (
                "steps",
                "highLevelDecisions",
                "desiredPortfolioActionCounts",
                "executionChoiceCounts",
                "optionTransitions",
            )
        },
        "strictPastOptionTransitions": report["strictPastOptionTransitions"],
        "terminalExecutionState": report["terminalExecutionState"],
    }


def is_semantically_valid(row: dict[str, Any]) -> bool:
    return bool(
        int(row["cycleInvariantViolationCount"]) == 0
        and row["semanticGate"].get("actualInventoryEqualsHftFillLedger")
        and int(row["faultAudit"]["observedDroppedMaintainDecisions"])
        == int(row["droppedMaintainDecisions"])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1573252)
    parser.add_argument("--output", default="hft_r2_fault_recovery_program_smoke_v1_report.json")
    parser.add_argument(
        "--preregistration",
        default="hft_r2_fault_recovery_program_smoke_v1_preregistered.json",
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    reference = summarize_run(
        args.market_id,
        next(program for program in PROGRAMS if program["programId"] == "BOTH_OFFSET0"),
        0,
        "NO_FAULT_REFERENCE",
    )
    rows.append(reference)
    print(
        json.dumps(
            {
                "progress": "1/9",
                "fault": reference["faultId"],
                "program": reference["programId"],
                "floor": reference["finalWorstCaseFloor"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    run_index = 1
    for dropped in (1, 2):
        fault_id = f"DROP_FIRST_{dropped}_MAINTAIN_DECISION" + ("S" if dropped != 1 else "")
        for program in PROGRAMS:
            row = summarize_run(args.market_id, program, dropped, fault_id)
            rows.append(row)
            run_index += 1
            print(
                json.dumps(
                    {
                        "progress": f"{run_index}/9",
                        "fault": fault_id,
                        "program": program["programId"],
                        "floor": row["finalWorstCaseFloor"],
                        "makerFilled": row["makerFilledShares"],
                        "takerFilled": row["takerFilledShares"],
                        "passiveActRate": row["faultAudit"]["passiveActRate"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    no_fault_floor = finite(reference["finalWorstCaseFloor"])
    profile_summaries = []
    for dropped in (1, 2):
        profile_rows = [row for row in rows if int(row["droppedMaintainDecisions"]) == dropped]
        wait_row = next(row for row in profile_rows if row["programId"] == "WAIT_ALL")
        wait_floor = finite(wait_row["finalWorstCaseFloor"])
        for row in profile_rows:
            row["floorValueVsFaultMatchedWait"] = finite(row["finalWorstCaseFloor"]) - wait_floor
        valid_rows = [row for row in profile_rows if is_semantically_valid(row)]
        if valid_rows:
            oracle = max(
                valid_rows,
                key=lambda row: (
                    finite(row["finalWorstCaseFloor"]),
                    -finite(row["finalAbsNet"]),
                    finite(row["makerFilledShares"]) + finite(row["takerFilledShares"]),
                ),
            )
            oracle_passes = bool(
                finite(oracle["finalWorstCaseFloor"]) >= 5.0
                and finite(oracle["makerFilledShares"]) + finite(oracle["takerFilledShares"]) > 0.0
            )
            profile_summaries.append(
                {
                    "droppedMaintainDecisions": dropped,
                    "faultId": oracle["faultId"],
                    "faultMatchedWaitFloor": wait_floor,
                    "constrainedOracleProgram": oracle["programId"],
                    "constrainedOracleWorstCaseFloor": oracle["finalWorstCaseFloor"],
                    "constrainedOracleFloorValueVsWait": oracle["floorValueVsFaultMatchedWait"],
                    "constrainedOraclePassiveActRate": oracle["faultAudit"]["passiveActRate"],
                    "constrainedOracleMakerFilledShares": oracle["makerFilledShares"],
                    "constrainedOracleTakerFilledShares": oracle["takerFilledShares"],
                    "recoveryFloorRetentionVsNoFault": finite(oracle["finalWorstCaseFloor"]) / no_fault_floor
                    if no_fault_floor > 0.0
                    else None,
                    "passesPreregisteredGate": oracle_passes,
                    "validPrograms": len(valid_rows),
                }
            )
        else:
            profile_summaries.append(
                {
                    "droppedMaintainDecisions": dropped,
                    "faultMatchedWaitFloor": wait_floor,
                    "constrainedOracleProgram": None,
                    "passesPreregisteredGate": False,
                    "validPrograms": 0,
                }
            )

    passes = sum(bool(row["passesPreregisteredGate"]) for row in profile_summaries)
    all_valid = all(is_semantically_valid(row) for row in rows)
    if not all_valid:
        decision = "NEED_MORE_DATA_BLOCKED_SEMANTIC_AUDIT"
    elif passes == 2:
        decision = "KEEP_RECOVERY_ARCHITECTURE"
    elif passes == 1:
        decision = "NEED_MORE_DATA"
    else:
        decision = "REJECT_RECOVERY_ACTION_FAMILY"

    output_report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": args.preregistration,
        "marketId": int(args.market_id),
        "cohort": "OPENED_ARCHITECTURE_DEVELOPMENT_REVEALED",
        "hypothesis": "Coarse integrated R2 programs can recover positive winner-free floor after deliberately dropped passive-maintain decisions without using Target actions as off-policy labels.",
        "faultSemantics": "First N distinct quote-active PASSIVE_MAINTAIN decisions force both quote sides to formal WAIT; submitted-resting-order no-fill is not claimed.",
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "logicAuthority": "FROZEN_R2",
            "lifecyclePolicy": "FIXED_KEEP_CURRENT_R2_OBJECTIVE",
            "winnerRuntimeInput": False,
            "targetFutureActionRuntimeInput": False,
            "targetFillRuntimeInput": False,
            "formalWait": True,
        },
        "primaryMetric": "terminal winner-free finalPortfolio.worst_case_floor",
        "programs": PROGRAMS,
        "runs": rows,
        "summary": {
            "runs": len(rows),
            "noFaultReferenceProgram": reference["programId"],
            "noFaultReferenceWorstCaseFloor": reference["finalWorstCaseFloor"],
            "noFaultReferencePassiveActRate": reference["faultAudit"]["passiveActRate"],
            "faultProfiles": profile_summaries,
            "learnedPolicyRealizedValue": None,
            "chronologicalUnseenOos": False,
            "allRunsSemanticAuditPass": all_valid,
            "decision": decision,
        },
        "next": "If kept, add partial-fill and cancelled-child fault states plus explicit obligation retire/rebase actions, then generate chronological episodes. Do not tune offsets on this revealed market.",
    }
    output = BASE / args.output
    output.write_text(json.dumps(output_report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), **output_report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
