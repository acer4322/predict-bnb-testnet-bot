from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import train_target_taker_direct_eligibility_special_regime_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_fast_v1_report.json"
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_fast_v1_scores.csv"
REPORT_VERSION = "TARGET_TAKER_POST_FIRST_IDLE_BURST_HAZARD_FAST_V1"
FEATURE_SET = "frozen16"
TOP_FRACTIONS = (0.05, 0.10, 0.20, 0.30)


def _epoch_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"time must include timezone offset or epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _parse_horizons(value: str) -> list[int]:
    result: list[int] = []
    for raw in str(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        horizon = int(raw)
        if horizon not in (2, 5):
            raise ValueError(f"unsupported horizon {horizon}; choose 2 or 5")
        if horizon not in result:
            result.append(horizon)
    if not result:
        raise ValueError("at least one horizon is required")
    return result


def _duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "?"
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _metric(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.5f}"
    except (TypeError, ValueError):
        return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(base._clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _market_order(frame: Any) -> list[int]:
    rows = (
        frame.groupby("market_id", as_index=False)["decision_sampled_at_ms"]
        .min()
        .sort_values(["decision_sampled_at_ms", "market_id"])
    )
    return [int(v) for v in rows["market_id"].tolist()]


def _split_hist(markets: list[int]) -> tuple[list[int], list[int]]:
    if len(markets) < 16:
        raise ValueError(f"need >=16 historical markets, got {len(markets)}")
    cal_count = max(8, int(round(len(markets) * 0.15)))
    cal_count = min(cal_count, len(markets) - 8)
    return markets[:-cal_count], markets[-cal_count:]


def _split_special_early(markets: list[int]) -> tuple[list[int], list[int]]:
    if len(markets) < 12:
        return markets, []
    cal_count = max(6, int(round(len(markets) * 0.25)))
    cal_count = min(cal_count, len(markets) - 8)
    return markets[:-cal_count], markets[-cal_count:]


def _safe_metrics(shared: Any, deps: dict[str, Any], y: Any, probability: Any) -> dict[str, Any]:
    if len(y) == 0:
        return {"rows": 0}
    result = shared._classification_metrics(deps, y, probability)
    result["rows"] = int(len(y))
    result["positiveRate"] = float(y.mean())
    return result


def _phase_metrics(shared: Any, deps: dict[str, Any], test: Any, y: Any, raw: Any, calibrated: Any) -> dict[str, Any]:
    np = deps["np"]
    result: dict[str, Any] = {}
    phases = test["macro_phase"].astype(str).to_numpy()
    for phase in ("OPEN", "MID", "TAIL"):
        mask = phases == phase
        idx = np.flatnonzero(mask)
        if len(idx) == 0:
            result[phase] = {"rows": 0}
            continue
        y_phase = y.iloc[idx]
        result[phase] = {
            "rows": int(len(idx)),
            "positiveRate": float(y_phase.mean()),
            "raw": _safe_metrics(shared, deps, y_phase, raw[idx]),
            "calibrated": _safe_metrics(shared, deps, y_phase, calibrated[idx]),
        }
    return result


def _unique_onset_keys(frame: Any, y: Any, horizon: int, prefix: str = "cap2") -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    label = f"{prefix}_label_next_burst_{horizon}s"
    delta_col = f"{prefix}_next_burst_delta_ms"
    for pos, (_, row) in enumerate(frame.iterrows()):
        if int(y.iloc[pos]) != 1:
            continue
        try:
            delta = int(float(row[delta_col]))
        except (TypeError, ValueError):
            continue
        if int(row[label]) != 1:
            continue
        onset = int(row["decision_sampled_at_ms"]) + delta
        keys.add((int(row["market_id"]), onset))
    return keys


def _gate_eval(
    *,
    deps: dict[str, Any],
    test: Any,
    y: Any,
    probability: Any,
    threshold: float,
    horizon: int,
    prefix: str,
) -> dict[str, Any]:
    np = deps["np"]
    selected = probability >= float(threshold)
    positives = y.to_numpy(dtype=int) == 1
    selected_count = int(selected.sum())
    selected_positive = int((selected & positives).sum())
    all_positive = int(positives.sum())

    eligible_onsets = _unique_onset_keys(test, y, horizon, prefix=prefix)
    captured_onsets: set[tuple[int, int]] = set()
    delta_col = f"{prefix}_next_burst_delta_ms"
    labels = y.to_numpy(dtype=int)
    for i, (_, row) in enumerate(test.iterrows()):
        if not bool(selected[i]) or labels[i] != 1:
            continue
        try:
            delta = int(float(row[delta_col]))
        except (TypeError, ValueError):
            continue
        captured_onsets.add((int(row["market_id"]), int(row["decision_sampled_at_ms"]) + delta))

    by_phase: dict[str, Any] = {}
    phase_values = test["macro_phase"].astype(str).to_numpy()
    for phase in ("OPEN", "MID", "TAIL"):
        mask = phase_values == phase
        phase_selected = selected & mask
        phase_positive = positives & mask
        p_selected = int(phase_selected.sum())
        p_selected_positive = int((phase_selected & positives).sum())
        by_phase[phase] = {
            "rows": int(mask.sum()),
            "selectedRows": p_selected,
            "selectedRate": p_selected / int(mask.sum()) if int(mask.sum()) else None,
            "precision": p_selected_positive / p_selected if p_selected else None,
            "positiveRowRecall": p_selected_positive / int(phase_positive.sum()) if int(phase_positive.sum()) else None,
        }

    return {
        "threshold": float(threshold),
        "selectedRows": selected_count,
        "selectedRate": selected_count / len(test) if len(test) else None,
        "precision": selected_positive / selected_count if selected_count else None,
        "positiveRowRecall": selected_positive / all_positive if all_positive else None,
        "eligibleUniqueBursts": len(eligible_onsets),
        "capturedUniqueBursts": len(captured_onsets & eligible_onsets),
        "uniqueBurstCaptureRate": (
            len(captured_onsets & eligible_onsets) / len(eligible_onsets) if eligible_onsets else None
        ),
        "byMacroPhase": by_phase,
    }


def _thresholds(np: Any, calibration_probability: Any) -> list[dict[str, Any]]:
    if len(calibration_probability) == 0:
        return []
    result: list[dict[str, Any]] = []
    for fraction in TOP_FRACTIONS:
        threshold = float(np.quantile(calibration_probability, 1.0 - fraction))
        result.append({"name": f"CAL_TOP_{int(round(fraction * 100)):02d}PCT", "fraction": fraction, "threshold": threshold})
    return result


def _fit_eval(
    *,
    shared: Any,
    deps: dict[str, Any],
    frame: Any,
    features: list[str],
    horizon: int,
    train_markets: list[int],
    calibration_markets: list[int],
    test_markets: list[int],
    interactions: int,
    max_rounds: int,
    outer_bags: int,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pd = deps["pd"]
    np = deps["np"]
    label = f"cap2_label_next_burst_{horizon}s"
    audit_label = f"cap3_label_next_burst_{horizon}s"

    train = frame[frame["market_id"].isin(train_markets)].copy()
    calibration = frame[frame["market_id"].isin(calibration_markets)].copy()
    test = frame[frame["market_id"].isin(test_markets)].copy()
    if len(train) < 100 or len(calibration) < 50 or len(test) < 50:
        return ({
            "status": "INSUFFICIENT_DATA",
            "trainRows": int(len(train)),
            "calibrationRows": int(len(calibration)),
            "testRows": int(len(test)),
        }, [])

    y_train = pd.to_numeric(train[label], errors="raise").astype(int)
    y_cal = pd.to_numeric(calibration[label], errors="raise").astype(int)
    y_test = pd.to_numeric(test[label], errors="raise").astype(int)
    if len(set(int(v) for v in y_train.unique())) < 2 or len(set(int(v) for v in y_cal.unique())) < 2:
        return ({
            "status": "INSUFFICIENT_CLASSES",
            "trainRows": int(len(train)),
            "calibrationRows": int(len(calibration)),
            "testRows": int(len(test)),
        }, [])

    model = shared._fit_classifier(
        deps,
        shared._numeric(pd, train, features),
        y_train,
        interactions=min(max(0, interactions), max(0, len(features) // 2)),
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )
    raw_cal = model.predict_proba(shared._numeric(pd, calibration, features))[:, 1]
    calibrator = shared._calibrate(deps, y_cal, raw_cal)
    cal_cal = shared._apply_calibration(deps, calibrator, raw_cal)

    raw_test = model.predict_proba(shared._numeric(pd, test, features))[:, 1]
    calibrated_test = shared._apply_calibration(deps, calibrator, raw_test)

    raw_metrics = _safe_metrics(shared, deps, y_test, raw_test)
    calibrated_metrics = _safe_metrics(shared, deps, y_test, calibrated_test)
    prior = min(1 - 1e-7, max(1e-7, float(y_train.mean())))
    baseline = shared._classification_metrics(deps, y_test, np.full(len(y_test), prior))

    gate_rows: list[dict[str, Any]] = []
    gate_report: list[dict[str, Any]] = []
    for rule in _thresholds(np, cal_cal):
        gate = _gate_eval(
            deps=deps,
            test=test,
            y=y_test,
            probability=calibrated_test,
            threshold=float(rule["threshold"]),
            horizon=horizon,
            prefix="cap2",
        )
        gate_report.append({**rule, **gate})

    cap3_y = pd.to_numeric(test[audit_label], errors="raise").astype(int)
    cap3_metrics = _safe_metrics(shared, deps, cap3_y, calibrated_test)
    cap3_gate_report: list[dict[str, Any]] = []
    for rule in _thresholds(np, cal_cal):
        cap3_gate_report.append({
            **rule,
            **_gate_eval(
                deps=deps,
                test=test,
                y=cap3_y,
                probability=calibrated_test,
                threshold=float(rule["threshold"]),
                horizon=horizon,
                prefix="cap3",
            ),
        })

    for i, (_, row) in enumerate(test.iterrows()):
        gate_rows.append({
            "experiment": "",
            "horizon_seconds": horizon,
            "market_id": int(row["market_id"]),
            "decision_sampled_at_ms": int(row["decision_sampled_at_ms"]),
            "seconds_left": row.get("seconds_left"),
            "macro_phase": str(row.get("macro_phase") or "UNKNOWN"),
            "cap2_label": int(y_test.iloc[i]),
            "cap3_label": int(cap3_y.iloc[i]),
            "raw_probability": float(raw_test[i]),
            "calibrated_probability": float(calibrated_test[i]),
        })

    return ({
        "status": "OK",
        "features": features,
        "trainRows": int(len(train)),
        "calibrationRows": int(len(calibration)),
        "testRows": int(len(test)),
        "trainMarkets": len(train_markets),
        "calibrationMarkets": len(calibration_markets),
        "testMarkets": len(test_markets),
        "trainPositiveRate": float(y_train.mean()),
        "calibrationPositiveRate": float(y_cal.mean()),
        "testPositiveRate": float(y_test.mean()),
        "raw": raw_metrics,
        "calibrated": calibrated_metrics,
        "baseline": baseline,
        "rawLogLossLiftVsPrior": float(baseline["logLoss"] - raw_metrics["logLoss"]),
        "calibratedLogLossLiftVsPrior": float(baseline["logLoss"] - calibrated_metrics["logLoss"]),
        "phaseMetrics": _phase_metrics(shared, deps, test, y_test, raw_test, calibrated_test),
        "calibrationTopGates": gate_report,
        "cap3Audit": {
            "positiveRate": float(cap3_y.mean()),
            "calibrated": cap3_metrics,
            "calibrationTopGates": cap3_gate_report,
        },
        "termImportances": shared._term_summary(model),
    }, gate_rows)


def _write_scores(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "experiment", "horizon_seconds", "market_id", "decision_sampled_at_ms", "seconds_left",
        "macro_phase", "cap2_label", "cap3_label", "raw_probability", "calibrated_probability",
    ]
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fast frozen16 EBM for Target post-first idle action-burst onset hazard."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--special-start", required=True)
    parser.add_argument("--special-end", default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--horizons", default="5,2")
    parser.add_argument("--special-train-fraction", type=float, default=0.50)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=600)
    parser.add_argument("--outer-bags", type=int, default=3)
    args = parser.parse_args()

    start_ms = _epoch_ms(args.special_start)
    end_ms = _epoch_ms(args.special_end)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--special-end must be after --special-start")
    try:
        horizons = _parse_horizons(args.horizons)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    shared = base._shared()
    deps = shared._imports()
    pd = deps["pd"]

    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise SystemExit(f"dataset missing: {dataset}")
    frame = pd.read_csv(dataset)
    required = {
        "market_id", "decision_sampled_at_ms", "macro_phase", "cap2_risk_post_first_idle",
        "cap3_risk_post_first_idle", "cap2_label_next_burst_2s", "cap2_label_next_burst_5s",
        "cap3_label_next_burst_2s", "cap3_label_next_burst_5s",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("dataset missing columns: " + ", ".join(missing))

    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["decision_sampled_at_ms"] = pd.to_numeric(frame["decision_sampled_at_ms"], errors="raise").astype("int64")
    for col in ("cap2_risk_post_first_idle", "cap3_risk_post_first_idle"):
        frame[col] = pd.to_numeric(frame[col], errors="raise").astype(int)

    # Training target is explicitly conditional on Target having entered before and currently being idle.
    # This state is NOT included as an EBM feature; live imitation maps it to the strategy's own actor state.
    risk = frame[frame["cap2_risk_post_first_idle"] == 1].copy()

    special_mask = risk["decision_sampled_at_ms"] >= int(start_ms)
    if end_ms is not None:
        special_mask &= risk["decision_sampled_at_ms"] < int(end_ms)
    special_market_ids = sorted(int(v) for v in risk.loc[special_mask, "market_id"].unique().tolist())
    if len(special_market_ids) < 12:
        raise SystemExit(f"need >=12 special post-first-idle markets, got {len(special_market_ids)}")

    historical = risk[
        (risk["decision_sampled_at_ms"] < int(start_ms))
        & (~risk["market_id"].isin(special_market_ids))
    ].copy()
    special = risk[risk["market_id"].isin(special_market_ids) & special_mask].copy()
    analysis = pd.concat([historical, special], ignore_index=True)

    historical_markets = _market_order(historical)
    special_markets = _market_order(special)
    hist_train, hist_cal = _split_hist(historical_markets)

    fraction = min(0.8, max(0.2, float(args.special_train_fraction)))
    cut = max(8, min(len(special_markets) - 4, int(round(len(special_markets) * fraction))))
    special_early = special_markets[:cut]
    special_late = special_markets[cut:]
    early_train, early_cal = _split_special_early(special_early)
    retrain_train = hist_train + early_train
    # Blended calibration avoids the previous three-special-market calibration failure.
    retrain_cal = hist_cal + early_cal

    features = base._usable(base.FROZEN16_FEATURES, risk, pd)
    if len(features) != len(base.FROZEN16_FEATURES):
        missing_features = [f for f in base.FROZEN16_FEATURES if f not in features]
        raise SystemExit("missing frozen16 features: " + ", ".join(missing_features))

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    total_fits = len(horizons) * 2

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "dataset": str(dataset),
        "riskSet": "cap2_risk_post_first_idle == 1",
        "liveStateMapping": (
            "Target post-first/idle state is used only to define the historical risk set, not as an EBM feature. "
            "A live imitation strategy must map this to its own actor state after its first entry and outside an execution cooldown."
        ),
        "primaryLabel": "cap2 bounded action-burst onset",
        "auditLabel": "cap3 bounded action-burst onset scored by the same cap2-trained model",
        "specialWindow": {"startMs": start_ms, "endMs": end_ms},
        "features": features,
        "config": {
            "horizons": horizons,
            "plannedFits": total_fits,
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
            "specialTrainFraction": fraction,
            "topFractions": list(TOP_FRACTIONS),
        },
        "rows": {
            "risk": int(len(risk)),
            "historical": int(len(historical)),
            "special": int(len(special)),
            "historicalMarkets": len(historical_markets),
            "specialMarkets": len(special_markets),
            "specialEarlyMarkets": len(special_early),
            "specialLateMarkets": len(special_late),
            "historicalTrainMarkets": len(hist_train),
            "historicalCalibrationMarkets": len(hist_cal),
            "earlySpecialTrainMarkets": len(early_train),
            "earlySpecialCalibrationMarkets": len(early_cal),
        },
        "experiments": {},
        "progress": {"completedFits": 0, "totalFits": total_fits, "percent": 0.0},
    }
    report_path = args.report.expanduser().resolve()
    _write_json(report_path, report)

    print(REPORT_VERSION, flush=True)
    print(
        f"risk rows: historical={len(historical):,} special={len(special):,} | "
        f"markets: historical={len(historical_markets)} special={len(special_markets)}",
        flush=True,
    )
    print(
        f"special split: early={len(special_early)} (train={len(early_train)} cal={len(early_cal)}) "
        f"late={len(special_late)} | historical cal markets={len(hist_cal)}",
        flush=True,
    )
    print(f"planned fits: {total_fits} | frozen16 only | cap2 primary + cap3 audit", flush=True)

    completed = 0
    started = time.perf_counter()
    fit_durations: list[float] = []
    score_rows: list[dict[str, Any]] = []

    for horizon in horizons:
        experiments = [
            (
                "A_history_to_full_special",
                hist_train,
                hist_cal,
                special_markets,
                5100 + horizon,
            ),
            (
                "B_history_plus_early_special_to_late_special",
                retrain_train,
                retrain_cal,
                special_late,
                6100 + horizon,
            ),
        ]
        for name, train_markets, cal_markets, test_markets, seed in experiments:
            number = completed + 1
            print(f"[FIT {number:02d}/{total_fits:02d}] START {horizon}s | {name}", flush=True)
            fit_started = time.perf_counter()
            result, rows = _fit_eval(
                shared=shared,
                deps=deps,
                frame=analysis,
                features=features,
                horizon=horizon,
                train_markets=train_markets,
                calibration_markets=cal_markets,
                test_markets=test_markets,
                interactions=interactions,
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=seed,
            )
            fit_seconds = time.perf_counter() - fit_started
            fit_durations.append(fit_seconds)
            completed += 1
            for row in rows:
                row["experiment"] = name
            score_rows.extend(rows)

            report["experiments"].setdefault(f"hazard_{horizon}s", {})[name] = result
            elapsed = time.perf_counter() - started
            avg = sum(fit_durations) / len(fit_durations)
            eta = avg * (total_fits - completed)
            report["progress"] = {
                "completedFits": completed,
                "totalFits": total_fits,
                "percent": completed / total_fits * 100.0,
                "elapsedSeconds": elapsed,
                "estimatedRemainingSeconds": eta,
                "lastFitSeconds": fit_seconds,
                "lastFit": {"horizon": horizon, "experiment": name, "status": result.get("status")},
            }
            _write_json(report_path, report)

            calibrated = result.get("calibrated", {})
            phase = result.get("phaseMetrics", {})
            print(
                f"[FIT {completed:02d}/{total_fits:02d}] DONE  {horizon}s | {name} | "
                f"fit={_duration(fit_seconds)} elapsed={_duration(elapsed)} ETA~{_duration(eta)} | "
                f"AUC={_metric(calibrated.get('rocAuc'))} AP={_metric(calibrated.get('averagePrecision'))} "
                f"lift={_metric(result.get('calibratedLogLossLiftVsPrior'))} | "
                f"TAIL_AUC={_metric((phase.get('TAIL') or {}).get('calibrated', {}).get('rocAuc'))}",
                flush=True,
            )

    report["progress"]["estimatedRemainingSeconds"] = 0.0
    report["progress"]["percent"] = 100.0
    _write_json(report_path, report)
    _write_scores(args.scores, score_rows)

    elapsed = time.perf_counter() - started
    print(f"\nPOST-FIRST IDLE BURST HAZARD COMPLETE: {completed}/{total_fits} fits in {_duration(elapsed)}", flush=True)
    print(f"report: {report_path}", flush=True)
    print(f"scores: {args.scores.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
