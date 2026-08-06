from __future__ import annotations

import math
import sys
from functools import wraps
from typing import Any, Iterable

from . import research_forward as _research


PATCH_VERSION = "RESEARCH_PRIMARY_SHADOW_REGISTRY_V1"

# These strategies are evaluated directly from the rolling market sample buffer.
# Every other pre-existing research strategy is treated as derived-only Shadow
# unless it is explicitly promoted through register_primary_strategy().
DIRECT_SIGNAL_STRATEGIES = (
    "R_MICROPRICE",
    "R_OFI",
    "R_OFI_MIN040",
    "R_OFI_EVENT_CUM",
    "R_OFI_EVENT_CUM_FILTERED",
    "R_FUTURES_LEAD",
    "R_FUTURES_LEAD_EXIT30",
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_EXIT30_DISTANCE",
    "R_CALIBRATED_VALUE",
    "R_CONSENSUS",
)


def _normalized(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value or "").strip().upper()
            for value in values
            if str(value or "").strip()
        )
    )


def _finite_nonnegative(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number >= 0 else default


def _sync_known_consumers() -> None:
    """Refresh modules that imported registry values before a runtime addition."""
    server = sys.modules.get("predict_bot.server")
    if server is not None:
        server.PRIMARY_RESEARCH_STRATEGIES = _research.PRIMARY_RESEARCH_STRATEGIES
        server.SHADOW_RESEARCH_STRATEGIES = _research.SHADOW_RESEARCH_STRATEGIES
        server.RESEARCH_STRATEGIES = _research.RESEARCH_STRATEGIES
        server.RESEARCH_PARAMETERS = _research.RESEARCH_PARAMETERS
        server.research_sampling_active = _research.sampling_active
        server.research_signal_for_strategy = _research.signal_for_strategy

    realtime = sys.modules.get("predict_bot.m_realtime")
    if realtime is not None:
        realtime.RESEARCH_PARAMETERS = _research.RESEARCH_PARAMETERS


def _set_registry(
    primary: Iterable[str],
    shadow: Iterable[str],
) -> None:
    primary_values = _normalized(primary)
    primary_set = set(primary_values)
    shadow_values = tuple(
        strategy
        for strategy in _normalized(shadow)
        if strategy not in primary_set
    )
    _research.PRIMARY_RESEARCH_STRATEGIES = primary_values
    _research.SHADOW_RESEARCH_STRATEGIES = shadow_values
    _research.RESEARCH_STRATEGIES = (*primary_values, *shadow_values)
    _sync_known_consumers()


def _canonicalize_existing_registry() -> None:
    existing = _normalized(
        (
            *_research.RESEARCH_STRATEGIES,
            *_research.PRIMARY_RESEARCH_STRATEGIES,
            *_research.SHADOW_RESEARCH_STRATEGIES,
        )
    )
    available = set(existing) | set(_research.RESEARCH_PARAMETERS)
    primary = tuple(
        strategy for strategy in DIRECT_SIGNAL_STRATEGIES if strategy in available
    )
    shadow = tuple(strategy for strategy in existing if strategy not in set(primary))
    _set_registry(primary, shadow)


def register_primary_strategy(
    strategy: str,
    *,
    parameters: dict[str, float] | None = None,
) -> None:
    normalized = str(strategy or "").strip().upper()
    if not normalized:
        raise ValueError("primary research strategy name is required")
    if parameters is not None:
        _research.RESEARCH_PARAMETERS[normalized] = dict(parameters)
    _set_registry(
        (*_research.PRIMARY_RESEARCH_STRATEGIES, normalized),
        (
            item
            for item in _research.SHADOW_RESEARCH_STRATEGIES
            if item != normalized
        ),
    )
    errors = validate_research_strategy_registry()
    if errors:
        raise RuntimeError("; ".join(errors))


def register_shadow_strategy(
    strategy: str,
    *,
    parameters: dict[str, float] | None = None,
) -> None:
    normalized = str(strategy or "").strip().upper()
    if not normalized:
        raise ValueError("shadow research strategy name is required")
    if parameters is not None:
        # Mutate in place so modules that imported RESEARCH_PARAMETERS keep the
        # same live dictionary instead of retaining a stale pre-registration copy.
        _research.RESEARCH_PARAMETERS[normalized] = dict(parameters)
    _set_registry(
        (
            item
            for item in _research.PRIMARY_RESEARCH_STRATEGIES
            if item != normalized
        ),
        (*_research.SHADOW_RESEARCH_STRATEGIES, normalized),
    )


def validate_research_strategy_registry() -> tuple[str, ...]:
    errors: list[str] = []
    primary = _research.PRIMARY_RESEARCH_STRATEGIES
    shadow = _research.SHADOW_RESEARCH_STRATEGIES
    all_strategies = _research.RESEARCH_STRATEGIES

    overlap = sorted(set(primary) & set(shadow))
    if overlap:
        errors.append(f"primary/shadow overlap: {', '.join(overlap)}")
    expected = (*primary, *shadow)
    if all_strategies != expected:
        errors.append("RESEARCH_STRATEGIES is not primary + shadow")
    if len(all_strategies) != len(set(all_strategies)):
        errors.append("research strategy registry contains duplicates")

    for strategy in primary:
        params = _research.RESEARCH_PARAMETERS.get(strategy)
        if not isinstance(params, dict):
            errors.append(f"primary strategy {strategy} has no parameter mapping")
            continue
        horizon = _finite_nonnegative(params.get("horizon"), default=-1.0)
        if horizon <= 0:
            errors.append(f"primary strategy {strategy} has invalid horizon")
    return tuple(errors)


def _patch_sampling_active() -> None:
    if getattr(_research.sampling_active, "_primary_registry_v1", False):
        return

    def sampling_active_primary_only(
        seconds_left: float,
        enabled: set[str],
    ) -> bool:
        current_seconds = _finite_nonnegative(seconds_left, default=-1.0)
        if current_seconds < 0:
            return False
        enabled_set = set(_normalized(enabled))
        for strategy in _research.PRIMARY_RESEARCH_STRATEGIES:
            if strategy not in enabled_set:
                continue
            params = _research.RESEARCH_PARAMETERS.get(strategy)
            if not isinstance(params, dict):
                continue
            horizon = _finite_nonnegative(params.get("horizon"), default=-1.0)
            if horizon <= 0:
                continue
            lag = _finite_nonnegative(params.get("lag", 0.0))
            entry_delay = _finite_nonnegative(
                params.get("entry_delay_seconds", 3.0)
            )
            warmup = _finite_nonnegative(
                params.get("volatility_lookback_seconds", 0.0)
            )
            if (
                horizon - entry_delay
                <= current_seconds
                <= horizon + warmup + lag + 2.5
            ):
                return True
        return False

    sampling_active_primary_only._primary_registry_v1 = True  # type: ignore[attr-defined]
    _research.sampling_active = sampling_active_primary_only


def _patch_signal_for_strategy() -> None:
    original = _research.signal_for_strategy
    if getattr(original, "_primary_registry_v1", False):
        return

    @wraps(original)
    def signal_for_primary_strategy_only(
        strategy: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        normalized = str(strategy or "").strip().upper()
        if normalized not in _research.PRIMARY_RESEARCH_STRATEGIES:
            return None
        if normalized not in _research.RESEARCH_PARAMETERS:
            return None
        return original(normalized, *args, **kwargs)

    signal_for_primary_strategy_only._primary_registry_v1 = True  # type: ignore[attr-defined]
    _research.signal_for_strategy = signal_for_primary_strategy_only


def install_research_strategy_registry_patch() -> None:
    _canonicalize_existing_registry()
    _patch_sampling_active()
    _patch_signal_for_strategy()
    errors = validate_research_strategy_registry()
    if errors:
        raise RuntimeError("; ".join(errors))
    _sync_known_consumers()
