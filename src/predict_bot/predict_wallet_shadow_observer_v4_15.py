from __future__ import annotations

import json
import statistics
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_reconstructed_maker_strategy_v2 as strategy_v2
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_2 as v4_2
from . import predict_wallet_shadow_observer_v4_14 as v4_14


VERSION = "PREDICT_WALLET_SHADOW_V0_19_RECONSTRUCTED_MAKER_V2"


class WalletShadowObserver(v4_14.WalletShadowObserver):
    """V4.14 plus a causal, cancellation-aware reconstructed Maker V2 A/B cohort."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_2.SIMULATION_DB_PATH) -> None:
        self.reconstructed_v2_schema_ready = False
        self.reconstructed_v2_sequence = 0
        self.reconstructed_v2_state: dict[str, Any] = {}
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            # V4.13 owns these generic reconstructed-Maker tables. Their primary
            # keys already include cohort, so V2 can remain isolated without a
            # duplicate schema or any V1 backfill.
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_reconstructed_maker_v1_meta WHERE cohort=?",
                (strategy_v2.COHORT,),
            ).fetchone()
            if row is None:
                deployed = base._now_ms()
                excluded = None
                self.db.execute(
                    "INSERT INTO wallet_reconstructed_maker_v1_meta VALUES (?,?,?,?)",
                    (strategy_v2.COHORT, deployed, None, json.dumps(strategy_v2.policy(), separators=(",", ":"))),
                )
            else:
                deployed = int(row["deployed_at_ms"])
                excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET policy_json=? WHERE cohort=?",
                    (json.dumps(strategy_v2.policy(), separators=(",", ":")), strategy_v2.COHORT),
                )
            self.db.commit()
        self.reconstructed_v2_state = self._empty_v2_state(deployed, excluded)
        self.reconstructed_v2_schema_ready = True
        self._seed_v2_pending_settlements()

    @staticmethod
    def _empty_v2_state(deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
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
            "upShares": 0.0,
            "downShares": 0.0,
            "upCost": 0.0,
            "downCost": 0.0,
            "initialReservedUsdt": 0.0,
            "peakReservedUsdt": 0.0,
            "lastRecenterMs": None,
            "toxicBlockUntilMs": {"UP": 0, "DOWN": 0},
            "lastToxicFlow": None,
            "cancelCounts": {},
        }

    def _record_v2_cancel(self, order: dict[str, Any], reason: str, now_ms: int) -> None:
        state = self.reconstructed_v2_state
        with self.db_lock:
            self.db.execute(
                """UPDATE wallet_reconstructed_maker_v1_orders
                      SET status='CANCELLED',closed_at_ms=?,close_reason=?
                    WHERE id=? AND status='ACTIVE'""",
                (now_ms, reason, order["id"]),
            )
            self.db.commit()
        key = (str(order["side"]), int(order["priceTick"]))
        state["orders"].pop(key, None)
        state["lastClosed"][key] = now_ms
        counts = state["cancelCounts"]
        counts[reason] = int(counts.get(reason, 0)) + 1

    def _cancel_v2_orders(self, reason: str, now_ms: int) -> None:
        for order in list(self.reconstructed_v2_state["orders"].values()):
            self._record_v2_cancel(order, reason, now_ms)

    def _place_v2_orders(self, orders: list[dict[str, Any]], *, snapshot_ns: int, now_ms: int) -> None:
        state = self.reconstructed_v2_state
        pending: list[tuple[Any, ...]] = []
        for order in orders:
            key = (str(order["side"]), int(order["priceTick"]))
            if key in state["orders"]:
                continue
            self.reconstructed_v2_sequence += 1
            order_id = f"{strategy_v2.COHORT}:{self.market_id}:{key[0]}:{key[1]}:{now_ms}:{self.reconstructed_v2_sequence}"
            origin = str(order.get("origin") or "PAIR_FIRST_ACTIVE_BAND")
            if key in state["lastClosed"]:
                origin = "REFILL"
            stored = {
                "id": order_id,
                "side": key[0],
                "priceTick": key[1],
                "price": float(order["price"]),
                "shares": float(order["shares"]),
                "origin": origin,
                "placedAtMs": now_ms,
            }
            state["orders"][key] = stored
            pending.append((
                order_id, strategy_v2.COHORT, int(self.market_id), key[0], key[1], stored["price"],
                stored["shares"], origin, now_ms, "ACTIVE", snapshot_ns,
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

    def _restore_v2_market(self, market_id: int, title: str | None, now_ms: int) -> None:
        state = self.reconstructed_v2_state
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), strategy_v2.COHORT),
                )
            market_row = self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy_v2.COHORT, int(market_id)),
            ).fetchone()
            clean = self._empty_v2_state(state["deployedAtMs"], state["excludedMarketId"])
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
                (strategy_v2.COHORT, int(market_id), title, now_ms),
            )
            market_row = self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy_v2.COHORT, int(market_id)),
            ).fetchone()
            state["initializationStatus"] = str(market_row["initialization_status"])
            state["initialized"] = state["initializationStatus"] in {"INITIALIZED", "FROZEN_30S"}
            state["openingMissed"] = state["initializationStatus"] == "MISSED_OPENING_WINDOW"
            state["frozen"] = state["initializationStatus"] == "FROZEN_30S"
            state["initialReservedUsdt"] = float(market_row["initial_reserved_usdt"])
            state["peakReservedUsdt"] = float(market_row["peak_reserved_usdt"])
            rows = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                (strategy_v2.COHORT, int(market_id)),
            )]
            for order in rows:
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
                if order["status"] == "CANCELLED" and order["close_reason"]:
                    reason = str(order["close_reason"])
                    state["cancelCounts"][reason] = int(state["cancelCounts"].get(reason, 0)) + 1
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        if self.reconstructed_v2_schema_ready and self.market_id is not None:
            self._cancel_v2_orders("MARKET_ROLLOVER", base._now_ms())
        super()._reset_market(market_id, bucket, title)
        if self.reconstructed_v2_schema_ready:
            self._restore_v2_market(int(market_id), title, base._now_ms())

    def _current_v2_inventory(self) -> dict[str, Any]:
        state = self.reconstructed_v2_state
        return strategy_v2.inventory(state["upShares"], state["downShares"], state["upCost"], state["downCost"])

    def _update_v2_reservation(self) -> None:
        state = self.reconstructed_v2_state
        current = sum(float(order["price"]) * float(order["shares"]) for order in state["orders"].values())
        state["peakReservedUsdt"] = max(float(state["peakReservedUsdt"]), current)
        with self.db_lock:
            self.db.execute(
                "UPDATE wallet_reconstructed_maker_v1_markets SET peak_reserved_usdt=? WHERE cohort=? AND market_id=?",
                (state["peakReservedUsdt"], strategy_v2.COHORT, int(self.market_id)),
            )
            self.db.commit()

    def _advance_reconstructed_v2(self) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not self.reconstructed_v2_schema_ready or self.market_id is None:
            return
        state = self.reconstructed_v2_state
        if state["excludedMarketId"] is None:
            with self.db_lock:
                state["excludedMarketId"] = int(self.market_id)
                state["active"] = False
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(self.market_id), strategy_v2.COHORT),
                )
                self.db.commit()
        if not state["active"] or not isinstance(snapshot, dict):
            return
        now_ms = base._now_ms()
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0 or snapshot_ns == state["lastSnapshotNs"]:
            return
        state["lastSnapshotNs"] = snapshot_ns
        usable, _ = strategy_v2.snapshot_is_usable(snapshot, market_id=int(self.market_id), now_ms=now_ms)
        if not usable:
            return
        seconds_left = float(snapshot.get("seconds_left") or 0.0)

        if not state["initialized"] and not state["openingMissed"]:
            if seconds_left < strategy_v2.OPENING_MIN_SECONDS_LEFT:
                state["openingMissed"] = True
                state["initializationStatus"] = "MISSED_OPENING_WINDOW"
                with self.db_lock:
                    self.db.execute(
                        "UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='MISSED_OPENING_WINDOW' WHERE cohort=? AND market_id=?",
                        (strategy_v2.COHORT, int(self.market_id)),
                    )
                    self.db.commit()
                return
            opening = strategy_v2.opening_orders(snapshot)
            self._place_v2_orders(opening, snapshot_ns=snapshot_ns, now_ms=now_ms)
            state["initialized"] = True
            state["initializationStatus"] = "INITIALIZED"
            state["initialReservedUsdt"] = sum(
                float(order["price"]) * float(order["shares"]) for order in state["orders"].values()
            )
            state["peakReservedUsdt"] = state["initialReservedUsdt"]
            state["lastRecenterMs"] = now_ms
            with self.db_lock:
                self.db.execute(
                    """UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='INITIALIZED',
                           initialized_at_ms=?,initial_reserved_usdt=?,peak_reserved_usdt=?
                       WHERE cohort=? AND market_id=?""",
                    (now_ms, state["initialReservedUsdt"], state["peakReservedUsdt"], strategy_v2.COHORT, int(self.market_id)),
                )
                self.db.commit()

        if not state["initialized"]:
            return

        # Keep the fill proxy identical to V1. The experiment changes only quote
        # lifecycle and inventory control, not the optimistic/pessimistic fill model.
        for key, order in list(state["orders"].items()):
            if not strategy_v2.ask_touch_fill(order, snapshot, now_ms=now_ms):
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

        flow = strategy_v2.toxic_flow(snapshot)
        state["lastToxicFlow"] = flow
        cancel_side = flow.get("cancelSide")
        if cancel_side in {"UP", "DOWN"}:
            state["toxicBlockUntilMs"][cancel_side] = max(
                int(state["toxicBlockUntilMs"].get(cancel_side, 0)), now_ms + strategy_v2.TOXIC_HOLD_MS,
            )
        blocked_sides = {
            side for side in ("UP", "DOWN")
            if int(state["toxicBlockUntilMs"].get(side, 0)) > now_ms
        }

        inventory_state = self._current_v2_inventory()
        desired = strategy_v2.desired_orders(snapshot, inventory_state, blocked_sides=blocked_sides)
        desired_keys = {(str(order["side"]), int(order["priceTick"])) for order in desired}
        residual_side = inventory_state.get("residualSide")
        residual_active = abs(float(inventory_state.get("deltaShares") or 0.0)) >= strategy_v2.PAIR_FIRST_DELTA_SHARES - 1e-9
        recenter_due = state["lastRecenterMs"] is None or now_ms - int(state["lastRecenterMs"]) >= strategy_v2.RECENTER_MIN_INTERVAL_MS
        recentered = False

        for key, order in list(state["orders"].items()):
            side = str(order["side"])
            reason = None
            if side in blocked_sides:
                reason = "TOXIC_FLOW_CANCEL"
            elif residual_active and side == residual_side:
                reason = "PAIR_FIRST_REDUCE_ONLY"
            elif key not in desired_keys and recenter_due:
                reason = "RECENTER_OUTSIDE_ACTIVE_BAND"
                recentered = True
            if reason:
                self._record_v2_cancel(order, reason, now_ms)

        if recentered:
            state["lastRecenterMs"] = now_ms

        if seconds_left <= strategy_v2.REFILL_STOP_SECONDS_LEFT:
            if not state["frozen"]:
                state["frozen"] = True
                state["initializationStatus"] = "FROZEN_30S"
                with self.db_lock:
                    self.db.execute(
                        """UPDATE wallet_reconstructed_maker_v1_markets SET
                               initialization_status='FROZEN_30S',frozen_at_ms=?
                           WHERE cohort=? AND market_id=?""",
                        (now_ms, strategy_v2.COHORT, int(self.market_id)),
                    )
                    self.db.commit()
            self._update_v2_reservation()
            return

        # If the near-touch band moved but the recenter throttle has not elapsed,
        # do not stack a second generation beside the old one. Pair repairs and
        # same-price refills can still happen immediately.
        remaining_keys = set(state["orders"])
        out_of_band_remains = any(key not in desired_keys for key in remaining_keys)
        for order in desired:
            key = (str(order["side"]), int(order["priceTick"]))
            if key in state["orders"]:
                continue
            if out_of_band_remains and not recenter_due:
                continue
            if now_ms - int(state["lastClosed"].get(key, 0)) < strategy_v2.REFILL_COOLDOWN_MS:
                continue
            self._place_v2_orders([order], snapshot_ns=snapshot_ns, now_ms=now_ms)
        self._update_v2_reservation()

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_reconstructed_v2()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.reconstructed_v2_schema_ready:
            self._seed_v2_pending_settlements()

    def _seed_v2_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_reconstructed_maker_v1_markets m
                     LEFT JOIN wallet_reconstructed_maker_v1_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.cohort=? AND m.started_at_ms>=? AND r.market_id IS NULL""",
                (strategy_v2.COHORT, cutoff),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.reconstructed_v2_schema_ready:
            return
        title = str(market.get("title") or market.get("question") or "") or None
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy_v2.COHORT, int(market_id)),
            ).fetchone()
            if registered is None:
                return
            fills = [dict(row) for row in self.db.execute(
                """SELECT side,price,shares FROM wallet_reconstructed_maker_v1_orders
                    WHERE cohort=? AND market_id=? AND status='FILLED'""",
                (strategy_v2.COHORT, int(market_id)),
            )]
            up_shares = sum(float(row["shares"]) for row in fills if row["side"] == "UP")
            down_shares = sum(float(row["shares"]) for row in fills if row["side"] == "DOWN")
            up_cost = sum(float(row["shares"]) * float(row["price"]) for row in fills if row["side"] == "UP")
            down_cost = sum(float(row["shares"]) * float(row["price"]) for row in fills if row["side"] == "DOWN")
            cost = up_cost + down_cost
            payout = up_shares if winner == "UP" else down_shares
            pnl = payout - cost
            total_shares = up_shares + down_shares
            paired_coverage = 2 * min(up_shares, down_shares) / total_shares if total_shares else None
            residual = up_shares - down_shares
            residual_side = "UP" if residual > 1e-9 else "DOWN" if residual < -1e-9 else None
            up_average = up_cost / up_shares if up_shares else 0.0
            down_average = down_cost / down_shares if down_shares else 0.0
            paired = min(up_shares, down_shares)
            locked_edge = paired * (1 - up_average - down_average) if up_shares and down_shares else 0.0
            status = "NO_FILL" if not fills else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
            self.db.execute(
                """INSERT INTO wallet_reconstructed_maker_v1_results(
                       cohort,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                       up_shares,down_shares,cost_usdt,payout_usdt,net_pnl_usdt,net_roi,
                       paired_coverage,residual_side,residual_shares,paired_locked_edge_usdt
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cohort,market_id) DO UPDATE SET
                       title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                       traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                       up_shares=excluded.up_shares,down_shares=excluded.down_shares,
                       cost_usdt=excluded.cost_usdt,payout_usdt=excluded.payout_usdt,
                       net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                       paired_coverage=excluded.paired_coverage,residual_side=excluded.residual_side,
                       residual_shares=excluded.residual_shares,paired_locked_edge_usdt=excluded.paired_locked_edge_usdt""",
                (
                    strategy_v2.COHORT, int(market_id), title or registered["title"], winner, base._now_ms(),
                    int(bool(fills)), status, len(fills), up_shares, down_shares, cost, payout, pnl,
                    pnl / cost if cost else None, paired_coverage, residual_side, abs(residual), locked_edge,
                ),
            )
            self.db.commit()

    def _v2_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(fill_count),0) fills,COALESCE(SUM(cost_usdt),0) cost,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(paired_locked_edge_usdt),0) locked_edge,
                          COALESCE(SUM(CASE WHEN traded=1 AND residual_side IS NOT NULL AND residual_side!=winner THEN 1 ELSE 0 END),0) adverse_residual
                     FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=?""",
                (strategy_v2.COHORT, cutoff),
            ).fetchone())
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (strategy_v2.COHORT, cutoff),
            ).fetchone()[0])
            coverage = [float(item[0]) for item in self.db.execute(
                """SELECT paired_coverage FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? AND paired_coverage IS NOT NULL""",
                (strategy_v2.COHORT, cutoff),
            )]
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,winner,status,fill_count,up_shares,down_shares,cost_usdt,
                          net_pnl_usdt,net_roi,paired_coverage,residual_side,residual_shares,resolved_at_ms
                     FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                (strategy_v2.COHORT, cutoff),
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

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        state = self.reconstructed_v2_state
        active_orders = list(state["orders"].values())
        current_reserved = sum(float(order["price"]) * float(order["shares"]) for order in active_orders)
        payload["reconstructedMakerRulesV2Lab"] = {
            "cohort": strategy_v2.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
            "deploymentBoundaryMs": state["deployedAtMs"],
            "excludedDeploymentMarketId": state["excludedMarketId"],
            "policy": strategy_v2.policy(),
            "current": {
                "marketId": self.market_id,
                "initializationStatus": state["initializationStatus"],
                "frozen": state["frozen"],
                "activeOrders": len(active_orders),
                "upOrders": sum(order["side"] == "UP" for order in active_orders),
                "downOrders": sum(order["side"] == "DOWN" for order in active_orders),
                "refillOrders": sum(order["origin"] == "REFILL" for order in active_orders),
                "currentReservedUsdt": current_reserved,
                "initialReservedUsdt": state["initialReservedUsdt"],
                "peakReservedUsdt": state["peakReservedUsdt"],
                "inventory": self._current_v2_inventory(),
                "lastToxicFlow": state["lastToxicFlow"],
                "toxicBlockUntilMs": dict(state["toxicBlockUntilMs"]),
                "lastRecenterMs": state["lastRecenterMs"],
                "cancelCounts": dict(state["cancelCounts"]),
            },
            "performance": self._v2_performance(),
            "evidenceBoundary": "V2 changes causal quote lifecycle only; it still cannot identify anonymous target cancellations from public levels with certainty",
            "promotion": "research-only cohort absent from every live allowlist",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["reconstructedMakerV2Cohort"] = strategy_v2.COHORT
        return payload


class _Handler(v4_14._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_15Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"{strategy_v2.COHORT} active beside V1; paperOnly=true; liveOrdersAffected=false",
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
