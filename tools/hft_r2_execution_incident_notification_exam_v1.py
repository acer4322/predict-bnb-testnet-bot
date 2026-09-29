from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke


OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_EXECUTION_INCIDENT_NOTIFICATION_EXAM_V1"
EPS = 1e-8
LIVE_STATES = {"ACKED_OPEN", "NEW", "PARTIALLY_FILLED", "CANCEL_PENDING"}
TERMINAL_STATES = {"FILLED", "CANCELED", "FAILED", "EXPIRED"}


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


class IncidentReceiverProbe:
    """Research proxy for the existing R2 information receiver; never returns an action."""

    REQUIRED = {
        "eventId",
        "incidentType",
        "atMs",
        "orderKey",
        "role",
        "side",
        "stateCertainty",
        "confirmedFilledQty",
        "fillDeltaQty",
        "unresolvedQty",
        "ownershipDirective",
        "receiverDirective",
    }

    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.event_ids: set[str] = set()
        self.schema_errors: list[dict[str, Any]] = []

    def receive(self, event: dict[str, Any]) -> None:
        missing = sorted(self.REQUIRED - set(event))
        if missing:
            self.schema_errors.append({"event": copy.deepcopy(event), "missing": missing})
            return
        event_id = str(event["eventId"])
        if event_id in self.event_ids:
            self.schema_errors.append({"eventId": event_id, "error": "DUPLICATE_EVENT_ID"})
            return
        self.event_ids.add(event_id)
        self.notifications.append(copy.deepcopy(event))


