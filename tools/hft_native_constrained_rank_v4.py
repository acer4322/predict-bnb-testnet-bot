from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402
from tools import hft_native_timegrid_inventory_value_v3 as v3  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TARGET_DIR = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
EPS = v1.EPS
QTY = v1.QTY
GRID = v1.GRID
TARGET_CLUSTERS = 8

TARGET_REGIME_FEATURES = [
    "seconds_left",
    "up_mid",
    "up_spread_ticks",
    "down_spread_ticks",
    "log_up_bid_depth",
    "log_up_ask_depth",
    "log_down_bid_depth",
    "log_down_ask_depth",
    "log_up_top3_bid_depth",
    "log_down_top3_bid_depth",
    "pair_ask_edge",
    "pair_bid_edge",
    "log_book_age_ms",
]

CORE_FEATURES = [
    "is_wait",
    "is_action",
    "side_is_up",
    "side_sign",
    "offset_0",
    "offset_1",
    "offset_2",
    "action_price",
    "current_bid",
    "current_ask",
    "current_spread_ticks",
    "floor_full_delta",
    "combined_abs_net_chunks",
    "side_signed_combined_net_chunks",
    "combined_imbalance_ratio",
    "combined_paired_coverage",
    "maker_abs_net_chunks",
    "taker_abs_net_chunks",
    "last_maker_age_s",
    "maker_fills_5s",
    "maker_shares_5s_chunks",
]
BASE_RANK_FEATURES = [
    *CORE_FEATURES,
    *[f"public_{name}" for name in v1.PUBLIC_FEATURES],
    *[f"queue_{name}" for name in v1.QUEUE_FEATURES],
    *[f"static_{name}" for name in v1.REGIME_FEATURES],
    *[f"regime_action_{name}" for name in TARGET_REGIME_FEATURES],
]
TARGET_ONEHOT_FEATURES = [f"target_regime_action_{index}" for index in range(TARGET_CLUSTERS)]


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else np.nan
    except Exception:
        return np.nan


def log_depth(value: Any) -> float:
    number = finite(value)
    return math.log1p(max(0.0, number)) if math.isfinite(number) else np.nan


def target_vector(row: dict[str, Any]) -> list[float]:
    up_bid = finite(row.get("up_bid"))
    up_ask = finite(row.get("up_ask"))
    down_bid = finite(row.get("down_bid"))
    down_ask = finite(row.get("down_ask"))
    up_mid = (up_bid + up_ask) / 2.0 if math.isfinite(up_bid) and math.isfinite(up_ask) else np.nan
    return [
        finite(row.get("seconds_left") if row.get("seconds_left") not in (None, "") else row.get("secondsLeft")),
        up_mid,
        (up_ask - up_bid) / GRID if math.isfinite(up_bid) and math.isfinite(up_ask) else np.nan,
        (down_ask - down_bid) / GRID if math.isfinite(down_bid) and math.isfinite(down_ask) else np.nan,
        log_depth(row.get("up_bid_depth")),
        log_depth(row.get("up_ask_depth")),
        log_depth(row.get("down_bid_depth")),
        log_depth(row.get("down_ask_depth")),
        log_depth(row.get("up_top3_bid_depth")),
        log_depth(row.get("down_top3_bid_depth")),
        finite(row.get("pair_ask_edge")),
        finite(row.get("pair_bid_edge")),
        log_depth(row.get("bookAgeMs")),
    ]


def fit_target_regime() -> tuple[SimpleImputer, StandardScaler, KMeans, dict[str, Any]]:
    vectors: list[list[float]] = []
    markets: set[int] = set()
    min_ms: int | None = None
    max_ms: int | None = None
    files = sorted(TARGET_DIR.glob("target_actionpoint_interrogation_v0_*_controls.csv"))
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    market_id = int(row["marketId"])
                    checkpoint_ms = int(float(row["checkpointMs"]))
                except Exception:
                    continue
                vectors.append(target_vector(row))
                markets.add(market_id)
                min_ms = checkpoint_ms if min_ms is None else min(min_ms, checkpoint_ms)
                max_ms = checkpoint_ms if max_ms is None else max(max_ms, checkpoint_ms)
    raw = np.asarray(vectors, dtype=float)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    transformed = scaler.fit_transform(imputer.fit_transform(raw))
    model = KMeans(n_clusters=TARGET_CLUSTERS, n_init=10, random_state=20260823)
    labels = model.fit_predict(transformed)
    metadata = {
        "files": [path.name for path in files],
        "rows": int(len(raw)),
        "markets": len(markets),
        "minCheckpointMs": min_ms,
        "maxCheckpointMs": max_ms,
        "clusterCounts": {str(key): int(value) for key, value in sorted(Counter(labels).items())},
        "features": TARGET_REGIME_FEATURES,
        "targetActionOrPortfolioFeaturesUsed": False,
    }
    return imputer, scaler, model, metadata


