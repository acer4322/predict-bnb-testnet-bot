from predict_bot import live_trading, research_forward


EXPECTED_SOURCES = (
    "R_MICROPRICE",
    "R_CALIBRATED_VALUE",
    "R_FUTURES_LEAD",
)


def _rules(strategy: str, mode: str) -> dict:
    return {
        "strategy": strategy,
        "strategies": [strategy],
        "maxStakeUsdt": 5.0 if mode == "CONFIRMATION_ADD" else 1.0,
        "strategyStakesUsdt": [
            5.0 if mode == "CONFIRMATION_ADD" else 1.0
        ],
        "strategyExecutionModes": [mode],
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


def test_confirmation_add_sources_replace_f1_with_futures_lead() -> None:
    assert research_forward.CONFIRMATION_ADD_SOURCE_STRATEGIES == EXPECTED_SOURCES
    assert live_trading.CONFIRMATION_ADD_SOURCE_STRATEGIES == EXPECTED_SOURCES
    assert "M01O_F1" not in EXPECTED_SOURCES


def test_futures_lead_accepts_live_confirmation_add() -> None:
    normalized = live_trading.normalize_live_rules(
        _rules("R_FUTURES_LEAD", "CONFIRMATION_ADD")
    )

    assert normalized["strategyExecutionModes"] == ["CONFIRMATION_ADD"]
    assert normalized["strategyInitialStakesUsdt"] == [1.0]
    assert normalized["strategyConfirmationAddStakesUsdt"] == [1.0]
    assert normalized["strategyStakesUsdt"] == [5.0]
    assert normalized["maxStakeUsdt"] == 5.0


def test_legacy_f1_confirmation_add_is_safely_downgraded() -> None:
    normalized = live_trading.normalize_live_rules(
        _rules("M01O_F1", "CONFIRMATION_ADD")
    )

    assert normalized["strategyExecutionModes"] == ["FIXED"]
    assert normalized["strategyStakesUsdt"] == [1.0]
    assert normalized["strategyInitialStakesUsdt"] == [1.0]
    assert normalized["maxStakeUsdt"] == 1.0
