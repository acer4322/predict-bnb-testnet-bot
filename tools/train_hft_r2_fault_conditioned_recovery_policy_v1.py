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

from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import ACTIONS, BASE, FEATURE_NAMES  # noqa: E402


VERSION = "HFT_R2_FAULT_CONDITIONED_RECOVERY_POLICY_V1"
WAIT = "WAIT_PRESERVE"
NON_WAIT = tuple(action for action in ACTIONS if action != WAIT)
TRAIN_MARKETS = (1575819, 1576119, 1576324, 1576518, 1576765, 1576991, 1577181, 1577392, 1577751, 1577937, 1578351, 1578546)
VALIDATION_MARKETS = (1578732, 1578921, 1579116, 1579313, 1579674, 1579874)
SEED = 20260823


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def build_contexts(report: dict[str, Any], markets: tuple[int, ...]) -> list[dict[str, Any]]:
    selected = [
        row
        for row in report["contexts"]
        if int(row["marketId"]) in set(markets) and row["matchedState"] and row["allSemanticPass"]
    ]
    runs = {
        (int(row["marketId"]), str(row["faultId"]), str(row["actionId"])): row
        for row in report["rows"]
    }
    contexts = []
    for row in selected:
        market_id = int(row["marketId"])
        fault_id = str(row["faultId"])
        action_rows = {action: runs[(market_id, fault_id, action)] for action in ACTIONS}
        state = action_rows[WAIT]["stateAtFirstAction"]
        contexts.append(
            {
                **row,
                "stateHash": state["stateHash"],
                "features": {name: finite(state["features"].get(name)) for name in FEATURE_NAMES},
                "actionWorstCaseFloors": {
                    action: finite(action_rows[action]["terminal"]["worstCaseFloor"]) for action in ACTIONS
                },
                "actionSemanticPass": {
                    action: bool(
                        action_rows[action]["cycleInvariantViolationCount"] == 0
                        and action_rows[action]["semanticGate"].get("actualInventoryEqualsHftFillLedger")
                    )
                    for action in ACTIONS
                },
            }
        )
    return contexts


