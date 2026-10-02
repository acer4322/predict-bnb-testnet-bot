from __future__ import annotations

from predict_bot import gemini_btc5m_testnet_bridge_v1 as b


def test_bridge_is_bound_to_localhost_and_separate_port():
    assert b.HOST == "127.0.0.1"
    assert b.PORT == 8810


def test_bridge_defaults_to_short_retention():
    assert 12 <= b.RETENTION_HOURS <= 168


def test_bridge_action_endpoint_is_sandbox_named():
    assert "TESTNET" in b.VERSION
