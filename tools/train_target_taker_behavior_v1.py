from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from predict_bot.target_maker_ebm_v3_dataset import TRAJECTORY_FEATURES
from predict_bot.target_maker_taker_state_link_v3 import (
    DEFAULT_OUTPUT as DEFAULT_STATE_LINK_DATASET,
    STATE_FEATURE_COLUMNS,
)
from predict_bot.target_taker_behavior_v1 import (
    DEFAULT_OUTPUT as DEFAULT_TAKER_DATASET,
    SIDE_MODEL_FEATURES,
    SIZE_MODEL_FEATURES,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_behavior_report_v1.json"
DEFAULT_MODEL_DIR = ROOT / "data" / "research" / "target_taker_behavior_models_v1"
REPORT_VERSION = "TARGET_TAKER_BEHAVIOR_V1_HAZARD_SIDE_SIZE_WALK_FORWARD"


def _available(columns: list[str], universe: list[str]) -> list[str]:
    allowed = set(universe)
    return list(dict.fromkeys(column for column in columns if column in allowed))


OCC_TIME = ["seconds_left"]
OCC_PRICE = [
    "target_price",
    "side_predict_bid",
    "side_predict_ask",
    "side_predict_mid",
    "side_predict_spread",
    "opposite_predict_mid",
]
OCC_MICRO = [
    "side_aligned_direction_score",
    "side_aligned_spot_queue_imbalance",
    "side_aligned_spot_taker_imbalance_250ms",
    "side_aligned_spot_taker_imbalance_1s",
    "side_aligned_futures_queue_imbalance",
    "side_aligned_futures_taker_imbalance_250ms",
    "side_aligned_futures_taker_imbalance_1s",
] + list(TRAJECTORY_FEATURES)
OCC_FILL = [
    "highres_fill_duration_ms",
    "highres_allocation_rows",
    "target_filled_shares",
    "observed_filled_near_18",
]
OCC_INVENTORY = [
    "post_fill_maker_delta_shares",
    "post_fill_maker_imbalance_ratio",
    "post_fill_maker_paired_coverage",
]
OCC_COMPACT = _available(
    OCC_TIME + OCC_PRICE + OCC_MICRO + OCC_FILL + OCC_INVENTORY,
    STATE_FEATURE_COLUMNS,
)
OCC_FEATURE_SETS = {
    "time_only": _available(OCC_TIME, STATE_FEATURE_COLUMNS),
    "time_price": _available(OCC_TIME + OCC_PRICE, STATE_FEATURE_COLUMNS),
    "microstructure_only": _available(OCC_MICRO, STATE_FEATURE_COLUMNS),
    "time_price_micro": _available(OCC_TIME + OCC_PRICE + OCC_MICRO, STATE_FEATURE_COLUMNS),
    "compact_hazard": OCC_COMPACT,
    "without_time": [column for column in OCC_COMPACT if column not in set(OCC_TIME)],
    "without_price": [column for column in OCC_COMPACT if column not in set(OCC_PRICE)],
    "without_microstructure": [column for column in OCC_COMPACT if column not in set(OCC_MICRO)],
}

SIDE_TIME_PRICE = [
    "seconds_left",
    "predict_up_bid",
    "predict_up_ask",
    "predict_up_mid",
    "predict_down_bid",
    "predict_down_ask",
    "predict_down_mid",
    "predict_up_spread",
    "predict_down_spread",
    "predict_up_mid_edge",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps",
    "perp_spot_basis_bps",
]
SIDE_FLOW = [
    "direction_score",
    "abs_direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_250ms",
    "spot_taker_imbalance_1s",
    "spot_return_250ms_bps",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "spot_return_5s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_250ms",
    "futures_taker_imbalance_1s",
    "futures_return_250ms_bps",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "futures_return_5s_bps",
]
SIDE_COMPACT = _available(
    [
        "seconds_left",
        "predict_up_mid",
        "predict_up_spread",
        "predict_down_spread",
        "spot_minus_strike_bps",
        "chainlink_minus_strike_bps",
        "direction_score",
        "spot_queue_imbalance",
        "spot_taker_imbalance_1s",
        "spot_return_1s_bps",
        "spot_return_3s_bps",
        "futures_queue_imbalance",
        "futures_taker_imbalance_1s",
        "futures_return_1s_bps",
        "futures_return_3s_bps",
        "signal_age_ms",
    ],
    SIDE_MODEL_FEATURES,
)
SIDE_FEATURE_SETS = {
    "direction_only": _available(["direction_score"], SIDE_MODEL_FEATURES),
    "time_price": _available(SIDE_TIME_PRICE, SIDE_MODEL_FEATURES),
    "microstructure": _available(SIDE_FLOW, SIDE_MODEL_FEATURES),
    "compact_side": SIDE_COMPACT,
    "full_public": list(SIDE_MODEL_FEATURES),
    "without_direction_score": [column for column in SIDE_COMPACT if column != "direction_score"],
}

SIZE_COMPACT = _available(
    [
        "seconds_left",
        "label_side_up",
        "chosen_predict_ask",
        "chosen_predict_mid",
        "chosen_predict_spread",
        "direction_score",
        "spot_queue_imbalance",
        "spot_taker_imbalance_1s",
        "spot_return_1s_bps",
        "spot_return_3s_bps",
        "futures_queue_imbalance",
        "futures_taker_imbalance_1s",
        "futures_return_1s_bps",
        "futures_return_3s_bps",
        "signal_age_ms",
    ],
    SIZE_MODEL_FEATURES,
)
SIZE_FEATURE_SETS = {
    "time_price_side": _available(
        [
            "seconds_left",
            "label_side_up",
            "chosen_predict_bid",
            "chosen_predict_ask",
            "chosen_predict_mid",
            "chosen_predict_spread",
            "opposite_predict_mid",
        ],
        SIZE_MODEL_FEATURES,
    ),
    "flow_side": _available(["label_side_up"] + SIDE_FLOW, SIZE_MODEL_FEATURES),
    "compact_size": SIZE_COMPACT,
    "full_conditional": list(SIZE_MODEL_FEATURES),
}


def _imports() -> dict[str, Any]:
    try:
        import joblib
        import numpy as np
        import pandas as pd
        from interpret.glassbox import ExplainableBoostingClassifier, ExplainableBoostingRegressor
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import (
            accuracy_score,
            average_precision_score,
            balanced_accuracy_score,
            brier_score_loss,
            f1_score,
            log_loss,
            mean_absolute_error,
            median_absolute_error,
            precision_score,
            r2_score,
            recall_score,
            roc_auc_score,
        )
        from sklearn.utils.class_weight import compute_sample_weight
    except ImportError as exc:
        raise SystemExit('Target Taker research dependencies missing. Run: pip install -e ".[research]"') from exc
    return locals()


def _numeric(pd: Any, frame: Any, features: list[str]) -> Any:
    return frame[features].apply(pd.to_numeric, errors="coerce")


def _market_order(df: Any, time_column: str) -> list[int]:
    rows = (
        df.groupby("market_id", as_index=False)[time_column]
        .min()
        .sort_values([time_column, "market_id"])
    )
    return [int(value) for value in rows["market_id"].tolist()]


def _walk_forward_folds(
    markets: list[int],
    *,
    min_train_markets: int,
    test_markets: int,
    max_folds: int,
) -> list[dict[str, list[int]]]:
    if len(markets) < min_train_markets + test_markets:
        raise ValueError(
            f"need at least {min_train_markets + test_markets} markets for walk-forward; got {len(markets)}"
        )
    folds: list[dict[str, list[int]]] = []
    end = int(min_train_markets)
    while end < len(markets) and len(folds) < max_folds:
        test = markets[end : min(len(markets), end + test_markets)]
        if len(test) < max(5, test_markets // 2):
            break
        prior = markets[:end]
        calibration_count = max(8, int(round(len(prior) * 0.15)))
        calibration_count = min(calibration_count, max(1, len(prior) - 12))
        folds.append(
            {
                "trainMarkets": prior[:-calibration_count],
                "calibrationMarkets": prior[-calibration_count:],
                "testMarkets": test,
            }
        )
        end += test_markets
    return folds


def _fit_classifier(
    deps: dict[str, Any], X: Any, y: Any, *, interactions: int, max_rounds: int, outer_bags: int, seed: int
) -> Any:
    if set(int(value) for value in y.tolist()) != {0, 1}:
        raise ValueError("binary EBM training requires both classes")
    model = deps["ExplainableBoostingClassifier"](
        interactions=max(0, int(interactions)),
        random_state=int(seed),
        max_rounds=max(100, int(max_rounds)),
        early_stopping_rounds=80,
        outer_bags=max(2, int(outer_bags)),
        n_jobs=-2,
    )
    model.fit(X, y, sample_weight=deps["compute_sample_weight"]("balanced", y))
    return model


def _fit_regressor(
    deps: dict[str, Any], X: Any, y: Any, *, interactions: int, max_rounds: int, outer_bags: int, seed: int
) -> Any:
    model = deps["ExplainableBoostingRegressor"](
        interactions=max(0, int(interactions)),
        random_state=int(seed),
        max_rounds=max(100, int(max_rounds)),
        early_stopping_rounds=80,
        outer_bags=max(2, int(outer_bags)),
        n_jobs=-2,
    )
    model.fit(X, y)
    return model


def _logit(np: Any, probability: Any) -> Any:
    p = np.clip(probability, 1e-7, 1 - 1e-7)
    return np.log(p / (1 - p)).reshape(-1, 1)


def _calibrate(deps: dict[str, Any], y: Any, probability: Any) -> Any | None:
    if len(y) < 20 or set(int(value) for value in y.tolist()) != {0, 1}:
        return None
    model = deps["LogisticRegression"](C=100.0, max_iter=3000, random_state=42)
    model.fit(_logit(deps["np"], probability), y)
    return model


def _apply_calibration(deps: dict[str, Any], calibrator: Any | None, probability: Any) -> Any:
    if calibrator is None:
        return probability
    return calibrator.predict_proba(_logit(deps["np"], probability))[:, 1]


def _classification_metrics(deps: dict[str, Any], y: Any, probability: Any) -> dict[str, Any]:
    np = deps["np"]
    p = np.clip(probability, 1e-7, 1 - 1e-7)
    predicted = (p >= 0.5).astype(int)
    both = set(int(value) for value in y.tolist()) == {0, 1}
    return {
        "rows": int(len(y)),
        "positives": int(y.sum()),
        "positiveRate": float(y.mean()) if len(y) else None,
        "accuracy": float(deps["accuracy_score"](y, predicted)),
        "balancedAccuracy": float(deps["balanced_accuracy_score"](y, predicted)),
        "precision": float(deps["precision_score"](y, predicted, zero_division=0)),
        "recall": float(deps["recall_score"](y, predicted, zero_division=0)),
        "f1": float(deps["f1_score"](y, predicted, zero_division=0)),
        "rocAuc": float(deps["roc_auc_score"](y, p)) if both else None,
        "averagePrecision": float(deps["average_precision_score"](y, p)) if int(y.sum()) > 0 else None,
        "logLoss": float(deps["log_loss"](y, p, labels=[0, 1])),
        "brier": float(deps["brier_score_loss"](y, p)),
    }


def _classification_fold(
    *,
    deps: dict[str, Any],
    frame: Any,
    label: str,
    features: list[str],
    fold: dict[str, list[int]],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    train = frame[frame["market_id"].isin(fold["trainMarkets"])].copy()
    calibration = frame[frame["market_id"].isin(fold["calibrationMarkets"])].copy()
    test = frame[frame["market_id"].isin(fold["testMarkets"])].copy()
    if len(train) < 80 or len(test) < 20:
        return {"status": "INSUFFICIENT_DATA", "trainRows": int(len(train)), "testRows": int(len(test))}
    y_train = train[label].astype(int)
    y_cal = calibration[label].astype(int)
    y_test = test[label].astype(int)
    if set(int(value) for value in y_train.unique()) != {0, 1}:
        return {"status": "INSUFFICIENT_CLASSES", "trainRows": int(len(train)), "testRows": int(len(test))}
    model = _fit_classifier(
        deps,
        _numeric(pd, train, features),
        y_train,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    raw_cal = model.predict_proba(_numeric(pd, calibration, features))[:, 1] if len(calibration) else deps["np"].array([])
    calibrator = _calibrate(deps, y_cal, raw_cal)
    probability = _apply_calibration(
        deps, calibrator, model.predict_proba(_numeric(pd, test, features))[:, 1]
    )
    prior = min(1 - 1e-7, max(1e-7, float(y_train.mean())))
    baseline_probability = deps["np"].full(len(y_test), prior)
    baseline = _classification_metrics(deps, y_test, baseline_probability)
    metrics = _classification_metrics(deps, y_test, probability)
    return {
        "status": "OK",
        "trainRows": int(len(train)),
        "calibrationRows": int(len(calibration)),
        "testRows": int(len(test)),
        "testMarkets": fold["testMarkets"],
        "baseline": baseline,
        "test": metrics,
        "logLossLiftVsPrior": float(baseline["logLoss"] - metrics["logLoss"]),
        "averagePrecisionLiftVsBaseRate": (
            float(metrics["averagePrecision"] - metrics["positiveRate"])
            if metrics.get("averagePrecision") is not None else None
        ),
        "aucLiftVsRandom": float(metrics["rocAuc"] - 0.5) if metrics.get("rocAuc") is not None else None,
    }


def _regression_fold(
    *,
    deps: dict[str, Any],
    frame: Any,
    target: str,
    features: list[str],
    fold: dict[str, list[int]],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    train = frame[frame["market_id"].isin(fold["trainMarkets"])].copy()
    test = frame[frame["market_id"].isin(fold["testMarkets"])].copy()
    if len(train) < 80 or len(test) < 20:
        return {"status": "INSUFFICIENT_DATA", "trainRows": int(len(train)), "testRows": int(len(test))}
    y_train = pd.to_numeric(train[target], errors="coerce")
    y_test = pd.to_numeric(test[target], errors="coerce")
    valid_train = y_train.notna()
    valid_test = y_test.notna()
    train = train.loc[valid_train]
    test = test.loc[valid_test]
    y_train = y_train.loc[valid_train]
    y_test = y_test.loc[valid_test]
    model = _fit_regressor(
        deps,
        _numeric(pd, train, features),
        y_train,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    prediction = model.predict(_numeric(pd, test, features))
    median_baseline = float(y_train.median())
    baseline = deps["np"].full(len(y_test), median_baseline)
    model_mae = float(deps["mean_absolute_error"](y_test, prediction))
    baseline_mae = float(deps["mean_absolute_error"](y_test, baseline))
    model_medae = float(deps["median_absolute_error"](y_test, prediction))
    baseline_medae = float(deps["median_absolute_error"](y_test, baseline))
    raw_actual = deps["np"].expm1(y_test.to_numpy())
    raw_prediction = deps["np"].expm1(deps["np"].clip(prediction, 0.0, 20.0))
    return {
        "status": "OK",
        "trainRows": int(len(train)),
        "testRows": int(len(test)),
        "testMarkets": fold["testMarkets"],
        "baselineTrainMedianLogShares": median_baseline,
        "test": {
            "maeLogShares": model_mae,
            "medianAeLogShares": model_medae,
            "r2LogShares": float(deps["r2_score"](y_test, prediction)),
            "maeRawShares": float(deps["mean_absolute_error"](raw_actual, raw_prediction)),
        },
        "baseline": {
            "maeLogShares": baseline_mae,
            "medianAeLogShares": baseline_medae,
        },
        "maeLogLiftVsMedian": baseline_mae - model_mae,
        "medianAeLogLiftVsMedian": baseline_medae - model_medae,
    }


def _aggregate_classification(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("status") == "OK"]
    output: dict[str, Any] = {"folds": rows, "validFolds": len(valid)}
    for key in ("logLossLiftVsPrior", "averagePrecisionLiftVsBaseRate", "aucLiftVsRandom"):
        values = [float(row[key]) for row in valid if row.get(key) is not None]
        output[f"mean_{key}"] = statistics.mean(values) if values else None
        output[f"positiveFoldRate_{key}"] = sum(value > 0 for value in values) / len(values) if values else None
    aucs = [float(row["test"]["rocAuc"]) for row in valid if row["test"].get("rocAuc") is not None]
    aps = [float(row["test"]["averagePrecision"]) for row in valid if row["test"].get("averagePrecision") is not None]
    output["meanTestRocAuc"] = statistics.mean(aucs) if aucs else None
    output["meanTestAveragePrecision"] = statistics.mean(aps) if aps else None
    return output


def _aggregate_regression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("status") == "OK"]
    lifts = [float(row["maeLogLiftVsMedian"]) for row in valid]
    r2 = [float(row["test"]["r2LogShares"]) for row in valid]
    return {
        "folds": rows,
        "validFolds": len(valid),
        "meanMaeLogLiftVsMedian": statistics.mean(lifts) if lifts else None,
        "positiveFoldRateMaeLogLift": sum(value > 0 for value in lifts) / len(lifts) if lifts else None,
        "meanR2LogShares": statistics.mean(r2) if r2 else None,
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _time_bin(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "UNKNOWN"
    if number <= 60:
        return "LATE_LE_60S"
    if number <= 200:
        return "MID_60_200S"
    return "EARLY_GT_200S"


def _price_bin(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "UNKNOWN"
    if number < 0.33:
        return "LOW_LT_033"
    if number > 0.67:
        return "HIGH_GT_067"
    return "MID_033_067"


def _direction_bin(value: Any) -> str:
    number = _finite(value)
    if number is None or abs(number) < 0.05:
        return "NEUTRAL"
    return "UP" if number > 0 else "DOWN"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


def _direct_empirical(df: Any) -> dict[str, Any]:
    frame = df.copy()
    frame["timeRegime"] = frame["seconds_left"].apply(_time_bin)
    frame["targetPriceRegime"] = frame["target_average_price"].apply(_price_bin)
    frame["publicDirectionRegime"] = frame["direction_score"].apply(_direction_bin)

    def grouped(columns: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key, group in frame.groupby(columns, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            shares = [float(value) for value in group["target_latest_shares"].dropna().tolist()]
            prices = [float(value) for value in group["target_average_price"].dropna().tolist()]
            direction_known = group["label_matches_public_direction"].dropna()
            row = {column: str(value) for column, value in zip(columns, values)}
            row.update(
                {
                    "rows": int(len(group)),
                    "upRate": float(group["label_side_up"].mean()),
                    "medianShares": float(statistics.median(shares)) if shares else None,
                    "p90Shares": _quantile(shares, 0.90),
                    "medianTargetPrice": float(statistics.median(prices)) if prices else None,
                    "publicDirectionMatchRate": float(direction_known.mean()) if len(direction_known) else None,
                    "publicDirectionKnownRows": int(len(direction_known)),
                }
            )
            rows.append(row)
        return rows

    return {
        "time": grouped(["timeRegime"]),
        "targetPrice": grouped(["targetPriceRegime"]),
        "timeByTargetPrice": grouped(["timeRegime", "targetPriceRegime"]),
        "publicDirection": grouped(["publicDirectionRegime"]),
    }


def _hazard_empirical(df: Any) -> dict[str, Any]:
    frame = df.copy()
    frame["timeRegime"] = frame["seconds_left"].apply(_time_bin)
    frame["makerPriceRegime"] = frame["target_price"].apply(_price_bin)

    def grouped(columns: list[str]) -> list[dict[str, Any]]:
        rows = []
        for key, group in frame.groupby(columns, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            row = {column: str(value) for column, value in zip(columns, values)}
            row.update(
                {
                    "rows": int(len(group)),
                    "taker1sRate": float(group["label_next_taker_any_1s"].mean()),
                    "taker2sRate": float(group["label_next_taker_any_2s"].mean()),
                    "taker5sRate": float(group["label_next_taker_any_5s"].mean()),
                }
            )
            rows.append(row)
        return rows

    return {
        "time": grouped(["timeRegime"]),
        "makerPrice": grouped(["makerPriceRegime"]),
        "timeByMakerPrice": grouped(["timeRegime", "makerPriceRegime"]),
    }


def _term_summary(model: Any) -> list[dict[str, Any]]:
    rows = []
    for name, indexes, importance in zip(model.term_names_, model.term_features_, model.term_importances()):
        rows.append(
            {
                "term": str(name),
                "importance": float(importance),
                "featureIndexes": [int(value) for value in indexes],
            }
        )
    rows.sort(key=lambda row: row["importance"], reverse=True)
    return rows[:30]


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Research Target Taker behavior as three separate layers: 5-second occurrence hazard, "
            "conditional BUY_UP/BUY_DOWN side, and conditional parent size. Offline research only."
        )
    )
    parser.add_argument("--state-link-dataset", type=Path, default=DEFAULT_STATE_LINK_DATASET)
    parser.add_argument("--taker-dataset", type=Path, default=DEFAULT_TAKER_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--min-train-markets", type=int, default=60)
    parser.add_argument("--test-markets", type=int, default=18)
    parser.add_argument("--max-folds", type=int, default=4)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=3000)
    parser.add_argument("--outer-bags", type=int, default=8)
    args = parser.parse_args()

    deps = _imports()
    pd = deps["pd"]
    state_path = args.state_link_dataset.expanduser().resolve()
    taker_path = args.taker_dataset.expanduser().resolve()
    if not state_path.exists():
        raise SystemExit(f"State Link dataset missing: {state_path}\nRun: python tools/build_target_maker_taker_state_link_v3_dataset.py")
    if not taker_path.exists():
        raise SystemExit(f"Direct Taker dataset missing: {taker_path}\nRun: python tools/build_target_taker_behavior_v1_dataset.py")

    state = pd.read_csv(state_path)
    taker = pd.read_csv(taker_path)
    for frame, time_column in ((state, "last_target_ms"), (taker, "target_event_ms")):
        frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
        frame[time_column] = pd.to_numeric(frame[time_column], errors="raise").astype("int64")

    min_train = max(20, int(args.min_train_markets))
    test_markets = max(5, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    state_markets = _market_order(state, "last_target_ms")
    taker_markets = _market_order(taker, "target_event_ms")
    hazard_folds = _walk_forward_folds(state_markets, min_train_markets=min_train, test_markets=test_markets, max_folds=max_folds)
    direct_folds = _walk_forward_folds(taker_markets, min_train_markets=min_train, test_markets=test_markets, max_folds=max_folds)

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "architecture": {
            "hazard": "Maker anchor -> P(Target Taker within 5 seconds)",
            "side": "Actual Taker parent -> P(BUY_UP), using raw public pre-event features only",
            "size": "Actual Taker parent + chosen side -> log1p(final observed parent shares)",
        },
        "timestampBoundary": (
            "Direct Taker side/size features are strictly before the start of the second containing target_event_ms. "
            "No same-second Target Taker observation or public snapshot is permitted."
        ),
        "leakageBoundary": (
            "Side models never receive side_aligned_* features, chosen_* fields, label_side_up, target price outcome, or target size. "
            "Chosen-side quote context becomes legal only after conditioning on the actual side in the size layer."
        ),
        "datasets": {
            "stateLink": str(state_path),
            "stateRows": int(len(state)),
            "stateMarkets": len(state_markets),
            "directTaker": str(taker_path),
            "directRows": int(len(taker)),
            "directMarkets": len(taker_markets),
        },
        "walkForward": {
            "minTrainMarkets": min_train,
            "testMarkets": test_markets,
            "maxFolds": max_folds,
            "hazardFolds": hazard_folds,
            "directTakerFolds": direct_folds,
        },
        "empirical": {
            "hazard": _hazard_empirical(state),
            "directTaker": _direct_empirical(taker),
        },
        "tasks": {},
    }

    # Stage A: occurrence hazard. Five seconds is primary; 1s/2s are timing sensitivity.
    hazard_tasks = {
        "next_taker_any_5s": "label_next_taker_any_5s",
        "next_taker_any_2s": "label_next_taker_any_2s",
        "next_taker_any_1s": "label_next_taker_any_1s",
    }
    for task_index, (task_name, label) in enumerate(hazard_tasks.items()):
        frame = state[state[label].notna()].copy()
        frame[label] = pd.to_numeric(frame[label], errors="raise").astype(int)
        feature_reports: dict[str, Any] = {}
        for feature_index, (name, features) in enumerate(OCC_FEATURE_SETS.items()):
            folds = [
                _classification_fold(
                    deps=deps,
                    frame=frame,
                    label=label,
                    features=features,
                    fold=fold,
                    interactions=max(0, int(args.interactions)),
                    max_rounds=max(100, int(args.max_rounds)),
                    outer_bags=max(2, int(args.outer_bags)),
                    seed=1000 + task_index * 100 + feature_index * 10 + fold_index,
                )
                for fold_index, fold in enumerate(hazard_folds)
            ]
            summary = _aggregate_classification(folds)
            summary["features"] = features
            feature_reports[name] = summary
        report["tasks"][task_name] = {
            "label": label,
            "rows": int(len(frame)),
            "positiveRate": float(frame[label].mean()),
            "featureSets": feature_reports,
            "rankingByMeanLogLossLift": sorted(
                [
                    {
                        "featureSet": name,
                        "meanLogLossLift": values.get("mean_logLossLiftVsPrior"),
                        "positiveFoldRate": values.get("positiveFoldRate_logLossLiftVsPrior"),
                        "meanAuc": values.get("meanTestRocAuc"),
                        "meanAveragePrecision": values.get("meanTestAveragePrecision"),
                    }
                    for name, values in feature_reports.items()
                    if values.get("mean_logLossLiftVsPrior") is not None
                ],
                key=lambda row: float(row["meanLogLossLift"]),
                reverse=True,
            ),
        }

    # Stage B: conditional side. The feature universe is deliberately target-side blind.
    side_frame = taker[taker["label_side_up"].notna()].copy()
    side_frame["label_side_up"] = pd.to_numeric(side_frame["label_side_up"], errors="raise").astype(int)
    side_reports: dict[str, Any] = {}
    for feature_index, (name, features) in enumerate(SIDE_FEATURE_SETS.items()):
        folds = [
            _classification_fold(
                deps=deps,
                frame=side_frame,
                label="label_side_up",
                features=features,
                fold=fold,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=3000 + feature_index * 10 + fold_index,
            )
            for fold_index, fold in enumerate(direct_folds)
        ]
        summary = _aggregate_classification(folds)
        summary["features"] = features
        side_reports[name] = summary
    report["tasks"]["taker_side_up_given_taker"] = {
        "label": "label_side_up",
        "rows": int(len(side_frame)),
        "positiveRate": float(side_frame["label_side_up"].mean()),
        "featureSets": side_reports,
        "rankingByMeanLogLossLift": sorted(
            [
                {
                    "featureSet": name,
                    "meanLogLossLift": values.get("mean_logLossLiftVsPrior"),
                    "positiveFoldRate": values.get("positiveFoldRate_logLossLiftVsPrior"),
                    "meanAuc": values.get("meanTestRocAuc"),
                    "meanAveragePrecision": values.get("meanTestAveragePrecision"),
                }
                for name, values in side_reports.items()
                if values.get("mean_logLossLiftVsPrior") is not None
            ],
            key=lambda row: float(row["meanLogLossLift"]),
            reverse=True,
        ),
    }

    # Stage C: conditional size. Regression avoids global full-sample quantile bucket leakage.
    size_frame = taker[taker["target_log1p_latest_shares"].notna()].copy()
    size_reports: dict[str, Any] = {}
    for feature_index, (name, features) in enumerate(SIZE_FEATURE_SETS.items()):
        folds = [
            _regression_fold(
                deps=deps,
                frame=size_frame,
                target="target_log1p_latest_shares",
                features=features,
                fold=fold,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=5000 + feature_index * 10 + fold_index,
            )
            for fold_index, fold in enumerate(direct_folds)
        ]
        summary = _aggregate_regression(folds)
        summary["features"] = features
        size_reports[name] = summary
    report["tasks"]["taker_parent_size_given_side"] = {
        "target": "log1p(target_latest_shares)",
        "rows": int(len(size_frame)),
        "featureSets": size_reports,
        "rankingByMeanMaeLift": sorted(
            [
                {
                    "featureSet": name,
                    "meanMaeLogLiftVsMedian": values.get("meanMaeLogLiftVsMedian"),
                    "positiveFoldRate": values.get("positiveFoldRateMaeLogLift"),
                    "meanR2LogShares": values.get("meanR2LogShares"),
                }
                for name, values in size_reports.items()
                if values.get("meanMaeLogLiftVsMedian") is not None
            ],
            key=lambda row: float(row["meanMaeLogLiftVsMedian"]),
            reverse=True,
        ),
        "caveat": (
            "target_latest_shares is the latest observed mirror parent total, not an exchange-declared original order size. "
            "Compare against target_shares_at_detection before turning any size association into a policy."
        ),
    }

    # Fixed research models for explanations only; selection is not based on the same report ranking.
    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    final_models: dict[str, Any] = {}
    fixed_models = [
        ("hazard_5s", state, "label_next_taker_any_5s", OCC_FEATURE_SETS["time_price_micro"], "classifier"),
        ("side_up", side_frame, "label_side_up", SIDE_FEATURE_SETS["compact_side"], "classifier"),
        ("size_log_shares", size_frame, "target_log1p_latest_shares", SIZE_FEATURE_SETS["compact_size"], "regressor"),
    ]
    for index, (name, frame, target, features, kind) in enumerate(fixed_models):
        if kind == "classifier":
            y = pd.to_numeric(frame[target], errors="coerce")
            valid = y.notna()
            fit_frame = frame.loc[valid]
            y = y.loc[valid].astype(int)
            model = _fit_classifier(
                deps,
                _numeric(pd, fit_frame, features),
                y,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=8000 + index,
            )
        else:
            y = pd.to_numeric(frame[target], errors="coerce")
            valid = y.notna()
            fit_frame = frame.loc[valid]
            y = y.loc[valid]
            model = _fit_regressor(
                deps,
                _numeric(pd, fit_frame, features),
                y,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=8000 + index,
            )
        path = model_dir / f"{name}.joblib"
        deps["joblib"].dump(
            {
                "reportVersion": REPORT_VERSION,
                "task": name,
                "target": target,
                "features": features,
                "model": model,
                "researchOnly": True,
                "automaticStrategyPromotion": False,
            },
            path,
        )
        final_models[name] = {"path": str(path), "features": features, "topTerms": _term_summary(model)}
    report["finalResearchModels"] = final_models

    report = _clean(report)
    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