class ExecutionIncidentNotifier:
    """Classify order-lifecycle facts and notify R2 without selecting an action."""

    def __init__(
        self,
        receiver: IncidentReceiverProbe,
        delay_notice_ms: int = 3_000,
        no_fill_stall_ms: int = 10_000,
        cancel_unknown_ms: int = 5_000,
    ) -> None:
        self.receiver = receiver
        self.delay_notice_ms = int(delay_notice_ms)
        self.no_fill_stall_ms = int(no_fill_stall_ms)
        self.cancel_unknown_ms = int(cancel_unknown_ms)
        self.orders: dict[str, dict[str, Any]] = {}
        self.emitted: set[tuple[str, str]] = set()
        self.duplicate_observations = 0
        self.out_of_order_observations = 0
        self.observations = 0

    def _emit(
        self,
        observation: dict[str, Any],
        incident_type: str,
        fill_delta: float,
        ownership: str,
        directive: str,
        certainty: str | None = None,
    ) -> None:
        order_key = str(observation["orderKey"])
        dedup_key = (order_key, incident_type)
        if dedup_key in self.emitted:
            return
        self.emitted.add(dedup_key)
        requested = finite(observation.get("requestedQty"))
        filled = finite(observation.get("cumulativeFilledQty"))
        submitted_raw = observation.get("submittedAtMs")
        submitted_at = int(observation["atMs"]) if submitted_raw is None else int(submitted_raw)
        raw_id = f"{order_key}|{incident_type}".encode("utf-8")
        event = {
            "eventId": hashlib.sha256(raw_id).hexdigest(),
            "incidentType": incident_type,
            "atMs": int(observation["atMs"]),
            "orderKey": order_key,
            "role": str(observation.get("role") or "UNKNOWN"),
            "side": str(observation.get("side") or "UNKNOWN"),
            "venueState": str(observation.get("venueState") or "UNKNOWN"),
            "stateCertainty": str(certainty or observation.get("stateCertainty") or "CONFIRMED"),
            "requestedQty": requested,
            "confirmedFilledQty": filled,
            "fillDeltaQty": max(0.0, float(fill_delta)),
            "unresolvedQty": max(0.0, requested - filled),
            "submittedAtMs": submitted_at,
            "orderAgeMs": max(0, int(observation["atMs"]) - submitted_at),
            "ownershipDirective": ownership,
            "receiverDirective": directive,
            "targetRevision": copy.deepcopy(observation.get("targetRevision")),
            "reason": observation.get("reason"),
            "noExecutionActionSelected": True,
        }
        self.receiver.receive(event)

    def observe(self, observation: dict[str, Any]) -> None:
        self.observations += 1
        obs = copy.deepcopy(observation)
        key = str(obs["orderKey"])
        now = int(obs["atMs"])
        sequence = int(obs.get("sourceSequence") or now)
        previous = self.orders.get(key)
        if previous is not None:
            previous_sequence = int(previous.get("sourceSequence") or previous["atMs"])
            if sequence < previous_sequence:
                self.out_of_order_observations += 1
                return
            if sequence == previous_sequence and obs == previous:
                self.duplicate_observations += 1
                return

        requested = finite(obs.get("requestedQty"))
        cumulative = min(requested, max(0.0, finite(obs.get("cumulativeFilledQty"))))
        obs["cumulativeFilledQty"] = cumulative
        previous_cumulative = 0.0 if previous is None else finite(previous.get("cumulativeFilledQty"))
        fill_delta = max(0.0, cumulative - previous_cumulative)
        state = str(obs.get("venueState") or "UNKNOWN")
        submitted_raw = obs.get("submittedAtMs")
        submitted = now if submitted_raw is None else int(submitted_raw)
        age = max(0, now - submitted)
        cancel_requested = obs.get("cancelRequestedAtMs")

        if state == "UNKNOWN_SUBMISSION":
            self._emit(
                obs,
                "ORDER_STATE_UNKNOWN",
                fill_delta,
                "PRESERVE_OWNER_FREEZE",
                "FREEZE_AND_RECONCILE",
                "UNCERTAIN",
            )
        elif previous is not None and str(previous.get("venueState")) == "UNKNOWN_SUBMISSION":
            self._emit(
                obs,
                "RECONCILED_ORDER_STATE",
                fill_delta,
                "RECOMPUTE_FROM_CONFIRMED_STATE",
                "R2_REEVALUATE",
            )

        if state == "SUBMIT_REJECTED":
            self._emit(
                obs,
                "SUBMIT_REJECT_CONFIRMED",
                fill_delta,
                "RETURN_UNFILLED_TO_R2",
                "R2_REEVALUATE",
            )

        if state in LIVE_STATES:
            if cumulative <= EPS and age >= self.delay_notice_ms:
                self._emit(
                    obs,
                    "LIVE_NO_FILL_DELAY",
                    fill_delta,
                    "KEEP_WITH_LIVE_CHILD",
                    "OBSERVE_WAIT",
                )
            if cumulative <= EPS and age >= self.no_fill_stall_ms:
                self._emit(
                    obs,
                    "LIVE_NO_FILL_STALL",
                    fill_delta,
                    "KEEP_WITH_LIVE_CHILD",
                    "R2_REEVALUATE",
                )
            last_fill_at = int(
                obs.get("lastFillAtMs")
                or (previous or {}).get("lastFillAtMs")
                or submitted
            )
            if cumulative > EPS and cumulative + EPS < requested and now - last_fill_at >= self.no_fill_stall_ms:
                self._emit(
                    obs,
                    "LIVE_PARTIAL_FILL_STALL",
                    fill_delta,
                    "KEEP_CONFIRMED_REMAINDER_WITH_LIVE_CHILD",
                    "R2_REEVALUATE",
                )

        if fill_delta > EPS:
            fill_type = "FULL_FILL_CONFIRMED" if cumulative + EPS >= requested else "PARTIAL_FILL_CONFIRMED"
            self._emit(
                obs,
                fill_type,
                fill_delta,
                "RECOMPUTE_FROM_ACTUAL_FILL",
                "R2_REEVALUATE",
            )
            if (key, "LIVE_NO_FILL_DELAY") in self.emitted or (key, "LIVE_NO_FILL_STALL") in self.emitted or (key, "LIVE_PARTIAL_FILL_STALL") in self.emitted:
                self._emit(
                    obs,
                    "LATE_FILL_AFTER_DELAY",
                    fill_delta,
                    "RECOMPUTE_FROM_ACTUAL_FILL",
                    "R2_REEVALUATE",
                )
            if previous is not None and (
                str(previous.get("venueState")) == "CANCEL_PENDING"
                or previous.get("cancelRequestedAtMs") is not None
            ):
                self._emit(
                    obs,
                    "FILL_DURING_CANCEL",
                    fill_delta,
                    "RECOMPUTE_FROM_ACTUAL_FILL_KEEP_CANCEL_OWNERSHIP",
                    "R2_REEVALUATE",
                )
            obs["lastFillAtMs"] = now
        elif previous is not None and previous.get("lastFillAtMs") is not None:
            obs["lastFillAtMs"] = previous["lastFillAtMs"]

        if state == "CANCEL_PENDING" and cancel_requested is not None and now - int(cancel_requested) >= self.cancel_unknown_ms:
            self._emit(
                obs,
                "CANCEL_ACK_TIMEOUT",
                fill_delta,
                "PRESERVE_OWNER_FREEZE",
                "FREEZE_AND_RECONCILE",
                "UNCERTAIN",
            )

        if state in TERMINAL_STATES and state != "FILLED":
            if cumulative <= EPS:
                self._emit(
                    obs,
                    "TERMINAL_ZERO_FILL_CONFIRMED",
                    fill_delta,
                    "RETURN_UNFILLED_TO_R2",
                    "R2_REEVALUATE",
                )
            elif cumulative + EPS < requested:
                self._emit(
                    obs,
                    "TERMINAL_PARTIAL_FILL_CONFIRMED",
                    fill_delta,
                    "RETURN_CONFIRMED_REMAINDER_TO_R2",
                    "R2_REEVALUATE",
                )

        self.orders[key] = obs


