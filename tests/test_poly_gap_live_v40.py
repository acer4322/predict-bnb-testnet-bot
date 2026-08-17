from predict_bot.poly_gap_live_v40 import (
    REVERSAL_REENTRY_CONFIRM_MS,
    REVERSAL_REENTRY_MIN_EDGE,
    reversal_reentry_confirmation_policy,
    reversal_reentry_edge_policy,
)


def test_reversal_reentry_requires_full_continuous_confirmation() -> None:
    assert reversal_reentry_confirmation_policy(
        target_side="DOWN", direction="DOWN", fresh=True, elapsed_ms=REVERSAL_REENTRY_CONFIRM_MS - 1
    ) == "WAIT_CONFIRMATION"
    assert reversal_reentry_confirmation_policy(
        target_side="DOWN", direction="DOWN", fresh=True, elapsed_ms=REVERSAL_REENTRY_CONFIRM_MS
    ) == "ELIGIBLE"


def test_reversal_reentry_resets_on_stale_neutral_or_original_direction() -> None:
    assert reversal_reentry_confirmation_policy(
        target_side="DOWN", direction="DOWN", fresh=False, elapsed_ms=999999
    ) == "RESET_STALE"
    assert reversal_reentry_confirmation_policy(
        target_side="DOWN", direction="NEUTRAL", fresh=True, elapsed_ms=999999
    ) == "RESET_NEUTRAL"
    assert reversal_reentry_confirmation_policy(
        target_side="DOWN", direction="UP", fresh=True, elapsed_ms=999999
    ) == "RESET_DIRECTION_CHANGED"


def test_reversal_reentry_executable_edge_floor_is_inclusive() -> None:
    assert reversal_reentry_edge_policy(None) == "BLOCK_UNAVAILABLE"
    assert reversal_reentry_edge_policy(REVERSAL_REENTRY_MIN_EDGE - 0.000001) == "BLOCK_EDGE"
    assert reversal_reentry_edge_policy(REVERSAL_REENTRY_MIN_EDGE) == "ALLOW"
    assert reversal_reentry_edge_policy(REVERSAL_REENTRY_MIN_EDGE + 0.01) == "ALLOW"
