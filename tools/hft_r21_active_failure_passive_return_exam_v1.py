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

from tools.hft_r21_cooperation_response_belief_curriculum_v1 import finite  # noqa: E402
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402
from tools.hft_r2_execution_incident_notification_exam_v1 import (  # noqa: E402
    ExecutionIncidentNotifier,
    HftIncidentBridge,
    IncidentReceiverProbe,
)
from tools.hft_r2_fault_recovery_second_action_curriculum_v1 import (  # noqa: E402
    FirstPairCompletionFault,
)
from tools.train_evaluate_hft_r21_cooperation_response_head_v1 import (  # noqa: E402
    ACTIVE,
    FEATURE_NAMES,
    MODEL_PATH as RESPONSE_MODEL_PATH,
    VERSION as RESPONSE_VERSION,
    predict_context,
)
from tools.train_hft_r21_lifecycle_belief_v1 import RecordingIncidentBridge  # noqa: E402
from tools.train_hft_r21_obligation_residual_belief_v2 import (  # noqa: E402
    FirstNMakerFaults,
    R21ObligationResidualInbox,
)

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
BELIEF_MODEL_PATH = OUT / "hft_r21_obligation_residual_belief_v2.joblib"
VERSION = "HFT_R21_ACTIVE_FAILURE_PASSIVE_RETURN_EXAM_V1"
MODEL_SHA256 = "B063E1BB83B1126F462F81A89D406383C0CF29EE7AE843EE1F0F2DF2ACF1E80C"
TAKER_FAULTS = ("SUBMIT_REJECT", "NO_FILL_STALL")
BRANCHES = {
    "WAIT": "WAIT_FOR_CLARITY",
    "PASSIVE_RETURN": "RETURN_TO_PASSIVE_REPAIR",
}
EPS = 1e-9


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def reduction(baseline: float, candidate: float) -> float:
    if abs(baseline) <= EPS:
        return 0.0 if abs(candidate) <= EPS else -math.inf
    return (baseline - candidate) / abs(baseline)


class FrozenResponseThenFallbackPolicy:
    def __init__(self, provider: R21ObligationResidualInbox, response_model: Any, fallback: str) -> None:
        self.provider = provider
        self.response_model = response_model
        self.fallback = fallback
        self.calls = 0
        self.primary_applied = False
        self.primary_anchor: dict[str, Any] | None = None
        self.primary_prediction: str | None = None
        self.primary_probability: float | None = None
        self.primary_vector: list[float] | None = None
        self.failure_state: dict[str, Any] | None = None
        self.fallback_applied = False

    def _latest_belief(self, at_ms: int) -> dict[str, Any] | None:
        candidates = [
            row
            for row in self.provider.rows
            if int(row.get("atMs") or 0) <= at_ms and finite(row.get("residualQty")) > EPS
        ]
        if not candidates:
            return None
        row = sorted(
            candidates,
            key=lambda value: (
                int(value.get("atMs") or 0),
                int(value.get("faultAtMs") or 0),
                str(value.get("obligationId") or ""),
            ),
        )[-1]
        return {
            "obligationId": str(row.get("obligationId") or ""),
            "faultType": str(row.get("faultType") or ""),
            "faultAtMs": int(row.get("faultAtMs") or 0),
            "side": str(row.get("side") or ""),
            "originalQty": finite((row.get("features") or {}).get("obligation_original_qty")),
            "progressQty": finite((row.get("features") or {}).get("obligation_progress_qty")),
            "residualQty": finite(row.get("residualQty")),
            "probability": finite(row.get("onlineModelProbability")),
            "informationOnly": True,
            "actionAuthority": False,
            "beliefAsOfMs": int(row.get("atMs") or 0),
        }

    @staticmethod
    def _active_failure(execution_state: dict[str, Any], side: str) -> bool:
        pending = (execution_state.get("pendingReplace") or {}).get(side) or {}
        owner = (execution_state.get("remainderOwner") or {}).get(side) or {}
        return (
            str(pending.get("state") or "") == "RETURN_TO_CONTROLLER_UNRESOLVED"
            or str(owner.get("state") or "") == "RETURNED_UNRESOLVED"
        )

    def __call__(self, state: dict[str, Any]) -> str:
        self.calls += 1
        at_ms = int(state.get("atMs") or 0)
        default = str(state.get("defaultAction") or "WAIT_FOR_CLARITY")
        belief = self._latest_belief(at_ms)
        if belief is None:
            return default

        if not self.primary_applied:
            anchor = {
                "atMs": at_ms,
                "side": str(state.get("side") or ""),
                "trackingError": finite(state.get("trackingError")),
                "checkpointDelayMs": int(state.get("checkpointDelayMs") or 0),
                "features": state.get("features") or {},
                "belief": belief,
            }
            anchor["stateHash"] = stable_hash(anchor)
            prediction, probability, vector = predict_context(
                self.response_model, {"anchor": anchor, "fault": "NO_FILL_STALL"}
            )
            self.primary_anchor = anchor
            self.primary_prediction = prediction
            self.primary_probability = probability
            self.primary_vector = vector
            self.primary_applied = True
            return "REPLACE_ROUTE" if prediction == ACTIVE else "WAIT_FOR_CLARITY"

        execution_state = state.get("executionState") or {}
        side = str(state.get("side") or "")
        if self._active_failure(execution_state, side):
            if self.failure_state is None:
                self.failure_state = {
                    "atMs": at_ms,
                    "side": side,
                    "trackingError": finite(state.get("trackingError")),
                    "checkpointDelayMs": int(state.get("checkpointDelayMs") or 0),
                    "features": state.get("features") or {},
                    "executionState": execution_state,
                }
                self.failure_state["stateHash"] = stable_hash(self.failure_state)
            if not self.fallback_applied:
                self.fallback_applied = True
                return BRANCHES[self.fallback]
        return "WAIT_FOR_CLARITY"


