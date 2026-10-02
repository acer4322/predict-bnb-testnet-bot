from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v41 import QUOTE_EVENT_TYPES
from .poly_gap_live_v42 import TakeProfitMarketLockPolyGapLiveEngine


SOURCE_STATUS_V43 = "ENTRY_POLY_QUOTE_SOURCE_STALE_V43"


def select_quote_timestamp_pair(
    side: dict[str, Any],
    *,
    current_ws_session: int,
    fallback_source_ms: int | None,
    fallback_received_ms: int | None,
) -> tuple[int | None, int | None, str]:
    """Prefer collector-persisted quote timestamps over poll-time event inference."""

    def positive_int(value: Any) -> int | None:
        value = base._finite(value)
        return int(value) if value is not None and value > 0 else None

    explicit_source = positive_int(side.get("quoteSourceTimestampMs"))
    explicit_received = positive_int(side.get("quoteReceivedTimestampMs"))
    try:
        quote_session = int(side.get("quoteWsSession") or 0)
    except (TypeError, ValueError):
        quote_session = 0

    # A quote timestamp from an older websocket generation must never authorize
    # a BUY in the current generation. Zero is accepted only for backward
    # compatibility when the producer has not published a session marker yet.
    explicit_session_ok = quote_session in {0, int(current_ws_session or 0)}
    if explicit_source is not None and explicit_received is not None and explicit_session_ok:
        return explicit_source, explicit_received, "COLLECTOR_QUOTE_TIMESTAMP_V3"

    return fallback_source_ms, fallback_received_ms, "V41_LEGACY_POLL_INFERENCE"


