from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v8 import MomentumTolerancePolyGapLiveEngine


ORDER_RECONCILE_INTERVAL_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ORDER_RECONCILE_INTERVAL_MS", "250")),
)
FILLED_POSITION_GRACE_MS = max(
    base.POSITION_SYNC_TIMEOUT_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_FILLED_POSITION_GRACE_MS", "15000")),
)

_FILLED_STATUSES = {
    "FILLED",
    "COMPLETED",
    "COMPLETE",
    "SUCCESS",
    "SUCCEEDED",
    "EXECUTED",
    "MATCHED",
}
_TERMINAL_NO_FILL_STATUSES = {
    "FAILED",
    "REJECTED",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "NOT_FILLED",
    "UNFILLED",
    "NO_FILL",
}
_ACTIVE_STATUSES = {
    "NEW",
    "OPEN",
    "PENDING",
    "PROCESSING",
    "PARTIALLY_FILLED",
    "PARTIAL",
}


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _order_id_from_node(node: dict[str, Any]) -> str:
    for key in ("orderId", "order_id", "orderID", "orderNo", "orderNumber"):
        if key in node and node.get(key) is not None:
            return _text(node.get(key))
    return ""


def _order_status_from_node(node: dict[str, Any]) -> str:
    for key in (
        "status",
        "orderStatus",
        "order_status",
        "state",
        "executionStatus",
        "execution_status",
    ):
        if key in node and node.get(key) is not None:
            return _text(node.get(key)).upper()
    return ""


def _find_order(payload: Any, order_id: str) -> dict[str, Any] | None:
    target = _text(order_id)
    if not target:
        return None
    for node in _walk(payload):
        if _order_id_from_node(node) == target:
            return dict(node)
    return None


