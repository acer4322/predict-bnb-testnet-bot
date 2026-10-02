from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from predict_bot import predict_wallet_maker_ebm_strategy_v1 as maker_ebm

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUR_DB = ROOT / "data" / "strategy_target_compare_v1.db"
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "unified_maker_stable_minus1_open_mid_v0_report.json"
VERSION = "UNIFIED_MAKER_STABLE_MINUS1_OPEN_MID_V0"
BASE_PREFIX = "UNIFIED_CONTROLLER_PAPER_V1"
OPEN_MID_MIN_SECONDS_LEFT = 60.0


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def q(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos)); w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def stats(values: list[float]) -> dict[str, float | None]:
    xs = [float(x) for x in values if x is not None]
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "max": max(xs) if xs else None,
        "mean": statistics.mean(xs) if xs else None,
        "median": statistics.median(xs) if xs else None,
        "p25": q(xs, 0.25),
        "p75": q(xs, 0.75),
        "sum": sum(xs) if xs else None,
    }


def snapshot_value(snapshot: dict[str, Any], snake: str, camel: str) -> float | None:
    v = num(snapshot.get(camel))
    if v is None:
        v = num(snapshot.get(snake))
    return v


def permissive_decide(snapshot: dict[str, Any], inventory_state: dict[str, Any]) -> dict[str, Any]:
    """OPEN/MID experimental policy.

    Bypass both frozen EBMs in OPEN/MID. New quotes start one tick behind best bid.
    Existing quotes are kept resting while their side remains desired, instead of
    repricing on each snapshot. Preserve inventory side rule, 18-share size and pair cap.
    """
    seconds_left = snapshot_value(snapshot, "seconds_left", "secondsLeft")
    if seconds_left is None or seconds_left <= OPEN_MID_MIN_SECONDS_LEFT:
        return {"decision": "DEFER_TO_BASE", "reason": "TAIL_USES_BASE", "orders": []}

    orders: list[dict[str, Any]] = []
    for side in maker_ebm.desired_sides(inventory_state):
        tick = maker_ebm._quote_tick(snapshot, side, 1)  # stable experiment: one tick behind best bid
        if tick is None:
            continue
        orders.append({
            "side": side,
            "priceTick": tick,
            "price": round(tick * maker_ebm.GRID, 2),
            "shares": maker_ebm.SHARES_PER_ORDER,
            "offsetTicks": 1,
            "origin": VERSION,
        })

    if len(orders) == 2:
        while sum(float(x["price"]) for x in orders) > maker_ebm.MAX_PAIR_PRICE_SUM + 1e-9:
            expensive = max(orders, key=lambda x: float(x["price"]))
            nxt = int(expensive["priceTick"]) - 1
            if nxt < int(round(maker_ebm.MIN_PRICE / maker_ebm.GRID)):
                return {"decision": "IDLE", "reason": "PAIR_PRICE_CAP_UNSATISFIABLE", "orders": []}
            expensive["priceTick"] = nxt
            expensive["price"] = round(nxt * maker_ebm.GRID, 2)
            expensive["offsetTicks"] = int(expensive["offsetTicks"]) + 1

    return {
        "decision": "QUOTE" if orders else "IDLE",
        "reason": "STABLE_MINUS1_OPEN_MID" if orders else "NO_EXECUTABLE_QUOTE",
        "orders": orders,
    }


@dataclass
class SimOrder:
    key: tuple[str, int]
    side: str
    price_tick: int
    price: float
    shares: float
    placed_at_ms: int
    placed_snapshot_ns: int


