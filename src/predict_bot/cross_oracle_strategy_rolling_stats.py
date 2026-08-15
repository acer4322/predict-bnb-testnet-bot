from __future__ import annotations

from typing import Any

from . import cross_oracle_strategy_chop_guard_v2 as base
from .cross_oracle_strategies import STRATEGIES


ROLLING_MARKET_WINDOW = 10
TERMINAL_TRADE_STATUSES = ("EXITED", "SETTLED_WIN", "SETTLED_LOSS")


class RollingStatsPaperEngine(base.ImmediateChopBreakerPaperEngine):
    """Add comparable recent-market stats to the three original Poly strategies.

    A "game" is one distinct finalized/evaluable Binance 5m market where the
    strategy has at least one terminal trade. R_POLY_GAP_SCALP may trade many
    rounds in one market, so all terminal trade PnL is summed before averaging.

    Flip count intentionally reuses the persisted CHOP-guard confirmed reversal
    count instead of inventing another process-local counter. This keeps restart
    behavior deterministic and makes the dashboard's churn metric use the same
    500ms / distinct-receipt semantics as the existing regime guard.
    """

    def _rolling_market_stats(self, strategy: str) -> dict[str, Any]:
        if strategy not in STRATEGIES:
            return {}
        placeholders = ",".join("?" for _ in TERMINAL_TRADE_STATUSES)
        with self.db_lock:
            rows = self.db.execute(
                f"""
                WITH per_market AS (
                    SELECT
                        t.binance_market_id AS market_id,
                        SUM(COALESCE(t.gross_pnl_usdt, 0.0)) AS market_pnl,
                        MAX(COALESCE(t.closed_at_ms, t.opened_at_ms)) AS last_closed_at_ms
                    FROM cross_oracle_strategy_trades t
                    JOIN poly_chop_guard_markets c
                      ON c.market_id = t.binance_market_id
                    WHERE t.strategy = ?
                      AND t.status IN ({placeholders})
                      AND t.gross_pnl_usdt IS NOT NULL
                      AND c.finalized_at_ms IS NOT NULL
                      AND c.evaluable = 1
                      AND c.status IN ('CALM', 'CHOPPY')
                    GROUP BY t.binance_market_id
                    ORDER BY last_closed_at_ms DESC, market_id DESC
                    LIMIT ?
                )
                SELECT
                    p.market_id,
                    p.market_pnl,
                    p.last_closed_at_ms,
                    COALESCE(c.confirmed_reversals, 0) AS confirmed_reversals
                FROM per_market p
                JOIN poly_chop_guard_markets c ON c.market_id = p.market_id
                ORDER BY p.last_closed_at_ms DESC, p.market_id DESC
                """,
                (
                    strategy,
                    *TERMINAL_TRADE_STATUSES,
                    ROLLING_MARKET_WINDOW,
                ),
            ).fetchall()

        count = len(rows)
        total_pnl = sum(float(row["market_pnl"] or 0.0) for row in rows)
        total_reversals = sum(int(row["confirmed_reversals"] or 0) for row in rows)
        return {
            "rolling10Markets": count,
            "rolling10AveragePnlUsdt": total_pnl / count if count else None,
            "rolling10TotalPnlUsdt": total_pnl,
            "rolling10AverageReversals": total_reversals / count if count else None,
            "rolling10TotalReversals": total_reversals,
            "rolling10MarketIds": [int(row["market_id"]) for row in rows],
            "rolling10Window": ROLLING_MARKET_WINDOW,
            "rolling10Basis": (
                "distinct finalized/evaluable Binance 5m markets; same-market trade PnL summed; "
                "reversals reuse persisted CHOP-guard confirmed reversal counts"
            ),
        }

    def _summary(self, strategy: str) -> dict[str, Any]:
        payload = super()._summary(strategy)
        payload.update(self._rolling_market_stats(strategy))
        return payload

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["rolling10MarketStats"] = {
            "enabled": True,
            "windowMarkets": ROLLING_MARKET_WINDOW,
            "strategies": list(STRATEGIES),
            "sameMarketGapTradesAggregatedBeforeAverage": True,
            "flipDefinition": "CHOP_GUARD_CONFIRMED_REVERSAL",
            "requiresFinalizedEvaluableMarket": True,
        }
        return payload


# v2 installs ImmediateChopBreakerPaperEngine into the existing launcher. Replace
# only that concrete engine class; the sidecar HTTP API, DB and all strategy rules
# stay unchanged.
base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = RollingStatsPaperEngine
