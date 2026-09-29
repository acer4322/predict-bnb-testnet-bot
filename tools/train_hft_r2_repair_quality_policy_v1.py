from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import (  # noqa: E402
    ACTIONS,
    BASE,
    FEATURE_NAMES,
)


VERSION = "HFT_R2_REPAIR_QUALITY_POLICY_V1"
WAIT = "WAIT_PRESERVE"
NON_WAIT = tuple(action for action in ACTIONS if action != WAIT)
TRAIN_MARKETS = (
    1575819,
    1576119,
    1576324,
    1576518,
    1576765,
    1576991,
    1577181,
    1577392,
    1577751,
    1577937,
    1578351,
    1578546,
)
VALIDATION_MARKETS = (1578732, 1578921, 1579116, 1579313, 1579674, 1579874)
FAULTS = ("NATURAL_LIFECYCLE", "DEEP_FIRST_PASSIVE_CHILD")
SEED = 20260824
AREA_WEIGHT = 0.7
RESIDUAL_WEIGHT = 0.3
METRIC_FLOOR = 18.0


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def clipped_reduction(wait_value: float, action_value: float) -> float:
    reduction = (wait_value - action_value) / max(wait_value, METRIC_FLOOR)
    return float(np.clip(reduction, -2.0, 1.0))


def build_contexts(report: dict[str, Any], markets: tuple[int, ...]) -> list[dict[str, Any]]:
    allowed = set(markets)
    runs = {
        (int(row["marketId"]), str(row["faultId"]), str(row["actionId"])): row
        for row in report["rows"]
    }
    contexts: list[dict[str, Any]] = []
    for context in report["contexts"]:
        market_id = int(context["marketId"])
        fault_id = str(context["faultId"])
        if market_id not in allowed or not context["matchedState"] or not context["allSemanticPass"]:
            continue
        action_rows = {action: runs[(market_id, fault_id, action)] for action in ACTIONS}
        wait_terminal = action_rows[WAIT]["terminal"]
        wait_area = finite(wait_terminal["trackingErrorAreaShareSeconds"])
        wait_residual = finite(wait_terminal["finalAbsTrackingError"])
        metrics: dict[str, dict[str, Any]] = {}
        for action, action_row in action_rows.items():
            terminal = action_row["terminal"]
            area = finite(terminal["trackingErrorAreaShareSeconds"])
            residual = finite(terminal["finalAbsTrackingError"])
            area_reduction = clipped_reduction(wait_area, area)
            residual_reduction = clipped_reduction(wait_residual, residual)
            metrics[action] = {
                "trackingErrorArea": area,
                "terminalResidual": residual,
                "trackingAreaReduction": area_reduction,
                "terminalResidualReduction": residual_reduction,
                "repairQuality": AREA_WEIGHT * area_reduction + RESIDUAL_WEIGHT * residual_reduction,
                "pairedCoverage": finite(terminal["pairedCoverage"]),
                "worstCaseFloor": finite(terminal["worstCaseFloor"]),
                "makerFilledShares": finite(terminal["makerFilledShares"]),
                "takerFilledShares": finite(terminal["takerFilledShares"]),
                "semanticPass": bool(
                    action_row["cycleInvariantViolationCount"] == 0
                    and action_row["semanticGate"].get("actualInventoryEqualsHftFillLedger")
                    and action_row["semanticGate"].get("noCycleInvariantViolation")
                ),
            }
        state = action_rows[WAIT]["stateAtFirstAction"]
        contexts.append(
            {
                "marketId": market_id,
                "faultId": fault_id,
                "stateHash": str(state["stateHash"]),
                "features": {name: finite(state["features"].get(name)) for name in FEATURE_NAMES},
                "metrics": metrics,
            }
        )
    return contexts


