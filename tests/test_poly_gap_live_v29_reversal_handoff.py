from __future__ import annotations

import sqlite3
import threading

from predict_bot.poly_gap_live_v26 import LIVE_SAME_MARKET_BREAKER_EXIT_COUNT
from predict_bot.poly_gap_live_v29 import ReversalHandoffPolyGapLiveEngine


def _engine() -> ReversalHandoffPolyGapLiveEngine:
    engine = ReversalHandoffPolyGapLiveEngine.__new__(ReversalHandoffPolyGapLiveEngine)
    engine.db_lock = threading.RLock()
    engine.db = sqlite3.connect(":memory:")
    engine.db.row_factory = sqlite3.Row
    engine.db.execute(
        """
        CREATE TABLE poly_gap_live_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            exit_signal_at_ms INTEGER,
            exit_intent TEXT,
            handoff_parent_round_id INTEGER
        )
        """
    )
    return engine


def _round(
    engine: ReversalHandoffPolyGapLiveEngine,
    market_id: int,
    state: str,
    *,
    exit_signal_at_ms: int | None = None,
    exit_intent: str | None = None,
    parent: int | None = None,
) -> int:
    cursor = engine.db.execute(
        """INSERT INTO poly_gap_live_rounds(
               market_id,state,exit_signal_at_ms,exit_intent,handoff_parent_round_id
           ) VALUES(?,?,?,?,?)""",
        (market_id, state, exit_signal_at_ms, exit_intent, parent),
    )
    engine.db.commit()
    return int(cursor.lastrowid)


def test_first_reversal_projects_below_breaker_and_can_handoff():
    engine = _engine()
    try:
        assert LIVE_SAME_MARKET_BREAKER_EXIT_COUNT == 2
        state = engine._projected_reversal_breaker(1001)
        assert state["completedBeforeCurrentExit"] == 0
        assert state["projectedIfCurrentExitCompletes"] == 1
        assert state["handoffBlocked"] is False
    finally:
        engine.db.close()


def test_second_reversal_is_blocked_before_new_buy_is_sent():
    engine = _engine()
    try:
        _round(
            engine,
            2001,
            "CLOSED",
            exit_signal_at_ms=1_000,
            exit_intent="POLY_DIRECTION_FLIP",
        )
        state = engine._projected_reversal_breaker(2001)
        assert state["completedBeforeCurrentExit"] == 1
        assert state["projectedIfCurrentExitCompletes"] == 2
        assert state["handoffBlocked"] is True
    finally:
        engine.db.close()


def test_take_profit_does_not_consume_projected_reversal_budget():
    engine = _engine()
    try:
        _round(
            engine,
            3001,
            "CLOSED",
            exit_signal_at_ms=1_000,
            exit_intent="TAKE_PROFIT",
        )
        state = engine._projected_reversal_breaker(3001)
        assert state["completedBeforeCurrentExit"] == 0
        assert state["projectedIfCurrentExitCompletes"] == 1
        assert state["handoffBlocked"] is False
    finally:
        engine.db.close()


def test_unresolved_parent_remains_primary_while_handoff_child_is_active():
    engine = _engine()
    try:
        parent = _round(engine, 4001, "EXIT_SYNC", exit_intent="POLY_DIRECTION_FLIP")
        child = _round(engine, 4001, "ENTRY_SYNC", parent=parent)

        active = engine._current_active_round()
        assert active is not None
        assert int(active["id"]) == parent

        engine.db.execute("UPDATE poly_gap_live_rounds SET state='CLOSED' WHERE id=?", (parent,))
        engine.db.commit()

        active = engine._current_active_round()
        assert active is not None
        assert int(active["id"]) == child
    finally:
        engine.db.close()
