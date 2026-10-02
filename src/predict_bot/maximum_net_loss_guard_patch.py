from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Callable


GUARD_VERSION = "MAXIMUM_NET_LOSS_GUARD_V1"
DEFAULT_MAXIMUM_LOSS_USDT = Decimal("10.00")
MINIMUM_LIMIT_USDT = Decimal("0.01")
MAXIMUM_LIMIT_USDT = Decimal("1000000.00")
COMPARISON_EPSILON = Decimal("0.00000001")
CUSTOM_RULE_FIELDS = {
    "maximumNetLossGuardEnabled",
    "maximumNetLossUsdt",
    "resetMaximumNetLoss",
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
    raise ValueError("maximumNetLossGuardEnabled must be boolean")


def _ensure_schema(ledger: Any, utc_iso: Callable[[], str]) -> None:
    if getattr(ledger, "_maximum_net_loss_guard_schema_ready", False):
        return
    now = utc_iso()
    with ledger.lock:
        ledger.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_maximum_net_loss_guard (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                enabled INTEGER NOT NULL DEFAULT 0,
                maximum_loss_usdt REAL NOT NULL DEFAULT 10.0,
                reset_at TEXT NOT NULL,
                tripped INTEGER NOT NULL DEFAULT 0,
                tripped_at TEXT,
                tripped_loss_usdt REAL,
                acknowledged_loss_usdt REAL NOT NULL DEFAULT 0,
                acknowledged_at TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        ledger.db.execute(
            """INSERT OR IGNORE INTO live_maximum_net_loss_guard(
                   singleton, enabled, maximum_loss_usdt, reset_at,
                   tripped, acknowledged_loss_usdt, updated_at
               ) VALUES (1, 0, ?, ?, 0, 0, ?)""",
            (float(DEFAULT_MAXIMUM_LOSS_USDT), now, now),
        )
        ledger.db.commit()
    ledger._maximum_net_loss_guard_schema_ready = True


def _state(ledger: Any, utc_iso: Callable[[], str]) -> dict[str, Any]:
    _ensure_schema(ledger, utc_iso)
    with ledger.lock:
        row = ledger.db.execute(
            "SELECT * FROM live_maximum_net_loss_guard WHERE singleton=1"
        ).fetchone()
        assert row is not None
        aggregate = ledger.db.execute(
            """SELECT COUNT(*) AS settled_count,
                      COALESCE(SUM(pnl_usdt), 0.0) AS net_pnl_usdt
                 FROM live_strategy_settlements
                WHERE updated_at > ?""",
            (str(row["reset_at"]),),
        ).fetchone()
    net_pnl = Decimal(str(aggregate["net_pnl_usdt"] or 0.0))
    current_loss = max(Decimal("0"), -net_pnl)
    maximum_loss = Decimal(str(row["maximum_loss_usdt"]))
    remaining = max(Decimal("0"), maximum_loss - current_loss)
    enabled = bool(row["enabled"])
    tripped = bool(row["tripped"])
    return {
        "version": GUARD_VERSION,
        "enabled": enabled,
        "status": "TRIPPED" if tripped else "MONITORING" if enabled else "DISABLED",
        "maximumLossUsdt": float(maximum_loss),
        "netPnlUsdt": float(net_pnl),
        "currentLossUsdt": float(current_loss),
        "remainingBeforePauseUsdt": float(remaining),
        "thresholdReached": current_loss + COMPARISON_EPSILON >= maximum_loss,
        "settledTrades": int(aggregate["settled_count"] or 0),
        "resetAt": row["reset_at"],
        "tripped": tripped,
        "trippedAt": row["tripped_at"],
        "trippedLossUsdt": row["tripped_loss_usdt"],
        "acknowledgedLossUsdt": float(row["acknowledged_loss_usdt"] or 0.0),
        "acknowledgedAt": row["acknowledged_at"],
        "updatedAt": row["updated_at"],
        "manualResumeRequired": tripped,
        "basis": "sum of official settled live pnl_usdt after the last manual reset",
        "unsettledTradesIncluded": False,
    }


def _configure(
    ledger: Any,
    utc_iso: Callable[[], str],
    *,
    enabled: bool,
    maximum_loss_usdt: Any,
) -> dict[str, Any]:
    _ensure_schema(ledger, utc_iso)
    maximum_loss = _decimal(maximum_loss_usdt)
    if maximum_loss is None or not MINIMUM_LIMIT_USDT <= maximum_loss <= MAXIMUM_LIMIT_USDT:
        raise ValueError(
            f"maximumNetLossUsdt must be between {MINIMUM_LIMIT_USDT} and "
            f"{MAXIMUM_LIMIT_USDT}"
        )
    now = utc_iso()
    with ledger.lock:
        row = ledger.db.execute(
            "SELECT enabled, reset_at FROM live_maximum_net_loss_guard WHERE singleton=1"
        ).fetchone()
        assert row is not None
        was_enabled = bool(row["enabled"])
        reset_at = now if enabled and not was_enabled else str(row["reset_at"])
        ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET enabled=?, maximum_loss_usdt=?, reset_at=?,
                      tripped=CASE WHEN ? THEN tripped ELSE 0 END,
                      tripped_at=CASE WHEN ? THEN tripped_at ELSE NULL END,
                      tripped_loss_usdt=CASE WHEN ? THEN tripped_loss_usdt ELSE NULL END,
                      acknowledged_loss_usdt=CASE WHEN ? THEN acknowledged_loss_usdt ELSE 0 END,
                      acknowledged_at=CASE WHEN ? THEN acknowledged_at ELSE NULL END,
                      updated_at=?
                WHERE singleton=1""",
            (
                1 if enabled else 0,
                float(maximum_loss),
                reset_at,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                now,
            ),
        )
        if enabled and not was_enabled:
            ledger.db.execute(
                """UPDATE live_maximum_net_loss_guard
                      SET tripped=0, tripped_at=NULL, tripped_loss_usdt=NULL,
                          acknowledged_loss_usdt=0, acknowledged_at=NULL,
                          updated_at=?
                    WHERE singleton=1""",
                (now,),
            )
        ledger.db.commit()
    return _state(ledger, utc_iso)


def _reset(ledger: Any, utc_iso: Callable[[], str]) -> dict[str, Any]:
    _ensure_schema(ledger, utc_iso)
    now = utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET reset_at=?, tripped=0, tripped_at=NULL,
                      tripped_loss_usdt=NULL, acknowledged_loss_usdt=0,
                      acknowledged_at=NULL, updated_at=?
                WHERE singleton=1""",
            (now, now),
        )
        ledger.db.commit()
    return _state(ledger, utc_iso)


def _acknowledge_manual_resume(
    ledger: Any, utc_iso: Callable[[], str]
) -> dict[str, Any]:
    state = _state(ledger, utc_iso)
    now = utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET tripped=0, tripped_at=NULL,
                      acknowledged_loss_usdt=?, acknowledged_at=?, updated_at=?
                WHERE singleton=1""",
            (float(state["currentLossUsdt"]), now, now),
        )
        ledger.db.commit()
    return _state(ledger, utc_iso)


def _clear_acknowledged_loss(
    ledger: Any, utc_iso: Callable[[], str]
) -> dict[str, Any]:
    now = utc_iso()
    with ledger.lock:
        ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET acknowledged_loss_usdt=0, acknowledged_at=NULL, updated_at=?
                WHERE singleton=1""",
            (now,),
        )
        ledger.db.commit()
    return _state(ledger, utc_iso)


