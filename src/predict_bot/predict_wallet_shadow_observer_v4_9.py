from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_8 as v4_8


VERSION = "PREDICT_WALLET_SHADOW_V0_12_RECENTERED_POOLED_INVENTORY_PAPER"


class WalletShadowObserver(v4_8.WalletShadowObserver):
    """V4.8 labs plus an isolated recentered pooled-inventory Maker cohort."""

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        return payload


class _Handler(v4_8._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_9Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "recentered pooled-inventory Maker forward cohort active; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
