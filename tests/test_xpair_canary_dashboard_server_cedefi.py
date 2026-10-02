from __future__ import annotations

from predict_bot.xpair_canary_dashboard_server_cedefi import (
    CeDeFiLaunchRequest,
    state_payload,
)


def payload(**overrides):
    return {
        "mode": "quote-only",
        "selection": "BTC_DOWN_ETH_UP",
        "pairBudgetUsdt": 2.0,
        "balanceBufferUsdt": 0.1,
        "maxTotalCost": 0.98,
        "maxLegReprice": 0.01,
        "entrySecondsLeft": 180,
        "entryWindowSeconds": 10,
        "slippageBps": 100,
        "accountType": "CeDeFi",
        "reconcileSeconds": 15,
        **overrides,
    }


def test_dashboard_request_forces_cedefi_and_wrapper_module() -> None:
    request = CeDeFiLaunchRequest.from_payload(payload(accountType="SPOT"))
    assert request.account_type == "CeDeFi"
    command = request.command()
    assert command[:3] == [
        command[0],
        "-m",
        "predict_bot.xpair_btc_eth_canary_cedefi",
    ]
    assert command[command.index("--account-type") + 1] == "CeDeFi"


def test_dashboard_defaults_expose_cedefi() -> None:
    assert state_payload()["defaults"]["accountType"] == "CeDeFi"
