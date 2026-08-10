import pytest

from predict_bot.poly_gap_live_v37 import (
    SHOTGUN_MIN_ORDER_USDT,
    _parse_shotgun_levels,
    _validate_shotgun_order_amount,
)


def test_shotgun_levels_are_unique_and_descending():
    assert _parse_shotgun_levels(
        "0.05,0.10,0.20,0.30,0.40,0.10",
        minimum=0.05,
        maximum=0.40,
    ) == (0.40, 0.30, 0.20, 0.10, 0.05)


def test_shotgun_rejects_more_than_five_levels():
    with pytest.raises(ValueError, match="at most 5"):
        _parse_shotgun_levels(
            [0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
            minimum=0.05,
            maximum=0.30,
        )


def test_shotgun_rejects_level_outside_configured_range():
    with pytest.raises(ValueError, match="inside"):
        _parse_shotgun_levels(
            [0.04, 0.10, 0.20],
            minimum=0.05,
            maximum=0.40,
        )


def test_shotgun_enforces_observed_one_usdt_minimum():
    assert SHOTGUN_MIN_ORDER_USDT == 1.0
    assert _validate_shotgun_order_amount(1.0) == 1.0
    with pytest.raises(ValueError, match="1.00"):
        _validate_shotgun_order_amount(0.99)
