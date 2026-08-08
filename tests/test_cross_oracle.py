from __future__ import annotations

import json

from predict_bot.cross_oracle import (
    _timestamp_ms,
    current_btc_5m_slug,
    parse_chainlink_message,
    parse_gamma_market,
)


def test_current_btc_5m_slug_uses_five_minute_epoch_bucket() -> None:
    slug, bucket = current_btc_5m_slug(1_786_147_923.4)
    assert bucket == 1_786_147_800
    assert slug == "btc-updown-5m-1786147800"


def test_parse_gamma_market_maps_up_and_down_tokens_by_outcome() -> None:
    payload = {
        "id": "123",
        "slug": "btc-updown-5m-1786147800",
        "question": "Bitcoin Up or Down",
        "conditionId": "0xabc",
        "clobTokenIds": json.dumps(["down-token", "up-token"]),
        "outcomes": json.dumps(["Down", "Up"]),
        "active": True,
        "closed": False,
    }
    market = parse_gamma_market(payload, bucket_start=1_786_147_800)
    assert market is not None
    assert market["upTokenId"] == "up-token"
    assert market["downTokenId"] == "down-token"
    assert market["windowStartMs"] == 1_786_147_800_000
    assert market["windowEndMs"] == 1_786_148_100_000


def test_parse_chainlink_message_preserves_source_timestamp_and_price() -> None:
    raw = json.dumps(
        {
            "topic": "crypto_prices_chainlink",
            "type": "update",
            "timestamp": 1_786_147_801_999,
            "payload": {
                "symbol": "btc/usd",
                "timestamp": 1_786_147_801_234,
                "value": 64_871.5,
            },
        }
    )
    parsed = parse_chainlink_message(raw)
    assert parsed == {
        "topic": "crypto_prices_chainlink",
        "symbol": "btc/usd",
        "price": 64_871.5,
        "sourceTimestampMs": 1_786_147_801_234,
    }


def test_timestamp_normalization_accepts_seconds_milliseconds_and_nanoseconds() -> None:
    assert _timestamp_ms(1_786_147_801) == 1_786_147_801_000
    assert _timestamp_ms(1_786_147_801_234) == 1_786_147_801_234
    assert _timestamp_ms(1_786_147_801_234_000_000) == 1_786_147_801_234
