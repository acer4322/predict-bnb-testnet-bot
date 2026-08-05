from __future__ import annotations

import sqlite3
import threading

from predict_bot import microprice_confirm_exit_098_live_patch as exit_patch
from predict_bot.live_trading import LiveLedger
from predict_bot.microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
)


def test_exit_candidates_include_confirmation_add_fills() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE live_orders (
            id INTEGER PRIMARY KEY,
            strategy TEXT,
            market_id INTEGER,
            side TEXT,
            status TEXT,
            token_id TEXT,
            filled_share_qty REAL
        );
        CREATE TABLE live_strategy_settlements (
            order_local_id INTEGER PRIMARY KEY
        );
        CREATE TABLE live_manual_exits (
            order_local_id INTEGER PRIMARY KEY,
            status TEXT
        );
        """
    )
    strategy = f"{EXIT_098_STRATEGY}:CONFIRM_ADD_2"
    db.execute(
        """INSERT INTO live_orders(
               id, strategy, market_id, side, status,
               token_id, filled_share_qty
           ) VALUES (9, ?, 101, 'UP', 'FILLED', 'up-token', 1.25)""",
        (strategy,),
    )
    db.commit()
    engine = type(
        "Engine",
        (),
        {"ledger": type("Ledger", (), {"db": db, "lock": threading.RLock()})()},
    )()

    candidates = exit_patch._live_exit_candidates(engine, 101)

    assert [row["id"] for row in candidates] == [9]
    assert candidates[0]["strategy"] == strategy


def test_live_ledger_normalizes_confirmation_add_for_exit_worker(tmp_path) -> None:
    ledger = LiveLedger(tmp_path / "live.db")
    source_strategy = f"{EXIT_098_STRATEGY}:CONFIRM_ADD_1"
    with ledger.lock:
        ledger.db.execute(
            """INSERT INTO live_orders(
                   strategy, topic_id, market_id, side, token_id,
                   signal_price, max_stake_usdt, requested_amount_wei,
                   status, order_type, time_in_force, account_type,
                   filled_share_qty, signal_at, updated_at
               ) VALUES (?, 1, 101, 'DOWN', 'down-token',
                         0.70, 1.00, '1000000000000000000',
                         'FILLED', 'LIMIT', 'GTC', 'CeDeFi',
                         1.40, '2026-08-05T00:00:00+00:00',
                         '2026-08-05T00:00:01+00:00')""",
            (source_strategy,),
        )
        ledger.db.commit()
        local_id = int(ledger.db.execute("SELECT last_insert_rowid()").fetchone()[0])

    order = ledger.order_for_manual_exit(local_id)

    assert order is not None
    assert order["strategy"] == EXIT_098_STRATEGY
    assert order["exit_098_source_strategy"] == source_strategy
