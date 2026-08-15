from __future__ import annotations

import pytest

from predict_bot import target_taker_live_execution_v1 as v1
from predict_bot import target_taker_live_execution_v2 as v2


def test_binance_account_type_defaults_to_spot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(v1.BINANCE_WALLET_ADDRESS_ENV, "0xabc")
    monkeypatch.setenv(v1.BINANCE_WALLET_ID_ENV, "wallet-1")
    monkeypatch.delenv(v1.BINANCE_ACCOUNT_TYPE_ENV, raising=False)
    executor = v2.TargetTakerLiveExecutor(
        v1.TargetTakerLiveConfig(mode="live", venue="binance", notional_usdt=1.0)
    )
    address, wallet_id, account_type = executor._binance_wallet()
    assert address == "0xabc"
    assert wallet_id == "wallet-1"
    assert account_type == "SPOT"


def test_binance_account_type_accepts_funding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(v1.BINANCE_WALLET_ADDRESS_ENV, "0xabc")
    monkeypatch.setenv(v1.BINANCE_WALLET_ID_ENV, "wallet-1")
    monkeypatch.setenv(v1.BINANCE_ACCOUNT_TYPE_ENV, "funding")
    executor = v2.TargetTakerLiveExecutor(
        v1.TargetTakerLiveConfig(mode="live", venue="binance", notional_usdt=1.0)
    )
    assert executor._binance_wallet()[2] == "FUNDING"


def test_binance_account_type_rejects_mpc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(v1.BINANCE_WALLET_ADDRESS_ENV, "0xabc")
    monkeypatch.setenv(v1.BINANCE_WALLET_ID_ENV, "wallet-1")
    monkeypatch.setenv(v1.BINANCE_ACCOUNT_TYPE_ENV, "MPC")
    executor = v2.TargetTakerLiveExecutor(
        v1.TargetTakerLiveConfig(mode="live", venue="binance", notional_usdt=1.0)
    )
    with pytest.raises(v1.TargetTakerLiveError, match="SPOT or FUNDING"):
        executor._binance_wallet()


class _FakePredictClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def _request(self, method: str, path: str) -> dict:
        self.calls.append((method, path))
        return self.payload


def test_predict_market_uses_exact_id_endpoint() -> None:
    executor = v2.TargetTakerLiveExecutor(
        v1.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
    )
    client = _FakePredictClient({"success": True, "data": {"id": 6827001}})
    market = executor._predict_market(client, 6827001)
    assert market["id"] == 6827001
    assert client.calls == [("GET", "/v1/markets/6827001")]


def test_predict_market_rejects_wrong_exact_id() -> None:
    executor = v2.TargetTakerLiveExecutor(
        v1.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
    )
    client = _FakePredictClient({"success": True, "data": {"id": 6827002}})
    with pytest.raises(v1.TargetTakerLiveError, match="did not return market 6827001"):
        executor._predict_market(client, 6827001)
