from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_execution_incident_notification_exam_v1 import (
    ExecutionIncidentNotifier,
    FirstMakerFault,
    HftIncidentBridge,
    IncidentReceiverProbe,
    compact_execution,
)


OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R21_INFORMATION_ONLY_INCIDENT_INBOX_EXAM_V1"
INBOX_VERSION = "R2.1"
INBOX_FIELD = "r21ExecutionIncidentInbox"
FACT_FIELDS = (
    "eventId",
    "incidentType",
    "atMs",
    "orderKey",
    "role",
    "side",
    "venueState",
    "stateCertainty",
    "requestedQty",
    "confirmedFilledQty",
    "fillDeltaQty",
    "unresolvedQty",
    "submittedAtMs",
    "orderAgeMs",
    "reason",
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class R21InformationOnlyIncidentInbox:
    """Expose strict-past lifecycle facts to R2 without exposing an action API."""

    def __init__(self, receiver: IncidentReceiverProbe, max_incidents: int = 16) -> None:
        self.receiver = receiver
        self.max_incidents = int(max_incidents)
        self.calls = 0
        self.nonempty_calls = 0
        self.visible_event_ids: set[str] = set()
        self.visible_incident_types: Counter[str] = Counter()
        self.strict_past_violations: list[dict[str, Any]] = []
        self.schema_errors: list[dict[str, Any]] = []

    def _fact(self, event: dict[str, Any]) -> dict[str, Any]:
        # Deliberately omit receiverDirective and ownershipDirective. R2.1 gets
        # lifecycle facts and must decide for itself; the inbox never prescribes
        # an execution response.
        return {key: copy.deepcopy(event.get(key)) for key in FACT_FIELDS}

    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        at_ms = int(snapshot["sampledAtMs"])
        visible = sorted(
            (
                self._fact(event)
                for event in self.receiver.notifications
                if int(event.get("atMs") or 0) <= at_ms
            ),
            key=lambda event: int(event.get("atMs") or 0),
        )
        incidents = visible[-self.max_incidents :]
        future = [event for event in incidents if int(event.get("atMs") or 0) > at_ms]
        if future:
            self.strict_past_violations.extend(copy.deepcopy(future))
        if incidents:
            self.nonempty_calls += 1
        for event in incidents:
            event_id = str(event.get("eventId") or "")
            incident_type = str(event.get("incidentType") or "UNKNOWN")
            self.visible_event_ids.add(event_id)
            self.visible_incident_types[incident_type] += 1

        payload = {
            "version": INBOX_VERSION,
            "contractVersion": VERSION,
            "mode": "INFORMATION_ONLY",
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "asOfMs": at_ms,
            "totalIncidentCount": len(visible),
            "latestEventId": None if not visible else visible[-1]["eventId"],
            "incidents": incidents,
        }
        if payload["actionAuthority"] is not False or payload["mode"] != "INFORMATION_ONLY":
            self.schema_errors.append({"atMs": at_ms, "error": "ACTION_AUTHORITY_PRESENT"})
        return payload


def decision_comparison(baseline: dict[str, Any], r21: dict[str, Any]) -> dict[str, Any]:
    baseline_rows = baseline["controller"]["decisions"]
    r21_rows = r21["controller"]["decisions"]
    baseline_encoded = [canonical(row) for row in baseline_rows]
    r21_encoded = [canonical(row) for row in r21_rows]
    mismatch_indices = [
        index
        for index in range(max(len(baseline_encoded), len(r21_encoded)))
        if index >= len(baseline_encoded)
        or index >= len(r21_encoded)
        or baseline_encoded[index] != r21_encoded[index]
    ]
    return {
        "baselineDecisionCount": len(baseline_rows),
        "r21DecisionCount": len(r21_rows),
        "baselineDigest": digest(baseline_rows),
        "r21Digest": digest(r21_rows),
        "mismatchCount": len(mismatch_indices),
        "firstMismatchIndices": mismatch_indices[:5],
        "exactPass": not mismatch_indices,
    }


def lifecycle_comparison(baseline: dict[str, Any], r21: dict[str, Any]) -> dict[str, Any]:
    baseline_trace = baseline["executionLifecycleTrace"]
    r21_trace = r21["executionLifecycleTrace"]
    return {
        "baselineDigest": digest(baseline_trace),
        "r21Digest": digest(r21_trace),
        "exactPass": canonical(baseline_trace) == canonical(r21_trace),
    }


def run_case(market_id: int, case: str, r21_enabled: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = HftIncidentBridge(notifier)
    inbox = R21InformationOnlyIncidentInbox(receiver)
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
        behavior_policy_override=bridge if r21_enabled else None,
        behavior_ownstate_reentry=False,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
        fault_no_fill_stall_ms=15_000 if case == "MAKER_LONG_NO_FILL" else None,
        r21_incident_inbox_provider=inbox if r21_enabled else None,
    )
    generated_types = Counter(str(row["incidentType"]) for row in receiver.notifications)
    delivered = report["r21InformationInbox"]
    summary = {
        "marketId": int(market_id),
        "case": case,
        "r21Enabled": bool(r21_enabled),
        "faultUsed": None if fault is None else bool(fault.used),
        "runtimeSeconds": report["runtimeSeconds"],
        "execution": compact_execution(report),
        "controllerDecisionDigest": digest(report["controller"]["decisions"]),
        "lifecycleDigest": digest(report["executionLifecycleTrace"]),
        "receiver": {
            "bridgeCalls": bridge.calls if r21_enabled else 0,
            "generatedNotifications": len(receiver.notifications),
            "generatedIncidentTypeCounts": dict(sorted(generated_types.items())),
            "receiverSchemaErrors": receiver.schema_errors,
        },
        "inbox": {
            "field": INBOX_FIELD if r21_enabled else None,
            "calls": inbox.calls if r21_enabled else 0,
            "nonEmptyCalls": inbox.nonempty_calls if r21_enabled else 0,
            "uniqueVisibleEvents": len(inbox.visible_event_ids) if r21_enabled else 0,
            "visibleIncidentTypes": sorted(inbox.visible_incident_types) if r21_enabled else [],
            "strictPastViolations": inbox.strict_past_violations if r21_enabled else [],
            "schemaErrors": inbox.schema_errors if r21_enabled else [],
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "controllerConsumedSnapshots": delivered["consumedSnapshotCount"],
            "controllerConsumedNonEmptySnapshots": delivered["nonEmptyConsumedSnapshotCount"],
            "controllerConsumedIncidentTypes": delivered["consumedIncidentTypes"],
        },
    }
    return report, summary


def main() -> None:
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1569361)
    parser.add_argument("--output", default="hft_r21_information_only_incident_inbox_exam_market1569361_v1_report.json")
    args = parser.parse_args()

    required_types = {
        "NATURAL_EXECUTION": {"LIVE_NO_FILL_STALL", "LATE_FILL_AFTER_DELAY"},
        "MAKER_SUBMIT_REJECT": {"SUBMIT_REJECT_CONFIRMED"},
        "MAKER_LONG_NO_FILL": {"LIVE_NO_FILL_STALL", "TERMINAL_ZERO_FILL_CONFIRMED"},
    }
    cases: list[dict[str, Any]] = []
    for case, required in required_types.items():
        baseline_report, baseline = run_case(args.market_id, case, False)
        r21_report, r21 = run_case(args.market_id, case, True)
        decision_gate = decision_comparison(baseline_report, r21_report)
        lifecycle_gate = lifecycle_comparison(baseline_report, r21_report)
        execution_exact = canonical(baseline["execution"]) == canonical(r21["execution"])
        visible_types = set(r21["inbox"]["controllerConsumedIncidentTypes"])
        row = {
            "case": case,
            "requiredVisibleIncidentTypes": sorted(required),
            "actualVisibleIncidentTypes": sorted(visible_types),
            "notificationVisibleToControllerPass": required.issubset(visible_types),
            "strictPastPass": not r21["inbox"]["strictPastViolations"],
            "zeroInboxSchemaErrors": not r21["inbox"]["schemaErrors"],
            "zeroReceiverSchemaErrors": not r21["receiver"]["receiverSchemaErrors"],
            "zeroActionAuthority": (
                r21["inbox"]["actionAuthority"] is False
                and r21["inbox"]["orderMutationAuthority"] is False
                and r21["inbox"]["desiredPortfolioMutationAuthority"] is False
            ),
            "perDecisionExactNonInterference": decision_gate,
            "lifecycleExactNonInterference": lifecycle_gate,
            "terminalExecutionExactNonInterference": execution_exact,
            "baseline": baseline,
            "r21": r21,
        }
        row["pass"] = bool(
            row["notificationVisibleToControllerPass"]
            and row["strictPastPass"]
            and row["zeroInboxSchemaErrors"]
            and row["zeroReceiverSchemaErrors"]
            and row["zeroActionAuthority"]
            and decision_gate["exactPass"]
            and lifecycle_gate["exactPass"]
            and execution_exact
        )
        cases.append(row)
        print(
            json.dumps(
                {
                    "case": case,
                    "pass": row["pass"],
                    "visibleTypes": row["actualVisibleIncidentTypes"],
                    "decisionExact": decision_gate["exactPass"],
                    "lifecycleExact": lifecycle_gate["exactPass"],
                    "executionExact": execution_exact,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    all_pass = all(row["pass"] for row in cases)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "name": "R2.1",
        "hypothesis": "Strict-past execution incidents can be delivered through R2's existing snapshot receiver without changing any Frozen R2 decision or execution lifecycle.",
        "dedupBoundary": "This does not re-test an incident classifier or repair policy. It tests the previously missing connection from classified facts into the actual R2 controller input, with per-decision non-interference as the falsification gate.",
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "incidentTiming": "STRICT_PAST_PREVIOUSLY_OBSERVED_LIFECYCLE_FACTS",
        },
        "contract": {
            "field": INBOX_FIELD,
            "mode": "INFORMATION_ONLY",
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "eventFields": list(FACT_FIELDS),
            "excludedActionLikeFields": ["receiverDirective", "ownershipDirective"],
            "frozenR2SemanticsChanged": False,
        },
        "cohort": {"markets": [int(args.market_id)], "cases": list(required_types)},
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "passed": sum(int(row["pass"]) for row in cases),
            "perDecisionExactPasses": sum(int(row["perDecisionExactNonInterference"]["exactPass"]) for row in cases),
            "lifecycleExactPasses": sum(int(row["lifecycleExactNonInterference"]["exactPass"]) for row in cases),
            "terminalExecutionExactPasses": sum(int(row["terminalExecutionExactNonInterference"]) for row in cases),
            "allPass": all_pass,
        },
        "decision": "KEEP_R21_INFORMATION_ONLY_INCIDENT_INBOX" if all_pass else "REJECT_R21_INBOX_CONTRACT",
        "next": "Use this immutable inbox contract in a separate research-only response-learning exam. Any learned R2.1 candidate must still have no direct executor API and must graduate on fault-matched passive plus bounded-active recovery before economic tuning.",
    }
    output = OUT / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": all_pass, "report": str(output), "summary": report["summary"], "decision": report["decision"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
