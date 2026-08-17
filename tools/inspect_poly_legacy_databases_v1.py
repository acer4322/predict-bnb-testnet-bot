from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "poly_legacy_db_inspector_v1_report.json"
REPORT_VERSION = "POLY_LEGACY_DB_INSPECTOR_V1"
DEFAULT_DATABASES = (
    ROOT / "data" / "microstructure.db",
    ROOT / "data" / "cross_oracle.db",
)
DEFAULT_WINDOWS = (
    ("stress_2026_08_16", "2026-08-16T03:40:00+08:00", "2026-08-16T11:35:00+08:00"),
    ("ordinary_2026_08_17", "2026-08-17T03:40:00+08:00", "2026-08-17T11:35:00+08:00"),
)
TIMESTAMP_TOKENS = (
    "timestamp", "event_ms", "event_ns", "event_time", "received_wall_ns",
    "received_at", "created_at", "updated_at", "observed_at", "time_ms", "time_ns",
    "ts_ms", "ts_ns", "epoch", "time", "ts",
)
PRICE_TOKENS = (
    "best_bid", "best_ask", "bid_price", "ask_price", "last_trade", "last_price",
    "mid_price", "midpoint", "mid", "price", "probability", "up_price", "down_price",
    "yes_price", "no_price", "bid", "ask",
)
IDENTITY_TOKENS = (
    "market_slug", "market_id", "condition_id", "token_id", "outcome", "side",
    "asset", "symbol", "slug", "market", "condition", "token",
)
BOOK_TOKENS = ("bids_json", "asks_json", "orderbook", "book", "bids", "asks")


def _qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt


def _iso_ms(value: str) -> int:
    return int(_parse_iso(value).timestamp() * 1000)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def detect_timestamp_encoding(values: Iterable[Any]) -> dict[str, Any]:
    numeric: list[float] = []
    iso_count = 0
    samples = list(values)
    for value in samples:
        number = _finite_number(value)
        if number is not None and number > 0:
            numeric.append(abs(number))
            continue
        if isinstance(value, str):
            try:
                _parse_iso(value)
                iso_count += 1
            except ValueError:
                pass
    if numeric:
        median = sorted(numeric)[len(numeric) // 2]
        if median < 10_000_000_000:
            unit = "seconds"
            multiplier = 1_000
        elif median < 10_000_000_000_000:
            unit = "milliseconds"
            multiplier = 1
        elif median < 10_000_000_000_000_000:
            unit = "microseconds"
            multiplier = 0.001
        else:
            unit = "nanoseconds"
            multiplier = 0.000001
        return {
            "kind": "numeric_epoch",
            "unit": unit,
            "toEpochMsMultiplier": multiplier,
            "sampleCount": len(numeric),
        }
    if iso_count:
        return {
            "kind": "iso8601_text",
            "unit": None,
            "toEpochMsMultiplier": None,
            "sampleCount": iso_count,
        }
    return {
        "kind": "unknown",
        "unit": None,
        "toEpochMsMultiplier": None,
        "sampleCount": len(samples),
    }


def _epoch_ms_to_raw(epoch_ms: int, encoding: dict[str, Any]) -> int | float | None:
    if encoding.get("kind") != "numeric_epoch":
        return None
    unit = encoding.get("unit")
    if unit == "seconds":
        return epoch_ms / 1000.0
    if unit == "milliseconds":
        return int(epoch_ms)
    if unit == "microseconds":
        return int(epoch_ms) * 1000
    if unit == "nanoseconds":
        return int(epoch_ms) * 1_000_000
    return None


def _raw_to_epoch_ms(value: Any, encoding: dict[str, Any]) -> int | None:
    number = _finite_number(value)
    if number is None:
        if encoding.get("kind") == "iso8601_text" and isinstance(value, str):
            try:
                return int(_parse_iso(value).timestamp() * 1000)
            except ValueError:
                return None
        return None
    unit = encoding.get("unit")
    if unit == "seconds":
        return int(number * 1000)
    if unit == "milliseconds":
        return int(number)
    if unit == "microseconds":
        return int(number / 1000)
    if unit == "nanoseconds":
        return int(number / 1_000_000)
    return None


def _matches(name: str, tokens: Iterable[str]) -> bool:
    lower = name.lower()
    return lower in tokens or any(token in lower for token in tokens if len(token) >= 4)


def classify_columns(columns: list[dict[str, Any]]) -> dict[str, list[str]]:
    names = [str(row["name"]) for row in columns]
    return {
        "timestamp": [name for name in names if _matches(name, TIMESTAMP_TOKENS)],
        "price": [name for name in names if _matches(name, PRICE_TOKENS)],
        "identity": [name for name in names if _matches(name, IDENTITY_TOKENS)],
        "orderbook": [name for name in names if _matches(name, BOOK_TOKENS)],
    }


def _indexed_timestamp_columns(
    columns: list[dict[str, Any]], indexes: list[dict[str, Any]], timestamp_columns: list[str]
) -> dict[str, dict[str, Any]]:
    wanted = set(timestamp_columns)
    out: dict[str, dict[str, Any]] = {}
    for row in columns:
        name = str(row["name"])
        if name in wanted and int(row.get("pk") or 0) > 0:
            out[name] = {"safeRangeProbe": True, "via": "PRIMARY_KEY"}
    for index in indexes:
        cols = index.get("columns") or []
        if not cols:
            continue
        first = cols[0]
        if first in wanted:
            out[first] = {
                "safeRangeProbe": True,
                "via": "INDEX_LEFTMOST",
                "index": index.get("name"),
                "indexColumns": cols,
            }
    return out


def _candidate_score(
    table_name: str,
    classified: dict[str, list[str]],
    safe_timestamps: dict[str, dict[str, Any]],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    lower = table_name.lower()
    if "poly" in lower or "predict" in lower or "market" in lower:
        score += 3
        reasons.append("table name looks market/Poly related")
    prices = {name.lower() for name in classified["price"]}
    if any("best_bid" in name or name == "bid" for name in prices):
        score += 2
        reasons.append("bid field")
    if any("best_ask" in name or name == "ask" for name in prices):
        score += 2
        reasons.append("ask field")
    if any("last_trade" in name or "last_price" in name for name in prices):
        score += 1
        reasons.append("last-trade field")
    if any("mid" in name for name in prices):
        score += 1
        reasons.append("mid field")
    identities = {name.lower() for name in classified["identity"]}
    if any("token" in name for name in identities):
        score += 1
        reasons.append("token identity")
    if any("outcome" in name or name == "side" for name in identities):
        score += 1
        reasons.append("outcome/side identity")
    if any("market" in name or "slug" in name or "condition" in name for name in identities):
        score += 1
        reasons.append("market identity")
    if safe_timestamps:
        score += 2
        reasons.append("indexed timestamp supports bounded window probes")
    if classified["orderbook"]:
        score += 1
        reasons.append("order-book field")
    return score, reasons


def _read_table_schema(conn: sqlite3.Connection, table_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns = [dict(row) for row in conn.execute(f"PRAGMA table_info({_qident(table_name)})")]
    indexes: list[dict[str, Any]] = []
    for idx_row in conn.execute(f"PRAGMA index_list({_qident(table_name)})"):
        idx = dict(idx_row)
        idx_name = str(idx["name"])
        idx["columns"] = [
            str(row["name"]) for row in conn.execute(f"PRAGMA index_info({_qident(idx_name)})")
            if row["name"] is not None
        ]
        indexes.append(idx)
    return columns, indexes


def _sample_table(
    conn: sqlite3.Connection,
    table_name: str,
    selected_columns: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not selected_columns or limit <= 0:
        return []
    cols = ", ".join(_qident(name) for name in selected_columns)
    sql = f"SELECT {cols} FROM {_qident(table_name)} LIMIT ?"
    return [dict(row) for row in conn.execute(sql, (int(limit),))]


def _sample_timestamp_values(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    *,
    indexed: bool,
    limit: int = 5,
) -> list[Any]:
    table = _qident(table_name)
    col = _qident(column_name)
    if indexed:
        sql = f"SELECT {col} AS value FROM {table} WHERE {col} IS NOT NULL ORDER BY {col} ASC LIMIT ?"
    else:
        sql = f"SELECT {col} AS value FROM {table} LIMIT ?"
    return [row["value"] for row in conn.execute(sql, (int(limit),)) if row["value"] is not None]


def _probe_numeric_window(
    conn: sqlite3.Connection,
    table_name: str,
    timestamp_column: str,
    encoding: dict[str, Any],
    window: dict[str, Any],
    count_cap: int,
) -> dict[str, Any]:
    start_ms = int(window["startEpochMs"])
    end_ms = int(window["endEpochMs"])
    raw_start = _epoch_ms_to_raw(start_ms, encoding)
    raw_end = _epoch_ms_to_raw(end_ms, encoding)
    if raw_start is None or raw_end is None:
        return {"status": "SKIPPED_UNSUPPORTED_TIMESTAMP_ENCODING"}
    table = _qident(table_name)
    col = _qident(timestamp_column)
    where = f"{col} >= ? AND {col} < ?"
    first_sql = f"SELECT {col} AS value FROM {table} WHERE {where} ORDER BY {col} ASC LIMIT 1"
    last_sql = f"SELECT {col} AS value FROM {table} WHERE {where} ORDER BY {col} DESC LIMIT 1"
    first = conn.execute(first_sql, (raw_start, raw_end)).fetchone()
    last = conn.execute(last_sql, (raw_start, raw_end)).fetchone()
    cap_plus_one = max(2, int(count_cap) + 1)
    count_sql = f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM {table} WHERE {where} LIMIT ?)"
    bounded_n = int(conn.execute(count_sql, (raw_start, raw_end, cap_plus_one)).fetchone()["n"])
    capped = bounded_n > count_cap
    estimate = count_cap if capped else bounded_n
    return {
        "status": "OK",
        "hasRows": first is not None,
        "boundedRowCount": estimate,
        "rowCountCapped": capped,
        "rowCountCap": int(count_cap),
        "firstRawTimestamp": None if first is None else first["value"],
        "lastRawTimestamp": None if last is None else last["value"],
        "firstEpochMs": None if first is None else _raw_to_epoch_ms(first["value"], encoding),
        "lastEpochMs": None if last is None else _raw_to_epoch_ms(last["value"], encoding),
    }


def inspect_database(
    path: Path,
    windows: list[dict[str, Any]],
    *,
    sample_limit: int = 3,
    count_cap: int = 25_000,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    result: dict[str, Any] = {
        "path": str(path),
        "resolvedPath": str(resolved),
        "exists": resolved.exists(),
    }
    if not resolved.exists():
        result["status"] = "MISSING"
        return result
    result["fileSizeBytes"] = resolved.stat().st_size
    uri_path = quote(resolved.as_posix(), safe="/:")
    uri = f"file:{uri_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
    except sqlite3.Error as exc:
        result.update(status="OPEN_ERROR", error=str(exc))
        return result

    try:
        result["sqliteVersion"] = conn.execute("SELECT sqlite_version() AS version").fetchone()["version"]
        table_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables: list[dict[str, Any]] = []
        for table_row in table_rows:
            table_name = str(table_row["name"])
            try:
                columns, indexes = _read_table_schema(conn, table_name)
                classified = classify_columns(columns)
                safe_timestamps = _indexed_timestamp_columns(columns, indexes, classified["timestamp"])
                score, reasons = _candidate_score(table_name, classified, safe_timestamps)
                selected: list[str] = []
                for group in ("timestamp", "identity", "price", "orderbook"):
                    for name in classified[group]:
                        if name not in selected:
                            selected.append(name)
                selected = selected[:16]
                samples = _sample_table(conn, table_name, selected, sample_limit)
                timestamp_diagnostics: dict[str, Any] = {}
                for ts_name in classified["timestamp"]:
                    is_safe = ts_name in safe_timestamps
                    ts_values = _sample_timestamp_values(
                        conn, table_name, ts_name, indexed=is_safe, limit=5
                    )
                    encoding = detect_timestamp_encoding(ts_values)
                    ts_diag: dict[str, Any] = {
                        "safeRangeProbe": is_safe,
                        "indexEvidence": safe_timestamps.get(ts_name),
                        "encoding": encoding,
                        "sampleValues": ts_values[:5],
                        "windows": {},
                    }
                    for window in windows:
                        if not is_safe:
                            ts_diag["windows"][window["name"]] = {"status": "SKIPPED_UNINDEXED"}
                        elif encoding.get("kind") != "numeric_epoch":
                            ts_diag["windows"][window["name"]] = {
                                "status": "SKIPPED_UNSUPPORTED_TIMESTAMP_ENCODING"
                            }
                        else:
                            ts_diag["windows"][window["name"]] = _probe_numeric_window(
                                conn, table_name, ts_name, encoding, window, count_cap
                            )
                    timestamp_diagnostics[ts_name] = ts_diag
                tables.append(
                    {
                        "name": table_name,
                        "candidateScore": score,
                        "candidateReasons": reasons,
                        "columns": columns,
                        "indexes": indexes,
                        "classifiedColumns": classified,
                        "safeTimestampColumns": safe_timestamps,
                        "timestampDiagnostics": timestamp_diagnostics,
                        "sample": samples,
                    }
                )
            except sqlite3.Error as exc:
                tables.append({"name": table_name, "status": "TABLE_INSPECTION_ERROR", "error": str(exc)})
        tables.sort(key=lambda row: (-int(row.get("candidateScore") or 0), str(row.get("name") or "")))
        result["tables"] = tables
        result["candidateTables"] = [
            {
                "name": row["name"],
                "score": row.get("candidateScore", 0),
                "reasons": row.get("candidateReasons", []),
                "priceColumns": row.get("classifiedColumns", {}).get("price", []),
                "identityColumns": row.get("classifiedColumns", {}).get("identity", []),
                "safeTimestampColumns": list((row.get("safeTimestampColumns") or {}).keys()),
            }
            for row in tables
            if int(row.get("candidateScore") or 0) > 0
        ]
        result["status"] = "OK"
        result["safety"] = {
            "sqliteMode": "read-only",
            "queryOnly": True,
            "unindexedWindowQueries": False,
            "boundedCountCap": int(count_cap),
            "sampleLimitPerTable": int(sample_limit),
        }
        return result
    finally:
        conn.close()


def _window_payload(name: str, start: str, end: str) -> dict[str, Any]:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if end_dt <= start_dt:
        raise ValueError(f"window {name!r}: end must be after start")
    return {
        "name": str(name),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "startEpochMs": int(start_dt.timestamp() * 1000),
        "endEpochMs": int(end_dt.timestamp() * 1000),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only, low-load inspection of legacy SQLite DBs for Poly market-price history."
    )
    parser.add_argument(
        "--db",
        action="append",
        default=None,
        help="SQLite DB path. Repeat for multiple DBs. Defaults to data/microstructure.db and data/cross_oracle.db.",
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--sample-limit", type=int, default=3)
    parser.add_argument("--count-cap", type=int, default=25_000)
    parser.add_argument(
        "--window",
        action="append",
        nargs=3,
        metavar=("NAME", "START_ISO", "END_ISO"),
        help="Coverage window. If supplied at least once, the two default windows are replaced.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_paths = [Path(value) for value in args.db] if args.db else list(DEFAULT_DATABASES)
    raw_windows = args.window if args.window else list(DEFAULT_WINDOWS)
    windows = [_window_payload(name, start, end) for name, start, end in raw_windows]
    sample_limit = max(0, min(int(args.sample_limit), 10))
    count_cap = max(1, min(int(args.count_cap), 1_000_000))
    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "purpose": "Locate safely-queryable Polymarket price history in legacy SQLite databases.",
        "windows": windows,
        "databases": [
            inspect_database(path, windows, sample_limit=sample_limit, count_cap=count_cap)
            for path in db_paths
        ],
        "guardrails": {
            "readOnly": True,
            "noDatabaseCopies": True,
            "noWrites": True,
            "noUnindexedWindowQueries": True,
            "noFullTableCount": True,
            "boundedCountsOnly": True,
            "note": "An unindexed timestamp is reported but never range-scanned by this tool.",
        },
        "paperResearchOnly": True,
        "noModelFit": True,
        "noStrategyPromotion": True,
    }
    _write_json(Path(args.report), report)
    print(f"wrote {Path(args.report)}")
    for db in report["databases"]:
        print(f"{db['path']}: {db['status']}")
        for candidate in (db.get("candidateTables") or [])[:8]:
            print(
                "  "
                f"{candidate['name']} score={candidate['score']} "
                f"prices={','.join(candidate['priceColumns']) or '-'} "
                f"safe_ts={','.join(candidate['safeTimestampColumns']) or '-'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
