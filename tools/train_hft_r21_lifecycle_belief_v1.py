from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


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
    finite,
)
from tools.hft_r21_information_only_incident_inbox_exam_v1 import (
    R21InformationOnlyIncidentInbox,
    canonical,
    digest,
)


OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R21_LIFECYCLE_BELIEF_PILOT_V1"
PREREGISTRATION = "hft_r21_lifecycle_belief_pilot_v1_preregistered.json"
REFERENCE = OUT / "hft_r21_information_only_incident_inbox_exam_market1569361_v1_report.json"
HORIZON_MS = 15_000
SEED = 20260824
EPS = 1e-8
CASES = ("NATURAL_EXECUTION", "MAKER_SUBMIT_REJECT", "MAKER_LONG_NO_FILL")

FEATURES = (
    "seconds_left",
    "direction_score",
    "spot_return_1s_bps",
    "spot_queue_imbalance",
    "futures_return_1s_bps",
    "futures_queue_imbalance",
    "tracking_error",
    "abs_tracking_error",
    "target_net",
    "actual_net",
    "desired_recovery_shares",
    "actual_recovery_shares",
    "combined_paired_coverage",
    "worst_case_floor",
    "maker_fills_1s",
    "maker_fills_5s",
    "maker_shares_5s",
    "recovery_child_exists",
    "recovery_child_age_ms",
    "recovery_child_price",
    "recovery_child_requested_qty",
    "recovery_child_filled_qty",
    "recovery_child_leaves_qty",
    "recovery_child_partial",
    "recovery_child_cancel_pending",
    "recovery_book_bid",
    "recovery_book_ask",
    "recovery_book_spread_ticks",
    "recovery_book_bid_depth",
    "recovery_book_ask_depth",
    "recovery_book_top3_bid_depth",
    "recovery_route_blocked",
    "open_taker_children",
    "responsibility_token_count",
    "responsibility_remaining_shares",
    "target_revision_recovery",
    "incident_count_30s",
    "incident_live_delay_30s",
    "incident_live_stall_30s",
    "incident_partial_fill_30s",
    "incident_late_fill_30s",
    "incident_terminal_zero_30s",
    "incident_submit_reject_30s",
    "latest_incident_age_ms",
    "consecutive_reject",
    "consecutive_no_fill",
    "consecutive_partial",
    "same_route_retry_count",
)


class RecordingIncidentBridge:
    def __init__(self, bridge: HftIncidentBridge) -> None:
        self.bridge = bridge
        self.latest_payload: dict[str, Any] | None = None
        self.calls = 0

    def __call__(self, payload: dict[str, Any]) -> None:
        self.calls += 1
        self.bridge(payload)
        self.latest_payload = copy.deepcopy(payload)
        return None


