from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

from predict_bot import cross_oracle_storage_retention as retention


NOW_MS = 1_900_000_000_000


def _collector() -> SimpleNamespace:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE polymarket_markets (
            slug TEXT PRIMARY KEY,
            window_end_ms INTEGER NOT NULL
        );
        CREATE TABLE polymarket_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            received_wall_ns INTEGER NOT NULL
        );
        CREATE INDEX idx_poly_events_market_received
            ON polymarket_events(market_slug, received_wall_ns);
        CREATE TABLE chainlink_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_wall_ns INTEGER NOT NULL
        );
        """
    )
    return SimpleNamespace(db=db, db_lock=threading.RLock())


def _insert_market(
    collector: SimpleNamespace,
    slug: str,
    *,
    hours_old: float,
    event_count: int,
) -> None:
    window_end_ms = NOW_MS - int(hours_old * 3600 * 1000)
    collector.db.execute(
        "INSERT INTO polymarket_markets(slug, window_end_ms) VALUES (?, ?)",
        (slug, window_end_ms),
    )
    collector.db.executemany(
        "INSERT INTO polymarket_events(market_slug, received_wall_ns) VALUES (?, ?)",
        [(slug, (window_end_ms + 1) * 1_000_000)] * event_count,
    )
    collector.db.commit()


def _event_count(collector: SimpleNamespace, slug: str) -> int:
    return int(
        collector.db.execute(
            "SELECT COUNT(*) FROM polymarket_events WHERE market_slug=?",
            (slug,),
        ).fetchone()[0]
    )


def _configure(monkeypatch, *, batch_rows: int, batches: int) -> None:
    monkeypatch.setattr(retention.time, "time", lambda: NOW_MS / 1000)
    monkeypatch.setattr(retention, "POLY_HOURS", 6.0)
    monkeypatch.setattr(retention, "CHAIN_HOURS", 72.0)
    monkeypatch.setattr(retention, "DEFER_POLY_BACKLOG_CLEANUP", False)
    monkeypatch.setattr(retention, "POLY_DELETE_BATCH_ROWS", batch_rows)
    monkeypatch.setattr(retention, "POLY_DELETE_BATCHES_PER_CLEANUP", batches)


def test_cleanup_skips_already_empty_oldest_market(monkeypatch) -> None:
    collector = _collector()
    try:
        _configure(monkeypatch, batch_rows=4, batches=4)
        _insert_market(collector, "expired-empty", hours_old=12, event_count=0)
        _insert_market(collector, "expired-with-events", hours_old=10, event_count=10)
        _insert_market(collector, "fresh", hours_old=1, event_count=10)

        result = retention._cleanup_once(collector)

        assert _event_count(collector, "expired-empty") == 0
        assert _event_count(collector, "expired-with-events") == 0
        assert _event_count(collector, "fresh") == 10
        assert result["polyEvents"] == 10
        assert result["polyExpiredMarketsRemaining"] == 0
        assert result["polyOldestPendingMarket"] is None
    finally:
        collector.db.close()


def test_cleanup_advances_across_multiple_expired_markets(monkeypatch) -> None:
    collector = _collector()
    try:
        _configure(monkeypatch, batch_rows=2, batches=4)
        _insert_market(collector, "expired-empty", hours_old=14, event_count=0)
        _insert_market(collector, "expired-b", hours_old=12, event_count=2)
        _insert_market(collector, "expired-c", hours_old=10, event_count=2)
        _insert_market(collector, "expired-d", hours_old=8, event_count=2)

        result = retention._cleanup_once(collector)

        assert _event_count(collector, "expired-b") == 0
        assert _event_count(collector, "expired-c") == 0
        assert _event_count(collector, "expired-d") == 0
        assert result["polyEvents"] == 6
        assert result["polyBatches"] == 3
        assert result["polyExpiredMarketsRemaining"] == 0
    finally:
        collector.db.close()


def test_cleanup_respects_batch_budget_and_reports_backlog(monkeypatch) -> None:
    collector = _collector()
    try:
        _configure(monkeypatch, batch_rows=3, batches=2)
        _insert_market(collector, "expired-large", hours_old=12, event_count=10)

        result = retention._cleanup_once(collector)

        assert _event_count(collector, "expired-large") == 4
        assert result["polyEvents"] == 6
        assert result["polyBatches"] == 2
        assert result["polyExpiredMarketsRemaining"] == 1
        assert result["polyOldestPendingMarket"] == "expired-large"
    finally:
        collector.db.close()


def test_storage_metrics_expose_reusable_sqlite_pages() -> None:
    collector = _collector()
    try:
        collector.db.executemany(
            "INSERT INTO chainlink_ticks(received_wall_ns) VALUES (?)",
            [(index,) for index in range(5000)],
        )
        collector.db.commit()
        collector.db.execute("DELETE FROM chainlink_ticks")
        collector.db.commit()

        metrics = retention._storage_metrics(collector)

        assert metrics["pageSizeBytes"] > 0
        assert metrics["pageCount"] > 0
        assert metrics["freelistCount"] >= 0
        assert metrics["dbBytes"] == metrics["pageSizeBytes"] * metrics["pageCount"]
        assert metrics["reusableBytes"] == metrics["pageSizeBytes"] * metrics["freelistCount"]
        assert metrics["liveBytesApprox"] == metrics["dbBytes"] - metrics["reusableBytes"]
        assert 0 <= metrics["reusableFraction"] <= 1
        assert metrics["storageMetricsError"] is None
    finally:
        collector.db.close()


def test_retention_health_reports_healthy_and_catching_up(monkeypatch) -> None:
    collector = _collector()
    try:
        _configure(monkeypatch, batch_rows=10, batches=2)
        monkeypatch.setattr(retention, "CLEANUP_SECONDS", 60.0)
        collector._retention_error = None
        collector._retention_at_ms = NOW_MS
        collector._retention_last = {"polyExpiredMarketsRemaining": 0}

        status, age_ms = retention._retention_health(collector)
        assert status == "HEALTHY"
        assert age_ms == 0

        collector._retention_last = {"polyExpiredMarketsRemaining": 5}
        status, age_ms = retention._retention_health(collector)
        assert status == "CATCHING_UP"
        assert age_ms == 0
    finally:
        collector.db.close()
