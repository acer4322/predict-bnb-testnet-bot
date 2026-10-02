from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from predict_bot.core import taker_fee
from predict_bot.target_style_controller_v0 import (
    DOWN,
    NEUTRAL,
    UP,
    ControllerV0Config,
    PortfolioState,
    TargetStyleControllerV0,
    evaluate_opportunity,
    market_signal_from_mapping,
)

VERSION = "TARGET_STYLE_HIERARCHICAL_CONTROLLER_V0"
DEFAULT_DB = ROOT / "data" / "simulation.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_style_controller_v0_report.json"
DEFAULT_ACTIONS = ROOT / "data" / "research" / "target_style_controller_v0_actions.csv"
TAIPEI = ZoneInfo("Asia/Taipei")
FEE_BPS = 200


@dataclass
class Fill:
    market_id: int
    strategy: str
    observation_id: int
    timestamp: str
    action: str
    side: str
    shares: float
    price: float
    visible_ask_size: float
    fee: float
    seconds_left: float
    spot_move_bps: float
    opportunity_status: str
    portfolio_up_after: float
    portfolio_down_after: float
    portfolio_net_after: float
    portfolio_gross_after: float


@dataclass
class MarketResult:
    strategy: str
    split: str
    regime: str
    market_id: int
    first_observation_id: int
    first_timestamp: str
    winner: str
    up_shares: float
    down_shares: float
    net_shares: float
    gross_shares: float
    paired_shares: float
    cash_spent: float
    fees_paid: float
    payout: float
    pnl: float
    add_actions: int
    repair_actions: int
    total_actions: int
    had_repair: bool


@dataclass
class _ReplayState:
    portfolio: PortfolioState
    fills: list[Fill]
    add_actions: int = 0
    repair_actions: int = 0


def _open_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _split_labels(market_rows: list[sqlite3.Row]) -> dict[int, str]:
    total = len(market_rows)
    dev_end = math.ceil(total * 0.60)
    val_end = math.ceil(total * 0.80)
    out: dict[int, str] = {}
    for idx, row in enumerate(market_rows):
        mid = int(row["market_id"])
        out[mid] = "development" if idx < dev_end else "validation" if idx < val_end else "holdout"
    return out


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _regime(first_timestamp: str) -> str:
    local_date = _parse_ts(first_timestamp).astimezone(TAIPEI).date().isoformat()
    return "STRESS_2026_08_16" if local_date == "2026-08-16" else "ORDINARY"


def _finite_book(row: sqlite3.Row) -> bool:
    try:
        values = [
            float(row["start_price"]), float(row["spot_price"]),
            float(row["up_bid"]), float(row["up_ask"]),
            float(row["down_bid"]), float(row["down_ask"]),
            float(row["up_ask_size"]), float(row["down_ask_size"]),
            float(row["seconds_left"]),
        ]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in values):
        return False
    _, _, ub, ua, db, da, uas, das, seconds_left = values
    return (
        0 <= ub <= ua <= 1
        and 0 <= db <= da <= 1
        and ua > 0 and da > 0
        and uas > 0 and das > 0
        and 0 <= seconds_left <= 300.5
    )


def _fill(
    *,
    state: _ReplayState,
    row: sqlite3.Row,
    strategy: str,
    action: str,
    side: str,
    requested_shares: float,
    opportunity_status: str,
    spot_move_bps: float,
) -> bool:
    if side not in {UP, DOWN} or requested_shares <= 1e-12:
        return False
    ask = float(row[f"{side.lower()}_ask"])
    visible = float(row[f"{side.lower()}_ask_size"])
    shares = min(float(requested_shares), visible)
    if shares <= 1e-12:
        return False
    fee = taker_fee(shares, ask, FEE_BPS)
    ts_text = str(row["timestamp"])
    ts = _parse_ts(ts_text).timestamp()
    state.portfolio.add_fill(side, shares, ask, fee, ts)
    if action == "TAKER_ADD":
        state.add_actions += 1
    elif action == "TAKER_REPAIR":
        state.repair_actions += 1
    state.fills.append(
        Fill(
            market_id=int(row["market_id"]),
            strategy=strategy,
            observation_id=int(row["id"]),
            timestamp=ts_text,
            action=action,
            side=side,
            shares=shares,
            price=ask,
            visible_ask_size=visible,
            fee=fee,
            seconds_left=float(row["seconds_left"]),
            spot_move_bps=spot_move_bps,
            opportunity_status=opportunity_status,
            portfolio_up_after=state.portfolio.up_shares,
            portfolio_down_after=state.portfolio.down_shares,
            portfolio_net_after=state.portfolio.net_shares,
            portfolio_gross_after=state.portfolio.gross_shares,
        )
    )
    return True


