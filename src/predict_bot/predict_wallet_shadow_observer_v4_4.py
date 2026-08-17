from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v3 as v3
from . import predict_wallet_shadow_observer_v4_3 as v4_3
from . import predict_wallet_taker_signal_strategy as signal_strategy


VERSION = "PREDICT_WALLET_SHADOW_V0_6_TAKER_SIGNAL_CONSENSUS"
SIGNAL_STATE_URL = "http://127.0.0.1:8777/state"
RETIRED_COHORTS = (
    "TAKER_V1",
    "CAPITAL_S1",
    "CAPITAL_S1_CAP100_STRESS",
    "MIN1_EXEC_CAP100",
    "MIN1_WALLET_GROWTH",
    "MIN1_WALLET_GROWTH_TIME20",
    "MIN1_BATCHED_MAKER_TAKER_RESERVE",
)


class WalletShadowObserver(v4_3.WalletShadowObserver):
    """Forward-only paper Taker driven solely by public pre-trade market signals."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_3.v4_2.SIMULATION_DB_PATH) -> None:
        if not hasattr(self, "signal_strategy_enabled"):
            self.signal_strategy_enabled = True
        self.signal_active_market = False
        self.signal_events: list[dict[str, Any]] = []
        self.signal_last_processed_ns: int | None = None
        self.signal_last_trade_ms: int | None = None
        self.signal_last_trade_side: str | None = None
        self.signal_last_decision: dict[str, Any] | None = None
        self.latest_public_signal_snapshot: dict[str, Any] | None = None
        self.signal_collector_status: dict[str, Any] = {"status": "WAITING", "error": None}
        self.signal_schema_ready = False
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_v2_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_shadow_retired_cohorts (
                    cohort TEXT PRIMARY KEY,
                    retired_at_ms INTEGER NOT NULL,
                    historical_audit_preserved INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_v2_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_v2_decisions (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    signal_at_ms INTEGER,
                    decision_at_ms INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    up_votes INTEGER NOT NULL,
                    down_votes INTEGER NOT NULL,
                    sample_age_ms REAL,
                    seconds_left REAL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort,market_id,snapshot_timestamp_ns)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_taker_signal_v2_decisions_time
                    ON wallet_taker_signal_v2_decisions(cohort,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_v2_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    signal_at_ms INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    principal_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL,
                    total_cost_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    up_votes INTEGER NOT NULL,
                    down_votes INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_taker_signal_v2_events_market
                    ON wallet_taker_signal_v2_events(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_v2_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    principal_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL,
                    total_cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    stress_1tick_pnl_usdt REAL NOT NULL,
                    stress_1tick_roi REAL,
                    stress_2tick_pnl_usdt REAL NOT NULL,
                    stress_2tick_roi REAL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_taker_signal_v2_results_time
                    ON wallet_taker_signal_v2_results(cohort,resolved_at_ms);
                """
            )
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_taker_signal_v2_meta WHERE cohort=?",
                (signal_strategy.COHORT,),
            ).fetchone()
            if row is None:
                self.signal_deployed_at_ms = base._now_ms()
                self.signal_excluded_market_id: int | None = None
                self.db.execute(
                    "INSERT INTO wallet_taker_signal_v2_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                    (signal_strategy.COHORT, self.signal_deployed_at_ms, None, json.dumps(self._signal_config(), separators=(",", ":"))),
                )
            else:
                self.signal_deployed_at_ms = int(row["deployed_at_ms"])
                self.signal_excluded_market_id = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
            retired_now_ms = base._now_ms()
            for cohort in RETIRED_COHORTS:
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_shadow_retired_cohorts(cohort,retired_at_ms,historical_audit_preserved) VALUES (?,?,1)",
                    (cohort, retired_now_ms),
                )
            self.legacy_retired_at_ms = min(
                int(row[0]) for row in self.db.execute(
                    "SELECT retired_at_ms FROM wallet_shadow_retired_cohorts WHERE cohort IN ({})".format(
                        ",".join("?" for _ in RETIRED_COHORTS)
                    ),
                    RETIRED_COHORTS,
                )
            )
            self.db.commit()
        self.signal_schema_ready = True
        self._seed_signal_pending_settlements()

    @staticmethod
    def _signal_config() -> dict[str, Any]:
        return {
            "features": list(signal_strategy.FEATURES),
            "quorum": signal_strategy.QUORUM,
            "stakeUsdt": signal_strategy.STAKE_USDT,
            "takerFeeBps": signal_strategy.TAKER_FEE_RATE * 10_000,
            "repeatCooldownMs": signal_strategy.REPEAT_COOLDOWN_MS,
            "flipCooldownMs": signal_strategy.FLIP_COOLDOWN_MS,
            "maxSampleAgeMs": signal_strategy.MAX_SAMPLE_AGE_MS,
            "maxPredictAgeMs": signal_strategy.MAX_PREDICT_AGE_MS,
            "maxAsk": signal_strategy.MAX_ASK,
            "minimumSecondsLeft": signal_strategy.MIN_SECONDS_LEFT,
            "sizing": "fixed principal so direction/timing can be evaluated without confidence-size confounding",
        }

    def _register_v1_market(self, market_id: int) -> None:
        # V1 is retained in SQLite for audit only. This runtime must never register
        # a new V1 market even if a parent-module environment default changes.
        return

    def _advance_taker_v1(self, new_common_events: list[base.ShadowEvent], book: dict[str, Any], core: dict[str, Any]) -> None:
        return

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        self.signal_events = []
        self.signal_last_processed_ns = None
        self.signal_last_trade_ms = None
        self.signal_last_trade_side = None
        self.signal_last_decision = None
        if not self.signal_strategy_enabled:
            self.signal_active_market = False
            return
        with self.db_lock:
            if self.signal_excluded_market_id is None:
                self.signal_excluded_market_id = int(market_id)
                self.db.execute(
                    "UPDATE wallet_taker_signal_v2_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), signal_strategy.COHORT),
                )
            registered = self.db.execute(
                "SELECT 1 FROM wallet_taker_signal_v2_markets WHERE cohort=? AND market_id=?",
                (signal_strategy.COHORT, int(market_id)),
            ).fetchone()
            self.signal_active_market = bool(registered) or int(market_id) != self.signal_excluded_market_id
            if self.signal_active_market:
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_taker_signal_v2_markets(cohort,market_id,title,started_at_ms) VALUES (?,?,?,?)",
                    (signal_strategy.COHORT, int(market_id), title, base._now_ms()),
                )
                stored_events = [dict(row) for row in self.db.execute(
                    "SELECT * FROM wallet_taker_signal_v2_events WHERE cohort=? AND market_id=? ORDER BY decision_at_ms,id",
                    (signal_strategy.COHORT, int(market_id)),
                )]
                self.signal_events = [
                    json.loads(row["payload_json"]) if row.get("payload_json") else row
                    for row in stored_events
                ]
                if self.signal_events:
                    latest = self.signal_events[-1]
                    self.signal_last_trade_ms = int(latest["decision_at_ms"])
                    self.signal_last_trade_side = str(latest["side"])
                decision = self.db.execute(
                    "SELECT snapshot_timestamp_ns FROM wallet_taker_signal_v2_decisions WHERE cohort=? AND market_id=? ORDER BY snapshot_timestamp_ns DESC LIMIT 1",
                    (signal_strategy.COHORT, int(market_id)),
                ).fetchone()
                if decision is not None:
                    self.signal_last_processed_ns = int(decision[0])
            self.db.commit()

    def _latest_signal(self) -> dict[str, Any] | None:
        try:
            response = self.http.get(SIGNAL_STATE_URL, timeout=1.0)
            response.raise_for_status()
            payload = response.json()
            state = payload.get("state") if isinstance(payload, dict) else None
            state = state if isinstance(state, dict) else payload if isinstance(payload, dict) else {}
            self.signal_collector_status = {
                "status": state.get("status"),
                "version": state.get("version"),
                "sampleAgeMs": state.get("sampleAgeMs"),
                "error": state.get("error"),
            }
            latest = state.get("latest")
            self.latest_public_signal_snapshot = dict(latest) if isinstance(latest, dict) else None
            return self.latest_public_signal_snapshot
        except Exception as exc:
            self.signal_collector_status = {"status": "DEGRADED", "error": str(exc)[:300]}
            return None

    def _advance_signal_strategy(self) -> None:
        if not self.signal_strategy_enabled:
            return
        if not self.signal_active_market or self.market_id is None:
            return
        snapshot = self._latest_signal()
        if snapshot is None:
            return
        timestamp_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if timestamp_ns <= 0 or timestamp_ns == self.signal_last_processed_ns:
            return
        now_ms = base._now_ms()
        decision = signal_strategy.decide(
            snapshot,
            expected_market_id=int(self.market_id),
            now_ms=now_ms,
            last_trade_ms=self.signal_last_trade_ms,
            last_trade_side=self.signal_last_trade_side,
        )
        decision_payload = {
            **decision,
            "marketId": self.market_id,
            "snapshotTimestampNs": timestamp_ns,
            "paperOnly": True,
            "targetEventsUsed": False,
            "features": {feature: snapshot.get(feature) for feature in signal_strategy.FEATURES},
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_taker_signal_v2_decisions(
                       cohort,market_id,snapshot_timestamp_ns,signal_at_ms,decision_at_ms,decision,
                       reason,side,up_votes,down_votes,sample_age_ms,seconds_left,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_strategy.COHORT, self.market_id, timestamp_ns, decision.get("signalAtMs"),
                    now_ms, decision["decision"], decision["reason"], decision.get("side"),
                    decision["upVotes"], decision["downVotes"], decision.get("sampleAgeMs"),
                    decision.get("secondsLeft"), json.dumps(decision_payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        self.signal_last_processed_ns = timestamp_ns
        self.signal_last_decision = decision_payload
        if decision["decision"] != "TRADE" or decision.get("side") not in {"UP", "DOWN"}:
            return
        side = str(decision["side"])
        fill = signal_strategy.execution(side, snapshot)
        if fill is None:
            return
        event = {
            "id": f"{signal_strategy.COHORT}:{self.market_id}:{timestamp_ns}",
            "cohort": signal_strategy.COHORT,
            "marketId": self.market_id,
            "signalAtMs": decision["signalAtMs"],
            "decisionAtMs": now_ms,
            "side": side,
            "upVotes": decision["upVotes"],
            "downVotes": decision["downVotes"],
            **fill,
            "paperOnly": True,
            "targetEventsUsed": False,
            "signal": decision_payload["features"],
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_taker_signal_v2_events(
                       id,cohort,market_id,snapshot_timestamp_ns,signal_at_ms,decision_at_ms,side,
                       observed_ask,principal_usdt,fee_usdt,total_cost_usdt,shares,up_votes,down_votes,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event["id"], signal_strategy.COHORT, self.market_id, timestamp_ns,
                    int(decision["signalAtMs"]), now_ms, side, fill["ask"], fill["principalUsdt"],
                    fill["feeUsdt"], fill["totalCostUsdt"], fill["shares"], decision["upVotes"],
                    decision["downVotes"], json.dumps(event, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        self.signal_events.append(event)
        self.signal_events = self.signal_events[-120:]
        self.signal_last_trade_ms = now_ms
        self.signal_last_trade_side = side

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_signal_strategy()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if not self.signal_schema_ready:
            return
        self._seed_signal_pending_settlements()

    def _seed_signal_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT m.market_id FROM wallet_taker_signal_v2_markets m
                     LEFT JOIN wallet_taker_signal_v2_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (signal_strategy.COHORT, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_taker_signal_v2_markets WHERE cohort=? AND market_id=?",
                (signal_strategy.COHORT, int(market_id)),
            ).fetchone()
            if registered is None:
                return
            events = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_taker_signal_v2_events WHERE cohort=? AND market_id=? ORDER BY decision_at_ms,id",
                (signal_strategy.COHORT, int(market_id)),
            )]
        principal = sum(float(event["principal_usdt"]) for event in events)
        fees = sum(float(event["fee_usdt"]) for event in events)
        total_cost = principal + fees
        payout = sum(float(event["shares"]) for event in events if event["side"] == winner)
        pnl = payout - total_cost
        stress_pnls = []
        for ticks in (1, 2):
            stressed_payout = sum(
                float(event["principal_usdt"]) / min(0.99, float(event["observed_ask"]) + ticks * 0.01)
                for event in events if event["side"] == winner
            )
            stress_pnls.append(stressed_payout - total_cost)
        status = "NO_TRADE" if not events else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
        title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_taker_signal_v2_results(
                       cohort,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       principal_usdt,fee_usdt,total_cost_usdt,payout_usdt,net_pnl_usdt,net_roi,
                       stress_1tick_pnl_usdt,stress_1tick_roi,stress_2tick_pnl_usdt,stress_2tick_roi
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                       principal_usdt=excluded.principal_usdt,fee_usdt=excluded.fee_usdt,
                       total_cost_usdt=excluded.total_cost_usdt,payout_usdt=excluded.payout_usdt,
                       net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                       stress_1tick_pnl_usdt=excluded.stress_1tick_pnl_usdt,
                       stress_1tick_roi=excluded.stress_1tick_roi,
                       stress_2tick_pnl_usdt=excluded.stress_2tick_pnl_usdt,
                       stress_2tick_roi=excluded.stress_2tick_roi""",
                (
                    signal_strategy.COHORT, int(market_id), title, winner, base._now_ms(),
                    1 if events else 0, status, len(events), principal, fees, total_cost, payout, pnl,
                    pnl / total_cost if total_cost else None, stress_pnls[0],
                    stress_pnls[0] / total_cost if total_cost else None, stress_pnls[1],
                    stress_pnls[1] / total_cost if total_cost else None,
                ),
            )
            self.db.commit()

    def _signal_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """SELECT COUNT(*) settled,SUM(traded) traded,
                          SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END) wins,
                          SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END) losses,
                          COALESCE(SUM(fill_count),0) fills,COALESCE(SUM(total_cost_usdt),0) cost,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(stress_1tick_pnl_usdt),0) stress1_pnl,
                          COALESCE(SUM(stress_2tick_pnl_usdt),0) stress2_pnl
                     FROM wallet_taker_signal_v2_results WHERE cohort=? AND resolved_at_ms>=?""",
                (signal_strategy.COHORT, cutoff),
            ).fetchone()
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_taker_signal_v2_markets WHERE cohort=? AND started_at_ms>=?",
                (signal_strategy.COHORT, cutoff),
            ).fetchone()[0])
            decisions = [dict(row) for row in self.db.execute(
                """SELECT decision,reason,COUNT(*) count FROM wallet_taker_signal_v2_decisions
                    WHERE cohort=? AND decision_at_ms>=? GROUP BY decision,reason ORDER BY decision,reason""",
                (signal_strategy.COHORT, cutoff),
            )]
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,winner,status,fill_count,total_cost_usdt,net_pnl_usdt,net_roi,
                          stress_1tick_roi,stress_2tick_roi,resolved_at_ms
                     FROM wallet_taker_signal_v2_results WHERE cohort=? AND resolved_at_ms>=?
                    ORDER BY resolved_at_ms DESC LIMIT 30""",
                (signal_strategy.COHORT, cutoff),
            )]
        data = dict(summary) if summary is not None else {}
        traded = int(data.get("traded") or 0)
        wins = int(data.get("wins") or 0)
        cost = float(data.get("cost") or 0.0)
        return {
            "deploymentBoundaryMs": self.signal_deployed_at_ms,
            "excludedDeploymentMarketId": self.signal_excluded_market_id,
            "markets": markets,
            "settledMarkets": int(data.get("settled") or 0),
            "pendingMarkets": max(0, markets - int(data.get("settled") or 0)),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(data.get("losses") or 0),
            "winRate": wins / traded if traded else None,
            "fills": int(data.get("fills") or 0),
            "decisions": sum(int(row["count"]) for row in decisions),
            "decisionBreakdown": decisions,
            "totalCostUsdt": cost,
            "netPnlUsdt": float(data.get("pnl") or 0.0),
            "netRoi": float(data.get("pnl") or 0.0) / cost if cost else None,
            "stress1TickRoi": float(data.get("stress1_pnl") or 0.0) / cost if cost else None,
            "stress2TickRoi": float(data.get("stress2_pnl") or 0.0) / cost if cost else None,
            "recentMarkets": recent,
        }

    def _signal_target_similarity(self) -> dict[str, Any]:
        cutoff = max(self.signal_deployed_at_ms, base._now_ms() - self.retention_ms)
        with self.db_lock:
            signal_events = [dict(row) for row in self.db.execute(
                """SELECT market_id,decision_at_ms,side FROM wallet_taker_signal_v2_events
                    WHERE cohort=? AND decision_at_ms>=? ORDER BY market_id,decision_at_ms""",
                (signal_strategy.COHORT, cutoff),
            )]
            target_events = [dict(row) for row in self.db.execute(
                """SELECT market_id,event_ms,side,order_hash FROM wallet_shadow_target_events
                    WHERE wallet=? AND role='TAKER' AND quote_type='BID' AND event_ms>=?
                    GROUP BY market_id,event_ms,side,order_hash ORDER BY market_id,event_ms""",
                (self.wallet, cutoff),
            )]
        target_by_market: dict[int, list[dict[str, Any]]] = {}
        for event in target_events:
            target_by_market.setdefault(int(event["market_id"]), []).append(event)
        within_2s = within_5s = side_matches_5s = 0
        absolute_lags: list[int] = []
        for event in signal_events:
            candidates = target_by_market.get(int(event["market_id"]), [])
            if not candidates:
                continue
            nearest = min(candidates, key=lambda item: abs(int(item["event_ms"]) - int(event["decision_at_ms"])))
            lag = abs(int(nearest["event_ms"]) - int(event["decision_at_ms"]))
            if lag <= 5_000:
                within_5s += 1
                absolute_lags.append(lag)
                side_matches_5s += int(str(nearest["side"]) == str(event["side"]))
            if lag <= 2_000:
                within_2s += 1
        absolute_lags.sort()
        return {
            "strategyEvents": len(signal_events),
            "targetParents": len(target_events),
            "strategyToTargetCountRatio": len(signal_events) / len(target_events) if target_events else None,
            "nearestTargetWithin2s": within_2s,
            "nearestTargetWithin2sRate": within_2s / len(signal_events) if signal_events else None,
            "nearestTargetWithin5s": within_5s,
            "nearestTargetWithin5sRate": within_5s / len(signal_events) if signal_events else None,
            "sideMatchesWithin5s": side_matches_5s,
            "sideMatchWithin5sRate": side_matches_5s / within_5s if within_5s else None,
            "medianAbsoluteLagMsWithin5s": absolute_lags[len(absolute_lags) // 2] if absolute_lags else None,
            "note": "post-trade diagnostic only; target events never drive strategy decisions",
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.signal_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute(
                "DELETE FROM wallet_taker_signal_v2_decisions WHERE cohort=? AND decision_at_ms<?",
                (signal_strategy.COHORT, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_taker_signal_v2_events WHERE cohort=? AND decision_at_ms<?",
                (signal_strategy.COHORT, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_taker_signal_v2_results WHERE cohort=? AND resolved_at_ms<?",
                (signal_strategy.COHORT, cutoff),
            )
            self.db.execute(
                """DELETE FROM wallet_taker_signal_v2_markets WHERE cohort=? AND started_at_ms<?
                    AND market_id NOT IN (SELECT market_id FROM wallet_taker_signal_v2_events WHERE cohort=?)""",
                (signal_strategy.COHORT, cutoff, signal_strategy.COHORT),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        for key in (
            "takerV1", "capitalS1", "capitalS1Cap100Stress", "min1ExecCap100",
            "min1WalletGrowth", "min1WalletGrowthTime20", "min1BatchedMakerTakerReserve",
        ):
            payload.pop(key, None)
        payload["retiredCohorts"] = {
            "status": "RETIRED_NO_NEW_EVENTS",
            "cohorts": list(RETIRED_COHORTS),
            "retiredAtMs": self.legacy_retired_at_ms,
            "historicalAuditPreserved": True,
        }
        payload["takerSignalConsensusV2"] = {
            "cohort": signal_strategy.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "liveOrdersAffected": False,
            "targetEventsDriveStrategy": False,
            "status": "RETIRED_NO_NEW_EVENTS" if not self.signal_strategy_enabled else "ACTIVE" if self.signal_active_market else "WAITING_NEXT_COMPLETE_MARKET",
            "config": self._signal_config(),
            "collector": dict(self.signal_collector_status),
            "current": {
                "marketId": self.market_id,
                "active": self.signal_active_market,
                "lastDecision": self.signal_last_decision,
                "lastTradeSide": self.signal_last_trade_side,
                "events": list(reversed(self.signal_events[-40:])),
            },
            "performance": self._signal_performance(),
            "targetSimilarity": self._signal_target_similarity(),
            "promotion": "isolated forward paper cohort; never routes orders and is absent from the live allowlist",
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_4Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"cohort={signal_strategy.COHORT}; legacy experimental cohorts retired; paper only; "
        f"liveOrdersAffected=false; db={base.DB_PATH}",
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
