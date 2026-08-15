from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import httpx

from .binance_exact_market import find_exact_market_summary, live_cache_from_summary
from .core import ApiHttpError, ApiTransportError, binance_top_of_book
from .predict_wallet_target_taker_public_side_strategy_v1 import MIN_SECONDS_LEFT
from .target_taker_live_execution_v3 import (
    BINANCE_ACCOUNT_TYPE_ENV,
    BINANCE_SYMBOL_ENV,
    MAX_ASK,
    WEI,
    TargetTakerLiveAmbiguousError,
    TargetTakerLiveConfig,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V3TargetTakerLiveExecutor,
    _data,
    _finite,
    _first_number,
    _first_text,
    _market_id,
)


BINANCE_BSC_RPC_ENV = "PREDICT_TARGET_TAKER_BINANCE_BSC_RPC_URL"
BINANCE_BSC_RPC_DEFAULT = "https://bsc-dataseed.bnbchain.org"
BINANCE_BSC_USDT_ENV = "PREDICT_TARGET_TAKER_BINANCE_USDT_ADDRESS"
BINANCE_BSC_USDT_DEFAULT = "0x55d398326f99059fF775485246999027B3197955"
BINANCE_BSC_USDT_DECIMALS = 18

# Binance's current W3W Prediction API documents MARKET orders as requiring
# approximately 1.5 USDT minimum input (liquidity can require more) and
# slippageBps in the inclusive range 1..10000. Never silently increase stake.
BINANCE_MARKET_MIN_NOTIONAL_USDT = 1.5
BINANCE_LIVE_SLIPPAGE_BPS = 1
BINANCE_RECONCILE_DELAYS_SECONDS = (0.20, 0.80)
BINANCE_FILLED_STATUSES = {"FILLED", "PARTIALLY_FILLED"}
BINANCE_NO_FILL_TERMINAL_STATUSES = {"FAILED", "EXPIRED", "CANCELLED"}
BINANCE_PENDING_STATUSES = {"PENDING", "SUBMITTED"}


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


def _binance_order_rows(payload: Any) -> list[dict[str, Any]]:
    data = _data(payload) if isinstance(payload, dict) else {}
    rows = data.get("orders") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _binance_order_by_id(payload: Any, order_id: str) -> dict[str, Any] | None:
    wanted = str(order_id or "").strip()
    if not wanted:
        return None
    for row in _binance_order_rows(payload):
        if str(row.get("orderId") or "").strip() == wanted:
            return row
    return None


