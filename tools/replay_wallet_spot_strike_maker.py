from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_wallet_spot_strike_forward import wilson_interval
from tools.replay_wallet_shadow_spot_strike import (
    DEFAULT_PREDICT_DB,
    DEFAULT_RESEARCH_CUTOFF_MS,
    DEFAULT_SIM_DB,
    chronological_split,
    effective_taker_cost,
    select_asof_book,
    select_asof_observation,
)

DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "spot_strike_maker_replay.json"


@dataclass(frozen=True)
class MakerCandidate:
    market_bucket: int
    predict_market_id: int
    decision_seconds: int
    decision_at_ms: int
    side: str
    displacement_bps: float
    quote_offset: float
    quote: float
    quote_book_age_ms: float
    fill_at_ms: int | None
    fill_seconds_left: float | None
    fill_touch_ask: float | None
    winner: str


@dataclass(frozen=True)
class MakerPolicy:
    decision_seconds: int
    min_displacement_bps: float
    max_quote: float
    quote_offset: float


def _connect_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
    db.row_factory = sqlite3.Row
    return db


def first_fresh_ask_touch(
    rows: Iterable[dict[str, Any]],
    *,
    placed_at_ms: int,
    cancel_seconds: float,
    side: str,
    quote: float,
    max_age_ms: int,
) -> dict[str, Any] | None:
    ask_key = "up_ask" if side == "UP" else "down_ask"
    eligible: list[dict[str, Any]] = []
    for source in rows:
        sampled_at_ms = int(source["sampled_at_ms"])
        seconds_left = source.get("seconds_left")
        receipt_age_ms = source.get("receipt_age_ms")
        ask = source.get(ask_key)
        if (
            sampled_at_ms <= placed_at_ms
            or seconds_left is None
            or float(seconds_left) < cancel_seconds
            or receipt_age_ms is None
            or not 0 <= float(receipt_age_ms) <= max_age_ms
            or ask is None
            or not 0 < float(ask) <= quote + 1e-12
        ):
            continue
        eligible.append(dict(source))
    return min(eligible, key=lambda row: int(row["sampled_at_ms"]), default=None)


def evaluate_policy(
    candidates: Iterable[MakerCandidate],
    policy: MakerPolicy,
    *,
    stake: float = 1.0,
    fee_rate_bps: int = 200,
    flip_side: bool = False,
    winner_shift: int = 0,
) -> dict[str, Any]:
    rows = sorted(
        (
            row for row in candidates
            if row.decision_seconds == policy.decision_seconds
            and abs(row.quote_offset - policy.quote_offset) <= 1e-12
        ),
        key=lambda row: row.market_bucket,
    )
    winners = [row.winner for row in rows]
    trades: list[dict[str, Any]] = []
    equity = peak = max_drawdown = 0.0
    wins = 0
    for index, row in enumerate(rows):
        if abs(row.displacement_bps) < policy.min_displacement_bps or row.quote > policy.max_quote:
            continue
        if row.fill_at_ms is None:
            continue
        side = row.side
        if flip_side:
            side = "DOWN" if side == "UP" else "UP"
        winner_index = index + winner_shift
        if winner_index < 0 or winner_index >= len(winners):
            continue
        winner = winners[winner_index]
        unit_cost = effective_taker_cost(row.quote, fee_rate_bps)
        shares = stake / unit_cost
        won = side == winner
        pnl = shares - stake if won else -stake
        wins += int(won)
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        trades.append({
            "marketBucket": row.market_bucket,
            "side": side,
            "winner": winner,
            "quote": row.quote,
            "fillTouchAsk": row.fill_touch_ask,
            "fillSecondsLeft": row.fill_seconds_left,
            "displacementBps": row.displacement_bps,
            "pnl": pnl,
        })
    count = len(trades)
    pnl = sum(float(row["pnl"]) for row in trades)
    return {
        "fills": count,
        "wins": wins,
        "winRate": wins / count if count else None,
        "winRateWilson95": wilson_interval(wins, count),
        "pnl": pnl,
        "roi": pnl / (stake * count) if count else None,
        "maxDrawdownStakeUnits": max_drawdown / stake if stake else None,
        "details": trades,
    }


