from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v19 as v19
from .target_taker_live_execution_v4 import _binance_order_rows, _first_number, _first_text

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V17_GENERIC_AMBIGUOUS_RECONCILIATION"
HOST = v19.HOST
PORT = v19.PORT
AMBIGUOUS_ORDER_RECONCILE_INTERVAL_SECONDS = 1.0
AMBIGUOUS_ORDER_HISTORY_LIMIT = 100
AMBIGUOUS_ORDER_DEEP_SCAN_AGE_MS = 10 * 60 * 1000
AMBIGUOUS_ORDER_DEEP_SCAN_MAX_PAGES = 10
NO_FILL_TERMINAL_STATUSES = {"FAILED", "EXPIRED", "CANCELLED", "CANCELED", "REJECTED"}


class EchtgeldEngine(v19.EchtgeldEngine):
    """V19 plus read-only delayed order-history reconciliation for every Binance order.

    V11 already repairs AMBIGUOUS Poly entries by polling token positions, but normal
    Target Taker intents have no engine_poly_rounds row and were therefore excluded.
    Binance can legitimately return PENDING/SUBMITTED for several seconds after a
    successful FOK placement and expose the FILLED amounts only later.  Those orders
    stayed AMBIGUOUS forever, leaving shares/submitted_usdt NULL and excluding real
    fills from settled PnL and the engine stop-loss calculation.

    This layer never quotes, places, retries, cancels or sells.  It reads the existing
    Binance order history for already-known vendor_order_id values only.  Positive
    filled amounts promote the durable order to SUBMITTED; terminal no-fill evidence
    promotes it to REJECTED.  Anything still uncertain remains AMBIGUOUS.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        start_worker = bool(kwargs.get("start_worker", True))
        self._generic_ambiguous_stop = threading.Event()
        self._generic_ambiguous_thread: threading.Thread | None = None
        self.generic_ambiguous_checks = 0
        self.generic_ambiguous_history_reads = 0
        self.generic_ambiguous_filled = 0
        self.generic_ambiguous_no_fill = 0
        self.generic_ambiguous_pending = 0
        self.generic_ambiguous_last_at_ms: int | None = None
        self.generic_ambiguous_last_error: str | None = None
        self.generic_ambiguous_last_result: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)
        if start_worker:
            self._generic_ambiguous_thread = threading.Thread(
                target=self._generic_ambiguous_loop,
                name="echtgeld-generic-ambiguous-order-history",
                daemon=True,
            )
            self._generic_ambiguous_thread.start()

    def _ambiguous_binance_orders(self) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT id,intent_id,dedupe_key,strategy,cohort,market_id,venue,side,
                          signal_ask,target_notional_usdt,status,attempted_at_ms,completed_at_ms,
                          execution_price,shares,submitted_usdt,vendor_order_id,vendor_order_hash,
                          error_message,result_json,context_json
                     FROM engine_orders
                    WHERE venue='binance'
                      AND status='AMBIGUOUS'
                      AND vendor_order_id IS NOT NULL
                      AND TRIM(vendor_order_id)<>''
                    ORDER BY attempted_at_ms DESC,id DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _history_map(payload: Any) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("orderId") or "").strip(): row
            for row in _binance_order_rows(payload if isinstance(payload, dict) else {})
            if str(row.get("orderId") or "").strip()
        }

    @staticmethod
    def _json_object(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _promote_filled_from_history(
        self,
        local: dict[str, Any],
        remote: dict[str, Any],
        *,
        filled_usdt: float,
        filled_shares: float,
    ) -> bool:
        intent_id = str(local.get("intent_id") or "")
        order_id = str(local.get("vendor_order_id") or "")
        exchange_status = str(remote.get("status") or "").strip().upper()
        vendor_hash = _first_text(remote, "vendorOrderId", "vendor_order_id")
        actual_price = _first_number(remote, "price", "averagePrice", "avgPrice")
        if actual_price is None or actual_price <= 0:
            actual_price = v1._finite(local.get("execution_price"))
        fill_pct = _first_number(remote, "fillPercentage")
        now_ms = v1._now_ms()

        with self.db_lock:
            current = self.db.execute(
                "SELECT status,result_json FROM engine_orders WHERE id=?",
                (int(local["id"]),),
            ).fetchone()
            if current is None or str(current["status"] or "").upper() != "AMBIGUOUS":
                return False
            result = self._json_object(current["result_json"])
            result.pop("error", None)
            result.update(
                status="SUBMITTED",
                exchangeStatus=exchange_status or "FILLED_AMOUNT_REPORTED",
                executionPrice=actual_price,
                shares=float(filled_shares),
                submittedUsdt=float(filled_usdt),
                vendorOrderId=order_id,
                delayedOrderHistoryReconciliation=True,
                reconciledAtMs=now_ms,
                readOnlyReconciliation=True,
            )
            if vendor_hash:
                result["vendorOrderHash"] = vendor_hash
            if fill_pct is not None:
                result["fillPercentage"] = float(fill_pct)
            cur = self.db.execute(
                """UPDATE engine_orders
                      SET status='SUBMITTED',execution_price=COALESCE(?,execution_price),
                          shares=?,submitted_usdt=?,vendor_order_hash=COALESCE(?,vendor_order_hash),
                          error_message=NULL,result_json=?,completed_at_ms=COALESCE(completed_at_ms,?)
                    WHERE id=? AND status='AMBIGUOUS'""",
                (
                    actual_price,
                    float(filled_shares),
                    float(filled_usdt),
                    vendor_hash,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now_ms,
                    int(local["id"]),
                ),
            )
            if cur.rowcount != 1:
                self.db.rollback()
                return False
            self.db.execute(
                """UPDATE engine_intents
                      SET status='SUBMITTED',error_message=NULL
                    WHERE intent_id=? AND status='AMBIGUOUS'""",
                (intent_id,),
            )
            self.db.commit()

        context = {
            "intentId": intent_id,
            "marketId": local.get("market_id"),
            "strategy": local.get("strategy"),
            "cohort": local.get("cohort"),
            "side": local.get("side"),
            "vendorOrderId": order_id,
            "vendorOrderHash": vendor_hash,
            "exchangeStatus": exchange_status,
            "filledUsdtAmount": float(filled_usdt),
            "filledShareQty": float(filled_shares),
            "fillPercentage": fill_pct,
            "executionPrice": actual_price,
            "readOnlyReconciliation": True,
            "automaticOrderRetry": False,
        }
        self._record_event(
            "INFO",
            "AMBIGUOUS_ORDER_RECONCILED_FILLED",
            "SUBMITTED",
            "Existing Binance order was later confirmed filled by read-only order-history reconciliation; no venue retry was performed",
            context=context,
        )
        self.generic_ambiguous_filled += 1
        self.generic_ambiguous_last_result = {"kind": "FILLED", **context}
        current_last = self.last_result if isinstance(self.last_result, dict) else {}
        if str(current_last.get("vendorOrderId") or "") == order_id:
            self.last_result = result
        return True

    def _promote_no_fill_from_history(
        self,
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> bool:
        intent_id = str(local.get("intent_id") or "")
        order_id = str(local.get("vendor_order_id") or "")
        exchange_status = str(remote.get("status") or "").strip().upper()
        vendor_hash = _first_text(remote, "vendorOrderId", "vendor_order_id")
        now_ms = v1._now_ms()
        error = f"Delayed Binance order-history reconciliation confirmed terminal no-fill status {exchange_status}"

        with self.db_lock:
            current = self.db.execute(
                "SELECT status,result_json FROM engine_orders WHERE id=?",
                (int(local["id"]),),
            ).fetchone()
            if current is None or str(current["status"] or "").upper() != "AMBIGUOUS":
                return False
            result = self._json_object(current["result_json"])
            result.update(
                status="REJECTED",
                exchangeStatus=exchange_status,
                vendorOrderId=order_id,
                delayedOrderHistoryReconciliation=True,
                reconciledAtMs=now_ms,
                readOnlyReconciliation=True,
                error=error,
            )
            if vendor_hash:
                result["vendorOrderHash"] = vendor_hash
            cur = self.db.execute(
                """UPDATE engine_orders
                      SET status='REJECTED',vendor_order_hash=COALESCE(?,vendor_order_hash),
                          shares=NULL,submitted_usdt=NULL,error_message=?,result_json=?,
                          completed_at_ms=COALESCE(completed_at_ms,?)
                    WHERE id=? AND status='AMBIGUOUS'""",
                (
                    vendor_hash,
                    error,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now_ms,
                    int(local["id"]),
                ),
            )
            if cur.rowcount != 1:
                self.db.rollback()
                return False
            self.db.execute(
                """UPDATE engine_intents SET status='REJECTED',error_message=?
                    WHERE intent_id=? AND status='AMBIGUOUS'""",
                (error, intent_id),
            )
            self.db.commit()

        context = {
            "intentId": intent_id,
            "marketId": local.get("market_id"),
            "strategy": local.get("strategy"),
            "cohort": local.get("cohort"),
            "side": local.get("side"),
            "vendorOrderId": order_id,
            "vendorOrderHash": vendor_hash,
            "exchangeStatus": exchange_status,
            "readOnlyReconciliation": True,
            "automaticOrderRetry": False,
        }
        self._record_event(
            "WARN",
            "AMBIGUOUS_ORDER_RECONCILED_NO_FILL",
            "REJECTED",
            error,
            context=context,
        )
        self.generic_ambiguous_no_fill += 1
        self.generic_ambiguous_last_result = {"kind": "NO_FILL", **context}
        current_last = self.last_result if isinstance(self.last_result, dict) else {}
        if str(current_last.get("vendorOrderId") or "") == order_id:
            self.last_result = result
        return True

    def _refresh_pending_history_projection(
        self,
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> None:
        exchange_status = str(remote.get("status") or "").strip().upper()
        vendor_hash = _first_text(remote, "vendorOrderId", "vendor_order_id")
        now_ms = v1._now_ms()
        with self.db_lock:
            current = self.db.execute(
                "SELECT status,result_json FROM engine_orders WHERE id=?",
                (int(local["id"]),),
            ).fetchone()
            if current is None or str(current["status"] or "").upper() != "AMBIGUOUS":
                return
            result = self._json_object(current["result_json"])
            result["exchangeStatus"] = exchange_status or result.get("exchangeStatus") or "UNKNOWN"
            result["lastOrderHistoryCheckAtMs"] = now_ms
            result["delayedOrderHistoryReconciliation"] = True
            if vendor_hash:
                result["vendorOrderHash"] = vendor_hash
            self.db.execute(
                """UPDATE engine_orders
                      SET vendor_order_hash=COALESCE(?,vendor_order_hash),result_json=?
                    WHERE id=? AND status='AMBIGUOUS'""",
                (
                    vendor_hash,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    int(local["id"]),
                ),
            )
            self.db.commit()

    def _reconcile_ambiguous_order_history_once(self) -> int:
        pending = self._ambiguous_binance_orders()
        self.generic_ambiguous_last_at_ms = v1._now_ms()
        if not pending:
            self.generic_ambiguous_last_error = None
            return 0

        client, wallet = self._poly_ensure_client()
        wallet_address = wallet["walletAddress"]
        history = client.order_history(wallet_address, limit=AMBIGUOUS_ORDER_HISTORY_LIMIT)
        self.generic_ambiguous_history_reads += 1
        history_map = self._history_map(history)

        # Current orders should always be in the newest page.  Only old unresolved
        # AMBIGUOUS rows are allowed to trigger bounded pagination so a historical
        # audit repair cannot burn API weight every second for a fresh PENDING FOK.
        now_ms = v1._now_ms()
        old_missing = {
            str(row.get("vendor_order_id") or "").strip()
            for row in pending
            if now_ms - int(row.get("attempted_at_ms") or now_ms) >= AMBIGUOUS_ORDER_DEEP_SCAN_AGE_MS
            and str(row.get("vendor_order_id") or "").strip() not in history_map
        }
        if old_missing and hasattr(client, "signed_get"):
            for page in range(1, AMBIGUOUS_ORDER_DEEP_SCAN_MAX_PAGES):
                payload = client.signed_get(
                    "/sapi/v1/w3w/wallet/prediction/order/history",
                    {
                        "walletAddress": wallet_address,
                        "offset": page * AMBIGUOUS_ORDER_HISTORY_LIMIT,
                        "limit": AMBIGUOUS_ORDER_HISTORY_LIMIT,
                    },
                )
                self.generic_ambiguous_history_reads += 1
                page_map = self._history_map(payload)
                history_map.update(page_map)
                old_missing -= set(page_map)
                if not old_missing or len(page_map) < AMBIGUOUS_ORDER_HISTORY_LIMIT:
                    break

        changed = 0
        still_pending = 0

        for local in pending:
            self.generic_ambiguous_checks += 1
            order_id = str(local.get("vendor_order_id") or "").strip()
            remote = history_map.get(order_id)
            if remote is None:
                still_pending += 1
                continue
            exchange_status = str(remote.get("status") or "").strip().upper()
            filled_usdt = _first_number(remote, "filledUsdtAmount")
            filled_shares = _first_number(remote, "filledShareQty")
            if (
                filled_usdt is not None
                and filled_usdt > 0
                and filled_shares is not None
                and filled_shares > 0
            ):
                if self._promote_filled_from_history(
                    local,
                    remote,
                    filled_usdt=float(filled_usdt),
                    filled_shares=float(filled_shares),
                ):
                    changed += 1
                continue
            if exchange_status in NO_FILL_TERMINAL_STATUSES:
                if self._promote_no_fill_from_history(local, remote):
                    changed += 1
                continue
            still_pending += 1
            self._refresh_pending_history_projection(local, remote)

        self.generic_ambiguous_pending = still_pending
        self.generic_ambiguous_last_error = None
        if changed:
            # Re-evaluate the settled PnL guard immediately after durable fill
            # recovery.  This can auto-pause an armed engine before another intent
            # is admitted if the newly recovered losses cross the configured limit.
            try:
                self._enforce_stop_loss(force_sync=True)
            except Exception as exc:
                self.generic_ambiguous_last_error = (
                    f"risk recheck after reconciliation: {type(exc).__name__}: {str(exc)[:300]}"
                )
        return changed

    def _generic_ambiguous_loop(self) -> None:
        # Read-only venue reconciliation.  This loop must never call quote/place.
        while not self._generic_ambiguous_stop.wait(AMBIGUOUS_ORDER_RECONCILE_INTERVAL_SECONDS):
            try:
                self._reconcile_ambiguous_order_history_once()
            except Exception as exc:
                self.generic_ambiguous_last_at_ms = v1._now_ms()
                self.generic_ambiguous_last_error = f"{type(exc).__name__}: {str(exc)[:400]}"

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        pending = len(self._ambiguous_binance_orders())
        self.generic_ambiguous_pending = pending
        payload["genericAmbiguousOrderHistoryReconciliation"] = {
            "enabled": True,
            "mode": "READ_ONLY_ORDER_HISTORY_NO_ORDER_RETRY",
            "intervalMs": int(AMBIGUOUS_ORDER_RECONCILE_INTERVAL_SECONDS * 1000),
            "historyLimit": AMBIGUOUS_ORDER_HISTORY_LIMIT,
            "checks": int(self.generic_ambiguous_checks),
            "historyReads": int(self.generic_ambiguous_history_reads),
            "confirmedFilled": int(self.generic_ambiguous_filled),
            "confirmedNoFill": int(self.generic_ambiguous_no_fill),
            "ambiguousPending": pending,
            "lastAtMs": self.generic_ambiguous_last_at_ms,
            "lastResult": self.generic_ambiguous_last_result,
            "lastError": self.generic_ambiguous_last_error,
            "riskRecheckAfterRecovery": True,
            "automaticOrderRetry": False,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["genericAmbiguousOrderHistoryReconciliation"] = True
        payload["ambiguousOrderHistoryNeverRetriesVenue"] = True
        payload["ambiguousRecoveryFeedsPnlStopLoss"] = True
        payload["genericAmbiguousReconcileWorkerAlive"] = bool(
            self._generic_ambiguous_thread and self._generic_ambiguous_thread.is_alive()
        ) if self._generic_ambiguous_thread is not None else True
        return payload

    def close(self) -> None:
        self._generic_ambiguous_stop.set()
        if self._generic_ambiguous_thread is not None and self._generic_ambiguous_thread.is_alive():
            self._generic_ambiguous_thread.join(timeout=2.0)
        super().close()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV20Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "all Binance AMBIGUOUS orders use read-only order-history reconciliation; "
        "confirmed fills feed durable PnL + stop-loss; no venue retry",
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
