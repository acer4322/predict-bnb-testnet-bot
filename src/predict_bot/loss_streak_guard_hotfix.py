from __future__ import annotations

import threading
from functools import wraps
from typing import Any


_CACHE = threading.local()


def _install_cached_shadow_floor_lookup(guard: Any) -> None:
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


def _install_shadow_sync_transaction_fix(guard: Any) -> None:
    original_sync = guard._sync_shadow
    if getattr(original_sync, "_loss_streak_hotfix_v2", False):
        return

    @wraps(original_sync)
    def sync_shadow_without_nested_transaction(
        ledger: Any,
        strategy: str,
        utc_iso: Any,
    ) -> dict[str, Any]:
        normalized = guard._strategy(strategy)
        guard._ensure_schema(ledger)

        with ledger.lock:
            row = guard._state_row_locked(ledger, normalized, utc_iso)
            if str(row["mode"] or "") != guard.LOSS_STREAK_MODE_SHADOW:
                ledger.db.commit()
                return guard._public_state(ledger, normalized, utc_iso)
            cycle = int(row["shadow_cycle"] or 0)
            floor = row["shadow_source_trade_id_floor"]
            ledger.db.commit()

        if floor is None:
            fresh_floor, floor_error = guard._simulation_source_max_id(normalized)
            with ledger.lock:
                ledger.db.execute(
                    """UPDATE live_strategy_loss_streak_guard_state
                          SET shadow_source_trade_id_floor=?,
                              last_error=?, updated_at=?
                        WHERE strategy=?""",
                    (fresh_floor, floor_error, utc_iso(), normalized),
                )
                ledger.db.commit()
            if fresh_floor is None:
                return guard._public_state(ledger, normalized, utc_iso)
            floor = fresh_floor

        with ledger.lock:
            processed_rows = ledger.db.execute(
                """SELECT source_trade_id
                     FROM live_strategy_loss_streak_shadow_samples
                    WHERE strategy=? AND shadow_cycle=?""",
                (normalized, cycle),
            ).fetchall()
            processed = {int(item["source_trade_id"]) for item in processed_rows}
            ledger.db.commit()

        rows, shadow_error = guard._simulation_shadow_rows(
            normalized,
            source_id_floor=int(floor),
            already_processed=processed,
        )

        with ledger.lock:
            try:
                ledger.db.execute("BEGIN IMMEDIATE")
                for item in rows:
                    status = str(item.get("status") or "").upper()
                    pnl = float(item.get("pnl") or 0.0)
                    result = (
                        "WIN"
                        if status == "SETTLED_WIN" or pnl > 0
                        else "LOSS"
                    )
                    ledger.db.execute(
                        """INSERT OR IGNORE INTO
                           live_strategy_loss_streak_shadow_samples(
                               strategy, shadow_cycle, source_trade_id, market_id,
                               result, pnl_usdt, closed_at, processed_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            normalized,
                            cycle,
                            int(item["id"]),
                            int(item["market_id"]),
                            result,
                            pnl,
                            item.get("closed_at"),
                            utc_iso(),
                        ),
                    )
                    ledger.db.execute(
                        """UPDATE live_strategy_loss_streak_guard_state
                              SET last_shadow_source_trade_id=?,
                                  last_shadow_market_id=?, updated_at=?
                            WHERE strategy=?""",
                        (
                            int(item["id"]),
                            int(item["market_id"]),
                            utc_iso(),
                            normalized,
                        ),
                    )

                latest = ledger.db.execute(
                    """SELECT pnl_usdt
                         FROM live_strategy_loss_streak_shadow_samples
                        WHERE strategy=? AND shadow_cycle=?
                        ORDER BY source_trade_id DESC LIMIT ?""",
                    (
                        normalized,
                        cycle,
                        guard.LOSS_STREAK_RECOVERY_SAMPLES,
                    ),
                ).fetchall()
                latest_values = [float(item["pnl_usdt"]) for item in latest]
                latest_sum = sum(latest_values) if latest_values else None
                count_row = ledger.db.execute(
                    """SELECT COUNT(*) AS count
                         FROM live_strategy_loss_streak_shadow_samples
                        WHERE strategy=? AND shadow_cycle=?""",
                    (normalized, cycle),
                ).fetchone()
                sample_count = int(count_row["count"] or 0)
                recovered = (
                    sample_count >= guard.LOSS_STREAK_RECOVERY_SAMPLES
                    and len(latest_values) == guard.LOSS_STREAK_RECOVERY_SAMPLES
                    and latest_sum is not None
                    and latest_sum > 0
                )
                ledger.db.execute(
                    """UPDATE live_strategy_loss_streak_guard_state
                          SET mode=?, consecutive_losses=?,
                              probation_remaining=?, latest_shadow_pnl_sum=?,
                              last_error=?, updated_at=?
                        WHERE strategy=?""",
                    (
                        (
                            guard.LOSS_STREAK_MODE_PROBATION
                            if recovered
                            else guard.LOSS_STREAK_MODE_SHADOW
                        ),
                        0 if recovered else guard.LOSS_STREAK_SHADOW_AFTER,
                        (
                            guard.LOSS_STREAK_PROBATION_WINS
                            if recovered
                            else 0
                        ),
                        latest_sum,
                        shadow_error,
                        utc_iso(),
                        normalized,
                    ),
                )
                ledger.db.commit()
            except Exception:
                ledger.db.rollback()
                raise
        return guard._public_state(ledger, normalized, utc_iso)

    sync_shadow_without_nested_transaction._loss_streak_hotfix_v2 = True  # type: ignore[attr-defined]
    guard._sync_shadow = sync_shadow_without_nested_transaction


def _install_partial_live_rule_update(live: Any, guard: Any) -> None:
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
    from . import live_trading as live
    from . import loss_streak_guard_patch as guard

    _install_cached_shadow_floor_lookup(guard)
    _install_shadow_sync_transaction_fix(guard)
    _install_partial_live_rule_update(live, guard)
