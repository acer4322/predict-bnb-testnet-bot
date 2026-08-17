from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICT_DB = ROOT / "data" / "predict_fun_observer.db"
DEFAULT_SIM_DB = ROOT / "data" / "simulation.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "spot_strike_replay.json"
DEFAULT_RESEARCH_CUTOFF_MS = 1_786_581_338_809


@dataclass(frozen=True)
class MarketSnapshot:
    market_bucket: int
    predict_market_id: int
    binance_market_id: int
    decision_seconds: int
    sampled_at_ms: int
    book_age_ms: float
    observation_timestamp: str
    observation_lag_seconds: float
    start_price: float
    spot_price: float
    displacement_bps: float
    up_ask: float | None
    down_ask: float | None
    winner: str


@dataclass(frozen=True)
class Policy:
    decision_seconds: int
    min_displacement_bps: float
    max_ask: float


def _connect_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
    db.row_factory = sqlite3.Row
    return db


def effective_taker_cost(price: float, fee_rate_bps: int = 200) -> float:
    fee = min(price, 1.0 - price) * fee_rate_bps / 10_000.0
    return price + fee


def select_asof_book(
    rows: Iterable[dict[str, Any]], target_ms: int, max_age_ms: int
) -> dict[str, Any] | None:
    eligible: list[dict[str, Any]] = []
    for source in rows:
        sampled_at_ms = int(source["sampled_at_ms"])
        receipt_age_ms = source.get("receipt_age_ms")
        if sampled_at_ms > target_ms or receipt_age_ms is None:
            continue
        effective_receipt_age_ms = float(receipt_age_ms) + (target_ms - sampled_at_ms)
        if not 0 <= effective_receipt_age_ms <= max_age_ms:
            continue
        row = dict(source)
        row["_effective_receipt_age_ms"] = effective_receipt_age_ms
        eligible.append(row)
    return max(eligible, key=lambda row: int(row["sampled_at_ms"]), default=None)


def select_asof_observation(
    rows: Iterable[dict[str, Any]], decision_seconds: int, max_age_seconds: float
) -> dict[str, Any] | None:
    eligible = [
        row
        for row in rows
        if row.get("seconds_left") is not None
        and float(row["seconds_left"]) >= decision_seconds
        and float(row["seconds_left"]) - decision_seconds <= max_age_seconds
        and row.get("start_price") is not None
        and row.get("spot_price") is not None
        and float(row["start_price"]) > 0
        and float(row["spot_price"]) > 0
        and (row.get("spot_age_ms") is None or float(row["spot_age_ms"]) <= max_age_seconds * 1000)
    ]
    return min(eligible, key=lambda row: float(row["seconds_left"]), default=None)


def chronological_split(markets: list[int]) -> dict[str, set[int]]:
    ordered = sorted(set(markets))
    n = len(ordered)
    train_end = int(n * 0.60)
    validation_end = int(n * 0.80)
    return {
        "train": set(ordered[:train_end]),
        "validation": set(ordered[train_end:validation_end]),
        "holdout": set(ordered[validation_end:]),
    }


