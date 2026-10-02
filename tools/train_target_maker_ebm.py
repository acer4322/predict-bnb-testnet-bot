from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from predict_bot.target_maker_ebm_dataset import DEFAULT_OUTPUT, FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_ebm_report.json"
DEFAULT_MODEL_DIR = ROOT / "data" / "research" / "target_maker_ebm_models"
REPORT_VERSION = "TARGET_MAKER_EBM_POLICY_DISCOVERY_V1"

# These fields are useful for filtering/diagnostics but should not drive the learned
# policy: otherwise EBM can learn our 8778 inference confidence instead of target
# behavior. The remaining lifecycle fields are all known by the post-fill decision
# point or strict as-of 8777 state.
OBSERVER_QUALITY_ONLY_FEATURES = {
    "placement_coverage",
    "fill_allocation_coverage",
    "parent_confidence",
    "placement_supports_18",
    "signal_age_ms",
}
POLICY_FEATURES = [column for column in FEATURE_COLUMNS if column not in OBSERVER_QUALITY_ONLY_FEATURES]

LABELS = {
    "reprice_1_3_ticks": "label_reprice_1_3_ticks",
    "same_price_refill": "label_same_price_refill",
}


def _imports() -> dict[str, Any]:
    try:
        import joblib
        import numpy as np
        import pandas as pd
        from interpret.glassbox import ExplainableBoostingClassifier
        from sklearn.metrics import (
            accuracy_score,
            average_precision_score,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        from sklearn.utils.class_weight import compute_sample_weight
    except ImportError as exc:
        raise SystemExit(
            "EBM research dependencies are missing. Install them with: pip install -e \".[research]\""
        ) from exc
    return locals()


def _market_splits(df: Any, *, train_fraction: float = 0.70, validation_fraction: float = 0.15) -> tuple[list[int], list[int], list[int]]:
    market_times = (
        df.groupby("market_id", as_index=False)["last_target_ms"]
        .min()
        .sort_values(["last_target_ms", "market_id"])
    )
    markets = [int(value) for value in market_times["market_id"].tolist()]
    if len(markets) < 6:
        raise ValueError(f"need at least 6 markets for chronological train/validation/test split; got {len(markets)}")
    train_count = max(2, int(len(markets) * train_fraction))
    validation_count = max(1, int(len(markets) * validation_fraction))
    if train_count + validation_count >= len(markets):
        validation_count = 1
        train_count = len(markets) - 2
    return (
        markets[:train_count],
        markets[train_count : train_count + validation_count],
        markets[train_count + validation_count :],
    )


def _safe_auc(metrics: dict[str, Any], y_true: Any, probabilities: Any) -> float | None:
    if len(set(int(value) for value in y_true.tolist())) < 2:
        return None
    return float(metrics["roc_auc_score"](y_true, probabilities))


def _safe_average_precision(metrics: dict[str, Any], y_true: Any, probabilities: Any) -> float | None:
    if int(y_true.sum()) <= 0:
        return None
    return float(metrics["average_precision_score"](y_true, probabilities))


def _choose_threshold(metrics: dict[str, Any], np: Any, y_true: Any, probabilities: Any) -> tuple[float, float | None]:
    if len(y_true) == 0 or len(set(int(value) for value in y_true.tolist())) < 2:
        return 0.5, None
    best_threshold = 0.5
    best_f1 = -1.0
    for threshold in np.linspace(0.05, 0.95, 91):
        predicted = (probabilities >= threshold).astype(int)
        score = float(metrics["f1_score"](y_true, predicted, zero_division=0))
        if score > best_f1:
            best_f1 = score
            best_threshold = float(threshold)
    return best_threshold, best_f1


def _evaluate(metrics: dict[str, Any], y_true: Any, probabilities: Any, threshold: float) -> dict[str, Any]:
    predicted = (probabilities >= threshold).astype(int)
    return {
        "rows": int(len(y_true)),
        "positives": int(y_true.sum()),
        "positiveRate": float(y_true.mean()) if len(y_true) else None,
        "threshold": float(threshold),
        "rocAuc": _safe_auc(metrics, y_true, probabilities),
        "averagePrecision": _safe_average_precision(metrics, y_true, probabilities),
        "accuracy": float(metrics["accuracy_score"](y_true, predicted)) if len(y_true) else None,
        "precision": float(metrics["precision_score"](y_true, predicted, zero_division=0)) if len(y_true) else None,
        "recall": float(metrics["recall_score"](y_true, predicted, zero_division=0)) if len(y_true) else None,
        "f1": float(metrics["f1_score"](y_true, predicted, zero_division=0)) if len(y_true) else None,
    }


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
    return terms[:30], interactions[:15]


def _response_probes(model: Any, pd: Any, X_train: Any, top_terms: list[dict[str, Any]]) -> dict[str, Any]:
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
        if len(selected) >= 12:
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
        probabilities = model.predict_proba(frame)[:, 1].tolist()
        probes[feature] = [
            {"value": value, "predictedPositiveProbabilityAtMedianContext": float(probability)}
            for value, probability in zip(unique_values, probabilities)
        ]
    return probes


def _train_one(
    *,
    name: str,
    label_column: str,
    df: Any,
    train_markets: list[int],
    validation_markets: list[int],
    test_markets: list[int],
    deps: dict[str, Any],
    interactions: int,
    random_state: int,
    model_dir: Path,
) -> dict[str, Any]:
    np = deps["np"]
    pd = deps["pd"]
    EBM = deps["ExplainableBoostingClassifier"]
    metrics = {key: deps[key] for key in (
        "accuracy_score", "average_precision_score", "f1_score", "precision_score", "recall_score", "roc_auc_score"
    )}

    train = df[df["market_id"].isin(train_markets)].copy()
    validation = df[df["market_id"].isin(validation_markets)].copy()
    test = df[df["market_id"].isin(test_markets)].copy()
    y_train = train[label_column].astype(int)
    y_validation = validation[label_column].astype(int)
    y_test = test[label_column].astype(int)

    if len(set(y_train.tolist())) < 2 or int(y_train.sum()) < 10:
        return {
            "status": "INSUFFICIENT_CLASS_VARIATION",
            "trainRows": int(len(train)),
            "trainPositives": int(y_train.sum()),
        }

    X_train = train[POLICY_FEATURES].apply(pd.to_numeric, errors="coerce")
    X_validation = validation[POLICY_FEATURES].apply(pd.to_numeric, errors="coerce")
    X_test = test[POLICY_FEATURES].apply(pd.to_numeric, errors="coerce")
    sample_weight = deps["compute_sample_weight"]("balanced", y_train)

    model = EBM(
        interactions=max(0, int(interactions)),
        random_state=int(random_state),
        max_rounds=5000,
        early_stopping_rounds=100,
        outer_bags=8,
        n_jobs=-2,
    )
    model.fit(X_train, y_train, sample_weight=sample_weight)

    validation_probability = model.predict_proba(X_validation)[:, 1] if len(X_validation) else np.array([])
    threshold, validation_best_f1 = _choose_threshold(metrics, np, y_validation, validation_probability)
    test_probability = model.predict_proba(X_test)[:, 1] if len(X_test) else np.array([])
    top_terms, top_interactions = _term_summary(model)

    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{name}.joblib"
    deps["joblib"].dump(
        {
            "reportVersion": REPORT_VERSION,
            "modelName": name,
            "labelColumn": label_column,
            "policyFeatures": POLICY_FEATURES,
            "threshold": threshold,
            "model": model,
        },
        model_path,
    )

    return {
        "status": "TRAINED",
        "labelColumn": label_column,
        "classWeighting": "balanced sample_weight on training markets only",
        "validationBestF1": validation_best_f1,
        "train": {
            "rows": int(len(train)),
            "positives": int(y_train.sum()),
            "positiveRate": float(y_train.mean()),
        },
        "validation": _evaluate(metrics, y_validation, validation_probability, threshold),
        "test": _evaluate(metrics, y_test, test_probability, threshold),
        "topTerms": top_terms,
        "topInteractions": top_interactions,
        "featureResponseProbes": _response_probes(model, pd, X_train, top_terms),
        "modelFile": str(model_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train explainable EBM models for target Maker post-fill reprice/refill behavior using chronological market splits."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    deps = _imports()
    pd = deps["pd"]
    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise SystemExit(f"dataset not found: {dataset}. Run tools/build_target_maker_ebm_dataset.py first.")
    df = pd.read_csv(dataset)
    missing = sorted((set(POLICY_FEATURES) | set(LABELS.values()) | {"market_id", "last_target_ms"}) - set(df.columns))
    if missing:
        raise SystemExit(f"dataset is missing required columns: {missing}")

    train_markets, validation_markets, test_markets = _market_splits(df)
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "targetEventsDriveTrading": False,
        "dataset": str(dataset),
        "rows": int(len(df)),
        "markets": int(df["market_id"].nunique()),
        "decisionPoint": "AFTER_PARENT_FILL_BEFORE_NEXT_PARENT",
        "policyFeatures": POLICY_FEATURES,
        "excludedObserverQualityFeatures": sorted(OBSERVER_QUALITY_ONLY_FEATURES),
        "split": {
            "method": "chronological whole-market split; a market never appears in more than one partition",
            "trainMarkets": train_markets,
            "validationMarkets": validation_markets,
            "testMarkets": test_markets,
        },
        "models": {},
        "interpretationBoundary": "models discover associations in forward-collected target lifecycle behavior; they are not automatically promoted to paper/live strategy rules",
    }

    for name, label in LABELS.items():
        report["models"][name] = _train_one(
            name=name,
            label_column=label,
            df=df,
            train_markets=train_markets,
            validation_markets=validation_markets,
            test_markets=test_markets,
            deps=deps,
            interactions=args.interactions,
            random_state=args.random_state,
            model_dir=args.model_dir.expanduser().resolve(),
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
