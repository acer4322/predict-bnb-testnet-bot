from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from predict_bot.target_taker_direct_eligibility_special_regime_v1 import (
    DEFAULT_OUTPUT,
    FROZEN16_FEATURES,
    SPECIAL_REGIME_FEATURES,
    SIDE_MODEL_FEATURES,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_direct_eligibility_special_regime_v1_report.json"
REPORT_VERSION = "TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1_EBM_STRESS_HOLDOUT"

FEATURE_SETS = {
    "frozen16": FROZEN16_FEATURES,
    "special_regime_only": SPECIAL_REGIME_FEATURES,
    "frozen16_plus_special": list(dict.fromkeys(FROZEN16_FEATURES + SPECIAL_REGIME_FEATURES)),
    "full_public_plus_special": list(dict.fromkeys(list(SIDE_MODEL_FEATURES) + SPECIAL_REGIME_FEATURES)),
}


def _shared() -> Any:
    path = ROOT / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("shared_target_taker_special_regime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _epoch_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        raise ValueError(f"time must include timezone offset or be epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _usable(features: list[str], frame: Any, pd: Any) -> list[str]:
    return [
        feature
        for feature in features
        if feature in frame.columns
        and pd.to_numeric(frame[feature], errors="coerce").notna().any()
    ]


def _market_order(frame: Any, time_column: str) -> list[int]:
    rows = (
        frame.groupby("market_id", as_index=False)[time_column]
        .min()
        .sort_values([time_column, "market_id"])
    )
    return [int(value) for value in rows["market_id"].tolist()]


def _split_train_cal(markets: list[int]) -> tuple[list[int], list[int]]:
    if len(markets) < 12:
        return markets, []
    cal_count = max(6, int(round(len(markets) * 0.15)))
    cal_count = min(cal_count, max(1, len(markets) - 8))
    return markets[:-cal_count], markets[-cal_count:]


def _adapt_split(
    historical_markets: list[int], special_early: list[int]
) -> tuple[list[int], list[int]]:
    base_train, base_cal = _split_train_cal(historical_markets)
    if len(special_early) >= 3:
        special_cal_count = max(1, min(3, int(round(len(special_early) * 0.20))))
        return (
            historical_markets + special_early[:-special_cal_count],
            special_early[-special_cal_count:],
        )
    return base_train + special_early, base_cal


def _fit_eval(
    *,
    shared: Any,
    deps: dict[str, Any],
    frame: Any,
    label: str,
    features: list[str],
    train_markets: list[int],
    calibration_markets: list[int],
    test_markets: list[int],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    train = frame[frame["market_id"].isin(train_markets)].copy()
    calibration = frame[frame["market_id"].isin(calibration_markets)].copy()
    test = frame[frame["market_id"].isin(test_markets)].copy()
    if len(train) < 80 or len(test) < 20:
        return {
            "status": "INSUFFICIENT_DATA",
            "trainRows": int(len(train)),
            "testRows": int(len(test)),
        }
    y_train = pd.to_numeric(train[label], errors="raise").astype(int)
    y_cal = pd.to_numeric(calibration[label], errors="raise").astype(int)
    y_test = pd.to_numeric(test[label], errors="raise").astype(int)
    if set(int(v) for v in y_train.unique()) != {0, 1}:
        return {
            "status": "INSUFFICIENT_CLASSES",
            "trainRows": int(len(train)),
            "testRows": int(len(test)),
        }

    model = shared._fit_classifier(
        deps,
        shared._numeric(pd, train, features),
        y_train,
        interactions=min(max(0, interactions), max(0, len(features) // 2)),
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    raw_cal = (
        model.predict_proba(shared._numeric(pd, calibration, features))[:, 1]
        if len(calibration)
        else deps["np"].array([])
    )
    calibrator = shared._calibrate(deps, y_cal, raw_cal)
    raw_test = model.predict_proba(shared._numeric(pd, test, features))[:, 1]
    probability = shared._apply_calibration(deps, calibrator, raw_test)
    metrics = shared._classification_metrics(deps, y_test, probability)
    prior = min(1 - 1e-7, max(1e-7, float(y_train.mean())))
    baseline = shared._classification_metrics(
        deps, y_test, deps["np"].full(len(y_test), prior)
    )
    return {
        "status": "OK",
        "trainRows": int(len(train)),
        "calibrationRows": int(len(calibration)),
        "testRows": int(len(test)),
        "trainMarkets": len(train_markets),
        "testMarkets": test_markets,
        "test": metrics,
        "baseline": baseline,
        "logLossLiftVsPrior": float(baseline["logLoss"] - metrics["logLoss"]),
        "termImportances": shared._term_summary(model),
    }


def _normal_reference_split(markets: list[int]) -> tuple[list[int], list[int], list[int]]:
    if len(markets) < 20:
        train, cal = _split_train_cal(markets)
        return train, cal, []
    test_count = max(6, int(round(len(markets) * 0.20)))
    test_count = min(test_count, max(6, len(markets) - 12))
    prior = markets[:-test_count]
    test = markets[-test_count:]
    train, cal = _split_train_cal(prior)
    return train, cal, test


def _distribution(frame: Any, feature: str, pd: Any) -> dict[str, Any]:
    if feature not in frame.columns:
        return {"rows": int(len(frame)), "missingRate": 1.0}
    series = pd.to_numeric(frame[feature], errors="coerce")
    clean = series.dropna()
    if len(clean) == 0:
        return {"rows": int(len(frame)), "missingRate": 1.0}
    return {
        "rows": int(len(frame)),
        "nonMissing": int(len(clean)),
        "missingRate": float(series.isna().mean()),
        "mean": float(clean.mean()),
        "std": float(clean.std(ddof=0)),
        "median": float(clean.median()),
        "p10": float(clean.quantile(0.10)),
        "p90": float(clean.quantile(0.90)),
    }


def _drift(history: Any, special: Any, features: list[str], pd: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature in features:
        a = _distribution(history, feature, pd)
        b = _distribution(special, feature, pd)
        mean_a, mean_b = a.get("mean"), b.get("mean")
        std_a, std_b = a.get("std"), b.get("std")
        pooled = None
        if std_a is not None and std_b is not None:
            pooled = math.sqrt((float(std_a) ** 2 + float(std_b) ** 2) / 2.0)
        smd = (
            (float(mean_b) - float(mean_a)) / pooled
            if pooled is not None and pooled > 1e-12 and mean_a is not None and mean_b is not None
            else None
        )
        rows.append(
            {
                "feature": feature,
                "historical": a,
                "special": b,
                "standardizedMeanDifference": smd,
                "absStandardizedMeanDifference": abs(smd) if smd is not None else None,
            }
        )
    rows.sort(
        key=lambda row: (
            row["absStandardizedMeanDifference"]
            if row["absStandardizedMeanDifference"] is not None
            else -1.0
        ),
        reverse=True,
    )
    return rows


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stress-test direct Target Taker eligibility EBM on a user-defined special regime. "
            "Experiment A: pre-special history -> special holdout. "
            "Experiment B: history + early special -> later special."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--special-start", required=True)
    parser.add_argument("--special-end", default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--special-train-fraction", type=float, default=0.50)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=3000)
    parser.add_argument("--outer-bags", type=int, default=8)
    args = parser.parse_args()

    start_ms = _epoch_ms(args.special_start)
    end_ms = _epoch_ms(args.special_end)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--special-end must be after --special-start")

    shared = _shared()
    deps = shared._imports()
    pd = deps["pd"]
    path = args.dataset.expanduser().resolve()
    if not path.exists():
        raise SystemExit(
            f"dataset missing: {path}\n"
            "Run: python tools/build_target_taker_direct_eligibility_special_regime_v1_dataset.py"
        )
    frame = pd.read_csv(path)
    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["decision_sampled_at_ms"] = pd.to_numeric(
        frame["decision_sampled_at_ms"], errors="raise"
    ).astype("int64")

    special_mask = frame["decision_sampled_at_ms"] >= start_ms
    if end_ms is not None:
        special_mask &= frame["decision_sampled_at_ms"] < end_ms
    special_market_ids = sorted(
        int(v) for v in frame.loc[special_mask, "market_id"].unique().tolist()
    )
    if not special_market_ids:
        raise SystemExit("No special-regime rows found in the requested window.")

    historical = frame[
        (frame["decision_sampled_at_ms"] < start_ms)
        & (~frame["market_id"].isin(special_market_ids))
    ].copy()
    special = frame[frame["market_id"].isin(special_market_ids) & special_mask].copy()
    analysis_frame = pd.concat([historical, special], ignore_index=True)
    historical_markets = _market_order(historical, "decision_sampled_at_ms")
    special_markets = _market_order(special, "decision_sampled_at_ms")
    if len(historical_markets) < 12:
        raise SystemExit(f"Need more pre-special markets; got {len(historical_markets)}")
    if len(special_markets) < 3:
        raise SystemExit(f"Need at least 3 special markets; got {len(special_markets)}")

    all_drift_features = list(dict.fromkeys(FROZEN16_FEATURES + SPECIAL_REGIME_FEATURES))
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "dataset": str(path),
        "specialWindow": {"startMs": start_ms, "endMs": end_ms},
        "boundary": (
            "Special markets are market-disjoint from pre-special training. "
            "Target events in the dataset are strict future-second buckets; same-second events are forbidden."
        ),
        "rows": {
            "historical": int(len(historical)),
            "special": int(len(special)),
            "historicalMarkets": len(historical_markets),
            "specialMarkets": len(special_markets),
        },
        "featureDrift": _drift(historical, special, all_drift_features, pd)[:40],
        "tasks": {},
    }

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    fraction = min(0.8, max(0.2, float(args.special_train_fraction)))

    normal_train, normal_cal, normal_test = _normal_reference_split(historical_markets)
    hist_train, hist_cal = _split_train_cal(historical_markets)
    cut = max(1, min(len(special_markets) - 1, int(round(len(special_markets) * fraction))))
    special_early = special_markets[:cut]
    special_late = special_markets[cut:]

    for horizon in (1, 2, 5):
        label = f"label_next_target_taker_any_{horizon}s"
        task = {
            "label": label,
            "historicalPositiveRate": float(pd.to_numeric(historical[label], errors="raise").mean()),
            "specialPositiveRate": float(pd.to_numeric(special[label], errors="raise").mean()),
            "featureSets": {},
        }
        for feature_index, (name, requested) in enumerate(FEATURE_SETS.items()):
            features = _usable(requested, frame, pd)
            if not features:
                continue
            normal_reference = (
                _fit_eval(
                    shared=shared,
                    deps=deps,
                    frame=historical,
                    label=label,
                    features=features,
                    train_markets=normal_train,
                    calibration_markets=normal_cal,
                    test_markets=normal_test,
                    interactions=interactions,
                    max_rounds=max_rounds,
                    outer_bags=outer_bags,
                    seed=1000 + horizon * 100 + feature_index,
                )
                if normal_test
                else {"status": "INSUFFICIENT_MARKETS"}
            )
            special_holdout = _fit_eval(
                shared=shared,
                deps=deps,
                frame=analysis_frame,
                label=label,
                features=features,
                train_markets=hist_train,
                calibration_markets=hist_cal,
                test_markets=special_markets,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=3000 + horizon * 100 + feature_index,
            )
            retrain_train, retrain_cal = _adapt_split(
                historical_markets, special_early
            )
            special_retrain = (
                _fit_eval(
                    shared=shared,
                    deps=deps,
                    frame=analysis_frame,
                    label=label,
                    features=features,
                    train_markets=retrain_train,
                    calibration_markets=retrain_cal,
                    test_markets=special_late,
                    interactions=interactions,
                    max_rounds=max_rounds,
                    outer_bags=outer_bags,
                    seed=5000 + horizon * 100 + feature_index,
                )
                if special_late
                else {"status": "INSUFFICIENT_SPECIAL_LATE"}
            )
            task["featureSets"][name] = {
                "features": features,
                "normalReference": normal_reference,
                "experimentA_preSpecialToSpecial": special_holdout,
                "experimentB_earlySpecialToLateSpecial": special_retrain,
            }

        ranking = []
        for name, values in task["featureSets"].items():
            special_eval = values["experimentA_preSpecialToSpecial"]
            ranking.append(
                {
                    "featureSet": name,
                    "specialAuc": special_eval.get("test", {}).get("rocAuc")
                    if special_eval.get("status") == "OK"
                    else None,
                    "specialLogLossLift": special_eval.get("logLossLiftVsPrior")
                    if special_eval.get("status") == "OK"
                    else None,
                    "normalAuc": values["normalReference"].get("test", {}).get("rocAuc")
                    if values["normalReference"].get("status") == "OK"
                    else None,
                    "normalLogLossLift": values["normalReference"].get("logLossLiftVsPrior")
                    if values["normalReference"].get("status") == "OK"
                    else None,
                }
            )
        ranking.sort(
            key=lambda row: (
                row["specialLogLossLift"]
                if row["specialLogLossLift"] is not None
                else -999.0
            ),
            reverse=True,
        )
        task["rankingBySpecialLogLossLift"] = ranking
        report["tasks"][f"eligibility_{horizon}s"] = task

    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_suffix(report_path.suffix + ".tmp")
    temp.write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(report_path)
    print(json.dumps(_clean(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
