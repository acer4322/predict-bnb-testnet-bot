from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import analyze_target_taker_ordinary_paper_pnl_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_ordinary_paper_pnl_robustness_v1_report.json"
DEFAULT_SETTLEMENT_DB = ROOT / "data" / "research" / "target_taker_official_settlements_v1.db"
REPORT_VERSION = "TARGET_TAKER_ORDINARY_PAPER_PNL_ROBUSTNESS_V1"
PRIMARY_SLIPPAGE_BPS = 50


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _nested_metrics(rows: list[dict[str, Any]], outer: str, inner: str) -> dict[str, Any]:
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[str(row[outer])][str(row[inner])].append(row)
    return {
        outer_key: {inner_key: base._metrics(values) for inner_key, values in sorted(inner_map.items())}
        for outer_key, inner_map in sorted(groups.items())
    }


def _fold_direction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for fold in sorted({int(row["fold"]) for row in rows}):
        fold_rows = [row for row in rows if int(row["fold"]) == fold]
        result[str(fold)] = {
            "ALL": base._metrics(fold_rows),
            "UP": base._metrics([row for row in fold_rows if str(row["predicted_side"]) == "UP"]),
            "DOWN": base._metrics([row for row in fold_rows if str(row["predicted_side"]) == "DOWN"]),
        }
    return result


