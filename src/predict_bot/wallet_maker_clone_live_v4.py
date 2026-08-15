from __future__ import annotations

import os
from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v3 as base


OPENING_WARMUP_SECONDS = max(
    0.0,
    float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_OPENING_WARMUP_SECONDS", "8")),
)
RESTING_CANCEL_SECONDS = max(
    5.0,
    min(299.0, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_RESTING_CANCEL_SECONDS", "60"))),
)
MAX_BOOK_SPREAD = max(
    0.01,
    min(0.99, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_MAX_BOOK_SPREAD", "0.25"))),
)
TERMINAL_ORDER_STATES = {"FILLED", "CANCELED", "REJECTED"}
TERMINAL_REMOTE_STATUSES = {"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}


def market_seconds_left(market: dict[str, Any], now_ms: int) -> float:
    return max(0.0, (int(market.get("end_ms") or 0) - int(now_ms)) / 1000.0)


def market_elapsed_seconds(market: dict[str, Any], now_ms: int) -> float:
    end_ms = int(market.get("end_ms") or 0)
    if end_ms <= 0:
        return 0.0
    start_ms = end_ms - 300_000
    return max(0.0, (int(now_ms) - start_ms) / 1000.0)


def book_is_sane(book: dict[str, Any], *, maximum_spread: float = MAX_BOOK_SPREAD) -> tuple[bool, str]:
    bid = core._finite(book.get("bestBid"))
    ask = core._finite(book.get("bestAsk"))
    if bid is None or ask is None:
        return False, "missing bid/ask"
    spread = ask - bid
    if spread <= 0:
        return False, f"crossed/locked book bid={bid:.4f} ask={ask:.4f}"
    if spread > maximum_spread + 1e-12:
        return False, f"spread {spread:.4f} exceeds safety maximum {maximum_spread:.4f}"
    return True, "OK"


class FinalFillSafeWalletMakerCloneEngine(base.MinimumOrderSafeWalletMakerCloneEngine):
    """V4: bound opening-book and late-cancel failure modes found in Echtgeld.

    The first Echtgeld sample showed two distinct hazards:
    * immediately after market rollover an almost-empty 0.01/0.99 book could be
      mistaken for a meaningful quoting surface and create floor/ceiling orders;
    * Binance can acknowledge a cancel while the vendor order is still filling,
      so CANCEL_REQUEST_ACCEPTED must never be treated as final exposure.

    V4 therefore waits briefly after each 5-minute open, refuses very wide books,
    proactively cancels resting inventory before the final minute, records cancel
    requests as CANCEL_PENDING, and reconciles Binance history until every order is
    truly terminal before rolling to the next market.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V4"

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        book = self.books.get(side) or {}
        sane, reason = book_is_sane(book)
        if not sane:
            bid = core._finite(book.get("bestBid"))
            ask = core._finite(book.get("bestAsk"))
            return {
                "side": side,
                "blocked": True,
                "reason": f"book sanity blocked: {reason}",
                "price": bid,
                "bestBid": bid,
                "bestAsk": ask,
            }
        return super()._plan_order(side, market)

    def _cancel_pair(self, pair_id: int, reason: str) -> bool:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            return False

        orders = self._pair_orders(pair_id)
        cancellable = [
            row
            for row in orders
            if row.get("order_id")
            and str(row.get("state") or "") not in TERMINAL_ORDER_STATES | {"CANCEL_PENDING"}
        ]
        if not cancellable:
            return True

        pair = self._current_pair() or {}
        market_id = int(pair.get("market_id") or 0) or None
        order_ids = [str(row["order_id"]) for row in cancellable]
        self._set_pair(pair_id, state="CANCEL_PENDING", close_reason=reason)
        self._event(
            "WARN",
            "PAIR_CANCEL_REQUESTED_V4",
            market_id,
            pair_id,
            f"reason={reason}; orderIds={order_ids}; awaiting terminal reconciliation",
        )
        try:
            client.batch_cancel_orders_raw(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                order_ids=order_ids,
            )
        except Exception as exc:
            self.last_error = f"batch cancel: {str(exc)[:400]}"
            self._event("ERROR", "PAIR_CANCEL_FAILED_V4", market_id, pair_id, self.last_error)
            return False

        for row in cancellable:
            self._update_order(
                int(row["id"]),
                state="CANCEL_PENDING",
                order_status="CANCEL_REQUEST_ACCEPTED",
            )
        return True

    def _final_reconcile_pair(self, pair: dict[str, Any]) -> bool:
        """Reconcile a cancel/rollover from active-orders + history until terminal.

        This intentionally does not trust CANCEL_REQUEST_ACCEPTED. A late fill can
        arrive after the cancel response, as observed in BNB Echtgeld market
        6844705, so filled cost/shares are always refreshed from the venue record.
        """
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return False

        orders = self._pair_orders(int(pair["id"]))
        ids = {str(row.get("order_id") or "") for row in orders if row.get("order_id")}
        if not ids:
            return True

        active: dict[str, dict[str, Any]] = {}
        history: dict[str, dict[str, Any]] = {}
        try:
            active_payload = client.active_orders(
                wallet_address,
                market_id=int(pair.get("market_id") or 0),
                limit=100,
            )
            active = {
                str(row.get("orderId") or row.get("order_id")): row
                for row in core._order_rows(active_payload)
            }
        except Exception as exc:
            self.last_error = f"cancel active reconciliation: {str(exc)[:300]}"

        try:
            history_payload = client.order_history(wallet_address, limit=100)
            history = {
                str(row.get("orderId") or row.get("order_id")): row
                for row in core._order_rows(history_payload)
            }
        except Exception as exc:
            self.last_error = f"cancel history reconciliation: {str(exc)[:300]}"

        terminal = True
        for order in orders:
            oid = str(order.get("order_id") or "")
            if not oid:
                continue
            remote = active.get(oid) or history.get(oid)
            if remote is None:
                if str(order.get("state") or "") not in TERMINAL_ORDER_STATES:
                    terminal = False
                continue

            update = self._normalize_order_update(remote)
            remote_status = str(update.get("order_status") or "").upper()
            if oid in active and remote_status not in TERMINAL_REMOTE_STATUSES:
                # Once a cancel has been requested, keep the explicit pending
                # state while still refreshing partial-fill quantities.
                update["state"] = "CANCEL_PENDING"
                terminal = False
            elif remote_status not in TERMINAL_REMOTE_STATUSES:
                terminal = False
            self._update_order(int(order["id"]), **update)

        refreshed = self._pair_orders(int(pair["id"]))
        if any(str(row.get("state") or "") not in TERMINAL_ORDER_STATES for row in refreshed):
            terminal = False

        if terminal:
            fills = [
                (
                    str(row.get("side") or ""),
                    float(row.get("filled_usdt_amount") or 0.0),
                    float(row.get("filled_share_qty") or 0.0),
                    str(row.get("order_status") or ""),
                )
                for row in refreshed
            ]
            self._set_pair(int(pair["id"]), state="CANCELED")
            self._event(
                "INFO",
                "PAIR_CANCEL_TERMINAL_RECONCILED_V4",
                int(pair.get("market_id") or 0) or None,
                int(pair["id"]),
                f"final venue states={fills}",
            )
        return terminal

    def _tick(self) -> None:
        settings = self._settings()
        if not core.MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not self._ensure_client():
            return

        interlock = self._normal_live_conflict()
        if interlock.get("blocked") is True:
            if settings["runtimeEnabled"]:
                self._set_setting("runtime_enabled", "0")
                self._event("ERROR", "NORMAL_LIVE_INTERLOCK_TRIPPED", None, None, str(interlock.get("reason")))
                self._cancel_recorded_active("NORMAL_LIVE_INTERLOCK")
            self.status = "BLOCKED_NORMAL_LIVE"
            return

        market = self._prime_market()
        if market is None:
            self.status = "WAITING_MARKET"
            return
        self._poll_books(market)

        pair = self._current_pair()
        if pair is not None and int(pair["market_id"]) != int(market["market_id"]):
            # Never mark the previous pair ROLLED until the venue confirms the
            # final terminal state. This closes the CANCEL_REQUEST_ACCEPTED race.
            if str(pair.get("state") or "") != "CANCEL_PENDING":
                self._cancel_pair(int(pair["id"]), "MARKET_ROLLOVER")
            if not self._final_reconcile_pair(pair):
                self.status = "ROLLOVER_RECONCILING_FINAL_FILLS"
                return
            self._set_pair(int(pair["id"]), state="ROLLED", close_reason="MARKET_ROLLOVER")
            pair = None

        if pair is None:
            pair = self._new_pair(market)

        # If a pause/cutoff already requested cancellation, finish that
        # reconciliation before doing anything else.
        if str(pair.get("state") or "") == "CANCEL_PENDING":
            if not self._final_reconcile_pair(pair):
                self.status = "CANCEL_PENDING_RECONCILIATION"
                return
            pair = self._current_pair() or pair

        # Normal fill reconciliation remains active while the order is live.
        self._reconcile_pair(pair, market)
        pair = self._current_pair() or pair

        now_ms = core._now_ms()
        seconds_left = market_seconds_left(market, now_ms)

        # Existing GTC orders are actively removed before the final minute.
        # V4 is deliberately more conservative than the target-wallet hypothesis
        # because multi-generation repricing is not implemented yet; a quote from
        # market open must not survive until settlement.
        live_orders = [
            row
            for row in self._pair_orders(int(pair["id"]))
            if row.get("order_id") and str(row.get("state") or "") not in TERMINAL_ORDER_STATES
        ]
        if seconds_left <= RESTING_CANCEL_SECONDS and live_orders:
            self._cancel_pair(int(pair["id"]), "PRE_SETTLEMENT_CUTOFF_V4")
            self._final_reconcile_pair(pair)
            self.status = "PRE_SETTLEMENT_CANCEL_PENDING"
            return

        if not settings["runtimeEnabled"]:
            self.status = "PAUSED"
            return
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return

        elapsed = market_elapsed_seconds(market, now_ms)
        if elapsed < OPENING_WARMUP_SECONDS:
            self.status = "OPENING_BOOK_WARMUP"
            return

        for side in ("UP", "DOWN"):
            sane, reason = book_is_sane(self.books.get(side) or {})
            if not sane:
                self.status = "WAITING_SANE_BOOK"
                self.last_error = f"{side} {reason}"
                return

        orders = self._pair_orders(int(pair["id"]))
        if not orders and str(pair.get("state") or "") in {"READY", "CANCELED"}:
            self._place_pair(pair, market)
            self.status = "PAIR_ACTIVE"
            return
        self.status = "PAIR_ACTIVE"

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        payload.setdefault("rules", {}).update(
            openingBookWarmupSecondsV4=OPENING_WARMUP_SECONDS,
            maximumBookSpreadV4=MAX_BOOK_SPREAD,
            restingOrdersCancelSecondsBeforeEndV4=RESTING_CANCEL_SECONDS,
            cancelRequestIsNotTerminalV4=True,
            finalFillReconciliationBeforeRolloverV4=True,
            staleOpeningFloorCeilingBookBlockedV4=True,
        )
        return payload


# Patch the complete V3 -> V2 -> V1 entrypoint chain.
base.WalletMakerCloneEngine = FinalFillSafeWalletMakerCloneEngine
base.base.WalletMakerCloneEngine = FinalFillSafeWalletMakerCloneEngine
base.base.base.WalletMakerCloneEngine = FinalFillSafeWalletMakerCloneEngine


def main() -> int:
    return base.base.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
