from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from predict_bot.target_maker_ebm_dataset import DEFAULT_OUTPUT, FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_ebm_report_v2.json"
DEFAULT_MODEL_DIR = ROOT / "data" / "research" / "target_maker_ebm_models_v2"
REPORT_VERSION = "TARGET_MAKER_EBM_POLICY_DISCOVERY_V2"

ACTION_CLASSES = ["NO_ACTION", "SAME_PRICE_REFILL", "REPRICE_1_3_TICKS"]
POST_ACTION_TO_CLASS = {
    "NO_CONFIRMED_NEXT_PARENT_5S": "NO_ACTION",
    "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT": "SAME_PRICE_REFILL",
    "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT": "REPRICE_1_3_TICKS",
}
CLASS_TO_INDEX = {name: index for index, name in enumerate(ACTION_CLASSES)}

# These are inference-quality diagnostics, not target-policy inputs. Keeping them
# out prevents the model from learning the observer's confidence machinery.
OBSERVER_QUALITY_ONLY_FEATURES = {
    "placement_coverage",
    "fill_allocation_coverage",
    "parent_confidence",
    "placement_supports_18",
    "signal_age_ms",
}
POLICY_FEATURES = [column for column in FEATURE_COLUMNS if column not in OBSERVER_QUALITY_ONLY_FEATURES]

TIMER_FEATURES = {
    "seconds_left",
    "resting_ms",
}
FILL_LIFECYCLE_FEATURES = {
    "resting_ms",
    "target_fill_count",
    "target_filled_shares",
    "expected_parent_shares",
    "multi_fill_parent",
    "observed_filled_near_18",
}
INVENTORY_FEATURES = {
    "prior_parent_count",
    "prior_maker_up_shares",
    "prior_maker_down_shares",
    "prior_maker_delta_shares",
    "prior_maker_imbalance_ratio",
    "prior_maker_paired_coverage",
    "side_aligned_prior_delta_shares",
}
PRICE_TIME_FEATURES = {
    "seconds_left",
    "target_side_is_up",
    "target_price",
    "native_price",
    "predict_up_bid",
    "predict_up_ask",
    "predict_up_mid",
    "predict_down_bid",
    "predict_down_ask",
    "predict_down_mid",
    "predict_up_spread",
    "predict_down_spread",
    "side_predict_bid",
    "side_predict_ask",
    "side_predict_mid",
    "side_predict_spread",
    "opposite_predict_bid",
    "opposite_predict_ask",
    "opposite_predict_mid",
    "opposite_predict_spread",
}
MICROSTRUCTURE_FEATURES = {
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
    "direction_score",
    "side_aligned_direction_score",
    "side_aligned_spot_queue_imbalance",
    "side_aligned_spot_taker_imbalance_250ms",
    "side_aligned_spot_taker_imbalance_1s",
    "side_aligned_spot_return_1s_bps",
    "side_aligned_spot_return_3s_bps",
    "side_aligned_futures_queue_imbalance",
    "side_aligned_futures_taker_imbalance_250ms",
    "side_aligned_futures_taker_imbalance_1s",
    "side_aligned_futures_return_1s_bps",
    "side_aligned_futures_return_3s_bps",
}
ORACLE_DISTANCE_FEATURES = {
    "strike_price",
    "spot_price",
    "spot_microprice",
    "futures_price",
    "futures_microprice",
    "perp_spot_basis_bps",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps",
}


def _ordered_features(values: set[str]) -> list[str]:
    return [column for column in POLICY_FEATURES if column in values]


