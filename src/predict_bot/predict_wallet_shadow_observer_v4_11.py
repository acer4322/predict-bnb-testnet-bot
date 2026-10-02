from __future__ import annotations

import json
import sqlite3
import statistics
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_10 as v4_10
from . import predict_wallet_taker_signal_collector as signal_collector
from . import predict_wallet_target_taker_mirror as mirror


VERSION = "PREDICT_WALLET_SHADOW_V0_14_TARGET_TAKER_MIRROR_AUDIT_PAPER"
COHORT = "TARGET_TAKER_MIRROR_AUDIT_V1"


def _side(value: Any, threshold: float = 0.0) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if abs(number) <= threshold:
        return None
    return "UP" if number > 0 else "DOWN"


class WalletShadowObserver(v4_10.WalletShadowObserver):
    """V4.10 plus an isolated post-detection target Taker execution audit."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_10.v4_9.v4_8.v4_7.v4_6.v4_5.v4_4.v4_3.v4_2.SIMULATION_DB_PATH) -> None:
        self.mirror_timers: list[threading.Timer] = []
        self.mirror_deployed_at_ms = 0
        self.mirror_excluded_market_id: int | None = None
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_target_taker_mirror_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_target_taker_mirror_parents (
                    cohort TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    order_hash TEXT,
                    side TEXT NOT NULL,
                    target_event_ms INTEGER NOT NULL,
                    target_last_event_ms INTEGER NOT NULL,
                    detected_at_ms INTEGER NOT NULL,
                    detection_lag_ms INTEGER NOT NULL,
                    target_average_price REAL,
                    target_shares_at_detection REAL NOT NULL,
                    target_latest_shares REAL NOT NULL,
                    target_fill_legs INTEGER NOT NULL,
                    strict_pre_signal_ms INTEGER,
                    strict_pre_signal_lead_ms INTEGER,
                    strict_pre_signal_json TEXT,
                    PRIMARY KEY(cohort,parent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_mirror_parent_market
                    ON wallet_target_taker_mirror_parents(cohort,market_id,target_event_ms);
                CREATE TABLE IF NOT EXISTS wallet_target_taker_mirror_captures (
                    cohort TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    horizon_ms INTEGER NOT NULL,
                    profile TEXT NOT NULL,
                    scheduled_at_ms INTEGER NOT NULL,
                    captured_at_ms INTEGER NOT NULL,
                    actual_delay_ms INTEGER NOT NULL,
                    book_status TEXT NOT NULL,
                    book_update_ms INTEGER,
                    book_age_ms INTEGER,
                    requested_kind TEXT,
                    requested_amount REAL,
                    filled_shares REAL NOT NULL DEFAULT 0,
                    principal_usdt REAL NOT NULL DEFAULT 0,
                    fee_usdt REAL NOT NULL DEFAULT 0,
                    total_cost_usdt REAL NOT NULL DEFAULT 0,
                    vwap REAL,
                    best_ask REAL,
                    worst_ask REAL,
                    levels_consumed INTEGER NOT NULL DEFAULT 0,
                    fully_executable INTEGER NOT NULL DEFAULT 0,
                    minimum_notional_met INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort,parent_id,horizon_ms,profile)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_mirror_capture_market
                    ON wallet_target_taker_mirror_captures(cohort,market_id,horizon_ms,profile);
                """
            )
            now_ms = base._now_ms()
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_target_taker_mirror_meta WHERE cohort=?",
                (COHORT,),
            ).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO wallet_target_taker_mirror_meta VALUES (?,?,?,?)",
                    (COHORT, now_ms, None, json.dumps(mirror.policy(), separators=(",", ":"))),
                )
                self.mirror_deployed_at_ms = now_ms
            else:
                self.mirror_deployed_at_ms = int(row["deployed_at_ms"])
                self.mirror_excluded_market_id = (
                    int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                )
                self.db.execute(
                    "UPDATE wallet_target_taker_mirror_meta SET policy_json=? WHERE cohort=?",
                    (json.dumps(mirror.policy(), separators=(",", ":")), COHORT),
                )
            self.db.commit()

    def stop(self) -> None:
        for timer in self.mirror_timers:
            timer.cancel()
        super().stop()

    @staticmethod
    def _strict_pre_signal(market_id: int, event_ms: int) -> tuple[int | None, int | None, dict[str, Any] | None]:
        path = Path(signal_collector.DB_PATH)
        if not path.exists():
            return None, None, None
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        try:
            db = sqlite3.connect(uri, uri=True, timeout=1.0)
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM wallet_taker_signal_snapshots "
                "WHERE market_id=? AND sampled_at_ms<? ORDER BY sampled_at_ms DESC LIMIT 1",
                (int(market_id), int(event_ms)),
            ).fetchone()
            db.close()
        except sqlite3.Error:
            return None, None, None
        if row is None:
            return None, None, None
        sampled = int(row["sampled_at_ms"])
        lead = int(event_ms) - sampled
        if lead <= 0 or lead > 5_000:
            return sampled, lead, None
        fields = (
            "sampled_at_ms", "seconds_left", "predict_up_bid", "predict_up_ask",
            "predict_down_bid", "predict_down_ask", "spot_minus_strike_bps",
            "spot_queue_imbalance", "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
            "spot_return_250ms_bps", "spot_return_1s_bps", "spot_return_3s_bps",
            "futures_queue_imbalance", "futures_taker_imbalance_250ms",
            "futures_taker_imbalance_1s", "futures_return_250ms_bps",
            "futures_return_1s_bps", "futures_return_3s_bps", "perp_spot_basis_bps",
            "chainlink_minus_strike_bps", "direction_score", "direction_bias", "volatility_alert",
        )
        context = {field: row[field] for field in fields if field in row.keys()}
        return sampled, lead, context

    def _store_capture_status(
        self,
        *,
        parent_id: str,
        market_id: int,
        side: str,
        horizon_ms: int,
        scheduled_at_ms: int,
        captured_at_ms: int,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        rows = []
        for profile in mirror.EXECUTION_PROFILES:
            body = {"profile": profile["profile"], "bookStatus": status, **(payload or {})}
            rows.append((
                COHORT, parent_id, int(market_id), side, int(horizon_ms), profile["profile"],
                int(scheduled_at_ms), int(captured_at_ms),
                int(captured_at_ms - (scheduled_at_ms - horizon_ms)),
                status, json.dumps(body, separators=(",", ":"), default=str),
            ))
        with self.db_lock:
            self.db.executemany(
                """INSERT OR IGNORE INTO wallet_target_taker_mirror_captures(
                       cohort,parent_id,market_id,side,horizon_ms,profile,scheduled_at_ms,captured_at_ms,
                       actual_delay_ms,book_status,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            self.db.commit()

    def _capture_mirror(
        self,
        parent_id: str,
        market_id: int,
        side: str,
        target_shares: float,
        detected_at_ms: int,
        horizon_ms: int,
    ) -> None:
        if self.stop_event.is_set():
            return
        scheduled = int(detected_at_ms + horizon_ms)
        captured = base._now_ms()
        if self.market_id != int(market_id):
            self._store_capture_status(
                parent_id=parent_id, market_id=market_id, side=side, horizon_ms=horizon_ms,
                scheduled_at_ms=scheduled, captured_at_ms=captured, status="MARKET_ROLLOVER",
            )
            return
        try:
            response = self.http.get(f"{base.API_BASE}/v1/markets/{int(market_id)}/orderbook")
            response.raise_for_status()
            orderbook = response.json()
        except Exception as exc:
            self._store_capture_status(
                parent_id=parent_id, market_id=market_id, side=side, horizon_ms=horizon_ms,
                scheduled_at_ms=scheduled, captured_at_ms=base._now_ms(), status="ORDERBOOK_ERROR",
                payload={"error": str(exc)[:300]},
            )
            return
        captured = base._now_ms()
        data = orderbook.get("data") if isinstance(orderbook, dict) else None
        data = data if isinstance(data, dict) else {}
        book_market = int(data.get("marketId") or 0)
        update_ms = int(data.get("updateTimestampMs") or 0)
        age_ms = captured - update_ms if update_ms else None
        if book_market != int(market_id):
            status = "BOOK_MARKET_MISMATCH"
        elif age_ms is None or age_ms < 0 or age_ms > mirror.MAX_BOOK_AGE_MS:
            status = "STALE_ORDERBOOK"
        else:
            status = "OK"
        if status != "OK":
            self._store_capture_status(
                parent_id=parent_id, market_id=market_id, side=side, horizon_ms=horizon_ms,
                scheduled_at_ms=scheduled, captured_at_ms=captured, status=status,
                payload={"bookMarketId": book_market, "bookUpdateMs": update_ms, "bookAgeMs": age_ms},
            )
            return
        captures = mirror.simulate_profiles(orderbook, side, target_shares)
        rows = []
        for fill in captures:
            payload = {
                **fill,
                "targetSharesAtDetection": target_shares,
                "bookUpdateMs": update_ms,
                "bookAgeMs": age_ms,
                "paperOnly": True,
            }
            rows.append((
                COHORT, parent_id, int(market_id), side, int(horizon_ms), fill["profile"],
                scheduled, captured, int(captured - detected_at_ms), status, update_ms, age_ms,
                fill["requestedKind"], fill["requestedAmount"], fill["filledShares"],
                fill["principalUsdt"], fill["feeUsdt"], fill["totalCostUsdt"], fill["vwap"],
                fill["bestAsk"], fill["worstAsk"], fill["levelsConsumed"],
                int(bool(fill["fullyExecutable"])), int(bool(fill["minimumNotionalMet"])),
                json.dumps(payload, separators=(",", ":"), default=str),
            ))
        with self.db_lock:
            self.db.executemany(
                """INSERT OR IGNORE INTO wallet_target_taker_mirror_captures(
                       cohort,parent_id,market_id,side,horizon_ms,profile,scheduled_at_ms,captured_at_ms,
                       actual_delay_ms,book_status,book_update_ms,book_age_ms,requested_kind,requested_amount,
                       filled_shares,principal_usdt,fee_usdt,total_cost_usdt,vwap,best_ask,worst_ask,
                       levels_consumed,fully_executable,minimum_notional_met,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            self.db.commit()

    def _schedule_mirror(
        self,
        parent_id: str,
        market_id: int,
        side: str,
        target_shares: float,
        detected_at_ms: int,
        horizon_ms: int,
    ) -> None:
        due = detected_at_ms + horizon_ms
        delay = max(0.0, (due - base._now_ms()) / 1_000.0)
        if delay == 0:
            self._capture_mirror(parent_id, market_id, side, target_shares, detected_at_ms, horizon_ms)
            return
        timer = threading.Timer(
            delay, self._capture_mirror,
            args=(parent_id, market_id, side, target_shares, detected_at_ms, horizon_ms),
        )
        timer.daemon = True
        self.mirror_timers.append(timer)
        timer.start()

    def _advance_target_taker_mirror(self) -> None:
        if self.market_id is None:
            return
        if self.mirror_excluded_market_id is None:
            self.mirror_excluded_market_id = int(self.market_id)
            with self.db_lock:
                self.db.execute(
                    "UPDATE wallet_target_taker_mirror_meta SET excluded_market_id=? WHERE cohort=?",
                    (self.mirror_excluded_market_id, COHORT),
                )
                self.db.commit()
        if int(self.market_id) == int(self.mirror_excluded_market_id):
            return
        parents = [
            parent for parent in self.parents.values()
            if parent.role == "TAKER" and parent.quote_type == "BID"
            and parent.side in {"UP", "DOWN"}
            and parent.first_event_ms >= self.mirror_deployed_at_ms
        ]
        for parent in parents:
            detected = base._now_ms()
            sampled, lead, context = self._strict_pre_signal(parent.market_id, parent.first_event_ms)
            with self.db_lock:
                existing = self.db.execute(
                    "SELECT detected_at_ms,target_shares_at_detection FROM wallet_target_taker_mirror_parents "
                    "WHERE cohort=? AND parent_id=?",
                    (COHORT, parent.id),
                ).fetchone()
                if existing is None:
                    self.db.execute(
                        """INSERT INTO wallet_target_taker_mirror_parents(
                               cohort,parent_id,market_id,order_hash,side,target_event_ms,target_last_event_ms,
                               detected_at_ms,detection_lag_ms,target_average_price,target_shares_at_detection,
                               target_latest_shares,target_fill_legs,strict_pre_signal_ms,strict_pre_signal_lead_ms,
                               strict_pre_signal_json
                           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            COHORT, parent.id, parent.market_id, parent.order_hash, parent.side,
                            parent.first_event_ms, parent.last_event_ms, detected,
                            detected - parent.first_event_ms, parent.average_price, parent.shares,
                            parent.shares, parent.fill_legs, sampled, lead,
                            json.dumps(context, separators=(",", ":"), default=str) if context else None,
                        ),
                    )
                    is_new = True
                    detected_at = detected
                    shares_at_detection = float(parent.shares)
                else:
                    self.db.execute(
                        """UPDATE wallet_target_taker_mirror_parents
                              SET target_last_event_ms=?,target_average_price=?,target_latest_shares=?,target_fill_legs=?
                            WHERE cohort=? AND parent_id=?""",
                        (parent.last_event_ms, parent.average_price, parent.shares, parent.fill_legs, COHORT, parent.id),
                    )
                    is_new = False
                    detected_at = int(existing["detected_at_ms"])
                    shares_at_detection = float(existing["target_shares_at_detection"])
                self.db.commit()
            if is_new:
                for horizon in mirror.HORIZONS_MS:
                    self._schedule_mirror(
                        parent.id, parent.market_id, parent.side, shares_at_detection, detected_at, horizon
                    )

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_target_taker_mirror()

    def _mirror_signal_evidence(self) -> dict[str, Any]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT side,target_shares_at_detection,strict_pre_signal_json "
                "FROM wallet_target_taker_mirror_parents WHERE cohort=?",
                (COHORT,),
            ).fetchall()
        fields = {
            "directionScore": ("direction_score", 0.05),
            "spotTaker1s": ("spot_taker_imbalance_1s", 0.0),
            "spotReturn1s": ("spot_return_1s_bps", 0.0),
            "futuresQueue": ("futures_queue_imbalance", 0.0),
            "futuresTaker1s": ("futures_taker_imbalance_1s", 0.0),
            "futuresReturn1s": ("futures_return_1s_bps", 0.0),
        }
        result: dict[str, Any] = {}
        contexts = 0
        for label, (field, threshold) in fields.items():
            comparable = matches = 0
            weighted_total = weighted_match = 0.0
            for row in rows:
                try:
                    context = json.loads(row["strict_pre_signal_json"] or "null")
                except json.JSONDecodeError:
                    context = None
                if not isinstance(context, dict):
                    continue
                signal_side = _side(context.get(field), threshold)
                if signal_side is None:
                    continue
                comparable += 1
                weight = float(row["target_shares_at_detection"] or 0.0)
                weighted_total += weight
                if signal_side == row["side"]:
                    matches += 1
                    weighted_match += weight
            result[label] = {
                "comparable": comparable,
                "matchRate": matches / comparable if comparable else None,
                "shareWeightedMatchRate": weighted_match / weighted_total if weighted_total else None,
            }
        for row in rows:
            if row["strict_pre_signal_json"]:
                contexts += 1
        return {
            "parents": len(rows),
            "strictPreSignalContexts": contexts,
            "coverage": contexts / len(rows) if rows else None,
            "fields": result,
            "interpretationBoundary": "association with strict pre-event public signals; not proof that the target directly uses any field",
        }

    def _mirror_performance(self) -> dict[str, Any]:
        with self.db_lock:
            parent_count = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_target_taker_mirror_parents WHERE cohort=?", (COHORT,)
            ).fetchone()[0])
            captures = [dict(row) for row in self.db.execute(
                """SELECT c.*,r.winner,r.resolved_at_ms,p.target_average_price,p.detection_lag_ms
                     FROM wallet_target_taker_mirror_captures c
                     JOIN wallet_target_taker_mirror_parents p
                       ON p.cohort=c.cohort AND p.parent_id=c.parent_id
                     LEFT JOIN wallet_shadow_target_market_results r
                       ON r.wallet=? AND r.market_id=c.market_id
                    WHERE c.cohort=? ORDER BY c.captured_at_ms""",
                (self.wallet, COHORT),
            )]
            recent = [dict(row) for row in self.db.execute(
                """SELECT p.parent_id,p.market_id,p.side,p.target_event_ms,p.detected_at_ms,p.detection_lag_ms,
                          p.target_average_price,p.target_shares_at_detection,p.target_latest_shares,
                          p.strict_pre_signal_lead_ms,
                          c.book_status,c.vwap,c.fully_executable,c.actual_delay_ms
                     FROM wallet_target_taker_mirror_parents p
                     LEFT JOIN wallet_target_taker_mirror_captures c
                       ON c.cohort=p.cohort AND c.parent_id=p.parent_id
                      AND c.horizon_ms=0 AND c.profile='MIN1_USDT'
                    WHERE p.cohort=? ORDER BY p.detected_at_ms DESC LIMIT 20""",
                (COHORT,),
            )]
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in captures:
            groups.setdefault((int(row["horizon_ms"]), str(row["profile"])), []).append(row)
        matrix = []
        for horizon in mirror.HORIZONS_MS:
            for profile in (str(item["profile"]) for item in mirror.EXECUTION_PROFILES):
                rows = groups.get((horizon, profile), [])
                full = [row for row in rows if row["book_status"] == "OK" and int(row["fully_executable"])]
                settled = [row for row in full if row.get("winner") in {"UP", "DOWN"}]
                wins = sum(row["side"] == row["winner"] for row in settled)
                pnl = stress1 = stress2 = cost = 0.0
                slippages = [
                    float(row["vwap"]) - float(row["target_average_price"])
                    for row in full
                    if row.get("target_average_price") is not None and row.get("vwap") is not None
                ]
                for row in settled:
                    fill = {"filledShares": row["filled_shares"]}
                    # Use the captured depth-walk principal rather than reconstructing it from shares.
                    captured_cost = float(row["total_cost_usdt"] or 0.0)
                    shares = float(row["filled_shares"] or 0.0)
                    payout = shares if row["side"] == row["winner"] else 0.0
                    cost += captured_cost
                    pnl += payout - captured_cost
                    stress1 += payout - captured_cost - shares * 0.01 * (1 + mirror.TAKER_FEE_RATE)
                    stress2 += payout - captured_cost - shares * 0.02 * (1 + mirror.TAKER_FEE_RATE)
                delays = [int(row["actual_delay_ms"]) for row in rows]
                end_to_end = [
                    int(row["detection_lag_ms"]) + int(row["actual_delay_ms"])
                    for row in rows
                ]
                matrix.append({
                    "horizonMs": horizon,
                    "profile": profile,
                    "captures": len(rows),
                    "fullyExecutable": len(full),
                    "executionRate": len(full) / len(rows) if rows else None,
                    "settled": len(settled),
                    "wins": wins,
                    "winRate": wins / len(settled) if settled else None,
                    "costUsdt": cost,
                    "pnlUsdt": pnl,
                    "roi": pnl / cost if cost else None,
                    "stress1TickRoi": stress1 / cost if cost else None,
                    "stress2TickRoi": stress2 / cost if cost else None,
                    "medianActualDelayMs": statistics.median(delays) if delays else None,
                    "medianEndToEndLatencyMs": statistics.median(end_to_end) if end_to_end else None,
                    "medianPriceSlippageVsTarget": statistics.median(slippages) if slippages else None,
                })
        lags = [int(row["detection_lag_ms"]) for row in captures if int(row["horizon_ms"]) == 0 and row["profile"] == "MIN1_USDT"]
        return {
            "targetParents": parent_count,
            "captureRows": len(captures),
            "medianDetectionLagMs": statistics.median(lags) if lags else None,
            "matrix": matrix,
            "recentParents": recent,
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["targetTakerMirrorAudit"] = {
            "cohort": COHORT,
            "status": (
                "WAITING_NEXT_COMPLETE_MARKET"
                if self.market_id is None or self.mirror_excluded_market_id is None
                or int(self.market_id) == int(self.mirror_excluded_market_id)
                else "ACTIVE"
            ),
            "deploymentBoundaryMs": self.mirror_deployed_at_ms,
            "excludedDeploymentMarketId": self.mirror_excluded_market_id,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "liveOrdersAffected": False,
            "policy": mirror.policy(),
            "performance": self._mirror_performance(),
            "signalEvidence": self._mirror_signal_evidence(),
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        return payload


class _Handler(v4_10._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_11Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "target Taker mirror execution audit active; paperOnly=true; liveOrdersAffected=false",
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
