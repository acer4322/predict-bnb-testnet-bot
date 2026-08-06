from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from predict_bot.decision_strategy_shadows import DecisionStrategyTracker


def _store() -> SimpleNamespace:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            topic_id INTEGER,
            market_id INTEGER NOT NULL,
            side TEXT,
            status TEXT,
            entry_price REAL,
            stake REAL,
            pnl REAL,
            fee_rate_bps INTEGER,
            opened_at TEXT,
            closed_at TEXT,
            model_probability REAL,
            model_edge REAL,
            diagnostics_json TEXT
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            start_price REAL,
            spot_price REAL,
            seconds_left REAL
        );
        CREATE TABLE strategy_measurement_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT,
            cutoff_trade_id INTEGER,
            reset_at TEXT
        );
        """
    )
    return SimpleNamespace(db=db)


def test_history_is_added_in_actual_settlement_order() -> None:
    store = _store()
    store.db.executemany(
        """INSERT INTO trades(
               strategy, market_id, side, entry_price, stake, pnl,
               fee_rate_bps, opened_at, closed_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "R_FUTURES_LEAD",
                1,
                "UP",
                0.40,
                5.0,
                1.0,
                200,
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:10:00+00:00",
            ),
            (
                "R_FUTURES_LEAD",
                2,
                "DOWN",
                0.45,
                5.0,
                -5.0,
                200,
                "2026-08-01T00:01:00+00:00",
                "2026-08-01T00:05:00+00:00",
            ),
        ],
    )
    tracker = DecisionStrategyTracker(SimpleNamespace(), store)
    history = tracker._closed_history(
        "R_FUTURES_LEAD",
        "2026-08-01T00:20:00+00:00",
    )
    assert [row["market_id"] for row in history] == [2, 1]


def test_source_context_is_frozen_at_or_before_signal_open() -> None:
    store = _store()
    store.db.executemany(
        """INSERT INTO observations(
               timestamp, market_id, start_price, spot_price, seconds_left
           ) VALUES (?, ?, ?, ?, ?)""",
        [
            ("2026-08-01T00:00:00+00:00", 7, 100.0, 100.0, 299.0),
            ("2026-08-01T00:00:30+00:00", 7, 100.0, 101.0, 269.0),
            ("2026-08-01T00:01:00+00:00", 7, 100.0, 90.0, 239.0),
        ],
    )
    tracker = DecisionStrategyTracker(SimpleNamespace(), store)
    source = {
        "id": 100,
        "strategy": "R_FUTURES_LEAD",
        "market_id": 7,
        "side": "UP",
        "entry_price": 0.40,
        "fee_rate_bps": 200,
        "opened_at": "2026-08-01T00:00:31+00:00",
    }
    context = tracker._source_context(source)
    assert context["available"] is True
    assert context["observationTimestamp"] == "2026-08-01T00:00:30+00:00"
    assert context["startMoveBps"] == 100.0
    assert context["pathEr"] == 1.0
    assert context["context"] == ("EARLY", "ALIGNED", "P_030_055", "ER_HIGH")

    store.db.execute(
        """INSERT INTO observations(
               timestamp, market_id, start_price, spot_price, seconds_left
           ) VALUES (?, ?, ?, ?, ?)""",
        ("2026-08-01T00:02:00+00:00", 7, 100.0, 130.0, 179.0),
    )
    cached = tracker._source_context(source)
    assert cached == context
