from __future__ import annotations

import sqlite3
from pathlib import Path

from predict_bot.cross_oracle_strategy_db_split import bootstrap_strategy_db


def _seed_legacy(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        db.executescript(
            """
            CREATE TABLE polymarket_events (
                id INTEGER PRIMARY KEY,
                market_slug TEXT,
                raw_json TEXT
            );
            CREATE TABLE chainlink_ticks (
                id INTEGER PRIMARY KEY,
                price REAL
            );
            CREATE TABLE cross_oracle_strategy_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                binance_market_id INTEGER NOT NULL
            );
            CREATE TABLE poly_binance_lead_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                binance_market_id INTEGER NOT NULL,
                observed_at_ms INTEGER NOT NULL
            );
            CREATE TABLE poly_chop_guard_state (
                id INTEGER PRIMARY KEY,
                paused INTEGER NOT NULL
            );
            CREATE TABLE poly_quote_canary_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO polymarket_events(id,market_slug,raw_json) VALUES(1,'raw','{}')"
        )
        db.execute("INSERT INTO chainlink_ticks(id,price) VALUES(1,123.0)")
        db.execute(
            "INSERT INTO cross_oracle_strategy_trades(strategy,binance_market_id) VALUES('R_POLY_GAP_SCALP',123)"
        )
        db.execute(
            "INSERT INTO poly_binance_lead_samples(binance_market_id,observed_at_ms) VALUES(123,456)"
        )
        db.execute("INSERT INTO poly_chop_guard_state(id,paused) VALUES(1,0)")
        db.execute(
            "INSERT INTO poly_quote_canary_attempts(strategy) VALUES('R_POLY_GAP_SCALP')"
        )
        db.commit()
    finally:
        db.close()


def test_bootstrap_copies_strategy_tables_but_not_collector_raw(tmp_path: Path) -> None:
    source = tmp_path / "cross_oracle.db"
    destination = tmp_path / "cross_oracle_strategy.db"
    _seed_legacy(source)

    result = bootstrap_strategy_db(
        source_path=source,
        destination_path=destination,
    )

    assert result["status"] == "MIGRATED"
    assert result["rows"] == 4

    db = sqlite3.connect(destination)
    try:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "cross_oracle_strategy_trades" in tables
        assert "poly_binance_lead_samples" in tables
        assert "poly_chop_guard_state" in tables
        assert "poly_quote_canary_attempts" in tables
        assert "polymarket_events" not in tables
        assert "chainlink_ticks" not in tables
        assert db.execute(
            "SELECT COUNT(*) FROM poly_binance_lead_samples"
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_bootstrap_is_one_time(tmp_path: Path) -> None:
    source = tmp_path / "cross_oracle.db"
    destination = tmp_path / "cross_oracle_strategy.db"
    _seed_legacy(source)

    first = bootstrap_strategy_db(
        source_path=source,
        destination_path=destination,
    )
    second = bootstrap_strategy_db(
        source_path=source,
        destination_path=destination,
    )

    assert first["status"] == "MIGRATED"
    assert second["status"] == "ALREADY_SPLIT"
