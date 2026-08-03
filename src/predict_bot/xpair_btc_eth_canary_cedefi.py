from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Any

from . import xpair_btc_eth_canary as canary

ACCOUNT_TYPES = ("CeDeFi", "SPOT", "FUNDING")


def normalize_account_type(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized == "CEDEFI":
        return "CeDeFi"
    if normalized in {"SPOT", "FUNDING"}:
        return normalized
    raise argparse.ArgumentTypeError(
        "account type must be CeDeFi, SPOT, or FUNDING"
    )


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def preflight_wallet(
    client: canary.BinancePredictionTradingClient,
    *,
    account_type: str,
    required_balance: Decimal,
    allow_existing_exposure: bool = False,
) -> tuple[str, str, Decimal]:
    wallets = client.wallets().get("wallets") or []
    if len(wallets) != 1:
        raise RuntimeError(f"expected one Prediction wallet, got {len(wallets)}")
    wallet_address = str(wallets[0].get("walletAddress") or "")
    wallet_id = str(wallets[0].get("walletId") or "")
    if not wallet_address or not wallet_id:
        raise RuntimeError("Prediction wallet metadata is incomplete")
    client.quota_status()

    requested_type = normalize_account_type(account_type)
    requested_key = requested_type.upper()
    balance_payload = client.payment_option_balances()
    enabled = [
        item
        for item in (balance_payload.get("items") or [])
        if item.get("enabled") is True
    ]
    matching = [
        item
        for item in enabled
        if str(item.get("accountType") or "").strip().upper() == requested_key
    ]
    if not matching:
        available_types = ", ".join(
            str(item.get("accountType") or "UNKNOWN") for item in enabled
        ) or "none"
        raise RuntimeError(
            f"Prediction payment account {requested_type} is unavailable; "
            f"enabled account types: {available_types}"
        )

    available = sum(
        (
            _decimal(item.get("availableBalanceDisplay")) or Decimal("0")
            for item in matching
        ),
        Decimal("0"),
    )
    if available < required_balance:
        raise RuntimeError(
            f"enabled Prediction balance for {requested_type} "
            f"{available:.8f} is below required {required_balance:.8f} USDT"
        )

    active_orders = client.active_orders(wallet_address, limit=100).get("orders") or []
    portfolio = client.portfolio(wallet_address, activeOnly=True)
    active_position_count = int(portfolio.get("activePositionsCount") or 0)
    active_order_count = len(active_orders) if isinstance(active_orders, list) else 1

    if active_order_count or active_position_count:
        if allow_existing_exposure:
            print(
                "PREFLIGHT_WARNING existing Prediction exposure detected; "
                "quote-only will not place orders: "
                f"active_orders={active_order_count} "
                f"active_positions={active_position_count}"
            )
        else:
            if active_order_count:
                raise RuntimeError(
                    "existing active Prediction orders detected; "
                    "stop the other live executor"
                )
            raise RuntimeError(
                "existing active Prediction positions detected; wait for settlement"
            )
    return wallet_address, wallet_id, available


def build_parser() -> argparse.ArgumentParser:
    parser = canary.build_parser()
    for action in parser._actions:
        if action.dest == "account_type":
            action.type = normalize_account_type
            action.choices = ACCOUNT_TYPES
            action.default = "CeDeFi"
            break
    else:
        raise RuntimeError("canary parser is missing --account-type")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    allow_existing_exposure = args.mode == "quote-only"

    def mode_preflight(
        client: canary.BinancePredictionTradingClient,
        *,
        account_type: str,
        required_balance: Decimal,
    ) -> tuple[str, str, Decimal]:
        return preflight_wallet(
            client,
            account_type=account_type,
            required_balance=required_balance,
            allow_existing_exposure=allow_existing_exposure,
        )

    canary.preflight_wallet = mode_preflight
    return canary.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
