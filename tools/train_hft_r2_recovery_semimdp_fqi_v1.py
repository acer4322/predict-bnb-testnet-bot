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

from tools.hft_r2_cycle_option_program_dataset_v1 import (  # noqa: E402
    OBSERVATION_FEATURES,
    observation_vector,
)
from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import ACTIONS, BASE  # noqa: E402


VERSION = "HFT_R2_RECOVERY_SEMIMDP_FQI_V1"
WAIT = "WAIT_PRESERVE"
NON_WAIT = tuple(action for action in ACTIONS if action != WAIT)
ITERATIONS = 20
TREES = 500
MIN_LEAF = 5
MAX_FEATURES = 0.7
SEED = 20260823
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def floor_of_state(state: dict[str, Any]) -> float:
    return finite((state.get("actualPortfolio") or {}).get("worst_case_floor"))


def build_transitions(report: dict[str, Any], split: str) -> list[dict[str, Any]]:
    records = []
    for row in report["rows"]:
        options = list(row.get("optionTransitions") or [])
        if not options:
            raise RuntimeError("trajectory has no option transitions")
        episode_rewards = []
        for index, option in enumerate(options):
            state = np.asarray(observation_vector(option["startState"]), dtype=float)
            if index + 1 < len(options):
                next_option = options[index + 1]
                next_state_dict = next_option["startState"]
                end_floor = floor_of_state(next_state_dict)
                duration_ms = int(next_option["startAtMs"]) - int(option["startAtMs"])
                terminal = False
            else:
                next_state_dict = option["nextState"]
                end_floor = finite(row["terminalWorstCaseFloor"])
                duration_ms = int(option["endAtMs"]) - int(option["startAtMs"])
                terminal = True
            reward = end_floor - floor_of_state(option["startState"])
            episode_rewards.append(reward)
            records.append(
                {
                    "split": split,
                    "marketId": int(row["marketId"]),
                    "faultId": str(row["faultId"]),
                    "runAction": str(row["actionId"]),
                    "optionIndex": index,
                    "pointStateHash": str(option["pointStateHash"]),
                    "state": state,
                    "nextState": np.asarray(observation_vector(next_state_dict), dtype=float),
                    "reward": float(reward),
                    "terminal": bool(terminal),
                    "truncated": bool(option.get("censored")),
                    "durationMs": int(duration_ms),
                }
            )
        expected = finite(row["terminalWorstCaseFloor"]) - floor_of_state(options[0]["startState"])
        if abs(sum(episode_rewards) - expected) > EPS:
            raise RuntimeError(
                f"episode reward does not telescope for {row['marketId']}/{row['faultId']}/{row['actionId']}"
            )
    return records


def matrices(records: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.asarray([row["state"] for row in records], dtype=float),
        np.asarray([row["nextState"] for row in records], dtype=float),
        np.asarray([row["reward"] for row in records], dtype=float),
        np.asarray([row["terminal"] or row["truncated"] for row in records], dtype=bool),
    )


def fit_fqi(records: list[dict[str, Any]]) -> tuple[dict[str, RandomForestRegressor], list[dict[str, float]]]:
    state, next_state, reward, terminal = matrices(records)
    actions = np.asarray([row["runAction"] for row in records], dtype=object)
    prior_models: dict[str, RandomForestRegressor] | None = None
    prior_q = np.zeros(len(records), dtype=float)
    history = []
    for iteration in range(ITERATIONS):
        if prior_models is None:
            bootstrap = np.zeros(len(records), dtype=float)
        else:
            next_values = np.column_stack(
                [prior_models[action].predict(next_state) for action in ACTIONS]
            )
            bootstrap = np.max(next_values, axis=1)
        target = reward + np.where(terminal, 0.0, bootstrap)
        models: dict[str, RandomForestRegressor] = {}
        for action_index, action in enumerate(ACTIONS):
            mask = actions == action
            if int(mask.sum()) < MIN_LEAF * 2:
                raise RuntimeError(f"insufficient transition support for {action}: {int(mask.sum())}")
            model = RandomForestRegressor(
                n_estimators=TREES,
                min_samples_leaf=MIN_LEAF,
                max_features=MAX_FEATURES,
                bootstrap=True,
                random_state=SEED + iteration * 10 + action_index,
                n_jobs=-1,
            )
            model.fit(state[mask], target[mask])
            models[action] = model
        fitted_q = np.asarray(
            [models[action].predict(state[index : index + 1])[0] for index, action in enumerate(actions)]
        )
        history.append(
            {
                "iteration": float(iteration + 1),
                "meanAbsBellmanResidual": float(np.mean(np.abs(fitted_q - target))),
                "meanAbsQChange": float(np.mean(np.abs(fitted_q - prior_q))),
                "meanTarget": float(np.mean(target)),
                "meanFittedQ": float(np.mean(fitted_q)),
            }
        )
        prior_models = models
        prior_q = fitted_q
    assert prior_models is not None
    return prior_models, history


