from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import analyze_target_controller_prediction_recent_independent_v276 as v276
import analyze_target_controller_prediction_common_support_v273b as v273

base = v276.base
ROOT = base.ROOT
VERSION = "TARGET_CONTROLLER_PREDICTION_RAW_UNSEEN_V277"
DEFAULT_PREVIOUS = ROOT / "data" / "research" / "target_controller_prediction_recent_independent_v276_report.json"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_prediction_raw_unseen_v277_report.json"
DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_prediction_raw_unseen_v277_states.csv"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_prediction_raw_unseen_v277_rows.csv"


def _previous_exclusions(path: Path) -> set[int]:
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    out = {int(x) for x in payload.get("policy", {}).get("developmentMarketsExcludedByConstruction", [])}
    out.update(int(x["marketId"]) for x in payload.get("selectedModelMarkets", []))
    return out


def _repair_event_lower_bound(rows: list[dict[str, Any]]) -> tuple[set[tuple[int, int, str]], set[int]]:
    events: set[tuple[int, int, str]] = set()
    markets: set[int] = set()
    for row in rows:
        if int(row.get("repair_within_3s") or 0) != 1:
            continue
        if str(row.get("next_taker_purpose") or "").upper() != "REPAIR":
            continue
        delay = base._num(row.get("next_taker_delay_ms"))
        if delay is None or delay < 0 or delay > 3000:
            continue
        mid = int(row["market_id"])
        event_ms = int(row["sample_ms"] + round(delay))
        events.add((mid, event_ms, str(row.get("next_taker_side") or "")))
        markets.add(mid)
    return events, markets


