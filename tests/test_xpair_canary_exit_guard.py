from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from predict_bot.xpair_canary_autopilot_server_v7 import (
    classify_pair_orders_with_exit_guard,
)
from predict_bot.xpair_exit_guard_common import (
    ExitLedger,
    evaluate_one_win_profitability,
)


def wei(value: str) -> str:
    return str(int(Decimal(value) * Decimal(10**18)))


def context(*, btc_fee: int = 0, eth_fee: int = 0):
    return {
        "legs": {
            "BTC": {"fee_bps": btc_fee, "token_id": "btc-token"},
            "ETH": {"fee_bps": eth_fee, "token_id": "eth-token"},
        },
        "quotes": {
            "BTC": {
                "symbol": "BTC",
                "amountIn": wei("1.60"),
                "amountOut": wei("4.00"),
                "averagePrice": "0.40",
            },
            "ETH": {
                "symbol": "ETH",
                "amountIn": wei("2.20"),
                "amountOut": wei("4.00"),
                "averagePrice": "0.55",
            },
        },
    }


def order(status: str, shares: str, cash: str):
    return {
        "status": status,
        "filledShareQty": shares,
        "filledUsdtAmount": cash,
    }


def test_actual_one_win_profit_is_held() -> None:
    result = evaluate_one_win_profitability(
        orders={
            "BTC": order("FILLED", "4", "1.6"),
            "ETH": order("FILLED", "4", "2.2"),
        },
        context=context(),
    )
    assert result["minimumPayout"] == Decimal("4")
    assert result["totalCost"] == Decimal("3.8")
    assert result["oneWinPnl"] == Decimal("0.2")
    assert result["hold"] is True


def test_non_positive_one_win_profit_requires_unwind() -> None:
    result = evaluate_one_win_profitability(
        orders={
            "BTC": order("FILLED", "4", "1.9"),
            "ETH": order("FILLED", "4", "2.2"),
        },
        context=context(),
    )
    assert result["oneWinPnl"] == Decimal("-0.1")
    assert result["hold"] is False


def test_share_mismatch_fails_hold_even_with_positive_cash_pnl() -> None:
    result = evaluate_one_win_profitability(
        orders={
            "BTC": order("FILLED", "4", "1.4"),
            "ETH": order("FILLED", "3.9", "2.2"),
        },
        context=context(),
    )
    assert result["oneWinPnl"] == Decimal("0.3")
    assert result["shareMismatch"] > Decimal("0.0025")
    assert result["hold"] is False


def test_both_filled_waits_for_post_fill_guard() -> None:
    action, status, _ = classify_pair_orders_with_exit_guard(
        btc_order=order("FILLED", "4", "1.6"),
        eth_order=order("FILLED", "4", "2.2"),
        tracking_age_seconds=2.0,
        market_ended=False,
    )
    assert action == "TRACK"
    assert status == "FILLED_BOTH_POST_FILL_CHECK"


def test_one_sided_terminal_fill_enters_recovery() -> None:
    action, status, _ = classify_pair_orders_with_exit_guard(
        btc_order=order("FILLED", "4", "1.6"),
        eth_order={"status": "REJECTED"},
        tracking_age_seconds=2.0,
        market_ended=False,
    )
    assert action == "TRACK"
    assert status == "ONE_SIDED_RECOVERY_REQUIRED"


def test_one_sided_open_order_waits_instead_of_false_unwind() -> None:
    action, status, _ = classify_pair_orders_with_exit_guard(
        btc_order=order("FILLED", "4", "1.6"),
        eth_order={"status": "NEW"},
        tracking_age_seconds=2.0,
        market_ended=False,
    )
    assert action == "TRACK"
    assert status == "ONE_SIDED_WAITING_OTHER_LEG"


def test_exit_ledger_persists_decision(tmp_path: Path) -> None:
    path = tmp_path / "xpair.db"
    first = ExitLedger(path)
    assert first.claim(run_id=7, market_key="1:2", trigger="BOTH_FILLED") is True
    first.update(
        7,
        status="HOLD_PROFITABLE_PAIR",
        reason="positive",
        one_win_pnl=Decimal("0.20"),
        total_cost=Decimal("3.80"),
        minimum_payout=Decimal("4.00"),
        resolved=True,
    )
    restored = ExitLedger(path).snapshot()
    assert restored is not None
    assert restored["status"] == "HOLD_PROFITABLE_PAIR"
    assert restored["oneWinPnlUsdt"] == 0.2