def _settle(
    *,
    strategy: str,
    split: str,
    market_id: int,
    first_observation_id: int,
    first_timestamp: str,
    winner: str,
    state: _ReplayState,
) -> MarketResult:
    p = state.portfolio
    payout = p.up_shares if winner == UP else p.down_shares
    pnl = payout - p.cash_spent - p.fees_paid
    return MarketResult(
        strategy=strategy,
        split=split,
        regime=_regime(first_timestamp),
        market_id=market_id,
        first_observation_id=first_observation_id,
        first_timestamp=first_timestamp,
        winner=winner,
        up_shares=p.up_shares,
        down_shares=p.down_shares,
        net_shares=p.net_shares,
        gross_shares=p.gross_shares,
        paired_shares=min(p.up_shares, p.down_shares),
        cash_spent=p.cash_spent,
        fees_paid=p.fees_paid,
        payout=payout,
        pnl=pnl,
        add_actions=state.add_actions,
        repair_actions=state.repair_actions,
        total_actions=len(state.fills),
        had_repair=state.repair_actions > 0,
    )


def _replay_direct_once(
    rows: list[sqlite3.Row],
    *,
    split: str,
    winner: str,
    cfg: ControllerV0Config,
) -> tuple[MarketResult, list[Fill]]:
    state = _ReplayState(PortfolioState(), [])
    for row in rows:
        if not _finite_book(row):
            continue
        signal = market_signal_from_mapping(row)
        opportunity = evaluate_opportunity(signal, cfg)
        if opportunity.status != "CONFIRMED" or opportunity.side not in {UP, DOWN}:
            continue
        ask = signal.up_ask if opportunity.side == UP else signal.down_ask
        if ask > cfg.max_entry_ask:
            continue
        filled = _fill(
            state=state,
            row=row,
            strategy="DIRECT_ONCE",
            action="TAKER_ADD",
            side=opportunity.side,
            requested_shares=cfg.target_net_shares,
            opportunity_status=opportunity.status,
            spot_move_bps=opportunity.spot_move_bps,
        )
        if filled:
            break
    first = rows[0]
    return _settle(
        strategy="DIRECT_ONCE",
        split=split,
        market_id=int(first["market_id"]),
        first_observation_id=int(first["id"]),
        first_timestamp=str(first["timestamp"]),
        winner=winner,
        state=state,
    ), state.fills


def _replay_layered(
    rows: list[sqlite3.Row],
    *,
    split: str,
    winner: str,
    cfg: ControllerV0Config,
) -> tuple[MarketResult, list[Fill], Counter[str]]:
    state = _ReplayState(PortfolioState(), [])
    controller = TargetStyleControllerV0(cfg)
    intent_counts: Counter[str] = Counter()
    for row in rows:
        if not _finite_book(row):
            continue
        signal = market_signal_from_mapping(row)
        now_ts = _parse_ts(str(row["timestamp"])).timestamp()
        trace = controller.step(signal, state.portfolio, now_ts=now_ts)
        intent_counts[trace.intent.action] += 1
        if trace.intent.action not in {"TAKER_ADD", "TAKER_REPAIR"}:
            continue
        _fill(
            state=state,
            row=row,
            strategy="LAYERED_V0",
            action=trace.intent.action,
            side=trace.intent.side,
            requested_shares=trace.intent.shares,
            opportunity_status=trace.opportunity.status,
            spot_move_bps=trace.opportunity.spot_move_bps,
        )
    first = rows[0]
    result = _settle(
        strategy="LAYERED_V0",
        split=split,
        market_id=int(first["market_id"]),
        first_observation_id=int(first["id"]),
        first_timestamp=str(first["timestamp"]),
        winner=winner,
        state=state,
    )
    return result, state.fills, intent_counts


