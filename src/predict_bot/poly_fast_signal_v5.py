from __future__ import annotations

import time
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v4 as v4

ASSETS = v4.ASSETS
HOST = v4.HOST
PORT = v4.PORT
ROOT = v4.ROOT
DB_PATH = v4.DB_PATH
ECHTGELD_URL = v4.ECHTGELD_URL
LIFECYCLE_REFRESH_SECONDS = 0.15


class LifecycleSignalEvaluator(v4.SignalOnlyPinnedEvaluator):
    """8792 detector with the original sequential scalp lifecycle restored.

    There may be many rounds in one five-minute market, but only one active or
    potentially-active round per asset.  Once 8781 confirms an entry, repeated
    same-direction entry signals are ignored until a Poly direction reversal is
    sent as an exit intent and 8781 confirms the position flat.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lifecycle_cache: dict[str, Any] | None = None
        self._lifecycle_at_mono = 0.0
        self.exit_attempts = 0
        self.exit_confirmed_flat = 0
        self.last_exit_result: dict[str, Any] | None = None
        self.last_exit_error: str | None = None
        self._last_exit_attempt_by_round: dict[str, float] = {}

    def _active_round(self, *, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        if force or now - self._lifecycle_at_mono >= LIFECYCLE_REFRESH_SECONDS:
            try:
                response = self.intent_http.get(f"{ECHTGELD_URL}/poly-lifecycle")
                data = response.json() if response.content else {}
                lifecycle = data.get("lifecycle") if isinstance(data, dict) else None
                if isinstance(lifecycle, dict):
                    row = lifecycle.get(self.asset)
                    self._lifecycle_cache = dict(row) if isinstance(row, dict) else None
                    self._lifecycle_at_mono = now
                    self.last_gateway_error = None
            except Exception as exc:
                self.last_gateway_error = f"lifecycle: {type(exc).__name__}: {str(exc)[:300]}"
        return dict(self._lifecycle_cache) if isinstance(self._lifecycle_cache, dict) else None

    def _maybe_exit(self, active: dict[str, Any], poly_state: dict[str, Any]) -> None:
        phase = str(active.get("phase") or "").upper()
        if phase != "OPEN":
            return
        entry_side = str(active.get("side") or "").upper()
        direction = str(poly_state.get("direction") or "").upper()
        if entry_side not in {"UP", "DOWN"} or direction not in {"UP", "DOWN"}:
            return
        if direction == entry_side:
            return
        round_id = str(active.get("round_id") or "").strip()
        if not round_id:
            return
        now = time.monotonic()
        if now - self._last_exit_attempt_by_round.get(round_id, 0.0) < 0.50:
            return
        self._last_exit_attempt_by_round[round_id] = now
        created_at_ms = observer_base._now_ms()
        payload = {
            "intentId": f"poly-fast-exit:{round_id}:{created_at_ms}",
            "strategy": "R_PINNED_BINANCE_POLY_DIVERGENCE",
            "asset": self.asset,
            "roundId": round_id,
            "marketId": int(active.get("market_id") or 0),
            "entrySide": entry_side,
            "newPolyDirection": direction,
            "createdAtMs": created_at_ms,
            "reason": "POLY_DIRECTION_REVERSAL",
        }
        self.exit_attempts += 1
        try:
            response = self.intent_http.post(f"{ECHTGELD_URL}/poly-exit-intent", json=payload)
            data = response.json() if response.content else {}
            if not isinstance(data, dict):
                data = {"raw": str(data)[:300]}
            self.last_exit_result = dict(data)
            self.last_exit_error = None if response.status_code < 400 else f"HTTP {response.status_code}: {str(data)[:300]}"
            if str(data.get("status") or "").upper() == "FLAT":
                self.exit_confirmed_flat += 1
                self._lifecycle_at_mono = 0.0
                self._lifecycle_cache = None
        except Exception as exc:
            self.last_exit_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def _evaluate_and_forward(self) -> None:
        market = self._prime_market(force=False)
        poly_state = self._poly_state()
        if not isinstance(market, dict) or not isinstance(poly_state, dict):
            return

        active = self._active_round()
        if isinstance(active, dict):
            self._maybe_exit(active, poly_state)
            return

        evaluation = self._evaluate_pinned_divergence(poly_state)
        self._last_pinned_evaluation = dict(evaluation)
        if evaluation.get("allowed") is not True:
            return
        market_id = int(market.get("market_id") or 0)
        direction = str(evaluation.get("direction") or "").upper()
        if market_id <= 0 or direction not in {"UP", "DOWN"}:
            return
        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        b = observer_base._record(state.get("binance"))
        market_meta = observer_base._record(b.get("market"))
        signal_ask = observer_base._finite(b.get("upAsk") if direction == "UP" else b.get("downAsk"))
        token_id = str(market_meta.get("upTokenId") if direction == "UP" else market_meta.get("downTokenId") or "")
        bucket_start = int(state.get("bucketStartSec") or 0)
        window_end = int(state.get("windowEndMs") or 0)
        seconds_left = observer_base._finite(state.get("secondsLeft"))
        if signal_ask is None or not 0 < signal_ask <= 1 or not token_id or bucket_start <= 0 or window_end <= 0:
            return

        # Do not permanently dedupe by market.  A round key is unique and a new
        # round is only possible after 8781 reports the previous position flat.
        created_at_ms = observer_base._now_ms()
        round_id = f"{self.asset}:{market_id}:{created_at_ms}"
        key = f"R_PINNED_BINANCE_POLY_DIVERGENCE:{round_id}"
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
            "strategy": "R_PINNED_BINANCE_POLY_DIVERGENCE",
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
            if data.get("queued") is True or status in {"QUEUED", "PROCESSING", "SUBMITTED", "DUPLICATE_FENCE", "ACTIVE_ROUND_EXISTS"}:
                self.gateway_queued += int(data.get("queued") is True)
                self._lifecycle_at_mono = 0.0
        except Exception as exc:
            self.last_gateway_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        active = self._active_round()
        payload["signalGeneration"] = {
            "mode": "ROUND_SCOPED",
            "sameMarketMultipleRounds": True,
            "oneActiveRoundAtATime": True,
            "sameDirectionSignalsWhileOpen": "IGNORED",
            "exitSignal": "POLY_DIRECTION_REVERSAL",
            "rearm": "CONFIRMED_FLAT_ONLY",
            "activeRound": active,
        }
        gateway = payload.get("polyFastSignalGateway")
        if isinstance(gateway, dict):
            gateway.update(
                lifecycleSource="8781_DURABLE_LEDGER",
                sameMarketMultipleRounds=True,
                oneActiveRoundAtATime=True,
                roundScopedDedupe=True,
                exitEndpoint="/poly-exit-intent",
                exitAttempts=int(self.exit_attempts),
                exitConfirmedFlat=int(self.exit_confirmed_flat),
                lastExitResult=self.last_exit_result,
                lastExitError=self.last_exit_error,
            )
        return payload


class PolyFastSignalRuntimeV5(v4.PolyFastSignalRuntimeV4):
    def __init__(self) -> None:
        self.observer = v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: LifecycleSignalEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V5_LIFECYCLE"
        payload["architecture"].update(
            lifecycleOwner="8781_DURABLE+8792_SIGNAL",
            sameMarketMultipleRounds=True,
            oneActiveRoundAtATime=True,
            rearmRequiresConfirmedFlat=True,
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV5


def main() -> int:
    runtime = PolyFastSignalRuntimeV5()
    runtime.start()
    handler = type("PolyFastSignalV5Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V5 listening on http://{HOST}:{PORT}/state; lifecycle=one-active-round; exit=Poly-direction-reversal; Echtgeld=8781-only",
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
