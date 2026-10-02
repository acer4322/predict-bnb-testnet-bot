from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MICRO_DB = ROOT / "data" / "microstructure.db"
DEFAULT_CROSS_DB = ROOT / "data" / "cross_oracle.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "poly_market_indexed_history_v2_report.json"
DEFAULT_START = "2026-08-16T03:40:00+08:00"
DEFAULT_END = "2026-08-16T11:35:00+08:00"
REPORT_VERSION = "POLY_MARKET_INDEXED_HISTORY_V2"


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _epoch_ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _iso_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _quote_ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _open_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _index_prefix(conn: sqlite3.Connection, table: str, prefix: list[str]) -> dict[str, Any] | None:
    for row in conn.execute(f"PRAGMA index_list({_quote_ident(table)})"):
        index_name = str(row[1])
        cols = [str(info[2]) for info in conn.execute(f"PRAGMA index_info({_quote_ident(index_name)})")]
        if cols[: len(prefix)] == prefix:
            return {"name": index_name, "columns": cols}
    return None


def _first_last_lead(
    conn: sqlite3.Connection,
    index_name: str,
    market_id: int,
    start_ms: int,
    end_ms: int,
    descending: bool = False,
) -> dict[str, Any] | None:
    direction = "DESC" if descending else "ASC"
    row = conn.execute(
        f"""
        SELECT observed_at_ms, poly_received_at_ms, binance_observed_at_ms,
               poly_market_slug, poly_up_mid, binance_up_mid,
               seconds_left_skew, binance_book_age_ms, binance_book_skew_ms
        FROM poly_binance_lead_samples INDEXED BY {_quote_ident(index_name)}
        WHERE binance_market_id = ? AND observed_at_ms >= ? AND observed_at_ms < ?
        ORDER BY observed_at_ms {direction}
        LIMIT 1
        """,
        (int(market_id), int(start_ms), int(end_ms)),
    ).fetchone()
    return dict(row) if row is not None else None


def _nearest_lead_at_or_after(
    conn: sqlite3.Connection,
    index_name: str,
    market_id: int,
    target_ms: int,
    end_ms: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT observed_at_ms, poly_received_at_ms, poly_market_slug,
               poly_up_mid, binance_up_mid
        FROM poly_binance_lead_samples INDEXED BY {_quote_ident(index_name)}
        WHERE binance_market_id = ? AND observed_at_ms >= ? AND observed_at_ms < ?
        ORDER BY observed_at_ms ASC
        LIMIT 1
        """,
        (int(market_id), int(target_ms), int(end_ms)),
    ).fetchone()
    return dict(row) if row is not None else None


def _bounded_count(
    conn: sqlite3.Connection,
    index_name: str,
    market_id: int,
    start_ms: int,
    end_ms: int,
    cap: int,
) -> tuple[int, bool]:
    limit = int(cap) + 1
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT 1
            FROM poly_binance_lead_samples INDEXED BY {_quote_ident(index_name)}
            WHERE binance_market_id = ? AND observed_at_ms >= ? AND observed_at_ms < ?
            LIMIT ?
        )
        """,
        (int(market_id), int(start_ms), int(end_ms), limit),
    ).fetchone()
    n = int(row[0]) if row is not None else 0
    return min(n, int(cap)), n > int(cap)


