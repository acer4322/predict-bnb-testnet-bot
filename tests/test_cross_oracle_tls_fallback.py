from __future__ import annotations

import ssl
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from predict_bot.cross_oracle_tls_fallback import (
    _ALLOWED_HOSTS,
    _gamma_slug_from_url,
    _is_gamma_slug_url,
    _is_self_signed_tls_error,
    _public_polymarket_json,
)
from predict_bot.cross_oracle_transport_hardening import (
    _WS_ALLOWED_HOSTS,
    _gamma_list_lookup_404_safe,
    _self_signed_tls_error,
    _ws_host_allowed,
    _ws_sslopt,
)


def test_tls_fallback_scope_is_public_polymarket_only() -> None:
    assert _ALLOWED_HOSTS == {
        "gamma-api.polymarket.com",
        "clob.polymarket.com",
    }
    assert "api.binance.com" not in _ALLOWED_HOSTS


def test_self_signed_detection_is_narrow() -> None:
    error = RuntimeError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self-signed certificate (_ssl.c:1028)"
    )
    assert _is_self_signed_tls_error(error)
    assert _self_signed_tls_error(error)
    assert not _is_self_signed_tls_error(RuntimeError("timed out"))
    assert not _self_signed_tls_error(RuntimeError("HTTP 500"))


def test_helper_refuses_binance_before_any_network_request() -> None:
    with pytest.raises(urllib.error.URLError) as excinfo:
        _public_polymarket_json("https://api.binance.com/api/v3/time", timeout=0.01)
    assert "refused non-public Polymarket endpoint" in str(excinfo.value)


def test_gamma_slug_detection_is_narrow() -> None:
    url = "https://gamma-api.polymarket.com/markets/slug/btc-updown-5m-1786190100"
    assert _is_gamma_slug_url(url)
    assert _gamma_slug_from_url(url) == "btc-updown-5m-1786190100"
    assert not _is_gamma_slug_url(
        "https://clob.polymarket.com/book?token_id=abc"
    )
    assert not _is_gamma_slug_url(
        "https://gamma-api.polymarket.com/events/slug/btc-updown-5m-1786190100"
    )


def test_gamma_404_is_normalized_to_waiting_market_not_tls_failure() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "cross_oracle_tls_fallback.py"
    ).read_text(encoding="utf-8")
    assert "_GammaMarketNotPublished" in source
    assert '"status": "WAITING_GAMMA"' in source
    assert 'self.polymarket["error"] = None' in source
    assert 'self.gap_reason = "MARKET_NOT_PUBLISHED"' in source
    assert "exact 404; list query empty" in source


def test_gamma_exact_404_has_same_slug_list_query_recovery() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "cross_oracle_tls_fallback.py"
    ).read_text(encoding="utf-8")
    assert 'params={"slug": slug, "limit": 5}' in source
    assert 'str(row.get("slug") or "") == slug' in source
    assert '"LIST_QUERY_FALLBACK"' in source
    assert '"EXACT_SLUG"' in source
    assert '"gammaExact404ListFallback": True' in source


def test_gamma_list_query_404_is_not_wrapped_as_tls_failure() -> None:
    class Response:
        status_code = 404

        def raise_for_status(self) -> None:
            raise AssertionError("404 must be handled before raise_for_status")

        def json(self) -> Any:
            raise AssertionError("404 must not be decoded as JSON")

    class Client:
        def get(self, *_args: Any, **_kwargs: Any) -> Response:
            return Response()

    result = _gamma_list_lookup_404_safe(
        Client(),
        slug="btc-updown-5m-1786199100",
    )
    assert result is None


def test_websocket_tls_fallback_is_public_polymarket_only() -> None:
    assert _WS_ALLOWED_HOSTS == {
        "ws-live-data.polymarket.com",
        "ws-subscriptions-clob.polymarket.com",
    }
    assert _ws_host_allowed("wss://ws-live-data.polymarket.com")
    assert _ws_host_allowed("wss://ws-subscriptions-clob.polymarket.com/ws/market")
    assert not _ws_host_allowed("wss://api.binance.com/ws")
    assert _ws_sslopt(False, "wss://ws-live-data.polymarket.com") is None
    sslopt = _ws_sslopt(True, "wss://ws-live-data.polymarket.com")
    assert sslopt is not None
    assert sslopt["cert_reqs"] == ssl.CERT_NONE
    assert sslopt["check_hostname"] is False
    with pytest.raises(RuntimeError, match="refused non-public Polymarket host"):
        _ws_sslopt(True, "wss://api.binance.com/ws")


def test_supervisor_runs_transport_hardening_entrypoint() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_transport_hardening" in source
    assert '"predict_bot.cross_oracle_tls_fallback"' not in source
