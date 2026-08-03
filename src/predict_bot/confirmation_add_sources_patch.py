from __future__ import annotations

import json
from functools import wraps
from typing import Any


CONFIRMATION_ADD_SOURCE_STRATEGIES_V2 = (
    "R_MICROPRICE",
    "R_CALIBRATED_VALUE",
    "R_FUTURES_LEAD",
)


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return list(decoded) if isinstance(decoded, list) else [value]
    return []


def _migrate_legacy_f1_confirmation_add(
    values: dict[str, Any],
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    """Downgrade obsolete F1 confirmation-add slots to a fixed initial order.

    This is intentionally fail-safe for rules already persisted before the
    source list changed. New R_FUTURES_LEAD slots remain eligible for the full
    five-stage confirmation ladder.
    """
    candidate = {**(current or {}), **values}
    strategies = _list_value(candidate.get("strategies"))
    if not strategies and candidate.get("strategy"):
        strategies = [candidate["strategy"]]
    strategies = [str(value or "").strip().upper() for value in strategies]
    if not strategies:
        return candidate

    modes = _list_value(candidate.get("strategyExecutionModes"))
    totals = _list_value(candidate.get("strategyStakesUsdt"))
    initials = _list_value(candidate.get("strategyInitialStakesUsdt"))
    adds = _list_value(candidate.get("strategyConfirmationAddStakesUsdt"))

    def padded(raw: list[Any], fallback: Any) -> list[Any]:
        default = raw[0] if raw else fallback
        return (raw + [default] * len(strategies))[: len(strategies)]

    modes = padded(modes, "FIXED")
    totals = padded(totals, candidate.get("maxStakeUsdt", 1.0))
    initials = padded(initials, totals[0] if totals else 1.0)
    adds = padded(adds, 1.0)

    changed = False
    for index, strategy in enumerate(strategies):
        if (
            strategy == "M01O_F1"
            and str(modes[index] or "").strip().upper() == "CONFIRMATION_ADD"
        ):
            modes[index] = "FIXED"
            totals[index] = initials[index]
            changed = True

    if not changed:
        return candidate

    candidate["strategy"] = strategies[0]
    candidate["strategies"] = strategies
    candidate["strategyExecutionModes"] = modes
    candidate["strategyStakesUsdt"] = totals
    candidate["strategyInitialStakesUsdt"] = initials
    candidate["strategyConfirmationAddStakesUsdt"] = adds
    candidate["maxStakeUsdt"] = totals[0]
    return candidate


def install_confirmation_add_sources_patch() -> None:
    from . import live_trading as live
    from . import research_forward as research

    research.CONFIRMATION_ADD_SOURCE_STRATEGIES = (
        CONFIRMATION_ADD_SOURCE_STRATEGIES_V2
    )
    live.CONFIRMATION_ADD_SOURCE_STRATEGIES = (
        CONFIRMATION_ADD_SOURCE_STRATEGIES_V2
    )

    original = live.normalize_live_rules
    if getattr(original, "_confirmation_add_sources_v2", False):
        return

    @wraps(original)
    def normalize_with_confirmation_add_sources_v2(
        values: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        migrated = _migrate_legacy_f1_confirmation_add(values, current)
        return original(migrated, None)

    normalize_with_confirmation_add_sources_v2._confirmation_add_sources_v2 = True  # type: ignore[attr-defined]
    live.normalize_live_rules = normalize_with_confirmation_add_sources_v2
