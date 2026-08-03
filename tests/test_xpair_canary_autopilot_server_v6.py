from __future__ import annotations

import pytest

from predict_bot.xpair_canary_autopilot_server_v6 import (
    state_payload,
    validate_button_arm_header,
)


def test_dashboard_confirmation_header_is_sufficient() -> None:
    validate_button_arm_header("confirmed")


def test_missing_dashboard_confirmation_header_is_rejected() -> None:
    with pytest.raises(ValueError, match="dashboard confirmation header"):
        validate_button_arm_header(None)


def test_typed_live_confirmation_is_not_advertised() -> None:
    policy = state_payload()["policy"]
    assert policy["typedLiveConfirmationRequired"] is False
    assert policy["liveArmConfirmation"] == "dashboard_button_and_browser_dialog"
    assert "liveConfirmationPhrase" not in policy
