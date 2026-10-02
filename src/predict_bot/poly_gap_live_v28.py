from __future__ import annotations

import math
import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v8 import ENTRY_MAX_DETERIORATION_BPS, ENTRY_SLIPPAGE_BPS
from .poly_gap_live_v27 import LocalDbPaperGuardPolyGapLiveEngine


DEFAULT_TAKE_PROFIT_PRICE = min(
    0.99,
    max(0.01, float(os.environ.get("PREDICT_POLY_GAP_LIVE_TAKE_PROFIT_PRICE", "0.95"))),
)
DEFAULT_MAX_ENTRY_PRICE = min(
    0.99,
    max(0.01, float(os.environ.get("PREDICT_POLY_GAP_LIVE_MAX_ENTRY_PRICE", "0.90"))),
)


class PriceRiskControlsPolyGapLiveEngine(LocalDbPaperGuardPolyGapLiveEngine):
    """V28: dashboard-editable absolute entry ceiling and take-profit exit.

    Two persistent price controls are added without weakening the existing V27
    safety lineage:

    - maxEntryPrice: a NEW BUY is forbidden when the Binance same-side Ask is at
      or above the configured ceiling. The signed BUY average is checked again
      immediately before placement so a quote that reprices above the ceiling is
      rejected even when the trigger Ask was still cheap enough.
    - takeProfitPrice: while a position is OPEN, the dedicated executor samples
      the held-side Binance best Bid. A Bid at or above the configured threshold
      starts the existing signed SELL MARKET/FOK + reconciliation path immediately.

    Exit intent is persisted on the round so TAKE_PROFIT remains distinct from a
    POLY_DIRECTION_FLIP across process restarts and SELL reconciliation. This is
    important because V26's same-market breaker must count only real reversal
    exits, never profitable take-profit exits.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._entry_price_blocked_this_tick = False
        self._last_entry_price_guard: dict[str, Any] | None = None
        self._last_take_profit_book: dict[str, Any] | None = None
        self._last_take_profit_error: str | None = None
        self._take_profit_announced_rounds: set[int] = set()
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row["name"])
                for row in self.db.execute("PRAGMA table_info(poly_gap_live_rounds)").fetchall()
            }
            if "exit_intent" not in columns:
                self.db.execute("ALTER TABLE poly_gap_live_rounds ADD COLUMN exit_intent TEXT")
                self.db.commit()

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        defaults = {
            "take_profit_price": f"{DEFAULT_TAKE_PROFIT_PRICE:.8f}",
            "max_entry_price": f"{DEFAULT_MAX_ENTRY_PRICE:.8f}",
        }
        now = base._now_ms()
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings.update(
            {
                "takeProfitPrice": float(
                    self._setting("take_profit_price", str(DEFAULT_TAKE_PROFIT_PRICE))
                ),
                "maxEntryPrice": float(
                    self._setting("max_entry_price", str(DEFAULT_MAX_ENTRY_PRICE))
                ),
            }
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        extra_keys = {"takeProfitPrice", "maxEntryPrice"}
        current = self._settings()
        candidate_take_profit = float(current["takeProfitPrice"])
        candidate_max_entry = float(current["maxEntryPrice"])

        if "takeProfitPrice" in values:
            try:
                candidate_take_profit = float(values["takeProfitPrice"])
            except (TypeError, ValueError) as exc:
                raise ValueError("takeProfitPrice must be a number") from exc
            if not math.isfinite(candidate_take_profit) or not 0.01 <= candidate_take_profit <= 0.99:
                raise ValueError("takeProfitPrice must be between 0.01 and 0.99")

        if "maxEntryPrice" in values:
            try:
                candidate_max_entry = float(values["maxEntryPrice"])
            except (TypeError, ValueError) as exc:
                raise ValueError("maxEntryPrice must be a number") from exc
            if not math.isfinite(candidate_max_entry) or not 0.01 <= candidate_max_entry <= 0.99:
                raise ValueError("maxEntryPrice must be between 0.01 and 0.99")

        if candidate_max_entry >= candidate_take_profit - 1e-12:
            raise ValueError("maxEntryPrice must be lower than takeProfitPrice")

        forwarded = {key: value for key, value in values.items() if key not in extra_keys}
        if forwarded:
            super().update_settings(forwarded)

        if "takeProfitPrice" in values:
            self._set_setting("take_profit_price", f"{candidate_take_profit:.8f}")
            self._event(
                "INFO",
                "TAKE_PROFIT_PRICE_UPDATED",
                None,
                None,
                f"take-profit held-side Bid threshold set to {candidate_take_profit:.4f}",
            )
        if "maxEntryPrice" in values:
            self._set_setting("max_entry_price", f"{candidate_max_entry:.8f}")
            self._event(
                "INFO",
                "MAX_ENTRY_PRICE_UPDATED",
                None,
                None,
                f"new BUY forbidden at Binance Ask >= {candidate_max_entry:.4f}",
            )
        return self.snapshot()

    def _set_exit_intent(self, round_id: int, intent: str) -> None:
        with self.db_lock:
            self.db.execute(
                "UPDATE poly_gap_live_rounds SET exit_intent=?, updated_at_ms=? WHERE id=?",
                (str(intent), base._now_ms(), int(round_id)),
            )
            self.db.commit()

    def _exit_intent(self, round_id: int) -> str | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT exit_intent FROM poly_gap_live_rounds WHERE id=?",
                (int(round_id),),
            ).fetchone()
        value = str(row["exit_intent"] or "").strip() if row else ""
        return value or None

    def _completed_reversal_exits_for_market(self, market_id: int) -> int:
        # New V28 rows carry an explicit exit_intent. Legacy rows predate the
        # take-profit feature, so an old CLOSED row with an exit signal remains a
        # valid backwards-compatible reversal-exit count.
        with self.db_lock:
            row = self.db.execute(
                """SELECT COUNT(*) AS n
                     FROM poly_gap_live_rounds
                    WHERE market_id=?
                      AND state='CLOSED'
                      AND (
                          exit_intent='POLY_DIRECTION_FLIP'
                          OR (exit_intent IS NULL AND exit_signal_at_ms IS NOT NULL)
                      )""",
                (int(market_id),),
            ).fetchone()
        return int(row["n"] if row else 0)

    def _finish_round(self, row: dict[str, Any], pnl: float, proceeds: float, reason: str) -> None:
        intent = self._exit_intent(int(row["id"]))
        semantic_reason = intent if intent in {"TAKE_PROFIT", "POLY_DIRECTION_FLIP"} else reason
        super()._finish_round(row, pnl, proceeds, semantic_reason)

    def _rolling_live_performance(self, current_market_id: int | None) -> dict[str, Any]:
        payload = super()._rolling_live_performance(current_market_id)
        market_ids = [int(value) for value in payload.get("marketIds") or []]
        if not market_ids:
            payload["reversalLossMarkets"] = 0
        else:
            placeholders = ",".join("?" for _ in market_ids)
            with self.db_lock:
                rows = self.db.execute(
                    f"""SELECT market_id,
                               SUM(COALESCE(pnl_usdt,0.0)) AS market_pnl,
                               MAX(CASE
                                   WHEN state='CLOSED' AND pnl_usdt<0 AND (
                                       exit_intent='POLY_DIRECTION_FLIP'
                                       OR (exit_intent IS NULL AND exit_signal_at_ms IS NOT NULL)
                                   ) THEN 1 ELSE 0 END) AS has_reversal_loss
                          FROM poly_gap_live_rounds
                         WHERE market_id IN ({placeholders})
                           AND state IN ('CLOSED','SETTLED')
                           AND pnl_usdt IS NOT NULL
                         GROUP BY market_id""",
                    market_ids,
                ).fetchall()
            payload["reversalLossMarkets"] = sum(
                1
                for row in rows
                if float(row["market_pnl"] or 0.0) < -1e-12
                and int(row["has_reversal_loss"] or 0) == 1
            )
        payload["reversalLossDefinition"] = (
            "market net PnL<0 and contains >=1 losing CLOSED Echtgeld round whose "
            "exit intent is POLY_DIRECTION_FLIP; legacy pre-V28 exit-signal rows remain compatible"
        )
        payload["takeProfitExitIsNotReversalLoss"] = True
        return payload

    def _direct_book(self, market: dict[str, Any], side: str) -> tuple[float | None, float | None, float]:
        ask, ask_size, rtt_ms = super()._direct_book(market, side)
        self._entry_price_blocked_this_tick = False
        cap = float(self._settings()["maxEntryPrice"])
        if ask is not None and ask + 1e-12 >= cap:
            self._entry_price_blocked_this_tick = True
            self._last_entry_price_guard = {
                "marketId": int(market["market_id"]),
                "side": str(side),
                "ask": ask,
                "maxEntryPrice": cap,
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return None, ask_size, rtt_ms
        if ask is not None:
            self._last_entry_price_guard = {
                "marketId": int(market["market_id"]),
                "side": str(side),
                "ask": ask,
                "maxEntryPrice": cap,
                "blocked": False,
                "checkedAtMs": base._now_ms(),
            }
        return ask, ask_size, rtt_ms

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        """V8 entry execution with an additional absolute signed-price ceiling."""
        if not self._entry_allowed():
            self._update_round(int(row["id"]), state="REJECTED", close_reason="ENTRY_DISABLED")
            return

        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            self._update_round(
                int(row["id"]), state="FAILED", error_kind="PREFLIGHT",
                error_message="live client unavailable",
            )
            return

        signal_poly_selected = base._finite(poly.get("selectedMid"))
        trigger_ask = base._finite(row.get("entry_binance_ask"))
        max_entry_price = float(self._settings()["maxEntryPrice"])
        if signal_poly_selected is None or trigger_ask is None or trigger_ask <= 0:
            self._update_round(
                int(row["id"]), state="REJECTED", close_reason="ENTRY_SIGNAL_DATA_INVALID",
            )
            return
        if trigger_ask + 1e-12 >= max_entry_price:
            message = (
                f"trigger Ask {trigger_ask:.6f} is at/above configured max entry "
                f"{max_entry_price:.6f}"
            )
            self._update_round(
                int(row["id"]), state="REJECTED",
                close_reason="ENTRY_ABOVE_MAX_ENTRY_PRICE",
                error_kind="ENTRY_ABOVE_MAX_ENTRY_PRICE",
                error_message=message,
            )
            return

        deterioration_cap = min(
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
                int(row["id"]), state="REJECTED",
                error_kind="ENTRY_QUOTE_REJECTED", error_message=str(exc)[:500],
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
            if current_selected is not None and average is not None and current_direction == row["side"]
            else None
        )
        deterioration_bps = (
            (average / trigger_ask - 1.0) * 10_000.0
            if average is not None and trigger_ask > 0 else None
        )
        effective_cap = min(deterioration_cap, max_entry_price)

        self.last_entry_latency = {
            "signalToQuoteStartMs": max(0, started_ms - int(row["entry_signal_at_ms"] or started_ms)),
            "quoteRttMs": rtt_ms,
            "signalToQuoteResponseMs": max(0, completed_ms - int(row["entry_signal_at_ms"] or completed_ms)),
            "edgeAfterQuote": edge_after,
            "triggerAsk": trigger_ask,
            "signedQuoteAverage": average,
            "signedQuotePriceCap": deterioration_cap,
            "configuredMaxEntryPrice": max_entry_price,
            "effectiveEntryPriceCap": effective_cap,
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
                int(row["id"]), state="REJECTED", close_reason="POLY_SIGNAL_GONE",
                error_kind="POLY_SIGNAL_GONE",
                error_message="Polymarket direction changed or became unavailable while BUY quote was in flight",
            )
            self._event(
                "INFO", "ENTRY_REJECTED_SIGNAL_GONE", int(row["market_id"]), int(row["id"]),
                "signed BUY quote returned after the Polymarket direction was no longer valid",
            )
            return

        if (
            not quote_id or average is None or shares is None or shares <= 0
            or not self._quote_expiry_safe(quote, client)
        ):
            self._update_round(
                int(row["id"]), state="REJECTED", close_reason="ENTRY_QUOTE_INVALID",
                error_kind="ENTRY_QUOTE_INVALID",
                error_message="BUY quote lacked executable id/average/shares/expiry",
            )
            return

        if average + 1e-12 >= max_entry_price:
            message = (
                f"signed BUY average {average:.6f} is at/above configured max entry "
                f"{max_entry_price:.6f}; BUY not submitted"
            )
            self._update_round(
                int(row["id"]), state="REJECTED",
                close_reason="ENTRY_SIGNED_QUOTE_ABOVE_MAX_ENTRY_PRICE",
                error_kind="ENTRY_SIGNED_QUOTE_ABOVE_MAX_ENTRY_PRICE",
                error_message=message,
            )
            self._event(
                "INFO", "ENTRY_MAX_PRICE_REJECTED", int(row["market_id"]), int(row["id"]), message,
            )
            return

        if average > deterioration_cap + 1e-12:
            cap_pct = ENTRY_MAX_DETERIORATION_BPS / 100.0
            message = (
                f"signed BUY average {average:.6f} exceeds {cap_pct:.2f}% entry cap "
                f"{deterioration_cap:.6f} from trigger Ask {trigger_ask:.6f}; "
                f"deterioration={deterioration_bps:.1f}bps"
            )
            self._update_round(
                int(row["id"]), state="REJECTED",
                close_reason="ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP",
                error_kind="ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP",
                error_message=message,
            )
            self._event(
                "INFO", "ENTRY_PRICE_CAP_REJECTED", int(row["market_id"]), int(row["id"]), message,
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
                int(row["market_id"]), f"ambiguous BUY placement: {exc}", int(row["id"]),
            )
            self._update_round(
                int(row["id"]), state="AMBIGUOUS",
                error_kind="ENTRY_PLACE_AMBIGUOUS", error_message=str(exc)[:500],
            )
            return
        except Exception as exc:
            self._update_round(
                int(row["id"]), state="FAILED",
                error_kind="ENTRY_PLACE_REJECTED", error_message=str(exc)[:500],
                close_reason="ENTRY_PLACE_REJECTED",
            )
            return

        placed_ms = base._now_ms()
        order_id = base._first_text(placed, ("orderId", "order_id", "id"))
        self._update_round(
            int(row["id"]), state="ENTRY_SYNC", entry_order_id=order_id,
            entry_placed_at_ms=placed_ms, entry_sync_started_at_ms=placed_ms,
        )
        self._event(
            "WARN", "ENTRY_PLACED", int(row["market_id"]), int(row["id"]),
            (
                f"round {row['round_no']} {row['side']} MARKET/FOK submitted; "
                f"triggerAsk={trigger_ask:.6f}; signedAverage={average:.6f}; "
                f"configuredMaxEntry={max_entry_price:.6f}; "
                f"deteriorationCap={deterioration_cap:.6f}; entrySlippage={ENTRY_SLIPPAGE_BPS}bps"
            ),
        )

    def _held_side_take_profit_bid(
        self, market: dict[str, Any], active: dict[str, Any]
    ) -> tuple[float | None, float | None, float | None]:
        now = time.monotonic()
        if now - self.last_binance_book_at < base.BINANCE_BOOK_MIN_INTERVAL:
            cached = self._last_take_profit_book or {}
            if (
                int(cached.get("marketId") or 0) == int(active["market_id"])
                and str(cached.get("side") or "") == str(active["side"])
            ):
                return (
                    base._finite(cached.get("bid")),
                    base._finite(cached.get("bidSize")),
                    base._finite(cached.get("bookRttMs")),
                )
            return None, None, None

        with self.lock:
            client = self.client
        if client is None:
            return None, None, None
        token_id = str(active.get("token_id") or market.get(f"{str(active['side']).lower()}_token_id") or "")
        if not token_id:
            return None, None, None

        started = time.monotonic()
        try:
            book = client.orderbook(int(active["market_id"]), token_id)
        except Exception as exc:
            self.last_binance_book_at = time.monotonic()
            self._last_take_profit_error = str(exc)[:300]
            return None, None, None
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        self.last_binance_book_at = time.monotonic()
        bid, bid_size = base._best_level(book, "bid")
        snapshot = {
            "marketId": int(active["market_id"]),
            "side": str(active["side"]),
            "bid": bid,
            "bidSize": bid_size,
            "bookRttMs": rtt_ms,
            "observedAtMs": base._now_ms(),
        }
        self._last_take_profit_book = snapshot
        self._last_take_profit_error = None
        with self.lock:
            existing = dict(self.last_binance or {})
            if (
                int(existing.get("marketId") or 0) != int(active["market_id"])
                or str(existing.get("side") or "") != str(active["side"])
            ):
                existing = {"marketId": int(active["market_id"]), "side": str(active["side"])}
            existing.update(snapshot)
            self.last_binance = existing
        return bid, bid_size, rtt_ms

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        # Calls reaching this override are the inherited V12 stable Poly reversal
        # path. The take-profit path deliberately calls super()._exit_round after
        # persisting TAKE_PROFIT so the two intents cannot overwrite one another.
        self._set_exit_intent(int(row["id"]), "POLY_DIRECTION_FLIP")
        return super()._exit_round(row, signal_ms)

    def _tick(self) -> None:
        active = self._current_active_round()
        if active is not None and str(active.get("state") or "") == "OPEN":
            market = self._prime_market()
            now_ms = base._now_ms()
            if (
                isinstance(market, dict)
                and int(market.get("market_id") or 0) == int(active["market_id"])
                and now_ms < int(market.get("end_ms") or 0)
            ):
                bid, _bid_size, _rtt = self._held_side_take_profit_bid(market, active)
                take_profit = float(self._settings()["takeProfitPrice"])
                if bid is not None and bid + 1e-12 >= take_profit:
                    round_id = int(active["id"])
                    self._set_exit_intent(round_id, "TAKE_PROFIT")
                    clear_candidate = getattr(self, "_clear_exit_candidate", None)
                    if callable(clear_candidate):
                        clear_candidate("take-profit threshold reached before Poly reversal confirmation")
                    if round_id not in self._take_profit_announced_rounds:
                        self._take_profit_announced_rounds.add(round_id)
                        self._event(
                            "INFO", "TAKE_PROFIT_TRIGGERED", int(active["market_id"]), round_id,
                            (
                                f"held-side Binance best Bid {bid:.6f} reached take-profit "
                                f"threshold {take_profit:.6f}; starting signed SELL"
                            ),
                        )
                    self.status = "TAKE_PROFIT_TRIGGERED"
                    self.last_error = None
                    # Skip this class's _exit_round override so the persisted
                    # TAKE_PROFIT intent is not relabeled as a Poly reversal.
                    super()._exit_round(active, now_ms)
                    return

        self._entry_price_blocked_this_tick = False
        result = super()._tick()
        if self._entry_price_blocked_this_tick and self._current_active_round() is None:
            self.status = "BLOCKED_ENTRY_PRICE_CEILING"
            guard = self._last_entry_price_guard or {}
            self.last_error = (
                f"Binance {guard.get('side') or ''} Ask {guard.get('ask')} is at/above "
                f"configured max entry {guard.get('maxEntryPrice')}; no new BUY"
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        settings = self._settings()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V28"
        payload["settings"] = settings
        payload["priceRiskControls"] = {
            "takeProfitPrice": float(settings["takeProfitPrice"]),
            "takeProfitTrigger": "HELD_SIDE_BINANCE_BEST_BID_GTE_THRESHOLD",
            "maxEntryPrice": float(settings["maxEntryPrice"]),
            "entryBlockTrigger": "BINANCE_SAME_SIDE_ASK_GTE_THRESHOLD",
            "signedBuyAverageMustStayBelowMaxEntry": True,
            "takeProfitUsesExistingSignedSellReconciliation": True,
            "takeProfitCountsAsReversalExit": False,
            "currentEntryGuard": dict(self._last_entry_price_guard or {}),
            "currentTakeProfitBook": dict(self._last_take_profit_book or {}),
            "takeProfitBookError": self._last_take_profit_error,
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            {
                "takeProfitEditableFromDashboard": True,
                "maxEntryPriceEditableFromDashboard": True,
                "takeProfitBidPollMinIntervalMs": int(base.BINANCE_BOOK_MIN_INTERVAL * 1000),
                "takeProfitExitIntentPersisted": True,
            }
        )
        return payload


base.PolyGapLiveEngine = PriceRiskControlsPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
