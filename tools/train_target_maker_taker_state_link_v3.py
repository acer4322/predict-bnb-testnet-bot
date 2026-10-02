from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from predict_bot.target_maker_ebm_v3_dataset import FILL_PATH_FEATURES, TRAJECTORY_FEATURES
from predict_bot.target_maker_taker_state_link_v3 import DEFAULT_OUTPUT, STATE_FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_taker_state_link_report_v3.json"
DEFAULT_MODEL_DIR = ROOT / "data" / "research" / "target_maker_taker_state_link_models_v3"
REPORT_VERSION = "TARGET_MAKER_TAKER_STATE_LINK_V3_EBM_WALK_FORWARD_POSTFILL_RISK_STATE"

POST_FILL_INVENTORY = [
    "post_fill_maker_up_shares",
    "post_fill_maker_down_shares",
    "post_fill_maker_delta_shares",
    "post_fill_maker_imbalance_ratio",
    "post_fill_maker_paired_coverage",
    "side_aligned_post_fill_delta_shares",
]
PRIOR_INVENTORY = [
    "prior_maker_up_shares",
    "prior_maker_down_shares",
    "prior_maker_delta_shares",
    "prior_maker_imbalance_ratio",
    "prior_maker_paired_coverage",
    "side_aligned_prior_delta_shares",
]
DURATION_FEATURES = [
    "highres_fill_duration_ms",
    "path_fill_duration_ms",
    "path_median_inter_fill_gap_ms",
    "path_max_inter_fill_gap_ms",
    "fill_duration_ge_1500ms",
    "fill_duration_ge_2000ms",
]
FILL_CONTEXT = [
    "highres_fill_duration_ms",
    "highres_allocation_rows",
    "highres_allocated_shares",
    "target_fill_count",
    "target_filled_shares",
    "multi_fill_parent",
    "observed_filled_near_18",
] + list(FILL_PATH_FEATURES)
MARKET_CONTEXT = [
    "seconds_left",
    "target_price",
    "target_side_is_up",
    "side_predict_bid",
    "side_predict_ask",
    "side_predict_mid",
    "side_predict_spread",
    "opposite_predict_mid",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "perp_spot_basis_bps",
    "side_aligned_direction_score",
    "side_aligned_spot_queue_imbalance",
    "side_aligned_spot_taker_imbalance_250ms",
    "side_aligned_spot_taker_imbalance_1s",
    "side_aligned_futures_queue_imbalance",
    "side_aligned_futures_taker_imbalance_250ms",
    "side_aligned_futures_taker_imbalance_1s",
] + list(TRAJECTORY_FEATURES)


def _dedupe_available(columns: list[str]) -> list[str]:
    available = set(STATE_FEATURE_COLUMNS)
    return list(dict.fromkeys(column for column in columns if column in available))


FEATURE_SETS = {
    "full_state": list(STATE_FEATURE_COLUMNS),
    "without_fill_duration": [
        column for column in STATE_FEATURE_COLUMNS if column not in set(DURATION_FEATURES)
    ],
    "fill_path_only": _dedupe_available(FILL_CONTEXT),
    "postfill_inventory_only": _dedupe_available(POST_FILL_INVENTORY + PRIOR_INVENTORY),
    "fill_plus_postfill_inventory": _dedupe_available(
        FILL_CONTEXT + POST_FILL_INVENTORY + PRIOR_INVENTORY
    ),
    "market_state_only": _dedupe_available(MARKET_CONTEXT),
    "fill_plus_market": _dedupe_available(FILL_CONTEXT + MARKET_CONTEXT),
    "compact_state": _dedupe_available(
        [
            "highres_fill_duration_ms",
            "highres_allocation_rows",
            "target_filled_shares",
            "observed_filled_near_18",
            "post_fill_maker_delta_shares",
            "post_fill_maker_imbalance_ratio",
            "post_fill_maker_paired_coverage",
            "side_aligned_post_fill_delta_shares",
            "seconds_left",
            "target_price",
            "target_side_is_up",
            "side_aligned_direction_score",
            "side_aligned_spot_queue_imbalance",
            "side_aligned_futures_queue_imbalance",
            "side_aligned_spot_taker_imbalance_1s",
            "side_aligned_futures_taker_imbalance_1s",
        ]
        + list(TRAJECTORY_FEATURES)
    ),
}

