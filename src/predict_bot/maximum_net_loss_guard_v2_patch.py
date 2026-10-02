from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Callable


GUARD_VERSION = "MAXIMUM_NET_LOSS_GUARD_V2_TIERED"
DEFAULT_REDUCTION_LOSS_USDT = Decimal("5.00")
DEFAULT_REDUCTION_MULTIPLIER_PCT = Decimal("50.00")
MINIMUM_LIMIT_USDT = Decimal("0.01")
MAXIMUM_LIMIT_USDT = Decimal("1000000.00")
MINIMUM_MULTIPLIER_PCT = Decimal("1.00")
MAXIMUM_MULTIPLIER_PCT = Decimal("100.00")
COMPARISON_EPSILON = Decimal("0.00000001")
CUSTOM_RULE_FIELDS = {
    "maximumNetLossReduceEnabled",
    "maximumNetLossReduceUsdt",
    "maximumNetLossReduceMultiplierPct",
}


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("maximumNetLossReduceEnabled must be boolean")


def _ensure_schema(ledger: Any) -> None:
    if getattr(ledger, "_maximum_net_loss_guard_v2_schema_ready", False):
        return
    with ledger.lock:
        columns = {
            str(row["name"])
            for row in ledger.db.execute(
                "PRAGMA table_info(live_maximum_net_loss_guard)"
            ).fetchall()
        }
        migrations = {
            "reduce_enabled": "INTEGER NOT NULL DEFAULT 0",
            "reduce_loss_usdt": "REAL NOT NULL DEFAULT 5.0",
            "reduce_multiplier_pct": "REAL NOT NULL DEFAULT 50.0",
            "reduction_tripped": "INTEGER NOT NULL DEFAULT 0",
            "reduction_tripped_at": "TEXT",
            "reduction_tripped_loss_usdt": "REAL",
        }
        for name, definition in migrations.items():
            if name not in columns:
                ledger.db.execute(
                    f"ALTER TABLE live_maximum_net_loss_guard ADD COLUMN {name} {definition}"
                )
        ledger.db.commit()
    ledger._maximum_net_loss_guard_v2_schema_ready = True


def _extended_state(
    ledger: Any,
    original_state: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    _ensure_schema(ledger)
    payload = dict(original_state(ledger))
    with ledger.lock:
        row = ledger.db.execute(
            """SELECT reduce_enabled, reduce_loss_usdt, reduce_multiplier_pct,
                      reduction_tripped, reduction_tripped_at,
                      reduction_tripped_loss_usdt
                 FROM live_maximum_net_loss_guard
                WHERE singleton=1"""
        ).fetchone()
    assert row is not None
    current_loss = Decimal(str(payload.get("currentLossUsdt") or 0))
    threshold = Decimal(str(row["reduce_loss_usdt"] or DEFAULT_REDUCTION_LOSS_USDT))
    multiplier_pct = Decimal(
        str(row["reduce_multiplier_pct"] or DEFAULT_REDUCTION_MULTIPLIER_PCT)
    )
    enabled = bool(row["reduce_enabled"])
    reduced = bool(row["reduction_tripped"])
    stopped = bool(payload.get("tripped"))
    payload.update(
        {
            "version": GUARD_VERSION,
            "reductionEnabled": enabled,
            "reductionThresholdUsdt": float(threshold),
            "reductionMultiplierPct": float(multiplier_pct),
            "reductionTripped": reduced,
            "reductionTrippedAt": row["reduction_tripped_at"],
            "reductionTrippedLossUsdt": row["reduction_tripped_loss_usdt"],
            "remainingBeforeReductionUsdt": float(
                Decimal("0") if reduced else max(Decimal("0"), threshold - current_loss)
            ),
            "reductionThresholdReached": current_loss + COMPARISON_EPSILON >= threshold,
            "effectiveStakeMultiplier": float(multiplier_pct / Decimal("100"))
            if reduced
            else 1.0,
            "phase": "STOPPED" if stopped else "REDUCED" if reduced else "NORMAL",
            "sharedLossCounter": True,
            "reductionLatchUntilReset": True,
            "reductionAppliesToNewOrdersOnly": True,
        }
    )
    if stopped:
        payload["status"] = "TRIPPED"
    elif reduced:
        payload["status"] = "REDUCED"
    elif enabled or bool(payload.get("enabled")):
        payload["status"] = "MONITORING"
    else:
        payload["status"] = "DISABLED"
    return payload


def _configure_reduction(
    ledger: Any,
    utc_iso: Callable[[], str],
    *,
    enabled: bool,
    threshold_usdt: Any,
    multiplier_pct: Any,
) -> None:
    _ensure_schema(ledger)
    threshold = _decimal(threshold_usdt)
    multiplier = _decimal(multiplier_pct)
    if threshold is None or not MINIMUM_LIMIT_USDT <= threshold <= MAXIMUM_LIMIT_USDT:
        raise ValueError(
            f"maximumNetLossReduceUsdt must be between {MINIMUM_LIMIT_USDT} and {MAXIMUM_LIMIT_USDT}"
        )
    if (
        multiplier is None
        or not MINIMUM_MULTIPLIER_PCT <= multiplier <= MAXIMUM_MULTIPLIER_PCT
    ):
        raise ValueError(
            "maximumNetLossReduceMultiplierPct must be between 1 and 100"
        )
    now = utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET reduce_enabled=?, reduce_loss_usdt=?, reduce_multiplier_pct=?,
                      reduction_tripped=CASE WHEN ? THEN reduction_tripped ELSE 0 END,
                      reduction_tripped_at=CASE WHEN ? THEN reduction_tripped_at ELSE NULL END,
                      reduction_tripped_loss_usdt=CASE WHEN ? THEN reduction_tripped_loss_usdt ELSE NULL END,
                      updated_at=?
                WHERE singleton=1""",
            (
                1 if enabled else 0,
                float(threshold),
                float(multiplier),
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                now,
            ),
        )
        ledger.db.commit()


def _mark_reduction_tripped(
    ledger: Any,
    utc_iso: Callable[[], str],
    current_loss_usdt: Decimal,
) -> bool:
    _ensure_schema(ledger)
    now = utc_iso()
    with ledger.lock:
        cursor = ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET reduction_tripped=1, reduction_tripped_at=?,
                      reduction_tripped_loss_usdt=?, updated_at=?
                WHERE singleton=1 AND reduce_enabled=1 AND reduction_tripped=0""",
            (now, float(current_loss_usdt), now),
        )
        ledger.db.commit()
    return cursor.rowcount == 1


