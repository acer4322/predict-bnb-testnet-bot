from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v4 as v4
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v9 as v9

VERSION = "ECHTGELD_ENGINE_V2_4310_POLY_MULTI_STRATEGY_V7"
HOST = v9.HOST
PORT = v9.PORT
GAP_STRATEGY = "R_POLY_GAP_SCALP_LIVE"
PINNED_STRATEGY = "R_PINNED_BINANCE_POLY_DIVERGENCE"
POLY_STRATEGIES = {GAP_STRATEGY, PINNED_STRATEGY}


class EchtgeldEngine(v9.EchtgeldEngine):
    """V9 settlement/risk engine accepting both production GAP and experimental PINNED Poly intents."""

    _poly_strategy_lock = threading.RLock()

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        supplied = str(raw.get("strategy") or GAP_STRATEGY).strip()
        if supplied not in POLY_STRATEGIES:
            raise v9.v8.v7.v6.v5.v4.v1.EchtgeldEngineError(f"unsupported Poly Fast strategy: {supplied}")
        # V4's adapter was originally built around one Poly strategy constant.
        # Keep its mature schema/execution path untouched, but select that
        # constant for the duration of this serialized admission call so the
        # durable payload records the real source strategy.
        with self._poly_strategy_lock:
            previous = v4.POLY_FAST_STRATEGY
            v4.POLY_FAST_STRATEGY = supplied
            try:
                result = super().submit_poly_fast_intent(raw)
            finally:
                v4.POLY_FAST_STRATEGY = previous
        result["sourceStrategy"] = supplied
        return result

    def _intent_source_strategies(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        with self.db_lock:
            rows = self.db.execute(
                "SELECT intent_id,payload_json FROM engine_intents WHERE intent_id LIKE 'poly-fast:%'"
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except Exception:
                continue
            strategy = str(payload.get("sourceStrategy") or "").strip()
            if strategy in POLY_STRATEGIES:
                mapping[str(row["intent_id"])] = strategy
        return mapping

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = super().orders(limit)
        strategies = self._intent_source_strategies()
        for row in rows:
            intent_id = str(row.get("intent_id") or "")
            strategy = strategies.get(intent_id)
            if strategy:
                row["strategy"] = strategy
                row["sourceStrategy"] = strategy
        return rows

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        allowed = set(payload.get("allowedStrategies") or [])
        allowed.update(POLY_STRATEGIES)
        payload["allowedStrategies"] = sorted(allowed)
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway["strategies"] = sorted(POLY_STRATEGIES)
            gateway["defaultProductionStrategy"] = GAP_STRATEGY
            gateway["experimentalStrategy"] = PINNED_STRATEGY
            gateway["canonicalLedgerIdentity"] = "PER_INTENT_SOURCE_STRATEGY"
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyGapStrategyAccepted"] = True
        payload["polyPinnedStrategyAccepted"] = True
        return payload


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV10Handler", (v9.v8.v7.v6._Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; Poly GAP+PINNED accepted; venue-owner=8781-only",
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
