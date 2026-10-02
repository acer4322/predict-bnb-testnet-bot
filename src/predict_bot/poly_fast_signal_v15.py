from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v12 as v12
from . import poly_fast_signal_v14 as v14
from . import poly_gap_live as poly_base

ASSETS = v14.ASSETS
HOST = v14.HOST
PORT = v14.PORT
ROOT = v14.ROOT
DB_PATH = v14.DB_PATH


class EmbeddedPolyStateEvaluator(v12.PostRejectDiagnosticEvaluator):
    """V14 trading/diagnostics with Poly state sourced from the embedded 8792 observer.

    The inherited Poly GAP engine still read CROSS_ORACLE_URL inside _poly_state(),
    even though the fast-signal architecture had already moved Poly/Binance feeds
    in-process.  That could leave the evaluator in WAITING_POLY_DIRECTION while
    the embedded observer showed a fresh, strongly directional Poly book.

    This override changes only the source/projection of Poly state.  Thresholds,
    probability math, entry delay, gap admission, direction rules, TP, reversal,
    safe rearm and Echtgeld forwarding are unchanged.
    """

    def _poly_state(self) -> dict[str, Any] | None:
        try:
            with self._embedded_observer.lock:
                state = self._embedded_observer._asset_snapshot(self.asset)
        except Exception as exc:
            self.last_error = f"{self.asset} embedded Poly state: {str(exc)[:300]}"
            return None

        poly = observer_base._record(state.get("poly"))
        if str(poly.get("status") or "").upper() != "LIVE":
            self.last_error = "Polymarket feed is not LIVE"
            return None

        received_ms = observer_base._finite(poly.get("receivedTimestampMs"))
        if received_ms is None:
            received_candidates = []
            for side_key in ("up", "down"):
                side = observer_base._record(poly.get(side_key))
                value = observer_base._finite(
                    side.get("quoteReceivedTimestampMs") or side.get("receivedTimestampMs")
                )
                if value is not None:
                    received_candidates.append(value)
            received_ms = max(received_candidates) if received_candidates else None

        now_ms = observer_base._now_ms()
        age = None if received_ms is None else max(0.0, now_ms - received_ms)
        if age is None or age > poly_base.MAX_POLY_AGE_MS:
            self.last_error = f"Polymarket quote stale: {age}ms"
            return None

        up = observer_base._record(poly.get("up"))
        down = observer_base._record(poly.get("down"))
        market = observer_base._record(poly.get("market"))
        if not up or not down or not market:
            self.last_error = "Polymarket embedded book/market unavailable"
            return None

        up_mid = poly_base.probability_mid(up.get("bestBid"), up.get("bestAsk"))
        if up_mid is None:
            self.last_error = "Polymarket UP midpoint unavailable"
            return None

        direction = poly_base.probability_direction(up_mid)
        result = {
            "upMid": up_mid,
            "direction": direction,
            "selectedMid": poly_base.selected_probability(up_mid, direction) if direction else None,
            "ageMs": age,
            "slug": market.get("slug") or market.get("eventSlug") or market.get("gammaMarketSlug"),
            "windowEndMs": int(state.get("windowEndMs") or market.get("windowEndMs") or 0),
            "receivedTimestampMs": int(received_ms) if received_ms is not None else None,
            "gapGeneration": None,
            "source": "8792_EMBEDDED_OBSERVER",
        }
        self.last_poly = result
        self.last_error = None
        return result


class PolyFastSignalRuntimeV15(v14.PolyFastSignalRuntimeV14):
    def __init__(self) -> None:
        self.observer = v14.ActiveCurrentBinanceObserver(db_path=DB_PATH)
        self.engines = {
            asset: EmbeddedPolyStateEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V15_EMBEDDED_POLY_STATE"
        payload["architecture"].update(
            polyStateTransport="IN_PROCESS_MEMORY",
            crossOraclePolyStateBypassed=True,
        )
        return payload

    def diagnostics(self) -> dict[str, Any]:
        payload = super().diagnostics()
        payload["strategyVersion"] = self.snapshot().get("version")
        payload["behavior"]["note"] = (
            "V1.2 diagnostics; Binance strict current-market retry; Poly evaluator state now "
            "comes from the same embedded 8792 observer shown by diagnostics. Trading rules are unchanged."
        )
        return payload


class _Handler(v14._Handler):
    runtime: PolyFastSignalRuntimeV15


def main() -> int:
    runtime = PolyFastSignalRuntimeV15()
    runtime.start()
    handler = type("PolyFastSignalV15Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V15 listening on http://{HOST}:{PORT}/state; "
        "Poly state=8792 embedded observer; Binance=current-active-only retry; "
        "V14 diagnostics + V12 trading rules unchanged",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.10)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
