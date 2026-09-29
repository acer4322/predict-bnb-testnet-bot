from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import joblib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_execution_incident_notification_exam_v1 import compact_execution
from tools.hft_r21_information_only_incident_inbox_exam_v1 import canonical, digest
from tools.train_hft_r21_obligation_residual_belief_v2 import evaluate, label_rows, run_trajectory


OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R21_OBLIGATION_RESIDUAL_BELIEF_V2_REPLICATION"
PREREGISTRATION = "hft_r21_obligation_residual_belief_v2_replication_preregistered.json"
MODEL = OUT / "hft_r21_obligation_residual_belief_v2.joblib"
SOURCE_REPORT = OUT / "hft_r21_obligation_residual_belief_v2_report.json"


def main() -> None:
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1572594)
    parser.add_argument("--output", default="hft_r21_obligation_residual_belief_v2_replication_report.json")
    parser.add_argument("--dataset-output", default="hft_r21_obligation_residual_belief_v2_replication_dataset.json")
    args = parser.parse_args()

    artifact = joblib.load(MODEL)
    model = artifact["model"]
    source = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    training_prevalence = float(source["learner"]["trainingPrevalence"])

    print(json.dumps({"stage": "frozen_replication_baseline", "marketId": args.market_id}), flush=True)
    baseline_report, baseline_provider, baseline_bridge, baseline_faults = run_trajectory(
        args.market_id, "FROZEN_REPLICATION_BASELINE", "NO_FILL_STALL", None
    )
    print(json.dumps({"stage": "frozen_replication_belief", "marketId": args.market_id}), flush=True)
    belief_report, belief_provider, belief_bridge, belief_faults = run_trajectory(
        args.market_id, "FROZEN_REPLICATION_BELIEF", "NO_FILL_STALL", model
    )
    rows = label_rows(belief_provider, belief_report)
    metrics = evaluate(rows, model, training_prevalence)
    prevalence = float(metrics["prevalence"])
    normalized_ap = (
        (float(metrics["averagePrecision"]) - prevalence) / (1.0 - prevalence)
        if prevalence < 1.0
        else 0.0
    )
    baseline_brier = float(metrics["trainingPrevalenceConstantBrier"])
    relative_brier_improvement = (
        (baseline_brier - float(metrics["brier"])) / baseline_brier
        if baseline_brier > 0.0
        else 0.0
    )

    decision_exact = digest(baseline_report["controller"]["decisions"]) == digest(belief_report["controller"]["decisions"])
    lifecycle_exact = digest(baseline_report["executionLifecycleTrace"]) == digest(belief_report["executionLifecycleTrace"])
    baseline_execution = compact_execution(baseline_report)
    belief_execution = compact_execution(belief_report)
    terminal_exact = canonical(baseline_execution) == canonical(belief_execution)
    strict_past = not baseline_provider.strict_past_violations and not belief_provider.strict_past_violations
    schema_clean = not baseline_provider.base.schema_errors and not belief_provider.base.schema_errors
    zero_violations = baseline_report["cycleInvariantViolationCount"] == 0 and belief_report["cycleInvariantViolationCount"] == 0
    non_interference = decision_exact and lifecycle_exact and terminal_exact and strict_past and schema_clean and zero_violations
    fault_gate = baseline_faults.used >= 2 and belief_faults.used >= 2
    gate = bool(
        fault_gate
        and metrics["bothClasses"]
        and normalized_ap >= 0.25
        and metrics["rocAucDiagnostic"] is not None
        and float(metrics["rocAucDiagnostic"]) >= 0.65
        and relative_brier_improvement >= 0.05
        and non_interference
    )

    dataset = {
        "version": f"{VERSION}_DATASET",
        "researchOnly": True,
        "strictPastInputs": True,
        "offlineFutureLabelOnly": True,
        "frozenModel": MODEL.name,
        "marketId": int(args.market_id),
        "rows": rows,
    }
    (OUT / args.dataset_output).write_text(json.dumps(dataset, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")

    report: dict[str, Any] = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": PREREGISTRATION,
        "frozenModel": MODEL.name,
        "modelRetrained": False,
        "modelThresholdTuned": False,
        "cohort": {
            "marketId": int(args.market_id),
            "fault": "FIRST_THREE_MAKER_NO_FILL_STALLS",
            "officialHftForward": False,
            "sealed": False,
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
        "faultCoverage": {
            "baselineActual": baseline_faults.used,
            "beliefActual": belief_faults.used,
            "minimumTwoPass": fault_gate,
            "obligations": len(belief_provider.ledgers),
        },
        "frozenBeliefMetrics": {
            **metrics,
            "normalizedAveragePrecisionImprovement": normalized_ap,
            "relativeBrierImprovementVsTrainingPrevalenceConstant": relative_brier_improvement,
        },
        "nonInterference": {
            "perDecisionExact": decision_exact,
            "lifecycleExact": lifecycle_exact,
            "terminalExecutionExact": terminal_exact,
            "strictPast": strict_past,
            "schemaClean": schema_clean,
            "zeroCycleViolations": zero_violations,
            "pass": non_interference,
            "baselineExecution": baseline_execution,
            "beliefExecution": belief_execution,
        },
        "informationContract": {
            "actionAuthority": False,
            "orderMutationAuthority": False,
            "desiredPortfolioMutationAuthority": False,
            "actionRecommendation": None,
        },
        "waitAct": "N/A_INFORMATION_ONLY_MODEL",
        "oracleValueCeiling": "N/A_NO_ACTION_COUNTERFACTUAL",
        "learnedPolicyRealizedValue": "N/A_NO_POLICY_ACTION",
        "lockedGate": {
            "normalizedAveragePrecisionImprovement": 0.25,
            "rocAuc": 0.65,
            "relativeBrierImprovementVsTrainingPrevalenceConstant": 0.05,
            "pass": gate,
        },
        "decision": "KEEP_R21_OBLIGATION_RESIDUAL_BELIEF_COMPONENT" if gate else "REJECT_R21_OBLIGATION_RESIDUAL_BELIEF_REPLICATION",
        "next": (
            "Freeze the belief component and generate a small chronological multi-market cooperation curriculum. Only then train an R2 logic response head over existing R2-authorized recovery options."
            if gate
            else "Do not retrain or threshold-tune this belief. Move to actual Predict wallet lifecycle data or a cause-specific event-sequence representation."
        ),
        "artifacts": {"dataset": args.dataset_output, "report": args.output},
    }
    (OUT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(OUT / args.output), "metrics": report["frozenBeliefMetrics"], "faultCoverage": report["faultCoverage"], "nonInterference": report["nonInterference"], "decision": report["decision"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
