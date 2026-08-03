from __future__ import annotations

from functools import wraps
from types import SimpleNamespace
from typing import Any

from . import m_realtime as _realtime
from .microprice_variants import (
    MICROPRICE_CONFIRM_STRATEGY,
    MICROPRICE_REVERSION_STRATEGY,
    MICROPRICE_STAKE_USDT,
    MicropriceVariantTracker,
)


_ACTIVE_TRACKER: MicropriceVariantTracker | None = None


def _database_state_for_store(
    store: Any,
    tracker: MicropriceVariantTracker,
) -> dict[str, Any]:
    """Read settlements from the Store instance serving this dashboard call.

    Production intentionally uses a separate DASHBOARD_STORE connection for
    /api/state.  The tracker belongs to the execution Store, so calling its
    normal database_state() used to read the wrong Store instance and the
    dashboard-only Store was never wrapped at all.  Reuse the tracker's runtime
    state while querying through the caller's SQLite connection.
    """
    view = SimpleNamespace(
        store=store,
        state=tracker.state,
    )
    return MicropriceVariantTracker.database_state(view)  # type: ignore[arg-type]


def _inject_experiment(
    payload: dict[str, Any],
    tracker: MicropriceVariantTracker,
    *,
    source_store: Any | None = None,
) -> dict[str, Any]:
    store = source_store if source_store is not None else tracker.store
    experiment = _database_state_for_store(store, tracker)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropricePairedExperiment"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            try:
                source_enabled = bool(
                    store.config().get(
                        "strategy_r_microprice_enabled",
                        True,
                    )
                )
            except Exception:
                source_enabled = True
            for strategy, mode in (
                (
                    MICROPRICE_CONFIRM_STRATEGY,
                    "FOLLOW_CONFIRMED_IMBALANCE",
                ),
                (
                    MICROPRICE_REVERSION_STRATEGY,
                    "REVERSE_CONFIRMED_IMBALANCE",
                ),
            ):
                stats = experiment["strategies"].get(strategy, {})
                strategies[strategy] = {
                    "enabled": source_enabled,
                    "stakeUsdt": MICROPRICE_STAKE_USDT,
                    "selectedBacktestParameters": {
                        **experiment["runtime"]["rules"],
                        "variantMode": mode,
                        "pairedOnly": True,
                    },
                    "chronologicalValidation": {
                        "status": (
                            "ANALYZABLE"
                            if int(stats.get("settled") or 0) >= 30
                            else "COLLECTING"
                        ),
                        "samples": int(stats.get("trades") or 0),
                        "settled": int(stats.get("settled") or 0),
                        "wins": int(stats.get("wins") or 0),
                        "losses": int(stats.get("losses") or 0),
                        "realizedPnl": float(
                            stats.get("realizedPnl") or 0.0
                        ),
                        "minimum": 30,
                        "pairedMarketOnly": True,
                    },
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                }

    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        for strategy, stats in experiment["strategies"].items():
            summaries[strategy] = {
                "trades": int(stats.get("trades") or 0),
                "open": int(stats.get("open") or 0),
                "wins": int(stats.get("wins") or 0),
                "losses": int(stats.get("losses") or 0),
                "realized_pnl": float(
                    stats.get("realizedPnl") or 0.0
                ),
            }
    return payload


def _wrap_dashboard(store: Any, tracker: MicropriceVariantTracker) -> None:
    """Wrap Store.dashboard at class scope so DASHBOARD_STORE is included."""
    global _ACTIVE_TRACKER
    _ACTIVE_TRACKER = tracker

    store_class = type(store)
    original = getattr(store_class, "dashboard", None)
    if not callable(original):
        return
    if getattr(original, "_microprice_dashboard_class_wrapped", False):
        return

    @wraps(original)
    def dashboard_with_microprice_variants(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        active_tracker = _ACTIVE_TRACKER
        if not isinstance(active_tracker, MicropriceVariantTracker):
            return payload
        return _inject_experiment(
            payload,
            active_tracker,
            source_store=self,
        )

    dashboard_with_microprice_variants._microprice_dashboard_class_wrapped = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_microprice_variants


def install_microprice_dashboard_patch() -> None:
    engine_cls = _realtime.MSeriesRealtimeEngine
    original_init = engine_cls.__init__
    if getattr(original_init, "_microprice_dashboard_patch", False):
        return

    @wraps(original_init)
    def init_with_microprice_dashboard(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        tracker = getattr(self, "microprice_variant_tracker", None)
        if isinstance(tracker, MicropriceVariantTracker):
            _wrap_dashboard(self.store, tracker)

    init_with_microprice_dashboard._microprice_dashboard_patch = True  # type: ignore[attr-defined]
    engine_cls.__init__ = init_with_microprice_dashboard
