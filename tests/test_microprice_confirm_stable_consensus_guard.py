from predict_bot import live_trading, research_forward
from predict_bot.microprice_confirm_stable_consensus_guard import (
    MAX_RAW_TOP_ASK_EXCLUSIVE,
    MIN_RAW_TOP_ASK,
    OBSERVER_VERSION,
    STRATEGY,
    stable_consensus_signal_decision,
)


def ready_gate(
    *,
    market_id: int = 9001,
    state: str = "RANGE",
    range_score: int = 4,
    trend_score: int = 0,
) -> dict[str, object]:
    return {
        "allowed": True,
        "status": "ALLOW",
        "profile": "F1",
        "dataQualityStatus": "READY",
        "historicalSampleCount": 20,
        "minSettledSamples": 6,
        "currentMarketId": market_id,
        "historicalState": state,
        "historicalRangeScore": range_score,
        "historicalTrendScore": trend_score,
        "historicalProvisional": False,
    }


def test_stable_consensus_accepts_only_frozen_price_band() -> None:
    gate = ready_gate()

    assert stable_consensus_signal_decision(
        gate,
        MIN_RAW_TOP_ASK,
        expected_market_id=9001,
    )["allowed"] is True
    assert stable_consensus_signal_decision(
        gate,
        MAX_RAW_TOP_ASK_EXCLUSIVE - 0.000001,
        expected_market_id=9001,
    )["allowed"] is True

    below = stable_consensus_signal_decision(
        gate,
        MIN_RAW_TOP_ASK - 0.000001,
        expected_market_id=9001,
    )
    at_upper = stable_consensus_signal_decision(
        gate,
        MAX_RAW_TOP_ASK_EXCLUSIVE,
        expected_market_id=9001,
    )
    assert below["allowed"] is False
    assert at_upper["allowed"] is False


def test_stable_consensus_retains_transition_observer_block() -> None:
    gate = ready_gate(
        state="UNCERTAIN",
        range_score=2,
        trend_score=3,
    )
    decision = stable_consensus_signal_decision(
        gate,
        0.70,
        expected_market_id=9001,
    )

    assert decision["allowed"] is False
    assert decision["transitionRisk"] is True


def test_stable_consensus_fails_closed_without_gate_or_price() -> None:
    assert stable_consensus_signal_decision(
        None,
        0.70,
        expected_market_id=9001,
    )["allowed"] is False
    assert stable_consensus_signal_decision(
        ready_gate(),
        None,
        expected_market_id=9001,
    )["allowed"] is False


def test_strategy_and_observer_version_are_registered_for_live_selection() -> None:
    assert STRATEGY in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert STRATEGY in live_trading.LIVE_RESEARCH_STRATEGIES
    assert STRATEGY in live_trading.LIVE_OBSERVER_STRATEGIES
    assert OBSERVER_VERSION in research_forward.FUTURES_LEAD_OBSERVER_VERSIONS
    assert OBSERVER_VERSION in live_trading.FUTURES_LEAD_OBSERVER_VERSIONS
    assert OBSERVER_VERSION not in live_trading.LIVE_SUPPORTED_STRATEGIES


def test_live_rules_preserve_strategy_and_observer_version() -> None:
    rules = live_trading.normalize_live_rules(
        {
            "strategies": [STRATEGY],
            "strategyObserverEnabled": [True],
            "strategyObserverVersions": [OBSERVER_VERSION],
            "strategyStakesUsdt": [1.0],
        }
    )

    assert rules["strategies"] == [STRATEGY]
    assert rules["strategyObserverEnabled"] == [True]
    assert rules["strategyObserverVersions"] == [OBSERVER_VERSION]
