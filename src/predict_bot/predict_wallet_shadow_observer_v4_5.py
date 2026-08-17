from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_maker_grid_strategy as maker
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_4 as v4_4


VERSION = "PREDICT_WALLET_SHADOW_V0_7_MAKER_GRID_DEPTH"


class WalletShadowObserver(v4_4.WalletShadowObserver):
    """Three isolated forward-only Maker grid depth cohorts plus Taker V2."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_4.v4_3.v4_2.SIMULATION_DB_PATH) -> None:
        self.maker_schema_ready = False
        self.maker_states: dict[str, dict[str, Any]] = {}
        self.maker_sequence = 0
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_maker_grid_v1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    levels INTEGER NOT NULL,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_grid_v1_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_grid_v1_orders (
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
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_grid_v1_orders_market
                    ON wallet_maker_grid_v1_orders(cohort,market_id,status,placed_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_maker_grid_v1_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_count INTEGER NOT NULL,
                    up_fills INTEGER NOT NULL,
                    down_fills INTEGER NOT NULL,
                    cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_grid_v1_results_time
                    ON wallet_maker_grid_v1_results(cohort,resolved_at_ms);
                """
            )
            now_ms = base._now_ms()
            for variant in maker.COHORTS:
                cohort = str(variant["cohort"])
                row = self.db.execute(
                    "SELECT deployed_at_ms,excluded_market_id FROM wallet_maker_grid_v1_meta WHERE cohort=?",
                    (cohort,),
                ).fetchone()
                if row is None:
                    deployed = now_ms
                    excluded = None
                    self.db.execute(
                        "INSERT INTO wallet_maker_grid_v1_meta(cohort,deployed_at_ms,excluded_market_id,levels,policy_json) VALUES (?,?,?,?,?)",
                        (cohort, deployed, None, int(variant["levels"]), json.dumps(maker.policy(int(variant["levels"])), separators=(",", ":"))),
                    )
                else:
                    deployed = int(row["deployed_at_ms"])
                    excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                self.maker_states[cohort] = {
                    "active": False,
                    "deployedAtMs": deployed,
                    "excludedMarketId": excluded,
                    "anchors": None,
                    "lastRecenterMs": None,
                    "lastSnapshotNs": None,
                    "orders": {},
                    "lastClosed": {},
                    "cutoffApplied": False,
                }
            self.db.commit()
        self.maker_schema_ready = True
        self._seed_maker_pending_settlements()

    def _cancel_orders(self, cohort: str, reason: str, now_ms: int) -> None:
        state = self.maker_states[cohort]
        ids = [str(order["id"]) for order in state["orders"].values()]
        if ids:
            with self.db_lock:
                self.db.executemany(
                    "UPDATE wallet_maker_grid_v1_orders SET status='CANCELLED',closed_at_ms=?,close_reason=? WHERE id=? AND status='ACTIVE'",
                    [(now_ms, reason, order_id) for order_id in ids],
                )
                self.db.commit()
        for key in list(state["orders"]):
            state["lastClosed"][key] = now_ms
        state["orders"] = {}

    def _place_order(self, cohort: str, order: dict[str, Any], snapshot_ns: int, now_ms: int) -> None:
        state = self.maker_states[cohort]
        key = (str(order["side"]), int(order["level"]))
        if key in state["orders"]:
            return
        self.maker_sequence += 1
        order_id = f"{cohort}:{self.market_id}:{order['side']}:{order['level']}:{now_ms}:{self.maker_sequence}"
        row = {
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
                "INSERT INTO wallet_maker_grid_v1_orders(id,cohort,market_id,side,level,price,shares,placed_at_ms,status,snapshot_timestamp_ns) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (order_id, cohort, int(self.market_id), row["side"], row["level"], row["price"], row["shares"], now_ms, "ACTIVE", snapshot_ns),
            )
            self.db.commit()
        state["orders"][key] = row

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        previous_market = self.market_id
        if self.maker_schema_ready and previous_market is not None:
            for variant in maker.COHORTS:
                self._cancel_orders(str(variant["cohort"]), "MARKET_ROLLOVER", base._now_ms())
        super()._reset_market(market_id, bucket, title)
        if not self.maker_schema_ready:
            return
        now_ms = base._now_ms()
        with self.db_lock:
            for variant in maker.COHORTS:
                cohort = str(variant["cohort"])
                state = self.maker_states[cohort]
                if state["excludedMarketId"] is None:
                    state["excludedMarketId"] = int(market_id)
                    self.db.execute(
                        "UPDATE wallet_maker_grid_v1_meta SET excluded_market_id=? WHERE cohort=?",
                        (int(market_id), cohort),
                    )
                registered = self.db.execute(
                    "SELECT 1 FROM wallet_maker_grid_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                state.update({
                    "active": bool(registered) or int(market_id) != state["excludedMarketId"],
                    "anchors": None, "lastRecenterMs": None, "lastSnapshotNs": None,
                    "orders": {}, "lastClosed": {}, "cutoffApplied": False,
                })
                if state["active"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO wallet_maker_grid_v1_markets(cohort,market_id,title,started_at_ms) VALUES (?,?,?,?)",
                        (cohort, int(market_id), title, now_ms),
                    )
                    rows = [dict(row) for row in self.db.execute(
                        "SELECT * FROM wallet_maker_grid_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                        (cohort, int(market_id)),
                    )]
                    for row in rows:
                        key = (str(row["side"]), int(row["level"]))
                        if row["status"] == "ACTIVE":
                            state["orders"][key] = row
                        elif row["closed_at_ms"] is not None:
                            state["lastClosed"][key] = max(int(row["closed_at_ms"]), int(state["lastClosed"].get(key, 0)))
            self.db.commit()

    def _advance_maker_grids(self) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not self.maker_schema_ready or self.market_id is None:
            return
        now_ms = base._now_ms()
        # A parent observer can acquire its first public market during its own
        # initialization path. Enforce the forward boundary here as well so a
        # partially observed deployment market can never become scoreable.
        with self.db_lock:
            for variant in maker.COHORTS:
                cohort = str(variant["cohort"])
                state = self.maker_states[cohort]
                if state["excludedMarketId"] is None:
                    state["excludedMarketId"] = int(self.market_id)
                    state["active"] = False
                    self.db.execute(
                        "UPDATE wallet_maker_grid_v1_meta SET excluded_market_id=? WHERE cohort=?",
                        (int(self.market_id), cohort),
                    )
            self.db.commit()
        if not isinstance(snapshot, dict):
            return
        snapshot_ns = int(snapshot.get("timestamp_ns") or 0)
        usable, reason = maker.snapshot_is_usable(snapshot, market_id=int(self.market_id), now_ms=now_ms)
        for variant in maker.COHORTS:
            cohort = str(variant["cohort"])
            state = self.maker_states[cohort]
            if not state["active"] or snapshot_ns <= 0 or snapshot_ns == state["lastSnapshotNs"]:
                continue
            state["lastSnapshotNs"] = snapshot_ns
            if not usable:
                if reason == "AFTER_MAKER_ACTIVE_WINDOW" and not state["cutoffApplied"]:
                    self._cancel_orders(cohort, "ACTIVE_WINDOW_CUTOFF_30S", now_ms)
                    state["cutoffApplied"] = True
                continue
            current_anchors = maker.anchors(snapshot)
            if current_anchors is None:
                continue
            recenter = maker.should_recenter(
                state["anchors"], current_anchors,
                last_recenter_ms=state["lastRecenterMs"], now_ms=now_ms,
            )
            if recenter:
                if state["orders"]:
                    self._cancel_orders(cohort, "BATCH_RECENTER_2TICKS", now_ms)
                state["anchors"] = current_anchors
                state["lastRecenterMs"] = now_ms

            for key, order in list(state["orders"].items()):
                if not maker.ask_touch_fill(order, snapshot, now_ms=now_ms):
                    continue
                side = str(order["side"]).lower()
                ask = float(snapshot[f"predict_{side}_ask"])
                with self.db_lock:
                    self.db.execute(
                        "UPDATE wallet_maker_grid_v1_orders SET status='FILLED',closed_at_ms=?,close_reason='STRICT_ASK_TOUCH_PROXY',fill_ask=? WHERE id=? AND status='ACTIVE'",
                        (now_ms, ask, order["id"]),
                    )
                    self.db.commit()
                state["orders"].pop(key, None)
                state["lastClosed"][key] = now_ms

            for order in maker.desired_grid(snapshot, int(variant["levels"])):
                key = (str(order["side"]), int(order["level"]))
                if key in state["orders"]:
                    continue
                if now_ms - int(state["lastClosed"].get(key, 0)) < maker.REFILL_COOLDOWN_MS:
                    continue
                self._place_order(cohort, order, snapshot_ns, now_ms)

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_maker_grids()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.maker_schema_ready:
            self._seed_maker_pending_settlements()

    def _seed_maker_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_maker_grid_v1_markets m
                     LEFT JOIN wallet_maker_grid_v1_results r ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.maker_schema_ready:
            return
        title = str(market.get("title") or market.get("question") or "") or None
        with self.db_lock:
            for variant in maker.COHORTS:
                cohort = str(variant["cohort"])
                registered = self.db.execute(
                    "SELECT title FROM wallet_maker_grid_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                if registered is None:
                    continue
                fills = [dict(row) for row in self.db.execute(
                    "SELECT side,price,shares FROM wallet_maker_grid_v1_orders WHERE cohort=? AND market_id=? AND status='FILLED'",
                    (cohort, int(market_id)),
                )]
                cost = sum(float(row["price"]) * float(row["shares"]) for row in fills)
                payout = sum(float(row["shares"]) for row in fills if row["side"] == winner)
                pnl = payout - cost
                status = "NO_FILL" if not fills else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
                self.db.execute(
                    """INSERT INTO wallet_maker_grid_v1_results(
                           cohort,market_id,title,winner,resolved_at_ms,traded,status,fill_count,up_fills,down_fills,
                           cost_usdt,payout_usdt,net_pnl_usdt,net_roi
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,market_id) DO UPDATE SET
                           title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                           traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                           up_fills=excluded.up_fills,down_fills=excluded.down_fills,cost_usdt=excluded.cost_usdt,
                           payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi""",
                    (cohort, int(market_id), title or registered["title"], winner, base._now_ms(), int(bool(fills)), status,
                     len(fills), sum(row["side"] == "UP" for row in fills), sum(row["side"] == "DOWN" for row in fills),
                     cost, payout, pnl, pnl / cost if cost else None),
                )
            self.db.commit()

    def _maker_performance(self, cohort: str) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END),0) losses,
                          COALESCE(SUM(fill_count),0) fills,COALESCE(SUM(cost_usdt),0) cost,
                          COALESCE(SUM(net_pnl_usdt),0) pnl
                     FROM wallet_maker_grid_v1_results WHERE cohort=? AND resolved_at_ms>=?""",
                (cohort, cutoff),
            ).fetchone()
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_maker_grid_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (cohort, cutoff),
            ).fetchone()[0])
            recent = [dict(item) for item in self.db.execute(
                "SELECT market_id,winner,status,fill_count,up_fills,down_fills,cost_usdt,net_pnl_usdt,net_roi,resolved_at_ms FROM wallet_maker_grid_v1_results WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20",
                (cohort, cutoff),
            )]
        data = dict(row)
        cost = float(data["cost"])
        traded = int(data["traded"])
        return {
            "markets": markets, "settledMarkets": int(data["settled"]), "pendingMarkets": max(0, markets - int(data["settled"])),
            "tradedMarkets": traded, "wins": int(data["wins"]), "losses": int(data["losses"]),
            "winRate": int(data["wins"]) / traded if traded else None, "fills": int(data["fills"]),
            "costUsdt": cost, "netPnlUsdt": float(data["pnl"]), "netRoi": float(data["pnl"]) / cost if cost else None,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.maker_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute("DELETE FROM wallet_maker_grid_v1_results WHERE resolved_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_maker_grid_v1_orders WHERE placed_at_ms<? AND status!='ACTIVE'", (cutoff,))
            self.db.execute("DELETE FROM wallet_maker_grid_v1_markets WHERE started_at_ms<? AND market_id NOT IN (SELECT market_id FROM wallet_maker_grid_v1_orders)", (cutoff,))
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        variants = []
        for variant in maker.COHORTS:
            cohort = str(variant["cohort"])
            state = self.maker_states[cohort]
            active_orders = list(state["orders"].values())
            variants.append({
                **variant,
                "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
                "deploymentBoundaryMs": state["deployedAtMs"],
                "excludedDeploymentMarketId": state["excludedMarketId"],
                "config": maker.policy(int(variant["levels"])),
                "current": {
                    "marketId": self.market_id, "activeOrders": len(active_orders),
                    "upOrders": sum(order["side"] == "UP" for order in active_orders),
                    "downOrders": sum(order["side"] == "DOWN" for order in active_orders),
                    "anchors": state["anchors"], "cutoffApplied": state["cutoffApplied"],
                },
                "performance": self._maker_performance(cohort),
            })
        payload["makerGridDepthLab"] = {
            "paperOnly": True, "forwardOnly": True, "historicalBackfill": False,
            "liveOrdersAffected": False, "targetEventsDriveStrategy": False,
            "sharedEvidence": {
                "depths": "3/7/15 are frozen from observed same-second ladder cluster median/P90/maximum",
                "activeCutoff": "last 30s parent count fell 40.5% versus prior 30s; active-market share fell 61.8% to 46.3%",
                "limitations": "public fills cannot reveal unfilled/cancelled orders, queue priority or rebates",
            },
            "variants": variants,
            "promotion": "isolated paper cohorts; absent from every live allowlist",
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_5Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "Maker grid depth cohorts=3/7/15; Taker V2 retained; paper only; liveOrdersAffected=false",
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
