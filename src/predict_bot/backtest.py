from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core import taker_fee
from .market_observer import MarketStateObserver


@dataclass(frozen=True)
class M01BacktestSpec:
    """Parameterised M01-family rule evaluated against recorded top-of-book rows."""

    name: str
    seed: int = 20260717
    max_entry: float = 0.30
    min_seconds_left_exclusive: float = 0.0
    max_seconds_left_inclusive: float = 300.0
    stake: float = 10.0
    max_book_skew_ms: float = 500.0
    max_book_age_ms: float = 2000.0
    fee_bps: int = 200

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("strategy name is required")
        if not 0 < self.max_entry <= 1:
            raise ValueError("max_entry must be between 0 and 1")
        if not 0 <= self.min_seconds_left_exclusive < 300:
            raise ValueError("min_seconds_left_exclusive must be in [0, 300)")
        if not 0 < self.max_seconds_left_inclusive <= 300:
            raise ValueError("max_seconds_left_inclusive must be in (0, 300]")
        if self.min_seconds_left_exclusive >= self.max_seconds_left_inclusive:
            raise ValueError("the time window is empty")
        if self.stake <= 0 or self.max_book_skew_ms < 0 or self.max_book_age_ms < 0:
            raise ValueError("stake and book limits must be non-negative")
        if self.fee_bps < 0:
            raise ValueError("fee_bps cannot be negative")


@dataclass(frozen=True)
class BacktestTrade:
    strategy: str
    split: str
    market_id: int
    observation_id: int
    timestamp: str
    side: str
    winner: str
    seconds_left: float
    entry_price: float
    visible_ask_size: float
    requested_stake: float
    filled_stake: float
    shares: float
    fee: float
    pnl: float


@dataclass(frozen=True)
class M01TakeProfitTrade:
    strategy: str
    split: str
    market_id: int
    observation_id: int
    timestamp: str
    side: str
    winner: str
    seconds_left: float
    entry_price: float
    visible_ask_size: float
    requested_stake: float
    filled_stake: float
    shares: float
    entry_fee: float
    target_price: float | None
    exit_reason: str
    exit_observation_id: int | None
    exit_timestamp: str | None
    exit_seconds_left: float | None
    observed_exit_bid: float | None
    visible_bid_size: float | None
    exit_price: float | None
    exit_fee: float
    hold_seconds: float | None
    pnl: float


@dataclass(frozen=True)
class M01AdvancedExitTrade:
    strategy: str
    split: str
    market_id: int
    observation_id: int
    timestamp: str
    side: str
    winner: str
    seconds_left: float
    entry_price: float
    visible_ask_size: float
    requested_stake: float
    filled_stake: float
    shares: float
    entry_fee: float
    exit_method: str
    trigger_price: float | None
    exit_fraction: float
    trailing_drop: float | None
    exit_reason: str
    exit_observation_id: int | None
    exit_timestamp: str | None
    exit_seconds_left: float | None
    observed_exit_bid: float | None
    visible_bid_size: float | None
    exit_price: float | None
    exited_shares: float
    exit_fee: float
    remaining_shares: float
    settlement_payout: float
    hold_seconds: float
    pnl: float


class ReplayMarketStateObserver(MarketStateObserver):
    """Market observer with an in-memory settled-round ledger for fast replay."""

    def __init__(self, *, window_size: int, touch_threshold: float, fee_bps: int) -> None:
        super().__init__(db_path=None, window_size=window_size, touch_threshold=touch_threshold)
        self.replay_rounds: list[dict[str, Any]] = []
        self.replay_fee_bps = fee_bps

    def get_recent_rounds(self, limit: int | None = None) -> list[dict[str, Any]]:
        return list(reversed(self.replay_rounds[-(limit or self.window_size) :]))

    def settle_replay_round(
        self,
        market_id: int,
        winner: str,
        baseline: BacktestTrade | None,
    ) -> None:
        with self.lock:
            snapshot = self._round_snapshot_locked()
            up_touched = bool(snapshot["up_touched"])
            down_touched = bool(snapshot["down_touched"])
            self.replay_rounds.append(
                {
                    "market_id": market_id,
                    "winner": winner,
                    "up_touched": 1 if up_touched else 0,
                    "down_touched": 1 if down_touched else 0,
                    "both_touched": 1 if up_touched and down_touched else 0,
                    "winner_touched": 1
                    if (winner == "UP" and up_touched) or (winner == "DOWN" and down_touched)
                    else 0,
                    "crossover_count": int(snapshot["crossover_count"]),
                    "effective_crossover_count": int(snapshot["effective_crossover_count"]),
                    "avg_er_60s": snapshot["avg_er_60s"],
                    "median_er_60s": snapshot["median_er_60s"],
                    "early_er_60s": snapshot["early_er_60s"],
                    "p75_er_60s": snapshot["p75_er_60s"],
                    "m01_filled": 1 if baseline else 0,
                    "m01_won": 1 if baseline and baseline.side == winner else 0,
                    "m01_fill_price": baseline.entry_price if baseline else None,
                    "m01_avg_fill_price": baseline.entry_price if baseline else None,
                    "m01_avg_fee_rate_bps": float(self.replay_fee_bps) if baseline else None,
                }
            )
            self._classification_cache = None


def deterministic_m0_side(seed: int, market_id: int) -> str:
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    return "UP" if digest[0] < 128 else "DOWN"


def _split_labels(market_ids: list[int]) -> dict[int, str]:
    total = len(market_ids)
    development_end = math.ceil(total * 0.60)
    validation_end = math.ceil(total * 0.80)
    return {
        market_id: (
            "development"
            if index < development_end
            else "validation" if index < validation_end else "holdout"
        )
        for index, market_id in enumerate(market_ids)
    }


