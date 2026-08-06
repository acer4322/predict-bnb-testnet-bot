from predict_bot import live_trading
from predict_bot.microprice_confirm_observer_guard_patch import OBSERVER_VERSION


def test_guard_version_is_allowed_for_other_observer_supported_strategy() -> None:
    rules = live_trading.normalize_live_rules(
        {
            "strategies": ["R_FUTURES_LEAD"],
            "strategyObserverEnabled": [True],
            "strategyObserverVersions": [OBSERVER_VERSION],
        }
    )

    assert rules["strategies"] == ["R_FUTURES_LEAD"]
    assert rules["strategyObserverEnabled"] == [True]
    assert rules["strategyObserverVersions"] == [OBSERVER_VERSION]


def test_guard_remains_an_observer_version_not_a_live_strategy() -> None:
    assert OBSERVER_VERSION in live_trading.FUTURES_LEAD_OBSERVER_VERSIONS
    assert OBSERVER_VERSION not in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert "R_MICROPRICE_CONFIRM" in live_trading.LIVE_OBSERVER_STRATEGIES
