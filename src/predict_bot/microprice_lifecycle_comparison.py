from __future__ import annotations

import os
from functools import wraps
from pathlib import Path
from typing import Any

from .microprice_lifecycle_comparison_store import (
    VERSION,
    MicropriceLifecycleComparisonTracker,
)


def _tracker(engine: Any, lifecycle: Any) -> MicropriceLifecycleComparisonTracker:
    tracker = getattr(engine, "_microprice_lifecycle_comparison_tracker", None)
    if isinstance(tracker, MicropriceLifecycleComparisonTracker):
        return tracker
    path = getattr(lifecycle, "path", None)
    if path is None:
        root = Path(__file__).resolve().parents[2]
        path = Path(os.environ.get("PREDICT_SIM_DB", root / "data" / "simulation.db"))
    tracker = MicropriceLifecycleComparisonTracker(path)
    engine._microprice_lifecycle_comparison_tracker = tracker
    return tracker


def _error(message: str | None = None) -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": "ERROR" if message else "WAITING_FOR_FIRST_FILL",
        "paperOnly": True,
        "liveOrdersAffected": False,
        "error": message,
    }


def install_microprice_lifecycle_comparison_sidecar(live: Any) -> None:
    """Attach a fail-open paper-only A/B observer to the existing snapshot sink."""
    engine = live.LiveM0WEngine
    original_record = engine.record_confirmation_add_snapshot
    if not getattr(original_record, "_microprice_lifecycle_comparison_v1", False):
        @wraps(original_record)
        def record(self: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
            result = original_record(self, snapshot)
            try:
                lifecycle = getattr(self, "_microprice_signal_lifecycle_tracker", None)
                if lifecycle is not None:
                    tracker = _tracker(self, lifecycle)
                    tracker.sync(snapshot)
                    self._microprice_lifecycle_comparison_error = None
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:500]
                self._microprice_lifecycle_comparison_error = message
                tracker = getattr(self, "_microprice_lifecycle_comparison_tracker", None)
                if tracker is not None:
                    tracker.last_error = message
                    tracker.counters["syncErrors"] = int(
                        tracker.counters.get("syncErrors") or 0
                    ) + 1
            return result

        record._microprice_lifecycle_comparison_v1 = True  # type: ignore[attr-defined]
        engine.record_confirmation_add_snapshot = record

    original_state = engine.state
    if not getattr(original_state, "_microprice_lifecycle_comparison_v1", False):
        @wraps(original_state)
        def state(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = original_state(self, *args, **kwargs)
            if kwargs.get("include_ledger", True) is False or not isinstance(payload, dict):
                return payload
            tracker = getattr(self, "_microprice_lifecycle_comparison_tracker", None)
            if tracker is None:
                comparison = _error(
                    getattr(self, "_microprice_lifecycle_comparison_error", None)
                )
            else:
                try:
                    comparison = tracker.state()
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"[:500]
                    self._microprice_lifecycle_comparison_error = message
                    tracker.last_error = message
                    comparison = _error(message)
            payload["micropriceSignalLifecycleComparison"] = comparison
            lifecycle = payload.get("micropriceSignalLifecycle")
            if isinstance(lifecycle, dict):
                lifecycle["comparison"] = comparison
            return payload

        state._microprice_lifecycle_comparison_v1 = True  # type: ignore[attr-defined]
        engine.state = state
