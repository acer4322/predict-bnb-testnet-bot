from __future__ import annotations

import os
from typing import Any

from . import target_taker_live_execution_v4 as _v4
from .target_taker_live_execution_v4 import (
    TargetTakerLiveConfig,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V4TargetTakerLiveExecutor,
    _display_balance_number,
)


# V4 carried an approximate 1.5 USDT MARKET minimum as a local pre-submit
# policy. It is not an execution-safety invariant and can reject venue-valid
# 1.0 USDT orders before Binance gets a chance to validate them. The active V5
# path deliberately disables only that legacy estimate. TargetTakerLiveConfig
# still requires a positive bounded notional, and the venue remains the final
# authority for any actual minimum-order rejection.
_v4.BINANCE_MARKET_MIN_NOTIONAL_USDT = 0.0

VERSION = "TARGET_TAKER_LIVE_EXECUTION_V5_REMOTE_VENUE_MIN_NOTIONAL"
BALANCE_ACCOUNT_TYPE_ENV = "PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE"


def binance_prediction_payment_options(payload: Any) -> list[dict[str, Any]]:
    """Normalize the old 4310/XPAIR Prediction payment-options response."""

    if not isinstance(payload, dict):
        return []
    rows = payload.get("items")
    if not isinstance(rows, list):
        data = payload.get("data")
        rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []

    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_type = str(row.get("accountType") or "").strip()
        if not account_type:
            continue
        output.append(
            {
                "accountType": account_type,
                "enabled": row.get("enabled") is True,
                "availableUsdt": _display_balance_number(row.get("availableBalanceDisplay")),
            }
        )
    return output


def _aggregate_payment_type(options: list[dict[str, Any]], account_type: str) -> dict[str, Any] | None:
    wanted = str(account_type or "").strip().upper()
    matching = [
        row
        for row in options
        if row.get("enabled") is True
        and row.get("availableUsdt") is not None
        and str(row.get("accountType") or "").strip().upper() == wanted
    ]
    if not matching:
        return None
    return {
        "accountType": str(matching[0]["accountType"]),
        "enabled": True,
        "availableUsdt": sum(float(row["availableUsdt"]) for row in matching),
        "rows": len(matching),
    }


def select_prediction_payment_balance(
    options: list[dict[str, Any]], *, preferred_account_type: str | None = None
) -> dict[str, Any] | None:
    enabled_types: list[str] = []
    for row in options:
        if row.get("enabled") is not True or row.get("availableUsdt") is None:
            continue
        account_type = str(row.get("accountType") or "").strip()
        if account_type and account_type.upper() not in {item.upper() for item in enabled_types}:
            enabled_types.append(account_type)
    if not enabled_types:
        return None

    preferred = str(preferred_account_type or "").strip()
    if preferred:
        selected = _aggregate_payment_type(options, preferred)
        if selected is not None:
            return selected

    # Exact old XPAIR/CeDeFi behavior: Prediction funding was found under
    # accountType=CeDeFi while SPOT/FUNDING were zero, and matching enabled rows
    # were summed before the balance check.
    selected = _aggregate_payment_type(options, "CeDeFi")
    if selected is not None:
        return selected

    aggregated = [
        item
        for account_type in enabled_types
        if (item := _aggregate_payment_type(options, account_type)) is not None
    ]
    return max(aggregated, key=lambda row: float(row.get("availableUsdt") or 0.0)) if aggregated else None


class TargetTakerLiveExecutor(_V4TargetTakerLiveExecutor):
    """Hardened V4 execution with venue-authoritative minimum and 4310 balance display."""

    def available_balance_snapshot(self) -> dict[str, Any]:
        if self.config.venue != "binance":
            return super().available_balance_snapshot()

        # Keep V4's exact wallet/list + MPC BSC balance as a separate safety
        # diagnostic. This method does not change _execute_binance preflight.
        mpc = dict(super().available_balance_snapshot())
        try:
            client = self._ensure_binance()
            raw = client.payment_option_balances()
            options = binance_prediction_payment_options(raw)
            preferred = str(os.environ.get(BALANCE_ACCOUNT_TYPE_ENV) or "").strip() or None
            selected = select_prediction_payment_balance(
                options,
                preferred_account_type=preferred,
            )
            if selected is None:
                raise TargetTakerLiveError(
                    "Binance Prediction payment-options returned no enabled numeric balance"
                )

            return {
                **mpc,
                "status": "OK",
                "availableUsdt": float(selected["availableUsdt"]),
                "source": "binance_prediction.payment-options",
                "accountType": str(selected["accountType"]),
                "paymentSourceRows": int(selected.get("rows") or 1),
                "paymentSourceStatus": "OK",
                "paymentOptions": options,
                "mpcWalletAvailableUsdt": mpc.get("availableUsdt"),
                "mpcWalletBalanceStatus": mpc.get("status"),
                "mpcWalletBalanceSource": mpc.get("source"),
                "balanceSemantics": (
                    "Primary display is Binance Prediction payment-options availability; "
                    "MPC wallet on-chain USDT is retained separately for execution safety."
                ),
            }
        except Exception as exc:
            return {
                **mpc,
                "paymentSourceStatus": "UNAVAILABLE",
                "paymentSourceError": f"{type(exc).__name__}: {str(exc)[:300]}",
                "mpcWalletAvailableUsdt": mpc.get("availableUsdt"),
                "mpcWalletBalanceStatus": mpc.get("status"),
                "mpcWalletBalanceSource": mpc.get("source"),
                "balanceSemantics": (
                    "Prediction payment-options unavailable; showing the separate MPC wallet "
                    "on-chain safety diagnostic instead."
                ),
            }


__all__ = [
    "BALANCE_ACCOUNT_TYPE_ENV",
    "TargetTakerLiveConfig",
    "TargetTakerLiveError",
    "TargetTakerLiveExecutor",
    "binance_prediction_payment_options",
    "select_prediction_payment_balance",
]