class Simulator:
    def __init__(self, models: dict[str, dict[str, Any]], variant: str) -> None:
        self.models = models
        self.variant = variant
        self.orders: dict[tuple[str, int], SimOrder] = {}
        self.last_closed: dict[tuple[str, int], int] = {}
        self.up_shares = 0.0
        self.down_shares = 0.0
        self.up_cost = 0.0
        self.down_cost = 0.0
        self.placements = 0
        self.cancels = 0
        self.maker_fills: list[dict[str, Any]] = []
        self.seed_fills: list[dict[str, Any]] = []

    def inventory(self) -> dict[str, Any]:
        return maker_ebm.inventory(self.up_shares, self.down_shares, self.up_cost, self.down_cost)

    def fill_existing(self, snapshot: dict[str, Any], snapshot_ns: int, now_ms: int) -> int:
        filled = 0
        for key, order in list(self.orders.items()):
            raw = {
                "side": order.side,
                "price": order.price,
                "placedAtMs": order.placed_at_ms,
                "placedSnapshotNs": order.placed_snapshot_ns,
            }
            if not maker_ebm.ask_touch_fill(raw, snapshot, snapshot_ns=snapshot_ns, now_ms=now_ms):
                continue
            self.orders.pop(key, None)
            self.last_closed[key] = now_ms
            if order.side == "UP":
                self.up_shares += order.shares
                self.up_cost += order.shares * order.price
            else:
                self.down_shares += order.shares
                self.down_cost += order.shares * order.price
            self.maker_fills.append({
                "side": order.side, "price": order.price, "shares": order.shares, "at_ms": now_ms,
            })
            filled += 1
        return filled

    def apply_seed(self, seed: dict[str, Any]) -> None:
        side = str(seed["side"])
        shares = float(seed["shares"])
        price = float(seed["price"])
        if side == "UP":
            self.up_shares += shares; self.up_cost += shares * price
        else:
            self.down_shares += shares; self.down_cost += shares * price
        self.seed_fills.append(dict(seed))

    def decide(self, snapshot: dict[str, Any], market_id: int, now_ms: int) -> dict[str, Any]:
        seconds_left = snapshot_value(snapshot, "seconds_left", "secondsLeft")
        inv = self.inventory()
        if self.variant == "PERMISSIVE" and seconds_left is not None and seconds_left > OPEN_MID_MIN_SECONDS_LEFT:
            return permissive_decide(snapshot, inv)
        return maker_ebm.decide(
            snapshot, self.models, inv, cohort=maker_ebm.COMBINED_COHORT,
            expected_market_id=market_id, now_ms=now_ms,
        )

    def apply_plan(self, decision: dict[str, Any], snapshot_ns: int, now_ms: int, allow_new: bool) -> None:
        desired_rows = decision.get("orders") if decision.get("decision") == "QUOTE" else []
        desired_rows = list(desired_rows or [])
        desired_sides = {str(r["side"]) for r in desired_rows}

        # OPEN/MID stable-resting experiment: if a side is still desired, keep its
        # existing quote at the original price instead of cancel/reprice every snapshot.
        for key in list(self.orders):
            if key[0] in desired_sides:
                continue
            self.orders.pop(key, None)
            self.last_closed[key] = now_ms
            self.cancels += 1
        if not allow_new:
            return

        active_by_side = {order.side: order for order in self.orders.values()}
        for row0 in desired_rows:
            side = str(row0["side"])
            if side in active_by_side:
                continue
            row = dict(row0)
            tick = int(row["priceTick"])
            price = float(row["price"])

            # Preserve the real pair cap against an already-resting opposite quote.
            opp = "DOWN" if side == "UP" else "UP"
            opposite = active_by_side.get(opp)
            if opposite is not None:
                while price + float(opposite.price) > maker_ebm.MAX_PAIR_PRICE_SUM + 1e-9:
                    tick -= 1
                    if tick < int(round(maker_ebm.MIN_PRICE / maker_ebm.GRID)):
                        tick = -1
                        break
                    price = round(tick * maker_ebm.GRID, 2)
                if tick < 0:
                    continue

            key = (side, tick)
            if now_ms - int(self.last_closed.get(key, 0)) < maker_ebm.REFILL_COOLDOWN_MS:
                continue
            order = SimOrder(
                key=key, side=side, price_tick=tick, price=price, shares=float(row["shares"]),
                placed_at_ms=now_ms, placed_snapshot_ns=snapshot_ns,
            )
            self.orders[key] = order
            active_by_side[side] = order
            self.placements += 1


