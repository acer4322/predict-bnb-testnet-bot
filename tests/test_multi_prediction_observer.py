from __future__ import annotations

import json

from predict_bot.multi_prediction_observer import current_slug, parse_gamma_candidate


def test_current_slug_supports_btc_eth_bnb() -> None:
    now = 1_786_449_301.0
    bucket = 1_786_449_300
    assert current_slug("BTC", now) == (f"btc-updown-5m-{bucket}", bucket)
    assert current_slug("ETH", now) == (f"eth-updown-5m-{bucket}", bucket)
    assert current_slug("BNB", now) == (f"bnb-updown-5m-{bucket}", bucket)


def test_parse_gamma_candidate_maps_explicit_up_down_tokens() -> None:
    bucket = 1_786_449_300
    payload = {
        "id": "123",
        "slug": "underlying-market-slug",
        "conditionId": "0xabc",
        "question": "ETH Up or Down 5m",
        "clobTokenIds": json.dumps(["up-token", "down-token"]),
        "outcomes": json.dumps(["Up", "Down"]),
    }
    parsed = parse_gamma_candidate(
        payload,
        asset="ETH",
        event_slug=f"eth-updown-5m-{bucket}",
        bucket=bucket,
    )
    assert parsed is not None
    assert parsed["asset"] == "ETH"
    assert parsed["upTokenId"] == "up-token"
    assert parsed["downTokenId"] == "down-token"
    assert parsed["windowEndMs"] == (bucket + 300) * 1000
    assert parsed["eventSlug"] == f"eth-updown-5m-{bucket}"


def test_parse_gamma_candidate_rejects_non_up_down_binary_market() -> None:
    bucket = 1_786_449_300
    payload = {
        "conditionId": "0xabc",
        "clobTokenIds": ["yes-token", "no-token"],
        "outcomes": ["Yes", "No"],
    }
    assert (
        parse_gamma_candidate(
            payload,
            asset="BNB",
            event_slug=f"bnb-updown-5m-{bucket}",
            bucket=bucket,
        )
        is None
    )
