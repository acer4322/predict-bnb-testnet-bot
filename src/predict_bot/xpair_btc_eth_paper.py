from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "xpair_btc_eth_paper.db"
STRATEGY_NAME = "XPAIR_BTC_ETH_PAPER"
BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
VARIANTS = (
    ("BTC_UP_ETH_DOWN", "UP", "DOWN"),
    ("BTC_DOWN_ETH_UP", "DOWN", "UP"),
)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalized_asks(book: dict[str, Any]) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for raw in book.get("asks") or []:
        if isinstance(raw, dict):
            price = _finite(raw.get("price"))
            size = _finite(raw.get("size", raw.get("quantity")))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            price = _finite(raw[0])
            size = _finite(raw[1])
        else:
            continue
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            continue
        levels.append((price, size))
    levels.sort(key=lambda level: level[0])
    return levels


def entry_fee(shares: float, price: float, fee_bps: int) -> float:
    return shares * min(price, 1.0 - price) * max(0, int(fee_bps)) / 10_000.0


def equal_share_cross_fill(
    first_levels: Sequence[tuple[float, float]],
    second_levels: Sequence[tuple[float, float]],
    *,
    total_budget: float,
    first_fee_bps: int,
    second_fee_bps: int,
    max_total_cost_per_share: float,
) -> dict[str, float | int | bool] | None:
    """Buy equal shares from two independent books while the total-cost cap holds."""
    if (
        not first_levels
        or not second_levels
        or not math.isfinite(total_budget)
        or total_budget <= 0
        or not math.isfinite(max_total_cost_per_share)
        or max_total_cost_per_share <= 0
    ):
        return None

    first_top = first_levels[0][0]
    second_top = second_levels[0][0]
    top_unit_fee = entry_fee(1.0, first_top, first_fee_bps) + entry_fee(
        1.0, second_top, second_fee_bps
    )
    top_unit_cost = first_top + second_top + top_unit_fee
    if top_unit_cost > max_total_cost_per_share + 1e-12:
        return None

    requested_shares = total_budget / top_unit_cost
    remaining_shares = requested_shares
    remaining_budget = total_budget
    first_index = second_index = 0
    first_remaining = first_levels[0][1]
    second_remaining = second_levels[0][1]
    filled_shares = 0.0
    first_notional = second_notional = 0.0
    first_fees = second_fees = 0.0
    first_levels_used: set[int] = set()
    second_levels_used: set[int] = set()

    while (
        remaining_shares > 1e-12
        and remaining_budget > 1e-12
        and first_index < len(first_levels)
        and second_index < len(second_levels)
    ):
        first_price = first_levels[first_index][0]
        second_price = second_levels[second_index][0]
        first_unit_fee = entry_fee(1.0, first_price, first_fee_bps)
        second_unit_fee = entry_fee(1.0, second_price, second_fee_bps)
        unit_cost = first_price + second_price + first_unit_fee + second_unit_fee
        if unit_cost > max_total_cost_per_share + 1e-12:
            break
        quantity = min(
            remaining_shares,
            first_remaining,
            second_remaining,
            remaining_budget / unit_cost,
        )
        if quantity <= 1e-12:
            break

        filled_shares += quantity
        first_notional += quantity * first_price
        second_notional += quantity * second_price
        first_fees += quantity * first_unit_fee
        second_fees += quantity * second_unit_fee
        remaining_shares -= quantity
        remaining_budget -= quantity * unit_cost
        first_remaining -= quantity
        second_remaining -= quantity
        first_levels_used.add(first_index)
        second_levels_used.add(second_index)

        if first_remaining <= 1e-12:
            first_index += 1
            if first_index < len(first_levels):
                first_remaining = first_levels[first_index][1]
        if second_remaining <= 1e-12:
            second_index += 1
            if second_index < len(second_levels):
                second_remaining = second_levels[second_index][1]

    if filled_shares <= 1e-12:
        return None

    total_cost = first_notional + second_notional + first_fees + second_fees
    return {
        "requested_shares": requested_shares,
        "filled_shares": filled_shares,
        "fill_ratio": filled_shares / requested_shares,
        "partial_fill": filled_shares + 1e-12 < requested_shares,
        "first_vwap": first_notional / filled_shares,
        "second_vwap": second_notional / filled_shares,
        "first_fee": first_fees,
        "second_fee": second_fees,
        "total_cost": total_cost,
        "cost_per_share": total_cost / filled_shares,
        "first_levels_used": len(first_levels_used),
        "second_levels_used": len(second_levels_used),
    }


