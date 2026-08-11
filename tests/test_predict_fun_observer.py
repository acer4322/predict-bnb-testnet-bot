from __future__ import annotations

from predict_bot.predict_fun_observer import (
    complement_price,
    expected_slug,
    parse_predict_orderbook,
    select_predict_market,
)


def test_predict_orderbook_derives_native_down_from_yes_book() -> None:
    parsed = parse_predict_orderbook(
        {
            "marketId": 42,
            "updateTimestampMs": 1_700_000_000_123,
            "asks": [[0.62, 10], [0.63, 20]],
            "bids": [[0.61, 30], [0.60, 40]],
        },
        decimal_precision=2,
    )
    assert parsed is not None
    assert parsed["upBid"] == 0.61
    assert parsed["upAsk"] == 0.62
    assert parsed["downBid"] == 0.38
    assert parsed["downAsk"] == 0.39
    assert parsed["upMid"] == 0.615
    assert parsed["downMid"] == 0.385


def test_complement_respects_market_precision() -> None:
    assert complement_price(0.667, 3) == 0.333
    assert complement_price(0.62, 2) == 0.38


def test_select_predict_market_prefers_exact_current_five_minute_slug() -> None:
    bucket = 1_786_449_300
    slug = expected_slug("BTC", bucket)
    payload = {
        "success": True,
        "data": {
            "categories": [
                {
                    "slug": slug,
                    "title": "BTC Up or Down 5m",
                    "variantData": {"type": "CRYPTO_UP_DOWN", "priceFeedSymbol": "BTC/USD"},
                    "markets": [
                        {
                            "id": 7001,
                            "title": "BTC Up or Down 5m",
                            "question": "Will BTC go up or down?",
                            "tradingStatus": "OPEN",
                            "isVisible": True,
                            "categorySlug": slug,
                            "decimalPrecision": 3,
                            "variantData": {"type": "CRYPTO_UP_DOWN", "priceFeedSymbol": "BTC/USD"},
                        }
                    ],
                },
                {
                    "slug": f"btc-updown-15m-{bucket}",
                    "title": "BTC Up or Down 15m",
                    "markets": [{"id": 9999, "title": "BTC Up or Down 15m", "tradingStatus": "OPEN"}],
                },
            ],
            "markets": [],
        },
    }
    selected = select_predict_market(payload, asset="BTC", bucket=bucket, now_ms=bucket * 1000 + 60_000)
    assert selected is not None
    assert selected["id"] == 7001
    assert selected["categorySlug"] == slug
    assert selected["decimalPrecision"] == 3


def test_select_predict_market_rejects_wrong_asset_or_interval() -> None:
    bucket = 1_786_449_300
    payload = {
        "data": {
            "categories": [],
            "markets": [
                {
                    "id": 1,
                    "title": "ETH Up or Down 15m",
                    "question": "ETH Up or Down 15m",
                    "tradingStatus": "OPEN",
                    "categorySlug": f"eth-updown-15m-{bucket}",
                    "variantData": {"type": "CRYPTO_UP_DOWN"},
                }
            ],
        }
    }
    assert select_predict_market(payload, asset="BTC", bucket=bucket) is None
