from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from predict_bot.target_maker_ebm_v3_dataset import (
    DEFAULT_OUTPUT,
    FILL_PATH_FEATURES,
    POLICY_FEATURE_COLUMNS,
    TRAJECTORY_FEATURES,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_ebm_report_v3.json"
DEFAULT_MODEL_DIR = ROOT / "data" / "research" / "target_maker_ebm_models_v3"
REPORT_VERSION = "TARGET_MAKER_EBM_POLICY_DISCOVERY_V3_HIERARCHICAL_TRAJECTORY_WALK_FORWARD"

STATIC_FEATURES = [
    column for column in POLICY_FEATURE_COLUMNS
    if column not in set(FILL_PATH_FEATURES) | set(TRAJECTORY_FEATURES)
]
WITHOUT_TIMER_FEATURES = [
    column for column in POLICY_FEATURE_COLUMNS if column not in {"seconds_left", "resting_ms"}
]
FILL_CONTEXT = {
    "target_fill_count",
    "target_filled_shares",
    "multi_fill_parent",
    "observed_filled_near_18",
    "target_price",
    "target_side_is_up",
}
DYNAMIC_CONTEXT = {
    "target_price",
    "target_side_is_up",
    "prior_maker_imbalance_ratio",
    "side_aligned_prior_delta_shares",
}
FEATURE_SETS = {
    "full_v3": POLICY_FEATURE_COLUMNS,
    "without_legacy_timer": WITHOUT_TIMER_FEATURES,
    "static_v2_equivalent": STATIC_FEATURES,
    "fill_path_only": [c for c in POLICY_FEATURE_COLUMNS if c in set(FILL_PATH_FEATURES) | FILL_CONTEXT],
    "trajectory_only": [c for c in POLICY_FEATURE_COLUMNS if c in set(TRAJECTORY_FEATURES) | {"target_price", "target_side_is_up"}],
    "dynamic_path_plus_trajectory": [
        c for c in POLICY_FEATURE_COLUMNS
        if c in set(FILL_PATH_FEATURES) | set(TRAJECTORY_FEATURES) | DYNAMIC_CONTEXT
    ],
}

TASKS = {
    "continue_vs_stop": "label_continue",
    "refill_vs_reprice_given_continue": "label_refill_given_continue",
    "reprice_toward_vs_away": "label_reprice_toward_touch",
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
        raise SystemExit('V3 research dependencies missing. Run: pip install -e ".[research]"') from exc
    return locals()


def _market_order(df: Any) -> list[int]:
    rows = (
        df.groupby("market_id", as_index=False)["last_target_ms"]
        .min()
        .sort_values(["last_target_ms", "market_id"])
    )
    return [int(value) for value in rows["market_id"].tolist()]


def _walk_forward_folds(markets: list[int], *, min_train_markets: int, test_markets: int, max_folds: int) -> list[dict[str, list[int]]]:
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
        folds.append({
            "trainMarkets": prior[:-calibration_count],
            "calibrationMarkets": prior[-calibration_count:],
            "testMarkets": test,
        })
        end += test_markets
    return folds


def _numeric(pd: Any, frame: Any, features: list[str]) -> Any:
    return frame[features].apply(pd.to_numeric, errors="coerce")


def _task_frame(df: Any, label: str) -> Any:
    frame = df[df[label].notna()].copy()
    frame[label] = frame[label].astype(int)
    return frame


def _fit_model(deps: dict[str, Any], X: Any, y: Any, *, interactions: int, max_rounds: int, outer_bags: int, seed: int) -> Any:
    if set(int(v) for v in y.tolist()) != {0, 1}:
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


def _logit(np: Any, p: Any) -> Any:
    p = np.clip(p, 1e-7, 1 - 1e-7)
    return np.log(p / (1 - p)).reshape(-1, 1)


def _fit_calibrator(deps: dict[str, Any], y: Any, probability: Any) -> Any | None:
    if len(y) < 20 or set(int(v) for v in y.tolist()) != {0, 1}:
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
    both = set(int(v) for v in y.tolist()) == {0, 1}
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
        "averagePrecision": float(deps["average_precision_score"](y, p)) if int(y.sum()) > 0 else None,
        "logLoss": float(deps["log_loss"](y, p, labels=[0, 1])),
        "brier": float(deps["brier_score_loss"](y, p)),
    }


def _baseline_metrics(deps: dict[str, Any], y_train: Any, y_test: Any) -> dict[str, Any]:
    prior = float(y_train.mean())
    p = deps["np"].full(len(y_test), min(1 - 1e-7, max(1e-7, prior)))
    result = _metrics(deps, y_test, p)
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
    if len(train) < 80 or len(test) < 20 or set(y_train.unique()) != {0, 1}:
        return {"status": "INSUFFICIENT_DATA", "trainRows": int(len(train)), "testRows": int(len(test))}
    X_train = _numeric(pd, train, features)
    X_calibration = _numeric(pd, calibration, features)
    X_test = _numeric(pd, test, features)
    model = _fit_model(
        deps, X_train, y_train,
        interactions=interactions, max_rounds=max_rounds, outer_bags=outer_bags, seed=seed,
    )
    raw_calibration = model.predict_proba(X_calibration)[:, 1] if len(calibration) else deps["np"].array([])
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
        "aucLiftVsRandom": (float(metrics["rocAuc"] - 0.5) if metrics.get("rocAuc") is not None else None),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("status") == "OK"]
    output: dict[str, Any] = {"folds": rows, "validFolds": len(valid)}
    for source_key in ("logLossLiftVsPrior", "aucLiftVsRandom"):
        values = [float(row[source_key]) for row in valid if row.get(source_key) is not None]
        output[f"mean_{source_key}"] = statistics.mean(values) if values else None
        output[f"median_{source_key}"] = statistics.median(values) if values else None
        output[f"positiveFoldRate_{source_key}"] = (
            sum(value > 0 for value in values) / len(values) if values else None
        )
    aucs = [float(row["test"]["rocAuc"]) for row in valid if row["test"].get("rocAuc") is not None]
    losses = [float(row["test"]["logLoss"]) for row in valid]
    output["meanTestRocAuc"] = statistics.mean(aucs) if aucs else None
    output["meanTestLogLoss"] = statistics.mean(losses) if losses else None
    return output


