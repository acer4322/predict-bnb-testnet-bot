from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_8778_predict_book_coverage_v2_5a.json"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
MIN_PARENT_CONFIDENCE = 0.70
MIN_PLACEMENT_COVERAGE = 0.80
MIN_FILL_COVERAGE = 0.80
FRESHNESS_THRESHOLDS_MS = (2_000, 5_000, 10_000, 30_000)
TAIPEI = timezone(timedelta(hours=8))


def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def parse_time_ms(value: str) -> int:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return int(dt.timestamp() * 1000)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


@dataclass
class OrderedUpdates:
    times: list[int]
    source_times: list[int]
    received_times: list[int]
    checkpoint_prefix: list[int | None]

    @classmethod
    def build(cls, rows: list[dict[str, Any]], *, order_key: str) -> "OrderedUpdates":
        ordered = sorted(rows, key=lambda row: (int(row[order_key]), int(row["id"])))
        times: list[int] = []
        source_times: list[int] = []
        received_times: list[int] = []
        checkpoint_prefix: list[int | None] = []
        latest_checkpoint: int | None = None
        for index, row in enumerate(ordered):
            times.append(int(row[order_key]))
            source_times.append(int(row["source_timestamp_ms"]))
            received_times.append(int(row["received_at_ms"]))
            if (
                int(row.get("is_checkpoint") or 0) == 1
                and row.get("native_bids_z") is not None
                and row.get("native_asks_z") is not None
            ):
                latest_checkpoint = index
            checkpoint_prefix.append(latest_checkpoint)
        return cls(
            times=times,
            source_times=source_times,
            received_times=received_times,
            checkpoint_prefix=checkpoint_prefix,
        )

    def strict_pre(self, placement_ms: int) -> dict[str, Any]:
        index = bisect.bisect_left(self.times, int(placement_ms)) - 1
        if index < 0:
            return {
                "priorUpdate": False,
                "reconstructable": False,
                "latestAgeMs": None,
                "checkpointAgeMs": None,
            }
        checkpoint_index = self.checkpoint_prefix[index]
        latest_time = self.times[index]
        if checkpoint_index is None:
            return {
                "priorUpdate": True,
                "reconstructable": False,
                "latestAgeMs": max(0, int(placement_ms) - latest_time),
                "checkpointAgeMs": None,
            }
        return {
            "priorUpdate": True,
            "reconstructable": True,
            "latestAgeMs": max(0, int(placement_ms) - latest_time),
            "checkpointAgeMs": max(0, int(placement_ms) - self.times[checkpoint_index]),
        }


