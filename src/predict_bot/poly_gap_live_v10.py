from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from . import poly_gap_live_v9 as v9
from .poly_gap_live_v9 import OrderReconciledPolyGapLiveEngine


EXIT_ORDER_RECONCILE_INTERVAL_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_ORDER_RECONCILE_INTERVAL_MS", "250")),
)
EXIT_ORDER_UNKNOWN_GRACE_MS = max(
    2_000,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_ORDER_UNKNOWN_GRACE_MS", "15000")),
)
POSITION_EPSILON = 1e-9

_FILLED_STATUSES = set(v9._FILLED_STATUSES)
_NO_FILL_STATUSES = set(v9._TERMINAL_NO_FILL_STATUSES) | {
    "EXPIRED_IN_MATCH",
}
_ACTIVE_STATUSES = set(v9._ACTIVE_STATUSES) | {
    "ACCEPTED",
    "PENDING_NEW",
}


def _filled_quantity(node: dict[str, Any]) -> float | None:
    for key in (
        "executedQty",
        "executedQuantity",
        "filledQty",
        "filledQuantity",
        "filledShares",
        "executedShares",
        "matchedQuantity",
        "cumQty",
    ):
        value = base._finite(node.get(key))
        if value is not None:
            return value
    return None


def _actual_exit_proceeds(node: dict[str, Any] | None) -> float | None:
    if not isinstance(node, dict):
        return None
    for key in (
        "amountOut",
        "proceeds",
        "receivedAmount",
        "executedQuoteQty",
        "filledQuoteAmount",
        "quoteAmount",
    ):
        value = base._finite(node.get(key))
        if value is not None and value >= 0:
            # Prediction API payloads may expose 18-decimal integer strings for
            # token amounts. Reuse the base converter only for obviously wei-like
            # magnitudes; normal decimal USDT values remain unchanged.
            if value > 1_000_000:
                converted = base._from_wei(node.get(key))
                if converted is not None:
                    return converted
            return value
    return None