def _term_summary(model: Any) -> list[dict[str, Any]]:
    values = []
    for name, indexes, importance in zip(model.term_names_, model.term_features_, model.term_importances()):
        values.append({
            "term": str(name),
            "importance": float(importance),
            "featureIndexes": [int(value) for value in indexes],
        })
    values.sort(key=lambda row: row["importance"], reverse=True)
    return values[:35]


def _empirical_structure(df: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    reprice = df[df["label_reprice_toward_touch"].notna()].copy()
    if len(reprice):
        direction_counts = reprice["label_reprice_toward_touch"].astype(int).value_counts().to_dict()
        ticks = reprice["label_reprice_ticks_abs"].dropna().astype(float)
        output["signedReprice"] = {
            "rows": int(len(reprice)),
            "towardTouch": int(direction_counts.get(1, 0)),
            "awayFromTouch": int(direction_counts.get(0, 0)),
            "towardTouchRate": float(direction_counts.get(1, 0) / len(reprice)),
            "medianAbsoluteTicks": float(ticks.median()) if len(ticks) else None,
            "tickDistribution": {
                str(int(tick)): int(count)
                for tick, count in ticks.round().astype(int).value_counts().sort_index().items()
            } if len(ticks) else {},
        }
    continued = df[df["label_continue"] == 1].copy()
    if len(continued):
        delay = continued["post_action_delay_ms"].dropna().astype(float)
        output["continueDelay"] = {
            "rows": int(len(delay)),
            "medianMs": float(delay.median()) if len(delay) else None,
            "p90Ms": float(delay.quantile(0.90)) if len(delay) else None,
            "cdf": {
                str(threshold): float((delay <= threshold).mean()) if len(delay) else None
                for threshold in (250, 500, 1000, 2000, 5000)
            },
        }
    fill = df.copy()
    fill["fill_speed_regime"] = "SLOW_GT_1000MS"
    fill.loc[fill["path_fill_duration_ms"] <= 1000, "fill_speed_regime"] = "MEDIUM_250_1000MS"
    fill.loc[fill["path_fill_duration_ms"] <= 250, "fill_speed_regime"] = "FAST_50_250MS"
    fill.loc[fill["path_fill_duration_ms"] <= 50, "fill_speed_regime"] = "INSTANT_LE_50MS"
    speed_rows = []
    for name, group in fill.groupby("fill_speed_regime"):
        speed_rows.append({
            "regime": str(name),
            "rows": int(len(group)),
            "continueRate": float(group["label_continue"].astype(float).mean()),
            "refillRateAllRows": float((group["post_action"] == "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT").mean()),
            "repriceRateAllRows": float((group["post_action"] == "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT").mean()),
        })
    output["fillSpeedActionTable"] = speed_rows
    return output


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
        description="Train hierarchical target Maker EBM V3 models with fill-path/trajectory features and expanding whole-market walk-forward validation."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--min-train-markets", type=int, default=60)
    parser.add_argument("--test-markets", type=int, default=18)
    parser.add_argument("--max-folds", type=int, default=4)
    parser.add_argument("--interactions", type=int, default=6)
    parser.add_argument("--max-rounds", type=int, default=1800)
    parser.add_argument("--outer-bags", type=int, default=4)
    parser.add_argument("--random-state", type=int, default=43)
    args = parser.parse_args()

    deps = _imports()
    pd = deps["pd"]
    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise SystemExit(f"V3 dataset not found: {dataset}. Run tools/build_target_maker_ebm_v3_dataset.py first.")
    df = pd.read_csv(dataset)
    required = set(POLICY_FEATURE_COLUMNS) | set(TASKS.values()) | {
        "market_id", "last_target_ms", "post_action", "post_action_delay_ms",
        "label_reprice_ticks_abs",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise SystemExit(f"V3 dataset missing required columns: {missing}")

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
        "targetEventsDriveTrading": False,
        "automaticStrategyPromotion": False,
        "dataset": str(dataset),
        "rows": int(len(df)),
        "markets": len(markets),
        "walkForward": {
            "method": "expanding chronological whole-market train; tail prior markets calibrate; next unseen markets test",
            "folds": folds,
        },
        "featureSets": {name: features for name, features in FEATURE_SETS.items()},
        "hierarchicalTasks": {},
        "empiricalStructure": _empirical_structure(df),
        "interpretationBoundary": (
            "V3 tests whether target Maker continuation, refill-vs-reprice, signed reprice direction, and action timing are stable out of time. "
            "All features are strict pre/at-anchor observations; post-action fields are labels only."
        ),
    }

    for task_name, label in TASKS.items():
        frame = _task_frame(df, label)
        sets = FEATURE_SETS if task_name == "continue_vs_stop" else {
            key: FEATURE_SETS[key]
            for key in ("full_v3", "without_legacy_timer", "dynamic_path_plus_trajectory")
        }
        experiments: dict[str, Any] = {}
        for set_name, features in sets.items():
            rows = []
            for fold_index, fold in enumerate(folds):
                rows.append(_run_fold(
                    deps=deps,
                    frame=frame,
                    label=label,
                    features=features,
                    fold=fold,
                    interactions=min(args.interactions, max(0, len(features) // 3)),
                    max_rounds=args.max_rounds,
                    outer_bags=args.outer_bags,
                    seed=args.random_state + fold_index,
                ))
            experiments[set_name] = _aggregate(rows)
        ranking = sorted(
            (
                {
                    "featureSet": name,
                    "meanLogLossLiftVsPrior": row.get("mean_logLossLiftVsPrior"),
                    "positiveFoldRate": row.get("positiveFoldRate_logLossLiftVsPrior"),
                    "meanTestRocAuc": row.get("meanTestRocAuc"),
                }
                for name, row in experiments.items()
            ),
            key=lambda row: -(-1e9 if row["meanLogLossLiftVsPrior"] is None else row["meanLogLossLiftVsPrior"]),
        )
        report["hierarchicalTasks"][task_name] = {
            "label": label,
            "rows": int(len(frame)),
            "positiveRate": float(frame[label].astype(int).mean()) if len(frame) else None,
            "experiments": experiments,
            "rankingByMeanWalkForwardLogLossLift": ranking,
        }

    # Fit one latest explanatory model per task. These are saved for inspection only;
    # walk-forward results above remain the promotion gate.
    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    final_models: dict[str, Any] = {}
    final_fold = folds[-1]
    for task_name, label in TASKS.items():
        frame = _task_frame(df, label)
        train_markets = final_fold["trainMarkets"] + final_fold["calibrationMarkets"]
        train = frame[frame["market_id"].isin(train_markets)].copy()
        if len(train) < 80 or set(train[label].astype(int).unique()) != {0, 1}:
            final_models[task_name] = {"status": "INSUFFICIENT_DATA"}
            continue
        X = _numeric(pd, train, POLICY_FEATURE_COLUMNS)
        y = train[label].astype(int)
        model = _fit_model(
            deps, X, y,
            interactions=args.interactions,
            max_rounds=args.max_rounds,
            outer_bags=args.outer_bags,
            seed=args.random_state,
        )
        path = model_dir / f"{task_name}.joblib"
        deps["joblib"].dump({
            "reportVersion": REPORT_VERSION,
            "task": task_name,
            "label": label,
            "features": POLICY_FEATURE_COLUMNS,
            "model": model,
        }, path)
        final_models[task_name] = {
            "status": "TRAINED_FOR_EXPLANATION_ONLY",
            "modelFile": str(path),
            "topTerms": _term_summary(model),
        }
    report["latestExplanatoryModels"] = final_models

    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(report_path)
    print(json.dumps(_clean(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
