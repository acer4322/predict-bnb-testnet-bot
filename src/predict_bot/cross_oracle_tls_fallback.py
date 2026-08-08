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


def _is_self_signed_tls_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "certificate_verify_failed" in text
        and ("self-signed certificate" in text or "self signed certificate" in text)
    )


def _public_polymarket_json(url: str, *, timeout: float = 4.0) -> Any:
    """Strict TLS first; insecure fallback only for two public read-only Poly hosts.

    This never applies to Binance, signed APIs, wallets, quotes, placements, exits,
    redemptions, or any endpoint carrying credentials. The fallback exists only
    because some Windows/network setups insert a self-signed TLS inspection CA
    that Python does not trust even when environment proxies are disabled.
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
            response = client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as primary_exc:
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
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
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

_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _snapshot_with_tls_fallback(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    with _LOCK:
        count = _FALLBACK_COUNT
        fallback_at = _LAST_FALLBACK_AT_MS
        fallback_host = _LAST_FALLBACK_HOST
        primary_error = _LAST_PRIMARY_TLS_ERROR
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