def classify_pair_result(
    *,
    btc_side: str,
    eth_side: str,
    btc_winner: str,
    eth_winner: str,
    shares: float | None,
    total_cost: float | None,
) -> dict[str, float | int | bool | None]:
    btc_leg_won = btc_side == btc_winner
    eth_leg_won = eth_side == eth_winner
    winning_legs = int(btc_leg_won) + int(eth_leg_won)
    payout = None
    pnl = None
    if shares is not None and total_cost is not None:
        payout = float(shares) * winning_legs
        pnl = payout - float(total_cost)
    return {
        "btc_leg_won": btc_leg_won,
        "eth_leg_won": eth_leg_won,
        "winning_legs": winning_legs,
        "double_loss": winning_legs == 0,
        "payout": payout,
        "pnl": pnl,
    }


def wilson_interval(
    successes: int,
    total: int,
    z: float = 1.959963984540054,
) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1.0 + (z * z) / total
    centre = (p + (z * z) / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt((p * (1.0 - p) / total) + (z * z) / (4.0 * total * total))
        / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


@dataclass(frozen=True)
class MarketRef:
    symbol: str
    topic_id: int
    market_id: int
    title: str
    start_ms: int
    end_ms: int
    start_price: float
    fee_bps: int
    up_token_id: str
    down_token_id: str


def market_ref_from_topic(
    topic: dict[str, Any], select_binary_market: Any
) -> MarketRef | None:
    try:
        selected = topic.get("_selectedMarket") or select_binary_market(topic)
        variant = topic.get("variantData") or {}
        return MarketRef(
            symbol=str(topic["symbol"]),
            topic_id=int(topic["marketTopicId"]),
            market_id=int(selected["market"]["marketId"]),
            title=str(topic.get("title") or ""),
            start_ms=int(topic["startDate"]),
            end_ms=int(topic["endDate"]),
            start_price=float(variant["startPrice"]),
            fee_bps=int(topic.get("feeRateBps") or 0),
            up_token_id=str(selected["up"]["tokenId"]),
            down_token_id=str(selected["down"]["tokenId"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def choose_active_topic(
    topics: Iterable[dict[str, Any]], symbol: str, now_ms: int
) -> dict[str, Any] | None:
    candidates = [
        topic
        for topic in topics
        if topic.get("chartType") == "CRYPTO_UP_DOWN"
        and topic.get("symbol") == symbol
        and 0
        < int(topic.get("endDate") or 0) - int(topic.get("startDate") or 0)
        <= 301_000
        and int(topic.get("endDate") or 0) > now_ms
    ]
    active = [
        topic
        for topic in candidates
        if int(topic.get("startDate") or 0)
        <= now_ms
        < int(topic.get("endDate") or 0)
    ]
    return min(active, key=lambda topic: int(topic["endDate"]), default=None)


def markets_are_aligned(btc: MarketRef, eth: MarketRef, tolerance_ms: int) -> bool:
    return (
        abs(btc.start_ms - eth.start_ms) <= tolerance_ms
        and abs(btc.end_ms - eth.end_ms) <= tolerance_ms
    )


class XPairStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._init()

    def close(self) -> None:
        self.db.close()

    def _init(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS xpair_trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                variant TEXT NOT NULL,
                btc_topic_id INTEGER NOT NULL,
                btc_market_id INTEGER NOT NULL,
                eth_topic_id INTEGER NOT NULL,
                eth_market_id INTEGER NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                btc_start_price REAL NOT NULL,
                eth_start_price REAL NOT NULL,
                signal_at TEXT NOT NULL,
                seconds_left REAL NOT NULL,
                btc_side TEXT NOT NULL CHECK (btc_side IN ('UP','DOWN')),
                eth_side TEXT NOT NULL CHECK (eth_side IN ('UP','DOWN')),
                entry_status TEXT NOT NULL,
                rejection_reason TEXT,
                eligible INTEGER NOT NULL DEFAULT 0,
                requested_stake REAL NOT NULL,
                requested_shares REAL,
                filled_shares REAL,
                fill_ratio REAL,
                btc_vwap REAL,
                eth_vwap REAL,
                btc_fee REAL,
                eth_fee REAL,
                total_cost REAL,
                cost_per_share REAL,
                btc_book_age_ms REAL,
                eth_book_age_ms REAL,
                cross_book_skew_ms REAL,
                btc_winner TEXT CHECK (btc_winner IN ('UP','DOWN') OR btc_winner IS NULL),
                eth_winner TEXT CHECK (eth_winner IN ('UP','DOWN') OR eth_winner IS NULL),
                winning_legs INTEGER,
                double_loss INTEGER,
                payout REAL,
                pnl REAL,
                settlement_status TEXT NOT NULL DEFAULT 'PENDING',
                settled_at TEXT,
                diagnostics_json TEXT,
                UNIQUE(variant, btc_market_id, eth_market_id)
            );
            CREATE INDEX IF NOT EXISTS xpair_trials_variant_idx
                ON xpair_trials(variant, id);
            CREATE INDEX IF NOT EXISTS xpair_trials_pending_idx
                ON xpair_trials(settlement_status, end_ms);
            """
        )
        self.db.commit()

    def has_capture(self, btc_market_id: int, eth_market_id: int) -> bool:
        row = self.db.execute(
            """SELECT 1 FROM xpair_trials
               WHERE btc_market_id=? AND eth_market_id=? LIMIT 1""",
            (btc_market_id, eth_market_id),
        ).fetchone()
        return row is not None

    def record_capture(
        self,
        *,
        btc: MarketRef,
        eth: MarketRef,
        signal_at: str,
        seconds_left: float,
        requested_stake: float,
        btc_book_age_ms: float | None,
        eth_book_age_ms: float | None,
        cross_book_skew_ms: float | None,
        trials: Sequence[dict[str, Any]],
    ) -> None:
        for trial in trials:
            self.db.execute(
                """INSERT OR IGNORE INTO xpair_trials(
                       strategy, variant, btc_topic_id, btc_market_id,
                       eth_topic_id, eth_market_id, start_ms, end_ms,
                       btc_start_price, eth_start_price, signal_at, seconds_left,
                       btc_side, eth_side, entry_status, rejection_reason, eligible,
                       requested_stake, requested_shares, filled_shares, fill_ratio,
                       btc_vwap, eth_vwap, btc_fee, eth_fee, total_cost,
                       cost_per_share, btc_book_age_ms, eth_book_age_ms,
                       cross_book_skew_ms, diagnostics_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    STRATEGY_NAME,
                    trial["variant"],
                    btc.topic_id,
                    btc.market_id,
                    eth.topic_id,
                    eth.market_id,
                    min(btc.start_ms, eth.start_ms),
                    max(btc.end_ms, eth.end_ms),
                    btc.start_price,
                    eth.start_price,
                    signal_at,
                    seconds_left,
                    trial["btc_side"],
                    trial["eth_side"],
                    trial["entry_status"],
                    trial.get("rejection_reason"),
                    int(bool(trial.get("eligible"))),
                    requested_stake,
                    trial.get("requested_shares"),
                    trial.get("filled_shares"),
                    trial.get("fill_ratio"),
                    trial.get("btc_vwap"),
                    trial.get("eth_vwap"),
                    trial.get("btc_fee"),
                    trial.get("eth_fee"),
                    trial.get("total_cost"),
                    trial.get("cost_per_share"),
                    btc_book_age_ms,
                    eth_book_age_ms,
                    cross_book_skew_ms,
                    json.dumps(trial.get("diagnostics") or {}, sort_keys=True),
                ),
            )
        self.db.commit()

    def pending_pairs(self, now_ms: int) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT btc_topic_id, btc_market_id, eth_topic_id, eth_market_id,
                      btc_start_price, eth_start_price, MAX(end_ms) AS end_ms
                 FROM xpair_trials
                WHERE settlement_status='PENDING' AND end_ms <= ?
                GROUP BY btc_topic_id, btc_market_id, eth_topic_id, eth_market_id,
                         btc_start_price, eth_start_price
                ORDER BY end_ms ASC""",
            (now_ms,),
        ).fetchall()

    def settle_pair(
        self,
        *,
        btc_market_id: int,
        eth_market_id: int,
        btc_winner: str,
        eth_winner: str,
        settled_at: str,
    ) -> None:
        rows = self.db.execute(
            """SELECT * FROM xpair_trials
               WHERE btc_market_id=? AND eth_market_id=?
                 AND settlement_status='PENDING'""",
            (btc_market_id, eth_market_id),
        ).fetchall()
        for row in rows:
            result = classify_pair_result(
                btc_side=str(row["btc_side"]),
                eth_side=str(row["eth_side"]),
                btc_winner=btc_winner,
                eth_winner=eth_winner,
                shares=(
                    float(row["filled_shares"])
                    if row["filled_shares"] is not None
                    else None
                ),
                total_cost=(
                    float(row["total_cost"])
                    if row["total_cost"] is not None
                    else None
                ),
            )
            self.db.execute(
                """UPDATE xpair_trials
                      SET btc_winner=?, eth_winner=?, winning_legs=?, double_loss=?,
                          payout=?, pnl=?, settlement_status='SETTLED', settled_at=?
                    WHERE id=?""",
                (
                    btc_winner,
                    eth_winner,
                    int(result["winning_legs"]),
                    int(bool(result["double_loss"])),
                    result["payout"],
                    result["pnl"],
                    settled_at,
                    int(row["id"]),
                ),
            )
        self.db.commit()

    def summary(self) -> dict[str, Any]:
        matched_rounds = int(
            self.db.execute(
                """SELECT COUNT(*) FROM (
                       SELECT DISTINCT btc_market_id, eth_market_id
                         FROM xpair_trials
                   )"""
            ).fetchone()[0]
        )
        variants: dict[str, Any] = {}
        for variant, _, _ in VARIANTS:
            row = self.db.execute(
                """SELECT COUNT(*) AS observed,
                          SUM(CASE WHEN settlement_status='SETTLED' THEN 1 ELSE 0 END) AS settled,
                          SUM(eligible) AS eligible,
                          SUM(CASE WHEN settlement_status='SETTLED' AND eligible=1 THEN 1 ELSE 0 END) AS eligible_settled,
                          SUM(CASE WHEN settlement_status='SETTLED' AND double_loss=1 THEN 1 ELSE 0 END) AS all_double_losses,
                          SUM(CASE WHEN settlement_status='SETTLED' AND eligible=1 AND double_loss=1 THEN 1 ELSE 0 END) AS eligible_double_losses,
                          SUM(CASE WHEN settlement_status='SETTLED' AND eligible=1 AND winning_legs=1 THEN 1 ELSE 0 END) AS eligible_one_win,
                          SUM(CASE WHEN settlement_status='SETTLED' AND eligible=1 AND winning_legs=2 THEN 1 ELSE 0 END) AS eligible_two_wins,
                          SUM(CASE WHEN settlement_status='SETTLED' AND eligible=1 THEN pnl ELSE 0 END) AS eligible_pnl,
                          SUM(CASE WHEN settlement_status='SETTLED' AND eligible=1 THEN total_cost ELSE 0 END) AS eligible_cost
                     FROM xpair_trials WHERE variant=?""",
                (variant,),
            ).fetchone()
            integer_keys = (
                "observed",
                "settled",
                "eligible",
                "eligible_settled",
                "all_double_losses",
                "eligible_double_losses",
                "eligible_one_win",
                "eligible_two_wins",
            )
            data = {key: int(row[key] or 0) for key in integer_keys}
            data["eligible_pnl"] = float(row["eligible_pnl"] or 0.0)
            data["eligible_cost"] = float(row["eligible_cost"] or 0.0)
            data["all_double_loss_rate"] = (
                data["all_double_losses"] / data["settled"]
                if data["settled"]
                else None
            )
            data["eligible_double_loss_rate"] = (
                data["eligible_double_losses"] / data["eligible_settled"]
                if data["eligible_settled"]
                else None
            )
            lower, upper = wilson_interval(
                data["eligible_double_losses"], data["eligible_settled"]
            )
            data["eligible_double_loss_ci95"] = [lower, upper]
            data["eligible_roi"] = (
                data["eligible_pnl"] / data["eligible_cost"]
                if data["eligible_cost"] > 0
                else None
            )
            variants[variant] = data

        outcome_rows = self.db.execute(
            """SELECT btc_winner, eth_winner,
                      COUNT(DISTINCT btc_market_id || ':' || eth_market_id) AS rounds
                 FROM xpair_trials
                WHERE settlement_status='SETTLED'
                GROUP BY btc_winner, eth_winner"""
        ).fetchall()
        joint_outcomes = {
            f"BTC_{row['btc_winner']}_ETH_{row['eth_winner']}": int(row["rounds"])
            for row in outcome_rows
        }
        return {
            "strategy": STRATEGY_NAME,
            "paper_only": True,
            "matched_rounds": matched_rounds,
            "variants": variants,
            "joint_outcomes": joint_outcomes,
        }


def _book_timestamp_ms(book: dict[str, Any]) -> int | None:
    return _integer(book.get("updateTimestampMs", book.get("timestamp")))


def _book_metrics(
    up_book: dict[str, Any], down_book: dict[str, Any], now_ms: int
) -> tuple[float | None, float | None, list[int]]:
    timestamps = [
        timestamp
        for timestamp in (_book_timestamp_ms(up_book), _book_timestamp_ms(down_book))
        if timestamp is not None
    ]
    if len(timestamps) != 2:
        return None, None, timestamps
    return (
        max(0.0, float(now_ms - min(timestamps))),
        float(max(timestamps) - min(timestamps)),
        timestamps,
    )


def build_trials(
    *,
    btc: MarketRef,
    eth: MarketRef,
    books: dict[str, dict[str, Any]],
    total_stake: float,
    max_total_cost_per_share: float,
    minimum_filled_shares: float,
    max_book_age_ms: float,
    max_book_skew_ms: float,
    max_cross_book_skew_ms: float,
    now_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    btc_age, btc_skew, btc_timestamps = _book_metrics(
        books["btc_up"], books["btc_down"], now_ms
    )
    eth_age, eth_skew, eth_timestamps = _book_metrics(
        books["eth_up"], books["eth_down"], now_ms
    )
    all_timestamps = btc_timestamps + eth_timestamps
    cross_skew = (
        float(max(all_timestamps) - min(all_timestamps))
        if len(all_timestamps) == 4
        else None
    )
    books_valid = bool(
        btc_age is not None
        and eth_age is not None
        and btc_skew is not None
        and eth_skew is not None
        and cross_skew is not None
        and btc_age <= max_book_age_ms
        and eth_age <= max_book_age_ms
        and btc_skew <= max_book_skew_ms
        and eth_skew <= max_book_skew_ms
        and cross_skew <= max_cross_book_skew_ms
    )

    ask_levels = {
        "BTC_UP": normalized_asks(books["btc_up"]),
        "BTC_DOWN": normalized_asks(books["btc_down"]),
        "ETH_UP": normalized_asks(books["eth_up"]),
        "ETH_DOWN": normalized_asks(books["eth_down"]),
    }
    trials: list[dict[str, Any]] = []
    for variant, btc_side, eth_side in VARIANTS:
        base: dict[str, Any] = {
            "variant": variant,
            "btc_side": btc_side,
            "eth_side": eth_side,
            "eligible": False,
            "entry_status": "SKIPPED_BOOK",
            "rejection_reason": None,
            "diagnostics": {
                "paper_only": True,
                "atomic_execution_assumed": False,
                "equal_shares_required": True,
                "one_win_break_even_condition": "cost_per_share < 1",
            },
        }
        if not books_valid:
            base["rejection_reason"] = "BOOK_FRESHNESS_OR_SKEW"
            trials.append(base)
            continue
        btc_levels = ask_levels[f"BTC_{btc_side}"]
        eth_levels = ask_levels[f"ETH_{eth_side}"]
        if not btc_levels or not eth_levels:
            base["rejection_reason"] = "EMPTY_ASK_LEVELS"
            trials.append(base)
            continue
        top_cost = (
            btc_levels[0][0]
            + eth_levels[0][0]
            + entry_fee(1.0, btc_levels[0][0], btc.fee_bps)
            + entry_fee(1.0, eth_levels[0][0], eth.fee_bps)
        )
        fill = equal_share_cross_fill(
            btc_levels,
            eth_levels,
            total_budget=total_stake,
            first_fee_bps=btc.fee_bps,
            second_fee_bps=eth.fee_bps,
            max_total_cost_per_share=max_total_cost_per_share,
        )
        if fill is None:
            base["entry_status"] = (
                "SKIPPED_PRICE"
                if top_cost > max_total_cost_per_share
                else "SKIPPED_DEPTH"
            )
            base["rejection_reason"] = (
                "TOP_COST_ABOVE_LIMIT"
                if top_cost > max_total_cost_per_share
                else "NO_EQUAL_SHARE_FILL"
            )
            base["diagnostics"]["top_cost_per_share"] = top_cost
            trials.append(base)
            continue
        base.update(
            requested_shares=fill["requested_shares"],
            filled_shares=fill["filled_shares"],
            fill_ratio=fill["fill_ratio"],
            btc_vwap=fill["first_vwap"],
            eth_vwap=fill["second_vwap"],
            btc_fee=fill["first_fee"],
            eth_fee=fill["second_fee"],
            total_cost=fill["total_cost"],
            cost_per_share=fill["cost_per_share"],
        )
        base["diagnostics"].update(
            {
                "top_cost_per_share": top_cost,
                "btc_levels_used": fill["first_levels_used"],
                "eth_levels_used": fill["second_levels_used"],
                "partial_fill": fill["partial_fill"],
            }
        )
        if float(fill["filled_shares"]) + 1e-12 < minimum_filled_shares:
            base["entry_status"] = "SKIPPED_DEPTH"
            base["rejection_reason"] = "MINIMUM_FILLED_SHARES"
        else:
            base["entry_status"] = "ELIGIBLE"
            base["eligible"] = True
        trials.append(base)

    return trials, {
        "btc_book_age_ms": btc_age,
        "eth_book_age_ms": eth_age,
        "cross_book_skew_ms": cross_skew,
    }


def discover_active_pair(
    client: Any,
    select_binary_market: Any,
    *,
    now_ms: int,
    tolerance_ms: int,
    max_pages: int = 5,
) -> tuple[MarketRef, MarketRef] | None:
    topics: list[dict[str, Any]] = []
    offset = 0
    for _ in range(max(1, max_pages)):
        response = client.list_markets(offset=offset)
        page = response.get("marketTopics") or []
        topics.extend(topic for topic in page if isinstance(topic, dict))
        btc_topic = choose_active_topic(topics, BTC_SYMBOL, now_ms)
        eth_topic = choose_active_topic(topics, ETH_SYMBOL, now_ms)
        if btc_topic is not None and eth_topic is not None:
            btc = market_ref_from_topic(btc_topic, select_binary_market)
            eth = market_ref_from_topic(eth_topic, select_binary_market)
            if btc and eth and markets_are_aligned(btc, eth, tolerance_ms):
                return btc, eth
            return None
        if not response.get("hasMore"):
            break
        offset += int(response.get("limit") or 100)
    return None


def fetch_books(
    client: Any, btc: MarketRef, eth: MarketRef
) -> dict[str, dict[str, Any]]:
    requests = {
        "btc_up": (btc.market_id, btc.up_token_id),
        "btc_down": (btc.market_id, btc.down_token_id),
        "eth_up": (eth.market_id, eth.up_token_id),
        "eth_down": (eth.market_id, eth.down_token_id),
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            key: pool.submit(client.orderbook, market_id, token_id)
            for key, (market_id, token_id) in requests.items()
        }
        return {key: future.result() for key, future in futures.items()}


def detail_winner(client: Any, topic_id: int, start_price: float) -> str | None:
    detail = client.market_detail(topic_id)
    end_raw = (detail.get("variantData") or {}).get("endPrice")
    end_price = _finite(end_raw)
    if end_price is None:
        return None
    return "UP" if end_price > start_price else "DOWN"


def reconcile_settlements(store: XPairStore, client: Any, now_ms: int) -> int:
    settled = 0
    for pending in store.pending_pairs(now_ms):
        btc_winner = detail_winner(
            client, int(pending["btc_topic_id"]), float(pending["btc_start_price"])
        )
        eth_winner = detail_winner(
            client, int(pending["eth_topic_id"]), float(pending["eth_start_price"])
        )
        if btc_winner is None or eth_winner is None:
            continue
        store.settle_pair(
            btc_market_id=int(pending["btc_market_id"]),
            eth_market_id=int(pending["eth_market_id"]),
            btc_winner=btc_winner,
            eth_winner=eth_winner,
            settled_at=utc_iso(),
        )
        settled += 1
    return settled


def print_summary(summary: dict[str, Any]) -> None:
    print(
        f"[{STRATEGY_NAME}] matched_rounds={summary['matched_rounds']} "
        f"paper_only={summary['paper_only']}"
    )
    for variant, data in summary["variants"].items():
        rate = data["eligible_double_loss_rate"]
        ci = data["eligible_double_loss_ci95"]
        rate_text = "n/a" if rate is None else f"{rate * 100:.2f}%"
        ci_text = (
            "n/a"
            if ci[0] is None
            else f"{ci[0] * 100:.2f}%..{ci[1] * 100:.2f}%"
        )
        roi = data["eligible_roi"]
        roi_text = "n/a" if roi is None else f"{roi * 100:.2f}%"
        print(
            f"  {variant}: eligible_settled={data['eligible_settled']} "
            f"double_loss={data['eligible_double_losses']} rate={rate_text} "
            f"ci95={ci_text} one_win={data['eligible_one_win']} "
            f"two_wins={data['eligible_two_wins']} roi={roi_text}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Independent paper-only BTC/ETH opposite-side pair experiment. "
            "No live order endpoint is called."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--discovery-interval", type=float, default=5.0)
    parser.add_argument("--settlement-interval", type=float, default=15.0)
    parser.add_argument("--entry-seconds-left", type=float, default=180.0)
    parser.add_argument("--entry-window-seconds", type=float, default=10.0)
    parser.add_argument("--stake", type=float, default=10.0)
    parser.add_argument("--max-total-cost", type=float, default=0.98)
    parser.add_argument("--minimum-filled-shares", type=float, default=1.0)
    parser.add_argument("--max-book-age-ms", type=float, default=2000.0)
    parser.add_argument("--max-book-skew-ms", type=float, default=500.0)
    parser.add_argument("--max-cross-book-skew-ms", type=float, default=1000.0)
    parser.add_argument("--market-time-tolerance-ms", type=int, default=2000)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    positive = (
        "interval",
        "discovery_interval",
        "settlement_interval",
        "entry_seconds_left",
        "entry_window_seconds",
        "stake",
        "max_total_cost",
        "minimum_filled_shares",
        "max_book_age_ms",
        "max_book_skew_ms",
        "max_cross_book_skew_ms",
        "market_time_tolerance_ms",
    )
    for key in positive:
        if float(getattr(args, key)) <= 0:
            raise SystemExit(f"--{key.replace('_', '-')} must be positive")
    if args.entry_window_seconds >= args.entry_seconds_left:
        raise SystemExit("--entry-window-seconds must be smaller than entry time")
    if args.max_total_cost >= 2:
        raise SystemExit("--max-total-cost must be below 2")


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    store = XPairStore(args.db)
    if args.summary_only:
        print_summary(store.summary())
        store.close()
        return 0

    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        store.close()
        raise SystemExit(
            "BINANCE_API_KEY and BINANCE_API_SECRET are required (read-only is enough)."
        )

    from .core import BinancePredictionClient, select_binary_market

    client = BinancePredictionClient(api_key, api_secret)
    current_pair: tuple[MarketRef, MarketRef] | None = None
    next_discovery = 0.0
    next_settlement = 0.0
    last_status: str | None = None

    print(
        f"Starting {STRATEGY_NAME}: db={args.db} stake={args.stake:.2f} "
        f"max_total_cost={args.max_total_cost:.4f} "
        f"entry={args.entry_seconds_left:.1f}s"
    )
    print(
        "Paper only. No Prediction trade, cancel, redeem, transfer, or "
        "withdrawal endpoint is used."
    )

    try:
        while True:
            loop_started = time.monotonic()
            try:
                now_ms = client.server_timestamp_ms()
                if loop_started >= next_settlement:
                    next_settlement = loop_started + args.settlement_interval
                    settled = reconcile_settlements(store, client, now_ms)
                    if settled:
                        print(f"Settled {settled} matched round(s).")
                        print_summary(store.summary())

                if current_pair is not None and now_ms >= max(
                    current_pair[0].end_ms, current_pair[1].end_ms
                ):
                    current_pair = None

                if current_pair is None and loop_started >= next_discovery:
                    next_discovery = loop_started + args.discovery_interval
                    current_pair = discover_active_pair(
                        client,
                        select_binary_market,
                        now_ms=now_ms,
                        tolerance_ms=args.market_time_tolerance_ms,
                    )

                if current_pair is None:
                    status = "WAITING_FOR_ALIGNED_BTC_ETH_MARKETS"
                else:
                    btc, eth = current_pair
                    seconds_left = max(
                        0.0, (min(btc.end_ms, eth.end_ms) - now_ms) / 1000.0
                    )
                    if store.has_capture(btc.market_id, eth.market_id):
                        status = (
                            f"CAPTURED market={btc.market_id}/{eth.market_id} "
                            f"left={seconds_left:.1f}s"
                        )
                    elif not (
                        args.entry_seconds_left - args.entry_window_seconds
                        <= seconds_left
                        <= args.entry_seconds_left
                    ):
                        status = f"WAITING_ENTRY_WINDOW left={seconds_left:.1f}s"
                    else:
                        books = fetch_books(client, btc, eth)
                        after_fetch_ms = client.server_timestamp_ms()
                        trials, metrics = build_trials(
                            btc=btc,
                            eth=eth,
                            books=books,
                            total_stake=args.stake,
                            max_total_cost_per_share=args.max_total_cost,
                            minimum_filled_shares=args.minimum_filled_shares,
                            max_book_age_ms=args.max_book_age_ms,
                            max_book_skew_ms=args.max_book_skew_ms,
                            max_cross_book_skew_ms=args.max_cross_book_skew_ms,
                            now_ms=after_fetch_ms,
                        )
                        store.record_capture(
                            btc=btc,
                            eth=eth,
                            signal_at=utc_iso(),
                            seconds_left=max(
                                0.0,
                                (min(btc.end_ms, eth.end_ms) - after_fetch_ms)
                                / 1000.0,
                            ),
                            requested_stake=args.stake,
                            btc_book_age_ms=metrics["btc_book_age_ms"],
                            eth_book_age_ms=metrics["eth_book_age_ms"],
                            cross_book_skew_ms=metrics["cross_book_skew_ms"],
                            trials=trials,
                        )
                        status = f"CAPTURED market={btc.market_id}/{eth.market_id}"
                        for trial in trials:
                            print(
                                f"  {trial['variant']}: {trial['entry_status']} "
                                f"cost/share={trial.get('cost_per_share')} "
                                f"filled={trial.get('filled_shares')}"
                            )
                        print_summary(store.summary())
                        if args.once:
                            return 0

                if status != last_status:
                    print(status)
                    last_status = status
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                status = "RATE_LIMITED" if "HTTP 429" in str(exc) else "ERROR"
                print(f"{status}: {str(exc)[:300]}")
                retry_after = getattr(client, "retry_after_seconds", None)
                if status == "RATE_LIMITED":
                    time.sleep(max(10.0, float(retry_after or 0.0)))

            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("Stopped.")
        print_summary(store.summary())
        return 0
    finally:
        client.close()
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
