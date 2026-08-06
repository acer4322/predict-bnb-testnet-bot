from __future__ import annotations

import math

from predict_bot import research_forward
from predict_bot.research_strategy_registry_patch import (
    DIRECT_SIGNAL_STRATEGIES,
    validate_research_strategy_registry,
)


DIRECT_VARIANTS = {
    "R_OFI_MIN040",
    "R_OFI_EVENT_CUM",
    "R_OFI_EVENT_CUM_FILTERED",
    "R_FUTURES_LEAD_EXIT30",
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_EXIT30_DISTANCE",
}

DERIVED_VARIANTS = {
    "R_CALIBRATED_VALUE_CONTINUOUS_V2",
    "R_MICROPRICE_REVERSE",
    "R_CALIBRATED_VALUE_REVERSE",
    "R_FUTURES_LEAD_CONTINUOUS_V2",
    "R_FUTURES_LEAD_REVERSE",
    "R_FUTURES_LEAD_REGIME_REVERSE_3L",
    "R_FUTURES_LEAD_SIGNAL_100",
    "R_FUTURES_LEAD_MIN_ENTRY_020",
    "R_FUTURES_LEAD_OBSERVER_F1",
    "R_OFI_OBSERVER_V3",
    "R_MICROPRICE_OBSERVER_V3",
    "R_MICROPRICE_CONFIRM_STABLE_DIRECTION",
}


def test_registry_is_disjoint_complete_and_valid() -> None:
    primary = research_forward.PRIMARY_RESEARCH_STRATEGIES
    shadow = research_forward.SHADOW_RESEARCH_STRATEGIES

    assert set(primary).isdisjoint(shadow)
    assert research_forward.RESEARCH_STRATEGIES == (*primary, *shadow)
    assert len(research_forward.RESEARCH_STRATEGIES) == len(
        set(research_forward.RESEARCH_STRATEGIES)
    )
    assert validate_research_strategy_registry() == ()


def test_all_direct_signal_variants_are_primary() -> None:
    assert set(DIRECT_SIGNAL_STRATEGIES).issubset(
        research_forward.PRIMARY_RESEARCH_STRATEGIES
    )
    assert DIRECT_VARIANTS.issubset(
        research_forward.PRIMARY_RESEARCH_STRATEGIES
    )


def test_derived_variants_are_shadow_only() -> None:
    assert DERIVED_VARIANTS.issubset(
        research_forward.SHADOW_RESEARCH_STRATEGIES
    )
    assert DERIVED_VARIANTS.isdisjoint(
        research_forward.PRIMARY_RESEARCH_STRATEGIES
    )


def test_every_primary_strategy_has_a_finite_positive_horizon() -> None:
    for strategy in research_forward.PRIMARY_RESEARCH_STRATEGIES:
        horizon = float(research_forward.RESEARCH_PARAMETERS[strategy]["horizon"])
        assert math.isfinite(horizon)
        assert horizon > 0


def test_sampling_ignores_shadow_and_unknown_strategies_without_raising() -> None:
    assert research_forward.sampling_active(
        180.0,
        {"R_MICROPRICE_REVERSE"},
    ) is False
    assert research_forward.sampling_active(
        180.0,
        {"R_UNKNOWN_RESEARCH_STRATEGY"},
    ) is False
    assert research_forward.sampling_active(
        180.0,
        {"R_MICROPRICE_REVERSE", "R_MICROPRICE"},
    ) is True


def test_generic_signal_path_fails_closed_for_shadow_and_unknown() -> None:
    for strategy in (
        "R_MICROPRICE_REVERSE",
        "R_MICROPRICE_CONFIRM_STABLE_DIRECTION",
        "R_UNKNOWN_RESEARCH_STRATEGY",
    ):
        assert research_forward.signal_for_strategy(
            strategy,
            {},
            None,
            fee_bps=200,
            slippage_bps=50.0,
        ) is None
