from __future__ import annotations

import json
import math
import os
from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v5 as base


REPLENISH_DELAY_SECONDS = max(
    1.0,
    min(30.0, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_REPLENISH_DELAY_SECONDS", "3"))),
)
MAX_GENERATIONS_PER_SIDE = max(
    1,
    min(12, int(os.environ.get("PREDICT_WALLET_MAKER_CLONE_MAX_GENERATIONS_PER_SIDE", "6"))),
)
BASE_POTENTIAL_PROFIT_USDT = 1.0


class SequentialReplenishmentWalletMakerCloneEngine(base.NeutralAnchorWalletMakerCloneEngine):
    """V6 Echtgeld experiment: one active parent per outcome, sequentially replenished.

    Target-wallet makerHash profiling over ETH/BNB exact 5m markets showed roughly
    12 distinct parent hashes per market (median), roughly 6 per market/outcome,
    no observed overlapping fill windows, and rapid same-price refills after some
    fills. V6 therefore tests the smallest falsifiable model:

    * generation 1 uses V5's neutral complementary 0.50/0.50 anchor;
    * after a side's parent is confirmed FILLED, only that side may replenish;
    * the next parent is posted at the then-current passive best bid;
    * a minimum 3 second fill-to-refill delay is enforced;
    * each side is capped at 6 parent generations per market for the first test;
    * requested potential-profit tranche is locked to $1;
    * Binance's $1 minimum and the existing maximumOrderUsdt remain hard bounds;
    * V4 cancel-finality and pre-settlement cancel protections remain active.

    V6 intentionally does NOT implement price-move cancel/reprice yet. A resting
    parent must fill (or be cancelled by pause/cutoff/rollover) before another
    parent on that outcome is created. This isolates genuine fill-driven
    replenishment from a more complicated quote-chasing strategy.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V6_SEQUENTIAL_REPLENISH"

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row[1])
                for row in self.db.execute("PRAGMA table_info(wallet_maker_clone_orders)").fetchall()
            }
            if "generation" in columns:
                return

            # V1-V5 used UNIQUE(pair_id, side), which made more than one parent
            # order per outcome impossible. Rebuild the table transactionally and
            # preserve every historical row as generation 1.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "ALTER TABLE wallet_maker_clone_orders RENAME TO wallet_maker_clone_orders_v5_migration"
                )
                self.db.execute("DROP INDEX IF EXISTS idx_wallet_clone_orders_order_id")
                self.db.execute("DROP INDEX IF EXISTS idx_wallet_clone_orders_state")
                self.db.execute(
                    """
                    CREATE TABLE wallet_maker_clone_orders(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        pair_id INTEGER NOT NULL,
                        market_id INTEGER NOT NULL,
                        side TEXT NOT NULL,
                        generation INTEGER NOT NULL DEFAULT 1,
                        token_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        target_price REAL,
                        best_bid_at_plan REAL,
                        best_ask_at_plan REAL,
                        target_profit_usdt REAL,
                        planned_cost_usdt REAL,
                        planned_shares REAL,
                        quote_id TEXT,
                        quote_average REAL,
                        quote_amount_in_wei TEXT,
                        quote_amount_out_wei TEXT,
                        quote_expire_at_ms INTEGER,
                        quote_started_at_ms INTEGER,
                        quote_completed_at_ms INTEGER,
                        quote_rtt_ms REAL,
                        place_started_at_ms INTEGER,
                        place_completed_at_ms INTEGER,
                        place_rtt_ms REAL,
                        order_id TEXT,
                        vendor_order_id TEXT,
                        order_status TEXT,
                        maker_usdt_amount REAL,
                        maker_share_qty REAL,
                        filled_usdt_amount REAL,
                        filled_share_qty REAL,
                        fill_percentage REAL,
                        last_reconciled_at_ms INTEGER,
                        raw_status_json TEXT,
                        error_kind TEXT,
                        error_message TEXT,
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        UNIQUE(pair_id, side, generation)
                    )
                    """
                )
                self.db.execute(
                    """
                    INSERT INTO wallet_maker_clone_orders(
                        id,pair_id,market_id,side,generation,token_id,state,target_price,
                        best_bid_at_plan,best_ask_at_plan,target_profit_usdt,planned_cost_usdt,
                        planned_shares,quote_id,quote_average,quote_amount_in_wei,quote_amount_out_wei,
                        quote_expire_at_ms,quote_started_at_ms,quote_completed_at_ms,quote_rtt_ms,
                        place_started_at_ms,place_completed_at_ms,place_rtt_ms,order_id,vendor_order_id,
                        order_status,maker_usdt_amount,maker_share_qty,filled_usdt_amount,filled_share_qty,
                        fill_percentage,last_reconciled_at_ms,raw_status_json,error_kind,error_message,
                        created_at_ms,updated_at_ms
                    )
                    SELECT
                        id,pair_id,market_id,side,1,token_id,state,target_price,
                        best_bid_at_plan,best_ask_at_plan,target_profit_usdt,planned_cost_usdt,
                        planned_shares,quote_id,quote_average,quote_amount_in_wei,quote_amount_out_wei,
                        quote_expire_at_ms,quote_started_at_ms,quote_completed_at_ms,quote_rtt_ms,
                        place_started_at_ms,place_completed_at_ms,place_rtt_ms,order_id,vendor_order_id,
                        order_status,maker_usdt_amount,maker_share_qty,filled_usdt_amount,filled_share_qty,
                        fill_percentage,last_reconciled_at_ms,raw_status_json,error_kind,error_message,
                        created_at_ms,updated_at_ms
                    FROM wallet_maker_clone_orders_v5_migration
                    """
                )
                self.db.execute("DROP TABLE wallet_maker_clone_orders_v5_migration")
                self.db.execute(
                    "CREATE INDEX idx_wallet_clone_orders_order_id ON wallet_maker_clone_orders(order_id)"
                )
                self.db.execute(
                    "CREATE INDEX idx_wallet_clone_orders_state ON wallet_maker_clone_orders(state, market_id)"
                )
                self.db.execute(
                    "CREATE INDEX idx_wallet_clone_orders_generation ON wallet_maker_clone_orders(pair_id, side, generation)"
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        # Keep this experiment controlled: one fixed base tranche and no V1
        # cancel-only auto-requote behavior mixed into the sample.
        self._set_setting("target_profit_usdt", f"{BASE_POTENTIAL_PROFIT_USDT:.8f}")
        self._set_setting("auto_requote", "0")

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if "targetPotentialProfitUsdt" in values:
            try:
                requested = float(values["targetPotentialProfitUsdt"])
            except (TypeError, ValueError) as exc:
                raise ValueError("targetPotentialProfitUsdt must be a number") from exc
            if abs(requested - BASE_POTENTIAL_PROFIT_USDT) > 1e-9:
                raise ValueError("V6 experiment locks targetPotentialProfitUsdt to 1.00 USDT")
        if values.get("autoRequote") is True:
            raise ValueError("V6 uses fill-driven sequential replenishment; Auto Requote must remain OFF")
        return super().update_settings(values)

    def _pair_orders(self, pair_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM wallet_maker_clone_orders
                   WHERE pair_id=?
                   ORDER BY CASE side WHEN 'UP' THEN 0 ELSE 1 END, generation ASC, id ASC""",
                (int(pair_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _insert_order_plan(self, pair_id: int, market_id: int, plan: dict[str, Any]) -> int:
        now = core._now_ms()
        side = str(plan["side"])
        with self.db_lock:
            row = self.db.execute(
                "SELECT COALESCE(MAX(generation),0) AS generation FROM wallet_maker_clone_orders WHERE pair_id=? AND side=?",
                (int(pair_id), side),
            ).fetchone()
            generation = int(row["generation"] if row else 0) + 1
            cursor = self.db.execute(
                """INSERT INTO wallet_maker_clone_orders(
                       pair_id,market_id,side,generation,token_id,state,target_price,
                       best_bid_at_plan,best_ask_at_plan,target_profit_usdt,planned_cost_usdt,
                       planned_shares,created_at_ms,updated_at_ms
                   ) VALUES(?,?,?,?,?,'PLANNED',?,?,?,?,?,?,?,?)""",
                (
                    int(pair_id),
                    int(market_id),
                    side,
                    generation,
                    str(plan["tokenId"]),
                    float(plan["price"]),
                    float(plan["bestBid"]),
                    float(plan["bestAsk"]),
                    float(plan["targetProfit"]),
                    float(plan["plannedCost"]),
                    float(plan["plannedShares"]),
                    now,
                    now,
                ),
            )
            self.db.commit()
        return int(cursor.lastrowid)

    def _latest_side_order(self, pair_id: int, side: str) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT * FROM wallet_maker_clone_orders
                   WHERE pair_id=? AND side=?
                   ORDER BY generation DESC, id DESC LIMIT 1""",
                (int(pair_id), str(side)),
            ).fetchone()
        return dict(row) if row else None

    def _plan_replenishment(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        settings = self._settings()
        book = self.books.get(side) or {}
        bid = core._finite(book.get("bestBid"))
        ask = core._finite(book.get("bestAsk"))
        if bid is None or ask is None:
            return None

        precision = max(1, int(market.get("precision") or 2))
        tick = 10 ** (-precision)
        raw = bid - int(settings["bidOffsetTicks"]) * tick
        price = math.floor((raw + 1e-12) / tick) * tick
        price = round(price, precision)
        if price <= 0 or price >= 1:
            return None
        if price + 1e-12 >= ask:
            return {
                "side": side,
                "blocked": True,
                "reason": f"replenishment post-only blocked price {price:.6f} >= ask {ask:.6f}",
                "price": price,
                "bestBid": bid,
                "bestAsk": ask,
            }

        requested_profit = BASE_POTENTIAL_PROFIT_USDT
        theoretical_shares = requested_profit / max(1e-9, 1.0 - price)
        theoretical_cost = theoretical_shares * price
        cost = max(float(settings["minimumOrderUsdt"]), theoretical_cost)
        cost = min(float(settings["maximumOrderUsdt"]), cost)
        shares = cost / price
        effective_profit = shares * (1.0 - price)
        return {
            "side": side,
            "blocked": False,
            "price": price,
            "referencePrice": bid,
            "pricingModel": "CURRENT_PASSIVE_BEST_BID_REPLENISH",
            "bestBid": bid,
            "bestAsk": ask,
            "requestedPotentialProfit": requested_profit,
            "targetProfit": effective_profit,
            "theoreticalCostBeforeVenueBounds": theoretical_cost,
            "venueBoundDistorted": abs(effective_profit - requested_profit) > 0.05,
            "plannedCost": cost,
            "plannedShares": shares,
            "tokenId": str(market[f"{side.lower()}_token_id"]),
        }

    def _place_side_generation(
        self,
        pair: dict[str, Any],
        market: dict[str, Any],
        side: str,
    ) -> bool:
        plan = self._plan_replenishment(side, market)
        if plan is None:
            self.status = f"WAITING_{side}_BOOK"
            return False
        if plan.get("blocked"):
            self.status = f"{side}_POST_ONLY_BLOCKED"
            self._event(
                "INFO",
                "REPLENISH_POST_ONLY_BLOCK",
                int(market["market_id"]),
                int(pair["id"]),
                str(plan.get("reason")),
            )
            return False

        row_id = self._insert_order_plan(int(pair["id"]), int(market["market_id"]), plan)
        with self.db_lock:
            created = self.db.execute(
                "SELECT generation FROM wallet_maker_clone_orders WHERE id=?", (row_id,)
            ).fetchone()
        generation = int(created["generation"] if created else 0)
        self._event(
            "WARN",
            "SIDE_REPLENISH_PLACE_STARTED_V6",
            int(market["market_id"]),
            int(pair["id"]),
            (
                f"{side} generation={generation} price={plan['price']:.4f} "
                f"cost={plan['plannedCost']:.4f} effectivePotentialProfit={plan['targetProfit']:.4f} "
                f"venueBoundDistorted={plan.get('venueBoundDistorted')}"
            ),
        )
        result = self._place_one(row_id, plan, market)
        if result.get("ok"):
            self._event(
                "INFO",
                "SIDE_REPLENISH_PLACE_COMPLETED_V6",
                int(market["market_id"]),
                int(pair["id"]),
                f"{side} generation={generation} orderId={result.get('orderId')}",
            )
            return True

        if result.get("ambiguous"):
            self._set_setting("runtime_enabled", "0")
            self.status = "AMBIGUOUS_MANUAL_RECONCILIATION"
            self._event(
                "ERROR",
                "SIDE_REPLENISH_AMBIGUOUS_AUTO_PAUSE_V6",
                int(market["market_id"]),
                int(pair["id"]),
                f"{side} generation={generation}; {result}",
            )
        else:
            self.status = f"{side}_REPLENISH_REJECTED"
            self._event(
                "ERROR",
                "SIDE_REPLENISH_REJECTED_V6",
                int(market["market_id"]),
                int(pair["id"]),
                f"{side} generation={generation}; {result}",
            )
        return False

    def _tick(self) -> None:
        settings = self._settings()
        if not core.MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not self._ensure_client():
            return

        interlock = self._normal_live_conflict()
        if interlock.get("blocked") is True:
            if settings["runtimeEnabled"]:
                self._set_setting("runtime_enabled", "0")
                self._event(
                    "ERROR",
                    "NORMAL_LIVE_INTERLOCK_TRIPPED",
                    None,
                    None,
                    str(interlock.get("reason")),
                )
                self._cancel_recorded_active("NORMAL_LIVE_INTERLOCK")
            self.status = "BLOCKED_NORMAL_LIVE"
            return

        market = self._prime_market()
        if market is None:
            self.status = "WAITING_MARKET"
            return
        self._poll_books(market)

        pair = self._current_pair()
        if pair is not None and int(pair["market_id"]) != int(market["market_id"]):
            if str(pair.get("state") or "") != "CANCEL_PENDING":
                self._cancel_pair(int(pair["id"]), "MARKET_ROLLOVER")
            if not self._final_reconcile_pair(pair):
                self.status = "ROLLOVER_RECONCILING_FINAL_FILLS"
                return
            self._set_pair(int(pair["id"]), state="ROLLED", close_reason="MARKET_ROLLOVER")
            pair = None

        if pair is None:
            pair = self._new_pair(market)

        if str(pair.get("state") or "") == "CANCEL_PENDING":
            if not self._final_reconcile_pair(pair):
                self.status = "CANCEL_PENDING_RECONCILIATION"
                return
            pair = self._current_pair() or pair

        self._reconcile_pair(pair, market)
        pair = self._current_pair() or pair

        now_ms = core._now_ms()
        seconds_left = base.base.market_seconds_left(market, now_ms)
        orders = self._pair_orders(int(pair["id"]))
        live_orders = [
            row
            for row in orders
            if row.get("order_id")
            and str(row.get("state") or "") not in base.base.TERMINAL_ORDER_STATES
        ]
        if seconds_left <= base.base.RESTING_CANCEL_SECONDS:
            if live_orders:
                self._cancel_pair(int(pair["id"]), "PRE_SETTLEMENT_CUTOFF_V6")
                self._final_reconcile_pair(pair)
                self.status = "PRE_SETTLEMENT_CANCEL_PENDING"
            else:
                self.status = "PRE_SETTLEMENT_CUTOFF"
            return

        if not settings["runtimeEnabled"]:
            self.status = "PAUSED"
            return
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return

        if not orders:
            if self._complementary_anchor() is None:
                self.status = "WAITING_BOTH_BOOKS"
                return
            # Generation 1 remains the controlled V5 neutral-anchor pair.
            super()._place_pair(pair, market)
            self.status = "PAIR_ACTIVE"
            return

        placed_any = False
        capped_sides = 0
        for side in ("UP", "DOWN"):
            latest = self._latest_side_order(int(pair["id"]), side)
            if latest is None:
                continue
            generation = int(latest.get("generation") or 1)
            if generation >= MAX_GENERATIONS_PER_SIDE:
                capped_sides += 1
                continue
            state = str(latest.get("state") or "")
            if state != "FILLED":
                continue
            terminal_at_ms = int(latest.get("updated_at_ms") or latest.get("last_reconciled_at_ms") or now_ms)
            if now_ms - terminal_at_ms < REPLENISH_DELAY_SECONDS * 1000.0:
                continue
            if self._place_side_generation(pair, market, side):
                placed_any = True

        if placed_any:
            self.status = "SEQUENTIAL_REPLENISH_ACTIVE"
        elif capped_sides == 2:
            self.status = "GENERATION_CAP_REACHED"
        else:
            self.status = "WAITING_REPLENISH_TRIGGER"

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        pair = self._current_pair()
        orders = self._pair_orders(int(pair["id"])) if pair else []
        latest_by_side: dict[str, dict[str, Any]] = {}
        for row in orders:
            latest_by_side[str(row.get("side") or "")] = row

        payload["orders"] = {
            "UP": latest_by_side.get("UP"),
            "DOWN": latest_by_side.get("DOWN"),
        }
        payload["orderGenerations"] = orders
        payload["generationStats"] = {
            side: {
                "parents": sum(1 for row in orders if str(row.get("side")) == side),
                "filledParents": sum(
                    1
                    for row in orders
                    if str(row.get("side")) == side and str(row.get("state")) == "FILLED"
                ),
                "filledCostUsdt": sum(
                    float(row.get("filled_usdt_amount") or 0.0)
                    for row in orders
                    if str(row.get("side")) == side
                ),
                "filledShares": sum(
                    float(row.get("filled_share_qty") or 0.0)
                    for row in orders
                    if str(row.get("side")) == side
                ),
            }
            for side in ("UP", "DOWN")
        }
        payload["summary"] = {
            "filledCostUsdt": sum(float(row.get("filled_usdt_amount") or 0.0) for row in orders),
            "upFilledShares": sum(
                float(row.get("filled_share_qty") or 0.0)
                for row in orders
                if str(row.get("side")) == "UP"
            ),
            "downFilledShares": sum(
                float(row.get("filled_share_qty") or 0.0)
                for row in orders
                if str(row.get("side")) == "DOWN"
            ),
        }
        payload.setdefault("rules", {}).update(
            experimentV6="SEQUENTIAL_FILL_DRIVEN_REPLENISHMENT",
            generation1Pricing="V5_COMPLEMENTARY_MID_NEUTRAL_0P5",
            laterGenerationPricing="CURRENT_PASSIVE_BEST_BID",
            requestedPotentialProfitPerParentUsdt=BASE_POTENTIAL_PROFIT_USDT,
            replenishDelaySeconds=REPLENISH_DELAY_SECONDS,
            maximumParentGenerationsPerMarketOutcome=MAX_GENERATIONS_PER_SIDE,
            maximumSimultaneousParentOrdersPerOutcome=1,
            priceMoveCancelRepriceEnabled=False,
            autoRequoteEnabled=False,
            venueMinMaxCanDistortEffectivePotentialProfit=True,
            multiGenerationSchemaV6=True,
        )
        return payload


# Patch the complete V5 -> V4 -> V3 -> V2 -> V1 entrypoint chain.
base.WalletMakerCloneEngine = SequentialReplenishmentWalletMakerCloneEngine
base.base.WalletMakerCloneEngine = SequentialReplenishmentWalletMakerCloneEngine
base.base.base.WalletMakerCloneEngine = SequentialReplenishmentWalletMakerCloneEngine
base.base.base.base.WalletMakerCloneEngine = SequentialReplenishmentWalletMakerCloneEngine
base.base.base.base.base.WalletMakerCloneEngine = SequentialReplenishmentWalletMakerCloneEngine


def main() -> int:
    return base.base.base.base.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
