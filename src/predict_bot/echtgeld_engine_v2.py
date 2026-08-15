from __future__ import annotations

import os
import sqlite3
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import echtgeld_engine_v1 as v1
from .target_taker_live_execution_v5 import (
    TargetTakerLiveConfig,
    TargetTakerLiveExecutor,
)


VERSION = "ECHTGELD_ENGINE_V2_4310_BALANCE_PNL"
HOST = str(os.environ.get("PREDICT_ECHTGELD_ENGINE_HOST") or "127.0.0.1").strip()
PORT = int(os.environ.get("PREDICT_ECHTGELD_ENGINE_PORT") or "8781")
ROOT = Path(__file__).resolve().parents[2]
SETTLEMENT_DB_PATH = Path(
    os.environ.get("PREDICT_ECHTGELD_SETTLEMENT_DB")
    or os.environ.get("PREDICT_WALLET_SHADOW_DB")
    or ROOT / "data" / "predict_wallet_shadow.db"
)
SETTLEMENT_SYNC_INTERVAL_MS = 5_000


class EchtgeldEngine(v1.EchtgeldEngine):
    """V1 execution engine plus old-live-control style balance and PnL monitoring.

    Settlement ingestion is observation-only. Failure to read the research DB is
    reported in state but never blocks, submits, retries, or replays an order.
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
        super().__init__(
            db_path,
            executor_factory=executor_factory,
            start_worker=start_worker,
        )

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
                """
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

    def performance(self) -> dict[str, Any]:
        self._sync_settlements()
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

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["performance"] = self.performance()
        payload["recentOrders"] = self.orders(50)
        payload["settlementSync"] = dict(self.settlement_sync_state)
        payload["monitoringCompatibility"] = "4310_LIVE_CONTROL_BALANCE_PNL"
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["settlementDbPath"] = str(self.settlement_db_path)
        return payload


class _Handler(v1._Handler):
    engine: EchtgeldEngine


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV2Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "strategy-observer-independent=true; balance=4310-payment-options+MPC-safety; pnl=durable-settlement-ledger",
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
