from __future__ import annotations

import os
import time
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v4 as v4
from . import poly_fast_signal_v5 as v5
from .cross_oracle_strategies import SCALP_MIN_EDGE

ASSETS = v5.ASSETS
HOST = v5.HOST
PORT = v5.PORT
ROOT = v5.ROOT
DB_PATH = v5.DB_PATH
ECHTGELD_URL = v5.ECHTGELD_URL

MODE_POLY_GAP = "POLY_GAP"
MODE_PINNED = "PINNED_DIVERGENCE"
SUPPORTED_ENTRY_MODES = {MODE_POLY_GAP, MODE_PINNED}
DEFAULT_ENTRY_MODE = MODE_POLY_GAP
GAP_STRATEGY = "R_POLY_GAP_SCALP_LIVE"
PINNED_STRATEGY = "R_PINNED_BINANCE_POLY_DIVERGENCE"
ENTRY_DELAY_SECONDS = max(0.0, float(os.environ.get("PREDICT_POLY_GAP_ENTRY_DELAY_SECONDS", "10")))
MAX_ENTRY_PRICE = min(0.99, max(0.01, float(os.environ.get("PREDICT_POLY_GAP_LIVE_MAX_ENTRY_PRICE", "0.90"))))


def _entry_mode() -> str:
    value = str(os.environ.get("PREDICT_POLY_FAST_ENTRY_MODE") or DEFAULT_ENTRY_MODE).strip().upper()
    return value if value in SUPPORTED_ENTRY_MODES else DEFAULT_ENTRY_MODE


