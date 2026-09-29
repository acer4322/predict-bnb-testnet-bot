from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import warnings
from pathlib import Path
from typing import Any

import joblib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter as base  # noqa: E402
from tools.hft_r21_active_failure_passive_return_exam_v1 import (  # noqa: E402
    MODEL_SHA256,
    FrozenResponseThenFallbackPolicy,
    file_sha256,
)
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402
from tools.hft_r2_execution_incident_notification_exam_v1 import (  # noqa: E402
    ExecutionIncidentNotifier,
    HftIncidentBridge,
    IncidentReceiverProbe,
)
from tools.train_evaluate_hft_r21_cooperation_response_head_v1 import (  # noqa: E402
    ACTIVE,
    MODEL_PATH as RESPONSE_MODEL_PATH,
    VERSION as RESPONSE_VERSION,
)
from tools.train_hft_r21_lifecycle_belief_v1 import RecordingIncidentBridge  # noqa: E402
from tools.train_hft_r21_obligation_residual_belief_v2 import (  # noqa: E402
    R21ObligationResidualInbox,
)

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
BELIEF_MODEL_PATH = OUT / "hft_r21_obligation_residual_belief_v2.joblib"
VERSION = "HFT_R21_KNOWN_CANCEL_RACE_OVERLAY_EXAM_V1"
MARKET_ID = 1522232
LIFECYCLE_SHA256 = "787EEB82C50B458CCB8883DF70C34CA9350EBDEC8C2AB464715CD3510565249D"
CHUNK = 18.0
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def expected_recomputed_qty(episode: dict[str, Any]) -> float:
    side = str(episode.get("side") or "")
    error = finite(episode.get("trackingErrorAtCancelAck"))
    needed = (error > EPS and side == "DOWN") or (error < -EPS and side == "UP")
    return min(CHUNK, abs(error)) if needed else 0.0


