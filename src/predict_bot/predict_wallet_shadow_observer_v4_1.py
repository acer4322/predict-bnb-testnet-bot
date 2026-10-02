from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3
from . import predict_wallet_shadow_observer_v4 as v4

VERSION = "PREDICT_WALLET_SHADOW_V0_4_TAKER_V1_1"
V1_FOLLOW_MAX_LAG_MS = max(
    500,
    min(5_000, int(float(os.environ.get("PREDICT_WALLET_SHADOW_TAKER_V1_FOLLOW_MAX_LAG_SECONDS", "2.5")) * 1000)),
)


class WalletShadowObserver(v4.WalletShadowObserver):
    """V1.1 keeps Maker fills eligible for a short 0-2.5s follow window."""

    def _advance_taker_v1(
        self,
        new_common_events: list[base.ShadowEvent],
        book: dict[str, Any],
        core: dict[str, Any],
    ) -> None:
        _ = new_common_events
        now_ms = base._now_ms()
        for event in self.shadow_events[-40:]:
            if event.event_type != "MAKER_FILL_PROXY" or event.id in self.taker_v1_processed_maker_fills:
                continue
            age_ms = now_ms - event.at_ms
            if age_ms < 0:
                continue
            if age_ms > V1_FOLLOW_MAX_LAG_MS:
                self.taker_v1_processed_maker_fills.add(event.id)
                continue
            ask = base._finite(book.get("upAsk" if event.side == "UP" else "downAsk"))
            if ask is None or event.price is None:
                continue
            price_delta = ask - event.price
            if not (-1e-9 <= price_delta <= v4.V1_FOLLOW_MAX_PRICE_DELTA + 1e-9):
                continue
            before = len(self.taker_v1_events)
            self._emit_taker_v1(
                side=event.side,
                price=ask,
                requested_shares=v4.V1_BASE_SHARES,
                trigger="MAKER_FOLLOW",
                reason=(
                    f"same-side maker fill proxy followed after {age_ms / 1000:.3f}s; "
                    f"ask-maker delta={price_delta:.4f}"
                ),
                core=core,
                source_event_id=event.id,
            )
            if len(self.taker_v1_events) > before:
                self.taker_v1_processed_maker_fills.add(event.id)

        core_side = core.get("side")
        previous = self.taker_v1_last_core_side
        if core_side in {"UP", "DOWN"}:
            if previous in {"UP", "DOWN"} and previous != core_side:
                ask = base._finite(book.get("upAsk" if core_side == "UP" else "downAsk"))
                self._emit_taker_v1(
                    side=str(core_side),
                    price=ask,
                    requested_shares=v4.V1_CORE_FLIP_SHARES,
                    trigger="CORE_FLIP",
                    reason=f"core direction flipped {previous}->{core_side}; small independent taker probe",
                    core=core,
                )
            self.taker_v1_last_core_side = str(core_side)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        taker_v1 = payload.get("takerV1") if isinstance(payload.get("takerV1"), dict) else {}
        config = taker_v1.get("config") if isinstance(taker_v1.get("config"), dict) else {}
        config["followMaxLagMs"] = V1_FOLLOW_MAX_LAG_MS
        config["makerFollowWindow"] = "each Maker fill proxy stays eligible until followed or 2.5s expires"
        taker_v1["config"] = config
        payload["takerV1"] = taker_v1
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_1Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; retention={v3.RETENTION_DAYS}d; "
        f"takerV1FollowWindow={V1_FOLLOW_MAX_LAG_MS}ms; paper only; "
        f"apiKeyConfigured={bool(observer.api_key)}; db={base.DB_PATH}",
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
