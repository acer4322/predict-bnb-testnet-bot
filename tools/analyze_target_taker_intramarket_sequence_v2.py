from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import analyze_target_taker_intramarket_sequence_v1 as v1
from build_target_taker_direct_eligibility_special_regime_official_v2 import (
    DEFAULT_OFFICIAL_TARGET_DB,
    _merge_labels,
)
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB
from predict_bot.target_taker_direct_eligibility_special_regime_v1 import (
    DEFAULT_OUTPUT as DEFAULT_ELIGIBILITY_DATASET,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_intramarket_sequence_v1_report.json"
DEFAULT_EVENTS_CSV = ROOT / "data" / "research" / "target_taker_intramarket_sequence_v1_events.csv"
DEFAULT_RISKSET_CSV = ROOT / "data" / "research" / "target_taker_intramarket_riskset_v1.csv"
REPORT_VERSION = "TARGET_TAKER_INTRAMARKET_SEQUENCE_V2_PUBLIC_OVERLAP_SAFE"


def _event_in_window(event_ms: int, start_ms: int | None, end_ms: int | None) -> bool:
    if start_ms is not None and event_ms < start_ms:
        return False
    if end_ms is not None and event_ms >= end_ms:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstruct Target Taker parent sequences while preserving pre-window parent "
            "state and reporting only events that overlap the public per-second dataset."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_ELIGIBILITY_DATASET)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--official-target-db", type=Path, default=DEFAULT_OFFICIAL_TARGET_DB)
    parser.add_argument("--start", default=None, help="Optional ISO time or epoch ms.")
    parser.add_argument("--end", default=None, help="Optional ISO time or epoch ms; exclusive.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV)
    parser.add_argument("--riskset-csv", type=Path, default=DEFAULT_RISKSET_CSV)
    args = parser.parse_args()

    requested_start_ms = v1._epoch_ms(args.start)
    requested_end_ms = v1._epoch_ms(args.end)
    if (
        requested_start_ms is not None
        and requested_end_ms is not None
        and requested_end_ms <= requested_start_ms
    ):
        raise SystemExit("--end must be after --start")

    print("TARGET_TAKER_INTRAMARKET_SEQUENCE_V2_PUBLIC_OVERLAP_SAFE", flush=True)
    print("[1/5] Merge complete legacy + official Target TAKER parent truth...", flush=True)
    merged, source_stats, _deployed, excluded_market_id = _merge_labels(
        args.shadow_db, args.official_target_db
    )
    all_events = [
        dict(row)
        for row in merged
        if excluded_market_id is None
        or int(row["market_id"]) != int(excluded_market_id)
    ]
    print(f"      complete parent truth={len(all_events):,}", flush=True)

    print("[2/5] Load public per-second eligibility dataset and resolve overlap window...", flush=True)
    full_frame = v1._load_dataset(args.dataset)
    if full_frame.empty:
        raise SystemExit("eligibility dataset is empty")
    public_min_ms = int(full_frame["decision_sampled_at_ms"].min())
    # +1 makes the final sampled millisecond part of the half-open interval.
    public_end_exclusive_ms = int(full_frame["decision_sampled_at_ms"].max()) + 1

    effective_start_ms = max(
        public_min_ms,
        int(requested_start_ms) if requested_start_ms is not None else public_min_ms,
    )
    effective_end_ms = min(
        public_end_exclusive_ms,
        int(requested_end_ms)
        if requested_end_ms is not None
        else public_end_exclusive_ms,
    )
    if effective_end_ms <= effective_start_ms:
        raise SystemExit(
            "requested window has no overlap with the public eligibility dataset"
        )

    frame = full_frame[
        (full_frame["decision_sampled_at_ms"] >= effective_start_ms)
        & (full_frame["decision_sampled_at_ms"] < effective_end_ms)
    ].copy()
    print(
        f"      requested={v1._iso_taipei(requested_start_ms)} -> "
        f"{v1._iso_taipei(requested_end_ms)}",
        flush=True,
    )
    print(
        f"      effective public overlap={v1._iso_taipei(effective_start_ms)} -> "
        f"{v1._iso_taipei(effective_end_ms)} | rows={len(frame):,} "
        f"markets={frame['market_id'].nunique():,}",
        flush=True,
    )

    # IMPORTANT: classify FIRST_ENTRY / REENTRY / FLIP from the complete parent history.
    # We only filter after sequence state has been reconstructed, so a parent just before
    # the requested window still makes a later parent POST-FIRST rather than a false FIRST.
    print("[3/5] Reconstruct full parent state, then retain public-overlap events...", flush=True)
    phase_index = v1._dataset_index(frame)
    full_sequence = v1._sequence_rows(all_events, phase_index)
    source_window_sequence = [
        row
        for row in full_sequence
        if _event_in_window(
            int(row["target_event_ms"]), effective_start_ms, effective_end_ms
        )
    ]
    overlap_sequence = [
        row for row in source_window_sequence if row.get("seconds_left") is not None
    ]
    unmatched = len(source_window_sequence) - len(overlap_sequence)
    print(
        f"      Target parents inside effective time window={len(source_window_sequence):,} | "
        f"public-aligned={len(overlap_sequence):,} | unmatched={unmatched:,}",
        flush=True,
    )

    event_columns = [
        "market_id",
        "event_index",
        "market_event_count",
        "parent_id",
        "order_hash",
        "label_source",
        "target_event_ms",
        "target_event_taipei",
        "side",
        "event_type",
        "gap_from_previous_parent_ms",
        "elapsed_from_first_parent_ms",
        "aligned_public_sample_ms",
        "phase_alignment_delta_ms",
        "seconds_left",
        "phase",
        "macro_phase",
    ]
    v1._write_csv(args.events_csv, overlap_sequence, event_columns)

    print("[4/5] Build FULL-HISTORY per-second retrospective state...", flush=True)
    # The continuous hazard trainer needs historical public rows too. Build one annotation
    # row for every public second, but use only the requested overlap slice in this report.
    riskset_full = v1._riskset_rows(full_frame, all_events)
    riskset_window = [
        row
        for row in riskset_full
        if effective_start_ms <= int(row["decision_sampled_at_ms"]) < effective_end_ms
    ]
    riskset_columns = [
        "market_id",
        "decision_sampled_at_ms",
        "decision_bucket_start_ms",
        "seconds_left",
        "phase",
        "macro_phase",
        "target_parent_count_before_decision",
        "position_state",
        "previous_target_side",
        "previous_target_parent_ms",
        "ms_since_previous_target_parent",
        "next_target_parent_ms",
        "next_target_side",
        "next_target_event_type",
        "ms_to_next_target_parent",
        "label_next_target_taker_any_1s",
        "label_next_target_taker_any_2s",
        "label_next_target_taker_any_5s",
    ]
    v1._write_csv(args.riskset_csv, riskset_full, riskset_columns)
    print(
        f"      full risk-set rows={len(riskset_full):,} | report-window rows={len(riskset_window):,}",
        flush=True,
    )

    print("[5/5] Aggregate reliable OPEN / MID / TAIL behavior...", flush=True)
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "requestedWindow": {
            "startMs": requested_start_ms,
            "startTaipei": v1._iso_taipei(requested_start_ms),
            "endMs": requested_end_ms,
            "endTaipei": v1._iso_taipei(requested_end_ms),
        },
        "publicDatasetCoverage": {
            "startMs": public_min_ms,
            "startTaipei": v1._iso_taipei(public_min_ms),
            "endExclusiveMs": public_end_exclusive_ms,
            "endExclusiveTaipei": v1._iso_taipei(public_end_exclusive_ms),
        },
        "effectivePublicOverlapWindow": {
            "startMs": effective_start_ms,
            "startTaipei": v1._iso_taipei(effective_start_ms),
            "endMs": effective_end_ms,
            "endTaipei": v1._iso_taipei(effective_end_ms),
        },
        "inputs": {
            "dataset": str(args.dataset.expanduser().resolve()),
            "shadowDb": str(args.shadow_db.expanduser().resolve()),
            "officialTargetDb": str(args.official_target_db.expanduser().resolve()),
            "targetSources": source_stats,
        },
        "coverageAccounting": {
            "completeMergedTargetParents": len(all_events),
            "targetParentsInsideEffectiveTimeWindow": len(source_window_sequence),
            "publicAlignedTargetParentsUsedForPhaseStats": len(overlap_sequence),
            "unmatchedTargetParentsExcludedFromPhaseStats": unmatched,
            "fullHistoryRiskSetRowsWritten": len(riskset_full),
            "riskSetRowsInsideReportWindow": len(riskset_window),
            "note": (
                "FIRST_ENTRY / SAME_SIDE_REENTRY / SIDE_FLIP classification is built from "
                "complete parent history first. OPEN/MID/TAIL statistics and replay events "
                "then use only parents alignable to the selected public per-second coverage. "
                "The risk-set CSV intentionally contains full public history for causal model training."
            ),
        },
        "outputs": {
            "eventsCsv": str(args.events_csv.expanduser().resolve()),
            "risksetCsv": str(args.riskset_csv.expanduser().resolve()),
        },
        "phaseDefinition": {
            "fine": [name for _low, _high, name in v1.PHASE_BINS],
            "macro": {
                "OPEN": "seconds_left > 180",
                "MID": "60 < seconds_left <= 180",
                "TAIL": "seconds_left <= 60",
            },
        },
        "events": v1._event_report(overlap_sequence),
        "riskSet": v1._riskset_report(riskset_window),
        "nextResearchStep": (
            "Score frozen16 continuously on every public second and compare opening-only, "
            "first-signal-once, and continuous capture of FIRST_ENTRY, SAME_SIDE_REENTRY, "
            "and SIDE_FLIP by OPEN/MID/TAIL phase."
        ),
    }
    resolved_report = args.report.expanduser().resolve()
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved_report.with_suffix(resolved_report.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved_report)

    summary = report["events"]
    print("\nPUBLIC-OVERLAP INTRAMARKET SUMMARY", flush=True)
    print(
        f"markets={summary.get('markets', 0):,} parents={summary.get('events', 0):,} | "
        f"types={summary.get('eventTypes', {})}",
        flush=True,
    )
    print(f"macro phases={summary.get('macroPhaseCounts', {})}", flush=True)
    print(
        f"same-side share among subsequent={summary.get('sameSideShareAmongSubsequent')}",
        flush=True,
    )
    print(f"events CSV:  {args.events_csv.expanduser().resolve()}", flush=True)
    print(f"riskset CSV: {args.riskset_csv.expanduser().resolve()}", flush=True)
    print(f"report:      {resolved_report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
