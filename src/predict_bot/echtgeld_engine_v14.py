from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v13 as v13

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V11_RECOVERED_AMBIGUOUS_STATE"
HOST = v13.HOST
PORT = v13.PORT


class EchtgeldEngine(v13.EchtgeldEngine):
    """V13 plus derived current-state cleanup for recovered AMBIGUOUS entries.

    The first bounded post-place reconciliation may legitimately classify a Binance
    order as AMBIGUOUS when the venue has an order id/status but filled amount is
    not visible yet.  V12 later reconciles order/history and promotes the durable
    order to SUBMITTED with shares/submitted_usdt and FILLED evidence.

    Historical engine_events must remain immutable for auditability.  However,
    state.latestError and state.lastResult are *current-state* projections, so an
    ORDER_AMBIGUOUS_NO_RETRY event whose exact intent has since been positively
    reconciled must no longer be presented as an unresolved current error.
    """

    @staticmethod
    def _recovered_order(order: dict[str, Any]) -> bool:
        status = str(order.get("status") or "").upper()
        if status != "SUBMITTED":
            return False
        try:
            shares = float(order.get("shares") or 0.0)
        except (TypeError, ValueError):
            shares = 0.0
        result = order.get("result") if isinstance(order.get("result"), dict) else {}
        history_status = str(result.get("orderHistoryStatus") or "").upper()
        history_reconciled = bool(result.get("orderHistoryReconciliation"))
        delayed_reconciled = bool(result.get("delayedReconciliation"))
        try:
            confirmed_shares = float(result.get("filledShareQty") or result.get("confirmedShares") or shares or 0.0)
        except (TypeError, ValueError):
            confirmed_shares = shares
        return bool(
            shares > 0
            and confirmed_shares > 0
            and (history_status == "FILLED" or history_reconciled or delayed_reconciled)
        )

    @staticmethod
    def _intent_id(item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return ""
        return str(item.get("intent_id") or item.get("intentId") or "").strip()

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION

        orders = [item for item in payload.get("recentOrders", []) if isinstance(item, dict)]
        recovered_by_intent = {
            self._intent_id(order): order
            for order in orders
            if self._intent_id(order) and self._recovered_order(order)
        }

        latest_error = payload.get("latestError") if isinstance(payload.get("latestError"), dict) else None
        latest_error_intent = self._intent_id(latest_error)
        latest_error_type = str(latest_error.get("event_type") or "") if latest_error else ""
        recovered_error = (
            latest_error_type == "ORDER_AMBIGUOUS_NO_RETRY"
            and latest_error_intent in recovered_by_intent
        )

        if recovered_error and latest_error is not None:
            payload["latestHistoricalError"] = latest_error
            payload["latestError"] = None
            order = recovered_by_intent[latest_error_intent]
            payload["latestRecoveredAmbiguous"] = {
                "intentId": latest_error_intent,
                "marketId": order.get("market_id") or order.get("marketId"),
                "side": order.get("side"),
                "vendorOrderId": order.get("vendor_order_id") or order.get("vendorOrderId"),
                "status": "RECOVERED_FILLED",
                "shares": order.get("shares"),
                "submittedUsdt": order.get("submitted_usdt") or order.get("submittedUsdt"),
                "result": order.get("result"),
                "message": "Initial AMBIGUOUS entry was later confirmed FILLED by Binance order-history reconciliation; historical error event retained for audit only",
            }

        last_result = payload.get("lastResult") if isinstance(payload.get("lastResult"), dict) else None
        last_intent = payload.get("lastIntent") if isinstance(payload.get("lastIntent"), dict) else None
        last_intent_id = self._intent_id(last_intent)
        if last_result and str(last_result.get("status") or "").upper() == "AMBIGUOUS" and last_intent_id in recovered_by_intent:
            order = recovered_by_intent[last_intent_id]
            result = order.get("result") if isinstance(order.get("result"), dict) else {}
            payload["lastResultHistorical"] = last_result
            payload["lastResult"] = {
                **last_result,
                **result,
                "status": "SUBMITTED",
                "recoveredFromAmbiguous": True,
                "recoveryStatus": "FILLED",
                "error": None,
                "shares": order.get("shares"),
                "submittedUsdt": order.get("submitted_usdt") or order.get("submittedUsdt"),
            }

        payload["ambiguousRecoveryProjectionV14"] = {
            "enabled": True,
            "historicalEventsImmutable": True,
            "latestRecoveredErrorSuppressed": bool(recovered_error),
            "recoveredRecentOrderCount": len(recovered_by_intent),
            "currentStatePrefersOrderHistoryFillEvidence": True,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["recoveredAmbiguousCurrentStateProjection"] = True
        payload["historicalAmbiguousEventsPreserved"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV14Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "recovered AMBIGUOUS entries no longer surface as unresolved current errors; audit events preserved",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
