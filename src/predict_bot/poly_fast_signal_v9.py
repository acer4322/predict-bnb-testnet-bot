from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v6 as v6
from . import poly_fast_signal_v8 as v8

ASSETS = v8.ASSETS
HOST = v8.HOST
PORT = v8.PORT
ROOT = v8.ROOT
DB_PATH = v8.DB_PATH
ECHTGELD_URL = v6.ECHTGELD_URL
SAFE_REJECT_RETRY_SECONDS = 1.0


class SafeRejectedEntryRearmEvaluator(v8.BucketAlignedGapSignalEvaluator):
    """V8 plus explicit terminal-intent tracking for safe pre-venue rejection retries.

    A queued entry is never duplicated while 8781 is still processing it.  If the
    durable intent later becomes REJECTED and there is no evidence of a venue
    write (no vendor order id, shares, or submitted amount), the asset may rearm
    only after a short cooldown and a newer embedded Binance book observation.
    AMBIGUOUS/SUBMITTED or any venue-write evidence always remains locked.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_entry: dict[str, Any] | None = None
        self.safe_reject_rearms = 0
        self.safe_reject_waits = 0
        self.unsafe_retry_blocks = 0
        self.last_entry_terminal: dict[str, Any] | None = None

    def _current_binance_observed_at_ms(self) -> int:
        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        b = observer_base._record(state.get("binance"))
        return int(b.get("observedAtMs") or 0)

    def _intent_status(self, intent_id: str) -> dict[str, Any] | None:
        try:
            response = self.intent_http.get(
                f"{ECHTGELD_URL}/poly-intent-status?intentId={quote(intent_id, safe='')}"
            )
            data = response.json() if response.content else {}
            return data if isinstance(data, dict) else None
        except Exception as exc:
            self.last_gateway_error = f"intent-status: {type(exc).__name__}: {str(exc)[:300]}"
            return None

    @staticmethod
    def _venue_write_evidence(status: dict[str, Any]) -> bool:
        result = status.get("result") if isinstance(status.get("result"), dict) else {}
        round_row = status.get("round") if isinstance(status.get("round"), dict) else {}
        vendor = result.get("vendorOrderId") or result.get("vendor_order_id") or round_row.get("vendor_order_id")
        shares = observer_base._finite(result.get("shares"))
        submitted = observer_base._finite(result.get("submittedUsdt"))
        exchange_status = str(result.get("exchangeStatus") or "").upper()
        return bool(vendor or (shares is not None and shares > 0) or (submitted is not None and submitted > 0) or exchange_status in {"SUBMITTED", "FILLED", "PARTIALLY_FILLED", "PROCESSING"})

    def _pending_entry_allows_new_attempt(self) -> bool:
        pending = self._pending_entry
        if not isinstance(pending, dict):
            return True
        intent_id = str(pending.get("intentId") or "")
        status = self._intent_status(intent_id)
        if not isinstance(status, dict):
            return False
        intent_status = str(status.get("intentStatus") or status.get("status") or "").upper()
        order_status = str(status.get("orderStatus") or "").upper()
        effective = order_status or intent_status
        self.last_entry_terminal = {
            "intentId": intent_id,
            "intentStatus": intent_status or None,
            "orderStatus": order_status or None,
            "effectiveStatus": effective or None,
            "venueWriteEvidence": self._venue_write_evidence(status),
        }
        if effective in {"SUBMITTED", "AMBIGUOUS", "ATTEMPTING", "QUEUED", "PROCESSING"}:
            if effective in {"SUBMITTED", "AMBIGUOUS"}:
                self.unsafe_retry_blocks += 1
            return False
        if effective != "REJECTED":
            return False
        if self._venue_write_evidence(status):
            self.unsafe_retry_blocks += 1
            return False

        now = time.monotonic()
        if now - float(pending.get("attemptMono") or 0.0) < SAFE_REJECT_RETRY_SECONDS:
            self.safe_reject_waits += 1
            return False
        current_book_ms = self._current_binance_observed_at_ms()
        attempted_book_ms = int(pending.get("binanceObservedAtMs") or 0)
        if current_book_ms <= attempted_book_ms:
            self.safe_reject_waits += 1
            return False

        self.safe_reject_rearms += 1
        self._pending_entry = None
        self._lifecycle_cache = None
        self._lifecycle_at_mono = 0.0
        return True

    def _forward_gap_entry(self, market: dict[str, Any], evaluation: dict[str, Any]) -> None:
        if not self._pending_entry_allows_new_attempt():
            return
        before_attempts = int(self.gateway_attempts)
        before_result = self.last_gateway_result
        book_ms = self._current_binance_observed_at_ms()
        super()._forward_gap_entry(market, evaluation)
        if int(self.gateway_attempts) <= before_attempts:
            return
        result = self.last_gateway_result if isinstance(self.last_gateway_result, dict) else {}
        intent_id = str(result.get("intentId") or result.get("signalId") or "")
        # The POST response normally carries the round id but not always intentId;
        # infer it from the durable gateway result only when explicitly present.
        if not intent_id and isinstance(before_result, dict) and self.last_gateway_result is before_result:
            return
        # Recover the just-created intent id from the response when possible; if
        # absent, lifecycle remains the fallback and we do not invent an id.
        if intent_id:
            self._pending_entry = {
                "intentId": intent_id,
                "attemptMono": time.monotonic(),
                "binanceObservedAtMs": book_ms,
            }

    def _evaluate_and_forward(self) -> None:
        # If the last queued entry reached a safe REJECTED terminal state, clear
        # the local latch before the normal V8 lifecycle/evaluation path runs.
        self._pending_entry_allows_new_attempt()
        return super()._evaluate_and_forward()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["safeRejectedEntryRearm"] = {
            "enabled": True,
            "policy": "REJECTED_NO_VENUE_WRITE + NEW_BINANCE_BOOK + 1S_COOLDOWN",
            "cooldownMs": int(SAFE_REJECT_RETRY_SECONDS * 1000),
            "rearms": int(self.safe_reject_rearms),
            "waits": int(self.safe_reject_waits),
            "unsafeRetryBlocks": int(self.unsafe_retry_blocks),
            "pendingEntry": self._pending_entry,
            "lastTerminal": self.last_entry_terminal,
            "ambiguousNeverRetried": True,
        }
        return payload


class PolyFastSignalRuntimeV9(v8.PolyFastSignalRuntimeV8):
    def __init__(self) -> None:
        self.observer = v8.v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: SafeRejectedEntryRearmEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V9_SAFE_REJECT_REARM"
        payload["architecture"].update(
            safeRejectedEntryRearm=True,
            safeRetryRequiresNoVenueWrite=True,
            safeRetryRequiresNewBinanceBook=True,
            ambiguousEntryNeverRetried=True,
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV9


def main() -> int:
    runtime = PolyFastSignalRuntimeV9()
    runtime.start()
    handler = type("PolyFastSignalV9Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V9 listening on http://{HOST}:{PORT}/state; "
        f"safe-reject-rearm=true; ambiguous-never-retry=true; Echtgeld=8781-only",
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
