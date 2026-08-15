from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v7 import ReArmingScalpPolyGapLiveEngine


ENTRY_SLIPPAGE_BPS = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_SLIPPAGE_BPS", "1000")),
)
ENTRY_MAX_DETERIORATION_BPS = max(
    0,
    int(
        os.environ.get(
            "PREDICT_POLY_GAP_LIVE_ENTRY_MAX_PRICE_MOVE_BPS",
            str(ENTRY_SLIPPAGE_BPS),
        )
    ),
)
EXIT_SLIPPAGE_BPS = max(
    ENTRY_SLIPPAGE_BPS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_SLIPPAGE_BPS", "2000")),
)


class MomentumTolerancePolyGapLiveEngine(ReArmingScalpPolyGapLiveEngine):
    """V8: keep the 3% signal requirement but allow momentum-driven entry repricing.

    V8 separates signal quality from execution tolerance:
    - visible Poly-vs-Binance Ask gap must still be >= SCALP_MIN_EDGE;
    - Polymarket must remain fresh and keep the same confident direction while
      the signed BUY quote is in flight;
    - signed BUY average may deteriorate by at most 10% versus the trigger Ask
      (configurable in bps), matching the intended MARKET-order tolerance;
    - the signed quote no longer has to retain the original 3% edge;
    - SELL is exit-priority and keeps a wider 20% default tolerance;
    - ambiguous placements and unconfirmed positions remain hard market halts.
    """

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        if not self._entry_allowed():
            self._update_round(
                int(row["id"]), state="REJECTED", close_reason="ENTRY_DISABLED"
            )
            return

        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            self._update_round(
                int(row["id"]),
                state="FAILED",
                error_kind="PREFLIGHT",
                error_message="live client unavailable",
            )
            return

        signal_poly_selected = base._finite(poly.get("selectedMid"))
        trigger_ask = base._finite(row.get("entry_binance_ask"))
        if signal_poly_selected is None or trigger_ask is None or trigger_ask <= 0:
            self._update_round(
                int(row["id"]),
                state="REJECTED",
                close_reason="ENTRY_SIGNAL_DATA_INVALID",
            )
            return

        price_cap = min(
            1.0,
            trigger_ask * (1.0 + ENTRY_MAX_DETERIORATION_BPS / 10_000.0),
        )
        amount_in = base._to_wei(float(row["stake_usdt"]))
        started_ms = base._now_ms()
        started = time.monotonic()
        self._update_round(int(row["id"]), entry_quote_started_at_ms=started_ms)

        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=str(row["token_id"]),
                amount_in_wei=amount_in,
                price_limit=None,
                slippage_bps=ENTRY_SLIPPAGE_BPS,
                fee_rate_bps=int((self.market_cache or {}).get("fee_rate_bps") or 200),
                funding_source="MPC",
                side="BUY",
                order_type="MARKET",
            )
        except Exception as exc:
            self._update_round(
                int(row["id"]),
                state="REJECTED",
                error_kind="ENTRY_QUOTE_REJECTED",
                error_message=str(exc)[:500],
                close_reason="ENTRY_QUOTE_REJECTED",
            )
            return

        completed_ms = base._now_ms()
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        average = base._finite(quote.get("averagePrice"))
        quote_id = str(quote.get("quoteId") or "")
        amount_out_wei = str(quote.get("amountOut") or "")
        cost_usdt = base._from_wei(quote.get("amountIn"))
        shares = base._from_wei(quote.get("amountOut"))

        current_poly = self._poly_state()
        current_selected = base._finite((current_poly or {}).get("selectedMid"))
        current_direction = (current_poly or {}).get("direction")
        edge_after = (
            current_selected - average
            if current_selected is not None
            and average is not None
            and current_direction == row["side"]
            else None
        )
        deterioration_bps = (
            (average / trigger_ask - 1.0) * 10_000.0
            if average is not None and trigger_ask > 0
            else None
        )

        self.last_entry_latency = {
            "signalToQuoteStartMs": max(
                0, started_ms - int(row["entry_signal_at_ms"] or started_ms)
            ),
            "quoteRttMs": rtt_ms,
            "signalToQuoteResponseMs": max(
                0, completed_ms - int(row["entry_signal_at_ms"] or completed_ms)
            ),
            "edgeAfterQuote": edge_after,
            "triggerAsk": trigger_ask,
            "signedQuoteAverage": average,
            "signedQuotePriceCap": price_cap,
            "signedQuoteDeteriorationBps": deterioration_bps,
        }
        self._update_round(
            int(row["id"]),
            entry_quote_completed_at_ms=completed_ms,
            entry_quote_rtt_ms=rtt_ms,
            entry_quote_average=average,
            entry_quote_amount_in_wei=str(quote.get("amountIn") or ""),
            entry_quote_amount_out_wei=amount_out_wei,
            entry_quote_expire_at_ms=int(quote.get("expireAt") or 0) or None,
            entry_cost_usdt=cost_usdt,
            shares=shares,
        )

        if current_direction != row["side"] or current_selected is None:
            self._update_round(
                int(row["id"]),
                state="REJECTED",
                close_reason="POLY_SIGNAL_GONE",
                error_kind="POLY_SIGNAL_GONE",
                error_message="Polymarket direction changed or became unavailable while BUY quote was in flight",
            )
            self._event(
                "INFO",
                "ENTRY_REJECTED_SIGNAL_GONE",
                int(row["market_id"]),
                int(row["id"]),
                "signed BUY quote returned after the Polymarket direction was no longer valid",
            )
            return

        if (
            not quote_id
            or average is None
            or shares is None
            or shares <= 0
            or not self._quote_expiry_safe(quote, client)
        ):
            self._update_round(
                int(row["id"]),
                state="REJECTED",
                close_reason="ENTRY_QUOTE_INVALID",
                error_kind="ENTRY_QUOTE_INVALID",
                error_message="BUY quote lacked executable id/average/shares/expiry",
            )
            return

        if average > price_cap + 1e-12:
            cap_pct = ENTRY_MAX_DETERIORATION_BPS / 100.0
            message = (
                f"signed BUY average {average:.6f} exceeds {cap_pct:.2f}% entry cap {price_cap:.6f} "
                f"from trigger Ask {trigger_ask:.6f}; deterioration={deterioration_bps:.1f}bps"
            )
            self._update_round(
                int(row["id"]),
                state="REJECTED",
                close_reason="ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP",
                error_kind="ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP",
                error_message=message,
            )
            self._event(
                "INFO",
                "ENTRY_PRICE_CAP_REJECTED",
                int(row["market_id"]),
                int(row["id"]),
                message,
            )
            return

        try:
            placed = client.place_market_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                slippage_bps=ENTRY_SLIPPAGE_BPS,
                account_type=base.ACCOUNT_TYPE,
                funding_source="MPC",
            )
        except base.ApiTransportError as exc:
            self._halt_market(
                int(row["market_id"]),
                f"ambiguous BUY placement: {exc}",
                int(row["id"]),
            )
            self._update_round(
                int(row["id"]),
                state="AMBIGUOUS",
                error_kind="ENTRY_PLACE_AMBIGUOUS",
                error_message=str(exc)[:500],
            )
            return
        except Exception as exc:
            self._update_round(
                int(row["id"]),
                state="FAILED",
                error_kind="ENTRY_PLACE_REJECTED",
                error_message=str(exc)[:500],
                close_reason="ENTRY_PLACE_REJECTED",
            )
            return

        placed_ms = base._now_ms()
        order_id = base._first_text(placed, ("orderId", "order_id", "id"))
        self._update_round(
            int(row["id"]),
            state="ENTRY_SYNC",
            entry_order_id=order_id,
            entry_placed_at_ms=placed_ms,
            entry_sync_started_at_ms=placed_ms,
        )
        self._event(
            "WARN",
            "ENTRY_PLACED",
            int(row["market_id"]),
            int(row["id"]),
            (
                f"round {row['round_no']} {row['side']} MARKET/FOK submitted; "
                f"triggerAsk={trigger_ask:.6f}; signedAverage={average:.6f}; "
                f"cap={price_cap:.6f}; entrySlippage={ENTRY_SLIPPAGE_BPS}bps"
            ),
        )

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if not base.MASTER_ENABLED or client is None or not wallet_address or not wallet_id:
            self._halt_market(
                int(row["market_id"]),
                "exit required but live master/client is unavailable",
                int(row["id"]),
            )
            return
        try:
            available = self._position_shares(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"exit position read: {str(exc)[:300]}"
            self._event(
                "ERROR",
                "EXIT_POSITION_READ_FAILED",
                int(row["market_id"]),
                int(row["id"]),
                str(exc)[:500],
            )
            return
        if available is not None and available <= 1e-9:
            proceeds = base._finite(row.get("exit_proceeds_usdt")) or 0.0
            cost = base._finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
            self._event(
                "INFO",
                "EXIT_CONFIRMED",
                int(row["market_id"]),
                int(row["id"]),
                "position was already flat when exit was checked",
            )
            self._finish_round(row, proceeds - cost, proceeds, "POSITION_ALREADY_FLAT")
            return
        if available is None or available <= 0:
            return

        self._update_round(int(row["id"]), state="EXIT_QUOTE", exit_signal_at_ms=signal_ms)
        started_ms = base._now_ms()
        started = time.monotonic()
        self._update_round(int(row["id"]), exit_quote_started_at_ms=started_ms)
        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=str(row["token_id"]),
                amount_in_wei=base._to_wei(available),
                price_limit=None,
                slippage_bps=EXIT_SLIPPAGE_BPS,
                fee_rate_bps=int((self.market_cache or {}).get("fee_rate_bps") or 200),
                funding_source="MPC",
                side="SELL",
                order_type="MARKET",
            )
        except Exception as exc:
            message = str(exc)[:500]
            self._update_round(
                int(row["id"]),
                state="OPEN",
                error_kind="EXIT_QUOTE_REJECTED",
                error_message=message,
            )
            self._event(
                "WARN",
                "EXIT_QUOTE_REJECTED",
                int(row["market_id"]),
                int(row["id"]),
                message,
            )
            return

        completed_ms = base._now_ms()
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        average = base._finite(quote.get("averagePrice"))
        quote_id = str(quote.get("quoteId") or "")
        proceeds = base._from_wei(quote.get("amountOut"))
        self.last_exit_latency = {
            "signalToQuoteStartMs": max(0, started_ms - signal_ms),
            "quoteRttMs": rtt_ms,
            "signalToQuoteResponseMs": max(0, completed_ms - signal_ms),
        }
        self._update_round(
            int(row["id"]),
            exit_quote_completed_at_ms=completed_ms,
            exit_quote_rtt_ms=rtt_ms,
            exit_quote_average=average,
            exit_quote_amount_in_wei=str(quote.get("amountIn") or ""),
            exit_quote_amount_out_wei=str(quote.get("amountOut") or ""),
            exit_proceeds_usdt=proceeds,
        )
        if (
            not quote_id
            or proceeds is None
            or proceeds < 0
            or not self._quote_expiry_safe(quote, client)
        ):
            message = "SELL quote lacked executable id/proceeds/expiry"
            self._update_round(
                int(row["id"]),
                state="OPEN",
                error_kind="EXIT_QUOTE_INVALID",
                error_message=message,
            )
            self._event(
                "WARN",
                "EXIT_QUOTE_INVALID",
                int(row["market_id"]),
                int(row["id"]),
                message,
            )
            return
        try:
            placed = client.place_market_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                slippage_bps=EXIT_SLIPPAGE_BPS,
                account_type=base.ACCOUNT_TYPE,
                funding_source="MPC",
            )
        except base.ApiTransportError as exc:
            self._halt_market(
                int(row["market_id"]),
                f"ambiguous SELL placement: {exc}",
                int(row["id"]),
            )
            self._update_round(
                int(row["id"]),
                state="AMBIGUOUS",
                error_kind="EXIT_PLACE_AMBIGUOUS",
                error_message=str(exc)[:500],
            )
            return
        except Exception as exc:
            message = str(exc)[:500]
            self._update_round(
                int(row["id"]),
                state="OPEN",
                error_kind="EXIT_PLACE_REJECTED",
                error_message=message,
            )
            self._event(
                "WARN",
                "EXIT_PLACE_REJECTED",
                int(row["market_id"]),
                int(row["id"]),
                message,
            )
            return

        placed_ms = base._now_ms()
        order_id = base._first_text(placed, ("orderId", "order_id", "id"))
        self._update_round(
            int(row["id"]),
            state="EXIT_SYNC",
            exit_order_id=order_id,
            exit_placed_at_ms=placed_ms,
            exit_sync_started_at_ms=placed_ms,
        )
        self._event(
            "WARN",
            "EXIT_PLACED",
            int(row["market_id"]),
            int(row["id"]),
            f"round {row['round_no']} SELL MARKET/FOK submitted; slippage={EXIT_SLIPPAGE_BPS}bps",
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V8"
        tuning = payload.setdefault("executionTuning", {})
        tuning.update(
            {
                "entrySlippageBps": ENTRY_SLIPPAGE_BPS,
                "exitSlippageBps": EXIT_SLIPPAGE_BPS,
                "entryEdgeRuleChanged": True,
                "initialSignalMinimumEdge": base.SCALP_MIN_EDGE,
                "minimumExecutableEntryEdge": None,
                "entrySignedQuoteMaxDeteriorationBps": ENTRY_MAX_DETERIORATION_BPS,
                "entrySignedQuoteMaxDeteriorationPct": ENTRY_MAX_DETERIORATION_BPS / 100.0,
                "entryAcceptanceRule": (
                    "initial Poly-vs-Binance Ask gap >= threshold; Poly direction remains valid; "
                    "signed BUY average <= trigger Ask * (1 + max deterioration)"
                ),
                "exitPriority": True,
            }
        )
        with self.db_lock:
            row = self.db.execute(
                """SELECT COUNT(*) AS n FROM poly_gap_live_rounds
                    WHERE error_kind='ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP'
                       OR close_reason='ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP'"""
            ).fetchone()
        entry_execution = payload.setdefault("entryExecution", {})
        entry_execution["priceCapRejected"] = int(row["n"] if row else 0)
        return payload


base.PolyGapLiveEngine = MomentumTolerancePolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
