from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
from pathlib import Path
from typing import Any

from predict_bot.target_eth_taker_behavior_v1 import (
    DEFAULT_OUTPUT as DEFAULT_BEHAVIOR_DATASET,
    MICRO_FEATURES,
    PREDICT_FEATURES,
    SIDE_MODEL_FEATURES,
    SIZE_MODEL_FEATURES,
)
from predict_bot.target_eth_taker_hazard_v1 import DEFAULT_OUTPUT as DEFAULT_HAZARD_DATASET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_eth_taker_behavior_report_v1.json"
DEFAULT_MODEL_DIR = ROOT / "data" / "research" / "target_eth_taker_behavior_models_v1"
REPORT_VERSION = "TARGET_ETH_TAKER_BEHAVIOR_V1_HAZARD_SIDE_SIZE_WALK_FORWARD"

TIME = ["seconds_left"]
PRICE = [
    "predict_up_bid",
    "predict_up_ask",
    "predict_up_mid",
    "predict_down_bid",
    "predict_down_ask",
    "predict_down_mid",
    "predict_up_spread",
    "predict_down_spread",
    "predict_up_mid_edge",
    "predict_mid_sum",
]
TRAJECTORY = [
    column
    for column in PREDICT_FEATURES
    if column.startswith("predict_up_mid_delta_") or column.startswith("predict_up_spread_delta_")
]
MICRO = [column for column in MICRO_FEATURES if column not in {"strike_price", "spot_price", "futures_price"}]

HAZARD_FEATURE_SETS = {
    "time_only": TIME,
    "price_only": PRICE,
    "trajectory_only": TRAJECTORY,
    "time_price": TIME + PRICE,
    "time_price_trajectory": list(dict.fromkeys(TIME + PRICE + TRAJECTORY)),
    "without_time": list(dict.fromkeys(PRICE + TRAJECTORY)),
    "without_price": list(dict.fromkeys(TIME + TRAJECTORY)),
    "without_trajectory": list(dict.fromkeys(TIME + PRICE)),
}
SIDE_FEATURE_SETS = {
    "time_only": TIME,
    "price_only": PRICE,
    "trajectory_only": TRAJECTORY,
    "time_price": list(dict.fromkeys(TIME + PRICE)),
    "time_price_trajectory": list(dict.fromkeys(TIME + PRICE + TRAJECTORY)),
    "microstructure": MICRO,
    "full_public": list(SIDE_MODEL_FEATURES),
}
SIZE_FEATURE_SETS = {
    "time_price_side": [
        "seconds_left",
        "label_side_up",
        "chosen_predict_bid",
        "chosen_predict_ask",
        "chosen_predict_mid",
        "chosen_predict_spread",
        "opposite_predict_mid",
    ],
    "trajectory_side": list(dict.fromkeys(["label_side_up"] + TRAJECTORY)),
    "time_price_trajectory_side": list(
        dict.fromkeys(
            TIME
            + PRICE
            + TRAJECTORY
            + [
                "label_side_up",
                "chosen_predict_bid",
                "chosen_predict_ask",
                "chosen_predict_mid",
                "chosen_predict_spread",
                "opposite_predict_mid",
            ]
        )
    ),
    "micro_side": list(dict.fromkeys(["label_side_up"] + MICRO)),
    "full_conditional": list(SIZE_MODEL_FEATURES),
}


