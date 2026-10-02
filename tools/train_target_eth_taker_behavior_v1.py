from __future__ import annotations

import argparse
import importlib.util
import json
import math
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
REPORT_VERSION = "TARGET_ETH_TAKER_BEHAVIOR_V1_HAZARD_SIDE_SIZE_INDEPENDENT_WALK_FORWARD"

TIME = ["seconds_left"]
PRICE = [
    "predict_up_bid", "predict_up_ask", "predict_up_mid",
    "predict_down_bid", "predict_down_ask", "predict_down_mid",
    "predict_up_spread", "predict_down_spread", "predict_up_mid_edge", "predict_mid_sum",
]
TRAJECTORY = [
    feature for feature in PREDICT_FEATURES
    if feature.startswith("predict_up_mid_delta_") or feature.startswith("predict_up_spread_delta_")
]
MICRO = [feature for feature in MICRO_FEATURES if feature not in {"strike_price", "spot_price", "futures_price"}]

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
        "seconds_left", "label_side_up", "chosen_predict_bid", "chosen_predict_ask",
        "chosen_predict_mid", "chosen_predict_spread", "opposite_predict_mid",
    ],
    "trajectory_side": list(dict.fromkeys(["label_side_up"] + TRAJECTORY)),
    "time_price_trajectory_side": list(dict.fromkeys(
        TIME + PRICE + TRAJECTORY + [
            "label_side_up", "chosen_predict_bid", "chosen_predict_ask",
            "chosen_predict_mid", "chosen_predict_spread", "opposite_predict_mid",
        ]
    )),
    "micro_side": list(dict.fromkeys(["label_side_up"] + MICRO)),
    "full_conditional": list(SIZE_MODEL_FEATURES),
}


