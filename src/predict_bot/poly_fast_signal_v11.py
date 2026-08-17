from __future__ import annotations

import time
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v10 as v10
from . import poly_fast_signal_v6 as v6

ASSETS = v10.ASSETS
HOST = v10.HOST
PORT = v10.PORT
ROOT = v10.ROOT
DB_PATH = v10.DB_PATH
TAKE_PROFIT_PRICE = 0.95
MAX_ENTRY_PRICE = 0.90


class TakeProfitSignalEvaluator(v10.AssetGuardedSignalEvaluator):
    """V10 plus the original 0.95 take-profit and a strict >0.90 entry ceiling.

    New entries remain subject to all existing GAP, opening-delay, bucket-alignment,
    safe-reject-rearm and BNB-entry-block rules.  The only entry-boundary change
    is that an executable Ask of exactly 0.90 is allowed while Ask > 0.90 is
    blocked.  While an OPEN round exists, an executable same-side Binance Bid at
    or above 0.95 sends the same risk-reducing 8781 exit intent path used by the
    reversal exit.  The original Poly-direction reversal exit remains enabled.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.take_profit_checks = 0
        self.take_profit_triggers = 0
        self.last_take_profit: dict[str, Any] | None = None

    def _gap_evaluation(self, market: dict[str, Any], poly_state: dict[str, Any]) -> dict[str, Any]:
        result = super()._gap_evaluation(market, poly_state)
        # V6 used >= 0.90. The requested production rule is strictly > 0.90,
        # therefore re-open the exact 0.90 boundary only when every other GAP
        # admission condition is already satisfied.
        if str(result.get("state") or "") != "BLOCKED_MAX_ENTRY_PRICE":
            return result
        ask = observer_base._finite(result.get("binanceSelectedAsk"))
        edge = observer_base._finite(result.get("edge"))
        elapsed = observer_base._finite(result.get("elapsedSeconds"))
        direction = str(result.get("direction") or "").upper()
        selected = observer_base._finite(result.get("polySelected"))
        if (
            ask is not None
            and ask <= MAX_ENTRY_PRICE + 1e-12
            and direction in {"UP", "DOWN"}
            and selected is not None
            and edge is not None
            and edge + 1e-12 >= float(v6.SCALP_MIN_EDGE)
            and elapsed is not None
            and elapsed + 1e-12 >= float(v6.ENTRY_DELAY_SECONDS)
        ):
            result.update(
                allowed=True,
                state="ENTRY_READY",
                reason="Poly GAP conditions satisfied; executable Ask is at the allowed 0.90 boundary",
                maxEntryPrice=MAX_ENTRY_PRICE,
                maxEntryComparison="ASK_GT_0.90_BLOCKED",
            )
        else:
            result["maxEntryComparison"] = "ASK_GT_0.90_BLOCKED"
        return result

    def _current_take_profit_bid(self, entry_side: str) -> float | None:
        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        b = observer_base._record(state.get("binance"))
        return observer_base._finite(b.get("upBid") if entry_side == "UP" else b.get("downBid"))

    def _send_take_profit_exit(self, active: dict[str, Any], entry_side: str, bid: float) -> bool:
        round_id = str(active.get("round_id") or "").strip()
        if not round_id:
            return False
        now = time.monotonic()
        if now - self._last_exit_attempt_by_round.get(round_id, 0.0) < 0.50:
            return False
        self._last_exit_attempt_by_round[round_id] = now
        created_at_ms = observer_base._now_ms()
        payload = {
            "intentId": f"poly-fast-exit:{round_id}:{created_at_ms}",
            "strategy": "R_POLY_GAP_SCALP_LIVE",
            "asset": self.asset,
            "roundId": round_id,
            "marketId": int(active.get("market_id") or 0),
            "entrySide": entry_side,
            "newPolyDirection": entry_side,
            "createdAtMs": created_at_ms,
            "reason": "TAKE_PROFIT_0_95",
            "takeProfitPrice": TAKE_PROFIT_PRICE,
            "observedExecutableBid": float(bid),
        }
        self.exit_attempts += 1
        self.take_profit_triggers += 1
        self.last_take_profit = {
            "roundId": round_id,
            "asset": self.asset,
            "side": entry_side,
            "bid": float(bid),
            "threshold": TAKE_PROFIT_PRICE,
            "triggeredAtMs": created_at_ms,
        }
        try:
            response = self.intent_http.post(f"{v6.ECHTGELD_URL}/poly-exit-intent", json=payload)
            data = response.json() if response.content else {}
            if not isinstance(data, dict):
                data = {"raw": str(data)[:300]}
            self.last_exit_result = dict(data)
            self.last_exit_error = None if response.status_code < 400 else f"HTTP {response.status_code}: {str(data)[:300]}"
            if str(data.get("status") or "").upper() == "FLAT":
                self.exit_confirmed_flat += 1
                self._lifecycle_at_mono = 0.0
                self._lifecycle_cache = None
            return True
        except Exception as exc:
            self.last_exit_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            return True

    def _maybe_exit(self, active: dict[str, Any], poly_state: dict[str, Any]) -> None:
        phase = str(active.get("phase") or "").upper()
        if phase != "OPEN":
            return super()._maybe_exit(active, poly_state)
        entry_side = str(active.get("side") or "").upper()
        if entry_side in {"UP", "DOWN"}:
            self.take_profit_checks += 1
            bid = self._current_take_profit_bid(entry_side)
            self.last_take_profit = {
                "roundId": active.get("round_id"),
                "asset": self.asset,
                "side": entry_side,
                "bid": bid,
                "threshold": TAKE_PROFIT_PRICE,
                "triggered": bool(bid is not None and bid + 1e-12 >= TAKE_PROFIT_PRICE),
            }
            if bid is not None and bid + 1e-12 >= TAKE_PROFIT_PRICE:
                if self._send_take_profit_exit(active, entry_side, bid):
                    return
        # Keep the existing immediate Poly-direction-reversal exit as the second
        # independent risk-reducing exit condition.
        return super()._maybe_exit(active, poly_state)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        gap = payload.get("gapEntry")
        if isinstance(gap, dict):
            gap["maxEntryPrice"] = MAX_ENTRY_PRICE
            gap["maxEntryComparison"] = "BLOCK_ONLY_IF_ASK_GT_0.90"
        payload["takeProfit"] = {
            "enabled": True,
            "price": TAKE_PROFIT_PRICE,
            "priceSource": "BINANCE_EXECUTABLE_SAME_SIDE_BID",
            "comparison": "BID_GTE_0.95",
            "checks": int(self.take_profit_checks),
            "triggers": int(self.take_profit_triggers),
            "last": self.last_take_profit,
            "reversalExitStillEnabled": True,
        }
        generation = payload.get("signalGeneration")
        if isinstance(generation, dict):
            generation["exitSignals"] = ["TAKE_PROFIT_BID_GTE_0.95", "POLY_DIRECTION_REVERSAL_IMMEDIATE"]
        return payload


class PolyFastSignalRuntimeV11(v10.PolyFastSignalRuntimeV10):
    def __init__(self) -> None:
        self.observer = v10.v9.v8.v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: TakeProfitSignalEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V11_TAKE_PROFIT_095"
        payload["architecture"].update(
            maxEntryPrice=MAX_ENTRY_PRICE,
            maxEntryRule="BLOCK_ONLY_IF_ASK_GT_0.90",
            takeProfitPrice=TAKE_PROFIT_PRICE,
            takeProfitSource="BINANCE_EXECUTABLE_SAME_SIDE_BID",
            reversalExitStillEnabled=True,
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV11


def main() -> int:
    runtime = PolyFastSignalRuntimeV11()
    runtime.start()
    handler = type("PolyFastSignalV11Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V11 listening on http://{HOST}:{PORT}/state; "
        "entry Ask>0.90 blocked; take-profit Bid>=0.95; reversal-exit=enabled; "
        "BNB-new-entry=blocked; Echtgeld=8781-only",
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
