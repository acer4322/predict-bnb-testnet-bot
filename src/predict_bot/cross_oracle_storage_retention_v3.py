from __future__ import annotations

import json
import time
from typing import Any

from . import cross_oracle_resilient as resilient
from . import cross_oracle_storage_retention_v2 as v2


Collector = v2.Collector
QUOTE_EVENT_TYPES = {"book", "price_change", "best_bid_ask"}

_original_apply_poly_quote = Collector._apply_poly_quote
_original_restart_stream = Collector._restart_polymarket_stream
_original_snapshot = Collector.snapshot
_original_handler_do_get = resilient._Handler.do_GET


def _clear_quote_freshness_locked(self: Any) -> None:
    for side_key in ("up", "down"):
        side = self.polymarket.get(side_key)
        if not isinstance(side, dict):
            continue
        side["quoteSourceTimestampMs"] = None
        side["quoteReceivedTimestampMs"] = None
        side["quoteEventType"] = None
        side["quoteWsSession"] = getattr(self, "_poly_ws_session", None)


def _restart_polymarket_stream_v3(self: Any) -> None:
    with self.lock:
        _clear_quote_freshness_locked(self)
    return _original_restart_stream(self)


def _apply_poly_quote_v3(
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

    normalized_type = str(event_type or "").strip().lower()
    if normalized_type not in QUOTE_EVENT_TYPES:
        return

    token_id = str(
        event.get("asset_id")
        or event.get("assetId")
        or event.get("token_id")
        or event.get("tokenId")
        or ""
    )
    received_ms = int(received_wall_ns // 1_000_000)
    with self.lock:
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
        # These fields are quote-specific and intentionally survive later
        # last_trade_price events. Generic sourceTimestampMs/lastEventType remain
        # useful event diagnostics but must not be used as quote freshness.
        side["quoteSourceTimestampMs"] = source_ms
        side["quoteReceivedTimestampMs"] = received_ms
        side["quoteEventType"] = normalized_type
        side["quoteWsSession"] = getattr(self, "_poly_ws_session", None)


def _snapshot_v3(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    poly = payload.get("polymarket")
    if isinstance(poly, dict):
        up = poly.get("up") if isinstance(poly.get("up"), dict) else {}
        down = poly.get("down") if isinstance(poly.get("down"), dict) else {}
        poly["quoteFreshnessV3"] = {
            "enabled": True,
            "quoteEventTypes": sorted(QUOTE_EVENT_TYPES),
            "lastTradeDoesNotOverwriteQuoteTimestamp": True,
            "upQuoteSourceTimestampMs": up.get("quoteSourceTimestampMs"),
            "upQuoteReceivedTimestampMs": up.get("quoteReceivedTimestampMs"),
            "upQuoteEventType": up.get("quoteEventType"),
            "downQuoteSourceTimestampMs": down.get("quoteSourceTimestampMs"),
            "downQuoteReceivedTimestampMs": down.get("quoteReceivedTimestampMs"),
            "downQuoteEventType": down.get("quoteEventType"),
        }
    payload.setdefault("storage", {})["quoteFreshnessProducerV3"] = {
        "enabled": True,
        "quoteTimestampIsIndependentOfLastTrade": True,
        "clearedOnWebsocketGenerationRestart": True,
    }
    return payload


def _client_disconnected(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054}


def _lightweight_health(collector: Any) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    with collector.lock:
        chainlink = dict(getattr(collector, "chainlink", {}) or {})
        polymarket = dict(getattr(collector, "polymarket", {}) or {})
        market = dict(getattr(collector, "market", {}) or {})
        gap_active = bool(getattr(collector, "gap_active", False))
        gap_generation = int(getattr(collector, "gap_generation", 0) or 0)
    chainlink_status = str(chainlink.get("status") or "")
    polymarket_status = str(polymarket.get("status") or "")
    return {
        "ok": True,
        "status": "ONLINE",
        "version": "CROSS_ORACLE_STORAGE_RETENTION_V3_LIGHTWEIGHT_HEALTH_V1",
        "generatedAtMs": now_ms,
        "processAlive": True,
        "startedAtMs": getattr(collector, "started_at_ms", None),
        "chainlinkStatus": chainlink_status,
        "polymarketStatus": polymarket_status,
        "marketSlug": market.get("slug"),
        "gapActive": gap_active,
        "gapGeneration": gap_generation,
        "fullStatePath": "/state",
        "healthDoesNotBuildSnapshot": True,
    }


def _do_get_v3(self: Any) -> None:
    request_path = self.path.split("?", 1)[0]
    if request_path == "/health":
        body = json.dumps(
            _lightweight_health(self.collector),
            allow_nan=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BaseException as exc:
            if not _client_disconnected(exc):
                raise
        return

    try:
        return _original_handler_do_get(self)
    except BaseException as exc:
        # A polling client that times out or navigates away can close its socket
        # while /state is still being serialized. This is benign and must not
        # flood the shared supervisor stderr with WinError 10053/10054 traces.
        if _client_disconnected(exc):
            return
        raise


Collector._restart_polymarket_stream = _restart_polymarket_stream_v3
Collector._apply_poly_quote = _apply_poly_quote_v3
Collector.snapshot = _snapshot_v3
resilient._Handler.do_GET = _do_get_v3


def main() -> int:
    return v2.main()


if __name__ == "__main__":
    raise SystemExit(main())
