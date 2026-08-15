from decimal import Decimal

from predict_bot import (
    live_trading,
    m_realtime,
    microprice_variants,
    research_forward,
)
from predict_bot.microprice_confirm_live_patch import (
    MICROPRICE_CONFIRM_LIVE_PATCH_VERSION,
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

    assert MICROPRICE_CONFIRM_LIVE_PATCH_VERSION == "MICROPRICE_CONFIRM_LIVE_V2"
    assert strategy in live_trading.LIVE_RESEARCH_STRATEGIES
    assert strategy in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert strategy in m_realtime.LIVE_RESEARCH_STRATEGIES
    assert strategy in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert "R_MICROPRICE_REVERSION" not in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert live_trading.LIVE_RESEARCH_REPRICE_GAPS[strategy] == Decimal("0.05")


def test_microprice_confirm_live_source_uses_exact_relaxed_v2_profile() -> None:
    assert microprice_variants.MICROPRICE_VARIANT_VERSION == (
        "MICROPRICE_VARIANTS_V2_RELAXED"
    )
    assert microprice_variants.DIRECT_OUTCOME_DATA_SOURCE == "dual_token_rest"
    assert microprice_variants.MICROPRICE_THRESHOLD == 0.20
    assert microprice_variants.MICROPRICE_MIN_CONFIRMATIONS == 2
    assert microprice_variants.MICROPRICE_MIN_CONFIRMATION_MS == 150.0
    assert microprice_variants.MICROPRICE_MAX_BOOK_AGE_MS == 500.0
    assert microprice_variants.MICROPRICE_MAX_BOOK_SKEW_MS == 150.0
    assert microprice_variants.MICROPRICE_MIN_MIDPOINT_MOVE == 0.0005
    assert microprice_variants.MICROPRICE_MIN_RETAINED_STRENGTH == 0.65
    assert microprice_variants.MICROPRICE_WINDOW_MIN_SECONDS_LEFT == 170.0
    assert microprice_variants.MICROPRICE_WINDOW_MAX_SECONDS_LEFT == 181.0
    assert microprice_variants.MICROPRICE_STAKE_USDT == 5.0
    assert microprice_variants.MICROPRICE_SLIPPAGE_BPS == 50.0


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
