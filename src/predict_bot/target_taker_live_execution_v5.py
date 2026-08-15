from __future__ import annotations

import os
from typing import Any

from .target_taker_live_execution_v4 import (
    TargetTakerLiveConfig,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V4TargetTakerLiveExecutor,
    _display_balance_number,
)


VERSION = "TARGET_TAKER_LIVE_EXECUTION_V5_4310_BALANCE_DIAGNOSTICS"
BALANCE_ACCOUNT_TYPE_ENV = "PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE"


def binance_prediction_payment_options(payload: Any) -> list[dict[str, Any]]:
    """Normalize the old 4310/XPAIR Prediction payment-options response.

    The proven legacy endpoint returns rows such as accountType=CeDeFi with an
    availableBalanceDisplay string. This helper is deliberately display/read
    only; live order funding semantics remain in the hardened V4 executor.
    """

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
        available = _display_balance_number(row.get("availableBalanceDisplay"))
        output.append(
            {
                "accountType": account_type,
                "enabled": row.get("enabled") is True,
                "availableUsdt": available,
            }
        )
    return output


def select_prediction_payment_balance(
    options: list[dict[str, Any]], *, preferred_account_type: str | None = None
) -> dict[str, Any] | None:
    enabled = [
        row
        for row in options
        if row.get("enabled") is True and row.get("availableUsdt") is not None
    ]
    if not enabled:
        return None

    preferred = str(preferred_account_type or "").strip().upper()
    if preferred:
        for row in enabled:
            if str(row.get("accountType") or "").strip().upper() == preferred:
                return row

    # The old 4310/XPAIR live page discovered the funded Prediction source as
    # CeDeFi while SPOT/FUNDING were zero. Preserve that proven preference, then
    # fall back to the largest enabled source rather than silently summing types.
    for row in enabled:
        if str(row.get("accountType") or "").strip().upper() == "CEDEFI":
            return row
    return max(enabled, key=lambda row: float(row.get("availableUsdt") or 0.0))


class TargetTakerLiveExecutor(_V4TargetTakerLiveExecutor):
    """Hardened V4 execution plus the proven 4310 Prediction balance display."""

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
            # Do not hide a valid MPC wallet diagnostic merely because the old
            # Prediction payment-source endpoint is temporarily unavailable.
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
