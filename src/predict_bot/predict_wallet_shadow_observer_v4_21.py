from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_20 as v4_20
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .target_taker_live_execution_v4 import TargetTakerLiveConfig, TargetTakerLiveExecutor


VERSION = "PREDICT_WALLET_SHADOW_V0_26_TARGET_TAKER_LIVE_V1"


class WalletShadowObserver(v4_20.WalletShadowObserver):
    """V4.20 plus an explicitly armed, isolated Target Taker live bridge.

    Paper cohorts continue unchanged. Only the configured public-side cohort may
    reach the live bridge, and a durable ATTEMPTING row is committed before any
    venue write so a restart can never blindly duplicate the same market order.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self.target_taker_live_config = TargetTakerLiveConfig.from_env()
        self.target_taker_live_executor = TargetTakerLiveExecutor(self.target_taker_live_config)
        self.target_taker_live_last: dict[str, Any] | None = None
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_target_taker_public_side_v1_live_orders (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    strategy_version TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    signal_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    target_notional_usdt REAL NOT NULL,
                    signal_ask REAL NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER,
                    execution_price REAL,
                    shares REAL,
                    submitted_usdt REAL,
                    vendor_order_id TEXT,
                    vendor_order_hash TEXT,
                    error_message TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_public_side_v1_live_orders_time
                    ON wallet_target_taker_public_side_v1_live_orders(attempted_at_ms);
                """
            )
            self.db.commit()

    def stop(self) -> None:
        try:
            self.target_taker_live_executor.close()
        finally:
            super().stop()

    def _execute_public_side(
        self,
        cohort: str,
        decision: dict[str, Any],
        hazard: dict[str, Any] | None,
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = self.public_side_states[cohort]
        before_event = state.get("event")
        super()._execute_public_side(
            cohort, decision, hazard, snapshot_ns=snapshot_ns, now_ms=now_ms
        )
        event = state.get("event")
        if before_event is not None or not isinstance(event, dict):
            return
        config = self.target_taker_live_config
        if config.mode != "live" or cohort != config.cohort:
            return

        signal_id = str(event.get("id") or f"{cohort}:{self.market_id}:{snapshot_ns}")
        side = str(decision.get("side") or "").upper()
        signal_ask = float(decision.get("ask") or 0.0)
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_target_taker_public_side_v1_live_orders(
                       cohort,market_id,strategy_version,venue,mode,signal_id,side,
                       target_notional_usdt,signal_ask,status,attempted_at_ms,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cohort,
                    int(self.market_id),
                    public_side.VERSION,
                    config.venue,
                    config.mode,
                    signal_id,
                    side,
                    float(config.notional_usdt),
                    signal_ask,
                    "ATTEMPTING",
                    int(now_ms),
                    json.dumps(
                        {
                            "status": "ATTEMPTING",
                            "venue": config.venue,
                            "strategy": public_side.VERSION,
                            "signalId": signal_id,
                            "side": side,
                            "signalAsk": signal_ask,
                            "notionalUsdt": config.notional_usdt,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            self.db.commit()
        if cursor.rowcount <= 0:
            return

        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            result = {
                "status": "REJECTED",
                "venue": config.venue,
                "strategy": public_side.VERSION,
                "error": "public signal snapshot disappeared before live execution",
                "completedAtMs": int(now_ms),
                "notionalUsdt": config.notional_usdt,
            }
        else:
            result = self.target_taker_live_executor.execute(
                cohort=cohort,
                market_id=int(self.market_id),
                decision=decision,
                snapshot=snapshot,
                signal_id=signal_id,
            )
        self.target_taker_live_last = result
        with self.db_lock:
            self.db.execute(
                """UPDATE wallet_target_taker_public_side_v1_live_orders
                      SET status=?,completed_at_ms=?,execution_price=?,shares=?,submitted_usdt=?,
                          vendor_order_id=?,vendor_order_hash=?,error_message=?,payload_json=?
                    WHERE cohort=? AND market_id=?""",
                (
                    str(result.get("status") or "UNKNOWN"),
                    int(result.get("completedAtMs") or now_ms),
                    result.get("executionPrice"),
                    result.get("shares"),
                    result.get("submittedUsdt"),
                    result.get("vendorOrderId"),
                    result.get("vendorOrderHash"),
                    str(result.get("error") or "")[:500] or None,
                    json.dumps(result, separators=(",", ":"), default=str),
                    cohort,
                    int(self.market_id),
                ),
            )
            self.db.commit()

    def _target_taker_live_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT cohort,market_id,venue,mode,signal_id,side,target_notional_usdt,
                              signal_ask,status,attempted_at_ms,completed_at_ms,execution_price,
                              shares,submitted_usdt,vendor_order_id,error_message
                         FROM wallet_target_taker_public_side_v1_live_orders
                        ORDER BY attempted_at_ms DESC LIMIT ?""",
                    (max(1, min(100, int(limit))),),
                )
            ]
        return rows

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["targetTakerLiveV1"] = {
            **self.target_taker_live_config.snapshot(),
            "armed": self.target_taker_live_config.mode == "live",
            "paperCohortsStillActive": True,
            "oneLiveAttemptPerMarket": True,
            "lastExecution": self.target_taker_live_last,
            "recentOrders": self._target_taker_live_recent(),
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["targetTakerLiveV1"] = {
            **self.target_taker_live_config.snapshot(),
            "armed": self.target_taker_live_config.mode == "live",
            "lastExecution": self.target_taker_live_last,
        }
        payload["paperOnly"] = self.target_taker_live_config.mode != "live"
        payload["liveOrdersAffected"] = self.target_taker_live_config.mode == "live"
        return payload


class _Handler(v4_20._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_21Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    config = observer.target_taker_live_config
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"TargetTakerMode={config.mode}; venue={config.venue}; notional={config.notional_usdt:.2f} USDT; "
        f"cohort={config.cohort}; paperCohortsStillActive=true",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
