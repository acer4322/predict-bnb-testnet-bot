from __future__ import annotations

import pytest

from predict_bot import target_taker_live_execution_v4 as live
from predict_bot import target_taker_live_execution_v3 as live_v3


class _FakePredictClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def _request(self, method: str, path: str) -> dict:
        self.calls.append((method, path))
        return self.payload


def test_exact_predict_market_lookup_uses_signal_market_id() -> None:
    client = _FakePredictClient({"success": True, "data": {"id": 6827001}})
    market = live.TargetTakerLiveExecutor._predict_market(client, 6827001)
    assert market["id"] == 6827001
    assert client.calls == [("GET", "/v1/markets/6827001")]


def test_exact_predict_market_lookup_rejects_mismatch() -> None:
    client = _FakePredictClient({"success": True, "data": {"id": 6827002}})
    with pytest.raises(live.TargetTakerLiveError, match="expected 6827001, returned 6827002"):
        live.TargetTakerLiveExecutor._predict_market(client, 6827001)


def test_payment_option_parser_accepts_documented_schema() -> None:
    payload = {
        "items": [
            {
                "accountType": "SPOT",
                "availableBalanceDisplay": "12.345 USDT",
                "enabled": True,
            },
            {
                "accountType": "FUNDING",
                "availableBalanceDisplay": "7.5",
                "enabled": True,
            },
        ]
    }
    assert live.extract_binance_available_usdt(payload, account_type="SPOT") == pytest.approx(12.345)
    assert live.extract_binance_available_usdt(payload, account_type="FUNDING") == pytest.approx(7.5)


def test_prediction_wallet_selection_requires_exact_address_and_id() -> None:
    payload = {
        "wallets": [
            {"walletAddress": "0x1111111111111111111111111111111111111111", "walletId": "wallet-a"},
            {"walletAddress": "0x2222222222222222222222222222222222222222", "walletId": "wallet-b"},
        ]
    }
    selected = live.select_configured_binance_prediction_wallet(
        payload,
        wallet_address="0x2222222222222222222222222222222222222222",
        wallet_id="wallet-b",
    )
    assert selected is not None
    assert selected["walletId"] == "wallet-b"
    assert live.select_configured_binance_prediction_wallet(
        payload,
        wallet_address="0x2222222222222222222222222222222222222222",
        wallet_id="wallet-a",
    ) is None


def test_erc20_balance_calldata_targets_configured_wallet() -> None:
    wallet = "0x1234567890abcdef1234567890abcdef12345678"
    data = live._erc20_balance_of_calldata(wallet)
    assert data.startswith("0x70a08231")
    assert data.endswith(wallet[2:].lower())
    assert len(data) == 2 + 8 + 64


class _FakeBinanceClient:
    def __init__(self, wallet_address: str, wallet_id: str) -> None:
        self.wallet_address = wallet_address
        self.wallet_id = wallet_id
        self.wallet_calls = 0
        self.payment_calls = 0

    def wallets(self) -> dict:
        self.wallet_calls += 1
        return {
            "wallets": [
                {
                    "walletAddress": self.wallet_address,
                    "walletId": self.wallet_id,
                    "registeredTime": 1,
                }
            ]
        }

    def payment_option_balances(self) -> dict:
        self.payment_calls += 1
        raise AssertionError("MPC wallet balance must not use payment-options")


class _FakeRpcResponse:
    status_code = 200

    def __init__(self, amount_wei: int) -> None:
        self.amount_wei = amount_wei

    def json(self) -> dict:
        return {"jsonrpc": "2.0", "id": 1, "result": hex(self.amount_wei)}


def test_binance_balance_reads_exact_prediction_mpc_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wallet_address = "0x1234567890abcdef1234567890abcdef12345678"
    wallet_id = "prediction-wallet-1"
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ADDRESS_ENV, wallet_address)
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ID_ENV, wallet_id)
    monkeypatch.setenv(live_v3.BINANCE_ACCOUNT_TYPE_ENV, "SPOT")

    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="paper", venue="binance", notional_usdt=1.0)
    )
    fake_client = _FakeBinanceClient(wallet_address, wallet_id)
    monkeypatch.setattr(executor, "_ensure_binance", lambda: fake_client)

    seen: dict = {}

    def fake_post(url: str, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs["json"]
        return _FakeRpcResponse(12_500_000_000_000_000_000)

    monkeypatch.setattr(live.httpx, "post", fake_post)
    snapshot = executor.available_balance_snapshot()

    assert snapshot["status"] == "OK"
    assert snapshot["availableUsdt"] == pytest.approx(12.5)
    assert snapshot["fundingSource"] == "MPC"
    assert snapshot["predictionWalletAddress"] == wallet_address
    assert snapshot["predictionWalletId"] == wallet_id
    assert fake_client.wallet_calls == 1
    assert fake_client.payment_calls == 0
    assert seen["json"]["method"] == "eth_call"
    assert seen["json"]["params"][0]["data"].endswith(wallet_address[2:].lower())


def test_binance_balance_fails_closed_when_configured_wallet_is_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_address = "0x1234567890abcdef1234567890abcdef12345678"
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ADDRESS_ENV, configured_address)
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ID_ENV, "configured-wallet")
    monkeypatch.setenv(live_v3.BINANCE_ACCOUNT_TYPE_ENV, "SPOT")

    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="paper", venue="binance", notional_usdt=1.0)
    )
    fake_client = _FakeBinanceClient(
        "0x9999999999999999999999999999999999999999",
        "different-wallet",
    )
    monkeypatch.setattr(executor, "_ensure_binance", lambda: fake_client)

    def forbidden_post(*_args, **_kwargs):
        raise AssertionError("unregistered configured wallet reached BSC RPC")

    monkeypatch.setattr(live.httpx, "post", forbidden_post)
    snapshot = executor.available_balance_snapshot()
    assert snapshot["status"] == "UNAVAILABLE"
    assert "walletAddress/walletId pair was not returned by wallet/list" in snapshot["error"]


