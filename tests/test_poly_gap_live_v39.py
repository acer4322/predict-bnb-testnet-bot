from predict_bot.poly_gap_live_v39 import (
    FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS,
    fast_retry_signal_age_policy,
    post_quote_edge_policy,
)


def test_fast_retry_signal_age_allows_boundary() -> None:
    assert FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS == 1200
    assert fast_retry_signal_age_policy(1200) == "ALLOW"


def test_fast_retry_signal_age_expires_old_signal() -> None:
    assert fast_retry_signal_age_policy(1201) == "EXPIRED"
    assert fast_retry_signal_age_policy(4893) == "EXPIRED"
    assert fast_retry_signal_age_policy(6193) == "EXPIRED"
    assert fast_retry_signal_age_policy(8609) == "EXPIRED"
    assert fast_retry_signal_age_policy(12101) == "EXPIRED"


def test_fast_retry_signal_age_fails_closed_when_unavailable() -> None:
    assert fast_retry_signal_age_policy(None) == "EXPIRED_UNAVAILABLE"


def test_post_quote_edge_blocks_zero_and_negative_edge() -> None:
    assert post_quote_edge_policy(0.0) == "BLOCK_NON_POSITIVE"
    assert post_quote_edge_policy(-0.0291) == "BLOCK_NON_POSITIVE"
    assert post_quote_edge_policy(-0.115) == "BLOCK_NON_POSITIVE"


def test_post_quote_edge_allows_small_positive_edge() -> None:
    assert post_quote_edge_policy(0.0001) == "ALLOW"
    assert post_quote_edge_policy(0.012) == "ALLOW"


def test_post_quote_edge_fails_closed_when_unavailable() -> None:
    assert post_quote_edge_policy(None) == "BLOCK_UNAVAILABLE"
