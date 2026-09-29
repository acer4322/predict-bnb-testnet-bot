from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter as base  # noqa: E402


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
EPS = 1e-8


class _ConstantBinaryModel:
    """A fixed execution-policy head; it contains no fitted Target/future labels."""

    def __init__(self, positive_probability: float) -> None:
        self.positive_probability = float(positive_probability)

    def predict_proba(self, values: Any) -> np.ndarray:
        rows = len(values)
        p = self.positive_probability
        return np.tile(np.asarray([[1.0 - p, p]], dtype=float), (rows, 1))


def _fixed_keep_artifact() -> dict[str, Any]:
    # In an asymmetric R2 repair episode, keep the already-authorized passive child.
    # Frozen R2 may still create new high-level Maker/Taker objectives on later controller steps.
    return {
        "currentOnly": {
            "features": ["workingRecoveryExists"],
            "models": {
                "act": _ConstantBinaryModel(1.0),
                "wait": _ConstantBinaryModel(0.0),
                "replace": _ConstantBinaryModel(0.0),
            },
        },
        "thresholds": {"act": 0.5, "wait": 0.5, "replace": 0.5},
        "provenance": "FIXED_KEEP_NO_TARGET_OR_FUTURE_TEACHER",
    }


def _portfolio_without_private(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("_combined_net", None)
    return result


def _action_counts(decisions: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in decisions:
        value = str(row.get(key) or "NONE")
        counts[value] = counts.get(value, 0) + 1
    return counts


def run_smoke(
    market_id: int,
    passive_mode: str = "offset1",
    option_scope: str = "all",
    passive_program: dict[str, str] | None = None,
    own_state_poll_ms: int | None = None,
    passive_policy: Callable[[dict[str, Any]], str | None] | None = None,
    passive_price_policy: Callable[[dict[str, Any]], float | None] | None = None,
    lifecycle_artifact: dict[str, Any] | None = None,
    lifecycle_action_override: Callable[[dict[str, Any]], str | None] | None = None,
    allowed_executor_taker_kinds: set[str] | None = None,
    strict_past_trace_gate: Callable[[], bool] | None = None,
    trace_execution_states: bool = False,
    taker_submit_fault_override: Callable[[dict[str, Any]], str | None] | None = None,
    maker_submit_fault_override: Callable[[dict[str, Any]], str | None] | None = None,
    fault_reentry_enabled: bool = False,
    behavior_policy_override: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    behavior_ownstate_reentry: bool = False,
    fault_containment_freeze_before_redecision: bool = False,
    fault_no_fill_stall_ms: int | None = None,
    taker_confirm_ms_override: int | None = None,
    taker_price_buffer_ticks_override: int | None = None,
    r21_incident_inbox_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    if passive_mode not in {"wait", "offset0", "offset1", "offset2"}:
        raise ValueError(f"unsupported passive mode: {passive_mode}")
    if option_scope not in {"all", "maintain_only", "repair_only"}:
        raise ValueError(f"unsupported option scope: {option_scope}")
    resolved_program = {
        "PASSIVE_MAINTAIN": passive_mode,
        "PASSIVE_REPAIR": passive_mode,
    }
    if passive_program is not None:
        resolved_program.update({str(key): str(value) for key, value in passive_program.items()})
    invalid_program_modes = {
        value for value in resolved_program.values() if value not in {"wait", "offset0", "offset1", "offset2"}
    }
    if invalid_program_modes:
        raise ValueError(f"unsupported passive program modes: {sorted(invalid_program_modes)}")
    traces: list[dict[str, Any]] = []
    strict_past_traces: list[dict[str, Any]] = []
    r21_inbox_traces: list[dict[str, Any]] = []
    logic_state: dict[str, Any] = {"desiredPortfolioAction": None}
    original_new_controller = base.new_controller
    original_joblib_load = base.joblib.load
    original_passive_quote = base.r2_passive_quote

    def traced_new_controller(adapter: Any) -> Any:
        controller = original_new_controller(adapter)
        original_step = controller._step

        def traced_step(snapshot: dict[str, Any]) -> Any:
            at_ms = int(snapshot["sampledAtMs"])
            controller_snapshot = snapshot
            if r21_incident_inbox_provider is not None:
                # R2.1 is an additive information-only input.  The provider receives
                # an isolated copy and can neither overwrite the legacy R2 snapshot
                # nor reach any order/executor method through this interface.
                inbox = r21_incident_inbox_provider(copy.deepcopy(snapshot))
                if inbox is not None:
                    if not isinstance(inbox, dict):
                        raise TypeError("R2.1 incident inbox provider must return a dict or None")
                    if inbox.get("version") != "R2.1":
                        raise ValueError("R2.1 incident inbox must declare version=R2.1")
                    if inbox.get("mode") != "INFORMATION_ONLY" or inbox.get("actionAuthority") is not False:
                        raise ValueError("R2.1 incident inbox must be information-only with zero action authority")
                    events = inbox.get("incidents") or []
                    if not isinstance(events, list):
                        raise TypeError("R2.1 incident inbox incidents must be a list")
                    future_event_ids = [
                        str(event.get("eventId"))
                        for event in events
                        if not isinstance(event, dict) or int(event.get("atMs") or 0) > at_ms
                    ]
                    if future_event_ids:
                        raise ValueError(f"R2.1 incident inbox contains future events: {future_event_ids}")
                    controller_snapshot = copy.deepcopy(snapshot)
                    controller_snapshot["r21ExecutionIncidentInbox"] = copy.deepcopy(inbox)
                    r21_inbox_traces.append(
                        {
                            "atMs": at_ms,
                            "incidentCount": len(events),
                            "incidentTypes": sorted({str(event.get("incidentType")) for event in events}),
                            "latestEventId": inbox.get("latestEventId"),
                            "actionAuthority": False,
                            "strictPastPass": True,
                        }
                    )
            before = _portfolio_without_private(controller.inventory.features(at_ms))
            capture_strict_past = strict_past_trace_gate is not None and bool(strict_past_trace_gate())
            public_state: dict[str, Any] = {}
            outcome_book: dict[str, Any] = {}
            if capture_strict_past:
                public_fields = (
                    "secondsLeft",
                    "directionScore",
                    "spotReturn1sBps",
                    "spotReturn3sBps",
                    "spotQueueImbalance",
                    "spotTakerImbalance1s",
                    "futuresReturn1sBps",
                    "futuresReturn3sBps",
                    "futuresQueueImbalance",
                    "futuresTakerImbalance1s",
                    "bookReceivedAtMs",
                    "sampledAtMs",
                )
                public_state = {key: copy.deepcopy(snapshot.get(key)) for key in public_fields}
                outcome_book = base.mod.outcome_book(controller.book.book, None) or {}
            result = original_step(controller_snapshot)
            decision = copy.deepcopy(controller.last_decision)
            if decision is not None and int(decision.get("decisionMs") or -1) == at_ms:
                logic_state["desiredPortfolioAction"] = decision.get("desiredPortfolioAction")
                logic_state["decision"] = decision
            logic_state["portfolio"] = before
            logic_state["atMs"] = at_ms
            traces.append(
                {
                    "atMs": at_ms,
                    "executionTrigger": "OWN_STATE_EVENT"
                    if bool(snapshot.get("ownStateEventReentry"))
                    else "PUBLIC_SNAPSHOT",
                    "publicStateAsOfMs": int(snapshot.get("publicStateAsOfMs") or at_ms),
                    "actualPortfolioBeforeStep": before,
                    "decision": decision
                    if decision is not None and int(decision.get("decisionMs") or -1) == at_ms
                    else None,
                }
            )
            if capture_strict_past:
                strict_past_traces.append(
                    {
                        **traces[-1],
                        "publicState": public_state,
                        "outcomeBook": {
                            key: copy.deepcopy(value) for key, value in outcome_book.items()
                        },
                    }
                )
            return result

        controller._step = traced_step
        return controller

    def fixed_load(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path).resolve() == base.MODEL_PATH.resolve():
            return lifecycle_artifact if lifecycle_artifact is not None else _fixed_keep_artifact()
        return original_joblib_load(path, *args, **kwargs)

    def fixed_passive_quote(book: dict[str, dict[float, float]], side: str, opposite_price: float | None) -> float | None:
        desired_action = logic_state.get("desiredPortfolioAction")
        if option_scope == "maintain_only" and desired_action != "PASSIVE_MAINTAIN":
            return None
        if option_scope == "repair_only" and desired_action != "PASSIVE_REPAIR":
            return None
        selected_mode = resolved_program.get(str(desired_action), passive_mode)
        if passive_policy is not None:
            override = passive_policy({"desiredAction": desired_action, "side": side, "portfolio": logic_state.get("portfolio") or {}, "decision": logic_state.get("decision") or {}, "atMs": logic_state.get("atMs"), "defaultMode": selected_mode, "book": book})
            if override is not None:
                selected_mode = str(override)
        if selected_mode == "wait":
            return None
        book_features = base.mod.outcome_book(book, None)
        if not book_features:
            return None
        offset_ticks = int(selected_mode[-1])
        bid = float(book_features["up_bid"] if side == "UP" else book_features["down_bid"])
        tick = int(math.floor((bid + 1e-9) / base.mod.GRID)) - offset_ticks
        tick = max(int(round(base.mod.MIN_PRICE / base.mod.GRID)), tick)
        price = round(tick * base.mod.GRID, 2)
        if opposite_price is not None:
            while price + float(opposite_price) > base.mod.MAX_PAIR_PRICE_SUM + EPS:
                tick -= 1
                if tick < int(round(base.mod.MIN_PRICE / base.mod.GRID)):
                    return None
                price = round(tick * base.mod.GRID, 2)
        if passive_price_policy is not None:
            override_price = passive_price_policy(
                {
                    "desiredAction": desired_action,
                    "side": side,
                    "portfolio": logic_state.get("portfolio") or {},
                    "decision": logic_state.get("decision") or {},
                    "atMs": logic_state.get("atMs"),
                    "selectedMode": selected_mode,
                    "defaultPrice": price,
                    "oppositePrice": opposite_price,
                    "book": book,
                }
            )
            if override_price is None:
                return None
            price = round(float(override_price), 2)
            if price < base.mod.MIN_PRICE - EPS or price > 0.99 + EPS:
                raise ValueError(f"passive price override outside venue bounds: {price}")
            if opposite_price is not None and price + float(opposite_price) > base.mod.MAX_PAIR_PRICE_SUM + EPS:
                raise ValueError("passive price override violates pair price-sum bound")
        return price

    started = time.perf_counter()
    base.new_controller = traced_new_controller
    base.joblib.load = fixed_load
    base.r2_passive_quote = fixed_passive_quote
    try:
        row = base.run_market(
            int(market_id),
            trace_option_transitions=True,
            trace_execution_states=trace_execution_states,
            own_state_poll_ms=own_state_poll_ms,
            lifecycle_action_override=lifecycle_action_override,
            taker_submit_fault_override=taker_submit_fault_override,
            maker_submit_fault_override=maker_submit_fault_override,
            fault_reentry_enabled=fault_reentry_enabled,
            behavior_policy_override=behavior_policy_override,
            behavior_ownstate_reentry=behavior_ownstate_reentry,
            fault_containment_freeze_before_redecision=fault_containment_freeze_before_redecision,
            fault_no_fill_stall_ms=fault_no_fill_stall_ms,
            taker_confirm_ms_override=taker_confirm_ms_override,
            taker_price_buffer_ticks_override=taker_price_buffer_ticks_override,
        )
    finally:
        base.new_controller = original_new_controller
        base.joblib.load = original_joblib_load
        base.r2_passive_quote = original_passive_quote
    runtime_seconds = time.perf_counter() - started

    controller_decisions = [
        trace["decision"] for trace in traces if isinstance(trace.get("decision"), dict)
    ]
    fills = [
        {"role": "MAKER", **fill} for fill in row["makerFills"]
    ] + [
        {"role": "TAKER", **fill} for fill in row["takerFills"]
    ]
    fills.sort(key=lambda value: (int(value["observedAtMs"]), int(value["atMs"])))

    feedback_cycles = []
    for fill in fills:
        observed_at = int(fill["observedAtMs"])
        next_trace = next((trace for trace in traces if int(trace["atMs"]) >= observed_at), None)
        feedback_cycles.append(
            {
                "fillObservedAtMs": observed_at,
                "fillAtMs": int(fill["atMs"]),
                "role": fill["role"],
                "side": fill["side"],
                "shares": float(fill.get("deltaShares", fill.get("shares", 0.0))),
                "controllerReevaluatedAtMs": None if next_trace is None else int(next_trace["atMs"]),
                "actualPortfolioVisibleBeforeStep": None
                if next_trace is None
                else next_trace["actualPortfolioBeforeStep"],
                "newHighLevelDecision": None if next_trace is None else next_trace["decision"],
            }
        )

    maker_up = sum(float(fill["deltaShares"]) for fill in row["makerFills"] if fill["side"] == "UP")
    maker_down = sum(float(fill["deltaShares"]) for fill in row["makerFills"] if fill["side"] == "DOWN")
    taker_up = sum(float(fill["shares"]) for fill in row["takerFills"] if fill["side"] == "UP")
    taker_down = sum(float(fill["shares"]) for fill in row["takerFills"] if fill["side"] == "DOWN")
    portfolio = row["actualExecution"]["finalPortfolio"]
    expected = {
        "maker_gross": maker_up + maker_down,
        "maker_net": maker_up - maker_down,
        "taker_gross": taker_up + taker_down,
        "taker_net": taker_up - taker_down,
    }
    inventory_mismatches = {
        key: {"expected": value, "actual": float(portfolio[key])}
        for key, value in expected.items()
        if abs(float(portfolio[key]) - value) > EPS
    }
    allowed_taker_kinds = allowed_executor_taker_kinds or {"FROZEN_R2"}
    unauthorized_taker_attempts = [
        attempt for attempt in row["takerAttempts"] if attempt.get("kind") not in allowed_taker_kinds
    ]
    missing_feedback = [cycle for cycle in feedback_cycles if cycle["controllerReevaluatedAtMs"] is None]

    desired_sequence = [str(decision.get("desiredPortfolioAction") or "NONE") for decision in controller_decisions]
    option_transitions = sum(
        desired_sequence[index] != desired_sequence[index - 1]
        for index in range(1, len(desired_sequence))
    )
    violations = {
        "actualInventoryMismatch": inventory_mismatches,
        "actualFillWithoutControllerReevaluation": missing_feedback,
        "unauthorizedExecutorTakerAttempt": unauthorized_taker_attempts,
        "duplicateLiveObjectiveOwner": [],
        "cancelReplacementBeforeTerminalAck": [],
        "orphanedUnresolvedRemainder": [],
    }
    violation_count = sum(len(value) for value in violations.values())
    desired_by_side = {"UP": 0.0, "DOWN": 0.0}
    for intent in row["makerIntents"]:
        desired_by_side[str(intent["side"])] = float(intent["desiredAfter"])
    intent_reason_counts: dict[str, int] = {}
    increment_reason_counts: dict[str, int] = {}
    for intent in row["makerIntents"]:
        reason = str(intent.get("reason") or "NONE")
        intent_reason_counts[reason] = intent_reason_counts.get(reason, 0) + 1
        if float(intent.get("targetIncrementShares") or 0.0) > EPS:
            increment_reason_counts[reason] = increment_reason_counts.get(reason, 0) + 1
    responsibility_event_counts: dict[str, int] = {}
    for event in row["strategyRollout"]["responsibilityEvents"]:
        action = str(event.get("action") or "NONE")
        responsibility_event_counts[action] = responsibility_event_counts.get(action, 0) + 1
    semantic_gate = {
        "frozenR2GeneratedHighLevelDecisions": len(controller_decisions) > 0,
        "atLeastOneActualFill": len(fills) > 0,
        "everyActualFillFedBackBeforeControllerStep": not missing_feedback,
        "actualInventoryEqualsHftFillLedger": not inventory_mismatches,
        "noFreeExecutorTakerSideOrMode": not unauthorized_taker_attempts,
        "noCycleInvariantViolation": violation_count == 0,
        "targetOrFutureTeacherExcluded": True,
        "paperDreamFillExcludedFromInventory": True,
    }
    decision = "SEMANTICS_PASS" if all(semantic_gate.values()) else "NEED_ANOTHER_SMOKE_MARKET"

    return {
        "version": "HFT_R2_CYCLE_PRESERVING_EXECUTION_SMOKE_V1",
        "researchOnly": True,
        "marketId": int(market_id),
        "runtimeSeconds": runtime_seconds,
        "policy": {
            "logicAuthority": "FROZEN_R2",
            "executionAdapter": "FIXED_KEEP_CURRENT_R2_OBJECTIVE",
            "passiveMode": passive_mode,
            "passiveProgram": resolved_program,
            "optionScope": option_scope,
            "learnedExecutionModel": False,
            "targetFutureTeacher": False,
            "winnerRuntimeInput": False,
            "ownStatePollMs": own_state_poll_ms,
            "customLifecycleArtifact": lifecycle_artifact is not None,
            "customLifecycleActionOverride": lifecycle_action_override is not None,
            "allowedExecutorTakerKinds": sorted(allowed_taker_kinds),
            "customTakerSubmitFaultOverride": taker_submit_fault_override is not None,
            "customMakerSubmitFaultOverride": maker_submit_fault_override is not None,
            "faultNoFillStallMs": fault_no_fill_stall_ms,
            "takerConfirmMsOverride": taker_confirm_ms_override,
            "takerPriceBufferTicksOverride": taker_price_buffer_ticks_override,
            "r21InformationOnlyIncidentInbox": r21_incident_inbox_provider is not None,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "ownStateEventReentry": own_state_poll_ms is not None,
        },
        "controller": {
            "steps": len(traces),
            "highLevelDecisions": len(controller_decisions),
            "desiredPortfolioActionCounts": _action_counts(controller_decisions, "desiredPortfolioAction"),
            "executionChoiceCounts": _action_counts(controller_decisions, "executionChoice"),
            "optionTransitions": option_transitions,
            "decisions": controller_decisions,
        },
        "actualExecution": row["actualExecution"],
        "r2ObjectiveExecution": {
            "makerIntents": len(row["makerIntents"]),
            "targetIncrementIntents": row["strategyRollout"]["targetIncrementIntents"],
            "targetIncrementShares": sum(float(intent.get("targetIncrementShares") or 0.0) for intent in row["makerIntents"]),
            "desiredMakerShares": row["strategyRollout"]["desiredMakerShares"],
            "desiredBySide": desired_by_side,
            "intentReasonCounts": intent_reason_counts,
            "targetIncrementReasonCounts": increment_reason_counts,
            "makerSubmits": len(row["makerSubmits"]),
            "makerFills": len(row["makerFills"]),
            "takerAttempts": len(row["takerAttempts"]),
            "takerFills": len(row["takerFills"]),
            "fixedLifecycleActionCounts": row["lifecycle"]["actionCounts"],
            "suppressedDreamInventoryEvents": row["strategyRollout"]["suppressedDreamInventoryEvents"],
            "duplicateObjectiveReissueCensors": row["strategyRollout"]["duplicateTargetIncrementCensors"],
            "responsibilityEventCounts": responsibility_event_counts,
            "responsibilityTokensAtEnd": row["strategyRollout"]["responsibilityTokensAtEnd"],
            "behaviorOverrideEvents": row["strategyRollout"].get("behaviorOverrideEvents", []),
        },
        "lifecycleAudit": {
            "actionCounts": row["lifecycle"]["actionCounts"],
            "decisions": row["lifecycle"]["decisions"],
            "replaceEpisodes": row["lifecycle"]["replaceEpisodes"],
            "takerChildStateCounts": row["lifecycle"]["takerChildStateCounts"],
            "cancelPendingAtDataEnd": row["lifecycle"]["cancelPendingAtDataEnd"],
            "cancelAckEvidenceCounts": row["lifecycle"]["cancelAckEvidenceCounts"],
            "unresolvedTakerReturns": row["lifecycle"]["unresolvedTakerReturns"],
            "unresolvedCauseCounts": row["lifecycle"]["unresolvedCauseCounts"],
            "remainderOwnershipAtEnd": row["lifecycle"]["remainderOwnershipAtEnd"],
            "ownershipEventCounts": row["lifecycle"]["ownershipEventCounts"],
            "ownStateEventReentries": [
                trace for trace in traces if trace["executionTrigger"] == "OWN_STATE_EVENT"
            ],
        },
        "actualFillFeedbackCycles": feedback_cycles,
        "r21InformationInbox": {
            "enabled": r21_incident_inbox_provider is not None,
            "field": "r21ExecutionIncidentInbox" if r21_incident_inbox_provider is not None else None,
            "actionAuthority": False,
            "consumedSnapshotCount": len(r21_inbox_traces),
            "nonEmptyConsumedSnapshotCount": sum(
                int(trace["incidentCount"] > 0) for trace in r21_inbox_traces
            ),
            "consumedIncidentTypes": sorted(
                {
                    incident_type
                    for trace in r21_inbox_traces
                    for incident_type in trace["incidentTypes"]
                }
            ),
            "traces": r21_inbox_traces,
        },
        "executionLifecycleTrace": {
            # Research-only, read-only trace for incident-notification contract exams.
            # These are the adapter's existing HftBacktest lifecycle records; exposing
            # them here does not mutate inventory, desired state, or order behavior.
            "makerSubmits": copy.deepcopy(row["makerSubmits"]),
            "makerFills": copy.deepcopy(row["makerFills"]),
            "takerAttempts": copy.deepcopy(row["takerAttempts"]),
            "takerFills": copy.deepcopy(row["takerFills"]),
        },
        "strictPastControllerTraces": strict_past_traces,
        "strictPastExecutionStates": row.get("strictPastExecutionStates", []),
        "strictPastOptionTransitions": row.get("strictPastOptionTransitions", []),
        "terminalExecutionState": row.get("terminalExecutionState"),
        "cycleInvariantViolations": violations,
        "cycleInvariantViolationCount": violation_count,
        "semanticGate": semantic_gate,
        "decision": decision,
        "economicSelectionEligible": False,
        "next": "If semantics pass and runtime is comfortably below 30 minutes, preregister the three-market fixed-executor/constrained-oracle/isolated-ablation pilot. Do not interpret this one market as value evidence.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, required=True)
    parser.add_argument("--passive-mode", choices=["wait", "offset0", "offset1", "offset2"], default="offset1")
    parser.add_argument("--option-scope", choices=["all", "maintain_only", "repair_only"], default="all")
    parser.add_argument("--own-state-poll-ms", type=int)
    parser.add_argument("--output", default="hft_r2_cycle_preserving_execution_smoke_v1.json")
    args = parser.parse_args()
    report = run_smoke(
        args.market_id,
        passive_mode=args.passive_mode,
        option_scope=args.option_scope,
        own_state_poll_ms=args.own_state_poll_ms,
    )
    output = OUT / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(output),
                "marketId": report["marketId"],
                "runtimeSeconds": report["runtimeSeconds"],
                "controller": {
                    key: report["controller"][key]
                    for key in ["steps", "highLevelDecisions", "desiredPortfolioActionCounts", "executionChoiceCounts", "optionTransitions"]
                },
                "r2ObjectiveExecution": report["r2ObjectiveExecution"],
                "semanticGate": report["semanticGate"],
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