def _positive_fold_summary(control: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    cf = {int(f["holdoutTargetMarketId"]): f for f in control.get("folds", []) if f.get("status") == "OK"}
    rf = {int(f["holdoutTargetMarketId"]): f for f in raw.get("folds", []) if f.get("status") == "OK"}
    rows = []
    for mid in sorted(set(cf) & set(rf)):
        c, r = cf[mid], rf[mid]
        if int(r.get("testPositives") or 0) <= 0:
            continue
        ca, ra = base._num(c.get("auc")), base._num(r.get("auc"))
        cl, rl = base._num(c.get("logLoss")), base._num(r.get("logLoss"))
        if ca is None or ra is None or cl is None or rl is None:
            continue
        rows.append({
            "holdoutTargetMarketId": mid,
            "testPositives": int(r.get("testPositives") or 0),
            "controlAuc": ca, "rawAuc": ra, "deltaAuc": ra-ca,
            "controlLogLoss": cl, "rawLogLoss": rl, "deltaLogLoss": rl-cl,
        })
    n = len(rows)
    both = sum(x["deltaAuc"] > 0 and x["deltaLogLoss"] < 0 for x in rows)
    required = math.ceil(0.60*n) if n else None
    macro_c = sum(x["controlAuc"] for x in rows)/n if n else None
    macro_r = sum(x["rawAuc"] for x in rows)/n if n else None
    mean_dll = sum(x["deltaLogLoss"] for x in rows)/n if n else None
    replicated = bool(
        n >= 5 and macro_c is not None and macro_r is not None and macro_r > macro_c
        and mean_dll is not None and mean_dll < 0 and both >= int(required or 999)
    )
    return {
        "positiveHoldoutFolds": n,
        "foldsBetterOnBothAucAndLogLoss": both,
        "requiredBetterFolds": required,
        "macroControlAuc": macro_c,
        "macroRawAuc": macro_r,
        "macroDeltaAuc": (macro_r-macro_c if macro_r is not None and macro_c is not None else None),
        "meanDeltaLogLossOnPositiveFolds": mean_dll,
        "relativeSignalReplicated": replicated,
        "folds": rows,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.7 unseen-market RAW Prediction REPAIR replication")
    p.add_argument("--previous-report", type=Path, default=DEFAULT_PREVIOUS)
    p.add_argument("--official-db", type=Path, default=v276.DEFAULT_OFFICIAL_DB)
    p.add_argument("--micro-db", type=Path, default=v276.DEFAULT_MICRO_DB)
    p.add_argument("--book-db", type=Path, default=v276.DEFAULT_BOOK_DB)
    p.add_argument("--asset", default="BTC")
    p.add_argument("--cutover", default=v276.hazard.v2.DEFAULT_CUTOVER)
    p.add_argument("--gap-minutes", type=float, default=30.0)
    p.add_argument("--candidate-markets", type=int, default=96)
    p.add_argument("--max-model-markets", type=int, default=16)
    p.add_argument("--min-model-markets", type=int, default=8)
    p.add_argument("--min-model-rows", type=int, default=800)
    p.add_argument("--min-repair-events", type=int, default=10)
    p.add_argument("--min-repair-markets", type=int, default=5)
    p.add_argument("--completion-grace-ms", type=int, default=5000)
    p.add_argument("--idle-gap-ms", type=int, default=1000)
    p.add_argument("--burst-cap-ms", type=int, default=3000)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--states", type=Path, default=DEFAULT_STATES)
    p.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = p.parse_args()

    excluded = _previous_exclusions(args.previous_report)
    raw_events, source_audit = v276.hazard.compat._load_source_compat(args.official_db, "OFFICIAL", str(args.asset).upper())
    events, selection_audit = v276.hazard._select(raw_events, v276.hazard.v2._parse_ms(args.cutover))
    states, candidate_meta, recent_source = v276._build_recent_states(
        events, args.gap_minutes, args.candidate_markets, args.completion_grace_ms,
        args.idle_gap_ms, args.burst_cap_ms,
    )
    candidate_meta = [m for m in candidate_meta if int(m["marketId"]) not in excluded]
    allowed = {int(m["marketId"]) for m in candidate_meta}
    states = [r for r in states if int(r["market_id"]) in allowed]
    states.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    if not states:
        raise RuntimeError("no unseen recent states after excluding V2.7.6/development markets")
    v276.hazard.v2._write_csv(args.states, states, v276.hazard.STATE_FIELDS)

    start_ms, end_ms = min(int(r["sample_ms"]) for r in states), max(int(r["sample_ms"]) for r in states)
    snapshots, _ = base._load_micro(args.micro_db, start_ms, end_ms)
    pred_by_key, pred_audit = v276.v272._load_8778_rows(states, args.book_db)
    joined, join_audit = v276.v272.build_ab_rows(states, snapshots, pred_by_key)
    complete = v273.complete(joined, v276.REQUIRED)
    markets = v276._support_report(candidate_meta, states, joined, complete)
    selected = v276._select_model_markets(markets, args.max_model_markets)
    selected_ids = {int(m["marketId"]) for m in selected}
    model_rows = [r for r in complete if int(r["market_id"]) in selected_ids]
    model_rows.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    base._write_csv(args.rows, model_rows)

    repair_events, repair_markets = _repair_event_lower_bound(model_rows)
    support_ready = (
        len(selected) >= args.min_model_markets and len(model_rows) >= args.min_model_rows
        and len(repair_events) >= args.min_repair_events and len(repair_markets) >= args.min_repair_markets
    )
    result = None
    models = None
    if support_ready:
        control, _ = base.fit_one(model_rows, v276.CONTROL, "REPAIR", 3, "V277__CONTROL", False)
        raw_model, _ = base.fit_one(model_rows, v276.RAW_FEATURES, "REPAIR", 3, "V277__RAW", False)
        pair = v273.pair_summary(control, raw_model)
        positive = _positive_fold_summary(control, raw_model)
        result = {
            "pair": pair,
            "positiveFoldPrimary": positive,
            "rawVsConstant": v276._constant_baseline(raw_model),
        }
        models = {"control": control, "raw": raw_model}
        status = "RAW_RELATIVE_SIGNAL_REPLICATED_UNSEEN_OOS" if positive["relativeSignalReplicated"] else "RAW_RELATIVE_SIGNAL_NOT_REPLICATED_UNSEEN_OOS"
    else:
        status = "INSUFFICIENT_UNSEEN_REPAIR_EVENT_SUPPORT_DO_NOT_FIT"

    report = {
        "version": VERSION,
        "policy": {
            "purpose": "Fresh replication of frozen RAW Prediction -> 3s REPAIR timing after V2.7.6 rejected simple gap orientation.",
            "previousV276SelectedMarketsExcluded": sorted(excluded),
            "candidateMarkets": args.candidate_markets,
            "maxModelMarkets": args.max_model_markets,
            "jointSupportRule": "Same per-market joint-support gate as V2.7.6.",
            "eventGate": f"Before fitting require >= {args.min_model_markets} unseen eligible markets, >= {args.min_model_rows} rows, >= {args.min_repair_events} distinct strict-next REPAIR event timestamps across >= {args.min_repair_markets} markets.",
            "primaryValidation": "LOMO positive-event holdouts: macro AUC delta + mean paired logloss delta; require >=60% folds better on both. Pooled AUC is secondary.",
            "predictionFeaturesFrozen": v276.REQUIRED,
            "noHyperparameterTuning": True,
            "noLiveTradingChanges": True,
        },
        "source": {
            "sourceAudit": source_audit,
            "selectionAudit": selection_audit,
            "recentSession": recent_source,
            "statesAfterExclusion": len(states),
            "microSnapshotsLoaded": len(snapshots),
            "freshMicroJoinedRows": len(joined),
            "jointCompleteRows": len(complete),
            "predictionCoverageDecision": pred_audit.get("decision"),
            "microJoinAudit": dict(join_audit),
        },
        "markets": markets,
        "selectedModelMarkets": selected,
        "modelRows": len(model_rows),
        "repairPositiveSeconds": sum(int(r.get("repair_within_3s") or 0) for r in model_rows),
        "distinctStrictNextRepairEventsLowerBound": len(repair_events),
        "marketsWithStrictNextRepairEvents": len(repair_markets),
        "decision": {
            "status": status,
            "supportReady": support_ready,
            "deployment": "RESEARCH_ONLY_DO_NOT_PROMOTE_FROM_THIS_RESULT" if support_ready else "NO_MODEL_FIT",
        },
        "result": result,
        "models": models,
    }
    out = args.report.expanduser().resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "version": VERSION,
        "decision": report["decision"],
        "selectedMarkets": len(selected),
        "modelRows": len(model_rows),
        "repairEventsLowerBound": len(repair_events),
        "repairEventMarkets": len(repair_markets),
        "report": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
