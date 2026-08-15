from __future__ import annotations

from http.server import ThreadingHTTPServer

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_21 as v4_21
from .target_taker_multi_entry_paper_v1 import COHORT, MultiEntryPaperMixin, VERSION as MULTI_ENTRY_VERSION


VERSION = "PREDICT_WALLET_SHADOW_V0_27_TARGET_TAKER_MULTI_ENTRY_PAPER_V1"


class WalletShadowObserver(MultiEntryPaperMixin, v4_21.WalletShadowObserver):
    """V4.21 plus an isolated paper-only every-signal multi-entry experiment."""

    def snapshot(self):
        payload = super().snapshot()
        payload["version"] = VERSION
        return payload

    def health_snapshot(self):
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["targetTakerMultiEntryExperimentVersion"] = MULTI_ENTRY_VERSION
        payload["targetTakerMultiEntryCohort"] = COHORT
        return payload


class _Handler(v4_21._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_22Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    config = observer.target_taker_live_config
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"TargetTakerMode={config.mode}; venue={config.venue}; "
        f"multiEntry={COHORT}; paperOnly=true; everyNewTradeSnapshot=true; "
        "liveMultiEntry=false",
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
