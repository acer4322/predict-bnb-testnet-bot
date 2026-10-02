from predict_bot.poly_gap_live_v34 import _commitment_policy


def test_neutral_poly_retries_inside_exit_commitment_window():
    assert (
        _commitment_policy(
            direction="",
            held_side="DOWN",
            elapsed_ms=500,
            window_ms=1000,
        )
        == "RETRY_NEUTRAL_COMMITTED"
    )


def test_neutral_poly_waits_after_exit_commitment_window_expires():
    assert (
        _commitment_policy(
            direction="",
            held_side="DOWN",
            elapsed_ms=1001,
            window_ms=1000,
        )
        == "WAIT_NEUTRAL_EXPIRED"
    )


def test_explicit_opposite_direction_retries_even_after_window():
    assert (
        _commitment_policy(
            direction="UP",
            held_side="DOWN",
            elapsed_ms=5000,
            window_ms=1000,
        )
        == "RETRY_OPPOSITE"
    )


def test_explicit_return_to_held_side_cancels_commitment():
    assert (
        _commitment_policy(
            direction="DOWN",
            held_side="DOWN",
            elapsed_ms=100,
            window_ms=1000,
        )
        == "CANCEL_HELD_SIDE"
    )
