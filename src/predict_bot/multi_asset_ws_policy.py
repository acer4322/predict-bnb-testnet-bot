from __future__ import annotations

from typing import Any


REQUIRED_OBSERVER_VERSION = "MULTI_PREDICTION_OBSERVER_V2"


def ws_generation_policy(
    *,
    observer_version: str | None,
    current_ws_session: Any,
    quote_ws_session: Any,
) -> str:
    """Fail-closed BUY authorization policy for the 8770 Poly quote generation."""
    if str(observer_version or "") != REQUIRED_OBSERVER_VERSION:
        return "BLOCK_OBSERVER_VERSION"
    try:
        current = int(current_ws_session or 0)
    except (TypeError, ValueError):
        current = 0
    try:
        quote = int(quote_ws_session or 0)
    except (TypeError, ValueError):
        quote = 0
    if current <= 0 or quote <= 0:
        return "BLOCK_SESSION_UNAVAILABLE"
    if current != quote:
        return "BLOCK_STALE_WS_GENERATION"
    return "ALLOW"