class GapLifecycleSignalEvaluator(v5.LifecycleSignalEvaluator):
    """V5 durable lifecycle with the original high-frequency Poly GAP entry restored.

    Entry semantics intentionally follow the dedicated R_POLY_GAP_SCALP live
    lineage: use the fresh Polymarket direction/selected probability, compare it
    to the executable Binance same-side Ask, require SCALP_MIN_EDGE, respect the
    original 10-second market-entry delay and the absolute max-entry-price cap.

    PINNED_DIVERGENCE remains available as an explicit experimental startup mode.
    Echtgeld execution remains 8781-only.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_mode = _entry_mode()
        self.gap_checks = 0
        self.gap_triggers = 0
        self.last_gap_evaluation: dict[str, Any] | None = None

    def _gap_evaluation(self, market: dict[str, Any], poly_state: dict[str, Any]) -> dict[str, Any]:
        self.gap_checks += 1
        direction = str(poly_state.get("direction") or "").upper()
        selected_mid = observer_base._finite(poly_state.get("selectedMid"))
        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        b = observer_base._record(state.get("binance"))
        seconds_left = observer_base._finite(state.get("secondsLeft"))
        elapsed = None if seconds_left is None else max(0.0, 300.0 - seconds_left)
        ask = observer_base._finite(b.get("upAsk") if direction == "UP" else b.get("downAsk"))
        edge = selected_mid - ask if selected_mid is not None and ask is not None else None

        result: dict[str, Any] = {
            "allowed": False,
            "state": "WAITING_DATA",
            "strategy": GAP_STRATEGY,
            "direction": direction or None,
            "polySelected": selected_mid,
            "binanceSelectedAsk": ask,
            "edge": edge,
            "minimumEdge": float(SCALP_MIN_EDGE),
            "entryDelaySeconds": float(ENTRY_DELAY_SECONDS),
            "elapsedSeconds": elapsed,
            "maxEntryPrice": float(MAX_ENTRY_PRICE),
        }
        if direction not in {"UP", "DOWN"} or selected_mid is None:
            result.update(state="WAITING_POLY_DIRECTION", reason="fresh Poly probability has not crossed the original direction threshold")
            return result
        if seconds_left is None:
            result.update(state="WAITING_WINDOW", reason="five-minute window timing unavailable")
            return result
        if elapsed is not None and elapsed + 1e-9 < ENTRY_DELAY_SECONDS:
            result.update(state="WAITING_ENTRY_DELAY", reason="original Poly GAP opening delay has not elapsed")
            return result
        if ask is None or not 0 < ask < 1:
            result.update(state="WAITING_BINANCE_BOOK", reason="same-side Binance Ask unavailable")
            return result
        if ask + 1e-12 >= MAX_ENTRY_PRICE:
            result.update(state="BLOCKED_MAX_ENTRY_PRICE", reason="Binance Ask is at/above the original max-entry-price ceiling")
            return result
        if edge is None or edge + 1e-12 < SCALP_MIN_EDGE:
            result.update(state="WAITING_GAP", reason="Poly selected probability minus Binance Ask is below minimumEdge")
            return result
        result.update(allowed=True, state="ENTRY_READY", reason="original R_POLY_GAP_SCALP_LIVE entry conditions satisfied")
        return result

    def _forward_gap_entry(self, market: dict[str, Any], evaluation: dict[str, Any]) -> None:
        market_id = int(market.get("market_id") or 0)
        direction = str(evaluation.get("direction") or "").upper()
        if market_id <= 0 or direction not in {"UP", "DOWN"}:
            return
        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        b = observer_base._record(state.get("binance"))
        market_meta = observer_base._record(b.get("market"))
        signal_ask = observer_base._finite(b.get("upAsk") if direction == "UP" else b.get("downAsk"))
        token_id = str((market_meta.get("upTokenId") if direction == "UP" else market_meta.get("downTokenId")) or "")
        bucket_start = int(state.get("bucketStartSec") or 0)
        window_end = int(state.get("windowEndMs") or 0)
        seconds_left = observer_base._finite(state.get("secondsLeft"))
        if signal_ask is None or not 0 < signal_ask <= 1 or not token_id or bucket_start <= 0 or window_end <= 0:
            return

        created_at_ms = observer_base._now_ms()
        round_id = f"{self.asset}:{market_id}:{created_at_ms}"
        key = f"{GAP_STRATEGY}:{round_id}"
        now_mono = time.monotonic()
        if now_mono - self._last_attempt_by_key.get(key, 0.0) < v4.RETRY_WHILE_PAUSED_SECONDS:
            return
        self._last_attempt_by_key[key] = now_mono
        intent_id = f"poly-fast:{round_id}:ENTRY"
        payload = {
            "intentId": intent_id,
            "signalId": intent_id,
            "dedupeKey": key,
            "roundId": round_id,
            "strategy": GAP_STRATEGY,
            "asset": self.asset,
            "marketId": market_id,
            "side": direction,
            "tokenId": token_id,
            "feeRateBps": int(market_meta.get("feeRateBps") or 200),
            "signalAsk": float(signal_ask),
            "createdAtMs": created_at_ms,
            "bucketStartSec": bucket_start,
            "windowEndMs": window_end,
            "secondsLeft": seconds_left,
            "polySelected": evaluation.get("polySelected"),
            "binanceSelectedMid": None,
            "divergenceGap": evaluation.get("edge"),
            "entryEdge": evaluation.get("edge"),
            "entryMode": MODE_POLY_GAP,
        }
        self.gateway_attempts += 1
        self.last_gateway_attempt_ms = created_at_ms
        try:
            response = self.intent_http.post(f"{ECHTGELD_URL}/poly-intent", json=payload)
            data = response.json() if response.content else {}
            if not isinstance(data, dict):
                data = {"raw": str(data)[:300]}
            self.last_gateway_result = dict(data)
            self.last_gateway_error = None if response.status_code < 400 else f"HTTP {response.status_code}: {str(data)[:300]}"
            status = str(data.get("status") or "").upper()
            if data.get("queued") is True or status in {"QUEUED", "PROCESSING", "SUBMITTED", "DUPLICATE_FENCE", "ACTIVE_ROUND_EXISTS"}:
                self.gateway_queued += int(data.get("queued") is True)
                self.gap_triggers += 1
                self._lifecycle_at_mono = 0.0
        except Exception as exc:
            self.last_gateway_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def _evaluate_and_forward(self) -> None:
        if self.entry_mode == MODE_PINNED:
            return super()._evaluate_and_forward()

        market = self._prime_market(force=False)
        poly_state = self._poly_state()
        if not isinstance(market, dict) or not isinstance(poly_state, dict):
            return
        active = self._active_round()
        if isinstance(active, dict):
            self._maybe_exit(active, poly_state)
            return

        evaluation = self._gap_evaluation(market, poly_state)
        self.last_gap_evaluation = dict(evaluation)
        if evaluation.get("allowed") is True:
            self._forward_gap_entry(market, evaluation)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["entryStrategyMode"] = self.entry_mode
        payload["entryStrategy"] = GAP_STRATEGY if self.entry_mode == MODE_POLY_GAP else PINNED_STRATEGY
        payload["availableEntryModes"] = sorted(SUPPORTED_ENTRY_MODES)
        payload["gapEntry"] = {
            "enabled": self.entry_mode == MODE_POLY_GAP,
            "strategy": GAP_STRATEGY,
            "minimumEdge": float(SCALP_MIN_EDGE),
            "entryDelaySeconds": float(ENTRY_DELAY_SECONDS),
            "maxEntryPrice": float(MAX_ENTRY_PRICE),
            "checks": int(self.gap_checks),
            "triggers": int(self.gap_triggers),
            "lastEvaluation": self.last_gap_evaluation,
        }
        generation = payload.get("signalGeneration")
        if isinstance(generation, dict):
            generation["entryStrategy"] = payload["entryStrategy"]
            generation["entryMode"] = self.entry_mode
            generation["exitSignal"] = "POLY_DIRECTION_REVERSAL_IMMEDIATE"
        return payload


class PolyFastSignalRuntimeV6(v5.PolyFastSignalRuntimeV5):
    def __init__(self) -> None:
        self.observer = v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: GapLifecycleSignalEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V6_GAP_LIFECYCLE"
        payload["architecture"].update(
            defaultEntryMode=DEFAULT_ENTRY_MODE,
            availableEntryModes=sorted(SUPPORTED_ENTRY_MODES),
            highFrequencyGapRestored=True,
            pinnedDivergenceExperimental=True,
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV6


def main() -> int:
    runtime = PolyFastSignalRuntimeV6()
    runtime.start()
    handler = type("PolyFastSignalV6Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V6 listening on http://{HOST}:{PORT}/state; entry={_entry_mode()}; lifecycle=one-active-round; Echtgeld=8781-only",
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