def _positive_fold_summary(by_fold: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for side in ("ALL", "UP", "DOWN"):
        rois = [
            float(payload[side]["roiOnStake"])
            for payload in by_fold.values()
            if payload.get(side, {}).get("roiOnStake") is not None
        ]
        summary[side] = {
            "folds": len(rois),
            "positiveFolds": sum(value > 0 for value in rois),
            "negativeFolds": sum(value < 0 for value in rois),
            "minRoi": min(rois) if rois else None,
            "medianRoi": median(rois) if rois else None,
            "maxRoi": max(rois) if rois else None,
            "meanRoi": sum(rois) / len(rois) if rois else None,
        }
    return summary


def _market_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[int(row["market_id"])].append(row)
    if not groups:
        return {"markets": 0}
    market_pnl: list[float] = []
    action_counts: list[int] = []
    stakes: list[float] = []
    both_side = 0
    for values in groups.values():
        market_pnl.append(sum(float(row["netPnl"]) for row in values))
        action_counts.append(len(values))
        stakes.append(sum(float(row["stake"]) for row in values))
        if len({str(row["predicted_side"]) for row in values}) > 1:
            both_side += 1
    return {
        "markets": len(groups),
        "profitableMarkets": sum(value > 0 for value in market_pnl),
        "losingMarkets": sum(value < 0 for value in market_pnl),
        "flatMarkets": sum(value == 0 for value in market_pnl),
        "totalNetPnl": sum(market_pnl),
        "avgNetPnlPerMarket": sum(market_pnl) / len(market_pnl),
        "medianNetPnlPerMarket": median(market_pnl),
        "bestMarketNetPnl": max(market_pnl),
        "worstMarketNetPnl": min(market_pnl),
        "actionsPerMarket": {
            "min": min(action_counts),
            "median": median(action_counts),
            "max": max(action_counts),
            "mean": sum(action_counts) / len(action_counts),
        },
        "stakePerMarket": {
            "min": min(stakes),
            "median": median(stakes),
            "max": max(stakes),
            "mean": sum(stakes) / len(stakes),
        },
        "marketsWithBothPredictedSides": both_side,
        "bothSideRate": both_side / len(groups),
    }


def _first_only(rows: list[dict[str, Any]], *, by_side: bool = False) -> list[dict[str, Any]]:
    chosen: dict[tuple[int, str] | tuple[int], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (int(item["sampled_ms"]), int(item["market_id"]))):
        key = (int(row["market_id"]), str(row["predicted_side"])) if by_side else (int(row["market_id"]),)
        chosen.setdefault(key, row)
    return sorted(chosen.values(), key=lambda item: (int(item["sampled_ms"]), int(item["market_id"])))


def _sensitivity_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_fold = _fold_direction(rows)
    return {
        "overall": base._metrics(rows),
        "byFoldAndDirection": by_fold,
        "foldStability": _positive_fold_summary(by_fold),
        "byPhase": base._group_metrics(rows, lambda row: str(row["phase"])),
        "byPriceBucket": base._group_metrics(rows, lambda row: base._price_bucket(float(row["entryAsk"]))),
        "marketAggregate": _market_aggregate(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Robustness audit for the frozen ordinary Target-like paper PnL policy; no refit and no threshold promotion.")
    parser.add_argument("--oof", type=Path, default=base.DEFAULT_OOF)
    parser.add_argument("--signal-db", type=Path, action="append", default=None)
    parser.add_argument("--settlement-db", type=Path, default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--window-rows", type=int, default=300)
    parser.add_argument("--min-history-rows", type=int, default=40)
    parser.add_argument("--max-price-lag-ms", type=int, default=1500)
    parser.add_argument("--fee-bps", type=int, default=200)
    parser.add_argument("--stake", type=float, default=1.0)
    args = parser.parse_args()

    signal_paths = args.signal_db if args.signal_db else base.DEFAULT_SIGNAL_DBS
    selected, selection_coverage = base._selected_rows(
        args.oof,
        window_rows=max(80, int(args.window_rows)),
        min_history_rows=max(20, min(int(args.min_history_rows), max(80, int(args.window_rows)))),
    )
    signals = base.SignalLookup(signal_paths)
    settlements = base.SettlementLookup(args.settlement_db)
    try:
        raw_rows, coverage = base._build_base_trades(
            selected,
            signals=signals,
            settlements=settlements,
            max_price_lag_ms=max(0, int(args.max_price_lag_ms)),
        )
    finally:
        signals.close()
        settlements.close()

    executed = [
        result for row in raw_rows
        if (result := base._trade_result(
            row,
            slippage_bps=PRIMARY_SLIPPAGE_BPS,
            fee_bps=max(0, int(args.fee_bps)),
            stake=float(args.stake),
        )) is not None
    ]
    first_market = _first_only(executed, by_side=False)
    first_market_side = _first_only(executed, by_side=True)
    by_fold_direction = _fold_direction(executed)

    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "purpose": "Test whether the positive aggregate OOF PnL survives chronological folds, direction splits, phase/price slices, and market-level exposure concentration.",
        "policyFrozen": {
            "hazardCandidate": base.PRIMARY_CANDIDATE,
            "sideGate": "phase/fold causal raw-rank top/bottom 20%",
            "mixedVeto": False,
            "slippageBps": PRIMARY_SLIPPAGE_BPS,
            "feeBps": max(0, int(args.fee_bps)),
            "stakePerActionUsdt": float(args.stake),
            "priceCap": None,
        },
        "selectionCoverage": selection_coverage,
        "marketDataCoverage": coverage,
        "allActions": {
            "overall": base._metrics(executed),
            "byFoldAndDirection": by_fold_direction,
            "foldStability": _positive_fold_summary(by_fold_direction),
            "byFoldAndPhase": _nested_metrics(executed, "fold", "phase"),
            "byFoldAndPriceBucket": {
                str(fold): base._group_metrics(
                    [row for row in executed if int(row["fold"]) == fold],
                    lambda row: base._price_bucket(float(row["entryAsk"])),
                )
                for fold in sorted({int(row["fold"]) for row in executed})
            },
            "byPhaseAndDirection": _nested_metrics(executed, "phase", "predicted_side"),
            "byPriceBucketAndDirection": {
                bucket: {
                    side: base._metrics([
                        row for row in executed
                        if base._price_bucket(float(row["entryAsk"])) == bucket and str(row["predicted_side"]) == side
                    ])
                    for side in ("UP", "DOWN")
                }
                for bucket in sorted({base._price_bucket(float(row["entryAsk"])) for row in executed})
            },
            "marketAggregate": _market_aggregate(executed),
        },
        "sensitivityOnly": {
            "firstActionPerMarket": _sensitivity_payload(first_market),
            "firstActionPerMarketPerSide": _sensitivity_payload(first_market_side),
            "important": "These are exposure-concentration audits only; they do not promote one-per-market execution semantics.",
        },
    }
    _write_json(args.report, report)
    overall = report["allActions"]["overall"]
    stability = report["allActions"]["foldStability"]
    market = report["allActions"]["marketAggregate"]
    print(REPORT_VERSION)
    print(f"actions={overall.get('trades', 0)} markets={market.get('markets', 0)} net={overall.get('netPnl')} roi={overall.get('roiOnStake')}")
    print(f"positive folds ALL={stability['ALL']['positiveFolds']}/{stability['ALL']['folds']} UP={stability['UP']['positiveFolds']}/{stability['UP']['folds']} DOWN={stability['DOWN']['positiveFolds']}/{stability['DOWN']['folds']}")
    print(f"profitable markets={market.get('profitableMarkets')}/{market.get('markets')} both-side markets={market.get('marketsWithBothPredictedSides')}")
    print(f"Report: {args.report.expanduser().resolve()}")
    print("No direction, phase, price bucket, exposure cap, paper strategy, or live strategy was promoted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
