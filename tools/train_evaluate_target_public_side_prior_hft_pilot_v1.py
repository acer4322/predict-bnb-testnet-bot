from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "target_public_side_prior_hft_pilot_v1_preregistered.json"
MODEL_PATH = BASE / "target_public_side_prior_hft_pilot_v1.joblib"
FROZEN_TARGET_REPORT = BASE / "target_public_side_prior_hft_pilot_v1_target_frozen_report.json"
DEFAULT_OUTPUT = BASE / "target_public_side_prior_hft_pilot_v1_report.json"
TARGET_FILES = [
    TARGET_DIR / "target_actionpoint_interrogation_v0_c0_20_maker.csv",
    TARGET_DIR / "target_actionpoint_interrogation_v0_c1_20_maker.csv",
    TARGET_DIR / "target_actionpoint_interrogation_v0_late20_maker.csv",
]
TARGET_FEATURES = ["secondsLeft", "directionScore", "up_bid", "up_ask", "down_bid", "down_ask"]
PUBLIC_FEATURES = ["secondsLeft", "directionScore", "upBid", "upAsk", "downBid", "downAsk"]
WAIT_CONFIDENCE = 0.60
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except Exception:
        return 0.0


def load_target_rows() -> pd.DataFrame:
    frames = [pd.read_csv(path) for path in TARGET_FILES]
    data = pd.concat(frames, ignore_index=True)
    if data["parentId"].duplicated().any():
        duplicates = int(data["parentId"].duplicated().sum())
        raise RuntimeError(f"unexpected duplicate Target parentId rows: {duplicates}")
    data["marketId"] = pd.to_numeric(data["marketId"], errors="raise").astype(int)
    data["marketEndMs"] = pd.to_numeric(data["marketEndMs"], errors="raise").astype(np.int64)
    data["labelUp"] = data["actionSide"].astype(str).str.upper().eq("UP").astype(int)
    for feature in TARGET_FEATURES:
        data[feature] = pd.to_numeric(data[feature], errors="coerce")
    return data.sort_values(["marketEndMs", "checkpointMs", "marketId", "parentId"]).reset_index(drop=True)


def make_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=30,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=20260823,
    )


def metrics(labels: pd.Series, probabilities: np.ndarray) -> dict[str, Any]:
    y = labels.astype(int).to_numpy()
    p = np.asarray(probabilities, dtype=float)
    predicted = (p >= 0.5).astype(int)
    return {
        "rows": int(len(y)),
        "upRows": int(y.sum()),
        "upRate": float(y.mean()),
        "accuracy": float(accuracy_score(y, predicted)),
        "balancedAccuracy": float(balanced_accuracy_score(y, predicted)),
        "rocAuc": float(roc_auc_score(y, p)) if len(set(y.tolist())) == 2 else None,
        "logLoss": float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=[0, 1])),
        "confidence60Coverage": float(np.mean(np.maximum(p, 1 - p) >= WAIT_CONFIDENCE)),
    }


def action_for(row: dict[str, Any], side: str, offset: int) -> dict[str, Any]:
    for action in row.get("actions") or []:
        if str(action.get("side") or "").upper() == side and int(action.get("offset")) == offset:
            return action
    raise RuntimeError(f"missing action {side}_{offset} at {row.get('marketId')}:{row.get('checkpointMs')}")


def best_with_wait(row: dict[str, Any], side: str | None = None) -> float:
    candidates = [
        finite(action.get("mtm1sUsdt"))
        for action in row.get("actions") or []
        if side is None or str(action.get("side") or "").upper() == side
    ]
    return max([0.0, *candidates])


