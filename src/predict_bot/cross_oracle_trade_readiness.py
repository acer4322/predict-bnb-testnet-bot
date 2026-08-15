from __future__ import annotations

import json
import threading
import time
import urllib.parse
from typing import Any

from . import cross_oracle_event_identity_recovery as identity


prefetch = identity.prefetch
resilient = identity.resilient
cross = identity.cross
rollover = prefetch.rollover

_INITIAL_BOOK_TIMEOUT_SECONDS = 3.0
_REDISCOVER_AFTER_BOOK_TIMEOUTS = 2
_STRICT_END_SKEW_MS = 30_000
_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
_BASE_TRANSPORT = identity._TRANSPORT_HTTP_JSON

_DISCOVERY_LOCK = threading.RLock()
_DISCOVERY_V2 = {
    "eventExactAttempts": 0,
    "eventExactHits": 0,
    "eventListAttempts": 0,
    "eventListHits": 0,
    "marketFallbackHits": 0,
    "lastMethod": None,
    "lastSlug": None,
    "lastError": None,
    "lastSuccessAtMs": None,
}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _strict_market_candidate(
    row: dict[str, Any],
    *,
    event_slug: str,
    bucket: int,
) -> dict[str, Any] | None:
    """Accept only the actual BTC five-minute UP/DOWN market underneath an event."""

    tokens = [str(value) for value in _json_list(row.get("clobTokenIds"))]
    outcomes = [str(value).strip().upper() for value in _json_list(row.get("outcomes"))]
    if len(tokens) != 2 or set(outcomes) != {"UP", "DOWN"}:
        return None
    if not str(row.get("conditionId") or "").strip():
        return None

    parsed = identity._BASE_PARSE_GAMMA_MARKET(row, bucket_start=bucket)
    if parsed is None:
        return None

    expected_end_ms = (int(bucket) + 300) * 1000
    end_ms = identity._iso_ms(row.get("endDate") or row.get("endDateIso"))
    if end_ms is not None and abs(end_ms - expected_end_ms) > _STRICT_END_SKEW_MS:
        return None

    adapted = dict(row)
    actual_market_slug = str(row.get("slug") or "") or None
    adapted["gammaMarketSlug"] = actual_market_slug
    adapted["eventSlug"] = event_slug
    adapted["slug"] = event_slug
    adapted["_identityRecoveredFromEvent"] = True
    adapted["_strictUpDownValidated"] = True
    return adapted


