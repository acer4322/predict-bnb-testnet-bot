from __future__ import annotations

import os
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3
from . import predict_wallet_shadow_observer_v4_1 as v4_1


VERSION = "PREDICT_WALLET_SHADOW_V0_4_3_SPOT_STRIKE_FORWARD"
COHORT = "SPOT_STRIKE_10S_V2"
SIMULATION_DB_PATH = Path(
    os.environ.get("PREDICT_WALLET_SHADOW_SIMULATION_DB", base.ROOT / "data" / "simulation.db")
)
DECISION_SECONDS = 10.0
MAX_DECISION_LATENESS_SECONDS = 3.0
MAX_ASK = 0.95
MAX_INPUT_AGE_MS = 3_000
STAKE_USDT = 1.0
FEE_RATE_BPS = 200


def effective_taker_cost(price: float, fee_rate_bps: int = FEE_RATE_BPS) -> float:
    return price + min(price, 1.0 - price) * fee_rate_bps / 10_000.0


class WalletShadowObserver(v4_1.WalletShadowObserver):
    """Independent paper-only forward cohort for the locked 10-second policy."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=SIMULATION_DB_PATH) -> None:
        self.forward_event: dict[str, Any] | None = None
        self.forward_decision: dict[str, Any] | None = None
        self.forward_last_block: dict[str, Any] | None = None
        super().__init__(db_path)
        self.simulation_db = sqlite3.connect(
            f"file:{Path(simulation_db_path).as_posix()}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=2.0,
        )
        self.simulation_db.row_factory = sqlite3.Row
        with self.db_lock:
            row = self.db.execute(
                "SELECT deployed_at_ms FROM wallet_spot_strike_forward_meta WHERE cohort=?",
                (COHORT,),
            ).fetchone()
            if row is None:
                self.forward_deployed_at_ms = base._now_ms()
                self.db.execute(
                    "INSERT INTO wallet_spot_strike_forward_meta(cohort,deployed_at_ms,policy_json) VALUES (?,?,?)",
                    (
                        COHORT,
                        self.forward_deployed_at_ms,
                        base.json.dumps(self._forward_config(), separators=(",", ":")),
                    ),
                )
                self.db.commit()
            else:
                self.forward_deployed_at_ms = int(row[0])

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_spot_strike_forward_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_spot_strike_forward_events (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    market_bucket INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    seconds_left REAL NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    effective_unit_cost REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    start_price REAL NOT NULL,
                    spot_price REAL NOT NULL,
                    displacement_bps REAL NOT NULL,
                    spot_age_ms REAL,
                    prediction_receipt_age_ms REAL NOT NULL,
                    source_market_id INTEGER NOT NULL,
                    source_observation_id INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort, market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_spot_strike_forward_decisions (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    market_bucket INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    seconds_left REAL NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    observed_ask REAL,
                    start_price REAL,
                    spot_price REAL,
                    displacement_bps REAL,
                    spot_age_ms REAL,
                    prediction_receipt_age_ms REAL,
                    source_market_id INTEGER,
                    source_observation_id INTEGER,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort, market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_spot_strike_forward_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    market_bucket INTEGER NOT NULL,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(cohort, market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_spot_strike_forward_results
                    ON wallet_spot_strike_forward_results(cohort, resolved_at_ms);
                """
            )
            self.db.commit()

    def _forward_config(self) -> dict[str, Any]:
        return {
            "decisionSeconds": DECISION_SECONDS,
            "maxDecisionLatenessSeconds": MAX_DECISION_LATENESS_SECONDS,
            "minDisplacementBps": 0.0,
            "maxAsk": MAX_ASK,
            "maxInputAgeMs": MAX_INPUT_AGE_MS,
            "stakeUsdt": STAKE_USDT,
            "feeRateBps": FEE_RATE_BPS,
            "oneEntryPerMarket": True,
            "holdToOfficialSettlement": True,
        }

    def stop(self) -> None:
        self.simulation_db.close()
        super().stop()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM wallet_spot_strike_forward_events WHERE cohort=? AND market_id=?",
                (COHORT, int(market_id)),
            ).fetchone()
            decision = self.db.execute(
                "SELECT * FROM wallet_spot_strike_forward_decisions WHERE cohort=? AND market_id=?",
                (COHORT, int(market_id)),
            ).fetchone()
        self.forward_event = dict(row) if row is not None else None
        self.forward_decision = dict(decision) if decision is not None else None
        self.forward_last_block = None

    def _current_spot_snapshot(self, bucket: int) -> dict[str, Any] | None:
        sequence = self.simulation_db.execute(
            "SELECT market_id FROM strategy_m_market_sequence WHERE start_ms=? ORDER BY sequence_no DESC LIMIT 1",
            (int(bucket) * 1000,),
        ).fetchone()
        if sequence is None:
            return None
        source_market_id = int(sequence[0])
        row = self.simulation_db.execute(
            """SELECT id,market_id,start_price,spot_price,seconds_left,spot_age_ms,timestamp
                 FROM observations
                WHERE market_id=? AND start_price IS NOT NULL AND spot_price IS NOT NULL
                ORDER BY id DESC LIMIT 1""",
            (source_market_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _record_forward_decision(
        self,
        *,
        decision: str,
        reason: str,
        seconds_left: float,
        details: dict[str, Any],
        commit: bool = True,
    ) -> bool:
        if self.market_id is None or self.bucket_start_sec is None or self.forward_decision is not None:
            return False
        payload = {
            "cohort": COHORT,
            "marketId": self.market_id,
            "marketBucket": self.bucket_start_sec,
            "decisionAtMs": base._now_ms(),
            "secondsLeft": seconds_left,
            "decision": decision,
            "reason": reason,
            **details,
        }
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_spot_strike_forward_decisions(
                       cohort,market_id,market_bucket,decision_at_ms,seconds_left,decision,
                       reason,side,observed_ask,start_price,spot_price,displacement_bps,
                       spot_age_ms,prediction_receipt_age_ms,source_market_id,
                       source_observation_id,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    COHORT, self.market_id, self.bucket_start_sec, payload["decisionAtMs"],
                    seconds_left, decision, reason, details.get("side"),
                    details.get("observedAsk"), details.get("startPrice"),
                    details.get("spotPrice"), details.get("displacementBps"),
                    details.get("spotAgeMs"), details.get("predictionReceiptAgeMs"),
                    details.get("sourceMarketId"), details.get("sourceObservationId"),
                    base.json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            if commit:
                self.db.commit()
        if cursor.rowcount <= 0:
            return False
        self.forward_decision = payload
        if decision == "SKIP":
            self.forward_last_block = payload
        return True

    def _block_forward(self, reason: str, seconds_left: float, **details: Any) -> None:
        self._record_forward_decision(
            decision="SKIP", reason=reason, seconds_left=seconds_left, details=details
        )

    def _advance_spot_strike(self, book: dict[str, Any]) -> None:
        if self.forward_decision is not None or self.market_id is None or self.bucket_start_sec is None:
            return
        seconds_left = base._finite(book.get("secondsLeft"))
        if seconds_left is None or not 0 < seconds_left <= DECISION_SECONDS:
            return
        if seconds_left < DECISION_SECONDS - MAX_DECISION_LATENESS_SECONDS:
            self._block_forward(
                "MISSED_DECISION_WINDOW",
                seconds_left,
                decisionLatenessSeconds=DECISION_SECONDS - seconds_left,
            )
            return
        now_ms = base._now_ms()
        received_ms = base._positive_int(book.get("receivedTimestampMs"))
        receipt_age_ms = now_ms - received_ms if received_ms is not None else None
        if receipt_age_ms is None or receipt_age_ms < 0 or receipt_age_ms > MAX_INPUT_AGE_MS:
            self._block_forward(
                "STALE_PREDICT_BOOK", seconds_left, predictionReceiptAgeMs=receipt_age_ms
            )
            return
        observation = self._current_spot_snapshot(self.bucket_start_sec)
        if observation is None:
            self._block_forward("NO_CAUSAL_SPOT_OBSERVATION", seconds_left)
            return
        spot_age_ms = base._finite(observation.get("spot_age_ms"))
        observation_seconds = base._finite(observation.get("seconds_left"))
        if (
            spot_age_ms is not None
            and (spot_age_ms < 0 or spot_age_ms > MAX_INPUT_AGE_MS)
        ):
            self._block_forward(
                "STALE_SPOT_TRADE",
                seconds_left,
                spotAgeMs=spot_age_ms,
                predictionReceiptAgeMs=receipt_age_ms,
                sourceMarketId=int(observation["market_id"]),
                sourceObservationId=int(observation["id"]),
            )
            return
        if observation_seconds is None or abs(observation_seconds - seconds_left) > 3.0:
            self._block_forward(
                "SPOT_PREDICT_TIME_SKEW",
                seconds_left,
                spotSecondsLeft=observation_seconds,
                predictSecondsLeft=seconds_left,
                spotAgeMs=spot_age_ms,
                predictionReceiptAgeMs=receipt_age_ms,
                sourceMarketId=int(observation["market_id"]),
                sourceObservationId=int(observation["id"]),
            )
            return
        start_price = base._finite(observation.get("start_price"))
        spot_price = base._finite(observation.get("spot_price"))
        if start_price is None or start_price <= 0 or spot_price is None or spot_price <= 0:
            self._block_forward(
                "INVALID_SPOT_OR_START",
                seconds_left,
                spotAgeMs=spot_age_ms,
                predictionReceiptAgeMs=receipt_age_ms,
                sourceMarketId=int(observation["market_id"]),
                sourceObservationId=int(observation["id"]),
            )
            return
        displacement_bps = (spot_price - start_price) / start_price * 10_000.0
        if abs(displacement_bps) <= 1e-12:
            self._block_forward(
                "ZERO_DISPLACEMENT",
                seconds_left,
                startPrice=start_price,
                spotPrice=spot_price,
                displacementBps=displacement_bps,
                spotAgeMs=spot_age_ms,
                predictionReceiptAgeMs=receipt_age_ms,
                sourceMarketId=int(observation["market_id"]),
                sourceObservationId=int(observation["id"]),
            )
            return
        side = "UP" if displacement_bps > 0 else "DOWN"
        ask = base._finite(book.get("upAsk" if side == "UP" else "downAsk"))
        if ask is None or not 0 < ask:
            self._block_forward(
                "NO_EXECUTABLE_ASK",
                seconds_left,
                side=side,
                observedAsk=ask,
                startPrice=start_price,
                spotPrice=spot_price,
                displacementBps=displacement_bps,
                spotAgeMs=spot_age_ms,
                predictionReceiptAgeMs=receipt_age_ms,
                sourceMarketId=int(observation["market_id"]),
                sourceObservationId=int(observation["id"]),
            )
            return
        if ask > MAX_ASK:
            self._block_forward(
                "ASK_ABOVE_MAX",
                seconds_left,
                side=side,
                observedAsk=ask,
                startPrice=start_price,
                spotPrice=spot_price,
                displacementBps=displacement_bps,
                spotAgeMs=spot_age_ms,
                predictionReceiptAgeMs=receipt_age_ms,
                sourceMarketId=int(observation["market_id"]),
                sourceObservationId=int(observation["id"]),
            )
            return
        unit_cost = effective_taker_cost(ask)
        shares = STAKE_USDT / unit_cost
        event = {
            "cohort": COHORT,
            "marketId": self.market_id,
            "marketBucket": self.bucket_start_sec,
            "decisionAtMs": now_ms,
            "secondsLeft": seconds_left,
            "side": side,
            "observedAsk": ask,
            "effectiveUnitCost": unit_cost,
            "stakeUsdt": STAKE_USDT,
            "shares": shares,
            "startPrice": start_price,
            "spotPrice": spot_price,
            "displacementBps": displacement_bps,
            "spotAgeMs": spot_age_ms,
            "predictionReceiptAgeMs": receipt_age_ms,
            "sourceMarketId": int(observation["market_id"]),
            "sourceObservationId": int(observation["id"]),
        }
        if not self._record_forward_decision(
            decision="TRADE",
            reason="LOCKED_POLICY_MATCH",
            seconds_left=seconds_left,
            details={
                "side": side,
                "observedAsk": ask,
                "startPrice": start_price,
                "spotPrice": spot_price,
                "displacementBps": displacement_bps,
                "spotAgeMs": spot_age_ms,
                "predictionReceiptAgeMs": receipt_age_ms,
                "sourceMarketId": int(observation["market_id"]),
                "sourceObservationId": int(observation["id"]),
            },
            commit=False,
        ):
            return
        try:
            with self.db_lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO wallet_spot_strike_forward_events(
                           cohort,market_id,market_bucket,decision_at_ms,seconds_left,side,
                           observed_ask,effective_unit_cost,stake_usdt,shares,start_price,
                           spot_price,displacement_bps,spot_age_ms,prediction_receipt_age_ms,
                           source_market_id,source_observation_id,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        COHORT, self.market_id, self.bucket_start_sec, now_ms, seconds_left, side,
                        ask, unit_cost, STAKE_USDT, shares, start_price, spot_price,
                        displacement_bps, spot_age_ms, receipt_age_ms,
                        int(observation["market_id"]), int(observation["id"]),
                        base.json.dumps(event, separators=(",", ":"), default=str),
                    ),
                )
                self.db.commit()
        except Exception:
            with self.db_lock:
                self.db.rollback()
            self.forward_decision = None
            raise
        self.forward_event = event
        self.forward_last_block = None

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_spot_strike(book)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        with self.db_lock:
            event = self.db.execute(
                "SELECT * FROM wallet_spot_strike_forward_events WHERE cohort=? AND market_id=?",
                (COHORT, int(market_id)),
            ).fetchone()
            if event is None:
                return
            payout = float(event["shares"]) if str(event["side"]) == winner else 0.0
            pnl = payout - float(event["stake_usdt"])
            roi = pnl / float(event["stake_usdt"])
            status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
            self.db.execute(
                """INSERT INTO wallet_spot_strike_forward_results(
                       cohort,market_id,market_bucket,winner,resolved_at_ms,side,
                       observed_ask,stake_usdt,shares,payout_usdt,net_pnl_usdt,net_roi,status
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,market_id) DO UPDATE SET
                       winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,
                       net_roi=excluded.net_roi,status=excluded.status""",
                (
                    COHORT, int(market_id), int(event["market_bucket"]), winner,
                    base._now_ms(), event["side"], event["observed_ask"],
                    event["stake_usdt"], event["shares"], payout, pnl, roi, status,
                ),
            )
            self.db.commit()

    def _forward_performance(self) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute(
                """SELECT COUNT(*) settled,
                          SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) wins,
                          COALESCE(SUM(stake_usdt),0) stake,
                          COALESCE(SUM(net_pnl_usdt),0) pnl
                     FROM wallet_spot_strike_forward_results WHERE cohort=?""",
                (COHORT,),
            ).fetchone()
            events = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_spot_strike_forward_events WHERE cohort=?",
                (COHORT,),
            ).fetchone()[0])
            decision_rows = [dict(item) for item in self.db.execute(
                """SELECT decision,reason,COUNT(*) count
                     FROM wallet_spot_strike_forward_decisions WHERE cohort=?
                    GROUP BY decision,reason ORDER BY decision,reason""",
                (COHORT,),
            )]
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,market_bucket,winner,side,observed_ask,status,
                          net_pnl_usdt,net_roi,resolved_at_ms
                     FROM wallet_spot_strike_forward_results WHERE cohort=?
                    ORDER BY resolved_at_ms DESC LIMIT 30""",
                (COHORT,),
            )]
            ordered_results = [dict(item) for item in self.db.execute(
                """SELECT status,net_pnl_usdt
                     FROM wallet_spot_strike_forward_results WHERE cohort=?
                    ORDER BY resolved_at_ms,market_id""",
                (COHORT,),
            )]
        settled = int(row["settled"] or 0)
        wins = int(row["wins"] or 0)
        stake = float(row["stake"] or 0.0)
        pnl = float(row["pnl"] or 0.0)
        equity = peak = max_drawdown = 0.0
        loss_streak = longest_loss_streak = 0
        for result in ordered_results:
            equity += float(result["net_pnl_usdt"] or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if str(result["status"]) == "LOSS":
                loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, loss_streak)
            else:
                loss_streak = 0
        return {
            "deploymentBoundaryMs": self.forward_deployed_at_ms,
            "events": events,
            "decisions": sum(int(item["count"]) for item in decision_rows),
            "decisionBreakdown": decision_rows,
            "settledMarkets": settled,
            "pendingMarkets": max(0, events - settled),
            "wins": wins,
            "losses": settled - wins,
            "winRate": wins / settled if settled else None,
            "netStakeUsdt": stake,
            "netPnlUsdt": pnl,
            "netRoi": pnl / stake if stake else None,
            "maxDrawdownUsdt": max_drawdown,
            "maxDrawdownStakeUnits": max_drawdown / STAKE_USDT,
            "longestLossStreak": longest_loss_streak,
            "completionEligible": bool(settled >= 30 and wins / settled > 0.60 and pnl / stake > 0.10) if stake else False,
            "minimumSettledForCompletion": 30,
            "recentMarkets": recent,
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["spotStrikeForward"] = {
            "cohort": COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "causalStart": True,
            "config": self._forward_config(),
            "currentEvent": self.forward_event,
            "currentDecision": self.forward_decision,
            "lastBlock": self.forward_last_block,
            "performance": self._forward_performance(),
            "promotion": "not in live allowlist; no automatic activation",
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_2Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; retention={v3.RETENTION_DAYS}d; "
        f"spotStrikeForward={COHORT}; paper only; apiKeyConfigured={bool(observer.api_key)}; "
        f"db={base.DB_PATH}",
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
