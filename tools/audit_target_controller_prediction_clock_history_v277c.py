from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import audit_target_controller_prediction_history_decoupled_v277b as v277b
import analyze_target_controller_prediction_raw_unseen_v277 as v277
import analyze_target_controller_prediction_common_support_v273b as v273

v276 = v277.v276
v272 = v276.v272
base = v276.base
predmod = v272.predmod
bookmod = v272.bookmod
ROOT = base.ROOT
VERSION = "TARGET_CONTROLLER_PREDICTION_CLOCK_HISTORY_V277C"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_prediction_clock_history_v277c_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_prediction_clock_history_v277c_rows.csv"
ANCHOR_OFFSETS_MS = (3000, 2000, 1000, 0)


def _anchor_requests(joined: list[dict[str, Any]]) -> list[dict[str, int]]:
    keys: set[tuple[int, int]] = set()
    for row in joined:
        mid = int(row["market_id"])
        t = int(row["sample_ms"])
        for offset in ANCHOR_OFFSETS_MS:
            keys.add((mid, t - offset))
    return [
        {"market_id": mid, "sample_ms": sample_ms}
        for mid, sample_ms in sorted(keys, key=lambda x: (x[0], x[1]))
    ]


def _load_clock_audit(
    joined: list[dict[str, Any]],
    book_db: Path,
) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[str, Any]]:
    requests = _anchor_requests(joined)
    market_ids = sorted({int(r["market_id"]) for r in requests})
    with predmod._open_ro(book_db) as conn:
        bookmod.require_table(conn, bookmod.BOOK_TABLE)
        updates = bookmod.load_updates(conn, market_ids)
    audited = predmod.audit_rows(requests, updates, predmod.PRIMARY_MODE)
    by_key = {(int(r["market_id"]), int(r["sample_ms"])): dict(r) for r in audited}

    offset_counts = {str(offset): {"rows": 0, "fresh2s": 0} for offset in ANCHOR_OFFSETS_MS}
    for row in joined:
        mid = int(row["market_id"])
        t = int(row["sample_ms"])
        for offset in ANCHOR_OFFSETS_MS:
            slot = offset_counts[str(offset)]
            slot["rows"] += 1
            pred = by_key.get((mid, t - offset))
            if pred and bool(pred.get("fresh_2s")):
                slot["fresh2s"] += 1
    for slot in offset_counts.values():
        slot["fresh2sRate"] = slot["fresh2s"] / slot["rows"] if slot["rows"] else None

    return by_key, {
        "anchorRequests": len(requests),
        "targetMarkets": len(market_ids),
        "freshnessByOffsetMs": offset_counts,
        "retained8778UpdatesByTargetMarket": {
            str(mid): len(updates.get(mid, [])) for mid in market_ids
        },
    }


def _clock_features_for_row(
    source: Mapping[str, Any],
    audited: Mapping[tuple[int, int], Mapping[str, Any]],
) -> dict[str, Any]:
    row = dict(source)
    mid = int(row["market_id"])
    t = int(row["sample_ms"])
    current = audited.get((mid, t))
    current_mid = v272._fresh_mid(current)
    row["micro_prediction_event_age_ms"] = base._num(current.get("latest_age_ms")) if current else None
    row["micro_prediction_up_mid"] = current_mid
    row["micro_prediction_distance_05"] = abs(current_mid - 0.5) if current_mid is not None else None
    row["prediction_8778_reconstructable"] = int(bool(current and current.get("reconstructable")))
    row["prediction_8778_valid_mid"] = int(bool(current and current.get("valid_mid")))
    row["prediction_8778_fresh_2s"] = int(current_mid is not None)
    row["prediction_8778_latest_update_id"] = current.get("latest_update_id") if current else None
    row["prediction_8778_checkpoint_update_id"] = current.get("checkpoint_update_id") if current else None

    points: list[tuple[int, float]] = []
    for offset in ANCHOR_OFFSETS_MS:
        anchor_ms = t - offset
        pred = audited.get((mid, anchor_ms))
        value = v272._fresh_mid(pred)
        if value is not None:
            points.append((anchor_ms, value))
    points.sort()

    delta = None
    pred_range = None
    if current_mid is not None and len(points) >= 2:
        coverage = (points[-1][0] - points[0][0]) / 3000.0
        if coverage >= 0.65:
            delta = points[-1][1] - points[0][1]
            pred_range = max(v for _, v in points) - min(v for _, v in points)

    row["micro_prediction_delta_3s"] = delta
    row["micro_prediction_range_3s"] = pred_range
    pressure = base._num(row.get("micro_book_pressure_mean_3s"))
    row["micro_prediction_pressure_aligned_delta_3s"] = (
        None if delta is None or pressure is None
        else delta * (1 if pressure > 0 else -1 if pressure < 0 else 0)
    )
    return row