def _valid_entry(row: sqlite3.Row, spec: M01BacktestSpec, side: str) -> tuple[float, float] | None:
    try:
        seconds_left = float(row["seconds_left"])
        top = {key: float(row[key]) for key in ("up_ask", "up_bid", "down_ask", "down_bid")}
        visible_size = float(row[f"{side.lower()}_ask_size"])
        book_skew = float(row["book_skew_ms"])
        book_age = float(row["book_age_ms"])
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(seconds_left)
        and spec.min_seconds_left_exclusive < seconds_left <= spec.max_seconds_left_inclusive
        and all(math.isfinite(value) for value in top.values())
        and 0 < top["up_ask"] <= 1
        and 0 <= top["up_bid"] <= top["up_ask"]
        and 0 < top["down_ask"] <= 1
        and 0 <= top["down_bid"] <= top["down_ask"]
        and math.isfinite(visible_size)
        and visible_size > 0
        and math.isfinite(book_skew)
        and 0 <= book_skew <= spec.max_book_skew_ms
        and math.isfinite(book_age)
        and 0 <= book_age <= spec.max_book_age_ms
    ):
        return None
    entry = top[f"{side.lower()}_ask"]
    return (entry, visible_size) if entry <= spec.max_entry else None


def run_m01_backtests(db_path: Path, specs: Iterable[M01BacktestSpec]) -> dict[str, Any]:
    specs = list(specs)
    if not specs:
        raise ValueError("at least one strategy specification is required")
    for spec in specs:
        spec.validate()

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        market_rows = db.execute(
            """SELECT o.market_id, MIN(o.id) AS first_observation_id
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               GROUP BY o.market_id ORDER BY first_observation_id"""
        ).fetchall()
        market_ids = [int(row["market_id"]) for row in market_rows]
        split_by_market = _split_labels(market_ids)
        unresolved = {spec.name: set(market_ids) for spec in specs}
        trades: list[BacktestTrade] = []
        rows = db.execute(
            """SELECT o.*, s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               ORDER BY o.id"""
        )
        for row in rows:
            market_id = int(row["market_id"])
            for spec in specs:
                if market_id not in unresolved[spec.name]:
                    continue
                side = deterministic_m0_side(spec.seed, market_id)
                entry = _valid_entry(row, spec, side)
                if entry is None:
                    continue
                price, visible_size = entry
                requested_shares = spec.stake / price
                shares = min(requested_shares, visible_size)
                filled_stake = shares * price
                fee = taker_fee(shares, price, spec.fee_bps)
                winner = str(row["official_winner"])
                pnl = (shares if side == winner else 0.0) - filled_stake - fee
                trades.append(
                    BacktestTrade(
                        strategy=spec.name,
                        split=split_by_market[market_id],
                        market_id=market_id,
                        observation_id=int(row["id"]),
                        timestamp=str(row["timestamp"]),
                        side=side,
                        winner=winner,
                        seconds_left=float(row["seconds_left"]),
                        entry_price=price,
                        visible_ask_size=visible_size,
                        requested_stake=spec.stake,
                        filled_stake=filled_stake,
                        shares=shares,
                        fee=fee,
                        pnl=pnl,
                    )
                )
                unresolved[spec.name].remove(market_id)
    finally:
        db.close()

    summaries: dict[str, Any] = {}
    for spec in specs:
        strategy_trades = [trade for trade in trades if trade.strategy == spec.name]
        summaries[spec.name] = {
            "config": asdict(spec),
            "overall": _summarize(strategy_trades, len(market_ids)),
            "splits": {
                split: _summarize(
                    [trade for trade in strategy_trades if trade.split == split],
                    sum(value == split for value in split_by_market.values()),
                )
                for split in ("development", "validation", "holdout")
            },
        }
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDatabase": str(db_path.resolve()),
        "sourceModel": "recorded_observations_top_of_book",
        "candidateMarkets": len(market_ids),
        "splitPolicy": "chronological_60_20_20",
        "summaries": summaries,
        "trades": [asdict(trade) for trade in trades],
    }


