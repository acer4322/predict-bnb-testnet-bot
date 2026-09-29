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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_r2_pending_management_closed_loop_v0 import winners  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "hft_terminal_value_policy_v1_preregistered.json"
V4_CONTRACT = BASE / "hft_native_constrained_rank_v4_preregistered.json"
V4_DATASET = BASE / "hft_native_constrained_rank_unused90_v4.json"
V3_CONTRACT = BASE / "hft_native_timegrid_inventory_chronology_v3_preregistered.json"
V3_DATASET = BASE / "hft_native_timegrid_inventory_unused80_v3.json"
MODEL = BASE / "hft_terminal_value_policy_v1_model.joblib"
VALIDATION_REPORT = BASE / "hft_terminal_value_policy_v1_validation_report.json"
HOLDOUT_REPORT = BASE / "hft_terminal_value_policy_v1_holdout_report.json"
EPS = 1e-9
SEED = 20260823


FEATURES = [
    "side_sign",
    "offset_0",
    "offset_1",
    "offset_2",
    "action_price",
    "current_bid",
    "current_ask",
    "spread_ticks",
    "seconds_left",
    "direction_side",
    "spot_minus_strike_side",
    "chainlink_minus_strike_side",
    "perp_spot_basis_side",
    "spot_return_1s_side",
    "spot_return_5s_side",
    "futures_return_1s_side",
    "futures_return_5s_side",
    "spot_queue_imbalance_side",
    "spot_taker_imbalance_1s_side",
    "futures_queue_imbalance_side",
    "futures_taker_imbalance_1s_side",
    "predict_side_mid",
    "mid_minus_action_price",
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


def ids(contract: dict[str, Any], split: str) -> list[int]:
    return [int(row["marketId"]) for row in contract[split]]


def feature_row(raw: dict[str, Any], action: dict[str, Any]) -> dict[str, float]:
    side = str(action["side"]).upper()
    sign = 1.0 if side == "UP" else -1.0
    offset = int(action["offset"])
    bid = finite(raw.get("upBid") if side == "UP" else raw.get("downBid"))
    ask = finite(raw.get("upAsk") if side == "UP" else raw.get("downAsk"))
    side_mid = finite(raw.get("predictUpMid") if side == "UP" else raw.get("predictDownMid"))
    price = finite(action.get("price"))
    return {
        "side_sign": sign,
        "offset_0": float(offset == 0),
        "offset_1": float(offset == 1),
        "offset_2": float(offset == 2),
        "action_price": price,
        "current_bid": bid,
        "current_ask": ask,
        "spread_ticks": (ask - bid) / 0.01 if math.isfinite(ask) and math.isfinite(bid) else np.nan,
        "seconds_left": finite(raw.get("secondsLeft")),
        "direction_side": sign * finite(raw.get("directionScore")),
        "spot_minus_strike_side": sign * finite(raw.get("spotMinusStrikeBps")),
        "chainlink_minus_strike_side": sign * finite(raw.get("chainlinkMinusStrikeBps")),
        "perp_spot_basis_side": sign * finite(raw.get("perpSpotBasisBps")),
        "spot_return_1s_side": sign * finite(raw.get("spotReturn1sBps")),
        "spot_return_5s_side": sign * finite(raw.get("spotReturn5sBps")),
        "futures_return_1s_side": sign * finite(raw.get("futuresReturn1sBps")),
        "futures_return_5s_side": sign * finite(raw.get("futuresReturn5sBps")),
        "spot_queue_imbalance_side": sign * finite(raw.get("spotQueueImbalance")),
        "spot_taker_imbalance_1s_side": sign * finite(raw.get("spotTakerImbalance1s")),
        "futures_queue_imbalance_side": sign * finite(raw.get("futuresQueueImbalance")),
        "futures_taker_imbalance_1s_side": sign * finite(raw.get("futuresTakerImbalance1s")),
        "predict_side_mid": side_mid,
        "mid_minus_action_price": side_mid - price if math.isfinite(side_mid) and math.isfinite(price) else np.nan,
    }


def load_frame(dataset_path: Path, wanted: set[int], outcome_map: dict[int, str]) -> pd.DataFrame:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for checkpoint in dataset.get("rows") or []:
        market_id = int(checkpoint["marketId"])
        if market_id not in wanted:
            continue
        if market_id not in outcome_map:
            raise RuntimeError(f"missing offline outcome label for {market_id}")
        seen.add(market_id)
        raw = dict(checkpoint.get("features") or {})
        checkpoint_ms = int(checkpoint["checkpointMs"])
        winner = str(outcome_map[market_id]).upper()
        for action in checkpoint.get("actions") or []:
            if action.get("invalid"):
                continue
            side = str(action.get("side") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            filled_shares = float(action.get("filledShares5s") or 0.0)
            fill_price = finite(action.get("fillPrice"))
            execution_price = fill_price if math.isfinite(fill_price) else float(action["price"])
            side_wins = float(side == winner)
            terminal_reward = (
                filled_shares * (side_wins - execution_price) if filled_shares > EPS else 0.0
            )
            rows.append(
                {
                    "market_id": market_id,
                    "checkpoint_ms": checkpoint_ms,
                    "side": side,
                    "offset": int(action["offset"]),
                    "filled": float(filled_shares > EPS),
                    "filled_shares": filled_shares,
                    "execution_price": execution_price,
                    "side_wins": side_wins,
                    "terminal_reward": terminal_reward,
                    "direction_score": finite(raw.get("directionScore")),
                    **feature_row(raw, action),
                }
            )
    if seen != wanted:
        raise RuntimeError(f"dataset coverage mismatch for {dataset_path.name}: missing={sorted(wanted-seen)}")
    return pd.DataFrame(rows)


def equal_market_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("market_id").market_id.transform("size").to_numpy(dtype=float)
    return 1.0 / np.maximum(counts, 1.0)


def logistic() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(C=0.25, max_iter=2000, random_state=SEED),
            ),
        ]
    )


