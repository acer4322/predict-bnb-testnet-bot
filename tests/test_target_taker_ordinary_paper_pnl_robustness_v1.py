from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_taker_ordinary_paper_pnl_robustness_v1 as mod


def _row(market: int, fold: int, side: str, pnl: float, sampled: int) -> dict:
    return {
        "market_id": market,
        "fold": fold,
        "predicted_side": side,
        "phase": "MID",
        "entryAsk": 0.4,
        "sampled_ms": sampled,
        "netPnl": pnl,
        "stake": 1.0,
        "cashCost": 1.02,
        "entryFee": 0.02,
        "wonSettlement": int(pnl > 0),
        "grossPnl": pnl + 0.02,
        "executionPrice": 0.4,
    }


def test_first_only_respects_market_and_optional_side():
    rows = [
        _row(1, 1, "UP", 1.0, 1000),
        _row(1, 1, "UP", -1.0, 2000),
        _row(1, 1, "DOWN", 1.0, 3000),
        _row(2, 1, "UP", 1.0, 4000),
    ]
    assert len(mod._first_only(rows, by_side=False)) == 2
    assert len(mod._first_only(rows, by_side=True)) == 3


def test_market_aggregate_counts_correlated_exposure():
    rows = [
        _row(1, 1, "UP", 1.0, 1000),
        _row(1, 1, "DOWN", -0.5, 2000),
        _row(2, 1, "UP", -1.0, 3000),
    ]
    payload = mod._market_aggregate(rows)
    assert payload["markets"] == 2
    assert payload["profitableMarkets"] == 1
    assert payload["losingMarkets"] == 1
    assert payload["marketsWithBothPredictedSides"] == 1
    assert payload["actionsPerMarket"]["max"] == 2


def test_positive_fold_summary_keeps_direction_stability():
    rows = [
        _row(1, 1, "UP", 1.0, 1000),
        _row(2, 1, "DOWN", -1.0, 2000),
        _row(3, 2, "UP", -1.0, 3000),
        _row(4, 2, "DOWN", 1.0, 4000),
    ]
    by_fold = mod._fold_direction(rows)
    summary = mod._positive_fold_summary(by_fold)
    assert summary["UP"]["positiveFolds"] == 1
    assert summary["DOWN"]["positiveFolds"] == 1
    assert summary["ALL"]["folds"] == 2
