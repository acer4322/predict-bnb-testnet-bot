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
from tools.train_hft_r21_lifecycle_belief_v1 import RecordingIncidentBridge


OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R21_OBLIGATION_RESIDUAL_BELIEF_V2"
PREREGISTRATION = "hft_r21_obligation_residual_belief_v2_preregistered.json"
HORIZON_MS = 15_000
SEED = 20260824
EPS = 1e-8
TERMINAL_FAULT_TYPES = {
    "SUBMIT_REJECT_CONFIRMED",
    "TERMINAL_ZERO_FILL_CONFIRMED",
    "TERMINAL_PARTIAL_FILL_CONFIRMED",
}

FEATURES = (
    "seconds_left",
    "obligation_age_ms",
    "obligation_original_qty",
    "obligation_progress_qty",
    "obligation_progress_fraction",
    "obligation_residual_qty",
    "obligation_side_up",
    "current_tracking_error",
    "current_abs_tracking_error",
    "current_tracking_aligned_with_obligation",
    "current_desired_side_shares",
    "current_maker_side_shares",
    "target_revision_delta",
    "recovery_child_exists",
    "recovery_child_age_ms",
    "recovery_child_price",
    "recovery_child_leaves_qty",
    "recovery_child_partial",
    "recovery_child_cancel_pending",
    "side_book_bid",
    "side_book_ask",
    "side_book_spread_ticks",
    "side_book_bid_depth",
    "side_book_ask_depth",
    "side_book_top3_bid_depth",
    "maker_fills_1s",
    "maker_fills_5s",
    "maker_shares_5s",
    "combined_paired_coverage",
    "worst_case_floor",
    "incident_count_30s",
    "incident_live_delay_30s",
    "incident_live_stall_30s",
    "incident_late_fill_30s",
    "latest_incident_age_ms",
    "consecutive_reject",
    "consecutive_no_fill",
    "consecutive_partial",
    "same_route_retry_count",
)


class FirstNMakerFaults:
    def __init__(self, mode: str, count: int = 3) -> None:
        self.mode = str(mode)
        self.count = int(count)
        self.used = 0

    def __call__(self, _state: dict[str, Any]) -> str | None:
        if self.used >= self.count:
            return None
        self.used += 1
        return self.mode


def maker_shares_by_side(state: dict[str, Any]) -> dict[str, float]:
    portfolio = state.get("actualPortfolio") or {}
    gross = finite(portfolio.get("maker_gross"))
    net = finite(portfolio.get("maker_net"))
    return {
        "UP": max(0.0, (gross + net) / 2.0),
        "DOWN": max(0.0, (gross - net) / 2.0),
    }


