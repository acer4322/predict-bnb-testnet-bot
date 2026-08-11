from __future__ import annotations

import os
from typing import Any

from . import microstructure as micro


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


MICRO_ENABLED = _enabled("PREDICT_MICRO_ENABLED", True)
_INSTALLED = False


def install_microstructure_lightweight_patch() -> None:
    """Allow POLY_LIVE profile to skip the heavyweight research observer.

    The production Binance market collector and dedicated Poly Live executor do
    not depend on the microstructure archival sockets.  When disabled we avoid
    opening/counting a potentially huge microstructure.db during startup and do
    not start its five websocket/writer workers.  The public state object remains
    available and reports DISABLED so dashboard callers do not crash.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    if MICRO_ENABLED:
        return

    original_initialize = micro.MicrostructureStore._initialize
    original_start = micro.MicrostructureObserver.start
    original_state = micro.MicrostructureObserver.state

    def lightweight_initialize(self: micro.MicrostructureStore) -> None:
        # Do not even open the large archive.  COUNT(*) on a very large SQLite
        # table was a significant startup cost and is unnecessary in POLY_LIVE.
        self.counts = {"events": 0, "snapshots": 0, "liquidity": 0, "gaps": 0}

    def lightweight_start(self: micro.MicrostructureObserver) -> None:
        self.writer_status = "DISABLED"
        self.writer_error = None
        with self.state_lock:
            for stats in self.stream_stats.values():
                stats.update(status="DISABLED", error=None)
        return None

    def lightweight_state(self: micro.MicrostructureObserver) -> dict[str, Any]:
        payload = original_state(self)
        payload["status"] = "DISABLED"
        payload["enabled"] = False
        storage = payload.setdefault("storage", {})
        storage.update(
            {
                "enabled": False,
                "archiveEnabled": False,
                "disabledBy": "PREDICT_MICRO_ENABLED",
                "note": "POLY_LIVE lightweight mode; existing database is left untouched",
            }
        )
        return payload

    # Keep originals attached for diagnostics/tests and future wrappers.
    micro.MicrostructureStore._full_initialize = original_initialize  # type: ignore[attr-defined]
    micro.MicrostructureObserver._full_start = original_start  # type: ignore[attr-defined]
    micro.MicrostructureStore._initialize = lightweight_initialize
    micro.MicrostructureObserver.start = lightweight_start
    micro.MicrostructureObserver.state = lightweight_state
