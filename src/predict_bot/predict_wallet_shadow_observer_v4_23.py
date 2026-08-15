from __future__ import annotations

import json
import os
from http.server import ThreadingHTTPServer
from typing import Any

import httpx

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_19 as v4_19
from . import predict_wallet_shadow_observer_v4_22 as v4_22
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .target_taker_live_execution_v4 import TargetTakerLiveConfig, TargetTakerLiveExecutor


VERSION = "PREDICT_WALLET_SHADOW_V0_28_ECHTGELD_INTENT_PRODUCER_V1"
ENGINE_URL = str(os.environ.get("PREDICT_ECHTGELD_ENGINE_URL") or "http://127.0.0.1:8781").rstrip("/")


class WalletShadowObserver(v4_22.WalletShadowObserver):
    """V4.22 research observer with embedded Echtgeld disabled.

    SIDE_ONLY / HAZARD_SIDE still produce their normal paper event. A newly
    created paper event is then copied once to the independent 8781 Echtgeld
    Engine as a durable TradeIntent. Venue access, credentials, arming, risk and
    order submission no longer live in this process.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self.echtgeld_intent_last: dict[str, Any] | None = None
        self.echtgeld_intent_sent = 0
        self.echtgeld_intent_errors = 0
        self._echtgeld_http = httpx.Client(
            timeout=httpx.Timeout(0.75, connect=0.25),
            trust_env=False,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        super().__init__(db_path, simulation_db_path)
        # V4.21 remains in the inheritance chain for its paper AutoBankroll and
        # schema compatibility. Force its embedded executor permanently PAUSED;
        # this subclass never calls that executor for event handling.
        with self.target_taker_live_runtime_lock:
            current = self.target_taker_live_config
            old = self.target_taker_live_executor
            safe = TargetTakerLiveConfig(
                mode="paper",
                venue=current.venue,
                notional_usdt=current.notional_usdt,
                cohort=current.cohort if current.cohort in public_side.COHORTS else public_side.SIDE_ONLY_COHORT,
                max_price_drift=current.max_price_drift,
            )
            self.target_taker_live_config = safe
            self.target_taker_live_startup_config = safe
            self.target_taker_live_executor = TargetTakerLiveExecutor(safe)
            old.close()

    def stop(self) -> None:
        try:
            self._echtgeld_http.close()
        finally:
            super().stop()

    def update_target_taker_live_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(
            "Embedded Target Taker Echtgeld is retired on v4.23. Use the standalone Echtgeld Engine on port 8781."
        )

    def _publish_echtgeld_intent(
        self,
        cohort: str,
        event: dict[str, Any],
        decision: dict[str, Any],
        *,
        now_ms: int,
    ) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict) or self.market_id is None:
            self.echtgeld_intent_errors += 1
            self.echtgeld_intent_last = {
                "ok": False,
                "status": "NO_PUBLIC_SNAPSHOT",
                "cohort": cohort,
                "marketId": self.market_id,
                "asOfMs": int(now_ms),
            }
            return
        event_id = str(event.get("id") or "").strip()
        signal_id = event_id or f"{cohort}:{int(self.market_id)}:{int(now_ms)}"
        payload = {
            "intentId": signal_id,
            "dedupeKey": f"{public_side.VERSION}:{cohort}:{int(self.market_id)}",
            "producerVersion": VERSION,
            "strategy": public_side.VERSION,
            "cohort": cohort,
            "marketId": int(self.market_id),
            "signalId": signal_id,
            "createdAtMs": int(event.get("decisionAtMs") or now_ms),
            "decision": decision,
            "snapshot": dict(snapshot),
        }
        try:
            encoded = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
            response = self._echtgeld_http.post(f"{ENGINE_URL}/intent", content=encoded)
            try:
                body = response.json()
            except Exception:
                body = {"raw": response.text[:300]}
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {str(body)[:300]}")
            self.echtgeld_intent_sent += 1
            self.echtgeld_intent_last = {
                "ok": True,
                "status": str(body.get("status") or "ACCEPTED"),
                "accepted": bool(body.get("accepted")),
                "queued": bool(body.get("queued")),
                "intentId": signal_id,
                "cohort": cohort,
                "marketId": int(self.market_id),
                "engineUrl": ENGINE_URL,
                "asOfMs": int(now_ms),
            }
        except Exception as exc:
            # Never retry the same event from this observer. A lost producer-to-
            # engine handoff is safer than replaying a stale Echtgeld signal.
            self.echtgeld_intent_errors += 1
            self.echtgeld_intent_last = {
                "ok": False,
                "status": "ENGINE_UNREACHABLE",
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "intentId": signal_id,
                "cohort": cohort,
                "marketId": int(self.market_id),
                "engineUrl": ENGINE_URL,
                "retry": False,
                "asOfMs": int(now_ms),
            }

    def _execute_public_side(
        self,
        cohort: str,
        decision: dict[str, Any],
        hazard: dict[str, Any] | None,
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = self.public_side_states[cohort]
        before = state.get("event")

        # Explicitly call the pure paper implementation from v4.19. This skips
        # v4.21's embedded venue executor while preserving the frozen strategy's
        # exact one-event-per-market paper semantics.
        v4_19.WalletShadowObserver._execute_public_side(
            self,
            cohort,
            decision,
            hazard,
            snapshot_ns=snapshot_ns,
            now_ms=now_ms,
        )
        event = state.get("event")
        if before is not None or not isinstance(event, dict):
            return

        # V4.21 used an AutoBankroll mixin above v4.20. Since this method
        # deliberately bypasses that MRO live hook, mirror the paper overlay here.
        if cohort == public_side.SIDE_ONLY_COHORT and getattr(self, "auto_bankroll_schema_ready", False):
            self._mirror_auto_bankroll_event(event)

        self._publish_echtgeld_intent(cohort, event, decision, now_ms=now_ms)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["targetTakerEchtgeldProducerV1"] = {
            "version": VERSION,
            "engineUrl": ENGINE_URL,
            "embeddedLiveDisabled": True,
            "producerOnly": True,
            "liveCredentialsUsedHere": False,
            "sent": self.echtgeld_intent_sent,
            "errors": self.echtgeld_intent_errors,
            "last": self.echtgeld_intent_last,
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["paperOnly"] = True
        payload["liveOrdersAffected"] = False
        payload["targetTakerEchtgeldProducerV1"] = {
            "engineUrl": ENGINE_URL,
            "embeddedLiveDisabled": True,
            "producerOnly": True,
            "sent": self.echtgeld_intent_sent,
            "errors": self.echtgeld_intent_errors,
            "last": self.echtgeld_intent_last,
        }
        return payload


class _Handler(v4_22._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_23Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"EchtgeldEngine={ENGINE_URL}; embeddedLive=false; producerOnly=true; "
        "paperResearchCanRestartWithoutRestartingEchtgeldEngine=true",
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