def _mark_tripped(
    ledger: Any,
    utc_iso: Callable[[], str],
    current_loss_usdt: Decimal,
) -> bool:
    now = utc_iso()
    with ledger.lock:
        cursor = ledger.db.execute(
            """UPDATE live_maximum_net_loss_guard
                  SET tripped=1, tripped_at=?, tripped_loss_usdt=?, updated_at=?
                WHERE singleton=1 AND enabled=1 AND tripped=0""",
            (now, float(current_loss_usdt), now),
        )
        ledger.db.commit()
    return cursor.rowcount == 1


def _install_ledger(live: Any) -> None:
    ledger_class = live.LiveLedger
    original_init = ledger_class.__init__
    if not getattr(original_init, "_maximum_net_loss_guard_v1", False):
        @wraps(original_init)
        def init_with_maximum_net_loss_guard(
            self: Any, *args: Any, **kwargs: Any
        ) -> None:
            original_init(self, *args, **kwargs)
            _ensure_schema(self, live.utc_iso)

        init_with_maximum_net_loss_guard._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.__init__ = init_with_maximum_net_loss_guard

    ledger_class.maximum_net_loss_guard_state = (
        lambda self: _state(self, live.utc_iso)
    )
    ledger_class.configure_maximum_net_loss_guard = (
        lambda self, enabled, maximum_loss_usdt: _configure(
            self,
            live.utc_iso,
            enabled=bool(enabled),
            maximum_loss_usdt=maximum_loss_usdt,
        )
    )
    ledger_class.reset_maximum_net_loss_guard = (
        lambda self: _reset(self, live.utc_iso)
    )
    ledger_class.acknowledge_maximum_net_loss_resume = (
        lambda self: _acknowledge_manual_resume(self, live.utc_iso)
    )
    ledger_class.clear_maximum_net_loss_acknowledgement = (
        lambda self: _clear_acknowledged_loss(self, live.utc_iso)
    )
    ledger_class.mark_maximum_net_loss_tripped = (
        lambda self, current_loss_usdt: _mark_tripped(
            self,
            live.utc_iso,
            Decimal(str(current_loss_usdt)),
        )
    )

    def notify_engine(ledger: Any, source: str) -> None:
        callback = getattr(ledger, "_maximum_net_loss_guard_callback", None)
        if not callable(callback):
            return
        try:
            callback(source)
        except Exception as exc:
            try:
                ledger.record_event(
                    "ERROR",
                    "MAXIMUM_NET_LOSS_GUARD_EVALUATION_FAILED",
                    str(exc)[:400],
                )
            except Exception:
                pass

    original_settlement = ledger_class.record_strategy_settlement
    if not getattr(original_settlement, "_maximum_net_loss_guard_v1", False):
        @wraps(original_settlement)
        def settlement_with_maximum_net_loss_guard(
            self: Any,
            order: dict[str, Any],
            position: dict[str, Any],
        ) -> dict[str, Any] | None:
            try:
                settlement = original_settlement(self, order, position)
            except Exception:
                notify_engine(self, "official settlement recovery")
                raise
            if isinstance(settlement, dict):
                notify_engine(self, "official settlement")
            return settlement

        settlement_with_maximum_net_loss_guard._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.record_strategy_settlement = settlement_with_maximum_net_loss_guard

    original_manual_settlement = ledger_class.record_manual_exit_settlement
    if not getattr(original_manual_settlement, "_maximum_net_loss_guard_v1", False):
        @wraps(original_manual_settlement)
        def manual_settlement_with_maximum_net_loss_guard(
            self: Any,
            exit_id: int,
        ) -> dict[str, Any] | None:
            try:
                settlement = original_manual_settlement(self, exit_id)
            except Exception:
                notify_engine(self, "manual-exit settlement recovery")
                raise
            if isinstance(settlement, dict):
                notify_engine(self, "manual-exit settlement")
            return settlement

        manual_settlement_with_maximum_net_loss_guard._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.record_manual_exit_settlement = (
            manual_settlement_with_maximum_net_loss_guard
        )


