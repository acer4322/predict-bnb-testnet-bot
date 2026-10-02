from __future__ import annotations

import math
import sys
from functools import wraps
from typing import Any, Iterable

from . import research_forward as _research


PATCH_VERSION = "RESEARCH_PRIMARY_SHADOW_REGISTRY_V2"

# PRIMARY/SHADOW describe capital and ledger isolation. They do not describe
# whether a strategy receives an independent generic signal. Several research
# experiments remain isolated Shadows while still evaluating their own signal.
GENERIC_SIGNAL_STRATEGIES = (
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


def _generic_registry() -> tuple[str, ...]:
    return tuple(
        getattr(_research, "GENERIC_SIGNAL_RESEARCH_STRATEGIES", ())
    )


def _generic_parameter_view() -> dict[str, dict[str, float]]:
    return {
        strategy: _research.RESEARCH_PARAMETERS[strategy]
        for strategy in _generic_registry()
        if strategy in _research.RESEARCH_PARAMETERS
    }


def _sync_known_consumers() -> None:
    """Refresh modules that imported registry values before a runtime addition."""
    server = sys.modules.get("predict_bot.server")
    if server is not None:
        server.PRIMARY_RESEARCH_STRATEGIES = _research.PRIMARY_RESEARCH_STRATEGIES
        server.SHADOW_RESEARCH_STRATEGIES = _research.SHADOW_RESEARCH_STRATEGIES
        server.RESEARCH_STRATEGIES = _research.RESEARCH_STRATEGIES
        server.GENERIC_SIGNAL_RESEARCH_STRATEGIES = (
            _research.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        )
        server.DERIVED_RESEARCH_STRATEGIES = _research.DERIVED_RESEARCH_STRATEGIES
        server.RESEARCH_PARAMETERS = _research.RESEARCH_PARAMETERS
        server.research_sampling_active = _research.sampling_active
        server.research_signal_for_strategy = _research.signal_for_strategy

        default_config = getattr(server, "DEFAULT_CONFIG", None)
        if isinstance(default_config, dict):
            for strategy in _research.RESEARCH_STRATEGIES:
                prefix = f"strategy_{strategy.lower()}"
                default_config.setdefault(f"{prefix}_enabled", False)
                default_config.setdefault(f"{prefix}_stake", 5.0)

        supported = getattr(server, "SUPPORTED_STRATEGIES", None)
        if isinstance(supported, tuple):
            server.SUPPORTED_STRATEGIES = tuple(
                dict.fromkeys((*supported, *_research.RESEARCH_STRATEGIES))
            )

    realtime = sys.modules.get("predict_bot.m_realtime")
    if realtime is not None:
        # m_realtime uses this mapping to extend its event-evaluation horizon.
        # Generic-signal Shadows need that horizon; derived-only Shadows do not.
        realtime.RESEARCH_PARAMETERS = _generic_parameter_view()


def _set_registry(
    primary: Iterable[str],
    shadow: Iterable[str],
    generic: Iterable[str],
) -> None:
    primary_values = _normalized(primary)
    primary_set = set(primary_values)
    shadow_values = tuple(
        strategy
        for strategy in _normalized(shadow)
        if strategy not in primary_set
    )
    all_values = (*primary_values, *shadow_values)
    all_set = set(all_values)
    generic_values = tuple(
        strategy for strategy in _normalized(generic) if strategy in all_set
    )
    generic_set = set(generic_values)

    _research.PRIMARY_RESEARCH_STRATEGIES = primary_values
    _research.SHADOW_RESEARCH_STRATEGIES = shadow_values
    _research.RESEARCH_STRATEGIES = all_values
    _research.GENERIC_SIGNAL_RESEARCH_STRATEGIES = generic_values
    _research.DERIVED_RESEARCH_STRATEGIES = tuple(
        strategy for strategy in all_values if strategy not in generic_set
    )
    _sync_known_consumers()


def _canonicalize_existing_registry() -> None:
    primary = _normalized(_research.PRIMARY_RESEARCH_STRATEGIES)
    primary_set = set(primary)
    existing = _normalized(
        (
            *_research.RESEARCH_STRATEGIES,
            *primary,
            *_research.SHADOW_RESEARCH_STRATEGIES,
        )
    )
    shadow = tuple(strategy for strategy in existing if strategy not in primary_set)
    available = set(existing) | set(_research.RESEARCH_PARAMETERS)
    generic = tuple(
        strategy for strategy in GENERIC_SIGNAL_STRATEGIES if strategy in available
    )
    _set_registry(primary, shadow, generic)


def register_primary_strategy(
    strategy: str,
    *,
    parameters: dict[str, float] | None = None,
    generic_signal: bool = True,
) -> None:
    normalized = str(strategy or "").strip().upper()
    if not normalized:
        raise ValueError("primary research strategy name is required")
    if parameters is not None:
        _research.RESEARCH_PARAMETERS[normalized] = dict(parameters)
    generic = _generic_registry()
    if generic_signal:
        generic = (*generic, normalized)
    else:
        generic = tuple(item for item in generic if item != normalized)
    _set_registry(
        (*_research.PRIMARY_RESEARCH_STRATEGIES, normalized),
        (
            item
            for item in _research.SHADOW_RESEARCH_STRATEGIES
            if item != normalized
        ),
        generic,
    )
    errors = validate_research_strategy_registry()
    if errors:
        raise RuntimeError("; ".join(errors))


def register_shadow_strategy(
    strategy: str,
    *,
    parameters: dict[str, float] | None = None,
    generic_signal: bool = False,
) -> None:
    normalized = str(strategy or "").strip().upper()
    if not normalized:
        raise ValueError("shadow research strategy name is required")
    if parameters is not None:
        # Mutate in place so modules that imported RESEARCH_PARAMETERS keep the
        # same live dictionary instead of retaining a stale pre-registration copy.
        _research.RESEARCH_PARAMETERS[normalized] = dict(parameters)
    generic = _generic_registry()
    if generic_signal:
        generic = (*generic, normalized)
    else:
        generic = tuple(item for item in generic if item != normalized)
    _set_registry(
        (
            item
            for item in _research.PRIMARY_RESEARCH_STRATEGIES
            if item != normalized
        ),
        (*_research.SHADOW_RESEARCH_STRATEGIES, normalized),
        generic,
    )


def validate_research_strategy_registry() -> tuple[str, ...]:
    errors: list[str] = []
    primary = _research.PRIMARY_RESEARCH_STRATEGIES
    shadow = _research.SHADOW_RESEARCH_STRATEGIES
    all_strategies = _research.RESEARCH_STRATEGIES
    generic = _research.GENERIC_SIGNAL_RESEARCH_STRATEGIES
    derived = _research.DERIVED_RESEARCH_STRATEGIES

    overlap = sorted(set(primary) & set(shadow))
    if overlap:
        errors.append(f"primary/shadow overlap: {', '.join(overlap)}")
    expected = (*primary, *shadow)
    if all_strategies != expected:
        errors.append("RESEARCH_STRATEGIES is not primary + shadow")
    if len(all_strategies) != len(set(all_strategies)):
        errors.append("research strategy registry contains duplicates")

    if set(generic) & set(derived):
        errors.append("generic/derived signal registries overlap")
    if set(generic) | set(derived) != set(all_strategies):
        errors.append("generic/derived registries do not cover all research strategies")

    for strategy in generic:
        params = _research.RESEARCH_PARAMETERS.get(strategy)
        if not isinstance(params, dict):
            errors.append(f"generic strategy {strategy} has no parameter mapping")
            continue
        horizon = _finite_nonnegative(params.get("horizon"), default=-1.0)
        if horizon <= 0:
            errors.append(f"generic strategy {strategy} has invalid horizon")
    return tuple(errors)


def _patch_sampling_active() -> None:
    if getattr(_research.sampling_active, "_research_registry_v2", False):
        return

    def sampling_active_generic_only(
        seconds_left: float,
        enabled: set[str],
    ) -> bool:
        current_seconds = _finite_nonnegative(seconds_left, default=-1.0)
        if current_seconds < 0:
            return False
        enabled_set = set(_normalized(enabled))
        for strategy in _generic_registry():
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

    sampling_active_generic_only._research_registry_v2 = True  # type: ignore[attr-defined]
    _research.sampling_active = sampling_active_generic_only


def _patch_signal_for_strategy() -> None:
    original = _research.signal_for_strategy
    if getattr(original, "_research_registry_v2", False):
        return

    @wraps(original)
    def signal_for_generic_strategy_only(
        strategy: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        normalized = str(strategy or "").strip().upper()
        if normalized not in _generic_registry():
            return None
        if normalized not in _research.RESEARCH_PARAMETERS:
            return None
        return original(normalized, *args, **kwargs)

    signal_for_generic_strategy_only._research_registry_v2 = True  # type: ignore[attr-defined]
    _research.signal_for_strategy = signal_for_generic_strategy_only


def install_research_strategy_registry_patch() -> None:
    _canonicalize_existing_registry()
    _patch_sampling_active()
    _patch_signal_for_strategy()
    errors = validate_research_strategy_registry()
    if errors:
        raise RuntimeError("; ".join(errors))
    _sync_known_consumers()