def fit_bundle(training: pd.DataFrame) -> dict[str, Any]:
    fill_model = logistic()
    fill_model.fit(
        training[FEATURES],
        training.filled.astype(int),
        model__sample_weight=equal_market_weights(training),
    )
    filled = training[training.filled > 0].copy()
    if filled.market_id.nunique() < 10 or filled.side_wins.nunique() != 2:
        raise RuntimeError("insufficient filled terminal outcomes")
    side_model = logistic()
    side_model.fit(
        filled[FEATURES],
        filled.side_wins.astype(int),
        model__sample_weight=equal_market_weights(filled),
    )
    by_offset = filled.groupby("offset").filled_shares.mean().to_dict()
    global_size = float(filled.filled_shares.mean())

    direction_rewards: dict[int, float] = {}
    for offset in (0, 1, 2):
        direction_rewards[offset] = baseline_reward(training, offset)[0]
    baseline_offset = max(direction_rewards, key=lambda value: (direction_rewards[value], -value))
    return {
        "version": "HFT_TERMINAL_VALUE_POLICY_V1_MODEL",
        "features": FEATURES,
        "fillModel": fill_model,
        "sideWinGivenFillModel": side_model,
        "meanFilledSharesByOffset": {int(key): float(value) for key, value in by_offset.items()},
        "globalMeanFilledShares": global_size,
        "directionBaselineTrainingRewards": direction_rewards,
        "directionBaselineOffset": int(baseline_offset),
        "trainingMarkets": int(training.market_id.nunique()),
        "trainingRows": int(len(training)),
        "trainingFilledRows": int(len(filled)),
        "winnerSettlementPnlRuntimeInput": False,
        "winnerUsedAsOfflineLabelOnly": True,
        "targetActionTeacherUsed": False,
    }


def baseline_reward(frame: pd.DataFrame, offset: int) -> tuple[float, list[dict[str, Any]]]:
    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in frame.groupby(["market_id", "checkpoint_ms"], sort=True):
        direction = float(group.direction_score.iloc[0])
        side = "UP" if direction >= 0.0 else "DOWN"
        selected = group[(group.side == side) & (group.offset == int(offset))]
        if selected.empty:
            choices.append(
                {
                    "marketId": int(market_id),
                    "checkpointMs": int(checkpoint_ms),
                    "action": "WAIT_INVALID_ACTION",
                    "terminalRewardUsdt": 0.0,
                    "filledShares5s": 0.0,
                }
            )
            continue
        if len(selected) != 1:
            raise RuntimeError("direction baseline action is not unique")
        row = selected.iloc[0]
        choices.append(
            {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "action": f"{side}_{offset}",
                "terminalRewardUsdt": float(row.terminal_reward),
                "filledShares5s": float(row.filled_shares),
            }
        )
    return float(sum(row["terminalRewardUsdt"] for row in choices)), choices


