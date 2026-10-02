from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_maker_ebm_strategy_v1 as maker_ebm
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_19 as v4_19


VERSION = "PREDICT_WALLET_SHADOW_V0_25_MAKER_EBM_V1"


class WalletShadowObserver(v4_19.WalletShadowObserver):
    """V4.19 plus three isolated forward-only frozen-EBM Maker paper cohorts."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self.maker_ebm_schema_ready = False
        self.maker_ebm_sequence = 0
        self.maker_ebm_states: dict[str, dict[str, Any]] = {}
        self.maker_ebm_models: dict[str, dict[str, Any]] | None = None
        self.maker_ebm_model_error: str | None = None
        super().__init__(db_path, simulation_db_path)
        try:
            self.maker_ebm_models = maker_ebm.load_models()
        except Exception as exc:
            self.maker_ebm_models = None
            self.maker_ebm_model_error = str(exc)[:1000]

        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_maker_ebm_v1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_ebm_v1_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_ebm_v1_orders (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    price_tick INTEGER NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    origin TEXT NOT NULL,
                    placed_at_ms INTEGER NOT NULL,
                    placed_snapshot_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    closed_at_ms INTEGER,
                    close_reason TEXT,
                    fill_ask REAL,
                    UNIQUE(cohort,market_id,id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_ebm_v1_orders
                    ON wallet_maker_ebm_v1_orders(cohort,market_id,status,placed_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_maker_ebm_v1_decisions (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    hazard_probability REAL,
                    level_up_probability REAL,
                    level_down_probability REAL,
                    inventory_delta_shares REAL NOT NULL,
                    seconds_left REAL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_ebm_v1_decisions
                    ON wallet_maker_ebm_v1_decisions(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_maker_ebm_v1_results (
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
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_maker_ebm_v1_results
                    ON wallet_maker_ebm_v1_results(cohort,resolved_at_ms);
                """
            )
            now_ms = base._now_ms()
            policy_json = json.dumps(maker_ebm.policy(), separators=(",", ":"), default=str)
            deployment: dict[str, tuple[int, int | None]] = {}
            for cohort in maker_ebm.COHORTS:
                row = self.db.execute(
                    "SELECT deployed_at_ms,excluded_market_id FROM wallet_maker_ebm_v1_meta WHERE cohort=?",
                    (cohort,),
                ).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO wallet_maker_ebm_v1_meta VALUES (?,?,?,?)",
                        (cohort, now_ms, None, policy_json),
                    )
                    deployment[cohort] = (now_ms, None)
                else:
                    deployed = int(row["deployed_at_ms"])
                    excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                    self.db.execute(
                        "UPDATE wallet_maker_ebm_v1_meta SET policy_json=? WHERE cohort=?",
                        (policy_json, cohort),
                    )
                    deployment[cohort] = (deployed, excluded)
            self.db.commit()

        self.maker_ebm_states = {
            cohort: self._empty_maker_ebm_state(*deployment[cohort]) for cohort in maker_ebm.COHORTS
        }
        self.maker_ebm_schema_ready = True
        self._seed_maker_ebm_pending_settlements()

    @staticmethod
    def _empty_maker_ebm_state(deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
        return {
            "active": False,
            "deployedAtMs": int(deployed_at_ms),
            "excludedMarketId": excluded_market_id,
            "orders": {},
            "lastClosed": {},
            "upShares": 0.0,
            "downShares": 0.0,
            "upCost": 0.0,
            "downCost": 0.0,
            "fillCount": 0,
            "lastDecision": None,
            "lastDecisionKey": None,
            "lastFill": None,
        }

    def _cancel_maker_ebm_orders(self, cohort: str, reason: str, now_ms: int) -> None:
        state = self.maker_ebm_states[cohort]
        rows: list[tuple[int, str, str]] = []
        for key, order in list(state["orders"].items()):
            rows.append((int(now_ms), reason, str(order["id"])))
            state["lastClosed"][key] = int(now_ms)
        if rows:
            with self.db_lock:
                self.db.executemany(
                    """UPDATE wallet_maker_ebm_v1_orders
                          SET status='CANCELLED',closed_at_ms=?,close_reason=?
                        WHERE id=? AND status='ACTIVE'""",
                    rows,
                )
                self.db.commit()
        state["orders"] = {}

    def _restore_maker_ebm_market(self, cohort: str, market_id: int, title: str | None, now_ms: int) -> None:
        state = self.maker_ebm_states[cohort]
        with self.db_lock:
            if state["excludedMarketId"] is None:
                state["excludedMarketId"] = int(market_id)
                self.db.execute(
                    "UPDATE wallet_maker_ebm_v1_meta SET excluded_market_id=? WHERE cohort=?",
                    (int(market_id), cohort),
                )
            registered = self.db.execute(
                "SELECT 1 FROM wallet_maker_ebm_v1_markets WHERE cohort=? AND market_id=?",
                (cohort, int(market_id)),
            ).fetchone()
            clean = self._empty_maker_ebm_state(state["deployedAtMs"], state["excludedMarketId"])
            clean["active"] = bool(registered) or int(market_id) != int(state["excludedMarketId"])
            state.clear()
            state.update(clean)
            if not state["active"]:
                self.db.commit()
                return
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_maker_ebm_v1_markets(cohort,market_id,title,started_at_ms) VALUES (?,?,?,?)",
                (cohort, int(market_id), title, int(now_ms)),
            )
            rows = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_maker_ebm_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                (cohort, int(market_id)),
            )]
            for order in rows:
                key = (str(order["side"]), int(order["price_tick"]))
                if order["status"] == "ACTIVE":
                    state["orders"][key] = {
                        "id": str(order["id"]),
                        "side": str(order["side"]),
                        "priceTick": int(order["price_tick"]),
                        "price": float(order["price"]),
                        "shares": float(order["shares"]),
                        "origin": str(order["origin"]),
                        "placedAtMs": int(order["placed_at_ms"]),
                        "placedSnapshotNs": int(order["placed_snapshot_ns"]),
                    }
                elif order["status"] == "FILLED":
                    side_key = str(order["side"]).lower()
                    shares = float(order["shares"])
                    price = float(order["price"])
                    state[f"{side_key}Shares"] += shares
                    state[f"{side_key}Cost"] += shares * price
                    state["fillCount"] += 1
                if order["closed_at_ms"] is not None:
                    state["lastClosed"][key] = max(
                        int(order["closed_at_ms"]), int(state["lastClosed"].get(key, 0))
                    )
            decision_row = self.db.execute(
                "SELECT payload_json,decision,reason FROM wallet_maker_ebm_v1_decisions "
                "WHERE cohort=? AND market_id=? ORDER BY decision_at_ms DESC,id DESC LIMIT 1",
                (cohort, int(market_id)),
            ).fetchone()
            if decision_row is not None:
                try:
                    state["lastDecision"] = json.loads(decision_row["payload_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    state["lastDecision"] = None
                state["lastDecisionKey"] = (str(decision_row["decision"]), str(decision_row["reason"]))
            self.db.commit()
        self.pending_settlement_ids.add(int(market_id))

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        if self.maker_ebm_schema_ready and self.market_id is not None:
            now_ms = base._now_ms()
            for cohort in maker_ebm.COHORTS:
                self._cancel_maker_ebm_orders(cohort, "MARKET_ROLLOVER", now_ms)
        super()._reset_market(market_id, bucket, title)
        if not self.maker_ebm_schema_ready:
            return
        now_ms = base._now_ms()
        for cohort in maker_ebm.COHORTS:
            self._restore_maker_ebm_market(cohort, int(market_id), title, now_ms)

    def _maker_ebm_inventory(self, cohort: str) -> dict[str, Any]:
        state = self.maker_ebm_states[cohort]
        return maker_ebm.inventory(
            float(state["upShares"]), float(state["downShares"]),
            float(state["upCost"]), float(state["downCost"]),
        )

    def _fill_maker_ebm_orders(
        self,
        cohort: str,
        snapshot: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> int:
        state = self.maker_ebm_states[cohort]
        filled = 0
        for key, order in list(state["orders"].items()):
            if not maker_ebm.ask_touch_fill(order, snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms):
                continue
            side = str(order["side"])
            ask = maker_ebm._value(snapshot, f"predict_{side.lower()}_ask", f"predict{side.title()}Ask")
            with self.db_lock:
                self.db.execute(
                    """UPDATE wallet_maker_ebm_v1_orders
                          SET status='FILLED',closed_at_ms=?,close_reason='STRICT_ASK_TOUCH_PROXY',fill_ask=?
                        WHERE id=? AND status='ACTIVE'""",
                    (int(now_ms), ask, str(order["id"])),
                )
                self.db.commit()
            state["orders"].pop(key, None)
            state["lastClosed"][key] = int(now_ms)
            side_key = side.lower()
            shares = float(order["shares"])
            price = float(order["price"])
            state[f"{side_key}Shares"] += shares
            state[f"{side_key}Cost"] += shares * price
            state["fillCount"] += 1
            state["lastFill"] = {
                "side": side,
                "price": price,
                "shares": shares,
                "fillAsk": ask,
                "filledAtMs": int(now_ms),
                "snapshotTimestampNs": int(snapshot_ns),
            }
            filled += 1
        return filled

    def _record_maker_ebm_decision(
        self,
        cohort: str,
        decision: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
        force: bool = False,
    ) -> None:
        state = self.maker_ebm_states[cohort]
        desired = tuple(
            (str(order.get("side")), int(order.get("priceTick") or 0))
            for order in decision.get("orders") or []
        )
        key = (str(decision["decision"]), str(decision["reason"]), desired)
        if not force and key == state.get("lastDecisionKey"):
            state["lastDecision"] = decision
            return
        self.maker_ebm_sequence += 1
        decision_id = f"{cohort}:{self.market_id}:DECISION:{snapshot_ns}:{self.maker_ebm_sequence}"
        hazard = decision.get("hazard") if isinstance(decision.get("hazard"), dict) else {}
        levels = decision.get("levels") if isinstance(decision.get("levels"), dict) else {}
        up_level = levels.get("UP") if isinstance(levels.get("UP"), dict) else {}
        down_level = levels.get("DOWN") if isinstance(levels.get("DOWN"), dict) else {}
        inventory_state = decision.get("inventory") if isinstance(decision.get("inventory"), dict) else {}
        payload = {
            **decision,
            "cohort": cohort,
            "marketId": self.market_id,
            "snapshotTimestampNs": int(snapshot_ns),
            "paperOnly": True,
            "targetEventsUsed": False,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_maker_ebm_v1_decisions(
                       id,cohort,market_id,snapshot_timestamp_ns,decision_at_ms,decision,reason,
                       hazard_probability,level_up_probability,level_down_probability,
                       inventory_delta_shares,seconds_left,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id, cohort, int(self.market_id), int(snapshot_ns), int(now_ms),
                    decision["decision"], decision["reason"], hazard.get("probability"),
                    up_level.get("probability"), down_level.get("probability"),
                    float(inventory_state.get("deltaShares") or 0.0), decision.get("secondsLeft"),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        state["lastDecision"] = payload
        state["lastDecisionKey"] = key

    def _apply_maker_ebm_plan(
        self,
        cohort: str,
        decision: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
        allow_new_orders: bool,
    ) -> None:
        state = self.maker_ebm_states[cohort]
        desired_orders = decision.get("orders") if decision.get("decision") == "QUOTE" else []
        desired = {
            (str(order["side"]), int(order["priceTick"])): order
            for order in (desired_orders or [])
        }

        cancel_rows: list[tuple[int, str, str]] = []
        for key, order in list(state["orders"].items()):
            if key in desired:
                continue
            cancel_rows.append((int(now_ms), str(decision["reason"]), str(order["id"])))
            state["lastClosed"][key] = int(now_ms)
            state["orders"].pop(key, None)
        if cancel_rows:
            with self.db_lock:
                self.db.executemany(
                    """UPDATE wallet_maker_ebm_v1_orders
                          SET status='CANCELLED',closed_at_ms=?,close_reason=?
                        WHERE id=? AND status='ACTIVE'""",
                    cancel_rows,
                )
                self.db.commit()

        if not allow_new_orders:
            return
        inserts: list[tuple[Any, ...]] = []
        for key, requested in desired.items():
            if key in state["orders"]:
                continue
            if now_ms - int(state["lastClosed"].get(key, 0)) < maker_ebm.REFILL_COOLDOWN_MS:
                continue
            self.maker_ebm_sequence += 1
            order_id = f"{cohort}:{self.market_id}:{key[0]}:{key[1]}:{now_ms}:{self.maker_ebm_sequence}"
            order = {
                "id": order_id,
                "side": key[0],
                "priceTick": key[1],
                "price": float(requested["price"]),
                "shares": float(requested["shares"]),
                "origin": str(requested.get("origin") or cohort),
                "placedAtMs": int(now_ms),
                "placedSnapshotNs": int(snapshot_ns),
            }
            state["orders"][key] = order
            inserts.append((
                order_id, cohort, int(self.market_id), key[0], key[1], order["price"], order["shares"],
                order["origin"], int(now_ms), int(snapshot_ns), "ACTIVE",
            ))
        if inserts:
            with self.db_lock:
                self.db.executemany(
                    """INSERT INTO wallet_maker_ebm_v1_orders(
                           id,cohort,market_id,side,price_tick,price,shares,origin,
                           placed_at_ms,placed_snapshot_ns,status
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    inserts,
                )
                self.db.commit()

    def _advance_maker_ebm_cohort(
        self,
        cohort: str,
        snapshot: dict[str, Any],
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        state = self.maker_ebm_states[cohort]
        if not state.get("active") or self.market_id is None:
            return
        filled = self._fill_maker_ebm_orders(
            cohort, snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms
        )
        decision = maker_ebm.decide(
            snapshot,
            self.maker_ebm_models,
            self._maker_ebm_inventory(cohort),
            cohort=cohort,
            expected_market_id=int(self.market_id),
            now_ms=int(now_ms),
        )
        if filled:
            decision = {
                **decision,
                "postFillSameSnapshotNewOrdersBlocked": True,
                "filledThisSnapshot": filled,
            }
        self._record_maker_ebm_decision(
            cohort, decision, snapshot_ns=snapshot_ns, now_ms=now_ms,
            force=bool(filled),
        )
        self._apply_maker_ebm_plan(
            cohort, decision, snapshot_ns=snapshot_ns, now_ms=now_ms,
            allow_new_orders=not bool(filled),
        )
        self.pending_settlement_ids.add(int(self.market_id))

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        if not self.maker_ebm_schema_ready or self.market_id is None:
            return
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            return
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0:
            return
        now_ms = base._now_ms()
        for cohort in maker_ebm.COHORTS:
            self._advance_maker_ebm_cohort(
                cohort, snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms
            )

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.maker_ebm_schema_ready:
            self._seed_maker_ebm_pending_settlements()

    def _seed_maker_ebm_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_maker_ebm_v1_markets m
                     LEFT JOIN wallet_maker_ebm_v1_results r
                       ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.maker_ebm_schema_ready:
            return
        resolved_at_ms = base._now_ms()
        for cohort in maker_ebm.COHORTS:
            with self.db_lock:
                registered = self.db.execute(
                    "SELECT title FROM wallet_maker_ebm_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                if registered is None:
                    continue
                rows = [dict(row) for row in self.db.execute(
                    """SELECT side,price,shares FROM wallet_maker_ebm_v1_orders
                        WHERE cohort=? AND market_id=? AND status='FILLED'""",
                    (cohort, int(market_id)),
                )]
                fill_count = len(rows)
                up_shares = sum(float(row["shares"]) for row in rows if row["side"] == "UP")
                down_shares = sum(float(row["shares"]) for row in rows if row["side"] == "DOWN")
                cost = sum(float(row["price"]) * float(row["shares"]) for row in rows)
                payout = up_shares if winner == "UP" else down_shares
                pnl = payout - cost
                roi = pnl / cost if cost else None
                traded = int(fill_count > 0)
                status = "NO_FILL" if not traded else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
                total = up_shares + down_shares
                paired = 2.0 * min(up_shares, down_shares) / total if total > 1e-9 else None
                title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
                self.db.execute(
                    """INSERT INTO wallet_maker_ebm_v1_results(
                           cohort,market_id,title,winner,resolved_at_ms,traded,status,fill_count,
                           up_shares,down_shares,cost_usdt,payout_usdt,net_pnl_usdt,net_roi,paired_coverage
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(cohort,market_id) DO UPDATE SET
                           title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                           traded=excluded.traded,status=excluded.status,fill_count=excluded.fill_count,
                           up_shares=excluded.up_shares,down_shares=excluded.down_shares,cost_usdt=excluded.cost_usdt,
                           payout_usdt=excluded.payout_usdt,net_pnl_usdt=excluded.net_pnl_usdt,
                           net_roi=excluded.net_roi,paired_coverage=excluded.paired_coverage""",
                    (
                        cohort, int(market_id), title, winner, int(resolved_at_ms), traded, status,
                        fill_count, up_shares, down_shares, cost, payout, pnl, roi, paired,
                    ),
                )
                self.db.commit()

    def _maker_ebm_performance(self, cohort: str) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            row = dict(self.db.execute(
                """SELECT COUNT(*) settled,COALESCE(SUM(traded),0) traded,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                          COALESCE(SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END),0) losses,
                          COALESCE(SUM(fill_count),0) fills,COALESCE(SUM(cost_usdt),0) cost,
                          COALESCE(SUM(net_pnl_usdt),0) pnl
                     FROM wallet_maker_ebm_v1_results
                    WHERE cohort=? AND resolved_at_ms>=?""",
                (cohort, cutoff),
            ).fetchone())
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_maker_ebm_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (cohort, cutoff),
            ).fetchone()[0])
            recent = [dict(item) for item in self.db.execute(
                """SELECT market_id,winner,status,fill_count,up_shares,down_shares,cost_usdt,
                          net_pnl_usdt,net_roi,paired_coverage,resolved_at_ms
                     FROM wallet_maker_ebm_v1_results
                    WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms DESC LIMIT 20""",
                (cohort, cutoff),
            )]
        traded = int(row["traded"])
        wins = int(row["wins"])
        cost = float(row["cost"])
        pnl = float(row["pnl"])
        paired_values = [float(item["paired_coverage"]) for item in recent if item["paired_coverage"] is not None]
        return {
            "markets": markets,
            "settledMarkets": int(row["settled"]),
            "pendingMarkets": max(0, markets - int(row["settled"])),
            "tradedMarkets": traded,
            "wins": wins,
            "losses": int(row["losses"]),
            "winRate": wins / traded if traded else None,
            "fills": int(row["fills"]),
            "costUsdt": cost,
            "netPnlUsdt": pnl,
            "netRoi": pnl / cost if cost else None,
            "recentPairedCoverageMean": sum(paired_values) / len(paired_values) if paired_values else None,
            "recentMarkets": recent,
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.maker_ebm_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute("DELETE FROM wallet_maker_ebm_v1_decisions WHERE decision_at_ms<?", (cutoff,))
            self.db.execute(
                "DELETE FROM wallet_maker_ebm_v1_orders WHERE placed_at_ms<? AND status!='ACTIVE'",
                (cutoff,),
            )
            self.db.execute("DELETE FROM wallet_maker_ebm_v1_results WHERE resolved_at_ms<?", (cutoff,))
            self.db.execute(
                "DELETE FROM wallet_maker_ebm_v1_markets WHERE started_at_ms<? AND market_id!=?",
                (cutoff, int(self.market_id or -1)),
            )
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        cohorts: dict[str, Any] = {}
        for cohort in maker_ebm.COHORTS:
            state = self.maker_ebm_states[cohort]
            inventory_state = self._maker_ebm_inventory(cohort)
            cohorts[cohort] = {
                "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
                "deploymentBoundaryMs": state["deployedAtMs"],
                "excludedDeploymentMarketId": state["excludedMarketId"],
                "activeOrders": len(state["orders"]),
                "orders": list(state["orders"].values()),
                "inventory": inventory_state,
                "fillCount": state["fillCount"],
                "lastFill": state["lastFill"],
                "lastDecision": state["lastDecision"],
                "performance": self._maker_ebm_performance(cohort),
            }
        payload["makerEbmV1Lab"] = {
            "version": maker_ebm.VERSION,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "models": {
                "loaded": self.maker_ebm_models is not None,
                "hazardPath": self.maker_ebm_models.get("hazard", {}).get("path") if self.maker_ebm_models else str(maker_ebm.HAZARD_MODEL_PATH),
                "levelPath": self.maker_ebm_models.get("level", {}).get("path") if self.maker_ebm_models else str(maker_ebm.LEVEL_MODEL_PATH),
                "error": self.maker_ebm_model_error,
            },
            "policy": maker_ebm.policy(),
            "cohorts": cohorts,
            "abTest": {
                "hazardOnly": maker_ebm.HAZARD_ONLY_COHORT,
                "levelOnly": maker_ebm.LEVEL_ONLY_COHORT,
                "combined": maker_ebm.COMBINED_COHORT,
                "question": "Does forward Maker PnL improve because EBM controls WHEN, WHERE, or only their combination?",
            },
            "evidenceBoundary": "Hazard and level probabilities come from frozen strict-pre inferred-placement EBM artifacts. Side is controlled only by each cohort's own paper inventory; Target events never trigger quotes.",
            "promotion": "research-only cohorts absent from live allowlists; no automatic promotion",
        }
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload["makerEbmV1"] = list(maker_ebm.COHORTS)
        payload["makerEbmV1ModelsLoaded"] = self.maker_ebm_models is not None
        payload["makerEbmV1ModelError"] = self.maker_ebm_model_error
        payload["paperOnly"] = True
        payload["liveOrdersAffected"] = False
        return payload


class _Handler(v4_19._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_20Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"MakerEBM={','.join(maker_ebm.COHORTS)}; modelsLoaded={observer.maker_ebm_models is not None}; "
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
