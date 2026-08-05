from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from predict_bot import live_trading
from predict_bot import loss_streak_guard_patch as live_guard
from predict_bot import microprice_confirm_loss_streak_guard as paper_guard


def _create_simulation_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE trades(
               id INTEGER PRIMARY KEY,
               strategy TEXT NOT NULL,
               market_id INTEGER NOT NULL,
               status TEXT,
               pnl REAL,
               closed_at TEXT
           )"""
    )
    connection.commit()
    return connection


def test_live_rule_normalization_and_persistence(tmp_path: Path) -> None:
    normalized = live_trading.normalize_live_rules(
        {
            "strategies": ["M0W", "M01W"],
            "strategyStakesUsdt": [1.0, 1.0],
            live_guard.LOSS_STREAK_RULE_FIELD: [True, False],
        }
    )
    assert normalized[live_guard.LOSS_STREAK_RULE_FIELD] == [True, False]

    ledger = live_trading.LiveLedger(tmp_path / "live.db")
    ledger.set_live_rules(normalized)
    overrides = ledger.live_rule_overrides()
    assert live_guard.LOSS_STREAK_RULE_FIELD in overrides
    reloaded = live_trading.normalize_live_rules(overrides)
    assert reloaded[live_guard.LOSS_STREAK_RULE_FIELD] == [True, False]


def test_live_guard_three_losses_shadow_and_positive_pnl_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sim_path = tmp_path / "simulation.db"
    sim = _create_simulation_db(sim_path)
    monkeypatch.setenv("PREDICT_SIM_DB", str(sim_path))
    ledger = live_trading.LiveLedger(tmp_path / "live.db")

    for order_id in range(1, 4):
        live_guard._record_live_result(
            ledger,
            order={
                "id": order_id,
                "strategy": "R_MICROPRICE_CONFIRM",
                "market_id": 100 + order_id,
            },
            settlement={"result": "LOSS", "pnl_usdt": -1.0},
            utc_iso=live_trading.utc_iso,
        )

    shadow = ledger.loss_streak_guard_state("R_MICROPRICE_CONFIRM")
    assert shadow["mode"] == live_guard.LOSS_STREAK_MODE_SHADOW
    assert shadow["consecutiveLosses"] == 3
    assert shadow["shadowSourceTradeIdFloor"] == 0

    sim.executemany(
        """INSERT INTO trades(id, strategy, market_id, status, pnl, closed_at)
           VALUES (?, 'R_MICROPRICE_CONFIRM', ?, ?, ?,
                   '2026-08-06T00:00:00+00:00')""",
        [
            (1, 201, "SETTLED_LOSS", -1.0),
            (2, 202, "SETTLED_WIN", 2.0),
            (3, 203, "SETTLED_WIN", 1.0),
        ],
    )
    sim.commit()
    sim.close()

    recovered = ledger.sync_loss_streak_shadow("R_MICROPRICE_CONFIRM")
    assert recovered["mode"] == live_guard.LOSS_STREAK_MODE_PROBATION
    assert recovered["probationRemaining"] == 2
    assert recovered["latestShadowPnlSum"] == 2.0

    live_guard._record_live_result(
        ledger,
        order={
            "id": 4,
            "strategy": "R_MICROPRICE_CONFIRM",
            "market_id": 204,
        },
        settlement={"result": "LOSS", "pnl_usdt": -1.0},
        utc_iso=live_trading.utc_iso,
    )
    reset = ledger.loss_streak_guard_state("R_MICROPRICE_CONFIRM")
    assert reset["mode"] == live_guard.LOSS_STREAK_MODE_SHADOW
    assert reset["shadowCycle"] == 2
    assert reset["shadowSourceTradeIdFloor"] == 3


class _PaperStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self._read_only = False
        self.db.execute(
            """CREATE TABLE trades(
                   id INTEGER PRIMARY KEY,
                   strategy TEXT NOT NULL,
                   market_id INTEGER NOT NULL,
                   side TEXT NOT NULL DEFAULT 'UP',
                   entry_price REAL NOT NULL DEFAULT 0.5,
                   status TEXT,
                   pnl REAL
               )"""
        )
        self.db.commit()


def _paper_decision(
    store: _PaperStore,
    *,
    trade_id: int,
    mode: str,
    pnl: float,
) -> None:
    status = "SETTLED_WIN" if pnl > 0 else "SETTLED_LOSS"
    store.db.execute(
        """INSERT INTO trades(id, strategy, market_id, status, pnl)
           VALUES (?, ?, ?, ?, ?)""",
        (trade_id, paper_guard.SOURCE_STRATEGY, 300 + trade_id, status, pnl),
    )
    store.db.execute(
        """INSERT INTO microprice_confirm_loss_streak_decisions(
               source_trade_id, source_market_id, source_side,
               source_entry_price, mode_at_decision,
               consecutive_losses_at_decision, stake_multiplier,
               opened, guard_trade_id, guard_strategy_version, created_at
           ) VALUES (?, ?, 'UP', 0.5, ?, 0, ?, ?, NULL, ?, ?)""",
        (
            trade_id,
            300 + trade_id,
            mode,
            0.0 if mode == paper_guard.MODE_SHADOW else 1.0,
            0 if mode == paper_guard.MODE_SHADOW else 1,
            paper_guard.LOSS_STREAK_TEST_VERSION,
            paper_guard._utc_iso(),
        ),
    )
    store.db.commit()


def test_paper_guard_forward_state_machine() -> None:
    store = _PaperStore()
    paper_guard._ensure_schema(store)
    for trade_id in range(1, 4):
        _paper_decision(
            store,
            trade_id=trade_id,
            mode=paper_guard.MODE_NORMAL,
            pnl=-1.0,
        )
    paper_guard._sync_results(store)
    shadow = paper_guard._public_state(store)
    assert shadow["mode"] == paper_guard.MODE_SHADOW
    assert shadow["shadowCycle"] == 1

    for trade_id, pnl in ((4, -1.0), (5, 2.0), (6, 1.0)):
        _paper_decision(
            store,
            trade_id=trade_id,
            mode=paper_guard.MODE_SHADOW,
            pnl=pnl,
        )
    paper_guard._sync_results(store)
    recovered = paper_guard._public_state(store)
    assert recovered["mode"] == paper_guard.MODE_PROBATION
    assert recovered["probationRemaining"] == 2
    assert recovered["latestShadowPnlSum"] == 2.0
