from __future__ import annotations

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_predict_direct_v8_2 as base
from .wallet_maker_clone_pair_locked_edge import PairLockedEdgeV83Mixin


class PairLockedEdgePredictDirectV83WalletMakerCloneEngine(
    PairLockedEdgeV83Mixin,
    base.PredictDirectV82WalletMakerCloneEngine,
):
    VERSION = "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_3_PAIR_LOCKED_EDGE"

    def snapshot(self):
        payload = super().snapshot()
        payload["executionVenue"] = "PREDICT_DIRECT"
        payload["executionPath"] = "PREDICT_NATIVE_POST_ONLY_LIMIT_PAIR_LOCKED_EDGE_V83"
        return payload


def main() -> int:
    engine = PairLockedEdgePredictDirectV83WalletMakerCloneEngine()
    engine.start()
    handler = type("PairLockedEdgePredictDirectV83WalletMakerCloneHandler", (core._Handler,), {"engine": engine})
    server = core.ThreadingHTTPServer((core.HOST, core.PORT), handler)
    print(
        f"{engine.VERSION} {core.ASSET} listening on http://{core.HOST}:{core.PORT}/state; "
        f"venue=PREDICT_DIRECT; masterEnabled={core.MASTER_ENABLED}; db={core.DB_PATH}",
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