def _feature_map(
    snapshot: dict[str, Any],
    latest_payload: dict[str, Any] | None,
    notifications: list[dict[str, Any]],
) -> tuple[str | None, dict[str, float] | None, int | None]:
    if latest_payload is None:
        return None, None, None
    at_ms = int(snapshot["sampledAtMs"])
    state_as_of = int(latest_payload.get("atMs") or 0)
    if state_as_of > at_ms:
        return None, None, state_as_of
    state = latest_payload.get("executionState") or {}
    tracking_error = finite(state.get("trackingError"))
    if abs(tracking_error) <= EPS:
        return None, None, state_as_of
    recovery_side = "UP" if tracking_error < 0.0 else "DOWN"
    side_lower = recovery_side.lower()
    public = state.get("publicState") or snapshot
    book = state.get("outcomeBook") or {}
    portfolio = state.get("actualPortfolio") or {}
    desired = latest_payload.get("desiredPortfolio") or {}
    actual = state.get("actualSharesBySide") or {}
    child = (state.get("activeMakerChildren") or {}).get(recovery_side) or {}
    submitted_at = int(child.get("submittedAtMs") or state_as_of)
    requested = finite(child.get("requestedQty"))
    filled = finite(child.get("cumExecQty"), finite(child.get("filledQty")))
    leaves = finite(child.get("leavesQty"), max(0.0, requested - filled))
    recent_incidents = [
        event
        for event in notifications
        if int(event.get("atMs") or 0) <= at_ms
        and at_ms - int(event.get("atMs") or 0) <= 30_000
    ]
    incident_counts = Counter(str(event.get("incidentType") or "UNKNOWN") for event in recent_incidents)
    latest_incident_at = max((int(event.get("atMs") or 0) for event in recent_incidents), default=at_ms)
    memory = state.get("behaviorMemory") or {}
    tokens = list(state.get("responsibilityTokens") or [])
    feature_map = {
        "seconds_left": finite(public.get("secondsLeft")),
        "direction_score": finite(public.get("directionScore")),
        "spot_return_1s_bps": finite(public.get("spotReturn1sBps")),
        "spot_queue_imbalance": finite(public.get("spotQueueImbalance")),
        "futures_return_1s_bps": finite(public.get("futuresReturn1sBps")),
        "futures_queue_imbalance": finite(public.get("futuresQueueImbalance")),
        "tracking_error": tracking_error,
        "abs_tracking_error": abs(tracking_error),
        "target_net": finite(state.get("targetNet")),
        "actual_net": finite(state.get("actualNet")),
        "desired_recovery_shares": finite(desired.get(recovery_side)),
        "actual_recovery_shares": finite(actual.get(recovery_side)),
        "combined_paired_coverage": finite(portfolio.get("combined_paired_coverage")),
        "worst_case_floor": finite(portfolio.get("worst_case_floor")),
        "maker_fills_1s": finite(portfolio.get("maker_fills_1s")),
        "maker_fills_5s": finite(portfolio.get("maker_fills_5s")),
        "maker_shares_5s": finite(portfolio.get("maker_shares_5s")),
        "recovery_child_exists": float(bool(child)),
        "recovery_child_age_ms": float(max(0, state_as_of - submitted_at)) if child else 0.0,
        "recovery_child_price": finite(child.get("price")),
        "recovery_child_requested_qty": requested,
        "recovery_child_filled_qty": filled,
        "recovery_child_leaves_qty": leaves,
        "recovery_child_partial": float(bool(child) and filled > EPS and leaves > EPS),
        "recovery_child_cancel_pending": float(bool(child.get("cancelPending") or child.get("cancelRequested"))),
        "recovery_book_bid": finite(book.get(f"{side_lower}_bid")),
        "recovery_book_ask": finite(book.get(f"{side_lower}_ask")),
        "recovery_book_spread_ticks": finite(book.get(f"{side_lower}_spread_ticks")),
        "recovery_book_bid_depth": finite(book.get(f"{side_lower}_bid_depth")),
        "recovery_book_ask_depth": finite(book.get(f"{side_lower}_ask_depth")),
        "recovery_book_top3_bid_depth": finite(book.get(f"{side_lower}_top3_bid_depth")),
        "recovery_route_blocked": float(bool((state.get("routeBlock") or {}).get(recovery_side))),
        "open_taker_children": float(len(state.get("openTakerChildren") or [])),
        "responsibility_token_count": float(len(tokens)),
        "responsibility_remaining_shares": sum(finite(token.get("remainingShares")) for token in tokens),
        "target_revision_recovery": finite((state.get("targetRevision") or {}).get(recovery_side)),
        "incident_count_30s": float(len(recent_incidents)),
        "incident_live_delay_30s": float(incident_counts.get("LIVE_NO_FILL_DELAY", 0)),
        "incident_live_stall_30s": float(incident_counts.get("LIVE_NO_FILL_STALL", 0)),
        "incident_partial_fill_30s": float(incident_counts.get("PARTIAL_FILL_CONFIRMED", 0)),
        "incident_late_fill_30s": float(incident_counts.get("LATE_FILL_AFTER_DELAY", 0)),
        "incident_terminal_zero_30s": float(incident_counts.get("TERMINAL_ZERO_FILL_CONFIRMED", 0)),
        "incident_submit_reject_30s": float(incident_counts.get("SUBMIT_REJECT_CONFIRMED", 0)),
        "latest_incident_age_ms": float(max(0, at_ms - latest_incident_at)) if recent_incidents else 30_000.0,
        "consecutive_reject": finite(memory.get("consecutiveReject")),
        "consecutive_no_fill": finite(memory.get("consecutiveNoFill")),
        "consecutive_partial": finite(memory.get("consecutivePartial")),
        "same_route_retry_count": finite(memory.get("sameRouteRetryCount")),
    }
    return recovery_side, feature_map, state_as_of


