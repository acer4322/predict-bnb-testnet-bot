from __future__ import annotations

from predict_bot.multi_asset_ws_policy import (
    REQUIRED_OBSERVER_VERSION,
    ws_generation_policy,
)


def test_current_ws_generation_is_required_for_buy_authorization() -> None:
    assert ws_generation_policy(
        observer_version=REQUIRED_OBSERVER_VERSION,
        current_ws_session=7,
        quote_ws_session=7,
    ) == "ALLOW"

    assert ws_generation_policy(
        observer_version=REQUIRED_OBSERVER_VERSION,
        current_ws_session=8,
        quote_ws_session=7,
    ) == "BLOCK_STALE_WS_GENERATION"


def test_missing_ws_generation_fails_closed() -> None:
    assert ws_generation_policy(
        observer_version=REQUIRED_OBSERVER_VERSION,
        current_ws_session=0,
        quote_ws_session=0,
    ) == "BLOCK_SESSION_UNAVAILABLE"

    assert ws_generation_policy(
        observer_version=REQUIRED_OBSERVER_VERSION,
        current_ws_session=9,
        quote_ws_session=None,
    ) == "BLOCK_SESSION_UNAVAILABLE"


def test_old_observer_version_cannot_authorize_buy() -> None:
    assert ws_generation_policy(
        observer_version="MULTI_PREDICTION_OBSERVER_V1",
        current_ws_session=5,
        quote_ws_session=5,
    ) == "BLOCK_OBSERVER_VERSION"

    assert ws_generation_policy(
        observer_version=None,
        current_ws_session=5,
        quote_ws_session=5,
    ) == "BLOCK_OBSERVER_VERSION"