def build_clock_rows(
    states: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    book_db: Path,
) -> tuple[list[dict[str, Any]], Any, dict[str, Any]]:
    # Keep the exact current micro strict-past rule; only Prediction history is moved
    # onto deterministic wall-clock anchors independent of Target fixed-grid sparsity.
    joined, audit = base.build_training_rows(states, snapshots, {})
    audited, anchor_audit = _load_clock_audit(joined, book_db)
    return [_clock_features_for_row(r, audited) for r in joined], audit, anchor_audit


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.7c coverage-only audit of 8778 Prediction 3s history on deterministic clock anchors")
    p.add_argument("--previous-report", type=Path, default=v277.DEFAULT_PREVIOUS)
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
    p.add_argument("--completion-grace-ms", type=int, default=5000)
    p.add_argument("--idle-gap-ms", type=int, default=1000)
    p.add_argument("--burst-cap-ms", type=int, default=3000)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = p.parse_args()

    excluded = v277._previous_exclusions(args.previous_report)
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
        raise RuntimeError("no unseen recent states after exclusions")

    start_ms, end_ms = min(int(r["sample_ms"]) for r in states), max(int(r["sample_ms"]) for r in states)
    snapshots, _ = base._load_micro(args.micro_db, start_ms, end_ms)

    # V2.7.7b reference: history built on all Target fixed-grid states.
    pred_by_key, pred_audit = v272._load_8778_rows(states, args.book_db)
    reference_rows, reference_join_audit = v277b.build_decoupled_rows(states, snapshots, pred_by_key)
    reference_complete = v273.complete(reference_rows, v276.REQUIRED)
    reference_markets = v276._support_report(candidate_meta, states, reference_rows, reference_complete)

    clock_rows, clock_join_audit, anchor_audit = build_clock_rows(states, snapshots, args.book_db)
    clock_complete = v273.complete(clock_rows, v276.REQUIRED)
    clock_markets = v276._support_report(candidate_meta, states, clock_rows, clock_complete)
    selected = v276._select_model_markets(clock_markets, args.max_model_markets)
    selected_ids = {int(m["marketId"]) for m in selected}
    selected_rows = [r for r in clock_complete if int(r["market_id"]) in selected_ids]
    selected_rows.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    base._write_csv(args.rows, selected_rows)

    before_eligible = sum(bool(m.get("jointEligible")) for m in reference_markets)
    after_eligible = len(selected)
    support_ready = after_eligible >= args.min_model_markets and len(selected_rows) >= args.min_model_rows
    status = "READY_FOR_V278_EVENT_GATE_AND_RAW_REPLICATION" if support_ready else "INSUFFICIENT_CLOCK_ANCHORED_JOINT_SUPPORT_DO_NOT_FIT"

    report = {
        "version": VERSION,
        "policy": {
            "purpose": "Coverage-only correctness audit: reconstruct frozen Prediction current+3s fields on deterministic t-3s/t-2s/t-1s/t anchors independent of Target fixed-grid sparsity.",
            "why": "V2.7.7b showed micro-decoupling added only 23 complete rows, leaving Target fixed-grid sparsity as the next suspected source of artificial Prediction-history missingness.",
            "exclusions": sorted(excluded),
            "predictionFieldsFrozen": v276.REQUIRED,
            "anchorOffsetsMs": list(ANCHOR_OFFSETS_MS),
            "receivedStrict": True,
            "freshnessMs": 2000,
            "historyCoverageRule": ">=65% span inside the 3s window, unchanged from prior history rule.",
            "sameMicroStrictPastRule": True,
            "noLabelInspection": True,
            "noModelFitting": True,
            "noHyperparameterTuning": True,
            "noLiveTradingChanges": True,
        },
        "source": {
            "sourceAudit": source_audit,
            "selectionAudit": selection_audit,
            "recentSession": recent_source,
            "statesAfterExclusion": len(states),
            "microSnapshotsLoaded": len(snapshots),
            "predictionCoverageDecision": pred_audit.get("decision"),
            "clockAnchorAudit": anchor_audit,
        },
        "beforeV277b": {
            "freshMicroJoinedRows": len(reference_rows),
            "jointCompleteRows": len(reference_complete),
            "eligibleMarkets": before_eligible,
            "microJoinAudit": dict(reference_join_audit),
            "markets": reference_markets,
        },
        "afterClockAnchors": {
            "freshMicroJoinedRows": len(clock_rows),
            "jointCompleteRows": len(clock_complete),
            "eligibleMarkets": after_eligible,
            "selectedRows": len(selected_rows),
            "microJoinAudit": dict(clock_join_audit),
            "markets": clock_markets,
            "selectedMarkets": selected,
        },
        "delta": {
            "jointCompleteRows": len(clock_complete) - len(reference_complete),
            "eligibleMarkets": after_eligible - before_eligible,
        },
        "decision": {
            "status": status,
            "supportReady": support_ready,
            "rule": f">= {args.min_model_markets} unseen joint-eligible markets and >= {args.min_model_rows} selected complete rows; coverage only, no REPAIR labels inspected.",
            "deployment": "NO_MODEL_FIT",
        },
    }
    out = args.report.expanduser().resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "version": VERSION,
        "beforeEligibleMarkets": before_eligible,
        "afterEligibleMarkets": after_eligible,
        "beforeJointCompleteRows": len(reference_complete),
        "afterJointCompleteRows": len(clock_complete),
        "selectedRows": len(selected_rows),
        "decision": report["decision"],
        "report": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
