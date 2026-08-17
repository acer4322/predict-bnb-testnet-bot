from __future__ import annotations

import os
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v4 as v4
from . import poly_fast_signal_v6 as v6

ASSETS = v6.ASSETS
HOST = v6.HOST
PORT = v6.PORT
ROOT = v6.ROOT
DB_PATH = v6.DB_PATH

POLY_UP_THRESHOLD = min(0.99, max(0.50, float(os.environ.get("PREDICT_POLY_GAP_UP_THRESHOLD", "0.55"))))
POLY_DOWN_THRESHOLD = max(0.01, min(0.50, float(os.environ.get("PREDICT_POLY_GAP_DOWN_THRESHOLD", "0.45"))))


class GapDirectionLifecycleSignalEvaluator(v6.GapLifecycleSignalEvaluator):
    """V6 GAP lifecycle with direction derived from the fresh embedded Poly book.

    V6 accidentally reused the inherited PINNED detector's _poly_state() direction,
    so the GAP evaluator could report WAITING_POLY_DIRECTION even while Poly was
    strongly UP or DOWN.  V7 restores the original Poly GAP 0.55/0.45 direction
    semantics directly from the in-process Polymarket bid/ask mids.  The same
    normalized direction is used for both entry and reversal exit.
    """

    def _gap_poly_state(self) -> dict[str, Any]:
        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        poly = observer_base._record(state.get("poly"))
        up = observer_base._record(poly.get("up"))
        down = observer_base._record(poly.get("down"))

        up_bid = observer_base._finite(up.get("bestBid"))
        up_ask = observer_base._finite(up.get("bestAsk"))
        down_bid = observer_base._finite(down.get("bestBid"))
        down_ask = observer_base._finite(down.get("bestAsk"))

        up_mid = (
            (up_bid + up_ask) / 2.0
            if up_bid is not None and up_ask is not None
            else None
        )
        down_mid = (
            (down_bid + down_ask) / 2.0
            if down_bid is not None and down_ask is not None
            else None
        )

        direction: str | None = None
        selected_mid: float | None = None
        if up_mid is not None and up_mid >= POLY_UP_THRESHOLD:
            direction = "UP"
            selected_mid = up_mid
        elif up_mid is not None and up_mid <= POLY_DOWN_THRESHOLD:
            direction = "DOWN"
            selected_mid = down_mid if down_mid is not None else 1.0 - up_mid
        elif down_mid is not None and down_mid >= (1.0 - POLY_DOWN_THRESHOLD):
            direction = "DOWN"
            selected_mid = down_mid
        elif down_mid is not None and down_mid <= (1.0 - POLY_UP_THRESHOLD):
            direction = "UP"
            selected_mid = up_mid if up_mid is not None else 1.0 - down_mid

        return {
            "direction": direction,
            "selectedMid": selected_mid,
            "polyUpMid": up_mid,
            "polyDownMid": down_mid,
            "upThreshold": float(POLY_UP_THRESHOLD),
            "downThreshold": float(POLY_DOWN_THRESHOLD),
            "source": "8792_EMBEDDED_POLY_BOOK",
        }

    def _evaluate_and_forward(self) -> None:
        if self.entry_mode == v6.MODE_PINNED:
            return super()._evaluate_and_forward()

        market = self._prime_market(force=False)
        if not isinstance(market, dict):
            return
        poly_state = self._gap_poly_state()
        active = self._active_round()
        if isinstance(active, dict):
            self._maybe_exit(active, poly_state)
            return

        evaluation = self._gap_evaluation(market, poly_state)
        evaluation["polyUpMid"] = poly_state.get("polyUpMid")
        evaluation["polyDownMid"] = poly_state.get("polyDownMid")
        evaluation["polyUpThreshold"] = float(POLY_UP_THRESHOLD)
        evaluation["polyDownThreshold"] = float(POLY_DOWN_THRESHOLD)
        evaluation["directionSource"] = "8792_EMBEDDED_POLY_BOOK"
        self.last_gap_evaluation = dict(evaluation)
        if evaluation.get("allowed") is True:
            self._forward_gap_entry(market, evaluation)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        gap = payload.get("gapEntry")
        if isinstance(gap, dict):
            gap["polyUpThreshold"] = float(POLY_UP_THRESHOLD)
            gap["polyDownThreshold"] = float(POLY_DOWN_THRESHOLD)
            gap["directionSource"] = "8792_EMBEDDED_POLY_BOOK"
            gap["pinnedDirectionDependency"] = False
        return payload


class PolyFastSignalRuntimeV7(v6.PolyFastSignalRuntimeV6):
    def __init__(self) -> None:
        self.observer = v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: GapDirectionLifecycleSignalEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V7_GAP_DIRECTION_FIX"
        payload["architecture"].update(
            gapDirectionSource="8792_EMBEDDED_POLY_BOOK",
            gapDirectionUsesPinnedDetector=False,
            polyUpThreshold=float(POLY_UP_THRESHOLD),
            polyDownThreshold=float(POLY_DOWN_THRESHOLD),
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV7


def main() -> int:
    runtime = PolyFastSignalRuntimeV7()
    runtime.start()
    handler = type("PolyFastSignalV7Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V7 listening on http://{HOST}:{PORT}/state; "
        f"entry={v6._entry_mode()}; gap-direction=Poly {POLY_UP_THRESHOLD:.2f}/{POLY_DOWN_THRESHOLD:.2f}; Echtgeld=8781-only",
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
