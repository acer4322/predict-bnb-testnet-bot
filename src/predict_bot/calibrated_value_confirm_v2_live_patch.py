from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any


STRATEGY = "R_CALIBRATED_VALUE_CONFIRM_V2"
LIVE_POLICY_VERSION = "CALIBRATED_VALUE_CONFIRM_V2_LIVE_V1"
REPRICE_GAP = Decimal("0.05")
MIN_SECONDS_LEFT = Decimal("55")
MAX_SECONDS_LEFT = Decimal("61")
MAX_RAW_TOP_ASK = Decimal("0.70")


def _append_unique(values: Any, value: str) -> tuple[str, ...]:
    normalized = tuple(str(item) for item in values)
    return normalized if value in normalized else (*normalized, value)


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _install_signal_provenance_guard(live: Any) -> None:
    engine_class = live.LiveM0WEngine
    original = engine_class._research_signal_price_is_allowed
    if getattr(original, "_calibrated_value_confirm_v2_live_v1", False):
        return

    @wraps(original)
    def validate_confirm_v2_signal(
        signal: dict[str, Any],
        strategy: str,
    ) -> tuple[bool, str]:
        allowed, reason = original(signal, strategy)
        if not allowed or str(strategy).upper() != STRATEGY:
            return allowed, reason
        if signal.get("calibrated_value_confirmation_variant") is not True:
            return False, "Calibrated Value Confirm V2 signal provenance is missing"
        if str(signal.get("variant_mode") or "") != "FOLLOW_CONFIRMED_REPRICING":
            return False, "Calibrated Value Confirm V2 only allows the confirmed follow variant"
        if signal.get("market_data_integrity_ok") is not True:
            return False, "Calibrated Value Confirm V2 market-data integrity is not verified"
        seconds_left = _decimal(signal.get("seconds_left"))
        if (
            seconds_left is None
            or seconds_left < MIN_SECONDS_LEFT
            or seconds_left > MAX_SECONDS_LEFT
        ):
            return False, "Calibrated Value Confirm V2 signal is outside its frozen 55-61s window"
        raw_top_ask = _decimal(signal.get("raw_top_ask"))
        if (
            raw_top_ask is None
            or raw_top_ask <= 0
            or raw_top_ask > MAX_RAW_TOP_ASK
        ):
            return False, "Calibrated Value Confirm V2 raw top ask exceeds 0.70"
        return True, ""

    validate_confirm_v2_signal._calibrated_value_confirm_v2_live_v1 = True  # type: ignore[attr-defined]
    engine_class._research_signal_price_is_allowed = staticmethod(
        validate_confirm_v2_signal
    )


def install_calibrated_value_confirm_v2_live_patch() -> None:
    """Expose only the confirmed follow variant to the existing live executor."""
    from . import live_trading as live
    from . import m_realtime as realtime
    from . import research_forward as research

    live.LIVE_RESEARCH_STRATEGIES = _append_unique(
        live.LIVE_RESEARCH_STRATEGIES,
        STRATEGY,
    )
    live.LIVE_SUPPORTED_STRATEGIES = _append_unique(
        live.LIVE_SUPPORTED_STRATEGIES,
        STRATEGY,
    )
    live.LIVE_RESEARCH_REPRICE_GAPS[STRATEGY] = REPRICE_GAP

    realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES = set(
        realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    ) | {STRATEGY}

    # The all-live confirmation-add patch snapshots the supported list during
    # startup, so append this late-installed strategy to both runtime copies.
    live.CONFIRMATION_ADD_SOURCE_STRATEGIES = _append_unique(
        live.CONFIRMATION_ADD_SOURCE_STRATEGIES,
        STRATEGY,
    )
    research.CONFIRMATION_ADD_SOURCE_STRATEGIES = _append_unique(
        research.CONFIRMATION_ADD_SOURCE_STRATEGIES,
        STRATEGY,
    )

    _install_signal_provenance_guard(live)
