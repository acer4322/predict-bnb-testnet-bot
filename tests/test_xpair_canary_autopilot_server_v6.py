from __future__ import annotations

from decimal import Decimal

import pytest

from predict_bot import xpair_canary_autopilot_server as base
from predict_bot.xpair_canary_autopilot_server_v2 import (
    clear_book_analysis,
    latest_book_analysis,
    publish_book_analysis,
)
from predict_bot.xpair_canary_autopilot_server_v6 import (
    configured_max_pair_budget,
    install_patches,
    state_payload,
    validate_button_arm_header,
    validate_live_selection,
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


def test_default_selection_uses_positive_eligible_sample_direction() -> None:
    install_patches()
    payload = state_payload()
    assert base.STATE.config.selection == "BTC_DOWN_ETH_UP"
    assert payload["defaults"]["selection"] == "BTC_DOWN_ETH_UP"
    assert payload["policy"]["automaticDirectionSelection"] is False
    assert payload["policy"]["defaultSelection"] == "BTC_DOWN_ETH_UP"
    assert payload["policy"]["liveAllowedSelections"] == ["BTC_DOWN_ETH_UP"]


def test_production_direction_is_allowed_for_live() -> None:
    validate_live_selection("BTC_DOWN_ETH_UP")


def test_negative_sample_direction_is_blocked_for_live_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XPAIR_ALLOW_EXPERIMENTAL_DIRECTIONS", raising=False)
    with pytest.raises(ValueError, match="empirical direction gate"):
        validate_live_selection("BTC_UP_ETH_DOWN")
    with pytest.raises(ValueError, match="empirical direction gate"):
        validate_live_selection("CHEAPEST_ELIGIBLE")


def test_experimental_direction_requires_explicit_environment_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XPAIR_ALLOW_EXPERIMENTAL_DIRECTIONS", "1")
    validate_live_selection("BTC_UP_ETH_DOWN")
    validate_live_selection("CHEAPEST_ELIGIBLE")


def test_experimental_direction_can_still_be_selected_for_monitoring() -> None:
    install_patches()
    config = base.MonitorConfig.from_payload(
        {"selection": "CHEAPEST_ELIGIBLE"},
        base.STATE.config,
    )
    assert config.selection == "CHEAPEST_ELIGIBLE"


def test_continuous_book_analysis_is_exposed_without_signed_quote() -> None:
    clear_book_analysis()
    publish_book_analysis(
        market_key="101:202",
        seconds_left=247.5,
        inside_entry_window=False,
        selection="BTC_DOWN_ETH_UP",
        trials=[
            {
                "variant": "BTC_UP_ETH_DOWN",
                "entry_status": "SKIPPED_PRICE",
                "eligible": False,
                "rejection_reason": "total cost exceeds cap",
                "cost_per_share": None,
            },
            {
                "variant": "BTC_DOWN_ETH_UP",
                "entry_status": "ELIGIBLE",
                "eligible": True,
                "rejection_reason": None,
                "cost_per_share": 0.91,
                "filled_shares": 3.1,
                "btc_vwap": 0.48,
                "eth_vwap": 0.43,
            },
        ],
        chosen={
            "variant": "BTC_DOWN_ETH_UP",
            "cost_per_share": 0.91,
            "filled_shares": 3.1,
        },
    )
    install_patches()
    payload = state_payload()
    analysis = payload["runtime"]["bookAnalysis"]
    assert analysis["marketKey"] == "101:202"
    assert analysis["insideEntryWindow"] is False
    assert analysis["selectedVariant"] == "BTC_DOWN_ETH_UP"
    assert analysis["trials"][1]["entryStatus"] == "ELIGIBLE"
    assert payload["policy"]["continuousBookMonitoring"] is True
    assert payload["policy"]["signedQuotesRestrictedToEntryWindow"] is True
    assert payload["policy"]["livePlacementRestrictedToEntryWindow"] is True
    clear_book_analysis()


def test_latest_book_analysis_returns_a_defensive_copy() -> None:
    clear_book_analysis()
    publish_book_analysis(
        market_key="1:2",
        seconds_left=250,
        inside_entry_window=False,
        selection="BTC_DOWN_ETH_UP",
        trials=[
            {
                "variant": "BTC_DOWN_ETH_UP",
                "entry_status": "ELIGIBLE",
                "eligible": True,
            }
        ],
        chosen={"variant": "BTC_DOWN_ETH_UP"},
    )
    first = latest_book_analysis()
    assert first is not None
    first["trials"][0]["entryStatus"] = "MUTATED"
    second = latest_book_analysis()
    assert second is not None
    assert second["trials"][0]["entryStatus"] == "ELIGIBLE"
    clear_book_analysis()


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
    with pytest.raises(ValueError, match="between 2.00 and 10.00 USDT"):
        base.MonitorConfig.from_payload(
            {"pairBudgetUsdt": "10.01"},
            base.STATE.config,
        )


def test_configured_max_budget_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XPAIR_MAX_PAIR_BUDGET_USDT", "250")
    assert configured_max_pair_budget() == Decimal("100.00")
    monkeypatch.setenv("XPAIR_MAX_PAIR_BUDGET_USDT", "1")
    assert configured_max_pair_budget() == Decimal("2.00")
