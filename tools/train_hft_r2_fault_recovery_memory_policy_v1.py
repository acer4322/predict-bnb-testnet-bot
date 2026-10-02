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
from tools.train_hft_r2_fault_conditioned_recovery_policy_v1 import (  # noqa: E402
    TRAIN_MARKETS,
    VALIDATION_MARKETS,
    build_contexts,
    fixed_action_baselines,
)


VERSION = "HFT_R2_FAULT_RECOVERY_MEMORY_POLICY_V1"
WAIT = "WAIT_PRESERVE"
NON_WAIT = tuple(action for action in ACTIONS if action != WAIT)
SEED = 20260823


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def join_memory(contexts: list[dict[str, Any]], memory: dict[str, Any]) -> list[dict[str, Any]]:
    by_key = {
        (int(row["marketId"]), str(row["faultId"])): row
        for row in memory["rows"]
    }
    joined = []
    for context in contexts:
        key = (int(context["marketId"]), str(context["faultId"]))
        row = by_key.get(key)
        if row is None:
            raise RuntimeError(f"memory dataset missing context {key}")
        if str(row["pointStateHash"]) != str(context["stateHash"]):
            raise RuntimeError(f"point state hash mismatch at {key}")
        joined.append(
            {
                **context,
                "memoryStateHash": str(row["memoryStateHash"]),
                "memoryFeatures": {name: finite(value) for name, value in row["memoryFeatures"].items()},
                "historyAudit": row["historyAudit"],
                "waitFloorMatchesPriorMatrix": bool(row["waitFloorMatchesPriorMatrix"]),
            }
        )
    return joined


