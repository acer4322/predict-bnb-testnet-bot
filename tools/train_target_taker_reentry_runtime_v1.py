from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from predict_bot import target_taker_reentry_v1_analysis as analysis
from predict_bot.target_taker_reentry_v1_dataset import DATASET_VERSION, DEFAULT_OUTPUT as DEFAULT_DATASET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "data" / "research" / "target_taker_reentry_v1_models" / "same_side_public_actor.joblib"
TASK = "SAME_SIDE_REENTRY_WITHIN_5S"
LABEL = analysis.TASKS[TASK]
FEATURE_SET = "PUBLIC_ACTOR"
FEATURES = list(analysis.FEATURE_SETS[FEATURE_SET])


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return value
    return numeric if numeric == numeric and abs(numeric) != float("inf") else None


def train(
    *,
    dataset: Path,
    model_path: Path,
    calibration_fraction: float = 0.15,
    min_train_markets: int = 80,
    min_calibration_markets: int = 12,
    interactions: int = 10,
    max_rounds: int = 500,
    outer_bags: int = 4,
    seed: int = 4210,
    n_jobs: int = -2,
) -> dict[str, Any]:
    analysis._validate_feature_contract()
    deps = analysis._imports()
    pd = deps["pd"]
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('REENTRY_V1 research dependencies missing. Run: pip install -e ".[research]"') from exc

    resolved = dataset.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    frame = pd.read_csv(resolved, low_memory=False)
    if "dataset_version" not in frame.columns:
        raise RuntimeError("REENTRY_V1 dataset has no dataset_version column")
    versions = {str(value) for value in frame["dataset_version"].dropna().unique().tolist()}
    if versions != {DATASET_VERSION}:
        raise RuntimeError(f"REENTRY_V1 dataset version mismatch: {sorted(versions)} != {[DATASET_VERSION]}")

    task_frame, cohort = analysis._common_task_frame(deps, frame, LABEL)
    if len(task_frame) == 0:
        raise RuntimeError("SAME_SIDE PUBLIC_ACTOR final training has no complete rows")
    markets = analysis._market_order(task_frame)
    min_train = max(10, int(min_train_markets))
    min_cal = max(5, int(min_calibration_markets))
    if len(markets) < min_train + min_cal:
        raise RuntimeError(f"Need at least {min_train + min_cal} markets; got {len(markets)}")
    cal_count = max(min_cal, int(round(len(markets) * max(0.05, min(0.35, calibration_fraction)))))
    cal_count = min(cal_count, len(markets) - min_train)
    train_markets = markets[:-cal_count]
    calibration_markets = markets[-cal_count:]
    train_frame = analysis._split_frame(task_frame, train_markets)
    calibration_frame = analysis._split_frame(task_frame, calibration_markets)

    train_weights = analysis._balanced_training_weights(train_frame, LABEL)
    model = analysis._fit_ebm(
        deps,
        train_frame[FEATURES],
        train_frame[LABEL],
        train_weights,
        interactions=max(0, int(interactions)),
        max_rounds=max(100, int(max_rounds)),
        outer_bags=max(2, int(outer_bags)),
        seed=int(seed),
        n_jobs=int(n_jobs),
    )
    calibration_weights = analysis._episode_weights(calibration_frame)
    raw_calibration = model.predict_proba(calibration_frame[FEATURES])[:, 1]
    calibrator = analysis._fit_calibrator(
        deps,
        calibration_frame[LABEL],
        raw_calibration,
        calibration_weights,
        seed=int(seed) + 1,
    )
    calibrated = analysis._apply_calibrator(deps, calibrator, raw_calibration)
    calibration_metrics = analysis._metrics(
        deps,
        calibration_frame[LABEL],
        calibrated,
        calibration_weights,
    )

    output = model_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "reportVersion": analysis.REPORT_VERSION,
        "datasetVersion": DATASET_VERSION,
        "task": TASK,
        "target": LABEL,
        "featureSet": FEATURE_SET,
        "features": FEATURES,
        "model": model,
        "calibrator": calibrator,
        "researchOnly": True,
        "automaticStrategyPromotion": False,
        "runtimeWiringAuthorizedForPaperOnly": True,
        "sameSideOnly": True,
        "oppositeSideEnabled": False,
        "training": {
            "dataset": str(resolved),
            "datasetRows": int(len(frame)),
            "commonCompleteRows": int(len(task_frame)),
            "trainMarkets": len(train_markets),
            "calibrationMarkets": len(calibration_markets),
            "trainRows": int(len(train_frame)),
            "calibrationRows": int(len(calibration_frame)),
            "trainEpisodes": int(train_frame["__episode_id"].nunique()),
            "calibrationEpisodes": int(calibration_frame["__episode_id"].nunique()),
            "calibratorUsed": calibrator is not None,
            "calibrationMetricsEpisodeWeighted": calibration_metrics,
            "cohort": cohort,
        },
        "parameters": {
            "calibrationFraction": float(calibration_fraction),
            "minTrainMarkets": int(min_train_markets),
            "minCalibrationMarkets": int(min_calibration_markets),
            "interactions": int(interactions),
            "maxRounds": int(max_rounds),
            "outerBags": int(outer_bags),
            "seed": int(seed),
            "nJobs": int(n_jobs),
        },
        "runtimeLeakageBoundary": (
            "actor_* runtime state must come only from this strategy's own prior paper entries; "
            "all other inputs must be contemporaneous public market data. Target future/live actions are forbidden."
        ),
    }
    joblib.dump(bundle, output)
    summary = {
        "ok": True,
        "modelPath": str(output),
        "reportVersion": analysis.REPORT_VERSION,
        "datasetVersion": DATASET_VERSION,
        "task": TASK,
        "featureSet": FEATURE_SET,
        "featureCount": len(FEATURES),
        "interactions": int(interactions),
        "trainMarkets": len(train_markets),
        "calibrationMarkets": len(calibration_markets),
        "trainRows": int(len(train_frame)),
        "calibrationRows": int(len(calibration_frame)),
        "calibratorUsed": calibrator is not None,
        "calibrationMetricsEpisodeWeighted": calibration_metrics,
        "topTerms": analysis._top_terms(model, limit=15),
        "paperOnly": True,
    }
    return _clean(summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit the paper-only SAME_SIDE PUBLIC_ACTOR re-entry EBM artifact.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--calibration-fraction", type=float, default=0.15)
    parser.add_argument("--min-train-markets", type=int, default=80)
    parser.add_argument("--min-calibration-markets", type=int, default=12)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=500)
    parser.add_argument("--outer-bags", type=int, default=4)
    parser.add_argument("--seed", type=int, default=4210)
    parser.add_argument("--n-jobs", type=int, default=-2)
    args = parser.parse_args()
    summary = train(
        dataset=args.dataset,
        model_path=args.model,
        calibration_fraction=args.calibration_fraction,
        min_train_markets=args.min_train_markets,
        min_calibration_markets=args.min_calibration_markets,
        interactions=args.interactions,
        max_rounds=args.max_rounds,
        outer_bags=args.outer_bags,
        seed=args.seed,
        n_jobs=args.n_jobs,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
