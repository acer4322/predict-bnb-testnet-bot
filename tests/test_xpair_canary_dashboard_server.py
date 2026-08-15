from __future__ import annotations

import sys
from collections import deque

import pytest

from predict_bot.xpair_canary_dashboard_server import (
    LIVE_CONFIRM_VALUE,
    LaunchRequest,
    infer_final_phase,
    infer_phase,
    origin_is_allowed,
)


def request(**values: object) -> LaunchRequest:
    return LaunchRequest.from_payload(
        {
            "mode": "dry-run",
            "selection": "BTC_DOWN_ETH_UP",
            "pairBudgetUsdt": 2.0,
            "balanceBufferUsdt": 0.1,
            "maxTotalCost": 0.98,
            "maxLegReprice": 0.01,
            "entrySecondsLeft": 180,
            "entryWindowSeconds": 10,
            "slippageBps": 100,
            "accountType": "SPOT",
            "reconcileSeconds": 15,
            **values,
        }
    )


def test_default_request_requires_2_10_available() -> None:
    assert request().required_balance_usdt == pytest.approx(2.10)


def test_pair_budget_is_hard_capped_at_three_usdt() -> None:
    with pytest.raises(ValueError):
        request(pairBudgetUsdt=3.01)
    with pytest.raises(ValueError):
        request(pairBudgetUsdt=0.01)


def test_live_command_has_explicit_execute_flag() -> None:
    command = request(mode="live").command()
    assert command[:3] == [sys.executable, "-m", "predict_bot.xpair_btc_eth_canary"]
    assert "--execute-live" in command
    assert LIVE_CONFIRM_VALUE not in command


def test_quote_only_command_never_has_live_flag() -> None:
    command = request(mode="quote-only").command()
    assert "--execute-live" not in command
    assert command[command.index("--mode") + 1] == "quote-only"


def test_runtime_phase_recognizes_manual_attention_states() -> None:
    assert infer_phase("ONE_SIDED_FILL_ALERT: BTC=FILLED ETH=OPEN", "RUNNING") == "ONE_SIDED_FILL_ALERT"
    logs = deque([
        {"timestamp": "x", "level": "INFO", "message": "SUBMITTED_BOTH"},
        {"timestamp": "y", "level": "ERROR", "message": "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE"},
    ])
    assert infer_final_phase(logs, "ERROR") == "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE"


def test_api_origin_is_loopback_only() -> None:
    assert origin_is_allowed(None)
    assert origin_is_allowed("http://localhost:3000")
    assert origin_is_allowed("http://127.0.0.1:3000")
    assert not origin_is_allowed("https://example.com")
