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


def _create_live_order(
    ledger: live_trading.LiveLedger,
    *,
    strategy: str,
    market_id: int,
) -> int:
    order_id = ledger.record_signal(
        topic_id=1,
        market_id=market_id,
        side="UP",
        token_id=f"token-{market_id}",
        signal_price=0.5,
        account_type="SPOT",
        signal_at=live_trading.utc_iso(),
        strategy=strategy,
        max_stake_usdt=1.0,
        requested_amount_wei=str(10**18),
    )
    assert order_id is not None
    return order_id


def test_live_rule_normalization_and_persistence(tmp_path: Path) -> None:
    normalized = live_trading.normalize_live_rules(
        {
            "strategies": ["R_MICROPRICE", "R_FUTURES_LEAD"],
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


def test_loss_protection_modes_are_mutually_exclusive() -> None:
    shadow_selected = live_trading.normalize_live_rules(
        {
            "strategies": ["R_MICROPRICE", "R_FUTURES_LEAD"],
            "strategyStakesUsdt": [1.0, 1.0],
            "strategyLossCooldownEnabled": [True, False],
            live_guard.LOSS_STREAK_RULE_FIELD: [True, False],
        }
    )
    assert shadow_selected[live_guard.LOSS_STREAK_RULE_FIELD] == [True, False]
    assert shadow_selected["strategyLossCooldownEnabled"] == [False, False]

    cooldown_selected = live_trading.normalize_live_rules(
        {"strategyLossCooldownEnabled": [True, False]},
        shadow_selected,
    )
    assert cooldown_selected["strategyLossCooldownEnabled"] == [True, False]
    assert cooldown_selected[live_guard.LOSS_STREAK_RULE_FIELD] == [False, False]


def test_guard_state_lookup_closes_sqlite_transaction(tmp_path: Path) -> None:
    ledger = live_trading.LiveLedger(tmp_path / "live.db")
    state = ledger.loss_streak_guard_state("R_MICROPRICE")
    assert state["mode"] == live_guard.LOSS_STREAK_MODE_NORMAL
    assert ledger.db.in_transaction is False

    blocked, cooldown = ledger.consume_loss_cooldown("R_MICROPRICE", 1001)
    assert blocked is False
    assert cooldown["consecutiveLosses"] == 0
    assert ledger.db.in_transaction is False


def test_normal_win_immediately_resets_two_loss_streak(tmp_path: Path) -> None:
    ledger = live_trading.LiveLedger(tmp_path / "live.db")
    strategy = "R_MICROPRICE"

    for index, result in enumerate(("LOSS", "LOSS", "WIN"), start=1):
        market_id = 1100 + index
        order_id = _create_live_order(
            ledger,
            strategy=strategy,
            market_id=market_id,
        )
        state = live_guard._record_live_result(
            ledger,
            order={
                "id": order_id,
                "strategy": strategy,
                "market_id": market_id,
            },
            settlement={
                "result": result,
                "pnl_usdt": 1.0 if result == "WIN" else -1.0,
            },
            utc_iso=live_trading.utc_iso,
        )

    assert state["mode"] == live_guard.LOSS_STREAK_MODE_NORMAL
    assert state["consecutiveLosses"] == 0
    assert state["lastLiveResult"] == "WIN"


def test_state_lookup_reconciles_committed_win_missed_by_guard(
    tmp_path: Path,
) -> None:
    ledger = live_trading.LiveLedger(tmp_path / "live.db")
    strategy = "R_MICROPRICE"

    for index in range(2):
        market_id = 1201 + index
        order_id = _create_live_order(
            ledger,
            strategy=strategy,
            market_id=market_id,
        )
        live_guard._record_live_result(
            ledger,
            order={
                "id": order_id,
                "strategy": strategy,
                "market_id": market_id,
            },
            settlement={"result": "LOSS", "pnl_usdt": -1.0},
            utc_iso=live_trading.utc_iso,
        )

    before = ledger.loss_streak_guard_state(strategy)
    assert before["consecutiveLosses"] == 2

    win_market_id = 1203
    win_order_id = _create_live_order(
        ledger,
        strategy=strategy,
        market_id=win_market_id,
    )
    now = live_trading.utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """INSERT INTO live_strategy_settlements(
                   order_local_id, market_id, position_status, result,
                   cost_usdt, payout_usdt, pnl_usdt, roi_pct,
                   settled_at, updated_at
               ) VALUES (?, ?, 'SETTLED', 'WIN', 1.0, 2.0, 1.0, 100.0, ?, ?)""",
            (win_order_id, win_market_id, now, now),
        )
        ledger.db.commit()

    repaired = ledger.loss_streak_guard_state(strategy)
    assert repaired["mode"] == live_guard.LOSS_STREAK_MODE_NORMAL
    assert repaired["consecutiveLosses"] == 0
    assert repaired["lastLiveResult"] == "WIN"
    assert repaired["lastLiveOrderLocalId"] == win_order_id

    with ledger.lock:
        result_row = ledger.db.execute(
            """SELECT result
                 FROM live_strategy_loss_streak_guard_results
                WHERE order_local_id=?""",
            (win_order_id,),
        ).fetchone()
        event_row = ledger.db.execute(
            """SELECT event_type
                 FROM live_events
                WHERE event_type='LOSS_STREAK_SETTLEMENT_RECONCILED'
                ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    assert result_row is not None
    assert result_row["result"] == "WIN"
    assert event_row is not None


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
