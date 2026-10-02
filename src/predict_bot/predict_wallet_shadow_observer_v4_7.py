from __future__ import annotations

import json
import statistics
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_maker_grid_strategy as maker_grid
from . import predict_wallet_maker_inventory_taker_strategy as shared_strategy
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_6 as v4_6


VERSION = "PREDICT_WALLET_SHADOW_V0_10_RESERVATION_SKEW_RESEARCH"


class WalletShadowObserver(v4_6.WalletShadowObserver):
    """Forward-only Maker inventory baseline feeding an isolated paper Taker."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_6.v4_5.v4_4.v4_3.v4_2.SIMULATION_DB_PATH) -> None:
        self.shared_schema_ready = False
        self.shared_states: dict[str, dict[str, Any]] = {}
        self.shared_sequence = 0
        self.shared_similarity_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self.last_full_state_generation_ms: float | None = None
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_maker_inventory_shared_v1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_inventory_shared_v1_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_inventory_shared_v1_orders (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    placed_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    closed_at_ms INTEGER,
                    close_reason TEXT,
                    fill_ask REAL,
                    snapshot_timestamp_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_inventory_shared_v1_orders_market
                    ON wallet_maker_inventory_shared_v1_orders(cohort,market_id,status,placed_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_maker_inventory_shared_v1_decisions (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    signal_at_ms INTEGER,
                    decision_at_ms INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    maker_delta REAL NOT NULL,
                    combined_delta REAL NOT NULL,
                    maker_paired_coverage REAL,
                    depth_regime TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort,market_id,snapshot_timestamp_ns)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_inventory_shared_v1_decisions_time
                    ON wallet_maker_inventory_shared_v1_decisions(cohort,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_maker_inventory_shared_v1_taker_events (
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
                    maker_delta_before REAL NOT NULL,
                    combined_delta_before REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_inventory_shared_v1_taker_market
                    ON wallet_maker_inventory_shared_v1_taker_events(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_maker_inventory_shared_v1_results (
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
                    stress_1tick_pnl_usdt REAL NOT NULL,
                    stress_1tick_roi REAL,
                    stress_2tick_pnl_usdt REAL NOT NULL,
                    stress_2tick_roi REAL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_inventory_shared_v1_results_time
                    ON wallet_maker_inventory_shared_v1_results(cohort,resolved_at_ms);
                """
            )
            now_ms = base._now_ms()
            for variant in shared_strategy.COHORTS:
                cohort = str(variant["cohort"])
                row = self.db.execute(
                    "SELECT deployed_at_ms,excluded_market_id FROM wallet_maker_inventory_shared_v1_meta WHERE cohort=?",
                    (cohort,),
                ).fetchone()
                if row is None:
                    deployed = now_ms
                    excluded = None
                    self.db.execute(
                        "INSERT INTO wallet_maker_inventory_shared_v1_meta(cohort,deployed_at_ms,excluded_market_id,policy_json) VALUES (?,?,?,?)",
                        (cohort, deployed, None, json.dumps(shared_strategy.policy(variant), separators=(",", ":"))),
                    )
                else:
                    deployed = int(row["deployed_at_ms"])
                    excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                    self.db.execute(
                        "UPDATE wallet_maker_inventory_shared_v1_meta SET policy_json=? WHERE cohort=?",
                        (json.dumps(shared_strategy.policy(variant), separators=(",", ":")), cohort),
                    )
                self.shared_states[cohort] = self._empty_shared_state(deployed, excluded)
            self.db.commit()
        self.shared_schema_ready = True
        self._seed_shared_pending_settlements()

    @staticmethod
    def _empty_shared_state(deployed: int, excluded: int | None) -> dict[str, Any]:
        return {
            "active": False,
            "deployedAtMs": deployed,
            "excludedMarketId": excluded,
            "anchors": None,
            "depthPlan": None,
            "lastRecenterMs": None,
            "lastSnapshotNs": None,
            "orders": {},
            "lastClosed": {},
            "cutoffApplied": False,
            "makerUpShares": 0.0,
            "makerDownShares": 0.0,
            "takerUpShares": 0.0,
            "takerDownShares": 0.0,
            "lastTakerTradeMs": None,
            "lastTakerTradeSide": None,
            "lastDecision": None,
        }

    def _variant(self, cohort: str) -> dict[str, Any]:
        return next(item for item in shared_strategy.COHORTS if item["cohort"] == cohort)

    def _shared_inventory(self, state: dict[str, Any]) -> dict[str, Any]:
        return shared_strategy.inventory_state(
            maker_up_shares=state["makerUpShares"],
            maker_down_shares=state["makerDownShares"],
            taker_up_shares=state["takerUpShares"],
            taker_down_shares=state["takerDownShares"],
        )

    def _cancel_shared_orders(self, cohort: str, reason: str, now_ms: int) -> None:
        state = self.shared_states[cohort]
        ids = [str(order["id"]) for order in state["orders"].values()]
        if ids:
            with self.db_lock:
                self.db.executemany(
                    "UPDATE wallet_maker_inventory_shared_v1_orders SET status='CANCELLED',closed_at_ms=?,close_reason=? WHERE id=? AND status='ACTIVE'",
                    [(now_ms, reason, order_id) for order_id in ids],
                )
                self.db.commit()
        for key in list(state["orders"]):
            state["lastClosed"][key] = now_ms
        state["orders"] = {}

    def _place_shared_order(self, cohort: str, order: dict[str, Any], snapshot_ns: int, now_ms: int) -> None:
        state = self.shared_states[cohort]
        key = (str(order["side"]), int(order["level"]))
        if key in state["orders"]:
            return
        self.shared_sequence += 1
        order_id = f"{cohort}:{self.market_id}:{order['side']}:{order['level']}:{now_ms}:{self.shared_sequence}"
        stored = {
            "id": order_id,
            "side": str(order["side"]),
            "level": int(order["level"]),
            "price": float(order["price"]),
            "shares": float(order["shares"]),
            "placed_at_ms": now_ms,
            "status": "ACTIVE",
        }
        with self.db_lock:
            self.db.execute(
                "INSERT INTO wallet_maker_inventory_shared_v1_orders(id,cohort,market_id,side,level,price,shares,placed_at_ms,status,snapshot_timestamp_ns) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (order_id, cohort, int(self.market_id), stored["side"], stored["level"], stored["price"], stored["shares"], now_ms, "ACTIVE", snapshot_ns),
            )
            self.db.commit()
        state["orders"][key] = stored

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        previous_market = self.market_id
        if self.shared_schema_ready and previous_market is not None:
            for variant in shared_strategy.COHORTS:
                self._cancel_shared_orders(str(variant["cohort"]), "MARKET_ROLLOVER", base._now_ms())
        super()._reset_market(market_id, bucket, title)
        if not self.shared_schema_ready:
            return
        now_ms = base._now_ms()
        with self.db_lock:
            for variant in shared_strategy.COHORTS:
                cohort = str(variant["cohort"])
                state = self.shared_states[cohort]
                if state["excludedMarketId"] is None:
                    state["excludedMarketId"] = int(market_id)
                    self.db.execute(
                        "UPDATE wallet_maker_inventory_shared_v1_meta SET excluded_market_id=? WHERE cohort=?",
                        (int(market_id), cohort),
                    )
                registered = self.db.execute(
                    "SELECT 1 FROM wallet_maker_inventory_shared_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                clean = self._empty_shared_state(state["deployedAtMs"], state["excludedMarketId"])
                clean["active"] = bool(registered) or int(market_id) != state["excludedMarketId"]
                state.clear()
                state.update(clean)
                if not state["active"]:
                    continue
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_maker_inventory_shared_v1_markets(cohort,market_id,title,started_at_ms) VALUES (?,?,?,?)",
                    (cohort, int(market_id), title, now_ms),
                )
                orders = [dict(row) for row in self.db.execute(
                    "SELECT * FROM wallet_maker_inventory_shared_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                    (cohort, int(market_id)),
                )]
                for order in orders:
                    key = (str(order["side"]), int(order["level"]))
                    if order["status"] == "ACTIVE":
                        state["orders"][key] = order
                    elif order["status"] == "FILLED":
                        state[f"maker{str(order['side']).title()}Shares"] += float(order["shares"])
                    if order["closed_at_ms"] is not None:
                        state["lastClosed"][key] = max(int(order["closed_at_ms"]), int(state["lastClosed"].get(key, 0)))
                events = [dict(row) for row in self.db.execute(
                    "SELECT * FROM wallet_maker_inventory_shared_v1_taker_events WHERE cohort=? AND market_id=? ORDER BY decision_at_ms,id",
                    (cohort, int(market_id)),
                )]
                for event in events:
                    state[f"taker{str(event['side']).title()}Shares"] += float(event["shares"])
                if events:
                    state["lastTakerTradeMs"] = int(events[-1]["decision_at_ms"])
                    state["lastTakerTradeSide"] = str(events[-1]["side"])
                decision = self.db.execute(
                    "SELECT snapshot_timestamp_ns,payload_json FROM wallet_maker_inventory_shared_v1_decisions WHERE cohort=? AND market_id=? ORDER BY snapshot_timestamp_ns DESC LIMIT 1",
                    (cohort, int(market_id)),
                ).fetchone()
                if decision:
                    state["lastSnapshotNs"] = int(decision["snapshot_timestamp_ns"])
                    state["lastDecision"] = json.loads(decision["payload_json"])
            self.db.commit()

    def _advance_shared_cohorts(self) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not self.shared_schema_ready or self.market_id is None or not isinstance(snapshot, dict):
            return
        now_ms = base._now_ms()
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0:
            return
        with self.db_lock:
            for variant in shared_strategy.COHORTS:
                cohort = str(variant["cohort"])
                state = self.shared_states[cohort]
                if state["excludedMarketId"] is None:
                    state["excludedMarketId"] = int(self.market_id)
                    state["active"] = False
                    self.db.execute(
                        "UPDATE wallet_maker_inventory_shared_v1_meta SET excluded_market_id=? WHERE cohort=?",
                        (int(self.market_id), cohort),
                    )
            self.db.commit()

        maker_usable, maker_reason = maker_grid.snapshot_is_usable(snapshot, market_id=int(self.market_id), now_ms=now_ms)
        for variant in shared_strategy.COHORTS:
            cohort = str(variant["cohort"])
            state = self.shared_states[cohort]
            if not state["active"] or state["lastSnapshotNs"] == snapshot_ns:
                continue
            state["lastSnapshotNs"] = snapshot_ns
            inventory = self._shared_inventory(state)
            plan = shared_strategy.depth_plan(variant, snapshot, inventory)

            if maker_usable:
                current_anchors = maker_grid.anchors(snapshot)
                anchor_recenter = maker_grid.should_recenter(
                    state["anchors"], current_anchors,
                    last_recenter_ms=state["lastRecenterMs"], now_ms=now_ms,
                )
                plan_keys = (
                    "upLevels", "downLevels", "upPriceOffsetTicks", "downPriceOffsetTicks",
                    "suspendUp", "suspendDown", "reservationSkewEnabled",
                )
                depth_changed = state["depthPlan"] is not None and tuple(
                    state["depthPlan"].get(key) for key in plan_keys
                ) != tuple(plan.get(key) for key in plan_keys)
                depth_recenter = depth_changed and (
                    state["lastRecenterMs"] is None
                    or now_ms - int(state["lastRecenterMs"]) >= maker_grid.MIN_RECENTER_INTERVAL_MS
                )
                if anchor_recenter or depth_recenter:
                    if state["orders"]:
                        self._cancel_shared_orders(cohort, "DYNAMIC_BATCH_RECENTER", now_ms)
                    state["anchors"] = current_anchors
                    state["depthPlan"] = plan
                    state["lastRecenterMs"] = now_ms
                elif state["depthPlan"] is None:
                    state["depthPlan"] = plan

                fills_in_batch = 0
                for key, order in list(state["orders"].items()):
                    if not maker_grid.ask_touch_fill(order, snapshot, now_ms=now_ms):
                        continue
                    side = str(order["side"])
                    ask = float(snapshot[f"predict_{side.lower()}_ask"])
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE wallet_maker_inventory_shared_v1_orders SET status='FILLED',closed_at_ms=?,close_reason='STRICT_ASK_TOUCH_PROXY',fill_ask=? WHERE id=? AND status='ACTIVE'",
                            (now_ms, ask, order["id"]),
                        )
                        self.db.commit()
                    state["orders"].pop(key, None)
                    state["lastClosed"][key] = now_ms
                    state[f"maker{side.title()}Shares"] += float(order["shares"])
                    fills_in_batch += 1

                inventory = self._shared_inventory(state)
                # Keep the currently resting generation frozen until the next
                # permitted batch recenter. A fill may change inventory now,
                # but mutating the recorded plan here would leave stale deep
                # orders live and hide the change from the next snapshot.
                next_plan = shared_strategy.depth_plan(variant, snapshot, inventory)
                if shared_strategy.pooled_rebalance_required(
                    variant, state["depthPlan"], next_plan, fills_in_batch=fills_in_batch
                ):
                    if state["orders"]:
                        self._cancel_shared_orders(cohort, "POOLED_INVENTORY_BATCH_REBALANCE", now_ms)
                    state["anchors"] = current_anchors
                    state["depthPlan"] = next_plan
                    state["lastRecenterMs"] = now_ms
                active_plan = state["depthPlan"] or next_plan
                for order in shared_strategy.desired_grid(snapshot, active_plan):
                    key = (str(order["side"]), int(order["level"]))
                    if key in state["orders"]:
                        continue
                    if now_ms - int(state["lastClosed"].get(key, 0)) < maker_grid.REFILL_COOLDOWN_MS:
                        continue
                    self._place_shared_order(cohort, order, snapshot_ns, now_ms)
            elif maker_reason == "AFTER_MAKER_ACTIVE_WINDOW" and not state["cutoffApplied"]:
                self._cancel_shared_orders(cohort, "ACTIVE_WINDOW_CUTOFF_30S", now_ms)
                state["cutoffApplied"] = True

            inventory = self._shared_inventory(state)
            if variant.get("targetCoreIntegrated"):
                decision = shared_strategy.decide_target_core_taker(
                    snapshot, inventory,
                    expected_market_id=int(self.market_id), now_ms=now_ms,
                    last_trade_ms=state["lastTakerTradeMs"],
                    last_trade_side=state["lastTakerTradeSide"],
                )
            else:
                decision = shared_strategy.decide_taker(
                    snapshot, inventory,
                    expected_market_id=int(self.market_id), now_ms=now_ms,
                    last_trade_ms=state["lastTakerTradeMs"],
                )
            decision_payload = {
                **decision,
                "cohort": cohort,
                "marketId": self.market_id,
                "snapshotTimestampNs": snapshot_ns,
                "depthPlan": state["depthPlan"] or plan,
                "paperOnly": True,
                "targetEventsUsed": False,
            }
            with self.db_lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO wallet_maker_inventory_shared_v1_decisions(
                           cohort,market_id,snapshot_timestamp_ns,signal_at_ms,decision_at_ms,decision,reason,side,
                           maker_delta,combined_delta,maker_paired_coverage,depth_regime,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cohort, int(self.market_id), snapshot_ns, decision.get("signalAtMs"), now_ms,
                        decision["decision"], decision["reason"], decision.get("side"),
                        float(inventory["makerDelta"]), float(inventory["combinedDelta"]),
                        inventory.get("makerPairedCoverage"), (state["depthPlan"] or plan).get("regime"),
                        json.dumps(decision_payload, separators=(",", ":"), default=str),
                    ),
                )
                self.db.commit()
            state["lastDecision"] = decision_payload
            if decision["decision"] != "TRADE" or decision.get("side") not in {"UP", "DOWN"}:
                continue
            side = str(decision["side"])
            fill = (
                shared_strategy.target_core_taker_execution(decision, snapshot)
                if variant.get("targetCoreIntegrated")
                else shared_strategy.taker_execution(side, snapshot, inventory)
            )
            if fill is None:
                continue
            event = {
                "id": f"{cohort}:{self.market_id}:TAKER:{snapshot_ns}",
                "cohort": cohort,
                "marketId": self.market_id,
                "snapshotTimestampNs": snapshot_ns,
                "signalAtMs": int(decision["signalAtMs"]),
                "decisionAtMs": now_ms,
                "side": side,
                "makerDeltaBefore": inventory["makerDelta"],
                "combinedDeltaBefore": inventory["combinedDelta"],
                **fill,
                "paperOnly": True,
                "targetEventsUsed": False,
                "decision": decision_payload,
            }
            with self.db_lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO wallet_maker_inventory_shared_v1_taker_events(
                           id,cohort,market_id,snapshot_timestamp_ns,signal_at_ms,decision_at_ms,side,observed_ask,
                           principal_usdt,fee_usdt,total_cost_usdt,shares,maker_delta_before,combined_delta_before,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event["id"], cohort, int(self.market_id), snapshot_ns, event["signalAtMs"], now_ms, side,
                        fill["ask"], fill["principalUsdt"], fill["feeUsdt"], fill["totalCostUsdt"], fill["shares"],
                        inventory["makerDelta"], inventory["combinedDelta"],
                        json.dumps(event, separators=(",", ":"), default=str),
                    ),
                )
                self.db.commit()
            state[f"taker{side.title()}Shares"] += float(fill["shares"])
            state["lastTakerTradeMs"] = now_ms
            state["lastTakerTradeSide"] = side

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        # V4.6 retired the old signal strategy. Public collection must remain
        # independent so Maker grids and the new shared cohorts keep advancing.
        self._latest_signal()
        super()._advance_shadow(book, core)
        self._advance_shared_cohorts()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.shared_schema_ready:
            self._seed_shared_pending_settlements()

    def _seed_shared_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_maker_inventory_shared_v1_markets m
                     LEFT JOIN wallet_maker_inventory_shared_v1_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.shared_schema_ready:
            return
        with self.db_lock:
            for variant in shared_strategy.COHORTS:
                cohort = str(variant["cohort"])
                registered = self.db.execute(
                    "SELECT title FROM wallet_maker_inventory_shared_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                if registered is None:
                    continue
                maker_fills = [dict(row) for row in self.db.execute(
                    "SELECT side,price,shares FROM wallet_maker_inventory_shared_v1_orders WHERE cohort=? AND market_id=? AND status='FILLED'",
                    (cohort, int(market_id)),
                )]
                taker_fills = [dict(row) for row in self.db.execute(
                    "SELECT side,observed_ask,principal_usdt,fee_usdt,total_cost_usdt,shares FROM wallet_maker_inventory_shared_v1_taker_events WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                )]
                maker_cost = sum(float(row["price"]) * float(row["shares"]) for row in maker_fills)
                maker_payout = sum(float(row["shares"]) for row in maker_fills if row["side"] == winner)
                maker_pnl = maker_payout - maker_cost
                taker_principal = sum(float(row["principal_usdt"]) for row in taker_fills)
                taker_fee = sum(float(row["fee_usdt"]) for row in taker_fills)
                taker_cost = taker_principal + taker_fee
                taker_payout = sum(float(row["shares"]) for row in taker_fills if row["side"] == winner)
                taker_pnl = taker_payout - taker_cost
                total_cost = maker_cost + taker_cost
                payout = maker_payout + taker_payout
                pnl = payout - total_cost
                stress_pnls = []
                for ticks in (1, 2):
                    stressed_taker_payout = sum(
                        float(row["principal_usdt"]) / min(0.99, float(row["observed_ask"]) + ticks * maker_grid.GRID)
                        for row in taker_fills if row["side"] == winner
                    )
                    stress_pnls.append(maker_payout + stressed_taker_payout - total_cost)
                traded = bool(maker_fills or taker_fills)
                status = "NO_TRADE" if not traded else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
                title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
                self.db.execute(
                    """INSERT INTO wallet_maker_inventory_shared_v1_results(
                           cohort,market_id,title,winner,resolved_at_ms,traded,status,maker_fill_count,taker_fill_count,
                           maker_cost_usdt,maker_payout_usdt,maker_pnl_usdt,taker_principal_usdt,taker_fee_usdt,
                           taker_total_cost_usdt,taker_payout_usdt,taker_pnl_usdt,total_cost_usdt,payout_usdt,
                           net_pnl_usdt,net_roi,stress_1tick_pnl_usdt,stress_1tick_roi,stress_2tick_pnl_usdt,stress_2tick_roi
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,market_id) DO UPDATE SET
                           title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                           traded=excluded.traded,status=excluded.status,maker_fill_count=excluded.maker_fill_count,
                           taker_fill_count=excluded.taker_fill_count,maker_cost_usdt=excluded.maker_cost_usdt,
                           maker_payout_usdt=excluded.maker_payout_usdt,maker_pnl_usdt=excluded.maker_pnl_usdt,
                           taker_principal_usdt=excluded.taker_principal_usdt,taker_fee_usdt=excluded.taker_fee_usdt,
                           taker_total_cost_usdt=excluded.taker_total_cost_usdt,taker_payout_usdt=excluded.taker_payout_usdt,
                           taker_pnl_usdt=excluded.taker_pnl_usdt,total_cost_usdt=excluded.total_cost_usdt,
                           payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                           stress_1tick_pnl_usdt=excluded.stress_1tick_pnl_usdt,stress_1tick_roi=excluded.stress_1tick_roi,
                           stress_2tick_pnl_usdt=excluded.stress_2tick_pnl_usdt,stress_2tick_roi=excluded.stress_2tick_roi""",
                    (
                        cohort, int(market_id), title, winner, base._now_ms(), int(traded), status,
                        len(maker_fills), len(taker_fills), maker_cost, maker_payout, maker_pnl,
                        taker_principal, taker_fee, taker_cost, taker_payout, taker_pnl,
                        total_cost, payout, pnl, pnl / total_cost if total_cost else None,
                        stress_pnls[0], stress_pnls[0] / total_cost if total_cost else None,
                        stress_pnls[1], stress_pnls[1] / total_cost if total_cost else None,
                    ),
                )
            self.db.commit()

    def _shared_performance(self, cohort: str) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            summary = self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END),0) losses,
                          COALESCE(SUM(maker_fill_count),0) maker_fills,
                          COALESCE(SUM(taker_fill_count),0) taker_fills,
                          COALESCE(SUM(maker_cost_usdt),0) maker_cost,
                          COALESCE(SUM(maker_pnl_usdt),0) maker_pnl,
                          COALESCE(SUM(taker_total_cost_usdt),0) taker_cost,
                          COALESCE(SUM(taker_pnl_usdt),0) taker_pnl,
                          COALESCE(SUM(total_cost_usdt),0) cost,COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(stress_1tick_pnl_usdt),0) stress1,
                          COALESCE(SUM(stress_2tick_pnl_usdt),0) stress2
                     FROM wallet_maker_inventory_shared_v1_results WHERE cohort=? AND resolved_at_ms>=?""",
                (cohort, cutoff),
            ).fetchone()
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_maker_inventory_shared_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (cohort, cutoff),
            ).fetchone()[0])
            decisions = [dict(row) for row in self.db.execute(
                """SELECT decision,reason,COUNT(*) count FROM wallet_maker_inventory_shared_v1_decisions
                    WHERE cohort=? AND decision_at_ms>=? GROUP BY decision,reason ORDER BY decision,reason""",
                (cohort, cutoff),
            )]
            recent = [dict(row) for row in self.db.execute(
                """SELECT market_id,winner,status,maker_fill_count,taker_fill_count,maker_pnl_usdt,taker_pnl_usdt,
                          total_cost_usdt,net_pnl_usdt,net_roi,stress_1tick_roi,stress_2tick_roi,resolved_at_ms
                     FROM wallet_maker_inventory_shared_v1_results WHERE cohort=? AND resolved_at_ms>=?
                    ORDER BY resolved_at_ms DESC LIMIT 20""",
                (cohort, cutoff),
            )]
            coverage_rows = [dict(row) for row in self.db.execute(
                """SELECT o.market_id,
                          COALESCE(SUM(CASE WHEN o.side='UP' THEN o.shares ELSE 0 END),0) up_shares,
                          COALESCE(SUM(CASE WHEN o.side='DOWN' THEN o.shares ELSE 0 END),0) down_shares
                     FROM wallet_maker_inventory_shared_v1_orders o
                     JOIN wallet_maker_inventory_shared_v1_results r
                       ON r.cohort=o.cohort AND r.market_id=o.market_id
                    WHERE o.cohort=? AND o.status='FILLED' AND r.resolved_at_ms>=?
                    GROUP BY o.market_id""",
                (cohort, cutoff),
            )]
        data = dict(summary)
        traded = int(data["traded"])
        wins = int(data["wins"])
        cost = float(data["cost"])
        coverages = []
        imbalances = []
        for row in coverage_rows:
            up = float(row["up_shares"])
            down = float(row["down_shares"])
            total = up + down
            if total <= 0:
                continue
            coverages.append(2 * min(up, down) / total)
            imbalances.append(abs(up - down) / total)
        return {
            "markets": markets,
            "settledMarkets": int(data["settled"]),
            "pendingMarkets": max(0, markets - int(data["settled"])),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(data["losses"]),
            "winRate": wins / traded if traded else None,
            "makerFills": int(data["maker_fills"]),
            "takerFills": int(data["taker_fills"]),
            "makerCostUsdt": float(data["maker_cost"]),
            "makerPnlUsdt": float(data["maker_pnl"]),
            "takerCostUsdt": float(data["taker_cost"]),
            "takerPnlUsdt": float(data["taker_pnl"]),
            "totalCostUsdt": cost,
            "netPnlUsdt": float(data["pnl"]),
            "netRoi": float(data["pnl"]) / cost if cost else None,
            "stress1TickRoi": float(data["stress1"]) / cost if cost else None,
            "stress2TickRoi": float(data["stress2"]) / cost if cost else None,
            "finalMakerPairedCoverageMedian": statistics.median(coverages) if coverages else None,
            "finalMakerImbalanceMedian": statistics.median(imbalances) if imbalances else None,
            "pairedCoverageMarkets": len(coverages),
            "decisions": sum(int(row["count"]) for row in decisions),
            "decisionBreakdown": decisions,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.shared_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute("DELETE FROM wallet_maker_inventory_shared_v1_results WHERE resolved_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_maker_inventory_shared_v1_decisions WHERE decision_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_maker_inventory_shared_v1_taker_events WHERE decision_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_maker_inventory_shared_v1_orders WHERE placed_at_ms<? AND status!='ACTIVE'", (cutoff,))
            self.db.execute(
                "DELETE FROM wallet_maker_inventory_shared_v1_markets WHERE started_at_ms<? AND market_id NOT IN (SELECT market_id FROM wallet_maker_inventory_shared_v1_orders)",
                (cutoff,),
            )
            self.db.commit()

    @staticmethod
    def _paired_coverage(events: list[dict[str, Any]]) -> float | None:
        up = sum(float(item.get("shares") or 0.0) for item in events if item.get("side") == "UP")
        down = sum(float(item.get("shares") or 0.0) for item in events if item.get("side") == "DOWN")
        total = up + down
        return 2 * min(up, down) / total if total else None

    @staticmethod
    def _event_match_rate(
        paper: list[dict[str, Any]], target: list[dict[str, Any]], *, price_ticks: int | None = None
    ) -> float | None:
        if not paper:
            return None
        used: set[int] = set()
        matched = 0
        for item in sorted(paper, key=lambda row: int(row["at_ms"])):
            candidates = [
                (abs(int(other["at_ms"]) - int(item["at_ms"])), index, other)
                for index, other in enumerate(target)
                if index not in used
                and other["market_id"] == item["market_id"]
                and other["side"] == item["side"]
                and abs(int(other["at_ms"]) - int(item["at_ms"])) <= 3_000
            ]
            if not candidates:
                continue
            _, index, other = min(candidates, key=lambda row: row[0])
            if price_ticks is not None and abs(float(other["price"]) - float(item["price"])) > price_ticks * maker_grid.GRID + 1e-9:
                continue
            used.add(index)
            matched += 1
        return matched / len(paper)

    def _shared_target_similarity(self, cohort: str) -> dict[str, Any]:
        now_ms = base._now_ms()
        cached = self.shared_similarity_cache.get(cohort)
        if cached is not None and now_ms - cached[0] < 30_000:
            return dict(cached[1])
        cutoff = now_ms - self.retention_ms
        with self.db_lock:
            market_ids = [int(row[0]) for row in self.db.execute(
                "SELECT market_id FROM wallet_maker_inventory_shared_v1_markets WHERE cohort=? AND started_at_ms>=? ORDER BY market_id",
                (cohort, cutoff),
            )]
            if not market_ids:
                result = {"comparableMarkets": 0, "status": "WAITING_FORWARD_MARKETS"}
                self.shared_similarity_cache[cohort] = (now_ms, result)
                return dict(result)
            placeholders = ",".join("?" for _ in market_ids)
            target_rows = [dict(row) for row in self.db.execute(
                f"""SELECT market_id,role,side,COALESCE(order_hash,leg_id) parent_id,MIN(event_ms) at_ms,
                           SUM(price*shares)/NULLIF(SUM(shares),0) price,SUM(shares) shares
                      FROM wallet_shadow_target_events
                     WHERE wallet=? AND market_id IN ({placeholders}) AND role IN ('MAKER','TAKER')
                     GROUP BY market_id,role,side,COALESCE(order_hash,leg_id)""",
                (self.wallet, *market_ids),
            )]
            maker_rows = [dict(row) for row in self.db.execute(
                f"""SELECT market_id,side,closed_at_ms at_ms,price,shares
                      FROM wallet_maker_inventory_shared_v1_orders
                     WHERE cohort=? AND status='FILLED' AND market_id IN ({placeholders})""",
                (cohort, *market_ids),
            )]
            taker_rows = [dict(row) for row in self.db.execute(
                f"""SELECT market_id,side,decision_at_ms at_ms,observed_ask price,shares
                      FROM wallet_maker_inventory_shared_v1_taker_events
                     WHERE cohort=? AND market_id IN ({placeholders})""",
                (cohort, *market_ids),
            )]
        target_market_ids = {int(row["market_id"]) for row in target_rows}
        comparable = sorted(set(market_ids) & target_market_ids)
        if not comparable:
            result = {"comparableMarkets": 0, "status": "WAITING_TARGET_EVENTS"}
            self.shared_similarity_cache[cohort] = (now_ms, result)
            return dict(result)
        comparable_set = set(comparable)
        target_maker = [row for row in target_rows if row["role"] == "MAKER" and row["market_id"] in comparable_set]
        target_taker = [row for row in target_rows if row["role"] == "TAKER" and row["market_id"] in comparable_set]
        paper_maker = [row for row in maker_rows if row["market_id"] in comparable_set]
        paper_taker = [row for row in taker_rows if row["market_id"] in comparable_set]

        def frequency_similarity(left: int, right: int) -> float | None:
            if left == 0 and right == 0:
                return 1.0
            return min(left, right) / max(left, right) if max(left, right) else None

        target_coverage = self._paired_coverage(target_maker)
        paper_coverage = self._paired_coverage(paper_maker)
        coverage_similarity = 1 - abs(target_coverage - paper_coverage) if target_coverage is not None and paper_coverage is not None else None
        components = {
            "makerFrequency": frequency_similarity(len(paper_maker), len(target_maker)),
            "takerFrequency": frequency_similarity(len(paper_taker), len(target_taker)),
            "makerPairedCoverage": coverage_similarity,
            "makerSideTiming3s": self._event_match_rate(paper_maker, target_maker),
            "makerSideTiming3sPrice1Tick": self._event_match_rate(paper_maker, target_maker, price_ticks=1),
            "takerSideTiming3s": self._event_match_rate(paper_taker, target_taker),
        }
        values = [float(value) for value in components.values() if value is not None]
        result = {
            "status": "FORWARD_COMPARISON",
            "comparableMarkets": len(comparable),
            "paperMakerEvents": len(paper_maker),
            "targetMakerParents": len(target_maker),
            "paperTakerEvents": len(paper_taker),
            "targetTakerParents": len(target_taker),
            "paperMakerPairedCoverage": paper_coverage,
            "targetMakerPairedCoverage": target_coverage,
            "components": components,
            "overallStructuralSimilarity": sum(values) / len(values) if values else None,
            "causality": "target events are joined only after paper decisions for scoring; they never enter quote or Taker decisions",
        }
        self.shared_similarity_cache[cohort] = (now_ms, result)
        return dict(result)

    def snapshot(self) -> dict[str, Any]:
        started = time.perf_counter()
        payload = super().snapshot()
        payload["version"] = VERSION
        variants = []
        for variant in shared_strategy.COHORTS:
            cohort = str(variant["cohort"])
            state = self.shared_states[cohort]
            inventory = self._shared_inventory(state)
            variants.append({
                **variant,
                "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
                "deploymentBoundaryMs": state["deployedAtMs"],
                "excludedDeploymentMarketId": state["excludedMarketId"],
                "policy": shared_strategy.policy(variant),
                "current": {
                    "marketId": self.market_id,
                    "activeOrders": len(state["orders"]),
                    "upOrders": sum(order["side"] == "UP" for order in state["orders"].values()),
                    "downOrders": sum(order["side"] == "DOWN" for order in state["orders"].values()),
                    "depthPlan": state["depthPlan"],
                    "inventory": inventory,
                    "lastDecision": state["lastDecision"],
                    "cutoffApplied": state["cutoffApplied"],
                },
                "performance": self._shared_performance(cohort),
                "targetSimilarity": self._shared_target_similarity(cohort),
            })
        payload["makerInventoryTakerSharedLab"] = {
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "publicSignalCollectionIndependentFromRetiredTakerV2": True,
            "hypothesis": "Maker maintains a paired baseline; its residual is one input to a lower-frequency Taker correction, not a copied target event.",
            "variants": variants,
            "comparison": "Fixed 7 isolates the value of shared inventory; dynamic 3/7/15 additionally tests volatility- and inventory-aware depth.",
        }
        self.last_full_state_generation_ms = (time.perf_counter() - started) * 1_000
        payload["observerDiagnostics"] = {
            "fullStateGenerationMs": self.last_full_state_generation_ms,
            "healthEndpoint": "/health",
            "fullStateEndpoint": "/state",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        with self.lock:
            market_id = self.market_id
            last_poll_ms = self.last_poll_ms
            error = self.last_error
            status = "LIVE" if self.api_key and market_id is not None and not error else "DEGRADED" if market_id else "WAITING"
            now_ms = base._now_ms()
        collector_status = dict(self.signal_collector_status)
        return {
            "version": VERSION,
            "status": status,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "error": error,
            "marketId": market_id,
            "lastPollMs": last_poll_ms,
            "pollAgeMs": now_ms - int(last_poll_ms) if last_poll_ms else None,
            "collector8777": collector_status,
            "lastFullStateGenerationMs": self.last_full_state_generation_ms,
            "checkedAtMs": now_ms,
        }


class _Handler(base._Handler):
    observer: WalletShadowObserver

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, {"ok": True, "state": self.observer.health_snapshot()})
            return
        super().do_GET()


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_7Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "Maker inventory shared Taker and dynamic depth forward paper active; liveOrdersAffected=false",
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
