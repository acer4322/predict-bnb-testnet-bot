from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import train_target_taker_post_first_idle_burst_hazard_fast_v1 as v1
import train_target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1 as ordinary_v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_report.json"
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_scores.csv"
REPORT_VERSION = "TARGET_TAKER_POST_FIRST_IDLE_BURST_HAZARD_ORDINARY_FULLTIMELINE_OOF_V1"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(v1.base._clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _write_scores(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "experiment", "fold", "horizon_seconds", "market_id", "decision_sampled_at_ms",
        "seconds_left", "macro_phase", "cap2_label", "cap3_label", "cap2_next_burst_delta_ms",
        "cap2_position_state", "cap2_burst_active", "cap2_risk_post_first_idle",
        "raw_probability",
    ]
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def _phase_metrics(v1mod: Any, shared: Any, deps: dict[str, Any], frame: Any, probability: Any, label: str) -> dict[str, Any]:
    pd = deps["pd"]
    np = deps["np"]
    result: dict[str, Any] = {}
    y = pd.to_numeric(frame[label], errors="raise").astype(int)
    phases = frame["macro_phase"].astype(str).to_numpy()
    for phase in ("OPEN", "MID", "TAIL"):
        idx = np.flatnonzero(phases == phase)
        if len(idx) == 0:
            result[phase] = {"rows": 0}
            continue
        result[phase] = v1mod._safe_metrics(shared, deps, y.iloc[idx], probability[idx])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train the ordinary post-first-idle frozen16 hazard exactly as before, but score every public second "
            "of each OOF test market. Target POST_FIRST/IDLE truth is training-risk-set truth only and never filters "
            "the full-timeline OOF scoring rows."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--special-start", default="2026-08-16T12:00:00+08:00")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--min-train-markets", type=int, default=180)
    parser.add_argument("--test-markets", type=int, default=50)
    parser.add_argument("--max-folds", type=int, default=7)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=600)
    parser.add_argument("--outer-bags", type=int, default=3)
    args = parser.parse_args()

    horizon = int(args.horizon)
    if horizon not in (2, 5):
        raise SystemExit("--horizon must be 2 or 5")
    start_ms = v1._epoch_ms(args.special_start)
    if start_ms is None:
        raise SystemExit("--special-start is required")

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
        "cap2_position_state", "cap2_burst_active", "cap2_risk_post_first_idle",
        f"cap2_label_next_burst_{horizon}s", f"cap3_label_next_burst_{horizon}s",
        "cap2_next_burst_delta_ms",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("dataset missing columns: " + ", ".join(missing))

    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["decision_sampled_at_ms"] = pd.to_numeric(frame["decision_sampled_at_ms"], errors="raise").astype("int64")
    frame["cap2_risk_post_first_idle"] = pd.to_numeric(frame["cap2_risk_post_first_idle"], errors="raise").astype(int)
    frame["cap2_burst_active"] = pd.to_numeric(frame["cap2_burst_active"], errors="raise").astype(int)

    # Strict boundary: any market observed at/after specialStart is excluded entirely.
    special_market_ids = set(
        int(v) for v in frame.loc[frame["decision_sampled_at_ms"] >= int(start_ms), "market_id"].unique().tolist()
    )
    ordinary_full = frame[
        (frame["decision_sampled_at_ms"] < int(start_ms))
        & (~frame["market_id"].isin(special_market_ids))
    ].copy()
    ordinary_risk = ordinary_full[ordinary_full["cap2_risk_post_first_idle"] == 1].copy()
    markets = v1._market_order(ordinary_risk)

    min_train = max(60, int(args.min_train_markets))
    test_markets = max(20, int(args.test_markets))
    max_folds = max(1, int(args.max_folds))
    folds = ordinary_v1._folds(markets, min_train=min_train, test_markets=test_markets, max_folds=max_folds)
    if not folds:
        raise SystemExit("no walk-forward folds")

    features = base._usable(base.FROZEN16_FEATURES, ordinary_full, pd)
    if len(features) != len(base.FROZEN16_FEATURES):
        missing_features = [f for f in base.FROZEN16_FEATURES if f not in features]
        raise SystemExit("missing frozen16 features: " + ", ".join(missing_features))

    label = f"cap2_label_next_burst_{horizon}s"
    audit_label = f"cap3_label_next_burst_{horizon}s"
    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Remove Target truth from runtime gating. The EBM is trained only on historical cap2 POST_FIRST+IDLE risk rows, "
            "but every public second in each ordinary OOF test market is scored. Target state columns are emitted only for "
            "after-the-fact audit and are forbidden as actor-state replay inputs."
        ),
        "dataset": str(dataset),
        "trainingRiskSet": "cap2_risk_post_first_idle == 1",
        "oofScoringSet": "ALL PUBLIC SECONDS IN ORDINARY TEST MARKETS",
        "ordinaryBoundary": {"specialStartMs": int(start_ms), "specialMarketsExcluded": len(special_market_ids)},
        "features": features,
        "config": {
            "horizonSeconds": horizon,
            "minTrainMarkets": min_train,
            "testMarketsPerFold": test_markets,
            "maxFolds": max_folds,
            "folds": len(folds),
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
        },
        "rows": {
            "ordinaryFullTimelineRows": int(len(ordinary_full)),
            "ordinaryTrainingRiskRows": int(len(ordinary_risk)),
            "ordinaryRiskMarkets": len(markets),
        },
        "folds": [],
        "aggregate": {},
        "progress": {"completedFits": 0, "totalFits": len(folds), "percent": 0.0},
    }
    report_path = args.report.expanduser().resolve()
    _write_json(report_path, report)

    print(REPORT_VERSION, flush=True)
    print(
        f"ordinary full rows={len(ordinary_full):,} | training risk rows={len(ordinary_risk):,} | "
        f"risk markets={len(markets)} | folds={len(folds)}",
        flush=True,
    )
    print("TARGET POST_FIRST/IDLE TRUTH DOES NOT FILTER OOF SCORING ROWS.", flush=True)

    score_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    completed = 0
    started = time.perf_counter()
    durations: list[float] = []

    for fold in folds:
        fold_number = int(fold["fold"])
        train = ordinary_risk[ordinary_risk["market_id"].isin(fold["trainMarkets"])].copy()
        test_full = ordinary_full[ordinary_full["market_id"].isin(fold["testMarkets"])].copy()
        test_full = test_full.sort_values(["decision_sampled_at_ms", "market_id"]).copy()
        y_train = pd.to_numeric(train[label], errors="raise").astype(int)
        if set(int(v) for v in y_train.unique()) != {0, 1}:
            raise SystemExit(f"fold {fold_number} training set lacks both classes")

        print(
            f"[FIT {completed + 1:02d}/{len(folds):02d}] START fold={fold_number} "
            f"trainRiskRows={len(train):,} testFullRows={len(test_full):,}",
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
            seed=10100 + horizon * 100 + fold_number,
        )
        raw = model.predict_proba(shared._numeric(pd, test_full, features))[:, 1]
        fit_seconds = time.perf_counter() - fit_started
        durations.append(fit_seconds)
        completed += 1

        y_full = pd.to_numeric(test_full[label], errors="raise").astype(int)
        full_metrics = v1._safe_metrics(shared, deps, y_full, raw)
        full_phase = _phase_metrics(v1, shared, deps, test_full, raw, label)

        risk_mask = pd.to_numeric(test_full["cap2_risk_post_first_idle"], errors="raise").astype(int).eq(1).to_numpy()
        risk_idx = np.flatnonzero(risk_mask)
        if len(risk_idx):
            risk_frame = test_full.iloc[risk_idx]
            y_risk = y_full.iloc[risk_idx]
            risk_metrics = v1._safe_metrics(shared, deps, y_risk, raw[risk_idx])
            risk_phase = _phase_metrics(v1, shared, deps, risk_frame, raw[risk_idx], label)
        else:
            risk_metrics = {"rows": 0}
            risk_phase = {}

        fold_payload = {
            "fold": fold_number,
            "trainMarkets": len(fold["trainMarkets"]),
            "testMarkets": len(fold["testMarkets"]),
            "trainRiskRows": int(len(train)),
            "testFullTimelineRows": int(len(test_full)),
            "testPostFirstIdleRows": int(len(risk_idx)),
            "trainPositiveRate": float(y_train.mean()),
            "fullTimeline": {"raw": full_metrics, "byMacroPhase": full_phase},
            "postFirstIdleAudit": {"raw": risk_metrics, "byMacroPhase": risk_phase},
        }
        report["folds"].append(fold_payload)

        cap3_y = pd.to_numeric(test_full[audit_label], errors="raise").astype(int)
        for i, (_, row) in enumerate(test_full.iterrows()):
            delta = row.get("cap2_next_burst_delta_ms")
            score_rows.append({
                "experiment": "ORDINARY_FULLTIMELINE_WALK_FORWARD",
                "fold": fold_number,
                "horizon_seconds": horizon,
                "market_id": int(row["market_id"]),
                "decision_sampled_at_ms": int(row["decision_sampled_at_ms"]),
                "seconds_left": row.get("seconds_left"),
                "macro_phase": str(row.get("macro_phase") or "UNKNOWN"),
                "cap2_label": int(y_full.iloc[i]),
                "cap3_label": int(cap3_y.iloc[i]),
                "cap2_next_burst_delta_ms": delta,
                "cap2_position_state": str(row.get("cap2_position_state") or ""),
                "cap2_burst_active": int(row.get("cap2_burst_active") or 0),
                "cap2_risk_post_first_idle": int(row.get("cap2_risk_post_first_idle") or 0),
                "raw_probability": float(raw[i]),
            })
            metric_rows.append({
                "fold": fold_number,
                "phase": str(row.get("macro_phase") or "UNKNOWN"),
                "label": int(y_full.iloc[i]),
                "risk": int(row.get("cap2_risk_post_first_idle") or 0),
                "probability": float(raw[i]),
            })

        elapsed = time.perf_counter() - started
        avg = sum(durations) / len(durations)
        eta = avg * (len(folds) - completed)
        report["progress"] = {
            "completedFits": completed,
            "totalFits": len(folds),
            "percent": completed / len(folds) * 100.0,
            "elapsedSeconds": elapsed,
            "estimatedRemainingSeconds": eta,
            "lastFitSeconds": fit_seconds,
            "lastFit": {"fold": fold_number},
        }
        _write_json(report_path, report)
        print(
            f"[FIT {completed:02d}/{len(folds):02d}] DONE fold={fold_number} "
            f"fullAUC={v1._metric(full_metrics.get('rocAuc'))} "
            f"riskAUC={v1._metric(risk_metrics.get('rocAuc'))} "
            f"fit={v1._duration(fit_seconds)} ETA~{v1._duration(eta)}",
            flush=True,
        )

    metric_frame = pd.DataFrame(metric_rows)
    y_all = pd.to_numeric(metric_frame["label"], errors="raise").astype(int)
    p_all = pd.to_numeric(metric_frame["probability"], errors="raise").to_numpy(dtype=float)
    aggregate_full = v1._safe_metrics(shared, deps, y_all, p_all)
    aggregate_phase: dict[str, Any] = {}
    for phase in ("OPEN", "MID", "TAIL"):
        idx = np.flatnonzero(metric_frame["phase"].astype(str).eq(phase).to_numpy())
        aggregate_phase[phase] = (
            v1._safe_metrics(shared, deps, y_all.iloc[idx], p_all[idx]) if len(idx) else {"rows": 0}
        )
    risk_idx = np.flatnonzero(pd.to_numeric(metric_frame["risk"], errors="raise").astype(int).eq(1).to_numpy())
    aggregate_risk = (
        v1._safe_metrics(shared, deps, y_all.iloc[risk_idx], p_all[risk_idx]) if len(risk_idx) else {"rows": 0}
    )
    report["aggregate"] = {
        "fullTimeline": {
            "oofRows": int(len(metric_frame)),
            "oofMarkets": len({int(r["market_id"]) for r in score_rows}),
            "raw": aggregate_full,
            "byMacroPhase": aggregate_phase,
        },
        "postFirstIdleAudit": {
            "rows": int(len(risk_idx)),
            "raw": aggregate_risk,
        },
    }
    report["progress"]["percent"] = 100.0
    report["progress"]["estimatedRemainingSeconds"] = 0.0
    _write_json(report_path, report)
    _write_scores(args.scores, score_rows)

    print("\nORDINARY FULL-TIMELINE OOF SCORING COMPLETE", flush=True)
    print(f"report: {report_path}", flush=True)
    print(f"scores: {args.scores.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
