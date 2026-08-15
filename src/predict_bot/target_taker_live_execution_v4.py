from __future__ import annotations

import os
import time
from typing import Any

from .target_taker_live_execution_v3 import (
    BINANCE_ACCOUNT_TYPE_ENV,
    WEI,
    TargetTakerLiveConfig,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V3TargetTakerLiveExecutor,
    _data,
    _finite,
    _market_id,
)


def _balance_number(value: Any, *, key: str = "") -> float | None:
    number = _finite(value)
    if number is None or number < 0:
        return None
    lowered = key.lower()
    if "wei" in lowered or number >= 1e12:
        return number / WEI
    return number


def extract_binance_available_usdt(payload: Any, *, account_type: str = "SPOT") -> float | None:
    """Best-effort parser for Binance Prediction payment-option balances.

    Binance has changed the envelope shape of this private endpoint before. We
    deliberately accept only rows that are explicitly USDT (or a direct USDT
    key), prefer the configured SPOT/FUNDING account, and never infer a balance
    from unrelated numeric fields.
    """

    wanted = str(account_type or "SPOT").strip().upper()
    candidates: list[tuple[int, int, float]] = []
    balance_keys = (
        "availableBalance",
        "available",
        "free",
        "balance",
        "amount",
        "availableAmount",
        "balanceWei",
        "amountWei",
    )

    def walk(node: Any, *, parent_key: str = "") -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, parent_key=parent_key)
            return
        if not isinstance(node, dict):
            return

        labels = {
            str(node.get(key) or "").strip().upper()
            for key in ("asset", "coin", "currency", "symbol", "token", "tokenSymbol")
            if node.get(key) is not None
        }
        account_labels = {
            str(node.get(key) or "").strip().upper()
            for key in ("accountType", "account", "source", "paymentOption", "fundingAccount")
            if node.get(key) is not None
        }
        is_usdt = "USDT" in labels or parent_key.upper() == "USDT"
        if is_usdt:
            account_rank = 2 if wanted in account_labels else 1 if not account_labels else 0
            for index, key in enumerate(balance_keys):
                if key not in node:
                    continue
                amount = _balance_number(node.get(key), key=key)
                if amount is not None:
                    candidates.append((account_rank, len(balance_keys) - index, amount))

        for key, value in node.items():
            if str(key).upper() == "USDT":
                direct = _balance_number(value, key=str(key))
                if direct is not None:
                    candidates.append((1, 1, direct))
            if isinstance(value, (dict, list)):
                walk(value, parent_key=str(key))

    walk(payload)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return float(candidates[0][2])


class TargetTakerLiveExecutor(_V3TargetTakerLiveExecutor):
    """V3 executor with exact Predict.fun lookup and dashboard balance reads."""

    @staticmethod
    def _predict_market(client: Any, market_id: int) -> dict[str, Any]:
        expected = int(market_id)
        if expected <= 0:
            raise TargetTakerLiveError("Predict.fun market ID must be positive")
        payload = client._request("GET", f"/v1/markets/{expected}")
        if not isinstance(payload, dict):
            raise TargetTakerLiveError(
                f"Predict.fun exact market lookup returned invalid payload for {expected}"
            )
        market = _data(payload)
        if not isinstance(market, dict) or _market_id(market) != expected:
            returned = _market_id(market) if isinstance(market, dict) else 0
            raise TargetTakerLiveError(
                f"Predict.fun exact market lookup mismatch: expected {expected}, returned {returned}"
            )
        return market

    def available_balance_snapshot(self) -> dict[str, Any]:
        """Read available USDT for the selected live venue without placing orders."""

        now_ms = int(time.time() * 1000)
        venue = self.config.venue
        try:
            if venue == "predictfun":
                _, builder = self._ensure_predict()
                predict_account = str(os.environ.get("PREDICT_FUN_ACCOUNT_ADDRESS") or "").strip()
                if predict_account:
                    raw = builder.balance_of("USDT", predict_account)
                else:
                    raw = builder.balance_of("USDT")
                amount = _balance_number(raw, key="balanceWei")
                if amount is None:
                    raise TargetTakerLiveError("Predict.fun USDT balance was not numeric")
                return {
                    "status": "OK",
                    "venue": venue,
                    "availableUsdt": amount,
                    "asOfMs": now_ms,
                    "source": "predict_sdk.balance_of",
                }

            client = self._ensure_binance()
            account_type = str(os.environ.get(BINANCE_ACCOUNT_TYPE_ENV) or "SPOT").strip().upper()
            if account_type not in {"SPOT", "FUNDING"}:
                raise TargetTakerLiveError(
                    f"{BINANCE_ACCOUNT_TYPE_ENV} must be SPOT or FUNDING"
                )
            payload = client.payment_option_balances()
            amount = extract_binance_available_usdt(payload, account_type=account_type)
            if amount is None:
                raise TargetTakerLiveError(
                    f"Binance Prediction payment-options response had no {account_type} USDT balance"
                )
            return {
                "status": "OK",
                "venue": venue,
                "availableUsdt": amount,
                "asOfMs": now_ms,
                "source": "binance_prediction.payment-options",
                "accountType": account_type,
            }
        except Exception as exc:
            return {
                "status": "UNAVAILABLE",
                "venue": venue,
                "availableUsdt": None,
                "asOfMs": now_ms,
                "source": (
                    "predict_sdk.balance_of"
                    if venue == "predictfun"
                    else "binance_prediction.payment-options"
                ),
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }


__all__ = [
    "TargetTakerLiveConfig",
    "TargetTakerLiveError",
    "TargetTakerLiveExecutor",
    "extract_binance_available_usdt",
]
