from __future__ import annotations

import os
import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import echtgeld_engine_v1 as v1
from .target_taker_live_execution_v5 import (
    TargetTakerLiveConfig,
    TargetTakerLiveExecutor,
)


VERSION = "ECHTGELD_ENGINE_V2_4310_BALANCE_PNL_STOP_LOSS"
HOST = str(os.environ.get("PREDICT_ECHTGELD_ENGINE_HOST") or "127.0.0.1").strip()
PORT = int(os.environ.get("PREDICT_ECHTGELD_ENGINE_PORT") or "8781")
ROOT = Path(__file__).resolve().parents[2]
SETTLEMENT_DB_PATH = Path(
    os.environ.get("PREDICT_ECHTGELD_SETTLEMENT_DB")
    or os.environ.get("PREDICT_WALLET_SHADOW_DB")
    or ROOT / "data" / "predict_wallet_shadow.db"
)
SETTLEMENT_SYNC_INTERVAL_MS = 5_000
RISK_MONITOR_INTERVAL_SECONDS = 2.0
MAX_STOP_LOSS_USDT = 1_000_000.0
RISK_BASIS = "SETTLED_NET_PNL_USDT"


class EchtgeldEngine(v1.EchtgeldEngine):
    """V1 execution engine plus balance, settled PnL, and durable PnL stop loss.

    The stop loss is an engine-level guard, not a Dashboard-only feature. A
    positive stopLossUsdt pauses new Echtgeld submissions when cumulative
    settled strategy net PnL is <= -stopLossUsdt. Zero disables the guard.
    """

    def __init__(
        self,
        db_path: Path | str = v1.DB_PATH,
        *,
        executor_factory: Callable[[TargetTakerLiveConfig], TargetTakerLiveExecutor] = TargetTakerLiveExecutor,
        start_worker: bool = True,
        settlement_db_path: Path | str = SETTLEMENT_DB_PATH,
    ) -> None:
        self.settlement_db_path = Path(settlement_db_path)
        self.settlement_sync_last_ms = 0
        self.settlement_sync_state: dict[str, Any] = {
            "status": "NOT_SYNCED",
            "source": str(self.settlement_db_path),
            "asOfMs": None,
            "rows": 0,
            "error": None,
        }
        self.risk_thread: threading.Thread | None = None
        self.stop_loss_usdt = 0.0
        self.stop_loss_last_triggered_at_ms: int | None = None
        self.stop_loss_last_triggered_net_pnl_usdt: float | None = None
        self.risk_last_check_at_ms: int | None = None
        self.risk_last_error: str | None = None
        super().__init__(
            db_path,
            executor_factory=executor_factory,
            start_worker=start_worker,
        )
        self._load_risk_control()
        if start_worker:
            self.risk_thread = threading.Thread(
                target=self._risk_loop,
                name="echtgeld-stop-loss-monitor",
                daemon=True,
            )
            self.risk_thread.start()

    def _setup_schema(self) -> None:
        super()._setup_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_settlements (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    synced_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_engine_settlements_time
                    ON engine_settlements(resolved_at_ms DESC);
                CREATE TABLE IF NOT EXISTS engine_risk_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    stop_loss_usdt REAL NOT NULL DEFAULT 0,
                    last_triggered_at_ms INTEGER,
                    last_triggered_net_pnl_usdt REAL,
                    updated_at_ms INTEGER NOT NULL
                );
                """
            )
            self.db.execute(
                """INSERT OR IGNORE INTO engine_risk_control(
                       id,stop_loss_usdt,last_triggered_at_ms,
                       last_triggered_net_pnl_usdt,updated_at_ms
                   ) VALUES (1,0,NULL,NULL,?)""",
                (v1._now_ms(),),
            )
            self.db.commit()

    def _load_risk_control(self) -> None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM engine_risk_control WHERE id=1"
            ).fetchone()
        if row is None:
            return
        self.stop_loss_usdt = max(0.0, float(row["stop_loss_usdt"] or 0.0))
        self.stop_loss_last_triggered_at_ms = (
            int(row["last_triggered_at_ms"])
            if row["last_triggered_at_ms"] is not None
            else None
        )
        self.stop_loss_last_triggered_net_pnl_usdt = (
            float(row["last_triggered_net_pnl_usdt"])
            if row["last_triggered_net_pnl_usdt"] is not None
            else None
        )

    @staticmethod
    def _validated_stop_loss(value: Any) -> float:
        parsed = v1._finite(value)
        if parsed is None or not 0 <= parsed <= MAX_STOP_LOSS_USDT:
            raise v1.EchtgeldEngineError(
                f"stopLossUsdt must be within [0, {MAX_STOP_LOSS_USDT:g}]; 0 disables the stop loss"
            )
        return float(parsed)

    def _persist_risk_control(self) -> None:
        with self.db_lock:
            self.db.execute(
                """INSERT INTO engine_risk_control(
                       id,stop_loss_usdt,last_triggered_at_ms,
                       last_triggered_net_pnl_usdt,updated_at_ms
                   ) VALUES (1,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       stop_loss_usdt=excluded.stop_loss_usdt,
                       last_triggered_at_ms=excluded.last_triggered_at_ms,
                       last_triggered_net_pnl_usdt=excluded.last_triggered_net_pnl_usdt,
                       updated_at_ms=excluded.updated_at_ms""",
                (
                    float(self.stop_loss_usdt),
                    self.stop_loss_last_triggered_at_ms,
                    self.stop_loss_last_triggered_net_pnl_usdt,
                    v1._now_ms(),
                ),
            )
            self.db.commit()

    @staticmethod
    def _readonly_sqlite(path: Path) -> sqlite3.Connection:
        resolved = path.resolve().as_posix()
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _sync_settlements(self, *, force: bool = False) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        if (
            not force
            and self.settlement_sync_last_ms > 0
            and now_ms - self.settlement_sync_last_ms < SETTLEMENT_SYNC_INTERVAL_MS
        ):
            return dict(self.settlement_sync_state)
        self.settlement_sync_last_ms = now_ms

        if not self.settlement_db_path.exists():
            self.settlement_sync_state = {
                "status": "UNAVAILABLE",
                "source": str(self.settlement_db_path),
                "asOfMs": now_ms,
                "rows": 0,
                "error": "Wallet Shadow settlement DB does not exist",
            }
            return dict(self.settlement_sync_state)

        source = None
        try:
            source = self._readonly_sqlite(self.settlement_db_path)
            table = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wallet_target_taker_public_side_v1_results'"
            ).fetchone()
            if table is None:
                raise RuntimeError("Target Taker settlement table is not available yet")
            rows = source.execute(
                """SELECT cohort,market_id,winner,resolved_at_ms
                     FROM wallet_target_taker_public_side_v1_results
                    WHERE winner IN ('UP','DOWN')
                    ORDER BY resolved_at_ms"""
            ).fetchall()
            with self.db_lock:
                self.db.executemany(
                    """INSERT INTO engine_settlements(
                           cohort,market_id,winner,resolved_at_ms,source,synced_at_ms
                       ) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(cohort,market_id) DO UPDATE SET
                           winner=excluded.winner,
                           resolved_at_ms=excluded.resolved_at_ms,
                           source=excluded.source,
                           synced_at_ms=excluded.synced_at_ms""",
                    [
                        (
                            str(row["cohort"]),
                            int(row["market_id"]),
                            str(row["winner"]).upper(),
                            int(row["resolved_at_ms"]),
                            "wallet_target_taker_public_side_v1_results",
                            now_ms,
                        )
                        for row in rows
                    ],
                )
                self.db.commit()
            self.settlement_sync_state = {
                "status": "OK",
                "source": str(self.settlement_db_path),
                "table": "wallet_target_taker_public_side_v1_results",
                "asOfMs": now_ms,
                "rows": len(rows),
                "error": None,
            }
        except Exception as exc:
            self.settlement_sync_state = {
                "status": "UNAVAILABLE",
                "source": str(self.settlement_db_path),
                "asOfMs": now_ms,
                "rows": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
        finally:
            if source is not None:
                source.close()
        return dict(self.settlement_sync_state)

    @staticmethod
    def _settled_pnl(order: dict[str, Any], settlement: dict[str, Any] | None) -> dict[str, Any]:
        winner = str((settlement or {}).get("winner") or "").upper()
        side = str(order.get("side") or "").upper()
        status = str(order.get("status") or "").upper()
        try:
            cost = float(order.get("submitted_usdt") or 0.0)
            shares = float(order.get("shares") or 0.0)
        except (TypeError, ValueError):
            cost = shares = 0.0

        result_status = "PENDING"
        pnl = roi = None
        if (
            status == "SUBMITTED"
            and winner in {"UP", "DOWN"}
            and side in {"UP", "DOWN"}
            and cost > 0
            and shares > 0
        ):
            payout = shares if side == winner else 0.0
            pnl = payout - cost
            roi = pnl / cost
            result_status = "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
        return {
            "winner": winner or None,
            "resolvedAtMs": (settlement or {}).get("resolved_at_ms"),
            "resultStatus": result_status,
            "netPnlUsdt": pnl,
            "netRoi": roi,
        }

    def _settlement_map(self) -> dict[tuple[str, int], dict[str, Any]]:
        with self.db_lock:
            return {
                (str(row["cohort"]), int(row["market_id"])): dict(row)
                for row in self.db.execute("SELECT * FROM engine_settlements")
            }

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        self._sync_settlements()
        rows = super().orders(limit)
        settlements = self._settlement_map()
        for row in rows:
            settlement = settlements.get((str(row.get("cohort") or ""), int(row.get("market_id") or 0)))
            row.update(self._settled_pnl(row, settlement))
        return rows

    def _performance_snapshot(self, *, force_sync: bool = False) -> dict[str, Any]:
        self._sync_settlements(force=force_sync)
        settlements = self._settlement_map()
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM engine_orders ORDER BY attempted_at_ms ASC,id ASC"
                )
            ]

        attempts = len(rows)
        submitted = rejected = ambiguous = 0
        settled = wins = losses = 0
        stake = pnl = equity = peak = max_drawdown = 0.0
        current_loss_streak = longest_loss_streak = 0

        for row in rows:
            status = str(row.get("status") or "").upper()
            submitted += int(status == "SUBMITTED")
            rejected += int(status == "REJECTED")
            ambiguous += int(status == "AMBIGUOUS")
            settlement = settlements.get((str(row.get("cohort") or ""), int(row.get("market_id") or 0)))
            computed = self._settled_pnl(row, settlement)
            row_pnl = computed["netPnlUsdt"]
            if row_pnl is None:
                continue
            settled += 1
            result_status = str(computed["resultStatus"])
            wins += int(result_status == "WIN")
            losses += int(result_status == "LOSS")
            cost = float(row.get("submitted_usdt") or 0.0)
            stake += cost
            pnl += float(row_pnl)
            equity += float(row_pnl)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if result_status == "LOSS":
                current_loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, current_loss_streak)
            else:
                current_loss_streak = 0

        return {
            "attempts": attempts,
            "submitted": submitted,
            "rejected": rejected,
            "ambiguous": ambiguous,
            "settledCounted": settled,
            "wins": wins,
            "losses": losses,
            "winRate": wins / settled if settled else None,
            "stakeUsdt": stake,
            "netPnlUsdt": pnl,
            "netRoi": pnl / stake if stake > 0 else None,
            "maxDrawdownUsdt": max_drawdown,
            "currentLossStreak": current_loss_streak,
            "longestLossStreak": longest_loss_streak,
            "accountingBasis": (
                "Official Target Taker market winner joined to Echtgeld Engine SUBMITTED orders using "
                "reconciled submittedUsdt/filledShareQty. This is tracked strategy PnL, not an exchange account statement."
            ),
            "settlementSync": dict(self.settlement_sync_state),
        }

    def performance(self) -> dict[str, Any]:
        return self._performance_snapshot()

    def _risk_snapshot(self, performance: dict[str, Any] | None = None) -> dict[str, Any]:
        perf = performance if isinstance(performance, dict) else self._performance_snapshot()
        net_pnl = float(perf.get("netPnlUsdt") or 0.0)
        threshold = float(self.stop_loss_usdt)
        enabled = threshold > 0
        tripped = enabled and net_pnl <= -threshold
        return {
            "enabled": enabled,
            "stopLossUsdt": threshold,
            "basis": RISK_BASIS,
            "currentNetPnlUsdt": net_pnl,
            "triggerAtOrBelowNetPnlUsdt": -threshold if enabled else None,
            "remainingLossBufferUsdt": max(0.0, threshold + net_pnl) if enabled else None,
            "tripped": tripped,
            "lastTriggeredAtMs": self.stop_loss_last_triggered_at_ms,
            "lastTriggeredNetPnlUsdt": self.stop_loss_last_triggered_net_pnl_usdt,
            "lastCheckAtMs": self.risk_last_check_at_ms,
            "lastError": self.risk_last_error,
            "monitorIntervalMs": int(RISK_MONITOR_INTERVAL_SECONDS * 1000),
            "enforcement": "BACKGROUND+INTENT_ACCEPT+PRE_VENUE+RESUME",
        }

    def _auto_pause_stop_loss(self, *, net_pnl: float, threshold: float) -> bool:
        with self.runtime_lock:
            if not self.armed:
                return False
            old = self.executor
            self.armed = False
            self.executor = self.executor_factory(self._executor_config(mode="paper"))
            self.balance_cache = None
            try:
                old.close()
            except Exception:
                pass
            triggered_at_ms = v1._now_ms()
            self.stop_loss_last_triggered_at_ms = triggered_at_ms
            self.stop_loss_last_triggered_net_pnl_usdt = float(net_pnl)
            self._persist_risk_control()
            self._record_event(
                "ERROR",
                "AUTO_PAUSED_STOP_LOSS",
                "PAUSED_STOP_LOSS",
                (
                    f"Echtgeld auto-paused: settled net PnL {net_pnl:.6f} USDT "
                    f"reached configured loss limit {threshold:.6f} USDT"
                ),
                context={
                    "basis": RISK_BASIS,
                    "stopLossUsdt": threshold,
                    "currentNetPnlUsdt": net_pnl,
                    "triggerAtOrBelowNetPnlUsdt": -threshold,
                },
            )
            return True

    def _enforce_stop_loss(self, *, force_sync: bool = False) -> dict[str, Any]:
        # A zero threshold is intentionally a true off switch. Do not add
        # settlement DB I/O to the live intent/worker path when the operator has
        # disabled the stop loss.
        if self.stop_loss_usdt <= 0:
            self.risk_last_check_at_ms = v1._now_ms()
            self.risk_last_error = None
            return self._risk_snapshot({"netPnlUsdt": 0.0})
        try:
            performance = self._performance_snapshot(force_sync=force_sync)
            self.risk_last_check_at_ms = v1._now_ms()
            self.risk_last_error = None
            snapshot = self._risk_snapshot(performance)
            if snapshot["tripped"]:
                self._auto_pause_stop_loss(
                    net_pnl=float(snapshot["currentNetPnlUsdt"]),
                    threshold=float(snapshot["stopLossUsdt"]),
                )
            return snapshot
        except Exception as exc:
            self.risk_last_check_at_ms = v1._now_ms()
            self.risk_last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise

    def _risk_loop(self) -> None:
        while not self.stop_event.wait(RISK_MONITOR_INTERVAL_SECONDS):
            try:
                self._enforce_stop_loss(force_sync=False)
            except Exception:
                # State exposes the monitor error. Do not spam durable events on
                # every poll if the observation-only settlement source is down.
                pass

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise v1.EchtgeldEngineError("settings must be a JSON object")
        with self.runtime_lock:
            if self.armed:
                raise v1.EchtgeldEngineError(
                    "Pause Echtgeld before changing venue, cohort, notional, drift or stop loss"
                )
            stop_loss = (
                self._validated_stop_loss(values.get("stopLossUsdt"))
                if "stopLossUsdt" in values
                else float(self.stop_loss_usdt)
            )
            replacement = self._validated_config(values, mode="paper")
            old = self.executor
            self.config = replacement
            self.stop_loss_usdt = stop_loss
            self.executor = self.executor_factory(self._executor_config(mode="paper"))
            self._persist_config(self.config)
            self._persist_risk_control()
            self.balance_cache = None
            old.close()
            self._record_event(
                "INFO",
                "SETTINGS_UPDATED",
                "PAUSED",
                "Echtgeld settings updated while PAUSED",
                context={
                    **self.config.snapshot(),
                    "stopLossUsdt": self.stop_loss_usdt,
                    "stopLossBasis": RISK_BASIS,
                },
            )
        return self.state()

    def resume(self) -> dict[str, Any]:
        risk = self._enforce_stop_loss(force_sync=True)
        if risk["tripped"]:
            raise v1.EchtgeldEngineError(
                "Cannot resume Echtgeld: settled net PnL "
                f"{float(risk['currentNetPnlUsdt']):.6f} USDT is at/below the configured "
                f"-{float(risk['stopLossUsdt']):.6f} USDT stop loss. "
                "While PAUSED, increase stopLossUsdt or set it to 0 to disable."
            )
        return super().resume()

    def submit_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Force a settlement refresh immediately before deciding whether a new
        # strategy intent is allowed into the live queue. The zero-threshold
        # fast path above makes this a no-I/O check when the guard is disabled.
        self._enforce_stop_loss(force_sync=True)
        return super().submit_intent(payload)

    def _process_intent(self, intent_id: str) -> None:
        # Second fence: re-check immediately before the parent worker can create
        # its durable ATTEMPTING row and touch the venue.
        self._enforce_stop_loss(force_sync=True)
        super()._process_intent(intent_id)

    def close(self) -> None:
        self.stop_event.set()
        if self.risk_thread is not None and self.risk_thread.is_alive():
            self.risk_thread.join(timeout=2.5)
        super().close()

    def state(self) -> dict[str, Any]:
        payload = super().state()
        performance = self._performance_snapshot()
        risk = self._risk_snapshot(performance)
        payload["version"] = VERSION
        payload["performance"] = performance
        payload["recentOrders"] = self.orders(50)
        payload["settlementSync"] = dict(self.settlement_sync_state)
        payload["monitoringCompatibility"] = "4310_LIVE_CONTROL_BALANCE_PNL"
        payload["riskControl"] = risk
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        config["stopLossUsdt"] = float(self.stop_loss_usdt)
        payload["config"] = config
        if risk["tripped"] and not self.armed:
            payload["runtimeStatus"] = "PAUSED_STOP_LOSS"
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["settlementDbPath"] = str(self.settlement_db_path)
        payload["stopLossEnabled"] = bool(self.stop_loss_usdt > 0)
        payload["stopLossUsdt"] = float(self.stop_loss_usdt)
        payload["riskMonitorAlive"] = bool(
            self.risk_thread and self.risk_thread.is_alive()
        ) if self.risk_thread is not None else True
        return payload


class _Handler(v1._Handler):
    engine: EchtgeldEngine


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV2Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "strategy-observer-independent=true; balance=4310-payment-options+MPC-safety; "
        "pnl=durable-settlement-ledger; stopLoss=durable-settled-net-pnl",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