HYPOTHESIS_FEATURE_SETS = {
    # Directly tests the old fixed-timeout hypothesis.
    "timer_only": _ordered_features(TIMER_FEATURES),
    # Does full/partial 18-share lifecycle state explain the next action?
    "fill_lifecycle_only": _ordered_features(FILL_LIFECYCLE_FEATURES),
    # Tests whether inventory/pairing state drives refill vs reprice.
    "inventory_only": _ordered_features(INVENTORY_FEATURES),
    # Tests the idea that quote regime is mostly price + time dependent.
    "price_time_only": _ordered_features(PRICE_TIME_FEATURES),
    # Tests whether public spot/futures flow is independently informative.
    "microstructure_only": _ordered_features(MICROSTRUCTURE_FEATURES),
    # Tests strike/basis/oracle-relative location independently.
    "oracle_distance_only": _ordered_features(ORACLE_DISTANCE_FEATURES),
    # Leave-one-family-out ablations answer whether a family adds incremental value.
    "all_without_timer": [column for column in POLICY_FEATURES if column not in TIMER_FEATURES],
    "all_without_inventory": [column for column in POLICY_FEATURES if column not in INVENTORY_FEATURES],
    "all_without_microstructure": [column for column in POLICY_FEATURES if column not in MICROSTRUCTURE_FEATURES],
    "all_without_fill_lifecycle": [column for column in POLICY_FEATURES if column not in FILL_LIFECYCLE_FEATURES],
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
            balanced_accuracy_score,
            f1_score,
            log_loss,
            precision_recall_fscore_support,
            roc_auc_score,
        )
        from sklearn.utils.class_weight import compute_sample_weight
    except ImportError as exc:
        raise SystemExit(
            "EBM research dependencies are missing. Install them with: pip install -e \".[research]\""
        ) from exc
    return locals()


