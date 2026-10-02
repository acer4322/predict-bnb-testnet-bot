from __future__ import annotations

import pytest

from tools.analyze_wallet_shadow_v0_capital import (
    ChurnScenario,
    Scenario,
    replay_market,
    replay_market_with_churn_guard,
    summarize,
)


def test_taker_cap_and_scale_preserve_event_count() -> None:
    events = [
        {"role": "MAKER", "side": "UP", "price": 0.4, "shares": 18},
        {"role": "TAKER", "side": "UP", "price": 0.6, "shares": 180},
    ]
    result = replay_market(events, "UP", Scenario(36, 1 / 18))
    assert result["eventCount"] == 2
    assert result["makerCost"] == 0.4
    assert result["takerCost"] == 1.2
    assert result["pnl"] == 1.4


def test_uniform_scale_preserves_roi() -> None:
    events = [
        {"role": "MAKER", "side": "DOWN", "price": 0.4, "shares": 18},
        {"role": "TAKER", "side": "UP", "price": 0.6, "shares": 36},
    ]
    full = replay_market(events, "UP", Scenario(180, 1))
    small = replay_market(events, "UP", Scenario(180, 1 / 18))
    assert full["pnl"] / full["cost"] == pytest.approx(small["pnl"] / small["cost"])


def test_summary_reports_drawdown_and_cost_ceiling() -> None:
    result = summarize([
        {"cost": 2, "pnl": 1, "makerCost": 1, "makerPnl": 0, "takerCost": 1, "takerPnl": 1, "eventCount": 2},
        {"cost": 3, "pnl": -2, "makerCost": 1, "makerPnl": 0, "takerCost": 2, "takerPnl": -2, "eventCount": 2},
    ])
    assert result["maximumCostPerMarketUsdt"] == 3
    assert result["maxDrawdownUsdt"] == 2
    assert result["longestLossStreak"] == 1


def test_churn_guard_blocks_taker_after_allowed_switches_but_keeps_maker() -> None:
    events = [
        {"role": "TAKER", "side": "UP", "price": 0.4, "shares": 18},
        {"role": "TAKER", "side": "DOWN", "price": 0.4, "shares": 18},
        {"role": "TAKER", "side": "UP", "price": 0.4, "shares": 18},
        {"role": "MAKER", "side": "DOWN", "price": 0.3, "shares": 18},
    ]
    result = replay_market_with_churn_guard(events, "DOWN", ChurnScenario(1, 18, 1))
    assert result["eventCount"] == 3
    assert result["originalEventCount"] == 4
    assert result["takerSideSwitchesObserved"] == 2
