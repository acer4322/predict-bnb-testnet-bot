from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from . import poly_gap_live_v6 as v6
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

# V8 owns the live entry/exit defaults. V6's exit implementation reads its
# module globals at call time, so updating them here preserves the tested V6/V7
# flatten-first lifecycle while widening only the execution tolerance.
base.QUOTE_SLIPPAGE_BPS = ENTRY_SLIPPAGE_BPS
v6.ENTRY_SLIPPAGE_BPS = ENTRY_SLIPPAGE_BPS
v6.EXIT_SLIPPAGE_BPS = EXIT_SLIPPAGE_BPS


class MomentumTolerancePolyGapLiveEngine(ReArmingScalpPolyGapLiveEngine):
    """V8: keep the 3% signal requirement but allow momentum-driven entry repricing.

    The original dedicated live path required the signed BUY quote to retain the
    full SCALP_MIN_EDGE after the quote response. In fast markets that rejected
    entries exactly when Binance began moving toward Polymarket.

    V8 separates *signal quality* from *execution tolerance*:
    - the visible Poly-vs-Binance Ask gap must still be >= SCALP_MIN_EDGE before
      a round is created;
    - after the signed BUY quote returns, Polymarket must still point in the same
      confident direction and remain fresh;
    - the signed BUY average may deteriorate by at most 10% versus the Binance
      Ask observed when the signal fired (configurable in bps);
    - the quote no longer has to retain the original 3% executable edge;
    - ambiguous placements and unconfirmed positions remain hard market halts;
    - SELL keeps the wider V6 exit-priority tolerance (20% by default here).
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
            message = (
                f"signed BUY average {average:.6f} exceeds 10% entry cap {price_cap:.6f} "
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
        return payload


base.PolyGapLiveEngine = MomentumTolerancePolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
