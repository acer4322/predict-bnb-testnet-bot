from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import inspect_poly_legacy_databases_v1 as mod


def test_detect_timestamp_encoding_seconds_ms_us_ns() -> None:
    assert mod.detect_timestamp_encoding([1_786_147_801])["unit"] == "seconds"
    assert mod.detect_timestamp_encoding([1_786_147_801_234])["unit"] == "milliseconds"
    assert mod.detect_timestamp_encoding([1_786_147_801_234_000])["unit"] == "microseconds"
    assert mod.detect_timestamp_encoding([1_786_147_801_234_000_000])["unit"] == "nanoseconds"


def test_classify_columns_finds_poly_price_identity_and_time() -> None:
    cols = [
        {"name": "source_timestamp_ms"},
        {"name": "market_slug"},
        {"name": "token_id"},
        {"name": "outcome"},
        {"name": "best_bid"},
        {"name": "best_ask"},
        {"name": "last_trade"},
    ]
    groups = mod.classify_columns(cols)
    assert "source_timestamp_ms" in groups["timestamp"]
    assert {"market_slug", "token_id", "outcome"}.issubset(groups["identity"])
    assert {"best_bid", "best_ask", "last_trade"}.issubset(groups["price"])


def test_inspector_uses_indexed_timestamp_and_skips_unindexed_range_probe(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE polymarket_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            token_id TEXT,
            outcome TEXT,
            best_bid REAL,
            best_ask REAL,
            last_trade REAL,
            source_timestamp_ms INTEGER
        );
        CREATE INDEX idx_poly_source ON polymarket_events(source_timestamp_ms);
        CREATE TABLE unindexed_market (
            ts_ms INTEGER,
            market_id TEXT,
            bid REAL,
            ask REAL
        );
        """
    )
    stress_ms = mod._iso_ms("2026-08-16T04:00:00+08:00")
    ordinary_ms = mod._iso_ms("2026-08-17T04:00:00+08:00")
    conn.executemany(
        "INSERT INTO polymarket_events(market_slug, token_id, outcome, best_bid, best_ask, last_trade, source_timestamp_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("btc-a", "up-a", "UP", 0.49, 0.51, 0.50, stress_ms),
            ("btc-b", "down-b", "DOWN", 0.45, 0.47, 0.46, ordinary_ms),
        ],
    )
    conn.execute(
        "INSERT INTO unindexed_market(ts_ms, market_id, bid, ask) VALUES (?, ?, ?, ?)",
        (stress_ms, "x", 0.4, 0.6),
    )
    conn.commit()
    conn.close()

    windows = [
        mod._window_payload(
            "stress", "2026-08-16T03:40:00+08:00", "2026-08-16T11:35:00+08:00"
        ),
        mod._window_payload(
            "ordinary", "2026-08-17T03:40:00+08:00", "2026-08-17T11:35:00+08:00"
        ),
    ]
    report = mod.inspect_database(db, windows, sample_limit=2, count_cap=10)
    assert report["status"] == "OK"
    by_name = {row["name"]: row for row in report["tables"]}
    poly = by_name["polymarket_events"]
    diag = poly["timestampDiagnostics"]["source_timestamp_ms"]
    assert diag["safeRangeProbe"] is True
    assert diag["windows"]["stress"]["hasRows"] is True
    assert diag["windows"]["stress"]["boundedRowCount"] == 1
    assert diag["windows"]["ordinary"]["hasRows"] is True
    slow = by_name["unindexed_market"]
    slow_diag = slow["timestampDiagnostics"]["ts_ms"]
    assert slow_diag["safeRangeProbe"] is False
    assert slow_diag["windows"]["stress"]["status"] == "SKIPPED_UNINDEXED"


def test_read_only_open_does_not_modify_database(tmp_path: Path) -> None:
    db = tmp_path / "readonly.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ticks(ts_ms INTEGER PRIMARY KEY, price REAL)")
    conn.execute(
        "INSERT INTO ticks VALUES (?, ?)",
        (mod._iso_ms("2026-08-16T04:00:00+08:00"), 0.5),
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()
    windows = [
        mod._window_payload(
            "stress", "2026-08-16T03:40:00+08:00", "2026-08-16T11:35:00+08:00"
        )
    ]
    mod.inspect_database(db, windows, sample_limit=1, count_cap=10)
    after = db.read_bytes()
    assert after == before
