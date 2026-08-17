from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_20 as v4_20
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .target_taker_auto_bankroll_v1 import AutoBankrollMixin
from .target_taker_live_execution_v4 import TargetTakerLiveConfig, TargetTakerLiveExecutor


VERSION = "PREDICT_WALLET_SHADOW_V0_26_TARGET_TAKER_LIVE_V1_AUTO_BANKROLL_V1_DASHBOARD_CONTROL_V1"


class WalletShadowObserver(AutoBankrollMixin, v4_20.WalletShadowObserver):
    """Target Taker paper labs, isolated live bridge, and localhost dashboard control.

    Dashboard controls are process-runtime overrides only. A restart always falls
    back to the launcher/environment configuration, so a previous UI Resume can
    never silently re-arm Echtgeld after a process restart.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        startup = TargetTakerLiveConfig.from_env()
        self.target_taker_live_startup_config = startup
        self.target_taker_live_config = startup
        self.target_taker_live_executor = TargetTakerLiveExecutor(startup)
        self.target_taker_live_last: dict[str, Any] | None = None
        self.target_taker_live_runtime_lock = threading.RLock()
        self.target_taker_live_settings_updated_at_ms: int | None = None
        self.target_taker_balance_cache: dict[str, Any] | None = None
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
            with self.target_taker_live_runtime_lock:
                self.target_taker_live_executor.close()
        finally:
            super().stop()

    @staticmethod
    def _cohort_value(value: Any) -> str:
        raw = str(value or "").strip()
        aliases = {
            "SIDE_ONLY": public_side.SIDE_ONLY_COHORT,
            "HAZARD_SIDE": public_side.HAZARD_SIDE_COHORT,
        }
        raw = aliases.get(raw.upper(), raw)
        if raw not in public_side.COHORTS:
            raise ValueError(
                "Target Taker live cohort must be SIDE_ONLY or HAZARD_SIDE"
            )
        return raw

    def update_target_taker_live_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        """Apply localhost-requested settings to future entries only.

        This method does not persist runtime enablement. The Vite control layer
        supplies a localhost-only session token; 8776 itself is also loopback-bound.
        """

        if not isinstance(values, dict):
            raise ValueError("settings payload must be an object")
        with self.target_taker_live_runtime_lock:
            current = self.target_taker_live_config
            enabled = current.mode == "live"
            if "runtimeEnabled" in values:
                if not isinstance(values["runtimeEnabled"], bool):
                    raise ValueError("runtimeEnabled must be boolean")
                enabled = bool(values["runtimeEnabled"])

            venue = str(values.get("venue", current.venue)).strip().lower()
            cohort = self._cohort_value(values.get("cohort", current.cohort))
            try:
                notional = float(values.get("notionalUsdt", current.notional_usdt))
                drift = float(values.get("maxPriceDrift", current.max_price_drift))
            except (TypeError, ValueError) as exc:
                raise ValueError("Target Taker numeric setting is invalid") from exc

            config = TargetTakerLiveConfig(
                mode="live" if enabled else "paper",
                venue=venue,
                notional_usdt=notional,
                cohort=cohort,
                max_price_drift=drift,
            )
            if config.venue not in {"predictfun", "binance"}:
                raise ValueError("venue must be predictfun or binance")
            if not 0 < config.notional_usdt <= 100:
                raise ValueError("notionalUsdt must be within (0, 100]")
            if not 0 <= config.max_price_drift <= 0.10:
                raise ValueError("maxPriceDrift must be within [0, 0.10]")

            old = self.target_taker_live_executor
            replacement = TargetTakerLiveExecutor(config)
            self.target_taker_live_config = config
            self.target_taker_live_executor = replacement
            self.target_taker_live_settings_updated_at_ms = base._now_ms()
            self.target_taker_balance_cache = None
            old.close()
        return self._target_taker_live_control_snapshot(include_recent=True)

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

        with self.target_taker_live_runtime_lock:
            config = self.target_taker_live_config
            executor = self.target_taker_live_executor
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
                result = executor.execute(
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

    def _target_taker_live_rows(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db_lock:
            return [
                dict(row)
                for row in self.db.execute(
                    """SELECT l.cohort,l.market_id,l.venue,l.mode,l.signal_id,l.side,
                              l.target_notional_usdt,l.signal_ask,l.status,l.attempted_at_ms,
                              l.completed_at_ms,l.execution_price,l.shares,l.submitted_usdt,
                              l.vendor_order_id,l.error_message,r.winner,r.resolved_at_ms
                         FROM wallet_target_taker_public_side_v1_live_orders l
                         LEFT JOIN wallet_target_taker_public_side_v1_results r
                           ON r.cohort=l.cohort AND r.market_id=l.market_id
                        ORDER BY l.attempted_at_ms DESC LIMIT ?""",
                    (max(1, min(1000, int(limit))),),
                )
            ]

    def _target_taker_live_performance(self) -> dict[str, Any]:
        rows = list(reversed(self._target_taker_live_rows(1000)))
        attempts = len(rows)
        submitted = sum(str(row.get("status") or "").upper() == "SUBMITTED" for row in rows)
        rejected = sum(str(row.get("status") or "").upper() == "REJECTED" for row in rows)
        ambiguous = sum(str(row.get("status") or "").upper() == "AMBIGUOUS" for row in rows)
        settled = wins = losses = 0
        stake = pnl = 0.0
        equity = peak = max_drawdown = 0.0
        current_loss_streak = longest_loss_streak = 0
        recent: list[dict[str, Any]] = []

        for row in rows:
            enriched = dict(row)
            row_status = str(row.get("status") or "").upper()
            winner = str(row.get("winner") or "").upper()
            side = str(row.get("side") or "").upper()
            submitted_usdt = float(row.get("submitted_usdt") or 0.0)
            shares = float(row.get("shares") or 0.0)
            result_status = "PENDING"
            row_pnl: float | None = None
            row_roi: float | None = None
            if (
                row_status == "SUBMITTED"
                and winner in {"UP", "DOWN"}
                and side in {"UP", "DOWN"}
                and submitted_usdt > 0
                and shares > 0
            ):
                settled += 1
                payout = shares if side == winner else 0.0
                row_pnl = payout - submitted_usdt
                row_roi = row_pnl / submitted_usdt
                result_status = "WIN" if row_pnl > 1e-9 else "LOSS" if row_pnl < -1e-9 else "FLAT"
                wins += int(result_status == "WIN")
                losses += int(result_status == "LOSS")
                stake += submitted_usdt
                pnl += row_pnl
                equity += row_pnl
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, peak - equity)
                if result_status == "LOSS":
                    current_loss_streak += 1
                    longest_loss_streak = max(longest_loss_streak, current_loss_streak)
                else:
                    current_loss_streak = 0
            enriched["resultStatus"] = result_status
            enriched["netPnlUsdt"] = row_pnl
            enriched["netRoi"] = row_roi
            recent.append(enriched)

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
                "Official market winner joined to SUBMITTED FOK order amounts; venue final fill status "
                "is not independently reconciled, so this is tracked FOK accounting rather than an exchange statement."
            ),
            "recentOrders": list(reversed(recent[-20:])),
        }

    def _target_taker_balance(self) -> dict[str, Any]:
        market_id = int(self.market_id or 0)
        with self.target_taker_live_runtime_lock:
            venue = self.target_taker_live_config.venue
            key = f"{venue}:{market_id}"
            cached = self.target_taker_balance_cache
            if isinstance(cached, dict) and cached.get("cacheKey") == key:
                return dict(cached)
            if market_id <= 0:
                result = {
                    "status": "WAITING_MARKET",
                    "venue": venue,
                    "availableUsdt": None,
                    "asOfMs": base._now_ms(),
                }
            else:
                result = self.target_taker_live_executor.available_balance_snapshot()
            result = {
                **result,
                "marketId": market_id or None,
                "cacheKey": key,
                "refreshPolicy": "ONCE_PER_MARKET_ROUND_OR_VENUE_CHANGE",
            }
            self.target_taker_balance_cache = result
            return dict(result)

    def _target_taker_live_control_snapshot(self, *, include_recent: bool = True) -> dict[str, Any]:
        config = self.target_taker_live_config
        performance = self._target_taker_live_performance()
        return {
            **config.snapshot(),
            "armed": config.mode == "live",
            "runtimeEnabled": config.mode == "live",
            "runtimeStatus": "LIVE" if config.mode == "live" else "PAUSED",
            "startupMode": self.target_taker_live_startup_config.mode,
            "settingsPersistence": "PROCESS_RUNTIME_ONLY",
            "settingsUpdatedAtMs": self.target_taker_live_settings_updated_at_ms,
            "paperCohortsStillActive": True,
            "autoBankrollPaperWalletActive": True,
            "oneLiveAttemptPerMarket": True,
            "balance": self._target_taker_balance(),
            "performance": performance,
            "lastExecution": self.target_taker_live_last,
            "recentOrders": performance["recentOrders"] if include_recent else [],
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["targetTakerLiveV1"] = self._target_taker_live_control_snapshot(include_recent=True)
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        control = self._target_taker_live_control_snapshot(include_recent=False)
        payload["targetTakerLiveV1"] = control
        payload["paperOnly"] = not bool(control["runtimeEnabled"])
        payload["liveOrdersAffected"] = bool(control["runtimeEnabled"])
        return payload


class _Handler(v4_20._Handler):
    observer: WalletShadowObserver

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/settings", "/api/settings"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > 16_384:
            self._send(400, {"ok": False, "error": "invalid settings request size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("settings payload must be an object")
            self.observer.update_target_taker_live_settings(payload)
        except (ValueError, TypeError) as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
            return
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
            return
        self._send(200, {"ok": True, "state": self.observer.snapshot()})


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_21Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    config = observer.target_taker_live_config
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"TargetTakerMode={config.mode}; venue={config.venue}; notional={config.notional_usdt:.2f} USDT; "
        f"cohort={config.cohort}; dashboardControl=localhost-runtime-only; "
        "paperCohortsStillActive=true; autoBankrollPaperWallet=true",
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
