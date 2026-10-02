from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_action_onset_preflight_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_action_model_v1_report.json"
DEFAULT_OOF = ROOT / "data" / "research" / "target_taker_action_model_v1_oof.csv"
REPORT_VERSION = "TARGET_TAKER_ACTION_MODEL_V1_ORDINARY_WALK_FORWARD_STRICT_PAST"

FROZEN16 = [
    "seconds_left",
    "predict_up_mid",
    "predict_up_spread",
    "predict_down_spread",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "signal_age_ms",
]

SIGNED_FEATURES = [
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
]

TRANSITION_ALIGNED = [
    "seconds_left",
    "prior_side_predict_mid",
    "prior_side_predict_spread",
    *[f"prior_aligned_{feature}" for feature in SIGNED_FEATURES],
    "signal_age_ms",
    "prior_clean_side_up",
]

TASKS = {
    "stage1_clean_vs_mixed": {
        "positive": "MIXED",
        "featureSets": {
            "frozen16_raw": FROZEN16,
            "frozen16_plus_prior_side": FROZEN16 + ["prior_clean_side_up"],
        },
    },
    "stage2_same_vs_flip": {
        "positive": "FLIP",
        "featureSets": {
            "frozen16_raw": FROZEN16,
            "frozen16_plus_prior_side": FROZEN16 + ["prior_clean_side_up"],
            "prior_side_aligned": TRANSITION_ALIGNED,
        },
    },
    "side_audit_up_vs_down": {
        "positive": "UP",
        "featureSets": {
            "frozen16_raw": FROZEN16,
        },
    },
}


