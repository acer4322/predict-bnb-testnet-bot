from __future__ import annotations

import json
import os
import sqlite3
import threading
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Any, Callable


LOSS_STREAK_RULE_FIELD = "strategyLossStreakGuardEnabled"
LOSS_STREAK_GUARD_VERSION = "LOSS_STREAK_GUARD_V1"
LOSS_STREAK_MODE_NORMAL = "NORMAL"
LOSS_STREAK_MODE_SHADOW = "SHADOW"
LOSS_STREAK_MODE_PROBATION = "PROBATION"
LOSS_STREAK_HALF_AFTER = 2
LOSS_STREAK_SHADOW_AFTER = 3
LOSS_STREAK_RECOVERY_SAMPLES = 3
LOSS_STREAK_PROBATION_WINS = 2
LOSS_STREAK_HALF_MULTIPLIER = Decimal("0.5")

_EXECUTION_CONTEXT = threading.local()


def _strategy(value: Any) -> str:
    return str(value or "").split(":", 1)[0].strip().upper()


def _simulation_db_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("PREDICT_SIM_DB", root / "data" / "simulation.db"))


def _read_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return list(decoded) if isinstance(decoded, list) else [value]
    return []


def _bool(value: Any, parser: Callable[[Any], bool]) -> bool:
    try:
        return bool(parser(value))
    except (TypeError, ValueError):
        return False


def _simulation_source_max_id(strategy: str) -> tuple[int | None, str | None]:
    path = _simulation_db_path()
    if not path.exists():
        return None, f"simulation DB not found: {path}"
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        row = connection.execute(
            "SELECT MAX(id) FROM trades WHERE strategy=?",
            (_strategy(strategy),),
        ).fetchone()
        connection.close()
        return int(row[0] or 0), None
    except Exception as exc:
        return None, f"simulation DB lookup failed: {str(exc)[:180]}"


def _simulation_shadow_rows(
    strategy: str,
    *,
    source_id_floor: int,
    already_processed: set[int],
) -> tuple[list[dict[str, Any]], str | None]:
    path = _simulation_db_path()
    if not path.exists():
        return [], f"simulation DB not found: {path}"
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT id, market_id, status, pnl, closed_at
                     FROM trades
                    WHERE strategy=? AND id>? AND pnl IS NOT NULL
                    ORDER BY id ASC""",
                (_strategy(strategy), int(source_id_floor)),
            ).fetchall()
            if int(row["id"]) not in already_processed
        ]
        connection.close()
        return rows, None
    except Exception as exc:
        return [], f"simulation DB shadow lookup failed: {str(exc)[:180]}"


def _ensure_schema(ledger: Any) -> None:
    with ledger.lock:
        ledger.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_strategy_loss_streak_guard_state (
                strategy TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'NORMAL',
                consecutive_losses INTEGER NOT NULL DEFAULT 0,
                probation_remaining INTEGER NOT NULL DEFAULT 0,
                shadow_cycle INTEGER NOT NULL DEFAULT 0,
                shadow_source_trade_id_floor INTEGER,
                latest_shadow_pnl_sum REAL,
                last_live_order_local_id INTEGER,
                last_live_market_id INTEGER,
                last_live_result TEXT,
                last_shadow_source_trade_id INTEGER,
                last_shadow_market_id INTEGER,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_strategy_loss_streak_guard_results (
                order_local_id INTEGER PRIMARY KEY,
                strategy TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('WIN', 'LOSS')),
                pnl_usdt REAL NOT NULL,
                processed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS live_loss_streak_results_strategy_idx
                ON live_strategy_loss_streak_guard_results(strategy, order_local_id);
            CREATE TABLE IF NOT EXISTS live_strategy_loss_streak_shadow_samples (
                strategy TEXT NOT NULL,
                shadow_cycle INTEGER NOT NULL,
                source_trade_id INTEGER NOT NULL,
                market_id INTEGER NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('WIN', 'LOSS')),
                pnl_usdt REAL NOT NULL,
                closed_at TEXT,
                processed_at TEXT NOT NULL,
                PRIMARY KEY(strategy, shadow_cycle, source_trade_id)
            );
            CREATE INDEX IF NOT EXISTS live_loss_streak_shadow_strategy_idx
                ON live_strategy_loss_streak_shadow_samples(
                    strategy, shadow_cycle, source_trade_id
                );
            """
        )
        ledger.db.commit()


