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
import train_target_maker_lifecycle_side_v2_3 as quick

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = builder.DEFAULT_OUTPUT
DEFAULT_META = builder.DEFAULT_META
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_lifecycle_side_v2_4_validation_report.json"
REPORT_VERSION = "TARGET_MAKER_LIFECYCLE_SIDE_V2_4_ORDINARY_CLEAN_CHRONOLOGICAL_VALIDATION"

FEATURE_SETS = {
    "PUBLIC_ONLY": quick.PUBLIC_ONLY,
    "PUBLIC_PLUS_LAST_MAKER": list(dict.fromkeys(quick.PUBLIC_ONLY + quick.LAST_MAKER)),
    "PUBLIC_PLUS_LIFECYCLE": list(dict.fromkeys(quick.PUBLIC_ONLY + quick.LIFECYCLE)),
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


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description="4-fold chronological validation for clean Target Maker lifecycle-side EBM")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--min-train-markets", type=int, default=70)
    parser.add_argument("--test-markets", type=int, default=15)
    parser.add_argument("--max-folds", type=int, default=4)
    parser.add_argument("--interactions", type=int, default=4)
    parser.add_argument("--max-rounds", type=int, default=350)
    parser.add_argument("--outer-bags", type=int, default=2)
    args = parser.parse_args()

    started = time.perf_counter()
    _progress(started, "START ordinary clean chronological validation")

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
        max_folds=max(2, int(args.max_folds)),
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
        fold_rows: list[dict[str, Any]] = []
        _progress(started, f"FEATURE {name} start features={len(features)}")
        for fold_index, fold in enumerate(folds):
            fit_index += 1
            fit_started = time.perf_counter()
            _progress(started, f"FIT {fit_index}/{total_fits} {name} fold {fold_index + 1}/{len(folds)}")
            result = shared._classification_fold(
                deps=deps,
                frame=frame,
                label="label_side_up",
                features=features,
                fold=fold,
                interactions=min(max(0, int(args.interactions)), max(0, len(features) // 2)),
                max_rounds=max(50, int(args.max_rounds)),
                outer_bags=max(1, int(args.outer_bags)),
                seed=24000 + feature_index * 20 + fold_index,
            )
            fold_rows.append(result)
            auc = result.get("test", {}).get("rocAuc") if result.get("status") == "OK" else None
            lift = result.get("logLossLiftVsPrior")
            _progress(started, f"DONE {name} fold {fold_index + 1} status={result.get('status')} AUC={auc} ll_lift={lift} fit_elapsed={time.perf_counter() - fit_started:.1f}s")
        aggregate = shared._aggregate_classification(fold_rows)
        aggregate["features"] = features
        task["featureSets"][name] = aggregate
        _progress(started, f"FEATURE {name} complete meanAUC={aggregate.get('meanTestRocAuc')}")

    public_folds = task["featureSets"].get("PUBLIC_ONLY", {}).get("folds", [])
    comparisons: list[dict[str, Any]] = []
    for name in ("PUBLIC_PLUS_LAST_MAKER", "PUBLIC_PLUS_LIFECYCLE"):
        values = task["featureSets"].get(name, {})
        candidate_folds = values.get("folds", [])
        foldwise: list[dict[str, Any]] = []
        auc_lifts: list[float] = []
        ll_better = 0
        for index in range(min(len(public_folds), len(candidate_folds))):
            public_row = public_folds[index]
            candidate_row = candidate_folds[index]
            public_auc = public_row.get("test", {}).get("rocAuc") if public_row.get("status") == "OK" else None
            candidate_auc = candidate_row.get("test", {}).get("rocAuc") if candidate_row.get("status") == "OK" else None
            auc_lift = float(candidate_auc) - float(public_auc) if public_auc is not None and candidate_auc is not None else None
            if auc_lift is not None:
                auc_lifts.append(auc_lift)
            candidate_ll_lift = candidate_row.get("logLossLiftVsPrior")
            if candidate_ll_lift is not None and float(candidate_ll_lift) > 0:
                ll_better += 1
            foldwise.append({
                "fold": index + 1,
                "publicAuc": public_auc,
                "candidateAuc": candidate_auc,
                "aucLiftVsPublic": auc_lift,
                "candidateLogLossLiftVsPrior": candidate_ll_lift,
                "testMarkets": candidate_row.get("testMarkets", []),
            })
        mean_auc = values.get("meanTestRocAuc")
        public_mean_auc = task["featureSets"].get("PUBLIC_ONLY", {}).get("meanTestRocAuc")
        comparisons.append({
            "featureSet": name,
            "meanTestRocAuc": mean_auc,
            "aucLiftVsPublic": float(mean_auc) - float(public_mean_auc) if mean_auc is not None and public_mean_auc is not None else None,
            "foldwise": foldwise,
            "meanFoldAucLiftVsPublic": _mean(auc_lifts),
            "minFoldAucLiftVsPublic": min(auc_lifts) if auc_lifts else None,
            "positiveAucLiftFoldRate": sum(value > 0 for value in auc_lifts) / len(auc_lifts) if auc_lifts else None,
            "positiveLogLossLiftFoldRate": ll_better / len(candidate_folds) if candidate_folds else None,
        })

    lifecycle_cmp = next((row for row in comparisons if row["featureSet"] == "PUBLIC_PLUS_LIFECYCLE"), {})
    mean_lift = lifecycle_cmp.get("aucLiftVsPublic")
    positive_auc_rate = lifecycle_cmp.get("positiveAucLiftFoldRate")
    positive_ll_rate = lifecycle_cmp.get("positiveLogLossLiftFoldRate")

    if mean_lift is None or positive_auc_rate is None or positive_ll_rate is None:
        decision = "INVALID_NO_COMPARABLE_FOLDS"
    elif float(mean_lift) >= 0.03 and float(positive_auc_rate) >= 0.75 and float(positive_ll_rate) >= 0.75:
        decision = "PASS_ORDINARY_CHRONOLOGICAL_PROCEED_SPECIAL_STRESS"
    elif float(mean_lift) >= 0.015 and float(positive_auc_rate) >= 0.5:
        decision = "AMBIGUOUS_ORDINARY_KEEP_RESEARCH_ONLY"
    else:
        decision = "FAIL_ORDINARY_CHRONOLOGICAL"

    meta: dict[str, Any] = {}
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
            "ordinaryPassRule": "PUBLIC_PLUS_LIFECYCLE mean AUC lift vs PUBLIC >= +0.03, positive AUC-lift fold rate >= 75%, positive log-loss-lift fold rate >= 75%",
            "meanLifecycleAucLiftVsPublic": mean_lift,
            "positiveLifecycleAucLiftFoldRate": positive_auc_rate,
            "positiveLifecycleLogLossLiftFoldRate": positive_ll_rate,
            "specialMarketNext": "Only rebuild/freeze a separate 2026-08-16+ special cohort after PASS_ORDINARY_CHRONOLOGICAL_PROCEED_SPECIAL_STRESS.",
        },
        "elapsedSeconds": time.perf_counter() - started,
    }
    _write_json(args.report, report)
    _progress(started, f"STOP GATE decision={decision} lifecycleMeanLift={mean_lift} aucPositiveRate={positive_auc_rate} llPositiveRate={positive_ll_rate}")
    _progress(started, f"REPORT {args.report.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