def _summarize(results: Iterable[MarketResult]) -> dict[str, Any]:
    xs = sorted(list(results), key=lambda r: r.first_observation_id)
    traded = [r for r in xs if r.total_actions > 0]
    wins = sum(r.pnl > 0 for r in traded)
    losses = sum(r.pnl <= 0 for r in traded)
    pnl = sum(r.pnl for r in xs)
    cost = sum(r.cash_spent + r.fees_paid for r in xs)
    gross_profit = sum(max(0.0, r.pnl) for r in traded)
    gross_loss = -sum(min(0.0, r.pnl) for r in traded)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for r in xs:
        equity += r.pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if r.total_actions > 0 and r.pnl <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        elif r.total_actions > 0:
            streak = 0
    return {
        "candidateMarkets": len(xs),
        "tradedMarkets": len(traded),
        "coverageRate": len(traded) / len(xs) if xs else None,
        "winningMarkets": wins,
        "losingMarkets": losses,
        "winRate": wins / len(traded) if traded else None,
        "cashSpent": sum(r.cash_spent for r in xs),
        "feesPaid": sum(r.fees_paid for r in xs),
        "realizedPnl": pnl,
        "roi": pnl / cost if cost else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_dd,
        "maxConsecutiveLosingMarkets": max_streak,
        "addActions": sum(r.add_actions for r in xs),
        "repairActions": sum(r.repair_actions for r in xs),
        "marketsWithRepair": sum(r.had_repair for r in xs),
        "averageGrossSharesTradedMarket": (
            sum(r.gross_shares for r in traded) / len(traded) if traded else None
        ),
        "averageAbsNetSharesTradedMarket": (
            sum(abs(r.net_shares) for r in traded) / len(traded) if traded else None
        ),
        "averagePairedSharesTradedMarket": (
            sum(r.paired_shares for r in traded) / len(traded) if traded else None
        ),
    }


def _delta(layered: dict[str, Any], direct: dict[str, Any]) -> dict[str, Any]:
    def d(key: str) -> float | None:
        a, b = layered.get(key), direct.get(key)
        return None if a is None or b is None else float(a) - float(b)
    return {
        "realizedPnlLayeredMinusDirect": d("realizedPnl"),
        "roiLayeredMinusDirect": d("roi"),
        "winRateLayeredMinusDirect": d("winRate"),
        "maxDrawdownLayeredMinusDirect": d("maxDrawdown"),
        "maxConsecutiveLosingMarketsLayeredMinusDirect": d("maxConsecutiveLosingMarkets"),
    }


