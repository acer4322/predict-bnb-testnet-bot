#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

DEFAULT_DATABASES = [
    Path("data/target_wallet_official_v1.db"),
    Path("data/target_taker_public_side_test_v1.db"),
    Path("data/predict_wallet_shadow.db"),
]

TABLE_HINTS = {
    "trade": 7,
    "fill": 7,
    "event": 6,
    "activity": 6,
    "transaction": 5,
    "taker": 5,
    "wallet": 4,
    "history": 4,
    "order": 3,
    "execution": 5,
    "position": 2,
    "official": 2,
    "raw": 1,
}

COLUMN_HINTS = {
    "market_id": 6,
    "marketid": 6,
    "condition_id": 4,
    "conditionid": 4,
    "parent_id": 4,
    "trade_id": 5,
    "event_id": 5,
    "transaction_hash": 3,
    "tx_hash": 3,
    "side": 5,
    "outcome": 4,
    "role": 5,
    "maker": 5,
    "taker": 6,
    "action": 5,
    "event_type": 5,
    "trade_type": 5,
    "wallet": 4,
    "address": 4,
    "proxy_wallet": 4,
    "funder": 3,
    "price": 2,
    "size": 2,
    "amount": 2,
    "shares": 2,
}

TIMESTAMP_EXACT = {
    "event_ms",
    "event_time_ms",
    "event_timestamp_ms",
    "timestamp_ms",
    "created_at_ms",
    "updated_at_ms",
    "sampled_at_ms",
    "observed_at_ms",
    "detected_at_ms",
    "received_at_ms",
    "executed_at_ms",
    "filled_at_ms",
    "time_ms",
    "ts_ms",
    "timestamp",
    "created_at",
    "updated_at",
    "event_time",
    "event_timestamp",
    "observed_at",
    "detected_at",
    "executed_at",
    "filled_at",
    "time",
    "ts",
}

INTEREST_RE = re.compile(
    r"(?:^|_)(?:id|market|condition|parent|trade|event|order|tx|hash|"
    r"side|outcome|role|maker|taker|action|type|wallet|address|funder|"
    r"price|size|amount|shares|qty|quantity|time|timestamp|created|"
    r"updated|sampled|observed|detected|executed|filled)(?:_|$)",
    re.I,
)


@dataclass
class Column:
    cid: int
    name: str
    decl_type: str
    notnull: bool
    default: Any
    pk: int


@dataclass
class TableProbe:
    table: str
    score: int
    columns: list[Column]
    indexes: dict[str, list[str]]
    sample_rows: list[dict[str, Any]]
    timestamp_findings: list[dict[str, Any]]
    special_coverage_sampled: bool


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only probe for candidate Target-wallet event SQLite sources. "
            "It inventories schemas and samples recent rows without mutating live DBs."
        )
    )
    parser.add_argument(
        "--special-start",
        default="2026-08-16T12:00:00+08:00",
        help="Special-regime start as ISO-8601 timestamp.",
    )
    parser.add_argument(
        "--db",
        action="append",
        dest="dbs",
        help="SQLite DB path. Repeat to override the default candidate list.",
    )
    parser.add_argument(
        "--max-tables",
        type=int,
        default=12,
        help="Maximum candidate tables to inspect per DB (default: 12).",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=5,
        help="Recent rows to sample from each candidate table (default: 5).",
    )
    parser.add_argument(
        "--all-tables",
        action="store_true",
        help="Inspect all user tables instead of only positively-scored candidates.",
    )
    return parser.parse_args()


def parse_iso_ms(text: str) -> int:
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return int(dt.timestamp() * 1000)


