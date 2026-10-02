from __future__ import annotations

import pytest

from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from predict_bot.target_taker_public_side_echtgeld_bridge_v1 import (
    build_base_echtgeld_intent,
)


def _snapshot(market_id: int = 123) -> dict[str, object]:
    return {
        "marketId": market_id,
        "timestampNs": 1_700_000_000_123_000_000,
        "sampledAtMs": 1_700_000_000_123,
        "secondsLeft": 120.0,
    }


def _trade() -> dict[str, object]:
    return {
        "decision": "TRADE",
        "reason": "PUBLIC_SIDE_EBM_MATCH",
        "side": "UP",
        "ask": 0.61,
        "signal": {"selectedProbability": 0.72},
    }


def test_base_intent_uses_exact_live_strategy_and_side_only_contract() -> None:
    intent = build_base_echtgeld_intent(
        123,
        _snapshot(),
        _trade(),
        created_at_ms=1_700_000_000_200,
    )

    assert intent["strategy"] == public_side.VERSION == "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD"
    assert intent["cohort"] == public_side.SIDE_ONLY_COHORT == "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
    assert intent["marketId"] == 123
    assert intent["createdAtMs"] == 1_700_000_000_200
    assert intent["entryOrdinal"] == 1
    assert intent["entrySource"] == "BASE_EBM"
    assert intent["reentryLiveEligible"] is False
    assert intent["decision"]["side"] == "UP"
    assert intent["decision"]["ask"] == 0.61
    assert intent["snapshot"]["marketId"] == 123


def test_base_intent_dedupe_is_one_live_attempt_per_market() -> None:
    first = build_base_echtgeld_intent(123, _snapshot(), _trade(), created_at_ms=1_700_000_000_200)
    second = build_base_echtgeld_intent(123, _snapshot(), _trade(), created_at_ms=1_700_000_000_250)

    assert first["dedupeKey"] == second["dedupeKey"]
    assert first["dedupeKey"] == (
        "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD:"
        "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY:123"
    )


def test_base_intent_rejects_non_trade_decision() -> None:
    decision = _trade()
    decision["decision"] = "SKIP"
    with pytest.raises(ValueError, match="TRADE"):
        build_base_echtgeld_intent(123, _snapshot(), decision)


def test_base_intent_rejects_market_mismatch() -> None:
    with pytest.raises(ValueError, match="market mismatch"):
        build_base_echtgeld_intent(123, _snapshot(456), _trade())


def test_base_intent_rejects_invalid_side_or_ask() -> None:
    bad_side = _trade()
    bad_side["side"] = "OPPOSITE"
    with pytest.raises(ValueError, match="side/ask"):
        build_base_echtgeld_intent(123, _snapshot(), bad_side)

    bad_ask = _trade()
    bad_ask["ask"] = 1.1
    with pytest.raises(ValueError, match="side/ask"):
        build_base_echtgeld_intent(123, _snapshot(), bad_ask)
