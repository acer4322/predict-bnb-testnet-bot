from __future__ import annotations

from functools import wraps
from typing import Any


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
LOSS_STREAK_TEST_STRATEGY = "R_MICROPRICE_CONFIRM_LOSS_STREAK_GUARD"
LOSS_STREAK_TEST_VERSION = "MICROPRICE_CONFIRM_LOSS_STREAK_GUARD_V1"
MODE_NORMAL = "NORMAL"
MODE_SHADOW = "SHADOW"
MODE_PROBATION = "PROBATION"
HALF_AFTER = 2
SHADOW_AFTER = 3
RECOVERY_SAMPLES = 3
PROBATION_WINS = 2

_RUNTIME: dict[str, Any] = {
    "sourceMarkets": 0,
    "opened": 0,
    "halfStakeOpened": 0,
    "shadowBlocked": 0,
    "recoveries": 0,
    "lastDecision": None,
}


def _utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(store: Any) -> None:
    if bool(getattr(store, "_read_only", False)):
        return
    with store.lock:
        store.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS microprice_confirm_loss_streak_state (
                strategy TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'NORMAL',
                consecutive_losses INTEGER NOT NULL DEFAULT 0,
                probation_remaining INTEGER NOT NULL DEFAULT 0,
                shadow_cycle INTEGER NOT NULL DEFAULT 0,
                latest_shadow_pnl_sum REAL,
                last_source_trade_id INTEGER,
                last_source_market_id INTEGER,
                last_result TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS microprice_confirm_loss_streak_decisions (
                source_trade_id INTEGER PRIMARY KEY,
                source_market_id INTEGER NOT NULL,
                source_side TEXT NOT NULL,
                source_entry_price REAL NOT NULL,
                mode_at_decision TEXT NOT NULL,
                consecutive_losses_at_decision INTEGER NOT NULL,
                stake_multiplier REAL NOT NULL,
                opened INTEGER NOT NULL,
                guard_trade_id INTEGER,
                guard_strategy_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS microprice_confirm_loss_streak_results (
                source_trade_id INTEGER PRIMARY KEY,
                source_market_id INTEGER NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('WIN', 'LOSS')),
                source_pnl REAL NOT NULL,
                decision_mode TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS microprice_confirm_loss_streak_shadow_samples (
                shadow_cycle INTEGER NOT NULL,
                source_trade_id INTEGER NOT NULL,
                source_market_id INTEGER NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('WIN', 'LOSS')),
                source_pnl REAL NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY(shadow_cycle, source_trade_id)
            );
            """
        )
        store.db.execute(
            """INSERT OR IGNORE INTO microprice_confirm_loss_streak_state(
                   strategy, updated_at
               ) VALUES (?, ?)""",
            (LOSS_STREAK_TEST_STRATEGY, _utc_iso()),
        )
        store.db.commit()


def _state_locked(store: Any) -> Any:
    return store.db.execute(
        """SELECT * FROM microprice_confirm_loss_streak_state
            WHERE strategy=?""",
        (LOSS_STREAK_TEST_STRATEGY,),
    ).fetchone()


def _enter_shadow_locked(store: Any, row: Any) -> None:
    store.db.execute(
        """UPDATE microprice_confirm_loss_streak_state
              SET mode='SHADOW', consecutive_losses=?, probation_remaining=0,
                  shadow_cycle=?, latest_shadow_pnl_sum=NULL, updated_at=?
            WHERE strategy=?""",
        (
            SHADOW_AFTER,
            int(row["shadow_cycle"] or 0) + 1,
            _utc_iso(),
            LOSS_STREAK_TEST_STRATEGY,
        ),
    )


def _apply_result_locked(
    store: Any,
    *,
    source_trade_id: int,
    source_market_id: int,
    result: str,
    source_pnl: float,
    decision_mode: str,
) -> None:
    inserted = store.db.execute(
        """INSERT OR IGNORE INTO microprice_confirm_loss_streak_results(
               source_trade_id, source_market_id, result, source_pnl,
               decision_mode, processed_at
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            source_trade_id,
            source_market_id,
            result,
            source_pnl,
            decision_mode,
            _utc_iso(),
        ),
    )
    if inserted.rowcount != 1:
        return
    row = _state_locked(store)
    mode = str(row["mode"] or MODE_NORMAL)
    losses = int(row["consecutive_losses"] or 0)
    probation = int(row["probation_remaining"] or 0)

    if decision_mode == MODE_SHADOW:
        store.db.execute(
            """INSERT OR IGNORE INTO microprice_confirm_loss_streak_shadow_samples(
                   shadow_cycle, source_trade_id, source_market_id,
                   result, source_pnl, processed_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                int(row["shadow_cycle"] or 0),
                source_trade_id,
                source_market_id,
                result,
                source_pnl,
                _utc_iso(),
            ),
        )
    elif decision_mode == MODE_PROBATION and mode == MODE_PROBATION:
        if result == "LOSS":
            _enter_shadow_locked(store, row)
        else:
            probation = max(0, probation - 1)
            store.db.execute(
                """UPDATE microprice_confirm_loss_streak_state
                      SET mode=?, consecutive_losses=0,
                          probation_remaining=?, last_source_trade_id=?,
                          last_source_market_id=?, last_result=?, updated_at=?
                    WHERE strategy=?""",
                (
                    MODE_NORMAL if probation == 0 else MODE_PROBATION,
                    probation,
                    source_trade_id,
                    source_market_id,
                    result,
                    _utc_iso(),
                    LOSS_STREAK_TEST_STRATEGY,
                ),
            )
    elif decision_mode == MODE_NORMAL and mode == MODE_NORMAL:
        losses = losses + 1 if result == "LOSS" else 0
        if losses >= SHADOW_AFTER:
            _enter_shadow_locked(store, row)
        else:
            store.db.execute(
                """UPDATE microprice_confirm_loss_streak_state
                      SET consecutive_losses=?, last_source_trade_id=?,
                          last_source_market_id=?, last_result=?, updated_at=?
                    WHERE strategy=?""",
                (
                    losses,
                    source_trade_id,
                    source_market_id,
                    result,
                    _utc_iso(),
                    LOSS_STREAK_TEST_STRATEGY,
                ),
            )
    else:
        store.db.execute(
            """UPDATE microprice_confirm_loss_streak_state
                  SET last_source_trade_id=?, last_source_market_id=?,
                      last_result=?, updated_at=? WHERE strategy=?""",
            (
                source_trade_id,
                source_market_id,
                result,
                _utc_iso(),
                LOSS_STREAK_TEST_STRATEGY,
            ),
        )


def _maybe_recover_locked(store: Any) -> None:
    row = _state_locked(store)
    if str(row["mode"] or "") != MODE_SHADOW:
        return
    cycle = int(row["shadow_cycle"] or 0)
    samples = store.db.execute(
        """SELECT source_pnl
             FROM microprice_confirm_loss_streak_shadow_samples
            WHERE shadow_cycle=? ORDER BY source_trade_id DESC LIMIT ?""",
        (cycle, RECOVERY_SAMPLES),
    ).fetchall()
    count_row = store.db.execute(
        """SELECT COUNT(*) AS count
             FROM microprice_confirm_loss_streak_shadow_samples
            WHERE shadow_cycle=?""",
        (cycle,),
    ).fetchone()
    values = [float(item["source_pnl"]) for item in samples]
    latest_sum = sum(values) if values else None
    recovered = (
        int(count_row["count"] or 0) >= RECOVERY_SAMPLES
        and len(values) == RECOVERY_SAMPLES
        and latest_sum is not None
        and latest_sum > 0
    )
    store.db.execute(
        """UPDATE microprice_confirm_loss_streak_state
              SET mode=?, consecutive_losses=?, probation_remaining=?,
                  latest_shadow_pnl_sum=?, updated_at=? WHERE strategy=?""",
        (
            MODE_PROBATION if recovered else MODE_SHADOW,
            0 if recovered else SHADOW_AFTER,
            PROBATION_WINS if recovered else 0,
            latest_sum,
            _utc_iso(),
            LOSS_STREAK_TEST_STRATEGY,
        ),
    )
    if recovered:
        _RUNTIME["recoveries"] += 1


def _sync_results(store: Any) -> None:
    _ensure_schema(store)
    if bool(getattr(store, "_read_only", False)):
        return
    with store.lock:
        rows = store.db.execute(
            """SELECT d.source_trade_id, d.source_market_id,
                      d.mode_at_decision, t.status, t.pnl
                 FROM microprice_confirm_loss_streak_decisions AS d
                 JOIN trades AS t ON t.id=d.source_trade_id
                 LEFT JOIN microprice_confirm_loss_streak_results AS r
                   ON r.source_trade_id=d.source_trade_id
                WHERE r.source_trade_id IS NULL AND t.pnl IS NOT NULL
                ORDER BY d.source_trade_id ASC"""
        ).fetchall()
        try:
            store.db.execute("BEGIN IMMEDIATE")
            for row in rows:
                pnl = float(row["pnl"])
                status = str(row["status"] or "").upper()
                _apply_result_locked(
                    store,
                    source_trade_id=int(row["source_trade_id"]),
                    source_market_id=int(row["source_market_id"]),
                    result="WIN" if status == "SETTLED_WIN" or pnl > 0 else "LOSS",
                    source_pnl=pnl,
                    decision_mode=str(row["mode_at_decision"]),
                )
            _maybe_recover_locked(store)
            store.db.commit()
        except Exception:
            store.db.rollback()
            raise


def _public_state(store: Any) -> dict[str, Any]:
    _ensure_schema(store)
    _sync_results(store)
    with store.lock:
        row = _state_locked(store)
        cycle = int(row["shadow_cycle"] or 0)
        count_row = store.db.execute(
            """SELECT COUNT(*) AS count
                 FROM microprice_confirm_loss_streak_shadow_samples
                WHERE shadow_cycle=?""",
            (cycle,),
        ).fetchone()
    return {
        "strategy": LOSS_STREAK_TEST_STRATEGY,
        "sourceStrategy": SOURCE_STRATEGY,
        "version": LOSS_STREAK_TEST_VERSION,
        "mode": str(row["mode"]),
        "consecutiveLosses": int(row["consecutive_losses"] or 0),
        "probationRemaining": int(row["probation_remaining"] or 0),
        "shadowCycle": cycle,
        "shadowSampleCount": int(count_row["count"] or 0),
        "latestShadowPnlSum": row["latest_shadow_pnl_sum"],
        "lastSourceTradeId": row["last_source_trade_id"],
        "lastSourceMarketId": row["last_source_market_id"],
        "lastResult": row["last_result"],
        "updatedAt": row["updated_at"],
        "rules": {
            "halfStakeAfterConsecutiveLosses": HALF_AFTER,
            "shadowAfterConsecutiveLosses": SHADOW_AFTER,
            "recoverySamples": RECOVERY_SAMPLES,
            "recoveryCondition": "latest three source paper PnL sum > 0",
            "probationHalfStakeWins": PROBATION_WINS,
            "probationLossReturnsToShadow": True,
        },
    }


def _latest_trade(store: Any, strategy: str, market_id: int) -> dict[str, Any] | None:
    row = store.db.execute(
        """SELECT * FROM trades WHERE strategy=? AND market_id=?
            ORDER BY id DESC LIMIT 1""",
        (strategy, int(market_id)),
    ).fetchone()
    return dict(row) if row is not None else None


def _wrap_open_trade(store_class: type[Any]) -> None:
    original = getattr(store_class, "open_trade", None)
    if not callable(original) or getattr(original, "_microprice_loss_streak_guard_v1", False):
        return

    @wraps(original)
    def open_trade_with_loss_streak_guard(self: Any, **kwargs: Any) -> None:
        original(self, **kwargs)
        if str(kwargs.get("strategy") or "").strip().upper() != SOURCE_STRATEGY:
            return
        _ensure_schema(self)
        _sync_results(self)
        market_id = int(kwargs["market_id"])
        source_trade = _latest_trade(self, SOURCE_STRATEGY, market_id)
        if source_trade is None:
            return
        with self.lock:
            existing = self.db.execute(
                """SELECT 1 FROM microprice_confirm_loss_streak_decisions
                    WHERE source_trade_id=?""",
                (int(source_trade["id"]),),
            ).fetchone()
        if existing is not None:
            return
        state = _public_state(self)
        mode = str(state["mode"])
        losses = int(state["consecutiveLosses"])
        multiplier = 0.5 if (
            mode == MODE_PROBATION
            or (mode == MODE_NORMAL and losses >= HALF_AFTER)
        ) else 1.0
        opened = False
        guard_trade_id = None
        if mode != MODE_SHADOW:
            guard_diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "forward_only": True,
                "guard_version": LOSS_STREAK_TEST_VERSION,
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": int(source_trade["id"]),
                "variant_mode": "FOLLOW_V2_WITH_LOSS_STREAK_GUARD",
                "loss_streak_mode": mode,
                "consecutive_losses": losses,
                "stake_multiplier": multiplier,
                "guard_rule": state["rules"],
                "source_diagnostics": kwargs.get("diagnostics") or {},
            }
            mirrored = dict(kwargs)
            mirrored.update(
                strategy=LOSS_STREAK_TEST_STRATEGY,
                target=None,
                stake=float(kwargs["stake"]) * multiplier,
                note=(
                    f"{LOSS_STREAK_TEST_STRATEGY} mirrored source trade "
                    f"#{source_trade['id']}; {mode} x{multiplier:.1f}; paper only"
                ),
                strategy_version=LOSS_STREAK_TEST_VERSION,
                diagnostics=guard_diagnostics,
            )
            original(self, **mirrored)
            guard_trade = _latest_trade(self, LOSS_STREAK_TEST_STRATEGY, market_id)
            if guard_trade is not None:
                opened = True
                guard_trade_id = int(guard_trade["id"])
                _RUNTIME["opened"] += 1
                if multiplier < 1:
                    _RUNTIME["halfStakeOpened"] += 1
        else:
            _RUNTIME["shadowBlocked"] += 1
        with self.lock:
            self.db.execute(
                """INSERT OR IGNORE INTO microprice_confirm_loss_streak_decisions(
                       source_trade_id, source_market_id, source_side,
                       source_entry_price, mode_at_decision,
                       consecutive_losses_at_decision, stake_multiplier,
                       opened, guard_trade_id, guard_strategy_version, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(source_trade["id"]),
                    market_id,
                    str(source_trade["side"]),
                    float(source_trade["entry_price"]),
                    mode,
                    losses,
                    multiplier if opened else 0.0,
                    1 if opened else 0,
                    guard_trade_id,
                    LOSS_STREAK_TEST_VERSION,
                    _utc_iso(),
                ),
            )
            self.db.commit()
        _RUNTIME["sourceMarkets"] += 1
        _RUNTIME["lastDecision"] = {
            "marketId": market_id,
            "sourceTradeId": int(source_trade["id"]),
            "status": "MIRRORED" if opened else "SHADOW_BLOCKED",
            "mode": mode,
            "consecutiveLosses": losses,
            "stakeMultiplier": multiplier if opened else 0.0,
        }

    open_trade_with_loss_streak_guard._microprice_loss_streak_guard_v1 = True  # type: ignore[attr-defined]
    store_class.open_trade = open_trade_with_loss_streak_guard


