from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v12 as v12
from . import echtgeld_engine_v16 as v16

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V14_EMPTY_POSITION_EXIT_RECONCILIATION"
HOST = v16.HOST
PORT = v16.PORT
EMPTY_POSITION_CONFIRMATIONS_REQUIRED = 3


class EchtgeldEngine(v16.EchtgeldEngine):
    """V16 plus conservative reconciliation for a fully-filled SELL whose token position is absent.

    Binance position/token can return HTTP 200 with an empty object after a position
    is no longer present.  V12 treated that shape as unknown forever, leaving a
    fully-filled SELL in EXIT_AMBIGUOUS indefinitely.  This layer only treats an
    empty position object as terminal evidence when the *existing* SELL is already
    confirmed FILLED with fillPercentage >= 1 and the same round receives multiple
    consecutive empty position responses.  It never places, retries, quotes or
    duplicates an order.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._empty_position_confirmations: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    def _mark_exit_flat_from_empty_position(
        self,
        row: dict[str, Any],
        *,
        exchange_status: str,
        filled_usdt: float | None,
        filled_shares: float | None,
        fill_pct: float | None,
        confirmation_count: int,
    ) -> bool:
        round_id = str(row.get("round_id") or "").strip()
        if not round_id:
            return False
        now_ms = v1._now_ms()
        proceeds = (
            filled_usdt
            if filled_usdt is not None and filled_usdt >= 0
            else v12._num(row.get("exit_proceeds_usdt"))
        )
        with self.db_lock:
            cur = self.db.execute(
                """UPDATE engine_poly_rounds
                      SET exit_status='FLAT',exit_proceeds_usdt=COALESCE(?,exit_proceeds_usdt),
                          exit_completed_at_ms=?,exit_error=NULL
                    WHERE round_id=? AND exit_status='AMBIGUOUS'""",
                (proceeds, now_ms, round_id),
            )
            self.db.commit()
        if cur.rowcount != 1:
            return False

        self.history_sync_exit_flats += 1
        self.history_sync_last_result = {
            "kind": "EXIT_CONFIRMED_FLAT_EMPTY_POSITION",
            "roundId": round_id,
            "asset": row.get("asset"),
            "marketId": row.get("market_id"),
            "orderId": row.get("exit_order_id"),
            "exchangeStatus": exchange_status or None,
            "filledUsdtAmount": filled_usdt,
            "filledShareQty": filled_shares,
            "fillPercentage": fill_pct,
            "positionResponseShape": "EMPTY_OBJECT",
            "emptyPositionConfirmations": confirmation_count,
            "exitProceedsUsdt": proceeds,
            "readOnlyReconciliation": True,
        }
        try:
            self._event(
                level="INFO",
                event_type="POLY_AMBIGUOUS_EXIT_RECONCILED_FLAT_EMPTY_POSITION",
                phase="FLAT",
                message=(
                    "Existing Binance SELL was FILLED and the token position endpoint "
                    "returned an empty object on repeated read-only checks; round confirmed "
                    "flat without any duplicate SELL"
                ),
                context=dict(self.history_sync_last_result),
            )
        except Exception:
            pass
        self._empty_position_confirmations.pop(round_id, None)
        return True

    def _sync_exit_from_history(self, row: dict[str, Any], order: dict[str, Any]) -> bool:
        token_id = str(row.get("token_id") or "").strip()
        round_id = str(row.get("round_id") or "").strip()
        if not token_id or not round_id:
            return False

        exchange_status = str(order.get("status") or "").upper()
        filled_usdt = v12._num(order.get("filledUsdtAmount"))
        filled_shares = v12._num(order.get("filledShareQty"))
        fill_pct = v12._num(order.get("fillPercentage"))

        # Only the narrow, high-confidence case is handled here.  Everything else
        # falls through to V12's existing order-history + position reconciliation.
        fully_filled_sell = bool(
            exchange_status == "FILLED"
            and fill_pct is not None
            and fill_pct >= 1.0 - 1e-9
            and filled_shares is not None
            and filled_shares > 0
        )
        if fully_filled_sell:
            client, wallet = self._poly_ensure_client()
            position = client.position_by_token(wallet["walletAddress"], token_id)
            if isinstance(position, dict) and not position:
                count = int(self._empty_position_confirmations.get(round_id, 0)) + 1
                self._empty_position_confirmations[round_id] = count
                self.history_sync_last_result = {
                    "kind": "EXIT_EMPTY_POSITION_CONFIRMING",
                    "roundId": round_id,
                    "asset": row.get("asset"),
                    "marketId": row.get("market_id"),
                    "orderId": row.get("exit_order_id"),
                    "exchangeStatus": exchange_status,
                    "filledUsdtAmount": filled_usdt,
                    "filledShareQty": filled_shares,
                    "fillPercentage": fill_pct,
                    "positionResponseShape": "EMPTY_OBJECT",
                    "emptyPositionConfirmations": count,
                    "emptyPositionConfirmationsRequired": EMPTY_POSITION_CONFIRMATIONS_REQUIRED,
                }
                if count >= EMPTY_POSITION_CONFIRMATIONS_REQUIRED:
                    return self._mark_exit_flat_from_empty_position(
                        row,
                        exchange_status=exchange_status,
                        filled_usdt=filled_usdt,
                        filled_shares=filled_shares,
                        fill_pct=fill_pct,
                        confirmation_count=count,
                    )
                return False
            self._empty_position_confirmations.pop(round_id, None)

        return super()._sync_exit_from_history(row, order)

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["emptyPositionExitReconciliationV17"] = {
            "enabled": True,
            "readOnly": True,
            "venueWritesChanged": False,
            "requiresSellStatus": "FILLED",
            "requiresFillPercentage": 1.0,
            "emptyPositionConfirmationsRequired": EMPTY_POSITION_CONFIRMATIONS_REQUIRED,
            "pendingConfirmations": dict(self._empty_position_confirmations),
            "neverRetriesSell": True,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["emptyPositionExitReconciliation"] = True
        payload["emptyPositionConfirmationsRequired"] = EMPTY_POSITION_CONFIRMATIONS_REQUIRED
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV17Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "fully-filled SELL + repeated empty token position can reconcile FLAT read-only",
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
