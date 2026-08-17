from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import audit_target_maker_8778_predict_book_coverage_v2_5b as core

PARENT_TABLE = "maker_book_inference_v21_parent_lifecycles"
MIN_PARENT_CONFIDENCE = 0.70
MIN_PLACEMENT_COVERAGE = 0.80
MIN_FILL_ALLOCATION_COVERAGE = 0.80


@dataclass(frozen=True)
class ParentV21:
    market_id: int
    parent_id: str
    placement_first_ms: int
    first_target_ms: int | None
    target_side: str
    confidence: float | None
    placement_coverage: float | None
    fill_allocation_coverage: float | None
    confidence_pass: bool
    placement_pass: bool
    fill_pass: bool
    include_master: bool
    eligibility_label: str
    filter_reasons: Tuple[str, ...]


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def classify_parent_v21(row: sqlite3.Row) -> ParentV21:
    placement_ms = _int(row["placement_first_ms"])
    if placement_ms is None:
        raise ValueError("placement_first_ms is required")

    first_target_ms = _int(row["first_target_ms"])
    confidence = _num(row["confidence"])
    placement_coverage = _num(row["placement_coverage"])
    fill_coverage = _num(row["fill_allocation_coverage"])

    confidence_pass = confidence is not None and confidence >= MIN_PARENT_CONFIDENCE
    placement_pass = placement_coverage is not None and placement_coverage >= MIN_PLACEMENT_COVERAGE
    fill_pass = fill_coverage is not None and fill_coverage >= MIN_FILL_ALLOCATION_COVERAGE
    clean_timestamp = first_target_ms is not None and first_target_ms >= placement_ms

    reasons: List[str] = []
    if not confidence_pass:
        reasons.append("CONFIDENCE_LT_0_70_OR_MISSING")
    if not placement_pass:
        reasons.append("PLACEMENT_COVERAGE_LT_0_80_OR_MISSING")
    if not fill_pass:
        reasons.append("FILL_ALLOCATION_COVERAGE_LT_0_80_OR_MISSING")
    if not clean_timestamp:
        reasons.append("FIRST_TARGET_BEFORE_PLACEMENT_OR_MISSING")

    eligible = confidence_pass and placement_pass and fill_pass and clean_timestamp
    return ParentV21(
        market_id=int(row["market_id"]),
        parent_id=str(row["parent_id"] or ""),
        placement_first_ms=placement_ms,
        first_target_ms=first_target_ms,
        target_side=str(row["target_side"] or "").upper(),
        confidence=confidence,
        placement_coverage=placement_coverage,
        fill_allocation_coverage=fill_coverage,
        confidence_pass=confidence_pass,
        placement_pass=placement_pass,
        fill_pass=fill_pass,
        include_master=eligible,
        eligibility_label="CLEAN_TARGET" if eligible else "FILTERED",
        filter_reasons=tuple(reasons),
    )


def load_parents_v21(conn: sqlite3.Connection, start_ms: int, end_ms: int) -> List[ParentV21]:
    rows = conn.execute(
        f"""
        SELECT parent_id, market_id, target_side, placement_first_ms, first_target_ms,
               confidence, placement_coverage, fill_allocation_coverage
        FROM {PARENT_TABLE}
        WHERE placement_first_ms IS NOT NULL
          AND placement_first_ms >= ?
          AND placement_first_ms < ?
        ORDER BY placement_first_ms, market_id, parent_id
        """,
        (start_ms, end_ms),
    ).fetchall()
    return [classify_parent_v21(row) for row in rows]


def funnel_counts_v21(parents: Sequence[ParentV21]) -> Dict[str, int]:
    confidence = [p for p in parents if p.confidence_pass]
    placement = [p for p in confidence if p.placement_pass]
    fill = [p for p in placement if p.fill_pass]
    return {
        "RAW_PARENT": len(parents),
        "CONFIDENCE_PASS": len(confidence),
        "PLACEMENT_COVERAGE_PASS": len(placement),
        "FILL_ALLOCATION_PASS": len(fill),
        "CLEAN_TARGET": sum(1 for p in parents if p.include_master),
    }


