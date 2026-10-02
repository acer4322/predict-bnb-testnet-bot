from __future__ import annotations

import os

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live_v7 import paired_generation_ready  # noqa: E402


def order(*, generation: int = 1, state: str = "FILLED", terminal_at_ms: int = 1_000) -> dict:
    return {
        "generation": generation,
        "state": state,
        "terminal_at_ms": terminal_at_ms,
    }


def test_unilateral_fill_does_not_replenish() -> None:
    ready, reason = paired_generation_ready(
        order(state="FILLED"),
        order(state="RESTING"),
        now_ms=10_000,
        delay_seconds=3,
        max_generations=6,
    )
    assert ready is False
    assert reason == "WAITING_COMPLEMENT_FILL"


def test_both_filled_wait_for_later_terminal_plus_delay() -> None:
    up = order(terminal_at_ms=1_000)
    down = order(terminal_at_ms=2_000)

    ready, reason = paired_generation_ready(
        up,
        down,
        now_ms=4_999,
        delay_seconds=3,
        max_generations=6,
    )
    assert ready is False
    assert reason == "PAIR_REPLENISH_COOLDOWN"

    ready, reason = paired_generation_ready(
        up,
        down,
        now_ms=5_000,
        delay_seconds=3,
        max_generations=6,
    )
    assert ready is True
    assert reason == "READY"


def test_generation_mismatch_is_blocked() -> None:
    ready, reason = paired_generation_ready(
        order(generation=2),
        order(generation=1),
        now_ms=10_000,
        delay_seconds=3,
        max_generations=6,
    )
    assert ready is False
    assert reason == "GENERATION_MISMATCH"


def test_generation_cap_is_blocked() -> None:
    ready, reason = paired_generation_ready(
        order(generation=6),
        order(generation=6),
        now_ms=10_000,
        delay_seconds=3,
        max_generations=6,
    )
    assert ready is False
    assert reason == "GENERATION_CAP_REACHED"