def _database_state(store: Any) -> dict[str, Any]:
    _ensure_schema(store)
    _sync_results(store)
    try:
        rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT id, strategy, market_id, side, status, entry_price,
                          exit_price, stake, fees, pnl, opened_at, closed_at
                     FROM trades WHERE strategy=? ORDER BY id ASC""",
                (LOSS_STREAK_TEST_STRATEGY,),
            ).fetchall()
        ]
    except Exception:
        rows = []
    terminal = [row for row in rows if row.get("pnl") is not None]
    wins = sum(float(row.get("pnl") or 0.0) > 0 for row in terminal)
    pnl = sum(float(row.get("pnl") or 0.0) for row in terminal)
    state = _public_state(store)
    entries = [float(row["entry_price"]) for row in rows]
    return {
        "version": LOSS_STREAK_TEST_VERSION,
        "sourceStrategy": SOURCE_STRATEGY,
        "sourceMarkets": int(_RUNTIME["sourceMarkets"]),
        "strategies": {
            LOSS_STREAK_TEST_STRATEGY: {
                "trades": len(rows),
                "open": len(rows) - len(terminal),
                "settled": len(terminal),
                "wins": wins,
                "losses": len(terminal) - wins,
                "winRate": wins / len(terminal) if terminal else None,
                "realizedPnl": pnl,
                "averageEntryPrice": sum(entries) / len(entries) if entries else None,
                "mode": state["mode"],
                "state": state,
                "parameters": state["rules"],
            }
        },
        "runtime": {
            **_RUNTIME,
            "state": state,
            "rules": {LOSS_STREAK_TEST_STRATEGY: state["rules"]},
        },
    }


def _inject_dashboard(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    experiment = _database_state(store)
    stats = experiment["strategies"][LOSS_STREAK_TEST_STRATEGY]
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceConfirmLossStreakGuard"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            strategies[LOSS_STREAK_TEST_STRATEGY] = {
                "enabled": True,
                "stakeUsdt": 5.0,
                "selectedBacktestParameters": stats["parameters"],
                "chronologicalValidation": {
                    "status": "ANALYZABLE" if int(stats["settled"]) >= 30 else "COLLECTING",
                    "samples": int(stats["trades"]),
                    "settled": int(stats["settled"]),
                    "wins": int(stats["wins"]),
                    "losses": int(stats["losses"]),
                    "realizedPnl": float(stats["realizedPnl"]),
                    "minimum": 30,
                    "forwardOnly": True,
                },
                "paperOnly": True,
                "liveOrdersAffected": False,
            }
    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        summaries[LOSS_STREAK_TEST_STRATEGY] = {
            "trades": int(stats["trades"]),
            "open": int(stats["open"]),
            "wins": int(stats["wins"]),
            "losses": int(stats["losses"]),
            "realized_pnl": float(stats["realizedPnl"]),
        }
    return payload


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(original, "_microprice_loss_streak_guard_v1", False):
        return

    @wraps(original)
    def dashboard_with_loss_streak_guard(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        return _inject_dashboard(payload, self) if isinstance(payload, dict) else payload

    dashboard_with_loss_streak_guard._microprice_loss_streak_guard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_loss_streak_guard


def install_microprice_confirm_loss_streak_guard() -> None:
    from . import m_realtime as realtime

    engine_class = realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_loss_streak_guard_v1", False):
        return

    @wraps(original_init)
    def init_with_loss_streak_guard(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is not None:
            _ensure_schema(store)
            _wrap_open_trade(type(store))
            _wrap_dashboard(type(store))

    init_with_loss_streak_guard._microprice_loss_streak_guard_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_loss_streak_guard
