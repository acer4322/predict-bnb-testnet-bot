from __future__ import annotations

import concurrent.futures
import threading
import time
import urllib.parse
from typing import Any, Callable

from . import cross_oracle_trade_readiness as ready


identity = ready.identity
prefetch = ready.prefetch
resilient = ready.resilient
cross = ready.cross

_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
_DIRECT_TRANSPORT = ready._BASE_TRANSPORT
_PATH_TIMEOUT_SECONDS = 1.5
_RETRY_PREFETCH_LEAD_SECONDS = 150.0
_STATE_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "attempts": 0,
    "hits": 0,
    "exactHits": 0,
    "slugListHits": 0,
    "eventWindowHits": 0,
    "strictMarketFallbackHits": 0,
    "lastMethod": None,
    "lastSlug": None,
    "lastError": None,
    "lastStartedAtMs": None,
    "lastSuccessAtMs": None,
    "lastLatencyMs": None,
}


def _record_attempt(slug: str) -> float:
    started = time.monotonic()
    with _STATE_LOCK:
        _STATE["attempts"] = int(_STATE["attempts"]) + 1
        _STATE["lastSlug"] = slug
        _STATE["lastStartedAtMs"] = int(time.time() * 1000)
    return started


def _record_success(method: str, slug: str, started: float) -> None:
    now_ms = int(time.time() * 1000)
    with _STATE_LOCK:
        _STATE["hits"] = int(_STATE["hits"]) + 1
        key = {
            "EVENT_EXACT_FAST": "exactHits",
            "EVENT_SLUG_LIST_FAST": "slugListHits",
            "EVENT_WINDOW_SCAN": "eventWindowHits",
            "STRICT_MARKET_FALLBACK_FAST": "strictMarketFallbackHits",
        }.get(method)
        if key:
            _STATE[key] = int(_STATE[key]) + 1
        _STATE["lastMethod"] = method
        _STATE["lastSlug"] = slug
        _STATE["lastError"] = None
        _STATE["lastSuccessAtMs"] = now_ms
        _STATE["lastLatencyMs"] = max(0.0, (time.monotonic() - started) * 1000.0)

    # Keep the V2 diagnostics coherent so existing dashboard consumers continue
    # to see the successful strict identity method.
    try:
        ready._record_discovery_success(method, slug)
    except Exception:
        pass


def _record_failure(slug: str, started: float, detail: str) -> None:
    with _STATE_LOCK:
        _STATE["lastSlug"] = slug
        _STATE["lastError"] = str(detail)[:500]
        _STATE["lastLatencyMs"] = max(0.0, (time.monotonic() - started) * 1000.0)


def _event_exact_fast(slug: str, bucket: int) -> dict[str, Any] | None:
    try:
        payload = _DIRECT_TRANSPORT(
            identity._gamma_event_url(slug), timeout=_PATH_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return ready._strict_candidate_market(payload, event_slug=slug, bucket=bucket)


def _event_slug_list_fast(slug: str, bucket: int) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"slug": slug, "limit": 10})
    try:
        payload = _DIRECT_TRANSPORT(
            f"{_GAMMA_EVENTS_URL}?{query}", timeout=_PATH_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    rows = payload if isinstance(payload, list) else []
    for event in rows:
        if not isinstance(event, dict) or str(event.get("slug") or "") != slug:
            continue
        candidate = ready._strict_candidate_market(
            event, event_slug=slug, bucket=bucket
        )
        if candidate is not None:
            return candidate
    return None


def _event_window_scan(slug: str, bucket: int) -> dict[str, Any] | None:
    """Bypass a stale Gamma slug index by scanning the exact event end window.

    The deterministic btc-updown-5m-* value is an event identity.  The previous
    fallback scanned /markets and required the underlying market slug to equal
    that event slug, which is not guaranteed.  This scan stays on the event API,
    then validates the nested market with the same strict UP/DOWN token rules.
    """

    expected_end = int(bucket) + 300
    end_min = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expected_end - 90))
    end_max = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expected_end + 90))
    query = urllib.parse.urlencode(
        {
            "active": "true",
            "closed": "false",
            "end_date_min": end_min,
            "end_date_max": end_max,
            "order": "endDate",
            "ascending": "true",
            "limit": 100,
        }
    )
    try:
        payload = _DIRECT_TRANSPORT(
            f"{_GAMMA_EVENTS_URL}?{query}", timeout=_PATH_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    rows = payload if isinstance(payload, list) else []
    for event in rows:
        if not isinstance(event, dict) or str(event.get("slug") or "") != slug:
            continue
        candidate = ready._strict_candidate_market(
            event, event_slug=slug, bucket=bucket
        )
        if candidate is not None:
            return candidate
    return None


def _parallel_event_discovery(
    slug: str, bucket: int
) -> tuple[dict[str, Any] | None, str | None]:
    started = _record_attempt(slug)
    paths: tuple[tuple[str, Callable[[str, int], dict[str, Any] | None]], ...] = (
        ("EVENT_EXACT_FAST", _event_exact_fast),
        ("EVENT_SLUG_LIST_FAST", _event_slug_list_fast),
        ("EVENT_WINDOW_SCAN", _event_window_scan),
    )
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(paths), thread_name_prefix="poly-gamma-discovery"
    )
    futures = {
        executor.submit(fn, slug, bucket): method for method, fn in paths
    }
    try:
        for future in concurrent.futures.as_completed(
            futures, timeout=_PATH_TIMEOUT_SECONDS + 0.5
        ):
            method = futures[future]
            try:
                candidate = future.result()
            except Exception:
                candidate = None
            if candidate is None:
                continue
            for other in futures:
                if other is not future:
                    other.cancel()
            _record_success(method, slug, started)
            executor.shutdown(wait=False, cancel_futures=True)
            return candidate, method
    except concurrent.futures.TimeoutError:
        pass
    finally:
        # Network calls are independently bounded.  Do not hold the market
        # supervisor waiting for a slow losing path once the overall deadline is
        # reached; its next discovery retry is more valuable.
        executor.shutdown(wait=False, cancel_futures=True)

    _record_failure(slug, started, "all Gamma event discovery paths missed or timed out")
    return None, None


