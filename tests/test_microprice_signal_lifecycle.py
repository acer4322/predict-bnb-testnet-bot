from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from predict_bot.microprice_signal_lifecycle import (
    MICROPRICE_LIFECYCLE_STRATEGY,
    MicropriceSignalLifecycleTracker,
)


class FakeStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                topic_id INTEGER NOT NULL,
                market_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_price REAL NOT NULL,
                target_price REAL,
                exit_price REAL,
                stake REAL NOT NULL,
                shares REAL NOT NULL,
                fees REAL NOT NULL,
                fee_rate_bps INTEGER NOT NULL,
                pnl REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                note TEXT NOT NULL,
                strategy_version TEXT,
                diagnostics_json TEXT
            )"""
        )
        self.db.commit()

    def config(self) -> dict[str, bool]:
        return {"strategy_r_microprice_enabled": True}

    def open_trade(
        self,
        *,
        strategy: str,
        topic_id: int,
        market_id: int,
        side: str,
        entry: float,
        target: float | None,
        stake: float,
        fee_rate_bps: int,
        note: str,
        strategy_version: str | None = None,
        diagnostics: dict | None = None,
        **_: object,
    ) -> None:
        shares = stake / entry
        fees = shares * min(entry, 1 - entry) * fee_rate_bps / 10_000
        self.db.execute(
            """INSERT INTO trades(
                strategy, topic_id, market_id, side, status, entry_price,
                target_price, stake, shares, fees, fee_rate_bps, opened_at,
                note, strategy_version, diagnostics_json
            ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, '2026-08-04T00:00:00+00:00', ?, ?, ?)""",
            (
                strategy,
                topic_id,
                market_id,
                side,
                entry,
                target,
                stake,
                shares,
                fees,
                fee_rate_bps,
                note,
                strategy_version,
                json.dumps(diagnostics or {}),
            ),
        )
        self.db.commit()


def snapshot(*, mode: str = "up", seconds_left: float = 180.0) -> dict:
    if mode == "up":
        sizes = {
            "up_bid_size": 1000.0,
            "up_ask_size": 100.0,
            "down_bid_size": 100.0,
            "down_ask_size": 1000.0,
        }
    elif mode == "down":
        sizes = {
            "up_bid_size": 100.0,
            "up_ask_size": 1000.0,
            "down_bid_size": 1000.0,
            "down_ask_size": 100.0,
        }
    else:
        sizes = {
            "up_bid_size": 50.0,
            "up_ask_size": 50.0,
            "down_bid_size": 50.0,
            "down_ask_size": 50.0,
        }
    return {
        "topic_id": 7,
        "market_id": 77,
        "seconds_left": seconds_left,
        "up_bid": 0.40,
        "up_ask": 0.41,
        "down_bid": 0.58,
        "down_ask": 0.59,
        "book_age_ms": 20.0,
        "book_skew_ms": 0.0,
        **sizes,
    }


def context(sequence: int, wall_ns: int) -> dict:
    return {
        "trigger_source": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "received_wall_ns": wall_ns,
        "signal_event_sequence": f"prediction:{sequence}",
    }


def tracker() -> tuple[MicropriceSignalLifecycleTracker, FakeStore]:
    store = FakeStore()
    engine = SimpleNamespace(
        prediction_event={
            "prediction_data_source": "dual_token_rest",
            "direct_outcome_books": True,
        }
    )
    return MicropriceSignalLifecycleTracker(engine, store), store


def test_pending_order_is_cancelled_when_edge_disappears() -> None:
    lifecycle, store = tracker()
    base = 1_000_000_000
    lifecycle.process(snapshot(), 200, context(1, base))
    lifecycle.process(snapshot(), 200, context(2, base + 150_000_000))
    lifecycle.process(snapshot(), 200, context(3, base + 350_000_000))
    assert lifecycle.active is not None
    assert lifecycle.active["status"] == "PENDING"

    lifecycle.process(snapshot(mode="neutral"), 200, context(4, base + 450_000_000))

    row = store.db.execute("SELECT * FROM microprice_signal_episodes").fetchone()
    assert row["status"] == "CANCELLED"
    assert row["end_reason"] == "EDGE_LOST"
    assert row["signal_duration_ms"] == pytest.approx(450.0)
    assert store.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_pending_order_is_cancelled_on_reversal() -> None:
    lifecycle, store = tracker()
    base = 2_000_000_000
    lifecycle.process(snapshot(), 200, context(1, base))
    lifecycle.process(snapshot(), 200, context(2, base + 150_000_000))
    lifecycle.process(snapshot(), 200, context(3, base + 350_000_000))
    lifecycle.process(snapshot(mode="down"), 200, context(4, base + 450_000_000))

    row = store.db.execute(
        "SELECT * FROM microprice_signal_episodes ORDER BY id ASC LIMIT 1"
    ).fetchone()
    assert row["status"] == "CANCELLED"
    assert row["end_reason"] == "SIGNAL_REVERSED"
    assert store.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_filled_trade_exits_when_edge_disappears() -> None:
    lifecycle, store = tracker()
    base = 3_000_000_000
    lifecycle.process(snapshot(), 200, context(1, base))
    lifecycle.process(snapshot(), 200, context(2, base + 150_000_000))
    lifecycle.process(snapshot(), 200, context(3, base + 350_000_000))
    opened = lifecycle.process(snapshot(), 200, context(4, base + 650_000_000))
    assert [item["strategy"] for item in opened] == [MICROPRICE_LIFECYCLE_STRATEGY]

    lifecycle.process(snapshot(mode="neutral"), 200, context(5, base + 800_000_000))

    episode = store.db.execute("SELECT * FROM microprice_signal_episodes").fetchone()
    trade = store.db.execute("SELECT * FROM trades").fetchone()
    assert episode["status"] == "EXITED"
    assert episode["end_reason"] == "EDGE_LOST"
    assert episode["position_duration_ms"] == pytest.approx(150.0)
    assert trade["status"] == "STOP_LOSS_EXIT"
    assert trade["exit_price"] == pytest.approx(0.40)
    assert trade["pnl"] is not None


def test_dashboard_state_exposes_lifecycle_durations() -> None:
    lifecycle, store = tracker()
    base = 4_000_000_000
    lifecycle.process(snapshot(), 200, context(1, base))
    lifecycle.process(snapshot(), 200, context(2, base + 150_000_000))
    lifecycle.process(snapshot(), 200, context(3, base + 350_000_000))
    lifecycle.process(snapshot(mode="neutral"), 200, context(4, base + 500_000_000))

    state = lifecycle.database_state(store)
    assert state["episodes"] == 1
    assert state["cancelledEpisodes"] == 1
    assert state["duration"]["averageMs"] == pytest.approx(500.0)
    assert state["reasons"]["EDGE_LOST"]["count"] == 1
    assert state["recentEpisodes"][0]["signalDurationMs"] == pytest.approx(500.0)
