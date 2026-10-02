from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_7 as v4_7
from . import predict_wallet_wide_maker_flow_strategy as strategy


VERSION = "PREDICT_WALLET_SHADOW_V0_11_WIDE_MAKER_FLOW_TAIL_PAPER"


class WalletShadowObserver(v4_7.WalletShadowObserver):
    """Wide resting Maker rails plus causal Maker-flow Taker and tail insurance paper lab."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_7.v4_6.v4_5.v4_4.v4_3.v4_2.SIMULATION_DB_PATH) -> None:
        self.wide_schema_ready = False
        self.wide_states: dict[str, dict[str, Any]] = {}
        self.wide_sequence = 0
        self.full_snapshot_lock = threading.Lock()
        self.full_snapshot_cache: tuple[int, dict[str, Any]] | None = None
        self.full_snapshot_cache_ttl_ms = 8_000
        super().__init__(db_path, simulation_db_path)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_wide_maker_flow_v1_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_wide_maker_flow_v1_markets (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL,
                    initialization_status TEXT NOT NULL,
                    initialized_at_ms INTEGER,
                    initial_reserved_usdt REAL NOT NULL DEFAULT 0,
                    peak_reserved_usdt REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_wide_maker_flow_v1_orders (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    price_tick INTEGER NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    placed_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    closed_at_ms INTEGER,
                    close_reason TEXT,
                    fill_ask REAL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    is_refill INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_wide_maker_flow_v1_orders_market
                    ON wallet_wide_maker_flow_v1_orders(cohort,market_id,status,placed_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_wide_maker_flow_v1_decisions (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision_kind TEXT NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(cohort,market_id,snapshot_timestamp_ns,decision_kind)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_wide_maker_flow_v1_decisions_time
                    ON wallet_wide_maker_flow_v1_decisions(cohort,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_wide_maker_flow_v1_taker_events (
                    id TEXT PRIMARY KEY,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    signal_at_ms INTEGER,
                    decision_at_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    principal_usdt REAL NOT NULL,
                    fee_usdt REAL NOT NULL,
                    total_cost_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_wide_maker_flow_v1_taker_market
                    ON wallet_wide_maker_flow_v1_taker_events(cohort,market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS wallet_wide_maker_flow_v1_results (
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    maker_fill_count INTEGER NOT NULL,
                    primary_fill_count INTEGER NOT NULL,
                    insurance_fill_count INTEGER NOT NULL,
                    maker_cost_usdt REAL NOT NULL,
                    maker_payout_usdt REAL NOT NULL,
                    maker_pnl_usdt REAL NOT NULL,
                    primary_cost_usdt REAL NOT NULL,
                    primary_payout_usdt REAL NOT NULL,
                    primary_pnl_usdt REAL NOT NULL,
                    insurance_cost_usdt REAL NOT NULL,
                    insurance_payout_usdt REAL NOT NULL,
                    insurance_pnl_usdt REAL NOT NULL,
                    without_insurance_pnl_usdt REAL NOT NULL,
                    total_cost_usdt REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL,
                    stress_1tick_pnl_usdt REAL NOT NULL,
                    stress_1tick_roi REAL,
                    stress_2tick_pnl_usdt REAL NOT NULL,
                    stress_2tick_roi REAL,
                    initial_reserved_usdt REAL NOT NULL,
                    peak_reserved_usdt REAL NOT NULL,
                    PRIMARY KEY(cohort,market_id)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_wide_maker_flow_v1_results_time
                    ON wallet_wide_maker_flow_v1_results(cohort,resolved_at_ms);
                """
            )
            now_ms = base._now_ms()
            for variant in strategy.COHORTS:
                cohort = str(variant["cohort"])
                row = self.db.execute(
                    "SELECT deployed_at_ms,excluded_market_id FROM wallet_wide_maker_flow_v1_meta WHERE cohort=?",
                    (cohort,),
                ).fetchone()
                if row is None:
                    deployed = now_ms
                    excluded = None
                    self.db.execute(
                        "INSERT INTO wallet_wide_maker_flow_v1_meta VALUES (?,?,?,?)",
                        (cohort, deployed, None, json.dumps(strategy.policy(variant), separators=(",", ":"))),
                    )
                else:
                    deployed = int(row["deployed_at_ms"])
                    excluded = int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None
                self.wide_states[cohort] = self._empty_wide_state(deployed, excluded)
            self.db.commit()
        self.wide_schema_ready = True
        self._seed_wide_pending_settlements()

    @staticmethod
    def _empty_wide_state(deployed: int, excluded: int | None) -> dict[str, Any]:
        return {
            "active": False,
            "deployedAtMs": deployed,
            "excludedMarketId": excluded,
            "initializationStatus": "WAITING",
            "initialized": False,
            "openingMissed": False,
            "lastSnapshotNs": None,
            "orders": {},
            "initialPriceKeys": set(),
            "lastClosed": {},
            "makerFlowEvents": [],
            "makerUpShares": 0.0,
            "makerDownShares": 0.0,
            "makerUpCost": 0.0,
            "makerDownCost": 0.0,
            "primaryUpShares": 0.0,
            "primaryDownShares": 0.0,
            "primaryUpPrincipal": 0.0,
            "primaryDownPrincipal": 0.0,
            "tailUpShares": 0.0,
            "tailDownShares": 0.0,
            "tailPrincipal": 0.0,
            "tailParents": 0,
            "lastPrimaryTradeMs": None,
            "lastTailTradeMs": None,
            "lastPrimaryDecision": None,
            "lastTailDecision": None,
            "initialReservedUsdt": 0.0,
            "peakReservedUsdt": 0.0,
        }

    @staticmethod
    def _drawdown(values: list[float]) -> float:
        equity = peak = max_drawdown = 0.0
        for value in values:
            equity += float(value)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return max_drawdown

    def _wide_inventory(self, state: dict[str, Any]) -> dict[str, Any]:
        return strategy.inventory_state(
            maker_up_shares=state["makerUpShares"],
            maker_down_shares=state["makerDownShares"],
            maker_up_cost=state["makerUpCost"],
            maker_down_cost=state["makerDownCost"],
            primary_up_shares=state["primaryUpShares"],
            primary_down_shares=state["primaryDownShares"],
            primary_up_principal=state["primaryUpPrincipal"],
            primary_down_principal=state["primaryDownPrincipal"],
            tail_up_shares=state["tailUpShares"],
            tail_down_shares=state["tailDownShares"],
            tail_principal=state["tailPrincipal"],
        )

    def _cancel_wide_orders(self, cohort: str, reason: str, now_ms: int) -> None:
        state = self.wide_states[cohort]
        ids = [str(order["id"]) for order in state["orders"].values()]
        if ids:
            with self.db_lock:
                self.db.executemany(
                    "UPDATE wallet_wide_maker_flow_v1_orders SET status='CANCELLED',closed_at_ms=?,close_reason=? WHERE id=? AND status='ACTIVE'",
                    [(now_ms, reason, order_id) for order_id in ids],
                )
                self.db.commit()
        state["orders"] = {}

    def _place_wide_orders(
        self,
        cohort: str,
        orders: list[dict[str, Any]],
        snapshot_ns: int,
        now_ms: int,
        *,
        is_refill: bool,
    ) -> None:
        state = self.wide_states[cohort]
        pending = []
        for order in orders:
            key = (str(order["side"]), int(order["priceTick"]))
            if key in state["orders"]:
                continue
            self.wide_sequence += 1
            order_id = f"{cohort}:{self.market_id}:{key[0]}:{key[1]}:{now_ms}:{self.wide_sequence}"
            stored = {
                "id": order_id,
                "side": key[0],
                "priceTick": key[1],
                "price": float(order["price"]),
                "shares": float(order["shares"]),
                "placed_at_ms": now_ms,
                "status": "ACTIVE",
                "isRefill": bool(is_refill),
            }
            state["orders"][key] = stored
            pending.append((
                order_id, cohort, int(self.market_id), key[0], key[1], stored["price"], stored["shares"],
                now_ms, "ACTIVE", snapshot_ns, int(is_refill),
            ))
        if pending:
            with self.db_lock:
                self.db.executemany(
                    """INSERT INTO wallet_wide_maker_flow_v1_orders(
                           id,cohort,market_id,side,price_tick,price,shares,placed_at_ms,status,snapshot_timestamp_ns,is_refill
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    pending,
                )
                self.db.commit()

    def _update_reservation(self, cohort: str) -> None:
        state = self.wide_states[cohort]
        current = strategy.reserved_capital(list(state["orders"].values()))
        state["peakReservedUsdt"] = max(float(state["peakReservedUsdt"]), current)
        with self.db_lock:
            self.db.execute(
                "UPDATE wallet_wide_maker_flow_v1_markets SET peak_reserved_usdt=? WHERE cohort=? AND market_id=?",
                (state["peakReservedUsdt"], cohort, int(self.market_id)),
            )
            self.db.commit()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        previous_market = self.market_id
        if self.wide_schema_ready and previous_market is not None:
            for variant in strategy.COHORTS:
                self._cancel_wide_orders(str(variant["cohort"]), "MARKET_ROLLOVER", base._now_ms())
        super()._reset_market(market_id, bucket, title)
        if not self.wide_schema_ready:
            return
        now_ms = base._now_ms()
        with self.db_lock:
            for variant in strategy.COHORTS:
                cohort = str(variant["cohort"])
                state = self.wide_states[cohort]
                if state["excludedMarketId"] is None:
                    state["excludedMarketId"] = int(market_id)
                    self.db.execute(
                        "UPDATE wallet_wide_maker_flow_v1_meta SET excluded_market_id=? WHERE cohort=?",
                        (int(market_id), cohort),
                    )
                market_row = self.db.execute(
                    "SELECT * FROM wallet_wide_maker_flow_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                clean = self._empty_wide_state(state["deployedAtMs"], state["excludedMarketId"])
                clean["active"] = market_row is not None or int(market_id) != state["excludedMarketId"]
                state.clear()
                state.update(clean)
                if not state["active"]:
                    continue
                self.db.execute(
                    """INSERT OR IGNORE INTO wallet_wide_maker_flow_v1_markets(
                           cohort,market_id,title,started_at_ms,initialization_status,initial_reserved_usdt,peak_reserved_usdt
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (cohort, int(market_id), title, now_ms, "WAITING_OPEN", 0.0, 0.0),
                )
                market_row = self.db.execute(
                    "SELECT * FROM wallet_wide_maker_flow_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                status = str(market_row["initialization_status"])
                state["initializationStatus"] = status
                state["initialized"] = status == "INITIALIZED"
                state["openingMissed"] = status == "MISSED_OPENING_WINDOW"
                state["initialReservedUsdt"] = float(market_row["initial_reserved_usdt"])
                state["peakReservedUsdt"] = float(market_row["peak_reserved_usdt"])
                orders = [dict(row) for row in self.db.execute(
                    "SELECT * FROM wallet_wide_maker_flow_v1_orders WHERE cohort=? AND market_id=? ORDER BY placed_at_ms,id",
                    (cohort, int(market_id)),
                )]
                for order in orders:
                    key = (str(order["side"]), int(order["price_tick"]))
                    state["initialPriceKeys"].add(key)
                    if order["status"] == "ACTIVE":
                        state["orders"][key] = {
                            "id": order["id"], "side": order["side"], "priceTick": order["price_tick"],
                            "price": order["price"], "shares": order["shares"],
                            "placed_at_ms": order["placed_at_ms"], "status": "ACTIVE",
                            "isRefill": bool(order["is_refill"]),
                        }
                    elif order["status"] == "FILLED":
                        side_title = str(order["side"]).title()
                        state[f"maker{side_title}Shares"] += float(order["shares"])
                        state[f"maker{side_title}Cost"] += float(order["shares"]) * float(order["price"])
                        state["makerFlowEvents"].append({
                            "side": order["side"], "shares": order["shares"], "filledAtMs": order["closed_at_ms"],
                        })
                    if order["closed_at_ms"] is not None:
                        state["lastClosed"][key] = max(
                            int(order["closed_at_ms"]), int(state["lastClosed"].get(key, 0))
                        )
                events = [dict(row) for row in self.db.execute(
                    "SELECT * FROM wallet_wide_maker_flow_v1_taker_events WHERE cohort=? AND market_id=? ORDER BY decision_at_ms,id",
                    (cohort, int(market_id)),
                )]
                for event in events:
                    side_title = str(event["side"]).title()
                    if event["event_kind"] == "PRIMARY":
                        state[f"primary{side_title}Shares"] += float(event["shares"])
                        state[f"primary{side_title}Principal"] += float(event["principal_usdt"])
                        state["lastPrimaryTradeMs"] = int(event["decision_at_ms"])
                    else:
                        state[f"tail{side_title}Shares"] += float(event["shares"])
                        state["tailPrincipal"] += float(event["principal_usdt"])
                        state["tailParents"] += 1
                        state["lastTailTradeMs"] = int(event["decision_at_ms"])
            self.db.commit()

    def _store_decision(
        self, cohort: str, snapshot_ns: int, kind: str, now_ms: int, decision: dict[str, Any]
    ) -> None:
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_wide_maker_flow_v1_decisions(
                       cohort,market_id,snapshot_timestamp_ns,decision_kind,decision_at_ms,decision,reason,side,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    cohort, int(self.market_id), snapshot_ns, kind, now_ms, decision["decision"],
                    decision["reason"], decision.get("side"),
                    json.dumps(decision, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _store_taker_event(
        self,
        cohort: str,
        snapshot_ns: int,
        kind: str,
        now_ms: int,
        decision: dict[str, Any],
        fill: dict[str, float],
    ) -> None:
        side = str(decision["side"])
        event_id = f"{cohort}:{self.market_id}:{kind}:{snapshot_ns}"
        payload = {
            "id": event_id, "cohort": cohort, "marketId": self.market_id, "eventKind": kind,
            "snapshotTimestampNs": snapshot_ns, "decisionAtMs": now_ms, "side": side,
            **fill, "decision": decision, "paperOnly": True, "targetEventsUsed": False,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO wallet_wide_maker_flow_v1_taker_events(
                       id,cohort,market_id,event_kind,snapshot_timestamp_ns,signal_at_ms,decision_at_ms,side,
                       observed_ask,principal_usdt,fee_usdt,total_cost_usdt,shares,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, cohort, int(self.market_id), kind, snapshot_ns, decision.get("signalAtMs"), now_ms,
                    side, fill["ask"], fill["principalUsdt"], fill["feeUsdt"], fill["totalCostUsdt"],
                    fill["shares"], json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _advance_wide_cohorts(self) -> None:
        snapshot = self.latest_public_signal_snapshot
        if not self.wide_schema_ready or self.market_id is None or not isinstance(snapshot, dict):
            return
        now_ms = base._now_ms()
        snapshot_ns = int(float(snapshot.get("timestamp_ns") or 0))
        if snapshot_ns <= 0:
            return
        usable, _ = strategy.snapshot_is_usable(snapshot, market_id=int(self.market_id), now_ms=now_ms)
        if not usable:
            return
        seconds_left = float(snapshot.get("seconds_left") or 0.0)

        for variant in strategy.COHORTS:
            cohort = str(variant["cohort"])
            state = self.wide_states[cohort]
            if not state["active"] or state["lastSnapshotNs"] == snapshot_ns:
                continue
            state["lastSnapshotNs"] = snapshot_ns

            if not state["initialized"] and not state["openingMissed"]:
                if seconds_left < strategy.OPENING_MIN_SECONDS_LEFT:
                    state["openingMissed"] = True
                    state["initializationStatus"] = "MISSED_OPENING_WINDOW"
                    with self.db_lock:
                        self.db.execute(
                            "UPDATE wallet_wide_maker_flow_v1_markets SET initialization_status='MISSED_OPENING_WINDOW' WHERE cohort=? AND market_id=?",
                            (cohort, int(self.market_id)),
                        )
                        self.db.commit()
                else:
                    opening_orders = strategy.opening_grid(snapshot)
                    state["initialPriceKeys"] = {
                        (str(order["side"]), int(order["priceTick"])) for order in opening_orders
                    }
                    self._place_wide_orders(cohort, opening_orders, snapshot_ns, now_ms, is_refill=False)
                    state["initialized"] = True
                    state["initializationStatus"] = "INITIALIZED"
                    state["initialReservedUsdt"] = strategy.reserved_capital(list(state["orders"].values()))
                    state["peakReservedUsdt"] = state["initialReservedUsdt"]
                    with self.db_lock:
                        self.db.execute(
                            """UPDATE wallet_wide_maker_flow_v1_markets SET initialization_status='INITIALIZED',
                                   initialized_at_ms=?,initial_reserved_usdt=?,peak_reserved_usdt=?
                               WHERE cohort=? AND market_id=?""",
                            (
                                now_ms, state["initialReservedUsdt"], state["peakReservedUsdt"],
                                cohort, int(self.market_id),
                            ),
                        )
                        self.db.commit()

            if not state["initialized"]:
                continue

            filled_keys: list[tuple[str, int]] = []
            for key, order in list(state["orders"].items()):
                if now_ms - int(order["placed_at_ms"]) < strategy.MIN_REST_MS:
                    continue
                ask = float(snapshot.get(f"predict_{str(order['side']).lower()}_ask") or 0.0)
                if ask <= 0 or ask > float(order["price"]) + 1e-9:
                    continue
                with self.db_lock:
                    self.db.execute(
                        """UPDATE wallet_wide_maker_flow_v1_orders SET status='FILLED',closed_at_ms=?,
                               close_reason='STRICT_ASK_TOUCH_PROXY',fill_ask=? WHERE id=? AND status='ACTIVE'""",
                        (now_ms, ask, order["id"]),
                    )
                    self.db.commit()
                state["orders"].pop(key, None)
                state["lastClosed"][key] = now_ms
                state["makerFlowEvents"].append({"side": order["side"], "shares": order["shares"], "filledAtMs": now_ms})
                side_title = str(order["side"]).title()
                state[f"maker{side_title}Shares"] += float(order["shares"])
                state[f"maker{side_title}Cost"] += float(order["shares"]) * float(order["price"])
                filled_keys.append(key)

            state["makerFlowEvents"] = [
                event for event in state["makerFlowEvents"]
                if now_ms - int(event.get("filledAtMs") or 0) <= strategy.FLOW_MAX_AGE_MS
            ]
            refill_orders = []
            for side, price_tick in state["initialPriceKeys"]:
                key = (side, price_tick)
                if key in state["orders"] or key not in state["lastClosed"]:
                    continue
                if now_ms - int(state["lastClosed"][key]) < strategy.REFILL_COOLDOWN_MS:
                    continue
                allowed, _ = strategy.may_refill(
                    variant, side=side, price_tick=price_tick, snapshot=snapshot
                )
                if allowed:
                    refill_orders.append({
                        "side": side, "priceTick": price_tick, "price": round(price_tick * strategy.GRID, 2),
                        "shares": strategy.SHARES_PER_ORDER,
                    })
            self._place_wide_orders(cohort, refill_orders, snapshot_ns, now_ms, is_refill=True)
            self._update_reservation(cohort)

            inventory = self._wide_inventory(state)
            flow = strategy.maker_flow(state["makerFlowEvents"], now_ms=now_ms)
            primary = strategy.primary_decision(
                snapshot, inventory, flow, expected_market_id=int(self.market_id), now_ms=now_ms,
                last_trade_ms=state["lastPrimaryTradeMs"],
            )
            primary.update({"cohort": cohort, "marketId": self.market_id, "paperOnly": True, "targetEventsUsed": False})
            self._store_decision(cohort, snapshot_ns, "PRIMARY", now_ms, primary)
            state["lastPrimaryDecision"] = primary
            if primary["decision"] == "TRADE" and primary.get("side") in {"UP", "DOWN"}:
                fill = strategy.primary_execution(str(primary["side"]), snapshot, float(primary["sizeScale"]))
                if fill is not None:
                    self._store_taker_event(cohort, snapshot_ns, "PRIMARY", now_ms, primary, fill)
                    side_title = str(primary["side"]).title()
                    state[f"primary{side_title}Shares"] += float(fill["shares"])
                    state[f"primary{side_title}Principal"] += float(fill["principalUsdt"])
                    state["lastPrimaryTradeMs"] = now_ms

            inventory = self._wide_inventory(state)
            tail = strategy.tail_decision(
                snapshot, inventory, expected_market_id=int(self.market_id), now_ms=now_ms,
                last_trade_ms=state["lastTailTradeMs"], tail_parents=state["tailParents"],
            )
            tail.update({"cohort": cohort, "marketId": self.market_id, "paperOnly": True, "targetEventsUsed": False})
            self._store_decision(cohort, snapshot_ns, "TAIL_INSURANCE", now_ms, tail)
            state["lastTailDecision"] = tail
            if tail["decision"] == "TRADE" and tail.get("side") in {"UP", "DOWN"}:
                fill = strategy.tail_execution(str(tail["side"]), snapshot)
                if fill is not None:
                    self._store_taker_event(cohort, snapshot_ns, "TAIL_INSURANCE", now_ms, tail, fill)
                    side_title = str(tail["side"]).title()
                    state[f"tail{side_title}Shares"] += float(fill["shares"])
                    state["tailPrincipal"] += float(fill["principalUsdt"])
                    state["tailParents"] += 1
                    state["lastTailTradeMs"] = now_ms

    def _advance_shadow(self, book: dict[str, Any], core: dict[str, Any]) -> None:
        super()._advance_shadow(book, core)
        self._advance_wide_cohorts()

    def _seed_pending_settlements(self) -> None:
        super()._seed_pending_settlements()
        if self.wide_schema_ready:
            self._seed_wide_pending_settlements()

    def _seed_wide_pending_settlements(self) -> None:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            rows = self.db.execute(
                """SELECT DISTINCT m.market_id FROM wallet_wide_maker_flow_v1_markets m
                     LEFT JOIN wallet_wide_maker_flow_v1_results r ON r.cohort=m.cohort AND r.market_id=m.market_id
                    WHERE m.started_at_ms>=? AND r.market_id IS NULL""",
                (cutoff,),
            ).fetchall()
        self.pending_settlement_ids.update(int(row[0]) for row in rows)

    def _store_market_result(self, market_id: int, market: dict[str, Any], winner: str) -> None:
        super()._store_market_result(market_id, market, winner)
        if not self.wide_schema_ready:
            return
        with self.db_lock:
            for variant in strategy.COHORTS:
                cohort = str(variant["cohort"])
                registered = self.db.execute(
                    "SELECT * FROM wallet_wide_maker_flow_v1_markets WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                ).fetchone()
                if registered is None:
                    continue
                maker = [dict(row) for row in self.db.execute(
                    "SELECT side,price,shares FROM wallet_wide_maker_flow_v1_orders WHERE cohort=? AND market_id=? AND status='FILLED'",
                    (cohort, int(market_id)),
                )]
                taker = [dict(row) for row in self.db.execute(
                    "SELECT * FROM wallet_wide_maker_flow_v1_taker_events WHERE cohort=? AND market_id=?",
                    (cohort, int(market_id)),
                )]
                primary = [row for row in taker if row["event_kind"] == "PRIMARY"]
                insurance = [row for row in taker if row["event_kind"] == "TAIL_INSURANCE"]

                maker_cost = sum(float(row["price"]) * float(row["shares"]) for row in maker)
                maker_payout = sum(float(row["shares"]) for row in maker if row["side"] == winner)
                maker_pnl = maker_payout - maker_cost

                def leg(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
                    cost = sum(float(row["total_cost_usdt"]) for row in rows)
                    payout = sum(float(row["shares"]) for row in rows if row["side"] == winner)
                    return cost, payout, payout - cost

                primary_cost, primary_payout, primary_pnl = leg(primary)
                insurance_cost, insurance_payout, insurance_pnl = leg(insurance)
                without_insurance = maker_pnl + primary_pnl
                total_cost = maker_cost + primary_cost + insurance_cost
                payout = maker_payout + primary_payout + insurance_payout
                pnl = payout - total_cost
                stress_pnls = []
                for ticks in (1, 2):
                    stressed_taker_payout = sum(
                        float(row["principal_usdt"]) / min(0.99, float(row["observed_ask"]) + ticks * strategy.GRID)
                        for row in taker if row["side"] == winner
                    )
                    stress_pnls.append(maker_payout + stressed_taker_payout - total_cost)
                traded = bool(maker or taker)
                status = "NO_TRADE" if not traded else "WIN" if pnl > 1e-9 else "LOSS" if pnl < -1e-9 else "FLAT"
                title = str(market.get("title") or market.get("question") or registered["title"] or "") or None
                values = (
                    cohort, int(market_id), title, winner, base._now_ms(), int(traded), status,
                    len(maker), len(primary), len(insurance), maker_cost, maker_payout, maker_pnl,
                    primary_cost, primary_payout, primary_pnl, insurance_cost, insurance_payout, insurance_pnl,
                    without_insurance, total_cost, payout, pnl, pnl / total_cost if total_cost else None,
                    stress_pnls[0], stress_pnls[0] / total_cost if total_cost else None,
                    stress_pnls[1], stress_pnls[1] / total_cost if total_cost else None,
                    float(registered["initial_reserved_usdt"]), float(registered["peak_reserved_usdt"]),
                )
                self.db.execute(
                    """INSERT INTO wallet_wide_maker_flow_v1_results (
                           cohort,market_id,title,winner,resolved_at_ms,traded,status,
                           maker_fill_count,primary_fill_count,insurance_fill_count,
                           maker_cost_usdt,maker_payout_usdt,maker_pnl_usdt,
                           primary_cost_usdt,primary_payout_usdt,primary_pnl_usdt,
                           insurance_cost_usdt,insurance_payout_usdt,insurance_pnl_usdt,
                           without_insurance_pnl_usdt,total_cost_usdt,payout_usdt,net_pnl_usdt,net_roi,
                           stress_1tick_pnl_usdt,stress_1tick_roi,stress_2tick_pnl_usdt,stress_2tick_roi,
                           initial_reserved_usdt,peak_reserved_usdt
                       ) VALUES (
                           ?,?,?,?,?,?,?,?,?,?,
                           ?,?,?,?,?,?,?,?,?,?,
                           ?,?,?,?,?,?,?,?,?,?
                       ) ON CONFLICT(cohort,market_id) DO UPDATE SET
                           title=excluded.title,winner=excluded.winner,resolved_at_ms=excluded.resolved_at_ms,
                           traded=excluded.traded,status=excluded.status,maker_fill_count=excluded.maker_fill_count,
                           primary_fill_count=excluded.primary_fill_count,insurance_fill_count=excluded.insurance_fill_count,
                           maker_cost_usdt=excluded.maker_cost_usdt,maker_payout_usdt=excluded.maker_payout_usdt,
                           maker_pnl_usdt=excluded.maker_pnl_usdt,primary_cost_usdt=excluded.primary_cost_usdt,
                           primary_payout_usdt=excluded.primary_payout_usdt,primary_pnl_usdt=excluded.primary_pnl_usdt,
                           insurance_cost_usdt=excluded.insurance_cost_usdt,insurance_payout_usdt=excluded.insurance_payout_usdt,
                           insurance_pnl_usdt=excluded.insurance_pnl_usdt,without_insurance_pnl_usdt=excluded.without_insurance_pnl_usdt,
                           total_cost_usdt=excluded.total_cost_usdt,payout_usdt=excluded.payout_usdt,
                           net_pnl_usdt=excluded.net_pnl_usdt,net_roi=excluded.net_roi,
                           stress_1tick_pnl_usdt=excluded.stress_1tick_pnl_usdt,stress_1tick_roi=excluded.stress_1tick_roi,
                           stress_2tick_pnl_usdt=excluded.stress_2tick_pnl_usdt,stress_2tick_roi=excluded.stress_2tick_roi,
                           initial_reserved_usdt=excluded.initial_reserved_usdt,peak_reserved_usdt=excluded.peak_reserved_usdt""",
                    values,
                )
            self.db.commit()

    def _wide_performance(self, cohort: str) -> dict[str, Any]:
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            results = [dict(row) for row in self.db.execute(
                "SELECT * FROM wallet_wide_maker_flow_v1_results WHERE cohort=? AND resolved_at_ms>=? ORDER BY resolved_at_ms",
                (cohort, cutoff),
            )]
            markets = int(self.db.execute(
                "SELECT COUNT(*) FROM wallet_wide_maker_flow_v1_markets WHERE cohort=? AND started_at_ms>=?",
                (cohort, cutoff),
            ).fetchone()[0])
            decision_rows = [dict(row) for row in self.db.execute(
                """SELECT decision_kind,decision,reason,COUNT(*) count FROM wallet_wide_maker_flow_v1_decisions
                    WHERE cohort=? AND decision_at_ms>=? GROUP BY decision_kind,decision,reason
                    ORDER BY decision_kind,decision,reason""",
                (cohort, cutoff),
            )]
        traded = [row for row in results if int(row["traded"])]
        total_cost = sum(float(row["total_cost_usdt"]) for row in results)
        net_pnl = sum(float(row["net_pnl_usdt"]) for row in results)
        insurance_pnl = sum(float(row["insurance_pnl_usdt"]) for row in results)
        dd = self._drawdown([float(row["net_pnl_usdt"]) for row in results])
        dd_without = self._drawdown([float(row["without_insurance_pnl_usdt"]) for row in results])
        return {
            "markets": markets,
            "settledMarkets": len(results),
            "pendingMarkets": max(0, markets - len(results)),
            "tradedMarkets": len(traded),
            "wins": sum(row["status"] == "WIN" for row in traded),
            "losses": sum(row["status"] == "LOSS" for row in traded),
            "winRate": sum(row["status"] == "WIN" for row in traded) / len(traded) if traded else None,
            "makerFills": sum(int(row["maker_fill_count"]) for row in results),
            "primaryTakerFills": sum(int(row["primary_fill_count"]) for row in results),
            "insuranceFills": sum(int(row["insurance_fill_count"]) for row in results),
            "makerCostUsdt": sum(float(row["maker_cost_usdt"]) for row in results),
            "makerPnlUsdt": sum(float(row["maker_pnl_usdt"]) for row in results),
            "primaryCostUsdt": sum(float(row["primary_cost_usdt"]) for row in results),
            "primaryPnlUsdt": sum(float(row["primary_pnl_usdt"]) for row in results),
            "insuranceCostUsdt": sum(float(row["insurance_cost_usdt"]) for row in results),
            "insurancePayoutUsdt": sum(float(row["insurance_payout_usdt"]) for row in results),
            "insurancePnlUsdt": insurance_pnl,
            "withoutInsurancePnlUsdt": sum(float(row["without_insurance_pnl_usdt"]) for row in results),
            "totalCostUsdt": total_cost,
            "netPnlUsdt": net_pnl,
            "netRoi": net_pnl / total_cost if total_cost else None,
            "stress1TickRoi": sum(float(row["stress_1tick_pnl_usdt"]) for row in results) / total_cost if total_cost else None,
            "stress2TickRoi": sum(float(row["stress_2tick_pnl_usdt"]) for row in results) / total_cost if total_cost else None,
            "maxDrawdownUsdt": dd,
            "maxDrawdownWithoutInsuranceUsdt": dd_without,
            "insuranceDrawdownImprovementUsdt": dd_without - dd,
            "averageInitialReservedUsdt": (
                sum(float(row["initial_reserved_usdt"]) for row in results) / len(results) if results else None
            ),
            "maximumPeakReservedUsdt": max((float(row["peak_reserved_usdt"]) for row in results), default=None),
            "decisionBreakdown": decision_rows,
            "recentMarkets": list(reversed(results[-20:])),
        }

    def _cleanup_retention(self, *, force: bool = False) -> None:
        super()._cleanup_retention(force=force)
        if not self.wide_schema_ready:
            return
        cutoff = base._now_ms() - self.retention_ms
        with self.db_lock:
            self.db.execute("DELETE FROM wallet_wide_maker_flow_v1_results WHERE resolved_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_wide_maker_flow_v1_decisions WHERE decision_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_wide_maker_flow_v1_taker_events WHERE decision_at_ms<?", (cutoff,))
            self.db.execute("DELETE FROM wallet_wide_maker_flow_v1_orders WHERE placed_at_ms<? AND status!='ACTIVE'", (cutoff,))
            self.db.execute(
                "DELETE FROM wallet_wide_maker_flow_v1_markets WHERE started_at_ms<? AND market_id NOT IN (SELECT market_id FROM wallet_wide_maker_flow_v1_orders)",
                (cutoff,),
            )
            self.db.commit()

    def _uncached_snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        variants = []
        for variant in strategy.COHORTS:
            cohort = str(variant["cohort"])
            state = self.wide_states[cohort]
            inventory = self._wide_inventory(state)
            current_reserved = strategy.reserved_capital(list(state["orders"].values()))
            variants.append({
                **variant,
                "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
                "deploymentBoundaryMs": state["deployedAtMs"],
                "excludedDeploymentMarketId": state["excludedMarketId"],
                "policy": strategy.policy(variant),
                "current": {
                    "marketId": self.market_id,
                    "initializationStatus": state["initializationStatus"],
                    "activeOrders": len(state["orders"]),
                    "upOrders": sum(order["side"] == "UP" for order in state["orders"].values()),
                    "downOrders": sum(order["side"] == "DOWN" for order in state["orders"].values()),
                    "initialPriceLevels": len(state["initialPriceKeys"]),
                    "currentReservedUsdt": current_reserved,
                    "initialReservedUsdt": state["initialReservedUsdt"],
                    "peakReservedUsdt": state["peakReservedUsdt"],
                    "inventory": inventory,
                    "makerFlow": strategy.maker_flow(state["makerFlowEvents"], now_ms=base._now_ms()),
                    "lastPrimaryDecision": state["lastPrimaryDecision"],
                    "lastTailDecision": state["lastTailDecision"],
                    "tailParents": state["tailParents"],
                },
                "performance": self._wide_performance(cohort),
            })
        payload["wideMakerFlowTailLab"] = {
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "hypothesis": "Wide resting Maker rails create a short-lived fill-flow alpha; cumulative inventory gates Taker risk and cheap opposite tail shares are separately budgeted insurance.",
            "evidenceBoundary": "Rules frozen from pre-deployment target structural analysis; only public market data and this paper strategy's own prior fills drive decisions.",
            "variants": variants,
        }
        return payload

    def snapshot(self) -> dict[str, Any]:
        # A full report reads several independent research cohorts.  Without a
        # single-flight guard, timed-out HTTP clients continue their server-side
        # work and can queue duplicate 10s+ reports indefinitely.
        with self.full_snapshot_lock:
            now_ms = base._now_ms()
            cached = self.full_snapshot_cache
            if cached is not None and now_ms - cached[0] <= self.full_snapshot_cache_ttl_ms:
                return cached[1]
            payload = self._uncached_snapshot()
            self.full_snapshot_cache = (base._now_ms(), payload)
            return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        return payload


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
    handler = type("PredictWalletShadowV4_8Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "wide Maker rails, Maker-flow Taker, inventory guard and tail insurance paper cohorts active; "
        "liveOrdersAffected=false",
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