def _strict_candidate_market(
    event: dict[str, Any],
    *,
    event_slug: str,
    bucket: int,
) -> dict[str, Any] | None:
    if str(event.get("slug") or "") != event_slug:
        return None
    markets = event.get("markets")
    if not isinstance(markets, list):
        return None

    candidates: list[tuple[int, dict[str, Any]]] = []
    expected_end_ms = (int(bucket) + 300) * 1000
    for raw in markets:
        if not isinstance(raw, dict):
            continue
        adapted = _strict_market_candidate(raw, event_slug=event_slug, bucket=bucket)
        if adapted is None:
            continue
        score = 0
        if bool(raw.get("active", True)):
            score += 4
        if not bool(raw.get("closed", False)):
            score += 4
        if bool(raw.get("enableOrderBook", True)):
            score += 2
        if bool(raw.get("acceptingOrders", True)):
            score += 1
        end_ms = identity._iso_ms(raw.get("endDate") or raw.get("endDateIso"))
        if end_ms is not None:
            score += max(0, 3 - int(abs(end_ms - expected_end_ms) // 10_000))
        candidates.append((score, adapted))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


# Tighten the event-level selector used by the previous recovery layer.  Never
# interpret an arbitrary two-token Yes/No market as UP/DOWN.
identity._candidate_market = _strict_candidate_market


def _event_exact(slug: str, bucket: int) -> dict[str, Any] | None:
    with _DISCOVERY_LOCK:
        _DISCOVERY_V2["eventExactAttempts"] += 1
    try:
        event = _BASE_TRANSPORT(identity._gamma_event_url(slug))
    except Exception as exc:
        with _DISCOVERY_LOCK:
            _DISCOVERY_V2["lastError"] = f"event exact: {str(exc)[:360]}"
        return None
    if not isinstance(event, dict):
        return None
    result = _strict_candidate_market(event, event_slug=slug, bucket=bucket)
    if result is not None:
        with _DISCOVERY_LOCK:
            _DISCOVERY_V2["eventExactHits"] += 1
    return result


def _event_list(slug: str, bucket: int) -> dict[str, Any] | None:
    """Recover from temporary event-by-slug index misses using the documented slug filter."""

    queries = (
        {"slug": slug, "active": "true", "closed": "false", "limit": 5},
        {"slug": slug, "limit": 5},
    )
    for params in queries:
        with _DISCOVERY_LOCK:
            _DISCOVERY_V2["eventListAttempts"] += 1
        try:
            payload = _BASE_TRANSPORT(
                f"{_GAMMA_EVENTS_URL}?{urllib.parse.urlencode(params)}"
            )
        except Exception as exc:
            with _DISCOVERY_LOCK:
                _DISCOVERY_V2["lastError"] = f"event list: {str(exc)[:360]}"
            continue
        rows = payload if isinstance(payload, list) else []
        for event in rows:
            if not isinstance(event, dict) or str(event.get("slug") or "") != slug:
                continue
            result = _strict_candidate_market(event, event_slug=slug, bucket=bucket)
            if result is not None:
                with _DISCOVERY_LOCK:
                    _DISCOVERY_V2["eventListHits"] += 1
                return result
    return None


def _record_discovery_success(method: str, slug: str) -> None:
    with _DISCOVERY_LOCK:
        _DISCOVERY_V2["lastMethod"] = method
        _DISCOVERY_V2["lastSlug"] = slug
        _DISCOVERY_V2["lastError"] = None
        _DISCOVERY_V2["lastSuccessAtMs"] = int(time.time() * 1000)


def _event_first_public_json(url: str, *, timeout: float = 4.0) -> Any:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    is_market_slug = (
        host == "gamma-api.polymarket.com"
        and parsed.path.startswith("/markets/slug/")
    )
    if not is_market_slug:
        return _BASE_TRANSPORT(url, timeout=timeout)

    slug = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    bucket = identity._bucket_from_slug(slug)
    if bucket is None:
        return _BASE_TRANSPORT(url, timeout=timeout)

    # The deterministic btc-updown-5m-* identifier is an event/bucket identity.
    # Resolve that object first instead of assuming it is also the market slug.
    recovered = _event_exact(slug, bucket)
    if recovered is not None:
        _record_discovery_success("EVENT_EXACT", slug)
        return recovered

    recovered = _event_list(slug, bucket)
    if recovered is not None:
        _record_discovery_success("EVENT_LIST_SLUG", slug)
        return recovered

    # Last compatibility fallback: some historical deployments used the same slug
    # for both event and market.  Accept it only when it is explicitly UP/DOWN.
    try:
        payload = _BASE_TRANSPORT(url, timeout=timeout)
    except Exception:
        raise
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if isinstance(payload, dict):
        adapted = _strict_market_candidate(payload, event_slug=slug, bucket=bucket)
        if adapted is not None:
            with _DISCOVERY_LOCK:
                _DISCOVERY_V2["marketFallbackHits"] += 1
            _record_discovery_success("STRICT_MARKET_FALLBACK", slug)
            return adapted
    raise ValueError(f"no strict current BTC 5m UP/DOWN market for {slug}")


# Prefetch resolves this module-global dynamically, so every exact discovery and
# next-market prefetch now uses event-first strict identity recovery.
prefetch._ORIGINAL_HTTP_JSON = _event_first_public_json


Collector = resilient.ResilientCrossOracleCollector
_original_init = Collector.__init__
_original_discover_market = Collector._discover_market
_original_restart_stream = Collector._restart_polymarket_stream
_original_polymarket_open = Collector._polymarket_open
_original_polymarket_message = Collector._polymarket_message
_original_apply_poly_quote = Collector._apply_poly_quote
_original_close_gap_if_fresh = Collector._close_gap_if_fresh
_original_snapshot = Collector.snapshot


def _ensure_trade_readiness(self: Any) -> None:
    if hasattr(self, "_poly_trade_ready"):
        return
    self._poly_trade_ready = False
    self._poly_trade_ready_at_ms = None
    self._poly_trade_generation = None
    self._poly_ws_session = 0
    self._poly_ws_opened_at_ms = None
    self._poly_ws_last_pong_at_ms = None
    self._poly_initial_book_timeouts = 0
    self._poly_consecutive_initial_book_timeouts = 0
    self._poly_last_book_timeout_at_ms = None
    self._poly_readiness_reason = "waiting for current-market websocket books"


def _reset_ready_locked(self: Any, reason: str, *, clear_prices: bool = False) -> None:
    _ensure_trade_readiness(self)
    self._poly_trade_ready = False
    self._poly_trade_ready_at_ms = None
    self._poly_trade_generation = self.polymarket_generation
    self._poly_readiness_reason = reason
    self.polymarket["receivedTimestampMs"] = None
    self.polymarket["sourceTimestampMs"] = None
    for side_key in ("up", "down"):
        side = self.polymarket.get(side_key)
        if not isinstance(side, dict):
            continue
        side["receivedTimestampMs"] = None
        side["sourceTimestampMs"] = None
        side["wsBookSeen"] = False
        side["wsSession"] = self._poly_ws_session
        side["lastEventType"] = None
        if clear_prices:
            side["bestBid"] = None
            side["bestAsk"] = None
            side["lastTrade"] = None
    self.polymarket["status"] = "SUBSCRIBED_WAITING_BOOK"


def _init_trade_ready(self: Any) -> None:
    _original_init(self)
    with self.lock:
        _ensure_trade_readiness(self)
        _reset_ready_locked(self, "collector start; waiting for current-market websocket books")


def _identity_tuple(market: dict[str, Any] | None) -> tuple[str, str, str, str]:
    row = market or {}
    return (
        str(row.get("slug") or ""),
        str(row.get("conditionId") or ""),
        str(row.get("upTokenId") or ""),
        str(row.get("downTokenId") or ""),
    )


def _discover_market_trade_ready(self: Any, slug: str, bucket: int) -> bool:
    with self.lock:
        before = dict(self.market or {})
        generation_before = int(self.polymarket_generation)
    ok = _original_discover_market(self, slug, bucket)
    if not ok:
        return False
    with self.lock:
        after = dict(self.market or {})
        generation_after = int(self.polymarket_generation)
    # Base discovery historically restarted only when the slug changed.  If Gamma
    # corrected condition/token identity under the same event slug, force a stream
    # restart so we never stay subscribed to an obsolete token pair for a full round.
    if (
        _identity_tuple(before) != _identity_tuple(after)
        and generation_after == generation_before
    ):
        self._restart_polymarket_stream()
    return True


def _restart_stream_trade_ready(self: Any) -> None:
    with self.lock:
        _ensure_trade_readiness(self)
        # Base increments the market generation inside the original method.
        _reset_ready_locked(
            self,
            "market/token generation changed; waiting for fresh websocket books",
            clear_prices=False,
        )
    _original_restart_stream(self)
    with self.lock:
        self._poly_trade_generation = int(self.polymarket_generation)


def _initial_book_watchdog(self: Any, generation: int, session: int) -> None:
    if self.stop_event.wait(_INITIAL_BOOK_TIMEOUT_SECONDS):
        return
    with self.lock:
        _ensure_trade_readiness(self)
        if (
            generation != self.polymarket_generation
            or session != self._poly_ws_session
            or self._poly_trade_ready
        ):
            return
        self._poly_initial_book_timeouts += 1
        self._poly_consecutive_initial_book_timeouts += 1
        self._poly_last_book_timeout_at_ms = int(time.time() * 1000)
        self._poly_readiness_reason = (
            f"no complete UP/DOWN websocket book within {_INITIAL_BOOK_TIMEOUT_SECONDS:.1f}s"
        )
        stale_ws = self.polymarket_ws
        target_slug, _ = cross.current_btc_5m_slug()
        consecutive = int(self._poly_consecutive_initial_book_timeouts)
    self._open_gap(
        "POLYMARKET_INITIAL_BOOK_TIMEOUT",
        self._poly_readiness_reason,
        target_slug,
    )
    try:
        if stale_ws is not None:
            stale_ws.close()
    except Exception:
        pass

    # Repeated failure may mean Gamma handed us a stale/corrected token pair.
    # Hard-detach it so the market supervisor re-discovers identity instead of
    # reconnecting to the same bad subscription for the remaining five minutes.
    if consecutive >= _REDISCOVER_AFTER_BOOK_TIMEOUTS:
        try:
            rollover._invalidate_stale_market_snapshot(self, target_slug)
        except Exception:
            pass


def _polymarket_open_trade_ready(self: Any, ws: Any, generation: int) -> None:
    if generation != self.polymarket_generation:
        return _original_polymarket_open(self, ws, generation)
    with self.lock:
        _ensure_trade_readiness(self)
        self._poly_ws_session += 1
        session = int(self._poly_ws_session)
        self._poly_ws_opened_at_ms = int(time.time() * 1000)
        _reset_ready_locked(
            self,
            "websocket subscribed; waiting for initial UP and DOWN book snapshots",
            clear_prices=False,
        )
        for side_key in ("up", "down"):
            side = self.polymarket.get(side_key)
            if isinstance(side, dict):
                side["wsSession"] = session
    _original_polymarket_open(self, ws, generation)
    with self.lock:
        if not self._poly_trade_ready:
            self.polymarket["status"] = "SUBSCRIBED_WAITING_BOOK"
    threading.Thread(
        target=_initial_book_watchdog,
        args=(self, generation, session),
        name=f"cross-oracle-poly-book-watchdog-{generation}-{session}",
        daemon=True,
    ).start()


def _polymarket_message_trade_ready(self: Any, ws: Any, raw: str, generation: int) -> None:
    if raw == "PONG" and generation == self.polymarket_generation:
        with self.lock:
            _ensure_trade_readiness(self)
            self._poly_ws_last_pong_at_ms = int(time.time() * 1000)
    return _original_polymarket_message(self, ws, raw, generation)


def _close_gap_trade_ready(self: Any, received_ms: int | None = None) -> None:
    with self.lock:
        _ensure_trade_readiness(self)
        ready = bool(self._poly_trade_ready)
    if not ready:
        return
    return _original_close_gap_if_fresh(self, received_ms)


def _apply_poly_quote_trade_ready(
    self: Any,
    event: dict[str, Any],
    event_type: str,
    source_ms: int | None,
    received_wall_ns: int,
    raw: str,
) -> None:
    _original_apply_poly_quote(
        self,
        event,
        event_type,
        source_ms,
        received_wall_ns,
        raw,
    )

    token_id = str(
        event.get("asset_id")
        or event.get("assetId")
        or event.get("token_id")
        or event.get("tokenId")
        or ""
    )
    received_ms = received_wall_ns // 1_000_000
    became_ready = False
    with self.lock:
        _ensure_trade_readiness(self)
        market = dict(self.market or {})
        if token_id == str(market.get("upTokenId") or ""):
            side_key = "up"
        elif token_id == str(market.get("downTokenId") or ""):
            side_key = "down"
        else:
            return
        side = self.polymarket.get(side_key)
        if not isinstance(side, dict):
            return
        side["receivedTimestampMs"] = received_ms
        side["sourceTimestampMs"] = source_ms
        side["lastEventType"] = event_type
        side["wsSession"] = int(self._poly_ws_session)
        if event_type == "book":
            side["wsBookSeen"] = True

        window_start = int(market.get("windowStartMs") or 0)
        target_slug, _ = cross.current_btc_5m_slug(received_ms / 1000.0)
        current_identity_ok = bool(
            market.get("slug") == target_slug
            and window_start > 0
            and received_ms >= window_start
        )
        up = self.polymarket.get("up") if isinstance(self.polymarket.get("up"), dict) else {}
        down = self.polymarket.get("down") if isinstance(self.polymarket.get("down"), dict) else {}
        both_books = bool(up.get("wsBookSeen") and down.get("wsBookSeen"))
        both_prices = all(
            value is not None
            for value in (
                up.get("bestBid"), up.get("bestAsk"),
                down.get("bestBid"), down.get("bestAsk"),
            )
        )
        both_receipts_current = bool(
            int(up.get("receivedTimestampMs") or 0) >= window_start
            and int(down.get("receivedTimestampMs") or 0) >= window_start
        )
        ready = current_identity_ok and both_books and both_prices and both_receipts_current
        if ready and not self._poly_trade_ready:
            self._poly_trade_ready = True
            self._poly_trade_ready_at_ms = received_ms
            self._poly_trade_generation = int(self.polymarket_generation)
            self._poly_readiness_reason = "current-market UP/DOWN websocket books confirmed"
            self._poly_consecutive_initial_book_timeouts = 0
            became_ready = True
        if ready:
            self.polymarket["status"] = "LIVE"
            self.polymarket["error"] = None
        else:
            self.polymarket["status"] = "SUBSCRIBED_WAITING_BOOK"

    if became_ready:
        self._close_gap_if_fresh(received_ms)


def _snapshot_trade_ready(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    with self.lock:
        _ensure_trade_readiness(self)
        market = dict(self.market or {})
        up = self.polymarket.get("up") if isinstance(self.polymarket.get("up"), dict) else {}
        down = self.polymarket.get("down") if isinstance(self.polymarket.get("down"), dict) else {}
        readiness = {
            "tradeReady": bool(self._poly_trade_ready),
            "readyAtMs": self._poly_trade_ready_at_ms,
            "marketGeneration": self._poly_trade_generation,
            "websocketSession": int(self._poly_ws_session),
            "websocketOpenedAtMs": self._poly_ws_opened_at_ms,
            "lastPongAtMs": self._poly_ws_last_pong_at_ms,
            "upInitialBookSeen": bool(up.get("wsBookSeen")),
            "downInitialBookSeen": bool(down.get("wsBookSeen")),
            "upReceivedTimestampMs": up.get("receivedTimestampMs"),
            "downReceivedTimestampMs": down.get("receivedTimestampMs"),
            "initialBookTimeoutSeconds": _INITIAL_BOOK_TIMEOUT_SECONDS,
            "initialBookTimeouts": int(self._poly_initial_book_timeouts),
            "consecutiveInitialBookTimeouts": int(self._poly_consecutive_initial_book_timeouts),
            "lastInitialBookTimeoutAtMs": self._poly_last_book_timeout_at_ms,
            "reason": self._poly_readiness_reason,
            "eventSlug": market.get("eventSlug") or market.get("slug"),
            "gammaMarketSlug": market.get("gammaMarketSlug"),
            "conditionId": market.get("conditionId"),
            "upTokenId": market.get("upTokenId"),
            "downTokenId": market.get("downTokenId"),
            "requiresBothWebsocketInitialBooks": True,
            "restBookPrimeAloneCanTrade": False,
            "socketOpenAloneCanTrade": False,
        }
    with _DISCOVERY_LOCK:
        discovery = dict(_DISCOVERY_V2)
    payload["tradeReadiness"] = readiness
    payload["strictMarketDiscovery"] = discovery
    poly = payload.get("polymarket")
    if isinstance(poly, dict):
        poly["tradeReady"] = readiness["tradeReady"]
        poly["tradeReadiness"] = readiness
        poly["strictMarketDiscovery"] = discovery
    continuity = payload.get("continuity")
    if isinstance(continuity, dict):
        continuity["tradeReady"] = readiness["tradeReady"]
        continuity["tradeReadiness"] = readiness
    return payload


Collector.__init__ = _init_trade_ready
Collector._discover_market = _discover_market_trade_ready
Collector._restart_polymarket_stream = _restart_stream_trade_ready
Collector._polymarket_open = _polymarket_open_trade_ready
Collector._polymarket_message = _polymarket_message_trade_ready
Collector._close_gap_if_fresh = _close_gap_trade_ready
Collector._apply_poly_quote = _apply_poly_quote_trade_ready
Collector.snapshot = _snapshot_trade_ready


def main() -> int:
    return identity.main()


if __name__ == "__main__":
    raise SystemExit(main())