def _shared() -> Any:
    path = ROOT / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("shared_target_taker_trainer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _market_order(frame: Any, time_column: str) -> list[int]:
    rows = frame.groupby("market_id", as_index=False)[time_column].min().sort_values([time_column, "market_id"])
    return [int(value) for value in rows["market_id"].tolist()]


def _folds(markets: list[int], *, min_train: int, test_markets: int, max_folds: int) -> list[dict[str, list[int]]]:
    if len(markets) < min_train + max(5, test_markets // 2):
        raise ValueError(
            f"not enough markets: got {len(markets)}, need at least {min_train + max(5, test_markets // 2)}"
        )
    output: list[dict[str, list[int]]] = []
    end = min_train
    while end < len(markets) and len(output) < max_folds:
        test = markets[end:min(len(markets), end + test_markets)]
        if len(test) < max(5, test_markets // 2):
            break
        prior = markets[:end]
        calibration_count = max(4, int(round(len(prior) * 0.15)))
        calibration_count = min(calibration_count, max(1, len(prior) - 8))
        output.append({
            "trainMarkets": prior[:-calibration_count],
            "calibrationMarkets": prior[-calibration_count:],
            "testMarkets": test,
        })
        end += test_markets
    return output


def _usable(features: list[str], frame: Any, pd: Any) -> list[str]:
    result = []
    for feature in features:
        if feature not in frame.columns:
            continue
        if pd.to_numeric(frame[feature], errors="coerce").notna().any():
            result.append(feature)
    return result


def _classification_suite(
    *, shared: Any, deps: dict[str, Any], frame: Any, label: str,
    feature_sets: dict[str, list[str]], folds: list[dict[str, list[int]]],
    interactions: int, max_rounds: int, outer_bags: int, seed_base: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    task: dict[str, Any] = {
        "label": label,
        "rows": int(len(frame)),
        "positiveRate": float(frame[label].mean()) if len(frame) else None,
        "featureSets": {},
    }
    for feature_index, (name, requested) in enumerate(feature_sets.items()):
        features = _usable(requested, frame, pd)
        if not features:
            continue
        fold_rows = [
            shared._classification_fold(
                deps=deps,
                frame=frame,
                label=label,
                features=features,
                fold=fold,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=seed_base + feature_index * 20 + fold_index,
            )
            for fold_index, fold in enumerate(folds)
        ]
        summary = shared._aggregate_classification(fold_rows)
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
    return task


def _regression_suite(
    *, shared: Any, deps: dict[str, Any], frame: Any, target: str,
    feature_sets: dict[str, list[str]], folds: list[dict[str, list[int]]],
    interactions: int, max_rounds: int, outer_bags: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    task: dict[str, Any] = {"target": target, "rows": int(len(frame)), "featureSets": {}}
    for feature_index, (name, requested) in enumerate(feature_sets.items()):
        features = _usable(requested, frame, pd)
        if not features:
            continue
        fold_rows = [
            shared._regression_fold(
                deps=deps,
                frame=frame,
                target=target,
                features=features,
                fold=fold,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=5000 + feature_index * 20 + fold_index,
            )
            for fold_index, fold in enumerate(folds)
        ]
        summary = shared._aggregate_regression(fold_rows)
        summary["features"] = features
        task["featureSets"][name] = summary
    task["rankingByMeanMaeLogLiftVsMedian"] = sorted(
        [
            {
                "featureSet": name,
                "meanMaeLogLiftVsMedian": values.get("meanMaeLogLiftVsMedian"),
                "positiveFoldRate": values.get("positiveFoldRateMaeLogLift"),
                "meanR2LogShares": values.get("meanR2LogShares"),
            }
            for name, values in task["featureSets"].items()
            if values.get("meanMaeLogLiftVsMedian") is not None
        ],
        key=lambda row: float(row["meanMaeLogLiftVsMedian"]),
        reverse=True,
    )
    return task


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Target ETH Taker hazard / side / size walk-forward research")
    parser.add_argument("--hazard-dataset", type=Path, default=DEFAULT_HAZARD_DATASET)
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
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
            "ETH datasets missing. Run build_target_eth_taker_hazard_v1_dataset.py and "
            "build_target_eth_taker_behavior_v1_dataset.py first."
        )
    hazard = pd.read_csv(hazard_path)
    behavior = pd.read_csv(behavior_path)
    for frame in (hazard, behavior):
        frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)

    min_train = max(10, int(args.min_train_markets))
    test_markets = max(5, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    hazard_markets = _market_order(hazard, "decision_sampled_at_ms")
    behavior_markets = _market_order(behavior, "target_event_ms")
    # Important: hazard includes every covered ETH market, including markets with
    # zero Target Taker activity. Side/size contain only actual Taker markets.
    # They must never share a Taker-conditioned market list.
    hazard_folds = _folds(hazard_markets, min_train=min_train, test_markets=test_markets, max_folds=max_folds)
    behavior_folds = _folds(behavior_markets, min_train=min_train, test_markets=test_markets, max_folds=max_folds)

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "asset": "ETH",
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "hazardRows": int(len(hazard)),
        "hazardMarkets": len(hazard_markets),
        "behaviorRows": int(len(behavior)),
        "behaviorMarkets": len(behavior_markets),
        "walkForward": {
            "hazardFolds": hazard_folds,
            "behaviorFolds": behavior_folds,
            "whySeparate": (
                "Hazard must retain covered markets with zero Target Taker activity. Side/size are event-conditioned. "
                "Sharing a Taker-conditioned market list would bias hazard evaluation upward."
            ),
        },
        "timestampBoundary": (
            "Hazard labels begin at decisionBucket+1s. Side/size features are strictly before the entire "
            "second-quantized Target event bucket."
        ),
        "sideLeakageBoundary": (
            "Side models use raw public UP/DOWN features only; chosen-side context is legal only for size after side is known."
        ),
        "hazardTasks": {},
    }
    for horizon in (1, 2, 5):
        report["hazardTasks"][f"next_taker_any_{horizon}s"] = _classification_suite(
            shared=shared,
            deps=deps,
            frame=hazard,
            label=f"label_next_taker_any_{horizon}s",
            feature_sets=HAZARD_FEATURE_SETS,
            folds=hazard_folds,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            seed_base=1000 + horizon * 100,
        )
    report["sideTask"] = _classification_suite(
        shared=shared,
        deps=deps,
        frame=behavior,
        label="label_side_up",
        feature_sets=SIDE_FEATURE_SETS,
        folds=behavior_folds,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed_base=3000,
    )
    report["sizeTask"] = _regression_suite(
        shared=shared,
        deps=deps,
        frame=behavior,
        target="target_log1p_latest_shares",
        feature_sets=SIZE_FEATURE_SETS,
        folds=behavior_folds,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
    )

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
