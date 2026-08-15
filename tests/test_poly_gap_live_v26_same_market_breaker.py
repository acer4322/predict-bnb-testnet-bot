from __future__ import annotations

import sqlite3
import threading

from predict_bot.poly_gap_live_v26 import (
    LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
    LiveExitCountBreakerPolyGapLiveEngine,
)


def _engine() -> LiveExitCountBreakerPolyGapLiveEngine:
    engine = LiveExitCountBreakerPolyGapLiveEngine.__new__(LiveExitCountBreakerPolyGapLiveEngine)
    engine.db_lock = threading.RLock()
    engine.db = sqlite3.connect(":memory:")
    engine.db.row_factory = sqlite3.Row
    engine.db.execute(
        """
        CREATE TABLE poly_gap_live_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            exit_signal_at_ms INTEGER
        )
        """
    )
    engine._last_chop_guard_event_key = None
    engine._event = lambda *args, **kwargs: None
    return engine


def _round(engine, market_id, state, exit_signal_at_ms):
    engine.db.execute(
        "INSERT INTO poly_gap_live_rounds(market_id,state,exit_signal_at_ms) VALUES(?,?,?)",
        (market_id, state, exit_signal_at_ms),
    )
    engine.db.commit()


def test_first_completed_reversal_exit_can_rearm_second_blocks_market():
    engine = _engine()
    try:
        assert LIVE_SAME_MARKET_BREAKER_EXIT_COUNT == 2

        _round(engine, 1001, "CLOSED", 1_000)
        first = engine._live_same_market_breaker(1001)
        assert first["completedReversalExits"] == 1
        assert first["remainingBeforeBlock"] == 1
        assert first["blocked"] is False

        _round(engine, 1001, "CLOSED", 2_000)
        second = engine._live_same_market_breaker(1001)
        assert second["completedReversalExits"] == 2
        assert second["remainingBeforeBlock"] == 0
        assert second["blocked"] is True
    finally:
        engine.db.close()


def test_only_completed_live_exit_signals_count_toward_breaker():
    engine = _engine()
    try:
        _round(engine, 2001, "SETTLED", None)      # official settlement
        _round(engine, 2001, "CLOSED", None)       # no recorded flip exit signal
        _round(engine, 2001, "EXIT_SYNC", 3_000)   # SELL unresolved, not completed
        _round(engine, 2001, "CLOSED", 4_000)      # one completed reversal exit

        state = engine._live_same_market_breaker(2001)
        assert state["completedReversalExits"] == 1
        assert state["blocked"] is False
    finally:
        engine.db.close()


def test_paper_current_market_chop_becomes_diagnostic_only():
    engine = _engine()
    try:
        state = {
            "verified": True,
            "blocked": True,
            "persistentPaused": False,
            "currentMarketChoppy": True,
            "reason": "Paper same-market breaker active",
        }
        engine._emit_guard_transition(state)

        assert state["paperRequestedBlockBeforeV26"] is True
        assert state["paperCurrentMarketChoppyDiagnosticOnly"] is True
        assert state["blocked"] is False
        assert "diagnostic only" in state["reason"]
    finally:
        engine.db.close()


def test_persistent_paper_chop_pause_still_blocks():
    engine = _engine()
    try:
        state = {
            "verified": True,
            "blocked": True,
            "persistentPaused": True,
            "currentMarketChoppy": True,
            "reason": "persistent Paper CHOP pause",
        }
        engine._emit_guard_transition(state)

        assert state["blocked"] is True
        assert state["persistentPaused"] is True
    finally:
        engine.db.close()
