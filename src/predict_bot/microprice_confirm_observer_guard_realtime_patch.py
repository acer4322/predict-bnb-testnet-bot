from __future__ import annotations

from typing import Iterable


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD"


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def install_microprice_confirm_observer_guard_realtime_patch() -> None:
    """Expose the guard to every strategy already supported by live Observer.

    The observer decision itself is strategy-agnostic.  This patch keeps the
    existing live Observer support boundary intact, while also adding
    R_MICROPRICE_CONFIRM to that boundary.  It deliberately does not register
    the observer version as a live strategy.
    """

    from . import live_trading as live
    from . import m_realtime as realtime

    live.LIVE_OBSERVER_STRATEGIES = _append_unique(
        live.LIVE_OBSERVER_STRATEGIES,
        SOURCE_STRATEGY,
    )
    realtime.LIVE_OBSERVER_STRATEGIES = tuple(live.LIVE_OBSERVER_STRATEGIES)
