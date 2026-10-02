from __future__ import annotations

from decimal import Decimal

import pytest

from predict_bot.xpair_btc_eth_canary_cedefi import (
    build_parser,
    normalize_account_type,
    preflight_wallet,
)


class FakeClient:
    def __init__(self, *, active_orders: int = 0, active_positions: int = 0) -> None:
        self.active_order_count = active_orders
        self.active_position_count = active_positions

    def wallets(self):
        return {"wallets": [{"walletAddress": "0xabc", "walletId": "wallet-1"}]}

    def quota_status(self):
        return {}

    def payment_option_balances(self):
        return {
            "items": [
                {
                    "accountType": "CeDeFi",
                    "availableBalanceDisplay": "81.75",
                    "enabled": True,
                },
                {
                    "accountType": "SPOT",
                    "availableBalanceDisplay": "0.00",
                    "enabled": True,
                },
                {
                    "accountType": "FUNDING",
                    "availableBalanceDisplay": "0.00",
                    "enabled": True,
                },
            ]
        }

    def active_orders(self, wallet_address, limit=100):
        return {
            "orders": [
                {"orderId": f"order-{index}"}
                for index in range(self.active_order_count)
            ]
        }

    def portfolio(self, wallet_address, activeOnly=True):
        return {"activePositionsCount": self.active_position_count}


def test_parser_accepts_cedefi_and_preserves_api_casing() -> None:
    args = build_parser().parse_args(["--account-type", "cedefi"])
    assert args.account_type == "CeDeFi"


def test_cedefi_balance_is_used_without_falling_back_to_spot() -> None:
    wallet_address, wallet_id, available = preflight_wallet(
        FakeClient(), account_type="CeDeFi", required_balance=Decimal("2.10")
    )
    assert wallet_address == "0xabc"
    assert wallet_id == "wallet-1"
    assert available == Decimal("81.75")


def test_spot_zero_is_rejected_even_when_cedefi_has_funds() -> None:
    with pytest.raises(RuntimeError, match="for SPOT 0.00000000"):
        preflight_wallet(
            FakeClient(), account_type="SPOT", required_balance=Decimal("2.10")
        )


def test_quote_only_can_preflight_with_existing_position(capsys) -> None:
    _, _, available = preflight_wallet(
        FakeClient(active_positions=1),
        account_type="CeDeFi",
        required_balance=Decimal("2.10"),
        allow_existing_exposure=True,
    )
    assert available == Decimal("81.75")
    assert "quote-only will not place orders" in capsys.readouterr().out


def test_quote_only_can_preflight_with_existing_order(capsys) -> None:
    preflight_wallet(
        FakeClient(active_orders=2),
        account_type="CeDeFi",
        required_balance=Decimal("2.10"),
        allow_existing_exposure=True,
    )
    assert "active_orders=2" in capsys.readouterr().out


def test_live_preflight_still_blocks_existing_position() -> None:
    with pytest.raises(RuntimeError, match="existing active Prediction positions"):
        preflight_wallet(
            FakeClient(active_positions=1),
            account_type="CeDeFi",
            required_balance=Decimal("2.10"),
        )


def test_live_preflight_still_blocks_existing_order() -> None:
    with pytest.raises(RuntimeError, match="existing active Prediction orders"):
        preflight_wallet(
            FakeClient(active_orders=1),
            account_type="CeDeFi",
            required_balance=Decimal("2.10"),
        )


def test_normalize_rejects_unknown_account_type() -> None:
    with pytest.raises(Exception):
        normalize_account_type("MARGIN")