def _valid_target_exit(
    row: sqlite3.Row,
    spec: M01BacktestSpec,
    side: str,
    target: float,
    shares: float,
) -> tuple[float, float] | None:
    try:
        bid = float(row[f"{side.lower()}_bid"])
        visible_size = float(row[f"{side.lower()}_bid_size"])
        book_skew = float(row["book_skew_ms"])
        book_age = float(row["book_age_ms"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if not (
        math.isfinite(bid)
        and target <= bid <= 1
        and math.isfinite(visible_size)
        and visible_size + 1e-12 >= shares
        and math.isfinite(book_skew)
        and 0 <= book_skew <= spec.max_book_skew_ms
        and math.isfinite(book_age)
        and 0 <= book_age <= spec.max_book_age_ms
    ):
        return None
    return bid, visible_size


def _take_profit_trade(
    *,
    entry_row: sqlite3.Row,
    later_rows: list[sqlite3.Row],
    spec: M01BacktestSpec,
    split: str,
    side: str,
    price: float,
    visible_size: float,
    target: float | None,
) -> M01TakeProfitTrade:
    requested_shares = spec.stake / price
    shares = min(requested_shares, visible_size)
    filled_stake = shares * price
    entry_fee = taker_fee(shares, price, spec.fee_bps)
    winner = str(entry_row["official_winner"])
    strategy = (
        f"{spec.name}_HOLD"
        if target is None
        else f"{spec.name}_TP_{target:.2f}"
    )
    if target is not None:
        for row in later_rows:
            fill = _valid_target_exit(row, spec, side, target, shares)
            if fill is None:
                continue
            observed_bid, bid_size = fill
            exit_fee = taker_fee(shares, target, spec.fee_bps)
            entry_seconds_left = float(entry_row["seconds_left"])
            exit_seconds_left = float(row["seconds_left"])
            return M01TakeProfitTrade(
                strategy=strategy,
                split=split,
                market_id=int(entry_row["market_id"]),
                observation_id=int(entry_row["id"]),
                timestamp=str(entry_row["timestamp"]),
                side=side,
                winner=winner,
                seconds_left=entry_seconds_left,
                entry_price=price,
                visible_ask_size=visible_size,
                requested_stake=spec.stake,
                filled_stake=filled_stake,
                shares=shares,
                entry_fee=entry_fee,
                target_price=target,
                exit_reason="TARGET_FILLED",
                exit_observation_id=int(row["id"]),
                exit_timestamp=str(row["timestamp"]),
                exit_seconds_left=exit_seconds_left,
                observed_exit_bid=observed_bid,
                visible_bid_size=bid_size,
                exit_price=target,
                exit_fee=exit_fee,
                hold_seconds=max(0.0, entry_seconds_left - exit_seconds_left),
                pnl=shares * target - filled_stake - entry_fee - exit_fee,
            )
    payout = shares if side == winner else 0.0
    return M01TakeProfitTrade(
        strategy=strategy,
        split=split,
        market_id=int(entry_row["market_id"]),
        observation_id=int(entry_row["id"]),
        timestamp=str(entry_row["timestamp"]),
        side=side,
        winner=winner,
        seconds_left=float(entry_row["seconds_left"]),
        entry_price=price,
        visible_ask_size=visible_size,
        requested_stake=spec.stake,
        filled_stake=filled_stake,
        shares=shares,
        entry_fee=entry_fee,
        target_price=target,
        exit_reason="SETTLED_WIN" if side == winner else "SETTLED_LOSS",
        exit_observation_id=None,
        exit_timestamp=None,
        exit_seconds_left=0.0,
        observed_exit_bid=None,
        visible_bid_size=None,
        exit_price=1.0 if side == winner else 0.0,
        exit_fee=0.0,
        hold_seconds=float(entry_row["seconds_left"]),
        pnl=payout - filled_stake - entry_fee,
    )


def _summarize_take_profit(
    trades: list[M01TakeProfitTrade], candidate_markets: int
) -> dict[str, Any]:
    base = _summarize_like(trades, candidate_markets)
    target_exits = [trade for trade in trades if trade.exit_reason == "TARGET_FILLED"]
    base.update(
        {
            "targetExits": len(target_exits),
            "targetExitRate": len(target_exits) / len(trades) if trades else None,
            "settlementWins": sum(
                trade.exit_reason == "SETTLED_WIN" for trade in trades
            ),
            "settlementLosses": sum(
                trade.exit_reason == "SETTLED_LOSS" for trade in trades
            ),
            "averageHoldSeconds": (
                sum(float(trade.hold_seconds or 0.0) for trade in trades)
                / len(trades) if trades else None
            ),
            "averageTargetHoldSeconds": (
                sum(float(trade.hold_seconds or 0.0) for trade in target_exits)
                / len(target_exits) if target_exits else None
            ),
        }
    )
    return base


def _summarize_like(
    trades: list[BacktestTrade] | list[M01TakeProfitTrade],
    candidate_markets: int,
) -> dict[str, Any]:
    wins = sum(trade.pnl > 0 for trade in trades)
    losses = sum(trade.pnl <= 0 for trade in trades)
    realized = sum(trade.pnl for trade in trades)
    cost = sum(
        trade.filled_stake
        + float(getattr(trade, "fee", getattr(trade, "entry_fee", 0.0)))
        for trade in trades
    )
    gross_profit = sum(max(0.0, trade.pnl) for trade in trades)
    gross_loss = -sum(min(0.0, trade.pnl) for trade in trades)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_consecutive_losses = 0
    consecutive_losses = 0
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if trade.pnl <= 0:
            consecutive_losses += 1
            max_consecutive_losses = max(
                max_consecutive_losses, consecutive_losses
            )
        else:
            consecutive_losses = 0
    return {
        "candidateMarkets": candidate_markets,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "coverageRate": len(trades) / candidate_markets if candidate_markets else None,
        "winRate": wins / len(trades) if trades else None,
        "averageEntryPrice": (
            sum(t.entry_price for t in trades) / len(trades) if trades else None
        ),
        "averageSecondsLeft": (
            sum(t.seconds_left for t in trades) / len(trades) if trades else None
        ),
        "filledCost": cost,
        "realizedPnl": realized,
        "roi": realized / cost if cost else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_drawdown,
        "maxConsecutiveLosses": max_consecutive_losses,
        "upTrades": sum(t.side == "UP" for t in trades),
        "downTrades": sum(t.side == "DOWN" for t in trades),
    }


def run_t180_take_profit_sweep(
    db_path: Path,
    targets: Iterable[float] | None = None,
    spec: M01BacktestSpec | None = None,
) -> dict[str, Any]:
    """Sweep fixed T180 take-profit prices using later recorded bid depth."""
    spec = spec or M01BacktestSpec(
        name="M01T180", min_seconds_left_exclusive=180
    )
    spec.validate()
    target_values = sorted(
        {round(float(value), 6) for value in (
            targets if targets is not None
            else (index / 100 for index in range(31, 96))
        )}
    )
    if not target_values:
        raise ValueError("at least one take-profit target is required")
    if any(target <= spec.max_entry or target > 1 for target in target_values):
        raise ValueError("take-profit targets must exceed max_entry and be <= 1")

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    try:
        market_rows = db.execute(
            """SELECT o.market_id, MIN(o.id) AS first_observation_id
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               GROUP BY o.market_id ORDER BY first_observation_id"""
        ).fetchall()
        market_ids = [int(row["market_id"]) for row in market_rows]
        split_by_market = _split_labels(market_ids)
        trades_by_target: dict[float | None, list[M01TakeProfitTrade]] = {
            None: [], **{target: [] for target in target_values}
        }

        def process_market(rows: list[sqlite3.Row]) -> None:
            if not rows:
                return
            market_id = int(rows[0]["market_id"])
            side = deterministic_m0_side(spec.seed, market_id)
            for index, row in enumerate(rows):
                entry = _valid_entry(row, spec, side)
                if entry is None:
                    continue
                price, visible_size = entry
                split = split_by_market[market_id]
                later_rows = rows[index + 1 :]
                for target in (None, *target_values):
                    trades_by_target[target].append(
                        _take_profit_trade(
                            entry_row=row,
                            later_rows=later_rows,
                            spec=spec,
                            split=split,
                            side=side,
                            price=price,
                            visible_size=visible_size,
                            target=target,
                        )
                    )
                return

        current_market_id: int | None = None
        current_rows: list[sqlite3.Row] = []
        rows = db.execute(
            """SELECT o.*, s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               ORDER BY o.id"""
        )
        for row in rows:
            market_id = int(row["market_id"])
            if current_market_id is not None and market_id != current_market_id:
                process_market(current_rows)
                current_rows = []
            current_market_id = market_id
            current_rows.append(row)
        process_market(current_rows)
    finally:
        db.close()

    split_counts = {
        split: sum(value == split for value in split_by_market.values())
        for split in ("development", "validation", "holdout")
    }
    baseline_trades = trades_by_target[None]
    baseline = {
        "overall": _summarize_take_profit(baseline_trades, len(market_ids)),
        "splits": {
            split: _summarize_take_profit(
                [trade for trade in baseline_trades if trade.split == split],
                split_counts[split],
            )
            for split in split_counts
        },
    }
    summaries: dict[str, Any] = {}
    for target in target_values:
        target_trades = trades_by_target[target]
        summaries[f"{target:.2f}"] = {
            "targetPrice": target,
            "overall": _summarize_take_profit(
                target_trades, len(market_ids)
            ),
            "splits": {
                split: _summarize_take_profit(
                    [trade for trade in target_trades if trade.split == split],
                    split_counts[split],
                )
                for split in split_counts
            },
        }
    selected_key = max(
        summaries,
        key=lambda key: (
            float(summaries[key]["splits"]["development"]["realizedPnl"]),
            -float(summaries[key]["splits"]["development"]["maxDrawdown"]),
        ),
    )
    selected_target = float(selected_key)
    selected_trades = trades_by_target[selected_target]
    ranking = sorted(
        summaries.values(),
        key=lambda value: (
            float(value["splits"]["development"]["realizedPnl"]),
            -float(value["splits"]["development"]["maxDrawdown"]),
        ),
        reverse=True,
    )
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDatabase": str(db_path.resolve()),
        "sourceModel": "recorded_observations_full_depth_target_limit",
        "candidateMarkets": len(market_ids),
        "splitPolicy": "chronological_60_20_20",
        "strategyConfig": asdict(spec),
        "exitModel": {
            "fillRule": "later held-side bid >= target and visible bid size >= all shares",
            "fillPrice": "fixed target limit price",
            "entryAndExitFeesIncluded": True,
            "partialTargetFills": False,
            "fallback": "official settlement",
            "fidelityNote": "Replays recorded observation ticks; moves between saved ticks cannot be reconstructed.",
        },
        "selection": {
            "metric": "maximum development realizedPnl; lower development maxDrawdown breaks ties",
            "selectedTarget": selected_target,
            "validationAndHoldoutExcludedFromSelection": True,
        },
        "baseline": baseline,
        "summaries": summaries,
        "developmentRanking": [
            {
                "targetPrice": value["targetPrice"],
                "realizedPnl": value["splits"]["development"]["realizedPnl"],
                "roi": value["splits"]["development"]["roi"],
                "maxDrawdown": value["splits"]["development"]["maxDrawdown"],
                "targetExitRate": value["splits"]["development"]["targetExitRate"],
            }
            for value in ranking
        ],
        "trades": [asdict(trade) for trade in selected_trades],
    }


def _valid_replay_bid(
    row: sqlite3.Row,
    spec: M01BacktestSpec,
    side: str,
) -> tuple[float, float] | None:
    try:
        bid = float(row[f"{side.lower()}_bid"])
        visible_size = float(row[f"{side.lower()}_bid_size"])
        book_skew = float(row["book_skew_ms"])
        book_age = float(row["book_age_ms"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if not (
        math.isfinite(bid)
        and 0 <= bid <= 1
        and math.isfinite(visible_size)
        and visible_size >= 0
        and math.isfinite(book_skew)
        and 0 <= book_skew <= spec.max_book_skew_ms
        and math.isfinite(book_age)
        and 0 <= book_age <= spec.max_book_age_ms
    ):
        return None
    return bid, visible_size


def _advanced_exit_trade(
    *,
    entry_row: sqlite3.Row,
    later_rows: list[sqlite3.Row],
    spec: M01BacktestSpec,
    split: str,
    side: str,
    price: float,
    visible_size: float,
    variant: dict[str, Any],
) -> M01AdvancedExitTrade:
    requested_shares = spec.stake / price
    shares = min(requested_shares, visible_size)
    filled_stake = shares * price
    entry_fee = taker_fee(shares, price, spec.fee_bps)
    winner = str(entry_row["official_winner"])
    entry_seconds_left = float(entry_row["seconds_left"])
    method = str(variant["method"])
    trigger = variant.get("triggerPrice")
    fraction = float(variant.get("exitFraction") or 0.0)
    trailing_drop = variant.get("trailingDrop")

    exit_row: sqlite3.Row | None = None
    exit_price: float | None = None
    exit_reason = ""
    if method == "PARTIAL_TP":
        exited_shares = shares * fraction
        for row in later_rows:
            fill = _valid_target_exit(
                row, spec, side, float(trigger), exited_shares
            )
            if fill is None:
                continue
            exit_row = row
            exit_price = float(trigger)
            exit_reason = "PARTIAL_TARGET_FILLED"
            break
    elif method == "TRAILING":
        exited_shares = shares
        high_bid: float | None = None
        for row in later_rows:
            valid_bid = _valid_replay_bid(row, spec, side)
            if valid_bid is None:
                continue
            bid, bid_size = valid_bid
            if high_bid is None:
                if bid >= float(trigger):
                    high_bid = bid
                continue
            if bid > high_bid:
                high_bid = bid
                continue
            if (
                bid <= high_bid - float(trailing_drop)
                and bid_size + 1e-12 >= shares
            ):
                exit_row = row
                exit_price = bid
                exit_reason = "TRAILING_EXIT_FILLED"
                break
    else:
        exited_shares = 0.0

    if exit_row is not None and exit_price is not None:
        remaining_shares = shares - exited_shares
        exit_fee = taker_fee(
            exited_shares, exit_price, spec.fee_bps
        )
        settlement_payout = (
            remaining_shares if side == winner else 0.0
        )
        exit_seconds_left = float(exit_row["seconds_left"])
        elapsed_to_exit = max(0.0, entry_seconds_left - exit_seconds_left)
        weighted_hold = (
            exited_shares * elapsed_to_exit
            + remaining_shares * entry_seconds_left
        ) / shares
        valid_bid = _valid_replay_bid(exit_row, spec, side)
        assert valid_bid is not None
        observed_bid, bid_size = valid_bid
        return M01AdvancedExitTrade(
            strategy=str(variant["id"]), split=split,
            market_id=int(entry_row["market_id"]),
            observation_id=int(entry_row["id"]),
            timestamp=str(entry_row["timestamp"]), side=side, winner=winner,
            seconds_left=entry_seconds_left, entry_price=price,
            visible_ask_size=visible_size, requested_stake=spec.stake,
            filled_stake=filled_stake, shares=shares, entry_fee=entry_fee,
            exit_method=method, trigger_price=float(trigger),
            exit_fraction=fraction, trailing_drop=(
                float(trailing_drop) if trailing_drop is not None else None
            ),
            exit_reason=exit_reason,
            exit_observation_id=int(exit_row["id"]),
            exit_timestamp=str(exit_row["timestamp"]),
            exit_seconds_left=exit_seconds_left,
            observed_exit_bid=observed_bid, visible_bid_size=bid_size,
            exit_price=exit_price, exited_shares=exited_shares,
            exit_fee=exit_fee, remaining_shares=remaining_shares,
            settlement_payout=settlement_payout, hold_seconds=weighted_hold,
            pnl=(
                exited_shares * exit_price + settlement_payout
                - filled_stake - entry_fee - exit_fee
            ),
        )

    settlement_payout = shares if side == winner else 0.0
    return M01AdvancedExitTrade(
        strategy=str(variant["id"]), split=split,
        market_id=int(entry_row["market_id"]),
        observation_id=int(entry_row["id"]),
        timestamp=str(entry_row["timestamp"]), side=side, winner=winner,
        seconds_left=entry_seconds_left, entry_price=price,
        visible_ask_size=visible_size, requested_stake=spec.stake,
        filled_stake=filled_stake, shares=shares, entry_fee=entry_fee,
        exit_method=method, trigger_price=(
            float(trigger) if trigger is not None else None
        ),
        exit_fraction=fraction, trailing_drop=(
            float(trailing_drop) if trailing_drop is not None else None
        ),
        exit_reason="SETTLED_WIN" if side == winner else "SETTLED_LOSS",
        exit_observation_id=None, exit_timestamp=None,
        exit_seconds_left=0.0, observed_exit_bid=None,
        visible_bid_size=None, exit_price=(1.0 if side == winner else 0.0),
        exited_shares=0.0, exit_fee=0.0, remaining_shares=shares,
        settlement_payout=settlement_payout,
        hold_seconds=entry_seconds_left,
        pnl=settlement_payout - filled_stake - entry_fee,
    )


def _summarize_advanced_exit(
    trades: list[M01AdvancedExitTrade], candidate_markets: int
) -> dict[str, Any]:
    summary = _summarize_like(trades, candidate_markets)
    exits = [trade for trade in trades if trade.exit_observation_id is not None]
    summary.update(
        {
            "exitFills": len(exits),
            "exitFillRate": len(exits) / len(trades) if trades else None,
            "averageExitedFraction": (
                sum(trade.exited_shares / trade.shares for trade in exits)
                / len(exits) if exits else None
            ),
            "averageHoldSeconds": (
                sum(trade.hold_seconds for trade in trades) / len(trades)
                if trades else None
            ),
            "settlementWins": sum(
                trade.exit_reason == "SETTLED_WIN" for trade in trades
            ),
            "settlementLosses": sum(
                trade.exit_reason == "SETTLED_LOSS" for trade in trades
            ),
        }
    )
    return summary


def run_t180_advanced_exit_sweep(
    db_path: Path,
    spec: M01BacktestSpec | None = None,
) -> dict[str, Any]:
    """Compare partial targets and armed trailing exits for T180."""
    spec = spec or M01BacktestSpec(
        name="M01T180", min_seconds_left_exclusive=180
    )
    spec.validate()
    variants: list[dict[str, Any]] = [
        {
            "id": "HOLD_TO_SETTLEMENT", "method": "HOLD",
            "triggerPrice": None, "exitFraction": 0.0,
            "trailingDrop": None,
        }
    ]
    for target in (0.85, 0.86, 0.87, 0.88, 0.89, 0.90):
        for fraction in (0.25, 0.50):
            variants.append(
                {
                    "id": f"PARTIAL_TP_{target:.2f}_P{int(fraction * 100)}",
                    "method": "PARTIAL_TP", "triggerPrice": target,
                    "exitFraction": fraction, "trailingDrop": None,
                }
            )
    for arm in (0.75, 0.80, 0.85, 0.90):
        for drop in (0.05, 0.10, 0.15, 0.20):
            variants.append(
                {
                    "id": f"TRAIL_ARM_{arm:.2f}_D{int(drop * 100):02d}",
                    "method": "TRAILING", "triggerPrice": arm,
                    "exitFraction": 1.0, "trailingDrop": drop,
                }
            )
    trades_by_variant: dict[str, list[M01AdvancedExitTrade]] = {
        str(variant["id"]): [] for variant in variants
    }
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    try:
        market_rows = db.execute(
            """SELECT o.market_id, MIN(o.id) AS first_observation_id
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               GROUP BY o.market_id ORDER BY first_observation_id"""
        ).fetchall()
        market_ids = [int(row["market_id"]) for row in market_rows]
        split_by_market = _split_labels(market_ids)

        def process_market(rows: list[sqlite3.Row]) -> None:
            if not rows:
                return
            market_id = int(rows[0]["market_id"])
            side = deterministic_m0_side(spec.seed, market_id)
            for index, row in enumerate(rows):
                entry = _valid_entry(row, spec, side)
                if entry is None:
                    continue
                price, visible_size = entry
                for variant in variants:
                    trades_by_variant[str(variant["id"])].append(
                        _advanced_exit_trade(
                            entry_row=row, later_rows=rows[index + 1 :],
                            spec=spec, split=split_by_market[market_id],
                            side=side, price=price,
                            visible_size=visible_size, variant=variant,
                        )
                    )
                return

        current_market_id: int | None = None
        current_rows: list[sqlite3.Row] = []
        for row in db.execute(
            """SELECT o.*, s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               ORDER BY o.id"""
        ):
            market_id = int(row["market_id"])
            if current_market_id is not None and market_id != current_market_id:
                process_market(current_rows)
                current_rows = []
            current_market_id = market_id
            current_rows.append(row)
        process_market(current_rows)
    finally:
        db.close()

    split_counts = {
        split: sum(value == split for value in split_by_market.values())
        for split in ("development", "validation", "holdout")
    }
    variant_by_id = {str(variant["id"]): variant for variant in variants}
    summaries: dict[str, Any] = {}
    for variant_id, variant_trades in trades_by_variant.items():
        summaries[variant_id] = {
            "config": variant_by_id[variant_id],
            "overall": _summarize_advanced_exit(
                variant_trades, len(market_ids)
            ),
            "splits": {
                split: _summarize_advanced_exit(
                    [trade for trade in variant_trades if trade.split == split],
                    split_counts[split],
                )
                for split in split_counts
            },
        }
    ranking = sorted(
        summaries.items(),
        key=lambda item: (
            float(item[1]["splits"]["development"]["realizedPnl"]),
            -float(item[1]["splits"]["development"]["maxDrawdown"]),
        ),
        reverse=True,
    )
    selected_id = ranking[0][0]
    family_selections: dict[str, str] = {}
    for family in ("PARTIAL_TP", "TRAIL_ARM"):
        family_selections[family] = next(
            variant_id for variant_id, _ in ranking
            if variant_id.startswith(family)
        )
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDatabase": str(db_path.resolve()),
        "sourceModel": "recorded_observations_depth_aware_advanced_exit",
        "candidateMarkets": len(market_ids),
        "splitPolicy": "chronological_60_20_20",
        "strategyConfig": asdict(spec),
        "exitModel": {
            "partialTargetFill": "fixed target price; enough visible bid depth for the partial quantity",
            "trailingFill": "observed bid after arm and high-water pullback; enough visible bid depth for all shares",
            "entryAndExitFeesIncluded": True,
            "unexitedShares": "official settlement",
            "fidelityNote": "Replays recorded observation ticks; moves between saved ticks cannot be reconstructed.",
        },
        "selection": {
            "metric": "maximum development realizedPnl; lower development maxDrawdown breaks ties",
            "selectedVariant": selected_id,
            "familySelections": family_selections,
            "validationAndHoldoutExcludedFromSelection": True,
        },
        "summaries": summaries,
        "developmentRanking": [
            {
                "variant": variant_id,
                "realizedPnl": values["splits"]["development"]["realizedPnl"],
                "roi": values["splits"]["development"]["roi"],
                "maxDrawdown": values["splits"]["development"]["maxDrawdown"],
                "exitFillRate": values["splits"]["development"]["exitFillRate"],
            }
            for variant_id, values in ranking
        ],
        "trades": [asdict(trade) for trade in trades_by_variant[selected_id]],
    }


def _make_trade(
    row: sqlite3.Row,
    spec: M01BacktestSpec,
    strategy: str,
    split: str,
    side: str,
    price: float,
    visible_size: float,
) -> BacktestTrade:
    requested_shares = spec.stake / price
    shares = min(requested_shares, visible_size)
    filled_stake = shares * price
    fee = taker_fee(shares, price, spec.fee_bps)
    winner = str(row["official_winner"])
    pnl = (shares if side == winner else 0.0) - filled_stake - fee
    return BacktestTrade(
        strategy=strategy,
        split=split,
        market_id=int(row["market_id"]),
        observation_id=int(row["id"]),
        timestamp=str(row["timestamp"]),
        side=side,
        winner=winner,
        seconds_left=float(row["seconds_left"]),
        entry_price=price,
        visible_ask_size=visible_size,
        requested_stake=spec.stake,
        filled_stake=filled_stake,
        shares=shares,
        fee=fee,
        pnl=pnl,
    )


def run_observer_replay(
    db_path: Path,
    spec: M01BacktestSpec | None = None,
    *,
    min_settled_samples: int = 6,
    window_size: int = 20,
) -> dict[str, Any]:
    """Causally rebuild Market Observer and replay F2/F1/LIVE gates.

    Official winners are consumed only after the last recorded observation of a
    market. The observer's settled-round ledger exists only in memory.
    """
    spec = spec or M01BacktestSpec(name="M01")
    spec.validate()
    if min_settled_samples < 1 or window_size < min_settled_samples:
        raise ValueError("observer window must cover the minimum settled samples")

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    source = sqlite3.connect(uri, uri=True, timeout=60)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("PRAGMA busy_timeout=60000")
    market_rows = source.execute(
        """SELECT o.market_id, MIN(o.id) AS first_observation_id
           FROM observations AS o
           JOIN market_settlements AS s ON s.market_id=o.market_id
           WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
           GROUP BY o.market_id ORDER BY first_observation_id"""
    ).fetchall()
    market_ids = [int(row["market_id"]) for row in market_rows]
    split_by_market = _split_labels(market_ids)
    profiles = {"F2": "M01O", "F1": "M01O_F1", "LIVE": "M01O_LIVE"}
    trades: list[BacktestTrade] = []
    opened: dict[str, set[int]] = {name: set() for name in ("M01", *profiles.values())}
    gate_evaluations: Counter[str] = Counter()
    gate_blocks: dict[str, Counter[str]] = {profile: Counter() for profile in profiles}
    baseline_by_market: dict[int, BacktestTrade] = {}

    observer = ReplayMarketStateObserver(
        window_size=window_size,
        touch_threshold=spec.max_entry,
        fee_bps=spec.fee_bps,
    )
    try:
        active_market: int | None = None
        active_winner: str | None = None
        active_start_price: float | None = None

        def settle_active() -> None:
            if active_market is None or active_winner not in {"UP", "DOWN"}:
                return
            baseline = baseline_by_market.get(active_market)
            observer.settle_replay_round(active_market, active_winner, baseline)

        rows = source.execute(
            """SELECT o.*, s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               ORDER BY o.id"""
        )
        for row in rows:
            market_id = int(row["market_id"])
            timestamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            now_ts = timestamp.timestamp()
            if market_id != active_market:
                settle_active()
                active_market = market_id
                active_winner = str(row["official_winner"])
                active_start_price = float(row["start_price"])
                market_start_ts = now_ts - (300.0 - float(row["seconds_left"]))
                observer.reset_market(market_id, active_start_price, market_start_ts)

            observer.update_tick(
                now_ts,
                float(row["spot_price"]) if row["spot_price"] is not None else None,
                float(row["up_ask"]) if row["up_ask"] is not None else None,
                float(row["down_ask"]) if row["down_ask"] is not None else None,
                market_id=market_id,
            )
            side = deterministic_m0_side(spec.seed, market_id)
            entry = _valid_entry(row, spec, side)
            if entry is None:
                continue
            price, visible_size = entry
            split = split_by_market[market_id]
            if market_id not in opened["M01"]:
                baseline = _make_trade(row, spec, "M01", split, side, price, visible_size)
                trades.append(baseline)
                baseline_by_market[market_id] = baseline
                opened["M01"].add(market_id)

            for profile, strategy in profiles.items():
                if market_id in opened[strategy]:
                    continue
                gate = observer.m01o_entry_gate(
                    min_settled_samples=min_settled_samples,
                    min_current_range_score=2 if profile == "F2" else 1,
                    profile=profile,
                    now_ts=now_ts,
                )
                gate_evaluations[profile] += 1
                if gate.get("allowed") is True:
                    trades.append(
                        _make_trade(row, spec, strategy, split, side, price, visible_size)
                    )
                    opened[strategy].add(market_id)
                else:
                    gate_blocks[profile][str(gate.get("blockCategory") or "UNKNOWN")] += 1
        settle_active()
    finally:
        source.close()

    summaries: dict[str, Any] = {}
    for strategy in ("M01", *profiles.values()):
        strategy_trades = [trade for trade in trades if trade.strategy == strategy]
        summaries[strategy] = {
            "overall": _summarize(strategy_trades, len(market_ids)),
            "splits": {
                split: _summarize(
                    [trade for trade in strategy_trades if trade.split == split],
                    sum(value == split for value in split_by_market.values()),
                )
                for split in ("development", "validation", "holdout")
            },
        }
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDatabase": str(db_path.resolve()),
        "sourceModel": "observer_replay_recorded_observations",
        "candidateMarkets": len(market_ids),
        "splitPolicy": "chronological_60_20_20",
        "observer": {
            "causalSettlement": True,
            "windowSize": window_size,
            "minSettledSamples": min_settled_samples,
            "profiles": list(profiles),
            "gateEvaluations": dict(gate_evaluations),
            "blockCategories": {
                profile: dict(counts) for profile, counts in gate_blocks.items()
            },
            "fidelityNote": "Replays recorded observation ticks; events between saved ticks cannot be reconstructed.",
        },
        "summaries": summaries,
        "trades": [asdict(trade) for trade in trades],
    }


def _summarize(trades: list[BacktestTrade], candidate_markets: int) -> dict[str, Any]:
    wins = sum(trade.pnl > 0 for trade in trades)
    losses = sum(trade.pnl <= 0 for trade in trades)
    realized = sum(trade.pnl for trade in trades)
    cost = sum(trade.filled_stake + trade.fee for trade in trades)
    gross_profit = sum(max(0.0, trade.pnl) for trade in trades)
    gross_loss = -sum(min(0.0, trade.pnl) for trade in trades)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "candidateMarkets": candidate_markets,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "coverageRate": len(trades) / candidate_markets if candidate_markets else None,
        "winRate": wins / len(trades) if trades else None,
        "averageEntryPrice": sum(t.entry_price for t in trades) / len(trades) if trades else None,
        "averageSecondsLeft": sum(t.seconds_left for t in trades) / len(trades) if trades else None,
        "filledCost": cost,
        "realizedPnl": realized,
        "roi": realized / cost if cost else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_drawdown,
        "upTrades": sum(t.side == "UP" for t in trades),
        "downTrades": sum(t.side == "DOWN" for t in trades),
    }


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"backtest-{stamp}.json"
    csv_path = output_dir / f"backtest-{stamp}-trades.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    trades = report["trades"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = (
            list(trades[0])
            if trades else list(BacktestTrade.__dataclass_fields__)
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)
    return json_path, csv_path