class TargetTakerLiveExecutor(_V3TargetTakerLiveExecutor):
    """V3 executor with exact lookups, MPC balance reads, and Binance fill reconciliation."""

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

    def _validate_binance_prediction_wallet(
        self, client: Any, *, wallet_address: str, wallet_id: str
    ) -> dict[str, Any]:
        selected = select_configured_binance_prediction_wallet(
            client.wallets(),
            wallet_address=wallet_address,
            wallet_id=wallet_id,
        )
        if selected is None:
            raise TargetTakerLiveError(
                "Configured Binance Prediction walletAddress/walletId pair was not returned by wallet/list"
            )
        return selected

    @staticmethod
    def _binance_market_is_open(summary: dict[str, Any]) -> bool:
        selected = summary.get("_selectedMarket")
        market = selected.get("market") if isinstance(selected, dict) else None
        return (
            isinstance(market, dict)
            and str(market.get("tradingStatus") or "").strip().upper() == "OPEN"
        )

    def _reconcile_binance_order(
        self,
        client: Any,
        *,
        wallet_address: str,
        order_id: str,
    ) -> dict[str, Any] | None:
        last: dict[str, Any] | None = None
        for delay in BINANCE_RECONCILE_DELAYS_SECONDS:
            if delay > 0:
                time.sleep(delay)
            history = client.order_history(wallet_address, limit=100)
            row = _binance_order_by_id(history, order_id)
            if row is None:
                continue
            last = row
            status = str(row.get("status") or "").strip().upper()
            if status in BINANCE_FILLED_STATUSES | BINANCE_NO_FILL_TERMINAL_STATUSES:
                return row
        return last

    def _execute_binance(
        self,
        *,
        source_market_id: int,
        side: str,
        signal_ask: float,
        snapshot: dict[str, Any],
        signal_id: str,
    ) -> dict[str, Any]:
        # Current Binance W3W Prediction docs state MARKET orders require
        # approximately 1.5 USDT. Do not silently increase the user's stake.
        if self.config.notional_usdt < BINANCE_MARKET_MIN_NOTIONAL_USDT:
            raise TargetTakerLiveError(
                "Binance Prediction MARKET orders currently require approximately "
                f"{BINANCE_MARKET_MIN_NOTIONAL_USDT:.1f} USDT minimum; configured notional "
                f"is {self.config.notional_usdt:.8f} USDT"
            )

        client = self._ensure_binance()
        wallet_address, wallet_id, account_type = self._binance_wallet()
        self._validate_binance_prediction_wallet(
            client,
            wallet_address=wallet_address,
            wallet_id=wallet_id,
        )

        wallet_balance = self._binance_prediction_wallet_usdt_balance(wallet_address)
        if wallet_balance + 1e-12 < self.config.notional_usdt:
            raise TargetTakerLiveError(
                f"Binance Prediction MPC wallet has {wallet_balance:.8f} USDT, "
                f"below configured {self.config.notional_usdt:.8f} USDT notional"
            )

        symbol = str(os.environ.get(BINANCE_SYMBOL_ENV, "BTCUSDT")).strip().upper()
        expected_start_ms = int(
            _finite(snapshot.get("bucket_start_sec") or snapshot.get("bucketStartSec")) or 0
        ) * 1000
        expected_end_ms = int(
            _finite(snapshot.get("window_end_ms") or snapshot.get("windowEndMs")) or 0
        )
        if expected_start_ms <= 0 or expected_end_ms <= expected_start_ms:
            raise TargetTakerLiveError("Target Taker snapshot has no valid five-minute window")

        summary = find_exact_market_summary(
            client,
            symbol=symbol,
            target_start_ms=expected_start_ms,
            max_pages=2,
        )
        if not isinstance(summary, dict):
            raise TargetTakerLiveError(
                f"Binance exact {symbol} prediction market was not found for the EBM signal window"
            )
        if not self._binance_market_is_open(summary):
            raise TargetTakerLiveError("Binance exact prediction market is not OPEN")

        cache = live_cache_from_summary(summary)
        if not isinstance(cache, dict):
            raise TargetTakerLiveError("Binance exact market metadata is incomplete")
        if abs(int(cache["end_ms"]) - expected_end_ms) > 2_000:
            raise TargetTakerLiveError("Binance market end does not match the EBM signal window")

        server_now_ms = int(client.server_timestamp_ms())
        if not int(cache["start_ms"]) <= server_now_ms < int(cache["end_ms"]):
            raise TargetTakerLiveError("Binance exact prediction market is outside its live window")
        seconds_left = (int(cache["end_ms"]) - server_now_ms) / 1000.0
        if seconds_left < MIN_SECONDS_LEFT:
            raise TargetTakerLiveError(
                f"Binance prediction market has only {seconds_left:.3f}s left; "
                f"strategy requires >= {MIN_SECONDS_LEFT:.3f}s"
            )

        venue_market_id = int(cache["market_id"])
        topic_id = int(cache["topic_id"])
        up_token = str(cache["up_token_id"])
        down_token = str(cache["down_token_id"])
        token_id = up_token if side == "UP" else down_token

        up_book = client.orderbook(venue_market_id, up_token)
        down_book = client.orderbook(venue_market_id, down_token)
        top = binance_top_of_book(up_book, down_book, server_now_ms)
        latest_ask = top.up_ask if side == "UP" else top.down_ask
        if latest_ask is None or not 0 < latest_ask <= MAX_ASK:
            raise TargetTakerLiveError("Binance latest ask is unavailable or outside strategy cap")
        if top.book_age_ms is None or top.book_age_ms > 2_000:
            raise TargetTakerLiveError(
                "Binance order book has no valid fresh timestamp"
                if top.book_age_ms is None
                else f"Binance order book is stale: {top.book_age_ms:.0f}ms"
            )
        maximum_price = min(MAX_ASK, signal_ask + self.config.max_price_drift)
        if latest_ask > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Binance ask moved from {signal_ask:.6f} to {latest_ask:.6f}, "
                f"cap={maximum_price:.6f}"
            )

        amount_in_wei = int(
            (Decimal(str(self.config.notional_usdt)) * Decimal(WEI)).to_integral_value()
        )
        quote_started = time.monotonic()
        quote = client.get_quote(
            wallet_address=wallet_address,
            token_id=token_id,
            amount_in_wei=str(amount_in_wei),
            price_limit=None,
            slippage_bps=BINANCE_LIVE_SLIPPAGE_BPS,
            fee_rate_bps=int(cache["fee_rate_bps"]),
            funding_source="MPC",
            side="BUY",
            order_type="MARKET",
        )
        quote_id = str(quote.get("quoteId") or "").strip()
        quote_amount_in = _finite(quote.get("amountIn"))
        quote_amount_out = _finite(quote.get("amountOut"))
        quote_price = _finite(quote.get("averagePrice"))
        if not quote_id:
            raise TargetTakerLiveError("Binance quote response contained no quoteId")
        if quote_amount_in is None or quote_amount_in <= 0 or quote_amount_in > amount_in_wei:
            raise TargetTakerLiveError(
                "Binance quote exceeds or invalidates the configured notional hard cap"
            )
        if quote_amount_out is None or quote_amount_out <= 0:
            raise TargetTakerLiveError("Binance quote returned no positive outcome amount")
        if quote_price is None or quote_price > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Binance quote average price {quote_price} exceeds execution cap {maximum_price:.6f}"
            )
        if quote.get("orderType") is not None and str(quote.get("orderType")).upper() != "MARKET":
            raise TargetTakerLiveError("Binance quote changed the requested order type")
        if quote.get("side") is not None and str(quote.get("side")).upper() != "BUY":
            raise TargetTakerLiveError("Binance quote changed the requested order side")
        if quote.get("tokenId") is not None and str(quote.get("tokenId")) != token_id:
            raise TargetTakerLiveError("Binance quote token does not match selected EBM side")
        if (
            quote.get("walletAddress") is not None
            and str(quote.get("walletAddress")).lower() != wallet_address.lower()
        ):
            raise TargetTakerLiveError("Binance quote wallet does not match configured Prediction wallet")
        if quote.get("chainId") is not None and str(quote.get("chainId")) != "56":
            raise TargetTakerLiveError("Binance quote changed BSC chainId")
        if quote.get("slippageBps") is not None:
            quote_slippage = int(_finite(quote.get("slippageBps")) or 0)
            if quote_slippage != BINANCE_LIVE_SLIPPAGE_BPS:
                raise TargetTakerLiveError(
                    "Binance quote did not preserve requested slippageBps"
                )
        expiry = int(_finite(quote.get("expireAt")) or 0)
        if expiry and expiry <= client.server_timestamp_ms() + 250:
            raise TargetTakerLiveError("Binance quote expired before placement")

        try:
            placed = client.place_market_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                slippage_bps=BINANCE_LIVE_SLIPPAGE_BPS,
                account_type=account_type,
                funding_source="MPC",
            )
        except Exception as exc:
            if isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            ):
                raise TargetTakerLiveAmbiguousError(str(exc)) from exc
            raise

        order_id = _first_text(placed, "orderId", "id")
        if not order_id:
            raise TargetTakerLiveAmbiguousError(
                "Binance placement response contained no orderId"
            )

        quote_rtt_ms = (time.monotonic() - quote_started) * 1000.0
        try:
            reconciled = self._reconcile_binance_order(
                client,
                wallet_address=wallet_address,
                order_id=order_id,
            )
        except Exception as exc:
            return {
                "status": "AMBIGUOUS",
                "venue": "binance",
                "sourceMarketId": source_market_id,
                "venueMarketId": venue_market_id,
                "venueTopicId": topic_id,
                "side": side,
                "signalId": signal_id,
                "signalAsk": signal_ask,
                "executionPrice": quote_price,
                "vendorOrderId": order_id,
                "exchangeStatus": "RECONCILIATION_FAILED",
                "quoteRttMs": quote_rtt_ms,
                "fillOrKill": True,
                "error": (
                    "Binance returned orderId but order-history reconciliation failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                ),
            }

        if reconciled is None:
            return {
                "status": "AMBIGUOUS",
                "venue": "binance",
                "sourceMarketId": source_market_id,
                "venueMarketId": venue_market_id,
                "venueTopicId": topic_id,
                "side": side,
                "signalId": signal_id,
                "signalAsk": signal_ask,
                "executionPrice": quote_price,
                "vendorOrderId": order_id,
                "exchangeStatus": "NOT_YET_VISIBLE",
                "quoteRttMs": quote_rtt_ms,
                "fillOrKill": True,
                "error": "Binance returned orderId but it was not visible in bounded order-history reconciliation",
            }

        exchange_status = str(reconciled.get("status") or "").strip().upper()
        filled_usdt = _first_number(reconciled, "filledUsdtAmount")
        filled_shares = _first_number(reconciled, "filledShareQty")
        if (
            filled_usdt is not None
            and filled_usdt > 0
            and filled_shares is not None
            and filled_shares > 0
        ):
            execution_price = _first_number(reconciled, "price") or quote_price
            return {
                "status": "SUBMITTED",
                "venue": "binance",
                "sourceMarketId": source_market_id,
                "venueMarketId": venue_market_id,
                "venueTopicId": topic_id,
                "side": side,
                "signalId": signal_id,
                "signalAsk": signal_ask,
                "executionPrice": execution_price,
                "shares": filled_shares,
                "submittedUsdt": filled_usdt,
                "vendorOrderId": order_id,
                "vendorOrderHash": _first_text(reconciled, "vendorOrderId"),
                "exchangeStatus": exchange_status or "FILLED_AMOUNT_REPORTED",
                "quoteRttMs": quote_rtt_ms,
                "fillOrKill": True,
            }

        if exchange_status in BINANCE_NO_FILL_TERMINAL_STATUSES:
            return {
                "status": "REJECTED",
                "venue": "binance",
                "sourceMarketId": source_market_id,
                "venueMarketId": venue_market_id,
                "venueTopicId": topic_id,
                "side": side,
                "signalId": signal_id,
                "signalAsk": signal_ask,
                "executionPrice": quote_price,
                "vendorOrderId": order_id,
                "vendorOrderHash": _first_text(reconciled, "vendorOrderId"),
                "exchangeStatus": exchange_status,
                "quoteRttMs": quote_rtt_ms,
                "fillOrKill": True,
                "error": f"Binance FOK order reached terminal no-fill status {exchange_status}",
            }

        return {
            "status": "AMBIGUOUS",
            "venue": "binance",
            "sourceMarketId": source_market_id,
            "venueMarketId": venue_market_id,
            "venueTopicId": topic_id,
            "side": side,
            "signalId": signal_id,
            "signalAsk": signal_ask,
            "executionPrice": quote_price,
            "vendorOrderId": order_id,
            "vendorOrderHash": _first_text(reconciled, "vendorOrderId"),
            "exchangeStatus": exchange_status or "UNKNOWN",
            "quoteRttMs": quote_rtt_ms,
            "fillOrKill": True,
            "error": (
                "Binance order exists but no confirmed filled amount was available after bounded "
                f"reconciliation (status={exchange_status or 'UNKNOWN'}); never retry automatically"
            ),
        }

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
            self._validate_binance_prediction_wallet(
                client,
                wallet_address=wallet_address,
                wallet_id=wallet_id,
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
    "BINANCE_LIVE_SLIPPAGE_BPS",
    "BINANCE_MARKET_MIN_NOTIONAL_USDT",
    "BINANCE_RECONCILE_DELAYS_SECONDS",
    "TargetTakerLiveConfig",
    "TargetTakerLiveError",
    "TargetTakerLiveExecutor",
    "extract_binance_available_usdt",
    "select_configured_binance_prediction_wallet",
]
