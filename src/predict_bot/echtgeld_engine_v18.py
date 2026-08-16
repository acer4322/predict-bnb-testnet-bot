from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v17 as v17

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V15_REJECTED_ROUND_FENCE_RELEASE"
HOST = v17.HOST
PORT = v17.PORT


class EchtgeldEngine(v17.EchtgeldEngine):
    """V17 plus alignment between Poly lifecycle and new-entry admission fencing.

    V6's /poly-lifecycle projection already treats an entry order with
    order_status=REJECTED as terminal/non-active.  Its submit_poly_fast_intent()
    preflight, however, used a separate SQL query that did not exclude REJECTED
    orders.  After a safe pre-venue rejection (for example an Ask moving above
    the execution cap), 8792 could therefore see FLAT_OR_UNKNOWN while every new
    intent was rejected with ACTIVE_ROUND_EXISTS forever.

    This layer only terminalizes that already-non-active REJECTED durable Poly
    round before delegating to the unchanged V17/V6 submission path.  Potential
    venue-write states (ATTEMPTING/SUBMITTED/AMBIGUOUS) are never released here.
    """

    def _release_rejected_poly_fence(self, *, asset: str, market_id: int, round_id: str) -> int:
        if asset not in {"BTC", "ETH", "BNB"} or market_id <= 0 or not round_id:
            return 0
        now_ms = v1._now_ms()
        with self.db_lock:
            rows = self.db.execute(
                """SELECT r.round_id,r.entry_intent_id,r.exit_status,
                          i.status AS intent_status,o.status AS order_status,
                          o.vendor_order_id,o.submitted_usdt,o.shares
                     FROM engine_poly_rounds r
                     LEFT JOIN engine_intents i ON i.intent_id=r.entry_intent_id
                     LEFT JOIN engine_orders o ON o.intent_id=r.entry_intent_id
                    WHERE r.asset=? AND r.market_id=?
                      AND r.round_id<>?
                      AND COALESCE(r.exit_status,'') NOT IN ('FLAT','CLOSED')
                    ORDER BY r.created_at_ms DESC LIMIT 25""",
                (asset, market_id, round_id),
            ).fetchall()

            released = 0
            for raw in rows:
                row = dict(raw)
                order_status = str(row.get("order_status") or "").upper()
                if order_status != "REJECTED":
                    continue

                cur = self.db.execute(
                    """UPDATE engine_poly_rounds
                          SET exit_status='CLOSED',
                              exit_completed_at_ms=COALESCE(exit_completed_at_ms,?),
                              exit_error=COALESCE(exit_error,'entry order rejected before an active position existed; admission fence released')
                        WHERE round_id=?
                          AND COALESCE(exit_status,'') NOT IN ('FLAT','CLOSED')""",
                    (now_ms, str(row.get("round_id") or "")),
                )
                released += int(cur.rowcount or 0)
            if released:
                self.db.commit()
        return released

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        asset = str(raw.get("asset") or "").strip().upper()
        market_id = int(v1._finite(raw.get("marketId")) or 0)
        round_id = str(raw.get("roundId") or "").strip()
        released = self._release_rejected_poly_fence(
            asset=asset,
            market_id=market_id,
            round_id=round_id,
        )
        result = super().submit_poly_fast_intent(raw)
        if released and isinstance(result, dict):
            result["rejectedRoundFencesReleased"] = released
        return result

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["rejectedRoundFenceReleaseV18"] = {
            "enabled": True,
            "scope": "ORDER_STATUS_REJECTED_ONLY",
            "lifecycleParity": True,
            "submittedReleased": False,
            "ambiguousReleased": False,
            "attemptingReleased": False,
            "venueRetryChanged": False,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["rejectedPolyRoundAdmissionFenceRelease"] = True
        payload["rejectedPolyRoundFenceScope"] = "ORDER_STATUS_REJECTED_ONLY"
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV18Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "REJECTED Poly entry rounds no longer remain in ACTIVE_ROUND_EXISTS admission fence",
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