def probe(
    micro_db: Path,
    cross_db: Path,
    *,
    start: str,
    end: str,
    count_cap_per_market: int = 10_000,
) -> dict[str, Any]:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    start_ms = _epoch_ms(start_dt)
    end_ms = _epoch_ms(end_dt)
    start_ns = _epoch_ns(start_dt)
    end_ns = _epoch_ns(end_dt)

    micro = _open_ro(micro_db)
    cross = _open_ro(cross_db)
    try:
        micro_index = _index_prefix(micro, "microstructure_snapshots", ["timestamp_ns"])
        lead_index = _index_prefix(cross, "poly_binance_lead_samples", ["binance_market_id", "observed_at_ms"])
        if micro_index is None:
            raise RuntimeError("microstructure_snapshots needs a timestamp_ns-leftmost index")
        if lead_index is None:
            raise RuntimeError("poly_binance_lead_samples needs (binance_market_id, observed_at_ms) index")

        market_rows = micro.execute(
            f"""
            SELECT market_id, MIN(timestamp_ns) AS first_ns, MAX(timestamp_ns) AS last_ns, COUNT(*) AS rows_in_window
            FROM microstructure_snapshots INDEXED BY {_quote_ident(micro_index['name'])}
            WHERE timestamp_ns >= ? AND timestamp_ns < ? AND market_id IS NOT NULL
            GROUP BY market_id
            ORDER BY first_ns ASC
            """,
            (start_ns, end_ns),
        ).fetchall()

        markets: list[dict[str, Any]] = []
        markets_with_poly = 0
        markets_covering_envelope = 0
        earliest_poly_ms: int | None = None
        latest_poly_ms: int | None = None

        for row in market_rows:
            market_id = int(row["market_id"])
            micro_first_ms = int(row["first_ns"]) // 1_000_000
            micro_last_ms = int(row["last_ns"]) // 1_000_000
            first = _first_last_lead(cross, lead_index["name"], market_id, start_ms, end_ms, False)
            last = _first_last_lead(cross, lead_index["name"], market_id, start_ms, end_ms, True)
            bounded_count, capped = _bounded_count(
                cross, lead_index["name"], market_id, start_ms, end_ms, count_cap_per_market
            )

            has_poly = first is not None and last is not None
            if has_poly:
                markets_with_poly += 1
                fms = int(first["observed_at_ms"])
                lms = int(last["observed_at_ms"])
                earliest_poly_ms = fms if earliest_poly_ms is None else min(earliest_poly_ms, fms)
                latest_poly_ms = lms if latest_poly_ms is None else max(latest_poly_ms, lms)
                covers_envelope = fms <= micro_first_ms + 10_000 and lms >= micro_last_ms - 10_000
                if covers_envelope:
                    markets_covering_envelope += 1
            else:
                covers_envelope = False

            probes: list[dict[str, Any]] = []
            span = max(1, micro_last_ms - micro_first_ms)
            for fraction in (0.10, 0.25, 0.50, 0.75, 0.90):
                target_ms = micro_first_ms + int(span * fraction)
                sample = _nearest_lead_at_or_after(
                    cross, lead_index["name"], market_id, target_ms, min(end_ms, micro_last_ms + 1)
                )
                probes.append(
                    {
                        "fraction": fraction,
                        "targetMs": target_ms,
                        "sample": sample,
                        "lagMs": None if sample is None else int(sample["observed_at_ms"]) - target_ms,
                    }
                )

            markets.append(
                {
                    "marketId": market_id,
                    "microRowsInWindow": int(row["rows_in_window"]),
                    "microFirstMs": micro_first_ms,
                    "microLastMs": micro_last_ms,
                    "microFirstUtc": _iso_ms(micro_first_ms),
                    "microLastUtc": _iso_ms(micro_last_ms),
                    "polyLeadHasRows": has_poly,
                    "polyLeadBoundedRowCount": bounded_count,
                    "polyLeadRowCountCapped": capped,
                    "polyLeadCountCap": int(count_cap_per_market),
                    "polyLeadFirst": first,
                    "polyLeadLast": last,
                    "polyLeadCoversMarketEnvelope10s": covers_envelope,
                    "polyLeadFirstOffsetFromMicroMs": None if first is None else int(first["observed_at_ms"]) - micro_first_ms,
                    "polyLeadLastOffsetFromMicroMs": None if last is None else micro_last_ms - int(last["observed_at_ms"]),
                    "polyLeadPointProbes": probes,
                }
            )

        total_markets = len(markets)
        return {
            "reportVersion": REPORT_VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "window": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "startEpochMs": start_ms,
                "endEpochMs": end_ms,
            },
            "sources": {
                "microstructureDb": str(micro_db),
                "crossOracleDb": str(cross_db),
                "microIndex": micro_index,
                "polyLeadIndex": lead_index,
            },
            "summary": {
                "marketsInMicrostructure": total_markets,
                "marketsWithPolyLeadSamples": markets_with_poly,
                "marketCoverageRate": None if total_markets == 0 else markets_with_poly / total_markets,
                "marketsCoveringMicroEnvelopeWithin10s": markets_covering_envelope,
                "envelopeCoverageRate": None if total_markets == 0 else markets_covering_envelope / total_markets,
                "earliestPolyLeadObservedMs": earliest_poly_ms,
                "latestPolyLeadObservedMs": latest_poly_ms,
                "earliestPolyLeadObservedUtc": _iso_ms(earliest_poly_ms),
                "latestPolyLeadObservedUtc": _iso_ms(latest_poly_ms),
            },
            "markets": markets,
            "guardrails": {
                "readOnly": True,
                "queryOnly": True,
                "microWindowUsesTimestampLeftmostIndex": True,
                "polyLeadQueriesUseCompositeMarketTimeIndex": True,
                "noFullTableScan": True,
                "boundedCountCapPerMarket": int(count_cap_per_market),
            },
            "paperResearchOnly": True,
            "noModelFit": True,
            "noStrategyPromotion": True,
        }
    finally:
        micro.close()
        cross.close()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely probe per-market Poly mid history through composite indexes.")
    parser.add_argument("--micro-db", type=Path, default=DEFAULT_MICRO_DB)
    parser.add_argument("--cross-db", type=Path, default=DEFAULT_CROSS_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--count-cap-per-market", type=int, default=10_000)
    args = parser.parse_args()

    report = probe(
        args.micro_db,
        args.cross_db,
        start=args.start,
        end=args.end,
        count_cap_per_market=max(100, int(args.count_cap_per_market)),
    )
    _write_json(args.report, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