def _strict_market_fallback(slug: str, bucket: int) -> dict[str, Any] | None:
    url = prefetch._gamma_exact_url(slug)
    try:
        payload = _DIRECT_TRANSPORT(url, timeout=_PATH_TIMEOUT_SECONDS)
    except Exception:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return None
    return ready._strict_market_candidate(payload, event_slug=slug, bucket=bucket)


def _fast_event_first_json(url: str, *, timeout: float = 4.0) -> Any:
    parsed = urllib.parse.urlsplit(url)
    is_market_slug = (
        (parsed.hostname or "").lower() == "gamma-api.polymarket.com"
        and parsed.path.startswith("/markets/slug/")
    )
    if not is_market_slug:
        return _DIRECT_TRANSPORT(url, timeout=timeout)

    slug = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    bucket = identity._bucket_from_slug(slug)
    if bucket is None:
        return _DIRECT_TRANSPORT(url, timeout=min(timeout, _PATH_TIMEOUT_SECONDS))

    candidate, _method = _parallel_event_discovery(slug, bucket)
    if candidate is not None:
        return candidate

    # Compatibility only: accept the market-slug endpoint when it itself exposes
    # explicit UP/DOWN tokens and the exact requested five-minute bucket.
    started = time.monotonic()
    fallback = _strict_market_fallback(slug, bucket)
    if fallback is not None:
        _record_success("STRICT_MARKET_FALLBACK_FAST", slug, started)
        return fallback
    raise ValueError(f"no strict current BTC 5m UP/DOWN event for {slug}")


def _discover_payload_redundant(
    slug: str, bucket: int
) -> tuple[dict[str, Any] | None, str | None]:
    candidate, method = _parallel_event_discovery(slug, bucket)
    if candidate is not None and method is not None:
        return candidate, method
    fallback = _strict_market_fallback(slug, bucket)
    if fallback is not None:
        return fallback, "STRICT_MARKET_FALLBACK_FAST"
    return None, None


# Replace both current-market discovery and next-market prefetch with the same
# bounded redundant resolver.  _http_json_with_prefetch_cache resolves this
# module global dynamically, so cached prefetched metadata remains first-class.
prefetch._ORIGINAL_HTTP_JSON = _fast_event_first_json
prefetch._discover_payload = _discover_payload_redundant
prefetch.PREFETCH_LEAD_SECONDS = max(
    float(prefetch.PREFETCH_LEAD_SECONDS), _RETRY_PREFETCH_LEAD_SECONDS
)


_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _snapshot_redundant_discovery(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    with _STATE_LOCK:
        state = dict(_STATE)
    state.update(
        {
            "enabled": True,
            "pathTimeoutSeconds": _PATH_TIMEOUT_SECONDS,
            "parallelPaths": [
                "EVENT_EXACT_FAST",
                "EVENT_SLUG_LIST_FAST",
                "EVENT_WINDOW_SCAN",
            ],
            "eventWindowUsesEndDateRange": True,
            "underlyingMarketSlugNeedNotEqualEventSlug": True,
            "strictExactEventSlugStillRequired": True,
            "explicitUpDownTokensStillRequired": True,
            "nearestEventFallback": False,
            "prefetchLeadSeconds": float(prefetch.PREFETCH_LEAD_SECONDS),
        }
    )
    payload["gammaRedundantDiscovery"] = state
    poly = payload.get("polymarket")
    if isinstance(poly, dict):
        poly["gammaRedundantDiscovery"] = state
    continuity = payload.get("continuity")
    if isinstance(continuity, dict):
        continuity["gammaRedundantDiscovery"] = state
    return payload


resilient.ResilientCrossOracleCollector.snapshot = _snapshot_redundant_discovery


def main() -> int:
    return ready.main()


if __name__ == "__main__":
    raise SystemExit(main())
