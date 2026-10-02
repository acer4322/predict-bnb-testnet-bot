from __future__ import annotations

import argparse
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
    FirstNMakerFaults,
    R21ObligationResidualInbox,
)

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
BELIEF_MODEL_PATH = OUT / "hft_r21_obligation_residual_belief_v2.joblib"
VERSION = "HFT_R21_CANCEL_FILL_RACE_COOPERATION_EXAM_V1"
CHUNK = 18.0
EPS = 1e-8

SCENARIOS = (
    {"marketId": 1574038, "initialMakerFault": "NO_FILL_STALL"},
    {"marketId": 1573848, "initialMakerFault": "SUBMIT_REJECT"},
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def incident_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("incidentType") or "NONE")
        counts[key] = counts.get(key, 0) + 1
    return counts


def expected_recomputed_qty(episode: dict[str, Any]) -> float:
    side = str(episode.get("side") or "")
    error = finite(episode.get("trackingErrorAtCancelAck"))
    needed = (error > EPS and side == "DOWN") or (error < -EPS and side == "UP")
    return min(CHUNK, abs(error)) if needed else 0.0


def run_case(scenario: dict[str, Any], response_model: Any) -> dict[str, Any]:
    market_id = int(scenario["marketId"])
    initial_fault = str(scenario["initialMakerFault"])
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    belief_artifact = joblib.load(BELIEF_MODEL_PATH)
    belief_model = belief_artifact["model"] if isinstance(belief_artifact, dict) else belief_artifact
    provider = R21ObligationResidualInbox(
        f"R21_CANCEL_FILL_RACE_{market_id}", receiver, bridge, model=belief_model
    )
    maker_faults = FirstNMakerFaults(initial_fault, count=3)
    policy = FrozenResponseThenFallbackPolicy(provider, response_model, "WAIT")
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        maker_submit_fault_override=maker_faults,
        fault_reentry_enabled=True,
        behavior_policy_override=bridge,
        behavior_ownstate_reentry=False,
        lifecycle_action_override=policy,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
        fault_no_fill_stall_ms=15_000,
        r21_incident_inbox_provider=provider,
    )

    episodes = [
        row
        for row in report["lifecycleAudit"]["replaceEpisodes"]
        if row.get("childOrderNum") is not None and row.get("cancelRequestedAtMs") is not None
    ]
    episode = episodes[0] if episodes else {}
    trigger_at = int(episode.get("triggerAtMs") or 0)
    ack_at = int(episode.get("cancelTerminalObservedAtMs") or 0)
    child_num = episode.get("childOrderNum")
    cum_at_trigger = max(
        0.0,
        finite(episode.get("requestedQty")) - finite(episode.get("childLeavesAtTrigger")),
    )
    cum_at_terminal = finite(episode.get("childCumExecAtTerminal"))
    fill_during_cancel = max(0.0, cum_at_terminal - cum_at_trigger)
    expected_qty = expected_recomputed_qty(episode) if episode else None
    actual_qty = finite(episode.get("recomputedQty")) if episode else None
    pair_attempts = [
        row
        for row in report["executionLifecycleTrace"]["takerAttempts"]
        if str(row.get("kind") or "") == "PAIR_COMPLETION_REPLACE"
    ]
    first_pair_attempt_at = min(
        (int(row.get("atMs") or row.get("submittedAtMs") or 0) for row in pair_attempts),
        default=0,
    )
    notifications = list(receiver.notifications)
    cancel_fill_notifications = [
        row for row in notifications if str(row.get("incidentType") or "") == "FILL_DURING_CANCEL"
    ]
    fill_rows = [
        row
        for row in report["executionLifecycleTrace"]["makerFills"]
        if child_num is not None
        and int(row.get("orderNum") or -1) == int(child_num)
        and trigger_at <= int(row.get("observedAtMs") or row.get("eventMs") or 0) <= ack_at
    ]
    terminal_ack = str(episode.get("cancelAckEvidence") or "") == "VENUE_TERMINAL_OBSERVED"
    no_taker_before_ack = not pair_attempts or (ack_at > 0 and first_pair_attempt_at >= ack_at)
    recompute_exact = (
        expected_qty is not None
        and actual_qty is not None
        and abs(float(expected_qty) - float(actual_qty)) <= EPS
    )
    semantic_clean = all(bool(value) for value in report["semanticGate"].values())
    common_clean = (
        policy.primary_prediction == ACTIVE
        and maker_faults.used > 0
        and bool(episode)
        and terminal_ack
        and no_taker_before_ack
        and recompute_exact
        and not provider.strict_past_violations
        and not receiver.schema_errors
        and int(report["cycleInvariantViolationCount"]) == 0
        and semantic_clean
    )
    race_applicable = fill_during_cancel > EPS
    race_notified = bool(cancel_fill_notifications)
    race_pass = common_clean and race_applicable and race_notified
    return {
        "marketId": market_id,
        "initialMakerFault": initial_fault,
        "makerFaultsUsed": maker_faults.used,
        "waitAct": {"WAIT": 0, "ACT": int(policy.primary_prediction == ACTIVE)},
        "primaryPrediction": policy.primary_prediction,
        "primaryActiveProbability": policy.primary_probability,
        "primaryStateHash": None if policy.primary_anchor is None else policy.primary_anchor.get("stateHash"),
        "r21": {
            "actionAuthority": False,
            "incidentCounts": incident_counts(notifications),
            "fillDuringCancelNotifications": cancel_fill_notifications,
            "consumedIncidentTypes": report["r21InformationInbox"]["consumedIncidentTypes"],
            "strictPastViolations": provider.strict_past_violations,
            "receiverSchemaErrors": receiver.schema_errors,
        },
        "cancelReplace": {
            "childOrderNum": child_num,
            "triggerAtMs": trigger_at,
            "cancelRequestedAtMs": episode.get("cancelRequestedAtMs"),
            "cancelTerminalObservedAtMs": ack_at,
            "cancelAckEvidence": episode.get("cancelAckEvidence"),
            "cancelTerminalStatus": episode.get("cancelTerminalStatus"),
            "requestedQty": episode.get("requestedQty"),
            "childLeavesAtTrigger": episode.get("childLeavesAtTrigger"),
            "childCumExecAtTriggerDerived": cum_at_trigger,
            "childCumExecAtTerminal": episode.get("childCumExecAtTerminal"),
            "fillDuringCancelShares": fill_during_cancel,
            "fillRowsDuringCancel": fill_rows,
            "actualNetAtCancelAck": episode.get("actualNetAtCancelAck"),
            "targetNetAtCancelAck": episode.get("targetNetAtCancelAck"),
            "trackingErrorAtCancelAck": episode.get("trackingErrorAtCancelAck"),
            "targetRevisionAtCancelAck": episode.get("targetRevisionAtCancelAck"),
            "expectedRecomputedQty": expected_qty,
            "actualRecomputedQty": actual_qty,
            "pairCompletionAttemptAtMs": first_pair_attempt_at,
        },
        "gates": {
            "terminalCancelAckObserved": terminal_ack,
            "noPairCompletionTakerBeforeAck": no_taker_before_ack,
            "actualFillRecomputeExact": recompute_exact,
            "actualFillDuringCancelObserved": race_applicable,
            "fillDuringCancelNotified": race_notified,
            "commonStrictPastLifecycleClean": common_clean,
            "applicableRacePass": race_pass,
        },
        "terminalAudit": {
            "finalAbsTrackingError": report["actualExecution"].get("finalAbsTrackingError"),
            "trackingErrorAreaShareSeconds": report["actualExecution"].get("targetErrorAreaShareSeconds"),
            "worstCaseFloor": report["actualExecution"]["finalPortfolio"].get("worst_case_floor"),
            "realizedPnl": report["actualExecution"].get("realizedPnl"),
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "semanticGate": report["semanticGate"],
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", default="hft_r21_cancel_fill_race_cooperation_exam_v1_report.json"
    )
    parser.add_argument("--max-markets", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    actual_hash = file_sha256(RESPONSE_MODEL_PATH)
    if actual_hash != MODEL_SHA256:
        raise RuntimeError(f"Frozen response model hash changed: {actual_hash}")
    response_artifact = joblib.load(RESPONSE_MODEL_PATH)
    if not isinstance(response_artifact, dict) or response_artifact.get("version") != RESPONSE_VERSION:
        raise RuntimeError("Frozen response model contract/version mismatch")
    response_model = response_artifact["model"]

    rows = []
    for scenario in SCENARIOS[: args.max_markets]:
        row = run_case(scenario, response_model)
        rows.append(row)
        print(
            json.dumps(
                {
                    "marketId": row["marketId"],
                    "initialMakerFault": row["initialMakerFault"],
                    "primaryPrediction": row["primaryPrediction"],
                    "cancelAck": row["gates"]["terminalCancelAckObserved"],
                    "fillDuringCancelShares": row["cancelReplace"]["fillDuringCancelShares"],
                    "notified": row["gates"]["fillDuringCancelNotified"],
                    "recomputeExact": row["gates"]["actualFillRecomputeExact"],
                    "commonClean": row["gates"]["commonStrictPastLifecycleClean"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if row["gates"]["actualFillDuringCancelObserved"]:
            break

    applicable = [row for row in rows if row["gates"]["actualFillDuringCancelObserved"]]
    common_clean = all(row["gates"]["commonStrictPastLifecycleClean"] for row in rows)
    if applicable and all(row["gates"]["applicableRacePass"] for row in applicable):
        decision = "KEEP_R21_CANCEL_FILL_RACE_COOPERATION_V1"
    elif applicable:
        decision = "REJECT_R21_CANCEL_FILL_RACE_COOPERATION_V1"
    else:
        decision = "NEED_MORE_DATA_NATURAL_CANCEL_FILL_RACE"
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "hft_r21_cancel_fill_race_cooperation_exam_v1_preregistered.json",
        "frozenResponseModelSha256": actual_hash,
        "cohort": [dict(row) for row in SCENARIOS],
        "stopRule": "At most two opened development markets; stop after first actual fill during Maker cancel.",
        "executionSemantics": "HftBacktest + Predict Execution Tape V1; risk queue; 1092ms entry / 273ms response latency; 250ms own-state polling; confirmed actual fills only; no cancel-race fill injection",
        "r21ExecutionAuthority": False,
        "rows": rows,
        "summary": {
            "marketsRun": len(rows),
            "waitAct": {
                "WAIT": sum(row["waitAct"]["WAIT"] for row in rows),
                "ACT": sum(row["waitAct"]["ACT"] for row in rows),
            },
            "terminalCancelAckMarkets": sum(row["gates"]["terminalCancelAckObserved"] for row in rows),
            "exactRecomputeMarkets": sum(row["gates"]["actualFillRecomputeExact"] for row in rows),
            "actualCancelFillRaceMarkets": len(applicable),
            "notifiedCancelFillRaceMarkets": sum(row["gates"]["applicableRacePass"] for row in applicable),
            "commonLifecycleClean": common_clean,
            "decision": decision,
        },
        "interpretation": "R2.1 only reports lifecycle facts. Frozen R2 selects the single bounded ACTIVE route. The executor must retain cancel ownership, wait for venue terminal evidence, and recompute the Taker remainder from confirmed actual state at ACK.",
        "next": "If no natural cancel-time fill appears, collect or replay a real own-wallet cancel/fill race before adding an HftBacktest tape-derived race fixture; never synthesize a favorable fill. If applicable and KEEP, move next to a late-fill-after-Taker-terminal cooperation case.",
    }
    output = OUT / args.output
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
