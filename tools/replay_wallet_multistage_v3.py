from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.replay_wallet_shadow_spot_strike import (
    DEFAULT_RESEARCH_CUTOFF_MS,
    chronological_split,
    effective_taker_cost,
)


DEFAULT_PREDICT_DB = ROOT / "data" / "predict_fun_observer.db"
DEFAULT_UPSTREAM_DB = ROOT / "data" / "multi_prediction_observer.db"
DEFAULT_SIM_DB = ROOT / "data" / "simulation.db"
DEFAULT_WALLET_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "multistage_v3_replay.json"


@dataclass(frozen=True)
class Policy:
    recenter_seconds: int = 5
    start_seconds: int = 240
    stop_seconds: int = 10
    maker_edge: float = 0.01
    taker_edge: float = 0.03
    max_input_age_ms: int = 2_500
    max_total_cost: float = 10.0
    max_net_shares: float = 4.0
    max_worst_case_loss: float = 3.0
    shares_per_fill: float = 1.0
    taker_fee_bps: int = 200


@dataclass(frozen=True)
class Action:
    market_bucket: int
    market_id: int
    at_ms: int
    seconds_left: float
    role: str
    side: str
    price: float
    unit_cost: float
    shares: float
    fair_probability: float
    edge: float


@dataclass
class Inventory:
    up_shares: float = 0.0
    down_shares: float = 0.0
    cost: float = 0.0

    def can_add(self, side: str, shares: float, unit_cost: float, policy: Policy) -> bool:
        up = self.up_shares + (shares if side == "UP" else 0.0)
        down = self.down_shares + (shares if side == "DOWN" else 0.0)
        cost = self.cost + shares * unit_cost
        return (
            cost <= policy.max_total_cost + 1e-12
            and abs(up - down) <= policy.max_net_shares + 1e-12
            and min(up, down) - cost >= -policy.max_worst_case_loss - 1e-12
        )

    def add(self, side: str, shares: float, unit_cost: float) -> None:
        if side == "UP":
            self.up_shares += shares
        else:
            self.down_shares += shares
        self.cost += shares * unit_cost


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
    conn.row_factory = sqlite3.Row
    return conn


def _floor_tick(value: float) -> float:
    return math.floor(value * 100 + 1e-9) / 100


def _asof(rows: list[dict[str, Any]], times: list[int], at_ms: int, max_age_ms: int) -> dict[str, Any] | None:
    index = bisect.bisect_right(times, at_ms) - 1
    if index < 0:
        return None
    row = rows[index]
    sampled_at = int(row["sampled_at_ms"])
    receipt_age = row.get("poly_receipt_age_ms")
    effective_age = (float(receipt_age) if receipt_age is not None else 0.0) + at_ms - sampled_at
    if sampled_at > at_ms or not 0 <= effective_age <= max_age_ms:
        return None
    return row


def _try_fill(
    inventory: Inventory,
    actions: list[Action],
    *,
    market_bucket: int,
    market_id: int,
    at_ms: int,
    seconds_left: float,
    role: str,
    side: str,
    price: float,
    fair: float,
    policy: Policy,
) -> bool:
    unit_cost = price if role == "MAKER" else effective_taker_cost(price, policy.taker_fee_bps)
    shares = policy.shares_per_fill
    if not inventory.can_add(side, shares, unit_cost, policy):
        return False
    inventory.add(side, shares, unit_cost)
    actions.append(Action(
        market_bucket=market_bucket,
        market_id=market_id,
        at_ms=at_ms,
        seconds_left=seconds_left,
        role=role,
        side=side,
        price=price,
        unit_cost=unit_cost,
        shares=shares,
        fair_probability=fair,
        edge=fair - unit_cost,
    ))
    return True