def _load_specs(path: Path | None) -> list[M01BacktestSpec]:
    if path is None:
        return [
            M01BacktestSpec(name="M01_BACKTEST"),
            M01BacktestSpec(name="M01T180_BACKTEST", min_seconds_left_exclusive=180),
        ]
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("strategies") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("strategy file must contain a list or a strategies list")
    return [M01BacktestSpec(**value) for value in values]


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay recorded BTC 5m books against M01-family strategies")
    parser.add_argument("--database", type=Path, default=Path("data/simulation.db"))
    parser.add_argument("--strategies", type=Path, help="JSON strategy specification file")
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtests"))
    parser.add_argument(
        "--observer-replay",
        action="store_true",
        help="causally rebuild Market Observer and compare M01O F2/F1/LIVE",
    )
    parser.add_argument(
        "--t180-take-profit-sweep",
        action="store_true",
        help="scan fixed T180 take-profit prices from 0.31 through 0.95",
    )
    parser.add_argument(
        "--t180-advanced-exit-sweep",
        action="store_true",
        help="compare partial T180 targets with armed high-water trailing exits",
    )
    args = parser.parse_args()
    try:
        replay_modes = sum((
            bool(args.observer_replay),
            bool(args.t180_take_profit_sweep),
            bool(args.t180_advanced_exit_sweep),
        ))
        if replay_modes > 1:
            raise SystemExit("choose only one replay mode")
        if args.t180_advanced_exit_sweep:
            specs = _load_specs(args.strategies)
            if len(specs) != 1 and args.strategies is not None:
                raise SystemExit("advanced exit sweep accepts one T180 specification")
            spec = (
                specs[0]
                if args.strategies is not None
                else M01BacktestSpec(
                    name="M01T180", min_seconds_left_exclusive=180
                )
            )
            report = run_t180_advanced_exit_sweep(args.database, spec=spec)
        elif args.t180_take_profit_sweep:
            specs = _load_specs(args.strategies)
            if len(specs) != 1 and args.strategies is not None:
                raise SystemExit("take-profit sweep accepts one T180 specification")
            spec = (
                specs[0]
                if args.strategies is not None
                else M01BacktestSpec(
                    name="M01T180", min_seconds_left_exclusive=180
                )
            )
            report = run_t180_take_profit_sweep(args.database, spec=spec)
        elif args.observer_replay:
            specs = _load_specs(args.strategies)
            if len(specs) != 1 and args.strategies is not None:
                raise SystemExit("observer replay accepts exactly one shared M01 specification")
            report = run_observer_replay(args.database, specs[0])
        else:
            report = run_m01_backtests(args.database, _load_specs(args.strategies))
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"backtest could not obtain a consistent read of {args.database}: {exc}"
        ) from None
    json_path, csv_path = write_report(report, args.output_dir)
    if args.t180_advanced_exit_sweep:
        selected = report["selection"]["selectedVariant"]
        families = report["selection"]["familySelections"]
        print(json.dumps({
            "selection": report["selection"],
            "selected": report["summaries"][selected],
            "bestPartial": report["summaries"][families["PARTIAL_TP"]],
            "bestTrailing": report["summaries"][families["TRAIL_ARM"]],
            "developmentTop10": report["developmentRanking"][:10],
        }, ensure_ascii=False, indent=2))
    elif args.t180_take_profit_sweep:
        selected = report["selection"]["selectedTarget"]
        print(json.dumps({
            "selection": report["selection"],
            "baseline": report["baseline"],
            "selected": report["summaries"][f"{selected:.2f}"],
            "developmentTop10": report["developmentRanking"][:10],
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report["summaries"], ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
