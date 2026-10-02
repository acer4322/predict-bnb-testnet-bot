from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import build_target_maker_lifecycle_side_v2_3 as builder
import train_target_maker_direct_placement_v1 as maker_v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = builder.DEFAULT_OUTPUT
DEFAULT_META = builder.DEFAULT_META
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_lifecycle_side_v2_3_report.json"
REPORT_VERSION = "TARGET_MAKER_LIFECYCLE_SIDE_V2_3_ORDINARY_CLEAN_QUICK"

PUBLIC_ONLY = list(maker_v1.SIDE_FEATURE_SETS["full_public"])
LAST_MAKER = ["last_maker_side_up", "last_maker_age_ms"]
LIFECYCLE = list(builder.LIFECYCLE_FEATURES)

FEATURE_SETS = {
    "PUBLIC_ONLY": PUBLIC_ONLY,
    "PUBLIC_PLUS_LAST_MAKER": list(dict.fromkeys(PUBLIC_ONLY + LAST_MAKER)),
    "PUBLIC_PLUS_LIFECYCLE": list(dict.fromkeys(PUBLIC_ONLY + LIFECYCLE)),
}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, tuple):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(_clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _progress(started: float, message: str) -> None:
    elapsed = time.perf_counter() - started
    now = datetime.now().strftime("%H:%M:%S")
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"[{now} +{minutes:02d}:{seconds:02d}] {message}", flush=True)


