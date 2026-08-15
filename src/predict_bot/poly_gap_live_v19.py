from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .binance_exact_market import DEFAULT_BOUNDARY_SKEW_MS, DEFAULT_WINDOW_MS, bucket_start_ms
from .poly_gap_live_v18 import DeepExactBinanceDiscoveryPolyGapLiveEngine


LOCAL_BINANCE_REFERENCE_URL = os.environ.get(
    "PREDICT_POLY_GAP_LIVE_BINANCE_REFERENCE_URL",
    "http://127.0.0.1:8766/api/realtime",
)
LOCAL_REFERENCE_RETRY_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_REFERENCE_RETRY_MS", "250")),
)


class SharedCollectorBinanceMarketPolyGapLiveEngine(DeepExactBinanceDiscoveryPolyGapLiveEngine):
    """V19: use 8766's verified current Binance market identity before REST discovery.

    8766 and 8769 previously rediscovered Binance Prediction markets independently.
    That allowed the dashboard/test trajectory to be healthy while the dedicated
    live executor sat in WAITING_BINANCE_CURRENT_MARKET because its own signed
    market/list request timed out.  V19 makes the already-running local collector
    the primary metadata source while keeping the execution client completely
    separate for order books, quotes, positions and real orders.

    Safety properties:
    - the local reference must match the exact current five-minute bucket;
    - market/topic IDs and explicit UP/DOWN token IDs must be complete;
    - the 8766 latest trajectory must report the same market ID;
    - any missing/mismatched local reference falls back to V18's strict exact
      Binance discovery; nearest previous/future windows are still forbidden;
    - the dedicated live prefetch thread no longer performs duplicate signed
      market/list scans, removing its ability to starve current-market lookup.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.local_reference_attempts = 0
        self.local_reference_hits = 0
        self.local_reference_rejects = 0
        self.local_reference_errors = 0
        self.local_reference_last_at_ms: int | None = None
        self.local_reference_last_market_id: int | None = None
        self.local_reference_last_error: str | None = None
        self.local_reference_last_source: str | None = None
        self._next_local_reference_attempt_mono = 0.0

    @staticmethod
    def _reference_cache(
        payload: dict[str, Any],
        *,
        target_start_ms: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        reference = payload.get("binanceMarketReference")
        if not isinstance(reference, dict):
            return None, "8766 binanceMarketReference unavailable"
        latest = payload.get("latest")
        if not isinstance(latest, dict):
            return None, "8766 latest Binance trajectory unavailable"
        try:
            market_id = int(reference.get("market_id") or 0)
            topic_id = int(reference.get("topic_id") or 0)
            start_ms = int(reference.get("start_ms") or 0)
            end_ms = int(reference.get("end_ms") or 0)
            latest_market_id = int(latest.get("market_id") or 0)
            fee_bps = int(reference.get("fee_bps") or 200)
        except (TypeError, ValueError):
            return None, "8766 Binance market reference contains invalid numeric fields"
        up_token = str(reference.get("up_token_id") or "")
        down_token = str(reference.get("down_token_id") or "")
        expected_end_ms = int(target_start_ms) + DEFAULT_WINDOW_MS
        if market_id <= 0 or topic_id <= 0:
            return None, "8766 Binance market/topic ID missing"
        if not up_token or not down_token or up_token == down_token:
            return None, "8766 explicit UP/DOWN token IDs missing or ambiguous"
        if abs(start_ms - int(target_start_ms)) > DEFAULT_BOUNDARY_SKEW_MS:
            return None, (
                f"8766 reference start mismatch: {start_ms} != {target_start_ms}"
            )
        if abs(end_ms - expected_end_ms) > DEFAULT_BOUNDARY_SKEW_MS:
            return None, (
                f"8766 reference end mismatch: {end_ms} != {expected_end_ms}"
            )
        if latest_market_id != market_id:
            return None, (
                f"8766 trajectory/reference market mismatch: {latest_market_id} != {market_id}"
            )
        return {
            "market_id": market_id,
            "topic_id": topic_id,
            "up_token_id": up_token,
            "down_token_id": down_token,
            "fee_rate_bps": fee_bps,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }, None

    def _local_current_cache(self, target_start_ms: int) -> dict[str, Any] | None:
        now_mono = time.monotonic()
        if now_mono < self._next_local_reference_attempt_mono:
            return None
        self._next_local_reference_attempt_mono = (
            now_mono + LOCAL_REFERENCE_RETRY_MS / 1000.0
        )
        self.local_reference_attempts += 1
        self.local_reference_last_at_ms = base._now_ms()
        try:
            response = self.http.get(LOCAL_BINANCE_REFERENCE_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.local_reference_errors += 1
            self.local_reference_last_error = (
                f"8766 Binance market reference request failed: {str(exc)[:300]}"
            )
            return None
        if not isinstance(payload, dict):
            self.local_reference_rejects += 1
            self.local_reference_last_error = "8766 realtime response is not an object"
            return None
        cache, reason = self._reference_cache(
            payload,
            target_start_ms=int(target_start_ms),
        )
        if cache is None:
            self.local_reference_rejects += 1
            self.local_reference_last_error = reason
            return None
        self.local_reference_hits += 1
        self.local_reference_last_market_id = int(cache["market_id"])
        self.local_reference_last_error = None
        self.local_reference_last_source = "8766_VERIFIED_COLLECTOR_REFERENCE"
        return cache

    def _binance_prefetch_loop(self) -> None:
        """Do not duplicate 8766's signed Binance future-market prefetch.

        V18's background prefetch shared a lock with current lookup.  A slow or
        timed-out future scan could therefore prevent the live executor from even
        attempting the current market.  8766 already owns future-market prefetch,
        so V19 leaves this thread intentionally idle.  The normal _prime_market
        fallback still performs strict direct current discovery if 8766 is down.
        """
        while not self.stop_event.wait(1.0):
            pass

    def _prime_market(self, force: bool = False) -> dict[str, Any] | None:
        now_ms = self._binance_server_now_ms()
        target_start_ms = bucket_start_ms(now_ms)

        with self.lock:
            cached = dict(self.market_cache or {})
        if self._cache_matches_start(cached, target_start_ms):
            return cached

        local_cache = self._local_current_cache(target_start_ms)
        if local_cache is not None:
            with self.lock:
                self.market_cache = dict(local_cache)
            market_id = int(local_cache["market_id"])
            if self.halted_market_id is not None and self.halted_market_id != market_id:
                self.halted_market_id = None
                self.halted_reason = None
            self.binance_last_current_error = None
            return dict(local_cache)

        # The local collector is preferred, not blindly trusted.  If it is not
        # ready or its exact identity does not match the wall/server bucket, keep
        # V18's direct exact-window fallback.
        return super()._prime_market(force=force)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V19"
        prefetch = payload.get("binanceMarketPrefetch")
        if isinstance(prefetch, dict):
            prefetch["dedicatedLivePrefetchEnabled"] = False
            prefetch["shared8766CollectorPrefetchPreferred"] = True
            prefetch["reasonV19"] = (
                "avoid duplicate signed market discovery and prevent future-prefetch lock starvation"
            )
        payload["binanceSharedMarketReference"] = {
            "enabled": True,
            "url": LOCAL_BINANCE_REFERENCE_URL,
            "retryMs": LOCAL_REFERENCE_RETRY_MS,
            "attempts": int(self.local_reference_attempts),
            "hits": int(self.local_reference_hits),
            "rejects": int(self.local_reference_rejects),
            "errors": int(self.local_reference_errors),
            "lastAtMs": self.local_reference_last_at_ms,
            "lastMarketId": self.local_reference_last_market_id,
            "lastError": self.local_reference_last_error,
            "lastSource": self.local_reference_last_source,
            "requiresExactCurrentFiveMinuteWindow": True,
            "requiresSame8766TrajectoryMarketId": True,
            "directV18ExactDiscoveryFallback": True,
            "nearestWrongWindowFallback": False,
            "executionOrdersStillUseDedicated8769Client": True,
        }
        return payload


base.PolyGapLiveEngine = SharedCollectorBinanceMarketPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
