from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import analyze_target_maker_heavy_survival_v3 as v3

REPORT_VERSION = "TARGET_MAKER_HEAVY_SURVIVAL_V3_1_INTEGRITY_CALIBRATED_BASELINE"
MIN_ORDINARY_COVERAGE = 0.95
MIN_SPECIAL_COVERAGE = 0.95

_ORIGINAL_FIT = v3._fit_walkforward
_ORIGINAL_SPECIAL = v3._score_special
_ORIGINAL_WRITE_JSON = v3._write_json
_PREDICT_CALIBRATORS: dict[tuple[str, ...], Any] = {}
_INTEGRITY: dict[str, Any] = {}


def _opposite(side: str) -> str:
    value = str(side or "").upper()
    if value == "UP":
        return "DOWN"
    if value == "DOWN":
        return "UP"
    raise ValueError(f"unsupported side: {side!r}")


def _coverage_guard(
    cohort: dict[int, str],
    settlements: dict[int, str],
    *,
    min_ordinary: float = MIN_ORDINARY_COVERAGE,
    min_special: float = MIN_SPECIAL_COVERAGE,
) -> dict[str, Any]:
    settled_ids = set(settlements)
    result: dict[str, Any] = {}
    thresholds = {
        "ORDINARY_PRE_SPECIAL": float(min_ordinary),
        "SPECIAL": float(min_special),
    }
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        requested = {market_id for market_id, value in cohort.items() if value == regime}
        resolved = requested & settled_ids
        missing = sorted(requested - resolved)
        coverage = len(resolved) / len(requested) if requested else None
        threshold = thresholds[regime]
        result[regime] = {
            "requestedMarkets": len(requested),
            "settledMarkets": len(resolved),
            "missingMarkets": len(missing),
            "coverage": coverage,
            "minimumRequired": threshold,
            "passed": coverage is not None and coverage >= threshold,
            "missingMarketIdsFirst50": missing[:50],
        }
    result["passed"] = all(
        bool(result[regime]["passed"])
        for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL")
    )
    return result


def _risk_implied_market_winners(
    risk_rows: list[dict[str, Any]],
) -> tuple[dict[int, str], dict[int, list[str]]]:
    by_market: dict[int, set[str]] = defaultdict(set)
    for row in risk_rows:
        heavy = str(row.get("maker_heavy_side") or "").upper()
        if heavy not in {"UP", "DOWN"}:
            continue
        won = int(row["heavy_side_won"])
        winner = heavy if won == 1 else _opposite(heavy)
        by_market[int(row["market_id"])].add(winner)
    resolved: dict[int, str] = {}
    conflicts: dict[int, list[str]] = {}
    for market_id, winners in by_market.items():
        ordered = sorted(winners)
        if len(ordered) == 1:
            resolved[market_id] = ordered[0]
        elif ordered:
            conflicts[market_id] = ordered
    return resolved, conflicts


def _winner_concordance(
    risk_rows: list[dict[str, Any]],
    settlements: dict[int, str],
) -> dict[str, Any]:
    risk_winners, conflicts = _risk_implied_market_winners(risk_rows)
    overlap = sorted(set(risk_winners) & set(settlements))
    mismatches = [
        market_id
        for market_id in overlap
        if str(risk_winners[market_id]).upper() != str(settlements[market_id]).upper()
    ]
    matched = len(overlap) - len(mismatches)
    return {
        "riskWinnerMarkets": len(risk_winners),
        "settlementMarkets": len(settlements),
        "overlapMarkets": len(overlap),
        "matchedWinnerMarkets": matched,
        "mismatchedWinnerMarkets": len(mismatches),
        "matchRate": matched / len(overlap) if overlap else None,
        "mismatchMarketIdsFirst50": mismatches[:50],
        "conflictingRiskWinnerMarkets": len(conflicts),
        "conflictingRiskWinnerMarketIdsFirst50": sorted(conflicts)[:50],
        "passed": bool(overlap) and not mismatches and not conflicts,
    }


def _blocked_metrics(
    rows: list[dict[str, Any]],
    *,
    label_key: str,
    probability_key: str,
) -> dict[str, Any]:
    brier_by_market: dict[int, list[float]] = defaultdict(list)
    ll_by_market: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        try:
            actual = int(row[label_key])
            probability = float(row[probability_key])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(probability):
            continue
        q = min(1.0 - 1e-7, max(1e-7, probability))
        market_id = int(row["market_id"])
        brier_by_market[market_id].append((actual - q) ** 2)
        ll_by_market[market_id].append(
            -(actual * math.log(q) + (1 - actual) * math.log(1.0 - q))
        )
    markets = sorted(set(brier_by_market) & set(ll_by_market))
    if not markets:
        return {"markets": 0, "brier": None, "logLoss": None}
    return {
        "markets": len(markets),
        "brier": statistics.fmean(
            statistics.fmean(brier_by_market[market_id]) for market_id in markets
        ),
        "logLoss": statistics.fmean(
            statistics.fmean(ll_by_market[market_id]) for market_id in markets
        ),
    }


