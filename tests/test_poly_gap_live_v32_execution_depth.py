from predict_bot.poly_gap_live_v32 import _buy_depth, _sell_depth


def test_buy_depth_requires_buffered_notional_inside_price_cap():
    levels = [(0.04, 50.0), (0.05, 40.0)]  # $4 total visible notional
    depth = _buy_depth(
        levels,
        stake_usdt=5.0,
        max_price=0.055,
        buffer_ratio=1.15,
    )
    assert depth["visibleNotionalUsdt"] == 4.0
    assert depth["requiredBufferedNotionalUsdt"] == 5.75
    assert depth["coverageRatio"] == 0.8
    assert depth["sufficient"] is False


def test_buy_depth_accepts_multi_level_liquidity_with_buffer():
    levels = [(0.04, 50.0), (0.05, 80.0)]  # $6 total visible notional
    depth = _buy_depth(
        levels,
        stake_usdt=5.0,
        max_price=0.055,
        buffer_ratio=1.15,
    )
    assert depth["visibleNotionalUsdt"] == 6.0
    assert depth["coverageRatio"] == 1.2
    assert depth["sufficient"] is True
    assert depth["expectedFillVwap"] is not None
    assert depth["expectedWorstPrice"] == 0.05


def test_sell_depth_reports_shortfall_without_defining_an_exit_block():
    levels = [(0.80, 2.0), (0.70, 2.0)]
    depth = _sell_depth(
        levels,
        required_shares=5.0,
        min_price=0.64,
    )
    assert depth["visibleShares"] == 4.0
    assert depth["coverageRatio"] == 0.8
    assert depth["sufficient"] is False
    # V32 consumes this as telemetry only; the SELL path itself remains exit-priority.


def test_sell_depth_estimates_vwap_across_multiple_bid_levels():
    levels = [(0.80, 2.0), (0.70, 4.0)]
    depth = _sell_depth(
        levels,
        required_shares=5.0,
        min_price=0.64,
    )
    assert depth["visibleShares"] == 6.0
    assert depth["coverageRatio"] == 1.2
    assert depth["sufficient"] is True
    assert abs(depth["expectedFillVwap"] - 0.74) < 1e-12
    assert depth["expectedWorstPrice"] == 0.70
