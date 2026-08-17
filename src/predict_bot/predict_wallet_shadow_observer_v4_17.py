from __future__ import annotations

import json
import statistics
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_reconstructed_maker_strategy_v3 as strategy_v3
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_16 as v4_16

VERSION = "PREDICT_WALLET_SHADOW_V0_22_EVENT_DRIVEN_MAKER_LIFECYCLE_V3"


class WalletShadowObserver(v4_16.WalletShadowObserver):
    """V4.16 plus a forward-only event-driven reconstructed Maker lifecycle cohort."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self.lifecycle_v3_schema_ready = False
        self.lifecycle_v3_sequence = 0
        self.lifecycle_v3_plan_sequence = 0
        self.lifecycle_v3_state: dict[str, Any] = {}
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS wallet_reconstructed_maker_lifecycle_v3_plans (
                    plan_id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    filled_order_id TEXT NOT NULL,
                    filled_side TEXT NOT NULL,
                    source_price_tick INTEGER NOT NULL,
                    source_price REAL NOT NULL,
                    fill_ms INTEGER NOT NULL,
                    due_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    resolved_at_ms INTEGER,
                    action TEXT,
                    target_price_tick INTEGER,
                    payload_json TEXT NOT NULL
                )"""
            )
            self.db.execute(
                """CREATE INDEX IF NOT EXISTS idx_wallet_reconstructed_maker_lifecycle_v3_plans
                   ON wallet_reconstructed_maker_lifecycle_v3_plans(cohort,market_id,status,due_ms)"""
            )
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_reconstructed_maker_v1_meta WHERE cohort=?",
                (strategy_v3.COHORT,),
            ).fetchone()
            if row is None:
                deployed = base._now_ms()
                excluded = None
                self.db.execute(
                    "INSERT INTO wallet_reconstructed_maker_v1_meta VALUES (?,?,?,?)",
                    (strategy_v3.COHORT, deployed, None, json.dumps(strategy_v3.policy(), separators=(",", ":"))),
                )
            else:
                deployed = int(row["deployed_at_ms"])
                excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET policy_json=? WHERE cohort=?",
                    (json.dumps(strategy_v3.policy(), separators=(",", ":")), strategy_v3.COHORT),
                )
            self.db.commit()
        self.lifecycle_v3_state = self._empty_lifecycle_state(deployed, excluded)
        self.lifecycle_v3_schema_ready = True
        self._seed_lifecycle_pending_settlements()

    @staticmethod
    def _empty_lifecycle_state(deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
        return {
            "active": False,
            "deployedAtMs": int(deployed_at_ms),
            "excludedMarketId": excluded_market_id,
            "initialized": False,
            "openingMissed": False,
            "initializationStatus": "WAITING_MARKET",
            "frozen": False,
            "lastSnapshotNs": None,
            "orders": {},
            "lastClosed": {},
            "pendingPlans": {},
            "upShares": 0.0,
            "downShares": 0.0,
            "upCost": 0.0,
            "downCost": 0.0,
            "initialReservedUsdt": 0.0,
            "peakReservedUsdt": 0.0,
            "lifecycleCounts": {},
            "lastPlan": None,
        }

    def _count_lifecycle(self, key: str) -> None:
        counts = self.lifecycle_v3_state["lifecycleCounts"]
        counts[key] = int(counts.get(key, 0)) + 1

    def _cancel_lifecycle_orders(self, reason: str, now_ms: int) -> None:
        state = self.lifecycle_v3_state
        rows = []
        for order in list(state["orders"].values()):
            rows.append((now_ms, reason, order["id"]))
            state["lastClosed"][(str(order["side"]), int(order["priceTick"]))] = now_ms
        if rows:
            with self.db_lock:
                self.db.executemany(
                    """UPDATE wallet_reconstructed_maker_v1_orders
                          SET status='CANCELLED',closed_at_ms=?,close_reason=?
                        WHERE id=? AND status='ACTIVE'""",
                    rows,
                )
                self.db.commit()
        state["orders"] = {}

    def _cancel_lifecycle_plans(self, reason: str, now_ms: int) -> None:
        state = self.lifecycle_v3_state
        ids = list(state["pendingPlans"])
        if ids:
            with self.db_lock:
                self.db.executemany(
                    """UPDATE wallet_reconstructed_maker_lifecycle_v3_plans
                          SET status='CANCELLED',resolved_at_ms=?,action=?
                        WHERE plan_id=? AND status='PENDING'""",
                    [(now_ms, reason, plan_id) for plan_id in ids],
                )
                self.db.commit()
        state["pendingPlans"] = {}

    def _place_lifecycle_orders(self, orders: list[dict[str, Any]], *, snapshot_ns: int, now_ms: int) -> int:
        state = self.lifecycle_v3_state
        pending: list[tuple[Any, ...]] = []
        for order in orders:
            key = (str(order["side"]), int(order["priceTick"]))
            if key in state["orders"]:
                continue
            self.lifecycle_v3_sequence += 1
            order_id = f"{strategy_v3.COHORT}:{self.market_id}:{key[0]}:{key[1]}:{now_ms}:{self.lifecycle_v3_sequence}"
            stored = {
                "id": order_id,
                "side": key[0],
                "priceTick": key[1],
                "price": float(order["price"]),
                "shares": float(order["shares"]),
                "origin": str(order.get("origin") or "LIFECYCLE"),
                "placedAtMs": now_ms,
            }
            state["orders"][key] = stored
            pending.append((
                order_id, strategy_v3.COHORT, int(self.market_id), key[0], key[1], stored["price"],
                stored["shares"], stored["origin"], now_ms, "ACTIVE", snapshot_ns,
            ))
        if pending:
            with self.db_lock:
                self.db.executemany(
                    """INSERT INTO wallet_reconstructed_maker_v1_orders(
                           id,cohort,market_id,side,price_tick,price,shares,origin,
                           placed_at_ms,status,snapshot_timestamp_ns
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    pending,
                )
                self.db.commit()
        return len(pending)

    def _schedule_lifecycle_plan(self, order: dict[str, Any], fill_ms: int) -> None:
        state = self.lifecycle_v3_state
        self.lifecycle_v3_plan_sequence += 1
        plan_id = f"{strategy_v3.COHORT}:{self.market_id}:{order['id']}:{fill_ms}:{self.lifecycle_v3_plan_sequence}"
        due_ms = fill_ms + strategy_v3.POST_FILL_DECISION_DELAY_MS
        plan = {
            "planId": plan_id,
            "filledOrderId": str(order["id"]),
            "filledSide": str(order["side"]),
            "sourcePriceTick": int(order["priceTick"]),
            "sourcePrice": float(order["price"]),
            "fillMs": int(fill_ms),
            "dueMs": int(due_ms),
        }
        state["pendingPlans"][plan_id] = plan
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_reconstructed_maker_lifecycle_v3_plans(
                       plan_id,cohort,market_id,filled_order_id,filled_side,source_price_tick,
                       source_price,fill_ms,due_ms,status,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?)""",
                (
                    plan_id, strategy_v3.COHORT, int(self.market_id), str(order["id"]), str(order["side"]),
                    int(order["priceTick"]), float(order["price"]), int(fill_ms), int(due_ms),
                    json.dumps({"scheduledFromOwnPaperFill": True}, separators=(",", ":")),
                ),
            )
            self.db.commit()
        self._count_lifecycle("PLAN_SCHEDULED")

    def _restore_lifecycle_market(self, market_id: int, title: str | None, now_ms: int) -> None:
        state = self.lifecycle_v3_state
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), strategy_v3.COHORT),
                )
            market_row = self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy_v3.COHORT, int(market_id)),
            ).fetchone()
            clean = self._empty_lifecycle_state(state["deployedAtMs"], state["excludedMarketId"])
            clean["active"] = market_row is not None or int(market_id) != state["excludedMarketId"]
            state.clear()
            state.update(clean)
            if not state["active"]:
                self.db.commit()
                return
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_reconstructed_maker_v1_markets(
                       cohort,market_id,title,started_at_ms,initialization_status
                   ) VALUES (?,?,?,?,'WAITING_OPEN')""",
                (strategy_v3.COHORT, int(market_id), title, now_ms),
            )
            market_row = self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy_v3.COHORT, int(market_id)),
            ).fetchone()
            state["initializationStatus"] = str(market_row["initialization_status"])
            state["initialized"] = state["initializationStatus"] in {"INITIALIZED", "FROZEN_30S"}
            state["openingMissed"] = state["initializationStatus"] == "MISSED_OPENING_WINDOW"
            state["frozen"] = state["initializationStatus"] == "FROZEN_30S"
            state["initialReservedUsdt"] = float(market_row["initial_reserved_usdt"])
            state["peakReservedUsdt"] = float(market_row["peak_reserved_usdt"])
            orders = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                (strategy_v3.COHORT, int(market_id)),
            )]
            for order in orders:
                key = (str(order["side"]), int(order["price_tick"]))
                if order["status"] == "ACTIVE":
                    state["orders"][key] = {
                        "id": order["id"], "side": order["side"], "priceTick": order["price_tick"],
                        "price": order["price"], "shares": order["shares"], "origin": order["origin"],
                        "placedAtMs": order["placed_at_ms"],
                    }
                elif order["status"] == "FILLED":
                    side_key = str(order["side"]).lower()
                    state[f"{side_key}Shares"] += float(order["shares"])
                    state[f"{side_key}Cost"] += float(order["shares"]) * float(order["price"])
                if order["closed_at_ms"] is not None:
                    state["lastClosed"][key] = max(int(order["closed_at_ms"]), int(state["lastClosed"].get(key, 0)))
            plans = [dict(row) for row in self.db.execute(
                """SELECT * FROM wallet_reconstructed_maker_lifecycle_v3_plans
                    WHERE cohort=? AND market_id=? AND status='PENDING' ORDER BY due_ms,plan_id""",
                (strategy_v3.COHORT, int(market_id)),
            )]
            for row in plans:
                state["pendingPlans"][str(row["plan_id"])] = {
                    "planId": str(row["plan_id"]),
                    "filledOrderId": str(row["filled_order_id"]),
                    "filledSide": str(row["filled_side"]),
                    "sourcePriceTick": int(row["source_price_tick"]),
                    "sourcePrice": float(row["source_price"]),
                    "fillMs": int(row["fill_ms"]),
                    "dueMs": int(row["due_ms"]),
                }
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        if self.lifecycle_v3_schema_ready and self.market_id is not None:
            now_ms = base._now_ms()
            self._cancel_lifecycle_orders("MARKET_ROLLOVER", now_ms)
            self._cancel_lifecycle_plans("MARKET_ROLLOVER", now_ms)
        super()._reset_market(market_id, bucket, title)
        if self.lifecycle_v3_schema_ready:
            self._restore_lifecycle_market(int(market_id), title, base._now_ms())

    def _current_lifecycle_inventory(self) -> dict[str, Any]:
        state = self.lifecycle_v3_state
        return strategy_v3.inventory(state["upShares"], state["downShares"], state["upCost"], state["downCost"])

    def _update_lifecycle_reservation(self) -> None:
        state = self.lifecycle_v3_state
        current = sum(float(order["price"]) * float(order["shares"]) for order in state["orders"].values())
        state["peakReservedUsdt"] = max(float(state["peakReservedUsdt"]), current)
        with self.db_lock:
            self.db.execute(
                "UPDATE wallet_reconstructed_maker_v1_markets SET peak_reserved_usdt=? WHERE cohort=? AND market_id=?",
                (state["peakReservedUsdt"], strategy_v3.COHORT, int(self.market_id)),
            )
            self.db.commit()

    def _resolve_due_lifecycle_plans(self, snapshot: dict[str, Any], snapshot_ns: int, now_ms: int) -> None:
        state = self.lifecycle_v3_state
        for plan_id, pending in sorted(list(state["pendingPlans"].items()), key=lambda item: item[1]["dueMs"]):
            if int(pending["dueMs"]) > now_ms:
                continue
            filled_order = {
                "id": pending["filledOrderId"],
                "side": pending["filledSide"],
                "priceTick": pending["sourcePriceTick"],
                "price": pending["sourcePrice"],
                "shares": strategy_v3.SHARES_PER_ORDER,
            }
            plan = strategy_v3.post_fill_plan(snapshot, self._current_lifecycle_inventory(), filled_order)
            action = str(plan["action"])
            placed = 0
            if action in {"SAME_PRICE_REFILL", "REPRICE"}:
                order = strategy_v3.planned_order(plan)
                assert order is not None
                key = (str(order["side"]), int(order["priceTick"]))
                if key in state["orders"]:
                    action = "SKIP_DUPLICATE_ACTIVE_LEVEL"
                elif action == "SAME_PRICE_REFILL" and now_ms - int(state["lastClosed"].get(key, 0)) < strategy_v3.SAME_PRICE_REFILL_COOLDOWN_MS:
                    action = "SKIP_REFILL_COOLDOWN"
                else:
                    placed = self._place_lifecycle_orders([order], snapshot_ns=snapshot_ns, now_ms=now_ms)
                    if not placed:
                        action = "SKIP_NOT_PLACED"
            state["pendingPlans"].pop(plan_id, None)
            state["lastPlan"] = {**plan, "resolvedAtMs": now_ms, "resolvedAction": action, "placedOrders": placed}
            self._count_lifecycle(action)
            with self.db_lock:
                self.db.execute(
                    """UPDATE wallet_reconstructed_maker_lifecycle_v3_plans
                          SET status='RESOLVED',resolved_at_ms=?,action=?,target_price_tick=?,payload_json=?
                        WHERE plan_id=?""",
                    (
                        now_ms, action, plan.get("targetPriceTick"),
                        json.dumps(state["lastPlan"], separators=(",", ":"), default=str), plan_id,
                    ),
                )
                self.db.commit()

    def _advance_lifecycle_v3(self) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not self.lifecycle_v3_schema_ready or self.market_id is None or not isinstance(snapshot, dict):
            return
        state = self.lifecycle_v3_state
        if state["excludedMarketId"] is None:
            with self.db_lock:
                state["excludedMarketId"] = int(self.market_id)
                state["active"] = False
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(self.market_id), strategy_v3.COHORT),
                )
                self.db.commit()
        if not state["active"]:
            return
        now_ms = base._now_ms()
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0 or snapshot_ns == state["lastSnapshotNs"]:
            return
        state["lastSnapshotNs"] = snapshot_ns
        usable, _ = strategy_v3.snapshot_is_usable(snapshot, market_id=int(self.market_id), now_ms=now_ms)
        if not usable:
            return
        seconds_left = float(snapshot.get("seconds_left") or 0.0)

        if not state["initialized"] and not state["openingMissed"]:
            if seconds_left < strategy_v3.OPENING_MIN_SECONDS_LEFT:
                state["openingMissed"] = True
                state["initializationStatus"] = "MISSED_OPENING_WINDOW"
                with self.db_lock:
                    self.db.execute(
                        "UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='MISSED_OPENING_WINDOW' WHERE cohort=? AND market_id=?",
                        (strategy_v3.COHORT, int(self.market_id)),
                    )
                    self.db.commit()
                return
            opening = strategy_v3.opening_orders(snapshot)
            self._place_lifecycle_orders(opening, snapshot_ns=snapshot_ns, now_ms=now_ms)
            state["initialized"] = True
            state["initializationStatus"] = "INITIALIZED"
            state["initialReservedUsdt"] = sum(float(o["price"]) * float(o["shares"]) for o in state["orders"].values())
            state["peakReservedUsdt"] = state["initialReservedUsdt"]
            with self.db_lock:
                self.db.execute(
                    """UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='INITIALIZED',
                           initialized_at_ms=?,initial_reserved_usdt=?,peak_reserved_usdt=?
                       WHERE cohort=? AND market_id=?""",
                    (now_ms, state["initialReservedUsdt"], state["peakReservedUsdt"], strategy_v3.COHORT, int(self.market_id)),
                )
                self.db.commit()

        if not state["initialized"]:
            return

        for key, order in list(state["orders"].items()):
            if not strategy_v3.ask_touch_fill(order, snapshot, now_ms=now_ms):
                continue
            ask = float(snapshot[f"predict_{str(order['side']).lower()}_ask"])
            with self.db_lock:
                self.db.execute(
                    """UPDATE wallet_reconstructed_maker_v1_orders SET status='FILLED',closed_at_ms=?,
                           close_reason='STRICT_ASK_TOUCH_PROXY',fill_ask=? WHERE id=? AND status='ACTIVE'""",
                    (now_ms, ask, order["id"]),
                )
                self.db.commit()
            state["orders"].pop(key, None)
            state["lastClosed"][key] = now_ms
            side_key = str(order["side"]).lower()
            state[f"{side_key}Shares"] += float(order["shares"])
            state[f"{side_key}Cost"] += float(order["shares"]) * float(order["price"])
            self._schedule_lifecycle_plan(order, now_ms)
            self._count_lifecycle("PAPER_FILL")

        if seconds_left <= strategy_v3.REFILL_STOP_SECONDS_LEFT:
            if not state["frozen"]:
                state["frozen"] = True
                state["initializationStatus"] = "FROZEN_30S"
                self._cancel_lifecycle_plans("TAIL_FREEZE", now_ms)
                with self.db_lock:
                    self.db.execute(
                        """UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='FROZEN_30S',frozen_at_ms=?
                           WHERE cohort=? AND market_id=?""",
                        (now_ms, strategy_v3.COHORT, int(self.market_id)),
                    )
                    self.db.commit()
            self._update_lifecycle_reservation()
            return

        self._resolve_due_lifecycle_plans(snapshot, snapshot_ns, now_ms)
        self._update_lifecycle_reservation()

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_lifecycle_v3()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.lifecycle_v3_schema_ready:
            self._seed_lifecycle_pending_settlements()

    def _seed_lifecycle_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_reconstructed_maker_v1_markets m
                     LEFT JOIN wallet_reconstructed_maker_v1_results r ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (strategy_v3.COHORT, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.lifecycle_v3_schema_ready:
            return
        title = str(market.get("title") or market.get("question") or "") or None
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy_v3.COHORT, int(market_id)),
            ).fetchone()
            if registered is None:
                return
            fills = [dict(row) for row in self.db.execute(
                """SELECT side,price,shares FROM wallet_reconstructed_maker_v1_orders
                    WHERE cohort=? AND market_id=? AND status='FILLED'""",
                (strategy_v3.COHORT, int(market_id)),
            )]
            up_shares = sum(float(row["shares"]) for row in fills if row["side"] == "UP")
            down_shares = sum(float(row["shares"]) for row in fills if row["side"] == "DOWN")
            up_cost = sum(float(row["shares"]) * float(row["price"]) for row in fills if row["side"] == "UP")
            down_cost = sum(float(row["shares"]) * float(row["price"]) for row in fills if row["side"] == "DOWN")
            cost = up_cost + down_cost
            payout = up_shares if winner == "UP" else down_shares
            pnl = payout - cost
            total = up_shares + down_shares
            coverage = 2 * min(up_shares, down_shares) / total if total else None
            residual = up_shares - down_shares
            residual_side = "UP" if residual > 1e-9 else "DOWN" if residual < -1e-9 else None
            up_avg = up_cost / up_shares if up_shares else 0.0
            down_avg = down_cost / down_shares if down_shares else 0.0
            paired = min(up_shares, down_shares)
            locked = paired * (1 - up_avg - down_avg) if up_shares and down_shares else 0.0
            status = "NO_FILL" if not fills else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            self.db.execute(
                """INSERT INTO wallet_reconstructed_maker_v1_results(
                       cohort,market_id,title,winner,resolved_at_ms,traded,status,fill_count,up_shares,down_shares,
                       cost_usdt,payout_usdt,net_pnl_usdt,net_roi,paired_coverage,residual_side,residual_shares,paired_locked_edge_usdt
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                       up_shares=excluded.up_shares,down_shares=excluded.down_shares,cost_usdt=excluded.cost_usdt,
                       payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                       paired_coverage=excluded.paired_coverage,residual_side=excluded.residual_side,
                       residual_shares=excluded.residual_shares,paired_locked_edge_usdt=excluded.paired_locked_edge_usdt""",
                (
                    strategy_v3.COHORT, int(market_id), title or registered["title"], winner, base._now_ms(), int(bool(fills)),
                    status, len(fills), up_shares, down_shares, cost, payout, pnl, pnl / cost if cost else None,
                    coverage, residual_side, abs(residual), locked,
                ),
            )
            self.db.commit()

    def _lifecycle_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(fill_count),0) fills,COALESCE(SUM(cost_usdt),0) cost,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,COALESCE(SUM(paired_locked_edge_usdt),0) locked_edge,
                          COALESCE(SUM(CASE WHEN traded=1 AND residual_side IS NOT NULL AND residual_side!=winner THEN 1 ELSE 0 END),0) adverse_residual
                     FROM wallet_reconstructed_maker_v1_results WHERE cohort=? AND resolved_at_ms>=?""",
                (strategy_v3.COHORT, cutoff),
            ).fetchone())
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (strategy_v3.COHORT, cutoff),
            ).fetchone()[0])
            coverage = [float(item[0]) for item in self.db.execute(
                """SELECT paired_coverage FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? AND paired_coverage IS NOT NULL""",
                (strategy_v3.COHORT, cutoff),
            )]
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,winner,status,fill_count,up_shares,down_shares,cost_usdt,net_pnl_usdt,net_roi,
                          paired_coverage,residual_side,residual_shares,resolved_at_ms
                     FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                (strategy_v3.COHORT, cutoff),
            )]
        cost = float(row["cost"])
        traded = int(row["traded"])
        adverse = int(row["adverse_residual"])
        return {
            "markets": markets,
            "settledMarkets": int(row["settled"]),
            "pendingMarkets": max(0, markets - int(row["settled"])),
            "tradedMarkets": traded,
            "wins": int(row["wins"]),
            "winRate": int(row["wins"]) / traded if traded else None,
            "fills": int(row["fills"]),
            "costUsdt": cost,
            "netPnlUsdt": float(row["pnl"]),
            "netRoi": float(row["pnl"]) / cost if cost else None,
            "pairedLockedEdgeUsdt": float(row["locked_edge"]),
            "pairedCoverageMedian": statistics.median(coverage) if coverage else None,
            "adverseResidualMarkets": adverse,
            "adverseResidualRate": adverse / traded if traded else None,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.lifecycle_v3_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute(
                "DELETE FROM wallet_reconstructed_maker_lifecycle_v3_plans WHERE cohort=? AND fill_ms<? AND status!='PENDING'",
                (strategy_v3.COHORT, cutoff),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        state = self.lifecycle_v3_state
        active_orders = list(state["orders"].values())
        current_reserved = sum(float(order["price"]) * float(order["shares"]) for order in active_orders)
        payload["reconstructedMakerLifecycleV3Lab"] = {
            "cohort": strategy_v3.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
            "deploymentBoundaryMs": state["deployedAtMs"],
            "excludedDeploymentMarketId": state["excludedMarketId"],
            "policy": strategy_v3.policy(),
            "current": {
                "marketId": self.market_id,
                "initializationStatus": state["initializationStatus"],
                "frozen": state["frozen"],
                "activeOrders": len(active_orders),
                "upOrders": sum(order["side"] == "UP" for order in active_orders),
                "downOrders": sum(order["side"] == "DOWN" for order in active_orders),
                "pendingPostFillPlans": len(state["pendingPlans"]),
                "currentReservedUsdt": current_reserved,
                "initialReservedUsdt": state["initialReservedUsdt"],
                "peakReservedUsdt": state["peakReservedUsdt"],
                "inventory": self._current_lifecycle_inventory(),
                "lifecycleCounts": dict(state["lifecycleCounts"]),
                "lastPlan": state["lastPlan"],
            },
            "performance": self._lifecycle_performance(),
            "controlCohort": strategy_v3.policy()["controlCohort"],
            "evidenceBoundary": "fill-triggered lifecycle uses only public snapshots and this cohort's own paper fills/inventory; target events never drive runtime decisions",
            "slowFillBoundary": "current ask-touch proxy fills whole 18-share orders and cannot test partial-fill duration; slow-fill/Taker hypothesis remains isolated in offline 8778 linkage V2",
            "promotion": "research-only cohort absent from every live allowlist",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["reconstructedMakerLifecycleV3Cohort"] = strategy_v3.COHORT
        return payload


class _Handler(v4_16._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_17Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"{strategy_v3.COHORT} active beside Maker Rules V2; paperOnly=true; liveOrdersAffected=false",
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
