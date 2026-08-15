from __future__ import annotations

import os
import time
from typing import Any

import httpx

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


BINANCE_BSC_RPC_ENV = "PREDICT_TARGET_TAKER_BINANCE_BSC_RPC_URL"
BINANCE_BSC_RPC_DEFAULT = "https://bsc-dataseed.bnbchain.org"
BINANCE_BSC_USDT_ENV = "PREDICT_TARGET_TAKER_BINANCE_USDT_ADDRESS"
BINANCE_BSC_USDT_DEFAULT = "0x55d398326f99059fF775485246999027B3197955"
BINANCE_BSC_USDT_DECIMALS = 18


def _balance_number(value: Any, *, key: str = "") -> float | None:
    number = _finite(value)
    if number is None or number < 0:
        return None
    lowered = key.lower()
    if "wei" in lowered or number >= 1e12:
        return number / WEI
    return number


def _display_balance_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    # Binance's documented payment-option field is a display string. Accept a
    # trailing currency label without treating unrelated text as a balance.
    first = text.split()[0]
    return _balance_number(first)


def extract_binance_available_usdt(payload: Any, *, account_type: str = "SPOT") -> float | None:
    """Parse Binance Prediction CEX payment-option USDT availability.

    The documented response rows contain ``accountType``,
    ``availableBalanceDisplay`` and ``enabled``. This is a CEX payment source
    balance (SPOT/FUNDING), not the selected MPC Prediction Wallet balance.
    Legacy explicitly-labelled USDT shapes remain accepted for compatibility.
    """

    wanted = str(account_type or "SPOT").strip().upper()
    candidates: list[tuple[int, int, float]] = []
    legacy_balance_keys = (
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

        account_type_value = str(node.get("accountType") or "").strip().upper()
        if account_type_value == wanted and node.get("enabled") is not False:
            amount = _display_balance_number(node.get("availableBalanceDisplay"))
            if amount is not None:
                candidates.append((3, 100, amount))

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
            for index, key in enumerate(legacy_balance_keys):
                if key not in node:
                    continue
                amount = _balance_number(node.get(key), key=key)
                if amount is not None:
                    candidates.append((account_rank, len(legacy_balance_keys) - index, amount))

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


def select_configured_binance_prediction_wallet(
    payload: Any, *, wallet_address: str, wallet_id: str
) -> dict[str, Any] | None:
    """Return only the exact configured Prediction Wallet from wallet/list."""

    wanted_address = str(wallet_address or "").strip().lower()
    wanted_id = str(wallet_id or "").strip()
    if not wanted_address or not wanted_id:
        return None

    def walk(node: Any) -> dict[str, Any] | None:
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found is not None:
                    return found
            return None
        if not isinstance(node, dict):
            return None

        address = str(node.get("walletAddress") or "").strip().lower()
        row_id = str(node.get("walletId") or "").strip()
        if address == wanted_address and row_id == wanted_id:
            return node

        for value in node.values():
            if isinstance(value, (dict, list)):
                found = walk(value)
                if found is not None:
                    return found
        return None

    return walk(payload)


def _evm_address_hex(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("0x") or len(text) != 42:
        raise TargetTakerLiveError(f"{label} must be a 20-byte EVM address")
    body = text[2:]
    try:
        int(body, 16)
    except ValueError as exc:
        raise TargetTakerLiveError(f"{label} is not valid hexadecimal") from exc
    return body.lower()


def _erc20_balance_of_calldata(wallet_address: str) -> str:
    address_hex = _evm_address_hex(wallet_address, label="Binance Prediction wallet address")
    return "0x70a08231" + ("0" * 24) + address_hex


def _decode_eth_call_balance(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise TargetTakerLiveError("BSC RPC returned a non-object response")
    if payload.get("error") is not None:
        raise TargetTakerLiveError(f"BSC RPC rejected USDT balance query: {str(payload['error'])[:200]}")
    raw = payload.get("result")
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise TargetTakerLiveError("BSC RPC returned no hexadecimal USDT balance")
    try:
        amount = int(raw, 16)
    except ValueError as exc:
        raise TargetTakerLiveError("BSC RPC returned an invalid hexadecimal USDT balance") from exc
    if amount < 0:
        raise TargetTakerLiveError("BSC RPC returned a negative USDT balance")
    return amount


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

    @staticmethod
    def _binance_prediction_wallet_usdt_balance(wallet_address: str) -> float:
        rpc_url = str(os.environ.get(BINANCE_BSC_RPC_ENV) or BINANCE_BSC_RPC_DEFAULT).strip()
        token_address = str(os.environ.get(BINANCE_BSC_USDT_ENV) or BINANCE_BSC_USDT_DEFAULT).strip()
        _evm_address_hex(token_address, label="Binance BSC USDT token address")
        call_data = _erc20_balance_of_calldata(wallet_address)
        try:
            response = httpx.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [
                        {"to": token_address, "data": call_data},
                        "latest",
                    ],
                },
                timeout=httpx.Timeout(4.0, connect=2.0),
                headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Target-Taker-Balance/1.0"},
            )
        except httpx.RequestError as exc:
            raise TargetTakerLiveError(
                f"BSC RPC USDT balance request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise TargetTakerLiveError(f"BSC RPC USDT balance HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise TargetTakerLiveError("BSC RPC USDT balance response was not JSON") from exc
        raw = _decode_eth_call_balance(payload)
        return raw / float(10**BINANCE_BSC_USDT_DECIMALS)

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
            wallet_address, wallet_id, account_type = self._binance_wallet()
            wallet_payload = client.wallets()
            selected_wallet = select_configured_binance_prediction_wallet(
                wallet_payload,
                wallet_address=wallet_address,
                wallet_id=wallet_id,
            )
            if selected_wallet is None:
                raise TargetTakerLiveError(
                    "Configured Binance Prediction walletAddress/walletId pair was not returned by wallet/list"
                )

            amount = self._binance_prediction_wallet_usdt_balance(wallet_address)
            return {
                "status": "OK",
                "venue": venue,
                "availableUsdt": amount,
                "asOfMs": now_ms,
                "source": "binance_prediction.wallet/list+bsc.eth_call.USDT.balanceOf",
                "fundingSource": "MPC",
                "accountType": account_type,
                "predictionWalletAddress": wallet_address,
                "predictionWalletId": wallet_id,
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
                    else "binance_prediction.wallet/list+bsc.eth_call.USDT.balanceOf"
                ),
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }


__all__ = [
    "BINANCE_BSC_RPC_DEFAULT",
    "BINANCE_BSC_RPC_ENV",
    "BINANCE_BSC_USDT_DEFAULT",
    "BINANCE_BSC_USDT_ENV",
    "TargetTakerLiveConfig",
    "TargetTakerLiveError",
    "TargetTakerLiveExecutor",
    "extract_binance_available_usdt",
    "select_configured_binance_prediction_wallet",
]