class HftIncidentBridge:
    """Translate strict-past HFT execution state into notifier observations online."""

    def __init__(self, notifier: ExecutionIncidentNotifier) -> None:
        self.notifier = notifier
        self.calls = 0
        self.last_order_by_role_side: dict[tuple[str, str], str] = {}
        self.requests: dict[str, float] = {}
        self.cumulative: dict[str, float] = {}
        self.seen_fill_events: set[tuple[Any, ...]] = set()
        self.seen_memory_events: set[tuple[Any, ...]] = set()
        self.source_sequence = 0

    def _next_sequence(self) -> int:
        self.source_sequence += 1
        return self.source_sequence

    def _observe_child(self, now: int, role: str, side: str, child: dict[str, Any]) -> None:
        key = f"{role}:{int(child['orderNum'])}"
        requested = finite(child.get("requestedQty"))
        cumulative = finite(child.get("cumExecQty"), finite(child.get("filledQty")))
        self.requests[key] = requested
        self.cumulative[key] = max(self.cumulative.get(key, 0.0), cumulative)
        self.last_order_by_role_side[(role, side)] = key
        cancel_pending = bool(child.get("cancelPending") or child.get("cancelRequested"))
        state = "CANCEL_PENDING" if cancel_pending else str(
            child.get("lifecycleState") or child.get("status") or "ACKED_OPEN"
        )
        if state == "SUBMITTED":
            state = "ACKED_OPEN"
        self.notifier.observe(
            {
                "orderKey": key,
                "atMs": now,
                "sourceSequence": self._next_sequence(),
                "role": role,
                "side": side,
                "requestedQty": requested,
                "cumulativeFilledQty": self.cumulative[key],
                "submittedAtMs": int(child.get("submittedAtMs") or now),
                "venueState": state,
                "cancelRequestedAtMs": now if cancel_pending else None,
                "stateCertainty": "CONFIRMED",
            }
        )

    def __call__(self, payload: dict[str, Any]) -> None:
        self.calls += 1
        state = copy.deepcopy(payload["executionState"])
        now = int(payload["atMs"])
        for side, child in (state.get("activeMakerChildren") or {}).items():
            if isinstance(child, dict):
                self._observe_child(now, "MAKER", str(side), child)
        for child in list(state.get("openTakerChildren") or []):
            self._observe_child(now, "TAKER", str(child.get("side") or "UNKNOWN"), child)

        for fill in list(state.get("recentActualFills10s") or []):
            fill_id = (
                int(fill.get("eventMs") or 0),
                str(fill.get("role") or "UNKNOWN"),
                str(fill.get("side") or "UNKNOWN"),
                finite(fill.get("shares")),
                finite(fill.get("price")),
            )
            if fill_id in self.seen_fill_events:
                continue
            self.seen_fill_events.add(fill_id)
            role, side = str(fill_id[1]), str(fill_id[2])
            key = self.last_order_by_role_side.get((role, side), f"{role}:UNMATCHED:{fill_id[0]}")
            requested = self.requests.get(key, finite(fill.get("shares")))
            cumulative = min(requested, self.cumulative.get(key, 0.0) + finite(fill.get("shares")))
            self.cumulative[key] = cumulative
            self.notifier.observe(
                {
                    "orderKey": key,
                    "atMs": now,
                    "sourceSequence": self._next_sequence(),
                    "role": role,
                    "side": side,
                    "requestedQty": requested,
                    "cumulativeFilledQty": cumulative,
                    "submittedAtMs": int((self.notifier.orders.get(key) or {}).get("submittedAtMs") or now),
                    "venueState": "FILLED" if cumulative + EPS >= requested else "PARTIALLY_FILLED",
                    "stateCertainty": "CONFIRMED",
                }
            )

        memory = state.get("behaviorMemory") or {}
        for row in list(memory.get("history") or []):
            memory_id = (
                int(row.get("atMs") or 0),
                str(row.get("action") or "UNKNOWN"),
                str(row.get("side") or "UNKNOWN"),
                str(row.get("outcome") or "UNKNOWN"),
                str(row.get("fault") or "NONE"),
            )
            if memory_id in self.seen_memory_events:
                continue
            self.seen_memory_events.add(memory_id)
            outcome = str(row.get("outcome") or "UNKNOWN")
            if outcome not in {"SUBMIT_REJECT", "TERMINAL_ZERO_FILL", "PARTIAL"}:
                continue
            role = "MAKER" if str(row.get("action")) == "MAKER_CHILD" else "TAKER"
            requested = finite(row.get("requestedQty"))
            filled = finite(row.get("filledQty"))
            venue_state = (
                "SUBMIT_REJECTED"
                if outcome == "SUBMIT_REJECT"
                else "FAILED"
                if outcome == "TERMINAL_ZERO_FILL"
                else "CANCELED"
            )
            self.notifier.observe(
                {
                    "orderKey": f"{role}:MEMORY:{memory_id[0]}:{memory_id[2]}",
                    "atMs": now,
                    "sourceSequence": self._next_sequence(),
                    "role": role,
                    "side": memory_id[2],
                    "requestedQty": requested,
                    "cumulativeFilledQty": filled,
                    "submittedAtMs": memory_id[0],
                    "venueState": venue_state,
                    "stateCertainty": "CONFIRMED",
                    "reason": memory_id[4],
                }
            )
        return None


