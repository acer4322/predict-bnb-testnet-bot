from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from . import decision_strategy_shadows as _decision


LOGGER = logging.getLogger(__name__)
PATCH_VERSION = "DECISION_SAFE_SOURCE_TRIGGER_V1"
SOURCE_STRATEGIES = frozenset(_decision.FAMILY_SOURCES.values())


def _contains_new_source_trade(opened: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(candidate, dict)
        and str(candidate.get("strategy") or "").upper() in SOURCE_STRATEGIES
        for candidate in opened
    )


def _base_maybe_enter(store: Any) -> Any:
    current = getattr(store, "maybe_enter_m_series", None)
    if not callable(current):
        return None
    # A process upgraded in place may already have the old every-event wrapper.
    # @wraps preserves the original bound method, so unwrap it before installing
    # the source-triggered safety wrapper.
    if getattr(store, "_decision_strategy_wrapped_v1", False):
        original = getattr(current, "__wrapped__", None)
        if callable(original):
            return original
    return current


def _safe_wrap_store(engine: Any, store: Any) -> None:
    """Attach Rank 1/2 without changing existing event-engine behavior.

    The original M-series method always runs first. Decision evaluation happens
    only when that call actually opened one of the three included source-family
    trades. Errors are isolated and the original candidates are returned intact.
    No dashboard method is wrapped here.
    """

    existing = getattr(store, "_decision_strategy_tracker_safe_v1", None)
    if isinstance(existing, _decision.DecisionStrategyTracker):
        existing.engine = engine
        engine.decision_strategy_tracker = existing
        _decision._ACTIVE_TRACKER = existing
        return

    original = _base_maybe_enter(store)
    if not callable(original):
        return

    tracker = _decision.DecisionStrategyTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_source_trigger(
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opened = list(
            original(
                snapshot,
                fee_bps,
                realtime_context=realtime_context,
            )
            or []
        )
        if not _contains_new_source_trade(opened):
            return opened
        try:
            decisions = tracker.process(
                snapshot,
                int(fee_bps),
                dict(realtime_context or {}),
            )
        except Exception:
            LOGGER.exception(
                "Decision Shadow evaluation failed after a source trade; "
                "returning original M-series candidates unchanged"
            )
            return opened
        return [*opened, *list(decisions or [])]

    maybe_enter_with_source_trigger._decision_safe_source_trigger_v1 = True  # type: ignore[attr-defined]
    store.maybe_enter_m_series = maybe_enter_with_source_trigger
    store._decision_strategy_wrapped_v1 = False
    store._decision_strategy_safe_source_trigger_v1 = True
    store._decision_strategy_tracker_safe_v1 = tracker
    store._decision_strategy_tracker_v1 = tracker
    engine.decision_strategy_tracker = tracker
    _decision._ACTIVE_TRACKER = tracker


def install_decision_strategy_safe_integration_patch() -> None:
    """Replace the old every-event integration before engine construction."""

    _decision._wrap_store = _safe_wrap_store
