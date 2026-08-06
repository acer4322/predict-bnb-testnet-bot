from __future__ import annotations

import copy
import logging
import time
from functools import wraps
from typing import Any

from . import decision_strategy_shadows as _decision


LOGGER = logging.getLogger(__name__)
FAILURE_COOLDOWN_SECONDS = 15.0

_LAST_GOOD_SUMMARY: dict[str, Any] | None = None
_LAST_ERROR: str | None = None
_FAILURE_UNTIL = 0.0


def _fallback_summary(*, stale: bool) -> dict[str, Any]:
    if _LAST_GOOD_SUMMARY is not None:
        payload = copy.deepcopy(_LAST_GOOD_SUMMARY)
        payload["status"] = "STALE" if stale else "ACTIVE"
        payload["summaryStale"] = bool(stale)
        payload["summaryError"] = _LAST_ERROR if stale else None
        return payload
    return {
        "version": _decision.DECISION_VERSION,
        "status": "UNAVAILABLE",
        "summaryStale": True,
        "summaryError": _LAST_ERROR,
        "paperOnly": True,
        "forwardOnly": True,
        "nativeEventDriven": True,
        "liveSelectable": True,
        "includedFamilies": list(_decision.FAMILY_SOURCES),
        "excludedFamilies": list(_decision.EXCLUDED_FAMILIES),
        "rules": {},
        "strategies": {},
    }


def install_decision_strategy_dashboard_isolation_patch() -> None:
    """Prevent optional decision statistics from breaking ``/api/state``.

    The main dashboard already has a large historical-statistics payload.  The
    decision experiment is an optional extension and must never make that
    existing payload return HTTP 500.  Keep the last successful decision
    summary and fail closed only for the new decision tab.
    """

    original = _decision._decision_summary
    if getattr(original, "_decision_dashboard_isolation_v1", False):
        return

    @wraps(original)
    def isolated_summary(store: Any, tracker: Any) -> dict[str, Any]:
        global _LAST_GOOD_SUMMARY, _LAST_ERROR, _FAILURE_UNTIL

        now = time.monotonic()
        if now < _FAILURE_UNTIL:
            return _fallback_summary(stale=True)

        try:
            payload = original(store, tracker)
            if not isinstance(payload, dict):
                raise TypeError("decision summary must be a mapping")
            payload = dict(payload)
            payload["status"] = "ACTIVE"
            payload["summaryStale"] = False
            payload["summaryError"] = None
            _LAST_GOOD_SUMMARY = copy.deepcopy(payload)
            _LAST_ERROR = None
            _FAILURE_UNTIL = 0.0
            return payload
        except Exception as exc:  # isolation boundary: never fail /api/state
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
            _FAILURE_UNTIL = now + FAILURE_COOLDOWN_SECONDS
            LOGGER.exception(
                "Decision strategy summary failed; preserving the existing dashboard payload"
            )
            return _fallback_summary(stale=True)

    isolated_summary._decision_dashboard_isolation_v1 = True  # type: ignore[attr-defined]
    _decision._decision_summary = isolated_summary