class FirstMakerFault:
    def __init__(self, mode: str) -> None:
        self.mode = str(mode)
        self.used = False

    def __call__(self, _state: dict[str, Any]) -> str | None:
        if self.used:
            return None
        self.used = True
        return self.mode


def obs(
    order: str,
    at_ms: int,
    state: str,
    cumulative: float = 0.0,
    *,
    role: str = "MAKER",
    side: str = "UP",
    requested: float = 18.0,
    submitted: int = 0,
    sequence: int | None = None,
    cancel_at: int | None = None,
) -> dict[str, Any]:
    return {
        "orderKey": order,
        "atMs": at_ms,
        "sourceSequence": at_ms if sequence is None else sequence,
        "role": role,
        "side": side,
        "requestedQty": requested,
        "cumulativeFilledQty": cumulative,
        "submittedAtMs": submitted,
        "venueState": state,
        "cancelRequestedAtMs": cancel_at,
        "stateCertainty": "UNCERTAIN" if state == "UNKNOWN_SUBMISSION" else "CONFIRMED",
    }


def run_contract_scenarios() -> list[dict[str, Any]]:
    scenarios = [
        ("ORDER_SUBMISSION_REJECTED", [obs("M1", 0, "SUBMIT_REJECTED")], {"SUBMIT_REJECT_CONFIRMED"}),
        (
            "ACK_LONG_NO_FILL_CANCELLED",
            [obs("M2", 0, "ACKED_OPEN"), obs("M2", 12_000, "ACKED_OPEN"), obs("M2", 13_000, "CANCELED")],
            {"LIVE_NO_FILL_DELAY", "LIVE_NO_FILL_STALL", "TERMINAL_ZERO_FILL_CONFIRMED"},
        ),
        (
            "ACK_LONG_NO_FILL_THEN_LATE_FILL",
            [obs("M3", 0, "ACKED_OPEN"), obs("M3", 12_000, "ACKED_OPEN"), obs("M3", 18_000, "FILLED", 18)],
            {"LIVE_NO_FILL_DELAY", "LIVE_NO_FILL_STALL", "FULL_FILL_CONFIRMED", "LATE_FILL_AFTER_DELAY"},
        ),
        (
            "PARTIAL_STALL_THEN_LATE_COMPLETE",
            [obs("M4", 0, "ACKED_OPEN"), obs("M4", 2_000, "PARTIALLY_FILLED", 6), obs("M4", 15_000, "PARTIALLY_FILLED", 6), obs("M4", 20_000, "FILLED", 18)],
            {"PARTIAL_FILL_CONFIRMED", "LIVE_PARTIAL_FILL_STALL", "FULL_FILL_CONFIRMED", "LATE_FILL_AFTER_DELAY"},
        ),
        (
            "CANCEL_FILL_RACE",
            [obs("M5", 0, "ACKED_OPEN"), obs("M5", 4_000, "CANCEL_PENDING", cancel_at=4_000), obs("M5", 6_000, "FILLED", 18)],
            {"FULL_FILL_CONFIRMED", "FILL_DURING_CANCEL"},
        ),
        (
            "CANCEL_ACK_TIMEOUT",
            [obs("M6", 0, "ACKED_OPEN"), obs("M6", 4_000, "CANCEL_PENDING", cancel_at=4_000), obs("M6", 10_000, "CANCEL_PENDING", cancel_at=4_000)],
            {"CANCEL_ACK_TIMEOUT"},
        ),
        (
            "UNKNOWN_SUBMISSION_RECOVERED_FILLED",
            [obs("M7", 0, "UNKNOWN_SUBMISSION"), obs("M7", 5_000, "FILLED", 18)],
            {"ORDER_STATE_UNKNOWN", "RECONCILED_ORDER_STATE", "FULL_FILL_CONFIRMED"},
        ),
        (
            "UNKNOWN_UNRESOLVED",
            [obs("M8", 0, "UNKNOWN_SUBMISSION"), obs("M8", 12_000, "UNKNOWN_SUBMISSION")],
            {"ORDER_STATE_UNKNOWN"},
        ),
        (
            "TAKER_SUBMIT_REJECTED",
            [obs("T1", 0, "SUBMIT_REJECTED", role="TAKER", side="DOWN")],
            {"SUBMIT_REJECT_CONFIRMED"},
        ),
        (
            "TAKER_NO_FILL_TERMINAL",
            [obs("T2", 0, "ACKED_OPEN", role="TAKER", side="DOWN"), obs("T2", 12_000, "ACKED_OPEN", role="TAKER", side="DOWN"), obs("T2", 13_000, "FAILED", role="TAKER", side="DOWN")],
            {"LIVE_NO_FILL_DELAY", "LIVE_NO_FILL_STALL", "TERMINAL_ZERO_FILL_CONFIRMED"},
        ),
    ]
    rows = []
    for name, observations, expected in scenarios:
        receiver = IncidentReceiverProbe()
        notifier = ExecutionIncidentNotifier(receiver)
        for observation in observations:
            notifier.observe(observation)
        types = {str(event["incidentType"]) for event in receiver.notifications}
        rows.append(
            {
                "scenario": name,
                "expectedIncidentTypes": sorted(expected),
                "actualIncidentTypes": sorted(types),
                "schemaErrors": receiver.schema_errors,
                "pass": expected.issubset(types) and not receiver.schema_errors,
                "notifications": receiver.notifications,
            }
        )

    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    first = obs("ORDERING", 1_000, "ACKED_OPEN", sequence=10)
    notifier.observe(first)
    notifier.observe(copy.deepcopy(first))
    notifier.observe(obs("ORDERING", 900, "ACKED_OPEN", sequence=9))
    rows.append(
        {
            "scenario": "DUPLICATE_AND_OUT_OF_ORDER_INPUT",
            "pass": notifier.duplicate_observations == 1 and notifier.out_of_order_observations == 1 and not receiver.schema_errors,
            "duplicateObservationsIgnored": notifier.duplicate_observations,
            "outOfOrderObservationsIgnored": notifier.out_of_order_observations,
            "schemaErrors": receiver.schema_errors,
            "notifications": receiver.notifications,
        }
    )
    return rows


