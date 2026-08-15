from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from predict_bot.microprice_signal_lifecycle import (
    MicropriceSignalLifecycleTracker,
    install_microprice_signal_lifecycle_sidecar,
)


def snapshot(
    timestamp_ns: int,
    direction: str = "UP",
    *,
    market_id: int = 11,
    seconds_left: float = 180.0,
    up_ask: float = 0.40,
    up_bid: float = 0.39,
    up_bid_size: float | None = None,
) -> dict:
    if direction == "UP":
        sizes = {
            "up_bid_size": 100.0,
            "up_ask_size": 20.0,
            "down_bid_size": 20.0,
            "down_ask_size": 100.0,
        }
    elif direction == "DOWN":
        sizes = {
            "up_bid_size": 20.0,
            "up_ask_size": 100.0,
            "down_bid_size": 100.0,
            "down_ask_size": 20.0,
        }
    else:
        sizes = {
            "up_bid_size": 50.0,
            "up_ask_size": 50.0,
            "down_bid_size": 50.0,
            "down_ask_size": 50.0,
        }
    if up_bid_size is not None:
        sizes["up_bid_size"] = up_bid_size
    return {
        "timestamp": "2026-08-04T00:00:00+00:00",
        "received_wall_ns": timestamp_ns,
        "market_id": market_id,
        "topic_id": 1,
        "seconds_left": seconds_left,
        "up_ask": up_ask,
        "up_bid": up_bid,
        "down_ask": 0.60,
        "down_bid": 0.59,
        "book_age_ms": 10.0,
        "book_skew_ms": 0.0,
        "up_book_timestamp_ms": timestamp_ns // 1_000_000,
        "down_book_timestamp_ms": timestamp_ns // 1_000_000,
        **sizes,
    }


def advance_to_pending(
    tracker: MicropriceSignalLifecycleTracker,
    base_ns: int,
) -> None:
    tracker.process(snapshot(base_ns), 200)
    tracker.process(snapshot(base_ns + 200_000_000), 200)
    tracker.process(snapshot(base_ns + 400_000_000), 200)
    assert tracker.active is not None
    assert tracker.active["status"] == "PENDING"


def advance_to_open(
    tracker: MicropriceSignalLifecycleTracker,
    base_ns: int,
) -> None:
    advance_to_pending(tracker, base_ns)
    tracker.process(snapshot(base_ns + 700_000_000), 200)
    assert tracker.active is not None
    assert tracker.active["status"] == "OPEN"


def test_pending_order_is_cancelled_when_edge_disappears(tmp_path: Path) -> None:
    tracker = MicropriceSignalLifecycleTracker(tmp_path / "simulation.db")
    base_ns = 1_000_000_000
    advance_to_pending(tracker, base_ns)

    tracker.process(snapshot(base_ns + 500_000_000, "FLAT"), 200)

    assert tracker.active is None
    episode = tracker.state()["recentEpisodes"][0]
    assert episode["status"] == "CANCELLED"
    assert episode["endReason"] == "EDGE_LOST"
    assert episode["signalDurationMs"] == pytest.approx(500.0)


def test_pending_order_is_cancelled_on_reversal(tmp_path: Path) -> None:
    tracker = MicropriceSignalLifecycleTracker(tmp_path / "simulation.db")
    base_ns = 2_000_000_000
    advance_to_pending(tracker, base_ns)

    tracker.process(snapshot(base_ns + 500_000_000, "DOWN"), 200)

    assert tracker.active is None
    episode = tracker.state()["recentEpisodes"][0]
    assert episode["status"] == "CANCELLED"
    assert episode["endReason"] == "SIGNAL_REVERSED"


def test_filled_position_exits_when_edge_disappears(tmp_path: Path) -> None:
    tracker = MicropriceSignalLifecycleTracker(tmp_path / "simulation.db")
    base_ns = 3_000_000_000
    advance_to_open(tracker, base_ns)

    tracker.process(snapshot(base_ns + 900_000_000, "FLAT"), 200)

    state = tracker.state()
    assert tracker.active is None
    assert state["exitedEpisodes"] == 1
    episode = state["recentEpisodes"][0]
    assert episode["status"] == "EXITED"
    assert episode["endReason"] == "EDGE_LOST"
    assert episode["entryPrice"] == pytest.approx(0.40)
    assert episode["exitPrice"] == pytest.approx(0.39)
    assert episode["positionDurationMs"] == pytest.approx(200.0)
    assert episode["pnl"] < 0


def test_exit_waits_for_visible_bid_depth_then_retries(tmp_path: Path) -> None:
    tracker = MicropriceSignalLifecycleTracker(tmp_path / "simulation.db")
    base_ns = 4_000_000_000
    advance_to_open(tracker, base_ns)

    tracker.process(
        snapshot(base_ns + 900_000_000, "FLAT", up_bid_size=1.0),
        200,
    )
    assert tracker.active is not None
    assert tracker.active["status"] == "EXIT_PENDING"

    tracker.process(snapshot(base_ns + 1_100_000_000, "FLAT"), 200)
    assert tracker.active is None
    assert tracker.state()["recentEpisodes"][0]["status"] == "EXITED"


def test_rollover_open_position_reconciles_official_settlement(tmp_path: Path) -> None:
    path = tmp_path / "simulation.db"
    tracker = MicropriceSignalLifecycleTracker(path)
    base_ns = 5_000_000_000
    advance_to_open(tracker, base_ns)

    tracker.process(
        snapshot(base_ns + 1_000_000_000, "UP", market_id=12),
        200,
    )
    assert tracker.state()["settlementPendingEpisodes"] == 1

    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY,
            official_winner TEXT,
            status TEXT
        )"""
    )
    db.execute("INSERT INTO market_settlements VALUES (11, 'UP', 'OFFICIAL')")
    db.commit()
    db.close()

    state = tracker.state()
    assert state["settlementPendingEpisodes"] == 0
    assert state["settledWins"] == 1
    assert state["realizedPnl"] > 0


def test_sidecar_skips_critical_state_and_fails_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PREDICT_SIM_DB", str(tmp_path / "sidecar.db"))

    class Engine:
        def record_confirmation_add_snapshot(self, snapshot: dict) -> dict:
            return {"original": True}

        def state(self, value=None, *, include_ledger: bool = True) -> dict:
            return {"main": True, "includeLedger": include_ledger}

    install_microprice_signal_lifecycle_sidecar(
        SimpleNamespace(LiveM0WEngine=Engine)
    )
    engine = Engine()
    assert engine.state(include_ledger=False) == {
        "main": True,
        "includeLedger": False,
    }
    assert engine.record_confirmation_add_snapshot(
        {"market_id": "not-an-int"}
    ) == {"original": True}
    detailed = engine.state()
    assert detailed["main"] is True
    assert detailed["micropriceSignalLifecycle"]["status"] == "DEGRADED"
    assert "ValueError" in detailed["micropriceSignalLifecycle"]["runtime"]["lastError"]
