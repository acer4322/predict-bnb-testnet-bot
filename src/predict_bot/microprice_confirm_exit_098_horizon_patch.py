from __future__ import annotations

from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from .microprice_confirm_optimization_shadows import EXIT_098_STRATEGY


def _selected_or_live_position_exists(
    live_engine: Any,
    market_id: int,
) -> bool:
    rules = getattr(live_engine, "live_rules", None)
    if isinstance(rules, dict):
        selected = {
            str(value or "").strip().upper()
            for value in rules.get("strategies", [])
        }
        if EXIT_098_STRATEGY in selected:
            return True

    ledger = getattr(live_engine, "ledger", None)
    db = getattr(ledger, "db", None)
    lock = getattr(ledger, "lock", None)
    if db is None:
        return False
    try:
        query = """SELECT 1
                     FROM live_orders AS o
                     LEFT JOIN live_strategy_settlements AS s
                       ON s.order_local_id=o.id
                    WHERE o.strategy=? AND o.market_id=?
                      AND COALESCE(o.filled_usdt_amount, 0)>0
                      AND s.order_local_id IS NULL
                    LIMIT 1"""
        if lock is None:
            row = db.execute(
                query,
                (EXIT_098_STRATEGY, int(market_id)),
            ).fetchone()
        else:
            with lock:
                row = db.execute(
                    query,
                    (EXIT_098_STRATEGY, int(market_id)),
                ).fetchone()
    except Exception:
        return False
    return row is not None


def _paper_position_exists(engine: Any, market_id: int) -> bool:
    store = getattr(engine, "store", None)
    db = getattr(store, "db", None)
    lock = getattr(store, "lock", None)
    if db is None:
        return False
    try:
        query = """SELECT 1 FROM trades
                    WHERE strategy=? AND market_id=? AND status='OPEN'
                    LIMIT 1"""
        if lock is None:
            row = db.execute(
                query,
                (EXIT_098_STRATEGY, int(market_id)),
            ).fetchone()
        else:
            with lock:
                row = db.execute(
                    query,
                    (EXIT_098_STRATEGY, int(market_id)),
                ).fetchone()
    except Exception:
        return False
    return row is not None


def _monitor_required(engine: Any) -> bool:
    market_id = getattr(engine, "market_id", None)
    if market_id is None:
        try:
            market = engine.current_market() or {}
            market_id = int(market.get("market_id"))
        except (TypeError, ValueError):
            market_id = None
    if market_id is None:
        return False

    sink = getattr(engine, "live_signal_sink", None)
    live_engine = getattr(sink, "__self__", None)
    if live_engine is not None and _selected_or_live_position_exists(
        live_engine,
        int(market_id),
    ):
        return True
    return _paper_position_exists(engine, int(market_id))


def install_microprice_confirm_exit_098_horizon_patch() -> None:
    engine_class = _realtime.MSeriesRealtimeEngine
    original = engine_class._refresh_evaluation_horizon
    if getattr(original, "_microprice_exit_098_horizon_v1", False):
        return

    @wraps(original)
    def refresh_with_exit_098_monitor(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original(self, *args, **kwargs)
        if _monitor_required(self):
            # Entry occurs around 170–181 seconds remaining, but the 0.98 sell
            # target may trigger at any later point. Keep Prediction-book
            # evaluation alive for the full five-minute market only while this
            # strategy is selected or has an unsettled paper/live position.
            self.evaluation_horizon_seconds = 300.0

    refresh_with_exit_098_monitor._microprice_exit_098_horizon_v1 = True  # type: ignore[attr-defined]
    engine_class._refresh_evaluation_horizon = refresh_with_exit_098_monitor
