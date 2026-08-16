from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v2 as v2
from .echtgeld_redeem_v1 import EchtgeldRedeemManager
from .target_taker_live_execution_v5 import TargetTakerLiveConfig, TargetTakerLiveExecutor


# Keep the ECHTGELD_ENGINE_V2 prefix for Dashboard/start-script compatibility.
# This module is an additive lifecycle wrapper; order execution, durable intent
# dedupe, settlement PnL, balance and stop-loss remain exactly V2-owned.
VERSION = "ECHTGELD_ENGINE_V2_4310_BALANCE_PNL_STOP_LOSS_REDEEM_V1"
HOST = v2.HOST
PORT = v2.PORT


class EchtgeldEngine(v2.EchtgeldEngine):
    """Current 8781 V2 engine plus the proven 4310 claim/redeem lifecycle."""

    def __init__(
        self,
        db_path: Path | str = v1.DB_PATH,
        *,
        executor_factory: Callable[[TargetTakerLiveConfig], TargetTakerLiveExecutor] = TargetTakerLiveExecutor,
        start_worker: bool = True,
        settlement_db_path: Path | str = v2.SETTLEMENT_DB_PATH,
        redeem_manager_factory: Callable[..., EchtgeldRedeemManager] = EchtgeldRedeemManager,
    ) -> None:
        self.redeem_manager: EchtgeldRedeemManager | None = None
        super().__init__(
            db_path,
            executor_factory=executor_factory,
            start_worker=start_worker,
            settlement_db_path=settlement_db_path,
        )
        self.redeem_manager = redeem_manager_factory(
            self.db_path,
            venue_getter=lambda: str(self.config.venue),
            start_worker=start_worker,
        )
        self._record_event(
            "INFO",
            "AUTO_REDEEM_STARTED" if self.redeem_manager.enabled else "AUTO_REDEEM_DISABLED",
            "SETTLEMENT",
            (
                "4310 Binance claim/redeem lifecycle attached; it remains active while Echtgeld is PAUSED"
                if self.redeem_manager.enabled
                else "4310 Binance claim/redeem lifecycle is disabled by environment"
            ),
            context={
                "redeemVersion": self.redeem_manager.summary().get("version"),
                "pauseIndependent": True,
                "scope": "8781_SUBMITTED_BINANCE_MARKETS_ONLY",
                "retryAmbiguousRedeem": False,
            },
        )

    def run_redeem_cycle(self) -> bool:
        manager = self.redeem_manager
        return bool(manager and manager.run_cycle())

    def _redeem_by_market(self) -> dict[int, dict[str, Any]]:
        manager = self.redeem_manager
        if manager is None:
            return {}
        output: dict[int, dict[str, Any]] = {}
        for row in manager.recent(200):
            try:
                market_id = int(row.get("venue_market_id") or 0)
            except (TypeError, ValueError):
                market_id = 0
            if market_id > 0 and market_id not in output:
                output[market_id] = row
        return output

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = super().orders(limit)
        redeem_by_market = self._redeem_by_market()
        for row in rows:
            if str(row.get("venue") or "").lower() != "binance":
                row["redeemStatus"] = None
                continue
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            try:
                venue_market_id = int(
                    row.get("venueMarketId")
                    or result.get("venueMarketId")
                    or 0
                )
            except (TypeError, ValueError):
                venue_market_id = 0
            redeem = redeem_by_market.get(venue_market_id)
            if redeem is None:
                row["redeemStatus"] = None
                continue
            row["redeemStatus"] = str(redeem.get("status") or "") or None
            row["redeemTokenId"] = str(redeem.get("token_id") or "") or None
            row["redeemTxHash"] = str(redeem.get("tx_hash") or "") or None
            row["claimableValueUsdt"] = redeem.get("claimable_value_usdt")
            row["redeemCompletedAtMs"] = redeem.get("completed_at_ms")
        return rows

    def state(self) -> dict[str, Any]:
        payload = super().state()
        manager = self.redeem_manager
        payload["version"] = VERSION
        payload["monitoringCompatibility"] = "4310_LIVE_CONTROL_BALANCE_PNL_REDEEM"
        payload["autoRedeem"] = manager.summary() if manager is not None else {
            "version": "ECHTGELD_4310_REDEEM_V1",
            "enabled": False,
            "status": "NOT_INITIALIZED",
            "pauseIndependent": True,
        }
        payload["recentRedeems"] = manager.recent(50) if manager is not None else []
        # super().state() already called self.orders() through dynamic dispatch,
        # so recentOrders includes redeem lifecycle fields without replacing the
        # V2 settlement PnL calculation.
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        manager = self.redeem_manager
        payload["version"] = VERSION
        payload["autoRedeemEnabled"] = bool(manager and manager.enabled)
        payload["autoRedeemStatus"] = manager.status if manager is not None else "NOT_INITIALIZED"
        payload["redeemWorkerAlive"] = bool(
            manager and manager.thread and manager.thread.is_alive()
        ) if manager is not None and manager.enabled else True
        payload["redeemPauseIndependent"] = True
        return payload

    def close(self) -> None:
        manager = self.redeem_manager
        if manager is not None:
            manager.close()
        super().close()


class _Handler(v2._Handler):
    engine: EchtgeldEngine


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV3Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "strategy-observer-independent=true; balance=4310-payment-options+MPC-safety; "
        "pnl=durable-settlement-ledger; stopLoss=durable-settled-net-pnl; "
        "redeem=4310-venue-claimable+durable-no-retry",
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
