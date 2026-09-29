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

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402
from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import (  # noqa: E402
    ACTIONS,
    BASE,
    FAULTS,
    DeepFirstPassiveChild,
    FirstLifecycleAction,
    first_lifecycle_decision,
    normalized_features,
    state_hash,
)


VERSION = "HFT_R2_FAULT_RECOVERY_MEMORY_V1"
WAIT = "WAIT_PRESERVE"
PUBLIC_ORIENTED = (
    "directionScore",
    "spotReturn3sBps",
    "spotQueueImbalance",
    "spotTakerImbalance1s",
    "futuresReturn3sBps",
    "futuresQueueImbalance",
    "futuresTakerImbalance1s",
)
BOOK_PATHS = (
    "recoveryBid",
    "recoveryAsk",
    "recoverySpreadTicks",
    "recoveryBidDepth",
    "recoveryAskDepth",
    "recoveryTop3BidDepth",
    "pairAskSum",
    "pairBidSum",
)
PORTFOLIO_PATHS = (
    "combinedGross",
    "combinedNetTowardRecovery",
    "combinedPairedCoverage",
    "worstCaseFloor",
    "absPayoffGap",
)
MODE_NAMES = (
    "PASSIVE_MAINTAIN",
    "PASSIVE_REPAIR",
    "TAKER_INTERVENE",
    "WAIT_KEEP",
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series_stats(values: list[float | None], prefix: str) -> dict[str, float]:
    clean = np.asarray([float(value) for value in values if value is not None and math.isfinite(float(value))])
    if not len(clean):
        return {
            f"{prefix}Available": 0.0,
            f"{prefix}Mean": 0.0,
            f"{prefix}Std": 0.0,
            f"{prefix}Delta": 0.0,
            f"{prefix}Min": 0.0,
            f"{prefix}Max": 0.0,
        }
    return {
        f"{prefix}Available": 1.0,
        f"{prefix}Mean": float(np.mean(clean)),
        f"{prefix}Std": float(np.std(clean)),
        f"{prefix}Delta": float(clean[-1] - clean[0]),
        f"{prefix}Min": float(np.min(clean)),
        f"{prefix}Max": float(np.max(clean)),
    }


def _oriented_public(trace: dict[str, Any], name: str, sign: float) -> float | None:
    value = finite_or_none((trace.get("publicState") or {}).get(name))
    return None if value is None else sign * value


def _book_paths(trace: dict[str, Any], side: str) -> dict[str, float | None]:
    book = trace.get("outcomeBook") or {}
    prefix = "up" if side == "UP" else "down"
    other = "down" if side == "UP" else "up"
    recovery_bid = finite_or_none(book.get(f"{prefix}_bid"))
    recovery_ask = finite_or_none(book.get(f"{prefix}_ask"))
    opposite_bid = finite_or_none(book.get(f"{other}_bid"))
    opposite_ask = finite_or_none(book.get(f"{other}_ask"))
    return {
        "recoveryBid": recovery_bid,
        "recoveryAsk": recovery_ask,
        "recoverySpreadTicks": finite_or_none(book.get(f"{prefix}_spread_ticks")),
        "recoveryBidDepth": finite_or_none(book.get(f"{prefix}_bid_depth")),
        "recoveryAskDepth": finite_or_none(book.get(f"{prefix}_ask_depth")),
        "recoveryTop3BidDepth": finite_or_none(book.get(f"{prefix}_top3_bid_depth")),
        "pairAskSum": None
        if recovery_ask is None or opposite_ask is None
        else recovery_ask + opposite_ask,
        "pairBidSum": None
        if recovery_bid is None or opposite_bid is None
        else recovery_bid + opposite_bid,
    }


def _portfolio_paths(trace: dict[str, Any], sign: float) -> dict[str, float | None]:
    portfolio = trace.get("actualPortfolioBeforeStep") or {}
    combined_net = finite_or_none(portfolio.get("combined_net"))
    return {
        "combinedGross": finite_or_none(portfolio.get("combined_gross")),
        "combinedNetTowardRecovery": None if combined_net is None else sign * combined_net,
        "combinedPairedCoverage": finite_or_none(portfolio.get("combined_paired_coverage")),
        "worstCaseFloor": finite_or_none(portfolio.get("worst_case_floor")),
        "absPayoffGap": finite_or_none(portfolio.get("abs_payoff_gap")),
    }


def _change_counts(values: list[float | None], prefix: str) -> dict[str, float]:
    pairs = [
        (float(left), float(right))
        for left, right in zip(values, values[1:])
        if left is not None and right is not None
    ]
    return {
        f"{prefix}UpMoves": float(sum(right > left + 1e-10 for left, right in pairs)),
        f"{prefix}DownMoves": float(sum(right < left - 1e-10 for left, right in pairs)),
        f"{prefix}UnchangedMoves": float(sum(abs(right - left) <= 1e-10 for left, right in pairs)),
    }


def _compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    decision = trace.get("decision")
    return {
        "atMs": int(trace["atMs"]),
        "executionTrigger": str(trace.get("executionTrigger") or "NONE"),
        "publicStateAsOfMs": int(trace.get("publicStateAsOfMs") or trace["atMs"]),
        "publicState": trace.get("publicState") or {},
        "outcomeBook": trace.get("outcomeBook") or {},
        "actualPortfolioBeforeStep": trace.get("actualPortfolioBeforeStep") or {},
        "decision": None
        if not isinstance(decision, dict)
        else {
            key: decision.get(key)
            for key in (
                "decisionId",
                "decisionMs",
                "phase",
                "desiredPortfolioAction",
                "executionChoice",
                "primaryReason",
                "direction",
            )
        },
    }


def summarize_memory(
    traces: list[dict[str, Any]],
    option_transitions: list[dict[str, Any]],
    decision: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    at_ms = int(decision["atMs"])
    side = str(decision["side"])
    sign = 1.0 if side == "UP" else -1.0
    point = normalized_features(decision["stateFeatures"])
    working_age = max(0.0, finite(point.get("workingRecoveryAgeMs")))
    last_fill_age = max(0.0, finite(point.get("lastMakerFillAgeMs")))
    if finite(point.get("workingRecoveryExists")) > 0.5 and working_age > 0.0:
        anchor_age = working_age
        anchor_kind = "WORKING_RECOVERY_CHILD_SUBMIT"
    elif last_fill_age > 0.0:
        anchor_age = last_fill_age
        anchor_kind = "LAST_CONFIRMED_MAKER_FILL"
    else:
        anchor_age = 5_000.0
        anchor_kind = "FIXED_5S_CAUSAL_FALLBACK"
    anchor_age = min(anchor_age, 60_000.0)
    anchor_ms = int(math.floor(at_ms - anchor_age))
    selected = [
        _compact_trace(trace)
        for trace in traces
        if anchor_ms <= int(trace["atMs"]) <= at_ms
    ]
    if not selected:
        prior = [trace for trace in traces if int(trace["atMs"]) <= at_ms]
        if not prior:
            raise RuntimeError("no strict-past controller trace at recovery checkpoint")
        selected = [_compact_trace(prior[-1])]
        anchor_ms = int(selected[0]["atMs"])
        anchor_kind += "_NEAREST_TRACE_ONLY"

    selected_transitions = [
        transition
        for transition in option_transitions
        if anchor_ms <= int(transition.get("atMs") or -1) <= at_ms
    ]
    times = [int(trace["atMs"]) for trace in selected]
    gaps = [right - left for left, right in zip(times, times[1:])]
    features: dict[str, float] = {
        "historyDurationMs": float(at_ms - anchor_ms),
        "historyTraceCount": float(len(selected)),
        "historyPublicTraceCount": float(sum(trace["executionTrigger"] == "PUBLIC_SNAPSHOT" for trace in selected)),
        "historyOwnStateTraceCount": float(sum(trace["executionTrigger"] == "OWN_STATE_EVENT" for trace in selected)),
        "historyMeanStepGapMs": float(np.mean(gaps)) if gaps else 0.0,
        "historyMaxStepGapMs": float(max(gaps)) if gaps else 0.0,
        "historyMeanPublicAgeMs": float(
            np.mean([max(0, int(trace["atMs"]) - int(trace["publicStateAsOfMs"])) for trace in selected])
        ),
        "historyMaxPublicAgeMs": float(
            max(max(0, int(trace["atMs"]) - int(trace["publicStateAsOfMs"])) for trace in selected)
        ),
        "historyWorkingChildAnchor": float(anchor_kind.startswith("WORKING_RECOVERY")),
        "historyLastFillAnchor": float(anchor_kind.startswith("LAST_CONFIRMED")),
        "historyOptionTransitionCount": float(len(selected_transitions)),
    }

    desired_actions = [
        str((trace.get("decision") or {}).get("desiredPortfolioAction") or "NONE") for trace in selected
    ]
    features["historyDesiredActionTransitions"] = float(
        sum(right != left for left, right in zip(desired_actions, desired_actions[1:]))
    )
    for mode in MODE_NAMES:
        features[f"historyModeFraction_{mode}"] = float(sum(value == mode for value in desired_actions)) / len(desired_actions)

    public_paths: dict[str, list[float | None]] = {
        name: [_oriented_public(trace, name, sign) for trace in selected] for name in PUBLIC_ORIENTED
    }
    for name, values in public_paths.items():
        features.update(_series_stats(values, f"history{name}TowardRecovery"))

    book_rows = [_book_paths(trace, side) for trace in selected]
    book_paths = {name: [row[name] for row in book_rows] for name in BOOK_PATHS}
    for name, values in book_paths.items():
        features.update(_series_stats(values, f"history{name}"))
    features.update(_change_counts(book_paths["recoveryBid"], "historyRecoveryBid"))
    features.update(_change_counts(book_paths["recoveryAsk"], "historyRecoveryAsk"))
    features.update(_change_counts(book_paths["recoveryBidDepth"], "historyRecoveryBidDepth"))
    features.update(_change_counts(book_paths["recoveryTop3BidDepth"], "historyRecoveryTop3BidDepth"))

    portfolio_rows = [_portfolio_paths(trace, sign) for trace in selected]
    portfolio_paths = {name: [row[name] for row in portfolio_rows] for name in PORTFOLIO_PATHS}
    for name, values in portfolio_paths.items():
        features.update(_series_stats(values, f"history{name}"))
    features.update(_change_counts(portfolio_paths["combinedGross"], "historyCombinedGross"))
    features["historyConfirmedInventoryChangeCount"] = float(
        sum(
            right is not None and left is not None and abs(float(right) - float(left)) > 1e-10
            for left, right in zip(portfolio_paths["combinedGross"], portfolio_paths["combinedGross"][1:])
        )
    )

    features["historyOptionStateAvailable"] = float(bool(selected_transitions))
    if selected_transitions:
        first = selected_transitions[0]
        last = selected_transitions[-1]
        recovery_revision = [finite((row.get("targetRevision") or {}).get(side)) for row in selected_transitions]
        other_side = "DOWN" if side == "UP" else "UP"
        other_revision = [finite((row.get("targetRevision") or {}).get(other_side)) for row in selected_transitions]
        token_counts = [float(len(row.get("responsibilityTokens") or [])) for row in selected_transitions]
        active_counts = [
            float(sum(value is not None for value in (row.get("activeMakerChildren") or {}).values()))
            for row in selected_transitions
        ]
        features.update(
            {
                "historyRecoveryTargetRevisionDelta": recovery_revision[-1] - recovery_revision[0],
                "historyOppositeTargetRevisionDelta": other_revision[-1] - other_revision[0],
                "historyResponsibilityTokenStart": token_counts[0],
                "historyResponsibilityTokenLast": token_counts[-1],
                "historyResponsibilityTokenMax": max(token_counts),
                "historyActiveMakerChildrenStart": active_counts[0],
                "historyActiveMakerChildrenLast": active_counts[-1],
                "historyActiveMakerChildrenMax": max(active_counts),
                "historyOptionTrackingErrorDelta": finite(last.get("trackingError")) - finite(first.get("trackingError")),
            }
        )
    else:
        for name in (
            "historyRecoveryTargetRevisionDelta",
            "historyOppositeTargetRevisionDelta",
            "historyResponsibilityTokenStart",
            "historyResponsibilityTokenLast",
            "historyResponsibilityTokenMax",
            "historyActiveMakerChildrenStart",
            "historyActiveMakerChildrenLast",
            "historyActiveMakerChildrenMax",
            "historyOptionTrackingErrorDelta",
        ):
            features[name] = 0.0

    audit = {
        "anchorKind": anchor_kind,
        "anchorAtMs": anchor_ms,
        "decisionAtMs": at_ms,
        "recoverySide": side,
        "strictPastInclusiveCurrentCheckpoint": True,
        "traces": selected,
        "optionTransitions": selected_transitions,
    }
    return features, audit


def memory_hash(point_hash: str, features: dict[str, float]) -> str:
    payload = {
        "pointStateHash": point_hash,
        "memoryFeatures": {key: finite(features[key]) for key in sorted(features)},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def collect_context(
    market_id: int,
    fault_id: str,
    expected: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    action_policy = FirstLifecycleAction(ACTIONS[WAIT])
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
        strict_past_trace_gate=lambda: len(action_policy.calls) == 0,
    )
    decision = first_lifecycle_decision(report)
    if decision is None:
        raise RuntimeError(f"no recovery checkpoint for {market_id}/{fault_id}")
    point_hash = state_hash(int(decision["atMs"]), str(decision["side"]), decision["stateFeatures"])
    source = expected[(int(market_id), str(fault_id))]
    expected_hash = str(source["stateAtFirstAction"]["stateHash"])
    if point_hash != expected_hash:
        raise RuntimeError(
            f"state hash mismatch for {market_id}/{fault_id}: replay={point_hash} matrix={expected_hash}"
        )
    features, history = summarize_memory(
        report["strictPastControllerTraces"],
        report["strictPastOptionTransitions"],
        decision,
    )
    return {
        "marketId": int(market_id),
        "faultId": str(fault_id),
        "pointStateHash": point_hash,
        "memoryStateHash": memory_hash(point_hash, features),
        "memoryFeatures": features,
        "historyAudit": history,
        "runtimeSeconds": time.perf_counter() - started,
        "waitTerminalFloor": finite(report["actualExecution"]["finalPortfolio"].get("worst_case_floor")),
        "expectedWaitTerminalFloor": finite(source["terminal"].get("worstCaseFloor")),
        "waitFloorMatchesPriorMatrix": abs(
            finite(report["actualExecution"]["finalPortfolio"].get("worst_case_floor"))
            - finite(source["terminal"].get("worstCaseFloor"))
        )
        <= 1e-8,
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "faultAudit": {
            "pricePolicyCalls": price_fault.calls,
            "applied": price_fault.applied,
            "effectiveDeepFault": any(row["effectiveDeepFault"] for row in price_fault.applied),
        },
    }


def save(output: Path, source_matrix: str, market_ids: list[int], rows: list[dict[str, Any]], complete: bool) -> None:
    feature_names = sorted({key for row in rows for key in row.get("memoryFeatures", {})})
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_fault_recovery_memory_v1_preregistered.json",
        "sourceMatrix": source_matrix,
        "complete": bool(complete),
        "marketIds": market_ids,
        "faultProfiles": list(FAULTS),
        "memoryFeatureNames": feature_names,
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "actualFillInventoryOnly": True,
            "formalWait": True,
            "historyEndsAtCurrentRecoveryCheckpoint": True,
            "futureTraceExcluded": True,
            "targetFutureActionRuntimeInput": False,
            "winnerRuntimeInput": False,
        },
        "rows": rows,
        "summary": {
            "contexts": len(rows),
            "pointStateHashMatches": len(rows),
            "waitFloorMatchesPriorMatrix": sum(row["waitFloorMatchesPriorMatrix"] for row in rows),
            "semanticViolationContexts": sum(row["cycleInvariantViolationCount"] != 0 for row in rows),
            "meanHistoryDurationMs": float(
                np.mean([row["memoryFeatures"]["historyDurationMs"] for row in rows])
            )
            if rows
            else 0.0,
            "meanHistoryTraceCount": float(
                np.mean([row["memoryFeatures"]["historyTraceCount"] for row in rows])
            )
            if rows
            else 0.0,
            "memoryUniqueStates": len({row["memoryStateHash"] for row in rows}),
        },
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-ids", required=True)
    parser.add_argument(
        "--source-matrix",
        default="hft_r2_fault_conditioned_recovery_train12_validation6_v1_report.json",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    market_ids = [int(value) for value in args.market_ids.split(",") if value.strip()]
    source = json.loads((BASE / args.source_matrix).read_text(encoding="utf-8"))
    expected = {
        (int(row["marketId"]), str(row["faultId"])): row
        for row in source["rows"]
        if row.get("actionId") == WAIT
    }
    output = BASE / args.output
    rows: list[dict[str, Any]] = []
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [
            row for row in prior.get("rows", []) if int(row.get("marketId") or -1) in set(market_ids)
        ]
    completed = {(int(row["marketId"]), str(row["faultId"])) for row in rows}
    total = len(market_ids) * len(FAULTS)
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    for market_id in market_ids:
        for fault_id in FAULTS:
            key = (market_id, fault_id)
            if key in completed:
                continue
            if key not in expected:
                raise RuntimeError(f"source matrix is missing {market_id}/{fault_id}")
            row = collect_context(market_id, fault_id, expected)
            rows.append(row)
            save(output, args.source_matrix, market_ids, rows, complete=False)
            print(
                json.dumps(
                    {
                        "progress": f"{len(rows)}/{total}",
                        "marketId": market_id,
                        "faultId": fault_id,
                        "pointStateHash": row["pointStateHash"],
                        "memoryStateHash": row["memoryStateHash"],
                        "historyDurationMs": row["memoryFeatures"]["historyDurationMs"],
                        "historyTraceCount": row["memoryFeatures"]["historyTraceCount"],
                        "waitFloorMatchesPriorMatrix": row["waitFloorMatchesPriorMatrix"],
                        "runtimeSeconds": row["runtimeSeconds"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    save(output, args.source_matrix, market_ids, rows, complete=True)
    final = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "output": str(output), **final["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