def test_binance_market_order_below_documented_minimum_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="binance", notional_usdt=1.0)
    )

    def forbidden_client():
        raise AssertionError("below-minimum Binance stake reached network")

    monkeypatch.setattr(executor, "_ensure_binance", forbidden_client)
    with pytest.raises(live.TargetTakerLiveError, match="approximately 1.5 USDT minimum"):
        executor._execute_binance(
            source_market_id=123,
            side="UP",
            signal_ask=0.40,
            snapshot={
                "market_id": 123,
                "bucket_start_sec": 1_800_000_000,
                "window_end_ms": 1_800_000_300_000,
            },
            signal_id="signal-minimum",
        )


class _FakeBinanceLiveClient:
    def __init__(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        history_rows: list[dict] | None = None,
        trading_status: str = "OPEN",
    ) -> None:
        self.wallet_address = wallet_address
        self.wallet_id = wallet_id
        self.start_ms = 1_800_000_000_000
        self.end_ms = self.start_ms + 300_000
        self.now_ms = self.start_ms + 20_000
        self.history_rows = list(history_rows or [])
        self.trading_status = trading_status
        self.quote_slippages: list[int] = []
        self.place_slippages: list[int] = []
        self.history_calls = 0
        self.place_calls = 0

    def wallets(self) -> dict:
        return {
            "wallets": [
                {
                    "walletAddress": self.wallet_address,
                    "walletId": self.wallet_id,
                }
            ]
        }

    def list_markets(self, offset: int = 0, limit: int = 100) -> dict:
        return {
            "marketTopics": [
                {
                    "chartType": "CRYPTO_UP_DOWN",
                    "symbol": "BTCUSDT",
                    "startDate": self.start_ms,
                    "endDate": self.end_ms,
                    "marketTopicId": 88,
                    "feeRateBps": 200,
                    "markets": [
                        {
                            "marketId": 99,
                            "tradingStatus": self.trading_status,
                            "outcomes": [
                                {"name": "UP", "tokenId": "up-token"},
                                {"name": "DOWN", "tokenId": "down-token"},
                            ],
                        }
                    ],
                }
            ],
            "hasMore": False,
            "limit": limit,
        }

    def server_timestamp_ms(self) -> int:
        return self.now_ms

    def orderbook(self, market_id: int, token_id: str) -> dict:
        ask = 0.40 if token_id == "up-token" else 0.60
        return {
            "asks": [{"price": str(ask), "size": "10"}],
            "bids": [{"price": str(ask - 0.01), "size": "10"}],
            "updateTimestampMs": self.now_ms,
        }

    def get_quote(self, **kwargs) -> dict:
        self.quote_slippages.append(int(kwargs["slippage_bps"]))
        amount_in = str(kwargs["amount_in_wei"])
        return {
            "quoteId": "quote-1",
            "tokenId": kwargs["token_id"],
            "side": "BUY",
            "amountIn": amount_in,
            "amountOut": "5000000000000000000",
            "averagePrice": 0.40,
            "walletAddress": kwargs["wallet_address"],
            "orderType": "MARKET",
            "slippageBps": int(kwargs["slippage_bps"]),
            "chainId": "56",
            "expireAt": self.now_ms + 5_000,
        }

    def place_market_order(self, **kwargs) -> dict:
        self.place_calls += 1
        self.place_slippages.append(int(kwargs["slippage_bps"]))
        return {"orderId": "order-1"}

    def order_history(self, wallet_address: str, *, limit: int = 100) -> dict:
        assert wallet_address == self.wallet_address
        self.history_calls += 1
        return {"orders": list(self.history_rows)}