def run_branch(market_id: int, taker_fault: str, branch: str, response_model: Any) -> dict[str, Any]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    belief_artifact = joblib.load(BELIEF_MODEL_PATH)
    belief_model = belief_artifact["model"] if isinstance(belief_artifact, dict) else belief_artifact
    provider = R21ObligationResidualInbox(
        f"R21_ACTIVE_FAIL_{taker_fault}_{branch}", receiver, bridge, model=belief_model
    )
    maker_faults = FirstNMakerFaults("NO_FILL_STALL", count=3)
    taker_faults = FirstPairCompletionFault(taker_fault)
    policy = FrozenResponseThenFallbackPolicy(provider, response_model, branch)
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        maker_submit_fault_override=maker_faults,
        taker_submit_fault_override=taker_faults,
        fault_reentry_enabled=True,
        behavior_policy_override=bridge,
        behavior_ownstate_reentry=False,
        lifecycle_action_override=policy,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
        fault_no_fill_stall_ms=15_000,
        r21_incident_inbox_provider=provider,
    )
    actual = report["actualExecution"]
    portfolio = actual["finalPortfolio"]
    failure_at = int((policy.failure_state or {}).get("atMs") or 0)
    recovery_side = str((policy.failure_state or {}).get("side") or "")
    maker_fills = [
        row
        for row in report["executionLifecycleTrace"]["makerFills"]
        if int(row.get("observedAtMs") or 0) >= failure_at
        and (not recovery_side or str(row.get("side") or "") == recovery_side)
    ]
    return {
        "marketId": market_id,
        "initialMakerFault": "NO_FILL_STALL",
        "activeChildFault": taker_fault,
        "fallbackBranch": branch,
        "makerFaultsUsed": maker_faults.used,
        "activeChildFaultUsed": taker_faults.used,
        "policyCalls": policy.calls,
        "primaryPrediction": policy.primary_prediction,
        "primaryActiveProbability": policy.primary_probability,
        "primaryFeature": None
        if policy.primary_vector is None
        else dict(zip(FEATURE_NAMES, policy.primary_vector)),
        "primaryStateHash": None
        if policy.primary_anchor is None
        else policy.primary_anchor.get("stateHash"),
        "activeFailureStateHash": None
        if policy.failure_state is None
        else policy.failure_state.get("stateHash"),
        "activeFailureState": policy.failure_state,
        "fallbackApplied": policy.fallback_applied,
        "postFailureRecoverySideMakerFills": {
            "count": len(maker_fills),
            "shares": sum(float(row.get("deltaShares") or 0.0) for row in maker_fills),
        },
        "terminal": {
            "finalAbsTrackingError": finite(actual.get("finalAbsTrackingError")),
            "trackingErrorAreaShareSeconds": finite(actual.get("targetErrorAreaShareSeconds")),
            "pairedCoverage": finite(portfolio.get("combined_paired_coverage")),
            "worstCaseFloorAudit": finite(portfolio.get("worst_case_floor")),
            "realizedPnlAudit": actual.get("realizedPnl"),
        },
        "lifecycle": {
            "actionCounts": report["lifecycleAudit"]["actionCounts"],
            "ownershipEventCounts": report["lifecycleAudit"]["ownershipEventCounts"],
            "unresolvedCauseCounts": report["lifecycleAudit"]["unresolvedCauseCounts"],
            "remainderOwnershipAtEnd": report["lifecycleAudit"]["remainderOwnershipAtEnd"],
        },
        "r21": {
            "modelOutputCount": provider.model_output_count,
            "strictPastViolations": provider.strict_past_violations,
            "actionAuthority": False,
        },
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "semanticGate": report["semanticGate"],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    contexts = []
    for fault in TAKER_FAULTS:
        group = [row for row in rows if row["activeChildFault"] == fault]
        by = {row["fallbackBranch"]: row for row in group}
        if set(by) != set(BRANCHES):
            continue
        primary_hashes = {row["primaryStateHash"] for row in group if row.get("primaryStateHash")}
        failure_hashes = {
            row["activeFailureStateHash"] for row in group if row.get("activeFailureStateHash")
        }
        usable = (
            len(primary_hashes) == 1
            and len(failure_hashes) == 1
            and all(row["makerFaultsUsed"] >= 1 for row in group)
            and all(row["activeChildFaultUsed"] for row in group)
            and all(row["primaryPrediction"] == ACTIVE for row in group)
            and all(row["fallbackApplied"] for row in group)
            and all(row["cycleInvariantViolationCount"] == 0 for row in group)
            and all(not row["r21"]["strictPastViolations"] for row in group)
        )
        wait = by["WAIT"]
        candidate = by["PASSIVE_RETURN"]
        residual_reduction = reduction(
            wait["terminal"]["finalAbsTrackingError"],
            candidate["terminal"]["finalAbsTrackingError"],
        )
        area_reduction = reduction(
            wait["terminal"]["trackingErrorAreaShareSeconds"],
            candidate["terminal"]["trackingErrorAreaShareSeconds"],
        )
        ownership_returned = (
            int(
                candidate["lifecycle"]["ownershipEventCounts"].get(
                    "OWNERSHIP_RETURNED_TO_PASSIVE_REPAIR", 0
                )
            )
            > 0
        )
        passive_fill = candidate["postFailureRecoverySideMakerFills"]["shares"] > EPS
        no_harm = residual_reduction >= -EPS and area_reduction >= -EPS
        material = max(residual_reduction, area_reduction) >= 0.30 and no_harm
        contexts.append(
            {
                "marketId": wait["marketId"],
                "activeChildFault": fault,
                "usable": usable,
                "matchedPrimaryState": len(primary_hashes) == 1,
                "matchedActiveFailureState": len(failure_hashes) == 1,
                "waitTerminalResidual": wait["terminal"]["finalAbsTrackingError"],
                "passiveReturnTerminalResidual": candidate["terminal"]["finalAbsTrackingError"],
                "terminalResidualReduction": residual_reduction,
                "waitTrackingErrorArea": wait["terminal"]["trackingErrorAreaShareSeconds"],
                "passiveReturnTrackingErrorArea": candidate["terminal"][
                    "trackingErrorAreaShareSeconds"
                ],
                "trackingErrorAreaReduction": area_reduction,
                "waitPairedCoverage": wait["terminal"]["pairedCoverage"],
                "passiveReturnPairedCoverage": candidate["terminal"]["pairedCoverage"],
                "passiveReturnConfirmed": ownership_returned,
                "postFailureRecoverySideMakerFillShares": candidate[
                    "postFailureRecoverySideMakerFills"
                ]["shares"],
                "noRecoveryHarm": no_harm,
                "material30PercentRecovery": material,
                "floorAudit": {
                    "WAIT": wait["terminal"]["worstCaseFloorAudit"],
                    "PASSIVE_RETURN": candidate["terminal"]["worstCaseFloorAudit"],
                },
            }
        )
    usable = len(contexts) == len(TAKER_FAULTS) and all(row["usable"] for row in contexts)
    all_passive_fills = all(
        row["passiveReturnConfirmed"] and row["postFailureRecoverySideMakerFillShares"] > EPS
        for row in contexts
    )
    no_harm = all(row["noRecoveryHarm"] for row in contexts)
    material_families = sum(row["material30PercentRecovery"] for row in contexts)
    decision = (
        "KEEP_ACTIVE_FAILURE_OWNERSHIP_RETURN_TO_PASSIVE_REPAIR"
        if usable and all_passive_fills and no_harm and material_families >= 1
        else (
            "REJECT_ACTIVE_FAILURE_PASSIVE_RETURN_V1"
            if usable and not no_harm
            else "NEED_MORE_DATA_ACTIVE_FAILURE_PASSIVE_RETURN_V1"
        )
    )
    return {
        "contexts": contexts,
        "summary": {
            "contexts": len(contexts),
            "usableContexts": sum(row["usable"] for row in contexts),
            "waitActAtPrimary": {"WAIT": 0, "ACT": len(contexts)},
            "postActiveFailureFallback": {
                "WAIT": len(contexts),
                "PASSIVE_RETURN": len(contexts),
            },
            "confirmedPassiveReturnFamilies": sum(row["passiveReturnConfirmed"] for row in contexts),
            "confirmedPostFailurePassiveFillFamilies": sum(
                row["postFailureRecoverySideMakerFillShares"] > EPS for row in contexts
            ),
            "materialRecoveryFamilies": material_families,
            "noHarmFamilies": sum(row["noRecoveryHarm"] for row in contexts),
            "decision": decision,
        },
    }


def main() -> None:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1574038)
    parser.add_argument(
        "--output", default="hft_r21_active_failure_passive_return_exam_v1_report.json"
    )
    args = parser.parse_args()
    actual_hash = file_sha256(RESPONSE_MODEL_PATH)
    if actual_hash != MODEL_SHA256:
        raise RuntimeError(f"Frozen response model hash changed: {actual_hash}")
    response_artifact = joblib.load(RESPONSE_MODEL_PATH)
    if not isinstance(response_artifact, dict) or response_artifact.get("version") != RESPONSE_VERSION:
        raise RuntimeError("Frozen response model contract/version mismatch")
    response_model = response_artifact["model"]
    rows = []
    for fault in TAKER_FAULTS:
        for branch in BRANCHES:
            row = run_branch(args.market_id, fault, branch, response_model)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "marketId": args.market_id,
                        "activeChildFault": fault,
                        "branch": branch,
                        "primaryPrediction": row["primaryPrediction"],
                        "failureState": bool(row["activeFailureStateHash"]),
                        "fallbackApplied": row["fallbackApplied"],
                        "residual": row["terminal"]["finalAbsTrackingError"],
                        "area": row["terminal"]["trackingErrorAreaShareSeconds"],
                        "postFailureMakerShares": row[
                            "postFailureRecoverySideMakerFills"
                        ]["shares"],
                        "violations": row["cycleInvariantViolationCount"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    aggregation = summarize(rows)
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "preregistration": "hft_r21_active_failure_passive_return_exam_v1_preregistered.json",
        "frozenResponseModel": str(RESPONSE_MODEL_PATH.relative_to(ROOT)),
        "frozenResponseModelSha256": actual_hash,
        "marketId": args.market_id,
        "initialMakerFault": "NO_FILL_STALL",
        "activeChildFaults": list(TAKER_FAULTS),
        "branches": BRANCHES,
        "executionSemantics": "HftBacktest + Predict Execution Tape V1; risk queue; actual-fill-only state; 1092ms/273ms latency; 250ms own-state polling",
        "r21ExecutionAuthority": False,
        "rows": rows,
        **aggregation,
        "next": "If KEEP, verify the same frozen response head does not activate on live no-fill/partial-fill warnings that later receive actual fills. If REJECT, do not threshold-tune V1; inspect ownership transition mechanics.",
    }
    output = OUT / args.output
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
