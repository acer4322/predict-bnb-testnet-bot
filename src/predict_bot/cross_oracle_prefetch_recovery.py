from __future__ import annotations

import json
import threading
import time
import urllib.parse
from typing import Any

from . import cross_oracle_rollover_guard as rollover


resilient = rollover.resilient
cross = resilient.cross_oracle_module

PREFETCH_LEAD_SECONDS = 75.0
PREFETCH_RETRY_SECONDS = 5.0
_GAMMA_LIST_URL = "https://gamma-api.polymarket.com/markets"
_ORIGINAL_HTTP_JSON = cross._http_json
_CACHE_LOCK = threading.RLock()
_MARKET_CACHE: dict[str, dict[str, Any]] = {}


def _gamma_exact_url(slug: str) -> str:
    return cross.GAMMA_MARKET_BY_SLUG.format(slug=urllib.parse.quote(slug))


def _cache_market(slug: str, payload: dict[str, Any], bucket: int, method: str) -> bool:
    market = cross.parse_gamma_market(payload, bucket_start=bucket)
    if market is None or str(market.get("slug") or "") != slug:
        return False
    with _CACHE_LOCK:
        _MARKET_CACHE[slug] = {
            "payload": dict(payload),
            "bucket": int(bucket),
            "method": method,
            "cachedAtMs": int(time.time() * 1000),
        }
        # Five-minute markets only need a tiny rolling cache.
        if len(_MARKET_CACHE) > 6:
            for key in sorted(_MARKET_CACHE, key=lambda item: int(_MARKET_CACHE[item]["bucket"]))[:-4]:
                _MARKET_CACHE.pop(key, None)
    return True


def _cached_payload(slug: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        row = _MARKET_CACHE.get(slug)
        payload = row.get("payload") if isinstance(row, dict) else None
        return dict(payload) if isinstance(payload, dict) else None


def _window_scan(slug: str, bucket: int) -> dict[str, Any] | None:
    """Find the exact slug through a narrow active-market scan.

    Gamma's exact-slug index and its filtered slug query can briefly disagree
    around five-minute rollovers. The broad scan remains fail-safe by accepting
    only the exact deterministic BTC 5m slug and validating its token pair.
    """
    start_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bucket - 30))
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bucket + 330))
    query = urllib.parse.urlencode(
        {
            "active": "true",
            "closed": "false",
            "start_date_min": start_iso,
            "start_date_max": end_iso,
            "limit": 100,
        }
    )
    payload = _ORIGINAL_HTTP_JSON(f"{_GAMMA_LIST_URL}?{query}")
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("slug") or "") != slug:
            continue
        if cross.parse_gamma_market(row, bucket_start=bucket) is not None:
            return dict(row)
    return None


def _discover_payload(slug: str, bucket: int) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = _ORIGINAL_HTTP_JSON(_gamma_exact_url(slug))
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if isinstance(payload, dict) and str(payload.get("slug") or "") == slug:
            if cross.parse_gamma_market(payload, bucket_start=bucket) is not None:
                return dict(payload), "EXACT_OR_SLUG_QUERY"
    except Exception:
        pass
    try:
        payload = _window_scan(slug, bucket)
        if payload is not None:
            return payload, "ACTIVE_WINDOW_SCAN"
    except Exception:
        pass
    return None, None


def _http_json_with_prefetch_cache(url: str, *, timeout: float = 4.0) -> Any:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.hostname or "").lower() == "gamma-api.polymarket.com" and parsed.path.startswith("/markets/slug/"):
        slug = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
        cached = _cached_payload(slug)
        if cached is not None:
            return cached
    return _ORIGINAL_HTTP_JSON(url, timeout=timeout)


# Base discovery resolves this helper dynamically. Cached next-market metadata can
# therefore be installed through the unchanged, well-tested discovery path.
cross._http_json = _http_json_with_prefetch_cache


def _ensure_prefetch_state(self: Any) -> None:
    if not hasattr(self, "_next_prefetch_attempt_at"):
        self._next_prefetch_attempt_at = 0.0
    if not hasattr(self, "_last_prefetched_slug"):
        self._last_prefetched_slug = None
    if not hasattr(self, "_last_prefetch_at_ms"):
        self._last_prefetch_at_ms = None
    if not hasattr(self, "_last_prefetch_method"):
        self._last_prefetch_method = None
    if not hasattr(self, "_prefetch_hit_count"):
        self._prefetch_hit_count = 0
    if not hasattr(self, "_window_scan_recovery_count"):
        self._window_scan_recovery_count = 0
    if not hasattr(self, "_discovery_before_invalidation_count"):
        self._discovery_before_invalidation_count = 0


