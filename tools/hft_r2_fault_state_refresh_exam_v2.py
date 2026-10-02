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
from tools.hft_r2_fault_state_refresh_exam_v1 import SequenceFault, run_case

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R2_FAULT_STATE_REFRESH_EXAM_V2"
CASES: dict[str, list[str]] = {
    "SINGLE_REJECT": ["SUBMIT_REJECT"],
    "DOUBLE_REJECT": ["SUBMIT_REJECT", "SUBMIT_REJECT"],
    "TRIPLE_REJECT": ["SUBMIT_REJECT", "SUBMIT_REJECT", "SUBMIT_REJECT"],
    "SINGLE_NO_FILL": ["NO_FILL_STALL"],
    "DOUBLE_NO_FILL": ["NO_FILL_STALL", "NO_FILL_STALL"],
    "MIXED_REJECT_NO_FILL": ["SUBMIT_REJECT", "NO_FILL_STALL"],
}
FAILURE_OUTCOMES = {"SUBMIT_REJECT", "TERMINAL_ZERO_FILL", "PARTIAL", "NO_FILL_STALL"}
ALLOWED_TAKER_KINDS = {"FROZEN_R2", "PAIR_COMPLETION_REPLACE"}
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def grade(row: dict[str, Any]) -> str:
    """Backward-compatible V2 structural grade over faults that actually occurred."""
    if int(row["faultInjectionCount"]) == 0:
        return "NOT_APPLICABLE"
    structural = bool(
        row["stateRefreshedAfterFault"]
        and row["postFaultStateHasActualInventory"]
        and row["postFaultStateHasDesiredPortfolio"]
        and row["postFaultStateHasFailureMemory"]
        and row["cycleInvariantViolationCount"] == 0
    )
    reject_count = sum(mode == "SUBMIT_REJECT" for mode in row["faultsInjected"])
    no_fill_count = sum(mode == "NO_FILL_STALL" for mode in row["faultsInjected"])
    memory_ok = bool(
        row["maxConsecutiveReject"] >= reject_count
        and row["maxConsecutiveNoFill"] >= no_fill_count
    )
    return "PASS" if structural and memory_ok else "FAIL"


class CandidateBehavior:
    """One bounded active, one WAIT per new fault, then authority returns to Frozen R2."""

    def __init__(self, post_fault_wait_forever: bool) -> None:
        self.post_fault_wait_forever = bool(post_fault_wait_forever)
        self.initial_active_proposed = False
        self.handled_failure_events = 0
        self.calls: list[dict[str, Any]] = []
        self.proposals: list[dict[str, Any]] = []

    @staticmethod
    def failure_events(memory: dict[str, Any]) -> int:
        return sum(
            str(row.get("outcome") or "") in FAILURE_OUTCOMES
            for row in list(memory.get("history") or [])
        )

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        state = copy.deepcopy(payload["executionState"])
        memory = copy.deepcopy(state.get("behaviorMemory") or {})
        failures = self.failure_events(memory)
        record = {
            "atMs": int(payload["atMs"]),
            "trigger": str(payload.get("trigger") or ""),
            "trackingError": finite(state.get("trackingError")),
            "actualPortfolio": copy.deepcopy(state.get("actualPortfolio")),
            "desiredPortfolio": copy.deepcopy(payload.get("desiredPortfolio")),
            "failureEvents": failures,
            "memory": memory,
        }
        proposal: dict[str, Any] | None = None
        if not self.initial_active_proposed:
            self.initial_active_proposed = True
            proposal = {"executionMode": "ACTIVE_REPAIR"}
        elif failures > self.handled_failure_events:
            self.handled_failure_events = failures
            proposal = {"executionMode": "WAIT"}
        elif self.post_fault_wait_forever and failures > 0:
            proposal = {"executionMode": "WAIT"}
        self.calls.append(record)
        if proposal is not None:
            self.proposals.append({**record, "proposal": copy.deepcopy(proposal)})
        return proposal