def simulate_market(
    local_rows: list[dict[str, Any]],
    upstream_rows: list[dict[str, Any]],
    winner: str,
    policy: Policy,
) -> dict[str, Any]:
    inventory = Inventory()
    actions: list[Action] = []
    active_quotes: dict[str, dict[str, float | int]] = {}
    upstream_times = [int(row["sampled_at_ms"]) for row in upstream_rows]
    last_slot: int | None = None
    market_bucket = int(local_rows[0]["market_bucket"])
    market_id = int(local_rows[0]["market_id"])

    for row in local_rows:
        at_ms = int(row["sampled_at_ms"])
        seconds_left = float(row["seconds_left"])
        if seconds_left < policy.stop_seconds or seconds_left > policy.start_seconds:
            continue
        upstream = _asof(upstream_rows, upstream_times, at_ms, policy.max_input_age_ms)
        if not upstream or upstream.get("poly_up_mid") is None:
            continue
        p_up = float(upstream["poly_up_mid"])
        if not 0 < p_up < 1:
            continue

        for side in list(active_quotes):
            quote = active_quotes[side]
            ask = row["up_ask" if side == "UP" else "down_ask"]
            if (
                ask is not None
                and at_ms > int(quote["placed_at_ms"])
                and float(ask) <= float(quote["price"]) + 1e-12
            ):
                fair = p_up if side == "UP" else 1.0 - p_up
                _try_fill(
                    inventory,
                    actions,
                    market_bucket=market_bucket,
                    market_id=market_id,
                    at_ms=at_ms,
                    seconds_left=seconds_left,
                    role="MAKER",
                    side=side,
                    price=float(quote["price"]),
                    fair=fair,
                    policy=policy,
                )
                del active_quotes[side]

        slot = at_ms // (policy.recenter_seconds * 1000)
        if slot == last_slot:
            continue
        last_slot = slot
        active_quotes.clear()

        for side, fair_key, bid_key, ask_key in (
            ("UP", p_up, "up_bid", "up_ask"),
            ("DOWN", 1.0 - p_up, "down_bid", "down_ask"),
        ):
            bid = row.get(bid_key)
            ask = row.get(ask_key)
            if bid is not None:
                quote = min(float(bid), _floor_tick(fair_key - policy.maker_edge))
                if 0 < quote < 1 and fair_key - quote >= policy.maker_edge - 1e-12:
                    active_quotes[side] = {"price": quote, "placed_at_ms": at_ms}
            if ask is not None:
                ask_value = float(ask)
                unit_cost = effective_taker_cost(ask_value, policy.taker_fee_bps)
                if fair_key - unit_cost >= policy.taker_edge - 1e-12:
                    _try_fill(
                        inventory,
                        actions,
                        market_bucket=market_bucket,
                        market_id=market_id,
                        at_ms=at_ms,
                        seconds_left=seconds_left,
                        role="TAKER",
                        side=side,
                        price=ask_value,
                        fair=fair_key,
                        policy=policy,
                    )

    payout = inventory.up_shares if winner == "UP" else inventory.down_shares
    pnl = payout - inventory.cost
    return {
        "marketBucket": market_bucket,
        "marketId": market_id,
        "winner": winner,
        "traded": bool(actions),
        "actions": [asdict(action) for action in actions],
        "makerFills": sum(action.role == "MAKER" for action in actions),
        "takerFills": sum(action.role == "TAKER" for action in actions),
        "upShares": inventory.up_shares,
        "downShares": inventory.down_shares,
        "cost": inventory.cost,
        "payout": payout,
        "pnl": pnl,
        "roi": pnl / inventory.cost if inventory.cost else None,
        "capitalSide": "UP" if inventory.up_shares > inventory.down_shares else "DOWN" if inventory.down_shares > inventory.up_shares else "FLAT",
    }


