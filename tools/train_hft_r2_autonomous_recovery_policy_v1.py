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

from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import ACTIONS, BASE  # noqa: E402


VERSION = "HFT_R2_AUTONOMOUS_RECOVERY_POLICY_V1"
WAIT = "WAIT_PRESERVE"
NON_WAIT = tuple(action for action in ACTIONS if action != WAIT)
INNER_VALIDATION_MARKETS = {1577181, 1577392, 1577751, 1577937, 1578351, 1578546}
TREES = 500
MIN_LEAF = 5
MAX_FEATURES = 0.7
SEED = 20260824
EPS = 1e-8


def fit_models(rows: list[dict[str, Any]], seed_offset: int = 0) -> dict[str, RandomForestRegressor]:
    x = np.asarray([row["observation"] for row in rows], dtype=float)
    models = {}
    for action_index, action in enumerate(NON_WAIT):
        y = np.asarray([row["actionAdvantagesVsWait"][action] for row in rows], dtype=float)
        model = RandomForestRegressor(
            n_estimators=TREES,
            min_samples_leaf=MIN_LEAF,
            max_features=MAX_FEATURES,
            bootstrap=True,
            random_state=SEED + seed_offset + action_index,
            n_jobs=-1,
        )
        model.fit(x, y)
        models[action] = model
    return models


def predict_action(models: dict[str, RandomForestRegressor], observation: list[float]) -> dict[str, Any]:
    x = np.asarray(observation, dtype=float).reshape(1, -1)
    predictions = {
        WAIT: {"medianAdvantageVsWait": 0.0, "meanAdvantageVsWait": 0.0, "q20AdvantageVsWait": 0.0}
    }
    for action in NON_WAIT:
        values = np.asarray([float(tree.predict(x)[0]) for tree in models[action].estimators_])
        predictions[action] = {
            "medianAdvantageVsWait": float(np.median(values)),
            "meanAdvantageVsWait": float(np.mean(values)),
            "q20AdvantageVsWait": float(np.quantile(values, 0.2)),
        }
    eligible = [action for action in NON_WAIT if predictions[action]["q20AdvantageVsWait"] > 0.0]
    selected = (
        max(eligible, key=lambda action: predictions[action]["medianAdvantageVsWait"])
        if eligible
        else WAIT
    )
    return {"selectedAction": selected, "predictions": predictions}


