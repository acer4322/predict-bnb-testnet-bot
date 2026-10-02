from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as base


class LiveGradeMultiPredictionObserver(base.MultiPredictionObserver):
    """V2: retain V1 trajectories while binding quote freshness to WS generation.

    ETH/BNB Echtgeld uses the same safety property as the BTC V43 collector: a
    quote timestamp produced by an older websocket session must not authorize a
    BUY after reconnect.  Generic trade events may update lastEventType but never
    migrate an older quote into the new generation.
    """

    def _poly_open(self, ws: Any, generation: int) -> None:
        super()._poly_open(ws, generation)
        if generation != self.poly_generation:
            return
        with self.lock:
            for asset in ("ETH", "BNB"):
                poly = self.assets[asset]["poly"]
                for side_key in ("up", "down"):
                    side = poly.get(side_key)
                    if isinstance(side, dict):
                        side["wsSession"] = int(generation)
                        # Deliberately do not rewrite quoteWsSession.  If the
                        # quote belongs to an older connection it remains old
                        # until a quote-changing event arrives on this session.

    def _apply_poly_quote(
        self,
        event: dict[str, Any],
        event_type: str,
        source_ms: int | None,
        received_ms: int,
    ) -> None:
        token = str(
            event.get("asset_id")
            or event.get("assetId")
            or event.get("token_id")
            or event.get("tokenId")
            or ""
        )
        mapped = self.poly_token_map.get(token)
        super()._apply_poly_quote(event, event_type, source_ms, received_ms)
        if mapped is None:
            return
        asset, outcome = mapped
        side_key = "up" if outcome == "UP" else "down"
        with self.lock:
            side = self.assets[asset]["poly"].get(side_key)
            if not isinstance(side, dict):
                return
            side["wsSession"] = int(self.poly_generation)
            if event_type in base.QUOTE_EVENT_TYPES:
                side["quoteWsSession"] = int(self.poly_generation)
                side["quoteEventType"] = str(event_type)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "MULTI_PREDICTION_OBSERVER_V2"
        payload["liveGradeFreshnessV2"] = {
            "enabled": True,
            "quoteWsSessionPersisted": True,
            "currentWsSessionPersisted": True,
            "oldSessionQuoteCannotAuthorizeNewSession": True,
            "lastTradeCannotRefreshQuoteGeneration": True,
            "liveTradingMethodsPresent": False,
        }
        return payload


def main() -> int:
    observer = LiveGradeMultiPredictionObserver()
    observer.start()
    handler = type(
        "LiveGradeMultiPredictionObserverHandler",
        (base._Handler,),
        {"observer": observer},
    )
    server = base.ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Read-only BTC/ETH/BNB prediction observer V2 listening on "
        f"http://{base.HOST}:{base.PORT}/state; db={base.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
