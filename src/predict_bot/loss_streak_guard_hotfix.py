from __future__ import annotations

import threading
from functools import wraps
from typing import Any

from . import live_trading as live
from . import loss_streak_guard_patch as guard


_CACHE = threading.local()


def _install_cached_shadow_floor_lookup() -> None:
    original_lookup = guard._simulation_source_max_id
    if getattr(original_lookup, "_loss_streak_hotfix_v1", False):
        return

    @wraps(original_lookup)
    def cached_lookup(strategy: str) -> tuple[int | None, str | None]:
        cached = getattr(_CACHE, "shadow_floor", None)
        if isinstance(cached, dict) and cached.get("strategy") == guard._strategy(strategy):
            return cached.get("floor"), cached.get("error")
        return original_lookup(strategy)

    cached_lookup._loss_streak_hotfix_v1 = True  # type: ignore[attr-defined]
    guard._simulation_source_max_id = cached_lookup

    original_record = guard._record_live_result
    if getattr(original_record, "_loss_streak_hotfix_v1", False):
        return

    @wraps(original_record)
    def record_with_precomputed_shadow_floor(
        ledger: Any,
        *,
        order: dict[str, Any],
        settlement: dict[str, Any],
        utc_iso: Any,
    ) -> dict[str, Any]:
        strategy = guard._strategy(order.get("strategy"))
        result = str(settlement.get("result") or "").upper()
        previous = getattr(_CACHE, "shadow_floor", None)
        try:
            if strategy and result == "LOSS":
                floor, error = original_lookup(strategy)
                _CACHE.shadow_floor = {
                    "strategy": strategy,
                    "floor": floor,
                    "error": error,
                }
            return original_record(
                ledger,
                order=order,
                settlement=settlement,
                utc_iso=utc_iso,
            )
        finally:
            _CACHE.shadow_floor = previous

    record_with_precomputed_shadow_floor._loss_streak_hotfix_v1 = True  # type: ignore[attr-defined]
    guard._record_live_result = record_with_precomputed_shadow_floor


def _install_partial_live_rule_update() -> None:
    engine_class = live.LiveM0WEngine
    original_update = engine_class.update_live_rules
    if getattr(original_update, "_loss_streak_hotfix_v1", False):
        return

    @wraps(original_update)
    def update_with_custom_only_support(
        self: Any,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(values, dict):
            return original_update(self, values)
        custom_only = (
            guard.LOSS_STREAK_RULE_FIELD in values
            and not any(key != guard.LOSS_STREAK_RULE_FIELD for key in values)
        )
        if not custom_only:
            return original_update(self, values)

        with self.lock:
            current = dict(self.live_rules)
        updated = live.normalize_live_rules(
            {guard.LOSS_STREAK_RULE_FIELD: values[guard.LOSS_STREAK_RULE_FIELD]},
            current,
        )
        self.ledger.set_live_rules(updated)
        with self.lock:
            self.live_rules = dict(updated)
        self.ledger.record_event(
            "WARN",
            "LOSS_STREAK_GUARD_RULE_UPDATED",
            (
                "loss-streak guard per strategy updated to "
                f"{updated[guard.LOSS_STREAK_RULE_FIELD]}"
            ),
        )
        return self.state()

    update_with_custom_only_support._loss_streak_hotfix_v1 = True  # type: ignore[attr-defined]
    engine_class.update_live_rules = update_with_custom_only_support


def install_loss_streak_guard_hotfix() -> None:
    """Apply safety fixes after the base loss-streak patch is installed."""
    _install_cached_shadow_floor_lookup()
    _install_partial_live_rule_update()