def hft_regime_vector(group: pd.DataFrame, raw: dict[str, Any]) -> list[float]:
    up_rows = group[group.side == "UP"].sort_values("action_offset")
    down_rows = group[group.side == "DOWN"].sort_values("action_offset")
    up = up_rows.iloc[0] if not up_rows.empty else None
    down = down_rows.iloc[0] if not down_rows.empty else None
    up_bid = finite(raw.get("upBid"))
    up_ask = finite(raw.get("upAsk"))
    down_bid = finite(raw.get("downBid"))
    down_ask = finite(raw.get("downAsk"))
    return [
        finite(raw.get("secondsLeft")),
        (up_bid + up_ask) / 2.0 if math.isfinite(up_bid) and math.isfinite(up_ask) else np.nan,
        (up_ask - up_bid) / GRID if math.isfinite(up_bid) and math.isfinite(up_ask) else np.nan,
        (down_ask - down_bid) / GRID if math.isfinite(down_bid) and math.isfinite(down_ask) else np.nan,
        log_depth(up.same_top_shares if up is not None else np.nan),
        log_depth(up.opposite_top_shares if up is not None else np.nan),
        log_depth(down.same_top_shares if down is not None else np.nan),
        log_depth(down.opposite_top_shares if down is not None else np.nan),
        log_depth(up.same_depth_3ticks_shares if up is not None else np.nan),
        log_depth(down.same_depth_3ticks_shares if down is not None else np.nan),
        1.0 - up_ask - down_ask if all(math.isfinite(x) for x in (up_ask, down_ask)) else np.nan,
        up_bid + down_bid - 1.0 if all(math.isfinite(x) for x in (up_bid, down_bid)) else np.nan,
        log_depth(raw.get("predictReceiptAgeMs")),
    ]


def raw_timegrid_lookup(dataset_name: str) -> dict[tuple[int, int], dict[str, Any]]:
    dataset = json.loads((BASE / dataset_name).read_text(encoding="utf-8"))
    return {
        (int(row["marketId"]), int(row["checkpointMs"])): dict(row.get("features") or {})
        for row in dataset.get("rows") or []
    }


def action_features(row: pd.Series, regime_vector: list[float], cluster: int) -> dict[str, float]:
    side_sign = 1.0 if str(row.side) == "UP" else -1.0
    offset = int(row.action_offset)
    floor_full = v1.post_floor_delta(row, str(row.side), float(row.action_price), QTY)
    result = {name: 0.0 for name in [*BASE_RANK_FEATURES, *TARGET_ONEHOT_FEATURES]}
    result.update(
        {
            "is_action": 1.0,
            "side_is_up": float(str(row.side) == "UP"),
            "side_sign": side_sign,
            "offset_0": float(offset == 0),
            "offset_1": float(offset == 1),
            "offset_2": float(offset == 2),
            "action_price": finite(row.action_price),
            "current_bid": finite(row.current_bid),
            "current_ask": finite(row.current_ask),
            "current_spread_ticks": finite(row.current_spread_ticks),
            "floor_full_delta": floor_full,
            "combined_abs_net_chunks": finite(row.combined_abs_net) / QTY,
            "side_signed_combined_net_chunks": side_sign * finite(row.combined_net) / QTY,
            "combined_imbalance_ratio": finite(row.combined_imbalance_ratio),
            "combined_paired_coverage": finite(row.combined_paired_coverage),
            "maker_abs_net_chunks": finite(row.maker_abs_net) / QTY,
            "taker_abs_net_chunks": finite(row.taker_abs_net) / QTY,
            "last_maker_age_s": finite(row.last_maker_age_ms) / 1000.0,
            "maker_fills_5s": finite(row.maker_fills_5s),
            "maker_shares_5s_chunks": finite(row.maker_shares_5s) / QTY,
        }
    )
    for name in v1.PUBLIC_FEATURES:
        result[f"public_{name}"] = finite(row.get(name))
    for name in v1.QUEUE_FEATURES:
        result[f"queue_{name}"] = finite(row.get(name))
    for name in v1.REGIME_FEATURES:
        result[f"static_{name}"] = finite(row.get(name))
    for name, value in zip(TARGET_REGIME_FEATURES, regime_vector):
        result[f"regime_action_{name}"] = finite(value)
    result[f"target_regime_action_{int(cluster)}"] = 1.0
    return result


