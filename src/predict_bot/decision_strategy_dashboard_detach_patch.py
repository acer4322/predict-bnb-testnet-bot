from __future__ import annotations

import sys
from typing import Any

from . import decision_strategy_shadows as _decision


PATCH_VERSION = "DECISION_DASHBOARD_DETACH_V1"


def _do_not_wrap_dashboard(_store_class: type[Any]) -> None:
    """Keep the optional decision experiment out of the legacy dashboard path."""


def _restore_server_store_dashboard() -> None:
    """Undo the old wrapper when this patch is installed into a running process."""
    server = sys.modules.get("predict_bot.server")
    store_class = getattr(server, "Store", None) if server is not None else None
    if store_class is None:
        return
    current = getattr(store_class, "dashboard", None)
    if not callable(current):
        return
    if not getattr(current, "_decision_strategy_dashboard_v1", False):
        return
    original = getattr(current, "__wrapped__", None)
    if callable(original):
        store_class.dashboard = original


def install_decision_strategy_dashboard_detach_patch() -> None:
    """Remove Rank 1/2 statistics from ``/api/state`` completely.

    This project has a large, established historical dashboard query. Optional
    Paper/Shadow experiments must never add SQLite work, locks, or failure modes
    to that route. Decision-strategy state will be exposed separately; until
    then, the new tab may show unavailable data while all existing statistics
    continue to work normally.
    """

    _restore_server_store_dashboard()
    _decision._wrap_dashboard = _do_not_wrap_dashboard
