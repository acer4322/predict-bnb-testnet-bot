from predict_bot.poly_gap_live_v36 import _entry_retry_policy


def test_entry_retry_policy_retries_same_side_inside_window() -> None:
    assert (
        _entry_retry_policy(
            direction="DOWN",
            intended_side="DOWN",
            elapsed_ms=200,
            retries_used=0,
            max_retries=2,
            window_ms=1200,
        )
        == "RETRY"
    )


def test_entry_retry_policy_waits_for_missing_or_neutral_poly() -> None:
    assert (
        _entry_retry_policy(
            direction="",
            intended_side="UP",
            elapsed_ms=300,
            retries_used=0,
            max_retries=2,
            window_ms=1200,
        )
        == "WAIT_FRESH_POLY"
    )


def test_entry_retry_policy_cancels_if_poly_flips() -> None:
    assert (
        _entry_retry_policy(
            direction="UP",
            intended_side="DOWN",
            elapsed_ms=300,
            retries_used=0,
            max_retries=2,
            window_ms=1200,
        )
        == "CANCEL_DIRECTION_CHANGED"
    )


def test_entry_retry_policy_exhausts_hard_retry_cap() -> None:
    assert (
        _entry_retry_policy(
            direction="UP",
            intended_side="UP",
            elapsed_ms=300,
            retries_used=2,
            max_retries=2,
            window_ms=1200,
        )
        == "EXHAUSTED"
    )


def test_entry_retry_policy_expires_commitment_window() -> None:
    assert (
        _entry_retry_policy(
            direction="UP",
            intended_side="UP",
            elapsed_ms=1201,
            retries_used=0,
            max_retries=2,
            window_ms=1200,
        )
        == "EXPIRED"
    )
