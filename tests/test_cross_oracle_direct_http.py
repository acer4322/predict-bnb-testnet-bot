from __future__ import annotations

import urllib.error

import httpx
import pytest

from predict_bot import cross_oracle
from predict_bot.cross_oracle_resilient import (
    HTTP_TRANSPORT,
    _direct_http_json,
)


def test_resilient_collector_replaces_base_http_helper() -> None:
    assert HTTP_TRANSPORT == "httpx-direct-no-env-proxy"
    assert cross_oracle._http_json is _direct_http_json


def test_direct_http_disables_environment_proxy_but_keeps_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            captured["raised"] = True

        def json(self) -> dict[str, bool]:
            return {"ok": True}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr("predict_bot.cross_oracle_resilient.httpx.Client", FakeClient)

    result = _direct_http_json("https://gamma-api.polymarket.com/test", timeout=3.25)

    assert result == {"ok": True}
    assert captured["url"] == "https://gamma-api.polymarket.com/test"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["trust_env"] is False
    assert kwargs["verify"] is True
    assert kwargs["follow_redirects"] is True
    assert kwargs["timeout"] == 3.25
    assert captured["raised"] is True


def test_direct_http_maps_httpx_network_error_to_retryable_urlerror(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str) -> object:
            raise httpx.ConnectError("certificate path changed")

    monkeypatch.setattr("predict_bot.cross_oracle_resilient.httpx.Client", FakeClient)

    with pytest.raises(urllib.error.URLError, match="direct httpx request failed"):
        _direct_http_json("https://gamma-api.polymarket.com/test")
