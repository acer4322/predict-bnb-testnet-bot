from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .binance_exact_market import DEFAULT_BOUNDARY_SKEW_MS, DEFAULT_WINDOW_MS, bucket_start_ms
from .poly_gap_live_v19 import SharedCollectorBinanceMarketPolyGapLiveEngine


class RolloverRaceSafeSharedMarketPolyGapLiveEngine(SharedCollectorBinanceMarketPolyGapLiveEngine):
    """V20: remove the 8766 metadata/latest-snapshot rollover race.

    V19 required ``/api/realtime.binanceMarketReference.market_id`` to equal
    ``/api/realtime.latest.market_id`` in the same HTTP response.  Those values
    are updated by different 8766 paths: the rollover thread can publish the new
    exact current market before the collector finishes the first startPrice/book
    cycle and replaces ``latest_snapshot``.  During that legitimate transition,
    V19 rejected the correct new metadata and could remain on an old local cache
    if its direct Binance fallback timed out.

    V20 treats 8766's exact-current market reference as identity authority and
    treats trajectory IDs as diagnostics/readiness only.  This is safe because:
    - reference start/end must still equal the exact current five-minute bucket;
    - market/topic IDs and explicit, distinct UP/DOWN token IDs are mandatory;
    - the live executor still fetches its own direct Binance order book before
      entry and retains V14's exact Poly/Binance market binding/warmup gate;
    - nearest previous/future market fallback remains forbidden.

    V20 also clears an expired/stale ``market_cache`` immediately when its start
    bucket no longer matches the current bucket, so the dashboard cannot display
    an old market ID while status says WAITING_BINANCE_CURRENT_MARKET.
    """

    @staticmethod
    def _reference_cache(
        payload: dict[str, Any],
        *,
        target_start_ms: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        reference = payload.get("binanceMarketReference")
        if not isinstance(reference, dict):
            return None, "8766 binanceMarketReference unavailable"
        try:
            market_id = int(reference.get("market_id") or 0)
            topic_id = int(reference.get("topic_id") or 0)
            start_ms = int(reference.get("start_ms") or 0)
            end_ms = int(reference.get("end_ms") or 0)
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
            return None, f"8766 reference start mismatch: {start_ms} != {target_start_ms}"
        if abs(end_ms - expected_end_ms) > DEFAULT_BOUNDARY_SKEW_MS:
            return None, f"8766 reference end mismatch: {end_ms} != {expected_end_ms}"

        # Trajectory IDs are deliberately not a hard identity gate.  8766's
        # rollover thread updates COLLECTOR.market before latest_snapshot and
        # mRealtime necessarily receive their first new-market book.  Preserve
        # both values in diagnostics so a persistent disagreement is observable.
        latest = payload.get("latest")
        latest_market_id: int | None = None
        if isinstance(latest, dict):
            try:
                latest_market_id = int(latest.get("market_id") or 0) or None
            except (TypeError, ValueError):
                latest_market_id = None
        m_realtime = payload.get("mRealtime")
        realtime_market_id: int | None = None
        if isinstance(m_realtime, dict):
            try:
                realtime_market_id = int(m_realtime.get("marketId") or 0) or None
            except (TypeError, ValueError):
                realtime_market_id = None

        return {
            "market_id": market_id,
            "topic_id": topic_id,
            "up_token_id": up_token,
            "down_token_id": down_token,
            "fee_rate_bps": fee_bps,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "_8766_latest_market_id": latest_market_id,
            "_8766_m_realtime_market_id": realtime_market_id,
        }, None

    def _local_current_cache(self, target_start_ms: int) -> dict[str, Any] | None:
        cache = super()._local_current_cache(target_start_ms)
        if cache is not None:
            self.local_reference_last_source = "8766_EXACT_CURRENT_MARKET_REFERENCE_V20"
        return cache

    def _prime_market(self, force: bool = False) -> dict[str, Any] | None:
        now_ms = self._binance_server_now_ms()
        target_start_ms = bucket_start_ms(now_ms)

        # Never retain/display a previous bucket as the current market.  V14's
        # binding already prevented entry, but the stale ID made diagnosis
        # misleading and could mask whether local-reference promotion occurred.
        with self.lock:
            cached = dict(self.market_cache or {})
            if cached and not self._cache_matches_start(cached, target_start_ms):
                self.market_cache = None
                cached = {}

        return super()._prime_market(force=force)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V20"
        shared = payload.get("binanceSharedMarketReference")
        if isinstance(shared, dict):
            shared["requiresSame8766TrajectoryMarketId"] = False
            shared["trajectoryMarketIdIsReadinessDiagnosticOnly"] = True
            shared["identityAuthority"] = "8766 exact-current COLLECTOR.market reference"
            shared["stalePreviousBucketCacheClearedImmediately"] = True
            shared["directBookStillRequiredBeforeEntry"] = True
            shared["v14PolyBinanceExactBindingStillRequired"] = True
        return payload


base.PolyGapLiveEngine = RolloverRaceSafeSharedMarketPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
