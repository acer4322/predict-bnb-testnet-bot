from predict_bot import live_trading, research_forward
from predict_bot.live_trading import LiveLedger
from predict_bot.microprice_confirm_observer_guard_patch import OBSERVER_VERSION as TRANSITION_OBSERVER
from predict_bot.microprice_confirm_stable_consensus_guard import (
    OBSERVER_VERSION as STABLE_OBSERVER,
    STRATEGY as STABLE_STRATEGY,
)


def test_observer_versions_are_registered_before_rule_normalization() -> None:
    assert TRANSITION_OBSERVER in research_forward.FUTURES_LEAD_OBSERVER_VERSIONS
    assert STABLE_OBSERVER in research_forward.FUTURES_LEAD_OBSERVER_VERSIONS
    assert TRANSITION_OBSERVER in live_trading.FUTURES_LEAD_OBSERVER_VERSIONS
    assert STABLE_OBSERVER in live_trading.FUTURES_LEAD_OBSERVER_VERSIONS


def test_observer_rules_survive_live_ledger_restart(tmp_path) -> None:
    path = tmp_path / "live-observer-rules.db"
    expected = live_trading.normalize_live_rules(
        {
            "strategies": [STABLE_STRATEGY, "R_OFI"],
            "strategyStakesUsdt": [1.0, 1.0],
            "strategyObserverEnabled": [True, True],
            "strategyObserverVersions": [
                STABLE_OBSERVER,
                TRANSITION_OBSERVER,
            ],
        }
    )

    ledger = LiveLedger(path)
    ledger.set_live_rules(expected)
    ledger.db.close()

    reopened = LiveLedger(path)
    restored = live_trading.normalize_live_rules(reopened.live_rule_overrides())
    reopened.db.close()

    assert restored["strategies"] == expected["strategies"]
    assert restored["strategyObserverEnabled"] == [True, True]
    assert restored["strategyObserverVersions"] == [
        STABLE_OBSERVER,
        TRANSITION_OBSERVER,
    ]
    assert restored["futuresLeadObserverEnabled"] is True
    assert restored["futuresLeadObserverVersion"] == STABLE_OBSERVER