def normalize_epoch_ms(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            number = float(raw)
        except ValueError:
            try:
                iso = raw
                if iso.endswith("Z"):
                    iso = iso[:-1] + "+00:00"
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None

    if not math.isfinite(number) or number <= 0:
        return None

    magnitude = abs(number)
    if magnitude >= 1e18:
        return int(number / 1_000_000)
    if magnitude >= 1e15:
        return int(number / 1_000)
    if magnitude >= 1e12:
        return int(number)
    if magnitude >= 1e9:
        return int(number * 1000)
    return None


def format_taipei(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TAIPEI).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def compact_value(value: Any, max_len: int = 180) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<BLOB {len(value)} bytes>"
    if isinstance(value, str) and len(value) > max_len:
        return value[: max_len - 3] + "..."
    return value


def connect_read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 2000")
    return conn


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[Column]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [
        Column(
            cid=int(row["cid"]),
            name=str(row["name"]),
            decl_type=str(row["type"] or ""),
            notnull=bool(row["notnull"]),
            default=row["dflt_value"],
            pk=int(row["pk"]),
        )
        for row in rows
    ]


def table_indexes(conn: sqlite3.Connection, table: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    try:
        rows = conn.execute(f"PRAGMA index_list({quote_ident(table)})").fetchall()
    except sqlite3.DatabaseError:
        return result

    for row in rows:
        index_name = str(row["name"])
        try:
            info = conn.execute(f"PRAGMA index_info({quote_ident(index_name)})").fetchall()
            result[index_name] = [str(item["name"]) for item in info if item["name"] is not None]
        except sqlite3.DatabaseError:
            result[index_name] = []
    return result


def timestamp_columns(columns: Iterable[Column]) -> list[str]:
    result: list[str] = []
    for col in columns:
        lower = col.name.lower()
        if (
            lower in TIMESTAMP_EXACT
            or lower.endswith(("_at_ms", "_time_ms", "_timestamp_ms", "_ts_ms"))
            or "timestamp" in lower
            or re.search(r"(?:^|_)(?:created|updated|sampled|observed|detected|executed|filled)_at(?:_|$)", lower)
        ):
            result.append(col.name)
    return result


def score_table(table: str, columns: list[Column]) -> int:
    name = table.lower()
    score = 0
    for token, points in TABLE_HINTS.items():
        if token in name:
            score += points

    col_names = {col.name.lower() for col in columns}
    for token, points in COLUMN_HINTS.items():
        if token in col_names:
            score += points

    if timestamp_columns(columns):
        score += 3
    if {"side", "price"} <= col_names:
        score += 3
    if any("market" in c for c in col_names) and any(
        x in col_names for x in ("side", "outcome", "action", "role", "taker", "maker")
    ):
        score += 4
    return score


def interesting_columns(columns: list[Column], max_cols: int = 18) -> list[str]:
    timestamp_set = set(timestamp_columns(columns))
    preferred: list[str] = []
    fallback: list[str] = []

    for col in columns:
        lower = col.name.lower()
        if col.name in timestamp_set or INTEREST_RE.search(lower):
            preferred.append(col.name)
        else:
            fallback.append(col.name)

    names = preferred[:max_cols]
    if len(names) < min(8, len(columns)):
        for name in fallback:
            if name not in names:
                names.append(name)
            if len(names) >= min(max_cols, len(columns)):
                break
    return names


def sample_recent_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: list[Column],
    limit: int,
) -> list[dict[str, Any]]:
    selected = interesting_columns(columns)
    if not selected:
        return []

    projection = ", ".join(quote_ident(name) for name in selected)
    qtable = quote_ident(table)
    queries = [
        f"SELECT rowid AS __rowid__, {projection} FROM {qtable} ORDER BY rowid DESC LIMIT ?",
        f"SELECT {projection} FROM {qtable} LIMIT ?",
    ]
    for sql in queries:
        try:
            rows = conn.execute(sql, (limit,)).fetchall()
            return [
                {key: compact_value(row[key]) for key in row.keys()}
                for row in rows
            ]
        except sqlite3.DatabaseError:
            continue
    return []


def indexed_columns(indexes: dict[str, list[str]]) -> set[str]:
    return {column for columns in indexes.values() for column in columns}


def find_timestamp_evidence(
    conn: sqlite3.Connection,
    table: str,
    columns: list[Column],
    indexes: dict[str, list[str]],
    samples: list[dict[str, Any]],
    special_start_ms: int,
) -> tuple[list[dict[str, Any]], bool]:
    findings: list[dict[str, Any]] = []
    found_special = False
    indexed = indexed_columns(indexes)
    tcols = timestamp_columns(columns)

    for name in tcols:
        observed: list[tuple[Any, int]] = []
        for row in samples:
            if name not in row:
                continue
            ms = normalize_epoch_ms(row[name])
            if ms is not None:
                observed.append((row[name], ms))

        finding: dict[str, Any] = {
            "column": name,
            "indexed": name in indexed,
            "recentSampleRaw": observed[0][0] if observed else None,
            "recentSampleTaipei": format_taipei(observed[0][1]) if observed else None,
            "recentSampleAfterSpecialStart": any(ms >= special_start_ms for _, ms in observed),
        }

        if finding["recentSampleAfterSpecialStart"]:
            found_special = True

        if name in indexed:
            try:
                qtable = quote_ident(table)
                qcol = quote_ident(name)
                row = conn.execute(
                    f"SELECT {qcol} AS value FROM {qtable} "
                    f"WHERE {qcol} IS NOT NULL ORDER BY {qcol} DESC LIMIT 1"
                ).fetchone()
                raw = row["value"] if row else None
                ms = normalize_epoch_ms(raw)
                finding["indexedNewestRaw"] = compact_value(raw)
                finding["indexedNewestTaipei"] = format_taipei(ms)
                finding["indexedNewestAfterSpecialStart"] = (
                    ms is not None and ms >= special_start_ms
                )
                if finding["indexedNewestAfterSpecialStart"]:
                    found_special = True
            except sqlite3.DatabaseError as exc:
                finding["indexedNewestError"] = str(exc)

        findings.append(finding)

    return findings, found_special


def probe_table(
    conn: sqlite3.Connection,
    table: str,
    score: int,
    columns: list[Column],
    sample_limit: int,
    special_start_ms: int,
) -> TableProbe:
    indexes = table_indexes(conn, table)
    samples = sample_recent_rows(conn, table, columns, sample_limit)
    timestamp_findings, special = find_timestamp_evidence(
        conn,
        table,
        columns,
        indexes,
        samples,
        special_start_ms,
    )
    return TableProbe(
        table=table,
        score=score,
        columns=columns,
        indexes=indexes,
        sample_rows=samples,
        timestamp_findings=timestamp_findings,
        special_coverage_sampled=special,
    )


def classify_truth_likelihood(probe: TableProbe) -> str:
    names = {col.name.lower() for col in probe.columns}
    table = probe.table.lower()

    truth_signals = 0
    model_signals = 0
    if any(token in table for token in ("trade", "fill", "activity", "event", "transaction")):
        truth_signals += 2
    if any(name in names for name in ("trade_id", "transaction_hash", "tx_hash", "event_id")):
        truth_signals += 2
    if any(name in names for name in ("taker", "maker", "role", "side", "outcome")):
        truth_signals += 1
    if any(name in names for name in ("wallet", "address", "proxy_wallet", "funder")):
        truth_signals += 1

    if any(token in table for token in ("prediction", "score", "signal", "model", "test", "paper")):
        model_signals += 2
    if any(
        key in name
        for name in names
        for key in ("prob", "prediction", "score", "model", "feature", "label")
    ):
        model_signals += 1

    if truth_signals >= model_signals + 2:
        return "LIKELY_OBSERVED_EVENT_TRUTH"
    if model_signals >= truth_signals + 2:
        return "LIKELY_MODEL_OR_EVALUATION_DATA"
    return "AMBIGUOUS_INSPECT_SAMPLES"


def print_probe(path: Path, probe: TableProbe) -> None:
    marker = "  <<< SPECIAL-COVERAGE CANDIDATE" if probe.special_coverage_sampled else ""
    print(f"\n  TABLE: {probe.table}  score={probe.score}{marker}")
    print(f"    classification: {classify_truth_likelihood(probe)}")

    col_text = ", ".join(
        f"{c.name}:{c.decl_type or '?'}{' PK' if c.pk else ''}"
        for c in probe.columns
    )
    print(f"    columns ({len(probe.columns)}): {col_text}")

    if probe.indexes:
        print("    indexes:")
        for name, cols in probe.indexes.items():
            print(f"      - {name}: {cols}")
    else:
        print("    indexes: none/undetected")

    if probe.timestamp_findings:
        print("    timestamp evidence:")
        for item in probe.timestamp_findings:
            newest = item.get("indexedNewestTaipei") or item.get("recentSampleTaipei")
            after = bool(
                item.get("indexedNewestAfterSpecialStart")
                or item.get("recentSampleAfterSpecialStart")
            )
            source = "indexed newest" if item.get("indexedNewestTaipei") else "recent row sample"
            print(
                f"      - {item['column']}: {newest or 'unparsed'} "
                f"[{source}; indexed={item['indexed']}; afterSpecial={after}]"
            )
    else:
        print("    timestamp evidence: no recognizable timestamp column")

    if probe.sample_rows:
        print("    recent rows (compact):")
        for row in probe.sample_rows:
            print("      " + json.dumps(row, ensure_ascii=False, default=str))
    else:
        print("    recent rows: unavailable/empty")


def main() -> int:
    args = parse_args()
    try:
        special_start_ms = parse_iso_ms(args.special_start)
    except ValueError as exc:
        raise SystemExit(f"invalid --special-start: {exc}") from exc

    db_paths = [Path(item) for item in args.dbs] if args.dbs else DEFAULT_DATABASES

    print("TARGET_WALLET_EVENT_SOURCE_PROBE_V1")
    print(f"specialStart:       {args.special_start}")
    print(f"specialStartMs:     {special_start_ms}")
    print(f"specialStartTaipei: {format_taipei(special_start_ms)}")
    print("mode:               READ ONLY")
    print(
        "note: unindexed multi-GB timestamp columns are NOT full-scanned; "
        "coverage is inferred from cheap recent-row samples unless an index is available."
    )

    any_candidate = False

    for path in db_paths:
        print("\n" + "=" * 100)
        print(f"DB: {path}")
        print(f"exists: {path.exists()}")
        if not path.exists():
            continue

        try:
            size_mb = path.stat().st_size / (1024 * 1024)
            modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
            print(f"sizeMB: {size_mb:.2f}")
            print(f"fileModifiedLocal: {modified}")
        except OSError as exc:
            print(f"stat warning: {exc}")

        try:
            conn = connect_read_only(path)
        except sqlite3.Error as exc:
            print(f"OPEN FAILED: {exc}")
            continue

        try:
            tables = list_tables(conn)
            print(f"user tables: {len(tables)}")
            print("table names: " + (", ".join(tables) if tables else "(none)"))

            ranked: list[tuple[int, str, list[Column]]] = []
            for table in tables:
                try:
                    cols = table_columns(conn, table)
                    ranked.append((score_table(table, cols), table, cols))
                except sqlite3.DatabaseError as exc:
                    print(f"  schema warning {table}: {exc}")

            ranked.sort(key=lambda item: (-item[0], item[1]))
            if not args.all_tables:
                positive = [item for item in ranked if item[0] > 0]
                chosen = positive[: max(args.max_tables, 1)]
                if not chosen:
                    chosen = ranked[: max(args.max_tables, 1)]
            else:
                chosen = ranked

            print(
                f"inspecting {len(chosen)} candidate tables "
                f"(use --all-tables if the relevant table is not shown)"
            )

            for score, table, cols in chosen:
                try:
                    probe = probe_table(
                        conn,
                        table,
                        score,
                        cols,
                        max(1, args.sample_rows),
                        special_start_ms,
                    )
                    print_probe(path, probe)
                    if probe.special_coverage_sampled:
                        any_candidate = True
                except sqlite3.DatabaseError as exc:
                    print(f"\n  TABLE: {table} score={score}")
                    print(f"    PROBE FAILED: {exc}")
        finally:
            conn.close()

    print("\n" + "=" * 100)
    if any_candidate:
        print(
            "RESULT: at least one table has cheap/read-only evidence of rows at or after "
            "the special start. Paste this output back so we can map it into the Taker label adapter."
        )
        return 0

    print(
        "RESULT: no candidate showed sampled/indexed timestamp coverage after the special start. "
        "This is not proof of absence for unindexed/out-of-order tables; rerun with --all-tables "
        "if needed."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
