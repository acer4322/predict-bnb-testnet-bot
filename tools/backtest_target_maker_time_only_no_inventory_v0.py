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
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_time_only_no_inventory_v0_report.json"
VERSION = "TARGET_MAKER_TIME_ONLY_NO_INVENTORY_V0"
BASE_PREFIX = "UNIFIED_CONTROLLER_PAPER_V1"

# Deliberately simple Layer-1-only policy from the observed Target time-depth profile.
# This is not claimed to be the Target ground-truth policy; it is one falsifiable V0.
TIME_OFFSETS = (
    (240.0, 1),
    (180.0, 1),
    (120.0, 2),
    (60.0, 3),
    (30.0, 4),
    (15.0, 5),
    (0.0, 5),
)


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
        "p90": q(xs, 0.90),
        "sum": sum(xs) if xs else None,
    }


def snapshot_value(snapshot: dict[str, Any], snake: str, camel: str) -> float | None:
    v = num(snapshot.get(camel))
    if v is None:
        v = num(snapshot.get(snake))
    return v


def time_offset(seconds_left: float | None) -> int | None:
    if seconds_left is None or seconds_left <= 0:
        return None
    for lower_exclusive, offset in TIME_OFFSETS:
        if seconds_left > lower_exclusive:
            return offset
    return 5


