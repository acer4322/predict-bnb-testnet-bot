from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_parameter_extraction_v1 as mod


def test_bid_buy_cash_and_terminal_payoffs():
    state = mod.PortfolioState()
    mod._apply_leg(state, "TAKER", "UP", "BID", 10.0, 0.40)
    metrics = mod._portfolio_metrics(state)
    assert metrics["cash"] == -4.0
    assert metrics["settle_up"] == 6.0
    assert metrics["settle_down"] == -4.0
    assert metrics["risk_deficit"] == 4.0
    assert metrics["payoff_gap"] == 10.0


def test_opposite_side_buy_can_reduce_gap_and_improve_worst_case():
    state = mod.PortfolioState(taker_up=10.0, taker_cash=-4.0)
    before = mod._portfolio_metrics(state)
    parent = {
        "role": "TAKER", "side": "DOWN", "quote_type": "BID",
        "shares": 5.0, "average_price": 0.10,
    }
    after = mod._portfolio_metrics(mod._apply_parent_cf(state, parent))
    assert before["worst_case_pnl"] == -4.0
    assert after["worst_case_pnl"] == 0.5
    assert before["abs_payoff_gap"] == 10.0
    assert after["abs_payoff_gap"] == 5.0
    assert mod._portfolio_effect(before, after) == "RISK_REDUCING"


def test_same_side_buy_is_exposure_add():
    state = mod.PortfolioState(taker_up=10.0, taker_cash=-4.0)
    before = mod._portfolio_metrics(state)
    parent = {
        "role": "TAKER", "side": "UP", "quote_type": "BID",
        "shares": 2.0, "average_price": 0.50,
    }
    after = mod._portfolio_metrics(mod._apply_parent_cf(state, parent))
    assert after["abs_payoff_gap"] > before["abs_payoff_gap"]
    assert mod._portfolio_effect(before, after) == "EXPOSURE_ADD"


def test_threshold_extraction_finds_separated_boundary():
    rows = []
    for x in range(20):
        rows.append({"risk_deficit": float(x), "taker_within_15s": int(x >= 10)})
    result = mod._best_threshold(rows, "risk_deficit", "taker_within_15s")
    assert result["threshold"] == 10.0
    assert result["youdenJ"] == 1.0
    checked = mod._evaluate_threshold(rows, "risk_deficit", "taker_within_15s", result["threshold"])
    assert checked["balancedAccuracy"] == 1.0


def test_strict_public_index_excludes_equal_timestamp(tmp_path):
    path = tmp_path / "public.csv"
    path.write_text(
        "market_id,decision_sampled_at_ms,seconds_left,predict_up_mid,predict_down_mid\n"
        "1,1000,200,0.55,0.45\n"
        "1,2000,199,0.70,0.30\n",
        encoding="utf-8",
    )
    index = mod.StrictPublicIndex(path)
    row = index.asof(1, 2000, 5000)
    assert row is not None
    assert row["sampled_ms"] == 1000
    assert row["predict_up_mid"] == 0.55
