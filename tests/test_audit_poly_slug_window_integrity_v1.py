from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_poly_slug_window_integrity_v1 as mod


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_slug_window_and_boundaries() -> None:
    start, end = mod._slug_window("btc-updown-5m-1786848900")
    assert start == 1786848900000
    assert end == 1786849200000
    rows = [
        {"market_slug": "btc-updown-5m-1786848900", "source_timestamp_ms": start, "outcome": "UP", "event_type": "book"},
        {"market_slug": "btc-updown-5m-1786848900", "source_timestamp_ms": end - 1, "outcome": "DOWN", "event_type": "book"},
        {"market_slug": "btc-updown-5m-1786848900", "source_timestamp_ms": end, "outcome": "UP", "event_type": "price_change"},
    ]
    audited, summary = mod._audit_rows(rows)
    assert audited[0]["insideWindowRows"] == 2
    assert audited[0]["afterWindowRows"] == 1
    assert audited[0]["firstLateMs"] == end
    assert summary["insideWindowRows"] == 2
    assert summary["afterWindowRows"] == 1


def test_expected_slugs_cover_partial_edge_windows() -> None:
    start = 1786849083247
    end = 1786851299985
    slugs = mod._expected_slugs(start, end)
    assert slugs[0] == "btc-updown-5m-1786848900"
    assert slugs[-1] == "btc-updown-5m-1786851000"
    assert len(slugs) == 8


def test_source_timestamp_index_is_required(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE polymarket_events(id INTEGER PRIMARY KEY, source_timestamp_ms INTEGER, market_slug TEXT)")
    db.execute("CREATE INDEX wrong_idx ON polymarket_events(market_slug, source_timestamp_ms)")
    db.commit()
    db.close()
    ro = mod._open_ro(path)
    try:
        try:
            mod._require_source_index(ro)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unindexed source_timestamp_ms should be rejected")
    finally:
        ro.close()


def test_read_only_point_lookups_do_not_change_db(tmp_path: Path) -> None:
    path = tmp_path / "cross.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE polymarket_events(
            id INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            outcome TEXT,
            event_type TEXT,
            best_bid REAL,
            best_ask REAL,
            last_trade REAL,
            source_timestamp_ms INTEGER
        );
        CREATE INDEX idx_poly_events_source ON polymarket_events(source_timestamp_ms);
        CREATE TABLE polymarket_markets(
            slug TEXT PRIMARY KEY,
            market_id TEXT,
            window_start_ms INTEGER,
            window_end_ms INTEGER,
            discovered_at_ms INTEGER
        );
        """
    )
    slug = "btc-updown-5m-1000"
    db.execute("INSERT INTO polymarket_events(market_slug,event_type,source_timestamp_ms) VALUES (?,?,?)", (slug, "book", 1_000_001))
    db.execute("INSERT INTO polymarket_markets VALUES (?,?,?,?,?)", (slug, "1", 1_000_000, 1_300_000, 999_000))
    db.commit()
    db.close()

    before = _digest(path)
    ro = mod._open_ro(path)
    try:
        idx = mod._require_source_index(ro)
        assert idx["columns"][0] == "source_timestamp_ms"
        rows, truncated = mod._load_events(ro, 1_000_000, 1_300_000, 100)
        assert not truncated and len(rows) == 1
        markets = mod._market_point_lookup(ro, [slug])
        assert slug in markets
    finally:
        ro.close()
    after = _digest(path)
    assert before == after
