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
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import mean_absolute_error, roc_auc_score
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402
from tools import hft_target_side_prior_value_rank_v1 as hybrid  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "hft_grouped_fill_support_rank_v1_preregistered.json"
FRAME = BASE / "hft_target_side_prior_value_rank_v1_frame.joblib"
GLOBAL_MODEL = BASE / "hft_target_side_prior_value_rank_v1_model.joblib"
MODEL = BASE / "hft_grouped_fill_support_rank_v1_model.joblib"
TRAIN_REPORT = BASE / "hft_grouped_fill_support_rank_v1_train_report.json"
FROZEN_MANIFEST = BASE / "hft_grouped_fill_support_rank_v1_frozen_manifest.json"
PILOT_REPORT = BASE / "hft_grouped_fill_support_rank_v1_pilot5_report.json"
QTY = v1.QTY
EPS = v1.EPS

CHECKPOINT_FEATURES = [
    "feasible_action_count",
    "seconds_left",
    "combined_abs_net_chunks",
    "combined_abs_imbalance_ratio",
    "combined_paired_coverage",
    "maker_abs_net_chunks",
    "maker_fills_5s",
    "maker_shares_5s_chunks",
    "min_spread_ticks",
    "min_queue_ahead_chunks",
    "mean_queue_ahead_chunks",
    "min_distance_best_ticks",
    "max_contra_trade_qty_1s",
    "max_contra_trade_through_qty_1s",
    "max_depth_events_1s",
    "max_abs_top_imbalance",
    "max_abs_direction",
    "max_abs_spot_return1s",
    "max_abs_futures_return1s",
    "max_abs_spot_queue",
    "max_abs_futures_queue",
]
ACTION_FEATURES = list(v1.FEATURES)


def sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest.upper()


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else np.nan
    except Exception:
        return np.nan


def maximum_abs(group: pd.DataFrame, name: str) -> float:
    values = pd.to_numeric(group[name], errors="coerce").abs()
    return float(values.max()) if values.notna().any() else np.nan


def checkpoint_row(group: pd.DataFrame) -> dict[str, Any]:
    first = group.iloc[0]
    return {
        "market_id": int(first.market_id),
        "checkpoint_ms": int(first.checkpoint_ms),
        "any_fill": float((group.filled > 0).any()),
        "feasible_action_count": float(len(group)),
        "seconds_left": finite(first.seconds_left),
        "combined_abs_net_chunks": finite(first.combined_abs_net) / QTY,
        "combined_abs_imbalance_ratio": abs(finite(first.combined_imbalance_ratio)),
        "combined_paired_coverage": finite(first.combined_paired_coverage),
        "maker_abs_net_chunks": finite(first.maker_abs_net) / QTY,
        "maker_fills_5s": finite(first.maker_fills_5s),
        "maker_shares_5s_chunks": finite(first.maker_shares_5s) / QTY,
        "min_spread_ticks": float(pd.to_numeric(group.current_spread_ticks, errors="coerce").min()),
        "min_queue_ahead_chunks": float(pd.to_numeric(group.queue_ahead_chunks, errors="coerce").min()),
        "mean_queue_ahead_chunks": float(pd.to_numeric(group.queue_ahead_chunks, errors="coerce").mean()),
        "min_distance_best_ticks": float(pd.to_numeric(group.distance_from_same_best_ticks, errors="coerce").min()),
        "max_contra_trade_qty_1s": float(pd.to_numeric(group.contra_trade_qty_1s, errors="coerce").max()),
        "max_contra_trade_through_qty_1s": float(
            pd.to_numeric(group.contra_trade_through_qty_1s, errors="coerce").max()
        ),
        "max_depth_events_1s": float(pd.to_numeric(group.depth_events_1s, errors="coerce").max()),
        "max_abs_top_imbalance": maximum_abs(group, "top_imbalance_toward_action"),
        "max_abs_direction": maximum_abs(group, "direction_toward_side"),
        "max_abs_spot_return1s": maximum_abs(group, "spot_ret1_toward_side"),
        "max_abs_futures_return1s": maximum_abs(group, "futures_ret1_toward_side"),
        "max_abs_spot_queue": maximum_abs(group, "spot_queue_toward_side"),
        "max_abs_futures_queue": maximum_abs(group, "futures_queue_toward_side"),
    }


