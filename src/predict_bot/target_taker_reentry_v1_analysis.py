from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .predict_wallet_target_taker_public_side_strategy_v1 import SIDE_EBM_EXPECTED_FEATURES
from .target_taker_reentry_v1_dataset import (
    ACTOR_STATE_FEATURES,
    DATASET_VERSION,
    DEFAULT_OUTPUT as DEFAULT_DATASET,
    DELTA_FEATURES,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_reentry_v1_walk_forward_report.json"
REPORT_VERSION = "TARGET_TAKER_REENTRY_V1_5S_MARKET_WALK_FORWARD_EBM"

TASKS = {
    "SAME_SIDE_REENTRY_WITHIN_5S": "label_same_side_reentry_within_5000ms",
    "OPPOSITE_SIDE_ENTRY_WITHIN_5S": "label_opposite_side_entry_within_5000ms",
}

# Keep the public baseline identical to the frozen SIDE EBM contract.  That makes
# the experiment answer a narrow question: do actor-owned state and post-entry
# public deltas add signal beyond the exact 16 public variables already trusted
# by TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY?
PUBLIC_ONLY_FEATURES = list(SIDE_EBM_EXPECTED_FEATURES)
FEATURE_SETS = {
    "PUBLIC_ONLY": PUBLIC_ONLY_FEATURES,
    "PUBLIC_ACTOR": PUBLIC_ONLY_FEATURES + list(ACTOR_STATE_FEATURES),
    "PUBLIC_ACTOR_DELTA": PUBLIC_ONLY_FEATURES + list(ACTOR_STATE_FEATURES) + list(DELTA_FEATURES),
}
FULL_FEATURES = FEATURE_SETS["PUBLIC_ACTOR_DELTA"]
FORBIDDEN_FEATURE_PREFIXES = ("target_", "label_", "audit_")


def _imports() -> dict[str, Any]:
    try:
        import numpy as np
        import pandas as pd
        from interpret.glassbox import ExplainableBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError('REENTRY_V1 research dependencies missing. Run: pip install -e ".[research]"') from exc
    return locals()


def _validate_feature_contract() -> None:
    for family, features in FEATURE_SETS.items():
        if len(features) != len(set(features)):
            raise RuntimeError(f"duplicate feature in {family}")
        for feature in features:
            if feature.startswith(FORBIDDEN_FEATURE_PREFIXES):
                raise RuntimeError(f"forbidden future/Target feature in {family}: {feature}")
    if FEATURE_SETS["PUBLIC_ACTOR"][: len(PUBLIC_ONLY_FEATURES)] != PUBLIC_ONLY_FEATURES:
        raise RuntimeError("PUBLIC_ACTOR does not preserve the frozen public baseline")
    if FEATURE_SETS["PUBLIC_ACTOR_DELTA"][: len(FEATURE_SETS["PUBLIC_ACTOR"])] != FEATURE_SETS["PUBLIC_ACTOR"]:
        raise RuntimeError("PUBLIC_ACTOR_DELTA does not preserve the nested feature contract")


def _episode_id(frame: Any) -> Any:
    return (
        frame["market_id"].astype("Int64").astype(str)
        + "|"
        + frame["actor_prev_parent_source"].fillna("").astype(str)
        + "|"
        + frame["actor_prev_parent_id"].fillna("").astype(str)
    )


def _episode_weights(frame: Any) -> Any:
    counts = frame.groupby("__episode_id")["__episode_id"].transform("size").astype(float)
    weights = 1.0 / counts
    total = float(weights.sum())
    if total > 0:
        weights = weights * (len(weights) / total)
    return weights


def _balanced_training_weights(frame: Any, label: str) -> Any:
    base = _episode_weights(frame)
    y = frame[label].astype(int)
    mass = {value: float(base[y == value].sum()) for value in (0, 1)}
    if mass[0] <= 0 or mass[1] <= 0:
        raise ValueError(f"training split lacks both classes for {label}: {mass}")
    total = mass[0] + mass[1]
    result = base.copy()
    for value in (0, 1):
        result.loc[y == value] *= total / (2.0 * mass[value])
    mean = float(result.mean())
    return result / mean if mean > 0 else result


def _weighted_positive_rate(y: Any, weights: Any | None) -> float | None:
    if len(y) == 0:
        return None
    if weights is None:
        return float(y.mean())
    total = float(weights.sum())
    return float((y.astype(float) * weights).sum() / total) if total > 0 else None


def _metrics(deps: dict[str, Any], y: Any, probability: Any, weights: Any | None) -> dict[str, Any]:
    np = deps["np"]
    p = np.clip(np.asarray(probability, dtype=float), 1e-7, 1 - 1e-7)
    y_values = np.asarray(y, dtype=int)
    sample_weight = None if weights is None else np.asarray(weights, dtype=float)
    both = set(int(value) for value in y_values.tolist()) == {0, 1}
    return {
        "rows": int(len(y_values)),
        "positiveRate": _weighted_positive_rate(y, weights),
        "logLoss": float(deps["log_loss"](y_values, p, labels=[0, 1], sample_weight=sample_weight)),
        "brier": float(deps["brier_score_loss"](y_values, p, sample_weight=sample_weight)),
        "rocAuc": float(deps["roc_auc_score"](y_values, p, sample_weight=sample_weight)) if both else None,
        "averagePrecision": (
            float(deps["average_precision_score"](y_values, p, sample_weight=sample_weight))
            if int(y_values.sum()) > 0
            else None
        ),
    }


def _logit(deps: dict[str, Any], probability: Any) -> Any:
    np = deps["np"]
    p = np.clip(np.asarray(probability, dtype=float), 1e-7, 1 - 1e-7)
    return np.log(p / (1 - p)).reshape(-1, 1)


def _fit_calibrator(
    deps: dict[str, Any],
    y: Any,
    raw_probability: Any,
    weights: Any,
    *,
    seed: int,
) -> Any | None:
    if len(y) < 20 or set(int(value) for value in y.tolist()) != {0, 1}:
        return None
    model = deps["LogisticRegression"](C=100.0, max_iter=3000, random_state=int(seed))
    model.fit(_logit(deps, raw_probability), y.astype(int), sample_weight=weights)
    return model


def _apply_calibrator(deps: dict[str, Any], calibrator: Any | None, raw_probability: Any) -> Any:
    if calibrator is None:
        return raw_probability
    return calibrator.predict_proba(_logit(deps, raw_probability))[:, 1]


def _market_order(frame: Any) -> list[int]:
    rows = (
        frame.groupby("market_id", as_index=False)["decision_sampled_at_ms"]
        .min()
        .sort_values(["decision_sampled_at_ms", "market_id"])
    )
    return [int(value) for value in rows["market_id"].tolist()]


def _walk_forward_folds(
    markets: list[int],
    *,
    min_train_markets: int,
    min_calibration_markets: int,
    calibration_fraction: float,
    test_markets: int,
    max_folds: int,
) -> list[dict[str, list[int]]]:
    min_train = max(10, int(min_train_markets))
    min_cal = max(5, int(min_calibration_markets))
    test_size = max(5, int(test_markets))
    if len(markets) < min_train + min_cal + 5:
        raise ValueError(
            f"need at least {min_train + min_cal + 5} ordered markets; got {len(markets)}"
        )
    folds: list[dict[str, list[int]]] = []
    test_start = min_train + min_cal
    while test_start < len(markets) and len(folds) < max(1, int(max_folds)):
        prior = markets[:test_start]
        cal_count = max(min_cal, int(round(len(prior) * max(0.05, min(0.35, calibration_fraction)))))
        cal_count = min(cal_count, len(prior) - min_train)
        if cal_count < min_cal:
            break
        test = markets[test_start : min(len(markets), test_start + test_size)]
        if len(test) < max(5, test_size // 2):
            break
        folds.append(
            {
                "trainMarkets": prior[:-cal_count],
                "calibrationMarkets": prior[-cal_count:],
                "testMarkets": test,
            }
        )
        test_start += test_size
    if not folds:
        raise ValueError("walk-forward configuration produced no folds")
    return folds


def _common_task_frame(deps: dict[str, Any], frame: Any, label: str) -> tuple[Any, dict[str, Any]]:
    pd = deps["pd"]
    np = deps["np"]
    required_columns = {
        "market_id",
        "decision_sampled_at_ms",
        "actor_prev_parent_id",
        "actor_prev_parent_source",
        "frozen16_complete",
        label,
        *FULL_FEATURES,
    }
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise RuntimeError(f"REENTRY_V1 dataset missing columns: {missing_columns}")

    work = frame.copy()
    work["market_id"] = pd.to_numeric(work["market_id"], errors="coerce")
    work["decision_sampled_at_ms"] = pd.to_numeric(work["decision_sampled_at_ms"], errors="coerce")
    work["frozen16_complete"] = pd.to_numeric(work["frozen16_complete"], errors="coerce")
    work[label] = pd.to_numeric(work[label], errors="coerce")
    for feature in FULL_FEATURES:
        work[feature] = pd.to_numeric(work[feature], errors="coerce")

    label_known = work[label].isin([0, 1])
    frozen_complete = work["frozen16_complete"] == 1
    identity_complete = (
        work["market_id"].notna()
        & work["decision_sampled_at_ms"].notna()
        & work["actor_prev_parent_id"].notna()
        & work["actor_prev_parent_source"].notna()
    )
    eligible = work[label_known & frozen_complete & identity_complete].copy()
    before_full_complete = len(eligible)
    numeric = eligible[FULL_FEATURES].to_numpy(dtype=float)
    finite_mask = np.isfinite(numeric).all(axis=1)
    eligible = eligible.loc[finite_mask].copy()
    eligible["market_id"] = eligible["market_id"].astype(int)
    eligible["decision_sampled_at_ms"] = eligible["decision_sampled_at_ms"].astype(int)
    eligible[label] = eligible[label].astype(int)
    eligible["__episode_id"] = _episode_id(eligible)
    eligible.sort_values(["decision_sampled_at_ms", "market_id"], inplace=True)
    eligible.reset_index(drop=True, inplace=True)

    base_weights = _episode_weights(eligible) if len(eligible) else None
    diagnostics = {
        "datasetRows": int(len(frame)),
        "labelKnownRows": int(label_known.sum()),
        "labelKnownFrozen16Rows": int((label_known & frozen_complete).sum()),
        "rowsBeforeFullFeatureCompleteCase": int(before_full_complete),
        "commonCompleteRows": int(len(eligible)),
        "commonCompleteRateVsKnownFrozen16": (
            len(eligible) / int((label_known & frozen_complete).sum())
            if int((label_known & frozen_complete).sum())
            else None
        ),
        "markets": int(eligible["market_id"].nunique()) if len(eligible) else 0,
        "episodes": int(eligible["__episode_id"].nunique()) if len(eligible) else 0,
        "rowPositiveRate": float(eligible[label].mean()) if len(eligible) else None,
        "episodeWeightedPositiveRate": (
            _weighted_positive_rate(eligible[label], base_weights) if len(eligible) else None
        ),
    }
    return eligible, diagnostics


def _fit_ebm(
    deps: dict[str, Any],
    X: Any,
    y: Any,
    weights: Any,
    *,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed: int,
    n_jobs: int,
) -> Any:
    if set(int(value) for value in y.tolist()) != {0, 1}:
        raise ValueError("binary REENTRY EBM training requires both classes")
    model = deps["ExplainableBoostingClassifier"](
        interactions=max(0, int(interactions)),
        random_state=int(seed),
        max_rounds=max(100, int(max_rounds)),
        early_stopping_rounds=60,
        outer_bags=max(2, int(outer_bags)),
        n_jobs=int(n_jobs),
    )
    model.fit(X, y.astype(int), sample_weight=weights)
    return model


def _top_terms(model: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    try:
        importances = list(model.term_importances())
        names = list(model.term_names_)
    except Exception:
        return []
    ranked = sorted(zip(names, importances), key=lambda item: float(item[1]), reverse=True)
    return [
        {"term": str(name), "importance": float(value)}
        for name, value in ranked[: max(1, int(limit))]
    ]


def _split_frame(frame: Any, markets: list[int]) -> Any:
    return frame[frame["market_id"].isin(markets)].copy()


def _fold_result(
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
    n_jobs: int,
) -> dict[str, Any]:
    train = _split_frame(frame, fold["trainMarkets"])
    calibration = _split_frame(frame, fold["calibrationMarkets"])
    test = _split_frame(frame, fold["testMarkets"])
    if min(len(train), len(calibration), len(test)) <= 0:
        return {
            "status": "EMPTY_SPLIT",
            "trainRows": int(len(train)),
            "calibrationRows": int(len(calibration)),
            "testRows": int(len(test)),
        }
    if set(int(value) for value in train[label].unique()) != {0, 1}:
        return {"status": "TRAIN_LACKS_BOTH_CLASSES", "trainRows": int(len(train))}
    if set(int(value) for value in test[label].unique()) != {0, 1}:
        return {"status": "TEST_LACKS_BOTH_CLASSES", "testRows": int(len(test))}

    train_weights = _balanced_training_weights(train, label)
    calibration_weights = _episode_weights(calibration)
    test_episode_weights = _episode_weights(test)
    model = _fit_ebm(
        deps,
        train[features],
        train[label],
        train_weights,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
        n_jobs=n_jobs,
    )
    raw_calibration = model.predict_proba(calibration[features])[:, 1]
    calibrator = _fit_calibrator(
        deps,
        calibration[label],
        raw_calibration,
        calibration_weights,
        seed=seed,
    )
    raw_test = model.predict_proba(test[features])[:, 1]
    calibrated_test = _apply_calibrator(deps, calibrator, raw_test)

    train_eval_weights = _episode_weights(train)
    train_prior = _weighted_positive_rate(train[label], train_eval_weights)
    if train_prior is None:
        raise RuntimeError("training prior unavailable")
    baseline_probability = deps["np"].full(len(test), float(train_prior))

    weighted_baseline = _metrics(deps, test[label], baseline_probability, test_episode_weights)
    weighted_raw = _metrics(deps, test[label], raw_test, test_episode_weights)
    weighted_test = _metrics(deps, test[label], calibrated_test, test_episode_weights)
    row_baseline = _metrics(deps, test[label], baseline_probability, None)
    row_test = _metrics(deps, test[label], calibrated_test, None)

    return {
        "status": "OK",
        "trainMarkets": len(fold["trainMarkets"]),
        "calibrationMarkets": len(fold["calibrationMarkets"]),
        "testMarkets": fold["testMarkets"],
        "trainRows": int(len(train)),
        "calibrationRows": int(len(calibration)),
        "testRows": int(len(test)),
        "trainEpisodes": int(train["__episode_id"].nunique()),
        "calibrationEpisodes": int(calibration["__episode_id"].nunique()),
        "testEpisodes": int(test["__episode_id"].nunique()),
        "trainEpisodeWeightedPositiveRate": train_prior,
        "calibratorUsed": calibrator is not None,
        "episodeWeighted": {
            "baseline": weighted_baseline,
            "rawTest": weighted_raw,
            "test": weighted_test,
            "logLossLiftVsPrior": float(weighted_baseline["logLoss"] - weighted_test["logLoss"]),
            "brierLiftVsPrior": float(weighted_baseline["brier"] - weighted_test["brier"]),
            "averagePrecisionLiftVsBaseRate": (
                float(weighted_test["averagePrecision"] - weighted_test["positiveRate"])
                if weighted_test.get("averagePrecision") is not None
                and weighted_test.get("positiveRate") is not None
                else None
            ),
            "rocAucLiftVsRandom": (
                float(weighted_test["rocAuc"] - 0.5)
                if weighted_test.get("rocAuc") is not None
                else None
            ),
        },
        "rowUnweightedDiagnostic": {
            "baseline": row_baseline,
            "test": row_test,
            "warning": "Rows are 250ms-correlated risk-set observations; use episodeWeighted metrics for model selection.",
        },
        "topTerms": _top_terms(model),
    }


def _mean(values: list[float | None]) -> float | None:
    kept = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(statistics.mean(kept)) if kept else None


def _aggregate_feature_set(folds: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [fold for fold in folds if fold.get("status") == "OK"]
    if not ok:
        return {"okFolds": 0, "status": "NO_OK_FOLDS"}
    logloss = [fold["episodeWeighted"]["test"]["logLoss"] for fold in ok]
    logloss_lift = [fold["episodeWeighted"]["logLossLiftVsPrior"] for fold in ok]
    brier = [fold["episodeWeighted"]["test"]["brier"] for fold in ok]
    brier_lift = [fold["episodeWeighted"]["brierLiftVsPrior"] for fold in ok]
    auc = [fold["episodeWeighted"]["test"].get("rocAuc") for fold in ok]
    ap = [fold["episodeWeighted"]["test"].get("averagePrecision") for fold in ok]
    ap_lift = [fold["episodeWeighted"].get("averagePrecisionLiftVsBaseRate") for fold in ok]
    positive_rate = [fold["episodeWeighted"]["test"].get("positiveRate") for fold in ok]

    term_values: dict[str, list[float]] = defaultdict(list)
    for fold in ok:
        for item in fold.get("topTerms") or []:
            term_values[str(item["term"])].append(float(item["importance"]))
    terms = sorted(
        (
            {"term": term, "meanImportanceWhenTop": float(statistics.mean(values)), "foldsPresent": len(values)}
            for term, values in term_values.items()
        ),
        key=lambda item: (item["foldsPresent"], item["meanImportanceWhenTop"]),
        reverse=True,
    )[:15]

    return {
        "status": "OK",
        "okFolds": len(ok),
        "meanEpisodeWeightedLogLoss": _mean(logloss),
        "meanEpisodeWeightedLogLossLiftVsPrior": _mean(logloss_lift),
        "positiveLogLossLiftFolds": sum(float(value) > 0 for value in logloss_lift),
        "meanEpisodeWeightedBrier": _mean(brier),
        "meanEpisodeWeightedBrierLiftVsPrior": _mean(brier_lift),
        "meanEpisodeWeightedRocAuc": _mean(auc),
        "meanEpisodeWeightedAveragePrecision": _mean(ap),
        "meanEpisodeWeightedAveragePrecisionLiftVsBaseRate": _mean(ap_lift),
        "meanEpisodeWeightedPositiveRate": _mean(positive_rate),
        "topTermsAcrossFolds": terms,
    }


def _task_comparison(feature_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    aggregates = {
        family: result.get("aggregate") or {}
        for family, result in feature_results.items()
    }
    eligible = [
        family
        for family, agg in aggregates.items()
        if agg.get("status") == "OK" and agg.get("meanEpisodeWeightedLogLoss") is not None
    ]
    ranking = sorted(
        eligible,
        key=lambda family: float(aggregates[family]["meanEpisodeWeightedLogLoss"]),
    )
    public = aggregates.get("PUBLIC_ONLY") or {}
    public_logloss = public.get("meanEpisodeWeightedLogLoss")
    public_ap = public.get("meanEpisodeWeightedAveragePrecision")
    public_auc = public.get("meanEpisodeWeightedRocAuc")
    incremental: dict[str, Any] = {}
    for family in eligible:
        agg = aggregates[family]
        incremental[family] = {
            "logLossImprovementVsPublicOnly": (
                float(public_logloss - agg["meanEpisodeWeightedLogLoss"])
                if public_logloss is not None and agg.get("meanEpisodeWeightedLogLoss") is not None
                else None
            ),
            "averagePrecisionImprovementVsPublicOnly": (
                float(agg["meanEpisodeWeightedAveragePrecision"] - public_ap)
                if public_ap is not None and agg.get("meanEpisodeWeightedAveragePrecision") is not None
                else None
            ),
            "rocAucImprovementVsPublicOnly": (
                float(agg["meanEpisodeWeightedRocAuc"] - public_auc)
                if public_auc is not None and agg.get("meanEpisodeWeightedRocAuc") is not None
                else None
            ),
        }

    recommendation: str | None = None
    for family in ranking:
        agg = aggregates[family]
        if (
            (agg.get("meanEpisodeWeightedLogLossLiftVsPrior") or 0.0) > 0
            and (agg.get("meanEpisodeWeightedAveragePrecisionLiftVsBaseRate") or 0.0) > 0
            and (agg.get("meanEpisodeWeightedRocAuc") or 0.0) > 0.5
            and int(agg.get("positiveLogLossLiftFolds") or 0) >= max(1, math.ceil(int(agg.get("okFolds") or 0) / 2))
        ):
            recommendation = family
            break

    return {
        "rankingByLowestMeanEpisodeWeightedLogLoss": ranking,
        "bestByWeightedLogLoss": ranking[0] if ranking else None,
        "incrementalVsPublicOnly": incremental,
        "researchRecommendation": recommendation,
        "recommendationRule": (
            "Among feature sets ranked by lowest mean episode-weighted calibrated log loss, choose the first that "
            "also has positive mean log-loss lift vs prior, positive AP lift vs base rate, ROC AUC > 0.5, and "
            "positive log-loss lift in at least half of OK market walk-forward folds. This is research-only and "
            "does not promote a runtime strategy automatically."
        ),
    }


def build_reentry_v1_walk_forward_report(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    report_path: Path = DEFAULT_REPORT,
    min_train_markets: int = 80,
    min_calibration_markets: int = 12,
    calibration_fraction: float = 0.15,
    test_markets: int = 40,
    max_folds: int = 3,
    interactions: int = 0,
    max_rounds: int = 500,
    outer_bags: int = 4,
    seed: int = 42,
    n_jobs: int = -2,
) -> dict[str, Any]:
    _validate_feature_contract()
    deps = _imports()
    pd = deps["pd"]
    resolved = dataset_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    frame = pd.read_csv(resolved, low_memory=False)
    if "dataset_version" not in frame.columns:
        raise RuntimeError("REENTRY_V1 dataset has no dataset_version column")
    versions = {str(value) for value in frame["dataset_version"].dropna().unique().tolist()}
    if versions != {DATASET_VERSION}:
        raise RuntimeError(f"REENTRY_V1 dataset version mismatch: {sorted(versions)} != {[DATASET_VERSION]}")

    task_reports: dict[str, Any] = {}
    for task_index, (task_name, label) in enumerate(TASKS.items()):
        task_frame, cohort = _common_task_frame(deps, frame, label)
        if len(task_frame) == 0:
            task_reports[task_name] = {
                "label": label,
                "status": "NO_COMMON_COMPLETE_ROWS",
                "cohort": cohort,
            }
            continue
        markets = _market_order(task_frame)
        folds = _walk_forward_folds(
            markets,
            min_train_markets=min_train_markets,
            min_calibration_markets=min_calibration_markets,
            calibration_fraction=calibration_fraction,
            test_markets=test_markets,
            max_folds=max_folds,
        )
        family_results: dict[str, Any] = {}
        for family_index, (family, features) in enumerate(FEATURE_SETS.items()):
            fold_results: list[dict[str, Any]] = []
            for fold_index, fold in enumerate(folds):
                fold_results.append(
                    _fold_result(
                        deps=deps,
                        frame=task_frame,
                        label=label,
                        features=features,
                        fold=fold,
                        interactions=interactions,
                        max_rounds=max_rounds,
                        outer_bags=outer_bags,
                        seed=int(seed) + task_index * 1000 + family_index * 100 + fold_index,
                        n_jobs=n_jobs,
                    )
                )
            family_results[family] = {
                "features": features,
                "featureCount": len(features),
                "folds": fold_results,
                "aggregate": _aggregate_feature_set(fold_results),
            }
        task_reports[task_name] = {
            "status": "OK",
            "label": label,
            "labelSemantics": (
                "Whether the next retained Target Taker BID parent after this risk-set snapshot is the requested "
                "same/opposite relation and is fully known to occur within 5000ms. Whole-second boundary-straddling "
                "cases remain excluded by the dataset label."
            ),
            "cohort": cohort,
            "orderedMarkets": len(markets),
            "foldPlan": folds,
            "featureSets": family_results,
            "comparison": _task_comparison(family_results),
        }

    report = {
        "reportVersion": REPORT_VERSION,
        "datasetVersion": DATASET_VERSION,
        "researchOnly": True,
        "automaticStrategyPromotion": False,
        "runtimeWiringChanged": False,
        "finalModelFrozen": False,
        "dataset": str(resolved),
        "datasetRows": int(len(frame)),
        "tasks": task_reports,
        "featureFamilies": FEATURE_SETS,
        "commonCohortPolicy": (
            "For each task, labels must be known, frozen16_complete must equal 1, and every feature in the largest "
            "PUBLIC_ACTOR_DELTA family must be finite. All three families are then trained/evaluated on exactly the "
            "same rows and market folds so incremental feature comparisons are apples-to-apples."
        ),
        "episodeWeightPolicy": (
            "Rows sharing market_id + actor_prev_parent_source + actor_prev_parent_id form one prior-entry episode. "
            "Evaluation weights normalize each episode to equal total mass. Training starts from the same episode "
            "normalization and then balances class mass, preventing long 250ms risk-set episodes from dominating."
        ),
        "splitPolicy": (
            "Expanding walk-forward split by whole market. The newest prior-market block is used only for probability "
            "calibration; the following market block is test. No market appears in more than one split within a fold."
        ),
        "calibrationPolicy": (
            "EBM training uses class-balanced episode weights. A Platt/logit logistic calibrator is fit only on the "
            "held-out calibration markets using natural episode weights. Model selection uses calibrated, episode-"
            "weighted test metrics; row-unweighted metrics are diagnostic only."
        ),
        "runtimeLeakageBoundary": (
            "No target_*, audit_* or label_* column is a model feature. actor_* training state is reconstructed from "
            "historical Target actions solely as the analogue of the strategy's own prior runtime entries. A future "
            "runtime implementation must populate actor_* and delta_* only from its own actions and public data."
        ),
        "modelParameters": {
            "interactions": int(interactions),
            "maxRounds": int(max_rounds),
            "outerBags": int(outer_bags),
            "nJobs": int(n_jobs),
            "seed": int(seed),
        },
        "walkForwardParameters": {
            "minTrainMarkets": int(min_train_markets),
            "minCalibrationMarkets": int(min_calibration_markets),
            "calibrationFraction": float(calibration_fraction),
            "testMarkets": int(test_markets),
            "maxFolds": int(max_folds),
        },
    }

    report_path = report_path.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(report_path)
    report["reportPath"] = str(report_path)
    return report