class OrderReconciledPolyGapLiveEngine(MomentumTolerancePolyGapLiveEngine):
    """V9: distinguish a definite FOK no-fill from a truly ambiguous BUY.

    The earlier live path treated "place returned an order id, but the position
    endpoint did not show shares inside POSITION_SYNC_TIMEOUT_MS" as ambiguous.
    That is too conservative for MARKET/FOK because Binance can legitimately
    create an order record that ends with no fill.

    V9 reconciles the returned order id against active orders and order history:
    - position > 0 => ENTRY_CONFIRMED as before;
    - explicit FAILED/REJECTED/CANCELED/EXPIRED/no-fill order => definite entry
      failure, re-arm after the normal retry cooldown, do not halt the market;
    - explicit FILLED order => allow a longer grace window for position API lag;
    - active/pending order => keep reconciling;
    - only if neither position nor order state can establish the outcome after
      the grace window does the executor mark AMBIGUOUS and halt the market.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._next_order_reconcile_at: dict[int, float] = {}
        self.last_entry_order_reconcile: dict[str, Any] | None = None

    def _reconcile_entry_order(self, row: dict[str, Any]) -> tuple[str, str | None]:
        round_id = int(row["id"])
        now = time.monotonic()
        next_at = self._next_order_reconcile_at.get(round_id, 0.0)
        if now < next_at:
            cached = self.last_entry_order_reconcile or {}
            if int(cached.get("roundId") or -1) == round_id:
                return str(cached.get("classification") or "UNKNOWN"), cached.get("status")
            return "UNKNOWN", None
        self._next_order_reconcile_at[round_id] = now + ORDER_RECONCILE_INTERVAL_MS / 1000.0

        order_id = _text(row.get("entry_order_id"))
        if not order_id:
            return "UNKNOWN", None
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return "UNKNOWN", None

        found: dict[str, Any] | None = None
        source: str | None = None
        errors: list[str] = []

        try:
            payload = client.active_orders(
                wallet_address,
                market_id=int(row["market_id"]),
                limit=100,
            )
            found = _find_order(payload, order_id)
            if found is not None:
                source = "ACTIVE_ORDERS"
        except Exception as exc:
            errors.append(f"active_orders: {str(exc)[:180]}")

        if found is None:
            try:
                payload = client.order_history(wallet_address, limit=100)
                found = _find_order(payload, order_id)
                if found is not None:
                    source = "ORDER_HISTORY"
            except Exception as exc:
                errors.append(f"order_history: {str(exc)[:180]}")

        status = _order_status_from_node(found or {})
        if status in _FILLED_STATUSES:
            classification = "FILLED"
        elif status in _TERMINAL_NO_FILL_STATUSES:
            classification = "NO_FILL"
        elif status in _ACTIVE_STATUSES:
            classification = "ACTIVE"
        elif found is not None:
            classification = "FOUND_UNKNOWN"
        else:
            classification = "NOT_FOUND"

        self.last_entry_order_reconcile = {
            "roundId": round_id,
            "marketId": int(row["market_id"]),
            "orderId": order_id,
            "classification": classification,
            "status": status or None,
            "source": source,
            "checkedAtMs": base._now_ms(),
            "errors": errors,
        }
        return classification, status or None

    def _sync_entry(self, row: dict[str, Any]) -> None:
        try:
            shares = self._position_shares(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"entry position sync: {str(exc)[:300]}"
            shares = None

        if shares is not None and shares > 0:
            self._next_order_reconcile_at.pop(int(row["id"]), None)
            self._update_round(int(row["id"]), state="OPEN", shares=shares)
            self._event(
                "INFO",
                "ENTRY_CONFIRMED",
                int(row["market_id"]),
                int(row["id"]),
                f"position confirmed {shares:.8f} shares",
            )
            return

        classification, order_status = self._reconcile_entry_order(row)
        started = int(row.get("entry_sync_started_at_ms") or base._now_ms())
        elapsed_ms = base._now_ms() - started

        if classification == "NO_FILL":
            self._next_order_reconcile_at.pop(int(row["id"]), None)
            message = (
                f"BUY order {row.get('entry_order_id') or 'unknown'} finished without a fill"
                + (f"; status={order_status}" if order_status else "")
            )
            refreshed = self._update_round(
                int(row["id"]),
                state="REJECTED",
                error_kind="ENTRY_ORDER_NOT_FILLED",
                error_message=message,
                close_reason="ENTRY_ORDER_NOT_FILLED",
            )
            self._event(
                "INFO",
                "ENTRY_ORDER_NOT_FILLED",
                int(row["market_id"]),
                int(row["id"]),
                message + "; same-market retry remains allowed",
            )
            key = (int(row["market_id"]), str(row["side"]))
            self._schedule_retry(
                key,
                self._failure_cooldown_ms(refreshed or row),
                "ENTRY_ORDER_NOT_FILLED",
            )
            self.status = "ENTRY_RETRY_COOLDOWN"
            return

        if classification == "FILLED":
            self.status = "ENTRY_FILLED_WAITING_POSITION"
            if elapsed_ms <= FILLED_POSITION_GRACE_MS:
                return
            reason = (
                f"BUY order reports FILLED but wallet position was not visible after "
                f"{elapsed_ms}ms"
            )
            self._halt_market(int(row["market_id"]), reason, int(row["id"]))
            self._update_round(
                int(row["id"]),
                state="AMBIGUOUS",
                error_kind="ENTRY_FILLED_POSITION_UNCONFIRMED",
                error_message=reason,
            )
            return

        if classification == "ACTIVE":
            self.status = "ENTRY_ORDER_ACTIVE"
            if elapsed_ms <= FILLED_POSITION_GRACE_MS:
                return

        # Do not decide ambiguity at the old 5s timeout if order/history lookup is
        # still catching up. Give both sources the same bounded grace interval.
        if elapsed_ms <= FILLED_POSITION_GRACE_MS:
            self.status = "ENTRY_RECONCILING_ORDER"
            return

        reason = (
            f"BUY placement returned orderId {row.get('entry_order_id') or 'unknown'}, "
            f"but neither wallet position nor order status established the outcome "
            f"after {elapsed_ms}ms; classification={classification}"
        )
        self._halt_market(int(row["market_id"]), reason, int(row["id"]))
        self._update_round(
            int(row["id"]),
            state="AMBIGUOUS",
            error_kind="ENTRY_POSITION_AND_ORDER_UNCONFIRMED",
            error_message=reason,
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V9"
        payload["entryReconciliation"] = {
            "usesPosition": True,
            "usesActiveOrders": True,
            "usesOrderHistory": True,
            "orderReconcileIntervalMs": ORDER_RECONCILE_INTERVAL_MS,
            "filledPositionGraceMs": FILLED_POSITION_GRACE_MS,
            "definiteFokNoFillRearms": True,
            "unknownOutcomeStillHalts": True,
            "last": self.last_entry_order_reconcile,
        }
        return payload


base.PolyGapLiveEngine = OrderReconciledPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