def summarize_hft(dataset: dict[str, Any], model: HistGradientBoostingClassifier) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_rows = list(dataset.get("rows") or [])
    matrix = pd.DataFrame(
        [
            {
                target_feature: finite((row.get("features") or {}).get(public_feature))
                for target_feature, public_feature in zip(TARGET_FEATURES, PUBLIC_FEATURES)
            }
            for row in raw_rows
        ]
    )
    probabilities = model.predict_proba(matrix)[:, 1]
    audit_rows: list[dict[str, Any]] = []
    for row, probability_up in zip(raw_rows, probabilities):
        probability_up = float(probability_up)
        predicted_side = "UP" if probability_up >= 0.5 else "DOWN"
        opposite_side = "DOWN" if predicted_side == "UP" else "UP"
        confidence = max(probability_up, 1.0 - probability_up)
        learned_action = action_for(row, predicted_side, 0)
        direction_score = finite((row.get("features") or {}).get("directionScore"))
        direction_side = "UP" if direction_score >= 0.0 else "DOWN"
        direction_action = action_for(row, direction_side, 0)
        acts = confidence >= WAIT_CONFIDENCE
        audit_rows.append(
            {
                "marketId": int(row["marketId"]),
                "checkpointMs": int(row["checkpointMs"]),
                "secondsLeft": finite((row.get("features") or {}).get("secondsLeft")),
                "probabilityUp": probability_up,
                "confidence": confidence,
                "policyAction": f"{predicted_side}_0" if acts else "WAIT",
                "predictedSide": predicted_side,
                "directionScoreSide": direction_side,
                "chosenRewardMtm1sUsdt": finite(learned_action.get("mtm1sUsdt")) if acts else 0.0,
                "chosenFilledShares5s": finite(learned_action.get("filledShares5s")) if acts else 0.0,
                "matchedActDirectionRewardMtm1sUsdt": finite(direction_action.get("mtm1sUsdt")) if acts else 0.0,
                "alwaysPredictedSideOffset0RewardMtm1sUsdt": finite(learned_action.get("mtm1sUsdt")),
                "alwaysDirectionScoreOffset0RewardMtm1sUsdt": finite(direction_action.get("mtm1sUsdt")),
                "freeOracleRewardMtm1sUsdt": best_with_wait(row),
                "predictedSideOracleRewardMtm1sUsdt": best_with_wait(row, predicted_side),
                "oppositeSideOracleRewardMtm1sUsdt": best_with_wait(row, opposite_side),
            }
        )

    checkpoints = len(audit_rows)
    acted = [row for row in audit_rows if row["policyAction"] != "WAIT"]
    filled = [row for row in acted if finite(row["chosenFilledShares5s"]) > EPS]
    learned_reward = sum(finite(row["chosenRewardMtm1sUsdt"]) for row in audit_rows)
    matched_direction_reward = sum(finite(row["matchedActDirectionRewardMtm1sUsdt"]) for row in audit_rows)
    summary = {
        "markets": len({int(row["marketId"]) for row in audit_rows}),
        "checkpoints": checkpoints,
        "waitCheckpoints": checkpoints - len(acted),
        "actCheckpoints": len(acted),
        "actRate": len(acted) / checkpoints if checkpoints else 0.0,
        "chosenActionsWithActualFill": len(filled),
        "chosenFilledShares5s": sum(finite(row["chosenFilledShares5s"]) for row in acted),
        "positiveChosenActions": sum(finite(row["chosenRewardMtm1sUsdt"]) > EPS for row in acted),
        "negativeChosenActions": sum(finite(row["chosenRewardMtm1sUsdt"]) < -EPS for row in acted),
        "learnedPolicyRealizedMtm1sUsdt": learned_reward,
        "matchedActDirectionScoreRealizedMtm1sUsdt": matched_direction_reward,
        "alwaysPredictedSideOffset0Mtm1sUsdt": sum(
            finite(row["alwaysPredictedSideOffset0RewardMtm1sUsdt"]) for row in audit_rows
        ),
        "alwaysDirectionScoreOffset0Mtm1sUsdt": sum(
            finite(row["alwaysDirectionScoreOffset0RewardMtm1sUsdt"]) for row in audit_rows
        ),
        "freeOracleCeilingMtm1sUsdt": sum(finite(row["freeOracleRewardMtm1sUsdt"]) for row in audit_rows),
        "predictedSideOracleMtm1sUsdt": sum(
            finite(row["predictedSideOracleRewardMtm1sUsdt"]) for row in audit_rows
        ),
        "oppositeSideOracleMtm1sUsdt": sum(
            finite(row["oppositeSideOracleRewardMtm1sUsdt"]) for row in audit_rows
        ),
    }
    return summary, audit_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hft-dataset", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    preregistration = json.loads(PREREG.read_text(encoding="utf-8"))
    data = load_target_rows()
    markets = (
        data[["marketId", "marketEndMs"]]
        .drop_duplicates()
        .sort_values(["marketEndMs", "marketId"])["marketId"]
        .astype(int)
        .tolist()
    )
    if len(markets) != 58:
        raise RuntimeError(f"expected 58 unique Target markets, found {len(markets)}")
    train_markets = markets[:48]
    validation_markets = markets[48:]
    train_mask = data["marketId"].isin(train_markets)
    validation_mask = data["marketId"].isin(validation_markets)
    if args.hft_dataset is not None:
        artifact = joblib.load(MODEL_PATH)
        if artifact.get("trainMarkets") != train_markets or artifact.get("validationMarkets") != validation_markets:
            raise RuntimeError("frozen Target model split does not match current source data")
        if artifact.get("targetFeatureNames") != TARGET_FEATURES or artifact.get("runtimePublicFeatureNames") != PUBLIC_FEATURES:
            raise RuntimeError("frozen Target model feature contract mismatch")
        model = artifact["model"]
        frozen_report = json.loads(FROZEN_TARGET_REPORT.read_text(encoding="utf-8"))
        target_evaluation = frozen_report["targetEvaluation"]
    else:
        model = make_model()
        model.fit(data.loc[train_mask, TARGET_FEATURES], data.loc[train_mask, "labelUp"])
        train_probabilities = model.predict_proba(data.loc[train_mask, TARGET_FEATURES])[:, 1]
        validation_probabilities = model.predict_proba(data.loc[validation_mask, TARGET_FEATURES])[:, 1]
        target_evaluation = {
            "sourceRows": int(len(data)),
            "sourceMarkets": len(markets),
            "trainMarkets": train_markets,
            "validationMarkets": validation_markets,
            "train": metrics(data.loc[train_mask, "labelUp"], train_probabilities),
            "validation": metrics(data.loc[validation_mask, "labelUp"], validation_probabilities),
        }
        artifact = {
            "version": "TARGET_PUBLIC_SIDE_PRIOR_HFT_PILOT_V1",
            "researchOnly": True,
            "targetFeatureNames": TARGET_FEATURES,
            "runtimePublicFeatureNames": PUBLIC_FEATURES,
            "waitConfidence": WAIT_CONFIDENCE,
            "trainMarkets": train_markets,
            "validationMarkets": validation_markets,
            "model": model,
        }
        joblib.dump(artifact, MODEL_PATH)

    report: dict[str, Any] = {
        "version": "TARGET_PUBLIC_SIDE_PRIOR_HFT_PILOT_V1_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "modelArtifact": str(MODEL_PATH.resolve()),
        "targetEvaluation": target_evaluation,
        "hftEvaluation": None,
        "decision": "TARGET_MODEL_FROZEN_PENDING_HFT",
        "guards": preregistration["guards"],
    }

    if args.hft_dataset is not None:
        dataset = json.loads(args.hft_dataset.read_text(encoding="utf-8"))
        expected_markets = [int(value) for value in preregistration["hftCohort"]["markets"]]
        actual_markets = [int(value) for value in dataset.get("markets") or []]
        if actual_markets != expected_markets:
            raise RuntimeError(f"HFT market mismatch: expected {expected_markets}, found {actual_markets}")
        if int(dataset.get("entryLatencyMs")) != 1092 or int(dataset.get("responseLatencyMs")) != 273:
            raise RuntimeError("HFT latency contract mismatch")
        hft_summary, audit_rows = summarize_hft(dataset, model)
        target_validation_ok = target_evaluation["validation"]["balancedAccuracy"] > 0.52
        sufficient_acts = hft_summary["actCheckpoints"] >= 5
        sufficient_fills = hft_summary["chosenActionsWithActualFill"] >= 2
        conditions = {
            "targetValidationBalancedAccuracyAbove052": target_validation_ok,
            "sufficientActCheckpoints": sufficient_acts,
            "sufficientChosenActionFills": sufficient_fills,
            "learnedPolicyRealizedValuePositive": hft_summary["learnedPolicyRealizedMtm1sUsdt"] > EPS,
            "learnedPolicyBeatsMatchedDirectionScore": (
                hft_summary["learnedPolicyRealizedMtm1sUsdt"]
                > hft_summary["matchedActDirectionScoreRealizedMtm1sUsdt"] + EPS
            ),
            "predictedSideOracleBeatsOpposite": (
                hft_summary["predictedSideOracleMtm1sUsdt"]
                > hft_summary["oppositeSideOracleMtm1sUsdt"] + EPS
            ),
        }
        if not sufficient_acts or not sufficient_fills:
            decision = "NEED_MORE_DATA"
        elif all(conditions.values()):
            decision = "KEEP_COMPONENT"
        else:
            decision = "REJECT"
        report.update(
            {
                "hftDataset": str(args.hft_dataset.resolve()),
                "execution": preregistration["execution"],
                "hftEvaluation": hft_summary,
                "gateConditions": conditions,
                "decision": decision,
                "rows": audit_rows,
            }
        )

    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output.resolve()),
                "model": str(MODEL_PATH.resolve()),
                "targetEvaluation": target_evaluation,
                "hftEvaluation": report["hftEvaluation"],
                "gateConditions": report.get("gateConditions"),
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
