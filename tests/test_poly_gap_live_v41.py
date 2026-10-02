from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v40 import ImmediateExitCautiousReentryPolyGapLiveEngine
from predict_bot.poly_gap_live_v41 import (
    MAX_POLY_SIGNAL_SOURCE_AGE_MS,
    POLY_SOURCE_FUTURE_TOLERANCE_MS,
    PolySourceFreshnessGuardPolyGapLiveEngine,
    source_freshness_policy,
)


def test_source_freshness_allows_fresh_quote() -> None:
    assert source_freshness_policy(
        source_age_ms=min(120.0, MAX_POLY_SIGNAL_SOURCE_AGE_MS),
        quote_receipt_age_ms=min(20.0, base.MAX_POLY_AGE_MS),
    ) == "ALLOW"


def test_source_freshness_blocks_old_source_even_when_receipt_is_fresh() -> None:
    assert source_freshness_policy(
        source_age_ms=MAX_POLY_SIGNAL_SOURCE_AGE_MS + 1.0,
        quote_receipt_age_ms=5.0,
    ) == "BLOCK_SOURCE_STALE"


def test_source_freshness_blocks_old_quote_receipt() -> None:
    assert source_freshness_policy(
        source_age_ms=min(120.0, MAX_POLY_SIGNAL_SOURCE_AGE_MS),
        quote_receipt_age_ms=base.MAX_POLY_AGE_MS + 1.0,
    ) == "BLOCK_QUOTE_RECEIPT_STALE"


def test_source_freshness_fails_closed_when_unavailable_or_clock_skewed() -> None:
    assert source_freshness_policy(
        source_age_ms=None,
        quote_receipt_age_ms=5.0,
    ) == "BLOCK_UNAVAILABLE"
    assert source_freshness_policy(
        source_age_ms=-(POLY_SOURCE_FUTURE_TOLERANCE_MS + 1.0),
        quote_receipt_age_ms=5.0,
    ) == "BLOCK_CLOCK_SKEW"


def test_v41_preserves_v40_exit_implementation() -> None:
    assert issubclass(
        PolySourceFreshnessGuardPolyGapLiveEngine,
        ImmediateExitCautiousReentryPolyGapLiveEngine,
    )
    assert (
        PolySourceFreshnessGuardPolyGapLiveEngine._observe_exit_flip
        is ImmediateExitCautiousReentryPolyGapLiveEngine._observe_exit_flip
    )
    assert (
        PolySourceFreshnessGuardPolyGapLiveEngine._exit_round
        is ImmediateExitCautiousReentryPolyGapLiveEngine._exit_round
    )
