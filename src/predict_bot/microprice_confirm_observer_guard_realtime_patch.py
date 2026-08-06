from __future__ import annotations

from typing import Iterable


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def install_microprice_confirm_observer_guard_realtime_patch() -> None:
    """Refresh m_realtime's import-by-value Observer support tuple."""

    from . import m_realtime as realtime

    realtime.LIVE_OBSERVER_STRATEGIES = _append_unique(
        realtime.LIVE_OBSERVER_STRATEGIES,
        SOURCE_STRATEGY,
    )