def _market_splits(
    df: Any,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> tuple[list[int], list[int], list[int]]:
    market_times = (
        df.groupby("market_id", as_index=False)["last_target_ms"]
        .min()
        .sort_values(["last_target_ms", "market_id"])
    )
    markets = [int(value) for value in market_times["market_id"].tolist()]
    if len(markets) < 9:
        raise ValueError(f"need at least 9 markets for chronological train/validation/test split; got {len(markets)}")
    train_count = max(3, int(len(markets) * train_fraction))
    validation_count = max(2, int(len(markets) * validation_fraction))
    if train_count + validation_count >= len(markets):
        validation_count = 2
        train_count = len(markets) - 3
    return (
        markets[:train_count],
        markets[train_count : train_count + validation_count],
        markets[train_count + validation_count :],
    )


def _attach_action_label(df: Any) -> Any:
    unknown = sorted(set(str(value) for value in df["post_action"].dropna().unique()) - set(POST_ACTION_TO_CLASS))
    if unknown:
        raise ValueError(f"dataset contains unsupported post_action values: {unknown}")
    result = df.copy()
    result["next_action"] = result["post_action"].map(POST_ACTION_TO_CLASS)
    if result["next_action"].isna().any():
        raise ValueError("dataset contains rows without a supported next action")
    result["next_action_index"] = result["next_action"].map(CLASS_TO_INDEX).astype(int)
    return result


def _numeric_frame(pd: Any, df: Any, features: list[str]) -> Any:
    return df[features].apply(pd.to_numeric, errors="coerce")


def _probability_logits(np: Any, probabilities: Any) -> Any:
    clipped = np.clip(probabilities, 1e-8, 1.0)
    return np.log(clipped)


def _fit_probability_calibrator(deps: dict[str, Any], y_validation: Any, raw_probability: Any) -> Any | None:
    if len(y_validation) < 30 or set(int(value) for value in y_validation.tolist()) != set(range(len(ACTION_CLASSES))):
        return None
    calibrator = deps["LogisticRegression"](
        C=100.0,
        max_iter=4000,
        random_state=42,
    )
    calibrator.fit(_probability_logits(deps["np"], raw_probability), y_validation)
    return calibrator


def _apply_probability_calibrator(deps: dict[str, Any], calibrator: Any | None, raw_probability: Any) -> Any:
    if calibrator is None:
        return raw_probability
    calibrated = calibrator.predict_proba(_probability_logits(deps["np"], raw_probability))
    # Validation requires every class, so dimensions should match. Keep a hard
    # guard because silent class reordering would invalidate the report.
    expected = list(range(len(ACTION_CLASSES)))
    actual = [int(value) for value in calibrator.classes_.tolist()]
    if actual != expected:
        raise ValueError(f"calibrator class order mismatch: expected {expected}, got {actual}")
    return calibrated


def _ece(np: Any, y_true: Any, probabilities: Any, bins: int = 10) -> float | None:
    if len(y_true) == 0:
        return None
    predicted = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    correct = (predicted == y_true.to_numpy()).astype(float)
    total = float(len(y_true))
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        mask = (confidence >= low) & (confidence < high if index < bins - 1 else confidence <= high)
        count = int(mask.sum())
        if count <= 0:
            continue
        error += count / total * abs(float(correct[mask].mean()) - float(confidence[mask].mean()))
    return float(error)


def _multiclass_metrics(deps: dict[str, Any], y_true: Any, probabilities: Any) -> dict[str, Any]:
    np = deps["np"]
    if len(y_true) == 0:
        return {"rows": 0}
    labels = list(range(len(ACTION_CLASSES)))
    predicted = probabilities.argmax(axis=1)
    precision, recall, f1, support = deps["precision_recall_fscore_support"](
        y_true,
        predicted,
        labels=labels,
        zero_division=0,
    )
    try:
        macro_auc = float(deps["roc_auc_score"](
            y_true,
            probabilities,
            labels=labels,
            multi_class="ovr",
            average="macro",
        ))
    except ValueError:
        macro_auc = None
    one_hot = np.eye(len(ACTION_CLASSES))[y_true.to_numpy(dtype=int)]
    per_class = {}
    counts = y_true.value_counts().to_dict()
    for index, name in enumerate(ACTION_CLASSES):
        per_class[name] = {
            "rows": int(counts.get(index, 0)),
            "rate": float(counts.get(index, 0) / len(y_true)),
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
    return {
        "rows": int(len(y_true)),
        "accuracy": float(deps["accuracy_score"](y_true, predicted)),
        "balancedAccuracy": float(deps["balanced_accuracy_score"](y_true, predicted)),
        "macroF1": float(deps["f1_score"](y_true, predicted, labels=labels, average="macro", zero_division=0)),
        "macroOvrRocAuc": macro_auc,
        "logLoss": float(deps["log_loss"](y_true, probabilities, labels=labels)),
        "multiclassBrier": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "expectedCalibrationError10": _ece(np, y_true, probabilities, bins=10),
        "perClass": per_class,
    }


def _prior_baseline_metrics(deps: dict[str, Any], y_train: Any, y_test: Any) -> dict[str, Any]:
    np = deps["np"]
    counts = np.bincount(y_train.to_numpy(dtype=int), minlength=len(ACTION_CLASSES)).astype(float)
    priors = counts / counts.sum()
    probabilities = np.tile(priors, (len(y_test), 1))
    result = _multiclass_metrics(deps, y_test, probabilities)
    result["classPriorsFromTrain"] = {name: float(priors[index]) for index, name in enumerate(ACTION_CLASSES)}
    return result


def _term_summary(model: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    importances = model.term_importances()
    terms: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    for index, (name, features, importance) in enumerate(zip(model.term_names_, model.term_features_, importances)):
        row = {
            "rankSourceIndex": int(index),
            "term": str(name),
            "importance": float(importance),
            "featureIndexes": [int(value) for value in features],
        }
        terms.append(row)
        if len(features) > 1:
            interactions.append(row)
    terms.sort(key=lambda row: row["importance"], reverse=True)
    interactions.sort(key=lambda row: row["importance"], reverse=True)
    return terms[:40], interactions[:20]


def _response_probes(
    *,
    model: Any,
    calibrator: Any | None,
    deps: dict[str, Any],
    X_train: Any,
    top_terms: list[dict[str, Any]],
) -> dict[str, Any]:
    pd = deps["pd"]
    baseline = X_train.median(numeric_only=True).reindex(X_train.columns).fillna(0.0)
    probes: dict[str, Any] = {}
    selected: list[str] = []
    for term in top_terms:
        if len(term["featureIndexes"]) != 1:
            continue
        feature = str(term["term"])
        if feature not in X_train.columns or feature in selected:
            continue
        selected.append(feature)
        if len(selected) >= 15:
            break
    for feature in selected:
        values = X_train[feature].dropna()
        if values.empty:
            continue
        quantiles = values.quantile([0.10, 0.25, 0.50, 0.75, 0.90]).tolist()
        unique_values: list[float] = []
        for value in quantiles:
            number = float(value)
            if not any(math.isclose(number, existing, rel_tol=1e-12, abs_tol=1e-12) for existing in unique_values):
                unique_values.append(number)
        frame = pd.DataFrame([baseline.to_dict() for _ in unique_values], columns=X_train.columns)
        frame[feature] = unique_values
        raw_probability = model.predict_proba(frame)
        calibrated_probability = _apply_probability_calibrator(deps, calibrator, raw_probability)
        rows = []
        for row_index, value in enumerate(unique_values):
            rows.append({
                "value": value,
                "raw": {
                    name: float(raw_probability[row_index, class_index])
                    for class_index, name in enumerate(ACTION_CLASSES)
                },
                "calibrated": {
                    name: float(calibrated_probability[row_index, class_index])
                    for class_index, name in enumerate(ACTION_CLASSES)
                },
            })
        probes[feature] = rows
    return probes


def _derive_regimes(df: Any) -> Any:
    result = df.copy()
    result["time_regime"] = "MID_60_200S"
    result.loc[result["seconds_left"] <= 60, "time_regime"] = "LATE_LE_60S"
    result.loc[result["seconds_left"] > 200, "time_regime"] = "EARLY_GT_200S"

    result["price_regime"] = "MID_033_067"
    result.loc[result["target_price"] < 0.33, "price_regime"] = "LOW_LT_033"
    result.loc[result["target_price"] > 0.67, "price_regime"] = "HIGH_GT_067"

    result["fill_regime"] = result["observed_filled_near_18"].fillna(0).astype(int).map(
        {0: "PARTIAL_OR_OTHER", 1: "FILLED_NEAR_18"}
    )
    result["side_regime"] = result["target_side"].astype(str)

    result["inventory_regime"] = "UNKNOWN"
    inventory = result["prior_maker_imbalance_ratio"]
    result.loc[inventory.notna() & (inventory <= 0.10), "inventory_regime"] = "BALANCED_LE_010"
    result.loc[inventory.notna() & (inventory > 0.10) & (inventory <= 0.25), "inventory_regime"] = "MODERATE_010_025"
    result.loc[inventory.notna() & (inventory > 0.25), "inventory_regime"] = "IMBALANCED_GT_025"

    result["queue_regime"] = "NEUTRAL"
    queue = result["side_aligned_spot_queue_imbalance"]
    result.loc[queue.isna(), "queue_regime"] = "UNKNOWN"
    result.loc[queue.notna() & (queue <= -0.50), "queue_regime"] = "ADVERSE_LE_-050"
    result.loc[queue.notna() & (queue >= 0.50), "queue_regime"] = "SUPPORTIVE_GE_050"

    result["direction_regime"] = "NEUTRAL"
    direction = result["side_aligned_direction_score"]
    result.loc[direction.isna(), "direction_regime"] = "UNKNOWN"
    result.loc[direction.notna() & (direction <= -0.25), "direction_regime"] = "AGAINST_SIDE_LE_-025"
    result.loc[direction.notna() & (direction >= 0.25), "direction_regime"] = "WITH_SIDE_GE_025"

    result["strike_regime"] = "UNKNOWN"
    strike_distance = result["spot_minus_strike_bps"].abs()
    result.loc[strike_distance.notna() & (strike_distance <= 1.0), "strike_regime"] = "PINNED_LE_1BPS"
    result.loc[strike_distance.notna() & (strike_distance > 1.0) & (strike_distance <= 3.0), "strike_regime"] = "NEAR_1_3BPS"
    result.loc[strike_distance.notna() & (strike_distance > 3.0), "strike_regime"] = "FAR_GT_3BPS"
    return result


def _empirical_action_tables(df: Any, *, min_rows: int) -> dict[str, Any]:
    specs = {
        "time": ["time_regime"],
        "price": ["price_regime"],
        "fill": ["fill_regime"],
        "side": ["side_regime"],
        "inventory": ["inventory_regime"],
        "queue": ["queue_regime"],
        "direction": ["direction_regime"],
        "strikeDistance": ["strike_regime"],
        "timeByPrice": ["time_regime", "price_regime"],
        "fillByQueue": ["fill_regime", "queue_regime"],
        "inventoryByFill": ["inventory_regime", "fill_regime"],
        "directionByInventory": ["direction_regime", "inventory_regime"],
        "priceByQueue": ["price_regime", "queue_regime"],
    }
    output: dict[str, Any] = {}
    for name, columns in specs.items():
        rows = []
        for keys, group in df.groupby(columns, dropna=False):
            if len(group) < min_rows:
                continue
            if not isinstance(keys, tuple):
                keys = (keys,)
            counts = group["next_action"].value_counts().to_dict()
            rows.append({
                "regime": {column: str(value) for column, value in zip(columns, keys)},
                "rows": int(len(group)),
                "actions": {
                    action: {
                        "count": int(counts.get(action, 0)),
                        "rate": float(counts.get(action, 0) / len(group)),
                    }
                    for action in ACTION_CLASSES
                },
            })
        output[name] = rows
    return output


def _regime_model_slices(
    *,
    df: Any,
    probabilities: Any,
    deps: dict[str, Any],
    min_rows: int,
) -> list[dict[str, Any]]:
    # Single and selected cross-regime slices expose where an otherwise weak
    # global model is actually useful or misleading.
    columns = [
        "time_regime",
        "price_regime",
        "fill_regime",
        "side_regime",
        "inventory_regime",
        "queue_regime",
        "direction_regime",
        "strike_regime",
    ]
    combinations = [
        ("time_regime", "price_regime"),
        ("fill_regime", "queue_regime"),
        ("inventory_regime", "fill_regime"),
        ("price_regime", "queue_regime"),
    ]
    slices: list[dict[str, Any]] = []

    def add_slice(label: str, mask: Any) -> None:
        indexes = deps["np"].flatnonzero(mask.to_numpy())
        if len(indexes) < min_rows:
            return
        subset = df.iloc[indexes]
        y = subset["next_action_index"].astype(int)
        local_counts = y.value_counts().to_dict()
        local_majority = max(local_counts.values()) / len(y)
        metrics = _multiclass_metrics(deps, y, probabilities[indexes])
        slices.append({
            "slice": label,
            "rows": int(len(y)),
            "localMajorityAccuracy": float(local_majority),
            "accuracyLiftVsLocalMajority": float(metrics["accuracy"] - local_majority),
            "metrics": metrics,
        })

    for column in columns:
        for value in sorted(str(item) for item in df[column].dropna().unique()):
            add_slice(f"{column}={value}", df[column].astype(str) == value)
    for left, right in combinations:
        pairs = df[[left, right]].astype(str).drop_duplicates().itertuples(index=False, name=None)
        for left_value, right_value in pairs:
            add_slice(
                f"{left}={left_value}|{right}={right_value}",
                (df[left].astype(str) == left_value) & (df[right].astype(str) == right_value),
            )
    slices.sort(key=lambda row: (-row["accuracyLiftVsLocalMajority"], -row["rows"]))
    return slices


def _fit_ebm(
    *,
    deps: dict[str, Any],
    X_train: Any,
    y_train: Any,
    interactions: int,
    random_state: int,
    max_rounds: int,
    outer_bags: int,
) -> Any:
    sample_weight = deps["compute_sample_weight"]("balanced", y_train)
    model = deps["ExplainableBoostingClassifier"](
        interactions=max(0, int(interactions)),
        random_state=int(random_state),
        max_rounds=max(100, int(max_rounds)),
        early_stopping_rounds=100,
        outer_bags=max(2, int(outer_bags)),
        n_jobs=-2,
    )
    model.fit(X_train, y_train, sample_weight=sample_weight)
    return model


def _train_hypothesis_suite(
    *,
    deps: dict[str, Any],
    train: Any,
    validation: Any,
    test: Any,
    y_train: Any,
    y_validation: Any,
    y_test: Any,
    interactions: int,
    random_state: int,
    max_rounds: int,
    outer_bags: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    experiments: dict[str, Any] = {}
    for name, features in HYPOTHESIS_FEATURE_SETS.items():
        if not features:
            continue
        X_train = _numeric_frame(pd, train, features)
        X_validation = _numeric_frame(pd, validation, features)
        X_test = _numeric_frame(pd, test, features)
        model = _fit_ebm(
            deps=deps,
            X_train=X_train,
            y_train=y_train,
            interactions=min(interactions, max(0, len(features) // 2)),
            random_state=random_state,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
        )
        raw_validation = model.predict_proba(X_validation)
        calibrator = _fit_probability_calibrator(deps, y_validation, raw_validation)
        raw_test = model.predict_proba(X_test)
        calibrated_test = _apply_probability_calibrator(deps, calibrator, raw_test)
        experiments[name] = {
            "features": features,
            "featureCount": len(features),
            "calibration": "VALIDATION_MULTINOMIAL_LOGIT_ON_RAW_LOG_PROBABILITIES" if calibrator is not None else "NONE",
            "testRaw": _multiclass_metrics(deps, y_test, raw_test),
            "testCalibrated": _multiclass_metrics(deps, y_test, calibrated_test),
        }
    ranking = sorted(
        (
            {
                "experiment": name,
                "logLoss": row["testCalibrated"].get("logLoss"),
                "macroF1": row["testCalibrated"].get("macroF1"),
                "accuracy": row["testCalibrated"].get("accuracy"),
            }
            for name, row in experiments.items()
        ),
        key=lambda row: (float("inf") if row["logLoss"] is None else row["logLoss"], -row["macroF1"]),
    )
    return {
        "purpose": "predefined feature-family experiments test prior Maker hypotheses without promoting them to strategy rules",
        "experiments": experiments,
        "rankingByCalibratedTestLogLoss": ranking,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train a calibrated multiclass EBM for target Maker next action and run regime/hypothesis experiments "
            "on chronological whole-market holdout data."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--interactions", type=int, default=12)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-rounds", type=int, default=5000)
    parser.add_argument("--outer-bags", type=int, default=8)
    parser.add_argument("--regime-min-rows", type=int, default=40)
    parser.add_argument("--empirical-min-rows", type=int, default=25)
    parser.add_argument("--skip-hypothesis-suite", action="store_true")
    parser.add_argument("--hypothesis-interactions", type=int, default=4)
    parser.add_argument("--hypothesis-max-rounds", type=int, default=2200)
    parser.add_argument("--hypothesis-outer-bags", type=int, default=4)
    args = parser.parse_args()

    deps = _imports()
    pd = deps["pd"]
    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise SystemExit(f"dataset not found: {dataset}. Run tools/build_target_maker_ebm_dataset.py first.")
    df = pd.read_csv(dataset)
    required = set(POLICY_FEATURES) | {"market_id", "last_target_ms", "target_side", "post_action"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise SystemExit(f"dataset is missing required columns: {missing}")
    df = _attach_action_label(df)

    train_markets, validation_markets, test_markets = _market_splits(df)
    train = df[df["market_id"].isin(train_markets)].copy()
    validation = df[df["market_id"].isin(validation_markets)].copy()
    test = df[df["market_id"].isin(test_markets)].copy()
    y_train = train["next_action_index"].astype(int)
    y_validation = validation["next_action_index"].astype(int)
    y_test = test["next_action_index"].astype(int)
    if set(y_train.unique()) != set(range(len(ACTION_CLASSES))):
        raise SystemExit(f"training markets do not contain all next-action classes: {sorted(y_train.unique().tolist())}")

    X_train = _numeric_frame(pd, train, POLICY_FEATURES)
    X_validation = _numeric_frame(pd, validation, POLICY_FEATURES)
    X_test = _numeric_frame(pd, test, POLICY_FEATURES)
    model = _fit_ebm(
        deps=deps,
        X_train=X_train,
        y_train=y_train,
        interactions=args.interactions,
        random_state=args.random_state,
        max_rounds=args.max_rounds,
        outer_bags=args.outer_bags,
    )
    raw_validation = model.predict_proba(X_validation)
    calibrator = _fit_probability_calibrator(deps, y_validation, raw_validation)
    calibrated_validation = _apply_probability_calibrator(deps, calibrator, raw_validation)
    raw_test = model.predict_proba(X_test)
    calibrated_test = _apply_probability_calibrator(deps, calibrator, raw_test)
    top_terms, top_interactions = _term_summary(model)

    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "next_action_multiclass.joblib"
    deps["joblib"].dump(
        {
            "reportVersion": REPORT_VERSION,
            "modelName": "next_action_multiclass",
            "actionClasses": ACTION_CLASSES,
            "policyFeatures": POLICY_FEATURES,
            "model": model,
            "calibrator": calibrator,
            "calibrationInput": "log(raw predict_proba)",
        },
        model_path,
    )

    test_regimes = _derive_regimes(test)
    regime_slices = _regime_model_slices(
        df=test_regimes,
        probabilities=calibrated_test,
        deps=deps,
        min_rows=max(10, int(args.regime_min_rows)),
    )
    promising = [
        row for row in regime_slices
        if row["rows"] >= max(80, int(args.regime_min_rows)) and row["accuracyLiftVsLocalMajority"] >= 0.03
    ][:25]

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "targetEventsDriveTrading": False,
        "automaticStrategyPromotion": False,
        "dataset": str(dataset),
        "rows": int(len(df)),
        "markets": int(df["market_id"].nunique()),
        "decisionPoint": "AFTER_PARENT_FILL_BEFORE_NEXT_PARENT",
        "actionClasses": ACTION_CLASSES,
        "policyFeatures": POLICY_FEATURES,
        "excludedObserverQualityFeatures": sorted(OBSERVER_QUALITY_ONLY_FEATURES),
        "split": {
            "method": "chronological whole-market split; a market never appears in more than one partition",
            "trainMarkets": train_markets,
            "validationMarkets": validation_markets,
            "testMarkets": test_markets,
        },
        "classDistribution": {
            "train": {name: int((y_train == index).sum()) for index, name in enumerate(ACTION_CLASSES)},
            "validation": {name: int((y_validation == index).sum()) for index, name in enumerate(ACTION_CLASSES)},
            "test": {name: int((y_test == index).sum()) for index, name in enumerate(ACTION_CLASSES)},
        },
        "nextActionModel": {
            "status": "TRAINED",
            "classWeighting": "balanced sample_weight on training markets only",
            "probabilityCalibration": (
                "VALIDATION_MULTINOMIAL_LOGIT_ON_RAW_LOG_PROBABILITIES" if calibrator is not None else "NONE"
            ),
            "baseline": _prior_baseline_metrics(deps, y_train, y_test),
            "validationRaw": _multiclass_metrics(deps, y_validation, raw_validation),
            "validationCalibrated": _multiclass_metrics(deps, y_validation, calibrated_validation),
            "testRaw": _multiclass_metrics(deps, y_test, raw_test),
            "testCalibrated": _multiclass_metrics(deps, y_test, calibrated_test),
            "topTerms": top_terms,
            "topInteractions": top_interactions,
            "featureResponseProbes": _response_probes(
                model=model,
                calibrator=calibrator,
                deps=deps,
                X_train=X_train,
                top_terms=top_terms,
            ),
            "modelFile": str(model_path),
        },
        "regimeAnalysis": {
            "definition": {
                "time": "late <=60s; mid 60-200s; early >200s",
                "price": "low <0.33; mid 0.33-0.67; high >0.67",
                "fill": "observed cumulative parent fill near 18 shares vs partial/other",
                "inventory": "prior Maker absolute imbalance <=10%, 10-25%, >25%",
                "queue": "side-aligned spot queue <=-0.5 adverse, >=0.5 supportive",
                "direction": "side-aligned direction score <=-0.25 against, >=0.25 with side",
                "strike": "absolute spot-minus-strike <=1 bps, 1-3 bps, >3 bps",
            },
            "empiricalTestActionTables": _empirical_action_tables(
                test_regimes,
                min_rows=max(10, int(args.empirical_min_rows)),
            ),
            "modelTestSlices": regime_slices,
            "promisingSlicesAccuracyLiftGe3PctAndRowsGe80": promising,
        },
        "hypothesisSuite": None,
        "interpretationBoundary": (
            "V2 discovers associations and out-of-time regime structure in forward-collected target lifecycle behavior. "
            "No learned rule is automatically promoted to a paper or live strategy."
        ),
    }

    if not args.skip_hypothesis_suite:
        report["hypothesisSuite"] = _train_hypothesis_suite(
            deps=deps,
            train=train,
            validation=validation,
            test=test,
            y_train=y_train,
            y_validation=y_validation,
            y_test=y_test,
            interactions=max(0, int(args.hypothesis_interactions)),
            random_state=args.random_state,
            max_rounds=max(100, int(args.hypothesis_max_rounds)),
            outer_bags=max(2, int(args.hypothesis_outer_bags)),
        )

    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
