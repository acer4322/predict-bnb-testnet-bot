from __future__ import annotations

import urllib.error

import pytest

from predict_bot.cross_oracle_tls_fallback import (
    _ALLOWED_HOSTS,
    _is_self_signed_tls_error,
    _public_polymarket_json,
)


def test_tls_fallback_scope_is_public_polymarket_only() -> None:
    assert _ALLOWED_HOSTS == {
        "gamma-api.polymarket.com",
        "clob.polymarket.com",
    }
    assert "api.binance.com" not in _ALLOWED_HOSTS


def test_self_signed_detection_is_narrow() -> None:
    assert _is_self_signed_tls_error(
        RuntimeError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "self-signed certificate (_ssl.c:1028)"
        )
    )
    assert not _is_self_signed_tls_error(RuntimeError("timed out"))
    assert not _is_self_signed_tls_error(RuntimeError("HTTP 500"))


def test_helper_refuses_binance_before_any_network_request() -> None:
    with pytest.raises(urllib.error.URLError) as excinfo:
        _public_polymarket_json("https://api.binance.com/api/v3/time", timeout=0.01)
    assert "refused non-public Polymarket endpoint" in str(excinfo.value)