def checkpoint_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [checkpoint_row(group) for _, group in frame.groupby(["market_id", "checkpoint_ms"], sort=True)]
    return pd.DataFrame(rows)


def pairwise_ranker(training: pd.DataFrame) -> dict[str, Any]:
    raw = training[ACTION_FEATURES].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    transformed = scaler.fit_transform(imputer.fit_transform(raw))
    positions = {index: position for position, index in enumerate(training.index)}
    pair_x: list[np.ndarray] = []
    pair_y: list[int] = []
    informative_checkpoints = 0
    for _, group in training.groupby(["market_id", "checkpoint_ms"], sort=True):
        filled_indices = list(group[group.filled > 0].index)
        empty_indices = list(group[group.filled <= 0].index)
        if not filled_indices or not empty_indices:
            continue
        informative_checkpoints += 1
        for filled_index in filled_indices:
            for empty_index in empty_indices:
                difference = transformed[positions[filled_index]] - transformed[positions[empty_index]]
                pair_x.extend((difference, -difference))
                pair_y.extend((1, 0))
    if not pair_y:
        raise RuntimeError("no informative within-checkpoint fill pairs")
    model = LogisticRegression(C=0.25, max_iter=2000, fit_intercept=False, random_state=20260823)
    model.fit(np.asarray(pair_x), np.asarray(pair_y))
    return {
        "features": ACTION_FEATURES,
        "imputer": imputer,
        "scaler": scaler,
        "model": model,
        "pairRows": len(pair_y),
        "informativeCheckpoints": informative_checkpoints,
    }


def quantile_markout_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="quantile",
        quantile=0.25,
        max_depth=3,
        learning_rate=0.045,
        max_iter=180,
        l2_regularization=5.0,
        min_samples_leaf=12,
        random_state=20260823,
    )


def fit_model(training: pd.DataFrame) -> dict[str, Any]:
    checkpoints = checkpoint_frame(training)
    checkpoint_model = v1.classifier()
    checkpoint_model.fit(checkpoints[CHECKPOINT_FEATURES], checkpoints.any_fill)
    ranker = pairwise_ranker(training)
    filled = training[training.filled > 0].copy()
    size_model = v1.regressor()
    size_model.fit(filled[ACTION_FEATURES].replace([np.inf, -np.inf], np.nan), filled.filled_shares)
    markout_model = quantile_markout_model()
    markout_model.fit(
        filled[ACTION_FEATURES].replace([np.inf, -np.inf], np.nan),
        filled.markout_per_share,
        sample_weight=filled.filled_shares,
    )
    return {
        "checkpointFeatures": CHECKPOINT_FEATURES,
        "checkpointModel": checkpoint_model,
        "ranker": ranker,
        "sizeModel": size_model,
        "markoutQ25Model": markout_model,
        "trainingCheckpointRows": len(checkpoints),
        "trainingAnyFillCheckpoints": int(checkpoints.any_fill.sum()),
        "trainingFilledActionRows": int(len(filled)),
    }


def rank_utility(frame: pd.DataFrame, ranker: dict[str, Any]) -> np.ndarray:
    raw = frame[ranker["features"]].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
    transformed = ranker["scaler"].transform(ranker["imputer"].transform(raw))
    return ranker["model"].decision_function(transformed)


def sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def evaluate(name: str, frame: pd.DataFrame, expected_ids: list[int], model: dict[str, Any]) -> dict[str, Any]:
    scored = frame.copy()
    group_rows = checkpoint_frame(scored)
    group_rows["p_any_fill"] = model["checkpointModel"].predict_proba(group_rows[CHECKPOINT_FEATURES])[:, 1]
    p_any_lookup = {
        (int(row.market_id), int(row.checkpoint_ms)): float(row.p_any_fill)
        for row in group_rows.itertuples(index=False)
    }
    scored["p_any_fill"] = [
        p_any_lookup[(int(row.market_id), int(row.checkpoint_ms))]
        for row in scored.itertuples(index=False)
    ]
    scored["rank_utility"] = rank_utility(scored, model["ranker"])
    scored["conditional_fill_support"] = sigmoid(scored.rank_utility.to_numpy(dtype=float))
    action_matrix = scored[ACTION_FEATURES].replace([np.inf, -np.inf], np.nan)
    scored["pred_filled_shares_if_fill"] = np.clip(model["sizeModel"].predict(action_matrix), 0.0, QTY)
    scored["pred_markout_q25_per_share"] = model["markoutQ25Model"].predict(action_matrix)
    scored["score"] = (
        scored.p_any_fill
        * scored.conditional_fill_support
        * scored.pred_filled_shares_if_fill
        * scored.pred_markout_q25_per_share
    )

    expected_keys = hybrid.expected_checkpoint_keys(expected_ids)
    observed_keys: set[tuple[int, int]] = set()
    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in scored.groupby(["market_id", "checkpoint_ms"], sort=True):
        observed_keys.add((int(market_id), int(checkpoint_ms)))
        oracle = group.sort_values(["mtm", "action_offset", "side"], ascending=[False, False, True]).iloc[0]
        oracle_reward = max(0.0, float(oracle.mtm))
        chosen = group.sort_values(["score", "action_offset", "side"], ascending=[False, False, True]).iloc[0]
        if float(chosen.score) > 0.0:
            action = f"{chosen.side}_{int(chosen.action_offset)}"
            reward = float(chosen.mtm)
            filled_shares = float(chosen.filled_shares)
            floor_delta = float(chosen.delta_floor_realized)
        else:
            action = "WAIT"
            reward = 0.0
            filled_shares = 0.0
            floor_delta = 0.0
        choices.append(
            {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "action": action,
                "predictedValue": float(chosen.score),
                "pAnyFill": float(chosen.p_any_fill),
                "conditionalFillSupport": float(chosen.conditional_fill_support),
                "predictedMarkoutQ25PerShare": float(chosen.pred_markout_q25_per_share),
                "rewardMtm1sUsdt": reward,
                "filledShares5s": filled_shares,
                "realizedFloorDeltaAudit": floor_delta,
                "oracleAction": f"{oracle.side}_{int(oracle.action_offset)}" if oracle_reward > EPS else "WAIT",
                "oracleRewardMtm1sUsdt": oracle_reward,
            }
        )
    for market_id, checkpoint_ms in expected_keys:
        if (market_id, checkpoint_ms) in observed_keys:
            continue
        choices.append(
            {
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "action": "WAIT",
                "predictedValue": 0.0,
                "pAnyFill": 0.0,
                "conditionalFillSupport": 0.0,
                "predictedMarkoutQ25PerShare": 0.0,
                "rewardMtm1sUsdt": 0.0,
                "filledShares5s": 0.0,
                "realizedFloorDeltaAudit": 0.0,
                "oracleAction": "WAIT",
                "oracleRewardMtm1sUsdt": 0.0,
            }
        )
    choices.sort(key=lambda row: (row["marketId"], row["checkpointMs"]))
    expected_label: list[int] = []
    expected_probability: list[float] = []
    group_lookup = {
        (int(row.market_id), int(row.checkpoint_ms)): row
        for row in group_rows.itertuples(index=False)
    }
    for key in expected_keys:
        row = group_lookup.get(key)
        expected_label.append(int(row.any_fill) if row is not None else 0)
        expected_probability.append(float(row.p_any_fill) if row is not None else 0.0)
    try:
        any_fill_auc = float(roc_auc_score(expected_label, expected_probability))
    except Exception:
        any_fill_auc = None
    filled_actions = scored[scored.filled > 0].copy()
    size_mae = float(
        mean_absolute_error(filled_actions.filled_shares, filled_actions.pred_filled_shares_if_fill)
    ) if not filled_actions.empty else None
    q25_below_rate = float(
        np.mean(filled_actions.markout_per_share >= filled_actions.pred_markout_q25_per_share)
    ) if not filled_actions.empty else None
    acts = [row for row in choices if row["action"] != "WAIT"]
    reward = float(sum(row["rewardMtm1sUsdt"] for row in choices))
    oracle_reward = float(sum(row["oracleRewardMtm1sUsdt"] for row in choices))
    return {
        "name": name,
        "markets": len(set(expected_ids)),
        "checkpoints": len(choices),
        "actionRows": int(len(scored)),
        "anyFillCheckpointAuc": any_fill_auc,
        "filledSharesMaeConditional": size_mae,
        "markoutQ25EmpiricalCoverage": q25_below_rate,
        "feasibleOracleMtm1sUsdt": oracle_reward,
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "policyMtm1sUsdt": reward,
        "policyActs": len(acts),
        "policyActRate": len(acts) / len(choices),
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


def splits() -> tuple[dict[str, list[int]], dict[str, Any]]:
    ids, contract = hybrid.split_ids()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    pilot = [int(value) for value in prereg["source"]["pilot"]["markets"]]
    if ids["holdout"][-5:] != pilot:
        raise RuntimeError("pilot is not the locked final five chronological V4 holdout markets")
    ids["pilot"] = pilot
    return ids, contract


def train_phase() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha256(FRAME) != str(prereg["source"]["frameSha256"]).upper():
        raise RuntimeError("frozen source frame hash mismatch")
    ids, contract = splits()
    frame = joblib.load(FRAME)
    training = frame[frame.market_id.isin(ids["train"])].copy()
    validation = frame[frame.market_id.isin(ids["validation"])].copy()
    primary = fit_model(training)
    global_artifact = joblib.load(GLOBAL_MODEL)
    primary_train = evaluate("train", training, ids["train"], primary)
    primary_validation = evaluate("validation", validation, ids["validation"], primary)
    global_validation = hybrid.evaluate(
        "validation", validation, global_artifact["ablation"], ids["validation"]
    )
    sufficient_oracle = primary_validation["feasibleOracleMtm1sUsdt"] > EPS
    sufficient_fills = primary_validation["filledActs"] >= 2
    promotion_conditions = {
        "sufficientValidationOracle": sufficient_oracle,
        "sufficientValidationActualFills": sufficient_fills,
        "validationMtmPositive": primary_validation["policyMtm1sUsdt"] > EPS,
        "validationBeatsGlobalBaseline": (
            primary_validation["policyMtm1sUsdt"] > global_validation["policyMtm1sUsdt"] + EPS
        ),
        "validationMajorityWait": primary_validation["policyActRate"] < 0.5,
    }
    if not sufficient_oracle or not sufficient_fills:
        promotion = "NEED_MORE_DATA"
    elif all(promotion_conditions.values()):
        promotion = "PROMOTE_TO_PILOT"
    else:
        promotion = "REJECT_BEFORE_PILOT"
    artifact = {
        "version": "HFT_GROUPED_FILL_SUPPORT_RANK_V1_MODEL",
        "researchOnly": True,
        "trainMarketIds": ids["train"],
        "validationMarketIds": ids["validation"],
        "pilotMarketIds": ids["pilot"],
        "primary": primary,
        "globalBaselineArtifactSha256": sha256(GLOBAL_MODEL),
        "promotionDecision": promotion,
    }
    joblib.dump(artifact, MODEL)
    report = {
        "version": "HFT_GROUPED_FILL_SUPPORT_RANK_V1_TRAIN_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "execution": contract["execution"],
        "architecture": prereg["architecture"],
        "training": {
            "markets": 60,
            "expectedCheckpoints": 180,
            "feasibleCheckpointRows": primary["trainingCheckpointRows"],
            "anyFillCheckpoints": primary["trainingAnyFillCheckpoints"],
            "filledActionRows": primary["trainingFilledActionRows"],
            "pairRows": primary["ranker"]["pairRows"],
            "informativePairCheckpoints": primary["ranker"]["informativeCheckpoints"],
        },
        "primaryTrain": primary_train,
        "primaryValidation": primary_validation,
        "globalBaselineValidation": global_validation,
        "promotionConditions": promotion_conditions,
        "promotionDecision": promotion,
        "pilotEvaluated": False,
        "guards": prereg["guards"],
    }
    TRAIN_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "phase": "train", "promotionDecision": promotion, "conditions": promotion_conditions, "primaryTrain": compact(primary_train), "primaryValidation": compact(primary_validation), "globalBaselineValidation": compact(global_validation), "report": str(TRAIN_REPORT), "model": str(MODEL)}, ensure_ascii=False, indent=2))


