from __future__ import annotations

from pathlib import Path

import pytest

from predict_bot.xpair_canary_autopilot_server_v4 import (
    SafetyLedger,
    classify_pair_orders,
)


def order(status: str, *, shares: float = 0.0, usdt: float = 0.0):
    return {
        "status": status,
        "filledShareQty": shares,
        "filledUsdtAmount": usdt,
    }


def test_safety_lock_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "xpair.db"
    first = SafetyLedger(path)
    first.activate_incident(
        status="ONE_SIDED_FILL_LOCK",
        reason="BTC filled while ETH did not",
        market_key="1:2",
        run_id=7,
        btc_order_id="btc-order",
        eth_order_id="eth-order",
    )

    restored = SafetyLedger(path).snapshot()
    assert restored["locked"] is True
    assert restored["lockKind"] == "INCIDENT"
    assert restored["status"] == "ONE_SIDED_FILL_LOCK"
    assert restored["runId"] == 7


def test_operator_cannot_clear_active_tracking(tmp_path: Path) -> None:
    ledger = SafetyLedger(tmp_path / "xpair.db")
    ledger.begin_tracking(
        market_key="1:2",
        run_id=1,
        btc_order_id="btc",
        eth_order_id="eth",
        market_end_ms=123,
    )
    with pytest.raises(RuntimeError, match="still under automatic tracking"):
        ledger.clear_by_operator()


def test_operator_can_clear_persistent_incident(tmp_path: Path) -> None:
    ledger = SafetyLedger(tmp_path / "xpair.db")
    ledger.activate_incident(
        status="ORDER_SYNC_UNRESOLVED_LOCK",
        reason="orders not found",
        market_key="1:2",
        run_id=2,
    )
    ledger.clear_by_operator("checked Binance order history")
    snapshot = ledger.snapshot()
    assert snapshot["locked"] is False
    assert snapshot["status"] == "OPERATOR_CLEARED"
    assert "checked Binance" in snapshot["reason"]


def test_both_filled_resolves_automatically() -> None:
    action, status, _ = classify_pair_orders(
        btc_order=order("FILLED", shares=3.0, usdt=1.2),
        eth_order=order("FILLED", shares=3.0, usdt=1.7),
        tracking_age_seconds=2.0,
        market_ended=False,
    )
    assert action == "RESOLVE"
    assert status == "FILLED_BOTH"


def test_one_sided_fill_becomes_hard_incident() -> None:
    action, status, reason = classify_pair_orders(
        btc_order=order("FILLED", shares=3.0, usdt=1.2),
        eth_order=order("OPEN"),
        tracking_age_seconds=2.0,
        market_ended=False,
    )
    assert action == "INCIDENT"
    assert status == "ONE_SIDED_FILL_LOCK"
    assert "BTC has a fill" in reason


def test_unknown_order_is_tracked_then_locked() -> None:
    early = classify_pair_orders(
        btc_order=None,
        eth_order=order("OPEN"),
        tracking_age_seconds=10.0,
        market_ended=False,
    )
    late = classify_pair_orders(
        btc_order=None,
        eth_order=order("OPEN"),
        tracking_age_seconds=46.0,
        market_ended=False,
    )
    assert early[0] == "TRACK"
    assert late[:2] == ("INCIDENT", "ORDER_SYNC_UNRESOLVED_LOCK")


def test_market_end_with_unresolved_orders_is_incident() -> None:
    action, status, _ = classify_pair_orders(
        btc_order=order("OPEN"),
        eth_order=order("OPEN"),
        tracking_age_seconds=20.0,
        market_ended=True,
    )
    assert action == "INCIDENT"
    assert status == "MARKET_ENDED_UNRESOLVED_LOCK"
