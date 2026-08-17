from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import train_target_taker_post_first_idle_burst_hazard_fast_v1 as v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_fast_v2_report.json"
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_fast_v2_scores.csv"
REPORT_VERSION = "TARGET_TAKER_POST_FIRST_IDLE_BURST_HAZARD_FAST_V2_SPECIAL_CAL"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fast frozen16 EBM for Target post-first idle action-burst onset hazard. "
            "Experiment B calibrates only on held-out early-special markets to avoid old-regime prevalence drag."
        )
    )
    parser.add_argument("--dataset", type=Path, default=v1.DEFAULT_DATASET)
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

    start_ms = v1._epoch_ms(args.special_start)
    end_ms = v1._epoch_ms(args.special_end)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--special-end must be after --special-start")
    try:
        horizons = v1._parse_horizons(args.horizons)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    base = v1.base
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
    frame["cap2_risk_post_first_idle"] = pd.to_numeric(frame["cap2_risk_post_first_idle"], errors="raise").astype(int)
    frame["cap3_risk_post_first_idle"] = pd.to_numeric(frame["cap3_risk_post_first_idle"], errors="raise").astype(int)

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

    historical_markets = v1._market_order(historical)
    special_markets = v1._market_order(special)
    hist_train, hist_cal = v1._split_hist(historical_markets)

    fraction = min(0.8, max(0.2, float(args.special_train_fraction)))
    cut = max(8, min(len(special_markets) - 4, int(round(len(special_markets) * fraction))))
    special_early = special_markets[:cut]
    special_late = special_markets[cut:]
    early_train, early_cal = v1._split_special_early(special_early)
    if len(early_cal) < 6:
        raise SystemExit(
            f"need >=6 held-out early-special calibration markets for Experiment B; got {len(early_cal)}"
        )

    # A: pre-special model/calibration -> full special holdout.
    # B: all historical markets + early-special training markets -> late special,
    #    calibrated ONLY on held-out early-special markets. This avoids dragging the
    #    calibration mapping toward the historical 23.7% 5s base rate when special
    #    post-first-idle risk is around 41%.
    retrain_train = historical_markets + early_train
    retrain_cal = early_cal

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
            "Target post-first/idle state defines the historical risk set only and is not an EBM feature. "
            "Live imitation must map it to the strategy's own post-entry actor state and execution cooldown."
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
            "topFractions": list(v1.TOP_FRACTIONS),
            "experimentBCalibrationPolicy": "held-out early-special markets only",
        },
        "rows": {
            "risk": int(len(risk)),
            "historical": int(len(historical)),
            "special": int(len(special)),
            "historicalMarkets": len(historical_markets),
            "specialMarkets": len(special_markets),
            "specialEarlyMarkets": len(special_early),
            "specialLateMarkets": len(special_late),
            "historicalTrainMarketsA": len(hist_train),
            "historicalCalibrationMarketsA": len(hist_cal),
            "experimentBTrainHistoricalMarkets": len(historical_markets),
            "experimentBEarlySpecialTrainMarkets": len(early_train),
            "experimentBEarlySpecialCalibrationMarkets": len(early_cal),
        },
        "experiments": {},
        "progress": {"completedFits": 0, "totalFits": total_fits, "percent": 0.0},
    }
    report_path = args.report.expanduser().resolve()
    v1._write_json(report_path, report)

    print(REPORT_VERSION, flush=True)
    print(
        f"risk rows: historical={len(historical):,} special={len(special):,} | "
        f"markets: historical={len(historical_markets)} special={len(special_markets)}",
        flush=True,
    )
    print(
        f"special split: early={len(special_early)} (train={len(early_train)} cal={len(early_cal)}) "
        f"late={len(special_late)}",
        flush=True,
    )
    print(
        "Experiment B calibration: EARLY-SPECIAL ONLY; historical calibration rows are not mixed into the mapping.",
        flush=True,
    )
    print(f"planned fits: {total_fits} | frozen16 only | cap2 primary + cap3 audit", flush=True)

    completed = 0
    started = time.perf_counter()
    fit_durations: list[float] = []
    score_rows: list[dict[str, Any]] = []

    for horizon in horizons:
        experiments = [
            ("A_history_to_full_special", hist_train, hist_cal, special_markets, 7100 + horizon),
            (
                "B_history_plus_early_special_to_late_special_special_cal",
                retrain_train,
                retrain_cal,
                special_late,
                8100 + horizon,
            ),
        ]
        for name, train_markets, cal_markets, test_markets, seed in experiments:
            number = completed + 1
            print(f"[FIT {number:02d}/{total_fits:02d}] START {horizon}s | {name}", flush=True)
            fit_started = time.perf_counter()
            result, rows = v1._fit_eval(
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
            v1._write_json(report_path, report)

            calibrated = result.get("calibrated", {})
            raw = result.get("raw", {})
            phase = result.get("phaseMetrics", {})
            print(
                f"[FIT {completed:02d}/{total_fits:02d}] DONE  {horizon}s | {name} | "
                f"fit={v1._duration(fit_seconds)} elapsed={v1._duration(elapsed)} ETA~{v1._duration(eta)} | "
                f"RAW_AUC={v1._metric(raw.get('rocAuc'))} CAL_AUC={v1._metric(calibrated.get('rocAuc'))} "
                f"AP={v1._metric(calibrated.get('averagePrecision'))} "
                f"CAL_lift={v1._metric(result.get('calibratedLogLossLiftVsPrior'))} | "
                f"TAIL_AUC={v1._metric((phase.get('TAIL') or {}).get('calibrated', {}).get('rocAuc'))}",
                flush=True,
            )

    report["progress"]["estimatedRemainingSeconds"] = 0.0
    report["progress"]["percent"] = 100.0
    v1._write_json(report_path, report)
    v1._write_scores(args.scores, score_rows)

    elapsed = time.perf_counter() - started
    print(f"\nPOST-FIRST IDLE BURST HAZARD V2 COMPLETE: {completed}/{total_fits} fits in {v1._duration(elapsed)}", flush=True)
    print(f"report: {report_path}", flush=True)
    print(f"scores: {args.scores.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
