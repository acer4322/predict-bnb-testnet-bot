from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import build_target_maker_inventory_conditioned_side_v2 as builder
import train_target_maker_direct_placement_v1 as maker_v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = builder.DEFAULT_OUTPUT
DEFAULT_META = builder.DEFAULT_META
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_inventory_conditioned_side_v2_report.json"
REPORT_VERSION = "TARGET_MAKER_INVENTORY_CONDITIONED_SIDE_V2_ORDINARY_WALK_FORWARD_SPECIAL_AUDIT"

PUBLIC_ONLY = list(maker_v1.SIDE_FEATURE_SETS["full_public"])
MAKER_ONLY = list(builder.MAKER_INVENTORY_FEATURES)
INVENTORY_ONLY = list(builder.INVENTORY_FEATURES)
PUBLIC_PLUS_MAKER = list(dict.fromkeys(PUBLIC_ONLY + MAKER_ONLY))
PUBLIC_PLUS_INVENTORY = list(dict.fromkeys(PUBLIC_ONLY + INVENTORY_ONLY))

FEATURE_SETS = {
    "PUBLIC_ONLY": PUBLIC_ONLY,
    "MAKER_INVENTORY_ONLY": MAKER_ONLY,
    "FULL_INVENTORY_ONLY": INVENTORY_ONLY,
    "PUBLIC_PLUS_MAKER_INVENTORY": PUBLIC_PLUS_MAKER,
    "PUBLIC_PLUS_FULL_INVENTORY": PUBLIC_PLUS_INVENTORY,
}
QUICK_FEATURE_SETS = {
    "PUBLIC_ONLY": PUBLIC_ONLY,
    "PUBLIC_PLUS_MAKER_INVENTORY": PUBLIC_PLUS_MAKER,
    "PUBLIC_PLUS_FULL_INVENTORY": PUBLIC_PLUS_INVENTORY,
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


def _fold_metric_summary(feature_payload: dict[str, Any]) -> dict[str, Any]:
    folds = [row for row in feature_payload.get("folds", []) if row.get("status") == "OK"]
    aucs = [float(row["test"]["rocAuc"]) for row in folds if row.get("test", {}).get("rocAuc") is not None]
    logloss = [float(row["test"]["logLoss"]) for row in folds if row.get("test", {}).get("logLoss") is not None]
    brier = [float(row["test"]["brier"]) for row in folds if row.get("test", {}).get("brier") is not None]
    balanced = [float(row["test"]["balancedAccuracy"]) for row in folds if row.get("test", {}).get("balancedAccuracy") is not None]
    return {
        "validFolds": len(folds),
        "meanAuc": statistics.fmean(aucs) if aucs else None,
        "minAuc": min(aucs) if aucs else None,
        "medianAuc": statistics.median(aucs) if aucs else None,
        "maxAuc": max(aucs) if aucs else None,
        "meanLogLoss": statistics.fmean(logloss) if logloss else None,
        "meanBrier": statistics.fmean(brier) if brier else None,
        "meanBalancedAccuracy": statistics.fmean(balanced) if balanced else None,
        "positiveAucFoldRate": sum(value > 0.5 for value in aucs) / len(aucs) if aucs else None,
    }


def _classification_suite_with_progress(
    *,
    shared: Any,
    deps: dict[str, Any],
    frame: Any,
    label: str,
    feature_sets: dict[str, list[str]],
    folds: list[dict[str, list[int]]],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed_base: int,
    started: float,
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
    total_fits = len(feature_sets) * len(folds)
    fit_index = 0
    for feature_index, (name, requested) in enumerate(feature_sets.items()):
        features = maker_v1._usable(requested, working, pd)
        if not features:
            continue
        _progress(started, f"FEATURE {name} start ({len(features)} features)")
        fold_rows: list[dict[str, Any]] = []
        feature_started = time.perf_counter()
        for fold_index, fold in enumerate(folds):
            fit_index += 1
            fold_started = time.perf_counter()
            _progress(
                started,
                f"FIT {fit_index}/{total_fits} {name} fold {fold_index + 1}/{len(folds)} "
                f"train={len(fold['trainMarkets'])} cal={len(fold['calibrationMarkets'])} test={len(fold['testMarkets'])}",
            )
            row = shared._classification_fold(
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
            fold_rows.append(row)
            auc = row.get("test", {}).get("rocAuc") if row.get("status") == "OK" else None
            lift = row.get("logLossLiftVsPrior")
            _progress(
                started,
                f"DONE {name} fold {fold_index + 1}/{len(folds)} "
                f"status={row.get('status')} AUC={auc} logloss_lift={lift} "
                f"fit_elapsed={time.perf_counter() - fold_started:.1f}s",
            )
        summary = shared._aggregate_classification(fold_rows)
        summary["features"] = features
        task["featureSets"][name] = summary
        _progress(
            started,
            f"FEATURE {name} complete meanAUC={summary.get('meanTestRocAuc')} "
            f"elapsed={time.perf_counter() - feature_started:.1f}s",
        )
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


def _term_importances(model: Any, features: list[str], limit: int = 20) -> list[dict[str, Any]]:
    try:
        names = list(model.term_names_)
        importances = [float(value) for value in model.term_importances()]
    except Exception:
        return []
    rows = sorted(
        ({"term": str(name), "importance": importance} for name, importance in zip(names, importances)),
        key=lambda row: float(row["importance"]),
        reverse=True,
    )
    return rows[: max(1, int(limit))]


def _special_audit(
    *,
    shared: Any,
    deps: dict[str, Any],
    ordinary: Any,
    special: Any,
    feature_sets: dict[str, list[str]],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    started: float,
) -> dict[str, Any]:
    pd = deps["pd"]
    if len(special) < 20:
        return {"status": "INSUFFICIENT_SPECIAL_ROWS", "rows": int(len(special)), "markets": int(special["market_id"].nunique()) if len(special) else 0}
    markets = maker_v1._market_order(ordinary, "placement_first_ms")
    if len(markets) < 20:
        return {"status": "INSUFFICIENT_ORDINARY_MARKETS", "ordinaryMarkets": len(markets)}
    calibration_count = max(8, int(round(len(markets) * 0.15)))
    calibration_count = min(calibration_count, max(1, len(markets) - 12))
    train_markets = markets[:-calibration_count]
    calibration_markets = markets[-calibration_count:]
    train = ordinary[ordinary["market_id"].isin(train_markets)].copy()
    calibration = ordinary[ordinary["market_id"].isin(calibration_markets)].copy()
    y_train = train["label_side_up"].astype(int)
    y_cal = calibration["label_side_up"].astype(int)
    y_special = special["label_side_up"].astype(int)
    if set(int(value) for value in y_train.unique()) != {0, 1} or set(int(value) for value in y_special.unique()) != {0, 1}:
        return {"status": "INSUFFICIENT_CLASSES", "trainRows": int(len(train)), "specialRows": int(len(special))}

    result: dict[str, Any] = {
        "status": "OK",
        "trainMarkets": len(train_markets),
        "calibrationMarkets": len(calibration_markets),
        "specialMarkets": int(special["market_id"].nunique()),
        "specialRows": int(len(special)),
        "featureSets": {},
    }
    for index, (name, requested) in enumerate(feature_sets.items()):
        features = maker_v1._usable(requested, ordinary, pd)
        if not features:
            continue
        audit_started = time.perf_counter()
        _progress(started, f"SPECIAL AUDIT {name} start")
        model = shared._fit_classifier(
            deps,
            shared._numeric(pd, train, features),
            y_train,
            interactions=min(max(0, interactions), max(0, len(features) // 2)),
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            seed=9100 + index,
        )
        raw_cal = model.predict_proba(shared._numeric(pd, calibration, features))[:, 1] if len(calibration) else deps["np"].array([])
        calibrator = shared._calibrate(deps, y_cal, raw_cal)
        raw_special = model.predict_proba(shared._numeric(pd, special, features))[:, 1]
        probability = shared._apply_calibration(deps, calibrator, raw_special)
        metrics = shared._classification_metrics(deps, y_special, probability)
        phase_metrics: dict[str, Any] = {}
        seconds = pd.to_numeric(special["seconds_left"], errors="coerce")
        phase_masks = {
            "OPEN_GT180": seconds > 180,
            "MID_60_180": (seconds > 60) & (seconds <= 180),
            "TAIL_LE60": seconds <= 60,
        }
        for phase, mask in phase_masks.items():
            if int(mask.sum()) < 20:
                continue
            phase_metrics[phase] = shared._classification_metrics(
                deps,
                y_special.loc[mask],
                probability[mask.to_numpy()],
            )
        first_mask = pd.to_numeric(special["is_first_maker_placement"], errors="coerce").fillna(0).astype(int) == 1
        lifecycle_metrics: dict[str, Any] = {}
        for label, mask in {"FIRST_MAKER": first_mask, "LATER_MAKER": ~first_mask}.items():
            if int(mask.sum()) < 20:
                continue
            lifecycle_metrics[label] = shared._classification_metrics(
                deps,
                y_special.loc[mask],
                probability[mask.to_numpy()],
            )
        result["featureSets"][name] = {
            "features": features,
            "metrics": metrics,
            "phaseMetrics": phase_metrics,
            "firstVsLaterMetrics": lifecycle_metrics,
            "topTermImportances": _term_importances(model, features),
        }
        _progress(
            started,
            f"SPECIAL AUDIT {name} done AUC={metrics.get('rocAuc')} "
            f"elapsed={time.perf_counter() - audit_started:.1f}s",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Target Maker inventory-conditioned side V2")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--preset", choices=("quick", "full"), default="quick")
    parser.add_argument("--min-train-markets", type=int, default=60)
    parser.add_argument("--test-markets", type=int, default=12)
    parser.add_argument("--max-folds", type=int, default=2)
    parser.add_argument("--interactions", type=int, default=4)
    parser.add_argument("--max-rounds", type=int, default=500)
    parser.add_argument("--outer-bags", type=int, default=3)
    args = parser.parse_args()

    started = time.perf_counter()
    _progress(started, f"START preset={args.preset}")
    active_feature_sets = QUICK_FEATURE_SETS if args.preset == "quick" else FEATURE_SETS
    shared = maker_v1._shared()
    deps = shared._imports()
    pd = deps["pd"]
    dataset_path = args.dataset.expanduser().resolve()
    if not dataset_path.exists():
        raise SystemExit("Inventory-conditioned Maker dataset missing. Run build_target_maker_inventory_conditioned_side_v2.py first.")
    frame = pd.read_csv(dataset_path)
    required = {"market_id", "placement_first_ms", "label_side_up", "regime"} | set(builder.INVENTORY_FEATURES)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("Inventory-conditioned dataset missing columns: " + ", ".join(missing))
    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["placement_first_ms"] = pd.to_numeric(frame["placement_first_ms"], errors="raise").astype("int64")
    frame["label_side_up"] = pd.to_numeric(frame["label_side_up"], errors="raise").astype(int)

    ordinary = frame[frame["regime"] == "ORDINARY_PRE_SPECIAL"].copy()
    special = frame[frame["regime"] == "SPECIAL"].copy()
    outside = frame[frame["regime"] == "OUTSIDE_PUBLIC_COHORT"].copy()
    ordinary_markets = maker_v1._market_order(ordinary, "placement_first_ms")
    min_train = max(20, int(args.min_train_markets))
    test_markets = max(5, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    folds = maker_v1._spanning_folds(
        ordinary_markets,
        min_train=min_train,
        test_markets=test_markets,
        max_folds=max_folds,
    )
    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    _progress(
        started,
        f"DATA ready ordinary_rows={len(ordinary)} markets={len(ordinary_markets)} "
        f"special_rows={len(special)} folds={len(folds)} featuresets={list(active_feature_sets)} "
        f"rounds={max_rounds} bags={outer_bags}",
    )

    side_task = _classification_suite_with_progress(
        shared=shared,
        deps=deps,
        frame=ordinary,
        label="label_side_up",
        feature_sets=active_feature_sets,
        folds=folds,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed_base=7000,
        started=started,
    )
    summaries = {
        name: _fold_metric_summary(payload)
        for name, payload in side_task.get("featureSets", {}).items()
    }
    public_auc = summaries.get("PUBLIC_ONLY", {}).get("meanAuc")
    for name, summary in summaries.items():
        auc = summary.get("meanAuc")
        summary["aucLiftVsPublicOnly"] = (
            float(auc) - float(public_auc)
            if auc is not None and public_auc is not None else None
        )

    if args.preset == "full":
        _progress(started, "Ordinary full screen complete; starting SPECIAL stress audit")
        special_audit = _special_audit(
            shared=shared,
            deps=deps,
            ordinary=ordinary,
            special=special,
            feature_sets=active_feature_sets,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            started=started,
        )
    else:
        special_audit = {
            "status": "SKIPPED_QUICK_SCREEN",
            "reason": "Quick preset stops after the shortest ordinary OOF evidence screen. Run preset=full only after reviewing evidence.",
        }
        _progress(started, "QUICK gate reached; SPECIAL audit intentionally skipped")

    primary = summaries.get("PUBLIC_PLUS_FULL_INVENTORY", {})
    maker_only_combo = summaries.get("PUBLIC_PLUS_MAKER_INVENTORY", {})
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "preset": args.preset,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "question": "Does strict-past Target inventory state explain the next inferred Maker placement side beyond the V1 full-public baseline?",
        "labelIdentity": "INFERRED_PLACEMENT_SIDE_NOT_PRIVATE_ORDER_GROUND_TRUTH",
        "deploymentBoundary": "Target inventory is used only as a research teacher state. Live deployment must substitute our own strict-past inventory state.",
        "timestampBoundary": "Only official fill events with event_ms < placement_first_ms enter inventory features. Same-timestamp/current placement fills are forbidden.",
        "fitPolicy": "ORDINARY_PRE_SPECIAL only, chronological market walk-forward. SPECIAL never enters fit or calibration.",
        "quickGatePolicy": (
            "Quick is the default: PUBLIC_ONLY vs PUBLIC+MAKER vs PUBLIC+FULL inventory, reduced folds/rounds/bags, "
            "and no SPECIAL audit. Full is manual only after evidence review."
        ),
        "runConfig": {
            "minTrainMarkets": min_train,
            "testMarkets": test_markets,
            "maxFolds": max_folds,
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
            "featureSets": list(active_feature_sets),
        },
        "dataset": str(dataset_path),
        "rows": int(len(frame)),
        "markets": int(frame["market_id"].nunique()),
        "ordinaryRows": int(len(ordinary)),
        "ordinaryMarkets": len(ordinary_markets),
        "specialRows": int(len(special)),
        "specialMarkets": int(special["market_id"].nunique()) if len(special) else 0,
        "outsideRows": int(len(outside)),
        "featureSets": active_feature_sets,
        "walkForwardFolds": folds,
        "ordinarySideTask": side_task,
        "ordinarySummary": summaries,
        "specialUntouchedAudit": special_audit,
        "primaryComparison": {
            "publicOnlyMeanAuc": public_auc,
            "publicPlusMakerInventoryMeanAuc": maker_only_combo.get("meanAuc"),
            "publicPlusFullInventoryMeanAuc": primary.get("meanAuc"),
            "makerInventoryAucLiftVsPublic": maker_only_combo.get("aucLiftVsPublicOnly"),
            "fullInventoryAucLiftVsPublic": primary.get("aucLiftVsPublicOnly"),
            "interpretation": (
                "A large, fold-stable positive lift supports an inventory-conditioned Maker-side policy. "
                "Near-zero/negative lift rejects inventory state as the missing explanation for weak V1 Maker-side prediction."
            ),
        },
        "guardrails": [
            "Do not interpret inferred placement labels as complete private placement/cancel/reprice truth.",
            "Do not promote a live rule from SPECIAL performance; SPECIAL is untouched stress audit only.",
            "Do not deploy Target inventory features directly. Replace them with the strategy's own inventory state.",
            "Same-timestamp official fills are explicitly excluded from features to prevent current-action leakage.",
            "Do not run the full preset automatically after quick; review quick evidence first.",
        ],
    }
    meta_path = args.meta.expanduser().resolve()
    if meta_path.exists():
        try:
            report["datasetMeta"] = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["datasetMetaReadError"] = type(exc).__name__

    _write_json(args.report, report)
    _progress(started, "RESULT SUMMARY")
    for name, summary in summaries.items():
        print(
            f"  {name}: AUC={summary.get('meanAuc')} lift_vs_public={summary.get('aucLiftVsPublicOnly')} "
            f"logloss={summary.get('meanLogLoss')} folds={summary.get('validFolds')}",
            flush=True,
        )
    _progress(started, f"DONE report={args.report.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
