from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v10 as v10
from . import poly_gap_live as poly_base

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_MULTI_STRATEGY_V8_DELAYED_RECONCILIATION"
HOST = v10.HOST
PORT = v10.PORT
RECONCILE_INTERVAL_SECONDS = 0.50


class EchtgeldEngine(v10.EchtgeldEngine):
    """V10 plus read-only delayed reconciliation for ambiguous Poly entries.

    The initial execution path remains fail-safe: once Binance returns an order id,
    an AMBIGUOUS result is never submitted again.  This layer only polls the
    already-known token position.  When Binance later exposes a positive position,
    the durable order is promoted to SUBMITTED so the Poly round becomes OPEN and
    the normal reversal-exit lifecycle can continue.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._delayed_reconcile_stop = threading.Event()
        self._delayed_reconcile_lock = threading.RLock()
        self._delayed_reconcile_thread = threading.Thread(
            target=self._delayed_reconcile_loop,
            name="echtgeld-poly-delayed-reconcile",
            daemon=True,
        )
        self.delayed_reconcile_checks = 0
        self.delayed_reconcile_confirmed = 0
        self.delayed_reconcile_last_at_ms: int | None = None
        self.delayed_reconcile_last_error: str | None = None
        self.delayed_reconcile_last_result: dict[str, Any] | None = None
        self._delayed_reconcile_thread.start()

    def _ambiguous_poly_entries(self) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT r.entry_intent_id,r.round_id,r.asset,r.market_id,r.side,r.token_id,
                          r.exit_status,o.vendor_order_id,o.target_notional_usdt,o.result_json
                     FROM engine_poly_rounds r
                     JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE o.venue='binance'
                      AND o.status='AMBIGUOUS'
                      AND o.vendor_order_id IS NOT NULL
                      AND COALESCE(r.exit_status,'') NOT IN ('FLAT','CLOSED')
                    ORDER BY r.created_at_ms ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _positive_position_shares(position: dict[str, Any]) -> float | None:
        shares = poly_base._first_number(
            position,
            ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"),
        )
        if shares is None or shares <= 1e-9:
            return None
        return float(shares)

    def _promote_ambiguous_entry(self, row: dict[str, Any], shares: float) -> None:
        intent_id = str(row.get("entry_intent_id") or "")
        now_ms = v1._now_ms()
        with self.db_lock:
            current = self.db.execute(
                "SELECT status,result_json,target_notional_usdt FROM engine_orders WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if current is None or str(current["status"] or "").upper() != "AMBIGUOUS":
                return
            try:
                result = json.loads(str(current["result_json"] or "{}"))
            except Exception:
                result = {}
            result["status"] = "SUBMITTED"
            result["delayedReconciliation"] = True
            result["confirmedShares"] = float(shares)
            result["reconciledAtMs"] = now_ms
            submitted_usdt = float(current["target_notional_usdt"] or 0.0)
            self.db.execute(
                """UPDATE engine_orders
                      SET status='SUBMITTED',shares=?,submitted_usdt=?,result_json=?,error_message=NULL,
                          completed_at_ms=COALESCE(completed_at_ms,?)
                    WHERE intent_id=? AND status='AMBIGUOUS'""",
                (
                    float(shares),
                    submitted_usdt if submitted_usdt > 0 else None,
                    json.dumps(result, separators=(",", ":"), ensure_ascii=False),
                    now_ms,
                    intent_id,
                ),
            )
            self.db.execute(
                "UPDATE engine_intents SET status='SUBMITTED' WHERE intent_id=? AND status='AMBIGUOUS'",
                (intent_id,),
            )
            self.db.commit()
        try:
            self._event(
                level="INFO",
                event_type="POLY_AMBIGUOUS_RECONCILED_OPEN",
                phase="SUBMITTED",
                message="Existing Binance order was confirmed by delayed position reconciliation; no venue retry was performed",
                context={
                    "intentId": intent_id,
                    "roundId": row.get("round_id"),
                    "asset": row.get("asset"),
                    "marketId": row.get("market_id"),
                    "side": row.get("side"),
                    "vendorOrderId": row.get("vendor_order_id"),
                    "confirmedShares": float(shares),
                    "readOnlyReconciliation": True,
                },
            )
        except Exception:
            pass

    def _reconcile_one_ambiguous(self, row: dict[str, Any]) -> bool:
        token_id = str(row.get("token_id") or "").strip()
        if not token_id:
            return False
        client, wallet = self._poly_ensure_client()
        position = client.position_by_token(wallet["walletAddress"], token_id)
        if not isinstance(position, dict):
            return False
        shares = self._positive_position_shares(position)
        self.delayed_reconcile_checks += 1
        self.delayed_reconcile_last_at_ms = v1._now_ms()
        self.delayed_reconcile_last_result = {
            "intentId": row.get("entry_intent_id"),
            "roundId": row.get("round_id"),
            "asset": row.get("asset"),
            "marketId": row.get("market_id"),
            "side": row.get("side"),
            "vendorOrderId": row.get("vendor_order_id"),
            "positionStatus": position.get("positionStatus"),
            "shares": shares,
        }
        if shares is None:
            return False
        self._promote_ambiguous_entry(row, shares)
        self.delayed_reconcile_confirmed += 1
        self.delayed_reconcile_last_error = None
        return True

    def _delayed_reconcile_loop(self) -> None:
        # Reconciliation is intentionally read-only.  Never call quote/place here.
        while not self._delayed_reconcile_stop.wait(RECONCILE_INTERVAL_SECONDS):
            try:
                with self._delayed_reconcile_lock:
                    for row in self._ambiguous_poly_entries():
                        try:
                            self._reconcile_one_ambiguous(row)
                        except Exception as exc:
                            self.delayed_reconcile_last_at_ms = v1._now_ms()
                            self.delayed_reconcile_last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            except Exception as exc:
                self.delayed_reconcile_last_at_ms = v1._now_ms()
                self.delayed_reconcile_last_error = f"loop: {type(exc).__name__}: {str(exc)[:300]}"

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["polyDelayedReconciliation"] = {
            "enabled": True,
            "mode": "READ_ONLY_POSITION_POLL_NO_ORDER_RETRY",
            "intervalMs": int(RECONCILE_INTERVAL_SECONDS * 1000),
            "checks": int(self.delayed_reconcile_checks),
            "confirmedOpen": int(self.delayed_reconcile_confirmed),
            "lastAtMs": self.delayed_reconcile_last_at_ms,
            "lastResult": self.delayed_reconcile_last_result,
            "lastError": self.delayed_reconcile_last_error,
            "ambiguousPending": len(self._ambiguous_poly_entries()),
        }
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway["ambiguousEntryDelayedReconciliation"] = True
            gateway["ambiguousEntryNeverBlindlyRetried"] = True
            gateway["confirmedPositionPromotesRoundOpen"] = True
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyDelayedReconciliation"] = True
        payload["ambiguousEntryNeverBlindlyRetried"] = True
        return payload

    def close(self) -> None:
        self._delayed_reconcile_stop.set()
        if self._delayed_reconcile_thread.is_alive():
            self._delayed_reconcile_thread.join(timeout=2.0)
        super().close()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV11Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; ambiguous Poly entries use read-only delayed reconciliation; venue-owner=8781-only",
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
