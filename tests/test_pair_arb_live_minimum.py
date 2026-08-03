from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from predict_bot import live_trading as live
from predict_bot.pair_arb_live_minimum import (
    PAIR_ARB_MIN_LEG_STAKE_USDT,
    _configured_pair_leg_stake,
    install_pair_arb_minimum,
)


class FakeEngine:
    def __init__(self, total_stake: float) -> None:
        self.lock = threading.RLock()
        self.live_rules = {
            "strategies": ["PAIR_ARB_010"],
            "strategyStakesUsdt": [total_stake],
            "maxStakeUsdt": total_stake,
        }


def test_pair_leg_allocation_detects_sub_one_usdt_leg() -> None:
    engine = FakeEngine(3.0)
    stake = _configured_pair_leg_stake(
        engine,
        {
            "strategy": "PAIR_ARB_010",
            "entry_price": 0.20,
            "pair_total_price": 0.95,
        },
    )
    assert stake is not None
    assert stake < PAIR_ARB_MIN_LEG_STAKE_USDT


def test_dynamic_pair_leg_stake_is_used_directly() -> None:
    engine = FakeEngine(10.0)
    stake = _configured_pair_leg_stake(
        engine,
        {
            "strategy": "PAIR_ARB_010",
            "entry_price": 0.20,
            "pair_total_price": 0.95,
            "_pair_dynamic_leg_stake_usdt": "1.25",
        },
    )
    assert stake == Decimal("1.25")


def test_pair_total_stake_below_two_is_rejected() -> None:
    install_pair_arb_minimum()
    with pytest.raises(ValueError, match="at least 2.00 USDT"):
        live.normalize_live_rules(
            {
                "strategies": ["PAIR_ARB_010"],
                "strategyStakesUsdt": [1.99],
            }
        )


def test_non_pair_strategy_keeps_existing_minimum() -> None:
    install_pair_arb_minimum()
    rules = live.normalize_live_rules(
        {
            "strategies": ["M01O_F1"],
            "strategyStakesUsdt": [0.50],
        }
    )
    assert rules["strategyStakesUsdt"] == [0.5]
