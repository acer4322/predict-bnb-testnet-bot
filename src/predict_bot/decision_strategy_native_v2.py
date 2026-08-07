from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from .decision_rank1_p50_80_shadow import install_rank1_p50_80_shadow
from .decision_rank1_snapshot_v2 import install_rank1_snapshot_v2
from .decision_strategy_config_v2 import initialize_controller_config
from .decision_strategy_rules import (
    SOURCE_STRATEGIES,
    STAKE_USDT,
    STRATEGIES,
)
from .decision_strategy_store import (
    DecisionStrategyTracker,
    wrap_dashboard,
)
from .research_strategy_registry_patch import register_shadow_strategy


LOGGER = logging.getLogger(__name__)
_ACTIVE_TRACKER: DecisionStrategyTracker | None = None


def _wrap_store(engine: Any, store: Any) -> None:
    global _ACTIVE_TRACKER
    install_rank1_snapshot_v2(store)
    install_rank1_p50_80_shadow(store)
    existing = getattr(store, "_decision_strategy_native_v2_tracker", None)
    if isinstance(existing, DecisionStrategyTracker):
        existing.engine = engine
        engine.decision_strategy_tracker = existing
        _ACTIVE_TRACKER = existing
        wrap_dashboard(type(store))
        return

    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    initialize_controller_config(store)
    tracker = DecisionStrategyTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_decision_strategy(
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
        has_new_source = any(
            isinstance(item, dict)
            and str(item.get("strategy") or "").upper() in SOURCE_STRATEGIES
            for item in opened
        )
        if not has_new_source:
            return opened
        try:
            derived = tracker.process(
                opened,
                snapshot,
                int(fee_bps),
                dict(realtime_context or {}),
            )
        except Exception:
            LOGGER.exception(
                "Decision controller evaluation failed; returning original "
                "Paper candidates unchanged"
            )
            return opened
        return [*opened, *derived]

    maybe_enter_with_decision_strategy._decision_strategy_native_v2 = True  # type: ignore[attr-defined]
    store.maybe_enter_m_series = maybe_enter_with_decision_strategy
    store._decision_strategy_native_v2_tracker = tracker
    engine.decision_strategy_tracker = tracker
    _ACTIVE_TRACKER = tracker
    wrap_dashboard(type(store))


def install_decision_strategy_native_v2() -> None:
    for strategy in STRATEGIES:
        register_shadow_strategy(
            strategy,
            parameters={
                "horizon": 300.0,
                "derived_only": 1.0,
                "native_event_driven": 1.0,
            },
            generic_signal=False,
        )

    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_decision_strategy_native_v2", False):
        return

    @wraps(original_init)
    def init_with_decision_strategy(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        _wrap_store(self, self.store)

    init_with_decision_strategy._decision_strategy_native_v2 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_decision_strategy
