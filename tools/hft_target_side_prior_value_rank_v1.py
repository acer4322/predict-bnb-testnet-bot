from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, roc_auc_score


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402
from tools import hft_native_timegrid_inventory_value_v3 as v3  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "hft_target_side_prior_value_rank_v1_preregistered.json"
SOURCE_CONTRACT = BASE / "hft_native_constrained_rank_v4_preregistered.json"
SOURCE_DATASET = BASE / "hft_native_constrained_rank_unused90_v4.json"
TARGET_MODEL = BASE / "target_public_side_prior_hft_pilot_v1.joblib"
FRAME_ARTIFACT = BASE / "hft_target_side_prior_value_rank_v1_frame.joblib"
MODEL_ARTIFACT = BASE / "hft_target_side_prior_value_rank_v1_model.joblib"
TRAIN_REPORT = BASE / "hft_target_side_prior_value_rank_v1_train_report.json"
FROZEN_MANIFEST = BASE / "hft_target_side_prior_value_rank_v1_frozen_manifest.json"
PILOT_REPORT = BASE / "hft_target_side_prior_value_rank_v1_pilot5_report.json"
QTY = v1.QTY
EPS = v1.EPS

TARGET_INPUT_NAMES = ["secondsLeft", "directionScore", "upBid", "upAsk", "downBid", "downAsk"]
TARGET_TRAIN_NAMES = ["secondsLeft", "directionScore", "up_bid", "up_ask", "down_bid", "down_ask"]
PRIOR_FEATURES = [
    "target_prior_alignment",
    "target_prior_side_match",
    "target_prior_confidence",
    "target_prior_uncertainty",
    "target_prior_alignment_x_offset0",
    "target_prior_alignment_x_offset1",
    "target_prior_alignment_x_offset2",
    "target_prior_alignment_x_spread",
    "target_prior_alignment_x_queue_ahead_chunks",
    "target_prior_alignment_x_recent_fill_chunks",
]


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else np.nan
    except Exception:
        return np.nan


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def split_ids() -> tuple[dict[str, list[int]], dict[str, Any]]:
    contract = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = {
        name: [int(row["marketId"]) for row in contract[name]]
        for name in ("train", "validation", "holdout")
    }
    pilot = [int(value) for value in prereg["split"]["pilot"]["markets"]]
    if result["holdout"][:5] != pilot:
        raise RuntimeError("pilot is not the locked first five chronological V4 holdout markets")
    result["pilot"] = pilot
    return result, contract


def raw_lookup() -> dict[tuple[int, int], dict[str, Any]]:
    dataset = json.loads(SOURCE_DATASET.read_text(encoding="utf-8"))
    return {
        (int(row["marketId"]), int(row["checkpointMs"])): dict(row.get("features") or {})
        for row in dataset.get("rows") or []
    }


def expected_checkpoint_keys(market_ids: list[int]) -> list[tuple[int, int]]:
    wanted = set(int(value) for value in market_ids)
    dataset = json.loads(SOURCE_DATASET.read_text(encoding="utf-8"))
    return sorted(
        (int(row["marketId"]), int(row["checkpointMs"]))
        for row in dataset.get("rows") or []
        if int(row["marketId"]) in wanted
    )


