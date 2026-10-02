from __future__ import annotations

import time
from typing import Any

from . import cross_oracle_transport_hardening as transport


resilient = transport.resilient
cross = resilient.cross_oracle_module


def _ensure_rollover_guard_state(collector: Any) -> None:
    if not hasattr(collector, "_rollover_invalidated_target_slug"):
        collector._rollover_invalidated_target_slug = None
    if not hasattr(collector, "_rollover_invalidation_count"):
        collector._rollover_invalidation_count = 0
    if not hasattr(collector, "_last_rollover_invalidated_at_ms"):
        collector._last_rollover_invalidated_at_ms = None
    if not hasattr(collector, "_last_rollover_invalidated_from_slug"):
        collector._last_rollover_invalidated_from_slug = None


def _invalidate_stale_market_snapshot(self: Any, target_slug: str) -> None:
    """Detach the previous five-minute Poly market before Gamma finds the next one.

    The old collector intentionally kept the previous market and its last quote in
    memory while Gamma discovery retried. That is safe only if every downstream
    consumer checks market identity perfectly. In practice it also made `Poly age`
    climb forever and let dashboards render the previous market's settlement quote
    beside the new Binance market.

    Rollover must therefore be a hard generation boundary: old websocket callbacks
    become invalid immediately, old prices/timestamps disappear from `/state`, and
    the collector remains fail-closed until the new exact slug is discovered and a
    fresh quote for its token IDs arrives.
    """

    _ensure_rollover_guard_state(self)
    with self.lock:
        if self._rollover_invalidated_target_slug == target_slug:
            return

        previous_slug = None
        if isinstance(self.market, dict):
            previous_slug = self.market.get("slug")
        previous_slug = str(previous_slug) if previous_slug else None

        self._rollover_invalidated_target_slug = target_slug
        self._rollover_invalidation_count += 1
        self._last_rollover_invalidated_at_ms = int(time.time() * 1000)
        self._last_rollover_invalidated_from_slug = previous_slug

        # Bump generation before closing the socket so any racing callback from
        # the previous token pair is ignored by _polymarket_message immediately.
        self.polymarket_generation += 1
        stale_ws = self.polymarket_ws
        self.polymarket_ws = None

        # No current market is better than a wrong current market. Historical data
        # remains in SQLite; this clears only the live snapshot used by strategy/UI.
        self.market = None
        self.polymarket["up"] = {
            "tokenId": None,
            "bestBid": None,
            "bestAsk": None,
            "lastTrade": None,
        }
        self.polymarket["down"] = {
            "tokenId": None,
            "bestBid": None,
            "bestAsk": None,
            "lastTrade": None,
        }
        self.polymarket["receivedTimestampMs"] = None
        self.polymarket["sourceTimestampMs"] = None
        self.polymarket["startPrice"] = None
        self.polymarket["startPriceTimestampMs"] = None
        self.polymarket["startPriceOffsetMs"] = None
        self.polymarket["status"] = "WAITING_MARKET"
        self.polymarket["error"] = None
        self.polymarket["marketDiscovery"] = {
            "status": "ROLLOVER_INVALIDATED",
            "slug": target_slug,
            "checkedAtMs": self._last_rollover_invalidated_at_ms,
            "detail": "previous market detached; waiting for exact current 5m Gamma market",
        }

    try:
        if stale_ws is not None:
            stale_ws.close()
    except Exception:
        pass


def _market_supervisor_rollover_guarded(self: Any) -> None:
    _ensure_rollover_guard_state(self)
    while not self.stop_event.is_set():
        slug, bucket = cross.current_btc_5m_slug()
        current_slug = self._market_slug()
        if current_slug != slug:
            self._open_gap(
                "MARKET_ROLLOVER_DISCOVERY",
                f"need current market {slug}; have {current_slug or 'none'}",
                slug,
            )
            _invalidate_stale_market_snapshot(self, slug)
            if self._discover_market(slug, bucket):
                with self.lock:
                    self._rollover_invalidated_target_slug = None
            self.stop_event.wait(resilient.DISCOVERY_RETRY_SECONDS)
            continue
        with self.lock:
            self._rollover_invalidated_target_slug = None
        self.stop_event.wait(resilient.DISCOVERY_RETRY_SECONDS)


_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _snapshot_rollover_guarded(self: Any) -> dict[str, Any]:
    _ensure_rollover_guard_state(self)
    payload = _original_snapshot(self)
    with self.lock:
        guard = {
            "hardInvalidateOnBucketChange": True,
            "invalidatedTargetSlug": self._rollover_invalidated_target_slug,
            "invalidationCount": int(self._rollover_invalidation_count),
            "lastInvalidatedAtMs": self._last_rollover_invalidated_at_ms,
            "lastInvalidatedFromSlug": self._last_rollover_invalidated_from_slug,
            "stalePricesRetainedDuringGammaWait": False,
            "staleTimestampRetainedDuringGammaWait": False,
        }
    payload["rolloverGuard"] = guard
    continuity = payload.get("continuity")
    if isinstance(continuity, dict):
        continuity["rolloverGuard"] = guard
    polymarket = payload.get("polymarket")
    if isinstance(polymarket, dict):
        polymarket["rolloverGuard"] = guard
        poly_continuity = polymarket.get("continuity")
        if isinstance(poly_continuity, dict):
            poly_continuity["rolloverGuard"] = guard
    return payload


resilient.ResilientCrossOracleCollector._market_supervisor = _market_supervisor_rollover_guarded
resilient.ResilientCrossOracleCollector.snapshot = _snapshot_rollover_guarded


def main() -> int:
    return transport.main()


if __name__ == "__main__":
    raise SystemExit(main())