def _score(results: dict[str, dict[str, Any]]) -> tuple[float, float, int, float]:
    train, validation = results["train"], results["validation"]
    if min(int(train["fills"]), int(validation["fills"])) < 8:
        return (-math.inf, -math.inf, 0, -math.inf)
    return (
        min(float(train["roi"]), float(validation["roi"])),
        min(float(train["winRate"]), float(validation["winRate"])),
        min(int(train["fills"]), int(validation["fills"])),
        (float(train["roi"]) + float(validation["roi"])) / 2,
    )


def load_candidates(
    predict_path: Path,
    simulation_path: Path,
    decision_seconds_values: Iterable[int],
    quote_offsets: Iterable[float],
    *,
    cancel_seconds: float,
    max_age_ms: int,
    research_cutoff_ms: int,
) -> tuple[list[MakerCandidate], dict[str, Any]]:
    predict = _connect_ro(predict_path)
    simulation = _connect_ro(simulation_path)
    try:
        raw_books = [dict(row) for row in predict.execute(
            """SELECT market_id,market_bucket,sampled_at_ms,seconds_left,
                      up_bid,up_ask,down_bid,down_ask,receipt_age_ms
                 FROM predict_fun_trajectory
                WHERE asset='BTC' ORDER BY market_bucket,sampled_at_ms"""
        )]
        books_by_bucket: dict[int, list[dict[str, Any]]] = {}
        predict_id_by_bucket: dict[int, int] = {}
        for row in raw_books:
            bucket = int(row["market_bucket"])
            if (bucket + 300) * 1000 > research_cutoff_ms:
                continue
            books_by_bucket.setdefault(bucket, []).append(row)
            predict_id_by_bucket[bucket] = int(row["market_id"])
        buckets = sorted(books_by_bucket)
        if not buckets:
            return [], {"reason": "NO_PRE_CUTOFF_BOOKS"}
        marks = ",".join("?" for _ in buckets)
        sequences = simulation.execute(
            f"SELECT market_id,start_ms FROM strategy_m_market_sequence WHERE start_ms IN ({marks})",
            [bucket * 1000 for bucket in buckets],
        ).fetchall()
        binance_by_bucket = {
            int(row["start_ms"]) // 1000: int(row["market_id"]) for row in sequences
        }
        binance_ids = sorted(set(binance_by_bucket.values()))
        id_marks = ",".join("?" for _ in binance_ids)
        settlements = {
            int(row["market_id"]): dict(row)
            for row in simulation.execute(
                f"""SELECT market_id,official_winner FROM market_settlements
                      WHERE status='OFFICIAL' AND official_winner IN ('UP','DOWN')
                        AND market_id IN ({id_marks})""",
                binance_ids,
            )
        }
        seconds_values = sorted(set(int(value) for value in decision_seconds_values))
        observations = [dict(row) for row in simulation.execute(
            f"""SELECT id,market_id,timestamp,start_price,spot_price,seconds_left,spot_age_ms
                  FROM observations WHERE market_id IN ({id_marks})
                    AND seconds_left BETWEEN ? AND ?
                    AND start_price IS NOT NULL AND spot_price IS NOT NULL
                  ORDER BY market_id,id""",
            [*binance_ids, min(seconds_values), max(seconds_values) + max_age_ms / 1000.0],
        )]
        observations_by_market: dict[int, list[dict[str, Any]]] = {}
        for row in observations:
            observations_by_market.setdefault(int(row["market_id"]), []).append(row)

        candidates: list[MakerCandidate] = []
        missing_entry_book = missing_observation = missing_bid = 0
        for bucket in buckets:
            binance_id = binance_by_bucket.get(bucket)
            settlement = settlements.get(binance_id or -1)
            if not binance_id or not settlement:
                continue
            books = books_by_bucket[bucket]
            for decision_seconds in seconds_values:
                placed_at_ms = (bucket + 300 - decision_seconds) * 1000
                book = select_asof_book(books, placed_at_ms, max_age_ms)
                if not book:
                    missing_entry_book += 1
                    continue
                observation = select_asof_observation(
                    observations_by_market.get(binance_id, []),
                    decision_seconds,
                    max_age_ms / 1000.0,
                )
                if not observation:
                    missing_observation += 1
                    continue
                start = float(observation["start_price"])
                spot = float(observation["spot_price"])
                displacement = (spot - start) / start * 10_000.0
                if abs(displacement) <= 1e-12:
                    continue
                side = "UP" if displacement > 0 else "DOWN"
                bid = book["up_bid" if side == "UP" else "down_bid"]
                if bid is None or not 0 < float(bid) < 1:
                    missing_bid += 1
                    continue
                for quote_offset in quote_offsets:
                    quote = math.floor((float(bid) - float(quote_offset)) * 100 + 1e-9) / 100
                    if not 0 < quote < 1:
                        continue
                    fill = first_fresh_ask_touch(
                        books,
                        placed_at_ms=placed_at_ms,
                        cancel_seconds=cancel_seconds,
                        side=side,
                        quote=quote,
                        max_age_ms=max_age_ms,
                    )
                    ask_key = "up_ask" if side == "UP" else "down_ask"
                    candidates.append(MakerCandidate(
                        market_bucket=bucket,
                        predict_market_id=predict_id_by_bucket[bucket],
                        decision_seconds=decision_seconds,
                        decision_at_ms=placed_at_ms,
                        side=side,
                        displacement_bps=displacement,
                        quote_offset=float(quote_offset),
                        quote=quote,
                        quote_book_age_ms=float(book["_effective_receipt_age_ms"]),
                        fill_at_ms=int(fill["sampled_at_ms"]) if fill else None,
                        fill_seconds_left=float(fill["seconds_left"]) if fill else None,
                        fill_touch_ask=float(fill[ask_key]) if fill else None,
                        winner=str(settlement["official_winner"]),
                    ))
        return candidates, {
            "researchCutoffMs": research_cutoff_ms,
            "cutoffPolicy": "market end must be at or before the immutable V2 deployment boundary",
            "predictBuckets": len(buckets),
            "mappedBinanceMarkets": len(binance_by_bucket),
            "officialSettlements": len(settlements),
            "candidates": len(candidates),
            "missingEntryBook": missing_entry_book,
            "missingCausalSpotObservation": missing_observation,
            "missingDirectionalBid": missing_bid,
            "maxInputAgeMs": max_age_ms,
            "cancelSeconds": cancel_seconds,
        }
    finally:
        predict.close()
        simulation.close()


