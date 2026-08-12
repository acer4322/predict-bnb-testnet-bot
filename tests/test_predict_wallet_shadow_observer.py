from predict_bot.predict_wallet_shadow_observer import (
    MAKER_UNIT_SHARES,
    ParentEvent,
    ShadowEvent,
    aggregate_parent,
    inventory_from_target,
    similarity,
)
from predict_bot.predict_wallet_shadow_observer_v2 import normalize_match_leg

WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"
WEI = 10**18


def wei(value: float) -> str:
    return str(int(round(value * WEI)))


def match_row(*, taker_signer="0xabc", maker_signer=WALLET, maker_hash="0xmaker", taker_hash="0xtaker"):
    return {
        "market": {
            "id": 123,
            "title": "BTC Up or Down 5m",
            "variantData": {"type": "CRYPTO_UP_DOWN", "priceFeedSymbol": "BTC/USD"},
        },
        "taker": {
            "quoteType": "Bid",
            "amount": wei(9),
            "price": wei(0.61),
            "outcome": {"name": "DOWN"},
            "signer": taker_signer,
            "hash": taker_hash,
        },
        "makers": [
            {
                "quoteType": "Bid",
                "amount": wei(9),
                "price": wei(0.41),
                "outcome": {"name": "UP"},
                "signer": maker_signer,
                "hash": maker_hash,
            }
        ],
        "transactionHash": "0xtx",
        "settlementId": "settlement-1",
        "executedAt": "2026-08-08T00:00:01.500Z",
    }


def test_normalize_match_leg_reads_wei_role_hash_side_and_timestamp():
    leg = normalize_match_leg(match_row(), wallet=WALLET, role="MAKER", maker_index=0)
    assert leg is not None
    assert leg["marketId"] == 123
    assert leg["role"] == "MAKER"
    assert leg["side"] == "UP"
    assert leg["quoteType"] == "BID"
    assert leg["orderHash"] == "0xmaker"
    assert leg["shares"] == 9
    assert abs(leg["price"] - 0.41) < 1e-12
    assert leg["eventMs"] == 1786147201500


def test_parent_aggregation_combines_partial_fills_by_order_hash():
    first = normalize_match_leg(match_row(), wallet=WALLET, role="MAKER", maker_index=0)
    assert first is not None
    second_raw = match_row()
    second_raw["makers"][0]["amount"] = wei(9)
    second_raw["makers"][0]["price"] = wei(0.43)
    second_raw["transactionHash"] = "0xtx2"
    second_raw["settlementId"] = "settlement-2"
    second_raw["executedAt"] = "2026-08-08T00:00:02.000Z"
    second = normalize_match_leg(second_raw, wallet=WALLET, role="MAKER", maker_index=0)
    assert second is not None
    parent = aggregate_parent(None, first)
    parent = aggregate_parent(parent, second)
    assert parent.id == "MAKER:0xmaker"
    assert parent.shares == 18
    assert parent.fill_legs == 2
    assert round(parent.average_price or 0, 6) == 0.42


def test_target_inventory_uses_bid_only_and_role_residuals():
    events = [
        ParentEvent("m1", "MAKER", 1, "UP", "BID", "m1", 1, 1, 0.4, 36, 1),
        ParentEvent("m2", "MAKER", 1, "DOWN", "BID", "m2", 2, 2, 0.6, 18, 1),
        ParentEvent("t1", "TAKER", 1, "DOWN", "BID", "t1", 3, 3, 0.7, 54, 1),
        ParentEvent("sell", "TAKER", 1, "UP", "ASK", "sell", 4, 4, 0.8, 999, 1),
    ]
    inventory = inventory_from_target(events)
    assert inventory["makerDelta"] == 18
    assert inventory["takerDelta"] == -54


def test_similarity_rewards_expected_maker_and_taker_event_shape():
    target = [
        ParentEvent("m", "MAKER", 1, "UP", "BID", "m", 10_000, 10_000, 0.41, MAKER_UNIT_SHARES, 1),
        ParentEvent("t", "TAKER", 1, "DOWN", "BID", "t", 20_000, 20_000, 0.72, 36, 1),
    ]
    shadow = [
        ShadowEvent("q", 1, 9_500, "MAKER_QUOTE", "MAKER", "UP", 0.41, MAKER_UNIT_SHARES, "KNOWN_PATTERN", "q", "DOWN", "test", 0, 0, 0, 0),
        ShadowEvent("f", 1, 10_500, "MAKER_FILL_PROXY", "MAKER", "UP", 0.41, MAKER_UNIT_SHARES, "INFERRED_FILL", "f", "DOWN", "test", 18, 0, 0, 0),
        ShadowEvent("taker", 1, 20_500, "TAKER_INTENT", "TAKER", "DOWN", 0.72, 36, "INFERRED_TAKER_TRIGGER", "t", "DOWN", "test", 18, 0, 0, 36),
    ]
    score = similarity(target, shadow)
    assert score["buyOnlyRate"] == 1.0
    assert score["makerUnitRate"] == 1.0
    assert score["makerQuotePriceWithin1Tick"] == 1.0
    assert score["makerFillTimingWithin3s"] == 1.0
    assert score["takerSideMatchWithin5s"] == 1.0
    assert score["takerTimingWithin3s"] == 1.0
    assert score["matchedPriceWithin1Tick"] == 1.0
    assert score["overall"] == 1.0
