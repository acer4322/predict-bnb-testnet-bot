from __future__ import annotations

from tools.evaluate_wallet_shadow_negative_controls import transform
from tools.evaluate_wallet_shadow_sync import Event


def test_negative_control_shifts_and_flips_without_using_target_data() -> None:
    source = [Event(1_000, "UP", 0.25, "source")]

    result = transform(source, shift_ms=10_000, flip_side=True)

    assert result == [Event(11_000, "DOWN", 0.75, "CONTROL:10000:1:source")]
