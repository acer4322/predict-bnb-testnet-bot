from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v15 as v15

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_V13_NONDESTRUCTIVE_TOKEN_REPAIR"
HOST = v15.HOST
PORT = v15.PORT


class EchtgeldEngine(v15.EchtgeldEngine):
    """V15 plus non-destructive legacy Poly token repair.

    Older V9 migration code derived token_id only from engine_redeems and then
    unconditionally overwrote engine_poly_rounds.token_id.  Before a market became
    claimable there was no redeem row, so a valid token selected and persisted at
    entry time could be replaced with an empty string.  That made later EXIT
    reconciliation impossible and left the asset fail-closed in
    TOKEN_REPAIR_REQUIRED.

    This override preserves any existing durable token.  A redeem token may fill
    an empty token, but an absent/empty redeem token can never blank an existing
    one.  No BUY, SELL, redeem, retry, lifecycle or strategy rule is changed.
    """

    def _repair_legacy_poly_rows(self) -> int:
        repaired = 0
        with self.db_lock:
            self.db.execute(
                """DELETE FROM engine_poly_rounds
                     WHERE round_id LIKE 'legacy:%'
                       AND entry_intent_id NOT IN (
                           SELECT intent_id FROM engine_orders
                            WHERE venue='binance' AND status IN ('SUBMITTED','AMBIGUOUS')
                       )"""
            )

            rows = self.db.execute(
                """SELECT i.*,o.status AS order_status,o.result_json
                     FROM engine_intents i
                     JOIN engine_orders o ON o.intent_id=i.intent_id
                    WHERE i.intent_id LIKE 'poly-fast:%'
                      AND o.venue='binance'
                      AND o.status IN ('SUBMITTED','AMBIGUOUS')
                    ORDER BY i.created_at_ms ASC"""
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                try:
                    payload = json.loads(str(row.get("payload_json") or "{}"))
                except Exception:
                    payload = {}
                asset = self._asset_from_intent(row, payload)
                market_id = int(row.get("market_id") or 0)
                side = str(row.get("side") or "").upper()
                if asset not in {"BTC", "ETH", "BNB"} or market_id <= 0 or side not in {"UP", "DOWN"}:
                    continue

                redeem = self.db.execute(
                    """SELECT token_id,claimable_value_usdt,discovered_at_ms,completed_at_ms
                         FROM engine_redeems
                        WHERE venue_market_id=? AND side=?
                        ORDER BY discovered_at_ms DESC LIMIT 1""",
                    (market_id, side),
                ).fetchone()
                redeem_token = str(redeem["token_id"] or "").strip() if redeem is not None else ""
                intent_id = str(row.get("intent_id") or "")
                round_id = f"legacy:{intent_id}"
                existing = self.db.execute(
                    "SELECT entry_intent_id,token_id FROM engine_poly_rounds WHERE entry_intent_id=?",
                    (intent_id,),
                ).fetchone()

                if existing is None:
                    self.db.execute(
                        """INSERT INTO engine_poly_rounds(
                               entry_intent_id,round_id,asset,market_id,side,token_id,fee_rate_bps,created_at_ms
                           ) VALUES(?,?,?,?,?,?,200,?)""",
                        (
                            intent_id,
                            round_id,
                            asset,
                            market_id,
                            side,
                            redeem_token,
                            int(row.get("created_at_ms") or v1._now_ms()),
                        ),
                    )
                    repaired += 1
                else:
                    before_token = str(existing["token_id"] or "").strip()
                    self.db.execute(
                        """UPDATE engine_poly_rounds
                              SET asset=?,market_id=?,side=?,
                                  token_id=CASE WHEN ?<>'' THEN ? ELSE token_id END
                            WHERE entry_intent_id=?""",
                        (asset, market_id, side, redeem_token, redeem_token, intent_id),
                    )
                    if redeem_token and redeem_token != before_token:
                        repaired += 1
            self.db.commit()
        return repaired

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["nonDestructiveLegacyTokenRepairV16"] = {
            "enabled": True,
            "emptyRedeemTokenNeverBlanksDurableToken": True,
            "redeemTokenMayFillMissingDurableToken": True,
            "venueWritesChanged": False,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["nonDestructiveLegacyTokenRepair"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV16Handler", (v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; "
        "legacy Poly repair preserves existing durable token IDs",
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
