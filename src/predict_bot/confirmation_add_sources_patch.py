from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Callable


CONFIRMATION_ADD_SOURCE_POLICY_VERSION = "ALL_LIVE_STRATEGIES_V3"


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


def _padded(raw: list[Any], length: int, fallback: Any) -> list[Any]:
    default = raw[0] if raw else fallback
    return (raw + [default] * length)[:length]


def _decimal_stake(value: Any, *, label: str, live: Any) -> Decimal:
    try:
        stake = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite decimal amount") from exc
    if not stake.is_finite() or not (
        live.LIVE_MIN_CONFIGURABLE_STAKE_USDT
        <= stake
        <= live.LIVE_MAX_CONFIGURABLE_STAKE_USDT
    ):
        raise ValueError(
            f"{label} must be between "
            f"{live.LIVE_MIN_CONFIGURABLE_STAKE_USDT} and "
            f"{live.LIVE_MAX_CONFIGURABLE_STAKE_USDT}"
        )
    live._stake_amount_wei(stake)
    return stake


def _requested_execution_plan(
    values: dict[str, Any],
    current: dict[str, Any] | None,
    *,
    live: Any,
) -> tuple[dict[str, Any], list[str], list[str], list[Decimal], list[Decimal], list[Decimal]]:
    candidate = {**(current or {}), **values}
    strategies = _list_value(candidate.get("strategies"))
    if not strategies and candidate.get("strategy"):
        strategies = [candidate["strategy"]]
    strategies = [
        str(value or "").strip().upper()
        for value in strategies
        if str(value or "").strip()
    ]
    if not strategies:
        strategies = [live.LIVE_DEFAULT_STRATEGY]

    raw_totals = _padded(
        _list_value(candidate.get("strategyStakesUsdt")),
        len(strategies),
        candidate.get("maxStakeUsdt", float(live.LIVE_DEFAULT_MAX_STAKE_USDT)),
    )
    raw_modes = _padded(
        _list_value(candidate.get("strategyExecutionModes")),
        len(strategies),
        live.LIVE_EXECUTION_MODE_FIXED,
    )
    raw_initials = _padded(
        _list_value(candidate.get("strategyInitialStakesUsdt")),
        len(strategies),
        raw_totals[0],
    )
    raw_adds = _padded(
        _list_value(candidate.get("strategyConfirmationAddStakesUsdt")),
        len(strategies),
        float(live.LIVE_DEFAULT_CONFIRMATION_ADD_STAKE_USDT),
    )

    modes = [
        str(value or live.LIVE_EXECUTION_MODE_FIXED).strip().upper()
        for value in raw_modes
    ]
    invalid_modes = sorted(set(modes) - set(live.LIVE_EXECUTION_MODES))
    if invalid_modes:
        raise ValueError(
            "each strategy execution mode must be one of: "
            + ", ".join(live.LIVE_EXECUTION_MODES)
        )

    initials: list[Decimal] = []
    adds: list[Decimal] = []
    totals: list[Decimal] = []
    for index, mode in enumerate(modes):
        initial_source = (
            raw_initials[index]
            if mode == live.LIVE_EXECUTION_MODE_CONFIRMATION_ADD
            else raw_totals[index]
        )
        initial = _decimal_stake(
            initial_source,
            label="each initial live stake",
            live=live,
        )
        add = _decimal_stake(
            raw_adds[index],
            label="each confirmation add stake",
            live=live,
        )
        total = (
            initial + add * live.LIVE_CONFIRMATION_ADD_TRANCHES
            if mode == live.LIVE_EXECUTION_MODE_CONFIRMATION_ADD
            else initial
        )
        if total > live.LIVE_MAX_CONFIGURABLE_STAKE_USDT:
            raise ValueError(
                "initial stake plus four confirmation adds may not exceed "
                f"{live.LIVE_MAX_CONFIGURABLE_STAKE_USDT} USDT"
            )
        live._stake_amount_wei(total)
        initials.append(initial)
        adds.append(add)
        totals.append(total)

    return candidate, strategies, modes, initials, adds, totals


def install_confirmation_add_sources_patch() -> None:
    from . import live_trading as live
    from . import research_forward as research

    all_live_sources = tuple(live.LIVE_SUPPORTED_STRATEGIES)
    research.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources
    live.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources

    original: Callable[..., dict[str, Any]] = live.normalize_live_rules
    if getattr(original, "_confirmation_add_all_live_v3", False):
        return

    @wraps(original)
    def normalize_with_all_live_confirmation_add(
        values: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        (
            candidate,
            strategies,
            modes,
            initials,
            adds,
            totals,
        ) = _requested_execution_plan(values, current, live=live)

        # The legacy normalizer contains the old source whitelist and explicit
        # pair/reverse exclusions. Run its remaining validation with FIXED
        # amounts, then restore the requested execution plans after all common
        # strategy, observer, drawdown, cooldown and reliability checks pass.
        sanitized = dict(candidate)
        sanitized["strategy"] = strategies[0]
        sanitized["strategies"] = strategies
        sanitized["strategyExecutionModes"] = [
            live.LIVE_EXECUTION_MODE_FIXED for _ in strategies
        ]
        sanitized["strategyStakesUsdt"] = [float(value) for value in initials]
        sanitized["strategyInitialStakesUsdt"] = [
            float(value) for value in initials
        ]
        sanitized["strategyConfirmationAddStakesUsdt"] = [
            float(value) for value in adds
        ]
        sanitized["maxStakeUsdt"] = float(initials[0])

        normalized = original(sanitized, None)
        normalized["strategyExecutionModes"] = modes
        normalized["strategyInitialStakesUsdt"] = [
            float(value) for value in initials
        ]
        normalized["strategyConfirmationAddStakesUsdt"] = [
            float(value) for value in adds
        ]
        normalized["strategyStakesUsdt"] = [float(value) for value in totals]
        normalized["maxStakeUsdt"] = float(totals[0])
        return normalized

    normalize_with_all_live_confirmation_add._confirmation_add_all_live_v3 = True  # type: ignore[attr-defined]
    live.normalize_live_rules = normalize_with_all_live_confirmation_add