def _summary(feature_payload: dict[str, Any]) -> dict[str, Any]:
    folds = [row for row in feature_payload.get("folds", []) if row.get("status") == "OK"]
    aucs = [float(row["test"]["rocAuc"]) for row in folds if row.get("test", {}).get("rocAuc") is not None]
    logloss = [float(row["test"]["logLoss"]) for row in folds if row.get("test", {}).get("logLoss") is not None]
    brier = [float(row["test"]["brier"]) for row in folds if row.get("test", {}).get("brier") is not None]
    return {
        "validFolds": len(folds),
        "meanAuc": statistics.fmean(aucs) if aucs else None,
        "minAuc": min(aucs) if aucs else None,
        "maxAuc": max(aucs) if aucs else None,
        "meanLogLoss": statistics.fmean(logloss) if logloss else None,
        "meanBrier": statistics.fmean(brier) if brier else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Quick 2-fold clean Maker lifecycle-side EBM")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--min-train-markets", type=int, default=70)
    parser.add_argument("--test-markets", type=int, default=15)
    parser.add_argument("--max-folds", type=int, default=2)
    parser.add_argument("--interactions", type=int, default=4)
    parser.add_argument("--max-rounds", type=int, default=350)
    parser.add_argument("--outer-bags", type=int, default=2)
    args = parser.parse_args()

    started = time.perf_counter()
    _progress(started, "START clean ordinary lifecycle-side quick")

    shared = maker_v1._shared()
    deps = shared._imports()
    pd = deps["pd"]

    dataset_path = args.dataset.expanduser().resolve()
    if not dataset_path.exists():
        raise SystemExit("Lifecycle-side dataset missing. Run build_target_maker_lifecycle_side_v2_3.py first.")
    frame = pd.read_csv(dataset_path)
    required = {"market_id", "placement_first_ms", "label_side_up", "clean_target"} | set(builder.LIFECYCLE_FEATURES)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("Lifecycle-side dataset missing columns: " + ", ".join(missing))

    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["placement_first_ms"] = pd.to_numeric(frame["placement_first_ms"], errors="raise").astype("int64")
    frame["label_side_up"] = pd.to_numeric(frame["label_side_up"], errors="raise").astype(int)
    frame = frame[pd.to_numeric(frame["clean_target"], errors="coerce").fillna(0).astype(int) == 1].copy()

    markets = maker_v1._market_order(frame, "placement_first_ms")
    folds = maker_v1._spanning_folds(
        markets,
        min_train=max(20, int(args.min_train_markets)),
        test_markets=max(5, int(args.test_markets)),
        max_folds=max(1, int(args.max_folds)),
    )
    _progress(started, f"DATA rows={len(frame)} markets={len(markets)} folds={len(folds)}")

    task: dict[str, Any] = {
        "label": "label_side_up",
        "rows": int(len(frame)),
        "markets": len(markets),
        "positiveRate": float(frame["label_side_up"].mean()) if len(frame) else None,
        "featureSets": {},
    }

    total_fits = len(FEATURE_SETS) * len(folds)
    fit_index = 0
    for feature_index, (name, requested) in enumerate(FEATURE_SETS.items()):
        features = maker_v1._usable(requested, frame, pd)
        _progress(started, f"FEATURE {name} start features={len(features)}")
        fold_rows: list[dict[str, Any]] = []
        for fold_index, fold in enumerate(folds):
            fit_index += 1
            fit_started = time.perf_counter()
            _progress(
                started,
                f"FIT {fit_index}/{total_fits} {name} fold {fold_index + 1}/{len(folds)} "
                f"train={len(fold['trainMarkets'])} cal={len(fold['calibrationMarkets'])} test={len(fold['testMarkets'])}",
            )
            result = shared._classification_fold(
                deps=deps,
                frame=frame,
                label="label_side_up",
                features=features,
                fold=fold,
                interactions=min(max(0, int(args.interactions)), max(0, len(features) // 2)),
                max_rounds=max(50, int(args.max_rounds)),
                outer_bags=max(1, int(args.outer_bags)),
                seed=23000 + feature_index * 20 + fold_index,
            )
            fold_rows.append(result)
            auc = result.get("test", {}).get("rocAuc") if result.get("status") == "OK" else None
            ll = result.get("test", {}).get("logLoss") if result.get("status") == "OK" else None
            _progress(
                started,
                f"DONE {name} fold {fold_index + 1} status={result.get('status')} "
                f"AUC={auc} logloss={ll} fit_elapsed={time.perf_counter() - fit_started:.1f}s",
            )
        aggregate = shared._aggregate_classification(fold_rows)
        aggregate["features"] = features
        aggregate["quickSummary"] = _summary(aggregate)
        task["featureSets"][name] = aggregate
        _progress(started, f"FEATURE {name} complete meanAUC={aggregate.get('meanTestRocAuc')}")

    baseline = task["featureSets"].get("PUBLIC_ONLY", {}).get("meanTestRocAuc")
    comparisons: list[dict[str, Any]] = []
    for name in ("PUBLIC_PLUS_LAST_MAKER", "PUBLIC_PLUS_LIFECYCLE"):
        values = task["featureSets"].get(name, {})
        auc = values.get("meanTestRocAuc")
        lift = float(auc) - float(baseline) if auc is not None and baseline is not None else None
        comparisons.append({
            "featureSet": name,
            "meanTestRocAuc": auc,
            "aucLiftVsPublic": lift,
            "meanLogLossLiftVsPrior": values.get("mean_logLossLiftVsPrior"),
            "positiveFoldRateLogLossLiftVsPrior": values.get("positiveFoldRate_logLossLiftVsPrior"),
        })

    best_lift = max((float(row["aucLiftVsPublic"]) for row in comparisons if row["aucLiftVsPublic"] is not None), default=None)
    if best_lift is None:
        decision = "INVALID_NO_COMPARABLE_FOLDS"
    elif best_lift >= 0.03:
        decision = "PROCEED_CHRONOLOGICAL_VALIDATION"
    elif best_lift >= 0.015:
        decision = "AMBIGUOUS_ADD_ONE_FOLD_ONLY"
    else:
        decision = "STOP_NO_MATERIAL_OOS_LIFT"

    meta = {}
    meta_path = args.meta.expanduser().resolve()
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {"status": "UNREADABLE_META"}

    report = {
        "version": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "specialFit": False,
        "dataset": str(dataset_path),
        "datasetMeta": meta,
        "preset": {
            "minTrainMarkets": int(args.min_train_markets),
            "testMarkets": int(args.test_markets),
            "maxFolds": int(args.max_folds),
            "interactions": int(args.interactions),
            "maxRounds": int(args.max_rounds),
            "outerBags": int(args.outer_bags),
        },
        "folds": folds,
        "sideTask": task,
        "comparisonsVsPublic": comparisons,
        "decisionGate": {
            "decision": decision,
            "bestAucLiftVsPublic": best_lift,
            "rule": ">=+0.03 proceed; +0.015..0.03 add one fold only; <+0.015 stop",
            "specialMarketNext": "stress-test only after ordinary chronological evidence is established",
        },
        "elapsedSeconds": time.perf_counter() - started,
    }
    _write_json(args.report, report)
    _progress(started, f"STOP GATE decision={decision} bestAucLiftVsPublic={best_lift}")
    _progress(started, f"REPORT {args.report.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