def enrich_with_target_prior(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    target_hash = sha256(TARGET_MODEL)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    expected_hash = str(prereg["targetPrior"]["frozenArtifactSha256"]).upper()
    if target_hash != expected_hash:
        raise RuntimeError(f"frozen Target model hash mismatch: {target_hash}")
    artifact = joblib.load(TARGET_MODEL)
    if artifact.get("targetFeatureNames") != TARGET_TRAIN_NAMES:
        raise RuntimeError("Target training feature contract mismatch")
    if artifact.get("runtimePublicFeatureNames") != TARGET_INPUT_NAMES:
        raise RuntimeError("Target runtime feature contract mismatch")
    model = artifact["model"]
    lookup = raw_lookup()
    output = frame.copy()
    probability_by_key: dict[tuple[int, int], float] = {}
    for key in output[["market_id", "checkpoint_ms"]].drop_duplicates().itertuples(index=False, name=None):
        normalized = (int(key[0]), int(key[1]))
        raw = lookup[normalized]
        matrix = pd.DataFrame(
            [{target_name: finite(raw.get(input_name)) for target_name, input_name in zip(TARGET_TRAIN_NAMES, TARGET_INPUT_NAMES)}]
        )
        probability_by_key[normalized] = float(model.predict_proba(matrix)[0, 1])

    probabilities: list[float] = []
    for row in output.itertuples(index=False):
        probabilities.append(probability_by_key[(int(row.market_id), int(row.checkpoint_ms))])
    output["target_prior_p_up"] = probabilities
    direction = 2.0 * output.target_prior_p_up - 1.0
    side_sign = np.where(output.side.astype(str).eq("UP"), 1.0, -1.0)
    output["target_prior_alignment"] = direction * side_sign
    output["target_prior_side_match"] = (output.target_prior_alignment >= 0.0).astype(float)
    output["target_prior_confidence"] = np.maximum(output.target_prior_p_up, 1.0 - output.target_prior_p_up)
    output["target_prior_uncertainty"] = 1.0 - output.target_prior_confidence
    for offset in (0, 1, 2):
        output[f"target_prior_alignment_x_offset{offset}"] = output.target_prior_alignment * (
            output.action_offset.astype(float).eq(float(offset)).astype(float)
        )
    output["target_prior_alignment_x_spread"] = output.target_prior_alignment * output.current_spread_ticks
    output["target_prior_alignment_x_queue_ahead_chunks"] = (
        output.target_prior_alignment * output.queue_ahead_chunks
    )
    output["target_prior_alignment_x_recent_fill_chunks"] = (
        output.target_prior_alignment * output.maker_shares_5s / QTY
    )
    metadata = {
        "targetModelSha256": target_hash,
        "checkpoints": len(probability_by_key),
        "meanProbabilityUp": float(np.mean(list(probability_by_key.values()))),
        "meanConfidence": float(np.mean([max(value, 1.0 - value) for value in probability_by_key.values()])),
        "features": PRIOR_FEATURES,
    }
    return output, metadata


def constrain(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    data = frame.copy()
    data["floor_full_delta"] = [
        v1.post_floor_delta(row, str(row.side), float(row.action_price), QTY)
        for _, row in data.iterrows()
    ]
    keep = data.floor_full_delta >= -EPS
    return data[keep].copy(), int((~keep).sum())


def matrix(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    return frame[features].replace([np.inf, -np.inf], np.nan)


def fit_bundle(training: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    fill_model = v1.classifier()
    fill_model.fit(matrix(training, features), training.filled)
    filled = training[training.filled > 0].copy()
    if len(filled) < 2:
        raise RuntimeError("insufficient filled training actions")
    size_model = v1.regressor()
    size_model.fit(matrix(filled, features), filled.filled_shares)
    markout_model = v1.regressor()
    markout_model.fit(
        matrix(filled, features),
        filled.markout_per_share,
        sample_weight=filled.filled_shares,
    )
    return {
        "features": features,
        "fillModel": fill_model,
        "sizeModel": size_model,
        "markoutModel": markout_model,
        "trainingActionRows": int(len(training)),
        "trainingFilledRows": int(len(filled)),
    }


def evaluate(
    name: str,
    frame: pd.DataFrame,
    bundle: dict[str, Any],
    expected_market_ids: list[int],
) -> dict[str, Any]:
    scored = frame.copy()
    xx = matrix(scored, bundle["features"])
    scored["pfill"] = bundle["fillModel"].predict_proba(xx)[:, 1]
    scored["pred_filled_shares_if_fill"] = np.clip(bundle["sizeModel"].predict(xx), 0.0, QTY)
    scored["pred_markout_per_share_if_fill"] = bundle["markoutModel"].predict(xx)
    scored["score"] = (
        scored.pfill * scored.pred_filled_shares_if_fill * scored.pred_markout_per_share_if_fill
    )
    try:
        fill_auc = float(roc_auc_score(scored.filled, scored.pfill))
    except Exception:
        fill_auc = None
    filled = scored[scored.filled > 0].copy()
    fill_size_mae = (
        float(mean_absolute_error(filled.filled_shares, filled.pred_filled_shares_if_fill))
        if not filled.empty
        else None
    )
    markout_mae = (
        float(mean_absolute_error(filled.markout_per_share, filled.pred_markout_per_share_if_fill))
        if not filled.empty
        else None
    )

    choices: list[dict[str, Any]] = []
    observed_keys: set[tuple[int, int]] = set()
    for (market_id, checkpoint_ms), group in scored.groupby(["market_id", "checkpoint_ms"], sort=True):
        observed_keys.add((int(market_id), int(checkpoint_ms)))
        oracle_ordered = group.sort_values(["mtm", "action_offset", "side"], ascending=[False, False, True])
        oracle = oracle_ordered.iloc[0]
        oracle_reward = max(0.0, float(oracle.mtm))
        ordered = group.sort_values(["score", "action_offset", "side"], ascending=[False, False, True])
        chosen = ordered.iloc[0]
        if float(chosen.score) > 0.0:
            action = f"{chosen.side}_{int(chosen.action_offset)}"
            reward = float(chosen.mtm)
            filled_shares = float(chosen.filled_shares)
            floor_delta = float(chosen.delta_floor_realized)
            predicted_value = float(chosen.score)
            target_probability_up = float(chosen.target_prior_p_up)
            target_alignment = float(chosen.target_prior_alignment)
        else:
            action = "WAIT"
            reward = 0.0
            filled_shares = 0.0
            floor_delta = 0.0
            predicted_value = float(chosen.score)
            target_probability_up = float(chosen.target_prior_p_up)
            target_alignment = 0.0
        choices.append(
            {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "action": action,
                "predictedValue": predicted_value,
                "rewardMtm1sUsdt": reward,
                "filledShares5s": filled_shares,
                "realizedFloorDeltaAudit": floor_delta,
                "targetProbabilityUp": target_probability_up,
                "targetAlignment": target_alignment,
                "oracleAction": f"{oracle.side}_{int(oracle.action_offset)}" if oracle_reward > EPS else "WAIT",
                "oracleRewardMtm1sUsdt": oracle_reward,
            }
        )
    expected_keys = expected_checkpoint_keys(expected_market_ids)
    for market_id, checkpoint_ms in expected_keys:
        if (market_id, checkpoint_ms) in observed_keys:
            continue
        choices.append(
            {
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "action": "WAIT",
                "predictedValue": 0.0,
                "rewardMtm1sUsdt": 0.0,
                "filledShares5s": 0.0,
                "realizedFloorDeltaAudit": 0.0,
                "targetProbabilityUp": None,
                "targetAlignment": 0.0,
                "oracleAction": "WAIT",
                "oracleRewardMtm1sUsdt": 0.0,
            }
        )
    choices.sort(key=lambda row: (int(row["marketId"]), int(row["checkpointMs"])))
    acts = [row for row in choices if row["action"] != "WAIT"]
    reward = float(sum(row["rewardMtm1sUsdt"] for row in choices))
    oracle_reward = float(sum(row["oracleRewardMtm1sUsdt"] for row in choices))
    return {
        "name": name,
        "markets": len(set(int(value) for value in expected_market_ids)),
        "checkpoints": len(choices),
        "minCheckpointMs": min(value[1] for value in expected_keys),
        "maxCheckpointMs": max(value[1] for value in expected_keys),
        "actionRows": int(len(frame)),
        "fillAuc": fill_auc,
        "filledSharesMaeConditional": fill_size_mae,
        "markoutPerShareMaeConditional": markout_mae,
        "feasibleOracleMtm1sUsdt": oracle_reward,
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "policyMtm1sUsdt": reward,
        "policyActs": len(acts),
        "policyActRate": len(acts) / max(1, len(choices)),
        "waits": len(choices) - len(acts),
        "filledActs": sum(row["filledShares5s"] > EPS for row in acts),
        "positiveActs": sum(row["rewardMtm1sUsdt"] > EPS for row in acts),
        "negativeActs": sum(row["rewardMtm1sUsdt"] < -EPS for row in acts),
        "zeroActs": sum(abs(row["rewardMtm1sUsdt"]) <= EPS for row in acts),
        "filledShares5s": float(sum(row["filledShares5s"] for row in acts)),
        "realizedFloorDeltaAudit": float(sum(row["realizedFloorDeltaAudit"] for row in acts)),
        "oracleCapture": reward / oracle_reward if oracle_reward > EPS else None,
        "rows": choices,
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "rows"}


def verify_frozen_manifest() -> None:
    manifest = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    paths = {
        "hft_target_side_prior_value_rank_v1_preregistered.json": PREREG,
        "hft_target_side_prior_value_rank_v1.py": Path(__file__).resolve(),
        "hft_target_side_prior_value_rank_v1_frame.joblib": FRAME_ARTIFACT,
        "hft_target_side_prior_value_rank_v1_model.joblib": MODEL_ARTIFACT,
        "hft_target_side_prior_value_rank_v1_train_report.json": TRAIN_REPORT,
    }
    for name, path in paths.items():
        actual = sha256(path)
        expected = str(manifest["files"][name]).upper()
        if actual != expected:
            raise RuntimeError(f"frozen hash mismatch for {name}: {actual} != {expected}")


def train_phase() -> None:
    ids, contract = split_ids()
    all_ids = set(ids["train"] + ids["validation"] + ids["holdout"])
    raw = v3.load_rows(SOURCE_DATASET.name, all_ids)
    enriched, target_metadata = enrich_with_target_prior(raw)
    feasible, excluded = constrain(enriched)
    joblib.dump(feasible, FRAME_ARTIFACT)
    frames = {
        "train": feasible[feasible.market_id.isin(ids["train"])].copy(),
        "validation": feasible[feasible.market_id.isin(ids["validation"])].copy(),
    }
    if int(frames["train"].checkpoint_ms.max()) >= int(frames["validation"].checkpoint_ms.min()):
        raise RuntimeError("training and validation are not strict chronology")
    target_max_checkpoint = 0
    for path in (
        ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
    ).glob("target_actionpoint_interrogation_v0_*_maker.csv"):
        values = pd.read_csv(path, usecols=["checkpointMs"])["checkpointMs"]
        target_max_checkpoint = max(target_max_checkpoint, int(pd.to_numeric(values).max()))
    if target_max_checkpoint >= int(frames["train"].checkpoint_ms.min()):
        raise RuntimeError("Target prior data is not strict-past relative to HFT training")

    ablation = fit_bundle(frames["train"], list(v1.FEATURES))
    primary = fit_bundle(frames["train"], [*v1.FEATURES, *PRIOR_FEATURES])
    model_artifact = {
        "version": "HFT_TARGET_SIDE_PRIOR_VALUE_RANK_V1_MODEL",
        "researchOnly": True,
        "trainMarketIds": ids["train"],
        "validationMarketIds": ids["validation"],
        "pilotMarketIds": ids["pilot"],
        "targetMetadata": target_metadata,
        "ablation": ablation,
        "primary": primary,
    }
    joblib.dump(model_artifact, MODEL_ARTIFACT)
    evaluations = {
        "primary": {
            split: evaluate(split, frame, primary, ids[split])
            for split, frame in frames.items()
        },
        "ablation": {
            split: evaluate(split, frame, ablation, ids[split])
            for split, frame in frames.items()
        },
    }
    report = {
        "version": "HFT_TARGET_SIDE_PRIOR_VALUE_RANK_V1_TRAIN_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "sourceContract": SOURCE_CONTRACT.name,
        "sourceDataset": SOURCE_DATASET.name,
        "execution": contract["execution"],
        "reward": "actual-fill 1-second MTM only",
        "portfolioFloorAddedToReward": False,
        "floorConstraintExcludedActionRows": excluded,
        "targetPrior": target_metadata,
        "chronology": {
            "targetMaxCheckpointMs": target_max_checkpoint,
            "trainMinCheckpointMs": int(frames["train"].checkpoint_ms.min()),
            "trainMaxCheckpointMs": int(frames["train"].checkpoint_ms.max()),
            "validationMinCheckpointMs": int(frames["validation"].checkpoint_ms.min()),
            "validationMaxCheckpointMs": int(frames["validation"].checkpoint_ms.max()),
        },
        "training": {
            "markets": int(frames["train"].market_id.nunique()),
            "checkpoints": len(expected_checkpoint_keys(ids["train"])),
            "feasibleActionRows": int(len(frames["train"])),
            "filledActionRows": int(frames["train"].filled.sum()),
        },
        "evaluations": evaluations,
        "decision": "MODELS_FROZEN_PENDING_PILOT",
        "guards": json.loads(PREREG.read_text(encoding="utf-8"))["guards"],
    }
    TRAIN_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "phase": "train",
                "model": str(MODEL_ARTIFACT),
                "frame": str(FRAME_ARTIFACT),
                "report": str(TRAIN_REPORT),
                "primary": {key: compact(value) for key, value in evaluations["primary"].items()},
                "ablation": {key: compact(value) for key, value in evaluations["ablation"].items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def pilot_phase() -> None:
    verify_frozen_manifest()
    ids, contract = split_ids()
    artifact = joblib.load(MODEL_ARTIFACT)
    if artifact.get("pilotMarketIds") != ids["pilot"]:
        raise RuntimeError("frozen model pilot IDs mismatch")
    frame = joblib.load(FRAME_ARTIFACT)
    pilot = frame[frame.market_id.isin(ids["pilot"])].copy()
    validation = frame[frame.market_id.isin(ids["validation"])].copy()
    if int(validation.checkpoint_ms.max()) >= int(pilot.checkpoint_ms.min()):
        raise RuntimeError("validation and pilot are not strict chronology")
    if len(expected_checkpoint_keys(ids["pilot"])) != 15:
        raise RuntimeError("pilot coverage mismatch")
    primary_validation = evaluate("validation", validation, artifact["primary"], ids["validation"])
    ablation_validation = evaluate("validation", validation, artifact["ablation"], ids["validation"])
    primary_pilot = evaluate("pilot", pilot, artifact["primary"], ids["pilot"])
    ablation_pilot = evaluate("pilot", pilot, artifact["ablation"], ids["pilot"])
    sufficient_oracle = primary_pilot["feasibleOracleMtm1sUsdt"] > EPS
    sufficient_fills = primary_pilot["filledActs"] >= 2
    conditions = {
        "sufficientFeasibleOracle": sufficient_oracle,
        "sufficientPrimaryActualFills": sufficient_fills,
        "primaryValidationAtLeastAblation": (
            primary_validation["policyMtm1sUsdt"] >= ablation_validation["policyMtm1sUsdt"] - EPS
        ),
        "primaryPilotMtmPositive": primary_pilot["policyMtm1sUsdt"] > EPS,
        "primaryPilotBeatsAblation": (
            primary_pilot["policyMtm1sUsdt"] > ablation_pilot["policyMtm1sUsdt"] + EPS
        ),
        "primaryPilotMajorityWait": primary_pilot["policyActRate"] < 0.5,
    }
    if not sufficient_oracle or not sufficient_fills:
        decision = "NEED_MORE_DATA"
    elif all(conditions.values()):
        decision = "KEEP_EXPAND_SMALL"
    else:
        decision = "REJECT"
    report = {
        "version": "HFT_TARGET_SIDE_PRIOR_VALUE_RANK_V1_PILOT5_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "frozenManifest": str(FROZEN_MANIFEST.resolve()),
        "execution": contract["execution"],
        "cohort": {
            "markets": ids["pilot"],
            "marketCount": 5,
            "checkpoints": 15,
            "openedDevelopment": True,
            "officialHftForward": False,
        },
        "reward": "actual-fill 1-second MTM only",
        "portfolioFloorAddedToReward": False,
        "primaryWithTargetSidePrior": {
            "validation": primary_validation,
            "pilot": primary_pilot,
        },
        "ablationWithoutTargetSidePrior": {
            "validation": ablation_validation,
            "pilot": ablation_pilot,
        },
        "gateConditions": conditions,
        "decision": decision,
        "guards": json.loads(PREREG.read_text(encoding="utf-8"))["guards"],
    }
    PILOT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "phase": "pilot",
                "report": str(PILOT_REPORT),
                "decision": decision,
                "conditions": conditions,
                "primaryValidation": compact(primary_validation),
                "ablationValidation": compact(ablation_validation),
                "primaryPilot": compact(primary_pilot),
                "ablationPilot": compact(ablation_pilot),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("train", "pilot"))
    args = parser.parse_args()
    if args.phase == "train":
        train_phase()
    else:
        pilot_phase()


if __name__ == "__main__":
    main()