def dedupe_train(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in contexts:
        prior = unique.get(row["stateHash"])
        if prior is not None:
            prior_targets = {action: prior["metrics"][action]["repairQuality"] for action in ACTIONS}
            current_targets = {action: row["metrics"][action]["repairQuality"] for action in ACTIONS}
            if prior_targets != current_targets:
                raise RuntimeError(f"duplicate state hash has inconsistent repair outcomes: {row['stateHash']}")
            continue
        unique[row["stateHash"]] = row
    return list(unique.values())


def feature_matrix(contexts: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray(
        [[finite(row["features"].get(name)) for name in FEATURE_NAMES] for row in contexts],
        dtype=float,
    )


def train_models(contexts: list[dict[str, Any]]) -> dict[str, RandomForestRegressor]:
    x = feature_matrix(contexts)
    models: dict[str, RandomForestRegressor] = {}
    for index, action in enumerate(NON_WAIT):
        y = np.asarray([row["metrics"][action]["repairQuality"] for row in contexts], dtype=float)
        model = RandomForestRegressor(
            n_estimators=300,
            min_samples_leaf=3,
            max_features=0.7,
            bootstrap=True,
            random_state=SEED + index,
            n_jobs=-1,
        )
        model.fit(x, y)
        models[action] = model
    return models


def predict(models: dict[str, RandomForestRegressor], row: dict[str, Any]) -> dict[str, Any]:
    x = np.asarray([[finite(row["features"].get(name)) for name in FEATURE_NAMES]], dtype=float)
    predictions: dict[str, dict[str, float]] = {}
    for action, model in models.items():
        tree_values = np.asarray([float(tree.predict(x)[0]) for tree in model.estimators_], dtype=float)
        predictions[action] = {
            "medianRepairQuality": float(np.median(tree_values)),
            "q20RepairQuality": float(np.quantile(tree_values, 0.2)),
            "meanRepairQuality": float(np.mean(tree_values)),
        }
    eligible = [action for action in NON_WAIT if predictions[action]["q20RepairQuality"] > 0.0]
    selected = max(eligible, key=lambda action: predictions[action]["medianRepairQuality"]) if eligible else WAIT
    return {"selectedAction": selected, "predictions": predictions}


def raw_reduction(wait_value: float, selected_value: float) -> float:
    if wait_value <= 0.0:
        return 0.0
    return (wait_value - selected_value) / wait_value


def summarize_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(row["selectedAction"] for row in decisions)
    wait_area = sum(row["waitMetrics"]["trackingErrorArea"] for row in decisions)
    selected_area = sum(row["selectedMetrics"]["trackingErrorArea"] for row in decisions)
    wait_residual = sum(row["waitMetrics"]["terminalResidual"] for row in decisions)
    selected_residual = sum(row["selectedMetrics"]["terminalResidual"] for row in decisions)
    fault_summaries: list[dict[str, Any]] = []
    for fault_id in FAULTS:
        selected = [row for row in decisions if row["faultId"] == fault_id]
        fault_wait_area = sum(row["waitMetrics"]["trackingErrorArea"] for row in selected)
        fault_selected_area = sum(row["selectedMetrics"]["trackingErrorArea"] for row in selected)
        fault_wait_residual = sum(row["waitMetrics"]["terminalResidual"] for row in selected)
        fault_selected_residual = sum(row["selectedMetrics"]["terminalResidual"] for row in selected)
        fault_summaries.append(
            {
                "faultId": fault_id,
                "contexts": len(selected),
                "trackingAreaReduction": raw_reduction(fault_wait_area, fault_selected_area),
                "terminalResidualReduction": raw_reduction(fault_wait_residual, fault_selected_residual),
            }
        )
    material = [
        row
        for row in decisions
        if raw_reduction(
            row["waitMetrics"]["trackingErrorArea"], row["selectedMetrics"]["trackingErrorArea"]
        )
        >= 0.3
        or raw_reduction(
            row["waitMetrics"]["terminalResidual"], row["selectedMetrics"]["terminalResidual"]
        )
        >= 0.3
    ]
    return {
        "contexts": len(decisions),
        "actionCounts": {action: action_counts.get(action, 0) for action in ACTIONS},
        "actRate": sum(row["selectedAction"] != WAIT for row in decisions) / len(decisions)
        if decisions
        else 0.0,
        "trackingErrorAreaWait": wait_area,
        "trackingErrorAreaPolicy": selected_area,
        "trackingErrorAreaReduction": raw_reduction(wait_area, selected_area),
        "terminalResidualWait": wait_residual,
        "terminalResidualPolicy": selected_residual,
        "terminalResidualReduction": raw_reduction(wait_residual, selected_residual),
        "meanPairedCoverageWait": float(
            np.mean([row["waitMetrics"]["pairedCoverage"] for row in decisions])
        )
        if decisions
        else 0.0,
        "meanPairedCoveragePolicy": float(
            np.mean([row["selectedMetrics"]["pairedCoverage"] for row in decisions])
        )
        if decisions
        else 0.0,
        "worstCaseFloorWaitAudit": sum(row["waitMetrics"]["worstCaseFloor"] for row in decisions),
        "worstCaseFloorPolicyAudit": sum(
            row["selectedMetrics"]["worstCaseFloor"] for row in decisions
        ),
        "materialImprovementContexts": len(material),
        "faultFamilies": fault_summaries,
        "semanticViolationDecisions": sum(not row["selectedMetrics"]["semanticPass"] for row in decisions),
    }


def evaluate(
    models: dict[str, RandomForestRegressor], contexts: list[dict[str, Any]], split: str
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for row in contexts:
        prediction = predict(models, row)
        selected = prediction["selectedAction"]
        oracle = max(ACTIONS, key=lambda action: row["metrics"][action]["repairQuality"])
        decisions.append(
            {
                "split": split,
                "marketId": row["marketId"],
                "faultId": row["faultId"],
                "stateHash": row["stateHash"],
                **prediction,
                "selectedMetrics": row["metrics"][selected],
                "waitMetrics": row["metrics"][WAIT],
                "repairQualityOracleAction": oracle,
                "repairQualityOracleMetrics": row["metrics"][oracle],
            }
        )
    return {"split": split, "decisions": decisions, **summarize_decisions(decisions)}


def evaluate_fixed(contexts: list[dict[str, Any]], action: str) -> dict[str, Any]:
    decisions = [
        {
            "marketId": row["marketId"],
            "faultId": row["faultId"],
            "selectedAction": action,
            "selectedMetrics": row["metrics"][action],
            "waitMetrics": row["metrics"][WAIT],
        }
        for row in contexts
    ]
    return summarize_decisions(decisions)


def gate_pass(summary: dict[str, Any]) -> bool:
    aggregate_pass = (
        summary["trackingErrorAreaReduction"] >= 0.3
        or summary["terminalResidualReduction"] >= 0.3
    )
    fault_pass = all(
        row["trackingAreaReduction"] >= 0.3 or row["terminalResidualReduction"] >= 0.3
        for row in summary["faultFamilies"]
    )
    return bool(
        aggregate_pass
        and fault_pass
        and summary["actRate"] > 0.0
        and summary["semanticViolationDecisions"] == 0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix", default="hft_r2_fault_conditioned_recovery_train12_validation6_v1_report.json"
    )
    parser.add_argument("--output", default="hft_r2_repair_quality_policy_v1_report.json")
    parser.add_argument("--model-output", default="hft_r2_repair_quality_policy_v1.joblib")
    args = parser.parse_args()

    source = json.loads((BASE / args.matrix).read_text(encoding="utf-8"))
    train_all = build_contexts(source, TRAIN_MARKETS)
    train = dedupe_train(train_all)
    validation = build_contexts(source, VALIDATION_MARKETS)
    if len(train) < 12 or len(validation) != 12:
        raise RuntimeError("insufficient matched chronological repair contexts")

    models = train_models(train)
    train_eval = evaluate(models, train, "train_unique_state")
    validation_eval = evaluate(models, validation, "chronological_validation")
    validation_gate = gate_pass(validation_eval)
    decision = (
        "KEEP_REPAIR_QUALITY_SELECTOR_FOR_MULTI_FAULT_PILOT"
        if validation_gate
        else "REJECT_REPAIR_QUALITY_SELECTOR"
    )
    artifact = {
        "version": VERSION,
        "features": list(FEATURE_NAMES),
        "actions": list(ACTIONS),
        "models": models,
        "repairQuality": {
            "trackingAreaWeight": AREA_WEIGHT,
            "terminalResidualWeight": RESIDUAL_WEIGHT,
            "metricFloor": METRIC_FLOOR,
            "componentClip": [-2.0, 1.0],
        },
        "decisionRule": "highest median non-WAIT repair quality only when tree q20 > 0; otherwise WAIT_PRESERVE",
        "seed": SEED,
        "trainMarkets": TRAIN_MARKETS,
        "validationMarkets": VALIDATION_MARKETS,
        "trainingStateHashes": [row["stateHash"] for row in train],
    }
    joblib.dump(artifact, BASE / args.model_output)

    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_repair_quality_policy_v1_preregistered.json",
        "sourceMatrix": args.matrix,
        "modelArtifact": args.model_output,
        "chronology": {
            "trainMarkets": list(TRAIN_MARKETS),
            "validationMarkets": list(VALIDATION_MARKETS),
            "holdoutMarketsUsed": False,
            "validationTuning": False,
        },
        "executionSemantics": source["executionSemantics"],
        "learner": {
            "family": "per-action random-forest repair-quality regression",
            "treesPerAction": 300,
            "minSamplesLeaf": 3,
            "maxFeatures": 0.7,
            "seed": SEED,
            "trainContextsBeforeStateDedup": len(train_all),
            "trainUniqueStates": len(train),
            "target": artifact["repairQuality"],
            "decisionRule": artifact["decisionRule"],
        },
        "train": train_eval,
        "validation": validation_eval,
        "fixedValidationBaselines": {
            action: evaluate_fixed(validation, action) for action in ACTIONS
        },
        "validationGatePass": validation_gate,
        "decision": decision,
        "currentStandardBoundary": {
            "passiveMaintainAndRepairUpstreamPresent": True,
            "activeRepairSelectorTested": True,
            "multiConsecutiveFailureRecoveryTested": False,
            "takerRejectOrNoFillRecoveryTested": False,
            "fullAutonomousRepairGraduation": False,
        },
        "next": "If kept, run only a small fault-cross pilot that adds consecutive passive failures plus bounded-Taker reject/no-fill while preserving the Frozen R2 coupled loop. Do not use the sealed HFT Forward cohort.",
    }
    (BASE / args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(BASE / args.output),
                "model": str(BASE / args.model_output),
                "train": {key: train_eval[key] for key in (
                    "contexts",
                    "actionCounts",
                    "actRate",
                    "trackingErrorAreaReduction",
                    "terminalResidualReduction",
                    "materialImprovementContexts",
                )},
                "validation": {key: validation_eval[key] for key in (
                    "contexts",
                    "actionCounts",
                    "actRate",
                    "trackingErrorAreaReduction",
                    "terminalResidualReduction",
                    "materialImprovementContexts",
                    "faultFamilies",
                    "worstCaseFloorWaitAudit",
                    "worstCaseFloorPolicyAudit",
                    "semanticViolationDecisions",
                )},
                "validationGatePass": validation_gate,
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