def verify_manifest() -> None:
    manifest = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    paths = {
        "hft_grouped_fill_support_rank_v1_preregistered.json": PREREG,
        "hft_grouped_fill_support_rank_v1.py": Path(__file__).resolve(),
        "hft_grouped_fill_support_rank_v1_model.joblib": MODEL,
        "hft_grouped_fill_support_rank_v1_train_report.json": TRAIN_REPORT,
    }
    for name, path in paths.items():
        if sha256(path) != str(manifest["files"][name]).upper():
            raise RuntimeError(f"frozen hash mismatch: {name}")


def pilot_phase() -> None:
    verify_manifest()
    ids, contract = splits()
    artifact = joblib.load(MODEL)
    if artifact.get("promotionDecision") != "PROMOTE_TO_PILOT":
        raise RuntimeError("validation did not promote this model to pilot")
    frame = joblib.load(FRAME)
    pilot = frame[frame.market_id.isin(ids["pilot"])].copy()
    global_artifact = joblib.load(GLOBAL_MODEL)
    primary = evaluate("pilot", pilot, ids["pilot"], artifact["primary"])
    baseline = hybrid.evaluate("pilot", pilot, global_artifact["ablation"], ids["pilot"])
    sufficient_oracle = primary["feasibleOracleMtm1sUsdt"] > EPS
    sufficient_fills = primary["filledActs"] >= 2
    conditions = {
        "sufficientPilotOracle": sufficient_oracle,
        "sufficientPilotActualFills": sufficient_fills,
        "pilotMtmPositive": primary["policyMtm1sUsdt"] > EPS,
        "pilotBeatsGlobalBaseline": primary["policyMtm1sUsdt"] > baseline["policyMtm1sUsdt"] + EPS,
        "pilotMajorityWait": primary["policyActRate"] < 0.5,
    }
    if not sufficient_oracle or not sufficient_fills:
        decision = "NEED_MORE_DATA"
    elif all(conditions.values()):
        decision = "KEEP_BREAKTHROUGH"
    else:
        decision = "REJECT"
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    report = {
        "version": "HFT_GROUPED_FILL_SUPPORT_RANK_V1_PILOT5_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "execution": contract["execution"],
        "cohort": {"markets": ids["pilot"], "checkpoints": 15, "openedDevelopment": True},
        "primary": primary,
        "globalBaseline": baseline,
        "gateConditions": conditions,
        "decision": decision,
        "guards": prereg["guards"],
    }
    PILOT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "phase": "pilot", "decision": decision, "conditions": conditions, "primary": compact(primary), "baseline": compact(baseline), "report": str(PILOT_REPORT)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("train", "pilot"), required=True)
    args = parser.parse_args()
    if args.phase == "train":
        train_phase()
    else:
        pilot_phase()


if __name__ == "__main__":
    main()