class R21LifecycleBeliefInbox:
    def __init__(
        self,
        case: str,
        receiver: IncidentReceiverProbe,
        recording_bridge: RecordingIncidentBridge,
        model: Pipeline | None = None,
    ) -> None:
        self.case = str(case)
        self.receiver = receiver
        self.recording_bridge = recording_bridge
        self.model = model
        self.base = R21InformationOnlyIncidentInbox(receiver)
        self.rows: list[dict[str, Any]] = []
        self.seen_buckets: set[tuple[int, str]] = set()
        self.strict_past_violations: list[dict[str, Any]] = []
        self.model_output_count = 0

    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = self.base(snapshot)
        recovery_side, feature_map, state_as_of = _feature_map(
            snapshot,
            self.recording_bridge.latest_payload,
            self.receiver.notifications,
        )
        at_ms = int(snapshot["sampledAtMs"])
        if state_as_of is not None and state_as_of > at_ms:
            self.strict_past_violations.append({"snapshotAtMs": at_ms, "stateAsOfMs": state_as_of})
        if recovery_side is None or feature_map is None or state_as_of is None:
            return payload
        bucket = (at_ms // 1_000, recovery_side)
        probability: float | None = None
        if self.model is not None:
            values = np.asarray([[feature_map[name] for name in FEATURES]], dtype=float)
            probability = float(self.model.predict_proba(values)[0, 1])
            payload["lifecycleBelief"] = {
                "version": VERSION,
                "asOfMs": at_ms,
                "executionStateAsOfMs": state_as_of,
                "recoverySide": recovery_side,
                "confirmedPassiveRecoveryFillProbability15s": probability,
                "informationOnly": True,
                "actionRecommendation": None,
            }
            self.model_output_count += 1
        if bucket not in self.seen_buckets:
            self.seen_buckets.add(bucket)
            self.rows.append(
                {
                    "case": self.case,
                    "atMs": at_ms,
                    "executionStateAsOfMs": state_as_of,
                    "recoverySide": recovery_side,
                    "features": feature_map,
                    "onlineModelProbability": probability,
                    "strictPastPass": state_as_of <= at_ms,
                }
            )
        return payload


def run_trajectory(market_id: int, case: str, model: Pipeline | None) -> tuple[dict[str, Any], R21LifecycleBeliefInbox, RecordingIncidentBridge]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    recording_bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    provider = R21LifecycleBeliefInbox(case, receiver, recording_bridge, model=model)
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
        behavior_policy_override=recording_bridge,
        behavior_ownstate_reentry=False,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=False,
        fault_no_fill_stall_ms=15_000 if case == "MAKER_LONG_NO_FILL" else None,
        r21_incident_inbox_provider=provider,
    )
    return report, provider, recording_bridge


