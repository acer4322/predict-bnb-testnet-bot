from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from predict_bot.xpair_canary_autopilot_server import MonitorConfig
from predict_bot.xpair_canary_autopilot_server_v3 import (
    DEFAULT_PAIR_BUDGET_USDT,
    MIN_EXCHANGE_LEG_STAKE_USDT,
    MIN_PAIR_BUDGET_USDT,
    quote_equal_share_pair_min_one,
    validate_monitor_config,
)


def test_exchange_minimums_are_explicit() -> None:
    assert MIN_EXCHANGE_LEG_STAKE_USDT == Decimal("1.00")
    assert MIN_PAIR_BUDGET_USDT == Decimal("2.00")
    assert DEFAULT_PAIR_BUDGET_USDT == Decimal("3.00")


def test_pair_budget_below_two_is_rejected() -> None:
    config = MonitorConfig(pair_budget_usdt=Decimal("1.99"))
    with pytest.raises(ValueError, match="at least 2.00 USDT"):
        validate_monitor_config(config)


def test_small_modeled_leg_is_rejected_before_exchange_quote() -> None:
    plan = SimpleNamespace(
        legs=(
            SimpleNamespace(
                symbol="BTC",
                side="DOWN",
                requested_stake=Decimal("0.75"),
            ),
            SimpleNamespace(
                symbol="ETH",
                side="UP",
                requested_stake=Decimal("2.10"),
            ),
        )
    )

    with pytest.raises(ValueError) as exc_info:
        quote_equal_share_pair_min_one(
            object(),
            wallet_address="0xabc",
            plan=plan,
            pair_budget=Decimal("3.00"),
            max_total_cost_per_share=Decimal("0.98"),
            slippage_bps=100,
        )

    message = str(exc_info.value)
    assert "MIN_ORDER_1_USDT" in message
    assert "BTC_DOWN" in message
    assert "pair_budget >= 4.00 USDT" in message
