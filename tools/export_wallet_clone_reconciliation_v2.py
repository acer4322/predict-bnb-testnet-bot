from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import export_wallet_clone_reconciliation as base  # noqa: E402


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _historical_market_from_category(payload: dict[str, Any], *, asset: str, slug: str) -> dict[str, Any] | None:
    """Select a Predict market from a category without requiring tradingStatus=OPEN.

    The live observer selector intentionally rejects resolved markets. Reconciliation
    is historical, so a closed/resolved 5m category must remain mappable after the
    market rolls over.
    """
    body = _record(payload)
    category = _record(body.get("data")) if isinstance(body.get("data"), dict) else body
    markets = category.get("markets") if isinstance(category.get("markets"), list) else []
    asset = str(asset).upper()
    slug = str(slug).lower()

    candidates: list[tuple[int, dict[str, Any]]] = []
    for raw in markets:
        market = _record(raw)
        try:
            market_id = int(market.get("id") or 0)
        except (TypeError, ValueError):
            market_id = 0
        if market_id <= 0:
            continue
        category_slug = str(market.get("categorySlug") or category.get("slug") or "").lower()
        variant = _record(market.get("variantData")) or _record(category.get("variantData"))
        feed_symbol = str(variant.get("priceFeedSymbol") or "").upper()
        variant_type = str(variant.get("type") or market.get("marketVariant") or category.get("marketVariant") or "").upper()
        text = " ".join(str(market.get(k) or "") for k in ("title", "question")).upper()

        score = 0
        if category_slug == slug:
            score += 1000
        if feed_symbol.startswith(asset):
            score += 200
        if variant_type in {"CRYPTO_UP_DOWN", "CRYPTOUPDOWN"}:
            score += 100
        if asset in text:
            score += 40
        if "UP OR DOWN" in text:
            score += 20
        if score > 0:
            candidates.append((score, market))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], int(item[1].get("id") or 0)), reverse=True)
    market = candidates[0][1]
    return market


def predict_market_for_pair_historical(asset: str, pair: dict[str, Any], api_key: str) -> dict[str, Any]:
    end_ms = int(pair.get("market_end_ms") or 0)
    if end_ms <= 0:
        raise RuntimeError(f"Binance market {pair.get('market_id')} is missing market_end_ms")

    bucket = (end_ms // 1000) - 300
    slug = base.expected_slug(asset, bucket)
    payload = base.api_get(f"/v1/categories/{slug}", {}, api_key)
    market = _historical_market_from_category(payload, asset=asset, slug=slug)
    if not isinstance(market, dict) or not market.get("id"):
        category = _record(payload.get("data")) if isinstance(payload.get("data"), dict) else _record(payload)
        status = str(category.get("status") or "UNKNOWN")
        trading_statuses = [str(_record(x).get("tradingStatus") or "UNKNOWN") for x in (category.get("markets") or []) if isinstance(x, dict)]
        raise RuntimeError(
            f"Predict historical market mapping failed for {asset} Binance market {pair.get('market_id')} "
            f"slug={slug}; categoryStatus={status}; tradingStatuses={trading_statuses}"
        )

    variant = _record(market.get("variantData"))
    return {
        "binanceMarketId": int(pair.get("market_id") or 0),
        "predictMarketId": int(market["id"]),
        "bucketStartSec": bucket,
        "categorySlug": slug,
        "predictTitle": market.get("title") or market.get("question"),
        "predictConditionId": market.get("conditionId"),
        "predictTradingStatus": market.get("tradingStatus"),
        "predictStatus": market.get("status"),
        "priceFeedSymbol": variant.get("priceFeedSymbol"),
        "mappingMode": "CATEGORY_SLUG_HISTORICAL_ALLOW_RESOLVED",
    }


base.predict_market_for_pair = predict_market_for_pair_historical


if __name__ == "__main__":
    raise SystemExit(base.main())
