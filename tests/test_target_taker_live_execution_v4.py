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
