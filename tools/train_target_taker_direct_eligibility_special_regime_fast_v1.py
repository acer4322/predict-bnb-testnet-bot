from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import train_target_taker_direct_eligibility_special_regime_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = (
    ROOT
    / "data"
    / "research"
    / "target_taker_direct_eligibility_special_regime_v1_fast_discovery_report.json"
)
REPORT_VERSION = "TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1_FAST_DISCOVERY"


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


def _duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "?"
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes:d}m{secs:02d}s"
    return f"{secs:d}s"


def _metric(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.5f}"
    except (TypeError, ValueError):
        return str(value)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(
        json.dumps(base._clean(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(resolved)


class Progress:
    def __init__(self, total: int, report_path: Path, report: dict[str, Any]) -> None:
        self.total = max(1, int(total))
        self.completed = 0
        self.started = time.perf_counter()
        self.fit_durations: list[float] = []
        self.report_path = report_path
        self.report = report

    def run(
        self,
        *,
        horizon: int,
        feature_set: str,
        experiment: str,
        fn: Any,
    ) -> dict[str, Any]:
        number = self.completed + 1
        pct_before = (self.completed / self.total) * 100.0
        print(
            f"[FIT {number:02d}/{self.total:02d} | {pct_before:5.1f}%] START "
            f"{horizon}s | {feature_set} | {experiment}",
            flush=True,
        )
        fit_started = time.perf_counter()
        result = fn()
        fit_seconds = time.perf_counter() - fit_started
        self.completed += 1
        self.fit_durations.append(fit_seconds)

        elapsed = time.perf_counter() - self.started
        avg = sum(self.fit_durations) / len(self.fit_durations)
        remaining = max(0, self.total - self.completed)
        eta = avg * remaining
        pct_after = (self.completed / self.total) * 100.0

        test = result.get("test", {}) if isinstance(result, dict) else {}
        status = result.get("status", "?") if isinstance(result, dict) else "?"
        lift = result.get("logLossLiftVsPrior") if isinstance(result, dict) else None
        print(
            f"[FIT {self.completed:02d}/{self.total:02d} | {pct_after:5.1f}%] DONE  "
            f"{horizon}s | {feature_set} | {experiment} | status={status} | "
            f"fit={_duration(fit_seconds)} elapsed={_duration(elapsed)} ETA~{_duration(eta)} | "
            f"logloss={_metric(test.get('logLoss'))} "
            f"lift={_metric(lift)} "
            f"AUC={_metric(test.get('rocAuc'))} "
            f"AP={_metric(test.get('averagePrecision'))}",
            flush=True,
        )
        self.report["progress"] = {
            "completedFits": self.completed,
            "totalFits": self.total,
            "percent": pct_after,
            "elapsedSeconds": elapsed,
            "estimatedRemainingSeconds": eta,
            "lastFitSeconds": fit_seconds,
            "lastFit": {
                "horizon": horizon,
                "featureSet": feature_set,
                "experiment": experiment,
                "status": status,
            },
        }
        _write_report(self.report_path, self.report)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fast-discovery Target Taker eligibility EBM with visible per-fit progress, "
            "ETA, and checkpointed partial reports."
        )
    )
    parser.add_argument("--dataset", type=Path, default=base.DEFAULT_OUTPUT)
    parser.add_argument("--special-start", required=True)
    parser.add_argument("--special-end", default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--special-train-fraction", type=float, default=0.50)
    parser.add_argument("--horizons", default="5,2")
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

    path = args.dataset.expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"dataset missing: {path}")

    print("TARGET_TAKER_FAST_DISCOVERY", flush=True)
    print(
        f"config: horizons={','.join(str(v) for v in horizons)} "
        f"featureSets={len(base.FEATURE_SETS)} splits=3 "
        f"maxRounds={max(100, int(args.max_rounds))} "
        f"outerBags={max(2, int(args.outer_bags))} "
        f"interactions={max(0, int(args.interactions))}",
        flush=True,
    )
    print(f"loading dataset: {path}", flush=True)

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
    historical_markets = base._market_order(historical, "decision_sampled_at_ms")
    special_markets = base._market_order(special, "decision_sampled_at_ms")
    if len(historical_markets) < 12:
        raise SystemExit(f"Need more pre-special markets; got {len(historical_markets)}")
    if len(special_markets) < 3:
        raise SystemExit(f"Need at least 3 special markets; got {len(special_markets)}")

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    fraction = min(0.8, max(0.2, float(args.special_train_fraction)))

    normal_train, normal_cal, normal_test = base._normal_reference_split(historical_markets)
    hist_train, hist_cal = base._split_train_cal(historical_markets)
    cut = max(
        1,
        min(
            len(special_markets) - 1,
            int(round(len(special_markets) * fraction)),
        ),
    )
    special_early = special_markets[:cut]
    special_late = special_markets[cut:]
    retrain_train, retrain_cal = base._adapt_split(historical_markets, special_early)

    total_fits = len(horizons) * len(base.FEATURE_SETS) * 3
    all_drift_features = list(
        dict.fromkeys(base.FROZEN16_FEATURES + base.SPECIAL_REGIME_FEATURES)
    )
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "dataset": str(path),
        "specialWindow": {"startMs": start_ms, "endMs": end_ms},
        "mode": "FAST_DISCOVERY",
        "config": {
            "horizons": horizons,
            "featureSets": list(base.FEATURE_SETS),
            "splitsPerFeatureSet": 3,
            "plannedFits": total_fits,
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
            "specialTrainFraction": fraction,
        },
        "boundary": (
            "Special markets are market-disjoint from pre-special training. "
            "Target events in the dataset are strict future-second buckets; "
            "same-second events are forbidden."
        ),
        "rows": {
            "historical": int(len(historical)),
            "special": int(len(special)),
            "historicalMarkets": len(historical_markets),
            "specialMarkets": len(special_markets),
            "specialEarlyMarkets": len(special_early),
            "specialLateMarkets": len(special_late),
        },
        "featureDrift": base._drift(
            historical, special, all_drift_features, pd
        )[:40],
        "tasks": {},
        "progress": {
            "completedFits": 0,
            "totalFits": total_fits,
            "percent": 0.0,
        },
    }
    report_path = args.report.expanduser().resolve()
    _write_report(report_path, report)

    print(
        f"rows: historical={len(historical):,} special={len(special):,} | "
        f"markets: historical={len(historical_markets)} special={len(special_markets)} "
        f"(early={len(special_early)}, late={len(special_late)})",
        flush=True,
    )
    print(f"planned fits: {total_fits}", flush=True)
    print(f"checkpoint report: {report_path}", flush=True)

    progress = Progress(total_fits, report_path, report)

    for horizon in horizons:
        label = f"label_next_target_taker_any_{horizon}s"
        print(
            f"\n=== HORIZON {horizon}s | "
            f"historical positive={float(pd.to_numeric(historical[label], errors='raise').mean()):.2%} | "
            f"special positive={float(pd.to_numeric(special[label], errors='raise').mean()):.2%} ===",
            flush=True,
        )
        task: dict[str, Any] = {
            "label": label,
            "historicalPositiveRate": float(
                pd.to_numeric(historical[label], errors="raise").mean()
            ),
            "specialPositiveRate": float(
                pd.to_numeric(special[label], errors="raise").mean()
            ),
            "featureSets": {},
        }
        report["tasks"][f"eligibility_{horizon}s"] = task
        _write_report(report_path, report)

        for feature_index, (name, requested) in enumerate(base.FEATURE_SETS.items()):
            features = base._usable(requested, frame, pd)
            if not features:
                task["featureSets"][name] = {
                    "features": [],
                    "status": "NO_USABLE_FEATURES",
                }
                for experiment in (
                    "normalReference",
                    "experimentA_preSpecialToSpecial",
                    "experimentB_earlySpecialToLateSpecial",
                ):
                    progress.run(
                        horizon=horizon,
                        feature_set=name,
                        experiment=experiment,
                        fn=lambda: {"status": "NO_USABLE_FEATURES"},
                    )
                continue

            values: dict[str, Any] = {"features": features}
            task["featureSets"][name] = values

            values["normalReference"] = progress.run(
                horizon=horizon,
                feature_set=name,
                experiment="normalReference",
                fn=(
                    (lambda h=horizon, fi=feature_index, fs=features: base._fit_eval(
                        shared=shared,
                        deps=deps,
                        frame=historical,
                        label=label,
                        features=fs,
                        train_markets=normal_train,
                        calibration_markets=normal_cal,
                        test_markets=normal_test,
                        interactions=interactions,
                        max_rounds=max_rounds,
                        outer_bags=outer_bags,
                        seed=1000 + h * 100 + fi,
                    ))
                    if normal_test
                    else (lambda: {"status": "INSUFFICIENT_MARKETS"})
                ),
            )
            _write_report(report_path, report)

            values["experimentA_preSpecialToSpecial"] = progress.run(
                horizon=horizon,
                feature_set=name,
                experiment="experimentA_preSpecialToSpecial",
                fn=lambda h=horizon, fi=feature_index, fs=features: base._fit_eval(
                    shared=shared,
                    deps=deps,
                    frame=analysis_frame,
                    label=label,
                    features=fs,
                    train_markets=hist_train,
                    calibration_markets=hist_cal,
                    test_markets=special_markets,
                    interactions=interactions,
                    max_rounds=max_rounds,
                    outer_bags=outer_bags,
                    seed=3000 + h * 100 + fi,
                ),
            )
            _write_report(report_path, report)

            values["experimentB_earlySpecialToLateSpecial"] = progress.run(
                horizon=horizon,
                feature_set=name,
                experiment="experimentB_earlySpecialToLateSpecial",
                fn=(
                    (lambda h=horizon, fi=feature_index, fs=features: base._fit_eval(
                        shared=shared,
                        deps=deps,
                        frame=analysis_frame,
                        label=label,
                        features=fs,
                        train_markets=retrain_train,
                        calibration_markets=retrain_cal,
                        test_markets=special_late,
                        interactions=interactions,
                        max_rounds=max_rounds,
                        outer_bags=outer_bags,
                        seed=5000 + h * 100 + fi,
                    ))
                    if special_late
                    else (lambda: {"status": "INSUFFICIENT_SPECIAL_LATE"})
                ),
            )
            _write_report(report_path, report)

        ranking: list[dict[str, Any]] = []
        for name, values in task["featureSets"].items():
            special_eval = values.get("experimentA_preSpecialToSpecial", {})
            normal_eval = values.get("normalReference", {})
            retrain_eval = values.get("experimentB_earlySpecialToLateSpecial", {})
            ranking.append(
                {
                    "featureSet": name,
                    "specialAuc": special_eval.get("test", {}).get("rocAuc")
                    if special_eval.get("status") == "OK"
                    else None,
                    "specialAveragePrecision": special_eval.get("test", {}).get(
                        "averagePrecision"
                    )
                    if special_eval.get("status") == "OK"
                    else None,
                    "specialLogLossLift": special_eval.get("logLossLiftVsPrior")
                    if special_eval.get("status") == "OK"
                    else None,
                    "normalLogLossLift": normal_eval.get("logLossLiftVsPrior")
                    if normal_eval.get("status") == "OK"
                    else None,
                    "retrainLateSpecialLogLossLift": retrain_eval.get(
                        "logLossLiftVsPrior"
                    )
                    if retrain_eval.get("status") == "OK"
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
        _write_report(report_path, report)

        print(f"\n--- {horizon}s ranking by Experiment A special log-loss lift ---", flush=True)
        for index, row in enumerate(ranking, 1):
            print(
                f"{index}. {row['featureSet']}: "
                f"lift={_metric(row['specialLogLossLift'])} "
                f"AUC={_metric(row['specialAuc'])} "
                f"AP={_metric(row['specialAveragePrecision'])} "
                f"late-retrain-lift={_metric(row['retrainLateSpecialLogLossLift'])}",
                flush=True,
            )

    elapsed = time.perf_counter() - progress.started
    report["progress"] = {
        "completedFits": progress.completed,
        "totalFits": total_fits,
        "percent": 100.0,
        "elapsedSeconds": elapsed,
        "estimatedRemainingSeconds": 0.0,
        "status": "COMPLETE",
    }
    _write_report(report_path, report)

    print(
        f"\nFAST DISCOVERY COMPLETE: {progress.completed}/{total_fits} fits "
        f"in {_duration(elapsed)}",
        flush=True,
    )
    print(f"report: {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
