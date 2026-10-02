from __future__ import annotations

from research.legacy_observer_backtest import (
    LegacyTrade,
    _assign_segments,
    _signal_timestamp,
    _stability_assessment,
    _summarize,
)


def trade(identifier: int, pnl: float, timestamp: str) -> LegacyTrade:
    return LegacyTrade(
        id=identifier,
        strategy="A",
        market_id=identifier,
        side="UP",
        status="SETTLED_WIN" if pnl > 0 else "SETTLED_LOSS",
        entry_price=0.5,
        stake=10.0,
        fees=0.2,
        pnl=pnl,
        opened_at=timestamp,
        signal_at=timestamp,
        signal_time_source="opened_at",
    )


def test_signal_timestamp_prefers_valid_diagnostics_value() -> None:
    opened = "2026-01-01T00:00:01+00:00"
    signal, source = _signal_timestamp(
        opened,
        '{"signal_timestamp":"2026-01-01T00:00:00+00:00"}',
    )
    assert signal == "2026-01-01T00:00:00+00:00"
    assert source == "diagnostics.signal_timestamp"
    assert _signal_timestamp(opened, "not-json") == (opened, "opened_at")


def test_segments_are_chronological_sixty_twenty_twenty_with_five_folds() -> None:
    rows = [
        trade(index, 1.0, f"2026-01-{index:02d}T00:00:00+00:00")
        for index in range(1, 11)
    ]
    splits, folds = _assign_segments(rows)
    assert [splits[index] for index in range(1, 11)] == [
        "development",
        "development",
        "development",
        "development",
        "development",
        "development",
        "validation",
        "validation",
        "holdout",
        "holdout",
    ]
    assert [folds[index] for index in range(1, 11)] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]


def test_summary_computes_drawdown_streak_daily_and_fold_stability() -> None:
    rows = [
        trade(1, 10.0, "2026-01-01T00:00:00+00:00"),
        trade(2, -4.0, "2026-01-01T01:00:00+00:00"),
        trade(3, -7.0, "2026-01-02T00:00:00+00:00"),
        trade(4, 3.0, "2026-01-03T00:00:00+00:00"),
    ]
    summary = _summarize(
        rows,
        baseline_count=8,
        fold_by_id={1: 1, 2: 2, 3: 3, 4: 4},
    )
    assert summary["realizedPnl"] == 2.0
    assert summary["retentionRate"] == 0.5
    assert summary["maxDrawdown"] == 11.0
    assert summary["maxConsecutiveLosses"] == 2
    assert summary["positiveFolds"] == 2
    assert summary["worstFoldPnl"] == -7.0


def test_stability_gate_requires_sample_holdouts_folds_and_lower_drawdown() -> None:
    overall = {
        "trades": 120,
        "retentionRate": 0.4,
        "realizedPnl": 25.0,
        "positiveFolds": 4,
        "maxDrawdown": 12.0,
    }
    splits = {
        "development": {"realizedPnl": 5.0},
        "validation": {"realizedPnl": 10.0},
        "holdout": {"realizedPnl": 10.0},
    }
    result = _stability_assessment(overall, splits, {"maxDrawdown": 20.0})
    assert result["passed"] is True
    splits["holdout"]["realizedPnl"] = -1.0
    result = _stability_assessment(overall, splits, {"maxDrawdown": 20.0})
    assert result["passed"] is False
    assert "positiveHoldoutPnl" in result["failedChecks"]
