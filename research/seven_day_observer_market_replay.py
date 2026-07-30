from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from predict_bot.backtest import ReplayMarketStateObserver
from predict_bot.core import taker_fee
from predict_bot.drawdown_control import MarketRegimeDrawdownController
from predict_bot.research_forward import (
    FUTURES_LEAD_OBSERVER_VERSIONS,
    RESEARCH_PARAMETERS,
    ResearchSampleBuffer,
    execution_candidate,
    futures_lead_observer_decision,
    signal_for_strategy,
)


REPLAYABLE_STRATEGIES = (
    "R_FUTURES_LEAD",
    "R_MICROPRICE",
    "R_OFI",
    "R_CALIBRATED_VALUE",
    "R_OFI_EVENT_CUM",
    "R_OFI_EVENT_CUM_FILTERED",
)
UNAVAILABLE_STRATEGIES = {
    "R_CONSENSUS": (
        "the rule requires futures returns, but observations.futures_price is NULL throughout "
        "the seven-day window"
    ),
}
VERSIONS = ("BASE", *FUTURES_LEAD_OBSERVER_VERSIONS)
CURRENT_COMBINATION = {
    "R_FUTURES_LEAD": "V2",
    "R_CALIBRATED_VALUE": "V6",
    "R_MICROPRICE": "V6",
}
CURRENT_STRATEGY_STAKES = {
    "R_FUTURES_LEAD": 4.0,
    "R_CALIBRATED_VALUE": 2.0,
    "R_MICROPRICE": 2.0,
}
DRAW_DOWN_SUFFIX = "+DD20"
DRAW_DOWN_LOOKBACK_MARKETS = 6
DRAW_DOWN_MIN_ABS_PRIOR_NET_BPS = 20.0
DRAW_DOWN_MAX_OPPOSED_MOVE_BPS = 3.0
MICRO_MAX_AGE_NS = 1_500_000_000
WINDOW_DAYS = 7
WARMUP_MARKETS = 20
STAKE = 5.0
FEE_BPS = 200
SLIPPAGE_BPS = 50.0
MINIMUM_STAKE = 2.0
MAX_SPREAD = 0.03
MIN_ENTRY = 0.05
MAX_BOOK_AGE_MS = 2000.0
MAX_BOOK_SKEW_MS = 500.0


def _timestamp_ns(value: str) -> int:
    return int(_dt(value).timestamp() * 1_000_000_000)


