from __future__ import annotations

from research.new_five_strategy_backtest import (
    Trade,
    _calibrated_probability,
    _fit_logistic,
    _microprice_score,
    _nearest_micro,
    _ofi,
    _summarize,
)


def book(**overrides):
    values = {
        "up_bid": 0.49,
        "up_ask": 0.51,
        "up_bid_size": 20.0,
        "up_ask_size": 5.0,
        "down_bid": 0.49,
        "down_ask": 0.51,
        "down_bid_size": 5.0,
        "down_ask_size": 20.0,
    }
    values.update(overrides)
    return values


def test_microprice_score_points_toward_stronger_up_queue():
    assert _microprice_score(book()) > 0


def test_normalized_ofi_rewards_bid_replenishment_and_ask_removal():
    previous = book(up_bid_size=10.0, up_ask_size=10.0)
    current = book(up_bid_size=20.0, up_ask=0.50, up_ask_size=4.0)
    assert _ofi(previous, current, "UP") > 0


def test_logistic_calibration_is_monotonic_in_market_probability():
    rows = [
        {"marketProbability": probability, "winner": winner}
        for probability, winner in (
            (0.20, "DOWN"),
            (0.30, "DOWN"),
            (0.40, "DOWN"),
            (0.60, "UP"),
            (0.70, "UP"),
            (0.80, "UP"),
        )
    ]
    beta = _fit_logistic(rows)
    low = _calibrated_probability(rows[0], beta)
    high = _calibrated_probability(rows[-1], beta)
    assert 0 < low < high < 1


def test_summary_computes_peak_to_trough_drawdown_and_streak():
    pnls = (10.0, -4.0, -7.0, 3.0)
    trades = [
        Trade(
            strategy="TEST",
            split="development",
            market_id=index,
            timestamp="2026-01-01T00:00:00+00:00",
            horizon_seconds=60,
            side="UP",
            winner="UP" if pnl > 0 else "DOWN",
            raw_ask=0.5,
            entry_price=0.5,
            shares=20.0,
            fee=0.2,
            pnl=pnl,
            signal=1.0,
        )
        for index, pnl in enumerate(pnls)
    ]
    summary = _summarize(trades, 10)
    assert summary["realizedPnl"] == 2.0
    assert summary["maxDrawdown"] == 11.0
    assert summary["maxConsecutiveLosses"] == 2


def test_microstructure_alignment_is_strictly_asof_without_future_tick():
    rows = [(1_000, 100.0, 101.0), (2_000, 102.0, 103.0)]
    assert _nearest_micro(rows, 1_900) == rows[0]
    assert _nearest_micro(rows, 2_000) == rows[1]
