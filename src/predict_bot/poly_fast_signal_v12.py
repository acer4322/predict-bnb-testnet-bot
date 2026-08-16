from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v11 as v11

ASSETS = v11.ASSETS
HOST = v11.HOST
PORT = v11.PORT
ROOT = v11.ROOT
DB_PATH = v11.DB_PATH


class PostRejectDiagnosticEvaluator(v11.TakeProfitSignalEvaluator):
    """V11 plus diagnostics for what happens after a safe rejected-entry rearm.

    This wrapper is intentionally observation-only. It does not change admission,
    retry, take-profit, reversal-exit, lifecycle, or venue-write behavior.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._post_reject_active = False
        self._post_reject_market_id: int | None = None
        self._post_reject_rearmed_at_ms: int | None = None
        self._post_reject_rearm_count_seen = int(self.safe_reject_rearms)
        self._post_reject_evaluations = 0
        self._post_reject_entry_ready = 0
        self._post_reject_new_intents = 0
        self._post_reject_blocked_by: dict[str, int] = {}
        self._post_reject_last_evaluation: dict[str, Any] | None = None
        self._post_reject_last_new_intent: dict[str, Any] | None = None

    def _pending_entry_allows_new_attempt(self) -> bool:
        before = int(self.safe_reject_rearms)
        allowed = super()._pending_entry_allows_new_attempt()
        after = int(self.safe_reject_rearms)
        if after > before:
            terminal = self.last_entry_terminal if isinstance(self.last_entry_terminal, dict) else {}
            intent_id = str(terminal.get("intentId") or "")
            market_id = None
            parts = intent_id.split(":")
            if len(parts) >= 4:
                try:
                    market_id = int(parts[2])
                except Exception:
                    market_id = None
            self._post_reject_active = True
            self._post_reject_market_id = market_id
            self._post_reject_rearmed_at_ms = observer_base._now_ms()
            self._post_reject_rearm_count_seen = after
            self._post_reject_evaluations = 0
            self._post_reject_entry_ready = 0
            self._post_reject_new_intents = 0
            self._post_reject_blocked_by = {}
            self._post_reject_last_evaluation = None
            self._post_reject_last_new_intent = None
        return allowed

    def _gap_evaluation(self, market: dict[str, Any], poly_state: dict[str, Any]) -> dict[str, Any]:
        result = super()._gap_evaluation(market, poly_state)
        if self._post_reject_active:
            market_id = int(market.get("market_id") or 0)
            if self._post_reject_market_id is None:
                self._post_reject_market_id = market_id or None
            if self._post_reject_market_id == market_id:
                self._post_reject_evaluations += 1
                state = str(result.get("state") or "UNKNOWN")
                if result.get("allowed") is True or state == "ENTRY_READY":
                    self._post_reject_entry_ready += 1
                else:
                    self._post_reject_blocked_by[state] = int(self._post_reject_blocked_by.get(state, 0)) + 1
                self._post_reject_last_evaluation = {
                    "atMs": observer_base._now_ms(),
                    "marketId": market_id,
                    "state": state,
                    "allowed": bool(result.get("allowed") is True),
                    "reason": result.get("reason"),
                    "direction": result.get("direction"),
                    "polySelected": result.get("polySelected"),
                    "binanceSelectedAsk": result.get("binanceSelectedAsk"),
                    "edge": result.get("edge"),
                    "elapsedSeconds": result.get("elapsedSeconds"),
                }
            elif market_id > 0 and self._post_reject_market_id is not None and market_id != self._post_reject_market_id:
                # Keep the completed diagnostic snapshot visible after rollover,
                # but stop attributing new-market evaluations to the old reject.
                self._post_reject_active = False
        return result

    def _forward_gap_entry(self, market: dict[str, Any], evaluation: dict[str, Any]) -> None:
        before_attempts = int(self.gateway_attempts)
        super()._forward_gap_entry(market, evaluation)
        if self._post_reject_active and int(self.gateway_attempts) > before_attempts:
            market_id = int(market.get("market_id") or 0)
            if self._post_reject_market_id == market_id:
                self._post_reject_new_intents += 1
                result = self.last_gateway_result if isinstance(self.last_gateway_result, dict) else {}
                self._post_reject_last_new_intent = {
                    "atMs": observer_base._now_ms(),
                    "marketId": market_id,
                    "status": result.get("status"),
                    "queued": result.get("queued"),
                    "intentId": result.get("intentId") or result.get("signalId"),
                    "roundId": result.get("roundId"),
                }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["postRejectRearm"] = {
            "enabled": True,
            "observationOnly": True,
            "active": bool(self._post_reject_active),
            "lastRejectedMarketId": self._post_reject_market_id,
            "rearmedAtMs": self._post_reject_rearmed_at_ms,
            "safeRearmCountAtStart": int(self._post_reject_rearm_count_seen),
            "evaluationsAfterRearm": int(self._post_reject_evaluations),
            "entryReadyAfterRearm": int(self._post_reject_entry_ready),
            "newIntentAfterRearm": int(self._post_reject_new_intents),
            "blockedAfterRearmBy": dict(sorted(self._post_reject_blocked_by.items())),
            "lastEvaluationAfterRearm": self._post_reject_last_evaluation,
            "lastNewIntentAfterRearm": self._post_reject_last_new_intent,
        }
        return payload


class PolyFastSignalRuntimeV12(v11.PolyFastSignalRuntimeV11):
    def __init__(self) -> None:
        self.observer = v11.v10.v9.v8.v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: PostRejectDiagnosticEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V12_POST_REJECT_DIAGNOSTIC"
        payload["architecture"].update(postRejectRearmDiagnostic=True, postRejectDiagnosticObservationOnly=True)
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV12


def main() -> int:
    runtime = PolyFastSignalRuntimeV12()
    runtime.start()
    handler = type("PolyFastSignalV12Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V12 listening on http://{HOST}:{PORT}/state; "
        "post-reject diagnostic=enabled observation-only; V11 trading rules unchanged",
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