def _install_engine(live: Any) -> None:
    engine_class = live.LiveM0WEngine
    original_set_runtime_enabled = engine_class.set_runtime_enabled

    def evaluate_maximum_net_loss_guard(
        self: Any,
        source: str = "state check",
    ) -> dict[str, Any]:
        state = self.ledger.maximum_net_loss_guard_state()
        if not state["enabled"]:
            return state

        current_loss = Decimal(str(state["currentLossUsdt"]))
        maximum_loss = Decimal(str(state["maximumLossUsdt"]))
        acknowledged_loss = Decimal(str(state["acknowledgedLossUsdt"]))

        if state["tripped"]:
            with self.lock:
                runtime_enabled = bool(self.runtime_enabled)
            if runtime_enabled:
                original_set_runtime_enabled(self, False)
                with self.lock:
                    self.status = "PAUSED_MAXIMUM_NET_LOSS"
                    self.armed = False
                    self.last_error = (
                        f"最大淨虧損保護已觸發：目前虧損 "
                        f"{current_loss:.8f} USDT，門檻 {maximum_loss:.8f} USDT"
                    )
            return self.ledger.maximum_net_loss_guard_state()

        if (
            acknowledged_loss > 0
            and current_loss + COMPARISON_EPSILON < maximum_loss
        ):
            state = self.ledger.clear_maximum_net_loss_acknowledgement()
            acknowledged_loss = Decimal("0")

        threshold_reached = (
            current_loss + COMPARISON_EPSILON >= maximum_loss
        )
        worsened_after_acknowledgement = (
            acknowledged_loss <= 0
            or current_loss > acknowledged_loss + COMPARISON_EPSILON
        )
        if not threshold_reached or not worsened_after_acknowledgement:
            return state
        if not self.ledger.mark_maximum_net_loss_tripped(current_loss):
            return self.ledger.maximum_net_loss_guard_state()

        with self.lock:
            runtime_enabled = bool(self.runtime_enabled)
        if runtime_enabled:
            original_set_runtime_enabled(self, False)
        message = (
            f"最大淨虧損保護觸發：重設後正式結算淨 PnL "
            f"{-current_loss:+.8f} USDT，目前虧損 {current_loss:.8f} USDT "
            f"已達門檻 {maximum_loss:.8f} USDT；實單已暫停，必須手動恢復"
        )
        with self.lock:
            self.status = "PAUSED_MAXIMUM_NET_LOSS"
            self.armed = False
            self.last_error = message[:400]
        self.ledger.record_event(
            "ERROR",
            "MAXIMUM_NET_LOSS_PAUSED",
            f"{message} (source={source})",
        )
        return self.ledger.maximum_net_loss_guard_state()

    engine_class.evaluate_maximum_net_loss_guard = evaluate_maximum_net_loss_guard

    original_init = engine_class.__init__
    if not getattr(original_init, "_maximum_net_loss_guard_v1", False):
        @wraps(original_init)
        def init_with_maximum_net_loss_guard(
            self: Any, *args: Any, **kwargs: Any
        ) -> None:
            original_init(self, *args, **kwargs)
            self.ledger._maximum_net_loss_guard_callback = (
                lambda source: self.evaluate_maximum_net_loss_guard(source)
            )
            self.evaluate_maximum_net_loss_guard("startup")

        init_with_maximum_net_loss_guard._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        engine_class.__init__ = init_with_maximum_net_loss_guard

    if not getattr(original_set_runtime_enabled, "_maximum_net_loss_guard_v1", False):
        @wraps(original_set_runtime_enabled)
        def runtime_with_maximum_net_loss_acknowledgement(
            self: Any,
            enabled: bool,
        ) -> dict[str, Any]:
            guard_state = self.ledger.maximum_net_loss_guard_state()
            acknowledged = bool(enabled and guard_state.get("tripped"))
            if acknowledged:
                acknowledged_state = (
                    self.ledger.acknowledge_maximum_net_loss_resume()
                )
                self.ledger.record_event(
                    "WARN",
                    "MAXIMUM_NET_LOSS_MANUAL_RESUME_ACKNOWLEDGED",
                    (
                        "operator manually resumed live trading after the maximum "
                        f"net-loss pause at {acknowledged_state['currentLossUsdt']:.8f} USDT"
                    ),
                )
            return original_set_runtime_enabled(self, enabled)

        runtime_with_maximum_net_loss_acknowledgement._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        engine_class.set_runtime_enabled = (
            runtime_with_maximum_net_loss_acknowledgement
        )

    original_update = engine_class.update_live_rules
    if not getattr(original_update, "_maximum_net_loss_guard_v1", False):
        @wraps(original_update)
        def update_with_maximum_net_loss_guard(
            self: Any,
            values: dict[str, Any],
        ) -> dict[str, Any]:
            if not isinstance(values, dict):
                return original_update(self, values)
            custom = {key: values[key] for key in CUSTOM_RULE_FIELDS if key in values}
            if not custom:
                return original_update(self, values)

            remaining = {
                key: value for key, value in values.items()
                if key not in CUSTOM_RULE_FIELDS
            }
            if remaining:
                original_update(self, remaining)

            current = self.ledger.maximum_net_loss_guard_state()
            enabled = (
                _boolean(custom["maximumNetLossGuardEnabled"])
                if "maximumNetLossGuardEnabled" in custom
                else bool(current["enabled"])
            )
            maximum_loss = custom.get(
                "maximumNetLossUsdt", current["maximumLossUsdt"]
            )
            if (
                "maximumNetLossGuardEnabled" in custom
                or "maximumNetLossUsdt" in custom
            ):
                self.ledger.configure_maximum_net_loss_guard(
                    enabled,
                    maximum_loss,
                )
            if _boolean(custom.get("resetMaximumNetLoss", False)):
                self.ledger.reset_maximum_net_loss_guard()
                self.ledger.record_event(
                    "WARN",
                    "MAXIMUM_NET_LOSS_COUNTER_RESET",
                    "operator manually reset the maximum net-loss counter to zero",
                )

            self.evaluate_maximum_net_loss_guard("rule update")
            return self.state()

        update_with_maximum_net_loss_guard._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        engine_class.update_live_rules = update_with_maximum_net_loss_guard

    original_state = engine_class.state
    if not getattr(original_state, "_maximum_net_loss_guard_v1", False):
        @wraps(original_state)
        def state_with_maximum_net_loss_guard(
            self: Any,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            payload = original_state(self, *args, **kwargs)
            guard_state = self.ledger.maximum_net_loss_guard_state()
            payload["maximumNetLossGuard"] = guard_state
            policy = payload.get("policy")
            if isinstance(policy, dict):
                policy["maximumNetLossGuard"] = {
                    "enabled": guard_state["enabled"],
                    "basis": guard_state["basis"],
                    "manualResetAvailable": True,
                    "manualResumeRequiredAfterTrip": True,
                    "winsOffsetLosses": True,
                    "unsettledTradesIncluded": False,
                }
            return payload

        state_with_maximum_net_loss_guard._maximum_net_loss_guard_v1 = True  # type: ignore[attr-defined]
        engine_class.state = state_with_maximum_net_loss_guard


def install_maximum_net_loss_guard_patch() -> None:
    """Install a persistent, resettable net-PnL circuit breaker for live trading."""
    from . import live_trading as live

    _install_ledger(live)
    _install_engine(live)