PRIMARY_TASKS = {
    "next_taker_any_1s": "label_next_taker_any_1s",
    "next_taker_inventory_balancing_1s": "label_next_taker_inventory_balancing_1s",
    "balancing_given_taker_1s": "label_next_taker_balancing_given_taker_1s",
}
SENSITIVITY_TASKS = {
    "next_taker_any_2s": "label_next_taker_any_2s",
    "next_taker_any_5s": "label_next_taker_any_5s",
}


def _imports() -> dict[str, Any]:
    try:
        import joblib
        import numpy as np
        import pandas as pd
        from interpret.glassbox import ExplainableBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import (
            accuracy_score,
            average_precision_score,
            balanced_accuracy_score,
            brier_score_loss,
            f1_score,
            log_loss,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        from sklearn.utils.class_weight import compute_sample_weight
    except ImportError as exc:
        raise SystemExit('State Link V3 research dependencies missing. Run: pip install -e ".[research]"') from exc
    return locals()


def _market_order(df: Any) -> list[int]:
    rows = (
        df.groupby("market_id", as_index=False)["last_target_ms"]
        .min()
        .sort_values(["last_target_ms", "market_id"])
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


def _numeric(pd: Any, frame: Any, features: list[str]) -> Any:
    return frame[features].apply(pd.to_numeric, errors="coerce")


def _task_frame(df: Any, label: str) -> Any:
    frame = df[df[label].notna()].copy()
    frame[label] = frame[label].astype(int)
    return frame


def _fit_model(
    deps: dict[str, Any],
    X: Any,
    y: Any,
    *,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed: int,
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
    weights = deps["compute_sample_weight"]("balanced", y)
    model.fit(X, y, sample_weight=weights)
    return model


def _logit(np: Any, probability: Any) -> Any:
    p = np.clip(probability, 1e-7, 1 - 1e-7)
    return np.log(p / (1 - p)).reshape(-1, 1)


def _fit_calibrator(deps: dict[str, Any], y: Any, probability: Any) -> Any | None:
    if len(y) < 20 or set(int(value) for value in y.tolist()) != {0, 1}:
        return None
    model = deps["LogisticRegression"](C=100.0, max_iter=3000, random_state=42)
    model.fit(_logit(deps["np"], probability), y)
    return model


def _calibrated(deps: dict[str, Any], calibrator: Any | None, probability: Any) -> Any:
    if calibrator is None:
        return probability
    return calibrator.predict_proba(_logit(deps["np"], probability))[:, 1]


def _metrics(deps: dict[str, Any], y: Any, probability: Any) -> dict[str, Any]:
    np = deps["np"]
    if len(y) == 0:
        return {"rows": 0}
    p = np.clip(probability, 1e-7, 1 - 1e-7)
    predicted = (p >= 0.5).astype(int)
    both = set(int(value) for value in y.tolist()) == {0, 1}
    return {
        "rows": int(len(y)),
        "positives": int(y.sum()),
        "positiveRate": float(y.mean()),
        "accuracy": float(deps["accuracy_score"](y, predicted)),
        "balancedAccuracy": float(deps["balanced_accuracy_score"](y, predicted)),
        "precision": float(deps["precision_score"](y, predicted, zero_division=0)),
        "recall": float(deps["recall_score"](y, predicted, zero_division=0)),
        "f1": float(deps["f1_score"](y, predicted, zero_division=0)),
        "rocAuc": float(deps["roc_auc_score"](y, p)) if both else None,
        "averagePrecision": (
            float(deps["average_precision_score"](y, p)) if int(y.sum()) > 0 else None
        ),
        "logLoss": float(deps["log_loss"](y, p, labels=[0, 1])),
        "brier": float(deps["brier_score_loss"](y, p)),
    }


def _baseline_metrics(deps: dict[str, Any], y_train: Any, y_test: Any) -> dict[str, Any]:
    prior = float(y_train.mean())
    probability = deps["np"].full(
        len(y_test), min(1 - 1e-7, max(1e-7, prior))
    )
    result = _metrics(deps, y_test, probability)
    result["trainPositivePrior"] = prior
    return result


def _run_fold(
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

    y_train = train[label].astype(int)
    y_calibration = calibration[label].astype(int)
    y_test = test[label].astype(int)
    if (
        len(train) < 80
        or len(test) < 20
        or set(int(value) for value in y_train.unique()) != {0, 1}
    ):
        return {
            "status": "INSUFFICIENT_DATA",
            "trainRows": int(len(train)),
            "testRows": int(len(test)),
        }

    X_train = _numeric(pd, train, features)
    X_calibration = _numeric(pd, calibration, features)
    X_test = _numeric(pd, test, features)
    model = _fit_model(
        deps,
        X_train,
        y_train,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    raw_calibration = (
        model.predict_proba(X_calibration)[:, 1]
        if len(calibration)
        else deps["np"].array([])
    )
    calibrator = _fit_calibrator(deps, y_calibration, raw_calibration)
    raw_test = model.predict_proba(X_test)[:, 1]
    calibrated_test = _calibrated(deps, calibrator, raw_test)
    baseline = _baseline_metrics(deps, y_train, y_test)
    metrics = _metrics(deps, y_test, calibrated_test)
    return {
        "status": "OK",
        "trainRows": int(len(train)),
        "calibrationRows": int(len(calibration)),
        "testRows": int(len(test)),
        "testMarkets": fold["testMarkets"],
        "calibration": "TAIL_MARKETS_LOGIT" if calibrator is not None else "NONE",
        "baseline": baseline,
        "test": metrics,
        "logLossLiftVsPrior": float(baseline["logLoss"] - metrics["logLoss"]),
        "averagePrecisionLiftVsBaseRate": (
            float(metrics["averagePrecision"] - metrics["positiveRate"])
            if metrics.get("averagePrecision") is not None
            else None
        ),
        "aucLiftVsRandom": (
            float(metrics["rocAuc"] - 0.5)
            if metrics.get("rocAuc") is not None
            else None
        ),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("status") == "OK"]
    output: dict[str, Any] = {"folds": rows, "validFolds": len(valid)}
    for source_key in (
        "logLossLiftVsPrior",
        "averagePrecisionLiftVsBaseRate",
        "aucLiftVsRandom",
    ):
        values = [
            float(row[source_key])
            for row in valid
            if row.get(source_key) is not None
        ]
        output[f"mean_{source_key}"] = statistics.mean(values) if values else None
        output[f"median_{source_key}"] = statistics.median(values) if values else None
        output[f"positiveFoldRate_{source_key}"] = (
            sum(value > 0 for value in values) / len(values) if values else None
        )
    aucs = [
        float(row["test"]["rocAuc"])
        for row in valid
        if row["test"].get("rocAuc") is not None
    ]
    aps = [
        float(row["test"]["averagePrecision"])
        for row in valid
        if row["test"].get("averagePrecision") is not None
    ]
    output["meanTestRocAuc"] = statistics.mean(aucs) if aucs else None
    output["meanTestAveragePrecision"] = statistics.mean(aps) if aps else None
    output["meanTestLogLoss"] = (
        statistics.mean(float(row["test"]["logLoss"]) for row in valid)
        if valid
        else None
    )
    return output


def _term_summary(model: Any) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for name, indexes, importance in zip(
        model.term_names_, model.term_features_, model.term_importances()
    ):
        values.append(
            {
                "term": str(name),
                "importance": float(importance),
                "featureIndexes": [int(value) for value in indexes],
            }
        )
    values.sort(key=lambda row: row["importance"], reverse=True)
    return values[:40]


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _duration_bin(value: Any) -> str:
    number = _finite_number(value)
    if number is None:
        return "UNKNOWN"
    if number <= 250:
        return "LE_250MS"
    if number <= 500:
        return "250_500MS"
    if number <= 1000:
        return "500_1000MS"
    if number <= 1500:
        return "1000_1500MS"
    if number <= 2000:
        return "1500_2000MS"
    return "GT_2000MS"


def _inventory_bin(value: Any) -> str:
    number = _finite_number(value)
    if number is None:
        return "UNKNOWN"
    if number <= 0.10:
        return "BALANCED_LE_10PCT"
    if number <= 0.25:
        return "MODERATE_10_25PCT"
    return "IMBALANCED_GT_25PCT"


def _time_bin(value: Any) -> str:
    number = _finite_number(value)
    if number is None:
        return "UNKNOWN"
    if number <= 60:
        return "LATE_LE_60S"
    if number <= 200:
        return "MID_60_200S"
    return "EARLY_GT_200S"


def _direction_bin(value: Any) -> str:
    number = _finite_number(value)
    if number is None:
        return "UNKNOWN"
    if number <= -0.25:
        return "AGAINST_MAKER_SIDE"
    if number >= 0.25:
        return "WITH_MAKER_SIDE"
    return "NEUTRAL"


def _empirical_rows(frame: Any, groups: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, group in frame.groupby(groups, dropna=False):
        values = key if isinstance(key, tuple) else (key,)
        any_label = group["label_next_taker_any_1s"].astype(float)
        balancing_label = group["label_next_taker_inventory_balancing_1s"].astype(float)
        conditional = (
            group["label_next_taker_balancing_given_taker_1s"]
            .dropna()
            .astype(float)
        )
        row = {column: str(value) for column, value in zip(groups, values)}
        row.update(
            {
                "rows": int(len(group)),
                "nextTakerAny1sRate": float(any_label.mean()),
                "nextBalancingTaker1sRate": float(balancing_label.mean()),
                "balancingGivenTaker1sRate": (
                    float(conditional.mean()) if len(conditional) else None
                ),
                "takerConditionalRows": int(len(conditional)),
            }
        )
        rows.append(row)
    rows.sort(key=lambda row: tuple(row[column] for column in groups))
    return rows


def _empirical_state_tables(df: Any) -> dict[str, Any]:
    frame = df.copy()
    frame["fillDurationRegime"] = frame["highres_fill_duration_ms"].apply(_duration_bin)
    frame["postFillInventoryRegime"] = frame["post_fill_maker_imbalance_ratio"].apply(_inventory_bin)
    frame["timeRegime"] = frame["seconds_left"].apply(_time_bin)
    frame["directionRegime"] = frame["side_aligned_direction_score"].apply(_direction_bin)
    return {
        "duration": _empirical_rows(frame, ["fillDurationRegime"]),
        "postFillInventory": _empirical_rows(frame, ["postFillInventoryRegime"]),
        "durationByPostFillInventory": _empirical_rows(
            frame, ["fillDurationRegime", "postFillInventoryRegime"]
        ),
        "durationByTime": _empirical_rows(frame, ["fillDurationRegime", "timeRegime"]),
        "durationByDirection": _empirical_rows(
            frame, ["fillDurationRegime", "directionRegime"]
        ),
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
            "Train timestamp-safe EBM models for the Target Maker/Taker post-fill risk state. "
            "This is offline research only and never changes live or paper strategy policy."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--min-train-markets", type=int, default=60)
    parser.add_argument("--test-markets", type=int, default=18)
    parser.add_argument("--max-folds", type=int, default=4)
    parser.add_argument("--interactions", type=int, default=12)
    parser.add_argument("--max-rounds", type=int, default=3500)
    parser.add_argument("--outer-bags", type=int, default=8)
    args = parser.parse_args()

    deps = _imports()
    pd = deps["pd"]
    dataset_path = args.dataset.expanduser().resolve()
    if not dataset_path.exists():
        raise SystemExit(
            f"State Link V3 dataset missing: {dataset_path}\n"
            "Run: python tools/build_target_maker_taker_state_link_v3_dataset.py"
        )

    df = pd.read_csv(dataset_path)
    df["market_id"] = pd.to_numeric(df["market_id"], errors="raise").astype(int)
    df["last_target_ms"] = pd.to_numeric(df["last_target_ms"], errors="raise").astype("int64")
    markets = _market_order(df)
    folds = _walk_forward_folds(
        markets,
        min_train_markets=max(20, int(args.min_train_markets)),
        test_markets=max(5, int(args.test_markets)),
        max_folds=max(1, int(args.max_folds)),
    )

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "dataset": str(dataset_path),
        "rows": int(len(df)),
        "markets": len(markets),
        "walkForward": {
            "minTrainMarkets": max(20, int(args.min_train_markets)),
            "testMarkets": max(5, int(args.test_markets)),
            "maxFolds": max(1, int(args.max_folds)),
            "folds": folds,
        },
        "timestampBoundary": (
            "All target labels exclude same-second Taker events because target_event_ms is "
            "second-quantized. The first eligible future label bucket starts at anchorBucket+1."
        ),
        "inventoryBoundary": (
            "Inventory-repair labels use post-fill Maker inventory, including the current "
            "Maker parent filled shares before classifying later Taker side."
        ),
        "empiricalStateTables": _empirical_state_tables(df),
        "tasks": {},
        "sensitivity": {},
    }

    for task_index, (task_name, label) in enumerate(PRIMARY_TASKS.items()):
        frame = _task_frame(df, label)
        task_report: dict[str, Any] = {
            "label": label,
            "rows": int(len(frame)),
            "positives": int(frame[label].sum()) if len(frame) else 0,
            "positiveRate": float(frame[label].mean()) if len(frame) else None,
            "featureSets": {},
        }
        for feature_index, (feature_name, features) in enumerate(FEATURE_SETS.items()):
            fold_rows = [
                _run_fold(
                    deps=deps,
                    frame=frame,
                    label=label,
                    features=features,
                    fold=fold,
                    interactions=max(0, int(args.interactions)),
                    max_rounds=max(100, int(args.max_rounds)),
                    outer_bags=max(2, int(args.outer_bags)),
                    seed=42 + task_index * 100 + feature_index * 10 + fold_index,
                )
                for fold_index, fold in enumerate(folds)
            ]
            summary = _aggregate(fold_rows)
            summary["featureCount"] = len(features)
            summary["features"] = features
            task_report["featureSets"][feature_name] = summary

        ranking = sorted(
            [
                {
                    "featureSet": name,
                    "meanLogLossLiftVsPrior": values.get("mean_logLossLiftVsPrior"),
                    "positiveFoldRate": values.get("positiveFoldRate_logLossLiftVsPrior"),
                    "meanTestRocAuc": values.get("meanTestRocAuc"),
                    "meanTestAveragePrecision": values.get("meanTestAveragePrecision"),
                }
                for name, values in task_report["featureSets"].items()
                if values.get("mean_logLossLiftVsPrior") is not None
            ],
            key=lambda row: float(row["meanLogLossLiftVsPrior"]),
            reverse=True,
        )
        task_report["rankingByMeanLogLossLiftVsPrior"] = ranking
        report["tasks"][task_name] = task_report

    for task_index, (task_name, label) in enumerate(SENSITIVITY_TASKS.items()):
        frame = _task_frame(df, label)
        fold_rows = [
            _run_fold(
                deps=deps,
                frame=frame,
                label=label,
                features=FEATURE_SETS["compact_state"],
                fold=fold,
                interactions=max(0, int(args.interactions)),
                max_rounds=max(100, int(args.max_rounds)),
                outer_bags=max(2, int(args.outer_bags)),
                seed=900 + task_index * 20 + fold_index,
            )
            for fold_index, fold in enumerate(folds)
        ]
        report["sensitivity"][task_name] = {
            "label": label,
            "rows": int(len(frame)),
            "positiveRate": float(frame[label].mean()) if len(frame) else None,
            "featureSet": "compact_state",
            "walkForward": _aggregate(fold_rows),
        }

    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    final_models: dict[str, Any] = {}
    for task_index, (task_name, label) in enumerate(PRIMARY_TASKS.items()):
        frame = _task_frame(df, label)
        features = FEATURE_SETS["compact_state"]
        if len(frame) < 100 or set(int(value) for value in frame[label].unique()) != {0, 1}:
            final_models[task_name] = {"status": "INSUFFICIENT_DATA"}
            continue
        X = _numeric(pd, frame, features)
        y = frame[label].astype(int)
        model = _fit_model(
            deps,
            X,
            y,
            interactions=max(0, int(args.interactions)),
            max_rounds=max(100, int(args.max_rounds)),
            outer_bags=max(2, int(args.outer_bags)),
            seed=5000 + task_index,
        )
        path = model_dir / f"{task_name}.joblib"
        deps["joblib"].dump(
            {
                "reportVersion": REPORT_VERSION,
                "task": task_name,
                "label": label,
                "features": features,
                "model": model,
                "note": (
                    "Research artifact fit on all available rows after walk-forward evaluation. "
                    "Do not use for automatic strategy promotion."
                ),
            },
            path,
        )
        final_models[task_name] = {
            "status": "OK",
            "path": str(path),
            "topTerms": _term_summary(model),
        }
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
