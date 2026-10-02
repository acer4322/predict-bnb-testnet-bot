from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from predict_bot.microprice_lifecycle_comparison import (
    install_microprice_lifecycle_comparison_sidecar,
)
from predict_bot.microprice_lifecycle_comparison_store import (
    COMPARISON_TABLE,
    MicropriceLifecycleComparisonTracker,
)
from predict_bot.microprice_signal_lifecycle import TABLE as SOURCE_TABLE


def create_source(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute(f"""CREATE TABLE {SOURCE_TABLE}(
      id INTEGER PRIMARY KEY, market_id INTEGER, topic_id INTEGER, side TEXT,
      status TEXT, filled_at TEXT, filled_ns INTEGER, ended_at TEXT,
      end_reason TEXT, exit_price REAL, pnl REAL, signal_duration_ms REAL,
      position_duration_ms REAL, entry_price REAL, stake REAL, shares REAL,
      entry_fee REAL, fee_rate_bps INTEGER
    )""")
    db.commit()
    return db


def insert_episode(
    db: sqlite3.Connection,
    *,
    episode_id: int = 1,
    status: str = "EXITED",
    reason: str | None = "EDGE_LOST",
    exit_price: float | None = 0.39,
    exit_pnl: float | None = -0.20,
) -> None:
    db.execute(f"""INSERT INTO {SOURCE_TABLE} VALUES(
      ?,11,1,'UP',?,'2026-08-04T00:00:00+00:00',1000,
      '2026-08-04T00:00:01+00:00',?,?,?,900,200,.40,5.0,12.5,.01,200
    )""", (episode_id, status, reason, exit_price, exit_pnl))
    db.commit()


def add_settlement(db: sqlite3.Connection, winner: str = "UP") -> None:
    db.execute("""CREATE TABLE market_settlements(
      market_id INTEGER PRIMARY KEY, official_winner TEXT, status TEXT
    )""")
    db.execute("INSERT INTO market_settlements VALUES(11,?,'OFFICIAL')", (winner,))
    db.commit()


def test_hold_control_uses_same_fill_and_settles(tmp_path: Path) -> None:
    path = tmp_path / "simulation.db"
    db = create_source(path)
    insert_episode(db)
    tracker = MicropriceLifecycleComparisonTracker(path)

    tracker.sync({"market_id": 11, "received_wall_ns": 2_000})
    row = tracker.db.execute(f"SELECT * FROM {COMPARISON_TABLE}").fetchone()
    assert row["entry_price"] == 0.40
    assert row["stake"] == 5.0
    assert row["shares"] == 12.5
    assert row["entry_fee"] == 0.01

    add_settlement(db, "UP")
    state = tracker.state()
    assert state["completedPairs"] == 1
    assert state["holdToEnd"]["totalPnl"] > 0
    assert state["comparison"]["holdBetter"] == 1
    assert state["recentPairs"][0]["winner"] == "HOLD_TO_END"


def test_no_early_exit_produces_tie_at_same_settlement(tmp_path: Path) -> None:
    path = tmp_path / "simulation.db"
    db = create_source(path)
    settlement_pnl = 12.5 - 5.0 - 0.01
    insert_episode(
        db,
        status="SETTLED_WIN",
        reason="MARKET_ROLLOVER_HELD",
        exit_price=1.0,
        exit_pnl=settlement_pnl,
    )
    add_settlement(db, "UP")

    state = MicropriceLifecycleComparisonTracker(path).state()
    assert state["comparison"]["ties"] == 1
    assert abs(state["comparison"]["netAdvantage"]) < 1e-9


def test_new_market_moves_hold_arm_to_settlement_pending(tmp_path: Path) -> None:
    path = tmp_path / "simulation.db"
    db = create_source(path)
    insert_episode(db, status="OPEN", reason=None, exit_price=None, exit_pnl=None)
    tracker = MicropriceLifecycleComparisonTracker(path)

    tracker.sync({"market_id": 12, "received_wall_ns": 5_000})

    row = tracker.db.execute(f"SELECT status FROM {COMPARISON_TABLE}").fetchone()
    assert row["status"] == "SETTLEMENT_PENDING"


def test_sidecar_is_fail_open_and_skips_critical_state(tmp_path: Path) -> None:
    path = tmp_path / "simulation.db"
    db = create_source(path)
    insert_episode(db)

    class Engine:
        def __init__(self) -> None:
            self._microprice_signal_lifecycle_tracker = SimpleNamespace(path=path)

        def record_confirmation_add_snapshot(self, snapshot: dict) -> dict:
            return {"original": True}

        def state(self, *, include_ledger: bool = True) -> dict:
            return {"main": True, "includeLedger": include_ledger}

    install_microprice_lifecycle_comparison_sidecar(
        SimpleNamespace(LiveM0WEngine=Engine)
    )
    engine = Engine()
    assert engine.state(include_ledger=False) == {
        "main": True,
        "includeLedger": False,
    }
    assert engine.record_confirmation_add_snapshot(
        {"market_id": 11, "received_wall_ns": 1}
    ) == {"original": True}
    assert "micropriceSignalLifecycleComparison" in engine.state()