def predict_action(models: dict[str, RandomForestRegressor], state: np.ndarray) -> dict[str, Any]:
    x = np.asarray(state, dtype=float).reshape(1, -1)
    tree_values = {
        action: np.asarray([float(tree.predict(x)[0]) for tree in models[action].estimators_])
        for action in ACTIONS
    }
    wait_values = tree_values[WAIT]
    predictions = {}
    for action in ACTIONS:
        values = tree_values[action]
        advantage = values - wait_values
        predictions[action] = {
            "medianQ": float(np.median(values)),
            "meanQ": float(np.mean(values)),
            "medianAdvantageVsWait": float(np.median(advantage)),
            "q20AdvantageVsWait": float(np.quantile(advantage, 0.2)),
        }
    eligible = [
        action for action in NON_WAIT if predictions[action]["q20AdvantageVsWait"] > 0.0
    ]
    selected = (
        max(eligible, key=lambda action: predictions[action]["medianAdvantageVsWait"])
        if eligible
        else WAIT
    )
    return {"selectedAction": selected, "predictions": predictions}


def first_contexts(report: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for row in report["rows"]:
        grouped.setdefault((int(row["marketId"]), str(row["faultId"])), {})[
            str(row["actionId"])
        ] = row
    contexts = []
    for (market_id, fault_id), by_action in sorted(grouped.items()):
        if set(by_action) != set(ACTIONS):
            raise RuntimeError(f"incomplete action context at {market_id}/{fault_id}")
        hashes = {
            str(row["optionTransitions"][0]["pointStateHash"]) for row in by_action.values()
        }
        if len(hashes) != 1:
            raise RuntimeError(f"unmatched first SMDP states at {market_id}/{fault_id}")
        wait = by_action[WAIT]
        wait_floor = finite(wait["terminalWorstCaseFloor"])
        floors = {action: finite(row["terminalWorstCaseFloor"]) for action, row in by_action.items()}
        advantages = {action: floor - wait_floor for action, floor in floors.items()}
        oracle = max(advantages, key=lambda action: (advantages[action], action == WAIT))
        contexts.append(
            {
                "marketId": market_id,
                "faultId": fault_id,
                "pointStateHash": next(iter(hashes)),
                "state": np.asarray(
                    observation_vector(wait["optionTransitions"][0]["startState"]), dtype=float
                ),
                "actionWorstCaseFloors": floors,
                "actionAdvantagesVsWait": advantages,
                "actionSemanticPass": {
                    action: int(row["cycleInvariantViolationCount"]) == 0
                    for action, row in by_action.items()
                },
                "oracleAction": oracle,
                "oracleWorstCaseFloor": floors[oracle],
                "oracleAdvantageVsWait": advantages[oracle],
            }
        )
    return contexts


def evaluate(
    models: dict[str, RandomForestRegressor], contexts: list[dict[str, Any]], split: str
) -> dict[str, Any]:
    decisions = []
    for context in contexts:
        prediction = predict_action(models, context["state"])
        action = prediction["selectedAction"]
        decisions.append(
            {
                "split": split,
                "marketId": context["marketId"],
                "faultId": context["faultId"],
                "pointStateHash": context["pointStateHash"],
                **prediction,
                "selectedWorstCaseFloor": context["actionWorstCaseFloors"][action],
                "waitWorstCaseFloor": context["actionWorstCaseFloors"][WAIT],
                "realizedAdvantageVsWait": context["actionAdvantagesVsWait"][action],
                "oracleAction": context["oracleAction"],
                "oracleWorstCaseFloor": context["oracleWorstCaseFloor"],
                "oracleAdvantageVsWait": context["oracleAdvantageVsWait"],
                "selectedSemanticPass": context["actionSemanticPass"][action],
            }
        )
    counts = Counter(row["selectedAction"] for row in decisions)
    return {
        "split": split,
        "contexts": len(decisions),
        "decisions": decisions,
        "actionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "actRate": sum(row["selectedAction"] != WAIT for row in decisions) / len(decisions),
        "policyWorstCaseFloor": sum(row["selectedWorstCaseFloor"] for row in decisions),
        "waitWorstCaseFloor": sum(row["waitWorstCaseFloor"] for row in decisions),
        "policyAdvantageVsWait": sum(row["realizedAdvantageVsWait"] for row in decisions),
        "oracleWorstCaseFloor": sum(row["oracleWorstCaseFloor"] for row in decisions),
        "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in decisions),
        "semanticViolationDecisions": sum(not row["selectedSemanticPass"] for row in decisions),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="hft_r2_recovery_semimdp_train26_v1_report.json")
    parser.add_argument("--validation", default="hft_r2_recovery_semimdp_validation6_v1_report.json")
    parser.add_argument("--output", default="hft_r2_recovery_semimdp_fqi_v1_report.json")
    parser.add_argument("--model-output", default="hft_r2_recovery_semimdp_fqi_v1.joblib")
    args = parser.parse_args()
    train_report = json.loads((BASE / args.train).read_text(encoding="utf-8"))
    validation_report = json.loads((BASE / args.validation).read_text(encoding="utf-8"))
    train_records = build_transitions(train_report, "train")
    train_contexts = first_contexts(train_report)
    validation_contexts = first_contexts(validation_report)
    models, bellman_history = fit_fqi(train_records)
    train_eval = evaluate(models, train_contexts, "train_first_matched_state")
    validation_eval = evaluate(models, validation_contexts, "chronological_validation")
    validation_gate = bool(
        validation_eval["policyWorstCaseFloor"] > 0.0
        and validation_eval["policyAdvantageVsWait"] > 0.0
        and validation_eval["actRate"] > 0.0
        and validation_eval["semanticViolationDecisions"] == 0
    )
    decision = (
        "KEEP_FREEZE_SEMIMDP_FQI_V1_FOR_BLIND_HOLDOUT"
        if validation_gate
        else "REJECT_SEMIMDP_FQI_V1_BEFORE_HOLDOUT"
    )
    artifact = {
        "version": VERSION,
        "observationFeatures": OBSERVATION_FEATURES,
        "actions": ACTIONS,
        "models": models,
        "iterations": ITERATIONS,
        "treesPerAction": TREES,
        "minSamplesLeaf": MIN_LEAF,
        "maxFeatures": MAX_FEATURES,
        "gamma": 1.0,
        "seed": SEED,
        "decisionRule": "highest median paired-tree Q advantage only when paired-tree q20 advantage over WAIT is positive; otherwise WAIT",
    }
    joblib.dump(artifact, BASE / args.model_output)
    transition_counts = Counter(row["runAction"] for row in train_records)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_recovery_semimdp_fqi_v1_preregistered.json",
        "sourceTrain": args.train,
        "sourceValidation": args.validation,
        "modelArtifact": args.model_output,
        "chronology": {
            "trainMarkets": sorted({row["marketId"] for row in train_records}),
            "validationMarkets": sorted({row["marketId"] for row in validation_contexts}),
            "holdoutMarketsUsed": False,
        },
        "dataset": {
            "episodes": len(train_report["rows"]),
            "transitions": len(train_records),
            "transitionsByAction": {action: transition_counts.get(action, 0) for action in ACTIONS},
            "observationFeatures": len(OBSERVATION_FEATURES),
            "censoredTransitions": sum(row["truncated"] for row in train_records),
            "negativeDurationTransitions": sum(row["durationMs"] < 0 for row in train_records),
            "winnerOrPnlInput": False,
            "faultIdAsFeature": False,
        },
        "learner": {
            "family": "per-action random-forest fitted Q iteration",
            "iterations": ITERATIONS,
            "treesPerAction": TREES,
            "minSamplesLeaf": MIN_LEAF,
            "maxFeatures": MAX_FEATURES,
            "gamma": 1.0,
            "seed": SEED,
            "decisionRule": artifact["decisionRule"],
            "validationSweep": False,
            "bellmanHistory": bellman_history,
        },
        "train": train_eval,
        "validation": validation_eval,
        "validationGatePass": validation_gate,
        "decision": decision,
        "next": (
            "Freeze model and first write blind holdout decisions before generating any holdout action outcomes."
            if validation_gate
            else "Do not reveal holdout and do not tune FQI iterations, forest or q20 on this validation."
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
                "dataset": report["dataset"],
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
