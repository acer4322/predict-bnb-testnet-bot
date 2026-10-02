from research.futures_lead_variant_observer_backtest import _cohorts
from research.legacy_observer_backtest import LegacyTrade


def _trade(identifier: int, strategy: str, entry: float) -> LegacyTrade:
    return LegacyTrade(
        id=identifier,
        strategy=strategy,
        market_id=identifier,
        side="UP",
        status="SETTLED_WIN",
        entry_price=entry,
        stake=5.0,
        fees=0.1,
        pnl=1.0,
        opened_at=f"2026-01-{identifier:02d}T00:00:00+00:00",
        signal_at=f"2026-01-{identifier:02d}T00:00:00+00:00",
        signal_time_source="opened_at",
    )


def test_variant_cohorts_apply_frozen_signal_and_entry_boundaries() -> None:
    trades = [
        _trade(1, "R_FUTURES_LEAD", 0.20),
        _trade(2, "R_FUTURES_LEAD", 0.21),
        _trade(3, "R_FUTURES_LEAD_DISTANCE", 0.60),
    ]
    cohorts = _cohorts(
        trades,
        {
            1: {"signal": 0.999},
            2: {"signal": -1.0},
            3: {"signal": 2.0},
        },
    )

    assert [trade.id for trade in cohorts["R_FUTURES_LEAD_SIGNAL_100"]] == [2]
    assert [trade.id for trade in cohorts["R_FUTURES_LEAD_MIN_ENTRY_020"]] == [2]
    assert [trade.id for trade in cohorts["R_FUTURES_LEAD_DISTANCE"]] == [3]