def load_eligible_parents(
    db: sqlite3.Connection,
    *,
    special_start_ms: int,
    min_parent_confidence: float,
    min_placement_coverage: float,
    min_fill_coverage: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    query = """
        SELECT parent_id,market_id,target_side,first_target_ms,placement_first_ms,
               placement_last_ms,expected_parent_shares,placement_coverage,
               fill_allocation_coverage,confidence
          FROM maker_book_inference_v21_parent_lifecycles
         WHERE placement_first_ms IS NOT NULL
           AND placement_first_ms>=?
           AND confidence>=?
           AND placement_coverage>=?
           AND fill_allocation_coverage>=?
         ORDER BY placement_first_ms,parent_id
    """
    for raw in db.execute(
        query,
        (
            int(special_start_ms),
            float(min_parent_confidence),
            float(min_placement_coverage),
            float(min_fill_coverage),
        ),
    ):
        rows.append(dict(raw))
    return rows


def load_updates_by_market(
    db: sqlite3.Connection,
    market_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not market_ids:
        return result
    for start in range(0, len(market_ids), 400):
        batch = market_ids[start:start + 400]
        placeholders = ",".join("?" for _ in batch)
        query = f"""
            SELECT id,market_id,source_timestamp_ms,received_at_ms,is_checkpoint,
                   native_bids_z,native_asks_z
              FROM maker_book_inference_updates
             WHERE market_id IN ({placeholders})
             ORDER BY market_id,source_timestamp_ms,id
        """
        for row in db.execute(query, batch):
            result[int(row["market_id"])].append(dict(row))
    return result


def safe_rate(numerator: int, denominator: int) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def summarize_evaluations(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    total = len(rows)
    prior = sum(bool(row[prefix]["priorUpdate"]) for row in rows)
    reconstructable = sum(bool(row[prefix]["reconstructable"]) for row in rows)
    fresh: dict[str, Any] = {}
    for threshold in FRESHNESS_THRESHOLDS_MS:
        count = sum(
            bool(row[prefix]["reconstructable"])
            and row[prefix]["latestAgeMs"] is not None
            and int(row[prefix]["latestAgeMs"]) <= threshold
            for row in rows
        )
        fresh[f"within{threshold // 1000}s"] = {
            "count": count,
            "rateAll": safe_rate(count, total),
            "rateReconstructable": safe_rate(count, reconstructable),
        }
    latest_ages = sorted(
        int(row[prefix]["latestAgeMs"])
        for row in rows
        if row[prefix]["reconstructable"] and row[prefix]["latestAgeMs"] is not None
    )
    checkpoint_ages = sorted(
        int(row[prefix]["checkpointAgeMs"])
        for row in rows
        if row[prefix]["reconstructable"] and row[prefix]["checkpointAgeMs"] is not None
    )

    def quantiles(values: list[int]) -> dict[str, int | None]:
        if not values:
            return {"p50": None, "p90": None, "p99": None, "max": None}
        def at(q: float) -> int:
            index = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
            return values[index]
        return {"p50": at(0.50), "p90": at(0.90), "p99": at(0.99), "max": values[-1]}

    return {
        "rows": total,
        "rowsWithPriorUpdate": prior,
        "priorUpdateRate": safe_rate(prior, total),
        "reconstructableRows": reconstructable,
        "reconstructableRate": safe_rate(reconstructable, total),
        "freshness": fresh,
        "latestUpdateAgeMs": quantiles(latest_ages),
        "checkpointAgeMs": quantiles(checkpoint_ages),
    }


def evaluate_cohort(
    parents: list[dict[str, Any]],
    source_books: dict[int, OrderedUpdates],
    received_books: dict[int, OrderedUpdates],
) -> list[dict[str, Any]]:
    evaluations: list[dict[str, Any]] = []
    for parent in parents:
        market_id = int(parent["market_id"])
        placement_ms = int(parent["placement_first_ms"])
        source = source_books.get(market_id)
        received = received_books.get(market_id)
        evaluations.append({
            "parentId": str(parent["parent_id"]),
            "marketId": market_id,
            "placementMs": placement_ms,
            "cleanTarget": int(parent["first_target_ms"]) >= placement_ms,
            "sourceStrict": source.strict_pre(placement_ms) if source else {
                "priorUpdate": False, "reconstructable": False, "latestAgeMs": None, "checkpointAgeMs": None,
            },
            "receivedStrict": received.strict_pre(placement_ms) if received else {
                "priorUpdate": False, "reconstructable": False, "latestAgeMs": None, "checkpointAgeMs": None,
            },
        })
    return evaluations


def market_summary(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in evaluations:
        grouped[int(row["marketId"])].append(row)
    result: list[dict[str, Any]] = []
    for market_id, rows in grouped.items():
        source_recon = sum(bool(row["sourceStrict"]["reconstructable"]) for row in rows)
        received_recon = sum(bool(row["receivedStrict"]["reconstructable"]) for row in rows)
        result.append({
            "marketId": market_id,
            "parents": len(rows),
            "sourceReconstructable": source_recon,
            "sourceCoverageRate": safe_rate(source_recon, len(rows)),
            "receivedReconstructable": received_recon,
            "receivedCoverageRate": safe_rate(received_recon, len(rows)),
            "firstPlacementMs": min(int(row["placementMs"]) for row in rows),
            "lastPlacementMs": max(int(row["placementMs"]) for row in rows),
        })
    result.sort(key=lambda row: (
        row["sourceCoverageRate"] if row["sourceCoverageRate"] is not None else -1.0,
        -int(row["parents"]),
        int(row["marketId"]),
    ))
    return result


def hourly_summary(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evaluations:
        dt = datetime.fromtimestamp(int(row["placementMs"]) / 1000, tz=timezone.utc).astimezone(TAIPEI)
        grouped[dt.strftime("%Y-%m-%d %H:00 +08")].append(row)
    result: list[dict[str, Any]] = []
    for hour in sorted(grouped):
        rows = grouped[hour]
        source_recon = sum(bool(row["sourceStrict"]["reconstructable"]) for row in rows)
        received_recon = sum(bool(row["receivedStrict"]["reconstructable"]) for row in rows)
        result.append({
            "hour": hour,
            "parents": len(rows),
            "sourceCoverageRate": safe_rate(source_recon, len(rows)),
            "receivedCoverageRate": safe_rate(received_recon, len(rows)),
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 8778 Predict-book strict-pre coverage for special Maker parents.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--min-parent-confidence", type=float, default=MIN_PARENT_CONFIDENCE)
    parser.add_argument("--min-placement-coverage", type=float, default=MIN_PLACEMENT_COVERAGE)
    parser.add_argument("--min-fill-coverage", type=float, default=MIN_FILL_COVERAGE)
    args = parser.parse_args()

    special_start_ms = parse_time_ms(args.special_start)
    db = connect_readonly(args.db)
    try:
        required = (
            "maker_book_inference_updates",
            "maker_book_inference_v21_parent_lifecycles",
        )
        missing = [table for table in required if not has_table(db, table)]
        if missing:
            raise RuntimeError(f"missing required tables: {', '.join(missing)}")

        parents = load_eligible_parents(
            db,
            special_start_ms=special_start_ms,
            min_parent_confidence=args.min_parent_confidence,
            min_placement_coverage=args.min_placement_coverage,
            min_fill_coverage=args.min_fill_coverage,
        )
        market_ids = sorted({int(row["market_id"]) for row in parents})
        updates_by_market = load_updates_by_market(db, market_ids)

        source_books = {
            market_id: OrderedUpdates.build(rows, order_key="source_timestamp_ms")
            for market_id, rows in updates_by_market.items()
            if rows
        }
        received_books = {
            market_id: OrderedUpdates.build(rows, order_key="received_at_ms")
            for market_id, rows in updates_by_market.items()
            if rows
        }
        evaluations = evaluate_cohort(parents, source_books, received_books)
        clean = [row for row in evaluations if row["cleanTarget"]]

        update_stats = db.execute(
            """SELECT COUNT(*) rows,COUNT(DISTINCT market_id) markets,
                      MIN(source_timestamp_ms) min_source_ms,MAX(source_timestamp_ms) max_source_ms,
                      MIN(received_at_ms) min_received_ms,MAX(received_at_ms) max_received_ms,
                      COALESCE(SUM(is_checkpoint),0) checkpoints
                 FROM maker_book_inference_updates"""
        ).fetchone()

        all_source = summarize_evaluations(evaluations, "sourceStrict")
        clean_source = summarize_evaluations(clean, "sourceStrict")
        all_received = summarize_evaluations(evaluations, "receivedStrict")
        clean_received = summarize_evaluations(clean, "receivedStrict")

        clean_source_rate = clean_source["reconstructableRate"] or 0.0
        all_source_rate = all_source["reconstructableRate"] or 0.0
        if clean and clean_source_rate >= 0.90 and all_source_rate >= 0.90:
            gate = "PASS_8778_PREDICT_BOOK_COVERAGE"
        elif clean and clean_source_rate >= 0.70:
            gate = "PARTIAL_8778_PREDICT_BOOK_COVERAGE"
        else:
            gate = "STOP_8778_PREDICT_BOOK_COVERAGE_INSUFFICIENT"

        markets = market_summary(evaluations)
        report = {
            "version": "TARGET_MAKER_8778_PREDICT_BOOK_COVERAGE_V2_5A",
            "generatedAtMs": now_ms(),
            "database": str(args.db),
            "specialStart": args.special_start,
            "specialStartMs": special_start_ms,
            "eligibility": {
                "minParentConfidence": args.min_parent_confidence,
                "minPlacementCoverage": args.min_placement_coverage,
                "minFillAllocationCoverage": args.min_fill_coverage,
            },
            "semantics": {
                "sourceStrict": "latest 8778 update with source_timestamp_ms < inferred placement_first_ms; requires a retained full checkpoint at or before that update",
                "receivedStrict": "latest 8778 update with received_at_ms < inferred placement_first_ms; stricter live-causal diagnostic",
                "cleanTarget": "first_target_ms >= placement_first_ms",
                "note": "This audit checks whether the Predict book can be reconstructed from 8778 checkpoints+deltas. It does not claim Binance/Chainlink public features are present in the Maker DB.",
            },
            "storage": dict(update_stats) if update_stats is not None else {},
            "eligible": {
                "parents": len(evaluations),
                "markets": len({int(row["marketId"]) for row in evaluations}),
                "cleanParents": len(clean),
                "cleanRate": safe_rate(len(clean), len(evaluations)),
                "marketsWithAnyRetainedUpdates": len(source_books),
            },
            "allEligible": {
                "sourceStrict": all_source,
                "receivedStrict": all_received,
            },
            "cleanTarget": {
                "sourceStrict": clean_source,
                "receivedStrict": clean_received,
            },
            "byHourTaipei": hourly_summary(evaluations),
            "worstMarkets": markets[:30],
            "marketCoverage": markets,
            "gate": gate,
        }

        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        print(json.dumps({
            "ok": True,
            "gate": gate,
            "eligibleParents": len(evaluations),
            "eligibleMarkets": report["eligible"]["markets"],
            "cleanParents": len(clean),
            "sourceCoverageAll": all_source["reconstructableRate"],
            "sourceCoverageClean": clean_source["reconstructableRate"],
            "receivedCoverageAll": all_received["reconstructableRate"],
            "receivedCoverageClean": clean_received["reconstructableRate"],
            "output": str(output),
        }, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