def _clear_reduction_latch(ledger: Any, utc_iso: Callable[[], str]) -> None:
    _ensure_schema(ledger)
    now = utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET reduction_tripped=0, reduction_tripped_at=NULL,
                      reduction_tripped_loss_usdt=NULL, updated_at=?
                WHERE singleton=1""",
            (now,),
        )
        ledger.db.commit()


def install_maximum_net_loss_guard_v2_patch() -> None:
    """Extend the existing global live circuit breaker with a numeric reduce stage."""
    from . import live_trading as live

    ledger_class = live.LiveLedger
    engine_class = live.LiveM0WEngine

    original_ledger_state = ledger_class.maximum_net_loss_guard_state
    if not getattr(original_ledger_state, "_maximum_net_loss_guard_v2", False):
        @wraps(original_ledger_state)
        def state_v2(self: Any) -> dict[str, Any]:
            return _extended_state(self, original_ledger_state)

        state_v2._maximum_net_loss_guard_v2 = True  # type: ignore[attr-defined]
        ledger_class.maximum_net_loss_guard_state = state_v2

    original_reset = ledger_class.reset_maximum_net_loss_guard
    if not getattr(original_reset, "_maximum_net_loss_guard_v2", False):
        @wraps(original_reset)
        def reset_v2(self: Any) -> dict[str, Any]:
            result = original_reset(self)
            _clear_reduction_latch(self, live.utc_iso)
            return self.maximum_net_loss_guard_state()

        reset_v2._maximum_net_loss_guard_v2 = True  # type: ignore[attr-defined]
        ledger_class.reset_maximum_net_loss_guard = reset_v2

    original_plan = live.live_strategy_execution_plan
    if not getattr(original_plan, "_maximum_net_loss_guard_v2", False):
        @wraps(original_plan)
        def reduced_execution_plan(
            rules: dict[str, Any], strategy: str
        ) -> dict[str, Any]:
            plan = dict(original_plan(rules, strategy))
            multiplier = _decimal(
                getattr(live, "_maximum_net_loss_reduction_multiplier", 1.0)
            ) or Decimal("1")
            if multiplier >= Decimal("1"):
                return plan
            minimum = live.LIVE_MIN_CONFIGURABLE_STAKE_USDT
            mode = str(plan.get("mode") or live.LIVE_EXECUTION_MODE_FIXED)
            original_initial = Decimal(str(plan.get("initialStakeUsdt") or 0))
            original_add = Decimal(str(plan.get("addStakeUsdt") or 0))
            if mode == live.LIVE_EXECUTION_MODE_CONFIRMATION_ADD:
                initial = max(minimum, original_initial * multiplier)
                add_stake = max(minimum, original_add * multiplier)
                total = initial + add_stake * live.LIVE_CONFIRMATION_ADD_TRANCHES
            else:
                initial = max(minimum, original_initial * multiplier)
                add_stake = original_add
                total = initial
            plan["initialStakeUsdt"] = initial
            plan["addStakeUsdt"] = add_stake
            plan["totalCapUsdt"] = total
            plan["lossGuardReductionMultiplier"] = float(multiplier)
            return plan

        reduced_execution_plan._maximum_net_loss_guard_v2 = True  # type: ignore[attr-defined]
        live.live_strategy_execution_plan = reduced_execution_plan

    original_evaluate = engine_class.evaluate_maximum_net_loss_guard
    if not getattr(original_evaluate, "_maximum_net_loss_guard_v2", False):
        @wraps(original_evaluate)
        def evaluate_v2(
            self: Any,
            source: str = "state check",
        ) -> dict[str, Any]:
            original_evaluate(self, source)
            state = self.ledger.maximum_net_loss_guard_state()
            current_loss = Decimal(str(state.get("currentLossUsdt") or 0))
            threshold = Decimal(str(state.get("reductionThresholdUsdt") or 0))
            if (
                state.get("reductionEnabled")
                and not state.get("reductionTripped")
                and current_loss + COMPARISON_EPSILON >= threshold
            ):
                if _mark_reduction_tripped(self.ledger, live.utc_iso, current_loss):
                    self.ledger.record_event(
                        "WARN",
                        "MAXIMUM_NET_LOSS_REDUCTION_TRIPPED",
                        (
                            f"global live loss {current_loss:.8f} USDT reached reduction threshold "
                            f"{threshold:.8f} USDT; new-order stakes now use "
                            f"{state.get('reductionMultiplierPct', 100):.2f}% until reset"
                        ),
                    )
                state = self.ledger.maximum_net_loss_guard_state()
            multiplier = (
                Decimal(str(state.get("reductionMultiplierPct") or 100))
                / Decimal("100")
                if state.get("reductionTripped")
                else Decimal("1")
            )
            live._maximum_net_loss_reduction_multiplier = float(multiplier)
            return state

        evaluate_v2._maximum_net_loss_guard_v2 = True  # type: ignore[attr-defined]
        engine_class.evaluate_maximum_net_loss_guard = evaluate_v2

    original_update = engine_class.update_live_rules
    if not getattr(original_update, "_maximum_net_loss_guard_v2", False):
        @wraps(original_update)
        def update_v2(self: Any, values: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(values, dict):
                return original_update(self, values)
            custom = {key: values[key] for key in CUSTOM_RULE_FIELDS if key in values}
            remaining = {
                key: value for key, value in values.items()
                if key not in CUSTOM_RULE_FIELDS
            }

            current = self.ledger.maximum_net_loss_guard_state()
            reduce_enabled = (
                _boolean(custom["maximumNetLossReduceEnabled"])
                if "maximumNetLossReduceEnabled" in custom
                else bool(current.get("reductionEnabled"))
            )
            reduce_threshold = custom.get(
                "maximumNetLossReduceUsdt",
                current.get("reductionThresholdUsdt", float(DEFAULT_REDUCTION_LOSS_USDT)),
            )
            multiplier_pct = custom.get(
                "maximumNetLossReduceMultiplierPct",
                current.get(
                    "reductionMultiplierPct",
                    float(DEFAULT_REDUCTION_MULTIPLIER_PCT),
                ),
            )
            future_stop_enabled = (
                _boolean(remaining["maximumNetLossGuardEnabled"])
                if "maximumNetLossGuardEnabled" in remaining
                else bool(current.get("enabled"))
            )
            future_stop_threshold = _decimal(
                remaining.get("maximumNetLossUsdt", current.get("maximumLossUsdt"))
            )
            reduce_threshold_decimal = _decimal(reduce_threshold)
            multiplier_decimal = _decimal(multiplier_pct)
            if (
                reduce_threshold_decimal is None
                or not MINIMUM_LIMIT_USDT <= reduce_threshold_decimal <= MAXIMUM_LIMIT_USDT
            ):
                raise ValueError("maximumNetLossReduceUsdt must be between 0.01 and 1000000")
            if (
                multiplier_decimal is None
                or not MINIMUM_MULTIPLIER_PCT
                <= multiplier_decimal
                <= MAXIMUM_MULTIPLIER_PCT
            ):
                raise ValueError("maximumNetLossReduceMultiplierPct must be between 1 and 100")
            if (
                reduce_enabled
                and future_stop_enabled
                and future_stop_threshold is not None
                and reduce_threshold_decimal + COMPARISON_EPSILON
                >= future_stop_threshold
            ):
                raise ValueError(
                    "maximumNetLossReduceUsdt must be lower than maximumNetLossUsdt when both stages are enabled"
                )

            if remaining:
                original_update(self, remaining)
            if custom:
                _configure_reduction(
                    self.ledger,
                    live.utc_iso,
                    enabled=reduce_enabled,
                    threshold_usdt=reduce_threshold_decimal,
                    multiplier_pct=multiplier_decimal,
                )
            self.evaluate_maximum_net_loss_guard("tiered loss rule update")
            return self.state()

        update_v2._maximum_net_loss_guard_v2 = True  # type: ignore[attr-defined]
        engine_class.update_live_rules = update_v2

    live._maximum_net_loss_reduction_multiplier = 1.0