def dedupe_train(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in contexts:
        prior = unique.get(row["stateHash"])
        if prior is not None:
            if prior["actionFloorAdvantagesVsWait"] != row["actionFloorAdvantagesVsWait"]:
                raise RuntimeError(f"duplicate state hash has inconsistent outcomes: {row['stateHash']}")
            continue
        unique[row["stateHash"]] = row
    return list(unique.values())


def feature_matrix(contexts: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([[finite(row["features"].get(name)) for name in FEATURE_NAMES] for row in contexts], dtype=float)


def train_models(contexts: list[dict[str, Any]]) -> dict[str, RandomForestRegressor]:
    x = feature_matrix(contexts)
    models: dict[str, RandomForestRegressor] = {}
    for index, action in enumerate(NON_WAIT):
        y = np.asarray([finite(row["actionFloorAdvantagesVsWait"].get(action)) for row in contexts], dtype=float)
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


def predict_context(models: dict[str, RandomForestRegressor], row: dict[str, Any]) -> dict[str, Any]:
    x = np.asarray([[finite(row["features"].get(name)) for name in FEATURE_NAMES]], dtype=float)
    predictions: dict[str, dict[str, float]] = {}
    for action, model in models.items():
        tree_values = np.asarray([float(tree.predict(x)[0]) for tree in model.estimators_], dtype=float)
        predictions[action] = {
            "medianAdvantage": float(np.median(tree_values)),
            "q20Advantage": float(np.quantile(tree_values, 0.2)),
            "meanAdvantage": float(np.mean(tree_values)),
        }
    eligible = [action for action in NON_WAIT if predictions[action]["q20Advantage"] > 0.0]
    selected = max(eligible, key=lambda action: predictions[action]["medianAdvantage"]) if eligible else WAIT
    return {"selectedAction": selected, "predictions": predictions}


def evaluate(models: dict[str, RandomForestRegressor], contexts: list[dict[str, Any]], split: str) -> dict[str, Any]:
    decisions = []
    for row in contexts:
        prediction = predict_context(models, row)
        selected = prediction["selectedAction"]
        decisions.append(
            {
                "split": split,
                "marketId": row["marketId"],
                "faultId": row["faultId"],
                "stateHash": row["stateHash"],
                **prediction,
                "selectedWorstCaseFloor": row["actionWorstCaseFloors"][selected],
                "waitWorstCaseFloor": row["actionWorstCaseFloors"][WAIT],
                "realizedAdvantageVsWait": row["actionFloorAdvantagesVsWait"][selected],
                "oracleAction": row["oracleAction"],
                "oracleWorstCaseFloor": row["oracleWorstCaseFloor"],
                "oracleAdvantageVsWait": row["oracleAdvantageVsWait"],
                "selectedSemanticPass": row["actionSemanticPass"][selected],
            }
        )
    action_counts = Counter(row["selectedAction"] for row in decisions)
    return {
        "split": split,
        "contexts": len(decisions),
        "decisions": decisions,
        "actionCounts": {action: action_counts.get(action, 0) for action in ACTIONS},
        "actRate": sum(row["selectedAction"] != WAIT for row in decisions) / len(decisions) if decisions else 0.0,
        "policyWorstCaseFloor": sum(row["selectedWorstCaseFloor"] for row in decisions),
        "waitWorstCaseFloor": sum(row["waitWorstCaseFloor"] for row in decisions),
        "policyAdvantageVsWait": sum(row["realizedAdvantageVsWait"] for row in decisions),
        "oracleWorstCaseFloor": sum(row["oracleWorstCaseFloor"] for row in decisions),
        "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in decisions),
        "semanticViolationDecisions": sum(not row["selectedSemanticPass"] for row in decisions),
    }


def fixed_action_baselines(contexts: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        action: {
            "worstCaseFloor": sum(row["actionWorstCaseFloors"][action] for row in contexts),
            "advantageVsWait": sum(row["actionFloorAdvantagesVsWait"][action] for row in contexts),
        }
        for action in ACTIONS
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default="hft_r2_fault_conditioned_recovery_train12_validation6_v1_report.json")
    parser.add_argument("--output", default="hft_r2_fault_conditioned_recovery_policy_v1_report.json")
    parser.add_argument("--model-output", default="hft_r2_fault_conditioned_recovery_policy_v1.joblib")
    args = parser.parse_args()
    source = json.loads((BASE / args.matrix).read_text(encoding="utf-8"))
    train_all = build_contexts(source, TRAIN_MARKETS)
    train = dedupe_train(train_all)
    validation = build_contexts(source, VALIDATION_MARKETS)
    if len(train) < 12 or len(validation) < 6:
        raise RuntimeError("insufficient matched chronological contexts")
    models = train_models(train)
    train_eval = evaluate(models, train, "train_unique_state")
    validation_eval = evaluate(models, validation, "chronological_validation")
    validation_gate = bool(
        validation_eval["policyWorstCaseFloor"] > 0.0
        and validation_eval["policyAdvantageVsWait"] > 0.0
        and validation_eval["actRate"] > 0.0
        and validation_eval["semanticViolationDecisions"] == 0
    )
    decision = "KEEP_FREEZE_FOR_HOLDOUT" if validation_gate else "REJECT_BEFORE_HOLDOUT"
    artifact = {
        "version": VERSION,
        "features": list(FEATURE_NAMES),
        "actions": ACTIONS,
        "models": models,
        "policy": "highest median action advantage only when its per-tree q20 is strictly positive; otherwise WAIT_PRESERVE",
        "seed": SEED,
        "trainMarkets": TRAIN_MARKETS,
        "validationMarkets": VALIDATION_MARKETS,
        "trainingStateHashes": [row["stateHash"] for row in train],
    }
    joblib.dump(artifact, BASE / args.model_output)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_fault_conditioned_recovery_matrix_v1_preregistered.json",
        "sourceMatrix": args.matrix,
        "modelArtifact": args.model_output,
        "chronology": {
            "trainMarkets": list(TRAIN_MARKETS),
            "validationMarkets": list(VALIDATION_MARKETS),
            "holdoutMarketsUsed": False,
            "globallyOpenedButContractUnseen": True,
        },
        "learner": {
            "family": "per-action random-forest advantage regression",
            "treesPerAction": 300,
            "minSamplesLeaf": 3,
            "maxFeatures": 0.7,
            "seed": SEED,
            "decisionRule": artifact["policy"],
            "validationThresholdSweep": False,
            "trainContextsBeforeStateDedup": len(train_all),
            "trainUniqueStates": len(train),
        },
        "train": train_eval,
        "validation": validation_eval,
        "fixedBaselines": {
            "trainUniqueState": fixed_action_baselines(train),
            "validation": fixed_action_baselines(validation),
        },
        "validationGatePass": validation_gate,
        "decision": decision,
        "next": "Generate and score the six later contract-unseen holdout markets only if decision is KEEP_FREEZE_FOR_HOLDOUT. Do not alter model, features, q20, forest parameters or action set after validation reveal.",
    }
    (BASE / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(BASE / args.output),
                "model": str(BASE / args.model_output),
                "train": {key: train_eval[key] for key in ("contexts", "actionCounts", "actRate", "policyWorstCaseFloor", "waitWorstCaseFloor", "policyAdvantageVsWait", "oracleWorstCaseFloor")},
                "validation": {key: validation_eval[key] for key in ("contexts", "actionCounts", "actRate", "policyWorstCaseFloor", "waitWorstCaseFloor", "policyAdvantageVsWait", "oracleWorstCaseFloor")},
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
