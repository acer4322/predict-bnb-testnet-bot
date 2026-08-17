from __future__ import annotations

import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v2 as v2
from . import echtgeld_engine_v3 as v3
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .target_taker_live_execution_v5 import TargetTakerLiveConfig, TargetTakerLiveExecutor


VERSION = "ECHTGELD_ENGINE_V2_4310_BALANCE_PNL_STOP_LOSS_REDEEM_POLY_FAST_V1"
HOST = v3.HOST
PORT = v3.PORT
POLY_FAST_STRATEGY = "R_PINNED_BINANCE_POLY_DIVERGENCE"
POLY_FAST_SOURCE = "POLY_FAST_8792"
POLY_ASSETS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT"}
BINANCE_SYMBOL_ENV = "PREDICT_TARGET_TAKER_BINANCE_SYMBOL"


class UniversalEchtgeldExecutor(TargetTakerLiveExecutor):
    """Reuse the hardened 8781 Binance path while selecting symbol per intent.

    Echtgeld's worker is serialized.  The short environment override therefore
    cannot cross two venue writes, and it is restored immediately afterwards.
    EBM/Target-Taker intents without snapshot.asset keep the existing symbol.
    """

    _symbol_lock = threading.RLock()

    def execute(
        self,
        *,
        cohort: str,
        market_id: int,
        decision: dict[str, Any],
        snapshot: dict[str, Any],
        signal_id: str,
    ) -> dict[str, Any]:
        asset = str(snapshot.get("asset") or "").strip().upper()
        symbol = POLY_ASSETS.get(asset)
        if not symbol:
            return super().execute(
                cohort=cohort,
                market_id=market_id,
                decision=decision,
                snapshot=snapshot,
                signal_id=signal_id,
            )
        with self._symbol_lock:
            previous = os.environ.get(BINANCE_SYMBOL_ENV)
            os.environ[BINANCE_SYMBOL_ENV] = symbol
            try:
                return super().execute(
                    cohort=cohort,
                    market_id=market_id,
                    decision=decision,
                    snapshot=snapshot,
                    signal_id=signal_id,
                )
            finally:
                if previous is None:
                    os.environ.pop(BINANCE_SYMBOL_ENV, None)
                else:
                    os.environ[BINANCE_SYMBOL_ENV] = previous


