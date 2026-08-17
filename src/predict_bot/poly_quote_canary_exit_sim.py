from __future__ import annotations

import sqlite3
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from .cross_oracle_strategies import SIM_DB_PATH, STRATEGIES
from .poly_quote_canary import MIN_QUOTE_COVERAGE, PolyQuoteCanary, _finite, _ms
from .poly_quote_canary_live_sizing import LiveSizedPolyQuoteCanary, _shares_from_wei

SETTLEMENT_REFRESH_SECONDS = 2.0


def _units_from_wei(value: Any) -> float | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return float(number / Decimal(10**18))


class ExitSimulatedPolyQuoteCanary(LiveSizedPolyQuoteCanary):
    """Measure real signed BUY quotes, but model exits without SELL get-quote.

    A Paper-only strategy never owns the simulated Prediction shares, so a real
    SELL get-quote is structurally biased toward Binance error -9000.  ENTRY is
    still measured with the real signed get-quote endpoint.  EXIT is instead
    recorded as two execution scenarios:

    * BEST_VISIBLE: sell only against the currently visible first-level Bid and
      never assume more size than that first level exposes.
    * NO_SELL_HOLD: sell zero shares and hold the whole simulated position to
      the official Binance Prediction settlement.

    The latter is an execution worst-case scenario, not necessarily the lower
    eventual PnL: if the held side wins, failing to exit can outperform an early
    sale.  Both outcomes are finalized only from the authoritative
    simulation.db market_settlements table.
    """

    def __init__(self, db_path: Any) -> None:
        self.exit_settlement_thread: threading.Thread | None = None
        super().__init__(db_path)

    def _create_schema(self) -> None:
        super()._create_schema()
        additions = {
            "execution_mode": "TEXT",
            "sim_exit_observed_at_ms": "INTEGER",
            "sim_exit_observation_lag_ms": "REAL",
            "sim_entry_cost_usdt": "REAL",
            "sim_entry_shares": "REAL",
            "sim_exit_bid": "REAL",
            "sim_exit_bid_size": "REAL",
            "sim_best_case_evaluable": "INTEGER",
            "sim_best_fill_shares": "REAL",
            "sim_best_fill_ratio": "REAL",
            "sim_best_unfilled_shares": "REAL",
            "sim_best_exit_proceeds_usdt": "REAL",
            "sim_best_exit_fee_usdt": "REAL",
            "sim_best_exit_net_usdt": "REAL",
            "sim_official_winner": "TEXT",
            "sim_best_remaining_payout_usdt": "REAL",
            "sim_best_final_pnl_usdt": "REAL",
            "sim_no_sell_payout_usdt": "REAL",
            "sim_no_sell_final_pnl_usdt": "REAL",
            "sim_settlement_status": "TEXT",
            "sim_finalized_at_ms": "INTEGER",
        }
        with self.db_lock:
            columns = {
                str(row[1])
                for row in self.db.execute(
                    "PRAGMA table_info(poly_quote_canary_attempts)"
                ).fetchall()
            }
            for name, ddl in additions.items():
                if name not in columns:
                    self.db.execute(
                        f"ALTER TABLE poly_quote_canary_attempts ADD COLUMN {name} {ddl}"
                    )
            self.db.commit()

    def start(self) -> None:
        super().start()
        if self.exit_settlement_thread is None or not self.exit_settlement_thread.is_alive():
            self.exit_settlement_thread = threading.Thread(
                target=self._exit_settlement_loop,
                name="poly-quote-canary-exit-settlement",
                daemon=True,
            )
            self.exit_settlement_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.exit_settlement_thread:
            self.exit_settlement_thread.join(timeout=2.5)
        super().stop()

    def _update_sim_fields(self, trade_id: int, **values: Any) -> None:
        allowed = {
            "execution_mode",
            "sim_exit_observed_at_ms",
            "sim_exit_observation_lag_ms",
            "sim_entry_cost_usdt",
            "sim_entry_shares",
            "sim_exit_bid",
            "sim_exit_bid_size",
            "sim_best_case_evaluable",
            "sim_best_fill_shares",
            "sim_best_fill_ratio",
            "sim_best_unfilled_shares",
            "sim_best_exit_proceeds_usdt",
            "sim_best_exit_fee_usdt",
            "sim_best_exit_net_usdt",
            "sim_official_winner",
            "sim_best_remaining_payout_usdt",
            "sim_best_final_pnl_usdt",
            "sim_no_sell_payout_usdt",
            "sim_no_sell_final_pnl_usdt",
            "sim_settlement_status",
            "sim_finalized_at_ms",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        updates["updated_at_ms"] = _ms()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.db_lock:
            self.db.execute(
                f"UPDATE poly_quote_canary_attempts SET {assignments} "
                "WHERE trade_id=? AND phase='EXIT'",
                (*updates.values(), int(trade_id)),
            )
            self.db.commit()

    def _entry_execution_row(self, trade_id: int) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT status, would_submit, quote_amount_in_wei,
                          quote_amount_out_wei, quote_average_price,
                          simulated_entry_stake_usdt, paper_stake_usdt,
                          fee_rate_bps
                     FROM poly_quote_canary_attempts
                    WHERE trade_id=? AND phase='ENTRY' LIMIT 1""",
                (int(trade_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def _process_event(self, event: dict[str, Any]) -> None:
        if str(event.get("phase") or "") != "EXIT":
            # ENTRY keeps the real Binance signed get-quote path, including the
            # currently configured live strategy size from the parent class.
            return super()._process_event(event)
        self._process_exit_simulation(dict(event))

    def _process_exit_simulation(self, event: dict[str, Any]) -> None:
        trade_id = int(event["trade_id"])
        signal_at_ms = int(event["signal_at_ms"])
        entry = self._entry_execution_row(trade_id)
        if entry is None:
            self._update_attempt(
                trade_id,
                "EXIT",
                status="EXIT_SIM_NOT_APPLICABLE_NO_ENTRY_CANARY",
                reason="no ENTRY signed quote canary exists for this Paper trade",
                would_submit=0,
            )
            self._update_sim_fields(
                trade_id,
                execution_mode="SIMULATED_EXIT_NO_SELL_QUOTE",
                sim_settlement_status="NOT_APPLICABLE",
            )
            return
        if int(entry.get("would_submit") or 0) != 1:
            self._update_attempt(
                trade_id,
                "EXIT",
                status="EXIT_SIM_NOT_APPLICABLE_ENTRY_FAILED",
                reason=(
                    "ENTRY signed quote did not pass simulated placement; "
                    "a real position would not exist to exit"
                ),
                would_submit=0,
            )
            self._update_sim_fields(
                trade_id,
                execution_mode="SIMULATED_EXIT_NO_SELL_QUOTE",
                sim_settlement_status="NOT_APPLICABLE",
            )
            return

        entry_cost = _units_from_wei(entry.get("quote_amount_in_wei"))
        if entry_cost is None:
            entry_cost = _finite(entry.get("simulated_entry_stake_usdt"))
        if entry_cost is None:
            entry_cost = _finite(entry.get("paper_stake_usdt"))
        entry_shares = _shares_from_wei(entry.get("quote_amount_out_wei"))
        if entry_shares is None:
            entry_price = _finite(entry.get("quote_average_price"))
            if entry_cost is not None and entry_price is not None and entry_price > 0:
                entry_shares = entry_cost / entry_price
        if entry_cost is None or entry_cost <= 0 or entry_shares is None or entry_shares <= 0:
            self._update_attempt(
                trade_id,
                "EXIT",
                status="EXIT_SIM_NOT_APPLICABLE_ENTRY_SIZE_UNKNOWN",
                reason="ENTRY signed quote cost/shares are unavailable",
                would_submit=0,
            )
            self._update_sim_fields(
                trade_id,
                execution_mode="SIMULATED_EXIT_NO_SELL_QUOTE",
                sim_settlement_status="NOT_APPLICABLE",
            )
            return

        observed_at_ms = _ms()
        latest = self._current_binance_state()
        market_id = int(event["market_id"])
        latest_market_id = 0
        try:
            latest_market_id = int((latest or {}).get("market_id") or 0)
        except (TypeError, ValueError):
            latest_market_id = 0
        side = str(event["side"]).upper()
        bid = _finite((latest or {}).get(f"{side.lower()}_bid"))
        bid_size = _finite((latest or {}).get(f"{side.lower()}_bid_size"))
        if latest_market_id != market_id:
            bid = None
            bid_size = None

        fee_bps = int(entry.get("fee_rate_bps") or 200)
        best_evaluable = bool(
            bid is not None and 0 < bid <= 1 and bid_size is not None and bid_size >= 0
        )
        if best_evaluable:
            fill_shares = min(entry_shares, max(0.0, float(bid_size)))
            fill_ratio = fill_shares / entry_shares
            proceeds = fill_shares * float(bid)
            exit_fee = proceeds * fee_bps / 10_000.0
            net_exit = proceeds - exit_fee
            unfilled = max(0.0, entry_shares - fill_shares)
            reason = (
                "best-visible exit uses only the current first-level Bid and its "
                "visible size; no deeper liquidity or perfect fill is assumed. "
                "no-sell scenario holds 100% to official settlement"
            )
        else:
            fill_shares = None
            fill_ratio = None
            proceeds = None
            exit_fee = None
            net_exit = None
            unfilled = None
            reason = (
                "current first-level Bid/depth unavailable or market mismatched; "
                "best-visible exit is not evaluable. no-sell scenario still waits "
                "for official settlement"
            )

        self._update_attempt(
            trade_id,
            "EXIT",
            quote_completed_at_ms=observed_at_ms,
            signal_to_quote_response_ms=max(0.0, observed_at_ms - signal_at_ms),
            adverse_price_move=(
                float(event["paper_price"]) - float(bid)
                if bid is not None else None
            ),
            signal_still_valid=1,
            would_submit=0,
            status="EXIT_SIM_WAITING_SETTLEMENT",
            reason=reason,
            quote_id_present=0,
        )
        self._update_sim_fields(
            trade_id,
            execution_mode="SIMULATED_EXIT_NO_SELL_QUOTE",
            sim_exit_observed_at_ms=observed_at_ms,
            sim_exit_observation_lag_ms=max(0.0, observed_at_ms - signal_at_ms),
            sim_entry_cost_usdt=entry_cost,
            sim_entry_shares=entry_shares,
            sim_exit_bid=bid,
            sim_exit_bid_size=bid_size,
            sim_best_case_evaluable=1 if best_evaluable else 0,
            sim_best_fill_shares=fill_shares,
            sim_best_fill_ratio=fill_ratio,
            sim_best_unfilled_shares=unfilled,
            sim_best_exit_proceeds_usdt=proceeds,
            sim_best_exit_fee_usdt=exit_fee,
            sim_best_exit_net_usdt=net_exit,
            sim_settlement_status="WAITING_OFFICIAL",
        )
        self._finalize_exit_settlements([market_id])

    def _exit_settlement_loop(self) -> None:
        while not self.stop_event.wait(SETTLEMENT_REFRESH_SECONDS):
            try:
                self._finalize_exit_settlements()
            except Exception:
                # Settlement enrichment is counterfactual research only. Never
                # degrade the ENTRY quote canary because this reader is late.
                pass

    def _official_winners(self, market_ids: list[int]) -> dict[int, str]:
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
                market_ids,
            ).fetchall()
            sim.close()
        except (sqlite3.Error, OSError):
            return {}
        return {int(row["market_id"]): str(row["official_winner"]) for row in rows}

    def _finalize_exit_settlements(self, restrict_market_ids: list[int] | None = None) -> None:
        with self.db_lock:
            if restrict_market_ids:
                placeholders = ",".join("?" for _ in restrict_market_ids)
                rows = self.db.execute(
                    f"""SELECT * FROM poly_quote_canary_attempts
                          WHERE phase='EXIT'
                            AND execution_mode='SIMULATED_EXIT_NO_SELL_QUOTE'
                            AND sim_settlement_status='WAITING_OFFICIAL'
                            AND binance_market_id IN ({placeholders})""",
                    [int(value) for value in restrict_market_ids],
                ).fetchall()
            else:
                rows = self.db.execute(
                    """SELECT * FROM poly_quote_canary_attempts
                        WHERE phase='EXIT'
                          AND execution_mode='SIMULATED_EXIT_NO_SELL_QUOTE'
                          AND sim_settlement_status='WAITING_OFFICIAL'"""
                ).fetchall()
        if not rows:
            return
        market_ids = sorted({int(row["binance_market_id"]) for row in rows})
        winners = self._official_winners(market_ids)
        if not winners:
            return

        finalized_at_ms = _ms()
        for row in rows:
            winner = winners.get(int(row["binance_market_id"]))
            if winner is None:
                continue
            side = str(row["side"])
            won = side == winner
            entry_cost = _finite(row["sim_entry_cost_usdt"])
            entry_shares = _finite(row["sim_entry_shares"])
            if entry_cost is None or entry_shares is None:
                continue
            no_sell_payout = entry_shares if won else 0.0
            no_sell_pnl = no_sell_payout - entry_cost

            best_evaluable = int(row["sim_best_case_evaluable"] or 0) == 1
            best_net = _finite(row["sim_best_exit_net_usdt"])
            unfilled = _finite(row["sim_best_unfilled_shares"])
            if best_evaluable and best_net is not None and unfilled is not None:
                remaining_payout = unfilled if won else 0.0
                best_final_pnl = best_net + remaining_payout - entry_cost
                final_status = "EXIT_SIM_FINALIZED"
            else:
                remaining_payout = None
                best_final_pnl = None
                final_status = "EXIT_SIM_FINALIZED_NO_BEST_DEPTH"

            self._update_sim_fields(
                int(row["trade_id"]),
                sim_official_winner=winner,
                sim_best_remaining_payout_usdt=remaining_payout,
                sim_best_final_pnl_usdt=best_final_pnl,
                sim_no_sell_payout_usdt=no_sell_payout,
                sim_no_sell_final_pnl_usdt=no_sell_pnl,
                sim_settlement_status="FINALIZED",
                sim_finalized_at_ms=finalized_at_ms,
            )
            self._update_attempt(
                int(row["trade_id"]),
                "EXIT",
                status=final_status,
                reason=(
                    f"official winner {winner}; best-visible final PnL "
                    f"{best_final_pnl if best_final_pnl is not None else 'not evaluable'}; "
                    f"no-sell hold final PnL {no_sell_pnl:.6f}"
                ),
                would_submit=0,
            )

    def _summary_for(self, strategy: str) -> dict[str, Any]:
        # Signed quote quality must measure ENTRY only. Legacy SELL -9000 rows
        # remain in the audit DB but are deliberately excluded from all rates.
        with self.db_lock:
            entry_rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_quote_canary_attempts
                        WHERE strategy=? AND phase='ENTRY' ORDER BY id ASC""",
                    (strategy,),
                ).fetchall()
            ]
            exit_rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_quote_canary_attempts
                        WHERE strategy=? AND phase='EXIT' ORDER BY id ASC""",
                    (strategy,),
                ).fetchall()
            ]
        completed = [
            row for row in entry_rows
            if str(row["status"]).startswith("PASS_")
            or str(row["status"]).startswith("FAIL_")
            or str(row["status"]) in {"QUOTE_REJECTED", "ERROR"}
        ]
        passed = [row for row in completed if int(row.get("would_submit") or 0) == 1]
        quote_rtts = [
            float(row["quote_rtt_ms"])
            for row in completed if row.get("quote_rtt_ms") is not None
        ]
        total_lags = [
            float(row["signal_to_quote_response_ms"])
            for row in completed if row.get("signal_to_quote_response_ms") is not None
        ]
        adverse = [
            float(row["adverse_price_move"])
            for row in completed if row.get("adverse_price_move") is not None
        ]
        simulated_exits = [
            row for row in exit_rows
            if str(row.get("execution_mode") or "") == "SIMULATED_EXIT_NO_SELL_QUOTE"
        ]
        finalized_exits = [
            row for row in simulated_exits
            if str(row.get("sim_settlement_status") or "") == "FINALIZED"
        ]
        best_pnls = [
            float(row["sim_best_final_pnl_usdt"])
            for row in finalized_exits
            if row.get("sim_best_final_pnl_usdt") is not None
        ]
        no_sell_pnls = [
            float(row["sim_no_sell_final_pnl_usdt"])
            for row in finalized_exits
            if row.get("sim_no_sell_final_pnl_usdt") is not None
        ]
        return {
            "attempts": len(entry_rows),
            "completed": len(completed),
            "wouldSubmit": len(passed),
            "wouldSubmitRate": len(passed) / len(completed) if completed else None,
            "quoteRejected": sum(str(row["status"]) == "QUOTE_REJECTED" for row in completed),
            "priceMoved": sum(str(row["status"]) == "FAIL_PRICE_MOVED" for row in completed),
            "signalGone": sum(str(row["status"]) == "FAIL_SIGNAL_GONE" for row in completed),
            "capacityFailed": sum(str(row["status"]) == "FAIL_QUOTE_CAPACITY" for row in completed),
            "expiryFailed": sum(str(row["status"]) == "FAIL_QUOTE_EXPIRY" for row in completed),
            "avgQuoteRttMs": sum(quote_rtts) / len(quote_rtts) if quote_rtts else None,
            "maxQuoteRttMs": max(quote_rtts) if quote_rtts else None,
            "avgSignalToQuoteMs": sum(total_lags) / len(total_lags) if total_lags else None,
            "maxSignalToQuoteMs": max(total_lags) if total_lags else None,
            "avgAdversePriceMove": sum(adverse) / len(adverse) if adverse else None,
            "entryQualityPrimary": True,
            "exitSimulations": len(simulated_exits),
            "exitFinalized": len(finalized_exits),
            "exitBestEvaluable": sum(
                int(row.get("sim_best_case_evaluable") or 0) == 1 for row in simulated_exits
            ),
            "avgBestVisibleFinalPnlUsdt": (
                sum(best_pnls) / len(best_pnls) if best_pnls else None
            ),
            "avgNoSellFinalPnlUsdt": (
                sum(no_sell_pnls) / len(no_sell_pnls) if no_sell_pnls else None
            ),
            "legacySellQuoteRejectedExcluded": sum(
                str(row.get("status") or "") == "QUOTE_REJECTED" for row in exit_rows
            ),
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        # The old mixed recent list is intentionally narrowed to ENTRY so the
        # dashboard cannot present simulated exits as if they were signed quotes.
        payload["recent"] = [
            row for row in payload.get("recent", [])
            if str(row.get("phase") or "") == "ENTRY"
        ]
        with self.db_lock:
            exits = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_quote_canary_attempts
                        WHERE phase='EXIT'
                          AND execution_mode='SIMULATED_EXIT_NO_SELL_QUOTE'
                        ORDER BY id DESC LIMIT 40"""
                ).fetchall()
            ]
        for row in exits:
            row.pop("token_id", None)
            row.pop("requested_amount_wei", None)
            row.pop("quote_amount_in_wei", None)
            row.pop("quote_amount_out_wei", None)
        payload.update(
            version="POLY_ENTRY_SIGNED_QUOTE_EXIT_SCENARIOS_V2",
            signedQuoteRequested=True,
            signedQuoteScope="ENTRY_ONLY",
            sellSignedQuoteRequested=False,
            exitExecutionMode="BEST_VISIBLE_PLUS_NO_SELL_HOLD",
            entryQualityPrimary=True,
            recentExitSimulations=exits,
        )
        sizing = payload.get("sizing")
        if isinstance(sizing, dict):
            sizing["exit"] = (
                "no signed SELL quote; best-visible first-level Bid/depth plus "
                "zero-fill hold-to-official-settlement scenario"
            )
        payload["exitSimulationRules"] = {
            "bestVisible": (
                "fill at current held-side best Bid only up to visible first-level bid size; "
                "remainder is held to official settlement"
            ),
            "noSellHold": "zero shares sold; entire signed-entry position held to official settlement",
            "exitFeeModel": "estimated proceeds * feeRateBps / 10000",
            "officialSettlementSource": str(SIM_DB_PATH),
            "legacySellQuoteRejectsExcludedFromEntryRate": True,
            "minimumEntryQuoteCoverage": MIN_QUOTE_COVERAGE,
        }
        return payload
