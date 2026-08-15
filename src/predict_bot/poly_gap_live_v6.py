from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v5 import OperationalMetricsPolyGapLiveEngine


ENTRY_SLIPPAGE_BPS = max(
    0,
    int(
        os.environ.get(
            "PREDICT_POLY_GAP_LIVE_ENTRY_SLIPPAGE_BPS",
            os.environ.get("PREDICT_POLY_GAP_LIVE_SLIPPAGE_BPS", "100"),
        )
    ),
)
EXIT_SLIPPAGE_BPS = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_SLIPPAGE_BPS", "300")),
)
EXIT_POSITION_SYNC_TIMEOUT_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_POSITION_SYNC_TIMEOUT_MS", "750")),
)

# The base entry implementation reads this module-global value. Dedicated V6
# keeps its default at the previous 100 bps while allowing an explicit entry-only
# override without coupling it to the wider SELL tolerance below.
base.QUOTE_SLIPPAGE_BPS = ENTRY_SLIPPAGE_BPS


class ExitPriorityPolyGapLiveEngine(OperationalMetricsPolyGapLiveEngine):
    """V6: keep entry strict while making an already-open position easier to flatten.

    Entry behavior remains inherited unchanged: the signed BUY quote uses the
    dedicated entry slippage setting (100 bps by default) and executable
    Poly-vs-signed-quote edge must still remain at or above SCALP_MIN_EDGE.

    Exit behavior is intentionally asymmetric:
    - signed SELL quote/place use a dedicated, wider slippage tolerance;
    - FOK position reconciliation retries from a fresh position read after a short
      timeout instead of waiting the entry-oriented five-second sync window;
    - exit execution outcomes are exposed separately from entry execution quality.
    """

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

    def _sync_exit(self, row: dict[str, Any]) -> None:
        try:
            shares = self._position_shares(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"exit position sync: {str(exc)[:300]}"
            return
        if shares is not None and shares <= 1e-9:
            proceeds = base._finite(row.get("exit_proceeds_usdt")) or 0.0
            cost = base._finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
            self._event(
                "INFO",
                "EXIT_CONFIRMED",
                int(row["market_id"]),
                int(row["id"]),
                f"wallet flat after SELL FOK in <= {EXIT_POSITION_SYNC_TIMEOUT_MS}ms reconciliation window",
            )
            self._finish_round(row, proceeds - cost, proceeds, "POLY_DIRECTION_FLIP")
            return

        started = int(row.get("exit_sync_started_at_ms") or base._now_ms())
        if base._now_ms() - started > EXIT_POSITION_SYNC_TIMEOUT_MS:
            self._update_round(
                int(row["id"]),
                state="OPEN",
                shares=shares,
                error_kind="EXIT_NOT_FLAT",
                error_message=(
                    "SELL FOK did not leave the wallet flat inside the dedicated "
                    f"{EXIT_POSITION_SYNC_TIMEOUT_MS}ms exit sync window; retrying from a fresh position read"
                ),
            )
            self._event(
                "WARN",
                "EXIT_RETRY_NOT_FLAT",
                int(row["market_id"]),
                int(row["id"]),
                f"remainingShares={shares}; fresh SELL quote will be requested",
            )

    def _exit_execution_metrics(self) -> dict[str, Any]:
        with self.db_lock:
            totals = self.db.execute(
                """SELECT
                       COALESCE(SUM(CASE WHEN exit_signal_at_ms IS NOT NULL THEN 1 ELSE 0 END),0) AS attempts,
                       COALESCE(SUM(CASE WHEN exit_signal_at_ms IS NOT NULL AND state='CLOSED' AND close_reason IN ('POLY_DIRECTION_FLIP','POSITION_ALREADY_FLAT') THEN 1 ELSE 0 END),0) AS confirmed,
                       COALESCE(SUM(CASE WHEN error_kind='EXIT_PLACE_AMBIGUOUS' THEN 1 ELSE 0 END),0) AS ambiguous_current
                   FROM poly_gap_live_rounds"""
            ).fetchone()
            event_rows = self.db.execute(
                """SELECT event_type, COUNT(*) AS n, COUNT(DISTINCT round_id) AS rounds
                     FROM poly_gap_live_events
                    WHERE event_type IN (
                        'EXIT_PLACED','EXIT_CONFIRMED','EXIT_QUOTE_REJECTED',
                        'EXIT_QUOTE_INVALID','EXIT_PLACE_REJECTED','EXIT_RETRY_NOT_FLAT'
                    )
                    GROUP BY event_type"""
            ).fetchall()
        events = {
            str(row["event_type"]): {
                "events": int(row["n"]),
                "rounds": int(row["rounds"]),
            }
            for row in event_rows
        }
        attempts = int(totals["attempts"] if totals else 0)
        confirmed = int(totals["confirmed"] if totals else 0)
        return {
            "attempts": attempts,
            "submitted": int((events.get("EXIT_PLACED") or {}).get("rounds", 0)),
            "confirmed": confirmed,
            "successRate": confirmed / attempts if attempts else None,
            "quoteRejectedEvents": int((events.get("EXIT_QUOTE_REJECTED") or {}).get("events", 0)),
            "invalidQuoteEvents": int((events.get("EXIT_QUOTE_INVALID") or {}).get("events", 0)),
            "placeRejectedEvents": int((events.get("EXIT_PLACE_REJECTED") or {}).get("events", 0)),
            "notFlatRetryEvents": int((events.get("EXIT_RETRY_NOT_FLAT") or {}).get("events", 0)),
            "ambiguousCurrent": int(totals["ambiguous_current"] if totals else 0),
            "definition": "rounds confirmed flat after an exit signal / rounds with an exit signal",
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V6"
        payload["exitExecution"] = self._exit_execution_metrics()
        payload["executionTuning"] = {
            "entrySlippageBps": ENTRY_SLIPPAGE_BPS,
            "exitSlippageBps": EXIT_SLIPPAGE_BPS,
            "entryPositionSyncTimeoutMs": base.POSITION_SYNC_TIMEOUT_MS,
            "exitPositionSyncTimeoutMs": EXIT_POSITION_SYNC_TIMEOUT_MS,
            "entryEdgeRuleChanged": False,
            "minimumExecutableEntryEdge": base.SCALP_MIN_EDGE,
        }
        return payload


base.PolyGapLiveEngine = ExitPriorityPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
