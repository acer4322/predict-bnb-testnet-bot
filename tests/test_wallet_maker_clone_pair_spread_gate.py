from __future__ import annotations

import pytest

from predict_bot.wallet_maker_clone_pair_spread_gate import apply_pair_spread_gate


def _plans(up_price: float, down_price: float, up_shares: float = 2.0, down_shares: float = 2.0):
    return {
        "UP": {
            "side": "UP",
            "blocked": False,
            "price": up_price,
            "bestBid": up_price,
            "bestAsk": min(0.99, up_price + 0.02),
            "plannedShares": up_shares,
            "plannedCost": up_price * up_shares,
            "targetProfit": up_shares * (1.0 - up_price),
            "tokenId": "up",
        },
        "DOWN": {
            "side": "DOWN",
            "blocked": False,
            "price": down_price,
            "bestBid": down_price,
            "bestAsk": min(0.99, down_price + 0.02),
            "plannedShares": down_shares,
            "plannedCost": down_price * down_shares,
            "targetProfit": down_shares * (1.0 - down_price),
            "tokenId": "down",
        },
    }


def test_050_050_is_blocked_without_resizing() -> None:
    plans = _plans(0.50, 0.50, up_shares=2.0, down_shares=3.0)
    result = apply_pair_spread_gate(plans, maximum_combined_maker_price=0.98)
    assert result["UP"]["blocked"] is True
    assert result["DOWN"]["blocked"] is True
    assert result["UP"]["plannedShares"] == pytest.approx(2.0)
    assert result["DOWN"]["plannedShares"] == pytest.approx(3.0)
    preview = result["UP"]["pairMakerSpread"]
    assert preview["combinedMakerPrice"] == pytest.approx(1.0)
    assert preview["makerSpreadCushion"] == pytest.approx(0.0)
    assert preview["guaranteedProfit"] is False
    assert preview["equalShareSizing"] is False


def test_049_049_passes_and_keeps_original_independent_sizes() -> None:
    result = apply_pair_spread_gate(
        _plans(0.49, 0.49, up_shares=2.1, down_shares=4.2),
        maximum_combined_maker_price=0.98,
    )
    assert result["UP"]["blocked"] is False
    assert result["DOWN"]["blocked"] is False
    assert result["UP"]["plannedShares"] == pytest.approx(2.1)
    assert result["DOWN"]["plannedShares"] == pytest.approx(4.2)
    preview = result["UP"]["pairMakerSpread"]
    assert preview["combinedMakerPrice"] == pytest.approx(0.98)
    assert preview["makerSpreadCushionPct"] == pytest.approx(2.0)


def test_asymmetric_passive_quotes_keep_payoff_normalized_sizing() -> None:
    result = apply_pair_spread_gate(
        _plans(0.20, 0.75, up_shares=5.0, down_shares=1.3333333333),
        maximum_combined_maker_price=0.98,
    )
    assert result["UP"]["blocked"] is False
    assert result["DOWN"]["blocked"] is False
    assert result["UP"]["plannedShares"] == pytest.approx(5.0)
    assert result["DOWN"]["plannedShares"] == pytest.approx(1.3333333333)
    assert result["UP"]["plannedCost"] == pytest.approx(1.0)
    assert result["DOWN"]["plannedCost"] == pytest.approx(1.0)
    assert result["UP"]["pairMakerSpread"]["combinedMakerPrice"] == pytest.approx(0.95)


def test_existing_post_only_block_is_preserved() -> None:
    plans = _plans(0.40, 0.50)
    plans["UP"]["blocked"] = True
    plans["UP"]["reason"] = "post-only block"
    result = apply_pair_spread_gate(plans, maximum_combined_maker_price=0.98)
    assert result["UP"]["blocked"] is True
    assert result["UP"]["reason"] == "post-only block"
    assert result["DOWN"]["blocked"] is False
