from __future__ import annotations

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v8 as base
from .wallet_maker_clone_pair_spread_gate import PairMakerSpreadV84Mixin


class PairMakerSpreadBinanceV84WalletMakerCloneEngine(
    PairMakerSpreadV84Mixin,
    base.BoundedRiskPairedWalletMakerCloneEngine,
):
    VERSION = "WALLET_MAKER_CLONE_LIVE_V8_4_PASSIVE_MAKER_SPREAD"

    def snapshot(self):
        payload = super().snapshot()
        payload["executionVenue"] = "BINANCE_PREDICTION"
        payload["executionPath"] = "BINANCE_PREDICTION_LIMIT_GTC_PASSIVE_MAKER_SPREAD_V84"
        return payload


def main() -> int:
    engine = PairMakerSpreadBinanceV84WalletMakerCloneEngine()
    engine.start()
    handler = type("PairMakerSpreadBinanceV84WalletMakerCloneHandler", (core._Handler,), {"engine": engine})
    server = core.ThreadingHTTPServer((core.HOST, core.PORT), handler)
    print(
        f"{engine.VERSION} {core.ASSET} listening on http://{core.HOST}:{core.PORT}/state; "
        f"venue=BINANCE; masterEnabled={core.MASTER_ENABLED}; db={core.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
