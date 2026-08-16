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


class EngineEventRedeemManager(EchtgeldRedeemManager):
    """Redeem manager that mirrors old 4310 lifecycle transitions into engine_events."""

    def __init__(
        self,
        *args: Any,
        event_sink: Callable[..., None],
        balance_invalidator: Callable[[], None],
        **kwargs: Any,
    ) -> None:
        self._event_sink = event_sink
        self._balance_invalidator = balance_invalidator
        self._last_scan_error_emitted: str | None = None
        super().__init__(*args, **kwargs)

    def _row_for_token(self, token_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM engine_redeems WHERE token_id=?",
                (str(token_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def _emit(
        self,
        level: str,
        event_type: str,
        message: str,
        row: dict[str, Any] | None,
        *,
        error_class: str | None = None,
    ) -> None:
        item = dict(row or {})
        market_id = item.get("venue_market_id")
        side = item.get("side")
        context = {
            "marketId": market_id,
            "side": side,
            "tokenId": item.get("token_id"),
            "redeemStatus": item.get("status"),
            "redeemTxHash": item.get("tx_hash"),
            "claimableValueUsdt": item.get("claimable_value_usdt"),
            "shares": item.get("shares"),
            "attemptCount": item.get("attempt_count"),
            "pauseIndependent": True,
            "retryAmbiguousRedeem": False,
        }
        self._event_sink(
            level,
            event_type,
            "SETTLEMENT",
            message,
            context=context,
            error_class=error_class,
        )

    def _upsert_claimable(self, position: dict[str, Any], tracked: dict[int, set[str]]) -> bool:
        token_id = str(position.get("tokenId") or "").strip()
        before = self._row_for_token(token_id) if token_id else None
        admitted = super()._upsert_claimable(position, tracked)
        if admitted and before is None and token_id:
            row = self._row_for_token(token_id)
            if row is not None:
                self._emit(
                    "INFO",
                    "AUTO_REDEEM_CLAIMABLE",
                    (
                        f"Binance marked tracked Echtgeld market {row.get('venue_market_id')} "
                        f"{row.get('side') or ''} claimable; redeem waits for the 60s settlement fence"
                    ).strip(),
                    row,
                )
        return admitted

    def _submit_row(self, client: Any, wallet: dict[str, str], row: dict[str, Any]) -> None:
        token_id = str(row.get("token_id") or "")
        self._emit(
            "INFO",
            "AUTO_REDEEM_ATTEMPTED",
            (
                f"Submitting one-token auto-redeem for tracked Echtgeld market "
                f"{row.get('venue_market_id')} after the 60s delay"
            ),
            row,
        )
        super()._submit_row(client, wallet, row)
        after = self._row_for_token(token_id)
        if after is None:
            return
        status = str(after.get("status") or "").upper()
        if status == "SUBMITTED":
            self._emit(
                "INFO",
                "AUTO_REDEEM_SUBMITTED",
                f"Auto-redeem submitted for market {after.get('venue_market_id')}; awaiting redeem/status reconciliation",
                after,
            )
        elif status == "REDEEMED":
            self._balance_invalidator()
            self._emit(
                "INFO",
                "AUTO_REDEEM_COMPLETED",
                (
                    f"Auto-redeem confirmed for market {after.get('venue_market_id')}; "
                    f"claimable value {float(after.get('claimable_value_usdt') or 0):.8f} USDT"
                ),
                after,
            )
        elif status == "AMBIGUOUS":
            self._emit(
                "ERROR",
                "AUTO_REDEEM_AMBIGUOUS",
                "Redeem result is uncertain and will not be retried automatically: "
                + str(after.get("last_error") or "unknown venue result"),
                after,
                error_class="AMBIGUOUS_REDEEM",
            )
        elif status == "FAILED_REVIEW":
            self._emit(
                "ERROR",
                "AUTO_REDEEM_REJECTED",
                str(after.get("last_error") or "Binance rejected auto-redeem; manual review required"),
                after,
                error_class="REDEEM_REJECTED",
            )

    def _reconcile_row(self, client: Any, wallet_address: str, row: dict[str, Any]) -> None:
        token_id = str(row.get("token_id") or "")
        before_status = str(row.get("status") or "").upper()
        super()._reconcile_row(client, wallet_address, row)
        after = self._row_for_token(token_id)
        if after is None:
            return
        status = str(after.get("status") or "").upper()
        if status == before_status:
            return
        if status == "REDEEMED":
            self._balance_invalidator()
            self._emit(
                "INFO",
                "AUTO_REDEEM_COMPLETED",
                (
                    f"Auto-redeem confirmed for market {after.get('venue_market_id')}; "
                    f"claimable value {float(after.get('claimable_value_usdt') or 0):.8f} USDT"
                ),
                after,
            )
        elif status == "FAILED_REVIEW":
            self._emit(
                "ERROR",
                "AUTO_REDEEM_REJECTED",
                str(after.get("last_error") or "Redeem reached a terminal failure; automatic resubmit is forbidden"),
                after,
                error_class="REDEEM_REJECTED",
            )
        elif status in {"PENDING", "PROCESSING", "SUBMITTED"}:
            self._emit(
                "INFO",
                "AUTO_REDEEM_PENDING",
                f"Auto-redeem for market {after.get('venue_market_id')} is {status}; no duplicate submit will be sent",
                after,
            )

    def run_cycle(self) -> bool:
        ok = super().run_cycle()
        if self.status == "ERROR" and self.last_error:
            if self.last_error != self._last_scan_error_emitted:
                self._last_scan_error_emitted = self.last_error
                self._emit(
                    "ERROR",
                    "AUTO_REDEEM_SCAN_FAILED",
                    self.last_error,
                    None,
                    error_class="REDEEM_SCAN_ERROR",
                )
        elif self.status != "ERROR":
            self._last_scan_error_emitted = None
        return ok


class EchtgeldEngine(v2.EchtgeldEngine):
    """Current 8781 V2 engine plus the proven 4310 claim/redeem lifecycle."""

    def __init__(
        self,
        db_path: Path | str = v1.DB_PATH,
        *,
        executor_factory: Callable[[TargetTakerLiveConfig], TargetTakerLiveExecutor] = TargetTakerLiveExecutor,
        start_worker: bool = True,
        settlement_db_path: Path | str = v2.SETTLEMENT_DB_PATH,
        redeem_manager_factory: Callable[..., EchtgeldRedeemManager] | None = None,
    ) -> None:
        self.redeem_manager: EchtgeldRedeemManager | None = None
        super().__init__(
            db_path,
            executor_factory=executor_factory,
            start_worker=start_worker,
            settlement_db_path=settlement_db_path,
        )
        factory = redeem_manager_factory
        if factory is None:
            self.redeem_manager = EngineEventRedeemManager(
                self.db_path,
                venue_getter=lambda: str(self.config.venue),
                start_worker=start_worker,
                event_sink=self._record_event,
                balance_invalidator=self._invalidate_balance_cache,
            )
        else:
            self.redeem_manager = factory(
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

    def _invalidate_balance_cache(self) -> None:
        self.balance_cache = None

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
        # V2 settlement PnL calculation. Redeem lifecycle transitions are also
        # persisted into engine_events, so the existing permanent-message table
        # requires no risky Dashboard rewrite.
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
