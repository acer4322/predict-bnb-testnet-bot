from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import poly_gap_live as base
from .binance_exact_market import (
    DEFAULT_WINDOW_MS,
    bucket_start_ms,
    find_exact_market_summary,
    live_cache_from_summary,
)
from .poly_gap_live_v14 import MarketBoundPolyGapLiveEngine


BINANCE_PREFETCH_LEAD_MS = max(
    30_000,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_PREFETCH_LEAD_MS", "120000")),
)
BINANCE_PREFETCH_RETRY_MS = max(
    1_000,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_PREFETCH_RETRY_MS", "5000")),
)
BINANCE_CURRENT_RETRY_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_CURRENT_RETRY_MS", "500")),
)
BINANCE_EXACT_MAX_PAGES = max(
    1,
    min(3, int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_EXACT_MAX_PAGES", "1"))),
)


class PrefetchedBinanceMarketPolyGapLiveEngine(MarketBoundPolyGapLiveEngine):
    """V15: prefetch exact next Binance Prediction metadata before rollover.

    V14 intentionally refuses a Poly/Binance market mismatch.  The remaining
    rollover delay came from waiting until the old Binance market expired before
    asking market/list for the replacement.  V15 keeps the strict V14 identity
    gate and changes only metadata availability:

    - during the final two minutes of a healthy current market, a background task
      asks for the *exact next five-minute start window*;
    - only explicit BTCUSDT CRYPTO_UP_DOWN topics with exact UP/DOWN token names,
      complete IDs and a five-minute start/end boundary are cached;
    - at the boundary that prefetched metadata is promoted immediately;
    - if Binance did not publish the future topic early, the executor continues
      exact-window discovery after rollover and remains fail-closed until it exists;
    - a nearest future/previous topic is never substituted for the requested bucket.

    No Poly rule, CHOP guard, order reconciliation, exit behavior or price
    tolerance is relaxed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.binance_prefetch_thread: threading.Thread | None = None
        self.binance_prefetched_market: dict[str, Any] | None = None
        self.binance_prefetch_target_start_ms: int | None = None
        self.binance_prefetch_attempts = 0
        self.binance_prefetch_hits = 0
        self.binance_prefetch_promotions = 0
        self.binance_exact_current_hits = 0
        self.binance_exact_current_misses = 0
        self.binance_last_prefetch_at_ms: int | None = None
        self.binance_last_prefetch_error: str | None = None
        self.binance_last_current_lookup_at_ms: int | None = None
        self.binance_last_current_error: str | None = None
        self.binance_last_promoted_market_id: int | None = None
        self._binance_query_lock = threading.Lock()
        self._next_prefetch_attempt_mono = 0.0
        self._next_current_attempt_mono = 0.0

    def start(self) -> None:
        if self.binance_prefetch_thread is None or not self.binance_prefetch_thread.is_alive():
            self.binance_prefetch_thread = threading.Thread(
                target=self._binance_prefetch_loop,
                name="poly-gap-binance-prefetch",
                daemon=True,
            )
            self.binance_prefetch_thread.start()
        super().start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.binance_prefetch_thread:
            self.binance_prefetch_thread.join(timeout=2.0)
        super().stop()

    def _binance_server_now_ms(self) -> int:
        with self.lock:
            metadata = self.metadata_client
        if metadata is not None:
            try:
                return int(metadata.server_timestamp_ms())
            except Exception:
                pass
        return base._now_ms()

    @staticmethod
    def _cache_matches_start(cache: dict[str, Any] | None, target_start_ms: int) -> bool:
        if not cache:
            return False
        try:
            start_ms = int(cache.get("start_ms") or (int(cache.get("end_ms") or 0) - DEFAULT_WINDOW_MS))
            end_ms = int(cache.get("end_ms") or 0)
            market_id = int(cache.get("market_id") or 0)
        except (TypeError, ValueError):
            return False
        return bool(
            market_id > 0
            and abs(start_ms - int(target_start_ms)) <= 1_500
            and abs(end_ms - (int(target_start_ms) + DEFAULT_WINDOW_MS)) <= 1_500
        )

    def _fetch_exact_cache(self, target_start_ms: int) -> dict[str, Any] | None:
        if not self._ensure_clients():
            return None
        with self.lock:
            metadata = self.metadata_client
        if metadata is None:
            return None
        summary = find_exact_market_summary(
            metadata,
            symbol="BTCUSDT",
            target_start_ms=int(target_start_ms),
            max_pages=BINANCE_EXACT_MAX_PAGES,
        )
        return live_cache_from_summary(summary) if summary is not None else None

    def _binance_prefetch_loop(self) -> None:
        while not self.stop_event.wait(0.25):
            now_ms = self._binance_server_now_ms()
            current_start_ms = bucket_start_ms(now_ms)
            target_start_ms = current_start_ms + DEFAULT_WINDOW_MS
            until_target_ms = target_start_ms - now_ms

            with self.lock:
                cached = dict(self.binance_prefetched_market or {})
            if cached and not self._cache_matches_start(cached, target_start_ms):
                with self.lock:
                    self.binance_prefetched_market = None
                    self.binance_prefetch_target_start_ms = None

            if until_target_ms < 0 or until_target_ms > BINANCE_PREFETCH_LEAD_MS:
                continue
            with self.lock:
                if self._cache_matches_start(self.binance_prefetched_market, target_start_ms):
                    continue
            now_mono = time.monotonic()
            if now_mono < self._next_prefetch_attempt_mono:
                continue
            self._next_prefetch_attempt_mono = now_mono + BINANCE_PREFETCH_RETRY_MS / 1000.0
            if not self._binance_query_lock.acquire(blocking=False):
                continue
            try:
                self.binance_prefetch_attempts += 1
                self.binance_last_prefetch_at_ms = base._now_ms()
                candidate = self._fetch_exact_cache(target_start_ms)
                if candidate is None:
                    self.binance_last_prefetch_error = (
                        f"exact next Binance BTC 5m topic not published yet: start={target_start_ms}"
                    )
                    continue
                with self.lock:
                    self.binance_prefetched_market = candidate
                    self.binance_prefetch_target_start_ms = target_start_ms
                self.binance_prefetch_hits += 1
                self.binance_last_prefetch_error = None
            except Exception as exc:
                self.binance_last_prefetch_error = str(exc)[:500]
            finally:
                self._binance_query_lock.release()

    def _promote_prefetched(self, target_start_ms: int) -> dict[str, Any] | None:
        with self.lock:
            prefetched = dict(self.binance_prefetched_market or {})
        if not self._cache_matches_start(prefetched, target_start_ms):
            return None
        with self.lock:
            self.market_cache = dict(prefetched)
            self.binance_prefetched_market = None
            self.binance_prefetch_target_start_ms = None
        market_id = int(prefetched["market_id"])
        if self.halted_market_id is not None and self.halted_market_id != market_id:
            self.halted_market_id = None
            self.halted_reason = None
        self.binance_prefetch_promotions += 1
        self.binance_last_promoted_market_id = market_id
        self._event(
            "INFO",
            "BINANCE_NEXT_MARKET_PREFETCH_PROMOTED",
            market_id,
            None,
            (
                f"promoted exact prefetched Binance 5m metadata at boundary; "
                f"start={target_start_ms}; end={prefetched['end_ms']}"
            ),
        )
        return prefetched

    def _prime_market(self, force: bool = False) -> dict[str, Any] | None:
        now_ms = self._binance_server_now_ms()
        target_start_ms = bucket_start_ms(now_ms)

        with self.lock:
            cached = dict(self.market_cache or {})
        if self._cache_matches_start(cached, target_start_ms):
            self.binance_last_current_error = None
            return cached

        promoted = self._promote_prefetched(target_start_ms)
        if promoted is not None:
            self.binance_last_current_error = None
            return promoted

        # Never leave an old/future market in the live cache once the wall/server
        # bucket says it belongs to a different window.
        with self.lock:
            self.market_cache = None

        now_mono = time.monotonic()
        if not force and now_mono < self._next_current_attempt_mono:
            return None
        self._next_current_attempt_mono = now_mono + BINANCE_CURRENT_RETRY_MS / 1000.0
        if not self._binance_query_lock.acquire(blocking=False):
            return None
        try:
            self.binance_last_current_lookup_at_ms = base._now_ms()
            candidate = self._fetch_exact_cache(target_start_ms)
            if candidate is None:
                self.binance_exact_current_misses += 1
                self.binance_last_current_error = (
                    f"waiting for exact current Binance BTC 5m topic start={target_start_ms}"
                )
                return None
            with self.lock:
                self.market_cache = dict(candidate)
            market_id = int(candidate["market_id"])
            if self.halted_market_id is not None and self.halted_market_id != market_id:
                self.halted_market_id = None
                self.halted_reason = None
            self.binance_exact_current_hits += 1
            self.binance_last_current_error = None
            return candidate
        except Exception as exc:
            self.binance_last_current_error = str(exc)[:500]
            return None
        finally:
            self._binance_query_lock.release()

    def _tick(self) -> None:
        had_active = self._current_active_round() is not None
        super()._tick()
        if had_active or self._current_active_round() is not None:
            return
        now_ms = self._binance_server_now_ms()
        target_start_ms = bucket_start_ms(now_ms)
        with self.lock:
            cache = dict(self.market_cache or {})
        if not self._cache_matches_start(cache, target_start_ms):
            self.status = "WAITING_BINANCE_CURRENT_MARKET"
            self.last_error = self.binance_last_current_error or (
                f"exact Binance 5m metadata unavailable for start={target_start_ms}"
            )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V15"
        with self.lock:
            prefetched = dict(self.binance_prefetched_market or {}) or None
        payload["binanceMarketPrefetch"] = {
            "enabled": True,
            "leadMs": BINANCE_PREFETCH_LEAD_MS,
            "prefetchRetryMs": BINANCE_PREFETCH_RETRY_MS,
            "currentRetryMs": BINANCE_CURRENT_RETRY_MS,
            "exactMaxPages": BINANCE_EXACT_MAX_PAGES,
            "prefetched": prefetched,
            "prefetchTargetStartMs": self.binance_prefetch_target_start_ms,
            "attempts": int(self.binance_prefetch_attempts),
            "hits": int(self.binance_prefetch_hits),
            "promotions": int(self.binance_prefetch_promotions),
            "exactCurrentHits": int(self.binance_exact_current_hits),
            "exactCurrentMisses": int(self.binance_exact_current_misses),
            "lastPrefetchAtMs": self.binance_last_prefetch_at_ms,
            "lastPrefetchError": self.binance_last_prefetch_error,
            "lastCurrentLookupAtMs": self.binance_last_current_lookup_at_ms,
            "lastCurrentError": self.binance_last_current_error,
            "lastPromotedMarketId": self.binance_last_promoted_market_id,
            "neverUsesNearestWrongWindow": True,
            "v14PolyMarketBindingStillRequired": True,
        }
        return payload


base.PolyGapLiveEngine = PrefetchedBinanceMarketPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
