from __future__ import annotations

from predict_bot import xpair_canary_autopilot_server as base
from predict_bot.xpair_canary_autopilot_server_v10 import (
    EXECUTION_ENTRY_SECONDS_LEFT,
    EXECUTION_MIN_SECONDS_LEFT,
    EXECUTION_WINDOW_SECONDS,
    execution_allowed,
    first_eligible_from_payload,
    normalize_first_eligible_payload,
)


def test_legacy_target_seconds_are_overridden() -> None:
    payload = normalize_first_eligible_payload(
        {
            "entrySecondsLeft": 180,
            "entryWindowSeconds": 10,
            "pairBudgetUsdt": 3,
        }
    )
    assert payload["entrySecondsLeft"] == 295.0
    assert payload["entryWindowSeconds"] == 275.0


def test_config_payload_cannot_restore_narrow_window() -> None:
    current = base.MonitorConfig()
    candidate = first_eligible_from_payload(
        base.MonitorConfig,
        {
            "entrySecondsLeft": 180,
            "entryWindowSeconds": 10,
            "pairBudgetUsdt": "3.00",
        },
        current,
    )
    assert candidate.entry_seconds_left == EXECUTION_ENTRY_SECONDS_LEFT
    assert candidate.entry_window_seconds == EXECUTION_WINDOW_SECONDS
    assert candidate.entry_seconds_left - candidate.entry_window_seconds == EXECUTION_MIN_SECONDS_LEFT


def test_safe_lifetime_boundaries() -> None:
    assert execution_allowed(296.0) is False
    assert execution_allowed(295.0) is True
    assert execution_allowed(180.0) is True
    assert execution_allowed(20.0) is True
    assert execution_allowed(19.999) is False
