from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v11 as v11
from . import poly_gap_live as poly_base

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V9_ORDER_HISTORY_RECONCILIATION"
HOST = v11.HOST
PORT = v11.PORT
ORDER_SYNC_INTERVAL_SECONDS = 0.50
DISABLED_POLY_ENTRY_ASSETS = {"BNB"}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


class EchtgeldEngine(v11.EchtgeldEngine):
    """V11 plus old-4310-style asynchronous Binance order-history sync.

    The old 4310 live engine did not require fills to become visible inside the
    initial post-place call. It continuously synchronized Binance order/history
    and persisted filledUsdtAmount / filledShareQty / fillPercentage later.

    This layer restores that model for Poly entries and exits while preserving
    the standalone-engine invariant: background reconciliation is READ ONLY and
    never quotes, places, retries, or duplicates an order.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._history_sync_stop = threading.Event()
        self._history_sync_lock = threading.RLock()
        self.history_sync_checks = 0
        self.history_sync_entry_updates = 0
        self.history_sync_exit_flats = 0
        self.history_sync_last_at_ms: int | None = None
        self.history_sync_last_error: str | None = None
        self.history_sync_last_result: dict[str, Any] | None = None
        self._history_sync_thread = threading.Thread(
            target=self._history_sync_loop,
            name="echtgeld-poly-4310-order-history-sync",
            daemon=True,
        )
        self._history_sync_thread.start()

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        asset = str(raw.get("asset") or "").strip().upper()
        if asset in DISABLED_POLY_ENTRY_ASSETS:
            return {
                "ok": True,
                "accepted": False,
                "queued": False,
                "status": "ASSET_DISABLED",
                "asset": asset,
                "message": "BNB new Poly entries are temporarily disabled in 8781; existing BNB exits remain allowed",
            }
        return super().submit_poly_fast_intent(raw)

    def _poly_orders_needing_history_sync(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self.db_lock:
            entries = self.db.execute(
                """SELECT r.entry_intent_id,r.round_id,r.asset,r.market_id,r.side,r.token_id,
                          o.status,o.vendor_order_id,o.target_notional_usdt,o.result_json
                     FROM engine_poly_rounds r
                     JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE o.venue='binance'
                      AND o.vendor_order_id IS NOT NULL
                      AND o.status IN ('AMBIGUOUS','SUBMITTED')
                      AND (o.shares IS NULL OR o.shares<=0 OR o.submitted_usdt IS NULL OR o.submitted_usdt<=0)
                    ORDER BY r.created_at_ms ASC"""
            ).fetchall()
            exits = self.db.execute(
                """SELECT r.entry_intent_id,r.round_id,r.asset,r.market_id,r.side,r.token_id,
                          r.exit_status,r.exit_order_id,r.exit_proceeds_usdt
                     FROM engine_poly_rounds r
                    WHERE r.exit_status='AMBIGUOUS'
                      AND r.exit_order_id IS NOT NULL
                    ORDER BY r.created_at_ms ASC"""
            ).fetchall()
        return [dict(row) for row in entries], [dict(row) for row in exits]

    @staticmethod
    def _history_order_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = payload.get("orders") if isinstance(payload, dict) else None
        result: dict[str, dict[str, Any]] = {}
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            order_id = str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or "").strip()
            if order_id:
                result[order_id] = raw
        return result

    def _sync_entry_from_history(self, row: dict[str, Any], order: dict[str, Any]) -> bool:
        intent_id = str(row.get("entry_intent_id") or "")
        filled_usdt = _num(order.get("filledUsdtAmount"))
        filled_shares = _num(order.get("filledShareQty"))
        fill_pct = _num(order.get("fillPercentage"))
        exchange_status = str(order.get("status") or "").upper()
        if (filled_usdt is None or filled_usdt <= 0) and (filled_shares is None or filled_shares <= 0):
            return False

        now_ms = v1._now_ms()
        with self.db_lock:
            current = self.db.execute(
                "SELECT status,result_json,target_notional_usdt FROM engine_orders WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if current is None:
                return False
            try:
                result = json.loads(str(current["result_json"] or "{}"))
            except Exception:
                result = {}
            result.update(
                status="SUBMITTED",
                orderHistoryReconciliation=True,
                orderHistoryStatus=exchange_status or None,
                filledUsdtAmount=filled_usdt,
                filledShareQty=filled_shares,
                fillPercentage=fill_pct,
                reconciledAtMs=now_ms,
            )
            submitted = filled_usdt if filled_usdt is not None and filled_usdt > 0 else _num(current["target_notional_usdt"])
            self.db.execute(
                """UPDATE engine_orders
                      SET status='SUBMITTED',shares=COALESCE(?,shares),submitted_usdt=COALESCE(?,submitted_usdt),
                          result_json=?,error_message=NULL
                    WHERE intent_id=?""",
                (
                    filled_shares if filled_shares is not None and filled_shares > 0 else None,
                    submitted if submitted is not None and submitted > 0 else None,
                    json.dumps(result, separators=(",", ":"), ensure_ascii=False),
                    intent_id,
                ),
            )
            self.db.execute(
                "UPDATE engine_intents SET status='SUBMITTED' WHERE intent_id=? AND status IN ('AMBIGUOUS','SUBMITTED')",
                (intent_id,),
            )
            self.db.commit()
        self.history_sync_entry_updates += 1
        return True

    def _position_is_flat(self, token_id: str) -> tuple[bool, dict[str, Any] | None]:
        client, wallet = self._poly_ensure_client()
        position = client.position_by_token(wallet["walletAddress"], token_id)
        if not isinstance(position, dict):
            return False, None
        shares = poly_base._first_number(
            position,
            ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"),
        )
        return bool(shares is not None and shares <= 1e-9), position

    def _sync_exit_from_history(self, row: dict[str, Any], order: dict[str, Any]) -> bool:
        token_id = str(row.get("token_id") or "").strip()
        round_id = str(row.get("round_id") or "").strip()
        if not token_id or not round_id:
            return False

        exchange_status = str(order.get("status") or "").upper()
        filled_usdt = _num(order.get("filledUsdtAmount"))
        filled_shares = _num(order.get("filledShareQty"))
        fill_pct = _num(order.get("fillPercentage"))
        flat, position = self._position_is_flat(token_id)
        if not flat:
            self.history_sync_last_result = {
                "kind": "EXIT_PENDING",
                "roundId": round_id,
                "asset": row.get("asset"),
                "marketId": row.get("market_id"),
                "orderId": row.get("exit_order_id"),
                "exchangeStatus": exchange_status or None,
                "filledUsdtAmount": filled_usdt,
                "filledShareQty": filled_shares,
                "fillPercentage": fill_pct,
                "positionStatus": position.get("positionStatus") if isinstance(position, dict) else None,
            }
            return False

        now_ms = v1._now_ms()
        proceeds = filled_usdt if filled_usdt is not None and filled_usdt >= 0 else _num(row.get("exit_proceeds_usdt"))
        with self.db_lock:
            self.db.execute(
                """UPDATE engine_poly_rounds
                      SET exit_status='FLAT',exit_proceeds_usdt=COALESCE(?,exit_proceeds_usdt),
                          exit_completed_at_ms=?,exit_error=NULL
                    WHERE round_id=? AND exit_status='AMBIGUOUS'""",
                (proceeds, now_ms, round_id),
            )
            self.db.commit()
        self.history_sync_exit_flats += 1
        try:
            self._event(
                level="INFO",
                event_type="POLY_AMBIGUOUS_EXIT_RECONCILED_FLAT",
                phase="FLAT",
                message="Existing Binance SELL was confirmed flat by 4310-style delayed order/position sync; no duplicate SELL was performed",
                context={
                    "roundId": round_id,
                    "asset": row.get("asset"),
                    "marketId": row.get("market_id"),
                    "vendorOrderId": row.get("exit_order_id"),
                    "exchangeStatus": exchange_status or None,
                    "filledUsdtAmount": filled_usdt,
                    "filledShareQty": filled_shares,
                    "fillPercentage": fill_pct,
                    "exitProceedsUsdt": proceeds,
                    "readOnlyReconciliation": True,
                },
            )
        except Exception:
            pass
        return True

    def _sync_history_once(self) -> None:
        entries, exits = self._poly_orders_needing_history_sync()
        if not entries and not exits:
            return
        client, wallet = self._poly_ensure_client()
        payload = client.order_history(wallet["walletAddress"], limit=100)
        orders = self._history_order_map(payload if isinstance(payload, dict) else {})
        self.history_sync_checks += 1
        self.history_sync_last_at_ms = v1._now_ms()
        touched: list[dict[str, Any]] = []

        for row in entries:
            order_id = str(row.get("vendor_order_id") or "")
            order = orders.get(order_id)
            if order is not None and self._sync_entry_from_history(row, order):
                touched.append({"kind": "ENTRY", "intentId": row.get("entry_intent_id"), "orderId": order_id})

        for row in exits:
            order_id = str(row.get("exit_order_id") or "")
            order = orders.get(order_id)
            if order is not None and self._sync_exit_from_history(row, order):
                touched.append({"kind": "EXIT", "roundId": row.get("round_id"), "orderId": order_id})

        if touched:
            self.history_sync_last_result = {
                "atMs": self.history_sync_last_at_ms,
                "updates": touched,
            }
        self.history_sync_last_error = None

    def _history_sync_loop(self) -> None:
        while not self._history_sync_stop.wait(ORDER_SYNC_INTERVAL_SECONDS):
            try:
                with self._history_sync_lock:
                    self._sync_history_once()
            except Exception as exc:
                self.history_sync_last_at_ms = v1._now_ms()
                self.history_sync_last_error = f"{type(exc).__name__}: {str(exc)[:400]}"

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["poly4310OrderSync"] = {
            "enabled": True,
            "mode": "READ_ONLY_ORDER_HISTORY_PLUS_POSITION_NO_ORDER_RETRY",
            "intervalMs": int(ORDER_SYNC_INTERVAL_SECONDS * 1000),
            "checks": int(self.history_sync_checks),
            "entryUpdates": int(self.history_sync_entry_updates),
            "exitConfirmedFlat": int(self.history_sync_exit_flats),
            "lastAtMs": self.history_sync_last_at_ms,
            "lastResult": self.history_sync_last_result,
            "lastError": self.history_sync_last_error,
            "sourceSemantics": "OLD_4310_LIVE_ORDER_SYNC",
        }
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway.update(
                enabledEntryAssets=["BTC", "ETH"],
                disabledEntryAssets=["BNB"],
                disabledAssetExitStillAllowed=True,
                orderHistoryDelayedReconciliation=True,
                ambiguousExitDelayedReconciliation=True,
                backgroundReconciliationNeverPlacesOrders=True,
            )
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["poly4310OrderSync"] = True
        payload["polyBnbEntryDisabled"] = True
        payload["polyBnbExistingExitAllowed"] = True
        payload["ambiguousExitDelayedReconciliation"] = True
        return payload

    def close(self) -> None:
        self._history_sync_stop.set()
        if self._history_sync_thread.is_alive():
            self._history_sync_thread.join(timeout=2.0)
        super().close()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV12Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "BNB new entries disabled; old-4310 order-history reconciliation restored; venue-owner=8781-only",
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
