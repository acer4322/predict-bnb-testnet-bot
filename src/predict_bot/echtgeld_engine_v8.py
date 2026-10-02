from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v7 as v7

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_LIFECYCLE_REDEEM_PNL_STOP_LOSS_V5_MIGRATION"
HOST = v7.HOST
PORT = v7.PORT


class EchtgeldEngine(v7.EchtgeldEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.legacy_poly_rows_migrated = self._migrate_legacy_poly_rows()

    def _migrate_legacy_poly_rows(self) -> int:
        migrated = 0
        with self.db_lock:
            rows = self.db.execute(
                """SELECT i.* FROM engine_intents i
                     LEFT JOIN engine_poly_rounds r ON r.entry_intent_id=i.intent_id
                    WHERE r.entry_intent_id IS NULL AND i.intent_id LIKE 'poly-fast:%'
                    ORDER BY i.created_at_ms ASC"""
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                try:
                    payload = json.loads(str(row.get("payload_json") or "{}"))
                except Exception:
                    payload = {}
                snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
                asset = str(payload.get("asset") or snapshot.get("asset") or "").strip().upper()
                if asset not in {"BTC", "ETH", "BNB"}:
                    parts = str(row.get("intent_id") or "").split(":")
                    asset = parts[1].upper() if len(parts) > 1 else ""
                if asset not in {"BTC", "ETH", "BNB"}:
                    continue
                market_id = int(row.get("market_id") or 0)
                side = str(row.get("side") or "").upper()
                if market_id <= 0 or side not in {"UP", "DOWN"}:
                    continue
                token_id = ""
                redeem = self.db.execute(
                    "SELECT token_id FROM engine_redeems WHERE venue_market_id=? AND side=? ORDER BY discovered_at_ms DESC LIMIT 1",
                    (market_id, side),
                ).fetchone()
                if redeem is not None:
                    token_id = str(redeem["token_id"] or "")
                intent_id = str(row.get("intent_id") or "")
                round_id = f"legacy:{intent_id}"
                self.db.execute(
                    """INSERT OR IGNORE INTO engine_poly_rounds(
                           entry_intent_id,round_id,asset,market_id,side,token_id,fee_rate_bps,created_at_ms
                       ) VALUES(?,?,?,?,?,?,200,?)""",
                    (intent_id, round_id, asset, market_id, side, token_id, int(row.get("created_at_ms") or v1._now_ms())),
                )
                migrated += 1
            self.db.commit()
        return migrated

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway["legacyPolyRowsMigrated"] = int(self.legacy_poly_rows_migrated)
            gateway["legacyIntentPrefixMigration"] = "poly-fast:*"
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["legacyPolyRowsMigrated"] = int(self.legacy_poly_rows_migrated)
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV8Handler", (v7.v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"{VERSION} listening on http://{HOST}:{PORT}; legacy Poly migration complete", flush=True)
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