def _state_row_locked(ledger: Any, strategy: str, utc_iso: Callable[[], str]) -> Any:
    normalized = _strategy(strategy)
    ledger.db.execute(
        """INSERT OR IGNORE INTO live_strategy_loss_streak_guard_state(
               strategy, updated_at
           ) VALUES (?, ?)""",
        (normalized, utc_iso()),
    )
    return ledger.db.execute(
        """SELECT * FROM live_strategy_loss_streak_guard_state
            WHERE strategy=?""",
        (normalized,),
    ).fetchone()


def _public_state(ledger: Any, strategy: str, utc_iso: Callable[[], str]) -> dict[str, Any]:
    normalized = _strategy(strategy)
    _ensure_schema(ledger)
    with ledger.lock:
        row = _state_row_locked(ledger, normalized, utc_iso)
        cycle = int(row["shadow_cycle"] or 0)
        sample_rows = ledger.db.execute(
            """SELECT pnl_usdt FROM live_strategy_loss_streak_shadow_samples
                WHERE strategy=? AND shadow_cycle=?
                ORDER BY source_trade_id DESC LIMIT ?""",
            (normalized, cycle, LOSS_STREAK_RECOVERY_SAMPLES),
        ).fetchall()
        sample_count_row = ledger.db.execute(
            """SELECT COUNT(*) AS count
                 FROM live_strategy_loss_streak_shadow_samples
                WHERE strategy=? AND shadow_cycle=?""",
            (normalized, cycle),
        ).fetchone()
    latest = [float(item["pnl_usdt"]) for item in sample_rows]
    latest_sum = sum(latest) if latest else None
    return {
        "strategy": normalized,
        "version": LOSS_STREAK_GUARD_VERSION,
        "mode": str(row["mode"] or LOSS_STREAK_MODE_NORMAL),
        "consecutiveLosses": int(row["consecutive_losses"] or 0),
        "probationRemaining": int(row["probation_remaining"] or 0),
        "shadowCycle": cycle,
        "shadowSampleCount": int(sample_count_row["count"] or 0),
        "latestShadowPnl": latest,
        "latestShadowPnlSum": latest_sum,
        "shadowSourceTradeIdFloor": row["shadow_source_trade_id_floor"],
        "lastLiveOrderLocalId": row["last_live_order_local_id"],
        "lastLiveMarketId": row["last_live_market_id"],
        "lastLiveResult": row["last_live_result"],
        "lastShadowSourceTradeId": row["last_shadow_source_trade_id"],
        "lastShadowMarketId": row["last_shadow_market_id"],
        "lastError": row["last_error"],
        "updatedAt": row["updated_at"],
        "rules": {
            "halfStakeAfterConsecutiveLosses": LOSS_STREAK_HALF_AFTER,
            "shadowAfterConsecutiveLosses": LOSS_STREAK_SHADOW_AFTER,
            "recoveryShadowSamples": LOSS_STREAK_RECOVERY_SAMPLES,
            "recoveryLatestPnlMustBePositive": True,
            "probationHalfStakeWins": LOSS_STREAK_PROBATION_WINS,
            "probationLossReturnsToShadow": True,
            "confirmationAddsSuppressedWhileReducedRisk": True,
        },
    }


def _enter_shadow_locked(
    ledger: Any,
    *,
    strategy: str,
    row: Any,
    utc_iso: Callable[[], str],
    reason: str,
) -> None:
    floor, error = _simulation_source_max_id(strategy)
    next_cycle = int(row["shadow_cycle"] or 0) + 1
    ledger.db.execute(
        """UPDATE live_strategy_loss_streak_guard_state
              SET mode=?,
                  consecutive_losses=?,
                  probation_remaining=0,
                  shadow_cycle=?,
                  shadow_source_trade_id_floor=?,
                  latest_shadow_pnl_sum=NULL,
                  last_error=?,
                  updated_at=?
            WHERE strategy=?""",
        (
            LOSS_STREAK_MODE_SHADOW,
            LOSS_STREAK_SHADOW_AFTER,
            next_cycle,
            floor,
            error or reason,
            utc_iso(),
            _strategy(strategy),
        ),
    )