class NativePostFaultWaitBehavior:
    """Do not touch native R2 before a real fault; freeze behavior only after it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.proposals: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        state = copy.deepcopy(payload["executionState"])
        memory = copy.deepcopy(state.get("behaviorMemory") or {})
        failures = CandidateBehavior.failure_events(memory)
        record = {
            "atMs": int(payload["atMs"]),
            "trigger": str(payload.get("trigger") or ""),
            "trackingError": finite(state.get("trackingError")),
            "actualPortfolio": copy.deepcopy(state.get("actualPortfolio")),
            "desiredPortfolio": copy.deepcopy(payload.get("desiredPortfolio")),
            "failureEvents": failures,
            "memory": memory,
        }
        self.calls.append(record)
        if failures <= 0:
            return None
        proposal = {"executionMode": "WAIT", "freezeNewEconomicIntents": True}
        self.proposals.append({**record, "proposal": copy.deepcopy(proposal)})
        return proposal


class TargetPreservingPostFaultNoRepairBehavior(NativePostFaultWaitBehavior):
    """Keep Frozen R2 target formation visible, but create no new execution children."""

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        proposal = super().__call__(payload)
        if proposal is None:
            return None
        proposal.pop("freezeNewEconomicIntents", None)
        proposal.update(
            {
                "freezeFrozenR2TakerIntents": True,
                "freezeMakerExecutionChildren": True,
            }
        )
        self.proposals[-1]["proposal"] = copy.deepcopy(proposal)
        return proposal


class BoundedActiveThenPassiveBehavior(NativePostFaultWaitBehavior):
    """After one observed fault, allow one lifecycle repair then keep passive recovery only."""

    def __init__(self) -> None:
        super().__init__()
        self.active_repair_submitted = False

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        state = copy.deepcopy(payload["executionState"])
        memory = copy.deepcopy(state.get("behaviorMemory") or {})
        failures = CandidateBehavior.failure_events(memory)
        record = {
            "atMs": int(payload["atMs"]),
            "trigger": str(payload.get("trigger") or ""),
            "trackingError": finite(state.get("trackingError")),
            "actualPortfolio": copy.deepcopy(state.get("actualPortfolio")),
            "desiredPortfolio": copy.deepcopy(payload.get("desiredPortfolio")),
            "failureEvents": failures,
            "memory": memory,
        }
        self.calls.append(record)
        if failures <= 0:
            return None
        history = list(memory.get("history") or [])
        self.active_repair_submitted = self.active_repair_submitted or any(
            str(row.get("action") or "") == "PAIR_COMPLETION_REPLACE"
            and str(row.get("outcome") or "") in {
                "SUBMITTED",
                "ACKED_OPEN",
                "PARTIAL",
                "FILLED",
                "TERMINAL_ZERO_FILL",
            }
            for row in history
        )
        mode = "KEEP_PASSIVE" if self.active_repair_submitted else "ACTIVE_REPAIR"
        proposal = {
            "executionMode": mode,
            "freezeFrozenR2TakerIntents": True,
            "freezeMakerExecutionChildren": False,
        }
        self.proposals.append({**record, "proposal": copy.deepcopy(proposal)})
        return proposal


class PassiveOnlyAfterFaultBehavior(NativePostFaultWaitBehavior):
    """After a confirmed fault, suppress direct Takers and leave passive R2 repair active."""

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        proposal = super().__call__(payload)
        if proposal is None:
            return None
        proposal.pop("freezeNewEconomicIntents", None)
        proposal.update(
            {
                "executionMode": "KEEP_PASSIVE",
                "freezeFrozenR2TakerIntents": True,
                "freezeMakerExecutionChildren": False,
            }
        )
        self.proposals[-1]["proposal"] = copy.deepcopy(proposal)
        return proposal


class SelectiveSequenceFault(SequenceFault):
    """Inject only into the locked responsibility class; never changes fill semantics."""

    def __init__(self, modes: list[str], fault_target: str) -> None:
        super().__init__(modes)
        self.fault_target = str(fault_target)
        self.applied_calls: list[dict[str, Any]] = []

    def eligible(self, state: dict[str, Any]) -> bool:
        kind = str(state.get("kind") or "")
        if kind not in ALLOWED_TAKER_KINDS:
            return False
        if self.fault_target == "any":
            return True
        if kind == "PAIR_COMPLETION_REPLACE":
            return True
        context = state.get("intentContext") or {}
        return bool(
            str(context.get("predEffect") or "") == "REPAIR_EFFECT"
            and context.get("intentSideAlignedWithTrackingRepair") is True
        )

    def __call__(self, state: dict[str, Any]) -> str | None:
        row = copy.deepcopy(state)
        self.calls.append(row)
        if not self.eligible(row) or len(self.used) >= len(self.modes):
            return None
        mode = self.modes[len(self.used)]
        self.used.append(mode)
        self.applied_calls.append(row)
        return mode


def applied_fault_calls(injector: SequenceFault) -> list[dict[str, Any]]:
    if hasattr(injector, "applied_calls"):
        return list(getattr(injector, "applied_calls"))
    eligible = [
        row for row in injector.calls if str(row.get("kind") or "") in ALLOWED_TAKER_KINDS
    ]
    return eligible[: len(injector.used)]


def first_fault_hash(injector: SequenceFault) -> str | None:
    calls = applied_fault_calls(injector)
    if not calls:
        return None
    row = calls[0]
    payload = {
        "atMs": int(row["atMs"]),
        "side": str(row["side"]),
        "qty": finite(row["qty"]),
        "kind": str(row["kind"]),
        "mode": injector.used[0],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def proposal_counts(
    policy: CandidateBehavior | NativePostFaultWaitBehavior | None,
) -> dict[str, int]:
    if policy is None:
        return {"WAIT": 0, "ACTIVE_REPAIR": 0, "KEEP_PASSIVE": 0}
    counts = Counter(
        str(row["proposal"].get("executionMode") or "NONE") for row in policy.proposals
    )
    return {
        "WAIT": counts.get("WAIT", 0),
        "ACTIVE_REPAIR": counts.get("ACTIVE_REPAIR", 0),
        "KEEP_PASSIVE": counts.get("KEEP_PASSIVE", 0),
    }


def controller_wait_act(report: dict[str, Any], first_fault_ms: int | None) -> dict[str, Any]:
    decisions = []
    for decision in list((report.get("controller") or {}).get("decisions") or []):
        at_ms = int(decision.get("decisionMs") or -1)
        if first_fault_ms is not None and at_ms < first_fault_ms:
            continue
        choice = str(decision.get("executionChoice") or "NONE")
        if choice == "NONE":
            continue
        decisions.append({"atMs": at_ms, "executionChoice": choice})
    by_choice = Counter(row["executionChoice"] for row in decisions)
    wait = by_choice.get("WAIT", 0)
    act = sum(count for choice, count in by_choice.items() if choice != "WAIT")
    return {
        "scope": "POST_FAULT_FROZEN_R2_HIGH_LEVEL_DECISIONS"
        if first_fault_ms is not None
        else "FULL_MARKET_FROZEN_R2_HIGH_LEVEL_DECISIONS",
        "WAIT": wait,
        "ACT": act,
        "actRate": act / (wait + act) if wait + act else 0.0,
        "executionChoiceCounts": dict(sorted(by_choice.items())),
        "definition": "WAIT is Frozen R2 executionChoice=WAIT; ACT is every non-WAIT Frozen R2 executionChoice.",
    }


def recovery_landmark(states: list[dict[str, Any]], first_fault_ms: int | None) -> dict[str, Any]:
    post = [row for row in states if first_fault_ms is not None and int(row["atMs"]) >= first_fault_ms]
    if not post:
        return {
            "postFaultStates": 0,
            "firstAbsTrackingError": None,
            "minimumAbsTrackingError": None,
            "halvedAtMs": None,
            "timeToHalveMs": None,
            "statesToHalve": None,
        }
    first_abs = abs(finite(post[0].get("trackingError")))
    threshold = max(18.0, 0.5 * first_abs)
    halved_index = next(
        (index for index, row in enumerate(post) if abs(finite(row.get("trackingError"))) <= threshold),
        None,
    )
    halved_at = None if halved_index is None else int(post[halved_index]["atMs"])
    return {
        "postFaultStates": len(post),
        "firstAbsTrackingError": first_abs,
        "minimumAbsTrackingError": min(abs(finite(row.get("trackingError"))) for row in post),
        "halveThreshold": threshold,
        "halvedAtMs": halved_at,
        "timeToHalveMs": None if halved_at is None else halved_at - int(first_fault_ms),
        "statesToHalve": None if halved_index is None else halved_index + 1,
    }


def run_continuation(market_id: int, case_name: str, modes: list[str], role: str) -> dict[str, Any]:
    injector = SequenceFault(modes)
    policy = CandidateBehavior(post_fault_wait_forever=role == "POST_FAULT_WAIT_BASELINE")
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        taker_submit_fault_override=injector,
        fault_reentry_enabled=True,
        behavior_policy_override=policy,
        behavior_ownstate_reentry=True,
        allowed_executor_taker_kinds=ALLOWED_TAKER_KINDS,
        trace_execution_states=True,
    )
    fault_calls = applied_fault_calls(injector)
    first_fault_ms = int(fault_calls[0]["atMs"]) if fault_calls else None
    actual = report["actualExecution"]
    portfolio = actual["finalPortfolio"]
    desired_mutations = sum(
        event.get("beforeDesired") != event.get("afterDesired")
        for event in report["r2ObjectiveExecution"].get("behaviorOverrideEvents", [])
    )
    return {
        "marketId": int(market_id),
        "case": case_name,
        "role": role,
        "faultPlan": list(modes),
        "faultsInjected": list(injector.used),
        "faultInjectionCount": len(injector.used),
        "faultDepthPass": len(injector.used) == len(modes),
        "firstFaultStateHash": first_fault_hash(injector),
        "firstFaultAtMs": first_fault_ms,
        "policyCalls": len(policy.calls),
        "behaviorProposalCounts": proposal_counts(policy),
        "behaviorDesiredMutationCount": desired_mutations,
        "recoveryLandmark": recovery_landmark(
            list(report.get("strictPastExecutionStates") or []), first_fault_ms
        ),
        "terminal": {
            "trackingErrorAreaShareSeconds": finite(actual.get("targetErrorAreaShareSeconds")),
            "finalAbsTrackingError": finite(actual.get("finalAbsTrackingError")),
            "pairedCoverage": finite(portfolio.get("combined_paired_coverage")),
            "combinedAbsNet": finite(actual.get("combinedFinalAbsNet")),
            "worstCaseFloorAuditOnly": finite(portfolio.get("worst_case_floor")),
            "realizedPnlAuditOnly": actual.get("realizedPnl"),
            "makerFilledShares": finite(actual.get("makerFilledShares")),
            "takerFilledShares": finite(actual.get("takerFilledShares")),
        },
        "lifecycle": {
            "actionCounts": copy.deepcopy(report["lifecycleAudit"].get("actionCounts")),
            "unresolvedTakerReturns": int(
                report["lifecycleAudit"].get("unresolvedTakerReturns") or 0
            ),
            "remainderOwnershipAtEnd": copy.deepcopy(
                report["lifecycleAudit"].get("remainderOwnershipAtEnd")
            ),
            "ownershipEventCounts": copy.deepcopy(
                report["lifecycleAudit"].get("ownershipEventCounts")
            ),
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
    }


def run_native_strategy_continuation(
    market_id: int,
    case_name: str,
    modes: list[str],
    role: str,
    fault_target: str = "any",
) -> dict[str, Any]:
    injector = SelectiveSequenceFault(modes, fault_target)
    if role == "POST_FAULT_WAIT_BASELINE":
        policy: NativePostFaultWaitBehavior | None = NativePostFaultWaitBehavior()
    elif role == "TARGET_PRESERVING_POST_FAULT_NO_REPAIR":
        policy = TargetPreservingPostFaultNoRepairBehavior()
    elif role == "BOUNDED_ACTIVE_THEN_PASSIVE":
        policy = BoundedActiveThenPassiveBehavior()
    elif role == "PASSIVE_ONLY_AFTER_FAULT":
        policy = PassiveOnlyAfterFaultBehavior()
    else:
        policy = None
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        taker_submit_fault_override=injector,
        fault_reentry_enabled=True,
        behavior_policy_override=policy,
        behavior_ownstate_reentry=policy is not None,
        fault_containment_freeze_before_redecision=policy is not None,
        allowed_executor_taker_kinds=ALLOWED_TAKER_KINDS,
        trace_execution_states=True,
    )
    fault_calls = applied_fault_calls(injector)
    first_fault_ms = int(fault_calls[0]["atMs"]) if fault_calls else None
    actual = report["actualExecution"]
    portfolio = actual["finalPortfolio"]
    desired_mutations = sum(
        event.get("beforeDesired") != event.get("afterDesired")
        for event in report["r2ObjectiveExecution"].get("behaviorOverrideEvents", [])
    )
    eligible_calls = [
        row for row in injector.calls if str(row.get("kind") or "") in ALLOWED_TAKER_KINDS
    ]
    taker_call_times = Counter(int(row["atMs"]) for row in eligible_calls)
    strictly_post_fault_takers = sum(
        count
        for at_ms, count in taker_call_times.items()
        if first_fault_ms is not None and at_ms > first_fault_ms
    )
    post_fault_maker_fills = [
        row
        for row in list(report.get("actualFillFeedbackCycles") or [])
        if first_fault_ms is not None
        and str(row.get("role") or "") == "MAKER"
        and int(row.get("fillObservedAtMs") or -1) > first_fault_ms
    ]
    return {
        "marketId": int(market_id),
        "case": case_name,
        "role": role,
        "faultTarget": fault_target,
        "faultPlan": list(modes),
        "faultsInjected": list(injector.used),
        "faultInjectionCount": len(injector.used),
        "faultDepthPass": len(injector.used) == len(modes),
        "firstFaultStateHash": first_fault_hash(injector),
        "firstFaultAtMs": first_fault_ms,
        "policyCalls": 0 if policy is None else len(policy.calls),
        "behaviorProposalCounts": proposal_counts(policy),
        "behaviorDesiredMutationCount": desired_mutations,
        "nativeControllerWaitAct": controller_wait_act(report, first_fault_ms),
        "nativeTakerAttempts": {
            "total": len(eligible_calls),
            "afterFaultedAttempt": strictly_post_fault_takers
            if first_fault_ms is not None
            else 0,
            "sameControllerTimestampSiblings": max(
                0, taker_call_times.get(first_fault_ms, 0) - 1
            )
            if first_fault_ms is not None
            else 0,
            "attemptCountsByAtMs": {
                str(at_ms): count for at_ms, count in sorted(taker_call_times.items())
            },
            "attempts": [
                {
                    "atMs": int(row["atMs"]),
                    "kind": str(row.get("kind") or ""),
                    "side": str(row.get("side") or ""),
                    "qty": finite(row.get("qty")),
                    "intentContext": copy.deepcopy(row.get("intentContext")),
                }
                for row in eligible_calls
            ],
            "postFaultDefinition": "Strictly atMs > firstFaultAtMs; same-timestamp sibling intents are not autonomous reactions to observed failure.",
        },
        "postFaultConfirmedMakerFills": {
            "count": len(post_fault_maker_fills),
            "shares": sum(finite(row.get("shares")) for row in post_fault_maker_fills),
            "sideCounts": dict(
                sorted(Counter(str(row.get("side") or "") for row in post_fault_maker_fills).items())
            ),
            "definition": "Confirmed HftBacktest MAKER fills with fillObservedAtMs > firstFaultAtMs.",
        },
        "recoveryLandmark": recovery_landmark(
            list(report.get("strictPastExecutionStates") or []), first_fault_ms
        ),
        "terminal": {
            "trackingErrorAreaShareSeconds": finite(actual.get("targetErrorAreaShareSeconds")),
            "finalAbsTrackingError": finite(actual.get("finalAbsTrackingError")),
            "pairedCoverage": finite(portfolio.get("combined_paired_coverage")),
            "combinedAbsNet": finite(actual.get("combinedFinalAbsNet")),
            "worstCaseFloorAuditOnly": finite(portfolio.get("worst_case_floor")),
            "realizedPnlAuditOnly": actual.get("realizedPnl"),
            "makerFilledShares": finite(actual.get("makerFilledShares")),
            "takerFilledShares": finite(actual.get("takerFilledShares")),
        },
        "lifecycle": {
            "actionCounts": copy.deepcopy(report["lifecycleAudit"].get("actionCounts")),
            "unresolvedTakerReturns": int(
                report["lifecycleAudit"].get("unresolvedTakerReturns") or 0
            ),
            "remainderOwnershipAtEnd": copy.deepcopy(
                report["lifecycleAudit"].get("remainderOwnershipAtEnd")
            ),
            "ownershipEventCounts": copy.deepcopy(
                report["lifecycleAudit"].get("ownershipEventCounts")
            ),
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
    }


def metric_reduction(baseline: float, candidate: float) -> float:
    return 0.0 if baseline <= EPS else (baseline - candidate) / baseline


def compare(candidate: dict[str, Any], baseline: dict[str, Any], minimum: float) -> dict[str, Any]:
    ct, bt = candidate["terminal"], baseline["terminal"]
    area_reduction = metric_reduction(
        bt["trackingErrorAreaShareSeconds"], ct["trackingErrorAreaShareSeconds"]
    )
    residual_reduction = metric_reduction(bt["finalAbsTrackingError"], ct["finalAbsTrackingError"])
    matched_start = bool(
        candidate["firstFaultStateHash"] is not None
        and candidate["firstFaultStateHash"] == baseline["firstFaultStateHash"]
    )
    full_depth = bool(candidate["faultDepthPass"] and baseline["faultDepthPass"])
    safe = bool(
        candidate["cycleInvariantViolationCount"] == 0
        and baseline["cycleInvariantViolationCount"] == 0
        and candidate["behaviorDesiredMutationCount"] == 0
        and baseline["behaviorDesiredMutationCount"] == 0
    )
    material = area_reduction >= minimum or residual_reduction >= minimum
    if candidate["faultInjectionCount"] == 0 or baseline["faultInjectionCount"] == 0:
        result = "NOT_APPLICABLE"
    elif not matched_start:
        result = "FAIL_UNMATCHED_FIRST_FAULT_STATE"
    elif not full_depth:
        result = "NOT_REACHED_FAULT_DEPTH"
    elif not safe:
        result = "FAIL_SAFETY_OR_AUTHORITY"
    elif material:
        result = "PASS_RECOVERY"
    else:
        result = "FAIL_NO_MATERIAL_IMPROVEMENT"
    return {
        "marketId": candidate["marketId"],
        "case": candidate["case"],
        "faultPlan": candidate["faultPlan"],
        "matchedFirstFaultState": matched_start,
        "candidateFaultsInjected": candidate["faultsInjected"],
        "baselineFaultsInjected": baseline["faultsInjected"],
        "fullFaultDepthReached": full_depth,
        "trackingErrorAreaReduction": area_reduction,
        "terminalResidualReduction": residual_reduction,
        "pairedCoverageDelta": ct["pairedCoverage"] - bt["pairedCoverage"],
        "candidateRecoveryLandmark": candidate["recoveryLandmark"],
        "baselineRecoveryLandmark": baseline["recoveryLandmark"],
        "candidateBehaviorProposalCounts": candidate["behaviorProposalCounts"],
        "baselineBehaviorProposalCounts": baseline["behaviorProposalCounts"],
        "candidateNativeControllerWaitAct": candidate.get("nativeControllerWaitAct"),
        "baselineNativeControllerWaitAct": baseline.get("nativeControllerWaitAct"),
        "candidateNativeTakerAttempts": candidate.get("nativeTakerAttempts"),
        "baselineNativeTakerAttempts": baseline.get("nativeTakerAttempts"),
        "candidatePostFaultConfirmedMakerFills": candidate.get(
            "postFaultConfirmedMakerFills"
        ),
        "baselinePostFaultConfirmedMakerFills": baseline.get(
            "postFaultConfirmedMakerFills"
        ),
        "candidateTerminal": ct,
        "baselineTerminal": bt,
        "candidateDesiredMutationCount": candidate["behaviorDesiredMutationCount"],
        "baselineDesiredMutationCount": baseline["behaviorDesiredMutationCount"],
        "candidateCycleInvariantViolations": candidate["cycleInvariantViolationCount"],
        "baselineCycleInvariantViolations": baseline["cycleInvariantViolationCount"],
        "grade": result,
    }


def fault_family(case_name: str) -> str:
    if "MIXED" in case_name:
        return "MIXED"
    return "NO_FILL_STALL" if "NO_FILL" in case_name else "SUBMIT_REJECT"


def summarize_candidate(
    comparisons: list[dict[str, Any]], candidates: list[dict[str, Any]], minimum: float
) -> dict[str, Any]:
    full_depth = [row for row in comparisons if row["fullFaultDepthReached"]]
    passed = [row for row in comparisons if row["grade"] == "PASS_RECOVERY"]
    counts = Counter()
    for row in candidates:
        counts.update(row["behaviorProposalCounts"])
    wait, act = counts.get("WAIT", 0), counts.get("ACTIVE_REPAIR", 0)
    families = sorted({fault_family(row["case"]) for row in passed})
    family_pass = {"SUBMIT_REJECT", "NO_FILL_STALL"}.issubset(families)
    baseline_area = sum(row["baselineTerminal"]["trackingErrorAreaShareSeconds"] for row in comparisons)
    candidate_area = sum(row["candidateTerminal"]["trackingErrorAreaShareSeconds"] for row in comparisons)
    baseline_residual = sum(row["baselineTerminal"]["finalAbsTrackingError"] for row in comparisons)
    candidate_residual = sum(row["candidateTerminal"]["finalAbsTrackingError"] for row in comparisons)
    oracle_area = sum(
        min(
            row["baselineTerminal"]["trackingErrorAreaShareSeconds"],
            row["candidateTerminal"]["trackingErrorAreaShareSeconds"],
        )
        for row in comparisons
    )
    oracle_residual = sum(
        min(
            row["baselineTerminal"]["finalAbsTrackingError"],
            row["candidateTerminal"]["finalAbsTrackingError"],
        )
        for row in comparisons
    )
    safety = all(
        row["candidateCycleInvariantViolations"] == 0
        and row["baselineCycleInvariantViolations"] == 0
        and row["candidateDesiredMutationCount"] == 0
        and row["baselineDesiredMutationCount"] == 0
        for row in comparisons
    )
    gate = bool(family_pass and safety)
    if gate:
        decision = "KEEP_COUPLED_RETURN_R2_FOR_BROADER_FAULT_EXAM"
    elif not full_depth:
        decision = "NEED_MORE_APPLICABLE_FAULT_DEPTH"
    else:
        decision = "REJECT_COUPLED_RETURN_R2_CANDIDATE_V1"
    return {
        "contexts": len(comparisons),
        "fullFaultDepthContexts": len(full_depth),
        "passRecoveryContexts": len(passed),
        "notApplicableContexts": sum(row["grade"] == "NOT_APPLICABLE" for row in comparisons),
        "notReachedFaultDepthContexts": sum(
            row["grade"] == "NOT_REACHED_FAULT_DEPTH" for row in comparisons
        ),
        "failedContexts": sum(row["grade"].startswith("FAIL_") for row in comparisons),
        "faultFamiliesPassed": families,
        "requiredFaultFamiliesPass": family_pass,
        "candidateWaitAct": {
            "WAIT": wait,
            "ACT": act,
            "actRate": act / (wait + act) if wait + act else 0.0,
        },
        "minimumMaterialReduction": minimum,
        "aggregateRealizedRecovery": {
            "trackingErrorAreaBaseline": baseline_area,
            "trackingErrorAreaCandidate": candidate_area,
            "trackingErrorAreaReduction": metric_reduction(baseline_area, candidate_area),
            "terminalResidualBaseline": baseline_residual,
            "terminalResidualCandidate": candidate_residual,
            "terminalResidualReduction": metric_reduction(baseline_residual, candidate_residual),
        },
        "twoActionOracleCeiling": {
            "trackingErrorArea": oracle_area,
            "trackingErrorAreaReductionVsBaseline": metric_reduction(baseline_area, oracle_area),
            "terminalResidual": oracle_residual,
            "terminalResidualReductionVsBaseline": metric_reduction(baseline_residual, oracle_residual),
        },
        "safetyPass": safety,
        "gatePass": gate,
        "decision": decision,
        "chronologicalUnseenOos": False,
    }


def summarize_native_strategy(
    comparisons: list[dict[str, Any]], candidates: list[dict[str, Any]], minimum: float
) -> dict[str, Any]:
    common = summarize_candidate(comparisons, candidates, minimum)
    wait = sum(int((row.get("nativeControllerWaitAct") or {}).get("WAIT") or 0) for row in candidates)
    act = sum(int((row.get("nativeControllerWaitAct") or {}).get("ACT") or 0) for row in candidates)
    taker_after_fault = sum(
        int((row.get("nativeTakerAttempts") or {}).get("afterFaultedAttempt") or 0)
        for row in candidates
    )
    if common["gatePass"]:
        decision = "KEEP_NATIVE_R2_AUTONOMOUS_REPAIR_FOR_BROADER_FAULT_EXAM"
    elif common["fullFaultDepthContexts"] == 0:
        decision = "NEED_MORE_NATIVE_TAKER_APPLICABILITY"
    else:
        decision = "REJECT_NATIVE_R2_AUTONOMOUS_REPAIR_ON_THIS_PILOT"
    common.update(
        {
            "candidateWaitAct": {
                "WAIT": wait,
                "ACT": act,
                "actRate": act / (wait + act) if wait + act else 0.0,
                "definition": "Post-fault Frozen R2 high-level executionChoice: WAIT versus every non-WAIT choice.",
            },
            "candidateActualTakerAttemptsAfterFaultedAttempt": taker_after_fault,
            "decision": decision,
        }
    )
    return common


def run_structural(market_ids: list[int]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for market_id in market_ids:
        for name, modes in CASES.items():
            row = run_case(market_id, name, modes)
            row["grade"] = grade(row)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "marketId": market_id,
                        "case": name,
                        "grade": row["grade"],
                        "injected": row["faultsInjected"],
                        "refresh": row["stateRefreshedAfterFault"],
                        "floorAuditOnly": row["terminalFloorAuditOnly"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    applicable = [row for row in rows if row["grade"] != "NOT_APPLICABLE"]
    passed = [row for row in applicable if row["grade"] == "PASS"]
    floors = [row["terminalFloorAuditOnly"] for row in passed if row["terminalFloorAuditOnly"] is not None]
    summary = {
        "markets": len(market_ids),
        "cases": len(rows),
        "applicable": len(applicable),
        "pass": len(passed),
        "fail": sum(row["grade"] == "FAIL" for row in rows),
        "notApplicable": sum(row["grade"] == "NOT_APPLICABLE" for row in rows),
        "structuralPassRate": len(passed) / len(applicable) if applicable else None,
        "actualMultiFaultDepthCases": sum(row["faultInjectionCount"] >= 2 for row in rows),
        "plannedMultiFaultCases": sum(len(row["faultPlan"]) >= 2 for row in rows),
        "auditOnlyPassedCaseFloor": {
            "min": min(floors) if floors else None,
            "mean": sum(floors) / len(floors) if floors else None,
            "max": max(floors) if floors else None,
        },
        "graduationRule": "Structural only. Planned multi-fault depth is reported separately and is not implied by PASS.",
    }
    return {
        "version": VERSION,
        "role": "CANONICAL_FAULT_RECOVERY_EXAMINER",
        "examMode": "structural",
        "frozenExamSemantics": True,
        "currentGoalContract": "r2_autonomous_small_loss_recovery_current_goal_v1.json",
        "rows": rows,
        "summary": summary,
    }


def run_candidate(args: argparse.Namespace, market_ids: list[int]) -> dict[str, Any]:
    names = [name.strip() for name in args.cases.split(",") if name.strip()]
    unknown = [name for name in names if name not in CASES]
    if unknown:
        raise ValueError(f"unsupported cases: {unknown}; refusing to fabricate unsupported faults")
    candidates, baselines, comparisons = [], [], []
    for market_id in market_ids:
        for name in names:
            modes = CASES[name]
            baseline = run_continuation(market_id, name, modes, "POST_FAULT_WAIT_BASELINE")
            candidate = run_continuation(market_id, name, modes, "COUPLED_RETURN_R2_AFTER_ONE_WAIT")
            comparison = compare(candidate, baseline, args.minimum_reduction)
            baselines.append(baseline)
            candidates.append(candidate)
            comparisons.append(comparison)
            print(
                json.dumps(
                    {
                        "marketId": market_id,
                        "case": name,
                        "grade": comparison["grade"],
                        "candidateFaults": candidate["faultsInjected"],
                        "baselineFaults": baseline["faultsInjected"],
                        "areaReduction": comparison["trackingErrorAreaReduction"],
                        "residualReduction": comparison["terminalResidualReduction"],
                        "violations": comparison["candidateCycleInvariantViolations"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return {
        "version": VERSION,
        "role": "CANONICAL_FAULT_RECOVERY_EXAMINER",
        "examMode": "candidate_matched_recovery",
        "frozenStructuralExamSemantics": True,
        "preregistration": "hft_r2_fault_state_refresh_exam_v2_candidate_pilot_preregistered.json",
        "currentGoalContract": "r2_autonomous_small_loss_recovery_current_goal_v1.json",
        "candidate": "COUPLED_RETURN_R2_AFTER_ONE_WAIT",
        "baseline": "POST_FAULT_WAIT_BASELINE",
        "unsupportedNotFabricated": [
            "PARTIAL_FILL_STALL",
            "CANCEL_ACK_DELAY",
            "AMBIGUOUS_EXECUTION_PENDING",
        ],
        "candidateRows": candidates,
        "baselineRows": baselines,
        "comparisons": comparisons,
        "summary": summarize_candidate(comparisons, candidates, args.minimum_reduction),
    }


def run_strategy(args: argparse.Namespace, market_ids: list[int]) -> dict[str, Any]:
    names = [name.strip() for name in args.cases.split(",") if name.strip()]
    unknown = [name for name in names if name not in CASES]
    if unknown:
        raise ValueError(f"unsupported cases: {unknown}; refusing to fabricate unsupported faults")
    if args.strategy_candidate == "native":
        candidate_name = "NATIVE_FROZEN_R2_COUPLED_PASSIVE_LOOP"
        baseline_name = "POST_FAULT_WAIT_BASELINE"
        preregistration = "hft_r2_fault_state_refresh_exam_v2_native_strategy_pilot_preregistered.json"
    elif args.strategy_candidate == "bounded_active_passive":
        candidate_name = "BOUNDED_ACTIVE_THEN_PASSIVE"
        baseline_name = "TARGET_PRESERVING_POST_FAULT_NO_REPAIR"
        preregistration = "hft_r2_fault_state_refresh_exam_v2_bounded_active_passive_basic_preregistered.json"
    else:
        candidate_name = "PASSIVE_ONLY_AFTER_FAULT"
        baseline_name = "TARGET_PRESERVING_POST_FAULT_NO_REPAIR"
        preregistration = "hft_r2_fault_state_refresh_exam_v2_passive_only_basic_preregistered.json"
    if args.fault_target == "repair_aligned":
        preregistration = (
            "hft_r2_fault_state_refresh_exam_v2_repair_aligned_passive_basic_preregistered.json"
        )
    references, candidates, baselines, comparisons = [], [], [], []
    for market_id in market_ids:
        reference = run_native_strategy_continuation(
            market_id,
            "NO_FAULT_REFERENCE",
            [],
            "NATIVE_NO_FAULT_REFERENCE",
            args.fault_target,
        )
        references.append(reference)
        print(
            json.dumps(
                {
                    "marketId": market_id,
                    "case": "NO_FAULT_REFERENCE",
                    "area": reference["terminal"]["trackingErrorAreaShareSeconds"],
                    "residual": reference["terminal"]["finalAbsTrackingError"],
                    "floorAuditOnly": reference["terminal"]["worstCaseFloorAuditOnly"],
                    "violations": reference["cycleInvariantViolationCount"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        for name in names:
            modes = CASES[name]
            baseline = run_native_strategy_continuation(
                market_id, name, modes, baseline_name, args.fault_target
            )
            candidate = run_native_strategy_continuation(
                market_id, name, modes, candidate_name, args.fault_target
            )
            comparison = compare(candidate, baseline, args.minimum_reduction)
            if (
                candidate_name == "PASSIVE_ONLY_AFTER_FAULT"
                and comparison["grade"] == "PASS_RECOVERY"
                and int(candidate["postFaultConfirmedMakerFills"]["count"]) == 0
            ):
                comparison["grade"] = "FAIL_NO_CONFIRMED_PASSIVE_FILL"
            baselines.append(baseline)
            candidates.append(candidate)
            comparisons.append(comparison)
            print(
                json.dumps(
                    {
                        "marketId": market_id,
                        "case": name,
                        "grade": comparison["grade"],
                        "candidateFaults": candidate["faultsInjected"],
                        "baselineFaults": baseline["faultsInjected"],
                        "matchedFirstFaultState": comparison["matchedFirstFaultState"],
                        "areaReduction": comparison["trackingErrorAreaReduction"],
                        "residualReduction": comparison["terminalResidualReduction"],
                        "nativeWaitAct": candidate["nativeControllerWaitAct"],
                        "violations": comparison["candidateCycleInvariantViolations"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    summary = summarize_native_strategy(comparisons, candidates, args.minimum_reduction)
    if candidate_name == "BOUNDED_ACTIVE_THEN_PASSIVE":
        summary["decision"] = (
            "KEEP_BOUNDED_ACTIVE_THEN_PASSIVE_FOR_BROADER_FAULT_EXAM"
            if summary["gatePass"]
            else "REJECT_BOUNDED_ACTIVE_THEN_PASSIVE_ON_THIS_BASIC_PILOT"
        )
    elif candidate_name == "PASSIVE_ONLY_AFTER_FAULT":
        if summary["fullFaultDepthContexts"] == 0:
            summary["decision"] = "NEED_MORE_REPAIR_ALIGNED_FAULT_APPLICABILITY"
        else:
            summary["decision"] = (
                "KEEP_PASSIVE_ONLY_COMPONENT_FOR_OWNERSHIP_LINK_EXAM"
                if summary["gatePass"]
                else "REJECT_PASSIVE_ONLY_AFTER_DIRECT_FAULT_ON_THIS_BASIC_PILOT"
            )
    summary.update({"candidateName": candidate_name, "baselineName": baseline_name})
    return {
        "version": VERSION,
        "role": "CANONICAL_FAULT_RECOVERY_EXAMINER",
        "examMode": "native_strategy_matched_recovery",
        "frozenStructuralExamSemantics": True,
        "preregistration": preregistration,
        "currentGoalContract": "r2_autonomous_small_loss_recovery_current_goal_v1.json",
        "candidate": candidate_name,
        "baseline": baseline_name,
        "faultTarget": args.fault_target,
        "noFaultReference": "NATIVE_FROZEN_R2_COUPLED_PASSIVE_LOOP_EMPTY_FAULT_PLAN",
        "waitActDefinition": "Frozen R2 high-level executionChoice after first fault; WAIT versus every non-WAIT choice.",
        "unsupportedNotFabricated": [
            "PARTIAL_FILL_STALL",
            "CANCEL_ACK_DELAY",
            "AMBIGUOUS_EXECUTION_PENDING",
        ],
        "referenceRows": references,
        "candidateRows": candidates,
        "baselineRows": baselines,
        "comparisons": comparisons,
        "summary": summary,
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-ids", default="1579674,1579313,1578732")
    parser.add_argument("--output", default="hft_r2_fault_state_refresh_exam_v2_report.json")
    parser.add_argument(
        "--exam-mode", choices=("structural", "candidate", "strategy"), default="structural"
    )
    parser.add_argument(
        "--cases",
        default="SINGLE_REJECT,DOUBLE_REJECT,SINGLE_NO_FILL,DOUBLE_NO_FILL,MIXED_REJECT_NO_FILL",
    )
    parser.add_argument("--minimum-reduction", type=float, default=0.30)
    parser.add_argument(
        "--strategy-candidate",
        choices=("native", "bounded_active_passive", "passive_only"),
        default="native",
    )
    parser.add_argument(
        "--fault-target",
        choices=("any", "repair_aligned"),
        default="any",
    )
    args = parser.parse_args()
    if not 0.0 <= args.minimum_reduction <= 1.0:
        raise ValueError("minimum-reduction must be in [0, 1]")
    market_ids = [int(value) for value in args.market_ids.split(",") if value.strip()]
    if args.exam_mode == "structural":
        report = run_structural(market_ids)
    elif args.exam_mode == "candidate":
        report = run_candidate(args, market_ids)
    else:
        report = run_strategy(args, market_ids)
    output = OUT / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
