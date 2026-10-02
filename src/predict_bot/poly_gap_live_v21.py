from __future__ import annotations

import os
import time
from typing import Any

import httpx

from . import poly_gap_live as base
from .binance_exact_market import DEFAULT_BOUNDARY_SKEW_MS, DEFAULT_WINDOW_MS
from .poly_gap_live_v20 import RolloverRaceSafeSharedMarketPolyGapLiveEngine


LIGHTWEIGHT_REFERENCE_URL = os.environ.get(
    "PREDICT_POLY_GAP_LIVE_BINANCE_REFERENCE_URL",
    "http://127.0.0.1:8766/api/binance-market-reference",
)
LIGHTWEIGHT_REFERENCE_RETRY_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_REFERENCE_RETRY_MS", "250")),
)


class LightweightSharedMarketPolyGapLiveEngine(RolloverRaceSafeSharedMarketPolyGapLiveEngine):
    """V21: obtain current Binance identity from a tiny local-only 8766 endpoint.

    V19/V20 fetched the full ``/api/realtime`` dashboard payload just to obtain
    market metadata.  That endpoint also builds microstructure, realtime and
    observer state and can exceed the dedicated live client's short timeout under
    load.  A local reference failure then sent Live back to independent signed
    market/list discovery, recreating WAITING_BINANCE_CURRENT_MARKET even while
    the dashboard trajectory was healthy.

    V21 reads only ``/api/binance-market-reference`` using a dedicated httpx
    client with ``trust_env=False`` so localhost traffic never depends on proxy
    environment settings.  The response contains no credentials and is validated
    against the exact current five-minute bucket before use.  V18 direct exact
    discovery remains a fail-safe fallback; nearest-window fallback stays banned.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._local_market_http = httpx.Client(
            timeout=httpx.Timeout(1.0, connect=0.2),
            trust_env=False,
        )
        self.local_reference_last_latency_ms: float | None = None
        self.local_reference_last_collector_status: str | None = None

    def stop(self) -> None:
        try:
            self._local_market_http.close()
        except Exception:
            pass
        super().stop()

    @staticmethod
    def _lightweight_reference_cache(
        payload: dict[str, Any],
        *,
        target_start_ms: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        reference = payload.get("marketReference")
        if not isinstance(reference, dict):
            return None, "8766 lightweight marketReference unavailable"
        try:
            market_id = int(reference.get("market_id") or 0)
            topic_id = int(reference.get("topic_id") or 0)
            start_ms = int(reference.get("start_ms") or 0)
            end_ms = int(reference.get("end_ms") or 0)
            fee_bps = int(reference.get("fee_bps") or 200)
        except (TypeError, ValueError):
            return None, "8766 lightweight reference contains invalid numeric fields"
        up_token = str(reference.get("up_token_id") or "")
        down_token = str(reference.get("down_token_id") or "")
        expected_end_ms = int(target_start_ms) + DEFAULT_WINDOW_MS
        if market_id <= 0 or topic_id <= 0:
            return None, "8766 lightweight market/topic ID missing"
        if not up_token or not down_token or up_token == down_token:
            return None, "8766 lightweight explicit UP/DOWN token IDs missing or ambiguous"
        if abs(start_ms - int(target_start_ms)) > DEFAULT_BOUNDARY_SKEW_MS:
            return None, f"8766 lightweight start mismatch: {start_ms} != {target_start_ms}"
        if abs(end_ms - expected_end_ms) > DEFAULT_BOUNDARY_SKEW_MS:
            return None, f"8766 lightweight end mismatch: {end_ms} != {expected_end_ms}"
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
            now_mono + LIGHTWEIGHT_REFERENCE_RETRY_MS / 1000.0
        )
        self.local_reference_attempts += 1
        self.local_reference_last_at_ms = base._now_ms()
        started = time.monotonic()
        try:
            response = self._local_market_http.get(LIGHTWEIGHT_REFERENCE_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.local_reference_last_latency_ms = max(
                0.0, (time.monotonic() - started) * 1000.0
            )
            self.local_reference_errors += 1
            self.local_reference_last_error = (
                f"8766 lightweight Binance reference request failed: {str(exc)[:300]}"
            )
            return None

        self.local_reference_last_latency_ms = max(
            0.0, (time.monotonic() - started) * 1000.0
        )
        if not isinstance(payload, dict):
            self.local_reference_rejects += 1
            self.local_reference_last_error = "8766 lightweight response is not an object"
            return None
        self.local_reference_last_collector_status = str(payload.get("collectorStatus") or "")
        cache, reason = self._lightweight_reference_cache(
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
        self.local_reference_last_source = "8766_LIGHTWEIGHT_EXACT_CURRENT_REFERENCE"
        return cache

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V21"
        shared = payload.get("binanceSharedMarketReference")
        if isinstance(shared, dict):
            shared["url"] = LIGHTWEIGHT_REFERENCE_URL
            shared["retryMs"] = LIGHTWEIGHT_REFERENCE_RETRY_MS
            shared["endpointMode"] = "LIGHTWEIGHT_MARKET_METADATA_ONLY"
            shared["usesFullRealtimeDashboardPayload"] = False
            shared["localHttpTrustEnv"] = False
            shared["lastLatencyMs"] = self.local_reference_last_latency_ms
            shared["lastCollectorStatus"] = self.local_reference_last_collector_status
            shared["fallbackDirectExactDiscovery"] = True
            shared["nearestWrongWindowFallback"] = False
        return payload


base.PolyGapLiveEngine = LightweightSharedMarketPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