def _prefetch_next_market(self: Any, now: float, bucket: int) -> None:
    _ensure_prefetch_state(self)
    boundary = bucket + 300
    if boundary - now > PREFETCH_LEAD_SECONDS or now < self._next_prefetch_attempt_at:
        return
    next_bucket = bucket + 300
    next_slug = f"btc-updown-5m-{next_bucket}"
    if _cached_payload(next_slug) is not None:
        return
    self._next_prefetch_attempt_at = now + PREFETCH_RETRY_SECONDS
    payload, method = _discover_payload(next_slug, next_bucket)
    if payload is None or method is None:
        return
    if not _cache_market(next_slug, payload, next_bucket, method):
        return
    self._last_prefetched_slug = next_slug
    self._last_prefetch_at_ms = int(time.time() * 1000)
    self._last_prefetch_method = method


def _market_supervisor_prefetched(self: Any) -> None:
    _ensure_prefetch_state(self)
    while not self.stop_event.is_set():
        now = time.time()
        slug, bucket = cross.current_btc_5m_slug(now)
        current_slug = self._market_slug()

        if current_slug == slug:
            _prefetch_next_market(self, now, bucket)
            with self.lock:
                self._rollover_invalidated_target_slug = None
            self.stop_event.wait(resilient.DISCOVERY_RETRY_SECONDS)
            continue

        self._open_gap(
            "MARKET_ROLLOVER_DISCOVERY",
            f"need current market {slug}; have {current_slug or 'none'}",
            slug,
        )

        cached_before = _cached_payload(slug) is not None
        self._discovery_before_invalidation_count += 1
        if self._discover_market(slug, bucket):
            if cached_before:
                self._prefetch_hit_count += 1
            with self.lock:
                self._rollover_invalidated_target_slug = None
                discovery = self.polymarket.get("marketDiscovery")
                if isinstance(discovery, dict) and cached_before:
                    discovery["method"] = "PREFETCH_CACHE"
            self.stop_event.wait(resilient.DISCOVERY_RETRY_SECONDS)
            continue

        # Exact/filter discovery failed. Try a narrow unfiltered active scan before
        # severing the old socket; it still accepts only the exact target slug.
        payload, method = _discover_payload(slug, bucket)
        if payload is not None and method is not None and _cache_market(slug, payload, bucket, method):
            if method == "ACTIVE_WINDOW_SCAN":
                self._window_scan_recovery_count += 1
            if self._discover_market(slug, bucket):
                with self.lock:
                    self._rollover_invalidated_target_slug = None
                    discovery = self.polymarket.get("marketDiscovery")
                    if isinstance(discovery, dict):
                        discovery["method"] = method
                self.stop_event.wait(resilient.DISCOVERY_RETRY_SECONDS)
                continue

        # Only now detach the old market. Downstream continuity has already been
        # fail-closed since _open_gap above, so the stale market cannot trade.
        rollover._invalidate_stale_market_snapshot(self, slug)
        self.stop_event.wait(resilient.DISCOVERY_RETRY_SECONDS)


_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _snapshot_prefetched(self: Any) -> dict[str, Any]:
    _ensure_prefetch_state(self)
    payload = _original_snapshot(self)
    now = time.time()
    _, bucket = cross.current_btc_5m_slug(now)
    next_slug = f"btc-updown-5m-{bucket + 300}"
    with _CACHE_LOCK:
        cached = _MARKET_CACHE.get(next_slug)
        cache_info = dict(cached) if isinstance(cached, dict) else None
        if cache_info is not None:
            cache_info.pop("payload", None)
    state = {
        "enabled": True,
        "prefetchLeadSeconds": PREFETCH_LEAD_SECONDS,
        "retrySeconds": PREFETCH_RETRY_SECONDS,
        "nextSlug": next_slug,
        "nextMarketCached": cache_info is not None,
        "nextMarketCache": cache_info,
        "lastPrefetchedSlug": self._last_prefetched_slug,
        "lastPrefetchAtMs": self._last_prefetch_at_ms,
        "lastPrefetchMethod": self._last_prefetch_method,
        "prefetchHitCount": int(self._prefetch_hit_count),
        "windowScanRecoveryCount": int(self._window_scan_recovery_count),
        "discoveryBeforeInvalidationCount": int(self._discovery_before_invalidation_count),
        "invalidateOnlyAfterDiscoveryFailure": True,
    }
    payload["marketPrefetch"] = state
    polymarket = payload.get("polymarket")
    if isinstance(polymarket, dict):
        polymarket["marketPrefetch"] = state
    return payload


resilient.ResilientCrossOracleCollector._market_supervisor = _market_supervisor_prefetched
resilient.ResilientCrossOracleCollector.snapshot = _snapshot_prefetched


def main() -> int:
    return rollover.main()


if __name__ == "__main__":
    raise SystemExit(main())
