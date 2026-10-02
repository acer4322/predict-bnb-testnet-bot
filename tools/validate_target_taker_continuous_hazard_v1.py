from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import train_target_taker_direct_eligibility_special_regime_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RISKSET = ROOT / "data" / "research" / "target_taker_intramarket_riskset_v1.csv"
DEFAULT_EVENTS = ROOT / "data" / "research" / "target_taker_intramarket_sequence_v1_events.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_continuous_hazard_v1_report.json"
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_continuous_hazard_v1_scores.csv"
REPORT_VERSION = "TARGET_TAKER_CONTINUOUS_HAZARD_REPLAY_V1"
CALIBRATION_TOP_FRACTIONS = (0.05, 0.10, 0.20, 0.30)


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
        raise ValueError(f"time must include timezone offset or be epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _parse_horizons(value: str) -> list[int]:
    result: list[int] = []
    for raw in str(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        horizon = int(raw)
        if horizon not in (1, 2, 5):
            raise ValueError(f"unsupported horizon {horizon}; choose from 1,2,5")
        if horizon not in result:
            result.append(horizon)
    if not result:
        raise ValueError("at least one horizon is required")
    return result


def _duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes:d}m{secs:02d}s"
    return f"{secs:d}s"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(base._clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _load_inputs(dataset: Path, riskset: Path) -> tuple[Any, Any]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit('Research dependencies missing. Run: pip install -e ".[research]"') from exc

    dataset_path = dataset.expanduser().resolve()
    riskset_path = riskset.expanduser().resolve()
    if not dataset_path.exists():
        raise FileNotFoundError(dataset_path)
    if not riskset_path.exists():
        raise FileNotFoundError(
            f"risk-set file missing: {riskset_path}. Run tools/analyze_target_taker_intramarket_sequence_v1.py first."
        )

    frame = pd.read_csv(dataset_path)
    annotations = pd.read_csv(riskset_path)
    for table in (frame, annotations):
        table["market_id"] = pd.to_numeric(table["market_id"], errors="raise").astype(int)
        table["decision_sampled_at_ms"] = pd.to_numeric(
            table["decision_sampled_at_ms"], errors="raise"
        ).astype("int64")

    annotation_columns = [
        "market_id",
        "decision_sampled_at_ms",
        "phase",
        "macro_phase",
        "target_parent_count_before_decision",
        "position_state",
        "previous_target_side",
        "ms_since_previous_target_parent",
        "next_target_event_type",
        "ms_to_next_target_parent",
    ]
    annotations = annotations[annotation_columns].copy()
    merged = frame.merge(
        annotations,
        on=["market_id", "decision_sampled_at_ms"],
        how="left",
        validate="one_to_one",
    )
    if merged["position_state"].isna().any():
        missing = int(merged["position_state"].isna().sum())
        raise RuntimeError(f"risk-set join missed {missing} public rows; rebuild sequence/risk-set from the same dataset")
    merged.sort_values(["market_id", "decision_sampled_at_ms"], inplace=True)
    return merged, pd


def _load_events(path: Path, *, start_ms: int, end_ms: int | None) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"event sequence file missing: {resolved}. Run tools/analyze_target_taker_intramarket_sequence_v1.py first."
        )
    rows: list[dict[str, Any]] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            event_ms = int(raw["target_event_ms"])
            if event_ms < int(start_ms):
                continue
            if end_ms is not None and event_ms >= int(end_ms):
                continue
            row = dict(raw)
            row["market_id"] = int(raw["market_id"])
            row["target_event_ms"] = event_ms
            rows.append(row)
    rows.sort(key=lambda row: (row["market_id"], row["target_event_ms"], int(row.get("event_index") or 0)))
    return rows


def _thresholds_from_calibration(np: Any, probability: Any) -> list[dict[str, Any]]:
    p = np.asarray(probability, dtype=float)
    result: list[dict[str, Any]] = []
    for fraction in CALIBRATION_TOP_FRACTIONS:
        threshold = float(np.quantile(p, 1.0 - fraction))
        result.append(
            {
                "name": f"CAL_TOP_{int(round(fraction * 100)):02d}PCT",
                "calibrationTopFraction": float(fraction),
                "threshold": threshold,
            }
        )
    return result


def _row_gate_metrics(
    deps: dict[str, Any],
    *,
    frame: Any,
    label: str,
    probability: Any,
    threshold: float,
) -> dict[str, Any]:
    np = deps["np"]
    y = frame[label].astype(int).to_numpy()
    p = np.asarray(probability, dtype=float)
    selected = p >= float(threshold)
    selected_rows = int(selected.sum())
    positives = int(y.sum())
    tp = int(y[selected].sum()) if selected_rows else 0
    precision = tp / selected_rows if selected_rows else None
    recall = tp / positives if positives else None
    market_values = frame["market_id"].to_numpy(dtype=int)
    selected_market_counts: list[int] = []
    if selected_rows:
        _, counts = np.unique(market_values[selected], return_counts=True)
        selected_market_counts = [int(v) for v in counts.tolist()]
    return {
        "selectedRows": selected_rows,
        "selectedRate": selected_rows / len(frame) if len(frame) else None,
        "selectedMarkets": len(selected_market_counts),
        "avgSignalsPerSelectedMarket": (
            float(sum(selected_market_counts) / len(selected_market_counts))
            if selected_market_counts
            else None
        ),
        "medianSignalsPerSelectedMarket": (
            float(np.median(selected_market_counts)) if selected_market_counts else None
        ),
        "truePositiveRows": tp,
        "falsePositiveRows": selected_rows - tp,
        "precision": precision,
        "eventWindowRecall": recall,
        "basePositiveRate": positives / len(frame) if len(frame) else None,
        "precisionLiftVsBaseRate": (
            precision - positives / len(frame)
            if precision is not None and len(frame)
            else None
        ),
    }


def _subgroup_metrics(
    shared: Any,
    deps: dict[str, Any],
    *,
    frame: Any,
    probability: Any,
    label: str,
    group_column: str,
) -> dict[str, Any]:
    np = deps["np"]
    p = np.asarray(probability, dtype=float)
    result: dict[str, Any] = {}
    values = frame[group_column].fillna("UNKNOWN").astype(str).to_numpy()
    for value in sorted(set(values.tolist())):
        mask = values == value
        subset = frame.loc[mask]
        if len(subset) < 20:
            continue
        y = subset[label].astype(int)
        metrics = shared._classification_metrics(deps, y, p[mask])
        result[value] = metrics
    return result


def _score_index(frame: Any, probability: Any) -> dict[int, tuple[list[int], list[float]]]:
    grouped: dict[int, tuple[list[int], list[float]]] = {}
    temp = frame[["market_id", "decision_bucket_start_ms"]].copy()
    temp["score"] = probability
    for market_id, group in temp.groupby("market_id", sort=False):
        grouped[int(market_id)] = (
            [int(v) for v in group["decision_bucket_start_ms"].tolist()],
            [float(v) for v in group["score"].tolist()],
        )
    return grouped


def _event_capture(
    *,
    events: list[dict[str, Any]],
    score_index: dict[int, tuple[list[int], list[float]]],
    threshold: float,
    horizon_s: int,
    mode: str,
) -> dict[str, Any]:
    captured = 0
    eligible = 0
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_macro: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    lead_times: list[int] = []
    horizon_ms = int(horizon_s) * 1000

    first_signal_by_market: dict[int, int | None] = {}
    opening_signal_by_market: dict[int, int | None] = {}
    if mode in {"FIRST_SIGNAL_ONCE", "OPENING_ONLY"}:
        for market_id, (times, scores) in score_index.items():
            selected = [times[i] for i, score in enumerate(scores) if score >= threshold]
            first_signal_by_market[market_id] = selected[0] if selected else None
            opening_signal_by_market[market_id] = times[0] if times and scores[0] >= threshold else None

    for event in events:
        market_id = int(event["market_id"])
        event_ms = int(event["target_event_ms"])
        event_bucket = (event_ms // 1000) * 1000
        entry = score_index.get(market_id)
        if entry is None:
            continue
        eligible += 1
        event_type = str(event.get("event_type") or "UNKNOWN")
        macro = str(event.get("macro_phase") or "UNKNOWN")
        by_type[event_type][1] += 1
        by_macro[macro][1] += 1

        hit_time: int | None = None
        if mode == "CONTINUOUS":
            times, scores = entry
            left = bisect.bisect_left(times, event_bucket - horizon_ms)
            right = bisect.bisect_left(times, event_bucket)
            selected_times = [
                times[i] for i in range(left, right) if scores[i] >= float(threshold)
            ]
            if selected_times:
                hit_time = selected_times[-1]
        elif mode == "FIRST_SIGNAL_ONCE":
            candidate = first_signal_by_market.get(market_id)
            if candidate is not None and event_bucket - horizon_ms <= candidate < event_bucket:
                hit_time = candidate
        elif mode == "OPENING_ONLY":
            candidate = opening_signal_by_market.get(market_id)
            if candidate is not None and event_bucket - horizon_ms <= candidate < event_bucket:
                hit_time = candidate
        else:
            raise ValueError(f"unknown capture mode: {mode}")

        if hit_time is not None:
            captured += 1
            by_type[event_type][0] += 1
            by_macro[macro][0] += 1
            lead_times.append(event_bucket - hit_time)

    def summarize(groups: dict[str, list[int]]) -> dict[str, Any]:
        return {
            key: {
                "captured": values[0],
                "events": values[1],
                "captureRate": values[0] / values[1] if values[1] else None,
            }
            for key, values in sorted(groups.items())
        }

    return {
        "mode": mode,
        "capturedEvents": captured,
        "eligibleEvents": eligible,
        "captureRate": captured / eligible if eligible else None,
        "byEventType": summarize(by_type),
        "byMacroPhase": summarize(by_macro),
        "leadTimeMs": {
            "count": len(lead_times),
            "median": float(statistics.median(lead_times)) if lead_times else None,
            "mean": float(statistics.fmean(lead_times)) if lead_times else None,
        },
    }


def _write_scores(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys())
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate frozen16 as a continuous every-second Target Taker hazard gate, "
            "separating first-entry, same-side reentry, and side-flip capture by market phase."
        )
    )
    parser.add_argument("--dataset", type=Path, default=base.DEFAULT_OUTPUT)
    parser.add_argument("--riskset", type=Path, default=DEFAULT_RISKSET)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--test-end", default=None)
    parser.add_argument("--horizons", default="5,2")
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=600)
    parser.add_argument("--outer-bags", type=int, default=3)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--scores-csv", type=Path, default=DEFAULT_SCORES)
    args = parser.parse_args()

    start_ms = _epoch_ms(args.test_start)
    end_ms = _epoch_ms(args.test_end)
    if start_ms is None:
        raise SystemExit("--test-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--test-end must be after --test-start")
    try:
        horizons = _parse_horizons(args.horizons)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    # statistics is imported lazily here to keep the module header compact.
    global statistics
    import statistics

    print("TARGET_TAKER_CONTINUOUS_HAZARD_REPLAY_V1", flush=True)
    print("[1/4] Load public features + retrospective intramarket state...", flush=True)
    frame, pd = _load_inputs(args.dataset, args.riskset)
    test_mask = frame["decision_sampled_at_ms"] >= int(start_ms)
    if end_ms is not None:
        test_mask &= frame["decision_sampled_at_ms"] < int(end_ms)
    test_market_ids = sorted(int(v) for v in frame.loc[test_mask, "market_id"].unique().tolist())
    if not test_market_ids:
        raise SystemExit("no test markets found")

    historical = frame[
        (frame["decision_sampled_at_ms"] < int(start_ms))
        & (~frame["market_id"].isin(test_market_ids))
    ].copy()
    test = frame[test_mask & frame["market_id"].isin(test_market_ids)].copy()
    historical_markets = base._market_order(historical, "decision_sampled_at_ms")
    train_markets, calibration_markets = base._split_train_cal(historical_markets)
    train = historical[historical["market_id"].isin(train_markets)].copy()
    calibration = historical[historical["market_id"].isin(calibration_markets)].copy()
    features = base._usable(base.FROZEN16_FEATURES, frame, pd)
    if len(features) != len(base.FROZEN16_FEATURES):
        missing = sorted(set(base.FROZEN16_FEATURES) - set(features))
        raise RuntimeError(f"missing frozen16 features: {missing}")

    print(
        f"      train={len(train):,} rows/{len(train_markets)} markets | "
        f"cal={len(calibration):,}/{len(calibration_markets)} | "
        f"test={len(test):,}/{len(test_market_ids)}",
        flush=True,
    )
    events = _load_events(args.events, start_ms=int(start_ms), end_ms=end_ms)
    print(f"      test Target parents with sequence labels={len(events):,}", flush=True)

    shared = base._shared()
    deps = shared._imports()
    np = deps["np"]
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Compare opening-only, first-signal-once, and continuous every-second frozen16 "
            "hazard gating against actual Target parent timing."
        ),
        "testWindow": {"startMs": start_ms, "endMs": end_ms},
        "config": {
            "horizons": horizons,
            "features": features,
            "maxRounds": max(100, int(args.max_rounds)),
            "outerBags": max(2, int(args.outer_bags)),
            "interactions": max(0, int(args.interactions)),
            "thresholdRule": "quantile learned only from historical calibration markets",
            "calibrationTopFractions": list(CALIBRATION_TOP_FRACTIONS),
        },
        "rows": {
            "train": len(train),
            "calibration": len(calibration),
            "test": len(test),
            "trainMarkets": len(train_markets),
            "calibrationMarkets": len(calibration_markets),
            "testMarkets": len(test_market_ids),
            "testTargetParents": len(events),
        },
        "tasks": {},
    }
    _write_json(args.report, report)
    score_rows: dict[tuple[int, int], dict[str, Any]] = {}

    print("[2/4] Fit frozen16 hazard model(s)...", flush=True)
    started_all = time.perf_counter()
    for fit_index, horizon in enumerate(horizons, 1):
        label = f"label_next_target_taker_any_{horizon}s"
        print(f"      [FIT {fit_index}/{len(horizons)}] {horizon}s START", flush=True)
        fit_started = time.perf_counter()
        y_train = pd.to_numeric(train[label], errors="raise").astype(int)
        y_cal = pd.to_numeric(calibration[label], errors="raise").astype(int)
        y_test = pd.to_numeric(test[label], errors="raise").astype(int)
        model = shared._fit_classifier(
            deps,
            shared._numeric(pd, train, features),
            y_train,
            interactions=min(max(0, int(args.interactions)), len(features) // 2),
            max_rounds=max(100, int(args.max_rounds)),
            outer_bags=max(2, int(args.outer_bags)),
            seed=7600 + horizon,
        )
        raw_cal = model.predict_proba(shared._numeric(pd, calibration, features))[:, 1]
        raw_test = model.predict_proba(shared._numeric(pd, test, features))[:, 1]
        calibrator = shared._calibrate(deps, y_cal, raw_cal)
        cal_cal = shared._apply_calibration(deps, calibrator, raw_cal)
        cal_test = shared._apply_calibration(deps, calibrator, raw_test)

        prior = min(1 - 1e-7, max(1e-7, float(y_train.mean())))
        raw_metrics = shared._classification_metrics(deps, y_test, raw_test)
        calibrated_metrics = shared._classification_metrics(deps, y_test, cal_test)
        baseline = shared._classification_metrics(deps, y_test, np.full(len(y_test), prior))

        task: dict[str, Any] = {
            "label": label,
            "trainPositiveRate": float(y_train.mean()),
            "calibrationPositiveRate": float(y_cal.mean()),
            "testPositiveRate": float(y_test.mean()),
            "raw": {
                "test": raw_metrics,
                "logLossLiftVsPrior": float(baseline["logLoss"] - raw_metrics["logLoss"]),
                "byMacroPhase": _subgroup_metrics(
                    shared, deps, frame=test, probability=raw_test, label=label, group_column="macro_phase"
                ),
                "byPositionState": _subgroup_metrics(
                    shared, deps, frame=test, probability=raw_test, label=label, group_column="position_state"
                ),
            },
            "historicalCalibrated": {
                "test": calibrated_metrics,
                "logLossLiftVsPrior": float(baseline["logLoss"] - calibrated_metrics["logLoss"]),
                "byMacroPhase": _subgroup_metrics(
                    shared, deps, frame=test, probability=cal_test, label=label, group_column="macro_phase"
                ),
                "byPositionState": _subgroup_metrics(
                    shared, deps, frame=test, probability=cal_test, label=label, group_column="position_state"
                ),
            },
            "baseline": baseline,
            "gates": [],
            "termImportances": shared._term_summary(model),
        }

        raw_rules = _thresholds_from_calibration(np, raw_cal)
        calibrated_rules = _thresholds_from_calibration(np, cal_cal)
        variants = (
            ("RAW", raw_test, raw_rules),
            ("HIST_CALIBRATED", cal_test, calibrated_rules),
        )
        print(f"      [FIT {fit_index}/{len(horizons)}] {horizon}s scoring continuous gates...", flush=True)
        for variant_name, probability, rules in variants:
            score_index = _score_index(test, probability)
            for rule in rules:
                threshold = float(rule["threshold"])
                gate = {
                    "variant": variant_name,
                    **rule,
                    "rowMetrics": _row_gate_metrics(
                        deps,
                        frame=test,
                        label=label,
                        probability=probability,
                        threshold=threshold,
                    ),
                    "eventCapture": {
                        mode: _event_capture(
                            events=events,
                            score_index=score_index,
                            threshold=threshold,
                            horizon_s=horizon,
                            mode=mode,
                        )
                        for mode in ("OPENING_ONLY", "FIRST_SIGNAL_ONCE", "CONTINUOUS")
                    },
                }
                task["gates"].append(gate)

        report["tasks"][f"hazard_{horizon}s"] = task
        _write_json(args.report, report)
        for row_index, raw in enumerate(test.itertuples(index=False)):
            key = (int(raw.market_id), int(raw.decision_sampled_at_ms))
            target = score_rows.setdefault(
                key,
                {
                    "market_id": key[0],
                    "decision_sampled_at_ms": key[1],
                    "seconds_left": _number(raw.seconds_left),
                    "macro_phase": raw.macro_phase,
                    "position_state": raw.position_state,
                    "target_parent_count_before_decision": int(raw.target_parent_count_before_decision),
                    "next_target_event_type": raw.next_target_event_type,
                },
            )
            target[f"raw_score_{horizon}s"] = float(raw_test[row_index])
            target[f"calibrated_score_{horizon}s"] = float(cal_test[row_index])
            target[f"label_{horizon}s"] = int(y_test.iloc[row_index])

        elapsed = time.perf_counter() - fit_started
        print(
            f"      [FIT {fit_index}/{len(horizons)}] {horizon}s DONE in {_duration(elapsed)} | "
            f"raw AUC={raw_metrics.get('rocAuc'):.4f} AP={raw_metrics.get('averagePrecision'):.4f} | "
            f"cal logloss={calibrated_metrics.get('logLoss'):.4f}",
            flush=True,
        )

    print("[3/4] Write per-second continuous scores...", flush=True)
    ordered_scores = [score_rows[key] for key in sorted(score_rows)]
    _write_scores(args.scores_csv, ordered_scores)

    print("[4/4] Finalize replay report...", flush=True)
    elapsed_all = time.perf_counter() - started_all
    report["elapsedSeconds"] = elapsed_all
    report["outputs"] = {
        "report": str(args.report.expanduser().resolve()),
        "scoresCsv": str(args.scores_csv.expanduser().resolve()),
    }
    report["interpretationGuide"] = {
        "OPENING_ONLY": "Only the first public second can trigger; approximates the current open-once behavior.",
        "FIRST_SIGNAL_ONCE": "Scan every second but stop after the first threshold crossing in a market.",
        "CONTINUOUS": "Every second remains eligible to fire; this is the research precursor to first-entry + reentry state-machine replay.",
        "warning": (
            "Continuous event capture is research timing overlap, not PnL. Repeated signals must not be sent as live orders "
            "until PUBLIC_ACTOR reentry and sizing/risk controls are validated."
        ),
    }
    _write_json(args.report, report)

    print(f"CONTINUOUS HAZARD REPLAY COMPLETE in {_duration(elapsed_all)}", flush=True)
    for horizon in horizons:
        task = report["tasks"][f"hazard_{horizon}s"]
        print(
            f"  {horizon}s raw AUC={task['raw']['test'].get('rocAuc'):.4f} "
            f"AP={task['raw']['test'].get('averagePrecision'):.4f}",
            flush=True,
        )
        for gate in task["gates"]:
            if gate["variant"] == "RAW" and gate["name"] == "CAL_TOP_10PCT":
                captures = gate["eventCapture"]
                print(
                    f"    RAW CAL_TOP_10PCT capture: opening={captures['OPENING_ONLY']['captureRate']} "
                    f"first-once={captures['FIRST_SIGNAL_ONCE']['captureRate']} "
                    f"continuous={captures['CONTINUOUS']['captureRate']}",
                    flush=True,
                )
    print(f"report: {args.report.expanduser().resolve()}", flush=True)
    print(f"scores: {args.scores_csv.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
