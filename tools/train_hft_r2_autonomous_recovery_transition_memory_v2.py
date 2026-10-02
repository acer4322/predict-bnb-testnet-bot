from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import joblib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import BASE  # noqa: E402
from tools.train_hft_r2_autonomous_recovery_policy_v1 import (  # noqa: E402
    ACTIONS,
    INNER_VALIDATION_MARKETS,
    MAX_FEATURES,
    MIN_LEAF,
    NON_WAIT,
    SEED,
    TREES,
    WAIT,
    fit_models,
    predict_action,
)


VERSION = "HFT_R2_AUTONOMOUS_RECOVERY_TRANSITION_MEMORY_POLICY_V2"


def evaluate(models: dict[str, Any], rows: list[dict[str, Any]], split: str) -> dict[str, Any]:
    decisions = []
    for row in rows:
        prediction = predict_action(models, row["observation"])
        action = prediction["selectedAction"]
        decisions.append(
            {
                "split": split,
                "marketId": int(row["marketId"]),
                "previousRecoveryAction": str(row["previousRecoveryAction"]),
                "stateHash": str(row["stateHash"]),
                **prediction,
                "selectedMatchedAdvantageVsWait": float(row["actionAdvantagesVsWait"][action]),
                "oracleAction": str(row["oracleAction"]),
                "oracleAdvantageVsWait": float(row["oracleAdvantageVsWait"]),
            }
        )
    counts = Counter(row["selectedAction"] for row in decisions)
    previous_wait = [row for row in decisions if row["previousRecoveryAction"] == WAIT]
    return {
        "split": split,
        "contexts": len(decisions),
        "decisions": decisions,
        "actionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "actRate": sum(row["selectedAction"] != WAIT for row in decisions) / len(decisions),
        "selectedAdvantageVsWait": sum(row["selectedMatchedAdvantageVsWait"] for row in decisions),
        "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in decisions),
        "previousWait": {
            "contexts": len(previous_wait),
            "actCount": sum(row["selectedAction"] != WAIT for row in previous_wait),
            "actRate": sum(row["selectedAction"] != WAIT for row in previous_wait) / len(previous_wait) if previous_wait else 0.0,
            "selectedAdvantageVsWait": sum(row["selectedMatchedAdvantageVsWait"] for row in previous_wait),
            "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in previous_wait),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", default="hft_r2_autonomous_recovery_transition_memory_v2.json"
    )
    parser.add_argument(
        "--output", default="hft_r2_autonomous_recovery_transition_memory_policy_v2_report.json"
    )
    parser.add_argument(
        "--model-output", default="hft_r2_autonomous_recovery_transition_memory_policy_v2.joblib"
    )
    args = parser.parse_args()
    dataset = json.loads((BASE / args.dataset).read_text(encoding="utf-8"))
    rows = dataset["contexts"]
    inner_train = [row for row in rows if int(row["marketId"]) not in INNER_VALIDATION_MARKETS]
    inner_validation = [row for row in rows if int(row["marketId"]) in INNER_VALIDATION_MARKETS]
    models = fit_models(inner_train)
    train_eval = evaluate(models, inner_train, "chronological_inner_train17")
    validation_eval = evaluate(models, inner_validation, "chronological_inner_validation6")
    gate = bool(
        validation_eval["previousWait"]["actCount"] > 0
        and validation_eval["previousWait"]["selectedAdvantageVsWait"] > 0.0
        and validation_eval["selectedAdvantageVsWait"] >= 0.0
    )
    final_models = fit_models(rows, seed_offset=200) if gate else models
    artifact = {
        "version": VERSION,
        "features": dataset["features"],
        "actions": ACTIONS,
        "models": final_models,
        "trainedOnAllTrain23": gate,
        "firstRecoveryAction": WAIT,
        "maxLearnedRecoveryOptions": 1,
        "treesPerAction": TREES,
        "minSamplesLeaf": MIN_LEAF,
        "maxFeatures": MAX_FEATURES,
        "seed": SEED,
        "decisionRule": "mandatory first WAIT; at second recovery state choose highest median matched advantage with tree q20 > 0, otherwise WAIT; WAIT thereafter",
    }
    joblib.dump(artifact, BASE / args.model_output)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_autonomous_recovery_transition_memory_v2_preregistered.json",
        "dataset": args.dataset,
        "modelArtifact": args.model_output,
        "objectiveBoundary": "R2 autonomous repair after observed first WAIT outcome only; Frozen R2 objective unchanged.",
        "learner": {
            "treesPerAction": TREES,
            "minSamplesLeaf": MIN_LEAF,
            "maxFeatures": MAX_FEATURES,
            "seed": SEED,
            "sweep": False,
        },
        "innerTrain": train_eval,
        "innerValidation": validation_eval,
        "innerGatePass": gate,
        "trainedOnAllTrain23": gate,
        "externalValidationUsed": False,
        "holdoutUsed": False,
        "decision": (
            "KEEP_FREEZE_TRANSITION_MEMORY_V2_FOR_ONE_EXTERNAL_VALIDATION6"
            if gate
            else "REJECT_TRANSITION_MEMORY_V2_AND_SUPERVISED_RECOVERY_FAMILY"
        ),
        "next": (
            "Run one frozen closed-loop validation6 with first WAIT, learned second recovery action and WAIT tail."
            if gate
            else "Do not tune transition features, RF or q20 on this inner block; pivot away from supervised cross-market recovery prediction."
        ),
    }
    (BASE / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(BASE / args.output),
                "model": str(BASE / args.model_output),
                "innerTrain": {key: train_eval[key] for key in ("contexts", "actionCounts", "actRate", "selectedAdvantageVsWait", "oracleAdvantageVsWait", "previousWait")},
                "innerValidation": {key: validation_eval[key] for key in ("contexts", "actionCounts", "actRate", "selectedAdvantageVsWait", "oracleAdvantageVsWait", "previousWait")},
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