class _MicroFuturesReplay:
    def __init__(self, path: Path, *, start_ns: int, end_ns: int) -> None:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        self.db = sqlite3.connect(uri, uri=True, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")
        self.db.execute("PRAGMA busy_timeout=60000")
        self.rows = iter(
            self.db.execute(
                """SELECT timestamp_ns, market_id, spot_price, futures_price
                     FROM microstructure_snapshots
                    WHERE timestamp_ns BETWEEN ? AND ?
                      AND market_id IS NOT NULL
                      AND spot_price IS NOT NULL
                      AND futures_price IS NOT NULL
                    ORDER BY timestamp_ns""",
                (int(start_ns), int(end_ns)),
            )
        )
        self.pending = next(self.rows, None)
        self.latest_by_market: dict[int, tuple[int, float, float]] = {}
        self.rows_read = 0
        self.matches = 0

    def match(self, market_id: int, target_ns: int) -> dict[str, float] | None:
        while self.pending is not None and int(self.pending["timestamp_ns"]) <= target_ns:
            row = self.pending
            self.latest_by_market[int(row["market_id"])] = (
                int(row["timestamp_ns"]),
                float(row["spot_price"]),
                float(row["futures_price"]),
            )
            self.rows_read += 1
            self.pending = next(self.rows, None)
        selected = self.latest_by_market.get(int(market_id))
        if selected is None:
            return None
        age_ns = target_ns - selected[0]
        if not 0 <= age_ns <= MICRO_MAX_AGE_NS:
            return None
        self.matches += 1
        return {
            "timestamp_ns": float(selected[0]),
            "spot_price": selected[1],
            "futures_price": selected[2],
            "age_ms": age_ns / 1_000_000.0,
        }

    def close(self) -> None:
        self.db.close()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _split_labels(market_ids: list[int]) -> dict[int, str]:
    development_end = math.ceil(len(market_ids) * 0.60)
    validation_end = math.ceil(len(market_ids) * 0.80)
    return {
        market_id: (
            "development"
            if index < development_end
            else "validation"
            if index < validation_end
            else "holdout"
        )
        for index, market_id in enumerate(market_ids)
    }


def _market_metadata(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT o.market_id,
                  MIN(o.id) AS first_id,
                  MAX(o.id) AS last_id,
                  COUNT(*) AS observation_count,
                  MIN(o.timestamp) AS first_timestamp,
                  MAX(o.timestamp) AS last_timestamp,
                  MAX(o.seconds_left) AS max_seconds_left,
                  MIN(o.seconds_left) AS min_seconds_left,
                  s.official_winner,
                  s.start_price AS official_start_price,
                  s.official_end_price
             FROM observations AS o
             JOIN market_settlements AS s ON s.market_id=o.market_id
            WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
            GROUP BY o.market_id
            ORDER BY first_id"""
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        first_timestamp = _dt(str(row["first_timestamp"]))
        start_estimate = first_timestamp - timedelta(
            seconds=300.0 - float(row["max_seconds_left"])
        )
        result.append(
            {
                "market_id": int(row["market_id"]),
                "first_id": int(row["first_id"]),
                "last_id": int(row["last_id"]),
                "observation_count": int(row["observation_count"]),
                "first_timestamp": first_timestamp,
                "last_timestamp": _dt(str(row["last_timestamp"])),
                "start_estimate": start_estimate,
                "max_seconds_left": float(row["max_seconds_left"]),
                "min_seconds_left": float(row["min_seconds_left"]),
                "winner": str(row["official_winner"]),
                "official_start_price": (
                    float(row["official_start_price"])
                    if row["official_start_price"] is not None
                    else None
                ),
                "official_end_price": (
                    float(row["official_end_price"])
                    if row["official_end_price"] is not None
                    else None
                ),
            }
        )
    return result


def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _event_key(row: sqlite3.Row) -> str:
    up_timestamp = row["up_book_timestamp_ms"]
    down_timestamp = row["down_book_timestamp_ms"]
    if up_timestamp is not None or down_timestamp is not None:
        return f"{up_timestamp}:{down_timestamp}"
    return f"observation:{int(row['id'])}"


def _source_candidate(
    strategy: str,
    market_id: int,
    current: dict[str, float],
    snapshot: dict[str, Any],
    buffer: ResearchSampleBuffer,
    max_entry: float | None = None,
) -> dict[str, Any] | None:
    params = RESEARCH_PARAMETERS[strategy]
    seconds_left = float(current["seconds_left"])
    horizon = float(params["horizon"])
    if not horizon - 3.0 <= seconds_left <= horizon:
        return None

    previous = None
    cumulative_event_ofi = None
    lag = float(params.get("lag", 0.0))
    if lag:
        previous = buffer.lagged(market_id, current, lag)
    if strategy in {"R_OFI_EVENT_CUM", "R_OFI_EVENT_CUM_FILTERED"}:
        cumulative_event_ofi = buffer.cumulative_event_ofi(
            market_id,
            current,
            float(params["window"]),
            int(params["min_events"]),
        )
    signal = signal_for_strategy(
        strategy,
        current,
        previous,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        cumulative_event_ofi=cumulative_event_ofi,
    )
    if signal is None:
        return None
    candidate = execution_candidate(
        strategy,
        signal,
        current,
        snapshot,
        stake=CURRENT_STRATEGY_STAKES.get(strategy, STAKE),
        minimum_stake=MINIMUM_STAKE,
        slippage_bps=SLIPPAGE_BPS,
        max_spread=MAX_SPREAD,
        min_entry=MIN_ENTRY,
        max_book_age_ms=MAX_BOOK_AGE_MS,
        max_book_skew_ms=MAX_BOOK_SKEW_MS,
    )
    if candidate is None:
        return None
    if max_entry is not None and float(candidate["entry"]) > max_entry + 1e-12:
        return None
    return candidate


def _make_trade(
    strategy: str,
    version: str,
    split: str,
    winner: str,
    row: sqlite3.Row,
    candidate: dict[str, Any],
    decision: dict[str, Any] | None,
    drawdown_decision: dict[str, Any] | None = None,
    *,
    drawdown_control_applied: bool = False,
) -> dict[str, Any]:
    side = str(candidate["side"])
    entry = float(candidate["entry"])
    shares = float(candidate["filled_shares"])
    fee = taker_fee(shares, entry, FEE_BPS)
    pnl = (shares if side == winner else 0.0) - float(candidate["filled_stake"]) - fee
    return {
        "strategy": strategy,
        "observer_version": version,
        "split": split,
        "market_id": int(row["market_id"]),
        "observation_id": int(row["id"]),
        "timestamp": str(row["timestamp"]),
        "side": side,
        "winner": winner,
        "seconds_left": float(row["seconds_left"]),
        "entry_price": entry,
        "raw_ask": float(candidate["raw_ask"]),
        "visible_ask_size": float(candidate["visible_size"]),
        "stake": float(candidate["filled_stake"]),
        "shares": shares,
        "fee": fee,
        "pnl": pnl,
        "signal": float(candidate["signal"]),
        "book_age_ms": float(candidate["book_age_ms"]),
        "book_skew_ms": float(candidate["book_skew_ms"]),
        "observer_range_score": (
            decision.get("currentRangeScore") if decision is not None else None
        ),
        "observer_effective_crossovers": (
            decision.get("currentEffectiveCrossovers") if decision is not None else None
        ),
        "observer_trend_veto": (
            decision.get("currentTrendVeto") if decision is not None else None
        ),
        "drawdown_control_applied": drawdown_control_applied,
        "drawdown_control_status": (
            drawdown_decision.get("status")
            if drawdown_decision is not None
            else None
        ),
        "drawdown_prior_market_count": (
            drawdown_decision.get("prior_market_count")
            if drawdown_decision is not None
            else None
        ),
        "drawdown_prior_net_return_bps": (
            drawdown_decision.get("prior_net_return_bps")
            if drawdown_decision is not None
            else None
        ),
        "drawdown_side_alignment_bps": (
            drawdown_decision.get("side_alignment_bps")
            if drawdown_decision is not None
            else None
        ),
    }


def _summarize(trades: list[dict[str, Any]], candidate_markets: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: int(trade["observation_id"]))
    wins = sum(float(trade["pnl"]) > 0 for trade in ordered)
    realized = sum(float(trade["pnl"]) for trade in ordered)
    filled_cost = sum(float(trade["stake"]) + float(trade["fee"]) for trade in ordered)
    gross_profit = sum(max(0.0, float(trade["pnl"])) for trade in ordered)
    gross_loss = -sum(min(0.0, float(trade["pnl"])) for trade in ordered)
    equity = peak = max_drawdown = 0.0
    for trade in ordered:
        equity += float(trade["pnl"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "candidateMarkets": candidate_markets,
        "trades": len(ordered),
        "wins": wins,
        "losses": len(ordered) - wins,
        "coverageRate": len(ordered) / candidate_markets if candidate_markets else None,
        "winRate": wins / len(ordered) if ordered else None,
        "realizedPnl": realized,
        "filledCost": filled_cost,
        "roi": realized / filled_cost if filled_cost else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_drawdown,
        "averageEntryPrice": (
            sum(float(trade["entry_price"]) for trade in ordered) / len(ordered)
            if ordered
            else None
        ),
        "upTrades": sum(trade["side"] == "UP" for trade in ordered),
        "downTrades": sum(trade["side"] == "DOWN" for trade in ordered),
    }


def run(
    db_path: Path,
    *,
    micro_db_path: Path = Path("data/microstructure.db"),
    max_entry: float | None = None,
    requested_end_at: datetime | None = None,
) -> dict[str, Any]:
    if max_entry is not None and not 0.0 < max_entry < 1.0:
        raise ValueError("max_entry must be between 0 and 1")
    if requested_end_at is not None and requested_end_at.tzinfo is None:
        requested_end_at = requested_end_at.replace(tzinfo=timezone.utc)

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    micro_replay: _MicroFuturesReplay | None = None
    try:
        metadata = _market_metadata(db)
        latest_end_at = max(row["last_timestamp"] for row in metadata)
        end_at = min(requested_end_at, latest_end_at) if requested_end_at else latest_end_at
        start_at = end_at - timedelta(days=WINDOW_DAYS)
        candidate_metadata = [
            row for row in metadata if start_at <= row["start_estimate"] <= end_at
        ]
        candidate_ids = [row["market_id"] for row in candidate_metadata]
        candidate_set = set(candidate_ids)
        first_candidate_index = metadata.index(candidate_metadata[0])
        replay_metadata = metadata[
            max(0, first_candidate_index - WARMUP_MARKETS) : metadata.index(candidate_metadata[-1]) + 1
        ]
        replay_set = {row["market_id"] for row in replay_metadata}
        winner_by_market = {row["market_id"]: row["winner"] for row in replay_metadata}
        metadata_by_market = {row["market_id"]: row for row in replay_metadata}
        split_by_market = _split_labels(candidate_ids)

        micro_replay = _MicroFuturesReplay(
            micro_db_path,
            start_ns=_timestamp_ns(replay_metadata[0]["first_timestamp"].isoformat())
            - MICRO_MAX_AGE_NS,
            end_ns=_timestamp_ns(replay_metadata[-1]["last_timestamp"].isoformat()),
        )

        observer = ReplayMarketStateObserver(
            window_size=WARMUP_MARKETS,
            touch_threshold=0.30,
            fee_bps=FEE_BPS,
        )
        drawdown_controller = MarketRegimeDrawdownController(
            lookback_markets=DRAW_DOWN_LOOKBACK_MARKETS,
            min_abs_prior_net_return_bps=DRAW_DOWN_MIN_ABS_PRIOR_NET_BPS,
            max_opposed_move_bps=DRAW_DOWN_MAX_OPPOSED_MOVE_BPS,
        )
        buffer = ResearchSampleBuffer()
        opened = {strategy: set() for strategy in REPLAYABLE_STRATEGIES}
        gate_counts: dict[str, Counter[str]] = {
            version: Counter() for version in FUTURES_LEAD_OBSERVER_VERSIONS
        }
        drawdown_counts: dict[str, Counter[str]] = {
            strategy: Counter() for strategy in REPLAYABLE_STRATEGIES
        }
        trades: list[dict[str, Any]] = []
        active_market: int | None = None
        active_winner: str | None = None

        def settle_active() -> None:
            if active_market is not None and active_winner in {"UP", "DOWN"}:
                observer.settle_replay_round(active_market, active_winner, None)
                metadata_row = metadata_by_market[active_market]
                start_price = metadata_row.get("official_start_price")
                end_price = metadata_row.get("official_end_price")
                if start_price is not None and end_price is not None:
                    drawdown_controller.record_completed_market(
                        active_market,
                        start_price=float(start_price),
                        end_price=float(end_price),
                    )

        placeholders = ",".join("?" for _ in replay_set)
        rows = db.execute(
            f"""SELECT * FROM observations
                  WHERE market_id IN ({placeholders})
                  ORDER BY id""",
            tuple(replay_set),
        )
        for row in rows:
            market_id = int(row["market_id"])
            timestamp = _dt(str(row["timestamp"]))
            now_ts = timestamp.timestamp()
            if market_id != active_market:
                settle_active()
                active_market = market_id
                active_winner = winner_by_market[market_id]
                market_start_ts = now_ts - (300.0 - float(row["seconds_left"]))
                observer.reset_market(
                    market_id,
                    float(row["start_price"]),
                    market_start_ts,
                )

            observer.update_tick(
                now_ts,
                float(row["spot_price"]) if row["spot_price"] is not None else None,
                float(row["up_ask"]) if row["up_ask"] is not None else None,
                float(row["down_ask"]) if row["down_ask"] is not None else None,
                market_id=market_id,
            )
            snapshot = _snapshot(row)
            micro = micro_replay.match(market_id, _timestamp_ns(str(row["timestamp"])))
            if micro is not None:
                snapshot["spot_price"] = micro["spot_price"]
                snapshot["futures_price"] = micro["futures_price"]
                snapshot["spot_age_ms"] = micro["age_ms"]
                snapshot["futures_age_ms"] = micro["age_ms"]
            current = buffer.append(market_id, snapshot)
            if current is None:
                continue
            buffer.append_event_ofi(market_id, current, _event_key(row))
            if market_id not in candidate_set:
                continue

            for strategy in REPLAYABLE_STRATEGIES:
                if market_id in opened[strategy]:
                    continue
                candidate = _source_candidate(
                    strategy,
                    market_id,
                    current,
                    snapshot,
                    buffer,
                    max_entry=max_entry,
                )
                if candidate is None:
                    continue
                opened[strategy].add(market_id)
                split = split_by_market[market_id]
                winner = winner_by_market[market_id]
                drawdown_decision = drawdown_controller.evaluate(
                    side=str(candidate["side"]),
                    start_price=float(current["start_price"]),
                    spot_price=float(current["spot_price"]),
                ).as_dict()
                drawdown_counts[strategy][str(drawdown_decision["status"])] += 1
                trades.append(
                    _make_trade(
                        strategy,
                        "BASE",
                        split,
                        winner,
                        row,
                        candidate,
                        None,
                        drawdown_decision,
                    )
                )
                if drawdown_decision["allowed"] is True:
                    trades.append(
                        _make_trade(
                            strategy,
                            f"BASE{DRAW_DOWN_SUFFIX}",
                            split,
                            winner,
                            row,
                            candidate,
                            None,
                            drawdown_decision,
                            drawdown_control_applied=True,
                        )
                    )
                gate = observer.m01o_entry_gate(
                    min_settled_samples=6,
                    min_current_range_score=1,
                    profile="F1",
                    now_ts=now_ts,
                )
                for version in FUTURES_LEAD_OBSERVER_VERSIONS:
                    decision = futures_lead_observer_decision(
                        version,
                        gate,
                        expected_market_id=market_id,
                    )
                    gate_counts[version][
                        "ALLOW" if decision["allowed"] is True else "BLOCK"
                    ] += 1
                    if decision["allowed"] is True:
                        trades.append(
                            _make_trade(
                                strategy,
                                version,
                                split,
                                winner,
                                row,
                                candidate,
                                decision,
                                drawdown_decision,
                            )
                        )
                        if drawdown_decision["allowed"] is True:
                            trades.append(
                                _make_trade(
                                    strategy,
                                    f"{version}{DRAW_DOWN_SUFFIX}",
                                    split,
                                    winner,
                                    row,
                                    candidate,
                                    decision,
                                    drawdown_decision,
                                    drawdown_control_applied=True,
                                )
                            )
        settle_active()

        summaries: dict[str, Any] = {}
        report_versions = (*VERSIONS, *(f"{version}{DRAW_DOWN_SUFFIX}" for version in VERSIONS))
        for strategy in REPLAYABLE_STRATEGIES:
            summaries[strategy] = {}
            for version in report_versions:
                selected = [
                    trade
                    for trade in trades
                    if trade["strategy"] == strategy
                    and trade["observer_version"] == version
                ]
                summaries[strategy][version] = {
                    "overall": _summarize(selected, len(candidate_ids)),
                    "splits": {
                        split: _summarize(
                            [trade for trade in selected if trade["split"] == split],
                            sum(value == split for value in split_by_market.values()),
                        )
                        for split in ("development", "validation", "holdout")
                    },
                }

        ranking = []
        for strategy in REPLAYABLE_STRATEGIES:
            base = summaries[strategy]["BASE"]["overall"]
            for version in report_versions:
                result = summaries[strategy][version]["overall"]
                ranking.append(
                    {
                        "strategy": strategy,
                        "observerVersion": version,
                        **result,
                        "retainedVsBase": (
                            result["trades"] / base["trades"] if base["trades"] else None
                        ),
                        "pnlDeltaVsBase": result["realizedPnl"] - base["realizedPnl"],
                        "drawdownReductionVsBase": (
                            base["maxDrawdown"] - result["maxDrawdown"]
                        ),
                    }
                )
        ranking.sort(
            key=lambda item: (
                float(item["realizedPnl"]),
                -float(item["maxDrawdown"]),
                int(item["trades"]),
            ),
            reverse=True,
        )

        taipei = ZoneInfo("Asia/Taipei")
        daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for strategy in REPLAYABLE_STRATEGIES:
            for version in report_versions:
                selected = [
                    trade
                    for trade in trades
                    if trade["strategy"] == strategy
                    and trade["observer_version"] == version
                ]
                by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for trade in selected:
                    day = _dt(str(trade["timestamp"])).astimezone(taipei).date().isoformat()
                    by_day[day].append(trade)
                daily[strategy][version] = {
                    day: _summarize(day_trades, 0) for day, day_trades in sorted(by_day.items())
                }

        combination_before = [
            trade
            for trade in trades
            if CURRENT_COMBINATION.get(str(trade["strategy"]))
            == str(trade["observer_version"])
        ]
        combination_after = [
            trade
            for trade in trades
            if (
                f"{CURRENT_COMBINATION.get(str(trade['strategy']))}{DRAW_DOWN_SUFFIX}"
                == str(trade["observer_version"])
            )
        ]
        after_keys = {
            (
                str(trade["strategy"]),
                int(trade["market_id"]),
                int(trade["observation_id"]),
            )
            for trade in combination_after
        }
        combination_blocked = [
            trade
            for trade in combination_before
            if (
                str(trade["strategy"]),
                int(trade["market_id"]),
                int(trade["observation_id"]),
            )
            not in after_keys
        ]
        combination_before_summary = _summarize(
            combination_before, len(candidate_ids)
        )
        combination_after_summary = _summarize(
            combination_after, len(candidate_ids)
        )
        combination_blocked_summary = _summarize(
            combination_blocked, len(candidate_ids)
        )
        combination_splits = {}
        for split in ("development", "validation", "holdout"):
            split_markets = sum(
                value == split for value in split_by_market.values()
            )
            before_split = [
                trade for trade in combination_before if trade["split"] == split
            ]
            after_split = [
                trade for trade in combination_after if trade["split"] == split
            ]
            blocked_split = [
                trade for trade in combination_blocked if trade["split"] == split
            ]
            combination_splits[split] = {
                "before": _summarize(before_split, split_markets),
                "after": _summarize(after_split, split_markets),
                "blockedCounterfactual": _summarize(
                    blocked_split, split_markets
                ),
            }

        combination_daily: dict[str, dict[str, Any]] = {}
        for label, selected in (
            ("before", combination_before),
            ("after", combination_after),
            ("blockedCounterfactual", combination_blocked),
        ):
            by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for trade in selected:
                day = (
                    _dt(str(trade["timestamp"]))
                    .astimezone(taipei)
                    .date()
                    .isoformat()
                )
                by_day[day].append(trade)
            combination_daily[label] = {
                day: _summarize(day_trades, 0)
                for day, day_trades in sorted(by_day.items())
            }

        quality_complete = sum(
            row["max_seconds_left"] >= 290.0
            and row["min_seconds_left"] <= 10.0
            and row["observation_count"] >= 120
            for row in candidate_metadata
        )
        source_field_counts = db.execute(
            f"""SELECT COUNT(*) AS rows,
                       SUM(futures_price IS NOT NULL) AS futures_price_rows,
                       SUM(futures_age_ms IS NOT NULL) AS futures_age_rows,
                       SUM(spot_age_ms IS NOT NULL) AS spot_age_rows,
                       SUM(up_book_timestamp_ms IS NOT NULL) AS prediction_timestamp_rows
                  FROM observations
                 WHERE market_id IN ({','.join('?' for _ in candidate_set)})""",
            tuple(candidate_set),
        ).fetchone()
        return {
            "schemaVersion": 2,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sourceModel": "seven_day_recorded_market_path_causal_observer_replay",
            "sourceDatabase": str(db_path.resolve()),
            "sourceMicrostructureDatabase": str(micro_db_path.resolve()),
            "window": {
                "days": WINDOW_DAYS,
                "startUtc": start_at.isoformat(),
                "endUtc": end_at.isoformat(),
                "startTaipei": start_at.astimezone(taipei).isoformat(),
                "endTaipei": end_at.astimezone(taipei).isoformat(),
            },
            "dataCoverage": {
                "candidateMarkets": len(candidate_ids),
                "qualityCompleteMarkets": quality_complete,
                "warmupMarkets": min(WARMUP_MARKETS, first_candidate_index),
                "observationRows": int(source_field_counts["rows"] or 0),
                "futuresPriceRows": int(source_field_counts["futures_price_rows"] or 0),
                "futuresAgeRows": int(source_field_counts["futures_age_rows"] or 0),
                "spotAgeRows": int(source_field_counts["spot_age_rows"] or 0),
                "predictionTimestampRows": int(
                    source_field_counts["prediction_timestamp_rows"] or 0
                ),
                "microFuturesRowsRead": micro_replay.rows_read,
                "microFuturesMatches": micro_replay.matches,
            },
            "method": {
                "usesRecordedTradesOrOrders": False,
                "causalObserverSettlement": True,
                "observerWindowMarkets": WARMUP_MARKETS,
                "observerMinSettledSamples": 6,
                "splitPolicy": "candidate markets chronological 60/20/20",
                "execution": {
                    "defaultStakeUsdt": STAKE,
                    "currentStrategyStakesUsdt": CURRENT_STRATEGY_STAKES,
                    "feeBps": FEE_BPS,
                    "slippageBps": SLIPPAGE_BPS,
                    "maxEntryPrice": max_entry,
                    "maxEntryAppliesTo": "simulated_entry_after_slippage",
                    "fullVisibleAskDepthRequired": True,
                    "maxSpread": MAX_SPREAD,
                    "maxBookAgeMs": MAX_BOOK_AGE_MS,
                    "maxBookSkewMs": MAX_BOOK_SKEW_MS,
                    "sharedCapitalCapApplied": False,
                },
                "fidelityNote": (
                    "Only saved ticks can be replayed. Observer history is warmed with the prior "
                    "20 official markets and winners are revealed only after each market's final tick. "
                    "Futures Lead is replayed only where a <=1.5 second causal microstructure "
                    "spot/futures match exists; missing coverage produces no Futures Lead signal."
                ),
            },
            "unavailableStrategies": UNAVAILABLE_STRATEGIES,
            "observerGateCounts": {
                version: dict(counts) for version, counts in gate_counts.items()
            },
            "drawdownController": {
                "enabledInLive": False,
                "comparisonSuffix": DRAW_DOWN_SUFFIX,
                "rule": {
                    "lookbackCompletedMarkets": DRAW_DOWN_LOOKBACK_MARKETS,
                    "minAbsPriorNetReturnBps": DRAW_DOWN_MIN_ABS_PRIOR_NET_BPS,
                    "maxOpposedMoveBps": DRAW_DOWN_MAX_OPPOSED_MOVE_BPS,
                    "blockWhen": (
                        "sideAlignmentBps < -maxOpposedMoveBps AND "
                        "abs(priorNetReturnBps) >= minAbsPriorNetReturnBps"
                    ),
                    "historyPolicy": "fail closed until completed-market history is ready",
                    "causal": True,
                },
                "candidateDecisionCounts": {
                    strategy: dict(counts)
                    for strategy, counts in drawdown_counts.items()
                },
            },
            "currentCombination": {
                "strategies": CURRENT_COMBINATION,
                "stakesUsdt": CURRENT_STRATEGY_STAKES,
                "before": combination_before_summary,
                "after": combination_after_summary,
                "blockedCounterfactual": combination_blocked_summary,
                "pnlDelta": (
                    combination_after_summary["realizedPnl"]
                    - combination_before_summary["realizedPnl"]
                ),
                "drawdownReduction": (
                    combination_before_summary["maxDrawdown"]
                    - combination_after_summary["maxDrawdown"]
                ),
                "splits": combination_splits,
                "dailyTaipei": combination_daily,
            },
            "ranking": ranking,
            "summaries": summaries,
            "daily": daily,
            "trades": trades,
        }
    finally:
        if micro_replay is not None:
            micro_replay.close()
        db.close()


def write_outputs(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    max_entry = report["method"]["execution"].get("maxEntryPrice")
    variant = ""
    if max_entry is not None:
        variant = f"-max-entry-{float(max_entry):.3f}".replace(".", "p")
    base_name = f"seven-day-observer-market-replay{variant}-{stamp}"
    json_path = output_dir / f"{base_name}.json"
    ranking_path = output_dir / f"{base_name}-ranking.csv"
    trades_path = output_dir / f"{base_name}-trades.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with ranking_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report["ranking"][0].keys()))
        writer.writeheader()
        writer.writerows(report["ranking"])
    with trades_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report["trades"][0].keys()))
        writer.writeheader()
        writer.writerows(report["trades"])
    return json_path, ranking_path, trades_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay seven days of recorded market paths with frozen Observer versions."
    )
    parser.add_argument("--db", type=Path, default=Path("data/simulation.db"))
    parser.add_argument(
        "--micro-db",
        type=Path,
        default=Path("data/microstructure.db"),
        help="Read-only microstructure database used for causal Futures Lead replay.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtests"))
    parser.add_argument(
        "--max-entry",
        type=float,
        default=None,
        help="Maximum simulated entry price after slippage (for example 0.10).",
    )
    parser.add_argument(
        "--end-at",
        type=_dt,
        default=None,
        help="Optional fixed replay-window end timestamp (ISO-8601).",
    )
    args = parser.parse_args()
    report = run(
        args.db,
        micro_db_path=args.micro_db,
        max_entry=args.max_entry,
        requested_end_at=args.end_at,
    )
    paths = write_outputs(report, args.output_dir)
    print(json.dumps({"outputs": [str(path) for path in paths], "top": report["ranking"][:10]}, indent=2))


if __name__ == "__main__":
    main()
