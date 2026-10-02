from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_lifecycle_state_taker_strategy_v4 as state_taker
from . import predict_wallet_reconstructed_maker_strategy_v3 as strategy_v3
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_17 as v4_17

VERSION = "PREDICT_WALLET_SHADOW_V0_23_LIFECYCLE_V3_STATE_TAKER_V4"


class WalletShadowObserver(v4_17.WalletShadowObserver):
    """Lifecycle Maker V3 plus an isolated five-second state-gated paper Taker overlay."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self.state_taker_schema_ready = False
        self.state_taker_sequence = 0
        self.state_taker_state: dict[str, Any] = {}
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_lifecycle_state_taker_v4_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_lifecycle_state_taker_v4_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_lifecycle_state_taker_v4_decisions (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    intent TEXT,
                    side TEXT,
                    eligibility_opened_at_ms INTEGER,
                    eligibility_expires_at_ms INTEGER,
                    maker_up_shares REAL NOT NULL,
                    maker_down_shares REAL NOT NULL,
                    taker_up_shares REAL NOT NULL,
                    taker_down_shares REAL NOT NULL,
                    imbalance_ratio REAL NOT NULL,
                    repair_side TEXT,
                    direction_score REAL,
                    decision_threshold REAL,
                    seconds_left REAL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_lifecycle_state_taker_v4_decisions
                    ON wallet_lifecycle_state_taker_v4_decisions(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_lifecycle_state_taker_v4_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    intent TEXT NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    principal_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL,
                    total_cost_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_lifecycle_state_taker_v4_events
                    ON wallet_lifecycle_state_taker_v4_events(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_lifecycle_state_taker_v4_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    maker_fill_count INTEGER NOT NULL,
                    taker_fill_count INTEGER NOT NULL,
                    maker_cost_usdt REAL NOT NULL,
                    maker_payout_usdt REAL NOT NULL,
                    maker_pnl_usdt REAL NOT NULL,
                    taker_principal_usdt REAL NOT NULL,
                    taker_fee_usdt REAL NOT NULL,
                    taker_total_cost_usdt REAL NOT NULL,
                    taker_payout_usdt REAL NOT NULL,
                    taker_pnl_usdt REAL NOT NULL,
                    total_cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_lifecycle_state_taker_v4_results
                    ON wallet_lifecycle_state_taker_v4_results(cohort,resolved_at_ms);
                """
            )
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_lifecycle_state_taker_v4_meta WHERE cohort=?",
                (state_taker.COHORT,),
            ).fetchone()
            if row is None:
                deployed = base._now_ms()
                excluded = None
                self.db.execute(
                    "INSERT INTO wallet_lifecycle_state_taker_v4_meta VALUES (?,?,?,?)",
                    (state_taker.COHORT, deployed, None, json.dumps(state_taker.policy(), separators=(",", ":"))),
                )
            else:
                deployed = int(row["deployed_at_ms"])
                excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                self.db.execute(
                    "UPDATE wallet_lifecycle_state_taker_v4_meta SET policy_json=? WHERE cohort=?",
                    (json.dumps(state_taker.policy(), separators=(",", ":")), state_taker.COHORT),
                )
            self.db.commit()
        self.state_taker_state = self._empty_state_taker_state(deployed, excluded)
        self.state_taker_schema_ready = True
        self._seed_state_taker_pending_settlements()

    @staticmethod
    def _empty_state_taker_state(deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
        return {
            "active": False,
            "deployedAtMs": int(deployed_at_ms),
            "excludedMarketId": excluded_market_id,
            "eligibility": None,
            "lastDecisionSnapshotNs": None,
            "lastDecision": None,
            "takerUpShares": 0.0,
            "takerDownShares": 0.0,
            "takerUpPrincipal": 0.0,
            "takerDownPrincipal": 0.0,
            "takerFees": 0.0,
            "opens": 0,
            "expires": 0,
            "consumed": 0,
            "superseded": 0,
            "directionalTakers": 0,
            "inventoryRepairTakers": 0,
            "lastTransition": None,
        }

    def _restore_state_taker_market(self, market_id: int, title: str | None, now_ms: int) -> None:
        state = self.state_taker_state
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_lifecycle_state_taker_v4_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), state_taker.COHORT),
                )
            registered = self.db.execute(
                "SELECT 1 FROM wallet_lifecycle_state_taker_v4_markets WHERE cohort=? AND market_id=?",
                (state_taker.COHORT, int(market_id)),
            ).fetchone()
            clean = self._empty_state_taker_state(state["deployedAtMs"], state["excludedMarketId"])
            clean["active"] = bool(registered) or int(market_id) != int(state["excludedMarketId"])
            state.clear()
            state.update(clean)
            if not state["active"]:
                self.db.commit()
                return
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_lifecycle_state_taker_v4_markets(cohort,market_id,title,started_at_ms) VALUES (?,?,?,?)",
                (state_taker.COHORT, int(market_id), title, now_ms),
            )
            events = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_lifecycle_state_taker_v4_events WHERE cohort=? AND market_id=? ORDER BY decision_at_ms,id",
                (state_taker.COHORT, int(market_id)),
            )]
            for event in events:
                side = str(event["side"]).title()
                state[f"taker{side}Shares"] += float(event["shares"])
                state[f"taker{side}Principal"] += float(event["principal_usdt"])
                state["takerFees"] += float(event["fee_usdt"])
                if event["intent"] == "INVENTORY_REPAIR_TAKER":
                    state["inventoryRepairTakers"] += 1
                else:
                    state["directionalTakers"] += 1
                state["consumed"] += 1
            decision_row = self.db.execute(
                "SELECT payload_json,snapshot_timestamp_ns FROM wallet_lifecycle_state_taker_v4_decisions "
                "WHERE cohort=? AND market_id=? ORDER BY decision_at_ms DESC,id DESC LIMIT 1",
                (state_taker.COHORT, int(market_id)),
            ).fetchone()
            if decision_row is not None:
                state["lastDecision"] = json.loads(decision_row["payload_json"])
                state["lastDecisionSnapshotNs"] = int(decision_row["snapshot_timestamp_ns"])
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        super()._reset_market(market_id, bucket, title)
        if self.state_taker_schema_ready:
            self._restore_state_taker_market(int(market_id), title, base._now_ms())

    def _current_state_taker_inventory(self) -> dict[str, Any]:
        maker = self.lifecycle_v3_state
        state = self.state_taker_state
        return state_taker.inventory_state(
            maker_up_shares=float(maker.get("upShares") or 0.0),
            maker_down_shares=float(maker.get("downShares") or 0.0),
            taker_up_shares=float(state.get("takerUpShares") or 0.0),
            taker_down_shares=float(state.get("takerDownShares") or 0.0),
        )

    def _open_state_taker_eligibility(
        self,
        *,
        snapshot_ns: int,
        now_ms: int,
        up_filled_shares: float,
        down_filled_shares: float,
    ) -> None:
        state = self.state_taker_state
        if not state["active"]:
            return
        if state["eligibility"] is not None:
            state["superseded"] += 1
        state["eligibility"] = {
            "openedAtMs": now_ms,
            "expiresAtMs": now_ms + state_taker.ELIGIBILITY_MS,
            "openedSnapshotNs": int(snapshot_ns),
            "sourceMakerUpFilledShares": float(up_filled_shares),
            "sourceMakerDownFilledShares": float(down_filled_shares),
        }
        state["opens"] += 1
        state["lastTransition"] = {
            "kind": "ELIGIBILITY_OPENED",
            "atMs": now_ms,
            **state["eligibility"],
        }

    def _record_state_taker_decision(
        self,
        decision: dict[str, Any],
        inventory: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        self.state_taker_sequence += 1
        decision_id = f"{state_taker.COHORT}:{self.market_id}:DECISION:{snapshot_ns}:{self.state_taker_sequence}"
        payload = {
            **decision,
            "cohort": state_taker.COHORT,
            "marketId": self.market_id,
            "snapshotTimestampNs": snapshot_ns,
            "paperOnly": True,
            "targetEventsUsed": False,
        }
        score = decision.get("signal", {}).get("score")
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_lifecycle_state_taker_v4_decisions(
                       id,cohort,market_id,snapshot_timestamp_ns,decision_at_ms,state,decision,reason,intent,side,
                       eligibility_opened_at_ms,eligibility_expires_at_ms,maker_up_shares,maker_down_shares,
                       taker_up_shares,taker_down_shares,imbalance_ratio,repair_side,direction_score,
                       decision_threshold,seconds_left,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id, state_taker.COHORT, int(self.market_id), int(snapshot_ns), now_ms,
                    decision.get("state") or "NORMAL_MAKER", decision["decision"], decision["reason"],
                    decision.get("intent"), decision.get("side"), decision.get("eligibilityOpenedAtMs"),
                    decision.get("eligibilityExpiresAtMs"), float(inventory["makerUpShares"]),
                    float(inventory["makerDownShares"]), float(inventory["takerUpShares"]),
                    float(inventory["takerDownShares"]), float(inventory["combinedImbalanceRatio"]),
                    inventory.get("repairSide"), float(score) if score is not None else None,
                    decision.get("decisionThreshold"), decision.get("secondsLeft"),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        self.state_taker_state["lastDecision"] = payload
        self.state_taker_state["lastDecisionSnapshotNs"] = int(snapshot_ns)

    def _execute_state_taker(
        self,
        decision: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        fill = state_taker.execution(decision, snapshot)
        if fill is None or decision.get("side") not in {"UP", "DOWN"} or decision.get("intent") is None:
            return
        state = self.state_taker_state
        side = str(decision["side"])
        intent = str(decision["intent"])
        self.state_taker_sequence += 1
        event_id = f"{state_taker.COHORT}:{self.market_id}:TAKER:{snapshot_ns}:{self.state_taker_sequence}"
        payload = {
            "id": event_id,
            "cohort": state_taker.COHORT,
            "marketId": self.market_id,
            "snapshotTimestampNs": snapshot_ns,
            "decisionAtMs": now_ms,
            "intent": intent,
            "side": side,
            **fill,
            "paperOnly": True,
            "targetEventsUsed": False,
            "decision": decision,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_lifecycle_state_taker_v4_events(
                       id,cohort,market_id,snapshot_timestamp_ns,decision_at_ms,intent,side,observed_ask,
                       principal_usdt,fee_usdt,total_cost_usdt,shares,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, state_taker.COHORT, int(self.market_id), int(snapshot_ns), now_ms, intent, side,
                    fill["ask"], fill["principalUsdt"], fill["feeUsdt"], fill["totalCostUsdt"], fill["shares"],
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        state[f"taker{side.title()}Shares"] += float(fill["shares"])
        state[f"taker{side.title()}Principal"] += float(fill["principalUsdt"])
        state["takerFees"] += float(fill["feeUsdt"])
        state["consumed"] += 1
        if intent == "INVENTORY_REPAIR_TAKER":
            state["inventoryRepairTakers"] += 1
        else:
            state["directionalTakers"] += 1
        state["eligibility"] = None
        state["lastTransition"] = {
            "kind": "ELIGIBILITY_CONSUMED",
            "atMs": now_ms,
            "intent": intent,
            "side": side,
            "eventId": event_id,
        }

    def _advance_state_taker(self, snapshot: dict[str, Any], *, snapshot_ns: int, now_ms: int) -> None:
        state = self.state_taker_state
        if not self.state_taker_schema_ready or self.market_id is None:
            return
        if state["excludedMarketId"] is None:
            state["excludedMarketId"] = int(self.market_id)
            state["active"] = False
            with self.db_lock:
                self.db.execute(
                    "UPDATE wallet_lifecycle_state_taker_v4_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(self.market_id), state_taker.COHORT),
                )
                self.db.commit()
            return
        if not state["active"] or state["lastDecisionSnapshotNs"] == snapshot_ns:
            return
        eligibility = state.get("eligibility")
        if eligibility is None:
            return
        if now_ms > int(eligibility["expiresAtMs"]):
            state["expires"] += 1
            state["eligibility"] = None
            state["lastTransition"] = {
                "kind": "ELIGIBILITY_EXPIRED",
                "atMs": now_ms,
                "openedAtMs": eligibility["openedAtMs"],
                "expiresAtMs": eligibility["expiresAtMs"],
            }
            return
        inventory = self._current_state_taker_inventory()
        decision = state_taker.decide(
            snapshot,
            inventory,
            eligibility,
            expected_market_id=int(self.market_id),
            now_ms=now_ms,
        )
        self._record_state_taker_decision(decision, inventory, snapshot_ns=snapshot_ns, now_ms=now_ms)
        if decision["decision"] == "TRADE":
            self._execute_state_taker(decision, snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms)

    def _advance_lifecycle_v3(self) -> None:
        before_up = float(self.lifecycle_v3_state.get("upShares") or 0.0)
        before_down = float(self.lifecycle_v3_state.get("downShares") or 0.0)
        super()._advance_lifecycle_v3()
        if not self.state_taker_schema_ready or self.market_id is None:
            return
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            return
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0:
            return
        now_ms = base._now_ms()
        after_up = float(self.lifecycle_v3_state.get("upShares") or 0.0)
        after_down = float(self.lifecycle_v3_state.get("downShares") or 0.0)
        up_filled = max(0.0, after_up - before_up)
        down_filled = max(0.0, after_down - before_down)
        if up_filled + down_filled > 1e-9:
            self._open_state_taker_eligibility(
                snapshot_ns=snapshot_ns,
                now_ms=now_ms,
                up_filled_shares=up_filled,
                down_filled_shares=down_filled,
            )
        self._advance_state_taker(snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms)

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.state_taker_schema_ready:
            self._seed_state_taker_pending_settlements()

    def _seed_state_taker_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_lifecycle_state_taker_v4_markets m
                     LEFT JOIN wallet_lifecycle_state_taker_v4_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (state_taker.COHORT, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.state_taker_schema_ready:
            return
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_lifecycle_state_taker_v4_markets WHERE cohort=? AND market_id=?",
                (state_taker.COHORT, int(market_id)),
            ).fetchone()
            if registered is None:
                return
            maker_fills = [dict(row) for row in self.db.execute(
                """SELECT side,price,shares FROM wallet_reconstructed_maker_v1_orders
                    WHERE cohort=? AND market_id=? AND status='FILLED'""",
                (strategy_v3.COHORT, int(market_id)),
            )]
            taker_fills = [dict(row) for row in self.db.execute(
                """SELECT side,principal_usdt,fee_usdt,total_cost_usdt,shares
                    FROM wallet_lifecycle_state_taker_v4_events WHERE cohort=? AND market_id=?""",
                (state_taker.COHORT, int(market_id)),
            )]
            maker_cost = sum(float(row["price"]) * float(row["shares"]) for row in maker_fills)
            maker_payout = sum(float(row["shares"]) for row in maker_fills if row["side"] == winner)
            maker_pnl = maker_payout - maker_cost
            taker_principal = sum(float(row["principal_usdt"]) for row in taker_fills)
            taker_fee = sum(float(row["fee_usdt"]) for row in taker_fills)
            taker_cost = sum(float(row["total_cost_usdt"]) for row in taker_fills)
            taker_payout = sum(float(row["shares"]) for row in taker_fills if row["side"] == winner)
            taker_pnl = taker_payout - taker_cost
            total_cost = maker_cost + taker_cost
            payout = maker_payout + taker_payout
            pnl = payout - total_cost
            traded = bool(maker_fills or taker_fills)
            status = "NO_TRADE" if not traded else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
            self.db.execute(
                """INSERT INTO wallet_lifecycle_state_taker_v4_results(
                       cohort,market_id,title,winner,resolved_at_ms,traded,status,maker_fill_count,taker_fill_count,
                       maker_cost_usdt,maker_payout_usdt,maker_pnl_usdt,taker_principal_usdt,taker_fee_usdt,
                       taker_total_cost_usdt,taker_payout_usdt,taker_pnl_usdt,total_cost_usdt,payout_usdt,net_pnl_usdt,net_roi
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,maker_fill_count=excluded.maker_fill_count,
                       taker_fill_count=excluded.taker_fill_count,maker_cost_usdt=excluded.maker_cost_usdt,
                       maker_payout_usdt=excluded.maker_payout_usdt,maker_pnl_usdt=excluded.maker_pnl_usdt,
                       taker_principal_usdt=excluded.taker_principal_usdt,taker_fee_usdt=excluded.taker_fee_usdt,
                       taker_total_cost_usdt=excluded.taker_total_cost_usdt,taker_payout_usdt=excluded.taker_payout_usdt,
                       taker_pnl_usdt=excluded.taker_pnl_usdt,total_cost_usdt=excluded.total_cost_usdt,
                       payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi""",
                (
                    state_taker.COHORT, int(market_id), title, winner, base._now_ms(), int(traded), status,
                    len(maker_fills), len(taker_fills), maker_cost, maker_payout, maker_pnl,
                    taker_principal, taker_fee, taker_cost, taker_payout, taker_pnl,
                    total_cost, payout, pnl, pnl / total_cost if total_cost else None,
                ),
            )
            self.db.commit()

    def _state_taker_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(maker_fill_count),0) maker_fills,COALESCE(SUM(taker_fill_count),0) taker_fills,
                          COALESCE(SUM(maker_cost_usdt),0) maker_cost,COALESCE(SUM(maker_pnl_usdt),0) maker_pnl,
                          COALESCE(SUM(taker_total_cost_usdt),0) taker_cost,COALESCE(SUM(taker_pnl_usdt),0) taker_pnl,
                          COALESCE(SUM(total_cost_usdt),0) cost,COALESCE(SUM(net_pnl_usdt),0) pnl
                     FROM wallet_lifecycle_state_taker_v4_results WHERE cohort=? AND resolved_at_ms>=?""",
                (state_taker.COHORT, cutoff),
            ).fetchone())
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_lifecycle_state_taker_v4_markets WHERE cohort=? AND started_at_ms>=?",
                (state_taker.COHORT, cutoff),
            ).fetchone()[0])
            intents = {str(item["intent"]): int(item["n"]) for item in self.db.execute(
                """SELECT intent,COUNT(*) n FROM wallet_lifecycle_state_taker_v4_events
                    WHERE cohort=? AND decision_at_ms>=? GROUP BY intent""",
                (state_taker.COHORT, cutoff),
            )}
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,winner,status,maker_fill_count,taker_fill_count,maker_pnl_usdt,taker_pnl_usdt,
                          total_cost_usdt,net_pnl_usdt,net_roi,resolved_at_ms
                     FROM wallet_lifecycle_state_taker_v4_results
                    WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                (state_taker.COHORT, cutoff),
            )]
        cost = float(row["cost"])
        traded = int(row["traded"])
        return {
            "markets": markets,
            "settledMarkets": int(row["settled"]),
            "pendingMarkets": max(0, markets - int(row["settled"])),
            "tradedMarkets": traded,
            "wins": int(row["wins"]),
            "winRate": int(row["wins"]) / traded if traded else None,
            "makerFills": int(row["maker_fills"]),
            "takerFills": int(row["taker_fills"]),
            "directionalTakers": intents.get("DIRECTIONAL_TAKER", 0),
            "inventoryRepairTakers": intents.get("INVENTORY_REPAIR_TAKER", 0),
            "makerCostUsdt": float(row["maker_cost"]),
            "makerPnlUsdt": float(row["maker_pnl"]),
            "takerCostUsdt": float(row["taker_cost"]),
            "takerPnlUsdt": float(row["taker_pnl"]),
            "totalCostUsdt": cost,
            "netPnlUsdt": float(row["pnl"]),
            "netRoi": float(row["pnl"]) / cost if cost else None,
            "deltaVsSameMakerOnlyPnlUsdt": float(row["taker_pnl"]),
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.state_taker_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute(
                "DELETE FROM wallet_lifecycle_state_taker_v4_decisions WHERE cohort=? AND decision_at_ms<?",
                (state_taker.COHORT, cutoff),
            )
            self.db.execute(
                "DELETE FROM wallet_lifecycle_state_taker_v4_events WHERE cohort=? AND decision_at_ms<?",
                (state_taker.COHORT, cutoff),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        state = self.state_taker_state
        eligibility = state.get("eligibility")
        now_ms = base._now_ms()
        current_state = (
            "TAKER_ELIGIBLE"
            if eligibility is not None and now_ms <= int(eligibility["expiresAtMs"])
            else "NORMAL_MAKER"
        )
        payload["lifecycleStateTakerV4Lab"] = {
            "cohort": state_taker.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
            "deploymentBoundaryMs": state["deployedAtMs"],
            "excludedDeploymentMarketId": state["excludedMarketId"],
            "control": {
                "makerCohort": strategy_v3.COHORT,
                "sameMakerFillPath": True,
                "differenceUnderTest": "five-second lifecycle-gated public-state Taker overlay only",
            },
            "policy": state_taker.policy(),
            "current": {
                "marketId": self.market_id,
                "state": current_state,
                "eligibility": eligibility,
                "eligibilityRemainingMs": max(0, int(eligibility["expiresAtMs"]) - now_ms) if eligibility else 0,
                "eligibilityOpens": state["opens"],
                "eligibilityExpires": state["expires"],
                "eligibilityConsumed": state["consumed"],
                "eligibilitySuperseded": state["superseded"],
                "directionalTakers": state["directionalTakers"],
                "inventoryRepairTakers": state["inventoryRepairTakers"],
                "inventory": self._current_state_taker_inventory(),
                "lastDecision": state["lastDecision"],
                "lastTransition": state["lastTransition"],
            },
            "performance": self._state_taker_performance(),
            "evidenceBoundary": "Eligibility is opened only by Lifecycle V3's own strict full-18 paper Maker fills; target-wallet events never drive runtime decisions.",
            "slowFillBoundary": "Partial-fill duration is not synthesized. V4 tests lifecycle gating, time regime, inventory context and public flow only.",
            "promotion": "research-only cohort absent from every live allowlist",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["lifecycleStateTakerV4Cohort"] = state_taker.COHORT
        payload["liveOrdersAffected"] = False
        payload["paperOnly"] = True
        return payload


class _Handler(v4_17._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_18Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"{strategy_v3.COHORT} + {state_taker.COHORT} active; paperOnly=true; liveOrdersAffected=false",
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
