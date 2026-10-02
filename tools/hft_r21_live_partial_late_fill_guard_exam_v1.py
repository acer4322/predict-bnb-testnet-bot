from __future__ import annotations

import argparse
import hashlib
import json
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
    MODEL_PATH as RESPONSE_MODEL_PATH,
    VERSION as RESPONSE_VERSION,
)
from tools.train_hft_r21_lifecycle_belief_v1 import RecordingIncidentBridge  # noqa: E402
from tools.train_hft_r21_obligation_residual_belief_v2 import (  # noqa: E402
    R21ObligationResidualInbox,
)

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
BELIEF_MODEL_PATH = OUT / "hft_r21_obligation_residual_belief_v2.joblib"
VERSION = "HFT_R21_LIVE_PARTIAL_LATE_FILL_GUARD_EXAM_V1"


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def incident_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("incidentType") or "NONE")
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_natural(market_id: int, use_response_head: bool, response_model: Any) -> dict[str, Any]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    belief_artifact = joblib.load(BELIEF_MODEL_PATH)
    belief_model = belief_artifact["model"] if isinstance(belief_artifact, dict) else belief_artifact
    provider = R21ObligationResidualInbox(
        f"R21_LIVE_GUARD_{'HEAD' if use_response_head else 'BASELINE'}",
        receiver,
        bridge,
        model=belief_model,
    )
    policy = (
        FrozenResponseThenFallbackPolicy(provider, response_model, "WAIT")
        if use_response_head
        else None
    )
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        behavior_policy_override=bridge,
        behavior_ownstate_reentry=False,
        lifecycle_action_override=policy,
        trace_execution_states=True,
        r21_incident_inbox_provider=provider,
    )
    notifications = list(receiver.notifications)
    counts = incident_counts(notifications)
    return {
        "branch": "FROZEN_RESPONSE_HEAD" if use_response_head else "R21_INFORMATION_ONLY_BASELINE",
        "incidentCounts": counts,
        "partialIncidentCount": sum(
            counts.get(key, 0)
            for key in ("PARTIAL_FILL_CONFIRMED", "LIVE_PARTIAL_FILL_STALL")
        ),
        "lateFillIncidentCount": counts.get("LATE_FILL_AFTER_DELAY", 0),
        "terminalMakerFaultIncidentCount": sum(
            counts.get(key, 0)
            for key in (
                "SUBMIT_REJECT_CONFIRMED",
                "TERMINAL_ZERO_FILL_CONFIRMED",
                "TERMINAL_PARTIAL_FILL_CONFIRMED",
            )
        ),
        "obligationLedgers": len(provider.ledgers),
        "beliefRows": len(provider.rows),
        "responsePolicyCalls": 0 if policy is None else policy.calls,
        "responseHeadApplied": False if policy is None else policy.primary_applied,
        "responsePrimaryPrediction": None if policy is None else policy.primary_prediction,
        "actualExecutionHash": stable_hash(report["actualExecution"]),
        "controllerDecisionHash": stable_hash(report["controller"]["decisions"]),
        "makerLifecycleHash": stable_hash(
            {
                "submits": report["executionLifecycleTrace"]["makerSubmits"],
                "fills": report["executionLifecycleTrace"]["makerFills"],
            }
        ),
        "takerLifecycleHash": stable_hash(
            {
                "attempts": report["executionLifecycleTrace"]["takerAttempts"],
                "fills": report["executionLifecycleTrace"]["takerFills"],
            }
        ),
        "terminal": {
            "trackingErrorAreaShareSeconds": report["actualExecution"].get(
                "targetErrorAreaShareSeconds"
            ),
            "finalAbsTrackingError": report["actualExecution"].get("finalAbsTrackingError"),
            "worstCaseFloorAudit": report["actualExecution"]["finalPortfolio"].get(
                "worst_case_floor"
            ),
        },
        "strictPastViolations": provider.strict_past_violations,
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "semanticGate": report["semanticGate"],
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1569361)
    parser.add_argument(
        "--output", default="hft_r21_live_partial_late_fill_guard_exam_v1_report.json"
    )
    args = parser.parse_args()
    actual_hash = file_sha256(RESPONSE_MODEL_PATH)
    if actual_hash != MODEL_SHA256:
        raise RuntimeError(f"Frozen response model hash changed: {actual_hash}")
    response_artifact = joblib.load(RESPONSE_MODEL_PATH)
    if not isinstance(response_artifact, dict) or response_artifact.get("version") != RESPONSE_VERSION:
        raise RuntimeError("Frozen response model contract/version mismatch")
    response_model = response_artifact["model"]
    baseline = run_natural(args.market_id, False, response_model)
    candidate = run_natural(args.market_id, True, response_model)
    exact = {
        "actualExecution": baseline["actualExecutionHash"] == candidate["actualExecutionHash"],
        "controllerDecisions": baseline["controllerDecisionHash"]
        == candidate["controllerDecisionHash"],
        "makerLifecycle": baseline["makerLifecycleHash"] == candidate["makerLifecycleHash"],
        "takerLifecycle": baseline["takerLifecycleHash"] == candidate["takerLifecycleHash"],
    }
    required_events = (
        candidate["partialIncidentCount"] > 0 and candidate["lateFillIncidentCount"] > 0
    )
    no_terminal_obligation = (
        candidate["terminalMakerFaultIncidentCount"] == 0
        and candidate["obligationLedgers"] == 0
    )
    no_response = not candidate["responseHeadApplied"]
    safety = (
        all(exact.values())
        and required_events
        and no_terminal_obligation
        and no_response
        and not candidate["strictPastViolations"]
        and candidate["cycleInvariantViolationCount"] == 0
    )
    decision = (
        "KEEP_LIVE_PARTIAL_LATE_FILL_TERMINALITY_GUARD"
        if safety
        else "REJECT_OR_NEED_NEW_HFT_PARTIAL_LATE_FILL_MARKET"
    )
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "hft_r21_live_partial_late_fill_guard_exam_v1_preregistered.json",
        "marketId": args.market_id,
        "frozenResponseModelSha256": actual_hash,
        "executionSemantics": "Natural HftBacktest + Predict Execution Tape V1 queue/latency/partial-fill/late-fill lifecycle; no fault injection",
        "r21ExecutionAuthority": False,
        "baseline": baseline,
        "candidate": candidate,
        "exactNonInterference": exact,
        "gates": {
            "naturalPartialAndLateFillObserved": required_events,
            "noTerminalMakerObligation": no_terminal_obligation,
            "responseHeadDidNotActivate": no_response,
            "strictPastAndCycleClean": not candidate["strictPastViolations"]
            and candidate["cycleInvariantViolationCount"] == 0,
        },
        "decision": decision,
        "interpretation": "Live stall/partial/late-fill evidence remains owned by the existing child until terminal confirmation. The frozen response head must not create a repair obligation or select ACTIVE from warning events alone.",
        "next": "Build a true HftBacktest partial-terminal-remainder injector before training any partial-failure response; do not reuse deterministic structural arithmetic as performance evidence.",
    }
    output = OUT / args.output
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "marketId": args.market_id,
                "partialIncidents": candidate["partialIncidentCount"],
                "lateFillIncidents": candidate["lateFillIncidentCount"],
                "terminalMakerFaults": candidate["terminalMakerFaultIncidentCount"],
                "responseHeadApplied": candidate["responseHeadApplied"],
                "exactNonInterference": exact,
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
