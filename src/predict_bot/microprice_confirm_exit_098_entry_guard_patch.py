from __future__ import annotations

from decimal import Decimal
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from . import microprice_variants as _variants
from .microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
    EXIT_TARGET_PRICE,
)


MAX_LIVE_ENTRY_PRICE = Decimal("0.97999999")
MIN_LIVE_ENTRY_PRICE = Decimal("0.00000001")


def install_microprice_confirm_exit_098_entry_guard_patch() -> None:
    from . import live_trading as live

    live.LIVE_RESEARCH_PRICE_RANGES = {
        **live.LIVE_RESEARCH_PRICE_RANGES,
        EXIT_098_STRATEGY: (
            MIN_LIVE_ENTRY_PRICE,
            MAX_LIVE_ENTRY_PRICE,
        ),
    }

    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_microprice_exit_098_entry_guard_v1", False):
        return

    @wraps(original)
    def process_with_exit_098_entry_guard(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        opened = list(
            original(self, snapshot, int(fee_bps), context) or []
        )
        filtered: list[dict[str, Any]] = []
        for candidate in opened:
            if str(candidate.get("strategy") or "").upper() != EXIT_098_STRATEGY:
                filtered.append(candidate)
                continue
            target = candidate.get("live_exit_target_price")
            entry = candidate.get("entry_price")
            try:
                target_value = float(target)
                entry_value = float(entry)
            except (TypeError, ValueError):
                continue
            if (
                abs(target_value - EXIT_TARGET_PRICE) > 1e-12
                or not 0 < entry_value < EXIT_TARGET_PRICE
            ):
                continue
            filtered.append(candidate)
        return filtered

    process_with_exit_098_entry_guard._microprice_exit_098_entry_guard_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_exit_098_entry_guard