def time_depth_decide(
    snapshot: dict[str, Any],
    inventory_state: dict[str, Any],
    *,
    use_inventory: bool,
) -> dict[str, Any]:
    seconds_left = snapshot_value(snapshot, "seconds_left", "secondsLeft")
    offset = time_offset(seconds_left)
    if offset is None:
        return {"decision": "IDLE", "reason": "OUTSIDE_ACTIVE_WINDOW", "orders": []}

    sides = maker_ebm.desired_sides(inventory_state) if use_inventory else ["UP", "DOWN"]
    orders: list[dict[str, Any]] = []
    for side in sides:
        tick = maker_ebm._quote_tick(snapshot, side, offset)
        if tick is None:
            continue
        orders.append({
            "side": side,
            "priceTick": tick,
            "price": round(tick * maker_ebm.GRID, 2),
            "shares": maker_ebm.SHARES_PER_ORDER,
            "offsetTicks": offset,
            "origin": VERSION,
        })

    if len(orders) == 2:
        # Keep the existing V0 simultaneous pair-price safety bound.
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
        "reason": "TIME_DEPTH_WITH_INVENTORY" if use_inventory else "TIME_DEPTH_NO_INVENTORY",
        "orders": orders,
        "secondsLeft": seconds_left,
        "baseOffsetTicks": offset,
        "inventoryUsedForSide": use_inventory,
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
    def __init__(self, *, use_inventory: bool) -> None:
        self.use_inventory = bool(use_inventory)
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
                "side": order.side,
                "price": order.price,
                "shares": order.shares,
                "at_ms": now_ms,
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

    def decide(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return time_depth_decide(snapshot, self.inventory(), use_inventory=self.use_inventory)

    def apply_plan(self, decision: dict[str, Any], snapshot_ns: int, now_ms: int, allow_new: bool) -> None:
        desired_rows = decision.get("orders") if decision.get("decision") == "QUOTE" else []
        desired = {(str(r["side"]), int(r["priceTick"])): r for r in (desired_rows or [])}
        for key in list(self.orders):
            if key in desired:
                continue
            self.orders.pop(key, None)
            self.last_closed[key] = now_ms
            self.cancels += 1
        if not allow_new:
            return
        for key, row in desired.items():
            if key in self.orders:
                continue
            if now_ms - int(self.last_closed.get(key, 0)) < maker_ebm.REFILL_COOLDOWN_MS:
                continue
            self.orders[key] = SimOrder(
                key=key,
                side=key[0],
                price_tick=key[1],
                price=float(row["price"]),
                shares=float(row["shares"]),
                placed_at_ms=now_ms,
                placed_snapshot_ns=snapshot_ns,
            )
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
            "side": str(r["side"]),
            "price": float(r["price"]),
            "shares": float(r["shares"]),
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


def imbalance_metrics(fills: list[dict[str, Any]]) -> dict[str, float]:
    up = down = 0.0
    peak_abs_net = 0.0
    max_same_side_run = 0
    current_side = None
    current_run = 0
    for f in sorted(fills, key=lambda x: int(x.get("at_ms", x.get("filled_at_ms", 0)))):
        side = str(f["side"]); sh = float(f["shares"])
        if side == "UP": up += sh
        else: down += sh
        peak_abs_net = max(peak_abs_net, abs(up - down))
        if side == current_side:
            current_run += 1
        else:
            current_side = side
            current_run = 1
        max_same_side_run = max(max_same_side_run, current_run)
    gross = up + down
    return {
        "upShares": up,
        "downShares": down,
        "grossShares": gross,
        "finalNetShares": up - down,
        "finalAbsNetShares": abs(up - down),
        "finalImbalanceRatio": abs(up - down) / gross if gross > 1e-9 else 0.0,
        "peakAbsNetShares": peak_abs_net,
        "maxSameSideFillRun": float(max_same_side_run),
    }


def portfolio_metrics(fills: list[dict[str, Any]]) -> dict[str, float]:
    up = down = cost = 0.0
    for f in fills:
        sh = float(f["shares"]); px = float(f["price"])
        if str(f["side"]) == "UP": up += sh
        else: down += sh
        cost += sh * px
    settle_up = up - cost
    settle_down = down - cost
    worst = min(settle_up, settle_down)
    return {
        "costUsdt": cost,
        "settleUpPnlUsdt": settle_up,
        "settleDownPnlUsdt": settle_down,
        "worstCasePnlUsdt": worst,
        "riskDeficitUsdt": max(0.0, -worst),
    }


def run_sim(snapshots: list[dict[str, Any]], seed_rows: list[dict[str, Any]], *, use_inventory: bool) -> Simulator:
    sim = Simulator(use_inventory=use_inventory)
    seed_idx = 0
    for item in snapshots:
        now_ms = int(item["decision_ms"]); snap = dict(item["snapshot"])
        snapshot_ns = int(num(snap.get("timestampNs")) or num(snap.get("timestamp_ns")) or (now_ms * 1_000_000))
        maker_filled = sim.fill_existing(snap, snapshot_ns, now_ms)
        decision = sim.decide(snap)
        seed_filled_this = False
        while seed_idx < len(seed_rows) and int(seed_rows[seed_idx]["filled_at_ms"]) <= now_ms:
            sim.apply_seed(seed_rows[seed_idx]); seed_idx += 1; seed_filled_this = True
        sim.apply_plan(decision, snapshot_ns, now_ms, allow_new=(maker_filled == 0 and not seed_filled_this))
    while seed_idx < len(seed_rows):
        sim.apply_seed(seed_rows[seed_idx]); seed_idx += 1
    return sim


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
        markets = sorted(set(snapshots) & set(winners))
        rows: list[dict[str, Any]] = []

        for market_id in markets:
            seed_rows = list(seeds.get(market_id, []))
            sim_inv = run_sim(snapshots[market_id], seed_rows, use_inventory=True)
            sim_no = run_sim(snapshots[market_id], seed_rows, use_inventory=False)
            winner = winners[market_id]
            base_fills = [dict(x) for x in baseline.get(market_id, {}).get("fills", [])]
            base_maker = [f for f in base_fills if str(f.get("channel")) == "MAKER"]

            def pack(sim: Simulator) -> dict[str, Any]:
                maker = [dict(x) for x in sim.maker_fills]
                all_fills = maker + [
                    {"side": s["side"], "price": s["price"], "shares": s["shares"], "at_ms": s["filled_at_ms"]}
                    for s in sim.seed_fills
                ]
                return {
                    "pnlUsdt": pnl_from_fills(all_fills, winner),
                    "placements": sim.placements,
                    "cancels": sim.cancels,
                    "makerFills": len(maker),
                    "pair": fifo_pair(maker),
                    "makerImbalance": imbalance_metrics(maker),
                    "portfolio": portfolio_metrics(all_fills),
                }

            base_pack = {
                "pnlUsdt": pnl_from_fills(base_fills, winner),
                "placements": int(baseline.get(market_id, {}).get("placements", 0)),
                "cancels": int(baseline.get(market_id, {}).get("cancels", 0)),
                "makerFills": len(base_maker),
                "pair": fifo_pair(base_maker),
                "makerImbalance": imbalance_metrics(base_maker),
                "portfolio": portfolio_metrics(base_fills),
            }
            inv_pack = pack(sim_inv)
            no_pack = pack(sim_no)
            rows.append({
                "marketId": market_id,
                "winner": winner,
                "baseline": base_pack,
                "timeDepthWithInventory": inv_pack,
                "timeDepthNoInventory": no_pack,
                "noInventoryMinusWithInventoryPnl": no_pack["pnlUsdt"] - inv_pack["pnlUsdt"],
            })

        def get(which: str, field: str) -> list[float]:
            return [float(r[which][field]) for r in rows]
        def nested(which: str, group: str, field: str) -> list[float]:
            return [float(r[which][group][field]) for r in rows]

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "hypothesis": "Layer-1 Target Maker behavior may be approximated by time-conditioned quote depth. Compare the same time-depth policy with versus without inventory-imbalance side management to see whether Layer 2 is structurally necessary.",
            "policy": {
                "timeOffsets": [
                    {"secondsLeft": "300..240", "offsetTicks": 1},
                    {"secondsLeft": "240..180", "offsetTicks": 1},
                    {"secondsLeft": "180..120", "offsetTicks": 2},
                    {"secondsLeft": "120..60", "offsetTicks": 3},
                    {"secondsLeft": "60..30", "offsetTicks": 4},
                    {"secondsLeft": "30..15", "offsetTicks": 5},
                    {"secondsLeft": "15..0", "offsetTicks": 5},
                ],
                "timeDepthBasis": "simple deterministic approximation of TARGET_MAKER_TIME_DEPTH_LAYER_V1; not a fitted Target policy",
                "withInventory": "existing V0 desired_sides(): if abs net >=18 quote only deficient side, else quote both",
                "noInventory": "always quote UP and DOWN, regardless of accumulated holdings",
                "sameForBoth": [
                    "18 shares/order",
                    "dynamic reprice to current best bid minus time offset",
                    "simultaneous pair cap <=0.99",
                    ">=250ms later-snapshot ask-touch fill proxy",
                    "1s same side+tick refill cooldown",
                    "same recorded OPEN_SEED fills",
                    "no Hazard EBM",
                    "no Level EBM",
                    "no market direction input",
                ],
            },
            "coverage": {"markets": len(rows), "settledMarkets": len(rows)},
            "outcome": {},
            "makerActivity": {},
            "pairing": {},
            "inventoryRisk": {},
            "rows": rows,
            "interpretationGuard": [
                "This is a preliminary replay on snapshots recorded while baseline ran, not fresh blind validation.",
                "The time-depth curve is intentionally coarse. The key causal comparison is NO_INVENTORY versus WITH_INVENTORY under the exact same curve.",
                "Target time-depth evidence is probabilistic anchored placement inference; this strategy is not claimed to reproduce Target ground truth.",
                "Do not add direction conditioning to this test; that is Layer 3 and is intentionally absent.",
            ],
        }

        for which in ("baseline", "timeDepthWithInventory", "timeDepthNoInventory"):
            report["outcome"][which] = {
                "pnlUsdt": sum(get(which, "pnlUsdt")),
                "positiveMarkets": sum(1 for r in rows if float(r[which]["pnlUsdt"]) > 0),
                "pnlPerMarket": stats(get(which, "pnlUsdt")),
                "worstCasePnlUsdt": stats(nested(which, "portfolio", "worstCasePnlUsdt")),
                "riskDeficitUsdt": stats(nested(which, "portfolio", "riskDeficitUsdt")),
            }
            report["makerActivity"][which] = {
                "placements": stats(get(which, "placements")),
                "cancels": stats(get(which, "cancels")),
                "makerFills": stats(get(which, "makerFills")),
                "fillPerPlacementTotal": (
                    sum(get(which, "makerFills")) / sum(get(which, "placements"))
                    if sum(get(which, "placements")) else None
                ),
            }
            report["pairing"][which] = {
                "pairedShares": stats(nested(which, "pair", "pairedShares")),
                "pairedCoverage": stats(nested(which, "pair", "pairedCoverage")),
                "lockedEdgeUsdt": stats(nested(which, "pair", "lockedEdgeUsdt")),
                "pairCompletionEvents": stats(nested(which, "pair", "pairCompletionEvents")),
                "totalEdgePerPairedShare": (
                    sum(nested(which, "pair", "lockedEdgeUsdt")) / sum(nested(which, "pair", "pairedShares"))
                    if sum(nested(which, "pair", "pairedShares")) else None
                ),
            }
            report["inventoryRisk"][which] = {
                "finalAbsNetShares": stats(nested(which, "makerImbalance", "finalAbsNetShares")),
                "finalImbalanceRatio": stats(nested(which, "makerImbalance", "finalImbalanceRatio")),
                "peakAbsNetShares": stats(nested(which, "makerImbalance", "peakAbsNetShares")),
                "maxSameSideFillRun": stats(nested(which, "makerImbalance", "maxSameSideFillRun")),
            }

        no_minus_inv = [float(r["noInventoryMinusWithInventoryPnl"]) for r in rows]
        report["directInventoryAblation"] = {
            "noInventoryMinusWithInventoryPnlUsdt": sum(no_minus_inv),
            "deltaPerMarket": stats(no_minus_inv),
            "noInventoryBetterMarkets": sum(1 for x in no_minus_inv if x > 1e-9),
            "noInventoryWorseMarkets": sum(1 for x in no_minus_inv if x < -1e-9),
            "ties": sum(1 for x in no_minus_inv if abs(x) <= 1e-9),
            "finalAbsNetSharesDeltaMean": statistics.mean(
                [r["timeDepthNoInventory"]["makerImbalance"]["finalAbsNetShares"] - r["timeDepthWithInventory"]["makerImbalance"]["finalAbsNetShares"] for r in rows]
            ) if rows else None,
            "peakAbsNetSharesDeltaMean": statistics.mean(
                [r["timeDepthNoInventory"]["makerImbalance"]["peakAbsNetShares"] - r["timeDepthWithInventory"]["makerImbalance"]["peakAbsNetShares"] for r in rows]
            ) if rows else None,
        }

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "coverage": report["coverage"],
            "outcome": report["outcome"],
            "makerActivity": report["makerActivity"],
            "pairing": report["pairing"],
            "inventoryRisk": report["inventoryRisk"],
            "directInventoryAblation": report["directInventoryAblation"],
            "report": str(args.report),
        }, ensure_ascii=False, indent=2))
    finally:
        our.close(); target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