def load_snapshots(con: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in con.execute(
        """SELECT market_id,decision_ms,source_snapshot_ms,public_state_json
             FROM our_decisions
            WHERE strategy_version LIKE ?
            ORDER BY market_id,decision_ms""",
        (BASE_PREFIX + "%",),
    ):
        try:
            snap = json.loads(str(r["public_state_json"]))
        except Exception:
            continue
        if not isinstance(snap, dict):
            continue
        snap = dict(snap)
        snap.setdefault("sampledAtMs", r["source_snapshot_ms"])
        out[int(r["market_id"])].append({"decision_ms": int(r["decision_ms"]), "snapshot": snap})
    return out


def load_seeds(con: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in con.execute(
        """SELECT market_id,side,price,shares,filled_at_ms
             FROM our_fills
            WHERE strategy_version LIKE ? AND channel='TAKER' AND purpose='OPEN_SEED'
            ORDER BY market_id,filled_at_ms""",
        (BASE_PREFIX + "%",),
    ):
        out[int(r["market_id"])].append({
            "side": str(r["side"]), "price": float(r["price"]), "shares": float(r["shares"]),
            "filled_at_ms": int(r["filled_at_ms"]),
        })
    return out


def load_baseline(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = defaultdict(lambda: {"fills": [], "placements": 0, "cancels": 0})
    for r in con.execute(
        """SELECT market_id,side,price,shares,filled_at_ms,channel,purpose
             FROM our_fills
            WHERE strategy_version LIKE ? ORDER BY market_id,filled_at_ms""",
        (BASE_PREFIX + "%",),
    ):
        out[int(r["market_id"])]["fills"].append(dict(r))
    for r in con.execute(
        """SELECT market_id,status,COUNT(*) n FROM our_orders
            WHERE strategy_version LIKE ? GROUP BY market_id,status""",
        (BASE_PREFIX + "%",),
    ):
        m = int(r["market_id"]); n = int(r["n"])
        out[m]["placements"] += n
        if str(r["status"]) == "CANCELLED":
            out[m]["cancels"] += n
    return out


def load_winners(con: sqlite3.Connection) -> dict[int, str]:
    return {int(r["market_id"]): str(r["winner"]) for r in con.execute(
        "SELECT market_id,winner FROM target_market_results WHERE asset='BTC' AND winner IN ('UP','DOWN')"
    )}


def pnl_from_fills(fills: list[dict[str, Any]], winner: str) -> float:
    shares = {"UP": 0.0, "DOWN": 0.0}; cost = 0.0
    for f in fills:
        side = str(f["side"]); sh = float(f["shares"]); px = float(f["price"])
        shares[side] += sh; cost += sh * px
    return shares[winner] - cost


def fifo_pair(fills: list[dict[str, Any]]) -> dict[str, float]:
    queues = {"UP": deque(), "DOWN": deque()}
    paired = 0.0; edge = 0.0; completions = 0
    for f in sorted(fills, key=lambda x: int(x.get("at_ms", x.get("filled_at_ms", 0)))):
        side = str(f["side"]); opp = "DOWN" if side == "UP" else "UP"
        remaining = float(f["shares"]); price = float(f["price"]); matched_here = 0.0
        while remaining > 1e-9 and queues[opp]:
            old = queues[opp][0]
            m = min(remaining, old["shares"])
            paired += m
            edge += m * (1.0 - price - old["price"])
            matched_here += m
            remaining -= m; old["shares"] -= m
            if old["shares"] <= 1e-9:
                queues[opp].popleft()
        if matched_here > 1e-9:
            completions += 1
        if remaining > 1e-9:
            queues[side].append({"shares": remaining, "price": price})
    gross = sum(float(f["shares"]) for f in fills)
    return {
        "pairedShares": paired,
        "pairedCoverage": (2 * paired / gross) if gross > 0 else 0.0,
        "lockedEdgeUsdt": edge,
        "edgePerPairedShare": (edge / paired) if paired > 0 else 0.0,
        "pairCompletionEvents": completions,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--our-db", type=Path, default=DEFAULT_OUR_DB)
    ap.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = ap.parse_args()

    our = ro(args.our_db); target = ro(args.target_db)
    try:
        snapshots = load_snapshots(our)
        seeds = load_seeds(our)
        baseline = load_baseline(our)
        winners = load_winners(target)
        models = maker_ebm.load_models()
        markets = sorted(set(snapshots) & set(winners))

        rows: list[dict[str, Any]] = []
        for market_id in markets:
            sim = Simulator(models, "PERMISSIVE")
            seed_rows = list(seeds.get(market_id, [])); seed_idx = 0
            for item in snapshots[market_id]:
                now_ms = int(item["decision_ms"]); snap = dict(item["snapshot"])
                snapshot_ns = int(num(snap.get("timestampNs")) or num(snap.get("timestamp_ns")) or (now_ms * 1_000_000))
                maker_filled = sim.fill_existing(snap, snapshot_ns, now_ms)
                decision = sim.decide(snap, market_id, now_ms)
                seed_filled_this = False
                while seed_idx < len(seed_rows) and int(seed_rows[seed_idx]["filled_at_ms"]) <= now_ms:
                    seed = seed_rows[seed_idx]
                    sim.apply_seed(seed)
                    seed_idx += 1
                    seed_filled_this = True
                sim.apply_plan(decision, snapshot_ns, now_ms, allow_new=(maker_filled == 0 and not seed_filled_this))

            # Apply any seed whose recorded timestamp is after final sampled decision (rare).
            while seed_idx < len(seed_rows):
                sim.apply_seed(seed_rows[seed_idx]); seed_idx += 1

            winner = winners[market_id]
            variant_fills = [dict(x) for x in sim.maker_fills] + [
                {"side": s["side"], "price": s["price"], "shares": s["shares"], "at_ms": s["filled_at_ms"]}
                for s in sim.seed_fills
            ]
            base_fills = [dict(x) for x in baseline.get(market_id, {}).get("fills", [])]
            base_pnl = pnl_from_fills(base_fills, winner)
            variant_pnl = pnl_from_fills(variant_fills, winner)
            base_maker = [f for f in base_fills if str(f.get("channel")) == "MAKER"]
            var_maker = sim.maker_fills
            base_pair = fifo_pair(base_maker); var_pair = fifo_pair(var_maker)
            rows.append({
                "marketId": market_id,
                "winner": winner,
                "baselinePnlUsdt": base_pnl,
                "variantPnlUsdt": variant_pnl,
                "deltaPnlUsdt": variant_pnl - base_pnl,
                "baselinePlacements": int(baseline.get(market_id, {}).get("placements", 0)),
                "variantPlacements": sim.placements,
                "baselineMakerFills": len(base_maker),
                "variantMakerFills": len(var_maker),
                "baselineMakerCancels": int(baseline.get(market_id, {}).get("cancels", 0)),
                "variantMakerCancels": sim.cancels,
                "baselinePair": base_pair,
                "variantPair": var_pair,
            })

        def arr(field: str) -> list[float]:
            return [float(r[field]) for r in rows]
        def pair_arr(which: str, field: str) -> list[float]:
            return [float(r[which][field]) for r in rows]

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "hypothesis": "OPEN/MID Target-like Maker should stay broadly active and rest quotes rather than continuously gate/reprice; test stable best-bid-minus-1 quoting.",
            "variant": {
                "openMidDefinition": "seconds_left > 60",
                "hazardPolicy": "BYPASS in OPEN/MID",
                "levelPolicy": "BYPASS in OPEN/MID; new quote starts best bid -1 tick and remains resting while side stays desired",
                "preserved": [
                    "own unified inventory side rule", "18 shares/order", "simultaneous pair cap <=0.99",
                    ">=250ms later-snapshot ask-touch fill proxy", "1s same-side+tick refill cooldown",
                    "existing TAIL policy", "same recorded OPEN_SEED fills as baseline",
                ],
            },
            "coverage": {"markets": len(rows), "settledMarkets": len(rows)},
            "outcome": {
                "baselinePnlUsdt": sum(arr("baselinePnlUsdt")),
                "variantPnlUsdt": sum(arr("variantPnlUsdt")),
                "deltaPnlUsdt": sum(arr("deltaPnlUsdt")),
                "baselinePositiveMarkets": sum(1 for r in rows if r["baselinePnlUsdt"] > 0),
                "variantPositiveMarkets": sum(1 for r in rows if r["variantPnlUsdt"] > 0),
                "variantBetterMarkets": sum(1 for r in rows if r["deltaPnlUsdt"] > 1e-9),
                "variantWorseMarkets": sum(1 for r in rows if r["deltaPnlUsdt"] < -1e-9),
                "variantTiedMarkets": sum(1 for r in rows if abs(r["deltaPnlUsdt"]) <= 1e-9),
                "deltaPerMarket": stats(arr("deltaPnlUsdt")),
            },
            "makerActivity": {
                "baselinePlacements": stats(arr("baselinePlacements")),
                "variantPlacements": stats(arr("variantPlacements")),
                "baselineMakerFills": stats(arr("baselineMakerFills")),
                "variantMakerFills": stats(arr("variantMakerFills")),
                "baselineCancels": stats(arr("baselineMakerCancels")),
                "variantCancels": stats(arr("variantMakerCancels")),
            },
            "pairing": {
                "baselinePairedShares": stats(pair_arr("baselinePair", "pairedShares")),
                "variantPairedShares": stats(pair_arr("variantPair", "pairedShares")),
                "baselinePairedCoverage": stats(pair_arr("baselinePair", "pairedCoverage")),
                "variantPairedCoverage": stats(pair_arr("variantPair", "pairedCoverage")),
                "baselineLockedEdgeUsdt": stats(pair_arr("baselinePair", "lockedEdgeUsdt")),
                "variantLockedEdgeUsdt": stats(pair_arr("variantPair", "lockedEdgeUsdt")),
                "baselinePairCompletionEvents": stats(pair_arr("baselinePair", "pairCompletionEvents")),
                "variantPairCompletionEvents": stats(pair_arr("variantPair", "pairCompletionEvents")),
                "baselineTotalEdgePerPairedShare": (
                    sum(pair_arr("baselinePair", "lockedEdgeUsdt")) / sum(pair_arr("baselinePair", "pairedShares"))
                    if sum(pair_arr("baselinePair", "pairedShares")) else None
                ),
                "variantTotalEdgePerPairedShare": (
                    sum(pair_arr("variantPair", "lockedEdgeUsdt")) / sum(pair_arr("variantPair", "pairedShares"))
                    if sum(pair_arr("variantPair", "pairedShares")) else None
                ),
            },
            "interpretationGuard": [
                "Preliminary replay on snapshots recorded while baseline was running; paper strategy does not affect public market snapshots, but this is not fresh blind validation.",
                "The variant intentionally changes both OPEN/MID Hazard gating and Level aggressiveness as one user-proposed permissive-Maker concept; do not attribute outcome to one subcomponent yet.",
                "Ask-touch fill proxy remains intentionally unchanged, so any improvement survives the same conservative fill model.",
            ],
            "rows": rows,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "coverage": report["coverage"],
            "outcome": report["outcome"],
            "makerActivity": report["makerActivity"],
            "pairing": report["pairing"],
            "report": str(args.report),
        }, ensure_ascii=False, indent=2))
    finally:
        our.close(); target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
