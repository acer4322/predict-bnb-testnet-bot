from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_18 as v4_18
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side


VERSION = "PREDICT_WALLET_SHADOW_V0_24_TARGET_TAKER_PUBLIC_SIDE_V1"


class WalletShadowObserver(v4_18.WalletShadowObserver):
    """V4.18 plus two isolated public-side Target Taker forward paper cohorts.

    SIDE_ONLY asks whether the newly discovered public direction edge has forward
    PnL value without imitating Target timing. HAZARD_SIDE uses the exact same
    side rule but requires a five-second eligibility opened by Lifecycle V3's own
    paper Maker fill plus the simple time/price hazard gate. Target-wallet events
    are never runtime inputs for either cohort.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self.public_side_schema_ready = False
        self.public_side_sequence = 0
        self.public_side_states: dict[str, dict[str, Any]] = {}
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_target_taker_public_side_v1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_target_taker_public_side_v1_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_target_taker_public_side_v1_decisions (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    side_score REAL,
                    side_confidence REAL,
                    available_features INTEGER,
                    hazard_score REAL,
                    hazard_reason TEXT,
                    seconds_left REAL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_public_side_v1_decisions
                    ON wallet_target_taker_public_side_v1_decisions(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_target_taker_public_side_v1_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    effective_unit_cost REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    side_score REAL NOT NULL,
                    hazard_score REAL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_public_side_v1_events
                    ON wallet_target_taker_public_side_v1_events(cohort,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_target_taker_public_side_v1_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    side TEXT,
                    observed_ask REAL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_target_taker_public_side_v1_results
                    ON wallet_target_taker_public_side_v1_results(cohort,resolved_at_ms);
                """
            )
            now_ms = base._now_ms()
            policy_json = json.dumps(public_side.policy(), separators=(",", ":"), default=str)
            deployment: dict[str, tuple[int, int | None]] = {}
            for cohort in public_side.COHORTS:
                row = self.db.execute(
                    "SELECT deployed_at_ms,excluded_market_id FROM wallet_target_taker_public_side_v1_meta WHERE cohort=?",
                    (cohort,),
                ).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO wallet_target_taker_public_side_v1_meta VALUES (?,?,?,?)",
                        (cohort, now_ms, None, policy_json),
                    )
                    deployment[cohort] = (now_ms, None)
                else:
                    deployed = int(row["deployed_at_ms"])
                    excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                    self.db.execute(
                        "UPDATE wallet_target_taker_public_side_v1_meta SET policy_json=? WHERE cohort=?",
                        (policy_json, cohort),
                    )
                    deployment[cohort] = (deployed, excluded)
            self.db.commit()
        self.public_side_states = {
            cohort: self._empty_public_side_state(*deployment[cohort]) for cohort in public_side.COHORTS
        }
        self.public_side_schema_ready = True
        self._seed_public_side_pending_settlements()

    @staticmethod
    def _empty_public_side_state(deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
        return {
            "active": False,
            "deployedAtMs": int(deployed_at_ms),
            "excludedMarketId": excluded_market_id,
            "event": None,
            "lastDecision": None,
            "lastDecisionKey": None,
            "eligibility": None,
        }

    def _restore_public_side_market(self, cohort: str, market_id: int, title: str | None, now_ms: int) -> None:
        state = self.public_side_states[cohort]
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_target_taker_public_side_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), cohort),
                )
            registered = self.db.execute(
                "SELECT 1 FROM wallet_target_taker_public_side_v1_markets WHERE cohort=? AND market_id=?",
                (cohort, int(market_id)),
            ).fetchone()
            clean = self._empty_public_side_state(state["deployedAtMs"], state["excludedMarketId"])
            clean["active"] = bool(registered) or int(market_id) != int(state["excludedMarketId"])
            state.clear()
            state.update(clean)
            if not state["active"]:
                self.db.commit()
                return
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_target_taker_public_side_v1_markets(cohort,market_id,title,started_at_ms) VALUES (?,?,?,?)",
                (cohort, int(market_id), title, now_ms),
            )
            event = self.db.execute(
                "SELECT * FROM wallet_target_taker_public_side_v1_events WHERE cohort=? AND market_id=?",
                (cohort, int(market_id)),
            ).fetchone()
            if event is not None:
                state["event"] = dict(event)
            decision = self.db.execute(
                """SELECT payload_json,decision,reason,side FROM wallet_target_taker_public_side_v1_decisions
                    WHERE cohort=? AND market_id=? ORDER BY decision_at_ms DESC,id DESC LIMIT 1""",
                (cohort, int(market_id)),
            ).fetchone()
            if decision is not None:
                try:
                    state["lastDecision"] = json.loads(decision["payload_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    state["lastDecision"] = None
                state["lastDecisionKey"] = (
                    str(decision["decision"]), str(decision["reason"]), str(decision["side"] or "")
                )
            self.db.commit()
        self.pending_settlement_ids.add(int(market_id))

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        if not self.public_side_schema_ready:
            return
        now_ms = base._now_ms()
        for cohort in public_side.COHORTS:
            self._restore_public_side_market(cohort, int(market_id), title, now_ms)

    def _open_state_taker_eligibility(
        self,
        *,
        snapshot_ns: int,
        now_ms: int,
        up_filled_shares: float,
        down_filled_shares: float,
    ) -> None:
        # Preserve V4's own state-Taker experiment, then mirror only the causal
        # eligibility boundary into the new HAZARD_SIDE A/B cohort.
        super()._open_state_taker_eligibility(
            snapshot_ns=snapshot_ns,
            now_ms=now_ms,
            up_filled_shares=up_filled_shares,
            down_filled_shares=down_filled_shares,
        )
        if not self.public_side_schema_ready:
            return
        state = self.public_side_states.get(public_side.HAZARD_SIDE_COHORT)
        if not isinstance(state, dict) or not state.get("active") or state.get("event") is not None:
            return
        state["eligibility"] = {
            "openedAtMs": int(now_ms),
            "expiresAtMs": int(now_ms + 5_000),
            "openedSnapshotNs": int(snapshot_ns),
            "sourceMakerUpFilledShares": float(up_filled_shares),
            "sourceMakerDownFilledShares": float(down_filled_shares),
            "source": "LIFECYCLE_V3_OWN_PAPER_MAKER_FILL",
        }

    def _record_public_side_decision(
        self,
        cohort: str,
        decision: dict[str, Any],
        hazard: dict[str, Any] | None,
        *,
        snapshot_ns: int,
        now_ms: int,
        force: bool = False,
    ) -> None:
        state = self.public_side_states[cohort]
        key = (str(decision["decision"]), str(decision["reason"]), str(decision.get("side") or ""))
        if not force and key == state.get("lastDecisionKey"):
            state["lastDecision"] = decision
            return
        self.public_side_sequence += 1
        decision_id = f"{cohort}:{self.market_id}:DECISION:{snapshot_ns}:{self.public_side_sequence}"
        signal = decision.get("signal") if isinstance(decision.get("signal"), dict) else {}
        payload = {
            **decision,
            "cohort": cohort,
            "marketId": self.market_id,
            "snapshotTimestampNs": int(snapshot_ns),
            "hazard": hazard,
            "paperOnly": True,
            "targetEventsUsed": False,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_target_taker_public_side_v1_decisions(
                       id,cohort,market_id,snapshot_timestamp_ns,decision_at_ms,decision,reason,side,
                       side_score,side_confidence,available_features,hazard_score,hazard_reason,seconds_left,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id, cohort, int(self.market_id), int(snapshot_ns), int(now_ms),
                    decision["decision"], decision["reason"], decision.get("side"),
                    signal.get("score"), signal.get("confidence"), signal.get("availableFeatures"),
                    hazard.get("eligibilityScore") if isinstance(hazard, dict) else None,
                    hazard.get("reason") if isinstance(hazard, dict) else None,
                    decision.get("secondsLeft"), json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        state["lastDecision"] = payload
        state["lastDecisionKey"] = key

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
        if state.get("event") is not None:
            return
        fill = public_side.execution(decision)
        if fill is None:
            return
        signal = decision.get("signal") if isinstance(decision.get("signal"), dict) else {}
        self.public_side_sequence += 1
        event_id = f"{cohort}:{self.market_id}:TAKER:{snapshot_ns}:{self.public_side_sequence}"
        payload = {
            "id": event_id,
            "cohort": cohort,
            "marketId": self.market_id,
            "snapshotTimestampNs": int(snapshot_ns),
            "decisionAtMs": int(now_ms),
            "side": decision["side"],
            **fill,
            "sideScore": signal.get("score"),
            "sideSignal": signal,
            "hazard": hazard,
            "paperOnly": True,
            "targetEventsUsed": False,
            "oneEntryPerMarket": True,
        }
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_target_taker_public_side_v1_events(
                       id,cohort,market_id,snapshot_timestamp_ns,decision_at_ms,side,observed_ask,
                       effective_unit_cost,stake_usdt,shares,side_score,hazard_score,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, cohort, int(self.market_id), int(snapshot_ns), int(now_ms), decision["side"],
                    fill["ask"], fill["effectiveUnitCost"], fill["stakeUsdt"], fill["shares"],
                    float(signal.get("score") or 0.0),
                    hazard.get("eligibilityScore") if isinstance(hazard, dict) else None,
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        if cursor.rowcount > 0:
            state["event"] = payload
            if cohort == public_side.HAZARD_SIDE_COHORT:
                state["eligibility"] = None
            self.pending_settlement_ids.add(int(self.market_id))

    def _advance_public_side_cohort(
        self,
        cohort: str,
        snapshot: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = self.public_side_states[cohort]
        if not state.get("active") or state.get("event") is not None or self.market_id is None:
            return
        decision = public_side.decide_side(snapshot, expected_market_id=int(self.market_id), now_ms=now_ms)
        hazard: dict[str, Any] | None = None
        if cohort == public_side.HAZARD_SIDE_COHORT:
            eligibility = state.get("eligibility")
            hazard = public_side.hazard_gate(
                snapshot,
                decision.get("side"),
                eligibility if isinstance(eligibility, dict) else None,
                snapshot_ns=int(snapshot_ns),
                now_ms=int(now_ms),
            )
            if not hazard["eligible"]:
                decision = {**decision, "decision": "SKIP", "reason": str(hazard["reason"])}
            elif decision["decision"] == "TRADE":
                decision = {**decision, "reason": "PUBLIC_SIDE_PLUS_HAZARD_MATCH"}
        self._record_public_side_decision(
            cohort, decision, hazard, snapshot_ns=snapshot_ns, now_ms=now_ms,
            force=decision["decision"] == "TRADE",
        )
        if decision["decision"] == "TRADE":
            self._execute_public_side(
                cohort, decision, hazard, snapshot_ns=snapshot_ns, now_ms=now_ms
            )

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        if not self.public_side_schema_ready or self.market_id is None:
            return
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            return
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0:
            return
        now_ms = base._now_ms()
        for cohort in public_side.COHORTS:
            self._advance_public_side_cohort(
                cohort, snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms
            )

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.public_side_schema_ready:
            self._seed_public_side_pending_settlements()

    def _seed_public_side_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_target_taker_public_side_v1_markets m
                     LEFT JOIN wallet_target_taker_public_side_v1_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.public_side_schema_ready:
            return
        resolved_at_ms = base._now_ms()
        for cohort in public_side.COHORTS:
            with self.db_lock:
                registered = self.db.execute(
                    "SELECT title FROM wallet_target_taker_public_side_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                if registered is None:
                    continue
                event = self.db.execute(
                    "SELECT * FROM wallet_target_taker_public_side_v1_events WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
                if event is None:
                    traded = 0
                    status = "NO_TRADE"
                    side = None
                    observed_ask = None
                    stake = shares = payout = pnl = 0.0
                    roi = None
                else:
                    traded = 1
                    side = str(event["side"])
                    observed_ask = float(event["observed_ask"])
                    stake = float(event["stake_usdt"])
                    shares = float(event["shares"])
                    payout = shares if side == winner else 0.0
                    pnl = payout - stake
                    roi = pnl / stake if stake else None
                    status = "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
                self.db.execute(
                    """INSERT INTO wallet_target_taker_public_side_v1_results(
                           cohort,market_id,title,winner,resolved_at_ms,traded,status,side,observed_ask,
                           stake_usdt,shares,payout_usdt,net_pnl_usdt,net_roi
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,market_id) DO UPDATE SET
                           title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                           traded=excluded.traded,status=excluded.status,side=excluded.side,
                           observed_ask=excluded.observed_ask,stake_usdt=excluded.stake_usdt,
                           shares=excluded.shares,payout_usdt=excluded.payout_usdt,
                           net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi""",
                    (
                        cohort, int(market_id), title, winner, resolved_at_ms, traded, status, side,
                        observed_ask, stake, shares, payout, pnl, roi,
                    ),
                )
                self.db.commit()

    def _public_side_performance(self, cohort: str) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END),0) losses,
                          COALESCE(SUM(stake_usdt),0) stake,COALESCE(SUM(net_pnl_usdt),0) pnl
                     FROM wallet_target_taker_public_side_v1_results
                    WHERE cohort=? AND resolved_at_ms>=?""",
                (cohort, cutoff),
            ).fetchone())
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_target_taker_public_side_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (cohort, cutoff),
            ).fetchone()[0])
            ordered = [dict(item) for item in self.db.execute(
                """SELECT status,net_pnl_usdt FROM wallet_target_taker_public_side_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? AND traded=1 ORDER BY resolved_at_ms,market_id""",
                (cohort, cutoff),
            )]
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,winner,status,side,observed_ask,stake_usdt,net_pnl_usdt,net_roi,resolved_at_ms
                    FROM wallet_target_taker_public_side_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                (cohort, cutoff),
            )]
        settled = int(row["settled"])
        traded = int(row["traded"])
        wins = int(row["wins"])
        stake = float(row["stake"])
        pnl = float(row["pnl"])
        equity = peak = max_drawdown = 0.0
        current_loss_streak = longest_loss_streak = 0
        for item in ordered:
            equity += float(item["net_pnl_usdt"] or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if item["status"] == "LOSS":
                current_loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, current_loss_streak)
            else:
                current_loss_streak = 0
        return {
            "markets": markets,
            "settledMarkets": settled,
            "pendingMarkets": max(0, markets - settled),
            "tradedMarkets": traded,
            "tradeRate": traded / settled if settled else None,
            "wins": wins,
            "losses": int(row["losses"]),
            "winRate": wins / traded if traded else None,
            "stakeUsdt": stake,
            "netPnlUsdt": pnl,
            "netRoi": pnl / stake if stake else None,
            "maxDrawdownUsdt": max_drawdown,
            "longestLossStreak": longest_loss_streak,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.public_side_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute(
                "DELETE FROM wallet_target_taker_public_side_v1_decisions WHERE decision_at_ms<?",
                (cutoff,),
            )
            self.db.execute(
                "DELETE FROM wallet_target_taker_public_side_v1_events WHERE decision_at_ms<?",
                (cutoff,),
            )
            self.db.execute(
                "DELETE FROM wallet_target_taker_public_side_v1_results WHERE resolved_at_ms<?",
                (cutoff,),
            )
            self.db.execute(
                "DELETE FROM wallet_target_taker_public_side_v1_markets WHERE started_at_ms<? AND market_id!=?",
                (cutoff, int(self.market_id or -1)),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        cohorts: dict[str, Any] = {}
        for cohort in public_side.COHORTS:
            state = self.public_side_states[cohort]
            cohorts[cohort] = {
                "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
                "deploymentBoundaryMs": state["deployedAtMs"],
                "excludedDeploymentMarketId": state["excludedMarketId"],
                "currentEvent": state["event"],
                "lastDecision": state["lastDecision"],
                "eligibility": state["eligibility"] if cohort == public_side.HAZARD_SIDE_COHORT else None,
                "performance": self._public_side_performance(cohort),
            }
        payload["targetTakerPublicSideV1Lab"] = {
            "version": public_side.VERSION,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "fixedSizing": True,
            "policy": public_side.policy(),
            "cohorts": cohorts,
            "abTest": {
                "sideOnly": public_side.SIDE_ONLY_COHORT,
                "hazardSide": public_side.HAZARD_SIDE_COHORT,
                "question": "Does own-Maker-fill five-second time/price gating improve forward PnL over the same public side rule alone?",
            },
            "evidenceBoundary": "Side uses public BTC spot/strike, Prediction, futures/spot queue/return and direction state only. HAZARD_SIDE eligibility comes only from Lifecycle V3's own paper Maker fills; the entire Target wallet is observational and cannot trigger this strategy.",
            "promotion": "research-only cohorts absent from every live allowlist; no automatic promotion",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["targetTakerPublicSideV1"] = list(public_side.COHORTS)
        payload["paperOnly"] = True
        payload["liveOrdersAffected"] = False
        return payload


class _Handler(v4_18._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_19Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"{public_side.SIDE_ONLY_COHORT} + {public_side.HAZARD_SIDE_COHORT} active; "
        "paperOnly=true; targetEventsDriveStrategy=false; liveOrdersAffected=false",
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
