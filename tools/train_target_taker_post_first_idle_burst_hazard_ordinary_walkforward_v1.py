from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import train_target_taker_post_first_idle_burst_hazard_fast_v1 as v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1_report.json"
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1_scores.csv"
REPORT_VERSION = "TARGET_TAKER_POST_FIRST_IDLE_BURST_HAZARD_ORDINARY_WALKFORWARD_V1"


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(v1.base._clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _folds(markets: list[int], *, min_train: int, test_markets: int, max_folds: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    start = int(min_train)
    fold_index = 1
    while start < len(markets) and fold_index <= int(max_folds):
        test = markets[start : min(len(markets), start + int(test_markets))]
        if len(test) < max(10, int(test_markets) // 2):
            break
        result.append({
            "fold": fold_index,
            "trainMarkets": markets[:start],
            "testMarkets": test,
        })
        start += int(test_markets)
        fold_index += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate frozen16 Target post-first-idle burst hazard on ordinary pre-special markets only, "
            "using expanding market-disjoint walk-forward folds. Special-market rows never enter train or test."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--special-start", default="2026-08-16T12:00:00+08:00")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--horizons", default="5")
    parser.add_argument("--min-train-markets", type=int, default=180)
    parser.add_argument("--test-markets", type=int, default=50)
    parser.add_argument("--max-folds", type=int, default=7)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=600)
    parser.add_argument("--outer-bags", type=int, default=3)
    args = parser.parse_args()

    start_ms = v1._epoch_ms(args.special_start)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    horizons = _parse_horizons(args.horizons)

    base = v1.base
    shared = base._shared()
    deps = shared._imports()
    pd = deps["pd"]
    np = deps["np"]

    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise SystemExit(f"dataset missing: {dataset}")
    frame = pd.read_csv(dataset)
    required = {
        "market_id", "decision_sampled_at_ms", "seconds_left", "macro_phase",
        "cap2_risk_post_first_idle", "cap2_label_next_burst_2s", "cap2_label_next_burst_5s",
        "cap3_label_next_burst_2s", "cap3_label_next_burst_5s",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("dataset missing columns: " + ", ".join(missing))

    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["decision_sampled_at_ms"] = pd.to_numeric(frame["decision_sampled_at_ms"], errors="raise").astype("int64")
    frame["cap2_risk_post_first_idle"] = pd.to_numeric(frame["cap2_risk_post_first_idle"], errors="raise").astype(int)

    risk = frame[frame["cap2_risk_post_first_idle"] == 1].copy()
    special_market_ids = set(
        int(v) for v in risk.loc[risk["decision_sampled_at_ms"] >= int(start_ms), "market_id"].unique().tolist()
    )
    ordinary = risk[
        (risk["decision_sampled_at_ms"] < int(start_ms))
        & (~risk["market_id"].isin(special_market_ids))
    ].copy()
    markets = v1._market_order(ordinary)

    min_train = max(60, int(args.min_train_markets))
    test_markets = max(20, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    folds = _folds(markets, min_train=min_train, test_markets=test_markets, max_folds=max_folds)
    if not folds:
        raise SystemExit(
            f"no walk-forward folds: ordinary markets={len(markets)}, minTrain={min_train}, testMarkets={test_markets}"
        )

    features = base._usable(base.FROZEN16_FEATURES, ordinary, pd)
    if len(features) != len(base.FROZEN16_FEATURES):
        missing_features = [f for f in base.FROZEN16_FEATURES if f not in features]
        raise SystemExit("missing frozen16 features: " + ", ".join(missing_features))

    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))
    total_fits = len(horizons) * len(folds)

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Return the mechanism discovered/amplified in the special regime to ordinary pre-special markets. "
            "No special-market row is used in model fitting, threshold learning, or evaluation."
        ),
        "dataset": str(dataset),
        "riskSet": "cap2_risk_post_first_idle == 1",
        "ordinaryBoundary": {
            "specialStartMs": int(start_ms),
            "specialMarketsExcluded": len(special_market_ids),
        },
        "features": features,
        "config": {
            "horizons": horizons,
            "minTrainMarkets": min_train,
            "testMarketsPerFold": test_markets,
            "maxFolds": max_folds,
            "folds": len(folds),
            "plannedFits": total_fits,
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
            "scoreForReplay": "raw_probability",
        },
        "rows": {
            "ordinaryRiskRows": int(len(ordinary)),
            "ordinaryMarkets": len(markets),
        },
        "folds": {},
        "aggregate": {},
        "progress": {"completedFits": 0, "totalFits": total_fits, "percent": 0.0},
    }
    report_path = args.report.expanduser().resolve()
    _write_json(report_path, report)

    print(REPORT_VERSION, flush=True)
    print(
        f"ordinary pre-special risk rows={len(ordinary):,} markets={len(markets)} | "
        f"walk-forward folds={len(folds)} | planned fits={total_fits}",
        flush=True,
    )
    print("SPECIAL MARKET ROWS ARE EXCLUDED FROM BOTH TRAINING AND TESTING.", flush=True)

    score_rows: list[dict[str, Any]] = []
    completed = 0
    started = time.perf_counter()
    fit_durations: list[float] = []

    for horizon in horizons:
        label = f"cap2_label_next_burst_{horizon}s"
        audit_label = f"cap3_label_next_burst_{horizon}s"
        report["folds"][f"hazard_{horizon}s"] = []
        horizon_scores: list[dict[str, Any]] = []

        for fold in folds:
            fold_number = int(fold["fold"])
            train = ordinary[ordinary["market_id"].isin(fold["trainMarkets"])].copy()
            test = ordinary[ordinary["market_id"].isin(fold["testMarkets"])].copy()
            y_train = pd.to_numeric(train[label], errors="raise").astype(int)
            y_test = pd.to_numeric(test[label], errors="raise").astype(int)
            if set(int(v) for v in y_train.unique()) != {0, 1}:
                raise SystemExit(f"fold {fold_number} horizon {horizon}s training set lacks both classes")

            print(
                f"[FIT {completed + 1:02d}/{total_fits:02d}] START {horizon}s fold={fold_number} "
                f"trainMarkets={len(fold['trainMarkets'])} testMarkets={len(fold['testMarkets'])}",
                flush=True,
            )
            fit_started = time.perf_counter()
            model = shared._fit_classifier(
                deps,
                shared._numeric(pd, train, features),
                y_train,
                interactions=min(interactions, max(0, len(features) // 2)),
                max_rounds=max_rounds,
                outer_bags=outer_bags,
                seed=9100 + horizon * 100 + fold_number,
            )
            raw = model.predict_proba(shared._numeric(pd, test, features))[:, 1]
            fit_seconds = time.perf_counter() - fit_started
            fit_durations.append(fit_seconds)
            completed += 1

            metrics = v1._safe_metrics(shared, deps, y_test, raw)
            phase_metrics = v1._phase_metrics(shared, deps, test, y_test, raw, raw)
            fold_payload = {
                "fold": fold_number,
                "trainMarkets": len(fold["trainMarkets"]),
                "testMarkets": len(fold["testMarkets"]),
                "trainRows": int(len(train)),
                "testRows": int(len(test)),
                "trainPositiveRate": float(y_train.mean()),
                "testPositiveRate": float(y_test.mean()),
                "raw": metrics,
                "phaseMetrics": {
                    phase: {
                        "rows": payload.get("rows"),
                        "positiveRate": payload.get("positiveRate"),
                        "raw": payload.get("raw"),
                    }
                    for phase, payload in phase_metrics.items()
                },
            }
            report["folds"][f"hazard_{horizon}s"].append(fold_payload)

            cap3_y = pd.to_numeric(test[audit_label], errors="raise").astype(int)
            for i, (_, row) in enumerate(test.iterrows()):
                payload = {
                    "experiment": "ORDINARY_HISTORICAL_WALK_FORWARD",
                    "horizon_seconds": horizon,
                    "fold": fold_number,
                    "market_id": int(row["market_id"]),
                    "decision_sampled_at_ms": int(row["decision_sampled_at_ms"]),
                    "seconds_left": row.get("seconds_left"),
                    "macro_phase": str(row.get("macro_phase") or "UNKNOWN"),
                    "cap2_label": int(y_test.iloc[i]),
                    "cap3_label": int(cap3_y.iloc[i]),
                    "raw_probability": float(raw[i]),
                    "calibrated_probability": float(raw[i]),
                }
                horizon_scores.append(payload)
                score_rows.append(payload)

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
                "lastFit": {"horizon": horizon, "fold": fold_number},
            }
            _write_json(report_path, report)
            print(
                f"[FIT {completed:02d}/{total_fits:02d}] DONE  {horizon}s fold={fold_number} "
                f"AUC={v1._metric(metrics.get('rocAuc'))} AP={v1._metric(metrics.get('averagePrecision'))} "
                f"fit={v1._duration(fit_seconds)} ETA~{v1._duration(eta)}",
                flush=True,
            )

        # Aggregate only genuine out-of-fold rows.
        aggregate_test = pd.DataFrame(horizon_scores)
        y_all = pd.to_numeric(aggregate_test["cap2_label"], errors="raise").astype(int)
        p_all = pd.to_numeric(aggregate_test["raw_probability"], errors="raise").to_numpy(dtype=float)
        aggregate_metrics = v1._safe_metrics(shared, deps, y_all, p_all)
        aggregate_phase: dict[str, Any] = {}
        for phase in ("OPEN", "MID", "TAIL"):
            mask = aggregate_test["macro_phase"].astype(str).eq(phase).to_numpy()
            idx = np.flatnonzero(mask)
            if len(idx) == 0:
                aggregate_phase[phase] = {"rows": 0}
                continue
            y_phase = y_all.iloc[idx]
            aggregate_phase[phase] = v1._safe_metrics(shared, deps, y_phase, p_all[idx])
        report["aggregate"][f"hazard_{horizon}s"] = {
            "oofRows": int(len(aggregate_test)),
            "oofMarkets": int(aggregate_test["market_id"].nunique()),
            "raw": aggregate_metrics,
            "byMacroPhase": aggregate_phase,
        }
        _write_json(report_path, report)

    v1._write_scores(args.scores, score_rows)
    report["progress"]["percent"] = 100.0
    report["progress"]["estimatedRemainingSeconds"] = 0.0
    _write_json(report_path, report)

    print("\nORDINARY WALK-FORWARD COMPLETE", flush=True)
    print(f"report: {report_path}", flush=True)
    print(f"scores: {args.scores.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