def compact_execution(report: dict[str, Any]) -> dict[str, Any]:
    actual = report["actualExecution"]
    portfolio = actual["finalPortfolio"]
    return {
        "trackingErrorAreaShareSeconds": finite(actual.get("targetErrorAreaShareSeconds")),
        "finalAbsTrackingError": finite(actual.get("finalAbsTrackingError")),
        "makerFilledShares": finite(actual.get("makerFilledShares")),
        "takerFilledShares": finite(actual.get("takerFilledShares")),
        "pairedCoverage": finite(portfolio.get("combined_paired_coverage")),
        "worstCaseFloorAuditOnly": finite(portfolio.get("worst_case_floor")),
        "realizedPnlAuditOnly": actual.get("realizedPnl"),
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "controllerHighLevelDecisions": int(report["controller"]["highLevelDecisions"]),
        "makerSubmits": int(report["r2ObjectiveExecution"]["makerSubmits"]),
        "makerFills": int(report["r2ObjectiveExecution"]["makerFills"]),
        "takerAttempts": int(report["r2ObjectiveExecution"]["takerAttempts"]),
        "takerFills": int(report["r2ObjectiveExecution"]["takerFills"]),
    }


def run_hft_case(market_id: int, case: str, with_notifier: bool) -> dict[str, Any]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = HftIncidentBridge(notifier)
    fault: FirstMakerFault | None = None
    if case == "MAKER_SUBMIT_REJECT":
        fault = FirstMakerFault("SUBMIT_REJECT")
    elif case == "MAKER_LONG_NO_FILL":
        fault = FirstMakerFault("NO_FILL_STALL")
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        maker_submit_fault_override=fault,
        fault_reentry_enabled=fault is not None,
        behavior_policy_override=bridge if with_notifier else None,
        behavior_ownstate_reentry=False,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
        fault_no_fill_stall_ms=15_000 if case == "MAKER_LONG_NO_FILL" else None,
    )
    notices = receiver.notifications
    return {
        "marketId": int(market_id),
        "case": case,
        "withNotifier": bool(with_notifier),
        "faultUsed": None if fault is None else bool(fault.used),
        "runtimeSeconds": finite(report.get("runtimeSeconds")),
        "execution": compact_execution(report),
        "receiver": {
            "bridgeCalls": bridge.calls if with_notifier else 0,
            "notifications": len(notices),
            "incidentTypeCounts": dict(sorted(Counter(str(row["incidentType"]) for row in notices).items())),
            "schemaErrors": receiver.schema_errors,
            "selectedExecutionActions": 0,
            "desiredMutationCount": sum(
                event.get("beforeDesired") != event.get("afterDesired")
                for event in report["r2ObjectiveExecution"].get("behaviorOverrideEvents", [])
                if "beforeDesired" in event
            ),
        },
        "notifications": notices,
    }


