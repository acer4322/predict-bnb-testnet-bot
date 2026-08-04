from decimal import Decimal

from predict_bot import live_trading, m_realtime, research_forward
from predict_bot.microprice_confirm_live_patch import (
    MICROPRICE_CONFIRM_LIVE_STRATEGY,
)


def _confirmation_add_rules() -> dict:
    strategy = MICROPRICE_CONFIRM_LIVE_STRATEGY
    return {
        "strategy": strategy,
        "strategies": [strategy],
        "maxStakeUsdt": 5.0,
        "strategyStakesUsdt": [5.0],
        "strategyExecutionModes": ["CONFIRMATION_ADD"],
        "strategyInitialStakesUsdt": [1.0],
        "strategyConfirmationAddStakesUsdt": [1.0],
        "minHourlyWinRatePct": 50.0,
        "maxHourlyWinThenLossRatePct": 50.0,
        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyObserverEnabled": [False],
        "strategyObserverVersions": ["F1"],
        "strategyDrawdownControlEnabled": [False],
        "strategyLossCooldownEnabled": [False],
        "reliabilityGateTags": [],
    }


def test_microprice_confirm_is_registered_for_live_execution() -> None:
    strategy = MICROPRICE_CONFIRM_LIVE_STRATEGY

    assert strategy in live_trading.LIVE_RESEARCH_STRATEGIES
    assert strategy in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert strategy in m_realtime.LIVE_RESEARCH_STRATEGIES
    assert strategy in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert live_trading.LIVE_RESEARCH_REPRICE_GAPS[strategy] == Decimal("0.05")


def test_microprice_confirm_can_use_confirmation_add() -> None:
    strategy = MICROPRICE_CONFIRM_LIVE_STRATEGY
    normalized = live_trading.normalize_live_rules(_confirmation_add_rules())

    assert strategy in research_forward.CONFIRMATION_ADD_SOURCE_STRATEGIES
    assert strategy in live_trading.CONFIRMATION_ADD_SOURCE_STRATEGIES
    assert normalized["strategies"] == [strategy]
    assert normalized["strategyExecutionModes"] == ["CONFIRMATION_ADD"]
    assert normalized["strategyInitialStakesUsdt"] == [1.0]
    assert normalized["strategyConfirmationAddStakesUsdt"] == [1.0]
    assert normalized["strategyStakesUsdt"] == [5.0]
    assert normalized["maxStakeUsdt"] == 5.0
