from __future__ import annotations

from http.server import ThreadingHTTPServer

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v4 as v4


VERSION = "ECHTGELD_ENGINE_V2_4310_BALANCE_PNL_STOP_LOSS_REDEEM_POLY_FAST_V2"
HOST = v4.HOST
PORT = v4.PORT


class EchtgeldEngine(v4.EchtgeldEngine):
    def submit_poly_fast_intent(self, raw):
        if str(self.config.venue).strip().lower() != "binance":
            raise v1.EchtgeldEngineError(
                "Poly Fast execution requires Echtgeld venue=binance; pause 8781 and select Binance before enabling Poly Fast"
            )
        return super().submit_poly_fast_intent(raw)

    def state(self):
        payload = super().state()
        payload["version"] = VERSION
        gateway = payload.get("polyFastGateway")
        if isinstance(gateway, dict):
            gateway["binanceVenueRequired"] = True
            gateway["currentVenueEligible"] = str(self.config.venue).strip().lower() == "binance"
        return payload

    def health(self):
        payload = super().health()
        payload["version"] = VERSION
        payload["polyFastBinanceVenueRequired"] = True
        return payload


class _Handler(v4._Handler):
    engine: EchtgeldEngine


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV5Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "poly-fast-gateway=/poly-intent; poly-fast-venue=binance-only; venue-owner=8781-only",
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
