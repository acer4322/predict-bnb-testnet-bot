from __future__ import annotations

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

from tools import hftbacktest_r2_online_target_ledger_pair_completion_v7_cancel_ack_evidence as v7  # noqa: E402
from tools.hft_r21_active_failure_passive_return_exam_v1 import (  # noqa: E402
    MODEL_SHA256,
    FrozenResponseThenFallbackPolicy,
    file_sha256,
)
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
MARKET_ID = 1522232
VERSION = "HFT_R21_KNOWN_CANCEL_RACE_V7_OVERLAY_EXAM_V1"
EPS = 1e-8


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class RecordingProvider:
    def __init__(self, provider: R21ObligationResidualInbox) -> None:
        self.provider = provider
        self.calls = 0
        self.nonempty_calls = 0
        self.incident_types: set[str] = set()

    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        payload = self.provider(snapshot)
        events = payload.get("incidents") or []
        if events:
            self.nonempty_calls += 1
        self.incident_types.update(str(row.get("incidentType")) for row in events)
        return payload


def races(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for episode in report["lifecycle"]["replaceEpisodes"]:
        leaves = finite(episode.get("childLeavesAtTrigger"))
        cum_trigger = max(0.0, 18.0 - leaves)
        cum_terminal = finite(episode.get("childCumExecAtTerminal"))
        delta = max(0.0, cum_terminal - cum_trigger)
        if delta <= EPS:
            continue
        side = str(episode.get("side") or "")
        error = finite(episode.get("trackingErrorAtCancelAck"))
        needed = (error > EPS and side == "DOWN") or (error < -EPS and side == "UP")
        expected = min(18.0, abs(error)) if needed else 0.0
        rows.append(
            {
                "side": side,
                "childOrderNum": episode.get("childOrderNum"),
                "triggerAtMs": episode.get("triggerAtMs"),
                "cancelRequestedAtMs": episode.get("cancelRequestedAtMs"),
                "cancelTerminalObservedAtMs": episode.get("cancelTerminalObservedAtMs"),
                "cancelAckEvidence": episode.get("cancelAckEvidence"),
                "childCumExecAtTriggerDerived": cum_trigger,
                "childCumExecAtTerminal": cum_terminal,
                "fillDuringCancelShares": delta,
                "trackingErrorAtCancelAck": error,
                "expectedRecomputedQty": expected,
                "actualRecomputedQty": finite(episode.get("recomputedQty")),
                "recomputeExact": abs(expected - finite(episode.get("recomputedQty"))) <= EPS,
                "noTakerBeforeTerminalAck": int(episode.get("ackAtMs") or 0)
                >= int(episode.get("cancelTerminalObservedAtMs") or 0),
            }
        )
    return rows


def compact(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "raceEpisodes": races(report),
        "hashes": {
            "controllerDecisions": stable_hash(report["lifecycle"]["decisions"]),
            "actualExecution": stable_hash(report["actualExecution"]),
            "makerLifecycle": stable_hash(report["makerSubmits"] + report["makerFills"]),
            "takerLifecycle": stable_hash(report["takerAttempts"] + report["takerFills"]),
        },
        "actionCounts": report["lifecycle"]["actionCounts"],
        "terminalAudit": {
            "finalAbsTrackingError": report["actualExecution"]["finalAbsTrackingError"],
            "trackingErrorAreaShareSeconds": report["actualExecution"]["targetErrorAreaShareSeconds"],
            "worstCaseFloor": report["actualExecution"]["finalPortfolio"]["worst_case_floor"],
            "realizedPnl": report["actualExecution"]["realizedPnl"],
        },
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    if file_sha256(RESPONSE_MODEL_PATH) != MODEL_SHA256:
        raise RuntimeError("Frozen response model hash changed")
    response_artifact = joblib.load(RESPONSE_MODEL_PATH)
    if not isinstance(response_artifact, dict) or response_artifact.get("version") != RESPONSE_VERSION:
        raise RuntimeError("Frozen response model contract/version mismatch")

    baseline_raw = v7.run_market(MARKET_ID)
    print(json.dumps({"branch": "V7_BASELINE", "raceEpisodes": len(races(baseline_raw))}), flush=True)

    receiver = IncidentReceiverProbe()
    bridge = RecordingIncidentBridge(HftIncidentBridge(ExecutionIncidentNotifier(receiver)))
    belief_artifact = joblib.load(BELIEF_MODEL_PATH)
    belief_model = belief_artifact["model"] if isinstance(belief_artifact, dict) else belief_artifact
    provider = R21ObligationResidualInbox("R21_V7_CANCEL_RACE_1522232", receiver, bridge, model=belief_model)
    recording_provider = RecordingProvider(provider)
    policy = FrozenResponseThenFallbackPolicy(provider, response_artifact["model"], "WAIT")
    candidate_raw = v7.run_market(
        MARKET_ID,
        execution_observer=bridge,
        r21_inbox_provider=recording_provider,
        lifecycle_action_override=policy,
    )
    notices = [row for row in receiver.notifications if row.get("incidentType") == "FILL_DURING_CANCEL"]
    print(
        json.dumps(
            {
                "branch": "V7_R21_OVERLAY",
                "raceEpisodes": len(races(candidate_raw)),
                "fillDuringCancelNotifications": len(notices),
                "consumed": "FILL_DURING_CANCEL" in recording_provider.incident_types,
                "responseHeadApplied": policy.primary_applied,
                "responsePrimaryPrediction": policy.primary_prediction,
            }
        ),
        flush=True,
    )
    baseline = compact(baseline_raw)
    candidate = compact(candidate_raw)
    candidate["r21"] = {
        "actionAuthority": False,
        "fillDuringCancelNotifications": notices,
        "inboxCalls": recording_provider.calls,
        "nonemptyInboxCalls": recording_provider.nonempty_calls,
        "consumedIncidentTypes": sorted(recording_provider.incident_types),
        "strictPastViolations": provider.strict_past_violations,
        "receiverSchemaErrors": receiver.schema_errors,
        "responseHeadApplied": policy.primary_applied,
        "responsePrimaryPrediction": policy.primary_prediction,
        "responsePrimaryActiveProbability": policy.primary_probability,
    }
    exact = {key: baseline["hashes"][key] == candidate["hashes"][key] for key in baseline["hashes"]}
    race = bool(candidate["raceEpisodes"])
    notified = bool(notices)
    consumed = "FILL_DURING_CANCEL" in recording_provider.incident_types
    route_safe = race and all(
        row["cancelAckEvidence"] == "VENUE_TERMINAL_OBSERVED"
        and row["recomputeExact"]
        and row["noTakerBeforeTerminalAck"]
        for row in candidate["raceEpisodes"]
    )
    clean = not provider.strict_past_violations and not receiver.schema_errors
    response_inactive = not policy.primary_applied
    keep = race and notified and consumed and route_safe and clean and response_inactive and all(exact.values())
    decision = "KEEP_R21_KNOWN_CANCEL_RACE_V7_OVERLAY_V1" if keep else "REJECT_R21_KNOWN_CANCEL_RACE_V7_OVERLAY_V1"
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "hft_r21_known_cancel_race_v7_overlay_exam_v1_preregistered.json",
        "marketId": MARKET_ID,
        "executionSemantics": "Canonical V7 HftBacktest + Predict Execution Tape V1 known cancel-race carrier; risk queue; 1092ms entry / 273ms response; confirmed actual fills only; R2.1 hooks default off",
        "r21ExecutionAuthority": False,
        "baseline": baseline,
        "candidate": candidate,
        "exactNonInterference": exact,
        "gates": {
            "actualCancelFillRaceReproduced": race,
            "fillDuringCancelNotificationEmitted": notified,
            "fillDuringCancelConsumedByR2Inbox": consumed,
            "cancelOwnershipAckRecomputeSafe": route_safe,
            "strictPastReceiverClean": clean,
            "responseHeadCorrectlyInactive": response_inactive,
            "exactTrajectoryNonInterference": all(exact.values()),
        },
        "waitAct": {"WAIT": 0, "ACT": 0},
        "oracleValueCeiling": None,
        "learnedPolicyRealizedValue": None,
        "decision": decision,
        "interpretation": "FILL_DURING_CANCEL is confirmed progress under an already-owned live route, not a new terminal Maker obligation. R2.1 reports and R2 consumes the fact while the V7 executor retains authority through terminal ACK and recomputation; the frozen response head should therefore remain inactive.",
        "next": "Retain market 1522232 as the cancel-race R2.1 regression fixture. Next use an already-known genuine terminal-partial Maker case, not a synthetic fill injector.",
    }
    output = OUT / "hft_r21_known_cancel_race_v7_overlay_exam_v1_report.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"decision": decision, **payload["gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
