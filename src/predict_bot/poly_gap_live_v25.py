from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v24 import TieredLossGuardPolyGapLiveEngine


ROLLING_LIVE_MARKETS = 10
LIVE_RESULT_STATES = ("CLOSED", "SETTLED")


class RollingPerformancePolyGapLiveEngine(TieredLossGuardPolyGapLiveEngine):
    """V25: expose recent 10-market Echtgeld performance diagnostics.

    A game is one distinct completed Binance 5m market, not one scalp round.
    Same-market round PnL is summed before win/loss and average-PnL statistics are
    computed. The current market is excluded even if it already contains closed
    rounds, preventing a still-tradable market from entering the rolling sample.

    A reversal-loss game is counted once only when the whole market finishes net
    negative and at least one real-money round in that market actually CLOSED at
    a loss after an exit signal was emitted. This uses execution/accounting
    evidence rather than a fragile close_reason string, so reconciled SELL fills
    are still classified correctly. Official SETTLED losses without a filled exit
    are not called reversal-loss games.
    """

    def _rolling_live_performance(self, current_market_id: int | None) -> dict[str, Any]:
        current_id = int(current_market_id or 0)
        with self.db_lock:
            rows = self.db.execute(
                """
                WITH completed_markets AS (
                    SELECT
                        market_id,
                        SUM(COALESCE(pnl_usdt, 0.0)) AS market_pnl,
                        MAX(updated_at_ms) AS last_updated_at_ms,
                        MAX(
                            CASE
                                WHEN state='CLOSED'
                                 AND pnl_usdt < 0
                                 AND exit_signal_at_ms IS NOT NULL
                                THEN 1 ELSE 0
                            END
                        ) AS has_reversal_loss_round
                    FROM poly_gap_live_rounds
                    WHERE state IN ('CLOSED','SETTLED')
                      AND pnl_usdt IS NOT NULL
                      AND (? <= 0 OR market_id <> ?)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM poly_gap_live_rounds active
                          WHERE active.market_id = poly_gap_live_rounds.market_id
                            AND active.state IN (
                                'ENTRY_QUOTE','ENTRY_SYNC','OPEN','EXIT_QUOTE','EXIT_SYNC',
                                'WAITING_SETTLEMENT','SETTLEMENT_WAITING_EXIT_RECONCILIATION'
                            )
                      )
                    GROUP BY market_id
                    ORDER BY last_updated_at_ms DESC, market_id DESC
                    LIMIT ?
                )
                SELECT market_id, market_pnl, last_updated_at_ms, has_reversal_loss_round
                FROM completed_markets
                ORDER BY last_updated_at_ms DESC, market_id DESC
                """,
                (current_id, current_id, ROLLING_LIVE_MARKETS),
            ).fetchall()

        count = len(rows)
        pnls = [float(row["market_pnl"] or 0.0) for row in rows]
        wins = sum(value > 1e-12 for value in pnls)
        losses = sum(value < -1e-12 for value in pnls)
        neutral = count - wins - losses
        decided = wins + losses
        total_pnl = sum(pnls)
        reversal_loss_markets = sum(
            1
            for row in rows
            if float(row["market_pnl"] or 0.0) < -1e-12
            and int(row["has_reversal_loss_round"] or 0) == 1
        )
        return {
            "windowMarkets": ROLLING_LIVE_MARKETS,
            "markets": count,
            "wins": wins,
            "losses": losses,
            "neutral": neutral,
            "winRate": wins / decided if decided else None,
            "totalPnlUsdt": total_pnl,
            "averagePnlUsdt": total_pnl / count if count else None,
            "reversalLossMarkets": reversal_loss_markets,
            "marketIds": [int(row["market_id"]) for row in rows],
            "basis": "distinct completed Binance 5m markets; same-market round PnL aggregated first",
            "reversalLossDefinition": (
                "market net PnL<0 and contains >=1 CLOSED Echtgeld round with pnl_usdt<0 "
                "and exit_signal_at_ms present"
            ),
            "currentMarketExcluded": True,
            "officialSettlementLossWithoutFilledExitIsNotReversalLoss": True,
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        market = payload.get("market")
        current_market_id = None
        if isinstance(market, dict):
            try:
                current_market_id = int(market.get("market_id") or 0) or None
            except (TypeError, ValueError):
                current_market_id = None
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V25"
        payload["rolling10Performance"] = self._rolling_live_performance(current_market_id)
        return payload


base.PolyGapLiveEngine = RollingPerformancePolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