def _record_live_result(
    ledger: Any,
    *,
    order: dict[str, Any],
    settlement: dict[str, Any],
    utc_iso: Callable[[], str],
) -> dict[str, Any]:
    raw_strategy = str(order.get("strategy") or "")
    normalized = _strategy(raw_strategy)
    if (
        not normalized
        or ":CONFIRM_ADD_" in raw_strategy.upper()
        or normalized.startswith("PAIR_ARB_")
    ):
        return {}
    try:
        order_id = int(order["id"])
        market_id = int(order["market_id"])
        result = str(settlement["result"]).upper()
        pnl = float(settlement["pnl_usdt"])
    except (KeyError, TypeError, ValueError):
        return {}
    if result not in {"WIN", "LOSS"}:
        return {}

    _ensure_schema(ledger)
    with ledger.lock:
        try:
            ledger.db.execute("BEGIN IMMEDIATE")
            inserted = ledger.db.execute(
                """INSERT OR IGNORE INTO live_strategy_loss_streak_guard_results(
                       order_local_id, strategy, market_id, result,
                       pnl_usdt, processed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (order_id, normalized, market_id, result, pnl, utc_iso()),
            )
            if inserted.rowcount != 1:
                ledger.db.commit()
                return _public_state(ledger, normalized, utc_iso)
            row = _state_row_locked(ledger, normalized, utc_iso)
            mode = str(row["mode"] or LOSS_STREAK_MODE_NORMAL)
            losses = int(row["consecutive_losses"] or 0)
            probation = int(row["probation_remaining"] or 0)

            if mode == LOSS_STREAK_MODE_PROBATION:
                if result == "LOSS":
                    _enter_shadow_locked(
                        ledger,
                        strategy=normalized,
                        row=row,
                        utc_iso=utc_iso,
                        reason="probation loss returned strategy to SHADOW",
                    )
                else:
                    probation = max(0, probation - 1)
                    next_mode = (
                        LOSS_STREAK_MODE_NORMAL
                        if probation == 0
                        else LOSS_STREAK_MODE_PROBATION
                    )
                    ledger.db.execute(
                        """UPDATE live_strategy_loss_streak_guard_state
                              SET mode=?, consecutive_losses=0,
                                  probation_remaining=?,
                                  last_live_order_local_id=?,
                                  last_live_market_id=?, last_live_result=?,
                                  last_error=NULL, updated_at=?
                            WHERE strategy=?""",
                        (
                            next_mode,
                            probation,
                            order_id,
                            market_id,
                            result,
                            utc_iso(),
                            normalized,
                        ),
                    )
            elif mode == LOSS_STREAK_MODE_NORMAL:
                losses = losses + 1 if result == "LOSS" else 0
                if losses >= LOSS_STREAK_SHADOW_AFTER:
                    _enter_shadow_locked(
                        ledger,
                        strategy=normalized,
                        row=row,
                        utc_iso=utc_iso,
                        reason="three consecutive official live losses",
                    )
                    ledger.db.execute(
                        """UPDATE live_strategy_loss_streak_guard_state
                              SET last_live_order_local_id=?,
                                  last_live_market_id=?, last_live_result=?,
                                  updated_at=?
                            WHERE strategy=?""",
                        (order_id, market_id, result, utc_iso(), normalized),
                    )
                else:
                    ledger.db.execute(
                        """UPDATE live_strategy_loss_streak_guard_state
                              SET consecutive_losses=?,
                                  last_live_order_local_id=?,
                                  last_live_market_id=?, last_live_result=?,
                                  last_error=NULL, updated_at=?
                            WHERE strategy=?""",
                        (
                            losses,
                            order_id,
                            market_id,
                            result,
                            utc_iso(),
                            normalized,
                        ),
                    )
            else:
                ledger.db.execute(
                    """UPDATE live_strategy_loss_streak_guard_state
                          SET last_live_order_local_id=?,
                              last_live_market_id=?, last_live_result=?,
                              updated_at=?
                        WHERE strategy=?""",
                    (order_id, market_id, result, utc_iso(), normalized),
                )
            ledger.db.commit()
        except Exception:
            ledger.db.rollback()
            raise
    return _public_state(ledger, normalized, utc_iso)


def _sync_shadow(
    ledger: Any,
    strategy: str,
    utc_iso: Callable[[], str],
) -> dict[str, Any]:
    normalized = _strategy(strategy)
    _ensure_schema(ledger)
    with ledger.lock:
        row = _state_row_locked(ledger, normalized, utc_iso)
        if str(row["mode"] or "") != LOSS_STREAK_MODE_SHADOW:
            ledger.db.commit()
            return _public_state(ledger, normalized, utc_iso)
        cycle = int(row["shadow_cycle"] or 0)
        floor = row["shadow_source_trade_id_floor"]
        if floor is None:
            fresh_floor, error = _simulation_source_max_id(normalized)
            ledger.db.execute(
                """UPDATE live_strategy_loss_streak_guard_state
                      SET shadow_source_trade_id_floor=?,
                          last_error=?, updated_at=?
                    WHERE strategy=?""",
                (fresh_floor, error, utc_iso(), normalized),
            )
            ledger.db.commit()
            if fresh_floor is None:
                return _public_state(ledger, normalized, utc_iso)
            floor = fresh_floor
        processed_rows = ledger.db.execute(
            """SELECT source_trade_id
                 FROM live_strategy_loss_streak_shadow_samples
                WHERE strategy=? AND shadow_cycle=?""",
            (normalized, cycle),
        ).fetchall()
        processed = {int(item["source_trade_id"]) for item in processed_rows}

    rows, error = _simulation_shadow_rows(
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
                (normalized, cycle, LOSS_STREAK_RECOVERY_SAMPLES),
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
                sample_count >= LOSS_STREAK_RECOVERY_SAMPLES
                and len(latest_values) == LOSS_STREAK_RECOVERY_SAMPLES
                and latest_sum is not None
                and latest_sum > 0
            )
            ledger.db.execute(
                """UPDATE live_strategy_loss_streak_guard_state
                      SET mode=?,
                          consecutive_losses=?,
                          probation_remaining=?,
                          latest_shadow_pnl_sum=?,
                          last_error=?,
                          updated_at=?
                    WHERE strategy=?""",
                (
                    (
                        LOSS_STREAK_MODE_PROBATION
                        if recovered
                        else LOSS_STREAK_MODE_SHADOW
                    ),
                    0 if recovered else LOSS_STREAK_SHADOW_AFTER,
                    LOSS_STREAK_PROBATION_WINS if recovered else 0,
                    latest_sum,
                    error,
                    utc_iso(),
                    normalized,
                ),
            )
            ledger.db.commit()
        except Exception:
            ledger.db.rollback()
            raise
    return _public_state(ledger, normalized, utc_iso)


def _enabled(rules: dict[str, Any], strategy: str) -> bool:
    normalized = _strategy(strategy)
    if normalized.startswith("PAIR_ARB_"):
        return False
    try:
        strategies = [_strategy(value) for value in rules["strategies"]]
        index = strategies.index(normalized)
        return bool(rules.get(LOSS_STREAK_RULE_FIELD, [])[index])
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _install_normalizer(live: Any) -> None:
    original = live.normalize_live_rules
    if getattr(original, "_loss_streak_guard_v1", False):
        return

    @wraps(original)
    def normalize_with_loss_streak_guard(
        values: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = original(values, current)
        candidate = {**(current or {}), **(values or {})}
        raw = _read_list(candidate.get(LOSS_STREAK_RULE_FIELD))
        enabled = [
            _bool(value, live._boolean)
            for value in (raw + [False] * len(normalized["strategies"]))[
                : len(normalized["strategies"])
            ]
        ]
        normalized[LOSS_STREAK_RULE_FIELD] = enabled
        return normalized

    normalize_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
    live.normalize_live_rules = normalize_with_loss_streak_guard


def _install_ledger(live: Any) -> None:
    ledger_class = live.LiveLedger

    original_init = ledger_class.__init__
    if not getattr(original_init, "_loss_streak_guard_v1", False):
        @wraps(original_init)
        def init_with_loss_streak_guard(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            _ensure_schema(self)

        init_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.__init__ = init_with_loss_streak_guard

    original_overrides = ledger_class.live_rule_overrides
    if not getattr(original_overrides, "_loss_streak_guard_v1", False):
        @wraps(original_overrides)
        def overrides_with_loss_streak_guard(self: Any) -> dict[str, str]:
            result = dict(original_overrides(self))
            with self.lock:
                row = self.db.execute(
                    "SELECT value FROM live_settings WHERE key=?",
                    (LOSS_STREAK_RULE_FIELD,),
                ).fetchone()
            if row is not None:
                result[LOSS_STREAK_RULE_FIELD] = str(row["value"])
            return result

        overrides_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.live_rule_overrides = overrides_with_loss_streak_guard

    original_set = ledger_class.set_live_rules
    if not getattr(original_set, "_loss_streak_guard_v1", False):
        @wraps(original_set)
        def set_with_loss_streak_guard(self: Any, rules: dict[str, Any]) -> None:
            original_set(self, rules)
            value = rules.get(
                LOSS_STREAK_RULE_FIELD,
                [False] * len(rules.get("strategies", [])),
            )
            with self.lock:
                self.db.execute(
                    """INSERT INTO live_settings(key, value, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                           value=excluded.value, updated_at=excluded.updated_at""",
                    (
                        LOSS_STREAK_RULE_FIELD,
                        json.dumps(list(value)),
                        live.utc_iso(),
                    ),
                )
                self.db.commit()

        set_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.set_live_rules = set_with_loss_streak_guard

    original_settlement = ledger_class.record_strategy_settlement
    if not getattr(original_settlement, "_loss_streak_guard_v1", False):
        @wraps(original_settlement)
        def settlement_with_loss_streak_guard(
            self: Any,
            order: dict[str, Any],
            position: dict[str, Any],
        ) -> dict[str, Any] | None:
            settlement = original_settlement(self, order, position)
            if isinstance(settlement, dict):
                _record_live_result(
                    self,
                    order=order,
                    settlement=settlement,
                    utc_iso=live.utc_iso,
                )
            return settlement

        settlement_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        ledger_class.record_strategy_settlement = settlement_with_loss_streak_guard

    ledger_class.loss_streak_guard_state = (
        lambda self, strategy: _public_state(
            self, strategy, live.utc_iso
        )
    )
    ledger_class.sync_loss_streak_shadow = (
        lambda self, strategy: _sync_shadow(
            self, strategy, live.utc_iso
        )
    )


def _install_engine(live: Any) -> None:
    engine_class = live.LiveM0WEngine

    original_update = engine_class.update_live_rules
    if not getattr(original_update, "_loss_streak_guard_v1", False):
        @wraps(original_update)
        def update_with_loss_streak_guard(
            self: Any,
            values: dict[str, Any],
        ) -> dict[str, Any]:
            requested = (
                values.get(LOSS_STREAK_RULE_FIELD)
                if isinstance(values, dict)
                and LOSS_STREAK_RULE_FIELD in values
                else None
            )
            sanitized = dict(values)
            sanitized.pop(LOSS_STREAK_RULE_FIELD, None)
            original_update(self, sanitized)
            if requested is not None:
                with self.lock:
                    current = dict(self.live_rules)
                updated = live.normalize_live_rules(
                    {LOSS_STREAK_RULE_FIELD: requested},
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
                        f"{updated[LOSS_STREAK_RULE_FIELD]}"
                    ),
                )
            return self.state()

        update_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        engine_class.update_live_rules = update_with_loss_streak_guard

    original_state = engine_class.state
    if not getattr(original_state, "_loss_streak_guard_v1", False):
        @wraps(original_state)
        def state_with_loss_streak_guard(
            self: Any,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            payload = original_state(self, *args, **kwargs)
            rules = payload.get("rules") if isinstance(payload, dict) else None
            strategies = (
                list(rules.get("strategies", []))
                if isinstance(rules, dict)
                else []
            )
            payload["strategyLossStreakGuardStates"] = [
                {
                    **self.ledger.loss_streak_guard_state(strategy),
                    "enabled": _enabled(rules, strategy),
                }
                for strategy in strategies
            ]
            policy = payload.get("policy")
            if isinstance(policy, dict):
                policy["lossStreakGuard"] = {
                    "version": LOSS_STREAK_GUARD_VERSION,
                    "halfStakeAfterConsecutiveLosses": LOSS_STREAK_HALF_AFTER,
                    "shadowAfterConsecutiveLosses": LOSS_STREAK_SHADOW_AFTER,
                    "recoveryShadowSamples": LOSS_STREAK_RECOVERY_SAMPLES,
                    "recoveryCondition": "latest three paper PnL sum > 0",
                    "probationHalfStakeWins": LOSS_STREAK_PROBATION_WINS,
                    "probationLossReturnsToShadow": True,
                    "confirmationAddsSuppressedWhileReducedRisk": True,
                }
            return payload

        state_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        engine_class.state = state_with_loss_streak_guard

    original_plan = live.live_strategy_execution_plan
    if not getattr(original_plan, "_loss_streak_guard_v1", False):
        @wraps(original_plan)
        def plan_with_loss_streak_multiplier(
            rules: dict[str, Any],
            strategy: str,
        ) -> dict[str, Any]:
            plan = dict(original_plan(rules, strategy))
            context = getattr(_EXECUTION_CONTEXT, "value", None)
            if (
                isinstance(context, dict)
                and context.get("strategy") == _strategy(strategy)
                and context.get("multiplier") == LOSS_STREAK_HALF_MULTIPLIER
            ):
                initial = Decimal(str(plan["initialStakeUsdt"]))
                reduced = initial * LOSS_STREAK_HALF_MULTIPLIER
                plan["initialStakeUsdt"] = reduced
                if plan.get("mode") == live.LIVE_EXECUTION_MODE_FIXED:
                    plan["totalCapUsdt"] = reduced
            return plan

        plan_with_loss_streak_multiplier._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        live.live_strategy_execution_plan = plan_with_loss_streak_multiplier

    original_process = engine_class._process_single_signal
    if not getattr(original_process, "_loss_streak_guard_v1", False):
        @wraps(original_process)
        def process_with_loss_streak_guard(
            self: Any,
            signal: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            strategy = _strategy(signal.get("strategy"))
            with self.lock:
                rules = dict(self.live_rules)
                placement_ready = bool(self.runtime_enabled and self.armed)
            if (
                not placement_ready
                or not _enabled(rules, strategy)
                or strategy.startswith("PAIR_ARB_")
            ):
                return original_process(self, signal, *args, **kwargs)

            state = self.ledger.loss_streak_guard_state(strategy)
            if state["mode"] == LOSS_STREAK_MODE_SHADOW:
                state = self.ledger.sync_loss_streak_shadow(strategy)

            is_add = signal.get("_live_confirmation_add") is True
            reduced_risk = (
                state["mode"] == LOSS_STREAK_MODE_PROBATION
                or (
                    state["mode"] == LOSS_STREAK_MODE_NORMAL
                    and int(state["consecutiveLosses"]) >= LOSS_STREAK_HALF_AFTER
                )
            )
            if state["mode"] == LOSS_STREAK_MODE_SHADOW:
                self._record_blocked_signal(
                    signal,
                    "SKIPPED_LOSS_STREAK_SHADOW",
                    (
                        f"{strategy} is in SHADOW after three consecutive live losses; "
                        f"paper samples {state['shadowSampleCount']}, latest-three "
                        f"PnL {state['latestShadowPnlSum']}"
                    ),
                    diagnostics={
                        "lossStreakGuardVersion": LOSS_STREAK_GUARD_VERSION,
                        "lossStreakMode": state["mode"],
                        "shadowSampleCount": state["shadowSampleCount"],
                        "latestShadowPnlSum": state["latestShadowPnlSum"],
                    },
                )
                return None
            if is_add and reduced_risk:
                self._record_blocked_signal(
                    signal,
                    "SKIPPED_LOSS_STREAK_REDUCED_RISK_ADD",
                    (
                        f"{strategy} confirmation add suppressed while loss-streak "
                        f"guard is {state['mode']} with "
                        f"{state['consecutiveLosses']} consecutive losses"
                    ),
                )
                return None
            if not is_add and reduced_risk:
                base_plan = original_plan(rules, strategy)
                reduced = (
                    Decimal(str(base_plan["initialStakeUsdt"]))
                    * LOSS_STREAK_HALF_MULTIPLIER
                )
                if reduced < live.LIVE_MIN_CONFIGURABLE_STAKE_USDT:
                    self._record_blocked_signal(
                        signal,
                        "BLOCKED_LOSS_STREAK_HALF_STAKE_TOO_SMALL",
                        (
                            f"{strategy} half stake {reduced} is below live minimum "
                            f"{live.LIVE_MIN_CONFIGURABLE_STAKE_USDT}"
                        ),
                    )
                    return None
                _EXECUTION_CONTEXT.value = {
                    "strategy": strategy,
                    "multiplier": LOSS_STREAK_HALF_MULTIPLIER,
                }
                try:
                    self.ledger.record_event(
                        "WARN",
                        "LOSS_STREAK_HALF_STAKE",
                        (
                            f"{strategy} market {signal.get('market_id')} uses 50% "
                            f"initial stake in {state['mode']} mode"
                        ),
                        int(signal.get("market_id") or 0),
                    )
                    return original_process(self, signal, *args, **kwargs)
                finally:
                    _EXECUTION_CONTEXT.value = None
            return original_process(self, signal, *args, **kwargs)

        process_with_loss_streak_guard._loss_streak_guard_v1 = True  # type: ignore[attr-defined]
        engine_class._process_single_signal = process_with_loss_streak_guard


def install_loss_streak_guard_patch() -> None:
    """Install the persistent per-slot live loss-streak state machine."""
    from . import live_trading as live

    _install_normalizer(live)
    _install_ledger(live)
    _install_engine(live)
