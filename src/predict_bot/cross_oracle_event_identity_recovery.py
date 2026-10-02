from __future__ import annotations

import datetime as _dt
import threading
import time
import urllib.parse
from typing import Any

from . import cross_oracle_prefetch_recovery as prefetch


resilient = prefetch.resilient
cross = prefetch.cross

_GAMMA_EVENT_BY_SLUG = "https://gamma-api.polymarket.com/events/slug/{slug}"
_TRANSPORT_HTTP_JSON = prefetch._ORIGINAL_HTTP_JSON
_BASE_PARSE_GAMMA_MARKET = cross.parse_gamma_market
_STATE_LOCK = threading.RLock()
_EVENT_RECOVERY_ATTEMPTS = 0
_EVENT_RECOVERY_HITS = 0
_LAST_EVENT_SLUG: str | None = None
_LAST_GAMMA_MARKET_SLUG: str | None = None
_LAST_CONDITION_ID: str | None = None
_LAST_RECOVERED_AT_MS: int | None = None
_LAST_EVENT_ERROR: str | None = None


def _gamma_event_url(slug: str) -> str:
    return _GAMMA_EVENT_BY_SLUG.format(slug=urllib.parse.quote(slug))


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = _dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _candidate_market(event: dict[str, Any], *, event_slug: str, bucket: int) -> dict[str, Any] | None:
    if str(event.get("slug") or "") != event_slug:
        return None
    rows = event.get("markets")
    if not isinstance(rows, list):
        return None

    expected_end_ms = (int(bucket) + 300) * 1000
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Validate the actual underlying market object before adapting its identity.
        parsed = _BASE_PARSE_GAMMA_MARKET(row, bucket_start=bucket)
        if parsed is None:
            continue
        end_ms = _iso_ms(row.get("endDate"))
        if end_ms is not None and abs(end_ms - expected_end_ms) > 120_000:
            continue

        score = 0
        if bool(row.get("active", True)):
            score += 4
        if not bool(row.get("closed", False)):
            score += 4
        if bool(row.get("enableOrderBook", True)):
            score += 2
        if bool(row.get("acceptingOrders", True)):
            score += 1
        if end_ms is not None:
            score += max(0, 2 - int(abs(end_ms - expected_end_ms) // 30_000))
        candidates.append((score, dict(row)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[0][1]
    actual_market_slug = str(selected.get("slug") or "") or None

    # The deterministic btc-updown-5m-* value is the frontend/event identity used
    # for bucket continuity. The underlying Gamma market can have a different slug.
    # Keep both instead of pretending they are always the same object.
    adapted = dict(selected)
    adapted["gammaMarketSlug"] = actual_market_slug
    adapted["eventSlug"] = event_slug
    adapted["slug"] = event_slug
    adapted["_identityRecoveredFromEvent"] = True
    return adapted


def _event_market_payload(slug: str, bucket: int) -> dict[str, Any] | None:
    global _EVENT_RECOVERY_ATTEMPTS, _EVENT_RECOVERY_HITS
    global _LAST_EVENT_SLUG, _LAST_GAMMA_MARKET_SLUG, _LAST_CONDITION_ID
    global _LAST_RECOVERED_AT_MS, _LAST_EVENT_ERROR

    with _STATE_LOCK:
        _EVENT_RECOVERY_ATTEMPTS += 1
    try:
        event = _TRANSPORT_HTTP_JSON(_gamma_event_url(slug))
    except Exception as exc:
        with _STATE_LOCK:
            _LAST_EVENT_ERROR = str(exc)[:500]
        return None
    if not isinstance(event, dict):
        with _STATE_LOCK:
            _LAST_EVENT_ERROR = "Gamma event-by-slug returned no event object"
        return None

    payload = _candidate_market(event, event_slug=slug, bucket=bucket)
    if payload is None:
        with _STATE_LOCK:
            _LAST_EVENT_ERROR = "Gamma event exists but contains no validated current 5m UP/DOWN market"
        return None

    with _STATE_LOCK:
        _EVENT_RECOVERY_HITS += 1
        _LAST_EVENT_SLUG = slug
        _LAST_GAMMA_MARKET_SLUG = str(payload.get("gammaMarketSlug") or "") or None
        _LAST_CONDITION_ID = str(payload.get("conditionId") or "") or None
        _LAST_RECOVERED_AT_MS = int(time.time() * 1000)
        _LAST_EVENT_ERROR = None
    return payload


def _bucket_from_slug(slug: str) -> int | None:
    prefix = "btc-updown-5m-"
    if not slug.startswith(prefix):
        return None
    try:
        bucket = int(slug[len(prefix):])
    except ValueError:
        return None
    return bucket if bucket > 0 else None


def _event_aware_public_json(url: str, *, timeout: float = 4.0) -> Any:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    is_market_slug = host == "gamma-api.polymarket.com" and parsed.path.startswith("/markets/slug/")
    if not is_market_slug:
        return _TRANSPORT_HTTP_JSON(url, timeout=timeout)

    slug = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    bucket = _bucket_from_slug(slug)
    try:
        return _TRANSPORT_HTTP_JSON(url, timeout=timeout)
    except Exception as market_error:
        if bucket is None:
            raise
        recovered = _event_market_payload(slug, bucket)
        if recovered is not None:
            return recovered
        raise market_error


def _parse_gamma_market_with_identity(payload: dict[str, Any], *, bucket_start: int) -> dict[str, Any] | None:
    market = _BASE_PARSE_GAMMA_MARKET(payload, bucket_start=bucket_start)
    if market is None:
        return None
    event_slug = str(payload.get("eventSlug") or "") or None
    gamma_market_slug = str(payload.get("gammaMarketSlug") or "") or None
    if event_slug:
        market["slug"] = event_slug
        market["eventSlug"] = event_slug
        market["gammaMarketSlug"] = gamma_market_slug
        market["identitySource"] = "EVENT_SLUG_MARKET"
    else:
        market["eventSlug"] = str(market.get("slug") or "") or None
        market["gammaMarketSlug"] = str(payload.get("slug") or "") or None
        market["identitySource"] = "MARKET_SLUG"
    return market


# Prefetch captures the transport helper in a module global, so patch both that
# captured helper and the base parser. Base discovery continues using the existing
# prefetch cache wrapper but can now recover a frontend event slug into the actual
# market/token pair underneath it.
prefetch._ORIGINAL_HTTP_JSON = _event_aware_public_json
cross.parse_gamma_market = _parse_gamma_market_with_identity

_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _snapshot_event_identity(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    with _STATE_LOCK:
        recovery = {
            "enabled": True,
            "marketSlugLookupFirst": True,
            "eventSlugFallback": True,
            "eventSlugMustMatchExactly": True,
            "underlyingMarketValidatedForBinaryTokens": True,
            "attempts": int(_EVENT_RECOVERY_ATTEMPTS),
            "hits": int(_EVENT_RECOVERY_HITS),
            "lastEventSlug": _LAST_EVENT_SLUG,
            "lastGammaMarketSlug": _LAST_GAMMA_MARKET_SLUG,
            "lastConditionId": _LAST_CONDITION_ID,
            "lastRecoveredAtMs": _LAST_RECOVERED_AT_MS,
            "lastError": _LAST_EVENT_ERROR,
        }
    payload["eventIdentityRecovery"] = recovery
    polymarket = payload.get("polymarket")
    if isinstance(polymarket, dict):
        polymarket["eventIdentityRecovery"] = recovery
        market = polymarket.get("market")
        if isinstance(market, dict):
            market.setdefault("eventSlug", market.get("slug"))
            market.setdefault("gammaMarketSlug", market.get("slug"))
    continuity = payload.get("continuity")
    if isinstance(continuity, dict):
        continuity["eventIdentityRecovery"] = recovery
    return payload


resilient.ResilientCrossOracleCollector.snapshot = _snapshot_event_identity


def main() -> int:
    return prefetch.main()


if __name__ == "__main__":
    raise SystemExit(main())
