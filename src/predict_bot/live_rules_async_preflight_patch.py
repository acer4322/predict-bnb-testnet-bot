from __future__ import annotations

import threading
from functools import wraps
from typing import Any, Callable


PATCH_VERSION = "LIVE_RULES_ASYNC_PREFLIGHT_V1"


def _schedule_preflight(
    engine: Any,
    preflight: Callable[[], None],
) -> None:
    """Run one coalesced preflight worker after a live-rules save.

    Rule persistence must not wait for Binance wallet, quota, and balance
    network calls. Multiple saves made while one preflight is running collapse
    into one additional pass so the newest rules are always checked.
    """

    coordination = getattr(engine, "_live_rules_preflight_coordination", None)
    if not isinstance(coordination, dict):
        coordination = {
            "lock": threading.Lock(),
            "pending": False,
            "worker": None,
        }
        setattr(engine, "_live_rules_preflight_coordination", coordination)

    lock = coordination["lock"]
    with lock:
        coordination["pending"] = True
        worker = coordination.get("worker")
        if isinstance(worker, threading.Thread) and worker.is_alive():
            return

        def run() -> None:
            while True:
                with lock:
                    coordination["pending"] = False
                try:
                    preflight()
                except Exception as exc:  # pragma: no cover - defensive boundary
                    engine_lock = getattr(engine, "lock", None)
                    if engine_lock is not None:
                        with engine_lock:
                            engine.status = "BLOCKED_PREFLIGHT"
                            engine.armed = False
                            engine.last_error = str(exc)[:400]
                    else:
                        engine.status = "BLOCKED_PREFLIGHT"
                        engine.armed = False
                        engine.last_error = str(exc)[:400]
                with lock:
                    if coordination["pending"]:
                        continue
                    coordination["worker"] = None
                    return

        worker = threading.Thread(
            target=run,
            name="live-rules-preflight",
            daemon=True,
        )
        coordination["worker"] = worker
        worker.start()


def _patch_engine(engine_class: type[Any]) -> None:
    original = getattr(engine_class, "update_live_rules", None)
    if not callable(original):
        return
    if getattr(original, "_live_rules_async_preflight_v1", False):
        return

    @wraps(original)
    def update_live_rules_without_blocking_preflight(
        self: Any,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        original_preflight = self._preflight
        requested = False

        def defer_preflight() -> None:
            nonlocal requested
            requested = True

        had_instance_override = "_preflight" in self.__dict__
        previous_override = self.__dict__.get("_preflight")
        self.__dict__["_preflight"] = defer_preflight
        try:
            response = original(self, values)
        finally:
            if had_instance_override:
                self.__dict__["_preflight"] = previous_override
            else:
                self.__dict__.pop("_preflight", None)

        if requested:
            _schedule_preflight(self, original_preflight)

        if isinstance(response, dict):
            response = dict(response)
            response["liveRulesSave"] = {
                "status": "SAVED",
                "preflightStatus": "QUEUED" if requested else "NOT_REQUIRED",
                "version": PATCH_VERSION,
            }
        return response

    update_live_rules_without_blocking_preflight._live_rules_async_preflight_v1 = True  # type: ignore[attr-defined]
    engine_class.update_live_rules = update_live_rules_without_blocking_preflight


def install_live_rules_async_preflight_patch() -> None:
    from . import live_trading as live

    _patch_engine(live.LiveM0WEngine)
