from __future__ import annotations

from predict_bot.strategy_lifecycle_guard import build_strategy_lifecycle_summary


def row(strategy: str, market_id: int, pnl: float, *, cost: float = 5.0, seq: int = 0):
    return {
        "strategy": strategy,
        "market_id": market_id,
        "cost_usdt": cost,
        "pnl_usdt": pnl,
        "settled_at": f"2026-08-08T00:{seq:02d}:00+00:00",
    }


def by_strategy(payload):
    return {item["strategy"]: item for item in payload["strategies"]}


def test_all_whitelist_strategies_are_present_and_child_orders_roll_up():
    payload = build_strategy_lifecycle_summary(
        [
            row("R_MICROPRICE", 1, 2.0, seq=1),
            row("R_MICROPRICE:CONFIRM_ADD_1", 1, 1.0, cost=1.0, seq=2),
            row("R_MICROPRICE", 2, -1.0, seq=3),
            row("PAIR_ARB_010:UP", 10, 0.2, cost=1.0, seq=4),
            row("PAIR_ARB_010:DOWN", 10, 0.3, cost=1.0, seq=5),
        ],
        ["R_MICROPRICE", "R_FUTURES_LEAD", "PAIR_ARB_010"],
        ["R_MICROPRICE"],
    )
    strategies = by_strategy(payload)

    assert set(strategies) == {"R_MICROPRICE", "R_FUTURES_LEAD", "PAIR_ARB_010"}
    assert strategies["R_MICROPRICE"]["active"] is True
    assert strategies["R_MICROPRICE"]["settledMarkets"] == 2
    assert strategies["R_MICROPRICE"]["drawdown"]["lifetimePnlUsdt"] == 2.0
    assert strategies["PAIR_ARB_010"]["settledMarkets"] == 1
    assert strategies["PAIR_ARB_010"]["drawdown"]["lifetimePnlUsdt"] == 0.5
    assert strategies["R_FUTURES_LEAD"]["status"] == "NO_DATA"
    assert payload["advisoryOnly"] is True
    assert payload["automaticBlocking"] is False
    assert payload["automaticStakeChanges"] is False


def test_lifecycle_status_uses_last20_and_last50():
    rows = []
    market = 1
    patterns = {
        "ACTIVE": [1.0] * 50,
        "WATCH": [1.0] * 30 + [-1.0] * 20,
        "DEGRADED": [0.2] * 30 + [-1.0] * 20,
        "RECOVERY": [-1.0] * 30 + [1.0] * 20,
        "BUILDING": [1.0] * 10,
    }
    for strategy, pnls in patterns.items():
        for seq, pnl in enumerate(pnls):
            rows.append({
                "strategy": strategy,
                "market_id": market,
                "cost_usdt": 5.0,
                "pnl_usdt": pnl,
                "settled_at": f"2026-08-{1 + seq // 24:02d}T{seq % 24:02d}:00:00+00:00",
            })
            market += 1

    strategies = by_strategy(build_strategy_lifecycle_summary(rows, list(patterns)))
    assert strategies["ACTIVE"]["status"] == "ACTIVE"
    assert strategies["WATCH"]["status"] == "WATCH"
    assert strategies["DEGRADED"]["status"] == "DEGRADED"
    assert strategies["RECOVERY"]["status"] == "RECOVERY"
    assert strategies["BUILDING"]["status"] == "BUILDING"
    assert strategies["RECOVERY"]["last20"]["pnlUsdt"] > 0
    assert strategies["RECOVERY"]["last50"]["pnlUsdt"] < 0


def test_drawdown_and_recovery_evidence_are_strategy_local():
    payload = build_strategy_lifecycle_summary(
        [
            row("A", 1, 10.0, seq=1),
            row("A", 2, -8.0, seq=2),
            row("A", 3, -4.0, seq=3),
            row("A", 4, 6.0, seq=4),
            row("B", 5, 100.0, seq=5),
        ],
        ["A", "B"],
    )
    a = by_strategy(payload)["A"]

    assert a["drawdown"]["lifetimePnlUsdt"] == 4.0
    assert a["drawdown"]["peakPnlUsdt"] == 10.0
    assert a["drawdown"]["maxDrawdownUsdt"] == -12.0
    assert a["drawdown"]["currentDrawdownUsdt"] == -6.0
    assert a["drawdown"]["reboundFromTroughUsdt"] == 6.0
    assert a["drawdown"]["recoveryPct"] == 50.0
    assert a["recoveryEvidence"]["drawdownRecovering"] is True