class R21ObligationResidualInbox:
    """Track fixed failed Maker obligations and expose belief only; never choose an action."""

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
        self.seen_fault_events: set[str] = set()
        self.ledgers: dict[str, dict[str, Any]] = {}
        self.last_maker_shares: dict[str, float] | None = None
        self.rows: list[dict[str, Any]] = []
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.seen_buckets: set[tuple[str, int]] = set()
        self.strict_past_violations: list[dict[str, Any]] = []
        self.model_output_count = 0

    def _allocate_confirmed_maker_progress(self, at_ms: int, current: dict[str, float]) -> None:
        if self.last_maker_shares is None:
            self.last_maker_shares = dict(current)
            return
        for side in ("UP", "DOWN"):
            delta = max(0.0, current[side] - self.last_maker_shares[side])
            if delta <= EPS:
                continue
            open_ledgers = sorted(
                (
                    ledger
                    for ledger in self.ledgers.values()
                    if ledger["side"] == side and finite(ledger["residualQty"]) > EPS
                ),
                key=lambda ledger: (int(ledger["faultAtMs"]), str(ledger["obligationId"])),
            )
            for ledger in open_ledgers:
                applied = min(delta, finite(ledger["residualQty"]))
                ledger["progressQty"] = finite(ledger["progressQty"]) + applied
                ledger["residualQty"] = max(0.0, finite(ledger["originalQty"]) - finite(ledger["progressQty"]))
                if ledger["residualQty"] <= EPS and ledger.get("completedAtMs") is None:
                    ledger["completedAtMs"] = at_ms
                delta -= applied
                if delta <= EPS:
                    break
        self.last_maker_shares = dict(current)

    def _discover_faults(self, at_ms: int, state: dict[str, Any]) -> None:
        revisions = state.get("targetRevision") or {}
        for event in sorted(self.receiver.notifications, key=lambda row: int(row.get("atMs") or 0)):
            event_id = str(event.get("eventId") or "")
            if event_id in self.seen_fault_events:
                continue
            if int(event.get("atMs") or 0) > at_ms:
                continue
            if str(event.get("role")) != "MAKER" or str(event.get("incidentType")) not in TERMINAL_FAULT_TYPES:
                continue
            self.seen_fault_events.add(event_id)
            original = max(0.0, finite(event.get("unresolvedQty")))
            if original <= EPS:
                continue
            side = str(event.get("side") or "UNKNOWN")
            if side not in {"UP", "DOWN"}:
                continue
            self.ledgers[event_id] = {
                "obligationId": event_id,
                "faultEventId": event_id,
                "faultType": str(event.get("incidentType")),
                "faultAtMs": int(event.get("atMs") or at_ms),
                "side": side,
                "originalQty": original,
                "progressQty": 0.0,
                "residualQty": original,
                "targetRevisionAtFault": finite(revisions.get(side)),
                "completedAtMs": None,
            }
            self.history[event_id] = []

    def _features(self, snapshot: dict[str, Any], state: dict[str, Any], ledger: dict[str, Any]) -> dict[str, float]:
        at_ms = int(snapshot["sampledAtMs"])
        side = str(ledger["side"])
        lower = side.lower()
        public = state.get("publicState") or snapshot
        book = state.get("outcomeBook") or {}
        portfolio = state.get("actualPortfolio") or {}
        desired = state.get("desiredMakerShares") or {}
        tracking = finite(state.get("trackingError"))
        aligned = (tracking < 0.0 and side == "UP") or (tracking > 0.0 and side == "DOWN")
        child = (state.get("activeMakerChildren") or {}).get(side) or {}
        requested = finite(child.get("requestedQty"))
        filled = finite(child.get("cumExecQty"), finite(child.get("filledQty")))
        leaves = finite(child.get("leavesQty"), max(0.0, requested - filled))
        submitted_at = int(child.get("submittedAtMs") or at_ms)
        incidents = [
            event
            for event in self.receiver.notifications
            if int(event.get("atMs") or 0) <= at_ms
            and at_ms - int(event.get("atMs") or 0) <= 30_000
        ]
        counts = Counter(str(event.get("incidentType") or "UNKNOWN") for event in incidents)
        latest_incident = max((int(event.get("atMs") or 0) for event in incidents), default=at_ms)
        memory = state.get("behaviorMemory") or {}
        maker_side = maker_shares_by_side(state)[side]
        original = finite(ledger["originalQty"])
        progress = finite(ledger["progressQty"])
        return {
            "seconds_left": finite(public.get("secondsLeft")),
            "obligation_age_ms": float(max(0, at_ms - int(ledger["faultAtMs"]))),
            "obligation_original_qty": original,
            "obligation_progress_qty": progress,
            "obligation_progress_fraction": progress / original if original > EPS else 0.0,
            "obligation_residual_qty": finite(ledger["residualQty"]),
            "obligation_side_up": float(side == "UP"),
            "current_tracking_error": tracking,
            "current_abs_tracking_error": abs(tracking),
            "current_tracking_aligned_with_obligation": float(aligned),
            "current_desired_side_shares": finite(desired.get(side)),
            "current_maker_side_shares": maker_side,
            "target_revision_delta": finite((state.get("targetRevision") or {}).get(side)) - finite(ledger["targetRevisionAtFault"]),
            "recovery_child_exists": float(bool(child)),
            "recovery_child_age_ms": float(max(0, at_ms - submitted_at)) if child else 0.0,
            "recovery_child_price": finite(child.get("price")),
            "recovery_child_leaves_qty": leaves,
            "recovery_child_partial": float(bool(child) and filled > EPS and leaves > EPS),
            "recovery_child_cancel_pending": float(bool(child.get("cancelPending") or child.get("cancelRequested"))),
            "side_book_bid": finite(book.get(f"{lower}_bid")),
            "side_book_ask": finite(book.get(f"{lower}_ask")),
            "side_book_spread_ticks": finite(book.get(f"{lower}_spread_ticks")),
            "side_book_bid_depth": finite(book.get(f"{lower}_bid_depth")),
            "side_book_ask_depth": finite(book.get(f"{lower}_ask_depth")),
            "side_book_top3_bid_depth": finite(book.get(f"{lower}_top3_bid_depth")),
            "maker_fills_1s": finite(portfolio.get("maker_fills_1s")),
            "maker_fills_5s": finite(portfolio.get("maker_fills_5s")),
            "maker_shares_5s": finite(portfolio.get("maker_shares_5s")),
            "combined_paired_coverage": finite(portfolio.get("combined_paired_coverage")),
            "worst_case_floor": finite(portfolio.get("worst_case_floor")),
            "incident_count_30s": float(len(incidents)),
            "incident_live_delay_30s": float(counts.get("LIVE_NO_FILL_DELAY", 0)),
            "incident_live_stall_30s": float(counts.get("LIVE_NO_FILL_STALL", 0)),
            "incident_late_fill_30s": float(counts.get("LATE_FILL_AFTER_DELAY", 0)),
            "latest_incident_age_ms": float(max(0, at_ms - latest_incident)) if incidents else 30_000.0,
            "consecutive_reject": finite(memory.get("consecutiveReject")),
            "consecutive_no_fill": finite(memory.get("consecutiveNoFill")),
            "consecutive_partial": finite(memory.get("consecutivePartial")),
            "same_route_retry_count": finite(memory.get("sameRouteRetryCount")),
        }

    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = self.base(snapshot)
        latest = self.recording_bridge.latest_payload
        if latest is None:
            return payload
        at_ms = int(snapshot["sampledAtMs"])
        state_as_of = int(latest.get("atMs") or 0)
        if state_as_of > at_ms:
            self.strict_past_violations.append({"snapshotAtMs": at_ms, "executionStateAsOfMs": state_as_of})
            return payload
        state = latest.get("executionState") or {}
        self._allocate_confirmed_maker_progress(at_ms, maker_shares_by_side(state))
        self._discover_faults(at_ms, state)
        belief_rows = []
        for obligation_id, ledger in sorted(self.ledgers.items(), key=lambda pair: (int(pair[1]["faultAtMs"]), pair[0])):
            self.history[obligation_id].append({"atMs": at_ms, "residualQty": finite(ledger["residualQty"])})
            if finite(ledger["residualQty"]) <= EPS:
                continue
            features = self._features(snapshot, state, ledger)
            probability: float | None = None
            if self.model is not None:
                values = np.asarray([[features[name] for name in FEATURES]], dtype=float)
                probability = float(self.model.predict_proba(values)[0, 1])
                belief_rows.append(
                    {
                        "obligationId": obligation_id,
                        "faultAtMs": int(ledger["faultAtMs"]),
                        "side": ledger["side"],
                        "residualQty": finite(ledger["residualQty"]),
                        "confirmedMakerProgressProbability15s": probability,
                        "actionRecommendation": None,
                    }
                )
                self.model_output_count += 1
            bucket = (obligation_id, at_ms // 1_000)
            if bucket not in self.seen_buckets:
                self.seen_buckets.add(bucket)
                self.rows.append(
                    {
                        "case": self.case,
                        "marketId": None,
                        "obligationId": obligation_id,
                        "faultType": ledger["faultType"],
                        "faultAtMs": int(ledger["faultAtMs"]),
                        "atMs": at_ms,
                        "executionStateAsOfMs": state_as_of,
                        "side": ledger["side"],
                        "residualQty": finite(ledger["residualQty"]),
                        "features": features,
                        "onlineModelProbability": probability,
                        "strictPastPass": state_as_of <= at_ms,
                    }
                )
        if belief_rows:
            payload["obligationResidualBeliefs"] = {
                "version": VERSION,
                "asOfMs": at_ms,
                "informationOnly": True,
                "actionAuthority": False,
                "obligations": belief_rows,
            }
        return payload


def run_trajectory(
    market_id: int,
    case: str,
    fault_mode: str,
    model: Pipeline | None,
) -> tuple[dict[str, Any], R21ObligationResidualInbox, RecordingIncidentBridge, FirstNMakerFaults]:
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    provider = R21ObligationResidualInbox(case, receiver, bridge, model=model)
    faults = FirstNMakerFaults(fault_mode, count=3)
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        maker_submit_fault_override=faults,
        fault_reentry_enabled=True,
        behavior_policy_override=bridge,
        behavior_ownstate_reentry=False,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=False,
        fault_no_fill_stall_ms=15_000 if fault_mode == "NO_FILL_STALL" else None,
        r21_incident_inbox_provider=provider,
    )
    for row in provider.rows:
        row["marketId"] = int(market_id)
    return report, provider, bridge, faults


