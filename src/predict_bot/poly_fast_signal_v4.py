from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_live_v3 as v3


ASSETS = v3.ASSETS
HOST = v3.HOST
PORT = v3.PORT
ROOT = v3.ROOT
DB_PATH = v3.DB_PATH
FAST_BINANCE_POLL_SECONDS = v3.FAST_BINANCE_POLL_SECONDS
ECHTGELD_URL = "http://127.0.0.1:8781"
SIGNAL_LOOP_SECONDS = 0.05
RETRY_WHILE_PAUSED_SECONDS = 0.50


class SignalOnlyPinnedEvaluator(v3.SelfContainedPinnedEngine):
    """Reuse the pinned detector but deliberately never start its venue worker."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.signal_thread: threading.Thread | None = None
        self.intent_http = httpx.Client(
            timeout=httpx.Timeout(0.8, connect=0.2),
            trust_env=False,
            headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Poly-Fast-Signal/1.0"},
        )
        self.last_gateway_result: dict[str, Any] | None = None
        self.last_gateway_error: str | None = None
        self.last_gateway_attempt_ms: int | None = None
        self.gateway_attempts = 0
        self.gateway_queued = 0
        self._completed_keys: set[str] = set()
        self._last_attempt_by_key: dict[str, float] = {}

    def _observer_asset_state_for_pin(self) -> dict[str, Any] | None:
        # The inherited detector special-cases BTC back to 8770.  8792 must be
        # self-contained for every asset, including BTC.
        return self._asset_observer_state()

    def start(self) -> None:
        if self.signal_thread and self.signal_thread.is_alive():
            return
        self.signal_thread = threading.Thread(
            target=self._signal_loop,
            name=f"poly-fast-signal-{self.asset.lower()}",
            daemon=True,
        )
        self.signal_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.signal_thread and self.signal_thread.is_alive():
            self.signal_thread.join(timeout=2.0)
        try:
            self.intent_http.close()
        except Exception:
            pass
        # Close DB/HTTP resources from the inherited evaluator, but never invoke
        # its live execution worker because it was never started.
        try:
            self.http.close()
        except Exception:
            pass
        with self.db_lock:
            try:
                self.db.commit()
                self.db.close()
            except Exception:
                pass

    def _signal_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._evaluate_and_forward()
            except Exception as exc:
                self.last_gateway_error = f"signal loop: {type(exc).__name__}: {str(exc)[:300]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.01, SIGNAL_LOOP_SECONDS - elapsed))

    def _evaluate_and_forward(self) -> None:
        market = self._prime_market(force=False)
        poly_state = self._poly_state()
        if not isinstance(market, dict) or not isinstance(poly_state, dict):
            return
        evaluation = self._evaluate_pinned_divergence(poly_state)
        self._last_pinned_evaluation = dict(evaluation)
        if evaluation.get("allowed") is not True:
            return

        market_id = int(market.get("market_id") or 0)
        direction = str(evaluation.get("direction") or "").upper()
        if market_id <= 0 or direction not in {"UP", "DOWN"}:
            return
        key = f"R_PINNED_BINANCE_POLY_DIVERGENCE:{self.asset}:{market_id}"
        if key in self._completed_keys:
            return
        now_mono = time.monotonic()
        if now_mono - self._last_attempt_by_key.get(key, 0.0) < RETRY_WHILE_PAUSED_SECONDS:
            return
        self._last_attempt_by_key[key] = now_mono

        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        b = observer_base._record(state.get("binance"))
        signal_ask = observer_base._finite(b.get("upAsk") if direction == "UP" else b.get("downAsk"))
        bucket_start = int(state.get("bucketStartSec") or 0)
        window_end = int(state.get("windowEndMs") or 0)
        seconds_left = observer_base._finite(state.get("secondsLeft"))
        if signal_ask is None or not 0 < signal_ask <= 1 or bucket_start <= 0 or window_end <= 0:
            return

        created_at_ms = observer_base._now_ms()
        intent_id = f"poly-fast:{self.asset}:{market_id}:{direction}:{created_at_ms}"
        payload = {
            "intentId": intent_id,
            "signalId": intent_id,
            "dedupeKey": key,
            "strategy": "R_PINNED_BINANCE_POLY_DIVERGENCE",
            "asset": self.asset,
            "marketId": market_id,
            "side": direction,
            "signalAsk": float(signal_ask),
            "createdAtMs": created_at_ms,
            "bucketStartSec": bucket_start,
            "windowEndMs": window_end,
            "secondsLeft": seconds_left,
            "polySelected": evaluation.get("polySelected"),
            "binanceSelectedMid": evaluation.get("binanceSelectedMid"),
            "divergenceGap": evaluation.get("divergenceGap"),
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
            if data.get("queued") is True or status in {"QUEUED", "PROCESSING", "SUBMITTED", "DUPLICATE_FENCE"}:
                self.gateway_queued += int(data.get("queued") is True)
                self._completed_keys.add(key)
        except Exception as exc:
            self.last_gateway_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["realMoney"] = False
        payload["masterEnabled"] = False
        payload["status"] = "SIGNAL_ONLY"
        payload["credentialSource"] = "NOT_USED_BY_8792"
        payload["polyFastSignalGateway"] = {
            "enabled": True,
            "producerOnly": True,
            "executionDisabledInside8792": True,
            "gateway": ECHTGELD_URL,
            "endpoint": "/poly-intent",
            "venueOwner": "8781_ONLY",
            "attempts": int(self.gateway_attempts),
            "queued": int(self.gateway_queued),
            "lastAttemptMs": self.last_gateway_attempt_ms,
            "lastResult": self.last_gateway_result,
            "lastError": self.last_gateway_error,
            "retryWhileGatewayPausedMs": int(RETRY_WHILE_PAUSED_SECONDS * 1000),
        }
        pf = payload.get("polyFastLive")
        if isinstance(pf, dict):
            pf["orderExecution"] = "NONE_SIGNAL_ONLY"
            pf["echtgeldGateway"] = ECHTGELD_URL
            pf["executionLivesInside8792"] = False
        return payload


class PolyFastSignalRuntimeV4:
    def __init__(self) -> None:
        self.observer = v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines: dict[str, SignalOnlyPinnedEvaluator] = {
            asset: SignalOnlyPinnedEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def start(self) -> None:
        self.observer.start()
        for engine in self.engines.values():
            engine.start()

    def stop(self) -> None:
        for engine in self.engines.values():
            try:
                engine.stop()
            except Exception:
                pass
        self.observer.stop()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": "POLY_FAST_SIGNAL_V4",
            "realMoney": False,
            "host": HOST,
            "port": PORT,
            "architecture": {
                "processes": 1,
                "assets": list(ASSETS),
                "observerTransport": "IN_PROCESS_MEMORY",
                "marketIdentityTransport": "IN_PROCESS_MEMORY",
                "port8766Required": False,
                "port8770Required": False,
                "port8781RequiredForExecution": True,
                "executionLivesInside8792": False,
                "venueOwner": "8781_ONLY",
                "producerOnly": True,
            },
            "observer": self.observer.snapshot(),
            "assets": {asset: engine.snapshot() for asset, engine in self.engines.items()},
        }

    def update_settings(self, asset: str, values: dict[str, Any]) -> dict[str, Any]:
        key = str(asset).upper()
        engine = self.engines.get(key)
        if engine is None:
            raise ValueError("asset must be BTC, ETH or BNB")
        # runtimeEnabled belongs to 8781 now; silently reject attempts to arm 8792.
        if "runtimeEnabled" in values:
            values = dict(values)
            values.pop("runtimeEnabled", None)
        return engine.update_settings(values) if values else engine.snapshot()


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV4


def main() -> int:
    runtime = PolyFastSignalRuntimeV4()
    runtime.start()
    handler = type("PolyFastSignalV4Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V4 listening on http://{HOST}:{PORT}/state; "
        f"assets={','.join(ASSETS)}; producer-only=true; Echtgeld=8781-only",
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
