from __future__ import annotations

from decimal import Decimal

import pytest

from predict_bot import xpair_canary_autopilot_server as base
from predict_bot.xpair_canary_autopilot_server_v6 import (
    configured_max_pair_budget,
    install_patches,
    state_payload,
    validate_button_arm_header,
)


def test_dashboard_confirmation_header_is_sufficient() -> None:
    validate_button_arm_header("confirmed")


def test_missing_dashboard_confirmation_header_is_rejected() -> None:
    with pytest.raises(ValueError, match="dashboard confirmation header"):
        validate_button_arm_header(None)


def test_typed_live_confirmation_is_not_advertised() -> None:
    install_patches()
    policy = state_payload()["policy"]
    assert policy["typedLiveConfirmationRequired"] is False
    assert policy["liveArmConfirmation"] == "dashboard_button_and_browser_dialog"
    assert "liveConfirmationPhrase" not in policy


def test_edited_pair_budgets_are_accepted_through_ten_usdt() -> None:
    install_patches()
    current = base.STATE.config
    for amount in ("2.00", "2.50", "3.00", "5.00", "10.00"):
        config = base.MonitorConfig.from_payload(
            {"pairBudgetUsdt": amount},
            current,
        )
        assert config.pair_budget_usdt == Decimal(amount)


def test_pair_budget_above_configured_max_is_rejected() -> None:
    install_patches()
    with pytest.raises(ValueError, match="between 0.02 and 10.00 USDT"):
        base.MonitorConfig.from_payload(
            {"pairBudgetUsdt": "10.01"},
            base.STATE.config,
        )


def test_configured_max_budget_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XPAIR_MAX_PAIR_BUDGET_USDT", "250")
    assert configured_max_pair_budget() == Decimal("100.00")
    monkeypatch.setenv("XPAIR_MAX_PAIR_BUDGET_USDT", "1")
    assert configured_max_pair_budget() == Decimal("2.00")