class ExitOrderReconciledPolyGapLiveEngine(OrderReconciledPolyGapLiveEngine):
    """V10: never resend or settle across an unresolved SELL order.

    V6-V9 used only the position endpoint after a SELL FOK. If the position API
    lagged for ~750 ms, the round was returned to OPEN and another SELL could be
    submitted even though the first order was still active/filled/reserving the
    shares. That produced Binance -9000 "exceeded available shares" errors and
    could leave the round stuck until official settlement.

    V10 treats the returned SELL order id as an execution object that must be
    reconciled before any retry:
    - explicit FILLED => close the round and re-arm immediately;
    - explicit FOK no-fill/cancel/expire/reject => return to OPEN and allow a new
      SELL attempt;
    - active/unknown/not-found => keep EXIT_SYNC and do not submit another SELL;
    - unresolved outcome after a bounded grace => AMBIGUOUS + market halt;
    - official settlement is forbidden from pretending the position was held if
      an EXIT order still has an unresolved outcome.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._next_exit_order_reconcile_at: dict[int, float] = {}
        self.last_exit_order_reconcile: dict[str, Any] | None = None

    def _position_state(self, token_id: str) -> dict[str, Any]:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return {
                "availableShares": None,
                "totalShares": None,
                "totalSharesExplicit": False,
            }
        payload = client.position_by_token(wallet_address, token_id)
        available = base._first_number(
            payload,
            ("availableShares", "availableShareQty", "availableQuantity"),
        )
        total = base._first_number(
            payload,
            (
                "totalShares",
                "positionShares",
                "shares",
                "shareQty",
                "quantity",
            ),
        )
        explicit_total = total is not None
        if total is None:
            total = available
        if available is None:
            available = total
        return {
            "availableShares": available,
            "totalShares": total,
            "totalSharesExplicit": explicit_total,
        }

    def _reconcile_exit_order(self, row: dict[str, Any]) -> dict[str, Any]:
        round_id = int(row["id"])
        now = time.monotonic()
        next_at = self._next_exit_order_reconcile_at.get(round_id, 0.0)
        if now < next_at:
            cached = self.last_exit_order_reconcile or {}
            if int(cached.get("roundId") or -1) == round_id:
                return dict(cached)
        self._next_exit_order_reconcile_at[round_id] = (
            now + EXIT_ORDER_RECONCILE_INTERVAL_MS / 1000.0
        )

        order_id = str(row.get("exit_order_id") or "").strip()
        result: dict[str, Any] = {
            "roundId": round_id,
            "marketId": int(row["market_id"]),
            "orderId": order_id or None,
            "classification": "UNKNOWN",
            "status": None,
            "source": None,
            "checkedAtMs": base._now_ms(),
            "errors": [],
            "order": None,
        }
        if not order_id:
            self.last_exit_order_reconcile = result
            return result

        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            self.last_exit_order_reconcile = result
            return result

        found: dict[str, Any] | None = None
        source: str | None = None
        errors: list[str] = []

        try:
            payload = client.active_orders(
                wallet_address,
                market_id=int(row["market_id"]),
                limit=100,
            )
            found = v9._find_order(payload, order_id)
            if found is not None:
                source = "ACTIVE_ORDERS"
        except Exception as exc:
            errors.append(f"active_orders: {str(exc)[:180]}")

        if found is None:
            try:
                payload = client.order_history(wallet_address, limit=100)
                found = v9._find_order(payload, order_id)
                if found is not None:
                    source = "ORDER_HISTORY"
            except Exception as exc:
                errors.append(f"order_history: {str(exc)[:180]}")

        status = v9._order_status_from_node(found or {})
        filled_qty = _filled_quantity(found or {})
        if status in _FILLED_STATUSES:
            classification = "FILLED"
        elif status in _NO_FILL_STATUSES:
            classification = "NO_FILL"
        elif status in _ACTIVE_STATUSES:
            classification = "ACTIVE"
        elif found is not None and filled_qty is not None and filled_qty > 0:
            # MARKET/FOK cannot intentionally leave a partial resting remainder.
            # A positive executed quantity in terminal history is therefore strong
            # evidence of execution even if this product uses an unfamiliar label.
            classification = "FILLED_INFERRED"
        elif found is not None:
            classification = "FOUND_UNKNOWN"
        else:
            classification = "NOT_FOUND"

        result.update(
            classification=classification,
            status=status or None,
            source=source,
            errors=errors,
            order=found,
            filledQuantity=filled_qty,
        )
        self.last_exit_order_reconcile = result
        return result

    def _finalize_reconciled_exit(
        self,
        row: dict[str, Any],
        *,
        reason: str,
        order: dict[str, Any] | None = None,
    ) -> None:
        self._next_exit_order_reconcile_at.pop(int(row["id"]), None)
        proceeds = _actual_exit_proceeds(order)
        if proceeds is None:
            proceeds = base._finite(row.get("exit_proceeds_usdt")) or 0.0
        cost = base._finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
        self._event(
            "INFO",
            "EXIT_CONFIRMED",
            int(row["market_id"]),
            int(row["id"]),
            f"SELL reconciled as filled via {reason}; wallet can re-arm",
        )
        self._finish_round(row, proceeds - cost, proceeds, reason)

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        # Recovery path for V9/V8 rows that were already returned to OPEN after a
        # SELL submission. Reconcile the existing order before any new SELL.
        existing_order_id = str(row.get("exit_order_id") or "").strip()
        if existing_order_id:
            started = int(row.get("exit_sync_started_at_ms") or row.get("exit_placed_at_ms") or base._now_ms())
            refreshed = self._update_round(
                int(row["id"]),
                state="EXIT_SYNC",
                exit_sync_started_at_ms=started,
            )
            self.status = "EXIT_RECONCILING_EXISTING_ORDER"
            self._sync_exit(refreshed or row)
            return

        super()._exit_round(row, signal_ms)
        refreshed = self._round_state(int(row["id"]))
        if refreshed and str(refreshed.get("state") or "") == "EXIT_SYNC":
            # A fresh placement supersedes earlier retry errors.
            self._update_round(
                int(row["id"]),
                error_kind=None,
                error_message=None,
            )

    def _sync_exit(self, row: dict[str, Any]) -> None:
        try:
            position = self._position_state(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"exit position sync: {str(exc)[:300]}"
            position = {
                "availableShares": None,
                "totalShares": None,
                "totalSharesExplicit": False,
            }

        total = base._finite(position.get("totalShares"))
        available = base._finite(position.get("availableShares"))
        explicit_total = bool(position.get("totalSharesExplicit"))

        # Only an explicit total-position field can prove flatness by itself.
        # availableShares may be zero merely because a SELL order reserved them.
        if explicit_total and total is not None and total <= POSITION_EPSILON:
            self._finalize_reconciled_exit(row, reason="POSITION_TOTAL_FLAT")
            return

        reconciliation = self._reconcile_exit_order(row)
        classification = str(reconciliation.get("classification") or "UNKNOWN")
        order_status = reconciliation.get("status")
        elapsed_ms = base._now_ms() - int(
            row.get("exit_sync_started_at_ms")
            or row.get("exit_placed_at_ms")
            or base._now_ms()
        )

        if classification in {"FILLED", "FILLED_INFERRED"}:
            # For a MARKET/FOK SELL, a terminal FILLED status is authoritative:
            # there is no unfilled remainder to leave resting. Do not wait for a
            # lagging position endpoint and, critically, do not submit another SELL.
            self._finalize_reconciled_exit(
                row,
                reason="EXIT_ORDER_FILLED",
                order=reconciliation.get("order"),
            )
            return

        if classification == "NO_FILL":
            self._next_exit_order_reconcile_at.pop(int(row["id"]), None)
            message = (
                f"SELL order {row.get('exit_order_id') or 'unknown'} ended without a fill"
                + (f"; status={order_status}" if order_status else "")
            )
            self._update_round(
                int(row["id"]),
                state="OPEN",
                shares=total if total is not None else available,
                exit_order_id=None,
                exit_sync_started_at_ms=None,
                error_kind="EXIT_ORDER_NOT_FILLED",
                error_message=message,
            )
            self._event(
                "WARN",
                "EXIT_ORDER_NOT_FILLED",
                int(row["market_id"]),
                int(row["id"]),
                message + "; a fresh SELL may be attempted",
            )
            self.status = "MANAGING_POSITION"
            return

        if classification == "ACTIVE":
            self.status = "EXIT_ORDER_ACTIVE"
        else:
            self.status = "EXIT_RECONCILING_ORDER"

        # A zero *available* balance while total is still positive is consistent
        # with shares reserved by the existing SELL. This is a wait condition, not
        # proof of flatness and never a reason to send another SELL.
        if (
            available is not None
            and available <= POSITION_EPSILON
            and total is not None
            and total > POSITION_EPSILON
        ):
            self.status = "EXIT_SHARES_RESERVED_WAITING_ORDER"

        if elapsed_ms <= EXIT_ORDER_UNKNOWN_GRACE_MS:
            return

        reason = (
            f"SELL order {row.get('exit_order_id') or 'unknown'} outcome remained unresolved "
            f"for {elapsed_ms}ms; classification={classification}; status={order_status}; "
            f"totalShares={total}; availableShares={available}"
        )
        self._halt_market(int(row["market_id"]), reason, int(row["id"]))
        self._update_round(
            int(row["id"]),
            state="AMBIGUOUS",
            error_kind="EXIT_POSITION_AND_ORDER_UNCONFIRMED",
            error_message=reason,
        )

    def _settle_hold(self, row: dict[str, Any]) -> None:
        exit_order_id = str(row.get("exit_order_id") or "").strip()
        if not exit_order_id:
            return super()._settle_hold(row)

        # Never fabricate hold-to-settlement PnL while a SELL may have executed.
        # First reconcile the order exactly as the live exit path does.
        try:
            position = self._position_state(str(row["token_id"]))
        except Exception:
            position = {
                "totalShares": None,
                "availableShares": None,
                "totalSharesExplicit": False,
            }
        total = base._finite(position.get("totalShares"))
        if bool(position.get("totalSharesExplicit")) and total is not None and total <= POSITION_EPSILON:
            self._finalize_reconciled_exit(row, reason="POSITION_TOTAL_FLAT_AT_SETTLEMENT")
            return

        reconciliation = self._reconcile_exit_order(row)
        classification = str(reconciliation.get("classification") or "UNKNOWN")
        if classification in {"FILLED", "FILLED_INFERRED"}:
            self._finalize_reconciled_exit(
                row,
                reason="EXIT_ORDER_FILLED_AT_SETTLEMENT",
                order=reconciliation.get("order"),
            )
            return
        if classification == "NO_FILL":
            # The SELL definitely did not execute, so hold-to-settlement accounting
            # is now logically consistent. Clear the stale order id before using it.
            refreshed = self._update_round(
                int(row["id"]),
                exit_order_id=None,
                exit_sync_started_at_ms=None,
            )
            return super()._settle_hold(refreshed or row)

        self.status = "SETTLEMENT_WAITING_EXIT_RECONCILIATION"
        self.last_error = (
            f"official settlement deferred because SELL order {exit_order_id} is "
            f"still {classification}; refusing to assume the shares were held"
        )
        self._event(
            "WARN",
            "SETTLEMENT_DEFERRED_EXIT_UNRESOLVED",
            int(row["market_id"]),
            int(row["id"]),
            self.last_error,
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V10"
        with self.db_lock:
            anomaly = self.db.execute(
                """SELECT COUNT(*) AS n
                     FROM poly_gap_live_rounds
                    WHERE state='SETTLED'
                      AND exit_order_id IS NOT NULL
                      AND TRIM(exit_order_id)<>''"""
            ).fetchone()
        payload["exitReconciliation"] = {
            "usesPositionTotal": True,
            "usesAvailableSharesAsFlatProof": False,
            "usesActiveOrders": True,
            "usesOrderHistory": True,
            "orderReconcileIntervalMs": EXIT_ORDER_RECONCILE_INTERVAL_MS,
            "unknownOutcomeGraceMs": EXIT_ORDER_UNKNOWN_GRACE_MS,
            "filledOrderClosesImmediately": True,
            "definiteFokNoFillRetries": True,
            "unresolvedOrderBlocksDuplicateSell": True,
            "unresolvedOrderBlocksOfficialSettlement": True,
            "last": self.last_exit_order_reconcile,
            "legacySettledRowsWithExitOrder": int(anomaly["n"] if anomaly else 0),
        }
        return payload


base.PolyGapLiveEngine = ExitOrderReconciledPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
