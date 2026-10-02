import pytest

from predict_bot import live_trading, research_forward


EXPECTED_SOURCES = tuple(live_trading.LIVE_SUPPORTED_STRATEGIES)


def _rules(strategies: list[str], modes: list[str]) -> dict:
    initials = [1.0 for _ in strategies]
    adds = [1.0 for _ in strategies]
    totals = [
        initial + add * 4 if mode == "CONFIRMATION_ADD" else initial
        for initial, add, mode in zip(initials, adds, modes)
    ]
    return {
        "strategy": strategies[0],
        "strategies": strategies,
        "maxStakeUsdt": totals[0],
        "strategyStakesUsdt": totals,
        "strategyExecutionModes": modes,
        "strategyInitialStakesUsdt": initials,
        "strategyConfirmationAddStakesUsdt": adds,
        "minHourlyWinRatePct": 50.0,
        "maxHourlyWinThenLossRatePct": 50.0,
        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyObserverEnabled": [False for _ in strategies],
        "strategyObserverVersions": ["F1" for _ in strategies],
        "strategyDrawdownControlEnabled": [False for _ in strategies],
        "strategyLossCooldownEnabled": [False for _ in strategies],
        "reliabilityGateTags": [],
    }


def test_confirmation_add_sources_cover_every_live_strategy() -> None:
    assert research_forward.CONFIRMATION_ADD_SOURCE_STRATEGIES == EXPECTED_SOURCES
    assert live_trading.CONFIRMATION_ADD_SOURCE_STRATEGIES == EXPECTED_SOURCES
    assert "M01O_F1" in EXPECTED_SOURCES
    assert "R_FUTURES_LEAD" in EXPECTED_SOURCES
    assert "PAIR_ARB_010" in EXPECTED_SOURCES


@pytest.mark.parametrize(
    "strategy",
    [
        "M01O_F1",
        "R_FUTURES_LEAD",
        "R_MICROPRICE",
        "R_OFI",
        "R_CALIBRATED_VALUE",
        "R_OFI_EVENT_CUM",
        "PAIR_ARB_010",
        "PAIR_ARB_QC_015",
        "PAIR_ARB_020",
    ],
)
def test_each_independent_live_strategy_accepts_confirmation_add(
    strategy: str,
) -> None:
    normalized = live_trading.normalize_live_rules(
        _rules([strategy], ["CONFIRMATION_ADD"])
    )

    assert normalized["strategyExecutionModes"] == ["CONFIRMATION_ADD"]
    assert normalized["strategyInitialStakesUsdt"] == [1.0]
    assert normalized["strategyConfirmationAddStakesUsdt"] == [1.0]
    assert normalized["strategyStakesUsdt"] == [5.0]
    assert normalized["maxStakeUsdt"] == 5.0


def test_dependent_reverse_strategy_accepts_confirmation_add_in_valid_pair() -> None:
    normalized = live_trading.normalize_live_rules(
        _rules(
            ["R_FUTURES_LEAD", "R_FUTURES_LEAD_REVERSE"],
            ["CONFIRMATION_ADD", "CONFIRMATION_ADD"],
        )
    )

    assert normalized["strategyExecutionModes"] == [
        "CONFIRMATION_ADD",
        "CONFIRMATION_ADD",
    ]
    assert normalized["strategyStakesUsdt"] == [5.0, 5.0]


def test_confirmation_add_total_cap_still_cannot_exceed_hard_limit() -> None:
    rules = _rules(["R_OFI"], ["CONFIRMATION_ADD"])
    rules["strategyInitialStakesUsdt"] = [60.0]
    rules["strategyConfirmationAddStakesUsdt"] = [11.0]
    rules["strategyStakesUsdt"] = [104.0]
    rules["maxStakeUsdt"] = 104.0

    with pytest.raises(ValueError, match="may not exceed"):
        live_trading.normalize_live_rules(rules)
