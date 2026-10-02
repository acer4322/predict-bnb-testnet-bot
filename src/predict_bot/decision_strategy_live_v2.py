from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any

from .decision_strategy_rules import (
    FAMILY_SOURCES,
    MAX_ENTRY,
    RANK1_STRATEGY,
    RANK2_STRATEGY,
    STRATEGIES,
    VERSION,
)


LIVE_POLICY_VERSION = "DECISION_STRATEGY_LIVE_V2"
RANK1_LOGIC_VERSION = "RANK1_SIGNAL_SNAPSHOT_V2"
REPRICE_GAP = Decimal("0.05")
MIN_ENTRY = Decimal("0.01")
MAX_ENTRY_DECIMAL = Decimal(str(MAX_ENTRY))


def _append_unique(values: Any, additions: tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(str(item) for item in values)
    for value in additions:
        if value not in result:
            result = (*result, value)
    return result


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _install_signal_provenance_guard(live: Any) -> None:
    engine_class = live.LiveM0WEngine
    original = engine_class._research_signal_price_is_allowed
    if getattr(original, "_decision_strategy_live_v2", False):
        return

    @wraps(original)
    def validate_decision_signal(
        signal: dict[str, Any],
        strategy: str,
    ) -> tuple[bool, str]:
        allowed, reason = original(signal, strategy)
        normalized = str(strategy or "").upper()
        if not allowed or normalized not in STRATEGIES:
            return allowed, reason
        if signal.get("decision_strategy_controller") is not True:
            return False, "decision controller signal provenance is missing"
        if str(signal.get("decision_strategy_version") or "") != VERSION:
            return False, "decision controller version does not match the frozen policy"
        if _positive_int(signal.get("decision_evaluation_id")) is None:
            return False, "decision controller durable evaluation id is missing"
        if _positive_int(signal.get("paper_trade_id")) is None:
            return False, "decision controller durable Paper trade id is missing"
        if normalized == RANK1_STRATEGY:
            rank1_logic = str(signal.get("rank1_logic_version") or "")
            if rank1_logic:
                if rank1_logic != RANK1_LOGIC_VERSION:
                    return False, "Rank 1 signal-snapshot logic version is invalid"
                if _positive_int(signal.get("trigger_source_trade_id")) is None:
                    return False, "Rank 1 durable trigger source trade id is missing"
                if _positive_int(signal.get("selected_source_signal_id")) is None:
                    return False, "Rank 1 durable source signal snapshot id is missing"
            elif _positive_int(signal.get("selected_source_trade_id")) is None:
                return False, "legacy Rank 1 selected source trade id is missing"
        elif _positive_int(signal.get("selected_source_trade_id")) is None:
            return False, "decision controller selected source trade id is missing"
        if str(signal.get("selected_family") or "") not in FAMILY_SOURCES:
            return False, "decision controller selected family is not permitted"
        if signal.get("market_data_integrity_ok") is not True:
            return False, "decision controller market-data integrity is not verified"
        if str(signal.get("trend_status") or "") != "PASS":
            return False, "decision controller strong-trend gate did not pass"
        raw_top_ask = _decimal(signal.get("raw_top_ask"))
        entry_price = _decimal(signal.get("entry_price"))
        if (
            raw_top_ask is None
            or entry_price is None
            or raw_top_ask <= 0
            or entry_price <= 0
            or entry_price > MAX_ENTRY_DECIMAL
            or raw_top_ask > MAX_ENTRY_DECIMAL
        ):
            return False, "decision controller entry price is outside the frozen range"
        estimated_probability = _decimal(signal.get("estimated_probability"))
        agreement_weight = _decimal(signal.get("agreement_weight"))
        if (
            estimated_probability is None
            or not Decimal("0") < estimated_probability < Decimal("1")
            or agreement_weight is None
            or not Decimal("0") < agreement_weight <= Decimal("1")
        ):
            return False, "decision controller probability or agreement is invalid"
        if normalized == RANK2_STRATEGY:
            model_edge = _decimal(signal.get("model_edge"))
            if model_edge is None or model_edge < Decimal("0.03"):
                return False, "Rank 2 requires at least 3 percentage points of final edge"
        if normalized == RANK1_STRATEGY and signal.get("model_edge") is None:
            return False, "Rank 1 model diagnostics are missing"
        return True, ""

    validate_decision_signal._decision_strategy_live_v2 = True  # type: ignore[attr-defined]
    engine_class._research_signal_price_is_allowed = staticmethod(
        validate_decision_signal
    )


def install_decision_strategy_live_v2() -> None:
    """Expose Rank 1/2 to the existing live executor without bypassing it."""
    from . import live_trading as live
    from . import m_realtime as realtime
    from .strategy_lifecycle_paper_fallback import (
        install_strategy_lifecycle_paper_fallback,
    )

    live.LIVE_RESEARCH_STRATEGIES = _append_unique(
        live.LIVE_RESEARCH_STRATEGIES,
        STRATEGIES,
    )
    live.LIVE_SUPPORTED_STRATEGIES = _append_unique(
        live.LIVE_SUPPORTED_STRATEGIES,
        STRATEGIES,
    )
    for strategy in STRATEGIES:
        live.LIVE_RESEARCH_REPRICE_GAPS[strategy] = REPRICE_GAP
        live.LIVE_RESEARCH_PRICE_RANGES[strategy] = (
            MIN_ENTRY,
            MAX_ENTRY_DECIMAL,
        )

    realtime.LIVE_RESEARCH_STRATEGIES = _append_unique(
        realtime.LIVE_RESEARCH_STRATEGIES,
        STRATEGIES,
    )
    realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES = set(
        realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    ) | set(STRATEGIES)

    _install_signal_provenance_guard(live)
    # Install after every live-whitelist patch above so the Lifecycle reader
    # sees the final runtime whitelist, including Rank 1/2.
    install_strategy_lifecycle_paper_fallback(live)
