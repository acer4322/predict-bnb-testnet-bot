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


def _install_public_state_transaction_fix(guard: Any) -> None:
    """Do not leave a write transaction open after a dashboard state lookup."""
    original_public_state = guard._public_state
    if getattr(original_public_state, "_loss_streak_hotfix_v3", False):
        return

    @wraps(original_public_state)
    def public_state_with_closed_transaction(
        ledger: Any,
        strategy: str,
        utc_iso: Any,
    ) -> dict[str, Any]:
        try:
            result = original_public_state(ledger, strategy, utc_iso)
        except Exception:
            with ledger.lock:
                if ledger.db.in_transaction:
                    ledger.db.rollback()
            raise
        with ledger.lock:
            if ledger.db.in_transaction:
                ledger.db.commit()
        return result

    public_state_with_closed_transaction._loss_streak_hotfix_v3 = True  # type: ignore[attr-defined]
    guard._public_state = public_state_with_closed_transaction


def _install_cooldown_transaction_guard(live: Any) -> None:
    """Close only stale top-level transactions before the atomic cooldown consume."""
    ledger_class = live.LiveLedger
    original_consume = ledger_class.consume_loss_cooldown
    if getattr(original_consume, "_loss_streak_hotfix_v3", False):
        return

    @wraps(original_consume)
    def consume_without_stale_transaction(
        self: Any,
        strategy: str,
        market_id: int,
    ) -> tuple[bool, dict[str, Any]]:
        with self.lock:
            if self.db.in_transaction:
                self.db.commit()
        return original_consume(self, strategy, market_id)

    consume_without_stale_transaction._loss_streak_hotfix_v3 = True  # type: ignore[attr-defined]
    ledger_class.consume_loss_cooldown = consume_without_stale_transaction


def _install_settlement_reconciliation(live: Any, guard: Any) -> None:
    """Repair a settlement committed before the guard state update failed."""
    ledger_class = live.LiveLedger
    original_state_lookup = ledger_class.loss_streak_guard_state
    if getattr(original_state_lookup, "_loss_streak_hotfix_v4", False):
        return

    @wraps(original_state_lookup)
    def state_with_settlement_reconciliation(
        self: Any,
        strategy: str,
    ) -> dict[str, Any]:
        normalized = guard._strategy(strategy)
        if not normalized or normalized.startswith("PAIR_ARB_"):
            return original_state_lookup(self, strategy)

        reconciling = getattr(_CACHE, "settlement_reconciliation", set())
        if normalized in reconciling:
            return original_state_lookup(self, normalized)
        previous = set(reconciling)
        reconciling = set(reconciling)
        reconciling.add(normalized)
        _CACHE.settlement_reconciliation = reconciling
        try:
            current = original_state_lookup(self, normalized)
            last_processed_id = int(current.get("lastLiveOrderLocalId") or 0)
            with self.lock:
                rows = self.db.execute(
                    """SELECT o.id, o.strategy, o.market_id,
                              s.result, s.pnl_usdt
                         FROM live_orders AS o
                         JOIN live_strategy_settlements AS s
                           ON s.order_local_id=o.id
                         LEFT JOIN live_strategy_loss_streak_guard_results AS g
                           ON g.order_local_id=o.id
                        WHERE UPPER(o.strategy)=?
                          AND o.id>?
                          AND g.order_local_id IS NULL
                          AND UPPER(s.result) IN ('WIN','LOSS')
                        ORDER BY o.id ASC""",
                    (normalized, last_processed_id),
                ).fetchall()
                if self.db.in_transaction:
                    self.db.commit()

            repaired = 0
            for row in rows:
                guard._record_live_result(
                    self,
                    order={
                        "id": int(row["id"]),
                        "strategy": str(row["strategy"]),
                        "market_id": int(row["market_id"]),
                    },
                    settlement={
                        "result": str(row["result"]).upper(),
                        "pnl_usdt": float(row["pnl_usdt"]),
                    },
                    utc_iso=live.utc_iso,
                )
                repaired += 1

            if repaired:
                self.record_event(
                    "WARN",
                    "LOSS_STREAK_SETTLEMENT_RECONCILED",
                    (
                        f"{normalized} reconciled {repaired} official settlement(s) "
                        "that were committed before the loss-streak state update"
                    ),
                )
            return original_state_lookup(self, normalized)
        except Exception as exc:
            with self.lock:
                if self.db.in_transaction:
                    self.db.rollback()
                self.db.execute(
                    """UPDATE live_strategy_loss_streak_guard_state
                          SET last_error=?, updated_at=?
                        WHERE strategy=?""",
                    (
                        f"settlement reconciliation failed: {str(exc)[:180]}",
                        live.utc_iso(),
                        normalized,
                    ),
                )
                self.db.commit()
            return original_state_lookup(self, normalized)
        finally:
            _CACHE.settlement_reconciliation = previous

    state_with_settlement_reconciliation._loss_streak_hotfix_v4 = True  # type: ignore[attr-defined]
    ledger_class.loss_streak_guard_state = state_with_settlement_reconciliation

    original_manual_settlement = ledger_class.record_manual_exit_settlement
    if not getattr(original_manual_settlement, "_loss_streak_hotfix_v4", False):
        @wraps(original_manual_settlement)
        def manual_settlement_with_loss_streak_guard(
            self: Any,
            exit_id: int,
        ) -> dict[str, Any] | None:
            settlement = original_manual_settlement(self, exit_id)
            if isinstance(settlement, dict):
                try:
                    guard._record_live_result(
                        self,
                        order={
                            "id": int(settlement["order_local_id"]),
                            "strategy": str(settlement.get("strategy") or ""),
                            "market_id": int(settlement["market_id"]),
                        },
                        settlement=settlement,
                        utc_iso=live.utc_iso,
                    )
                except Exception as exc:
                    self.record_event(
                        "ERROR",
                        "LOSS_STREAK_MANUAL_SETTLEMENT_UPDATE_FAILED",
                        str(exc)[:400],
                        int(settlement.get("market_id") or 0) or None,
                    )
            return settlement

        manual_settlement_with_loss_streak_guard._loss_streak_hotfix_v4 = True  # type: ignore[attr-defined]
        ledger_class.record_manual_exit_settlement = (
            manual_settlement_with_loss_streak_guard
        )


