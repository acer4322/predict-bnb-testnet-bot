from __future__ import annotations

import sqlite3
from typing import Any

from .cross_oracle_strategies import SIM_DB_PATH, STRATEGIES
from .poly_quote_canary import _finite
from .poly_quote_canary_exit_sim import _units_from_wei
from .poly_quote_canary_exit_sim_v4 import SafeDurableExitSimulatedPolyQuoteCanary
from .poly_quote_canary_live_sizing import _shares_from_wei


class ScenarioSummaryPolyQuoteCanary(SafeDurableExitSimulatedPolyQuoteCanary):
    """V5: add apples-to-apples best/worst gross portfolio summaries.

    The original strategy summary is Paper Ask/Bid accounting.  These scenario
    summaries intentionally include only trades whose real signed ENTRY quote
    passed PASS_SIMULATED_PLACE (would_submit=1), so they answer the more useful
    question: what would the strategy look like after filtering out entries that
    were not actually quote-executable?

    BEST:
      * if an exit signal exists, use the persisted best-visible execution
        envelope (first-level depth when available, otherwise the explicitly
        labelled UPPER_BOUND_NO_DEPTH envelope), then hold any unfilled shares
        to official settlement;
      * if no exit occurs, hold to official settlement.

    WORST EXECUTION:
      * sell zero shares at every exit signal and hold the entire signed-entry
        position to official settlement.

    Both portfolio PnLs are GROSS: entry cost is subtracted, but the simulated
    exit fee is not, matching the original three strategy cards' gross-PnL
    convention.  Exit-fee estimates remain available in the detailed exit table.
    """

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "trades": 0,
            "open": 0,
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "winRate": None,
            "grossPnlUsdt": 0.0,
        }

    @staticmethod
    def _finish_summary(summary: dict[str, Any]) -> dict[str, Any]:
        wins = int(summary["wins"])
        losses = int(summary["losses"])
        summary["winRate"] = wins / (wins + losses) if wins + losses else None
        return summary

    @staticmethod
    def _add_closed(summary: dict[str, Any], pnl: float) -> None:
        summary["closed"] += 1
        summary["grossPnlUsdt"] += float(pnl)
        if pnl > 0:
            summary["wins"] += 1
        elif pnl < 0:
            summary["losses"] += 1

    def _official_winner_map(self, market_ids: list[int]) -> dict[int, str]:
        if not market_ids or not SIM_DB_PATH.exists():
            return {}
        try:
            sim = sqlite3.connect(f"file:{SIM_DB_PATH}?mode=ro", uri=True, timeout=1.0)
            sim.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in market_ids)
            rows = sim.execute(
                f"""SELECT market_id, official_winner
                       FROM market_settlements
                      WHERE market_id IN ({placeholders})
                        AND status='OFFICIAL'
                        AND official_winner IN ('UP','DOWN')""",
                [int(value) for value in market_ids],
            ).fetchall()
            sim.close()
        except (sqlite3.Error, OSError):
            return {}
        return {int(row["market_id"]): str(row["official_winner"]) for row in rows}

    def _scenario_rows(self, strategy: str) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT
                       t.id AS trade_id,
                       t.strategy,
                       t.binance_market_id,
                       t.side,
                       t.status AS paper_status,
                       e.status AS entry_canary_status,
                       e.would_submit AS entry_would_submit,
                       e.quote_amount_in_wei AS entry_amount_in_wei,
                       e.quote_amount_out_wei AS entry_amount_out_wei,
                       e.quote_average_price AS entry_quote_average_price,
                       e.simulated_entry_stake_usdt,
                       e.paper_stake_usdt AS canary_paper_stake_usdt,
                       x.execution_mode AS exit_execution_mode,
                       x.sim_best_case_evaluable,
                       x.sim_best_depth_mode,
                       x.sim_best_exit_proceeds_usdt,
                       x.sim_best_unfilled_shares,
                       x.sim_best_fill_ratio,
                       x.sim_settlement_status
                  FROM cross_oracle_strategy_trades t
                  JOIN poly_quote_canary_attempts e
                    ON e.trade_id=t.id AND e.phase='ENTRY'
             LEFT JOIN poly_quote_canary_attempts x
                    ON x.trade_id=t.id AND x.phase='EXIT'
                 WHERE t.strategy=?
              ORDER BY t.id ASC""",
                (str(strategy),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _entry_cost_and_shares(row: dict[str, Any]) -> tuple[float | None, float | None]:
        cost = _units_from_wei(row.get("entry_amount_in_wei"))
        if cost is None:
            cost = _finite(row.get("simulated_entry_stake_usdt"))
        if cost is None:
            cost = _finite(row.get("canary_paper_stake_usdt"))

        shares = _shares_from_wei(row.get("entry_amount_out_wei"))
        if shares is None:
            average = _finite(row.get("entry_quote_average_price"))
            if cost is not None and average is not None and average > 0:
                shares = cost / average
        return cost, shares

    def _portfolio_summary_for(self, strategy: str) -> dict[str, Any]:
        rows = self._scenario_rows(strategy)
        attempted_entries = len(rows)
        eligible = [row for row in rows if int(row.get("entry_would_submit") or 0) == 1]
        market_ids = sorted({int(row["binance_market_id"]) for row in eligible})
        winners = self._official_winner_map(market_ids)

        best = self._empty_summary()
        worst = self._empty_summary()
        best["trades"] = len(eligible)
        worst["trades"] = len(eligible)
        upper_bound_exits = 0
        first_level_exits = 0
        no_reliable_bid_exits = 0

        for row in eligible:
            cost, shares = self._entry_cost_and_shares(row)
            if cost is None or cost <= 0 or shares is None or shares <= 0:
                best["open"] += 1
                worst["open"] += 1
                continue

            market_id = int(row["binance_market_id"])
            side = str(row.get("side") or "")
            winner = winners.get(market_id)
            hold_pnl = (shares if winner == side else 0.0) - cost if winner is not None else None

            # WORST execution always means zero shares sold, full hold.
            if hold_pnl is None:
                worst["open"] += 1
            else:
                self._add_closed(worst, hold_pnl)

            exit_mode = str(row.get("exit_execution_mode") or "")
            has_exit_sim = exit_mode == "SIMULATED_EXIT_NO_SELL_QUOTE"
            if not has_exit_sim:
                if hold_pnl is None:
                    best["open"] += 1
                else:
                    self._add_closed(best, hold_pnl)
                continue

            depth_mode = str(row.get("sim_best_depth_mode") or "")
            if depth_mode == "FIRST_LEVEL_DEPTH":
                first_level_exits += 1
            elif depth_mode == "UPPER_BOUND_NO_DEPTH":
                upper_bound_exits += 1
            elif depth_mode == "NO_RELIABLE_BID":
                no_reliable_bid_exits += 1

            best_evaluable = int(row.get("sim_best_case_evaluable") or 0) == 1
            proceeds = _finite(row.get("sim_best_exit_proceeds_usdt"))
            unfilled = _finite(row.get("sim_best_unfilled_shares"))
            if best_evaluable and proceeds is not None and unfilled is not None:
                # Full fill closes immediately; partial fill still needs official
                # settlement for the residual position.
                if unfilled <= 1e-12:
                    self._add_closed(best, proceeds - cost)
                elif winner is not None:
                    remaining_payout = unfilled if winner == side else 0.0
                    self._add_closed(best, proceeds + remaining_payout - cost)
                else:
                    best["open"] += 1
            else:
                # If a trustworthy best-exit price is unavailable, do not invent
                # one. Fall back to the only observable outcome: hold to settle.
                if hold_pnl is None:
                    best["open"] += 1
                else:
                    self._add_closed(best, hold_pnl)

        self._finish_summary(best)
        self._finish_summary(worst)
        return {
            "basis": "ENTRY_SIGNED_QUOTE_PASS_ONLY",
            "grossPnl": True,
            "attemptedEntries": attempted_entries,
            "eligibleEntries": len(eligible),
            "excludedEntryFailures": attempted_entries - len(eligible),
            "best": best,
            "worst": worst,
            "exitEnvelope": {
                "firstLevelDepth": first_level_exits,
                "upperBoundNoDepth": upper_bound_exits,
                "noReliableBid": no_reliable_bid_exits,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_ENTRY_REAL_QUOTE_EXIT_SCENARIOS_V5"
        payload["scenarioPortfolioSummaries"] = {
            strategy: self._portfolio_summary_for(strategy) for strategy in STRATEGIES
        }
        payload["scenarioPortfolioRules"] = {
            "entryUniverse": "only ENTRY signed quotes with would_submit=1",
            "best": (
                "best-visible exit envelope when an exit signal exists; first-level depth "
                "when available, otherwise explicit UPPER_BOUND_NO_DEPTH; residual shares "
                "hold to official settlement; no exit signal means hold to settlement"
            ),
            "worst": "zero shares sold on every exit signal; full hold to official settlement",
            "grossPnl": "fees excluded to match original three Paper strategy cards",
        }
        return payload
