from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_11 as v4_11


VERSION = "PREDICT_WALLET_SHADOW_V0_15_TARGET_CORE_INTEGRATED_PAPER"


class WalletShadowObserver(v4_11.WalletShadowObserver):
    """V4.11 labs plus an isolated target-core Maker/Taker integrated cohort."""

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        # Health must not wait behind the observer's coarse processing lock.
        # The fields below are single-reference reads under CPython and are only
        # diagnostic; a slightly mixed instant is preferable to making the
        # supervisor/dashboard report the healthy paper observer as offline.
        market_id = self.market_id
        last_poll_ms = self.last_poll_ms
        error = self.last_error
        now_ms = base._now_ms()
        status = "LIVE" if self.api_key and market_id is not None and not error else "DEGRADED" if market_id else "WAITING"
        return {
            "version": VERSION,
            "status": status,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "error": error,
            "marketId": market_id,
            "lastPollMs": last_poll_ms,
            "pollAgeMs": now_ms - int(last_poll_ms) if last_poll_ms else None,
            "collector8777": dict(self.signal_collector_status),
            "lastFullStateGenerationMs": self.last_full_state_generation_ms,
            "checkedAtMs": now_ms,
        }


class _Handler(v4_11._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_12Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "target-core Maker/Taker integrated forward cohort active; paperOnly=true; liveOrdersAffected=false",
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