def _install_mutually_exclusive_loss_protection(live: Any, guard: Any) -> None:
    """A strategy slot may use cooldown or Shadow guard, never both."""
    original_normalize = live.normalize_live_rules
    if getattr(original_normalize, "_loss_protection_exclusive_v1", False):
        return

    @wraps(original_normalize)
    def normalize_with_exclusive_loss_protection(
        values: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = original_normalize(values, current)
        strategies = list(normalized.get("strategies", []))
        count = len(strategies)
        cooldown = [
            bool(value)
            for value in (
                list(normalized.get("strategyLossCooldownEnabled", []))
                + [False] * count
            )[:count]
        ]
        shadow = [
            bool(value)
            for value in (
                list(normalized.get(guard.LOSS_STREAK_RULE_FIELD, []))
                + [False] * count
            )[:count]
        ]
        explicit_shadow = isinstance(values, dict) and guard.LOSS_STREAK_RULE_FIELD in values
        explicit_cooldown = isinstance(values, dict) and "strategyLossCooldownEnabled" in values

        for index in range(count):
            if explicit_shadow and not explicit_cooldown and shadow[index]:
                cooldown[index] = False
            elif explicit_cooldown and not explicit_shadow and cooldown[index]:
                shadow[index] = False
            elif shadow[index] and cooldown[index]:
                # Existing persisted conflicts are migrated to the more complete
                # Shadow state machine instead of leaving two blockers active.
                cooldown[index] = False

        normalized["strategyLossCooldownEnabled"] = cooldown
        normalized[guard.LOSS_STREAK_RULE_FIELD] = shadow
        return normalized

    normalize_with_exclusive_loss_protection._loss_protection_exclusive_v1 = True  # type: ignore[attr-defined]
    live.normalize_live_rules = normalize_with_exclusive_loss_protection


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
                f"{updated[guard.LOSS_STREAK_RULE_FIELD]}; "
                "two-loss cooldown normalized to "
                f"{updated['strategyLossCooldownEnabled']}"
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
    _install_public_state_transaction_fix(guard)
    _install_cooldown_transaction_guard(live)
    _install_settlement_reconciliation(live, guard)
    _install_mutually_exclusive_loss_protection(live, guard)
    _install_partial_live_rule_update(live, guard)
