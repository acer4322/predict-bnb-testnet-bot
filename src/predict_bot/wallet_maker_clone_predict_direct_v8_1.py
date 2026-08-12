from __future__ import annotations

import time
from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_predict_direct_v8 as base
from .predict_fun_observer import (
    ASSETS as PREDICT_ASSETS,
    current_bucket,
    expected_slug,
    normalize_predict_category_payload,
    select_predict_market,
)


class PredictDirectV81WalletMakerCloneEngine(base.PredictDirectV8WalletMakerCloneEngine):
    """Predict Direct V8.1: reuse the proven observer discovery path.

    The first Predict-direct V8 attempted to discover current ETH/BNB 5m markets
    by scanning GET /v1/markets and parsing a title format. Predict's live crypto
    markets are more reliably identified by their exact epoch category slug
    (eth-updown-5m-{bucket} / bnb-updown-5m-{bucket}), which is already how the
    read-only Predict.fun observer in this repository discovers them.

    V8.1 changes discovery only. All V8 execution/risk rules stay inherited:
    native post-only LIMIT orders, paired-cycle replenishment, configurable entry
    cap and worst-case loss stop, no new pair at T-60, and hard cancellation at
    T-30 with final reconciliation.
    """

    VERSION = "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_1_DISCOVERY_FIXED"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.discovery_source: str | None = None
        self.discovery_slug: str | None = None
        self.discovery_error: str | None = None
        super().__init__(*args, **kwargs)

    def _discover_current_market(self, now_ms: int) -> dict[str, Any] | None:
        client = self.direct_client
        if client is None:
            return None

        asset = str(core.ASSET).upper()
        if asset not in PREDICT_ASSETS:
            self.discovery_error = f"unsupported Predict Direct discovery asset {asset}"
            return None

        bucket = current_bucket(now_ms / 1000.0)
        slug = expected_slug(asset, bucket)
        self.discovery_slug = slug
        exact_error: Exception | None = None

        try:
            exact_payload = client._request("GET", f"/v1/categories/{slug}", auth=False)
            normalized = normalize_predict_category_payload(exact_payload, slug=slug)
            if normalized is not None:
                candidate = select_predict_market(
                    normalized,
                    asset=asset,
                    bucket=bucket,
                    now_ms=now_ms,
                )
                if candidate is not None:
                    candidate["discoverySource"] = "CATEGORY_SLUG"
                    self.discovery_source = "CATEGORY_SLUG"
                    self.discovery_error = None
                    return candidate
        except base.PredictDirectApiError as exc:
            if exc.status_code != 404:
                exact_error = exc
        except Exception as exc:
            exact_error = exc

        try:
            search_payload = client._request(
                "GET",
                "/v1/search",
                auth=False,
                params={
                    "query": PREDICT_ASSETS[asset]["query"],
                    "includeResolved": "false",
                    "limit": 20,
                },
            )
            candidate = select_predict_market(
                search_payload,
                asset=asset,
                bucket=bucket,
                now_ms=now_ms,
            )
            if candidate is not None:
                candidate["discoverySource"] = "SEARCH_FALLBACK"
                self.discovery_source = "SEARCH_FALLBACK"
                self.discovery_error = (
                    f"exact category failed: {str(exact_error)[:220]}"
                    if exact_error is not None
                    else None
                )
                return candidate
        except Exception as search_error:
            if exact_error is not None:
                self.discovery_error = (
                    f"exact category failed: {str(exact_error)[:220]}; "
                    f"search fallback failed: {str(search_error)[:220]}"
                )
            else:
                self.discovery_error = f"search fallback failed: {str(search_error)[:300]}"
            return None

        self.discovery_source = None
        self.discovery_error = (
            f"no strict current Predict.fun {asset} 5m market for {slug}"
            if exact_error is None
            else f"exact category failed: {str(exact_error)[:220]}; search found no strict current market"
        )
        return None

    def _prime_market(self) -> dict[str, Any] | None:
        if not self._ensure_client():
            return None

        now_ms = core._now_ms()
        bucket = current_bucket(now_ms / 1000.0)
        cached = self.market if isinstance(self.market, dict) else None
        if cached:
            cached_bucket = int(cached.get("bucket_start_sec") or 0)
            end_ms = int(cached.get("end_ms") or 0)
            if (
                cached_bucket == bucket
                and now_ms < end_ms
                and time.monotonic() - self.last_market_refresh < base.DIRECT_MARKET_REFRESH_SECONDS
            ):
                return dict(cached)

        self.last_market_refresh = time.monotonic()
        candidate = self._discover_current_market(now_ms)
        if candidate is None:
            self.last_error = self.discovery_error or (
                f"current Predict Direct {core.ASSET} exact 5m market unavailable"
            )
            return None

        client = self.direct_client
        assert client is not None
        market_id = int(candidate.get("id") or 0)
        if market_id <= 0:
            self.last_error = "Predict Direct discovery returned invalid market id"
            return None

        try:
            detail_payload = client._request("GET", f"/v1/markets/{market_id}", auth=False)
        except Exception as exc:
            self.last_error = f"Predict Direct market detail {market_id}: {str(exc)[:400]}"
            return None
        selected = (
            detail_payload.get("data")
            if isinstance(detail_payload.get("data"), dict)
            else detail_payload
        )
        if not isinstance(selected, dict):
            self.last_error = f"Predict Direct market detail {market_id} returned invalid data"
            return None
        if str(selected.get("tradingStatus") or "").upper() not in {"OPEN", "TRADING"}:
            self.last_error = (
                f"Predict Direct discovered market {market_id} is not open: "
                f"{selected.get('tradingStatus')}"
            )
            return None

        outcomes = base._outcome_map(selected)
        if outcomes is None:
            names = [
                str(row.get("name") or "")
                for row in (selected.get("outcomes") or [])
                if isinstance(row, dict)
            ]
            self.last_error = (
                f"Predict Direct market {market_id} outcome metadata cannot map UP/DOWN; "
                f"outcomes={names}"
            )
            return None
        outcome_map, yes_outcome = outcomes
        up = outcome_map["UP"]
        down = outcome_map["DOWN"]

        start_ms = int(candidate.get("windowStartMs") or (bucket * 1000))
        end_ms = int(candidate.get("windowEndMs") or ((bucket + 300) * 1000))
        if not (start_ms <= now_ms < end_ms):
            self.last_error = (
                f"Predict Direct discovered market {market_id} outside current window: "
                f"start={start_ms} now={now_ms} end={end_ms}"
            )
            return None

        market = {
            "asset": core.ASSET,
            "symbol": core.SYMBOL,
            "market_id": market_id,
            "topic_id": market_id,
            "bucket_start_sec": bucket,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "up_token_id": str(up.get("onChainId") or up.get("tokenId") or ""),
            "down_token_id": str(down.get("onChainId") or down.get("tokenId") or ""),
            "yes_outcome": yes_outcome,
            "fee_rate_bps": int(selected.get("feeRateBps") or 0),
            "precision": int(selected.get("decimalPrecision") or candidate.get("decimalPrecision") or 2),
            "is_neg_risk": bool(selected.get("isNegRisk")),
            "is_yield_bearing": bool(selected.get("isYieldBearing")),
            "condition_id": str(selected.get("conditionId") or ""),
            "market_title": str(selected.get("title") or selected.get("question") or candidate.get("title") or ""),
            "category_slug": str(selected.get("categorySlug") or candidate.get("categorySlug") or self.discovery_slug or ""),
            "discovery_source": str(candidate.get("discoverySource") or self.discovery_source or ""),
        }
        if not market["up_token_id"] or not market["down_token_id"]:
            self.last_error = f"Predict Direct market {market_id} UP/DOWN onChainId metadata incomplete"
            return None

        self.market = dict(market)
        self.last_error = self.discovery_error
        self._refresh_approval_state(market)
        return market

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        direct = payload.setdefault("predictDirect", {})
        direct.update(
            discoveryModel="EXACT_EPOCH_CATEGORY_SLUG_WITH_SEARCH_FALLBACK",
            discoverySource=self.discovery_source,
            discoverySlug=self.discovery_slug,
            discoveryError=self.discovery_error,
        )
        payload.setdefault("rules", {}).update(
            predictDirectDiscoveryV81=True,
            brittleTitleParserUsedForLiveDiscovery=False,
        )
        return payload


def main() -> int:
    engine = PredictDirectV81WalletMakerCloneEngine()
    engine.start()
    handler = type("PredictDirectV81WalletMakerCloneHandler", (core._Handler,), {"engine": engine})
    server = core.ThreadingHTTPServer((core.HOST, core.PORT), handler)
    print(
        f"{engine.VERSION} {core.ASSET} listening on http://{core.HOST}:{core.PORT}/state; "
        f"venue=PREDICT_DIRECT; masterEnabled={core.MASTER_ENABLED}; db={core.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
