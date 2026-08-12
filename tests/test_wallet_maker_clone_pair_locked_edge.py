from __future__ import annotations

import pytest

from predict_bot.wallet_maker_clone_pair_locked_edge import apply_equal_share_locked_edge


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


def test_zero_edge_050_050_is_blocked() -> None:
    result = apply_equal_share_locked_edge(
        _plans(0.50, 0.50),
        minimum_order_usdt=1.0,
        maximum_order_usdt=25.0,
        maximum_combined_price=0.98,
        minimum_locked_return_pct=1.0,
    )
    assert result["UP"]["blocked"] is True
    assert result["DOWN"]["blocked"] is True
    edge = result["UP"]["pairEdge"]
    assert edge["combinedPrice"] == pytest.approx(1.0)
    assert edge["lockedProfitUsdt"] == pytest.approx(0.0)
    assert edge["passes"] is False


def test_049_049_passes_with_equal_shares_and_positive_locked_return() -> None:
    result = apply_equal_share_locked_edge(
        _plans(0.49, 0.49),
        minimum_order_usdt=1.0,
        maximum_order_usdt=25.0,
        maximum_combined_price=0.98,
        minimum_locked_return_pct=1.0,
    )
    up = result["UP"]
    down = result["DOWN"]
    assert up["blocked"] is False
    assert down["blocked"] is False
    assert up["plannedShares"] == pytest.approx(down["plannedShares"])
    edge = up["pairEdge"]
    assert edge["combinedPrice"] == pytest.approx(0.98)
    assert edge["lockedProfitUsdt"] > 0
    assert edge["lockedReturnPct"] == pytest.approx((1.0 - 0.98) / 0.98 * 100.0)
    assert edge["passes"] is True


def test_unequal_raw_sizes_are_equalized_before_locked_pnl() -> None:
    result = apply_equal_share_locked_edge(
        _plans(0.20, 0.75, up_shares=5.0, down_shares=1.3333333333),
        minimum_order_usdt=1.0,
        maximum_order_usdt=25.0,
        maximum_combined_price=0.98,
        minimum_locked_return_pct=1.0,
    )
    up = result["UP"]
    down = result["DOWN"]
    assert up["plannedShares"] == pytest.approx(5.0)
    assert down["plannedShares"] == pytest.approx(5.0)
    assert up["plannedCost"] == pytest.approx(1.0)
    assert down["plannedCost"] == pytest.approx(3.75)
    edge = up["pairEdge"]
    assert edge["lockedProfitUsdt"] == pytest.approx(0.25)
    assert edge["lockedReturnPct"] == pytest.approx(0.25 / 4.75 * 100.0)
    assert edge["passes"] is True


def test_equal_share_cost_bounds_can_block_extreme_shell() -> None:
    result = apply_equal_share_locked_edge(
        _plans(0.01, 0.99, up_shares=100.0, down_shares=2.0),
        minimum_order_usdt=1.0,
        maximum_order_usdt=25.0,
        maximum_combined_price=0.98,
        minimum_locked_return_pct=1.0,
    )
    edge = result["UP"]["pairEdge"]
    assert edge["passes"] is False
    assert edge["qMinByCostBounds"] > edge["qMaxByCostBounds"]
    assert "cost bounds" in edge["reason"]


def test_existing_venue_block_is_never_overridden() -> None:
    plans = _plans(0.40, 0.50)
    plans["UP"]["blocked"] = True
    plans["UP"]["reason"] = "post-only block"
    result = apply_equal_share_locked_edge(
        plans,
        minimum_order_usdt=1.0,
        maximum_order_usdt=25.0,
        maximum_combined_price=0.98,
        minimum_locked_return_pct=1.0,
    )
    assert result["UP"]["blocked"] is True
    assert result["UP"]["reason"] == "post-only block"