def evaluate(models: dict[str, RandomForestRegressor], rows: list[dict[str, Any]], split: str) -> dict[str, Any]:
    decisions = []
    for row in rows:
        prediction = predict_action(models, row["observation"])
        action = prediction["selectedAction"]
        decisions.append(
            {
                "split": split,
                "marketId": int(row["marketId"]),
                "optionIndex": int(row["optionIndex"]),
                "previousRecoveryAction": str(row["previousRecoveryAction"]),
                "stateHash": str(row["stateHash"]),
                **prediction,
                "selectedMatchedAdvantageVsWait": float(row["actionAdvantagesVsWait"][action]),
                "selectedMatchedWorstCaseFloor": float(row["actionWorstCaseFloors"][action]),
                "waitMatchedWorstCaseFloor": float(row["actionWorstCaseFloors"][WAIT]),
                "oracleAction": str(row["oracleAction"]),
                "oracleAdvantageVsWait": float(row["oracleAdvantageVsWait"]),
            }
        )
    counts = Counter(row["selectedAction"] for row in decisions)
    by_depth = {}
    for depth in (0, 1):
        part = [row for row in decisions if row["optionIndex"] == depth]
        by_depth[str(depth)] = {
            "contexts": len(part),
            "selectedAdvantageVsWait": sum(row["selectedMatchedAdvantageVsWait"] for row in part),
            "actRate": sum(row["selectedAction"] != WAIT for row in part) / len(part) if part else 0.0,
        }
    return {
        "split": split,
        "contexts": len(decisions),
        "decisions": decisions,
        "actionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "actRate": sum(row["selectedAction"] != WAIT for row in decisions) / len(decisions),
        "selectedAdvantageVsWait": sum(row["selectedMatchedAdvantageVsWait"] for row in decisions),
        "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in decisions),
        "byOptionIndex": by_depth,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="hft_r2_autonomous_recovery_branch_dataset_v1.json")
    parser.add_argument("--output", default="hft_r2_autonomous_recovery_policy_v1_report.json")
    parser.add_argument("--model-output", default="hft_r2_autonomous_recovery_policy_v1.joblib")
    args = parser.parse_args()
    dataset = json.loads((BASE / args.dataset).read_text(encoding="utf-8"))
    rows = dataset["contexts"]
    inner_train = [row for row in rows if int(row["marketId"]) not in INNER_VALIDATION_MARKETS]
    inner_validation = [row for row in rows if int(row["marketId"]) in INNER_VALIDATION_MARKETS]
    models = fit_models(inner_train)
    train_eval = evaluate(models, inner_train, "chronological_inner_train17")
    validation_eval = evaluate(models, inner_validation, "chronological_inner_validation6")
    inner_gate = bool(
        validation_eval["selectedAdvantageVsWait"] > 0.0
        and validation_eval["byOptionIndex"]["0"]["selectedAdvantageVsWait"] >= -EPS
        and validation_eval["byOptionIndex"]["1"]["selectedAdvantageVsWait"] >= -EPS
        and validation_eval["actRate"] > 0.0
    )
    final_models = fit_models(rows, seed_offset=100) if inner_gate else None
    artifact = {
        "version": VERSION,
        "observationFeatures": dataset["augmentedObservationFeatures"],
        "actions": ACTIONS,
        "models": final_models if final_models is not None else models,
        "trainedOnAllTrain23": bool(inner_gate),
        "maxLearnedRecoveryOptions": 2,
        "treesPerAction": TREES,
        "minSamplesLeaf": MIN_LEAF,
        "maxFeatures": MAX_FEATURES,
        "seed": SEED,
        "decisionRule": "highest median direct matched advantage among non-WAIT actions with tree q20 advantage > 0; otherwise WAIT",
    }
    joblib.dump(artifact, BASE / args.model_output)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_autonomous_recovery_policy_v1_preregistered.json",
        "dataset": args.dataset,
        "modelArtifact": args.model_output,
        "objectiveBoundary": "R2 autonomous execution repair only; Frozen R2 economic objective is unchanged.",
        "learner": {
            "family": "per-non-WAIT random-forest direct matched advantage regression",
            "treesPerAction": TREES,
            "minSamplesLeaf": MIN_LEAF,
            "maxFeatures": MAX_FEATURES,
            "seed": SEED,
            "decisionRule": artifact["decisionRule"],
            "sweep": False,
        },
        "chronology": {
            "innerTrainMarkets": sorted({int(row["marketId"]) for row in inner_train}),
            "innerValidationMarkets": sorted({int(row["marketId"]) for row in inner_validation}),
            "externalValidationUsed": False,
            "holdoutUsed": False,
        },
        "innerTrain": train_eval,
        "innerValidation": validation_eval,
        "innerGatePass": inner_gate,
        "trainedOnAllTrain23": bool(inner_gate),
        "decision": (
            "KEEP_FREEZE_FOR_ONE_CLOSED_LOOP_CHRONOLOGICAL_VALIDATION6"
            if inner_gate
            else "REJECT_AUTONOMOUS_RECOVERY_POLICY_V1_BEFORE_EXTERNAL_VALIDATION"
        ),
        "next": (
            "Run exactly one frozen two-recovery-option closed-loop replay on chronological validation6, then WAIT tail."
            if inner_gate
            else "Do not tune model or run external validation."
        ),
    }
    (BASE / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(BASE / args.output),
                "model": str(BASE / args.model_output),
                "datasetContexts": len(rows),
                "innerTrain": {key: train_eval[key] for key in ("contexts", "actionCounts", "actRate", "selectedAdvantageVsWait", "oracleAdvantageVsWait", "byOptionIndex")},
                "innerValidation": {key: validation_eval[key] for key in ("contexts", "actionCounts", "actRate", "selectedAdvantageVsWait", "oracleAdvantageVsWait", "byOptionIndex")},
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