def _fit_predict_calibrator(
    shared: Any,
    deps: dict[str, Any],
    frame: Any,
) -> Any:
    pd, np = deps["pd"], deps["np"]
    if not len(frame):
        return None
    raw = pd.to_numeric(frame["predict_up_mid"], errors="coerce").to_numpy()
    y = frame["label_up"].astype(int).to_numpy()
    valid = np.isfinite(raw)
    if not valid.any():
        return None
    return shared._calibrate(deps, y[valid], raw[valid])


def _apply_predict_calibrator(
    shared: Any,
    deps: dict[str, Any],
    calibrator: Any,
    frame: Any,
) -> Any:
    pd = deps["pd"]
    raw = pd.to_numeric(frame["predict_up_mid"], errors="coerce").to_numpy()
    return shared._apply_calibration(deps, calibrator, raw)


def _fit_walkforward_v31(
    *,
    shared: Any,
    deps: dict[str, Any],
    ordinary_all: Any,
    ordinary_fit: Any,
    features: list[str],
    min_train_markets: int,
    test_markets: int,
    max_folds: int,
    seed: int,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any, Any]:
    report, score_rows, final_model, final_calibrator = _ORIGINAL_FIT(
        shared=shared,
        deps=deps,
        ordinary_all=ordinary_all,
        ordinary_fit=ordinary_fit,
        features=features,
        min_train_markets=min_train_markets,
        test_markets=test_markets,
        max_folds=max_folds,
        seed=seed,
        interactions=interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
    )
    np = deps["np"]
    markets = v3._market_order(ordinary_fit)
    folds = shared._walk_forward_folds(
        markets,
        min_train_markets=min_train_markets,
        test_markets=test_markets,
        max_folds=max_folds,
    )
    fold_by_number = {
        int(row["fold"]): row
        for row in report.get("folds", [])
        if row.get("status") == "OK"
    }
    calibrated_by_key: dict[tuple[int, int], float] = {}
    all_y: list[int] = []
    all_calibrated: list[float] = []
    fold_lifts_ll: list[float] = []
    fold_lifts_brier: list[float] = []

    for fold_index, fold in enumerate(folds, 1):
        fold_report = fold_by_number.get(fold_index)
        if fold_report is None:
            continue
        calibration = ordinary_fit[
            ordinary_fit["market_id"].isin(fold["calibrationMarkets"])
        ].copy()
        test = ordinary_all[ordinary_all["market_id"].isin(fold["testMarkets"])].copy()
        calibrator = _fit_predict_calibrator(shared, deps, calibration)
        calibrated = _apply_predict_calibrator(shared, deps, calibrator, test)
        y_test = test["label_up"].astype(int).to_numpy()
        metrics = shared._classification_metrics(deps, y_test, calibrated)
        fold_report["calibratedPredictBaseline"] = metrics
        model_metrics = fold_report.get("model", {})
        ll_lift = (
            float(metrics["logLoss"] - model_metrics["logLoss"])
            if metrics.get("logLoss") is not None and model_metrics.get("logLoss") is not None
            else None
        )
        brier_lift = (
            float(metrics["brier"] - model_metrics["brier"])
            if metrics.get("brier") is not None and model_metrics.get("brier") is not None
            else None
        )
        fold_report["logLossLiftVsCalibratedPredict"] = ll_lift
        fold_report["brierLiftVsCalibratedPredict"] = brier_lift
        if ll_lift is not None:
            fold_lifts_ll.append(ll_lift)
        if brier_lift is not None:
            fold_lifts_brier.append(brier_lift)
        for (_, row), probability in zip(test.iterrows(), calibrated):
            calibrated_by_key[(int(row["market_id"]), int(row["sampled_ms"]))] = float(probability)
        all_y.extend(int(value) for value in y_test.tolist())
        all_calibrated.extend(float(value) for value in calibrated.tolist())

    for row in score_rows:
        key = (int(row["market_id"]), int(row["sampled_ms"]))
        probability = calibrated_by_key.get(key)
        if probability is not None:
            row["calibrated_predict_up_probability"] = probability

    aggregate_calibrated = (
        shared._classification_metrics(
            deps,
            np.asarray(all_y, dtype=int),
            np.asarray(all_calibrated, dtype=float),
        )
        if all_y
        else {}
    )
    aggregate = report.setdefault("aggregateOOF", {})
    aggregate["calibratedPredictBaseline"] = aggregate_calibrated
    model_metrics = aggregate.get("model", {})
    aggregate["logLossLiftVsCalibratedPredict"] = (
        aggregate_calibrated.get("logLoss", 0) - model_metrics.get("logLoss", 0)
        if aggregate_calibrated and model_metrics else None
    )
    aggregate["brierLiftVsCalibratedPredict"] = (
        aggregate_calibrated.get("brier", 0) - model_metrics.get("brier", 0)
        if aggregate_calibrated and model_metrics else None
    )
    complete_scores = [
        row for row in score_rows
        if row.get("calibrated_predict_up_probability") is not None
    ]
    aggregate["marketBlocked"] = {
        "model": _blocked_metrics(
            complete_scores, label_key="label_up", probability_key="model_up_probability"
        ),
        "rawPredict": _blocked_metrics(
            complete_scores, label_key="label_up", probability_key="predict_up_mid"
        ),
        "calibratedPredict": _blocked_metrics(
            complete_scores,
            label_key="label_up",
            probability_key="calibrated_predict_up_probability",
        ),
    }
    report["foldLogLossLiftVsCalibratedPredict"] = v3._metric_summary(fold_lifts_ll)
    report["foldBrierLiftVsCalibratedPredict"] = v3._metric_summary(fold_lifts_brier)

    calibration_count = max(12, int(round(len(markets) * 0.15)))
    calibration_count = min(calibration_count, max(1, len(markets) - 30))
    final_cal_markets = markets[-calibration_count:]
    final_calibration = ordinary_fit[
        ordinary_fit["market_id"].isin(final_cal_markets)
    ].copy()
    _PREDICT_CALIBRATORS[tuple(features)] = _fit_predict_calibrator(
        shared, deps, final_calibration
    )
    return report, score_rows, final_model, final_calibrator


