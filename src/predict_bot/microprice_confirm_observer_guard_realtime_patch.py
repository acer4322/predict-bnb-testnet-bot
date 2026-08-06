from __future__ import annotations

from functools import wraps
from typing import Any, Iterable


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD"


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _patch_live_rule_compatibility(live: Any) -> None:
    original = live.normalize_live_rules
    if getattr(original, "_microprice_confirm_observer_guard_compat_v1", False):
        return

    @wraps(original)
    def normalize_with_confirm_guard_compatibility(
        values: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = original(values, current)
        strategies = list(normalized.get("strategies") or [])
        versions = list(normalized.get("strategyObserverVersions") or [])
        for index, version in enumerate(versions):
            if str(version).strip().upper() != OBSERVER_VERSION:
                continue
            strategy = str(strategies[index] if index < len(strategies) else "").upper()
            if strategy != SOURCE_STRATEGY:
                raise ValueError(
                    f"{OBSERVER_VERSION} is only compatible with {SOURCE_STRATEGY}"
                )
        return normalized

    normalize_with_confirm_guard_compatibility._microprice_confirm_observer_guard_compat_v1 = True  # type: ignore[attr-defined]
    live.normalize_live_rules = normalize_with_confirm_guard_compatibility


def install_microprice_confirm_observer_guard_realtime_patch() -> None:
    """Refresh import-by-value live/realtime Observer integration points."""

    from . import live_trading as live
    from . import m_realtime as realtime

    realtime.LIVE_OBSERVER_STRATEGIES = _append_unique(
        realtime.LIVE_OBSERVER_STRATEGIES,
        SOURCE_STRATEGY,
    )
    _patch_live_rule_compatibility(live)