def run(args: argparse.Namespace) -> dict[str, Any]:
    decision_seconds_values = [30, 45, 60, 90, 120]
    quote_offsets = [0.0, 0.02, 0.05, 0.10, 0.15]
    candidates, audit = load_candidates(
        args.predict_db,
        args.simulation_db,
        decision_seconds_values,
        quote_offsets,
        cancel_seconds=args.cancel_seconds,
        max_age_ms=args.max_age_ms,
        research_cutoff_ms=args.research_cutoff_ms,
    )
    splits = chronological_split([row.market_bucket for row in candidates])
    by_split = {
        name: [row for row in candidates if row.market_bucket in buckets]
        for name, buckets in splits.items()
    }
    policies = [
        MakerPolicy(seconds, displacement, max_quote, quote_offset)
        for seconds in decision_seconds_values
        for displacement in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0)
        for max_quote in (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
        for quote_offset in quote_offsets
    ]
    ranked: list[tuple[tuple[float, float, int, float], MakerPolicy, dict[str, dict[str, Any]]]] = []
    for policy in policies:
        results = {
            name: evaluate_policy(by_split[name], policy)
            for name in ("train", "validation")
        }
        ranked.append((_score(results), policy, results))
    ranked.sort(key=lambda item: item[0], reverse=True)
    viable = bool(ranked and ranked[0][0][0] != -math.inf)
    locked = ranked[0][1] if viable else None
    results = None
    controls = None
    if locked is not None:
        results = {
            name: evaluate_policy(by_split[name], locked)
            for name in ("train", "validation", "holdout")
        }
        controls = {
            "holdoutFlipSide": evaluate_policy(by_split["holdout"], locked, flip_side=True),
            "holdoutWinnerShiftPlusOne": evaluate_policy(
                by_split["holdout"], locked, winner_shift=1
            ),
        }
    leaderboard = [{
        "policy": asdict(policy),
        "score": list(score),
        "train": {k: v for k, v in values["train"].items() if k != "details"},
        "validation": {k: v for k, v in values["validation"].items() if k != "details"},
    } for score, policy, values in ranked[:20]]
    offset_diagnostics = []
    for quote_offset in quote_offsets:
        rows_for_offset = [
            row for row in ranked if abs(row[1].quote_offset - quote_offset) <= 1e-12
        ]
        qualified = [row for row in rows_for_offset if row[0][0] != -math.inf]
        best = qualified[0] if qualified else None
        offset_diagnostics.append({
            "quoteOffset": quote_offset,
            "maximumMinimumFillsAcrossTrainValidation": max(
                (
                    min(int(row[2]["train"]["fills"]), int(row[2]["validation"]["fills"]))
                    for row in rows_for_offset
                ),
                default=0,
            ),
            "qualifiedPolicies": len(qualified),
            "bestQualifiedPolicy": asdict(best[1]) if best else None,
            "bestQualifiedMinimumRoi": best[0][0] if best else None,
            "bestQualifiedMinimumWinRate": best[0][1] if best else None,
        })
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "version": "WALLET_SPOT_STRIKE_MAKER_REPLAY_V1",
        "paperOnly": True,
        "researchOnly": True,
        "liveOrdersAffected": False,
        "fillModel": {
            "quote": "current directional best bid at the fixed decision second",
            "fill": "a later fresh observed ask on the same side touches or crosses the immutable quote",
            "cancel": f"cancel with {args.cancel_seconds:g} seconds remaining",
            "queuePositionKnown": False,
            "feeRateBps": args.fee_rate_bps,
            "warning": "ask-touch is a fill proxy, not venue-confirmed execution; no visible depth or queue position is available",
        },
        "audit": audit,
        "selection": {
            "minimumFillsPerTrainAndValidation": 8,
            "lockedPolicy": asdict(locked) if locked else None,
            "offsetDiagnostics": offset_diagnostics,
            "leaderboard": leaderboard,
        },
        "results": results,
        "controls": controls,
        "forwardDecision": "do not deploy from this replay; a separately preregistered forward maker cohort is required",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Causal Spot-strike passive-bid replay")
    parser.add_argument("--predict-db", type=Path, default=DEFAULT_PREDICT_DB)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIM_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--research-cutoff-ms", type=int, default=DEFAULT_RESEARCH_CUTOFF_MS)
    parser.add_argument("--max-age-ms", type=int, default=3_000)
    parser.add_argument("--cancel-seconds", type=float, default=7.0)
    parser.add_argument("--fee-rate-bps", type=int, default=200)
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "audit": report["audit"],
        "lockedPolicy": report["selection"]["lockedPolicy"],
        "results": {
            name: {k: v for k, v in values.items() if k != "details"}
            for name, values in (report["results"] or {}).items()
        },
        "controls": {
            name: {k: v for k, v in values.items() if k != "details"}
            for name, values in (report["controls"] or {}).items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
