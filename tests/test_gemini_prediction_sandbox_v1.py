from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from predict_bot import gemini_prediction_sandbox_v1 as m


def _event(start="2026-09-19T23:30:00.000Z", end="2026-09-19T23:35:00.000Z"):
    return {
        "id": "evt-1",
        "title": "BTC price today at 7:35pm EDT",
        "ticker": "BTC05M2609192335",
        "series": "BTC05M",
        "status": "active",
        "startTime": start,
        "expiryDate": end,
        "contracts": [
            {
                "ticker": "UP",
                "label": "Up",
                "instrumentSymbol": "GEMI-BTC05M2609192335-UP",
                "status": "active",
                "marketState": "open",
                "quantityIncrement": "1",
                "quantityMinimum": "1",
                "priceIncrement": "0.01",
                "priceMinimum": "0.01",
                "strike": {"type": "above", "value": "81000"},
                "prices": {
                    "buy": {"yes": "0.55", "no": "0.47"},
                    "sell": {"yes": "0.53", "no": "0.45"},
                    "bestBid": "0.53",
                    "bestAsk": "0.55",
                    "lastTradePrice": "0.54",
                },
            }
        ],
    }


def test_selects_active_btc05m_not_other_series():
    now = m.parse_iso_ms("2026-09-19T23:32:00Z")
    selected = m.select_btc5m_event(
        [
            {**_event(), "series": "BTC15M"},
            _event(),
        ],
        now_ms=now,
    )
    assert selected is not None
    event, contract, phase = selected
    assert phase == "ACTIVE"
    assert event["ticker"] == "BTC05M2609192335"
    assert contract["instrumentSymbol"] == "GEMI-BTC05M2609192335-UP"


def test_event_snapshot_maps_yes_to_up_and_no_to_down():
    event = _event()
    contract = event["contracts"][0]
    snap = m.event_snapshot(
        event,
        contract,
        observed_at_ms=m.parse_iso_ms("2026-09-19T23:32:00Z"),
    )
    assert snap["predictUpBid"] == pytest.approx(0.53)
    assert snap["predictUpAsk"] == pytest.approx(0.55)
    assert snap["predictDownBid"] == pytest.approx(0.45)
    assert snap["predictDownAsk"] == pytest.approx(0.47)
    assert snap["predictUpMid"] == pytest.approx(0.54)
    assert snap["strikePrice"] == pytest.approx(81000)
    assert snap["secondsLeft"] == pytest.approx(180.0)
    assert snap["venue"] == "GEMINI_PM_SANDBOX"


def test_merge_recomputes_cross_venue_strike_geometry():
    event = _event()
    snap = m.event_snapshot(
        event,
        event["contracts"][0],
        observed_at_ms=m.parse_iso_ms("2026-09-19T23:32:00Z"),
    )
    baseline = {
        "marketId": 123,
        "spotPrice": 81081.0,
        "chainlinkPrice": 80919.0,
        "spotMinusStrikeBps": 9999.0,
        "chainlinkMinusStrikeBps": 9999.0,
        "spotQueueImbalance": 0.2,
    }
    merged = m.merge_public_baseline(snap, baseline)
    assert merged["marketId"] != 123
    assert merged["baselineMarketId"] == 123
    assert merged["spotQueueImbalance"] == pytest.approx(0.2)
    assert merged["spotMinusStrikeBps"] == pytest.approx(10.0)
    assert merged["chainlinkMinusStrikeBps"] == pytest.approx(-10.0)
    assert merged["geminiStrikeMappingStatus"] == "GEMINI_STRIKE_APPLIED"


def test_merge_never_reuses_predict_fun_strike_when_gemini_strike_missing():
    event = _event()
    event["contracts"][0].pop("strike")
    snap = m.event_snapshot(event, event["contracts"][0])
    merged = m.merge_public_baseline(
        snap,
        {
            "strikePrice": 77777,
            "spotPrice": 81000,
            "spotMinusStrikeBps": 123.0,
            "chainlinkMinusStrikeBps": -123.0,
        },
    )
    assert merged["strikePrice"] is None
    assert merged["spotMinusStrikeBps"] is None
    assert merged["chainlinkMinusStrikeBps"] is None
    assert merged["geminiStrikeMappingStatus"] == "STRIKE_UNAVAILABLE_NO_CROSS_VENUE_REUSE"


