from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import analyze_target_taker_trigger_conditioned_selective_replay_v1 as selective

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OOF = ROOT / "data" / "research" / "target_taker_trigger_conditioned_action_v1_oof.csv"
DEFAULT_SIGNAL_DBS = [
    ROOT / "data" / "wallet_taker_signals.db",
    ROOT / "data" / "public_research_archive_v1.db",
]
DEFAULT_SETTLEMENT_DB = ROOT / "data" / "simulation.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_ordinary_paper_pnl_v1_report.json"
REPORT_VERSION = "TARGET_TAKER_ORDINARY_PAPER_PNL_V1_OFFICIAL_SETTLEMENT"
PRIMARY_CANDIDATE = "TOP10_LEVEL_CD5"
PRIMARY_SIDE_FRACTION = 0.20
PRICE_CAPS: tuple[float | None, ...] = (None, 0.50, 0.60, 0.70, 0.80, 0.90)
SLIPPAGE_SCENARIOS_BPS = (0, 25, 50, 100)
PRICE_BUCKETS = (
    ("LT_030", 0.0, 0.30),
    ("030_TO_050", 0.30, 0.50),
    ("050_TO_070", 0.50, 0.70),
    ("070_TO_085", 0.70, 0.85),
    ("GE_085", 0.85, 1.0000001),
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _normalize_winner(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"UP", "YES", "TRUE", "1"}:
        return "UP"
    if text in {"DOWN", "NO", "FALSE", "0"}:
        return "DOWN"
    return None


def _fee(shares: float, price: float, fee_bps: int | float) -> float:
    if shares <= 0 or not 0 <= price <= 1:
        return 0.0
    return shares * min(price, 1.0 - price) * float(fee_bps) / 10_000.0


class SignalLookup:
    def __init__(self, paths: Iterable[Path]) -> None:
        self.sources: list[dict[str, Any]] = []
        for priority, raw in enumerate(paths):
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                continue
            db = _connect_ro(path)
            if not _has_table(db, "wallet_taker_signal_snapshots"):
                db.close()
                continue
            columns = _columns(db, "wallet_taker_signal_snapshots")
            required = {"market_id", "sampled_at_ms", "predict_up_ask", "predict_down_ask"}
            if not required.issubset(columns):
                db.close()
                continue
            order_tail = ""
            if "timestamp_ns" in columns:
                order_tail = ", timestamp_ns DESC"
            elif "id" in columns:
                order_tail = ", id DESC"
            self.sources.append(
                {
                    "path": path,
                    "db": db,
                    "priority": priority,
                    "order_tail": order_tail,
                }
            )

    def close(self) -> None:
        for source in self.sources:
            source["db"].close()

    def asof(self, market_id: int, sampled_ms: int, max_lag_ms: int) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for source in self.sources:
            row = source["db"].execute(
                "SELECT sampled_at_ms,predict_up_ask,predict_down_ask,predict_up_bid,predict_down_bid "
                "FROM wallet_taker_signal_snapshots "
                "WHERE market_id=? AND sampled_at_ms<=? AND sampled_at_ms>=? "
                f"ORDER BY sampled_at_ms DESC{source['order_tail']} LIMIT 1",
                (int(market_id), int(sampled_ms), int(sampled_ms) - int(max_lag_ms)),
            ).fetchone()
            if row is None:
                continue
            candidate = dict(row)
            candidate["source"] = str(source["path"])
            candidate["sourcePriority"] = int(source["priority"])
            candidate["lagMs"] = int(sampled_ms) - int(candidate["sampled_at_ms"])
            if best is None:
                best = candidate
                continue
            current_key = (int(candidate["sampled_at_ms"]), int(candidate["sourcePriority"]))
            best_key = (int(best["sampled_at_ms"]), int(best["sourcePriority"]))
            if current_key >= best_key:
                best = candidate
        return best


class SettlementLookup:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.db = _connect_ro(self.path)
        table = "market_settlements"
        if not _has_table(self.db, table):
            raise RuntimeError(f"market_settlements missing: {self.path}")
        required = {"market_id", "status", "official_winner"}
        missing = sorted(required - _columns(self.db, table))
        if missing:
            raise RuntimeError("market_settlements missing columns: " + ", ".join(missing))

    def close(self) -> None:
        self.db.close()

    def official(self, market_id: int) -> str | None:
        row = self.db.execute(
            "SELECT status,official_winner FROM market_settlements WHERE market_id=? LIMIT 1",
            (int(market_id),),
        ).fetchone()
        if row is None or str(row["status"] or "").upper() != "OFFICIAL":
            return None
        return _normalize_winner(row["official_winner"])


def _selected_rows(
    oof_path: Path,
    *,
    window_rows: int,
    min_history_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = selective._prepare(oof_path)
    source = [row for row in rows if row["candidate"] == PRIMARY_CANDIDATE]
    marked, coverage = selective._causal_marks(
        source,
        side_fraction=PRIMARY_SIDE_FRACTION,
        mixed_veto_fraction=None,
        window_rows=window_rows,
        min_history_rows=min_history_rows,
    )
    return [row for row in marked if bool(row.get("selected"))], coverage


def _build_base_trades(
    selected: list[dict[str, Any]],
    *,
    signals: SignalLookup,
    settlements: SettlementLookup,
    max_price_lag_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    missing_price = 0
    missing_settlement = 0
    invalid_ask = 0
    source_counts: dict[str, int] = defaultdict(int)
    lag_values: list[int] = []

    for row in selected:
        side = str(row.get("predicted_side") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue
        snapshot = signals.asof(int(row["market_id"]), int(row["sampled_ms"]), max_price_lag_ms)
        if snapshot is None:
            missing_price += 1
            continue
        ask_raw = snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask")
        try:
            ask = float(ask_raw)
        except (TypeError, ValueError):
            invalid_ask += 1
            continue
        if not math.isfinite(ask) or not 0 < ask < 1:
            invalid_ask += 1
            continue
        winner = settlements.official(int(row["market_id"]))
        if winner is None:
            missing_settlement += 1
            continue
        enriched = dict(row)
        enriched.update(
            {
                "entryAsk": ask,
                "priceSampledMs": int(snapshot["sampled_at_ms"]),
                "priceLagMs": int(snapshot["lagMs"]),
                "priceSource": str(snapshot["source"]),
                "officialWinner": winner,
            }
        )
        rows.append(enriched)
        source_counts[str(snapshot["source"])] += 1
        lag_values.append(int(snapshot["lagMs"]))

    return rows, {
        "selectedActions": len(selected),
        "pricedAndOfficialActions": len(rows),
        "usableCoverage": len(rows) / len(selected) if selected else None,
        "missingPrice": missing_price,
        "invalidAsk": invalid_ask,
        "missingOfficialSettlement": missing_settlement,
        "priceSources": dict(source_counts),
        "priceLagMs": {
            "min": min(lag_values) if lag_values else None,
            "median": sorted(lag_values)[len(lag_values) // 2] if lag_values else None,
            "max": max(lag_values) if lag_values else None,
        },
    }


def _trade_result(
    row: dict[str, Any],
    *,
    slippage_bps: int,
    fee_bps: int,
    stake: float,
) -> dict[str, Any] | None:
    ask = float(row["entryAsk"])
    execution_price = ask * (1.0 + float(slippage_bps) / 10_000.0)
    if not math.isfinite(execution_price) or not 0 < execution_price < 1:
        return None
    shares = float(stake) / execution_price
    entry_fee = _fee(shares, execution_price, fee_bps)
    won = str(row["predicted_side"]) == str(row["officialWinner"])
    payout = shares if won else 0.0
    gross_pnl = payout - float(stake)
    net_pnl = gross_pnl - entry_fee
    cash_cost = float(stake) + entry_fee
    result = dict(row)
    result.update(
        {
            "executionPrice": execution_price,
            "shares": shares,
            "entryFee": entry_fee,
            "stake": float(stake),
            "cashCost": cash_cost,
            "wonSettlement": int(won),
            "grossPnl": gross_pnl,
            "netPnl": net_pnl,
        }
    )
    return result


def _max_drawdown(rows: list[dict[str, Any]]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in sorted(rows, key=lambda item: (int(item["sampled_ms"]), int(item["market_id"]))):
        equity += float(row["netPnl"])
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"trades": 0}
    wins = sum(int(row["wonSettlement"]) for row in rows)
    losses = len(rows) - wins
    total_stake = sum(float(row["stake"]) for row in rows)
    total_cash = sum(float(row["cashCost"]) for row in rows)
    total_fees = sum(float(row["entryFee"]) for row in rows)
    gross = sum(float(row["grossPnl"]) for row in rows)
    net = sum(float(row["netPnl"]) for row in rows)
    positive = sum(max(0.0, float(row["netPnl"])) for row in rows)
    negative = -sum(min(0.0, float(row["netPnl"])) for row in rows)
    return {
        "trades": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "wins": wins,
        "losses": losses,
        "winRate": wins / len(rows),
        "avgEntryAsk": sum(float(row["entryAsk"]) for row in rows) / len(rows),
        "avgExecutionPrice": sum(float(row["executionPrice"]) for row in rows) / len(rows),
        "totalStake": total_stake,
        "totalCashCost": total_cash,
        "totalFees": total_fees,
        "grossPnl": gross,
        "netPnl": net,
        "roiOnStake": net / total_stake if total_stake else None,
        "roiOnCashCost": net / total_cash if total_cash else None,
        "avgNetPnlPerTrade": net / len(rows),
        "profitFactor": positive / negative if negative > 0 else None,
        "maxDrawdownUsdt": _max_drawdown(rows),
    }


def _group_metrics(rows: list[dict[str, Any]], key_fn: Any) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    return {key: _metrics(values) for key, values in sorted(groups.items())}


def _price_bucket(ask: float) -> str:
    value = float(ask)
    for name, low, high in PRICE_BUCKETS:
        if low <= value < high:
            return name
    return "OUT_OF_RANGE"


def _scenario_payload(
    base_rows: list[dict[str, Any]],
    *,
    slippage_bps: int,
    fee_bps: int,
    stake: float,
) -> dict[str, Any]:
    executed = [
        result
        for row in base_rows
        if (result := _trade_result(row, slippage_bps=slippage_bps, fee_bps=fee_bps, stake=stake)) is not None
    ]

    def direction(rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
        return [row for row in rows if str(row["predicted_side"]) == side]

    caps: dict[str, Any] = {}
    for cap in PRICE_CAPS:
        name = "NO_CAP" if cap is None else f"MAX_ASK_{int(cap * 100):02d}"
        kept = executed if cap is None else [row for row in executed if float(row["entryAsk"]) <= float(cap)]
        caps[name] = {
            "maxEntryAsk": cap,
            "overall": _metrics(kept),
            "byPredictedSide": {
                "UP": _metrics(direction(kept, "UP")),
                "DOWN": _metrics(direction(kept, "DOWN")),
            },
            "byFold": _group_metrics(kept, lambda row: int(row["fold"])),
            "byMacroPhase": _group_metrics(kept, lambda row: str(row["phase"])),
            "byEntryAskBucket": _group_metrics(kept, lambda row: _price_bucket(float(row["entryAsk"]))),
        }
    return {
        "slippageBps": int(slippage_bps),
        "feeBps": int(fee_bps),
        "stakePerActionUsdt": float(stake),
        "executionRows": len(executed),
        "priceCaps": caps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the deployment-clean ordinary Target-like trigger policy against strict-as-of prediction asks "
            "and OFFICIAL market settlements. Selection is fixed to TOP10 hazard + causal side-tail20 + no MIXED veto."
        )
    )
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--signal-db", type=Path, action="append", default=None)
    parser.add_argument("--settlement-db", type=Path, default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--window-rows", type=int, default=300)
    parser.add_argument("--min-history-rows", type=int, default=40)
    parser.add_argument("--max-price-lag-ms", type=int, default=1500)
    parser.add_argument("--fee-bps", type=int, default=200)
    parser.add_argument("--stake", type=float, default=1.0)
    args = parser.parse_args()

    window_rows = max(80, int(args.window_rows))
    min_history_rows = max(20, min(int(args.min_history_rows), window_rows))
    max_price_lag_ms = max(0, int(args.max_price_lag_ms))
    fee_bps = max(0, int(args.fee_bps))
    stake = float(args.stake)
    if not math.isfinite(stake) or stake <= 0:
        raise SystemExit("stake must be positive")

    signal_paths = args.signal_db if args.signal_db else DEFAULT_SIGNAL_DBS
    selected, selection_coverage = _selected_rows(
        args.oof,
        window_rows=window_rows,
        min_history_rows=min_history_rows,
    )
    if not selected:
        raise SystemExit("primary selective policy produced no selected OOF rows")

    signals = SignalLookup(signal_paths)
    if not signals.sources:
        raise SystemExit(
            "no usable signal DB. Expected wallet_taker_signal_snapshots with predict_up_ask/predict_down_ask"
        )
    settlements = SettlementLookup(args.settlement_db)
    try:
        base_rows, coverage = _build_base_trades(
            selected,
            signals=signals,
            settlements=settlements,
            max_price_lag_ms=max_price_lag_ms,
        )
    finally:
        signals.close()
        settlements.close()

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Test economic value after freezing the validated ordinary imitation stack. "
            "Target labels do not decide trades; only OOF hazard/side scores, strict-as-of public asks, and later OFFICIAL settlement are used."
        ),
        "policyFrozenForAudit": {
            "hazardCandidate": PRIMARY_CANDIDATE,
            "sideGate": "phase/fold causal raw-rank top/bottom 20%",
            "mixedVeto": False,
            "windowRowsPerFoldPhase": window_rows,
            "minHistoryRowsPerFoldPhase": min_history_rows,
        },
        "executionSemantics": {
            "entry": "BUY predicted UP/DOWN at latest public ask at-or-before trigger; no future quote lookup",
            "maxPriceLagMs": max_price_lag_ms,
            "holding": "hold to OFFICIAL settlement",
            "stakePerActionUsdt": stake,
            "feeFormula": "shares * min(price,1-price) * fee_bps / 10000",
            "feeBps": fee_bps,
            "slippageScenariosBps": list(SLIPPAGE_SCENARIOS_BPS),
            "important": "Price-cap variants are sensitivity audits on this OOF sample, not promoted thresholds.",
        },
        "sources": {
            "oof": str(args.oof.expanduser().resolve()),
            "signalDbsRequested": [str(Path(path).expanduser().resolve()) for path in signal_paths],
            "settlementDb": str(args.settlement_db.expanduser().resolve()),
        },
        "selectionCoverage": selection_coverage,
        "marketDataCoverage": coverage,
        "analysisReady": bool(
            coverage.get("usableCoverage") is not None and float(coverage["usableCoverage"]) >= 0.80
        ),
        "scenarios": {},
    }

    print(REPORT_VERSION, flush=True)
    print(
        f"selected={len(selected):,} priced+official={len(base_rows):,} "
        f"coverage={coverage.get('usableCoverage')}",
        flush=True,
    )
    if not report["analysisReady"]:
        print("WARNING: usable price+official settlement coverage is below 80%; interpret PnL cautiously.", flush=True)

    for slippage in SLIPPAGE_SCENARIOS_BPS:
        name = f"SLIPPAGE_{int(slippage)}BPS"
        payload = _scenario_payload(
            base_rows,
            slippage_bps=int(slippage),
            fee_bps=fee_bps,
            stake=stake,
        )
        report["scenarios"][name] = payload
        primary = payload["priceCaps"]["NO_CAP"]["overall"]
        print(
            f"  {name}: trades={primary.get('trades', 0):,} "
            f"winRate={primary.get('winRate')} netPnl={primary.get('netPnl')} "
            f"roi={primary.get('roiOnStake')}",
            flush=True,
        )

    _write_json(args.report, report)
    print(f"Report: {args.report.expanduser().resolve()}", flush=True)
    print("No price cap, direction restriction, paper strategy, or live strategy was promoted.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