def label_rows(rows: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    if not rows:
        return []
    final_at = max(int(trace["atMs"]) for trace in report["r21InformationInbox"]["traces"])
    fills = report["executionLifecycleTrace"]["makerFills"]
    labelled = []
    for source in rows:
        row = copy.deepcopy(source)
        at_ms = int(row["atMs"])
        if at_ms + HORIZON_MS > final_at:
            row["censored"] = True
            labelled.append(row)
            continue
        future = [
            fill
            for fill in fills
            if str(fill.get("side")) == row["recoverySide"]
            and at_ms < int(fill.get("observedAtMs") or 0) <= at_ms + HORIZON_MS
        ]
        shares = sum(finite(fill.get("deltaShares"), finite(fill.get("shares"))) for fill in future)
        row.update(
            {
                "censored": False,
                "labelConfirmedRecoveryMakerFill15s": int(shares > EPS),
                "labelConfirmedRecoveryMakerShares15s": shares,
                "labelFirstFillDelayMs": None
                if not future
                else min(int(fill["observedAtMs"]) for fill in future) - at_ms,
            }
        )
        labelled.append(row)
    return labelled


def matrices(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    usable = [row for row in rows if not row.get("censored")]
    x = np.asarray([[finite(row["features"].get(name)) for name in FEATURES] for row in usable], dtype=float)
    y = np.asarray([int(row["labelConfirmedRecoveryMakerFill15s"]) for row in usable], dtype=int)
    return x, y


def evaluate(rows: list[dict[str, Any]], model: Pipeline, training_prevalence: float) -> dict[str, Any]:
    usable = [row for row in rows if not row.get("censored")]
    x, y = matrices(usable)
    probabilities = model.predict_proba(x)[:, 1]
    for row, probability in zip(usable, probabilities, strict=True):
        row["offlineModelProbability"] = float(probability)
    prevalence = float(np.mean(y)) if len(y) else 0.0
    both_classes = len(set(y.tolist())) == 2
    ap = float(average_precision_score(y, probabilities)) if len(y) else 0.0
    auc = float(roc_auc_score(y, probabilities)) if both_classes else None
    brier = float(brier_score_loss(y, probabilities)) if len(y) else None
    baseline_brier = float(np.mean((y - training_prevalence) ** 2)) if len(y) else None
    top_count = max(1, int(math.ceil(len(y) * 0.20))) if len(y) else 0
    order = np.argsort(-probabilities)[:top_count]
    top_rate = float(np.mean(y[order])) if top_count else 0.0
    return {
        "samples": len(usable),
        "censored": sum(int(row.get("censored", False)) for row in rows),
        "positives": int(np.sum(y)),
        "negatives": int(len(y) - np.sum(y)),
        "prevalence": prevalence,
        "bothClasses": both_classes,
        "averagePrecision": ap,
        "averagePrecisionLiftVsPrevalence": ap / prevalence if prevalence > 0.0 else 0.0,
        "rocAucDiagnostic": auc,
        "brier": brier,
        "trainingPrevalenceConstantBrier": baseline_brier,
        "brierBeatsTrainingPrevalenceConstant": bool(brier is not None and baseline_brier is not None and brier < baseline_brier),
        "topQuintileSamples": top_count,
        "topQuintilePositiveRate": top_rate,
        "topQuintileLift": top_rate / prevalence if prevalence > 0.0 else 0.0,
    }


def reference_by_case() -> dict[str, dict[str, Any]]:
    report = json.loads(REFERENCE.read_text(encoding="utf-8"))
    return {str(row["case"]): row["baseline"] for row in report["cases"]}


def trajectory_audit(
    case: str,
    report: dict[str, Any],
    provider: R21LifecycleBeliefInbox,
    recording_bridge: RecordingIncidentBridge,
    reference: dict[str, Any],
) -> dict[str, Any]:
    decision_digest = digest(report["controller"]["decisions"])
    lifecycle_digest = digest(report["executionLifecycleTrace"])
    execution = compact_execution(report)
    return {
        "case": case,
        "runtimeSeconds": float(report["runtimeSeconds"]),
        "recordedSnapshots": len(provider.rows),
        "modelOutputs": provider.model_output_count,
        "bridgeCalls": recording_bridge.calls,
        "strictPastViolations": provider.strict_past_violations,
        "inboxSchemaErrors": provider.base.schema_errors,
        "receiverSchemaErrors": provider.receiver.schema_errors,
        "decisionDigest": decision_digest,
        "referenceDecisionDigest": reference["controllerDecisionDigest"],
        "perDecisionExactNonInterference": decision_digest == reference["controllerDecisionDigest"],
        "lifecycleDigest": lifecycle_digest,
        "referenceLifecycleDigest": reference["lifecycleDigest"],
        "lifecycleExactNonInterference": lifecycle_digest == reference["lifecycleDigest"],
        "execution": execution,
        "terminalExecutionExactNonInterference": canonical(execution) == canonical(reference["execution"]),
        "actionAuthority": False,
        "orderMutationAuthority": False,
        "desiredPortfolioMutationAuthority": False,
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1569361)
    parser.add_argument("--output", default="hft_r21_lifecycle_belief_pilot_market1569361_v1_report.json")
    parser.add_argument("--dataset-output", default="hft_r21_lifecycle_belief_pilot_market1569361_v1_dataset.json")
    parser.add_argument("--model-output", default="hft_r21_lifecycle_belief_pilot_market1569361_v1.joblib")
    args = parser.parse_args()

    reference = reference_by_case()
    reports: dict[str, dict[str, Any]] = {}
    providers: dict[str, R21LifecycleBeliefInbox] = {}
    bridges: dict[str, RecordingIncidentBridge] = {}

    print(json.dumps({"stage": "train_trajectory", "case": "NATURAL_EXECUTION"}), flush=True)
    train_report, train_provider, train_bridge = run_trajectory(args.market_id, "NATURAL_EXECUTION", None)
    reports["NATURAL_EXECUTION"] = train_report
    providers["NATURAL_EXECUTION"] = train_provider
    bridges["NATURAL_EXECUTION"] = train_bridge
    train_rows = label_rows(train_provider.rows, train_report)
    train_x, train_y = matrices(train_rows)
    train_both_classes = len(set(train_y.tolist())) == 2
    model: Pipeline | None = None
    if train_both_classes:
        model = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                (
                    "logistic",
                    LogisticRegression(
                        C=1.0,
                        class_weight="balanced",
                        max_iter=1_000,
                        random_state=SEED,
                        solver="liblinear",
                    ),
                ),
            ]
        )
        model.fit(train_x, train_y)

    for case in ("MAKER_SUBMIT_REJECT", "MAKER_LONG_NO_FILL"):
        print(json.dumps({"stage": "unseen_fault_validation_trajectory", "case": case, "modelAvailable": model is not None}), flush=True)
        report, provider, bridge = run_trajectory(args.market_id, case, model)
        reports[case] = report
        providers[case] = provider
        bridges[case] = bridge

    labelled_by_case = {
        case: label_rows(providers[case].rows, reports[case]) for case in CASES
    }
    usable_train = [row for row in train_rows if not row.get("censored")]
    training_prevalence = float(np.mean(train_y)) if len(train_y) else 0.0
    train_metrics = evaluate(train_rows, model, training_prevalence) if model is not None else None
    validation_rows = labelled_by_case["MAKER_SUBMIT_REJECT"] + labelled_by_case["MAKER_LONG_NO_FILL"]
    validation_metrics = evaluate(validation_rows, model, training_prevalence) if model is not None else None
    validation_by_case = {
        case: evaluate(labelled_by_case[case], model, training_prevalence) if model is not None else None
        for case in ("MAKER_SUBMIT_REJECT", "MAKER_LONG_NO_FILL")
    }

    audits = {
        case: trajectory_audit(case, reports[case], providers[case], bridges[case], reference[case])
        for case in CASES
    }
    non_interference = all(
        audit["perDecisionExactNonInterference"]
        and audit["lifecycleExactNonInterference"]
        and audit["terminalExecutionExactNonInterference"]
        and not audit["strictPastViolations"]
        and not audit["inboxSchemaErrors"]
        and not audit["receiverSchemaErrors"]
        and audit["cycleInvariantViolationCount"] == 0
        for audit in audits.values()
    )
    gate = bool(
        model is not None
        and validation_metrics is not None
        and validation_metrics["bothClasses"]
        and validation_metrics["averagePrecisionLiftVsPrevalence"] >= 1.25
        and validation_metrics["topQuintileLift"] >= 1.5
        and validation_metrics["brierBeatsTrainingPrevalenceConstant"]
        and non_interference
    )

    coefficients: list[dict[str, Any]] = []
    if model is not None:
        raw = model.named_steps["logistic"].coef_[0]
        coefficients = [
            {"feature": name, "coefficient": float(value)}
            for name, value in sorted(zip(FEATURES, raw, strict=True), key=lambda pair: abs(pair[1]), reverse=True)
        ]
        artifact = {
            "version": VERSION,
            "researchOnly": True,
            "informationOnly": True,
            "actionAuthority": False,
            "features": list(FEATURES),
            "horizonMs": HORIZON_MS,
            "model": model,
            "trainedMarketIds": [int(args.market_id)],
            "trainedTrajectory": "NATURAL_EXECUTION",
            "validationTrajectories": ["MAKER_SUBMIT_REJECT", "MAKER_LONG_NO_FILL"],
            "gatePass": gate,
            "promotionEligible": False,
        }
        joblib.dump(artifact, OUT / args.model_output)

    dataset = {
        "version": f"{VERSION}_DATASET",
        "researchOnly": True,
        "strictPastInputs": True,
        "offlineFutureLabelOnly": True,
        "marketId": int(args.market_id),
        "features": list(FEATURES),
        "target": "CONFIRMED_RECOVERY_SIDE_MAKER_FILL_WITHIN_15S",
        "horizonMs": HORIZON_MS,
        "rows": [row for case in CASES for row in labelled_by_case[case]],
    }
    (OUT / args.dataset_output).write_text(json.dumps(dataset, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")

    report = {
        "version": VERSION,
        "researchOnly": True,
        "name": "R2.1 cooperation training phase 1",
        "preregistration": PREREGISTRATION,
        "hypothesis": "R2.1 own-lifecycle belief can rank near-term confirmed passive recovery progress on fault trajectories without selecting or changing any R2 action.",
        "dedupBoundary": "Unlike rejected point/delta recovery action gates and global action-fill heads, this model produces only an ongoing native-loop state probability; no WAIT, Maker or Taker action is a label or output.",
        "cohort": {
            "marketIds": [int(args.market_id)],
            "trainingTrajectory": "NATURAL_EXECUTION",
            "unseenFaultValidationTrajectories": ["MAKER_SUBMIT_REJECT", "MAKER_LONG_NO_FILL"],
            "unseenMarketOOS": False,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
        },
        "informationContract": {
            "inboxField": "r21ExecutionIncidentInbox.lifecycleBelief",
            "output": "confirmedPassiveRecoveryFillProbability15s",
            "actionRecommendation": None,
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
        },
        "learner": {
            "family": "median-imputed standardized logistic regression",
            "features": len(FEATURES),
            "trainingSamples": len(usable_train),
            "trainingBothClasses": train_both_classes,
            "trainingPrevalence": training_prevalence,
            "sweep": False,
            "largestAbsoluteCoefficients": coefficients[:12],
        },
        "train": train_metrics,
        "unseenFaultValidation": validation_metrics,
        "unseenFaultValidationByCase": validation_by_case,
        "trajectoryAudits": audits,
        "nonInterferencePass": non_interference,
        "waitAct": "N/A_INFORMATION_ONLY_MODEL",
        "oracleValueCeiling": "N/A_NO_ACTION_COUNTERFACTUAL",
        "learnedPolicyRealizedValue": "N/A_NO_POLICY_ACTION",
        "lockedGate": {
            "averagePrecisionLiftVsPrevalence": 1.25,
            "topQuintileLift": 1.5,
            "brierMustBeatTrainingPrevalenceConstant": True,
            "perDecisionLifecycleTerminalNonInterference": True,
            "pass": gate,
        },
        "decision": "KEEP_R21_LIFECYCLE_BELIEF_FOR_MULTIMARKET_DATASET" if gate else "REJECT_R21_LIFECYCLE_BELIEF_V1",
        "next": (
            "Freeze this information representation and generate a small chronological multi-market dataset before training an R2 response head. The response head remains inside R2 logic and may choose only existing R2-authorized recovery options."
            if gate
            else "Do not tune probability thresholds or logistic hyperparameters. Pivot to obligation-scoped residual progress or a different strict-past own-lifecycle representation."
        ),
        "artifacts": {
            "dataset": args.dataset_output,
            "model": args.model_output if model is not None else None,
            "report": args.output,
        },
    }
    (OUT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(OUT / args.output),
                "dataset": str(OUT / args.dataset_output),
                "model": None if model is None else str(OUT / args.model_output),
                "train": train_metrics,
                "unseenFaultValidation": validation_metrics,
                "nonInterferencePass": non_interference,
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
