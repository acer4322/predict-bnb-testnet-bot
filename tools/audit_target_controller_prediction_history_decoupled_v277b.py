from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import analyze_target_controller_prediction_raw_unseen_v277 as v277
import analyze_target_controller_prediction_common_support_v273b as v273

v276 = v277.v276
v272 = v276.v272
base = v276.base
ROOT = base.ROOT
VERSION = "TARGET_CONTROLLER_PREDICTION_HISTORY_DECOUPLED_V277B"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_prediction_history_decoupled_v277b_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_prediction_history_decoupled_v277b_rows.csv"

PRED_COPY_FIELDS = [
    "micro_prediction_event_age_ms",
    "micro_prediction_up_mid",
    "micro_prediction_distance_05",
    "prediction_8778_reconstructable",
    "prediction_8778_valid_mid",
    "prediction_8778_fresh_2s",
    "prediction_8778_latest_update_id",
    "prediction_8778_checkpoint_update_id",
]


def _prediction_feature_map(
    states: list[dict[str, Any]],
    pred_by_key: Mapping[tuple[int, int], Mapping[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    # Critical correctness rule: build Prediction history on the complete fixed-grid
    # state timeline, not on the subset that happened to have a fresh micro snapshot.
    pred_rows = [dict(r) for r in states]
    v272._overlay_current_prediction(pred_rows, pred_by_key)
    v272._prediction_history_features(pred_rows)
    return {(int(r["market_id"]), int(r["sample_ms"])): r for r in pred_rows}


def _apply_decoupled_prediction(
    joined: list[dict[str, Any]],
    feature_map: Mapping[tuple[int, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source in joined:
        row = dict(source)
        pred = feature_map.get((int(row["market_id"]), int(row["sample_ms"]))) or {}
        for field in PRED_COPY_FIELDS:
            row[field] = pred.get(field)
        for history_ms in base.HISTORY_MS:
            suffix = f"{history_ms // 1000}s"
            delta_name = f"micro_prediction_delta_{suffix}"
            range_name = f"micro_prediction_range_{suffix}"
            aligned_name = f"micro_prediction_pressure_aligned_delta_{suffix}"
            delta = base._num(pred.get(delta_name))
            row[delta_name] = delta
            row[range_name] = base._num(pred.get(range_name))
            pressure = base._num(row.get(f"micro_book_pressure_mean_{suffix}"))
            row[aligned_name] = None if delta is None or pressure is None else delta * (1 if pressure > 0 else -1 if pressure < 0 else 0)
        out.append(row)
    return out


def build_decoupled_rows(
    states: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    pred_by_key: Mapping[tuple[int, int], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], Any]:
    # Control/public micro rows still require the exact strict-past <=750 ms micro join.
    joined, audit = base.build_training_rows(states, snapshots, {})
    return _apply_decoupled_prediction(joined, _prediction_feature_map(states, pred_by_key)), audit


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.7b coverage-only audit of Prediction history independent from micro join gaps")
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
    pred_by_key, pred_audit = v272._load_8778_rows(states, args.book_db)

    old_joined, old_join_audit = v272.build_ab_rows(states, snapshots, pred_by_key)
    old_complete = v273.complete(old_joined, v276.REQUIRED)
    old_markets = v276._support_report(candidate_meta, states, old_joined, old_complete)

    new_joined, new_join_audit = build_decoupled_rows(states, snapshots, pred_by_key)
    new_complete = v273.complete(new_joined, v276.REQUIRED)
    new_markets = v276._support_report(candidate_meta, states, new_joined, new_complete)
    selected = v276._select_model_markets(new_markets, args.max_model_markets)
    selected_ids = {int(m["marketId"]) for m in selected}
    selected_rows = [r for r in new_complete if int(r["market_id"]) in selected_ids]
    selected_rows.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    base._write_csv(args.rows, selected_rows)

    old_eligible = sum(bool(m.get("jointEligible")) for m in old_markets)
    new_eligible = len(selected)
    support_ready = new_eligible >= args.min_model_markets and len(selected_rows) >= args.min_model_rows
    status = "READY_FOR_V278_EVENT_GATE_AND_RAW_REPLICATION" if support_ready else "INSUFFICIENT_DECOUPLED_JOINT_SUPPORT_DO_NOT_FIT"

    report = {
        "version": VERSION,
        "policy": {
            "purpose": "Coverage-only correctness audit: reconstruct Prediction current+history on all fixed-grid states before applying the independent microstructure current-row join.",
            "why": "V2.7.2 builder calculated Prediction history after dropping states without fresh micro, so micro gaps could falsely erase otherwise valid Prediction 3s history.",
            "exclusions": sorted(excluded),
            "predictionFieldsFrozen": v276.REQUIRED,
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
        },
        "before": {
            "freshMicroJoinedRows": len(old_joined),
            "jointCompleteRows": len(old_complete),
            "eligibleMarkets": old_eligible,
            "microJoinAudit": dict(old_join_audit),
            "markets": old_markets,
        },
        "after": {
            "freshMicroJoinedRows": len(new_joined),
            "jointCompleteRows": len(new_complete),
            "eligibleMarkets": new_eligible,
            "selectedRows": len(selected_rows),
            "microJoinAudit": dict(new_join_audit),
            "markets": new_markets,
            "selectedMarkets": selected,
        },
        "delta": {
            "jointCompleteRows": len(new_complete) - len(old_complete),
            "eligibleMarkets": new_eligible - old_eligible,
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
        "beforeEligibleMarkets": old_eligible,
        "afterEligibleMarkets": new_eligible,
        "beforeJointCompleteRows": len(old_complete),
        "afterJointCompleteRows": len(new_complete),
        "selectedRows": len(selected_rows),
        "decision": report["decision"],
        "report": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
