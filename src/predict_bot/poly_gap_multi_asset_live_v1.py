from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v38 import LEADER_GUARD_OFF
from .poly_gap_live_v41 import QUOTE_EVENT_TYPES
from .poly_gap_live_v43 import select_quote_timestamp_pair
from .poly_gap_live_v44 import IntegratedDecisionAndIdempotencyPolyGapLiveEngine


SUPPORTED_ASSETS = {
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
}
ASSET = os.environ.get("PREDICT_POLY_GAP_LIVE_ASSET", "").strip().upper()
if ASSET not in SUPPORTED_ASSETS:
    raise RuntimeError(
        "PREDICT_POLY_GAP_LIVE_ASSET must be ETH or BNB for the multi-asset live executor"
    )
SYMBOL = os.environ.get("PREDICT_POLY_GAP_LIVE_SYMBOL", SUPPORTED_ASSETS[ASSET]).strip().upper()
MULTI_OBSERVER_URL = os.environ.get(
    "PREDICT_MULTI_PREDICTION_STATE_URL",
    "http://127.0.0.1:8770/state",
)


def _positive_int(value: Any) -> int | None:
    number = base._finite(value)
    return int(number) if number is not None and number > 0 else None


class MultiAssetPolyGapLiveEngine(IntegratedDecisionAndIdempotencyPolyGapLiveEngine):
    """V44 policy lineage adapted to an isolated ETH/BNB five-minute market.

    The live execution policy is intentionally inherited rather than copied:
    V28 price controls/take-profit, V31 same-market reversal breaker, V32 FOK
    depth, V34/V35 exit telemetry/commitment, V36 bounded BUY retry, V37 Shotgun,
    V39 post-quote edge, V40 immediate reversal SELL/cautious re-entry,
    V41/V43 source freshness, V42 TAKE_PROFIT same-market lock and V44 execution
    idempotency all remain active.

    Only asset identity and signal plumbing change.  ETH/BNB read their Poly
    market from the dedicated 8770 multi-market observer and discover their own
    Binance Prediction market by symbol.  BTC-only Paper CHOP/leader state is
    deliberately not allowed to authorize or kill ETH/BNB exposure.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.asset = ASSET
        self.symbol = SYMBOL
        self._asset_guard_checks = 0
        super().__init__(*args, **kwargs)

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        # V38's 8768 leader regime is currently BTC-only.  Persist OFF every
        # startup so an old/copied DB can never make an ETH/BNB decision from BTC.
        self._set_setting("leader_guard_mode", LEADER_GUARD_OFF)

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings["leaderGuardMode"] = LEADER_GUARD_OFF
        settings["leaderGuardAssetCompatible"] = False
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        forwarded = dict(values)
        if "leaderGuardMode" in forwarded:
            requested = str(forwarded.pop("leaderGuardMode") or LEADER_GUARD_OFF).strip().upper()
            if requested != LEADER_GUARD_OFF:
                raise ValueError(
                    f"leaderGuardMode is BTC-only today; {self.asset} must remain OFF until asset-specific leader research exists"
                )
        result = super().update_settings(forwarded) if forwarded else self.snapshot()
        self._set_setting("leader_guard_mode", LEADER_GUARD_OFF)
        return result

    def _refresh_chop_guard(self, *, force: bool = False) -> dict[str, Any]:
        # V27/V33 read the BTC 8768 strategy DB.  Do not leak that market regime
        # into another asset.  ETH/BNB still retain their own persistent Live
        # same-market completed-reversal breaker and maximum-loss controls.
        self._asset_guard_checks += 1
        state = {
            "verified": True,
            "blocked": False,
            "persistentPaused": False,
            "currentMarketChoppy": None,
            "reason": (
                f"BTC Paper CHOP guard is intentionally isolated from {self.asset}; "
                "asset-specific Live reversal breaker remains authoritative"
            ),
            "checkedAtMs": base._now_ms(),
            "error": None,
            "source": "MULTI_ASSET_LIVE_ISOLATION_V1",
            "degraded": False,
            "asset": self.asset,
            "btcPaperGuardApplied": False,
        }
        self.last_chop_guard = dict(state)
        return state

    def _asset_observer_state(self) -> dict[str, Any] | None:
        try:
            response = self.http.get(MULTI_OBSERVER_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.last_error = f"{self.asset} observer state: {str(exc)[:300]}"
            return None
        if not isinstance(payload, dict):
            return None
        root = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        assets = root.get("assets") if isinstance(root, dict) else None
        row = assets.get(self.asset) if isinstance(assets, dict) else None
        return row if isinstance(row, dict) else None

    def _poly_state(self) -> dict[str, Any] | None:
        asset_state = self._asset_observer_state()
        if not isinstance(asset_state, dict):
            return None
        poly = asset_state.get("poly")
        if not isinstance(poly, dict) or str(poly.get("status") or "").upper() != "LIVE":
            self.last_error = f"{self.asset} Polymarket feed is not LIVE"
            return None
        up = poly.get("up")
        down = poly.get("down")
        market = poly.get("market")
        if not isinstance(up, dict) or not isinstance(down, dict) or not isinstance(market, dict):
            self.last_error = f"{self.asset} Polymarket market/quote metadata incomplete"
            return None

        up_mid = base.probability_mid(up.get("bestBid"), up.get("bestAsk"))
        if up_mid is None:
            self.last_error = f"{self.asset} Polymarket UP mid unavailable"
            return None
        direction = base.probability_direction(up_mid)
        now_ms = base._now_ms()
        try:
            ws_session = int(up.get("wsSession") or 0)
        except (TypeError, ValueError):
            ws_session = 0
        source_ms, received_ms, timestamp_source = select_quote_timestamp_pair(
            up,
            current_ws_session=ws_session,
            fallback_source_ms=_positive_int(up.get("quoteSourceTimestampMs") or up.get("sourceTimestampMs")),
            fallback_received_ms=_positive_int(up.get("quoteReceivedTimestampMs") or up.get("receivedTimestampMs")),
        )
        receipt_age = now_ms - received_ms if received_ms is not None else None
        if receipt_age is None or receipt_age > base.MAX_POLY_AGE_MS:
            self.last_error = f"{self.asset} Polymarket quote stale: {receipt_age}ms"
            return None
        source_age = now_ms - source_ms if source_ms is not None else None
        transport_age = (
            received_ms - source_ms
            if source_ms is not None and received_ms is not None
            else None
        )
        event_type = str(up.get("quoteEventType") or up.get("lastEventType") or "").strip().lower()
        window_end_ms = int(asset_state.get("windowEndMs") or 0)
        slug = str(
            market.get("eventSlug")
            or market.get("gammaMarketSlug")
            or market.get("slug")
            or ""
        )
        result = {
            "asset": self.asset,
            "upMid": up_mid,
            "direction": direction,
            "selectedMid": base.selected_probability(up_mid, direction) if direction else None,
            "ageMs": receipt_age,
            "slug": slug or None,
            "windowEndMs": window_end_ms,
            "receivedTimestampMs": received_ms,
            "sourceTimestampMs": source_ms,
            "gapGeneration": None,
            "signalSourceTimestampMs": source_ms,
            "signalQuoteReceivedTimestampMs": received_ms,
            "signalSourceAgeMs": source_age,
            "signalQuoteReceiptAgeMs": receipt_age,
            "signalTransportAgeMs": transport_age,
            "signalLastObservedEventType": event_type or None,
            "signalQuoteEventTypes": sorted(QUOTE_EVENT_TYPES),
            "signalFreshnessTimestampSource": f"8770_{timestamp_source}",
            "collectorQuoteEventType": up.get("quoteEventType") or up.get("lastEventType"),
            "collectorQuoteWsSession": up.get("quoteWsSession") or up.get("wsSession"),
        }
        self.last_poly = result
        return result

    def _prime_market(self, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        with self.lock:
            cached = dict(self.market_cache or {})
            metadata = self.metadata_client
        now_ms = base._now_ms()
        if not force and cached and int(cached.get("end_ms") or 0) > now_ms + 500:
            return cached
        if not force and now - self.last_market_refresh < base.MARKET_REFRESH_SECONDS:
            return cached or None
        self.last_market_refresh = now
        if metadata is None and not self._ensure_clients():
            return None
        with self.lock:
            metadata = self.metadata_client
        if metadata is None:
            return None

        summary = metadata.find_market_summary(self.symbol, max_pages=5)
        if not isinstance(summary, dict):
            self.last_error = f"Binance Prediction current {self.symbol} 5m market unavailable"
            return None
        selected = summary.get("_selectedMarket")
        if not isinstance(selected, dict):
            return None
        market = selected.get("market")
        up = selected.get("up")
        down = selected.get("down")
        if not isinstance(market, dict) or not isinstance(up, dict) or not isinstance(down, dict):
            return None
        cache = {
            "asset": self.asset,
            "symbol": self.symbol,
            "market_id": int(market.get("marketId") or 0),
            "topic_id": int(summary.get("marketTopicId") or 0),
            "up_token_id": str(up.get("tokenId") or ""),
            "down_token_id": str(down.get("tokenId") or ""),
            "fee_rate_bps": int(summary.get("feeRateBps") or market.get("feeRateBps") or 200),
            "end_ms": int(summary.get("endDate") or 0),
        }
        if cache["market_id"] <= 0 or not cache["up_token_id"] or not cache["down_token_id"]:
            self.last_error = f"Binance Prediction current {self.asset} market token metadata incomplete"
            return None
        with self.lock:
            self.market_cache = cache
        if self.halted_market_id is not None and self.halted_market_id != cache["market_id"]:
            self.halted_market_id = None
            self.halted_reason = None
        return dict(cache)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_MULTI_ASSET_LIVE_V1"
        payload["asset"] = self.asset
        payload["symbol"] = self.symbol
        payload["signalVenue"] = "POLYMARKET"
        payload["executionVenue"] = "BINANCE_PREDICTION"
        payload["assetIsolationV1"] = {
            "enabled": True,
            "databaseIsolated": True,
            "marketIdentityIsolated": True,
            "source": MULTI_OBSERVER_URL,
            "btcPaperGuardApplied": False,
            "btcLeaderGuardApplied": False,
            "liveSameMarketReversalBreakerPreserved": True,
            "maximumLossGuardPreserved": True,
            "takeProfitPreserved": True,
            "v40ImmediateReversalSellPreserved": True,
            "v40CautiousReentryPreserved": True,
            "v41V43SourceFreshnessPreserved": True,
            "v42TakeProfitMarketLockPreserved": True,
            "v44ExecutionIdempotencyPreserved": True,
            "paperGuardIsolationChecks": int(self._asset_guard_checks),
        }
        payload.setdefault("rules", {}).update(
            multiAssetLiveV1=True,
            asset=self.asset,
            symbol=self.symbol,
            btcPaperGuardAppliedToThisAsset=False,
            btcLeaderGuardAppliedToThisAsset=False,
        )
        return payload


base.PolyGapLiveEngine = MultiAssetPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