def _shared() -> Any:
    path = ROOT / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("shared_target_taker_trainer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared Target Taker trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _feature_has_data(frame: Any, feature: str, pd: Any) -> bool:
    if feature not in frame.columns:
        return False
    return pd.to_numeric(frame[feature], errors="coerce").notna().any()


def _usable(features: list[str], frame: Any, pd: Any) -> list[str]:
    return [feature for feature in features if _feature_has_data(frame, feature, pd)]


def _market_order(frame: Any, time_column: str) -> list[int]:
    rows = (
        frame.groupby("market_id", as_index=False)[time_column]
        .min()
        .sort_values([time_column, "market_id"])
    )
    return [int(value) for value in rows["market_id"].tolist()]


def _walk_forward(
    markets: list[int], *, min_train_markets: int, test_markets: int, max_folds: int
) -> list[dict[str, list[int]]]:
    if len(markets) < min_train_markets + max(5, test_markets // 2):
        raise ValueError(
            f"not enough ETH markets for walk-forward: got {len(markets)}, need about {min_train_markets + max(5, test_markets // 2)}"
        )
    folds: list[dict[str, list[int]]] = []
    end = min_train_markets
    while end < len(markets) and len(folds) < max_folds:
        test = markets[end : min(len(markets), end + test_markets)]
        if len(test) < max(5, test_markets // 2):
            break
        prior = markets[:end]
        calibration_count = max(4, int(round(len(prior) * 0.15)))
        calibration_count = min(calibration_count, max(1, len(prior) - 8))
        folds.append(
            {
                "trainMarkets": prior[:-calibration_count],
                "calibrationMarkets": prior[-calibration_count:],
                "testMarkets": test,
            }
        )
        end += test_markets
    return folds


def _time_bin(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if number <= 60:
        return "LATE_LE_60S"
    if number <= 200:
        return "MID_60_200S"
    return "EARLY_GT_200S"


def _price_bin(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if number < 0.33:
        return "UP_LOW_LT033"
    if number > 0.67:
        return "UP_HIGH_GT067"
    return "UP_MID_033_067"


def _hazard_empirical(frame: Any) -> dict[str, Any]:
    df = frame.copy()
    df["timeRegime"] = df["seconds_left"].apply(_time_bin)
    df["upMidRegime"] = df["predict_up_mid"].apply(_price_bin)

    def grouped(columns: list[str]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for key, group in df.groupby(columns, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            row = {column: str(value) for column, value in zip(columns, values)}
            row.update(
                {
                    "rows": int(len(group)),
                    "nextTaker1sRate": float(group["label_next_taker_any_1s"].mean()),
                    "nextTaker2sRate": float(group["label_next_taker_any_2s"].mean()),
                    "nextTaker5sRate": float(group["label_next_taker_any_5s"].mean()),
                }
            )
            output.append(row)
        return output

    return {
        "time": grouped(["timeRegime"]),
        "upMid": grouped(["upMidRegime"]),
        "timeByUpMid": grouped(["timeRegime", "upMidRegime"]),
    }


def _behavior_empirical(frame: Any) -> dict[str, Any]:
    df = frame.copy()
    df["timeRegime"] = df["seconds_left"].apply(_time_bin)
    df["upMidRegime"] = df["predict_up_mid"].apply(_price_bin)

    def grouped(columns: list[str]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for key, group in df.groupby(columns, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            shares = [float(value) for value in group["target_latest_shares"].dropna().tolist()]
            row = {column: str(value) for column, value in zip(columns, values)}
            row.update(
                {
                    "rows": int(len(group)),
                    "upRate": float(group["label_side_up"].mean()),
                    "medianShares": float(statistics.median(shares)) if shares else None,
                    "meanShares": float(statistics.mean(shares)) if shares else None,
                }
            )
            output.append(row)
        return output

    return {
        "time": grouped(["timeRegime"]),
        "upMid": grouped(["upMidRegime"]),
        "timeByUpMid": grouped(["timeRegime", "upMidRegime"]),
    }


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
            "Walk-forward Target ETH Taker research. Hazard uses uniform one-second public decision points; "
            "side and size are conditioned on actual Target Taker parents with the entire target event second excluded."
        )
    )
    parser.add_argument("--hazard-dataset", type=Path, default=DEFAULT_HAZARD_DATASET)
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--min-train-markets", type=int, default=20)
    parser.add_argument("--test-markets", type=int, default=8)
    parser.add_argument("--max-folds", type=int, default=4)
    parser.add_argument("--interactions", type=int, default=8)
    parser.add_argument("--max-rounds", type=int, default=3000)
    parser.add_argument("--outer-bags", type=int, default=6)
    args = parser.parse_args()

    shared = _shared()
    deps = shared._imports()
    pd = deps["pd"]

    hazard_path = args.hazard_dataset.expanduser().resolve()
    behavior_path = args.behavior_dataset.expanduser().resolve()
    if not hazard_path.exists() or not behavior_path.exists():
        raise SystemExit(
            "ETH Taker datasets are missing. Run:\n"
            "  python tools/build_target_eth_taker_hazard_v1_dataset.py\n"
            "  python tools/build_target_eth_taker_behavior_v1_dataset.py"
        )
    hazard = pd.read_csv(hazard_path)
    behavior = pd.read_csv(behavior_path)
    for frame in (hazard, behavior):
        frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)

    hazard_markets = _market_order(hazard, "decision_sampled_at_ms")
    behavior_markets = _market_order(behavior, "target_event_ms")
    common_market_order = [market for market in hazard_markets if market in set(behavior_markets)]
    # Use the same chronological market folds for all three tasks when possible.
    fold_markets = common_market_order if len(common_market_order) >= args.min_train_markets + 5 else hazard_markets
    folds = _walk_forward(
        fold_markets,
        min_train_markets=max(10, int(args.min_train_markets)),
        test_markets=max(5, int(args.test_markets)),
        max_folds=max(1, int(args.max_folds)),
    )

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "asset": "ETH",
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "hazardDataset": str(hazard_path),
        "behaviorDataset": str(behavior_path),
        "hazardRows": int(len(hazard)),
        "behaviorRows": int(len(behavior)),
        "hazardMarkets": len(hazard_markets),
        "behaviorMarkets": len(behavior_markets),
        "walkForward": {
            "minTrainMarkets": max(10, int(args.min_train_markets)),
            "testMarkets": max(5, int(args.test_markets)),
            "maxFolds": max(1, int(args.max_folds)),
            "sharedMarketFolds": folds,
        },
        "timestampBoundary": (
            "Hazard labels start at the next full target second. Side/size features are strictly before the entire "
            "second-quantized target event bucket. Same-second Target information is never a feature."
        ),
        "sideLeakageBoundary": (
            "Side classification excludes all chosen-side and target-side-aligned features. Chosen-side context "
            "appears only in the size model after the Target side is conditioned on."
        ),
        "empirical": {
            "hazard": _hazard_empirical(hazard),
            "actualTaker": _behavior_empirical(behavior),
        },
        "hazardTasks": {},
        "sideTask": {},
        "sizeTask": {},
    }

    for horizon in (1, 2, 5):
        label = f"label_next_taker_any_{horizon}s"
        task: dict[str, Any] = {
            "label": label,
            "rows": int(len(hazard)),
            "positiveRate": float(hazard[label].mean()),
            "featureSets": {},
        }
        for feature_index, (name, requested) in enumerate(HAZARD_FEATURE_SETS.items()):
            features = _usable(requested, hazard, pd)
            if not features:
                continue
            rows = [
                shared._classification_fold(
                    deps=deps,
                    frame=hazard,
                    label=label,
                    features=features,
                    fold=fold,
                    interactions=max(0, int(args.interactions)),
                    max_rounds=max(100, int(args.max_rounds)),
                    outer_bags=max(2, int(args.outer_bags)),
                    seed=1000 + horizon * 100 + feature_index * 10 + fold_index,
                )
                for fold_index, fold in enumerate(folds)
            ]
            summary = shared._aggregate_classification(rows)
            summary["features"] = features
            task["featureSets"][name] = summary
        task["rankingByMeanLogLossLiftVsPrior"] = sorted(
            [
                {
                    "featureSet": name,
                    "meanLogLossLiftVsPrior": values.get("mean_logLossLiftVsPrior"),
                    "positiveFoldRate": values.get("positiveFoldRate_logLossLiftVsPrior"),
                    "meanTestRocAuc": values.get("meanTestRocAuc"),
                    "meanTestAveragePrecision": values.get("meanTestAveragePrecision"),
                }
                for name, values in task["featureSets"].items()
                if values.get("mean_logLossLiftVsPrior") is not None
            ],
            key=lambda row: float(row["meanLogLossLiftVsPrior"]),
            reverse=True,
        )
        report["hazardTasks"][f"next_taker_any_{horizon}s"] = task

    side_label = "label_side_up"
    side_task: dict[str, Any] = {
        "label": side_label,
        "rows": int(len(behavior)),
        "positiveRate": float(behavior[side_label].mean()),
        "featureSets": {},
    }
    for feature_index, (name, requested) in enumerate(SIDE_FEATURE_SETS.items()):
        features = _usable(requested, behavior, pd)
        if not features:
            continue
        rows = [
            shared._classification_fold(
                deps=deps,
                frame=behavior,
                label=side_label,
                features=features,
                fold=fold,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=3000 + feature_index * 10 + fold_index,
            )
            for fold_index, fold in enumerate(folds)
        ]
        summary = shared._aggregate_classification(rows)
        summary["features"] = features
        side_task["featureSets"][name] = summary
    side_task["rankingByMeanLogLossLiftVsPrior"] = sorted(
        [
            {
                "featureSet": name,
                "meanLogLossLiftVsPrior": values.get("mean_logLossLiftVsPrior"),
                "positiveFoldRate": values.get("positiveFoldRate_logLossLiftVsPrior"),
                "meanTestRocAuc": values.get("meanTestRocAuc"),
                "meanTestAveragePrecision": values.get("meanTestAveragePrecision"),
            }
            for name, values in side_task["featureSets"].items()
            if values.get("mean_logLossLiftVsPrior") is not None
        ],
        key=lambda row: float(row["meanLogLossLiftVsPrior"]),
        reverse=True,
    )
    report["sideTask"] = side_task

    size_target = "target_log1p_latest_shares"
    size_task: dict[str, Any] = {"target": size_target, "rows": int(len(behavior)), "featureSets": {}}
    for feature_index, (name, requested) in enumerate(SIZE_FEATURE_SETS.items()):
        features = _usable(requested, behavior, pd)
        if not features:
            continue
        rows = [
            shared._regression_fold(
                deps=deps,
                frame=behavior,
                target=size_target,
                features=features,
                fold=fold,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=5000 + feature_index * 10 + fold_index,
            )
            for fold_index, fold in enumerate(folds)
        ]
        summary = shared._aggregate_regression(rows)
        summary["features"] = features
        size_task["featureSets"][name] = summary
    size_task["rankingByMeanMaeLogLiftVsMedian"] = sorted(
        [
            {
                "featureSet": name,
                "meanMaeLogLiftVsMedian": values.get("meanMaeLogLiftVsMedian"),
                "positiveFoldRate": values.get("positiveFoldRateMaeLogLift"),
                "meanR2LogShares": values.get("meanR2LogShares"),
            }
            for name, values in size_task["featureSets"].items()
            if values.get("meanMaeLogLiftVsMedian") is not None
        ],
        key=lambda row: float(row["meanMaeLogLiftVsMedian"]),
        reverse=True,
    )
    report["sizeTask"] = size_task

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
