from __future__ import annotations

from decimal import Decimal

import pytest

from predict_bot.xpair_btc_eth_canary_cedefi import (
    build_parser,
    normalize_account_type,
    preflight_wallet,
)


class FakeClient:
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
        return {"orders": []}

    def portfolio(self, wallet_address, activeOnly=True):
        return {"activePositionsCount": 0}


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


def test_normalize_rejects_unknown_account_type() -> None:
    with pytest.raises(Exception):
        normalize_account_type("MARGIN")