def evaluate(name: str, frame: pd.DataFrame, bundle: dict[str, Any]) -> dict[str, Any]:
    scored = frame.copy()
    scored["p_fill"] = bundle["fillModel"].predict_proba(scored[FEATURES])[:, 1]
    scored["p_side_wins_given_fill"] = bundle["sideWinGivenFillModel"].predict_proba(
        scored[FEATURES]
    )[:, 1]
    means = bundle["meanFilledSharesByOffset"]
    fallback = float(bundle["globalMeanFilledShares"])
    scored["pred_filled_shares_given_fill"] = [float(means.get(int(value), fallback)) for value in scored.offset]
    scored["score"] = (
        scored.p_fill
        * scored.pred_filled_shares_given_fill
        * (scored.p_side_wins_given_fill - scored.action_price)
    )

    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in scored.groupby(["market_id", "checkpoint_ms"], sort=True):
        best = group.sort_values(["score", "offset"], ascending=[False, True]).iloc[0]
        if float(best.score) <= EPS:
            chosen: dict[str, Any] = {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "action": "WAIT",
                "score": 0.0,
                "terminalRewardUsdt": 0.0,
                "filledShares5s": 0.0,
                "pFill": None,
                "pSideWinsGivenFill": None,
            }
        else:
            chosen = {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "action": f"{best.side}_{int(best.offset)}",
                "score": float(best.score),
                "terminalRewardUsdt": float(best.terminal_reward),
                "filledShares5s": float(best.filled_shares),
                "pFill": float(best.p_fill),
                "pSideWinsGivenFill": float(best.p_side_wins_given_fill),
            }
        oracle = group.sort_values(["terminal_reward", "offset"], ascending=[False, True]).iloc[0]
        chosen["oracleAction"] = (
            f"{oracle.side}_{int(oracle.offset)}" if float(oracle.terminal_reward) > EPS else "WAIT"
        )
        chosen["oracleTerminalRewardUsdt"] = max(0.0, float(oracle.terminal_reward))
        choices.append(chosen)

    acts = [row for row in choices if row["action"] != "WAIT"]
    per_market: dict[str, float] = {}
    for row in choices:
        key = str(row["marketId"])
        per_market[key] = per_market.get(key, 0.0) + float(row["terminalRewardUsdt"])
    baseline_value, baseline_rows = baseline_reward(frame, int(bundle["directionBaselineOffset"]))
    return {
        "name": name,
        "markets": int(frame.market_id.nunique()),
        "checkpoints": len(choices),
        "oracleTerminalRewardUsdt": float(sum(row["oracleTerminalRewardUsdt"] for row in choices)),
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "policyTerminalRewardUsdt": float(sum(row["terminalRewardUsdt"] for row in choices)),
        "policyActs": len(acts),
        "policyActRate": len(acts) / max(1, len(choices)),
        "waits": len(choices) - len(acts),
        "filledActs": sum(float(row["filledShares5s"]) > EPS for row in acts),
        "filledShares5s": float(sum(float(row["filledShares5s"]) for row in acts)),
        "positiveActs": sum(float(row["terminalRewardUsdt"]) > EPS for row in acts),
        "negativeActs": sum(float(row["terminalRewardUsdt"]) < -EPS for row in acts),
        "zeroActs": sum(abs(float(row["terminalRewardUsdt"])) <= EPS for row in acts),
        "positiveMarkets": sum(value > EPS for value in per_market.values()),
        "negativeMarkets": sum(value < -EPS for value in per_market.values()),
        "zeroMarkets": sum(abs(value) <= EPS for value in per_market.values()),
        "directionScoreBaselineOffset": int(bundle["directionBaselineOffset"]),
        "directionScoreBaselineTerminalRewardUsdt": baseline_value,
        "perMarketTerminalRewardUsdt": per_market,
        "rows": choices,
        "directionBaselineRows": baseline_rows,
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key not in {"rows", "directionBaselineRows"}}


def source_splits() -> dict[str, list[int]]:
    v4 = json.loads(V4_CONTRACT.read_text(encoding="utf-8"))
    v3 = json.loads(V3_CONTRACT.read_text(encoding="utf-8"))
    v4_all = ids(v4, "train") + ids(v4, "validation") + ids(v4, "holdout")
    return {
        "trainV4": v4_all,
        "trainV3": ids(v3, "train"),
        "validation": ids(v3, "validation"),
        "holdout": ids(v3, "holdout"),
    }