def summarize(markets: list[dict[str, Any]]) -> dict[str, Any]:
    traded = [row for row in markets if row["traded"]]
    costs = sum(float(row["cost"]) for row in traded)
    pnl = sum(float(row["pnl"]) for row in traded)
    equity = peak = max_drawdown = 0.0
    loss_streak = longest_loss_streak = 0
    for row in traded:
        equity += float(row["pnl"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if float(row["pnl"]) < 0:
            loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, loss_streak)
        else:
            loss_streak = 0
    action_counts = [len(row["actions"]) for row in traded]
    return {
        "markets": len(markets),
        "tradedMarkets": len(traded),
        "profitableMarkets": sum(float(row["pnl"]) > 0 for row in traded),
        "marketWinRate": sum(float(row["pnl"]) > 0 for row in traded) / len(traded) if traded else None,
        "cost": costs,
        "pnl": pnl,
        "roiOnCost": pnl / costs if costs else None,
        "makerFills": sum(int(row["makerFills"]) for row in traded),
        "takerFills": sum(int(row["takerFills"]) for row in traded),
        "bothRoleMarkets": sum(int(row["makerFills"]) > 0 and int(row["takerFills"]) > 0 for row in traded),
        "bothSideMarkets": sum(float(row["upShares"]) > 0 and float(row["downShares"]) > 0 for row in traded),
        "medianActionsPerTradedMarket": statistics.median(action_counts) if action_counts else None,
        "maxDrawdownUsdt": max_drawdown,
        "longestLossStreak": longest_loss_streak,
    }


def score_target_context(
    conn: sqlite3.Connection,
    markets: list[dict[str, Any]],
    *,
    tolerance_ms: int = 5_000,
) -> dict[str, Any]:
    actions = [action for market in markets for action in market["actions"]]
    matches = 0
    role_matches = 0
    capital_matches = comparable = 0
    for market in markets:
        market_id = int(market["marketId"])
        target = [dict(row) for row in conn.execute(
            """SELECT event_ms,role,side,price,shares FROM wallet_shadow_target_events
                 WHERE market_id=? ORDER BY event_ms""",
            (market_id,),
        )]
        for action in market["actions"]:
            nearby = [row for row in target if abs(int(row["event_ms"]) - int(action["at_ms"])) <= tolerance_ms]
            role_matches += int(any(str(row["role"]).upper() == action["role"] for row in nearby))
            matches += int(any(
                str(row["role"]).upper() == action["role"]
                and str(row["side"]).upper() == action["side"]
                for row in nearby
            ))
        if not target or market["capitalSide"] == "FLAT":
            continue
        costs = defaultdict(float)
        for row in target:
            costs[str(row["side"]).upper()] += float(row["price"] or 0) * float(row["shares"] or 0)
        target_side = "UP" if costs["UP"] > costs["DOWN"] else "DOWN" if costs["DOWN"] > costs["UP"] else "FLAT"
        if target_side != "FLAT":
            comparable += 1
            capital_matches += int(target_side == market["capitalSide"])
    return {
        "candidateActions": len(actions),
        "roleContextWithin5s": role_matches / len(actions) if actions else None,
        "roleAndSideContextWithin5s": matches / len(actions) if actions else None,
        "finalCapitalComparableMarkets": comparable,
        "finalCapitalMatches": capital_matches,
        "finalCapitalMatchRate": capital_matches / comparable if comparable else None,
        "warning": "post-hoc context score only; target events are never strategy inputs and dense target activity can inflate proximity",
    }


def load_and_run(args: argparse.Namespace) -> dict[str, Any]:
    predict = _connect_ro(args.predict_db)
    upstream = _connect_ro(args.upstream_db)
    simulation = _connect_ro(args.simulation_db)
    wallet = _connect_ro(args.wallet_db)
    policy = Policy()
    try:
        local_rows = [dict(row) for row in predict.execute(
            """SELECT market_id,market_bucket,sampled_at_ms,seconds_left,
                      up_bid,up_ask,down_bid,down_ask,receipt_age_ms
                 FROM predict_fun_trajectory
                WHERE asset='BTC' AND (market_bucket+300)*1000<=?
                ORDER BY market_bucket,sampled_at_ms""",
            (args.research_cutoff_ms,),
        )]
        upstream_rows = [dict(row) for row in upstream.execute(
            """SELECT market_bucket,sampled_at_ms,poly_up_mid,poly_down_mid,
                      poly_source_age_ms,poly_receipt_age_ms
                 FROM multi_prediction_trajectory
                WHERE asset='BTC' AND (market_bucket+300)*1000<=?
                ORDER BY market_bucket,sampled_at_ms""",
            (args.research_cutoff_ms,),
        )]
        local_by_bucket: dict[int, list[dict[str, Any]]] = defaultdict(list)
        upstream_by_bucket: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in local_rows:
            local_by_bucket[int(row["market_bucket"])].append(row)
        for row in upstream_rows:
            upstream_by_bucket[int(row["market_bucket"])].append(row)
        buckets = sorted(set(local_by_bucket) & set(upstream_by_bucket))
        marks = ",".join("?" for _ in buckets)
        sequence = simulation.execute(
            f"SELECT market_id,start_ms FROM strategy_m_market_sequence WHERE start_ms IN ({marks})",
            [bucket * 1000 for bucket in buckets],
        ).fetchall()
        binance_by_bucket = {int(row["start_ms"]) // 1000: int(row["market_id"]) for row in sequence}
        binance_ids = sorted(set(binance_by_bucket.values()))
        id_marks = ",".join("?" for _ in binance_ids)
        winners = {
            int(row["market_id"]): str(row["official_winner"])
            for row in simulation.execute(
                f"""SELECT market_id,official_winner FROM market_settlements
                      WHERE status='OFFICIAL' AND official_winner IN ('UP','DOWN')
                        AND market_id IN ({id_marks})""",
                binance_ids,
            )
        }
        results = []
        for bucket in buckets:
            binance_id = binance_by_bucket.get(bucket)
            winner = winners.get(binance_id or -1)
            if winner:
                results.append(simulate_market(local_by_bucket[bucket], upstream_by_bucket[bucket], winner, policy))
        split_buckets = chronological_split([int(row["marketBucket"]) for row in results])
        split_results = {
            name: [row for row in results if int(row["marketBucket"]) in bucket_set]
            for name, bucket_set in split_buckets.items()
        }
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "version": "WALLET_MULTISTAGE_V3_CAUSAL_REPLAY_V1",
            "paperOnly": True,
            "researchOnly": True,
            "liveOrdersAffected": False,
            "policy": asdict(policy),
            "causalBoundary": {
                "decisionInputs": "as-of Predict.fun book plus as-of upstream Poly midpoint only",
                "makerFillProxy": "later fresh same-side ask touches the resting immutable bid before the next 5-second recenter",
                "targetUse": "post-hoc role/side/capital context scoring only",
                "researchCutoffMs": args.research_cutoff_ms,
                "selection": "single preregistered structural candidate; no grid selection and no forward cohort input",
            },
            "coverage": {
                "predictBuckets": len(local_by_bucket),
                "upstreamBuckets": len(upstream_by_bucket),
                "commonBuckets": len(buckets),
                "settledMarkets": len(results),
                "splits": {name: len(rows) for name, rows in split_results.items()},
            },
            "results": {name: summarize(rows) for name, rows in split_results.items()},
            "targetContext": {name: score_target_context(wallet, rows) for name, rows in split_results.items()},
            "markets": results,
            "decision": "RESEARCH_ONLY_NOT_FORWARD_ELIGIBLE",
        }
    finally:
        predict.close()
        upstream.close()
        simulation.close()
        wallet.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Causal multi-stage maker+taker wallet strategy replay")
    parser.add_argument("--predict-db", type=Path, default=DEFAULT_PREDICT_DB)
    parser.add_argument("--upstream-db", type=Path, default=DEFAULT_UPSTREAM_DB)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIM_DB)
    parser.add_argument("--wallet-db", type=Path, default=DEFAULT_WALLET_DB)
    parser.add_argument("--research-cutoff-ms", type=int, default=DEFAULT_RESEARCH_CUTOFF_MS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = load_and_run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"coverage": report["coverage"], "results": report["results"], "targetContext": report["targetContext"], "decision": report["decision"]}, ensure_ascii=False, indent=2))
    print(f"wrote {args.output.resolve()}")


if __name__ == "__main__":
    main()