def build_candidates(
    frame: pd.DataFrame,
    raw_lookup: dict[tuple[int, int], dict[str, Any]],
    target_imputer: SimpleImputer,
    target_scaler: StandardScaler,
    target_model: KMeans,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    excluded = 0
    group_clusters: Counter[int] = Counter()
    for (market_id, checkpoint_ms), group in frame.groupby(["market_id", "checkpoint_ms"], sort=True):
        key = (int(market_id), int(checkpoint_ms))
        regime_vector = hft_regime_vector(group, raw_lookup[key])
        transformed = target_scaler.transform(target_imputer.transform(np.asarray([regime_vector], dtype=float)))
        cluster = int(target_model.predict(transformed)[0])
        group_clusters[cluster] += 1
        wait_features = {name: 0.0 for name in [*BASE_RANK_FEATURES, *TARGET_ONEHOT_FEATURES]}
        wait_features["is_wait"] = 1.0
        rows.append(
            {
                "market_id": int(market_id),
                "checkpoint_ms": int(checkpoint_ms),
                "action": "WAIT",
                "is_wait_action": True,
                "reward_mtm": 0.0,
                "filled_shares": 0.0,
                "realized_floor_delta": 0.0,
                "target_cluster": cluster,
                **wait_features,
            }
        )
        for _, action in group.iterrows():
            floor_full = v1.post_floor_delta(action, str(action.side), float(action.action_price), QTY)
            if floor_full < -EPS:
                excluded += 1
                continue
            rows.append(
                {
                    "market_id": int(market_id),
                    "checkpoint_ms": int(checkpoint_ms),
                    "action": f"{action.side}_{int(action.action_offset)}",
                    "is_wait_action": False,
                    "reward_mtm": float(action.mtm),
                    "filled_shares": float(action.filled_shares),
                    "realized_floor_delta": float(action.delta_floor_realized),
                    "target_cluster": cluster,
                    **action_features(action, regime_vector, cluster),
                }
            )
    return pd.DataFrame(rows), {
        "constrainedActionRows": int(len(rows) - frame[["market_id", "checkpoint_ms"]].drop_duplicates().shape[0]),
        "floorConstraintExcludedActionRows": excluded,
        "targetClusterCounts": {str(key): int(value) for key, value in sorted(group_clusters.items())},
    }


def pairwise_examples(candidates: pd.DataFrame, transformed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pair_x: list[np.ndarray] = []
    pair_y: list[int] = []
    positions = {index: position for position, index in enumerate(candidates.index)}
    for _, group in candidates.groupby(["market_id", "checkpoint_ms"], sort=True):
        indices = list(group.index)
        for left_pos in range(len(indices)):
            for right_pos in range(left_pos + 1, len(indices)):
                left_index = indices[left_pos]
                right_index = indices[right_pos]
                left = candidates.loc[left_index]
                right = candidates.loc[right_index]
                reward_diff = float(left.reward_mtm) - float(right.reward_mtm)
                if reward_diff > EPS:
                    left_wins = True
                elif reward_diff < -EPS:
                    left_wins = False
                elif bool(left.is_wait_action) != bool(right.is_wait_action):
                    left_wins = bool(left.is_wait_action)
                else:
                    continue
                difference = transformed[positions[left_index]] - transformed[positions[right_index]]
                pair_x.extend((difference, -difference))
                pair_y.extend((int(left_wins), int(not left_wins)))
    return np.asarray(pair_x, dtype=float), np.asarray(pair_y, dtype=int)


def fit_ranker(candidates: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    raw = candidates[features].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    transformed = scaler.fit_transform(imputer.fit_transform(raw))
    pair_x, pair_y = pairwise_examples(candidates, transformed)
    model = LogisticRegression(
        C=0.25,
        max_iter=2000,
        fit_intercept=False,
        random_state=20260823,
    )
    model.fit(pair_x, pair_y)
    return {
        "features": features,
        "imputer": imputer,
        "scaler": scaler,
        "model": model,
        "pairRows": int(len(pair_y)),
        "pairPositiveRate": float(pair_y.mean()),
    }


def score_candidates(bundle: dict[str, Any], candidates: pd.DataFrame) -> np.ndarray:
    raw = candidates[bundle["features"]].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
    transformed = bundle["scaler"].transform(bundle["imputer"].transform(raw))
    return bundle["model"].decision_function(transformed)


def unconstrained_oracle(frame: pd.DataFrame) -> tuple[float, int]:
    reward = 0.0
    acts = 0
    for _, group in frame.groupby(["market_id", "checkpoint_ms"], sort=True):
        best = max(0.0, float(group.mtm.max()))
        reward += best
        acts += int(best > EPS)
    return reward, acts


def evaluate(name: str, frame: pd.DataFrame, candidates: pd.DataFrame, bundle: dict[str, Any]) -> dict[str, Any]:
    scored = candidates.copy()
    scored["score"] = score_candidates(bundle, scored)
    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in scored.groupby(["market_id", "checkpoint_ms"], sort=True):
        ordered = group.sort_values(["score", "is_wait_action"], ascending=[False, False])
        chosen = ordered.iloc[0]
        oracle_ordered = group.sort_values(["reward_mtm", "is_wait_action"], ascending=[False, False])
        oracle = oracle_ordered.iloc[0]
        choices.append(
            {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "targetCluster": int(chosen.target_cluster),
                "action": str(chosen.action),
                "score": float(chosen.score),
                "rewardMtm1sUsdt": float(chosen.reward_mtm),
                "filledShares5s": float(chosen.filled_shares),
                "realizedFloorDelta": float(chosen.realized_floor_delta),
                "oracleAction": str(oracle.action),
                "oracleRewardMtm1sUsdt": float(oracle.reward_mtm),
            }
        )
    acts = [row for row in choices if row["action"] != "WAIT"]
    oracle_reward = float(sum(row["oracleRewardMtm1sUsdt"] for row in choices))
    policy_reward = float(sum(row["rewardMtm1sUsdt"] for row in choices))
    unconstrained_reward, unconstrained_acts = unconstrained_oracle(frame)
    return {
        "name": name,
        "markets": int(frame.market_id.nunique()),
        "checkpoints": len(choices),
        "minCheckpointMs": int(frame.checkpoint_ms.min()),
        "maxCheckpointMs": int(frame.checkpoint_ms.max()),
        "constrainedOracleRewardMtm1sUsdt": oracle_reward,
        "constrainedOracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "constrainedOracleActRate": sum(row["oracleAction"] != "WAIT" for row in choices) / max(1, len(choices)),
        "unconstrainedOracleRewardMtm1sUsdt": unconstrained_reward,
        "unconstrainedOracleActs": unconstrained_acts,
        "policyRewardMtm1sUsdt": policy_reward,
        "policyActs": len(acts),
        "policyActRate": len(acts) / max(1, len(choices)),
        "waits": len(choices) - len(acts),
        "filledActs": sum(row["filledShares5s"] > EPS for row in acts),
        "positiveActs": sum(row["rewardMtm1sUsdt"] > EPS for row in acts),
        "negativeActs": sum(row["rewardMtm1sUsdt"] < -EPS for row in acts),
        "zeroActs": sum(abs(row["rewardMtm1sUsdt"]) <= EPS for row in acts),
        "realizedFloorDeltaAudit": float(sum(row["realizedFloorDelta"] for row in acts)),
        "oracleCapture": policy_reward / oracle_reward if oracle_reward > EPS else None,
        "rows": choices,
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "rows"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="hft_native_constrained_rank_v4_preregistered.json")
    parser.add_argument("--dataset", default="hft_native_constrained_rank_unused90_v4.json")
    parser.add_argument("--output", default="hft_native_constrained_rank_v4_report.json")
    parser.add_argument("--model-output", default="hft_native_constrained_rank_v4.joblib")
    args = parser.parse_args()

    contract = json.loads((BASE / args.contract).read_text(encoding="utf-8"))
    ids = {
        split: {int(row["marketId"]) for row in contract[split]}
        for split in ("train", "validation", "holdout")
    }
    all_ids = set().union(*ids.values())
    all_rows = v3.load_rows(args.dataset, all_ids)
    frames = {
        split: all_rows[all_rows.market_id.isin(market_ids)].copy()
        for split, market_ids in ids.items()
    }
    if not (
        int(frames["train"].checkpoint_ms.max())
        < int(frames["validation"].checkpoint_ms.min())
        < int(frames["holdout"].checkpoint_ms.min())
    ):
        raise RuntimeError("HFT cohort split is not strict chronology")

    target_imputer, target_scaler, target_model, target_metadata = fit_target_regime()
    if int(target_metadata["maxCheckpointMs"] or 0) >= int(frames["train"].checkpoint_ms.min()):
        raise RuntimeError("Target regime representation is not strict-past relative to HFT train")
    raw_lookup = raw_timegrid_lookup(args.dataset)
    candidates: dict[str, pd.DataFrame] = {}
    candidate_metadata: dict[str, Any] = {}
    for split, frame in frames.items():
        candidates[split], candidate_metadata[split] = build_candidates(
            frame,
            raw_lookup,
            target_imputer,
            target_scaler,
            target_model,
        )

    primary_features = [*BASE_RANK_FEATURES, *TARGET_ONEHOT_FEATURES]
    ablation_features = list(BASE_RANK_FEATURES)
    primary_bundle = fit_ranker(candidates["train"], primary_features)
    ablation_bundle = fit_ranker(candidates["train"], ablation_features)
    primary = {
        split: evaluate(split, frames[split], candidates[split], primary_bundle)
        for split in frames
    }
    ablation = {
        split: evaluate(split, frames[split], candidates[split], ablation_bundle)
        for split in frames
    }
    holdout = primary["holdout"]
    if holdout["constrainedOracleRewardMtm1sUsdt"] <= EPS:
        decision = "NEED_MORE_DATA"
    elif holdout["policyRewardMtm1sUsdt"] > EPS:
        decision = "KEEP"
    else:
        decision = "REJECT"

    model_artifact = {
        "version": "HFT_NATIVE_CONSTRAINED_RANK_V4_MODEL",
        "researchOnly": True,
        "targetRegime": {
            "features": TARGET_REGIME_FEATURES,
            "imputer": target_imputer,
            "scaler": target_scaler,
            "model": target_model,
            "metadata": target_metadata,
        },
        "primary": primary_bundle,
        "ablation": ablation_bundle,
    }
    joblib.dump(model_artifact, BASE / args.model_output)
    report = {
        "version": "HFT_NATIVE_CONSTRAINED_RANK_V4",
        "researchOnly": True,
        "dreamFillAllowed": False,
        "winnerSettlementPnlRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "targetActionTeacherUsed": False,
        "floorAddedToReward": False,
        "contract": args.contract,
        "dataset": args.dataset,
        "modelArtifact": args.model_output,
        "execution": contract["execution"],
        "checkpointRule": contract["checkpointRule"],
        "feasibilityConstraint": contract["feasibilityConstraint"],
        "reward": contract["reward"],
        "targetRegimePretraining": target_metadata,
        "candidateMetadata": candidate_metadata,
        "ranking": {
            "primaryFeatures": primary_features,
            "ablationFeatures": ablation_features,
            "primaryPairRows": primary_bundle["pairRows"],
            "ablationPairRows": ablation_bundle["pairRows"],
            "waitTieBreak": "WAIT wins exact reward and score ties",
            "thresholdSweep": False,
            "hyperparameterSweep": False,
        },
        "primaryWithTargetRegime": primary,
        "ablationWithoutTargetRegime": ablation,
        "primaryHoldout": "holdout",
        "decision": decision,
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "modelOutput": str(BASE / args.model_output),
                "decision": decision,
                "targetRegime": target_metadata,
                "candidateMetadata": candidate_metadata,
                "primary": {split: compact(metrics) for split, metrics in primary.items()},
                "ablation": {split: compact(metrics) for split, metrics in ablation.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
