from __future__ import annotations

import threading
from functools import wraps
from typing import Any, Iterable


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD"

_ACTIVE_PAPER_CONTEXT = threading.local()


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _merge_unique(values: Iterable[str], additions: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), *tuple(additions))))


def _observer_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(context, dict):
        return None
    gates = context.get("m01o_observer_gates")
    direct = context.get("m01o_observer_gate")
    if not isinstance(gates, dict) and not isinstance(direct, dict):
        return None
    payload: dict[str, Any] = {}
    if isinstance(gates, dict):
        payload["m01o_observer_gates"] = gates
    if isinstance(direct, dict):
        payload["m01o_observer_gate"] = direct
    return payload


def _wrap_store_paper_context(store: Any) -> None:
    """Attach causal Observer gates to paper trades opened in this evaluation.

    R_MICROPRICE_CONFIRM is created inside the paper Store path. Before this
    patch its diagnostics omitted the realtime Observer gate, so the Guard
    could only classify the row as unavailable. The context is thread-local,
    market-bound, and exists only for the synchronous paper evaluation.
    """

    original_maybe_enter = getattr(store, "maybe_enter_m_series", None)
    if callable(original_maybe_enter) and not getattr(
        original_maybe_enter,
        "_observer_guard_paper_context_v1",
        False,
    ):

        @wraps(original_maybe_enter)
        def maybe_enter_with_observer_context(
            snapshot: dict[str, Any],
            fee_bps: int,
            *,
            realtime_context: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            previous = getattr(_ACTIVE_PAPER_CONTEXT, "value", None)
            try:
                market_id = int(snapshot["market_id"])
            except (KeyError, TypeError, ValueError):
                market_id = None
            _ACTIVE_PAPER_CONTEXT.value = {
                "market_id": market_id,
                "realtime_context": _observer_context(realtime_context),
            }
            try:
                return original_maybe_enter(
                    snapshot,
                    fee_bps,
                    realtime_context=realtime_context,
                )
            finally:
                if previous is None:
                    try:
                        delattr(_ACTIVE_PAPER_CONTEXT, "value")
                    except AttributeError:
                        pass
                else:
                    _ACTIVE_PAPER_CONTEXT.value = previous

        maybe_enter_with_observer_context._observer_guard_paper_context_v1 = True  # type: ignore[attr-defined]
        store.maybe_enter_m_series = maybe_enter_with_observer_context

    store_class = type(store)
    original_open_trade = getattr(store_class, "open_trade", None)
    if not callable(original_open_trade) or getattr(
        original_open_trade,
        "_observer_guard_paper_context_v1",
        False,
    ):
        return

    @wraps(original_open_trade)
    def open_trade_with_observer_context(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        active = getattr(_ACTIVE_PAPER_CONTEXT, "value", None)
        try:
            market_id = int(kwargs.get("market_id"))
        except (TypeError, ValueError):
            market_id = None
        active_market_id = (
            active.get("market_id")
            if isinstance(active, dict)
            else None
        )
        realtime_context = (
            active.get("realtime_context")
            if isinstance(active, dict)
            else None
        )
        if (
            market_id is not None
            and active_market_id == market_id
            and isinstance(realtime_context, dict)
        ):
            diagnostics = kwargs.get("diagnostics")
            enriched = dict(diagnostics) if isinstance(diagnostics, dict) else {}
            existing = enriched.get("realtime_context")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(realtime_context)
            enriched["realtime_context"] = merged
            kwargs = {**kwargs, "diagnostics": enriched}
        return original_open_trade(self, *args, **kwargs)

    open_trade_with_observer_context._observer_guard_paper_context_v1 = True  # type: ignore[attr-defined]
    store_class.open_trade = open_trade_with_observer_context


def _patch_engine_paper_context(realtime: Any) -> None:
    engine_class = realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_observer_guard_paper_context_v1", False):
        return

    @wraps(original_init)
    def init_with_observer_paper_context(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is not None:
            _wrap_store_paper_context(store)

    init_with_observer_paper_context._observer_guard_paper_context_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_observer_paper_context


def install_microprice_confirm_observer_guard_realtime_patch() -> None:
    """Expose the Guard generically and make Paper simulation count immediately.

    Every non-pair strategy already supported by live selection can use the
    existing per-slot Observer controls. Pair arbitrage remains excluded
    because it is a two-leg execution family rather than a directional signal.
    Slot 4 remains execution-only in the existing live rules model.
    """

    from . import live_trading as live
    from . import m_realtime as realtime

    generic_live_observer_strategies = tuple(
        strategy
        for strategy in live.LIVE_SUPPORTED_STRATEGIES
        if not str(strategy).startswith("PAIR_ARB_")
    )
    live.LIVE_OBSERVER_STRATEGIES = _merge_unique(
        live.LIVE_OBSERVER_STRATEGIES,
        generic_live_observer_strategies,
    )
    live.LIVE_OBSERVER_STRATEGIES = _append_unique(
        live.LIVE_OBSERVER_STRATEGIES,
        SOURCE_STRATEGY,
    )
    realtime.LIVE_OBSERVER_STRATEGIES = tuple(live.LIVE_OBSERVER_STRATEGIES)
    _patch_engine_paper_context(realtime)