def _score_special_v31(
    *,
    shared: Any,
    deps: dict[str, Any],
    special: Any,
    features: list[str],
    model: Any,
    calibrator: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report, rows = _ORIGINAL_SPECIAL(
        shared=shared,
        deps=deps,
        special=special,
        features=features,
        model=model,
        calibrator=calibrator,
    )
    if not len(special):
        return report, rows
    pd = deps["pd"]
    y = special["label_up"].astype(int).to_numpy()
    predict_calibrator = _PREDICT_CALIBRATORS.get(tuple(features))
    calibrated_predict = _apply_predict_calibrator(
        shared, deps, predict_calibrator, special
    )
    raw_model = model.predict_proba(v3._numeric(pd, special, features))[:, 1]
    calibrated_metrics = shared._classification_metrics(deps, y, calibrated_predict)
    raw_model_metrics = shared._classification_metrics(deps, y, raw_model)
    report["calibratedPredictBaseline"] = calibrated_metrics
    report["modelRawUncalibrated"] = raw_model_metrics
    model_metrics = report.get("model", {})
    report["logLossLiftVsCalibratedPredict"] = (
        calibrated_metrics["logLoss"] - model_metrics["logLoss"]
    )
    report["brierLiftVsCalibratedPredict"] = (
        calibrated_metrics["brier"] - model_metrics["brier"]
    )
    for row, probability in zip(rows, calibrated_predict):
        row["calibrated_predict_up_probability"] = float(probability)
    report["marketBlocked"] = {
        "model": _blocked_metrics(rows, label_key="label_up", probability_key="model_up_probability"),
        "rawPredict": _blocked_metrics(rows, label_key="label_up", probability_key="predict_up_mid"),
        "calibratedPredict": _blocked_metrics(
            rows,
            label_key="label_up",
            probability_key="calibrated_predict_up_probability",
        ),
    }
    return report, rows


def _write_scores_v31(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    fields = [*v3.SCORE_FIELDS, "calibrated_predict_up_probability"]
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _repair_bucket_summary_v31(
    rows: list[dict[str, Any]],
    probability_key: str,
) -> dict[str, Any]:
    base = v3._repair_bucket_summary(rows, probability_key)
    for bucket, payload in base.items():
        subset = [
            row for row in rows
            if v3._prob_bucket(float(row[probability_key])) == bucket
        ]
        inventory = [float(row.get("maker_abs_delta") or 0.0) for row in subset]
        payload["meanMakerAbsDelta"] = (
            statistics.fmean(inventory) if inventory else None
        )
        payload["medianMakerAbsDelta"] = _median(inventory)
    return base


def _slice_repair_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model": _repair_bucket_summary_v31(rows, "model_heavy_probability"),
        "rawPredict": _repair_bucket_summary_v31(rows, "predict_heavy_probability"),
        "calibratedPredict": _repair_bucket_summary_v31(
            rows, "calibrated_predict_heavy_probability"
        ),
    }


def _repair_audit_exact_v31(
    risk_rows: list[dict[str, Any]],
    score_rows_by_set: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for feature_set, score_rows in score_rows_by_set.items():
        index = {
            (int(row["market_id"]), int(row["sampled_ms"])): row
            for row in score_rows
            if row.get("calibrated_predict_up_probability") is not None
        }
        scored_markets = {market_id for market_id, _sampled_ms in index}
        feature_result: dict[str, Any] = {}
        for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
            source = [
                row for row in risk_rows
                if row["regime"] == regime
                and row["lifecycle_state"] == "POST_FIRST_TAKER"
            ]
            source_scored = [
                row for row in source if int(row["market_id"]) in scored_markets
            ]
            joined: list[dict[str, Any]] = []
            for row in source_scored:
                score_row = index.get((int(row["market_id"]), int(row["sampled_ms"])))
                if score_row is None:
                    continue
                item = dict(row)
                item["model_heavy_probability"] = v3._heavy_probability(
                    float(score_row["model_up_probability"]),
                    str(row["maker_heavy_side"]),
                )
                item["calibrated_predict_heavy_probability"] = v3._heavy_probability(
                    float(score_row["calibrated_predict_up_probability"]),
                    str(row["maker_heavy_side"]),
                )
                item["score_sampled_ms"] = int(score_row["sampled_ms"])
                item["join_delta_ms"] = int(item["score_sampled_ms"]) - int(row["sampled_ms"])
                joined.append(item)

            y = [int(row["heavy_side_won"]) for row in joined]
            p_model = [float(row["model_heavy_probability"]) for row in joined]
            p_predict = [float(row["predict_heavy_probability"]) for row in joined]
            p_cal = [float(row["calibrated_predict_heavy_probability"]) for row in joined]
            calibration = {
                "modelBrier": v3._brier(y, p_model),
                "predictMidBrier": v3._brier(y, p_predict),
                "calibratedPredictBrier": v3._brier(y, p_cal),
                "modelLogLoss": v3._logloss(y, p_model),
                "predictMidLogLoss": v3._logloss(y, p_predict),
                "calibratedPredictLogLoss": v3._logloss(y, p_cal),
                "marketBlockedModel": _blocked_metrics(
                    joined,
                    label_key="heavy_side_won",
                    probability_key="model_heavy_probability",
                ),
                "marketBlockedPredictMid": _blocked_metrics(
                    joined,
                    label_key="heavy_side_won",
                    probability_key="predict_heavy_probability",
                ),
                "marketBlockedCalibratedPredict": _blocked_metrics(
                    joined,
                    label_key="heavy_side_won",
                    probability_key="calibrated_predict_heavy_probability",
                ),
            }
            by_phase = {
                phase: _slice_repair_summary(
                    [row for row in joined if str(row.get("phase") or "") == phase]
                )
                for phase in ("OPEN", "MID", "TAIL")
            }
            by_heavy_side = {
                side: _slice_repair_summary(
                    [row for row in joined if str(row["maker_heavy_side"]) == side]
                )
                for side in ("UP", "DOWN")
            }
            join_deltas = [int(row["join_delta_ms"]) for row in joined]
            feature_result[regime] = {
                "sourceRiskRows": len(source),
                "sourceRiskMarkets": len({int(row["market_id"]) for row in source}),
                "riskRowsInScoredMarkets": len(source_scored),
                "riskMarketsInScoredMarkets": len({int(row["market_id"]) for row in source_scored}),
                "exactJoinedRows": len(joined),
                "exactJoinCoverageWithinScoredMarkets": (
                    len(joined) / len(source_scored) if source_scored else None
                ),
                "missingExactRows": len(source_scored) - len(joined),
                "futureJoinCount": sum(delta > 0 for delta in join_deltas),
                "pastJoinCount": sum(delta < 0 for delta in join_deltas),
                "exactJoinCount": sum(delta == 0 for delta in join_deltas),
                "markets": len({int(row["market_id"]) for row in joined}),
                "heavyOutcomeCalibration": calibration,
                "repairByModelHeavyProbability": _repair_bucket_summary_v31(
                    joined, "model_heavy_probability"
                ),
                "repairByPredictHeavyProbability": _repair_bucket_summary_v31(
                    joined, "predict_heavy_probability"
                ),
                "repairByCalibratedPredictHeavyProbability": _repair_bucket_summary_v31(
                    joined, "calibrated_predict_heavy_probability"
                ),
                "postFirstRepairByPhase": by_phase,
                "postFirstRepairByMakerHeavySide": by_heavy_side,
            }
        result[feature_set] = feature_result
    return result


def _write_json_v31(path: Path, payload: dict[str, Any]) -> None:
    if isinstance(payload, dict) and payload.get("models") is not None and payload.get("repairAudit") is not None:
        payload["reportVersion"] = REPORT_VERSION
        payload["baseline"] = (
            "raw target-blind Predict midpoint plus fold-wise past-only calibrated Predict; "
            "model promotion comparisons use calibrated Predict as the probability-quality baseline"
        )
        payload["integrityAudit"] = _INTEGRITY
        guardrails = list(payload.get("interpretationGuardrails") or [])
        additions = [
            "Settlement coverage must pass separate ordinary/special guards before any V3.1 fitting begins.",
            "V2 risk-set winners and V3.1 settlement winners must agree 100% on overlapping markets before repair calibration is audited.",
            "Repair scores join by exact market_id + sampled_ms only; second-bucket and future joins are forbidden.",
            "For log-loss/Brier claims, compare EBM against fold-wise past-only calibrated Predict as well as raw Predict so calibration-only gains are not mislabeled feature alpha.",
            "Special raw-model and calibrated-model metrics are both reported; special base-rate shifts are not evidence of external-feature alpha by themselves.",
        ]
        for item in additions:
            if item not in guardrails:
                guardrails.append(item)
        payload["interpretationGuardrails"] = guardrails
    _ORIGINAL_WRITE_JSON(path, payload)


def _preflight_from_argv() -> None:
    global _INTEGRITY
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--public-dataset", type=Path, default=v3.DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--settlement-db", type=Path, default=v3.DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--risk-csv", type=Path, default=v3.DEFAULT_RISK_CSV)
    parser.add_argument("--special-start", default=v3.DEFAULT_SPECIAL_START)
    known, _ = parser.parse_known_args(sys.argv[1:])
    special_start_ms = v3.lifecycle._epoch_ms(known.special_start)
    cohort, _, _public_meta = v3.lifecycle._load_public_cohorts(
        known.public_dataset, special_start_ms=special_start_ms
    )
    settlements = v3._load_settlements(known.settlement_db)
    risk_rows = v3._load_risk_rows(known.risk_csv)
    coverage = _coverage_guard(cohort, settlements)
    concordance = _winner_concordance(risk_rows, settlements)
    _INTEGRITY = {
        "status": "OK" if coverage["passed"] and concordance["passed"] else "FAILED",
        "settlementCoverage": coverage,
        "winnerConcordance": concordance,
        "repairJoinPolicy": "exact market_id + sampled_ms; no second-bucket fallback; no future join",
        "probabilityBaselinePolicy": "raw Predict + fold-wise past-only calibrated Predict",
    }
    print("V3.1 INTEGRITY PREFLIGHT", flush=True)
    ordinary = coverage["ORDINARY_PRE_SPECIAL"]
    special = coverage["SPECIAL"]
    print(
        f"  settlement ordinary={ordinary['settledMarkets']}/{ordinary['requestedMarkets']} "
        f"({ordinary['coverage']:.1%}) special={special['settledMarkets']}/{special['requestedMarkets']} "
        f"({special['coverage']:.1%})",
        flush=True,
    )
    print(
        f"  winner concordance overlap={concordance['overlapMarkets']} "
        f"mismatch={concordance['mismatchedWinnerMarkets']} "
        f"riskConflicts={concordance['conflictingRiskWinnerMarkets']}",
        flush=True,
    )
    failures: list[str] = []
    if not coverage["passed"]:
        failures.append("settlement coverage guard failed")
    if not concordance["passed"]:
        failures.append("V2/V3 winner concordance guard failed")
    if failures:
        raise SystemExit(
            "V3.1 INTEGRITY FAILED: " + "; ".join(failures) + ". Refusing to train/audit."
        )


def main() -> int:
    _preflight_from_argv()
    v3._fit_walkforward = _fit_walkforward_v31
    v3._score_special = _score_special_v31
    v3._write_scores = _write_scores_v31
    v3._repair_audit = _repair_audit_exact_v31
    v3._write_json = _write_json_v31
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
