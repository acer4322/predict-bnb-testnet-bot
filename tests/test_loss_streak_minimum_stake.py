from __future__ import annotations

from decimal import Decimal

from predict_bot import live_trading
from predict_bot import loss_streak_guard_patch as guard
from predict_bot.loss_streak_minimum_stake_patch import (
    LOSS_STREAK_MINIMUM_LIVE_STAKE_USDT,
    loss_streak_reduced_stake,
)


def test_loss_streak_reduction_uses_one_usdt_floor() -> None:
    minimum = LOSS_STREAK_MINIMUM_LIVE_STAKE_USDT

    assert loss_streak_reduced_stake(Decimal("1.00"), minimum) == Decimal("1.00")
    assert loss_streak_reduced_stake(Decimal("2.00"), minimum) == Decimal("1.00")
    assert loss_streak_reduced_stake(Decimal("4.00"), minimum) == Decimal("2.000")


def test_live_execution_plan_never_halves_one_usdt_to_half_usdt() -> None:
    rules = live_trading.normalize_live_rules(
        {
            "strategies": ["R_MICROPRICE"],
            "strategyStakesUsdt": [1.0],
            guard.LOSS_STREAK_RULE_FIELD: [True],
        }
    )
    previous = getattr(guard._EXECUTION_CONTEXT, "value", None)
    guard._EXECUTION_CONTEXT.value = {
        "strategy": "R_MICROPRICE",
        "multiplier": guard.LOSS_STREAK_HALF_MULTIPLIER,
    }
    try:
        plan = live_trading.live_strategy_execution_plan(rules, "R_MICROPRICE")
    finally:
        guard._EXECUTION_CONTEXT.value = previous

    assert Decimal(str(plan["initialStakeUsdt"])) == Decimal("1.00")
    assert Decimal(str(plan["totalCapUsdt"])) == Decimal("1.00")