def race_episodes(report: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in report["lifecycleAudit"]["replaceEpisodes"]:
        leaves = finite(row.get("childLeavesAtTrigger"))
        terminal = finite(row.get("childCumExecAtTerminal"))
        requested = finite(row.get("requestedQty"))
        at_trigger = max(0.0, requested - leaves)
        delta = max(0.0, terminal - at_trigger)
        if delta <= EPS:
            continue
        expected = expected_recomputed_qty(row)
        result.append(
            {
                "side": row.get("side"),
                "childOrderNum": row.get("childOrderNum"),
                "triggerAtMs": row.get("triggerAtMs"),
                "cancelRequestedAtMs": row.get("cancelRequestedAtMs"),
                "cancelTerminalObservedAtMs": row.get("cancelTerminalObservedAtMs"),
                "cancelAckEvidence": row.get("cancelAckEvidence"),
                "cancelTerminalStatus": row.get("cancelTerminalStatus"),
                "childCumExecAtTriggerDerived": at_trigger,
                "childCumExecAtTerminal": terminal,
                "fillDuringCancelShares": delta,
                "trackingErrorAtCancelAck": row.get("trackingErrorAtCancelAck"),
                "targetRevisionAtCancelAck": row.get("targetRevisionAtCancelAck"),
                "expectedRecomputedQty": expected,
                "actualRecomputedQty": finite(row.get("recomputedQty")),
                "recomputeExact": abs(expected - finite(row.get("recomputedQty"))) <= EPS,
                "takerOrderNum": row.get("takerOrderNum"),
                "takerSubmittedAtMs": row.get("ackAtMs"),
                "noTakerBeforeTerminalAck": int(row.get("ackAtMs") or 0)
                >= int(row.get("cancelTerminalObservedAtMs") or 0),
            }
        )
    return result


def run_baseline(lifecycle_artifact: dict[str, Any]) -> dict[str, Any]:
    report = run_smoke(
        MARKET_ID,
        passive_mode="offset1",
        own_state_poll_ms=250,
        lifecycle_artifact=lifecycle_artifact,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
    )
    return {
        "report": report,
        "raceEpisodes": race_episodes(report),
        "actualExecutionHash": stable_hash(report["actualExecution"]),
        "controllerDecisionHash": stable_hash(report["controller"]["decisions"]),
        "makerLifecycleHash": stable_hash(report["executionLifecycleTrace"]["makerSubmits"] + report["executionLifecycleTrace"]["makerFills"]),
        "takerLifecycleHash": stable_hash(report["executionLifecycleTrace"]["takerAttempts"] + report["executionLifecycleTrace"]["takerFills"]),
    }


def run_candidate(lifecycle_artifact: dict[str, Any], response_model: Any) -> dict[str, Any]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    belief_artifact = joblib.load(BELIEF_MODEL_PATH)
    belief_model = belief_artifact["model"] if isinstance(belief_artifact, dict) else belief_artifact
    provider = R21ObligationResidualInbox(
        "R21_KNOWN_CANCEL_RACE_1522232", receiver, bridge, model=belief_model
    )
    policy = FrozenResponseThenFallbackPolicy(provider, response_model, "WAIT")
    report = run_smoke(
        MARKET_ID,
        passive_mode="offset1",
        own_state_poll_ms=250,
        lifecycle_artifact=lifecycle_artifact,
        lifecycle_action_override=policy,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        behavior_policy_override=bridge,
        behavior_ownstate_reentry=False,
        trace_execution_states=True,
        r21_incident_inbox_provider=provider,
    )
    notifications = list(receiver.notifications)
    fill_during_cancel = [
        row for row in notifications if str(row.get("incidentType") or "") == "FILL_DURING_CANCEL"
    ]
    return {
        "report": report,
        "raceEpisodes": race_episodes(report),
        "actualExecutionHash": stable_hash(report["actualExecution"]),
        "controllerDecisionHash": stable_hash(report["controller"]["decisions"]),
        "makerLifecycleHash": stable_hash(report["executionLifecycleTrace"]["makerSubmits"] + report["executionLifecycleTrace"]["makerFills"]),
        "takerLifecycleHash": stable_hash(report["executionLifecycleTrace"]["takerAttempts"] + report["executionLifecycleTrace"]["takerFills"]),
        "r21": {
            "actionAuthority": False,
            "notifications": len(notifications),
            "fillDuringCancelNotifications": fill_during_cancel,
            "consumedIncidentTypes": report["r21InformationInbox"]["consumedIncidentTypes"],
            "strictPastViolations": provider.strict_past_violations,
            "receiverSchemaErrors": receiver.schema_errors,
            "responseHeadApplied": policy.primary_applied,
            "responsePrimaryPrediction": policy.primary_prediction,
            "responsePrimaryActiveProbability": policy.primary_probability,
            "responseWaitAct": {
                "WAIT": int(policy.primary_prediction is not None and policy.primary_prediction != ACTIVE),
                "ACT": int(policy.primary_prediction == ACTIVE),
            },
        },
    }


def strip(value: dict[str, Any]) -> dict[str, Any]:
    report = value["report"]
    return {
        "raceEpisodes": value["raceEpisodes"],
        "hashes": {
            "actualExecution": value["actualExecutionHash"],
            "controllerDecisions": value["controllerDecisionHash"],
            "makerLifecycle": value["makerLifecycleHash"],
            "takerLifecycle": value["takerLifecycleHash"],
        },
        "lifecycleActionCounts": report["lifecycleAudit"]["actionCounts"],
        "terminalAudit": {
            "finalAbsTrackingError": report["actualExecution"].get("finalAbsTrackingError"),
            "trackingErrorAreaShareSeconds": report["actualExecution"].get("targetErrorAreaShareSeconds"),
            "worstCaseFloor": report["actualExecution"]["finalPortfolio"].get("worst_case_floor"),
            "realizedPnl": report["actualExecution"].get("realizedPnl"),
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "semanticGate": report["semanticGate"],
        **({"r21": value["r21"]} if "r21" in value else {}),
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", default="hft_r21_known_cancel_race_overlay_exam_v1_report.json"
    )
    args = parser.parse_args()
    if file_sha256(RESPONSE_MODEL_PATH) != MODEL_SHA256:
        raise RuntimeError("Frozen response model hash changed")
    if file_sha256(base.MODEL_PATH) != LIFECYCLE_SHA256:
        raise RuntimeError("Frozen sequential lifecycle artifact hash changed")
    response_artifact = joblib.load(RESPONSE_MODEL_PATH)
    if not isinstance(response_artifact, dict) or response_artifact.get("version") != RESPONSE_VERSION:
        raise RuntimeError("Frozen response model contract/version mismatch")
    response_model = response_artifact["model"]
    lifecycle_artifact = joblib.load(base.MODEL_PATH)

    baseline_raw = run_baseline(lifecycle_artifact)
    print(
        json.dumps(
            {
                "branch": "BASELINE",
                "raceEpisodes": len(baseline_raw["raceEpisodes"]),
                "raceFillShares": sum(row["fillDuringCancelShares"] for row in baseline_raw["raceEpisodes"]),
            }
        ),
        flush=True,
    )
    candidate_raw = run_candidate(lifecycle_artifact, response_model)
    candidate_r21 = candidate_raw["r21"]
    print(
        json.dumps(
            {
                "branch": "R21_OVERLAY",
                "raceEpisodes": len(candidate_raw["raceEpisodes"]),
                "raceFillShares": sum(row["fillDuringCancelShares"] for row in candidate_raw["raceEpisodes"]),
                "fillDuringCancelNotifications": len(candidate_r21["fillDuringCancelNotifications"]),
                "responseHeadApplied": candidate_r21["responseHeadApplied"],
                "responsePrimaryPrediction": candidate_r21["responsePrimaryPrediction"],
            }
        ),
        flush=True,
    )

    baseline = strip(baseline_raw)
    candidate = strip(candidate_raw)
    exact = {
        "actualExecution": baseline["hashes"]["actualExecution"] == candidate["hashes"]["actualExecution"],
        "controllerDecisions": baseline["hashes"]["controllerDecisions"] == candidate["hashes"]["controllerDecisions"],
        "makerLifecycle": baseline["hashes"]["makerLifecycle"] == candidate["hashes"]["makerLifecycle"],
        "takerLifecycle": baseline["hashes"]["takerLifecycle"] == candidate["hashes"]["takerLifecycle"],
    }
    races = candidate["raceEpisodes"]
    actual_race = bool(races)
    notified = bool(candidate["r21"]["fillDuringCancelNotifications"])
    consumed = "FILL_DURING_CANCEL" in candidate["r21"]["consumedIncidentTypes"]
    route_safe = bool(races) and all(
        row["cancelAckEvidence"] == "VENUE_TERMINAL_OBSERVED"
        and row["noTakerBeforeTerminalAck"]
        and row["recomputeExact"]
        for row in races
    )
    semantic_clean = all(bool(value) for value in candidate["semanticGate"].values())
    common_clean = (
        not candidate["r21"]["strictPastViolations"]
        and not candidate["r21"]["receiverSchemaErrors"]
        and candidate["cycleInvariantViolationCount"] == 0
        and semantic_clean
    )
    keep = actual_race and notified and consumed and route_safe and common_clean and all(exact.values())
    decision = (
        "KEEP_R21_KNOWN_CANCEL_RACE_OVERLAY_V1"
        if keep
        else (
            "NEED_MORE_DATA_KNOWN_V7_RACE_NOT_REPRODUCED_ON_V10"
            if not actual_race
            else "REJECT_R21_KNOWN_CANCEL_RACE_OVERLAY_V1"
        )
    )
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "hft_r21_known_cancel_race_overlay_exam_v1_preregistered.json",
        "marketId": MARKET_ID,
        "frozenResponseModelSha256": MODEL_SHA256,
        "frozenSequentialLifecycleSha256": LIFECYCLE_SHA256,
        "executionSemantics": "HftBacktest + Predict Execution Tape V1; known opened-development cancel-race support; risk queue; 1092ms entry / 273ms response; 250ms own-state polling; confirmed actual fills only",
        "r21ExecutionAuthority": False,
        "baseline": baseline,
        "candidate": candidate,
        "exactNonInterference": exact,
        "gates": {
            "actualCancelFillRaceReproduced": actual_race,
            "fillDuringCancelNotificationEmitted": notified,
            "fillDuringCancelConsumedByR2Inbox": consumed,
            "cancelOwnershipAckRecomputeSafe": route_safe,
            "strictPastReceiverCycleSemanticClean": common_clean,
            "exactTrajectoryNonInterference": all(exact.values()),
        },
        "waitAct": candidate["r21"]["responseWaitAct"],
        "oracleValueCeiling": None,
        "learnedPolicyRealizedValue": None,
        "decision": decision,
        "interpretation": "This is a known-support lifecycle overlay, not a new policy/value backtest. R2.1 must expose the actual cancel-time fill to the R2 input path while preserving the already-correct executor sequence and Frozen R2 trajectory.",
        "next": "If KEEP, retain this event as a regression fixture and move to a genuine terminal-partial Maker remainder case. If REJECT, fix only the R2.1 observation boundary; do not alter R2 or cancel/recompute mechanics.",
    }
    output = OUT / args.output
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"decision": decision, **payload["gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
