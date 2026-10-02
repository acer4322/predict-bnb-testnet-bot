from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import analyze_target_controller_hazard_v21 as hazard
import analyze_target_controller_fragment_ebm_v272 as v272
import analyze_target_controller_prediction_common_support_v273b as v273
import analyze_target_controller_prediction_gap_oriented_v274 as v274

base = v272.base
ROOT = base.ROOT
VERSION = "TARGET_CONTROLLER_PREDICTION_RECENT_INDEPENDENT_V276"
DEFAULT_OFFICIAL_DB = hazard.DEFAULT_DB
DEFAULT_MICRO_DB = base.DEFAULT_DB
DEFAULT_BOOK_DB = v272.DEFAULT_BOOK_DB
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_prediction_recent_independent_v276_report.json"
DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_prediction_recent_independent_v276_states.csv"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_prediction_recent_independent_v276_rows.csv"
REQUIRED = list(v274.REQUIRED)
CONTROL = list(v274.CONTROL)
RAW_FEATURES = list(v274.RAW_FEATURES)
ORIENTED_FEATURES = list(v274.ORIENTED_FEATURES)
REGIME = "RECENT_CONTIGUOUS_SESSION"
BUCKET_MS = 300_000


def _bucket_bounds(ms: int) -> tuple[int, int]:
    start = (int(ms) // BUCKET_MS) * BUCKET_MS
    return start, start + BUCKET_MS


def _latest_segment(events: list[dict[str, Any]], mapping: Mapping[tuple[str, str], int]) -> int:
    if not events:
        raise RuntimeError("no Target events")
    last = max(events, key=lambda r: (int(r["event_ms"]), str(r["leg_id"])))
    key = (str(last["source_version"]), str(last["leg_id"]))
    segment = mapping.get(key)
    if segment is None:
        raise RuntimeError("latest Target event is not assigned to a continuity segment")
    return int(segment)


def _build_recent_states(
    events: list[dict[str, Any]],
    gap_minutes: float,
    candidate_markets: int,
    completion_grace_ms: int,
    idle_gap_ms: int,
    burst_cap_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    mapping, hard_gaps = hazard.v2._assign_segments(events, max(1, int(gap_minutes * 60_000)))
    segment_id = _latest_segment(events, mapping)
    latest_event_ms = max(int(r["event_ms"]) for r in events)
    parents = hazard.v2._build_parents(events)
    events_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    parents_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        events_by_market[int(row["market_id"])].append(row)
    for row in parents:
        parents_by_market[int(row["market_id"])].append(row)

    candidates: list[dict[str, Any]] = []
    states_by_market: dict[int, list[dict[str, Any]]] = {}
    for market_id, market_events in events_by_market.items():
        valid, market_segment, source, reason = hazard.v2._market_segment(market_events, mapping)
        if not valid or market_segment != segment_id:
            continue
        market_parents = parents_by_market.get(market_id, [])
        if not market_parents:
            continue
        first_parent_ms = min(int(p["first_event_ms"]) for p in market_parents)
        bucket_start, bucket_end = _bucket_bounds(first_parent_ms)
        complete_market = bucket_end + max(0, int(completion_grace_ms)) <= latest_event_ms
        meta = {
            "marketId": int(market_id),
            "segmentId": int(market_segment),
            "source": source,
            "bucketStartMs": bucket_start,
            "bucketEndMs": bucket_end,
            "completeByLatestObservedEvent": complete_market,
            "invalidReason": reason,
        }
        if not complete_market:
            meta["stateRows"] = 0
            meta["excludedReason"] = "LATEST_BUCKET_NOT_PROVEN_COMPLETE"
            candidates.append(meta)
            continue
        windows = {REGIME: (bucket_start, bucket_end)}
        drows = hazard._directional_bursts(
            int(market_id), int(market_segment), market_parents,
            max(0, int(idle_gap_ms)), max(0, int(burst_cap_ms)), windows,
        )
        srows, grid_audit = hazard._fixed_grid(
            int(market_id), int(market_segment), market_parents, drows, windows
        )
        meta["stateRows"] = len(srows)
        meta["gridAudit"] = grid_audit
        if not srows:
            meta["excludedReason"] = "NO_VALID_FIXED_GRID_STATES"
        candidates.append(meta)
        states_by_market[int(market_id)] = srows

    complete_candidates = [m for m in candidates if m.get("completeByLatestObservedEvent") and int(m.get("stateRows") or 0) > 0]
    complete_candidates.sort(key=lambda m: (int(m["bucketStartMs"]), int(m["marketId"])), reverse=True)
    retained = complete_candidates[: max(1, int(candidate_markets))]
    retained_ids = {int(m["marketId"]) for m in retained}
    states = [r for mid in retained_ids for r in states_by_market.get(mid, [])]
    states.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"]), int(r["segment_id"])))
    retained.sort(key=lambda m: int(m["bucketStartMs"]), reverse=True)
    source = {
        "latestSegmentId": segment_id,
        "latestObservedTargetEventMs": latest_event_ms,
        "hardGaps": hard_gaps,
        "marketsInLatestSegment": len({int(r["market_id"]) for r in events if mapping.get((str(r["source_version"]), str(r["leg_id"]))) == segment_id}),
        "candidateCompleteMarketsRetained": len(retained),
    }
    return states, retained, source


def _support_report(
    candidate_meta: list[dict[str, Any]],
    states: list[dict[str, Any]],
    joined: list[dict[str, Any]],
    complete: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    meta = {int(m["marketId"]): dict(m) for m in candidate_meta}
    state_n = Counter(int(r["market_id"]) for r in states)
    joined_n = Counter(int(r["market_id"]) for r in joined)
    complete_n = Counter(int(r["market_id"]) for r in complete)
    out = []
    for market_id in sorted(meta, key=lambda mid: int(meta[mid]["bucketStartMs"]), reverse=True):
        n, j, c = state_n[market_id], joined_n[market_id], complete_n[market_id]
        micro_rate = j / n if n else 0.0
        joint_rate = c / n if n else 0.0
        eligible = n >= 30 and j >= 30 and c >= 30 and micro_rate >= 0.80 and joint_rate >= 0.50
        row = dict(meta[market_id])
        row.update({
            "stateRows": n,
            "freshMicroJoinedRows": j,
            "freshMicroJoinRate": micro_rate,
            "jointCompleteRows": c,
            "jointCompleteRate": joint_rate,
            "jointEligible": eligible,
        })
        if not eligible:
            reasons = []
            if n < 30:
                reasons.append("STATE_ROWS_LT_30")
            if j < 30 or micro_rate < 0.80:
                reasons.append("MICRO_SUPPORT")
            if c < 30 or joint_rate < 0.50:
                reasons.append("PREDICTION_3S_JOINT_SUPPORT")
            row["supportExcludedReason"] = "+".join(reasons)
        out.append(row)
    return out


def _select_model_markets(markets: list[dict[str, Any]], max_markets: int) -> list[dict[str, Any]]:
    good = [m for m in markets if m.get("jointEligible")]
    good.sort(key=lambda m: (int(m["bucketStartMs"]), int(m["marketId"])), reverse=True)
    return good[: max(1, int(max_markets))]


def _constant_baseline(model: Mapping[str, Any]) -> dict[str, Any]:
    rows = v273.baselines(model)
    valid = [r for r in rows if r.get("constantTrainPrevalenceLogLoss") is not None and r.get("modelLogLoss") is not None]
    wins = sum(float(r["deltaModelMinusConstant"]) < 0 for r in valid)
    return {
        "folds": rows,
        "validFolds": len(valid),
        "modelBeatsConstantFolds": wins,
        "modelBeatsConstantRate": wins / len(valid) if valid else None,
    }


def _pair_signal(pair: Mapping[str, Any]) -> dict[str, Any]:
    folds = pair.get("foldDeltas") or []
    valid = [
        f for f in folds
        if f.get("deltaAucTreatmentMinusControl") is not None
        and f.get("deltaLogLossTreatmentMinusControl") is not None
    ]
    both = sum(
        float(f["deltaAucTreatmentMinusControl"]) > 0
        and float(f["deltaLogLossTreatmentMinusControl"]) < 0
        for f in valid
    )
    delta_auc = base._num(pair.get("deltaAucTreatmentMinusControl"))
    delta_ll = base._num(pair.get("deltaLogLossTreatmentMinusControl"))
    required = max(2, math.ceil(0.60 * len(valid))) if valid else 999
    supported = (
        delta_auc is not None and delta_auc > 0
        and delta_ll is not None and delta_ll < 0
        and len(valid) >= 3 and both >= required
    )
    return {
        "validPairedFolds": len(valid),
        "foldsBetterOnBothAucAndLogLoss": both,
        "requiredBetterFolds": required if valid else None,
        "pooledDeltaAuc": delta_auc,
        "pooledDeltaLogLoss": delta_ll,
        "orientationReplicated": supported,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.6 recent-session independent REPAIR validation")
    p.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_DB)
    p.add_argument("--micro-db", type=Path, default=DEFAULT_MICRO_DB)
    p.add_argument("--book-db", type=Path, default=DEFAULT_BOOK_DB)
    p.add_argument("--asset", default="BTC")
    p.add_argument("--cutover", default=hazard.v2.DEFAULT_CUTOVER)
    p.add_argument("--gap-minutes", type=float, default=30.0)
    p.add_argument("--candidate-markets", type=int, default=24)
    p.add_argument("--max-model-markets", type=int, default=12)
    p.add_argument("--min-model-markets", type=int, default=4)
    p.add_argument("--min-model-rows", type=int, default=300)
    p.add_argument("--completion-grace-ms", type=int, default=5000)
    p.add_argument("--idle-gap-ms", type=int, default=1000)
    p.add_argument("--burst-cap-ms", type=int, default=3000)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--states", type=Path, default=DEFAULT_STATES)
    p.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = p.parse_args()

    raw, source_audit = hazard.compat._load_source_compat(args.official_db, "OFFICIAL", str(args.asset).upper())
    events, selection_audit = hazard._select(raw, hazard.v2._parse_ms(args.cutover))
    if not events:
        raise RuntimeError("no OFFICIAL Target events after cutover")

    states, candidate_meta, recent_source = _build_recent_states(
        events, args.gap_minutes, args.candidate_markets, args.completion_grace_ms,
        args.idle_gap_ms, args.burst_cap_ms,
    )
    if not states:
        raise RuntimeError("latest contiguous Target session has no proven-complete fixed-grid markets")
    hazard.v2._write_csv(args.states, states, hazard.STATE_FIELDS)

    start_ms = min(int(r["sample_ms"]) for r in states)
    end_ms = max(int(r["sample_ms"]) for r in states)
    snapshots, _ = base._load_micro(args.micro_db, start_ms, end_ms)
    pred_by_key, pred_audit = v272._load_8778_rows(states, args.book_db)
    joined, join_audit = v272.build_ab_rows(states, snapshots, pred_by_key)
    complete = v273.complete(joined, REQUIRED)
    complete = [v274.add_gap_oriented_prediction(r) for r in complete]
    markets = _support_report(candidate_meta, states, joined, complete)
    selected_markets = _select_model_markets(markets, args.max_model_markets)
    selected_ids = {int(m["marketId"]) for m in selected_markets}
    model_rows = [r for r in complete if int(r["market_id"]) in selected_ids]
    model_rows.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    base._write_csv(args.rows, model_rows)

    support_ready = len(selected_markets) >= args.min_model_markets and len(model_rows) >= args.min_model_rows
    labels = [int(r["repair_within_3s"]) for r in model_rows]
    label_ready = len(set(labels)) == 2
    result: dict[str, Any] | None = None
    models: dict[str, Any] | None = None
    decision: dict[str, Any]

    if support_ready and label_ready:
        control, _ = base.fit_one(model_rows, CONTROL, "REPAIR", 3, "V276__CONTROL", False)
        raw_model, _ = base.fit_one(model_rows, RAW_FEATURES, "REPAIR", 3, "V276__RAW", False)
        oriented, _ = base.fit_one(model_rows, ORIENTED_FEATURES, "REPAIR", 3, "V276__GAP_ORIENTED", False)
        result = v274._three_way_summary(control, raw_model, oriented)
        signal = _pair_signal(result["orientedVsRaw"])
        oriented_constant = _constant_baseline(oriented)
        decision = {
            "status": "ORIENTATION_REPLICATED_RECENT_OOS" if signal["orientationReplicated"] else "ORIENTATION_NOT_REPLICATED_RECENT_OOS",
            "supportReady": True,
            "labelReady": True,
            "orientationSignal": signal,
            "orientedVsConstant": oriented_constant,
            "deployment": "RESEARCH_ONLY_DO_NOT_PROMOTE_FROM_THIS_RESULT",
        }
        models = {"control": control, "raw": raw_model, "oriented": oriented}
    else:
        reason = "INSUFFICIENT_JOINT_SUPPORT" if not support_ready else "INSUFFICIENT_REPAIR_LABEL_VARIATION"
        decision = {
            "status": reason,
            "supportReady": support_ready,
            "labelReady": label_ready,
            "eligibleMarkets": len(selected_markets),
            "modelRows": len(model_rows),
            "repairPositives": sum(labels),
            "deployment": "NO_MODEL_FIT",
        }

    report = {
        "version": VERSION,
        "policy": {
            "purpose": "Independent out-of-sample check of the frozen V2.7.4 RAW vs payoff-gap-oriented Prediction REPAIR representation on the newest contiguous collector session.",
            "sessionRule": f"Use only the latest OFFICIAL Target continuity segment after a >= {args.gap_minutes:g} minute observed-activity hard gap; never bridge older segments.",
            "marketCompletionRule": f"5m bucket end must be at least {args.completion_grace_ms} ms before the latest observed Target event in the same session.",
            "jointSupportRule": "Per market: >=30 fixed-grid rows, >=80% strict-past <=750ms micro join with >=30 rows, >=50% frozen current+3s Prediction complete-case rate with >=30 rows.",
            "selectionRule": f"Take the newest up to {args.max_model_markets} joint-eligible markets from the latest session; require >= {args.min_model_markets} markets and >= {args.min_model_rows} rows before fitting.",
            "validation": "LOMO by Target market; identical complete-case rows and frozen EBM parameters for CONTROL/RAW/ORIENTED.",
            "task": "REPAIR",
            "horizonS": 3,
            "predictionFieldsFrozenFromV273": REQUIRED,
            "developmentMarketsExcludedByConstruction": [1396279, 1396303, 1396309, 1396369],
            "noHyperparameterTuning": True,
            "noLiveTradingChanges": True,
        },
        "source": {
            "officialDb": str(args.official_db.expanduser().resolve()),
            "microDb": str(args.micro_db.expanduser().resolve()),
            "bookDb": str(args.book_db.expanduser().resolve()),
            "sourceAudit": source_audit,
            "selectionAudit": selection_audit,
            "recentSession": recent_source,
            "fixedGridRows": len(states),
            "microSnapshotsLoaded": len(snapshots),
            "freshMicroJoinedRows": len(joined),
            "jointCompleteRows": len(complete),
            "microJoinAudit": dict(join_audit),
            "predictionCoverageDecision": pred_audit.get("decision"),
        },
        "markets": markets,
        "selectedModelMarkets": selected_markets,
        "modelRows": len(model_rows),
        "repairPositives": sum(labels),
        "decision": decision,
        "result": result,
        "models": models,
        "interpretation": {
            "positive": "ORIENTATION_REPLICATED_RECENT_OOS means oriented beats RAW on pooled AUC and logloss and on both metrics in at least 60% of valid market holdouts. It supports the representation hypothesis, not live deployment.",
            "negative": "ORIENTATION_NOT_REPLICATED_RECENT_OOS means the stress-fragment orientation effect did not reproduce on the newest independent markets; do not feature-fish on these holdouts.",
            "constantBaseline": "Even if orientation replicates, inspect orientedVsConstant. Failure to beat train-prevalence constant on most holdouts means the timing model remains weak/non-deployable.",
        },
    }
    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "version": VERSION,
        "latestSegmentId": recent_source["latestSegmentId"],
        "eligibleMarkets": len(selected_markets),
        "modelRows": len(model_rows),
        "repairPositives": sum(labels),
        "decision": decision,
        "report": str(out),
        "states": str(args.states.expanduser().resolve()),
        "rows": str(args.rows.expanduser().resolve()),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