def main() -> None:
    args = core.parse_args()
    db_path = args.db
    book_db_path = args.book_db or args.db
    core.assert_not_legacy_book_db(book_db_path)

    start_ms = core.iso_to_ms(args.special_start)
    end_ms = core.iso_to_ms(args.special_end)
    if end_ms <= start_ms:
        raise SystemExit("--special-end must be strictly after --special-start")

    lifecycle_conn = core.connect(db_path)
    try:
        core.require_table(lifecycle_conn, PARENT_TABLE)
        parents = load_parents_v21(lifecycle_conn, start_ms, end_ms)
    finally:
        lifecycle_conn.close()

    market_ids = sorted({p.market_id for p in parents})
    book_conn = core.connect(book_db_path)
    try:
        core.require_table(book_conn, core.BOOK_TABLE)
        updates_by_market = core.load_updates(book_conn, market_ids)
    finally:
        book_conn.close()

    # Reuse the already-reviewed V2.5b 8778 replay unchanged; only the V2.1 parent adapter differs.
    core.funnel_counts = funnel_counts_v21

    received_records: List[Tuple[ParentV21, core.ReplayResult]] = []
    source_records: List[Tuple[ParentV21, core.ReplayResult]] = []
    parent_rows: List[Dict[str, Any]] = []

    for parent in parents:
        updates = updates_by_market.get(parent.market_id, [])
        received = core.replay_strict_pre(updates, parent.placement_first_ms, "receivedStrict")
        source = core.replay_strict_pre(updates, parent.placement_first_ms, "sourceStrict")
        received_records.append((parent, received))
        source_records.append((parent, source))
        parent_rows.append(
            {
                "marketId": parent.market_id,
                "parentId": parent.parent_id,
                "placementFirstMs": parent.placement_first_ms,
                "placementFirstIso": core.ms_to_iso(parent.placement_first_ms),
                "firstTargetMs": parent.first_target_ms,
                "firstTargetIso": core.ms_to_iso(parent.first_target_ms),
                "targetSide": parent.target_side,
                "confidence": parent.confidence,
                "placementCoverage": parent.placement_coverage,
                "fillAllocationCoverage": parent.fill_allocation_coverage,
                "eligibilityLabel": parent.eligibility_label,
                "filterReasons": list(parent.filter_reasons),
                "receivedStrict": received.to_json(),
                "sourceStrict": source.to_json(),
            }
        )

    received_all = core.summarize_replays(received_records, clean_only=False)
    received_clean = core.summarize_replays(received_records, clean_only=True)
    source_all = core.summarize_replays(source_records, clean_only=False)
    source_clean = core.summarize_replays(source_records, clean_only=True)

    report = {
        "version": core.REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceContract": {
            "bookSource": core.BOOK_TABLE,
            "bookDb": book_db_path,
            "lifecycleDb": db_path,
            "lifecycleParentTable": PARENT_TABLE,
            "parentSchemaBasis": "existing V2.1/V2.5a repository implementation",
            "legacySnapshotSourceUsed": False,
            "forbiddenLegacyBookDbBasenames": sorted(core.LEGACY_STALE_BOOK_DB_BASENAMES),
            "replayOrder": "maker_book_inference_updates.id ASC (collector insertion/arrival sequence)",
            "receivedStrictPolicy": "contiguous id prefix ending before first received_at_ms >= placement_first_ms",
            "sourceStrictPolicy": "conservative contiguous id prefix ending before first source_timestamp_ms >= placement_first_ms; later timestamp regressions are excluded",
            "note": "V2.5b never reads wallet_taker_signal_snapshots and never source-sorts changes_z rows.",
        },
        "window": {
            "specialStart": args.special_start,
            "specialEnd": args.special_end,
            "startInclusive": True,
            "endExclusive": True,
            "startMs": start_ms,
            "endMs": end_ms,
            "purpose": "pre-noon bounded discovery window; not a claim that every hour is special",
        },
        "eligibility": {
            "minParentConfidence": MIN_PARENT_CONFIDENCE,
            "minPlacementCoverage": MIN_PLACEMENT_COVERAGE,
            "minFillAllocationCoverage": MIN_FILL_ALLOCATION_COVERAGE,
            "cleanTargetRule": "first_target_ms >= placement_first_ms",
        },
        "parentFunnel": funnel_counts_v21(parents),
        "parentFunnelByHour": core.build_hourly_funnel(parents, start_ms, end_ms),
        "reconstructionSummary": {
            "receivedStrict": {"ALL_PARENTS": received_all, "CLEAN_TARGET": received_clean},
            "sourceStrict": {"ALL_PARENTS": source_all, "CLEAN_TARGET": source_clean},
        },
        "reconstructionByHour": core.by_hour_reconstruction(
            parents, received_records, source_records
        ),
        "raw8778CoverageByParentMarket": core.build_raw_market_coverage(
            parents, updates_by_market, start_ms, end_ms
        ),
        "parentReplay": parent_rows,
        "gate": core.gate(received_clean),
        "notes": [
            "Parent schema/thresholds are taken from the repository's existing lifecycle V2.1 and V2.5a implementations, not inferred.",
            "This adapter reuses the existing V2.5b zlib+JSON checkpoint decoding and changes_z replay unchanged.",
            "Checkpoint presence alone is not counted as reconstruction.",
            "receivedStrict remains the primary live-causal gate; sourceStrict remains diagnostic.",
            "The bounded end prevents later Aug-17 cohorts from washing out Aug-16 pre-noon coverage.",
            "No EBM fitting, refit, retune, Binance, Chainlink, or legacy wallet_taker_signal_snapshots data is used here.",
        ],
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "version": core.REPORT_VERSION,
                "out": str(out_path),
                "window": [args.special_start, args.special_end],
                "parentTable": PARENT_TABLE,
                "parents": len(parents),
                "cleanTarget": funnel_counts_v21(parents)["CLEAN_TARGET"],
                "receivedStrictCleanRate": received_clean.get("reconstructableRate"),
                "sourceStrictCleanRate": source_clean.get("reconstructableRate"),
                "gate": report["gate"],
                "legacySnapshotSourceUsed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
