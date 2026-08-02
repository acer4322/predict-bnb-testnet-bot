from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from predict_bot.backtest import ReplayMarketStateObserver
from predict_bot.research_forward import (
    FUTURES_LEAD_OBSERVER_VERSIONS,
    futures_lead_observer_decision,
)


LEGACY_STRATEGIES = tuple("ABCDEFGHIJKL")
VERSIONS = ("BASE", *FUTURES_LEAD_OBSERVER_VERSIONS)
SPLITS = ("development", "validation", "holdout")
OBSERVER_WINDOW_MARKETS = 20
OBSERVER_MIN_SETTLED_SAMPLES = 6
FEE_BPS = 200
MIN_RETAINED_TRADES = 100
MIN_RETENTION_RATE = 0.20
MIN_POSITIVE_FOLDS = 4
FOLD_COUNT = 5


@dataclass(frozen=True)
class LegacyTrade:
    id: int
    strategy: str
    market_id: int
    side: str
    status: str
    entry_price: float
    stake: float
    fees: float
    pnl: float
    opened_at: str
    signal_at: str
    signal_time_source: str


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _connect_read_only(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=60
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    return db


def _signal_timestamp(opened_at: str, diagnostics_json: str | None) -> tuple[str, str]:
    if diagnostics_json:
        try:
            diagnostics = json.loads(diagnostics_json)
        except (json.JSONDecodeError, TypeError):
            diagnostics = None
        if isinstance(diagnostics, dict):
            value = diagnostics.get("signal_timestamp")
            if isinstance(value, str):
                try:
                    _dt(value)
                except ValueError:
                    pass
                else:
                    return value, "diagnostics.signal_timestamp"
    return opened_at, "opened_at"


def _load_trades(db: sqlite3.Connection) -> list[LegacyTrade]:
    placeholders = ",".join("?" for _ in LEGACY_STRATEGIES)
    rows = db.execute(
        f"""SELECT id, strategy, market_id, side, status, entry_price,
                   stake, fees, pnl, opened_at, diagnostics_json
              FROM trades
             WHERE strategy IN ({placeholders})
               AND status<>'OPEN' AND pnl IS NOT NULL
             ORDER BY opened_at, id""",
        LEGACY_STRATEGIES,
    ).fetchall()
    result: list[LegacyTrade] = []
    for row in rows:
        signal_at, source = _signal_timestamp(
            str(row["opened_at"]), row["diagnostics_json"]
        )
        result.append(
            LegacyTrade(
                id=int(row["id"]),
                strategy=str(row["strategy"]),
                market_id=int(row["market_id"]),
                side=str(row["side"]),
                status=str(row["status"]),
                entry_price=float(row["entry_price"]),
                stake=float(row["stake"]),
                fees=float(row["fees"] or 0.0),
                pnl=float(row["pnl"]),
                opened_at=str(row["opened_at"]),
                signal_at=signal_at,
                signal_time_source=source,
            )
        )
    return result


def _assign_segments(trades: list[LegacyTrade]) -> tuple[dict[int, str], dict[int, int]]:
    ordered = sorted(trades, key=lambda trade: (_dt(trade.signal_at), trade.id))
    total = len(ordered)
    development_end = math.ceil(total * 0.60)
    validation_end = math.ceil(total * 0.80)
    split_by_id: dict[int, str] = {}
    fold_by_id: dict[int, int] = {}
    for index, trade in enumerate(ordered):
        split_by_id[trade.id] = (
            "development"
            if index < development_end
            else "validation" if index < validation_end else "holdout"
        )
        fold_by_id[trade.id] = min(FOLD_COUNT, index * FOLD_COUNT // max(1, total) + 1)
    return split_by_id, fold_by_id


def _summarize(
    trades: Iterable[LegacyTrade],
    *,
    baseline_count: int,
    fold_by_id: dict[int, int] | None = None,
) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: (_dt(trade.signal_at), trade.id))
    pnls = [trade.pnl for trade in ordered]
    wins = sum(pnl > 0 for pnl in pnls)
    gross_profit = sum(max(0.0, pnl) for pnl in pnls)
    gross_loss = -sum(min(0.0, pnl) for pnl in pnls)
    filled_cost = sum(trade.stake + trade.fees for trade in ordered)
    equity = peak = max_drawdown = 0.0
    losing_streak = max_losing_streak = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if pnl <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    taipei = ZoneInfo("Asia/Taipei")
    pnl_by_day: dict[str, float] = defaultdict(float)
    for trade in ordered:
        day = _dt(trade.signal_at).astimezone(taipei).date().isoformat()
        pnl_by_day[day] += trade.pnl
    daily_values = list(pnl_by_day.values())

    fold_pnls: dict[str, float] = {}
    if fold_by_id is not None:
        fold_pnls = {str(fold): 0.0 for fold in range(1, FOLD_COUNT + 1)}
        for trade in ordered:
            fold_pnls[str(fold_by_id[trade.id])] += trade.pnl

    return {
        "trades": len(ordered),
        "baselineTrades": baseline_count,
        "retentionRate": len(ordered) / baseline_count if baseline_count else None,
        "wins": wins,
        "losses": len(ordered) - wins,
        "winRate": wins / len(ordered) if ordered else None,
        "realizedPnl": sum(pnls),
        "filledCost": filled_cost,
        "roi": sum(pnls) / filled_cost if filled_cost else None,
        "averagePnl": statistics.fmean(pnls) if pnls else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_drawdown,
        "maxConsecutiveLosses": max_losing_streak,
        "activeDays": len(daily_values),
        "positiveDays": sum(value > 0 for value in daily_values),
        "negativeDays": sum(value < 0 for value in daily_values),
        "positiveActiveDayRate": (
            sum(value > 0 for value in daily_values) / len(daily_values)
            if daily_values
            else None
        ),
        "worstDayPnl": min(daily_values) if daily_values else None,
        "dailyPnlStdDev": (
            statistics.pstdev(daily_values) if len(daily_values) >= 2 else None
        ),
        "foldPnl": fold_pnls,
        "positiveFolds": sum(value > 0 for value in fold_pnls.values()),
        "worstFoldPnl": min(fold_pnls.values()) if fold_pnls else None,
    }


def _stability_assessment(
    overall: dict[str, Any],
    splits: dict[str, dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "minimumTrades": overall["trades"] >= MIN_RETAINED_TRADES,
        "minimumRetention": (
            overall["retentionRate"] is not None
            and overall["retentionRate"] >= MIN_RETENTION_RATE
        ),
        "positiveOverallPnl": overall["realizedPnl"] > 0,
        "positiveValidationPnl": splits["validation"]["realizedPnl"] > 0,
        "positiveHoldoutPnl": splits["holdout"]["realizedPnl"] > 0,
        "positiveTemporalFolds": overall["positiveFolds"] >= MIN_POSITIVE_FOLDS,
        "drawdownBelowBase": overall["maxDrawdown"] < baseline["maxDrawdown"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failedChecks": [name for name, passed in checks.items() if not passed],
    }


def _decision_rank(item: dict[str, Any]) -> tuple[Any, ...]:
    overall = item["overall"]
    splits = item["splits"]
    return (
        item["stability"]["passed"],
        sum(splits[name]["realizedPnl"] > 0 for name in SPLITS),
        overall["positiveFolds"],
        min(splits[name]["realizedPnl"] for name in SPLITS),
        overall["realizedPnl"],
        -overall["maxDrawdown"],
        overall["trades"],
    )


def _evaluate_trade(
    trade: LegacyTrade,
    observer: ReplayMarketStateObserver,
    *,
    last_observation: sqlite3.Row | None,
) -> dict[str, Any]:
    signal_dt = _dt(trade.signal_at)
    gate = observer.m01o_entry_gate(
        min_settled_samples=OBSERVER_MIN_SETTLED_SAMPLES,
        min_current_range_score=1,
        profile="F1",
        now_ts=signal_dt.timestamp(),
    )
    decisions = {
        version: futures_lead_observer_decision(
            version, gate, expected_market_id=trade.market_id
        )
        for version in FUTURES_LEAD_OBSERVER_VERSIONS
    }
    return {
        **asdict(trade),
        "asofObservationId": (
            int(last_observation["id"]) if last_observation is not None else None
        ),
        "asofObservationTimestamp": (
            str(last_observation["timestamp"])
            if last_observation is not None
            else None
        ),
        "observerHistoricalSamples": gate.get("historicalSampleCount"),
        "observerHistoricalState": gate.get("historicalState"),
        "observerDataQuality": gate.get("dataQualityStatus"),
        "observerRangeScore": gate.get("currentRangeScore"),
        "observerEffectiveCrossovers": gate.get("currentEffectiveCrossovers"),
        "observerBothSidesTouched": gate.get("currentBothSidesTouched"),
        "observerTrendVeto": gate.get("currentTrendVeto"),
        "observerPhase": gate.get("currentPhase"),
        "observerShortEr": gate.get("currentShortEr"),
        "observerMedianEr60s": gate.get("currentMedianEr60s"),
        **{
            f"{version}_allowed": decisions[version]["allowed"] is True
            for version in FUTURES_LEAD_OBSERVER_VERSIONS
        },
        **{
            f"{version}_reason": str(decisions[version].get("reason") or "")
            for version in FUTURES_LEAD_OBSERVER_VERSIONS
        },
    }


def _replay_observer(
    db: sqlite3.Connection, trades: list[LegacyTrade]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades_by_market: dict[int, list[LegacyTrade]] = defaultdict(list)
    for trade in trades:
        trades_by_market[trade.market_id].append(trade)
    for values in trades_by_market.values():
        values.sort(key=lambda trade: (_dt(trade.signal_at), trade.id))

    official_winners = {
        int(row["market_id"]): str(row["official_winner"])
        for row in db.execute(
            """SELECT market_id, official_winner FROM market_settlements
                WHERE status='OFFICIAL' AND official_winner IN ('UP','DOWN')"""
        )
    }
    observer = ReplayMarketStateObserver(
        window_size=OBSERVER_WINDOW_MARKETS,
        touch_threshold=0.30,
        fee_bps=FEE_BPS,
    )
    evaluations: list[dict[str, Any]] = []
    active_market: int | None = None
    active_trades: list[LegacyTrade] = []
    trade_index = 0
    last_observation: sqlite3.Row | None = None

    def evaluate_before(timestamp: datetime, *, inclusive: bool) -> None:
        nonlocal trade_index
        while trade_index < len(active_trades):
            trade = active_trades[trade_index]
            signal_time = _dt(trade.signal_at)
            if signal_time > timestamp or (not inclusive and signal_time == timestamp):
                break
            evaluations.append(
                _evaluate_trade(
                    trade, observer, last_observation=last_observation
                )
            )
            trade_index += 1

    def finish_active() -> None:
        nonlocal trade_index
        while trade_index < len(active_trades):
            evaluations.append(
                _evaluate_trade(
                    active_trades[trade_index],
                    observer,
                    last_observation=last_observation,
                )
            )
            trade_index += 1
        if active_market is not None and active_market in official_winners:
            observer.settle_replay_round(
                active_market, official_winners[active_market], None
            )

    rows = db.execute("SELECT * FROM observations ORDER BY id")
    observation_rows = 0
    for row in rows:
        observation_rows += 1
        market_id = int(row["market_id"])
        row_time = _dt(str(row["timestamp"]))
        if market_id != active_market:
            finish_active()
            active_market = market_id
            active_trades = trades_by_market.get(market_id, [])
            trade_index = 0
            last_observation = None
            market_start_ts = row_time.timestamp() - (
                300.0 - float(row["seconds_left"])
            )
            observer.reset_market(
                market_id, float(row["start_price"]), market_start_ts
            )

        # A trade between two saved ticks sees only the earlier tick. Exact-timestamp
        # trades see the matching tick, so the merge remains strictly as-of.
        evaluate_before(row_time, inclusive=False)
        observer.update_tick(
            row_time.timestamp(),
            float(row["spot_price"]) if row["spot_price"] is not None else None,
            float(row["up_ask"]) if row["up_ask"] is not None else None,
            float(row["down_ask"]) if row["down_ask"] is not None else None,
            market_id=market_id,
        )
        last_observation = row
        evaluate_before(row_time, inclusive=True)
    finish_active()

    evaluated_ids = {int(item["id"]) for item in evaluations}
    missing = [trade for trade in trades if trade.id not in evaluated_ids]
    for trade in missing:
        evaluations.append(
            {
                **asdict(trade),
                "asofObservationId": None,
                "asofObservationTimestamp": None,
                "observerHistoricalSamples": None,
                "observerHistoricalState": None,
                "observerDataQuality": "UNAVAILABLE",
                "observerRangeScore": None,
                "observerEffectiveCrossovers": None,
                "observerBothSidesTouched": None,
                "observerTrendVeto": None,
                "observerPhase": None,
                "observerShortEr": None,
                "observerMedianEr60s": None,
                **{
                    f"{version}_allowed": False
                    for version in FUTURES_LEAD_OBSERVER_VERSIONS
                },
                **{
                    f"{version}_reason": "market observations unavailable"
                    for version in FUTURES_LEAD_OBSERVER_VERSIONS
                },
            }
        )
    evaluations.sort(key=lambda item: (_dt(str(item["signal_at"])), int(item["id"])))
    return evaluations, {
        "observationRows": observation_rows,
        "officialSettlements": len(official_winners),
        "evaluatedTrades": len(evaluated_ids),
        "unmatchedTrades": len(missing),
    }


def _scope_report(
    name: str,
    trades: list[LegacyTrade],
    evaluations_by_id: dict[int, dict[str, Any]],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    scoped = [
        trade
        for trade in trades
        if (start is None or _dt(trade.signal_at) >= start)
        and (end is None or _dt(trade.signal_at) <= end)
    ]
    summaries: dict[str, dict[str, Any]] = {}
    ranking: list[dict[str, Any]] = []
    for strategy in LEGACY_STRATEGIES:
        baseline_trades = [trade for trade in scoped if trade.strategy == strategy]
        split_by_id, fold_by_id = _assign_segments(baseline_trades)
        baseline_overall = _summarize(
            baseline_trades,
            baseline_count=len(baseline_trades),
            fold_by_id=fold_by_id,
        )
        summaries[strategy] = {}
        for version in VERSIONS:
            selected = (
                baseline_trades
                if version == "BASE"
                else [
                    trade
                    for trade in baseline_trades
                    if evaluations_by_id[trade.id][f"{version}_allowed"] is True
                ]
            )
            overall = _summarize(
                selected,
                baseline_count=len(baseline_trades),
                fold_by_id=fold_by_id,
            )
            split_summaries = {
                split: _summarize(
                    [trade for trade in selected if split_by_id[trade.id] == split],
                    baseline_count=sum(
                        value == split for value in split_by_id.values()
                    ),
                )
                for split in SPLITS
            }
            stability = _stability_assessment(
                overall, split_summaries, baseline_overall
            )
            item = {
                "strategy": strategy,
                "observerVersion": version,
                "overall": overall,
                "splits": split_summaries,
                "stability": stability,
                "pnlDeltaVsBase": overall["realizedPnl"]
                - baseline_overall["realizedPnl"],
                "drawdownReductionVsBase": baseline_overall["maxDrawdown"]
                - overall["maxDrawdown"],
            }
            summaries[strategy][version] = item
            ranking.append(item)
        summaries[strategy]["best"] = max(
            (summaries[strategy][version] for version in VERSIONS),
            key=_decision_rank,
        )["observerVersion"]
    ranking.sort(key=_decision_rank, reverse=True)
    return {
        "name": name,
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "baselineTrades": len(scoped),
        "splitPolicy": "per-strategy chronological trades 60/20/20",
        "foldPolicy": f"per-strategy chronological {FOLD_COUNT} equal-count folds",
        "summaries": summaries,
        "ranking": ranking,
        "stabilityPasses": [
            {
                "strategy": item["strategy"],
                "observerVersion": item["observerVersion"],
            }
            for item in ranking
            if item["stability"]["passed"]
        ],
    }


def run(db_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = _connect_read_only(db_path)
    try:
        # Pin observations, settlements, and trades to one WAL snapshot even while
        # the paper collector continues appending to the live database.
        db.execute("BEGIN")
        observation = db.execute(
            """SELECT MIN(timestamp) AS first_timestamp,
                      MAX(timestamp) AS last_timestamp,
                      COUNT(*) AS rows, COUNT(DISTINCT market_id) AS markets
                 FROM observations"""
        ).fetchone()
        trades = _load_trades(db)
        evaluations, coverage = _replay_observer(db, trades)
        evaluations_by_id = {int(item["id"]): item for item in evaluations}

        by_strategy = {
            strategy: [trade for trade in trades if trade.strategy == strategy]
            for strategy in LEGACY_STRATEGIES
        }
        common_start = max(
            min(_dt(trade.signal_at) for trade in values)
            for values in by_strategy.values()
        )
        common_end = min(
            max(_dt(trade.signal_at) for trade in values)
            for values in by_strategy.values()
        )
        available = _scope_report(
            "fullAvailable", trades, evaluations_by_id
        )
        common = _scope_report(
            "commonWindow",
            trades,
            evaluations_by_id,
            start=common_start,
            end=common_end,
        )
        report = {
            "schemaVersion": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sourceDatabase": str(db_path.resolve()),
            "dataSnapshot": {
                "firstObservation": observation["first_timestamp"],
                "lastObservation": observation["last_timestamp"],
                "observationRows": int(observation["rows"]),
                "observedMarkets": int(observation["markets"]),
                "realizedLegacyTrades": len(trades),
                **coverage,
            },
            "method": {
                "strategies": list(LEGACY_STRATEGIES),
                "observerVersions": list(VERSIONS),
                "usesRecordedPaperFills": True,
                "pnlSource": "recorded realized trade pnl including recorded exits and fees",
                "causalObserverReplay": True,
                "singleReadTransactionSnapshot": True,
                "asofPolicy": (
                    "observations at or before each diagnostics signal_timestamp; "
                    "opened_at fallback when signal timestamp is unavailable"
                ),
                "officialSettlementPolicy": (
                    "only OFFICIAL UP/DOWN market settlements enter Observer history, "
                    "revealed after the market's final saved observation"
                ),
                "observerWindowMarkets": OBSERVER_WINDOW_MARKETS,
                "observerMinSettledSamples": OBSERVER_MIN_SETTLED_SAMPLES,
                "noSignalRegeneration": True,
                "interpretation": (
                    "counterfactual entry filter over actual realized paper trades; "
                    "blocked trades are omitted and allowed trades retain recorded pnl"
                ),
            },
            "stabilityRule": {
                "minimumRetainedTrades": MIN_RETAINED_TRADES,
                "minimumRetentionRate": MIN_RETENTION_RATE,
                "positiveOverallValidationAndHoldoutPnl": True,
                "minimumPositiveTemporalFolds": MIN_POSITIVE_FOLDS,
                "temporalFoldCount": FOLD_COUNT,
                "drawdownMustBeBelowBase": True,
                "warning": (
                    "A pass is a retrospective research candidate, not evidence for live activation."
                ),
            },
            "scopes": {
                "fullAvailable": available,
                "commonWindow": common,
            },
        }
        return report, evaluations
    finally:
        db.close()


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Legacy A-L x Observer stability backtest",
        "",
        f"Generated (UTC): `{report['generatedAt']}`",
        f"Data through (UTC): `{report['dataSnapshot']['lastObservation']}`",
        "",
        "This is a causal Observer overlay on recorded, realized paper fills. "
        "It does not change live routing or regenerate legacy signals.",
        "",
    ]
    for scope_name in ("fullAvailable", "commonWindow"):
        scope = report["scopes"][scope_name]
        lines.extend(
            [
                f"## {scope_name}",
                "",
                f"Window: `{scope['start'] or 'per-strategy available start'}` to "
                f"`{scope['end'] or 'per-strategy available end'}`",
                "",
                "| Strategy | Base PnL | Base MDD | Best version | Trades | Retained | PnL | Val PnL | Holdout PnL | Positive folds | MDD | Stability pass |",
                "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for strategy in LEGACY_STRATEGIES:
            values = scope["summaries"][strategy]
            base = values["BASE"]["overall"]
            best = values[values["best"]]
            overall = best["overall"]
            lines.append(
                f"| {strategy} | {base['realizedPnl']:.2f} | {base['maxDrawdown']:.2f} | "
                f"{best['observerVersion']} | {overall['trades']} | "
                f"{_pct(overall['retentionRate'])} | {overall['realizedPnl']:.2f} | "
                f"{best['splits']['validation']['realizedPnl']:.2f} | "
                f"{best['splits']['holdout']['realizedPnl']:.2f} | "
                f"{overall['positiveFolds']}/{FOLD_COUNT} | {overall['maxDrawdown']:.2f} | "
                f"{'PASS' if best['stability']['passed'] else 'NO'} |"
            )
        passes = scope["stabilityPasses"]
        lines.extend(
            [
                "",
                "Stability passes: "
                + (
                    ", ".join(
                        f"{item['strategy']}+{item['observerVersion']}" for item in passes
                    )
                    if passes
                    else "none"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Stability gate",
            "",
            f"At least {MIN_RETAINED_TRADES} trades and {_pct(MIN_RETENTION_RATE)} retention; "
            "positive overall, validation, and holdout PnL; at least "
            f"{MIN_POSITIVE_FOLDS}/{FOLD_COUNT} positive chronological folds; and MDD below BASE.",
            "",
            "A PASS remains retrospective paper evidence only. It is not authorization to change live settings.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any], evaluations: list[dict[str, Any]], output_dir: Path
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"legacy-a-l-observer-stability-{stamp}"
    json_path = output_dir / f"{base}.json"
    markdown_path = output_dir / f"{base}.md"
    ranking_path = output_dir / f"{base}-ranking.csv"
    audit_path = output_dir / f"{base}-trade-audit.csv"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    ranking_rows: list[dict[str, Any]] = []
    for scope_name, scope in report["scopes"].items():
        for item in scope["ranking"]:
            overall = item["overall"]
            ranking_rows.append(
                {
                    "scope": scope_name,
                    "strategy": item["strategy"],
                    "observer_version": item["observerVersion"],
                    "stability_pass": item["stability"]["passed"],
                    "trades": overall["trades"],
                    "retention_rate": overall["retentionRate"],
                    "wins": overall["wins"],
                    "losses": overall["losses"],
                    "win_rate": overall["winRate"],
                    "realized_pnl": overall["realizedPnl"],
                    "roi": overall["roi"],
                    "profit_factor": overall["profitFactor"],
                    "max_drawdown": overall["maxDrawdown"],
                    "max_consecutive_losses": overall["maxConsecutiveLosses"],
                    "positive_folds": overall["positiveFolds"],
                    "worst_fold_pnl": overall["worstFoldPnl"],
                    "validation_pnl": item["splits"]["validation"]["realizedPnl"],
                    "holdout_pnl": item["splits"]["holdout"]["realizedPnl"],
                    "pnl_delta_vs_base": item["pnlDeltaVsBase"],
                    "drawdown_reduction_vs_base": item[
                        "drawdownReductionVsBase"
                    ],
                    "failed_checks": ",".join(
                        item["stability"]["failedChecks"]
                    ),
                }
            )
    with ranking_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ranking_rows[0].keys()))
        writer.writeheader()
        writer.writerows(ranking_rows)
    with audit_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(evaluations[0].keys()))
        writer.writeheader()
        writer.writerows(evaluations)
    return json_path, markdown_path, ranking_path, audit_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay frozen Observer versions over realized legacy A-L paper trades."
    )
    parser.add_argument("--db", type=Path, default=Path("data/simulation.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtests"))
    args = parser.parse_args()
    report, evaluations = run(args.db)
    paths = write_outputs(report, evaluations, args.output_dir)
    print(
        json.dumps(
            {
                "outputs": [str(path) for path in paths],
                "fullAvailablePasses": report["scopes"]["fullAvailable"][
                    "stabilityPasses"
                ],
                "commonWindowPasses": report["scopes"]["commonWindow"][
                    "stabilityPasses"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