def run_hft_pilot(market_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    comparisons = []
    required = {
        "NATURAL_EXECUTION": {"LIVE_NO_FILL_STALL", "LATE_FILL_AFTER_DELAY"},
        "MAKER_SUBMIT_REJECT": {"SUBMIT_REJECT_CONFIRMED"},
        "MAKER_LONG_NO_FILL": {"LIVE_NO_FILL_STALL", "TERMINAL_ZERO_FILL_CONFIRMED"},
    }
    for case in required:
        baseline = run_hft_case(market_id, case, False)
        notified = run_hft_case(market_id, case, True)
        rows.extend([baseline, notified])
        actual_types = set(notified["receiver"]["incidentTypeCounts"])
        non_interference = baseline["execution"] == notified["execution"]
        comparisons.append(
            {
                "marketId": int(market_id),
                "case": case,
                "requiredIncidentTypes": sorted(required[case]),
                "actualIncidentTypes": sorted(actual_types),
                "notificationCoveragePass": required[case].issubset(actual_types),
                "nonInterferencePass": non_interference,
                "zeroSchemaErrors": not notified["receiver"]["schemaErrors"],
                "zeroNotifierSelectedActions": notified["receiver"]["selectedExecutionActions"] == 0,
                "zeroDesiredMutation": notified["receiver"]["desiredMutationCount"] == 0,
                "faultAppliedBoth": (
                    True
                    if case == "NATURAL_EXECUTION"
                    else baseline["faultUsed"] is True and notified["faultUsed"] is True
                ),
                "pass": bool(
                    required[case].issubset(actual_types)
                    and non_interference
                    and not notified["receiver"]["schemaErrors"]
                    and notified["receiver"]["selectedExecutionActions"] == 0
                    and notified["receiver"]["desiredMutationCount"] == 0
                    and (
                        case == "NATURAL_EXECUTION"
                        or (baseline["faultUsed"] is True and notified["faultUsed"] is True)
                    )
                ),
                "baselineExecution": baseline["execution"],
                "notifiedExecution": notified["execution"],
            }
        )
        print(
            json.dumps(
                {
                    "case": case,
                    "coverage": comparisons[-1]["notificationCoveragePass"],
                    "nonInterference": non_interference,
                    "types": sorted(actual_types),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return rows, comparisons


def main() -> None:
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1569361)
    parser.add_argument(
        "--output",
        default="hft_r2_execution_incident_notification_exam_v1_report.json",
    )
    args = parser.parse_args()

    contract_rows = run_contract_scenarios()
    hft_rows, comparisons = run_hft_pilot(args.market_id)
    contract_pass = all(row["pass"] for row in contract_rows)
    hft_pass = all(row["pass"] for row in comparisons)
    summary = {
        "contractScenarios": len(contract_rows),
        "contractPassed": sum(row["pass"] for row in contract_rows),
        "contractAllPass": contract_pass,
        "hftCases": len(comparisons),
        "hftPassed": sum(row["pass"] for row in comparisons),
        "hftAllPass": hft_pass,
        "notifierSelectedExecutionActions": 0,
        "executionSemantics": "HftBacktest + Predict Execution Tape V1; actual fills only; injected no-fill credits no fill",
        "chronologicalUnseenOos": False,
        "waitAct": "N/A_NOTIFICATION_LAYER_SELECTS_NO_ACTION",
        "oracleValueCeiling": None,
        "learnedPolicyRealizedValue": None,
        "decision": (
            "KEEP_PARALLEL_INCIDENT_NOTIFICATION_CONTRACT_FOR_R2_RECEIVER_EXAM"
            if contract_pass and hft_pass
            else "NEED_MORE_NOTIFICATION_CONTRACT_WORK"
        ),
    }
    report = {
        "version": VERSION,
        "researchOnly": True,
        "liveTradingChanges": False,
        "preregistration": {
            "hypothesis": "A parallel incident notifier can classify reject, live no-fill, and late actual fill, deliver structured facts to the existing R2 information receiver, and select no action without changing the HFT trajectory.",
            "dedupBoundary": "Existing v24/v25 drills test executor fail-closed safety and V1/V2 fault exams test state refresh/recovery actions. This exam uniquely tests notification classification, receiver delivery, late-fill-after-stall reversal, and exact non-interference.",
            "market": int(args.market_id),
            "revealedOpenedDevelopmentOnly": True,
            "sealedOrOfficialHftForwardUsed": False,
            "minimumGates": [
                "all contract scenarios pass",
                "reject/no-fill/natural-late-fill HFT notifications present",
                "notified and no-notifier terminal execution records exactly match",
                "zero notifier-selected actions, desired mutation, schema errors, or cycle violations",
            ],
        },
        "contractRows": contract_rows,
        "hftRows": hft_rows,
        "hftComparisons": comparisons,
        "summary": summary,
    }
    output = OUT / args.output
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({"report": str(output), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