def evaluate_policy(
    snapshots: Iterable[MarketSnapshot],
    policy: Policy,
    *,
    stake: float = 1.0,
    fee_rate_bps: int = 200,
    flip_side: bool = False,
    winner_shift: int = 0,
) -> dict[str, Any]:
    candidates = sorted(
        (row for row in snapshots if row.decision_seconds == policy.decision_seconds),
        key=lambda row: row.market_bucket,
    )
    shifted_winners = [row.winner for row in candidates]
    trades: list[dict[str, Any]] = []
    equity = peak = max_drawdown = 0.0
    wins = 0
    for index, row in enumerate(candidates):
        if abs(row.displacement_bps) < policy.min_displacement_bps:
            continue
        side = "UP" if row.displacement_bps > 0 else "DOWN"
        if flip_side:
            side = "DOWN" if side == "UP" else "UP"
        ask = row.up_ask if side == "UP" else row.down_ask
        if ask is None or not 0 < ask <= policy.max_ask:
            continue
        winner_index = index + winner_shift
        if winner_index < 0 or winner_index >= len(shifted_winners):
            continue
        winner = shifted_winners[winner_index]
        unit_cost = effective_taker_cost(float(ask), fee_rate_bps)
        shares = stake / unit_cost
        won = side == winner
        pnl = shares - stake if won else -stake
        wins += int(won)
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        trades.append(
            {
                "marketBucket": row.market_bucket,
                "side": side,
                "winner": winner,
                "ask": ask,
                "effectiveUnitCost": unit_cost,
                "displacementBps": row.displacement_bps,
                "pnl": pnl,
            }
        )
    count = len(trades)
    total_stake = stake * count
    pnl = sum(float(row["pnl"]) for row in trades)
    return {
        "trades": count,
        "wins": wins,
        "winRate": wins / count if count else None,
        "pnl": pnl,
        "roi": pnl / total_stake if total_stake else None,
        "maxDrawdownStakeUnits": max_drawdown / stake if stake else None,
        "medianAsk": (
            sorted(float(row["ask"]) for row in trades)[count // 2] if count else None
        ),
        "details": trades,
    }


def _policy_score(results: dict[str, dict[str, Any]]) -> tuple[float, float, int, float]:
    train = results["train"]
    validation = results["validation"]
    if min(train["trades"], validation["trades"]) < 10:
        return (-math.inf, -math.inf, 0, -math.inf)
    return (
        min(float(train["roi"]), float(validation["roi"])),
        min(float(train["winRate"]), float(validation["winRate"])),
        min(int(train["trades"]), int(validation["trades"])),
        (float(train["roi"]) + float(validation["roi"])) / 2,
    )


def select_policy(
    snapshots_by_split: dict[str, list[MarketSnapshot]], policies: Iterable[Policy]
) -> tuple[Policy, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    ranked: list[tuple[tuple[float, float, int, float], Policy, dict[str, dict[str, Any]]]] = []
    for policy in policies:
        results = {
            split: evaluate_policy(snapshots_by_split[split], policy)
            for split in ("train", "validation")
        }
        ranked.append((_policy_score(results), policy, results))
    ranked.sort(key=lambda row: row[0], reverse=True)
    if not ranked or ranked[0][0][0] == -math.inf:
        raise RuntimeError("No policy has at least 10 trades in both train and validation")
    _, locked, locked_results = ranked[0]
    leaderboard = [
        {
            "policy": asdict(policy),
            "score": list(score),
            "train": {key: value for key, value in results["train"].items() if key != "details"},
            "validation": {
                key: value for key, value in results["validation"].items() if key != "details"
            },
        }
        for score, policy, results in ranked[:20]
    ]
    return locked, locked_results, leaderboard


def load_snapshots(
    predict_db_path: Path,
    simulation_db_path: Path,
    decision_seconds_values: Iterable[int],
    *,
    max_age_ms: int = 3_000,
    research_cutoff_ms: int | None = DEFAULT_RESEARCH_CUTOFF_MS,
) -> tuple[list[MarketSnapshot], dict[str, Any]]:
    predict_db = _connect_ro(predict_db_path)
    simulation_db = _connect_ro(simulation_db_path)
    try:
        book_rows = [dict(row) for row in predict_db.execute(
            """SELECT market_id, market_bucket, sampled_at_ms, up_ask, down_ask,
                      source_age_ms, receipt_age_ms, transport_age_ms
                 FROM predict_fun_trajectory
                WHERE asset='BTC' AND up_ask IS NOT NULL AND down_ask IS NOT NULL
                ORDER BY market_bucket, sampled_at_ms"""
        )]
        books_by_bucket: dict[int, list[dict[str, Any]]] = {}
        predict_market_id: dict[int, int] = {}
        for row in book_rows:
            bucket = int(row["market_bucket"])
            books_by_bucket.setdefault(bucket, []).append(row)
            predict_market_id[bucket] = int(row["market_id"])

        buckets = sorted(books_by_bucket)
        if research_cutoff_ms is not None:
            buckets = [
                bucket for bucket in buckets
                if (bucket + 300) * 1000 <= int(research_cutoff_ms)
            ]
            books_by_bucket = {bucket: books_by_bucket[bucket] for bucket in buckets}
        if not buckets:
            return [], {"reason": "NO_PREDICT_BOOKS"}
        marks = ",".join("?" for _ in buckets)
        sequence_rows = simulation_db.execute(
            f"""SELECT market_id, start_ms
                  FROM strategy_m_market_sequence
                 WHERE start_ms IN ({marks})""",
            [bucket * 1000 for bucket in buckets],
        ).fetchall()
        binance_by_bucket = {int(row["start_ms"]) // 1000: int(row["market_id"]) for row in sequence_rows}
        binance_ids = sorted(set(binance_by_bucket.values()))
        id_marks = ",".join("?" for _ in binance_ids)
        settlements = {
            int(row["market_id"]): dict(row)
            for row in simulation_db.execute(
                f"""SELECT market_id, start_price, official_winner
                       FROM market_settlements
                      WHERE status='OFFICIAL' AND official_winner IN ('UP','DOWN')
                        AND market_id IN ({id_marks})""",
                binance_ids,
            )
        }
        seconds_values = sorted(set(int(value) for value in decision_seconds_values))
        lower = min(seconds_values)
        upper = max(seconds_values) + max_age_ms / 1000.0
        observation_rows = [dict(row) for row in simulation_db.execute(
            f"""SELECT market_id, timestamp, start_price, spot_price, seconds_left, spot_age_ms
                   FROM observations
                  WHERE market_id IN ({id_marks})
                    AND seconds_left BETWEEN ? AND ?
                    AND start_price IS NOT NULL AND spot_price IS NOT NULL
                  ORDER BY market_id, id""",
            [*binance_ids, lower, upper],
        )]
        observations_by_market: dict[int, list[dict[str, Any]]] = {}
        for row in observation_rows:
            observations_by_market.setdefault(int(row["market_id"]), []).append(row)

        snapshots: list[MarketSnapshot] = []
        start_price_mismatches = 0
        start_price_compared = 0
        for bucket in buckets:
            binance_id = binance_by_bucket.get(bucket)
            settlement = settlements.get(binance_id or -1)
            if not binance_id or not settlement:
                continue
            for decision_seconds in seconds_values:
                target_ms = bucket * 1000 + (300 - decision_seconds) * 1000
                book = select_asof_book(books_by_bucket[bucket], target_ms, max_age_ms)
                observation = select_asof_observation(
                    observations_by_market.get(binance_id, []),
                    decision_seconds,
                    max_age_ms / 1000.0,
                )
                if not book or not observation:
                    continue
                start_price = float(observation["start_price"])
                spot_price = float(observation["spot_price"])
                start_price_compared += 1
                if abs(start_price - float(settlement["start_price"])) > 1e-9:
                    start_price_mismatches += 1
                snapshots.append(MarketSnapshot(
                    market_bucket=bucket,
                    predict_market_id=predict_market_id[bucket],
                    binance_market_id=binance_id,
                    decision_seconds=decision_seconds,
                    sampled_at_ms=int(book["sampled_at_ms"]),
                    book_age_ms=float(book["_effective_receipt_age_ms"]),
                    observation_timestamp=str(observation["timestamp"]),
                    observation_lag_seconds=float(observation["seconds_left"]) - decision_seconds,
                    start_price=start_price,
                    spot_price=spot_price,
                    displacement_bps=(spot_price - start_price) / start_price * 10_000.0,
                    up_ask=float(book["up_ask"]) if book["up_ask"] is not None else None,
                    down_ask=float(book["down_ask"]) if book["down_ask"] is not None else None,
                    winner=str(settlement["official_winner"]),
                ))
        audit = {
            "predictBuckets": len(buckets),
            "mappedBinanceMarkets": len(binance_by_bucket),
            "officialSettlements": len(settlements),
            "snapshots": len(snapshots),
            "causalStartPriceCompared": start_price_compared,
            "causalStartPriceMismatchCount": start_price_mismatches,
            "maxAsOfAgeMs": max_age_ms,
            "researchCutoffMs": research_cutoff_ms,
            "cutoffPolicy": "market end must be at or before the immutable pre-forward deployment boundary",
        }
        return snapshots, audit
    finally:
        predict_db.close()
        simulation_db.close()


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    decision_seconds_values = [10, 15, 20, 30, 45, 60]
    snapshots, audit = load_snapshots(
        args.predict_db, args.simulation_db, decision_seconds_values, max_age_ms=args.max_age_ms
        , research_cutoff_ms=args.research_cutoff_ms
    )
    market_buckets = sorted({row.market_bucket for row in snapshots})
    split_ids = chronological_split(market_buckets)
    snapshots_by_split = {
        split: [row for row in snapshots if row.market_bucket in ids]
        for split, ids in split_ids.items()
    }
    policies = [
        Policy(seconds, displacement, max_ask)
        for seconds in decision_seconds_values
        for displacement in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0)
        for max_ask in (0.75, 0.80, 0.85, 0.90, 0.95)
    ]
    locked, train_validation, leaderboard = select_policy(snapshots_by_split, policies)
    holdout = evaluate_policy(snapshots_by_split["holdout"], locked)
    all_results = {
        **train_validation,
        "holdout": holdout,
    }
    negative_controls = {
        split: {
            "flipSide": {
                key: value
                for key, value in evaluate_policy(rows, locked, flip_side=True).items()
                if key != "details"
            },
            "winnerShiftPlusOne": {
                key: value
                for key, value in evaluate_policy(rows, locked, winner_shift=1).items()
                if key != "details"
            },
        }
        for split, rows in snapshots_by_split.items()
    }
    summary_results = {
        split: {key: value for key, value in result.items() if key != "details"}
        for split, result in all_results.items()
    }
    completion = {
        "historicalCriterionMet": all(
            result["trades"] >= 10
            and float(result["winRate"] or 0) > 0.60
            and float(result["roi"] or 0) > 0.10
            for result in all_results.values()
        ),
        "goalComplete": False,
        "reason": "Historical replay cannot complete a forward-performance goal.",
    }
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "researchOnly": True,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "featureProvenance": {
            "startPrice": "simulation.observations.start_price captured before decision",
            "spotPrice": "simulation.observations.spot_price as-of decision",
            "entryAsk": "predict_fun_trajectory ask as-of decision",
            "labelOnly": "market_settlements.official_winner",
            "selection": "train and validation only; holdout evaluated after policy lock",
        },
        "audit": audit,
        "marketSplit": {split: len(ids) for split, ids in split_ids.items()},
        "lockedPolicy": asdict(locked),
        "results": summary_results,
        "negativeControls": negative_controls,
        "leaderboard": leaderboard,
        "holdoutTrades": holdout["details"],
        "completion": completion,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Causal spot-vs-strike wallet strategy replay")
    parser.add_argument("--predict-db", type=Path, default=DEFAULT_PREDICT_DB)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIM_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-age-ms", type=int, default=3_000)
    parser.add_argument("--research-cutoff-ms", type=int, default=DEFAULT_RESEARCH_CUTOFF_MS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_analysis(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "lockedPolicy": result["lockedPolicy"],
        "results": result["results"],
        "completion": result["completion"],
    }, indent=2))


if __name__ == "__main__":
    main()