def _shared() -> Any:
    path = ROOT / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("shared_target_taker_action_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _market_order(frame: Any, pd: Any) -> list[int]:
    rows = (
        frame.groupby("market_id", as_index=False)["burst_onset_ms"]
        .min()
        .sort_values(["burst_onset_ms", "market_id"])
    )
    return [int(v) for v in rows["market_id"].tolist()]


def _attach_prior_clean_side(frame: Any, pd: Any) -> Any:
    result = frame.sort_values(
        ["market_id", "burst_onset_ms", "burst_index"], kind="mergesort"
    ).copy()
    result["prior_clean_side"] = ""
    state: dict[int, str] = {}
    for idx, row in result.iterrows():
        market_id = int(row["market_id"])
        prior = state.get(market_id, "")
        result.at[idx, "prior_clean_side"] = prior

        label = str(row.get("clean_mixed_label") or "").upper()
        side = str(row.get("clean_side_label") or "").upper()
        if label == "CLEAN" and side in {"UP", "DOWN"}:
            state[market_id] = side

    result["prior_clean_side_up"] = result["prior_clean_side"].map(
        {"UP": 1.0, "DOWN": 0.0}
    )
    sign = result["prior_clean_side"].map({"UP": 1.0, "DOWN": -1.0})
    up_mid = pd.to_numeric(result["predict_up_mid"], errors="coerce")
    up_spread = pd.to_numeric(result["predict_up_spread"], errors="coerce")
    down_spread = pd.to_numeric(result["predict_down_spread"], errors="coerce")
    result["prior_side_predict_mid"] = up_mid.where(
        result["prior_clean_side"] == "UP", 1.0 - up_mid
    )
    result["prior_side_predict_spread"] = up_spread.where(
        result["prior_clean_side"] == "UP", down_spread
    )
    for feature in SIGNED_FEATURES:
        result[f"prior_aligned_{feature}"] = (
            pd.to_numeric(result[feature], errors="coerce") * sign
        )
    return result


def _task_frame(frame: Any, task: str, pd: Any) -> Any:
    base = frame[
        (frame["region"] == "ORDINARY_PRE_SPECIAL")
        & (pd.to_numeric(frame["strict_past_within_2s"], errors="coerce") == 1)
        & (pd.to_numeric(frame["is_first_burst"], errors="coerce") == 0)
    ].copy()
    if task == "stage1_clean_vs_mixed":
        base = base[base["clean_mixed_label"].isin(["CLEAN", "MIXED"])].copy()
        base["label"] = (base["clean_mixed_label"] == "MIXED").astype(int)
    elif task == "stage2_same_vs_flip":
        base = base[
            base["transition_label"].isin(["SAME", "FLIP"])
            & base["prior_clean_side"].isin(["UP", "DOWN"])
        ].copy()
        base["label"] = (base["transition_label"] == "FLIP").astype(int)
    elif task == "side_audit_up_vs_down":
        base = base[
            (base["clean_mixed_label"] == "CLEAN")
            & base["clean_side_label"].isin(["UP", "DOWN"])
        ].copy()
        base["label"] = (base["clean_side_label"] == "UP").astype(int)
    else:
        raise ValueError(task)
    return base


def _special_task_frame(frame: Any, task: str, pd: Any) -> Any:
    base = frame[
        (frame["region"].isin(["POST_SPECIAL_START_AUDIT", "SPECIAL_WINDOW_AUDIT"]))
        & (pd.to_numeric(frame["strict_past_within_2s"], errors="coerce") == 1)
        & (pd.to_numeric(frame["is_first_burst"], errors="coerce") == 0)
    ].copy()
    if task == "stage1_clean_vs_mixed":
        base = base[base["clean_mixed_label"].isin(["CLEAN", "MIXED"])].copy()
        base["label"] = (base["clean_mixed_label"] == "MIXED").astype(int)
    elif task == "stage2_same_vs_flip":
        base = base[
            base["transition_label"].isin(["SAME", "FLIP"])
            & base["prior_clean_side"].isin(["UP", "DOWN"])
        ].copy()
        base["label"] = (base["transition_label"] == "FLIP").astype(int)
    elif task == "side_audit_up_vs_down":
        base = base[
            (base["clean_mixed_label"] == "CLEAN")
            & base["clean_side_label"].isin(["UP", "DOWN"])
        ].copy()
        base["label"] = (base["clean_side_label"] == "UP").astype(int)
    else:
        raise ValueError(task)
    return base


def _rank_metrics(deps: dict[str, Any], y: Any, score: Any) -> dict[str, Any]:
    np = deps["np"]
    y_arr = np.asarray(y, dtype=int)
    p = np.asarray(score, dtype=float)
    both = set(int(v) for v in y_arr.tolist()) == {0, 1}
    return {
        "rows": int(len(y_arr)),
        "positives": int(y_arr.sum()),
        "positiveRate": float(y_arr.mean()) if len(y_arr) else None,
        "rocAuc": float(deps["roc_auc_score"](y_arr, p)) if both else None,
        "averagePrecision": (
            float(deps["average_precision_score"](y_arr, p))
            if int(y_arr.sum()) > 0
            else None
        ),
        "scoreMean": float(np.mean(p)) if len(p) else None,
        "scoreP10": float(np.quantile(p, 0.10)) if len(p) else None,
        "scoreP50": float(np.quantile(p, 0.50)) if len(p) else None,
        "scoreP90": float(np.quantile(p, 0.90)) if len(p) else None,
    }


def _selective_metrics(
    deps: dict[str, Any],
    y_test: Any,
    test_score: Any,
    calibration_score: Any,
    quantile: float,
) -> dict[str, Any]:
    np = deps["np"]
    y = np.asarray(y_test, dtype=int)
    score = np.asarray(test_score, dtype=float)
    cal = np.asarray(calibration_score, dtype=float)
    q = float(quantile)
    if len(cal) < 20:
        return {"status": "INSUFFICIENT_CALIBRATION", "quantile": q}
    low = float(np.quantile(cal, q))
    high = float(np.quantile(cal, 1.0 - q))
    low_mask = score <= low
    high_mask = score >= high
    selected = low_mask | high_mask
    count = int(selected.sum())
    if count == 0:
        return {
            "status": "NO_SELECTION",
            "quantile": q,
            "lowThreshold": low,
            "highThreshold": high,
        }
    predicted = high_mask[selected].astype(int)
    actual = y[selected]
    low_count = int(low_mask.sum())
    high_count = int(high_mask.sum())
    return {
        "status": "OK",
        "quantile": q,
        "lowThreshold": low,
        "highThreshold": high,
        "selectedRows": count,
        "coverage": float(count / len(y)),
        "accuracy": float((predicted == actual).mean()),
        "highRows": high_count,
        "highPositiveRate": float(y[high_mask].mean()) if high_count else None,
        "lowRows": low_count,
        "lowNegativeRate": float((1 - y[low_mask]).mean()) if low_count else None,
    }


def _phase_metrics(deps: dict[str, Any], test: Any, score: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for phase in ("OPEN", "MID", "TAIL"):
        mask = (test["macro_phase"].astype(str) == phase).to_numpy()
        if int(mask.sum()) == 0:
            result[phase] = {"rows": 0}
            continue
        result[phase] = _rank_metrics(
            deps, test.loc[mask, "label"].astype(int).to_numpy(), score[mask]
        )
    return result


def _fit_fold(
    *,
    shared: Any,
    deps: dict[str, Any],
    frame: Any,
    features: list[str],
    fold: dict[str, list[int]],
    seed: int,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pd = deps["pd"]
    train = frame[frame["market_id"].isin(fold["trainMarkets"])].copy()
    calibration = frame[frame["market_id"].isin(fold["calibrationMarkets"])].copy()
    test = frame[frame["market_id"].isin(fold["testMarkets"])].copy()
    y_train = train["label"].astype(int)
    y_test = test["label"].astype(int)
    if len(train) < 80 or len(test) < 20:
        return (
            {
                "status": "INSUFFICIENT_DATA",
                "trainRows": int(len(train)),
                "testRows": int(len(test)),
            },
            [],
        )
    if set(int(v) for v in y_train.unique()) != {0, 1}:
        return (
            {
                "status": "INSUFFICIENT_CLASSES",
                "trainRows": int(len(train)),
                "testRows": int(len(test)),
            },
            [],
        )

    model = shared._fit_classifier(
        deps,
        shared._numeric(pd, train, features),
        y_train,
        interactions=min(max(0, interactions), max(0, len(features) // 2)),
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    raw_test = model.predict_proba(shared._numeric(pd, test, features))[:, 1]
    raw_cal = (
        model.predict_proba(shared._numeric(pd, calibration, features))[:, 1]
        if len(calibration)
        else deps["np"].array([])
    )
    metrics = _rank_metrics(deps, y_test.to_numpy(), raw_test)
    selective = {
        f"q{int(q * 100)}": _selective_metrics(
            deps, y_test.to_numpy(), raw_test, raw_cal, q
        )
        for q in (0.10, 0.20, 0.30)
    }
    summary = {
        "status": "OK",
        "trainRows": int(len(train)),
        "calibrationRows": int(len(calibration)),
        "testRows": int(len(test)),
        "trainPositiveRate": float(y_train.mean()),
        "test": metrics,
        "byMacroPhase": _phase_metrics(deps, test, raw_test),
        "selectiveFromPriorCalibrationQuantiles": selective,
        "termImportances": shared._term_summary(model),
        "testMarkets": [int(v) for v in fold["testMarkets"]],
    }
    pred_rows: list[dict[str, Any]] = []
    for (_, row), score in zip(test.iterrows(), raw_test):
        pred_rows.append(
            {
                "market_id": int(row["market_id"]),
                "burst_onset_ms": int(row["burst_onset_ms"]),
                "macro_phase": str(row.get("macro_phase") or ""),
                "label": int(row["label"]),
                "score": float(score),
                "prior_clean_side": str(row.get("prior_clean_side") or ""),
                "clean_side_label": str(row.get("clean_side_label") or ""),
                "transition_label": str(row.get("transition_label") or ""),
                "clean_mixed_label": str(row.get("clean_mixed_label") or ""),
            }
        )
    return summary, pred_rows


def _pooled_report(deps: dict[str, Any], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    if not predictions:
        return {"rows": 0}
    pd = deps["pd"]
    frame = pd.DataFrame(predictions)
    overall = _rank_metrics(
        deps, frame["label"].astype(int).to_numpy(), frame["score"].astype(float).to_numpy()
    )
    overall["byMacroPhase"] = {}
    for phase in ("OPEN", "MID", "TAIL"):
        part = frame[frame["macro_phase"] == phase]
        overall["byMacroPhase"][phase] = (
            _rank_metrics(
                deps,
                part["label"].astype(int).to_numpy(),
                part["score"].astype(float).to_numpy(),
            )
            if len(part)
            else {"rows": 0}
        )
    return overall


def _ordinary_to_special_audit(
    *,
    shared: Any,
    deps: dict[str, Any],
    ordinary: Any,
    special: Any,
    features: list[str],
    seed: int,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
) -> dict[str, Any]:
    pd = deps["pd"]
    if len(ordinary) < 80 or len(special) < 20:
        return {
            "status": "INSUFFICIENT_DATA",
            "trainRows": int(len(ordinary)),
            "testRows": int(len(special)),
        }
    y_train = ordinary["label"].astype(int)
    y_test = special["label"].astype(int)
    if set(int(v) for v in y_train.unique()) != {0, 1}:
        return {"status": "INSUFFICIENT_CLASSES"}
    model = shared._fit_classifier(
        deps,
        shared._numeric(pd, ordinary, features),
        y_train,
        interactions=min(max(0, interactions), max(0, len(features) // 2)),
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    score = model.predict_proba(shared._numeric(pd, special, features))[:, 1]
    return {
        "status": "OK",
        "trainRows": int(len(ordinary)),
        "testRows": int(len(special)),
        "trainMarkets": int(ordinary["market_id"].nunique()),
        "testMarkets": int(special["market_id"].nunique()),
        "test": _rank_metrics(deps, y_test.to_numpy(), score),
        "byMacroPhase": _phase_metrics(deps, special, score),
        "termImportances": shared._term_summary(model),
        "warning": "AUDIT_ONLY: special/post-special rows are never used for model fitting.",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(_clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _write_oof(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task",
        "feature_set",
        "fold",
        "market_id",
        "burst_onset_ms",
        "macro_phase",
        "label",
        "score",
        "prior_clean_side",
        "clean_side_label",
        "transition_label",
        "clean_mixed_label",
    ]
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train Target Taker action-onset EBMs on ordinary pre-special markets only. "
            "Strict-past cap2 onset rows are primary; special/post-special rows are audit only."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--min-train-markets", type=int, default=180)
    parser.add_argument("--test-markets", type=int, default=50)
    parser.add_argument("--max-folds", type=int, default=7)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=600)
    parser.add_argument("--outer-bags", type=int, default=3)
    args = parser.parse_args()

    shared = _shared()
    deps = shared._imports()
    pd = deps["pd"]

    path = args.dataset.expanduser().resolve()
    if not path.exists():
        raise SystemExit(
            f"dataset missing: {path}\n"
            "Run: .\\run-target-taker-action-onset-preflight-v1.ps1 first"
        )
    frame = pd.read_csv(path)
    required = {
        "market_id",
        "burst_onset_ms",
        "burst_index",
        "region",
        "strict_past_within_2s",
        "is_first_burst",
        "clean_mixed_label",
        "transition_label",
        "clean_side_label",
        "macro_phase",
        *FROZEN16,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("dataset missing columns: " + ", ".join(missing))

    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["burst_onset_ms"] = pd.to_numeric(frame["burst_onset_ms"], errors="raise").astype("int64")
    frame["burst_index"] = pd.to_numeric(frame["burst_index"], errors="raise").astype(int)
    frame = _attach_prior_clean_side(frame, pd)

    min_train = max(50, int(args.min_train_markets))
    test_markets = max(10, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))

    print(REPORT_VERSION, flush=True)
    print(
        f"walk-forward: minTrain={min_train} testMarkets={test_markets} maxFolds={max_folds} "
        f"interactions={interactions} maxRounds={max_rounds} outerBags={outer_bags}",
        flush=True,
    )
    print("Special/post-special rows are AUDIT ONLY and never enter fitting.", flush=True)

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "dataset": str(path),
        "targetUnit": "one cap2 action-burst onset row",
        "strictPastBoundary": "feature_sample_ms < burst_onset_ms and strict_past_within_2s == 1",
        "trainingPopulation": "ORDINARY_PRE_SPECIAL only",
        "specialPopulation": "AUDIT ONLY; never fit",
        "scorePolicy": (
            "Raw balanced-EBM scores are evaluated primarily by ROC AUC/AP and rank/selective metrics. "
            "No fixed p=0.5 deployment threshold and no special-regime calibration are used."
        ),
        "transitionSemantics": (
            "SAME/FLIP is relative to the previous clean Target burst. "
            "prior_clean_side is reconstructed strictly from earlier burst rows; MIXED bursts do not update it."
        ),
        "walkForward": {
            "minTrainMarkets": min_train,
            "testMarkets": test_markets,
            "maxFolds": max_folds,
        },
        "config": {
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
        },
        "tasks": {},
    }

    all_oof: list[dict[str, Any]] = []
    planned = sum(len(cfg["featureSets"]) for cfg in TASKS.values())
    completed = 0

    for task_index, (task_name, task_cfg) in enumerate(TASKS.items(), 1):
        ordinary = _task_frame(frame, task_name, pd)
        special = _special_task_frame(frame, task_name, pd)
        markets = _market_order(ordinary, pd)
        if len(markets) < min_train + test_markets:
            raise SystemExit(
                f"{task_name}: insufficient ordinary markets for walk-forward: {len(markets)}"
            )
        folds = shared._walk_forward_folds(
            markets,
            min_train_markets=min_train,
            test_markets=test_markets,
            max_folds=max_folds,
        )
        task_report: dict[str, Any] = {
            "positiveClass": task_cfg["positive"],
            "ordinaryRows": int(len(ordinary)),
            "ordinaryMarkets": int(ordinary["market_id"].nunique()),
            "ordinaryPositiveRate": float(ordinary["label"].mean()),
            "specialAuditRows": int(len(special)),
            "specialAuditMarkets": int(special["market_id"].nunique()),
            "specialAuditPositiveRate": float(special["label"].mean()) if len(special) else None,
            "folds": folds,
            "featureSets": {},
        }
        report["tasks"][task_name] = task_report
        print(
            f"\n[{task_index}/{len(TASKS)}] {task_name}: ordinary={len(ordinary):,} "
            f"markets={ordinary['market_id'].nunique()} positive={ordinary['label'].mean():.2%} "
            f"specialAudit={len(special):,}",
            flush=True,
        )

        for feature_index, (feature_name, features) in enumerate(task_cfg["featureSets"].items(), 1):
            completed += 1
            missing_features = [
                feature
                for feature in features
                if feature not in ordinary.columns
                or not pd.to_numeric(ordinary[feature], errors="coerce").notna().any()
            ]
            if missing_features:
                task_report["featureSets"][feature_name] = {
                    "status": "MISSING_FEATURES",
                    "missing": missing_features,
                }
                print(
                    f"  [{completed}/{planned}] {feature_name}: missing {missing_features}",
                    flush=True,
                )
                continue

            fold_reports: list[dict[str, Any]] = []
            feature_oof: list[dict[str, Any]] = []
            print(
                f"  [{completed}/{planned}] {feature_name}: {len(features)} features, {len(folds)} folds",
                flush=True,
            )
            for fold_index, fold in enumerate(folds, 1):
                fold_report, predictions = _fit_fold(
                    shared=shared,
                    deps=deps,
                    frame=ordinary,
                    features=features,
                    fold=fold,
                    seed=10000 + task_index * 1000 + feature_index * 100 + fold_index,
                    interactions=interactions,
                    max_rounds=max_rounds,
                    outer_bags=outer_bags,
                )
                fold_report["fold"] = fold_index
                fold_reports.append(fold_report)
                for row in predictions:
                    row.update(
                        {
                            "task": task_name,
                            "feature_set": feature_name,
                            "fold": fold_index,
                        }
                    )
                feature_oof.extend(predictions)
                if fold_report.get("status") == "OK":
                    metrics = fold_report["test"]
                    print(
                        f"      fold {fold_index}/{len(folds)} rows={metrics['rows']:,} "
                        f"AUC={metrics.get('rocAuc')} AP={metrics.get('averagePrecision')}",
                        flush=True,
                    )
                else:
                    print(
                        f"      fold {fold_index}/{len(folds)} status={fold_report.get('status')}",
                        flush=True,
                    )

            special_audit = _ordinary_to_special_audit(
                shared=shared,
                deps=deps,
                ordinary=ordinary,
                special=special,
                features=features,
                seed=20000 + task_index * 1000 + feature_index * 100,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
            )
            task_report["featureSets"][feature_name] = {
                "status": "OK",
                "features": features,
                "foldReports": fold_reports,
                "pooledOOF": _pooled_report(deps, feature_oof),
                "ordinaryToSpecialAudit": special_audit,
            }
            all_oof.extend(feature_oof)
            _write_json(args.report, report)
            _write_oof(args.oof, all_oof)

    _write_json(args.report, report)
    _write_oof(args.oof, all_oof)
    print(f"\nReport: {args.report.expanduser().resolve()}", flush=True)
    print(f"OOF:    {args.oof.expanduser().resolve()}", flush=True)
    print("No model was promoted to paper/live strategy.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
