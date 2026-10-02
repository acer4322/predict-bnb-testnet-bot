from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
from typing import Any

import httpx

from . import cross_oracle_resilient as resilient

_ALLOWED_HOSTS = {"gamma-api.polymarket.com", "clob.polymarket.com"}
_LOCK = threading.RLock()
_FALLBACK_COUNT = 0
_LAST_FALLBACK_AT_MS: int | None = None
_LAST_FALLBACK_HOST: str | None = None
_LAST_PRIMARY_TLS_ERROR: str | None = None
_LAST_GAMMA_DISCOVERY_METHOD: str | None = None
_GAMMA_LIST_URL = "https://gamma-api.polymarket.com/markets"


class _GammaMarketNotPublished(urllib.error.URLError):
    """The expected current five-minute Gamma slug does not exist yet.

    A 404 here is an application-level discovery result, not a TLS failure. The
    resilient collector keeps retrying the current bucket without surfacing it
    as a certificate/transport error.
    """


def _is_self_signed_tls_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "certificate_verify_failed" in text
        and ("self-signed certificate" in text or "self signed certificate" in text)
    )


def _is_gamma_slug_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return (
        (parsed.hostname or "").lower() == "gamma-api.polymarket.com"
        and parsed.path.startswith("/markets/slug/")
    )


def _gamma_slug_from_url(url: str) -> str | None:
    if not _is_gamma_slug_url(url):
        return None
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]) or None


