from __future__ import annotations

import os

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_predict_direct_v8_2 import (  # noqa: E402
    PredictDirectV82WalletMakerCloneEngine,
    sanitize_predict_limit_order_request,
)


def test_limit_payload_removes_market_only_reserved_balance_policy() -> None:
    original = {
        "pricePerShare": "500000000000000000",
        "strategy": "LIMIT",
        "slippageBps": "0",
        "isFillOrKill": False,
        "isPostOnly": True,
        "reservedBalancePolicy": "REJECT_MARKET_ORDER",
        "isMinAmountOut": False,
        "selfTradePrevention": "CANCEL_MAKER",
        "order": {"hash": "0xabc"},
    }
    sanitized = sanitize_predict_limit_order_request(original)

    assert sanitized["strategy"] == "LIMIT"
    assert sanitized["isPostOnly"] is True
    assert sanitized["selfTradePrevention"] == "CANCEL_MAKER"
    assert "reservedBalancePolicy" not in sanitized
    assert "slippageBps" not in sanitized
    assert "isMinAmountOut" not in sanitized
    assert original["reservedBalancePolicy"] == "REJECT_MARKET_ORDER"


def test_market_payload_is_not_rewritten() -> None:
    original = {
        "strategy": "MARKET",
        "reservedBalancePolicy": "REJECT_MARKET_ORDER",
        "slippageBps": "100",
        "isMinAmountOut": True,
    }
    assert sanitize_predict_limit_order_request(original) == original


def test_nonzero_limit_slippage_metadata_is_preserved_except_market_only_field() -> None:
    original = {
        "strategy": "LIMIT",
        "reservedBalancePolicy": "REJECT_MARKET_ORDER",
        "slippageBps": "25",
        "isMinAmountOut": True,
    }
    sanitized = sanitize_predict_limit_order_request(original)
    assert "reservedBalancePolicy" not in sanitized
    assert sanitized["slippageBps"] == "25"
    assert sanitized["isMinAmountOut"] is True


def test_v82_version_marker(tmp_path) -> None:
    engine = PredictDirectV82WalletMakerCloneEngine(tmp_path / "predict-direct-v82.db")
    try:
        assert engine.VERSION == "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_2_LIMIT_PAYLOAD_FIXED"
        state = engine.snapshot()
        assert state["rules"]["reservedBalancePolicySentOnLimit"] is False
        assert state["predictDirect"]["reservedBalancePolicyOnLimit"] is False
    finally:
        engine.http.close()
        if engine.direct_client is not None:
            engine.direct_client.close()
        with engine.db_lock:
            engine.db.commit()
            engine.db.close()