def test_prepare_order_maps_maker_and_taker_to_safe_tif():
    contract = _event()["contracts"][0]
    maker = m.prepare_order(contract, channel="MAKER", side="UP", price=0.53, quantity=10)
    taker = m.prepare_order(contract, channel="TAKER", side="DOWN", price=0.47, quantity=8)
    assert maker.time_in_force == "MOC"
    assert maker.outcome == "YES"
    assert maker.message["params"]["side"] == "BUY"
    assert taker.time_in_force == "IOC"
    assert taker.outcome == "NO"


def test_prepare_order_obeys_contract_increments():
    contract = _event()["contracts"][0]
    with pytest.raises(ValueError, match="increment"):
        m.prepare_order(contract, channel="MAKER", side="UP", price=0.535, quantity=10)
    with pytest.raises(ValueError, match="increment"):
        m.prepare_order({**contract, "quantityIncrement": "2"}, channel="TAKER", side="DOWN", price=0.47, quantity=3)


def test_auth_headers_match_official_hmac_shape():
    key = "k"
    secret = "secret"
    nonce = "1789860000"
    headers = m.GeminiPredictionSandboxClient.auth_headers(key, secret, nonce=nonce)
    payload = base64.b64encode(nonce.encode()).decode()
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha384).hexdigest()
    assert headers["X-GEMINI-APIKEY"] == key
    assert headers["X-GEMINI-NONCE"] == nonce
    assert headers["X-GEMINI-PAYLOAD"] == payload
    assert headers["X-GEMINI-SIGNATURE"] == expected


def test_client_refuses_production_urls():
    with pytest.raises(RuntimeError, match="sandbox-only"):
        m.GeminiPredictionSandboxClient(rest_url="https://api.gemini.com", ws_url=m.DEFAULT_WS_URL)
    with pytest.raises(RuntimeError, match="sandbox-only"):
        m.GeminiPredictionSandboxClient(rest_url=m.DEFAULT_REST_URL, ws_url="wss://ws.gemini.com")


def test_order_gate_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GEMINI_PM_SANDBOX_ORDER_ENABLED", raising=False)
    client = m.GeminiPredictionSandboxClient()
    try:
        assert client.order_enabled is False
        prepared = m.prepare_order(_event()["contracts"][0], channel="MAKER", side="UP", price=0.53, quantity=1)
        with pytest.raises(PermissionError, match="disabled"):
            client.submit_order(prepared)
    finally:
        client.close()


def test_credentials_fall_back_to_windows_user_env(monkeypatch):
    monkeypatch.delenv("GEMINI_PM_SANDBOX_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PM_SANDBOX_API_SECRET", raising=False)
    monkeypatch.delenv("gemini_sandbox_APIKEY", raising=False)
    monkeypatch.delenv("gemini_sandbox_APISecret", raising=False)
    values = {
        "gemini_sandbox_APIKEY": "account-example-key",
        "gemini_sandbox_APISecret": "example-secret",
    }
    monkeypatch.setattr(
        m.GeminiPredictionSandboxClient,
        "_windows_user_env",
        staticmethod(lambda name: values.get(name)),
    )
    client = m.GeminiPredictionSandboxClient()
    try:
        key, secret = client._credentials()
        assert key == "account-example-key"
        assert secret == "example-secret"
        meta = client.credential_metadata()
        assert meta["present"] is True
        assert meta["accountScoped"] is True
        assert "account-example-key" not in str(meta)
        assert "example-secret" not in str(meta)
    finally:
        client.close()


def test_current_btc5m_tolerates_rollover_detail_404(monkeypatch):
    import httpx

    event = _event()
    client = m.GeminiPredictionSandboxClient()
    try:
        monkeypatch.setattr(client, "list_events", lambda **_kwargs: [event])
        request = httpx.Request("GET", "https://api.sandbox.gemini.com/v1/prediction-markets/events/BTC05M2609192335")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("not found", request=request, response=response)
        monkeypatch.setattr(client, "get_event", lambda _ticker: (_ for _ in ()).throw(error))
        picked = client.current_btc5m(
            now_ms=m.parse_iso_ms("2026-09-19T23:32:00Z"),
            allow_upcoming=True,
            hydrate_detail=True,
        )
        assert picked is not None
        e, contract, phase = picked
        assert e["ticker"] == "BTC05M2609192335"
        assert contract["instrumentSymbol"] == "GEMI-BTC05M2609192335-UP"
        assert phase == "ACTIVE"
    finally:
        client.close()
