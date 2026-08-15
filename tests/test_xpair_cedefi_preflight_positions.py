from __future__ import annotations

from decimal import Decimal

import pytest

from predict_bot import xpair_canary_autopilot_server as base
from predict_bot.xpair_btc_eth_canary_cedefi import preflight_wallet
from predict_bot.xpair_canary_autopilot_server_v10 import (
    strict_live_preflight_allow_settled_positions,
)


class FakeClient:
    def __init__(
        self,
        *,
        active_orders: int = 0,
        active_positions: int = 0,
        available: str = "20.00",
    ) -> None:
        self.active_order_count = active_orders
        self.active_position_count = active_positions
        self.available = available

    def wallets(self):
        return {
            "wallets": [
                {"walletAddress": "wallet-address", "walletId": "wallet-id"}
            ]
        }

    def quota_status(self):
        return {}

    def payment_option_balances(self):
        return {
            "items": [
                {
                    "enabled": True,
                    "accountType": "CeDeFi",
                    "availableBalanceDisplay": self.available,
                }
            ]
        }

    def active_orders(self, wallet_address: str, limit: int = 100):
        return {
            "orders": [
                {"orderId": f"order-{index}"}
                for index in range(self.active_order_count)
            ]
        }

    def portfolio(self, wallet_address: str, activeOnly: bool = True):
        return {"activePositionsCount": self.active_position_count}


def test_live_preflight_allows_filled_positions_waiting_for_settlement() -> None:
    client = FakeClient(active_positions=3, available="10.00")
    wallet_address, wallet_id, available = preflight_wallet(
        client,
        account_type="CeDeFi",
        required_balance=Decimal("3.10"),
        allow_existing_positions=True,
        allow_existing_orders=False,
    )
    assert wallet_address == "wallet-address"
    assert wallet_id == "wallet-id"
    assert available == Decimal("10.00")


def test_live_preflight_still_blocks_working_orders() -> None:
    client = FakeClient(active_orders=1, active_positions=2)
    with pytest.raises(RuntimeError, match="active Prediction orders"):
        preflight_wallet(
            client,
            account_type="CeDeFi",
            required_balance=Decimal("3.10"),
            allow_existing_positions=True,
            allow_existing_orders=False,
        )


def test_live_preflight_still_requires_available_balance() -> None:
    client = FakeClient(active_positions=2, available="2.00")
    with pytest.raises(RuntimeError, match="below required"):
        preflight_wallet(
            client,
            account_type="CeDeFi",
            required_balance=Decimal("3.10"),
            allow_existing_positions=True,
            allow_existing_orders=False,
        )


def test_v10_live_wrapper_allows_positions_but_blocks_orders() -> None:
    config = base.MonitorConfig(
        pair_budget_usdt=Decimal("3.00"),
        balance_buffer_usdt=Decimal("0.10"),
    )
    allowed = FakeClient(active_positions=1, available="10.00")
    assert strict_live_preflight_allow_settled_positions(allowed, config)[2] == Decimal(
        "10.00"
    )

    blocked = FakeClient(active_orders=1, active_positions=1, available="10.00")
    with pytest.raises(RuntimeError, match="active Prediction orders"):
        strict_live_preflight_allow_settled_positions(blocked, config)
