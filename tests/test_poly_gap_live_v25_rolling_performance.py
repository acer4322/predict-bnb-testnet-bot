from __future__ import annotations

import sqlite3
import threading

from predict_bot.poly_gap_live_v25 import RollingPerformancePolyGapLiveEngine


def _engine() -> RollingPerformancePolyGapLiveEngine:
    engine = RollingPerformancePolyGapLiveEngine.__new__(RollingPerformancePolyGapLiveEngine)
    engine.db_lock = threading.RLock()
    engine.db = sqlite3.connect(":memory:")
    engine.db.row_factory = sqlite3.Row
    engine.db.execute(
        """
        CREATE TABLE poly_gap_live_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            pnl_usdt REAL,
            exit_signal_at_ms INTEGER,
            updated_at_ms INTEGER NOT NULL
        )
        """
    )
    return engine


def _row(engine, market_id, state, pnl, exit_signal_at_ms, updated_at_ms):
    engine.db.execute(
        """INSERT INTO poly_gap_live_rounds(
               market_id,state,pnl_usdt,exit_signal_at_ms,updated_at_ms
           ) VALUES(?,?,?,?,?)""",
        (market_id, state, pnl, exit_signal_at_ms, updated_at_ms),
    )
    engine.db.commit()


def test_rolling_stats_aggregate_rounds_by_market_and_classify_reversal_losses():
    engine = _engine()
    try:
        # Market 101 has two rounds. The loss was caused by a real flip exit, but
        # the market remains a net winner after same-market aggregation.
        _row(engine, 101, "CLOSED", 0.50, 1_000, 1_100)
        _row(engine, 101, "CLOSED", -0.10, 1_200, 1_300)

        # Official settlement loss: counts as a losing market, not a flip-exit loss.
        _row(engine, 102, "SETTLED", -0.20, None, 2_000)

        # Real flip-exit loss.
        _row(engine, 103, "CLOSED", -0.30, 3_000, 3_100)

        # Current market must never enter rolling results even if a round has closed.
        _row(engine, 104, "CLOSED", 9.99, 4_000, 4_100)

        stats = engine._rolling_live_performance(current_market_id=104)

        assert stats["markets"] == 3
        assert stats["wins"] == 1
        assert stats["losses"] == 2
        assert stats["neutral"] == 0
        assert abs(stats["winRate"] - (1 / 3)) < 1e-12
        assert abs(stats["totalPnlUsdt"] - (-0.10)) < 1e-12
        assert abs(stats["averagePnlUsdt"] - (-0.10 / 3)) < 1e-12
        assert stats["reversalLossMarkets"] == 2
        assert stats["marketIds"] == [103, 102, 101]
    finally:
        engine.db.close()


def test_active_market_is_not_treated_as_completed_history():
    engine = _engine()
    try:
        _row(engine, 201, "CLOSED", 0.25, 1_000, 1_100)
        _row(engine, 201, "OPEN", None, None, 1_200)
        _row(engine, 202, "SETTLED", 0.10, None, 2_000)

        stats = engine._rolling_live_performance(current_market_id=None)

        assert stats["markets"] == 1
        assert stats["marketIds"] == [202]
        assert stats["wins"] == 1
        assert stats["reversalLossMarkets"] == 0
    finally:
        engine.db.close()
