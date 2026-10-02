from __future__ import annotations

import json
import statistics
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_reconstructed_maker_strategy as strategy
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_2 as v4_2
from . import predict_wallet_shadow_observer_v4_12 as v4_12


VERSION = "PREDICT_WALLET_SHADOW_V0_16_RECONSTRUCTED_MAKER_PAPER"


class WalletShadowObserver(v4_12.WalletShadowObserver):
    """V4.12 plus an isolated evidence-reconstructed Maker-only paper cohort."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_2.SIMULATION_DB_PATH) -> None:
        self.reconstructed_schema_ready = False
        self.reconstructed_sequence = 0
        self.reconstructed_state: dict[str, Any] = {}
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_reconstructed_maker_v1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_reconstructed_maker_v1_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    initialization_status TEXT NOT NULL,
                    initialized_at_ms INTEGER,
                    frozen_at_ms INTEGER,
                    initial_reserved_usdt REAL NOT NULL DEFAULT 0,
                    peak_reserved_usdt REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_reconstructed_maker_v1_orders (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    price_tick INTEGER NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    origin TEXT NOT NULL,
                    placed_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    closed_at_ms INTEGER,
                    close_reason TEXT,
                    fill_ask REAL,
                    snapshot_timestamp_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_reconstructed_maker_orders
                    ON wallet_reconstructed_maker_v1_orders(cohort,market_id,status,placed_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_reconstructed_maker_v1_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    up_shares REAL NOT NULL,
                    down_shares REAL NOT NULL,
                    cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    paired_coverage REAL,
                    residual_side TEXT,
                    residual_shares REAL NOT NULL,
                    paired_locked_edge_usdt REAL NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_reconstructed_maker_results
                    ON wallet_reconstructed_maker_v1_results(cohort,resolved_at_ms);
                """
            )
            row = self.db.execute(
                "SELECT deployed_at_ms,excluded_market_id FROM wallet_reconstructed_maker_v1_meta WHERE cohort=?",
                (strategy.COHORT,),
            ).fetchone()
            if row is None:
                deployed = base._now_ms()
                excluded = None
                self.db.execute(
                    "INSERT INTO wallet_reconstructed_maker_v1_meta VALUES (?,?,?,?)",
                    (strategy.COHORT, deployed, None, json.dumps(strategy.policy(), separators=(",", ":"))),
                )
            else:
                deployed = int(row["deployed_at_ms"])
                excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET policy_json=? WHERE cohort=?",
                    (json.dumps(strategy.policy(), separators=(",", ":")), strategy.COHORT),
                )
            self.db.commit()
        self.reconstructed_state = self._empty_reconstructed_state(deployed, excluded)
        self.reconstructed_schema_ready = True
        self._seed_reconstructed_pending_settlements()

    @staticmethod
    def _empty_reconstructed_state(deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
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
            "blockedBySoftGuard": 0,
            "blockedByPairSum": 0,
        }

    def _cancel_reconstructed_orders(self, reason: str, now_ms: int) -> None:
        ids = [str(order["id"]) for order in self.reconstructed_state["orders"].values()]
        if ids:
            with self.db_lock:
                self.db.executemany(
                    "UPDATE wallet_reconstructed_maker_v1_orders SET status='CANCELLED',closed_at_ms=?,close_reason=? WHERE id=? AND status='ACTIVE'",
                    [(now_ms, reason, order_id) for order_id in ids],
                )
                self.db.commit()
        self.reconstructed_state["orders"] = {}

    def _place_reconstructed_orders(
        self,
        orders: list[dict[str, Any]],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = self.reconstructed_state
        pending = []
        for order in orders:
            key = (str(order["side"]), int(order["priceTick"]))
            if key in state["orders"]:
                continue
            self.reconstructed_sequence += 1
            order_id = f"{strategy.COHORT}:{self.market_id}:{key[0]}:{key[1]}:{now_ms}:{self.reconstructed_sequence}"
            origin = str(order.get("origin") or "ACTIVE_BAND")
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
                order_id, strategy.COHORT, int(self.market_id), key[0], key[1], stored["price"],
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

    def _restore_reconstructed_market(self, market_id: int, title: str | None, now_ms: int) -> None:
        state = self.reconstructed_state
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), strategy.COHORT),
                )
            market_row = self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy.COHORT, int(market_id)),
            ).fetchone()
            clean = self._empty_reconstructed_state(state["deployedAtMs"], state["excludedMarketId"])
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
                (strategy.COHORT, int(market_id), title, now_ms),
            )
            market_row = self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy.COHORT, int(market_id)),
            ).fetchone()
            state["initializationStatus"] = str(market_row["initialization_status"])
            state["initialized"] = state["initializationStatus"] in {"INITIALIZED", "FROZEN_30S"}
            state["openingMissed"] = state["initializationStatus"] == "MISSED_OPENING_WINDOW"
            state["frozen"] = state["initializationStatus"] == "FROZEN_30S"
            state["initialReservedUsdt"] = float(market_row["initial_reserved_usdt"])
            state["peakReservedUsdt"] = float(market_row["peak_reserved_usdt"])
            rows = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_reconstructed_maker_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                (strategy.COHORT, int(market_id)),
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
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        if self.reconstructed_schema_ready and self.market_id is not None:
            self._cancel_reconstructed_orders("MARKET_ROLLOVER", base._now_ms())
        super()._reset_market(market_id, bucket, title)
        if self.reconstructed_schema_ready:
            self._restore_reconstructed_market(int(market_id), title, base._now_ms())

    def _current_reconstructed_inventory(self) -> dict[str, Any]:
        state = self.reconstructed_state
        return strategy.inventory(state["upShares"], state["downShares"], state["upCost"], state["downCost"])

    def _update_reconstructed_reservation(self) -> None:
        state = self.reconstructed_state
        current = sum(float(order["price"]) * float(order["shares"]) for order in state["orders"].values())
        state["peakReservedUsdt"] = max(float(state["peakReservedUsdt"]), current)
        with self.db_lock:
            self.db.execute(
                "UPDATE wallet_reconstructed_maker_v1_markets SET peak_reserved_usdt=? WHERE cohort=? AND market_id=?",
                (state["peakReservedUsdt"], strategy.COHORT, int(self.market_id)),
            )
            self.db.commit()

    def _advance_reconstructed_maker(self) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not self.reconstructed_schema_ready or self.market_id is None:
            return
        state = self.reconstructed_state
        if state["excludedMarketId"] is None:
            with self.db_lock:
                state["excludedMarketId"] = int(self.market_id)
                state["active"] = False
                self.db.execute(
                    "UPDATE wallet_reconstructed_maker_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(self.market_id), strategy.COHORT),
                )
                self.db.commit()
        if not state["active"] or not isinstance(snapshot, dict):
            return
        now_ms = base._now_ms()
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0 or snapshot_ns == state["lastSnapshotNs"]:
            return
        state["lastSnapshotNs"] = snapshot_ns
        usable, _ = strategy.snapshot_is_usable(snapshot, market_id=int(self.market_id), now_ms=now_ms)
        if not usable:
            return
        seconds_left = float(snapshot.get("seconds_left") or 0.0)

        if not state["initialized"] and not state["openingMissed"]:
            if seconds_left < strategy.OPENING_MIN_SECONDS_LEFT:
                state["openingMissed"] = True
                state["initializationStatus"] = "MISSED_OPENING_WINDOW"
                with self.db_lock:
                    self.db.execute(
                        "UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='MISSED_OPENING_WINDOW' WHERE cohort=? AND market_id=?",
                        (strategy.COHORT, int(self.market_id)),
                    )
                    self.db.commit()
                return
            opening = strategy.opening_orders(snapshot)
            self._place_reconstructed_orders(opening, snapshot_ns=snapshot_ns, now_ms=now_ms)
            state["initialized"] = True
            state["initializationStatus"] = "INITIALIZED"
            state["initialReservedUsdt"] = sum(
                float(order["price"]) * float(order["shares"]) for order in state["orders"].values()
            )
            state["peakReservedUsdt"] = state["initialReservedUsdt"]
            with self.db_lock:
                self.db.execute(
                    """UPDATE wallet_reconstructed_maker_v1_markets SET initialization_status='INITIALIZED',
                           initialized_at_ms=?,initial_reserved_usdt=?,peak_reserved_usdt=?
                       WHERE cohort=? AND market_id=?""",
                    (now_ms, state["initialReservedUsdt"], state["peakReservedUsdt"], strategy.COHORT, int(self.market_id)),
                )
                self.db.commit()

        if not state["initialized"]:
            return
        for key, order in list(state["orders"].items()):
            if not strategy.ask_touch_fill(order, snapshot, now_ms=now_ms):
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

        if seconds_left <= strategy.REFILL_STOP_SECONDS_LEFT:
            if not state["frozen"]:
                state["frozen"] = True
                state["initializationStatus"] = "FROZEN_30S"
                with self.db_lock:
                    self.db.execute(
                        """UPDATE wallet_reconstructed_maker_v1_markets SET
                               initialization_status='FROZEN_30S',frozen_at_ms=?
                           WHERE cohort=? AND market_id=?""",
                        (now_ms, strategy.COHORT, int(self.market_id)),
                    )
                    self.db.commit()
            self._update_reconstructed_reservation()
            return

        inventory_state = self._current_reconstructed_inventory()
        desired = strategy.active_band_orders(snapshot, inventory_state)
        for order in desired:
            key = (str(order["side"]), int(order["priceTick"]))
            if key in state["orders"]:
                continue
            if now_ms - int(state["lastClosed"].get(key, 0)) < strategy.REFILL_COOLDOWN_MS:
                continue
            self._place_reconstructed_orders([order], snapshot_ns=snapshot_ns, now_ms=now_ms)
        self._update_reconstructed_reservation()

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_reconstructed_maker()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.reconstructed_schema_ready:
            self._seed_reconstructed_pending_settlements()

    def _seed_reconstructed_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_reconstructed_maker_v1_markets m
                     LEFT JOIN wallet_reconstructed_maker_v1_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.reconstructed_schema_ready:
            return
        title = str(market.get("title") or market.get("question") or "") or None
        with self.db_lock:
            registered = self.db.execute(
                "SELECT title FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND market_id=?",
                (strategy.COHORT, int(market_id)),
            ).fetchone()
            if registered is None:
                return
            fills = [dict(row) for row in self.db.execute(
                """SELECT side,price,shares FROM wallet_reconstructed_maker_v1_orders
                    WHERE cohort=? AND market_id=? AND status='FILLED'""",
                (strategy.COHORT, int(market_id)),
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
                    strategy.COHORT, int(market_id), title or registered["title"], winner, base._now_ms(),
                    int(bool(fills)), status, len(fills), up_shares, down_shares, cost, payout, pnl,
                    pnl / cost if cost else None, paired_coverage, residual_side, abs(residual), locked_edge,
                ),
            )
            self.db.commit()

    def _reconstructed_performance(self) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(fill_count),0) fills,COALESCE(SUM(cost_usdt),0) cost,
                          COALESCE(SUM(net_pnl_usdt),0) pnl,
                          COALESCE(SUM(paired_locked_edge_usdt),0) locked_edge
                     FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=?""",
                (strategy.COHORT, cutoff),
            ).fetchone())
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_reconstructed_maker_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (strategy.COHORT, cutoff),
            ).fetchone()[0])
            coverage = [float(item[0]) for item in self.db.execute(
                """SELECT paired_coverage FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? AND paired_coverage IS NOT NULL""",
                (strategy.COHORT, cutoff),
            )]
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,winner,status,fill_count,up_shares,down_shares,cost_usdt,
                          net_pnl_usdt,net_roi,paired_coverage,residual_side,residual_shares,resolved_at_ms
                     FROM wallet_reconstructed_maker_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                (strategy.COHORT, cutoff),
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
            "fills": int(row["fills"]),
            "costUsdt": cost,
            "netPnlUsdt": float(row["pnl"]),
            "netRoi": float(row["pnl"]) / cost if cost else None,
            "pairedLockedEdgeUsdt": float(row["locked_edge"]),
            "pairedCoverageMedian": statistics.median(coverage) if coverage else None,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.reconstructed_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute("DELETE FROM wallet_reconstructed_maker_v1_results WHERE resolved_at_ms<?", (cutoff,))
            self.db.execute(
                "DELETE FROM wallet_reconstructed_maker_v1_orders WHERE placed_at_ms<? AND status!='ACTIVE'",
                (cutoff,),
            )
            self.db.execute(
                """DELETE FROM wallet_reconstructed_maker_v1_markets WHERE started_at_ms<?
                     AND market_id NOT IN (SELECT market_id FROM wallet_reconstructed_maker_v1_orders)""",
                (cutoff,),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        state = self.reconstructed_state
        active_orders = list(state["orders"].values())
        current_reserved = sum(float(order["price"]) * float(order["shares"]) for order in active_orders)
        payload["reconstructedMakerRulesLab"] = {
            "cohort": strategy.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
            "deploymentBoundaryMs": state["deployedAtMs"],
            "excludedDeploymentMarketId": state["excludedMarketId"],
            "policy": strategy.policy(),
            "current": {
                "marketId": self.market_id,
                "initializationStatus": state["initializationStatus"],
                "frozen": state["frozen"],
                "activeOrders": len(active_orders),
                "upOrders": sum(order["side"] == "UP" for order in active_orders),
                "downOrders": sum(order["side"] == "DOWN" for order in active_orders),
                "openingOrders": sum(order["origin"] == "OPENING_RAIL" for order in active_orders),
                "refillOrders": sum(order["origin"] == "REFILL" for order in active_orders),
                "currentReservedUsdt": current_reserved,
                "initialReservedUsdt": state["initialReservedUsdt"],
                "peakReservedUsdt": state["peakReservedUsdt"],
                "inventory": self._current_reconstructed_inventory(),
            },
            "performance": self._reconstructed_performance(),
            "evidenceBoundary": "candidate reproduces inferred logic, not target orders; fills use strict later-ask touch without queue or rebate credit",
            "promotion": "research-only cohort absent from every live allowlist",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        return payload


class _Handler(v4_12._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_13Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "reconstructed Grid18 wide-rails soft-pool Maker cohort active; paperOnly=true; liveOrdersAffected=false",
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
