from __future__ import annotations

import sqlite3
import threading

from predict_bot.cross_oracle_strategies import STRATEGY_POLY_GAP_SCALP
from predict_bot.cross_oracle_strategy_rolling_stats import RollingStatsPaperEngine


def _engine() -> RollingStatsPaperEngine:
    engine = object.__new__(RollingStatsPaperEngine)
    engine.db = sqlite3.connect(":memory:")
    engine.db.row_factory = sqlite3.Row
    engine.db_lock = threading.RLock()
    engine.db.executescript(
        """
        CREATE TABLE cross_oracle_strategy_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            binance_market_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            gross_pnl_usdt REAL,
            opened_at_ms INTEGER NOT NULL,
            closed_at_ms INTEGER
        );
        CREATE TABLE poly_chop_guard_markets (
            market_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            confirmed_reversals INTEGER NOT NULL,
            evaluable INTEGER NOT NULL,
            finalized_at_ms INTEGER
        );
        """
    )
    return engine


def test_rolling_stats_use_last_ten_distinct_markets_and_aggregate_gap_rounds() -> None:
    engine = _engine()
    try:
        for market_id in range(1, 12):
            engine.db.execute(
                """INSERT INTO poly_chop_guard_markets(
                       market_id,status,confirmed_reversals,evaluable,finalized_at_ms
                   ) VALUES(?,?,?,?,?)""",
                (market_id, "CALM", market_id % 4, 1, market_id * 1000),
            )
            # Two completed GAP rounds in every market. The market result must be
            # 0.30, not two separate samples of 0.10 and 0.20.
            for offset, pnl in enumerate((0.10, 0.20), start=1):
                engine.db.execute(
                    """INSERT INTO cross_oracle_strategy_trades(
                           strategy,binance_market_id,status,gross_pnl_usdt,
                           opened_at_ms,closed_at_ms
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        STRATEGY_POLY_GAP_SCALP,
                        market_id,
                        "EXITED",
                        pnl,
                        market_id * 1000 + offset,
                        market_id * 1000 + offset + 100,
                    ),
                )
        engine.db.commit()

        stats = engine._rolling_market_stats(STRATEGY_POLY_GAP_SCALP)

        assert stats["rolling10Markets"] == 10
        assert stats["rolling10MarketIds"] == list(range(11, 1, -1))
        assert abs(stats["rolling10AveragePnlUsdt"] - 0.30) < 1e-12
        expected_reversals = sum(market_id % 4 for market_id in range(2, 12)) / 10
        assert abs(stats["rolling10AverageReversals"] - expected_reversals) < 1e-12
    finally:
        engine.db.close()


def test_rolling_stats_exclude_non_evaluable_markets() -> None:
    engine = _engine()
    try:
        engine.db.execute(
            "INSERT INTO poly_chop_guard_markets VALUES(1,'NOT_EVALUABLE',9,0,1000)"
        )
        engine.db.execute(
            """INSERT INTO cross_oracle_strategy_trades(
                   strategy,binance_market_id,status,gross_pnl_usdt,opened_at_ms,closed_at_ms
               ) VALUES(?,?,?,?,?,?)""",
            (STRATEGY_POLY_GAP_SCALP, 1, "EXITED", 5.0, 100, 900),
        )
        engine.db.commit()

        stats = engine._rolling_market_stats(STRATEGY_POLY_GAP_SCALP)

        assert stats["rolling10Markets"] == 0
        assert stats["rolling10AveragePnlUsdt"] is None
        assert stats["rolling10AverageReversals"] is None
    finally:
        engine.db.close()
