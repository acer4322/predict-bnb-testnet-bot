from predict_bot import poly_binance_opening_leader_research_v2 as research


def test_paper_proxy_supports_fewer_reversal_hypothesis():
    rows = [
        {"marketId": 1, "marketPnlUsdt": 1.0, "profitableMarket": True, "losingMarket": False, "trades": 1, "reversalExits": 0, "sideSwitches": 0},
        {"marketId": 2, "marketPnlUsdt": 0.8, "profitableMarket": True, "losingMarket": False, "trades": 1, "reversalExits": 0, "sideSwitches": 0},
        {"marketId": 3, "marketPnlUsdt": 0.5, "profitableMarket": True, "losingMarket": False, "trades": 2, "reversalExits": 1, "sideSwitches": 0},
        {"marketId": 4, "marketPnlUsdt": -0.5, "profitableMarket": False, "losingMarket": True, "trades": 3, "reversalExits": 2, "sideSwitches": 1},
        {"marketId": 5, "marketPnlUsdt": -0.8, "profitableMarket": False, "losingMarket": True, "trades": 4, "reversalExits": 3, "sideSwitches": 2},
        {"marketId": 6, "marketPnlUsdt": -1.0, "profitableMarket": False, "losingMarket": True, "trades": 3, "reversalExits": 2, "sideSwitches": 2},
    ]
    payload = research._paper_profit_proxy(rows)
    assert payload["hypothesisVerdict"] == "SUPPORTS_FEWER_REVERSALS_AND_REENTRIES"
    assert payload["medianComparisons"]["profitableMedianLower_reversalExits"] is True
    assert payload["medianComparisons"]["profitableMedianLower_trades"] is True


def test_path_stats_distinguish_smooth_from_choppy():
    smooth = [(0, 0.40), (1000, 0.45), (2000, 0.50), (3000, 0.60), (4000, 0.70)]
    choppy = [(0, 0.40), (1000, 0.60), (2000, 0.40), (3000, 0.60), (4000, 0.70)]
    smooth_stats = research._path_stats(smooth)
    choppy_stats = research._path_stats(choppy)
    assert smooth_stats["directionFlips"] < choppy_stats["directionFlips"]
    assert smooth_stats["totalVariation"] < choppy_stats["totalVariation"]
    assert smooth_stats["efficiencyRatio"] > choppy_stats["efficiencyRatio"]
