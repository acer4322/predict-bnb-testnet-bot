from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

from predict_bot.target_maker_direct_placement_v1 import (
    CHOSEN_SIDE_CONTEXT,
    DEFAULT_BEHAVIOR_OUTPUT,
    DEFAULT_HAZARD_OUTPUT,
    LEVEL_MODEL_FEATURES,
    PUBLIC_FEATURES,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_direct_placement_report_v1.json"
REPORT_VERSION = "TARGET_MAKER_DIRECT_PLACEMENT_V1_INFERRED_LABEL_INDEPENDENT_WALK_FORWARD"

TIME = ["seconds_left"]
PRICE = [
    "predict_up_bid", "predict_up_ask", "predict_up_mid",
    "predict_down_bid", "predict_down_ask", "predict_down_mid",
    "predict_up_spread", "predict_down_spread", "predict_mid_sum", "predict_up_mid_edge",
]
FLOW = [
    "direction_score", "abs_direction_score",
    "spot_queue_imbalance", "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
    "spot_return_250ms_bps", "spot_return_1s_bps", "spot_return_3s_bps", "spot_return_5s_bps",
    "futures_queue_imbalance", "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s",
    "futures_return_250ms_bps", "futures_return_1s_bps", "futures_return_3s_bps", "futures_return_5s_bps",
]
ORACLE = ["perp_spot_basis_bps", "spot_minus_strike_bps", "chainlink_minus_strike_bps", "spot_minus_chainlink_bps"]
COMPACT = [
    "seconds_left", "predict_up_mid", "predict_up_spread", "predict_down_spread",
    "spot_minus_strike_bps", "chainlink_minus_strike_bps", "direction_score",
    "spot_queue_imbalance", "spot_taker_imbalance_1s", "spot_return_1s_bps", "spot_return_3s_bps",
    "futures_queue_imbalance", "futures_taker_imbalance_1s", "futures_return_1s_bps", "futures_return_3s_bps",
]

HAZARD_FEATURE_SETS = {
    "time_only": TIME,
    "price_only": PRICE,
    "microstructure": FLOW,
    "oracle_distance": ORACLE,
    "time_price": list(dict.fromkeys(TIME + PRICE)),
    "price_micro": list(dict.fromkeys(PRICE + FLOW)),
    "compact_public": COMPACT,
    "full_public": PUBLIC_FEATURES,
    "without_microstructure": [feature for feature in PUBLIC_FEATURES if feature not in set(FLOW)],
}
SIDE_FEATURE_SETS = {
    "direction_only": ["direction_score"],
    "price_only": PRICE,
    "microstructure": FLOW,
    "oracle_distance": ORACLE,
    "time_price": list(dict.fromkeys(TIME + PRICE)),
    "compact_side": COMPACT,
    "full_public": PUBLIC_FEATURES,
    "without_direction_score": [feature for feature in COMPACT if feature != "direction_score"],
}
LEVEL_PRICE = list(dict.fromkeys(TIME + PRICE + ["label_side_up"] + CHOSEN_SIDE_CONTEXT))
LEVEL_FLOW = list(dict.fromkeys(["label_side_up"] + CHOSEN_SIDE_CONTEXT + FLOW + ORACLE))
LEVEL_COMPACT = list(dict.fromkeys(COMPACT + ["label_side_up"] + CHOSEN_SIDE_CONTEXT))
LEVEL_FEATURE_SETS = {
    "time_price_side": LEVEL_PRICE,
    "flow_side": LEVEL_FLOW,
    "compact_level": LEVEL_COMPACT,
    "full_conditional": LEVEL_MODEL_FEATURES,
}


def _shared() -> Any:
    path = ROOT / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("shared_target_taker_trainer_for_maker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _market_order(frame: Any, time_column: str) -> list[int]:
    rows = frame.groupby("market_id", as_index=False)[time_column].min().sort_values([time_column, "market_id"])
    return [int(value) for value in rows["market_id"].tolist()]


def _spanning_folds(markets: list[int], *, min_train: int, test_markets: int, max_folds: int) -> list[dict[str, list[int]]]:
    if len(markets) < min_train + max(5, test_markets // 2):
        raise ValueError(f"not enough markets: got {len(markets)}")
    last_start = max(min_train, len(markets) - test_markets)
    count = max(1, int(max_folds))
    if count == 1 or last_start == min_train:
        starts = [last_start]
    else:
        starts = sorted({
            int(round(min_train + index * (last_start - min_train) / (count - 1)))
            for index in range(count)
        })
    folds: list[dict[str, list[int]]] = []
    for start in starts:
        test = markets[start:min(len(markets), start + test_markets)]
        if len(test) < max(5, test_markets // 2):
            continue
        prior = markets[:start]
        calibration_count = max(8, int(round(len(prior) * 0.15)))
        calibration_count = min(calibration_count, max(1, len(prior) - 12))
        folds.append({
            "trainMarkets": prior[:-calibration_count],
            "calibrationMarkets": prior[-calibration_count:],
            "testMarkets": test,
        })
    return folds


def _usable(features: list[str], frame: Any, pd: Any) -> list[str]:
    result = []
    for feature in features:
        if feature in frame.columns and pd.to_numeric(frame[feature], errors="coerce").notna().any():
            result.append(feature)
    return result


def _classification_suite(
    *, shared: Any, deps: dict[str, Any], frame: Any, label: str,
    feature_sets: dict[str, list[str]], folds: list[dict[str, list[int]]],
    interactions: int, max_rounds: int, outer_bags: int, seed_base: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    working = frame[frame[label].notna()].copy()
    working[label] = pd.to_numeric(working[label], errors="raise").astype(int)
    task: dict[str, Any] = {
        "label": label,
        "rows": int(len(working)),
        "positiveRate": float(working[label].mean()) if len(working) else None,
        "featureSets": {},
    }
    for feature_index, (name, requested) in enumerate(feature_sets.items()):
        features = _usable(requested, working, pd)
        if not features:
            continue
        fold_rows = [
            shared._classification_fold(
                deps=deps,
                frame=working,
                label=label,
                features=features,
                fold=fold,
                interactions=min(max(0, interactions), max(0, len(features) // 2)),
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


def _missingness(frame: Any, features: list[str], pd: Any) -> dict[str, float]:
    result: dict[str, float] = {}
    for feature in features:
        if feature not in frame.columns:
            result[feature] = 1.0
            continue
        values = pd.to_numeric(frame[feature], errors="coerce")
        result[feature] = float(values.isna().mean()) if len(values) else 1.0
    return result


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct Target Maker inferred-placement EBM research")
    parser.add_argument("--hazard-dataset", type=Path, default=DEFAULT_HAZARD_OUTPUT)
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--min-train-markets", type=int, default=60)
    parser.add_argument("--test-markets", type=int, default=18)
    parser.add_argument("--max-folds", type=int, default=4)
    parser.add_argument("--interactions", type=int, default=8)
    parser.add_argument("--max-rounds", type=int, default=2200)
    parser.add_argument("--outer-bags", type=int, default=6)
    args = parser.parse_args()

    shared = _shared()
    deps = shared._imports()
    pd = deps["pd"]
    hazard_path = args.hazard_dataset.expanduser().resolve()
    behavior_path = args.behavior_dataset.expanduser().resolve()
    if not hazard_path.exists() or not behavior_path.exists():
        raise SystemExit("Maker direct datasets missing. Run tools/build_target_maker_direct_placement_v1_dataset.py first.")
    hazard = pd.read_csv(hazard_path)
    behavior = pd.read_csv(behavior_path)
    for frame in (hazard, behavior):
        frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    hazard["decision_sampled_at_ms"] = pd.to_numeric(hazard["decision_sampled_at_ms"], errors="raise").astype("int64")
    behavior["placement_first_ms"] = pd.to_numeric(behavior["placement_first_ms"], errors="raise").astype("int64")

    min_train = max(20, int(args.min_train_markets))
    test_markets = max(5, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    hazard_markets = _market_order(hazard, "decision_sampled_at_ms")
    behavior_markets = _market_order(behavior, "placement_first_ms")
    hazard_folds = _spanning_folds(hazard_markets, min_train=min_train, test_markets=test_markets, max_folds=max_folds)
    behavior_folds = _spanning_folds(behavior_markets, min_train=min_train, test_markets=test_markets, max_folds=max_folds)

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "labelIdentity": "INFERRED_PLACEMENT_NOT_PRIVATE_ORDER_GROUND_TRUTH",
        "hazardRows": int(len(hazard)),
        "hazardMarkets": len(hazard_markets),
        "behaviorRows": int(len(behavior)),
        "behaviorMarkets": len(behavior_markets),
        "walkForward": {
            "method": "chronological expanding train with test windows spread across the available history, including the latest window",
            "hazardFolds": hazard_folds,
            "behaviorFolds": behavior_folds,
            "whySeparate": "Hazard retains covered zero-placement markets; side/level are conditioned on high-confidence inferred placements.",
        },
        "evidenceBoundary": "8778 public levels are anonymous. Labels require high-confidence V2.1 consumable placement allocation linked retrospectively to known Target Maker fills; this does not prove private order ownership.",
        "timestampBoundary": "Behavior features are strictly sampled before placement_first_ms. Hazard labels require placement_ms strictly after decision_sampled_at_ms.",
        "missingness": {
            "hazardPublic": _missingness(hazard, PUBLIC_FEATURES, pd),
            "behaviorPublic": _missingness(behavior, PUBLIC_FEATURES, pd),
        },
        "hazardTasks": {},
    }

    for horizon in (1, 2, 5):
        label = f"label_next_inferred_placement_any_{horizon}s"
        report["hazardTasks"][f"next_inferred_placement_any_{horizon}s"] = _classification_suite(
            shared=shared, deps=deps, frame=hazard, label=label,
            feature_sets=HAZARD_FEATURE_SETS, folds=hazard_folds,
            interactions=interactions, max_rounds=max_rounds, outer_bags=outer_bags,
            seed_base=1000 + horizon * 100,
        )

    report["sideTask"] = _classification_suite(
        shared=shared, deps=deps, frame=behavior, label="label_side_up",
        feature_sets=SIDE_FEATURE_SETS, folds=behavior_folds,
        interactions=interactions, max_rounds=max_rounds, outer_bags=outer_bags,
        seed_base=3000,
    )
    report["levelTasks"] = {}
    for index, label in enumerate(("label_at_or_improves_best_bid", "label_near_best_1tick", "label_near_best_2ticks")):
        report["levelTasks"][label] = _classification_suite(
            shared=shared, deps=deps, frame=behavior, label=label,
            feature_sets=LEVEL_FEATURE_SETS, folds=behavior_folds,
            interactions=interactions, max_rounds=max_rounds, outer_bags=outer_bags,
            seed_base=4000 + index * 100,
        )

    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(report_path)
    print(json.dumps(_clean(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