def _gamma_list_lookup(
    client: httpx.Client,
    *,
    slug: str,
) -> dict[str, Any] | None:
    """Second discovery path for transient exact-slug 404s.

    Gamma exposes both /markets/slug/{slug} and /markets?slug={slug}. Around
    five-minute rollovers the exact endpoint can occasionally lag behind the
    list/query endpoint. Only an exact slug match is accepted so we can never
    silently bind the previous or next five-minute market.
    """

    response = client.get(
        _GAMMA_LIST_URL,
        params={"slug": slug, "limit": 5},
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if isinstance(row, dict) and str(row.get("slug") or "") == slug:
            return row
    return None


def _read_public_json(
    client: httpx.Client,
    url: str,
) -> Any:
    """Read one public endpoint and recover exact Gamma slug 404 via list lookup."""

    global _LAST_GAMMA_DISCOVERY_METHOD

    response = client.get(url)
    if response.status_code == 404:
        slug = _gamma_slug_from_url(url)
        if slug:
            recovered = _gamma_list_lookup(client, slug=slug)
            if recovered is not None:
                with _LOCK:
                    _LAST_GAMMA_DISCOVERY_METHOD = "LIST_QUERY_FALLBACK"
                return recovered
            raise _GammaMarketNotPublished(
                f"Gamma market not published yet: {slug} (exact 404; list query empty)"
            )
    response.raise_for_status()
    payload = response.json()
    if _is_gamma_slug_url(url):
        with _LOCK:
            _LAST_GAMMA_DISCOVERY_METHOD = "EXACT_SLUG"
    return payload


def _public_polymarket_json(url: str, *, timeout: float = 4.0) -> Any:
    """Strict TLS first; insecure fallback only for two public read-only Poly hosts.

    This never applies to Binance, signed APIs, wallets, quotes, placements, exits,
    redemptions, or any endpoint carrying credentials. The fallback exists only
    because some Windows/network setups insert a self-signed TLS inspection CA
    that Python does not trust even when environment proxies are disabled.

    Gamma's current five-minute slug can transiently return HTTP 404 around a
    rollover. The exact-slug path is immediately retried through the Gamma
    list/query endpoint for the same slug. If both paths say the market is not
    available, that is classified as WAITING_GAMMA rather than a TLS failure.
    """

    global _FALLBACK_COUNT, _LAST_FALLBACK_AT_MS, _LAST_FALLBACK_HOST, _LAST_PRIMARY_TLS_ERROR

    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _ALLOWED_HOSTS:
        raise urllib.error.URLError(
            f"restricted TLS helper refused non-public Polymarket endpoint: {host or 'unknown'}"
        )

    headers = {
        "User-Agent": resilient.USER_AGENT,
        "Accept": "application/json",
    }

    try:
        with httpx.Client(
            headers=headers,
            timeout=timeout,
            verify=True,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            return _read_public_json(client, url)
    except _GammaMarketNotPublished:
        raise
    except (json.JSONDecodeError, httpx.HTTPError) as primary_exc:
        if not _is_self_signed_tls_error(primary_exc):
            raise urllib.error.URLError(
                f"direct httpx request failed: {primary_exc}"
            ) from primary_exc
        with _LOCK:
            _LAST_PRIMARY_TLS_ERROR = str(primary_exc)[:500]

    try:
        with httpx.Client(
            headers=headers,
            timeout=timeout,
            verify=False,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            payload = _read_public_json(client, url)
    except _GammaMarketNotPublished:
        raise
    except (httpx.HTTPError, json.JSONDecodeError) as fallback_exc:
        raise urllib.error.URLError(
            f"public Polymarket TLS fallback failed: {fallback_exc}"
        ) from fallback_exc

    with _LOCK:
        _FALLBACK_COUNT += 1
        _LAST_FALLBACK_AT_MS = int(time.time() * 1000)
        _LAST_FALLBACK_HOST = host
    return payload


# cross_oracle_resilient imports and replaces cross_oracle._http_json at module
# import time. Replace it once more here before collector construction.
resilient.cross_oracle_module._http_json = _public_polymarket_json
resilient.HTTP_TRANSPORT = "httpx-direct-strict-then-public-poly-tls-fallback"

# The resilient discover method intentionally consumes urllib-style exceptions
# so it can account for feed gaps. Normalize only the expected Gamma 404 after
# that accounting has occurred; genuine TLS/transport failures remain errors.
_original_discover_market = resilient.ResilientCrossOracleCollector._discover_market


def _discover_market_with_waiting_404(
    self: Any, slug: str, bucket: int
) -> bool:
    ok = _original_discover_market(self, slug, bucket)
    if ok:
        with _LOCK:
            discovery_method = _LAST_GAMMA_DISCOVERY_METHOD
        with self.lock:
            self.polymarket["marketDiscovery"] = {
                "status": "FOUND",
                "slug": slug,
                "bucketStartSec": int(bucket),
                "checkedAtMs": int(time.time() * 1000),
                "method": discovery_method or "UNKNOWN",
            }
        return True

    with self.lock:
        detail = str(self.polymarket.get("error") or "")
    if "Gamma market not published yet:" not in detail:
        return False

    now_ms = int(time.time() * 1000)
    with self.lock:
        self.polymarket["status"] = "WAITING_MARKET"
        self.polymarket["error"] = None
        self.polymarket["marketDiscovery"] = {
            "status": "WAITING_GAMMA",
            "slug": slug,
            "bucketStartSec": int(bucket),
            "checkedAtMs": now_ms,
            "detail": "Exact slug returned 404 and list query had no exact match; retrying current 5m slug",
            "retrySeconds": float(resilient.DISCOVERY_RETRY_SECONDS),
        }
        # Preserve the feed gap, but classify it as a normal market publication
        # wait instead of a transport/TLS failure.
        if getattr(self, "gap_active", False):
            self.gap_reason = "MARKET_NOT_PUBLISHED"
            self.gap_detail = f"Gamma has no exact match for {slug}; retrying"
            self.gap_target_slug = slug
    self.last_discovery_error = None
    self.consecutive_discovery_failures = 0
    return False


resilient.ResilientCrossOracleCollector._discover_market = _discover_market_with_waiting_404

_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _snapshot_with_tls_fallback(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    with _LOCK:
        count = _FALLBACK_COUNT
        fallback_at = _LAST_FALLBACK_AT_MS
        fallback_host = _LAST_FALLBACK_HOST
        primary_error = _LAST_PRIMARY_TLS_ERROR
        discovery_method = _LAST_GAMMA_DISCOVERY_METHOD
    continuity = payload.get("continuity")
    if isinstance(continuity, dict):
        continuity.update(
            {
                "httpTransport": resilient.HTTP_TRANSPORT,
                "tlsVerify": "STRICT_FIRST",
                "tlsFallbackEnabled": True,
                "tlsFallbackScope": sorted(_ALLOWED_HOSTS),
                "tlsFallbackPublicReadOnlyOnly": True,
                "tlsFallbackCount": count,
                "lastTlsFallbackAtMs": fallback_at,
                "lastTlsFallbackHost": fallback_host,
                "lastPrimaryTlsError": primary_error,
                "lastGammaDiscoveryMethod": discovery_method,
                "gammaExact404ListFallback": True,
            }
        )
    polymarket = payload.get("polymarket")
    if isinstance(polymarket, dict) and isinstance(continuity, dict):
        polymarket["continuity"] = continuity
    return payload


resilient.ResilientCrossOracleCollector.snapshot = _snapshot_with_tls_fallback


def main() -> int:
    return resilient.main()


if __name__ == "__main__":
    raise SystemExit(main())