def label_rows(provider: R21ObligationResidualInbox, report: dict[str, Any]) -> list[dict[str, Any]]:
    final_at = max(int(trace["atMs"]) for trace in report["r21InformationInbox"]["traces"])
    labelled = []
    for source in provider.rows:
        row = copy.deepcopy(source)
        at_ms = int(row["atMs"])
        if at_ms + HORIZON_MS > final_at:
            row["censored"] = True
            labelled.append(row)
            continue
        future = [
            point
            for point in provider.history.get(str(row["obligationId"]), [])
            if at_ms < int(point["atMs"]) <= at_ms + HORIZON_MS
        ]
        minimum = min((finite(point["residualQty"]) for point in future), default=finite(row["residualQty"]))
        progress = max(0.0, finite(row["residualQty"]) - minimum)
        row.update(
            {
                "censored": False,
                "labelConfirmedMakerObligationProgress15s": int(progress > EPS),
                "labelConfirmedMakerObligationProgressQty15s": progress,
            }
        )
        labelled.append(row)
    return labelled


def matrix(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    usable = [row for row in rows if not row.get("censored")]
    x = np.asarray([[finite(row["features"].get(name)) for name in FEATURES] for row in usable], dtype=float)
    y = np.asarray([int(row["labelConfirmedMakerObligationProgress15s"]) for row in usable], dtype=int)
    return x, y, usable


def evaluate(rows: list[dict[str, Any]], model: Pipeline, training_prevalence: float) -> dict[str, Any]:
    x, y, usable = matrix(rows)
    probabilities = model.predict_proba(x)[:, 1]
    for row, probability in zip(usable, probabilities, strict=True):
        row["offlineModelProbability"] = float(probability)
    prevalence = float(np.mean(y)) if len(y) else 0.0
    both = len(set(y.tolist())) == 2
    ap = float(average_precision_score(y, probabilities)) if len(y) else 0.0
    brier = float(brier_score_loss(y, probabilities)) if len(y) else None
    baseline_brier = float(np.mean((y - training_prevalence) ** 2)) if len(y) else None
    top_count = max(1, int(math.ceil(len(y) * 0.20))) if len(y) else 0
    top = np.argsort(-probabilities)[:top_count]
    top_rate = float(np.mean(y[top])) if top_count else 0.0
    return {
        "samples": len(usable),
        "censored": sum(int(row.get("censored", False)) for row in rows),
        "obligations": len({str(row["obligationId"]) for row in usable}),
        "positives": int(np.sum(y)),
        "negatives": int(len(y) - np.sum(y)),
        "prevalence": prevalence,
        "bothClasses": both,
        "averagePrecision": ap,
        "averagePrecisionLiftVsPrevalence": ap / prevalence if prevalence > 0.0 else 0.0,
        "rocAucDiagnostic": float(roc_auc_score(y, probabilities)) if both else None,
        "brier": brier,
        "trainingPrevalenceConstantBrier": baseline_brier,
        "brierBeatsTrainingPrevalenceConstant": bool(brier is not None and baseline_brier is not None and brier < baseline_brier),
        "topQuintileSamples": top_count,
        "topQuintilePositiveRate": top_rate,
        "topQuintileLift": top_rate / prevalence if prevalence > 0.0 else 0.0,
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-market-id", type=int, default=1569361)
    parser.add_argument("--validation-market-id", type=int, default=1571387)
    parser.add_argument("--output", default="hft_r21_obligation_residual_belief_v2_report.json")
    parser.add_argument("--dataset-output", default="hft_r21_obligation_residual_belief_v2_dataset.json")
    parser.add_argument("--model-output", default="hft_r21_obligation_residual_belief_v2.joblib")
    args = parser.parse_args()

    print(json.dumps({"stage": "train_submit_reject", "marketId": args.train_market_id}), flush=True)
    train_report, train_provider, train_bridge, train_faults = run_trajectory(
        args.train_market_id, "TRAIN_SUBMIT_REJECT", "SUBMIT_REJECT", None
    )
    train_rows = label_rows(train_provider, train_report)
    train_x, train_y, train_usable = matrix(train_rows)
    train_both = len(set(train_y.tolist())) == 2
    model: Pipeline | None = None
    if train_both:
        model = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("logistic", LogisticRegression(C=1.0, class_weight="balanced", max_iter=1_000, random_state=SEED, solver="liblinear")),
            ]
        )
        model.fit(train_x, train_y)

    print(json.dumps({"stage": "validation_nofill_baseline", "marketId": args.validation_market_id}), flush=True)
    baseline_report, baseline_provider, baseline_bridge, baseline_faults = run_trajectory(
        args.validation_market_id, "VALIDATION_NO_FILL_BASELINE", "NO_FILL_STALL", None
    )
    print(json.dumps({"stage": "validation_nofill_belief", "marketId": args.validation_market_id, "modelAvailable": model is not None}), flush=True)
    belief_report, belief_provider, belief_bridge, belief_faults = run_trajectory(
        args.validation_market_id, "VALIDATION_NO_FILL_BELIEF", "NO_FILL_STALL", model
    )
    validation_rows = label_rows(belief_provider, belief_report)
    training_prevalence = float(np.mean(train_y)) if len(train_y) else 0.0
    train_metrics = evaluate(train_rows, model, training_prevalence) if model is not None else None
    validation_metrics = evaluate(validation_rows, model, training_prevalence) if model is not None else None

    decision_exact = digest(baseline_report["controller"]["decisions"]) == digest(belief_report["controller"]["decisions"])
    lifecycle_exact = digest(baseline_report["executionLifecycleTrace"]) == digest(belief_report["executionLifecycleTrace"])
    baseline_execution = compact_execution(baseline_report)
    belief_execution = compact_execution(belief_report)
    execution_exact = canonical(baseline_execution) == canonical(belief_execution)
    strict_past = not train_provider.strict_past_violations and not baseline_provider.strict_past_violations and not belief_provider.strict_past_violations
    schema_clean = not train_provider.base.schema_errors and not baseline_provider.base.schema_errors and not belief_provider.base.schema_errors
    safety = all(report["cycleInvariantViolationCount"] == 0 for report in (train_report, baseline_report, belief_report))
    non_interference = decision_exact and lifecycle_exact and execution_exact and strict_past and schema_clean and safety
    actual_fault_gate = train_faults.used >= 2 and baseline_faults.used >= 2 and belief_faults.used >= 2
    gate = bool(
        model is not None
        and validation_metrics is not None
        and validation_metrics["bothClasses"]
        and validation_metrics["averagePrecisionLiftVsPrevalence"] >= 1.25
        and validation_metrics["topQuintileLift"] >= 1.5
        and validation_metrics["brierBeatsTrainingPrevalenceConstant"]
        and actual_fault_gate
        and non_interference
    )

    coefficients = []
    if model is not None:
        raw = model.named_steps["logistic"].coef_[0]
        coefficients = [
            {"feature": name, "coefficient": float(value)}
            for name, value in sorted(zip(FEATURES, raw, strict=True), key=lambda pair: abs(pair[1]), reverse=True)
        ]
        joblib.dump(
            {
                "version": VERSION,
                "researchOnly": True,
                "informationOnly": True,
                "actionAuthority": False,
                "features": list(FEATURES),
                "horizonMs": HORIZON_MS,
                "model": model,
                "trainedMarkets": [int(args.train_market_id)],
                "validationMarkets": [int(args.validation_market_id)],
                "gatePass": gate,
                "promotionEligible": False,
            },
            OUT / args.model_output,
        )

    dataset = {
        "version": f"{VERSION}_DATASET",
        "researchOnly": True,
        "strictPastInputs": True,
        "offlineFutureLabelOnly": True,
        "features": list(FEATURES),
        "horizonMs": HORIZON_MS,
        "trainRows": train_rows,
        "validationRows": validation_rows,
    }
    (OUT / args.dataset_output).write_text(json.dumps(dataset, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")

    report = {
        "version": VERSION,
        "researchOnly": True,
        "name": "R2.1 cooperation training phase 1 obligation-residual pivot",
        "preregistration": PREREGISTRATION,
        "hypothesis": "A fixed failed-obligation residual ledger can generalize passive-progress belief across submit-reject to no-fill and across opened train markets without choosing an action.",
        "dedupBoundary": "V1 generic moving-state belief was rejected. V2 freezes fault side/quantity/revision, allocates confirmed Maker progress FIFO and trains only on obligation-scoped rows; it does not alter learner thresholds or output an action.",
        "cohort": {
            "training": {"marketId": int(args.train_market_id), "fault": "FIRST_THREE_MAKER_SUBMIT_REJECTS"},
            "validation": {"marketId": int(args.validation_market_id), "fault": "FIRST_THREE_MAKER_NO_FILL_STALLS"},
            "unseenPromotionOOS": False,
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
            "field": "r21ExecutionIncidentInbox.obligationResidualBeliefs",
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "actionRecommendation": None,
        },
        "faultCoverage": {
            "trainPlanned": train_faults.count,
            "trainActual": train_faults.used,
            "validationBaselinePlanned": baseline_faults.count,
            "validationBaselineActual": baseline_faults.used,
            "validationBeliefPlanned": belief_faults.count,
            "validationBeliefActual": belief_faults.used,
            "minimumTwoEachPass": actual_fault_gate,
            "trainObligations": len(train_provider.ledgers),
            "validationObligations": len(belief_provider.ledgers),
        },
        "learner": {
            "family": "median-imputed standardized logistic regression",
            "features": len(FEATURES),
            "trainingSamples": len(train_usable),
            "trainingBothClasses": train_both,
            "trainingPrevalence": training_prevalence,
            "sweep": False,
            "largestAbsoluteCoefficients": coefficients[:12],
        },
        "train": train_metrics,
        "crossMarketCrossFaultValidation": validation_metrics,
        "nonInterference": {
            "perDecisionExact": decision_exact,
            "lifecycleExact": lifecycle_exact,
            "terminalExecutionExact": execution_exact,
            "strictPast": strict_past,
            "schemaClean": schema_clean,
            "zeroCycleViolations": safety,
            "pass": non_interference,
            "baselineExecution": baseline_execution,
            "beliefExecution": belief_execution,
        },
        "waitAct": "N/A_INFORMATION_ONLY_MODEL",
        "oracleValueCeiling": "N/A_NO_ACTION_COUNTERFACTUAL",
        "learnedPolicyRealizedValue": "N/A_NO_POLICY_ACTION",
        "lockedGatePass": gate,
        "decision": "KEEP_R21_OBLIGATION_RESIDUAL_BELIEF_FOR_SMALL_MULTIMARKET_CURRICULUM" if gate else "REJECT_R21_OBLIGATION_RESIDUAL_BELIEF_V2",
        "next": (
            "Freeze V2 and generate a small chronological multi-market obligation dataset before allowing an R2 logic response head to consume the belief."
            if gate
            else "Do not tune the classifier or probability threshold. Use actual Predict wallet lifecycle data or a richer event-sequence/hazard representation before another response model."
        ),
        "artifacts": {"dataset": args.dataset_output, "model": args.model_output if model is not None else None, "report": args.output},
    }
    (OUT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(OUT / args.output),
                "train": train_metrics,
                "validation": validation_metrics,
                "faultCoverage": report["faultCoverage"],
                "nonInterference": report["nonInterference"],
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