def load_train_and_validation(split: dict[str, list[int]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_v4_outcomes = winners(split["trainV4"])
    train_v3_outcomes = winners(split["trainV3"])
    validation_outcomes = winners(split["validation"])
    train = pd.concat(
        [
            load_frame(V4_DATASET, set(split["trainV4"]), train_v4_outcomes),
            load_frame(V3_DATASET, set(split["trainV3"]), train_v3_outcomes),
        ],
        ignore_index=True,
    )
    validation = load_frame(V3_DATASET, set(split["validation"]), validation_outcomes)
    if int(train.checkpoint_ms.max()) >= int(validation.checkpoint_ms.min()):
        raise RuntimeError("train/validation chronology violation")
    return train, validation


def validation_phase() -> None:
    split = source_splits()
    train, validation = load_train_and_validation(split)
    bundle = fit_bundle(train)
    train_metrics = evaluate("train", train, bundle)
    validation_metrics = evaluate("validation", validation, bundle)
    conditions = {
        "oracleTerminalValuePositive": validation_metrics["oracleTerminalRewardUsdt"] > EPS,
        "learnedTerminalValuePositive": validation_metrics["policyTerminalRewardUsdt"] > EPS,
        "minimumActualFilledActs": validation_metrics["filledActs"] >= 3,
        "beatsFrozenDirectionScoreBaseline": validation_metrics["policyTerminalRewardUsdt"]
        > validation_metrics["directionScoreBaselineTerminalRewardUsdt"] + EPS,
        "actRateAtMost70Pct": validation_metrics["policyActRate"] <= 0.70 + EPS,
    }
    decision = "PROMOTE_TO_HOLDOUT" if all(conditions.values()) else "REJECT_BEFORE_HOLDOUT"
    joblib.dump(bundle, MODEL)
    report = {
        "version": "HFT_TERMINAL_VALUE_POLICY_V1_VALIDATION",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "execution": "HftBacktest + Predict Execution Tape V1; risk queue; 1092ms entry / 273ms response latency; partial fills; 5s horizon.",
        "reward": "Incremental terminal PnL of actual HftBacktest-filled shares; no fill=0; WAIT=0.",
        "winnerSettlementPnlRuntimeInput": False,
        "winnerUsedAsOfflineLabelOnly": True,
        "targetFutureActionRuntimeInput": False,
        "targetActionTeacherUsed": False,
        "split": {key: value for key, value in split.items() if key != "holdout"},
        "holdoutWinnerLabelsLoaded": False,
        "model": MODEL.name,
        "modelSha256": sha256(MODEL),
        "train": train_metrics,
        "validation": validation_metrics,
        "promotionConditions": conditions,
        "decision": decision,
    }
    VALIDATION_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(VALIDATION_REPORT),
                "decision": decision,
                "conditions": conditions,
                "train": compact(train_metrics),
                "validation": compact(validation_metrics),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def holdout_phase() -> None:
    report = json.loads(VALIDATION_REPORT.read_text(encoding="utf-8"))
    if report.get("decision") != "PROMOTE_TO_HOLDOUT":
        raise RuntimeError("validation did not unlock holdout")
    if sha256(MODEL) != str(report["modelSha256"]):
        raise RuntimeError("frozen model hash mismatch")
    split = source_splits()
    outcome_map = winners(split["holdout"])
    holdout = load_frame(V3_DATASET, set(split["holdout"]), outcome_map)
    bundle = joblib.load(MODEL)
    metrics = evaluate("holdout", holdout, bundle)
    conditions = {
        "terminalValuePositive": metrics["policyTerminalRewardUsdt"] > EPS,
        "minimumActualFilledActs": metrics["filledActs"] >= 5,
        "beatsFrozenDirectionScoreBaseline": metrics["policyTerminalRewardUsdt"]
        > metrics["directionScoreBaselineTerminalRewardUsdt"] + EPS,
    }
    decision = "KEEP_COMPONENT" if all(conditions.values()) else "REJECT"
    output = {
        "version": "HFT_TERMINAL_VALUE_POLICY_V1_HOLDOUT",
        "researchOnly": True,
        "validationReport": VALIDATION_REPORT.name,
        "model": MODEL.name,
        "modelSha256": sha256(MODEL),
        "winnerSettlementPnlRuntimeInput": False,
        "winnerUsedAsOfflineLabelOnly": True,
        "holdout": metrics,
        "conditions": conditions,
        "decision": decision,
    }
    HOLDOUT_REPORT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(HOLDOUT_REPORT), "decision": decision, "holdout": compact(metrics)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["validation", "holdout"], required=True)
    args = parser.parse_args()
    if args.phase == "validation":
        validation_phase()
    else:
        holdout_phase()


if __name__ == "__main__":
    main()
