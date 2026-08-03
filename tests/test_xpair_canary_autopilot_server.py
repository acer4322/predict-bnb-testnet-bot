from __future__ import annotations

from decimal import Decimal

import pytest

from predict_bot.xpair_canary_autopilot_server import (
    AutopilotState,
    MonitorConfig,
    config_payload,
)


def test_monitor_defaults_to_quote_only_disarmed() -> None:
    state = AutopilotState()
    snapshot = state.snapshot()
    assert snapshot["armed"] is False
    assert snapshot["liveAttempts"] == 0
    assert snapshot["config"]["selection"] == "BTC_DOWN_ETH_UP"
    assert snapshot["config"]["accountType"] == "CeDeFi"


def test_one_arm_is_consumed_by_exactly_one_market() -> None:
    state = AutopilotState()
    state.arm({})
    assert state.is_armed() is True
    assert state.consume_arm("btc-1:eth-1") is True
    assert state.is_armed() is False
    assert state.consume_arm("btc-1:eth-1") is False
    snapshot = state.snapshot()
    assert snapshot["liveAttempts"] == 1
    assert snapshot["lastAttemptMarketKey"] == "btc-1:eth-1"


def test_rejected_or_waiting_quote_does_not_consume_arm() -> None:
    state = AutopilotState()
    state.arm({})
    state.set_phase("QUOTE_REJECTED")
    assert state.is_armed() is True
    state.set_phase("ARMED_WAITING_SAFE_WALLET")
    assert state.is_armed() is True


def test_second_arm_is_rejected_until_operator_disarms_or_attempt_consumes_it() -> None:
    state = AutopilotState()
    state.arm({})
    with pytest.raises(RuntimeError, match="already armed"):
        state.arm({})
    state.disarm("test")
    state.arm({})
    assert state.is_armed() is True


def test_config_update_keeps_hard_budget_cap_and_cedefi_account() -> None:
    state = AutopilotState()
    config = state.update_config(
        {
            "selection": "CHEAPEST_ELIGIBLE",
            "pairBudgetUsdt": 3,
            "balanceBufferUsdt": 0.25,
            "maxTotalCost": 0.97,
            "accountType": "SPOT",
        }
    )
    assert config.selection == "CHEAPEST_ELIGIBLE"
    assert config.pair_budget_usdt == Decimal("3")
    assert config.required_balance == Decimal("3.25")
    assert config.account_type == "CeDeFi"

    with pytest.raises(ValueError, match="between 0.02 and 3.00"):
        state.update_config({"pairBudgetUsdt": 3.01})


def test_config_payload_exposes_quote_monitor_interval() -> None:
    payload = config_payload(MonitorConfig())
    assert payload["quoteIntervalSeconds"] == 1.0
    assert payload["requiredBalanceUsdt"] == 2.1
