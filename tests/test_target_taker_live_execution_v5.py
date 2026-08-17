from __future__ import annotations

import pytest

from predict_bot import target_taker_live_execution_v5 as live


WALLET_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
WALLET_ID = "prediction-wallet-1"


class _FakeBinanceClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.wallet_calls = 0

    def wallets(self) -> dict:
        self.wallet_calls += 1
        return self.payload


def test_single_prediction_wallet_is_discovered_from_authenticated_wallet_list() -> None:
    selected = live.select_single_binance_prediction_wallet(
        {
            "wallets": [
                {
                    "walletAddress": WALLET_ADDRESS,
                    "walletId": WALLET_ID,
                }
            ]
        }
    )
    assert selected == {"walletAddress": WALLET_ADDRESS, "walletId": WALLET_ID}


def test_single_prediction_wallet_accepts_nested_data_shape() -> None:
    selected = live.select_single_binance_prediction_wallet(
        {
            "data": {
                "wallets": [
                    {
                        "walletAddress": WALLET_ADDRESS,
                        "walletId": WALLET_ID,
                    }
                ]
            }
        }
    )
    assert selected["walletAddress"] == WALLET_ADDRESS
    assert selected["walletId"] == WALLET_ID


@pytest.mark.parametrize(
    "payload",
    [
        {"wallets": []},
        {
            "wallets": [
                {"walletAddress": WALLET_ADDRESS, "walletId": "wallet-a"},
                {"walletAddress": WALLET_ADDRESS, "walletId": "wallet-b"},
            ]
        },
    ],
)
def test_wallet_discovery_never_guesses_when_wallet_count_is_not_one(payload: dict) -> None:
    with pytest.raises(live.TargetTakerLiveError, match="exactly one wallet"):
        live.select_single_binance_prediction_wallet(payload)


def test_wallet_discovery_rejects_incomplete_single_wallet() -> None:
    with pytest.raises(live.TargetTakerLiveError, match="incomplete wallet"):
        live.select_single_binance_prediction_wallet(
            {"wallets": [{"walletAddress": WALLET_ADDRESS}]}
        )


def test_executor_uses_api_discovered_wallet_not_manual_wallet_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # These legacy Target-Taker-specific env names must not drive V5 wallet
    # selection. Binance authentication is BINANCE_API_KEY/BINANCE_API_SECRET;
    # walletAddress/walletId come from authenticated wallet/list.
    monkeypatch.setenv(
        "PREDICT_TARGET_TAKER_BINANCE_WALLET_ADDRESS",
        "0x9999999999999999999999999999999999999999",
    )
    monkeypatch.setenv("PREDICT_TARGET_TAKER_BINANCE_WALLET_ID", "wrong-wallet")
    monkeypatch.delenv(live.BINANCE_ORDER_ACCOUNT_TYPE_ENV, raising=False)

    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(
            mode="paper",
            venue="binance",
            notional_usdt=1.0,
        )
    )
    client = _FakeBinanceClient(
        {"wallets": [{"walletAddress": WALLET_ADDRESS, "walletId": WALLET_ID}]}
    )
    monkeypatch.setattr(executor, "_ensure_binance", lambda: client)

    wallet_address, wallet_id, account_type = executor._binance_wallet()
    assert wallet_address == WALLET_ADDRESS
    assert wallet_id == WALLET_ID
    assert account_type == "SPOT"
    assert client.wallet_calls == 1


def test_executor_keeps_explicit_order_account_type_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(live.BINANCE_ORDER_ACCOUNT_TYPE_ENV, "FUNDING")
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="paper", venue="binance", notional_usdt=1.0)
    )
    client = _FakeBinanceClient(
        {"wallets": [{"walletAddress": WALLET_ADDRESS, "walletId": WALLET_ID}]}
    )
    monkeypatch.setattr(executor, "_ensure_binance", lambda: client)

    assert executor._binance_wallet() == (WALLET_ADDRESS, WALLET_ID, "FUNDING")
