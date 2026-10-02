from __future__ import annotations

from functools import wraps
from typing import Any

from . import live_trading as _live
from .decision_snapshot_diagnostics import (
    DECISION_SNAPSHOT_VERSION,
    install_decision_snapshot_diagnostics,
)

_ELIGIBLE_STATUSES = {
    "BLOCKED_DRAWDOWN_CONTROL",
    "BLOCKED_STRATEGY_OBSERVER",
    "BLOCKED_FUTURES_LEAD_OBSERVER",
}


def install_decision_snapshot_state() -> None:
    """Expose and self-heal the read-only decision snapshot diagnostics hooks."""
    engine_cls = _live.LiveM0WEngine
    original_state = engine_cls.state
    if getattr(original_state, "_decision_snapshot_state", False):
        return

    @wraps(original_state)
    def state_with_snapshot_status(
        self: Any,
        m0_hourly_performance: dict[str, Any] | None = None,
        *,
        include_ledger: bool = True,
    ) -> dict[str, Any]:
        # Some launch/import paths can load LiveM0WEngine before the package-level
        # installer runs, and later wrappers can also replace class methods.  The
        # monitoring endpoint is read-only, so use it as an idempotent repair
        # point before the ledger is serialized.  This never changes a gate,
        # requests a signed quote, or places an order.
        record_hook_before = bool(
            getattr(
                engine_cls._record_blocked_signal,
                "_decision_snapshot_diagnostics",
                False,
            )
        )
        orders_hook_before = bool(
            getattr(
                _live.LiveLedger.recent_orders,
                "_decision_snapshot_diagnostics",
                False,
            )
        )
        install_decision_snapshot_diagnostics()
        record_hook_after = bool(
            getattr(
                engine_cls._record_blocked_signal,
                "_decision_snapshot_diagnostics",
                False,
            )
        )
        orders_hook_after = bool(
            getattr(
                _live.LiveLedger.recent_orders,
                "_decision_snapshot_diagnostics",
                False,
            )
        )

        payload = original_state(
            self,
            m0_hourly_performance,
            include_ledger=include_ledger,
        )
        if not isinstance(payload, dict):
            return payload

        orders = payload.get("orders")
        visible_orders = orders if isinstance(orders, list) else []
        snapshot_count = sum(
            isinstance(item, dict)
            and isinstance(item.get("decision_snapshot"), dict)
            for item in visible_orders
        )
        eligible_count = sum(
            isinstance(item, dict)
            and str(item.get("status") or "").upper() in _ELIGIBLE_STATUSES
            for item in visible_orders
        )
        payload["decisionSnapshotDiagnostics"] = {
            "installed": record_hook_after and orders_hook_after,
            "version": DECISION_SNAPSHOT_VERSION,
            "recordHookInstalled": record_hook_after,
            "recentOrdersHookInstalled": orders_hook_after,
            "stateHookInstalled": True,
            "selfHealAttempted": True,
            "selfHealChangedRecordHook": (
                not record_hook_before and record_hook_after
            ),
            "selfHealChangedRecentOrdersHook": (
                not orders_hook_before and orders_hook_after
            ),
            "snapshotOrdersVisible": int(snapshot_count),
            "eligibleBlockedOrdersVisible": int(eligible_count),
            "capturesOnlyNewEvents": True,
            "eligibleStatuses": sorted(_ELIGIBLE_STATUSES),
            "packageModulePath": __file__,
            "liveTradingModulePath": getattr(_live, "__file__", None),
        }
        return payload

    state_with_snapshot_status._decision_snapshot_state = True  # type: ignore[attr-defined]
    engine_cls.state = state_with_snapshot_status