def dedupe_train(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in contexts:
        key = row["memoryStateHash"]
        prior = unique.get(key)
        if prior is not None:
            if prior["actionFloorAdvantagesVsWait"] != row["actionFloorAdvantagesVsWait"]:
                raise RuntimeError(f"duplicate memory state has inconsistent outcomes: {key}")
            continue
        unique[key] = row
    return list(unique.values())


def raw_feature_names(memory: dict[str, Any]) -> list[str]:
    return list(FEATURE_NAMES) + [f"memory::{name}" for name in memory["memoryFeatureNames"]]


def raw_vector(row: dict[str, Any], names: list[str]) -> list[float]:
    result = []
    for name in names:
        if name.startswith("memory::"):
            result.append(finite(row["memoryFeatures"].get(name.split("::", 1)[1])))
        else:
            result.append(finite(row["features"].get(name)))
    return result


def fit_feature_filter(contexts: list[dict[str, Any]], names: list[str]) -> tuple[list[str], list[str], list[str]]:
    matrix = np.asarray([raw_vector(row, names) for row in contexts], dtype=float)
    constant = [name for index, name in enumerate(names) if np.all(matrix[:, index] == matrix[0, index])]
    retained: list[str] = []
    duplicate: list[str] = []
    retained_vectors: list[np.ndarray] = []
    for index, name in enumerate(names):
        if name in constant:
            continue
        vector = matrix[:, index]
        if any(np.array_equal(vector, prior) for prior in retained_vectors):
            duplicate.append(name)
            continue
        retained.append(name)
        retained_vectors.append(vector)
    return retained, constant, duplicate


def feature_matrix(contexts: list[dict[str, Any]], names: list[str]) -> np.ndarray:
    return np.asarray([raw_vector(row, names) for row in contexts], dtype=float)


def train_models(contexts: list[dict[str, Any]], names: list[str]) -> dict[str, RandomForestRegressor]:
    x = feature_matrix(contexts, names)
    models: dict[str, RandomForestRegressor] = {}
    for index, action in enumerate(NON_WAIT):
        y = np.asarray([finite(row["actionFloorAdvantagesVsWait"].get(action)) for row in contexts])
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


def predict_context(
    models: dict[str, RandomForestRegressor],
    row: dict[str, Any],
    names: list[str],
) -> dict[str, Any]:
    x = feature_matrix([row], names)
    predictions: dict[str, dict[str, float]] = {}
    for action, model in models.items():
        tree_values = np.asarray([float(tree.predict(x)[0]) for tree in model.estimators_])
        predictions[action] = {
            "medianAdvantage": float(np.median(tree_values)),
            "q20Advantage": float(np.quantile(tree_values, 0.2)),
            "meanAdvantage": float(np.mean(tree_values)),
        }
    eligible = [action for action in NON_WAIT if predictions[action]["q20Advantage"] > 0.0]
    selected = max(eligible, key=lambda action: predictions[action]["medianAdvantage"]) if eligible else WAIT
    return {"selectedAction": selected, "predictions": predictions}


def evaluate(
    models: dict[str, RandomForestRegressor],
    contexts: list[dict[str, Any]],
    names: list[str],
    split: str,
) -> dict[str, Any]:
    decisions = []
    for row in contexts:
        prediction = predict_context(models, row, names)
        selected = prediction["selectedAction"]
        decisions.append(
            {
                "split": split,
                "marketId": row["marketId"],
                "faultId": row["faultId"],
                "pointStateHash": row["stateHash"],
                "memoryStateHash": row["memoryStateHash"],
                "historyAnchorKind": row["historyAudit"]["anchorKind"],
                "historyDurationMs": row["memoryFeatures"]["historyDurationMs"],
                "historyTraceCount": row["memoryFeatures"]["historyTraceCount"],
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
    counts = Counter(row["selectedAction"] for row in decisions)
    return {
        "split": split,
        "contexts": len(decisions),
        "decisions": decisions,
        "actionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "actRate": sum(row["selectedAction"] != WAIT for row in decisions) / len(decisions) if decisions else 0.0,
        "policyWorstCaseFloor": sum(row["selectedWorstCaseFloor"] for row in decisions),
        "waitWorstCaseFloor": sum(row["waitWorstCaseFloor"] for row in decisions),
        "policyAdvantageVsWait": sum(row["realizedAdvantageVsWait"] for row in decisions),
        "oracleWorstCaseFloor": sum(row["oracleWorstCaseFloor"] for row in decisions),
        "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in decisions),
        "semanticViolationDecisions": sum(not row["selectedSemanticPass"] for row in decisions),
        "positiveOracleContexts": sum(row["oracleAdvantageVsWait"] > 1e-8 for row in decisions),
        "selectedPositiveContexts": sum(row["realizedAdvantageVsWait"] > 1e-8 for row in decisions),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        default="hft_r2_fault_conditioned_recovery_train12_validation6_v1_report.json",
    )
    parser.add_argument("--memory", required=True)
    parser.add_argument("--extra-matrix")
    parser.add_argument("--extra-memory")
    parser.add_argument("--extra-train-markets")
    parser.add_argument("--output", default="hft_r2_fault_recovery_memory_policy_v1_report.json")
    parser.add_argument("--model-output", default="hft_r2_fault_recovery_memory_policy_v1.joblib")
    args = parser.parse_args()

    matrix = json.loads((BASE / args.matrix).read_text(encoding="utf-8"))
    memory = json.loads((BASE / args.memory).read_text(encoding="utf-8"))
    if not memory.get("complete"):
        raise RuntimeError("memory dataset is incomplete")
    train_all = join_memory(build_contexts(matrix, TRAIN_MARKETS), memory)
    validation = join_memory(build_contexts(matrix, VALIDATION_MARKETS), memory)
    extra_train_markets: tuple[int, ...] = ()
    extra_matrix_name = None
    extra_memory_name = None
    if args.extra_matrix or args.extra_memory or args.extra_train_markets:
        if not (args.extra_matrix and args.extra_memory and args.extra_train_markets):
            raise RuntimeError("extra matrix, memory and train markets must be provided together")
        extra_train_markets = tuple(
            int(value) for value in args.extra_train_markets.split(",") if value.strip()
        )
        extra_matrix_name = str(args.extra_matrix)
        extra_memory_name = str(args.extra_memory)
        extra_matrix = json.loads((BASE / extra_matrix_name).read_text(encoding="utf-8"))
        extra_memory = json.loads((BASE / extra_memory_name).read_text(encoding="utf-8"))
        if not extra_memory.get("complete"):
            raise RuntimeError("extra memory dataset is incomplete")
        if list(extra_memory["memoryFeatureNames"]) != list(memory["memoryFeatureNames"]):
            raise RuntimeError("extra memory feature contract differs from primary memory dataset")
        train_all = join_memory(
            build_contexts(extra_matrix, extra_train_markets), extra_memory
        ) + train_all
    train = dedupe_train(train_all)
    if len(train) < 12 or len(validation) < 6:
        raise RuntimeError("insufficient matched chronological memory contexts")
    names_all = raw_feature_names(memory)
    names, constant_features, duplicate_features = fit_feature_filter(train, names_all)
    models = train_models(train, names)
    train_eval = evaluate(models, train, names, "train_unique_memory_state")
    validation_eval = evaluate(models, validation, names, "chronological_validation")
    validation_gate = bool(
        validation_eval["policyWorstCaseFloor"] > 0.0
        and validation_eval["policyAdvantageVsWait"] > 0.0
        and validation_eval["actRate"] > 0.0
        and validation_eval["semanticViolationDecisions"] == 0
    )
    decision = (
        "KEEP_FREEZE_MEMORY_POLICY_V1_FOR_BLIND_HOLDOUT"
        if validation_gate
        else "REJECT_MEMORY_POLICY_V1_BEFORE_HOLDOUT"
    )
    artifact = {
        "version": VERSION,
        "features": names,
        "rawFeatures": names_all,
        "trainOnlyConstantFeaturesRemoved": constant_features,
        "trainOnlyDuplicateFeaturesRemoved": duplicate_features,
        "actions": ACTIONS,
        "models": models,
        "policy": "highest median action advantage only when its per-tree q20 is strictly positive; otherwise WAIT_PRESERVE",
        "seed": SEED,
        "trainMarkets": extra_train_markets + TRAIN_MARKETS,
        "validationMarkets": VALIDATION_MARKETS,
        "trainingMemoryStateHashes": [row["memoryStateHash"] for row in train],
    }
    joblib.dump(artifact, BASE / args.model_output)

    point_report_path = BASE / "hft_r2_fault_conditioned_recovery_policy_v1_report.json"
    point_reference = None
    if point_report_path.exists():
        point = json.loads(point_report_path.read_text(encoding="utf-8"))
        point_reference = {
            key: point["validation"][key]
            for key in (
                "actionCounts",
                "actRate",
                "policyWorstCaseFloor",
                "waitWorstCaseFloor",
                "policyAdvantageVsWait",
                "oracleWorstCaseFloor",
            )
        }
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_fault_recovery_memory_v1_preregistered.json",
        "sourceMatrix": [name for name in (extra_matrix_name, args.matrix) if name is not None],
        "sourceMemory": [name for name in (extra_memory_name, args.memory) if name is not None],
        "modelArtifact": args.model_output,
        "chronology": {
            "trainMarkets": list(extra_train_markets + TRAIN_MARKETS),
            "validationMarkets": list(VALIDATION_MARKETS),
            "holdoutMarketsUsed": False,
            "validationPreviouslyRevealedForPointModel": True,
            "validationThresholdOrHyperparameterSweep": False,
        },
        "featureContract": {
            "rawFeatureCount": len(names_all),
            "retainedFeatureCount": len(names),
            "pointFeatureCount": len(FEATURE_NAMES),
            "memoryFeatureCount": len(memory["memoryFeatureNames"]),
            "trainOnlyConstantFeaturesRemoved": constant_features,
            "trainOnlyDuplicateFeaturesRemoved": duplicate_features,
            "faultIdAsFeature": False,
            "futureTraceExcluded": True,
            "winnerOrPnlInput": False,
        },
        "learner": {
            "family": "per-action random-forest advantage regression on point plus recovery memory",
            "treesPerAction": 300,
            "minSamplesLeaf": 3,
            "maxFeatures": 0.7,
            "seed": SEED,
            "decisionRule": artifact["policy"],
            "trainContextsBeforeMemoryDedup": len(train_all),
            "trainUniqueMemoryStates": len(train),
        },
        "train": train_eval,
        "validation": validation_eval,
        "pointPolicyValidationReference": point_reference,
        "fixedBaselines": {
            "trainUniqueMemoryState": fixed_action_baselines(train),
            "validation": fixed_action_baselines(validation),
        },
        "validationGatePass": validation_gate,
        "decision": decision,
        "next": (
            "Freeze features/model and collect holdout strict-past WAIT histories before any holdout action outcome is generated."
            if validation_gate
            else "Do not reveal holdout. Reject this fixed memory representation and pivot away from another forest/deeper model on the same outcomes."
        ),
    }
    (BASE / args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(BASE / args.output),
                "model": str(BASE / args.model_output),
                "featureCounts": {
                    "raw": len(names_all),
                    "retained": len(names),
                    "point": len(FEATURE_NAMES),
                    "memory": len(memory["memoryFeatureNames"]),
                },
                "train": {
                    key: train_eval[key]
                    for key in (
                        "contexts",
                        "actionCounts",
                        "actRate",
                        "policyWorstCaseFloor",
                        "waitWorstCaseFloor",
                        "policyAdvantageVsWait",
                        "oracleWorstCaseFloor",
                    )
                },
                "validation": {
                    key: validation_eval[key]
                    for key in (
                        "contexts",
                        "actionCounts",
                        "actRate",
                        "policyWorstCaseFloor",
                        "waitWorstCaseFloor",
                        "policyAdvantageVsWait",
                        "oracleWorstCaseFloor",
                    )
                },
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
