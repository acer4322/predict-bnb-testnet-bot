from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_FAULT_CONDITIONED_RECOVERY_MATRIX_V1"
ACTIONS = {
    "WAIT_PRESERVE": "WAIT_FOR_CLARITY",
    "CANCEL_ACK_BOUNDED_TAKER": "REPLACE_ROUTE",
    "CANCEL_ACK_RETIRE_REBASE": "RETIRE_OBLIGATION",
}
FAULTS = ("NATURAL_LIFECYCLE", "DEEP_FIRST_PASSIVE_CHILD")
FEATURE_NAMES = (
    "workingRecoveryExists",
    "workingRecoveryAgeMs",
    "workingRecoveryOffsetTicks",
    "workingRecoveryRemainingQty",
    "absTrackingError",
    "trackingError",
    "actualMakerNet",
    "actualCombinedGross",
    "secondsLeft",
    "recoveryBid",
    "recoveryAsk",
    "recoverySpreadTicks",
    "pairAskSum",
    "pairBidSum",
    "marginalSurplusChunkAvgCost",
    "lockedPairEdgePerShare",
    "lastMakerFillAgeMs",
    "lastMakerFillSideIsRecovery",
    "directionTowardRecovery",
    "spotReturn1sTowardRecovery",
    "spotReturn3sTowardRecovery",
    "spotQueueTowardRecovery",
    "spotTaker1sTowardRecovery",
    "futuresReturn1sTowardRecovery",
    "futuresReturn3sTowardRecovery",
    "futuresQueueTowardRecovery",
    "futuresTaker1sTowardRecovery",
    "asymmetryAgeMs",
    "observationDelayMs",
    "hasPriorObservation",
    "elapsedSincePriorMs",
    "recoveryStatusNew",
    "recoveryStatusPartial",
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def normalized_features(features: dict[str, Any]) -> dict[str, float]:
    return {name: finite(features.get(name)) for name in FEATURE_NAMES}


def state_hash(at_ms: int, side: str, features: dict[str, Any]) -> str:
    payload = {"atMs": int(at_ms), "side": str(side), "features": normalized_features(features)}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class FirstLifecycleAction:
    def __init__(self, lifecycle_action: str) -> None:
        self.lifecycle_action = lifecycle_action
        self.calls: list[dict[str, Any]] = []

    def __call__(self, state: dict[str, Any]) -> str:
        self.calls.append(
            {
                "atMs": int(state["atMs"]),
                "side": str(state["side"]),
                "defaultAction": str(state["defaultAction"]),
                "checkpointDelayMs": int(state["checkpointDelayMs"]),
                "trackingError": finite(state["trackingError"]),
                "features": normalized_features(state["features"]),
            }
        )
        return self.lifecycle_action


class DeepFirstPassiveChild:
    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.calls = 0
        self.applied: list[dict[str, Any]] = []

    def __call__(self, state: dict[str, Any]) -> float:
        self.calls += 1
        default_price = float(state["defaultPrice"])
        if not self.enabled or self.applied:
            return default_price
        override_price = 0.06
        opposite = state.get("oppositePrice")
        if opposite is not None and override_price + float(opposite) > 0.99 + 1e-8:
            override_price = default_price
        self.applied.append(
            {
                "atMs": int(state.get("atMs") or -1),
                "side": str(state.get("side") or "NONE"),
                "desiredAction": str(state.get("desiredAction") or "NONE"),
                "defaultPrice": default_price,
                "overridePrice": override_price,
                "effectiveDeepFault": override_price < default_price - 1e-8,
            }
        )
        return override_price


def first_lifecycle_decision(report: dict[str, Any]) -> dict[str, Any] | None:
    for row in report["lifecycleAudit"]["decisions"]:
        if isinstance(row.get("stateFeatures"), dict):
            return row
    return None


def compact_run(market_id: int, fault_id: str, action_id: str) -> dict[str, Any]:
    action_policy = FirstLifecycleAction(ACTIONS[action_id])
    price_fault = DeepFirstPassiveChild(fault_id == "DEEP_FIRST_PASSIVE_CHILD")
    started = time.perf_counter()
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        passive_price_policy=price_fault,
        lifecycle_action_override=action_policy,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
    )
    decision = first_lifecycle_decision(report)
    actual = report["actualExecution"]
    portfolio = actual["finalPortfolio"]
    replace_episodes = report["lifecycleAudit"]["replaceEpisodes"]
    cancel_episodes = [
        row
        for row in replace_episodes
        if row.get("cancelRequestedAtMs") is not None or row.get("childOrderNum") is not None
    ]
    return {
        "marketId": int(market_id),
        "faultId": fault_id,
        "actionId": action_id,
        "lifecycleAction": ACTIONS[action_id],
        "runtimeSeconds": time.perf_counter() - started,
        "actionable": decision is not None,
        "actionAppliedCalls": len(action_policy.calls),
        "stateAtFirstAction": None
        if decision is None
        else {
            "atMs": int(decision["atMs"]),
            "side": str(decision["side"]),
            "childStatus": str(decision.get("childStatus") or "NONE"),
            "stateHash": state_hash(int(decision["atMs"]), str(decision["side"]), decision["stateFeatures"]),
            "features": normalized_features(decision["stateFeatures"]),
        },
        "faultAudit": {
            "pricePolicyCalls": price_fault.calls,
            "applied": price_fault.applied,
            "effectiveDeepFault": any(row["effectiveDeepFault"] for row in price_fault.applied),
        },
        "terminal": {
            "worstCaseFloor": finite(portfolio.get("worst_case_floor")),
            "bestCasePnl": finite(portfolio.get("best_case_pnl")),
            "pairedCoverage": finite(portfolio.get("combined_paired_coverage")),
            "absPayoffGap": finite(portfolio.get("abs_payoff_gap")),
            "makerFilledShares": finite(actual.get("makerFilledShares")),
            "takerFilledShares": finite(actual.get("takerFilledShares")),
            "takerFeesUsdt": finite(actual.get("takerFeesUsdt")),
            "finalAbsNet": finite(actual.get("combinedFinalAbsNet")),
            "finalAbsTrackingError": finite(actual.get("finalAbsTrackingError")),
            "trackingErrorAreaShareSeconds": finite(actual.get("targetErrorAreaShareSeconds")),
            "exposureAreaShareSeconds": finite(actual.get("combinedExposureAreaShareSeconds")),
        },
        "lifecycle": {
            "actionCounts": report["lifecycleAudit"]["actionCounts"],
            "cancelEpisodes": cancel_episodes,
            "cancelPendingAtDataEnd": report["lifecycleAudit"]["cancelPendingAtDataEnd"],
            "cancelAckEvidenceCounts": report["lifecycleAudit"]["cancelAckEvidenceCounts"],
            "unresolvedTakerReturns": report["lifecycleAudit"]["unresolvedTakerReturns"],
            "remainderOwnershipAtEnd": report["lifecycleAudit"]["remainderOwnershipAtEnd"],
            "ownershipEventCounts": report["lifecycleAudit"]["ownershipEventCounts"],
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "semanticGate": report["semanticGate"],
    }


def context_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((int(row["marketId"]), str(row["faultId"])), []).append(row)
    results = []
    for (market_id, fault_id), group in grouped.items():
        by_action = {str(row["actionId"]): row for row in group}
        hashes = {
            row["stateAtFirstAction"]["stateHash"]
            for row in group
            if row.get("stateAtFirstAction") is not None
        }
        actionable = len(group) == len(ACTIONS) and all(row["actionable"] for row in group)
        matched = actionable and len(hashes) == 1
        wait_floor = finite(by_action.get("WAIT_PRESERVE", {}).get("terminal", {}).get("worstCaseFloor"))
        action_values = {
            action_id: finite(row["terminal"]["worstCaseFloor"]) - wait_floor
            for action_id, row in by_action.items()
        }
        oracle_action = max(action_values, key=lambda action: (action_values[action], action == "WAIT_PRESERVE"))
        results.append(
            {
                "marketId": market_id,
                "faultId": fault_id,
                "actionable": actionable,
                "matchedState": matched,
                "stateHashes": sorted(hashes),
                "waitWorstCaseFloor": wait_floor,
                "actionFloorAdvantagesVsWait": action_values,
                "oracleAction": oracle_action,
                "oracleAdvantageVsWait": action_values[oracle_action],
                "oracleWorstCaseFloor": finite(by_action[oracle_action]["terminal"]["worstCaseFloor"]),
                "allSemanticPass": all(
                    row["cycleInvariantViolationCount"] == 0
                    and row["semanticGate"].get("actualInventoryEqualsHftFillLedger")
                    and row["semanticGate"].get("noCycleInvariantViolation")
                    for row in group
                ),
            }
        )
    return sorted(results, key=lambda row: (row["marketId"], row["faultId"]))


def save_report(output: Path, market_ids: list[int], rows: list[dict[str, Any]], complete: bool) -> None:
    contexts = context_summaries(rows)
    matched = [row for row in contexts if row["matchedState"] and row["allSemanticPass"]]
    positive = [row for row in matched if row["oracleAction"] != "WAIT_PRESERVE" and row["oracleAdvantageVsWait"] > 1e-8]
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_fault_conditioned_recovery_matrix_v1_preregistered.json",
        "complete": bool(complete),
        "marketIds": market_ids,
        "faultProfiles": list(FAULTS),
        "actions": ACTIONS,
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "partialFills": True,
            "actualFillInventoryOnly": True,
            "formalWait": True,
            "targetFutureActionRuntimeInput": False,
            "winnerRuntimeInput": False,
        },
        "rows": rows,
        "contexts": contexts,
        "summary": {
            "runs": len(rows),
            "contexts": len(contexts),
            "matchedSemanticContexts": len(matched),
            "positiveNonWaitOracleContexts": len(positive),
            "positiveNonWaitOracleContextRate": len(positive) / len(matched) if matched else 0.0,
            "aggregateOracleAdvantageVsWait": sum(max(0.0, row["oracleAdvantageVsWait"]) for row in matched),
            "oracleActionCounts": {
                action_id: sum(row["oracleAction"] == action_id for row in matched) for action_id in ACTIONS
            },
            "semanticViolationRuns": sum(row["cycleInvariantViolationCount"] != 0 for row in rows),
        },
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-ids", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed-report")
    args = parser.parse_args()
    market_ids = [int(value) for value in args.market_ids.split(",") if value.strip()]
    output = BASE / args.output
    rows: list[dict[str, Any]] = []
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [row for row in (prior.get("rows") or []) if row.get("actionId") in ACTIONS]
    elif args.seed_report:
        prior = json.loads((BASE / args.seed_report).read_text(encoding="utf-8"))
        allowed_markets = set(market_ids)
        rows = [
            row
            for row in (prior.get("rows") or [])
            if int(row.get("marketId") or -1) in allowed_markets and row.get("actionId") in ACTIONS
        ]
    completed = {(int(row["marketId"]), str(row["faultId"]), str(row["actionId"])) for row in rows}
    total = len(market_ids) * len(FAULTS) * len(ACTIONS)
    for market_id in market_ids:
        for fault_id in FAULTS:
            for action_id in ACTIONS:
                key = (market_id, fault_id, action_id)
                if key in completed:
                    continue
                row = compact_run(market_id, fault_id, action_id)
                rows.append(row)
                save_report(output, market_ids, rows, complete=False)
                print(
                    json.dumps(
                        {
                            "progress": f"{len(rows)}/{total}",
                            "marketId": market_id,
                            "faultId": fault_id,
                            "actionId": action_id,
                            "actionable": row["actionable"],
                            "floor": row["terminal"]["worstCaseFloor"],
                            "violations": row["cycleInvariantViolationCount"],
                            "runtimeSeconds": row["runtimeSeconds"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    save_report(output, market_ids, rows, complete=True)
    final = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "output": str(output), **final["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