class QuoteTimestampRaceFixedPolyGapLiveEngine(TakeProfitMarketLockPolyGapLiveEngine):
    """V43: consume quote-specific collector timestamps and expose the true blocker.

    V41 inferred quote freshness by polling ``up.lastEventType``. In a high-rate
    feed a valid ``price_change`` could be overwritten a few milliseconds later by
    ``last_trade_price`` before Live's next 8767 poll, so the internal quote
    timestamp failed to advance and a healthy feed was falsely classified stale.

    The V3 collector now persists quoteSourceTimestampMs/quoteReceivedTimestampMs
    independently of generic event timestamps. V43 prefers those fields and keeps
    the V41 poll-time inference only as a backward-compatible fallback.

    No trading policy is relaxed: the 750ms source-age limit remains unchanged,
    V40 immediate reversal SELL and 2s/>=0.075 cautious re-entry remain unchanged,
    V42 TAKE_PROFIT same-market lock remains unchanged, and stale source still
    fails closed for BUY only.
    """

    def _poly_state(self) -> dict[str, Any] | None:
        try:
            response = self.http.get(base.CROSS_ORACLE_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.last_error = f"Poly state: {str(exc)[:300]}"
            return None
        if not isinstance(payload, dict):
            return None
        continuity = payload.get("continuity")
        if isinstance(continuity, dict) and continuity.get("gapActive") is True:
            self.last_error = "Polymarket continuity gap active"
            return None
        poly = payload.get("polymarket")
        if not isinstance(poly, dict) or str(poly.get("status")) != "LIVE":
            self.last_error = "Polymarket feed is not LIVE"
            return None
        receipt_age = base._finite(poly.get("ageMs"))
        if receipt_age is None or receipt_age > base.MAX_POLY_AGE_MS:
            self.last_error = f"Polymarket quote stale: {receipt_age}ms"
            return None

        up = poly.get("up")
        down = poly.get("down")
        market = poly.get("market")
        if not isinstance(up, dict) or not isinstance(down, dict) or not isinstance(market, dict):
            return None
        up_mid = base.probability_mid(up.get("bestBid"), up.get("bestAsk"))
        if up_mid is None:
            return None
        direction = base.probability_direction(up_mid)

        now_ms = base._now_ms()
        slug = str(market.get("slug") or "")
        try:
            ws_session = int(up.get("wsSession") or 0)
        except (TypeError, ValueError):
            ws_session = 0
        identity = (slug, ws_session)
        event_type = str(up.get("lastEventType") or "").strip().lower()

        # Keep V41's legacy inference up to date for mixed-version restarts, but
        # never let last_trade_price erase a quote timestamp once observed.
        with self._v41_guard_lock:
            if self._v41_quote_identity != identity:
                self._v41_quote_identity = identity
                self._v41_quote_source_timestamp_ms = None
                self._v41_quote_received_timestamp_ms = None
            if event_type in QUOTE_EVENT_TYPES:
                source_ms = self._int_ms(up.get("sourceTimestampMs"))
                received_ms = self._int_ms(up.get("receivedTimestampMs"))
                if source_ms is not None and received_ms is not None:
                    self._v41_quote_source_timestamp_ms = source_ms
                    self._v41_quote_received_timestamp_ms = received_ms
            fallback_source = self._v41_quote_source_timestamp_ms
            fallback_received = self._v41_quote_received_timestamp_ms

        signal_source_ms, signal_received_ms, freshness_source = select_quote_timestamp_pair(
            up,
            current_ws_session=ws_session,
            fallback_source_ms=fallback_source,
            fallback_received_ms=fallback_received,
        )
        source_age_ms = now_ms - signal_source_ms if signal_source_ms is not None else None
        quote_receipt_age_ms = now_ms - signal_received_ms if signal_received_ms is not None else None
        transport_age_ms = (
            signal_received_ms - signal_source_ms
            if signal_source_ms is not None and signal_received_ms is not None
            else None
        )
        result = {
            "upMid": up_mid,
            "direction": direction,
            "selectedMid": base.selected_probability(up_mid, direction) if direction else None,
            "ageMs": receipt_age,
            "slug": market.get("slug"),
            "windowEndMs": int(market.get("windowEndMs") or 0),
            "receivedTimestampMs": poly.get("receivedTimestampMs"),
            "sourceTimestampMs": poly.get("sourceTimestampMs"),
            "gapGeneration": (
                (continuity or {}).get("gapGeneration")
                if isinstance(continuity, dict)
                else None
            ),
            "signalSourceTimestampMs": signal_source_ms,
            "signalQuoteReceivedTimestampMs": signal_received_ms,
            "signalSourceAgeMs": source_age_ms,
            "signalQuoteReceiptAgeMs": quote_receipt_age_ms,
            "signalTransportAgeMs": transport_age_ms,
            "signalLastObservedEventType": event_type or None,
            "signalQuoteEventTypes": sorted(QUOTE_EVENT_TYPES),
            "signalFreshnessTimestampSource": freshness_source,
            "collectorQuoteEventType": up.get("quoteEventType"),
            "collectorQuoteWsSession": up.get("quoteWsSession"),
        }
        self.last_poly = result
        return result

    def _refresh_reentry_confirmation(self) -> str:
        result = super()._refresh_reentry_confirmation()
        policy, detail = self._source_policy()
        if policy != "ALLOW":
            # V41 already fail-closes and resets the timer. Mark the tick so its
            # outer status-restoration logic cannot let base ask=None overwrite
            # the real reason with the misleading WAITING_BINANCE_BOOK label.
            self._v41_source_blocked_this_tick = True
            self.status = SOURCE_STATUS_V43
            self.last_error = self._block_message("REVERSAL_REENTRY_CONFIRM", policy, detail)
        return result

    def _tick(self) -> None:
        result = super()._tick()
        if self._v41_source_blocked_this_tick:
            self.status = SOURCE_STATUS_V43
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V43"
        source = payload.get("sourceFreshnessV41")
        if isinstance(source, dict):
            current = source.get("current")
            if isinstance(current, dict):
                current["timestampSource"] = (self.last_poly or {}).get(
                    "signalFreshnessTimestampSource"
                )
            source["quoteTimestampRaceFixedV43"] = True
            source["collectorQuoteTimestampPreferred"] = True
            source["legacyPollInferenceFallbackOnly"] = True
            source["misleadingWaitingBinanceBookOnSourceBlockFixed"] = True
        payload["quoteTimestampRaceFixV43"] = {
            "enabled": True,
            "collectorProducer": "cross_oracle_storage_retention_v3",
            "preferredFields": ["quoteSourceTimestampMs", "quoteReceivedTimestampMs"],
            "lastTradeCannotOverwriteQuoteFreshness": True,
            "sourceAgeThresholdChanged": False,
            "v40ImmediateReversalSellPreserved": True,
            "v40CautiousReentryPreserved": True,
            "v42TakeProfitMarketLockPreserved": True,
            "sourceBlockStatus": SOURCE_STATUS_V43,
        }
        payload.setdefault("rules", {}).update(
            polyQuoteTimestampRaceFixedV43=True,
            polyQuoteSpecificTimestampProducerRequiredV43=True,
            polySourceAgeThresholdUnchangedV43=True,
            reversalImmediateSellV40PreservedByV43=True,
            reversalCautiousReentryV40PreservedByV43=True,
            takeProfitSameMarketLockV42PreservedByV43=True,
        )
        return payload


base.PolyGapLiveEngine = QuoteTimestampRaceFixedPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