def _live_binance_executor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    history_rows: list[dict] | None = None,
    trading_status: str = "OPEN",
    notional: float = 2.0,
) -> tuple[live.TargetTakerLiveExecutor, _FakeBinanceLiveClient]:
    wallet_address = "0x1234567890abcdef1234567890abcdef12345678"
    wallet_id = "prediction-wallet-1"
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ADDRESS_ENV, wallet_address)
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ID_ENV, wallet_id)
    monkeypatch.setenv(live_v3.BINANCE_ACCOUNT_TYPE_ENV, "SPOT")
    monkeypatch.setenv(live_v3.BINANCE_SYMBOL_ENV, "BTCUSDT")
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(
            mode="live",
            venue="binance",
            notional_usdt=notional,
            max_price_drift=0.02,
        )
    )
    client = _FakeBinanceLiveClient(
        wallet_address=wallet_address,
        wallet_id=wallet_id,
        history_rows=history_rows,
        trading_status=trading_status,
    )
    monkeypatch.setattr(executor, "_ensure_binance", lambda: client)
    monkeypatch.setattr(
        executor,
        "_binance_prediction_wallet_usdt_balance",
        lambda _wallet: 50.0,
    )
    monkeypatch.setattr(live.time, "sleep", lambda _seconds: None)
    return executor, client


def _execute_live_binance(executor: live.TargetTakerLiveExecutor) -> dict:
    return executor._execute_binance(
        source_market_id=123,
        side="UP",
        signal_ask=0.40,
        snapshot={
            "market_id": 123,
            "bucket_start_sec": 1_800_000_000,
            "window_end_ms": 1_800_000_300_000,
        },
        signal_id="signal-live",
    )


def test_binance_live_uses_supported_slippage_and_only_counts_reconciled_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, client = _live_binance_executor(
        monkeypatch,
        history_rows=[
            {
                "orderId": "order-1",
                "vendorOrderId": "vendor-1",
                "status": "FILLED",
                "filledUsdtAmount": "2.00",
                "filledShareQty": "5.00",
                "price": "0.40",
            }
        ],
    )
    result = _execute_live_binance(executor)

    assert result["status"] == "SUBMITTED"
    assert result["exchangeStatus"] == "FILLED"
    assert result["submittedUsdt"] == pytest.approx(2.0)
    assert result["shares"] == pytest.approx(5.0)
    assert result["vendorOrderId"] == "order-1"
    assert result["vendorOrderHash"] == "vendor-1"
    assert client.quote_slippages == [live.BINANCE_LIVE_SLIPPAGE_BPS]
    assert client.place_slippages == [live.BINANCE_LIVE_SLIPPAGE_BPS]
    assert live.BINANCE_LIVE_SLIPPAGE_BPS == 1
    assert client.place_calls == 1
    assert client.history_calls == 1


def test_binance_live_terminal_no_fill_is_rejected_not_fake_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, client = _live_binance_executor(
        monkeypatch,
        history_rows=[
            {
                "orderId": "order-1",
                "status": "FAILED",
                "filledUsdtAmount": "0.00",
                "filledShareQty": "0.00",
            }
        ],
    )
    result = _execute_live_binance(executor)

    assert result["status"] == "REJECTED"
    assert result["exchangeStatus"] == "FAILED"
    assert result.get("submittedUsdt") is None
    assert result.get("shares") is None
    assert client.place_calls == 1


def test_binance_live_unreconciled_order_is_ambiguous_and_never_fake_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, client = _live_binance_executor(
        monkeypatch,
        history_rows=[
            {
                "orderId": "order-1",
                "status": "SUBMITTED",
                "filledUsdtAmount": "0.00",
                "filledShareQty": "0.00",
            }
        ],
    )
    result = _execute_live_binance(executor)

    assert result["status"] == "AMBIGUOUS"
    assert result["vendorOrderId"] == "order-1"
    assert result["exchangeStatus"] == "SUBMITTED"
    assert result.get("submittedUsdt") is None
    assert result.get("shares") is None
    assert client.history_calls == len(live.BINANCE_RECONCILE_DELAYS_SECONDS)


def test_binance_live_requires_open_market_before_quote_or_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, client = _live_binance_executor(
        monkeypatch,
        trading_status="CLOSED",
    )
    with pytest.raises(live.TargetTakerLiveError, match="not OPEN"):
        _execute_live_binance(executor)
    assert client.quote_slippages == []
    assert client.place_calls == 0


def test_binance_live_rechecks_strategy_seconds_left_before_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, client = _live_binance_executor(monkeypatch)
    client.now_ms = client.end_ms - 5_000
    with pytest.raises(live.TargetTakerLiveError, match="strategy requires"):
        _execute_live_binance(executor)
    assert client.quote_slippages == []
    assert client.place_calls == 0


def test_binance_live_requires_exact_registered_wallet_before_any_trade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, client = _live_binance_executor(monkeypatch)
    client.wallet_id = "different-wallet"
    with pytest.raises(live.TargetTakerLiveError, match="walletAddress/walletId pair"):
        _execute_live_binance(executor)
    assert client.quote_slippages == []
    assert client.place_calls == 0