def run(db_path: Path, cfg: ControllerV0Config) -> tuple[dict[str, Any], list[Fill]]:
    db = _open_ro(db_path)
    try:
        required_tables = {
            str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = {"observations", "market_settlements"} - required_tables
        if missing:
            raise RuntimeError("simulation DB missing tables: " + ", ".join(sorted(missing)))
        market_rows = db.execute(
            """SELECT o.market_id, MIN(o.id) AS first_observation_id,
                      MIN(o.timestamp) AS first_timestamp,
                      s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               GROUP BY o.market_id, s.official_winner
               ORDER BY first_observation_id"""
        ).fetchall()
        split_by_market = _split_labels(market_rows)
        winner_by_market = {int(r["market_id"]): str(r["official_winner"]) for r in market_rows}
        ids = [int(r["market_id"]) for r in market_rows]
        if not ids:
            raise RuntimeError("no officially settled markets in simulation DB")

        all_results: list[MarketResult] = []
        all_fills: list[Fill] = []
        layered_intents: Counter[str] = Counter()
        current_mid: int | None = None
        current_rows: list[sqlite3.Row] = []

        def process(rows: list[sqlite3.Row]) -> None:
            if not rows:
                return
            mid = int(rows[0]["market_id"])
            split = split_by_market[mid]
            winner = winner_by_market[mid]
            direct, direct_fills = _replay_direct_once(rows, split=split, winner=winner, cfg=cfg)
            layered, layered_fills, intents = _replay_layered(rows, split=split, winner=winner, cfg=cfg)
            all_results.extend((direct, layered))
            all_fills.extend(direct_fills)
            all_fills.extend(layered_fills)
            layered_intents.update(intents)

        for row in db.execute(
            """SELECT o.*, s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               ORDER BY o.id"""
        ):
            mid = int(row["market_id"])
            if current_mid is not None and mid != current_mid:
                process(current_rows)
                current_rows = []
            current_mid = mid
            current_rows.append(row)
        process(current_rows)
    finally:
        db.close()

    by_strategy = {
        name: [r for r in all_results if r.strategy == name]
        for name in ("DIRECT_ONCE", "LAYERED_V0")
    }
    summaries: dict[str, Any] = {}
    for name, results in by_strategy.items():
        summaries[name] = {
            "overall": _summarize(results),
            "splits": {
                split: _summarize(r for r in results if r.split == split)
                for split in ("development", "validation", "holdout")
            },
            "regimes": {
                regime: _summarize(r for r in results if r.regime == regime)
                for regime in ("ORDINARY", "STRESS_2026_08_16")
            },
        }

    report = {
        "version": VERSION,
        "policy": {
            "researchOnly": True,
            "liveTradingChanges": False,
            "goal": "Test whether a minimal hierarchical portfolio controller improves strategy-level stability before adding Target-specific complexity.",
            "inputs": [
                "recorded BTC spot displacement versus market start price",
                "recorded Poly/Predict binary top-of-book confirmation and execution price",
            ],
            "intentionallyDeferred": [
                "Prediction wallet-derived signal",
                "microstructure model inputs",
                "Maker execution",
                "learned models / EBM",
                "Target action timestamps or Target inventory",
            ],
            "noTargetLeakage": True,
            "thresholdsFrozenBeforeOutcomeInspection": True,
            "settlement": "OFFICIAL market_settlements only; winning shares pay 1, losing shares pay 0",
            "execution": "taker buy at recorded selected-side ask, capped by recorded visible ask size; 200 bps taker fee helper used by existing backtests",
            "repairMeaning": "buy opposite outcome only far enough to hedge current simulated net toward neutral; V0 never flips through neutral in one reversal",
        },
        "config": {**asdict(cfg), "fee_bps": FEE_BPS},
        "source": {
            "database": str(db_path.expanduser().resolve()),
            "candidateMarkets": len(by_strategy["DIRECT_ONCE"]),
            "splitPolicy": "chronological_60_20_20",
            "fidelityNote": "Replays recorded observation ticks. Events between saved ticks and queue-position Maker fills are not reconstructed.",
        },
        "architecture": [
            "Opportunity: spot direction must clear deadband and agree with Poly mid direction",
            "DesiredExposure: fixed signed net target",
            "Inventory: only this simulation's own UP/DOWN fills",
            "Risk/Repair: hard gross cap; opposite confirmed state hedges existing net toward neutral",
            "Urgency V0: HOLD / TAKER_ADD / TAKER_REPAIR only",
            "Execution: depth-capped recorded taker ask",
        ],
        "summaries": summaries,
        "layeredMinusDirect": {
            "overall": _delta(summaries["LAYERED_V0"]["overall"], summaries["DIRECT_ONCE"]["overall"]),
            "splits": {
                split: _delta(
                    summaries["LAYERED_V0"]["splits"][split],
                    summaries["DIRECT_ONCE"]["splits"][split],
                ) for split in ("development", "validation", "holdout")
            },
            "regimes": {
                regime: _delta(
                    summaries["LAYERED_V0"]["regimes"][regime],
                    summaries["DIRECT_ONCE"]["regimes"][regime],
                ) for regime in ("ORDINARY", "STRESS_2026_08_16")
            },
        },
        "layeredIntentCounts": dict(layered_intents),
        "marketResults": [asdict(r) for r in sorted(all_results, key=lambda x: (x.first_observation_id, x.strategy))],
    }
    return report, all_fills


def _write_csv(path: Path, rows: list[Fill]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Fill.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest minimal Target-style hierarchical portfolio controller V0")
    p.add_argument("--database", type=Path, default=DEFAULT_DB)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    args = p.parse_args()

    cfg = ControllerV0Config()
    report, fills = run(args.database, cfg)
    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(args.actions, fills)
    print(json.dumps({
        "version": VERSION,
        "candidateMarkets": report["source"]["candidateMarkets"],
        "direct": report["summaries"]["DIRECT_ONCE"]["overall"],
        "layered": report["summaries"]["LAYERED_V0"]["overall"],
        "delta": report["layeredMinusDirect"]["overall"],
        "report": str(out),
        "actions": str(args.actions.expanduser().resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