class EchtgeldEngine(v3.EchtgeldEngine):
    """Current 8781 plus a narrow Poly Fast intent adapter.

    Poly Fast never receives credentials and never writes the venue. It submits
    a signal to this engine, which preserves the existing PAUSE/RESUME, durable
    intent/order fences, balance checks, stop-loss admission fences, signed
    MARKET/FOK execution, reconciliation and redeem lifecycle.
    """

    def __init__(
        self,
        db_path: Path | str = v1.DB_PATH,
        *,
        executor_factory: Callable[[TargetTakerLiveConfig], TargetTakerLiveExecutor] = UniversalEchtgeldExecutor,
        start_worker: bool = True,
        settlement_db_path: Path | str = v2.SETTLEMENT_DB_PATH,
        redeem_manager_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.poly_fast_intents_received = 0
        self.poly_fast_intents_accepted = 0
        self.poly_fast_last_intent: dict[str, Any] | None = None
        super().__init__(
            db_path,
            executor_factory=executor_factory,
            start_worker=start_worker,
            settlement_db_path=settlement_db_path,
            redeem_manager_factory=redeem_manager_factory,
        )

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise v1.EchtgeldEngineError("Poly Fast intent must be a JSON object")
        asset = str(raw.get("asset") or "").strip().upper()
        if asset not in POLY_ASSETS:
            raise v1.EchtgeldEngineError("Poly Fast asset must be BTC, ETH or BNB")
        side = str(raw.get("side") or "").strip().upper()
        if side not in {"UP", "DOWN"}:
            raise v1.EchtgeldEngineError("Poly Fast side must be UP or DOWN")
        market_id = int(v1._finite(raw.get("marketId")) or 0)
        created_at_ms = int(v1._finite(raw.get("createdAtMs")) or 0)
        bucket_start_sec = int(v1._finite(raw.get("bucketStartSec")) or 0)
        window_end_ms = int(v1._finite(raw.get("windowEndMs")) or 0)
        signal_ask = v1._finite(raw.get("signalAsk"))
        if market_id <= 0 or created_at_ms <= 0:
            raise v1.EchtgeldEngineError("Poly Fast marketId and createdAtMs are required")
        if bucket_start_sec <= 0 or window_end_ms <= bucket_start_sec * 1000:
            raise v1.EchtgeldEngineError("Poly Fast five-minute window metadata is invalid")
        if signal_ask is None or not 0 < signal_ask <= 1:
            raise v1.EchtgeldEngineError("Poly Fast signalAsk is invalid")
        supplied_strategy = str(raw.get("strategy") or POLY_FAST_STRATEGY).strip()
        if supplied_strategy != POLY_FAST_STRATEGY:
            raise v1.EchtgeldEngineError(f"unsupported Poly Fast strategy: {supplied_strategy}")

        self.poly_fast_intents_received += 1
        intent_id = str(raw.get("intentId") or f"poly-fast:{asset}:{market_id}:{side}").strip()
        signal_id = str(raw.get("signalId") or intent_id).strip()
        # Keep the mature V1 schema untouched.  The adapter uses the currently
        # selected Echtgeld cohort only as an execution/risk namespace; original
        # strategy/source/asset remain durable inside payload + snapshot.
        payload = {
            "intentId": intent_id,
            "signalId": signal_id,
            "dedupeKey": str(raw.get("dedupeKey") or f"{POLY_FAST_STRATEGY}:{asset}:{market_id}"),
            "strategy": public_side.VERSION,
            "cohort": self.config.cohort,
            "marketId": market_id,
            "createdAtMs": created_at_ms,
            "side": side,
            "signalAsk": float(signal_ask),
            "source": POLY_FAST_SOURCE,
            "sourceStrategy": POLY_FAST_STRATEGY,
            "asset": asset,
            "decision": {
                "decision": "TRADE",
                "side": side,
                "ask": float(signal_ask),
                "sourceStrategy": POLY_FAST_STRATEGY,
                "asset": asset,
            },
            "snapshot": {
                "market_id": market_id,
                "marketId": market_id,
                "bucket_start_sec": bucket_start_sec,
                "bucketStartSec": bucket_start_sec,
                "window_end_ms": window_end_ms,
                "windowEndMs": window_end_ms,
                "seconds_left": raw.get("secondsLeft"),
                "secondsLeft": raw.get("secondsLeft"),
                "sampled_at_ms": created_at_ms,
                "sampledAtMs": created_at_ms,
                "asset": asset,
                "symbol": POLY_ASSETS[asset],
                "source": POLY_FAST_SOURCE,
                "sourceStrategy": POLY_FAST_STRATEGY,
                "polySelected": raw.get("polySelected"),
                "binanceSelectedMid": raw.get("binanceSelectedMid"),
                "divergenceGap": raw.get("divergenceGap"),
            },
        }
        self.poly_fast_last_intent = {
            "intentId": intent_id,
            "asset": asset,
            "marketId": market_id,
            "side": side,
            "signalAsk": float(signal_ask),
            "createdAtMs": created_at_ms,
        }
        result = super().submit_intent(payload)
        if result.get("accepted") is True:
            self.poly_fast_intents_accepted += 1
        result["polyFast"] = True
        result["asset"] = asset
        result["sourceStrategy"] = POLY_FAST_STRATEGY
        return result

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["polyFastGateway"] = {
            "enabled": True,
            "source": POLY_FAST_SOURCE,
            "strategy": POLY_FAST_STRATEGY,
            "assets": sorted(POLY_ASSETS),
            "endpoint": "/poly-intent",
            "received": int(self.poly_fast_intents_received),
            "accepted": int(self.poly_fast_intents_accepted),
            "lastIntent": dict(self.poly_fast_last_intent or {}),
            "venueOwner": "8781_ONLY",
            "usesExistingPauseResume": True,
            "usesExistingStopLossAdmissionFence": True,
            "usesExistingBalanceChecks": True,
            "usesExistingDurableDedupe": True,
            "usesExistingSignedMarketFok": True,
            "usesExistingReconciliation": True,
            "dynamicSymbols": dict(POLY_ASSETS),
            "producerPort": 8792,
        }
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["polyFastGatewayEnabled"] = True
        payload["polyFastEndpoint"] = "/poly-intent"
        return payload


class _Handler(v3._Handler):
    engine: EchtgeldEngine

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/poly-intent":
            return super().do_POST()
        try:
            payload = self._body()
            result = self.engine.submit_poly_fast_intent(payload)
            self._send(202 if result.get("queued") else 200, result)
        except v1.EchtgeldEngineError as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV4Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "poly-fast-gateway=/poly-intent; venue-owner=8781-only; "
        "stopLoss=existing-durable-gate; redeem=existing-4310-lifecycle",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
